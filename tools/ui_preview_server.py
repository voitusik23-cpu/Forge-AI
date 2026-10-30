"""Serve the built command center and proxy /api to the real Forge Core.

Used for the prototype screenshots, for local review, and on a server: the frontend is
served from ``ui/dist`` while ``/api/*`` is forwarded to the running API on
``127.0.0.1:8011``, so the interface shows real core data on a single origin and no
backend change is needed.

Bind rules, the same as the Core API's
--------------------------------------
Both servers can be reached by strangers the moment the host is not loopback, so the same
rule is applied here:

* ``ui_bind_host`` / ``ui_port`` in the owner's settings file decide where this listens
  (defaults ``127.0.0.1`` / ``8020``); ``--host`` / ``--port`` override them;
* a **non-loopback host without an access token refuses to start**, with the exact reason
  printed, because the surface can write files and start processes;
* with a token configured (``FORGE_ACCESS_TOKEN`` through the secrets layer, or
  ``access_token`` in the settings file), every dynamic route here -- ``/api/*``,
  ``/diag``, ``/ui-defaults.json`` and ``/docs/*`` -- requires it, and the token is
  forwarded to the Core instead of being replaced by this proxy. Static assets stay open:
  they are the page that has to load before it can send the token, and they contain no
  data.

The UI learns the token from a one-time ``?token=...`` URL, keeps it in ``sessionStorage``
and sends it as ``X-Forge-Token`` on every call (``ui/src/api/access-token.ts``).
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

from app.api.access import (  # noqa: E402 - the sys.path line above has to run first
    AccessPolicy,
    access_log_line,
    extract_token,
    is_protected_path,
    refuse_if_exposed,
    resolve_access_token,
    tokens_match,
)

#: Paths this shell answers itself that carry data or diagnostics. They require the token
#: when one is configured; the static bundle does not.
_DYNAMIC_PATHS = ("/diag", "/ui-defaults.json", "/docs/")


def path_needs_token(path: str) -> bool:
    """Whether one request path is a data/diagnostic route rather than a static asset."""

    clean = path.split("?", 1)[0]
    if is_protected_path(clean):
        return True
    return any(clean == item or clean.startswith(item) for item in _DYNAMIC_PATHS)


class Handler(http.server.SimpleHTTPRequestHandler):
    #: The access policy of this process, set by :func:`main` before it serves. The default
    #: carries no token, which is exactly the loopback deployment.
    access: AccessPolicy = AccessPolicy()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="ui/dist", **kwargs)

    def log_message(self, *args):  # keep the console readable
        return

    def _authorized(self) -> bool:
        """Whether this request may reach a dynamic route on this deployment.

        The two spellings the Core accepts are accepted here too, and the comparison is the
        same constant-time helper -- one rule, two processes.
        """

        policy = type(self).access
        if not policy.enabled:
            return True
        return any(tokens_match(value, policy.token) for value in extract_token(self.headers))

    def _refuse_unauthorized(self) -> None:
        body = json.dumps(
            {
                "detail": {
                    "refused": True,
                    "error_code": "unauthorized",
                    "code": "unauthorized",
                    "message": (
                        "unauthorized: this Forge server requires its access token, sent "
                        "as 'Authorization: Bearer <token>' or 'X-Forge-Token: <token>'"
                    ),
                    "executed": False,
                }
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("WWW-Authenticate", 'Bearer realm="forge"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - the base class names it this way
        if path_needs_token(self.path) and not self._authorized():
            self._refuse_unauthorized()
            return
        if self.path.startswith("/diag"):
            self._diagnose()
            return
        if self.path.split("?", 1)[0] == "/ui-defaults.json":
            self._ui_defaults()
            return
        if self.path.split("?", 1)[0].startswith("/docs/"):
            self._docs_file()
            return
        if self.path.startswith("/api/"):
            self._proxy("GET")
            return
        if self.path in ("/", "") or self.path.startswith("/?"):
            self.path = "/index.html"
        super().do_GET()

    def _docs_file(self) -> None:
        """Serve one Markdown file from ``docs/`` so the interface can link to the manual.

        The «Справка» screen links to ``/docs/USER_MANUAL.md``. The bundle lives in ``ui/dist``,
        so without this handler that link answers 404 and the guide would be a broken promise.

        The handler is read-only and confined: one path segment, an allow-listed charset, a
        ``.md`` suffix, and the resolved target must sit inside ``docs/``. Nothing outside that
        directory is reachable, and no repository file is listed.
        """

        import re

        document = self.path.split("?", 1)[0][len("/docs/"):]
        if re.fullmatch(r"[A-Za-z0-9._-]{1,120}\.md", document) is None or ".." in document:
            self._docs_refusal(400, "the document name is not an allowed markdown file")
            return
        root = (REPO_ROOT / "docs").resolve()
        target = (root / document).resolve()
        if root not in target.parents:
            self._docs_refusal(403, "the document escapes docs/")
            return
        if not target.is_file():
            self._docs_refusal(404, "no such document in docs/")
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _docs_refusal(self, status: int, reason: str) -> None:
        body = f'{{"error": "{reason}"}}'.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        self._guard_then_proxy("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._guard_then_proxy("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._guard_then_proxy("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._guard_then_proxy("DELETE")

    def _guard_then_proxy(self, method: str) -> None:
        """Check the token, then forward the request with that same token intact."""

        if path_needs_token(self.path) and not self._authorized():
            self._refuse_unauthorized()
            return
        self._proxy(method)

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
        # The caller's own credential travels with the request. The Core compares it
        # against its own token, so this proxy never has to hold or inject one -- and a
        # deployment that put the token in ``.env`` for the Core alone would still work.
        for name in ("Authorization", "X-Forge-Token"):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        # The reverse proxy's client address is passed on so the Core's per-address rate
        # limit charges the real caller instead of this process.
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            headers["X-Forwarded-For"] = forwarded
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
    parser.add_argument("--host", default=None, help="bind address (default: the ui_bind_host setting)")
    parser.add_argument("--port", type=int, default=None, help="bind port (default: the ui_port setting)")
    args = parser.parse_args()

    # Where to listen and whether a token guards it: exactly one reader, shared with the
    # Core API, so the two processes cannot disagree about the deployment's shape. The
    # safety refusal comes BEFORE the bundle check: a misconfigured deployment should learn
    # the dangerous thing first, not that its build output is missing.
    from app.config.app_settings import AppSettingsStore

    settings = AppSettingsStore()
    host = (args.host or settings.ui_bind_host()).strip() or "127.0.0.1"
    port = int(args.port if args.port is not None else settings.ui_port())
    token = resolve_access_token(settings=settings)
    policy = AccessPolicy(token=token)
    try:
        refuse_if_exposed(host, policy.token, surface="Forge UI")
    except RuntimeError as refusal:
        # The exact reason, on stderr, and a non-zero exit: a service manager shows this
        # line and the operator reads it there instead of finding an open port later.
        print(refusal, file=sys.stderr, flush=True)
        return 2

    if not pathlib.Path("ui/dist/index.html").exists():
        print("ui/dist is missing: run the frontend build first")
        return 2

    Handler.access = policy
    with _Server((host, port), Handler) as httpd:
        line = access_log_line(host=host, port=port, policy=policy, surface="Forge UI shell")
        print(line, flush=True)
        print(f"command center on http://{host}:{port}/", flush=True)
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
