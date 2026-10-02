"""Offline tests for category based task dispatch."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
