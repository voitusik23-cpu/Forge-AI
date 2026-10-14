"""Serve the built command center and proxy /api to the real Forge Core.

Used for the prototype screenshots and for local review: the frontend is served from
``ui/dist`` while ``/api/*`` is forwarded to the running API on 127.0.0.1:8011, so the
interface shows real core data on a single origin and no backend change is needed.
"""

from __future__ import annotations

import argparse
import http.server
import pathlib
import socketserver
import urllib.error
import urllib.request

UPSTREAM = "http://127.0.0.1:8011"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="ui/dist", **kwargs)

    def log_message(self, *args):  # keep the console readable
        return

    def do_GET(self) -> None:  # noqa: N802 - the base class names it this way
        if self.path.startswith("/api/"):
            self._proxy("GET")
            return
        if self.path in ("/", "") or self.path.startswith("/?"):
            self.path = "/index.html"
        super().do_GET()

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    if not pathlib.Path("ui/dist/index.html").exists():
        print("ui/dist is missing: run the frontend build first")
        return 2
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(f"command center on http://127.0.0.1:{args.port}/")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
