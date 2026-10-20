"""One safe entry point, detected from the artifact manifest and nothing else.

Why the manifest and not the model's words
------------------------------------------

A model answer can *say* "open index.html" while writing nothing, and a plan can
name ``server.py`` that the run never produced. Launching from prose would therefore
turn a sentence into an execution decision. This module reads the **manifest** --
the list of files that really exist with their sizes and digests -- and picks at
most one entry point from it, with a fixed priority:

1. ``index.html`` at the project root;
2. ``app.py``, then ``main.py``, then ``server.py`` at the project root;
3. ``package.json`` at the project root that declares a ``start`` or ``dev``
   script (only the script *name* is read, only when the workspace is supplied);
4. any other ``*.html`` at the project root.

Anything else is ``None``: a project with no detected entry point has no entry
point, and the caller says so instead of guessing.

What it may and may not do
--------------------------

* It **reads the manifest** and, for one bounded case (``package.json``), one file
  inside the workspace, with a strict path, a size cap and a symlink check. It never
  reads a source file's contents to "decide" anything else.
* It **never executes anything** and never starts a process. ``run_command`` is the
  command a person would run -- a display value that this repository never passes to
  a shell -- and ``serve_kind`` says whether serving that kind is wired at all.
* A ``python`` or ``node`` entry point is reported with ``serve_kind="process"``;
  the preview layer refuses those, because spawning a project's own script is not a
  preview and is not wired.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

__all__ = [
    "ENTRY_KIND_NODE",
    "ENTRY_KIND_PYTHON",
    "ENTRY_KIND_STATIC_HTML",
    "EntryPoint",
    "MAX_PACKAGE_JSON_BYTES",
    "detect_entry_point",
]


ENTRY_KIND_STATIC_HTML = "static_html"
ENTRY_KIND_PYTHON = "python"
ENTRY_KIND_NODE = "node"

#: Serving kind: a static file server, or a process this repository does not start.
SERVE_KIND_STATIC = "static"
SERVE_KIND_PROCESS = "process"

#: The root-level names in priority order. ``index.html`` is first because it is the
#: one file a browser can open with no command at all.
_ROOT_HTML = "index.html"
_ROOT_PYTHON = ("app.py", "main.py", "server.py")
_PACKAGE_JSON = "package.json"

#: A ``package.json`` larger than this is not read: the detection needs one script
#: name, and an unbounded read would make a manifest entry a way to read a payload.
MAX_PACKAGE_JSON_BYTES = 64 * 1024

#: The script names a Node project is startable by, in the order tried.
_NODE_SCRIPTS = (("start", "npm start"), ("dev", "npm run dev"))


@dataclass(frozen=True)
class EntryPoint:
    """One detected entry point: what to open or run, and how it is served.

    ``path`` is **workspace-relative** and uses ``/``. ``run_command`` is a display
    string (never executed by this repository). ``serve_kind`` is ``static`` only for
    a file a browser can open without a process; everything else is ``process`` and
    the preview layer reports that local run is not wired.
    """

    kind: str
    path: str
    run_command: str = ""
    serve_kind: str = SERVE_KIND_PROCESS

    def __post_init__(self) -> None:
        if self.kind not in (
            ENTRY_KIND_STATIC_HTML,
            ENTRY_KIND_PYTHON,
            ENTRY_KIND_NODE,
        ):
            raise ValueError("unknown entry point kind")
        if not isinstance(self.path, str) or not self.path or "\\" in self.path:
            raise ValueError("entry point path must be a workspace-relative path")
        if self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError("entry point path must not escape the workspace")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "run_command": self.run_command,
            "serve_kind": self.serve_kind,
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


def _node_script(workspace: object) -> str:
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
    for name, command in _NODE_SCRIPTS:
        value = scripts.get(name)
        if isinstance(value, str) and value.strip():
            return command
    return ""


def detect_entry_point(
    manifest: Sequence[object] = (),
    *,
    workspace: Optional[object] = None,
) -> Optional[EntryPoint]:
    """The one safe entry point this manifest supports, or ``None``.

    ``manifest`` is the current version's artifact list
    (:class:`app.projects.models.ArtifactRef` records or their ``to_dict()`` shape).
    ``workspace`` is optional and is used **only** to read a ``package.json`` that is
    already in the manifest; passing ``None`` simply means a Node project cannot be
    detected, never that a path is guessed.
    """

    paths = _paths(manifest)
    if not paths:
        return None

    if _ROOT_HTML in paths:
        return EntryPoint(
            kind=ENTRY_KIND_STATIC_HTML,
            path=_ROOT_HTML,
            run_command="",
            serve_kind=SERVE_KIND_STATIC,
        )

    for name in _ROOT_PYTHON:
        if name in paths:
            return EntryPoint(
                kind=ENTRY_KIND_PYTHON,
                path=name,
                run_command=f"python {name}",
                serve_kind=SERVE_KIND_PROCESS,
            )

    if _PACKAGE_JSON in paths:
        command = _node_script(workspace)
        if command:
            return EntryPoint(
                kind=ENTRY_KIND_NODE,
                path=_PACKAGE_JSON,
                run_command=command,
                serve_kind=SERVE_KIND_PROCESS,
            )

    page = _root_html(paths)
    if page is not None:
        return EntryPoint(
            kind=ENTRY_KIND_STATIC_HTML,
            path=page,
            run_command="",
            serve_kind=SERVE_KIND_STATIC,
        )
    return None
