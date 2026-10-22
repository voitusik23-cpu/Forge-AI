"""A deleted project must never come back: the verify/delete race, pinned.

MEASURED BEFORE THIS FILE EXISTED: ``POST /api/projects/{id}/verify`` was already in
flight when ``DELETE /api/projects/{id}`` completed. The verification had read the
record before the move, so its ``_store.write`` put the record back: the id reappeared
in ``GET /api/projects/lifecycle`` while its workspace sat in
``.forge/projects-deleted/<id>-<ts>/``, and ``POST /{id}/restore-deleted`` then answered
``409 project_already_exists``. This was measured on a real project
(``proj-2e2fe4a64973``), not inferred.

The fix has two halves, and both are checked here:

* a durable deletion marker -- the trash entry itself -- that ``ProjectStore.write``
  re-checks at the write, so a late writer is refused with ``409 project_deleted``
  and no record is created;
* a per-project lock in :class:`ProjectService` held across *read -> check -> write*,
  so a delete either waits for an in-flight verification and then moves the project,
  or the verification observes the marker and is refused.

The interleaving is deterministic: the verification is paused inside its own critical
section (a hook on ``_recompute_verification``), the lock is observed to be held, the
delete is started, and only then is the verification released. No sleep decides any
assertion.

Every test uses a temporary store root and the real FastAPI application; no provider
or network call is made.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import threading
import unittest
from types import SimpleNamespace

from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.models import ProjectError
from app.projects.service import ProjectService
from app.projects.store import (
    DELETED_DIRECTORY_NAME,
    PROJECTS_ROOT_ENV_VAR,
    ProjectStore,
    UnknownProject,
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
        session_id=session_id or f"disc-race-{new_project_id()[5:]}",
        initial_goal=goal,
        specification=_FakeSpecification({"goal": goal, "project_type": "web app"}),
    )


class _DeletedProjectRaceCase(unittest.TestCase):
    """A project store, a discovery store and a run store under one temporary base."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-deleted-project-race"
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
    def _client(self) -> object:
        from fastapi.testclient import TestClient

        self.discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))
        app = create_api_app(
            self._service(),
            discovery_service=self.discovery,
            project_service=self.projects,
        )
        self.app = app
        return TestClient(app)

    def _service(self) -> ForgeApiService:
        return ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.run_root),
            workspace=Workspace(self.run_workspace),
            authorized_project_id="deleted-project-race-test",
        )

    def _record(
        self,
        *,
        run_id: str = "run-race-1",
        files: tuple = (),
        verification: str = "passed",
        write_status: str = "applied",
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

    def _project_with_files(
        self, goal: str = "A project that wrote a file"
    ) -> object:
        """A COMPLETED project with one recorded, passing artifact."""

        relative = "generated_tool.py"
        self._write_workspace_file(relative, b"def exported(rows):\n    return rows\n")
        project = self.projects.create_from_discovery(_session(goal))
        self.projects.confirm(project.project_id)
        attached = self.projects.attach_run(
            project.project_id,
            self._record(files=((relative, "create"),)),
            source_root=self.run_workspace,
        )
        self.assertEqual(attached.current().file_count, 1)
        self.assertEqual(attached.status.value, "COMPLETED")
        return attached

    def _active_ids(self) -> list:
        return [project.project_id for project in self.projects.list_projects()]

    def _trash_ids(self) -> list:
        return [row["project_id"] for row in self.projects.deleted_projects()]

    def _tree_hashes(self, folder: pathlib.Path) -> dict:
        return {
            path.relative_to(folder).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in folder.rglob("*")
            if path.is_file()
        }

    def assertDeletedRefusal(self, call, label: str = ""):
        """``call()`` refuses because the id is deleted, with the agreed code.

        Asserted on the published contract (``ProjectError`` carrying
        ``code == "project_deleted"``) rather than on one class identity, so the test
        pins what a caller sees and fails behaviourally -- not at import time -- when
        the guard is missing.
        """

        with self.assertRaises(ProjectError) as caught:
            call()
        self.assertEqual(
            getattr(caught.exception, "code", ""),
            "project_deleted",
            f"{label or 'the operation'} answered {caught.exception!r}",
        )
        return caught.exception

    def _assert_clean_deletion(self, identifier: str, trash_name: str) -> None:
        """No record in the store, the folder and record in the trash, no id in both."""

        self.assertFalse(
            (self.root / f"{identifier}.json").exists(),
            "the deleted project's record is back in the active store",
        )
        self.assertFalse(
            (self.root / identifier).exists(),
            "the deleted project's folder is back in the active store",
        )
        self.assertFalse(self.store.exists(identifier))
        self.assertTrue(self.store.is_deleted(identifier), "the deletion marker is gone")
        trash = self.deleted_root / trash_name
        self.assertTrue((trash / f"{identifier}.json").is_file())
        self.assertTrue((trash / identifier / "workspace").is_dir())
        self.assertNotIn(identifier, self._active_ids())
        self.assertIn(identifier, self._trash_ids())
        # The two lists never name the same project at the same time.
        self.assertEqual(
            set(self._active_ids()) & set(self._trash_ids()),
            set(),
            "an id is both active and in the trash",
        )


class VerifyBeforeDeleteTests(_DeletedProjectRaceCase):
    def test_01_verify_before_delete_is_normal_and_the_project_stays_active(self) -> None:
        project = self._project_with_files()
        identifier = project.project_id
        client = self._client()

        answer = client.post(f"/api/projects/{identifier}/verify")
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertTrue(body["verified"])
        self.assertEqual(body["verification"], "passed")
        self.assertIn(body["status"], {"COMPLETED", "NEEDS_CHANGES"})
        # Nothing moved and nothing was tombstoned by a verification.
        self.assertTrue((self.root / f"{identifier}.json").is_file())
        self.assertFalse(self.store.is_deleted(identifier))
        self.assertFalse(self.deleted_root.exists())
        self.assertIn(identifier, self._active_ids())
        self.assertEqual(self._trash_ids(), [])


class VerifyDeleteRaceTests(_DeletedProjectRaceCase):
    def test_02_an_in_flight_verify_cannot_recreate_the_deleted_record(self) -> None:
        """The measured defect: verify reads, DELETE completes, verify writes back.

        The verification is paused *inside* its critical section, after it read the
        record and before it wrote. The delete is then started and the verification is
        released. Whichever of the two orderings the lock produces, the outcome must
        be a clean deletion -- never a resurrected record. Here the permitted ordering
        is the measured one from the fix: DELETE waits for the verification and then
        moves the project.
        """

        project = self._project_with_files()
        identifier = project.project_id
        client = self._client()

        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = self.projects._recompute_verification

        def paused(project_value, version_value):
            entered.set()
            self.assertTrue(
                release.wait(10), "the verification was never released by the test"
            )
            return original(project_value, version_value)

        self.projects._recompute_verification = paused
        self.addCleanup(
            lambda: setattr(
                self.projects, "_recompute_verification", original
            )
        )

        verified: dict = {}
        deleted: dict = {}

        def run_verify() -> None:
            try:
                verified["project"] = self.projects.verify_again(identifier)
            except BaseException as exc:  # noqa: BLE001 - reported by the test
                verified["error"] = exc

        def run_delete() -> None:
            try:
                deleted["body"] = self.projects.delete(identifier)
            except BaseException as exc:  # noqa: BLE001 - reported by the test
                deleted["error"] = exc

        verify_thread = threading.Thread(target=run_verify, daemon=True)
        verify_thread.start()
        self.assertTrue(
            entered.wait(10), "the verification never reached its critical section"
        )
        # Deterministic: the lock is provably held while the verification is paused,
        # which is what makes the delete below serialise behind it.
        self.assertTrue(self.projects._project_lock(identifier).locked())

        delete_thread = threading.Thread(target=run_delete, daemon=True)
        delete_thread.start()
        release.set()
        verify_thread.join(30)
        delete_thread.join(30)
        self.assertFalse(verify_thread.is_alive(), "the verification never finished")
        self.assertFalse(delete_thread.is_alive(), "the delete never finished")

        self.assertNotIn("error", verified, f"the verification failed: {verified}")
        self.assertIn("project", verified)
        self.assertNotIn("error", deleted, f"the delete failed: {deleted}")
        body = deleted["body"]
        self.assertTrue(body["deleted"])

        # The record is gone, the trash holds folder + record, and the id is in
        # exactly one of the two lists.
        self._assert_clean_deletion(identifier, body["trash_name"])

        # The HTTP surface agrees with the store: the detail is gone, the listing
        # neither shows nor hides it.
        gone = client.get(f"/api/projects/{identifier}")
        self.assertEqual(gone.status_code, 404, gone.text)
        self.assertEqual(gone.json()["detail"]["code"], "unknown_project")
        self.assertNotIn(
            identifier,
            [row["project_id"] for row in client.get("/api/projects/lifecycle").json()],
        )
        rows = client.get("/api/projects/deleted").json()
        self.assertEqual([row["project_id"] for row in rows], [identifier])
        self.assertTrue(rows[0]["record_present"])
        self.assertTrue(rows[0]["folder_present"])

        # The late write, through the very guard that refused it, stays refused.
        self.projects._recompute_verification = original
        self.assertDeletedRefusal(
            lambda: self.store.write(project), "the late write"
        )

    def test_03_a_late_write_of_a_record_read_before_the_delete_is_refused(self) -> None:
        """The exact write path: the record object exists before DELETE, write after.

        This is the shape a resumed verification hands to ``ProjectStore.write`` with
        no service lock involved (as another process would). The guard is on the write
        itself, so the record is never re-created even then.
        """

        project = self._project_with_files()
        identifier = project.project_id
        client = self._client()

        # What the in-flight verification holds: the record it read, read before the
        # deletion completed.
        stale_record = self.projects.open_project(identifier)
        self.assertEqual(stale_record.project_id, identifier)

        deleted = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)

        caught = self.assertDeletedRefusal(
            lambda: self.store.write(stale_record), "the late write"
        )
        self.assertIn(identifier, str(caught))

        self._assert_clean_deletion(identifier, deleted.json()["trash_name"])
        self.assertEqual(
            client.get("/api/projects/lifecycle").json(),
            [],
            "the active listing shows a deleted project",
        )


class VerifyAfterDeleteTests(_DeletedProjectRaceCase):
    def test_04_verify_after_delete_is_a_safe_refusal_and_creates_nothing(self) -> None:
        project = self._project_with_files()
        identifier = project.project_id
        client = self._client()

        deleted = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        trash_name = deleted.json()["trash_name"]
        trash = self.deleted_root / trash_name
        before = self._tree_hashes(trash)

        refused = client.post(f"/api/projects/{identifier}/verify")
        self.assertEqual(refused.status_code, 409, refused.text)
        detail = refused.json()["detail"]
        self.assertEqual(detail["code"], "project_deleted")
        self.assertIn("no longer be modified", detail["error"])
        # A safe plain message: no host path, no store path, no secret shape.
        self.assertNotIn(str(self.base), refused.text)
        self.assertNotIn(str(self.root), refused.text)

        # Nothing was written: not the record, not the trash, not a version.
        self.assertFalse((self.root / f"{identifier}.json").exists())
        self.assertEqual(self._tree_hashes(trash), before)
        self._assert_clean_deletion(identifier, trash_name)
        self.assertEqual(
            client.get(f"/api/projects/{identifier}").status_code, 404
        )


class RestoreAfterDeleteTests(_DeletedProjectRaceCase):
    def test_05_delete_restore_verify_round_trip_clears_the_marker(self) -> None:
        project = self._project_with_files()
        identifier = project.project_id
        client = self._client()
        record_before = (self.root / f"{identifier}.json").read_bytes()
        workspace_before = self._tree_hashes(self.root / identifier)

        deleted = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(self.store.is_deleted(identifier))

        restored = client.post(f"/api/projects/{identifier}/restore-deleted")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["restored"])
        # The marker is gone, the move was byte-identical, and the trash is empty.
        self.assertFalse(self.store.is_deleted(identifier))
        self.assertEqual(self._tree_hashes(self.root / identifier), workspace_before)
        self.assertEqual((self.root / f"{identifier}.json").read_bytes(), record_before)
        self.assertEqual(client.get("/api/projects/deleted").json(), [])
        self.assertIn(identifier, self._active_ids())

        # A mutation guarded by the marker works again, and really persists.
        verified = client.post(f"/api/projects/{identifier}/verify")
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertTrue(verified.json()["verified"])
        self.assertEqual(verified.json()["verification"], "passed")
        self.assertIn(
            verified.json()["status"], {"COMPLETED", "NEEDS_CHANGES"}
        )

        staged = client.post(
            f"/api/projects/{identifier}/versions",
            json={"change": "add a helper for totals"},
        )
        self.assertEqual(staged.status_code, 200, staged.text)
        self.assertEqual(staged.json()["version"], 2)
        self.assertEqual(
            [item.version for item in self.projects.open_project(identifier).versions],
            [1, 2],
        )

    def test_06_the_active_list_and_the_trash_never_hold_the_same_id(self) -> None:
        project = self._project_with_files()
        identifier = project.project_id
        client = self._client()

        self.assertIn(identifier, self._active_ids())
        self.assertEqual(self._trash_ids(), [])
        self.assertEqual(client.get(f"/api/projects/{identifier}").status_code, 200)

        self.assertEqual(client.delete(f"/api/projects/{identifier}").status_code, 200)
        lifecycle = [
            row["project_id"] for row in client.get("/api/projects/lifecycle").json()
        ]
        trash = [
            row["project_id"] for row in client.get("/api/projects/deleted").json()
        ]
        self.assertNotIn(identifier, lifecycle)
        self.assertEqual(trash, [identifier])
        self.assertEqual(set(lifecycle) & set(trash), set())
        self.assertEqual(client.get(f"/api/projects/{identifier}").status_code, 404)

        self.assertEqual(
            client.post(f"/api/projects/{identifier}/restore-deleted").status_code, 200
        )
        lifecycle = [
            row["project_id"] for row in client.get("/api/projects/lifecycle").json()
        ]
        trash = [
            row["project_id"] for row in client.get("/api/projects/deleted").json()
        ]
        self.assertEqual(lifecycle, [identifier])
        self.assertEqual(trash, [])
        self.assertEqual(set(lifecycle) & set(trash), set())
        self.assertEqual(client.get(f"/api/projects/{identifier}").status_code, 200)


class GuardCoverageTests(_DeletedProjectRaceCase):
    def test_07_an_unknown_id_answers_404_and_nothing_is_created(self) -> None:
        missing = "proj-000000000000"
        client = self._client()

        answers = [
            client.get(f"/api/projects/{missing}"),
            client.post(f"/api/projects/{missing}/verify"),
            client.post(f"/api/projects/{missing}/archive"),
            client.post(f"/api/projects/{missing}/restore"),
            client.post(f"/api/projects/{missing}/versions", json={"change": "x"}),
            client.delete(f"/api/projects/{missing}"),
            client.post(f"/api/projects/{missing}/restore-deleted"),
        ]
        for answer in answers:
            self.assertEqual(answer.status_code, 404, answer.text)
            self.assertEqual(answer.json()["detail"]["code"], "unknown_project")
            self.assertNotIn(str(self.base), answer.text)

        self.assertFalse(self.deleted_root.exists())
        self.assertEqual(client.get("/api/projects/lifecycle").json(), [])
        self.assertEqual(client.get("/api/projects/deleted").json(), [])
        self.assertEqual(list(self.root.glob("proj-*.json")), [])
        with self.assertRaises(UnknownProject):
            self.store.restore_deleted(missing)

    def test_08_a_late_attach_or_version_write_is_refused_by_the_same_guard(self) -> None:
        project = self._project_with_files()
        identifier = project.project_id
        client = self._client()

        deleted = client.delete(f"/api/projects/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        trash = self.deleted_root / deleted.json()["trash_name"]
        before = self._tree_hashes(trash)

        late_calls = {
            "attach_run": lambda: self.projects.attach_run(
                identifier, self._record(run_id="run-late"), source_root=self.run_workspace
            ),
            "new_version": lambda: self.projects.new_version(identifier, "add x"),
            "set_preview_not_required": lambda: self.projects.set_preview_not_required(
                identifier, True
            ),
            "archive": lambda: self.projects.archive(identifier),
            "restore": lambda: self.projects.restore(identifier),
            "confirm": lambda: self.projects.confirm(identifier),
        }
        for label, call in late_calls.items():
            with self.subTest(operation=label):
                self.assertDeletedRefusal(call, label)
                self.assertFalse((self.root / f"{identifier}.json").exists())

        # The HTTP form of the version write answers the same typed refusal.
        refused = client.post(
            f"/api/projects/{identifier}/versions", json={"change": "add x"}
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["detail"]["code"], "project_deleted")

        # The trash is untouched by every refused write.
        self._assert_clean_deletion(identifier, deleted.json()["trash_name"])
        self.assertEqual(self._tree_hashes(trash), before)
        # The record read before the delete is still refused by the write itself.
        self.assertDeletedRefusal(
            lambda: self.store.write(project), "the late write"
        )


if __name__ == "__main__":
    unittest.main()
