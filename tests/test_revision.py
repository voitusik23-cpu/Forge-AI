"""Deterministic, offline integration coverage for bounded revisions."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.agents.registry import AgentRegistry
from app.orchestrator.models import EventType, RunState, Task, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.revision import RevisionLoopExecutor, RevisionStatus
from app.orchestrator.run import RunExecutor
from app.tools.acceptance import AcceptanceCriterion, AcceptanceStatus
from app.tools.approval import ApprovalPolicy, ApprovalState
from app.tools.contracts import ToolInvocation
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile


class _SequenceAgent:
    name = "fixture"
    provider_name = "fixture"

    def __init__(self, contents, *, path="result.txt"):
        self.contents = list(contents)
        self.path = path
        self.calls = 0
        self.revision_inputs = []

    @staticmethod
    def _revision_context(task):
        execution = task.context.get("forge_execution_context", {})
        for item in execution.get("items", []):
            if item.get("kind") == "task_context":
                value = json.loads(item["content"])
                return value.get("forge_revision")
        return None

    def run(self, task):
        if task.context.get("forge_tool_results"):
            return TaskResult(task.id, True, output="tool round complete")
        revision = self._revision_context(task)
        if revision is not None:
            self.revision_inputs.append(revision)
            index = revision["attempt_number"]
        else:
            index = 0
        content = self.contents[min(index, len(self.contents) - 1)]
        self.calls += 1
        return TaskResult(
            task.id,
            True,
            output="fixture proposal",
            tool_invocations=[ToolInvocation(
                WriteProjectFile.TOOL_ID,
                {"relative_path": self.path, "content": content, "overwrite": True},
                f"fixture-write-{self.calls}",
            )],
        )


class _Resolver:
    def __init__(self, decision=ApprovalState.APPROVED):
        self.decision = decision
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return self.decision


class RevisionLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.good = "correct fixture"
        self.bad = "incorrect fixture"
        self.criterion = AcceptanceCriterion("file-content", "File has expected digest")
        self.expectations = {
            self.criterion.criterion_id: VerificationExpectation(
                "result.txt", True, hashlib.sha256(self.good.encode()).hexdigest()
            )
        }

    def tearDown(self):
        self.temp.cleanup()

    def make_executor(self, contents, *, max_attempts=1, approval=ApprovalState.APPROVED, path="result.txt"):
        agent = _SequenceAgent(contents, path=path)
        agents = AgentRegistry()
        agents.register(agent)
        registry = ToolRegistry()
        registry.register(WriteProjectFile())
        resolver = _Resolver(approval)
        run_executor = RunExecutor(
            Orchestrator(agents, default_provider="fixture"),
            tool_executor=ToolExecutor(
                registry,
                approval_policy=ApprovalPolicy(),
                approval_resolver=resolver,
            ),
        )
        loop = RevisionLoopExecutor(run_executor, max_revision_attempts=max_attempts)
        return loop, agent, resolver

    def execute(self, loop, *, allowed=(WriteProjectFile.TOOL_ID,), explicit_inputs=()):
        return loop.execute(
            Task(id="revision-test", description="write expected file"),
            criteria=(self.criterion,),
            verification_expectations=self.expectations,
            explicit_inputs=explicit_inputs,
            allowed_tool_ids=allowed,
            workspace=self.workspace,
        )

    def test_initial_fail_triggers_approved_revision_and_passes(self):
        loop, agent, resolver = self.make_executor([self.bad, self.good])
        result = self.execute(
            loop,
            allowed=(item for item in (WriteProjectFile.TOOL_ID,)),
            explicit_inputs=(item for item in ("caller input",)),
        )
        run = result.run
        types = [event.type for event in run.events]
        self.assertEqual(result.status, RevisionStatus.COMPLETED)
        self.assertEqual(result.attempt_number, 1)
        self.assertEqual(result.acceptance_result.status, AcceptanceStatus.PASS)
        self.assertEqual((self.root / "result.txt").read_text(encoding="utf-8"), self.good)
        self.assertEqual(types.count(EventType.ACCEPTANCE_COMPLETED), 2)
        self.assertEqual(types.count(EventType.REVISION_STARTED), 1)
        self.assertEqual(types.count(EventType.REVISION_COMPLETED), 1)
        self.assertEqual(types.count(EventType.PERMISSION_CHECKED), 2)
        self.assertEqual(types.count(EventType.APPROVAL_REQUESTED), 2)
        self.assertEqual(types.count(EventType.TOOL_EXECUTION_COMPLETED), 2)
        self.assertEqual(types.count(EventType.VERIFICATION_COMPLETED), 2)
        self.assertEqual(len(resolver.requests), 2)
        self.assertEqual([r["attempt_number"] for r in agent.revision_inputs], [1])
        self.assertEqual(agent.revision_inputs[0]["reason"], "required_criteria_failed")
        self.assertEqual(agent.revision_inputs[0]["failed_criteria"], [
            {"criterion_id": "file-content", "code": "verification_failed"}
        ])
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertLess(types.index(EventType.REVISION_STARTED), types.index(EventType.ACCEPTANCE_COMPLETED, types.index(EventType.REVISION_STARTED)))
        self.assertNotIn(self.bad, repr(run.events))
        self.assertNotIn(self.good, repr(run.events))

    def test_failure_stops_at_configured_limit_and_attempts_are_monotonic(self):
        loop, agent, _ = self.make_executor([self.bad, self.bad], max_attempts=1)
        result = self.execute(loop)
        self.assertEqual(result.status, RevisionStatus.LIMIT_REACHED)
        self.assertEqual(result.attempt_number, 1)
        self.assertEqual(sum(e.type == EventType.REVISION_STARTED for e in result.run.events), 1)
        self.assertEqual([r["attempt_number"] for r in agent.revision_inputs], [1])

    def test_zero_limit_does_not_start_revision(self):
        loop, _, _ = self.make_executor([self.bad], max_attempts=0)
        result = self.execute(loop)
        self.assertEqual(result.status, RevisionStatus.LIMIT_REACHED)
        self.assertEqual(result.attempt_number, 0)
        self.assertFalse(any(e.type == EventType.REVISION_STARTED for e in result.run.events))

    def test_initial_pass_does_not_trigger_revision(self):
        loop, _, resolver = self.make_executor([self.good])
        result = self.execute(loop)
        self.assertEqual(result.status, RevisionStatus.COMPLETED)
        self.assertEqual(result.attempt_number, 0)
        self.assertEqual(len(resolver.requests), 1)
        self.assertFalse(any(e.type == EventType.REVISION_STARTED for e in result.run.events))

    def test_permission_deny_does_not_trigger_revision(self):
        loop, _, _ = self.make_executor([self.bad, self.good])
        result = self.execute(loop, allowed=())
        self.assertEqual(result.status, RevisionStatus.FAILED)
        self.assertIsNone(result.acceptance_result)
        self.assertFalse(any(e.type == EventType.REVISION_STARTED for e in result.run.events))

    def test_approval_rejection_does_not_trigger_or_bypass_approval(self):
        loop, _, resolver = self.make_executor([self.bad, self.good], approval=ApprovalState.REJECTED)
        result = self.execute(loop)
        self.assertEqual(result.status, RevisionStatus.FAILED)
        self.assertEqual(len(resolver.requests), 1)
        self.assertFalse(any(e.type == EventType.REVISION_STARTED for e in result.run.events))
        self.assertFalse((self.root / "result.txt").exists())

    def test_pending_approval_does_not_trigger_revision(self):
        loop, _, resolver = self.make_executor([self.bad, self.good], approval=None)
        result = self.execute(loop)
        self.assertEqual(result.status, RevisionStatus.FAILED)
        self.assertEqual(result.run.state, RunState.WAITING_FOR_APPROVAL)
        self.assertEqual(len(resolver.requests), 1)
        self.assertFalse(any(e.type == EventType.REVISION_STARTED for e in result.run.events))

    def test_workspace_traversal_is_denied_without_revision(self):
        loop, _, _ = self.make_executor([self.bad, self.good], path="../outside.txt")
        result = self.execute(loop)
        self.assertEqual(result.status, RevisionStatus.FAILED)
        self.assertFalse(any(e.type == EventType.REVISION_STARTED for e in result.run.events))
        self.assertFalse((self.root.parent / "outside.txt").exists())

    def test_two_revision_attempts_are_monotonic_and_no_third_occurs(self):
        loop, agent, _ = self.make_executor([self.bad], max_attempts=2)
        result = self.execute(loop)
        starts = [e for e in result.run.events if e.type == EventType.REVISION_STARTED]
        self.assertEqual(result.status, RevisionStatus.LIMIT_REACHED)
        self.assertEqual([e.data["attempt_number"] for e in starts], [1, 2])
        self.assertEqual([r["attempt_number"] for r in agent.revision_inputs], [1, 2])


if __name__ == "__main__":
    unittest.main()
