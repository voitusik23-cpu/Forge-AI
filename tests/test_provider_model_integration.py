"""Integration tests for Dispatcher and DispatchPolicy with ModelRegistry v0.2."""

from __future__ import annotations

import unittest

from app.agents.base import AgentExecutionError
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.providers.config import ProviderConfig
from app.agents.providers.mock import MockProvider
from app.agents.providers.model_registry import (
    ModelRegistry,
    create_default_registry,
)
from app.usage import Usage
from app.agents.providers.models import (
    CostTier,
    ModelCapability,
    ProviderInfo,
    ProviderModelInfo,
    ProviderProtocol,
)
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.orchestrator.dispatcher import DispatchPolicy, Dispatcher
from app.orchestrator.models import Task, TaskCategory, TaskResult


class StubProvider(Provider):
    def __init__(self, name: str) -> None:
        super().__init__(ProviderConfig(provider_name=name, model_name=f"{name}-test", enabled=True))

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output=f"Stub response: {request.prompt}",
            usage=Usage(input_tokens=0, output_tokens=0, estimated_cost=0.0),
        )


class StubAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.provider_name = name
        self.calls = 0

    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        return TaskResult(task_id=task.id, success=True, output=f"handled by {self.name}")


class FailingAgent(StubAgent):
    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        raise AgentExecutionError(f"{self.name} is not configured")


class DispatcherModelRegistryIntegrationTests(unittest.TestCase):
    """Test Dispatcher integration with ModelRegistry."""

    def setUp(self) -> None:
        self.agent_registry = AgentRegistry()
        self.provider_registry = ProviderRegistry()
        self.model_registry = create_default_registry()

        # Register stub providers and agents for testing
        for p in self.model_registry.list_providers():
            self.provider_registry.register(StubProvider(name=p.provider_id))
            self.agent_registry.register(StubAgent(name=p.provider_id))

    def test_dispatch_policy_with_model_registry_code_task(self) -> None:
        policy = DispatchPolicy()
        task = Task(id="t1", description="write a python script", category=TaskCategory.CODE)

        decision = policy.decide(
            task,
            default_provider="mock",
            model_registry=self.model_registry,
            registered_provider_names=self.provider_registry.list_providers(),
            registered_agent_names=self.agent_registry.list_agents(),
            allow_paid_providers=False,
        )

        self.assertIsNotNone(decision.selected_provider)
        # Without paid providers, cheap/free provider is selected (openrouter, deepseek, groq, mock)
        self.assertIn(decision.selected_provider, ("openrouter", "deepseek", "groq", "mock"))
        self.assertTrue(len(decision.candidates) > 0)

    def test_dispatch_policy_with_explicit_model_override(self) -> None:
        policy = DispatchPolicy()
        task = Task(
            id="t2",
            description="analyze this architecture",
            category=TaskCategory.ANALYSIS,
            parameters={"model": "deepseek:deepseek-reasoner"},
        )

        decision = policy.decide(
            task,
            default_provider="mock",
            model_registry=self.model_registry,
            registered_provider_names=self.provider_registry.list_providers(),
            registered_agent_names=self.agent_registry.list_agents(),
            allow_paid_providers=True,
        )

        self.assertEqual(decision.selected_provider, "deepseek")
        self.assertIn("Explicit model", decision.reason)

    def test_dispatcher_end_to_end_with_model_registry(self) -> None:
        dispatcher = Dispatcher(
            agent_registry=self.agent_registry,
            default_provider="mock",
            provider_registry=self.provider_registry,
            model_registry=self.model_registry,
            fallback_chain=("deepseek", "mock"),
            allow_paid_providers=False,
        )

        task = Task(id="t3", description="Review this pull request", category=TaskCategory.REVIEW)
        result = dispatcher.dispatch(task)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.provider)

    def test_dispatcher_fallback_preservation(self) -> None:
        # Create dispatcher where primary candidate fails execution
        failing_agent_registry = AgentRegistry()
        failing_agent_registry.register(FailingAgent(name="openrouter"))
        failing_agent_registry.register(StubAgent(name="mock"))

        dispatcher = Dispatcher(
            agent_registry=failing_agent_registry,
            default_provider="mock",
            provider_registry=self.provider_registry,
            model_registry=self.model_registry,
            fallback_chain=("mock",),
            allow_paid_providers=False,
        )

        task = Task(id="t4", description="write code", category=TaskCategory.CODE)
        result = dispatcher.dispatch(task)

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "mock")

    def test_backward_compatibility_with_provider_capabilities_registry(self) -> None:
        # Existing tests using ProviderCapabilitiesRegistry must continue to work seamlessly
        cap_registry = ProviderCapabilitiesRegistry()
        mock_p = MockProvider()
        cap_registry.register_provider(mock_p)

        policy = DispatchPolicy()
        task = Task(id="t5", description="misc task", category=TaskCategory.OTHER)
        decision = policy.decide(
            task,
            default_provider="mock",
            capabilities=cap_registry,
            registered_provider_names=["mock"],
            registered_agent_names=["mock"],
            allow_paid_providers=False,
        )
        self.assertEqual(decision.selected_provider, "mock")
