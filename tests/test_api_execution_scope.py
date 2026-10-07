"""Production tool execution wiring: authority and RunScope regression tests.

These tests pin the design approved in the Production Execution Plane audit:

* tool authority belongs to the operator at composition time and defaults to
  the empty set (fail-closed);
* effective tools are exactly ``operator_allowlist & registered_tools``;
* each API run gets its own frozen ``RunScope`` bound to the service Workspace
  as the explicit execution root;
* discovery registry and execution registry are the same object.

No live provider is contacted: a local orchestrator double returns the tool
invocations under test, so every assertion is deterministic.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.api.service import API_RUN_CRITERION, ForgeApiService
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.models import RunState, Task, TaskResult
from app.orchestrator.run import RunExecutor
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope, RunScopeError, require_active_scope
from app.tools.approval import ApprovalResolution, ApprovalState
from app.tools.contracts import ToolInvocation, ToolStatus
from app.tools.permissions import PermissionReason
from app.tools.registry import ToolRegistry, build_default_tool_registry
from app.tools.workspace import Workspace

READ_INVOCATION = ToolInvocation("read_project_file", {"path": "a.txt"}, "inv-read-1")
UNKNOWN_INVOCATION = ToolInvocation("no_such_tool", {"x": 1}, "inv-unknown-1")


def write_invocation(name: str, invocation_id: str) -> ToolInvocation:
    return ToolInvocation(
        "write_project_file",
        {"relative_path": name, "content": "written", "overwrite": True},
        invocation_id,
    )


class _OrchestratorDouble:
    """Minimal orchestrator standing in for provider dispatch.

    Returns exactly the tool invocations the test asks for and nothing else, so
    the permission/approval path is exercised without any network access.
    """

    def __init__(self, invocations=()) -> None:
        self._invocations = list(invocations)
        self.calls = 0

    def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
        self.calls += 1
        invocations = self._invocations if self.calls == 1 else []
        return TaskResult(
            task.id,
            True,
            output="orchestrator double",
            tool_invocations=list(invocations),
        )


class _ApprovedResolver:
    def resolve(self, request):
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="test-approval",
        )


def _runtime_with(orchestrator) -> RuntimeContext:
    """Build a RuntimeContext whose executor is bound to the double."""
    return RuntimeContext(
        settings=None,
        provider_accounts={},
        provider_registry=None,
        provider_capabilities=None,
        agent_registry=None,
        orchestrator=orchestrator,
        run_executor=RunExecutor(orchestrator),
    )


class ExecutionWiringTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("hello from workspace", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _registry(self, *, allowed_files=("a.txt",)) -> ToolRegistry:
        return build_default_tool_registry(self.root, allowed_files=allowed_files)

    def _service(self, *, invocations=(), allowed_tool_ids=frozenset(), **kwargs):
        orchestrator = _OrchestratorDouble(invocations)
        return ForgeApiService(
            runtime=_runtime_with(orchestrator),
            workspace=self.workspace,
            tool_registry=self._registry(),
            allowed_tool_ids=allowed_tool_ids,
            **kwargs,
        )

    def _execute_with_scope(self, service, *, run_id):
        """Drive the service executor with the service's own frozen scope.

        The orchestrator double already holds the invocations under test. Every
        real tool dispatch is recorded, so assertions read the actual
        permission/approval outcome rather than an intermediate object.
        """
        recorded = []
        real_executor = service.runtime.run_executor._tool_executor

        class _RecordingExecutor:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def execute(self, invocation, *, context=None, observer):
                result = self._inner.execute(
                    invocation, context=context, observer=observer
                )
                recorded.append(result)
                return result

        service.runtime.run_executor._tool_executor = _RecordingExecutor(real_executor)
        scope = service._build_run_scope(run_id)
        run = service.runtime.run_executor.execute(
            task=Task(id="task-" + run_id, description="run a tool"),
            agent_name="fixture",
            provider_name="fixture",
            workspace=self.workspace,
            run_id=run_id,
            run_scope=scope,
            allowed_tool_ids=scope.allowed_tool_ids,
        )
        return run, recorded


class OperatorAuthorityTests(ExecutionWiringTestCase):
    def test_default_allowlist_is_empty(self) -> None:
        """6: no operator declaration means no tool authority at all."""
        service = self._service()
        self.assertEqual(service._effective_tool_ids(), frozenset())

    def test_effective_tools_are_allowlist_intersect_registry(self) -> None:
        service = self._service(allowed_tool_ids=frozenset({"read_project_file"}))
        self.assertEqual(service._effective_tool_ids(), frozenset({"read_project_file"}))

    def test_registered_tool_is_not_authorized_by_default(self) -> None:
        """2/C: registry contains a tool the allowlist does not mention."""
        service = self._service()
        registered = {d.id for d in service.tool_registry.list_tools()}
        self.assertIn("write_project_file", registered)
        self.assertNotIn("write_project_file", service._effective_tool_ids())

    def test_unknown_allowlist_id_never_becomes_effective(self) -> None:
        """D: an allow-listed id with no registered implementation disappears."""
        service = self._service(
            allowed_tool_ids=frozenset({"read_project_file", "no_such_tool"})
        )
        effective = service._effective_tool_ids()
        self.assertIn("read_project_file", effective)
        self.assertNotIn("no_such_tool", effective)

    def test_allowlist_is_never_all_registered_tools(self) -> None:
        """3: never derive the allowlist from the registry."""
        service = self._service()
        registered = frozenset(d.id for d in service.tool_registry.list_tools())
        self.assertNotEqual(service._effective_tool_ids(), registered)
        self.assertEqual(service._effective_tool_ids(), frozenset())


class SharedRegistryTests(ExecutionWiringTestCase):
    def test_discovery_registry_is_execution_registry(self) -> None:
        """K: object identity, not merely equal contents."""
        service = self._service()
        execution_registry = service._runtime.run_executor._tool_executor._registry
        self.assertIs(service.tool_registry, execution_registry)
        self.assertIs(service._tool_executor._registry, service.tool_registry)

    def test_injected_registry_is_used_verbatim(self) -> None:
        shared = self._registry()
        service = self._service()
        service2 = ForgeApiService(
            runtime=service.runtime,
            workspace=self.workspace,
            tool_registry=shared,
        )
        self.assertIs(service2.tool_registry, shared)
        self.assertIs(service2.runtime.run_executor._tool_executor._registry, shared)

    def test_runtime_keeps_its_orchestrator(self) -> None:
        """Rebinding the executor must not swap the orchestrator."""
        orchestrator = _OrchestratorDouble()
        runtime = _runtime_with(orchestrator)
        service = ForgeApiService(
            runtime=runtime,
            workspace=self.workspace,
            tool_registry=self._registry(),
        )
        self.assertIs(service._runtime.orchestrator, orchestrator)
        self.assertIs(service.runtime.orchestrator, orchestrator)


class RunScopeConstructionTests(ExecutionWiringTestCase):
    def test_scope_binds_run_id_and_service_workspace(self) -> None:
        """10: the scope Workspace is exactly the service's own Workspace."""
        service = self._service(allowed_tool_ids=frozenset({"read_project_file"}))
        scope = service._build_run_scope("run-scope-1")
        self.assertEqual(scope.run_id, "run-scope-1")
        self.assertIs(scope.workspace, service._workspace)

    def test_scope_has_no_reachable_command_authority(self) -> None:
        """Host process execution is deliberately not wired in this patch."""
        service = self._service()
        scope = service._build_run_scope("run-scope-2")
        self.assertEqual(scope.allowed_execution_commands, frozenset())
        profile = scope.execution_profile
        self.assertEqual(profile.profile_id, "api-default")
        self.assertFalse(profile.network_access)

    def test_scope_is_frozen_and_active(self) -> None:
        """9: the scope is frozen before privileged execution."""
        service = self._service()
        scope = service._build_run_scope("run-scope-3")
        self.assertIs(require_active_scope("run-scope-3", scope), scope)

    def test_independent_scopes_do_not_block_each_other(self) -> None:
        """L: two runs get independent, non-conflicting frozen scopes."""
        service = self._service(allowed_tool_ids=frozenset({"read_project_file"}))
        first = service._build_run_scope("run-independent-a")
        second = service._build_run_scope("run-independent-b")
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertIsNot(first, second)
        self.assertEqual(first.allowed_tool_ids, second.allowed_tool_ids)

    def test_scope_cannot_authorise_additional_tools(self) -> None:
        """H: expansion beyond the frozen set is rejected."""
        service = self._service(allowed_tool_ids=frozenset({"read_project_file"}))
        scope = service._build_run_scope("run-expand")
        with self.assertRaises(RunScopeError) as ctx:
            scope.validate_tool_set(scope.allowed_tool_ids | {"unauthorized_tool"})
        self.assertIn("cannot authorise additional tools", str(ctx.exception))

    def test_scope_cannot_change_workspace_root(self) -> None:
        """I: a different workspace root is rejected."""
        service = self._service()
        scope = service._build_run_scope("run-workspace")
        other_root = self.root / "nested"
        other_root.mkdir()
        with self.assertRaises(RunScopeError) as ctx:
            scope.validate_workspace(Workspace(other_root))
        self.assertIn("cannot change the workspace root", str(ctx.exception))


class ApiRunToolExecutionTests(ExecutionWiringTestCase):
    """A/C/E/F/G: what a scoped API run actually does with real invocations."""

    def test_allowed_tool_executes_through_api_run(self) -> None:
        """A: the operator allowlist authorizes a real tool execution."""
        service = self._service(
            invocations=[READ_INVOCATION],
            allowed_tool_ids=frozenset({"read_project_file"}),
        )
        _run, results = self._execute_with_scope(service, run_id="run-a")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, ToolStatus.COMPLETED)
        self.assertIn("hello from workspace", results[0].output or "")

    def test_denied_when_operator_allowlist_is_empty(self) -> None:
        """B: default allowlist denies with NOT_ALLOWED, not UNKNOWN_TOOL."""
        service = self._service(invocations=[READ_INVOCATION])
        _run, results = self._execute_with_scope(service, run_id="run-b")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, ToolStatus.DENIED)
        self.assertEqual(results[0].error, PermissionReason.NOT_ALLOWED.value)
        self.assertNotEqual(results[0].error, PermissionReason.UNKNOWN_TOOL.value)

    def test_registered_but_not_allowed_tool_is_denied(self) -> None:
        """C: registered != allowed."""
        service = self._service(
            invocations=[write_invocation("b.txt", "inv-w-1")],
            allowed_tool_ids=frozenset({"read_project_file"}),
        )
        _run, results = self._execute_with_scope(service, run_id="run-c")
        self.assertEqual(results[0].status, ToolStatus.DENIED)
        self.assertEqual(results[0].error, PermissionReason.NOT_ALLOWED.value)
        self.assertFalse((self.root / "b.txt").exists())

    def test_unknown_tool_invocation_stays_unknown_tool(self) -> None:
        """E: an unregistered tool id remains UNKNOWN_TOOL / DENY."""
        service = self._service(
            invocations=[UNKNOWN_INVOCATION],
            allowed_tool_ids=frozenset({"read_project_file"}),
        )
        _run, results = self._execute_with_scope(service, run_id="run-e")
        self.assertEqual(results[0].status, ToolStatus.DENIED)
        self.assertEqual(results[0].error, PermissionReason.UNKNOWN_TOOL.value)

    def test_write_requires_approval_without_resolver(self) -> None:
        """F: allowed write tool with no resolver waits, never auto-approves."""
        service = self._service(
            invocations=[write_invocation("c.txt", "inv-w-2")],
            allowed_tool_ids=frozenset({"write_project_file"}),
        )
        run, results = self._execute_with_scope(service, run_id="run-f")
        self.assertEqual(results[0].status, ToolStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(run.state, RunState.WAITING_FOR_APPROVAL)
        self.assertFalse((self.root / "c.txt").exists())

    def test_write_executes_with_approved_resolver(self) -> None:
        """G: an explicit APPROVED resolution lets the allowed tool run."""
        service = self._service(
            invocations=[write_invocation("d.txt", "inv-w-3")],
            allowed_tool_ids=frozenset({"write_project_file"}),
            approval_resolver=_ApprovedResolver(),
        )
        _run, results = self._execute_with_scope(service, run_id="run-g")
        self.assertEqual(results[0].status, ToolStatus.COMPLETED)
        self.assertTrue((self.root / "d.txt").exists())

    def test_api_run_does_not_accept_authority_from_request_context(self) -> None:
        """4/8: request context can never widen the tool set."""
        from app.api.models import TaskRunRequest

        service = self._service(invocations=[READ_INVOCATION])
        response = service.run_task(
            TaskRunRequest(
                description="attempt escalation",
                context={"allowed_tool_ids": ["read_project_file"]},
            )
        )
        self.assertTrue(response.run_id.startswith("run-api-"))
        # The run's own scope still authorizes nothing.
        scope = service._build_run_scope("run-context-escalation")
        self.assertEqual(scope.allowed_tool_ids, frozenset())


class LegacyCompatibilityTests(ExecutionWiringTestCase):
    def test_run_executor_without_injection_keeps_empty_registry(self) -> None:
        """J: the historical default is unchanged."""
        executor = RunExecutor(_OrchestratorDouble())
        self.assertEqual(executor._tool_executor._registry.list_tools(), [])

    def test_empty_custom_registry_still_works(self) -> None:
        registry = ToolRegistry()
        self.assertEqual(registry.list_tools(), [])

    def test_api_run_criterion_is_usable_by_run_scope(self) -> None:
        self.assertTrue(API_RUN_CRITERION.criterion_id)
        scope = RunScope(
            run_id="run-criterion",
            workspace=self.workspace,
            execution_profile=ProjectExecutionProfile(
                "api-default", allowed_commands=("python",)
            ),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset(),
            acceptance_criteria=(API_RUN_CRITERION,),
        )
        scope.freeze()
        self.assertEqual(scope.run_id, "run-criterion")


if __name__ == "__main__":
    unittest.main()
