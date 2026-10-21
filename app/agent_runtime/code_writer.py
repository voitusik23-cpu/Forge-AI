"""Guarded code-writing execution path: a model answer becomes applied files.

Why this module exists
----------------------
Forge could plan, verify and report, but **nothing turned a model answer into a
change on disk**. The offline harness is read-only by construction, and no
executor applied model output to files, so the product had no path in which
Forge itself wrote code.

This module is that missing step, and it is deliberately small:

* **Standard library only, deterministic, no network, no model call, no shell.**
  One answer in, one structured report out; the same answer applied to the same
  tree produces the same bytes and the same report.
* **It is not authority.** The workspace root is supplied by the caller from the
  frozen perimeter it already holds (``RunScope.workspace``), never read from a
  request, a task, a decision, or the process directory. A path the model names
  is validated against that root and against a hard deny list; nothing here can
  widen the perimeter, choose a workspace, or authorize anything.
* **It is fail-closed.** Every change is parsed and validated *before* a single
  byte is written, so a rejected answer leaves the tree untouched. After writing,
  every touched ``.py`` is compiled; if one fails, **all** files of the run are
  rolled back from the backups taken first, so a broken tree is never left behind.

What it supports (and what it deliberately does not)
----------------------------------------------------
Two answer shapes are understood:

1. a fenced block carrying a path header - either in the fence info string
   (````` ```python path=tools/foo.py ````) or on the line directly before the
   fence (``**File: tools/foo.py**``);
2. a unified diff (``--- a/path`` / ``+++ b/path`` / ``@@`` hunks), applied
   strictly: a hunk whose context does not match the file on disk is refused
   rather than fuzzy-patched.

Anything else is reported as ``no_changes``: the module never guesses a file
name or a file body. Deletions are not supported (no shape produces one, and a
diff that would delete a file is refused), because "Forge never deletes files"
is a rail, not an omission. Writing outside the workspace, into ``.git``/``.venv``
``/node_modules``/``dist``/migrations, or into credential-shaped names is refused
by name, by containment, and by the project's own secret recognizer.
"""

from __future__ import annotations

import difflib
import os
import py_compile
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.execution.paths import PathSecurityError, normalize_workspace_relative_path
from app.execution.sandbox import WorkspaceBoundary, WorkspaceContainmentError
from app.execution.secret_files import is_secret_directory, secret_reason
from app.runtime.run_store import is_safe_run_id

__all__ = [
    "BACKUP_DIRECTORY",
    "MAX_FILES",
    "MAX_TOTAL_BYTES",
    "STATUS_APPLIED",
    "STATUS_NO_CHANGES",
    "STATUS_REJECTED",
    "STATUS_VERIFICATION_FAILED",
    "TEMP_SUFFIX",
    "WriteResult",
    "apply_changes",
    "build_workspace_context",
    "deny_reason",
    "render_write_report",
]

# --------------------------------------------------------------------------- #
# limits and rails
# --------------------------------------------------------------------------- #

#: The most files one answer may touch. A bound, not a budget: it keeps one run's
#: change small enough to read, review and roll back, and cannot be raised by a
#: model answer or by an HTTP caller (the service never passes a wider value).
MAX_FILES = 20

#: The most bytes one run may write in total.
MAX_TOTAL_BYTES = 200 * 1024

#: The suffix each file is written through before ``os.replace`` makes it visible.
#: A crash can therefore leave at most a stale ``*.forge-new`` file, never a
#: half-written source file.
TEMP_SUFFIX = ".forge-new"

#: Where the pre-change copy of every modified file is kept, inside the workspace.
BACKUP_DIRECTORY = ".forge/backups"

STATUS_APPLIED = "applied"
STATUS_NO_CHANGES = "no_changes"
STATUS_REJECTED = "rejected"
STATUS_VERIFICATION_FAILED = "verification_failed"

VERIFICATION_NOT_RUN = "not_run"
VERIFICATION_NOT_APPLICABLE = "not_applicable"
VERIFICATION_PASSED = "passed"
VERIFICATION_FAILED = "failed"

#: Path components that may never be written, at any depth. The repository's own
#: guard rails: credentials, the Git database, the virtualenv, installed
#: dependencies, build output, the database migration history, and the
#: authorization module are all off limits to a model answer.
_DENIED_COMPONENTS = frozenset({".git", ".venv", "node_modules", "dist"})

#: Path fragments that may never be written, matched against the whole relative
#: path (case-insensitive, forward slashes).
_DENIED_PATH_PREFIXES = ("app/platform/persistence/postgres/migrations/",)

#: Single file names that may never be written, at any depth.
_DENIED_FILE_NAMES = frozenset({"authorizer.py"})

#: Markers in a file name that mean "credential material". A substring match, so
#: ``api_token.txt`` and ``client_secret.json`` are both refused.
_DENIED_NAME_MARKERS = ("secret", "credential", "token")

#: Directories never listed into a workspace context, and never descended into.
_CONTEXT_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".forge",
        "node_modules",
        "dist",
        "build",
        "target",
    }
)

#: Bounds for the compact workspace context handed to a model.
MAX_CONTEXT_ENTRIES = 200
MAX_CONTEXT_DEPTH = 6


# --------------------------------------------------------------------------- #
# result
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WriteResult:
    """What one answer actually did to the workspace.

    ``status`` is one of the four module constants. ``files`` reports one entry
    per applied file with its action and the exact number of added/removed lines.
    ``diff`` is a real unified diff computed from the before and after bytes, not
    a paraphrase of the answer. ``reason`` carries the refusal, the failure, or
    the reason nothing was applied. ``verification`` reports the post-write
    ``py_compile`` outcome, which is what the service persists beside the diff.
    """

    status: str
    files: tuple[dict[str, object], ...] = ()
    diff: str = ""
    reason: str = ""
    verification: str = VERIFICATION_NOT_RUN

    @property
    def is_applied(self) -> bool:
        return self.status == STATUS_APPLIED

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "files": [dict(item) for item in self.files],
            "diff": self.diff,
            "reason": self.reason,
            "verification": self.verification,
        }


# --------------------------------------------------------------------------- #
# path rails
# --------------------------------------------------------------------------- #


def deny_reason(relative_path: str) -> str | None:
    """Return the rule that refuses ``relative_path``, or ``None`` when allowed.

    ``relative_path`` must already be normalized and workspace-relative. The
    rules are name- and shape-based and deterministic; a reason rather than a
    boolean so a caller can report *why* a path was refused without echoing the
    file's contents.
    """

    if not isinstance(relative_path, str) or not relative_path.strip():
        return "empty_path"
    lowered = relative_path.replace("\\", "/").casefold()
    parts = [part for part in lowered.split("/") if part]
    for part in parts:
        if part in _DENIED_COMPONENTS:
            return f"denied_directory:{part}"
        if part == ".env" or part.startswith(".env."):
            return "denied_environment_file"
        if part in _DENIED_FILE_NAMES:
            return f"denied_file:{part}"
        if any(marker in part for marker in _DENIED_NAME_MARKERS):
            return "denied_credential_like_name"
        if is_secret_directory(part):
            return "denied_secret_directory"
    for prefix in _DENIED_PATH_PREFIXES:
        if lowered.startswith(prefix):
            return "denied_migration_history"
    recognized = secret_reason(PurePosixPath(lowered).name)
    if recognized is not None:
        return f"denied_secret_file:{recognized}"
    return None


# --------------------------------------------------------------------------- #
# parsing: model answer -> raw changes
# --------------------------------------------------------------------------- #


class _ParseError(Exception):
    """The answer looked like a change but could not be read deterministically."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _FencedChange:
    """A file body carried by one fenced block with a path header."""

    path: str
    content: str


@dataclass(frozen=True)
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int


@dataclass(frozen=True)
class _DiffChange:
    """One file section of a unified diff."""

    old_path: str
    new_path: str
    hunks: tuple[_Hunk, ...] = ()
    body: tuple[tuple[str, ...], ...] = ()


# A fence that opens at the beginning of a line and closes at the beginning of a
# line. ``DOTALL`` is what lets the body span lines; ``MULTILINE`` anchors ``^``.
_FENCE = re.compile(
    r"^```[ \t]*(?P<header>[^\n]*)\n(?P<body>.*?)^```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)

# ``path=tools/foo.py`` (optionally quoted) anywhere in the fence info string.
_PATH_ATTRIBUTE = re.compile(
    r"""(?:^|[\s,;])(?:path|file)\s*=\s*"""
    r"""(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s,;`]+))""",
    re.IGNORECASE,
)

# ``**File: tools/foo.py**`` and ``File: tools/foo.py`` on the line before a fence.
# The colon is required: without it any sentence beginning with "Files" would look
# like a header, and a guessed path is exactly what this module must never do.
_FILE_MARKER = re.compile(r"^(?:file|path)\s*:\s*(?P<value>.+)$", re.IGNORECASE)

_HUNK_HEADER = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)

_DEV_NULL = ("/dev/null", "nul", "NUL")


def _clean_path_candidate(value: object) -> str:
    """Strip the punctuation a model wraps a path in, or return an empty string."""

    if not isinstance(value, str):
        return ""
    candidate = value.strip().strip("\"'`").strip()
    candidate = candidate.strip("<>").strip()
    candidate = candidate.strip("`*_").strip()
    candidate = candidate.rstrip(".,;:!?")
    candidate = candidate.replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    return candidate


def _path_from_fence_header(header: str) -> str:
    match = _PATH_ATTRIBUTE.search(header or "")
    if match is None:
        return ""
    for group in ("double", "single", "bare"):
        value = match.group(group)
        if value:
            return _clean_path_candidate(value)
    return ""


def _path_from_preceding_line(answer: str, fence_start: int) -> str:
    """The ``**File: path**`` marker on the last non-empty line before a fence."""

    prefix = answer[:fence_start]
    lines = [line for line in prefix.split("\n") if line.strip()]
    if not lines:
        return ""
    candidate = lines[-1].strip()
    if candidate.startswith("**"):
        candidate = candidate[2:].strip()
    match = _FILE_MARKER.match(candidate)
    if match is None:
        return ""
    raw_value = match.group("value").strip().strip("*").strip()
    # A bare sentence that happens to start with "File:" is still only a marker
    # when the value looks like a path (it contains a dot or a separator).
    value = _clean_path_candidate(raw_value)
    if not value:
        return ""
    if "/" not in value and "." not in value:
        return ""
    return value


def _parse_fenced(answer: str) -> list[_FencedChange]:
    changes: list[_FencedChange] = []
    for match in _FENCE.finditer(answer):
        header = match.group("header") or ""
        path = _path_from_fence_header(header) or _path_from_preceding_line(
            answer, match.start()
        )
        if not path:
            # A fenced block with no path is a code sample in prose, not a change.
            continue
        changes.append(_FencedChange(path=path, content=match.group("body") or ""))
    return changes


def _strip_diff_header_path(value: str) -> str:
    """``a/tools/foo.py`` and ``b/tools/foo.py`` name ``tools/foo.py``."""

    candidate = _clean_path_candidate(value)
    for prefix in ("a/", "b/"):
        if candidate.startswith(prefix):
            return candidate[len(prefix) :]
    return candidate


def _is_dev_null(value: str) -> bool:
    return _clean_path_candidate(value) in _DEV_NULL


def _is_diff_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and lines[index].startswith("--- ")
        and lines[index + 1].startswith("+++ ")
    )


def _parse_unified_diff(answer: str) -> list[_DiffChange]:
    """Parse every ``---``/``+++``/``@@`` section of the answer, strictly.

    The hunk body is bounded by the counts the header declares, so prose that
    follows a diff is not mistaken for hunk content, and a hunk that ends early
    is refused instead of being applied partially.
    """

    lines = [line[:-1] if line.endswith("\r") else line for line in answer.split("\n")]
    changes: list[_DiffChange] = []
    index = 0
    while index < len(lines) - 1:
        if not _is_diff_start(lines, index):
            index += 1
            continue
        old_path = lines[index][4:].strip()
        new_path = lines[index + 1][4:].strip()
        index += 2
        hunks: list[_Hunk] = []
        bodies: list[tuple[str, ...]] = []
        while index < len(lines) and lines[index].startswith("@@"):
            header = _HUNK_HEADER.match(lines[index])
            if header is None:
                raise _ParseError("malformed_hunk_header")
            old_start = int(header.group("old_start"))
            old_count = int(header.group("old_count") or 1)
            new_start = int(header.group("new_start"))
            new_count = int(header.group("new_count") or 1)
            index += 1
            body: list[str] = []
            seen_old = 0
            seen_new = 0
            while index < len(lines) and (
                seen_old < old_count or seen_new < new_count
            ):
                raw = lines[index]
                if raw.startswith("\\"):
                    # "\ No newline at end of file": metadata, not a hunk line.
                    index += 1
                    continue
                if raw.startswith("+++ ") or raw.startswith("--- "):
                    # A header inside a hunk body means the hunk is incomplete.
                    raise _ParseError("hunk_incomplete")
                if raw.startswith("+"):
                    seen_new += 1
                elif raw.startswith("-"):
                    seen_old += 1
                elif raw[:1] in (" ", ""):
                    seen_old += 1
                    seen_new += 1
                else:
                    raise _ParseError("unrecognized_hunk_line")
                if seen_old > old_count or seen_new > new_count:
                    raise _ParseError("hunk_count_mismatch")
                body.append(raw)
                index += 1
            if seen_old != old_count or seen_new != new_count:
                raise _ParseError("hunk_incomplete")
            hunks.append(
                _Hunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                )
            )
            bodies.append(tuple(body))
        if not hunks:
            raise _ParseError("diff_without_hunks")
        changes.append(
            _DiffChange(
                old_path=old_path,
                new_path=new_path,
                hunks=tuple(hunks),
                body=tuple(bodies),
            )
        )
    return changes


def _line_equal(file_line: str, diff_text: str) -> bool:
    """Compare one file line against a diff line, ignoring its line ending."""

    return file_line.rstrip("\r\n") == diff_text


def _apply_hunks(
    before_text: str,
    change: _DiffChange,
    *,
    create: bool,
) -> str:
    """Apply a parsed diff to ``before_text``, or raise ``_ParseError``.

    Strict by design: a context or removed line that does not match the file on
    disk refuses the whole change. A fuzzy patch would silently write a file the
    model never saw, which is exactly the failure this rail exists to prevent.
    Unchanged lines are carried over from the file itself, so their bytes (and
    their line endings) are preserved.
    """

    before_lines = before_text.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    for hunk, body in zip(change.hunks, change.body):
        start = max(hunk.old_start - 1, 0)
        if create:
            if hunk.old_count != 0:
                raise _ParseError("create_hunk_removes_lines")
            if hunk.old_start not in (0, 1):
                raise _ParseError("create_hunk_not_from_empty_file")
        else:
            if start < cursor:
                raise _ParseError("hunk_overlaps_previous_hunk")
            if start > len(before_lines):
                raise _ParseError("hunk_beyond_end_of_file")
            output.extend(before_lines[cursor:start])
            cursor = start
        for raw in body:
            if raw.startswith("+"):
                output.append(raw[1:] + "\n")
                continue
            if raw.startswith("-"):
                if create:
                    raise _ParseError("create_hunk_removes_lines")
                if cursor >= len(before_lines) or not _line_equal(
                    before_lines[cursor], raw[1:]
                ):
                    raise _ParseError("diff_context_mismatch")
                cursor += 1
                continue
            text = raw[1:] if raw[:1] == " " else raw
            if create:
                # A context line in a creation hunk is a contradiction: there is
                # no before-file to carry it over from.
                raise _ParseError("create_hunk_has_context_lines")
            if cursor >= len(before_lines) or not _line_equal(
                before_lines[cursor], text
            ):
                raise _ParseError("diff_context_mismatch")
            output.append(before_lines[cursor])
            cursor += 1
    if not create:
        output.extend(before_lines[cursor:])
    return "".join(output)


# --------------------------------------------------------------------------- #
# planning: validated changes, resolved inside the boundary
# --------------------------------------------------------------------------- #


class _Refusal(Exception):
    """One change is outside what this module may write."""

    def __init__(self, reason: str, *, path: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.path = path


@dataclass
class _PlannedFile:
    """One validated change, fully resolved before anything is written."""

    path: str
    action: str
    target: Path
    before: bytes | None
    after: bytes
    backup: Path | None = None


def _boundary_of(workspace: object) -> WorkspaceBoundary:
    """Build the canonical boundary from the caller's own workspace contract."""

    root = getattr(workspace, "root", workspace)
    checker = getattr(workspace, "assert_available", None)
    if callable(checker):
        # Re-proves the directory is still the one that was opened, in the same
        # spirit as every other workspace operation.
        checker()
    return WorkspaceBoundary(root=root)


def _backup_path(boundary: WorkspaceBoundary, run_id: str, relative: str) -> Path:
    """The backup location for one file, contained inside the workspace."""

    if not is_safe_run_id(run_id):
        raise _Refusal("run_id_is_not_usable_as_a_backup_directory")
    return boundary.resolve_relative(f"{BACKUP_DIRECTORY}/{run_id}/{relative}")


def _read_existing(target: Path) -> bytes | None:
    """The file's bytes, or ``None`` when it does not exist.

    A symlink, a directory, or any other non-regular entry is refused rather
    than followed: writing "through" such a name would change an object the
    model did not name.
    """

    if target.is_symlink():
        raise _Refusal("target_is_a_symlink", path=target.name)
    if not target.exists():
        return None
    if target.is_dir():
        raise _Refusal("target_is_a_directory", path=target.name)
    if not target.is_file():
        raise _Refusal("target_is_not_a_regular_file", path=target.name)
    try:
        return target.read_bytes()
    except OSError as error:
        raise _Refusal(f"target_unreadable:{type(error).__name__}", path=target.name)


def _plan_changes(
    changes: list[_FencedChange] | list[_DiffChange],
    boundary: WorkspaceBoundary,
    *,
    max_files: int,
    max_total_bytes: int,
) -> list[_PlannedFile]:
    """Validate every change before a byte is written, or refuse the whole run."""

    planned: list[_PlannedFile] = []
    seen: set[str] = set()
    total = 0
    for change in changes:
        if isinstance(change, _DiffChange):
            raw_path = _strip_diff_header_path(change.new_path)
            old_path = _strip_diff_header_path(change.old_path)
        else:
            raw_path = change.path
            old_path = ""
        if isinstance(change, _DiffChange) and _is_dev_null(change.new_path):
            raise _Refusal("unified_diff_deletes_a_file", path=raw_path)
        try:
            normalized = normalize_workspace_relative_path(raw_path)
        except PathSecurityError as error:
            raise _Refusal(f"invalid_path:{error}", path=str(raw_path)) from error
        denied = deny_reason(normalized)
        if denied is not None:
            raise _Refusal(denied, path=normalized)
        if normalized in seen:
            raise _Refusal("duplicate_path_in_model_answer", path=normalized)
        seen.add(normalized)
        try:
            target = boundary.resolve_relative(normalized)
        except WorkspaceContainmentError as error:
            raise _Refusal(f"outside_workspace:{error}", path=normalized) from error
        # The canonical target must be exactly the lexical one: a link anywhere
        # on the way would make "write this path" mean "write another file".
        lexical = boundary.root.joinpath(*normalized.split("/"))
        if target != lexical:
            raise _Refusal("path_crosses_a_link", path=normalized)
        before = _read_existing(target)
        try:
            if isinstance(change, _FencedChange):
                content = change.content
            elif before is None:
                if not _is_dev_null(old_path):
                    raise _Refusal("unified_diff_target_missing", path=normalized)
                content = _apply_hunks("", change, create=True)
            else:
                content = _apply_hunks(
                    before.decode("utf-8", "replace"), change, create=False
                )
        except _ParseError as error:
            raise _Refusal(f"unified_diff_not_applicable:{error.code}", path=normalized)
        encoded = content.encode("utf-8")
        total += len(encoded)
        if total > max_total_bytes:
            raise _Refusal(
                f"total_bytes_cap_exceeded:{max_total_bytes}", path=normalized
            )
        if len(planned) + 1 > max_files:
            raise _Refusal(f"file_cap_exceeded:{max_files}", path=normalized)
        planned.append(
            _PlannedFile(
                path=normalized,
                action="create" if before is None else "update",
                target=target,
                before=before,
                after=encoded,
            )
        )
    return planned


# --------------------------------------------------------------------------- #
# applying, diffing, verifying, rolling back
# --------------------------------------------------------------------------- #


def _atomic_write(target: Path, data: bytes) -> None:
    """Write ``target`` through ``<target>.forge-new`` then ``os.replace``."""

    temporary = target.with_name(target.name + TEMP_SUFFIX)
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _text_of(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _diff_for(path: str, before: bytes | None, after: bytes) -> tuple[str, int, int]:
    """The real unified diff of one file, with its added/removed line counts.

    The comparison is line-by-line over the decoded content, and the rendered diff
    always uses ``\\n``: a diff is a textual description, and letting a CRLF file
    print ``\\r`` into it would make one hunk unreadable without describing the
    change any more precisely. The file itself keeps the bytes it had - unchanged
    lines are carried over verbatim, line endings included.
    """

    old_text = "" if before is None else _text_of(before)
    new_text = _text_of(after)
    from_name = "/dev/null" if before is None else f"a/{path}"
    to_name = f"b/{path}"
    chunks = list(
        difflib.unified_diff(
            [f"{line}\n" for line in old_text.splitlines()],
            [f"{line}\n" for line in new_text.splitlines()],
            fromfile=from_name,
            tofile=to_name,
            n=3,
        )
    )
    added = 0
    removed = 0
    for line in chunks:
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    text = "".join(chunks)
    if text and not text.endswith("\n"):
        text += "\n"
    return text, added, removed


def _compile_python(target: Path, check_directory: Path, index: int) -> str | None:
    """Compile one Python file, inside the workspace, leaving nothing behind.

    The same check ``python -m py_compile`` performs, run in-process so nothing is
    started and no shell is involved. The bytecode is written to the run's own
    scratch directory under ``.forge/backups/<run_id>`` and removed immediately, so
    the verification touches nothing outside the workspace and the workspace keeps
    exactly the source files the run wrote.
    """

    cfile = check_directory / f"check-{index}.pyc"
    try:
        check_directory.mkdir(parents=True, exist_ok=True)
        py_compile.compile(file=str(target), cfile=str(cfile), doraise=True)
    except py_compile.PyCompileError as error:
        return f"py_compile_error:{type(error).__name__}"
    except (OSError, ValueError) as error:
        return f"compile_error:{type(error).__name__}"
    finally:
        try:
            cfile.unlink()
        except OSError:
            pass
    return None


def _rollback(records: list[tuple[_PlannedFile, Path | None]]) -> list[str]:
    """Restore every file of this run; return the paths that could not be restored."""

    failed: list[str] = []
    for item, backup in reversed(records):
        try:
            if backup is not None:
                _atomic_write(item.target, backup.read_bytes())
            else:
                item.target.unlink(missing_ok=True)
        except OSError:
            failed.append(item.path)
    return failed


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #


def carries_file_changes(answer: object) -> bool:
    """True when the answer carries at least one file change the writer would read.

    Read-only by contract: it parses the answer and returns a boolean, so a caller
    can decide whether the answer is worth asking the model about *before* anything
    is written (this is what the single write-mode retry keys on). It never writes,
    never raises and never guesses a path.

    An answer that looks like a change but cannot be read deterministically reports
    True: the writer's own refusal, with its parse error, is the honest outcome
    there -- asking the model again would not make a malformed block valid.
    """

    text = answer if isinstance(answer, str) else ""
    if not text.strip():
        return False
    try:
        if _parse_unified_diff(text):
            return True
    except _ParseError:
        return True
    return bool(_parse_fenced(text))


def apply_changes(
    answer: object,
    *,
    workspace: object,
    run_id: str,
    max_files: int = MAX_FILES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> WriteResult:
    """Turn one model answer into applied, verified file changes.

    ``workspace`` is the caller's own workspace contract (an object with an
    absolute ``Path`` root, as ``RunScope.workspace`` is); ``run_id`` names the
    backup directory under ``.forge/backups`` and is validated as a directory
    name with the run store's own rule.

    Never raises for a refused or unparseable answer: the outcome is the
    returned :class:`WriteResult`, whose ``status`` is one of the four module
    constants. The workspace is left untouched for every status except
    ``applied`` (which is verified) and ``no_changes``/``rejected`` (which wrote
    nothing); ``verification_failed`` restores what was there before.
    """

    text = answer if isinstance(answer, str) else ""
    if not isinstance(run_id, str) or not is_safe_run_id(run_id):
        return WriteResult(
            status=STATUS_REJECTED,
            reason="run_id_is_not_usable_as_a_backup_directory",
        )
    if not text.strip():
        return WriteResult(
            status=STATUS_NO_CHANGES, reason="the model answer is empty"
        )

    try:
        boundary = _boundary_of(workspace)
    except WorkspaceContainmentError as error:
        return WriteResult(
            status=STATUS_REJECTED,
            reason=f"workspace_not_usable:{type(error).__name__}",
        )
    except Exception as error:  # noqa: BLE001 - an unusable workspace is a refusal
        return WriteResult(
            status=STATUS_REJECTED,
            reason=f"workspace_unavailable:{type(error).__name__}",
        )

    # A unified diff wins over fenced blocks: a diff states both the file and the
    # exact lines it replaces, which is the more precise claim.
    try:
        diff_changes = _parse_unified_diff(text)
    except _ParseError as error:
        return WriteResult(
            status=STATUS_REJECTED,
            reason=f"model_answer_not_parseable:{error.code}",
        )
    if diff_changes:
        changes: list[_FencedChange] | list[_DiffChange] = diff_changes
    else:
        changes = _parse_fenced(text)
    if not changes:
        return WriteResult(
            status=STATUS_NO_CHANGES,
            reason=(
                "the model answer carries no fenced block with a path header "
                "and no unified diff"
            ),
        )

    try:
        planned = _plan_changes(
            changes,
            boundary,
            max_files=max(1, int(max_files)),
            max_total_bytes=max(0, int(max_total_bytes)),
        )
    except _Refusal as refusal:
        reason = refusal.reason
        if refusal.path:
            reason = f"{reason}:{refusal.path}"
        return WriteResult(status=STATUS_REJECTED, reason=reason)
    except Exception as error:  # noqa: BLE001 - planning failures are refusals
        return WriteResult(
            status=STATUS_REJECTED, reason=f"planning_failed:{type(error).__name__}"
        )

    if not planned:
        return WriteResult(status=STATUS_NO_CHANGES, reason="no change survived validation")

    # The diff is computed from the bytes that are about to be written and the
    # bytes that are on disk now, so it is a description of the real change.
    diffs: list[str] = []
    file_entries: list[dict[str, object]] = []
    for item in planned:
        text_diff, added, removed = _diff_for(item.path, item.before, item.after)
        diffs.append(text_diff)
        file_entries.append(
            {
                "path": item.path,
                "action": item.action,
                "added": added,
                "removed": removed,
            }
        )
    diff = "".join(diffs)

    records: list[tuple[_PlannedFile, Path | None]] = []
    try:
        for item in planned:
            backup: Path | None = None
            if item.before is not None:
                backup_target = _backup_path(boundary, run_id, item.path)
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(backup_target, item.before)
                backup = backup_target
            item.target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(item.target, item.after)
            records.append((item, backup))
    except OSError as error:
        restored_failures = _rollback(records)
        reason = f"write_failed:{type(error).__name__}"
        if restored_failures:
            reason += f":rollback_incomplete:{len(restored_failures)}"
        return WriteResult(status=STATUS_REJECTED, reason=reason)
    except Exception as error:  # noqa: BLE001 - any write failure fails closed
        restored_failures = _rollback(records)
        reason = f"write_failed:{type(error).__name__}"
        if restored_failures:
            reason += f":rollback_incomplete:{len(restored_failures)}"
        return WriteResult(status=STATUS_REJECTED, reason=reason)

    # Verification: every Python file the run touched must compile. A failure
    # rolls the WHOLE run back, so a broken tree is never left behind. The check
    # directory is this run's own scratch area inside the workspace, and it is
    # removed once the check is done.
    python_files = [item for item in planned if item.path.endswith(".py")]
    check_directory: Path | None = None
    if python_files:
        try:
            check_directory = _backup_path(boundary, run_id, "syntax-check")
            check_directory.mkdir(parents=True, exist_ok=True)
        except (OSError, _Refusal):
            check_directory = None
    broken: list[str] = []
    for index, item in enumerate(python_files):
        if check_directory is None:
            broken.append(f"{item.path}:compile_scratch_unavailable")
            continue
        failure = _compile_python(item.target, check_directory, index)
        if failure is not None:
            broken.append(f"{item.path}:{failure}")
    if check_directory is not None:
        try:
            check_directory.rmdir()
        except OSError:
            pass
    if broken:
        restored_failures = _rollback(records)
        reason = "py_compile_failed:" + ",".join(broken)
        if restored_failures:
            reason += f":rollback_incomplete:{len(restored_failures)}"
        return WriteResult(
            status=STATUS_VERIFICATION_FAILED,
            reason=reason,
            verification=VERIFICATION_FAILED,
        )

    return WriteResult(
        status=STATUS_APPLIED,
        files=tuple(file_entries),
        diff=diff,
        reason="",
        verification=(
            VERIFICATION_PASSED if python_files else VERIFICATION_NOT_APPLICABLE
        ),
    )


# --------------------------------------------------------------------------- #
# the model prompt's workspace context
# --------------------------------------------------------------------------- #


def build_workspace_context(
    workspace: object,
    *,
    max_entries: int = MAX_CONTEXT_ENTRIES,
    max_depth: int = MAX_CONTEXT_DEPTH,
) -> str:
    """A compact, bounded list of the workspace's files for the model prompt.

    Only names are listed - never contents - because a prompt is not a place to
    copy a source tree, and because a file's contents may be larger than the
    request budget. Credential-shaped files and directories, ``.git``, the
    virtualenv, dependencies, build output and the run store are all skipped, so
    the context cannot become a way to read what the rest of this module refuses
    to write. Deterministic: sorted, bounded, and identical for identical trees.
    """

    try:
        # ``Path`` is checked first on purpose: a ``WindowsPath`` has its own
        # ``.root`` attribute (the drive-relative ``"\\"``), so ``getattr(p, "root",
        # p)`` would silently name the drive root instead of the workspace. Only a
        # workspace *contract* object (``Workspace``) carries a useful ``.root``.
        if isinstance(workspace, (str, os.PathLike)):
            root = Path(workspace)
        else:
            root = Path(getattr(workspace, "root", workspace))
    except (TypeError, ValueError):
        return ""
    if not root.is_dir():
        return ""
    limit = max(1, int(max_entries))
    depth_limit = max(1, int(max_depth))
    found: list[str] = []
    truncated = False
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack and not truncated:
        current, depth = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        child_directories: list[tuple[Path, int]] = []
        for entry in entries:
            name = entry.name
            try:
                is_directory = entry.is_dir() and not entry.is_symlink()
            except OSError:
                continue
            if is_directory:
                lowered = name.casefold()
                if (
                    lowered in _CONTEXT_IGNORED_DIRECTORIES
                    or is_secret_directory(lowered)
                    or deny_reason(name) is not None
                ):
                    continue
                if depth < depth_limit:
                    child_directories.append((entry, depth + 1))
                continue
            if entry.is_symlink() or secret_reason(name) is not None:
                continue
            try:
                relative = entry.relative_to(root).as_posix()
            except ValueError:
                continue
            if deny_reason(relative) is not None:
                continue
            found.append(relative)
            if len(found) >= limit:
                truncated = True
                break
        # Sorted stack keeps the walk deterministic: last pushed is visited first.
        stack.extend(reversed(child_directories))
    if not found:
        return "Workspace context: (no files were visible at the workspace root)."
    lines = [
        "Workspace context (bounded file list; contents are deliberately not included):"
    ]
    lines.extend(f"- {path}" for path in found)
    if truncated:
        lines.append(
            f"(list truncated at {limit} entries; more files exist than are shown)"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #


def render_write_report(result: WriteResult) -> str:
    """A bounded, human-readable report of one write attempt.

    Every line is derived from the result: a refusal is printed as a refusal, a
    verification failure as a failure, and the diff is the one that was really
    applied. The report never invents a success marker.
    """

    lines = [f"CODE WRITER: {result.status}"]
    if result.files:
        lines.append(f"Files ({len(result.files)}):")
        for item in result.files:
            lines.append(
                "  {action} {path} (+{added} -{removed})".format(
                    action=str(item.get("action", "")),
                    path=str(item.get("path", "")),
                    added=int(item.get("added", 0) or 0),
                    removed=int(item.get("removed", 0) or 0),
                )
            )
    else:
        lines.append("Files: none")
    if result.verification == VERIFICATION_PASSED:
        lines.append("Verification: py_compile passed for every Python file written")
    elif result.verification == VERIFICATION_NOT_APPLICABLE:
        lines.append("Verification: no Python file was written, nothing to compile")
    elif result.verification == VERIFICATION_FAILED:
        lines.append("Verification: py_compile FAILED; every file of this run was rolled back")
    else:
        lines.append("Verification: not run")
    if result.reason:
        lines.append(f"Reason: {result.reason}")
    if result.diff:
        lines.append("Diff:")
        lines.append(result.diff.rstrip("\n"))
    return "\n".join(lines)
