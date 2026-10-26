"""Show «Почта АИ» in one double-click -- and never fail in silence.

The owner's report, in his own words: the shortcut stopped showing his application.
It reported success, and what he saw was a grey window titled ``127.0.0.1 /``. Four
separate things had gone wrong, and this module answers each of them:

* **Nothing was verified.** The old shortcut ran ``app.py --open``, whose last step is
  ``os.startfile(url)``. Under ``pythonw.exe`` that call opens the machine's default
  browser at whatever it decides -- or nothing at all -- and it cannot report a
  failure, because there is no console to print to. Here every step is *checked*: the
  server has to answer ``/api/health``, the page has to answer ``GET /`` with a 2xx,
  and a browser window has to exist with a title that is not a failed page.
* **The profile was reused.** The measured cause of the grey window: a browser
  profile left behind by an earlier attempt (``.forge\\app-profile\\...``) served a
  blank page whose title bar fell back to the bare host, ``127.0.0.1_/``. On a
  brand-new profile the same URL renders «Почта АИ». So a window that fails its own
  check is closed, its profile is discarded, and the next attempt starts from a
  clean one.
* **A dead browser looked like a live one.** A browser that hands the URL to an
  instance already running on that profile exits ``0`` and never becomes a window.
  Here the process is watched, and a window is only believed when it is *found* by
  enumerating the windows of that process.
* **Silence.** Every step is appended to ``<project>\\launch.log`` with a timestamp
  and the exact command line, and any failure ends in a Windows message box (and,
  as the last resort, ``os.startfile`` *plus* a message box naming the URL) rather
  than in nothing happening.

The order is the one the owner needs, and each step is idempotent:

1. read the port from ``settings.json`` (``8790`` when the file says nothing);
2. if ``/api/health`` does not answer, start ``pythonw.exe app.py`` **detached** with
   ``CREATE_NO_WINDOW`` and poll for up to :data:`SERVER_WAIT_SECONDS`;
3. verify ``GET /`` on that port really returns the application;
4. if a window we opened earlier is still there and healthy, focus it instead of
   opening a second one;
5. otherwise open ``--app=<url>`` in Chrome, then Edge, each in its own profile, and
   confirm the window exists; if neither produces a window, ``os.startfile`` and a
   message box with the URL.

Run it through ``pythonw.exe`` (the desktop shortcut does exactly that), so no
console ever flashes::

    pythonw.exe "C:\\...\\Forge-AI\\tools\\open_mail_ai.py"

An optional first argument is the project folder; without it the known project is
used.
"""

from __future__ import annotations

import ctypes
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent

#: The project this launcher exists for. The argument overrides it.
DEFAULT_PROJECT = REPO / ".forge" / "projects-visible" / "Почтовый центр (proj-fce53337245f)"

#: One browser profile for this application, under the repository's ignored ``.forge``.
PROFILE = REPO / ".forge" / "mail-profile"

#: What the previous launch left behind: the pid of the window that was opened, so a
#: second double-click focuses that window instead of opening a second one.
STATE_FILE = REPO / ".forge" / "mail-launch.json"

#: ``settings.json``'s own key, and the value the project uses when the key is absent.
DEFAULT_PORT = 8790

#: ``app.py`` binds the first free port at or above the remembered one, and a stale
#: neighbour can push it up; the wait therefore also accepts a port that started
#: answering just now, and never one that was already answering before this run.
PORT_SCAN = 20

HEALTH_PATH = "/api/health"
WINDOW_SIZE = "1280,900"

#: How long the server is given to answer ``/api/health`` after it was started.
SERVER_WAIT_SECONDS = 25.0

#: How long a started browser is given to produce its window.
WINDOW_WAIT_SECONDS = 12.0

#: How long a window we asked to close is given to disappear.
CLOSE_WAIT_SECONDS = 3.0

#: The two flags that keep a brand-new profile from covering the application with a
#: first-run bubble; both are about the browser's own chrome, never about the page.
QUIET_FLAGS = ("--no-first-run", "--no-default-browser-check")

#: A window whose title bar fell back to the bare host is a page that never rendered --
#: the measured signature of the grey window (``127.0.0.1_/``). It is not a success.
_FAILED_TITLE = re.compile(r"^127\.0\.0\.1(?=$|[\s:/_])")

#: The window class both Chromium browsers use, so a recycled pid cannot be mistaken
#: for our own browser window.
_BROWSER_CLASS = "Chrome_WidgetWin"

_IS_WINDOWS = os.name == "nt"

WM_CLOSE = 0x0010
SW_RESTORE = 9


# --------------------------------------------------------------------------- the log


def project_from(argv: list) -> pathlib.Path:
    """The project folder: the argument when there is one, the known path otherwise."""

    if len(argv) > 1 and str(argv[1]).strip():
        return pathlib.Path(str(argv[1]).strip().strip('"')).expanduser()
    return DEFAULT_PROJECT


def log_path(project: pathlib.Path) -> pathlib.Path:
    """``<project>\\launch.log``: the one file both this launcher and Forge read."""

    return project / "launch.log"


def log(project: pathlib.Path, message: str) -> None:
    """Append one timestamped line to the project's ``launch.log``.

    The format matches the lines the project's own ``app.py`` already writes, so one
    file reads as one history. A log that cannot be written must never fail a launch,
    and the line is echoed when a console happens to be attached.
    """

    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    try:
        project.mkdir(parents=True, exist_ok=True)
        with log_path(project).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    try:
        if sys.stderr is not None:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
    except (OSError, ValueError):
        pass


def message_box(title: str, text: str) -> None:
    """A visible notice that needs no console and no GUI toolkit."""

    if _IS_WINDOWS:
        try:
            # MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST: the owner has to see it.
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x40000 | 0x10000)
            return
        except Exception:  # noqa: BLE001 - a missing message box must not crash us
            pass
    try:
        if sys.stderr is not None:
            sys.stderr.write("%s: %s\n" % (title, text))
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------- server


def read_port(project: pathlib.Path) -> int:
    """The port from ``settings.json``, or :data:`DEFAULT_PORT` when it says nothing."""

    try:
        data = json.loads((project / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_PORT
    try:
        port = int(data.get("port") or 0)
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def health_answers(port: int, timeout: float = 1.0) -> bool:
    """Whether ``/api/health`` on this port answers 2xx right now."""

    url = "http://127.0.0.1:%d%s" % (port, HEALTH_PATH)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(getattr(response, "status", 0)) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def page_answers(port: int, timeout: float = 5.0) -> tuple:
    """``(status, bytes)`` for ``GET /`` on the project's port, or ``(0, 0)``."""

    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=timeout) as response:
            body = response.read()
            return int(getattr(response, "status", 0)), len(body)
    except (urllib.error.URLError, OSError, ValueError):
        return 0, 0


def pythonw() -> str:
    """``pythonw.exe``: the repository's own interpreter first, then the machine's."""

    candidate = REPO / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("pythonw") or shutil.which("pythonw.exe")
    return found or sys.executable


def server_flags() -> int:
    """Detach the server and give it no console window at all."""

    if not _IS_WINDOWS:
        return 0
    flags = 0
    for name in ("CREATE_NO_WINDOW", "DETACHED_PROCESS"):
        flags |= int(getattr(subprocess, name, 0) or 0)
    return flags


def browser_flags() -> int:
    """Detach the window: closing the launcher must not close the owner's application."""

    if not _IS_WINDOWS:
        return 0
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= int(getattr(subprocess, name, 0) or 0)
    return flags


def start_server(project: pathlib.Path):
    """Start the project's own server, detached and without a console window.

    ``POCHTA_NO_BROWSER=1`` tells ``app.py`` not to open a window of its own: this
    launcher opens the one window, and two independent openers are how the owner ended
    up looking at the wrong one. The variable is ignored by an ``app.py`` that predates
    it, which is why the window is *verified* afterwards rather than assumed.
    """

    vector = [pythonw(), str(project / "app.py")]
    log(project, "запускаю сервер: " + subprocess.list2cmdline(vector))
    environment = dict(os.environ)
    environment["POCHTA_NO_BROWSER"] = "1"
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.Popen(
        vector,
        cwd=str(project),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=server_flags(),
    )


def wait_for_server(project: pathlib.Path, port: int, baseline: set) -> int:
    """Return the port that answers ``/api/health``, or ``0`` after the deadline."""

    deadline = time.monotonic() + SERVER_WAIT_SECONDS
    while time.monotonic() < deadline:
        if health_answers(port):
            return port
        for candidate in range(port, port + PORT_SCAN):
            if candidate in baseline:
                continue
            if health_answers(candidate, timeout=0.4):
                return candidate
        time.sleep(0.4)
    return 0


def ensure_server(project: pathlib.Path, port: int) -> int:
    """Make the application answer, and return the port it really answers on.

    The port is read from ``settings.json``. A server that is already up is reused and
    never doubled -- two servers on one project is how a stale page ends up on screen.
    Ports that already answered *before* this run are excluded from the scan, so a
    neighbour application can never be mistaken for this one.
    """

    if health_answers(port):
        log(project, "сервер уже работает на порту %d" % port)
        return port
    baseline = {
        candidate
        for candidate in range(port, port + PORT_SCAN)
        if health_answers(candidate, timeout=0.3)
    }
    log(project, "сервер не отвечает на порту %d: запускаю его" % port)
    try:
        child = start_server(project)
    except OSError as error:
        log(project, "сервер не запустился: %s: %s" % (type(error).__name__, error))
        return 0
    found = wait_for_server(project, port, baseline)
    if found:
        log(project, "сервер отвечает на порту %d (pid %d)" % (found, int(child.pid or 0)))
        return found
    log(
        project,
        "сервер не ответил на %s за %d с: pid %s, код возврата %s"
        % (HEALTH_PATH, int(SERVER_WAIT_SECONDS), int(child.pid or 0), child.poll()),
    )
    return 0


# --------------------------------------------------------------------------- windows


_ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def desktop_windows() -> list:
    """Every visible top-level window of this desktop: ``[(hwnd, pid, title, class)]``.

    Only plain ``user32`` reads are used -- the window's text, its class and the
    process that owns it -- which is all this launcher needs, with no third-party
    module and no shell.
    """

    if not _IS_WINDOWS:
        return []
    user32 = ctypes.windll.user32
    found = []

    def _visit(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return True
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, text, length + 1)
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((int(hwnd), int(pid.value), text.value, name.value))
        return True

    try:
        user32.EnumWindows(_ENUM_PROC(_visit), None)
    except Exception:  # noqa: BLE001 - a window scan must never break a launch
        return []
    return found


def windows_of(pid: int) -> list:
    """``[(hwnd, title)]`` for the visible browser windows owned by one process."""

    if int(pid) <= 0:
        return []
    return [
        (hwnd, title)
        for hwnd, owner, title, name in desktop_windows()
        if owner == int(pid) and str(name).startswith(_BROWSER_CLASS)
    ]


def title_means_a_page(title: str) -> bool:
    """Whether a window title looks like a rendered page, not a failed one.

    A page that never rendered falls back to its address in the title bar; the measured
    example on this machine was ``127.0.0.1_/``. Such a window is exactly the grey
    window this launcher exists to prevent, so it is never accepted as success.
    """

    text = str(title or "").strip()
    if not text:
        return False
    return _FAILED_TITLE.match(text) is None


def wait_for_page_window(child, deadline: float) -> tuple:
    """``(hwnd, title)`` for the first healthy window of a started browser.

    A browser that exited -- with ``0`` when it handed the URL to an instance already
    running on that profile, or with a failure code -- never became *this* run's
    window, and the caller has to move on instead of claiming one.
    """

    pid = int(getattr(child, "pid", 0) or 0)
    while time.monotonic() < deadline:
        for hwnd, title in windows_of(pid):
            if title_means_a_page(title):
                return hwnd, title
        if child.poll() is not None:
            # One last look: the window may have existed a moment before the exit.
            time.sleep(0.3)
            for hwnd, title in windows_of(pid):
                if title_means_a_page(title):
                    return hwnd, title
            return 0, ""
        time.sleep(0.25)
    return 0, ""


def close_window(hwnd: int) -> bool:
    """Ask one window to close, and report whether it really went away."""

    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        ctypes.windll.user32.PostMessageW(int(hwnd), WM_CLOSE, 0, 0)
    except Exception:  # noqa: BLE001 - a close that fails is reported, not raised
        return False
    deadline = time.monotonic() + CLOSE_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not any(item[0] == int(hwnd) for item in desktop_windows()):
            return True
        time.sleep(0.15)
    return False


def focus_window(hwnd: int) -> bool:
    """Bring an already-open window to the front, un-minimised."""

    if not _IS_WINDOWS or not hwnd:
        return False
    user32 = ctypes.windll.user32
    try:
        user32.ShowWindow(int(hwnd), SW_RESTORE)
        user32.BringWindowToTop(int(hwnd))
        return bool(user32.SetForegroundWindow(int(hwnd)))
    except Exception:  # noqa: BLE001 - a focus that fails is not a launch failure
        return False


# -------------------------------------------------------------------------- browsers


def environment_roots() -> list:
    """The install roots of the two Chromium browsers on a Windows machine."""

    roots = []
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        value = os.environ.get(name)
        if value and value not in roots:
            roots.append(value)
    return roots


def browsers() -> list:
    """``[(name, executable)]`` -- Chrome first, then Edge, both installed for real.

    The order is the fallback chain: the app-mode window of the first browser that
    really produces a verified window is the one the owner sees.
    """

    found = []

    def _add(name: str, path) -> None:
        if not path:
            return
        try:
            if not pathlib.Path(str(path)).is_file():
                return
        except OSError:
            return
        item = (name, str(path))
        if item not in found:
            found.append(item)

    for name, executables, relative in (
        ("chrome", ("chrome", "chrome.exe"), ("Google", "Chrome", "Application", "chrome.exe")),
        ("edge", ("msedge", "msedge.exe"), ("Microsoft", "Edge", "Application", "msedge.exe")),
    ):
        for candidate in executables:
            _add(name, shutil.which(candidate))
        for root in environment_roots():
            _add(name, pathlib.Path(root).joinpath(*relative))
    return found


def browser_vector(
    executable: str, url: str, profile: pathlib.Path, sandbox: bool = True
) -> list:
    """The exact argument vector of one app-mode window.

    ``--app`` is what removes the address bar and the tabs: the window is the owner's
    application, not a browser page about it. ``--user-data-dir`` is this launcher's own
    profile, so a stale profile from an older attempt can never decide what the window
    shows, and the browser's singleton can never swallow the request. ``sandbox=False``
    adds :data:`NO_SANDBOX_RETRY` and is only ever reached by a retry.
    """

    vector = [
        executable,
        "--app=" + url,
        "--window-size=" + WINDOW_SIZE,
        "--user-data-dir=" + str(profile),
        *QUIET_FLAGS,
    ]
    if not sandbox:
        vector.extend(NO_SANDBOX_RETRY)
    return vector


#: The flags of the last resort before the message box. Measured on this machine on
#: 2026-10-26: a Chromium browser started **by a Python process** while that Python is
#: inside a Windows job object cannot initialise its renderer sandbox -- ``chrome.exe``
#: (155.0.8059.85) aborts about 0.3 s later with ``0x80000003`` (``STATUS_BREAKPOINT``),
#: and ``msedge.exe`` (156.0.8078.48) survives but shows a blank page whose title bar
#: falls back to the bare host (``127.0.0.1_/``) -- which is exactly the grey window the
#: owner was looking at. The same argument vector started *outside* such a job renders
#: the page, and so does the same vector with these flags. The sandbox therefore stays on
#: for every first attempt, and is given up only when a browser has produced no verified
#: window at all -- and then it is written into ``launch.log`` in so many words, because a
#: silently unsandboxed browser is not something to hide. ``--test-type`` is there
#: because Chrome paints an "unsupported command-line flag" infobar over the top of the
#: page for ``--no-sandbox`` alone; it is the same pair the project's own ``app.py`` and
#: its webview launcher already use.
NO_SANDBOX_RETRY = ("--no-sandbox", "--test-type")

#: The windows these attempts produce: ``(sandboxed, label)`` per browser, best first.
SANDBOX_ATTEMPTS = ((True, "с песочницей"), (False, "без песочницы"))


def spawn_browser(
    project: pathlib.Path,
    executable: str,
    url: str,
    profile: pathlib.Path,
    sandbox: bool = True,
):
    """Start one browser in app mode and return its process, or ``None`` if it refused."""

    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        log(project, "профиль окна не создан: %s: %s" % (type(error).__name__, error))
        return None
    vector = browser_vector(executable, url, profile, sandbox=sandbox)
    log(project, "запускаю браузер: " + subprocess.list2cmdline(vector))
    try:
        return subprocess.Popen(
            vector,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=browser_flags(),
        )
    except OSError as error:
        log(
            project,
            "браузер не запустился: %s: %s (%s)"
            % (pathlib.Path(executable).name, type(error).__name__, error),
        )
        return None


def discard_profile(project: pathlib.Path, reason: str) -> None:
    """Throw away the window profile a failed attempt left behind.

    Only ever the launcher's own directory under the repository's ignored ``.forge``:
    the path is checked against its parent before anything is removed.
    """

    root = (REPO / ".forge").resolve()
    target = PROFILE
    try:
        resolved = target.resolve()
        inside = root in resolved.parents
    except OSError:
        return
    if not inside or not target.exists():
        return
    log(project, "удаляю профиль окна (%s): %s" % (reason, target))
    try:
        shutil.rmtree(str(target))
    except OSError as error:
        log(project, "профиль окна не удалён: %s: %s" % (type(error).__name__, error))


def open_window(project: pathlib.Path, url: str) -> dict:
    """Open one verified app-mode window, healing a broken profile on the way.

    The chain is Chrome, then Edge, then the machine's default browser; each browser is
    first tried with its sandbox on, and only a browser that produced no verified window
    at all is retried with :data:`NO_SANDBOX_RETRY` (see the note on that constant: it is
    the one difference that turned the measured grey window into the real page here, and
    it is logged, never silent). A browser that produces no window -- a handoff and an
    exit, a refusal, or the grey page whose title never became a real one -- is closed,
    its profile discarded, and the next attempt made. The last resort still shows the URL,
    because a silent failure is the one outcome this launcher exists to remove.
    """

    failures = []
    first_attempt = True
    for name, executable in browsers():
        for sandbox, label in SANDBOX_ATTEMPTS:
            if not first_attempt:
                # Every retry starts from a clean profile: the attempt before it may have
                # left the directory half-initialised or locked by a dying browser.
                discard_profile(project, "окно не подтвердилось в предыдущей попытке")
            first_attempt = False
            if not sandbox:
                log(
                    project,
                    "пробую %s без песочницы: предыдущая попытка не дала окна "
                    "(песочница браузера не инициализируется в этом процессе)" % name,
                )
            child = spawn_browser(project, executable, url, PROFILE, sandbox=sandbox)
            if child is None:
                failures.append("%s (%s): не запустился" % (name, label))
                continue
            deadline = time.monotonic() + WINDOW_WAIT_SECONDS
            hwnd, title = wait_for_page_window(child, deadline)
            if hwnd:
                log(
                    project,
                    "окно открыто: %s (%s), pid %d, заголовок [%s]"
                    % (name, label, int(child.pid), title),
                )
                return {
                    "ok": True,
                    "browser": name,
                    "pid": int(child.pid),
                    "title": title,
                    "hwnd": hwnd,
                    "profile": str(PROFILE),
                    "sandbox": bool(sandbox),
                }
            code = child.poll()
            failures.append("%s (%s): окна нет (код %s)" % (name, label, code))
            log(project, "%s (%s, pid %s) не дал окна: код %s" % (name, label, int(child.pid), code))
            # A window that exists but never rendered is closed, together with the browser
            # that owns it, so the retry starts from a clean profile rather than a locked one.
            for stale, stale_title in windows_of(int(child.pid)):
                log(
                    project,
                    "закрываю неподтверждённое окно pid %s, заголовок [%s]"
                    % (int(child.pid), stale_title),
                )
                close_window(stale)
            if child.poll() is None:
                try:
                    child.terminate()
                    child.wait(timeout=CLOSE_WAIT_SECONDS)
                except (OSError, subprocess.SubprocessError):
                    pass
    detail = "; ".join(failures) if failures else "ни Chrome, ни Edge не найдены"
    log(project, "ни один браузер не дал окна: " + detail)
    try:
        os.startfile(url)  # noqa: S606 - the Windows shell's own "open with default"
        log(project, "последняя попытка: os.startfile(%s)" % url)
    except OSError as error:
        log(project, "os.startfile не смог открыть окно: %s: %s" % (type(error).__name__, error))
    message_box(
        "Почта АИ — окно не подтвердилось",
        "Не удалось подтвердить окно приложения.\n\n"
        "Адрес приложения: %s\n\n"
        "Попытки: %s\n\n"
        "Лог: %s" % (url, detail, log_path(project)),
    )
    return {"ok": False, "browser": "", "pid": 0, "title": "", "hwnd": 0, "profile": ""}


# ---------------------------------------------------------------------------- state


def read_state() -> dict:
    """What the previous launch recorded: the pid of the window it opened."""

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(data: dict) -> None:
    """Remember this launch's window, so the next double-click can reuse it."""

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ----------------------------------------------------------------------------- main


def run(argv: list) -> int:
    """The whole launch; every outcome is logged and none of them is silent."""

    project = project_from(argv)
    script = str(pathlib.Path(__file__).resolve())
    command_line = subprocess.list2cmdline([pythonw(), script, *[str(part) for part in argv[1:]]])
    if not (project / "app.py").is_file():
        message_box(
            "Почта АИ — папка приложения не найдена",
            "Не найдена папка приложения:\n%s\n\nЛог: %s" % (project, log_path(project)),
        )
        log(project, "папка приложения не найдена: %s" % project)
        return 1

    log(project, "=== запуск из ярлыка: " + command_line)
    port = read_port(project)
    port = ensure_server(project, port)
    if not port:
        message_box(
            "Почта АИ — сервер не запустился",
            "Сервер приложения не ответил на %s.\n\nЛог: %s" % (HEALTH_PATH, log_path(project)),
        )
        return 1

    url = "http://127.0.0.1:%d/" % port
    status, size = page_answers(port)
    log(project, "проверяю страницу: GET %s -> %s, %d байт" % (url, status, size))
    if not 200 <= status < 300:
        message_box(
            "Почта АИ — страница не открылась",
            "Сервер на порту %d отвечает, но страница не отдаётся (код %s).\n\nЛог: %s"
            % (port, status, log_path(project)),
        )
        return 1

    state = read_state()
    previous = int(state.get("pid") or 0)
    if str(state.get("url") or "") != url:
        # A window recorded for another address is not this launch's window.
        previous = 0
    for hwnd, title in windows_of(previous):
        if title_means_a_page(title):
            focus_window(hwnd)
            log(project, "окно уже открыто (pid %d, заголовок [%s]): поднял его, второе не открываю" % (previous, title))
            return 0
        log(project, "закрываю пустое окно прошлого запуска (pid %d, заголовок [%s])" % (previous, title))
        close_window(hwnd)
        discard_profile(project, "окно прошлого запуска было пустым")
        break

    opened = open_window(project, url)
    if not opened.get("ok"):
        return 1
    write_state(
        {
            "pid": int(opened["pid"]),
            "title": str(opened["title"]),
            "url": url,
            "browser": str(opened["browser"]),
            "profile": str(opened["profile"]),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )
    log(project, "=== готово: %s, pid %d, [%s]" % (url, int(opened["pid"]), opened["title"]))
    return 0


def main(argv: list) -> int:
    """Entry point: an unexpected failure is a message box, never a silent exit."""

    try:
        return run(list(argv))
    except Exception as error:  # noqa: BLE001 - the owner must see what happened
        project = project_from(argv)
        log(project, "неожиданная ошибка: %s: %s" % (type(error).__name__, error))
        message_box(
            "Почта АИ — ошибка ярлыка",
            "%s: %s\n\nЛог: %s" % (type(error).__name__, error, log_path(project)),
        )
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
