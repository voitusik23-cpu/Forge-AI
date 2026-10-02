"""Offline tests for the one-pass primary and reviewer workflow."""

import unittest
from types import SimpleNamespace

from app.agents.reviewer import ProviderReviewer
from app.orchestrator.models import Task, TaskCategory, TaskResult
from app.orchestrator.multi_agent import MultiAgentExecutor, MultiAgentStatus
from app.orchestrator.review import ReviewResult, ReviewStatus


class FakeOrchestrator:
    def __init__(self, primary=None, review=None):
        self.primary = primary or TaskResult(
            task_id="task-1",
            success=True,
            output="Primary answer",
            provider="openrouter",
            model_name="cohere/code:free",
        )
        self.review = review or TaskResult(
            task_id="task-1-review",
            success=True,
            output="APPROVED\nThe response addresses the task.",
            provider="anthropic",
            model_name="claude-test",
        )
        self.calls = []

    def dispatch(self, task, *, provider_name=None):
        self.calls.append((task, provider_name))
        return self.review if task.category == TaskCategory.REVIEW else self.primary


class FakeReviewer:
    name = "fake-reviewer"

    def __init__(self, result=None, error=None):
        self.result = result or ReviewResult(
            status=ReviewStatus.APPROVED,
            review_text="Looks complete.",
            reviewer=self.name,
            provider="anthropic",
        )
        self.error = error
        self.calls = []

    def review(self, task, primary_result):
        self.calls.append((task, primary_result))
        if self.error:
            raise self.error
        return self.result


class MultiAgentExecutorTests(unittest.TestCase):
    def test_successful_primary_and_approved_review(self):
        orchestrator = FakeOrchestrator()
        reviewer = FakeReviewer()
        result = MultiAgentExecutor(orchestrator, reviewer).execute(
            Task(id="task-1", description="Explain routing")
        )

        self.assertEqual(result.status, MultiAgentStatus.APPROVED)
        self.assertTrue(result.primary_result.success)
        self.assertEqual(result.review_result.status, ReviewStatus.APPROVED)
        self.assertEqual(len(reviewer.calls), 1)

    def test_changes_requested_is_preserved_as_overall_status(self):
        reviewer = FakeReviewer(
            result=ReviewResult(
                status=ReviewStatus.CHANGES_REQUESTED,
                review_text="Missing an edge case.",
                provider="anthropic",
            )
        )
        result = MultiAgentExecutor(FakeOrchestrator(), reviewer).execute(
            Task(id="task-1", description="Explain routing")
        )

        self.assertEqual(result.status, MultiAgentStatus.CHANGES_REQUESTED)
        self.assertEqual(result.review_result.review_text, "Missing an edge case.")

    def test_reviewer_failure_preserves_primary_result(self):
        primary = FakeOrchestrator().primary
        result = MultiAgentExecutor(
            FakeOrchestrator(primary=primary), FakeReviewer(error=RuntimeError("failed"))
        ).execute(Task(id="task-1", description="Explain routing"))

        self.assertEqual(result.status, MultiAgentStatus.REVIEW_FAILED)
        self.assertIs(result.primary_result, primary)
        self.assertEqual(result.review_result.status, ReviewStatus.FAILED)

    def test_primary_failure_skips_reviewer(self):
        orchestrator = FakeOrchestrator(
            primary=TaskResult(task_id="task-1", success=False, error="primary failed")
        )
        reviewer = FakeReviewer()

        result = MultiAgentExecutor(orchestrator, reviewer).execute(
            Task(id="task-1", description="Explain routing")
        )

        self.assertEqual(result.status, MultiAgentStatus.FAILED)
        self.assertIsNone(result.review_result)
        self.assertEqual(reviewer.calls, [])

    def test_reviewer_receives_original_task_and_primary_result_and_can_differ(self):
        orchestrator = FakeOrchestrator()
        reviewer = ProviderReviewer(
            orchestrator,
            SimpleNamespace(
                get=lambda name: SimpleNamespace(model_name="cohere/code:free")
            ),
        )
        original_task = Task(
            id="task-1",
            description="Explain why this is useful",
            context={"audience": "maintainer"},
            category=TaskCategory.ANALYSIS,
        )

        result = MultiAgentExecutor(orchestrator, reviewer).execute(original_task)

        review_task, provider_override = orchestrator.calls[1]
        self.assertIsNone(provider_override)
        self.assertEqual(review_task.category, TaskCategory.REVIEW)
        self.assertEqual(review_task.context["task_description"], original_task.description)
        self.assertEqual(review_task.context["task_category"], "analysis")
        self.assertEqual(review_task.context["primary_provider"], "openrouter")
        self.assertEqual(review_task.context["primary_model"], "cohere/code:free")
        self.assertEqual(review_task.context["primary_response"], "Primary answer")
        self.assertEqual(result.primary_result.provider, "openrouter")
        self.assertEqual(result.review_result.provider, "anthropic")

    def test_malformed_reviewer_decision_is_not_treated_as_approval(self):
        orchestrator = FakeOrchestrator(
            review=TaskResult(
                task_id="task-1-review",
                success=True,
                output="The response looks okay.",
                provider="anthropic",
            )
        )
        reviewer = ProviderReviewer(
            orchestrator,
            SimpleNamespace(
                get=lambda name: SimpleNamespace(model_name="cohere/code:free")
            ),
        )

        result = MultiAgentExecutor(orchestrator, reviewer).execute(
            Task(id="task-1", description="Explain routing")
        )

        self.assertEqual(result.status, MultiAgentStatus.REVIEW_FAILED)
        self.assertEqual(result.review_result.status, ReviewStatus.FAILED)

    def test_explicit_primary_provider_selection_is_forwarded(self):
        orchestrator = FakeOrchestrator()
        reviewer = ProviderReviewer(
            orchestrator,
            SimpleNamespace(
                get=lambda name: SimpleNamespace(model_name="cohere/code:free")
            ),
        )
        executor = MultiAgentExecutor(orchestrator, reviewer)

        executor.execute(
            Task(id="task-1", description="Explain routing"),
            provider_name="deepseek",
        )

        self.assertEqual(orchestrator.calls[0][1], "deepseek")
        self.assertIsNone(orchestrator.calls[1][1])


if __name__ == "__main__":
    unittest.main()
