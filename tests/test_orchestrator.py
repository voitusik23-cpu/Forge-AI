"""Tests for the provider-neutral orchestration foundation."""

import unittest

from app.agents.mock_agent import MockAgent
from app.agents.registry import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
    AgentRegistry,
)
from app.orchestrator.models import Task, TaskCategory, TaskPriority, TaskResult, Usage
from app.orchestrator.orchestrator import Orchestrator


class TaskModelTests(unittest.TestCase):
    def test_task_creation_uses_default_context_and_priority(self) -> None:
        task = Task(id="task-1", description="Review a change")

        self.assertEqual(task.id, "task-1")
        self.assertEqual(task.context, {})
        self.assertEqual(task.priority, TaskPriority.NORMAL)
        self.assertEqual(task.category, TaskCategory.OTHER)
        self.assertEqual(task.parameters, {})

    def test_task_priority_values(self) -> None:
        self.assertEqual(
            [priority.name for priority in TaskPriority],
            ["LOW", "NORMAL", "HIGH", "CRITICAL"],
        )


class AgentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AgentRegistry()
        self.agent = MockAgent()

    def test_register_and_get_agent(self) -> None:
        self.registry.register(self.agent)

        self.assertIs(self.registry.get("mock"), self.agent)
        self.assertEqual(self.registry.list_agents(), ["mock"])

    def test_duplicate_registration_raises_clear_error(self) -> None:
        self.registry.register(self.agent)

        with self.assertRaisesRegex(AgentAlreadyRegisteredError, "already registered"):
            self.registry.register(MockAgent())

    def test_unknown_agent_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(AgentNotFoundError, "not registered"):
            self.registry.get("missing")


class MockAgentTests(unittest.TestCase):
    def test_mock_agent_returns_predictable_success(self) -> None:
        task = Task(id="task-2", description="Summarize a module")

        result = MockAgent().run(task)

        self.assertIsInstance(result, TaskResult)
        self.assertEqual(result.task_id, task.id)
        self.assertTrue(result.success)
        self.assertIn("MockAgent", result.output)
        self.assertIsInstance(result.usage, Usage)


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AgentRegistry()
        self.agent = MockAgent()
        self.registry.register(self.agent)
        self.orchestrator = Orchestrator(self.registry)

    def test_dispatch_returns_successful_task_result(self) -> None:
        task = Task(id="task-3", description="Check startup")

        result = self.orchestrator.dispatch(task, agent_name="mock")

        self.assertEqual(result.task_id, task.id)
        self.assertTrue(result.success)
        self.assertIn("MockAgent", result.output)

    def test_dispatch_unknown_agent_raises_clear_error(self) -> None:
        task = Task(id="task-4", description="Check startup")

        with self.assertRaisesRegex(AgentNotFoundError, "missing.*not registered"):
            self.orchestrator.dispatch(task, agent_name="missing")


if __name__ == "__main__":
    unittest.main()
