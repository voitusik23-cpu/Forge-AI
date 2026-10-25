"""End-to-end vertical workflow, exercised as a real workflow.

These tests observe behaviour, not the presence of a class or a source string:

* a specification and its acceptance criteria are readable BEFORE anything runs;
* an action that needs approval and does not get it performs nothing;
* granting approval at the approval boundary lets the action run;
* the action cannot create a file outside the allowed workspace;
* verification really executes the generated project's own test suite and the
  reported exit code is the process's real exit code;
* a failing action or a failing verification is never reported as success;
* the reported created/deleted files are measured against the files on disk.

HONEST SCOPE: the project these tests build is produced by an operator-declared
``python`` command, NOT by an LLM. Nothing here exercises a model, a provider,
or an API key. What is proven is the workflow and its verification machinery.

ISOLATION: production ``ForgeApiService(run_store=None)`` uses a shared per-user
run-store root. Every service here is given its own ``RunStore`` under a
temporary directory, so these tests never race another process.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.agent_runtime import idempotency_integration as integration
from app.agent_runtime.acceptance_spec import AcceptanceSpec
from app.api.service import ForgeApiService
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.models import TaskResult
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_agent_harness
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope
from app.runtime.run_store import RunStore
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolution,
    ApprovalState,
    InMemoryApprovalResolver,
)
from app.tools.registry import build_default_tool_registry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.workflows.project_workflow import (
    DECLARED_COMMAND_IS_NOT_A_GENERATOR,
    ProjectWorkflowError,
    ProjectWorkflowRegistry,
    UnknownWorkflowError,
    build_specification,
    run_project_workflow,
)

# ---------------------------------------------------------------------------
# The declared commands. Both are operator-owned argv tuples; neither is
# supplied by a caller, a decision, or a model.
# ---------------------------------------------------------------------------

BUILD_ID = "demo.project.build"
BUILD_FAILING_ID = "demo.project.build-defective"
BUILD_OUTSIDE_ID = "demo.project.build-outside"
VERIFY_ID = "demo.project.verify"

_BUILD_SCRIPT = r"""
import sys

defective = "defective" in sys.argv
outside = "outside" in sys.argv

if outside:
    # A declared command that tries to leave the workspace. This is an
    # operator-declared command on purpose: no caller can inject it.
    open("../forge_outside.txt", "w", encoding="utf-8").write("escaped")

if defective:
    module_source = "def add(a, b):\n    return a - b\n"
else:
    module_source = "def add(a, b):\n    return a + b\n"

if not outside:
    open("mod.py", "w", encoding="utf-8").write(module_source)
    open("README.md", "w", encoding="utf-8").write("# demo\nsmall module\n")
    import os
    os.makedirs("tests", exist_ok=True)
    open("tests/__init__.py", "w", encoding="utf-8").write("")
    open("tests/test_mod.py", "w", encoding="utf-8").write(
        "import unittest\n"
        "import mod\n\n"
        "class AddTests(unittest.TestCase):\n"
        "    def test_add_positive(self):\n"
        "        self.assertEqual(mod.add(2, 3), 5)\n\n"
        "    def test_add_negative(self):\n"
        "        self.assertEqual(mod.add(-1, -1), -2)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
"""

BUILD_COMMAND = ("python", "-c", _BUILD_SCRIPT)
BUILD_DEFECTIVE_COMMAND = ("python", "-c", _BUILD_SCRIPT, "defective")
BUILD_OUTSIDE_COMMAND = ("python", "-c", _BUILD_SCRIPT, "outside")

# The second declared command. It really executes the generated project's own
# test suite; pytest is unavailable in this environment, so unittest discovery
# is used, exactly as the project's own tests would be run by hand.
VERIFY_COMMAND = (
    "python",
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-t",
    ".",
    "-p",
    "test_*.py",
)

PROJECT_FILES = ("README.md", "mod.py", "tests/__init__.py", "tests/test_mod.py")

CRITERIA = (
    AcceptanceCriterion(
        "project-module-present",
        "the generated project contains its module",
        requirement_id="project-source",
    ),
    AcceptanceCriterion(
        "project-readme-present",
        "the generated project contains its README",
        requirement_id="project-docs",
    ),
    AcceptanceCriterion(
        "project-tests-present",
        "the generated project contains its own test module",
        requirement_id="project-tests",
    ),
)

EXPECTATIONS = {
    "project-module-present": VerificationExpectation("mod.py", True),
    "project-readme-present": VerificationExpectation("README.md", True),
    "project-tests-present": VerificationExpectation("tests/test_mod.py", True),
}


class _OrchestratorDouble:
    """The smallest double the acceptance slice needs: it only dispatches."""

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
        harness=create_agent_harness(),
    )


def api_profile(
    *,
    profile_id: str = "api-default",
    commands: tuple[str, ...] = ("python",),
) -> ProjectExecutionProfile:
    """A profile that admits both declared commands.

    ``INTERPRET_TEXT`` is required by ``python -c`` and ``INTERPRET_MODULE`` by
    ``python -m``; the existing ``ExecutionPolicy`` denies an argv whose implied
    capability the profile does not hold, so both are declared here. This is a
    ceiling, not a grant: only an operator declaration can produce a request.
    """
    return ProjectExecutionProfile(
        profile_id=profile_id,
        allowed_commands=commands,
        capabilities=frozenset(
            {ExecutionCapability.INTERPRET_TEXT, ExecutionCapability.INTERPRET_MODULE}
        ),
        network_access=False,
        working_directory=".",
    )


def _declarations() -> dict[str, ExecutionDeclaration]:
    return {
        BUILD_ID: ExecutionDeclaration(
            declaration_id=BUILD_ID,
            command=BUILD_COMMAND,
            profile_id="api-default",
            working_directory=".",
            intent="Create a small Python project with a module, a README and its own tests.",
        ),
        BUILD_FAILING_ID: ExecutionDeclaration(
            declaration_id=BUILD_FAILING_ID,
            command=BUILD_DEFECTIVE_COMMAND,
            profile_id="api-default",
            working_directory=".",
            intent="Create a small Python project whose module is wrong on purpose.",
        ),
        BUILD_OUTSIDE_ID: ExecutionDeclaration(
            declaration_id=BUILD_OUTSIDE_ID,
            command=BUILD_OUTSIDE_COMMAND,
            profile_id="api-default",
            working_directory=".",
            intent="Write a file outside the allowed workspace.",
        ),
        VERIFY_ID: ExecutionDeclaration(
            declaration_id=VERIFY_ID,
            command=VERIFY_COMMAND,
            profile_id="api-default",
            working_directory=".",
            intent="Run the generated project's own test suite.",
            timeout_seconds=25.0,
        ),
    }


def _mismatched_declarations() -> dict[str, ExecutionDeclaration]:
    """A build and a verification declaration whose executables disagree.

    Both are individually admissible: ``ForgeApiService`` validates each
    declaration against the *active profile's* command ceiling, and this test's
    profile admits both ``python`` and ``python3``. ``profile_id`` and
    ``working_directory`` are applied by that same service-level validation, so
    the executable is the axis where two declarations of one workflow can still
    disagree without the service noticing. Only the workflow's own composition
    check catches it, and it catches it before any scope is frozen.
    """
    declarations = _declarations()
    declarations[VERIFY_ID] = ExecutionDeclaration(
        declaration_id=VERIFY_ID,
        command=("python3",) + VERIFY_COMMAND[1:],
        profile_id="api-default",
        working_directory=".",
        intent="Run the generated project's own test suite.",
        timeout_seconds=25.0,
    )
    return declarations


def _acceptance_specs() -> dict[str, AcceptanceSpec]:
    return {
        BUILD_ID: AcceptanceSpec(
            declaration_id=BUILD_ID, criteria=CRITERIA, expectations=EXPECTATIONS
        ),
        BUILD_FAILING_ID: AcceptanceSpec(
            declaration_id=BUILD_FAILING_ID, criteria=CRITERIA, expectations=EXPECTATIONS
        ),
        # The containment command is expected to fail, so its criteria are the
        # same workspace facts: it must not be able to satisfy them.
        BUILD_OUTSIDE_ID: AcceptanceSpec(
            declaration_id=BUILD_OUTSIDE_ID, criteria=CRITERIA, expectations=EXPECTATIONS
        ),
    }


DEFAULT_REGISTRY = ProjectWorkflowRegistry(
    verification_declarations={
        BUILD_ID: VERIFY_ID,
        BUILD_FAILING_ID: VERIFY_ID,
        BUILD_OUTSIDE_ID: VERIFY_ID,
    }
)


def _mismatch_registry() -> ProjectWorkflowRegistry:
    """A registry that binds the build to the verification declaration.

    ``_mismatched_declarations()`` supplies that declaration with a *different
    executable*, so the two helpers together describe one mismatched workflow.
    """
    return ProjectWorkflowRegistry(
        verification_declarations={BUILD_ID: VERIFY_ID}
    )


class _CapturingApprovalResolver:
    """An approval resolver that records every request and decides.

    It implements the existing ``ApprovalResolver`` protocol and nothing else. It
    decides on the request it was handed and echoes that request's own
    ``intent_fingerprint``, which the existing ``ExecutionCoordinator``
    independently re-checks against the built intent before it authorizes
    anything. The workflow dispatches two runs, so it is asked twice.
    """

    def __init__(self, decision: ApprovalState = ApprovalState.APPROVED) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    def resolve(self, request: ApprovalRequest) -> ApprovalResolution | None:
        self.requests.append(request)
        return ApprovalResolution(
            decision=self.decision,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="appr-workflow-test",
        )


class WorkflowTestCase(unittest.TestCase):
    """Shared composition: one temp workspace, one isolated run store per test."""

    registry = DEFAULT_REGISTRY

    def setUp(self) -> None:
        RunScope.release_all()
        # The workspace is a sub-directory of an isolated base directory, so the
        # containment tests that deliberately try to write ``..`` write into a
        # throw-away directory instead of the machine's shared temp directory.
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "ws"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        self._store_tmp = tempfile.TemporaryDirectory()
        self.run_store = RunStore(root=self._store_tmp.name)
        # Idempotency has its own per-user durable ledger. Without an explicit
        # root here, a test that uses an idempotency key would read and write the
        # process-wide ledger, so its outcome would depend on whether some earlier
        # run of the suite had already claimed that key. Isolating it is what makes
        # the replay test deterministic instead of order-dependent.
        self._ledger_tmp = tempfile.TemporaryDirectory()
        self.idempotency_guard = integration.RunIdempotencyGuard.create(
            ledger_root=self._ledger_tmp.name
        )
        # A file that already exists before the run, so "created" is measured
        # against a non-empty starting state rather than assumed to be empty.
        (self.root / "preexisting.txt").write_text("keep me", encoding="utf-8")

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()
        self._store_tmp.cleanup()
        self._ledger_tmp.cleanup()

    def service(
        self,
        *,
        approval_policy=None,
        approval_resolver=None,
        profile=None,
        declarations=None,
        acceptance_specs=None,
    ) -> ForgeApiService:
        return ForgeApiService(
            runtime=_runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=profile or api_profile(),
            declarations=(
                _declarations() if declarations is None else declarations
            ),
            acceptance_specs=(
                _acceptance_specs() if acceptance_specs is None else acceptance_specs
            ),
            approval_policy=(
                ApprovalPolicy() if approval_policy is None else approval_policy
            ),
            approval_resolver=approval_resolver,
            run_store=self.run_store,
            idempotency_guard=self.idempotency_guard,
        )

    def run_flow(self, service, declaration_id=BUILD_ID):
        return run_project_workflow(service, self.registry, declaration_id)

    def files_on_disk(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )


class SpecificationIsVisibleBeforeExecutionTests(WorkflowTestCase):
    def test_specification_and_criteria_are_readable_before_anything_runs(self) -> None:
        """The goal, the criteria, and the plan exist before any action runs.

        This is asserted against the observable state of the workspace: reading
        the specification must not create a single file.
        """
        service = self.service()
        before = self.files_on_disk()

        spec = build_specification(service, self.registry, BUILD_ID)

        self.assertEqual(self.files_on_disk(), before)
        self.assertEqual(spec.build_declaration_id, BUILD_ID)
        self.assertEqual(spec.verification_declaration_id, VERIFY_ID)
        self.assertTrue(spec.goal.strip())
        self.assertEqual(
            [c.criterion_id for c in spec.acceptance_criteria],
            [c.criterion_id for c in CRITERIA],
        )
        for criterion_view in spec.acceptance_criteria:
            self.assertTrue(
                criterion_view.expectation_path,
                "every criterion must state the workspace fact it is judged by",
            )
        self.assertTrue(spec.planned_steps, "a plan must be available up front")
        self.assertTrue(spec.plan_fingerprint)
        # The two declared commands are visible, so the operator can see exactly
        # what will run before it runs.
        self.assertEqual(spec.build_command, BUILD_COMMAND)
        self.assertEqual(spec.verification_command, VERIFY_COMMAND)
        # And this is a projection of the same critera the run will use.
        self.assertEqual(
            set(spec.to_dict()),
            {
                "task_id",
                "title",
                "goal",
                "requirements",
                "acceptance_criteria",
                "build_declaration_id",
                "verification_declaration_id",
                "build_command",
                "verification_command",
                "planned_steps",
                "plan_fingerprint",
            },
        )

    def test_a_declaration_without_criteria_is_refused_before_execution(self) -> None:
        service = self.service(
            acceptance_specs={BUILD_ID: _acceptance_specs()[BUILD_ID]}
        )
        with self.assertRaises(ProjectWorkflowError):
            build_specification(service, self.registry, BUILD_FAILING_ID)
        self.assertEqual(self.files_on_disk(), ["preexisting.txt"])

    def test_an_uncomposed_declaration_is_refused(self) -> None:
        service = self.service()
        with self.assertRaises(UnknownWorkflowError):
            build_specification(service, self.registry, "not.composed")
        with self.assertRaises(UnknownWorkflowError):
            self.run_flow(service, "not.composed")
        self.assertEqual(self.files_on_disk(), ["preexisting.txt"])

    def test_mismatched_build_and_verification_declarations_are_refused(self) -> None:
        """The new composition check: a mismatched binding fails closed.

        The build declaration runs ``python``; the verification declaration runs
        ``python3``. An operator who bound them together by mistake would
        otherwise discover it only as a mid-run denial, or would verify the
        project in a perimeter the build never touched. Composition must refuse
        before any scope is frozen or any process starts.
        """
        registry = _mismatch_registry()
        service = self.service(
            profile=api_profile(commands=("python", "python3")),
            declarations=_mismatched_declarations(),
        )
        with self.assertRaises(ProjectWorkflowError) as ctx:
            build_specification(service, registry, BUILD_ID)
        self.assertIn("executable", str(ctx.exception))
        self.assertIn("python3", str(ctx.exception))
        with self.assertRaises(ProjectWorkflowError):
            run_project_workflow(service, registry, BUILD_ID)
        self.assertEqual(self.files_on_disk(), ["preexisting.txt"])


class ApprovalTests(WorkflowTestCase):
    def test_without_approval_the_forbidden_action_is_not_performed(self) -> None:
        """A required approval that is never granted executes nothing.

        The policy requires approval for every execution and no resolver is
        configured, so the action must not happen and the run must not be
        reported as a success. On this path the run parks in
        ``WAITING_FOR_APPROVAL`` rather than failing, which is the acceptance
        slice's documented behaviour and differs from the ``run_task`` /
        ``run_declared_verification`` paths, where a missing approval policy is
        itself reported as ``approval_policy_required``.
        """
        service = self.service(approval_policy=ApprovalPolicy(("execute",)))
        result = self.run_flow(service)

        self.assertEqual(result.state, "WAITING_FOR_APPROVAL")
        self.assertFalse(result.success)
        self.assertEqual(result.acceptance_status, "not_evaluated")
        self.assertIsNone(result.verification)
        self.assertEqual(result.created_files, ())
        self.assertEqual(self.files_on_disk(), ["preexisting.txt"])

        # The refusal is a recorded, inspectable event, not a silent no-op.
        record = result.run_record
        denied = [
            dict(event.metadata)
            for event in record.events
            if event.event_type.name == "EXECUTION_DENIED"
        ]
        self.assertTrue(denied)
        self.assertEqual(denied[-1].get("outcome_status"), "APPROVAL_WAITING")
        self.assertEqual(denied[-1].get("reason"), "approval_waiting")

    def test_an_explicit_approval_at_the_boundary_lets_the_action_run(self) -> None:
        """The positive control for the previous test.

        A configured resolver is asked for a real request carrying the run and
        invocation identity and the intent fingerprint, and only after it
        answers APPROVED does the action run and the project appear.
        """
        resolver = _CapturingApprovalResolver()
        service = self.service(
            approval_policy=ApprovalPolicy(("execute",)), approval_resolver=resolver
        )
        result = self.run_flow(service)

        self.assertTrue(resolver.requests, "the approval boundary was never asked")
        # The approval boundary is asked once per dispatch: once for the build
        # action and once for the declared verification command. Each request
        # carries the run it belongs to, so an approval can never be replayed
        # from one run onto another.
        asked_run_ids = [request.run_id for request in resolver.requests]
        self.assertIn(result.build_run_id, asked_run_ids)
        for request in resolver.requests:
            self.assertTrue(request.invocation_id)
            self.assertTrue(request.intent_fingerprint)
            # Requests are bound to a run this workflow actually performed.
            self.assertTrue(
                request.run_id.startswith(("run-accept-", "run-verify-")),
                request.run_id,
            )

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.acceptance_status, "pass")
        for relative in PROJECT_FILES:
            self.assertTrue(
                (self.root / relative).is_file(),
                f"{relative} should exist after an approved run",
            )

    def test_a_rejected_approval_executes_nothing(self) -> None:
        resolver = _CapturingApprovalResolver(decision=ApprovalState.REJECTED)
        service = self.service(
            approval_policy=ApprovalPolicy(("execute",)), approval_resolver=resolver
        )
        result = self.run_flow(service)

        self.assertFalse(result.success)
        self.assertEqual(self.files_on_disk(), ["preexisting.txt"])
        self.assertEqual(result.created_files, ())

    def test_the_in_memory_resolver_ignores_an_approval_for_another_run(self) -> None:
        """An approval is bound to one run and one invocation.

        ``InMemoryApprovalResolver`` is the existing operator-facing grant
        mechanism. An entry pre-submitted for a different run must not authorize
        this run's execution, which is the property that makes granting approval
        a real decision rather than a global switch.
        """
        resolver = InMemoryApprovalResolver()
        resolver.submit(
            "run-somewhere-else",
            "invocation-somewhere-else",
            ApprovalState.APPROVED,
            intent_fingerprint="a" * 64,
        )
        service = self.service(
            approval_policy=ApprovalPolicy(("execute",)), approval_resolver=resolver
        )
        result = self.run_flow(service)

        self.assertFalse(result.success)
        self.assertEqual(result.state, "WAITING_FOR_APPROVAL")
        self.assertEqual(self.files_on_disk(), ["preexisting.txt"])


class WorkspaceContainmentTests(WorkflowTestCase):
    """What the workspace constraint does and does not do.

    Two different facts are asserted separately, because conflating them would
    overstate the guarantee:

    * the *authority* boundary really does reject a working directory that
      resolves outside the workspace root, and no process is started;
    * the child process itself is **not** operating-system sandboxed, so a
      declared command that writes an absolute or ``..``-relative path outside
      the workspace can still create that file. The workflow still reports the
      run as a failure, because the workspace facts it must satisfy are absent.
    """

    def test_a_working_directory_outside_the_workspace_is_rejected_by_authority(
        self,
    ) -> None:
        """The authority boundary denies an escaping working directory.

        This is the workspace constraint that really exists: a request whose
        working directory resolves outside the frozen root is denied by
        ``RunScope`` inside the coordinator, and nothing is spawned. The
        request is built here exactly as the declared-verification factory would
        build it, with only the working directory changed -- the declaration
        itself refuses such a path, so this proves the *second* line of defence.
        """
        from dataclasses import replace as dc_replace

        from app.execution.authorizer import ExecutionCoordinator
        from app.execution.adapter import LocalExecutionAdapter
        from app.execution.request import ExecutionRequest, ExecutionStatus
        from app.execution.declaration import to_execution_request
        from app.runtime.run_scope import RunScope
        from app.tools.acceptance import AcceptanceCriterion

        service = self.service()
        declaration = service.declarations[VERIFY_ID]
        request = to_execution_request(
            declaration, profile=api_profile(), run_id="run-escape-probe"
        )
        request = dc_replace(request, working_directory="..")

        scope = RunScope(
            run_id="run-escape-probe",
            workspace=self.workspace,
            execution_profile=api_profile(),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(AcceptanceCriterion("probe", "probe"),),
        )
        scope.freeze()
        coordinator = ExecutionCoordinator(LocalExecutionAdapter(isolate_workspace=False))
        result = coordinator.execute(
            request,
            workspace_root=self.root,
            run_id="run-escape-probe",
            allowed_commands=frozenset({"python"}),
            run_scope=scope,
        )

        self.assertIs(result.status, ExecutionStatus.DENIED)
        reason = str(result.metadata.get("denial_reason"))
        self.assertIn(reason, {"run_scope_violation", "workspace_root_mismatch"})
        self.assertIn("leaves the workspace boundary", result.stderr)
        # Nothing was spawned into the escaping directory.
        self.assertEqual(self.files_on_disk(), ["preexisting.txt"])

    def test_a_declared_command_that_writes_outside_is_reported_as_a_failure(
        self,
    ) -> None:
        """The escape is not hidden, and the workspace facts are not satisfied.

        The command writes ``../forge_outside.txt``. The process is not
        OS-sandboxed, so the write succeeds -- and the workflow reports the run
        as a failure because none of the required workspace facts exist. What is
        claimed here is the *report*, not a kernel-level containment guarantee
        (which this codebase states it does not provide).
        """
        outside = (self.base / "forge_outside.txt").resolve()
        if outside.exists():  # pragma: no cover - defensive cleanup
            outside.unlink()

        service = self.service()
        result = self.run_flow(service, BUILD_OUTSIDE_ID)

        self.assertFalse(result.success)
        self.assertEqual(result.acceptance_status, "fail")
        self.assertEqual(result.created_files, ())
        self.assertEqual(self.files_on_disk(), ["preexisting.txt"])
        build_step = [s for s in result.steps if s.step == "build"][0]
        self.assertFalse(build_step.success)
        self.assertIsNotNone(result.error)
        # Stated as an observed fact of this environment, not as a guarantee the
        # code makes.
        self.assertTrue(
            outside.exists(),
            "the child process is not OS-sandboxed, so this write is expected "
            "to land outside the workspace",
        )


class SuccessfulWorkflowTests(WorkflowTestCase):
    def test_successful_run_is_confirmed_by_real_verification_and_acceptance(
        self,
    ) -> None:
        """The full vertical slice, end to end.

        ``state`` is ``LIMIT_REACHED`` on the success path: the acceptance slice
        is bounded to two actions (one execution plus its verification) while
        ``COMPLETE_RUN`` would need a third, so the loop terminates on its action
        bound with ``success=True`` and a PASS verdict. That naming is a
        documented property of the existing slice, not something this workflow
        changes; the outcome is asserted through ``success`` and the acceptance
        verdict.
        """
        service = self.service()
        result = self.run_flow(service)

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.acceptance_status, "pass")
        self.assertEqual(result.run_id, result.build_run_id)
        build_step = [s for s in result.steps if s.step == "build"][0]
        self.assertEqual(build_step.state, "LIMIT_REACHED")
        self.assertEqual(build_step.acceptance_status, "pass")
        # The acceptance verdict and its per-criterion results are durable.
        acceptance_events = [
            dict(event.metadata)
            for event in result.run_record.events
            if event.event_type.name == "ACCEPTANCE_COMPLETED"
        ]
        self.assertTrue(acceptance_events)
        self.assertEqual(acceptance_events[-1]["status"], "pass")
        self.assertEqual(acceptance_events[-1]["code"], "required_criteria_passed")
        self.assertEqual(
            result.run_record.snapshot.project_state.get("acceptance_status"), "pass"
        )

    def test_verification_really_executes_the_generated_test_suite(self) -> None:
        """The verification result is a real process outcome, not a string.

        The generated project's own test suite is executed as a second declared
        command. A suite that runs and passes writes ``Ran 2 tests`` and
        ``OK`` to stderr, and exits 0. Both facts are required, because exit 0
        alone would also be produced by a command that ran no tests at all.
        """
        service = self.service()
        result = self.run_flow(service)

        verification = result.verification
        self.assertIsNotNone(verification)
        self.assertEqual(verification.command, VERIFY_COMMAND)
        self.assertEqual(verification.outcome_status, "EXECUTION_SUCCESS")
        self.assertEqual(verification.observed_exit_code, 0)
        self.assertEqual(verification.declared_expected_exit_code, 0)
        self.assertTrue(verification.success, verification.messages)
        diagnostics = f"{verification.stdout}\n{verification.stderr}"
        self.assertIn("Ran 2 tests", diagnostics)
        self.assertIn("OK", diagnostics)
        # Two tests were really collected and run.
        test_file = (self.root / "tests" / "test_mod.py").read_text(encoding="utf-8")
        self.assertEqual(test_file.count("def test_"), 2)

    def test_created_files_are_measured_against_the_files_on_disk(self) -> None:
        service = self.service()
        result = self.run_flow(service)

        for relative in result.created_files:
            self.assertTrue(
                (self.root / relative).exists(),
                f"{relative} was reported as created but is not on disk",
            )
        self.assertEqual(
            sorted(result.created_files),
            sorted(set(PROJECT_FILES)),
            "created_files must be exactly the files the action added",
        )
        self.assertNotIn("preexisting.txt", result.created_files)
        self.assertEqual(result.deleted_files, ())
        self.assertTrue((self.root / "preexisting.txt").is_file())

    def test_the_result_is_readable_through_the_existing_run_interface(self) -> None:
        """The same durable history the existing API route reads back."""
        service = self.service()
        result = self.run_flow(service)

        record = service.get_run_record(result.build_run_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.get("run_id"), result.build_run_id)
        self.assertEqual(record.get("task_id"), f"task-accept-{BUILD_ID}")
        self.assertTrue(record.get("event_count", 0) > 0)

        # And the verification run is equally durable.
        verification_record = service.get_run_record(result.verification.run_id)
        self.assertIsNotNone(verification_record)
        self.assertEqual(verification_record.get("run_id"), result.verification.run_id)

        # The documented honest-scope sentence travels with the result.
        self.assertIn(DECLARED_COMMAND_IS_NOT_A_GENERATOR, result.to_dict()["messages"])

    def test_the_response_carries_every_required_field(self) -> None:
        service = self.service()
        result = self.run_flow(service)
        payload = result.to_dict()
        for field_name in (
            "run_id",
            "state",
            "acceptance_status",
            "verification",
            "created_files",
            "deleted_files",
            "error",
            "run_record",
        ):
            self.assertIn(field_name, payload)
        # The task-run contract this workflow must not redefine is unchanged.
        from app.api.models import TaskRunResponse
        import dataclasses

        self.assertEqual(
            {f.name for f in dataclasses.fields(TaskRunResponse)},
            {
                "run_id",
                "task_id",
                "project_id",
                "state",
                "success",
                "output",
                "error",
                "tokens",
                "cost",
                "duration_seconds",
                "provider_used",
                "model_used",
                # The outcome gate's contract: the provider-neutral outcome, the
                # questions a clarification answer asked, and the four independent
                # save indicators (plus the file count they describe).
                "outcome",
                "clarification_questions",
                "needs_clarification",
                "run_record_saved",
                "project_created",
                "project_files_created",
                "artifact_manifest_saved",
                "files_created",
                # The write mode this run used ("" when it wrote nothing, otherwise
                # MOCK_DETERMINISTIC or REAL_PROVIDER_EXPLICIT). Additive, and stated
                # rather than inferred from the file count.
                "write_mode",
                "cost_tier",
                "estimated_cost",
                # The intent the answer declared (structured intent v0.1): an empty
                # mapping means "no intent declared". Additive, like the fields
                # above; nothing that existed before it changed.
                "model_intent",
                # The lifecycle project this run created or was attached to (the
                # `proj-…` record), or None. Additive and last: `project_id` remains
                # the workspace/Core project it always was.
                "lifecycle_project_id",
                # The clarification round (0 = no context, 1 = may ask, 2 = forced
                # final). Additive: the backend's own count, so a client never
                # infers a round from the words of an answer.
                "clarification_round",
                # The automatic routing decision (provider · model · tier · why).
                # Additive and last, like every field above it: an empty mapping means
                # this entry point made no decision.
                "model_choice",
            },
        )


class FailingWorkflowTests(WorkflowTestCase):
    def test_a_failing_action_is_never_reported_as_success(self) -> None:
        """The action fails, so the workflow reports failure and no acceptance.

        The declared creation command exits non-zero, so the acceptance slice
        never reaches its verification stage and ``acceptance_status`` is
        ``not_evaluated`` -- an absent verdict, never a pass. The workflow still
        reports the *real* outcome of the declared verification command, which
        genuinely could not run because the project was never created; that
        failure is stated rather than hidden.
        """
        failing = dict(_declarations())
        failing[BUILD_ID] = ExecutionDeclaration(
            declaration_id=BUILD_ID,
            command=("python", "-c", "import sys; print('boom'); sys.exit(3)"),
            profile_id="api-default",
            working_directory=".",
            intent="An operator-declared action that fails.",
        )
        service = self.service(
            declarations=failing,
            acceptance_specs={BUILD_ID: _acceptance_specs()[BUILD_ID]},
        )
        result = self.run_flow(service)

        self.assertFalse(result.success)
        self.assertEqual(result.acceptance_status, "not_evaluated")
        self.assertIsNotNone(result.error)
        self.assertEqual(self.files_on_disk(), ["preexisting.txt"])
        self.assertEqual(result.created_files, ())
        build_step = result.steps[0]
        self.assertFalse(build_step.success)
        self.assertEqual(build_step.state, "FAILED")
        # The verification command really ran and really failed, and the
        # workflow reports that rather than claiming a pass.
        self.assertIsNotNone(result.verification)
        self.assertFalse(result.verification.success)
        self.assertFalse(result.success)

    def test_a_failing_verification_is_never_reported_as_success(self) -> None:
        """The build succeeds; the project's own tests fail.

        This is the case a naive implementation would hide. The action creates
        every file the acceptance gate looks for, so the frozen workspace-fact
        acceptance PASSES. The generated module is wrong on purpose, so the
        project's own test suite exits 1 and the workflow must report failure
        while still reporting the acceptance verdict it really obtained.
        """
        service = self.service()
        result = self.run_flow(service, BUILD_FAILING_ID)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "verification_exit_code_mismatch")
        self.assertEqual(result.state, "VERIFICATION_FAILED")
        # The frozen workspace facts really did hold: the acceptance verdict is
        # reported as obtained, not re-labelled to match the verification.
        self.assertEqual(result.acceptance_status, "pass")

        verification = result.verification
        self.assertIsNotNone(verification)
        self.assertEqual(verification.observed_exit_code, 1)
        self.assertFalse(verification.success)
        self.assertEqual(verification.outcome_status, "EXECUTION_FAILURE")
        diagnostics = f"{verification.stdout}\n{verification.stderr}"
        self.assertIn("FAILED", diagnostics)

        # The project files really were created before the failing verification.
        for relative in PROJECT_FILES:
            self.assertTrue((self.root / relative).is_file())
        self.assertEqual(sorted(result.created_files), sorted(set(PROJECT_FILES)))


class ApprovalSliceSeparationTests(WorkflowTestCase):
    def test_creation_and_verification_are_two_distinct_runs(self) -> None:
        """Two declared commands, two runs, two durable histories.

        The creation action is judged by the acceptance slice; the verification
        command is a separate declared run through the existing
        ``run_declared_verification`` entry point. They are deliberately not
        merged, so neither verdict is presented as the other.
        """
        service = self.service()
        result = self.run_flow(service)

        self.assertNotEqual(result.build_run_id, result.verification_run_id)
        self.assertTrue(result.build_run_id.startswith("run-accept-"))
        self.assertTrue(result.verification_run_id.startswith("run-verify-"))
        self.assertEqual(len(result.steps), 2)
        self.assertEqual(result.steps[0].step, "build")
        self.assertEqual(result.steps[1].step, "verify")
        # Verification ran after the build: its relative snapshot contains only
        # what verification itself touched, which is nothing but byte-code.
        self.assertEqual(result.verification_deleted_files, ())

    def test_an_idempotency_key_gives_each_step_its_own_durable_operation(self) -> None:
        """A workflow replay reports the recorded outcome instead of re-running.

        The existing run-level idempotency applies to the two trusted entry points
        separately, so the workflow derives one key per step. Replaying with the
        same key must not execute the creation command a second time and must not
        re-run the test suite; the second delivery is reported as a refusal with
        its own recorded reason, and it is never reported as a success.
        """
        service = self.service()
        first = run_project_workflow(
            service, self.registry, BUILD_ID, idempotency_key="workflow-key-0001"
        )
        self.assertTrue(first.success, first.error)
        created = first.created_files

        second = run_project_workflow(
            service, self.registry, BUILD_ID, idempotency_key="workflow-key-0001"
        )

        self.assertFalse(
            second.success,
            "a replayed workflow must not be reported as a fresh success",
        )
        self.assertIsNotNone(second.error)
        # Nothing new was created: the files on disk are the ones the first run
        # produced, and the measured diff for the replay is empty.
        self.assertEqual(second.created_files, ())
        self.assertEqual(
            sorted(self.files_on_disk()),
            sorted({"preexisting.txt", *created}),
        )
        # The durable histories of both runs exist and are distinct runs.
        self.assertTrue(service.get_run_record(first.build_run_id))
        self.assertTrue(service.get_run_record(second.run_id))


if __name__ == "__main__":
    unittest.main()
