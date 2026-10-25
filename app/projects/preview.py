"""A safe, read-only local preview for a project's static files.

What this is
------------

A tiny HTTP server that serves **one** directory -- the project's own workspace --
on ``127.0.0.1`` with an *ephemeral* port, for as long as the owner asks for it.
It uses the same proven mechanics as ``tools/ui_preview_server.py``
(:class:`socketserver.ThreadingTCPServer` with daemon threads and a reusable
address), narrowed to one job: hand a browser the files a run already wrote.

What it deliberately is not
---------------------------

* **No execution.** No command is started, no script is run, no child process is
  spawned. A ``python`` or ``node`` entry point is refused with the sentence the
  interface prints: local run is not wired for that kind.
* **No traversal.** A request path is decoded strictly, split, and refused if any
  component is empty, ``.``, ``..``, starts with a dot, is a credential-shaped name,
  contains a backslash or a NUL, or is not in the allow-listed charset. The
  resolved target must still sit inside the project workspace, and a symlink is
  refused rather than followed.
* **No listing and no write.** Only ``GET``/``HEAD`` exist; there is no directory
  index (a directory answers 404), no upload, no method that mutates anything. The
  response says ``Cache-Control: no-store`` and the content type comes from a
  closed allow-list, so an unknown suffix is a download rather than a rendered page.
* **No absolute path in the main payload.** The status this server is reported
  through carries a loopback URL and a workspace-relative entry path; the absolute
  root appears only inside a ``diagnostics`` block.

Only the entry kind ``static`` is served. That is a fact about the entry point
(:mod:`app.projects.entrypoint`), not something this module re-decides.
"""

from __future__ import annotations

import http.server
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
import socketserver
import threading
import urllib.parse
from typing import Any, Dict, Optional

from app.execution.secret_files import is_secret_directory, secret_reason
from app.projects.entrypoint import SERVE_KIND_STATIC, EntryPoint

__all__ = [
    "PREVIEW_HOST",
    "PREVIEW_NEEDS_PROCESS",
    "PREVIEW_OWNED_BY_PROCESS",
    "LOCAL_RUN_NOT_WIRED",
    "NO_ENTRY_POINT",
    "PreviewRegistry",
    "PreviewSession",
]

#: The exact sentence the interface prints when an entry point exists but the
#: project's own process is not running: there is nothing to point a URL at, and
#: inventing one would be a claim the browser would immediately disprove.
PREVIEW_NEEDS_PROCESS = (
    "the project process is not running, so there is no preview URL"
)

#: The previous name of the sentence above. The word changed when local run became
#: wired (:mod:`app.projects.runner`): for a process entry the honest answer is no
#: longer "not wired", it is "nothing is running yet". The constant is kept so an
#: importer reads the same name, and it holds the current sentence.
LOCAL_RUN_NOT_WIRED = PREVIEW_NEEDS_PROCESS

#: The reason a preview cannot start when the manifest has no entry point.
NO_ENTRY_POINT = "no entry point was detected in the artifact manifest"

#: The preview route's answer when asked to *stop* a preview that belongs to the
#: project's own process: the preview cannot be closed without stopping the process.
PREVIEW_OWNED_BY_PROCESS = (
    "this preview is served by the project's own process: stop the process instead"
)

#: Loopback only. There is no host parameter anywhere in this module.
PREVIEW_HOST = "127.0.0.1"

#: The largest file the preview will hand out. A generated project is far smaller;
#: a larger file is refused rather than streamed.
MAX_PREVIEW_FILE_BYTES = 8 * 1024 * 1024

#: A closed content-type table. A suffix that is not here is served as a download,
#: so an unknown file can never be rendered as a document by the browser.
_CONTENT_TYPES: Dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".wasm": "application/wasm",
    ".map": "application/json; charset=utf-8",
}

#: The charset of a request path this server will even look at.
_PATH_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-"
)


def _content_type(target: Path) -> str:
    suffix = target.suffix.casefold()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(target.name)
    # Anything not in the closed table is a download, never a rendered document.
    return guessed if guessed else "application/octet-stream"


@dataclass(frozen=True)
class PreviewSession:
    """One running preview: loopback URL, port, and the entry it serves."""

    project_id: str
    workspace_root: str
    port: int
    url: str
    entry_path: str

    def to_dict(self) -> Dict[str, Any]:
        """The public shape. The absolute root is inside ``diagnostics`` only."""

        return {
            "available": True,
            "running": True,
            "url": self.url,
            "port": int(self.port),
            "entry_path": self.entry_path,
            "diagnostics": {
                "workspace_root": self.workspace_root,
                "read_only": True,
                "root_scoped": True,
                "loopback": True,
                "os_level_sandbox": False,
            },
        }


class _PreviewHandler(http.server.BaseHTTPRequestHandler):
    """Serve one file from the project workspace; refuse everything else."""

    server_version = "ForgePreview/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # noqa: D401 - keep the console quiet
        return

    # --------------------------------------------------------------- methods
    def do_GET(self) -> None:  # noqa: N802 - the base class names it this way
        self._serve(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802 - the base class names it this way
        self._serve(head_only=True)

    def _method_not_allowed(self) -> None:
        body = b'{"error": "this preview is read-only"}'
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _method_not_allowed  # noqa: N815 - mirrors the HTTP method
    do_PUT = _method_not_allowed  # noqa: N815
    do_PATCH = _method_not_allowed  # noqa: N815
    do_DELETE = _method_not_allowed  # noqa: N815
    do_OPTIONS = _method_not_allowed  # noqa: N815

    # ---------------------------------------------------------------- serving
    def _refuse(self, status: int, reason: str) -> None:
        body = ('{"error": "' + reason + '"}').encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _relative_path(self) -> Optional[str]:
        """The requested workspace-relative path, or ``None`` when it is refused."""

        raw = str(self.path or "").split("?", 1)[0].split("#", 1)[0]
        try:
            decoded = urllib.parse.unquote(raw, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return None
        if "\x00" in decoded or "\\" in decoded:
            return None
        if not decoded.startswith("/"):
            return None
        relative = decoded[1:]
        if relative == "":
            relative = str(getattr(self.server, "entry_path", "index.html") or "index.html")
        if any(character not in _PATH_CHARS for character in relative):
            return None
        parts = relative.split("/")
        for part in parts:
            if part in ("", ".", "..") or part.startswith("."):
                return None
            if is_secret_directory(part) or secret_reason(part) is not None:
                return None
        return "/".join(parts)

    def _serve(self, *, head_only: bool) -> None:
        relative = self._relative_path()
        if relative is None:
            self._refuse(403, "the requested path is not allowed")
            return

        root = Path(str(getattr(self.server, "workspace_root", "")))
        try:
            canonical_root = Path(os.path.realpath(str(root)))
        except (OSError, RuntimeError):
            self._refuse(404, "the project workspace is unavailable")
            return
        if canonical_root != root:
            # The directory was replaced (or became a link) while serving.
            self._refuse(404, "the project workspace changed")
            return

        target = canonical_root.joinpath(*relative.split("/"))
        try:
            resolved = Path(os.path.realpath(str(target)))
        except (OSError, RuntimeError):
            self._refuse(404, "no such file in the project workspace")
            return
        if resolved != canonical_root and canonical_root not in resolved.parents:
            self._refuse(403, "the requested path escapes the project workspace")
            return
        if target.is_symlink() or not target.is_file():
            self._refuse(404, "no such file in the project workspace")
            return
        try:
            size = target.stat().st_size
        except OSError:
            self._refuse(404, "no such file in the project workspace")
            return
        if size > MAX_PREVIEW_FILE_BYTES:
            self._refuse(413, "the file is too large for a local preview")
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._refuse(404, "no such file in the project workspace")
            return

        self.send_response(200)
        self.send_header("Content-Type", _content_type(target))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)


class _PreviewServer(socketserver.ThreadingTCPServer):
    """One connection per thread so a slow client cannot freeze the preview."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: object) -> None:
        super().__init__(address, handler)
        self.workspace_root = ""
        self.entry_path = "index.html"


class PreviewRegistry:
    """Start, stop and report the preview servers of one process.

    One registry is created at composition and shared by the routes, so a status
    request and a detail payload can never disagree about whether a preview is
    running. Servers bind an ephemeral port, so two projects can be previewed at the
    same time and a restart never collides with a port left in ``TIME_WAIT``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._servers: Dict[str, Any] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._sessions: Dict[str, PreviewSession] = {}
        #: Previews that are not served by this module at all: the project's own
        #: process bound a localhost port, and this is the URL a browser can use.
        self._processes: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ state
    def is_running(self, project_id: object) -> bool:
        key = str(project_id or "")
        with self._lock:
            return key in self._servers or key in self._processes

    def process_session(self, project_id: object) -> Optional[Dict[str, Any]]:
        """The registered process preview of one project, or ``None``.

        Registered by :mod:`app.projects.runner` when the child actually bound a
        port. Nothing else may register one, and nothing here starts a process.
        """

        key = str(project_id or "")
        with self._lock:
            session = self._processes.get(key)
            return dict(session) if session is not None else None

    def register_process(
        self,
        project_id: object,
        *,
        port: object,
        entry_path: str = "",
        entry_kind: str = "",
    ) -> Dict[str, Any]:
        """Publish the loopback URL of a project process that is listening.

        The port is the one the child **was observed listening on**, never a guess:
        the caller scanned the child's own sockets. A port outside the valid range
        is refused rather than turned into a URL.
        """

        key = str(project_id or "")
        try:
            number = int(port)
        except (TypeError, ValueError):
            number = 0
        if not key or not 1 <= number <= 65535:
            return {"available": False, "running": False, "url": "", "port": 0}
        session = {
            "available": True,
            "running": True,
            "source": "process",
            "url": f"http://{PREVIEW_HOST}:{number}/",
            "port": number,
            # The same number, named for what it is: the port a real ``GET /`` proved.
            # A project's own claim about itself is never published here.
            "observed_port": number,
            "entry_path": str(entry_path or ""),
            "entry_kind": str(entry_kind or ""),
            "diagnostics": {
                "loopback": True,
                "served_by": "project_process",
                "os_level_sandbox": False,
            },
        }
        with self._lock:
            self._processes[key] = dict(session)
        return dict(session)

    def unregister_process(self, project_id: object) -> None:
        """Forget a process preview (the process ended, or was stopped)."""

        key = str(project_id or "")
        with self._lock:
            self._processes.pop(key, None)

    def status(self, project_id: object) -> Dict[str, Any]:
        """The runtime state of one project's preview (never an absolute path)."""

        key = str(project_id or "")
        with self._lock:
            session = self._sessions.get(key)
            process = self._processes.get(key)
        if process is not None:
            return dict(process)
        if session is None:
            return {"running": False, "url": "", "port": 0, "entry_path": ""}
        payload = session.to_dict()
        payload["entry_path"] = session.entry_path
        return payload

    # ------------------------------------------------------------------ verbs
    def start(
        self,
        project_id: object,
        workspace: object,
        entry: Optional[EntryPoint],
    ) -> Dict[str, Any]:
        """Start a read-only preview of ``workspace`` for ``entry``.

        Returns a status dict. It refuses -- without starting anything -- when the
        manifest has no entry point, when the entry kind is not static, or when the
        workspace is not an existing directory.
        """

        key = str(project_id or "")
        if entry is None:
            return {
                "available": False,
                "running": False,
                "reason": NO_ENTRY_POINT,
                "url": "",
                "entry_path": "",
            }
        if entry.serve_kind != SERVE_KIND_STATIC:
            process = self.process_session(key)
            if process is not None:
                return process
            return {
                "available": False,
                "running": False,
                "reason": PREVIEW_NEEDS_PROCESS,
                "url": "",
                "entry_path": entry.path,
                "entry_kind": entry.kind,
            }

        try:
            # A ``WindowsPath`` carries its own ``.root`` (``"\\"``), so a path object
            # is converted before any ``.root`` attribute is consulted.
            if isinstance(workspace, (str, os.PathLike)):
                root: Optional[Path] = Path(workspace)
            else:
                root = Path(getattr(workspace, "root", workspace))
        except (TypeError, ValueError):
            root = None
        if root is None:
            return {
                "available": False,
                "running": False,
                "reason": "the project workspace is unavailable",
                "url": "",
                "entry_path": entry.path,
            }
        try:
            canonical = Path(os.path.realpath(str(root)))
        except (OSError, RuntimeError):
            canonical = None
        if canonical is None or not canonical.is_dir():
            return {
                "available": False,
                "running": False,
                "reason": "the project workspace is unavailable",
                "url": "",
                "entry_path": entry.path,
            }
        target = canonical.joinpath(*entry.path.split("/"))
        if target.is_symlink() or not target.is_file():
            return {
                "available": False,
                "running": False,
                "reason": "the entry point is not in the project workspace",
                "url": "",
                "entry_path": entry.path,
            }

        with self._lock:
            existing = self._sessions.get(key)
            if existing is not None and key in self._servers:
                return existing.to_dict()

            server = _PreviewServer((PREVIEW_HOST, 0), _PreviewHandler)
            server.workspace_root = str(canonical)
            server.entry_path = entry.path
            port = int(server.server_address[1])
            session = PreviewSession(
                project_id=key,
                workspace_root=str(canonical),
                port=port,
                url=f"http://{PREVIEW_HOST}:{port}/{entry.path}",
                entry_path=entry.path,
            )
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.05},
                name=f"forge-preview-{key}",
                daemon=True,
            )
            thread.start()
            self._servers[key] = server
            self._threads[key] = thread
            self._sessions[key] = session
            return session.to_dict()

    def stop(self, project_id: object) -> Dict[str, Any]:
        """Stop one preview; a no-op that reports ``running: false`` when absent.

        A preview served by the project's **own process** is not this module's to
        stop: ending it would close a URL while the process kept running, and the
        process is the thing the owner started. The answer says so, and
        ``POST /api/projects/{id}/run/stop`` is the way to end it.
        """

        key = str(project_id or "")
        with self._lock:
            process = self._processes.get(key)
        if process is not None:
            return {**process, "stopped": False, "reason": PREVIEW_OWNED_BY_PROCESS}
        with self._lock:
            server = self._servers.pop(key, None)
            thread = self._threads.pop(key, None)
            session = self._sessions.pop(key, None)
        if server is not None:
            try:
                server.shutdown()
            except Exception:  # noqa: BLE001 - a stopped server is the goal
                pass
            try:
                server.server_close()
            except Exception:  # noqa: BLE001
                pass
        if thread is not None:
            try:
                thread.join(timeout=2.0)
            except RuntimeError:  # pragma: no cover - joining from its own thread
                pass
        return {
            "running": False,
            "url": "",
            "port": 0,
            "entry_path": session.entry_path if session is not None else "",
        }

    def stop_all(self) -> None:
        """Stop every preview this process started (used at shutdown)."""

        with self._lock:
            keys = list(self._servers)
            self._processes.clear()
        for key in keys:
            self.stop(key)
