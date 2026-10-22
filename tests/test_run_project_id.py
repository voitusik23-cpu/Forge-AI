"""The run response's **lifecycle** project id: the contract and the UI selection.

The defect this suite pins down: ``TaskRunResponse.project_id`` is the workspace/Core
project this server was composed for (``ws-…`` on a real deployment), so a result-card
action that opened it landed on nothing. The run that creates or attaches to a project
has a different identifier -- the lifecycle record ``proj-…`` -- and that is the only
one «Проекты» can open.

What is asserted here:

1. a run that creates/attaches a lifecycle project publishes that project's own id;
2. the published id equals the lifecycle record's ``project_id`` and is *not* the
   workspace ``project_id`` (the two are different fields by design);
3. a run with no lifecycle project publishes ``None`` -- never a copied workspace id;
4. the same id is written into the run's durable ``project_state``;
5. the key set stays backward compatible: every pre-existing key keeps its position
   and an older caller that never sends/reads the new key is unaffected;
6. the UI selection function opens exactly the returned project, and an unknown id
   disables the action instead of opening a different project.

The UI half executes ``ui/src/project-selection.js`` with Node -- the production
function itself, not a Python re-implementation of it.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import shutil
import subprocess
import unittest

from app.api.models import TaskRunResponse
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.service import ProjectService
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]

#: The exact key set, in the exact order, of `TaskRunResponse.to_dict()`.
#: The first 24 keys are the pre-existing contract (unchanged, same order); the last
#: one is the additive lifecycle id. A change to this list is a contract change and
#: must be deliberate.
EXISTING_KEYS = [
    "run_id",
    "task_id",
    "project_id",
    "state",
    "success",
    "output",
    "error",
    "tokens",
    "cost",
    "duration_seconds",
    "provider_used",
    "model_used",
    "outcome",
    "clarification_questions",
    "needs_clarification",
    "run_record_saved",
    "project_created",
    "project_files_created",
    "artifact_manifest_saved",
    "files_created",
    "write_mode",
    "cost_tier",
    "estimated_cost",
    "model_intent",
]
CONTRACT_KEYS = [
    *EXISTING_KEYS,
    "lifecycle_project_id",
    # The clarification round of this run (0 = no context, 1 = may ask, 2 = forced
    # final). Additive and last, exactly like the lifecycle id above: it states the
    # backend's own count, so a client never infers a round from prose.
    "clarification_round",
]

#: The workspace/Core project this test server is composed for. It is deliberately
#: not the `proj-…` shape: if the response ever copies it into the lifecycle field,
#: the assertions below see it.
WORKSPACE_PROJECT_ID = "run-project-id-test"


class _RunProjectIdCase(unittest.TestCase):
    """The real FastAPI app over a throwaway project/discovery/run store."""

    def setUp(self) -> None:
        base = (
            REPOSITORY
            / ".forge"
            / "test-run-project-id"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.root = base / "projects"
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

    # ------------------------------------------------------------------ helpers
    def _client(self):
        from fastapi.testclient import TestClient

        service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.run_root),
            workspace=Workspace(self.run_workspace),
            authorized_project_id=WORKSPACE_PROJECT_ID,
        )
        projects = ProjectService(self.store)
        self.discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))
        app = create_api_app(
            service,
            discovery_service=self.discovery,
            project_service=projects,
        )
        self.app = app
        self.projects = projects
        return TestClient(app)

    def _confirmed_session(self, client, goal: str) -> tuple[str, str]:
        """Create + confirm a discovery session; return ``(session_id, project_id)``."""

        session = client.post("/api/discovery/sessions", json={"goal": goal}).json()["session"]
        session_id = session["session_id"]
        if session.get("status") != "SPECIFICATION_READY":
            skipped = client.post(
                f"/api/discovery/sessions/{session_id}/skip", json={"reason": "test"}
            )
            self.assertEqual(skipped.status_code, 200, skipped.text)
        confirmed = client.post(f"/api/discovery/sessions/{session_id}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        return session_id, str(confirmed.json()["project"]["project_id"])

    def _write_run(self, client, session_id: str | None) -> dict:
        """A real mock write-mode run: no network, no credential, no paid model."""

        payload: dict = {
            "description": "Создать приветственную страницу проекта",
            "category": "code",
            "provider_name": "mock",
            "write_mode": True,
        }
        if session_id is not None:
            payload["discovery_session_id"] = session_id
        response = client.post("/api/tasks/run", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response


class RunProjectIdContractTests(_RunProjectIdCase):
    def test_01_a_created_project_publishes_its_own_lifecycle_id(self) -> None:
        client = self._client()
        session_id, project_id = self._confirmed_session(
            client, "Сайт для бронирования отелей"
        )
        self.assertRegex(project_id, r"^proj-[0-9a-f]{12}$")

        response = self._write_run(client, session_id)
        body = response.json()

        # The created project is the one the run was attached to ...
        self.assertEqual(response.headers.get("X-Forge-Project-Attach"), "ok")
        self.assertEqual(body["lifecycle_project_id"], project_id)
        # ... and the run really belongs to it: the version carries this run id and
        # the workspace holds the file the mock scaffold wrote.
        detail = client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(detail["versions"][0]["run_id"], body["run_id"])
        self.assertGreater(detail["versions"][0]["file_count"], 0)
        written = [
            path
            for path in self.store.workspace_path(project_id).rglob("*")
            if path.is_file()
        ]
        self.assertTrue(written, "the mock write-mode run wrote no file into the project")

        # The lifecycle list names the same id the response published.
        listed = client.get("/api/projects/lifecycle").json()
        self.assertIn(project_id, [row["project_id"] for row in listed])

    def test_02_the_id_is_the_lifecycle_project_not_the_workspace(self) -> None:
        client = self._client()
        session_id, project_id = self._confirmed_session(
            client, "Небольшой трекер расходов"
        )
        body = self._write_run(client, session_id).json()

        # The two fields answer two different questions and must not collapse.
        self.assertEqual(body["project_id"], WORKSPACE_PROJECT_ID)
        self.assertEqual(body["lifecycle_project_id"], project_id)
        self.assertNotEqual(body["project_id"], body["lifecycle_project_id"])
        self.assertFalse(str(body["project_id"]).startswith("proj-"))
        # The record, not the workspace id, is what the lifecycle route resolves.
        self.assertEqual(
            client.get(f"/api/projects/{body['lifecycle_project_id']}").json()["project"][
                "project_id"
            ],
            project_id,
        )

    def test_03_no_lifecycle_project_publishes_null(self) -> None:
        client = self._client()
        # No discovery session: this run creates no lifecycle project at all.
        body = self._write_run(client, None).json()
        self.assertIsNone(body["lifecycle_project_id"])
        # The workspace project is still the one this server was composed for.
        self.assertEqual(body["project_id"], WORKSPACE_PROJECT_ID)

    def test_04_the_same_id_is_written_into_the_durable_run_record(self) -> None:
        client = self._client()
        session_id, project_id = self._confirmed_session(
            client, "Страница статуса сборки"
        )
        body = self._write_run(client, session_id).json()

        record = client.get(f"/api/runs/{body['run_id']}").json()
        project_state = record.get("project_state") or {}
        self.assertEqual(project_state.get("lifecycle_project_id"), project_id)
        # The response and the record agree, which is the whole point of persisting it.
        self.assertEqual(project_state.get("project_created"), True)

    def test_05_the_field_set_stays_backward_compatible(self) -> None:
        # The key order: every pre-existing key keeps its position, the new key is last.
        self.assertEqual(list(TaskRunResponse("r", "t", "p", "s", True, "o").to_dict()), CONTRACT_KEYS)
        self.assertEqual(
            [field.name for field in dataclasses.fields(TaskRunResponse)], CONTRACT_KEYS
        )
        # The new field defaults to None, so an older constructor call is unchanged ...
        legacy = TaskRunResponse(
            run_id="r", task_id="t", project_id="p", state="s", success=True, output="o"
        )
        self.assertIsNone(legacy.lifecycle_project_id)
        self.assertIsNone(legacy.to_dict()["lifecycle_project_id"])
        # ... and every pre-existing key still holds exactly what it held.
        payload = legacy.to_dict()
        for key in EXISTING_KEYS:
            self.assertIn(key, payload)
        self.assertEqual(payload["project_id"], "p")
        # Explicitly publishing an id is additive, not replacing.
        published = TaskRunResponse(
            run_id="r",
            task_id="t",
            project_id="ws-1",
            state="s",
            success=True,
            output="o",
            lifecycle_project_id="proj-000000000001",
        ).to_dict()
        base = TaskRunResponse(
            run_id="r", task_id="t", project_id="ws-1", state="s", success=True, output="o"
        ).to_dict()
        self.assertEqual(published, {**base, "lifecycle_project_id": "proj-000000000001"})

    def test_06_the_http_body_key_order_is_stable(self) -> None:
        client = self._client()
        body = self._write_run(client, None).json()
        self.assertEqual(list(body), CONTRACT_KEYS)


class UiSelectionTests(unittest.TestCase):
    """Execute the production UI selection function, and the card's own guard."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        cls.module = REPOSITORY / "ui" / "src" / "project-selection.js"

    def _select(self, raw_id: object, lifecycle_ids: list[str]) -> dict:
        if self.node is None:
            self.skipTest("node is not installed; the UI selection cannot be executed")
        script = f"""
import {{ selectRunProject }} from {json.dumps(self.module.as_uri())};
const raw = {json.dumps(raw_id)};
const ids = {json.dumps(lifecycle_ids)};
process.stdout.write(JSON.stringify(selectRunProject(raw, ids)));
"""
        completed = subprocess.run(
            [self.node, "--input-type=module", "-e", script],
            cwd=str(REPOSITORY),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_07_the_returned_project_opens_exactly_that_project(self) -> None:
        target = self._select("proj-000000000001", ["proj-000000000000", "proj-000000000001"])
        self.assertEqual(target, {"projectId": "proj-000000000001", "reason": ""})

    def test_08_an_unknown_id_disables_instead_of_opening_another_project(self) -> None:
        target = self._select("proj-ffffffffffff", ["proj-000000000000", "proj-000000000001"])
        self.assertEqual(target["projectId"], "")
        self.assertEqual(target["reason"], "unknown_project")
        self.assertNotIn(target["projectId"], ("proj-000000000000", "proj-000000000001"))

    def test_09_an_absent_id_says_the_run_has_no_project(self) -> None:
        for raw in (None, "", "   ", 7, {"project_id": "proj-000000000001"}):
            target = self._select(raw, ["proj-000000000001"])
            self.assertEqual(target, {"projectId": "", "reason": "no_project"}, repr(raw))

    def test_10_the_card_cannot_open_a_project_the_list_does_not_name(self) -> None:
        source = (REPOSITORY / "ui" / "src" / "screens" / "Forge.tsx").read_text(
            encoding="utf-8"
        )
        # The card reads the lifecycle field, never the workspace `project_id`.
        self.assertIn("selectRunProject(dispatched?.response.lifecycle_project_id", source)
        self.assertNotIn("selectRunProject(dispatched?.response.project_id", source)
        # Both honest reasons exist in the interface's own vocabulary.
        i18n = (REPOSITORY / "ui" / "src" / "i18n.ts").read_text(encoding="utf-8")
        self.assertIn("forge.result.action.no_project", i18n)
        self.assertIn("forge.result.action.unknown_project", i18n)
        self.assertIn("проект не найден", i18n)
        self.assertIn("у запуска нет проекта", i18n)
        # The action is refused when there is no id, so a click cannot fall through to
        # «Проекты» with nothing selected.
        self.assertIn('if (resultProjectId === "") return;', source)


if __name__ == "__main__":
    unittest.main()
