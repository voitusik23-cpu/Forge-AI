"""Bounded, server-side revision decision for one production run.

A revision is only ever triggered by an **objective** failure signalled by the
existing verification and acceptance stages - never because a planner or a
decision provider would simply like another step. This module is the boundary
that decides whether another attempt is allowed, and it holds no execution
authority: a `RevisionDecision` can say "the run should try again against this
bounded goal", but it cannot open a file, run a command, choose an executable,
change the workspace or environment, obtain a `RunScope`, or obtain an
`AuthorizedExecution`.

The budget and the failure classification are server-side values. Nothing here
reads a task request, a plan, an LLM output, or a decision for either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class RevisionError(ValueError):
    """Raised when a revision budget or decision cannot be trusted."""


class RevisionFailureCategory(str, Enum):
    """Closed taxonomy of why a revision might be considered.

    The classification decides actionability, and actionability decides whether
    a revision may run at all.
    """

    VERIFICATION_FAILED = "verification_failed"
    ACCEPTANCE_FAILED = "acceptance_failed"
    EXECUTION_FAILED = "execution_failed"
    VALIDATION_FAILED = "validation_failed"
    NO_PROGRESS = "no_progress"
    NON_ACTIONABLE_FAILURE = "non_actionable_failure"
    SECURITY_FAILURE = "security_failure"


#: Categories a bounded revision may react to. Everything else is terminal: a
#: security or authority failure must end the run rather than be retried, and a
#: validation failure means the trusted state itself is wrong.
ACTIONABLE_CATEGORIES = frozenset(
    {
        RevisionFailureCategory.VERIFICATION_FAILED,
        RevisionFailureCategory.ACCEPTANCE_FAILED,
        # An ordinary, expected implementation failure is exactly what a bounded
        # revision exists to react to.
        RevisionFailureCategory.EXECUTION_FAILED,
    }
)

#: Categories that always terminate the run. A revision loop must never attempt
#: to work around a security or authority boundary.
TERMINAL_CATEGORIES = frozenset(
    {
        RevisionFailureCategory.SECURITY_FAILURE,
        RevisionFailureCategory.VALIDATION_FAILED,
        RevisionFailureCategory.NON_ACTIONABLE_FAILURE,
        RevisionFailureCategory.NO_PROGRESS,
    }
)


@dataclass(frozen=True)
class RevisionBudget:
    """Server-side, immutable bound on how many revisions a run may perform."""

    max_revisions: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.max_revisions, bool) or not isinstance(
            self.max_revisions, int
        ):
            raise RevisionError("max_revisions must be an integer")
        if self.max_revisions < 0:
            raise RevisionError("max_revisions must not be negative")

    def remaining(self, revision_number: int) -> int:
        return max(0, self.max_revisions - revision_number)

    def allows(self, revision_number: int) -> bool:
        """A revision numbered `revision_number` is the (n+1)-th attempt."""
        if isinstance(revision_number, bool) or not isinstance(revision_number, int):
            raise RevisionError("revision_number must be an integer")
        if revision_number < 0:
            raise RevisionError("revision_number must not be negative")
        return revision_number < self.max_revisions


@dataclass(frozen=True)
class RevisionDecision:
    """Immutable, non-authoritative decision about one revision opportunity.

    It records whether the run may revise, why, and the bounded goal a replan
    should target. It cannot execute anything and cannot alter acceptance
    criteria, the run scope, or the workspace.
    """

    run_id: str
    task_id: str
    revision_number: int
    failure_category: RevisionFailureCategory
    reason: str
    revision_allowed: bool
    plan_id: str = ""
    plan_fingerprint: str = ""
    revision_goal: str = ""
    actionable: bool = False
    budget_remaining: int = 0
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("task_id", self.task_id),
            ("reason", self.reason),
        ):
            if not isinstance(value, str) or not value.strip():
                raise RevisionError(f"{name} must be a non-empty string")
        if isinstance(self.revision_number, bool) or not isinstance(
            self.revision_number, int
        ):
            raise RevisionError("revision_number must be an integer")
        if self.revision_number < 0:
            raise RevisionError("revision_number must not be negative")
        if not isinstance(self.failure_category, RevisionFailureCategory):
            raise RevisionError("failure_category must be a RevisionFailureCategory")
        if not isinstance(self.revision_allowed, bool):
            raise RevisionError("revision_allowed must be a boolean")
        if isinstance(self.budget_remaining, bool) or not isinstance(
            self.budget_remaining, int
        ):
            raise RevisionError("budget_remaining must be an integer")
        if self.budget_remaining < 0:
            raise RevisionError("budget_remaining must not be negative")
        if self.revision_allowed and not self.actionable:
            # Allowing a revision for a non-actionable failure would be a blind
            # retry against a boundary the run must not cross.
            raise RevisionError(
                "a revision may only be allowed for an actionable failure"
            )
        if self.revision_allowed and not self.revision_goal.strip():
            raise RevisionError("an allowed revision must carry a bounded goal")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def terminal(self) -> bool:
        return not self.revision_allowed

    def assert_belongs_to(self, run_id: str, task_id: str) -> None:
        if self.run_id != run_id:
            raise RevisionError(
                f"revision decision belongs to run {self.run_id!r}, not {run_id!r}"
            )
        if self.task_id != task_id:
            raise RevisionError(
                f"revision decision belongs to task {self.task_id!r}, not {task_id!r}"
            )

    def bounded_summary(self) -> dict[str, object]:
        """Sanitized metadata for events: identities, category, counts only."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "revision_number": self.revision_number,
            "revision_allowed": self.revision_allowed,
            "failure_category": self.failure_category.value,
            "actionable": self.actionable,
            "budget_remaining": self.budget_remaining,
            "plan_id": self.plan_id,
            "plan_fingerprint": self.plan_fingerprint,
            "reason": self.reason[:256],
        }
