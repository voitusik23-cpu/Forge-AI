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
   without a port -- with one plain sentence, and a refused launch starts no window at
   all;
5. the window is Chrome in app mode first, Edge second, the default browser last, with
   the exact argument vector: ``--app=<url> --window-size=1280,900
   --user-data-dir=<repo>/.forge/app-profile/<project-id>``, never a shell;
6. a process that dies but leaves a static page in its folder is reported plainly and
   the static page is served read-only instead (``fallback: true`` plus the failure).

The OS opener is always injected: no real browser window opens on the machine running
this suite. ``shell=False`` and the exact argv are asserted instead.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import time
import unittest
from unittest import mock
import urllib.request

from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects import launcher as launcher_module
from app.projects.app_window import (
    APP_PROFILE_DIRECTORY,
    APP_WINDOW_SIZE,
    WINDOW_CHROME_APP,
    WINDOW_DEFAULT_BROWSER,
    WINDOW_EDGE_APP,
    AppWindowOpener,
    WindowRefusal,
)
from app.projects.entrypoint import resolve_entry
from app.projects.launcher import LaunchRefusal, ProjectLauncher
from app.projects.runner import RunTarget
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


class _DeadSpawn:
    """A browser that exits immediately, as Chrome does on a host that forbids its sandbox."""

    def __init__(self, code: int = 3221225475, pid: int = 1111) -> None:
        self.code = int(code)
        self.pid = int(pid)
        self.calls: list[dict] = []

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

    def __call__(self, vector, **kwargs):
        self.calls.append({"vector": [str(part) for part in vector], "kwargs": dict(kwargs)})
        index = len(self.calls)
        code = self.code if index <= self.dead_calls else None

        class _Child:
            poll = staticmethod(lambda code=code: code)

        child = _Child()
        child.pid = 1000 + index
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
            profile_root=self.profiles,
            grace_seconds=0.01,
        )
        return opener, spawn, startfile

    def _dead_opener(self, installed: dict, spawn):
        startfile = _RecordingStartfile()
        opener = AppWindowOpener(
            which=_FakeWhich(installed),
            spawn=spawn,
            startfile=startfile,
            exists=lambda item: False,
            profile_root=self.profiles,
            grace_seconds=0.05,
        )
        return opener, spawn, startfile

    def test_01_chrome_is_first_and_the_argv_is_exact(self) -> None:
        opener, spawn, startfile = self._opener({"chrome": r"C:\fake\Google\Chrome\chrome.exe"})
        result = opener.open(PROJECT_ID, "http://127.0.0.1:4321/index.html")
        self.assertEqual(result.window, WINDOW_CHROME_APP)
        self.assertEqual(result.program.lower(), "chrome.exe")
        self.assertEqual(result.pid, 9876)
        self.assertEqual(len(spawn.calls), 1)
        call = spawn.calls[0]
        profile = self.profiles / PROJECT_ID
        self.assertEqual(
            call["vector"],
            [
                r"C:\fake\Google\Chrome\chrome.exe",
                "--app=http://127.0.0.1:4321/index.html",
                f"--window-size={APP_WINDOW_SIZE}",
                f"--user-data-dir={profile}",
            ],
        )
        # No shell, no console, detached: a folder or a URL can never become a command.
        self.assertIs(call["kwargs"].get("shell"), False)
        self.assertTrue(profile.is_dir(), "the per-project profile really exists")
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

    def test_05_the_profile_is_per_project_and_reused(self) -> None:
        opener, spawn, _startfile = self._opener({"chrome": r"C:\fake\chrome.exe"})
        other = "proj-ba9876543210"
        first = opener.profile_for(PROJECT_ID)
        again = opener.profile_for(PROJECT_ID)
        second = opener.profile_for(other)
        self.assertEqual(first, again, "a second launch of one project uses its own profile")
        self.assertNotEqual(first, second, "two projects never share a profile")
        self.assertEqual(first.name, PROJECT_ID)
        self.assertEqual(first.parent, self.profiles)
        opener.open(PROJECT_ID, "http://127.0.0.1:8000/")
        opener.open(PROJECT_ID, "http://127.0.0.1:8000/")
        self.assertEqual(len(spawn.calls), 2)
        # The same window memory, so the second launch restores the same window.
        self.assertEqual(spawn.calls[0]["vector"][3], spawn.calls[1]["vector"][3])

    def test_06_an_unsafe_project_id_never_becomes_a_path(self) -> None:
        opener, spawn, _startfile = self._opener({"chrome": r"C:\fake\chrome.exe"})
        for hostile in ("", "../../evil", "proj-XYZ", "..\\..\\windows", None):
            with self.assertRaises(WindowRefusal, msg=repr(hostile)):
                opener.open(hostile, "http://127.0.0.1:5000/")
        self.assertEqual(spawn.calls, [])

    def test_07_a_browser_that_dies_is_skipped_for_the_next_plan(self) -> None:
        opener, spawn, startfile = self._dead_opener(
            {
                "chrome": r"C:\fake\chrome.exe",
                "msedge": r"C:\fake\msedge.exe",
            },
            spawn=_FlakySpawn(dead_calls=1),
        )
        result = opener.open(PROJECT_ID, "http://127.0.0.1:9100/")
        self.assertEqual(result.window, WINDOW_EDGE_APP)
        self.assertEqual(result.program.lower(), "msedge.exe")
        self.assertEqual(len(spawn.calls), 2, "the plan after the dead one is tried")
        self.assertEqual(spawn.calls[0]["vector"][0], r"C:\fake\chrome.exe")
        self.assertEqual(spawn.calls[1]["vector"][0], r"C:\fake\msedge.exe")
        # The edge plan is the same exact vector, with this project's own profile.
        profile = self.profiles / PROJECT_ID
        self.assertEqual(
            spawn.calls[1]["vector"],
            [
                r"C:\fake\msedge.exe",
                "--app=http://127.0.0.1:9100/",
                f"--window-size={APP_WINDOW_SIZE}",
                f"--user-data-dir={profile}",
            ],
        )
        self.assertEqual(startfile.urls, [], "the default browser is not needed")

    def test_08_when_every_chromium_plan_dies_the_default_browser_is_the_last_resort(
        self,
    ) -> None:
        opener, spawn, startfile = self._dead_opener(
            {"chrome": r"C:\fake\chrome.exe", "msedge": r"C:\fake\msedge.exe"},
            spawn=_FlakySpawn(dead_calls=2, code=1),
        )
        result = opener.open(PROJECT_ID, "http://127.0.0.1:9200/")
        self.assertEqual(result.window, WINDOW_DEFAULT_BROWSER)
        self.assertEqual(result.pid, 0)
        self.assertEqual(len(spawn.calls), 2)
        self.assertEqual(startfile.urls, ["http://127.0.0.1:9200/"])

    def test_09_a_handoff_to_a_running_browser_is_a_window_not_a_failure(self) -> None:
        # Chrome's singleton: a second launch on a profile a browser already holds exits 0.
        opener, spawn, startfile = self._dead_opener(
            {"chrome": r"C:\fake\chrome.exe", "msedge": r"C:\fake\msedge.exe"},
            spawn=_DeadSpawn(code=0),
        )
        result = opener.open(PROJECT_ID, "http://127.0.0.1:9300/")
        self.assertEqual(result.window, WINDOW_CHROME_APP)
        self.assertEqual(len(spawn.calls), 1, "a clean handoff is not retried on Edge")
        self.assertEqual(startfile.urls, [])


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
            profile_root=base / "profiles",
        )

    def _entry(self, files=("app.py",)):
        (self.workspace / "app.py").write_text("print('x')\n", encoding="utf-8")
        return resolve_entry([{"path": path} for path in files]).entry

    def _launcher(self, *, runner=None, previews=None):
        return ProjectLauncher(
            runner=runner,
            previews=previews,
            windows=self.windows,
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

    def test_11_a_dead_process_is_reported_and_a_static_page_is_shown_instead(self) -> None:
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
        result = self._launcher(runner=runner, previews=previews).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=2,
            confirm=True,
            digest="d",
        )
        self.assertTrue(result.fallback)
        self.assertEqual(result.entry_kind, "static_html")
        self.assertEqual(result.url, "http://127.0.0.1:5002/index.html")
        self.assertIn("процесс завершился", result.notice)
        self.assertIn("кодом 1", result.notice)
        self.assertIn("index.html", result.notice)
        self.assertEqual(len(previews.started_calls), 1)
        self.assertEqual(previews.started_calls[0]["entry"].path, "index.html")
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
        self.assertIn("Проект не открылся", str(raised.exception))
        self.assertIn("кодом 2", str(raised.exception))
        self.assertEqual(raised.exception.reason_code, "run_failed")
        self.assertEqual(raised.exception.facts["run_state"], "FAILED")
        self.assertEqual(raised.exception.facts["exit_code"], 2)
        self.assertEqual(raised.exception.facts["log_tail"], ["boom"])
        self.assertEqual(self.spawn.calls, [], "a refusal opens no window")

    def test_13_a_running_static_preview_is_reused(self) -> None:
        (self.workspace / "index.html").write_text(STATIC_PAGE, encoding="utf-8")
        entry = resolve_entry([{"path": "index.html"}]).entry
        previews = _FakePreviews(
            running={"running": True, "url": "http://127.0.0.1:5010/index.html"}
        )
        result = self._launcher(previews=previews).launch(
            project_id=PROJECT_ID,
            target=self._target(entry),
            entry=entry,
            files=1,
        )
        self.assertTrue(result.reused)
        self.assertEqual(result.url, "http://127.0.0.1:5010/index.html")
        self.assertEqual(previews.started_calls, [], "the preview was not restarted")
        self.assertEqual(len(self.spawn.calls), 1)


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

    def _project(self, *, files: dict | None = None):
        files = dict(files or {})
        for relative, data in files.items():
            self._write_run_file(relative, data)
        project = self.projects.create_from_discovery(_Session())
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

        # The exact window argv: app mode, the fixed size, this project's profile.
        call = self.spawn.calls[-1]
        profile = self.base / APP_PROFILE_DIRECTORY / project.project_id
        self.assertEqual(
            call["vector"],
            [
                r"C:\fake\Google\Chrome\chrome.exe",
                f"--app={url}",
                f"--window-size={APP_WINDOW_SIZE}",
                f"--user-data-dir={profile}",
            ],
        )
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
        again = self._launch(
            client,
            project.project_id,
            {"confirm": True, "entrypoint_digest": digest},
        )
        self.assertEqual(again.status_code, 200, again.text)
        again_body = again.json()
        self.assertTrue(again_body["reused"])
        self.assertEqual(again_body["url"], url)
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
