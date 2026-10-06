"""Offline Run integration tests for approved workspace writes."""

import hashlib
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
from app.tools.approval import ApprovalPolicy, ApprovalResolution, ApprovalState
from app.tools.contracts import ToolInvocation, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.workspace import Workspace, WorkspacePathError
from app.tools.write_project_file import WriteProjectFile


class _Resolver:
    def __init__(self, decision):
        self.decision = decision
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return ApprovalResolution(
            decision=self.decision,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="ws-approval-id",
        )


class WorkspaceBoundaryRunTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.workspace = Workspace(self.project)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_runner(self, invocation, *, decision=ApprovalState.APPROVED, workspace=True):
        agents = AgentRegistry()
        agent = MockAgent(tool_invocations=(invocation,))
        agents.register(agent)
        orchestrator = Orchestrator(agents, default_provider="mock")
        tools = ToolRegistry()
        writer = WriteProjectFile()
        tools.register(writer)
        resolver = _Resolver(decision) if decision is not None else None
        runner = RunExecutor(
            orchestrator,
            ContextAssembler(),
            ToolExecutor(
                tools,
                approval_policy=ApprovalPolicy(),
                approval_resolver=resolver,
            ),
        )
        return runner, agent, writer, resolver, self.workspace if workspace else None

    def execute(self, runner, workspace, *, allowed=True):
        return runner.execute(
            Task(id="workspace-run", description="Create a project file"),
            allowed_tool_ids=("write_project_file",) if allowed else (),
            workspace=workspace,
        )

    def test_approved_write_runs_through_run_permission_approval_and_tool(self):
        file_content = "approved-file-content-marker"
        invocation = self.invocation("hello.txt", file_content)
        runner, agent, writer, resolver, workspace = self.make_runner(invocation)
        with patch.object(writer, "execute", wraps=writer.execute) as execute_tool:
            run = self.execute(runner, workspace)

        target = self.project / "hello.txt"
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(target.read_text(encoding="utf-8"), file_content)
        execute_tool.assert_called_once()
        self.assertEqual(run.result.tool_results[0].status, ToolStatus.COMPLETED)
        self.assertEqual(agent.received_tool_results[0]["status"], ToolStatus.COMPLETED.value)
        self.assertEqual(resolver.requests[0].run_id, run.id)
        self.assertEqual(resolver.requests[0].invocation_id, invocation.invocation_id)
        requested = next(e for e in run.events if e.type == EventType.APPROVAL_REQUESTED)
        resolved = next(e for e in run.events if e.type == EventType.APPROVAL_RESOLVED)
        started = next(e for e in run.events if e.type == EventType.TOOL_EXECUTION_STARTED)
        completed = next(e for e in run.events if e.type == EventType.TOOL_EXECUTION_COMPLETED)
        self.assertEqual(requested.data["invocation_id"], invocation.invocation_id)
        self.assertEqual(resolved.data["resolution"], ApprovalState.APPROVED.value)
        self.assertEqual(completed.data["relative_path"], "hello.txt")
        self.assertEqual(completed.data["bytes_written"], len(file_content.encode("utf-8")))
        self.assertEqual(len(completed.data["content_sha256"]), 64)
        self.assertEqual(started.data["tool_id"], WriteProjectFile.TOOL_ID)
        self.assertNotIn(file_content, repr(run.events))

    def test_nested_directories_are_created_inside_workspace(self):
        invocation = self.invocation("src/generated/result.txt", "nested")
        runner, _, _, _, workspace = self.make_runner(invocation)

        run = self.execute(runner, workspace)

        target = self.project / "src" / "generated" / "result.txt"
        self.assertEqual(run.result.tool_results[0].status, ToolStatus.COMPLETED)
        self.assertEqual(target.read_text(encoding="utf-8"), "nested")
        self.assertTrue(target.parent.is_dir())

    def test_traversal_mixed_and_absolute_paths_are_denied_without_outside_write(self):
        candidates = (
            "../outside.txt",
            "../../outside.txt",
            "..\\outside.txt",
            "sub\\..\\..\\outside.txt",
            str(self.outside / "absolute.txt"),
            "C:\\Windows\\outside.txt",
            "/etc/forge-outside.txt",
        )
        for index, relative_path in enumerate(candidates):
            with self.subTest(path=relative_path):
                invocation = self.invocation(relative_path, "must-not-write")
                runner, _, _, _, workspace = self.make_runner(invocation)
                run = self.execute(runner, workspace)
                self.assertNotEqual(run.result.tool_results[0].status, ToolStatus.COMPLETED)
                self.assertFalse((self.outside / "outside.txt").exists())
                self.assertFalse((self.outside / "absolute.txt").exists())
                self.assertFalse((self.project / f"rejected-{index}").exists())

    def test_write_waits_for_approval_and_rejection_does_not_write(self):
        for decision, expected_run_state, expected_status in (
            (None, RunState.WAITING_FOR_APPROVAL, ToolStatus.WAITING_FOR_APPROVAL),
            (ApprovalState.REJECTED, RunState.COMPLETED, ToolStatus.DENIED),
        ):
            with self.subTest(decision=decision):
                invocation = self.invocation(f"{decision}.txt", "blocked")
                runner, _, _, _, workspace = self.make_runner(
                    invocation, decision=decision
                )
                run = self.execute(runner, workspace)
                self.assertEqual(run.state, expected_run_state)
                self.assertEqual(run.result.tool_results[0].status, expected_status)
                self.assertFalse((self.project / f"{decision}.txt").exists())
                self.assertFalse(
                    any(e.type == EventType.TOOL_EXECUTION_STARTED for e in run.events)
                )

    def test_permission_denial_cannot_be_overridden_by_approval(self):
        invocation = self.invocation("denied.txt", "blocked")
        runner, _, writer, resolver, workspace = self.make_runner(invocation)
        with patch.object(writer, "execute", wraps=writer.execute) as execute_tool:
            run = self.execute(runner, workspace, allowed=False)

        self.assertEqual(run.result.tool_results[0].status, ToolStatus.DENIED)
        self.assertEqual(resolver.requests, [])
        execute_tool.assert_not_called()
        self.assertFalse((self.project / "denied.txt").exists())
        self.assertFalse(any(e.type == EventType.APPROVAL_REQUESTED for e in run.events))

    def test_agent_cannot_supply_approval_or_workspace_root(self):
        invocation = self.invocation("agent-input.txt", "blocked")
        invocation.input["approval"] = "APPROVED"
        invocation.input["workspace_root"] = str(self.project)
        runner, _, writer, _, workspace = self.make_runner(invocation, decision=None)
        with patch.object(writer, "execute", wraps=writer.execute) as execute_tool:
            run = self.execute(runner, workspace)

        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(run.result.tool_results[0].status, ToolStatus.DENIED)
        execute_tool.assert_not_called()
        self.assertFalse((self.project / "agent-input.txt").exists())

    def test_overwrite_is_explicit_and_deterministic(self):
        target = self.project / "existing.txt"
        target.write_text("before", encoding="utf-8")

        no_overwrite = self.invocation("existing.txt", "after")
        runner, _, _, _, workspace = self.make_runner(no_overwrite)
        denied_run = self.execute(runner, workspace)
        self.assertEqual(denied_run.result.tool_results[0].status, ToolStatus.DENIED)
        self.assertEqual(target.read_text(encoding="utf-8"), "before")

        overwrite = self.invocation("existing.txt", "after", overwrite=True)
        runner, _, _, _, workspace = self.make_runner(overwrite)
        allowed_run = self.execute(runner, workspace)
        self.assertEqual(allowed_run.result.tool_results[0].status, ToolStatus.COMPLETED)
        self.assertEqual(target.read_text(encoding="utf-8"), "after")

    def test_missing_workspace_fails_closed(self):
        invocation = self.invocation("no-root.txt", "blocked")
        runner, _, writer, _, _ = self.make_runner(invocation, workspace=False)
        with patch.object(writer, "execute", wraps=writer.execute) as execute_tool:
            run = self.execute(runner, None)

        execute_tool.assert_called_once()
        self.assertEqual(run.result.tool_results[0].status, ToolStatus.DENIED)
        self.assertFalse((self.project / "no-root.txt").exists())

    def test_existing_symlink_escape_is_rejected(self):
        outside_file = self.outside / "preserve.txt"
        outside_file.write_text("outside stays unchanged", encoding="utf-8")
        link = self.project / "linked.txt"
        try:
            link.symlink_to(outside_file)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"file symlinks unavailable in this environment: {type(exc).__name__}")

        invocation = self.invocation("linked.txt", "must-not-replace")
        runner, _, _, _, workspace = self.make_runner(invocation)
        run = self.execute(runner, workspace)

        self.assertEqual(run.result.tool_results[0].status, ToolStatus.DENIED)
        self.assertEqual(outside_file.read_text(encoding="utf-8"), "outside stays unchanged")

    def test_workspace_root_must_be_explicit_absolute_existing_directory(self):
        with self.assertRaises(WorkspacePathError):
            Workspace("relative-root")
        with self.assertRaises(WorkspacePathError):
            Workspace(self.base / "missing")

    @staticmethod
    def invocation(relative_path, content, *, overwrite=False):
        input_data = {"relative_path": relative_path, "content": content}
        if overwrite:
            input_data["overwrite"] = True
        return ToolInvocation(
            WriteProjectFile.TOOL_ID,
            input_data,
            "write-" + hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16],
        )


if __name__ == "__main__":
    unittest.main()
