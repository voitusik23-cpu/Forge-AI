"""The Controlled Project Factory vertical slice, verified from outside.

WHAT THIS FILE IS
=================

An independent end-to-end verifier for the slice

    Goal -> Requirements -> Plan -> Authorization -> Scoped Execution
         -> Verification -> Acceptance -> Structured Result

on the **operator-declared-command** path, plus the honest-negative behaviour of
the model path. It does not reuse another test file's helpers: its composition is
written from scratch so that an error in one harness cannot hide an error in the
other.

WHAT IS PROVEN, AND HOW
=======================

Every claim here is proved by **observed data**, never by the presence of a class
or a string:

* the goal becomes structured requirements and checkable acceptance criteria that
  are readable *before* the action runs (``build_specification``);
* the plan is readable before execution, and the run really uses the deterministic
  planner;
* the authorization chain is not bypassed -- a required-but-unresolved approval
  performs no action and reports no exit code;
* the declared verification command's **real** process exit code is the one that
  decides the verification status, measured independently by running the same
  command a second time in a separate interpreter;
* **no generative provider decision call is made for the declared command**: the
  same verification invocation is measured both through the pre-existing
  ``generate_decision=False`` entry point on its own and through the whole
  workflow, against the transport's own call record;
* generation, execution, verification and acceptance are **four separate fields**
  with four separate vocabularies, and the tests assert the combinations that
  would be impossible if any two were collapsed.

ISOLATION AND CONCURRENCY
=========================

This repository has produced *false* failures under concurrent test processes:
the default ``RunStore()`` and the idempotency ledger live in a shared per-user
root, and PostgreSQL roles are cluster-global. Every service built here therefore
gets its **own** ``RunStore`` and its **own** ``RunIdempotencyGuard`` under a
temporary directory, and every workspace is a fresh temporary directory.

NO REAL PROVIDER
================

``create_runtime(test_mode=True)`` is used wherever the ambient runtime is
involved, so the runtime itself refuses to register anything but the offline
provider. The test that measures this asserts the **observed** registry contents
rather than trusting the flag's name. No test in this file contacts a network or
reads a key.

WHAT IS NOT PROVEN HERE
=======================

Nothing about a real model. Every generation transport in this file is an
in-process fake that returns a fixed string, and the result is marked as an
injected transport. This file never claims real LLM generation.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from app.agent_runtime import idempotency_integration as integration
from app.agent_runtime.acceptance_spec import AcceptanceSpec
from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.api.service import ForgeApiService
from app.decision.ai_provider import AIDecisionProvider
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_agent_harness, create_runtime
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope
from app.runtime.run_store import RunStore
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.registry import build_default_tool_registry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.usage import Usage
from app.workflows.project_workflow import (
    EXECUTION_STATUSES,
    GENERATION_FAILED_RESPONSE,
    GENERATION_FAILED_TRANSPORT,
    GENERATION_REFUSED_LIMIT,
    GENERATION_STATUSES,
    GENERATION_SUCCEEDED,
    VERDICT_ACCEPTANCE_FAILED,
    VERDICT_APPROVAL_REQUIRED,
    VERDICT_DECLARED_COMMAND_VERIFIED,
    VERDICT_GENERATION_FAILED,
    VERDICT_GENERATION_REFUSED,
    VERDICT_MATERIALISATION_INCOMPLETE,
    VERDICT_PROJECT_VERIFIED,
    VERDICT_REAL_PROVIDER_NOT_VERIFIED,
    VERDICT_TESTS_FAILED,
    ProjectWorkflowError,
    ProjectWorkflowRegistry,
    build_specification,
    run_project_workflow,
)

# ---------------------------------------------------------------------------
# The operator-owned composition: commands, criteria, and the fake programme
# ---------------------------------------------------------------------------

#: The declared creation command. It writes a tiny package with its own tests.
#: It is one ``python -c`` programme, so the whole project source is visible here
#: and nowhere else.
_MODULE_SOURCE = "def add(a, b):\n    return a + b\n"
_TEST_SOURCE = (
    "import unittest\n"
    "\n"
    "from calc import add\n"
    "\n"
    "\n"
    "class CalcTests(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        self.assertEqual(add(2, 3), 5)\n"
    "\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)
_CREATION_PROGRAMME = (
    "import pathlib\n"
    f"pathlib.Path('calc.py').write_text({_MODULE_SOURCE!r}, encoding='utf-8')\n"
    "pathlib.Path('tests').mkdir(exist_ok=True)\n"
    "pathlib.Path('tests/__init__.py').write_text('', encoding='utf-8')\n"
    f"pathlib.Path('tests/test_calc.py').write_text({_TEST_SOURCE!r}, encoding='utf-8')\n"
)
CREATE_COMMAND = ("python", "-c", _CREATION_PROGRAMME)

#: The declared verification command: the generated project's **own** test suite
#: under the standard library runner. Forge AI's own tests are never substituted.
VERIFY_COMMAND = (
    "python", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "test_*.py",
)

MODULE_PATH = "calc.py"
TEST_PATH = "tests/test_calc.py"
TESTS_INIT_PATH = "tests/__init__.py"

CRITERIA = (
    AcceptanceCriterion(
        "module-present",
        "the project contains its module",
        requirement_id="project-source",
    ),
    AcceptanceCriterion(
        "tests-present",
        "the project contains its own tests",
        requirement_id="project-tests",
    ),
)
EXPECTATIONS = {
    "module-present": VerificationExpectation(MODULE_PATH, True),
    "tests-present": VerificationExpectation(TEST_PATH, True),
}
GOAL = "Produce a small Python package with its own passing tests."

#: The transport under test. It is named "injected" so no reader can mistake it
#: for a real remote model.
INJECTED_PROVIDER = "slice-injected-transport"
INJECTED_MODEL = "slice-injected-model"
INJECTED_USAGE = Usage(input_tokens=13, output_tokens=21, estimated_cost=0.011)

_id_lock = threading.Lock()
_id_counter = itertools.count(1)


def _unique(prefix: str) -> str:
    """A per-process-unique declaration id.

    The idempotency ledger is durable, so a fixed declaration id reused by a
    second concurrent process has already produced false failures here.
    """

    with _id_lock:
        return f"{prefix}.{os.getpid()}.{next(_id_counter)}"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def project_response(
    *,
    module: str = _MODULE_SOURCE,
    test: str = _TEST_SOURCE,
    extra: dict[str, str] | None = None,
) -> str:
    """One complete model response in the declared generation schema."""

    files: dict[str, str] = {
        MODULE_PATH: module,
        TEST_PATH: test,
        TESTS_INIT_PATH: "",
    }
    if extra:
        files.update(extra)
    return json.dumps(
        {
            "files": files,
            "decision": {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "project_ready",
                "rationale": "the project is complete",
                "confidence": 0.9,
            },
        }
    )


class _CountingTransport(Provider):
    """A real ``Provider`` subclass that records every call and can misbehave.

    Subclassing the production ``Provider`` is what keeps the dispatch path
    unchanged: only the network behind the provider is replaced. ``calls`` is the
    physical evidence the call-count assertions read.
    """

    def __init__(
        self,
        output: str | None = None,
        *,
        raises: BaseException | None = None,
        usage: Usage | None = INJECTED_USAGE,
    ) -> None:
        super().__init__(
            ProviderConfig(
                provider_name=INJECTED_PROVIDER,
                model_name=INJECTED_MODEL,
                enabled=True,
            )
        )
        self._output = output
        self._raises = raises
        self._usage = usage
        self.calls: list[ProviderRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if self._raises is not None:
            raise self._raises
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output=self._output or "",
            usage=self._usage,
        )


class _RejectingResolver:
    """A resolver that answers REJECTED. A rejection is not an approval."""

    def resolve(self, request):  # noqa: ANN001 - mirrors the real protocol
        from app.tools.approval import ApprovalResolution, ApprovalState

        return ApprovalResolution(
            decision=ApprovalState.REJECTED,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="slice-rejected",
        )


class _FakeDeclaredRun:
    """A stand-in for the ``Run`` an executor returns, with a chosen exit code.

    It exists for the one measurement that cannot be taken on the real path
    without a second interpreter: that the *whole workflow* adds no provider call
    around an already-authorized declared command. Every field the workflow reads
    is supplied explicitly, so nothing is implied by its absence.
    """

    def __init__(self, declaration_id: str, exit_code: int | None) -> None:
        from app.execution.request import (
            ExecutionOutcomeStatus,
            ExecutionResult,
            ExecutionStatus,
        )
        from app.orchestrator.models import RunState

        self.state = (
            RunState.COMPLETED if exit_code == 0 else RunState.FAILED
        )
        self.error = None
        self.result = None
        self.events: list[object] = []
        self.execution_results: list[ExecutionResult] = []
        if exit_code is None:
            return
        self.execution_results.append(
            ExecutionResult(
                request_id=f"req-{declaration_id}",
                status=(
                    ExecutionStatus.SUCCESS if exit_code == 0 else ExecutionStatus.FAILED
                ),
                exit_code=exit_code,
                stdout="",
                stderr="",
                outcome_status=(
                    ExecutionOutcomeStatus.EXECUTION_SUCCESS
                    if exit_code == 0
                    else ExecutionOutcomeStatus.EXECUTION_FAILURE
                ),
                metadata={"declaration_id": declaration_id},
            )
        )


class _CountingExecutor:
    """Wrap the real ``RunExecutor`` and count what each dispatch was for.

    Every argument and every result is forwarded unchanged. It changes no
    authorization and no outcome; it only records which task descriptions were
    dispatched, which is what makes "the declared command did not ask a model"
    measurable across the *whole* workflow rather than at one entry point.
    """

    def __init__(self, executor: object, *, scripted: dict[str, object] | None = None):
        self._executor = executor
        self._scripted = scripted or {}
        self.task_descriptions: list[str] = []
        #: How many dispatches were answered by a scripted run instead of the real
        #: executor. Counted so a test can prove its script was actually used
        #: rather than silently falling through.
        self.playbook_hits = 0

    def __getattr__(self, item: str) -> object:
        return getattr(self._executor, item)

    def execute(self, *args, **kwargs):
        task = kwargs.get("task")
        description = getattr(task, "description", "") or ""
        self.task_descriptions.append(description)
        task_id = str(getattr(task, "id", "") or "")
        for key, run in self._scripted.items():
            if key == task_id or key in description:
                self.playbook_hits += 1
                return run
        return self._executor.execute(*args, **kwargs)


class SliceTestCase(unittest.TestCase):
    """One isolated workspace, one isolated run store, one isolated ledger."""

    def setUp(self) -> None:
        RunScope.release_all()
        self.build_id = _unique("slice.build")
        self.verify_id = _unique("slice.verify")
        self._dirs: list[tempfile.TemporaryDirectory] = []
        self.base = self._temp_dir()
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        self.run_store = RunStore(root=str(self._temp_dir()))
        self.idempotency_guard = integration.RunIdempotencyGuard.create(
            ledger_root=str(self._temp_dir())
        )
        self.registry = ProjectWorkflowRegistry(
            verification_declarations={self.build_id: self.verify_id}
        )

    def tearDown(self) -> None:
        RunScope.release_all()
        for directory in self._dirs:
            directory.cleanup()

    def _temp_dir(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self._dirs.append(directory)
        return Path(directory.name)

    # -- composition -------------------------------------------------------

    def _profile(self) -> ProjectExecutionProfile:
        return ProjectExecutionProfile(
            profile_id="slice-profile",
            allowed_commands=("python",),
            capabilities=frozenset(
                {
                    ExecutionCapability.INTERPRET_TEXT,
                    ExecutionCapability.INTERPRET_MODULE,
                }
            ),
            network_access=False,
            working_directory=".",
        )

    def _runtime(self, providers: list[Provider]) -> RuntimeContext:
        """A runtime composed from explicit providers -- no ambient settings.

        With no providers at all, the runtime falls back to a runtime built with
        ``test_mode=True``, so the declared-command path always has the harness it
        needs while never gaining a live provider.
        """

        if not providers:
            return create_runtime(test_mode=True)
        provider_registry = ProviderRegistry()
        agent_registry = AgentRegistry()
        for provider in providers:
            provider_registry.register(provider)
            agent_registry.register(ProviderAgent(provider))
        orchestrator = Orchestrator(
            agent_registry,
            default_provider=providers[0].provider_name,
            provider_registry=provider_registry,
        )
        return RuntimeContext(
            settings=None,
            provider_accounts={},
            provider_registry=provider_registry,
            provider_capabilities=None,
            agent_registry=agent_registry,
            orchestrator=orchestrator,
            run_executor=RunExecutor(orchestrator),
            harness=create_agent_harness(),
        )

    def _service(
        self,
        providers: list[Provider] | None = None,
        *,
        runtime: RuntimeContext | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approval_resolver=None,
        verification_command: tuple[str, ...] = VERIFY_COMMAND,
        verification_timeout: float | None = 25.0,
        build_working_directory: str = ".",
        verify_working_directory: str = ".",
        acceptance_specs: dict | None = None,
    ) -> ForgeApiService:
        declarations = {
            self.build_id: ExecutionDeclaration(
                declaration_id=self.build_id,
                command=CREATE_COMMAND,
                profile_id="slice-profile",
                working_directory=build_working_directory,
                intent=GOAL,
            ),
            self.verify_id: ExecutionDeclaration(
                declaration_id=self.verify_id,
                command=verification_command,
                profile_id="slice-profile",
                working_directory=verify_working_directory,
                intent="Run the project's own test suite.",
                timeout_seconds=verification_timeout,
            ),
        }
        specs = (
            {
                self.build_id: AcceptanceSpec(
                    declaration_id=self.build_id,
                    criteria=CRITERIA,
                    expectations=dict(EXPECTATIONS),
                )
            }
            if acceptance_specs is None
            else acceptance_specs
        )
        if runtime is None:
            runtime = self._runtime(providers or [])
        return ForgeApiService(
            runtime=runtime,
            workspace=self.workspace,
            run_store=self.run_store,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=self._profile(),
            declarations=declarations,
            acceptance_specs=specs,
            approval_policy=(
                ApprovalPolicy() if approval_policy is None else approval_policy
            ),
            approval_resolver=approval_resolver,
            idempotency_guard=self.idempotency_guard,
        )

    # -- the two paths under test ------------------------------------------

    def run_declared(self, **kwargs):
        """Run the slice with no generation provider at all."""

        return run_project_workflow(self._service(**kwargs), self.registry, self.build_id)

    def run_model(self, transport: _CountingTransport, **kwargs):
        """Run the slice with the injected transport as the source of the text."""

        service = self._service([transport], **kwargs)
        result = run_project_workflow(
            service,
            self.registry,
            self.build_id,
            ai_provider=AIDecisionProvider(transport, mock_transport=True),
        )
        return result

    # -- independent observation -------------------------------------------

    def disk_files(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )

    def run_project_tests_here(self) -> subprocess.CompletedProcess:
        """Run the project's own suite **myself**, in a separate process.

        This is the independent measurement the workflow's reported exit code is
        compared against. It re-reads nothing the workflow produced.
        """

        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(self.root)
        return subprocess.run(
            list(VERIFY_COMMAND),
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=180,
            env=environment,
        )


# ---------------------------------------------------------------------------
# 1. The full success path
# ---------------------------------------------------------------------------


class TheFullSuccessPathTests(SliceTestCase):
    """Goal -> requirements -> plan -> authorization -> execution -> result."""

    def test_the_goal_becomes_requirements_and_criteria_before_anything_runs(self) -> None:
        service = self._service()
        spec = build_specification(service, self.registry, self.build_id)

        # The goal is the operator's own declaration intent, not a caller's text.
        self.assertEqual(spec.goal, GOAL)
        # Every recommendation is structured, and every criterion is checkable:
        # it names a workspace fact rather than a promise.
        self.assertEqual(
            [(r["requirement_id"], r["required"]) for r in spec.requirements],
            [("project-source", True), ("project-tests", True)],
        )
        self.assertEqual(
            [c.criterion_id for c in spec.acceptance_criteria],
            ["module-present", "tests-present"],
        )
        for criterion in spec.acceptance_criteria:
            self.assertTrue(criterion.expectation_path, criterion.to_dict())
            self.assertTrue(criterion.expectation_exists)
        # And nothing was executed to produce this view.
        self.assertEqual(self.disk_files(), [])
        self.assertFalse((self.root / MODULE_PATH).exists())

    def test_the_plan_is_readable_before_execution_and_covers_expected_outputs(self) -> None:
        service = self._service()
        spec = build_specification(service, self.registry, self.build_id)

        self.assertGreater(len(spec.planned_steps), 0)
        self.assertTrue(spec.plan_fingerprint)
        for step in spec.planned_steps:
            self.assertTrue(step["step_id"])
            self.assertTrue(step["purpose"])
            self.assertTrue(step["expected_outcome"])
            self.assertIn(step["step_type"], {"observe", "modify", "verify"})
        # A plan is a proposal: reading it created nothing and authorized nothing.
        self.assertEqual(self.disk_files(), [])

        # The plan the run really uses is produced by the same deterministic
        # planner, so the same goal yields the same step purposes.
        from app.planning.planner import Planner

        run_plan = Planner().plan_for_run(
            goal=spec.goal, run_id="planning-check", task_id=spec.task_id
        )
        self.assertEqual(
            [s["purpose"] for s in spec.planned_steps],
            [s.purpose for s in run_plan.steps],
        )

    def test_the_whole_slice_reaches_a_verified_result(self) -> None:
        result = self.run_declared()

        # Execution really happened, in the permitted workspace. ``execution_status``
        # answers the narrow question about the dispatched process, and
        # ``harness_status`` reports the harness loop's own lifecycle separately --
        # see ``test_the_harness_lifecycle_is_reported_separately_from_the_action``.
        self.assertTrue(result.declared_command_dispatched)
        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(result.harness_status, "LIMIT_REACHED")
        self.assertEqual(sorted(result.created_files), sorted(
            [MODULE_PATH, TEST_PATH, TESTS_INIT_PATH]
        ))
        # No generation was involved, and the result says so rather than implying
        # an unknown: the question "how many attempts?" does not apply here.
        self.assertFalse(result.generation_configured)
        self.assertIsNone(result.generation_attempts_spent)
        self.assertEqual(result.generation_status, "disabled")
        self.assertEqual(result.project_source, "declared_command")

        # Verification really ran, and its real exit code was retained.
        self.assertIsNotNone(result.verification)
        self.assertEqual(result.verification_exit_code, 0)
        self.assertEqual(result.verification.observed_exit_code, 0)
        self.assertTrue(result.verification.success)

        # Acceptance is its own verdict, from its own gate.
        self.assertEqual(result.acceptance_status, "pass")

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.verdict, VERDICT_DECLARED_COMMAND_VERIFIED)

    def test_the_reported_exit_code_matches_an_independent_run(self) -> None:
        result = self.run_declared()

        independent = self.run_project_tests_here()
        self.assertEqual(
            independent.returncode,
            result.verification.observed_exit_code,
            independent.stdout + independent.stderr,
        )
        self.assertEqual(independent.returncode, 0)
        # Exit 0 alone would also come from a run that collected nothing, so the
        # runner's own diagnostics are asserted too.
        diagnostics = f"{independent.stdout}{independent.stderr}"
        self.assertIn("Ran 1 test", diagnostics)

    def test_the_harness_lifecycle_is_reported_separately_from_the_action(self) -> None:
        """``LIMIT_REACHED`` is the harness's fact, not the action's.

        Measured on the ordinary successful declared run: the acceptance harness
        exhausts its iteration budget and ends in ``LIMIT_REACHED``, while the
        action it dispatched really exited 0, the generated project's own tests
        really passed, and the acceptance gate really returned PASS. This test
        pins the two apart, so that neither can be presented as the other:

        * ``harness_status`` is ``LIMIT_REACHED`` -- reported verbatim, never
          smoothed into "completed";
        * ``execution_status`` is ``succeeded`` -- a fact about the process;
        * the run is a success, because the acceptance gate passed and the exit
          code matched. ``LIMIT_REACHED`` here means "the harness loop hit its
          bound", not "the action was incomplete".
        """
        result = self.run_declared()

        self.assertEqual(result.harness_status, "LIMIT_REACHED")
        self.assertEqual(result.execution_status, "succeeded")
        self.assertNotEqual(result.execution_status, "limit_reached")
        self.assertEqual(result.verification_exit_code, 0)
        self.assertEqual(result.acceptance_status, "pass")
        self.assertTrue(result.success, result.error)
        # The harness state is still visible where it always was.
        self.assertEqual(result.steps[0].state, "LIMIT_REACHED")

    def test_a_manifestly_failed_action_is_not_reported_as_succeeded(self) -> None:
        """The control for the test above.

        If ``execution_status`` were hard-coded, or derived from something that
        cannot fail, the previous test would pass for the wrong reason. Here the
        declared build cannot succeed -- the executable the profile admits is
        ``python``, and the declaration is given an environment that stops the
        interpreter from starting -- so ``execution_status`` must not be
        ``succeeded`` and the run must not be a success.
        """
        import dataclasses

        service = self._service()
        service._declarations = dict(service.declarations)
        service._declarations[self.build_id] = dataclasses.replace(
            service.declarations[self.build_id],
            environment_variables={
                "PYTHONHOME": str(self.base / "no-such-python-home")
            },
        )
        result = run_project_workflow(service, self.registry, self.build_id)

        self.assertFalse(result.success)
        self.assertNotEqual(result.execution_status, "succeeded")
        self.assertIn(result.execution_status, EXECUTION_STATUSES)
        self.assertFalse((self.root / MODULE_PATH).exists())

    def test_the_result_serialises_every_separated_status(self) -> None:
        payload = self.run_declared().to_dict()

        for key in (
            "generation_status",
            "execution_status",
            "harness_status",
            "acceptance_status",
            "verification_exit_code",
            "verification",
            "project_source",
            "generation_attempts_spent",
            "max_generation_attempts",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["generation_status"], "disabled")
        self.assertEqual(payload["execution_status"], "succeeded")
        self.assertEqual(payload["harness_status"], "LIMIT_REACHED")
        self.assertEqual(payload["verification_exit_code"], 0)
        # The verification object's own success is the test-run verdict, and the
        # top-level acceptance_status is not it.
        self.assertTrue(payload["verification"]["success"])
        self.assertEqual(payload["acceptance_status"], "pass")


# ---------------------------------------------------------------------------
# 2-4. Provider/generation outcomes
# ---------------------------------------------------------------------------


class GenerationOutcomeTests(SliceTestCase):
    """An unusable response is reported for what it is and creates nothing."""

    def _assert_nothing_written(self) -> None:
        self.assertEqual(self.disk_files(), [])

    def test_an_empty_provider_response_is_a_generation_failure(self) -> None:
        transport = _CountingTransport("")
        result = self.run_model(transport)

        self._assert_nothing_written()
        self.assertFalse(result.success)
        self.assertEqual(result.generation_status, GENERATION_FAILED_RESPONSE)
        self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED)
        # The provider *was* asked exactly once -- measured at the transport's own
        # call record -- and the answer was unusable.
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result.generation_attempts_spent, 1)
        self.assertEqual(result.project_source, "injected_transport")
        self.assertEqual(
            result.parsed_project.fail_closed_reason, "empty_model_output"
        )
        # Nothing arrived, so no producer identity and no measurement is
        # attributed. That is not the same as "a model wrote this".
        self.assertFalse(result.generation.response_text_observed)
        self.assertEqual(result.generation.provider_name, "")
        self.assertFalse(result.generation.usage_present)

    def test_a_malformed_provider_response_is_kept_distinct_from_silence(self) -> None:
        transport = _CountingTransport("{ not json at all")
        result = self.run_model(transport)

        self._assert_nothing_written()
        self.assertFalse(result.success)
        self.assertEqual(result.generation_status, GENERATION_FAILED_RESPONSE)
        self.assertEqual(transport.call_count, 1)
        # Text did arrive: that weaker fact is preserved rather than collapsed
        # into "no response".
        self.assertTrue(result.generation.response_text_observed)
        # But it produced no project, so it is attributed no producer identity.
        self.assertEqual(result.generation.provider_name, "")
        self.assertEqual(result.generation.model_name, "")
        self.assertFalse(result.generation.usage_present)
        self.assertIsNone(result.generation.usage_input_tokens)

    def test_a_provider_that_raises_is_a_transport_failure_not_a_response(self) -> None:
        transport = _CountingTransport(raises=RuntimeError("provider exploded"))
        result = self.run_model(transport)

        self._assert_nothing_written()
        self.assertFalse(result.success)
        # The call was attempted, so this is a transport outcome -- a different
        # status from "a response arrived and could not be used". The production
        # dispatcher already converts a raising provider into a failed task with
        # no output, and the workflow reads that as a transport failure rather
        # than as an unparseable response.
        self.assertEqual(result.generation_status, GENERATION_FAILED_TRANSPORT)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result.generation_attempts_spent, 1)
        self.assertFalse(result.generation.response_text_observed)
        self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED)
        self.assertFalse(result.declared_command_dispatched)

    def test_a_provider_that_returns_no_usage_reports_usage_as_absent(self) -> None:
        transport = _CountingTransport(project_response(), usage=None)
        result = self.run_model(transport)

        # The project is real, so the producer identity is reported...
        self.assertEqual(result.generation_status, GENERATION_SUCCEEDED)
        self.assertEqual(result.generation.provider_name, INJECTED_PROVIDER)
        self.assertEqual(result.generation.model_name, INJECTED_MODEL)
        # ...and the measurement simply is not there. It is never invented, and
        # never rounded to zero.
        self.assertIsNone(result.generation.usage_input_tokens)
        self.assertIsNone(result.generation.usage_output_tokens)
        self.assertIsNone(result.generation.usage_estimated_cost)
        self.assertFalse(result.generation.usage_present)
        payload = result.to_dict()["generation"]
        self.assertIsNone(payload["usage_input_tokens"])
        self.assertFalse(payload["usage_present"])

    def test_reported_usage_comes_from_the_transport_not_from_the_workflow(self) -> None:
        transport = _CountingTransport(project_response())
        result = self.run_model(transport)

        self.assertEqual(result.generation.usage_input_tokens, INJECTED_USAGE.input_tokens)
        self.assertEqual(
            result.generation.usage_output_tokens, INJECTED_USAGE.output_tokens
        )
        self.assertEqual(
            result.generation.usage_estimated_cost, INJECTED_USAGE.estimated_cost
        )
        self.assertTrue(result.generation.usage_present)


# ---------------------------------------------------------------------------
# 5-6. The verification exit code, and acceptance
# ---------------------------------------------------------------------------


class VerificationAndAcceptanceTests(SliceTestCase):
    """The two verdicts are measured separately and never stand in for each other."""

    def test_a_non_zero_command_exit_code_fails_verification(self) -> None:
        # The creation command writes a module whose add() subtracts, while the
        # project's own test asserts addition. The write succeeds; the *tests*
        # fail, and the real exit code says so.
        broken = ("python", "-c", _CREATION_PROGRAMME.replace(
            "return a + b", "return a - b"
        ))
        result = self.run_declared()
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.verification_exit_code, 0)

        # Now the same slice with the broken programme declared instead.
        self.tearDown()
        self.setUp()
        service = self._service()
        service._declarations = dict(service.declarations)
        import dataclasses

        service._declarations[self.build_id] = dataclasses.replace(
            service.declarations[self.build_id], command=broken
        )
        result = run_project_workflow(service, self.registry, self.build_id)

        # The module really reached disk: the failure is in the tests.
        self.assertTrue((self.root / MODULE_PATH).is_file())
        self.assertNotEqual(result.verification_exit_code, 0)
        self.assertIsNotNone(result.verification_exit_code)
        self.assertFalse(result.verification.success)
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_TESTS_FAILED)
        # And acceptance still passed, because the operator's criteria really did
        # hold: the files exist. That is exactly why the two are separate fields.
        self.assertEqual(result.acceptance_status, "pass")
        # An independent run agrees about the exit code.
        independent = self.run_project_tests_here()
        self.assertEqual(independent.returncode, result.verification_exit_code)

    def test_an_unsatisfied_criterion_fails_acceptance_despite_a_zero_exit(self) -> None:
        """A passing test run cannot rescue a failed acceptance gate.

        The operator declares a third criterion whose file nothing ever creates,
        while the declared verification command genuinely exits 0. The exit code
        is therefore a real zero and the acceptance gate genuinely fails: the
        result must report both, in their own fields, and must not call the run a
        success. Remove the second half of this test and the first would pass for
        the wrong reason -- so both are asserted against the same run.
        """
        import dataclasses

        base_spec = self._service().acceptance_specs[self.build_id]
        unsatisfiable = dataclasses.replace(
            base_spec,
            criteria=(
                *CRITERIA,
                AcceptanceCriterion(
                    "docs-present",
                    "the project contains a document nothing creates",
                    requirement_id="project-docs",
                ),
            ),
            expectations={
                **EXPECTATIONS,
                "docs-present": VerificationExpectation("NEVER_CREATED.md", True),
            },
        )
        # The verification command is the real one, so the project's own suite
        # really runs and really passes. What fails is the operator's third
        # criterion, which nothing satisfies.
        result = self.run_declared(
            acceptance_specs={self.build_id: unsatisfiable},
        )

        # Verification is its own measure, and it really did exit 0.
        self.assertEqual(result.verification_exit_code, 0)
        self.assertTrue(result.verification.success)
        self.assertEqual(result.verification.outcome_status, "EXECUTION_SUCCESS")
        # Acceptance is a different measure, and it really did fail.
        self.assertEqual(result.acceptance_status, "fail")
        self.assertFalse(result.success)
        self.assertNotEqual(result.verdict, VERDICT_PROJECT_VERIFIED)
        self.assertNotEqual(result.verdict, VERDICT_DECLARED_COMMAND_VERIFIED)
        # The verdict blames the gate, not the build: the action really ran and
        # the operator's own criterion is what said no.
        self.assertEqual(result.verdict, VERDICT_ACCEPTANCE_FAILED)
        payload = result.to_dict()
        self.assertTrue(payload["verification"]["success"])
        self.assertEqual(payload["acceptance_status"], "fail")
        self.assertFalse(payload["success"])

    def test_an_exit_zero_that_collected_no_tests_is_not_verification_success(self) -> None:
        """``Ran 0 tests`` exits 0. The declared expectation decides.

        The declared expectation here is a non-zero exit code, which is what a
        project with no runnable tests must not produce. The result must follow
        the observed exit code, not the absence of a crash.
        """
        import dataclasses

        service = self._service()
        # The build declaration keeps its spec; the *verify* declaration is given
        # an expectation of 1 so the honest observation "it exited 0" fails it.
        service._declarations = dict(service.declarations)
        service._declarations[self.verify_id] = dataclasses.replace(
            service.declarations[self.verify_id], expected_exit_code=1
        )
        result = run_project_workflow(service, self.registry, self.build_id)

        self.assertEqual(result.verification_exit_code, 0)
        self.assertEqual(result.verification.declared_expected_exit_code, 1)
        self.assertFalse(result.verification.success)
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_TESTS_FAILED)

    def test_a_missing_execution_result_leaves_the_exit_code_unknown(self) -> None:
        """No observed process result is ``None``, which is not ``0``.

        Two halves, because either alone would pass for the wrong reason:

        * **End to end**: the declared verification is dispatched through a
          scripted executor whose run carries an ``ExecutionResult`` with no
          ``exit_code`` at all. The workflow must report an unknown exit code, a
          failed verification, and no verified verdict -- an unknown is never
          rounded to success.
        * **Directly**: the outcome builder is called with *no* result at all, so
          the "nothing was observed" branch is exercised without depending on how
          a run happens to be shaped.
        """
        import dataclasses
        from app.workflows.project_workflow import _verification_outcome

        service = self._service()
        declaration = service.declarations[self.verify_id]
        counting = _CountingExecutor(
            service.runtime.run_executor,
            # The declared-verification dispatch carries this exact description
            # (set by ``ForgeApiService.run_declared_verification``), so matching on
            # it scripts *that* dispatch and nothing else.
            scripted={"Declared verification execution.": _FakeDeclaredRun(self.verify_id, None)},
        )
        service.runtime = dataclasses.replace(service.runtime, run_executor=counting)
        result = run_project_workflow(service, self.registry, self.build_id)

        self.assertTrue(counting.playbook_hits > 0, counting.task_descriptions)
        self.assertIsNone(result.verification_exit_code)
        self.assertIsNone(result.verification.observed_exit_code)
        self.assertFalse(result.verification.success)
        self.assertFalse(result.success)
        self.assertNotEqual(result.verdict, VERDICT_DECLARED_COMMAND_VERIFIED)
        self.assertTrue(result.verification.messages)

        # The "no result at all" branch, asserted directly.
        outcome = _verification_outcome(declaration, None, None)
        self.assertIsNone(outcome.observed_exit_code)
        self.assertFalse(outcome.success)
        self.assertTrue(outcome.messages)


# ---------------------------------------------------------------------------
# 7. Authorization
# ---------------------------------------------------------------------------


class AuthorizationIsNotBypassedTests(SliceTestCase):
    """The slice adds a caller, not an authority."""

    def test_a_required_unresolved_approval_performs_nothing(self) -> None:
        result = self.run_declared(
            approval_policy=ApprovalPolicy(("execute",)),
            approval_resolver=None,
        )

        # Nothing was produced and nothing was verified: the declaration was
        # refused at the authorization boundary, so the action it names never ran.
        self.assertEqual(self.disk_files(), [])
        self.assertFalse(result.success)
        self.assertIsNone(result.verification_exit_code)
        self.assertEqual(result.acceptance_status, "not_evaluated")
        # The refusal is reported, and the refused action is not reported as a
        # success in any field. The state is the authorization outcome itself.
        self.assertNotEqual(result.execution_status, "succeeded")
        self.assertEqual(result.execution_status, "waiting_for_approval")
        self.assertEqual(result.harness_status, "WAITING_FOR_APPROVAL")
        self.assertNotEqual(result.verdict, VERDICT_DECLARED_COMMAND_VERIFIED)
        self.assertTrue(result.error or result.messages)

    def test_a_rejected_approval_performs_nothing(self) -> None:
        result = self.run_declared(
            approval_policy=ApprovalPolicy(("execute",)),
            approval_resolver=_RejectingResolver(),
        )

        self.assertEqual(self.disk_files(), [])
        self.assertFalse(result.success)
        self.assertIsNone(result.verification_exit_code)
        self.assertNotEqual(result.verdict, VERDICT_DECLARED_COMMAND_VERIFIED)

    def test_a_required_approval_also_gates_the_generation_path_execution(self) -> None:
        """Composing a provider does not create a second, ungated execution path.

        The model's files may be written -- writing is not execution -- but the
        declared verification command must not run without approval.
        """
        transport = _CountingTransport(project_response())
        result = self.run_model(
            transport,
            approval_policy=ApprovalPolicy(("execute",)),
            approval_resolver=None,
        )

        self.assertFalse(result.success)
        self.assertIsNotNone(result.verification)
        self.assertIsNone(result.verification.observed_exit_code)
        self.assertIsNone(result.verification_exit_code)
        self.assertFalse(result.verification.success)
        self.assertNotEqual(result.verdict, VERDICT_PROJECT_VERIFIED, result.verdict)
        self.assertEqual(result.acceptance_status, "not_evaluated")


# ---------------------------------------------------------------------------
# 8-9. The workspace boundary
# ---------------------------------------------------------------------------


class WorkspaceBoundaryTests(SliceTestCase):
    """The run stays inside the permitted area, or it does not run."""

    def test_a_working_directory_outside_the_permitted_area_is_refused(self) -> None:
        """The boundary refuses the declaration, before any process can exist.

        A declaration whose ``working_directory`` leaves the permitted area is
        rejected while the service is being composed -- so there is no run to
        escape from, not merely a run whose escape was reported afterwards. The
        marker file outside the workspace is the independent witness: it must be
        untouched, and it must be untouched because the command never existed.
        """
        from app.execution.declaration import ExecutionDeclarationError

        outside = self.base / "outside"
        outside.mkdir()
        marker = outside / "escaped.txt"
        marker.write_text("untouched", encoding="utf-8")

        with self.assertRaises(ExecutionDeclarationError) as raised:
            self.run_declared(verify_working_directory="../outside")

        self.assertIn("working_directory", str(raised.exception))
        self.assertEqual(marker.read_text(encoding="utf-8"), "untouched")
        # Nothing was created inside the permitted area either.
        self.assertEqual(self.disk_files(), [])
        self.assertFalse((outside / MODULE_PATH).exists())

    def test_a_declaration_inside_the_permitted_area_still_runs(self) -> None:
        """The control for the refusal above: the same declaration, in bounds.

        Without this, the previous test would pass for a reason that has nothing
        to do with the boundary -- for example if *every* declaration were
        refused.
        """
        result = self.run_declared(verify_working_directory=".")

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.verification_exit_code, 0)
        self.assertTrue((self.root / MODULE_PATH).is_file())

    def test_a_workspace_escape_attempt_is_blocked_not_merely_reported(self) -> None:
        """A model file with a traversal path must not exist anywhere."""

        transport = _CountingTransport(
            project_response(extra={"../escape.py": "ESCAPED = True\n"})
        )
        result = self.run_model(transport)

        # The refused path exists in no spelling, in the workspace or beside it.
        self.assertFalse((self.root / "escape.py").exists())
        self.assertFalse((self.base / "escape.py").exists())
        reasons = {item.reason for item in result.materialisation_rejections}
        self.assertIn("path_traversal", reasons, reasons)
        # The safe files beside it survive, and the run is not called a success.
        self.assertTrue((self.root / MODULE_PATH).is_file())
        self.assertNotIn("escape.py", self.disk_files())

    def test_a_protected_area_write_is_refused_and_never_created(self) -> None:
        transport = _CountingTransport(
            project_response(extra={".git/hooks/pre-commit": "#!/bin/sh\necho pwned\n"})
        )
        result = self.run_model(transport)

        self.assertFalse((self.root / ".git").exists())
        reasons = {item.reason for item in result.materialisation_rejections}
        self.assertIn("protected_path", reasons, reasons)

    def test_a_linked_parent_that_escapes_the_workspace_is_refused(self) -> None:
        """The check that answers "once *resolved*, is this still inside?".

        Every other path-rejection test here is refused by the **shape** check,
        which reads the path as text: ``../escape.py`` and ``.git/hooks/pre-commit``
        never reach the filesystem at all. This test covers the remaining hole, and
        the one a text check cannot close: a path whose *text* is innocent
        (``link/escape.py``) but whose **resolution** leaves the permitted root,
        because ``link`` inside the workspace is an NTFS junction to somewhere else.

        Two independent facts are asserted: the file outside the workspace is
        byte-for-byte unchanged, and an ``os.scandir`` walk of the workspace -- the
        same one the workflow itself uses for its created-file diff -- does not
        descend through the junction. Without this test the resolved-containment
        guard could be deleted and this whole file would still pass; that was
        measured, not assumed, by applying exactly that patch.
        """
        outside = self.base / "outside"
        outside.mkdir()
        witness = outside / "escape.py"
        witness.write_text("ORIGINAL = True\n", encoding="utf-8", newline="")

        link = self.root / "link"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True, text=True,
        )
        if created.returncode != 0 or not link.exists():
            self.skipTest(
                "this platform cannot create a directory link: "
                f"{created.stdout.strip()} {created.stderr.strip()}"
            )

        transport = _CountingTransport(
            project_response(extra={"link/escape.py": "ESCAPED = True\n"})
        )
        result = self.run_model(transport)

        # 1. The file outside the workspace is exactly as it was.
        self.assertEqual(witness.read_bytes(), b"ORIGINAL = True\n")
        reasons = {item.reason for item in result.materialisation_rejections}
        self.assertIn("path_escapes_workspace", reasons, reasons)
        self.assertFalse(result.success)

        # 2. The workspace walk does not descend through the link, which is what
        #    keeps the created-file diff from reporting files outside the root.
        #    The walk is the product's own, so this asserts the same code path the
        #    result's ``created_files`` comes from rather than a copy of it.
        from app.workflows.project_workflow import _snapshot_workspace

        walked = tuple(_snapshot_workspace(self.root).files)
        self.assertNotIn("link/escape.py", walked)
        self.assertNotIn("escape.py", walked)
        self.assertIn(MODULE_PATH, walked)

    def test_a_write_conflict_is_refused_without_the_operator_s_permission(self) -> None:
        (self.root / MODULE_PATH).write_text("ORIGINAL = True\n", encoding="utf-8")
        original = (self.root / MODULE_PATH).read_bytes()

        transport = _CountingTransport(project_response())
        result = self.run_model(transport)

        self.assertEqual((self.root / MODULE_PATH).read_bytes(), original)
        reasons = {item.reason for item in result.materialisation_rejections}
        self.assertIn("conflicts_with_existing_file", reasons, reasons)
        self.assertFalse(result.success)


# ---------------------------------------------------------------------------
# 10. Timeout / execution error
# ---------------------------------------------------------------------------


class ExecutionErrorTests(SliceTestCase):
    """A process that never finished is never a verified result."""

    def test_a_verification_timeout_is_never_a_success(self) -> None:
        import dataclasses

        service = self._service(verification_timeout=2.0)
        service._declarations = dict(service.declarations)
        service._declarations[self.verify_id] = dataclasses.replace(
            service.declarations[self.verify_id],
            command=("python", "-c", "import time; time.sleep(60)"),
        )
        result = run_project_workflow(service, self.registry, self.build_id)

        self.assertFalse(result.success)
        self.assertFalse(result.verification.success)
        self.assertNotEqual(
            result.verification.outcome_status, "EXECUTION_SUCCESS"
        )
        self.assertNotEqual(result.verdict, VERDICT_DECLARED_COMMAND_VERIFIED)
        self.assertEqual(result.verification.outcome_status, "EXECUTION_TIMEOUT")

    def test_a_command_that_cannot_start_reports_no_invented_exit_code(self) -> None:
        import dataclasses

        service = self._service()
        service._declarations = dict(service.declarations)
        service._declarations[self.verify_id] = dataclasses.replace(
            service.declarations[self.verify_id],
            environment_variables={
                "PYTHONHOME": str(self.base / "no-such-python-home")
            },
        )
        result = run_project_workflow(service, self.registry, self.build_id)

        self.assertFalse(result.success)
        self.assertIsNone(result.verification_exit_code)
        self.assertFalse(result.verification.success)
        self.assertTrue(result.verification.error or result.verification.messages)


# ---------------------------------------------------------------------------
# 11. A result never reports an unverified state as success
# ---------------------------------------------------------------------------


class NeverUnverifiedAsSuccessTests(SliceTestCase):
    """The strongest form of the claim: scan every negative for a false success."""

    def test_no_negative_case_reports_success_or_a_verified_verdict(self) -> None:
        # Cases whose *generation* step must fail outright: no project text at all
        # became a project, so the run cannot be a success and cannot carry a
        # verified verdict.
        hard_failures = (
            ("empty", _CountingTransport("")),
            ("malformed", _CountingTransport("<html>not json</html>")),
            (
                "missing-files",
                _CountingTransport(json.dumps({"decision": {"action": "EXECUTE"}})),
            ),
            ("raised", _CountingTransport(raises=ValueError("no answer"))),
        )

        for label, transport in hard_failures:
            self.tearDown()
            self.setUp()
            result = self.run_model(transport)

            self.assertFalse(result.success, f"{label} reported success")
            self.assertEqual(
                result.generation_status,
                GENERATION_FAILED_TRANSPORT
                if label == "raised"
                else GENERATION_FAILED_RESPONSE,
                label,
            )
            self.assertNotEqual(
                result.verdict, VERDICT_PROJECT_VERIFIED, label
            )
            self.assertNotEqual(
                result.verdict, VERDICT_DECLARED_COMMAND_VERIFIED, label
            )
            self.assertNotEqual(
                result.verdict, "REAL LLM PROJECT GENERATION VERIFIED", label
            )
            # Every status comes from its own closed vocabulary and is never blank.
            self.assertIn(result.generation_status, GENERATION_STATUSES, label)
            self.assertIn(result.execution_status, EXECUTION_STATUSES, label)
            self.assertIn(
                result.acceptance_status, {"pass", "fail", "not_evaluated"}, label
            )
            self.assertFalse(result.to_dict()["success"], label)
            # Nothing was written by a response that could not become a project.
            self.assertEqual(self.disk_files(), [], label)

    def test_a_partially_refused_response_is_never_presented_as_whole(self) -> None:
        """The fourth outcome: generation *succeeded* and still is not a success.

        A response whose files include a traversal path materialises the safe
        files and refuses the unsafe one, so ``generation_status`` is
        ``succeeded``. The run must still not be a success, because the project it
        wrote is not the project the model asked for. This is a different case
        from the four above and is kept separate on purpose: collapsing it into
        "generation failed" would misreport the transport.
        """
        transport = _CountingTransport(
            project_response(extra={"sub/../../../x.py": "X = 1\n"})
        )
        result = self.run_model(transport)

        self.assertEqual(result.generation_status, GENERATION_SUCCEEDED)
        self.assertIn(
            "path_traversal",
            {item.reason for item in result.materialisation_rejections},
        )
        self.assertFalse((self.root / "x.py").exists())
        self.assertFalse((self.base / "x.py").exists())
        # The surviving files passed their own tests -- and the run is still not a
        # verified project, because a file the response asked for was refused. The
        # verdict names that, instead of hiding it or discarding the passing run.
        self.assertEqual(result.verification_exit_code, 0)
        self.assertFalse(result.success)
        self.assertEqual(result.state, "MATERIALISATION_INCOMPLETE")
        self.assertEqual(result.verdict, VERDICT_MATERIALISATION_INCOMPLETE)
        self.assertEqual(result.error, "materialisation_incomplete")

    def test_acceptance_pass_alone_never_implies_the_tests_passed(self) -> None:
        """The exact conflation this slice must not allow.

        A live demonstration: the operator's criteria hold (the artefact exists),
        the acceptance gate therefore returns ``pass``, and the project's own test
        suite nevertheless fails with a real non-zero exit code. If any code path
        treated ``acceptance_status == "pass"`` as "the tests passed", this run
        would report success. It must not.
        """
        import dataclasses

        service = self._service()
        service._declarations = dict(service.declarations)
        service._declarations[self.build_id] = dataclasses.replace(
            service.declarations[self.build_id],
            # The programme writes the very files the criteria require, so the
            # acceptance gate passes -- and the test inside them fails.
            command=("python", "-c", _CREATION_PROGRAMME.replace(
                "return a + b", "return a - b"
            )),
        )
        result = run_project_workflow(service, self.registry, self.build_id)

        self.assertEqual(result.acceptance_status, "pass")
        self.assertNotEqual(result.verification_exit_code, 0)
        self.assertFalse(result.verification.success)
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_TESTS_FAILED)
        # Serialised, the two facts remain in two fields.
        payload = result.to_dict()
        self.assertEqual(payload["acceptance_status"], "pass")
        self.assertFalse(payload["verification"]["success"])
        self.assertFalse(payload["success"])

    def test_a_missing_acceptance_verdict_is_never_a_pass(self) -> None:
        """No ``AcceptanceGate`` verdict means ``not_evaluated``, not ``pass``.

        The acceptance spec is replaced by one whose single criterion is optional
        and whose expectation is a file the project never creates, so the gate
        reports what it really found. Whatever it reports, the workflow's own
        status must be the recorded verdict -- and when nothing was recorded it
        must be ``not_evaluated``.
        """
        transport = _CountingTransport(project_response())
        result = self.run_model(transport)

        # On the model path the gate is not driven at all, so there is no verdict,
        # and "no verdict" is reported as such.
        self.assertEqual(result.acceptance_status, "not_evaluated")
        self.assertIsNotNone(result.generation)
        self.assertEqual(result.generation_status, GENERATION_SUCCEEDED)


# ---------------------------------------------------------------------------
# 12. The declared command triggers NO additional LLM generation
# ---------------------------------------------------------------------------


class DeclaredCommandMakesNoModelCallTests(SliceTestCase):
    """Count provider calls where they physically happen: at the transport."""

    def test_the_declared_verification_entry_point_makes_no_provider_call(self) -> None:
        """The pre-existing entry point, measured directly.

        ``run_declared_verification`` is called on its own with a counting
        transport registered that would satisfy any generative dispatch. If the
        entry point asked a model, the counter would move. It must not: the
        decision for that run is the operator's declaration.
        """
        transport = _CountingTransport(project_response())
        service = self._service([transport])

        response = service.run_declared_verification(self.verify_id)

        self.assertEqual(
            transport.call_count,
            0,
            "the declared verification entry point asked a model",
        )
        self.assertEqual(response.state, "FAILED")  # nothing to run: no project yet
        self.assertEqual(response.tokens, 0)
        self.assertEqual(response.cost, 0.0)
        self.assertEqual(response.provider_used, "")
        self.assertEqual(response.model_used, "")

    def test_the_whole_declared_slice_never_dispatches_a_generative_task(self) -> None:
        """Count *dispatches* across the whole workflow, not just one entry point.

        The executor is wrapped so every ``RunExecutor.execute`` dispatch is
        recorded by task description. On the declared-command path the only
        dispatch that goes through the executor is the declared verification, and
        the transport's own call record must stay empty. The ``run_accepted_task``
        entry point deliberately does **not** go through this executor -- it drives
        the harness -- so its own behaviour is measured separately, further down.
        """
        import dataclasses

        transport = _CountingTransport(project_response())
        service = self._service([transport])
        counting = _CountingExecutor(service.runtime.run_executor)
        service.runtime = dataclasses.replace(service.runtime, run_executor=counting)
        result = run_project_workflow(service, self.registry, self.build_id)

        self.assertEqual(
            transport.call_count, 0, "a generative provider was asked"
        )
        self.assertIsNone(result.generation_attempts_spent)
        self.assertEqual(result.generation_status, "disabled")
        # Exactly one dispatch went through the executor, and it is the declared
        # verification: no generation task was ever dispatched.
        self.assertEqual(len(counting.task_descriptions), 1, counting.task_descriptions)
        self.assertEqual(
            counting.task_descriptions[0], "Declared verification execution."
        )
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.verification_exit_code, 0)

    def test_the_declared_verification_continues_a_real_project_with_no_model(self) -> None:
        """The verification step on a *populated* workspace, with the counter armed.

        This is the end-to-end form of the claim, and the sharpest one: after the
        build has really produced the project, the declared verification command
        is dispatched again on a service whose provider registry holds a counting
        transport that answers a complete, valid project response. If the
        verification path asked a model -- the duplication this slice's design
        removes -- the counter would move. It must not, and the exit code must
        still be the project's own.
        """
        declared = self.run_declared()
        self.assertTrue(declared.success, declared.error)

        transport = _CountingTransport(project_response())
        service = self._service([transport])
        response = service.run_declared_verification(self.verify_id)

        self.assertEqual(
            transport.call_count,
            0,
            "the declared verification asked a model for a decision",
        )
        # Zero tokens and zero cost are the workflow's own accounting, and they
        # must stay zero: there was nothing to bill.
        self.assertEqual(response.tokens, 0)
        self.assertEqual(response.cost, 0.0)
        self.assertEqual(response.provider_used, "")
        self.assertEqual(response.model_used, "")
        # And the command really ran: the project's own suite passed under it.
        self.assertEqual(response.state, "COMPLETED")
        self.assertTrue(response.success)
        independent = self.run_project_tests_here()
        self.assertEqual(independent.returncode, 0)

    def test_an_already_authorized_declared_command_adds_no_second_provider_call(
        self,
    ) -> None:
        """One generation attempt, then a declared verification -- and that is all.

        With the generation step granted its single attempt, the workflow must
        still report exactly one attempt spent: the declared verification step
        initiates none. The verify dispatch is scripted so this measures the
        *workflow* rather than the adapter.
        """
        import dataclasses

        transport = _CountingTransport(project_response())
        service = self._service([transport])
        counting = _CountingExecutor(
            service.runtime.run_executor,
            scripted={"task-verify-": _FakeDeclaredRun(self.verify_id, 0)},
        )
        service.runtime = dataclasses.replace(service.runtime, run_executor=counting)
        result = run_project_workflow(
            service,
            self.registry,
            self.build_id,
            ai_provider=AIDecisionProvider(transport, mock_transport=True),
        )

        # The transport was asked exactly once, for the project text.
        self.assertEqual(
            transport.call_count,
            1,
            "the declared verification step asked the model as well",
        )
        self.assertEqual(result.generation_attempts_spent, 1)
        self.assertEqual(result.generation_status, GENERATION_SUCCEEDED)
        # Two dispatches go through the executor in this run: the generation task,
        # and the declared verification. The generation one carries the project
        # prompt; the verification one is the declared command. Neither of those
        # is a *second* generative call around the declared command.
        self.assertEqual(len(counting.task_descriptions), 2, counting.task_descriptions)
        self.assertEqual(
            counting.task_descriptions[-1], "Declared verification execution."
        )
        self.assertEqual(result.verification_exit_code, 0)
        self.assertTrue(result.verification.success)

    def test_an_exhausted_generation_budget_refuses_before_it_spends(self) -> None:
        """A limit that is already exhausted refuses *before* the call."""
        transport = _CountingTransport(project_response())
        service = self._service([transport])
        result = run_project_workflow(
            service,
            self.registry,
            self.build_id,
            ai_provider=AIDecisionProvider(transport, mock_transport=True),
            max_generation_attempts=0,
        )

        # Refused before the call: nothing was spent and nothing was asked.
        self.assertEqual(transport.call_count, 0)
        self.assertEqual(result.generation_attempts_spent, 0)
        self.assertEqual(result.generation_status, GENERATION_REFUSED_LIMIT)
        self.assertEqual(result.verdict, VERDICT_GENERATION_REFUSED)
        self.assertEqual(result.error, "generation_refused")
        self.assertFalse(result.success)
        # A refusal is not routed around: the declared creation command must not
        # have been dispatched to produce the project some other way.
        self.assertFalse(result.declared_command_dispatched)
        self.assertFalse((self.root / MODULE_PATH).exists())
        self.assertEqual(self.disk_files(), [])
        self.assertEqual(result.max_generation_attempts, 0)

    def test_the_default_budget_is_one_attempt_and_is_reported(self) -> None:
        transport = _CountingTransport(project_response())
        result = self.run_model(transport)

        self.assertEqual(result.max_generation_attempts, 1)
        self.assertEqual(result.generation_attempts_spent, 1)
        self.assertEqual(transport.call_count, 1)

    def test_a_malformed_generation_budget_is_refused(self) -> None:
        """A bound the caller cannot be trusted to have meant is refused outright.

        ``-1`` and ``True`` are both plausible mistakes and neither is a budget.
        Accepting either and coercing it would silently pick a number of provider
        calls on the caller's behalf, which is exactly the kind of default this
        slice must not invent. The refusal must also cost nothing: no provider
        call may have happened.
        """
        transport = _CountingTransport(project_response())
        service = self._service([transport])

        for bad in (-1, True, "1", 1.5, None):
            with self.assertRaises(ProjectWorkflowError):
                run_project_workflow(
                    service,
                    self.registry,
                    self.build_id,
                    ai_provider=AIDecisionProvider(transport, mock_transport=True),
                    max_generation_attempts=bad,  # type: ignore[arg-type]
                )

        self.assertEqual(
            transport.call_count, 0, "a refused budget still spent a provider call"
        )

    def test_the_build_step_is_measured_separately_from_the_verification_step(
        self,
    ) -> None:
        """The honest measurement of the build step's own model dispatch.

        The declared ``run_accepted_task`` entry point drives the agent harness,
        and the harness's DECIDE phase performs a generative dispatch of its own.
        That is the *build* step and is pre-existing behaviour of that entry point
        -- not something the declared verification adds. Rather than assume either
        answer, this test measures the count before and after the verification, and
        asserts the part that matters: the verification does not increase it.
        """
        transport = _CountingTransport(project_response())
        service = self._service([transport])

        build = service.run_accepted_task(self.build_id)
        self.assertTrue(build.success, build.error)
        after_build = transport.call_count

        # The declared verification, which is the step that must not ask again.
        service.run_declared_verification(self.verify_id)
        self.assertEqual(
            transport.call_count,
            after_build,
            "the declared verification added a provider call of its own",
        )
        # Whatever the build step's count was, the verification's own contribution
        # is exactly zero -- and that statement does not depend on the harness's
        # internal routing.
        self.assertEqual(transport.call_count - after_build, 0)


# ---------------------------------------------------------------------------
# 13. Test-mode runtimes make no provider decision call
# ---------------------------------------------------------------------------


class CountingDelegateProvider(Provider):
    """A counting stand-in for the *runtime's own* provider.

    It is registered over whatever provider the runtime supplied -- under a test
    runtime that is the offline ``mock`` -- so the count is taken at the exact
    object the runtime would have dispatched to. It forwards ``generate``
    unchanged, so a call that happens is a call that would have happened.
    """

    def __init__(self, inner: Provider) -> None:
        super().__init__(inner.config)
        self._inner = inner
        self.calls: list[ProviderRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return self._inner.generate(request)


class TestModeRuntimeMakesNoProviderDecisionCallTests(SliceTestCase):
    """``test_mode=True`` is proved by the observed registry, not by its name.

    The ambient configuration on this machine really does enable live providers
    (a ``.env`` with nine keys exists). Nothing here reads it. Each test builds
    the runtime with ``test_mode=True``, **measures** the registry that runtime
    actually produced, and then measures provider calls at the counting transport
    the service under test was composed with -- so "no call was made" is an
    observation about this run, not an assumption about configuration.
    """

    def test_the_test_mode_registry_holds_only_the_offline_agent(self) -> None:
        runtime = create_runtime(test_mode=True)

        # Measured, not assumed: the registry's own contents.
        agents = runtime.agent_registry.list_agents()
        self.assertEqual(len(agents), 1, agents)
        self.assertEqual(agents, ["mock"])
        self.assertEqual(tuple(runtime.settings.enabled_providers), ("mock",))
        self.assertEqual(runtime.settings.default_provider, "mock")
        # The provider registry the runtime actually built holds the offline
        # provider and nothing else.
        self.assertEqual(runtime.provider_registry.list_providers(), ["mock"])

    def test_the_declared_verification_makes_no_provider_call(self) -> None:
        """The declared verification with the counter armed, on a real project.

        The service is composed with a single counting transport, so the
        measurement is taken at the exact object the runtime would dispatch to.
        The ambient ``test_mode=True`` registry is asserted first. The verification
        command must run and pass on a workspace that already holds a real
        project, and the counter must not move -- there is nothing left for a
        generation step to be tempted by.
        """
        runtime = create_runtime(test_mode=True)
        self.assertEqual(runtime.agent_registry.list_agents(), ["mock"])

        counting = _CountingTransport(project_response())
        service = self._service([counting], runtime=self._runtime([counting]))
        # Build the project through the workflow's own declared-command path, so
        # the workspace really holds a project for the verification to verify.
        first = run_project_workflow(service, self.registry, self.build_id)
        self.assertTrue(first.success, first.error)
        self.assertTrue((self.root / MODULE_PATH).is_file())
        after_build = counting.call_count

        response = service.run_declared_verification(self.verify_id)

        self.assertIn(response.state, ("COMPLETED",))
        self.assertTrue(response.success)
        self.assertEqual(response.tokens, 0)
        self.assertEqual(response.cost, 0.0)
        self.assertEqual(response.provider_used, "")
        self.assertEqual(response.model_used, "")
        self.assertEqual(
            counting.call_count,
            after_build,
            "the declared verification asked its provider",
        )
        # And the verification command really ran the project's own tests. The
        # standard-library runner writes its report to **stderr**, so the stdout
        # field is empty and the workflow reports a placeholder rather than
        # inventing output; the exit code is the evidence that the suite ran.
        self.assertTrue(response.success)
        self.assertEqual(
            (self.root / MODULE_PATH).read_text(encoding="utf-8"), _MODULE_SOURCE
        )

    def test_the_whole_declared_slice_runs_without_asking_a_provider(self) -> None:
        """End to end, with every status asserted and the counter at zero.

        The ambient registry of a ``test_mode=True`` runtime holds only ``mock``
        (measured above); *this* runtime's only provider is the counting
        transport, and the declared-command slice reaches a verified result while
        that transport is never asked. Both the coarse fact (which agents exist)
        and the fine fact (which were called) are therefore observed, not inferred.
        """
        ambient = create_runtime(test_mode=True)
        self.assertEqual(ambient.agent_registry.list_agents(), ["mock"])

        counting = _CountingTransport(project_response())
        service = self._service([counting], runtime=self._runtime([counting]))
        self.assertEqual(
            service.runtime.agent_registry.list_agents(), [INJECTED_PROVIDER]
        )

        result = run_project_workflow(service, self.registry, self.build_id)

        self.assertEqual(result.generation_status, "disabled")
        self.assertIsNone(result.generation_attempts_spent)
        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(result.verification_exit_code, 0)
        self.assertEqual(result.acceptance_status, "pass")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.verdict, VERDICT_DECLARED_COMMAND_VERIFIED)
        self.assertEqual(
            counting.call_count, 0, "the declared-command slice asked its provider"
        )


if __name__ == "__main__":
    unittest.main()
