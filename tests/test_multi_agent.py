"""Offline tests for the one-pass primary and reviewer workflow."""

import unittest
from types import SimpleNamespace

from app.agents.reviewer import ProviderReviewer
from app.orchestrator.models import Task, TaskCategory, TaskResult
from app.orchestrator.multi_agent import MultiAgentExecutor, MultiAgentStatus
from app.orchestrator.review import ReviewResult, ReviewStatus


class FakeOrchestrator:
    def __init__(
        self, primary=None, review=None, primary_results=None, review_results=None
    ):
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
        self.primary_results = list(primary_results or [self.primary])
        self.review_results = list(review_results or [self.review])
        self.calls = []

    def dispatch(self, task, *, provider_name=None):
        self.calls.append((task, provider_name))
        if task.category == TaskCategory.REVIEW:
            if len(self.review_results) > 1:
                return self.review_results.pop(0)
            return self.review_results[0]
        if len(self.primary_results) > 1:
            return self.primary_results.pop(0)
        return self.primary_results[0]


class FakeReviewer:
    name = "fake-reviewer"

    def __init__(self, result=None, error=None, results=None):
        self.result = result or ReviewResult(
            status=ReviewStatus.APPROVED,
            review_text="Looks complete.",
            reviewer=self.name,
            provider="anthropic",
        )
        self.results = list(results or [self.result])
        self.error = error
        self.calls = []

    def review(self, task, primary_result):
        self.calls.append((task, primary_result))
        if self.error:
            raise self.error
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


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
        self.assertEqual(len(reviewer.calls), 2)

    def test_requested_changes_run_one_revision_then_final_review(self):
        initial = TaskResult(
            task_id="task-1", success=True, output="Initial answer", provider="openrouter"
        )
        revised = TaskResult(
            task_id="task-1-revision-1",
            success=True,
            output="Revised answer",
            provider="openrouter",
        )
        first_review = ReviewResult(
            status=ReviewStatus.CHANGES_REQUESTED,
            review_text="Include the missing edge case.",
        )
        final_review = ReviewResult(
            status=ReviewStatus.APPROVED, review_text="The edge case is covered."
        )
        reviewer = FakeReviewer(results=[first_review, final_review])
        executor = MultiAgentExecutor(
            FakeOrchestrator(primary=initial, primary_results=[initial, revised]), reviewer
        )

        result = executor.execute(
            Task(
                id="task-1",
                description="Implement a parser",
                context={"scope": "small"},
                category=TaskCategory.CODE,
            )
        )

        revision_task, _ = reviewer.calls[1]
        self.assertEqual(result.status, MultiAgentStatus.APPROVED)
        self.assertIs(result.primary_result, initial)
        self.assertIs(result.revision_result, revised)
        self.assertIs(result.final_primary_result, revised)
        self.assertIs(result.review_result, first_review)
        self.assertIs(result.final_review_result, final_review)
        self.assertIn("Original task:\nImplement a parser", revision_task.description)
        self.assertEqual(revision_task.context["scope"], "small")
        self.assertEqual(revision_task.category, TaskCategory.CODE)
        self.assertEqual(
            revision_task.context["forge_revision"]["previous_primary_response"],
            "Initial answer",
        )
        self.assertEqual(
            revision_task.context["forge_revision"]["original_task_description"],
            "Implement a parser",
        )
        self.assertEqual(
            revision_task.context["forge_revision"]["review_feedback"],
            "Include the missing edge case.",
        )

    def test_no_second_revision_if_final_review_still_requests_changes(self):
        reviewer = FakeReviewer(
            results=[
                ReviewResult(ReviewStatus.CHANGES_REQUESTED, "First feedback"),
                ReviewResult(ReviewStatus.CHANGES_REQUESTED, "Still incomplete"),
            ]
        )
        orchestrator = FakeOrchestrator(
            primary_results=[
                TaskResult(task_id="task-1", success=True, output="v1"),
                TaskResult(task_id="task-1-revision-1", success=True, output="v2"),
            ]
        )

        result = MultiAgentExecutor(orchestrator, reviewer).execute(
            Task(id="task-1", description="Write code")
        )

        self.assertEqual(result.status, MultiAgentStatus.CHANGES_REQUESTED)
        self.assertEqual(len(reviewer.calls), 2)
        self.assertEqual(len(orchestrator.calls), 2)
        self.assertEqual(result.final_review.review_text, "Still incomplete")

    def test_revision_failure_preserves_original_primary_and_stops(self):
        initial = TaskResult(task_id="task-1", success=True, output="v1")
        failed_revision = TaskResult(
            task_id="task-1-revision-1", success=False, error="revision failed"
        )
        reviewer = FakeReviewer(
            result=ReviewResult(ReviewStatus.CHANGES_REQUESTED, "Please revise")
        )
        orchestrator = FakeOrchestrator(
            primary=initial, primary_results=[initial, failed_revision]
        )

        result = MultiAgentExecutor(orchestrator, reviewer).execute(
            Task(id="task-1", description="Write code")
        )

        self.assertEqual(result.status, MultiAgentStatus.REVISION_FAILED)
        self.assertIs(result.primary_result, initial)
        self.assertIs(result.revision_result, failed_revision)
        self.assertEqual(len(reviewer.calls), 1)

    def test_final_review_gets_revision_feedback_and_explicit_provider_is_retained(self):
        initial = TaskResult(
            task_id="task-1", success=True, output="initial", provider="openai"
        )
        revised = TaskResult(
            task_id="task-1-revision-1", success=True, output="revised", provider="openai"
        )
        orchestrator = FakeOrchestrator(
            primary_results=[initial, revised],
            review_results=[
                TaskResult(
                    task_id="task-1-review",
                    success=True,
                    output="CHANGES_REQUESTED\nAdd validation.",
                    provider="anthropic",
                ),
                TaskResult(
                    task_id="task-1-revision-1-review",
                    success=True,
                    output="APPROVED\nValidation is now present.",
                    provider="anthropic",
                ),
            ],
        )
        executor = MultiAgentExecutor(
            orchestrator,
            ProviderReviewer(
                orchestrator,
                SimpleNamespace(
                    get=lambda name: SimpleNamespace(model_name=f"{name}-model")
                ),
            ),
        )
        task = Task(id="task-1", description="Implement validation")

        result = executor.execute(task, provider_name="openai")

        final_review_task, _ = orchestrator.calls[3]
        self.assertEqual(result.status, MultiAgentStatus.APPROVED)
        self.assertEqual(
            [provider for _task, provider in orchestrator.calls],
            ["openai", None, "openai", None],
        )
        self.assertEqual(
            final_review_task.context["task_description"], "Implement validation"
        )
        self.assertEqual(
            final_review_task.context["prior_review_feedback"], "Add validation."
        )
        self.assertEqual(
            final_review_task.context["previous_primary_response"], "initial"
        )

    def test_final_review_failure_keeps_revision_and_initial_review(self):
        reviewer = FakeReviewer(
            results=[
                ReviewResult(ReviewStatus.CHANGES_REQUESTED, "Needs a fix"),
                ReviewResult(ReviewStatus.FAILED, "Final review unavailable"),
            ]
        )
        initial = TaskResult(task_id="task-1", success=True, output="v1")
        revised = TaskResult(task_id="task-1-revision-1", success=True, output="v2")
        result = MultiAgentExecutor(
            FakeOrchestrator(primary=initial, primary_results=[initial, revised]),
            reviewer,
        ).execute(Task(id="task-1", description="Write code"))

        self.assertEqual(result.status, MultiAgentStatus.REVIEW_FAILED)
        self.assertEqual(result.review_result.status, ReviewStatus.CHANGES_REQUESTED)
        self.assertEqual(result.final_review_result.status, ReviewStatus.FAILED)
        self.assertIs(result.revision_result, revised)

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
