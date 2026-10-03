"""Offline tests for bounded read-only tool execution through Run/Dispatcher."""

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
from app.tools.contracts import ToolInvocation, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.permissions import (
    PermissionDecision,
    PermissionPolicy,
    PermissionReason,
    ToolExecutionContext,
)
from app.tools.read_project_file import ReadProjectFile
from app.tools.registry import ToolRegistry


class ToolRunTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.allowed_content = "private fixture text that must not enter events"
        (self.root / "allowed.txt").write_text(self.allowed_content, encoding="utf-8")
        (self.root / "blocked.txt").write_text("blocked file", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_executor(
        self, invocation, *, allowed_files=(), register_tool=True, explicitly_allowed=True
    ):
        agents = AgentRegistry()
        agent = MockAgent(tool_invocations=[invocation])
        agents.register(agent)
        orchestrator = Orchestrator(agents, default_provider="mock")
        tools = ToolRegistry()
        read_tool = ReadProjectFile(self.root, allowed_files)
        if register_tool:
            tools.register(read_tool)
        runner = RunExecutor(orchestrator, ContextAssembler(), ToolExecutor(tools))
        run_allowlist = (
            ("read_project_file",) if register_tool and explicitly_allowed else ()
        )
        return runner, read_tool, agent, run_allowlist

    def test_allowed_read_project_file_flows_through_agent_dispatch_and_run(self):
        invocation = ToolInvocation(
            tool_id="read_project_file",
            input={"path": "allowed.txt"},
            invocation_id="invoke-allowed",
        )
        runner, _, agent, run_allowlist = self.make_executor(
            invocation, allowed_files=("allowed.txt",)
        )
        task = Task(id="read-allowed", description="Read the approved reference")

        run = runner.execute(task, allowed_tool_ids=run_allowlist)

        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertTrue(run.result.success)
        self.assertEqual(run.result.provider, "mock")
        self.assertEqual(run.result.tool_invocations, [invocation])
        self.assertEqual(len(run.result.tool_results), 1)
        result = run.result.tool_results[0]
        self.assertEqual(result.invocation_id, invocation.invocation_id)
        self.assertEqual(result.status, ToolStatus.COMPLETED)
        self.assertEqual(result.output, self.allowed_content)
        self.assertTrue(any(e.type == EventType.CONTEXT_ASSEMBLED for e in run.events))
        self.assertTrue(any(e.type == EventType.PROVIDER_ATTEMPT for e in run.events))

        expected_types = {
            EventType.TOOL_INVOCATION_REQUESTED,
            EventType.PERMISSION_CHECKED,
            EventType.TOOL_EXECUTION_STARTED,
            EventType.TOOL_EXECUTION_COMPLETED,
        }
        tool_events = [e for e in run.events if e.type in expected_types]
        self.assertEqual({e.type for e in tool_events}, expected_types)
        self.assertEqual(
            [event.type for event in tool_events],
            [
                EventType.TOOL_INVOCATION_REQUESTED,
                EventType.PERMISSION_CHECKED,
                EventType.TOOL_EXECUTION_STARTED,
                EventType.TOOL_EXECUTION_COMPLETED,
            ],
        )
        self.assertTrue(
            all(
                e.run_id == run.id
                and e.data["invocation_id"] == invocation.invocation_id
                and e.data["tool_id"] == invocation.tool_id
                for e in tool_events
            )
        )
        self.assertNotIn(self.allowed_content, repr(run.events))
        self.assertEqual(agent.received_tool_results[0]["output"], self.allowed_content)
        check = next(e for e in run.events if e.type == EventType.PERMISSION_CHECKED)
        self.assertEqual(check.data["decision"], PermissionDecision.ALLOW.value)
        self.assertEqual(check.data["reason"], PermissionReason.ALLOWED.value)
        self.assertEqual(check.data["run_id"], run.id)

    def test_unallowlisted_file_is_denied_without_being_read(self):
        invocation = ToolInvocation(
            tool_id="read_project_file",
            input={"path": "blocked.txt"},
            invocation_id="invoke-blocked",
        )
        runner, _, _, run_allowlist = self.make_executor(
            invocation, allowed_files=("allowed.txt",)
        )
        original_read = Path.read_text

        def track_read(path, *args, **kwargs):
            if path.name == "blocked.txt":
                self.fail("denied file was read")
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", track_read):
            run = runner.execute(
                Task(id="read-blocked", description="Try blocked read"),
                allowed_tool_ids=run_allowlist,
            )

        self.assertEqual(len(run.result.tool_results), 1)
        denied = run.result.tool_results[0]
        self.assertEqual(denied.status, ToolStatus.DENIED)
        self.assertEqual(denied.error, "file path is outside the explicit allow-list")
        failed = next(
            event
            for event in run.events
            if event.type == EventType.TOOL_EXECUTION_FAILED
        )
        self.assertEqual(failed.data["invocation_id"], invocation.invocation_id)
        requested = next(
            event
            for event in run.events
            if event.type == EventType.TOOL_INVOCATION_REQUESTED
        )
        self.assertEqual(requested.data["invocation_id"], invocation.invocation_id)
        self.assertNotIn("blocked file", repr(run.events))

    def test_unknown_tool_is_denied_before_tool_execution(self):
        invocation = ToolInvocation(
            tool_id="unregistered", input={"path": "allowed.txt"}, invocation_id="invoke-unknown"
        )
        runner, _, agent, run_allowlist = self.make_executor(
            invocation, register_tool=False
        )

        run = runner.execute(
            Task(id="unknown-tool", description="Unknown tool"),
            allowed_tool_ids=run_allowlist,
        )

        result = run.result.tool_results[0]
        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertEqual(result.error, PermissionReason.UNKNOWN_TOOL.value)
        self.assertFalse(self.has_execution_events(run))
        permission_event = next(
            event
            for event in run.events
            if event.type == EventType.PERMISSION_CHECKED
        )
        self.assertEqual(permission_event.data["tool_id"], "unregistered")
        requested = next(
            event
            for event in run.events
            if event.type == EventType.TOOL_INVOCATION_REQUESTED
        )
        self.assertEqual(requested.data["invocation_id"], invocation.invocation_id)
        self.assertEqual(agent.received_tool_results[0]["status"], ToolStatus.DENIED.value)
        self.assertEqual(permission_event.data["reason"], PermissionReason.UNKNOWN_TOOL.value)
        self.assertTrue(
            any(e.type == EventType.TOOL_INVOCATION_DENIED for e in run.events)
        )

    def test_registered_but_not_explicitly_allowed_tool_is_denied(self):
        invocation = ToolInvocation(
            tool_id="read_project_file", input={"path": "allowed.txt"}, invocation_id="invoke-denied"
        )
        runner, tool, agent, run_allowlist = self.make_executor(
            invocation,
            allowed_files=("allowed.txt",),
            explicitly_allowed=False,
        )

        invocation.input["decision"] = "ALLOW"  # Agent input is not policy authority.
        invocation.input["private_note"] = "offline-secret-marker"
        with patch.object(tool, "execute", wraps=tool.execute) as execute_tool:
            run = runner.execute(
                Task(id="not-allowed-tool", description="Denied tool"),
                allowed_tool_ids=run_allowlist,
            )

        self.assertEqual(run.result.tool_results[0].status, ToolStatus.DENIED)
        self.assertEqual(run.result.tool_results[0].error, PermissionReason.NOT_ALLOWED.value)
        self.assertFalse(self.has_execution_events(run))
        self.assertEqual(agent.received_tool_results[0]["status"], ToolStatus.DENIED.value)
        execute_tool.assert_not_called()
        denied_event = next(
            event
            for event in run.events
            if event.type == EventType.TOOL_INVOCATION_DENIED
        )
        self.assertEqual(denied_event.data["reason"], PermissionReason.NOT_ALLOWED.value)
        self.assertEqual(
            next(e for e in run.events if e.type == EventType.PERMISSION_CHECKED).data[
                "decision"
            ],
            PermissionDecision.DENY.value,
        )
        self.assertNotIn("offline-secret-marker", repr(run.events))

    def test_malformed_invocation_is_denied_before_tool_execution(self):
        for invocation_id, malformed_input in (
            ("invoke-malformed-none", None),
            ("invoke-malformed-empty", {}),
            ("invoke-malformed-drive", {"path": "C:outside.txt"}),
        ):
            with self.subTest(invocation_id=invocation_id):
                invocation = ToolInvocation(
                    tool_id="read_project_file",
                    input=malformed_input,  # type: ignore[arg-type]
                    invocation_id=invocation_id,
                )
                runner, tool, agent, run_allowlist = self.make_executor(
                    invocation, allowed_files=("allowed.txt",)
                )

                with patch.object(tool, "execute", wraps=tool.execute) as execute_tool:
                    run = runner.execute(
                        Task(id="malformed", description="Malformed request"),
                        allowed_tool_ids=run_allowlist,
                    )

                self.assertEqual(run.result.tool_results[0].status, ToolStatus.DENIED)
                self.assertEqual(
                    run.result.tool_results[0].error,
                    PermissionReason.INVALID_INVOCATION.value,
                )
                self.assertFalse(self.has_execution_events(run))
                self.assertEqual(
                    agent.received_tool_results[0]["status"], ToolStatus.DENIED.value
                )
                self.assertEqual(
                    next(
                        e for e in run.events
                        if e.type == EventType.PERMISSION_CHECKED
                    ).data["reason"],
                    PermissionReason.INVALID_INVOCATION.value,
                )
                execute_tool.assert_not_called()

    def test_tool_executor_direct_call_still_runs_default_deny_policy(self):
        invocation = ToolInvocation("read_project_file", {"path": "allowed.txt"})
        tools = ToolRegistry()
        tools.register(ReadProjectFile(self.root, ("allowed.txt",)))
        executor = ToolExecutor(tools)
        emitted = []

        result = executor.execute(invocation, observer=lambda kind, data: emitted.append((kind, data)))

        self.assertEqual(result.status, ToolStatus.DENIED)
        permission_event = next(
            item for item in emitted if item[0] == EventType.PERMISSION_CHECKED
        )
        self.assertEqual(permission_event[1]["reason"], PermissionReason.MISSING_CONTEXT.value)
        self.assertFalse(
            any(kind == EventType.TOOL_EXECUTION_STARTED for kind, _ in emitted)
        )

    def test_followup_tool_proposal_is_denied_at_one_round_limit(self):
        invocation = ToolInvocation(
            "read_project_file", {"path": "allowed.txt"}, "invoke-repeat"
        )
        agents = AgentRegistry()
        agent = MockAgent(
            tool_invocations=[invocation],
            repeat_tool_invocations_after_results=True,
        )
        agents.register(agent)
        orchestrator = Orchestrator(agents, default_provider="mock")
        tools = ToolRegistry()
        tool = ReadProjectFile(self.root, ("allowed.txt",))
        tools.register(tool)
        runner = RunExecutor(
            orchestrator, ContextAssembler(), ToolExecutor(tools)
        )
        with patch.object(tool, "execute", wraps=tool.execute) as execute_tool:
            run = runner.execute(
                Task(id="one-round", description="Bound tool calls"),
                allowed_tool_ids=("read_project_file",),
            )

        self.assertEqual(execute_tool.call_count, 1)
        checks = [
            event.data
            for event in run.events
            if event.type == EventType.PERMISSION_CHECKED
        ]
        self.assertEqual(
            [item["decision"] for item in checks],
            [PermissionDecision.ALLOW.value, PermissionDecision.DENY.value],
        )
        self.assertEqual(checks[1]["reason"], PermissionReason.TOOL_LOOP_LIMIT.value)
        self.assertEqual(run.result.tool_results[-1].status, ToolStatus.DENIED)
        started = [
            event for event in run.events
            if event.type == EventType.TOOL_EXECUTION_STARTED
        ]
        self.assertEqual(len(started), 1)

    def test_input_value_does_not_change_permission_decision(self):
        policy = PermissionPolicy()
        context = ToolExecutionContext(
            run_id="run-input-stability",
            context_fingerprint="f" * 64,
            allowed_tool_ids=frozenset({"read_project_file"}),
        )
        allow_path = policy.check(
            ToolInvocation("read_project_file", {"path": "allowed.txt"}),
            context=context,
            tool_registered=True,
        )
        other_path = policy.check(
            ToolInvocation("read_project_file", {"path": "blocked.txt"}),
            context=context,
            tool_registered=True,
        )

        self.assertEqual(allow_path.decision, PermissionDecision.ALLOW)
        self.assertEqual(other_path.decision, allow_path.decision)
        self.assertEqual(other_path.reason, allow_path.reason)

    @staticmethod
    def has_execution_events(run):
        execution_types = {
            EventType.TOOL_EXECUTION_STARTED,
            EventType.TOOL_EXECUTION_COMPLETED,
            EventType.TOOL_EXECUTION_FAILED,
        }
        return any(event.type in execution_types for event in run.events)
if __name__ == "__main__":
    unittest.main()
