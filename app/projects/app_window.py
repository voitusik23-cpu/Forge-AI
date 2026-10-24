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
                        --user-data-dir=<repo>/.forge/app-profile/<project-id>``
``edge_app``            the same argument vector, with ``msedge.exe``
``default_browser``     ``os.startfile(<url>)`` -- the last resort
======================  =====================================================

Five rules make that safe and honest:

* **The URL is loopback and comes from the caller.** It is the URL the project's
  own process bound or the preview server serves; :meth:`AppWindowOpener.open`
  refuses anything that is not ``http://127.0.0.1:<port>...``, so this module can
  never be used to start a browser at an arbitrary place.
* **No shell.** An argument vector and ``shell=False``: a URL or a folder name can
  never become a command. The one non-process path is ``os.startfile``, which is
  the Windows shell's own "open with the default program" call, given a checked URL.
* **One profile per project.** ``--user-data-dir`` points at
  ``.forge/app-profile/<project-id>``, created here. Chromium remembers a window's
  size and position inside its profile, so a second launch of the same project
  restores the same window instead of a fresh default one -- and two projects never
  share a profile.
* **An allow-list, not a search.** Exactly the two Chromium browsers above are
  started by name; there is no arbitrary program and no free-form argument.
* **A plan that dies is not a window.** Each browser is watched briefly after it is
  started; a browser that exits with a failure never became the window, so the next
  plan is tried -- Chrome, then Edge, then the machine's default browser. The answer
  names the one that really opened, and a machine where none of them can run gets a
  refusal instead of a claim.
* **A refusal is a sentence.** When no browser can be resolved, or the profile
  cannot be created, or the URL is not loopback, :class:`WindowRefusal` says so and
  nothing is started.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.projects.models import ProjectError
from app.projects.store import is_safe_project_id, repository_root

__all__ = [
    "APP_PROFILE_DIRECTORY",
    "APP_WINDOW_SIZE",
    "AppWindowOpener",
    "WINDOW_CHROME_APP",
    "WINDOW_DEFAULT_BROWSER",
    "WINDOW_EDGE_APP",
    "WINDOW_GRACE_SECONDS",
    "WINDOW_KINDS",
    "WindowRefusal",
    "WindowResult",
    "default_profile_root",
    "detached_flags",
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

#: The window the product asks for on a project's first launch. After that the
#: project's own profile remembers where the owner put the window.
APP_WINDOW_SIZE = "1280,900"

#: How long a started browser is watched before its window is accepted. A browser
#: that is still running after this is the window; one that exits non-zero inside it
#: never became one, and the next plan is tried. Measured on a confined host: Chrome
#: aborts after roughly 0.5-0.8 s because its own sandbox cannot initialise there.
WINDOW_GRACE_SECONDS = 1.0


class WindowRefusal(ProjectError):
    """No window was opened, and this is why."""

    code = "project_window_refused"

    def __init__(self, message: str, *, window: str = "") -> None:
        super().__init__(message)
        self.window = str(window or "")


@dataclass(frozen=True)
class WindowResult:
    """What was started: which kind of window, the program, its pid and the URL."""

    window: str
    program: str
    pid: int
    url: str
    arguments: Tuple[str, ...] = ()
    profile: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """The public shape. The absolute profile path stays out of it."""

        return {
            "window": self.window,
            "program": self.program,
            "pid": int(self.pid),
            "url": self.url,
            "diagnostics": {
                "arguments": list(self.arguments),
                "profile": self.profile,
            },
        }


def default_profile_root() -> Path:
    """``<repository>/.forge/app-profile``: one profile directory per project."""

    return repository_root() / ".forge" / APP_PROFILE_DIRECTORY


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


def _looks_like_loopback(url: object) -> bool:
    """True only for a loopback http URL: the one thing this module may open."""

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


class AppWindowOpener:
    """Resolve and start the one standalone window a launched project gets.

    ``which``, ``spawn``, ``startfile`` and ``exists`` are injectable so a test can
    prove *which* program would have been started, with which argument vector, on a
    machine whose installed browsers the test names -- without opening a real browser
    window on the machine running the suite. The production defaults are the real
    ones.
    """

    def __init__(
        self,
        *,
        which: Optional[Callable[[str], Optional[str]]] = None,
        spawn: Optional[Callable[..., Any]] = None,
        startfile: Optional[Callable[[str], Any]] = None,
        exists: Optional[Callable[[object], bool]] = None,
        profile_root: Optional[object] = None,
        grace_seconds: float = WINDOW_GRACE_SECONDS,
    ) -> None:
        self._which = which if which is not None else _default_which
        self._spawn = spawn if spawn is not None else subprocess.Popen
        self._startfile = startfile if startfile is not None else _default_startfile
        self._exists = exists if exists is not None else (lambda item: Path(str(item)).is_file())
        self._profile_root = Path(profile_root) if profile_root is not None else default_profile_root()
        self._grace_seconds = max(0.0, float(grace_seconds))

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
    def profile_for(self, project_id: object) -> Path:
        """The project's own browser profile directory, created if it is absent.

        It is the profile that carries the window's remembered size and position, so
        it is per project and never shared: two projects must not inherit each
        other's window placement.
        """

        if not is_safe_project_id(project_id):
            raise WindowRefusal(
                "Идентификатор проекта не годится для профиля окна: "
                "окно приложения не открыто."
            )
        target = self._profile_root / str(project_id)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WindowRefusal(
                "Не удалось создать профиль окна приложения "
                f"({type(exc).__name__}): окно не открыто."
            ) from exc
        return target

    def plans(
        self, project_id: object, url: object
    ) -> Tuple[Tuple[str, str, Tuple[str, ...], Path], ...]:
        """Every standalone-window launch this host allows, best first.

        Chrome first, Edge second. A plan is ``(window, program, argv, profile)`` and
        the *exact* argument vector is fixed here, so a caller cannot add a word to it
        and a test can assert it. The list is empty when no Chromium browser is
        installed: the machine's own default browser is then the last resort, and
        :meth:`open` records which of the three really happened.
        """

        if not _looks_like_loopback(url):
            raise WindowRefusal(
                "Открыть можно только адрес проекта на этом компьютере "
                "(127.0.0.1): окно не открыто."
            )
        address = str(url)
        plans = []
        for window, program in (
            (WINDOW_CHROME_APP, self.chrome()),
            (WINDOW_EDGE_APP, self.edge()),
        ):
            if not program:
                continue
            profile = self.profile_for(project_id)
            vector = (
                program,
                f"--app={address}",
                f"--window-size={APP_WINDOW_SIZE}",
                f"--user-data-dir={profile}",
            )
            plans.append((window, program, vector, profile))
        return tuple(plans)

    @staticmethod
    def _survives(child: Any, grace: float) -> Tuple[bool, Optional[int]]:
        """``(usable, exit_code)`` for a just-spawned browser.

        A browser that is still running after the grace period is the window. One that
        exited with ``0`` handed the request to an instance already running on this
        profile -- Chrome's singleton does that -- so a window was asked for and that
        is a success too. A browser that exited with any other code never became a
        window at all (measured on a confined host: Chrome aborts with
        ``STATUS_BREAKPOINT`` because its own sandbox cannot initialise), and the
        caller must move on to the next plan instead of reporting a window that is not
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
                return int(code) == 0, int(code)
            if time.monotonic() >= deadline:
                return True, None
            time.sleep(0.025)

    # ------------------------------------------------------------- the action
    def open(self, project_id: object, url: object) -> WindowResult:
        """Start the first usable window, or raise :class:`WindowRefusal`.

        The fallback chain is the specification and it is applied **at runtime**: a
        Chromium browser that cannot start on this host is not a window, so the next
        one is tried, and the machine's default browser is the last resort. Nothing is
        claimed that did not happen: ``window`` names the one that really opened.
        """

        address = str(url)
        failures: List[str] = []
        for window, program, vector, profile in self.plans(project_id, address):
            try:
                child = self._spawn(
                    list(vector),
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
                failures.append(f"{Path(program).name}: {type(exc).__name__}")
                continue
            pid = int(getattr(child, "pid", 0) or 0)
            usable, code = self._survives(child, self._grace_seconds)
            if usable:
                return WindowResult(
                    window=window,
                    program=Path(program).name,
                    pid=pid,
                    url=address,
                    arguments=tuple(str(part) for part in vector[1:]),
                    profile=str(profile),
                )
            failures.append(f"{Path(program).name}: exit {code}")
        # No Chromium browser can run here (or none is installed): the machine's own
        # default browser is the honest last resort. It cannot give a window without an
        # address bar, and the answer says so.
        try:
            self._startfile(address)
        except OSError as exc:
            detail = "; ".join(failures) if failures else "no Chromium browser is installed"
            raise WindowRefusal(
                "Не удалось открыть окно приложения "
                f"({detail}); браузер по умолчанию тоже не открылся "
                f"({type(exc).__name__})."
            ) from exc
        return WindowResult(
            window=WINDOW_DEFAULT_BROWSER,
            program="",
            pid=0,
            url=address,
            arguments=(),
            profile="",
        )
