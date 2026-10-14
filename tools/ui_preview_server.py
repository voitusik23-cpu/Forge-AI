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
            self._proxy()
            return
        if self.path in ("/", "") or self.path.startswith("/?"):
            self.path = "/index.html"
        super().do_GET()

    def _proxy(self) -> None:
        request = urllib.request.Request(UPSTREAM + self.path, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as error:
            self.send_response(error.code)
            self.end_headers()
        except Exception as error:  # noqa: BLE001 - report, never pretend
            body = f'{{"error": "core unreachable: {type(error).__name__}"}}'.encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


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
