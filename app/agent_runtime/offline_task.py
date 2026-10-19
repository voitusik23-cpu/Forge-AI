"""Deterministic offline task work: what the offline provider really does.

Why this module exists
----------------------
The offline (``mock``) provider is the only provider this composition may use
without credentials, without network access and without cost. Its provider call
returns one canned sentence, so a submitted task used to produce exactly one line
of text and no work at all.

This module gives the offline path something real to do **without widening any
perimeter**:

* the submitted description is turned into a bounded, ordered list of read-only
  workspace checks (:class:`OfflineStep` / :class:`OfflineCheck`). The parsing is
  deterministic keyword and path extraction: no model, no network, no randomness,
  so the same text always produces the same work;
* one :class:`~app.tools.acceptance.AcceptanceCriterion` is derived per check, so
  the run's verdict is about checks that were really performed rather than about
  a placeholder;
* :class:`OfflineWorkspaceVerifier` performs each check against the service's own
  :class:`~app.tools.workspace.Workspace` (list a directory, read the first N
  lines of a file, prove a path exists, count entries) and records what it
  observed. It never writes, never starts a process, never opens a socket, and it
  refuses credential-shaped paths through the canonical secret recognizer;
* :class:`OfflineTaskDecisionProvider` drives the existing
  :class:`~app.agent_runtime.harness.AgentHarness` run loop: verify, then
  complete on a passing acceptance, otherwise fail. It can never select
  ``EXECUTE`` or ``INVOKE_TOOL``, so on this path the loop has no way to reach a
  host command or a tool at all.

What this is not
----------------
This is **not** authority. The description selects *which read-only observation*
is made - the same trust position the operating surface already takes for an
allow-listed workspace-relative path - and never a command, a tool, a workspace,
a profile, an environment, an approval, or a project. Everything that decides
what a run may touch still comes from the composition: the service's own
``Workspace``, its execution profile, and the frozen ``RunScope``.

It is also not model work: nothing here calls a provider. The offline run performs
deterministic observation and reports exactly what it observed. When it cannot
observe something it says so (``DENIED`` / ``FAIL`` / ``ERROR``), and a failed
check is a failed acceptance, never a pass.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from app.config.settings import OFFLINE_PROVIDER_NAME
from app.decision.models import (
    Decision,
    DecisionAction,
    DecisionRequest,
    DecisionType,
)
from app.execution.secret_files import is_secret_directory, secret_reason
from app.orchestrator.models import EventType
from app.tools.acceptance import AcceptanceCriterion
from app.tools.verification import VerificationEvidence, VerificationResult, VerificationStatus
from app.tools.workspace import Workspace, WorkspacePathError

__all__ = [
    "MAX_STEPS",
    "MAX_EXCERPT_LINES",
    "MAX_LISTING_ENTRIES",
    "OfflineCheck",
    "OfflineStep",
    "OfflineStepKind",
    "OfflineTaskPlan",
    "OfflineTaskDecisionProvider",
    "OfflineWorkspaceVerifier",
    "build_offline_task_plan",
    "render_offline_task_report",
]

#: A submitted task is turned into at most this many checks. A bound, not a budget:
#: it keeps one run's work small enough to read, and it cannot be raised per-run.
MAX_STEPS = 6

#: How many lines of a file a ``read`` step returns by default, and at most.
DEFAULT_EXCERPT_LINES = 5
MAX_EXCERPT_LINES = 20

#: Longest single piece of evidence kept from one line.
MAX_EXCERPT_CHARS = 200

#: Bounded directory listing and bounded walk, so one check cannot become a scan
#: of an arbitrarily large tree.
MAX_LISTING_ENTRIES = 25
MAX_WALK_ENTRIES = 4000

#: Directories a bounded walk never descends into. The same heavy/generated
#: directories the project scanner already skips, plus the run store itself.
_WALK_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        "bin",
        "obj",
    }
)


class OfflineStepKind(str, Enum):
    """What one read-only offline step observes."""

    #: List the entries of a directory (the workspace root when no path is named).
    LIST = "list"
    #: Read the first N lines of a named file.
    READ = "read"
    #: Prove that a named path exists.
    EXISTS = "exists"
    #: Count files and directories under a path (the workspace root by default).
    COUNT = "count"
    #: Summarise the workspace: root entries plus file/directory counts.
    INSPECT = "inspect"


@dataclass(frozen=True)
class OfflineStep:
    """One bounded intention derived from the submitted task text."""

    step_id: str
    kind: OfflineStepKind
    title: str
    target: str = ""
    lines: int = 0


@dataclass(frozen=True)
class OfflineCheck:
    """One workspace check the run really performs.

    It plays the role ``VerificationExpectation`` plays on the operator-declared
    path: the harness hands it to the run's verifier unchanged, keyed by the
    criterion it answers.
    """

    criterion_id: str
    kind: OfflineStepKind
    target: str
    title: str
    lines: int = 0


@dataclass(frozen=True)
class OfflineTaskPlan:
    """The deterministic plan for one offline task run."""

    description: str
    steps: tuple[OfflineStep, ...]
    checks: tuple[OfflineCheck, ...]
    criteria: tuple[AcceptanceCriterion, ...]

    @property
    def step_count(self) -> int:
        return len(self.steps)


# --------------------------------------------------------------------------- #
# derivation: task text -> bounded read-only checks
# --------------------------------------------------------------------------- #

#: Clause separators. Only hard boundaries are used (a new sentence, a new line, a
#: semicolon, an explicit "then"), so one written instruction stays one step and
#: the derivation cannot invent steps a reader would not recognize.
_CLAUSE_SPLIT = re.compile(r"[\r\n;]+|\s+(?:then|затем|потом)\s+", re.IGNORECASE)

_READ_WORDS = re.compile(
    r"(прочит|прочес|\bread\b|\bhead\b|первые|первых|первую|"
    r"first\s+\d+|строк|строка|линий|\blines?\b)",
    re.IGNORECASE,
)
_COUNT_WORDS = re.compile(
    r"(посчита|количеств|сколько|count\b|number\s+of\s+files)", re.IGNORECASE
)
_LIST_WORDS = re.compile(
    r"(перечисл|список|содержим|list\b|entries|каталог|папк|директори|корн)",
    re.IGNORECASE,
)
_EXISTS_WORDS = re.compile(r"(существу|exists?\b|наличи|presence)", re.IGNORECASE)

_LINE_COUNT = re.compile(r"(\d+)\s*(?:строк|строка|lines?\b|лини)", re.IGNORECASE)

#: A bare directory named after a preposition, e.g. "count files in app". Only
#: consulted when no path-like token is present, and only for list/count steps, so
#: ordinary prose cannot silently become a path.
_DIR_HINT = re.compile(
    r"(?:\bв\b|\bво\b|\bиз\b|\bпод\b|\bвнутри\b|\bin\b|\binside\b|\bunder\b|\bof\b)"
    r"\s+([A-Za-z0-9_.\-/\\]+)",
    re.IGNORECASE,
)

#: A path-like token: either something with a separator, or a name with an
#: extension. Deliberately narrow - a plain word is not a path.
_PATH_TOKEN = re.compile(
    r"(?:[A-Za-z0-9_.\-]+[\\/])+[A-Za-z0-9_.\-]*"  # a/b, a\b\, app/api
    r"|[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,10}"  # README.md, main.py
)

#: Tokens that look like a path but are not one in ordinary prose.
_NOT_A_PATH = frozenset(
    {
        ".env.example",
        "e.g",
        "i.e",
        "т.д",
        "т.е",
        "т.п",
        "etc",
        "vs",
    }
)


def _clean_target(token: str) -> str:
    """Normalize one extracted path token, or return an empty string."""

    candidate = token.strip().strip("\"'`()[]{}<>«»").strip()
    candidate = candidate.rstrip(".,:;!?")
    candidate = candidate.replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    candidate = candidate.rstrip("/")
    if not candidate or candidate in _NOT_A_PATH:
        return ""
    return candidate


def _extract_targets(text: str) -> tuple[str, ...]:
    """Return the path-like tokens of one clause, in order, de-duplicated."""

    found: list[str] = []
    for match in _PATH_TOKEN.finditer(text):
        target = _clean_target(match.group(0))
        if target and target not in found:
            found.append(target)
    return tuple(found)


def _directory_hint(text: str) -> str:
    """A bare directory named after a preposition, or an empty string."""

    match = _DIR_HINT.search(text)
    if match is None:
        return ""
    return _clean_target(match.group(1))


#: Words that follow a preposition in ordinary prose and are not paths.
_NOT_A_TARGET = frozenset(
    {
        "readme",
        "файлы",
        "файлов",
        "файл",
        "директории",
        "каталоге",
        "каталог",
        "проекте",
        "репозитории",
        "репозитория",
        "рабочей",
        "the",
        "files",
        "file",
        "this",
    }
)


def _kind_for(text: str) -> OfflineStepKind:
    """Classify one clause into exactly one read-only intention, deterministically.

    Order matters and is fixed: an instruction to read a file is a read even when
    it also says "check that it exists"; ``read`` already fails when the file is
    absent, so the stronger observation wins.
    """

    if _READ_WORDS.search(text):
        return OfflineStepKind.READ
    if _COUNT_WORDS.search(text):
        return OfflineStepKind.COUNT
    if _LIST_WORDS.search(text):
        return OfflineStepKind.LIST
    if _EXISTS_WORDS.search(text):
        return OfflineStepKind.EXISTS
    return OfflineStepKind.INSPECT


def _line_count_for(text: str) -> int:
    """The requested number of lines, bounded; the default when none is stated."""

    match = _LINE_COUNT.search(text)
    if match is None:
        return DEFAULT_EXCERPT_LINES
    try:
        requested = int(match.group(1))
    except (TypeError, ValueError):  # pragma: no cover - regex guarantees digits
        return DEFAULT_EXCERPT_LINES
    if requested <= 0:
        return DEFAULT_EXCERPT_LINES
    return min(requested, MAX_EXCERPT_LINES)


def _step_title(clause: str) -> str:
    """A bounded, single-line label for one step."""

    collapsed = " ".join(clause.split())
    if len(collapsed) <= 160:
        return collapsed
    return collapsed[:157] + "..."


def build_offline_task_plan(description: object) -> OfflineTaskPlan:
    """Derive this offline run's bounded, ordered read-only checks.

    Deterministic: the same text always produces the same steps, ids, kinds,
    targets and criteria. No model, no network, no clock, no randomness.
    """

    text = description if isinstance(description, str) else ""
    clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(text) if clause.strip()]
    # One instruction per line or sentence, capped. An empty or unusable
    # description still gets exactly one check (a workspace summary), so the run
    # always has something true to say.
    clauses = clauses[:MAX_STEPS] or [""]

    steps: list[OfflineStep] = []
    checks: list[OfflineCheck] = []
    criteria: list[AcceptanceCriterion] = []
    seen: set[tuple[str, str]] = set()
    for clause in clauses:
        targets = _extract_targets(clause)
        kind = _kind_for(clause)
        target = targets[0] if targets else ""
        if not target and kind in (OfflineStepKind.LIST, OfflineStepKind.COUNT):
            hint = _directory_hint(clause)
            if hint and hint.lower() not in _NOT_A_TARGET:
                target = hint
        if kind in (OfflineStepKind.READ, OfflineStepKind.EXISTS) and not target:
            # A read/existence instruction with no named path is not a check that
            # can be performed honestly, so it degrades to observing the
            # workspace instead of pretending to inspect something.
            kind = OfflineStepKind.INSPECT
        key = (kind.value, target)
        if key in seen:
            continue
        seen.add(key)
        index = len(steps) + 1
        step_id = f"offline-step-{index:02d}-{kind.value}"
        title = _step_title(clause) or f"{kind.value} {target or 'workspace'}"
        lines = _line_count_for(clause) if kind == OfflineStepKind.READ else 0
        steps.append(
            OfflineStep(
                step_id=step_id,
                kind=kind,
                title=title,
                target=target,
                lines=lines,
            )
        )
        criteria.append(
            AcceptanceCriterion(
                criterion_id=step_id,
                description=title,
                required=True,
                requirement_id="offline-task",
            )
        )
        checks.append(
            OfflineCheck(
                criterion_id=step_id,
                kind=kind,
                target=target,
                title=title,
                lines=lines,
            )
        )

    return OfflineTaskPlan(
        description=text.strip(),
        steps=tuple(steps),
        checks=tuple(checks),
        criteria=tuple(criteria),
    )


# --------------------------------------------------------------------------- #
# verification: really observe the workspace, read-only
# --------------------------------------------------------------------------- #


class _Refusal(Exception):
    """The path this check names is outside what an offline read may touch."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OfflineWorkspaceVerifier:
    """Perform offline checks against the run's own workspace.

    Implements the harness's verifier port (``verify(expectation, *, workspace,
    run_id, observer, verification_id=None, criterion_id=None)``) so the existing
    run loop drives it without any change to the loop. Every observation is
    read-only and bounded; nothing here writes, executes, or opens a socket.
    """

    def verify(
        self,
        expectation: object,
        *,
        workspace: object | None,
        run_id: str,
        observer: Callable[[EventType, Mapping[str, object]], None],
        verification_id: str | None = None,
        criterion_id: str | None = None,
    ) -> VerificationResult:
        check = expectation if isinstance(expectation, OfflineCheck) else None
        resolved_criterion = (
            criterion_id
            or (check.criterion_id if check is not None else "")
            or "offline-step"
        )
        identifier = verification_id or f"verification-{resolved_criterion}"

        if check is None:
            result = _result(
                identifier,
                resolved_criterion,
                VerificationStatus.DENIED,
                "invalid_offline_check",
            )
            _emit(observer, run_id, result, {})
            return result
        if not isinstance(workspace, Workspace):
            result = _result(
                identifier,
                resolved_criterion,
                VerificationStatus.DENIED,
                "workspace_unavailable",
            )
            _emit(observer, run_id, result, {})
            return result

        try:
            status, code, metadata, relative_path = self._observe(check, workspace)
        except _Refusal as refusal:
            status, code, metadata, relative_path = (
                VerificationStatus.DENIED,
                refusal.code,
                {"kind": check.kind.value},
                None,
            )
        except WorkspacePathError:
            status, code, metadata, relative_path = (
                VerificationStatus.DENIED,
                "workspace_or_path_rejected",
                {"kind": check.kind.value},
                None,
            )
        except OSError:
            status, code, metadata, relative_path = (
                VerificationStatus.ERROR,
                "observation_failed",
                {"kind": check.kind.value},
                None,
            )
        except Exception:  # noqa: BLE001 - a broken check must never pass
            status, code, metadata, relative_path = (
                VerificationStatus.ERROR,
                "verifier_error",
                {"kind": check.kind.value},
                None,
            )

        result = _result(
            identifier,
            resolved_criterion,
            status,
            code,
            relative_path=relative_path,
            metadata=metadata,
        )
        _emit(
            observer,
            run_id,
            result,
            {
                "kind": check.kind.value,
                "target": relative_path or (check.target or "."),
                "step": check.title,
            },
        )
        return result

    # -- one check ---------------------------------------------------------- #

    def _observe(
        self, check: OfflineCheck, workspace: Workspace
    ) -> tuple[VerificationStatus, str, dict[str, object], str | None]:
        if check.kind == OfflineStepKind.READ:
            return self._read(check, workspace)
        if check.kind == OfflineStepKind.EXISTS:
            return self._exists(check, workspace)
        if check.kind == OfflineStepKind.LIST:
            return self._list(check, workspace)
        if check.kind == OfflineStepKind.COUNT:
            return self._count(check, workspace)
        return self._inspect(workspace)

    def _resolve(self, workspace: Workspace, target: str) -> tuple[Any, str]:
        """Resolve one workspace-relative target, refusing credential material.

        An empty target means the workspace root. A target with ``..``, an
        absolute path, or a drive letter is refused by the canonical
        ``Workspace`` validator; a credential-shaped name or directory is refused
        by the canonical secret recognizer before anything is read.
        """

        if not target:
            return workspace.verify_target(()), ""
        parts = workspace.normalize_relative_path(target)
        for part in parts:
            if is_secret_directory(part):
                raise _Refusal("secret_directory_refused")
        final = str(parts[-1])
        if secret_reason(final) is not None:
            raise _Refusal("secret_file_refused")
        path, normalized, _parts = workspace.resolve_target(target)
        return path, normalized

    def _read(
        self, check: OfflineCheck, workspace: Workspace
    ) -> tuple[VerificationStatus, str, dict[str, object], str | None]:
        path, normalized = self._resolve(workspace, check.target)
        if not path.exists():
            return (
                VerificationStatus.FAIL,
                "expected_file_missing",
                {"kind": check.kind.value, "actual_exists": False},
                normalized,
            )
        if not path.is_file():
            return (
                VerificationStatus.FAIL,
                "target_not_regular_file",
                {"kind": check.kind.value, "actual_exists": True},
                normalized,
            )
        wanted = check.lines or DEFAULT_EXCERPT_LINES
        wanted = max(1, min(int(wanted), MAX_EXCERPT_LINES))
        lines: list[str] = []
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for _ in range(wanted):
                line = stream.readline()
                if line == "":
                    break
                collapsed = line.rstrip("\r\n")
                if len(collapsed) > MAX_EXCERPT_CHARS:
                    collapsed = collapsed[: MAX_EXCERPT_CHARS - 3] + "..."
                lines.append(collapsed)
        size_bytes = path.stat().st_size
        return (
            VerificationStatus.PASS,
            "file_read",
            {
                "kind": check.kind.value,
                "actual_exists": True,
                "size_bytes": size_bytes,
                "lines_requested": wanted,
                "lines_returned": len(lines),
                "evidence": lines,
            },
            normalized,
        )

    def _exists(
        self, check: OfflineCheck, workspace: Workspace
    ) -> tuple[VerificationStatus, str, dict[str, object], str | None]:
        path, normalized = self._resolve(workspace, check.target)
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if info is None:
            return (
                VerificationStatus.FAIL,
                "expected_file_missing",
                {"kind": check.kind.value, "actual_exists": False},
                normalized,
            )
        is_directory = path.is_dir()
        metadata: dict[str, object] = {
            "kind": check.kind.value,
            "actual_exists": True,
            "is_directory": is_directory,
        }
        if not is_directory:
            metadata["size_bytes"] = info.st_size
        return (VerificationStatus.PASS, "expectation_matched", metadata, normalized)

    def _list(
        self, check: OfflineCheck, workspace: Workspace
    ) -> tuple[VerificationStatus, str, dict[str, object], str | None]:
        path, normalized = self._resolve(workspace, check.target)
        if not path.is_dir():
            return (
                VerificationStatus.FAIL,
                "target_not_a_directory",
                {"kind": check.kind.value, "actual_exists": path.exists()},
                normalized,
            )
        entries = sorted(os.listdir(path))
        shown = entries[:MAX_LISTING_ENTRIES]
        return (
            VerificationStatus.PASS,
            "directory_listed",
            {
                "kind": check.kind.value,
                "entries": shown,
                "entry_count": len(entries),
                "truncated": len(entries) > len(shown),
            },
            normalized,
        )

    def _count(
        self, check: OfflineCheck, workspace: Workspace
    ) -> tuple[VerificationStatus, str, dict[str, object], str | None]:
        path, normalized = self._resolve(workspace, check.target)
        if not path.is_dir():
            return (
                VerificationStatus.FAIL,
                "target_not_a_directory",
                {"kind": check.kind.value, "actual_exists": path.exists()},
                normalized,
            )
        file_count, directory_count, truncated = _bounded_walk(path)
        return (
            VerificationStatus.PASS,
            "entries_counted",
            {
                "kind": check.kind.value,
                "file_count": file_count,
                "directory_count": directory_count,
                "truncated": truncated,
            },
            normalized,
        )

    def _inspect(
        self, workspace: Workspace
    ) -> tuple[VerificationStatus, str, dict[str, object], str | None]:
        root = workspace.verify_target(())
        entries = sorted(os.listdir(root))
        shown = entries[:MAX_LISTING_ENTRIES]
        file_count, directory_count, truncated = _bounded_walk(root)
        return (
            VerificationStatus.PASS,
            "workspace_inspected",
            {
                "kind": OfflineStepKind.INSPECT.value,
                "entries": shown,
                "entry_count": len(entries),
                "file_count": file_count,
                "directory_count": directory_count,
                "truncated": truncated or len(entries) > len(shown),
            },
            "",
        )


def _bounded_walk(root: Any) -> tuple[int, int, bool]:
    """Count files and directories under ``root`` within a hard entry bound.

    Deterministic and bounded: the same tree yields the same numbers, a huge tree
    yields ``truncated=True`` instead of a long walk, and heavy/generated
    directories are skipped by name.
    """

    file_count = 0
    directory_count = 0
    visited = 0
    truncated = False
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            visited += 1
            if visited > MAX_WALK_ENTRIES:
                truncated = True
                return file_count, directory_count, truncated
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in _WALK_IGNORED_DIRS or is_secret_directory(entry.name):
                        continue
                    directory_count += 1
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    file_count += 1
            except OSError:
                continue
    return file_count, directory_count, truncated


def _result(
    verification_id: str,
    criterion_id: str,
    status: VerificationStatus,
    code: str,
    *,
    relative_path: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> VerificationResult:
    details = dict(metadata or {})
    return VerificationResult(
        verification_id=verification_id,
        status=status,
        code=code,
        relative_path=relative_path,
        metadata=details,
        criterion_id=criterion_id,
        evidence=VerificationEvidence(
            evidence_id=f"evidence-{verification_id}",
            verification_id=verification_id,
            source="offline_workspace",
            outcome=status.value,
            metadata={**details, "criterion_id": criterion_id, "code": code},
        ),
    )


def _emit(
    observer: Callable[[EventType, Mapping[str, object]], None],
    run_id: str,
    result: VerificationResult,
    extra: Mapping[str, object],
) -> None:
    """Report one completed check to the loop's own event stream.

    The harness passes its own ``emit`` here, so this event is the same single
    stream the run already records - not a second one. Only bounded, already
    observed facts are written: identifiers, a status, a code, a size, file names
    and the bounded excerpt the step asked for. Credential material is never read,
    so it can never appear here.
    """

    payload: dict[str, object] = {
        "run_id": run_id,
        "verification_id": result.verification_id,
        "criterion_id": result.criterion_id,
        "status": result.status.value,
        "code": result.code,
    }
    if result.relative_path is not None:
        payload["relative_path"] = result.relative_path
    payload.update(dict(result.metadata))
    payload.update({key: value for key, value in extra.items() if value is not None})
    observer(EventType.VERIFICATION_COMPLETED, payload)


# --------------------------------------------------------------------------- #
# decision: drive the existing loop, never a command and never a tool
# --------------------------------------------------------------------------- #


class OfflineTaskDecisionProvider:
    """Deterministic decision provider for the offline task loop.

    The existing :class:`~app.decision.provider.DeterministicDecisionProvider`
    expresses project-development control flow, in which the first action is
    always ``EXECUTE`` of a declared command. The offline task path has no
    declared command by construction - the frozen scope authorizes none - so that
    first action could only ever be denied. This provider expresses the control
    flow this path actually has:

    ``RUN_VERIFICATION`` -> ``COMPLETE_RUN`` on a passing acceptance, otherwise
    ``FAIL_RUN``.

    It is a *recommendation* source like every decision provider: the harness
    still validates the decision and still refuses a completion whose acceptance
    did not pass. It cannot propose ``EXECUTE``, ``INVOKE_TOOL``,
    ``REQUEST_REVISION``, or an approval wait, so no command, tool, or unbounded
    retry is reachable through it.
    """

    def decide(self, request: DecisionRequest) -> Decision:
        conditions = set(request.blocking_conditions or ())
        state = request.current_project_state
        state_status = str(getattr(getattr(state, "status", None), "value", "") or "")
        acceptance_status = request.acceptance_status or str(
            getattr(state, "acceptance_status", None) or ""
        )

        if conditions & {"security_denied", "permission_denied", "policy_denied"}:
            return self._decision(
                request,
                DecisionType.FAIL,
                DecisionAction.FAIL_RUN,
                "security_denied",
            )
        if acceptance_status == "pass":
            return self._decision(
                request,
                DecisionType.COMPLETE,
                DecisionAction.COMPLETE_RUN,
                "acceptance_passed",
            )
        if acceptance_status == "fail" or state_status == "FAILED":
            # A failed check is a failed task: the offline path has no repair
            # authority, so it reports the outcome instead of retrying blindly.
            return self._decision(
                request,
                DecisionType.FAIL,
                DecisionAction.FAIL_RUN,
                "acceptance_failed",
            )
        if "approval_pending" in conditions or "approval_required" in conditions:
            return self._decision(
                request,
                DecisionType.WAIT,
                DecisionAction.WAIT_FOR_APPROVAL,
                "approval_pending",
            )
        return self._decision(
            request,
            DecisionType.VERIFY,
            DecisionAction.RUN_VERIFICATION,
            "offline_observation_required",
        )

    @staticmethod
    def _decision(
        request: DecisionRequest,
        decision_type: DecisionType,
        action: DecisionAction,
        reason_code: str,
    ) -> Decision:
        references: dict[str, Any] = {}
        if request.context_id:
            references["context_id"] = request.context_id
        if request.context_fingerprint:
            references["context_fingerprint"] = request.context_fingerprint
        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            decision_type=decision_type,
            action=action,
            reason_code=reason_code,
            attempt_number=request.attempt_number or 0,
            references=references or None,
        )


# --------------------------------------------------------------------------- #
# reporting: what the run really did, from the loop's own result
# --------------------------------------------------------------------------- #


def _bounded(value: object, limit: int = 200) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def render_offline_task_report(
    plan: OfflineTaskPlan,
    result: object,
    *,
    run_id: str,
    task_id: str,
    project_id: str,
    duration_seconds: float,
) -> str:
    """Describe one finished offline run from what the loop actually produced.

    Every line is derived from the harness result or the derived plan: no value is
    invented, and an observation that failed is printed as a failure. The report is
    bounded so one response cannot grow with the workspace.
    """

    verifications = {
        str(getattr(item, "criterion_id", "") or ""): item
        for item in (getattr(result, "verification_results", ()) or ())
    }
    acceptance = getattr(result, "final_acceptance", None)
    acceptance_status = str(getattr(getattr(acceptance, "status", None), "value", "") or "")
    acceptance_code = str(getattr(acceptance, "code", "") or "")
    final_status = str(
        getattr(getattr(getattr(result, "final_state", None), "status", None), "value", "")
        or ""
    )
    decisions = tuple(getattr(result, "decisions", ()) or ())
    observations = tuple(getattr(result, "observations", ()) or ())
    execution_results = tuple(getattr(result, "execution_results", ()) or ())
    events = tuple(getattr(result, "events", ()) or ())

    passed = sum(
        1
        for item in verifications.values()
        if str(getattr(getattr(item, "status", None), "value", "") or "") == "pass"
    )
    total = len(plan.checks)

    lines: list[str] = []
    lines.append(
        "OFFLINE TASK EXECUTION - deterministic; no provider call, no network, "
        "no credentials, no cost"
    )
    lines.append(f"Task: {_bounded(plan.description or '(empty description)', 300)}")
    lines.append(
        f"Run: {run_id} | task: {task_id} | project: {project_id} | "
        f"provider: {OFFLINE_PROVIDER_NAME} (offline)"
    )
    lines.append("")
    lines.append(f"Plan derived from the task text: {len(plan.steps)} read-only check(s)")
    for index, step in enumerate(plan.steps, start=1):
        target = f" -> {step.target}" if step.target else " -> workspace root"
        lines.append(f"  {index}. [{step.kind.value}] {_bounded(step.title, 160)}{target}")
    lines.append("")
    lines.append("Work performed (read-only, inside this service's workspace):")
    for index, check in enumerate(plan.checks, start=1):
        observed = verifications.get(check.criterion_id)
        target = check.target or "."
        if observed is None:
            lines.append(f"  [{index}/{total}] {check.kind.value} {target} -> NOT RUN")
            continue
        status = str(getattr(getattr(observed, "status", None), "value", "") or "")
        code = str(getattr(observed, "code", "") or "")
        relative = getattr(observed, "relative_path", None)
        shown_target = relative if relative not in (None, "") else target
        lines.append(
            f"  [{index}/{total}] {check.kind.value} {shown_target} -> "
            f"{status.upper()} ({code})"
        )
        metadata = getattr(observed, "metadata", None) or {}
        excerpt = metadata.get("evidence")
        if isinstance(excerpt, (list, tuple)) and excerpt:
            lines.append(f"        first {len(excerpt)} line(s):")
            for line in excerpt:
                lines.append(f"        | {_bounded(line, MAX_EXCERPT_CHARS)}")
        entries = metadata.get("entries")
        if isinstance(entries, (list, tuple)) and entries:
            shown = ", ".join(_bounded(entry, 60) for entry in entries)
            more = ""
            if metadata.get("entry_count") and int(metadata["entry_count"]) > len(entries):
                more = f" (+{int(metadata['entry_count']) - len(entries)} more)"
            lines.append(f"        entries: {shown}{more}")
        if metadata.get("file_count") is not None:
            lines.append(
                "        files: "
                f"{metadata.get('file_count')} | directories: "
                f"{metadata.get('directory_count', 0)}"
                + (" (walk truncated)" if metadata.get("truncated") else "")
            )
        if metadata.get("size_bytes") is not None:
            lines.append(f"        size: {metadata.get('size_bytes')} bytes")
    lines.append("")
    lines.append(
        "Verification: "
        f"{passed}/{total} check(s) passed (read-only workspace observation)"
    )
    if acceptance is None:
        lines.append("Acceptance: not evaluated (no verdict was produced)")
    else:
        lines.append(
            f"Acceptance: {acceptance_status.upper() or 'UNKNOWN'}"
            + (f" ({acceptance_code})" if acceptance_code else "")
        )
    lines.append(
        f"Loop: {len(observations)} observation(s), {len(decisions)} decision(s), "
        f"{len(execution_results)} host execution(s), {len(events)} recorded event(s)"
    )
    lines.append(f"Final state: {final_status or 'UNKNOWN'}")
    lines.append(f"Duration: {duration_seconds:.3f} s (measured)")
    lines.append("")
    lines.append(
        "Limits: deterministic offline observation only. Nothing here calls a model, "
        "starts a process, opens a socket, or writes to the workspace."
    )
    return "\n".join(lines)

