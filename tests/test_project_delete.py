"""Deleting a project: a move into ``projects-deleted``, never an erasure.

Six properties are checked, because each one is a way a naive "delete" would be
wrong for this product:

1. the project's folder **and** its record leave the store and land in
   ``projects-deleted/<id>-<timestamp>/`` (so a restore stays possible);
2. the project disappears from ``GET /api/projects/lifecycle`` and its own id
   answers a clear ``404`` afterwards;
3. a project with a **live process** is refused with ``409 project_is_running`` and
   nothing moves -- stopping it is the caller's explicit decision, not a side effect;
4. the **bulk** route only touches projects whose current version holds no file
   *and* whose status is ``NEEDS_CHANGES``, ``FAILED`` or ``ARCHIVED``;
5. no request can make the surface move anything outside the store root: a malformed
   or hostile id is refused before a path is composed;
6. the trash listing and ``restore-deleted`` are real (the record comes back).

Every test uses a temporary store root and the real FastAPI application; no network
call and no provider is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import unittest
from types import SimpleNamespace

from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.models import ProjectStatus
from app.projects.service import ProjectService
from app.projects.store import (
    DELETED_DIRECTORY_NAME,
    PROJECTS_ROOT_ENV_VAR,
    ProjectStore,
    UnknownProject,
    is_safe_project_id,
    new_project_id,
)
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime


class _FakeSpecification:
    """Only ``to_dict`` is used, which is all the lifecycle layer requires."""

    def __init__(self, value: dict) -> None:
        self._value = dict(value)

    def to_dict(self) -> dict:
        return dict(self._value)


def _session(goal: str = "A small expense tracker", session_id: str | None = None):
    return SimpleNamespace(
        session_id=session_id or f"disc-{new_project_id()[5:]}",
        initial_goal=goal,
        specification=_FakeSpecification(
            {
                "goal": goal,
                "project_type": "web app",
                "core_features": ["add expenses"],
            }
        ),
    )


class _StaticRunner:
    """A runner double: it reports the run state it was given and starts nothing.

    ``state`` is the only method the delete route reads; the rest exist so the
    composition can hand this object over without a missing attribute turning the
    refusal into a crash.
    """

    def __init__(self, running: set[str] | None = None) -> None:
        self.running = set(running or ())

    def state(self, project_id: object) -> str:
        return "RUNNING" if str(project_id) in self.running else "NOT_RUN"

    def status(self, target: object) -> dict:
        return {
            "state": "RUNNING" if str(getattr(target, "project_id", "")) in self.running else "NOT_RUN",
            "running": str(getattr(target, "project_id", "")) in self.running,
            "command": "",
            "pid": 0,
            "port": 0,
            "log": [],
        }

    def stop(self, project_id: object) -> dict:
        self.running.discard(str(project_id))
        return {"state": "STOPPED", "stopped": True}

    def log(self, target: object) -> dict:
        return {"state": "NOT_RUN", "lines": [], "count": 0, "truncated": False}

    def digest_for(self, target: object) -> str:
        return ""

    def stop_all(self) -> None:
        self.running.clear()


class _DeleteTestCase(unittest.TestCase):
    """A project store, a discovery store and a run store under one temporary base."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-project-delete"
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
    def _service(self) -> ForgeApiService:
        return ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.run_root),
            workspace=Workspace(self.run_workspace),
            authorized_project_id="project-delete-test",
        )

    def _client(self, runner: object | None = None):
        from fastapi.testclient import TestClient

        self.discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))
        app = create_api_app(
            self._service(),
            discovery_service=self.discovery,
            project_service=self.projects,
            project_runner=runner,
        )
        self.app = app
        return TestClient(app)

    def _record(
        self,
        *,
        run_id: str = "run-delete-1",
        verification: str = "not_run",
        write_status: str = "no_changes",
        files: tuple = (),
    ) -> dict:
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
                    "status": write_status,
                    "verification": verification,
                    "files": [{"path": path, "action": action} for path, action in files],
                }
            },
            "provider_used": "mock",
            "model_used": "deterministic-offline",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    def _write_workspace_file(self, relative: str, data: bytes) -> pathlib.Path:
        target = self.run_workspace.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _empty_project(self, goal: str = "A small expense tracker"):
        """A confirmed project whose single version was filled by a run that wrote nothing."""

        project = self.projects.create_from_discovery(_session(goal))
        self.projects.confirm(project.project_id)
        attached = self.projects.attach_run(
            project.project_id, self._record(), source_root=self.run_workspace
        )
        self.assertEqual(attached.current().file_count, 0)
        return attached

    def _project_with_files(self, goal: str = "A project that wrote a file"):
        relative = "generated_tool.py"
        self._write_workspace_file(relative, b"def exported(rows):\n    return rows\n")
        project = self.projects.create_from_discovery(_session(goal))
        self.projects.confirm(project.project_id)
        attached = self.projects.attach_run(
            project.project_id,
            self._record(
                run_id="run-delete-file",
                verification="passed",
                write_status="applied",
                files=((relative, "create"),),
            ),
            source_root=self.run_workspace,
        )
        self.assertEqual(attached.current().file_count, 1)
        return attached

    def _project_with_static_page(self, goal: str = "A project with a static page"):
        relative = "index.html"
        self._write_workspace_file(
            relative, b"<!doctype html><title>Forge preview</title><h1>ok</h1>\n"
        )
        project = self.projects.create_from_discovery(_session(goal))
        self.projects.confirm(project.project_id)
        attached = self.projects.attach_run(
            project.project_id,
            self._record(
                run_id="run-delete-static",
                verification="passed",
                write_status="applied",
                files=((relative, "create"),),
            ),
            source_root=self.run_workspace,
        )
        self.assertEqual(attached.current().file_count, 1)
        return attached

    def _project_with_two_versions(self, goal: str = "A project with two versions"):
        first = "generated_tool.py"
        second = "second_tool.py"
        self._write_workspace_file(first, b"def exported(rows):\n    return rows\n")
        project = self.projects.create_from_discovery(_session(goal))
        self.projects.confirm(project.project_id)
        self.projects.attach_run(
            project.project_id,
            self._record(
                run_id="run-delete-v1",
                verification="passed",
                write_status="applied",
                files=((first, "create"),),
            ),
            source_root=self.run_workspace,
        )
        staged = self.projects.new_version(project.project_id, "add a second helper")
        self.assertEqual(staged.version, 2)
        self._write_workspace_file(second, b"def second():\n    return 2\n")
        attached = self.projects.attach_run(
            project.project_id,
            self._record(
                run_id="run-delete-v2",
                verification="passed",
                write_status="applied",
                files=((second, "create"),),
            ),
            source_root=self.run_workspace,
        )
        self.assertEqual(attached.current_version, 2)
        self.assertEqual([item.version for item in attached.versions], [1, 2])
        return attached

    def _tree_hashes(self, folder: pathlib.Path) -> dict:
        """Every file under ``folder``, keyed by relative path, with its SHA-256."""

        return {
            path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob("*")
            if path.is_file()
        }


class DeleteMoveTests(_DeleteTestCase):
    def test_01_delete_moves_the_folder_and_the_record_and_drops_the_listing(self) -> None:
        project = self._empty_project()
        identifier = project.project_id
        folder = self.root / identifier
        record = self.root / f"{identifier}.json"
        self.assertTrue(folder.is_dir())
        self.assertTrue(record.is_file())
        self.assertTrue(self.store.workspace_path(identifier).is_dir())
        self.assertEqual([row.project_id for row in self.projects.list_projects()], [identifier])

        client = self._client()
        answer = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertTrue(body["deleted"])
        self.assertEqual(body["project_id"], identifier)

        # Nothing is left in the store, and the listing no longer has it.
        self.assertFalse(folder.exists(), "the project folder is still in the store")
        self.assertFalse(record.exists(), "the project record is still in the store")
        self.assertEqual(client.get("/api/projects/lifecycle").json(), [])

        # The trash holds the timestamped folder with the record *and* the workspace.
        trash = self.deleted_root / body["trash_name"]
        self.assertTrue(trash.is_dir(), f"no trash folder at {trash}")
        self.assertTrue((trash / identifier).is_dir())
        self.assertTrue((trash / f"{identifier}.json").is_file())
        self.assertTrue((trash / identifier / "workspace").is_dir())
        payload = json.loads((trash / f"{identifier}.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["project_id"], identifier)
        self.assertEqual(payload["schema"], "forge.project.v1")
        self.assertEqual(len(body["moved"]), 2)
        for item in body["moved"]:
            self.assertIn(DELETED_DIRECTORY_NAME, item["to"])
        # No absolute path of the host is published.
        self.assertNotIn(str(self.base), answer.text)

        # The index no longer names it either (the directory, not the cache, is the
        # truth, but a stale cache entry is still a lie).
        index = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [item for item in index["projects"] if item.get("project_id") == identifier], []
        )

    def test_02_a_deleted_id_answers_a_clear_404_and_can_be_restored(self) -> None:
        project = self._empty_project()
        identifier = project.project_id
        client = self._client()
        self.assertEqual(client.delete(f"/api/projects/{identifier}").status_code, 200)

        gone = client.get(f"/api/projects/{identifier}")
        self.assertEqual(gone.status_code, 404, gone.text)
        self.assertEqual(gone.json()["detail"]["code"], "unknown_project")
        self.assertEqual(client.delete(f"/api/projects/{identifier}").status_code, 404)

        # The trash listing knows what is there and the restore moves it back.
        listed = client.get("/api/projects/deleted")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([item["project_id"] for item in listed.json()], [identifier])
        self.assertTrue(listed.json()[0]["record_present"])

        restored = client.post(f"/api/projects/{identifier}/restore-deleted")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["restored"])
        self.assertTrue((self.root / identifier).is_dir())
        self.assertTrue((self.root / f"{identifier}.json").is_file())
        back = client.get(f"/api/projects/{identifier}")
        self.assertEqual(back.status_code, 200, back.text)
        self.assertEqual(back.json()["project"]["project_id"], identifier)
        self.assertEqual(client.get("/api/projects/deleted").json(), [])


class DeleteRefusalTests(_DeleteTestCase):
    def test_03_a_project_with_a_running_process_is_refused_and_nothing_moves(self) -> None:
        project = self._empty_project()
        identifier = project.project_id
        runner = _StaticRunner({identifier})
        client = self._client(runner)

        refused = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(refused.status_code, 409, refused.text)
        detail = refused.json()["detail"]
        self.assertEqual(detail["code"], "project_is_running")
        self.assertIn("stop it first", detail["error"])
        self.assertEqual(detail["run_state"], "RUNNING")

        # Nothing moved: both the folder and the record are still in the store.
        self.assertTrue((self.root / identifier).is_dir())
        self.assertTrue((self.root / f"{identifier}.json").is_file())
        self.assertEqual(
            [row["project_id"] for row in client.get("/api/projects/lifecycle").json()],
            [identifier],
        )
        # The POST form refuses identically.
        self.assertEqual(client.post(f"/api/projects/{identifier}/delete").status_code, 409)
        self.assertFalse(self.deleted_root.exists())

        # Stopping the process is what makes the delete possible -- and it is the
        # caller who stops it, not the delete route.
        self.assertEqual(client.post(f"/api/projects/{identifier}/run/stop").status_code, 200)
        self.assertEqual(client.delete(f"/api/projects/{identifier}").status_code, 200)

    def test_04_the_surface_never_moves_anything_outside_the_store(self) -> None:
        project = self._empty_project()
        client = self._client()
        outside = self.base / "outside.txt"
        outside.write_text("not a project", encoding="utf-8")

        hostile = [
            "..",
            "../..",
            "../../outside.txt",
            "index",
            "proj-zzzzzzzzzzzz",
            "proj-ABCDEF012345",
            "proj-0123456789ab/../../outside",
        ]
        for candidate in hostile:
            for answer in (
                client.delete(f"/api/projects/{candidate}"),
                client.post(f"/api/projects/{candidate}/delete"),
            ):
                self.assertIn(
                    answer.status_code,
                    (400, 404, 405, 422),
                    f"{candidate} answered {answer.status_code}: {answer.text}",
                )
        for answer in (client.delete("/api/projects/"), client.post("/api/projects//delete")):
            self.assertIn(answer.status_code, (400, 404, 405, 422), answer.text)
        self.assertTrue(outside.is_file(), "a hostile id reached a file outside the store")
        self.assertEqual(outside.read_text(encoding="utf-8"), "not a project")
        # The one real project is untouched and no trash folder was invented.
        self.assertTrue((self.root / project.project_id).is_dir())
        self.assertFalse(self.deleted_root.exists())

        # The store itself refuses anything that is not the generated id shape, so the
        # guard does not depend on the HTTP layer having parsed the path first.
        for candidate in ("../outside", "proj-0123456789AB", "proj-0123456789a", 12, None):
            self.assertFalse(is_safe_project_id(candidate))
            with self.assertRaises(UnknownProject):
                self.store.delete(candidate)

    def test_05_bulk_cleanup_only_touches_empty_finished_projects(self) -> None:
        empty_needs_changes = self._empty_project("An empty project that needs changes")
        self.assertEqual(empty_needs_changes.status, ProjectStatus.NEEDS_CHANGES)

        with_files = self._project_with_files()

        archived = self._empty_project("An empty archived project")
        self.projects.archive(archived.project_id)

        waiting = self.projects.create_from_discovery(_session("A project still being planned"))
        # Still WAITING_FOR_CONFIRMATION: empty, but not swept away.

        client = self._client()
        answer = client.post("/api/projects/delete-empty")
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        removed = {item["project_id"] for item in body["deleted"]}
        self.assertEqual(removed, {empty_needs_changes.project_id, archived.project_id})
        self.assertEqual(body["deleted_count"], 2)
        self.assertEqual(body["skipped_count"], 0)

        # The project with files and the project still being planned survive.
        remaining = {row["project_id"] for row in client.get("/api/projects/lifecycle").json()}
        self.assertEqual(remaining, {with_files.project_id, waiting.project_id})
        # Both removed projects are in the trash with their records.
        trash = sorted(item.name for item in self.deleted_root.iterdir() if item.is_dir())
        self.assertEqual(len(trash), 2)
        for name in trash:
            identifier = name.split("-", 2)
            identifier = "-".join(identifier[:2])
            self.assertTrue((self.deleted_root / name / f"{identifier}.json").is_file())

        # A second sweep finds nothing left to remove and is honest about it.
        again = client.post("/api/projects/delete-empty").json()
        self.assertEqual(again["deleted"], [])
        self.assertEqual(again["deleted_count"], 0)

    def test_06_bulk_cleanup_reports_a_running_project_instead_of_deleting_it(self) -> None:
        empty = self._empty_project("An empty project with a live process")
        runner = _StaticRunner({empty.project_id})
        client = self._client(runner)
        body = client.post("/api/projects/delete-empty").json()
        self.assertEqual(body["deleted_count"], 0)
        self.assertEqual(
            [item["project_id"] for item in body["skipped"]], [empty.project_id]
        )
        self.assertEqual(body["skipped"][0]["reason"], "project_is_running")
        self.assertTrue((self.root / empty.project_id).is_dir())


class DeleteEdgeTests(_DeleteTestCase):
    """The edges the reversible delete promises: idempotence, archive, preview,
    byte-identical restore, and the absence of a permanent-delete route."""

    #: A value shaped like a credential. No error body may ever contain one.
    SECRET_SHAPE = re.compile(
        r"(sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{10,}|api[_-]?key\s*[:=])",
        re.IGNORECASE,
    )

    def _assert_no_host_path_or_secret(self, answer, label: str) -> None:
        text = answer.text
        self.assertNotIn(str(self.base), text, f"{label} published an absolute host path")
        self.assertNotIn(str(self.root), text, f"{label} published the store path")
        self.assertIsNone(
            self.SECRET_SHAPE.search(text), f"{label} published a secret-shaped value"
        )

    def test_07_deleting_twice_is_a_404_and_moves_nothing_twice(self) -> None:
        project = self._empty_project()
        identifier = project.project_id
        client = self._client()

        first = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(first.status_code, 200, first.text)

        second = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(second.status_code, 404, second.text)
        self.assertEqual(second.json()["detail"]["code"], "unknown_project")
        self._assert_no_host_path_or_secret(second, "the second delete")
        # The POST form agrees and the trash still holds exactly one copy.
        again = client.post(f"/api/projects/{identifier}/delete")
        self.assertEqual(again.status_code, 404, again.text)
        self.assertEqual(
            len([entry.name for entry in self.deleted_root.iterdir() if entry.is_dir()]),
            1,
            "the second delete created a second trash entry",
        )
        trash = next(entry for entry in self.deleted_root.iterdir() if entry.is_dir())
        self.assertEqual(
            [path.name for path in trash.iterdir() if path.is_file()],
            [f"{identifier}.json"],
            "the second delete moved the record again",
        )

    def test_08_an_unknown_id_answers_404_and_the_trash_does_not_invent_one(self) -> None:
        client = self._client()
        missing = "proj-000000000000"
        for answer in (
            client.delete(f"/api/projects/{missing}"),
            client.post(f"/api/projects/{missing}/delete"),
            client.get(f"/api/projects/{missing}"),
            client.post(f"/api/projects/{missing}/restore-deleted"),
        ):
            self.assertEqual(answer.status_code, 404, answer.text)
            self.assertEqual(answer.json()["detail"]["code"], "unknown_project")
            self._assert_no_host_path_or_secret(answer, "an unknown id")
        self.assertEqual(client.get("/api/projects/deleted").json(), [])
        self.assertFalse(self.deleted_root.exists())

    def test_09_an_archived_project_can_be_deleted_and_restored(self) -> None:
        project = self._project_with_files(goal="A project archived before deletion")
        identifier = project.project_id
        self.projects.archive(identifier)
        self.assertEqual(self.projects.open_project(identifier).status, ProjectStatus.ARCHIVED)

        client = self._client()
        answer = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(answer.status_code, 200, answer.text)
        self._assert_no_host_path_or_secret(answer, "deleting an archived project")
        trash = self.deleted_root / answer.json()["trash_name"]
        payload = json.loads((trash / f"{identifier}.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "ARCHIVED")
        # The archived project's files moved with it.
        self.assertTrue((trash / identifier / "workspace" / "generated_tool.py").is_file())

        restored = client.post(f"/api/projects/{identifier}/restore-deleted")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["restored"])
        back = self.projects.open_project(identifier)
        self.assertEqual(back.status, ProjectStatus.ARCHIVED)
        self.assertEqual(back.current().file_count, 1)

    def test_10_a_live_static_preview_is_stopped_before_the_move(self) -> None:
        project = self._project_with_static_page()
        identifier = project.project_id
        client = self._client()
        self.addCleanup(lambda: self.app.state.forge_previews.stop_all())

        started = client.post(f"/api/projects/{identifier}/preview", json={"action": "start"})
        self.assertEqual(started.status_code, 200, started.text)
        self.assertTrue(started.json()["running"], started.text)
        self.assertTrue(
            self.app.state.forge_previews.status(identifier)["running"],
            "the preview was not actually started",
        )

        answer = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(answer.status_code, 200, answer.text)
        # The route stops it first: a preview pointed at a folder that moved would be
        # a dead URL claiming to be alive.
        self.assertFalse(self.app.state.forge_previews.status(identifier)["running"])
        trash = self.deleted_root / answer.json()["trash_name"]
        self.assertTrue((trash / identifier / "workspace" / "index.html").is_file())

        restored = client.post(f"/api/projects/{identifier}/restore-deleted")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertFalse(self.app.state.forge_previews.status(identifier)["running"])

    def test_11_restore_brings_back_workspace_record_manifest_versions_and_history(
        self,
    ) -> None:
        project = self._project_with_two_versions()
        identifier = project.project_id
        client = self._client()

        live_folder = self.root / identifier
        live_record = self.root / f"{identifier}.json"
        folder_before = self._tree_hashes(live_folder)
        record_before = live_record.read_bytes()
        history_before = client.get(f"/api/projects/{identifier}/history").json()
        detail_before = client.get(f"/api/projects/{identifier}").json()
        files_before = {
            version: client.get(
                f"/api/projects/{identifier}/files?version={version}"
            ).json()
            for version in (1, 2)
        }
        runs_before = self._tree_hashes(self.run_root)
        self.assertEqual(len(history_before["versions"]), 2)
        self.assertGreater(len(folder_before), 0)

        deleted = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        body = deleted.json()
        trash = self.deleted_root / body["trash_name"]
        # The move is byte-identical: nothing was rewritten, only relocated.
        self.assertEqual(self._tree_hashes(trash / identifier), folder_before)
        self.assertEqual((trash / f"{identifier}.json").read_bytes(), record_before)
        # The run store was not touched at all by the deletion.
        self.assertEqual(self._tree_hashes(self.run_root), runs_before)

        restored = client.post(f"/api/projects/{identifier}/restore-deleted")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["restored"])

        self.assertEqual(self._tree_hashes(self.root / identifier), folder_before)
        self.assertEqual((self.root / f"{identifier}.json").read_bytes(), record_before)
        self.assertEqual(
            client.get(f"/api/projects/{identifier}/history").json(), history_before
        )
        self.assertEqual(
            client.get(f"/api/projects/{identifier}").json(), detail_before
        )
        for version, payload in files_before.items():
            self.assertEqual(
                client.get(
                    f"/api/projects/{identifier}/files?version={version}"
                ).json(),
                payload,
            )
        self.assertEqual(self._tree_hashes(self.run_root), runs_before)
        # The record's own versions still name both runs, in order.
        record = self.projects.open_project(identifier)
        self.assertEqual([item.run_id for item in record.versions], ["run-delete-v1", "run-delete-v2"])
        self.assertEqual([item.version for item in record.versions], [1, 2])

    def test_12_no_permanent_delete_route_or_api_exists(self) -> None:
        self._empty_project()
        client = self._client()

        delete_routes = {
            (
                str(getattr(route, "path", "")),
                tuple(sorted(getattr(route, "methods", ()) or ())),
            )
            for route in self.app.routes
            if "DELETE" in (getattr(route, "methods", ()) or ())
        }
        # Two DELETE routes exist, and neither erases a project:
        #   * `/api/projects/{id}` moves the record and its files into the trash;
        #   * `/api/settings/providers/{id}` removes a provider's API key from the
        #     operating system's credential vault and drops its stored entry. It names no
        #     project, touches no project folder and cannot restore or erase one.
        self.assertEqual(
            delete_routes,
            {
                ("/api/projects/{project_id}", ("DELETE",)),
                ("/api/settings/providers/{provider_id}", ("DELETE",)),
            },
        )
        for path, _methods in delete_routes:
            lowered = path.lower()
            for word in ("permanent", "purge", "hard", "erase", "destroy"):
                self.assertNotIn(word, lowered, f"a permanent-delete route exists: {path}")
        # The literal collection paths are not a second delete: there is exactly one
        # DELETE route, and it answers an unknown id with 404 (and moves nothing).
        self.assertEqual(client.delete("/api/projects/deleted").status_code, 404)
        self.assertEqual(client.delete("/api/projects/lifecycle").status_code, 404)
        self.assertFalse(self.deleted_root.exists())
        # No lower layer offers a routine that would erase the folder.
        for layer in (self.store, self.projects):
            for name in ("hard_delete", "permanent_delete", "purge", "erase", "rmtree"):
                self.assertFalse(
                    hasattr(layer, name),
                    f"{type(layer).__name__} exposes a permanent delete: {name}",
                )

    def test_13_error_bodies_never_carry_a_host_path_or_a_secret(self) -> None:
        project = self._empty_project()
        identifier = project.project_id
        runner = _StaticRunner({identifier})
        client = self._client(runner)

        refused = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(refused.status_code, 409, refused.text)
        self._assert_no_host_path_or_secret(refused, "the running-process refusal")
        self.assertEqual(refused.json()["detail"]["code"], "project_is_running")

        # A restore collision is a refusal with the backend's own sentence, not a 500
        # and not a silent overwrite. Put the record back by hand to simulate a live
        # project taking the id while the trash copy still exists.
        runner.running.discard(identifier)
        deleted = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        trash = self.deleted_root / deleted.json()["trash_name"]
        shutil.copy2(trash / f"{identifier}.json", self.root / f"{identifier}.json")

        collision = client.post(f"/api/projects/{identifier}/restore-deleted")
        self.assertEqual(collision.status_code, 409, collision.text)
        self.assertEqual(collision.json()["detail"]["code"], "project_already_exists")
        self.assertIn("already exists", collision.json()["detail"]["error"])
        self._assert_no_host_path_or_secret(collision, "the restore collision")
        # Nothing was overwritten and the trash copy is still there.
        self.assertTrue((trash / f"{identifier}.json").is_file())
        self.assertEqual(
            json.loads((self.root / f"{identifier}.json").read_text(encoding="utf-8"))[
                "project_id"
            ],
            identifier,
        )


class TrashListingTests(_DeleteTestCase):
    """The trash listing: its fields, its newest-first order, and a bounded page.

    A real trash on this machine held 42 entries and the «Удалённые проекты» section
    drew every one of them in one table. The fix is a bound, and a bound is only safe
    if the listing it bounds keeps its meaning: the same rows, the same newest-first
    order, the same fields, and the defaults unchanged for every caller that does not
    page. These tests pin exactly that.
    """

    #: Deletion moments the store will accept, oldest first: ``<id>-<YYYYmmddTHHMMSS>``.
    MOMENTS = tuple(f"202601{days:02d}T120000" for days in range(1, 10))

    def _trashed(self, count: int) -> list[str]:
        """Create ``count`` projects and move each into the trash at a known moment."""

        identifiers: list[str] = []
        for index in range(count):
            project = self._empty_project(f"Trashed project {index}")
            self.store.delete(project.project_id, stamp=self.MOMENTS[index])
            identifiers.append(project.project_id)
        return identifiers

    def test_14_the_trash_listing_can_be_read_in_bounded_pages(self) -> None:
        identifiers = self._trashed(7)
        newest_first = list(reversed(identifiers))
        client = self._client()

        # The default answer is what it always was: every row, newest first.
        whole = client.get("/api/projects/deleted")
        self.assertEqual(whole.status_code, 200, whole.text)
        self.assertEqual([row["project_id"] for row in whole.json()], newest_first)

        # A page is a window on that list, not a second list.
        first = client.get("/api/projects/deleted", params={"limit": 3}).json()
        self.assertEqual([row["project_id"] for row in first], newest_first[:3])
        self.assertEqual(first[0], whole.json()[0])
        second = client.get("/api/projects/deleted", params={"limit": 3, "offset": 3}).json()
        self.assertEqual([row["project_id"] for row in second], newest_first[3:6])
        tail = client.get("/api/projects/deleted", params={"offset": 5}).json()
        self.assertEqual([row["project_id"] for row in tail], newest_first[5:])
        last = client.get("/api/projects/deleted", params={"limit": 1, "offset": 6}).json()
        self.assertEqual([row["project_id"] for row in last], newest_first[6:])
        # Paging cannot invent a row, and the pages do not overlap or drop one.
        self.assertEqual(client.get("/api/projects/deleted", params={"offset": 99}).json(), [])
        self.assertEqual(
            [row["project_id"] for row in first + second + last],
            newest_first,
        )
        # Additive only: a limit that covers everything answers exactly the default.
        self.assertEqual(
            client.get(
                "/api/projects/deleted", params={"limit": len(identifiers)}
            ).json(),
            whole.json(),
        )
        # Out-of-range paging is a controlled 400, never a silently reinterpreted page.
        for params in ({"limit": 0}, {"limit": -1}, {"offset": -1}):
            refused = client.get("/api/projects/deleted", params=params)
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertEqual(
                refused.json()["detail"]["code"], "invalid_request_schema", refused.text
            )

        # A row read from the second page restores to exactly that project: a window
        # does not change what an id means.
        target = second[0]["project_id"]
        restored = client.post(f"/api/projects/{target}/restore-deleted")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["project_id"], target)
        self.assertTrue((self.root / target).is_dir())
        self.assertNotIn(
            target,
            [row["project_id"] for row in client.get("/api/projects/deleted").json()],
        )

    def test_15_each_row_carries_the_date_the_original_status_and_the_file_count(
        self,
    ) -> None:
        with_files = self._project_with_files(goal="A trashed project that held a file")
        self.store.delete(with_files.project_id, stamp=self.MOMENTS[7])
        archived = self._empty_project("A trashed project that was archived")
        self.projects.archive(archived.project_id)
        self.store.delete(archived.project_id, stamp=self.MOMENTS[8])

        client = self._client()
        rows = client.get("/api/projects/deleted").json()
        self.assertEqual(
            [row["project_id"] for row in rows], [archived.project_id, with_files.project_id]
        )
        newest, oldest = rows

        # When it was deleted, what status it had, how many files it held, and its name:
        # the four facts a person needs to decide whether restoring it is worth doing.
        self.assertEqual(newest["deleted_at"], "2026-01-09T12:00:00")
        self.assertEqual(newest["status"], "ARCHIVED")
        self.assertEqual(newest["file_count"], 0)
        # The name is the **record's** own name. A project's name is now derived from the
        # idea by `derive_project_name` (short, at a word boundary, an article kept), so
        # the listing is asserted against the record rather than against the goal text.
        self.assertEqual(newest["name"], archived.name)
        self.assertEqual(oldest["deleted_at"], "2026-01-08T12:00:00")
        self.assertEqual(oldest["status"], "COMPLETED")
        self.assertEqual(oldest["file_count"], 1)
        self.assertEqual(oldest["name"], with_files.name)
        self.assertTrue(newest["name"].startswith("A trashed project"), newest["name"])
        for row in rows:
            self.assertTrue(row["record_present"])
            self.assertTrue(row["folder_present"])
            self.assertIn(DELETED_DIRECTORY_NAME, row["trash_path"])
        # No row publishes a host path.
        self.assertNotIn(str(self.base), json.dumps(rows))

        # The newest-first order is by deletion moment, not by project id: the row the
        # listing puts first is the one deleted latest.
        self.assertGreater(newest["deleted_at"], oldest["deleted_at"])

    def test_16_a_restore_conflict_names_the_code_and_the_reason(self) -> None:
        project = self._empty_project("A project whose id is taken again")
        identifier = project.project_id
        client = self._client()

        deleted = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        trash = self.deleted_root / deleted.json()["trash_name"]
        # A live project takes the id while the trash copy still holds one.
        shutil.copy2(trash / f"{identifier}.json", self.root / f"{identifier}.json")

        collision = client.post(f"/api/projects/{identifier}/restore-deleted")
        self.assertEqual(collision.status_code, 409, collision.text)
        detail = collision.json()["detail"]
        self.assertEqual(detail["code"], "project_already_exists")
        self.assertIn(identifier, detail["error"])
        self.assertIn("already exists", detail["error"])
        self.assertNotIn(str(self.base), collision.text)
        # Nothing was overwritten: the trash copy is still there, so the restore stays
        # possible once the live id is out of the way.
        self.assertTrue((trash / f"{identifier}.json").is_file())


if __name__ == "__main__":
    unittest.main()
