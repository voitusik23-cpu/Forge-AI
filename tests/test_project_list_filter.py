"""The «Проекты» list must not become a ribbon of junk again.

The owner's screenshot showed 42 projects, and almost all of them were records a test
or a demo script had left behind -- his own «Почтовый центр» lost in the middle of a
ribbon of task sentences. Two rules keep that from happening, and this file proves both
against the production code, offline, with throwaway stores:

1. **The default view is the owner's projects.** A record whose files really live under
   a configured *visible* project root is drawn; a record left in the legacy hidden
   workspace (``<store>/<id>/workspace``), which is where every test and scratch script
   writes, is not. Nothing is erased and nothing is filtered out of the API answer: the
   row carries the fact (``in_visible_root``), the screen hides it by default and
   «Показать всё» brings it back.
2. **The table is bounded.** At most one page (25) of rows is drawn, «Показать ещё»
   reveals the next page, and the counts stay the real ones.

The rule itself is ``ui/src/project-list.js`` -- plain ESM with no React, no DOM and no
network -- so it is *executed* here with Node rather than re-implemented in Python.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import unittest

from app.projects.models import LifecycleProject, ProjectStatus
from app.projects.service import ProjectService
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore, new_project_id

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
MODULE = REPOSITORY / "ui" / "src" / "project-list.js"

#: The one page size the screen and the test must agree on.
EXPECTED_PAGE_SIZE = 25


class _Session:
    """The minimum a discovery session must expose for ``create_from_discovery``."""

    def __init__(self, goal: str) -> None:
        self.session_id = f"disc-list-{os.getpid()}-{id(self)}"
        self.initial_goal = goal
        self.specification = {"goal": goal, "platform": "веб-приложение"}


class _Base(unittest.TestCase):
    """One throwaway tree: a record store, a visible root, a run workspace."""

    def setUp(self) -> None:
        base = REPOSITORY / ".forge" / "test-project-list" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        self.base = base.resolve()
        self.root = self.base / "records"
        self.visible = self.base / "Desktop" / "Forge Projects"
        self.runs = self.base / "runs"
        self.run_workspace = self.base / "run-workspace"
        for directory in (self.root, self.visible, self.runs, self.run_workspace):
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

    # ------------------------------------------------------------------ objects
    def _service(self, *, visible: bool = True) -> ProjectService:
        return ProjectService(
            ProjectStore(root=self.root, visible_root=self.visible if visible else None)
        )

    def _client(self, service: ProjectService):
        from fastapi.testclient import TestClient

        from app.api.server import create_api_app
        from app.api.service import ForgeApiService
        from app.discovery.service import DiscoveryService
        from app.discovery.store import DiscoveryStore
        from app.runtime.run_store import RunStore
        from app.tools.workspace import Workspace
        from offline_runtime import offline_runtime

        api = ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.runs),
            workspace=Workspace(self.run_workspace),
            authorized_project_id="ws-test",
        )
        app = create_api_app(
            api,
            discovery_service=DiscoveryService(
                DiscoveryStore(root=self.base / "discovery")
            ),
            project_service=service,
        )
        return TestClient(app)

    # ------------------------------------------------------------------- rows
    def _owner_project(self, goal: str = "Почтовый центр") -> LifecycleProject:
        """A project the way the owner creates one: its files land in the visible root."""

        return self._service().create_from_discovery(_Session(goal))

    def _legacy_project(self, name: str = "Тестовый проект") -> LifecycleProject:
        """A record exactly as the test suite leaves one: no visible folder at all."""

        project_id = new_project_id()
        record = LifecycleProject(
            project_id=project_id,
            name=name,
            status=ProjectStatus.PLANNED,
            workspace_id=project_id,
            project_path=f".forge/projects/{project_id}/workspace",
        )
        self._service(visible=False).store.write(record)
        return record

    # ------------------------------------------------------- the production rule
    def _list_rule(self, summaries: list[dict], **options: object) -> dict:
        """Execute ``ui/src/project-list.js``: the rule the screen really uses."""

        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed; the list rule cannot be executed")
        payload = {"summaries": summaries, **options}
        script = (
            f"import {{ projectListRows, PROJECT_LIST_PAGE_SIZE }} from "
            f"{json.dumps(MODULE.as_uri())};\n"
            f"const slice = projectListRows({json.dumps(payload)});\n"
            "process.stdout.write(JSON.stringify({ slice, pageSize: PROJECT_LIST_PAGE_SIZE }));\n"
        )
        completed = subprocess.run(
            [node, "--input-type=module", "-e", script],
            cwd=str(REPOSITORY),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        answer = json.loads(completed.stdout)
        answer["slice"]["pageSizeConstant"] = answer["pageSize"]
        return answer["slice"]

    def _rows(self, client) -> list[dict]:
        answer = client.get("/api/projects/lifecycle")
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    @staticmethod
    def _ids(slice_payload: dict) -> list[str]:
        return [str(row["project_id"]) for row in slice_payload["rows"]]


class DefaultViewTests(_Base):
    """A test-created project is hidden by default; «Показать всё» reveals it."""

    def test_01_a_test_project_is_hidden_by_default_and_shown_by_the_toggle(self) -> None:
        owner = self._owner_project()
        legacy = self._legacy_project()
        rows = self._rows(self._client(self._service()))

        # The API answer is the whole listing: both records, each with the root fact.
        self.assertEqual({row["project_id"] for row in rows}, {owner.project_id, legacy.project_id})
        flags = {row["project_id"]: row["in_visible_root"] for row in rows}
        self.assertIs(flags[owner.project_id], True)
        self.assertIs(flags[legacy.project_id], False)
        # The existing summary keys are untouched: this change is additive.
        for key in ("name", "status", "archived", "current_version", "file_count", "project_path"):
            self.assertIn(key, rows[0])

        default = self._list_rule(rows)
        self.assertEqual(default["scope"], "visible-root")
        self.assertEqual(self._ids(default), [owner.project_id])
        self.assertEqual(default["total"], 2)
        self.assertEqual(default["hidden"], 1)
        self.assertEqual(default["tabTotal"], 1)

        everything = self._list_rule(rows, showAll=True)
        self.assertEqual(everything["scope"], "all")
        self.assertEqual(
            set(self._ids(everything)), {owner.project_id, legacy.project_id}
        )
        # The toggle does not pretend the junk is gone: the count stays visible.
        self.assertEqual(everything["hidden"], 1)

    def test_02_the_owners_project_is_always_visible(self) -> None:
        owner = self._owner_project()
        self._legacy_project()
        rows = self._rows(self._client(self._service()))
        for options in ({}, {"showAll": True}, {"archived": True}, {"archived": False}):
            drawn = self._ids(self._list_rule(rows, **options))
            if options.get("archived"):
                self.assertEqual(drawn, [], "the owner's project is not archived")
            else:
                self.assertIn(owner.project_id, drawn, options)

    def test_03_without_a_visible_root_nothing_is_hidden(self) -> None:
        # A deployment that configured no project root cannot tell a test project from a
        # real one, and hiding everything would be a worse answer than showing it.
        legacy = self._legacy_project()
        client = self._client(self._service(visible=False))
        rows = self._rows(client)
        self.assertEqual([row["project_id"] for row in rows], [legacy.project_id])
        self.assertIs(rows[0]["in_visible_root"], False)
        slice_payload = self._list_rule(rows)
        self.assertEqual(slice_payload["scope"], "all")
        self.assertEqual(self._ids(slice_payload), [legacy.project_id])
        self.assertEqual(slice_payload["hidden"], 0)

    def test_04_a_record_without_a_folder_is_not_the_owners(self) -> None:
        project = self._owner_project()
        record = self._service().open_project(project.project_id)
        # A record that carries no recorded folder (the legacy layout) is not visible,
        # whatever its name says: the folder is the evidence, not the name.
        self.assertTrue(self._service().store.in_visible_root(record))
        moved = LifecycleProject(**{**record.__dict__, "workspace_dir": ""})
        self.assertFalse(self._service().store.in_visible_root(moved))


class BoundedTableTests(_Base):
    """The table draws one page at a time and «Показать ещё» reveals the next."""

    def test_01_the_page_size_is_the_documented_one(self) -> None:
        owner = self._owner_project()
        rows = self._rows(self._client(self._service()))
        slice_payload = self._list_rule(rows)
        self.assertEqual(slice_payload["pageSizeConstant"], EXPECTED_PAGE_SIZE)
        self.assertEqual(slice_payload["pageSize"], EXPECTED_PAGE_SIZE)
        self.assertEqual(self._ids(slice_payload), [owner.project_id])

    def test_02_a_long_list_is_capped_and_reveals_the_next_page(self) -> None:
        owner = self._owner_project()
        for index in range(30):
            self._legacy_project(name=f"Тестовый проект {index}")
        rows = self._rows(self._client(self._service()))
        self.assertEqual(len(rows), 31)

        # The default view still shows exactly the owner's one project, uncapped by junk.
        default = self._list_rule(rows)
        self.assertEqual(self._ids(default), [owner.project_id])
        self.assertFalse(default["hasMore"])

        # «Показать всё» is one page, and says how much of the table that is.
        first = self._list_rule(rows, showAll=True)
        self.assertEqual(first["drawn"], EXPECTED_PAGE_SIZE)
        self.assertEqual(first["tabTotal"], 31)
        self.assertTrue(first["hasMore"])
        self.assertEqual(first["hidden"], 30)

        # «Показать ещё» reveals the next page, and the last one stops growing.
        second = self._list_rule(rows, showAll=True, shown=EXPECTED_PAGE_SIZE * 2)
        self.assertEqual(second["drawn"], 31)
        self.assertFalse(second["hasMore"])
        self.assertEqual(
            first["rows"], second["rows"][: len(first["rows"])], "the order is stable"
        )

    def test_03_the_archive_tab_is_bounded_and_filtered_the_same_way(self) -> None:
        owner = self._owner_project()
        archived_legacy = self._legacy_project(name="Удалённый тестовый проект")
        self._service().archive(archived_legacy.project_id)
        self._legacy_project(name="Активный тестовый проект")
        rows = self._rows(self._client(self._service()))

        active = self._list_rule(rows, archived=False)
        self.assertEqual(self._ids(active), [owner.project_id])
        archive = self._list_rule(rows, archived=True)
        self.assertEqual(self._ids(archive), [])
        # …and with the toggle on, the archive tab holds exactly the archived rows.
        archive_all = self._list_rule(rows, archived=True, showAll=True)
        self.assertEqual(self._ids(archive_all), [archived_legacy.project_id])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
