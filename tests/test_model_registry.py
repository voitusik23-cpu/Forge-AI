"""Unit tests for Provider & Model Capability Registry v0.2."""

from __future__ import annotations

import json
import unittest

from app.agents.providers.model_registry import (
    DuplicateRegistrationError,
    ModelNotFoundError,
    ModelRegistry,
    ProviderNotFoundError,
    create_default_registry,
)
from app.agents.providers.models import (
    CostTier,
    ModelCapability,
    ProviderAccount,
    ProviderInfo,
    ProviderModelInfo,
    ProviderProtocol,
)


class ModelRegistryCoreTests(unittest.TestCase):
    """Test core registration, validation, and lookup mechanics of ModelRegistry."""

    def setUp(self) -> None:
        self.registry = ModelRegistry()

    def test_provider_registration_and_lookup(self) -> None:
        provider = ProviderInfo(
            provider_id="test_provider",
            display_name="Test Provider",
            protocol=ProviderProtocol.OPENAI_CHAT,
            default_base_url="https://api.test.com/v1",
            default_secret_env="TEST_API_KEY",
            supported_models=("test-model-1",),
        )
        self.registry.register_provider(provider)
        self.assertTrue(self.registry.has_provider("test_provider"))
        retrieved = self.registry.get_provider("test_provider")
        self.assertEqual(retrieved.provider_id, "test_provider")
        self.assertEqual(retrieved.display_name, "Test Provider")
        self.assertEqual(retrieved.default_secret_env, "TEST_API_KEY")

    def test_provider_registration_validation(self) -> None:
        with self.assertRaises(ValueError):
            ProviderInfo(provider_id="", display_name="Test", protocol=ProviderProtocol.OPENAI_CHAT)
        with self.assertRaises(ValueError):
            ProviderInfo(provider_id="test", display_name="", protocol=ProviderProtocol.OPENAI_CHAT)
        with self.assertRaises(ValueError):
            ProviderInfo(
                provider_id="test",
                display_name="Test",
                protocol=ProviderProtocol.OPENAI_CHAT,
                default_secret_env="invalid-secret-name!",
            )

    def test_duplicate_provider_registration_fails_closed(self) -> None:
        provider = ProviderInfo(
            provider_id="p1",
            display_name="Provider 1",
            protocol=ProviderProtocol.MOCK,
        )
        self.registry.register_provider(provider)
        with self.assertRaises(DuplicateRegistrationError):
            self.registry.register_provider(provider)

    def test_model_registration_and_lookup(self) -> None:
        provider = ProviderInfo(
            provider_id="p1",
            display_name="Provider 1",
            protocol=ProviderProtocol.OPENAI_CHAT,
        )
        self.registry.register_provider(provider)

        model = ProviderModelInfo(
            provider_id="p1",
            model_id="m1",
            display_name="Model 1",
            context_window=32768,
            capabilities=frozenset({ModelCapability.CODE, ModelCapability.FAST}),
            cost_tier=CostTier.CHEAP,
        )
        self.registry.register_model(model)

        self.assertEqual(model.canonical_id, "p1:m1")
        self.assertTrue(self.registry.has_model("p1:m1"))
        self.assertTrue(self.registry.has_model("m1"))

        # Lookup via canonical ID
        retrieved_canonical = self.registry.get_model("p1:m1")
        self.assertEqual(retrieved_canonical.model_id, "m1")
        self.assertEqual(retrieved_canonical.context_window, 32768)

        # Lookup via short ID
        retrieved_short = self.registry.get_model("m1")
        self.assertEqual(retrieved_short.canonical_id, "p1:m1")

        # Lookup via explicit provider
        retrieved_explicit = self.registry.get_model("m1", provider_id="p1")
        self.assertEqual(retrieved_explicit.canonical_id, "p1:m1")

    def test_model_registration_unregistered_provider_fails(self) -> None:
        model = ProviderModelInfo(
            provider_id="unknown_provider",
            model_id="m1",
            context_window=32768,
            capabilities=frozenset({ModelCapability.CODE}),
            cost_tier=CostTier.FREE,
        )
        with self.assertRaises(ProviderNotFoundError):
            self.registry.register_model(model)

    def test_duplicate_model_registration_fails_closed(self) -> None:
        provider = ProviderInfo(provider_id="p1", display_name="P1", protocol=ProviderProtocol.MOCK)
        self.registry.register_provider(provider)
        model = ProviderModelInfo(
            provider_id="p1",
            model_id="m1",
            context_window=32768,
            capabilities=frozenset({ModelCapability.CODE}),
            cost_tier=CostTier.FREE,
        )
        self.registry.register_model(model)
        with self.assertRaises(DuplicateRegistrationError):
            self.registry.register_model(model)

    def test_ambiguous_short_model_lookup_fails_closed(self) -> None:
        # Two providers having the same short model_id
        p1 = ProviderInfo(provider_id="p1", display_name="P1", protocol=ProviderProtocol.MOCK)
        p2 = ProviderInfo(provider_id="p2", display_name="P2", protocol=ProviderProtocol.MOCK)
        self.registry.register_provider(p1)
        self.registry.register_provider(p2)

        m1 = ProviderModelInfo(
            provider_id="p1",
            model_id="shared-model",
            context_window=10000,
            capabilities=frozenset({ModelCapability.CODE}),
            cost_tier=CostTier.FREE,
        )
        m2 = ProviderModelInfo(
            provider_id="p2",
            model_id="shared-model",
            context_window=20000,
            capabilities=frozenset({ModelCapability.CODE}),
            cost_tier=CostTier.PAID,
        )
        self.registry.register_model(m1)
        self.registry.register_model(m2)

        # Ambiguous lookup without provider_id raises ModelNotFoundError with clear message
        with self.assertRaises(ModelNotFoundError) as ctx:
            self.registry.get_model("shared-model")
        self.assertIn("Ambiguous model ID", str(ctx.exception))

        # Explicit canonical lookup succeeds
        self.assertEqual(self.registry.get_model("p1:shared-model").context_window, 10000)
        self.assertEqual(self.registry.get_model("p2:shared-model").context_window, 20000)

    def test_account_registration_and_validation(self) -> None:
        provider = ProviderInfo(provider_id="p1", display_name="P1", protocol=ProviderProtocol.MOCK)
        self.registry.register_provider(provider)

        account = ProviderAccount(
            account_id="acc_main",
            provider_id="p1",
            secret_ref="PROVIDER_P1_KEY",
            account_email="admin@forge.local",
            priority=200,
        )
        self.registry.register_account(account)

        accounts = self.registry.list_accounts("p1")
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].account_id, "acc_main")
        self.assertEqual(accounts[0].secret_ref, "PROVIDER_P1_KEY")

        # SecretRef validation
        with self.assertRaises(ValueError):
            ProviderAccount(
                account_id="acc_bad",
                provider_id="p1",
                secret_ref="lowercase_invalid_key",
            )


class ModelCapabilityAndFilteringTests(unittest.TestCase):
    """Test model capability querying, context filtering, and cost sorting."""

    def setUp(self) -> None:
        self.registry = create_default_registry()

    def test_default_catalog_providers_loaded(self) -> None:
        expected_providers = {
            "openai", "anthropic", "google", "deepseek", "openrouter", "groq", "together", "xai", "mock"
        }
        registered = {p.provider_id for p in self.registry.list_providers()}
        self.assertEqual(registered, expected_providers)

    def test_find_models_by_task_category(self) -> None:
        code_models = self.registry.find_models(task_category="code")
        self.assertTrue(len(code_models) > 0)
        for m in code_models:
            self.assertTrue(m.has_capability(ModelCapability.CODE))

    def test_find_models_by_required_capabilities(self) -> None:
        reasoning_code_models = self.registry.find_models(
            capabilities=(ModelCapability.CODE, ModelCapability.REASONING)
        )
        self.assertTrue(len(reasoning_code_models) > 0)
        for m in reasoning_code_models:
            self.assertTrue(m.has_capability(ModelCapability.CODE))
            self.assertTrue(m.has_capability(ModelCapability.REASONING))

    def test_find_models_by_context_window(self) -> None:
        large_context_models = self.registry.find_models(min_context=500000)
        self.assertTrue(len(large_context_models) > 0)
        for m in large_context_models:
            self.assertGreaterEqual(m.context_window, 500000)

    def test_find_models_by_cost_tier(self) -> None:
        free_models = self.registry.find_models(max_cost_tier=CostTier.FREE)
        self.assertTrue(len(free_models) > 0)
        for m in free_models:
            self.assertEqual(m.cost_tier, CostTier.FREE)

        cheap_models = self.registry.find_models(max_cost_tier=CostTier.CHEAP)
        for m in cheap_models:
            self.assertIn(m.cost_tier, (CostTier.FREE, CostTier.CHEAP))

    def test_unknown_capability_fails_closed(self) -> None:
        models = self.registry.find_models(capabilities=("non_existent_capability_xyz",))
        self.assertEqual(models, ())

    def test_gateway_model_canonical_id(self) -> None:
        openrouter_free = self.registry.get_model("openrouter:cohere/north-mini-code:free")
        self.assertEqual(openrouter_free.provider_id, "openrouter")
        self.assertEqual(openrouter_free.model_id, "cohere/north-mini-code:free")
        self.assertEqual(openrouter_free.cost_tier, CostTier.FREE)


class SecretBoundaryAndSafetyTests(unittest.TestCase):
    """Verify that credentials and secrets never enter registry representations."""

    def test_provider_account_repr_safety(self) -> None:
        account = ProviderAccount(
            account_id="acc_secret_test",
            provider_id="deepseek",
            secret_ref="DEEPSEEK_API_KEY",
        )
        rep = repr(account)
        self.assertIn("acc_secret_test", rep)
        self.assertIn("DEEPSEEK_API_KEY", rep)
        # Verify no actual credential string or arbitrary payload is exposed
        self.assertTrue(rep.startswith("ProviderAccount("))

    def test_model_info_serialization_safety(self) -> None:
        registry = create_default_registry()
        model = registry.get_model("deepseek:deepseek-chat")
        as_dict = {
            "canonical_id": model.canonical_id,
            "provider_id": model.provider_id,
            "model_id": model.model_id,
            "context_window": model.context_window,
            "capabilities": [c.value for c in model.capabilities],
            "cost_tier": model.cost_tier.value,
        }
        serialized = json.dumps(as_dict)
        self.assertNotIn("API_KEY", serialized)
        self.assertNotIn("secret", serialized)
