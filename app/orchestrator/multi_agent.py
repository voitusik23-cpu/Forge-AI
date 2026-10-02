"""One-primary, one-review execution workflow."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.agents.reviewer import Reviewer
from app.orchestrator.models import Task, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.review import ReviewResult, ReviewStatus


class MultiAgentStatus(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    FAILED = "failed"
    REVIEW_FAILED = "review_failed"


@dataclass
class MultiAgentResult:
    task_id: str
    primary_result: TaskResult
    review_result: Optional[ReviewResult]
    status: MultiAgentStatus


class MultiAgentExecutor:
    """Run a routed primary once, then at most one reviewer once."""

    def __init__(self, orchestrator: Orchestrator, reviewer: Reviewer) -> None:
        self._orchestrator = orchestrator
        self._reviewer = reviewer

    def execute(
        self, task: Task, *, provider_name: Optional[str] = None
    ) -> MultiAgentResult:
        """Execute primary and one review pass without correction or recursion."""
        primary = self._orchestrator.dispatch(task, provider_name=provider_name)
        if not primary.success:
            return MultiAgentResult(
                task_id=task.id,
                primary_result=primary,
                review_result=None,
                status=MultiAgentStatus.FAILED,
            )

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
            review = ReviewResult(
                status=ReviewStatus.FAILED,
                review_text="Reviewer returned an invalid result.",
                reviewer=getattr(self._reviewer, "name", None),
            )

        overall = {
            ReviewStatus.APPROVED: MultiAgentStatus.APPROVED,
            ReviewStatus.CHANGES_REQUESTED: MultiAgentStatus.CHANGES_REQUESTED,
            ReviewStatus.FAILED: MultiAgentStatus.REVIEW_FAILED,
        }[review.status]
        return MultiAgentResult(
            task_id=task.id,
            primary_result=primary,
            review_result=review,
            status=overall,
        )
