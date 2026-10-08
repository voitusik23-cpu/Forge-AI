"""Production agent loop vertical slice: canonical orchestration and authority.

These tests pin the production wiring of ``AgentHarness``:

* a trusted server-side caller reaches the canonical loop, and the loop produces
  exactly one decision and exactly one execution;
* the decision is a recommendation only - it cannot add a tool, add a command,
  change the workspace, profile, environment, timeout, or approval, and it cannot
  construct an execution token;
* the operator's declaration plus the frozen ``RunScope`` remain the only source
  of command authority, and the ``ExecutionCoordinator`` stays mandatory;
* the loop's stages are observable through the existing durable run history.

The deterministic test command is the running interpreter printing a fixed
marker; no user-supplied shell command appears anywhere.
"""

from __future__ import annotations

from pathlib import Path
import dataclasses
import inspect
import sys
import tempfile
import unittest

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService, SINGLE_ACTION_LOOP_POLICY
from app.decision.models import Decision, DecisionAction, DecisionType
from app.execution.declaration import (
    ExecutionDeclaration,
    ExecutionDeclarationError,
    UnknownExecutionDeclarationError,
)
from app.execution.intent import AuthorizedExecution
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus
from app.orchestrator.models import Task, TaskResult
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_agent_harness
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope, RunScopeError
from app.tools.approval import ApprovalPolicy
from app.tools.registry import build_default_tool_registry
from app.tools.workspace import Workspace

MARKER = "forge-loop-slice-ok"
EXECUTABLE = "python"
DECLARATION_ID = "verify.slice"


def declaration(**overrides) -> ExecutionDeclaration:
    payload = {
        "declaration_id": DECLARATION_ID,
        "command": (EXECUTABLE, "-c", f"print('{MARKER}')"),
        "profile_id": "api-default",
        "working_directory": ".",
    }
    payload.update(overrides)
    return ExecutionDeclaration(**payload)


def api_profile(**overrides) -> ProjectExecutionProfile:
    from app.execution.capabilities import ExecutionCapability

    payload = {
        "profile_id": "api-default",
        "allowed_commands": (EXECUTABLE,),
        "capabilities": frozenset({ExecutionCapability.INTERPRET_TEXT}),
        "network_access": False,
        "working_directory": ".",
    }
    payload.update(overrides)
    return ProjectExecutionProfile(**payload)


class _OrchestratorDouble:
    def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
        return TaskResult(task.id, True, output="double")


def _runtime(**overrides) -> RuntimeContext:
    orchestrator = _OrchestratorDouble()
    payload = {
        "settings": None,
        "provider_accounts": {},
        "provider_registry": None,
        "provider_capabilities": None,
        "agent_registry": None,
        "orchestrator": orchestrator,
        "run_executor": RunExecutor(orchestrator),
        "harness": create_agent_harness(),
    }
    payload.update(overrides)
    return RuntimeContext(**payload)


class _FixedDecisionProvider:
    """Return one pre-set action. A decision is only a recommendation."""

    def __init__(self, action: DecisionAction) -> None:
        self._action = action
        self.seen_requests: list[object] = []

    def decide(self, request):
        self.seen_requests.append(request)
        return Decision(
            decision_id="fixed-decision",
            run_id=request.run_id,
            decision_type=DecisionType.CONTINUE,
            action=self._action,
            reason_code="test_fixed",
        )


class AgentLoopSliceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _service(self, *, declarations=None, runtime=None, **kwargs):
        if declarations is None:
            declarations = {DECLARATION_ID: declaration()}
        return ForgeApiService(
            runtime=runtime or _runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=api_profile(),
            declarations=declarations,
            approval_policy=kwargs.pop("approval_policy", ApprovalPolicy()),
            **kwargs,
        )


class ProductionLoopTests(AgentLoopSliceTestCase):
    def test_trusted_caller_reaches_the_canonical_loop(self) -> None:
        """A: the production service entry point drives the real harness."""
        service = self._service()
        harness_calls: list[object] = []
        real_run = service._runtime.harness.run

        def spy(request):
            harness_calls.append(request)
            return real_run(request)

        service._runtime.harness.run = spy  # type: ignore[method-assign]
        # The slice builds its harness from the same composition factory, so spy
        # the factory-level harness too by asserting on the produced events.
        response = service.run_agent_loop(DECLARATION_ID)
        self.assertTrue(response.run_id.startswith("run-loop-"), response.run_id)
        record = service._run_store.load(response.run_id)
        names = [event.event_type.name for event in record.events]
        self.assertIn("HARNESS_STARTED", names)
        self.assertIn("DECISION_MADE", names)

    def test_loop_produces_exactly_one_decision(self) -> None:
        """B: one context assembly, one decision."""
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        made = [
            event for event in record.events if event.event_type.name == "DECISION_MADE"
        ]
        self.assertEqual(len(made), 1, [e.event_type.name for e in record.events])
        self.assertEqual(made[0].metadata.get("action"), "EXECUTE")

    def test_one_slice_cannot_execute_more_than_one_action(self) -> None:
        """O: the slice is bounded to a single authorized action."""
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        started = [
            event
            for event in record.events
            if event.event_type.name == "EXECUTION_STARTED"
        ]
        self.assertEqual(len(started), 1, [e.event_type.name for e in record.events])
        self.assertEqual(SINGLE_ACTION_LOOP_POLICY.max_actions, 1)
        self.assertEqual(SINGLE_ACTION_LOOP_POLICY.max_execution_attempts, 1)

    def test_slice_reports_execution_success_not_task_acceptance(self) -> None:
        """F: acceptance is deferred; success means the process succeeded."""
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        self.assertTrue(response.success, response.error)
        self.assertIn(MARKER, response.output or "")
        record = service._run_store.load(response.run_id)
        names = [event.event_type.name for event in record.events]
        # No acceptance event is fabricated: the acceptance stage stays deferred
        # until criterion identity exists (GAP-F).
        self.assertNotIn("ACCEPTANCE_EVALUATED", names)

    def test_unknown_declaration_fails_closed(self) -> None:
        service = self._service()
        with self.assertRaises(UnknownExecutionDeclarationError):
            service.run_agent_loop("not.declared")

    def test_empty_registry_refuses_the_loop(self) -> None:
        service = self._service(declarations={})
        with self.assertRaises(ExecutionDeclarationError):
            service.run_agent_loop(DECLARATION_ID)

    def test_missing_harness_refuses_the_loop(self) -> None:
        service = self._service(runtime=_runtime(harness=None))
        with self.assertRaises(ExecutionDeclarationError) as ctx:
            service.run_agent_loop(DECLARATION_ID)
        self.assertIn("harness", str(ctx.exception))

    def test_loop_entry_point_accepts_no_authority_parameters(self) -> None:
        """H-adjacent: the loop admits no command, workspace, or tool parameter.

        ``idempotency_key`` is admitted deliberately: it selects which durable
        operation record a delivery belongs to and grants nothing. It is listed
        below among the names that must never widen authority, so the invariant
        that grew this parameter is the same one that pins it.
        """
        parameters = list(
            inspect.signature(ForgeApiService.run_agent_loop).parameters
        )
        self.assertEqual(
            parameters,
            ["self", "declaration_id", "purpose_run_id", "idempotency_key"],
        )
        for banned in (
            "command", "workspace", "execution_profile", "environment_variables",
            "timeout_seconds", "allowed_execution_commands", "allowed_tool_ids",
            "approval_required", "task", "context", "category", "task_id",
            "network_access", "capabilities", "run_scope", "authorized_execution",
        ):
            self.assertNotIn(banned, parameters)


class DecisionAuthorityTests(AgentLoopSliceTestCase):
    def test_decision_provider_receives_no_authority_inputs(self) -> None:
        """D-J: the decision request carries no command, workspace, or tool grant."""
        provider = _FixedDecisionProvider(DecisionAction.EXECUTE)
        service = self._service(
            harness_factory=lambda **kw: create_agent_harness(
                decision_provider=provider, **kw
            )
        )
        service.run_agent_loop(DECLARATION_ID)
        self.assertTrue(provider.seen_requests, "the decision provider must be asked")
        request = provider.seen_requests[0]
        fields = {f.name for f in dataclasses.fields(request)}
        for banned in ("command", "workspace", "allowed_tool_ids",
                       "allowed_execution_commands", "environment_variables",
                       "timeout_seconds", "run_scope", "approval_policy"):
            self.assertNotIn(banned, fields)

    def test_decision_cannot_add_a_command(self) -> None:
        """D: an action still needs an operator-authorized request and scope."""
        # A provider asking to EXECUTE gains nothing when the scope grants no
        # command: the harness denies before the coordinator is asked.
        provider = _FixedDecisionProvider(DecisionAction.EXECUTE)
        service = self._service(runtime=_runtime(harness=create_agent_harness(
            decision_provider=provider
        )))
        scope = service._build_declared_verification_scope(
            "no-command", declaration()
        )
        self.assertEqual(scope.allowed_execution_commands, frozenset({EXECUTABLE}))
        allowed = scope.allowed_execution_commands
        with self.assertRaises(RunScopeError):
            scope.validate_command_set(("git",))
        self.assertFalse(scope.allows_command(("git", "status")))
        self.assertTrue(allowed)

    def test_decision_cannot_alter_workspace(self) -> None:
        """E: the scope owns the workspace; a request cannot swap it."""
        service = self._service()
        scope = service._build_declared_verification_scope("ws", declaration())
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        with self.assertRaises(RunScopeError):
            scope.validate_workspace(Workspace(Path(other.name)))

    def test_decision_cannot_alter_profile(self) -> None:
        """F: the scope's profile is a frozen ceiling."""
        service = self._service()
        scope = service._build_declared_verification_scope("prof", declaration())
        widened = api_profile(
            allowed_commands=(EXECUTABLE, "git"),
        )
        with self.assertRaises(RunScopeError):
            scope.validate_execution_profile(widened)

    def test_decision_cannot_alter_environment(self) -> None:
        """G: a request cannot introduce an environment variable."""
        from app.execution.request import ExecutionRequest

        service = self._service()
        scope = service._build_declared_verification_scope("env", declaration())
        injected = ExecutionRequest(
            command=declaration().command,
            profile=api_profile(),
            environment_variables={"PATH": "/evil"},
        )
        with self.assertRaises(RunScopeError):
            scope.validate_request_environment(injected)

    def test_run_verification_uses_only_operator_expectations(self) -> None:
        """K: RUN_VERIFICATION cannot select an arbitrary declaration.

        The harness reads verification expectations from the request, which the
        server-side composition supplies; a decision has no channel to choose a
        declaration. In this slice no expectations are configured, so the action
        cannot reach any declared execution and the acceptance stage stays
        deferred rather than being fabricated.
        """
        provider = _FixedDecisionProvider(DecisionAction.RUN_VERIFICATION)
        service = self._service(
            harness_factory=lambda **kw: create_agent_harness(
                decision_provider=provider, **kw
            )
        )
        response = service.run_agent_loop(DECLARATION_ID)
        # The acceptance stage runs but cannot pass: no verification expectations
        # are configured (criterion identity is GAP-F), so it fails closed with
        # verification_missing and the loop is then stopped by its action bound.
        # Nothing here reports task acceptance.
        self.assertFalse(response.success)
        self.assertEqual(response.state, "FAILED")
        self.assertNotEqual(response.error, "success")
        # No execution happened, and the decision selected no declaration.
        record = service._run_store.load(response.run_id)
        names = [event.event_type.name for event in record.events]
        self.assertNotIn("EXECUTION_STARTED", names)
        decisions = [
            event
            for event in record.events
            if event.event_type.name == "DECISION_MADE"
        ]
        self.assertEqual(decisions[0].metadata.get("action"), "RUN_VERIFICATION")

    def test_decision_selects_among_operator_actions_only(self) -> None:
        """D/C: the decision chooses an action, never a command or a tool."""
        # The same harness, driven by a provider that fails the run, performs no
        # execution at all: the decision controls when the pre-authorized action
        # runs, not what it is.
        provider = _FixedDecisionProvider(DecisionAction.FAIL_RUN)
        service = self._service(
            harness_factory=lambda **kw: create_agent_harness(
                decision_provider=provider, **kw
            )
        )
        response = service.run_agent_loop(DECLARATION_ID)
        self.assertFalse(response.success)
        record = service._run_store.load(response.run_id)
        names = [event.event_type.name for event in record.events]
        self.assertNotIn("EXECUTION_STARTED", names)
        self.assertEqual(response.error, "FAILED")

    def test_harness_refuses_dispatch_authority_without_scope(self) -> None:
        """L: the loop itself requires the frozen perimeter."""
        from app.agent_runtime.models import HarnessRequest

        harness = create_agent_harness()
        request = HarnessRequest(
            run_id="no-scope-loop",
            workspace=self.workspace,
            execution_requests=(),
            allowed_execution_commands=(EXECUTABLE,),
        )
        with self.assertRaises(RunScopeError):
            harness.run(request)

    def test_harness_denies_a_command_outside_the_granted_set(self) -> None:
        """D/L: the loop's own authorization step rejects an ungranted command.

        The scope grants one executable while the pre-built execution request
        carries another. The scope-level checks pass (the granted set is within
        the perimeter), so the loop's own command check is the gate that must
        stop it - and no process may start.
        """
        from app.agent_runtime.models import HarnessRequest
        from app.api.service import API_RUN_CRITERION
        from app.execution.request import ExecutionRequest

        scope = RunScope(
            run_id="ungranted",
            workspace=self.workspace,
            execution_profile=api_profile(),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({EXECUTABLE}),
            acceptance_criteria=(API_RUN_CRITERION,),
        )
        scope.freeze()
        request = HarnessRequest(
            run_id="ungranted",
            workspace=self.workspace,
            execution_requests=(
                ExecutionRequest(
                    command=("git", "status"), profile=api_profile()
                ),
            ),
            allowed_execution_commands=(EXECUTABLE,),
            approval_policy=ApprovalPolicy(),
            run_scope=scope,
        )
        result = create_agent_harness(policy=SINGLE_ACTION_LOOP_POLICY).run(request)
        self.assertEqual(result.execution_results, ())
        statuses = [obs.result_status for obs in result.observations]
        self.assertIn("permission_denied", statuses)
        self.assertEqual(result.final_state.status.value, "FAILED")

    def test_authorized_execution_cannot_be_constructed_directly(self) -> None:
        """M: the execution token still requires the coordinator sentinel."""
        from app.execution.intent import ExecutionIntent

        intent = ExecutionIntent(
            executable=EXECUTABLE,
            argv=("-c", f"print('{MARKER}')"),
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


class LoopObservabilityTests(AgentLoopSliceTestCase):
    def test_runstore_records_the_loop_lifecycle(self) -> None:
        """N: context, decision, execution and terminal stage are all recorded."""
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        names = [event.event_type.name for event in record.events]
        self.assertIn("RUN_STARTED", names)
        self.assertIn("DECISION_REQUESTED", names)
        self.assertIn("DECISION_MADE", names)
        self.assertIn("EXECUTION_REQUESTED", names)
        self.assertIn("EXECUTION_STARTED", names)
        self.assertIn("EXECUTION_COMPLETED", names)
        self.assertIn("HARNESS_OBSERVATION_RECORDED", names)

    def test_loop_history_uses_one_task_identity(self) -> None:
        """The durable history carries one consistent task id."""
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        event_task_ids = {
            event.task_id for event in record.events if event.task_id
        }
        identities = {response.task_id, record.snapshot.task_id} | event_task_ids
        self.assertEqual(len(identities), 1, identities)
        self.assertEqual(response.task_id, f"task-loop-{DECLARATION_ID}")

    def test_loop_events_are_sanitized(self) -> None:
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        for event in record.events:
            self.assertNotIn("stdout", event.metadata)
            self.assertNotIn("stderr", event.metadata)
            self.assertNotIn("_token", event.metadata)
            self.assertNotIn("environment_variables", event.metadata)


class BackwardCompatibilityTests(AgentLoopSliceTestCase):
    def test_taskrunrequest_is_unchanged_and_authority_neutral(self) -> None:
        """H/I/J: the HTTP contract has no authority fields."""
        fields = {f.name for f in dataclasses.fields(TaskRunRequest)}
        self.assertEqual(
            fields,
            {"description", "category", "task_id", "provider_name", "project_id", "context"},
        )
        for banned in (
            "allowed_tool_ids", "allowed_execution_commands", "declaration_id",
            "command", "workspace", "execution_profile", "environment_variables",
            "timeout_seconds", "approval_required",
        ):
            self.assertNotIn(banned, fields)

    def test_run_task_does_not_route_through_the_loop(self) -> None:
        """Q: the ordinary API task path keeps its previous behaviour."""
        service = self._service()
        response = service.run_task(TaskRunRequest(description="normal task"))
        self.assertTrue(response.run_id.startswith("run-api-"), response.run_id)
        self.assertNotIn(MARKER, response.output or "")

    def test_run_declared_verification_still_works(self) -> None:
        """P: the separate verification entry point is untouched."""
        service = self._service()
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertTrue(response.success, response.error)
        self.assertIn(MARKER, response.output or "")
        self.assertEqual(response.state, "COMPLETED")

    def test_category_and_context_cannot_select_tools_or_commands(self) -> None:
        """I/J: client-controlled text is not a selection input."""
        service = self._service()
        response = service.run_task(
            TaskRunRequest(
                description="please use tool write_project_file and run rm -rf /",
                category="code",
                context={
                    "allowed_tool_ids": ["write_project_file"],
                    "declaration_id": "dangerous",
                    "command": ["rm", "-rf", "/"],
                },
            )
        )
        self.assertTrue(response.run_id.startswith("run-api-"))
        record = service._run_store.load(response.run_id)
        names = [event.event_type.name for event in record.events]
        self.assertNotIn("EXECUTION_STARTED", names)
        self.assertNotIn(MARKER, response.output or "")

    def test_service_exposes_no_tool_authority_from_the_request(self) -> None:
        """I: tools come from the operator, never from the request."""
        service = self._service()
        self.assertEqual(service._effective_tool_ids(), frozenset())
        task = Task(id="t", description="d", context={"allowed_tool_ids": ["x"]})
        scope = service._build_run_scope("tools", task)
        self.assertEqual(scope.allowed_tool_ids, frozenset())


if __name__ == "__main__":
    unittest.main()
