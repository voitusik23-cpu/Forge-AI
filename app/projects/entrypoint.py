"""One safe entry point, detected from the artifact manifest and nothing else.

Why the manifest and not the model's words
------------------------------------------

A model answer can *say* "open index.html" while writing nothing, and a plan can
name ``server.py`` that the run never produced. Launching from prose would therefore
turn a sentence into an execution decision. This module reads the **manifest** --
the list of files that really exist with their sizes and digests -- and picks at
most one entry point from it, with a fixed priority:

1. ``index.html`` at the project root;
2. ``app.py``, then ``main.py``, then ``server.py`` at the project root
   (``python_script``);
3. a root package directory that holds ``__main__.py`` (``python_module``);
4. ``server.js`` or ``app.js`` at the project root (``node_script``);
5. ``package.json`` at the project root that declares a ``start`` or ``dev``
   script (``node_npm``; only the script *name* is read, only when the workspace
   is supplied);
6. any other ``*.html`` at the project root.

Anything else is ``None``: a project with no detected entry point has no entry
point, and the caller says so instead of guessing. :func:`resolve_entry` returns
that ``None`` together with the reason word, so a caller never has to invent one.

What it may and may not do
--------------------------

* It **reads the manifest** and, for one bounded case (``package.json``), one file
  inside the workspace, with a strict path, a size cap and a symlink check. It never
  reads a source file's contents to "decide" anything else.
* It **never executes anything** and never starts a process. ``command`` is the
  argv a runner may use -- resolved **here, server-side**, from the interpreter this
  deployment really has -- and ``run_command`` is the same thing as the display
  string a person reads. Neither value is ever taken from a request, a model
  answer, or prose.
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
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

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

#: Every kind this module may return. ``static_html`` is the only one a browser can
#: open with no command at all; the other four are runnable kinds.
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

#: What the project is written in, for the card's «Язык проекта» fact.
LANGUAGE_HTML = "HTML"
LANGUAGE_PYTHON = "Python"
LANGUAGE_NODE = "Node.js"

#: The root-level names in priority order. ``index.html`` is first because it is the
#: one file a browser can open with no command at all.
_ROOT_HTML = "index.html"
_ROOT_PYTHON = ("app.py", "main.py", "server.py")
_ROOT_NODE = ("server.js", "app.js")
_PACKAGE_JSON = "package.json"
_PACKAGE_MAIN = "__main__.py"

#: A ``package.json`` larger than this is not read: the detection needs one script
#: name, and an unbounded read would make a manifest entry a way to read a payload.
MAX_PACKAGE_JSON_BYTES = 64 * 1024

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
        """True when this entry point has a command that could be started."""

        return self.serve_kind == SERVE_KIND_PROCESS and bool(self.command)

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


def _node_script_name(workspace: object) -> str:
    """The start command of ``package.json``, from its ``scripts`` names only.

    Returns ``""`` when the file is absent from the workspace, unreadable, larger
    than the cap, not valid JSON, or declares neither a ``start`` nor a ``dev``
    script. Only the presence of a key is used: no value, dependency or body reaches
    the caller, so this read cannot leak content into an event or a payload.
    """

    if workspace is None:
        return ""
    try:
        # ``WindowsPath.root`` is ``"\\"``, so a path object must never be read via
        # ``getattr(..., "root", ...)``.
        if isinstance(workspace, (str, os.PathLike)):
            root = Path(workspace)
        else:
            root = Path(getattr(workspace, "root", workspace))
    except (TypeError, ValueError):
        return ""
    target = root / _PACKAGE_JSON
    try:
        if target.is_symlink() or not target.is_file():
            return ""
        if target.stat().st_size > MAX_PACKAGE_JSON_BYTES:
            return ""
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return ""
    scripts = document.get("scripts") if isinstance(document, Mapping) else None
    if not isinstance(scripts, Mapping):
        return ""
    for name, _display in _NODE_SCRIPTS:
        value = scripts.get(name)
        if isinstance(value, str) and value.strip():
            return name
    return ""


def _static(path: str) -> EntryPoint:
    return EntryPoint(
        kind=ENTRY_KIND_STATIC_HTML,
        path=path,
        run_command="",
        serve_kind=SERVE_KIND_STATIC,
    )


def resolve_entry(
    manifest: Sequence[object] = (),
    *,
    workspace: Optional[object] = None,
) -> EntryResolution:
    """The one safe entry point this manifest supports, with its reason word.

    ``manifest`` is the current version's artifact list
    (:class:`app.projects.models.ArtifactRef` records or their ``to_dict()`` shape).
    ``workspace`` is optional and is used **only** to read a ``package.json`` that is
    already in the manifest; passing ``None`` simply means a Node project cannot be
    detected, never that a path is guessed.
    """

    paths = _paths(manifest)
    if not paths:
        return EntryResolution(reason=ENTRY_REASON_NO_ENTRY)

    if _ROOT_HTML in paths:
        return EntryResolution(entry=_static(_ROOT_HTML))

    for name in _ROOT_PYTHON:
        if name in paths:
            python = python_executable()
            return EntryResolution(
                entry=EntryPoint(
                    kind=ENTRY_KIND_PYTHON_SCRIPT,
                    path=name,
                    run_command=f"python {name}",
                    serve_kind=SERVE_KIND_PROCESS,
                    command=(python, name),
                )
            )

    package = _root_package(paths)
    if package is not None:
        python = python_executable()
        return EntryResolution(
            entry=EntryPoint(
                kind=ENTRY_KIND_PYTHON_MODULE,
                path=package,
                run_command=f"python -m {package}",
                serve_kind=SERVE_KIND_PROCESS,
                command=(python, "-m", package),
            )
        )

    for name in _ROOT_NODE:
        if name in paths:
            node = node_executable()
            if node is None:
                return EntryResolution(
                    entry=EntryPoint(
                        kind=ENTRY_KIND_NODE_SCRIPT,
                        path=name,
                        run_command=f"node {name}",
                        serve_kind=SERVE_KIND_PROCESS,
                        command=(),
                        reason=REASON_NODE_MISSING,
                    )
                )
            return EntryResolution(
                entry=EntryPoint(
                    kind=ENTRY_KIND_NODE_SCRIPT,
                    path=name,
                    run_command=f"node {name}",
                    serve_kind=SERVE_KIND_PROCESS,
                    command=(node, name),
                )
            )

    if _PACKAGE_JSON in paths:
        script = _node_script_name(workspace)
        if script:
            display = dict((name, text) for name, text in _NODE_SCRIPTS)[script]
            command, reason = npm_command(script)
            return EntryResolution(
                entry=EntryPoint(
                    kind=ENTRY_KIND_NODE_NPM,
                    path=_PACKAGE_JSON,
                    run_command=display,
                    serve_kind=SERVE_KIND_PROCESS,
                    command=() if command is None else command,
                    reason=reason,
                )
            )

    page = _root_html(paths)
    if page is not None:
        return EntryResolution(entry=_static(page))
    return EntryResolution(reason=ENTRY_REASON_NO_ENTRY)


def detect_entry_point(
    manifest: Sequence[object] = (),
    *,
    workspace: Optional[object] = None,
) -> Optional[EntryPoint]:
    """The one safe entry point this manifest supports, or ``None``.

    The unchanged contract every existing caller reads: the entry, or ``None``.
    :func:`resolve_entry` is the same detection with the reason word.
    """

    return resolve_entry(manifest, workspace=workspace).entry
