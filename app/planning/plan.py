"""Immutable, non-authoritative execution plan for one production run.

An ``ExecutionPlan`` is a *declarative intention*: it says what the run intends to
observe, modify, verify, and accept, in a deterministic order with explicit
dependencies. It deliberately carries no execution authority:

* a step has no argv, no executable, no shell string, no environment, no working
  directory, no timeout, no network flag, and no tool grant;
* a plan cannot become an ``AuthorizedExecution``, and nothing in this module can
  reach the coordinator, the adapter, a subprocess, or the filesystem;
* a plan is bound to exactly one ``run_id`` and one ``task_id`` and cannot be
  replayed against another run.

Any real action still has to pass the existing trusted chain: decision ->
validation -> server-side composition -> ``RunScope`` -> ``ExecutionCoordinator``
-> ``AuthorizedExecution`` -> ``LocalExecutionAdapter``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class PlanValidationError(ValueError):
    """Raised when a plan cannot be trusted as this run's execution intention."""


class PlanStepType(str, Enum):
    """Closed taxonomy of planning intentions.

    These are intentions, not capabilities: ``MODIFY`` does not grant a write,
    ``VERIFY`` does not grant a command, and ``ACCEPT`` does not grant a verdict.
    """

    OBSERVE = "observe"
    MODIFY = "modify"
    VERIFY = "verify"
    ACCEPT = "accept"


#: The only step types this slice accepts. Anything else is rejected outright
#: rather than ignored, so an unknown intention can never slip through.
ALLOWED_STEP_TYPES = frozenset(PlanStepType)

#: Keys that must never appear anywhere in a plan. They name either authority
#: objects or credential material, and their presence means the plan is not a
#: pure intention.
FORBIDDEN_PLAN_KEYS = frozenset(
    {
        "command", "commands", "argv", "executable", "shell", "cmd",
        "run_scope", "runscope", "authorizedexecution", "authorized_execution",
        "executioncoordinator", "execution_coordinator", "localexecutionadapter",
        "local_execution_adapter", "approval", "approval_policy",
        "approval_resolver", "allowed_execution_commands",
        "allowed_tool_ids", "environment", "env", "timeout",
        "network_access", "capabilities", "profile", "workspace", "workspace_root",
        "credential", "credentials", "secret", "secrets", "token", "password",
        "api_key", "private_key",
    }
)


@dataclass(frozen=True)
class PlanStep:
    """One immutable intention within a plan."""

    step_id: str
    step_type: PlanStepType
    purpose: str
    expected_outcome: str = ""
    depends_on: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise PlanValidationError("step_id must be a non-empty string")
        if not isinstance(self.step_type, PlanStepType):
            raise PlanValidationError("step_type must be a PlanStepType")
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise PlanValidationError("step purpose must be a non-empty string")
        if not isinstance(self.expected_outcome, str):
            raise PlanValidationError("expected_outcome must be a string")

        dependencies = self.depends_on
        if isinstance(dependencies, list):
            dependencies = tuple(dependencies)
        if not isinstance(dependencies, tuple):
            raise PlanValidationError("depends_on must be a tuple of step ids")
        for dependency in dependencies:
            if not isinstance(dependency, str) or not dependency.strip():
                raise PlanValidationError("dependencies must be non-empty step ids")
        if self.step_id in dependencies:
            # A step that depends on itself can never become ready.
            raise PlanValidationError(f"step {self.step_id!r} depends on itself")

        metadata = dict(self.metadata or {})
        _reject_forbidden_keys(metadata, where=f"step {self.step_id!r}")
        object.__setattr__(self, "depends_on", dependencies)
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, object]:
        """Bounded, JSON-safe representation for context and history."""
        return {
            "step_id": self.step_id,
            "step_type": self.step_type.value,
            "purpose": self.purpose[:512],
            "expected_outcome": self.expected_outcome[:512],
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class ExecutionPlan:
    """Immutable, run-bound, non-authoritative execution intention."""

    plan_id: str
    run_id: str
    task_id: str
    steps: tuple[PlanStep, ...]
    source: str = "deterministic_planner"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("plan_id", self.plan_id),
            ("run_id", self.run_id),
            ("task_id", self.task_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise PlanValidationError(f"{name} must be a non-empty string")

        steps = tuple(self.steps)
        if not steps:
            raise PlanValidationError("a plan must contain at least one step")
        for step in steps:
            if not isinstance(step, PlanStep):
                raise PlanValidationError("steps must be PlanStep instances")

        metadata = dict(self.metadata or {})
        _reject_forbidden_keys(metadata, where="plan")
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "metadata", metadata)

    @property
    def step_ids(self) -> tuple[str, ...]:
        return tuple(step.step_id for step in self.steps)

    @property
    def step_types(self) -> tuple[str, ...]:
        return tuple(step.step_type.value for step in self.steps)

    @property
    def dependency_count(self) -> int:
        return sum(len(step.depends_on) for step in self.steps)

    @property
    def fingerprint(self) -> str:
        """Deterministic identity over the plan's own content.

        It covers the run and task binding and every step, so a plan that is
        altered in any meaningful way cannot keep the same fingerprint. It is an
        integrity value, not a capability.
        """
        import hashlib
        import json

        payload = {
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "source": self.source,
            "steps": [
                {
                    "step_id": step.step_id,
                    "step_type": step.step_type.value,
                    "purpose": step.purpose,
                    "expected_outcome": step.expected_outcome,
                    "depends_on": list(step.depends_on),
                }
                for step in self.steps
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def assert_belongs_to(self, run_id: str, task_id: str) -> None:
        """Fail closed when the plan is not this run's plan."""
        if self.run_id != run_id:
            raise PlanValidationError(
                f"plan belongs to run {self.run_id!r}, not {run_id!r}"
            )
        if self.task_id != task_id:
            raise PlanValidationError(
                f"plan belongs to task {self.task_id!r}, not {task_id!r}"
            )

    def bounded_summary(self) -> dict[str, object]:
        """Bounded, sanitized metadata for events and context.

        Counts, types, the fingerprint, and the step purposes only - never a
        command, environment, credential, or raw planner output.
        """
        return {
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "step_count": len(self.steps),
            "step_types": list(self.step_types),
            "dependency_count": self.dependency_count,
            "plan_fingerprint": self.fingerprint,
        }

    def to_context_dict(self) -> dict[str, object]:
        """Bounded representation handed to planning-aware context."""
        return {**self.bounded_summary(), "steps": [s.to_dict() for s in self.steps]}


def _reject_forbidden_keys(mapping: Mapping[str, object], *, where: str) -> None:
    """Refuse a plan that carries authority- or credential-shaped keys."""
    for key in mapping:
        normalized = str(key).lower().strip().replace("-", "_").replace(" ", "_")
        if normalized in FORBIDDEN_PLAN_KEYS:
            raise PlanValidationError(
                f"{where} carries a forbidden key: {key!r}"
            )
