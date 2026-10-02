"""End-to-end tests for the offline execution pipeline."""

import unittest

from app.agents.base import AgentExecutionError
from app.agents.provider_agent import ProviderAgent
from app.agents.providers.openai import OpenAIProvider
from app.agents.registry import AgentNotFoundError, AgentRegistry
from app.config.settings import RuntimeSettings
from app.orchestrator.executor import InvalidTaskError
from app.orchestrator.models import Task, TaskResult, Usage
from app.orchestrator.orchestrator import Orchestrator
from app.runtime.bootstrap import create_runtime


class ExecutionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = create_runtime(
            RuntimeSettings(default_provider="mock", default_model="offline-test")
        )

    def test_mock_provider_executes_task_end_to_end(self) -> None:
        task = Task(id="e2e-1", description="test execution")

        result = self.runtime.orchestrator.dispatch(task, agent_name="mock")

        self.assertIsInstance(result, TaskResult)
        self.assertTrue(result.success)
        self.assertEqual(result.task_id, task.id)
        self.assertEqual(result.output, "MockProvider response: test execution")
        self.assertEqual(result.agent, "mock")
        self.assertEqual(result.provider, "mock")
        self.assertEqual(result.usage, Usage(0, 0, 0.0))

    def test_unknown_agent_is_not_hidden(self) -> None:
        task = Task(id="missing-agent", description="test execution")

        with self.assertRaisesRegex(AgentNotFoundError, "not registered"):
            self.runtime.orchestrator.dispatch(task, agent_name="missing")

    def test_invalid_task_is_rejected_before_dispatch(self) -> None:
        task = Task(id="  ", description="test execution")

        with self.assertRaisesRegex(InvalidTaskError, "task id"):
            self.runtime.orchestrator.dispatch(task, agent_name="mock")

    def test_invalid_task_object_is_rejected(self) -> None:
        with self.assertRaisesRegex(InvalidTaskError, "Task instance"):
            self.runtime.orchestrator.dispatch(None, agent_name="mock")

    def test_provider_error_returns_structured_failure(self) -> None:
        registry = AgentRegistry()
        registry.register(ProviderAgent(OpenAIProvider()))
        orchestrator = Orchestrator(registry)

        result = orchestrator.dispatch(
            Task(id="provider-failure", description="offline only"),
            agent_name="openai",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.agent, "openai")
        self.assertIn("Provider integration not configured", result.error)

    def test_agent_domain_error_returns_structured_failure(self) -> None:
        class FailingAgent:
            name = "failing"

            def run(self, task: Task) -> TaskResult:
                raise AgentExecutionError("controlled agent failure")

        registry = AgentRegistry()
        registry.register(FailingAgent())
        result = Orchestrator(registry).dispatch(
            Task(id="agent-failure", description="test error"),
            agent_name="failing",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.agent, "failing")
        self.assertEqual(result.error, "controlled agent failure")

    def test_empty_success_result_becomes_failure(self) -> None:
        class EmptyAgent:
            name = "empty"

            def run(self, task: Task) -> TaskResult:
                return TaskResult(task_id=task.id, success=True, output="  ")

        registry = AgentRegistry()
        registry.register(EmptyAgent())
        result = Orchestrator(registry).dispatch(
            Task(id="empty-output", description="return nothing"),
            agent_name="empty",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Agent returned an empty result")


if __name__ == "__main__":
    unittest.main()
