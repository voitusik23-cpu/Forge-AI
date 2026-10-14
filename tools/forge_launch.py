"""Start, supervise and cleanly stop the Forge AI Command Center.

Design notes, each answering a requirement:

* **No console window.** The launcher itself is started through ``pythonw.exe`` and it
  starts both servers through ``pythonw.exe`` as well, so nothing flashes on screen.
* **No duplicate servers.** A port that already answers is reused, not replaced, and it is
  then *not* stopped on exit -- the launcher only cleans up what it started itself.
* **Wait for readiness.** The WebView2 window opens only after ``/api/status`` and the shell
  page both answer, so the first screen is never an error page.
* **Loopback only.** Both servers are bound to ``127.0.0.1``; nothing is exposed on the
  network, and no firewall rule is needed.
* **A readable journal.** Every step is appended to ``.forge/logs/launcher.log`` with a
  timestamp, and each server's own output goes to its own file.
* **A real error message.** If a server does not come up, a Windows message box states what
  failed and where the log is -- shown without a console.

Run it directly, or use the desktop shortcut, or ``--selftest`` to exercise the whole
lifecycle headlessly.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
LOGS = REPO / ".forge" / "logs"
API_PORT = 8011
UI_PORT = 8020
API_URL = f"http://127.0.0.1:{API_PORT}/api/status"
UI_BASE = f"http://127.0.0.1:{UI_PORT}/"


def ui_url(screen: str) -> str:
    """The entry the window opens: the workspace, or the authorization screen."""

    return UI_BASE if screen != "auth" else f"{UI_BASE}?screen=auth"
READY_TIMEOUT_SECONDS = 45
EDGE = pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


class Journal:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}"
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def port_is_served(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def answers(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 500
    except urllib.error.HTTPError as error:
        return error.code < 500
    except Exception:  # noqa: BLE001 - not answering yet is not an error here
        return False


def start_server(arguments: list[str], log_name: str) -> subprocess.Popen:
    pythonw = REPO / ".venv" / "Scripts" / "pythonw.exe"
    executable = str(pythonw if pythonw.exists() else REPO / ".venv" / "Scripts" / "python.exe")
    environment = dict(os.environ)
    environment.update(
        {
            "FORGE_ENV": "test",
            "FORGE_API_HOST": "127.0.0.1",
            "FORGE_API_PORT": str(API_PORT),
            "PYTHONPATH": str(REPO),
        }
    )
    handle = (LOGS / log_name).open("a", encoding="utf-8")
    return subprocess.Popen(
        [executable, "-B", "-X", "utf8", *arguments],
        cwd=str(REPO),
        env=environment,
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def message_box(title: str, text: str) -> None:
    """A modal error notice that needs no console and no GUI toolkit."""

    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:  # noqa: BLE001 - a missing message box must not crash the launcher
        print(f"{title}: {text}", file=sys.stderr)


# ---------------------------------------------------------------------------- hygiene
def forge_processes() -> list[int]:
    """PIDs of Forge AI server processes, identified by their executable path.

    Matching on the command line was wrong once: an ad-hoc cleanup of mine also matched the
    harness that was running the command, because the harness command line contained the
    pattern. The executable path cannot match a shell that merely mentions the script.
    """

    if os.name != "nt":
        return []
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Process -ErrorAction SilentlyContinue | "
                "Where-Object { $_.Path -like '*Forge-AI*' -and $_.Path -like '*\\Scripts\\python*' } | "
                "Select-Object -ExpandProperty Id",
            ],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except subprocess.TimeoutExpired:
        # Hygiene is best effort: a slow shell must never delay the launch.
        return []
    pids = []
    for line in (completed.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def report_existing(journal: Journal) -> None:
    """Report other Forge AI server processes without terminating anything.

    Terminating them was measured to be unsafe: the path-based enumeration can select a
    process belonging to the launch that is starting, and the launch then dies. The supervisor
    already reuses a server that answers and only ever stops the processes it started itself,
    so hygiene here is diagnostics only.
    """

    pids = [pid for pid in forge_processes() if pid != os.getpid()]
    if not pids:
        journal.write("no other Forge AI server processes are running")
        return
    journal.write(f"other Forge AI server processes: {pids} (left untouched on purpose)")
    if not port_is_served(API_PORT) and not port_is_served(UI_PORT):
        journal.write(
            "note: ports are free but those processes exist; the launcher will start fresh "
            "servers and will not touch them"
        )


class Supervisor:
    def __init__(self, journal: Journal, open_window: bool, url: str) -> None:
        self.journal = journal
        self.open_window = open_window
        self.url = url
        self.own: list[tuple[str, subprocess.Popen]] = []

    def ensure(self, name: str, port: int, arguments: list[str], log_name: str, url: str) -> bool:
        if port_is_served(port):
            self.journal.write(f"{name}: port {port} already answers, reusing it (will not be stopped)")
            return True
        self.journal.write(f"{name}: starting on 127.0.0.1:{port}")
        process = start_server(arguments, log_name)
        self.own.append((name, process))
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.journal.write(f"{name}: process exited early with code {process.returncode}")
                self.journal.write(f"{name}: see {(LOGS / log_name)}")
                return False
            if answers(url):
                self.journal.write(f"{name}: ready after {READY_TIMEOUT_SECONDS - int(deadline - time.monotonic())}s")
                return True
            time.sleep(0.5)
        self.journal.write(f"{name}: not ready within {READY_TIMEOUT_SECONDS}s")
        self.journal.write(f"{name}: see {(LOGS / log_name)}")
        return False

    def watch_api(self, stop_event: threading.Event) -> None:
        """Keep the API alive while the window is open; one restart, then report."""

        restarts = 0
        while not stop_event.is_set():
            if stop_event.wait(5.0):
                return
            if answers(API_URL):
                continue
            if restarts >= 1:
                self.journal.write("core-api is not answering again; not restarting a second time")
                return
            restarts += 1
            self.journal.write("core-api stopped answering; restarting it once")
            process = start_server(["-m", "app.api.main"], "api.log")
            self.own.append(("core-api (restarted)", process))
            deadline = time.monotonic() + READY_TIMEOUT_SECONDS
            while time.monotonic() < deadline and not answers(API_URL):
                time.sleep(0.5)
            self.journal.write(
                "core-api: " + ("back after restart" if answers(API_URL) else "did not come back")
            )

    def close_window(self) -> int:
        """Open the WebView2 window and wait for the user to close it."""

        if not EDGE.exists():
            self.journal.write(f"edge not found at {EDGE}")
            return 2
        profile = REPO / ".forge" / "webview-profile"
        process = subprocess.Popen(
            [
                str(EDGE),
                f"--app={self.url}",
                "--window-size=1600,1000",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            cwd=str(REPO),
        )
        stop_event = threading.Event()
        watchdog = threading.Thread(target=self.watch_api, args=(stop_event,), daemon=True)
        watchdog.start()
        self.journal.write(f"window opened at {self.url} (pid {process.pid}); waiting for it to close")
        process.wait()
        stop_event.set()
        self.journal.write("window closed")
        return 0

    def stop_own(self) -> None:
        for name, process in self.own:
            if process.poll() is None:
                self.journal.write(f"{name}: stopping pid {process.pid}")
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.journal.write(f"{name}: did not stop, killing pid {process.pid}")
                    process.kill()
            else:
                self.journal.write(f"{name}: already exited")
        self.own.clear()
        for port in (API_PORT, UI_PORT):
            for _ in range(20):
                if not port_is_served(port):
                    break
                time.sleep(0.25)
            self.journal.write(
                f"port {port}: {'still listening' if port_is_served(port) else 'free'}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge AI Command Center launcher")
    parser.add_argument(
        "--screen",
        choices=("app", "auth"),
        default="app",
        help="which entry to open: the workspace, or the authorization screen",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="start, verify readiness, then stop again without opening a window",
    )
    args = parser.parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)
    journal = Journal(LOGS / "launcher.log")
    journal.write("=" * 60)
    journal.write(f"launcher start (selftest={args.selftest})")

    if not (REPO / "ui" / "dist" / "index.html").exists():
        text = (
            "The interface is not built yet.\n\n"
            "Expected: ui\\dist\\index.html\n\n"
            "Build it with:\n"
            "  cd ui\n"
            "  node node_modules\\vite\\bin\\vite.js build"
        )
        journal.write("ui/dist/index.html is missing")
        message_box("Forge AI -- interface not built", text)
        return 3

    target = ui_url(args.screen)
    journal.write(f"entry to open: {target}")
    if not args.selftest:
        report_existing(journal)
    supervisor = Supervisor(journal, open_window=not args.selftest, url=target)
    try:
        api_ok = supervisor.ensure(
            "core-api",
            API_PORT,
            ["-m", "app.api.main"],
            "api.log",
            API_URL,
        )
        ui_ok = supervisor.ensure(
            "shell-server",
            UI_PORT,
            ["tools/ui_preview_server.py", "--port", str(UI_PORT)],
            "ui.log",
            UI_BASE,
        )
        if not (api_ok and ui_ok):
            message_box(
                "Forge AI -- the local server did not start",
                "One of the local servers did not become ready.\n\n"
                f"Log: {LOGS / 'launcher.log'}\n"
                f"Server logs: {LOGS}",
            )
            return 4
        if args.selftest:
            journal.write("selftest: readiness confirmed, stopping our own processes")
            return 0
        return supervisor.close_window()
    finally:
        supervisor.stop_own()
        journal.write("launcher finished")


if __name__ == "__main__":
    raise SystemExit(main())
