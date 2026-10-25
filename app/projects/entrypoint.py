"""One safe entry point, detected from the artifact manifest and nothing else.

Why the manifest and not the model's words
------------------------------------------

A model answer can *say* "open index.html" while writing nothing, and a plan can
name ``server.py`` that the run never produced. Launching from prose would therefore
turn a sentence into an execution decision. This module reads the **manifest** --
the list of files that really exist with their sizes and digests -- plus, when the
workspace is supplied, the **names of the files that are really at the project
root**, and picks at most one entry point with a fixed priority.

**A project is a program first and a page second.** The order is:

1. an explicitly **declared** entry -- ``forge.json`` at the project root with
   ``"entry": "<path>"``, or the project specification's ``entry``/``entrypoint``
   value passed by the caller -- honoured only when it names a file that exists;
2. ``app.py``, then ``main.py``, then ``server.py`` at the project root
   (``python_script``);
3. a root package directory that holds ``__main__.py`` (``python_module``);
4. ``server.js`` or ``app.js`` at the project root (``node_script``);
5. ``package.json`` at the project root that declares a ``start`` or ``dev``
   script (``node_npm``; only the script *name* is read, only when the workspace
   is supplied);
6. ``index.html`` at the project root, then any other root ``*.html``
   (``static_html``).

Ranking ``index.html`` first was the measured defect this order fixes: a project
that really has a server (``app.py``/``main.py``/``server.py``, or a
``package.json`` start script) *and* a leftover or stub page was launched as a
static page, Forge served the HTML with no backend, and the owner saw an empty
grey window four separate times. Static is now used **only** when no runnable
entry exists, and the card says so («статическая страница, сервер не требуется»).

Two narrow exceptions keep Forge's own scaffolding from pretending to be a
program: the deterministic ``MOCK_DETERMINISTIC`` scaffold module (recognised by
its own banner, bounded read) is not a server entry, and a declared entry is
honoured only when it names a real file.

Anything else is ``None``: a project with no detected entry point has no entry
point, and the caller says so instead of guessing. :func:`resolve_entry` returns
that ``None`` together with the reason word, so a caller never has to invent one.

What it may and may not do
--------------------------

* It **reads the manifest** and, for bounded cases, files **inside the project
  workspace**: ``package.json`` (one script name), ``forge.json`` (one declared
  path), the names of the files at the workspace root, and the first few kilobytes
  of a root ``*.py`` file to recognise Forge's own deterministic scaffold. Every
  read has a strict path, a size cap and a symlink check. It never reads a source
  file's body to "decide" anything else.
* It **never executes anything** and never starts a process. ``command`` is the
  argv a runner may use -- resolved **here, server-side**, from the interpreter this
  deployment really has -- and ``run_command`` is the same thing as the display
  string a person reads. Neither value is ever taken from a request, a model
  answer, or prose.
* A **static page has no argv**: its launch is the product's existing read-only
  preview route, so ``runnable`` is true and ``run_command`` is the fixed sentence
  :data:`STATIC_RUN_COMMAND`. Reporting such a page as unrunnable (while the same
  route answered HTTP 200) was the measured defect this half of the module fixes.
* A runnable kind whose interpreter (or ``npm``) does not exist is still reported,
  with an **empty** ``command`` and a ``reason``: the shape is recognised, and the
  honest answer is that it could not be launched. A caller maps that to
  ``RUN_BLOCKED`` instead of inventing a command.
* ``digest`` is a SHA-256 over the kind, the workspace-relative path and the exact
  argv. It is the value an approval is bound to, so a changed command invalidates
  the approval that named the old one.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.projects.mock_answer import MOCK_ANSWER_BANNER

__all__ = [
    "ENTRY_KIND_NODE",
    "ENTRY_KIND_NODE_NPM",
    "ENTRY_KIND_NODE_SCRIPT",
    "ENTRY_KIND_PYTHON",
    "ENTRY_KIND_PYTHON_MODULE",
    "ENTRY_KIND_PYTHON_SCRIPT",
    "ENTRY_KIND_STATIC_HTML",
    "ENTRY_KINDS",
    "ENTRY_REASON_NO_ENTRY",
    "LANGUAGE_HTML",
    "LANGUAGE_NODE",
    "LANGUAGE_PYTHON",
    "EntryPoint",
    "EntryResolution",
    "MAX_PACKAGE_JSON_BYTES",
    "STATIC_RUN_COMMAND",
    "detect_entry_point",
    "entry_digest",
    "node_executable",
    "npm_command",
    "python_executable",
    "reset_cached_interpreters",
    "resolve_entry",
]


ENTRY_KIND_STATIC_HTML = "static_html"
ENTRY_KIND_PYTHON_SCRIPT = "python_script"
ENTRY_KIND_PYTHON_MODULE = "python_module"
ENTRY_KIND_NODE_SCRIPT = "node_script"
ENTRY_KIND_NODE_NPM = "node_npm"

#: The previous vocabulary, kept so an import of the old names keeps working. The
#: words themselves are the new, narrower ones: ``python`` is a script entry and
#: ``node`` is the ``npm`` entry, which is what those constants always meant.
ENTRY_KIND_PYTHON = ENTRY_KIND_PYTHON_SCRIPT
ENTRY_KIND_NODE = ENTRY_KIND_NODE_NPM

#: Every kind this module may return. All five are launchable by the product: a
#: static page through the read-only preview route, the other four through a process
#: whose argv this module resolved.
ENTRY_KINDS = (
    ENTRY_KIND_STATIC_HTML,
    ENTRY_KIND_PYTHON_SCRIPT,
    ENTRY_KIND_PYTHON_MODULE,
    ENTRY_KIND_NODE_SCRIPT,
    ENTRY_KIND_NODE_NPM,
)

#: The one sentence used when the manifest supports no entry point at all.
ENTRY_REASON_NO_ENTRY = "no entry point was detected in the artifact manifest"

#: The reason a recognised runnable shape carries when its interpreter is missing.
REASON_NODE_MISSING = "node is not installed"
REASON_NPM_MISSING = "npm is not installed"
#: ``node_npm`` runs a declared script only; nothing else is ever an npm command.
REASON_NPM_SCRIPT = "only a declared start or dev script may run"

#: Serving kind: a static file server, or a process that may bind a port.
SERVE_KIND_STATIC = "static"
SERVE_KIND_PROCESS = "process"

#: The one sentence a static page carries instead of a command. A static entry has no
#: argv -- the product's existing read-only preview route serves the file, and that
#: *is* the launch -- so the card prints this sentence where a process kind prints its
#: command. Without it the page was reported as "no run command: nothing to start",
#: while the same preview route answered HTTP 200. It also says, in the owner's words,
#: that a server is not required, so a page never looks like a broken program.
STATIC_RUN_COMMAND = "статическая страница, сервер не требуется"

#: What the project is written in, for the card's «Язык проекта» fact.
LANGUAGE_HTML = "HTML"
LANGUAGE_PYTHON = "Python"
LANGUAGE_NODE = "Node.js"

#: The root-level names in priority order. The Python script wins first because a
#: project that has a server is a program; ``index.html`` is the *last* candidate, so a
#: leftover page can never hide a backend.
_ROOT_HTML = "index.html"
_ROOT_PYTHON = ("app.py", "main.py", "server.py")
_ROOT_NODE = ("server.js", "app.js")
_PACKAGE_JSON = "package.json"
_PACKAGE_MAIN = "__main__.py"

#: The small, optional project manifest a run (or the owner) may write to declare the
#: entry explicitly. ``{"entry": "index.html"}`` is the documented override.
_DECLARED_MANIFEST = "forge.json"

#: A ``package.json`` larger than this is not read: the detection needs one script
#: name, and an unbounded read would make a manifest entry a way to read a payload.
MAX_PACKAGE_JSON_BYTES = 64 * 1024

#: Same cap for the declared-entry manifest: one short string is all that is read.
MAX_DECLARED_MANIFEST_BYTES = 64 * 1024

#: How many root directory entries are looked at when the workspace is supplied. A
#: project root with more entries than this is not a project Forge served a page from;
#: the cap keeps one manifest lookup from walking an unbounded directory.
MAX_ROOT_ENTRIES = 512

#: How much of a root ``*.py`` file is read to recognise Forge's own scaffold banner.
MAX_SCAFFOLD_BYTES = 4096

#: The script names a Node project is startable by, in the order tried.
_NODE_SCRIPTS = (("start", "npm start"), ("dev", "npm run dev"))


# --------------------------------------------------------------------------- #
# interpreters, resolved once, server-side
# --------------------------------------------------------------------------- #


_INTERPRETERS: Dict[str, Optional[str]] = {}


def _repository_root() -> Path:
    """The repository this module belongs to, from its own location."""

    return Path(__file__).resolve().parents[2]


def _existing(path: Path) -> Optional[str]:
    try:
        if path.is_file():
            return str(path)
    except OSError:
        return None
    return None


def python_executable() -> str:
    """The interpreter a project's Python entry point runs with, absolute.

    Resolution order, each an existing file on this machine:

    1. the repository's own virtual environment (``.venv/Scripts/python.exe`` on
       Windows, ``.venv/bin/python`` elsewhere);
    2. the interpreter running this process, with ``pythonw.exe`` mapped to its
       ``python.exe`` sibling -- a windowless interpreter has no usable stdout, and
       a project's output would silently disappear;
    3. ``sys.executable`` as a last resort.

    The answer is cached: it is a fact about the deployment, not about a request,
    and nothing a caller supplies can change it.
    """

    cached = _INTERPRETERS.get("python")
    if cached:
        return cached
    root = _repository_root()
    for candidate in (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    ):
        found = _existing(candidate)
        if found:
            _INTERPRETERS["python"] = found
            return found
    running = str(getattr(sys, "executable", "") or "")
    if running:
        name = os.path.basename(running).lower()
        if name.startswith("pythonw"):
            sibling = os.path.join(os.path.dirname(running), "python.exe")
            found = _existing(Path(sibling))
            if found:
                _INTERPRETERS["python"] = found
                return found
        if os.path.isabs(running):
            _INTERPRETERS["python"] = running
            return running
    _INTERPRETERS["python"] = "python"
    return "python"


def node_executable() -> Optional[str]:
    """``node`` on this machine, absolute, or ``None`` when it is not installed.

    Resolved once and cached, for the same reason as :func:`python_executable`: a
    project cannot make ``node`` appear by naming it.
    """

    if "node" in _INTERPRETERS:
        return _INTERPRETERS["node"]
    found = shutil.which("node")
    resolved: Optional[str] = None
    if found and os.path.isabs(found):
        resolved = found
    _INTERPRETERS["node"] = resolved
    return resolved


def _npm_cli_script(npm_path: str) -> Optional[str]:
    """The ``npm-cli.js`` that a Windows ``npm.cmd`` wrapper would run.

    CreateProcess cannot start a ``.cmd`` batch file without a command
    interpreter, and this repository never uses a shell. The wrapper itself is
    three lines of ``node "%dp0%\\node_modules\\npm\\bin\\npm-cli.js" %*``, so the
    same JavaScript entry is used directly -- no shell, and the argv stays exactly
    ``node <npm-cli.js> run <script>``.
    """

    folder = Path(npm_path).parent
    for candidate in (
        folder / "node_modules" / "npm" / "bin" / "npm-cli.js",
        folder.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
    ):
        found = _existing(candidate)
        if found:
            return found
    return None


def npm_command(script: str) -> Tuple[Optional[Tuple[str, ...]], str]:
    """The argv that runs ``npm run <script>`` without a shell, and any reason.

    Returns ``(command, reason)``. ``command`` is ``None`` when ``npm`` (or the
    ``node`` it needs) is not installed; ``reason`` then says which one is missing.
    ``npm install`` is never part of this: only the declared ``start``/``dev``
    script runs.
    """

    if script not in ("start", "dev"):
        return None, REASON_NPM_SCRIPT
    node = node_executable()
    if node is None:
        return None, REASON_NODE_MISSING
    npm = shutil.which("npm")
    if not npm:
        return None, REASON_NPM_MISSING
    if os.name == "nt" and npm.lower().endswith((".cmd", ".bat")):
        cli = _npm_cli_script(npm)
        if cli is None:
            # A wrapper exists but its JavaScript entry does not: running the batch
            # file would need a shell, and this deployment does not use one.
            return None, REASON_NPM_MISSING
        return (node, cli, "run", script), ""
    return (npm, "run", script), ""


def reset_cached_interpreters() -> None:
    """Forget the resolved interpreters (used after an install, and by tests)."""

    _INTERPRETERS.clear()


def entry_digest(kind: object, path: object, command: Sequence[str] = ()) -> str:
    """SHA-256 over the kind, the workspace-relative path and the exact argv.

    The one canonical form: an approval names this value, so a command that changed
    by one argument produces a different digest and the old approval no longer
    matches. It is derived from the *command*, not from a display string, which is
    why a re-resolved interpreter also invalidates a stale approval.
    """

    payload = json.dumps(
        {
            "command": [str(part) for part in command or ()],
            "kind": str(kind or ""),
            "path": str(path or ""),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class EntryPoint:
    """One detected entry point: what may run, and how it is served.

    ``path`` is **workspace-relative** and uses ``/``. ``command`` is the exact argv
    a runner may use, resolved server-side; it is deliberately *not* part of
    :meth:`to_dict` because it contains an absolute interpreter path, which never
    reaches a payload outside ``diagnostics``. ``run_command`` is the display string
    for the same command. ``serve_kind`` is ``static`` only for a file a browser can
    open without a process.
    """

    kind: str
    path: str
    run_command: str = ""
    serve_kind: str = SERVE_KIND_PROCESS
    command: Tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ENTRY_KINDS:
            raise ValueError("unknown entry point kind")
        if not isinstance(self.path, str) or not self.path or "\\" in self.path:
            raise ValueError("entry point path must be a workspace-relative path")
        if self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError("entry point path must not escape the workspace")
        if isinstance(self.command, list):
            object.__setattr__(self, "command", tuple(self.command))
        if self.command and any(
            not isinstance(part, str) or not part.strip() for part in self.command
        ):
            raise ValueError("entry point command parts must be non-empty strings")
        if self.serve_kind not in (SERVE_KIND_STATIC, SERVE_KIND_PROCESS):
            raise ValueError("unknown serve kind")

    # ------------------------------------------------------------- derived facts
    @property
    def language(self) -> str:
        """What the project is written in, for the card. Derived from the kind."""

        if self.kind == ENTRY_KIND_STATIC_HTML:
            return LANGUAGE_HTML
        if self.kind in (ENTRY_KIND_PYTHON_SCRIPT, ENTRY_KIND_PYTHON_MODULE):
            return LANGUAGE_PYTHON
        return LANGUAGE_NODE

    @property
    def runnable(self) -> bool:
        """True when the product can really launch this entry point.

        A static page is launched by the existing read-only preview route, so it is
        runnable with no command at all; a process kind is runnable only when its
        argv was resolved server-side (a recognised shape whose interpreter is
        missing is *not* runnable, and the card must say so).
        """

        if self.serve_kind == SERVE_KIND_STATIC:
            return True
        return bool(self.command)

    @property
    def digest(self) -> str:
        """SHA-256 over kind, path and exact argv: the value an approval binds to."""

        return entry_digest(self.kind, self.path, self.command)

    def to_dict(self) -> Dict[str, Any]:
        """The public shape. The absolute interpreter path is never in it."""

        return {
            "kind": self.kind,
            "path": self.path,
            "run_command": self.run_command,
            "serve_kind": self.serve_kind,
            "language": self.language,
            "runnable": self.runnable,
            "digest": self.digest,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EntryResolution:
    """The one entry point a manifest supports, or the reason there is none."""

    entry: Optional[EntryPoint] = None
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.entry is not None

    @property
    def blocked(self) -> bool:
        """True when a runnable shape was recognised but cannot be launched."""

        return self.entry is not None and bool(self.entry.reason)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entrypoint": self.entry.to_dict() if self.entry is not None else None,
            "reason": self.reason if self.entry is None else self.entry.reason,
            "blocked": self.blocked,
        }


def _path_of(item: object) -> str:
    if isinstance(item, Mapping):
        return str(item.get("path", "") or "")
    return str(getattr(item, "path", "") or "")


def _paths(manifest: Sequence[object]) -> Tuple[str, ...]:
    """The manifest's paths, normalized to forward slashes, in manifest order."""

    found = []
    for item in manifest or ():
        path = _path_of(item).replace("\\", "/").strip()
        if path:
            found.append(path)
    return tuple(found)


def _root_html(paths: Sequence[str]) -> Optional[str]:
    candidates = [path for path in paths if "/" not in path and path.lower().endswith(".html")]
    if not candidates:
        return None
    # Deterministic: a name is chosen, not the first hash-ordered directory entry.
    return sorted(candidates)[0]


def _root_package(paths: Sequence[str]) -> Optional[str]:
    """The one root package directory that declares ``__main__.py``, if any.

    A *root* package is a manifest entry ``<package>/__main__.py`` whose first
    component is a single identifier-shaped name and whose second is ``__main__.py``
    with nothing after it. The name is chosen deterministically when several exist.
    """

    found = set()
    for path in paths:
        parts = path.split("/")
        if len(parts) != 2 or parts[1] != _PACKAGE_MAIN:
            continue
        name = parts[0]
        if not name or not name[0].isalpha() and name[0] != "_":
            continue
        if not all(character.isalnum() or character == "_" for character in name):
            continue
        found.add(name)
    if not found:
        return None
    return sorted(found)[0]


# --------------------------------------------------------------------------- #
# bounded reads inside the project workspace
# --------------------------------------------------------------------------- #


def _workspace_root(workspace: object) -> Optional[Path]:
    """The workspace as a path, or ``None`` when it is absent or unusable."""

    if workspace is None:
        return None
    try:
        # ``WindowsPath.root`` is ``"\\"``, so a path object must never be read via
        # ``getattr(..., "root", ...)``.
        if isinstance(workspace, (str, os.PathLike)):
            return Path(workspace)
        return Path(getattr(workspace, "root", workspace))
    except (TypeError, ValueError):
        return None


def _json_document(workspace: object, name: str, cap: int) -> Optional[Mapping]:
    """One small, bounded JSON object from the workspace root, or ``None``."""

    root = _workspace_root(workspace)
    if root is None:
        return None
    target = root / name
    try:
        if target.is_symlink() or not target.is_file():
            return None
        if target.stat().st_size > cap:
            return None
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return document if isinstance(document, Mapping) else None


def _root_file_names(workspace: object) -> Tuple[str, ...]:
    """The names of the regular files at the workspace root, sorted and bounded.

    This is what keeps the entry point from going stale. A version's manifest lists
    the files *that run* wrote, so after a run that wrote only ``index.html`` a
    project's still-present ``app.py`` is missing from the new manifest -- while the
    file is still on disk and the project is still a program. Only file **names** are
    read: no body, no recursion, no symlink follows, and the listing is capped.
    """

    root = _workspace_root(workspace)
    if root is None:
        return ()
    names: List[str] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if len(names) >= MAX_ROOT_ENTRIES:
                    break
                try:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                names.append(entry.name)
    except OSError:
        return ()
    return tuple(sorted(names))


def _is_forge_scaffold(workspace: object, name: str) -> bool:
    """Whether this root file is Forge's own deterministic mock scaffold module.

    ``MOCK_DETERMINISTIC`` writes a page *and* an importable module, and that module
    is deliberately side-effect free ("no side effect on import"). It must not be
    treated as the project's server: every offline scaffold would start a process
    that exits at once, and the owner would lose the page that does work. The banner
    is Forge's own literal read from at most the first few kilobytes of a root
    ``*.py`` file, so this can never misfire on the owner's code.
    """

    if not name.lower().endswith(".py"):
        return False
    root = _workspace_root(workspace)
    if root is None:
        return False
    target = root / name
    try:
        if target.is_symlink() or not target.is_file():
            return False
        if target.stat().st_size > MAX_SCAFFOLD_BYTES:
            return False
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return MOCK_ANSWER_BANNER in text


def _safe_relative(value: object) -> str:
    """A declared entry, normalized, or ``""`` when it could escape the workspace."""

    if not isinstance(value, str):
        return ""
    text = value.strip().replace("\\", "/")
    first = text.split("/")[0]
    if not text or text.startswith("/") or ":" in first:
        return ""
    parts = [part for part in text.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _declared_entry_name(workspace: object) -> str:
    """The entry ``forge.json`` declares, or ``""``.

    The override is a *declaration*, not a preference: only a name the ranking below
    can also recognise is honoured (:func:`_entry_for_name`), so a manifest cannot
    name a file the project does not have.
    """

    document = _json_document(
        workspace, _DECLARED_MANIFEST, MAX_DECLARED_MANIFEST_BYTES
    )
    if document is None:
        return ""
    for key in ("entry", "entrypoint"):
        value = _safe_relative(document.get(key))
        if value:
            return value
    return ""


def _node_script_name(workspace: object) -> str:
    """The start command of ``package.json``, from its ``scripts`` names only.

    Returns ``""`` when the file is absent from the workspace, unreadable, larger
    than the cap, not valid JSON, or declares neither a ``start`` nor a ``dev``
    script. Only the presence of a key is used: no value, dependency or body reaches
    the caller, so this read cannot leak content into an event or a payload.
    """

    document = _json_document(workspace, _PACKAGE_JSON, MAX_PACKAGE_JSON_BYTES)
    if document is None:
        return ""
    scripts = document.get("scripts")
    if not isinstance(scripts, Mapping):
        return ""
    for name, _display in _NODE_SCRIPTS:
        value = scripts.get(name)
        if isinstance(value, str) and value.strip():
            return name
    return ""


# --------------------------------------------------------------------------- #
# the entry points, built in exactly one place each
# --------------------------------------------------------------------------- #


def _static(path: str) -> EntryPoint:
    """A page the preview route serves read-only: runnable, with no argv at all."""

    return EntryPoint(
        kind=ENTRY_KIND_STATIC_HTML,
        path=path,
        run_command=STATIC_RUN_COMMAND,
        serve_kind=SERVE_KIND_STATIC,
    )


def _python_script(name: str) -> EntryPoint:
    python = python_executable()
    return EntryPoint(
        kind=ENTRY_KIND_PYTHON_SCRIPT,
        path=name,
        run_command=f"python {name}",
        serve_kind=SERVE_KIND_PROCESS,
        command=(python, name),
    )


def _python_module(package: str) -> EntryPoint:
    python = python_executable()
    return EntryPoint(
        kind=ENTRY_KIND_PYTHON_MODULE,
        path=package,
        run_command=f"python -m {package}",
        serve_kind=SERVE_KIND_PROCESS,
        command=(python, "-m", package),
    )


def _node_script(name: str) -> EntryPoint:
    node = node_executable()
    if node is None:
        return EntryPoint(
            kind=ENTRY_KIND_NODE_SCRIPT,
            path=name,
            run_command=f"node {name}",
            serve_kind=SERVE_KIND_PROCESS,
            command=(),
            reason=REASON_NODE_MISSING,
        )
    return EntryPoint(
        kind=ENTRY_KIND_NODE_SCRIPT,
        path=name,
        run_command=f"node {name}",
        serve_kind=SERVE_KIND_PROCESS,
        command=(node, name),
    )


def _node_npm_entry(workspace: object) -> Optional[EntryPoint]:
    script = _node_script_name(workspace)
    if not script:
        return None
    display = dict((name, text) for name, text in _NODE_SCRIPTS)[script]
    command, reason = npm_command(script)
    return EntryPoint(
        kind=ENTRY_KIND_NODE_NPM,
        path=_PACKAGE_JSON,
        run_command=display,
        serve_kind=SERVE_KIND_PROCESS,
        command=() if command is None else command,
        reason=reason,
    )


def _paths_with_workspace(
    manifest: Sequence[object], workspace: object
) -> Tuple[str, ...]:
    """The manifest's paths plus the root files that really exist right now.

    The union keeps the entry point honest after a run that wrote only part of the
    project: the manifest is what the run recorded, the workspace is what the project
    has. When the workspace is unavailable the manifest is all there is.
    """

    paths = _paths(manifest)
    names = _root_file_names(workspace)
    if not names:
        return paths
    seen = set(paths)
    merged = list(paths)
    for name in names:
        if name not in seen:
            seen.add(name)
            merged.append(name)
    return tuple(merged)


def _entry_for_name(
    name: str, paths: Sequence[str], workspace: object
) -> Optional[EntryPoint]:
    """The recognised entry point for one *declared* name, or ``None``."""

    if name not in paths:
        return None
    if "/" not in name:
        if name in _ROOT_PYTHON:
            if _is_forge_scaffold(workspace, name):
                return None
            return _python_script(name)
        if name in _ROOT_NODE:
            return _node_script(name)
        if name == _PACKAGE_JSON:
            return _node_npm_entry(workspace)
        if name.lower().endswith(".html"):
            return _static(name)
        return None
    parts = name.split("/")
    if (
        len(parts) == 2
        and parts[1] == _PACKAGE_MAIN
        and _root_package((name,)) == parts[0]
    ):
        return _python_module(parts[0])
    return None


def resolve_entry(
    manifest: Sequence[object] = (),
    *,
    workspace: Optional[object] = None,
    declared_entry: object = None,
) -> EntryResolution:
    """The one safe entry point this project supports, with its reason word.

    ``manifest`` is the current version's artifact list
    (:class:`app.projects.models.ArtifactRef` records or their ``to_dict()`` shape).
    ``workspace`` is optional: when supplied, the names of the files really at the
    project root are added to the candidates (so a manifest that lists only the last
    run's file cannot hide the server that is still there), and ``package.json`` and
    ``forge.json`` are read with bounded, symlink-checked reads. ``declared_entry`` is
    the project specification's explicit ``entry`` value; a project's own
    ``forge.json`` is read as the second declaration.

    **The order is a program first and a page second:** declared entry, ``app.py`` ->
    ``main.py`` -> ``server.py``, a root ``__main__.py`` package, ``server.js``/
    ``app.js``, ``package.json`` with ``start``/``dev``, and only then ``index.html``
    and any other root ``*.html``. Static is the fallback used when no runnable entry
    exists at all.
    """

    paths = _paths_with_workspace(manifest, workspace)
    if not paths:
        return EntryResolution(reason=ENTRY_REASON_NO_ENTRY)

    # 1. An explicit declaration wins, the caller's (the project specification) first
    #    and the project's own forge.json second, but only for a name the ranking can
    #    recognise. A declaration that names nothing real is ignored, not obeyed.
    for declared in (declared_entry, _declared_entry_name(workspace)):
        name = _safe_relative(declared)
        if not name:
            continue
        entry = _entry_for_name(name, paths, workspace)
        if entry is not None:
            return EntryResolution(entry=entry)

    # 2. Python scripts: a project that has a server is a program, not a page.
    for name in _ROOT_PYTHON:
        if name in paths and not _is_forge_scaffold(workspace, name):
            return EntryResolution(entry=_python_script(name))

    # 3. A root package with ``__main__.py``.
    package = _root_package(paths)
    if package is not None:
        return EntryResolution(entry=_python_module(package))

    # 4. A Node script.
    for name in _ROOT_NODE:
        if name in paths:
            return EntryResolution(entry=_node_script(name))

    # 5. A Node project whose package.json declares a start/dev script.
    if _PACKAGE_JSON in paths:
        npm = _node_npm_entry(workspace)
        if npm is not None:
            return EntryResolution(entry=npm)

    # 6. A static page: the fallback, and only when nothing runnable exists.
    if _ROOT_HTML in paths:
        return EntryResolution(entry=_static(_ROOT_HTML))
    page = _root_html(paths)
    if page is not None:
        return EntryResolution(entry=_static(page))
    return EntryResolution(reason=ENTRY_REASON_NO_ENTRY)


def detect_entry_point(
    manifest: Sequence[object] = (),
    *,
    workspace: Optional[object] = None,
    declared_entry: object = None,
) -> Optional[EntryPoint]:
    """The one safe entry point this project supports, or ``None``.

    The unchanged contract every existing caller reads: the entry, or ``None``.
    :func:`resolve_entry` is the same detection with the reason word.
    """

    return resolve_entry(
        manifest, workspace=workspace, declared_entry=declared_entry
    ).entry
