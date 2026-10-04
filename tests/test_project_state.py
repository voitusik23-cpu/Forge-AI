"""Tests for ProjectState contract and state derivation."""

from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import MagicMock

from app.artifacts import ChangeSet
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunResult,
    EngineeringRunStatus,
)
from app.orchestrator.models import Event, EventType, Run, Task
from app.orchestrator.revision import RevisionAttemptResult, RevisionResult, RevisionStatus
from app.projects.state import (
    ProjectState,
    ProjectStateStatus,
    derive_project_state,
)
from app.snapshots import ProjectSnapshot
from app.tools.acceptance import AcceptanceCriterion, AcceptanceResult, AcceptanceStatus
from app.tools.verification import VerificationResult, VerificationStatus


class TestProjectStateContract(unittest.TestCase):
    """Unit tests for the ProjectState dataclass and ProjectStateStatus enum."""

    def test_project_state_status_enum_values(self):
        """All required lifecycle statuses must be distinct and string-compatible."""
        expected = {"INITIAL", "IN_PROGRESS", "CHANGED", "VERIFIED", "ACCEPTED", "FAILED"}
        actual = {status.value for status in ProjectStateStatus}
        self.assertEqual(actual, expected)
        for name in expected:
            self.assertEqual(ProjectStateStatus(name).value, name)

    def test_project_state_creation_and_defaults(self):
        """ProjectState can be instantiated with minimal required fields."""
        state = ProjectState(
            run_id="run-1",
            attempt_number=1,
            status=ProjectStateStatus.INITIAL,
        )
        self.assertEqual(state.run_id, "run-1")
        self.assertEqual(state.attempt_number, 1)
        self.assertEqual(state.status, ProjectStateStatus.INITIAL)
        self.assertEqual(state.project_id, "")
        self.assertEqual(state.task_id, "")
        self.assertEqual(state.snapshot_ids, ())
        self.assertEqual(state.changeset_ids, ())
        self.assertEqual(state.execution_result_ids, ())
        self.assertEqual(state.verification_result_ids, ())
        self.assertIsNone(state.acceptance_status)
        self.assertEqual(state.metadata, {})
        self.assertIsNone(state.snapshot_id)
        self.assertIsNone(state.changeset_id)

    def test_project_state_immutability(self):
        """ProjectState is frozen and cannot be mutated after creation."""
        state = ProjectState(
            run_id="run-1",
            attempt_number=1,
            status=ProjectStateStatus.INITIAL,
        )
        with self.assertRaises(FrozenInstanceError):
            state.status = ProjectStateStatus.IN_PROGRESS  # type: ignore

    def test_project_state_tuple_coercion_and_accessors(self):
        """Lists passed to sequence fields are coerced to immutable tuples."""
        state = ProjectState(
            run_id="run-1",
            attempt_number=1,
            status=ProjectStateStatus.CHANGED,
            snapshot_ids=["snap-1", "snap-2"],  # type: ignore
            changeset_ids=["cs-1"],  # type: ignore
            execution_result_ids=["req-1"],  # type: ignore
            verification_result_ids=["ver-1"],  # type: ignore
        )
        self.assertEqual(state.snapshot_ids, ("snap-1", "snap-2"))
        self.assertEqual(state.changeset_ids, ("cs-1",))
        self.assertEqual(state.execution_result_ids, ("req-1",))
        self.assertEqual(state.verification_result_ids, ("ver-1",))
        self.assertEqual(state.snapshot_id, "snap-2")
        self.assertEqual(state.changeset_id, "cs-1")

    def test_project_state_to_dict_serialization(self):
        """to_dict returns a clean dictionary serialization."""
        state = ProjectState(
            run_id="run-abc",
            attempt_number=2,
            status=ProjectStateStatus.VERIFIED,
            project_id="proj-1",
            task_id="task-1",
            snapshot_ids=("snap-1",),
            changeset_ids=("cs-1",),
            execution_result_ids=("req-1",),
            verification_result_ids=("ver-1",),
            acceptance_status="pass",
            metadata={"source": "test"},
        )
        d = state.to_dict()
        self.assertEqual(
            d,
            {
                "run_id": "run-abc",
                "attempt_number": 2,
                "status": "VERIFIED",
                "project_id": "proj-1",
                "task_id": "task-1",
                "snapshot_ids": ["snap-1"],
                "changeset_ids": ["cs-1"],
                "execution_result_ids": ["req-1"],
                "verification_result_ids": ["ver-1"],
                "acceptance_status": "pass",
                "metadata": {"source": "test"},
            },
        )


class TestProjectStateDerivation(unittest.TestCase):
    """Unit tests for derive_project_state logic."""

    def test_derive_initial_state(self):
        """Empty artifacts and is_running=False results in INITIAL state."""
        state = derive_project_state(run_id="run-1", attempt_number=0)
        self.assertEqual(state.status, ProjectStateStatus.INITIAL)
        self.assertEqual(state.snapshot_ids, ())
        self.assertEqual(state.changeset_ids, ())

    def test_derive_in_progress_state(self):
        """is_running=True with no changes results in IN_PROGRESS state."""
        state = derive_project_state(run_id="run-1", attempt_number=1, is_running=True)
        self.assertEqual(state.status, ProjectStateStatus.IN_PROGRESS)

    def test_derive_changed_state_from_changesets(self):
        """Presence of changesets results in CHANGED state when unverified."""
        cs = ChangeSet(
            changeset_id="cs-10",
            run_id="run-1",
        )
        state = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            changesets=[cs],
        )
        self.assertEqual(state.status, ProjectStateStatus.CHANGED)
        self.assertEqual(state.changeset_ids, ("cs-10",))
        self.assertEqual(state.changeset_id, "cs-10")

    def test_derive_changed_state_from_execution_results(self):
        """Presence of execution_results results in CHANGED state when unverified."""
        res = ExecutionResult(
            request_id="req-1",
            status=ExecutionStatus.SUCCESS,
            outcome_status=ExecutionOutcomeStatus.EXECUTION_SUCCESS,
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_seconds=10.0,
        )
        state = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            execution_results=[res],
        )
        self.assertEqual(state.status, ProjectStateStatus.CHANGED)
        self.assertEqual(state.execution_result_ids, ("req-1",))

    def test_derive_verified_state_when_all_verifications_pass(self):
        """Passing verifications without acceptance evaluate to VERIFIED."""
        v1 = VerificationResult(
            verification_id="ver-1",
            status=VerificationStatus.PASS,
            code="test_pass",
        )
        v2 = VerificationResult(
            verification_id="ver-2",
            status=VerificationStatus.PASS,
            code="lint_pass",
        )
        state = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            verification_results=[v1, v2],
        )
        self.assertEqual(state.status, ProjectStateStatus.VERIFIED)
        self.assertEqual(state.verification_result_ids, ("ver-1", "ver-2"))

    def test_derive_failed_state_when_verification_fails(self):
        """Any failing verification results in FAILED state."""
        v1 = VerificationResult(
            verification_id="ver-1",
            status=VerificationStatus.PASS,
            code="test_pass",
        )
        v2 = VerificationResult(
            verification_id="ver-2",
            status=VerificationStatus.FAIL,
            code="test_fail",
        )
        state = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            verification_results=[v1, v2],
        )
        self.assertEqual(state.status, ProjectStateStatus.FAILED)
        self.assertEqual(state.verification_result_ids, ("ver-1", "ver-2"))

    def test_derive_accepted_state_when_acceptance_passes(self):
        """Passing acceptance result yields ACCEPTED state."""
        acc = AcceptanceResult(
            status=AcceptanceStatus.PASS,
            results=(),
            code="acceptance_passed",
        )
        state = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            acceptance_result=acc,
        )
        self.assertEqual(state.status, ProjectStateStatus.ACCEPTED)
        self.assertEqual(state.acceptance_status, "pass")

    def test_derive_failed_state_when_acceptance_rejected(self):
        """Rejected acceptance result yields FAILED state even if verifications passed."""
        v1 = VerificationResult(
            verification_id="ver-1",
            status=VerificationStatus.PASS,
            code="passed",
        )
        acc = AcceptanceResult(
            status=AcceptanceStatus.FAIL,
            results=(),
            code="acceptance_failed",
        )
        state = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            verification_results=[v1],
            acceptance_result=acc,
        )
        self.assertEqual(state.status, ProjectStateStatus.FAILED)
        self.assertEqual(state.acceptance_status, "fail")

    def test_metadata_sanitization_removes_stdout_stderr_raw_output(self):
        """derive_project_state purges stdout, stderr, and raw_output from metadata."""
        meta = {
            "stdout": "secret stdout output",
            "stderr": "secret stderr output",
            "raw_output": "raw process data",
            "safe_key": "safe_value",
            "attempt_kind": "fix",
        }
        state = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            metadata=meta,
        )
        self.assertNotIn("stdout", state.metadata)
        self.assertNotIn("stderr", state.metadata)
        self.assertNotIn("raw_output", state.metadata)
        self.assertEqual(state.metadata["safe_key"], "safe_value")
        self.assertEqual(state.metadata["attempt_kind"], "fix")

    def test_deterministic_derivation(self):
        """Identical inputs produce identical ProjectState outputs."""
        state1 = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            project_id="p1",
            task_id="t1",
            snapshots=["s1"],
            changesets=["c1"],
            execution_results=["e1"],
            verification_results=["v1"],
            metadata={"key": "val"},
        )
        state2 = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            project_id="p1",
            task_id="t1",
            snapshots=["s1"],
            changesets=["c1"],
            execution_results=["e1"],
            verification_results=["v1"],
            metadata={"key": "val"},
        )
        self.assertEqual(state1, state2)
        self.assertEqual(state1.to_dict(), state2.to_dict())


class TestEngineeringRunProjectStateIntegration(unittest.TestCase):
    """Integration tests verifying ProjectState across EngineeringRun attempts."""

    def test_multi_attempt_progression(self):
        """Attempt 1 fails and Attempt 2 succeeds, yielding distinct ProjectStates."""
        # Attempt 1: Changed, failed verification
        cs1 = ChangeSet(changeset_id="cs-1", run_id="run-1")
        v1 = VerificationResult(
            verification_id="v-1",
            status=VerificationStatus.FAIL,
            code="test_failed",
        )
        state_attempt1 = derive_project_state(
            run_id="run-1",
            attempt_number=1,
            task_id="t-1",
            changesets=[cs1],
            verification_results=[v1],
        )
        self.assertEqual(state_attempt1.status, ProjectStateStatus.FAILED)
        self.assertEqual(state_attempt1.attempt_number, 1)

        # Attempt 2: Changed, passed verification, accepted
        cs2 = ChangeSet(changeset_id="cs-2", run_id="run-1")
        v2 = VerificationResult(
            verification_id="v-2",
            status=VerificationStatus.PASS,
            code="test_passed",
        )
        acc = AcceptanceResult(
            status=AcceptanceStatus.PASS,
            results=(),
            code="passed",
        )
        state_attempt2 = derive_project_state(
            run_id="run-1",
            attempt_number=2,
            task_id="t-1",
            changesets=[cs2],
            verification_results=[v2],
            acceptance_result=acc,
        )
        self.assertEqual(state_attempt2.status, ProjectStateStatus.ACCEPTED)
        self.assertEqual(state_attempt2.attempt_number, 2)

        # Construct EngineeringRunResult with multiple attempt states
        res = EngineeringRunResult(
            run_id="run-1",
            task_id="t-1",
            final_status=EngineeringRunStatus.SUCCESS,
            final_acceptance=acc,
            attempts=(
                RevisionAttemptResult(1, (), (cs1,), (v1,), None),
                RevisionAttemptResult(2, (), (cs2,), (v2,), acc),
            ),
            changesets=(cs1, cs2),
            snapshots=(),
            verification_results=(v1, v2),
            acceptance_results=(acc,),
            revision_result=RevisionResult(
                status=RevisionStatus.COMPLETED,
                attempt_number=2,
                acceptance_result=acc,
                run=Run(id="run-1", task=Task(id="t-1", description="desc")),
                attempts=(
                    RevisionAttemptResult(1, (), (cs1,), (v1,), None),
                    RevisionAttemptResult(2, (), (cs2,), (v2,), acc),
                ),
            ),
            run=Run(id="run-1", task=Task(id="t-1", description="desc")),
            project_states=(state_attempt1, state_attempt2),
        )
        self.assertEqual(len(res.project_states), 2)
        self.assertEqual(res.project_states[0].status, ProjectStateStatus.FAILED)
        self.assertEqual(res.project_states[1].status, ProjectStateStatus.ACCEPTED)
        self.assertIsNotNone(res.final_project_state)
        self.assertEqual(res.final_project_state.status, ProjectStateStatus.ACCEPTED)
        self.assertEqual(res.final_project_state.attempt_number, 2)

    def test_engineering_run_executor_populates_project_states(self):
        """EngineeringRunExecutor automatically populates project_states in EngineeringRunResult."""
        mock_revision_executor = MagicMock()
        cs = ChangeSet(changeset_id="cs-99", run_id="run-exec")
        snap = ProjectSnapshot(snapshot_id="snap-99", run_id="run-exec")
        v_res = VerificationResult(
            verification_id="ver-99",
            status=VerificationStatus.PASS,
            code="test_pass",
        )
        crit = AcceptanceCriterion(
            criterion_id="ac-99",
            description="Test passes",
        )
        acc = AcceptanceResult(
            status=AcceptanceStatus.PASS,
            results=(),
            code="acceptance_passed",
        )
        attempt = RevisionAttemptResult(
            attempt_number=1,
            snapshots=(snap,),
            changesets=(cs,),
            verification_results=(v_res,),
            acceptance_result=acc,
        )
        mock_run = Run(id="run-exec", task=Task(id="task-99", description="solve issue"))
        mock_run.events.append(Event(run_id="run-exec", type=EventType.RUN_STARTED))
        mock_revision_executor.execute.return_value = RevisionResult(
            status=RevisionStatus.COMPLETED,
            attempt_number=1,
            acceptance_result=acc,
            run=mock_run,
            attempts=(attempt,),
        )

        executor = EngineeringRunExecutor(revision_executor=mock_revision_executor)
        req = EngineeringRunRequest(
            task=Task(id="task-99", description="solve issue"),
            acceptance_criteria=[crit],
        )

        res = executor.execute(req)
        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        self.assertEqual(len(res.project_states), 1)
        st = res.final_project_state
        self.assertIsNotNone(st)
        self.assertEqual(st.status, ProjectStateStatus.ACCEPTED)
        self.assertEqual(st.attempt_number, 1)
        self.assertEqual(st.changeset_ids, ("cs-99",))
        self.assertEqual(st.snapshot_ids, ("snap-99",))
        self.assertEqual(st.verification_result_ids, ("ver-99",))
        self.assertEqual(st.acceptance_status, "pass")


if __name__ == "__main__":
    unittest.main()
