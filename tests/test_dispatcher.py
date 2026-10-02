"""Offline tests for category based task dispatch."""

import unittest

from app.agents.base import AgentExecutionError
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.orchestrator.dispatcher import DispatchPolicy, Dispatcher
from app.orchestrator.models import Task, TaskCategory, TaskResult
from app.orchestrator.orchestrator import Orchestrator


class StubAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.provider_name = name
        self.calls = 0

    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        return TaskResult(task_id=task.id, success=True, output=f"handled by {self.name}")


class UnavailableAgent(StubAgent):
    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        raise AgentExecutionError(f"{self.name} is not configured")


class DispatchPolicyTests(unittest.TestCase):
    def test_categories_choose_ordered_provider_preferences(self) -> None:
        policy = DispatchPolicy()
        cases = {
            TaskCategory.CODING: ("openai", "anthropic"),
            TaskCategory.REASONING: ("anthropic", "openai"),
            TaskCategory.LARGE_CONTEXT: ("google",),
            TaskCategory.CHEAP_FREE: ("openrouter",),
            TaskCategory.FAST_CHEAP: ("deepseek",),
            TaskCategory.OTHER: ("xai",),
        }
        for category, expected in cases.items():
            with self.subTest(category=category):
                decision = policy.decide(
                    Task(id="policy", description="task", category=category), "xai"
                )
                self.assertEqual(decision.candidates, expected)
                self.assertEqual(decision.selected_provider, expected[0])


class DispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AgentRegistry()
        self.agents = {}
        for name in ("openai", "anthropic", "google", "openrouter", "deepseek", "xai"):
            agent = StubAgent(name)
            self.agents[name] = agent
            self.registry.register(agent)
        self.orchestrator = Orchestrator(self.registry, default_provider="xai")

    def make_cost_dispatcher(
        self, names, *, allow_paid=False, fallback_chain=(), unavailable=()
    ) -> tuple[Dispatcher, dict[str, StubAgent]]:
        agents = AgentRegistry()
        providers = ProviderRegistry()
        capabilities = ProviderCapabilitiesRegistry()
        factory = ProviderFactory()
        agent_map = {}
        for name in names:
            agent = UnavailableAgent(name) if name in unavailable else StubAgent(name)
            agent_map[name] = agent
            agents.register(agent)
            provider = factory.create(name)
            providers.register(provider)
            capabilities.register_provider(provider)
        return (
            Dispatcher(
                agents,
                provider_registry=providers,
                capabilities_registry=capabilities,
                fallback_chain=fallback_chain,
                allow_paid_providers=allow_paid,
            ),
            agent_map,
        )

    def test_category_dispatches_to_first_policy_choice_only(self) -> None:
        task = Task(id="coding", description="write a function", category=TaskCategory.CODING)

        result = self.orchestrator.dispatch(task)

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(self.agents["openai"].calls, 1)
        self.assertEqual(self.agents["anthropic"].calls, 0)

    def test_other_category_uses_configured_default(self) -> None:
        result = self.orchestrator.dispatch(
            Task(id="default", description="ordinary task")
        )

        self.assertEqual(result.provider, "xai")

    def test_provider_can_be_manually_selected(self) -> None:
        result = self.orchestrator.dispatch(
            Task(id="manual", description="task", category=TaskCategory.CODING),
            provider_name="anthropic",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(self.agents["openai"].calls, 0)

    def test_unavailable_provider_returns_structured_failure_without_fallback(self) -> None:
        registry = AgentRegistry()
        anthropic = StubAgent("anthropic")
        registry.register(anthropic)
        dispatcher = Dispatcher(registry)

        result = dispatcher.dispatch(
            Task(id="unavailable", description="task", category=TaskCategory.CODING)
        )

        self.assertFalse(result.success)
        self.assertIn("openai", result.error)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(anthropic.calls, 0)

    def test_legacy_explicit_agent_dispatch_remains_available(self) -> None:
        result = self.orchestrator.dispatch(
            Task(id="legacy", description="task"), agent_name="anthropic"
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "anthropic")

    def test_successful_primary_does_not_use_fallback(self) -> None:
        dispatcher = Dispatcher(
            self.registry,
            default_provider="xai",
            fallback_chain=("anthropic",),
        )

        result = dispatcher.dispatch(
            Task(id="primary-ok", description="task", category=TaskCategory.CODING)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(self.agents["anthropic"].calls, 0)

    def test_unavailable_primary_uses_next_configured_provider(self) -> None:
        registry = AgentRegistry()
        primary = UnavailableAgent("openai")
        secondary = StubAgent("anthropic")
        registry.register(primary)
        registry.register(secondary)
        dispatcher = Dispatcher(registry, fallback_chain=("anthropic",))

        result = dispatcher.dispatch(
            Task(id="primary-fails", description="task", category=TaskCategory.CODING)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(secondary.calls, 1)

    def test_exhausted_chain_returns_combined_failure_reason(self) -> None:
        registry = AgentRegistry()
        registry.register(UnavailableAgent("openai"))
        dispatcher = Dispatcher(registry, fallback_chain=("missing", "also-missing"))

        result = dispatcher.dispatch(
            Task(id="chain-fails", description="task", category=TaskCategory.CODING)
        )

        self.assertFalse(result.success)
        self.assertIn("Provider fallback chain exhausted", result.error)
        self.assertIn("openai is not configured", result.error)
        self.assertIn("missing", result.error)
        self.assertEqual(result.provider, "openai")

    def test_explicit_provider_failure_does_not_switch_provider(self) -> None:
        registry = AgentRegistry()
        selected = UnavailableAgent("openai")
        fallback = StubAgent("anthropic")
        registry.register(selected)
        registry.register(fallback)
        dispatcher = Dispatcher(registry, fallback_chain=("anthropic",))

        result = dispatcher.dispatch(
            Task(id="explicit", description="task"), provider_name="openai"
        )

        self.assertFalse(result.success)
        self.assertIn("openai is not configured", result.error)
        self.assertEqual(fallback.calls, 0)

    def test_provider_and_capability_registries_guard_dispatch(self) -> None:
        agents = AgentRegistry()
        openai_agent = StubAgent("openai")
        agents.register(openai_agent)
        providers = ProviderRegistry()
        providers.register(ProviderFactory().create("openai"))
        capabilities = ProviderCapabilitiesRegistry()
        dispatcher = Dispatcher(
            agents,
            provider_registry=providers,
            capabilities_registry=capabilities,
        )

        result = dispatcher.dispatch(
            Task(id="missing-capability", description="task", category=TaskCategory.CODING)
        )

        self.assertFalse(result.success)
        self.assertIn("capabilities are not registered", result.error)
        self.assertEqual(openai_agent.calls, 0)

    def test_ordinary_task_prefers_available_free_provider(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("openai", "deepseek", "mock")
        )

        result = dispatcher.dispatch(Task(id="free", description="ordinary task"))

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "mock")
        self.assertEqual(agents["openai"].calls, 0)
        self.assertEqual(agents["deepseek"].calls, 0)

    def test_cheap_provider_precedes_paid_when_no_free_is_registered(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(("openai", "deepseek"))

        result = dispatcher.dispatch(Task(id="cheap", description="ordinary task"))

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "deepseek")
        self.assertEqual(agents["openai"].calls, 0)

    def test_unavailable_free_provider_advances_to_cheap_tier(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("mock", "deepseek", "openai"), unavailable=("mock",)
        )

        result = dispatcher.dispatch(Task(id="free-fails", description="ordinary task"))

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "deepseek")
        self.assertEqual(agents["mock"].calls, 1)
        self.assertEqual(agents["openai"].calls, 0)

    def test_paid_provider_is_selected_only_when_policy_allows(self) -> None:
        forbidden, forbidden_agents = self.make_cost_dispatcher(("openai",))
        forbidden_result = forbidden.dispatch(
            Task(id="paid-forbidden", description="ordinary task")
        )
        self.assertFalse(forbidden_result.success)
        self.assertEqual(forbidden_agents["openai"].calls, 0)

        permitted, permitted_agents = self.make_cost_dispatcher(
            ("openai",), allow_paid=True
        )
        permitted_result = permitted.dispatch(
            Task(id="paid-permitted", description="ordinary task")
        )
        self.assertTrue(permitted_result.success)
        self.assertEqual(permitted_result.provider, "openai")
        self.assertEqual(permitted_agents["openai"].calls, 1)

    def test_explicit_paid_provider_overrides_cost_policy(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("mock", "openai"), allow_paid=False, fallback_chain=("mock",)
        )

        result = dispatcher.dispatch(
            Task(id="explicit-paid", description="ordinary task"),
            provider_name="openai",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(agents["mock"].calls, 0)


if __name__ == "__main__":
    unittest.main()
