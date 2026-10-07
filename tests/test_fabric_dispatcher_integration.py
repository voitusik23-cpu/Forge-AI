"r""Tests for Capability and Resource Fabric integration with Dispatcher and Execution (Stage 2)."""
import unittest
from app.agents.base import AgentExecutionError
from app.agents.providers.model_registry import create_default_registry
from app.agents.providers.models import CostTier, ModelCapability
from app.agents.registry import AgentRegistry
from app.fabric import (
    CapabilityDomain,
    CapabilityFabric,
    CapabilityRequirement,
)
from app.orchestrator.dispatcher import DispatchPolicy, Dispatcher
from app.orchestrator.models import Task, TaskCategory, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.usage import Usage


class StubAgent:
    def __init__(self, name: str, model_name: str = "default-model") -> None:
        self.name = name
        self.provider_name = name
        self.model_name = model_name
        self.calls = 0

    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        usage = Usage(input_tokens=100, output_tokens=50, estimated_cost=0.0005)
        return TaskResult(
            task_id=task.id,
            success=True,
            output=f"handled by {self.name}",
            provider=self.name,
            agent=self.name,
            model_name=self.model_name,
            usage=usage,
        )


class FailingAgent(StubAgent):
    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        raise AgentExecutionError(f"{self.name} failed due to rate limit")


class FabricDispatcherIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent_registry = AgentRegistry()
        self.model_registry = create_default_registry()
        self.fabric = CapabilityFabric(model_registry=self.model_registry)

        self.agents = {}
        for name in ("openai", "anthropic", "google", "deepseek", "mock"):
            agent = StubAgent(name, model_name=f"{name}-chat-v1")
            self.agents[name] = agent
            self.agent_registry.register(agent)

        self.dispatcher = Dispatcher(
            agent_registry=self.agent_registry,
            default_provider="mock",
            fallback_chain=("deepseek", "anthropic", "openai"),
            model_registry=self.model_registry,
            fabric=self.fabric,
            allow_paid_providers=True,
        )

    def test_01_dispatcher_routes_using_typed_capabilities_from_task(self) -> None:
        task = Task(
            id="task-cap-1",
            description="Complex architectural reasoning task",
            category=TaskCategory.ANALYSIS,
            parameters={
                "required_capabilities": [
                    CapabilityRequirement.model(ModelCapability.REASONING),
                ],
            },
        )
        result = self.dispatcher.dispatch(task)
        self.assertTrue(result.success)
        self.assertIn(result.provider, ("deepseek", "anthropic", "openai", "google"))


    def test_02_dispatcher_routes_with_string_capability_namespaces(self) -> None:
        task = Task(
            id="task-cap-2",
            description="Write python algorithm",
            category=TaskCategory.CODE,
            parameters={
                "required_capabilities": ["model:code"],
            },
        )
        result = self.dispatcher.dispatch(task)
        self.assertTrue(result.success)
        self.assertIn(result.provider, ("mock", "openrouter", "deepseek", "openai", "anthropic"))

    def test_03_unmet_required_capability_fails_closed(self) -> None:
        task = Task(
            id="task-cap-3",
            description="Non-existent quantum capability",
            category=TaskCategory.CODE,
            parameters={
                "required_capabilities": ["model:quantum_warp_drive"],
            },
        )
        result = self.dispatcher.dispatch(task)
        self.assertFalse(result.success)
        self.assertIn("CapabilityFabric rejected request", result.error)

    def test_04_execution_records_telemetry_and_accounting_in_fabric(self) -> None:
        run_id = "run-exec-telemetry-101"
        task = Task(
            id="task-telemetry-1",
            description="Analyze codebase",
            category=TaskCategory.ANALYSIS,
            context={"run_id": run_id},
        )
        result = self.dispatcher.dispatch(task)
        self.assertTrue(result.success)

        # Check telemetry recorded in Fabric
        accounting = self.fabric.get_run_accounting(run_id=run_id, task_id=task.id)
        self.assertEqual(accounting.run_id, run_id)
        self.assertEqual(accounting.total_attempts, 1)
        self.assertEqual(accounting.total_input_tokens, 100)
        self.assertEqual(accounting.total_output_tokens, 50)
        self.assertEqual(accounting.total_tokens, 150)
        self.assertEqual(accounting.total_cost, 0.0005)
        self.assertIsNotNone(accounting.successful_attempt)

    def test_05_fallback_chain_records_multi_attempt_accounting(self) -> None:
        # Replace deepseek with failing agent
        failing_deepseek = FailingAgent("deepseek", model_name="deepseek-chat")
        self.agent_registry._agents["deepseek"] = failing_deepseek

        run_id = "run-exec-fallback-202"
        task = Task(
            id="task-fb-1",
            description="Code refactoring with fallback",
            category=TaskCategory.CODE,
            context={"run_id": run_id},
            parameters={
                "model": "deepseek:deepseek-chat",
            },
        )
        result = self.dispatcher.dispatch(task)
        self.assertTrue(result.success)
        # Succeeded on fallback (e.g. anthropic or openai)
        self.assertNotEqual(result.provider, "deepseek")

        accounting = self.fabric.get_run_accounting(run_id=run_id, task_id=task.id)
        self.assertTrue(accounting.total_attempts >= 2)
        self.assertEqual(accounting.attempt_records[0].provider_name, "deepseek")
        self.assertFalse(accounting.attempt_records[0].success)
        self.assertIsNotNone(accounting.successful_attempt)
        self.assertTrue(accounting.successful_attempt.success)


    def test_06_orchestrator_initializes_with_fabric(self) -> None:
        orch = Orchestrator(
            registry=self.agent_registry,
            default_provider="mock",
            model_registry=self.model_registry,
            fabric=self.fabric,
            allow_paid_providers=True,
        )
        task = Task(
            id="task-orch-1",
            description="Review PR",
            category=TaskCategory.REVIEW,
        )
        result = orch.dispatch(task)
        self.assertTrue(result.success)


if __name__ == '__main__':
    unittest.main()
