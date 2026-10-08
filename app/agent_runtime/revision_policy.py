"""Server-side eligibility for one bounded revision.

Eligibility is deliberately objective and conservative:

    revision_allowed = verification_failed
                       AND revision_budget_remaining
                       AND failure_is_actionable
                       AND a genuinely new plan is available

Nothing about that decision is subjective, and nothing in it is supplied by a
task request, a plan, an LLM, or a decision provider. A security, authority,
validation, or no-progress failure terminates the run rather than being retried.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent_runtime.revision_decision import (
    ACTIONABLE_CATEGORIES,
    TERMINAL_CATEGORIES,
    RevisionBudget,
    RevisionDecision,
    RevisionError,
    RevisionFailureCategory,
)

#: Failure categories produced by the execution stage that are security- or
#: authority-shaped. They must never become a revision trigger.
_SECURITY_OUTCOMES = frozenset(
    {
        "PERMISSION_DENIED",
        "POLICY_DENIED",
        "APPROVAL_REJECTED",
    }
)

#: Execution outcomes that are ordinary, actionable implementation failures.
_ACTIONABLE_OUTCOMES = frozenset(
    {
        "EXECUTION_FAILURE",
        "EXECUTION_TIMEOUT",
        "EXECUTION_ERROR",
    }
)


@dataclass(frozen=True)
class RevisionEligibility:
    """Inputs the eligibility check reads. All of them are server-side."""

    run_id: str
    task_id: str
    revision_number: int
    verification_attempted: bool
    acceptance_passed: bool
    execution_outcome: str = ""
    plan_id: str = ""
    plan_fingerprint: str = ""
    plan_structure: str = ""
    seen_plan_fingerprints: tuple[str, ...] = ()
    seen_plan_structures: tuple[str, ...] = ()


def plan_structure_signature(steps: object) -> str:
    """Deterministic signature of a plan's *shape*.

    Two plans can carry different identities while describing exactly the same
    ordered intentions. Repeating that shape against the same failure is a blind
    retry, so the signature - not just the fingerprint - is what no-progress
    protection compares.
    """
    if not steps:
        return ""
    parts: list[str] = []
    for step in steps:
        step_type = getattr(step, "step_type", None)
        type_value = getattr(step_type, "value", step_type)
        purposes = getattr(step, "purpose", "")
        depends = tuple(getattr(step, "depends_on", ()) or ())
        parts.append(f"{type_value}|{purposes}|{','.join(depends)}")
    return " || ".join(parts)


def classify_failure(
    *,
    verification_attempted: bool,
    acceptance_passed: bool,
    execution_outcome: str,
) -> RevisionFailureCategory:
    """Classify why a run is not accepted, using only trusted outcomes."""
    if acceptance_passed:
        # An accepted run never becomes a revision candidate.
        return RevisionFailureCategory.NON_ACTIONABLE_FAILURE
    if execution_outcome in _SECURITY_OUTCOMES:
        return RevisionFailureCategory.SECURITY_FAILURE
    if execution_outcome in _ACTIONABLE_OUTCOMES:
        return RevisionFailureCategory.EXECUTION_FAILED
    if not verification_attempted:
        # Revision requires a real verification outcome; without one there is
        # nothing objective to react to.
        return RevisionFailureCategory.NON_ACTIONABLE_FAILURE
    if not acceptance_passed:
        return RevisionFailureCategory.ACCEPTANCE_FAILED
    return RevisionFailureCategory.NON_ACTIONABLE_FAILURE


def evaluate_revision(
    *,
    eligibility: RevisionEligibility,
    budget: RevisionBudget,
    base_goal: str,
) -> RevisionDecision:
    """Decide whether another bounded revision may run, and against which goal.

    The returned decision is always well formed; it is simply not allowed when
    any condition fails, and its `reason` records exactly which one did.
    """
    if not isinstance(budget, RevisionBudget):
        raise RevisionError("budget must be a RevisionBudget")

    number = eligibility.revision_number
    remaining = budget.remaining(number)
    category = classify_failure(
        verification_attempted=eligibility.verification_attempted,
        acceptance_passed=eligibility.acceptance_passed,
        execution_outcome=eligibility.execution_outcome,
    )

    # No-progress is decided where the replanned intention is available: the
    # current plan cannot be compared against itself, so eligibility only checks
    # the objective failure trigger and the budget.
    plan_fingerprint = eligibility.plan_fingerprint

    actionable = category in ACTIONABLE_CATEGORIES and category not in TERMINAL_CATEGORIES

    allowed = True
    reason = "actionable_failure_within_budget"
    if eligibility.acceptance_passed:
        allowed, reason = False, "already_accepted"
    elif category in TERMINAL_CATEGORIES:
        allowed, reason = False, f"terminal_failure:{category.value}"
    elif not actionable:
        allowed, reason = False, "failure_not_actionable"
    elif not budget.allows(number):
        allowed, reason = False, "revision_budget_exhausted"
    elif not plan_fingerprint:
        # A revision must be able to point at the plan it replaces.
        allowed, reason = False, "no_current_plan"
    elif not isinstance(base_goal, str) or not base_goal.strip():
        allowed, reason = False, "no_revision_goal_source"

    goal = ""
    if allowed:
        # The bounded revision goal names the objective failure, so the replanned
        # identity differs and the planner is not asked the same question twice.
        goal = (
            f"{base_goal.strip()} "
            f"[revision {number + 1}: address {category.value}]"
        )

    return RevisionDecision(
        run_id=eligibility.run_id,
        task_id=eligibility.task_id,
        revision_number=number,
        failure_category=category,
        reason=reason,
        revision_allowed=allowed,
        plan_id=eligibility.plan_id,
        plan_fingerprint=plan_fingerprint,
        revision_goal=goal,
        actionable=actionable,
        budget_remaining=remaining,
    )
