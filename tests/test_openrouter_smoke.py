"""Offline tests for the explicit OpenRouter smoke command."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import smoke_openrouter
from app.orchestrator.models import TaskResult


class OpenRouterSmokeTests(unittest.TestCase):
    def test_smoke_dispatches_explicitly_to_openrouter_without_exposing_output(self):
        calls = {}

        def dispatch(task, *, provider_name):
            calls["task"] = task
            calls["provider_name"] = provider_name
            return TaskResult(
                task_id=task.id,
                success=True,
                output="FORGE_OPENROUTER_SMOKE_OK plus hidden response text",
            )

        runtime = SimpleNamespace(orchestrator=SimpleNamespace(dispatch=dispatch))
        with patch.object(smoke_openrouter, "create_runtime", return_value=runtime):
            with patch("builtins.print") as output:
                self.assertEqual(smoke_openrouter.main(), 0)

        self.assertEqual(calls["provider_name"], "openrouter")
        self.assertEqual(calls["task"].parameters, {})
        self.assertEqual(
            output.call_args.args[0],
            "OpenRouter -> Cohere -> Forge AI smoke test passed.",
        )

    def test_failure_message_suppresses_provider_details(self):
        runtime = SimpleNamespace(
            orchestrator=SimpleNamespace(
                dispatch=lambda *args, **kwargs: TaskResult(
                    task_id="openrouter-smoke",
                    success=False,
                    error="provider error with sensitive detail",
                )
            )
        )
        with patch.object(smoke_openrouter, "create_runtime", return_value=runtime):
            with patch("builtins.print") as output:
                self.assertEqual(smoke_openrouter.main(), 1)

        self.assertNotIn("sensitive", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
