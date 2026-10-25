"""Start, supervise and cleanly stop the Forge AI Command Center.

Design notes, each answering a requirement:

* **No console window.** The launcher itself is started through ``pythonw.exe`` and it
  starts both servers through ``pythonw.exe`` as well, so nothing flashes on screen.
* **No duplicate servers.** A port that already answers is reused, not replaced, and it is then
  *not* stopped on exit -- the launcher only stops what it provably owns: the processes it started
  itself, plus servers a dead launcher of the *same* workspace left behind, adopted only while no
  other launcher of that workspace is alive (see "instance ownership" below).
* **Wait for readiness.** The browser window (Chrome, or Edge when Chrome is absent) opens only after
  ``/api/status`` and the shell page both answer, so the first screen is never an error page.
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
import datetime
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import NamedTuple

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

#: The two servers the window needs, each with everything required to bring it back:
#: ``(name, readiness url, launch arguments, log file, ownership role)``. Keeping them in
#: one tuple is what stops a watchdog from covering only one of them -- which is exactly
#: how 8011 survived and 8020 stayed dead on 2026-10-25.
SERVER_ROLES = (
    ("core-api", API_URL, ["-m", "app.api.main"], "api.log", "api"),
    (
        "shell-server",
        UI_BASE,
        ["tools/ui_preview_server.py", "--port", str(UI_PORT)],
        "ui.log",
        "ui",
    ),
)

#: How often the two ports are polled while the window is open.
SERVER_WATCH_SECONDS = 5.0

#: How many consecutive missed answers mean "this port is dead", not "this moment was busy".
SERVER_WATCH_MISSES = 2


class Browser(NamedTuple):
    """A browser executable the application window may be opened with."""

    name: str
    executable: pathlib.Path


#: The window is opened with Chrome: that is the browser the owner runs the application in.
CHROME = pathlib.Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
#: Edge stays as the fallback, because it ships with Windows and needs no installation.
EDGE = pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def chrome_candidates() -> list:
    """Every place Chrome installs, in lookup order: both per-machine roots, then the per-user one."""

    found = [
        CHROME,
        pathlib.Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found.append(pathlib.Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return found


def browser_catalogue() -> list:
    """``(name, candidate executables)`` in preference order: Chrome first, Edge second."""

    return [
        ("chrome", chrome_candidates()),
        (
            "edge",
            [
                EDGE,
                pathlib.Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            ],
        ),
    ]


def resolve_browser(available=None):
    """The first installed browser of :func:`browser_catalogue`, or ``None`` when none is installed.

    Chrome is preferred and Edge is the fallback, so the window still opens on a machine without
    Chrome. ``available`` is the existence test; it is injectable only so tests need no real
    installation.
    """

    accept = available if available is not None else pathlib.Path.is_file
    for name, candidates in browser_catalogue():
        for candidate in candidates:
            if accept(candidate):
                return Browser(name, candidate)
    return None


def window_profile(repo: pathlib.Path, browser: Browser) -> pathlib.Path:
    """The window profile directory: one per browser.

    A Chromium profile belongs to the browser that created it. The single ``webview-profile`` this
    launcher used for everything had been written by Edge, and the window Chrome opened on it was an
    empty gray page instead of the application -- the failure the owner reported. Giving each browser
    its own directory is the smallest way to make that impossible: Chrome never inherits Edge's
    profile, and Edge never inherits Chrome's.
    """

    return repo / ".forge" / f"webview-profile-{browser.name}"


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
    # ``FORGE_ENV`` is NOT forced here. It used to be set to "test", which made the composition adopt
    # the TEST-runtime invariant (``runtime.bootstrap._offline_only_settings``): every live provider is
    # dropped and only the offline one survives, so no real model could ever answer through the desktop
    # app. The environment's own value decides instead -- unset means development, which keeps the
    # configured providers reachable -- and a caller that wants a test runtime still sets it explicitly.
    environment.update(
        {
            "FORGE_API_HOST": "127.0.0.1",
            "FORGE_API_PORT": str(API_PORT),
            "PYTHONPATH": str(REPO),
            # An explicit, writable run store. The default lives under the user profile,
            # which some platforms (and this sandbox) refuse to create; the store honours
            # this variable, so persistence works either way and the location is visible.
            "FORGE_RUN_STORE_ROOT": str(REPO / ".forge" / "run-store"),
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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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


def window_title(handle: int) -> str:
    """The current title of a window handle."""

    import ctypes

    buffer = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(handle, buffer, 512)
    return buffer.value


#: Characters a window title can carry invisibly. Measured on this machine: Edge's own title is
#: "... Microsoft\u200b Edge" -- with U+200B between the two words -- so a plain substring test for
#: "microsoft edge" never matched and the owner's Edge window was adopted as the application window.
INVISIBLE_TITLE_CHARACTERS = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))


def window_is_another_browser(handle: int) -> bool:
    """``True`` for an ordinary browser window, which carries the browser name in its title.

    The launcher's own window is an ``--app`` window and has no such suffix (measured:
    'FORGE AI — Command Center'), so this is how its window is told apart from a normal Chrome or Edge
    window that merely happens to show ``127.0.0.1`` -- for example the owner's own browser window with
    the application URL in a tab. Adopting such a window would absorb the launch and wait for a window
    the launcher does not own.
    """

    title = window_title(handle).translate(INVISIBLE_TITLE_CHARACTERS).lower()
    return "google chrome" in title or "microsoft edge" in title


WINDOW_PROFILE_FALLBACKS = 4


def window_profile_candidates(primary: pathlib.Path, fallbacks: int = WINDOW_PROFILE_FALLBACKS) -> list:
    """The primary profile first, then a bounded set of separate profiles.

    A browser holding a profile makes another launch on the same profile exit immediately. Retrying
    on a *new* directory solves that without touching the held profile, and the bound keeps repeated
    conflicts from creating profiles without end.
    """

    return [primary] + [primary.with_name(f"{primary.name}-alt{index}") for index in range(1, fallbacks + 1)]


def process_created_at(pid, refresh: bool = False):
    """The creation time of a live process as a comparable string, or ``None`` if it cannot be known.

    ``None`` never means "the process is gone": a failed query, a timeout, an unavailable CIM provider and
    a genuinely absent process are all indistinguishable from here, and the caller treats ``None`` as
    "unknown", never as proof that a claim can be taken over. The value is returned verbatim from the same
    provider in the same format every time, so two readings of one process compare equal and a recycled pid
    with a different creation time does not.

    ``refresh`` reads the provider again instead of answering from the cache. A decision that terminates
    a process needs it: the cached value belongs to whatever process held the pid when it was first read,
    and a recycled pid must not inherit that remembered identity.
    """

    cache = process_created_at.__dict__.setdefault("_cache", {})
    if refresh:
        cache.pop(int(pid), None)
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; "
                "if ($p) { $p.CreationDate.ToString('o') }",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
    except Exception:  # noqa: BLE001 - an unavailable query is unknown, not absent
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    # One process has exactly one creation time, so its identity must read the same on every call. The
    # lookup is a PowerShell/CIM round trip that can fail or time out, and two calls made inside a single
    # claim could otherwise disagree - one returning the timestamp and the next returning ``None`` - which
    # let a process fail to recognise the marker it had just written and steal its own profile. Caching the
    # first successful reading removes that disagreement; until a reading succeeds every caller sees None,
    # which is the conservative answer.
    if value:
        cache[int(pid)] = value
    return cache.get(int(pid))


# ------------------------------------------------------------------ instance ownership
#
# Closing the last window stops only the servers this launcher instance can still *prove* it owns.
# The proof is a (pid, creation time) pair, because a pid alone is recycled by the operating system and
# a new process behind an old pid must never be mistaken for a server of ours.
#
# The record is kept per workspace in ``.forge/instance-<id>.json``. It is written atomically, and every
# read-modify-write is serialised by ``.forge/instance-<id>.lock`` so two launchers of one workspace
# cannot both conclude that they own the same server. A server started by a launcher that is now gone is
# adopted by the next launcher of that workspace -- that is how orphans get cleaned up -- but only while
# no other launcher of that workspace is alive and only while the identity still matches.

OWNED_ROLES = {"core-api": "api", "shell-server": "ui"}
#: The ``owner`` values of an entry that only *records an observation*. Such an entry says "a server
#: answers here and nobody proved it is ours"; it never grants a stop by itself.
OBSERVATION_OWNERS = frozenset({"reused", "unknown"})
INSTANCE_LOCK_TIMEOUT_SECONDS = 2.0
INSTANCE_LOCK_STALE_SECONDS = 30.0


class Ownership(NamedTuple):
    """The answer for one already-answering port, with the reason that belongs in the journal."""

    adopt: bool
    reason: str


def workspace_instance_id(workspace=None) -> str:
    """The stable id of one workspace, so each checkout has exactly one instance record."""

    root = REPO if workspace is None else workspace
    resolved = str(pathlib.Path(root).resolve()).lower()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def instance_paths(workspace=None) -> tuple[pathlib.Path, pathlib.Path]:
    """``(state file, lock file)`` of this workspace's launcher instance record."""

    root = pathlib.Path(REPO if workspace is None else workspace)
    forge = root / ".forge"
    ident = workspace_instance_id(root)
    return forge / f"instance-{ident}.json", forge / f"instance-{ident}.lock"


def launcher_created_at():
    """This launcher's own creation time, read once, or ``None`` when it cannot be known."""

    remembered = launcher_created_at.__dict__
    if remembered.get("value"):
        return remembered["value"]
    value = process_created_at(os.getpid(), refresh=True)
    if value:
        remembered["value"] = value
    return value


def pid_exists(pid) -> bool:
    """Whether a process with this pid exists -- or cannot be ruled out. Never "it is gone".

    A pid that is free is refused by ``OpenProcess`` with ``ERROR_INVALID_PARAMETER``; every other
    refusal means the process is there but cannot be inspected, which is not proof that it is gone.
    The distinction matters: this answer decides whether an orphan may be adopted at all.
    """

    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        ERROR_INVALID_PARAMETER = 87
        WAIT_TIMEOUT = 0x00000102
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, number
        )
        if not handle:
            return ctypes.get_last_error() != ERROR_INVALID_PARAMETER
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - an unavailable probe is unknown, and unknown never owns anything
        return True


def process_alive(pid, created, refresh: bool = False) -> bool:
    """Whether *this exact process* is still running: the same pid and the same creation time.

    ``created`` is the only thing that separates a process from a later one that was given its pid, so
    an unknown creation time is never accepted as proof. ``refresh`` asks the provider again, which is
    what a decision to terminate something needs.
    """

    if not isinstance(created, str) or not created:
        return False
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    if not pid_exists(number):
        return False
    return process_created_at(number, refresh=refresh) == created


def same_workspace(left, right) -> bool:
    """Whether two paths name the same directory, Windows casing included."""

    def normalized(value):
        try:
            return os.path.normcase(str(pathlib.Path(str(value)).resolve()))
        except (OSError, ValueError):
            return None

    one, two = normalized(left), normalized(right)
    return one is not None and one == two


def current_launcher_identity() -> tuple:
    """This launcher's own ``(pid, creation time)``: the whole identity a record must carry to be ``self``."""

    return os.getpid(), launcher_created_at()


def recorded_launcher_status(state, me=None, me_created=None) -> str:
    """How the launcher in ``state`` stands: ``self``, ``gone``, ``live``, ``unknown`` or ``none``.

    ``self`` requires the *whole* identity: the recorded pid and the recorded creation time must both be
    this process's. A pid alone is recycled by the operating system, so a record naming this pid with a
    different creation time describes a *different* launcher, which may still be running -- it is asked
    about through :func:`process_alive` instead of being mistaken for this one. ``me_created`` unknown
    means this launcher cannot prove it is ``self``, which only ever makes the answer more cautious.
    """

    if me is None or me_created is None:
        own_pid, own_created = current_launcher_identity()
        me = own_pid if me is None else me
        me_created = own_created if me_created is None else me_created
    launcher = state.get("launcher") if isinstance(state, dict) else None
    if not isinstance(launcher, dict):
        return "none"
    try:
        pid = int(launcher.get("pid"))
        mine = int(me)
    except (TypeError, ValueError):
        return "none"
    if pid <= 0:
        return "none"
    if (
        pid == mine
        and isinstance(me_created, str)
        and me_created
        and launcher.get("created") == me_created
    ):
        return "self"
    if process_alive(pid, launcher.get("created")):
        return "live"
    if pid_exists(pid):
        # The pid is taken, but the process cannot be proven to be the recorded launcher (its creation
        # time is unknown or differs). That is not proof the launcher is gone.
        return "unknown"
    return "gone"


def decide_ownership(state, role, pid, created, workspace=None, me=None, me_created=None) -> Ownership:
    """Whether this launcher may take over the server answering on a port, and why.

    Requires the caller's *whole* identity in ``me``/``me_created`` (pid **and** creation time); a record
    that carries both is this launcher's own record and is treated as ``self``, never as a foreign launcher
    whose death has to be proven. It compares the record with the identity read from the port and asks
    :func:`process_alive` about the recorded launcher. It adopts only when every proof lines up -- the
    record is this workspace's, it names this exact process (pid *and* creation time), and no other
    launcher of this workspace is alive to stop it itself. Anything unknown is refused.

    An entry written by ``record_observation`` (``owner`` is ``reused`` or ``unknown``) is only an
    observation: it may be adopted by a later launcher while the launcher slot is empty, holds a process
    that is provably gone, or holds this very launcher, and only while the pid and creation time still
    match the live listener. An empty launcher slot is never permission by itself.
    """

    if not isinstance(state, dict):
        return Ownership(False, "no instance record for this workspace; reused without ownership")
    if not same_workspace(state.get("workspace"), REPO if workspace is None else workspace):
        return Ownership(False, "the instance record belongs to a different workspace; reused without ownership")
    try:
        number = int(pid)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        return Ownership(False, "the process listening on the port could not be identified; reused without ownership")
    if not created:
        return Ownership(False, "its creation time is unknown, so ownership cannot be proven; reused without ownership")
    entry = state.get(role)
    if not isinstance(entry, dict):
        return Ownership(False, "the record names no owner for this server; reused without ownership")
    observed = entry.get("owner") in OBSERVATION_OWNERS and not entry.get("adopted")
    status = recorded_launcher_status(state, me=me, me_created=me_created)
    if status == "live":
        return Ownership(False, "owned by a live launcher, will not be stopped")
    # "self" is adoptable for an observation as well: the recorded launcher is this very process, so no
    # *other* launcher is alive to stop that server and nobody can lose an ownership they hold.
    allowed = ("gone", "none", "self") if observed else ("gone", "self")
    if status not in allowed:
        return Ownership(
            False,
            "the recorded launcher cannot be proven gone, so ownership is unknown; reused without ownership",
        )
    try:
        recorded_pid = int(entry.get("pid"))
    except (TypeError, ValueError):
        recorded_pid = 0
    if recorded_pid != number:
        return Ownership(False, "the recorded owner is a different process; reused without ownership")
    if not entry.get("created") or entry.get("created") != created:
        return Ownership(False, "the recorded owner has a different creation time; reused without ownership")
    if observed:
        if status == "self":
            return Ownership(True, "adopted from a recorded observation this launcher left behind")
        return Ownership(
            True, "adopted from a recorded observation no live launcher holds"
        )
    if status == "self":
        return Ownership(True, "adopted from this launcher's own earlier record")
    return Ownership(True, "adopted from a launcher that is gone")


def new_state(workspace, launcher_pid, launcher_created) -> dict:
    """A fresh instance record for this launcher and this workspace."""

    return {
        "workspace": str(pathlib.Path(REPO if workspace is None else workspace).resolve()),
        "launcher": {"pid": int(launcher_pid), "created": launcher_created},
        "api": None,
        "ui": None,
        "windows": [],
        "owned": [],
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def load_state(path):
    """The stored record, or ``None`` when it is absent or unreadable.

    An unreadable record is treated exactly like a missing one: nothing in it is trusted, so nothing
    is taken over.
    """

    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    if not isinstance(state.get("owned"), list):
        state["owned"] = []
    for role in ("api", "ui"):
        state.setdefault(role, None)
    if not isinstance(state.get("windows"), list):
        state["windows"] = []
    if not isinstance(state.get("launcher"), dict):
        state["launcher"] = None
    return state


def save_state(path, state) -> bool:
    """Write the record atomically: a temporary file beside it, then ``os.replace``."""

    target = pathlib.Path(path)
    payload = dict(state)
    payload["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, target)
        return True
    except (OSError, TypeError, ValueError):
        try:
            temporary.unlink()
        except OSError:
            pass
        return False


def reclaim_stale_instance_lock(lock_path, stale_after=INSTANCE_LOCK_STALE_SECONDS) -> bool:
    """Remove a lock left behind by a launcher that died inside a transition; ``False`` otherwise.

    A live holder keeps the lock for one read-modify-write, i.e. milliseconds, so a file older than
    ``stale_after`` cannot belong to a live holder. Both conditions are required: a lock is never
    broken on age alone, and never while its recorded owner may still be running. Without this, one
    launcher that died at the wrong moment could keep every later launcher from cleaning up orphans.
    """

    target = pathlib.Path(lock_path)
    try:
        age = time.time() - target.stat().st_mtime
        raw = target.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return False
    if age < float(stale_after):
        return False
    pid_text, _, created = raw.partition("|")
    try:
        pid = int(pid_text)
    except ValueError:
        # An unreadable lock is the record of a launcher that died between creating it and writing its
        # identity, so it has no owner left to wait for; the age above is the proof that nobody is
        # merely holding it.
        pid = 0
    if pid > 0:
        if pid == os.getpid():
            return False
        if process_alive(pid, created or None) or pid_exists(pid):
            return False
    try:
        target.unlink()
    except OSError:
        return False
    return True


def acquire_instance_lock(
    lock_path,
    timeout: float = INSTANCE_LOCK_TIMEOUT_SECONDS,
    stale_after: float = INSTANCE_LOCK_STALE_SECONDS,
):
    """Create the instance lock exclusively; ``None`` when somebody else holds it.

    The lock is only held for one read-modify-write of the record, so a short retry is enough. Failing
    to take it is the safe answer: the record may be mid-transition, so ownership is unknown and
    nothing is taken over. The returned value is the lock path, to be passed to
    :func:`release_instance_lock`.
    """

    target = pathlib.Path(lock_path)
    deadline = time.monotonic() + max(0.0, float(timeout))
    payload = f"{os.getpid()}|{process_created_at(os.getpid()) or ''}".encode("ascii", "replace")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    while True:
        try:
            descriptor = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if reclaim_stale_instance_lock(target, stale_after):
                continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)
            continue
        except OSError:
            return None
        try:
            os.write(descriptor, payload)
        except OSError:
            pass
        finally:
            os.close(descriptor)
        return target


def release_instance_lock(lock_path) -> None:
    """Remove the lock this caller holds, so the record is never left locked behind it."""

    try:
        pathlib.Path(lock_path).unlink()
    except OSError:
        pass


def listening_pid(port: int):
    """The pid the Windows TCP table reports as listening on ``port``, or ``None``."""

    command = (
        "$c = Get-NetTCPConnection -State Listen -LocalPort "
        f"{int(port)} -ErrorAction SilentlyContinue; "
        "if ($c) { ($c | Select-Object -First 1).OwningProcess }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
    except Exception:  # noqa: BLE001 - an unavailable query is unknown, not absent
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return int(value) if value.isdigit() else None


def server_identity(port: int):
    """``(pid, created)`` of the process answering on ``port``, or ``None`` when it is unidentifiable.

    ``created`` may be ``None``: the pid is known but its creation time could not be read. That is
    recorded honestly and can never be used as proof of ownership.
    """

    pid = listening_pid(port)
    if not pid:
        return None
    return pid, process_created_at(pid, refresh=True)


def process_command_line(pid):
    """The full command line of ``pid``, or ``None`` when it cannot be read.

    ``None`` is the answer whenever the query cannot run or answers nothing: this is the last check
    before a stop, so "could not read it" must never be mistaken for "it is ours".
    """

    try:
        number = int(pid)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    command = (
        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {number}'; "
        "if ($p) { $p.CommandLine }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
    except Exception:  # noqa: BLE001 - an unavailable query is unknown, and unknown never kills
        return None
    try:
        if completed.returncode != 0:
            return None
        value = (completed.stdout or "").strip()
    except Exception:  # noqa: BLE001 - a substitute process table answers oddly; treat it as unknown
        return None
    return value or None


def references_workspace(pid, workspace=None) -> bool:
    """Whether ``pid``'s command line still names this repository.

    The servers are started from this checkout's own interpreter (``<repo>\\.venv\\Scripts\\...``), so
    the repository path is in their command line. That check is what separates a Forge server of this
    workspace from an unrelated process that was given the same pid.
    """

    line = process_command_line(pid)
    if not line:
        return False
    try:
        root = os.path.normcase(
            str(pathlib.Path(REPO if workspace is None else workspace).resolve())
        )
        return bool(root) and root in os.path.normcase(str(line))
    except (OSError, TypeError, ValueError):
        return False


# ------------------------------------------------------- pinned process termination
#
# A pid alone is not a process: Windows recycles it the moment the process exits. ``taskkill /PID`` takes
# a pid, resolves it again inside another process and kills whatever currently answers to it, so a check
# and a kill that are separated by a few milliseconds can act on two different processes. Everything below
# exists to make the check and the kill act on *one* process: a handle is opened first, held while the
# identity and command line are verified, and then terminated through that same handle.

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
WAIT_OBJECT_0 = 0x00000000
EPOCH_DIFFERENCE_TICKS = 116_444_736_000_000_000  # 1601-01-01 -> 1970-01-01 in 100 ns ticks
#: How far the creation time a handle reports may differ from the recorded one and still be that process.
#: One millisecond absorbs the rounding of the timestamp representations involved and stays far below the
#: gap between two processes that were given the same pid.
CREATION_TOLERANCE_TICKS = 10_000
PINNED_EXIT_TIMEOUT_MS = 8_000


class PinnedProcess(NamedTuple):
    """A process pinned by an open handle, or the reason it could not be pinned."""

    handle: int
    exited: bool
    reason: str


def kernel32_library():
    """``kernel32`` with per-call error reporting.

    A function rather than a module attribute so that a test can substitute the library itself and assert
    on the Win32 calls the code makes, instead of on a helper that wraps them.
    """

    return ctypes.WinDLL("kernel32", use_last_error=True)


def last_error() -> int:
    """The Win32 error of the last failed call, or ``0`` when it cannot be read."""

    try:
        return int(ctypes.get_last_error())
    except Exception:  # noqa: BLE001 - an unreadable error is unknown, and unknown never is a success
        return 0


def open_pinned_process(pid) -> PinnedProcess:
    """Open a handle that pins ``pid`` for verification and termination.

    ``PROCESS_TERMINATE`` is what the stop needs, ``PROCESS_QUERY_LIMITED_INFORMATION`` lets the handle
    answer questions about identity, and ``SYNCHRONIZE`` lets it report the exit. A pid that no longer
    exists is refused by ``OpenProcess`` with ``ERROR_INVALID_PARAMETER``: the process exited, which is a
    normal outcome. Every other refusal -- access denied in particular -- means the process is there but
    cannot be proven and cannot be stopped, so it is left untouched.
    """

    try:
        number = int(pid)
    except (TypeError, ValueError):
        return PinnedProcess(0, False, "its pid is not a number")
    if number <= 0:
        return PinnedProcess(0, False, "it is not a live process id")
    try:
        handle = kernel32_library().OpenProcess(
            PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, number
        )
    except Exception as error:  # noqa: BLE001 - no handle means no proof, and no proof means no stop
        return PinnedProcess(0, False, f"no process handle could be opened ({error})")
    if not handle:
        if last_error() == ERROR_INVALID_PARAMETER:
            return PinnedProcess(0, True, "the process had already exited")
        return PinnedProcess(0, False, f"OpenProcess failed with Windows error {last_error()}")
    return PinnedProcess(handle, False, "the process is pinned by an open handle")


def process_has_exited(handle) -> bool:
    """Whether the pinned process has finished. A handle that cannot be asked is not an exit."""

    try:
        return kernel32_library().WaitForSingleObject(handle, 0) == WAIT_OBJECT_0
    except Exception:  # noqa: BLE001 - unknown is never treated as "already gone"
        return False


def process_start_ticks(handle):
    """The creation time of the process behind ``handle`` in FILETIME ticks, or ``None`` if unreadable.

    Read from the handle itself, so the answer describes the process the handle refers to and no other.
    """

    try:
        kernel32 = kernel32_library()
        created = ctypes.c_ulonglong()
        exited = ctypes.c_ulonglong()
        kernel_time = ctypes.c_ulonglong()
        user_time = ctypes.c_ulonglong()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        return int(created.value)
    except Exception:  # noqa: BLE001 - an unreadable creation time is unknown, not a mismatch
        return None


def recorded_creation_ticks(created):
    """The recorded CIM creation time as FILETIME ticks (100 ns since 1601-01-01 UTC), or ``None``.

    The record holds what ``Get-CimInstance`` printed: a local timestamp with an explicit offset and up to
    seven fractional digits. Comparing it with :func:`process_start_ticks` needs the offset applied and the
    fraction kept to the 100 ns digit; dropping either would shift the value by hours or round two
    different processes onto one number. Anything unparseable answers ``None``, which makes the caller rely
    on the provider comparison instead of guessing.
    """

    text = str(created or "").strip()
    if not text:
        return None
    body, offset_text = text, ""
    if body.endswith("Z"):
        body = body[:-1]
    else:
        cut = max(body.rfind("+"), body.rfind("-"))
        if cut > 10:
            body, offset_text = body[:cut], body[cut:]
    body = body.replace("T", " ", 1).strip()
    integer, _, fraction = body.partition(".")
    try:
        moment = datetime.datetime.strptime(integer, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    offset_seconds = 0
    if offset_text:
        sign = -1 if offset_text.startswith("-") else 1
        digits = offset_text[1:].replace(":", "")
        if len(digits) != 4 or not digits.isdigit():
            return None
        offset_seconds = sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    seconds = int(moment.replace(tzinfo=datetime.timezone.utc).timestamp())
    ticks = seconds * 10_000_000 + EPOCH_DIFFERENCE_TICKS - offset_seconds * 10_000_000
    if fraction:
        digits = fraction[:7]
        if not digits.isdigit():
            return None
        ticks += int(digits.ljust(7, "0"))
    return ticks


def pinned_identity_proof(pid, created, handle) -> tuple:
    """Whether the process pinned by ``handle`` is exactly the recorded ``(pid, created)`` of this repo.

    Every question is asked while the handle is open, so no answer can describe a different process than
    the one a later ``TerminateProcess`` acts on. The creation time is checked twice on purpose: once from
    the handle (independent of any helper process) and once from the same provider the record was written
    with (which is what the recorded string means). The command line is the last check, because a recycled
    pid can belong to a process of the same age but never to one started for this repository.
    """

    started = process_start_ticks(handle)
    expected = recorded_creation_ticks(created)
    if started is not None and expected is not None:
        if abs(started - expected) > CREATION_TOLERANCE_TICKS:
            return False, "has a different creation time than the record"
    if not process_alive(pid, created, refresh=True):
        return False, "is no longer the process this launcher adopted"
    if not references_workspace(pid):
        return False, "does not name this repository any more"
    return True, "is this repository's adopted server"


def terminate_pinned_process(handle) -> int:
    """Ask the pinned process to terminate; ``0`` on success, otherwise the Win32 error code."""

    try:
        if kernel32_library().TerminateProcess(handle, 1):
            return 0
        return last_error() or -1
    except Exception:  # noqa: BLE001 - a failed termination is reported, never assumed
        return -1


def wait_for_pinned_exit(handle, timeout_ms: int = PINNED_EXIT_TIMEOUT_MS) -> bool:
    """Whether the pinned process finished within ``timeout_ms``."""

    try:
        return kernel32_library().WaitForSingleObject(handle, int(timeout_ms)) == WAIT_OBJECT_0
    except Exception:  # noqa: BLE001 - an unaskable handle means the stop was not observed
        return False


def close_pinned_process(handle) -> None:
    """Release a pinned handle. Without this the process object would be kept alive by the handle."""

    try:
        kernel32_library().CloseHandle(handle)
    except Exception:  # noqa: BLE001 - a handle that cannot be closed is reported by the OS, not crashed on
        pass


def stop_adopted_servers(journal, adopted) -> None:
    """Stop adopted servers, each only while an open handle still proves it is that exact process.

    ``taskkill /PID`` was the wrong tool here. It resolves the pid *again* inside another process, so the
    identity check and the termination were separated by a window in which Windows could recycle the pid
    and an unrelated process be killed. This path opens a handle to the process first and keeps it open
    across every check: holding the handle is exactly what stops the pid from being reused, and the handle
    that was verified is the one terminated. Identity is proven from three things while the handle is held
    -- the creation time the handle reports, the creation time and pid the record holds, and a command line
    that still names this repository. Anything unproven is left untouched, and a process that already
    exited is a normal outcome rather than an error.
    """

    for name, pid, created in list(adopted or []):
        opened = open_pinned_process(pid)
        if opened.exited:
            journal.write(f"{name}: pid {pid} had already exited; nothing to stop")
            continue
        if not opened.handle:
            journal.write(
                f"{name}: pid {pid} cannot be pinned for verification ({opened.reason}); "
                "ownership unknown, left untouched"
            )
            continue
        try:
            if process_has_exited(opened.handle):
                journal.write(f"{name}: pid {pid} had already exited; nothing to stop")
                continue
            proven, reason = pinned_identity_proof(pid, created, opened.handle)
            if not proven:
                journal.write(
                    f"{name}: pid {pid} (created {created}) {reason}; ownership unknown, left untouched"
                )
                continue
            journal.write(f"{name}: stopping adopted pid {pid} (created {created})")
            error = terminate_pinned_process(opened.handle)
            if error:
                journal.write(
                    f"{name}: terminating pid {pid} failed with Windows error {error}; "
                    "ownership unknown, left untouched"
                )
                continue
            if wait_for_pinned_exit(opened.handle):
                journal.write(f"{name}: adopted pid {pid} stopped")
            else:
                journal.write(f"{name}: adopted pid {pid} did not stop")
        finally:
            close_pinned_process(opened.handle)


def forget_owned_roles(roles, journal) -> None:
    """Drop this launcher's claims from the record, leaving every other entry exactly as it was.

    The lock is released -- and so removed -- after the transition, so no lock file outlives a
    launcher. When nothing a later launcher could take over is left, the record is removed as well.
    """

    roles = [role for role in (roles or []) if role]
    if not roles:
        return
    state_path, lock_path = instance_paths(REPO)
    lock = acquire_instance_lock(lock_path)
    if lock is None:
        journal.write(
            f"instance record {state_path.name}: contended; this launcher's claims were left in place"
        )
        return
    try:
        state = load_state(state_path)
        if not isinstance(state, dict):
            return
        updated = dict(state)
        remaining = [item for item in (state.get("owned") or []) if item not in roles]
        updated["owned"] = remaining
        for role in roles:
            updated[role] = None
        if not remaining and not updated.get("api") and not updated.get("ui") and not updated.get("windows"):
            try:
                state_path.unlink()
            except OSError:
                pass
            journal.write(f"instance record {state_path.name}: removed, nothing is left to own")
            return
        if save_state(state_path, updated):
            journal.write(
                f"instance record {state_path.name}: released {', '.join(roles)}; "
                f"still recorded: {', '.join(remaining) or 'nothing owned'}"
            )
        else:
            journal.write(f"instance record {state_path.name}: could not be written")
    finally:
        release_instance_lock(lock)


def claim_window_profile(candidates, journal=None):
    """Claim the first candidate directory atomically, or return ``None``.

    ``mkdir(exist_ok=False)`` is the claim: two launches racing for the same fallback cannot both
    succeed, so parallel launches never share one new profile. Existing directories are skipped, never
    cleared, and nothing outside the candidates is touched.
    """

    for candidate in candidates:
        # The MARKER is the claim, and it is taken before the directory is touched. Claiming the directory
        # first left a window in which "directory exists, no marker yet" looked like a free profile to the
        # next contender, which is exactly how two callers came to share one profile (measured:
        # [alt1, alt1, alt2]). With the marker first, the state that is visible to everyone else is either
        # "marker present" or "directory plainly untouched", and neither is mistaken for a free slot.
        marker = candidate.with_name(candidate.name + ".claim")
        try:
            me = (str(os.getpid()), str(process_created_at(os.getpid())))
        except OSError:
            me = (str(os.getpid()), "")
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            fresh = marker.open("x", encoding="ascii")
        except FileExistsError:
            fresh = None
        except OSError:
            continue
        if fresh is not None:
            try:
                fresh.write("|".join(me))
                fresh.close()
            except OSError:
                continue  # the marker could not be written: refuse to hand out this profile
            try:
                candidate.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            if journal is not None:
                journal.write(f"window profile claimed: {candidate}")
            return candidate
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            # The marker exists and the directory already did too, so this profile is claimed by someone.
            # The owner decision below is the only thing that may hand it over.
            # Existence is not ownership. A directory left behind by a finished run is reusable, and
            # refusing to reuse it is what used to exhaust every candidate and end in "no usable window".
            # Ownership is decided from the live browsers' own --user-data-dir arguments, so a profile a
            # running browser still holds is never handed to a second instance.
            owner = profile_owner(candidate)
            if owner is None:
                if journal is not None:
                    journal.write(
                        f"window profile {candidate.name}: ownership cannot be determined, skipping it"
                    )
                continue
            if owner:
                continue
            # An existing directory with no live browser is reusable, but two launches may reach this line
            # together. The claim marker makes the reuse atomic: it is created with O_EXCL (so exactly one
            # process wins), it records the owner's pid and that process's creation time (so a recycled pid
            # cannot be mistaken for the original owner), and it is replaced - never deleted - with
            # os.replace once its owner is provably gone, which leaves no window in which one process
            # removes a marker another process still holds.
            marker = candidate.with_name(candidate.name + ".claim")
            try:
                me = (str(os.getpid()), str(process_created_at(os.getpid())))
            except OSError:
                me = (str(os.getpid()), "")
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                handle = marker.open("x", encoding="ascii")
            except FileExistsError:
                try:
                    recorded = marker.read_text(encoding="ascii").strip()
                except OSError:
                    if journal is not None:
                        journal.write(f"window profile {candidate.name}: claim unreadable, skipping it")
                    continue
                if recorded == "|".join(me):
                    # This profile is already claimed by this very process: hand out the NEXT candidate
                    # instead of returning a directory a second caller of the same process would share.
                    continue
                other_pid, other_created = (recorded.split("|", 1) + [""])[:2]
                if other_pid == str(os.getpid()):
                    # The marker names this very process. It is therefore already ours, whatever the
                    # creation-time reading says: never treat ourselves as a dead owner and take the
                    # profile back, which is how two callers of one process came to share a profile.
                    continue
                try:
                    recorded_identity = (int(other_pid), other_created)
                except (TypeError, ValueError):
                    recorded_identity = None
                try:
                    actual = process_created_at(int(other_pid)) if other_pid.isdigit() else None
                except (TypeError, ValueError):
                    actual = None
                # Ownership is only disproved when the marker carries a complete identity AND re-reading
                # that same process yields a different creation time (a recycled pid). Every unknown -
                # no recorded time, CIM error, timeout, empty marker - counts as held.
                alive = (
                    recorded_identity is None
                    or not other_created
                    or actual is None
                    or actual != other_created
                )
                if alive:
                    if journal is not None:
                        journal.write(f"window profile {candidate.name}: claimed by pid {recorded}")
                    continue
                # A successful replace is an atomic write, not proof of ownership: two processes can both
                # replace the same stale marker, so write-then-read-back cannot make them mutually
                # exclusive. The exclusion comes from the rename below instead: moving ONE file away
                # succeeds for exactly one caller, and the loser gets an error and moves on. The winner
                # then creates the marker with O_EXCL, its own unique claim. The stale copy is kept under a
                # distinct name rather than deleted, so no profile data is lost.
                stolen = marker.with_name(marker.name + f".stale.{os.getpid()}")
                try:
                    os.replace(marker, stolen)
                except OSError:
                    continue  # another process moved it first: this profile is not ours
                try:
                    fresh = marker.open("x", encoding="ascii")
                except (FileExistsError, OSError):
                    continue
                try:
                    fresh.write("|".join(me))
                    fresh.close()
                except OSError:
                    continue
            except OSError:
                continue
            else:
                try:
                    handle.write("|".join(me))
                    handle.close()
                except OSError:
                    continue
            if journal is not None:
                journal.write(f"window profile reused, no live owner: {candidate}")
            return candidate
        except OSError:
            continue
        else:
            # ``fresh is None`` means a marker already existed while the directory did not: another caller
            # holds the marker and simply has not created its directory yet. This profile is not ours, so
            # it is skipped instead of being handed out (measured before this fix as [alt1, alt1, alt2]).
            continue
        if journal is not None:
            journal.write(f"window profile claimed: {candidate}")
        return candidate
    return None


def live_browser_command_lines():
    """Command lines of live Chrome/Edge/WebView2 processes, or ``None`` when that cannot be known.

    Chrome is included because the window may now be Chrome's, and a profile only counts as owned when
    the browser that holds it is *seen* holding it.

    ``None`` is deliberately distinct from an empty list. An empty list is the fact "no browser process
    is running"; ``None`` means the query itself failed or timed out, and the caller must then stay
    conservative instead of treating a profile as free.
    """

    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match '^(chrome|msedge|msedgewebview2)\\.exe$' } | "
                "Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
    except Exception:  # noqa: BLE001 - an unavailable query is a fact, not a crash
        return None
    if completed.returncode != 0:
        return None
    return [line for line in (completed.stdout or "").splitlines() if line.strip()]


def profile_owner(profile):
    """``True``/``False`` when profile ownership is known, ``None`` when it could not be determined.

    A profile counts as owned when a live browser process names it in ``--user-data-dir``. Paths are
    compared after resolution and case folding, so a path that merely contains this one as a substring is
    not read as an owner, and a differently spelled but identical path still is.
    """

    command_lines = live_browser_command_lines()
    if command_lines is None:
        return None
    try:
        target = str(pathlib.Path(profile).resolve()).rstrip("\\/").lower()
    except OSError:
        return None
    for line in command_lines:
        for token in line.split():
            lowered = token.lower()
            if not lowered.startswith("--user-data-dir="):
                continue
            value = token.split("=", 1)[1].strip('"').strip("'")
            try:
                named = str(pathlib.Path(value).resolve()).rstrip("\\/").lower()
            except OSError:
                continue
            if named == target:
                return True
    return False


def window_handles(title_fragment: str) -> list:
    """Handles of visible windows whose title contains the fragment."""

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _lparam):  # noqa: ANN001 - Win32 callback signature
        if not user32.IsWindowVisible(hwnd):
            return True
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buffer, 512)
        title = buffer.value.lower()
        wanted = title_fragment.lower()
        if wanted in title:
            handles.append(hwnd)
        elif wanted == "forge ai" and "127.0.0.1" in title:
            # Edge titles an --app window after the page, and a page without its own <title> makes the
            # title the URL (measured: "127.0.0.1_/"). Every caller of this function asks for "FORGE AI",
            # so a loop waiting for that exact string concluded the window was closed the moment it
            # opened, closed it and stopped the servers. The window is still identified by the loopback
            # URL the launcher opened, so the title is not the only identity in use.
            handles.append(hwnd)
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return handles


#: How many consecutive checks may find none of this instance's windows before the last one is called closed.
#: A single scan that finds nothing proves nothing: Edge can hide or briefly destroy a window while it moves
#: it between processes. Three checks at :data:`WINDOW_POLL_SECONDS` each is the bound the close is decided
#: on, and any check that sees one of the windows again resets the count.
WINDOW_MISS_CHECKS = 3
WINDOW_POLL_SECONDS = 2.0


def window_owner_pid(handle) -> int:
    """The process that owns this window handle right now, or ``0`` when it cannot be asked.

    A handle the system has already recycled belongs to a different process, and that is how a stale
    registration is told apart from the window this instance opened: the recorded pid and the owner of
    the live handle must agree. ``0`` means "unknown", never "another process".
    """

    try:
        number = int(handle)
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0
    try:
        from ctypes import wintypes

        owner = wintypes.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(number), ctypes.byref(owner))
        return int(owner.value)
    except Exception:  # noqa: BLE001 - an unreadable owner is unknown, not a foreign window
        return 0


def windows_of_process(pid, visible_only: bool = True) -> list:
    """Handles of the top-level windows this exact process owns right now.

    Used to find the *new* handle of an application window whose old handle was replaced while the
    window stayed on screen, without going back to the page title for identity.
    """

    try:
        number = int(pid)
    except (TypeError, ValueError):
        return []
    if number <= 0:
        return []

    from ctypes import wintypes

    user32 = ctypes.windll.user32
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _lparam):  # noqa: ANN001 - Win32 callback signature
        owner = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) != number:
            return True
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        if hwnd:
            handles.append(int(hwnd))
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return handles


def window_exists(handle) -> bool:
    """Whether this exact handle is still a window -- visible, minimised or temporarily hidden.

    This is the identity the session is decided on. Edge titles its ``--app`` window after the page, so
    the title scan used to be the only proof that the application was open and a single scan that missed
    that title ended the session under a live window. ``IsWindow`` asks about the handle itself. A handle
    that cannot be asked at all answers ``True``: an unreadable fact must never end the wait and stop the
    servers behind a window that is still open.
    """

    try:
        number = int(handle)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    try:
        return bool(ctypes.windll.user32.IsWindow(number))
    except Exception:  # noqa: BLE001 - an unaskable window must not stop anything
        return True


def window_is_visible(handle) -> bool:
    """Whether this exact window handle still exists and is visible right now.

    ``IsWindow`` matters as much as ``IsWindowVisible``: a closed window's handle stops being valid, and the
    handle is what the instance record stores. A minimised window still counts as present, because it is
    still open. A handle that cannot be asked at all answers ``True``: an unreadable fact must never end the
    wait and stop the servers behind a window that is still open.
    """

    if not window_exists(handle):
        return False
    try:
        return bool(ctypes.windll.user32.IsWindowVisible(int(handle)))
    except Exception:  # noqa: BLE001 - an unaskable window must not stop anything
        return True


#: What one registered window handle looks like right now, asked directly from Win32.
WINDOW_OPEN = "open"  # the handle still exists and is visible: the application is open
WINDOW_HIDDEN = "hidden"  # still a window but not visible: minimised or briefly hidden
WINDOW_GONE = "gone"  # no window with this handle exists any more
WINDOW_FOREIGN = "foreign"  # the handle now belongs to another process: the registration is stale

#: The states that mean "the application is still open here".
WINDOW_PRESENT_STATES = (WINDOW_OPEN, WINDOW_HIDDEN)


def registered_window_state(entry) -> str:
    """Ask Win32 what one registered window is now, without consulting its title.

    The handle is the primary identity. Its owner is only used to reject a *stale* registration: a handle
    the system has recycled belongs to another process, and a handle whose recorded process was replaced
    (same pid, another creation time) is not the window this instance opened either. An unknown owner is
    never treated as a foreign window, because an unaskable fact must not end the session -- that is the
    defect this decision exists to prevent.
    """

    handle = entry.get("hwnd") if isinstance(entry, dict) else None
    if not window_exists(handle):
        return WINDOW_GONE
    recorded = 0
    created = None
    if isinstance(entry, dict):
        try:
            recorded = int(entry.get("pid")) if entry.get("pid") else 0
        except (TypeError, ValueError):
            recorded = 0
        created = entry.get("created")
    owner = window_owner_pid(handle)
    if owner and recorded:
        if owner != recorded:
            return WINDOW_FOREIGN
        if created and not process_alive(recorded, created):
            # The pid now names a later process: the window cannot be the one this entry recorded.
            return WINDOW_FOREIGN
    return WINDOW_OPEN if window_is_visible(handle) else WINDOW_HIDDEN


def build_stamp(repo: pathlib.Path = REPO) -> str:
    """The content hash of the built shell, or ``""`` when nothing was built.

    The launcher records it in the journal so a launch names the build it is about to
    show. It is a *fact about the files on disk*, not a version number, so it cannot
    claim a build the server is not serving.
    """

    try:
        return hashlib.sha256((repo / "ui" / "dist" / "index.html").read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


#: The loopback port the app window's DevTools endpoint listens on. It is used for one
#: thing only: reloading an already-open window after a rebuild. The endpoint binds to
#: loopback by Chrome itself and the Forge API on 8011 has no authentication either, so
#: this opens no new trust boundary -- it is a second loopback listener, not a wider one.
DEVTOOLS_PORT = 9340

#: Windows virtual-key codes for the keyboard fallback (Ctrl+Shift+R).
_VK_CONTROL = 0x11
_VK_SHIFT = 0x10
_VK_R = 0x52
_KEYEVENTF_KEYUP = 0x0002


def _websocket_reload(port: int = DEVTOOLS_PORT, timeout: float = 5.0) -> bool:
    """Ask the browser to hard-reload its page, over the DevTools protocol.

    A minimal RFC 6455 client: only the two frames this needs are implemented (one
    masked text frame out, one header in). It talks to ``127.0.0.1`` only, and only
    when the app window was started with :data:`DEVTOOLS_PORT` open. ``Page.reload``
    with ``ignoreCache`` is a real reload of the *document* the window runs.
    """

    import base64
    import socket
    import struct
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/json/list", timeout=timeout
        ) as response:
            targets = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - no endpoint is a normal, non-fatal state
        return False
    page = next(
        (
            item
            for item in targets
            if item.get("type") == "page"
            and str(item.get("webSocketDebuggerUrl", "")).startswith("ws://")
        ),
        None,
    )
    if page is None:
        return False
    url = str(page["webSocketDebuggerUrl"])
    hostport = url.split("//", 1)[1].split("/", 1)[0]
    host, _, raw_port = hostport.partition(":")
    path = "/" + url.split("//", 1)[1].split("/", 1)[1]
    try:
        with socket.create_connection((host or "127.0.0.1", int(raw_port or 80)), timeout=timeout) as sock:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {hostport}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(handshake.encode("ascii"))
            answer = b""
            while b"\r\n\r\n" not in answer:
                chunk = sock.recv(4096)
                if not chunk:
                    return False
                answer += chunk
            if b" 101 " not in answer.split(b"\r\n", 1)[0]:
                return False
            payload = json.dumps(
                {"id": 1, "method": "Page.reload", "params": {"ignoreCache": True}}
            ).encode("utf-8")
            mask = os.urandom(4)
            length = len(payload)
            header = bytearray([0x81])
            if length < 126:
                header.append(0x80 | length)
            elif length < 65536:
                header.append(0x80 | 126)
                header.extend(struct.pack("!H", length))
            else:  # pragma: no cover - this payload is tiny
                header.append(0x80 | 127)
                header.extend(struct.pack("!Q", length))
            header.extend(mask)
            masked = bytes(
                byte ^ mask[index % 4] for index, byte in enumerate(payload)
            )
            sock.sendall(bytes(header) + masked)
            return True
    except Exception:  # noqa: BLE001 - a reload helper must never break a launch
        return False


def _hard_reload_keys(handle: int) -> bool:
    """Send Ctrl+Shift+R to ``handle`` -- only while it really is the foreground window.

    This is the fallback for a browser started without a DevTools port. The foreground
    check is deliberate: a keystroke sent to a window that is *not* ours would type
    into whatever the owner has in front of him, so it is sent only when the OS reports
    the application window as the foreground one.
    """

    import ctypes

    user32 = ctypes.windll.user32
    if int(user32.GetForegroundWindow()) != int(handle):
        return False
    for vk in (_VK_CONTROL, _VK_SHIFT, _VK_R):
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
    for vk in (_VK_R, _VK_SHIFT, _VK_CONTROL):
        user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
    return True


def hard_reload_window(title_fragment: str, journal: "Journal", timeout: float = 20.0) -> bool:
    """Restore the application window and make it load the current build.

    The measured failure this exists for: the launcher only brought an already-open
    window to the front, so a window opened before ``vite build`` kept running the
    previous bundle, and the owner saw the old interface after a rebuild while the
    server was already serving the new one.

    Two paths, in order: the DevTools endpoint (a real ``Page.reload`` with the cache
    ignored), then Ctrl+Shift+R while the window is provably in front. Either way the
    window loads a **new document**, so ``index.html`` and the content-hashed assets
    are fetched again.
    """

    handles = window_handles(title_fragment)
    usable = [handle for handle in handles if not window_is_another_browser(handle)]
    if not usable:
        return False
    if not bring_to_front(title_fragment, journal):
        return False
    if _websocket_reload():
        journal.write(
            f"window hard-reloaded over the DevTools endpoint (build {build_stamp() or 'unbuilt'})"
        )
        return True
    if _hard_reload_keys(int(usable[0])):
        journal.write(
            f"window hard-reloaded with Ctrl+Shift+R (build {build_stamp() or 'unbuilt'})"
        )
        return True
    journal.write(
        "the window could not be reloaded automatically (no DevTools endpoint, and the "
        "window is not in front); the «Обновить» button loads the current build"
    )
    return True


def bring_to_front(title_fragment: str, journal: "Journal", timeout: float = 20.0) -> bool:
    """Restore and foreground the application window, whatever state it opened in.

    A window can be created minimised when the process that spawned it was started hidden or
    minimised -- measured on this machine, the launcher's own window arrived at ``-16000,-16000``
    with ``iconic=True`` and was invisible to the owner. Asking for it explicitly is the smallest
    reliable fix and works for every launch path.
    """

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    SW_SHOW = 5
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        handles: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def collect(hwnd, _lparam):  # noqa: ANN001 - Win32 callback signature
            buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buffer, 512)
            if title_fragment.lower() in buffer.value.lower():
                handles.append(hwnd)
            return True

        user32.EnumWindows(callback_type(collect), 0)
        for hwnd in handles:
            iconic = bool(user32.IsIconic(hwnd))
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.ShowWindow(hwnd, SW_SHOW)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            still_minimised = bool(user32.IsIconic(hwnd))
            journal.write(
                f"window '{title_fragment}': was_minimised={iconic}, "
                f"still_minimised={still_minimised}"
            )
            if not still_minimised:
                return True
            journal.write(
                "that window did not restore; it may belong to another application, so it is not "
                "reused"
            )
            return False
        time.sleep(0.5)
    journal.write(f"window '{title_fragment}' was not found within {int(timeout)}s")
    return False


class Supervisor:
    def __init__(self, journal: Journal, open_window: bool, url: str) -> None:
        self.journal = journal
        self.open_window = open_window
        self.url = url
        self.own: list[tuple[str, subprocess.Popen]] = []
        # Servers this launcher did not start but provably inherited from a launcher that is gone.
        # They are stopped by pid, and only while their identity can still be proven.
        self.adopted: list[tuple[str, int, str]] = []
        #: ``role -> pid`` for servers that were already answering when this launcher
        #: started. They are reused, not owned, so their death is reported rather than
        #: reaped -- and a restart of one is always journaled as a restart of a reused
        #: process (measured: the shell server that died on 2026-10-25 was one of these).
        self.reused: dict[str, int] = {}
        self.owned_roles: list[str] = []
        # Windows this launcher opened, as ``{"hwnd", "pid", "created", "registered"}`` entries. They are
        # written into the instance record as well, so the close is decided on recorded state and real
        # handles -- not on a title scan that a window can slip out of between two polls.
        self.windows: list[dict] = []

    # -------------------------------------------------------------- instance record
    def record_transition(self, mutate):
        """Apply one locked read-modify-write of the instance record.

        ``mutate`` receives the current record (or ``None``) and returns the record to store, or
        ``None`` to leave the record exactly as it was. The answer is the resulting record, or
        ``None`` when the record is contended or unreadable -- in which case nothing was written and
        nothing may be taken over.
        """

        state_path, lock_path = instance_paths(REPO)
        lock = acquire_instance_lock(lock_path)
        if lock is None:
            self.journal.write(
                f"instance record {state_path.name}: contended, another launcher is writing it; "
                "ownership unknown, nothing was taken over"
            )
            return None
        try:
            state = load_state(state_path)
            updated = mutate(state)
            if updated is not None:
                if save_state(state_path, updated):
                    self.journal.write(f"instance record {state_path.name}: written")
                else:
                    self.journal.write(f"instance record {state_path.name}: could not be written")
            return updated if updated is not None else state
        finally:
            release_instance_lock(lock)

    def record_observation(self, name: str, role: str, pid, created, owner: str = "reused") -> bool:
        """Record that a server answers on this role's port, without claiming ownership.

        A launcher that finds a server it cannot prove it owns still writes down what it saw: the
        listener's pid, its creation time, ``"adopted": false`` and an ``owner`` of ``reused`` or
        ``unknown``. The launcher slot is left exactly as it was -- ``null`` when there was no record,
        so writing an observation never turns this launcher into the owner -- and the role is not
        added to ``"owned"``. Without this, a later launcher finds no record at all and the loop never
        closes: ownership is never adopted, servers are never stopped, and the ports stay occupied.

        The write happens under the instance lock, so two launchers of one workspace cannot interleave
        a read-modify-write. A record held by a *live* launcher is not touched at all: it is that
        launcher's, and rewriting it would overwrite a decision it still holds.
        """

        owner = owner if owner in OBSERVATION_OWNERS else "reused"
        try:
            number = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            number = None
        written = {"value": False}
        state_path, _lock_path = instance_paths(REPO)
        my_pid, me_created = current_launcher_identity()

        def mutate(state):
            status = recorded_launcher_status(state, me=my_pid, me_created=me_created)
            if status == "live":
                self.journal.write(
                    f"{name}: a live launcher of this workspace holds {state_path.name}; "
                    "the observation is not written and ownership stays unknown"
                )
                return None
            base = (
                dict(state)
                if isinstance(state, dict)
                else {
                    "workspace": str(pathlib.Path(REPO).resolve()),
                    "launcher": None,
                    "api": None,
                    "ui": None,
                    "owned": [],
                }
            )
            base.setdefault("workspace", str(pathlib.Path(REPO).resolve()))
            base.setdefault("launcher", None)
            base.setdefault("owned", [])
            existing = base.get(role)
            if (
                isinstance(existing, dict)
                and existing.get("pid") == number
                and existing.get("created") == created
            ):
                # Exactly this process is already written down; nothing new was observed.
                return None
            base[role] = {
                "pid": number,
                "created": created,
                "adopted": False,
                "owner": owner,
            }
            written["value"] = True
            return base

        self.record_transition(mutate)
        if written["value"]:
            self.journal.write(
                f"instance record {state_path.name}: recorded {role} pid {number} "
                f"(created {created}) as {owner}, not owned"
            )
        return written["value"]

    def adopt_owned(self, name: str, role: str, port: int) -> bool:
        """Take over the server already answering on ``port``, but only with proof of ownership."""

        identity = server_identity(port)
        if identity is None:
            self.journal.write(
                f"{name}: the process listening on port {port} could not be identified; "
                "ownership unknown, it will not be stopped"
            )
            # What was seen is still written down, so the next launcher of this workspace does not
            # have to start from nothing.
            self.record_observation(name, role, None, None, owner="unknown")
            return False
        pid, created = identity
        taken = {"value": False}
        # The current launcher's identity, taken once and handed to the decision for *this* role: after the
        # first role is adopted the record's launcher slot holds exactly these two values, and the second
        # role must recognise them as ``self`` instead of asking whether a foreign launcher is still alive.
        my_pid, me_created = current_launcher_identity()

        def mutate(state):
            decision = decide_ownership(
                state, role, pid, created, workspace=REPO, me=my_pid, me_created=me_created
            )
            if not decision.adopt:
                self.journal.write(
                    f"{name}: pid {pid} (created {created}) already answers on port {port}; "
                    f"{decision.reason}"
                )
                return None
            base = dict(state)
            base["launcher"] = {"pid": my_pid, "created": me_created}
            owned = [item for item in (base.get("owned") or []) if item != role]
            base["owned"] = owned + [role]
            base[role] = {
                "pid": pid,
                "created": created,
                "adopted": True,
                "owner": "adopted",
            }
            taken["value"] = True
            self.journal.write(
                f"{name}: adopting pid {pid} (created {created}) on port {port}; {decision.reason}, "
                "so it will be stopped when this window closes"
            )
            return base

        self.record_transition(mutate)
        if taken["value"]:
            self.adopted.append((name, pid, created))
            self.owned_roles.append(role)
        else:
            # Ownership could not be proven, and that is exactly what must be recorded: the server is
            # observed, adopted is false, the role is not claimed -- and the launcher slot is left
            # alone, so an empty slot is never read as permission to kill.
            self.record_observation(
                name, role, pid, created, owner="reused" if created else "unknown"
            )
        return taken["value"]

    def record_own(self, name: str, role: str, process: subprocess.Popen) -> None:
        """Record a server this launcher just started, so a later launcher can clean it up."""

        pid = int(process.pid)
        created = process_created_at(pid, refresh=True)
        recorded = {"value": False}
        my_pid, me_created = current_launcher_identity()

        def mutate(state):
            status = recorded_launcher_status(state, me=my_pid, me_created=me_created)
            if status in ("live", "unknown"):
                self.journal.write(
                    f"{name}: the instance record belongs to a launcher that is "
                    + ("alive" if status == "live" else "not provably gone")
                    + "; it is left untouched and this server is only stopped from memory"
                )
                return None
            base = (
                dict(state)
                if isinstance(state, dict)
                else new_state(REPO, my_pid, me_created)
            )
            base["launcher"] = {"pid": my_pid, "created": me_created}
            owned = [item for item in (base.get("owned") or []) if item != role]
            base["owned"] = owned + [role]
            base[role] = {"pid": pid, "created": created}
            recorded["value"] = True
            return base

        self.record_transition(mutate)
        if recorded["value"]:
            self.owned_roles.append(role)
            if created:
                self.journal.write(f"{name}: started pid {pid} (created {created}), recorded as ours")
            else:
                self.journal.write(
                    f"{name}: started pid {pid}, but its creation time could not be read; "
                    "a later launcher will not be able to prove ownership of it"
                )

    # --------------------------------------------------------------------- windows
    def register_windows(self, handles, pid=None) -> list:
        """Record the windows this instance opened, under the instance lock, the moment they exist.

        Registration happens *before* the caller is told that a usable window exists, so a window the owner
        closes immediately after it appears is already written down and cannot be missed between "found" and
        "registered". The stored identity is the window handle with the process that actually owns that
        handle -- asked from Win32 instead of assumed from the launched process, because a browser
        invocation on a profile that is already running hands the window to the existing process and exits
        -- plus that process's creation time, which is what makes the entry verifiable rather than a guess.
        A live foreign launcher's record is never rewritten: its decisions are not this launcher's to
        overwrite.
        """

        launched = int(pid) if pid else None
        fresh = []
        for handle in handles or []:
            try:
                number = int(handle)
            except (TypeError, ValueError):
                continue
            if number <= 0:
                continue
            owner = window_owner_pid(number) or launched
            if owner and launched and owner != launched:
                self.journal.write(
                    f"window {number} belongs to pid {owner}, not the launched pid {launched}; "
                    "the owning process is what the registration keeps"
                )
            fresh.append(
                {
                    "hwnd": number,
                    "pid": owner,
                    "created": process_created_at(owner) if owner else None,
                    "registered": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
        if not fresh:
            return []
        known = {entry["hwnd"] for entry in fresh}
        self.windows = [entry for entry in self.windows if entry.get("hwnd") not in known] + fresh
        my_pid, me_created = current_launcher_identity()

        def mutate(state):
            if recorded_launcher_status(state, me=my_pid, me_created=me_created) == "live":
                self.journal.write(
                    "window registration: a live launcher of this workspace holds the instance record; "
                    "the registration is kept in memory only"
                )
                return None
            base = (
                dict(state)
                if isinstance(state, dict)
                else {
                    "workspace": str(pathlib.Path(REPO).resolve()),
                    "launcher": None,
                    "api": None,
                    "ui": None,
                    "owned": [],
                    "windows": [],
                }
            )
            base.setdefault("workspace", str(pathlib.Path(REPO).resolve()))
            base.setdefault("launcher", None)
            base.setdefault("owned", [])
            base["windows"] = [dict(entry) for entry in self.windows]
            return base

        self.record_transition(mutate)
        self.journal.write(
            "window registered: "
            + ", ".join(
                f"hwnd {entry['hwnd']} (pid {entry['pid']}, created {entry['created']})"
                for entry in fresh
            )
        )
        return fresh

    def unregister_windows(self, handles=None) -> None:
        """Drop this instance's window registrations once those windows are gone."""

        if handles is None:
            targets = {entry.get("hwnd") for entry in self.windows}
        else:
            targets = set()
            for handle in handles:
                try:
                    targets.add(int(handle))
                except (TypeError, ValueError):
                    continue
        if not targets:
            return
        self.windows = [entry for entry in self.windows if entry.get("hwnd") not in targets]

        def mutate(state):
            if not isinstance(state, dict):
                return None
            recorded = [item for item in (state.get("windows") or []) if isinstance(item, dict)]
            remaining = [item for item in recorded if item.get("hwnd") not in targets]
            if remaining == recorded:
                return None
            base = dict(state)
            base["windows"] = remaining
            return base

        self.record_transition(mutate)

    def registered_windows(self) -> list:
        """The window identities this instance registered, read back from the record when it was written.

        The record is the state the close is decided on, and the in-memory list is the fallback for the one
        case the record cannot answer: it was contended or unwritable, so what this launcher opened only
        exists in memory. Reading the record back is what lets an unregistration be honoured wherever it
        happens.
        """

        if self.windows:
            return [dict(entry) for entry in self.windows]
        state_path, _lock_path = instance_paths(REPO)
        state = load_state(state_path)
        recorded = state.get("windows") if isinstance(state, dict) else None
        return [
            item
            for item in (recorded or [])
            if isinstance(item, dict) and item.get("hwnd")
        ]

    def present_windows(self) -> list:
        """Handles of the registered windows that are still real windows right now.

        A minimised or temporarily hidden window is present too: it is still open, and hiding it must not
        end the session. A handle that now belongs to another process is not present: that registration
        is stale.
        """

        return [
            handle
            for handle, state in self.window_states()
            if state in WINDOW_PRESENT_STATES
        ]

    def window_states(self) -> list:
        """``(handle, state)`` for every registered window, asked from Win32 right now."""

        return [
            (entry.get("hwnd"), registered_window_state(entry))
            for entry in self.registered_windows()
        ]

    def registered_browser_processes(self) -> list:
        """The browser identities behind the registered windows that are provably still running.

        The identity is the pair the record stores -- the pid that owns the window handle and that
        process's creation time -- so a pid that was recycled is not mistaken for the browser, and a
        window handle that was destroyed is not mistaken for the application being closed while the
        browser that owns it still runs.
        """

        browsers = []
        seen = set()
        for entry in self.registered_windows():
            try:
                number = int(entry.get("pid"))
            except (TypeError, ValueError):
                continue
            created = entry.get("created")
            if number <= 0 or number in seen or not created:
                continue
            seen.add(number)
            if process_alive(number, created):
                browsers.append((number, created))
        return browsers

    def window_check(self) -> tuple:
        """One full check: the state of every registered window and the browsers behind them."""

        return self.window_states(), self.registered_browser_processes()

    def recover_windows(self, browsers) -> list:
        """Re-register visible windows that still belong to a registered browser process.

        A browser can replace the handle of its window while the window stays on screen. When that
        happens the recorded handle is gone but the application is not; the new handle is found through
        the process that owns it -- never through the page title -- and registered in place of the stale
        one, so a later close is still decided on a real handle.
        """

        stale = [
            handle
            for handle, state in self.window_states()
            if state in (WINDOW_GONE, WINDOW_FOREIGN)
        ]
        if stale:
            self.unregister_windows(stale)
            self.journal.write(
                "dropped stale window registration(s): "
                + ", ".join(str(handle) for handle in stale)
            )
        known = {entry.get("hwnd") for entry in self.registered_windows()}
        found = []
        for pid, _created in browsers:
            for handle in windows_of_process(pid):
                try:
                    number = int(handle)
                except (TypeError, ValueError):
                    continue
                if number in known or number in found:
                    continue
                found.append(number)
        if not found:
            return []
        fresh = self.register_windows(found)
        self.journal.write(
            "the window handle changed under a live browser; re-registered: "
            + ", ".join(f"hwnd {entry['hwnd']}" for entry in fresh)
        )
        return fresh

    def window_check_line(self, states, misses: int, required: int) -> str:
        """The diagnostic for one empty check: what was asked, what it answered, and for which pid."""

        recorded = {entry.get("hwnd"): entry for entry in self.registered_windows()}
        details = []
        for handle, state in states:
            entry = recorded.get(handle) or {}
            line = (
                f"hwnd {handle}: IsWindow={'False' if state == WINDOW_GONE else 'True'}, "
                f"IsWindowVisible={'True' if state == WINDOW_OPEN else 'False'}, "
                f"pid {entry.get('pid')}"
            )
            if state == WINDOW_FOREIGN:
                owner = window_owner_pid(handle)
                line += f", a stale registration: the handle now belongs to pid {owner or 'unknown'}"
            details.append(line)
        return (
            ("; ".join(details) or "no registered window handle")
            + "; the window is gone and its browser has exited "
            + f"(check {misses} of {required})"
        )

    def await_windows_closed(self) -> int:
        """Wait until the last registered window is gone *and* the browser behind it has exited.

        Three facts decide it, in this order, because the measured failure was a live window whose title
        stopped being enumerable:

        * a registered handle that is still a window -- visible, minimised or briefly hidden -- means the
          application is open, whatever its title says, and nothing else may override that;
        * a handle that is gone does not end the session by itself: the browser that owned it is asked,
          and while that exact process (pid *and* creation time) still runs the wait continues, because
          the browser may already have replaced its window handle;
        * only a gone handle *and* an exited browser conclude the close -- and still only after
          :data:`WINDOW_MISS_CHECKS` consecutive checks agree, because one observation (a handle the
          system is recycling, a process the provider has not reaped yet) proves nothing.

        A handle that now belongs to another process is a stale registration, not this application, and
        is never counted as open.
        """

        required = max(1, int(WINDOW_MISS_CHECKS))
        misses = 0
        while True:
            states, browsers = self.window_check()
            present = [handle for handle, state in states if state in WINDOW_PRESENT_STATES]
            if not present and browsers:
                # The handle cannot prove the application open, but the browser can still be running
                # and may have replaced its window: look for the new handle before waiting on.
                if self.recover_windows(browsers):
                    states, browsers = self.window_check()
                    present = [handle for handle, state in states if state in WINDOW_PRESENT_STATES]
            if present:
                if misses:
                    self.journal.write(
                        "window handle(s) "
                        + ", ".join(str(handle) for handle in present)
                        + f": visible again after {misses} empty check(s); continuing to wait"
                    )
                misses = 0
                hidden = [handle for handle, state in states if state == WINDOW_HIDDEN]
                if hidden:
                    self.journal.write(
                        "window handle(s) "
                        + ", ".join(str(handle) for handle in hidden)
                        + ": still a window but not visible right now (minimised or hidden); "
                        "continuing to wait"
                    )
            elif browsers:
                self.journal.write(
                    "registered window handle(s) gone, but the browser process "
                    + ", ".join(f"pid {pid} (created {created})" for pid, created in browsers)
                    + " is still alive; continuing to wait"
                )
                misses = 0
            else:
                misses += 1
                self.journal.write(self.window_check_line(states, misses, required))
                if misses >= required:
                    return misses
            time.sleep(WINDOW_POLL_SECONDS)

    def ensure(self, name: str, port: int, arguments: list[str], log_name: str, url: str) -> bool:
        role = OWNED_ROLES.get(name)
        if port_is_served(port):
            self.journal.write(f"{name}: port {port} already answers, reusing it")
            # Written down so a later restart can say *whose* server died: a reused one was
            # never ours, and its exit code is not ours to read. Measured: 8020 was a reused
            # pid, it died, and the journal said nothing at all about it.
            reused = listening_pid(port)
            if reused is not None:
                self.reused[role or name] = int(reused)
            if role is not None:
                self.adopt_owned(name, role, port)
            return True
        self.journal.write(f"{name}: starting on 127.0.0.1:{port}")
        process = start_server(arguments, log_name)
        self.own.append((name, process))
        if role is not None:
            self.record_own(name, role, process)
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

    def watch_servers(self, stop_event: threading.Event) -> None:
        """Keep BOTH servers alive while the window is open; one restart each, then report.

        Measured defect (2026-10-25): the shell server on 8020 stopped answering while the
        window was open. This watchdog existed for ``core-api`` only, so 8011 came back and
        8020 did not -- every request the window made then died, and nothing on screen said
        why. A port that is down is not a detail: the whole interface is served from it.

        Three facts are written down for every restart, because "it died" is not a reason:

        * which server and which port stopped answering, and how many checks missed it;
        * the tail of its own log (a traceback, when it left one) -- so the next reader sees
          the cause in ``launcher.log`` instead of having to reproduce it;
        * whether this launcher started it (and its exit code) or merely found it already
          listening (``reused``), because a reused server that dies cannot be stopped by us.

        One restart per server per session: a server that dies twice is a fact about the
        machine, and a loop of restarts would hide it behind a busy log.
        """

        restarts = {name: 0 for name, *_ in SERVER_ROLES}
        misses = {name: 0 for name, *_ in SERVER_ROLES}
        while not stop_event.is_set():
            if stop_event.wait(SERVER_WATCH_SECONDS):
                return
            for name, url, arguments, log_name, role in SERVER_ROLES:
                if answers(url, timeout=1.0):
                    misses[name] = 0
                    continue
                misses[name] += 1
                # One missed answer can be a moment of load; two in a row is a dead port.
                if misses[name] < SERVER_WATCH_MISSES:
                    continue
                if restarts[name] >= 1:
                    if misses[name] == SERVER_WATCH_MISSES:
                        self.journal.write(
                            f"{name} is not answering again on port "
                            f"{API_PORT if role == 'api' else UI_PORT}; not restarting a "
                            f"second time (its log: {LOGS / log_name})"
                        )
                    continue
                restarts[name] += 1
                self.journal.write(
                    f"{name} stopped answering on port "
                    f"{API_PORT if role == 'api' else UI_PORT} after "
                    f"{misses[name]} checks; restarting it once"
                )
                self.journal_server_reason(name, log_name, role)
                process = start_server(arguments, log_name)
                self.own.append((f"{name} (restarted)", process))
                self.record_own(f"{name} (restarted)", role, process)
                deadline = time.monotonic() + READY_TIMEOUT_SECONDS
                while time.monotonic() < deadline and not answers(url, timeout=1.0):
                    time.sleep(0.5)
                if answers(url, timeout=1.0):
                    self.journal.write(f"{name}: back after restart (pid {process.pid})")
                    misses[name] = 0
                    if role == "ui":
                        # The window was talking to a dead port; once the shell is back the
                        # page it shows is reloaded, so the owner does not have to do it.
                        if hard_reload_window("FORGE AI", self.journal):
                            self.journal.write(
                                "the window was reloaded after the shell server came back"
                            )
                else:
                    self.journal.write(
                        f"{name}: did not come back after the restart; its log is "
                        f"{LOGS / log_name}"
                    )
                    self.journal_log_tail(name, log_name)

    def journal_server_reason(self, name: str, log_name: str, role: str) -> None:
        """Write down what is known about the server that just stopped answering."""

        port = API_PORT if role == "api" else UI_PORT
        for entry_name, process in self.own:
            if not entry_name.startswith(name):
                continue
            code = process.poll()
            if code is not None:
                self.journal.write(
                    f"{name}: the process this launcher started has exited with code "
                    f"{code} (pid {process.pid})"
                )
            else:
                self.journal.write(
                    f"{name}: pid {process.pid} is still running but no longer answers on "
                    f"port {port}"
                )
        reused = getattr(self, "reused", {}).get(role)
        if reused is not None:
            self.journal.write(
                f"{name}: it was NOT started by this launcher -- pid {reused} was already "
                f"listening on port {port} and was reused without ownership, so its exit "
                f"cannot be stopped or reaped by us; it is started again here"
            )
        self.journal_log_tail(name, log_name)

    def journal_log_tail(self, name: str, log_name: str) -> None:
        """Copy the last traceback (or the last lines) of a server's log into the journal."""

        path = LOGS / log_name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            self.journal.write(f"{name}: its log {path} could not be read")
            return
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            self.journal.write(f"{name}: its log {path} is empty")
            return
        start = None
        for index, line in enumerate(lines):
            if line.startswith("Traceback (most recent call last)"):
                start = index
        picked = lines[start : start + 24] if start is not None else lines[-8:]
        self.journal.write(
            f"{name}: "
            + ("its last traceback" if start is not None else "the tail of its log")
            + f" ({path.name}):"
        )
        for line in picked:
            self.journal.write(f"    {line}")

    def close_window(self) -> int:
        """Open the application window and wait for the user to close it."""

        existing = window_handles("FORGE AI")
        if existing:
            # Only a window this launcher can actually bring back to the screen may be reused: a
            # foreign or ghost window with the same title would otherwise absorb the launch and the
            # owner would see nothing.
            self.journal.write("an application window is already open; trying to bring it forward")
            # A window owned by a different browser can carry the same title (measured: the owner's own
            # Edge window shows "FORGE AI — Command Center" as one of its tabs), so those are skipped
            # instead of absorbing the launch attempt -- and they are never registered.
            foreign = [handle for handle in existing if window_is_another_browser(handle)]
            for handle in foreign:
                self.journal.write(
                    f"skipping a window belonging to another browser: {window_title(handle)!r}"
                )
            own = [handle for handle in existing if handle not in foreign]
            if own and hard_reload_window("FORGE AI", self.journal):
                self.register_windows(own)
                self.await_windows_closed()
                self.unregister_windows()
                self.journal.write("window closed")
                return 0
            self.journal.write("starting a fresh browser window instead")

        browser = resolve_browser()
        if browser is None:
            self.journal.write(
                "no browser found: looked for chrome at "
                + ", ".join(str(path) for path in chrome_candidates())
                + " and for edge at "
                + ", ".join(str(path) for _name, paths in browser_catalogue()[1:] for path in paths)
            )
            return 2
        self.journal.write(f"browser for the window: {browser.name} ({browser.executable})")
        profile = window_profile(REPO, browser)
        stop_event = threading.Event()
        watchdog = threading.Thread(target=self.watch_servers, args=(stop_event,), daemon=True)
        watchdog.start()
        restored, conflicted = self.open_window_with_profile(profile, browser)
        if not restored and conflicted and browser.name == "chrome":
            # Measured on this machine (2026-10-18): a chrome.exe started by this launcher exited at once
            # and left a crash dump in ``<profile>/Crashpad/reports`` (STATUS_BREAKPOINT inside chrome.dll,
            # before the browser's own log existed) while the very same build opened the window when its
            # browser sandbox was not initialised. The retry below is what makes the window appear; the
            # first attempt keeps the sandbox whenever it works, and the journal states what happened.
            # ``--test-type`` only suppresses the "unsupported command-line flag" infobar that Chrome
            # shows for ``--no-sandbox``; without it the window carries a warning strip over the UI.
            flags = ("--no-sandbox", "--test-type")
            self.journal.write(
                "chrome exited before a usable window appeared (measured: its browser sandbox cannot "
                "initialise when chrome is started by this launcher); retrying once with --no-sandbox"
            )
            restored, conflicted = self.open_window_with_profile(profile, browser, flags)
        else:
            flags = ()
        if not restored and conflicted:
            # The primary profile is genuinely held: retry once on a separate profile. The held one
            # is left exactly as it is, and no browser process is terminated.
            fallback = claim_window_profile(window_profile_candidates(profile)[1:], self.journal)
            if fallback is not None:
                self.journal.write(f"retrying the window with a separate profile: {fallback}")
                restored, _ = self.open_window_with_profile(fallback, browser, flags)
            else:
                self.journal.write("no separate profile could be claimed for the window")
        if not restored:
            self.journal.write(
                "no usable window appeared; the application window could not be shown"
            )
        else:
            # The close is decided from the windows this instance registered, the real handles behind
            # them and the browser process that owns those handles -- not from a title scan that ends the
            # moment the title is not found. A window closed right after it appeared, or a handle the
            # browser replaced while the window stayed on screen, is therefore still decided correctly,
            # and the launcher reaches ``stop_own`` instead of leaving the ports occupied.
            self.await_windows_closed()
            self.unregister_windows()
        self.journal.write("window closed")
        stop_event.set()
        return 0

    def open_window_with_profile(self, profile: pathlib.Path, browser=None, flags=()):
        """Start ``browser`` (Chrome, or Edge when Chrome is absent) on ``profile``.

        Reports ``(usable window, profile conflict)``. A conflict is the measured condition only: the
        browser exited before a usable window appeared, which is what another instance holding the same
        profile produces. A window that is merely slow to appear past the deadline is not a conflict,
        so a slow browser is never blamed on the profile. Nothing is terminated or deleted here.

        ``flags`` are appended only when a caller retries deliberately; the first attempt never carries
        any.
        """

        browser = browser if browser is not None else resolve_browser()
        if browser is None:
            self.journal.write("no browser found to open the window with")
            return False, False
        process = subprocess.Popen(
            [
                str(browser.executable),
                f"--app={self.url}",
                "--window-size=1600,1000",
                f"--user-data-dir={profile}",
                # A loopback DevTools endpoint, so an already-open window can be
                # reloaded after a rebuild instead of silently running the old bundle.
                f"--remote-debugging-port={DEVTOOLS_PORT}",
                "--no-first-run",
                "--no-default-browser-check",
                *flags,
            ],
            cwd=str(REPO),
        )
        self.journal.write(
            f"window opened with {browser.name}: {self.url} "
            f"(pid {process.pid}, profile {profile.name}{', ' + ' '.join(flags) if flags else ''})"
        )
        # The window title comes from the page, and a page without its own <title> makes the browser title
        # the window after the URL (measured: '127.0.0.1_/'). Waiting for one exact string therefore reported
        # "no usable window" for a window that was already on screen, so the page's own host is accepted
        # as well. A browser that dies is still detected immediately below.
        host = self.url.split("//", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
        titles = ("FORGE AI", host) if host else ("FORGE AI",)
        # A cold browser start with a freshly created profile measured well past 15s on this machine, and the
        # deadline expiring reported "no usable window" for a window that was still coming up. The wait is
        # bounded but long enough for the real startup, and a browser that exits is still detected
        # immediately below.
        deadline = time.monotonic() + 60.0
        reported_foreign: set = set()
        while time.monotonic() < deadline:
            for fragment in titles:
                found = window_handles(fragment)
                # An ordinary browser window names the browser in its title, so it is never the launcher's
                # ``--app`` window even when the application URL appears in it. Measured on this machine:
                # the host fallback below adopted the owner's own Edge window with a ``127.0.0.1`` tab and
                # waited on it, so no window of the launcher's own was ever shown.
                foreign = [handle for handle in found if window_is_another_browser(handle)]
                for handle in foreign:
                    if handle in reported_foreign:
                        continue
                    reported_foreign.add(handle)
                    self.journal.write(
                        f"skipping a window belonging to another browser: {window_title(handle)!r}"
                    )
                found = [handle for handle in found if handle not in foreign]
                if not found:
                    continue
                if not bring_to_front(fragment, self.journal):
                    continue
                # The window is registered here, inside the very call that discovers it, and only then is
                # it reported as usable. A close that happens within milliseconds of the window appearing
                # used to fall between "found" and the caller's wait loop and was lost; registering first
                # means the wait below is already looking at a recorded window when it starts.
                self.register_windows(found, process.pid)
                return True, False
            if process.poll() is not None:
                self.journal.write(
                    f"the browser exited with code {process.returncode} before a usable window "
                    f"appeared; another instance may hold the profile {profile}"
                )
                return False, True
            time.sleep(0.5)
        return False, process.poll() is not None

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
        adopted = list(getattr(self, "adopted", None) or [])
        stop_adopted_servers(self.journal, adopted)
        self.adopted = []
        for port in (API_PORT, UI_PORT):
            for _ in range(20):
                if not port_is_served(port):
                    break
                time.sleep(0.25)
            self.journal.write(
                f"port {port}: {'still listening' if port_is_served(port) else 'free'}"
            )
        roles = list(getattr(self, "owned_roles", None) or [])
        self.owned_roles = []
        forget_owned_roles(roles, self.journal)


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
