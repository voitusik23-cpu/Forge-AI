"""A project is a program first and a page second: the entry point ranking.

The owner's measured defect, hit at least four times:

    «Запустить приложение» -> the window is empty / grey.

The cause was one line of priority: ``index.html`` ranked above every runnable entry,
so a project that really had ``app.py`` (or ``main.py``, ``server.py``, a
``package.json`` start script) **and** a leftover or stub page was launched as a
static page. Forge served the HTML with no backend and the owner concluded nothing
worked. This file proves the fixed policy, end to end:

* the ranking -- ``python_script`` > ``python_module`` > ``node_script``/``node_npm``
  > ``static_html`` -- for every combination, both from the manifest and from the
  files that are really at the project root;
* a declared override (``forge.json`` ``"entry"`` or the project specification's
  ``entry``) is honoured, but only when it names a real file;
* a stale manifest cannot demote the entry: after a run that writes only
  ``index.html`` over a project that still has ``app.py``, the entry is still
  ``python_script`` and the card shows it;
* the **launch path** uses the project's process (the runner + port discovery), not
  the static preview, whenever a server entry exists -- and the preview registry is
  not touched;
* Forge's own deterministic mock scaffold module is not mistaken for a server.

Everything here is offline: the launcher's runner, preview registry, browser spawn
and HTTP probe are injected fakes, so no process and no window is started.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import unittest
from dataclasses import replace

from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.app_window import AppWindowOpener
from app.projects.entrypoint import (
    ENTRY_KIND_NODE_NPM,
    ENTRY_KIND_NODE_SCRIPT,
    ENTRY_KIND_PYTHON_SCRIPT,
    ENTRY_KIND_STATIC_HTML,
    ENTRY_REASON_NO_ENTRY,
    SERVE_KIND_PROCESS,
    SERVE_KIND_STATIC,
    STATIC_RUN_COMMAND,
    resolve_entry,
)
from app.projects.launcher import ProjectLauncher
from app.projects.materialize import materialize
from app.projects.mock_answer import MOCK_ANSWER_BANNER
from app.projects.models import (
    ProjectStatus,
    ProjectVersion,
    utc_now,
)
from app.projects.runner import RunTarget
from app.projects.service import ProjectService
from app.projects.store import ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
SCRATCH = REPOSITORY / ".forge" / "test-entry-priority"
PROJECT_ID = "proj-0123456789ab"

#: A real stdlib server: the shape the ranking must put above any page.
SERVER_APP = """\
import http.server
import socketserver


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ENTRY-SERVER-MARKER"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
    print("listening %d" % httpd.server_address[1], flush=True)
    httpd.serve_forever()
"""

PAGE = "<!doctype html><title>Entry page</title><h1>ENTRY-PAGE-MARKER</h1>\n"
DECOY_PAGE = "<!doctype html><title>leftover</title>\n"


def _fenced(path: str, body: str, language: str = "") -> str:
    header = f"{language} " if language else ""
    return f"```{header}path={path}\n{body}\n```\n"


ANSWER_SERVER_AND_PAGE = (
    "A real server and a page.\n\n"
    + _fenced("app.py", SERVER_APP, "python")
    + "\n"
    + _fenced("index.html", PAGE, "html")
)

#: A run that writes a *static* scaffold only, over a project that already has a
#: server. This is the "stub over a real project" case the record must survive.
ANSWER_STATIC_SCAFFOLD = _fenced("index.html", DECOY_PAGE, "html")


# --------------------------------------------------------------------------- #
# recorders injected where the real OS would be
# --------------------------------------------------------------------------- #


class _FakeWhich:
    def __init__(self, installed: dict) -> None:
        self.installed = dict(installed)
        self.asked: list[str] = []

    def __call__(self, program: str):
        self.asked.append(program)
        return self.installed.get(program)


class _RecordingSpawn:
    def __init__(self, pid: int = 9876) -> None:
        self.calls: list[dict] = []
        self.pid = int(pid)

    def __call__(self, vector, **kwargs):
        self.calls.append(
            {"vector": [str(part) for part in vector], "kwargs": dict(kwargs)}
        )

        class _Child:
            pass

        child = _Child()
        child.pid = self.pid
        return child


class _RecordingStartfile:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> None:
        self.urls.append(url)


class _FakeRunner:
    """A supervisor stand-in: records the approval and reports the named state."""

    def __init__(self, *, started: dict) -> None:
        self.started_calls: list[dict] = []
        self._started = dict(started)
        self._status = {"state": "NOT_RUN", "url": ""}

    def status(self, target):
        return dict(self._status)

    def start_confirmed(self, target, *, confirm, digest, offline=False):
        self.started_calls.append(
            {"target": target, "confirm": confirm, "digest": digest}
        )
        self._status = dict(self._started)
        return dict(self._started)


class _NoRunner:
    """A runner that must not be asked anything."""

    def __init__(self) -> None:
        self.started_calls: list[dict] = []

    def status(self, target):
        return {"state": "NOT_RUN", "url": ""}

    def start_confirmed(self, target, **kwargs):  # pragma: no cover - must not run
        self.started_calls.append(kwargs)
        raise AssertionError("the static launch must not start a process")


class _FakePreviews:
    """The read-only preview registry, recording every call made to it."""

    def __init__(self, *, url: str = "http://127.0.0.1:54322/index.html") -> None:
        self.started_calls: list[dict] = []
        self.stopped_calls: list[str] = []
        self._url = url
        self._running: dict = {"running": False, "url": "", "port": 0}

    def status(self, project_id):
        return dict(self._running)

    def start(self, project_id, workspace, entry):
        self.started_calls.append(
            {"project_id": project_id, "workspace": workspace, "entry": entry}
        )
        self._running = {"running": True, "url": self._url, "port": 54322}
        return dict(self._running)

    def stop(self, project_id):
        self.stopped_calls.append(project_id)
        self._running = {"running": False, "url": "", "port": 0}
        return dict(self._running)


class _Session:
    def __init__(self, goal: str = "Локальное приложение: сервер и страница.") -> None:
        self.session_id = f"disc-entry-{os.getpid()}-{id(self)}"
        self.initial_goal = goal
        self.specification = {"goal": goal, "platform": "веб-приложение"}


# --------------------------------------------------------------------------- #
# 1. the ranking, for every combination
# --------------------------------------------------------------------------- #


class _WorkspaceCase(unittest.TestCase):
    """A fresh, empty project workspace per test, plus the two write/resolve helpers."""

    def setUp(self) -> None:
        self.base = SCRATCH / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(self.base, ignore_errors=True)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))

    def write(self, relative: str, body: str = "x") -> pathlib.Path:
        target = self.workspace.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def resolve(self, paths, *, workspace: bool = True, declared: object = None):
        return resolve_entry(
            [{"path": path} for path in paths],
            workspace=self.workspace if workspace else None,
            declared_entry=declared,
        )

    def entry(self, paths, **kwargs):
        resolution = self.resolve(paths, **kwargs)
        self.assertIsNotNone(resolution.entry, resolution.reason)
        return resolution.entry


class RankingTests(_WorkspaceCase):
    def test_01_app_py_beats_index_html(self) -> None:
        self.write("app.py", SERVER_APP)
        self.write("index.html", PAGE)
        entry = self.entry(["app.py", "index.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(entry.path, "app.py")
        self.assertEqual(entry.serve_kind, SERVE_KIND_PROCESS)
        self.assertEqual(entry.run_command, "python app.py")
        self.assertEqual(entry.command[1:], ("app.py",))
        self.assertIs(entry.runnable, True)
        self.assertFalse(entry.reason)

    def test_02_main_py_beats_index_html(self) -> None:
        self.write("main.py", SERVER_APP)
        self.write("index.html", PAGE)
        entry = self.entry(["main.py", "index.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(entry.path, "main.py")
        self.assertEqual(entry.run_command, "python main.py")

    def test_03_server_py_alone_is_a_python_script(self) -> None:
        self.write("server.py", SERVER_APP)
        entry = self.entry(["server.py"])
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(entry.path, "server.py")
        self.assertEqual(entry.serve_kind, SERVE_KIND_PROCESS)

    def test_04_app_py_wins_over_main_and_server_in_that_order(self) -> None:
        for name in ("app.py", "main.py", "server.py"):
            self.write(name, 'print("x")\n')
        entry = self.entry(["server.py", "main.py", "app.py"])
        self.assertEqual(entry.path, "app.py")

    def test_05_package_json_beats_index_html(self) -> None:
        self.write(
            "package.json", json.dumps({"scripts": {"start": "node server.js"}})
        )
        self.write("index.html", PAGE)
        entry = self.entry(["package.json", "index.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_NODE_NPM)
        self.assertEqual(entry.path, "package.json")
        self.assertEqual(entry.serve_kind, SERVE_KIND_PROCESS)
        self.assertEqual(entry.run_command, "npm start")
        # The command may be empty when node/npm is not installed: the *shape* is
        # still recognised and the honest reason says which one is missing.
        if not entry.command:
            self.assertTrue(entry.reason)

    def test_06_node_script_beats_index_html(self) -> None:
        self.write("server.js", "console.log('x')\n")
        self.write("index.html", PAGE)
        entry = self.entry(["server.js", "index.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_NODE_SCRIPT)
        self.assertEqual(entry.path, "server.js")
        self.assertEqual(entry.serve_kind, SERVE_KIND_PROCESS)

    def test_07_index_html_alone_is_the_static_fallback(self) -> None:
        self.write("index.html", PAGE)
        entry = self.entry(["index.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(entry.path, "index.html")
        self.assertEqual(entry.serve_kind, SERVE_KIND_STATIC)
        self.assertEqual(entry.command, ())
        self.assertIs(entry.runnable, True)
        self.assertEqual(entry.run_command, STATIC_RUN_COMMAND)
        self.assertEqual(STATIC_RUN_COMMAND, "статическая страница, сервер не требуется")

    def test_08_a_named_other_page_is_static(self) -> None:
        self.write("landing.html", PAGE)
        entry = self.entry(["landing.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(entry.path, "landing.html")
        self.assertEqual(entry.serve_kind, SERVE_KIND_STATIC)

    def test_09_nothing_is_no_entry_point(self) -> None:
        resolution = self.resolve([])
        self.assertIsNone(resolution.entry)
        self.assertEqual(resolution.reason, ENTRY_REASON_NO_ENTRY)
        self.assertFalse(resolution.blocked)

    def test_10_the_manifest_alone_uses_the_same_ranking(self) -> None:
        # Without a workspace the manifest is all there is, and the program still wins.
        entry = self.entry(["app.py", "index.html"], workspace=False)
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(entry.path, "app.py")

    def test_11_a_file_on_disk_that_the_manifest_omits_still_wins(self) -> None:
        # The stale-manifest half: the manifest lists only the page, the server is
        # still on disk. A run that wrote one file did not delete the other one.
        self.write("app.py", SERVER_APP)
        self.write("index.html", PAGE)
        entry = self.entry(["index.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(entry.path, "app.py")

    def test_12_forge_s_own_scaffold_module_is_not_a_server(self) -> None:
        # ``MOCK_DETERMINISTIC`` writes a page and a side-effect-free module. The
        # module must not make every offline project a process that exits at once.
        self.write("app.py", f'"""{MOCK_ANSWER_BANNER}\n\nno side effects."""\n')
        self.write("index.html", PAGE)
        entry = self.entry(["index.html", "app.py"])
        self.assertEqual(entry.kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(entry.path, "index.html")
        self.assertEqual(entry.serve_kind, SERVE_KIND_STATIC)

    def test_13_a_real_program_containing_the_word_mock_is_still_a_program(self) -> None:
        # Only Forge's exact banner is carved out: an ordinary server is never skipped.
        self.write("app.py", 'print("MOCK-ISH but real")\n')
        self.write("index.html", PAGE)
        entry = self.entry(["index.html", "app.py"])
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)


# --------------------------------------------------------------------------- #
# 2. the declared override
# --------------------------------------------------------------------------- #


class DeclaredOverrideTests(_WorkspaceCase):
    def test_20_forge_json_entry_wins_over_the_ranking(self) -> None:
        self.write("app.py", SERVER_APP)
        self.write("index.html", PAGE)
        self.write("forge.json", json.dumps({"entry": "index.html"}))
        entry = self.entry(["app.py", "index.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(entry.path, "index.html")
        self.assertEqual(entry.serve_kind, SERVE_KIND_STATIC)

    def test_21_the_specification_entry_wins_over_the_ranking(self) -> None:
        self.write("app.py", SERVER_APP)
        self.write("index.html", PAGE)
        entry = self.entry(["app.py", "index.html"], declared="index.html")
        self.assertEqual(entry.kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(entry.path, "index.html")

    def test_22_the_caller_s_declaration_is_read_before_forge_json(self) -> None:
        self.write("app.py", SERVER_APP)
        self.write("index.html", PAGE)
        self.write("forge.json", json.dumps({"entry": "index.html"}))
        entry = self.entry(["app.py", "index.html"], declared="app.py")
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(entry.path, "app.py")

    def test_23_a_declaration_that_names_nothing_is_ignored(self) -> None:
        self.write("app.py", SERVER_APP)
        self.write("index.html", PAGE)
        self.write("forge.json", json.dumps({"entry": "missing.html"}))
        entry = self.entry(["app.py", "index.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(entry.path, "app.py")

    def test_24_an_escaping_declaration_is_ignored(self) -> None:
        self.write("app.py", SERVER_APP)
        self.write("index.html", PAGE)
        for declared in ("../outside.html", "/etc/passwd", "C:/Windows/win.ini"):
            entry = self.entry(
                ["app.py", "index.html"], declared=declared
            )
            self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT, declared)

    def test_25_a_broken_forge_json_is_ignored(self) -> None:
        self.write("app.py", SERVER_APP)
        self.write("index.html", PAGE)
        self.write("forge.json", "{not json")
        entry = self.entry(["app.py", "index.html"])
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)

    def test_26_a_declared_page_that_only_exists_on_disk_is_honoured(self) -> None:
        self.write("landing.html", PAGE)
        self.write("app.py", SERVER_APP)
        entry = self.entry(["app.py"], declared="landing.html")
        self.assertEqual(entry.kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(entry.path, "landing.html")

    def test_27_a_declaration_of_an_unknown_kind_is_ignored(self) -> None:
        self.write("app.py", SERVER_APP)
        self.write("run.sh", "#!/bin/sh\n")
        entry = self.entry(["app.py", "run.sh"], declared="run.sh")
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)


# --------------------------------------------------------------------------- #
# 3. the record after a run: the entry is recomputed, never stale
# --------------------------------------------------------------------------- #


class StaleEntryRecordTests(unittest.TestCase):
    """A run that writes only a page must not relabel a project that has a server."""

    def setUp(self) -> None:
        self.base = SCRATCH / f"record-{os.getpid()}-{id(self)}"
        shutil.rmtree(self.base, ignore_errors=True)
        self.base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.store = ProjectStore(root=self.base / "projects")
        self.projects = ProjectService(self.store)
        created = self.projects.create_from_discovery(_Session())
        self.project = self.projects.confirm(created.project_id)
        self.run_store = RunStore(root=self.base / "runs")
        self.run_workspace = self.base / "run-workspace"
        self.run_workspace.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ helpers
    def workspace(self) -> pathlib.Path:
        return self.store.workspace_path(self.project.project_id)

    def record_version(self, project, result, number: int):
        version = ProjectVersion(
            version=number,
            created_at=utc_now(),
            run_id=f"run-entry-priority-{number}",
            files=tuple(result.files),
            verification=result.verification,
            acceptance="passed",
        )
        versions = sorted(
            [item for item in project.versions if item.version != number]
            + [version],
            key=lambda item: item.version,
        )
        updated = replace(
            project,
            versions=versions,
            current_version=number,
            status=ProjectStatus.COMPLETED,
            updated_at=utc_now(),
        )
        self.store.write(updated)
        return updated

    def test_30_a_page_only_run_keeps_the_server_entry(self) -> None:
        first = materialize(
            self.project,
            ANSWER_SERVER_AND_PAGE,
            "run-entry-priority-1",
            store=self.store,
        )
        self.assertEqual(
            sorted(item.path for item in first.files), ["app.py", "index.html"]
        )
        project = self.record_version(self.project, first, 1)
        self.assertEqual(
            resolve_entry(project.current().files, workspace=self.workspace()).entry.path,
            "app.py",
        )

        # A mock run (the same guarded writer the offline scaffold uses) writes only a
        # page over the project. ``app.py`` is still there; the entry must not change.
        second = materialize(
            self.project,
            ANSWER_STATIC_SCAFFOLD,
            "run-entry-priority-2",
            store=self.store,
        )
        self.assertEqual([item.path for item in second.files], ["index.html"])
        self.assertTrue((self.workspace() / "app.py").is_file())
        project = self.record_version(self.project, second, 2)

        resolution = resolve_entry(
            project.current().files, workspace=self.workspace()
        )
        self.assertIsNotNone(resolution.entry, resolution.reason)
        self.assertEqual(resolution.entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(resolution.entry.path, "app.py")
        self.assertEqual(resolution.entry.serve_kind, SERVE_KIND_PROCESS)

    def test_31_the_card_shows_the_recomputed_entry_point(self) -> None:
        first = materialize(
            self.project,
            ANSWER_SERVER_AND_PAGE,
            "run-entry-priority-3",
            store=self.store,
        )
        project = self.record_version(self.project, first, 1)
        second = materialize(
            project,
            ANSWER_STATIC_SCAFFOLD,
            "run-entry-priority-4",
            store=self.store,
        )
        project = self.record_version(project, second, 2)

        service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=self.run_store,
            workspace=Workspace(root=self.run_workspace),
            authorized_project_id="test-entry-priority",
        )
        app = create_api_app(
            service,
            project_service=self.projects,
            discovery_service=DiscoveryService(
                DiscoveryStore(root=self.base / "discovery")
            ),
        )
        from fastapi.testclient import TestClient

        client = TestClient(app)
        detail = client.get(f"/api/projects/{project.project_id}").json()
        entry = detail["entrypoint"]
        self.assertIsNotNone(entry, detail)
        self.assertEqual(entry["kind"], ENTRY_KIND_PYTHON_SCRIPT, entry)
        self.assertEqual(entry["path"], "app.py")
        self.assertEqual(entry["serve_kind"], SERVE_KIND_PROCESS)
        self.assertEqual(entry["run_command"], "python app.py")
        self.assertNotIn("index.html", json.dumps(entry))
        # The preview block describes the process, not a read-only page.
        self.assertEqual(detail["preview"]["serve_kind"], SERVE_KIND_PROCESS)
        self.assertEqual(detail["preview"]["entry_path"], "app.py")
        # The card's action names the program, not the page the old manifest named.
        self.assertEqual(detail["presentation"]["launch_role"], "run")
        self.assertEqual(detail["presentation"]["launch_path"], "app.py")
        actions = {
            str(item["id"]): item for item in detail["presentation"]["actions"]
        }
        self.assertIs(actions["launch_local"]["visible"], True)
        self.assertIn("app.py", actions["launch_local"]["reason"])
        self.assertNotIn("index.html", actions["launch_local"]["reason"])

    def test_32_the_launch_starts_the_process_not_the_static_preview(self) -> None:
        first = materialize(
            self.project,
            ANSWER_SERVER_AND_PAGE,
            "run-entry-priority-5",
            store=self.store,
        )
        project = self.record_version(self.project, first, 1)
        second = materialize(
            project,
            ANSWER_STATIC_SCAFFOLD,
            "run-entry-priority-6",
            store=self.store,
        )
        project = self.record_version(project, second, 2)

        entry = resolve_entry(
            project.current().files, workspace=self.workspace()
        ).entry
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        runner = _FakeRunner(
            started={
                "state": "RUNNING",
                "url": "http://127.0.0.1:54321/",
                "port": 54321,
            }
        )
        previews = _FakePreviews()
        launcher = self._launcher(runner=runner, previews=previews)
        result = launcher.launch(
            project_id=PROJECT_ID,
            target=RunTarget(
                project_id=PROJECT_ID,
                workspace=self.workspace(),
                entry=entry,
                project_path="projects-visible/x",
            ),
            entry=entry,
            files=len(project.current().files),
            confirm=True,
            digest=entry.digest,
        )
        self.assertTrue(result.launched)
        self.assertEqual(result.entry_kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(result.url, "http://127.0.0.1:54321/")
        self.assertEqual(len(runner.started_calls), 1)
        self.assertEqual(previews.started_calls, [], "the static preview was opened")
        self.assertEqual(self.spawn.calls[0]["vector"][1], "--app=http://127.0.0.1:54321/")

    # ------------------------------------------------------------------ window
    def _launcher(self, *, runner, previews) -> ProjectLauncher:
        self.spawn = _RecordingSpawn()
        self.startfile = _RecordingStartfile()
        windows = AppWindowOpener(
            which=_FakeWhich({"chrome": r"C:\fake\chrome.exe"}),
            spawn=self.spawn,
            startfile=self.startfile,
            exists=lambda item: False,
            profile_root=self.base / "profiles",
        )
        return ProjectLauncher(
            runner=runner,
            previews=previews,
            windows=windows,
            probe=lambda port: 200,
            readiness_timeout=0.5,
        )


# --------------------------------------------------------------------------- #
# 4. the launch path, both kinds
# --------------------------------------------------------------------------- #


class LaunchPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = SCRATCH / f"launch-{os.getpid()}-{id(self)}"
        shutil.rmtree(self.base, ignore_errors=True)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.spawn = _RecordingSpawn()
        self.startfile = _RecordingStartfile()
        self.windows = AppWindowOpener(
            which=_FakeWhich({"chrome": r"C:\fake\chrome.exe"}),
            spawn=self.spawn,
            startfile=self.startfile,
            exists=lambda item: False,
            profile_root=self.base / "profiles",
        )

    def write(self, relative: str, body: str = "x") -> None:
        target = self.workspace.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def launcher(self, runner, previews) -> ProjectLauncher:
        return ProjectLauncher(
            runner=runner,
            previews=previews,
            windows=self.windows,
            probe=lambda port: 200,
            readiness_timeout=0.5,
        )

    def target(self, entry) -> RunTarget:
        return RunTarget(
            project_id=PROJECT_ID,
            workspace=self.workspace,
            entry=entry,
            project_path="projects-visible/x",
        )

    def test_40_a_server_and_a_page_launch_the_server(self) -> None:
        self.write("app.py", SERVER_APP)
        self.write("index.html", PAGE)
        entry = resolve_entry(
            [{"path": "index.html"}], workspace=self.workspace
        ).entry
        runner = _FakeRunner(
            started={
                "state": "RUNNING",
                "url": "http://127.0.0.1:54321/",
                "port": 54321,
            }
        )
        previews = _FakePreviews()
        result = self.launcher(runner, previews).launch(
            project_id=PROJECT_ID,
            target=self.target(entry),
            entry=entry,
            files=2,
            confirm=True,
            digest=entry.digest,
        )
        self.assertEqual(result.entry_kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(result.url, "http://127.0.0.1:54321/")
        self.assertEqual(len(runner.started_calls), 1)
        self.assertEqual(previews.started_calls, [])
        self.assertEqual(previews.stopped_calls, [])
        self.assertEqual(self.spawn.calls[0]["vector"][1], "--app=http://127.0.0.1:54321/")
        self.assertIs(self.spawn.calls[0]["kwargs"].get("shell"), False)

    def test_41_a_pure_static_project_opens_the_preview(self) -> None:
        self.write("index.html", PAGE)
        entry = resolve_entry(
            [{"path": "index.html"}], workspace=self.workspace
        ).entry
        runner = _NoRunner()
        previews = _FakePreviews()
        result = self.launcher(runner, previews).launch(
            project_id=PROJECT_ID,
            target=self.target(entry),
            entry=entry,
            files=1,
        )
        self.assertEqual(result.entry_kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(result.url, "http://127.0.0.1:54322/index.html")
        self.assertEqual(len(previews.started_calls), 1)
        self.assertEqual(runner.started_calls, [])
        self.assertEqual(
            self.spawn.calls[0]["vector"][1], "--app=http://127.0.0.1:54322/index.html"
        )


if __name__ == "__main__":  # pragma: no cover - convenience only
    unittest.main()
