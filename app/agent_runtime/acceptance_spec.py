"""Trusted server-side acceptance criteria and their per-run identity.

This module is the criterion-identity boundary. It separates two things:

* ``AcceptanceSpec`` is the **operator's definition**, declared once at
  composition time next to the execution declaration. It names the criterion and
  its deterministic verification expectation. It carries no execution authority:
  a criterion never becomes a command.
* ``RunAcceptanceCriteria`` is the **per-run binding**: the frozen criteria,
  expectations, and criterion identities, bound to one ``run_id`` and one
  ``task_id``. Only the trusted service composition creates it, and it can only
  be bound once, so criteria cannot be swapped after a run starts.

Nothing in this module is reachable from an HTTP request, a task context, a
description, a category, a task id, an LLM, or a decision provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from app.tools.acceptance import AcceptanceCriterion
from app.tools.verification import VerificationExpectation

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AcceptanceSpecError(ValueError):
    """Raised when acceptance criteria cannot be trusted."""


def _validate_criteria(
    criteria: tuple[AcceptanceCriterion, ...],
    expectations: Mapping[str, VerificationExpectation],
) -> None:
    if not criteria:
        # An empty criterion set must never be mistaken for a permissive one.
        raise AcceptanceSpecError("criteria must not be empty")
    if not expectations:
        raise AcceptanceSpecError("expectations must not be empty")

    seen: set[str] = set()
    for criterion in criteria:
        if not isinstance(criterion, AcceptanceCriterion):
            raise AcceptanceSpecError("criteria must be AcceptanceCriterion")
        criterion_id = criterion.criterion_id
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            raise AcceptanceSpecError("criterion_id must be a non-empty string")
        if criterion_id in seen:
            raise AcceptanceSpecError(f"duplicate criterion_id: {criterion_id!r}")
        seen.add(criterion_id)

    for criterion_id, expectation in expectations.items():
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            raise AcceptanceSpecError("expectation keys must be criterion ids")
        if criterion_id not in seen:
            raise AcceptanceSpecError(
                f"expectation for unknown criterion: {criterion_id!r}"
            )
        if not isinstance(expectation, VerificationExpectation):
            raise AcceptanceSpecError("expectations must be VerificationExpectation")
        if not isinstance(expectation.exists, bool):
            raise AcceptanceSpecError("expectation.exists must be a boolean")
        if expectation.sha256 is not None and (
            not expectation.exists
            or not isinstance(expectation.sha256, str)
            or _SHA256.match(expectation.sha256) is None
        ):
            raise AcceptanceSpecError(
                "expectation.sha256 must be a lowercase 64-character digest "
                "and requires exists=True"
            )

    missing = sorted(seen - set(expectations))
    if missing:
        # Every criterion must be checkable, otherwise acceptance could only ever
        # report verification_missing.
        raise AcceptanceSpecError(
            f"criteria without a verification expectation: {missing}"
        )


@dataclass(frozen=True)
class AcceptanceSpec:
    """Operator-declared acceptance definition for one declared execution.

    Declared at composition time, like ``ExecutionDeclaration``. The service
    freezes it into per-run criteria through :meth:`bind`.
    """

    declaration_id: str
    criteria: tuple[AcceptanceCriterion, ...]
    expectations: Mapping[str, VerificationExpectation]
    source: str = "operator_composition"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.declaration_id, str) or not self.declaration_id.strip():
            raise AcceptanceSpecError("declaration_id must be a non-empty string")
        criteria = tuple(self.criteria)
        expectations = dict(self.expectations)
        _validate_criteria(criteria, expectations)
        object.__setattr__(self, "criteria", criteria)
        object.__setattr__(self, "expectations", expectations)

    @property
    def criterion_ids(self) -> tuple[str, ...]:
        return tuple(criterion.criterion_id for criterion in self.criteria)

    def bind(self, *, run_id: str, task_id: str) -> "RunAcceptanceCriteria":
        """Create the frozen per-run criterion identity for this definition."""
        return RunAcceptanceCriteria(
            run_id=run_id,
            task_id=task_id,
            declaration_id=self.declaration_id,
            criteria=self.criteria,
            expectations=self.expectations,
            source=self.source,
        )


@dataclass(frozen=True)
class RunAcceptanceCriteria:
    """Frozen criteria bound to exactly one run and one task.

    The binding is what makes the criterion identity trustworthy: criteria frozen
    for one run cannot be replayed against another, and the acceptance verdict
    recorded for them therefore belongs to the run whose action they verified.
    """

    run_id: str
    task_id: str
    declaration_id: str
    criteria: tuple[AcceptanceCriterion, ...]
    expectations: Mapping[str, VerificationExpectation]
    source: str = "operator_composition"

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise AcceptanceSpecError("run_id must be a non-empty string")
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise AcceptanceSpecError("task_id must be a non-empty string")
        if not isinstance(self.declaration_id, str) or not self.declaration_id.strip():
            raise AcceptanceSpecError("declaration_id must be a non-empty string")
        criteria = tuple(self.criteria)
        expectations = dict(self.expectations)
        _validate_criteria(criteria, expectations)
        object.__setattr__(self, "criteria", criteria)
        object.__setattr__(self, "expectations", expectations)

    @property
    def criterion_ids(self) -> tuple[str, ...]:
        return tuple(criterion.criterion_id for criterion in self.criteria)

    def assert_belongs_to(self, run_id: str, task_id: str) -> None:
        """Fail closed when the frozen criteria do not belong to this run."""
        if self.run_id != run_id:
            raise AcceptanceSpecError(
                f"criteria belong to run {self.run_id!r}, not {run_id!r}"
            )
        if self.task_id != task_id:
            raise AcceptanceSpecError(
                f"criteria belong to task {self.task_id!r}, not {task_id!r}"
            )
