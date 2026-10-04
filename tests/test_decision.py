"""Tests for the Decision Layer / Run Control contract v0.1."""

from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import MagicMock

from app.artifacts import ChangeSet
from app.decision import (
    DECISION_TO_ACTION_COMPATIBILITY,
    Decision,
    DecisionAction,
    DecisionProvider,
    DecisionRequest,
    DecisionType,
    DecisionValidationReport,
    DeterministicDecisionProvider,
    sanitize_decision_metadata,
    validate_decision,
)
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunResult,
    EngineeringRunStatus,
)
from app.orchestrator.models import Event, EventType, Run, Task
from app.orchestrator.revision import RevisionAttemptResult, RevisionResult, RevisionStatus
from app.projects.state import ProjectState, ProjectStateStatus, derive_project_state
from app.snapshots import ProjectSnapshot
from app.tools.acceptance import AcceptanceCriterion, AcceptanceResult, AcceptanceStatus
from app.tools.verification import VerificationResult, VerificationStatus


class TestDecisionModels(unittest.TestCase):
    """Unit tests for Decision, DecisionRequest, DecisionType, and DecisionAction."""

    def test_decision_creation_and_immutability(self):
        """Decision must be frozen/immutable and require core fields."""
        dec = Decision(
            decision_id="dec-1",
            run_id="run-1",
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="execution_required",
            confidence=0.95,
            attempt_number=1,
            references={"step": "first"},
            metadata={"source": "test"},
        )
        self.assertEqual(dec.decision_id, "dec-1")
        self.assertEqual(dec.run_id, "run-1")
        self.assertEqual(dec.decision_type, DecisionType.CONTINUE)
        self.assertEqual(dec.action, DecisionAction.EXECUTE)
        self.assertEqual(dec.reason_code, "execution_required")
        self.assertEqual(dec.rationale, "execution_required")
        self.assertEqual(dec.confidence, 0.95)
        self.assertEqual(dec.attempt_number, 1)

        with self.assertRaises(FrozenInstanceError):
            dec.decision_id = "dec-2"  # type: ignore

    def test_decision_type_enum(self):
        """DecisionType enum must contain all canonical members."""
        expected = {
            "CONTINUE",
            "REVISE",
            "VERIFY",
            "REQUEST_APPROVAL",
            "WAIT",
            "FAIL",
            "COMPLETE",
        }
        actual = {m.name for m in DecisionType}
        self.assertTrue(expected.issubset(actual))
        # Verify string lookup
        for name in expected:
            self.assertEqual(DecisionType(name).name, name)

    def test_decision_action_enum(self):
        """DecisionAction enum must contain all canonical operations."""
        expected = {
            "EXECUTE",
            "RUN_VERIFICATION",
            "REQUEST_REVISION",
            "REQUEST_USER_APPROVAL",
            "WAIT_FOR_APPROVAL",
            "COMPLETE_RUN",
            "FAIL_RUN",
        }
        actual = {m.name for m in DecisionAction}
        self.assertTrue(expected.issubset(actual))
        for name in expected:
            self.assertEqual(DecisionAction(name).name, name)

    def test_run_and_attempt_binding(self):
        """Decision and DecisionRequest must enforce non-empty run_id and non-negative attempt_number."""
        with self.assertRaises(ValueError):
            Decision(
                decision_id="d1",
                run_id="",
                decision_type=DecisionType.CONTINUE,
                action=DecisionAction.EXECUTE,
                reason_code="test",
            )
        with self.assertRaises(ValueError):
            DecisionRequest(
                run_id="",
            )
        with self.assertRaises(ValueError):
            DecisionRequest(
                run_id="run-1",
                attempt_number=-1,
            )

    def test_metadata_sanitization(self):
        """Forbidden keys (stdout, stderr, secret, token, etc.) and oversized values must be purged."""
        raw_meta = {
            "safe_key": "safe_val",
            "stdout": "catastrophic raw stdout",
            "my_secret_token": "secret123",
            "api_key": "key-xyz",
            "password": "pass",
            "large_string": "x" * 5000,
        }
        sanitized = sanitize_decision_metadata(raw_meta)
        self.assertIn("safe_key", sanitized)
        self.assertNotIn("stdout", sanitized)
        self.assertNotIn("my_secret_token", sanitized)
        self.assertNotIn("api_key", sanitized)
        self.assertNotIn("password", sanitized)
        self.assertNotIn("large_string", sanitized)

    def test_to_dict_serialization(self):
        """to_dict must produce json-serializable dictionary representation."""
        dec = Decision(
            decision_id="dec-10",
            run_id="run-10",
            decision_type=DecisionType.COMPLETE,
            action=DecisionAction.COMPLETE_RUN,
            reason_code="passed",
            attempt_number=2,
            references={"acceptance_status": "pass"},
            metadata={"env": "prod"},
        )
        d = dec.to_dict()
        self.assertEqual(d["decision_id"], "dec-10")
        self.assertEqual(d["run_id"], "run-10")
        self.assertEqual(d["decision_type"], "COMPLETE")
        self.assertEqual(d["action"], "COMPLETE_RUN")
        self.assertEqual(d["reason_code"], "passed")
        self.assertEqual(d["attempt_number"], 2)


class TestDeterministicDecisionRules(unittest.TestCase):
    """Unit tests for DeterministicDecisionProvider precedence rules A through H."""

    def setUp(self):
        self.provider = DeterministicDecisionProvider()

    def test_rule_a_security_or_permission_denied(self):
        """Rule A: security/permission/policy failure takes top precedence -> FAIL."""
        req = DecisionRequest(
            run_id="run-1",
            attempt_number=1,
            blocking_conditions=("security_denied",),
        )
        decision = self.provider.decide(req)
        self.assertEqual(decision.decision_type, DecisionType.FAIL)
        self.assertEqual(decision.action, DecisionAction.FAIL_RUN)
        self.assertEqual(decision.reason_code, "security_denied")

        req_policy = DecisionRequest(
            run_id="run-1",
            attempt_number=1,
            blocking_conditions=("policy_denied",),
        )
        dec_policy = self.provider.decide(req_policy)
        self.assertEqual(dec_policy.decision_type, DecisionType.FAIL)
        self.assertEqual(dec_policy.action, DecisionAction.FAIL_RUN)
        self.assertEqual(dec_policy.reason_code, "policy_denied")

    def test_rule_b_revision_limit_reached(self):
        """Rule B: revision limit reached takes precedence over lower rules -> FAIL."""
        req = DecisionRequest(
            run_id="run-1",
            attempt_number=3,
            blocking_conditions=("revision_limit_reached",),
            acceptance_status="fail",
        )
        decision = self.provider.decide(req)
        self.assertEqual(decision.decision_type, DecisionType.FAIL)
        self.assertEqual(decision.action, DecisionAction.FAIL_RUN)
        self.assertEqual(decision.reason_code, "revision_limit_reached")

    def test_rule_c_approval_pending(self):
        """Rule C: approval pending/required takes precedence over acceptance/verification -> REQUEST_APPROVAL."""
        req_pending = DecisionRequest(
            run_id="run-1",
            attempt_number=1,
            blocking_conditions=("approval_pending",),
        )
        dec_pending = self.provider.decide(req_pending)
        self.assertEqual(dec_pending.decision_type, DecisionType.REQUEST_APPROVAL)
        self.assertEqual(dec_pending.action, DecisionAction.WAIT_FOR_APPROVAL)

        req_required = DecisionRequest(
            run_id="run-1",
            attempt_number=1,
            blocking_conditions=("approval_required",),
        )
        dec_required = self.provider.decide(req_required)
        self.assertEqual(dec_required.decision_type, DecisionType.REQUEST_APPROVAL)
        self.assertEqual(dec_required.action, DecisionAction.REQUEST_USER_APPROVAL)

    def test_rule_d_acceptance_passed(self):
        """Rule D: acceptance passed -> COMPLETE / COMPLETE_RUN."""
        state = ProjectState(
            run_id="run-1",
            attempt_number=1,
            status=ProjectStateStatus.ACCEPTED,
            acceptance_status="pass",
        )
        req = DecisionRequest(
            run_id="run-1",
            attempt_number=1,
            current_project_state=state,
            acceptance_status="pass",
        )
        decision = self.provider.decide(req)
        self.assertEqual(decision.decision_type, DecisionType.COMPLETE)
        self.assertEqual(decision.action, DecisionAction.COMPLETE_RUN)
        self.assertEqual(decision.reason_code, "acceptance_passed")

    def test_rule_e_verification_failed(self):
        """Rule E: verification failed -> REVISE / REQUEST_REVISION."""
        state = ProjectState(
            run_id="run-1",
            attempt_number=1,
            status=ProjectStateStatus.FAILED,
        )
        req = DecisionRequest(
            run_id="run-1",
            attempt_number=1,
            current_project_state=state,
            verification_status_summary="fail",
        )
        decision = self.provider.decide(req)
        self.assertEqual(decision.decision_type, DecisionType.REVISE)
        self.assertEqual(decision.action, DecisionAction.REQUEST_REVISION)
        self.assertEqual(decision.reason_code, "verification_failed")

    def test_rule_f_verification_required(self):
        """Rule F: project state is CHANGED -> VERIFY / RUN_VERIFICATION."""
        state = ProjectState(
            run_id="run-1",
            attempt_number=1,
            status=ProjectStateStatus.CHANGED,
        )
        req = DecisionRequest(
            run_id="run-1",
            attempt_number=1,
            current_project_state=state,
        )
        decision = self.provider.decide(req)
        self.assertEqual(decision.decision_type, DecisionType.VERIFY)
        self.assertEqual(decision.action, DecisionAction.RUN_VERIFICATION)
        self.assertEqual(decision.reason_code, "verification_required")

    def test_rule_g_execution_required(self):
        """Rule G: project state is INITIAL -> CONTINUE / EXECUTE."""
        state = ProjectState(
            run_id="run-1",
            attempt_number=0,
            status=ProjectStateStatus.INITIAL,
        )
        req = DecisionRequest(
            run_id="run-1",
            attempt_number=0,
            current_project_state=state,
        )
        decision = self.provider.decide(req)
        self.assertEqual(decision.decision_type, DecisionType.CONTINUE)
        self.assertEqual(decision.action, DecisionAction.EXECUTE)
        self.assertEqual(decision.reason_code, "execution_required")

    def test_rule_h_otherwise_wait(self):
        """Rule H: unrecognized or idle state -> WAIT / WAIT_FOR_APPROVAL."""
        state = ProjectState(
            run_id="run-1",
            attempt_number=1,
            status=ProjectStateStatus.VERIFIED,
        )
        req = DecisionRequest(
            run_id="run-1",
            attempt_number=1,
            current_project_state=state,
        )
        decision = self.provider.decide(req)
        self.assertEqual(decision.decision_type, DecisionType.WAIT)
        self.assertEqual(decision.action, DecisionAction.WAIT_FOR_APPROVAL)

    def test_rule_determinism(self):
        """Identical inputs produce identical decisions (same type, action, reason)."""
        state = ProjectState(
            run_id="run-det",
            attempt_number=1,
            status=ProjectStateStatus.CHANGED,
        )
        req1 = DecisionRequest(
            run_id="run-det",
            attempt_number=1,
            current_project_state=state,
        )
        req2 = DecisionRequest(
            run_id="run-det",
            attempt_number=1,
            current_project_state=state,
        )
        d1 = self.provider.decide(req1)
        d2 = self.provider.decide(req2)
        self.assertEqual(d1.decision_type, d2.decision_type)
        self.assertEqual(d1.action, d2.action)
        self.assertEqual(d1.reason_code, d2.reason_code)
        self.assertEqual(d1.attempt_number, d2.attempt_number)


class TestDecisionValidator(unittest.TestCase):
    """Unit tests for validate_decision."""

    def test_valid_decision_passes(self):
        """Valid decision with matching run_id, compatible action, and capabilities passes."""
        req = DecisionRequest(
            run_id="run-val",
            attempt_number=1,
            available_actions=(DecisionAction.EXECUTE, DecisionAction.FAIL_RUN),
        )
        dec = Decision(
            decision_id="dec-1",
            run_id="run-val",
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="ok",
            attempt_number=1,
        )
        report = validate_decision(dec, req)
        self.assertTrue(report.valid)
        self.assertEqual(report.errors, ())
        self.assertTrue(bool(report))

    def test_validator_rejects_wrong_run_id(self):
        """Validator rejects decision when decision.run_id != request.run_id."""
        req = DecisionRequest(run_id="run-A")
        dec = Decision(
            decision_id="dec-1",
            run_id="run-B",
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="ok",
        )
        report = validate_decision(dec, req)
        self.assertFalse(report.valid)
        self.assertIn("run_id_mismatch", report.errors)

    def test_validator_rejects_incompatible_action(self):
        """Validator rejects incompatible decision_type and action pairings."""
        req = DecisionRequest(run_id="run-1")
        # COMPLETE cannot have EXECUTE action
        dec = Decision(
            decision_id="dec-1",
            run_id="run-1",
            decision_type=DecisionType.COMPLETE,
            action=DecisionAction.EXECUTE,
            reason_code="invalid",
        )
        report = validate_decision(dec, req)
        self.assertFalse(report.valid)
        self.assertTrue(any(e.startswith("incompatible_action:") for e in report.errors))

    def test_validator_rejects_unavailable_capability(self):
        """Validator rejects actions that are not present in available_actions."""
        req = DecisionRequest(
            run_id="run-1",
            available_actions=(DecisionAction.WAIT_FOR_APPROVAL,),
        )
        dec = Decision(
            decision_id="dec-1",
            run_id="run-1",
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="unsupported",
        )
        report = validate_decision(dec, req)
        self.assertFalse(report.valid)
        self.assertIn("unavailable_action:EXECUTE", report.errors)

    def test_validator_rejects_cross_run_reference(self):
        """Validator rejects decision with references pointing to a different run_id."""
        req = DecisionRequest(run_id="run-original")
        dec = Decision(
            decision_id="dec-1",
            run_id="run-original",
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="ok",
            references={"run_id": "run-foreign"},
        )
        report = validate_decision(dec, req)
        self.assertFalse(report.valid)
        self.assertIn("cross_run_reference", report.errors)

    def test_validator_rejects_attempt_number_mismatch(self):
        """Validator rejects attempt number inconsistency."""
        req = DecisionRequest(run_id="run-1", attempt_number=1)
        dec = Decision(
            decision_id="dec-1",
            run_id="run-1",
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="ok",
            attempt_number=2,
        )
        report = validate_decision(dec, req)
        self.assertFalse(report.valid)
        self.assertIn("attempt_number_mismatch", report.errors)


class TestEngineeringRunDecisionIntegration(unittest.TestCase):
    """Integration tests verifying Decision Layer operation in EngineeringRunExecutor."""

    def test_engineering_run_executor_populates_decisions(self):
        """EngineeringRunExecutor automatically evaluates and stores decisions."""
        mock_rev_executor = MagicMock()
        run = Run(task=Task(id="task-1", description="test task"))
        crit = AcceptanceCriterion(criterion_id="c1", description="desc")
        acc = AcceptanceResult(status=AcceptanceStatus.PASS, results=(), code="passed")
        attempt = RevisionAttemptResult(
            attempt_number=1,
            changesets=(),
            snapshots=(),
            verification_results=(),
            acceptance_result=acc,
        )
        mock_rev_executor.execute.return_value = RevisionResult(
            status=RevisionStatus.COMPLETED,
            attempt_number=1,
            acceptance_result=acc,
            run=run,
            attempts=(attempt,),
        )

        executor = EngineeringRunExecutor(revision_executor=mock_rev_executor)
        req = EngineeringRunRequest(task=run.task, acceptance_criteria=(crit,))
        result = executor.execute(req)

        self.assertTrue(len(result.decisions) > 0)
        self.assertIsNotNone(result.final_decision)
        self.assertEqual(result.final_decision.decision_type, DecisionType.COMPLETE)
        self.assertEqual(result.final_decision.action, DecisionAction.COMPLETE_RUN)

    def test_trace_contains_decision_requested_and_made(self):
        """Trace contains DECISION_REQUESTED and DECISION_MADE events with matching decision_id."""
        mock_rev_executor = MagicMock()
        run = Run(task=Task(id="task-1", description="test task"))
        crit = AcceptanceCriterion(criterion_id="c1", description="desc")
        acc = AcceptanceResult(status=AcceptanceStatus.PASS, results=(), code="passed")
        attempt = RevisionAttemptResult(
            attempt_number=1,
            changesets=(),
            snapshots=(),
            verification_results=(),
            acceptance_result=acc,
        )
        mock_rev_executor.execute.return_value = RevisionResult(
            status=RevisionStatus.COMPLETED,
            attempt_number=1,
            acceptance_result=acc,
            run=run,
            attempts=(attempt,),
        )

        executor = EngineeringRunExecutor(revision_executor=mock_rev_executor)
        req = EngineeringRunRequest(task=run.task, acceptance_criteria=(crit,))
        result = executor.execute(req)

        trace = result.trace
        self.assertIsNotNone(trace)
        requested_events = [e for e in trace.events if e.event_type == EventType.DECISION_REQUESTED]
        made_events = [e for e in trace.events if e.event_type == EventType.DECISION_MADE]

        self.assertTrue(len(requested_events) > 0)
        self.assertTrue(len(made_events) > 0)
        self.assertIsNotNone(requested_events[0].decision_id)
        self.assertIsNotNone(made_events[0].decision_id)
        self.assertEqual(made_events[0].decision_id, result.decisions[0].decision_id)

    def test_trace_contains_decision_rejected_when_validator_fails(self):
        """When provider produces an invalid decision, DECISION_REJECTED is recorded in trace."""
        class InvalidDecisionProvider:
            def decide(self, request: DecisionRequest) -> Decision:
                # Return decision with wrong run_id so validator fails
                return Decision(
                    decision_id="dec-bad",
                    run_id="wrong-run-id",
                    decision_type=DecisionType.CONTINUE,
                    action=DecisionAction.EXECUTE,
                    reason_code="invalid",
                    attempt_number=request.attempt_number,
                )

        mock_rev_executor = MagicMock()
        run = Run(task=Task(id="task-1", description="test task"))
        crit = AcceptanceCriterion(criterion_id="c1", description="desc")
        attempt = RevisionAttemptResult(
            attempt_number=1,
            changesets=(),
            snapshots=(),
            verification_results=(),
            acceptance_result=None,
        )
        mock_rev_executor.execute.return_value = RevisionResult(
            status=RevisionStatus.COMPLETED,
            attempt_number=1,
            acceptance_result=None,
            run=run,
            attempts=(attempt,),
        )

        executor = EngineeringRunExecutor(
            revision_executor=mock_rev_executor,
            decision_provider=InvalidDecisionProvider(),
        )
        req = EngineeringRunRequest(task=run.task, acceptance_criteria=(crit,))
        result = executor.execute(req)

        # Rejected decision must NOT be accepted in result.decisions
        self.assertEqual(len(result.decisions), 0)

        # Trace must contain DECISION_REJECTED event
        trace = result.trace
        self.assertIsNotNone(trace)
        rejected_events = [e for e in trace.events if e.event_type == EventType.DECISION_REJECTED]
        self.assertTrue(len(rejected_events) > 0)
        self.assertEqual(rejected_events[0].decision_id, "dec-bad")
        self.assertIn("errors", rejected_events[0].metadata)


if __name__ == "__main__":
    unittest.main()
