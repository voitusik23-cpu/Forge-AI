"""«Запустить приложение»: one click, one request, the result in its own window.

The owner's sentence this file guards:

    «Если написано открыть проект, я так понял, что я открою работающий проект и он у
    меня развернётся на рабочем столе. Я хочу видеть результат, а не вечное меню
    Forge.»

Before this, «Открыть проект» navigated inside Forge and «Запустить локально» started a
server but left the owner looking for a URL. This file proves ``POST
/api/projects/{id}/launch``:

1. it **starts the project** (a real local runner and a real preview registry: the same
   objects the shipped process composes) and opens the URL it really got;
2. it picks the right URL and kind: the port the project's own process bound, or the
   read-only preview of a static page;
3. it **reuses** a running server: the second click starts no second process, it only
   opens the window again (``reused: true``, same URL, same server pid);
4. it refuses **honestly** -- no files, no entry point, no approval, a process that died
   without a port -- with one plain sentence and the run's own facts (state, exit code,
   redacted log tail), and a refused launch starts no window at all;
5. the window is Chrome in app mode first, Edge second, the default browser last, with
   the exact argument vector: ``--app=<url> --window-size=1280,900
   --user-data-dir=<repo>/.forge/app-profile/<project-id>``, never a shell;
6. a process that dies but leaves a static page in its folder is reported plainly and the
   page is offered as an explicit second action (``fallback_available`` plus a real
   ``fallback_url``); it is shown only when the caller asks with ``fallback: true``;
7. no launch ever opens a portless URL, and the child (and the containment probe) are
   created without a console window.

The OS opener is always injected: no real browser window opens on the machine running
this suite. ``shell=False`` and the exact argv are asserted instead.
"""

from __future__ import annotations

import http.server
import os
import pathlib
import shutil
import socket
import subprocess
import threading
import time
import unittest
from unittest import mock
import urllib.request

from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.config.app_settings import AppSettingsStore
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects import launcher as launcher_module
from app.projects import ports as ports_module
from app.projects import runner as runner_module
from app.projects.app_window import (
    APP_PROFILE_DIRECTORY,
    APP_WINDOW_SIZE,
    NO_SANDBOX_FLAGS,
    PROFILE_RETENTION,
    SANDBOX_DOWNGRADE_NOTE,
    WINDOW_CHROME_APP,
    WINDOW_DEFAULT_BROWSER,
    WINDOW_EDGE_APP,
    AppWindowOpener,
    WindowRefusal,
)
from app.projects.entrypoint import resolve_entry
from app.projects.launcher import (
    CONSOLE_INPUT_MESSAGE,
    MANIFEST_PORT_NOTE,
    NOT_A_SERVER_MESSAGE,
    REUSED_SERVER_NOTE,
    LaunchRefusal,
    ProjectLauncher,
    looks_like_console_prompt,
)
from app.projects.ports import (
    first_answering,
    manifest_port_claim,
    parse_banner_ports,
    probe_http,
)
from app.projects.runner import LocalRunner, RunTarget
from app.projects.service import ProjectService
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
WORKSPACE_PROJECT_ID = "launch-test-workspace"
PROJECT_ID = "proj-0123456789ab"
GOAL = "Почтовый центр: список писем и фильтр по отправителю."

#: A project that really serves a page on a port the runner can discover.
SERVING = """\
import http.server, socketserver

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"LAUNCH-APP-OK\\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return

with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
    print("listening %d" % httpd.server_address[1], flush=True)
    httpd.serve_forever()
"""

STATIC_PAGE = "<!doctype html><title>Launch me</title><h1>LAUNCH-STATIC-OK</h1>\n"

#: A project that writes a console prompt and exits without ever binding a port.
#: This is the shape the owner's console-only application has to be diagnosed as.
PROMPT_APP = """\
print("Введите пароль:", flush=True)
raise SystemExit(3)
"""

#: The owner's real case, end to end: the application finds **another instance** already
#: serving, prints that instance's address, and exits 0 without ever binding the port
#: itself. Forge used to scan the child's sockets, see nothing, and refuse with «порт не
#: открылся» while the application was running on the printed address.
ALREADY_RUNNING = """\
print("Мейл АИ уже запущен", flush=True)
print("Адрес: {url}", flush=True)
raise SystemExit(0)
"""

#: A child that announces an address nobody answers on and exits 0. The printed address
#: is a claim; only a real `GET` may turn it into a server.
DEAD_ADDRESS = """\
print("Адрес: http://127.0.0.1:{port}", flush=True)
raise SystemExit(0)
"""

#: The port the project's **manifest** claims, in its own specification text. Reality
#: chooses another one in the launch tests, and both numbers must be named.
MANIFEST_CLAIM_PORT = 8765
MANIFEST_GOAL = (
    f"{GOAL} ЗАПУСК: start.bat запускает python app.py и открывает "
    f"http://127.0.0.1:{MANIFEST_CLAIM_PORT} в браузере."
)


class _ServingHandler(http.server.BaseHTTPRequestHandler):
    """One page, for the server this test starts instead of a project's process."""

    def do_GET(self):  # noqa: N802 - the base class names it this way
        body = bytes(getattr(self.server, "body", b"LAUNCH-APP-OK\n"))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D401 - keep the test output quiet
        return


class _FakeAppServer:
    """A real loopback server: the "application that was already running".

    It is a real socket in the test process, so the launch path's HTTP probe meets a
    real HTTP answer -- no mock of the verification in the proofs that matter.
    """

    def __init__(self, body: bytes = b"LAUNCH-APP-OK\n") -> None:
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ServingHandler)
        self._server.body = body
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def stop(self) -> None:
        try:
            self._server.shutdown()
        finally:
            self._server.server_close()
            self._thread.join(timeout=5.0)


def _closed_port() -> int:
    """A port in the usable range with nothing listening on it."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --------------------------------------------------------------------------- #
# recorders injected where the real OS would be
# --------------------------------------------------------------------------- #


class _FakeWhich:
    """The program table of the machine this test pretends to be."""

    def __init__(self, installed: dict) -> None:
        self.installed = dict(installed)
        self.asked: list[str] = []

    def __call__(self, program: str):
        self.asked.append(program)
        return self.installed.get(program)


class _RecordingSpawn:
    """Records the launch instead of starting a browser."""

    def __init__(self, pid: int = 9876) -> None:
        self.calls: list[dict] = []
        self.pid = pid

    def __call__(self, vector, **kwargs):
        self.calls.append({"vector": [str(part) for part in vector], "kwargs": dict(kwargs)})
        pid = self.pid

        class _Child:
            pass

        child = _Child()
        child.pid = pid
        return child


class _RecordingStartfile:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> None:
        self.urls.append(url)


class _RaisingStartfile:
    """A machine whose default browser cannot be opened at all."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> None:
        self.urls.append(url)
        raise OSError("no default browser is registered")


class _FakeWindows:
    """The OS window table of the machine this test pretends to be.

    ``titles`` maps a pid to the ``(hwnd, title)`` pairs that process owns; ``default``
    is what any *other* pid owns. A pid that maps to an empty list -- or to a title that
    fell back to the bare host -- has no rendered page, which is the measured grey
    window. ``alive`` (when given) decides whether a pid is a live process at all: a dead
    process owns no window, whatever the table says. ``asked`` records every pid the
    opener asked about.
    """

    def __init__(
        self,
        titles: dict | None = None,
        default: tuple = ("Почта АИ",),
        alive=None,
    ) -> None:
        self.titles = {int(pid): list(items) for pid, items in (titles or {}).items()}
        self.default = list(default)
        self.alive = alive
        self.asked: list[int] = []

    def __call__(self, pid: int):
        self.asked.append(int(pid))
        if self.alive is not None and not self.alive(int(pid)):
            return []
        if int(pid) in self.titles:
            return list(self.titles[int(pid)])
        return [(100000 + int(pid), title) for title in self.default]


class _AliveSpawn:
    """Every browser it starts stays alive; each gets the next pid.

    The opener must still *confirm* the window, which is what the injected
    :class:`_FakeWindows` answers. An attempt that is abandoned is terminated and
    recorded, so a test can see the grey window's process really ended.
    """

    def __init__(self, first_pid: int = 1001) -> None:
        self.calls: list[dict] = []
        self.stopped: list[int] = []
        self._first_pid = int(first_pid)

    def __call__(self, vector, **kwargs):
        self.calls.append({"vector": [str(part) for part in vector], "kwargs": dict(kwargs)})
        pid = self._first_pid + len(self.calls) - 1
        stopped = self.stopped

        class _Child:
            poll = staticmethod(lambda: None)

        child = _Child()
        child.pid = pid
        child.terminate = lambda pid=pid: stopped.append(pid)
        return child


class _DeadSpawn:
    """A browser that exits immediately, as Chrome does on a host that forbids its sandbox."""

    def __init__(self, code: int = 3221225475, pid: int = 1111) -> None:
        self.code = int(code)
        self.pid = int(pid)
        self.calls: list[dict] = []

    def is_alive(self, pid: int) -> bool:
        return False

    def __call__(self, vector, **kwargs):
        self.calls.append({"vector": [str(part) for part in vector], "kwargs": dict(kwargs)})
        code = self.code
        pid = self.pid

        class _Child:
            poll = staticmethod(lambda code=code: code)

        child = _Child()
        child.pid = pid
        return child


class _FlakySpawn:
    """The first ``dead_calls`` browsers die; the ones after stay up like a real window."""

    def __init__(self, dead_calls: int = 1, code: int = 3221225475) -> None:
        self.dead_calls = int(dead_calls)
        self.code = int(code)
        self.calls: list[dict] = []
        self.alive_pids: set[int] = set()

    def is_alive(self, pid: int) -> bool:
        return int(pid) in self.alive_pids

    def __call__(self, vector, **kwargs):
        self.calls.append({"vector": [str(part) for part in vector], "kwargs": dict(kwargs)})
        index = len(self.calls)
        code = self.code if index <= self.dead_calls else None
        pid = 1000 + index
        if code is None:
            self.alive_pids.add(pid)

        class _Child:
            poll = staticmethod(lambda code=code: code)

        child = _Child()
        child.pid = pid
        return child


class _FakeRunner:
    """A supervisor stand-in: records the approval and returns the state a test names."""

    def __init__(
        self,
        *,
        started: dict | None = None,
        status: dict | None = None,
    ) -> None:
        self.started_calls: list[dict] = []
        self._started = dict(started or {"state": "RUNNING", "url": "http://127.0.0.1:5001/"})
        self._status = dict(status or {"state": "NOT_RUN", "url": ""})

    def status(self, target):
        return dict(self._status)

    def start_confirmed(self, target, *, confirm, digest, offline=False):
        self.started_calls.append(
            {"target": target, "confirm": confirm, "digest": digest, "offline": offline}
        )
        self._status = dict(self._started)
        return dict(self._started)


class _FakePreviews:
    def __init__(self, *, started: dict | None = None, running: dict | None = None) -> None:
        self.started_calls: list[dict] = []
        self.stopped_calls: list[str] = []
        self._started = dict(
            started
            or {"running": True, "url": "http://127.0.0.1:5002/index.html", "port": 5002}
        )
        self._running = dict(running or {"running": False})

    def status(self, project_id):
        return dict(self._running)

    def start(self, project_id, workspace, entry):
        self.started_calls.append(
            {"project_id": project_id, "workspace": workspace, "entry": entry}
        )
        self._running = dict(self._started)
        return dict(self._started)

    def stop(self, project_id):
        self.stopped_calls.append(project_id)
        self._running = {"running": False, "url": "", "port": 0}
        return dict(self._running)


def _answering(*ports: int):
    """A probe that answers only on the named ports, like a real loopback probe."""

    allowed = {int(port) for port in ports}

    def probe(port: int) -> int:
        return 200 if int(port) in allowed else 0

    return probe


class _Session:
    def __init__(self, goal: str = GOAL) -> None:
        self.session_id = f"disc-launch-{os.getpid()}-{id(self)}"
        self.initial_goal = goal
        self.specification = {"goal": goal, "platform": "веб-приложение"}


def _record(*, run_id: str, files: tuple = (), verification: str = "passed") -> dict:
    """A completed run record: what the lifecycle layer reads to fill a version."""

    return {
        "run_id": run_id,
        "state": "COMPLETED",
        "status": "COMPLETED",
        "classification": {
            "outcome": "TERMINAL_SUCCESS",
            "is_success": True,
            "is_terminal": True,
        },
        "project_state": {
            "code_write": {
                "status": "applied" if files else "no_changes",
                "verification": verification,
                "files": [{"path": path, "action": "create"} for path in files],
            }
        },
        "provider_used": "mock",
        "model_used": "deterministic-offline",
        "updated_at": "2026-01-01T00:00:00Z",
    }


# --------------------------------------------------------------------------- #
# port discovery: the child's own output, then a real GET
# --------------------------------------------------------------------------- #


class PortDiscoveryTests(unittest.TestCase):
    """A port is read from reality, never assumed from a manifest.

    Three independent facts are proved here: every startup-banner form a real runtime
    prints is read; a port named inside a traceback is not (a crash is not a server);
    and no candidate is called ready without a real HTTP answer.
    """

    def _runner(self) -> LocalRunner:
        base = REPOSITORY / ".forge" / "test-project-launch-ports" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        return LocalRunner(store=ProjectStore(root=base / "projects"), previews=None)

    @staticmethod
    def _answering(*ports: int):
        allowed = {int(port) for port in ports}

        def probe(port: int) -> int:
            return 200 if int(port) in allowed else 0

        return probe

    def test_01_every_banner_form_is_read(self) -> None:
        forms = {
            "http://127.0.0.1:8790": 8790,
            "Адрес: http://127.0.0.1:8790": 8790,
            "Адрес: 127.0.0.1:8790": 8790,
            "listening on 127.0.0.1:9001": 9001,
            "listening 12345": 12345,
            "Local: http://localhost:5173": 5173,
            "Serving on port 8080": 8080,
            "Serving HTTP on 127.0.0.1 port 8000 (http://127.0.0.1:8000/) ...": 8000,
            "INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)": 8000,
            " * Running on :5000": 5000,
        }
        for line, port in forms.items():
            self.assertEqual(parse_banner_ports([line]), (port,), line)

    def test_02_the_owner_s_own_output_is_read(self) -> None:
        # The exact lines of the 409 the owner saw: the application *had* a server.
        lines = [
            "====================================================",
            "  Мейл АИ уже запущен",
            "  Адрес: http://127.0.0.1:8790",
            "====================================================",
        ]
        self.assertEqual(parse_banner_ports(lines), (8790,))

    def test_03_the_first_address_wins_and_duplicates_collapse(self) -> None:
        self.assertEqual(
            parse_banner_ports(
                ["Адрес: http://127.0.0.1:8790", "listening on 127.0.0.1:9000"]
            ),
            (8790, 9000),
            "the address the program chose is the first one it printed",
        )
        self.assertEqual(
            parse_banner_ports(["Local: http://localhost:5173", "http://127.0.0.1:5173"]),
            (5173,),
            "one port named twice is one candidate",
        )

    def test_04_ports_outside_the_usable_range_are_ignored(self) -> None:
        self.assertEqual(parse_banner_ports(["http://127.0.0.1:80"]), ())
        self.assertEqual(parse_banner_ports(["http://127.0.0.1:70000"]), ())
        # A mail server's port is not this application's own port.
        self.assertEqual(parse_banner_ports(["IMAP port 993 connected"]), ())

    def test_05_a_port_inside_a_traceback_is_ignored(self) -> None:
        traceback_lines = [
            "Traceback (most recent call last):",
            '  File "app.py", line 885, in main',
            "    server = ThreadingHTTPServer((HOST, PORT), Handler)",
            "OSError: [WinError 10048] 127.0.0.1:9999 busy",
        ]
        self.assertEqual(parse_banner_ports(traceback_lines), ())
        # ... and the child's next announcement (a real one) is still read.
        self.assertEqual(
            parse_banner_ports(traceback_lines + ["listening on 127.0.0.1:9100"]),
            (9100,),
        )

    def test_06_the_manifest_claim_is_a_claim(self) -> None:
        self.assertEqual(
            manifest_port_claim(
                "ЗАПУСК: start.bat запускает python app.py и открывает "
                "http://127.0.0.1:8765 в браузере. порт 993"
            ),
            8765,
            "a full loopback URL is the strongest claim; port 993 is the mail server",
        )
        self.assertEqual(manifest_port_claim("сервер на порту 8790"), 8790)
        self.assertEqual(manifest_port_claim("ничего не сказано"), 0)

    def test_07_only_a_real_answer_makes_a_candidate_ready(self) -> None:
        self.assertEqual(first_answering([8790], probe=self._answering(8790)), (8790, 200))
        # 401 and 500 are answers too: a server is there, the page just needs a login.
        self.assertEqual(first_answering([8790], probe=lambda port: 401), (8790, 401))
        self.assertEqual(first_answering([8790], probe=lambda port: 500), (8790, 500))
        self.assertEqual(first_answering([8790], probe=self._answering()), (0, 0))
        # A port outside the range is not even asked.
        asked: list[int] = []
        first_answering(
            [80, 70000, 8790],
            probe=lambda port: asked.append(int(port)) or 0,
        )
        self.assertEqual(asked, [8790])

        def broken(port: int) -> int:
            raise RuntimeError("the probe itself failed")

        self.assertEqual(first_answering([8790], probe=broken), (0, 0))

    def test_08_the_real_probe_reads_a_real_loopback_answer(self) -> None:
        server = _FakeAppServer()
        self.addCleanup(server.stop)
        self.assertEqual(probe_http(server.port), 200)
        self.assertEqual(probe_http(_closed_port()), 0)
        self.assertEqual(first_answering([_closed_port(), server.port]), (server.port, 200))

    def test_09_the_pid_s_sockets_are_enumerated_and_probed(self) -> None:
        runner = self._runner()
        with mock.patch.object(runner_module, "listening_ports", return_value=(9001,)):
            with mock.patch.object(ports_module, "probe_http", self._answering(9001)):
                self.assertEqual(runner._discover_port(4242), 9001)

    def test_10_a_descendant_s_socket_is_included(self) -> None:
        runner = self._runner()
        asked: list[set] = []
        with mock.patch.object(runner_module, "process_tree_pids", return_value=(5150,)):
            with mock.patch.object(
                runner_module,
                "listening_ports",
                side_effect=lambda pids: asked.append(set(pids)) or (9001,),
            ):
                with mock.patch.object(ports_module, "probe_http", self._answering(9001)):
                    self.assertEqual(runner._discover_port(4242), 9001)
        self.assertTrue(asked, "the socket table was never consulted")
        self.assertEqual(asked[0], {4242, 5150} if os.name == "nt" else {4242})

    def test_11_a_bound_socket_that_does_not_answer_is_not_ready(self) -> None:
        runner = self._runner()
        with mock.patch.object(runner_module, "listening_ports", return_value=(9002,)):
            with mock.patch.object(ports_module, "probe_http", self._answering()):
                self.assertEqual(
                    runner._discover_port(4242),
                    0,
                    "a bound socket is not a server until it answers",
                )

    def test_12_the_announced_port_is_preferred_and_still_verified(self) -> None:
        runner = self._runner()
        with mock.patch.object(runner_module, "listening_ports", return_value=(9001,)):
            with mock.patch.object(
                ports_module, "probe_http", self._answering(8790, 9001)
            ):
                self.assertEqual(
                    runner._discover_port(4242, (8790,)),
                    8790,
                    "the port the child printed is the one it chose",
                )
            with mock.patch.object(ports_module, "probe_http", self._answering()):
                self.assertEqual(
                    runner._discover_port(4242, (8790,)),
                    0,
                    "an announced port that nobody answers is not a server",
                )

    def test_13_the_already_running_check_reads_the_child_s_own_output(self) -> None:
        runner = self._runner()
        facts = runner._empty(PROJECT_ID)
        facts.stdout_log.feed("Мейл АИ уже запущен\n".encode("utf-8"))
        facts.stdout_log.feed("Адрес: http://127.0.0.1:8790\n".encode("utf-8"))
        with mock.patch.object(ports_module, "probe_http", self._answering(8790)):
            self.assertEqual(runner._answering_output_port(facts), 8790)
        with mock.patch.object(ports_module, "probe_http", self._answering()):
            self.assertEqual(
                runner._answering_output_port(facts),
                0,
                "a printed address nobody answers on is not a running application",
            )


# --------------------------------------------------------------------------- #
# the window integration, in isolation
# --------------------------------------------------------------------------- #


class AppWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        base = REPOSITORY / ".forge" / "test-project-launch-window" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.base = base.resolve()
        self.profiles = self.base / "profiles"

    def _opener(self, installed: dict):
        spawn = _RecordingSpawn()
        startfile = _RecordingStartfile()
        opener = AppWindowOpener(
            which=_FakeWhich(installed),
            spawn=spawn,
            startfile=startfile,
            # Nothing beyond what the test names is installed, whatever this host has.
            exists=lambda item: False,
            visible_windows=_FakeWindows(),
            profile_root=self.profiles,
            grace_seconds=0.01,
            title_wait_seconds=0.05,
        )
        return opener, spawn, startfile

    def _dead_opener(self, installed: dict, spawn):
        startfile = _RecordingStartfile()
        opener = AppWindowOpener(
            which=_FakeWhich(installed),
            spawn=spawn,
            startfile=startfile,
            exists=lambda item: False,
            # A dead plan owns no window: the table follows the spawn's liveness.
            visible_windows=_FakeWindows(alive=getattr(spawn, "is_alive", None)),
            profile_root=self.profiles,
            grace_seconds=0.05,
            title_wait_seconds=0.05,
        )
        return opener, spawn, startfile

    def test_01_chrome_is_first_and_the_argv_is_exact(self) -> None:
        opener, spawn, startfile = self._opener({"chrome": r"C:\fake\Google\Chrome\chrome.exe"})
        result = opener.open(PROJECT_ID, "http://127.0.0.1:4321/index.html")
        self.assertEqual(result.window, WINDOW_CHROME_APP)
        self.assertEqual(result.program.lower(), "chrome.exe")
        self.assertEqual(result.pid, 9876)
        self.assertEqual(result.failures, ())
        self.assertEqual(len(spawn.calls), 1)
        call = spawn.calls[0]
        self.assertEqual(
            call["vector"][:3],
            [
                r"C:\fake\Google\Chrome\chrome.exe",
                "--app=http://127.0.0.1:4321/index.html",
                f"--window-size={APP_WINDOW_SIZE}",
            ],
        )
        self.assertEqual(len(call["vector"]), 4)
        # The profile is this project's own, and a directory created for this launch only.
        profile = pathlib.Path(call["vector"][3][len("--user-data-dir=") :])
        self.assertTrue(
            call["vector"][3].startswith(f"--user-data-dir={self.profiles / PROJECT_ID}"),
            call["vector"][3],
        )
        self.assertTrue(profile.name.startswith("launch-"), profile.name)
        # No shell, no console, detached: a folder or a URL can never become a command.
        self.assertIs(call["kwargs"].get("shell"), False)
        self.assertTrue(profile.is_dir(), "the fresh per-launch profile really exists")
        self.assertEqual(startfile.urls, [], "the default browser is not touched")

    def test_02_edge_is_the_fallback_with_the_same_vector(self) -> None:
        opener, spawn, startfile = self._opener({"msedge": r"C:\fake\Edge\msedge.exe"})
        result = opener.open(PROJECT_ID, "http://127.0.0.1:6000/")
        self.assertEqual(result.window, WINDOW_EDGE_APP)
        self.assertEqual(result.program.lower(), "msedge.exe")
        vector = spawn.calls[0]["vector"]
        self.assertEqual(vector[0], r"C:\fake\Edge\msedge.exe")
        self.assertEqual(vector[1], "--app=http://127.0.0.1:6000/")
        self.assertEqual(vector[2], f"--window-size={APP_WINDOW_SIZE}")
        self.assertTrue(vector[3].startswith("--user-data-dir="))
        self.assertEqual(startfile.urls, [])

    def test_03_the_default_browser_is_the_last_resort(self) -> None:
        opener, spawn, startfile = self._opener({})
        result = opener.open(PROJECT_ID, "http://127.0.0.1:7000/x")
        self.assertEqual(result.window, WINDOW_DEFAULT_BROWSER)
        self.assertEqual(result.pid, 0)
        self.assertEqual(spawn.calls, [], "nothing is started as a process")
        self.assertEqual(startfile.urls, ["http://127.0.0.1:7000/x"])

    def test_04_only_a_loopback_url_can_be_opened(self) -> None:
        opener, spawn, startfile = self._opener({"chrome": r"C:\fake\chrome.exe"})
        for hostile in (
            "http://example.com/",
            "file:///C:/Windows/System32/calc.exe",
            "javascript:alert(1)",
            "https://127.0.0.1:5000/",
            "http://127.0.0.1:not-a-port/",
        ):
            with self.assertRaises(WindowRefusal, msg=hostile) as raised:
                opener.open(PROJECT_ID, hostile)
            self.assertIn("127.0.0.1", str(raised.exception))
        self.assertEqual(spawn.calls, [])
        self.assertEqual(startfile.urls, [])

    def test_05_the_profile_is_fresh_for_every_launch_of_one_project(self) -> None:
        opener, spawn, _startfile = self._opener({"chrome": r"C:\fake\chrome.exe"})
        other = "proj-ba9876543210"
        first = opener.profile_for(PROJECT_ID)
        again = opener.profile_for(PROJECT_ID)
        second = opener.profile_for(other)
        # A profile reused from an earlier attempt is what served the grey window, so
        # every launch gets its own; the project's directory is what keeps them together.
        self.assertNotEqual(first, again, "a second launch gets its own fresh profile")
        self.assertNotEqual(first, second, "two projects never share a profile")
        self.assertTrue(first.name.startswith("launch-"), first.name)
        self.assertEqual(first.parent.name, PROJECT_ID)
        self.assertEqual(first.parent.parent, self.profiles)
        self.assertTrue(first.is_dir() and again.is_dir())
        self.assertEqual(second.parent.name, other)
        opener.open(PROJECT_ID, "http://127.0.0.1:8000/")
        opener.open(PROJECT_ID, "http://127.0.0.1:8000/")
        self.assertEqual(len(spawn.calls), 2)
        self.assertNotEqual(
            spawn.calls[0]["vector"][3],
            spawn.calls[1]["vector"][3],
            "the second launch does not inherit the first profile",
        )

    def test_05b_only_the_newest_launch_profiles_are_kept(self) -> None:
        opener, _spawn, _startfile = self._opener({"chrome": r"C:\fake\chrome.exe"})
        made = [opener.profile_for(PROJECT_ID) for _index in range(PROFILE_RETENTION + 3)]
        kept = sorted(
            item.name
            for item in (self.profiles / PROJECT_ID).iterdir()
            if item.is_dir() and item.name.startswith("launch-")
        )
        self.assertEqual(len(kept), PROFILE_RETENTION, kept)
        self.assertIn(made[-1].name, kept, "the profile of this launch is never pruned")

    def test_06_an_unsafe_project_id_never_becomes_a_path(self) -> None:
        opener, spawn, _startfile = self._opener({"chrome": r"C:\fake\chrome.exe"})
        for hostile in ("", "../../evil", "proj-XYZ", "..\\..\\windows", None):
            with self.assertRaises(WindowRefusal, msg=repr(hostile)):
                opener.open(hostile, "http://127.0.0.1:5000/")
        self.assertEqual(spawn.calls, [])

    def test_07_a_browser_that_dies_is_retried_without_its_sandbox_before_the_next_one(
        self,
    ) -> None:
        # Chrome exits inside the grace period (a handoff or a refusal). The *same*
        # browser is retried once without its sandbox before Edge is considered at all.
        opener, spawn, startfile = self._dead_opener(
            {
                "chrome": r"C:\fake\chrome.exe",
                "msedge": r"C:\fake\msedge.exe",
            },
            spawn=_FlakySpawn(dead_calls=1),
        )
        result = opener.open(PROJECT_ID, "http://127.0.0.1:9100/")
        self.assertEqual(result.window, WINDOW_CHROME_APP)
        self.assertEqual(result.program.lower(), "chrome.exe")
        self.assertFalse(result.sandboxed)
        self.assertTrue(result.downgraded)
        self.assertEqual(len(spawn.calls), 2, "the retry of the same browser is the second plan")
        self.assertEqual(spawn.calls[0]["vector"][0], r"C:\fake\chrome.exe")
        self.assertEqual(spawn.calls[1]["vector"][0], r"C:\fake\chrome.exe")
        # The first attempt is exactly the old vector; the retry adds the documented pair.
        self.assertEqual(spawn.calls[0]["vector"][4:], [])
        self.assertEqual(spawn.calls[1]["vector"][4:], list(NO_SANDBOX_FLAGS))
        self.assertEqual(spawn.calls[1]["vector"][1], "--app=http://127.0.0.1:9100/")
        self.assertEqual(spawn.calls[1]["vector"][2], f"--window-size={APP_WINDOW_SIZE}")
        profile = pathlib.Path(spawn.calls[1]["vector"][3][len("--user-data-dir=") :])
        self.assertEqual(profile.parent, self.profiles / PROJECT_ID)
        self.assertTrue(profile.is_dir(), "the retry has its own fresh profile")
        self.assertNotEqual(spawn.calls[0]["vector"][3], spawn.calls[1]["vector"][3])
        self.assertEqual(startfile.urls, [], "the default browser is not needed")
        # The dead plan is remembered, and the downgrade is written down for the log.
        self.assertEqual(len(result.failures), 1, result.failures)
        self.assertIn("chrome.exe", result.failures[0])
        self.assertIn("exit", result.failures[0])
        self.assertTrue(
            any(SANDBOX_DOWNGRADE_NOTE in line for line in result.log_lines),
            result.log_lines,
        )
        payload = result.to_dict()
        self.assertFalse(payload["sandboxed"])
        self.assertTrue(payload["downgraded"])

    def test_08_when_every_chromium_plan_dies_the_default_browser_is_the_last_resort(
        self,
    ) -> None:
        opener, spawn, startfile = self._dead_opener(
            {"chrome": r"C:\fake\chrome.exe", "msedge": r"C:\fake\msedge.exe"},
            spawn=_FlakySpawn(dead_calls=4, code=1),
        )
        result = opener.open(PROJECT_ID, "http://127.0.0.1:9200/")
        self.assertEqual(result.window, WINDOW_DEFAULT_BROWSER)
        self.assertEqual(result.pid, 0)
        self.assertEqual(len(spawn.calls), 4, "each browser is tried with and without its sandbox")
        self.assertEqual(
            [pathlib.Path(call["vector"][0]).name for call in spawn.calls],
            ["chrome.exe", "chrome.exe", "msedge.exe", "msedge.exe"],
        )
        self.assertEqual(
            [call["vector"][4:] for call in spawn.calls],
            [[], list(NO_SANDBOX_FLAGS), [], list(NO_SANDBOX_FLAGS)],
        )
        self.assertEqual(startfile.urls, ["http://127.0.0.1:9200/"])
        # No Chromium plan produced a window, so no sandbox claim is made either.
        self.assertFalse(result.sandboxed)
        self.assertFalse(result.downgraded)

    def test_09_an_exit_zero_inside_the_grace_period_is_a_handoff_not_a_window(self) -> None:
        # Chrome's singleton answers a launch on a profile it already holds with exit 0
        # and no window of its own -- the measured way a launch reported a window while
        # the owner was looking at a stale one. It is a dead plan: the same browser is
        # retried without its sandbox, and only then the next browser.
        spawn = _DeadSpawn(code=0)
        opener, _spawn, startfile = self._dead_opener(
            {"chrome": r"C:\fake\chrome.exe", "msedge": r"C:\fake\msedge.exe"},
            spawn=spawn,
        )
        result = opener.open(PROJECT_ID, "http://127.0.0.1:9300/")
        self.assertEqual(result.window, WINDOW_DEFAULT_BROWSER)
        self.assertEqual(
            len(spawn.calls), 4, "a clean handoff is not a window; every plan is tried"
        )
        self.assertEqual(startfile.urls, ["http://127.0.0.1:9300/"])
        self.assertEqual(len(result.failures), 4, result.failures)
        self.assertIn("chrome.exe", result.failures[0])
        self.assertIn("exit 0", result.failures[0])

    def test_10_a_window_without_a_real_title_is_not_the_window(self) -> None:
        # Edge survives the grace period but shows the measured grey page whose title
        # bar fell back to the bare host. That is not the owner's application: the same
        # browser is retried with the documented relaxation, which renders the page.
        windows = _FakeWindows(
            titles={1001: [(11, "127.0.0.1_/")], 1002: [(12, "Почта АИ")]},
            default=(),
        )
        spawn = _AliveSpawn(first_pid=1001)
        startfile = _RecordingStartfile()
        opener = AppWindowOpener(
            which=_FakeWhich({"msedge": r"C:\fake\msedge.exe"}),
            spawn=spawn,
            startfile=startfile,
            exists=lambda item: False,
            visible_windows=windows,
            profile_root=self.profiles,
            grace_seconds=0.01,
            title_wait_seconds=0.05,
        )
        result = opener.open(PROJECT_ID, "http://127.0.0.1:9500/")
        self.assertEqual(result.window, WINDOW_EDGE_APP)
        self.assertTrue(result.downgraded)
        self.assertFalse(result.sandboxed)
        self.assertEqual(result.title, "Почта АИ")
        self.assertEqual(result.pid, 1002)
        self.assertEqual(len(spawn.calls), 2)
        self.assertEqual(spawn.calls[1]["vector"][4:], list(NO_SANDBOX_FLAGS))
        self.assertIn("127.0.0.1_/", result.failures[0], result.failures)
        self.assertIn("окно не подтвердилось", result.failures[0])
        self.assertEqual(spawn.stopped, [1001], "the grey window's process is ended")
        self.assertEqual(startfile.urls, [])

    def test_11_the_toggle_off_forbids_the_downgrade_and_refuses_honestly(self) -> None:
        spawn = _DeadSpawn(code=1)
        startfile = _RaisingStartfile()
        opener = AppWindowOpener(
            which=_FakeWhich(
                {"chrome": r"C:\fake\chrome.exe", "msedge": r"C:\fake\msedge.exe"}
            ),
            spawn=spawn,
            startfile=startfile,
            exists=lambda item: False,
            visible_windows=_FakeWindows(alive=spawn.is_alive),
            window_policy=lambda: False,
            profile_root=self.profiles,
            grace_seconds=0.01,
            title_wait_seconds=0.05,
        )
        with self.assertRaises(WindowRefusal) as raised:
            opener.open(PROJECT_ID, "http://127.0.0.1:9600/")
        self.assertEqual(len(spawn.calls), 2, "only the sandboxed attempt of each browser")
        for call in spawn.calls:
            self.assertNotIn(
                "--no-sandbox", call["vector"], "the owner forbade the unsandboxed retry"
            )
        self.assertIn("повтор без песочницы запрещён", str(raised.exception))
        self.assertTrue(raised.exception.failures)
        self.assertEqual(raised.exception.log_lines, ())
        self.assertEqual(startfile.urls, ["http://127.0.0.1:9600/"], "still the last resort")


# --------------------------------------------------------------------------- #
# the launcher's decision, in isolation
# --------------------------------------------------------------------------- #


class LauncherUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        base = REPOSITORY / ".forge" / "test-project-launch-unit" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        self.workspace = base / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.base = base
        self.spawn = _RecordingSpawn()
        self.startfile = _RecordingStartfile()
        self.windows = AppWindowOpener(
            which=_FakeWhich({"chrome": r"C:\fake\chrome.exe"}),
            spawn=self.spawn,
            startfile=self.startfile,
            exists=lambda item: False,
            visible_windows=_FakeWindows(),
            profile_root=base / "profiles",
            title_wait_seconds=0.05,
        )

    def _entry(self, files=("app.py",)):
        (self.workspace / "app.py").write_text("print('x')\n", encoding="utf-8")
        return resolve_entry([{"path": path} for path in files]).entry

    def _launcher(self, *, runner=None, previews=None, probe=None):
        return ProjectLauncher(
            runner=runner,
            previews=previews,
            windows=self.windows,
            probe=probe,
            readiness_timeout=0.5,
        )

    def _target(self, entry) -> RunTarget:
        return RunTarget(
            project_id=PROJECT_ID,
            workspace=self.workspace,
            entry=entry,
            project_path="projects-visible/x",
        )

    def test_07_no_files_refuses_before_anything_is_started(self) -> None:
        entry = self._entry()
        runner = _FakeRunner()
        with self.assertRaises(LaunchRefusal) as raised:
            self._launcher(runner=runner).launch(
                project_id=PROJECT_ID,
                target=self._target(entry),
                entry=entry,
                files=0,
            )
        self.assertIn("нет файлов", str(raised.exception))
        self.assertEqual(raised.exception.reason_code, "no_files")
        self.assertEqual(runner.started_calls, [])
        self.assertEqual(self.spawn.calls, [])

    def test_08_no_entry_point_refuses_before_anything_is_started(self) -> None:
        with self.assertRaises(LaunchRefusal) as raised:
            self._launcher(runner=_FakeRunner()).launch(
                project_id=PROJECT_ID,
                target=self._target(None),
                entry=None,
                files=2,
            )
        self.assertIn("Точка входа не найдена", str(raised.exception))
        self.assertEqual(self.spawn.calls, [])

    def test_09_a_desktop_kind_is_a_typed_refusal(self) -> None:
        entry = self._entry()
        runner = _FakeRunner()
        with mock.patch.object(
            launcher_module, "DESKTOP_ENTRY_KINDS", frozenset({entry.kind})
        ):
            with self.assertRaises(LaunchRefusal) as raised:
                self._launcher(runner=runner).launch(
                    project_id=PROJECT_ID,
                    target=self._target(entry),
                    entry=entry,
                    files=1,
                )
        self.assertEqual(raised.exception.reason_code, "desktop_entry_unsupported")
        self.assertEqual(runner.started_calls, [])
        self.assertEqual(self.spawn.calls, [])

    def test_10_the_approval_is_passed_through_and_a_second_launch_reuses_the_server(
        self,
    ) -> None:
        entry = self._entry()
        runner = _FakeRunner(
            started={"state": "RUNNING", "url": "http://127.0.0.1:5005/"},
        )
        launcher = self._launcher(runner=runner)

        first = launcher.launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
            confirm=True,
            digest="digest-shown-to-the-owner",
        )
        self.assertTrue(first.launched)
        self.assertFalse(first.reused)
        self.assertEqual(first.url, "http://127.0.0.1:5005/")
        self.assertEqual(first.window, WINDOW_CHROME_APP)
        self.assertEqual(first.entry_kind, entry.kind)
        self.assertEqual(len(runner.started_calls), 1)
        self.assertEqual(
            runner.started_calls[0]["digest"], "digest-shown-to-the-owner"
        )
        self.assertIs(runner.started_calls[0]["confirm"], True)

        # The remembered address is asked again before it is opened again: this machine
        # has the server on 5005, so the second click reuses it.
        with mock.patch.object(launcher_module, "probe_http", lambda port, **kw: 200):
            second = launcher.launch(
                project_id=PROJECT_ID,
                target=self._target(entry),
                entry=entry,
                files=1,
                confirm=True,
                digest="digest-shown-to-the-owner",
            )
        self.assertTrue(second.reused)
        self.assertEqual(second.url, first.url)
        self.assertEqual(len(runner.started_calls), 1, "the server was not started again")
        self.assertEqual(len(self.spawn.calls), 2, "the window was opened again")
        self.assertEqual(self.spawn.calls[0]["vector"][1], f"--app={first.url}")

    def test_10b_a_remembered_address_that_stopped_is_started_again(self) -> None:
        """A terminal run keeps the URL it proved; a later click must re-ask it.

        Without the second question, the first reused address would be reused forever:
        the window would open on a server that had since stopped, which is exactly the
        "blank page" this launch path exists to prevent.
        """

        entry = self._entry()
        runner = _FakeRunner(
            status={
                "state": "SUCCEEDED",
                "url": "http://127.0.0.1:5006/",
                "reused": True,
                "observed_port": 5006,
            },
            started={"state": "RUNNING", "url": "http://127.0.0.1:5007/"},
        )
        result = self._launcher(runner=runner).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
            confirm=True,
            digest="d",
        )
        self.assertEqual(len(runner.started_calls), 1, "nothing answered on 5006")
        self.assertFalse(result.reused)
        self.assertEqual(result.url, "http://127.0.0.1:5007/")
        self.assertEqual(result.observed_port, 5007)
        self.assertNotIn("5006", result.notice)

    def test_11_a_dead_process_is_refused_and_the_caller_never_sees_a_browser(self) -> None:
        # The old behaviour served the project's static page silently. That is exactly
        # what showed the owner a blank page: the run he asked for was never reported.
        # Now the page is *offered* in the refusal and nothing is opened.
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        entry = self._entry()
        runner = _FakeRunner(
            started={
                "state": "FAILED",
                "url": "",
                "exit_code": 1,
                "reason": "Address already in use",
                "log": {"lines": ["OSError: [WinError 10048] port busy"]},
            },
        )
        previews = _FakePreviews()
        with self.assertRaises(LaunchRefusal) as raised:
            self._launcher(
                runner=runner, previews=previews, probe=_answering(5002)
            ).launch(
                project_id=PROJECT_ID,
                target=self._target(entry),
                entry=entry,
                files=2,
                confirm=True,
                digest="d",
            )
        refusal = raised.exception
        self.assertIn(NOT_A_SERVER_MESSAGE, str(refusal))
        self.assertIn("кодом 1", str(refusal))
        self.assertEqual(refusal.reason_code, "port_not_opened")
        self.assertEqual(refusal.facts["run_state"], "FAILED")
        self.assertEqual(refusal.facts["exit_code"], 1)
        self.assertEqual(
            refusal.facts["log_tail"], ["OSError: [WinError 10048] port busy"]
        )
        self.assertTrue(refusal.facts["fallback_available"])
        self.assertEqual(refusal.facts["fallback_url"], "http://127.0.0.1:5002/index.html")
        self.assertEqual(refusal.facts["fallback_page"], "index.html")
        self.assertEqual(
            self.spawn.calls, [], "a portless refusal must open no browser at all"
        )
        self.assertEqual(len(previews.started_calls), 1, "the offer has a real URL")

    def test_11b_the_static_page_is_shown_only_on_an_explicit_request(self) -> None:
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        entry = self._entry()
        runner = _FakeRunner(
            started={
                "state": "FAILED",
                "url": "",
                "exit_code": 1,
                "log": {"lines": ["Address already in use"]},
            },
        )
        previews = _FakePreviews()
        result = self._launcher(
            runner=runner, previews=previews, probe=_answering(5002)
        ).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=2,
            confirm=True,
            digest="d",
            static_fallback=True,
        )
        self.assertTrue(result.fallback)
        self.assertTrue(result.launched)
        self.assertEqual(result.url, "http://127.0.0.1:5002/index.html")
        self.assertIn("По запросу показана статическая страница index.html", result.notice)
        self.assertEqual(self.spawn.calls[0]["vector"][1], f"--app={result.url}")

    def test_12_a_dead_process_with_no_static_page_is_a_plain_refusal(self) -> None:
        entry = self._entry()
        runner = _FakeRunner(
            started={
                "state": "FAILED",
                "url": "",
                "exit_code": 2,
                "reason": "boom",
                "log": {"lines": ["boom"]},
            },
        )
        with self.assertRaises(LaunchRefusal) as raised:
            self._launcher(runner=runner, previews=_FakePreviews()).launch(
                project_id=PROJECT_ID,
                target=self._target(entry),
                entry=entry,
                files=1,
                confirm=True,
                digest="d",
            )
        self.assertIn(NOT_A_SERVER_MESSAGE, str(raised.exception))
        self.assertIn("кодом 2", str(raised.exception))
        self.assertEqual(raised.exception.reason_code, "port_not_opened")
        self.assertEqual(raised.exception.facts["run_state"], "FAILED")
        self.assertEqual(raised.exception.facts["exit_code"], 2)
        self.assertEqual(raised.exception.facts["log_tail"], ["boom"])
        self.assertFalse(raised.exception.facts["fallback_available"])
        self.assertEqual(self.spawn.calls, [], "a refusal opens no window")

    def test_12b_a_portless_url_is_never_opened(self) -> None:
        # A supervisor that handed back a URL without a port must not become a browser
        # window at port 80: the launch refuses and names the reason.
        entry = self._entry()
        runner = _FakeRunner(started={"state": "RUNNING", "url": "http://127.0.0.1/"})
        with self.assertRaises(LaunchRefusal) as raised:
            self._launcher(runner=runner).launch(
                project_id=PROJECT_ID,
                target=self._target(entry),
                entry=entry,
                files=1,
                confirm=True,
                digest="d",
            )
        self.assertIn("без порта", str(raised.exception))
        self.assertIn(NOT_A_SERVER_MESSAGE, str(raised.exception))
        self.assertEqual(raised.exception.facts["url"], "http://127.0.0.1/")
        self.assertEqual(self.spawn.calls, [])
        self.assertEqual(self.startfile.urls, [])

    def test_12c_a_prompt_like_log_is_diagnosed_as_waiting_for_console_input(self) -> None:
        entry = self._entry()
        runner = _FakeRunner(
            started={
                "state": "FAILED",
                "url": "",
                "exit_code": 3,
                "log": {
                    "path": ".forge/projects/proj-0123456789ab/run.log",
                    "lines": ["Выбрать действие:", "--- stderr ---", "EOFError"],
                },
            },
        )
        with self.assertRaises(LaunchRefusal) as raised:
            self._launcher(runner=runner).launch(
                project_id=PROJECT_ID,
                target=self._target(entry),
                entry=entry,
                files=1,
                confirm=True,
                digest="d",
            )
        refusal = raised.exception
        self.assertIn(CONSOLE_INPUT_MESSAGE, str(refusal))
        self.assertIn(NOT_A_SERVER_MESSAGE, str(refusal))
        self.assertIn(".forge/projects/proj-0123456789ab/run.log", str(refusal))
        self.assertTrue(refusal.facts["waiting_for_input"])
        self.assertEqual(refusal.reason_code, "console_input_required")
        self.assertEqual(refusal.facts["log_tail"][0], "Выбрать действие:")
        self.assertEqual(self.spawn.calls, [], "a prompt is diagnosed, never shown")

    def test_12d_the_prompt_detector_reads_the_owner_s_own_words(self) -> None:
        for lines in (
            ["Выбрать действие:"],
            ["Введите пароль"],
            ["Press Enter to continue"],
            ["input your choice"],
        ):
            self.assertTrue(looks_like_console_prompt(lines), lines)
        self.assertFalse(looks_like_console_prompt(["listening on 127.0.0.1:8765"]))
        self.assertFalse(looks_like_console_prompt(["--- stderr ---"]))
        self.assertFalse(looks_like_console_prompt([]))
        # A real crash is a crash, not a prompt: the live 409 evidence showed
        # "Traceback (most recent call last):" being mistaken for a prompt.
        self.assertFalse(
            looks_like_console_prompt(
                [
                    "Traceback (most recent call last):",
                    '  File "app.py", line 885, in main',
                    "    server = ThreadingHTTPServer((HOST, PORT), Handler)",
                    "PermissionError: [WinError 10013] access denied",
                ]
            )
        )

    def test_13_a_running_static_preview_is_reused(self) -> None:
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        entry = resolve_entry([{"path": "index.html"}]).entry
        previews = _FakePreviews(
            running={"running": True, "url": "http://127.0.0.1:5010/index.html"}
        )
        result = self._launcher(previews=previews, probe=_answering(5010)).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
        )
        self.assertTrue(result.reused)
        self.assertEqual(result.url, "http://127.0.0.1:5010/index.html")
        self.assertEqual(previews.started_calls, [], "the preview was not restarted")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_13a_a_preview_url_that_stopped_answering_is_never_opened(self) -> None:
        # The registry still lists a preview session, but nothing answers on its port
        # any more: that address is stale. Opening it is the empty grey window the
        # owner saw, so the launch serves the page again and opens the new address.
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        entry = resolve_entry([{"path": "index.html"}]).entry
        stale = "http://127.0.0.1:5011/index.html"
        fresh = "http://127.0.0.1:5012/index.html"
        previews = _FakePreviews(
            running={"running": True, "url": stale},
            started={"running": True, "url": fresh, "port": 5012},
        )
        result = self._launcher(previews=previews, probe=_answering(5012)).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
        )
        self.assertEqual(result.url, fresh)
        self.assertFalse(result.reused, "a restarted preview is not a reused one")
        self.assertEqual(previews.stopped_calls, [PROJECT_ID], "the stale session is dropped")
        self.assertEqual(len(previews.started_calls), 1, "the page is served again")
        self.assertEqual(
            [call["vector"][1] for call in self.spawn.calls],
            ["--app=" + fresh],
            "only the address that answered is opened",
        )
        self.assertNotIn(
            stale,
            [call["vector"][1] for call in self.spawn.calls],
            "the stale URL is not opened",
        )

    def test_13b_a_preview_that_never_answers_is_a_refusal_not_a_window(self) -> None:
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        entry = resolve_entry([{"path": "index.html"}]).entry
        previews = _FakePreviews(
            started={"running": True, "url": "http://127.0.0.1:5013/index.html", "port": 5013}
        )
        with self.assertRaises(LaunchRefusal) as raised:
            self._launcher(previews=previews, probe=_answering()).launch(
                project_id=PROJECT_ID,
                target=self._target(entry),
                entry=entry,
                files=1,
            )
        self.assertEqual(raised.exception.reason_code, "preview_failed")
        self.assertIn("5013", str(raised.exception))
        self.assertEqual(self.spawn.calls, [], "no window is opened on nothing")

    def test_13c_a_url_without_a_port_is_never_opened(self) -> None:
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        entry = resolve_entry([{"path": "index.html"}]).entry
        previews = _FakePreviews(
            running={"running": True, "url": "http://127.0.0.1/index.html"},
            started={"running": True, "url": "http://127.0.0.1/index.html"},
        )
        with self.assertRaises(LaunchRefusal) as raised:
            self._launcher(previews=previews, probe=_answering(80)).launch(
                project_id=PROJECT_ID,
                target=self._target(entry),
                entry=entry,
                files=1,
            )
        self.assertEqual(raised.exception.reason_code, "preview_failed")
        self.assertEqual(self.spawn.calls, [], "a portless URL never becomes a window")
        self.assertEqual(self.startfile.urls, [], "and not the default browser either")

    def test_13d_a_dead_window_plan_is_written_into_the_project_s_own_launch_log(self) -> None:
        # Chrome exits inside the grace period (a handoff or a refusal); the same browser
        # opens the window without its sandbox. That is a success *with a story*, and the
        # story goes into the project's launch.log -- the file the next press and the
        # desktop shortcut both read.
        spawn = _FlakySpawn(dead_calls=1)
        self.windows = AppWindowOpener(
            which=_FakeWhich(
                {"chrome": r"C:\fake\chrome.exe", "msedge": r"C:\fake\msedge.exe"}
            ),
            spawn=spawn,
            startfile=self.startfile,
            exists=lambda item: False,
            visible_windows=_FakeWindows(alive=spawn.is_alive),
            profile_root=self.base / "profiles",
            grace_seconds=0.05,
            title_wait_seconds=0.05,
        )
        self._entry()
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        # A static page: the launch route's page branch, which opens the window without
        # starting a process, so this test is about the window and its log only.
        entry = resolve_entry([{"path": "index.html"}]).entry
        previews = _FakePreviews(
            running={"running": True, "url": "http://127.0.0.1:8790/"},
            started={"running": True, "url": "http://127.0.0.1:8790/"},
        )
        result = self._launcher(previews=previews, probe=_answering(8790)).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
        )
        self.assertEqual(result.window, WINDOW_CHROME_APP)
        self.assertTrue(result.window_downgraded)
        self.assertFalse(result.window_sandboxed)
        text = (self.workspace / "launch.log").read_text(encoding="utf-8")
        self.assertIn("Forge:", text, text)
        self.assertIn("chrome.exe", text, text)
        self.assertIn("exit", text, text)
        self.assertIn(SANDBOX_DOWNGRADE_NOTE, text, text)

    def test_13e_the_launch_payload_records_which_attempt_really_opened_the_window(
        self,
    ) -> None:
        spawn = _FlakySpawn(dead_calls=1, code=0)
        self.windows = AppWindowOpener(
            which=_FakeWhich({"chrome": r"C:\fake\chrome.exe"}),
            spawn=spawn,
            startfile=self.startfile,
            exists=lambda item: False,
            visible_windows=_FakeWindows(alive=spawn.is_alive),
            profile_root=self.base / "profiles",
            grace_seconds=0.05,
            title_wait_seconds=0.05,
        )
        self._entry()
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        entry = resolve_entry([{"path": "index.html"}]).entry
        previews = _FakePreviews(
            running={"running": True, "url": "http://127.0.0.1:8791/"},
            started={"running": True, "url": "http://127.0.0.1:8791/"},
        )
        result = self._launcher(previews=previews, probe=_answering(8791)).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
        )
        payload = result.to_dict()
        self.assertEqual(payload["window"], WINDOW_CHROME_APP)
        self.assertTrue(payload["downgraded"])
        self.assertFalse(payload["sandboxed"])
        text = (self.workspace / "launch.log").read_text(encoding="utf-8")
        self.assertIn(SANDBOX_DOWNGRADE_NOTE, text, text)

    def test_13f_when_no_plan_works_the_refusal_is_typed_and_carries_the_log_tail(
        self,
    ) -> None:
        # Every browser plan dies and the machine's default browser refuses too. The
        # answer is a typed refusal with the dead plans and the launch.log tail -- the
        # same facts a success would show -- never a claimed window.
        spawn = _DeadSpawn(code=1)
        self.windows = AppWindowOpener(
            which=_FakeWhich(
                {"chrome": r"C:\fake\chrome.exe", "msedge": r"C:\fake\msedge.exe"}
            ),
            spawn=spawn,
            startfile=_RaisingStartfile(),
            exists=lambda item: False,
            visible_windows=_FakeWindows(alive=spawn.is_alive),
            profile_root=self.base / "profiles",
            grace_seconds=0.05,
            title_wait_seconds=0.05,
        )
        self._entry()
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        entry = resolve_entry([{"path": "index.html"}]).entry
        previews = _FakePreviews(
            running={"running": True, "url": "http://127.0.0.1:8792/"},
            started={"running": True, "url": "http://127.0.0.1:8792/"},
        )
        with self.assertRaises(LaunchRefusal) as raised:
            self._launcher(previews=previews, probe=_answering(8792)).launch(
                project_id=PROJECT_ID,
                target=self._target(entry),
                entry=entry,
                files=1,
            )
        refusal = raised.exception
        self.assertEqual(refusal.code, "project_launch_refused")
        self.assertEqual(refusal.reason_code, "window_refused")
        self.assertEqual(refusal.facts["refusal_reason"], "window_refused")
        self.assertEqual(len(refusal.facts["window_failures"]), 4, refusal.facts)
        tail = "\n".join(refusal.facts["launch_log_tail"])
        self.assertIn(SANDBOX_DOWNGRADE_NOTE, tail)
        self.assertIn("chrome.exe", tail)

    def test_14_the_notice_names_both_ports_when_the_manifest_differs(self) -> None:
        # The manifest claims 8765; the application chose 8790 and was already running.
        # Both facts are said out loud, and the window opens the port that answered.
        entry = self._entry()
        runner = _FakeRunner(
            started={
                "state": "SUCCEEDED",
                "url": "http://127.0.0.1:8790/",
                "observed_port": 8790,
                "reused": True,
                "exit_code": 0,
            },
        )
        result = self._launcher(runner=runner).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
            confirm=True,
            digest="d",
            manifest_port=8765,
        )
        self.assertTrue(result.launched)
        self.assertTrue(result.reused, "an already-running server is a success")
        self.assertEqual(result.url, "http://127.0.0.1:8790/")
        self.assertEqual(result.observed_port, 8790)
        self.assertIn(REUSED_SERVER_NOTE, result.notice)
        self.assertIn(
            MANIFEST_PORT_NOTE.format(manifest=8765, observed=8790), result.notice
        )
        self.assertIn("8765", result.notice)
        self.assertIn("8790", result.notice)
        payload = result.to_dict()
        self.assertEqual(payload["observed_port"], 8790)
        self.assertEqual(payload["notice"], result.notice)
        self.assertEqual(self.spawn.calls[0]["vector"][1], "--app=http://127.0.0.1:8790/")

    def test_15_the_notice_is_silent_when_the_manifest_and_reality_agree(self) -> None:
        entry = self._entry()
        runner = _FakeRunner(
            started={
                "state": "RUNNING",
                "url": "http://127.0.0.1:8765/",
                "observed_port": 8765,
                "exit_code": None,
            },
        )
        result = self._launcher(runner=runner).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
            confirm=True,
            digest="d",
            manifest_port=8765,
        )
        self.assertFalse(result.reused)
        self.assertEqual(result.observed_port, 8765)
        self.assertEqual(result.notice, "", "nothing extra to say when the two agree")
        self.assertNotIn("notice", result.to_dict())

    def test_16_the_observed_port_falls_back_to_the_opened_url(self) -> None:
        # A supervisor that reports only the URL still yields the port it opened, so
        # the payload never claims a port it did not open.
        entry = self._entry()
        runner = _FakeRunner(started={"state": "RUNNING", "url": "http://127.0.0.1:5007/"})
        result = self._launcher(runner=runner).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
            confirm=True,
            digest="d",
        )
        self.assertEqual(result.observed_port, 5007)


# --------------------------------------------------------------------------- #
# the route, against the real FastAPI surface
# --------------------------------------------------------------------------- #


class LaunchRouteTests(unittest.TestCase):
    """The real application, a real runner and a real preview registry.

    The OS window opener is the only injected object; the project itself is started by
    the same :class:`app.projects.runner.LocalRunner` and served by the same
    :class:`app.projects.preview.PreviewRegistry` the shipped process composes.
    """

    def setUp(self) -> None:
        base = REPOSITORY / ".forge" / "test-project-launch" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        self.base = base.resolve()
        self.root = self.base / "projects"
        self.discovery_root = self.base / "discovery"
        self.run_root = self.base / "runs"
        self.run_workspace = self.base / "run-workspace"
        for directory in (
            self.root,
            self.discovery_root,
            self.run_root,
            self.run_workspace,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        previous = os.environ.get(PROJECTS_ROOT_ENV_VAR)
        os.environ[PROJECTS_ROOT_ENV_VAR] = str(self.root)

        def _restore() -> None:
            if previous is None:
                os.environ.pop(PROJECTS_ROOT_ENV_VAR, None)
            else:
                os.environ[PROJECTS_ROOT_ENV_VAR] = previous

        self.addCleanup(_restore)
        self.store = ProjectStore(root=self.root)
        self.projects = ProjectService(self.store)
        self.discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))
        self.spawn = _RecordingSpawn()
        self.startfile = _RecordingStartfile()
        self.window_opener = AppWindowOpener(
            which=_FakeWhich({"chrome": r"C:\fake\Google\Chrome\chrome.exe"}),
            spawn=self.spawn,
            startfile=self.startfile,
            exists=lambda item: False,
            visible_windows=_FakeWindows(),
            profile_root=self.base / APP_PROFILE_DIRECTORY,
        )

    # ------------------------------------------------------------------ helpers
    def _client(self):
        from fastapi.testclient import TestClient

        service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.run_root),
            workspace=Workspace(self.run_workspace),
            authorized_project_id=WORKSPACE_PROJECT_ID,
        )
        self.app = create_api_app(
            service,
            discovery_service=self.discovery,
            project_service=self.projects,
            window_opener=self.window_opener,
        )
        self.addCleanup(self._stop_everything)
        return TestClient(self.app)

    def _stop_everything(self) -> None:
        runner = getattr(self.app.state, "forge_runner", None)
        if runner is not None:
            try:
                runner.stop_all()
            except Exception:  # noqa: BLE001 - shutdown is best effort
                pass
        previews = getattr(self.app.state, "forge_previews", None)
        if previews is not None:
            try:
                previews.stop_all()
            except Exception:  # noqa: BLE001 - shutdown is best effort
                pass

    def _write_run_file(self, relative: str, data: bytes) -> pathlib.Path:
        target = self.run_workspace.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _project(self, *, files: dict | None = None, goal: str = GOAL):
        files = dict(files or {})
        for relative, data in files.items():
            self._write_run_file(relative, data)
        project = self.projects.create_from_discovery(_Session(goal))
        self.projects.confirm(project.project_id)
        return self.projects.attach_run(
            project.project_id,
            _record(run_id=f"run-{project.project_id}", files=tuple(files)),
            source_root=self.run_workspace,
        )

    def _launch(self, client, project_id: str, body: dict | None = None):
        return client.post(f"/api/projects/{project_id}/launch", json=body or {})

    def _wait(self, call, predicate, label: str, timeout: float = 30.0):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = call()
            if predicate(last):
                return last
            time.sleep(0.2)
        self.fail(f"timed out waiting for {label}; last answer was {last!r}")

    # -------------------------------------------------------------------- tests
    def test_01_a_static_project_is_served_and_opened_in_its_own_window(self) -> None:
        client = self._client()
        project = self._project(files={"index.html": STATIC_PAGE.encode("utf-8")})
        detail = client.get(f"/api/projects/{project.project_id}").json()
        self.assertEqual(detail["entrypoint"]["kind"], "static_html")
        digest = str(detail["entrypoint"]["digest"])

        answer = self._launch(
            client,
            project.project_id,
            {"confirm": True, "entrypoint_digest": digest},
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        for field in (
            "launched",
            "url",
            "window",
            "pid",
            "reused",
            "project_id",
            "entry_kind",
        ):
            self.assertIn(field, body, body)
        self.assertTrue(body["launched"])
        self.assertEqual(body["project_id"], project.project_id)
        self.assertEqual(body["entry_kind"], "static_html")
        self.assertEqual(body["window"], WINDOW_CHROME_APP)
        self.assertEqual(body["pid"], 9876)
        self.assertFalse(body["reused"])
        url = str(body["url"])
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)

        # The URL really answers with this project's page.
        with urllib.request.urlopen(url, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"LAUNCH-STATIC-OK", response.read())

        # The exact window argv: app mode, the fixed size, a fresh profile of this
        # project's own -- never one an earlier launch left behind.
        call = self.spawn.calls[-1]
        profile_root = self.base / APP_PROFILE_DIRECTORY / project.project_id
        self.assertEqual(
            call["vector"][:3],
            [
                r"C:\fake\Google\Chrome\chrome.exe",
                f"--app={url}",
                f"--window-size={APP_WINDOW_SIZE}",
            ],
        )
        self.assertEqual(len(call["vector"]), 4)
        self.assertTrue(
            call["vector"][3].startswith(f"--user-data-dir={profile_root}"),
            call["vector"][3],
        )
        profile = pathlib.Path(call["vector"][3][len("--user-data-dir=") :])
        self.assertTrue(profile.is_dir(), "the fresh profile really exists")
        self.assertIs(call["kwargs"].get("shell"), False)
        self.assertEqual(self.startfile.urls, [])

    def test_02_a_second_launch_reuses_the_running_server_and_reopens_the_window(self) -> None:
        client = self._client()
        project = self._project(files={"index.html": STATIC_PAGE.encode("utf-8")})
        digest = client.get(f"/api/projects/{project.project_id}").json()["entrypoint"]["digest"]

        first = self._launch(client, project.project_id, {"confirm": True, "entrypoint_digest": digest})
        self.assertEqual(first.status_code, 200, first.text)
        first_body = first.json()

        second = self._launch(client, project.project_id, {"confirm": True, "entrypoint_digest": digest})
        self.assertEqual(second.status_code, 200, second.text)
        second_body = second.json()
        self.assertTrue(second_body["reused"])
        self.assertEqual(second_body["url"], first_body["url"])
        self.assertEqual(second_body["window"], WINDOW_CHROME_APP)
        self.assertEqual(len(self.spawn.calls), 2, "the window was opened again, once per click")
        # One preview server, not two: the card's preview block names the same port.
        preview = client.get(f"/api/projects/{project.project_id}/preview").json()
        self.assertTrue(preview["running"])
        self.assertEqual(preview["url"], first_body["url"])

    def test_03_the_project_process_is_started_with_the_approval_and_its_url_is_opened(
        self,
    ) -> None:
        client = self._client()
        project = self._project(files={"app.py": SERVING.encode("utf-8")})
        detail = client.get(f"/api/projects/{project.project_id}").json()
        self.assertEqual(detail["entrypoint"]["kind"], "python_script")
        digest = str(detail["entrypoint"]["digest"])
        self.assertTrue(digest)

        answer = self._launch(
            client,
            project.project_id,
            {"confirm": True, "entrypoint_digest": digest},
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual(body["entry_kind"], "python_script")
        self.assertFalse(body["reused"])
        url = str(body["url"])
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)

        with urllib.request.urlopen(url, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"LAUNCH-APP-OK", response.read())
        self.assertEqual(self.spawn.calls[-1]["vector"][1], f"--app={url}")

        # The second click: same process, same URL, a new window.
        run = client.get(f"/api/projects/{project.project_id}/run").json()
        pid = int(run["pid"])
        opened_port = int(url.rsplit(":", 1)[1].split("/", 1)[0])
        self.assertEqual(run["observed_port"], opened_port, run)
        self.assertFalse(run["reused"], "this launch started its own server")
        again = self._launch(
            client,
            project.project_id,
            {"confirm": True, "entrypoint_digest": digest},
        )
        self.assertEqual(again.status_code, 200, again.text)
        again_body = again.json()
        self.assertTrue(again_body["reused"])
        self.assertEqual(again_body["url"], url)
        self.assertEqual(again_body["observed_port"], opened_port)
        self.assertEqual(
            int(client.get(f"/api/projects/{project.project_id}/run").json()["pid"]),
            pid,
            "the second click must not start a second process",
        )
        self.assertEqual(len(self.spawn.calls), 2)

    def test_04_a_launch_without_the_approval_is_refused_and_starts_nothing(self) -> None:
        client = self._client()
        project = self._project(files={"app.py": SERVING.encode("utf-8")})
        # No digest, no confirm: the runner's own boundary refuses the start.
        answer = self._launch(client, project.project_id, {})
        self.assertEqual(answer.status_code, 409, answer.text)
        detail = answer.json()["detail"]
        self.assertEqual(detail["code"], "project_launch_refused")
        self.assertIn("не одобрен", detail["error"])
        status = client.get(f"/api/projects/{project.project_id}/run").json()
        self.assertEqual(status["state"], "REFUSED", status)
        self.assertFalse(status["running"])
        self.assertEqual(self.spawn.calls, [], "a refused launch opens no window")

    def test_05_no_files_or_no_entry_is_a_plain_refusal_and_opens_no_window(self) -> None:
        client = self._client()
        empty = self._project(files={})
        answer = self._launch(client, empty.project_id, {"confirm": True})
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(answer.json()["detail"]["code"], "project_launch_refused")
        self.assertIn("нет файлов", answer.json()["detail"]["error"])

        nothing_to_run = self._project(files={"data.json": b'{"a": 1}\n'})
        answer = self._launch(client, nothing_to_run.project_id, {"confirm": True})
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertIn("Точка входа не найдена", answer.json()["detail"]["error"])
        self.assertEqual(self.spawn.calls, [])

    def test_06_an_unknown_project_is_404_and_a_bad_body_is_400(self) -> None:
        client = self._client()
        project = self._project(files={"index.html": STATIC_PAGE.encode("utf-8")})
        missing = self._launch(client, "proj-000000000000", {"confirm": True})
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"]["code"], "unknown_project")

        malformed = client.post(
            f"/api/projects/{project.project_id}/launch",
            json={"confirm": "yes", "entrypoint_digest": 7},
        )
        self.assertEqual(malformed.status_code, 400, malformed.text)
        self.assertEqual(self.spawn.calls, [])

    def test_07_an_archived_project_cannot_be_launched(self) -> None:
        client = self._client()
        project = self._project(files={"index.html": STATIC_PAGE.encode("utf-8")})
        archived = client.post(f"/api/projects/{project.project_id}/archive")
        self.assertEqual(archived.status_code, 200, archived.text)
        answer = self._launch(client, project.project_id, {"confirm": True})
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(
            answer.json()["detail"]["code"], "illegal_project_transition", answer.text
        )
        self.assertEqual(self.spawn.calls, [])

    def test_08_the_default_browser_is_used_when_no_chromium_browser_exists(self) -> None:
        # A second composition whose machine has neither Chrome nor Edge: the launch still
        # opens the project, through the default browser, and says which one happened.
        self.window_opener = AppWindowOpener(
            which=_FakeWhich({}),
            spawn=self.spawn,
            startfile=self.startfile,
            exists=lambda item: False,
            visible_windows=_FakeWindows(),
            profile_root=self.base / APP_PROFILE_DIRECTORY,
        )
        client = self._client()
        project = self._project(files={"index.html": STATIC_PAGE.encode("utf-8")})
        answer = self._launch(client, project.project_id, {"confirm": True})
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual(body["window"], WINDOW_DEFAULT_BROWSER)
        self.assertEqual(body["pid"], 0)
        self.assertEqual(self.startfile.urls, [body["url"]])
        self.assertEqual(self.spawn.calls, [])

    def test_09_the_child_and_the_probe_are_created_without_a_console_window(self) -> None:
        """The console window the owner saw is a process-creation flag, not a mystery.

        Forge runs under ``pythonw`` (no console), so a child created without
        ``CREATE_NO_WINDOW`` gets a fresh console from Windows. Both the project's own
        process and the containment probe must carry the flag; the child keeps its own
        process group and never gets a *new console*.
        """

        client = self._client()
        project = self._project(files={"app.py": SERVING.encode("utf-8")})
        digest = client.get(f"/api/projects/{project.project_id}").json()["entrypoint"]["digest"]

        real_popen = subprocess.Popen
        calls: list[dict] = []

        def recording_popen(*args, **kwargs):
            calls.append(
                {
                    "argv": [str(part) for part in (args[0] if args else ())],
                    "flags": int(kwargs.get("creationflags") or 0),
                }
            )
            return real_popen(*args, **kwargs)

        with mock.patch.object(subprocess, "Popen", side_effect=recording_popen):
            answer = self._launch(
                client,
                project.project_id,
                {"confirm": True, "entrypoint_digest": digest},
            )
        self.assertEqual(answer.status_code, 200, answer.text)

        no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
        self.assertTrue(no_window, "this proof is about a Windows host")
        new_console = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0) or 0)
        self.assertTrue(calls, "the launch created at least the project's process")
        for call in calls:
            self.assertTrue(
                call["flags"] & no_window,
                f"a created process has no CREATE_NO_WINDOW: {call}",
            )
            if new_console:
                self.assertFalse(
                    call["flags"] & new_console,
                    f"a created process asked for a new console: {call}",
                )
        child = [
            call
            for call in calls
            if any(str(part).endswith("app.py") for part in call["argv"])
        ]
        self.assertTrue(child, f"the project's own process was not recorded: {calls}")
        self.assertTrue(
            child[0]["flags"] & subprocess.CREATE_NEW_PROCESS_GROUP,
            "the child keeps its own process group",
        )

    def test_10_a_console_app_is_refused_with_its_log_and_offered_as_a_static_choice(
        self,
    ) -> None:
        """The owner's real case, end to end: a project that never becomes a server.

        The current version's entry point is ``app.py``; it writes a prompt-like line
        and exits. The launch must answer ``409 project_launch_refused`` with the run's
        state, exit code and redacted log tail, must open **no** browser, and must offer
        the static page only as an explicit second request.
        """

        client = self._client()
        project = self._project(files={"app.py": PROMPT_APP.encode("utf-8")})
        # The current version's manifest names only ``app.py`` (as the owner's project
        # v2 does); the stale ``index.html`` from an earlier version still sits in the
        # folder. That is exactly the page the old code opened silently.
        workspace = self.store.workspace_path(project.project_id, create=True)
        pathlib.Path(workspace, "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        detail = client.get(f"/api/projects/{project.project_id}").json()
        self.assertEqual(detail["entrypoint"]["kind"], "python_script")
        digest = str(detail["entrypoint"]["digest"])

        answer = self._launch(
            client,
            project.project_id,
            {"confirm": True, "entrypoint_digest": digest},
        )
        self.assertEqual(answer.status_code, 409, answer.text)
        detail = answer.json()["detail"]
        self.assertEqual(detail["code"], "project_launch_refused")
        self.assertIn(NOT_A_SERVER_MESSAGE, detail["error"])
        self.assertIn(CONSOLE_INPUT_MESSAGE, detail["error"])
        self.assertEqual(detail["run_state"], "FAILED")
        self.assertEqual(detail["exit_code"], 3)
        self.assertTrue(detail["waiting_for_input"])
        self.assertIn("Введите пароль:", detail["log_tail"])
        self.assertTrue(detail["fallback_available"])
        fallback_url = str(detail["fallback_url"])
        self.assertTrue(fallback_url.startswith("http://127.0.0.1:"), fallback_url)
        self.assertTrue(fallback_url.endswith("/index.html"), fallback_url)
        self.assertEqual(self.spawn.calls, [], "a portless launch opens no browser")
        self.assertEqual(self.startfile.urls, [])

        # The offer has a real URL: the read-only preview really answers.
        with urllib.request.urlopen(fallback_url, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"LAUNCH-STATIC-OK", response.read())

        # The explicit second request is what shows the page -- and only then a window.
        chosen = self._launch(
            client,
            project.project_id,
            {"confirm": True, "entrypoint_digest": digest, "fallback": True},
        )
        self.assertEqual(chosen.status_code, 200, chosen.text)
        body = chosen.json()
        self.assertTrue(body["fallback"])
        self.assertTrue(body["launched"])
        self.assertEqual(body["url"], fallback_url)
        self.assertEqual(len(self.spawn.calls), 1, "the explicit request opens one window")
        self.assertEqual(self.spawn.calls[0]["vector"][1], f"--app={fallback_url}")
        self.assertEqual(self.startfile.urls, [])

    def test_11_a_bad_fallback_flag_is_a_bad_body(self) -> None:
        client = self._client()
        project = self._project(files={"index.html": STATIC_PAGE.encode("utf-8")})
        answer = self._launch(client, project.project_id, {"fallback": "yes"})
        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertEqual(self.spawn.calls, [])

    def test_12_an_already_running_app_is_a_success_with_the_reused_port(self) -> None:
        """The owner's exact failure: exit 0, no socket of its own, a live server.

        The launched child never binds a port -- another instance answers on it -- so it
        prints that instance's address and exits 0. Forge used to scan the child's
        sockets, find nothing and answer ``409 project_launch_refused`` («порт не
        открылся») while the application was up. Now the port is read from the child's
        own output, proved with a real ``GET /``, and the window opens.

        The manifest claims 8765 and reality chose another port: both numbers are named.
        """

        server = _FakeAppServer()
        self.addCleanup(server.stop)
        client = self._client()
        project = self._project(
            files={"app.py": ALREADY_RUNNING.format(url=server.url).encode("utf-8")},
            goal=MANIFEST_GOAL,
        )
        detail = client.get(f"/api/projects/{project.project_id}").json()
        self.assertEqual(detail["entrypoint"]["kind"], "python_script")
        digest = str(detail["entrypoint"]["digest"])

        answer = self._launch(
            client,
            project.project_id,
            {"confirm": True, "entrypoint_digest": digest},
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertTrue(body["launched"])
        self.assertTrue(body["reused"], body)
        self.assertEqual(body["url"], server.url, body)
        self.assertEqual(body["observed_port"], server.port, body)
        # The manifest's claimed port is not the port that answered, and the notice says
        # both numbers instead of pretending the claim was right.
        notice = str(body["notice"])
        self.assertIn(str(MANIFEST_CLAIM_PORT), notice, notice)
        self.assertIn(str(server.port), notice, notice)
        self.assertIn(REUSED_SERVER_NOTE, notice, notice)
        # The opened URL really answers: this is the fact the refusal used to miss.
        with urllib.request.urlopen(str(body["url"]), timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"LAUNCH-APP-OK", response.read())
        self.assertEqual(self.spawn.calls[-1]["vector"][1], f"--app={server.url}")

        # The run path agrees with the launch path: the same observed port, the same
        # reused fact, from the supervisor's own payload.
        run = client.get(f"/api/projects/{project.project_id}/run").json()
        self.assertEqual(run["observed_port"], server.port, run)
        self.assertTrue(run["reused"], run)
        self.assertEqual(run["state"], "SUCCEEDED", run)
        self.assertEqual(run["exit_code"], 0, run)
        self.assertEqual(run["url"], server.url, run)

    def test_13_an_announced_address_nobody_answers_is_still_a_refusal(self) -> None:
        """A printed address is a claim: only a real answer may become a window."""

        dead_port = _closed_port()
        client = self._client()
        project = self._project(
            files={"app.py": DEAD_ADDRESS.format(port=dead_port).encode("utf-8")},
            goal=MANIFEST_GOAL,
        )
        digest = client.get(f"/api/projects/{project.project_id}").json()["entrypoint"]["digest"]

        answer = self._launch(
            client,
            project.project_id,
            {"confirm": True, "entrypoint_digest": digest},
        )
        self.assertEqual(answer.status_code, 409, answer.text)
        detail = answer.json()["detail"]
        self.assertEqual(detail["code"], "project_launch_refused", detail)
        self.assertIn(NOT_A_SERVER_MESSAGE, detail["error"])
        self.assertEqual(detail["run_state"], "SUCCEEDED", detail)
        self.assertEqual(detail["exit_code"], 0, detail)
        self.assertIn(f"Адрес: http://127.0.0.1:{dead_port}", detail["log_tail"])
        self.assertEqual(
            self.spawn.calls, [], "a portless claim must open no browser at all"
        )
        run = client.get(f"/api/projects/{project.project_id}/run").json()
        self.assertEqual(run["state"], "SUCCEEDED", run)
        self.assertFalse(run["reused"], run)
        self.assertEqual(run["url"], "", run)
        self.assertEqual(run["observed_port"], 0, run)

    def test_14_a_window_that_never_opens_is_a_typed_refusal_with_the_log_tail(self) -> None:
        """No plan produced a window: the answer is ``project_launch_refused``, not grey.

        Every browser plan dies -- the sandboxed one and the documented retry -- and the
        machine's default browser refuses too. The refusal carries every dead plan and
        the project's own ``launch.log`` tail, which now holds the downgrade sentences.
        No window is claimed, because none appeared.
        """

        spawn = _DeadSpawn(code=1)
        self.window_opener = AppWindowOpener(
            which=_FakeWhich(
                {"chrome": r"C:\fake\chrome.exe", "msedge": r"C:\fake\msedge.exe"}
            ),
            spawn=spawn,
            startfile=_RaisingStartfile(),
            exists=lambda item: False,
            visible_windows=_FakeWindows(alive=spawn.is_alive),
            profile_root=self.base / APP_PROFILE_DIRECTORY,
            grace_seconds=0.01,
            title_wait_seconds=0.05,
        )
        client = self._client()
        project = self._project(files={"index.html": STATIC_PAGE.encode("utf-8")})
        answer = self._launch(client, project.project_id, {"confirm": True})
        self.assertEqual(answer.status_code, 409, answer.text)
        detail = answer.json()["detail"]
        self.assertEqual(detail["code"], "project_launch_refused", detail)
        self.assertEqual(detail["refusal_reason"], "window_refused", detail)
        self.assertEqual(len(detail["window_failures"]), 4, detail)
        tail = "\n".join(detail["launch_log_tail"])
        self.assertIn(SANDBOX_DOWNGRADE_NOTE, tail)
        self.assertIn("chrome.exe", tail)
        self.assertEqual(len(spawn.calls), 4, "both browsers, with and without the sandbox")


class WindowSettingsTests(unittest.TestCase):
    """«Настройки → Как открываются окна»: the switch that forbids the downgrade.

    It lives in the same settings file as the projects folder, defaults to **allowed**
    (the owner asked for the retry), and is read by the launch at every press -- never at
    composition time, so a toggle takes effect on the next launch without a restart.
    """

    def setUp(self) -> None:
        base = REPOSITORY / ".forge" / "test-project-launch-policy" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.base = base.resolve()
        self.store = AppSettingsStore(self.base / "settings.json")

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.settings_routes import register_settings_routes

        app = FastAPI()
        register_settings_routes(app, self.store)
        return TestClient(app)

    def test_01_it_defaults_to_allowed_and_only_that_key_is_written(self) -> None:
        root = str(self.base / "projects")
        self.store.set_project_root(root)
        self.assertTrue(self.store.window_downgrade_allowed(), "the owner asked for the retry")
        self.assertFalse(self.store.set_window_downgrade_allowed(False))
        self.assertFalse(self.store.window_downgrade_allowed())
        # The project root in the same file is untouched by the window switch.
        self.assertEqual(str(self.store.project_root()), root)
        self.assertTrue(self.store.set_window_downgrade_allowed(True))
        self.assertTrue(self.store.window_downgrade_allowed())

    def test_02_the_route_reads_and_writes_it(self) -> None:
        client = self._client()
        first = client.get("/api/settings").json()
        self.assertIs(first["window_downgrade_allowed"], True)
        written = client.post("/api/settings", json={"window_downgrade_allowed": False})
        self.assertEqual(written.status_code, 200, written.text)
        self.assertIs(written.json()["window_downgrade_allowed"], False)
        self.assertIs(client.get("/api/settings").json()["window_downgrade_allowed"], False)
        # A value that is not a boolean, or the switch mixed with a projects folder, is
        # a bad request -- never a guess that silently permits an unsandboxed browser.
        self.assertEqual(
            client.post("/api/settings", json={"window_downgrade_allowed": "yes"}).status_code,
            400,
        )
        self.assertEqual(
            client.post(
                "/api/settings",
                json={"window_downgrade_allowed": False, "project_root": "C:\\Windows"},
            ).status_code,
            400,
        )

    def test_03_the_launcher_composes_the_opener_with_that_policy(self) -> None:
        policy = self.store.window_downgrade_allowed
        with mock.patch.object(launcher_module, "AppWindowOpener") as composed:
            launcher_module.ProjectLauncher(window_policy=policy)
        composed.assert_called_once()
        self.assertIs(composed.call_args.kwargs.get("window_policy"), policy)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
