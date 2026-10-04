"""Tests for Decision Context Envelope v0.1 (Stage 10)."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import socket
from unittest.mock import patch

from app.context.models import (
    ContextFreshness,
    ContextItem,
    ContextSensitivity,
    ContextSourceType,
    ContextTrustLevel,
    DecisionContextEnvelope,
    TraceSummary,
)
from app.context.validation import (
    MAX_CONTEXT_ITEMS,
    MAX_METADATA_ITEMS,
    MAX_STRING_LENGTH,
    sanitize_context_metadata,
    validate_decision_context,
)
from app.context.assembler import (
    ContextAssemblyError,
    ContextBudgetExceededError,
    DecisionContextAssembler,
)
from app.decision.models import (
    Decision,
    DecisionAction,
    DecisionRequest,
    DecisionType,
)
from app.decision.provider import DeterministicDecisionProvider
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunStatus,
)
from app.orchestrator.models import EventType, Task
from app.orchestrator.revision import (
    RevisionAttemptResult,
    RevisionLoopExecutor,
    RevisionResult,
    RevisionStatus,
)
from app.orchestrator.trace import RunEvent, RunTrace
from app.projects.state import ProjectState, ProjectStateStatus
from app.snapshots import ProjectSnapshot
from app.tasks.specification import Requirement, TaskSpecification
from app.tools.acceptance import (
    AcceptanceCriterion,
    AcceptanceResult,
    AcceptanceStatus,
)
from app.tools.changesets import ChangeSet
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
)


class TestContextItem(unittest.TestCase):
    """Verify ContextItem model, enums, immutability, and backwards compatibility."""

    def test_context_item_creation_and_fields(self) -> None:
        item = ContextItem(
            item_id="req-1",
            item_type="requirement",
            source_type=ContextSourceType.REQUIREMENT,
            value="Must be performant",
            trust_level=ContextTrustLevel.VERIFIED,
            freshness=ContextFreshness.CURRENT,
            sensitivity=ContextSensitivity.INTERNAL,
            source_id="task-123",
            metadata={"priority": "high"},
        )
        self.assertEqual(item.item_id, "req-1")
        self.assertEqual(item.item_type, "requirement")
        self.assertEqual(item.source_type, ContextSourceType.REQUIREMENT)
        self.assertEqual(item.value, "Must be performant")
        self.assertEqual(item.trust_level, ContextTrustLevel.VERIFIED)
        self.assertEqual(item.freshness, ContextFreshness.CURRENT)
        self.assertEqual(item.sensitivity, ContextSensitivity.INTERNAL)
        self.assertEqual(item.source_id, "task-123")
        self.assertEqual(item.metadata["priority"], "high")

    def test_context_item_immutability(self) -> None:
        item = ContextItem(
            item_id="item-1",
            item_type="task",
            source_type=ContextSourceType.USER_TASK,
            value="Do something",
        )
        with self.assertRaises(FrozenInstanceError):
            item.value = "Mutated"  # type: ignore

    def test_context_item_legacy_compatibility(self) -> None:
        item = ContextItem(
            id="legacy-1",
            kind="legacy_kind",
            content="legacy content",
            source=ContextSourceType.EXPLICIT_INPUT,
            trust=ContextTrustLevel.UNTRUSTED,
        )
        self.assertEqual(item.id, "legacy-1")
        self.assertEqual(item.kind, "legacy_kind")
        self.assertEqual(item.content, "legacy content")
        self.assertEqual(item.source, ContextSourceType.EXPLICIT_INPUT)
        self.assertEqual(item.trust, ContextTrustLevel.UNTRUSTED)
        self.assertEqual(item.item_id, "legacy-1")
        self.assertEqual(item.value, "legacy content")

    def test_context_item_to_dict(self) -> None:
        item = ContextItem(
            item_id="i-1",
            item_type="type_a",
            source_type=ContextSourceType.PROJECT_STATE,
            value="val_a",
        )
        d = item.to_dict()
        self.assertEqual(d["item_id"], "i-1")
        self.assertEqual(d["source_type"], "PROJECT_STATE")
        self.assertEqual(d["value"], "val_a")
        self.assertEqual(d["id"], "i-1")
        self.assertEqual(d["content"], "val_a")


class TestTraceSummary(unittest.TestCase):
    """Verify bounded trace summary behaviour."""

    def test_trace_summary_bounds_and_fields(self) -> None:
        summary = TraceSummary(
            event_count=42,
            latest_event_types=("engineering_run_started", "decision_made"),
            latest_sequence_number=41,
            latest_decision_reference="dec-123",
            latest_verification_reference="ver-456",
            latest_acceptance_reference="acc-789",
        )
        self.assertEqual(summary.event_count, 42)
        self.assertEqual(summary.latest_sequence_number, 41)
        self.assertEqual(len(summary.latest_event_types), 2)
        d = summary.to_dict()
        self.assertEqual(d["event_count"], 42)
        self.assertEqual(d["latest_decision_reference"], "dec-123")


class TestDecisionContextEnvelope(unittest.TestCase):
    """Verify DecisionContextEnvelope properties, immutability, and deterministic fingerprinting."""

    def test_envelope_creation_and_immutability(self) -> None:
        envelope = DecisionContextEnvelope(
            context_id="ctx-1",
            run_id="run-1",
            attempt_number=0,
            task_id="task-1",
            project_state_status="INITIAL",
            blocking_conditions=("approval_pending",),
            available_actions=("execute",),
        )
        self.assertEqual(envelope.context_id, "ctx-1")
        self.assertEqual(envelope.run_id, "run-1")
        self.assertEqual(envelope.item_count, 0)
        with self.assertRaises(FrozenInstanceError):
            envelope.task_id = "task-2"  # type: ignore

    def test_deterministic_fingerprint_invariance(self) -> None:
        item1 = ContextItem(
            item_id="item-1",
            item_type="req",
            source_type=ContextSourceType.REQUIREMENT,
            value="Req 1",
            trust_level=ContextTrustLevel.VERIFIED,
        )
        env1 = DecisionContextEnvelope(
            context_id="ctx-aaa",
            run_id="run-100",
            attempt_number=1,
            task_id="task-1",
            project_state_status="IN_PROGRESS",
            requirements_summary={"count": 1},
            blocking_conditions=("cond_b", "cond_a"),
            context_items=(item1,),
        )
        env2 = DecisionContextEnvelope(
            context_id="ctx-bbb",
            run_id="run-100",
            attempt_number=1,
            task_id="task-1",
            project_state_status="IN_PROGRESS",
            requirements_summary={"count": 1},
            blocking_conditions=("cond_a", "cond_b"),
            context_items=(item1,),
        )
        self.assertEqual(env1.context_fingerprint, env2.context_fingerprint)
        self.assertEqual(env1.fingerprint, env2.fingerprint)

    def test_fingerprint_changes_on_state_difference(self) -> None:
        env1 = DecisionContextEnvelope(
            context_id="ctx-1",
            run_id="run-100",
            attempt_number=1,
            project_state_status="INITIAL",
        )
        env2 = DecisionContextEnvelope(
            context_id="ctx-1",
            run_id="run-100",
            attempt_number=1,
            project_state_status="CHANGED",
        )
        self.assertNotEqual(env1.context_fingerprint, env2.context_fingerprint)


class TestContextValidation(unittest.TestCase):
    """Verify context validation and budget checks."""

    def test_valid_envelope_passes(self) -> None:
        env = DecisionContextEnvelope(
            context_id="ctx-1",
            run_id="run-1",
            attempt_number=0,
            task_id="task-1",
            project_state_status="INITIAL",
        )
        report = validate_decision_context(env)
        self.assertTrue(report.valid)
        self.assertEqual(len(report.errors), 0)

    def test_invalid_attempt_number_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DecisionContextEnvelope(
                context_id="ctx-1",
                run_id="run-1",
                attempt_number=-1,
            )

    def test_empty_run_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DecisionContextEnvelope(
                context_id="ctx-1",
                run_id="",
                attempt_number=0,
            )

    def test_budget_item_count_exceeded(self) -> None:
        items = tuple(
            ContextItem(
                item_id=f"item-{i}",
                item_type="test",
                source_type=ContextSourceType.SYSTEM_POLICY,
                value=f"val-{i}",
            )
            for i in range(MAX_CONTEXT_ITEMS + 1)
        )
        env = DecisionContextEnvelope(
            context_id="ctx-1",
            run_id="run-1",
            attempt_number=0,
            context_items=items,
        )
        report = validate_decision_context(env)
        self.assertFalse(report.valid)
        self.assertTrue(any("context_items_budget_exceeded" in err for err in report.errors))

    def test_forbidden_substrings_in_metadata_rejected(self) -> None:
        env = DecisionContextEnvelope(
            context_id="ctx-1",
            run_id="run-1",
            attempt_number=0,
            metadata={"secret_token": "hidden_val"},
        )
        report = validate_decision_context(env)
        self.assertFalse(report.valid)
        self.assertTrue(any("forbidden_metadata_key" in err for err in report.errors))

    def test_duplicate_item_ids_rejected(self) -> None:
        item_a = ContextItem(
            item_id="duplicate-id",
            item_type="type_a",
            source_type=ContextSourceType.PROJECT_STATE,
            value="v1",
        )
        item_b = ContextItem(
            item_id="duplicate-id",
            item_type="type_b",
            source_type=ContextSourceType.ACCEPTANCE,
            value="v2",
        )
        env = DecisionContextEnvelope(
            context_id="ctx-1",
            run_id="run-1",
            attempt_number=0,
            context_items=(item_a, item_b),
        )
        report = validate_decision_context(env)
        self.assertFalse(report.valid)
        self.assertTrue(any("duplicate_item_id" in err for err in report.errors))

    def test_cross_run_reference_rejected(self) -> None:
        env = DecisionContextEnvelope(
            context_id="ctx-1",
            run_id="run-1",
            attempt_number=0,
            project_state=ProjectState(
                run_id="other-run-999",
                attempt_number=0,
                status=ProjectStateStatus.INITIAL,
            ),
        )
        report = validate_decision_context(env)
        self.assertFalse(report.valid)
        self.assertTrue(any("cross_run_reference" in err for err in report.errors))

    def test_sanitize_context_metadata_removes_forbidden_keys(self) -> None:
        raw = {
            "safe_key": "safe_val",
            "api_key": "sk-12345",
            "user_password": "pwd",
            "stdout": "large raw stream",
            "long_val": "x" * 5000,
        }
        sanitized = sanitize_context_metadata(raw)
        self.assertIn("safe_key", sanitized)
        self.assertNotIn("api_key", sanitized)
        self.assertNotIn("user_password", sanitized)
        self.assertNotIn("stdout", sanitized)
        self.assertNotIn("long_val", sanitized)


class TestDecisionContextAssembler(unittest.TestCase):
    """Verify deterministic assembly of envelopes from run artifacts."""

    def setUp(self) -> None:
        self.assembler = DecisionContextAssembler()

    def test_assemble_from_structured_artifacts(self) -> None:
        req = Requirement(requirement_id="req-1", description="Implement math add")
        spec = TaskSpecification(
            task_id="task-math",
            title="Math Feature",
            description="Add feature",
            requirements=(req,),
            acceptance_criteria=(AcceptanceCriterion(criterion_id="crit-1", description="Adds 2+2=4", requirement_id="req-1"),),
        )
        state = ProjectState(
            run_id="run-42",
            attempt_number=1,
            status=ProjectStateStatus.CHANGED,
            task_id="task-math",
        )
        ver_res = VerificationResult(
            verification_id="ver-1",
            status=VerificationStatus.PASS,
            criterion_id="crit-1",
            code="verification_passed",
        )
        acc_res = AcceptanceResult(
            status=AcceptanceStatus.PASS,
            results=(),
            code="acceptance_passed",
        )

        trace = RunTrace("run-42")
        trace.append(
            RunEvent(
                run_id="run-42",
                sequence_number=0,
                event_type=EventType.ENGINEERING_RUN_STARTED,
            )
        )

        envelope = self.assembler.assemble(
            run_id="run-42",
            attempt_number=1,
            task_specification=spec,
            project_state=state,
            verification_results=(ver_res,),
            acceptance_result=acc_res,
            run_trace=trace,
            blocking_conditions=("approval_pending",),
        )

        self.assertEqual(envelope.run_id, "run-42")
        self.assertEqual(envelope.attempt_number, 1)
        self.assertEqual(envelope.task_id, "task-math")
        self.assertEqual(envelope.project_state_status, "CHANGED")
        self.assertEqual(envelope.acceptance_summary["status"], "pass")
        self.assertEqual(envelope.verification_summary["passed"], 1)
        self.assertEqual(envelope.blocking_conditions, ("approval_pending",))
        self.assertIsNotNone(envelope.trace_summary)
        self.assertEqual(envelope.trace_summary.event_count, 1)
        self.assertTrue(len(envelope.context_items) >= 4)
        self.assertTrue(len(envelope.context_fingerprint) == 64)

    def test_assemble_missing_structured_data_does_not_crash(self) -> None:
        envelope = self.assembler.assemble(
            run_id="run-minimal",
            attempt_number=0,
        )
        self.assertEqual(envelope.run_id, "run-minimal")
        self.assertEqual(envelope.attempt_number, 0)
        self.assertIsNone(envelope.task_id)
        self.assertIsNone(envelope.project_state_status)
        self.assertEqual(envelope.item_count, 0)
        self.assertTrue(len(envelope.context_fingerprint) == 64)

    def test_assemble_budget_exceeded_raises_error(self) -> None:
        items = [
            ContextItem(
                item_id=f"extra-{i}",
                item_type="extra",
                source_type=ContextSourceType.SYSTEM_POLICY,
                value="x",
            )
            for i in range(MAX_CONTEXT_ITEMS + 1)
        ]
        with self.assertRaises(ContextBudgetExceededError):
            self.assembler.assemble(
                run_id="run-overflow",
                attempt_number=0,
                context_items=items,
            )


class TestDecisionIntegration(unittest.TestCase):
    """Verify integration between DecisionRequest, DecisionContextEnvelope, and DecisionProvider."""

    def test_decision_request_carries_context_references(self) -> None:
        env = DecisionContextEnvelope(
            context_id="ctx-xyz",
            run_id="run-99",
            attempt_number=0,
            project_state_status="INITIAL",
        )
        req = DecisionRequest(
            decision_id="dec-1",
            run_id="run-99",
            attempt_number=0,
            context_id=env.context_id,
            context_fingerprint=env.context_fingerprint,
            context_envelope=env,
        )
        self.assertEqual(req.context_id, "ctx-xyz")
        self.assertEqual(req.context_fingerprint, env.context_fingerprint)
        self.assertEqual(req.context_envelope, env)
        d = req.to_dict()
        self.assertEqual(d["context_id"], "ctx-xyz")
        self.assertEqual(d["context_fingerprint"], env.context_fingerprint)

    def test_deterministic_decision_provider_populates_context_references(self) -> None:
        env = DecisionContextEnvelope(
            context_id="ctx-abc",
            run_id="run-10",
            attempt_number=0,
            project_state_status="INITIAL",
        )
        req = DecisionRequest(
            decision_id="dec-req-1",
            run_id="run-10",
            attempt_number=0,
            context_id=env.context_id,
            context_fingerprint=env.context_fingerprint,
            context_envelope=env,
        )
        provider = DeterministicDecisionProvider()
        decision = provider.decide(req)

        self.assertIn("context_id", decision.references)
        self.assertIn("context_fingerprint", decision.references)
        self.assertEqual(decision.references["context_id"], "ctx-abc")
        self.assertEqual(decision.references["context_fingerprint"], env.context_fingerprint)

    def test_decision_provider_falls_back_to_context_envelope_blocking_conditions(self) -> None:
        env = DecisionContextEnvelope(
            context_id="ctx-block",
            run_id="run-block",
            attempt_number=0,
            blocking_conditions=("security_denied",),
        )
        req = DecisionRequest(
            decision_id="dec-b",
            run_id="run-block",
            attempt_number=0,
            blocking_conditions=(),  # empty direct
            context_envelope=env,
        )
        provider = DeterministicDecisionProvider()
        decision = provider.decide(req)
        self.assertEqual(decision.decision_type, DecisionType.FAIL)
        self.assertEqual(decision.action, DecisionAction.FAIL_RUN)
        self.assertEqual(decision.reason_code, "security_denied")


class FakeRevisionExecutor(RevisionLoopExecutor):
    """Deterministic fake revision executor for integration testing."""

    def __init__(self, attempts: tuple[RevisionAttemptResult, ...], final_status: RevisionStatus) -> None:
        self._attempts = attempts
        self._final_status = final_status

    def execute(self, *args, **kwargs) -> RevisionResult:
        from app.orchestrator.models import Run, RunState
        run = Run(id="run-mock", task=Task(id="task-mock", description="Mock task"))
        return RevisionResult(
            run=run,
            status=self._final_status,
            attempt_number=len(self._attempts),
            acceptance_result=self._attempts[-1].acceptance_result if self._attempts else None,
            attempts=self._attempts,
        )


class TestEngineeringRunContextIntegration(unittest.TestCase):
    """Verify EngineeringRunExecutor assembles envelopes, emits trace events, and populates results."""

    def test_engineering_run_assembles_context_and_emits_event(self) -> None:
        attempt = RevisionAttemptResult(
            attempt_number=1,
            snapshots=(ProjectSnapshot(snapshot_id="snap-1", run_id="run-mock"),),
            changesets=(ChangeSet(changeset_id="cs-1"),),
            verification_results=(),
            acceptance_result=AcceptanceResult(
                status=AcceptanceStatus.PASS,
                results=(),
                code="acceptance_passed",
            ),
        )
        fake_revision = FakeRevisionExecutor(
            attempts=(attempt,),
            final_status=RevisionStatus.COMPLETED,
        )
        executor = EngineeringRunExecutor(revision_executor=fake_revision)

        crit = AcceptanceCriterion(criterion_id="crit-1", description="Must pass", requirement_id="req-1")
        req_item = Requirement(requirement_id="req-1", description="Task requirement")
        spec = TaskSpecification(
            task_id="task-spec-1",
            title="Spec Title",
            description="Testing context pipeline",
            requirements=(req_item,),
            acceptance_criteria=(crit,),
        )
        req = EngineeringRunRequest(task_specification=spec)

        res = executor.execute(req)

        # 1. Result carries context_envelopes and final_context_envelope
        self.assertTrue(len(res.context_envelopes) > 0)
        final_env = res.final_context_envelope
        self.assertIsNotNone(final_env)
        self.assertEqual(final_env.task_id, "task-spec-1")
        self.assertEqual(final_env.run_id, "run-mock")

        # 2. CONTEXT_DECISION_READY trace event emitted
        event_types = [ev.event_type for ev in res.events]
        self.assertIn(EventType.CONTEXT_DECISION_READY, event_types)

        ctx_ev = next(ev for ev in res.events if ev.event_type == EventType.CONTEXT_DECISION_READY)
        self.assertEqual(ctx_ev.context_id, final_env.context_id)
        self.assertEqual(ctx_ev.context_fingerprint, final_env.context_fingerprint)

        # 3. Decision references carry context_id and context_fingerprint
        self.assertTrue(len(res.decisions) > 0)
        final_dec = res.final_decision
        self.assertIsNotNone(final_dec)
        self.assertEqual(final_dec.references.get("context_id"), final_env.context_id)
        self.assertEqual(final_dec.references.get("context_fingerprint"), final_env.context_fingerprint)


class TestSafetyAndDeterminism(unittest.TestCase):
    """Verify safety boundaries: no network, no LLMs, no mutation."""

    def test_no_network_calls_during_assembly(self) -> None:
        assembler = DecisionContextAssembler()
        with patch.object(socket, "socket", side_effect=RuntimeError("Network access forbidden")):
            env = assembler.assemble(
                run_id="run-safe",
                attempt_number=0,
                blocking_conditions=("test_condition",),
            )
            self.assertIsNotNone(env)

    def test_context_objects_are_frozen(self) -> None:
        item = ContextItem(
            item_id="i1",
            item_type="t",
            source_type=ContextSourceType.SYSTEM_POLICY,
            value="v",
        )
        with self.assertRaises(FrozenInstanceError):
            item.value = "new_v"  # type: ignore

        env = DecisionContextEnvelope(
            context_id="c1",
            run_id="r1",
            attempt_number=0,
            context_items=(item,),
        )
        with self.assertRaises(FrozenInstanceError):
            env.context_id = "c2"  # type: ignore


if __name__ == "__main__":
    unittest.main()
