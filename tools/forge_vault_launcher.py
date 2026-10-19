"""Open the AI vault window, starting its local server first when needed.

The owner starts this from the desktop shortcut ``АИ``; the shortcut runs it through
``pythonw.exe``, so nothing flashes on screen. What it does, in order:

1. If the vault server already answers on 127.0.0.1:8030, reuse it -- a second copy is
   never started, and the existing one is left alone on exit.
2. Otherwise start ``tools/forge_vault_server.py`` with the same ``pythonw.exe`` in a
   detached, console-free process, and wait until it answers ``/api/ping``.
3. If the application window is already open (its title carries "АИ"), bring it to the
   front instead of opening a second one.
4. Otherwise open Chrome in app mode:
   ``--app=http://127.0.0.1:8030/ --window-size=1200,900 --user-data-dir=<profile>
   --no-first-run``.

Two measured facts shape step 4, both reproduced on this machine:

* Chrome keeps a browser process alive after the last window closes, and while that
  process lives a new ``--app`` start for the *same* profile exits within a second
  without opening anything. The launcher therefore starts on ``.forge/vault-profile`` and,
  when that profile is still held, moves on to ``vault-profile-2``, ``-3``, ... until a
  window appears. Free profiles are reused, so the set stays small.
* ``chrome.exe`` started by a Python launcher dies at once with STATUS_BREAKPOINT while
  its browser sandbox initialises (the same measurement is recorded in
  ``tools/forge_launch.py`` for the Forge window), and the same build opens the window
  with ``--no-sandbox``. The window is therefore started with
  ``--no-sandbox --test-type``; ``--test-type`` only hides the flag warning strip.

Every step is written to ``.forge/logs/vault.log``. A failure ends in a Windows message
box that says what happened and where the log is, because a pythonw process has no
console to print to.

This launcher never stops the Forge application or its servers, and never touches ports
8011/8020; the vault is a separate, read-only consumer of the balance report.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
SERVER = REPO / "tools" / "forge_vault_server.py"
DEFAULT_PORT = 8030
SERVER_LOG = REPO / ".forge" / "logs" / "vault-server.log"
LAUNCH_LOG = REPO / ".forge" / "logs" / "vault.log"
PROFILE = REPO / ".forge" / "vault-profile"
PROFILE_SLOTS = 6

CHROME = pathlib.Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
EDGE = pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
WINDOW_TITLE_PART = "АИ — ключи"

CREATE_NO_WINDOW = 0x08000000
#: A cold Chrome start measured well past 15 s on this machine.
WINDOW_WAIT_SECONDS = 25


def log(message: str) -> None:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} {message}\n"
    try:
        LAUNCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LAUNCH_LOG, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def message_box(text: str, title: str = "АИ — хранилище ключей") -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:  # noqa: BLE001 - no GUI available (tests, service context)
        print(f"{title}: {text}", file=sys.stderr)


def pythonw() -> pathlib.Path:
    """Prefer pythonw.exe so a double-click never opens a console window."""

    candidate = pathlib.Path(sys.executable)
    if candidate.name.lower() == "python.exe":
        sibling = candidate.with_name("pythonw.exe")
        if sibling.exists():
            return sibling
    return candidate


def listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def ping(port: int, timeout: float = 1.5) -> dict:
    url = f"http://127.0.0.1:{port}/api/ping"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_ready(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if ping(port).get("service") == "forge-vault":
                return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.3)
    return False


def start_server(port: int) -> bool:
    SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    handle = open(SERVER_LOG, "a", encoding="utf-8")
    try:
        subprocess.Popen(
            [str(pythonw()), str(SERVER), "--port", str(port)],
            cwd=str(REPO),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    finally:
        handle.close()
    log(f"server started on port {port}")
    return wait_ready(port)


# --------------------------------------------------------------------------- window


def find_window(part: str):
    """Return the HWND of a visible top-level window whose title contains ``part``."""

    found = []
    user32 = ctypes.windll.user32
    buffer = ctypes.create_unicode_buffer(512)

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return 1
        user32.GetWindowTextW(hwnd, buffer, 512)
        if part in buffer.value:
            found.append(hwnd)
            return 0
        return 1

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def focus_window(hwnd) -> None:
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)


def browser() -> pathlib.Path:
    for candidate in (CHROME, EDGE):
        if candidate.exists():
            return candidate
    return CHROME


def profile_slots() -> list:
    """``.forge/vault-profile`` first, then numbered siblings for a profile Chrome still holds."""

    extras = [PROFILE.with_name(f"vault-profile-{n}") for n in range(2, PROFILE_SLOTS + 1)]
    return [PROFILE] + extras


def profile_is_free(path: pathlib.Path) -> bool:
    """True when nothing holds ``path`` -- checked by renaming it away and back.

    Chrome keeps the profile directory open for as long as any of its processes live,
    even after the window is gone, and a profile that is held is exactly what makes a new
    ``--app`` start exit or wait without opening a window. Renaming needs no evaluation of
    command lines and costs microseconds; a held directory simply refuses the rename.
    """

    if not path.exists():
        return True
    probe = path.with_name(path.name + ".probe")
    try:
        os.rename(path, probe)
        os.rename(probe, path)
        return True
    except OSError:
        return False


def choose_profile() -> pathlib.Path:
    """The first profile nothing holds, else a brand-new directory name."""

    for slot in profile_slots():
        if profile_is_free(slot):
            return slot
    return REPO / ".forge" / f"vault-profile-{int(time.time())}"


def prune_profiles(max_age_seconds: float = 3600.0) -> None:
    """Delete old numbered profiles that nothing holds, so the set cannot grow forever."""

    horizon = time.time() - max_age_seconds
    for entry in sorted((REPO / ".forge").glob("vault-profile*")):
        if entry == PROFILE or not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime > horizon:
                continue
        except OSError:
            continue
        if profile_is_free(entry):
            shutil.rmtree(entry, ignore_errors=True)
            log(f"pruned unused profile {entry.name}")


def open_window(port: int, profile: pathlib.Path):
    """Start Chrome in app mode on ``profile`` and return the process handle."""

    url = f"http://127.0.0.1:{port}/"
    executable = browser()
    if not executable.exists():
        raise FileNotFoundError(f"браузер не найден: {executable}")
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        str(executable),
        f"--app={url}",
        "--window-size=1200,900",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        # Measured: the browser sandbox cannot initialise when chrome is started by a
        # Python launcher on this machine, and the window never appears without this.
        "--no-sandbox",
        "--test-type",
    ]
    process = subprocess.Popen(
        args,
        cwd=str(REPO),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        close_fds=True,
    )
    log(f"window opened: {' '.join(args)}")
    return process


def wait_window(process, timeout: float) -> str:
    """Wait for the app window; report ``window``, ``exited`` or ``timeout``.

    A cold Chrome start is slow, so the wait is generous -- but a browser that already
    exited cannot produce a window, and waiting out the full deadline for it would make
    every start feel broken. A one-off exit is not proof either: Chrome hands the command
    line to an already running instance of the same profile, and that instance opens the
    window while the launched process exits.
    """

    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_window(WINDOW_TITLE_PART):
            return "window"
        if process is not None and process.poll() is not None:
            time.sleep(1.5)  # hand-off to a live browser takes a moment
            return "window" if find_window(WINDOW_TITLE_PART) else "exited"
        time.sleep(0.4)
    return "timeout"


def selftest(port: int) -> int:
    """Exercise the whole start path without opening a window."""

    report = {"port": port, "listening_before": listening(port)}
    if not report["listening_before"]:
        report["server_started"] = start_server(port)
    else:
        try:
            report["server_identity"] = ping(port).get("service")
        except (urllib.error.URLError, OSError, ValueError) as error:
            report["server_identity"] = f"unreachable: {type(error).__name__}"
    report["ready"] = wait_ready(port, timeout=5) if not report.get("server_started") else True
    report["window_open"] = bool(find_window(WINDOW_TITLE_PART))
    report["profile_slots"] = [slot.name for slot in profile_slots()]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ready", True) else 1


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description="Открыть окно АИ с ключами и балансами")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="только поднять сервер")
    parser.add_argument("--selftest", action="store_true", help="проверить запуск без окна")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest(args.port)

    log("launcher started")
    try:
        if listening(args.port):
            try:
                identity = ping(args.port).get("service")
            except (urllib.error.URLError, OSError, ValueError) as error:
                log(f"port {args.port} busy and not the vault: {type(error).__name__}")
                message_box(
                    f"Порт {args.port} занят другой программой.\n"
                    f"Подробности: {LAUNCH_LOG}"
                )
                return 2
            if identity != "forge-vault":
                log(f"port {args.port} answers as {identity!r}, refusing")
                message_box(
                    f"Порт {args.port} отвечает не как хранилище АИ.\n"
                    f"Подробности: {LAUNCH_LOG}"
                )
                return 2
            log(f"reusing running server on {args.port}")
        elif not start_server(args.port):
            log("server did not become ready")
            message_box(
                "Сервер хранилища не запустился за 25 секунд.\n\n"
                f"Лог: {SERVER_LOG}\nЛог запуска: {LAUNCH_LOG}"
            )
            return 3

        if args.no_browser:
            print(f"http://127.0.0.1:{args.port}/")
            return 0

        hwnd = find_window(WINDOW_TITLE_PART)
        if hwnd:
            focus_window(hwnd)
            log("existing window focused")
            return 0

        prune_profiles()
        profile = choose_profile()
        log(f"profile for the window: {profile.name}")
        process = open_window(args.port, profile)
        outcome = wait_window(process, WINDOW_WAIT_SECONDS)
        used = profile
        if outcome != "window":
            log(f"profile {profile.name} ended: {outcome}")
            time.sleep(2.0)
            # A profile that looked free can still be held by a browser Chrome is winding
            # down; a brand-new directory always starts clean.
            used = REPO / ".forge" / f"vault-profile-{int(time.time())}"
            process = open_window(args.port, used)
            outcome = wait_window(process, WINDOW_WAIT_SECONDS)
            if outcome != "window":
                log(f"profile {used.name} ended: {outcome}")
        if outcome == "window":
            log(f"window is on screen (profile {used.name})")
            return 0

        message_box(
            "Окно АИ не появилось.\n\n"
            f"Подробности: {LAUNCH_LOG}\nСервер: {SERVER_LOG}"
        )
        return 4
    except Exception as error:  # noqa: BLE001 - a pythonw start must not die silently
        log(f"failed: {type(error).__name__}: {error}")
        message_box(f"Не удалось открыть окно АИ: {type(error).__name__}: {error}\n\nЛог: {LAUNCH_LOG}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
