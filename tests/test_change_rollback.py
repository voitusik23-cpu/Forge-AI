"""A change that breaks a working project is rolled back, and the card says so.

Why this file exists
--------------------

The owner's project (`.forge/projects-visible/Почтовый центр (proj-fce53337245f)`) was
a working application: `app.py` served `127.0.0.1:8790`, the desktop shortcut started
it, and a change run replaced that file with an answer that exits `1`. Nothing served
the port afterwards, the shortcut showed nothing -- and the record still said
`current_version: 8 · COMPLETED`, because the write gate only ever asked whether the
file **compiles**.

What is asserted here, against a real temporary project and the real writer:

1. a change that breaks the server is **restored byte-identically** (SHA-256 compared
   with the snapshot), the version does **not** advance, and the answer is the owner's
   sentence plus the reason from the run's own log;
2. a change that keeps the project answering is applied and the version **does**
   advance -- the guard is not a gate that refuses every change;
3. a project that was never launchable is not asked to launch (a first creation keeps
   the existing compile gate only);
4. a rollback that cannot restore a file is reported as **failed** and never as a
   success, and the card's evidence block says the restore is incomplete;
5. a launch check that could not start the project at all does not roll anything back
   (a refused spawn is not evidence about the project's own code).

Everything is offline: the launch probe is a fake injected into the real
:func:`app.projects.regression.verify_change`, while the writer, the snapshot directory,
the rollback and the version record are the product's own code. The real probe is
proved live, with the running application, outside this suite.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import unittest
from types import SimpleNamespace

from app.agent_runtime.code_writer import (
    STATUS_APPLIED,
    STATUS_VERIFICATION_FAILED,
    WriteResult,
    apply_changes,
)
from app.projects.models import (
    ACCEPTANCE_PASSED,
    ArtifactRef,
    ChangeRequest,
    LifecycleProject,
    ProjectStatus,
    ProjectVersion,
    VERIFICATION_FAILED,
    VERIFICATION_PASSED,
    utc_now,
)
from app.projects.presentation import (
    RUN_NO_CHANGE_NOTICE,
    RUN_ROLLED_BACK_NOTICE,
    build_project_presentation,
)
from app.projects.regression import (
    CHANGE_LAUNCH_BUDGET_SECONDS,
    OUTCOME_KEPT,
    OUTCOME_NOT_REQUIRED,
    OUTCOME_NOT_VERIFIED,
    OUTCOME_ROLLED_BACK,
    OUTCOME_ROLLBACK_FAILED,
    ProbeAnswer,
    ROLLBACK_PREFIX,
    SNAPSHOT_DIRECTORY,
    capture_launch_state,
    restore_snapshot,
    verify_change,
)
from app.projects.service import ProjectService, run_facts
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.reports.evidence import build_evidence
from app.tools.workspace import Workspace

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
PROJECTS_SCREEN = REPOSITORY / "ui" / "src" / "screens" / "Projects.tsx"
CHANGE_STATE = REPOSITORY / "ui" / "src" / "change-state.ts"
I18N = REPOSITORY / "ui" / "src" / "i18n.ts"

#: A change that keeps the application a server: it answers ``/api/health`` on its own
#: port and its own page.
WORKING_APP = '''\
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "v2"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            self._json({"ok": True, "app": "mail-ai"})
            return
        self._json({"ok": True, "version": VERSION})

    def _json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8790), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
'''

#: A change that breaks the application: it raises at import and never binds a port.
BROKEN_APP = '''\
import sys

raise SystemExit(1)
'''

#: What the model answer of such a change looks like to the real writer.
BROKEN_ANSWER = "```python path=app.py\n" + BROKEN_APP + "```\n"
WORKING_ANSWER = "```python path=app.py\n" + WORKING_APP + "```\n"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _answers(status: int, *, ran: bool = True, reason: str = "", tail=()) -> ProbeAnswer:
    """A probe answer with the same shape the real probe returns."""

    return ProbeAnswer(status=status, ran=ran, reason=reason, log_tail=tuple(tail))


class _WorkspaceCase(unittest.TestCase):
    """A real project folder in a temporary directory, removed afterwards."""

    def setUp(self) -> None:
        base = (
            REPOSITORY
            / ".forge"
            / "test-change-rollback"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.workspace = base / "project"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.entry = self._write("app.py", WORKING_APP.encode("utf-8"))

    # ------------------------------------------------------------------ helpers
    def _write(self, relative: str, data: bytes) -> pathlib.Path:
        target = self.workspace.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _read(self, relative: str) -> bytes:
        return self.workspace.joinpath(*relative.split("/")).read_bytes()

    def _path(self, relative: str) -> pathlib.Path:
        return self.workspace.joinpath(*relative.split("/"))

    def _snapshot(self, run_id: str, relative: str, data: bytes) -> pathlib.Path:
        target = (
            self.workspace
            / SNAPSHOT_DIRECTORY.replace("/", os.sep)
            / run_id
            / relative
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target


# --------------------------------------------------------------------------- #
# 1. the guard itself
# --------------------------------------------------------------------------- #


class RollbackTests(_WorkspaceCase):
    def test_01_a_change_that_breaks_the_server_is_restored_byte_identically(self) -> None:
        """THE OWNER'S CASE: app.py is replaced by an answer that exits 1."""

        original = self._read("app.py")
        original_digest = _digest(original)
        before = capture_launch_state(self.workspace)
        self.assertTrue(before.launchable, before.reason)
        self.assertEqual(before.entry_path, "app.py")

        # The change really writes (the real writer, the real backup directory) ...
        run_id = "run-rollback-1"
        applied = apply_changes(
            BROKEN_ANSWER, workspace=Workspace(root=self.workspace), run_id=run_id
        )
        self.assertEqual(applied.status, STATUS_APPLIED, applied.reason)
        self.assertNotEqual(_digest(self._read("app.py")), original_digest)

        # ... and the project no longer answers.
        verdict = verify_change(
            workspace=self.workspace,
            before=before,
            written_paths=[str(item["path"]) for item in applied.files],
            run_id=run_id,
            probe=lambda root, entry: _answers(
                0, reason="процесс завершился с кодом 1", tail=("SystemExit: 1",)
            ),
        )

        self.assertTrue(verdict.rolled_back, verdict.to_dict())
        self.assertEqual(verdict.outcome, OUTCOME_ROLLED_BACK)
        self.assertIs(verdict.to_dict()["rolled_back"], True)
        self.assertEqual(verdict.status, "rolled_back")
        # THE BYTES: byte-identical, proved by the digest of the file on disk now.
        self.assertEqual(_digest(self._read("app.py")), original_digest)
        self.assertEqual(verdict.restored[0].path, "app.py")
        self.assertEqual(verdict.restored[0].sha256, original_digest)
        self.assertEqual(verdict.restored[0].size, len(original))
        # THE SENTENCE: the owner's words plus the reason from the log.
        self.assertTrue(verdict.message.startswith(ROLLBACK_PREFIX), verdict.message)
        self.assertIn("кодом 1", verdict.message)
        self.assertIn("SystemExit", verdict.message)
        # THE SNAPSHOT it came from is named, so an operator can check it by hand.
        self.assertEqual(verdict.to_dict()["snapshot"], run_id)

    def test_02_a_change_that_keeps_it_working_is_kept(self) -> None:
        before = capture_launch_state(self.workspace)
        run_id = "run-keep-1"
        applied = apply_changes(
            WORKING_ANSWER, workspace=Workspace(root=self.workspace), run_id=run_id
        )
        self.assertEqual(applied.status, STATUS_APPLIED, applied.reason)
        changed = self._read("app.py")

        verdict = verify_change(
            workspace=self.workspace,
            before=before,
            written_paths=[str(item["path"]) for item in applied.files],
            run_id=run_id,
            probe=lambda root, entry: _answers(200),
        )

        self.assertEqual(verdict.outcome, OUTCOME_KEPT)
        self.assertFalse(verdict.rolled_back)
        self.assertEqual(verdict.http_status, 200)
        self.assertEqual(verdict.restored, ())
        # Nothing was put back: the change is what is on disk.
        self.assertEqual(self._read("app.py"), changed)
        self.assertEqual(_digest(self._read("app.py")), _digest(changed))

    def test_03_a_project_that_was_never_launchable_requires_no_launch(self) -> None:
        empty = self.base / "empty"
        empty.mkdir(parents=True, exist_ok=True)
        before = capture_launch_state(empty)
        self.assertFalse(before.launchable)
        self.assertEqual(before.reason, "no_entry_point")

        called: list[str] = []

        def _probe(root, entry):  # pragma: no cover - it must never be called
            called.append(str(root))
            return _answers(0, reason="never")

        verdict = verify_change(
            workspace=empty,
            before=before,
            written_paths=["index.html"],
            run_id="run-new-1",
            probe=_probe,
        )
        self.assertEqual(verdict.outcome, OUTCOME_NOT_REQUIRED)
        self.assertFalse(verdict.checked)
        self.assertFalse(verdict.rolled_back)
        self.assertEqual(called, [])

    def test_04_a_rollback_that_cannot_restore_is_a_failure_never_a_success(self) -> None:
        original = self._read("app.py")
        before = capture_launch_state(self.workspace)
        run_id = "run-rollback-2"
        # The snapshot holds the previous bytes, but the target is no longer a file:
        # the restore cannot replace it, which is the measured shape of a failed
        # rollback (a locked or replaced path).
        self._snapshot(run_id, "app.py", original)
        self._path("app.py").unlink()
        self._path("app.py").mkdir()

        verdict = verify_change(
            workspace=self.workspace,
            before=before,
            written_paths=["app.py"],
            run_id=run_id,
            probe=lambda root, entry: _answers(0, reason="порт не открылся"),
        )

        self.assertEqual(verdict.outcome, OUTCOME_ROLLBACK_FAILED)
        self.assertFalse(verdict.rolled_back)
        self.assertIs(verdict.to_dict()["rolled_back"], False)
        self.assertEqual(verdict.status, "failed")
        self.assertEqual(verdict.failed, ("app.py",))
        self.assertIn("НЕ откатаны полностью", verdict.message)
        # The evidence block repeats the same fact instead of reporting a restore.
        evidence = build_evidence(rollback=verdict.to_dict())
        verified_claims = " ".join(str(item.get("claim", "")) for item in evidence["verified"])
        self.assertNotIn("откатано", verified_claims)
        unknown = " ".join(str(item.get("item", "")) for item in evidence["unknown"])
        self.assertIn("откат не завершён", unknown)

    def test_05_a_probe_that_could_not_start_the_project_rolls_nothing_back(self) -> None:
        before = capture_launch_state(self.workspace)
        run_id = "run-unverified-1"
        applied = apply_changes(
            WORKING_ANSWER, workspace=Workspace(root=self.workspace), run_id=run_id
        )
        changed = self._read("app.py")

        verdict = verify_change(
            workspace=self.workspace,
            before=before,
            written_paths=[str(item["path"]) for item in applied.files],
            run_id=run_id,
            # ``ran = False``: the execution chain refused the spawn, so the attempt
            # says nothing about the project's own code.
            probe=lambda root, entry: _answers(
                0, ran=False, reason="approval_required"
            ),
        )

        self.assertEqual(verdict.outcome, OUTCOME_NOT_VERIFIED)
        self.assertFalse(verdict.rolled_back)
        self.assertEqual(verdict.restored, ())
        self.assertEqual(self._read("app.py"), changed)

    def test_06_a_file_the_change_created_is_removed_by_the_rollback(self) -> None:
        before = capture_launch_state(self.workspace)
        run_id = "run-rollback-3"
        answer = "```python path=helper.py\nprint('new')\n```\n"
        applied = apply_changes(
            answer, workspace=Workspace(root=self.workspace), run_id=run_id
        )
        self.assertEqual(applied.status, STATUS_APPLIED, applied.reason)
        self.assertTrue(self._path("helper.py").is_file())

        verdict = verify_change(
            workspace=self.workspace,
            before=before,
            written_paths=[str(item["path"]) for item in applied.files],
            run_id=run_id,
            probe=lambda root, entry: _answers(0, reason="порт не открылся"),
        )
        self.assertTrue(verdict.rolled_back)
        self.assertEqual(verdict.removed, ("helper.py",))
        self.assertFalse(self._path("helper.py").exists())

    def test_07_the_restore_is_deterministic_and_bounded(self) -> None:
        data = b"original\n"
        self._write("notes.txt", data)
        run_id = "run-restore-1"
        self._snapshot(run_id, "notes.txt", data)
        self._write("notes.txt", b"changed\n")
        restored, removed, failed = restore_snapshot(
            self.workspace, run_id, ["notes.txt"]
        )
        self.assertEqual(failed, ())
        self.assertEqual(removed, ())
        self.assertEqual(restored[0].sha256, _digest(data))
        self.assertEqual(self._read("notes.txt"), data)
        # An unusable run id never names a directory inside the workspace.
        for bad in ("", "..", "../escape", "run id"):
            self.assertEqual(
                restore_snapshot(self.workspace, bad, ["notes.txt"]),
                ((), (), ("notes.txt",)),
            )

    def test_08_the_budget_is_the_launch_readiness_budget(self) -> None:
        """The check gets the launch budget the product already uses, not its own."""

        from app.projects.launcher import READINESS_TIMEOUT_SECONDS

        self.assertEqual(CHANGE_LAUNCH_BUDGET_SECONDS, float(READINESS_TIMEOUT_SECONDS))


# --------------------------------------------------------------------------- #
# 2. the version record and the card
# --------------------------------------------------------------------------- #


def _artifact(path: str, data: bytes) -> ArtifactRef:
    return ArtifactRef(
        path=path,
        kind="python",
        size=len(data),
        sha256=_digest(data),
        change="update",
        verification_relevance="compiled",
    )


def _rollback_payload(*, rolled_back: bool = True) -> dict:
    data = WORKING_APP.encode("utf-8")
    return {
        "outcome": OUTCOME_ROLLED_BACK if rolled_back else OUTCOME_ROLLBACK_FAILED,
        "status": "rolled_back" if rolled_back else "failed",
        "rolled_back": rolled_back,
        "checked": True,
        "launchable_before": True,
        "launchable_after": False,
        "http_status": 0,
        "reason": "процесс завершился с кодом 1",
        "message": ROLLBACK_PREFIX + " (процесс завершился с кодом 1)",
        "snapshot": "run-api-4c2cbe0e",
        "restored": [
            {"path": "app.py", "sha256": _digest(data), "size": len(data)}
        ],
        "removed": [],
        "failed": [],
    }


class VersionRecordTests(unittest.TestCase):
    def test_09_a_version_keeps_the_rollback_and_its_own_words(self) -> None:
        version = ProjectVersion(
            version=9,
            created_at=utc_now(),
            run_id="run-api-4c2cbe0e",
            files=(),
            verification=VERIFICATION_FAILED,
            acceptance=ACCEPTANCE_PASSED,
            change_request=ChangeRequest(text="я не смог запустить программу", version=9),
            rollback=_rollback_payload(),
        )
        self.assertTrue(version.rolled_back)
        payload = version.to_dict()
        self.assertIs(payload["rolled_back"], True)
        self.assertEqual(payload["rollback"]["snapshot"], "run-api-4c2cbe0e")
        self.assertEqual(payload["file_count"], 0)
        # The record round-trips: a reloaded version still knows it was rolled back.
        again = ProjectVersion.from_dict(json.loads(json.dumps(payload)))
        self.assertEqual(again.rollback, version.rollback)
        self.assertTrue(again.rolled_back)

    def test_10_the_card_says_rolled_back_and_can_never_say_ready(self) -> None:
        files = [_artifact("app.py", WORKING_APP.encode("utf-8"))]
        record = {
            "project_id": "proj-000000000001",
            "name": "Почтовый центр",
            "status": "COMPLETED",
            "current_version": 2,
            "versions": [
                ProjectVersion(
                    version=2, created_at=utc_now(), run_id="run-2", files=tuple(files),
                    verification=VERIFICATION_PASSED, acceptance=ACCEPTANCE_PASSED,
                ).to_dict(),
                ProjectVersion(
                    version=3,
                    created_at=utc_now(),
                    run_id="run-3",
                    files=(),
                    verification=VERIFICATION_FAILED,
                    change_request=ChangeRequest(text="добавь ещё", version=3),
                    rollback=_rollback_payload(),
                ).to_dict(),
            ],
        }
        presentation = build_project_presentation(
            project={key: value for key, value in record.items() if key != "versions"},
            versions=record["versions"],
            workspace_exists=True,
        )
        self.assertFalse(presentation.is_ready)
        self.assertNotEqual(presentation.project_state, "ГОТОВО")
        self.assertTrue(presentation.rolled_back)
        self.assertIn(RUN_ROLLED_BACK_NOTICE, presentation.notices)
        self.assertNotIn(RUN_NO_CHANGE_NOTICE, presentation.notices)
        self.assertIn("откатаны", presentation.last_action)
        self.assertIn("откатана", presentation.next_step)
        # The proof is on the card: the snapshot and the restored digest.
        rows = dict(presentation.rollback)
        self.assertEqual(rows[".forge/backups"], "run-api-4c2cbe0e")
        self.assertIn("app.py", rows["Восстановлено"])
        self.assertIn(_digest(WORKING_APP.encode("utf-8"))[:16], rows["Восстановлено"])
        self.assertIs(presentation.to_dict()["rolled_back"], True)

    def test_11_a_change_that_wrote_nothing_keeps_the_old_sentence(self) -> None:
        """The rollback notice is for a rollback, not for every empty version."""

        record = {"status": "COMPLETED", "current_version": 2}
        versions = [
            ProjectVersion(
                version=2, created_at=utc_now(), run_id="run-2",
                files=(_artifact("app.py", b"x\n"),),
                verification=VERIFICATION_PASSED,
            ).to_dict(),
            ProjectVersion(
                version=3, created_at=utc_now(), run_id="run-3", files=(),
                verification=VERIFICATION_FAILED,
                change_request=ChangeRequest(text="добавь ещё", version=3),
            ).to_dict(),
        ]
        presentation = build_project_presentation(
            project=record, versions=versions, workspace_exists=True
        )
        self.assertFalse(presentation.rolled_back)
        self.assertIn(RUN_NO_CHANGE_NOTICE, presentation.notices)
        self.assertNotIn(RUN_ROLLED_BACK_NOTICE, presentation.notices)


class EvidenceTests(unittest.TestCase):
    def test_12_the_rollback_is_a_verified_fact_with_digests(self) -> None:
        data = WORKING_APP.encode("utf-8")
        evidence = build_evidence(
            outcome="COMPLETED",
            files=(),
            verification=VERIFICATION_FAILED,
            rollback=_rollback_payload(),
        )
        kinds = [str(item.get("kind", "")) for item in evidence["verified"]]
        self.assertIn("rollback", kinds)
        restored = [
            item
            for item in evidence["verified"]
            if str(item.get("kind", "")) == "file"
        ]
        self.assertEqual(len(restored), 1)
        self.assertIn(_digest(data), str(restored[0]["evidence"]))
        self.assertEqual(
            evidence["proof"]["rollback"]["restored"][0]["sha256"], _digest(data)
        )
        self.assertIs(evidence["proof"]["rollback"]["rolled_back"], True)
        unknown = " ".join(str(item.get("item", "")) for item in evidence["unknown"])
        self.assertIn("изменение не применено", unknown)

    def test_13_no_rollback_means_no_rollback_block(self) -> None:
        evidence = build_evidence(
            files=(_artifact("app.py", b"x\n"),),
            verification=VERIFICATION_PASSED,
            acceptance=ACCEPTANCE_PASSED,
            project={"version": 2},
        )
        self.assertIsNone(evidence["proof"]["rollback"])
        self.assertNotIn(
            "rollback", [str(item.get("kind", "")) for item in evidence["verified"]]
        )


# --------------------------------------------------------------------------- #
# 3. the change path: the version must not advance
# --------------------------------------------------------------------------- #


class _RunsAndVersionsCase(unittest.TestCase):
    """A project service rooted in a temporary directory."""

    def setUp(self) -> None:
        base = (
            REPOSITORY
            / ".forge"
            / "test-change-rollback"
            / f"store-{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.root = base / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
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

    def _project(self) -> LifecycleProject:
        """A completed project at v1 whose workspace holds a real app.py."""

        now = utc_now()
        project_id = "proj-aaaaaaaaaaaa"
        workspace = self.store.workspace_path(project_id, create=True)
        project = LifecycleProject(
            project_id=project_id,
            name="Почтовый центр",
            status=ProjectStatus.COMPLETED,
            workspace_id=project_id,
            project_path=str(workspace.relative_to(REPOSITORY).as_posix()),
            created_at=now,
            updated_at=now,
            current_version=1,
            versions=[
                ProjectVersion(
                    version=1,
                    created_at=now,
                    run_id="run-1",
                    files=(_artifact("app.py", WORKING_APP.encode("utf-8")),),
                    verification=VERIFICATION_PASSED,
                    acceptance=ACCEPTANCE_PASSED,
                )
            ],
        )
        workspace.joinpath("app.py").write_bytes(WORKING_APP.encode("utf-8"))
        self.store.write(project)
        return project

    @staticmethod
    def _run_record(rollback: dict, *, status: str, verification: str) -> dict:
        return {
            "run_id": "run-change-1",
            "state": "COMPLETED",
            "classification": {
                "outcome": "TERMINAL_SUCCESS",
                "is_success": True,
                "is_terminal": True,
            },
            "project_state": {
                "code_write": {
                    "status": status,
                    "verification": verification,
                    "files": [],
                    "reason": str(rollback.get("message", "")),
                    "rollback": rollback,
                }
            },
            "provider_used": "mock",
            "model_used": "deterministic-offline",
            "updated_at": utc_now(),
        }

    def test_14_a_rolled_back_change_does_not_advance_the_version(self) -> None:
        project = self._project()
        staged = self.projects.new_version(project.project_id, "я не смог запустить")
        self.assertEqual(staged.version, 2)
        record = self._run_record(
            _rollback_payload(), status=STATUS_VERIFICATION_FAILED,
            verification=VERIFICATION_FAILED,
        )
        facts = run_facts(record)
        self.assertTrue(facts.rollback.get("rolled_back"))
        attached = self.projects.attach_run(
            project.project_id,
            record,
            source_root=self.store.workspace_path(project.project_id),
        )
        # THE PREVIOUS VERSION STAYS CURRENT.
        self.assertEqual(attached.current_version, 1)
        attempt = attached.version(2)
        self.assertEqual(attempt.files, ())
        self.assertEqual(attempt.verification, VERIFICATION_FAILED)
        self.assertTrue(attempt.rolled_back)
        self.assertEqual(attempt.rollback["snapshot"], "run-api-4c2cbe0e")
        # The card derives «изменения откатаны» from the record itself.
        presentation = build_project_presentation(
            project=attached.to_dict(),
            versions=[item.to_dict() for item in attached.versions],
            workspace_exists=True,
        )
        self.assertTrue(presentation.rolled_back)
        self.assertIn(RUN_ROLLED_BACK_NOTICE, presentation.notices)
        self.assertFalse(presentation.is_ready)

    def test_15_a_kept_change_does_advance_the_version(self) -> None:
        project = self._project()
        self.projects.new_version(project.project_id, "добавь кнопку")
        record = {
            "run_id": "run-change-2",
            "state": "COMPLETED",
            "classification": {
                "outcome": "TERMINAL_SUCCESS",
                "is_success": True,
                "is_terminal": True,
            },
            "project_state": {
                "code_write": {
                    "status": STATUS_APPLIED,
                    "verification": VERIFICATION_PASSED,
                    "files": [{"path": "app.py", "action": "update"}],
                    "rollback": {
                        "outcome": OUTCOME_KEPT,
                        "rolled_back": False,
                        "checked": True,
                        "launchable_before": True,
                        "launchable_after": True,
                        "http_status": 200,
                    },
                }
            },
            "updated_at": utc_now(),
        }
        attached = self.projects.attach_run(
            project.project_id,
            record,
            source_root=self.store.workspace_path(project.project_id),
        )
        self.assertEqual(attached.current_version, 2)
        self.assertEqual([item.path for item in attached.version(2).files], ["app.py"])
        self.assertFalse(attached.version(2).rolled_back)
        self.assertEqual(
            attached.version(2).rollback.get("outcome"), OUTCOME_KEPT
        )


# --------------------------------------------------------------------------- #
# 4. the writer's own report
# --------------------------------------------------------------------------- #


class WriterReportTests(unittest.TestCase):
    def test_16_a_write_result_carries_its_rollback(self) -> None:
        from app.agent_runtime.code_writer import render_write_report

        result = WriteResult(
            status=STATUS_VERIFICATION_FAILED,
            verification=VERIFICATION_FAILED,
            reason=ROLLBACK_PREFIX,
            rollback=_rollback_payload(),
        )
        self.assertTrue(result.was_rolled_back)
        payload = result.to_dict()
        self.assertIs(payload["rollback"]["rolled_back"], True)
        report = render_write_report(result)
        self.assertIn("Rollback:", report)
        self.assertIn("restored from the snapshot", report)
        # A plain result has no rollback at all.
        self.assertIs(WriteResult(status=STATUS_APPLIED).to_dict()["rollback"], None)
        self.assertFalse(WriteResult(status=STATUS_APPLIED).was_rolled_back)


# --------------------------------------------------------------------------- #
# 5. the screen must say it in the owner's language
# --------------------------------------------------------------------------- #


class ScreenTests(unittest.TestCase):
    def test_17_the_screen_renders_the_rollback_from_the_record(self) -> None:
        screen = PROJECTS_SCREEN.read_text(encoding="utf-8")
        for marker in (
            "rolled_back",
            "rollback",
        ):
            self.assertIn(marker, screen)
        # The client type carries the verdict, so the card cannot drop it silently.
        forge = (REPOSITORY / "ui" / "src" / "api" / "forge.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("rolled_back", forge)
        self.assertIn("rollback", forge)
        # The sentence is the backend's own, printed from i18n, not composed inline.
        i18n = I18N.read_text(encoding="utf-8")
        self.assertIn("projects.rollback.notice", i18n)
        self.assertIn("Изменения откатаны", i18n)
        self.assertIn("projects.rollback.notice", screen)


# --------------------------------------------------------------------------- #
# 6. the change field is cleared after a completed send, kept after a failed one
# --------------------------------------------------------------------------- #


def _node(script: str) -> str:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - node is present on this host
        raise unittest.SkipTest("node is not installed; the UI rule cannot be executed")
    completed = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=str(REPOSITORY),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout


class ChangeFieldTests(unittest.TestCase):
    """The rule the textarea really uses, executed from ``ui/src/change-state.ts``."""

    @classmethod
    def _ask(cls, expression: str):
        script = (
            f"import * as s from {json.dumps(CHANGE_STATE.as_uri())};"
            "process.stdout.write(JSON.stringify(" + expression + "));"
        )
        return json.loads(_node(script))

    def test_18_a_completed_send_clears_the_field(self) -> None:
        """A success and a refusal are both "sent, and answered": the field is free."""

        self.assertEqual(
            self._ask('s.changeDraftAfterDispatch({ok: true})'), "clear"
        )
        # A refusal: the Core answered with an HTTP status and a body.
        self.assertEqual(
            self._ask(
                's.changeDraftAfterDispatch({ok: false, failure:'
                '{kind: "error", status: 409, path: "/api/tasks/run", detail: "refused"}})'
            ),
            "clear",
        )
        self.assertEqual(
            self._ask(
                's.changeDraftAfterDispatch({ok: false, failure:'
                '{kind: "unauthorized", status: 401, path: "/x", detail: "no access"}})'
            ),
            "clear",
        )
        # No failure object at all (a completed dispatch with nothing to report).
        self.assertEqual(self._ask("s.changeDraftAfterDispatch(null)"), "clear")

    def test_19_a_request_that_never_reached_the_core_keeps_the_field(self) -> None:
        """Nothing was sent, so nothing is lost: the owner's words stay exactly as typed."""

        self.assertIs(
            self._ask(
                's.dispatchReachedTheCore({kind: "disconnected", status: 0,'
                ' path: "/api/tasks/run", detail: "fetch failed"})'
            ),
            False,
        )
        self.assertEqual(
            self._ask(
                's.changeDraftAfterDispatch({ok: false, failure:'
                '{kind: "disconnected", status: 0, path: "/api/tasks/run",'
                ' detail: "Failed to fetch"}})'
            ),
            "keep",
        )
        # The positive control: every other kind is an answer.
        for kind in ("error", "unauthorized", ""):
            with self.subTest(kind=kind):
                self.assertEqual(
                    self._ask(
                        "s.changeDraftAfterDispatch({ok: false, failure:"
                        + json.dumps({"kind": kind, "status": 500, "path": "/x"})
                        + "})"
                    ),
                    "clear",
                )

    def test_20_the_screen_uses_the_rule_instead_of_re_implementing_it(self) -> None:
        screen = PROJECTS_SCREEN.read_text(encoding="utf-8")
        self.assertIn("dispatchReachedTheCore", screen)
        self.assertIn("settleChangeDraft", screen)
        # The clear happens on the completed paths, and only those: the two send
        # failures are the ones guarded by the rule.
        self.assertIn("settleChangeDraft(stagedOutcome.failure)", screen)
        self.assertIn("settleChangeDraft(outcome.failure)", screen)
        self.assertIn('setChange("")', screen)
        self.assertIn("clearAttachments()", screen)


if __name__ == "__main__":
    unittest.main()
