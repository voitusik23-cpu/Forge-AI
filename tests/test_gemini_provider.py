"""Offline tests for the official Google Gen AI provider adapter."""

import unittest
from types import SimpleNamespace

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import ProviderNotConfiguredError, ProviderRequest
from app.agents.providers.config import ProviderConfig
from app.agents.providers.google import GeminiAPIError, GoogleProvider
from app.config.secrets import SecretStore
from app.agents.registry import AgentRegistry
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.models import Task, TaskCategory, TaskResult


class FakeGoogleAPIError(Exception):
    def __init__(self, code, status, message, http_status=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.response = SimpleNamespace(status_code=http_status or code)


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


class GeminiProviderTests(unittest.TestCase):
    def make_provider(self, *, key="test-placeholder", response=None, error=None):
        models = FakeModels(response, error)
        client = SimpleNamespace(models=models)
        provider = GoogleProvider(
            ProviderConfig(
                provider_name="google",
                model_name="gemini-test-model",
                api_key_env_var="GEMINI_API_KEY",
            ),
            client=client,
            sdk_module=SimpleNamespace(
                errors=SimpleNamespace(APIError=FakeGoogleAPIError)
            ),
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
        self.assertEqual(
            models.kwargs["config"]["automatic_function_calling"]["disable"], True
        )
        self.assertIn('"scope": "module"', models.kwargs["contents"])
        self.assertEqual(result.provider_name, "google")
        self.assertEqual(result.output, "Gemini reply")
        self.assertEqual(result.usage.input_tokens, 8)
        self.assertEqual(result.usage.output_tokens, 3)

    def test_503_preserves_safe_structured_google_error(self):
        provider, _ = self.make_provider(
            error=FakeGoogleAPIError(
                503,
                "UNAVAILABLE",
                "This model is currently experiencing high demand",
            )
        )

        with self.assertRaises(GeminiAPIError) as caught:
            provider.generate(ProviderRequest(prompt="offline"))

        self.assertEqual(caught.exception.http_status, 503)
        self.assertEqual(caught.exception.google_code, 503)
        self.assertEqual(caught.exception.google_status, "UNAVAILABLE")
        self.assertIn("high demand", caught.exception.safe_message)
        self.assertNotIn("test-placeholder", str(caught.exception))

    def test_other_api_error_preserves_status_and_sanitizes_credentials(self):
        provider, _ = self.make_provider(
            error=FakeGoogleAPIError(
                400,
                "INVALID_ARGUMENT",
                "Invalid request; key=test-placeholder",
            )
        )

        with self.assertRaises(GeminiAPIError) as caught:
            provider.generate(ProviderRequest(prompt="offline"))

        self.assertEqual(caught.exception.http_status, 400)
        self.assertEqual(caught.exception.google_status, "INVALID_ARGUMENT")
        self.assertNotIn("test-placeholder", caught.exception.safe_message)
        self.assertIn("[REDACTED]", caught.exception.safe_message)

    def test_missing_key_fails_before_calling_client(self):
        provider, models = self.make_provider(key=None)
        with self.assertRaisesRegex(ProviderNotConfiguredError, "GEMINI_API_KEY"):
            provider.generate(ProviderRequest(prompt="offline"))
        self.assertIsNone(models.kwargs)

    def test_dispatcher_uses_fallback_after_gemini_503(self):
        gemini, models = self.make_provider(
            error=FakeGoogleAPIError(
                503, "UNAVAILABLE", "This model is currently experiencing high demand"
            )
        )

        class FallbackAgent:
            name = "openrouter"

            def run(self, task):
                return TaskResult(
                    task_id=task.id, success=True, output="fallback response"
                )

        registry = AgentRegistry()
        registry.register(ProviderAgent(gemini))
        registry.register(FallbackAgent())
        dispatcher = Dispatcher(registry, fallback_chain=("openrouter",))

        result = dispatcher.dispatch(
            Task(
                id="gemini-fallback",
                description="summarize",
                category=TaskCategory.LARGE_CONTEXT,
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output, "fallback response")
        self.assertEqual(models.kwargs["model"], "gemini-test-model")


if __name__ == "__main__":
    unittest.main()
