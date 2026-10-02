"""Tests for safe and validated runtime settings."""

import unittest
from dataclasses import FrozenInstanceError, fields

from app.config.settings import (
    ApplicationEnvironment,
    ConfigurationError,
    RuntimeSettings,
    load_settings,
)


class RuntimeSettingsTests(unittest.TestCase):
    def test_safe_default_settings(self) -> None:
        settings = load_settings(environ={})

        self.assertEqual(settings.environment, ApplicationEnvironment.DEVELOPMENT)
        self.assertFalse(settings.debug)
        self.assertEqual(settings.default_provider, "mock")
        self.assertEqual(settings.default_model, "mock")
        self.assertEqual(settings.request_timeout, 30)
        self.assertEqual(settings.retry_count, 2)
        self.assertEqual(settings.log_level, "INFO")

    def test_environment_variables_override_defaults(self) -> None:
        settings = load_settings(
            environ={
                "FORGE_ENV": "test",
                "FORGE_DEBUG": "true",
                "FORGE_DEFAULT_PROVIDER": "mock",
                "FORGE_DEFAULT_MODEL": "test-model",
                "FORGE_REQUEST_TIMEOUT": "45",
                "FORGE_RETRY_COUNT": "4",
                "FORGE_LOG_LEVEL": "debug",
                "FORGE_PROVIDER_FALLBACK_CHAIN": "anthropic, deepseek",
                "FORGE_ALLOW_PAID_PROVIDERS": "true",
            }
        )

        self.assertEqual(settings.environment, ApplicationEnvironment.TEST)
        self.assertTrue(settings.debug)
        self.assertEqual(settings.default_model, "test-model")
        self.assertEqual(settings.request_timeout, 45)
        self.assertEqual(settings.retry_count, 4)
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.provider_fallback_chain, ("anthropic", "deepseek"))
        self.assertTrue(settings.allow_paid_providers)

    def test_boolean_parsing_accepts_documented_forms(self) -> None:
        for value, expected in (("yes", True), ("1", True), ("OFF", False), ("0", False)):
            with self.subTest(value=value):
                settings = load_settings(environ={"FORGE_DEBUG": value})
                self.assertEqual(settings.debug, expected)

    def test_invalid_boolean_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "FORGE_DEBUG.*boolean"):
            load_settings(environ={"FORGE_DEBUG": "sometimes"})

    def test_paid_provider_policy_defaults_to_disabled(self) -> None:
        self.assertFalse(load_settings(environ={}).allow_paid_providers)
        with self.assertRaisesRegex(ConfigurationError, "FORGE_ALLOW_PAID_PROVIDERS"):
            load_settings(environ={"FORGE_ALLOW_PAID_PROVIDERS": "sometimes"})

    def test_integer_values_are_parsed(self) -> None:
        settings = load_settings(
            environ={"FORGE_REQUEST_TIMEOUT": "12", "FORGE_RETRY_COUNT": "0"}
        )

        self.assertEqual(settings.request_timeout, 12)
        self.assertEqual(settings.retry_count, 0)

    def test_invalid_integer_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "FORGE_RETRY_COUNT.*integer"):
            load_settings(environ={"FORGE_RETRY_COUNT": "many"})

    def test_invalid_timeout_integer_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ConfigurationError, "FORGE_REQUEST_TIMEOUT.*integer"
        ):
            load_settings(environ={"FORGE_REQUEST_TIMEOUT": "soon"})

    def test_unknown_environment_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "FORGE_ENV"):
            load_settings(environ={"FORGE_ENV": "staging"})

    def test_non_string_environment_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "FORGE_ENV"):
            RuntimeSettings(environment=None)

    def test_non_positive_timeout_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "greater than 0"):
            load_settings(environ={"FORGE_REQUEST_TIMEOUT": "0"})

    def test_negative_retry_count_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "must not be negative"):
            load_settings(environ={"FORGE_RETRY_COUNT": "-1"})

    def test_invalid_log_level_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "FORGE_LOG_LEVEL"):
            load_settings(environ={"FORGE_LOG_LEVEL": "TRACE"})

    def test_empty_provider_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "FORGE_DEFAULT_PROVIDER"):
            load_settings(environ={"FORGE_DEFAULT_PROVIDER": "  "})

    def test_missing_optional_environment_variables_use_defaults(self) -> None:
        self.assertEqual(load_settings(environ={}), RuntimeSettings())

    def test_fallback_chain_rejects_duplicate_providers(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "must not contain duplicates"):
            RuntimeSettings(provider_fallback_chain=("anthropic", "anthropic"))

    def test_settings_are_immutable_and_contain_no_api_keys(self) -> None:
        settings = RuntimeSettings()

        with self.assertRaises(FrozenInstanceError):
            settings.debug = True
        field_names = {item.name for item in fields(settings)}
        self.assertFalse(any("api_key" in name.lower() for name in field_names))
        self.assertFalse(hasattr(settings, "openai_api_key"))


if __name__ == "__main__":
    unittest.main()
