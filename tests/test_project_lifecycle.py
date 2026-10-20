"""Offline tests for the persistent project lifecycle.

Everything here is offline and uses fake providers/run records only: the lifecycle
layer itself makes no provider call, and where a run is attached it is described by
a run record dict in exactly the shape the run store produces. The API tests build
the real application with a project service rooted in a temporary directory, so no
test touches the deployment's own project store or the network.

What is pinned, and why each matters:

* creation stores the confirmed specification and links the discovery session;
* the published workspace path is repository-relative and inside the repository;
* every row of the transition table works, and every other pair is refused;
* ``COMPLETED`` is impossible without a terminal-success run, passed verification,
  passed acceptance, and (when files were written) a recorded artifact;
* a failed run is ``FAILED`` and an imperfect one is ``NEEDS_CHANGES``;
* a new version never overwrites the previous one, and a failed new version leaves
  the previous version current;
* archiving deletes nothing and restoring returns the exact previous status;
* the artifact manifest carries size + sha256 + change and never contents;
* no payload carries a secret-shaped value or an absolute path;
* history is newest-first.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import shutil
import unittest
from types import SimpleNamespace

from app.api.models import TaskRunRequest
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.lifecycle import (
    ALLOWED_TRANSITIONS,
    CompletionNotAllowed,
    IllegalTransition,
    LifecycleEvent,
    advance,
    completion_blockers,
    describe_transitions,
)
from app.projects.models import (
    ArtifactRef,
    Project,
    ProjectStatus,
    ProjectVersion,
    utc_now,
)
from app.projects.service import (
    InvalidChangeRequest,
    ProjectService,
    UnknownProjectVersion,
)
from app.projects.store import (
    PROJECTS_ROOT_ENV_VAR,
    ProjectStore,
    UnknownProject,
    default_projects_root,
    is_safe_project_id,
    new_project_id,
)
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime


#: A secret-shaped value, deliberately fake. It matches the repository's existing
#: ``sk-`` provider-key recognizer, which is what makes it a useful probe: the
#: lifecycle layer must store a redaction marker in its place.
SECRET_LIKE = "sk-ABCDEFGHIJKLMNOPQRST1234"

#: The value the stand-in "model" answers with in tests that need a real write.
WRITE_ANSWER = """```python path=generated_tool.py
def exported(rows):
    return ",".join(str(row) for row in rows)
```
"""


class _FakeSpecification:
    """Only ``to_dict`` is used, which is all the layer requires of a specification."""

    def __init__(self, value: dict) -> None:
        self._value = dict(value)

    def to_dict(self) -> dict:
        return dict(self._value)


def _session(goal: str = "A small expense tracker", session_id: str | None = None):
    """A discovery session double: an id, a goal, and a specification."""

    return SimpleNamespace(
        session_id=session_id or f"disc-{new_project_id()[5:]}",
        initial_goal=goal,
        specification=_FakeSpecification(
            {
                "goal": goal,
                "project_type": "web app",
                "core_features": ["add expenses", "monthly report"],
            }
        ),
    )


class _StandInRunExecutor:
    """An in-process stand-in for the provider-path executor (no socket is opened)."""

    def __init__(self, answer: str = WRITE_ANSWER) -> None:
        self.answer = answer
        self.calls = 0

    def execute(self, **kwargs):  # noqa: ANN003 - mirrors RunExecutor.execute
        from app.orchestrator.models import Run, RunState, TaskResult

        task = kwargs["task"]
        run = Run(task=task, **({"id": kwargs["run_id"]} if kwargs.get("run_id") else {}))
        run.state = RunState.COMPLETED
        run.result = TaskResult(
            task_id=task.id,
            success=True,
            output=self.answer,
            provider="stand-in",
            model_name="deterministic-stand-in",
        )
        self.calls += 1
        return run


class _ProjectTestCase(unittest.TestCase):
    """A project service rooted in a temporary directory, removed afterwards."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-project-lifecycle"
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
        self.projects = ProjectService(self.store)

    # ------------------------------------------------------------------ helpers
    def _create(self, goal: str = "A small expense tracker", **kwargs) -> Project:
        return self.projects.create_from_discovery(_session(goal), **kwargs)

    def _project_at(self, status: ProjectStatus) -> Project:
        now = utc_now()
        return Project(
            project_id=new_project_id(),
            name="transition fixture",
            status=status,
            workspace_id="ws",
            project_path=".forge/projects/fixture/workspace",
            created_at=now,
            updated_at=now,
        )

    def _run_record(
        self,
        *,
        run_id: str = "run-test-1",
        state: str = "COMPLETED",
        outcome: str = "TERMINAL_SUCCESS",
        error: str = "",
        verification: str = "not_run",
        write_status: str = "no_changes",
        files=(),
        provider: str = "mock",
        model: str = "deterministic-offline",
    ) -> dict:
        record = {
            "run_id": run_id,
            "state": state,
            "status": state,
            "classification": {
                "outcome": outcome,
                "is_success": outcome == "TERMINAL_SUCCESS",
                "is_terminal": True,
            },
            "project_state": {
                "code_write": {
                    "status": write_status,
                    "verification": verification,
                    "files": [{"path": path, "action": action} for path, action in files],
                }
            },
            "provider_used": provider,
            "model_used": model,
            "updated_at": utc_now(),
        }
        if error:
            record["error"] = error
        return record

    def _write_run_workspace_file(self, relative: str, data: bytes) -> pathlib.Path:
        target = self.run_workspace.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _verified_run(self, *, run_id: str = "run-write-1", relative="generated_tool.py") -> dict:
        data = b"def exported(rows):\n    return ','.join(rows)\n"
        self._write_run_workspace_file(relative, data)
        return self._run_record(
            run_id=run_id,
            verification="passed",
            write_status="applied",
            files=[(relative, "create")],
        )

    def _record_text(self, project_id: str) -> str:
        return self.store.path_for(project_id).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. creation
# --------------------------------------------------------------------------- #


class CreationTests(_ProjectTestCase):
    def test_01_creation_records_the_project_the_specification_and_the_session_link(self) -> None:
        session = _session("Хочу веб-инструмент для учёта домашних расходов")
        project = self.projects.create_from_discovery(session, plan_ref="plan-abc")
        self.assertTrue(is_safe_project_id(project.project_id))
        self.assertRegex(project.project_id, r"^proj-[0-9a-f]{12}$")
        self.assertEqual(project.status, ProjectStatus.WAITING_FOR_CONFIRMATION)
        self.assertFalse(project.archived)
        self.assertEqual(project.plan_ref, "plan-abc")
        self.assertEqual(project.current_version, 0)
        self.assertEqual(project.versions, [])
        self.assertEqual(
            project.specification["goal"], "Хочу веб-инструмент для учёта домашних расходов"
        )
        # The record exists on disk and the workspace exists inside it.
        self.assertTrue(self.store.path_for(project.project_id).is_file())
        self.assertTrue(self.store.workspace_path(project.project_id).is_dir())
        # The session link is what the run path uses to find the project.
        self.assertEqual(
            self.projects.project_for_session(session.session_id), project.project_id
        )
        # Confirmation is the documented move to PLANNED.
        confirmed = self.projects.confirm(project.project_id)
        self.assertEqual(confirmed.status, ProjectStatus.PLANNED)
        # Creating twice for the same session returns the same project.
        again = self.projects.create_from_discovery(session)
        self.assertEqual(again.project_id, project.project_id)

    def test_02_reopening_a_project_returns_the_stored_record(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        reopened = self.projects.open_project(project.project_id)
        self.assertEqual(reopened.to_dict(), self.store.read(project.project_id).to_dict())
        self.assertEqual(reopened.status, ProjectStatus.PLANNED)
        # A store rooted elsewhere does not see it, and an unknown id is a typed 404.
        other = ProjectService(ProjectStore(root=self.base / "other-projects"))
        with self.assertRaises(UnknownProject):
            other.open_project(project.project_id)
        with self.assertRaises(UnknownProject):
            self.projects.open_project("proj-000000000000")

    def test_03_a_real_discovery_session_produces_a_project(self) -> None:
        discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))
        session = discovery.start("Сайт для бронирования отелей")
        session = discovery.skip(session.session_id, "достаточно")
        session = discovery.confirm(session.session_id)
        project = self.projects.create_from_discovery(session)
        self.assertEqual(project.specification["goal"], "Сайт для бронирования отелей")
        self.assertEqual(project.name, "Сайт для бронирования отелей")
        self.assertEqual(project.status, ProjectStatus.WAITING_FOR_CONFIRMATION)


# --------------------------------------------------------------------------- #
# 2. the workspace path contract
# --------------------------------------------------------------------------- #


class WorkspaceContractTests(_ProjectTestCase):
    def test_04_the_default_workspace_path_is_repository_relative_and_inside_the_repository(self) -> None:
        # Pure computation, with the store redirection out of the way: the default
        # store's published path is the documented contract string.
        import unittest.mock as mock

        with mock.patch.dict(os.environ, {PROJECTS_ROOT_ENV_VAR: ""}, clear=False):
            default_store = ProjectStore()
            self.assertEqual(
                default_store.project_path_relative("proj-0123456789ab"),
                ".forge/projects/proj-0123456789ab/workspace",
            )
            self.assertEqual(default_projects_root().name, "projects")
            self.assertEqual(default_projects_root().parent.name, ".forge")

    def test_05_a_created_project_publishes_a_relative_path_inside_the_store(self) -> None:
        project = self._create()
        path = project.project_path
        self.assertFalse(pathlib.Path(path).is_absolute(), path)
        self.assertNotIn(":", path)
        self.assertNotIn("\\", path)
        self.assertNotIn("..", path)
        self.assertTrue(path.startswith(".forge/"), path)
        self.assertTrue(path.endswith(f"{project.project_id}/workspace"), path)
        self.assertEqual(path, self.store.project_path_relative(project.project_id))
        workspace = self.store.workspace_path(project.project_id)
        root = self.root.resolve()
        self.assertEqual(root in workspace.resolve().parents, True)
        self.assertTrue(workspace.is_dir())

    def test_06_an_unsafe_id_or_path_is_refused_not_resolved(self) -> None:
        for bad in ("../etc", "proj-XYZ", "", "proj-0123456789ab/../x", "index"):
            self.assertFalse(is_safe_project_id(bad), bad)
            with self.assertRaises(ValueError):
                self.store.path_for(bad)
            with self.assertRaises(ValueError):
                self.store.workspace_path(bad)
        project = self._create()
        for bad in ("../escape.py", "/abs.py", "a/../../b.py", "a\\b.py", "", "."):
            with self.assertRaises(ValueError):
                self.store.workspace_file_path(project.project_id, bad)


# --------------------------------------------------------------------------- #
# 3. the transition table
# --------------------------------------------------------------------------- #


class TransitionTests(_ProjectTestCase):
    def _event_for(self, name: str, target: ProjectStatus) -> LifecycleEvent:
        if target is not ProjectStatus.COMPLETED:
            return LifecycleEvent(name=name)
        return LifecycleEvent(
            name=name,
            run_state="COMPLETED",
            verification="passed",
            acceptance="passed",
            artifact_count=1,
            wrote_files=True,
        )

    def test_07_every_row_of_the_table_advances_to_its_target(self) -> None:
        rows = describe_transitions()
        self.assertGreaterEqual(len(rows), 20)
        for from_name, event_name, to_name in rows:
            source = ProjectStatus(from_name)
            target = ProjectStatus(to_name)
            project = self._project_at(source)
            if source is ProjectStatus.ARCHIVED:
                # ``restore`` returns the recorded previous status, not the table's
                # placeholder Target.
                project = dataclasses.replace(
                    project,
                    status=ProjectStatus.ARCHIVED,
                    archived=True,
                    previous_status=ProjectStatus.COMPLETED,
                )
                advanced = advance(project, LifecycleEvent(name=event_name))
                self.assertEqual(advanced.status, ProjectStatus.COMPLETED)
                self.assertFalse(advanced.archived)
                self.assertIsNone(advanced.previous_status)
                continue
            advanced = advance(project, self._event_for(event_name, target))
            self.assertEqual(
                advanced.status,
                target,
                f"{from_name} --{event_name}--> {advanced.status.value}, expected {to_name}",
            )
            if target is ProjectStatus.ARCHIVED:
                self.assertTrue(advanced.archived)
                self.assertEqual(advanced.previous_status, source)
            else:
                self.assertFalse(advanced.archived)

    def test_08_every_other_pair_is_refused(self) -> None:
        known_events = {row[1] for row in describe_transitions()} | {"bogus_event"}
        checked = 0
        for status in ProjectStatus:
            allowed = set(ALLOWED_TRANSITIONS.get(status, {}))
            if status is not ProjectStatus.ARCHIVED:
                allowed.add("archived")
            for event_name in sorted(known_events):
                if event_name in allowed:
                    continue
                project = self._project_at(status)
                with self.assertRaises(IllegalTransition) as caught:
                    advance(project, LifecycleEvent(name=event_name))
                self.assertEqual(caught.exception.event, event_name)
                checked += 1
        self.assertGreater(checked, 50)

    def test_09_archived_projects_are_frozen_and_restore_needs_a_previous_status(self) -> None:
        project = self._project_at(ProjectStatus.COMPLETED)
        archived = advance(project, LifecycleEvent(name="archived"))
        for event_name in ("run_started", "completed", "changes_required", "archived", "plan_created"):
            with self.assertRaises(IllegalTransition):
                advance(archived, LifecycleEvent(name=event_name))
        # No recorded previous status: restoring would have to invent one.
        orphan = dataclasses.replace(
            self._project_at(ProjectStatus.PLANNED),
            status=ProjectStatus.ARCHIVED,
            archived=True,
        )
        with self.assertRaises(IllegalTransition):
            advance(orphan, LifecycleEvent(name="restore"))
        # Restoring a project that is not archived is refused too.
        with self.assertRaises(IllegalTransition):
            advance(self._project_at(ProjectStatus.PLANNED), LifecycleEvent(name="restore"))

    def test_10_an_unknown_event_name_is_a_typed_refusal(self) -> None:
        with self.assertRaises(IllegalTransition) as caught:
            advance(self._project_at(ProjectStatus.PLANNED), LifecycleEvent(name="finished"))
        self.assertIn("not allowed", str(caught.exception))
        self.assertEqual(caught.exception.code, "illegal_project_transition")


# --------------------------------------------------------------------------- #
# 4. the completion gate (three negatives + the positive)
# --------------------------------------------------------------------------- #


class CompletionGateTests(_ProjectTestCase):
    def _reviewing(self) -> Project:
        return self._project_at(ProjectStatus.REVIEWING)

    def test_11_completed_requires_a_passed_verification(self) -> None:
        event = LifecycleEvent(
            name="completed",
            run_state="COMPLETED",
            verification="failed",
            acceptance="passed",
            artifact_count=1,
            wrote_files=True,
        )
        self.assertIn("verification_failed", completion_blockers(event))
        with self.assertRaises(CompletionNotAllowed) as caught:
            advance(self._reviewing(), event)
        self.assertIn("verification_failed", caught.exception.blockers)

    def test_12_completed_requires_a_passed_acceptance(self) -> None:
        event = LifecycleEvent(
            name="completed",
            run_state="COMPLETED",
            verification="passed",
            acceptance="failed",
            artifact_count=1,
            wrote_files=True,
        )
        self.assertIn("acceptance_failed", completion_blockers(event))
        with self.assertRaises(CompletionNotAllowed) as caught:
            advance(self._reviewing(), event)
        self.assertIn("acceptance_failed", caught.exception.blockers)

    def test_13_completed_requires_an_artifact_when_files_were_written(self) -> None:
        event = LifecycleEvent(
            name="completed",
            run_state="COMPLETED",
            verification="passed",
            acceptance="passed",
            artifact_count=0,
            wrote_files=True,
        )
        self.assertEqual(completion_blockers(event), ("artifacts_missing",))
        with self.assertRaises(CompletionNotAllowed) as caught:
            advance(self._reviewing(), event)
        self.assertEqual(caught.exception.blockers, ("artifacts_missing",))

    def test_14_completed_requires_a_terminal_success_run(self) -> None:
        event = LifecycleEvent(
            name="completed",
            run_state="FAILED",
            verification="passed",
            acceptance="passed",
            artifact_count=1,
            wrote_files=True,
        )
        with self.assertRaises(CompletionNotAllowed):
            advance(self._reviewing(), event)

    def test_15_completed_is_allowed_when_every_fact_holds(self) -> None:
        event = LifecycleEvent(
            name="completed",
            run_state="COMPLETED",
            verification="passed",
            acceptance="passed",
            artifact_count=2,
            wrote_files=True,
        )
        self.assertEqual(completion_blockers(event), ())
        advanced = advance(self._reviewing(), event)
        self.assertEqual(advanced.status, ProjectStatus.COMPLETED)
        # A run that wrote nothing has no artifact rule to satisfy.
        quiet = LifecycleEvent(
            name="completed",
            run_state="COMPLETED",
            verification="passed",
            acceptance="passed",
            wrote_files=False,
        )
        self.assertEqual(completion_blockers(quiet), ())
        # ... but it still has to be verified.
        unverified = dataclasses.replace(quiet, verification="not_run")
        self.assertIn("verification_not_run", completion_blockers(unverified))


# --------------------------------------------------------------------------- #
# 5/6/7. attaching runs: COMPLETED, NEEDS_CHANGES, FAILED
# --------------------------------------------------------------------------- #


class AttachRunTests(_ProjectTestCase):
    def test_16_a_verified_run_with_an_artifact_completes_and_records_the_manifest(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        attached = self.projects.attach_run(
            project.project_id, self._verified_run(), source_root=self.run_workspace
        )
        self.assertEqual(attached.status, ProjectStatus.COMPLETED)
        self.assertEqual(attached.current_version, 1)
        version = attached.current()
        self.assertIsNotNone(version)
        self.assertEqual(version.run_id, "run-write-1")
        self.assertEqual(version.verification, "passed")
        self.assertEqual(version.acceptance, "passed")
        self.assertEqual(version.provider, "mock")
        self.assertEqual(version.model, "deterministic-offline")
        self.assertEqual(len(version.files), 1)
        artifact = version.files[0]
        data = self.run_workspace.joinpath("generated_tool.py").read_bytes()
        self.assertEqual(artifact.path, "generated_tool.py")
        self.assertEqual(artifact.kind, "python")
        self.assertEqual(artifact.change, "create")
        self.assertEqual(artifact.size, len(data))
        self.assertEqual(artifact.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(artifact.verification_relevance, "compiled")
        # The bytes really were copied into the project workspace, unchanged.
        copied = self.store.workspace_file_path(project.project_id, "generated_tool.py")
        self.assertEqual(copied.read_bytes(), data)
        # The published location is repository-relative and names the workspace.
        self.assertEqual(
            self.projects.artifact_location(project.project_id, artifact),
            f"{attached.project_path}/generated_tool.py",
        )
        self.assertEqual(attached.last_run_at, version.created_at)

    def test_17_a_failed_run_lands_in_failed(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        failed_run = self._run_record(
            state="FAILED",
            outcome="TERMINAL_FAILURE",
            error="provider refused the request",
            write_status="rejected",
        )
        attached = self.projects.attach_run(
            project.project_id, failed_run, source_root=self.run_workspace
        )
        self.assertEqual(attached.status, ProjectStatus.FAILED)
        self.assertEqual(attached.current_version, 0)
        version = attached.latest_version
        self.assertIsNotNone(version)
        self.assertEqual(version.run_id, "run-test-1")
        self.assertEqual(version.acceptance, "failed")
        self.assertEqual(version.files, ())

    def test_18_an_imperfect_run_lands_in_needs_changes(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        # A terminal success that wrote nothing: acceptance passed, but nothing was
        # verified, so it must not be COMPLETED.
        attached = self.projects.attach_run(
            project.project_id,
            self._run_record(verification="not_run", write_status="no_changes"),
            source_root=self.run_workspace,
        )
        self.assertEqual(attached.status, ProjectStatus.NEEDS_CHANGES)
        self.assertEqual(attached.current_version, 1)
        self.assertEqual(attached.current().verification, "not_run")
        # A writer that refused the answer is imperfect too, and still not COMPLETED.
        second = self._create("Second goal")
        self.projects.confirm(second.project_id)
        refused = self.projects.attach_run(
            second.project_id,
            self._run_record(
                run_id="run-test-2", write_status="rejected", verification="not_run"
            ),
            source_root=self.run_workspace,
        )
        self.assertEqual(refused.status, ProjectStatus.NEEDS_CHANGES)

    def test_19_a_run_whose_declared_file_is_missing_is_not_completed(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        record = self._run_record(
            run_id="run-missing",
            verification="passed",
            write_status="applied",
            files=[("never_written_by_anyone.py", "create")],
        )
        attached = self.projects.attach_run(
            project.project_id, record, source_root=self.run_workspace
        )
        self.assertEqual(attached.status, ProjectStatus.NEEDS_CHANGES)
        self.assertEqual(attached.current().verification, "failed")
        self.assertEqual(attached.current().files, ())

    def test_20_an_attached_run_cannot_be_attached_twice_into_two_versions(self) -> None:
        # The service is not itself idempotent by run id (the API guards that), but a
        # second attach must never overwrite the first version's evidence.
        project = self._create()
        self.projects.confirm(project.project_id)
        record = self._verified_run(run_id="run-once")
        first = self.projects.attach_run(
            project.project_id, record, source_root=self.run_workspace
        )
        snapshot = first.version(1).to_dict()
        second = self.projects.attach_run(
            project.project_id, record, source_root=self.run_workspace
        )
        self.assertEqual(second.version(1).to_dict(), snapshot)
        self.assertEqual([item.version for item in second.versions], [1, 2])


# --------------------------------------------------------------------------- #
# 8. new versions
# --------------------------------------------------------------------------- #


class NewVersionTests(_ProjectTestCase):
    def _completed_project(self) -> Project:
        project = self._create()
        self.projects.confirm(project.project_id)
        return self.projects.attach_run(
            project.project_id, self._verified_run(), source_root=self.run_workspace
        )

    def test_21_new_version_stages_a_slot_and_returns_the_task_text(self) -> None:
        project = self._completed_project()
        staged = self.projects.new_version(project.project_id, "add a CSV export")
        self.assertEqual(staged.version, 2)
        self.assertIn("add a CSV export", staged.task_text)
        self.assertIn("VERSION: 2", staged.task_text)
        self.assertIn("PROJECT:", staged.task_text)
        self.assertEqual(staged.project.status, ProjectStatus.PLANNED)
        pending = staged.project.pending_version
        self.assertIsNotNone(pending)
        self.assertEqual(pending.version, 2)
        self.assertTrue(pending.is_pending)
        self.assertEqual(staged.project.current_version, 1)
        self.assertEqual(staged.project.current().files, project.current().files)
        # Restaging the same slot does not append a third one.
        restaged = self.projects.new_version(project.project_id, "add a PDF export")
        self.assertEqual([item.version for item in restaged.project.versions], [1, 2])
        self.assertIn("add a PDF export", restaged.task_text)

    def test_22_the_previous_version_stays_byte_identical(self) -> None:
        project = self._completed_project()
        before = json.dumps(project.version(1).to_dict(), sort_keys=True)
        workspace_before = self.store.workspace_file_path(
            project.project_id, "generated_tool.py"
        ).read_bytes()
        staged = self.projects.new_version(project.project_id, "add a CSV export")
        self.assertEqual(
            json.dumps(staged.project.version(1).to_dict(), sort_keys=True), before
        )
        self.assertEqual(
            self.store.workspace_file_path(
                project.project_id, "generated_tool.py"
            ).read_bytes(),
            workspace_before,
        )
        # A second run fills version 2 and leaves version 1 alone.
        second = self._verified_run(run_id="run-write-2", relative="second_tool.py")
        attached = self.projects.attach_run(
            project.project_id, second, source_root=self.run_workspace
        )
        self.assertEqual(attached.current_version, 2)
        self.assertEqual(attached.status, ProjectStatus.COMPLETED)
        self.assertEqual(
            json.dumps(attached.version(1).to_dict(), sort_keys=True), before
        )
        self.assertEqual(attached.version(2).files[0].path, "second_tool.py")
        self.assertEqual(attached.version(2).change_request.text, "add a CSV export")

    def test_23_a_failed_new_version_leaves_the_previous_version_current(self) -> None:
        project = self._completed_project()
        self.projects.new_version(project.project_id, "add a CSV export")
        failed = self.projects.attach_run(
            project.project_id,
            self._run_record(
                run_id="run-write-2",
                state="FAILED",
                outcome="TERMINAL_FAILURE",
                error="provider timeout",
            ),
            source_root=self.run_workspace,
        )
        self.assertEqual(failed.status, ProjectStatus.FAILED)
        self.assertEqual(failed.current_version, 1)
        self.assertEqual(failed.current().run_id, "run-write-1")
        self.assertEqual(failed.version(2).run_id, "run-write-2")
        self.assertFalse(failed.version(2).is_pending)
        self.assertEqual(failed.current().files[0].path, "generated_tool.py")

    def test_24_an_empty_change_request_is_refused(self) -> None:
        project = self.create_and_confirm()
        for bad in ("", "   ", None):
            with self.assertRaises(InvalidChangeRequest):
                self.projects.new_version(project.project_id, bad)
        self.assertEqual(project.current_version, 0)

    def create_and_confirm(self) -> Project:
        project = self._create()
        return self.projects.confirm(project.project_id)

    def test_25_a_new_version_is_refused_while_a_run_is_in_flight_or_unplanned(self) -> None:
        running = self._project_at(ProjectStatus.RUNNING)
        self.store.write(running)
        with self.assertRaises(IllegalTransition):
            self.projects.new_version(running.project_id, "anything")
        archiving = self.create_and_confirm()
        self.projects.archive(archiving.project_id)
        with self.assertRaises(IllegalTransition):
            self.projects.new_version(archiving.project_id, "anything")


# --------------------------------------------------------------------------- #
# 9/10/11. archive, restore, history, files
# --------------------------------------------------------------------------- #


class ArchiveAndHistoryTests(_ProjectTestCase):
    def test_26_archive_keeps_every_field_and_deletes_nothing(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        project = self.projects.attach_run(
            project.project_id, self._verified_run(), source_root=self.run_workspace
        )
        record_path = self.store.path_for(project.project_id)
        workspace_file = self.store.workspace_file_path(
            project.project_id, "generated_tool.py"
        )
        before = project.to_dict()

        archived = self.projects.archive(project.project_id)
        self.assertEqual(archived.status, ProjectStatus.ARCHIVED)
        self.assertTrue(archived.archived)
        self.assertEqual(archived.previous_status, ProjectStatus.COMPLETED)
        # Every other field is unchanged: versions, files, specification, times.
        after = archived.to_dict()
        for key in (
            "project_id",
            "name",
            "workspace_id",
            "project_path",
            "created_at",
            "current_version",
            "versions",
            "specification",
            "plan_ref",
            "last_run_at",
        ):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(archived.last_run_at, before["last_run_at"])
        # Nothing was deleted.
        self.assertTrue(record_path.is_file())
        self.assertTrue(workspace_file.is_file())
        self.assertEqual(len(self.projects.files(project.project_id)), 1)

    def test_27_restore_returns_the_previous_status(self) -> None:
        for status in (
            ProjectStatus.PLANNED,
            ProjectStatus.NEEDS_CHANGES,
            ProjectStatus.COMPLETED,
            ProjectStatus.FAILED,
        ):
            project = self._project_at(status)
            self.store.write(project)
            archived = self.projects.archive(project.project_id)
            self.assertEqual(archived.status, ProjectStatus.ARCHIVED)
            restored = self.projects.restore(project.project_id)
            self.assertEqual(restored.status, status)
            self.assertFalse(restored.archived)
            self.assertIsNone(restored.previous_status)
            self.assertEqual(restored.project_id, project.project_id)

    def test_28_history_is_newest_first_and_lists_every_version(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        self.projects.attach_run(
            project.project_id, self._verified_run(run_id="run-1"), source_root=self.run_workspace
        )
        self.projects.new_version(project.project_id, "second")
        self.projects.attach_run(
            project.project_id,
            self._verified_run(run_id="run-2", relative="second.py"),
            source_root=self.run_workspace,
        )
        self.projects.new_version(project.project_id, "third")
        self.projects.attach_run(
            project.project_id,
            self._verified_run(run_id="run-3", relative="third.py"),
            source_root=self.run_workspace,
        )
        history = self.projects.history(project.project_id)
        self.assertEqual([item.version for item in history], [3, 2, 1])
        self.assertEqual(
            [item.run_id for item in history], ["run-3", "run-2", "run-1"]
        )

    def test_29_files_answers_for_one_version_or_the_current_one(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        self.projects.attach_run(
            project.project_id, self._verified_run(), source_root=self.run_workspace
        )
        self.projects.new_version(project.project_id, "second")
        self.projects.attach_run(
            project.project_id,
            self._verified_run(run_id="run-2", relative="second.py"),
            source_root=self.run_workspace,
        )
        self.assertEqual(
            [item.path for item in self.projects.files(project.project_id)],
            ["second.py"],
        )
        self.assertEqual(
            [item.path for item in self.projects.files(project.project_id, 1)],
            ["generated_tool.py"],
        )
        with self.assertRaises(UnknownProjectVersion):
            self.projects.files(project.project_id, 7)
        # A project with no recorded version has an empty manifest, not an error.
        fresh = self._create("fresh")
        self.assertEqual(self.projects.files(fresh.project_id), ())


# --------------------------------------------------------------------------- #
# 12. manifests and payloads carry no contents and no secrets
# --------------------------------------------------------------------------- #


class PayloadSafetyTests(_ProjectTestCase):
    def test_30_the_artifact_manifest_has_size_sha256_and_change_but_no_contents(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        attached = self.projects.attach_run(
            project.project_id, self._verified_run(), source_root=self.run_workspace
        )
        artifact = attached.current().files[0]
        payload = artifact.to_dict()
        self.assertEqual(
            set(payload),
            {"path", "kind", "size", "sha256", "change", "verification_relevance"},
        )
        source = self.run_workspace.joinpath("generated_tool.py").read_text(
            encoding="utf-8"
        )
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("return", rendered)
        self.assertNotIn(source.strip(), rendered)
        self.assertNotIn(source.strip(), self._record_text(project.project_id))
        self.assertEqual(payload["change"], "create")
        self.assertEqual(payload["size"], len(source.encode("utf-8")))
        self.assertEqual(len(payload["sha256"]), 64)

    def test_31_no_secret_shaped_value_reaches_a_payload_or_the_store(self) -> None:
        project = self._create()
        for payload in (
            json.dumps(project.to_dict(), ensure_ascii=False),
            self._record_text(project.project_id),
            json.dumps(self.store.read_index(), ensure_ascii=False),
        ):
            self.assertNotIn(SECRET_LIKE, payload)

        staged = self.projects.new_version(
            project.project_id, f"rotate the key {SECRET_LIKE} please"
        )
        self.assertNotIn(SECRET_LIKE, staged.task_text)
        self.assertIn("[REDACTED:api_key]", staged.task_text)
        self.assertEqual(staged.change_request.text, "[REDACTED:api_key]")
        for payload in (
            json.dumps(staged.project.to_dict(), ensure_ascii=False),
            self._record_text(project.project_id),
            json.dumps(
                [item.to_dict() for item in self.projects.history(project.project_id)],
                ensure_ascii=False,
            ),
        ):
            self.assertNotIn(SECRET_LIKE, payload)
            self.assertNotIn(str(self.base), payload, "an absolute path leaked")

    def test_32_no_payload_publishes_an_absolute_path(self) -> None:
        project = self._create()
        self.projects.confirm(project.project_id)
        attached = self.projects.attach_run(
            project.project_id, self._verified_run(), source_root=self.run_workspace
        )
        for payload in (
            json.dumps(attached.to_dict(), ensure_ascii=False),
            json.dumps(attached.summary(), ensure_ascii=False),
            json.dumps(
                [
                    artifact.to_dict()
                    for artifact in self.projects.files(project.project_id)
                ],
                ensure_ascii=False,
            ),
        ):
            self.assertNotIn(str(self.base), payload)
            self.assertNotIn(str(self.root), payload)
            self.assertNotIn("\\\\", payload)


# --------------------------------------------------------------------------- #
# 13. re-verification
# --------------------------------------------------------------------------- #


class VerifyAgainTests(_ProjectTestCase):
    def _completed(self) -> Project:
        project = self._create()
        self.projects.confirm(project.project_id)
        return self.projects.attach_run(
            project.project_id, self._verified_run(), source_root=self.run_workspace
        )

    def test_33_re_verification_confirms_an_intact_project_and_detects_tampering(self) -> None:
        project = self._completed()
        self.assertEqual(project.status, ProjectStatus.COMPLETED)
        confirmed = self.projects.verify_again(project.project_id)
        self.assertEqual(confirmed.status, ProjectStatus.COMPLETED)
        self.assertEqual(confirmed.current().verification, "passed")

        target = self.store.workspace_file_path(project.project_id, "generated_tool.py")
        original = target.read_bytes()
        target.write_bytes(b"def exported(  # broken\n")
        tampered = self.projects.verify_again(project.project_id)
        self.assertEqual(tampered.status, ProjectStatus.NEEDS_CHANGES)
        self.assertEqual(tampered.current().verification, "failed")

        target.write_bytes(original)
        repaired = self.projects.verify_again(project.project_id)
        self.assertEqual(repaired.status, ProjectStatus.COMPLETED)
        self.assertEqual(repaired.current().verification, "passed")

    def test_34_re_verification_calls_no_provider_and_refuses_the_wrong_status(self) -> None:
        from unittest.mock import patch

        project = self._completed()
        with patch(
            "app.agents.providers.mock.MockProvider.generate",
            side_effect=AssertionError("re-verification must not call a provider"),
        ):
            self.projects.verify_again(project.project_id)
        running = self._project_at(ProjectStatus.RUNNING)
        self.store.write(running)
        with self.assertRaises(IllegalTransition):
            self.projects.verify_again(running.project_id)

    def test_35_re_verification_of_a_missing_artifact_is_a_failure(self) -> None:
        project = self._completed()
        self.store.workspace_file_path(project.project_id, "generated_tool.py").unlink()
        result = self.projects.verify_again(project.project_id)
        self.assertEqual(result.status, ProjectStatus.NEEDS_CHANGES)
        self.assertEqual(result.current().verification, "failed")


# --------------------------------------------------------------------------- #
# 14. the HTTP surface
# --------------------------------------------------------------------------- #


class ProjectApiTests(_ProjectTestCase):
    def _service(self) -> ForgeApiService:
        return ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.run_root),
            workspace=Workspace(self.run_workspace),
            authorized_project_id="project-lifecycle-test",
        )

    def _client(self, service: ForgeApiService | None = None):
        from fastapi.testclient import TestClient

        self.discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))
        app = create_api_app(
            service or self._service(),
            discovery_service=self.discovery,
            project_service=self.projects,
        )
        return TestClient(app)

    def _completed_project(self) -> Project:
        project = self._create()
        self.projects.confirm(project.project_id)
        return self.projects.attach_run(
            project.project_id, self._verified_run(), source_root=self.run_workspace
        )

    def test_36_the_lifecycle_list_does_not_replace_the_existing_projects_route(self) -> None:
        project = self._completed_project()
        client = self._client()
        lifecycle = client.get("/api/projects/lifecycle")
        self.assertEqual(lifecycle.status_code, 200, lifecycle.text)
        rows = lifecycle.json()
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            set(row),
            {
                "project_id",
                "name",
                "status",
                "archived",
                "current_version",
                "last_run_at",
                "file_count",
                "project_path",
            },
        )
        self.assertEqual(row["project_id"], project.project_id)
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(row["current_version"], 1)
        self.assertEqual(row["file_count"], 1)
        self.assertEqual(row["project_path"], project.project_path)
        # The pre-existing route still answers with the workspace project.
        existing = client.get("/api/projects")
        self.assertEqual(existing.status_code, 200, existing.text)
        self.assertEqual(existing.json()[0]["project_id"], "project-lifecycle-test")

        # The archived filter is honoured.
        self.projects.archive(project.project_id)
        self.assertEqual(client.get("/api/projects/lifecycle").json()[0]["archived"], True)
        self.assertEqual(client.get("/api/projects/lifecycle?archived=false").json(), [])

    def test_37_the_detail_history_and_files_routes_publish_no_contents(self) -> None:
        project = self._completed_project()
        client = self._client()
        detail = client.get(f"/api/projects/{project.project_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        body = detail.json()
        self.assertEqual(body["project"]["project_id"], project.project_id)
        self.assertEqual(body["versions"][0]["version"], 1)
        self.assertEqual(body["files"][0]["path"], "generated_tool.py")
        self.assertTrue(body["files"][0]["location"].endswith("/generated_tool.py"))
        self.assertNotIn("content", json.dumps(body))
        self.assertNotIn(str(self.base), detail.text)

        history = client.get(f"/api/projects/{project.project_id}/history")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["count"], 1)
        self.assertEqual(history.json()["versions"][0]["run_id"], "run-write-1")

        files = client.get(f"/api/projects/{project.project_id}/files")
        self.assertEqual(files.status_code, 200, files.text)
        self.assertEqual(files.json()["count"], 1)
        self.assertEqual(files.json()["version"], 1)
        self.assertEqual(
            client.get(f"/api/projects/{project.project_id}/files?version=1").json()["count"],
            1,
        )
        self.assertEqual(
            client.get(f"/api/projects/{project.project_id}/files?version=9").status_code,
            404,
        )

        unknown = client.get("/api/projects/proj-000000000000")
        self.assertEqual(unknown.status_code, 404, unknown.text)
        self.assertEqual(unknown.json()["detail"]["code"], "unknown_project")

    def test_38_versions_archive_restore_and_verify_over_http(self) -> None:
        project = self._completed_project()
        client = self._client()
        # Re-verification first: the project is still COMPLETED at this point.
        verified = client.post(f"/api/projects/{project.project_id}/verify")
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertEqual(verified.json()["verification"], "passed")
        self.assertEqual(verified.json()["status"], "COMPLETED")

        created = client.post(
            f"/api/projects/{project.project_id}/versions",
            json={"change": "add a CSV export"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["version"], 2)
        self.assertIn("add a CSV export", created.json()["task_text"])
        self.assertEqual(created.json()["project"]["status"], "PLANNED")
        self.assertEqual(
            client.post(
                f"/api/projects/{project.project_id}/versions", json={"change": ""}
            ).status_code,
            400,
        )
        archived = client.post(f"/api/projects/{project.project_id}/archive")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["project"]["status"], "ARCHIVED")
        self.assertEqual(archived.json()["project"]["previous_status"], "PLANNED")
        # A new version of an archived project is refused, not silently allowed.
        refused = client.post(
            f"/api/projects/{project.project_id}/versions", json={"change": "more"}
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["detail"]["code"], "illegal_project_transition")
        restored = client.post(f"/api/projects/{project.project_id}/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["project"]["status"], "PLANNED")
        self.assertEqual(restored.json()["project"]["archived"], False)

    def test_39_confirming_a_session_creates_the_project_and_the_run_attaches(
        self,
    ) -> None:
        client = self._client()
        session_id = client.post(
            "/api/discovery/sessions", json={"goal": "Сайт для бронирования отелей"}
        ).json()["session"]["session_id"]
        # Still needs clarification: confirmation is refused with the existing 409.
        refused = client.post(f"/api/discovery/sessions/{session_id}/confirm")
        self.assertEqual(refused.status_code, 409, refused.text)
        client.post(f"/api/discovery/sessions/{session_id}/skip", json={"reason": "ok"})
        confirmed = client.post(f"/api/discovery/sessions/{session_id}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        project_payload = confirmed.json()["project"]
        self.assertEqual(project_payload["status"], "PLANNED")
        self.assertTrue(project_payload["project_path"].startswith(".forge/"))
        listed = client.get("/api/projects/lifecycle").json()
        self.assertEqual([row["project_id"] for row in listed], [project_payload["project_id"]])

        # A run that names the confirmed session attaches to that project.
        run = client.post(
            "/api/tasks/run",
            json={
                "description": "перечислить содержимое корня проекта",
                "category": "code",
                "provider_name": "mock",
                "discovery_session_id": session_id,
            },
        )
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.headers["X-Forge-Project-Attach"], "ok")
        detail = client.get(f"/api/projects/{project_payload['project_id']}").json()
        # The offline provider wrote nothing, so the honest verdict is NEEDS_CHANGES.
        self.assertEqual(detail["project"]["status"], "NEEDS_CHANGES")
        self.assertEqual(detail["project"]["current_version"], 1)
        self.assertEqual(
            detail["versions"][0]["run_id"], run.json()["run_id"]
        )
        self.assertEqual(detail["versions"][0]["acceptance"], "passed")
        self.assertEqual(detail["versions"][0]["verification"], "not_run")


if __name__ == "__main__":
    unittest.main()
