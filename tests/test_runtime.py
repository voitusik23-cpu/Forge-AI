"""Tests for explicit, offline application runtime construction."""

import logging
import unittest

from app.agents.providers.mock import MockProvider
from app.config.settings import RuntimeSettings
from app.orchestrator.models import Task
from app.orchestrator.orchestrator import Orchestrator
from app.runtime.bootstrap import create_runtime
from app.runtime.context import RuntimeContext
from app.runtime.logging import configure_logging


class RuntimeBootstrapTests(unittest.TestCase):
    def test_runtime_contains_settings_registries_and_orchestrator(self) -> None:
        settings = RuntimeSettings(default_model="offline-test")

        runtime = create_runtime(settings)

        self.assertIsInstance(runtime, RuntimeContext)
        self.assertIs(runtime.settings, settings)
        self.assertIsInstance(runtime.orchestrator, Orchestrator)
        self.assertEqual(runtime.provider_registry.list_providers(), ["mock"])
        self.assertEqual(runtime.agent_registry.list_agents(), ["mock"])
        provider = runtime.provider_registry.get("mock")
        self.assertIsInstance(provider, MockProvider)
        self.assertEqual(provider.model_name, "offline-test")

    def test_default_provider_is_configuration_and_dispatch_is_explicit(self) -> None:
        runtime = create_runtime(RuntimeSettings(default_provider="mock"))
        task = Task(id="runtime-task", description="offline task")

        result = runtime.orchestrator.dispatch(task, agent_name="mock")

        self.assertTrue(result.success)
        self.assertEqual(result.output, "MockProvider response: offline task")

    def test_non_mock_provider_bootstraps_without_api_call_or_secret_loading(self) -> None:
        settings = RuntimeSettings(default_provider="openai", default_model="placeholder")

        runtime = create_runtime(settings)

        provider = runtime.provider_registry.get("openai")
        self.assertEqual(provider.model_name, "placeholder")
        self.assertFalse(provider.config.enabled)
        self.assertEqual(provider.config.api_key_env_var, "OPENAI_API_KEY")
        self.assertFalse(hasattr(provider.config, "api_key"))

    def test_logging_uses_configured_level_and_standard_formatter(self) -> None:
        logger = configure_logging(RuntimeSettings(log_level="WARNING"))

        self.assertEqual(logger.level, logging.WARNING)
        self.assertTrue(logger.handlers)
        self.assertIn("%(levelname)s", logger.handlers[0].formatter._fmt)


if __name__ == "__main__":
    unittest.main()
