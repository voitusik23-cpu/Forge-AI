"""Serve the built command center and proxy /api to the real Forge Core.

Used for the prototype screenshots and for local review: the frontend is served from
``ui/dist`` while ``/api/*`` is forwarded to the running API on 127.0.0.1:8011, so the
interface shows real core data on a single origin and no backend change is needed.
"""

from __future__ import annotations

import argparse
import http.server
import json
import pathlib
import socketserver
import sys
import urllib.error
import urllib.request

UPSTREAM = "http://127.0.0.1:8011"

#: The repository root, so ``app.config.settings`` imports even when this file is started by hand
#: (the launcher already passes PYTHONPATH=REPO, a manual ``python tools/ui_preview_server.py``
#: does not).
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="ui/dist", **kwargs)

    def log_message(self, *args):  # keep the console readable
        return

    def do_GET(self) -> None:  # noqa: N802 - the base class names it this way
        if self.path.startswith("/diag"):
            self._diagnose()
            return
        if self.path.split("?", 1)[0] == "/ui-defaults.json":
            self._ui_defaults()
            return
        if self.path.startswith("/api/"):
            self._proxy("GET")
            return
        if self.path in ("/", "") or self.path.startswith("/?"):
            self.path = "/index.html"
        super().do_GET()

    def _ui_defaults(self) -> None:
        """The provider and model the installed configuration defaults to.

        ``FORGE_DEFAULT_MODEL`` is resolved inside the Core by ``app/config/settings.py``, and the
        Core has no route that publishes it (``/api/status.details`` carries ``default_provider``
        only). The workspace has to preselect the model the Core would actually use, so the shell
        reads the two routing defaults from that same settings loader instead of hard-coding them
        into the bundle, where they would go stale the moment ``.env`` changes.

        Read-only and secret-free: only ``default_provider``/``default_model`` leave this handler,
        and ``load_settings`` itself only reads ``FORGE_``-prefixed names. A failure is reported as
        the exception type, never as a value, and the screen then falls back to the first model of
        the provider.
        """
        payload: dict[str, str] = {"source": "app.config.settings.load_settings"}
        try:
            from app.config.settings import load_settings

            settings = load_settings()
            payload["default_provider"] = str(settings.default_provider)
            payload["default_model"] = str(settings.default_model)
        except Exception as error:  # noqa: BLE001 - report the failure, never invent a default
            payload["error"] = f"{type(error).__name__}: {error}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _diagnose(self) -> None:
        """Explain why the interface cannot reach the Core API.

        Read-only and secret-free: it reports ports, processes, the build timestamp and the
        tail of the API log. It never prints environment values or credentials.
        """

        import json
        import pathlib
        import socket
        import time
        import urllib.request

        def listening(port: int) -> bool:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.4)
                return probe.connect_ex(("127.0.0.1", port)) == 0

        api_ok = False
        try:
            with urllib.request.urlopen(f"{UPSTREAM}/api/status", timeout=3) as response:
                api_ok = response.status == 200
        except Exception:  # noqa: BLE001 - the whole point is to report the failure
            api_ok = False

        log = pathlib.Path(".forge/logs/api.log")
        tail = ""
        if log.exists():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-12:])

        index = pathlib.Path("ui/dist/index.html")
        payload = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "api_reachable": api_ok,
            "api_port_listening": listening(8011),
            "shell_port_listening": listening(8020),
            "api_log_tail": tail,
            "ui_built_at": (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(index.stat().st_mtime))
                if index.exists()
                else ""
            ),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Write methods must reach the Core API. Without these, POST /api/tasks/run answered
    # 501 from the base class and the run form could never dispatch anything.
    def do_POST(self) -> None:  # noqa: N802
        self._proxy("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy("DELETE")

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _proxy(self, method: str = "GET") -> None:
        body = self._body() if method in ("POST", "PUT", "PATCH") else b""
        headers = {"accept": "application/json"}
        if body:
            headers["content-type"] = self.headers.get("Content-Type", "application/json")
        request = urllib.request.Request(
            UPSTREAM + self.path, data=body or None, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as error:
            # The backend's own refusal must reach the interface unchanged.
            payload = error.read() or b"{}"
            self.send_response(error.code)
            self.send_header("Content-Type", error.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as error:  # noqa: BLE001 - report, never pretend
            body_text = f'{{"error": "core unreachable: {type(error).__name__}"}}'.encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_text)))
            self.end_headers()
            self.wfile.write(body_text)


class _Server(socketserver.ThreadingTCPServer):
    """Handle each connection in its own thread.

    ``TCPServer`` handles one connection at a time, and the ``/api`` proxy waits up to 300s for the
    Core to answer (``_proxy``). One slow or stuck request - for example the request the open
    application window itself holds - therefore froze every other client, which is exactly what
    measured as "port 8020 listens but '/' never answers". The threads are daemons so stopping the
    server never waits on a client, and the address is reusable so a restart does not fail on a socket
    left in TIME_WAIT.
    """

    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    if not pathlib.Path("ui/dist/index.html").exists():
        print("ui/dist is missing: run the frontend build first")
        return 2
    with _Server(("127.0.0.1", args.port), Handler) as httpd:
        print(f"command center on http://127.0.0.1:{args.port}/")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
