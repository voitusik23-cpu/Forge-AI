"""Model-generated project files: observed behaviour, with an injected transport.

WHAT THESE TESTS PROVE, AND WHAT THEY DO NOT
============================================

Every test here observes **behaviour**, not the presence of a class, a function,
or a source string:

* the model is asked through the project's existing provider path, and the
  provider's own call record plus the result's ``generation`` evidence show it;
* the model's response *content* is what lands on disk;
* an empty, malformed, or rejected response never yields a created project and
  never yields a success verdict -- each fail-closed reason is exercised;
* unsafe paths (absolute, traversal, escaping, protected areas, credential
  paths) are refused, and refused files are not created;
* an existing user file is not overwritten without explicit operator permission,
  and is written when that permission is given;
* every created file is inside the permitted workspace root, proved by resolving
  and comparing roots rather than by trusting the reported path;
* the generated project's own tests really execute as a second declared command,
  read from the real process exit code;
* a deliberately failing generated test yields a non-zero exit code and a
  NOT-verified verdict, and a non-zero exit is never reported as verified;
* mock/injected providers are marked as such in data, never presented as real
  generation;
* the existing authorization and approval behaviour is unbroken -- a
  required-but-unresolved approval still performs nothing.

WHAT THESE TESTS DO NOT PROVE
=============================

**No real provider call is made anywhere in this file.** Every provider here is
an injected, in-process fake whose only job is to hand back a fixed string. That
proves the generation *path*, the schema validation, the materialisation checks,
and the verification machinery. It proves **nothing** about the quality of
model-generated code, and it says nothing about whether a real provider adapter
works. The opt-in test that would make a real call lives in
``tests/test_project_generation_real_provider.py``, is skipped by default, and
was never executed.

ISOLATION: ``ForgeApiService(run_store=None)`` uses a shared per-user run-store
root, and the idempotency ledger is likewise per-user. Every service here is
given its own ``RunStore`` and ``RunIdempotencyGuard`` under a temporary
directory, so these tests never race another process.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
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
from app.execution.secret_files import is_secret_directory, is_secret_file
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_agent_harness
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope
from app.runtime.run_store import RunStore
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.registry import build_default_tool_registry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.usage import Usage
from app.workflows.project_generation import (
    MATERIALISATION_REASON_CODES,
    MOCK_TRANSPORT_IS_NOT_REAL_GENERATION,
    parse_model_project,
    secret_content_reason,
)
from app.workflows.project_workflow import (
    VERDICT_APPROVAL_REQUIRED,
    VERDICT_GENERATION_FAILED,
    VERDICT_PROJECT_VERIFIED,
    VERDICT_REAL_PROVIDER_NOT_VERIFIED,
    VERDICT_TESTS_FAILED,
    ProjectWorkflowRegistry,
    run_project_workflow,
)

BUILD_ID = "demo.generated.build"
VERIFY_ID = "demo.generated.verify"

FAKE_PROVIDER_NAME = "fake-gen"
FAKE_MODEL_NAME = "fake-gen-model-1"

#: The designated creation command for this scenario. It is never dispatched on
#: the generation path -- the model's files are the project -- but the workflow
#: still resolves it, and the *verification* declaration is a real declared
#: command that really runs. It writes a marker file so a dispatched creation
#: command would be detectable in the workspace.
CREATE_COMMAND = (
    "python",
    "-c",
    "open('DECLARED_CREATION_COMMAND_RAN.txt','w',encoding='utf-8').write('ran')",
)

#: The second declared command. It really executes the generated project's own
#: test suite; pytest is unavailable in this environment, so unittest discovery
#: is used, exactly as the project's own tests would be run by hand.
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

#: The fixed physical measurement every fake provider reports, so a test can
#: assert that usage propagates without depending on a tokenizer.
FAKE_USAGE = Usage(input_tokens=101, output_tokens=202, estimated_cost=0.5)

#: The injected transport. It is a real ``Provider`` implementation, so the
#: production dispatch path is exercised unchanged; only the model behind it is
#: replaced. It records every call it receives, which is the proof that the model
#: was asked.
MOCK_TRANSPORT_MARKER = MOCK_TRANSPORT_IS_NOT_REAL_GENERATION


class _InjectedProvider(Provider):
    """A provider that returns a fixed string and records every request.

    It is deliberately named "injected" rather than "mock" in the code: every
    field it feeds is marked as a non-real transport by the test that composes
    it, so nothing downstream can read its text as a real model's work.
    """

    def __init__(
        self,
        output: str,
        *,
        provider_name: str = FAKE_PROVIDER_NAME,
        model_name: str = FAKE_MODEL_NAME,
        usage: Usage | None = None,
    ) -> None:
        super().__init__(
            ProviderConfig(
                provider_name=provider_name,
                model_name=model_name,
                enabled=True,
            )
        )
        self._output = output
        self._usage = usage if usage is not None else FAKE_USAGE
        self.calls: list[ProviderRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_output(self) -> str:
        return self._output

    def set_output(self, output: str) -> None:
        """Change what this transport will answer, without rebuilding anything.

        Used to pin that one validator and one transport reused across two
        invocations cannot carry a previous answer's identity or measurement into
        the next, fail-closed one.
        """
        self._output = output

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output=self._output,
            usage=self._usage,
        )


def _fake_project(
    *,
    add_operator: str = "+",
    module_body: str | None = None,
    readme_line: str = "generated by an injected provider",
    test_body: str = "self.assertEqual(mod.add(2, 3), 5)",
    extra_files: dict[str, str] | None = None,
    decision: dict | None = None,
) -> str:
    """One complete model response in the declared schema.

    ``add_operator``, ``module_body``, and ``test_body`` are the knobs the content
    tests turn, so "the model's text changed the files on disk" is asserted on
    real bytes rather than on a length.
    """
    if module_body is None:
        module_body = (
            '"""A generated module."""\n\n\n'
            f"def add(a, b):\n    return a {add_operator} b\n"
        )
    test_source = (
        "import unittest\n\n"
        "import mod\n\n\n"
        "class GeneratedTests(unittest.TestCase):\n"
        "    def test_add(self):\n"
        f"        {test_body}\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    readme = f"# generated project\n\n{readme_line}\n"
    files: dict[str, str] = {
        "mod.py": module_body,
        "README.md": readme,
        "tests/__init__.py": "",
        "tests/test_mod.py": test_source,
    }
    if extra_files:
        files.update(extra_files)
    payload: dict[str, object] = {
        "files": files,
        "decision": {
            "decision_type": "CONTINUE",
            "action": "EXECUTE",
            "reason_code": "project_ready",
            "rationale": "the project is complete",
            "confidence": 0.9,
        },
    }
    if decision is not None:
        payload["decision"] = decision
    return json.dumps(payload)


def _failing_project() -> str:
    """A project whose own test fails for the right reason.

    The module is wrong; the test is correct. The generated suite therefore exits
    non-zero because a real assertion failed, which is the case a naive
    implementation would report as success.
    """
    return _fake_project(add_operator="-")


def _runtime(provider: Provider) -> RuntimeContext:
    """The production dispatch path with one injected transport.

    A real ``Orchestrator``, a real ``ProviderRegistry``, a real ``AgentRegistry``
    holding a real ``ProviderAgent``: only the network behind the provider is
    replaced. This is what makes "the model is called through the existing
    provider path" a testable claim rather than a description.
    """
    provider_registry = ProviderRegistry()
    provider_registry.register(provider)
    agent_registry = AgentRegistry()
    agent_registry.register(ProviderAgent(provider))
    orchestrator = Orchestrator(
        agent_registry,
        default_provider=provider.provider_name,
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


def _profile() -> ProjectExecutionProfile:
    return ProjectExecutionProfile(
        profile_id="api-default",
        allowed_commands=("python",),
        capabilities=frozenset(
            {ExecutionCapability.INTERPRET_TEXT, ExecutionCapability.INTERPRET_MODULE}
        ),
        network_access=False,
        working_directory=".",
        # The profile is the operator's ceiling, and the existing
        # ``validate_declarations_against_profile`` refuses a declaration that
        # exceeds it -- so a longer declaration timeout must be granted here too,
        # which is exactly the fail-closed behaviour it should have. This is a
        # runaway guard, not a performance assertion: a non-zero exit is still
        # never reported as verified.
        timeout_seconds=120.0,
    )


def _declarations() -> dict[str, ExecutionDeclaration]:
    return {
        BUILD_ID: ExecutionDeclaration(
            declaration_id=BUILD_ID,
            command=CREATE_COMMAND,
            profile_id="api-default",
            working_directory=".",
            intent=(
                "Create a small Python project with a module, a README and its own "
                "tests, all of which must pass."
            ),
        ),
        VERIFY_ID: ExecutionDeclaration(
            declaration_id=VERIFY_ID,
            command=VERIFY_COMMAND,
            profile_id="api-default",
            working_directory=".",
            intent="Run the generated project's own test suite.",
            # A generous timeout on purpose. The generated suite takes well under a
            # second on an idle machine, but this repository is worked on by more
            # than one agent at a time, and a 30s bound was observed to expire
            # under that load and report EXECUTION_TIMEOUT instead of the real exit
            # code. The bound is a runaway guard, not a performance assertion;
            # making it load-tolerant removes a false failure without weakening
            # anything, because a non-zero exit is still never reported as verified.
            timeout_seconds=120.0,
        ),
    }


def _acceptance_specs() -> dict[str, AcceptanceSpec]:
    return {
        BUILD_ID: AcceptanceSpec(
            declaration_id=BUILD_ID, criteria=CRITERIA, expectations=EXPECTATIONS
        )
    }


REGISTRY = ProjectWorkflowRegistry(verification_declarations={BUILD_ID: VERIFY_ID})


def _remove_link(link: Path) -> None:
    """Remove a directory link/junction without deleting its target's contents.

    ``shutil.rmtree`` on a junction is a known way to delete the target, so the
    link is removed with ``rmdir`` (which detaches a junction and fails on a real
    non-empty directory) and any failure is swallowed: this is test cleanup, and a
    failed cleanup must never turn a passing assertion into an error.
    """
    try:
        os.rmdir(link)
    except OSError:
        try:
            os.unlink(link)
        except OSError:
            pass


class GenerationTestCase(unittest.TestCase):
    """Shared composition: a temp workspace, isolated stores, one injected model."""

    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "ws"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        self._store_tmp = tempfile.TemporaryDirectory()
        self.run_store = RunStore(root=self._store_tmp.name)
        self._ledger_tmp = tempfile.TemporaryDirectory()
        self.idempotency_guard = integration.RunIdempotencyGuard.create(
            ledger_root=self._ledger_tmp.name
        )
        # A file that exists before the run, so "created" is measured against a
        # non-empty starting state rather than assumed.
        (self.root / "preexisting.txt").write_text("keep me", encoding="utf-8")

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()
        self._store_tmp.cleanup()
        self._ledger_tmp.cleanup()

    def service(
        self,
        provider: Provider,
        *,
        approval_policy=None,
        approval_resolver=None,
    ) -> ForgeApiService:
        return ForgeApiService(
            runtime=_runtime(provider),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=_profile(),
            declarations=_declarations(),
            acceptance_specs=_acceptance_specs(),
            approval_policy=(
                ApprovalPolicy() if approval_policy is None else approval_policy
            ),
            approval_resolver=approval_resolver,
            run_store=self.run_store,
            idempotency_guard=self.idempotency_guard,
        )

    def validator(self, provider: Provider) -> AIDecisionProvider:
        """The existing decision provider, explicitly marked as a fake transport.

        Marking it is not decoration: the workflow copies ``mock_transport`` into
        the result's generation evidence, which is what a report reads to avoid
        presenting injected text as a real model's work.
        """
        return AIDecisionProvider(provider, mock_transport=True)

    def run_flow(self, provider: Provider, **kwargs):
        return run_project_workflow(
            self.service(provider),
            REGISTRY,
            BUILD_ID,
            ai_provider=self.validator(provider),
            **kwargs,
        )

    def disk_files(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )

    def read(self, relative: str) -> str:
        return (self.root / relative).read_text(encoding="utf-8")


class ProviderParticipationTests(GenerationTestCase):
    """The model is asked, through the path the project already has."""

    def test_the_injected_provider_is_really_called_and_reports_its_identity(self) -> None:
        provider = _InjectedProvider(_fake_project())
        result = self.run_flow(provider)

        # 1. The provider's OWN call record. Not a message, not a flag.
        #    The first call is the generation task: its prompt is the project
        #    brief. (A later call exists because the declared verification
        #    command is dispatched through the same orchestration path, which
        #    asks the configured provider for the verification task's text before
        #    the host command really runs. That call's text is not used as
        #    project content, and it is asserted here so the path is described
        #    accurately rather than assumed.)
        self.assertGreaterEqual(provider.call_count, 1)
        generation_call = provider.calls[0]
        self.assertIn("files", generation_call.prompt)
        self.assertIn("relative path", generation_call.prompt)
        # The generation task does not pin a model: the provider's own configured
        # model is used, which is what the evidence then reports.
        self.assertIsNone(generation_call.model_name)

        # 2. Provider identity and usage propagate into the result as data.
        evidence = result.generation
        self.assertIsNotNone(evidence, "the result must carry provider evidence")
        self.assertEqual(evidence.provider_name, FAKE_PROVIDER_NAME)
        self.assertEqual(evidence.model_name, FAKE_MODEL_NAME)
        self.assertEqual(evidence.usage_input_tokens, 101)
        self.assertEqual(evidence.usage_output_tokens, 202)
        self.assertEqual(evidence.usage_estimated_cost, 0.5)
        self.assertTrue(evidence.usage_present)

        # 3. The raw text is the provider's output, byte for byte.
        self.assertEqual(evidence.raw_model_text, provider.last_output)

        # 4. The same identity is in the JSON projection the API surface reads.
        payload = result.to_dict()["generation"]
        self.assertEqual(payload["provider_name"], FAKE_PROVIDER_NAME)
        self.assertEqual(payload["model_name"], FAKE_MODEL_NAME)
        self.assertEqual(payload["usage_input_tokens"], 101)
        self.assertEqual(payload["usage_output_tokens"], 202)
        # The raw model text is deliberately NOT in the projection.
        self.assertFalse(payload["raw_model_text_included"])
        self.assertNotIn(evidence.raw_model_text, json.dumps(payload))

    def test_an_empty_response_reports_no_producer_identity_and_no_measurement(self) -> None:
        """A fail-closed answer is never dressed up as a model that answered.

        The provider was called and returned nothing. ``provider_name``,
        ``model_name``, usage, and raw text are all empty, because no model
        produced a project and there is no project-sized measurement to
        attribute. ``response_text_observed`` is False, which is the separate,
        weaker fact -- a response did not even arrive.
        """
        provider = _InjectedProvider("")
        result = self.run_flow(provider)

        evidence = result.generation
        self.assertEqual(evidence.provider_name, "")
        self.assertEqual(evidence.model_name, "")
        self.assertIsNone(evidence.usage_input_tokens)
        self.assertIsNone(evidence.usage_output_tokens)
        self.assertFalse(evidence.usage_present)
        self.assertFalse(evidence.response_text_observed)
        self.assertEqual(evidence.raw_model_text, "")
        self.assertEqual(evidence.decision.reason_code, "empty_model_output")
        self.assertEqual(result.parsed_project.fail_closed_reason, "empty_model_output")

    def test_malformed_text_reports_that_text_arrived_but_no_producer_identity(self) -> None:
        """The boundary between "nothing arrived" and "something unusable arrived".

        Model text did arrive here, so ``response_text_observed`` is True -- the
        fact is not lost. But the text produced no project, so the identity and
        measurement fields stay empty and the raw text is withheld. Both facts are
        asserted separately so neither can be traded for the other.
        """
        provider = _InjectedProvider("{this is not json")
        result = self.run_flow(provider)

        evidence = result.generation
        self.assertTrue(evidence.response_text_observed)
        self.assertEqual(evidence.provider_name, "")
        self.assertEqual(evidence.model_name, "")
        self.assertIsNone(evidence.usage_input_tokens)
        self.assertFalse(evidence.usage_present)
        self.assertEqual(evidence.raw_model_text, "")
        self.assertEqual(evidence.decision.reason_code, "malformed_json")
        self.assertEqual(result.parsed_project.fail_closed_reason, "malformed_json")
        self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED)
        # And the projection says so consistently.
        payload = result.to_dict()["generation"]
        self.assertTrue(payload["response_text_observed"])
        self.assertEqual(payload["provider_name"], "")
        self.assertFalse(payload["usage_present"])

    def test_the_generation_run_is_durable_and_records_the_provider_call(self) -> None:
        """Participation is provable from a durable record, not only from the result.

        The generation dispatch writes its history through the service's own run
        store, so the physical provider events survive the call. A report can
        therefore read ``provider_attempt`` / ``provider_result`` back from storage
        instead of trusting the returned object.
        """
        provider = _InjectedProvider(_fake_project())
        result = self.run_flow(provider)

        record = self.service_record(result)
        self.assertIsNotNone(record, "the generation run must have a durable record")
        attempts = [
            dict(event.metadata)
            for event in record.events
            if event.event_type.name == "PROVIDER_ATTEMPT"
        ]
        self.assertTrue(attempts, "no provider attempt was recorded durably")
        self.assertEqual(attempts[0].get("provider"), FAKE_PROVIDER_NAME)
        self.assertEqual(attempts[0].get("model"), FAKE_MODEL_NAME)
        results = [
            dict(event.metadata)
            for event in record.events
            if event.event_type.name == "PROVIDER_RESULT"
        ]
        self.assertTrue(results)
        self.assertTrue(results[-1].get("success"))
        self.assertEqual(results[-1].get("usage", {}).get("input_tokens"), 101)
        self.assertEqual(results[-1].get("usage", {}).get("output_tokens"), 202)

    def service_record(self, result):
        """Read the generation run's durable record through the existing store.

        The run id is derived the same way the workflow derives it, so this reads
        storage rather than an object handed back in the result.
        """
        from app.workflows.project_workflow import _generation_run_id

        return self.run_store.load(_generation_run_id(BUILD_ID))

    def test_a_second_workflow_on_the_same_validator_carries_no_stale_state(self) -> None:
        """One validator reused across two invocations leaks nothing into the second.

        The first invocation succeeds; the transport is then told to answer
        nothing and the second invocation fails closed. Two separate facts are
        asserted, because they have different causes and only one of them is a
        stale-state risk:

        * the generator's **own** attempt fields
          (``last_provider_name`` / ``last_model_name`` / ``last_usage``) are empty
          on this path for a structural reason: the call is made by the dispatch,
          not by the validator, so the validator has no attempt of its own to
          report. Provider identity therefore comes from the ``TaskResult`` the
          provider filled in, which is what ``GenerationEvidence`` reads first;
        * the **validation report** is the real stale-state risk, and it is gated:
          a fail-closed reply does not update it, so an unconditional read would
          return the first invocation's errors beside a decision that failed for an
          unrelated reason. Both invocations must therefore report no validation
          errors of their own.
        """
        transport = _InjectedProvider(_fake_project())
        validator = self.validator(transport)

        first = run_project_workflow(
            self.service(transport), REGISTRY, BUILD_ID, ai_provider=validator
        )
        # Identity comes from the object the provider itself filled in.
        self.assertEqual(first.generation.provider_name, FAKE_PROVIDER_NAME)
        self.assertEqual(first.generation.model_name, FAKE_MODEL_NAME)
        self.assertEqual(first.generation.usage_input_tokens, 101)
        self.assertTrue(first.generation.usage_present)
        self.assertEqual(first.generation.validation_errors, ())
        # The validator produced a decision from the text it was handed, and it
        # reports no attempt of its own because it made no call of its own.
        self.assertTrue(validator.last_response_produced_a_decision)
        self.assertEqual(validator.last_provider_name, "")
        self.assertIsNone(validator.last_usage)

        # The SAME transport and the SAME validator; only the answer changes.
        self.tearDown()
        self.setUp()
        transport.set_output("")
        second = run_project_workflow(
            self.service(transport), REGISTRY, BUILD_ID, ai_provider=validator
        )

        self.assertEqual(second.verdict, VERDICT_GENERATION_FAILED)
        self.assertEqual(second.generation.provider_name, "")
        self.assertEqual(second.generation.model_name, "")
        self.assertIsNone(second.generation.usage_input_tokens)
        self.assertIsNone(second.generation.usage_output_tokens)
        self.assertIsNone(second.generation.usage_estimated_cost)
        self.assertFalse(second.generation.usage_present)
        self.assertFalse(second.generation.response_text_observed)
        self.assertEqual(second.generation.raw_model_text, "")
        # The gate is closed, the producer view withholds everything, and -- the
        # stale-state assertion -- no earlier validation report is carried over.
        self.assertFalse(validator.last_response_produced_a_decision)
        self.assertEqual(validator.producer_provider_name, "")
        self.assertEqual(validator.producer_model_name, "")
        self.assertIsNone(validator.producer_usage)
        self.assertEqual(second.generation.validation_errors, ())

    def test_a_fail_closed_decision_carries_no_earlier_runs_validation_errors(self) -> None:
        """The stale-state defect, pinned directly.

        ``AIDecisionProvider`` does not update ``last_validation_report`` on a
        fail-closed reply, so an unconditional read returns whatever an *earlier*
        invocation left there. That would report another run's validation errors
        beside a decision that failed for an unrelated reason. The read is gated on
        the reply having actually produced a decision; this test fails if that gate
        is removed.
        """
        from app.decision.validator import DecisionValidationReport
        from app.workflows.project_generation import decision_for_model_text

        validator = self.validator(_InjectedProvider(_fake_project()))
        # A report left behind by an earlier, unrelated invocation.
        validator.last_validation_report = DecisionValidationReport(
            valid=False, errors=("stale_error_from_an_earlier_run",)
        )
        validator.last_response_produced_a_decision = False

        decision, errors = decision_for_model_text(
            validator, "{ this is not json", run_id="run-x", task_id="task-x"
        )

        self.assertEqual(decision.reason_code, "malformed_json")
        self.assertEqual(
            errors,
            (),
            "a fail-closed decision must not carry an earlier run's validation errors",
        )

    def test_the_validation_errors_of_a_produced_decision_are_reported(self) -> None:
        """The positive control for the gate: it really does open.

        Without this, the previous test would also pass if the errors were simply
        never returned at all.
        """
        from app.workflows.project_generation import decision_for_model_text

        validator = self.validator(_InjectedProvider(_fake_project()))
        decision, errors = decision_for_model_text(
            validator, _fake_project(), run_id="run-y", task_id="task-y"
        )

        self.assertTrue(validator.last_response_produced_a_decision)
        self.assertEqual(decision.reason_code, "project_ready")
        report = validator.last_validation_report
        self.assertIsNotNone(report)
        self.assertEqual(errors, tuple(report.errors or ()))

    def test_the_decision_member_is_validated_by_the_existing_layer(self) -> None:
        provider = _InjectedProvider(_fake_project())
        result = self.run_flow(provider)

        decision = result.generation.decision
        self.assertIsNotNone(decision)
        self.assertEqual(decision.decision_type.value, "CONTINUE")
        self.assertEqual(decision.action.value, "EXECUTE")
        self.assertEqual(decision.reason_code, "project_ready")
        self.assertEqual(result.generation.validation_errors, ())

    def test_a_model_that_refuses_to_continue_is_still_reported_as_a_decision(self) -> None:
        """The decision member is validated, not ignored.

        A model that answers the schema with ``FAIL``/``FAIL_RUN`` produces that
        very decision. The workflow records it as the observed recommendation; it
        does not turn it into an action of its own, because a decision has no
        execution authority anywhere in this project.
        """
        provider = _InjectedProvider(_fake_project(decision={
            "decision_type": "FAIL",
            "action": "FAIL_RUN",
            "reason_code": "cannot_satisfy_criteria",
            "rationale": "the requested criteria are contradictory",
            "confidence": 0.8,
        }))
        result = self.run_flow(provider)

        decision = result.generation.decision
        self.assertEqual(decision.decision_type.value, "FAIL")
        self.assertEqual(decision.action.value, "FAIL_RUN")
        self.assertEqual(decision.reason_code, "cannot_satisfy_criteria")


class ModelContentDeterminesTheProjectTests(GenerationTestCase):
    """The model's text is the project. Two texts, two different disks."""

    def test_two_different_model_outputs_produce_different_file_contents(self) -> None:
        first_provider = _InjectedProvider(_fake_project(add_operator="+"))
        first = self.run_flow(first_provider)
        self.assertTrue(first.success, first.error)
        first_module = self.read("mod.py")
        first_readme = self.read("README.md")
        first_test = self.read("tests/test_mod.py")

        # A second, independent workspace and service.
        self.tearDown()
        self.setUp()

        # A different but still correct implementation, and a different test that
        # also passes: the difference asserted below is a difference in content,
        # not a difference between passing and failing.
        second_provider = _InjectedProvider(_fake_project(
            module_body=(
                '"""A generated module."""\n\n\n'
                "def add(a, b):\n    return sum((a, b))\n"
            ),
            readme_line="a different model answer",
            test_body="self.assertEqual(mod.add(5, 7), 12)",
        ))
        second = self.run_flow(second_provider)
        self.assertTrue(second.success, second.error)

        self.assertIn("return a + b", first_module)
        self.assertIn("return sum((a, b))", self.read("mod.py"))
        self.assertNotEqual(first_module, self.read("mod.py"))
        self.assertNotEqual(first_readme, self.read("README.md"))
        self.assertNotEqual(first_test, self.read("tests/test_mod.py"))

    def test_the_declared_creation_command_is_not_dispatched_on_the_model_path(self) -> None:
        """Composing a provider replaces the source of the text; it adds no second writer.

        The declared creation command writes a marker file. If it had run, the
        marker would exist. It must not, because its output would overwrite the
        files the model produced and the workflow's own report would then
        misattribute them.
        """
        provider = _InjectedProvider(_fake_project())
        result = self.run_flow(provider)

        self.assertFalse(
            (self.root / "DECLARED_CREATION_COMMAND_RAN.txt").exists(),
            "the declared creation command must not be dispatched on the model path",
        )
        self.assertFalse(result.declared_command_dispatched)
        self.assertEqual(result.verdict, VERDICT_PROJECT_VERIFIED)


class FailClosedGenerationTests(GenerationTestCase):
    """Nothing unusable ever becomes a project, and each reason is named."""

    def _assert_no_project(self, result, expected_reason: str) -> None:
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED)
        self.assertEqual(result.error, expected_reason)
        self.assertEqual(result.created_files, ())
        self.assertIsNone(result.materialisation)
        self.assertEqual(self.disk_files(), ["preexisting.txt"])

    def test_empty_model_output_creates_nothing(self) -> None:
        result = self.run_flow(_InjectedProvider(""))
        self._assert_no_project(result, "empty_model_output")
        self.assertEqual(result.parsed_project.fail_closed_reason, "empty_model_output")

    def test_whitespace_only_model_output_creates_nothing(self) -> None:
        result = self.run_flow(_InjectedProvider("   \n\t  "))
        self._assert_no_project(result, "empty_model_output")

    def test_malformed_json_creates_nothing(self) -> None:
        result = self.run_flow(_InjectedProvider("{not json at all"))
        self._assert_no_project(result, "malformed_json")
        self.assertEqual(result.parsed_project.fail_closed_reason, "malformed_json")

    def test_json_that_is_not_an_object_creates_nothing(self) -> None:
        result = self.run_flow(_InjectedProvider('["mod.py", "contents"]'))
        self._assert_no_project(result, "malformed_model_output")

    def test_output_without_a_files_member_creates_nothing(self) -> None:
        result = self.run_flow(_InjectedProvider(json.dumps({"decision": {}})))
        self._assert_no_project(result, "model_output_missing_files")

    def test_files_member_that_is_not_an_object_creates_nothing(self) -> None:
        result = self.run_flow(
            _InjectedProvider(json.dumps({"files": ["mod.py"]}))
        )
        self._assert_no_project(result, "model_output_files_not_object")

    def test_an_empty_file_set_creates_nothing(self) -> None:
        result = self.run_flow(_InjectedProvider(json.dumps({"files": {}})))
        self._assert_no_project(result, "model_output_empty_file_set")

    def test_non_text_content_creates_nothing(self) -> None:
        payload = json.dumps({"files": {"mod.py": {"nested": "object"}}})
        result = self.run_flow(_InjectedProvider(payload))
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED)
        # The only declared file was refused, so nothing can be created.
        self.assertEqual(result.error, "model_output_rejected")
        self.assertEqual(self.disk_files(), ["preexisting.txt"])

    def test_a_file_over_the_size_limit_is_refused_and_not_written(self) -> None:
        """A bounded per-file limit, exercised on a real refusal.

        The oversized file is refused and reported; the four admissible files are
        still materialised, because a refusal is a per-file decision and reporting
        it is the point. What must never happen is the oversized body reaching the
        disk.
        """
        provider = _InjectedProvider(_fake_project(extra_files={"big.txt": "x" * 5000}))
        result = run_project_workflow(
            self.service(provider),
            REGISTRY,
            BUILD_ID,
            ai_provider=self.validator(provider),
            max_file_bytes=1024,
        )
        self.assertFalse((self.root / "big.txt").exists())
        reasons = {
            item.path: item.reason
            for item in result.materialisation_rejections
        }
        self.assertEqual(reasons.get("big.txt"), "file_too_large")
        self.assertEqual(
            sorted(result.materialisation.created_files),
            ["README.md", "mod.py", "tests/__init__.py", "tests/test_mod.py"],
        )
        self.assertIn("big.txt", [item.path for item in result.parsed_project.rejections])

    def test_a_project_whose_every_file_is_too_large_creates_nothing(self) -> None:
        provider = _InjectedProvider(json.dumps({
            "files": {"a.txt": "x" * 2000, "b.txt": "y" * 2000}
        }))
        result = run_project_workflow(
            self.service(provider),
            REGISTRY,
            BUILD_ID,
            ai_provider=self.validator(provider),
            max_file_bytes=1024,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED)
        self.assertEqual(result.error, "model_output_too_large")
        self.assertEqual(self.disk_files(), ["preexisting.txt"])

    def test_the_successful_model_response_is_accepted_as_a_control(self) -> None:
        """The positive control: the same harness does create the project."""
        provider = _InjectedProvider(_fake_project())
        result = self.run_flow(provider)
        self.assertIsNotNone(result.parsed_project)
        self.assertTrue(result.parsed_project.ok)
        self.assertEqual(result.parsed_project.file_count, 4)
        self.assertIsNotNone(result.materialisation)


class UnsafePathTests(GenerationTestCase):
    """Every unsafe path is refused, and no refused path becomes a file."""

    def _run_with_files(self, files: dict[str, str], **kwargs):
        provider = _InjectedProvider(json.dumps({"files": files}))
        # One provider instance is used for both the service and the validator,
        # so the text, the call record, and the usage all come from one transport.
        return run_project_workflow(
            self.service(provider),
            REGISTRY,
            BUILD_ID,
            ai_provider=self.validator(provider),
            **kwargs,
        )

    def test_unsafe_paths_are_refused_and_nothing_is_created(self) -> None:
        cases = (
            ("/etc/passwd", "absolute_path"),
            ("C:/Windows/system.ini", "absolute_path"),
            ("//host/share/x.py", "absolute_path"),
            ("../escape.py", "path_traversal"),
            ("src/../../escape.py", "path_traversal"),
            ("a\\b.py", "not_a_relative_posix_path"),
            (".git/hooks/pre-commit", "protected_path"),
            (".forge/state.json", "protected_path"),
            (".env", "protected_path"),
            (".env/local", "protected_path"),
            ("secrets/id_rsa", "secret_path"),
            (".ssh/authorized_keys", "secret_path"),
            ("keys/deploy.pem", "secret_path"),
            ("server.pem", "secret_path"),
            ("credentials.json", "secret_path"),
        )
        for unsafe, expected_reason in cases:
            with self.subTest(path=unsafe):
                # A payload whose ONLY file is the unsafe one: nothing survives,
                # so the whole response fails closed.
                result = self._run_with_files({unsafe: "payload"})
                self.assertFalse(result.success, unsafe)
                self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED, unsafe)
                self.assertEqual(self.disk_files(), ["preexisting.txt"], unsafe)
                reasons = [item.reason for item in result.parsed_project.rejections]
                self.assertIn(expected_reason, reasons, f"{unsafe}: {reasons}")
                for reason in reasons:
                    self.assertIn(reason, MATERIALISATION_REASON_CODES)

    def test_a_safe_file_survives_beside_an_unsafe_one_and_the_unsafe_one_is_reported(self) -> None:
        """A refusal is per-file, but the response as a whole is never silent.

        One good file and one absolute path: the good file is written, and the
        refusal is reported in the same result. Nothing about the refused path
        appears on disk or inside the workspace.
        """
        result = self._run_with_files({
            "good.py": "VALUE = 1\n",
            "/tmp/absolute_escape.py": "VALUE = 2\n",
            "../traversal_escape.py": "VALUE = 3\n",
        })

        self.assertEqual(sorted(result.materialisation.created_files), ["good.py"])
        self.assertEqual(self.read("good.py"), "VALUE = 1\n")
        self.assertFalse((self.base / "traversal_escape.py").exists())
        rejections = {item.reason for item in result.materialisation_rejections}
        self.assertIn("absolute_path", rejections)
        self.assertIn("path_traversal", rejections)
        self.assertEqual(
            result.generated_files, ("good.py",),
            "the refused paths are not part of the model's accepted file list",
        )

    def test_parsing_refuses_unsafe_paths_on_its_own(self) -> None:
        """The parser is independently safe; it does not rely on the writer."""
        parsed = parse_model_project(json.dumps({"files": {"../escape.py": "x"}}))
        self.assertFalse(parsed.ok)
        self.assertEqual(parsed.files, ())
        self.assertEqual(parsed.fail_closed_reason, "model_output_unsafe_path")

    def test_a_path_that_escapes_once_resolved_is_refused(self) -> None:
        """The second line of defence: the *resolved* path must stay inside.

        A text-only check ("no ``..`` segment") would pass this path. The directory
        ``linked`` is a link to a directory outside the workspace, so
        ``linked/x.py`` is a relative path with no traversal segment that still
        resolves outside the root. Only a resolve-and-compare check refuses it.

        A symlink cannot always be created on Windows without elevation, so a
        Windows directory junction is used as the fallback. Both are reparse
        points, which is the mechanism the check must not be fooled by. If neither
        can be created, the test is skipped rather than weakened, and the skip is
        reported.
        """
        outside = self.base / "outside_dir"
        outside.mkdir()
        link = self.root / "linked"
        linked = False
        try:
            os.symlink(outside, link, target_is_directory=True)
            linked = link.is_dir()
        except (OSError, NotImplementedError):
            linked = False
        if not linked:
            # Junctions do not require elevation on Windows.
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
                text=True,
                check=False,
            )
            linked = link.is_dir() and completed.returncode == 0
        if not linked:
            self.skipTest("cannot create a symlink or junction in this environment")

        try:
            result = self._run_with_files({"linked/x.py": "VALUE = 1\n"})

            self.assertFalse(result.success)
            # The refusal happens at materialisation time, because a path that
            # does not exist yet cannot be resolved to a link. That is exactly why
            # the resolve-and-compare check runs at the last moment before the
            # first byte is written, and not only when the text is parsed.
            reasons = [item.reason for item in result.materialisation_rejections]
            self.assertIn("path_escapes_workspace", reasons, reasons)
            self.assertFalse((outside / "x.py").exists())
            self.assertFalse((self.root / "linked" / "x.py").exists())
            self.assertEqual(result.verdict, "materialisation_failed")
        finally:
            _remove_link(link)


class ExistingFileConflictTests(GenerationTestCase):
    """An operator's file is not overwritten by a model unless they said so."""

    def test_an_existing_user_file_is_not_overwritten_without_permission(self) -> None:
        original = "the operator wrote this\n"
        (self.root / "README.md").write_text(original, encoding="utf-8")

        provider = _InjectedProvider(_fake_project(readme_line="model text"))
        result = self.run_flow(provider)

        self.assertEqual(self.read("README.md"), original)
        self.assertNotIn("README.md", result.materialisation.file_list)
        rejections = {
            item.path: item.reason for item in result.materialisation.rejections
        }
        self.assertEqual(rejections.get("README.md"), "conflicts_with_existing_file")
        # The other three files were still written: the refusal is per file.
        self.assertIn("mod.py", result.materialisation.created_files)

    def test_an_existing_user_file_is_written_when_permission_is_granted(self) -> None:
        original = "the operator wrote this\n"
        (self.root / "README.md").write_text(original, encoding="utf-8")

        provider = _InjectedProvider(_fake_project(readme_line="model text"))
        result = self.run_flow(provider, permitted_overwrite_paths=("README.md",))

        self.assertNotEqual(self.read("README.md"), original)
        self.assertIn("model text", self.read("README.md"))
        self.assertIn("README.md", result.materialisation.overwritten_files)
        self.assertNotIn("README.md", result.materialisation.created_files)

    def test_permission_for_one_path_does_not_permit_another(self) -> None:
        (self.root / "README.md").write_text("keep me\n", encoding="utf-8")
        (self.root / "mod.py").write_text("keep me too\n", encoding="utf-8")

        provider = _InjectedProvider(_fake_project())
        result = self.run_flow(provider, permitted_overwrite_paths=("README.md",))

        self.assertEqual(self.read("mod.py"), "keep me too\n")
        self.assertIn("README.md", result.materialisation.overwritten_files)


class SecretRefusalTests(GenerationTestCase):
    """A credential is never written into the project by a model."""

    def test_a_path_the_recognizer_knows_is_refused(self) -> None:
        for unsafe in (".env", "config/.env", "certs/server.pem", "secrets.json",
                       "id_ed25519", ".npmrc"):
            with self.subTest(path=unsafe):
                # Both the recognizer and the module under test must agree, which
                # is asserted so a future change to either cannot drift silently.
                last = unsafe.split("/")[-1]
                self.assertTrue(
                    is_secret_file(last) or is_secret_directory(unsafe.split("/")[0]),
                    f"{unsafe} should be recognised as credential material",
                )
                parsed = parse_model_project(
                    json.dumps({"files": {unsafe: "placeholder"}})
                )
                self.assertFalse(parsed.ok, unsafe)
                self.assertEqual(parsed.files, (), unsafe)

    def test_credential_looking_content_is_refused(self) -> None:
        samples = {
            "config/settings.py": 'API_KEY = "abcdefghijklmnopqrstuvwxyz123456"\n',
            "notes.md": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n",
            "auth.py": (
                'TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.'
                'dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"\n'
            ),
            "deploy.sh": "export AWS_KEY=AKIAIOSFODNN7EXAMPLEKEY\n",
        }
        for path, content in samples.items():
            with self.subTest(path=path):
                self.assertIsNotNone(secret_content_reason(content), path)
                # A payload with the credential file and one ordinary file: the
                # credential file is refused, the ordinary one is written.
                provider = _InjectedProvider(json.dumps({
                    "files": {path: content, "harmless.py": "VALUE = 1\n"}
                }))
                result = run_project_workflow(
                    self.service(provider),
                    REGISTRY,
                    BUILD_ID,
                    ai_provider=self.validator(provider),
                )
                self.assertFalse((self.root / path).exists(), path)
                reasons = [item.reason for item in result.materialisation_rejections]
                self.assertIn("secret_content", reasons, f"{path}: {reasons}")
                self.assertTrue((self.root / "harmless.py").is_file())

    def test_ordinary_source_is_not_refused_as_a_secret(self) -> None:
        """The negative control: the check must not fire on ordinary code.

        A filter that refuses everything is not a filter. These are the shapes a
        generated project legitimately contains, including the safe way to obtain
        a value from the environment and a placeholder that inherits from one.
        """
        harmless = (
            "TOKEN_ENV_VAR = 'APP_TOKEN'\n",
            "import os\nVALUE = os.environ.get('TOKEN', '')\n",
            "import os\nVALUE = os.environ['API_KEY']\n",
            "KEY = os.getenv('OPENAI_API_KEY', '')\n",
            "class Credentials:\n    pass\n",
            "SECRET_NAME = 'a-short-name'\n",
            "def secrets_are_not_stored_here():\n    return True\n",
            "PASSWORD_FIELD = 'password'\n",
            "KEY_LENGTH = 32\n",
            "PUBLIC_KEY_FILE = 'id_rsa.pub'\n",
        )
        for content in harmless:
            with self.subTest(content=content):
                self.assertIsNone(secret_content_reason(content), content)

    def test_a_literal_assignment_is_still_refused_beside_an_environment_lookup(self) -> None:
        """The exemption is narrow, and it does not short-circuit the scan.

        A file that reads a value from the environment *and* hard-codes another is
        refused for the hard-coded one. This is the regression that a
        first-match-only scan caused: the environment lookup came first, so the
        scan returned "not a secret" and the literal was written to disk.
        """
        self.assertIsNone(secret_content_reason("K = os.environ['API_KEY']\n"))
        self.assertIsNotNone(
            secret_content_reason(
                "K = os.environ['API_KEY']\n"
                "OTHER = 'abcdefghijklmnopqrstuvwxyz'\n"
                "API_KEY = 'abcdefghijklmnopqrstuvwxyz'\n"
            )
        )
        # Every order is refused, not only the ones that happen to put the literal
        # first.
        for source in (
            "API_KEY = 'abcdefghijklmnopqrstuvwxyz'\nK = os.environ['API_KEY']\n",
            "K = os.environ['API_KEY']\nAPI_KEY = 'abcdefghijklmnopqrstuvwxyz'\n",
            "A = os.getenv('X')\nB = os.environ['Y']\nDB_PASSWORD = 'a-very-long-passphrase'\n",
        ):
            with self.subTest(source=source):
                self.assertIsNotNone(secret_content_reason(source), source)

    def test_a_literal_secret_after_an_environment_lookup_is_refused_as_project_content(self) -> None:
        """The same regression, observed end to end on the files on disk.

        This is the security-relevant form of the previous test: the literal must
        not reach the workspace, and the harmless file beside it must.
        """
        credential_body = (
            "import os\n"
            "GREETING = 'hello-world-this-is-an-ordinary-constant'\n"
            "K = os.environ['API_KEY']\n"
            "API_KEY = 'abcdefghijklmnopqrstuvwxyz123456'\n"
        )
        self.assertIsNotNone(secret_content_reason(credential_body))

        provider = _InjectedProvider(json.dumps({
            "files": {"config.py": credential_body, "harmless.py": "VALUE = 1\n"}
        }))
        result = run_project_workflow(
            self.service(provider),
            REGISTRY,
            BUILD_ID,
            ai_provider=self.validator(provider),
        )
        self.assertFalse((self.root / "config.py").exists())
        reasons = [item.reason for item in result.materialisation_rejections]
        self.assertIn("secret_content", reasons, reasons)
        self.assertTrue((self.root / "harmless.py").is_file())

    def test_a_public_key_name_is_not_refused_as_a_credential(self) -> None:
        """The recognizer's own exemption is reused rather than re-derived.

        ``id_rsa.pub`` is a public key and ``known_hosts`` is public host data;
        the shared recognizer deliberately allows both, and no rule here may
        re-introduce the false positive that earlier work removed.
        """
        for content in (
            "PUBLIC_KEY_FILE = 'id_rsa.pub'\n",
            "KNOWN_HOSTS_FILE = 'known_hosts'\n",
            "SSH_KEY = 'id_ed25519.pub'\n",
        ):
            with self.subTest(content=content):
                self.assertIsNone(secret_content_reason(content), content)
        # The private half of the same pair is still refused when it is named as a
        # file -- the form that matters, because that is how a credential path is
        # written into project content.
        self.assertIsNotNone(secret_content_reason("PRIVATE_KEY_PATH = '.ssh/id_rsa'\n"))

    def test_a_bare_prose_mention_of_a_key_name_is_allowed(self) -> None:
        """A documented limitation, asserted so it cannot drift into a surprise.

        The content rule recognises a credential *file reference*, not a word. A
        README that tells a reader where their key lives is ordinary
        documentation, and refusing it would break legitimate projects. The
        content check does not claim to be a general secret classifier, and this
        test states that limit rather than leaving it implied.
        """
        for content in (
            "Place your private key at id_rsa inside your home directory.\n",
            "The tool reads credentials from your environment.\n",
        ):
            with self.subTest(content=content):
                self.assertIsNone(secret_content_reason(content), content)


class MaterialisedFilesAreInsideTheWorkspaceTests(GenerationTestCase):
    """Containment is proved by resolving and comparing roots, not by trust."""

    def test_every_created_file_resolves_inside_the_permitted_workspace(self) -> None:
        provider = _InjectedProvider(_fake_project(extra_files={
            "pkg/__init__.py": "",
            "pkg/deep/nested/module.py": "DEEP = True\n",
            "docs/notes/todo.md": "todo\n",
        }))
        result = self.run_flow(provider)

        self.assertTrue(result.materialisation.file_list)
        root_resolved = self.root.resolve()
        self.assertEqual(
            Path(result.materialisation.workspace_root), root_resolved
        )
        for relative in result.materialisation.file_list:
            with self.subTest(relative=relative):
                resolved = (self.root / relative).resolve()
                # It exists, it is a real file, and its resolved path is inside
                # the permitted root. A text check could be defeated by a link;
                # this cannot.
                self.assertTrue(resolved.is_file())
                self.assertEqual(
                    resolved.relative_to(root_resolved).as_posix(), relative
                )
                self.assertTrue(resolved.is_relative_to(root_resolved))

    def test_nothing_was_written_outside_the_workspace_during_generation(self) -> None:
        before = sorted(item.name for item in self.base.iterdir())
        provider = _InjectedProvider(_fake_project())
        self.run_flow(provider)
        after = sorted(item.name for item in self.base.iterdir())

        # The base directory holds the workspace and nothing else: no generation
        # artefact landed beside it. ``__pycache__`` is written by the generated
        # project's own interpreter inside the workspace and is irrelevant here.
        self.assertEqual(before, ["ws"])
        self.assertEqual(after, ["ws"])


class GeneratedTestsReallyExecuteTests(GenerationTestCase):
    """The second declared command runs the model's own tests, and is measured."""

    def test_the_generated_projects_own_tests_execute_and_pass(self) -> None:
        provider = _InjectedProvider(_fake_project())
        result = self.run_flow(provider)

        verification = result.verification
        self.assertIsNotNone(verification)
        self.assertEqual(verification.command, VERIFY_COMMAND)
        self.assertEqual(verification.step_declaration_id, VERIFY_ID)
        self.assertEqual(verification.observed_exit_code, 0)
        self.assertEqual(verification.outcome_status, "EXECUTION_SUCCESS")
        self.assertTrue(verification.success, verification.messages)

        # Exit 0 alone would also be produced by a run that collected no tests,
        # so the diagnostic output is asserted too.
        diagnostics = f"{verification.stdout}\n{verification.stderr}"
        self.assertIn("Ran 1 test", diagnostics)
        self.assertIn("OK", diagnostics)

        self.assertTrue(result.success, result.error)
        # The project's own tests really ran and really passed, so the verdict
        # says the project was verified. It stays transport-neutral: the injected
        # transport is a separate fact, carried by ``generation.mock_transport``,
        # and this verdict never claims a real remote model answered.
        self.assertEqual(result.verdict, VERDICT_PROJECT_VERIFIED)
        self.assertTrue(result.generation.mock_transport)
        # The acceptance verdict is NOT manufactured on the model path.
        self.assertEqual(result.acceptance_status, "not_evaluated")

    def test_a_failing_generated_test_yields_a_non_zero_exit_and_a_not_verified_verdict(self) -> None:
        provider = _InjectedProvider(_failing_project())
        result = self.run_flow(provider)

        verification = result.verification
        self.assertIsNotNone(verification)
        self.assertEqual(verification.observed_exit_code, 1)
        self.assertEqual(verification.outcome_status, "EXECUTION_FAILURE")
        self.assertFalse(verification.success)
        diagnostics = f"{verification.stdout}\n{verification.stderr}"
        self.assertIn("FAILED", diagnostics)

        # The project was created -- and it is still not verified.
        self.assertTrue((self.root / "mod.py").is_file())
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_TESTS_FAILED)
        self.assertEqual(result.error, "verification_exit_code_mismatch")
        self.assertNotEqual(result.verdict, VERDICT_REAL_PROVIDER_NOT_VERIFIED)

    def test_a_non_zero_exit_is_never_reported_as_verified(self) -> None:
        """The property, asserted directly across both outcomes."""
        for payload, expected_exit in (
            (_fake_project(), 0),
            (_failing_project(), 1),
        ):
            with self.subTest(exit=expected_exit):
                self.tearDown()
                self.setUp()
                result = self.run_flow(_InjectedProvider(payload))
                self.assertEqual(result.verification.observed_exit_code, expected_exit)
                if expected_exit != 0:
                    self.assertFalse(result.success)
                    self.assertFalse(result.verification.success)
                    self.assertEqual(result.verdict, VERDICT_TESTS_FAILED)
                else:
                    self.assertTrue(result.success)

    def test_a_missing_generated_test_file_produces_a_real_non_zero_exit(self) -> None:
        """A model that omits its tests cannot be reported as verified.

        The test module also has to be discovered, so the payload names only the
        module and the README. The declared verification command then really runs
        and really fails to find a suite.
        """
        provider = _InjectedProvider(json.dumps({
            "files": {
                "mod.py": "def add(a, b):\n    return a + b\n",
                "README.md": "# no tests here\n",
            }
        }))
        result = self.run_flow(provider)

        self.assertIsNotNone(result.verification)
        self.assertNotEqual(result.verification.observed_exit_code, 0)
        self.assertFalse(result.verification.success)
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_TESTS_FAILED)


class MockIsNeverPresentedAsRealTests(GenerationTestCase):
    """Injected transport is marked, in data, everywhere it appears."""

    def test_the_evidence_marks_the_transport_as_injected(self) -> None:
        provider = _InjectedProvider(_fake_project())
        result = self.run_flow(provider)

        self.assertTrue(result.generation.mock_transport)
        self.assertTrue(result.to_dict()["generation"]["mock_transport"])
        # ``transport`` says how the text arrived; ``mock_transport`` says what
        # produced it. Both are needed and neither is a real-provider claim.
        self.assertEqual(result.generation.transport, "task_result")
        self.assertEqual(
            result.verdict_expected_with_real_provider,
            "REAL LLM PROJECT GENERATION VERIFIED",
        )
        # The verdict this path can reach is NOT the real-provider verdict.
        self.assertNotEqual(result.verdict, "REAL LLM PROJECT GENERATION VERIFIED")

    def test_the_honest_limit_travels_with_the_result(self) -> None:
        provider = _InjectedProvider(_fake_project())
        result = self.run_flow(provider)
        messages = result.to_dict()["messages"]
        self.assertIn(MOCK_TRANSPORT_MARKER, messages)
        # And the raw model text is not in the JSON projection at all.
        rendered = json.dumps(result.to_dict())
        self.assertNotIn("def add(a, b)", rendered)


class AuthorizationIsUnbrokenTests(GenerationTestCase):
    """The existing approval and authorization behaviour is untouched."""

    def test_a_required_unresolved_approval_still_performs_nothing(self) -> None:
        """Approval gates the declared command; it is not bypassed by generation.

        With no resolver, the declared verification command is not authorized. The
        model's files were already materialised before that point -- which is a
        separate, reported fact -- but no process ran and nothing is verified.
        """
        provider = _InjectedProvider(_fake_project())
        result = run_project_workflow(
            self.service(
                provider, approval_policy=ApprovalPolicy(("execute",))
            ),
            REGISTRY,
            BUILD_ID,
            ai_provider=self.validator(provider),
        )

        self.assertFalse(result.success)
        self.assertIsNotNone(result.verification)
        self.assertFalse(result.verification.success)
        self.assertIsNone(result.verification.observed_exit_code)
        self.assertNotEqual(result.verdict, VERDICT_REAL_PROVIDER_NOT_VERIFIED)

    def test_the_declared_command_path_still_requires_approval(self) -> None:
        """The unchanged path: no generation provider, approval unresolved.

        This is the pre-existing behaviour the additive change must not break. The
        declared creation command is not dispatched, no file is created, and the
        verdict names the missing approval.
        """
        provider = _InjectedProvider(_fake_project())
        service = self.service(
            provider, approval_policy=ApprovalPolicy(("execute",))
        )
        result = run_project_workflow(service, REGISTRY, BUILD_ID)

        self.assertEqual(result.verdict, VERDICT_APPROVAL_REQUIRED)
        self.assertFalse(result.success)
        self.assertEqual(result.state, "WAITING_FOR_APPROVAL")
        self.assertEqual(result.acceptance_status, "not_evaluated")
        self.assertIsNone(result.verification)
        self.assertEqual(result.created_files, ())
        self.assertEqual(self.disk_files(), ["preexisting.txt"])
        self.assertEqual(provider.call_count, 0, "no model may be consulted")

    def test_a_rejected_approval_still_performs_nothing(self) -> None:
        """A resolver that answers REJECTED is not an approval."""
        from app.tools.approval import ApprovalResolution, ApprovalState

        class _RejectingResolver:
            def resolve(self, request):
                return ApprovalResolution(
                    decision=ApprovalState.REJECTED,
                    approved_fingerprint=request.intent_fingerprint or "",
                    approval_id="rejected",
                )

        provider = _InjectedProvider(_fake_project())
        result = run_project_workflow(
            self.service(
                provider,
                approval_policy=ApprovalPolicy(("execute",)),
                approval_resolver=_RejectingResolver(),
            ),
            REGISTRY,
            BUILD_ID,
            ai_provider=self.validator(provider),
        )

        self.assertFalse(result.success)
        self.assertNotEqual(result.verdict, VERDICT_REAL_PROVIDER_NOT_VERIFIED)
        self.assertIsNone(result.verification.observed_exit_code)


if __name__ == "__main__":
    unittest.main()
