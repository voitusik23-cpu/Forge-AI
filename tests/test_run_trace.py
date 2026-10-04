"""Tests for the Run Trace and Run Event contract v0.1."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock

from app.artifacts import ChangeSet
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunResult,
    EngineeringRunStatus,
)
from app.orchestrator.models import Event, EventType, Run, Task
from app.orchestrator.revision import RevisionAttemptResult, RevisionResult, RevisionStatus
from app.orchestrator.trace import (
    RunEvent,
    RunEventCollector,
    RunEventType,
    RunTrace,
    build_trace_from_run,
    sanitize_event_metadata,
    validate_event_metadata,
)
from app.projects.state import ProjectState, ProjectStateStatus, derive_project_state
from app.snapshots import ProjectSnapshot
from app.tools.acceptance import AcceptanceCriterion, AcceptanceResult, AcceptanceStatus
from app.tools.verification import VerificationResult, VerificationStatus


class TestRunEventContract(unittest.TestCase):
    """Unit tests for the RunEvent model and RunEventType enum."""

    def test_run_event_type_canonical_members(self):
        """All minimum canonical event types must be defined."""
        required = {
            "RUN_STARTED",
            "CONTEXT_ASSEMBLED",
            "EXECUTION_REQUESTED",
            "EXECUTION_POLICY_CHECKED",
            "EXECUTION_STARTED",
            "EXECUTION_COMPLETED",
            "EXECUTION_DENIED",
            "APPROVAL_REQUESTED",
            "APPROVAL_RESOLVED",
            "VERIFICATION_COMPLETED",
            "ACCEPTANCE_COMPLETED",
            "REVISION_STARTED",
            "REVISION_COMPLETED",
            "PROJECT_STATE_UPDATED",
            "RUN_COMPLETED",
        }
        for name in required:
            self.assertTrue(
                hasattr(RunEventType, name),
                f"Missing required RunEventType member: {name}",
            )
            # Case-insensitive string normalization
            member = RunEventType(name)
            self.assertEqual(member, getattr(RunEventType, name))

    def test_event_creation_and_defaults(self):
        """RunEvent can be instantiated with minimal required fields."""
        ev = RunEvent(
            run_id="run-100",
            sequence_number=0,
            event_type=RunEventType.RUN_STARTED,
        )
        self.assertEqual(ev.run_id, "run-100")
        self.assertEqual(ev.sequence_number, 0)
        self.assertEqual(ev.event_type, RunEventType.RUN_STARTED)
        self.assertTrue(len(ev.event_id) > 0)
        self.assertIsInstance(ev.timestamp, datetime)
        self.assertIsNone(ev.attempt_number)
        self.assertIsNone(ev.task_id)
        self.assertIsNone(ev.requirement_id)
        self.assertIsNone(ev.criterion_id)
        self.assertIsNone(ev.execution_request_id)
        self.assertIsNone(ev.execution_result_id)
        self.assertIsNone(ev.verification_id)
        self.assertIsNone(ev.changeset_id)
        self.assertIsNone(ev.snapshot_id)
        self.assertIsNone(ev.project_state_status)
        self.assertIsNone(ev.acceptance_status)
        self.assertEqual(ev.metadata, {})

    def test_event_immutability(self):
        """RunEvent is frozen and cannot be mutated."""
        ev = RunEvent(
            run_id="run-100",
            sequence_number=0,
            event_type=RunEventType.RUN_STARTED,
        )
        with self.assertRaises(FrozenInstanceError):
            ev.sequence_number = 1  # type: ignore

    def test_run_id_and_sequence_number_validation(self):
        """Empty run_id or negative sequence_number must raise ValueError."""
        with self.assertRaises(ValueError):
            RunEvent(run_id="", sequence_number=0, event_type=RunEventType.RUN_STARTED)
        with self.assertRaises(ValueError):
            RunEvent(run_id="run-1", sequence_number=-1, event_type=RunEventType.RUN_STARTED)

    def test_artifact_references_and_to_dict(self):
        """RunEvent references artifacts by ID and serializes to dictionary safely."""
        now = datetime.now(timezone.utc)
        ev = RunEvent(
            run_id="run-abc",
            sequence_number=3,
            event_type=RunEventType.VERIFICATION_COMPLETED,
            timestamp=now,
            attempt_number=1,
            task_id="task-1",
            requirement_id="req-1",
            criterion_id="crit-1",
            execution_request_id="exec-req-1",
            execution_result_id="exec-res-1",
            verification_id="ver-1",
            changeset_id="cs-1",
            snapshot_id="snap-1",
            project_state_status="CHANGED",
            acceptance_status="pass",
            metadata={"check_type": "unit"},
        )
        d = ev.to_dict()
        self.assertEqual(d["run_id"], "run-abc")
        self.assertEqual(d["sequence_number"], 3)
        self.assertEqual(d["event_type"], "verification_completed")
        self.assertEqual(d["timestamp"], now.isoformat())
        self.assertEqual(d["task_id"], "task-1")
        self.assertEqual(d["requirement_id"], "req-1")
        self.assertEqual(d["criterion_id"], "crit-1")
        self.assertEqual(d["execution_request_id"], "exec-req-1")
        self.assertEqual(d["execution_result_id"], "exec-res-1")
        self.assertEqual(d["verification_id"], "ver-1")
        self.assertEqual(d["changeset_id"], "cs-1")
        self.assertEqual(d["snapshot_id"], "snap-1")
        self.assertEqual(d["project_state_status"], "CHANGED")
        self.assertEqual(d["acceptance_status"], "pass")
        self.assertEqual(d["metadata"], {"check_type": "unit"})


class TestMetadataSanitization(unittest.TestCase):
    """Unit tests verifying metadata sanitization and secret/stream purging."""

    def test_sanitization_removes_sensitive_and_output_fields(self):
        """Metadata containing stdout, stderr, raw output, credentials or prompts is sanitized."""
        raw = {
            "stdout": "raw output stream",
            "stderr": "error stream",
            "raw_output": "more output",
            "prompt": "system prompt",
            "chain_of_thought": "thinking steps",
            "secret_key": "supersecret",
            "api_key": "sk-12345",
            "token": "bearer xyz",
            "password": "pass",
            "credential": "cred",
            "source_code": "def foo(): pass",
            "file_content": "file data",
            "safe_tag": "build_check",
            "profile_name": "local",
        }
        sanitized = sanitize_event_metadata(raw)
        self.assertNotIn("stdout", sanitized)
        self.assertNotIn("stderr", sanitized)
        self.assertNotIn("raw_output", sanitized)
        self.assertNotIn("prompt", sanitized)
        self.assertNotIn("chain_of_thought", sanitized)
        self.assertNotIn("secret_key", sanitized)
        self.assertNotIn("api_key", sanitized)
        self.assertNotIn("token", sanitized)
        self.assertNotIn("password", sanitized)
        self.assertNotIn("credential", sanitized)
        self.assertNotIn("source_code", sanitized)
        self.assertNotIn("file_content", sanitized)
        self.assertEqual(sanitized["safe_tag"], "build_check")
        self.assertEqual(sanitized["profile_name"], "local")

    def test_run_event_automatically_sanitizes_metadata(self):
        """Constructing a RunEvent with sensitive metadata purges it in __post_init__."""
        ev = RunEvent(
            run_id="run-1",
            sequence_number=0,
            event_type=RunEventType.RUN_STARTED,
            metadata={"stdout": "leaked", "password": "123", "allowed_info": "ok"},
        )
        self.assertNotIn("stdout", ev.metadata)
        self.assertNotIn("password", ev.metadata)
        self.assertEqual(ev.metadata.get("allowed_info"), "ok")

    def test_validate_event_metadata_identifies_forbidden_keys(self):
        """validate_event_metadata returns errors for all forbidden substrings."""
        errors = validate_event_metadata({"user_api_key": "x", "stderr": "y", "clean": "z"})
        self.assertIn("forbidden_metadata_key:api_key", errors)
        self.assertIn("forbidden_metadata_key:stderr", errors)


class TestRunTraceAndCollector(unittest.TestCase):
    """Unit tests for RunTrace and RunEventCollector behavior."""

    def test_empty_trace(self):
        """An empty RunTrace has 0 length and empty events tuple."""
        trace = RunTrace(run_id="run-empty")
        self.assertEqual(trace.run_id, "run-empty")
        self.assertEqual(len(trace), 0)
        self.assertEqual(trace.events, ())

    def test_sequence_ordering_and_monotonicity(self):
        """RunTrace accepts events in strictly monotonic sequence order."""
        trace = RunTrace(run_id="run-seq")
        ev0 = RunEvent(run_id="run-seq", sequence_number=0, event_type=RunEventType.RUN_STARTED)
        ev1 = RunEvent(run_id="run-seq", sequence_number=1, event_type=RunEventType.CONTEXT_ASSEMBLED)
        trace.append(ev0)
        trace.append(ev1)
        self.assertEqual(len(trace), 2)
        self.assertEqual(trace[0].sequence_number, 0)
        self.assertEqual(trace[1].sequence_number, 1)

    def test_invalid_sequence_number_rejection(self):
        """Appending an event with a non-sequential sequence_number raises ValueError."""
        trace = RunTrace(run_id="run-seq")
        ev_wrong = RunEvent(run_id="run-seq", sequence_number=1, event_type=RunEventType.RUN_STARTED)
        with self.assertRaises(ValueError):
            trace.append(ev_wrong)

    def test_duplicate_sequence_number_rejection(self):
        """Appending a duplicate sequence_number raises ValueError."""
        trace = RunTrace(run_id="run-seq")
        ev0 = RunEvent(run_id="run-seq", sequence_number=0, event_type=RunEventType.RUN_STARTED)
        trace.append(ev0)
        with self.assertRaises(ValueError):
            trace.append(ev0)

    def test_cross_run_event_rejection(self):
        """Appending an event from a different run_id raises ValueError."""
        trace = RunTrace(run_id="run-A")
        ev_b = RunEvent(run_id="run-B", sequence_number=0, event_type=RunEventType.RUN_STARTED)
        with self.assertRaises(ValueError):
            trace.append(ev_b)

    def test_immutable_read_only_event_history(self):
        """trace.events is a tuple and cannot be modified externally."""
        trace = RunTrace(run_id="run-imm")
        ev0 = RunEvent(run_id="run-imm", sequence_number=0, event_type=RunEventType.RUN_STARTED)
        trace.append(ev0)
        events = trace.events
        self.assertIsInstance(events, tuple)
        with self.assertRaises(TypeError):
            events[0] = None  # type: ignore

    def test_collector_emit_and_to_trace(self):
        """RunEventCollector emits events with automatic sequence incrementation."""
        collector = RunEventCollector(run_id="run-col")
        e0 = collector.emit(RunEventType.RUN_STARTED, task_id="t-1")
        e1 = collector.emit(RunEventType.EXECUTION_REQUESTED, execution_request_id="req-1")
        e2 = collector.emit(RunEventType.RUN_COMPLETED)

        self.assertEqual(e0.sequence_number, 0)
        self.assertEqual(e1.sequence_number, 1)
        self.assertEqual(e2.sequence_number, 2)

        trace = collector.to_trace()
        self.assertIsInstance(trace, RunTrace)
        self.assertEqual(len(trace), 3)
        self.assertEqual(trace[0].event_type, RunEventType.RUN_STARTED)
        self.assertEqual(trace[1].event_type, RunEventType.EXECUTION_REQUESTED)
        self.assertEqual(trace[2].event_type, RunEventType.RUN_COMPLETED)


class TestEngineeringRunTraceIntegration(unittest.TestCase):
    """Integration tests verifying RunTrace in EngineeringRunResult."""

    def test_engineering_run_executor_populates_trace(self):
        """EngineeringRunExecutor produces an ordered RunTrace in EngineeringRunResult."""
        mock_revision_executor = MagicMock()
        cs = ChangeSet(changeset_id="cs-1", run_id="run-trace-test")
        snap = ProjectSnapshot(snapshot_id="snap-1", run_id="run-trace-test")
        v_res = VerificationResult(
            verification_id="ver-1",
            status=VerificationStatus.PASS,
            code="test_pass",
        )
        crit = AcceptanceCriterion(
            criterion_id="ac-1",
            description="All tests pass",
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

        mock_run = Run(id="run-trace-test", task=Task(id="task-trace", description="run task"))
        mock_run.events.append(Event(run_id="run-trace-test", type=EventType.RUN_STARTED, data={"task_id": "task-trace"}))
        mock_run.events.append(Event(run_id="run-trace-test", type=EventType.CONTEXT_ASSEMBLED, data={"task_id": "task-trace"}))
        mock_run.events.append(Event(run_id="run-trace-test", type=EventType.REVISION_STARTED, data={"attempt_number": 1}))
        mock_run.events.append(Event(run_id="run-trace-test", type=EventType.VERIFICATION_COMPLETED, data={"verification_id": "ver-1", "attempt_number": 1}))
        mock_run.events.append(Event(run_id="run-trace-test", type=EventType.ACCEPTANCE_COMPLETED, data={"acceptance_status": "pass", "attempt_number": 1}))
        mock_run.events.append(Event(run_id="run-trace-test", type=EventType.REVISION_COMPLETED, data={"attempt_number": 1}))

        mock_revision_executor.execute.return_value = RevisionResult(
            status=RevisionStatus.COMPLETED,
            attempt_number=1,
            acceptance_result=acc,
            run=mock_run,
            attempts=(attempt,),
        )

        executor = EngineeringRunExecutor(revision_executor=mock_revision_executor)
        req = EngineeringRunRequest(
            task=Task(id="task-trace", description="run task"),
            acceptance_criteria=[crit],
        )

        res = executor.execute(req)
        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)

        # Trace integration checks
        self.assertIsNotNone(res.trace)
        self.assertIsInstance(res.trace, RunTrace)
        self.assertEqual(res.trace.run_id, "run-trace-test")
        self.assertTrue(len(res.events) > 0)

        # Monotonic sequence check
        for idx, event in enumerate(res.events):
            self.assertEqual(event.sequence_number, idx)
            self.assertEqual(event.run_id, "run-trace-test")

        event_types = [e.event_type for e in res.events]

        # Verify key lifecycle event presence
        self.assertIn(RunEventType.RUN_STARTED, event_types)
        self.assertIn(RunEventType.REVISION_STARTED, event_types)
        self.assertIn(RunEventType.REVISION_COMPLETED, event_types)
        self.assertIn(RunEventType.PROJECT_STATE_UPDATED, event_types)
        self.assertIn(RunEventType.RUN_COMPLETED, event_types)

        # Check final event is RUN_COMPLETED
        self.assertEqual(res.events[-1].event_type, RunEventType.RUN_COMPLETED)

        # Check project state event contains artifact references
        ps_events = [e for e in res.events if e.event_type == RunEventType.PROJECT_STATE_UPDATED]
        self.assertTrue(len(ps_events) >= 1)
        ps_ev = ps_events[0]
        self.assertEqual(ps_ev.project_state_status, "ACCEPTED")
        self.assertEqual(ps_ev.changeset_id, "cs-1")
        self.assertEqual(ps_ev.snapshot_id, "snap-1")


if __name__ == "__main__":
    unittest.main()
