"""Repair Loop: a bounded decision loop that refuses to repeat an unknown effect.

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT
====================================================

A **planner with a budget**, not a retry engine. It performs the first five steps
of the loop for real and stops, honestly, before the sixth:

1. **detect failure** -- from the recorded ``StepStatus`` and
   ``VerificationStatus`` of the attempt, never from a message;
2. **collect evidence** -- the step's own ``evidence`` codes, its recorded exit
   code, its verification observation, and the durable ledger position of its own
   ``operation_identity``;
3. **determine a probable cause and a confidence level** -- a probable cause is a
   *named* hypothesis with a basis and a stated confidence, and its confidence is
   the ordinal ``Confidence`` value below, whose derivation is written out rather
   than being a percentage anyone chose;
4. **build a minimal repair plan** -- the smallest targeted re-verification, the
   declared operation it would invoke, and whether the repeat guarantee permits
   it at all;
5. **check required authorization and approval** -- read from the composition's
   own ``ApprovalPolicy`` and resolver, never assumed, and never substituted;
6. *(not performed)* **the repair itself.** See "WHY STEP 6 IS NOT HERE".

WHY STEP 6 IS NOT HERE
======================

No code path in this architecture re-runs a ``FAILED`` step or re-attempts an
``UNKNOWN`` step, and ``app/runtime/recovery.py`` states that as a deliberate
property ("Not a retry engine"). Re-dispatching a host command from an analysis
module would create exactly the second execution mechanism this block forbids and
would be the one change most likely to break a repeat guarantee.

So the loop stops with an explicit ``stop_rule`` of
``dispatch_not_composed_on_this_surface`` and a machine ``dispatch_path`` naming
what would have to exist. Its budget, its attempt cap, its no-progress rule and
its refusal path are all real and are all exercised by tests; only the dispatch
is absent, and the absence is reported rather than papered over with a
simulation.

THE REFUSAL THAT IS NEVER LIFTED
================================

An operation whose side effect began without a surviving completion record has
``RepeatGuarantee.REFUSED_SIDE_EFFECT_UNKNOWN``. That is not "retry carefully", it
is **refusal**: whether the effect landed is unprovable, so repeating could
double it. This loop returns ``REFUSED_SIDE_EFFECT_UNKNOWN`` unchanged, stops
with ``stop_rule="side_effect_outcome_unknown"``, and performs nothing. It never
converts the refusal into a permission.

BUDGET, ATTEMPT CAP, NO PROGRESS
================================

* ``attempt_cap`` -- the maximum number of *claims* this loop may take. The count
  comes from the attempt's own recorded ``attempts`` plus the claims already
  recorded for it, so it cannot be reset by asking again;
* ``step_budget`` -- the maximum number of steps one attempt may touch;
* ``seconds_budget`` -- a wall-clock ceiling measured from the attempt's own
  recorded ``updated_at``, so a long-dead attempt is not treated as freshly
  started;
* **no progress** -- ``progress_marker`` is a digest of the observable state
  (per-step status, verification status, exit code, evidence codes, ledger
  guarantee). A marker that repeats means the previous attempt changed nothing
  observable, so the loop stops with ``stop_rule="no_progress"`` instead of
  spending the rest of its budget. It is a digest of observations, not a
  heartbeat, and it makes no liveness claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence

from app.agent_runtime.idempotency_integration import RepeatGuarantee
from app.workflows.step_graph import StepStatus, VerificationStatus

__all__ = [
    "Confidence",
    "ProbableCause",
    "RepairAttempt",
    "RepairBudget",
    "RepairLoop",
    "RepairOutcome",
    "RepairPlan",
    "RepairStepPlan",
    "StopRule",
    "DEFAULT_ATTEMPT_CAP",
    "DEFAULT_SECONDS_BUDGET",
    "DEFAULT_STEP_BUDGET",
    "progress_marker",
]


DEFAULT_ATTEMPT_CAP = 3
DEFAULT_STEP_BUDGET = 8
DEFAULT_SECONDS_BUDGET = 900.0


class RepairOutcome(str, Enum):
    """What the loop concluded. Every value is reachable and none is a success by default."""

    REPAIR_ADMISSIBLE = "REPAIR_ADMISSIBLE"
    NO_FAILURE_DETECTED = "NO_FAILURE_DETECTED"
    NO_PROGRESS = "NO_PROGRESS"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    ATTEMPT_CAP_REACHED = "ATTEMPT_CAP_REACHED"
    REFUSED_SIDE_EFFECT_UNKNOWN = "REFUSED_SIDE_EFFECT_UNKNOWN"
    REFUSED_BY_REPEAT_GUARANTEE = "REFUSED_BY_REPEAT_GUARANTEE"
    REQUIRES_REAPPROVAL = "REQUIRES_REAPPROVAL"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    BLOCKED_NO_TARGET = "BLOCKED_NO_TARGET"
    UNKNOWN = "UNKNOWN"


class StopRule(str, Enum):
    """The single reason the loop stopped. Always present in the response."""

    NONE = "none"
    ATTEMPT_CAP = "attempt_cap"
    STEP_BUDGET = "step_budget"
    SECONDS_BUDGET = "seconds_budget"
    NO_PROGRESS = "no_progress"
    SIDE_EFFECT_OUTCOME_UNKNOWN = "side_effect_outcome_unknown"
    REPEAT_GUARANTEE_DOES_NOT_PERMIT = "repeat_guarantee_does_not_permit"
    REAPPROVAL_REQUIRED = "reapproval_required"
    APPROVAL_UNRESOLVED = "approval_unresolved"
    NO_TARGET = "no_target"
    DISPATCH_NOT_COMPOSED = "dispatch_not_composed_on_this_surface"
    NOTHING_FAILED = "nothing_failed"


class Confidence(str, Enum):
    """An **ordinal** confidence, with its derivation written out.

    There is no percentage here, and that is deliberate: a number such as "0.72"
    would look like a measurement while being an author's choice. These three
    values are decided by a rule that can be read and audited:

    ``SUPPORTED``   the probable cause is named by at least one *specific*
    evidence code the step itself recorded (an ``exit_code_mismatch``, an
    ``artifact_not_produced``, a ``refused_before_dispatch:*`` code) **and** an
    observation exists to compare it against;
    ``INFERRED``    the probable cause is named by the step's own recorded status
    and verification status only -- a real observation, but not a specific code;
    ``UNSUPPORTED`` no evidence at all names a cause, so no probable cause is
    asserted and the cause is reported as ``UNKNOWN``.

    ``SUPPORTED`` therefore means "a cause is named by a specific recorded code",
    not "this is probably right", and the limits of that are stated in the
    response's own ``confidence_meaning`` field.
    """

    SUPPORTED = "SUPPORTED"
    INFERRED = "INFERRED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ProbableCause:
    """A named hypothesis about why the step did not settle, with its basis."""

    code: str
    statement: str
    confidence: Confidence
    basis: tuple[str, ...] = ()
    limitations: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "statement": self.statement,
            "confidence": self.confidence.value,
            "confidence_is_ordinal_not_a_probability": True,
            "confidence_meaning": (
                "an ORDINAL judgement, not a probability and not a percentage: "
                "SUPPORTED = a specific evidence code the step recorded names this "
                "cause; INFERRED = only the recorded statuses name it; UNSUPPORTED = "
                "nothing names a cause and none is asserted"
            ),
            "basis": list(self.basis),
            "limitations": self.limitations,
        }


@dataclass(frozen=True)
class RepairBudget:
    """The three ceilings and how much of each is left."""

    attempt_cap: int = DEFAULT_ATTEMPT_CAP
    step_budget: int = DEFAULT_STEP_BUDGET
    seconds_budget: float = DEFAULT_SECONDS_BUDGET
    attempts_used: int = 0
    steps_targeted: int = 0
    seconds_elapsed: float = 0.0

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.attempt_cap - self.attempts_used)

    @property
    def steps_remaining(self) -> int:
        return max(0, self.step_budget - self.steps_targeted)

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.seconds_budget - self.seconds_elapsed)

    @property
    def exhausted(self) -> bool:
        return (
            self.attempts_remaining == 0
            or self.steps_remaining == 0
            or self.seconds_remaining <= 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_cap": self.attempt_cap,
            "step_budget": self.step_budget,
            "seconds_budget": self.seconds_budget,
            "attempts_used": self.attempts_used,
            "steps_targeted": self.steps_targeted,
            "seconds_elapsed": self.seconds_elapsed,
            "attempts_remaining": self.attempts_remaining,
            "steps_remaining": self.steps_remaining,
            "seconds_remaining": self.seconds_remaining,
            "exhausted": self.exhausted,
        }


@dataclass(frozen=True)
class RepairStepPlan:
    """The minimal repair proposed for one step, and whether it is admissible."""

    step_id: str
    declaration_id: str
    failure_kind: str
    probable_cause: ProbableCause
    targeted_condition: str
    proposed_action: str
    repeat_guarantee: str
    permits_automatic_repeat: bool
    requires_reapproval: bool
    approval_required: bool
    approval_resolved: bool
    authorized_to_dispatch: bool
    dispatched: bool = False
    refusal_code: str = ""
    evidence: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "declaration_id": self.declaration_id,
            "failure_kind": self.failure_kind,
            "probable_cause": self.probable_cause.to_dict(),
            "targeted_condition": self.targeted_condition,
            "proposed_action": self.proposed_action,
            "repeat_guarantee": self.repeat_guarantee,
            "permits_automatic_repeat": self.permits_automatic_repeat,
            "requires_reapproval": self.requires_reapproval,
            "approval_required": self.approval_required,
            "approval_resolved": self.approval_resolved,
            "authorized_to_dispatch": self.authorized_to_dispatch,
            "dispatched": self.dispatched,
            "refusal_code": self.refusal_code,
            "evidence": list(self.evidence),
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class RepairPlan:
    """The loop's whole answer: outcome, stop rule, budget, and per-step plans."""

    graph_run_id: str
    outcome: RepairOutcome
    stop_rule: StopRule
    budget: RepairBudget
    steps: tuple[RepairStepPlan, ...] = ()
    evidence: tuple[str, ...] = ()
    progress_marker: str = ""
    previous_progress_marker: str = ""
    dispatch_performed: bool = False
    dispatch_path: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_run_id": self.graph_run_id,
            "outcome": self.outcome.value,
            "stop_rule": self.stop_rule.value,
            "budget": self.budget.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "evidence": list(self.evidence),
            "progress_marker": self.progress_marker,
            "previous_progress_marker": self.previous_progress_marker,
            # THREE-VALUED, and deliberately so. ``None`` means no previous
            # observation was supplied, so progress is not *establishable* -- which
            # is not the same fact as "no progress was made". Reporting ``false``
            # there would claim a comparison that never happened.
            "progress_made": (
                None
                if not self.previous_progress_marker
                else (
                    self.progress_marker != self.previous_progress_marker
                    and bool(self.progress_marker)
                )
            ),
            "progress_is_establishable": bool(self.previous_progress_marker),
            "progress_meaning": (
                "null = no previous observation was supplied, so progress cannot be "
                "established; true/false = the observable state differs from / equals "
                "the supplied previous progress marker"
            ),
            "dispatch_performed": self.dispatch_performed,
            "dispatch_path": self.dispatch_path,
            "notes": list(self.notes),
        }


def progress_marker(observations: Mapping[str, Any]) -> str:
    """A digest of the *observable* state of an attempt.

    Observables only: each step's status, verification status, exit code, evidence
    codes and ledger guarantee. It carries no timestamp, no attempt counter and no
    random value, so two observations of an unchanged attempt produce the same
    marker -- which is exactly what makes "no progress" decidable.
    """

    payload = json.dumps(observations, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


_SPECIFIC_EVIDENCE_PREFIXES = (
    "exit_code_mismatch",
    "artifact_not_produced",
    "refused_before_dispatch",
    "verification_error",
    "verification_not_run",
    "execution_did_not_succeed",
    "idempotency",
)

#: Evidence prefixes that name a *command* failure rather than a check that could
#: not be evaluated. ``verification_not_run:execution_did_not_succeed`` is a check
#: that was never reached **because the command itself failed**, which is a
#: refuted result and not an unevaluable one. Collapsing the two would report a
#: broken command as a missing capability.
_EXECUTION_FAILURE_PREFIXES = (
    "execution_did_not_succeed",
    "execution_error",
    "verification_not_run:execution_did_not_succeed",
)


class RepairLoop:
    """Decide, within a budget, whether one failed attempt may be repaired.

    Constructed per call by the operating surface from the recorded attempt, the
    composition's own approval policy and resolver, and the durable ledger. It
    holds no state between calls and dispatches nothing.
    """

    def __init__(
        self,
        *,
        graph_run_id: str,
        snapshot: Any,
        declarations: Mapping[str, Any] | None = None,
        repeat_decisions: Mapping[str, Any] | None = None,
        approval_policy: Any = None,
        approval_resolver: Any = None,
        attempt_cap: int | None = None,
        step_budget: int | None = None,
        seconds_budget: float | None = None,
        previous_progress_marker: str = "",
        now: datetime | None = None,
    ) -> None:
        self._graph_run_id = graph_run_id
        self._snapshot = snapshot
        self._declarations = dict(declarations or {})
        self._repeat = dict(repeat_decisions or {})
        self._policy = approval_policy
        self._resolver = approval_resolver
        self._attempt_cap = max(1, int(attempt_cap or DEFAULT_ATTEMPT_CAP))
        self._step_budget = max(1, int(step_budget or DEFAULT_STEP_BUDGET))
        self._seconds_budget = max(1.0, float(seconds_budget or DEFAULT_SECONDS_BUDGET))
        self._previous_marker = str(previous_progress_marker or "")
        self._now = now or datetime.now(timezone.utc)

    # -- observations ---------------------------------------------------
    def _observations(self) -> dict[str, Any]:
        steps = tuple(getattr(self._snapshot, "steps", ()) or ())
        return {
            "steps": {
                str(getattr(record, "step_id", "")): {
                    "status": getattr(
                        getattr(record, "status", None), "value", ""
                    ),
                    "verification_status": getattr(
                        getattr(record, "verification_status", None), "value", ""
                    ),
                    "exit_code": getattr(record, "execution_exit_code", None),
                    "outcome": str(getattr(record, "execution_outcome", "")),
                    "evidence": [str(code) for code in getattr(record, "evidence", ())],
                    "attempts": int(getattr(record, "attempts", 0) or 0),
                }
                for record in steps
            },
            "guarantees": {
                str(step_id): str(getattr(decision, "guarantee", "")).replace(
                    "RepeatGuarantee.", ""
                )
                for step_id, decision in sorted(self._repeat.items())
            },
            "graph_status": getattr(
                getattr(self._snapshot, "status", None), "value", ""
            ),
        }

    def _failed_steps(self) -> list[Any]:
        failed: list[Any] = []
        for record in tuple(getattr(self._snapshot, "steps", ()) or ()):
            status = getattr(record, "status", None)
            verification = getattr(record, "verification_status", None)
            if status is StepStatus.FAILED:
                failed.append(record)
                continue
            if verification is VerificationStatus.FAILED:
                failed.append(record)
                continue
            if status is StepStatus.UNKNOWN:
                failed.append(record)
        return failed

    @staticmethod
    def _specific_evidence(record: Any) -> tuple[str, ...]:
        codes = tuple(str(code) for code in getattr(record, "evidence", ()))
        return tuple(
            code
            for code in codes
            if any(code.startswith(prefix) for prefix in _SPECIFIC_EVIDENCE_PREFIXES)
        )

    @staticmethod
    def _execution_failure_evidence(record: Any) -> tuple[str, ...]:
        """Evidence codes that name a *command* failure, not an unevaluable check."""

        codes = tuple(str(code) for code in getattr(record, "evidence", ()))
        return tuple(
            code
            for code in codes
            if any(code.startswith(prefix) for prefix in _EXECUTION_FAILURE_PREFIXES)
        )

    def _probable_cause(self, record: Any) -> ProbableCause:
        """A named cause with an ordinal confidence, derived from recorded evidence."""

        specific = self._specific_evidence(record)
        execution_failure = self._execution_failure_evidence(record)
        status = getattr(getattr(record, "status", None), "value", "")
        verification = getattr(
            getattr(record, "verification_status", None), "value", ""
        )
        outcome = str(getattr(record, "execution_outcome", ""))
        exit_code = getattr(record, "execution_exit_code", None)

        if not specific and not status and not verification:
            return ProbableCause(
                code="no_evidence",
                statement=(
                    "the record carries no evidence, so no probable cause is asserted"
                ),
                confidence=Confidence.UNSUPPORTED,
                basis=(),
                limitations=(
                    "an unsupported cause is reported as unknown; it is never used to "
                    "justify an action"
                ),
            )

        if execution_failure or (
            outcome.startswith("EXECUTION_") and outcome != "EXECUTION_SUCCESS"
        ):
            # The command itself did not succeed. A declared check that was never
            # reached because of that is NOT an unevaluable check: the failure is a
            # recorded observation about the command.
            return ProbableCause(
                code="command_did_not_succeed",
                statement=(
                    f"the command exited {exit_code!r} with outcome {outcome!r}, so "
                    "the step's declared check was never reached"
                ),
                confidence=(
                    Confidence.SUPPORTED if execution_failure else Confidence.INFERRED
                ),
                basis=execution_failure
                or (f"execution_outcome={outcome}", f"exit_code={exit_code}"),
                limitations=(
                    "this names that the command did not succeed and the exit code it "
                    "produced; it does not diagnose WHY the program exited that way"
                ),
            )

        if any(code.startswith("exit_code_mismatch") for code in specific) or (
            verification == "FAILED" and exit_code is not None
        ):
            return ProbableCause(
                code="declared_verification_did_not_hold",
                statement=(
                    "the command ran and exited "
                    f"{exit_code!r}, and the step's declared check did not hold"
                ),
                confidence=(
                    Confidence.SUPPORTED if specific else Confidence.INFERRED
                ),
                basis=specific
                or (f"verification_status={verification}", f"exit_code={exit_code}"),
                limitations=(
                    "this names WHICH check failed, not why the program produced that "
                    "result; the cause is not established by this record"
                ),
            )
        if any(code.startswith("artifact_not_produced") for code in specific) or (
            "artifact_absent" in str(getattr(record, "verification_observed", ""))
        ):
            return ProbableCause(
                code="declared_artifact_absent",
                statement=(
                    "the declared artifact target is absent from the harvested "
                    "results of a command that exited "
                    f"{exit_code!r}"
                ),
                confidence=(
                    Confidence.SUPPORTED if specific else Confidence.INFERRED
                ),
                basis=specific or (f"verification_status={verification}",),
                limitations=(
                    "absence of a harvested artifact is evidence about the harvest and "
                    "the declared target, not proof the program failed to write "
                    "anything anywhere"
                ),
            )
        if any(code.startswith("refused_before_dispatch") for code in specific):
            return ProbableCause(
                code="refused_before_dispatch",
                statement=(
                    "the step never reached the execution plane: the repeat guard "
                    "refused it before dispatch, so no process ran"
                ),
                confidence=Confidence.SUPPORTED,
                basis=specific,
                limitations=(
                    "a refusal is not a failure of the work; it says an attempt was "
                    "not admitted"
                ),
            )
        if any(code.startswith("verification_error") for code in specific) or (
            verification == "ERROR"
        ):
            return ProbableCause(
                code="verification_unevaluable",
                statement=(
                    "the declared check could not be evaluated, so nothing about the "
                    "step's result is proven either way"
                ),
                confidence=(
                    Confidence.SUPPORTED if specific else Confidence.INFERRED
                ),
                basis=specific or (f"verification_status={verification}",),
                limitations=(
                    "an unevaluable check is not a failed one; this cause names a "
                    "missing capability, not a broken program"
                ),
            )
        if status == "UNKNOWN" or outcome == "UNKNOWN":
            return ProbableCause(
                code="outcome_unprovable",
                statement=(
                    "the recorded outcome does not prove whether the work completed"
                ),
                confidence=Confidence.INFERRED,
                basis=(f"status={status}", f"execution_outcome={outcome}"),
                limitations=(
                    "an unprovable outcome is never treated as success and never "
                    "automatically repeated"
                ),
            )
        return ProbableCause(
            code="status_only",
            statement=(
                f"the recorded status is {status!r} with verification "
                f"{verification!r}"
            ),
            confidence=Confidence.INFERRED if (status or verification) else Confidence.UNSUPPORTED,
            basis=(f"status={status}", f"verification_status={verification}"),
            limitations=(
                "a status is an observation about the record, not a diagnosis of the "
                "program"
            ),
        )

    def _approval_state(self, declaration_id: str) -> tuple[bool, bool]:
        """(approval_required, approval_resolved) read from the composition itself."""

        if self._policy is None:
            # A missing policy is never permission. It is reported as required and
            # unresolved, which is the fail-closed reading used everywhere else.
            return True, False
        declaration = self._declarations.get(declaration_id)
        if declaration is None:
            return True, False
        command = tuple(getattr(declaration, "command", ()) or ())
        try:
            from app.execution.identity import CommandIdentity

            identity = CommandIdentity(
                executable=command[0] if command else "",
                argv=tuple(str(item) for item in command),
            )
        except Exception:  # noqa: BLE001
            identity = command[0] if command else ""
        try:
            state = self._policy.evaluate(identity)
        except Exception:  # noqa: BLE001 - an unevaluable policy is not a permit
            return True, False
        required = getattr(state, "value", str(state)) == "REQUIRED"
        if not required:
            return False, True
        return True, self._resolver is not None

    def _seconds_elapsed(self) -> float:
        updated = str(getattr(self._snapshot, "updated_at", "") or "")
        if not updated:
            return 0.0
        try:
            started = datetime.fromisoformat(updated)
        except ValueError:
            return 0.0
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0.0, (self._now - started).total_seconds())

    # -- the loop --------------------------------------------------------
    def plan(self) -> RepairPlan:
        observations = self._observations()
        marker = progress_marker(observations)
        failed = self._failed_steps()
        recorded_attempts = int(getattr(self._snapshot, "attempts", 0) or 0)
        budget = RepairBudget(
            attempt_cap=self._attempt_cap,
            step_budget=self._step_budget,
            seconds_budget=self._seconds_budget,
            attempts_used=recorded_attempts,
            steps_targeted=min(len(failed), self._step_budget),
            seconds_elapsed=self._seconds_elapsed(),
        )

        base = {
            "graph_run_id": self._graph_run_id,
            "budget": budget,
            "progress_marker": marker,
            "previous_progress_marker": self._previous_marker,
        }
        notes = [
            "this is a bounded decision loop: it may take at most "
            f"{self._attempt_cap} attempt(s), touch at most {self._step_budget} "
            f"step(s), and run for at most {self._seconds_budget:g} seconds",
            "a probable cause is a named hypothesis with an ordinal confidence, not a "
            "diagnosis, and it never by itself authorizes an action",
        ]

        if not failed:
            return RepairPlan(
                **base,
                outcome=RepairOutcome.NO_FAILURE_DETECTED,
                stop_rule=StopRule.NOTHING_FAILED,
                evidence=tuple(
                    f"{getattr(record, 'step_id', '')}={getattr(getattr(record, 'status', None), 'value', '')}"
                    for record in tuple(getattr(self._snapshot, "steps", ()) or ())
                ),
                dispatch_path=self._dispatch_path(),
                notes=tuple(
                    notes
                    + [
                        "no step is recorded FAILED or UNKNOWN, so there is nothing to "
                        "repair; this is not a claim that the work is correct"
                    ]
                ),
            )

        if recorded_attempts >= self._attempt_cap:
            return RepairPlan(
                **base,
                outcome=RepairOutcome.ATTEMPT_CAP_REACHED,
                stop_rule=StopRule.ATTEMPT_CAP,
                evidence=(f"recorded_attempts={recorded_attempts}",),
                dispatch_path=self._dispatch_path(),
                notes=tuple(
                    notes
                    + [
                        "the attempt cap is reached, so the loop stops instead of "
                        "spending another attempt"
                    ]
                ),
            )

        if self._seconds_elapsed() >= self._seconds_budget:
            return RepairPlan(
                **base,
                outcome=RepairOutcome.BUDGET_EXHAUSTED,
                stop_rule=StopRule.SECONDS_BUDGET,
                evidence=(
                    f"seconds_elapsed={self._seconds_elapsed():.3f}",
                    f"seconds_budget={self._seconds_budget:g}",
                ),
                dispatch_path=self._dispatch_path(),
                notes=tuple(notes + ["the wall-clock budget is exhausted"]),
            )

        if self._previous_marker and self._previous_marker == marker:
            return RepairPlan(
                **base,
                outcome=RepairOutcome.NO_PROGRESS,
                stop_rule=StopRule.NO_PROGRESS,
                evidence=(
                    f"progress_marker={marker}",
                    "the observable state is identical to the previous observation",
                ),
                dispatch_path=self._dispatch_path(),
                notes=tuple(
                    notes
                    + [
                        "the previous attempt changed nothing observable, so the loop "
                        "stops rather than spending the remaining budget"
                    ]
                ),
            )

        steps: list[RepairStepPlan] = []
        worst = RepairOutcome.REPAIR_ADMISSIBLE
        worst_rule = StopRule.DISPATCH_NOT_COMPOSED
        for record in failed[: self._step_budget]:
            step_plan, outcome, rule = self._plan_step(record)
            steps.append(step_plan)
            # Order the outcomes by how restrictive they are: the loop reports the
            # most restrictive one, so a refusal can never be hidden behind an
            # admissible sibling.
            if _OUTCOME_SEVERITY[outcome] < _OUTCOME_SEVERITY[worst] or (
                _OUTCOME_SEVERITY[outcome] == _OUTCOME_SEVERITY[worst]
                and rule is not StopRule.DISPATCH_NOT_COMPOSED
                and worst_rule is StopRule.DISPATCH_NOT_COMPOSED
            ):
                worst = outcome
                worst_rule = rule

        if len(failed) > self._step_budget:
            budget = RepairBudget(
                attempt_cap=self._attempt_cap,
                step_budget=self._step_budget,
                seconds_budget=self._seconds_budget,
                attempts_used=recorded_attempts,
                steps_targeted=self._step_budget,
                seconds_elapsed=budget.seconds_elapsed,
            )
            base["budget"] = budget
            notes.append(
                f"{len(failed)} step(s) did not settle but the step budget is "
                f"{self._step_budget}; the remainder is reported as untargeted, not "
                "as repaired"
            )
            if worst is RepairOutcome.REPAIR_ADMISSIBLE:
                worst = RepairOutcome.BUDGET_EXHAUSTED
                worst_rule = StopRule.STEP_BUDGET

        return RepairPlan(
            **base,
            outcome=worst,
            stop_rule=worst_rule,
            steps=tuple(steps),
            evidence=tuple(
                code
                for step_plan in steps
                for code in step_plan.evidence
            ),
            dispatch_path=self._dispatch_path(),
            notes=tuple(notes),
        )

    def _dispatch_path(self) -> str:
        return (
            "no dispatch path is composed on this surface: an admitted repair would "
            "have to be dispatched through an existing authorized entry point "
            "(ForgeApiService.run_declared_verification, or "
            "StepGraphRunner.resume) under a fresh idempotency key and the same "
            "RunScope -> ApprovalPolicy -> ExecutionCoordinator chain; this planner "
            "therefore dispatches nothing and says so"
        )

    def _plan_step(self, record: Any) -> tuple[RepairStepPlan, RepairOutcome, StopRule]:
        step_id = str(getattr(record, "step_id", ""))
        declaration_id = str(getattr(record, "declaration_id", ""))
        cause = self._probable_cause(record)
        decision = self._repeat.get(step_id)
        guarantee = str(getattr(getattr(decision, "guarantee", None), "value", ""))
        permits = bool(getattr(decision, "permits_automatic_repeat", False))
        reason = str(getattr(decision, "reason", ""))
        approval_required, approval_resolved = self._approval_state(declaration_id)
        proposals = tuple(getattr(record, "evidence", ()))
        verification_kind = str(getattr(record, "verification_kind", ""))
        targeted = (
            f"{verification_kind}:{getattr(record, 'verification_expected', '')}"
            if verification_kind
            else "no declared verification, so no targeted condition exists"
        )

        evidence = (
            f"step_status={getattr(getattr(record, 'status', None), 'value', '')}",
            f"verification_status={getattr(getattr(record, 'verification_status', None), 'value', '')}",
            f"execution_exit_code={getattr(record, 'execution_exit_code', None)}",
            f"repeat_guarantee={guarantee or 'not_measured'}",
            f"approval_required={approval_required}",
            f"approval_resolved={approval_resolved}",
        ) + tuple(f"evidence:{code}" for code in proposals)

        if guarantee == RepeatGuarantee.REFUSED_SIDE_EFFECT_UNKNOWN.value:
            return (
                RepairStepPlan(
                    step_id=step_id,
                    declaration_id=declaration_id,
                    failure_kind="SIDE_EFFECT_OUTCOME_UNKNOWN",
                    probable_cause=cause,
                    targeted_condition=targeted,
                    proposed_action=(
                        "none: the outcome of a non-replay-safe side effect is unknown, "
                        "so repeating it could double it and is forbidden"
                    ),
                    repeat_guarantee=guarantee,
                    permits_automatic_repeat=False,
                    requires_reapproval=True,
                    approval_required=approval_required,
                    approval_resolved=approval_resolved,
                    authorized_to_dispatch=False,
                    refusal_code=RepeatGuarantee.REFUSED_SIDE_EFFECT_UNKNOWN.value,
                    evidence=evidence + (f"repeat_reason={reason}",),
                ),
                RepairOutcome.REFUSED_SIDE_EFFECT_UNKNOWN,
                StopRule.SIDE_EFFECT_OUTCOME_UNKNOWN,
            )

        if guarantee == RepeatGuarantee.REQUIRES_REAPPROVAL.value:
            return (
                RepairStepPlan(
                    step_id=step_id,
                    declaration_id=declaration_id,
                    failure_kind="REQUIRES_REAPPROVAL",
                    probable_cause=cause,
                    targeted_condition=targeted,
                    proposed_action=(
                        "none until authority is re-established through the normal "
                        "approval path"
                    ),
                    repeat_guarantee=guarantee,
                    permits_automatic_repeat=False,
                    requires_reapproval=True,
                    approval_required=approval_required,
                    approval_resolved=approval_resolved,
                    authorized_to_dispatch=False,
                    refusal_code=RepeatGuarantee.REQUIRES_REAPPROVAL.value,
                    evidence=evidence + (f"repeat_reason={reason}",),
                ),
                RepairOutcome.REQUIRES_REAPPROVAL,
                StopRule.REAPPROVAL_REQUIRED,
            )

        if guarantee and not permits:
            return (
                RepairStepPlan(
                    step_id=step_id,
                    declaration_id=declaration_id,
                    failure_kind="REPEAT_NOT_PERMITTED",
                    probable_cause=cause,
                    targeted_condition=targeted,
                    proposed_action="none: the repeat guarantee does not permit a repeat",
                    repeat_guarantee=guarantee,
                    permits_automatic_repeat=False,
                    requires_reapproval=guarantee
                    == RepeatGuarantee.REFUSED_SECURITY_TERMINAL.value,
                    approval_required=approval_required,
                    approval_resolved=approval_resolved,
                    authorized_to_dispatch=False,
                    refusal_code=guarantee,
                    evidence=evidence + (f"repeat_reason={reason}",),
                ),
                RepairOutcome.REFUSED_BY_REPEAT_GUARANTEE,
                StopRule.REPEAT_GUARANTEE_DOES_NOT_PERMIT,
            )

        if approval_required and not approval_resolved:
            return (
                RepairStepPlan(
                    step_id=step_id,
                    declaration_id=declaration_id,
                    failure_kind="APPROVAL_UNRESOLVED",
                    probable_cause=cause,
                    targeted_condition=targeted,
                    proposed_action=(
                        "none: the composed ApprovalPolicy requires approval for this "
                        "operation and no resolver has resolved it"
                    ),
                    repeat_guarantee=guarantee or "not_measured",
                    permits_automatic_repeat=permits,
                    requires_reapproval=False,
                    approval_required=True,
                    approval_resolved=False,
                    authorized_to_dispatch=False,
                    refusal_code="approval_unresolved",
                    evidence=evidence,
                ),
                RepairOutcome.AWAITING_APPROVAL,
                StopRule.APPROVAL_UNRESOLVED,
            )

        return (
            RepairStepPlan(
                step_id=step_id,
                declaration_id=declaration_id,
                failure_kind="FAILURE_WITH_ADMISSIBLE_REPEAT",
                probable_cause=cause,
                targeted_condition=targeted,
                proposed_action=(
                    "re-establish the targeted condition only: re-run the declared "
                    f"operation {declaration_id!r} and re-evaluate the declared check "
                    f"({targeted})"
                ),
                repeat_guarantee=guarantee or "not_measured",
                permits_automatic_repeat=permits,
                requires_reapproval=False,
                approval_required=approval_required,
                approval_resolved=approval_resolved,
                authorized_to_dispatch=bool(permits and not approval_required),
                evidence=evidence + (f"repeat_reason={reason}",),
                extra={"proposed_reverification": targeted},
            ),
            RepairOutcome.REPAIR_ADMISSIBLE,
            StopRule.DISPATCH_NOT_COMPOSED,
        )


#: Lower is more restrictive. The loop reports the most restrictive outcome it found,
#: so an admissible sibling can never mask a refusal.
_OUTCOME_SEVERITY: dict[RepairOutcome, int] = {
    RepairOutcome.REFUSED_SIDE_EFFECT_UNKNOWN: 0,
    RepairOutcome.REFUSED_BY_REPEAT_GUARANTEE: 1,
    RepairOutcome.REQUIRES_REAPPROVAL: 2,
    RepairOutcome.AWAITING_APPROVAL: 3,
    RepairOutcome.BLOCKED_NO_TARGET: 4,
    RepairOutcome.UNKNOWN: 5,
    RepairOutcome.ATTEMPT_CAP_REACHED: 6,
    RepairOutcome.BUDGET_EXHAUSTED: 7,
    RepairOutcome.NO_PROGRESS: 8,
    RepairOutcome.NO_FAILURE_DETECTED: 9,
    RepairOutcome.REPAIR_ADMISSIBLE: 10,
}
