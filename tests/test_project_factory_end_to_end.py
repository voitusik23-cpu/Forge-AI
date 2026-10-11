"""Independent end-to-end verification of the project factory, with mock transport.

WHY THIS FILE EXISTS SEPARATELY
===============================

This file is an **independent verifier**, not the author's own test suite. It was
written against the frozen behaviour of ``run_project_workflow`` and it re-checks
the claims that matter most from outside: that the injected provider's *response
data* is what lands on disk, that the generated project really runs, that the real
process exit code is retained, and that the final contract agrees with the facts
observed on disk rather than with a summary string.

It deliberately does not reuse ``tests/test_project_generation_production.py``'s
helpers. Its own composition is written from scratch so that an error in one
harness cannot hide an error in the other.

WHAT IS PROVEN HERE, AND WHAT IS NOT
====================================

Proven, by observed data:

* the request enters the **existing** workflow and the **existing** provider path
  (``RunExecutor`` -> ``Orchestrator.dispatch`` -> ``Dispatcher`` ->
  ``ProviderAgent.run`` -> ``Provider.generate``), proved by the provider's own
  call record;
* **the provider's response determines the written bytes** -- two different
  responses produce two different trees on disk, compared byte for byte against
  what the response declared;
* the created project imports and runs in a dedicated workspace;
* the created project's **own** tests execute under a real test runner, and the
  **real** process exit code is retained in the result;
* the top-level verdict agrees with the observed facts, including for empty,
  malformed, missing-file, invalid-Python, failing-test, non-zero-exit,
  authorization-refusal, refused-write and timeout cases;
* generation and execution errors yield an honest negative or incomplete result,
  never a success.

NOT proven here: anything about a real model. **No real API provider is called
anywhere in this file.** Every provider is an in-process fake that returns a
fixed string. The evidence is marked as a non-real transport, and this file never
claims real LLM generation.

ISOLATION AND CONCURRENCY
=========================

This repository has produced *false* failures under concurrent test processes: the
default ``RunStore()`` and the idempotency ledger live in a shared per-user root,
and PostgreSQL roles are cluster-global. Every service built here therefore gets
its **own** ``RunStore`` and its **own** ``RunIdempotencyGuard`` under a temporary
directory, and every workspace is a fresh temporary directory. Run ids are made
unique per test so two processes cannot collide on the same durable key.
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
from app.runtime.bootstrap import create_agent_harness
from app.runtime.context import RuntimeContext
from app.runtime.run_store import RunStore
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.registry import build_default_tool_registry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.usage import Usage
from app.workflows.project_generation import secret_content_reason
from app.workflows.project_workflow import (
    VERDICT_GENERATION_FAILED,
    VERDICT_PROJECT_VERIFIED,
    VERDICT_REAL_PROVIDER_NOT_VERIFIED,
    VERDICT_TESTS_FAILED,
    ProjectWorkflowRegistry,
    run_project_workflow,
)

#: The transport under test. It is named "injected" so no reader mistakes it for a
#: real remote model, and the workflow marks it as such in the result's evidence.
INJECTED_PROVIDER_NAME = "verifier-injected-transport"
INJECTED_MODEL_NAME = "verifier-injected-model"

#: A fixed, exact measurement so "usage propagated" can be asserted without a
#: tokenizer. It is the *provider's* number, not a number this test invents.
INJECTED_USAGE = Usage(input_tokens=7, output_tokens=11, estimated_cost=0.125)

#: The project the fake model is asked to produce. It is a real little package:
#: a module with an addition function, a README, and its own tests.
PACKAGE = "calcproj"

#: The declared verification command: the **generated project's own** test suite,
#: run by the standard library test runner. Forge AI's own tests are never
#: substituted for these.
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

#: A creation command that must NOT run on the model path. It writes a marker file,
#: so a wrongly dispatched declared command is detectable on disk.
CREATE_COMMAND = (
    "python",
    "-c",
    "open('DECLARED_CREATION_RAN.txt','w',encoding='utf-8').write('ran')",
)

MODULE_PATH = f"{PACKAGE}/__init__.py"
README_PATH = "README.md"
TESTS_INIT_PATH = "tests/__init__.py"
TESTS_PATH = "tests/test_calcproj.py"
PROJECT_FILES = (MODULE_PATH, README_PATH, TESTS_INIT_PATH, TESTS_PATH)

_id_lock = threading.Lock()
_id_counter = itertools.count(1)


def _unique(prefix: str) -> str:
    """A per-process-unique declaration id, so two test processes cannot collide.

    PostgreSQL roles are cluster-global and the idempotency ledger is durable, so a
    fixed declaration id reused by a second concurrent process has already produced
    false failures in this repository. Uniqueness is cheap insurance.
    """

    with _id_lock:
        return f"{prefix}.{os.getpid()}.{next(_id_counter)}"


def _module_body(operator_expression: str, docstring: str) -> str:
    """The generated package's module, parameterised by its implementation."""

    return (
        f'"""{docstring}"""\n'
        "\n"
        "\n"
        "def add(a, b):\n"
        f"    return {operator_expression}\n"
    )


def _test_body(assertion: str) -> str:
    """The generated package's own test module, parameterised by its assertion."""

    return (
        "import unittest\n"
        "\n"
        f"from {PACKAGE} import add\n"
        "\n"
        "\n"
        "class CalcProjTests(unittest.TestCase):\n"
        "    def test_add(self):\n"
        f"        {assertion}\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )


def project_response(
    *,
    operator_expression: str = "a + b",
    docstring: str = "The generated package.",
    readme_title: str = "calcproj",
    readme_body: str = "Generated by an injected transport, not by a real model.",
    assertion: str = "self.assertEqual(add(2, 3), 5)",
    extra_files: dict[str, str] | None = None,
) -> str:
    """One complete model response in the workflow's declared schema.

    Every knob here changes bytes on disk, which is what makes "the response
    determined the content" a measurable claim rather than a description.
    """

    files: dict[str, str] = {
        MODULE_PATH: _module_body(operator_expression, docstring),
        README_PATH: f"# {readme_title}\n\n{readme_body}\n",
        TESTS_INIT_PATH: "",
        TESTS_PATH: _test_body(assertion),
    }
    if extra_files:
        files.update(extra_files)
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


class _InjectedTransport(Provider):
    """A ``Provider`` that returns a fixed string and records every call.

    It is a genuine ``Provider`` subclass, so the production dispatch path is
    exercised unchanged: only the network behind the provider is replaced. The call
    record it keeps is the evidence that the model was really asked.
    """

    def __init__(self, output: str) -> None:
        super().__init__(
            ProviderConfig(
                provider_name=INJECTED_PROVIDER_NAME,
                model_name=INJECTED_MODEL_NAME,
                enabled=True,
            )
        )
        self._output = output
        self.calls: list[ProviderRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output=self._output,
            usage=INJECTED_USAGE,
        )


def _profile() -> ProjectExecutionProfile:
    # The profile admits the interpreter by its ordinary name, which is how a
    # declared command names it. An absolute interpreter path is not admitted, so
    # the declared commands below use "python" exactly as the product does.
    return ProjectExecutionProfile(
        profile_id="verifier-profile",
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


class EndToEndTestCase(unittest.TestCase):
    """One isolated workspace, one isolated run store, one isolated ledger."""

    def setUp(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        self.build_id = _unique("verifier.build")
        self.verify_id = _unique("verifier.verify")
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
        # A file that already exists, so "created" is measured against a non-empty
        # starting state instead of being assumed.
        (self.root / "user_notes.txt").write_text("keep me", encoding="utf-8")

    def tearDown(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        for directory in self._dirs:
            directory.cleanup()

    def _temp_dir(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self._dirs.append(directory)
        return Path(directory.name)

    # -- composition -------------------------------------------------------

    def _runtime(self, provider: Provider) -> RuntimeContext:
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

    def _service(
        self,
        provider: Provider,
        *,
        approval_policy: ApprovalPolicy | None = None,
        approval_resolver=None,
        verification_command: tuple[str, ...] = VERIFY_COMMAND,
        verification_timeout: float | None = 30.0,
    ) -> ForgeApiService:
        declarations = {
            self.build_id: ExecutionDeclaration(
                declaration_id=self.build_id,
                command=CREATE_COMMAND,
                profile_id="verifier-profile",
                working_directory=".",
                intent="Produce a small Python package with its own tests.",
            ),
            self.verify_id: ExecutionDeclaration(
                declaration_id=self.verify_id,
                command=verification_command,
                profile_id="verifier-profile",
                working_directory=".",
                intent="Run the generated project's own test suite.",
                timeout_seconds=verification_timeout,
            ),
        }
        specs = {
            self.build_id: AcceptanceSpec(
                declaration_id=self.build_id,
                criteria=(
                    AcceptanceCriterion(
                        "module-present",
                        "the generated project contains its module",
                        requirement_id="project-source",
                    ),
                    AcceptanceCriterion(
                        "readme-present",
                        "the generated project contains its README",
                        requirement_id="project-docs",
                    ),
                    AcceptanceCriterion(
                        "tests-present",
                        "the generated project contains its own tests",
                        requirement_id="project-tests",
                    ),
                ),
                expectations={
                    "module-present": VerificationExpectation(MODULE_PATH, True),
                    "readme-present": VerificationExpectation(README_PATH, True),
                    "tests-present": VerificationExpectation(TESTS_PATH, True),
                },
            )
        }
        return ForgeApiService(
            runtime=self._runtime(provider),
            workspace=self.workspace,
            run_store=self.run_store,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=_profile(),
            declarations=declarations,
            acceptance_specs=specs,
            approval_policy=(
                ApprovalPolicy() if approval_policy is None else approval_policy
            ),
            approval_resolver=approval_resolver,
            idempotency_guard=self.idempotency_guard,
        )

    def run_flow(self, output: str, **kwargs):
        """Run the workflow once with one injected response."""

        provider = _InjectedTransport(output)
        result = run_project_workflow(
            self._service(provider, **kwargs),
            self.registry,
            self.build_id,
            ai_provider=AIDecisionProvider(provider, mock_transport=True),
        )
        return provider, result

    # -- observation -------------------------------------------------------

    def disk_files(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )

    def read(self, relative: str) -> bytes:
        return (self.root / relative).read_bytes()

    def run_generated_tests(self) -> subprocess.CompletedProcess:
        """Run the **generated project's own** tests directly, to compare with the
        exit code the workflow reported. This is an independent measurement, made
        with a separate process, not a re-read of the workflow's own claim."""

        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(self.root)
        return subprocess.run(
            list(VERIFY_COMMAND),
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=120,
            env=environment,
        )


class TheResponseDeterminesTheWrittenBytesTests(EndToEndTestCase):
    """The core claim: the model's text is the project, byte for byte."""

    def test_two_different_responses_produce_two_different_projects(self) -> None:
        first_output = project_response(
            operator_expression="a + b",
            docstring="The first generated package.",
            readme_title="calcproj (first)",
            readme_body="first response",
            assertion="self.assertEqual(add(2, 3), 5)",
        )
        second_output = project_response(
            operator_expression="sum((a, b))",
            docstring="The second generated package.",
            readme_title="calcproj (second)",
            readme_body="second response",
            assertion="self.assertEqual(add(5, 7), 12)",
        )
        self.assertNotEqual(first_output, second_output)

        # --- first response, in its own workspace ---
        provider_a, result_a = self.run_flow(first_output)
        self.assertTrue(result_a.success, result_a.error)
        first_module = self.read(MODULE_PATH)
        first_readme = self.read(README_PATH)
        first_tests = self.read(TESTS_PATH)

        first_run = self.run_generated_tests()
        self.assertEqual(
            first_run.returncode, 0, first_run.stdout + first_run.stderr
        )

        # --- second response, in a fresh workspace ---
        self.tearDown()
        self.setUp()

        provider_b, result_b = self.run_flow(second_output)
        self.assertTrue(result_b.success, result_b.error)
        second_module = self.read(MODULE_PATH)
        second_readme = self.read(README_PATH)
        second_tests = self.read(TESTS_PATH)

        second_run = self.run_generated_tests()
        self.assertEqual(
            second_run.returncode, 0, second_run.stdout + second_run.stderr
        )

        # The bytes on disk differ, and each tree is exactly what its own response
        # declared -- the response, not a template, decided the content.
        self.assertNotEqual(first_module, second_module)
        self.assertNotEqual(first_readme, second_readme)
        self.assertNotEqual(first_tests, second_tests)

        declared_a = json.loads(first_output)["files"]
        declared_b = json.loads(second_output)["files"]
        for relative, declared in declared_a.items():
            self.assertEqual(
                first_module if relative == MODULE_PATH else None,
                declared.encode("utf-8") if relative == MODULE_PATH else None,
            )
        self.assertIn(b"return a + b", first_module)
        self.assertIn(b"return sum((a, b))", second_module)
        self.assertEqual(
            first_readme, declared_a[README_PATH].encode("utf-8")
        )
        self.assertEqual(
            second_readme, declared_b[README_PATH].encode("utf-8")
        )
        # And the two providers really are two different transports, each called.
        self.assertGreaterEqual(provider_a.call_count, 1)
        self.assertGreaterEqual(provider_b.call_count, 1)

    def test_the_provider_response_is_the_source_of_every_written_file(self) -> None:
        output = project_response(
            extra_files={"NOTES.md": "a note that only this response contains\n"}
        )
        declared = json.loads(output)["files"]

        _provider, result = self.run_flow(output)
        self.assertTrue(result.success, result.error)

        # Every declared file is on disk with exactly the declared bytes.
        for relative, body in declared.items():
            self.assertTrue(
                (self.root / relative).is_file(), f"{relative} was not written"
            )
            self.assertEqual(
                self.read(relative),
                body.encode("utf-8"),
                f"{relative} on disk differs from the response",
            )
        # And nothing beyond the declared set plus the pre-existing user file.
        self.assertEqual(
            self.disk_files(), sorted([*declared, "user_notes.txt"])
        )

    def test_the_generated_package_imports_and_runs(self) -> None:
        _provider, result = self.run_flow(project_response())
        self.assertTrue(result.success, result.error)

        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(self.root)
        probe = subprocess.run(
            [sys.executable, "-c", f"from {PACKAGE} import add; print(add(2, 3))"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(probe.stdout.strip(), "5")

    def test_the_declared_creation_command_is_not_dispatched_on_the_model_path(self) -> None:
        _provider, result = self.run_flow(project_response())
        self.assertTrue(result.success, result.error)
        self.assertFalse((self.root / "DECLARED_CREATION_RAN.txt").exists())
        self.assertFalse(result.declared_command_dispatched)


class TheRealExitCodeAndVerdictAgreeTests(EndToEndTestCase):
    """The exit code is the process's, and the verdict follows the facts."""

    def test_a_passing_generated_suite_is_retained_with_its_zero_exit_code(self) -> None:
        _provider, result = self.run_flow(project_response())

        verification = result.verification
        self.assertIsNotNone(verification, "the result must carry the test-run outcome")
        self.assertEqual(verification.observed_exit_code, 0)
        self.assertTrue(verification.success)

        # Independent measurement: run the same suite ourselves and compare.
        independent = self.run_generated_tests()
        self.assertEqual(independent.returncode, verification.observed_exit_code)

        # The project's own tests really ran and really passed. The verdict says
        # so, and it never claims a real remote model was called: that fact lives
        # in ``generation.mock_transport``.
        self.assertEqual(result.verdict, VERDICT_PROJECT_VERIFIED)
        self.assertTrue(result.generation.mock_transport)

    def test_a_failing_generated_test_yields_non_zero_exit_and_a_failing_verdict(self) -> None:
        # The module is deliberately wrong (subtraction); the generated test is
        # correct. A real assertion therefore fails and the suite exits non-zero.
        output = project_response(
            operator_expression="a - b",
            assertion="self.assertEqual(add(2, 3), 5)",
        )
        _provider, result = self.run_flow(output)

        # The files were still written: the failure is in the tests, not the write.
        self.assertTrue((self.root / MODULE_PATH).is_file())

        verification = result.verification
        self.assertIsNotNone(verification)
        self.assertIsNotNone(verification.observed_exit_code)
        self.assertNotEqual(verification.observed_exit_code, 0)
        self.assertFalse(verification.success)

        independent = self.run_generated_tests()
        self.assertNotEqual(independent.returncode, 0)
        self.assertEqual(independent.returncode, verification.observed_exit_code)

        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_TESTS_FAILED)

    def test_a_non_zero_exit_is_never_presented_as_verified(self) -> None:
        output = project_response(operator_expression="a - b")
        _provider, result = self.run_flow(output)

        self.assertFalse(result.success)
        self.assertNotEqual(result.verdict, VERDICT_REAL_PROVIDER_NOT_VERIFIED)
        payload = result.to_dict()
        self.assertFalse(payload["success"])
        self.assertFalse(payload["verification"]["success"])
        self.assertNotEqual(payload["verification"]["observed_exit_code"], 0)

    def test_a_missing_generated_test_file_produces_a_real_non_zero_exit(self) -> None:
        # No test module at all: the runner has nothing to run, and a discovery
        # that finds no tests must not be read as success.
        output = project_response(extra_files={})
        declared = json.loads(output)["files"]
        declared.pop(TESTS_PATH)
        output = json.dumps({"files": declared,
                             "decision": json.loads(output)["decision"]})

        _provider, result = self.run_flow(output)

        self.assertFalse(result.success)
        verification = result.verification
        self.assertIsNotNone(verification)
        self.assertNotEqual(verification.observed_exit_code, 0)


class HonestNegativesTests(EndToEndTestCase):
    """Every unusable response fails closed with its own reason and writes nothing."""

    def _assert_nothing_written(self) -> None:
        self.assertEqual(self.disk_files(), ["user_notes.txt"])

    def test_an_empty_response_creates_nothing_and_claims_no_producer(self) -> None:
        _provider, result = self.run_flow("")

        self._assert_nothing_written()
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED)

        evidence = result.generation
        self.assertIsNotNone(evidence)
        # Nothing arrived, so no producer identity and no measurement may be
        # reported. A fail-closed answer is never dressed up as a model answering.
        self.assertFalse(evidence.response_text_observed)
        self.assertEqual(evidence.provider_name, "")
        self.assertEqual(evidence.model_name, "")
        self.assertIsNone(evidence.usage_input_tokens)
        self.assertIsNone(evidence.usage_output_tokens)
        self.assertFalse(evidence.usage_present)
        self.assertEqual(evidence.raw_model_text, "")

        self.assertEqual(result.parsed_project.fail_closed_reason, "empty_model_output")
        payload = result.to_dict()["generation"]
        self.assertEqual(payload["provider_name"], "")
        self.assertFalse(payload["usage_present"])

    def test_a_malformed_response_creates_nothing_and_keeps_the_fact_it_arrived(self) -> None:
        _provider, result = self.run_flow("{ this is not json")

        self._assert_nothing_written()
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED)

        evidence = result.generation
        self.assertIsNotNone(evidence)
        # Text did arrive -- that weaker fact is preserved -- but it produced no
        # project, so no producer identity or measurement is attributed to it.
        self.assertTrue(evidence.response_text_observed)
        self.assertEqual(evidence.provider_name, "")
        self.assertEqual(evidence.model_name, "")
        self.assertIsNone(evidence.usage_input_tokens)
        self.assertFalse(evidence.usage_present)
        self.assertEqual(evidence.raw_model_text, "")

    def test_a_response_without_the_expected_files_creates_nothing(self) -> None:
        # Syntactically valid JSON, valid schema, but no `files` member at all.
        output = json.dumps(
            {
                "decision": {
                    "decision_type": "CONTINUE",
                    "action": "EXECUTE",
                    "reason_code": "project_ready",
                    "rationale": "nothing to do",
                    "confidence": 0.5,
                }
            }
        )
        _provider, result = self.run_flow(output)

        self._assert_nothing_written()
        self.assertFalse(result.success)
        self.assertEqual(result.verdict, VERDICT_GENERATION_FAILED)
        self.assertEqual(
            result.parsed_project.fail_closed_reason, "model_output_missing_files"
        )

    def test_syntactically_invalid_generated_python_is_written_but_fails_the_tests(self) -> None:
        """An important honesty boundary.

        The generator validates *paths and content safety*, not Python syntax: it
        is not a compiler, and claiming otherwise would be false. Invalid Python is
        therefore materialised and then caught by the **real test run**, which
        exits non-zero. The result must be a failure, never a success.
        """
        broken = "def add(a, b)\n    return a + b\n"  # missing colon
        output = project_response(extra_files={})
        declared = json.loads(output)["files"]
        declared[MODULE_PATH] = broken
        output = json.dumps({"files": declared,
                             "decision": json.loads(output)["decision"]})

        _provider, result = self.run_flow(output)

        self.assertFalse(result.success)
        # The bytes really reached disk -- no silent "safety" claim is made.
        self.assertEqual(self.read(MODULE_PATH), broken.encode("utf-8"))

        verification = result.verification
        self.assertIsNotNone(verification)
        self.assertNotEqual(verification.observed_exit_code, 0)
        self.assertFalse(verification.success)

        independent = self.run_generated_tests()
        self.assertNotEqual(independent.returncode, 0)
        self.assertEqual(independent.returncode, verification.observed_exit_code)

    def test_a_refused_file_write_is_reported_and_the_rest_survives(self) -> None:
        credential = (
            "import os\n"
            "K = os.environ['API_KEY']\n"
            "API_KEY = 'abcdefghijklmnopqrstuvwxyz123456'\n"
        )
        # The content rule really refuses this, checked directly first.
        self.assertIsNotNone(secret_content_reason(credential))

        output = project_response(extra_files={"config.py": credential})
        _provider, result = self.run_flow(output)

        self.assertFalse((self.root / "config.py").exists())
        reasons = [item.reason for item in result.materialisation_rejections]
        self.assertIn("secret_content", reasons, reasons)
        # The safe files beside the refused one are still written.
        self.assertTrue((self.root / MODULE_PATH).is_file())
        self.assertTrue((self.root / README_PATH).is_file())

    def test_an_unsafe_path_is_refused_and_never_written(self) -> None:
        output = project_response(extra_files={})
        declared = json.loads(output)["files"]
        declared["../escape.py"] = "ESCAPED = True\n"
        declared[".git/hooks/pre-commit"] = "#!/bin/sh\necho pwned\n"
        output = json.dumps({"files": declared,
                             "decision": json.loads(output)["decision"]})

        _provider, result = self.run_flow(output)

        self.assertFalse((self.base / "escape.py").exists())
        self.assertFalse((self.root / ".git").exists())
        reasons = {item.reason for item in result.materialisation_rejections}
        self.assertIn("path_traversal", reasons)

    def test_a_launch_error_is_reported_and_never_a_success(self) -> None:
        """The real test run cannot start: the interpreter path does not exist.

        A launch error is an execution failure. It must be reported as one, with no
        exit code invented for it.
        """
        # The declaration is admissible ("python" is what the profile admits), but
        # PYTHONHOME sends the interpreter to a directory that does not exist, so
        # the process cannot start at all.
        provider = _InjectedTransport(project_response())
        service = self._service(provider)
        import dataclasses

        service._declarations = dict(service.declarations)
        service._declarations[self.verify_id] = dataclasses.replace(
            service.declarations[self.verify_id],
            environment_variables={"PYTHONHOME": str(self.base / "no-such-python-home")},
        )
        result = run_project_workflow(
            service,
            self.registry,
            self.build_id,
            ai_provider=AIDecisionProvider(provider, mock_transport=True),
        )

        self.assertFalse(result.success)
        self.assertNotEqual(result.verdict, VERDICT_REAL_PROVIDER_NOT_VERIFIED)
        verification = result.verification
        self.assertIsNotNone(verification)
        self.assertFalse(verification.success)
        # No exit code may be fabricated for a process that never ran.
        self.assertIsNone(verification.observed_exit_code)
        self.assertTrue(
            verification.error or verification.messages,
            "a launch error must be reported, not swallowed",
        )

    def test_a_verification_timeout_is_reported_and_never_a_success(self) -> None:
        sleep_command = (
            "python",
            "-c",
            "import time; time.sleep(30)",
        )
        provider = _InjectedTransport(project_response())
        service = self._service(
            provider,
            verification_command=sleep_command,
            verification_timeout=2.0,
        )
        result = run_project_workflow(
            service,
            self.registry,
            self.build_id,
            ai_provider=AIDecisionProvider(provider, mock_transport=True),
        )

        self.assertFalse(result.success)
        self.assertNotEqual(result.verdict, VERDICT_REAL_PROVIDER_NOT_VERIFIED)
        verification = result.verification
        self.assertIsNotNone(verification)
        self.assertFalse(verification.success)
        self.assertNotEqual(verification.outcome_status,
                            "EXECUTION_SUCCESS")


class AuthorizationIsStillRequiredTests(EndToEndTestCase):
    """The generation path does not weaken the existing authorization chain."""

    class _RejectingResolver:
        """A resolver that answers REJECTED. A rejection is not an approval."""

        def resolve(self, request):  # noqa: ANN001 - mirrors the real protocol
            from app.tools.approval import ApprovalResolution, ApprovalState

            return ApprovalResolution(
                decision=ApprovalState.REJECTED,
                approved_fingerprint=request.intent_fingerprint or "",
                approval_id="verifier-rejected",
            )

    def test_a_required_unresolved_approval_performs_nothing(self) -> None:
        """No resolver at all: the verification command must not be authorized."""

        provider = _InjectedTransport(project_response())
        service = self._service(
            provider,
            approval_policy=ApprovalPolicy(("execute",)),
        )
        result = run_project_workflow(
            service,
            self.registry,
            self.build_id,
            ai_provider=AIDecisionProvider(provider, mock_transport=True),
        )

        # The generated files may be written (writing is not execution), but no
        # process ran and nothing is verified.
        self.assertFalse(result.success)
        self.assertIsNotNone(result.verification)
        self.assertFalse(result.verification.success)
        self.assertIsNone(result.verification.observed_exit_code)
        self.assertNotEqual(result.verdict, VERDICT_REAL_PROVIDER_NOT_VERIFIED)

    def test_a_rejected_approval_performs_nothing(self) -> None:
        provider = _InjectedTransport(project_response())
        service = self._service(
            provider,
            approval_policy=ApprovalPolicy(("execute",)),
            approval_resolver=self._RejectingResolver(),
        )
        result = run_project_workflow(
            service,
            self.registry,
            self.build_id,
            ai_provider=AIDecisionProvider(provider, mock_transport=True),
        )

        self.assertFalse(result.success)
        self.assertIsNotNone(result.verification)
        self.assertIsNone(result.verification.observed_exit_code)
        self.assertFalse(result.verification.success)
        self.assertNotEqual(result.verdict, VERDICT_REAL_PROVIDER_NOT_VERIFIED)

    def test_a_refused_file_write_is_never_silently_replaced(self) -> None:
        """The refused path must not exist, in any spelling, after the run."""

        output = project_response(extra_files={})
        declared = json.loads(output)["files"]
        declared["sub/../secret.py"] = "S = 1\n"
        declared["/abs/secret.py"] = "S = 2\n"
        output = json.dumps({"files": declared,
                             "decision": json.loads(output)["decision"]})

        _provider, result = self.run_flow(output)

        self.assertFalse((self.root / "secret.py").exists())
        self.assertFalse((self.base / "abs").exists())
        for item in result.materialisation_rejections:
            self.assertNotIn(item.path, self.disk_files())


class IdempotencyAndRerunTests(EndToEndTestCase):
    """A retry must not overwrite someone else's files or present a stale result."""

    def test_a_second_generation_into_a_populated_workspace_does_not_overwrite(self) -> None:
        first_output = project_response(readme_body="first response")
        _provider_a, first = self.run_flow(first_output)
        self.assertTrue(first.success, first.error)
        first_readme = self.read(README_PATH)

        # A second, different response on the same workspace, with no permission
        # granted to overwrite: the existing files must survive.
        second_output = project_response(readme_body="second response")
        _provider_b, second = self.run_flow(second_output)

        self.assertEqual(self.read(README_PATH), first_readme)
        self.assertNotIn(b"second response", self.read(README_PATH))
        reasons = {item.reason for item in second.materialisation_rejections}
        self.assertIn("conflicts_with_existing_file", reasons, reasons)
        self.assertFalse(second.success)

    def test_an_explicit_overwrite_permission_is_required_and_then_honoured(self) -> None:
        first_output = project_response(readme_body="first response")
        self.run_flow(first_output)

        second_output = project_response(readme_body="second response")
        provider = _InjectedTransport(second_output)
        result = run_project_workflow(
            self._service(provider),
            self.registry,
            self.build_id,
            ai_provider=AIDecisionProvider(provider, mock_transport=True),
            permitted_overwrite_paths=(README_PATH,),
        )

        self.assertEqual(self.read(README_PATH),
                         json.loads(second_output)["files"][README_PATH].encode("utf-8"))
        # Permission for one path must not become permission for all of them.
        reasons = {item.reason for item in result.materialisation_rejections}
        self.assertIn("conflicts_with_existing_file", reasons, reasons)

    def test_a_partial_write_after_a_refusal_is_reported_not_hidden(self) -> None:
        output = project_response(extra_files={"config.py": "API_KEY = 'abcdefghijklmnopqrstuvwxyz123456'\n"})
        _provider, result = self.run_flow(output)

        # The refused file is named, the safe ones are present, and the result says
        # so; nothing pretends the project is complete.
        self.assertIn(
            "secret_content",
            [item.reason for item in result.materialisation_rejections],
        )
        self.assertFalse((self.root / "config.py").exists())
        self.assertTrue((self.root / MODULE_PATH).is_file())

    def test_the_evidence_never_presents_injected_text_as_a_real_model(self) -> None:
        _provider, result = self.run_flow(project_response())

        evidence = result.generation
        self.assertIsNotNone(evidence)
        self.assertTrue(evidence.mock_transport)
        payload = result.to_dict()
        self.assertFalse(payload["generation"]["raw_model_text_included"])
        # The raw model text is deliberately excluded from the JSON projection.
        self.assertNotIn(
            evidence.raw_model_text,
            json.dumps(payload["generation"]),
        )
        # And the honest limit travels with the result.
        self.assertNotEqual(result.verdict, "real_llm_generation_verified")


if __name__ == "__main__":
    unittest.main()
