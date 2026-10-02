"""One-primary, one-review execution workflow."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.agents.reviewer import Reviewer
from app.orchestrator.classification import classify_task
from app.orchestrator.models import Task, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.review import ReviewResult, ReviewStatus


class MultiAgentStatus(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    FAILED = "failed"
    REVIEW_FAILED = "review_failed"
    REVISION_FAILED = "revision_failed"


@dataclass
class MultiAgentResult:
    task_id: str
    primary_result: TaskResult
    review_result: Optional[ReviewResult]
    status: MultiAgentStatus
    revision_result: Optional[TaskResult] = None
    final_review_result: Optional[ReviewResult] = None

    @property
    def final_primary_result(self) -> TaskResult:
        """Return the revised primary result when one was produced."""
        return self.revision_result or self.primary_result

    @property
    def final_review(self) -> Optional[ReviewResult]:
        """Return the last review decision while retaining the initial review."""
        return self.final_review_result or self.review_result


class MultiAgentExecutor:
    """Run a primary and review, with at most one controlled correction cycle."""

    def __init__(self, orchestrator: Orchestrator, reviewer: Reviewer) -> None:
        self._orchestrator = orchestrator
        self._reviewer = reviewer

    def execute(
        self, task: Task, *, provider_name: Optional[str] = None
    ) -> MultiAgentResult:
        """Run primary -> review and, once if requested, revision -> final review."""
        primary = self._orchestrator.dispatch(task, provider_name=provider_name)
        if not primary.success:
            return MultiAgentResult(
                task_id=task.id,
                primary_result=primary,
                review_result=None,
                status=MultiAgentStatus.FAILED,
            )

        review = self._run_review(task, primary)
        if review.status != ReviewStatus.CHANGES_REQUESTED:
            return MultiAgentResult(
                task_id=task.id,
                primary_result=primary,
                review_result=review,
                status=self._overall_status(review),
            )

        revision_task = self._revision_task(task, primary, review)
        try:
            revision = self._orchestrator.dispatch(
                revision_task, provider_name=provider_name
            )
        except Exception:
            revision = TaskResult(
                task_id=revision_task.id,
                success=False,
                error="Primary revision execution failed.",
                provider=provider_name,
            )
        if not revision.success:
            return MultiAgentResult(
                task_id=task.id,
                primary_result=primary,
                review_result=review,
                status=MultiAgentStatus.REVISION_FAILED,
                revision_result=revision,
            )

        final_review = self._run_review(revision_task, revision)
        return MultiAgentResult(
            task_id=task.id,
            primary_result=primary,
            review_result=review,
            status=self._overall_status(final_review),
            revision_result=revision,
            final_review_result=final_review,
        )

    def _run_review(self, task: Task, primary: TaskResult) -> ReviewResult:
        try:
            review = self._reviewer.review(task, primary)
        except Exception:
            review = ReviewResult(
                status=ReviewStatus.FAILED,
                review_text="Reviewer execution failed.",
                reviewer=getattr(self._reviewer, "name", None),
            )
        if not isinstance(review, ReviewResult) or not isinstance(
            review.status, ReviewStatus
        ):
            return ReviewResult(
                status=ReviewStatus.FAILED,
                review_text="Reviewer returned an invalid result.",
                reviewer=getattr(self._reviewer, "name", None),
            )
        return review

    @staticmethod
    def _overall_status(review: ReviewResult) -> MultiAgentStatus:
        return {
            ReviewStatus.APPROVED: MultiAgentStatus.APPROVED,
            ReviewStatus.CHANGES_REQUESTED: MultiAgentStatus.CHANGES_REQUESTED,
            ReviewStatus.FAILED: MultiAgentStatus.REVIEW_FAILED,
        }[review.status]

    @staticmethod
    def _revision_task(
        task: Task, primary: TaskResult, review: ReviewResult
    ) -> Task:
        """Build one revision request while pinning the original task category."""
        context = dict(task.context)
        context["forge_revision"] = {
            "attempt": 1,
            "original_task_description": task.description,
            "previous_primary_response": primary.output,
            "review_feedback": review.review_text,
        }
        return Task(
            id=f"{task.id}-revision-1",
            description=(
                "Revise your previous response to address the review feedback while "
                "still fulfilling the original task.\n\nOriginal task:\n"
                f"{task.description}"
            ),
            context=context,
            priority=task.priority,
            category=classify_task(task),
            parameters=dict(task.parameters),
        )
