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
        agents.register(MockAgent(tool_invocations=[invocation]))
        orchestrator = Orchestrator(agents, default_provider="mock")
        tools = ToolRegistry()
        read_tool = ReadProjectFile(self.root, allowed_files)
        if register_tool:
            tools.register(read_tool)
        executor = ToolExecutor(
            tools,
            allowed_tool_ids=(
                ("read_project_file",) if register_tool and explicitly_allowed else ()
            ),
        )
        return RunExecutor(orchestrator, ContextAssembler(), executor), read_tool

    def test_allowed_read_project_file_flows_through_agent_dispatch_and_run(self):
        invocation = ToolInvocation(
            tool_id="read_project_file",
            input={"path": "allowed.txt"},
            invocation_id="invoke-allowed",
        )
        runner, _ = self.make_executor(invocation, allowed_files=("allowed.txt",))
        task = Task(id="read-allowed", description="Read the approved reference")

        run = runner.execute(task)

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
            EventType.TOOL_EXECUTION_STARTED,
            EventType.TOOL_EXECUTION_COMPLETED,
        }
        tool_events = [e for e in run.events if e.type in expected_types]
        self.assertEqual({e.type for e in tool_events}, expected_types)
        self.assertTrue(
            all(
                e.run_id == run.id
                and e.data["invocation_id"] == invocation.invocation_id
                and e.data["tool_id"] == invocation.tool_id
                for e in tool_events
            )
        )
        self.assertNotIn(self.allowed_content, repr(run.events))

    def test_unallowlisted_file_is_denied_without_being_read(self):
        invocation = ToolInvocation(
            tool_id="read_project_file",
            input={"path": "blocked.txt"},
            invocation_id="invoke-blocked",
        )
        runner, _ = self.make_executor(invocation, allowed_files=("allowed.txt",))
        original_read = Path.read_text

        def track_read(path, *args, **kwargs):
            if path.name == "blocked.txt":
                self.fail("denied file was read")
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", track_read):
            run = runner.execute(Task(id="read-blocked", description="Try blocked read"))

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
        runner, _ = self.make_executor(invocation, register_tool=False)

        run = runner.execute(Task(id="unknown-tool", description="Unknown tool"))

        result = run.result.tool_results[0]
        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertEqual(result.error, "tool is not registered")
        self.assertFalse(
            any(e.type == EventType.TOOL_EXECUTION_STARTED for e in run.events)
        )
        failed = next(
            event
            for event in run.events
            if event.type == EventType.TOOL_EXECUTION_FAILED
        )
        self.assertEqual(failed.data["tool_id"], "unregistered")
        requested = next(
            event
            for event in run.events
            if event.type == EventType.TOOL_INVOCATION_REQUESTED
        )
        self.assertEqual(requested.data["invocation_id"], invocation.invocation_id)

    def test_registered_but_not_explicitly_allowed_tool_is_denied(self):
        invocation = ToolInvocation(
            tool_id="read_project_file", input={"path": "allowed.txt"}, invocation_id="invoke-denied"
        )
        runner, _ = self.make_executor(
            invocation,
            allowed_files=("allowed.txt",),
            explicitly_allowed=False,
        )

        run = runner.execute(Task(id="not-allowed-tool", description="Denied tool"))

        self.assertEqual(run.result.tool_results[0].status, ToolStatus.DENIED)
        self.assertEqual(run.result.tool_results[0].error, "tool is not explicitly allowed")
        self.assertFalse(
            any(e.type == EventType.TOOL_EXECUTION_STARTED for e in run.events)
        )
if __name__ == "__main__":
    unittest.main()
