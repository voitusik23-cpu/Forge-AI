"""Offline tests for DeepSeek and OpenRouter compatible provider adapters."""

import unittest
from types import SimpleNamespace

from app.agents.providers.config import ProviderConfig
from app.agents.providers.deepseek import DeepSeekProvider
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.groq import GroqProvider
from app.agents.providers.openai_compatible import (
    CompatibleAuthenticationError,
    CompatibleRateLimitError,
    CompatibleRequestError,
    CompatibleTimeoutError,
)
from app.agents.providers.openrouter import OpenRouterProvider
from app.agents.providers.base import ProviderNotConfiguredError, ProviderRequest
from app.config.secrets import SecretStore


class FakeAuthenticationError(Exception):
    pass


class FakeRateLimitError(Exception):
    pass


class FakeAPITimeoutError(Exception):
    pass


class FakeAPIConnectionError(Exception):
    pass


class FakeAPIError(Exception):
    pass


FAKE_SDK = SimpleNamespace(
    AuthenticationError=FakeAuthenticationError,
    RateLimitError=FakeRateLimitError,
    APITimeoutError=FakeAPITimeoutError,
    APIConnectionError=FakeAPIConnectionError,
    APIError=FakeAPIError,
)


class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


class OpenAICompatibleProviderTests(unittest.TestCase):
    def make_provider(self, provider_type, model="test-model", response=None, error=None):
        completions = FakeCompletions(response=response, error=error)
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        provider = provider_type(
            ProviderConfig(
                provider_name=provider_type.PROVIDER_NAME,
                model_name=model,
                api_key_env_var=provider_type.API_KEY_ENV_VAR,
            ),
            client=client,
            sdk_module=FAKE_SDK,
            secret_store=SecretStore(
                env_file="missing-compatible-test-env",
                environ={provider_type.API_KEY_ENV_VAR: "test-placeholder"},
            ),
        )
        return provider, completions

    def test_factory_registers_both_explicit_providers(self):
        factory = ProviderFactory()
        self.assertIsInstance(factory.create("deepseek"), DeepSeekProvider)
        self.assertIsInstance(factory.create("openrouter"), OpenRouterProvider)
        self.assertIsInstance(factory.create("groq"), GroqProvider)

    def test_deepseek_url_key_prompt_usage_and_response(self):
        provider, completions = self.make_provider(
            DeepSeekProvider,
            model="deepseek-chat",
            response=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="DeepSeek reply"))],
                model="deepseek-chat",
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5),
            ),
        )
        result = provider.generate(
            ProviderRequest(prompt="Summarize", context={"scope": "module"})
        )

        self.assertEqual(provider.BASE_URL, "https://api.deepseek.com")
        self.assertEqual(completions.kwargs["model"], "deepseek-chat")
        self.assertEqual(completions.kwargs["messages"][0]["role"], "user")
        self.assertIn('"scope": "module"', completions.kwargs["messages"][0]["content"])
        self.assertEqual(result.output, "DeepSeek reply")
        self.assertEqual(result.provider_name, "deepseek")
        self.assertEqual(result.usage.input_tokens, 11)
        self.assertEqual(result.usage.output_tokens, 5)
        self.assertIsNone(result.usage.estimated_cost)

    def test_groq_uses_official_compatible_endpoint_and_key(self):
        provider, completions = self.make_provider(
            GroqProvider,
            model="groq-test-model",
            response=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Groq reply"))],
                model="groq-test-model",
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2),
            ),
        )

        result = provider.generate(ProviderRequest(prompt="offline"))

        self.assertEqual(provider.BASE_URL, "https://api.groq.com/openai/v1")
        self.assertEqual(provider.config.api_key_env_var, "GROQ_API_KEY")
        self.assertEqual(completions.kwargs["model"], "groq-test-model")
        self.assertEqual(result.output, "Groq reply")
        self.assertEqual(result.provider_name, "groq")

    def test_openrouter_accepts_arbitrary_and_free_model_ids(self):
        model_ids = (
            "vendor/model:variant",
            OpenRouterProvider.FREE_MODEL_ID,
            OpenRouterProvider.DEFAULT_MODEL_ID,
        )
        for model_id in model_ids:
            with self.subTest(model=model_id):
                provider, completions = self.make_provider(
                    OpenRouterProvider,
                    model=model_id,
                    response=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="Routed"))],
                        model=model_id,
                        usage=None,
                    ),
                )
                config = ProviderConfig(
                    provider_name="openrouter", model_name=model_id
                )
                self.assertEqual(config.model_name, model_id)
                result = provider.generate(ProviderRequest(prompt="Hi"))
                self.assertEqual(provider.BASE_URL, "https://openrouter.ai/api/v1")
                self.assertEqual(completions.kwargs["model"], model_id)
                self.assertEqual(result.model_name, model_id)
                self.assertEqual(result.output, "Routed")

    def test_sdk_client_uses_expected_key_reference_and_base_url(self):
        for provider_type in (DeepSeekProvider, OpenRouterProvider, GroqProvider):
            with self.subTest(provider=provider_type.PROVIDER_NAME):
                completions = FakeCompletions(
                    response=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                        model="test-model",
                        usage=None,
                    )
                )
                captured = {}

                def create_client(**kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(
                        chat=SimpleNamespace(completions=completions)
                    )

                sdk = SimpleNamespace(OpenAI=create_client, **vars(FAKE_SDK))
                store = SecretStore(
                    env_file="missing-compatible-test-env",
                    environ={provider_type.API_KEY_ENV_VAR: "test-value"},
                )
                provider = provider_type(
                    ProviderConfig(
                        provider_name=provider_type.PROVIDER_NAME,
                        model_name="model-id",
                    ),
                    sdk_module=sdk,
                    secret_store=store,
                )

                self.assertEqual(captured, {})
                provider.generate(ProviderRequest(prompt="test"))
                self.assertEqual(
                    captured,
                    {"api_key": "test-value", "base_url": provider_type.BASE_URL},
                )

    def test_missing_key_fails_without_calling_client(self):
        for provider_type in (DeepSeekProvider, OpenRouterProvider, GroqProvider):
            provider, completions = self.make_provider(provider_type)
            provider.secret_store = SecretStore(
                env_file="missing-compatible-test-env", environ={}
            )
            with self.subTest(provider=provider_type.PROVIDER_NAME):
                with self.assertRaisesRegex(
                    ProviderNotConfiguredError, provider_type.API_KEY_ENV_VAR
                ):
                    provider.generate(ProviderRequest(prompt="offline"))
                self.assertIsNone(completions.kwargs)

    def test_sdk_errors_are_sanitized_and_unexpected_errors_propagate(self):
        cases = (
            (FakeAuthenticationError("sensitive"), CompatibleAuthenticationError),
            (FakeRateLimitError("sensitive"), CompatibleRateLimitError),
            (FakeAPITimeoutError("sensitive"), CompatibleTimeoutError),
            (FakeAPIConnectionError("sensitive"), CompatibleRequestError),
            (FakeAPIError("sensitive"), CompatibleRequestError),
        )
        for provider_type in (DeepSeekProvider, OpenRouterProvider, GroqProvider):
            for sdk_error, expected_error in cases:
                with self.subTest(provider=provider_type.PROVIDER_NAME, error=type(sdk_error).__name__):
                    provider, _ = self.make_provider(provider_type, error=sdk_error)
                    with self.assertRaises(expected_error) as raised:
                        provider.generate(ProviderRequest(prompt="test"))
                    self.assertNotIn("sensitive", str(raised.exception))

            provider, _ = self.make_provider(provider_type, error=ValueError("unexpected"))
            with self.assertRaisesRegex(ValueError, "unexpected"):
                provider.generate(ProviderRequest(prompt="test"))


if __name__ == "__main__":
    unittest.main()
