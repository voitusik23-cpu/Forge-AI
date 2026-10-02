"""Offline tests for the OpenAI Responses API adapter."""

import os
import io
import logging
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.providers.base import ProviderNotConfiguredError, ProviderRequest
from app.agents.providers.config import ProviderConfig
from app.agents.providers.openai import (
    OpenAIAuthenticationError,
    OpenAIProvider,
    OpenAIRateLimitError,
    OpenAIRequestError,
    OpenAIResponseError,
    OpenAITimeoutError,
)


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


class FakeResponses:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


class OpenAIProviderTests(unittest.TestCase):
    def make_provider(self, response=None, error=None):
        responses = FakeResponses(response=response, error=error)
        client = SimpleNamespace(responses=responses)
        config = ProviderConfig(
            provider_name="openai",
            model_name="test-model",
            api_key_env_var="OPENAI_API_KEY",
        )
        return OpenAIProvider(config, client=client, sdk_module=FAKE_SDK), responses

    def test_sends_model_and_prompt_to_responses_api(self):
        provider, responses = self.make_provider(
            SimpleNamespace(output_text="Generated text", model="served-model", usage=None)
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}):
            result = provider.generate(ProviderRequest(prompt="Say hello"))

        self.assertEqual(responses.kwargs, {"model": "test-model", "input": "Say hello"})
        self.assertEqual(result.output, "Generated text")
        self.assertEqual(result.provider_name, "openai")
        self.assertEqual(result.model_name, "served-model")

    def test_formats_context_and_maps_usage(self):
        provider, responses = self.make_provider(
            SimpleNamespace(
                output_text="Answer",
                model="test-model",
                usage=SimpleNamespace(input_tokens=12, output_tokens=7),
            )
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}):
            result = provider.generate(
                ProviderRequest(prompt="Prompt", context={"scope": "module"})
            )

        self.assertIn('"scope": "module"', responses.kwargs["input"])
        self.assertEqual(result.usage.input_tokens, 12)
        self.assertEqual(result.usage.output_tokens, 7)
        self.assertIsNone(result.usage.estimated_cost)

    def test_creates_sdk_client_lazily_with_environment_key_only(self):
        responses = FakeResponses(
            response=SimpleNamespace(output_text="OK", model="test-model", usage=None)
        )
        captured = {}

        def make_client(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(responses=responses)

        sdk = SimpleNamespace(OpenAI=make_client, **vars(FAKE_SDK))
        provider = OpenAIProvider(
            ProviderConfig(provider_name="openai", model_name="test-model"),
            sdk_module=sdk,
        )
        self.assertNotIn("test-placeholder", repr(provider.config))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}):
            result = provider.generate(ProviderRequest(prompt="test"))

        self.assertEqual(captured, {"api_key": "test-placeholder"})
        self.assertEqual(result.output, "OK")

    def test_missing_key_is_clear_and_does_not_call_client(self):
        provider, responses = self.make_provider(
            SimpleNamespace(output_text="no", model="test-model", usage=None)
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ProviderNotConfiguredError, "OPENAI_API_KEY"):
                provider.generate(ProviderRequest(prompt="offline"))
        self.assertIsNone(responses.kwargs)

    def test_unconfigured_model_is_rejected(self):
        provider, responses = self.make_provider()
        provider.config = ProviderConfig(provider_name="openai")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}):
            with self.assertRaisesRegex(ProviderNotConfiguredError, "model"):
                provider.generate(ProviderRequest(prompt="offline"))
        self.assertIsNone(responses.kwargs)

    def test_expected_sdk_errors_are_mapped_without_sdk_messages(self):
        cases = (
            (FakeAuthenticationError("sensitive sdk detail"), OpenAIAuthenticationError),
            (FakeRateLimitError("sensitive sdk detail"), OpenAIRateLimitError),
            (FakeAPITimeoutError("sensitive sdk detail"), OpenAITimeoutError),
            (FakeAPIConnectionError("sensitive sdk detail"), OpenAIRequestError),
            (FakeAPIError("sensitive sdk detail"), OpenAIRequestError),
        )
        for sdk_error, provider_error in cases:
            with self.subTest(error=type(sdk_error).__name__):
                provider, _ = self.make_provider(error=sdk_error)
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}):
                    with self.assertRaises(provider_error) as raised:
                        provider.generate(ProviderRequest(prompt="test"))
                self.assertNotIn("sensitive sdk detail", str(raised.exception))

    def test_provider_error_and_logs_do_not_expose_key(self):
        hidden = "test-placeholder-key"
        provider, _ = self.make_provider(error=FakeAuthenticationError(hidden))
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger("app.agents.providers.openai")
        logger.addHandler(handler)
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": hidden}):
                with self.assertRaises(OpenAIAuthenticationError) as raised:
                    provider.generate(ProviderRequest(prompt="test"))
        finally:
            logger.removeHandler(handler)
        self.assertNotIn(hidden, str(raised.exception))
        formatted = "".join(traceback.format_exception(raised.exception))
        self.assertNotIn(hidden, formatted)
        self.assertNotIn(hidden, output.getvalue())

    def test_malformed_response_and_usage_are_reported(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}):
            provider, _ = self.make_provider(
                SimpleNamespace(output_text=None, model="test-model", usage=None)
            )
            with self.assertRaises(OpenAIResponseError):
                provider.generate(ProviderRequest(prompt="test"))

            provider, _ = self.make_provider(
                SimpleNamespace(
                    output_text="text",
                    model="test-model",
                    usage=SimpleNamespace(input_tokens="bad", output_tokens=1),
                )
            )
            with self.assertRaisesRegex(OpenAIResponseError, "usage"):
                provider.generate(ProviderRequest(prompt="test"))

    def test_unexpected_errors_are_not_swallowed(self):
        provider, _ = self.make_provider(error=ValueError("unexpected"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}):
            with self.assertRaisesRegex(ValueError, "unexpected"):
                provider.generate(ProviderRequest(prompt="test"))


if __name__ == "__main__":
    unittest.main()
