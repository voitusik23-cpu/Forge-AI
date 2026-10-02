"""Deterministic tests for the provider layer and Agent integration."""

import unittest
from dataclasses import fields

from app.agents.mock_agent import MockAgent
from app.agents.provider_agent import ProviderAgent
from app.agents.registry import AgentRegistry
from app.agents.providers.anthropic import AnthropicProvider
from app.agents.providers.base import (
    Provider,
    ProviderNotConfiguredError,
    ProviderRequest,
    ProviderResponse,
)
from app.agents.providers.config import ProviderConfig
from app.agents.providers.deepseek import DeepSeekProvider
from app.agents.providers.factory import ProviderFactory, UnknownProviderError
from app.agents.providers.google import GoogleProvider
from app.agents.providers.groq import GroqProvider
from app.agents.providers.mock import MockProvider
from app.agents.providers.openai import OpenAIProvider
from app.agents.providers.openrouter import OpenRouterProvider
from app.agents.providers.registry import (
    DuplicateProviderError,
    ProviderNotFoundError,
    ProviderRegistry,
)
from app.agents.providers.xai import XAIProvider
from app.config.secrets import SecretStore
from app.orchestrator.models import Task
from app.orchestrator.orchestrator import Orchestrator


class ProviderInterfaceTests(unittest.TestCase):
    def test_provider_implementations_use_common_interface(self) -> None:
        for provider_type in (
            OpenAIProvider,
            AnthropicProvider,
            DeepSeekProvider,
            GoogleProvider,
            OpenRouterProvider,
            GroqProvider,
            XAIProvider,
            MockProvider,
        ):
            self.assertTrue(issubclass(provider_type, Provider))

    def test_placeholder_metadata_and_openai_provider_metadata(self) -> None:
        providers = (
            (
                GoogleProvider(
                    secret_store=SecretStore(
                        env_file="missing-provider-interface-env", environ={}
                    )
                ),
                "google",
                "GEMINI_API_KEY",
            ),
            (XAIProvider(), "xai", "XAI_API_KEY"),
        )
        request = ProviderRequest(prompt="offline test")

        for provider, expected_name, env_reference in providers:
            with self.subTest(provider=expected_name):
                self.assertEqual(provider.provider_name, expected_name)
                self.assertEqual(provider.config.api_key_env_var, env_reference)
                self.assertFalse(provider.config.enabled)
                expected = (
                    "GEMINI_API_KEY"
                    if expected_name == "google"
                    else "integration not configured"
                )
                with self.assertRaisesRegex(ProviderNotConfiguredError, expected):
                    provider.generate(request)

        openai = OpenAIProvider()
        self.assertEqual(openai.provider_name, "openai")
        self.assertEqual(openai.config.api_key_env_var, "OPENAI_API_KEY")
        self.assertFalse(openai.config.enabled)
        anthropic = AnthropicProvider()
        self.assertEqual(anthropic.provider_name, "anthropic")
        self.assertEqual(anthropic.config.api_key_env_var, "ANTHROPIC_API_KEY")
        self.assertFalse(anthropic.config.enabled)
        deepseek = DeepSeekProvider()
        self.assertEqual(deepseek.config.api_key_env_var, "DEEPSEEK_API_KEY")
        openrouter = OpenRouterProvider()
        self.assertEqual(openrouter.config.api_key_env_var, "OPENROUTER_API_KEY")
        groq = GroqProvider()
        self.assertEqual(groq.config.api_key_env_var, "GROQ_API_KEY")


class ProviderFactoryTests(unittest.TestCase):
    def test_factory_creates_each_named_provider(self) -> None:
        factory = ProviderFactory()

        expected = {
            "openai": OpenAIProvider,
            "anthropic": AnthropicProvider,
            "deepseek": DeepSeekProvider,
            "google": GoogleProvider,
            "xai": XAIProvider,
            "openrouter": OpenRouterProvider,
            "groq": GroqProvider,
        }
        for name, provider_type in expected.items():
            with self.subTest(provider=name):
                self.assertIsInstance(factory.create(name), provider_type)

    def test_factory_accepts_matching_configuration(self) -> None:
        config = ProviderConfig(
            provider_name="openai",
            model_name="test-model",
            api_key_env_var="OPENAI_API_KEY",
        )

        provider = ProviderFactory().create("openai", config)

        self.assertEqual(provider.model_name, "test-model")

    def test_factory_rejects_unknown_provider(self) -> None:
        with self.assertRaisesRegex(UnknownProviderError, "Unknown provider"):
            ProviderFactory().create("unknown")


class ProviderRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ProviderRegistry()
        self.provider = MockProvider()

    def test_register_get_and_list_providers(self) -> None:
        self.registry.register(self.provider)

        self.assertIs(self.registry.get("mock"), self.provider)
        self.assertEqual(self.registry.list_providers(), ["mock"])

    def test_duplicate_provider_registration_is_rejected(self) -> None:
        self.registry.register(self.provider)

        with self.assertRaisesRegex(DuplicateProviderError, "already registered"):
            self.registry.register(MockProvider())

    def test_unknown_provider_has_clear_error(self) -> None:
        with self.assertRaisesRegex(ProviderNotFoundError, "not registered"):
            self.registry.get("missing")


class MockProviderTests(unittest.TestCase):
    def test_mock_provider_is_deterministic_and_offline(self) -> None:
        provider = MockProvider()
        request = ProviderRequest(
            prompt="summarize this",
            context={"section": "intro"},
        )

        first = provider.generate(request)
        second = provider.generate(request)

        self.assertEqual(first, second)
        self.assertIsInstance(first, ProviderResponse)
        self.assertEqual(first.provider_name, "mock")
        self.assertEqual(first.output, "MockProvider response: summarize this")


class ProviderConfigTests(unittest.TestCase):
    def test_configuration_stores_only_an_environment_reference(self) -> None:
        config = ProviderConfig(provider_name="openai", api_key_env_var="OPENAI_API_KEY")

        self.assertFalse(config.enabled)
        self.assertEqual(config.api_key_env_var, "OPENAI_API_KEY")
        self.assertNotIn("api_key", {item.name for item in fields(config)})

    def test_configuration_rejects_non_reference_key_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "environment variable name"):
            ProviderConfig(provider_name="openai", api_key_env_var="not a variable value")


class ProviderAgentIntegrationTests(unittest.TestCase):
    def test_provider_agent_adapts_provider_for_existing_orchestrator(self) -> None:
        agent = ProviderAgent(MockProvider())

        agent_registry = AgentRegistry()
        agent_registry.register(agent)
        orchestrator = Orchestrator(agent_registry)
        task = Task(
            id="provider-task",
            description="review a small change",
            context={"scope": "tests"},
        )

        result = orchestrator.dispatch(task, agent_name="mock")

        self.assertTrue(result.success)
        self.assertEqual(result.task_id, task.id)
        self.assertEqual(result.output, "MockProvider response: review a small change")

    def test_existing_mock_agent_remains_compatible(self) -> None:
        registry = AgentRegistry()
        registry.register(MockAgent())
        result = Orchestrator(registry).dispatch(
            Task(id="existing", description="existing path"), agent_name="mock"
        )

        self.assertTrue(result.success)
        self.assertIn("MockAgent", result.output)


if __name__ == "__main__":
    unittest.main()
