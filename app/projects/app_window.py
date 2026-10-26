"""Open a launched project's URL in its **own OS window**, not in the Forge tab.

The owner's words: *«Если написано открыть проект, я так понял, что я открою
работающий проект и он у меня развернётся на рабочем столе. Я хочу видеть
результат, а не вечное меню Forge.»* A URL printed somewhere in the Forge screen is
not that result: the window has to be the owner's application, with no address bar
and no Forge interface around it.

This module is only the *window* half of that. It resolves a Chromium browser on
this machine and starts it in app mode:

======================  =====================================================
``chrome_app``          ``chrome.exe --app=<url> --window-size=1280,900
                        --user-data-dir=<repo>/.forge/app-profile/<project-id>/launch-…``
``edge_app``            the same argument vector, with ``msedge.exe``
``default_browser``     ``os.startfile(<url>)`` -- the last resort
======================  =====================================================

Eight rules make that safe and honest:

* **The URL is loopback and comes from the caller.** It is the URL the project's
  own process bound or the preview server serves; :meth:`AppWindowOpener.open`
  refuses anything that is not ``http://127.0.0.1:<port>...``, so this module can
  never be used to start a browser at an arbitrary place.
* **No shell.** An argument vector and ``shell=False``: a URL or a folder name can
  never become a command. The one non-process path is ``os.startfile``, which is
  the Windows shell's own "open with the default program" call, given a checked URL.
* **One profile per launch, never a shared one.** ``--user-data-dir`` points at a
  directory created for *this* launch under ``.forge/app-profile/<project-id>/``.
  Chromium remembers a window's size and position inside its profile, so a stable
  profile looks like the friendlier choice -- and it is the measured cause of the grey
  window the owner was looking at: a profile left behind by an earlier attempt served a
  blank page whose title bar fell back to the bare host (``127.0.0.1_/``), and Chromium's
  own singleton answers a second launch on a profile it already holds with *no window
  at all* (exit ``0``). A fresh profile per launch cannot do either; only the last
  :data:`PROFILE_RETENTION` directories are kept, so it cannot fill a disk.
* **An allow-list, not a search.** Exactly the two Chromium browsers above are
  started by name; there is no arbitrary program and no free-form argument.
* **A plan that dies is not a window.** Each browser is watched for
  :data:`WINDOW_GRACE_SECONDS` after it is started; a browser that exits inside that
  window -- with a failure *or* with ``0`` -- never became the window, so the next plan
  is tried. The answer names the one that really opened, remembers which plans died,
  and a machine where none of them can run gets a refusal instead of a claim.
* **A process is not a window either.** A browser that survives is confirmed by asking
  the OS for a **visible window that belongs to that process**, with a title that is a
  rendered page and not the bare host. That is the measured difference between the
  owner's real «Почта АИ» window and the grey ``127.0.0.1_/`` one.
* **A browser whose own sandbox cannot start is retried once, and told.** Measured on
  the owner's machine (``0x80000003``, ``STATUS_BREAKPOINT``, when a Chromium browser is
  started by a Python process inside its job object): a browser started *by Forge* may
  abort, or may show the grey page, while the very same browser without its sandbox
  renders the application. So each browser gets one second, documented attempt with
  :data:`NO_SANDBOX_FLAGS` -- but only after the sandboxed attempt produced no window,
  never instead of it, and every such downgrade is written into the project's
  ``launch.log`` in so many words. «Настройки» can forbid the retry; then the refusal
  is the honest answer instead of an unsandboxed browser.
* **A refusal is a sentence.** When no browser can be resolved, or the profile
  cannot be created, or the URL is not loopback, :class:`WindowRefusal` says so and
  nothing is started.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import uuid

from app.projects.models import ProjectError
from app.projects.store import is_safe_project_id, repository_root

__all__ = [
    "APP_PROFILE_DIRECTORY",
    "APP_WINDOW_SIZE",
    "AppWindowOpener",
    "NO_SANDBOX_FLAGS",
    "PROFILE_RETENTION",
    "SANDBOX_DOWNGRADE_NOTE",
    "WINDOW_CHROME_APP",
    "WINDOW_DEFAULT_BROWSER",
    "WINDOW_EDGE_APP",
    "WINDOW_GRACE_SECONDS",
    "WINDOW_KINDS",
    "WINDOW_TITLE_WAIT_SECONDS",
    "WindowPlan",
    "WindowRefusal",
    "WindowResult",
    "default_profile_root",
    "detached_flags",
    "is_loopback_url_with_port",
    "window_title_means_a_page",
]

#: A Chromium browser in app mode: a standalone window with no address bar.
WINDOW_CHROME_APP = "chrome_app"
WINDOW_EDGE_APP = "edge_app"
#: The last resort: the machine's own default browser, whatever it is.
WINDOW_DEFAULT_BROWSER = "default_browser"

WINDOW_KINDS: Tuple[str, ...] = (
    WINDOW_CHROME_APP,
    WINDOW_EDGE_APP,
    WINDOW_DEFAULT_BROWSER,
)

#: Where the per-project browser profiles live, under the repository's own
#: (gitignored) ``.forge`` directory.
APP_PROFILE_DIRECTORY = "app-profile"

#: The window the product asks for on a project's launch. A per-launch profile cannot
#: remember a previous window's placement; the size stays fixed instead.
APP_WINDOW_SIZE = "1280,900"

#: How long a started browser is watched before its window is accepted. A browser that
#: is still running after this is the window; one that exits inside it -- with a failure
#: code **or with ``0``**, because it handed the request to an instance already running
#: on that profile -- never became a window, and the next plan is tried. Measured on a
#: confined host: Chrome aborts after roughly 0.5-0.8 s because its own sandbox cannot
#: initialise there.
WINDOW_GRACE_SECONDS = 2.0

#: How long one attempt is watched for a **visible window with a real title** after the
#: process survived the grace period. The measured grey window showed its title bar
#: within a second; a real page a moment later. A browser that never produces such a
#: window is not this launch's window, and the next plan is tried.
WINDOW_TITLE_WAIT_SECONDS = 6.0

#: The two flags of the one documented retry: the same browser, the same URL, a fresh
#: profile, but without the browser's own sandbox (and with the infobar suppressed). The
#: pair is the one the owner's desktop launcher and the project's own ``app.py`` already
#: use; it is only ever reached by a retry, never by the first attempt.
NO_SANDBOX_FLAGS: Tuple[str, ...] = ("--no-sandbox", "--test-type")

#: The exact words the owner reads in ``<project>\launch.log`` when a browser's first,
#: sandboxed attempt produced no window and the same browser is retried without its
#: sandbox. The words are fixed because they are the log line, not a comment.
SANDBOX_DOWNGRADE_NOTE = "песочница недоступна (0x80000003) — повтор без песочницы"

#: How many of a project's per-launch profiles are kept on disk. Each launch gets a
#: fresh directory (see :meth:`AppWindowOpener.profile_for`); the newest
#: :data:`PROFILE_RETENTION` are kept and the older ones are removed.
PROFILE_RETENTION = 2

#: A window whose title bar fell back to the bare host is a page that never rendered --
#: the measured signature of the grey window (``127.0.0.1_/``). It is not a success.
_FAILED_WINDOW_TITLE = re.compile(r"^127\.0\.0\.1(?=$|[\s:/_])")

#: The window class both Chromium browsers use, so a recycled pid cannot be mistaken
#: for our own browser window.
_BROWSER_WINDOW_CLASS = "Chrome_WidgetWin"


class WindowRefusal(ProjectError):
    """No window was opened, and this is why.

    ``failures`` and ``log_lines`` are the same facts :class:`WindowResult` carries on a
    success: every plan that died, and every downgrade the caller has to write into the
    project's own log. They make the refusal explain itself instead of only saying no.
    """

    code = "project_window_refused"

    def __init__(
        self,
        message: str,
        *,
        window: str = "",
        failures: Sequence[str] = (),
        log_lines: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.window = str(window or "")
        self.failures: Tuple[str, ...] = tuple(str(item) for item in failures)
        self.log_lines: Tuple[str, ...] = tuple(str(item) for item in log_lines)


@dataclass(frozen=True)
class WindowPlan:
    """One attempt at one window: the browser, its exact argv, and its own profile.

    ``sandboxed`` is ``True`` for a browser's first attempt and ``False`` only for the
    one documented retry (:data:`NO_SANDBOX_FLAGS`). ``profile`` is a directory created
    for *this* attempt just before it starts -- never one an earlier attempt left behind,
    and never one a plan that is not reached creates at all.
    """

    window: str
    program: str
    vector: Tuple[str, ...]
    profile: Path
    sandboxed: bool = True

    @property
    def downgraded(self) -> bool:
        """True for the one retry without the browser's sandbox."""

        return not self.sandboxed

    @property
    def program_name(self) -> str:
        return Path(self.program).name


@dataclass(frozen=True)
class WindowResult:
    """What was started: which kind of window, the program, its pid and the URL.

    ``failures`` names every plan that died before the window that really opened: one
    ``"<program>: <reason>"`` string per plan. It is what turns "the window opened" into
    "the window opened *after* Chrome exited 0 inside two seconds" in the project's own
    ``launch.log``, so the next person to read the log knows why the second browser was
    used.

    ``sandboxed`` and ``downgraded`` name *which attempt* produced the window: the first,
    sandboxed one, or the documented retry without the sandbox. ``title`` is the window
    title the OS reported, and ``log_lines`` are the sentences the caller writes into the
    project's log (a downgrade is never silent).
    """

    window: str
    program: str
    pid: int
    url: str
    arguments: Tuple[str, ...] = ()
    profile: str = ""
    failures: Tuple[str, ...] = ()
    sandboxed: bool = True
    downgraded: bool = False
    title: str = ""
    log_lines: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        """The public shape. The absolute profile path stays out of it."""

        return {
            "window": self.window,
            "program": self.program,
            "pid": int(self.pid),
            "url": self.url,
            "sandboxed": bool(self.sandboxed),
            "downgraded": bool(self.downgraded),
            "diagnostics": {
                "arguments": list(self.arguments),
                "profile": self.profile,
                "failures": list(self.failures),
                "title": self.title,
                "log_lines": list(self.log_lines),
            },
        }


def default_profile_root() -> Path:
    """``<repository>/.forge/app-profile``: one directory of profiles per project."""

    return repository_root() / ".forge" / APP_PROFILE_DIRECTORY


def window_title_means_a_page(title: object) -> bool:
    """Whether a window title looks like a rendered page, not a failed one.

    A page that never rendered falls back to its address in the title bar; the measured
    example on this machine was ``127.0.0.1_/``. Such a window is the grey window this
    module exists to prevent, so it is never accepted as the project's window.
    """

    text = str(title or "").strip()
    if not text:
        return False
    return _FAILED_WINDOW_TITLE.match(text) is None


def _default_visible_windows(pid: int) -> Optional[List[Tuple[int, str]]]:
    """``[(hwnd, title)]`` for the visible browser windows of one process.

    A thin ``user32`` read and nothing else: no dependency, no shell. ``None`` means this
    host cannot answer the question (no Win32 API, or the call itself failed), and the
    caller then falls back to "a process that survived the grace period is the window"
    instead of pretending no window exists.
    """

    if os.name != "nt":  # pragma: no cover - this product runs on Windows
        return None
    try:
        import ctypes
    except Exception:  # pragma: no cover - defensive
        return None
    found: List[Tuple[int, str]] = []
    try:
        user32 = ctypes.windll.user32
        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _visit(hwnd: Any, _param: Any) -> bool:
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = int(user32.GetWindowTextLengthW(hwnd))
                if length <= 0:
                    return True
                text = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, text, length + 1)
                name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, name, 256)
                if not str(name.value).startswith(_BROWSER_WINDOW_CLASS):
                    return True
                owner = ctypes.c_ulong(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if int(owner.value) != int(pid):
                    return True
                found.append((int(hwnd), str(text.value)))
            except Exception:  # noqa: BLE001 - a window scan must never break a launch
                return True
            return True

        user32.EnumWindows(callback(_visit), None)
    except Exception:  # noqa: BLE001 - an unanswerable host is "cannot check"
        return None
    return found


def detached_flags() -> int:
    """Process-creation flags that detach the window, or ``0`` off Windows."""

    if os.name != "nt":  # pragma: no cover - this product runs on Windows
        return 0
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= int(getattr(subprocess, name, 0) or 0)
    return flags


def _default_which(program: str) -> Optional[str]:
    return shutil.which(program)


def _default_startfile(url: str) -> Any:
    """The Windows "open with the default program" call, if this host has it."""

    opener = getattr(os, "startfile", None)
    if opener is None:  # pragma: no cover - this product runs on Windows
        raise OSError("this platform has no shell file association")
    return opener(url)


def _first_existing(
    candidates: Sequence[object], exists: Callable[[object], bool]
) -> Optional[str]:
    for candidate in candidates:
        if candidate is None:
            continue
        path = Path(str(candidate))
        try:
            if exists(path):
                return str(path)
        except OSError:  # pragma: no cover - defensive
            continue
    return None


def _install_candidates(names: Sequence[str]) -> List[Path]:
    """The usual per-user and machine-wide install locations of one browser."""

    found: List[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found.append(Path(local).joinpath(*names[0].split("/")))
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            found.append(Path(root).joinpath(*names[1].split("/")))
    return found


def is_loopback_url_with_port(url: object) -> bool:
    """True only for a loopback http URL **with an explicit port**.

    A URL without a port (``http://127.0.0.1/``) points at port 80, where no project
    of this product can be listening: opening it shows a blank page and teaches the
    owner nothing. The predicate is public because the launch route checks the same
    fact before it ever reaches the opener, so "never open a portless URL" is enforced
    at both ends.
    """

    if not isinstance(url, str):
        return False
    text = url.strip()
    if not text.startswith("http://127.0.0.1:"):
        return False
    rest = text[len("http://127.0.0.1:") :]
    port = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    try:
        number = int(port)
    except ValueError:
        return False
    return 1 <= number <= 65535


#: Kept as the module's internal name: the check above is what it always meant.
_looks_like_loopback = is_loopback_url_with_port


class AppWindowOpener:
    """Resolve and start the one standalone window a launched project gets.

    ``which``, ``spawn``, ``startfile``, ``exists`` and ``visible_windows`` are injectable
    so a test can prove *which* program would have been started, with which argument
    vector, on a machine whose installed browsers the test names -- without opening a real
    browser window on the machine running the suite. The production defaults are the real
    ones.

    ``window_policy`` is a zero-argument callable answering whether the documented retry
    without the browser's sandbox is allowed. It is the owner's «Настройки» switch, read
    at every launch; an absent (or failing) policy means allowed, because the owner asked
    for the retry. It never turns the *first* attempt into an unsandboxed one.
    """

    def __init__(
        self,
        *,
        which: Optional[Callable[[str], Optional[str]]] = None,
        spawn: Optional[Callable[..., Any]] = None,
        startfile: Optional[Callable[[str], Any]] = None,
        exists: Optional[Callable[[object], bool]] = None,
        visible_windows: Optional[Callable[[int], Optional[Sequence[Tuple[int, str]]]]] = None,
        window_policy: Optional[Callable[[], bool]] = None,
        profile_root: Optional[object] = None,
        grace_seconds: float = WINDOW_GRACE_SECONDS,
        title_wait_seconds: float = WINDOW_TITLE_WAIT_SECONDS,
    ) -> None:
        self._which = which if which is not None else _default_which
        self._spawn = spawn if spawn is not None else subprocess.Popen
        self._startfile = startfile if startfile is not None else _default_startfile
        self._exists = exists if exists is not None else (lambda item: Path(str(item)).is_file())
        self._visible_windows = (
            visible_windows if visible_windows is not None else _default_visible_windows
        )
        self._window_policy = window_policy
        self._profile_root = Path(profile_root) if profile_root is not None else default_profile_root()
        self._grace_seconds = max(0.0, float(grace_seconds))
        self._title_seconds = max(0.0, float(title_wait_seconds))

    # -------------------------------------------------------------- resolution
    def chrome(self) -> Optional[str]:
        """``chrome.exe``: on ``PATH`` or in one of its usual install folders."""

        program = self._which("chrome") or self._which("chrome.exe")
        if program:
            return str(program)
        return _first_existing(
            _install_candidates(
                (
                    "Google/Chrome/Application/chrome.exe",
                    "Google/Chrome/Application/chrome.exe",
                )
            ),
            self._exists,
        )

    def edge(self) -> Optional[str]:
        """``msedge.exe``: on ``PATH`` or in one of its usual install folders."""

        program = self._which("msedge") or self._which("msedge.exe")
        if program:
            return str(program)
        return _first_existing(
            _install_candidates(
                (
                    "Microsoft/Edge/Application/msedge.exe",
                    "Microsoft/Edge/Application/msedge.exe",
                )
            ),
            self._exists,
        )

    # ---------------------------------------------------------------- the plan
    def project_profile_root(self, project_id: object) -> Path:
        """``<root>/<project-id>``: this project's own directory of launch profiles."""

        if not is_safe_project_id(project_id):
            raise WindowRefusal(
                "Идентификатор проекта не годится для профиля окна: "
                "окно приложения не открыто."
            )
        root = self._profile_root / str(project_id)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WindowRefusal(
                "Не удалось создать профиль окна приложения "
                f"({type(exc).__name__}): окно не открыто."
            ) from exc
        return root

    def _next_profile_path(self, project_id: object) -> Path:
        """A unique profile directory of this project, **not created yet**."""

        root = self.project_profile_root(project_id)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        return root / f"launch-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:6]}"

    def profile_for(self, project_id: object) -> Path:
        """A **fresh** browser profile directory for this launch, created here.

        It is fresh on purpose. A profile reused from an earlier attempt is what put the
        grey window on the owner's screen: it served a blank page whose title bar fell
        back to the bare host, and Chromium's singleton answers a launch on a profile it
        already holds with no window at all and exit ``0`` -- a claim of a window that is
        not there. A new directory under ``<root>/<project-id>/launch-…`` cannot do
        either, so two projects never share one and no launch inherits the last one's
        state. Only :data:`PROFILE_RETENTION` directories are kept per project.
        """

        root = self.project_profile_root(project_id)
        # Prune *before* creating, and leave room for the new one: the profile this
        # launch is about to use must never be the one removed by its own retention rule.
        self._prune_profiles(root, max(1, PROFILE_RETENTION - 1))
        target = self._next_profile_path(project_id)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WindowRefusal(
                "Не удалось создать профиль окна приложения "
                f"({type(exc).__name__}): окно не открыто."
            ) from exc
        return target

    @staticmethod
    def _prune_profiles(root: Path, keep: int = PROFILE_RETENTION) -> None:
        """Keep only the newest ``keep`` launch profiles of one project.

        Only directories this method itself recognises -- a ``launch-`` prefix directly
        inside the project's own profile root -- are ever removed, and a failure to
        remove one changes nothing: a disk is cheaper than a broken launch.
        """

        try:
            candidates = [
                item
                for item in root.iterdir()
                if item.is_dir() and item.name.startswith("launch-")
            ]
        except OSError:
            return
        if len(candidates) <= max(1, int(keep)):
            return
        try:
            ordered = sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)
        except OSError:
            return
        for stale in ordered[max(1, int(keep)) :]:
            shutil.rmtree(str(stale), ignore_errors=True)

    def plans(
        self,
        project_id: object,
        url: object,
        *,
        allow_downgrade: bool = True,
    ) -> Tuple[WindowPlan, ...]:
        """Every standalone-window attempt this host allows, in the order to try them.

        Chrome first, Edge second; **each browser gets its sandboxed attempt first**
        (:data:`NO_SANDBOX_FLAGS` is never part of it), and -- unless the owner forbade it
        -- one retry of that same browser with the documented relaxation. A plan is a
        :class:`WindowPlan`, whose argument vector is fixed here, so a caller cannot add a
        word to it and a test can assert it. Each plan gets its own profile directory,
        created only when its attempt really starts: Chrome and Edge do not write into one
        directory, and a fresh one per attempt is what keeps a stale profile from deciding
        what the window shows. The list is empty when no Chromium browser is installed:
        the machine's own default browser is then the last resort, and :meth:`open`
        records which plan really happened.
        """

        if not _looks_like_loopback(url):
            raise WindowRefusal(
                "Открыть можно только адрес проекта на этом компьютере "
                "(127.0.0.1): окно не открыто."
            )
        address = str(url)
        plans: List[WindowPlan] = []
        for window, program in (
            (WINDOW_CHROME_APP, self.chrome()),
            (WINDOW_EDGE_APP, self.edge()),
        ):
            if not program:
                continue
            for sandboxed in ((True, False) if allow_downgrade else (True,)):
                profile = self._next_profile_path(project_id)
                vector = (
                    program,
                    f"--app={address}",
                    f"--window-size={APP_WINDOW_SIZE}",
                    f"--user-data-dir={profile}",
                    *(() if sandboxed else NO_SANDBOX_FLAGS),
                )
                plans.append(
                    WindowPlan(
                        window=window,
                        program=program,
                        vector=vector,
                        profile=profile,
                        sandboxed=sandboxed,
                    )
                )
        return tuple(plans)

    # ------------------------------------------------------- the window itself
    @staticmethod
    def _became_a_window(child: Any, grace: float) -> Tuple[bool, Optional[int]]:
        """``(usable, exit_code)`` for a just-spawned browser, without a window scan.

        A browser that is still running after the grace period is the window. One that
        exits inside it never became *this* launch's window, whatever its code: a
        Chromium singleton answers a launch on a profile it already holds with exit
        ``0`` and no window of its own -- which is exactly how a launch reported a
        window while the owner was looking at a stale one -- and a refusal exits with a
        failure code (measured on a confined host: Chrome aborts with
        ``STATUS_BREAKPOINT`` because its own sandbox cannot initialise there). Both make
        the caller move on to the next plan instead of claiming a window that is not
        there.
        """

        poll = getattr(child, "poll", None)
        if not callable(poll):
            # An injected child that cannot be asked is treated as the window it stands
            # for; only the real ``Popen`` is watched.
            return True, None
        deadline = time.monotonic() + max(0.0, float(grace))
        while True:
            code = poll()
            if code is not None:
                return False, int(code)
            if time.monotonic() >= deadline:
                return True, None
            time.sleep(0.025)

    def _visible_titles(self, pid: int) -> Optional[List[Tuple[int, str]]]:
        """The process's visible browser windows, or ``None`` when the host cannot say."""

        if self._visible_windows is None:  # pragma: no cover - a non-Windows host
            return None
        try:
            found = self._visible_windows(int(pid))
        except Exception:  # noqa: BLE001 - a scan must never break a launch
            return None
        if found is None:
            return None
        windows: List[Tuple[int, str]] = []
        for item in found:
            try:
                hwnd, title = item
            except (TypeError, ValueError):
                continue
            windows.append((int(hwnd), str(title or "")))
        return windows

    def _observe(self, child: Any, pid: int) -> Tuple[str, Optional[int], int, str]:
        """What one attempt really produced: ``window``, ``exit`` or ``none``.

        The return value is ``(kind, exit_code, hwnd, title)``. A window is only ever
        reported when the OS names a **visible window of that process whose title is a
        rendered page**; a process that survived but never showed such a window is
        ``none``, and the caller tries the next plan. When the host cannot answer the
        window question (no Win32 scan), the older rule stands: a process that survived
        the grace period is the window. A child that cannot be asked (an injected test
        double) is the window it stands for.
        """

        poll = getattr(child, "poll", None)
        if not callable(poll):
            return "window", None, 0, ""
        titles = self._visible_titles(pid)
        if titles is None:
            usable, code = self._became_a_window(child, self._grace_seconds)
            if usable:
                return "window", None, 0, ""
            return "exit", code, 0, ""
        deadline = time.monotonic() + self._title_seconds
        seen = ""
        while True:
            for hwnd, title in titles or ():
                if window_title_means_a_page(title):
                    return "window", None, int(hwnd), title
                if not seen:
                    seen = title
            code = poll()
            if code is not None:
                # One last look: the window may have existed a moment before the exit.
                time.sleep(0.05)
                for hwnd, title in self._visible_titles(pid) or ():
                    if window_title_means_a_page(title):
                        return "window", None, int(hwnd), title
                return "exit", int(code), 0, seen
            if time.monotonic() >= deadline:
                return "none", None, 0, seen
            time.sleep(0.025)
            titles = self._visible_titles(pid)

    @staticmethod
    def _stop(child: Any) -> None:
        """End an attempt that did not become the window.

        A grey window belongs to the browser that showed it; leaving that process alive
        after its plan failed is what would leave the owner looking at two windows. The
        process is this launch's own child, and a termination that fails is not an error.
        """

        terminate = getattr(child, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except (OSError, ValueError):  # pragma: no cover - a dying child
                pass

    def _downgrade_allowed(self) -> bool:
        """The owner's «Настройки» switch, read now; absent or broken means allowed."""

        if self._window_policy is None:
            return True
        try:
            return bool(self._window_policy())
        except Exception:  # noqa: BLE001 - a broken setting never withholds the window
            return True

    # ------------------------------------------------------------- the action
    def open(self, project_id: object, url: object) -> WindowResult:
        """Start the first usable window, or raise :class:`WindowRefusal`.

        The fallback chain is the specification and it is applied **at runtime**: a
        Chromium browser that cannot start on this host -- or that hands the request to
        an instance it does not own and exits, or that survives without a rendered window
        -- is not a window. That browser is then retried **once** without its sandbox
        (:data:`NO_SANDBOX_FLAGS`, the documented relaxation, written into ``log_lines``
        and from there into the project's ``launch.log``), and only after that the next
        browser is tried; the machine's default browser is the last resort. Nothing is
        claimed that did not happen: ``window`` names the one that really opened,
        ``sandboxed``/``downgraded`` name which attempt did, and ``failures`` names every
        plan that died before it.
        """

        address = str(url)
        allow_downgrade = self._downgrade_allowed()
        failures: List[str] = []
        notes: List[str] = []
        chain = self.plans(project_id, address, allow_downgrade=allow_downgrade)
        root = self.project_profile_root(project_id)
        for plan in chain:
            if plan.downgraded:
                notes.append(
                    f"{SANDBOX_DOWNGRADE_NOTE}: {plan.program_name} "
                    "(первая попытка не дала окна)"
                )
            try:
                plan.profile.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                failures.append(
                    f"{plan.program_name}: профиль окна не создан ({type(exc).__name__})"
                )
                continue
            try:
                child = self._spawn(
                    list(plan.vector),
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    # Detached from this server: closing Forge must not close the
                    # owner's application window, and the window must not hold the
                    # API's console.
                    creationflags=detached_flags(),
                )
            except OSError as exc:
                failures.append(f"{plan.program_name}: {type(exc).__name__}")
                continue
            pid = int(getattr(child, "pid", 0) or 0)
            kind, code, _hwnd, title = self._observe(child, pid)
            if kind == "window":
                self._prune_profiles(root, PROFILE_RETENTION)
                return WindowResult(
                    window=plan.window,
                    program=plan.program_name,
                    pid=pid,
                    url=address,
                    arguments=tuple(str(part) for part in plan.vector[1:]),
                    profile=str(plan.profile),
                    failures=tuple(failures),
                    sandboxed=plan.sandboxed,
                    downgraded=plan.downgraded,
                    title=title,
                    log_lines=tuple(notes),
                )
            # A plan that produced no window is ended before the next attempt: a grey
            # window must not survive the plan that failed to render the page.
            self._stop(child)
            if kind == "exit":
                # Exit 0 inside the grace period is a handoff, not a window: Chromium's
                # singleton does that on a profile it already holds. It is recorded as a
                # dead plan, and the next attempt is tried.
                failures.append(
                    f"{plan.program_name}: exit {code} — окно не подтвердилось"
                )
            else:
                shown = f", заголовок «{title}»" if title else ""
                failures.append(
                    f"{plan.program_name}: окно не подтвердилось за "
                    f"{self._title_seconds:g} с{shown}"
                )
            if not allow_downgrade and plan.sandboxed:
                failures.append(
                    f"{plan.program_name}: повтор без песочницы запрещён в настройках"
                )
        # No Chromium browser can run here (or none is installed): the machine's own
        # default browser is the honest last resort. It cannot give a window without an
        # address bar, and the answer says so.
        try:
            self._startfile(address)
        except OSError as exc:
            self._prune_profiles(root, PROFILE_RETENTION)
            detail = "; ".join(failures) if failures else "no Chromium browser is installed"
            raise WindowRefusal(
                "Не удалось открыть окно приложения "
                f"({detail}); браузер по умолчанию тоже не открылся "
                f"({type(exc).__name__}).",
                failures=tuple(failures),
                log_lines=tuple(notes),
            ) from exc
        self._prune_profiles(root, PROFILE_RETENTION)
        return WindowResult(
            window=WINDOW_DEFAULT_BROWSER,
            program="",
            pid=0,
            url=address,
            arguments=(),
            profile="",
            failures=tuple(failures),
            # No Chromium plan produced this window, so no sandbox decision was made
            # here: claiming either attempt would be a fact this module did not observe.
            sandboxed=False,
            downgraded=False,
            title="",
            log_lines=tuple(notes),
        )
