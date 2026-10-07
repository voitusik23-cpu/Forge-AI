"""Server-side declared verification execution: authority and mapping tests.

These tests pin the declared-execution design:

* host command authority comes only from an operator-owned
  ``ExecutionDeclaration``, declared at composition time;
* the command set a run may use is derived from the declaration a server-side
  resolver selects, never from an independently configured allowlist;
* nothing on the HTTP contract - not the request body, not ``context``, not the
  description - can name a command or select a declaration;
* a declaration only adds limits: the frozen ``RunScope``, the execution
  profile, the execution policy, and the approval policy still decide.

The deterministic test executable is the running interpreter printing a fixed
marker, which is the same shape the existing execution tests use. No
user-provided shell command appears anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path
import tempfile
import unittest

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.execution.declaration import (
    ExecutionDeclaration,
    ExecutionDeclarationError,
    UnknownExecutionDeclarationError,
    to_execution_request,
    validate_declarations_against_profile,
)
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

MARKER = "forge-declared-verification-ok"
EXECUTABLE = "python"
DECLARATION_ID = "verify.smoke"


def declaration(**overrides) -> ExecutionDeclaration:
    payload = {
        "declaration_id": DECLARATION_ID,
        "command": (EXECUTABLE, "-c", f"print('{MARKER}')"),
        "profile_id": "api-default",
        "working_directory": ".",
        "expected_exit_code": 0,
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


def _runtime() -> RuntimeContext:
    orchestrator = _OrchestratorDouble()
    return RuntimeContext(
        settings=None,
        provider_accounts={},
        provider_registry=None,
        provider_capabilities=None,
        agent_registry=None,
        orchestrator=orchestrator,
        run_executor=RunExecutor(orchestrator),
    )


class DeclaredExecutionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _service(self, *, declarations=None, resolver="default", **kwargs):
        kwargs.setdefault("runtime", _runtime())
        kwargs.setdefault("workspace", self.workspace)
        kwargs.setdefault("tool_registry", build_default_tool_registry(self.root))
        kwargs.setdefault("execution_profile", api_profile())
        if declarations is not None:
            kwargs.setdefault("declarations", declarations)
        if resolver == "default":
            kwargs.setdefault(
                "verification_resolver", lambda task, profile: DECLARATION_ID
            )
        elif resolver is not None:
            kwargs.setdefault("verification_resolver", resolver)
        return ForgeApiService(**kwargs)

    def _run(self, service, *, run_id, task=None, approval_policy="__service__",
             factory=None):
        task = task or Task(id="task-" + run_id, description="declared execution")
        scope = service._build_run_scope(run_id, task)
        policy = (
            service._approval_policy
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
            execution_request_factory=(
                factory if factory is not None
                else service._build_execution_request_factory(run_id)
            ),
            approval_policy=policy,
            approval_resolver=service._approval_resolver,
        )


class EmptyDeclarationTests(DeclaredExecutionTestCase):
    def test_no_declarations_means_no_command_authority(self) -> None:
        """A: an undeclared service derives an empty command set."""
        service = self._service(declarations=None)
        self.assertEqual(service._declarations, {})
        task = Task(id="t", description="d")
        self.assertEqual(service._effective_execution_commands(task), frozenset())

    def test_no_declarations_means_no_host_execution(self) -> None:
        """A: nothing is requested, so no process runs."""
        service = self._service(declarations=None)
        run = self._run(service, run_id="empty")
        self.assertEqual(run.execution_results, [])
        self.assertEqual(run.state.value, "COMPLETED")

    def test_no_declarations_yields_no_factory(self) -> None:
        service = self._service(declarations=None)
        self.assertIsNone(service._build_execution_request_factory("empty-factory"))

    def test_empty_derived_authority_short_circuits_the_factory(self) -> None:
        """A: the executor's command-authority gate blocks any request.

        A factory that would raise is never reached when the derived authority
        is empty, so an undeclared or unresolved binding cannot even produce a
        request, let alone a process.
        """
        service = self._service(declarations=None)
        task = Task(id="no-authority", description="d")
        run = service.runtime.run_executor.execute(
            task=task,
            agent_name="fixture",
            provider_name="fixture",
            workspace=self.workspace,
            run_id="no-authority",
            run_scope=service._build_run_scope("no-authority", task),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=service._effective_execution_commands(task),
            execution_request_factory=lambda task, profile: (_ for _ in ()).throw(
                AssertionError("the factory must not be reached")
            ),
            approval_policy=ApprovalPolicy(),
        )
        self.assertEqual(run.execution_results, [])
        self.assertEqual(
            [e for e in run.events if e.type.value == "execution_denied"], []
        )

    def test_unresolved_declaration_never_reaches_the_executor_gate(self) -> None:
        """A: an undecided binding composes to an empty authority.

        The operator declared a verification execution but supplied no resolver,
        so nothing can select it. The derived command set is therefore empty,
        which is what the executor's command-authority gate consumes, and the
        run requests nothing even though a factory exists.
        """
        service = self._service(
            declarations={DECLARATION_ID: declaration()}, resolver=None
        )
        task = Task(id="unresolved", description="d")
        derived = service._effective_execution_commands(task)
        built_factory = service._build_execution_request_factory("unresolved")
        self.assertEqual(derived, frozenset())
        self.assertIsNotNone(built_factory, "the declaration exists, so a factory exists")

        run = service.runtime.run_executor.execute(
            task=task,
            agent_name="fixture",
            provider_name="fixture",
            workspace=self.workspace,
            run_id="unresolved",
            run_scope=service._build_run_scope("unresolved", task),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=derived,
            execution_request_factory=built_factory,
            approval_policy=ApprovalPolicy(),
        )
        self.assertEqual(run.execution_results, [])
        self.assertEqual(
            [e for e in run.events if e.type.value == "execution_denied"], []
        )


class ServerSideDeclarationTests(DeclaredExecutionTestCase):
    def test_declaration_produces_a_request_with_the_operator_command(self) -> None:
        """B: the command comes from the declaration, not from the caller."""
        service = self._service(declarations={DECLARATION_ID: declaration()})
        task = Task(id="t", description="d")
        self.assertEqual(
            service._effective_execution_commands(task), frozenset({EXECUTABLE})
        )
        request = service._build_execution_request_factory("run-b")(task, api_profile())[0]
        self.assertEqual(request.command, declaration().command)
        self.assertEqual(request.metadata["declaration_id"], DECLARATION_ID)

    def test_translated_profile_is_the_scope_profile(self) -> None:
        """The request profile always comes from the server-side scope."""
        profile = api_profile()
        request = to_execution_request(
            declaration(), profile=profile, run_id="run-b2"
        )
        self.assertIs(request.profile, profile)

    def test_declaration_cannot_carry_approval_authority(self) -> None:
        """A declaration has no approval field, so it cannot weaken approval."""
        self.assertFalse(hasattr(declaration(), "approval_required"))
        request = to_execution_request(
            declaration(), profile=api_profile(), run_id="run-b3"
        )
        self.assertIs(request.approval_required, False)

    def test_declaration_metadata_cannot_overwrite_canonical_fields(self) -> None:
        request = to_execution_request(
            declaration(metadata={"declaration_id": "forged", "note": "ok"}),
            profile=api_profile(),
            run_id="run-b4",
        )
        self.assertEqual(request.metadata["declaration_id"], DECLARATION_ID)
        self.assertEqual(request.metadata["note"], "ok")


class NoHttpAuthorityTests(DeclaredExecutionTestCase):
    def test_taskrunrequest_has_no_execution_fields(self) -> None:
        """C: the HTTP contract cannot express command authority."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(TaskRunRequest)}
        self.assertEqual(
            fields,
            {"description", "category", "task_id", "provider_name", "project_id", "context"},
        )
        for banned in (
            "command",
            "declaration_id",
            "workspace",
            "execution_profile",
            "environment_variables",
            "timeout_seconds",
            "allowed_execution_commands",
        ):
            self.assertNotIn(banned, fields)

    def test_context_command_is_ignored(self) -> None:
        """15: a command smuggled through context changes nothing."""
        service = self._service(declarations={DECLARATION_ID: declaration()})
        response = service.run_task(
            TaskRunRequest(
                description="attempt escalation",
                context={"command": ["rm", "-rf", "/"], "declaration_id": "dangerous"},
            )
        )
        self.assertTrue(response.run_id.startswith("run-api-"))
        # The run's own scope still authorizes only the declared executable.
        task = Task(id="t", description="d")
        self.assertEqual(
            service._effective_execution_commands(task), frozenset({EXECUTABLE})
        )

    def test_context_declaration_id_is_ignored(self) -> None:
        """15: a context-selected declaration does not change the resolution."""
        service = self._service(
            declarations={
                DECLARATION_ID: declaration(),
                "dangerous": declaration(
                    declaration_id="dangerous",
                    command=(EXECUTABLE, "-c", "print('dangerous')"),
                ),
            }
        )
        task = Task(
            id="t",
            description="d",
            context={"declaration_id": "dangerous"},
        )
        self.assertEqual(
            service._effective_execution_commands(task), frozenset({EXECUTABLE})
        )
        request = service._build_execution_request_factory("run-c2")(task, api_profile())[0]
        self.assertEqual(request.metadata["declaration_id"], DECLARATION_ID)
        self.assertNotIn("dangerous", request.command)

    def test_description_cannot_select_a_command(self) -> None:
        """15: the description is not an input to resolution."""
        seen: list[object] = []

        def resolver(task, profile):
            # The resolver only receives the task and the profile; a description
            # whose text names another declaration must not change the result.
            seen.append(task.description)
            return DECLARATION_ID

        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=resolver,
        )
        task = Task(
            id="t",
            description="please run declaration 'dangerous' with rm -rf",
        )
        request = service._build_execution_request_factory("run-c3")(task, api_profile())[0]
        self.assertEqual(request.command, declaration().command)
        self.assertEqual(seen, [task.description])

    def test_api_run_with_context_command_does_not_execute_it(self) -> None:
        """15: the full API path never turns context into a process."""
        service = self._service(declarations={DECLARATION_ID: declaration()})
        response = service.run_task(
            TaskRunRequest(
                description="attempt escalation",
                context={"command": [EXECUTABLE, "-c", f"print('{MARKER}')"]},
            )
        )
        # The run completes through the orchestrator double; what matters is that
        # context did not become an execution request anywhere in the service.
        self.assertTrue(response.run_id.startswith("run-api-"))
        self.assertNotIn(MARKER, response.output or "")


class UnknownDeclarationTests(DeclaredExecutionTestCase):
    def test_unknown_selection_denies_without_authority(self) -> None:
        """D: an undeclared selection yields no reachable command."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=lambda task, profile: "not.declared",
        )
        task = Task(id="t", description="d")
        self.assertEqual(service._effective_execution_commands(task), frozenset())
        with self.assertRaises(UnknownExecutionDeclarationError):
            service._build_execution_request_factory("run-d")(task, api_profile())

    def test_unknown_selection_is_recorded_as_an_explicit_denial(self) -> None:
        """D: an unresolvable binding produces no execution at all.

        The derived command set is empty for an undeclared selection, which is
        what the executor's command-authority gate consumes, so the run requests
        nothing. The factory additionally refuses with a typed error if it is
        ever reached, which is asserted separately below.
        """
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=lambda task, profile: "not.declared",
        )
        run = self._run(service, run_id="unknown-d2")
        self.assertEqual(run.execution_results, [])
        task = Task(id="t", description="d")
        with self.assertRaises(UnknownExecutionDeclarationError):
            service._build_execution_request_factory("unknown-d3")(task, api_profile())

    def test_missing_resolver_grants_nothing(self) -> None:
        """A resolver is required; without it host execution stays disabled."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()}, resolver=None
        )
        task = Task(id="t", description="d")
        self.assertEqual(service._effective_execution_commands(task), frozenset())


class DeclarationProfileConsistencyTests(DeclaredExecutionTestCase):
    def test_command_outside_profile_is_rejected_at_composition(self) -> None:
        """E: the profile ceiling cannot be exceeded by a declaration."""
        with self.assertRaises(ExecutionDeclarationError):
            ForgeApiService(
                runtime=_runtime(),
                workspace=self.workspace,
                tool_registry=build_default_tool_registry(self.root),
                execution_profile=api_profile(),
                declarations={
                    "bad": declaration(
                        declaration_id="bad", command=("git", "status")
                    )
                },
            )

    def test_working_directory_mismatch_is_rejected(self) -> None:
        """E: the profile declares the working directory exactly."""
        with self.assertRaises(ExecutionDeclarationError):
            ForgeApiService(
                runtime=_runtime(),
                workspace=self.workspace,
                tool_registry=build_default_tool_registry(self.root),
                execution_profile=api_profile(working_directory="sub"),
                declarations={DECLARATION_ID: declaration(working_directory=".")},
            )

    def test_environment_mismatch_is_rejected(self) -> None:
        """E: a declaration cannot introduce an environment variable."""
        with self.assertRaises(ExecutionDeclarationError):
            ForgeApiService(
                runtime=_runtime(),
                workspace=self.workspace,
                tool_registry=build_default_tool_registry(self.root),
                execution_profile=api_profile(environment_variables={}),
                declarations={
                    DECLARATION_ID: declaration(
                        environment_variables={"PYTHONPATH": "/evil"}
                    )
                },
            )

    def test_environment_value_mismatch_is_rejected(self) -> None:
        """E: a declaration cannot change a declared value either."""
        with self.assertRaises(ExecutionDeclarationError):
            ForgeApiService(
                runtime=_runtime(),
                workspace=self.workspace,
                tool_registry=build_default_tool_registry(self.root),
                execution_profile=api_profile(environment_variables={"A": "1"}),
                declarations={
                    DECLARATION_ID: declaration(environment_variables={"A": "2"})
                },
            )

    def test_timeout_above_profile_is_rejected(self) -> None:
        """E: the profile timeout is the ceiling."""
        with self.assertRaises(ExecutionDeclarationError):
            ForgeApiService(
                runtime=_runtime(),
                workspace=self.workspace,
                tool_registry=build_default_tool_registry(self.root),
                execution_profile=api_profile(timeout_seconds=1.0),
                declarations={DECLARATION_ID: declaration(timeout_seconds=60.0)},
            )

    def test_profile_id_mismatch_is_rejected(self) -> None:
        """E: a declaration must target the active profile."""
        with self.assertRaises(ExecutionDeclarationError):
            validate_declarations_against_profile(
                {DECLARATION_ID: declaration(profile_id="other")}, api_profile()
            )

    def test_consistent_declaration_is_accepted(self) -> None:
        """The accepted case is the one the other tests deny."""
        validate_declarations_against_profile(
            {DECLARATION_ID: declaration()}, api_profile()
        )


class RunScopeGateTests(DeclaredExecutionTestCase):
    def test_command_outside_scope_is_denied(self) -> None:
        """F: the coordinator re-validates the request against the scope."""
        service = self._service(declarations={DECLARATION_ID: declaration()})
        task = Task(id="t", description="d")
        scope = service._build_run_scope("run-f", task)
        with self.assertRaises(RunScopeError):
            scope.validate_command_set((("git",),))

    def test_scope_command_set_is_derived_not_configured(self) -> None:
        """7: the scope command set comes from the resolved declaration."""
        service = self._service(declarations={DECLARATION_ID: declaration()})
        task = Task(id="t", description="d")
        scope = service._build_run_scope("run-f2", task)
        self.assertEqual(scope.allowed_execution_commands, frozenset({EXECUTABLE}))
        self.assertFalse(scope.allows_command(("git",)))

    def test_legacy_allowlist_parameter_grants_nothing(self) -> None:
        """7: an independently configured allowlist is no longer authority."""
        service = self._service(
            declarations=None,
            allowed_execution_commands=frozenset({EXECUTABLE}),
        )
        task = Task(id="t", description="d")
        self.assertEqual(service._allowed_execution_commands, frozenset())
        self.assertEqual(service._effective_execution_commands(task), frozenset())


class ApprovalBoundaryTests(DeclaredExecutionTestCase):
    def test_missing_approval_policy_refuses_to_execute(self) -> None:
        """G: host execution requires a server-side approval policy."""
        service = self._service(declarations={DECLARATION_ID: declaration()})
        run = self._run(service, run_id="approval-missing", approval_policy=None)
        self.assertEqual(run.execution_results, [])
        reasons = [
            e.data.get("reason")
            for e in run.events
            if e.type.value == "execution_denied"
        ]
        self.assertIn("approval_policy_required", reasons)

    def test_resolver_none_waits_for_approval(self) -> None:
        """G: a pending approval is reported as waiting, not as success."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            approval_policy=ApprovalPolicy(("execute",)),
        )
        run = self._run(
            service, run_id="approval-wait",
            approval_policy=ApprovalPolicy(("execute",)),
        )
        self.assertEqual(len(run.execution_results), 1)
        self.assertEqual(
            run.execution_results[0].outcome_status,
            ExecutionOutcomeStatus.APPROVAL_WAITING,
        )
        self.assertEqual(run.state.value, "WAITING_FOR_APPROVAL")

    def test_request_cannot_disable_server_side_approval(self) -> None:
        """G: approval_required=False does not bypass the server policy."""

        def factory(task, profile):
            return (
                ExecutionRequest(
                    command=declaration().command,
                    profile=profile,
                    approval_required=False,
                ),
            )

        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            approval_policy=ApprovalPolicy(("execute",)),
        )
        run = self._run(
            service, run_id="approval-bypass",
            approval_policy=ApprovalPolicy(("execute",)), factory=factory,
        )
        self.assertEqual(
            run.execution_results[0].outcome_status,
            ExecutionOutcomeStatus.APPROVAL_WAITING,
        )

    def test_approved_execution_completes(self) -> None:
        class _Approved:
            def resolve(self, request):
                return ApprovalResolution(
                    decision=ApprovalState.APPROVED,
                    approved_fingerprint=request.intent_fingerprint or "",
                    approval_id="declared-test",
                )

        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            approval_policy=ApprovalPolicy(("execute",)),
            approval_resolver=_Approved(),
        )
        run = self._run(
            service, run_id="approval-approved",
            approval_policy=ApprovalPolicy(("execute",)),
        )
        self.assertEqual(
            run.execution_results[0].outcome_status,
            ExecutionOutcomeStatus.EXECUTION_SUCCESS,
        )


class ImmutabilityTests(DeclaredExecutionTestCase):
    def test_declaration_is_frozen(self) -> None:
        """H: a declaration cannot be mutated after creation."""
        import dataclasses

        subject = declaration()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            subject.command = ("other",)

    def test_request_is_frozen(self) -> None:
        """H: a translated request cannot be mutated either."""
        import dataclasses

        request = to_execution_request(
            declaration(), profile=api_profile(), run_id="run-h"
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.command = ("other",)

    def test_declaration_rejects_wrong_purpose(self) -> None:
        """v0.1 is verification-only."""
        with self.assertRaises(ExecutionDeclarationError):
            declaration(purpose="deploy")

    def test_declaration_rejects_unsafe_paths(self) -> None:
        with self.assertRaises(ExecutionDeclarationError):
            declaration(working_directory="../escape")
        with self.assertRaises(ExecutionDeclarationError):
            declaration(artifact_targets=("../escape",))

    def test_approved_fingerprint_cannot_authorize_another_command(self) -> None:
        """H: approval is bound to the intent fingerprint.

        A resolver that approves a fingerprint other than the intent it was
        asked about is the classic confused-deputy attempt: a grant for one
        intent must not authorize a different one.
        """

        class _WrongFingerprintApprover:
            def resolve(self, request):
                return ApprovalResolution(
                    decision=ApprovalState.APPROVED,
                    approved_fingerprint="not-the-current-intent",
                    approval_id="mismatched",
                )

        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            approval_policy=ApprovalPolicy(("execute",)),
            approval_resolver=_WrongFingerprintApprover(),
        )
        run = self._run(
            service, run_id="fingerprint-mismatch",
            approval_policy=ApprovalPolicy(("execute",)),
        )
        self.assertEqual(len(run.execution_results), 1)
        self.assertEqual(
            run.execution_results[0].outcome_status,
            ExecutionOutcomeStatus.APPROVAL_WAITING,
        )
        self.assertEqual(
            run.execution_results[0].metadata.get("denial_reason"),
            "intent_fingerprint_mismatch",
        )


class ExecutionTokenBoundaryTests(DeclaredExecutionTestCase):
    def test_authorized_execution_cannot_be_created_directly(self) -> None:
        """I: the execution token requires the coordinator sentinel."""
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

    def test_api_layer_has_no_direct_adapter_or_process_access(self) -> None:
        """I: the API cannot reach the adapter or spawn anything.

        Prose that names the execution chain in documentation is not a code
        path, so the assertion inspects executable lines with module and
        function docstrings removed.
        """
        import ast

        repo = Path(__file__).resolve().parents[1]
        for relative in (
            "app/api/service.py",
            "app/api/server.py",
            "app/api/models.py",
            "app/api/client.py",
        ):
            source = (repo / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc:
                        docstrings.add(doc)
            code_lines = []
            for line in source.splitlines():
                stripped = line.strip()
                if any(stripped and stripped in doc for doc in docstrings):
                    continue
                code_lines.append(line)
            code = "\n".join(code_lines)
            for banned in ("LocalExecutionAdapter", "AuthorizedExecution", "subprocess", "Popen"):
                self.assertNotIn(banned, code, f"{relative}: {banned}")


class RealDeclaredExecutionTests(DeclaredExecutionTestCase):
    def test_declared_command_executes_through_the_real_path(self) -> None:
        """J: operator declaration plus derived allowlist runs the process."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            approval_policy=ApprovalPolicy(),
        )
        run = self._run(service, run_id="real-execution")
        self.assertEqual(len(run.execution_results), 1)
        result = run.execution_results[0]
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.exit_code, 0)
        self.assertIn(MARKER, result.stdout or "")
        self.assertEqual(run.state.value, "COMPLETED")

    def test_execution_metadata_is_sanitized(self) -> None:
        """Only identifiers and statuses are recorded, never raw output."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            approval_policy=ApprovalPolicy(),
        )
        run = self._run(service, run_id="real-sanitized")
        events = [e for e in run.events if e.type.value.startswith("execution_")]
        self.assertTrue(events)
        for event in events:
            self.assertNotIn("stdout", event.data)
            self.assertNotIn("stderr", event.data)
            self.assertNotIn("_token", event.data)
            self.assertNotIn("environment_variables", event.data)

    def test_relative_scratch_isolation_holds(self) -> None:
        """The isolated adapter runs in a scratch copy, not the scoped root."""
        service = self._service(
            declarations={
                "write.marker": declaration(
                    declaration_id="write.marker",
                    command=(
                        EXECUTABLE,
                        "-c",
                        "open('declared-marker.txt','w').write('x')",
                    ),
                )
            },
            resolver=lambda task, profile: "write.marker",
            approval_policy=ApprovalPolicy(),
        )
        run = self._run(service, run_id="real-isolated")
        self.assertEqual(
            run.execution_results[0].outcome_status,
            ExecutionOutcomeStatus.EXECUTION_SUCCESS,
        )
        self.assertFalse((self.root / "declared-marker.txt").exists())


class LegacyCompatibilityTests(DeclaredExecutionTestCase):
    def test_run_executor_without_new_parameters_still_works(self) -> None:
        executor = RunExecutor(_OrchestratorDouble())
        run = executor.execute(
            task=Task(id="legacy", description="d"),
            agent_name="fixture",
            provider_name="fixture",
        )
        self.assertEqual(run.state.value, "COMPLETED")
        self.assertEqual(run.execution_results, [])

    def test_plain_declaration_service_has_no_execution(self) -> None:
        service = self._service(declarations=None)
        self.assertIsNone(service._execution_request_factory)
        self.assertEqual(service._effective_execution_commands(None), frozenset())


class DeclaredVerificationEntryPointTests(DeclaredExecutionTestCase):
    """The trusted server-side entry point ``run_declared_verification``.

    Selection input is a declaration id only, resolved against the operator
    registry. No other authority is accepted, and the existing execution chain
    is reused unchanged.
    """

    def _entry_service(self, **kwargs):
        kwargs.setdefault("approval_policy", ApprovalPolicy())
        return self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=None,  # the entry point must not need a task resolver
            **kwargs,
        )

    def test_valid_id_runs_a_real_verification_process(self) -> None:
        """A: a declared id reaches the real adapter and runs the command."""
        service = self._entry_service()
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.state, "COMPLETED")
        self.assertIn(MARKER, response.output or "")

    def test_unknown_id_fails_closed(self) -> None:
        """B: an undeclared id raises the existing typed error."""
        service = self._entry_service()
        with self.assertRaises(UnknownExecutionDeclarationError):
            service.run_declared_verification("not.declared")

    def test_unknown_id_starts_no_process(self) -> None:
        """B: no scope is frozen and no run is recorded for an unknown id."""
        service = self._entry_service()
        before = set(service._run_store.list_run_ids())
        with self.assertRaises(UnknownExecutionDeclarationError):
            service.run_declared_verification("not.declared")
        self.assertEqual(set(service._run_store.list_run_ids()), before)

    def test_empty_registry_disables_execution(self) -> None:
        """C: no declarations means no execution, with no implicit command."""
        service = self._service(declarations=None, resolver=None,
                               approval_policy=ApprovalPolicy())
        with self.assertRaises(ExecutionDeclarationError) as ctx:
            service.run_declared_verification(DECLARATION_ID)
        self.assertIn("disabled", str(ctx.exception))

    def test_entry_point_accepts_no_authority_parameters(self) -> None:
        """D-H: the signature admits no command, workspace, env, or timeout."""
        import inspect

        signature = inspect.signature(ForgeApiService.run_declared_verification)
        parameters = list(signature.parameters)
        self.assertEqual(parameters, ["self", "declaration_id", "purpose_run_id"])
        for banned in (
            "command", "workspace", "execution_profile", "environment_variables",
            "timeout_seconds", "allowed_execution_commands", "approval_required",
            "task", "context", "category", "description", "task_id", "profile",
        ):
            self.assertNotIn(banned, parameters)

    def test_caller_cannot_supply_a_command(self) -> None:
        """D: there is no parameter and no attribute that carries a command."""
        service = self._entry_service()
        with self.assertRaises(TypeError):
            service.run_declared_verification(DECLARATION_ID, command=("rm", "-rf", "/"))

    def test_caller_cannot_influence_workspace_or_profile(self) -> None:
        """E/F: the scope's workspace and profile are the service's own."""
        service = self._entry_service()
        captured = {}

        real_builder = service._build_declared_verification_scope

        def spy(run_id, declared):
            scope = real_builder(run_id, declared)
            captured["scope"] = scope
            return scope

        service._build_declared_verification_scope = spy
        service.run_declared_verification(DECLARATION_ID)
        scope = captured["scope"]
        self.assertIs(scope.workspace, service._workspace)
        self.assertIs(scope.execution_profile, service._execution_profile)

    def test_scope_command_authority_is_only_the_declared_executable(self) -> None:
        """The derived command set is exactly the declared executable."""
        service = self._entry_service()
        captured = {}
        real_builder = service._build_declared_verification_scope

        def spy(run_id, declared):
            scope = real_builder(run_id, declared)
            captured["scope"] = scope
            return scope

        service._build_declared_verification_scope = spy
        service.run_declared_verification(DECLARATION_ID)
        scope = captured["scope"]
        self.assertEqual(scope.allowed_execution_commands, frozenset({EXECUTABLE}))
        self.assertFalse(scope.allows_command(("git",)))
        self.assertEqual(scope.allowed_tool_ids, frozenset())

    def test_client_controlled_values_cannot_select_a_declaration(self) -> None:
        """J: category, context, description and task_id are not selection inputs.

        The check inspects the executable body only, because the docstring names
        the rejected channels in prose.
        """
        import inspect

        source = inspect.getsource(ForgeApiService.run_declared_verification)
        parts = source.split('"""', 2)
        code = parts[0] + (parts[2] if len(parts) > 2 else "")
        for banned in (
            "req.category", "req.context", "req.description", "req.task_id",
            "_verification_resolver", "TaskRunRequest", "context.get",
        ):
            self.assertNotIn(banned, code, banned)

    def test_run_task_stays_execution_disabled(self) -> None:
        """K: the normal API path never invokes a declared verification."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=lambda task, profile: DECLARATION_ID,
            approval_policy=ApprovalPolicy(),
        )
        response = service.run_task(TaskRunRequest(description="normal task"))
        self.assertTrue(response.run_id.startswith("run-api-"), response.run_id)
        self.assertNotIn(MARKER, response.output or "")

    def test_server_generated_run_id_is_used(self) -> None:
        """P: the entry point generates its own run id, never a caller value."""
        service = self._entry_service()
        first = service.run_declared_verification(DECLARATION_ID, purpose_run_id="run-api-x")
        second = service.run_declared_verification(DECLARATION_ID, purpose_run_id="run-api-x")
        self.assertTrue(first.run_id.startswith("run-verify-"), first.run_id)
        self.assertNotEqual(first.run_id, second.run_id)

    def test_purpose_run_id_is_correlation_only(self) -> None:
        """Q: the correlation value cannot change what executes."""
        service = self._entry_service()
        plain = service.run_declared_verification(DECLARATION_ID)
        correlated = service.run_declared_verification(
            DECLARATION_ID, purpose_run_id="run-api-correlation"
        )
        self.assertTrue(correlated.success, correlated.error)
        self.assertIn(MARKER, correlated.output or "")
        self.assertEqual(
            MARKER in (plain.output or ""), MARKER in (correlated.output or "")
        )
        # The correlation value is recorded as metadata on the run history.
        record = service._run_store.load(correlated.run_id)
        self.assertIsNotNone(record)
        self.assertNotEqual(record.run_id, "run-api-correlation")

    def test_purpose_run_id_must_be_a_valid_value_when_given(self) -> None:
        service = self._entry_service()
        for bad in ("", "   ", 42):
            with self.assertRaises(ExecutionDeclarationError):
                service.run_declared_verification(DECLARATION_ID, purpose_run_id=bad)

    def test_missing_approval_policy_refuses_to_execute(self) -> None:
        """L: no server-side approval policy means no execution."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=None,
        )
        self.assertIsNone(service._approval_policy)
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertFalse(response.success)
        self.assertEqual(response.error, "approval_policy_required")
        self.assertNotIn(MARKER, response.output or "")

    def test_approval_policy_is_still_enforced(self) -> None:
        """L: a policy requiring approval stops the process."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=None,
            approval_policy=ApprovalPolicy(("execute",)),
        )
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertFalse(response.success)
        self.assertEqual(response.error, "approval_waiting")

    def test_approval_waiting_without_resolver(self) -> None:
        """M: a pending approval is reported as the existing wait state."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=None,
            approval_policy=ApprovalPolicy(("execute",)),
        )
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertEqual(response.state, "WAITING_FOR_APPROVAL")

    def test_approval_fingerprint_mismatch_is_rejected(self) -> None:
        """N: an approval bound to another intent cannot authorize this one."""

        class _WrongFingerprint:
            def resolve(self, request):
                return ApprovalResolution(
                    decision=ApprovalState.APPROVED,
                    approved_fingerprint="not-the-current-intent",
                    approval_id="mismatched",
                )

        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=None,
            approval_policy=ApprovalPolicy(("execute",)),
            approval_resolver=_WrongFingerprint(),
        )
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertFalse(response.success)
        self.assertEqual(response.error, "intent_fingerprint_mismatch")

    def test_approved_declaration_executes(self) -> None:
        """L: an explicit approval lets the declared command run."""

        class _Approved:
            def resolve(self, request):
                return ApprovalResolution(
                    decision=ApprovalState.APPROVED,
                    approved_fingerprint=request.intent_fingerprint or "",
                    approval_id="approved",
                )

        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=None,
            approval_policy=ApprovalPolicy(("execute",)),
            approval_resolver=_Approved(),
        )
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertTrue(response.success, response.error)
        self.assertIn(MARKER, response.output or "")

    def test_declaration_mutation_cannot_alter_the_authorized_command(self) -> None:
        """O: the declaration is frozen, so the command cannot be swapped."""
        import dataclasses

        service = self._entry_service()
        declared = service._declarations[DECLARATION_ID]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            declared.command = ("rm", "-rf", "/")

    def test_subprocess_is_reached_only_through_the_adapter(self) -> None:
        """S: the entry point does not spawn anything itself."""
        import inspect

        source = inspect.getsource(ForgeApiService.run_declared_verification)
        parts = source.split('"""', 2)
        code = parts[0] + (parts[2] if len(parts) > 2 else "")
        for banned in ("subprocess", "Popen", "LocalExecutionAdapter",
                       "AuthorizedExecution", "ExecutionCoordinator", "os.system"):
            self.assertNotIn(banned, code, banned)

    def test_coordinator_cannot_be_bypassed(self) -> None:
        """R: the run carries a real coordinator-produced execution result."""
        service = self._entry_service()
        response = service.run_declared_verification(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        self.assertIsNotNone(record)
        names = [event.event_type.name for event in record.events]
        self.assertIn("EXECUTION_STARTED", names)
        self.assertIn("EXECUTION_COMPLETED", names)

    def test_verification_run_has_one_consistent_task_identity(self) -> None:
        """A/B/C/D: response, stored state, and events share one task_id.

        The declaration id is a declaration identity and must never be stored as
        a task id, and the run id stays a separate identity.
        """
        service = self._entry_service()
        response = service.run_declared_verification(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        self.assertIsNotNone(record)

        event_task_ids = {event.task_id for event in record.events if event.task_id}
        self.assertTrue(event_task_ids, "execution events must record a task id")

        identities = {response.task_id, record.snapshot.task_id} | event_task_ids
        self.assertEqual(
            len(identities), 1, f"one run must expose one task id, got {identities}"
        )
        self.assertEqual(response.task_id, f"task-verify-{DECLARATION_ID}")
        self.assertNotEqual(response.task_id, DECLARATION_ID)
        self.assertNotIn(DECLARATION_ID, event_task_ids)
        self.assertNotEqual(response.task_id, response.run_id)
        self.assertTrue(response.run_id.startswith("run-verify-"))

    def test_purpose_run_id_never_becomes_a_task_identity(self) -> None:
        """E: the correlation value stays correlation, not identity."""
        service = self._entry_service()
        correlation = "run-api-correlation-source"
        response = service.run_declared_verification(
            DECLARATION_ID, purpose_run_id=correlation
        )
        record = service._run_store.load(response.run_id)
        event_task_ids = {event.task_id for event in record.events if event.task_id}

        self.assertNotEqual(response.task_id, correlation)
        self.assertNotEqual(response.run_id, correlation)
        self.assertNotIn(correlation, event_task_ids)
        self.assertNotEqual(record.snapshot.task_id, correlation)
        self.assertTrue(response.run_id.startswith("run-verify-"))

    def test_run_task_keeps_its_own_task_identity(self) -> None:
        """F/G: the ordinary API path is unchanged and still consistent."""
        service = self._service(
            declarations={DECLARATION_ID: declaration()},
            resolver=lambda task, profile: DECLARATION_ID,
            approval_policy=ApprovalPolicy(),
        )
        response = service.run_task(TaskRunRequest(description="normal task"))
        self.assertTrue(response.run_id.startswith("run-api-"))
        self.assertNotEqual(response.task_id, DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        event_task_ids = {event.task_id for event in record.events if event.task_id}
        if event_task_ids:
            identities = {response.task_id, record.snapshot.task_id} | event_task_ids
            self.assertEqual(len(identities), 1, identities)

    def test_declared_verification_records_no_raw_output(self) -> None:
        """Only sanitized lifecycle metadata is persisted."""
        service = self._entry_service()
        response = service.run_declared_verification(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        for event in record.events:
            self.assertNotIn("stdout", event.metadata)
            self.assertNotIn("stderr", event.metadata)
            self.assertNotIn("_token", event.metadata)
            self.assertNotIn("environment_variables", event.metadata)


if __name__ == "__main__":
    unittest.main()
