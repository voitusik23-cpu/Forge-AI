"""Production tool execution through AgentHarness.

These tests pin the tool boundary:

* a tool action is authorized server-side from the run's frozen
  ``allowed_tool_ids``; a decision selects an index and never supplies a tool
  identity, an argument, or any authority;
* a ``ToolIntent`` is data - a tool identity plus bounded arguments - and it is
  refused outright when it names execution authority;
* the existing production ``ToolExecutor`` owns the permission check, the
  approval flow, and the tool call; the harness only composes its input;
* results are bounded before they reach the run's event trail;
* there is no direct execution path, no second orchestrator, and no bypass of
  ``ExecutionCoordinator``.

Real bounded behaviour runs against real temporary workspaces; no tracked
repository file is mutated.
"""

from __future__ import annotations

from pathlib import Path
import dataclasses
import json
import tempfile
import unittest
from uuid import uuid4

from app.agent_runtime.harness import AgentHarness
from app.agent_runtime.models import HarnessRequest
from app.agent_runtime.policy import AgentHarnessPolicy
from app.agent_runtime.tool_execution import (
    FORBIDDEN_ARGUMENT_KEYS,
    AuthorizedToolCall,
    ToolAuthorizationError,
    ToolIntent,
    authorize_tool_intent,
)
from app.decision.models import Decision, DecisionAction, DecisionRequest, DecisionType
from app.decision.validator import validate_decision
from app.execution.capabilities import ExecutionCapability
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.models import EventType
from app.projects.state import ProjectState, ProjectStateStatus
from app.runtime.run_scope import RunScope, RunScopeError
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalResolution, ApprovalState
from app.tools.bounded import (
    MAX_ERROR_CHARS,
    MAX_METADATA_ITEMS,
    MAX_PREVIEW_CHARS,
    BoundedResultError,
    BoundedToolResult,
    bound_tool_result,
)
from app.tools.contracts import ToolResult, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.registry import build_default_tool_registry
from app.tools.workspace import Workspace

RUN_ID = "run-tool-1"
TASK_ID = "task-tool-1"
WRITE_TOOL = "write_project_file"
READ_TOOL = "read_project_file"

PROFILE = ProjectExecutionProfile(
    profile_id="p",
    allowed_commands=("python",),
    capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
    network_access=False,
    working_directory=".",
)


class _ApproveAll:
    """Resolves every approval for the exact bound payload."""

    def resolve(self, request):
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=request.intent_fingerprint,
        )


class _RejectAll:
    def resolve(self, request):
        return ApprovalResolution(
            decision=ApprovalState.REJECTED,
            approved_fingerprint=request.intent_fingerprint,
        )


class _ToolThenFail:
    """Selects the tool action once when offered, then fails."""

    def __init__(self) -> None:
        self.requests: list[DecisionRequest] = []

    def decide(self, request: DecisionRequest) -> Decision:
        self.requests.append(request)
        offered = DecisionAction.INVOKE_TOOL in tuple(request.available_actions)
        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            decision_type=(
                DecisionType.INVOKE_TOOL if offered else DecisionType.FAIL
            ),
            action=(
                DecisionAction.INVOKE_TOOL if offered else DecisionAction.FAIL_RUN
            ),
            reason_code="tool_probe",
            attempt_number=request.attempt_number,
        )


class _AlwaysInvoke:
    """Keeps asking for the tool action, to prove the loop stays bounded."""

    def decide(self, request: DecisionRequest) -> Decision:
        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            decision_type=DecisionType.INVOKE_TOOL,
            action=DecisionAction.INVOKE_TOOL,
            reason_code="always_invoke",
            attempt_number=request.attempt_number,
        )


class ToolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.workspace = Workspace(self.root)
        self.registry = build_default_tool_registry(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def executor(self, resolver=None) -> ToolExecutor:
        return ToolExecutor(self.registry, approval_resolver=resolver or _ApproveAll())

    def scope(
        self,
        run_id: str = RUN_ID,
        tool_ids: frozenset[str] = frozenset({WRITE_TOOL}),
        workspace: Workspace | None = None,
    ) -> RunScope:
        scope = RunScope(
            run_id=run_id,
            workspace=workspace or self.workspace,
            execution_profile=PROFILE,
            allowed_tool_ids=tool_ids,
            allowed_execution_commands=frozenset(),
            acceptance_criteria=(
                AcceptanceCriterion(criterion_id="c1", description="d"),
            ),
        )
        scope.freeze()
        return scope

    def request(
        self,
        *,
        run_id: str = RUN_ID,
        task_id: str = TASK_ID,
        intents=(),
        scope: RunScope | None = None,
        workspace: Workspace | None = None,
    ) -> HarnessRequest:
        scope = scope or self.scope(run_id)
        return HarnessRequest(
            run_id=run_id,
            workspace=workspace or self.workspace,
            tool_requests=tuple(intents),
            metadata={"task_id": task_id},
            initial_project_state=ProjectState(
                run_id=run_id,
                attempt_number=0,
                status=ProjectStateStatus.INITIAL,
            ),
            run_scope=scope,
        )

    def run_harness(
        self,
        request: HarnessRequest,
        *,
        provider=None,
        executor=None,
        policy=None,
    ):
        return AgentHarness(
            policy=policy or AgentHarnessPolicy(max_iterations=3, max_actions=2),
            decision_provider=provider or _ToolThenFail(),
            tool_executor=executor or self.executor(),
        ).run(request)

    @staticmethod
    def _events(result, name: str):
        return [
            dict(event.metadata)
            for event in result.events
            if event.event_type.name == name
        ]


# --------------------------------------------------------------------------- #
# 1-2. Allowed tool executes; unknown tool is denied
# --------------------------------------------------------------------------- #


class AllowedToolExecutionTests(ToolTestCase):
    def test_allowed_tool_executes_and_writes_the_file(self) -> None:
        result = self.run_harness(
            self.request(
                intents=(
                    ToolIntent(WRITE_TOOL, {"relative_path": "out.txt", "content": "hi"}),
                )
            )
        )
        self.assertTrue((self.root / "out.txt").exists())
        names = [event.event_type.name for event in result.events]
        self.assertIn("TOOL_INVOCATION_REQUESTED", names)
        self.assertIn("PERMISSION_CHECKED", names)
        self.assertIn("TOOL_EXECUTION_COMPLETED", names)
        self.assertIn("TOOL_RESULT_BOUNDED", names)

        checked = self._events(result, "PERMISSION_CHECKED")
        self.assertEqual(checked[0]["decision"], "ALLOW")
        self.assertEqual(checked[0]["reason"], "ALLOWED")

    def test_bounded_result_is_recorded_once(self) -> None:
        result = self.run_harness(
            self.request(
                intents=(ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"}),)
            )
        )
        bounded = self._events(result, "TOOL_RESULT_BOUNDED")
        self.assertEqual(len(bounded), 1)
        self.assertEqual(bounded[0]["status"], ToolStatus.COMPLETED.value)
        self.assertEqual(bounded[0]["tool_id"], WRITE_TOOL)
        self.assertEqual(bounded[0]["metadata"]["bytes_written"], 1)

    def test_unknown_tool_is_denied_by_the_registry(self) -> None:
        # The scope authorizes an id, but nothing is registered under it.
        result = self.run_harness(
            self.request(
                intents=(ToolIntent("ghost_tool", {"relative_path": "a.txt"}),),
                scope=self.scope(tool_ids=frozenset({"ghost_tool"})),
            )
        )
        denied = self._events(result, "TOOL_INVOCATION_DENIED")
        self.assertTrue(denied)
        self.assertEqual(denied[0]["reason"], "UNKNOWN_TOOL")
        self.assertEqual(result.final_state.status.value, "FAILED")

    def test_tool_not_in_trusted_allowed_ids_is_denied(self) -> None:
        intent = ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"})
        with self.assertRaises(ToolAuthorizationError):
            authorize_tool_intent(
                intent,
                run_id=RUN_ID,
                task_id=TASK_ID,
                allowed_tool_ids=frozenset(),
                context_fingerprint="fp",
            )
        self.assertFalse((self.root / "a.txt").exists())

    def test_declaring_an_intent_outside_the_perimeter_is_rejected(self) -> None:
        """A request cannot even declare an intent for an unauthorized tool."""
        request = self.request(
            intents=(ToolIntent(READ_TOOL, {"relative_path": "x"}),),
            scope=self.scope(tool_ids=frozenset({WRITE_TOOL})),
        )
        with self.assertRaises(RunScopeError):
            AgentHarness(
                policy=AgentHarnessPolicy(max_iterations=2, max_actions=2),
                tool_executor=self.executor(),
            ).run(request)


# --------------------------------------------------------------------------- #
# 3-5. Authority cannot be injected or widened
# --------------------------------------------------------------------------- #


class ToolAuthorityTests(ToolTestCase):
    def test_intent_refuses_authority_shaped_arguments(self) -> None:
        for key in ("command", "argv", "executable", "shell", "environment",
                    "timeout", "workspace", "run_scope", "approval_policy",
                    "allowed_tool_ids", "capabilities", "network_access",
                    "credentials", "token", "password", "api_key", "cwd"):
            with self.assertRaises(ToolAuthorizationError, msg=key):
                ToolIntent(WRITE_TOOL, {key: "x"})

    def test_forbidden_keys_cover_the_authority_surface(self) -> None:
        for key in ("command", "argv", "executable", "shell", "environment",
                    "timeout", "run_scope", "authorized_execution",
                    "approval_resolver", "allowed_execution_commands",
                    "allowed_tool_ids", "capabilities", "workspace",
                    "network_access", "credentials", "token"):
            self.assertIn(key, FORBIDDEN_ARGUMENT_KEYS, key)

    def test_intent_has_no_authority_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(ToolIntent)}
        self.assertEqual(fields, {"tool_id", "arguments"})
        call_fields = {f.name for f in dataclasses.fields(AuthorizedToolCall)}
        for banned in ("command", "argv", "executable", "shell", "environment",
                       "timeout", "capabilities", "allowed_tool_ids",
                       "run_scope", "authorization", "approval"):
            self.assertNotIn(banned, call_fields, banned)

    def test_intent_cannot_widen_the_run_scope(self) -> None:
        """Authorizing against a scope grants exactly the scope's tool set."""
        intent = ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"})
        call = authorize_tool_intent(
            intent,
            run_id=RUN_ID,
            task_id=TASK_ID,
            allowed_tool_ids=frozenset({WRITE_TOOL}),
            context_fingerprint="fp",
        )
        self.assertEqual(call.context.allowed_tool_ids, frozenset({WRITE_TOOL}))
        # The context carries the run's perimeter, not anything from the intent.
        self.assertEqual(call.context.run_id, RUN_ID)
        self.assertNotIn(READ_TOOL, call.context.allowed_tool_ids)

    def test_oversized_and_malformed_arguments_are_rejected(self) -> None:
        with self.assertRaises(ToolAuthorizationError):
            ToolIntent(WRITE_TOOL, {f"k{i}": "v" for i in range(40)})
        with self.assertRaises(ToolAuthorizationError):
            ToolIntent(WRITE_TOOL, {"k" * 200: "v"})
        with self.assertRaises(ToolAuthorizationError):
            ToolIntent(WRITE_TOOL, {"content": "x" * 100_000})
        with self.assertRaises(ToolAuthorizationError):
            ToolIntent(WRITE_TOOL, {"content": b"bytes"})
        with self.assertRaises(ToolAuthorizationError):
            ToolIntent("", {})
        with self.assertRaises(ToolAuthorizationError):
            ToolIntent(WRITE_TOOL, "not-a-mapping")

    def test_malformed_arguments_never_reach_the_tool(self) -> None:
        """A bad argument shape is denied by the registry's own validation."""
        result = self.run_harness(
            self.request(intents=(ToolIntent(WRITE_TOOL, {"nonsense": 1}),))
        )
        denied = self._events(result, "TOOL_INVOCATION_DENIED")
        self.assertTrue(denied)
        self.assertEqual(denied[0]["reason"], "INVALID_INVOCATION")

    def test_fresh_invocation_identity_per_authorization(self) -> None:
        intent = ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"})
        first = authorize_tool_intent(
            intent, run_id=RUN_ID, task_id=TASK_ID,
            allowed_tool_ids=frozenset({WRITE_TOOL}), context_fingerprint="fp",
        )
        second = authorize_tool_intent(
            intent, run_id=RUN_ID, task_id=TASK_ID,
            allowed_tool_ids=frozenset({WRITE_TOOL}), context_fingerprint="fp",
        )
        self.assertNotEqual(
            first.invocation.invocation_id, second.invocation.invocation_id
        )

    def test_cross_run_tool_call_is_rejected(self) -> None:
        intent = ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"})
        call = authorize_tool_intent(
            intent, run_id=RUN_ID, task_id=TASK_ID,
            allowed_tool_ids=frozenset({WRITE_TOOL}), context_fingerprint="fp",
        )
        call.assert_belongs_to(RUN_ID, TASK_ID)
        with self.assertRaises(ToolAuthorizationError):
            call.assert_belongs_to("other-run", TASK_ID)
        with self.assertRaises(ToolAuthorizationError):
            call.assert_belongs_to(RUN_ID, "other-task")

    def test_cross_run_scope_cannot_authorize_another_runs_tool(self) -> None:
        other_scope = self.scope(run_id="run-other", tool_ids=frozenset({WRITE_TOOL}))
        request = self.request(
            run_id=RUN_ID,
            intents=(ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"}),),
            scope=other_scope,
        )
        with self.assertRaises(RunScopeError):
            AgentHarness(
                policy=AgentHarnessPolicy(max_iterations=2, max_actions=2),
                tool_executor=self.executor(),
            ).run(request)


# --------------------------------------------------------------------------- #
# 6-8. Result boundaries and failure shape
# --------------------------------------------------------------------------- #


class BoundedResultTests(ToolTestCase):
    def test_large_output_is_capped_and_hashed(self) -> None:
        bounded = bound_tool_result(
            ToolResult("i1", ToolStatus.COMPLETED, output="x" * 5000),
            tool_id=WRITE_TOOL,
        )
        self.assertEqual(len(bounded.preview), MAX_PREVIEW_CHARS)
        self.assertEqual(bounded.output_bytes, 5000)
        self.assertTrue(bounded.truncated)
        self.assertEqual(len(bounded.output_sha256), 64)

    def test_result_metadata_is_allowlisted(self) -> None:
        bounded = bound_tool_result(
            ToolResult(
                "i1",
                ToolStatus.COMPLETED,
                output="ok",
                metadata={
                    "relative_path": "a/b.txt",
                    "bytes_written": 2,
                    "content_sha256": "a" * 64,
                    # These must never reach history.
                    "stdout": "raw",
                    "stderr": "raw",
                    "environment": {"SECRET": "x"},
                    "token": "secret",
                },
            ),
            tool_id=WRITE_TOOL,
        )
        self.assertEqual(
            sorted(bounded.metadata), ["bytes_written", "content_sha256", "relative_path"]
        )

    def test_error_is_truncated(self) -> None:
        bounded = bound_tool_result(
            ToolResult("i1", ToolStatus.FAILED, error="e" * 5000), tool_id=WRITE_TOOL
        )
        self.assertEqual(len(bounded.error), MAX_ERROR_CHARS)

    def test_malformed_result_becomes_an_explicit_failure(self) -> None:
        bounded = bound_tool_result(object(), tool_id=WRITE_TOOL)
        self.assertEqual(bounded.status, ToolStatus.FAILED)
        self.assertEqual(bounded.error, "malformed tool result")

    def test_bounded_result_is_immutable_and_bounded(self) -> None:
        bounded = bound_tool_result(
            ToolResult("i1", ToolStatus.COMPLETED, output="ok"), tool_id=WRITE_TOOL
        )
        self.assertTrue(BoundedToolResult.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            bounded.preview = "x"
        with self.assertRaises(BoundedResultError):
            BoundedToolResult("i", WRITE_TOOL, ToolStatus.COMPLETED, preview="x" * 9999)

    def test_result_has_no_authority_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(BoundedToolResult)}
        for banned in ("command", "argv", "executable", "shell", "environment",
                       "timeout", "workspace", "run_scope", "capabilities"):
            self.assertNotIn(banned, fields, banned)

    def test_tool_failure_produces_a_bounded_failure_event(self) -> None:
        # Writing without the overwrite flag into an existing file is a failure.
        (self.root / "exists.txt").write_text("old", encoding="utf-8")
        result = self.run_harness(
            self.request(
                intents=(
                    ToolIntent(WRITE_TOOL, {"relative_path": "exists.txt", "content": "new"}),
                )
            )
        )
        bounded = self._events(result, "TOOL_RESULT_BOUNDED")
        self.assertEqual(bounded[0]["status"], ToolStatus.DENIED.value)
        self.assertEqual(bounded[0]["error"], "target already exists; set overwrite=true to replace it")
        self.assertEqual(result.final_state.status.value, "FAILED")

    def test_event_trail_carries_no_raw_output(self) -> None:
        marker = "CANARY_TOOL_OUTPUT_9f31"
        result = self.run_harness(
            self.request(
                intents=(
                    ToolIntent(WRITE_TOOL, {"relative_path": "c.txt", "content": marker}),
                )
            )
        )
        blob = json.dumps(
            [dict(event.metadata) for event in result.events], sort_keys=True
        )
        # The content is a bounded preview at most; it is never raw stdout, and no
        # environment, credential, or unlimited output field exists.
        for banned in ("stdout", "stderr", "environment", "token", "password",
                       "secret", "argv", "command"):
            self.assertNotIn(banned, blob.lower(), banned)
        bounded = self._events(result, "TOOL_RESULT_BOUNDED")
        self.assertLessEqual(len(str(bounded[0].get("preview", ""))), MAX_PREVIEW_CHARS)


# --------------------------------------------------------------------------- #
# 9-11. Denial is terminal; revision cannot bypass authorization
# --------------------------------------------------------------------------- #


class ToolDenialTests(ToolTestCase):
    def test_approval_rejection_is_terminal(self) -> None:
        result = self.run_harness(
            self.request(
                intents=(ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"}),)
            ),
            executor=self.executor(_RejectAll()),
        )
        bounded = self._events(result, "TOOL_RESULT_BOUNDED")
        self.assertEqual(bounded[0]["status"], ToolStatus.DENIED.value)
        self.assertEqual(bounded[0]["error"], "approval rejected")
        self.assertEqual(result.final_state.status.value, "FAILED")
        self.assertTrue(result.final_state.terminal)
        self.assertFalse((self.root / "a.txt").exists())

    def test_denied_tool_ends_the_run_and_never_revises(self) -> None:
        result = self.run_harness(
            self.request(
                intents=(ToolIntent("ghost_tool", {"relative_path": "a"}),),
                scope=self.scope(tool_ids=frozenset({"ghost_tool"})),
            ),
            provider=_AlwaysInvoke(),
            executor=self.executor(),
        )
        # The scope authorizes the id but nothing is registered under it, so the
        # executor's permission check denies it. A denial is terminal: the run ends
        # and no revision is ever requested, however insistently the provider asks.
        denied = self._events(result, "TOOL_INVOCATION_DENIED")
        self.assertTrue(denied)
        self.assertEqual(denied[0]["reason"], "UNKNOWN_TOOL")
        self.assertEqual(result.final_state.status.value, "FAILED")
        self.assertTrue(result.final_state.terminal)
        self.assertEqual(self._events(result, "REVISION_STARTED"), [])
        self.assertIn(
            "tool_denied",
            {
                entry.get("reason")
                for entry in self._events(result, "HARNESS_FAILED")
            },
        )

    def test_tool_action_is_not_offered_without_authorization(self) -> None:
        provider = _ToolThenFail()
        self.run_harness(
            self.request(),
            provider=provider,
            executor=self.executor(),
        )
        self.assertTrue(provider.requests)
        for request in provider.requests:
            self.assertNotIn(DecisionAction.INVOKE_TOOL, tuple(request.available_actions))

    def test_tool_action_is_offered_only_once_per_run(self) -> None:
        provider = _AlwaysInvoke()
        result = self.run_harness(
            self.request(
                intents=(ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"}),)
            ),
            provider=provider,
            executor=self.executor(),
        )
        # The loop stops even though the provider keeps asking: only one tool
        # invocation is recorded, and the second attempt is refused as a bounded
        # tool-limit denial that ends the run.
        self.assertTrue(result.final_state.terminal)
        bounded = self._events(result, "TOOL_RESULT_BOUNDED")
        self.assertEqual(len(bounded), 1)
        self.assertEqual(result.final_state.status.value, "FAILED")
        self.assertIn(
            "tool_limit_reached",
            {
                entry.get("reason")
                for entry in self._events(result, "HARNESS_FAILED")
            },
        )

    def test_no_tool_executor_means_no_tool_action(self) -> None:
        provider = _ToolThenFail()
        result = AgentHarness(
            policy=AgentHarnessPolicy(max_iterations=3, max_actions=2),
            decision_provider=provider,
        ).run(
            self.request(
                intents=(ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"}),)
            )
        )
        self.assertFalse((self.root / "a.txt").exists())
        self.assertEqual(self._events(result, "TOOL_RESULT_BOUNDED"), [])

    def test_empty_scope_tool_set_offers_no_tool_action(self) -> None:
        provider = _ToolThenFail()
        self.run_harness(
            self.request(
                intents=(),
                scope=self.scope(tool_ids=frozenset()),
            ),
            provider=provider,
            executor=self.executor(),
        )
        for request in provider.requests:
            self.assertNotIn(DecisionAction.INVOKE_TOOL, tuple(request.available_actions))


# --------------------------------------------------------------------------- #
# 12. Decision binding
# --------------------------------------------------------------------------- #


class ToolDecisionTests(ToolTestCase):
    def test_invoke_tool_is_a_distinct_decision(self) -> None:
        self.assertEqual(DecisionAction.INVOKE_TOOL.value, "INVOKE_TOOL")
        self.assertEqual(DecisionType.INVOKE_TOOL.value, "INVOKE_TOOL")
        request = DecisionRequest(run_id=RUN_ID)
        decision = Decision(
            decision_id="d", run_id=RUN_ID,
            decision_type=DecisionType.INVOKE_TOOL,
            action=DecisionAction.INVOKE_TOOL,
            reason_code="tool",
        )
        self.assertTrue(validate_decision(decision, request).valid)

    def test_tool_decision_cannot_claim_to_execute(self) -> None:
        request = DecisionRequest(run_id=RUN_ID)
        decision = Decision(
            decision_id="d", run_id=RUN_ID,
            decision_type=DecisionType.INVOKE_TOOL,
            action=DecisionAction.EXECUTE,
            reason_code="tool",
        )
        report = validate_decision(decision, request)
        self.assertFalse(report.valid)
        self.assertIn("incompatible_action:INVOKE_TOOL->EXECUTE", report.errors)

    def test_execute_decision_cannot_claim_to_invoke_a_tool(self) -> None:
        request = DecisionRequest(run_id=RUN_ID)
        decision = Decision(
            decision_id="d", run_id=RUN_ID,
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.INVOKE_TOOL,
            reason_code="tool",
        )
        self.assertFalse(validate_decision(decision, request).valid)


# --------------------------------------------------------------------------- #
# 13-14. No bypass: no direct execution, single orchestrator
# --------------------------------------------------------------------------- #


class ToolIsolationTests(ToolTestCase):
    def test_new_tool_modules_do_not_execute_directly(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        for relative in (
            "app/tools/bounded.py",
            "app/agent_runtime/tool_execution.py",
        ):
            source = (repo / relative).read_text(encoding="utf-8")
            for banned in (
                "subprocess", "Popen", "os.system", "shell=True",
                "LocalExecutionAdapter", "ExecutionCoordinator",
                "AuthorizedExecution", "os.environ",
            ):
                self.assertNotIn(banned, source, f"{relative}: {banned}")

    def test_harness_owns_no_direct_tool_execution(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        source = (repo / "app/agent_runtime/harness.py").read_text(encoding="utf-8")
        for banned in ("subprocess", "Popen", "os.system", "shell=True"):
            self.assertNotIn(banned, source, banned)
        # The tool call goes through the existing executor, never a raw call.
        self.assertIn("self._tool_executor.execute(", source)

    def test_single_orchestrator_is_preserved(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        source = (repo / "app/agent_runtime/harness.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("class AgentHarness"), 1)
        self.assertEqual(source.count("self.run("), 0)

    def test_tool_action_reuses_the_executor(self) -> None:
        """The invocation reaches the executor, which owns permission + approval."""
        calls: list[str] = []

        class RecordingExecutor(ToolExecutor):
            def execute(self, invocation, *, context=None, observer):
                calls.append(invocation.tool_id)
                return super().execute(invocation, context=context, observer=observer)

        result = self.run_harness(
            self.request(
                intents=(ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": "x"}),)
            ),
            executor=RecordingExecutor(self.registry, approval_resolver=_ApproveAll()),
        )
        self.assertEqual(calls, [WRITE_TOOL])
        self.assertTrue((self.root / "a.txt").exists())


# --------------------------------------------------------------------------- #
# Compatibility
# --------------------------------------------------------------------------- #


class ToolCompatibilityTests(ToolTestCase):
    def test_harness_without_tool_requests_is_unchanged(self) -> None:
        result = self.run_harness(self.request())
        self.assertEqual(self._events(result, "TOOL_RESULT_BOUNDED"), [])
        names = [event.event_type.name for event in result.events]
        self.assertNotIn("TOOL_INVOCATION_REQUESTED", names)

    def test_harness_request_defaults_to_no_tool_requests(self) -> None:
        self.assertEqual(HarnessRequest(run_id="r").tool_requests, ())

    def test_tool_result_bounded_event_is_registered(self) -> None:
        self.assertEqual(EventType.TOOL_RESULT_BOUNDED.value, "tool_result_bounded")

    def test_service_defaults_to_no_tool_intents(self) -> None:
        import inspect

        from app.api.service import ForgeApiService

        signature = inspect.signature(ForgeApiService.__init__)
        self.assertIn("tool_intents", signature.parameters)
        self.assertEqual(signature.parameters["tool_intents"].default, ())

    def test_intent_summary_exposes_shape_not_values(self) -> None:
        marker = "SECRET_VALUE_4b21"
        intent = ToolIntent(WRITE_TOOL, {"relative_path": "a.txt", "content": marker})
        summary = json.dumps(intent.bounded_summary(), sort_keys=True)
        self.assertNotIn(marker, summary)
        self.assertIn("content", summary)

    def test_default_service_posture_reaches_no_tool(self) -> None:
        """With no declared intents the frozen tool perimeter stays empty."""
        from app.api.service import ForgeApiService
        from app.execution.declaration import ExecutionDeclaration
        from app.orchestrator.models import TaskResult
        from app.orchestrator.run import RunExecutor
        from app.runtime.bootstrap import create_agent_harness
        from app.runtime.context import RuntimeContext
        from app.tools.approval import ApprovalPolicy

        class _Orchestrator:
            def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
                return TaskResult(task.id, True, output="d")

        def _runtime():
            orchestrator = _Orchestrator()
            return RuntimeContext(
                settings=None, provider_accounts={}, provider_registry=None,
                provider_capabilities=None, agent_registry=None,
                orchestrator=orchestrator,
                run_executor=RunExecutor(orchestrator),
                harness=create_agent_harness(
                    with_discovery=False, with_planning=False
                ),
            )

        service = ForgeApiService(
            runtime=_runtime(),
            workspace=self.workspace,
            tool_registry=self.registry,
            execution_profile=PROFILE,
            # The operator allow-lists a tool but declares no intent for it.
            allowed_tool_ids=frozenset({WRITE_TOOL}),
            declarations={
                "verify.p": ExecutionDeclaration(
                    declaration_id="verify.p",
                    command=("python", "-c", "print('ok')"),
                    profile_id="p",
                    working_directory=".",
                )
            },
            approval_policy=ApprovalPolicy(),
            harness_factory=lambda **kw: create_agent_harness(
                **{**kw, "decision_provider": _ToolThenFail()}
            ),
        )
        response = service.run_agent_loop("verify.p")
        record = service._run_store.load(response.run_id)
        tool_events = [
            event.event_type.name
            for event in record.events
            if "TOOL" in event.event_type.name
        ]
        self.assertEqual(tool_events, [])
        scope = service._build_declared_verification_scope(
            response.run_id,
            service._declarations["verify.p"],
        )
        self.assertEqual(scope.allowed_tool_ids, frozenset())


if __name__ == "__main__":
    unittest.main()
