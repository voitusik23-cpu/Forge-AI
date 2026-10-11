"""Verification Planning: choose checks from changes and the deterministic graph.

WHAT THIS MODULE IS
===================

A planner that turns a change-impact report and the recorded dependency data into
a concrete list of checks, each carrying an explicit state. It chooses:

* the criteria the operator already declared, from
  ``app.agent_runtime/acceptance_spec.py``;
* the topological dependents the scanner already derived, from
  ``UnderstandingSnapshot.topology`` -- **no model is asked to guess a
  dependency** and no dependency is inferred from a file's name alone;
* the existing test files whose *path* relates to a changed path, reported as a
  weak relation rather than as coverage.

THE SIX STATES, AND WHY THEY ARE SIX
====================================

``NOT_RUN``   the check exists and no observation of it has been made;
``RUNNING``   an attempt is in flight and has produced no verdict;
``PASSED``    an observation settled it and it held;
``FAILED``    an observation settled it and it did not hold;
``SKIPPED``   the check was deliberately not attempted, and the reason is named;
``UNKNOWN``   the check could not be evaluated at all, so nothing is proven.

``PASSED`` is the only state that counts as a pass. ``SKIPPED`` and ``UNKNOWN``
are never presented as passed, and ``ERROR`` from the step-graph vocabulary is
mapped onto ``UNKNOWN`` -- deliberately *not* onto ``FAILED``, because "could not
be evaluated" and "was evaluated and failed" are different observations.

COVERAGE GAPS ARE NAMED
=======================

A changed path that no declared expectation covers becomes a check with state
``UNKNOWN`` and ``uncovered_reason = "no_declared_expectation_covers_this_path"``,
listed in ``coverage_gaps``. A requirement with no criterion at all is listed in
``requirements_without_checks``. A gap is a gap: it is reported, never rounded up
to a pass and never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

__all__ = [
    "CheckState",
    "CheckCriticality",
    "VerificationCheck",
    "VerificationPlan",
    "VerificationPlanner",
    "CRITICALITY_ORDER",
]


class CheckState(str, Enum):
    """The state of one check. ``PASSED`` is the only passing value."""

    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_pass(self) -> bool:
        return self is CheckState.PASSED

    @property
    def is_proven(self) -> bool:
        """Whether this state settles the check either way.

        ``FAILED`` settles it (negatively) and ``PASSED`` settles it
        (positively); nothing else settles it at all.
        """

        return self in {CheckState.PASSED, CheckState.FAILED}


class CheckCriticality(str, Enum):
    """How much a check matters, derived from the change kind, never asserted.

    ``CRITICAL`` -- the change removed something a declared expectation still
    names, or a deleted path carried structural facts;
    ``HIGH``     -- a modified path that carried structural facts, or an ambiguous
    move, which is a change whose destination is unknown;
    ``NORMAL``   -- an added path, or a modified path with no extracted fact;
    ``LOW``      -- a change to a test file or a documentation file.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


CRITICALITY_ORDER: tuple[CheckCriticality, ...] = (
    CheckCriticality.CRITICAL,
    CheckCriticality.HIGH,
    CheckCriticality.NORMAL,
    CheckCriticality.LOW,
)

_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")


@dataclass(frozen=True)
class VerificationCheck:
    """One planned check: what it is, why it was chosen, and what it observed."""

    check_id: str
    kind: str
    description: str
    state: CheckState
    criticality: CheckCriticality = CheckCriticality.NORMAL
    #: The changed paths that drove this check being planned.
    driven_by: tuple[str, ...] = ()
    #: The requirements this check relates to.
    requirements: tuple[str, ...] = ()
    #: Existing tests whose path relates to the changed path.
    related_tests: tuple[str, ...] = ()
    #: The concrete observation the state rests on. Empty for a non-settled state.
    evidence: tuple[str, ...] = ()
    #: Why the state is what it is. Always present.
    reason: str = ""
    #: Set when the state means "no coverage", naming the gap exactly.
    uncovered_reason: str = ""
    #: The deterministic dependency data that reached this check.
    via_dependency: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind,
            "description": self.description,
            "state": self.state.value,
            "is_pass": self.state.is_pass,
            "is_proven": self.state.is_proven,
            "criticality": self.criticality.value,
            "driven_by": list(self.driven_by),
            "requirements": list(self.requirements),
            "related_tests": list(self.related_tests),
            "evidence": list(self.evidence),
            "reason": self.reason,
            "uncovered_reason": self.uncovered_reason,
            "via_dependency": list(self.via_dependency),
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class VerificationPlan:
    """The whole plan: checks, gaps, and the honest counts."""

    checks: tuple[VerificationCheck, ...] = ()
    coverage_gaps: tuple[str, ...] = ()
    requirements_without_checks: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def state_counts(self) -> dict[str, int]:
        counts = {state.value: 0 for state in CheckState}
        for check in self.checks:
            counts[check.state.value] += 1
        return counts

    @property
    def passed_count(self) -> int:
        return sum(1 for check in self.checks if check.state.is_pass)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [check.to_dict() for check in self.checks],
            "state_counts": self.state_counts,
            "passed_count": self.passed_count,
            "coverage_gaps": list(self.coverage_gaps),
            "requirements_without_checks": list(self.requirements_without_checks),
            "notes": list(self.notes),
            "states": [state.value for state in CheckState],
            "only_passed_counts_as_a_pass": True,
        }


def _verification_state(
    *,
    record: Any,
    graph_status: str,
) -> tuple[CheckState, tuple[str, ...], str]:
    """Map one step record onto one check state.

    THE ORDER OF THE RULES IS THE MEANING, and an earlier revision had it wrong. It
    tested the declared verification status first, so a step recorded as ``UNKNOWN``
    -- a step whose outcome is *unprovable* -- came back as ``NOT_RUN``, which reads
    as "nothing has happened yet". Those are different facts and the second is
    weaker than the truth. The rules are therefore ordered by what the attempt
    itself establishes:

    * ``PASSED`` / ``FAILED`` are real observations and are reported as themselves,
      whatever the step status;
    * ``ERROR`` is "the check could not be evaluated" and becomes ``UNKNOWN``,
      deliberately **not** ``FAILED``;
    * then the STEP status decides:
      - ``RUNNING``  -> ``RUNNING`` (in flight, no verdict);
      - ``UNKNOWN``  -> ``UNKNOWN`` (the outcome is unprovable, whatever the check
        says -- this is the case the earlier ordering got wrong);
      - ``BLOCKED`` / ``CANCELLED`` -> ``SKIPPED`` (deliberately not attempted);
      - ``FAILED``   -> ``FAILED`` (the step failed);
      - ``SUCCEEDED`` with no evaluated check -> ``UNKNOWN``: a successful command
        is **not** a passing check, because no check was ever observed;
      - anything else -> ``NOT_RUN``.

    ``NOT_RUN`` therefore means exactly "this check exists and no observation of it
    has been made". ``GRAPH_INDETERMINATE`` (the whole attempt is unprovable)
    likewise yields ``UNKNOWN``.
    """

    if record is None:
        return (
            CheckState.NOT_RUN,
            (),
            "no attempt record exists for this check",
        )
    raw = getattr(getattr(record, "verification_status", None), "value", "")
    step_status = getattr(getattr(record, "status", None), "value", "")
    evidence_codes = ",".join(str(code) for code in getattr(record, "evidence", ()))

    if raw == "PASSED":
        return (
            CheckState.PASSED,
            (
                f"verification_observed={getattr(record, 'verification_observed', '')}",
                f"execution_exit_code={getattr(record, 'execution_exit_code', None)}",
            ),
            "the recorded observation settled this check and it held",
        )
    if raw == "FAILED":
        return (
            CheckState.FAILED,
            (
                f"verification_observed={getattr(record, 'verification_observed', '')}",
                f"execution_exit_code={getattr(record, 'execution_exit_code', None)}",
                f"evidence={evidence_codes}",
            ),
            "the recorded observation settled this check and it did not hold",
        )
    if raw == "ERROR":
        return (
            CheckState.UNKNOWN,
            (
                f"verification_error={getattr(record, 'verification_error', '')}",
                f"evidence={evidence_codes}",
            ),
            "the declared check could not be evaluated, so nothing is proven either way",
        )
    if step_status == "RUNNING":
        return (
            CheckState.RUNNING,
            (f"step_status={step_status}",),
            "an attempt is in flight and has produced no verdict",
        )
    if step_status == "UNKNOWN" or graph_status == "INDETERMINATE":
        return (
            CheckState.UNKNOWN,
            (
                f"step_status={step_status or 'UNKNOWN'}",
                f"graph_status={graph_status or 'UNKNOWN'}",
            ),
            (
                "the recorded outcome is unprovable, so this check is neither passed "
                "nor failed and is not reported as not-yet-run"
            ),
        )
    if step_status in {"CANCELLED", "BLOCKED"}:
        return (
            CheckState.SKIPPED,
            (f"step_status={step_status}",),
            {
                "CANCELLED": "the attempt was cancelled before this check was reached",
                "BLOCKED": "a dependency did not succeed, so this check was not attempted",
            }[step_status],
        )
    if step_status == "FAILED":
        return (
            CheckState.FAILED,
            (
                f"step_status={step_status}",
                f"verification_status={raw or 'NOT_DECLARED'}",
                f"evidence={evidence_codes}",
            ),
            (
                "the step is recorded FAILED and no declared check overrode that, so "
                "this check did not establish a pass"
            ),
        )
    if step_status == "SUCCEEDED":
        return (
            CheckState.UNKNOWN,
            (
                f"step_status={step_status}",
                f"verification_status={raw or 'NOT_DECLARED'}",
            ),
            (
                "the step succeeded but no declared check was observed to hold, so its "
                "result is not a proven check: a successful command is not a passing "
                "check"
            ),
        )
    return (
        CheckState.NOT_RUN,
        (f"step_status={step_status}", f"verification_status={raw or 'NOT_DECLARED'}"),
        "a check exists and no observation of it has been made",
    )


class VerificationPlanner:
    """Plan checks for a change set using the existing deterministic data."""

    def __init__(
        self,
        *,
        acceptance_specs: Mapping[str, Any] | None = None,
        declarations: Mapping[str, Any] | None = None,
        step_records: Sequence[Any] = (),
        declared_plan: Sequence[Mapping[str, Any]] = (),
        graph_status: str = "",
        baseline_present: bool = False,
    ) -> None:
        self._specs = dict(acceptance_specs or {})
        self._declarations = dict(declarations or {})
        self._records = tuple(step_records)
        self._declared_plan = tuple(declared_plan)
        self._graph_status = graph_status
        self._baseline_present = bool(baseline_present)

    # -- helpers --------------------------------------------------------
    def _record_for_declaration(self, declaration_id: str) -> Any | None:
        for entry in self._declared_plan:
            action = entry.get("action")
            if not isinstance(action, Mapping):
                continue
            if str(action.get("declaration_id", "")) != declaration_id:
                continue
            step_id = str(entry.get("step_id", ""))
            for record in self._records:
                if str(getattr(record, "step_id", "")) == step_id:
                    return record
        return None

    @staticmethod
    def _criticality(impact: Mapping[str, Any]) -> CheckCriticality:
        kind = str(impact.get("kind", ""))
        path = str(impact.get("path", ""))
        if any(path.lower().endswith(suffix) for suffix in _DOC_SUFFIXES):
            return CheckCriticality.LOW
        if "/tests/" in f"/{path}" or path.startswith("tests/") or path.rsplit(
            "/", 1
        )[-1].startswith("test_"):
            return CheckCriticality.LOW
        has_facts = bool(impact.get("affected_fact_ids"))
        if kind == "DELETED":
            return CheckCriticality.CRITICAL if has_facts else CheckCriticality.HIGH
        if impact.get("ambiguous_move"):
            return CheckCriticality.HIGH
        if kind == "MODIFIED":
            return CheckCriticality.HIGH if has_facts else CheckCriticality.NORMAL
        return CheckCriticality.NORMAL

    # -- entry point ----------------------------------------------------
    def plan(self, impact: Mapping[str, Any] | None) -> VerificationPlan:
        """Build the plan. ``impact`` is the ``ChangeImpactReport.to_dict()`` shape.

        It may be ``None`` (no change report could be produced) and the plan is
        still built: the declared criteria are still listed, because they exist
        regardless, and the absence of a change report is itself stated.
        """

        findings: list[Mapping[str, Any]] = []
        if isinstance(impact, Mapping):
            findings = [
                item for item in impact.get("findings", ()) if isinstance(item, Mapping)
            ]

        changes_by_path: dict[str, Mapping[str, Any]] = {
            str(item.get("path", "")): item for item in findings
        }
        covered_paths: set[str] = set()

        checks: list[VerificationCheck] = []
        gaps: list[str] = []
        requirements_without_checks: list[str] = []

        for declaration_id in sorted(self._declarations):
            spec = self._specs.get(declaration_id)
            criteria = list(getattr(spec, "criteria", ()) or ()) if spec else []
            expectations = dict(getattr(spec, "expectations", {}) or {}) if spec else {}
            if not criteria:
                requirements_without_checks.append(declaration_id)
                gaps.append(
                    f"requirement {declaration_id!r} has no declared criterion, so no "
                    "check can be planned for it and none is reported as passed"
                )
                continue
            for criterion in criteria:
                criterion_id = str(criterion.criterion_id)
                expectation = expectations.get(criterion.criterion_id)
                expectation_path = (
                    str(getattr(expectation, "relative_path", ""))
                    if expectation is not None
                    else ""
                )
                record = self._record_for_declaration(declaration_id)
                state, evidence, reason = _verification_state(
                    record=record, graph_status=self._graph_status
                )
                if expectation is None:
                    state = CheckState.UNKNOWN
                    evidence = ()
                    reason = (
                        "the criterion declares no expectation, so no observation "
                        "could ever settle it"
                    )
                impact_item = changes_by_path.get(expectation_path)
                driven_by: tuple[str, ...] = ()
                covered = False
                if impact_item is not None:
                    driven_by = (expectation_path,)
                    covered_paths.add(expectation_path)
                    covered = True
                checks.append(
                    VerificationCheck(
                        check_id=f"criterion:{declaration_id}:{criterion_id}",
                        kind="DECLARED_EXPECTATION",
                        description=criterion.description,
                        state=state,
                        criticality=(
                            self._criticality(impact_item)
                            if impact_item is not None
                            else CheckCriticality.NORMAL
                        ),
                        driven_by=driven_by,
                        requirements=(str(getattr(criterion, "requirement_id", "")),),
                        evidence=evidence,
                        reason=reason,
                        via_dependency=(
                            tuple(
                                str(link.get("ref", ""))
                                for link in (impact_item or {}).get("dependent_nodes", ())
                                if isinstance(link, Mapping)
                            )
                            if impact_item is not None
                            else ()
                        ),
                        extra={
                            "expectation_path": expectation_path,
                            "must_exist": bool(
                                getattr(expectation, "exists", False)
                            )
                            if expectation is not None
                            else False,
                            "planned_because": (
                                "a changed path matches this criterion's declared "
                                "expectation"
                                if covered
                                else "declared by the operator; no change touches it"
                            ),
                            "related_tests": list(
                                (impact_item or {}).get("affected_tests", ())
                            ),
                        },
                    )
                )
                if state is CheckState.SKIPPED:
                    gaps.append(
                        f"check {checks[-1].check_id} is SKIPPED ({reason}); it is not a pass"
                    )
                elif state is CheckState.UNKNOWN and expectation is None:
                    gaps.append(
                        f"check {checks[-1].check_id} is UNKNOWN: the criterion has no "
                        "expectation, so it cannot be evaluated"
                    )

        # Any changed path no declared expectation names is an explicit gap.
        for path in sorted(changes_by_path):
            if path in covered_paths:
                continue
            impact_item = changes_by_path[path]
            state = CheckState.UNKNOWN
            uncovered = "no_declared_expectation_covers_this_path"
            checks.append(
                VerificationCheck(
                    check_id=f"changed-path:{path}",
                    kind="UNCOVERED_CHANGE",
                    description=(
                        f"no declared acceptance expectation names {path}; nothing can "
                        "check it"
                    ),
                    state=state,
                    criticality=self._criticality(impact_item),
                    driven_by=(path,),
                    requirements=(),
                    related_tests=tuple(
                        str(item.get("path", ""))
                        for item in impact_item.get("affected_tests", ())
                        if isinstance(item, Mapping)
                    ),
                    reason=(
                        "the path differs from the baseline and no declared expectation "
                        "covers it, so its impact is unverified rather than verified"
                    ),
                    uncovered_reason=uncovered,
                    via_dependency=tuple(
                        str(link.get("ref", ""))
                        for link in impact_item.get("dependent_nodes", ())
                        if isinstance(link, Mapping)
                    ),
                    extra={
                        "changed_kind": impact_item.get("kind", ""),
                        "classification": impact_item.get("classification", ""),
                        "basis": impact_item.get("basis", []),
                    },
                )
            )
            gaps.append(
                f"changed path {path!r} has no declared expectation "
                f"({uncovered}); it is reported UNKNOWN and NOT as passed"
            )

        if not self._baseline_present:
            checks.append(
                VerificationCheck(
                    check_id="baseline:absent",
                    kind="BASELINE",
                    description=(
                        "no baseline snapshot is recorded, so affected components "
                        "cannot be measured"
                    ),
                    state=CheckState.UNKNOWN,
                    criticality=CheckCriticality.HIGH,
                    reason=(
                        "without a baseline there is no reference point, so a change "
                        "set is unmeasurable; this is not evidence that nothing changed"
                    ),
                    uncovered_reason="no_baseline_snapshot_recorded",
                )
            )
            gaps.append(
                "no baseline snapshot is recorded; change coverage is unmeasurable"
            )

        checks.sort(key=lambda check: (check.criticality.value, check.check_id))
        notes = [
            "checks are chosen from the operator's declared expectations, the "
            "scanner's own topology, and the recorded step results; no model was "
            "asked to guess a dependency",
            "PASSED is the only state that counts as a pass: SKIPPED and UNKNOWN are "
            "reported as themselves",
        ]
        if impact is None:
            notes.append(
                "no change-impact report was available, so the plan lists the declared "
                "checks and states that coverage of changes is unknown"
            )
        return VerificationPlan(
            checks=tuple(checks),
            coverage_gaps=tuple(gaps),
            requirements_without_checks=tuple(requirements_without_checks),
            notes=tuple(notes),
        )
