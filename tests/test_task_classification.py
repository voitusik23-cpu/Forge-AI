"""Offline tests for deterministic task classification and specialized routing."""

import unittest

from app.agents.base import AgentExecutionError
from app.agents.providers.capabilities import (
    CostTier,
    ProviderCapabilities,
)
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.orchestrator.classification import classify_task
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.models import Task, TaskCategory, TaskResult


class RoutingAgent:
    def __init__(self, name, *, fails=False):
        self.name = name
        self.provider_name = name
        self.fails = fails
        self.calls = 0

    def run(self, task):
        self.calls += 1
        if self.fails:
            raise AgentExecutionError("temporary provider failure")
        return TaskResult(task_id=task.id, success=True, output=f"via {self.name}")


class StaticCapabilities:
    def __init__(self):
        self.items = {}

    def get(self, name):
        return self.items[name]

    def list_capabilities(self):
        return list(self.items.values())


class ClassificationTests(unittest.TestCase):
    def test_explicit_category_is_preserved(self):
        task = Task(
            id="explicit-other",
            description="Implement a function",
            category=TaskCategory.OTHER,
        )
        self.assertEqual(classify_task(task), TaskCategory.OTHER)

    def test_code_classification(self):
        self.assertEqual(
            classify_task(Task(id="code", description="Write Python code")),
            TaskCategory.CODE,
        )

    def test_review_classification(self):
        self.assertEqual(
            classify_task(Task(id="review", description="Find bugs in this module")),
            TaskCategory.REVIEW,
        )

    def test_analysis_classification(self):
        self.assertEqual(
            classify_task(Task(id="analysis", description="Compare these approaches")),
            TaskCategory.ANALYSIS,
        )

    def test_other_is_the_conservative_fallback(self):
        self.assertEqual(
            classify_task(Task(id="other", description="What time is it?")),
            TaskCategory.OTHER,
        )

    def test_classifies_from_task_metadata(self):
        self.assertEqual(
            classify_task(
                Task(id="metadata", description="Handle this", parameters={"intent": "review"})
            ),
            TaskCategory.REVIEW,
        )


class SpecializedRoutingTests(unittest.TestCase):
    def make_dispatcher(self, *, fallback=(), failed=()):
        factory = ProviderFactory()
        providers = ProviderRegistry()
        agents = AgentRegistry()
        capabilities = StaticCapabilities()
        agent_map = {}
        declarations = {
            "openrouter": (CostTier.FREE, ("code", "review")),
            "deepseek": (CostTier.CHEAP, ("analysis",)),
        }
        for name, (tier, categories) in declarations.items():
            provider = factory.create(name)
            providers.register(provider)
            capabilities.items[name] = ProviderCapabilities(
                provider_name=name,
                api_key_env=None,
                supports_streaming=False,
                supports_tools=False,
                cost_tier=tier,
                enabled_by_config=True,
                task_categories=categories,
            )
            agent = RoutingAgent(name, fails=name in failed)
            agents.register(agent)
            agent_map[name] = agent
        return (
            Dispatcher(
                agents,
                provider_registry=providers,
                capabilities_registry=capabilities,
                fallback_chain=fallback,
            ),
            agent_map,
        )

    def test_category_changes_automatic_provider_selection(self):
        dispatcher, _ = self.make_dispatcher()

        code = dispatcher.dispatch(Task(id="code", description="Implement a function"))
        analysis = dispatcher.dispatch(Task(id="analysis", description="Analyze this issue"))

        self.assertEqual(code.provider, "openrouter")
        self.assertEqual(analysis.provider, "deepseek")

    def test_explicit_provider_override_still_wins(self):
        dispatcher, agents = self.make_dispatcher(fallback=("deepseek",))

        result = dispatcher.dispatch(
            Task(id="explicit", description="Implement a function"),
            provider_name="deepseek",
        )

        self.assertEqual(result.provider, "deepseek")
        self.assertEqual(agents["openrouter"].calls, 0)

    def test_fallback_chain_remains_available(self):
        dispatcher, agents = self.make_dispatcher(
            fallback=("deepseek",), failed=("openrouter",)
        )

        result = dispatcher.dispatch(Task(id="fallback", description="Implement code"))

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "deepseek")
        self.assertEqual(agents["openrouter"].calls, 1)
        self.assertEqual(agents["deepseek"].calls, 1)


if __name__ == "__main__":
    unittest.main()
