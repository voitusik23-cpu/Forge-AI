"""Offline tests for the explicit Gemini smoke command."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import smoke_gemini
from app.orchestrator.models import TaskResult


class GeminiSmokeTests(unittest.TestCase):
    def test_smoke_selects_google_explicitly_and_suppresses_response(self):
        calls = {}

        def dispatch(task, *, provider_name):
            calls["task"] = task
            calls["provider_name"] = provider_name
            return TaskResult(
                task_id=task.id,
                success=True,
                output="FORGE_GEMINI_SMOKE_OK with private response text",
            )

        runtime = SimpleNamespace(orchestrator=SimpleNamespace(dispatch=dispatch))
        with patch.object(smoke_gemini, "create_runtime", return_value=runtime):
            with patch("builtins.print") as output:
                self.assertEqual(smoke_gemini.main(), 0)

        self.assertEqual(calls["provider_name"], "google")
        self.assertEqual(calls["task"].category.value, "large-context")
        self.assertEqual(output.call_args.args[0], "Gemini -> Forge AI smoke test passed.")

    def test_failed_smoke_does_not_print_result_details(self):
        runtime = SimpleNamespace(
            orchestrator=SimpleNamespace(
                dispatch=lambda *args, **kwargs: TaskResult(
                    task_id="gemini-smoke",
                    success=False,
                    error="sensitive provider response",
                )
            )
        )
        with patch.object(smoke_gemini, "create_runtime", return_value=runtime):
            with patch("builtins.print") as output:
                self.assertEqual(smoke_gemini.main(), 1)

        self.assertNotIn("sensitive", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
