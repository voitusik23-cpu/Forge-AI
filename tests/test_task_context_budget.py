"""Regression tests for the task context budget (D4: 26624 tokens was too small).

Measured defect: a moderately detailed task description was rejected with
``Critical Tier 1 context item 'task:...' cannot fit into available budget (26624 tokens,
max_items=16)``. The whole pool was the model's 32k window minus output and headroom -- a
bound sized for *retrieved evidence*, applied to the operator's own instruction. The task
item now has a named budget of its own
(:data:`app.context.selector.TASK_SPECIFICATION_BUDGET_TOKENS`), still bounded, so an
absurd description is refused with a clear limit message.

The provider is a double: nothing here calls a real API.
"""

import unittest

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.providers.config import ProviderConfig
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.context.assembler import ContextAssembler, ContextBudgetExceededError
from app.context.selector import (
    TASK_SPECIFICATION_BUDGET_TOKENS,
    CriticalContextUnfitError,
)
from app.orchestrator.models import RunState, Task
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor

#: The measured shape: a detailed Russian brief of a few kilobytes that names token
#: fields (the same text the security filter used to refuse).
DETAILED_DESCRIPTION = " ".join(
    [
        "Нужно доработать сервис учёта расхода контекста:",
        "посчитать tokens_in и tokens_out для каждого запроса,",
        "лимит max_tokens держать в конфигурации, token_usage писать в отчёт,",
        "не менять формат ответа API, добавить регрессионный тест на граничные случаи,",
        "проверить, что старое поведение сохраняется, и объяснить решение.",
    ]
) * 8

#: A long brief that no longer fits the old 26 624-token pool but is far inside the new
#: Tier 1 budget: 120 000 characters is ~40 000 tokens at three characters per token.
LONG_DESCRIPTION = ("детали " * 20_000)[:120_000]

#: Absurd: above the Tier 1 character bound, so it must be refused, not trimmed.
ABSURD_DESCRIPTION = "x" * 500_001


class _RecordingProvider(Provider):
    """Answer, recording the exact prompt a provider was handed."""

    def __init__(self) -> None:
        super().__init__(
            ProviderConfig(
                provider_name="mock", model_name="mock-small", enabled=True
            )
        )
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output="answer from the fake provider",
        )


def _executor_with(provider: _RecordingProvider) -> RunExecutor:
    agents = AgentRegistry()
    provider_registry = ProviderRegistry()
    capabilities = ProviderCapabilitiesRegistry()
    agents.register(ProviderAgent(provider))
    provider_registry.register(provider)
    capabilities.register_provider(provider)
    orchestrator = Orchestrator(
        agents,
        default_provider="mock",
        provider_registry=provider_registry,
        capabilities_registry=capabilities,
    )
    return RunExecutor(orchestrator)


class TaskContextBudgetTests(unittest.TestCase):
    """The operator's own instruction is budgeted separately from retrieved content."""

    def test_01_the_named_budget_is_at_least_120k_tokens(self) -> None:
        self.assertGreaterEqual(TASK_SPECIFICATION_BUDGET_TOKENS, 120_000)
        # And it is a real budget: the old pool was 26 624 tokens (32k - 4k - 2k).
        self.assertGreater(TASK_SPECIFICATION_BUDGET_TOKENS, 26_624)

    def test_02_the_previously_failing_description_reaches_a_provider(self) -> None:
        provider = _RecordingProvider()
        executor = _executor_with(provider)

        run = executor.execute(
            Task(id="detailed-brief", description=DETAILED_DESCRIPTION),
            provider_name="mock",
            run_id="run-detailed-brief",
        )

        self.assertEqual(run.state, RunState.COMPLETED, run.error)
        self.assertEqual([request.prompt for request in provider.requests], [DETAILED_DESCRIPTION])
        self.assertIsNotNone(run.result)
        self.assertEqual(run.result.output, "answer from the fake provider")
        # The old failure said the task could not fit; that message must be gone.
        self.assertNotIn("cannot fit into available budget", str(run.error or ""))

    def test_03_a_long_brief_is_accepted(self) -> None:
        # The old pool could not admit this item: it is above 26 624 tokens, which is
        # exactly what the measured failure reported.
        self.assertGreater(len(LONG_DESCRIPTION), 26_624 * 3)

        context = ContextAssembler().assemble(
            Task(id="long-brief", description=LONG_DESCRIPTION), "run-long-brief"
        )

        self.assertEqual(len(context.items), 1)
        self.assertEqual(context.items[0].value, LONG_DESCRIPTION)

    def test_03b_the_same_brief_is_refused_by_the_old_window(self) -> None:
        """The regression property: without the Tier 1 budget this item is evicted."""

        from app.context.models import ContextItem, ContextSourceType
        from app.context.selector import ContextBudgetPolicy, ContextSelector

        old_policy = ContextBudgetPolicy(
            total_context_window=32_768,
            max_output_tokens=4_096,
            safety_headroom_tokens=2_048,
        )
        item = ContextItem(
            item_id="task:long-brief",
            item_type="user_task",
            source_type=ContextSourceType.USER_TASK,
            value=LONG_DESCRIPTION,
        )
        with self.assertRaises(CriticalContextUnfitError):
            ContextSelector(policy=old_policy).select([item], budget_policy=old_policy)

    def test_04_an_absurd_description_is_rejected_with_a_clear_limit(self) -> None:
        with self.assertRaises(ContextBudgetExceededError) as raised:
            ContextAssembler().assemble(
                Task(id="absurd", description=ABSURD_DESCRIPTION), "run-absurd"
            )

        message = str(raised.exception)
        self.assertIn("exceeds configured budget", message)
        self.assertIn("characters", message)
        self.assertIn(str(len(ABSURD_DESCRIPTION)), message)
        # It is a limit message, not a silent truncation and not the old Tier 1 wording.
        self.assertNotIn("cannot fit into available budget", message)

    def test_05_a_long_brief_still_fails_closed_on_secret_material(self) -> None:
        """Raising the budget must not disable the security filter."""

        leaked = LONG_DESCRIPTION[:1000] + " key: sk-live-" + "a1b2c3d4" * 5
        with self.assertRaises(CriticalContextUnfitError) as raised:
            ContextAssembler().assemble(
                Task(id="long-leak", description=leaked), "run-long-leak"
            )

        self.assertIn("security_forbidden_content:api_key", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
