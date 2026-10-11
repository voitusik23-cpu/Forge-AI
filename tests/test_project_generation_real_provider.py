"""OPT-IN real-provider integration test for the project-generation path.

NEVER EXECUTED. READ THIS DOCSTRING BEFORE RUNNING IT
=====================================================

**This test was never run while it was written.** It is skipped by default, and it
was left skipped. Its purpose is to describe, precisely and runnably, the one
experiment that would upgrade the verdict from
``GENERATION PATH IMPLEMENTED, REAL PROVIDER NOT VERIFIED`` to
``REAL LLM PROJECT GENERATION VERIFIED``.

Why it is skipped, and why that is not timidity
-----------------------------------------------

* ``AGENTS.md`` §1 forbids connecting external AI providers or using credentials
  unless the task explicitly authorizes it, and the mission that produced this
  file explicitly forbade real provider calls.
* No provider credential exists in the environment this was written in. Every one
  of ``OPENROUTER_API_KEY``, ``DEEPSEEK_API_KEY``, ``OPENAI_API_KEY``,
  ``ANTHROPIC_API_KEY``, ``GEMINI_API_KEY``, ``GROQ_API_KEY`` is absent.
* A test that silently substituted a mock here would be a false claim of the
  strongest kind: it would report "a real model wrote this project".

How to run it, deliberately and once
------------------------------------

Set BOTH of these in the process environment, then run this file alone::

    $env:FORGE_ALLOW_REAL_PROVIDER_GENERATION = "1"
    $env:OPENROUTER_API_KEY = "<owner-supplied key>"
    python -X utf8 -m unittest discover -s tests -p "test_project_generation_real_provider.py"

Nothing else is needed: the test composes the real adapter from the existing
factory, so it exercises the production path rather than a bespoke one.

What a passing run would and would not prove
--------------------------------------------

It would prove that a real remote model was asked through Forge's own dispatch
path and that its answer became files that Forge's own declared verification
command then tested. It would **not** prove the code is good: one sample from one
model at one temperature is not evidence about generated-code quality. It would
also spend the owner's money, which is why it is opt-in.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from app.agent_runtime import idempotency_integration as integration
from app.agent_runtime.acceptance_spec import AcceptanceSpec
from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider
from app.agents.providers.config import ProviderConfig
from app.agents.providers.factory import ProviderFactory
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
from app.runtime.run_scope import RunScope
from app.runtime.run_store import RunStore
from app.tools.acceptance import AcceptanceCriterion
from app.tools.registry import build_default_tool_registry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.workflows.project_workflow import (
    VERDICT_PROJECT_VERIFIED,
    VERDICT_REAL_PROVIDER_NOT_VERIFIED,
    ProjectWorkflowRegistry,
    run_project_workflow,
)

#: The explicit opt-in flag. It is deliberately a separate variable from the key:
#: possessing a key must not be enough to make a paid call.
OPT_IN_FLAG = "FORGE_ALLOW_REAL_PROVIDER_GENERATION"

#: Provider name, model id, and the environment variable that holds its key.
REAL_PROVIDER_NAME = os.environ.get(
    "FORGE_REAL_GENERATION_PROVIDER", "openrouter"
)
REAL_MODEL_ID = os.environ.get(
    "FORGE_REAL_GENERATION_MODEL", "cohere/north-mini-code:free"
)
_KEY_ENV_VAR_BY_PROVIDER = {
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}

BUILD_ID = "real.generated.build"
VERIFY_ID = "real.generated.verify"
CREATE_COMMAND = (
    "python",
    "-c",
    "open('DECLARED_CREATION_COMMAND_RAN.txt','w',encoding='utf-8').write('ran')",
)
VERIFY_COMMAND = (
    "python", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "test_*.py",
)
CRITERIA = (
    AcceptanceCriterion(
        "project-module-present",
        "the generated project contains an importable Python module",
        requirement_id="project-source",
    ),
    AcceptanceCriterion(
        "project-tests-present",
        "the generated project contains its own test module",
        requirement_id="project-tests",
    ),
)
EXPECTATIONS = {
    "project-module-present": VerificationExpectation("mod.py", True),
    "project-tests-present": VerificationExpectation("tests/test_mod.py", True),
}


def _skip_reason() -> str | None:
    """Return why a real call must not be made, or ``None`` when it may be."""
    if os.environ.get(OPT_IN_FLAG, "").strip() != "1":
        return (
            f"opt-in flag {OPT_IN_FLAG} is not set to '1'; "
            "this test makes a real, paid provider call and is never run by default"
        )
    key_var = _KEY_ENV_VAR_BY_PROVIDER.get(REAL_PROVIDER_NAME)
    if key_var is None:
        return f"no key variable is known for provider {REAL_PROVIDER_NAME!r}"
    if not os.environ.get(key_var, "").strip():
        return f"{key_var} is not set, so no real provider call is possible"
    return None


@unittest.skipUnless(
    _skip_reason() is None,
    _skip_reason() or "skipped",
)
class RealProviderGenerationTests(unittest.TestCase):
    """One real model call, one real project, one real test run."""

    def test_a_real_model_answer_becomes_a_project_that_really_runs(self) -> None:
        RunScope.release_all()
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as ledger_tmp:
            root = Path(tmp) / "ws"
            root.mkdir()
            workspace = Workspace(root)

            provider: Provider = ProviderFactory().create(
                REAL_PROVIDER_NAME,
                ProviderConfig(
                    provider_name=REAL_PROVIDER_NAME,
                    model_name=REAL_MODEL_ID,
                    enabled=True,
                    api_key_env_var=_KEY_ENV_VAR_BY_PROVIDER[REAL_PROVIDER_NAME],
                ),
            )
            provider_registry = ProviderRegistry()
            provider_registry.register(provider)
            agent_registry = AgentRegistry()
            agent_registry.register(ProviderAgent(provider))
            runtime = RuntimeContext(
                settings=None,
                provider_accounts={},
                provider_registry=provider_registry,
                provider_capabilities=None,
                agent_registry=agent_registry,
                orchestrator=Orchestrator(
                    agent_registry,
                    default_provider=REAL_PROVIDER_NAME,
                    provider_registry=provider_registry,
                ),
                run_executor=RunExecutor(
                    Orchestrator(
                        agent_registry,
                        default_provider=REAL_PROVIDER_NAME,
                        provider_registry=provider_registry,
                    )
                ),
                harness=create_agent_harness(),
            )

            service = ForgeApiService(
                runtime=runtime,
                workspace=workspace,
                tool_registry=build_default_tool_registry(root),
                execution_profile=ProjectExecutionProfile(
                    profile_id="api-default",
                    allowed_commands=("python",),
                    capabilities=frozenset({
                        ExecutionCapability.INTERPRET_TEXT,
                        ExecutionCapability.INTERPRET_MODULE,
                    }),
                    network_access=False,
                    working_directory=".",
                ),
                declarations={
                    BUILD_ID: ExecutionDeclaration(
                        declaration_id=BUILD_ID,
                        command=CREATE_COMMAND,
                        profile_id="api-default",
                        working_directory=".",
                        intent=(
                            "Create a Python module named mod.py exposing add(a, b), "
                            "and a tests/test_mod.py that passes."
                        ),
                    ),
                    VERIFY_ID: ExecutionDeclaration(
                        declaration_id=VERIFY_ID,
                        command=VERIFY_COMMAND,
                        profile_id="api-default",
                        working_directory=".",
                        intent="Run the generated project's own test suite.",
                        timeout_seconds=60.0,
                    ),
                },
                acceptance_specs={
                    BUILD_ID: AcceptanceSpec(
                        declaration_id=BUILD_ID,
                        criteria=CRITERIA,
                        expectations=EXPECTATIONS,
                    )
                },
                run_store=RunStore(root=store_tmp),
                idempotency_guard=integration.RunIdempotencyGuard.create(
                    ledger_root=ledger_tmp
                ),
            )

            # mock_transport=False is the honest value here: this test composes a
            # real adapter. If a future edit swaps in a fake, this field becomes a
            # lie and the assertion below is the tripwire.
            result = run_project_workflow(
                service,
                ProjectWorkflowRegistry({BUILD_ID: VERIFY_ID}),
                BUILD_ID,
                ai_provider=AIDecisionProvider(provider, mock_transport=False),
            )

            self.assertFalse(result.generation.mock_transport)
            self.assertEqual(result.generation.provider_name, REAL_PROVIDER_NAME)
            self.assertTrue(result.generation.raw_model_text.strip())
            self.assertFalse(
                result.declared_command_dispatched,
                "the model's files are the project; the declared creation command "
                "must not have overwritten them",
            )
            self.assertTrue(result.materialisation.created_files)
            self.assertIsNotNone(result.verification)
            self.assertEqual(result.verification.observed_exit_code, 0,
                             f"the generated project's own tests must pass: "
                             f"{result.verification.stdout}\n{result.verification.stderr}")
            # The verdict says the project's own tests really passed. It stays
            # transport-neutral on purpose: which transport produced the text is
            # carried by ``generation.mock_transport`` / ``.provider_name``, so
            # the verdict cannot be misread as either "a remote model was
            # verified" or "the project was not verified".
            self.assertEqual(result.verdict, VERDICT_PROJECT_VERIFIED)
            print(json.dumps(result.to_dict(), indent=2)[:4000])
        RunScope.release_all()


if __name__ == "__main__":
    unittest.main()
