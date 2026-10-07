"""Production host process execution wiring: authority and perimeter tests.

These tests pin the approved design for host process execution:

* command authority belongs to the operator at composition time and defaults to
  the empty set, which means host process execution is disabled;
* the RunScope is the perimeter the coordinator re-validates every request
  against;
* ``ExecutionRequest`` is a request, never authority: it cannot widen the scope,
  the workspace, the profile, the environment, the network, the timeout, or the
  output limit;
* only ``ExecutionCoordinator`` can mint an ``AuthorizedExecution``, and
  ``LocalExecutionAdapter`` refuses anything else.

No shell command with side effects is used: the deterministic test executable is
the current interpreter asked to print a marker, which is what the existing
execution test infrastructure uses.
"""

from __future__ import annotations

import sys
from pathlib import Path
import tempfile
import unittest

from app.api.service import ForgeApiService
from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.intent import AuthorizedExecution, ExecutionIntent
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus, ExecutionRequest
from app.orchestrator.models import Task, TaskResult
from app.orchestrator.run import RunExecutor
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope, RunScopeError
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalResolution,
    ApprovalState,
)
from app.tools.registry import build_default_tool_registry
from app.tools.workspace import Workspace

# Deterministic, side-effect-free test executable: the running interpreter
# printing a fixed marker.
SAFE_EXECUTABLE = sys.executable
SAFE_ARGV = ("-c", "print('forge-host-exec-ok')")


def _profile(*, network_access: bool = False, timeout_seconds: float = 30.0,
             max_output_bytes: int = 1048576, environment_variables=None):
    return ProjectExecutionProfile(
        profile_id="api-default",
        allowed_commands=(SAFE_EXECUTABLE,),
        # The profile is a ceiling for capabilities too: policy.py requires that
        # every capability the argv implies is declared here, otherwise the
        # invocation is denied with argv_not_authorized.
        capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        network_access=network_access,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        environment_variables=environment_variables or {},
    )


def _request(**overrides):
    payload = {
        "command": (SAFE_EXECUTABLE, *SAFE_ARGV),
        "profile": _profile(),
    }
    payload.update(overrides)
    return ExecutionRequest(**payload)


class _OrchestratorDouble:
    """Server-side orchestrator stand-in; contacts no provider."""

    def __init__(self, *, success: bool = True) -> None:
        self._success = success

    def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
        return TaskResult(task.id, self._success, output="double")


class _ApprovedResolver:
    def resolve(self, request):
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="host-test",
        )


class HostExecutionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _runtime(self, *, success: bool = True) -> RuntimeContext:
        orchestrator = _OrchestratorDouble(success=success)
        return RuntimeContext(
            settings=None,
            provider_accounts={},
            provider_registry=None,
            provider_capabilities=None,
            agent_registry=None,
            orchestrator=orchestrator,
            run_executor=RunExecutor(orchestrator),
        )

    def _service(self, **kwargs) -> ForgeApiService:
        """Build a service whose command authority comes from a declaration.

        Since the declared-execution block, command authority is derived from
        operator declarations rather than an independent allowlist parameter, so
        these mechanism tests declare one verification execution and add the
        server-side resolver that selects it.
        """
        declaration = ExecutionDeclaration(
            declaration_id="verify.host",
            command=(SAFE_EXECUTABLE, *SAFE_ARGV),
            profile_id="api-default",
            working_directory=".",
        )
        kwargs.setdefault("runtime", self._runtime())
        kwargs.setdefault("workspace", self.workspace)
        kwargs.setdefault("tool_registry", build_default_tool_registry(self.root))
        kwargs.setdefault("declarations", {"verify.host": declaration})
        kwargs.setdefault(
            "verification_resolver", lambda task, profile: "verify.host"
        )
        kwargs.setdefault("execution_profile", _profile())
        kwargs.pop("allowed_execution_commands", None)
        return ForgeApiService(**kwargs)

    def _run_with_execution(self, service, *, run_id, requests_factory,
                            approval_policy="__service__"):
        """Drive the real RunExecutor host execution branch."""
        task = Task(id="task-" + run_id, description="host execution")
        scope = service._build_run_scope(run_id, task)
        policy = (
            (service._approval_policy or ApprovalPolicy())
            if approval_policy == "__service__"
            else approval_policy
        )
        return service.runtime.run_executor.execute(
            task=task,
            agent_name="fixture",
            provider_name="fixture",
            workspace=self.workspace,
            run_id=run_id,
            run_scope=scope,
            allowed_tool_ids=scope.allowed_tool_ids,
            allowed_execution_commands=scope.allowed_execution_commands,
            execution_request_factory=requests_factory,
            approval_policy=policy,
        )


class OperatorCommandAuthorityTests(HostExecutionTestCase):
    def test_default_command_allowlist_is_empty(self) -> None:
        """A: without declarations there is no command authority at all."""
        service = ForgeApiService(
            runtime=self._runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
        )
        self.assertEqual(service._allowed_execution_commands, frozenset())
        self.assertEqual(service._declarations, {})
        self.assertIsNone(service._verification_resolver)

    def test_default_scope_authorises_no_command(self) -> None:
        """A: with no declarations no command is inside the perimeter."""
        service = ForgeApiService(
            runtime=self._runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
        )
        scope = service._build_run_scope("host-default", Task(id="t", description="d"))
        self.assertEqual(scope.allowed_execution_commands, frozenset())
        self.assertFalse(scope.allows_command((SAFE_EXECUTABLE, *SAFE_ARGV)))
        self.assertFalse(scope.allows_command((SAFE_EXECUTABLE,)))

    def test_declared_service_derives_the_scope_command(self) -> None:
        """The scope command set is derived from the resolved declaration."""
        service = self._service()
        task = Task(id="host-derived", description="d")
        self.assertEqual(
            service._effective_execution_commands(task), frozenset({SAFE_EXECUTABLE})
        )
        scope = service._build_run_scope("host-derived", task)
        self.assertEqual(scope.allowed_execution_commands, frozenset({SAFE_EXECUTABLE}))

    def test_no_process_runs_without_declarations(self) -> None:
        """A: a server-side factory cannot execute without a declaration."""
        service = ForgeApiService(
            runtime=self._runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
        )
        run = self._run_with_execution(
            service,
            run_id="host-no-declaration",
            requests_factory=lambda task, profile: (_request(),),
        )
        self.assertEqual(run.execution_results, [])
        self.assertEqual(run.state.value, "COMPLETED")

    def test_authority_is_never_derived_from_the_profile(self) -> None:
        """The profile is a ceiling; only a declaration grants a command."""
        service = self._service()
        task = Task(id="host-ceiling", description="d")
        self.assertEqual(
            frozenset(service._execution_profile.allowed_commands),
            frozenset(service._effective_execution_commands(task)),
            "the derived command must be admitted by the profile ceiling",
        )
        # A declaration for a command outside the ceiling is rejected outright
        # rather than silently ignored, so the ceiling cannot be bypassed.
        with self.assertRaises(Exception):
            ForgeApiService(
                runtime=self._runtime(),
                workspace=self.workspace,
                tool_registry=build_default_tool_registry(self.root),
                declarations={
                    "outside": ExecutionDeclaration(
                        declaration_id="outside",
                        command=("definitely-not-allowed",),
                        profile_id="api-default",
                    )
                },
            )


class HostExecutionAuthorisedTests(HostExecutionTestCase):
    def test_authorised_command_reaches_the_coordinator_and_executes(self) -> None:
        """B: with operator authority the real process path executes."""
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(),
        )
        run = self._run_with_execution(
            service,
            run_id="host-authorised",
            requests_factory=lambda task, profile: (_request(),),
        )
        self.assertEqual(len(run.execution_results), 1)
        result = run.execution_results[0]
        self.assertEqual(
            result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS, result
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("forge-host-exec-ok", result.stdout or "")

    def test_execution_is_recorded_without_raw_output(self) -> None:
        """13: only sanitized lifecycle metadata is recorded."""
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(),
        )
        run = self._run_with_execution(
            service,
            run_id="host-recorded",
            requests_factory=lambda task, profile: (_request(),),
        )
        execution_events = [
            e for e in run.events if e.type.value.startswith("execution_")
        ]
        self.assertTrue(execution_events)
        for event in execution_events:
            self.assertNotIn("stdout", event.data)
            self.assertNotIn("stderr", event.data)
            self.assertNotIn("_token", event.data)
            self.assertNotIn("environment_variables", event.data)


class HostExecutionDenialTests(HostExecutionTestCase):
    def _denied(self, service, requests_factory, run_id):
        run = self._run_with_execution(
            service, run_id=run_id, requests_factory=requests_factory
        )
        self.assertEqual(len(run.execution_results), 1)
        return run.execution_results[0]

    def test_request_outside_scope_is_denied(self) -> None:
        """C: a command outside the frozen scope is a run_scope_violation."""
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(),
        )
        other = ExecutionRequest(
            command=("git", "status"), profile=_profile()
        )
        result = self._denied(
            service, lambda task, profile: (other,), "host-outside-scope"
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(
            result.metadata.get("denial_reason"), "run_scope_violation"
        )

    def test_workspace_mismatch_is_denied(self) -> None:
        """E: a different execution root is refused.

        The default adapter isolates the workspace and runs in an ephemeral
        scratch directory, which is why the coordinator skips the scope's
        workspace-root check for it (the isolation is enforced by
        EphemeralWorkspaceManager plus the Workspace boundary on the scratch
        root instead). This test uses the non-isolating adapter, where the scope
        root IS the execution root, so the mismatch must be denied.
        """
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(),
        )
        other_root = self.root / "nested"
        other_root.mkdir()
        scope = service._build_run_scope("host-workspace")
        coordinator = ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=other_root, isolate_workspace=False)
        )
        result = coordinator.execute(
            _request(),
            workspace_root=other_root,
            run_id="host-workspace",
            allowed_commands=scope.allowed_execution_commands,
            approval_policy=ApprovalPolicy(),
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(
            result.metadata.get("denial_reason"), "run_scope_violation"
        )

    def test_isolated_adapter_runs_in_a_scratch_workspace(self) -> None:
        """E: with isolation the process runs in a scratch directory.

        EphemeralWorkspaceManager stages the source workspace with COPY
        semantics and runs the process with that scratch directory as its
        working directory. This test pins that behaviour by proving a relative
        write lands in the scratch copy and not in the scoped source root.

        NOTE: this is a working-directory boundary, not a filesystem sandbox. A
        process given an absolute path can still write outside it; that is the
        separately deferred OS-level isolation block, and the test deliberately
        does not claim otherwise.
        """
        service = self._service(
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(),
        )
        scope = service._build_run_scope(
            "host-isolated", Task(id="host-isolated", description="d")
        )
        relative_write = ExecutionRequest(
            command=(SAFE_EXECUTABLE, "-c", "open('scratch-marker.txt','w').write('x')"),
            profile=_profile(),
        )
        result = ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.root, isolate_workspace=True)
        ).execute(
            relative_write,
            workspace_root=self.root,
            run_id="host-isolated",
            allowed_commands=scope.allowed_execution_commands,
            approval_policy=ApprovalPolicy(),
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertFalse(
            (self.root / "scratch-marker.txt").exists(),
            "a relative write must land in the scratch copy, not the scoped root",
        )

    def test_environment_variable_outside_profile_is_denied(self) -> None:
        """F: a request cannot introduce an environment variable."""
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(),
        )
        injected = ExecutionRequest(
            command=(SAFE_EXECUTABLE, *SAFE_ARGV),
            profile=_profile(),
            environment_variables={"PYTHONPATH": "/tmp/attacker"},
        )
        result = self._denied(
            service, lambda task, profile: (injected,), "host-env"
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)

    def test_network_enablement_is_denied(self) -> None:
        """G: a request profile cannot enable network access."""
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(network_access=False),
            approval_policy=ApprovalPolicy(),
        )
        net_request = ExecutionRequest(
            command=(SAFE_EXECUTABLE, *SAFE_ARGV),
            profile=_profile(network_access=True),
        )
        result = self._denied(
            service, lambda task, profile: (net_request,), "host-network"
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)

    def test_timeout_above_ceiling_is_denied(self) -> None:
        """H: a request cannot raise the timeout above the profile ceiling.

        Two existing barriers refuse this, depending on which ceiling is hit
        first: the run scope compares the request profile against the frozen
        profile exactly, and the execution policy rejects a request timeout above
        the profile limit. Either way the outcome is a policy denial, and the
        request cannot buy itself a longer execution.
        """
        service = self._service(
            execution_profile=_profile(timeout_seconds=30.0),
            approval_policy=ApprovalPolicy(),
        )
        scope = service._build_run_scope(
            "host-timeout", Task(id="host-timeout", description="d")
        )
        slow_request = ExecutionRequest(
            command=(SAFE_EXECUTABLE, *SAFE_ARGV),
            profile=_profile(timeout_seconds=30.0),
            timeout_seconds=600.0,
        )
        coordinator = ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.root)
        )
        result = coordinator.execute(
            slow_request,
            workspace_root=self.root,
            run_id="host-timeout",
            allowed_commands=scope.allowed_execution_commands,
            approval_policy=ApprovalPolicy(),
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertIn(
            str(result.metadata.get("denial_reason")),
            {"run_scope_violation", "timeout_exceeds_profile_limit:600.0>30.0"},
        )

    def test_output_above_ceiling_is_denied(self) -> None:
        """I: a request cannot raise the output limit above the ceiling."""
        service = self._service(
            execution_profile=_profile(max_output_bytes=1024),
            approval_policy=ApprovalPolicy(),
        )
        scope = service._build_run_scope(
            "host-output", Task(id="host-output", description="d")
        )
        loud_request = ExecutionRequest(
            command=(SAFE_EXECUTABLE, *SAFE_ARGV),
            profile=_profile(max_output_bytes=1024 * 1024 * 64),
        )
        coordinator = ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.root)
        )
        result = coordinator.execute(
            loud_request,
            workspace_root=self.root,
            run_id="host-output",
            allowed_commands=scope.allowed_execution_commands,
            approval_policy=ApprovalPolicy(),
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)

    def test_expanding_the_scope_command_set_is_rejected(self) -> None:
        """D: allowed_commands can never exceed the frozen scope."""
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(),
        )
        scope = service._build_run_scope("host-expand")
        with self.assertRaises(RunScopeError) as ctx:
            scope.validate_command_set((("git",),))
        self.assertIn("cannot authorise additional execution commands",
                      str(ctx.exception))


class HostExecutionApprovalTests(HostExecutionTestCase):
    def test_mandatory_approval_without_resolver_waits(self) -> None:
        """J: a server-side policy requiring approval fails closed.

        The default adapter isolates the workspace, so the process would run in
        an ephemeral scratch directory. This test therefore injects a
        non-isolating adapter bound to the scoped root, which is the strictest
        configuration, and proves approval still stops execution.
        """
        service = self._service(
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(("execute",)),
        )
        scope = service._build_run_scope(
            "host-approval-wait", Task(id="host-approval-wait", description="d")
        )
        result = ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.root, isolate_workspace=False)
        ).execute(
            _request(),
            workspace_root=self.root,
            run_id="host-approval-wait",
            allowed_commands=scope.allowed_execution_commands,
            approval_policy=service._approval_policy,
            run_scope=scope,
        )
        self.assertEqual(
            result.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING
        )
        self.assertNotEqual(result.exit_code, 0)

    def test_missing_approval_policy_refuses_to_execute(self) -> None:
        """8: a server-side ApprovalPolicy is mandatory for host execution.

        Without it, approval would depend only on the request's own
        ``approval_required`` flag, so the executor refuses rather than running
        with a weaker approval posture than the tool path.
        """
        service = self._service(
            execution_profile=_profile(),
        )
        self.assertIsNone(service._approval_policy)
        run = self._run_with_execution(
            service,
            run_id="host-no-approval-policy",
            requests_factory=lambda task, profile: (_request(),),
            approval_policy=None,
        )
        self.assertEqual(run.execution_results, [])
        reasons = [
            e.data.get("reason")
            for e in run.events
            if e.type.value == "execution_denied"
        ]
        self.assertIn("approval_policy_required", reasons)
        self.assertFalse(run.result.success)

    def test_missing_run_scope_refuses_to_execute(self) -> None:
        """4: host execution requires the frozen scope perimeter.

        Both layers refuse here: the executor's own gate and, independently, the
        coordinator, which denies any dispatch that declares command authority
        without a frozen scope. The assertion therefore pins the observable
        contract (no execution results, an explicit ``run_scope_required``
        denial) rather than attributing the refusal to one of the two.
        """
        service = self._service(
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(),
        )
        task = Task(id="task-no-scope", description="host execution")
        run = service.runtime.run_executor.execute(
            task=task,
            agent_name="fixture",
            provider_name="fixture",
            workspace=self.workspace,
            run_id="host-no-scope",
            allowed_execution_commands=service._effective_execution_commands(task),
            execution_request_factory=lambda task, profile: (_request(),),
            approval_policy=service._approval_policy,
        )
        self.assertEqual(run.execution_results, [])
        reasons = [
            e.data.get("reason")
            for e in run.events
            if e.type.value == "execution_denied"
        ]
        self.assertIn("run_scope_required", reasons)
        self.assertFalse(run.result.success)

    def test_coordinator_denies_command_authority_without_scope(self) -> None:
        """4: the coordinator independently refuses an unscoped dispatch."""
        scope_less = ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.root, isolate_workspace=False)
        ).execute(
            _request(),
            workspace_root=self.root,
            run_id="no-scope-probe",
            allowed_commands=frozenset({SAFE_EXECUTABLE}),
            approval_policy=ApprovalPolicy(),
            run_scope=None,
        )
        self.assertEqual(scope_less.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(
            scope_less.metadata.get("denial_reason"), "run_scope_required"
        )

    def test_run_state_becomes_waiting_for_approval(self) -> None:
        """J: the Run reflects the approval wait instead of completing."""
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(("execute",)),
        )
        run = self._run_with_execution(
            service,
            run_id="host-approval-state",
            requests_factory=lambda task, profile: (_request(),),
        )
        self.assertEqual(
            run.execution_results[0].outcome_status,
            ExecutionOutcomeStatus.APPROVAL_WAITING,
        )
        self.assertEqual(run.state.value, "WAITING_FOR_APPROVAL")
        self.assertFalse(run.result.success)

    def test_request_cannot_disable_server_side_approval(self) -> None:
        """K: approval_required=False must not bypass the server policy."""
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(("execute",)),
        )
        sneaky = ExecutionRequest(
            command=(SAFE_EXECUTABLE, *SAFE_ARGV),
            profile=_profile(),
            approval_required=False,
        )
        run = self._run_with_execution(
            service,
            run_id="host-approval-bypass",
            requests_factory=lambda task, profile: (sneaky,),
        )
        self.assertEqual(
            run.execution_results[0].outcome_status,
            ExecutionOutcomeStatus.APPROVAL_WAITING,
            "server-side policy must still require approval",
        )

    def test_approved_execution_completes(self) -> None:
        service = self._service(
            allowed_execution_commands=frozenset({SAFE_EXECUTABLE}),
            execution_profile=_profile(),
            approval_policy=ApprovalPolicy(("execute",)),
            approval_resolver=_ApprovedResolver(),
        )
        scope = service._build_run_scope(
            "host-approved", Task(id="host-approved", description="d")
        )
        result = ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.root)
        ).execute(
            _request(),
            workspace_root=self.root,
            run_id="host-approved",
            allowed_commands=scope.allowed_execution_commands,
            approval_policy=service._approval_policy,
            approval_resolver=service._approval_resolver,
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)


class ExecutionTokenBoundaryTests(HostExecutionTestCase):
    def test_authorized_execution_cannot_be_created_directly(self) -> None:
        """L: the execution token requires the coordinator sentinel."""
        intent = ExecutionIntent(
            executable=SAFE_EXECUTABLE,
            argv=SAFE_ARGV,
            working_directory=".",
            environment_variables=(),
            timeout_seconds=5.0,
            max_output_bytes=1024,
            artifact_targets=(),
            profile_id="api-default",
            network_access=False,
            capabilities=frozenset(),
        )
        with self.assertRaises(PermissionError):
            AuthorizedExecution.create(intent=intent, workspace_root=self.root)

    def test_adapter_refuses_without_authorized_execution(self) -> None:
        """M: the adapter refuses anything that is not a valid token."""
        adapter = LocalExecutionAdapter(workspace_root=self.root)
        for bad in (None, "python", {"intent": "x"}, (SAFE_EXECUTABLE,)):
            with self.assertRaises(Exception) as ctx:
                adapter.execute(bad)
            self.assertIn("AuthorizedExecution", str(ctx.exception))

    def test_api_never_holds_an_execution_token(self) -> None:
        """The API surface exposes no execution token."""
        service = self._service()
        for name in dir(service):
            self.assertNotIn("authorized", name.lower())
            self.assertNotIn("_token", name)


class LegacyCompatibilityTests(HostExecutionTestCase):
    def test_run_executor_without_new_parameters_still_works(self) -> None:
        """16: the historical construction path is unchanged."""
        executor = RunExecutor(_OrchestratorDouble())
        self.assertEqual(executor._tool_executor._registry.list_tools(), [])
        run = executor.execute(
            task=Task(id="legacy", description="d"),
            agent_name="fixture",
            provider_name="fixture",
        )
        self.assertEqual(run.state.value, "COMPLETED")
        self.assertEqual(run.execution_results, [])

    def test_default_service_requests_no_execution(self) -> None:
        """A default service has neither authority nor a request source."""
        service = self._service()
        self.assertIsNone(service._execution_request_factory)
        self.assertEqual(service._allowed_execution_commands, frozenset())
        self.assertIsNone(service._approval_policy)


if __name__ == "__main__":
    unittest.main()
