"""Offline integration tests for approval through the ordinary Run path."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.mock_agent import MockAgent
from app.agents.registry import AgentRegistry
from app.context import ContextAssembler
from app.orchestrator.models import EventType, RunState, Task
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolution,
    ApprovalState,
    InMemoryApprovalResolver,
)
from app.tools.contracts import ToolInvocation, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.read_project_file import ReadProjectFile
from app.tools.registry import ToolRegistry


class _FixedResolver:
    def __init__(self, decision):
        self.decision = decision
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return ApprovalResolution(
            decision=self.decision,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="fixed-appr",
        )


class ApprovalBoundaryRunTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.content = "approved read result"
        (self.root / "allowed.txt").write_text(self.content, encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_runner(self, invocation, *, required=True, resolver=None):
        agents = AgentRegistry()
        agent = MockAgent(tool_invocations=(invocation,))
        agents.register(agent)
        orchestrator = Orchestrator(agents, default_provider="mock")
        registry = ToolRegistry()
        tool = ReadProjectFile(self.root, ("allowed.txt",))
        registry.register(tool)
        tool_executor = ToolExecutor(
            registry,
            approval_policy=ApprovalPolicy(
                ("read_project_file",) if required else ()
            ),
            approval_resolver=resolver,
        )
        runner = RunExecutor(
            orchestrator, ContextAssembler(), tool_executor
        )
        return runner, agent, tool

    def execute(self, runner, invocation):
        return runner.execute(
            Task(id="approval-test", description="Read a permitted project file"),
            allowed_tool_ids=("read_project_file",),
        )

    def test_permission_deny_never_requests_approval_or_executes(self):
        invocation = self.invocation()
        resolver = _FixedResolver(ApprovalState.APPROVED)
        runner, _, tool = self.make_runner(invocation, resolver=resolver)

        # The tool is registered, but caller authorization is absent.
        with patch.object(tool, "execute", wraps=tool.execute) as execute:
            run = runner.execute(
                Task(id="denied", description="Try a tool"), allowed_tool_ids=()
            )

        self.assertEqual(run.result.tool_results[0].status, ToolStatus.DENIED)
        self.assertFalse(any(e.type == EventType.APPROVAL_REQUESTED for e in run.events))
        self.assertFalse(any(e.type == EventType.TOOL_EXECUTION_STARTED for e in run.events))
        self.assertEqual(resolver.requests, [])
        execute.assert_not_called()

    def test_allow_and_not_required_executes_and_returns_result(self):
        invocation = self.invocation()
        runner, agent, tool = self.make_runner(invocation, required=False)
        with patch.object(tool, "execute", wraps=tool.execute) as execute:
            run = self.execute(runner, invocation)

        execute.assert_called_once()
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(run.result.tool_results[0].status, ToolStatus.COMPLETED)
        self.assertEqual(agent.received_tool_results[0]["output"], self.content)
        self.assertFalse(any(e.type == EventType.APPROVAL_REQUESTED for e in run.events))

    def test_required_without_resolution_waits_without_execution(self):
        invocation = self.invocation()
        runner, _, tool = self.make_runner(invocation)
        with patch.object(tool, "execute", wraps=tool.execute) as execute:
            run = self.execute(runner, invocation)

        self.assertEqual(run.state, RunState.WAITING_FOR_APPROVAL)
        self.assertEqual(
            run.result.tool_results[0].status, ToolStatus.WAITING_FOR_APPROVAL
        )
        self.assertFalse(any(e.type == EventType.RUN_COMPLETED for e in run.events))
        self.assertFalse(any(e.type == EventType.TOOL_EXECUTION_STARTED for e in run.events))
        execute.assert_not_called()
        requested = next(e for e in run.events if e.type == EventType.APPROVAL_REQUESTED)
        self.assertEqual(requested.data["run_id"], run.id)
        self.assertEqual(requested.data["invocation_id"], invocation.invocation_id)
        self.assertEqual(requested.data["tool_id"], invocation.tool_id)
        self.assertNotIn(self.content, repr(run.events))

    def test_required_and_approved_executes_through_tool_executor(self):
        invocation = self.invocation()
        resolver = _FixedResolver(ApprovalState.APPROVED)
        runner, _, tool = self.make_runner(invocation, resolver=resolver)
        with patch.object(tool, "execute", wraps=tool.execute) as execute:
            run = self.execute(runner, invocation)

        execute.assert_called_once()
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(run.result.tool_results[0].status, ToolStatus.COMPLETED)
        self.assertEqual(resolver.requests[0].run_id, run.id)
        self.assertEqual(resolver.requests[0].invocation_id, invocation.invocation_id)
        self.assertTrue(any(e.type == EventType.APPROVAL_RESOLVED for e in run.events))

    def test_required_and_rejected_does_not_execute(self):
        invocation = self.invocation()
        runner, _, tool = self.make_runner(
            invocation, resolver=_FixedResolver(ApprovalState.REJECTED)
        )
        with patch.object(tool, "execute", wraps=tool.execute) as execute:
            run = self.execute(runner, invocation)

        execute.assert_not_called()
        self.assertEqual(run.result.tool_results[0].status, ToolStatus.DENIED)
        self.assertEqual(run.result.tool_results[0].error, "approval rejected")
        self.assertTrue(any(e.type == EventType.APPROVAL_RESOLVED for e in run.events))

    def test_agent_input_cannot_self_approve_and_other_run_grant_does_not_match(self):
        invocation = self.invocation()
        invocation.input["approval"] = "APPROVED"
        resolver = InMemoryApprovalResolver()
        resolver.submit("different-run", invocation.invocation_id, ApprovalState.APPROVED)
        runner, _, tool = self.make_runner(invocation, resolver=resolver)
        with patch.object(tool, "execute", wraps=tool.execute) as execute:
            run = self.execute(runner, invocation)

        self.assertEqual(run.state, RunState.WAITING_FOR_APPROVAL)
        self.assertFalse(any(e.type == EventType.TOOL_EXECUTION_STARTED for e in run.events))
        execute.assert_not_called()
        self.assertNotIn("APPROVED", repr(run.events))

    def test_approval_resolver_requires_exact_run_and_invocation(self):
        resolver = InMemoryApprovalResolver()
        resolver.submit("run-1", "invoke-1", ApprovalState.APPROVED, intent_fingerprint="fp-1")
        wrong_run = ApprovalRequest("run-2", "invoke-1", "read_project_file", "test", intent_fingerprint="fp-1")
        wrong_invocation = ApprovalRequest("run-1", "invoke-2", "read_project_file", "test", intent_fingerprint="fp-1")
        exact = ApprovalRequest("run-1", "invoke-1", "read_project_file", "test", intent_fingerprint="fp-1")

        self.assertIsNone(resolver.resolve(wrong_run))
        self.assertIsNone(resolver.resolve(wrong_invocation))
        res = resolver.resolve(exact)
        self.assertIsNotNone(res)
        self.assertEqual(res.decision, ApprovalState.APPROVED)
        self.assertEqual(res.approved_fingerprint, "fp-1")
        self.assertIsNone(resolver.resolve(exact))  # decisions are consumed once

    @staticmethod
    def invocation():
        return ToolInvocation(
            "read_project_file", {"path": "allowed.txt"}, "invoke-approval-test"
        )


if __name__ == "__main__":
    unittest.main()
