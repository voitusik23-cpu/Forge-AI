"""Provider-neutral reviewer contract and Dispatcher-backed implementation."""

import re
from typing import Protocol

from app.orchestrator.classification import classify_task
from app.orchestrator.models import Task, TaskCategory, TaskResult
from app.orchestrator.review import ReviewResult, ReviewStatus
from app.agents.providers.registry import ProviderRegistry
from app.orchestrator.orchestrator import Orchestrator


class Reviewer(Protocol):
    """Evaluate a primary result without changing files or application state."""

    name: str

    def review(self, task: Task, primary_result: TaskResult) -> ReviewResult:
        """Return one review result for the original task and primary output."""


class ProviderReviewer:
    """Send a structured review task through the existing Dispatcher."""

    name = "provider-reviewer"
    _DECISION = re.compile(r"^(APPROVED|CHANGES_REQUESTED)\b\s*[:\-]?\s*(.*)$", re.I)

    def __init__(
        self, orchestrator: Orchestrator, provider_registry: ProviderRegistry
    ) -> None:
        self._orchestrator = orchestrator
        self._provider_registry = provider_registry

    def review(self, task: Task, primary_result: TaskResult) -> ReviewResult:
        """Ask the automatically routed REVIEW task for a single decision."""
        revision_context = task.context.get("forge_revision", {})
        is_revision = isinstance(revision_context, dict) and bool(revision_context)
        primary_provider = primary_result.provider or "unknown"
        primary_model = primary_result.model_name
        if primary_model is None and primary_provider != "unknown":
            try:
                primary_model = self._provider_registry.get(primary_provider).model_name
            except LookupError:
                primary_model = "unknown"

        review_instruction = (
            "Evaluate the primary response for correctness, completeness, obvious "
            "errors, and whether it addresses the task."
        )
        if is_revision and revision_context:
            review_instruction += (
                " This is the single permitted revision; check whether it addresses "
                "the previous review feedback and still fulfills the task."
            )
        review_instruction += (
            " Return exactly one first line: APPROVED or CHANGES_REQUESTED. "
            "Follow it with concise findings."
        )
        review_task = Task(
            id=f"{task.id}-review",
            description=review_instruction,
            context={
                "task_description": (
                    revision_context.get("original_task_description", task.description)
                    if is_revision
                    else task.description
                ),
                "task_category": classify_task(task).value,
                "primary_provider": primary_provider,
                "primary_model": primary_model or "unknown",
                "primary_response": primary_result.output,
                "original_task_context": task.context,
                "prior_review_feedback": (
                    revision_context.get("review_feedback")
                    if is_revision
                    else None
                ),
                "previous_primary_response": (
                    revision_context.get("previous_primary_response")
                    if is_revision
                    else None
                ),
            },
            priority=task.priority,
            category=TaskCategory.REVIEW,
        )
        result = self._orchestrator.dispatch(review_task)
        return self._to_review_result(result)

    def _to_review_result(self, result: TaskResult) -> ReviewResult:
        if not result.success:
            return ReviewResult(
                status=ReviewStatus.FAILED,
                review_text=result.error or "Reviewer execution failed.",
                reviewer=self.name,
                provider=result.provider,
                model_name=result.model_name,
                usage=result.usage,
            )

        lines = result.output.strip().splitlines()
        match = self._DECISION.match(lines[0].strip()) if lines else None
        if match is None:
            return ReviewResult(
                status=ReviewStatus.FAILED,
                review_text="Reviewer did not return the required decision format.",
                reviewer=self.name,
                provider=result.provider,
                model_name=result.model_name,
                usage=result.usage,
            )

        status = ReviewStatus(match.group(1).lower())
        first_line_findings = match.group(2).strip()
        remaining_findings = "\n".join(lines[1:]).strip()
        review_text = "\n".join(
            part for part in (first_line_findings, remaining_findings) if part
        )
        return ReviewResult(
            status=status,
            review_text=review_text,
            reviewer=self.name,
            provider=result.provider,
            model_name=result.model_name,
            usage=result.usage,
        )
