"""Offline tests for the official Google Gen AI provider adapter."""

import unittest
from types import SimpleNamespace

from app.agents.providers.base import ProviderNotConfiguredError, ProviderRequest
from app.agents.providers.config import ProviderConfig
from app.agents.providers.google import GoogleProvider
from app.config.secrets import SecretStore


class FakeModels:
    def __init__(self, response=None):
        self.response = response
        self.kwargs = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class GeminiProviderTests(unittest.TestCase):
    def make_provider(self, *, key="test-placeholder", response=None):
        models = FakeModels(response)
        client = SimpleNamespace(models=models)
        provider = GoogleProvider(
            ProviderConfig(
                provider_name="google",
                model_name="gemini-test-model",
                api_key_env_var="GEMINI_API_KEY",
            ),
            client=client,
            sdk_module=SimpleNamespace(),
            secret_store=SecretStore(
                env_file="missing-gemini-test-env",
                environ={"GEMINI_API_KEY": key} if key else {},
            ),
        )
        return provider, models

    def test_gemini_uses_secret_model_context_and_maps_response(self):
        provider, models = self.make_provider(
            response=SimpleNamespace(
                text="Gemini reply",
                model_version="gemini-test-model",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=8, candidates_token_count=3
                ),
            )
        )

        result = provider.generate(
            ProviderRequest(prompt="Summarize", context={"scope": "module"})
        )

        self.assertEqual(models.kwargs["model"], "gemini-test-model")
        self.assertIn('"scope": "module"', models.kwargs["contents"])
        self.assertEqual(result.provider_name, "google")
        self.assertEqual(result.output, "Gemini reply")
        self.assertEqual(result.usage.input_tokens, 8)
        self.assertEqual(result.usage.output_tokens, 3)

    def test_missing_key_fails_before_calling_client(self):
        provider, models = self.make_provider(key=None)
        with self.assertRaisesRegex(ProviderNotConfiguredError, "GEMINI_API_KEY"):
            provider.generate(ProviderRequest(prompt="offline"))
        self.assertIsNone(models.kwargs)


if __name__ == "__main__":
    unittest.main()
