"""Offline tests for the automatic routing smoke command."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import smoke_routing
from app.orchestrator.models import TaskResult


class RoutingSmokeTests(unittest.TestCase):
    def test_smoke_dispatches_without_provider_override_and_reports_selected_model(self):
        calls = {}

        def dispatch(task, *args, **kwargs):
            calls["task"] = task
            calls["args"] = args
            calls["kwargs"] = kwargs
            return TaskResult(
                task_id=task.id,
                success=True,
                output="A dispatcher routes tasks to a provider.",
                provider="openrouter",
            )

        runtime = SimpleNamespace(
            orchestrator=SimpleNamespace(dispatch=dispatch),
            provider_registry=SimpleNamespace(
                get=lambda name: SimpleNamespace(model_name="cohere/test:free")
            ),
            provider_capabilities=SimpleNamespace(list_capabilities=lambda: []),
        )
        with patch.object(smoke_routing, "create_runtime", return_value=runtime):
            with patch("builtins.print") as output:
                self.assertEqual(smoke_routing.main(), 0)

        self.assertEqual(calls["args"], ())
        self.assertEqual(calls["kwargs"], {})
        self.assertEqual(calls["task"].category.value, "other")
        self.assertEqual(output.call_args_list[0].args[0], "provider: openrouter")
        self.assertEqual(output.call_args_list[1].args[0], "model: cohere/test:free")
        self.assertEqual(output.call_args_list[2].args[0], "status: success")
        self.assertIn("dispatcher", output.call_args_list[3].args[0])

    def test_smoke_redacts_credentials_from_response(self):
        provider = SimpleNamespace(
            model_name="model", secret_store=SimpleNamespace()
        )
        provider.secret_store.get_secret = lambda name: "token-secret-value"
        runtime = SimpleNamespace(
            orchestrator=SimpleNamespace(
                dispatch=lambda task: TaskResult(
                    task_id=task.id,
                    success=True,
                    output="Use token-secret-value carefully.",
                    provider="openrouter",
                )
            ),
            provider_registry=SimpleNamespace(
                get=lambda name: provider
            ),
            provider_capabilities=SimpleNamespace(
                list_capabilities=lambda: [
                    SimpleNamespace(provider_name="openrouter", api_key_env="KEY")
                ]
            ),
        )

        with patch.object(smoke_routing, "create_runtime", return_value=runtime):
            with patch("builtins.print") as output:
                self.assertEqual(smoke_routing.main(), 0)

        rendered = " ".join(call.args[0] for call in output.call_args_list)
        self.assertNotIn("token-secret-value", rendered)
        self.assertIn("[REDACTED]", rendered)


if __name__ == "__main__":
    unittest.main()
