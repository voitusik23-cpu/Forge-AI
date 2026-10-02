"""Offline tests for the real multi-agent smoke command wiring."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import smoke_multi_agent
from app.orchestrator.models import TaskCategory, TaskResult


class MultiAgentSmokeTests(unittest.TestCase):
    def test_smoke_runs_primary_then_reviewer_and_reports_both_models(self):
        calls = []

        def dispatch(task, *, provider_name=None):
            calls.append((task, provider_name))
            if task.category == TaskCategory.REVIEW:
                return TaskResult(
                    task_id=task.id,
                    success=True,
                    output="APPROVED\nThe answer is complete.",
                    provider="anthropic",
                    model_name="review-model",
                )
            return TaskResult(
                task_id=task.id,
                success=True,
                output="Deterministic routing is predictable.",
                provider="openrouter",
                model_name="primary-model",
            )

        models = {
            "openrouter": "primary-model",
            "anthropic": "review-model",
        }
        runtime = SimpleNamespace(
            orchestrator=SimpleNamespace(dispatch=dispatch),
            provider_registry=SimpleNamespace(
                get=lambda name: SimpleNamespace(model_name=models[name])
            ),
            provider_capabilities=SimpleNamespace(list_capabilities=lambda: []),
        )
        with patch.object(smoke_multi_agent, "create_runtime", return_value=runtime):
            with patch("builtins.print") as output:
                self.assertEqual(smoke_multi_agent.main(), 0)

        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0][1])
        self.assertIsNone(calls[1][1])
        self.assertEqual(calls[1][0].category, TaskCategory.REVIEW)
        rendered = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("provider: openrouter", rendered)
        self.assertIn("provider: anthropic", rendered)
        self.assertIn("model: primary-model", rendered)
        self.assertIn("model: review-model", rendered)
        self.assertIn("status: approved", rendered)


if __name__ == "__main__":
    unittest.main()
