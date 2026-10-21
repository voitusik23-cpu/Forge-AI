"""The eight project actions of the beta interface, one scenario each.

The owner's report was specific: the previous pass clicked «Открыть файлы», «Удалить
проект», «Восстановить» and the disabled states, but actions 2, 3, 5 and 6 -- «Запустить
локально», «Проверить», «Восстановить» and "a running project cannot be deleted" -- were
never exercised *separately*. This file exercises all eight against the real FastAPI
application, with the mock provider and a throwaway store, and executes the production
UI selection function with Node instead of re-implementing it in Python.

What is real here
-----------------
The application (`create_api_app`), the lifecycle service and store, the mock provider
(`app.offline`), the run store, the local runner and the preview registry: the same
objects the shipped process composes. Action 2 really starts a child process that binds
a port and really fetches its URL; action 6 really starts one and proves the delete is
refused while it lives.

What is not here
----------------
No browser and no CDP: the DOM half is photographed separately against the running
application (`.forge/beta_ui_actions_cdp.py`, screenshots in `.forge/screenshots/`).
No outbound network: every URL this file fetches is `127.0.0.1`. No paid provider: the
only model is `mock`.

The eight actions and the backend fact each one rests on:

1. «Открыть файлы»        -- a result card opens exactly `lifecycle_project_id`;
2. «Запустить локально»   -- the project starts and yields a URL that answers;
3. «Проверить»            -- `POST /verify` really re-checks the artifacts on disk;
4. «Удалить проект»       -- the project moves into `projects-deleted`;
5. «Восстановить»         -- the same workspace, files, versions, manifest and run id;
6. a running project      -- cannot be deleted until it is stopped;
7. no id                  -- a disabled control with the reason, never a false action;
8. an unknown id          -- a disabled control with the reason, never another project.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time
import unittest
import urllib.request

from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.service import ProjectService
from app.projects.store import (
    DELETED_DIRECTORY_NAME,
    PROJECTS_ROOT_ENV_VAR,
    ProjectStore,
)
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]

#: The workspace/Core project this test server is composed for. It is deliberately not
#: a ``proj-…`` id: action 1 must never open it.
WORKSPACE_PROJECT_ID = "beta-ui-actions-test"

#: A project that really serves a page: it binds port 0 and prints the port it got, so
#: the runner can publish the URL the child actually listens on (the recipe measured in
#: `tests/test_local_execution.py`).
SERVING = """\
import http.server, socketserver

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"BETA-UI-ACTION-OK\\n"
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

STATIC_PAGE = "<!doctype html><title>Forge beta preview</title><h1>ok</h1>\n"


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


class _Session:
    """The minimum a discovery session must expose for ``create_from_discovery``."""

    def __init__(self, goal: str) -> None:
        self.session_id = f"disc-beta-{os.getpid()}-{id(self)}"
        self.initial_goal = goal
        self.specification = {"goal": goal, "platform": "веб-приложение"}


class _UiActionsCase(unittest.TestCase):
    """One throwaway project/discovery/run store and the real application on top."""

    def setUp(self) -> None:
        base = (
            REPOSITORY
            / ".forge"
            / "test-beta-ui-actions"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.root = base / "projects"
        self.deleted_root = base / DELETED_DIRECTORY_NAME
        self.discovery_root = base / "discovery"
        self.run_root = base / "runs"
        self.run_workspace = base / "run-workspace"
        for directory in (self.root, self.discovery_root, self.run_root, self.run_workspace):
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

    # ------------------------------------------------------------------ helpers
    def _client(self):
        from fastapi.testclient import TestClient

        service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.run_root),
            workspace=Workspace(self.run_workspace),
            authorized_project_id=WORKSPACE_PROJECT_ID,
        )
        self.discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))
        self.app = create_api_app(
            service,
            discovery_service=self.discovery,
            project_service=self.projects,
        )
        # Whatever a test started, this test stops: a child process that outlived its
        # test would be an orphan, which is the thing the delete route refuses to make.
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

    def _project(self, goal: str = "A small project", *, files: dict | None = None):
        """A confirmed project whose current version holds ``files``.

        ``files`` maps a workspace-relative path to its bytes; the run record names
        every one of them as created, which is what the lifecycle layer reads to fill
        the version and what the entry-point detection reads to find a launchable file.
        """

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

    def _serving_project(self, goal: str = "A project that serves a page"):
        return self._project(goal, files={"app.py": SERVING.encode("utf-8")})

    def _static_project(self, goal: str = "A project with one static page"):
        return self._project(goal, files={"index.html": STATIC_PAGE.encode("utf-8")})

    def _wait(self, call, predicate, label: str, timeout: float = 30.0):
        """Poll ``call`` until ``predicate`` accepts its value, or fail with the last one."""

        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = call()
            if predicate(last):
                return last
            time.sleep(0.2)
        self.fail(f"timed out waiting for {label}; last answer was {last!r}")

    def _start_run(self, client, identifier: str) -> dict:
        """The two clicks «Запустить» performs: read the digest, then approve it."""

        detail = client.get(f"/api/projects/{identifier}").json()
        digest = str((detail.get("run") or {}).get("entrypoint_digest") or "")
        self.assertTrue(digest, f"the project has no runnable entry point: {detail.get('run')}")
        started = client.post(
            f"/api/projects/{identifier}/run",
            json={"confirm": True, "entrypoint_digest": digest, "offline": True},
        )
        self.assertEqual(started.status_code, 200, started.text)
        return started.json()

    # ------------------------------------------------- the production UI function
    def _select_run_project(self, raw_id: object, lifecycle_ids: list[str]) -> dict:
        """Execute ``ui/src/project-selection.js``: the rule the result card uses."""

        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed; the UI selection cannot be executed")
        module = REPOSITORY / "ui" / "src" / "project-selection.js"
        script = f"""
import {{ selectRunProject }} from {json.dumps(module.as_uri())};
const raw = {json.dumps(raw_id)};
const ids = {json.dumps(lifecycle_ids)};
process.stdout.write(JSON.stringify(selectRunProject(raw, ids)));
"""
        completed = subprocess.run(
            [node, "--input-type=module", "-e", script],
            cwd=str(REPOSITORY),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def _lifecycle_ids(self, client) -> list[str]:
        return [
            str(row["project_id"])
            for row in client.get("/api/projects/lifecycle").json()
        ]


class ResultCardActionTests(_UiActionsCase):
    """Actions 1, 7 and 8: what a result card may open, and what it must refuse."""

    def _confirmed_session(self, client, goal: str) -> str:
        session = client.post("/api/discovery/sessions", json={"goal": goal}).json()["session"]
        session_id = session["session_id"]
        if session.get("status") != "SPECIFICATION_READY":
            skipped = client.post(
                f"/api/discovery/sessions/{session_id}/skip", json={"reason": "test"}
            )
            self.assertEqual(skipped.status_code, 200, skipped.text)
        confirmed = client.post(f"/api/discovery/sessions/{session_id}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        return session_id

    def test_01_open_files_from_a_result_opens_exactly_the_lifecycle_project(self) -> None:
        client = self._client()
        session_id = self._confirmed_session(client, "Сайт для бронирования отелей")

        response = client.post(
            "/api/tasks/run",
            json={
                "description": "Создать приветственную страницу проекта",
                "category": "code",
                "provider_name": "mock",
                "write_mode": True,
                "discovery_session_id": session_id,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        lifecycle_id = str(body["lifecycle_project_id"])
        self.assertRegex(lifecycle_id, r"^proj-[0-9a-f]{12}$")

        # The two identifiers are different facts; only the lifecycle one is a project
        # «Проекты» can open, and the card reads that field.
        self.assertEqual(body["project_id"], WORKSPACE_PROJECT_ID)
        self.assertNotEqual(body["project_id"], lifecycle_id)

        # The production selection function opens exactly that project.
        target = self._select_run_project(lifecycle_id, self._lifecycle_ids(client))
        self.assertEqual(target, {"projectId": lifecycle_id, "reason": ""})

        # And it truly opens it: the files route answers for it ...
        files = client.get(f"/api/projects/{target['projectId']}/files")
        self.assertEqual(files.status_code, 200, files.text)
        self.assertEqual(files.json()["project_id"], lifecycle_id)
        self.assertGreater(files.json()["count"], 0)
        # ... while the workspace id opens nothing at all.
        workspace = client.get(f"/api/projects/{body['project_id']}")
        self.assertEqual(workspace.status_code, 404, workspace.text)
        self.assertEqual(workspace.json()["detail"]["code"], "unknown_project")
        # The card's own source reads the lifecycle field, not the workspace one.
        forge = (REPOSITORY / "ui" / "src" / "screens" / "Forge.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("selectRunProject(dispatched?.response.lifecycle_project_id", forge)
        self.assertNotIn("selectRunProject(dispatched?.response.project_id", forge)

    def test_07_a_project_without_an_id_produces_no_false_action(self) -> None:
        client = self._client()
        self._project("A project that exists but is not the run's")
        known = self._lifecycle_ids(client)
        self.assertEqual(len(known), 1)

        # A run that produced no project: the card is off with the reason, and the
        # reason is not "the project was not found" -- there is nothing to look for.
        for raw in (None, "", "   ", 7):
            target = self._select_run_project(raw, known)
            self.assertEqual(target, {"projectId": "", "reason": "no_project"}, repr(raw))

        # The interface prints that reason and refuses the click.
        i18n = (REPOSITORY / "ui" / "src" / "i18n.ts").read_text(encoding="utf-8")
        self.assertIn("forge.result.action.no_project", i18n)
        self.assertIn("у запуска нет проекта", i18n)
        forge = (REPOSITORY / "ui" / "src" / "screens" / "Forge.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn('if (resultProjectId === "") return;', forge)

        # The trash row and the project card refuse the same way: a record without an
        # id disables the control and shows the reason beside it.
        projects = (REPOSITORY / "ui" / "src" / "screens" / "Projects.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn('const canDelete = projectId !== "";', projects)
        self.assertIn('disabledReason={canDelete ? undefined : t(lang, "projects.delete.no_id")}', projects)
        self.assertIn('disabledReason={id === "" ? t(lang, "projects.delete.no_id") : undefined}', projects)
        self.assertIn("projects.delete.no_id", i18n)

        # And no request was made: nothing in the store moved because of a click that
        # could not name a project.
        self.assertEqual(
            [row["project_id"] for row in client.get("/api/projects/lifecycle").json()],
            known,
        )

    def test_08_an_unknown_id_never_opens_another_project(self) -> None:
        client = self._client()
        self._project("A project the list does name")
        self._project("A second project the list does name")
        known = self._lifecycle_ids(client)
        self.assertEqual(len(known), 2)

        unknown = "proj-ffffffffffff"
        self.assertNotIn(unknown, known)
        target = self._select_run_project(unknown, known)
        self.assertEqual(target, {"projectId": "", "reason": "unknown_project"})
        # The refused id is not quietly replaced by one that does exist.
        self.assertEqual(target["projectId"], "")

        # The backend agrees: an id nobody owns answers 404 and opens nothing.
        for path in (
            f"/api/projects/{unknown}",
            f"/api/projects/{unknown}/files",
            f"/api/projects/{unknown}/preview",
            f"/api/projects/{unknown}/run",
        ):
            answer = client.get(path)
            self.assertEqual(answer.status_code, 404, f"{path}: {answer.text}")
            self.assertEqual(answer.json()["detail"]["code"], "unknown_project", path)
        i18n = (REPOSITORY / "ui" / "src" / "i18n.ts").read_text(encoding="utf-8")
        self.assertIn("forge.result.action.unknown_project", i18n)
        self.assertIn("проект не найден", i18n)


class ProjectActionTests(_UiActionsCase):
    """Actions 2, 3, 4, 5 and 6: each one clicked on its own."""

    def test_02_launch_locally_on_its_own_starts_the_project_and_publishes_a_url(
        self,
    ) -> None:
        project = self._serving_project()
        identifier = project.project_id
        client = self._client()

        # Not merely enabled: the digest is real, and the approval names it.
        detail = client.get(f"/api/projects/{identifier}").json()
        self.assertEqual(detail["entrypoint"]["kind"], "python_script")
        self.assertTrue(detail["entrypoint"]["runnable"], detail["entrypoint"])
        self.assertTrue(detail["run"]["entrypoint_digest"])

        started = self._start_run(client, identifier)
        self.assertTrue(started["running"], started)
        self.assertGreater(started["pid"], 0)

        status = self._wait(
            lambda: client.get(f"/api/projects/{identifier}/run").json(),
            lambda value: value["state"] == "RUNNING" and value["serving"],
            "the local process to bind a port",
        )
        url = str(status["url"])
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        self.assertGreater(status["port"], 0)

        # The URL answers, and it answers with this project's page.
        with urllib.request.urlopen(url, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"BETA-UI-ACTION-OK", response.read())

        # The preview block the card draws says the same thing the run block says.
        preview = client.get(f"/api/projects/{identifier}/preview").json()
        self.assertTrue(preview["available"], preview)
        self.assertTrue(preview["running"], preview)
        self.assertEqual(preview["source"], "process")
        self.assertEqual(preview["url"], url)

        stopped = client.post(f"/api/projects/{identifier}/run/stop")
        self.assertEqual(stopped.status_code, 200, stopped.text)
        after = client.get(f"/api/projects/{identifier}/preview").json()
        self.assertFalse(after["running"], after)
        self.assertEqual(after["url"], "")

    def test_03_verify_on_its_own_re_checks_the_artifacts_and_reports_the_result(
        self,
    ) -> None:
        project = self._project(
            "A project with one recorded file",
            files={"generated_tool.py": b"def exported(rows):\n    return rows\n"},
        )
        identifier = project.project_id
        client = self._client()

        answered = client.post(f"/api/projects/{identifier}/verify")
        self.assertEqual(answered.status_code, 200, answered.text)
        body = answered.json()
        self.assertTrue(body["verified"])
        self.assertEqual(body["verification"], "passed")
        self.assertEqual(body["acceptance"], project.current().acceptance)
        self.assertIn(body["status"], {"COMPLETED", "NEEDS_CHANGES"})

        # The check is not a cached word: change the file on disk and the same click
        # reports the truth about the new bytes.
        self.store.workspace_file_path(identifier, "generated_tool.py").write_bytes(
            b"def exported(rows):\n    return rows  # edited by hand\n"
        )
        again = client.post(f"/api/projects/{identifier}/verify")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["verification"], "failed", again.text)
        self.assertEqual(again.json()["status"], "NEEDS_CHANGES", again.text)

    def test_04_delete_moves_the_project_into_the_trash(self) -> None:
        project = self._project(
            "A project to delete",
            files={"generated_tool.py": b"def exported(rows):\n    return rows\n"},
        )
        identifier = project.project_id
        client = self._client()
        record_before = (self.root / f"{identifier}.json").read_bytes()

        deleted = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        body = deleted.json()
        self.assertTrue(body["deleted"])
        self.assertEqual(body["project_id"], identifier)

        # It left the store and the listing ...
        self.assertFalse((self.root / identifier).exists())
        self.assertFalse((self.root / f"{identifier}.json").exists())
        self.assertEqual(client.get("/api/projects/lifecycle").json(), [])
        self.assertEqual(client.get(f"/api/projects/{identifier}").status_code, 404)
        # ... and landed in the trash, record included, unmodified.
        trash = self.deleted_root / body["trash_name"]
        self.assertTrue((trash / identifier).is_dir())
        self.assertEqual((trash / f"{identifier}.json").read_bytes(), record_before)
        listed = client.get("/api/projects/deleted").json()
        self.assertEqual([row["project_id"] for row in listed], [identifier])
        self.assertEqual(listed[0]["status"], "COMPLETED")
        self.assertEqual(listed[0]["file_count"], 1)
        self.assertTrue(listed[0]["deleted_at"])

    def test_05_restore_returns_the_same_workspace_files_versions_manifest_and_run_id(
        self,
    ) -> None:
        project = self._project(
            "A project that will be restored",
            files={
                "generated_tool.py": b"def exported(rows):\n    return rows\n",
                "index.html": STATIC_PAGE.encode("utf-8"),
            },
        )
        identifier = project.project_id
        # A second version, so "the same versions" is more than one row.
        staged = self.projects.new_version(identifier, "add a second helper")
        self.assertEqual(staged.version, 2)
        self._write_run_file("second_tool.py", b"def second():\n    return 2\n")
        self.projects.attach_run(
            identifier,
            _record(run_id="run-restore-v2", files=("second_tool.py",)),
            source_root=self.run_workspace,
        )

        client = self._client()

        def snapshot() -> dict:
            return {
                "detail": client.get(f"/api/projects/{identifier}").json(),
                "history": client.get(f"/api/projects/{identifier}/history").json(),
                "files_v1": client.get(
                    f"/api/projects/{identifier}/files?version=1"
                ).json(),
                "files_v2": client.get(
                    f"/api/projects/{identifier}/files?version=2"
                ).json(),
                "workspace": {
                    path.relative_to(self.root).as_posix(): path.read_bytes()
                    for path in (self.root / identifier).rglob("*")
                    if path.is_file()
                },
                "record": (self.root / f"{identifier}.json").read_bytes(),
                "runs": {
                    path.relative_to(self.run_root).as_posix(): path.read_bytes()
                    for path in self.run_root.rglob("*")
                    if path.is_file()
                },
            }

        before = snapshot()
        self.assertGreater(len(before["workspace"]), 0)
        # The history is newest-first, so version 2's run is named first.
        self.assertEqual(
            [version["run_id"] for version in before["history"]["versions"]],
            ["run-restore-v2", f"run-{identifier}"],
        )

        self.assertEqual(client.delete(f"/api/projects/{identifier}").status_code, 200)
        restored = client.post(f"/api/projects/{identifier}/restore-deleted")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["restored"])
        self.assertEqual(restored.json()["project_id"], identifier)

        after = snapshot()
        # Same workspace bytes, same record bytes, same files list, same versions, same
        # run id -- a move back, not a reconstruction.
        self.assertEqual(after["workspace"], before["workspace"])
        self.assertEqual(after["record"], before["record"])
        self.assertEqual(after["detail"], before["detail"])
        self.assertEqual(after["history"], before["history"])
        self.assertEqual(after["files_v1"], before["files_v1"])
        self.assertEqual(after["files_v2"], before["files_v2"])
        self.assertEqual(after["runs"], before["runs"])
        self.assertEqual(
            [version["run_id"] for version in after["history"]["versions"]],
            ["run-restore-v2", f"run-{identifier}"],
        )
        self.assertEqual(client.get("/api/projects/deleted").json(), [])

    def test_06_a_running_project_cannot_be_deleted_until_it_is_stopped(self) -> None:
        project = self._serving_project("A project that is running while deleted")
        identifier = project.project_id
        client = self._client()

        started = self._start_run(client, identifier)
        self.assertTrue(started["running"], started)
        pid = int(started["pid"])
        self.assertGreater(pid, 0)
        self._wait(
            lambda: client.get(f"/api/projects/{identifier}/run").json(),
            lambda value: value["state"] == "RUNNING",
            "the local process to be running",
        )

        refused = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(refused.status_code, 409, refused.text)
        detail = refused.json()["detail"]
        self.assertEqual(detail["code"], "project_is_running")
        self.assertIn("stop it first", detail["error"])
        # Nothing moved, and the running child is still the same live pid.
        self.assertTrue((self.root / identifier).is_dir())
        self.assertTrue((self.root / f"{identifier}.json").is_file())
        self.assertFalse(self.deleted_root.exists())
        self.assertEqual(
            [row["project_id"] for row in client.get("/api/projects/lifecycle").json()],
            [identifier],
        )
        self.assertTrue(_pid_alive(pid), "the refused delete killed the running process")

        # Stopping is the caller's explicit act, and only then does the delete happen.
        self.assertEqual(client.post(f"/api/projects/{identifier}/run/stop").status_code, 200)
        self.assertEqual(client.delete(f"/api/projects/{identifier}").status_code, 200)
        self.assertFalse(_pid_alive(pid), "the stopped process outlived the delete")

    def test_06b_a_static_preview_is_stopped_rather_than_refusing_the_delete(self) -> None:
        # The one preview that *can* be closed without stopping a process: a static
        # page served by this process. It is stopped first, so the answer is honest.
        project = self._static_project("A project with a static preview")
        identifier = project.project_id
        client = self._client()

        started = client.post(f"/api/projects/{identifier}/preview", json={"action": "start"})
        self.assertEqual(started.status_code, 200, started.text)
        self.assertTrue(started.json()["running"], started.text)
        url = str(started.json()["url"])
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        with urllib.request.urlopen(url, timeout=10) as response:
            self.assertEqual(response.status, 200)

        deleted = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertFalse(
            self.app.state.forge_previews.status(identifier)["running"],
            "the delete left a preview pointing at a folder that moved",
        )


def _pid_alive(pid: int) -> bool:
    """Whether a pid is still alive, without touching anything this test did not start."""

    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            check=False,
        )
        return str(pid) in (completed.stdout or b"").decode("utf-8", errors="replace")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    unittest.main()
