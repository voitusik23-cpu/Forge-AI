"""Offline tests for the Anthropic Messages API provider."""

import unittest
from types import SimpleNamespace

from app.agents.providers.base import ProviderNotConfiguredError, ProviderRequest
from app.agents.providers.config import ProviderConfig
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.anthropic import (
    AnthropicAuthenticationError,
    AnthropicProvider,
    AnthropicRateLimitError,
    AnthropicRequestError,
    AnthropicResponseError,
    AnthropicTimeoutError,
)
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


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


class AnthropicProviderTests(unittest.TestCase):
    def make_provider(self, response=None, error=None):
        messages = FakeMessages(response=response, error=error)
        client = SimpleNamespace(messages=messages)
        config = ProviderConfig(
            provider_name="anthropic",
            model_name="claude-test-model",
            api_key_env_var="ANTHROPIC_API_KEY",
        )
        secret_store = SecretStore(
            env_file="missing-anthropic-test-env",
            environ={"ANTHROPIC_API_KEY": "test-placeholder"},
        )
        return (
            AnthropicProvider(
                config,
                client=client,
                sdk_module=FAKE_SDK,
                secret_store=secret_store,
            ),
            messages,
        )

    def test_messages_api_receives_model_prompt_context_and_limit(self):
        provider, messages = self.make_provider(
            SimpleNamespace(
                content=[SimpleNamespace(type="text", text="First"),
                         SimpleNamespace(type="text", text="Second")],
                model="claude-served-model",
                usage=SimpleNamespace(input_tokens=14, output_tokens=9),
            )
        )
        result = provider.generate(
            ProviderRequest(prompt="Review this", context={"scope": "module"})
        )

        self.assertEqual(messages.kwargs["model"], "claude-test-model")
        self.assertEqual(messages.kwargs["max_tokens"], 1024)
        self.assertEqual(messages.kwargs["messages"][0]["role"], "user")
        self.assertIn('"scope": "module"', messages.kwargs["messages"][0]["content"])
        self.assertEqual(result.provider_name, "anthropic")
        self.assertEqual(result.model_name, "claude-served-model")
        self.assertEqual(result.output, "First\nSecond")
        self.assertEqual(result.usage.input_tokens, 14)
        self.assertEqual(result.usage.output_tokens, 9)
        self.assertIsNone(result.usage.estimated_cost)

    def test_provider_factory_registers_anthropic_with_secret_store(self):
        store = SecretStore(env_file="missing-test-env", environ={})
        provider = ProviderFactory().create("anthropic", secret_store=store)

        self.assertIsInstance(provider, AnthropicProvider)
        self.assertIs(provider.secret_store, store)

    def test_missing_key_fails_before_api_call(self):
        provider, messages = self.make_provider(
            SimpleNamespace(content=[], model="claude", usage=None)
        )
        provider.secret_store = SecretStore(
            env_file="missing-anthropic-test-env", environ={}
        )

        with self.assertRaisesRegex(ProviderNotConfiguredError, "ANTHROPIC_API_KEY"):
            provider.generate(ProviderRequest(prompt="offline"))
        self.assertIsNone(messages.kwargs)

    def test_sdk_client_is_created_lazily_with_secret_store_value(self):
        messages = FakeMessages(
            response=SimpleNamespace(
                content=[{"type": "text", "text": "Safe"}],
                model="claude-test-model",
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )
        )
        captured = {}

        def make_client(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(messages=messages)

        sdk = SimpleNamespace(Anthropic=make_client, **vars(FAKE_SDK))
        store = SecretStore(
            env_file="missing-test-env", environ={"ANTHROPIC_API_KEY": "test-value"}
        )
        provider = AnthropicProvider(
            ProviderConfig(provider_name="anthropic", model_name="claude-test-model"),
            sdk_module=sdk,
            secret_store=store,
        )

        self.assertEqual(captured, {})
        result = provider.generate(ProviderRequest(prompt="test"))

        self.assertEqual(captured, {"api_key": "test-value"})
        self.assertEqual(result.output, "Safe")

    def test_expected_sdk_errors_are_sanitized(self):
        cases = (
            (FakeAuthenticationError("sensitive"), AnthropicAuthenticationError),
            (FakeRateLimitError("sensitive"), AnthropicRateLimitError),
            (FakeAPITimeoutError("sensitive"), AnthropicTimeoutError),
            (FakeAPIConnectionError("sensitive"), AnthropicRequestError),
            (FakeAPIError("sensitive"), AnthropicRequestError),
        )
        for sdk_error, provider_error in cases:
            with self.subTest(error=type(sdk_error).__name__):
                provider, _ = self.make_provider(error=sdk_error)
                with self.assertRaises(provider_error) as raised:
                    provider.generate(ProviderRequest(prompt="test"))
                self.assertNotIn("sensitive", str(raised.exception))

    def test_malformed_responses_and_unexpected_errors(self):
        malformed = (
            SimpleNamespace(content=[], model="claude", usage=None),
            SimpleNamespace(
                content=[{"type": "text", "text": "text"}],
                model="claude",
                usage=SimpleNamespace(input_tokens="invalid", output_tokens=1),
            ),
        )
        for response in malformed:
            with self.subTest(response=response):
                provider, _ = self.make_provider(response=response)
                with self.assertRaises(AnthropicResponseError):
                    provider.generate(ProviderRequest(prompt="test"))

        provider, _ = self.make_provider(error=ValueError("unexpected"))
        with self.assertRaisesRegex(ValueError, "unexpected"):
            provider.generate(ProviderRequest(prompt="test"))


if __name__ == "__main__":
    unittest.main()
