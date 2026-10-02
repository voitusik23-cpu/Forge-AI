"""Offline tests for the classified real-request smoke command."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import smoke_classified_routing
from app.orchestrator.models import TaskResult


class ClassifiedRoutingSmokeTests(unittest.TestCase):
    def test_smoke_reports_each_detected_category_without_provider_override(self):
        calls = []

        def dispatch(task, *args, **kwargs):
            calls.append((task, args, kwargs))
            return TaskResult(
                task_id=task.id,
                success=True,
                output="Short model response",
                provider="openrouter",
            )

        runtime = SimpleNamespace(
            orchestrator=SimpleNamespace(dispatch=dispatch),
            provider_registry=SimpleNamespace(
                get=lambda name: SimpleNamespace(model_name="cohere/test:free")
            ),
            provider_capabilities=SimpleNamespace(list_capabilities=lambda: []),
        )
        with patch.object(
            smoke_classified_routing, "create_runtime", return_value=runtime
        ):
            with patch("builtins.print") as output:
                self.assertEqual(smoke_classified_routing.main(), 0)

        self.assertEqual(len(calls), 3)
        self.assertEqual([call[0].id.rsplit("-", 1)[-1] for call in calls], [
            "coding", "analysis", "review"
        ])
        for _task, args, kwargs in calls:
            self.assertEqual(args, ())
            self.assertEqual(kwargs, {})
        rendered = "\n".join(call.args[0] for call in output.call_args_list)
        for category in ("code", "analysis", "review"):
            self.assertIn(f"detected category: {category}", rendered)
        self.assertIn("selected provider: openrouter", rendered)
        self.assertIn("selected model: cohere/test:free", rendered)


if __name__ == "__main__":
    unittest.main()
