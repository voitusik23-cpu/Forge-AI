"""Offline tests for deterministic task acceptance."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.agents.mock_agent import MockAgent
from app.agents.registry import AgentRegistry
from app.orchestrator.models import Event, EventType, Task
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.tools.acceptance import (
    AcceptanceCriterion, AcceptanceGate, AcceptanceStatus,
)
from app.tools.approval import ApprovalPolicy, ApprovalResolution, ApprovalState
from app.tools.contracts import ToolInvocation
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import (
    VerificationExpectation, VerificationResult, VerificationStatus,
    WorkspaceVerifier,
)
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile


class _Approved:
    def resolve(self, request):
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="acc-approval-id",
        )


class AcceptanceGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = AcceptanceGate()
        self.events = []
        self.observer = lambda kind, data: self.events.append((kind, data))
        self.passed = VerificationResult("v-pass", VerificationStatus.PASS, "expectation_matched")
        self.failed = VerificationResult("v-fail", VerificationStatus.FAIL, "content_hash_mismatch")

    def evaluate(self, criteria, verifications):
        return self.gate.evaluate(criteria, verifications, run_id="run-a", observer=self.observer)

    def test_all_required_pass(self):
        result = self.evaluate([AcceptanceCriterion("a", "A"), AcceptanceCriterion("b", "B")], {"a": self.passed, "b": self.passed})
        self.assertEqual(result.status, AcceptanceStatus.PASS)

    def test_required_failure_fails(self):
        result = self.evaluate([AcceptanceCriterion("a", "A")], {"a": self.failed})
        self.assertEqual(result.status, AcceptanceStatus.FAIL)

    def test_optional_failure_does_not_fail_acceptance(self):
        result = self.evaluate([AcceptanceCriterion("a", "A"), AcceptanceCriterion("b", "B", False)], {"a": self.passed, "b": self.failed})
        self.assertEqual(result.status, AcceptanceStatus.PASS)
        self.assertEqual(result.results[1].status, AcceptanceStatus.FAIL)

    def test_missing_required_fails(self):
        result = self.evaluate([AcceptanceCriterion("a", "A")], {})
        self.assertEqual(result.results[0].code, "verification_missing")
        self.assertEqual(result.status, AcceptanceStatus.FAIL)

    def test_multiple_results_are_stable_and_event_counts_are_safe(self):
        criteria = [AcceptanceCriterion("z", "Z"), AcceptanceCriterion("a", "A", False), AcceptanceCriterion("b", "B")]
        first = self.evaluate(criteria, {"z": self.passed, "a": self.failed, "b": self.failed})
        event = self.events[-1][1]
        second = self.evaluate(criteria, {"z": self.passed, "a": self.failed, "b": self.failed})
        self.assertEqual(first, second)
        self.assertEqual(event["required_criteria_count"], 2)
        self.assertEqual(event["passed_count"], 1)
        self.assertEqual(event["failed_count"], 1)
        self.assertEqual(event["run_id"], "run-a")

    def test_duplicate_ids_and_empty_set_fail_safely(self):
        duplicate = self.evaluate([AcceptanceCriterion("a", "A"), AcceptanceCriterion("a", "Again")], {})
        empty = self.evaluate([], {})
        self.assertEqual(duplicate.code, "duplicate_criterion_id")
        self.assertEqual(duplicate.status, AcceptanceStatus.FAIL)
        self.assertEqual(empty.code, "empty_criteria")
        self.assertEqual(empty.status, AcceptanceStatus.FAIL)

    def test_write_verify_accept_and_verification_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root)
            payload = "offline fixture"
            invocation = ToolInvocation(WriteProjectFile.TOOL_ID, {"relative_path": "result.txt", "content": payload}, "write-1")
            agents = AgentRegistry()
            agents.register(MockAgent(tool_invocations=(invocation,)))
            tools = ToolRegistry()
            tools.register(WriteProjectFile())
            executor = RunExecutor(Orchestrator(agents, default_provider="mock"), tool_executor=ToolExecutor(tools, approval_policy=ApprovalPolicy(), approval_resolver=_Approved()))
            run = executor.execute(Task(id="acceptance-run", description="write fixture"), allowed_tool_ids=(WriteProjectFile.TOOL_ID,), workspace=workspace)
            observer = lambda kind, data: run.events.append(Event(run_id=run.id, type=kind, data=data))
            verifier = WorkspaceVerifier()
            criterion = AcceptanceCriterion("file-written", "Expected content is present")
            expected_hash = hashlib.sha256(payload.encode()).hexdigest()
            verification = verifier.verify(VerificationExpectation("result.txt", True, expected_hash), workspace=workspace, run_id=run.id, observer=observer)
            gate = AcceptanceGate()
            accepted = gate.evaluate([criterion], {criterion.criterion_id: verification}, run_id=run.id, observer=observer)
            self.assertEqual(accepted.status, AcceptanceStatus.PASS)
            event = next(e for e in run.events if e.type == EventType.ACCEPTANCE_COMPLETED)
            self.assertEqual(event.data["status"], "pass")
            self.assertNotIn(payload, repr(event.data))

            (root / "result.txt").write_text("changed", encoding="utf-8")
            failed_verification = verifier.verify(VerificationExpectation("result.txt", True, expected_hash), workspace=workspace, run_id=run.id, observer=observer)
            rejected = gate.evaluate([criterion], {criterion.criterion_id: failed_verification}, run_id=run.id, observer=observer)
            self.assertEqual(rejected.status, AcceptanceStatus.FAIL)
            self.assertEqual(rejected.results[0].code, "verification_failed")
            self.assertEqual((root / "result.txt").read_text(encoding="utf-8"), "changed")


if __name__ == "__main__":
    unittest.main()
