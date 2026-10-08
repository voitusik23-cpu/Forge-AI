"""Server-side validation of an execution plan before it may be used.

Validation is the boundary that keeps planning a *declarative* layer: it proves
the plan belongs to this run and task, is structurally sound, is an acyclic and
deterministically ordered DAG, uses only known step types, and carries no
authority- or credential-shaped data.

A plan that fails validation is rejected outright. There is no "best effort"
repair, because silently fixing a plan that tries to smuggle authority is exactly
the failure this boundary exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.planning.plan import (
    ALLOWED_STEP_TYPES,
    ExecutionPlan,
    PlanStep,
    PlanStepType,
    PlanValidationError,
)


@dataclass(frozen=True)
class PlanValidationIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class PlanValidationReport:
    valid: bool
    issues: tuple[PlanValidationIssue, ...] = ()
    order: tuple[str, ...] = ()

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


def _topological_order(steps: tuple[PlanStep, ...]) -> tuple[str, ...]:
    """Deterministic Kahn ordering; raises on a cycle.

    Ties are broken by the plan's own step order, so the same plan always yields
    the same execution order.
    """
    order_index = {step.step_id: index for index, step in enumerate(steps)}
    remaining = {step.step_id: set(step.depends_on) for step in steps}
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            (sid for sid, deps in remaining.items() if not deps),
            key=lambda sid: order_index[sid],
        )
        if not ready:
            raise PlanValidationError("dependency cycle detected")
        for sid in ready:
            ordered.append(sid)
            del remaining[sid]
        for deps in remaining.values():
            deps.difference_update(ready)
    return tuple(ordered)


def validate_plan(
    plan: object,
    *,
    run_id: str,
    task_id: str,
) -> PlanValidationReport:
    """Validate a plan against the run it claims to belong to."""
    issues: list[PlanValidationIssue] = []

    if not isinstance(plan, ExecutionPlan):
        return PlanValidationReport(
            valid=False,
            issues=(
                PlanValidationIssue(
                    "invalid_plan_type",
                    f"expected ExecutionPlan, got {type(plan).__name__}",
                ),
            ),
        )

    # 1. Identity: the plan must belong to this run and task.
    try:
        plan.assert_belongs_to(run_id, task_id)
    except PlanValidationError as exc:
        issues.append(PlanValidationIssue("plan_identity_mismatch", str(exc)))

    # 2. Structure: at least one step, and every step well formed.
    steps = plan.steps
    if not steps:
        issues.append(PlanValidationIssue("empty_plan", "plan has no steps"))

    seen: set[str] = set()
    for step in steps:
        if not isinstance(step, PlanStep):
            issues.append(
                PlanValidationIssue(
                    "invalid_step", f"step is {type(step).__name__}, not PlanStep"
                )
            )
            continue
        if not isinstance(step.step_type, PlanStepType) or (
            step.step_type not in ALLOWED_STEP_TYPES
        ):
            issues.append(
                PlanValidationIssue(
                    "forbidden_step_type",
                    f"step {step.step_id!r} has unknown type {step.step_type!r}",
                )
            )
        if step.step_id in seen:
            issues.append(
                PlanValidationIssue(
                    "duplicate_step_id", f"duplicate step id {step.step_id!r}"
                )
            )
        seen.add(step.step_id)

    # 3. Dependencies: known targets only, no self-dependency.
    for step in steps:
        if not isinstance(step, PlanStep):
            continue
        for dependency in step.depends_on:
            if dependency == step.step_id:
                issues.append(
                    PlanValidationIssue(
                        "self_dependency", f"step {step.step_id!r} depends on itself"
                    )
                )
            elif dependency not in seen:
                issues.append(
                    PlanValidationIssue(
                        "unknown_dependency",
                        f"step {step.step_id!r} depends on unknown {dependency!r}",
                    )
                )

    # 4. Ordering: a DAG must exist and must be deterministic.
    order: tuple[str, ...] = ()
    if not issues:
        try:
            order = _topological_order(steps)
        except PlanValidationError as exc:
            issues.append(PlanValidationIssue("dependency_cycle", str(exc)))
        except KeyError as exc:  # defensive: never mask as valid
            issues.append(
                PlanValidationIssue("unknown_dependency", f"unresolved {exc}")
            )

    # 5. Authority and credential isolation.
    for step in steps:
        if not isinstance(step, PlanStep):
            continue
        for key in step.metadata:
            normalized = str(key).lower().strip().replace("-", "_").replace(" ", "_")
            from app.planning.plan import FORBIDDEN_PLAN_KEYS

            if normalized in FORBIDDEN_PLAN_KEYS:
                issues.append(
                    PlanValidationIssue(
                        "authority_leak",
                        f"step {step.step_id!r} carries forbidden key {key!r}",
                    )
                )
    for key in plan.metadata:
        normalized = str(key).lower().strip().replace("-", "_").replace(" ", "_")
        from app.planning.plan import FORBIDDEN_PLAN_KEYS

        if normalized in FORBIDDEN_PLAN_KEYS:
            issues.append(
                PlanValidationIssue(
                    "authority_leak", f"plan carries forbidden key {key!r}"
                )
            )

    return PlanValidationReport(valid=not issues, issues=tuple(issues), order=order)


def require_valid_plan(
    plan: object,
    *,
    run_id: str,
    task_id: str,
) -> tuple[ExecutionPlan, tuple[str, ...]]:
    """Return the validated plan and its deterministic order, or raise.

    Fail closed: an invalid plan never proceeds with a partial or repaired form.
    """
    report = validate_plan(plan, run_id=run_id, task_id=task_id)
    if not report.valid:
        detail = "; ".join(f"{i.code}: {i.detail}" for i in report.issues)
        raise PlanValidationError(f"invalid execution plan: {detail}")
    assert isinstance(plan, ExecutionPlan)
    return plan, report.order
