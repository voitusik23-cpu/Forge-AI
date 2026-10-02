"""Offline tests for declarative provider capability metadata."""

import unittest

from app.agents.providers.capabilities import (
    CostTier,
    ProviderCapabilities,
    ProviderCapabilitiesRegistry,
)
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.config import ProviderConfig


class ProviderCapabilitiesTests(unittest.TestCase):
    def test_runtime_declarations_cover_all_builtin_providers(self) -> None:
        factory = ProviderFactory()
        registry = ProviderCapabilitiesRegistry()
        for name in factory.list_providers():
            registry.register_provider(factory.create(name))

        values = {item.provider_name: item for item in registry.list_capabilities()}
        self.assertEqual(
            set(values),
            {"openai", "anthropic", "google", "xai", "deepseek", "openrouter", "groq", "mock"},
        )
        expected_keys = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GEMINI_API_KEY",
            "xai": "XAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "mock": None,
        }
        for name, key_env in expected_keys.items():
            with self.subTest(provider=name):
                self.assertEqual(values[name].api_key_env, key_env)
                self.assertIsInstance(values[name].cost_tier, CostTier)
                self.assertIsInstance(values[name].supports_streaming, bool)
                self.assertIsInstance(values[name].supports_tools, bool)
                self.assertEqual(values[name].enabled_by_config, name == "mock")

    def test_cost_tier_values_are_coarse_and_fixed(self) -> None:
        self.assertEqual([tier.value for tier in CostTier], ["free", "cheap", "paid"])

    def test_enabled_flag_mirrors_existing_provider_config(self) -> None:
        provider = ProviderFactory().create(
            "openai",
            ProviderConfig(
                provider_name="openai",
                model_name="test-model",
                enabled=True,
                api_key_env_var="OPENAI_API_KEY",
            ),
        )

        capabilities = ProviderCapabilitiesRegistry()
        registered = capabilities.register_provider(provider)

        self.assertTrue(registered.enabled_by_config)
        self.assertIs(capabilities.get("openai"), registered)
        self.assertIsInstance(registered, ProviderCapabilities)

    def test_duplicate_and_unknown_capability_lookup_are_clear(self) -> None:
        registry = ProviderCapabilitiesRegistry()
        provider = ProviderFactory().create("groq")
        registry.register_provider(provider)
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register_provider(provider)
        with self.assertRaisesRegex(LookupError, "not registered"):
            registry.get("missing")


if __name__ == "__main__":
    unittest.main()
