"""Evidence Graph: real traceability from a goal to a final status.

WHAT THIS MODULE IS
===================

A **projection**, not a store. It assembles the chain

``Goal -> Requirements -> Acceptance Criteria -> Plan -> Steps -> Approval ->
Authorized Actions -> File Changes -> Commands -> Verification Results ->
Final Status``

out of identifiers that already exist, and it does so without adding a second
registry of anything:

======================  ==========================================================
Transition              Existing object it is read from
======================  ==========================================================
Goal -> Requirements    ``MemoryRecord`` (``HYPOTHESIS``) in ``ProjectMemory``,
                        plus ``ForgeApiService.declarations``
Requirements -> Criteria ``AcceptanceSpec.criteria`` /
                        ``AcceptanceCriterion.criterion_id``
Criteria -> Plan        ``StepDefinition.action.declaration_id`` in the declared
                        plan of the recorded attempt, matched to ``declaration_id``
Plan -> Steps           ``StepGraphSnapshot.declared_plan`` / ``StepRecord.step_id``
Steps -> Approval       ``StepRecord.status`` / ``approval_fingerprint`` on the
                        snapshot, plus ``StepRecord.parameters`` re-fingerprinted
                        through ``StepGraphRunner.intent_fingerprint``
Approval -> Authorized  ``RunIdempotencyGuard.recorded_state`` for the step's own
                        ``StepGraphRunner.operation_identity``
Authorized -> Files     ``StructuralFact.source_path`` plus ``SnapshotFile`` sets
                        of the recorded step run and the project baseline
Files -> Commands       ``StepRecord.performed_executable`` /
                        ``performed_working_directory``
Commands -> Verification ``StepRecord.execution_exit_code`` /
                        ``execution_outcome`` / ``verification_status``
Verification -> Status  computed, never asserted -- see below
======================  ==========================================================

MISSING LINKS ARE SHOWN, NEVER INVENTED
=======================================

Every link is an object with a ``status``. A link whose target genuinely has no
evidence is emitted with ``status="MISSING"`` and a machine ``missing_code``
naming *which* link is absent -- it is never omitted from the graph as if the
transition did not exist, and it is never filled in with a plausible-looking
object. Two examples the tests pin:

* a step that is ``WAITING_APPROVAL`` yields an
  ``approved_actions`` link with ``missing_code = "no_approval_decision_recorded"``;
* a requirement with no acceptance criterion yields a
  ``acceptance_criteria`` link with
  ``missing_code = "requirement_has_no_declared_criterion"``.

THE FINAL STATUS DOES NOT COME FROM AN AGENT'S CLAIM
====================================================

``derive_verdict`` reads only observations: the ``VerificationStatus`` of the
step records (where ``PASSED`` is the only pass and ``NOT_RUN`` / ``ERROR`` prove
nothing) and the requirements' own criteria. ``StepGraphStatus.COMPLETED`` is
**not** accepted as proof, because it is a statement about the graph's control
flow, not about a check that held. An empty graph, or one where nothing was
verified, is ``UNPROVEN`` -- never ``PROVEN``.

Anything an agent *claimed* is placed in ``claimed_by_agent``, a separate
compartment that the verdict computation does not read. It is kept so an operator
can see the claim next to the evidence; it is deliberately inert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from app.workflows.step_graph import (
    StepStatus,
    VerificationStatus,
)

__all__ = [
    "LinkStatus",
    "Phase",
    "TraceLink",
    "EvidenceGraph",
    "EvidenceGraphBuilder",
    "Verdict",
    "derive_verdict",
    "expectations_satisfied",
    "MISSING_CODES",
]


class Phase(str, Enum):
    """The ordered compartments of the chain. The order is the chain itself."""

    GOAL = "GOAL"
    REQUIREMENTS = "REQUIREMENTS"
    ACCEPTANCE_CRITERIA = "ACCEPTANCE_CRITERIA"
    PLAN = "PLAN"
    STEPS = "STEPS"
    APPROVAL = "APPROVAL"
    AUTHORIZED_ACTIONS = "AUTHORIZED_ACTIONS"
    FILE_CHANGES = "FILE_CHANGES"
    COMMANDS = "COMMANDS"
    VERIFICATION_RESULTS = "VERIFICATION_RESULTS"
    FINAL_STATUS = "FINAL_STATUS"


#: The canonical order of the chain. A consumer walks it in this order.
PHASE_ORDER: tuple[Phase, ...] = tuple(Phase)


class LinkStatus(str, Enum):
    """Whether a transition is established, and by what.

    ``ESTABLISHED`` -- the target object exists and at least one concrete
    identifier is cited for it;
    ``MISSING`` -- the target genuinely has no evidence. The link is *emitted*
    with this status and a ``missing_code``; it is never dropped and never filled
    in;
    ``REFUSED`` -- the server refused the transition; the refusal itself is the
    evidence.
    """

    ESTABLISHED = "ESTABLISHED"
    MISSING = "MISSING"
    REFUSED = "REFUSED"


class Verdict(str, Enum):
    """The final status of a requirement, derived from observations only.

    ``PROVEN``     every criterion has a declared check that observed ``PASSED``;
    ``REFUTED``    a declared check was evaluated and did not hold (``FAILED``);
    ``PARTIAL``    some declared checks passed and others provably were not run;
    ``UNPROVEN``   nothing observed proves the requirement either way. Used when
                   nothing about the requirement was even declared to be checkable;
    ``REFUSED``    the authorization chain refused;
    ``UNKNOWN``    the record cannot be trusted or the outcome is unprovable;
    ``INCOMPLETE`` the requirement was declared checkable and that declared
                   expectation was never settled. Distinct from ``UNPROVEN``:
                   the first says "a check was named and did not run", the second
                   says "there was nothing to run". Collapsing them would hide
                   which of the two happened.
    """

    PROVEN = "PROVEN"
    REFUTED = "REFUTED"
    PARTIAL = "PARTIAL"
    UNPROVEN = "UNPROVEN"
    REFUSED = "REFUSED"
    UNKNOWN = "UNKNOWN"
    INCOMPLETE = "INCOMPLETE"


#: Machine codes naming exactly which link is absent. New codes are added rather
#: than reusing one, because "which link is missing" is the whole diagnostic value.
MISSING_CODES: tuple[str, ...] = (
    "no_goal_recorded",
    "no_requirement_declared",
    "requirement_has_no_declared_criterion",
    "criterion_has_no_verification_expectation",
    "no_step_targets_this_requirement",
    "no_attempt_recorded_for_this_step",
    "no_approval_decision_recorded",
    "no_authorized_action_recorded",
    "no_file_change_observed_since_baseline",
    "no_command_performed",
    "no_declared_verification_for_this_step",
    "step_verification_not_run",
    "step_verification_unevaluable",
    "baseline_absent_so_changes_are_unmeasurable",
    "no_recorded_attempt",
)


@dataclass(frozen=True)
class TraceLink:
    """One transition of the chain, with the objects and evidence behind it."""

    link_id: str
    frm: Phase
    to: Phase
    status: LinkStatus
    source_ref: str = ""
    target_ref: str = ""
    evidence: tuple[str, ...] = ()
    detail: str = ""
    missing_code: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "from": self.frm.value,
            "to": self.to.value,
            "status": self.status.value,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "evidence": list(self.evidence),
            "detail": self.detail,
            "missing_code": self.missing_code,
            "extra": dict(self.extra),
        }


def _missing(
    link_id: str,
    frm: Phase,
    to: Phase,
    code: str,
    *,
    source_ref: str = "",
    target_ref: str = "",
    detail: str = "",
    evidence: Sequence[str] = (),
) -> TraceLink:
    if code not in MISSING_CODES:
        raise ValueError(f"unknown missing code: {code!r}")
    return TraceLink(
        link_id=link_id,
        frm=frm,
        to=to,
        status=LinkStatus.MISSING,
        source_ref=source_ref,
        target_ref=target_ref,
        evidence=tuple(evidence),
        detail=detail,
        missing_code=code,
    )


@dataclass(frozen=True)
class EvidenceGraph:
    """The assembled chain, plus the compartment that the verdict ignores."""

    goal_refs: tuple[str, ...] = ()
    links: tuple[TraceLink, ...] = ()
    #: A requirement id -> verdict map, computed from observations only.
    requirement_verdicts: Mapping[str, str] = field(default_factory=dict)
    #: The whole-project verdict. Derived from ``requirement_verdicts``.
    verdict: Verdict = Verdict.UNPROVEN
    #: What an agent said, kept apart and never read by the verdict computation.
    claimed_by_agent: tuple[Mapping[str, Any], ...] = ()
    notes: tuple[str, ...] = ()

    def links_of(self, frm: Phase | None = None, to: Phase | None = None) -> tuple[TraceLink, ...]:
        return tuple(
            link
            for link in self.links
            if (frm is None or link.frm is frm) and (to is None or link.to is to)
        )

    @property
    def missing_links(self) -> tuple[TraceLink, ...]:
        return tuple(link for link in self.links if link.status is LinkStatus.MISSING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phases": [phase.value for phase in PHASE_ORDER],
            "goal_refs": list(self.goal_refs),
            "links": [link.to_dict() for link in self.links],
            "missing_links": [link.to_dict() for link in self.missing_links],
            "missing_link_count": len(self.missing_links),
            "requirement_verdicts": dict(self.requirement_verdicts),
            "verdict": self.verdict.value,
            "claimed_by_agent": [dict(item) for item in self.claimed_by_agent],
            "notes": list(self.notes),
            "verdict_is_not_an_agent_claim": True,
        }


# ---------------------------------------------------------------------------
# verdict derivation
# ---------------------------------------------------------------------------


def derive_verdict(criterion_states: Sequence[str]) -> Verdict:
    """Derive a verdict from observed per-criterion states, and from nothing else.

    ``criterion_states`` holds one of ``PASSED``/``FAILED``/``NOT_RUN``/
    ``ERROR``/``REFUSED``/``UNKNOWN`` per criterion -- the observations, not a
    summary anyone wrote.

    The rules, in order:

    * no criterion at all -> ``UNPROVEN``. An empty requirement is not a proven
      one, which is the direction this project requires: absence of evidence is
      never success;
    * any ``FAILED`` -> ``REFUTED``;
    * any ``REFUSED`` (and no ``FAILED``) -> ``REFUSED``;
    * any ``UNKNOWN``/``ERROR`` (and no ``FAILED``/``REFUSED``) -> ``UNKNOWN``;
    * every state ``PASSED`` -> ``PROVEN``;
    * otherwise (a mix of ``PASSED`` and ``NOT_RUN``) -> ``PARTIAL``.

    ``PASSED`` is the only passing state. ``NOT_RUN``, ``SKIPPED``, ``ERROR`` and
    ``UNKNOWN`` are never counted as a pass.
    """

    if not criterion_states:
        return Verdict.UNPROVEN
    states = {str(state).upper() for state in criterion_states}
    if "FAILED" in states:
        return Verdict.REFUTED
    if "REFUSED" in states:
        return Verdict.REFUSED
    if "UNKNOWN" in states or "ERROR" in states:
        return Verdict.UNKNOWN
    if states == {"PASSED"}:
        return Verdict.PROVEN
    return Verdict.PARTIAL


def _overall(verdicts: Sequence[str]) -> Verdict:
    """Combine per-requirement verdicts into one project verdict.

    Deliberately pessimistic: the weakest verdict wins, and ``UNPROVEN`` is what
    an empty set produces. ``INCOMPLETE`` outranks ``UNPROVEN`` in strictness,
    because "a declared check never ran" is a stronger statement than "nothing was
    declared checkable".
    """

    if not verdicts:
        return Verdict.UNPROVEN
    rank = {
        Verdict.REFUTED.value: 0,
        Verdict.REFUSED.value: 1,
        Verdict.UNKNOWN.value: 2,
        Verdict.INCOMPLETE.value: 3,
        Verdict.PARTIAL.value: 4,
        Verdict.UNPROVEN.value: 5,
        Verdict.PROVEN.value: 6,
    }
    worst = min(verdicts, key=lambda value: rank.get(value, 4))
    return Verdict(worst)


# ---------------------------------------------------------------------------
# the builder
# ---------------------------------------------------------------------------


def expectations_satisfied(
    expectations: Mapping[str, Any],
    current_fingerprints: Mapping[str, str | None],
) -> bool:
    """Whether every declared expectation is met by the workspace as it is now.

    An expectation is ``VerificationExpectation(relative_path, exists)`` -- a
    declared, checkable statement about the operator's own workspace. "Present" is
    decided by the scanner's fingerprint being non-``None``, which is the same
    observation the understanding layer makes: a secret-recognised or unreadable
    file has no fingerprint and is therefore **not** counted as present. That is
    the fail-closed direction, and it is stated here rather than assumed.

    An empty mapping is satisfied: a declaration that names no expectation has
    nothing unmet. Whether it is *checkable* is a different question, answered by
    ``criterion_has_no_verification_expectation`` in the links.
    """

    for expectation in expectations.values():
        path = str(getattr(expectation, "relative_path", ""))
        declared = bool(getattr(expectation, "exists", False))
        present = current_fingerprints.get(path) is not None
        if declared != present:
            return False
    return True


def _criteria_state_from_record(record: Any) -> str:
    """One step record's own verification observation, in the shared vocabulary."""

    status = getattr(record, "verification_status", None)
    if isinstance(status, VerificationStatus):
        mapping = {
            VerificationStatus.PASSED: "PASSED",
            VerificationStatus.FAILED: "FAILED",
            VerificationStatus.ERROR: "UNKNOWN",
            VerificationStatus.NOT_RUN: "NOT_RUN",
        }
        return mapping[status]
    return "NOT_RUN"


class EvidenceGraphBuilder:
    """Assemble one chain from the surface's own reads.

    Every input is a *reader* the operating surface already owns, so this module
    performs no new read of the filesystem, no new ledger read, and no dispatch.
    """

    def __init__(
        self,
        *,
        project_id: str,
        goal_records: Sequence[Any] = (),
        declarations: Mapping[str, Any] | None = None,
        acceptance_specs: Mapping[str, Any] | None = None,
        step_records: Sequence[Any] = (),
        graph_run_id: str = "",
        declared_plan: Sequence[Mapping[str, Any]] = (),
        approval_fingerprints: Mapping[str, str] | None = None,
        current_fingerprints: Mapping[str, str | None] | None = None,
        baseline_fingerprints: Mapping[str, str | None] | None = None,
        operation_states: Mapping[str, str] | None = None,
        graph_status: str = "",
        stopped_reason: str = "",
        agent_claims: Sequence[Mapping[str, Any]] = (),
        baseline_present: bool = False,
    ) -> None:
        self._project_id = project_id
        self._goals = tuple(goal_records)
        self._declarations = dict(declarations or {})
        self._specs = dict(acceptance_specs or {})
        self._records = tuple(step_records)
        self._graph_run_id = graph_run_id
        self._declared_plan = tuple(declared_plan)
        self._approval_fingerprints = dict(approval_fingerprints or {})
        self._current_fingerprints = dict(current_fingerprints or {})
        self._baseline_fingerprints = dict(baseline_fingerprints or {})
        self._operation_states = dict(operation_states or {})
        self._graph_status = graph_status
        self._stopped_reason = stopped_reason
        self._agent_claims = tuple(agent_claims)
        self._baseline_present = bool(baseline_present)

    # -- helpers --------------------------------------------------------
    def _record(self, step_id: str) -> Any | None:
        for record in self._records:
            if str(getattr(record, "step_id", "")) == step_id:
                return record
        return None

    def _reviewed_paths(self) -> tuple[set[str], set[str]]:
        """(changed paths, all current paths) from the two fingerprint maps."""

        current = set(self._current_fingerprints)
        base = set(self._baseline_fingerprints)
        changed = {
            path
            for path in current | base
            if self._current_fingerprints.get(path)
            != self._baseline_fingerprints.get(path)
        }
        return changed, current

    # -- assembly -------------------------------------------------------
    def build(self) -> EvidenceGraph:
        links: list[TraceLink] = []
        goal_refs = tuple(str(getattr(record, "memory_id", "")) for record in self._goals)
        goal_refs = tuple(ref for ref in goal_refs if ref)

        # GOAL -> REQUIREMENTS
        if self._goals:
            for record in self._goals:
                memory_id = str(getattr(record, "memory_id", ""))
                if not self._declarations:
                    links.append(
                        _missing(
                            f"goal->requirements:{memory_id}",
                            Phase.GOAL,
                            Phase.REQUIREMENTS,
                            "no_requirement_declared",
                            source_ref=memory_id,
                            detail=(
                                "a goal is recorded but the composition declares no "
                                "requirement, so no accepted requirement exists to "
                                "link it to"
                            ),
                            evidence=(f"memory_id={memory_id}",),
                        )
                    )
                    continue
                for declaration_id in sorted(self._declarations):
                    links.append(
                        TraceLink(
                            link_id=f"goal->requirements:{memory_id}->{declaration_id}",
                            frm=Phase.GOAL,
                            to=Phase.REQUIREMENTS,
                            status=LinkStatus.ESTABLISHED,
                            source_ref=memory_id,
                            target_ref=declaration_id,
                            evidence=(
                                f"memory_id={memory_id}",
                                f"memory_kind={getattr(getattr(record, 'kind', ''), 'value', '')}",
                                f"declaration_id={declaration_id}",
                            ),
                            detail=(
                                "the goal record is a hypothesis; the requirement is "
                                "the operator's declaration, not the goal text"
                            ),
                        )
                    )
        else:
            for declaration_id in sorted(self._declarations):
                links.append(
                    _missing(
                        f"goal->requirements:<none>->{declaration_id}",
                        Phase.GOAL,
                        Phase.REQUIREMENTS,
                        "no_goal_recorded",
                        target_ref=declaration_id,
                        detail=(
                            "no goal has been recorded, so the chain starts at the "
                            "declaration; the missing link is shown rather than filled"
                        ),
                        evidence=(f"declaration_id={declaration_id}",),
                    )
                )

        criterion_states: dict[str, dict[str, list[str]]] = {}
        expectations_met: dict[str, bool] = {}
        step_ids_by_declaration: dict[str, list[str]] = {}
        for entry in self._declared_plan:
            definition_action = entry.get("action")
            declaration_id = ""
            if isinstance(definition_action, Mapping):
                declaration_id = str(definition_action.get("declaration_id", ""))
            step_id = str(entry.get("step_id", ""))
            if declaration_id and step_id:
                step_ids_by_declaration.setdefault(declaration_id, []).append(step_id)

        for declaration_id in sorted(self._declarations):
            spec = self._specs.get(declaration_id)
            criteria = list(getattr(spec, "criteria", ()) or ()) if spec else []
            expectations = dict(getattr(spec, "expectations", {}) or {}) if spec else {}
            delivered = expectations_satisfied(expectations, self._current_fingerprints)
            expectations_met[declaration_id] = delivered

            # REQUIREMENTS -> ACCEPTANCE_CRITERIA
            if not criteria:
                links.append(
                    _missing(
                        f"requirements->criteria:{declaration_id}",
                        Phase.REQUIREMENTS,
                        Phase.ACCEPTANCE_CRITERIA,
                        "requirement_has_no_declared_criterion",
                        source_ref=declaration_id,
                        detail=(
                            "the declaration has no acceptance spec, so nothing can "
                            "check it; this is a coverage gap, not a pass"
                        ),
                        evidence=(f"declaration_id={declaration_id}",),
                    )
                )
            for criterion in criteria:
                criterion_id = str(criterion.criterion_id)
                expectation = expectations.get(criterion.criterion_id)
                if expectation is None:
                    links.append(
                        _missing(
                            f"criteria->plan:{declaration_id}:{criterion_id}",
                            Phase.ACCEPTANCE_CRITERIA,
                            Phase.PLAN,
                            "criterion_has_no_verification_expectation",
                            source_ref=criterion_id,
                            target_ref=declaration_id,
                            detail=(
                                "the criterion declares no expectation, so no "
                                "observation could ever settle it"
                            ),
                            evidence=(f"criterion_id={criterion_id}",),
                        )
                    )
                    criterion_states.setdefault(declaration_id, {})[criterion_id] = [
                        "UNKNOWN"
                    ]
                    continue

                # CRITERIA -> PLAN
                links.append(
                    TraceLink(
                        link_id=f"criteria->plan:{declaration_id}:{criterion_id}",
                        frm=Phase.ACCEPTANCE_CRITERIA,
                        to=Phase.PLAN,
                        status=LinkStatus.ESTABLISHED,
                        source_ref=criterion_id,
                        target_ref=declaration_id,
                        evidence=(
                            f"criterion_id={criterion_id}",
                            f"expectation_path={expectation.relative_path}",
                            f"declaration_id={declaration_id}",
                        ),
                        detail="the criterion's declared expectation is the observation",
                    )
                )

                # PLAN -> STEPS
                step_ids = step_ids_by_declaration.get(declaration_id, [])
                declared = bool(getattr(expectation, "exists", False))
                expected_path = str(getattr(expectation, "relative_path", ""))
                present = expected_path in self._current_fingerprints
                state_of_expectation = "SATISFIED" if declared == present else "UNMET"
                if not step_ids:
                    links.append(
                        _missing(
                            f"plan->steps:{declaration_id}:{criterion_id}",
                            Phase.PLAN,
                            Phase.STEPS,
                            "no_step_targets_this_requirement",
                            source_ref=criterion_id,
                            target_ref=declaration_id,
                            detail=(
                                "no declared step dispatches this declaration, so the "
                                "criterion has no planned observation"
                            ),
                            evidence=(f"declaration_id={declaration_id}",),
                        )
                    )
                    criterion_states.setdefault(declaration_id, {})[criterion_id] = [
                        {
                            "SATISFIED": "PASSED",
                            "UNMET": "REFUTED",
                        }[state_of_expectation]
                    ]
                    continue

                states: list[str] = []
                for step_id in step_ids:
                    states.extend(
                        self._step_links(links, declaration_id, criterion_id, step_id)
                    )
                criterion_states.setdefault(declaration_id, {})[criterion_id] = states

        requirement_verdicts: dict[str, str] = {}
        for declaration_id in sorted(self._declarations):
            spec = self._specs.get(declaration_id)
            criteria = list(getattr(spec, "criteria", ()) or ()) if spec else []
            if not criteria:
                # Nothing checkable at all: UNPROVEN, never PROVEN.
                requirement_verdicts[declaration_id] = Verdict.UNPROVEN.value
                continue
            per_criterion = criterion_states.get(declaration_id) or {}
            if not per_criterion:
                requirement_verdicts[declaration_id] = Verdict.INCOMPLETE.value
                continue
            verdicts = [
                derive_verdict(states).value for states in per_criterion.values()
            ]
            verdict = _overall(verdicts).value
            if verdict == Verdict.UNPROVEN.value:
                # A criterion that has no observation at all keeps the requirement
                # INCOMPLETE when its declared expectation is not met. UNPROVEN
                # would say "nothing was checkable", which is a different fact.
                if not expectations_met.get(declaration_id, False):
                    verdict = Verdict.INCOMPLETE.value
            requirement_verdicts[declaration_id] = verdict

        overall = _overall(list(requirement_verdicts.values()))

        links.append(
            TraceLink(
                link_id="verification->final_status",
                frm=Phase.VERIFICATION_RESULTS,
                to=Phase.FINAL_STATUS,
                status=LinkStatus.ESTABLISHED,
                source_ref=self._graph_run_id,
                target_ref=overall.value,
                evidence=tuple(
                    f"verification_status:{record.step_id}={getattr(getattr(record, 'verification_status', ''), 'value', '')}"
                    for record in self._records
                ),
                detail=(
                    "the final status is derived from the recorded verification "
                    "observations and the requirements' own criteria; it is not read "
                    "from the graph's own completion flag and not from any claim"
                ),
                extra={
                    "graph_status_reported": self._graph_status,
                    "graph_status_used_as_proof": False,
                    "stopped_reason": self._stopped_reason,
                    "requirement_verdicts": dict(requirement_verdicts),
                },
            )
        )

        notes = [
            "a MISSING link is part of the graph: it names which transition has no "
            "evidence instead of being omitted or invented",
            "StepGraphStatus.COMPLETED is a statement about control flow and is "
            "deliberately not used as proof that a check held",
        ]
        return EvidenceGraph(
            goal_refs=goal_refs,
            links=tuple(links),
            requirement_verdicts=requirement_verdicts,
            verdict=overall,
            claimed_by_agent=self._agent_claims,
            notes=tuple(notes),
        )

    def _step_links(
        self,
        links: list[TraceLink],
        declaration_id: str,
        criterion_id: str,
        step_id: str,
    ) -> list[str]:
        """Emit the STEPS..VERIFICATION links for one step, and return its states."""

        record = self._record(step_id)
        prefix = f"{declaration_id}:{criterion_id}:{step_id}"
        if record is None:
            links.append(
                _missing(
                    f"steps->approval:{prefix}",
                    Phase.STEPS,
                    Phase.APPROVAL,
                    "no_attempt_recorded_for_this_step",
                    source_ref=step_id,
                    detail=(
                        "the plan names this step but the recorded attempt holds no "
                        "record for it, so nothing about it is established"
                    ),
                    evidence=(f"step_id={step_id}",),
                )
            )
            return ["NOT_RUN"]

        status = getattr(record, "status", None)
        parameters = tuple(getattr(record, "parameters", ()) or ())
        links.append(
            TraceLink(
                link_id=f"plan->steps:{prefix}",
                frm=Phase.PLAN,
                to=Phase.STEPS,
                status=LinkStatus.ESTABLISHED,
                source_ref=step_id,
                target_ref=declaration_id,
                evidence=(
                    f"step_id={step_id}",
                    f"declaration_id={declaration_id}",
                    f"action_fingerprint={getattr(record, 'action_fingerprint', '')}",
                ),
                detail="the declared plan names the step and the step names the declaration",
            )
        )

        approved_fingerprint = self._approval_fingerprints.get(step_id, "")
        observed_fingerprint = self._approval_fingerprints.get(f"{step_id}#observed", "")
        performing = status in {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.RUNNING,
            StepStatus.UNKNOWN,
        }
        if approved_fingerprint:
            links.append(
                TraceLink(
                    link_id=f"steps->approval:{prefix}",
                    frm=Phase.STEPS,
                    to=Phase.APPROVAL,
                    status=LinkStatus.ESTABLISHED,
                    source_ref=step_id,
                    target_ref=approved_fingerprint[:16],
                    evidence=(
                        f"step_id={step_id}",
                        "parameters=" + ", ".join(f"{n}={v}" for n, v in parameters),
                        f"intent_fingerprint={approved_fingerprint}",
                        f"performed_intent_fingerprint={observed_fingerprint}",
                        f"fingerprints_match={bool(observed_fingerprint) and observed_fingerprint == approved_fingerprint}",
                    ),
                    detail=(
                        "the approval is bound to this step's parameter set through "
                        "the intent fingerprint; the coordinator compares the same "
                        "value independently at dispatch"
                    ),
                    extra={
                        "parameters": [
                            {"name": name, "value": value} for name, value in parameters
                        ],
                        "approved_fingerprint": approved_fingerprint,
                        "observed_fingerprint": observed_fingerprint,
                        "parameter_binding_holds": bool(observed_fingerprint)
                        and observed_fingerprint == approved_fingerprint,
                    },
                )
            )
        elif status is StepStatus.WAITING_APPROVAL:
            links.append(
                _missing(
                    f"steps->approval:{prefix}",
                    Phase.STEPS,
                    Phase.APPROVAL,
                    "no_approval_decision_recorded",
                    source_ref=step_id,
                    detail=(
                        "the step is WAITING_APPROVAL: no approved fingerprint is "
                        "recorded, so the transition to an authorized action does "
                        "not exist"
                    ),
                    evidence=(
                        f"step_id={step_id}",
                        f"status={getattr(status, 'value', '')}",
                    ),
                )
            )
        elif status is StepStatus.PENDING:
            links.append(
                _missing(
                    f"steps->approval:{prefix}",
                    Phase.STEPS,
                    Phase.APPROVAL,
                    "no_approval_decision_recorded",
                    source_ref=step_id,
                    detail="the step never reached an approval decision",
                    evidence=(f"step_id={step_id}", "status=PENDING"),
                )
            )
        else:
            links.append(
                _missing(
                    f"steps->approval:{prefix}",
                    Phase.STEPS,
                    Phase.APPROVAL,
                    "no_approval_decision_recorded",
                    source_ref=step_id,
                    detail=(
                        "no approval record exists for this step; a step status is "
                        "not an approval"
                    ),
                    evidence=(
                        f"step_id={step_id}",
                        f"status={getattr(status, 'value', '')}",
                    ),
                )
            )

        # APPROVAL -> AUTHORIZED ACTIONS, read from the durable ledger state of the
        # step's own operation identity. Nothing about it is inferred from the step
        # status: the ledger either has a record or it does not.
        performed = bool(getattr(record, "step_run_id", "")) or bool(
            getattr(record, "execution_outcome", "")
        )
        ledger_state = self._operation_states.get(step_id, "")
        if performed:
            outcome = str(getattr(record, "execution_outcome", ""))
            refused = outcome.startswith("REFUSED")
            links.append(
                TraceLink(
                    link_id=f"approval->authorized:{prefix}",
                    frm=Phase.APPROVAL,
                    to=Phase.AUTHORIZED_ACTIONS,
                    status=(
                        LinkStatus.REFUSED if refused else LinkStatus.ESTABLISHED
                    ),
                    source_ref=step_id,
                    target_ref=str(getattr(record, "request_id", "")),
                    evidence=(
                        f"step_run_id={getattr(record, 'step_run_id', '')}",
                        f"request_id={getattr(record, 'request_id', '')}",
                        f"execution_outcome={outcome}",
                        f"ledger_state={ledger_state or 'no_record'}",
                    ),
                    detail=(
                        "the execution plane dispatched; the durable ledger state for "
                        "this step's operation identity is reported verbatim, "
                        "including when it is absent"
                    ),
                    extra={"ledger_state": ledger_state, "refused": refused},
                )
            )
        else:
            links.append(
                _missing(
                    f"approval->authorized:{prefix}",
                    Phase.APPROVAL,
                    Phase.AUTHORIZED_ACTIONS,
                    "no_authorized_action_recorded",
                    source_ref=step_id,
                    detail=(
                        "the execution plane was never asked to run anything for this "
                        "step, so no authorized action exists"
                    ),
                    evidence=(
                        f"step_id={step_id}",
                        f"ledger_state={ledger_state or 'no_record'}",
                    ),
                )
            )

        # AUTHORIZED ACTIONS -> FILE CHANGES.
        changed, current = self._reviewed_paths()
        if not self._baseline_present:
            links.append(
                _missing(
                    f"authorized->files:{prefix}",
                    Phase.AUTHORIZED_ACTIONS,
                    Phase.FILE_CHANGES,
                    "baseline_absent_so_changes_are_unmeasurable",
                    source_ref=step_id,
                    detail=(
                        "no baseline snapshot is recorded, so a file change caused by "
                        "this step cannot be measured; the link is missing rather "
                        "than assumed"
                    ),
                    evidence=(f"step_id={step_id}",),
                )
            )
        elif performed and changed:
            for path in sorted(changed)[:50]:
                links.append(
                    TraceLink(
                        link_id=f"authorized->files:{prefix}:{path}",
                        frm=Phase.AUTHORIZED_ACTIONS,
                        to=Phase.FILE_CHANGES,
                        status=LinkStatus.ESTABLISHED,
                        source_ref=step_id,
                        target_ref=path,
                        evidence=(
                            f"relative_path={path}",
                            f"baseline_fingerprint={self._baseline_fingerprints.get(path)}",
                            f"current_fingerprint={self._current_fingerprints.get(path)}",
                        ),
                        detail=(
                            "the scanner measured a different fingerprint for this "
                            "path than the baseline recorded"
                        ),
                    )
                )
        else:
            links.append(
                _missing(
                    f"authorized->files:{prefix}",
                    Phase.AUTHORIZED_ACTIONS,
                    Phase.FILE_CHANGES,
                    "no_file_change_observed_since_baseline",
                    source_ref=step_id,
                    detail=(
                        "no path differs from the recorded baseline, so no file "
                        "change can be attributed; this is not evidence that nothing "
                        "was written"
                    ),
                    evidence=(f"step_id={step_id}",),
                )
            )

        # FILES -> COMMANDS.
        if performed and getattr(record, "performed_executable", ""):
            links.append(
                TraceLink(
                    link_id=f"files->commands:{prefix}",
                    frm=Phase.FILE_CHANGES,
                    to=Phase.COMMANDS,
                    status=LinkStatus.ESTABLISHED,
                    source_ref=step_id,
                    target_ref=str(getattr(record, "performed_executable", "")),
                    evidence=(
                        f"performed_executable={getattr(record, 'performed_executable', '')}",
                        f"performed_working_directory={getattr(record, 'performed_working_directory', '')}",
                    ),
                    detail=(
                        "only the executable is recorded, never its argv; that is the "
                        "existing execution-record discipline"
                    ),
                )
            )
        else:
            links.append(
                _missing(
                    f"files->commands:{prefix}",
                    Phase.FILE_CHANGES,
                    Phase.COMMANDS,
                    "no_command_performed",
                    source_ref=step_id,
                    detail=(
                        "no command was performed for this step, so there is nothing "
                        "to attribute a file change to"
                    ),
                    evidence=(f"step_id={step_id}",),
                )
            )

        # COMMANDS -> VERIFICATION RESULTS.
        verification_state = _criteria_state_from_record(record)
        verification_kind = str(getattr(record, "verification_kind", ""))
        if not verification_kind:
            links.append(
                _missing(
                    f"commands->verification:{prefix}",
                    Phase.COMMANDS,
                    Phase.VERIFICATION_RESULTS,
                    "no_declared_verification_for_this_step",
                    source_ref=step_id,
                    detail=(
                        "the step declares no verification, so nothing observed this "
                        "step's result; NOT_RUN is not a pass"
                    ),
                    evidence=(f"step_id={step_id}", "verification_status=NOT_RUN"),
                )
            )
        else:
            code = ""
            if verification_state == "NOT_RUN":
                code = "step_verification_not_run"
            elif verification_state == "UNKNOWN":
                code = "step_verification_unevaluable"
            if code:
                links.append(
                    _missing(
                        f"commands->verification:{prefix}",
                        Phase.COMMANDS,
                        Phase.VERIFICATION_RESULTS,
                        code,
                        source_ref=step_id,
                        detail=(
                            "the declared check exists but produced no verdict that "
                            "settles the criterion"
                        ),
                        evidence=(
                            f"step_id={step_id}",
                            f"verification_status={getattr(getattr(record, 'verification_status', ''), 'value', '')}",
                            f"verification_observed={getattr(record, 'verification_observed', '')}",
                        ),
                    )
                )
            else:
                links.append(
                    TraceLink(
                        link_id=f"commands->verification:{prefix}",
                        frm=Phase.COMMANDS,
                        to=Phase.VERIFICATION_RESULTS,
                        status=LinkStatus.ESTABLISHED,
                        source_ref=step_id,
                        target_ref=verification_kind,
                        evidence=(
                            f"verification_kind={verification_kind}",
                            f"verification_expected={getattr(record, 'verification_expected', '')}",
                            f"verification_observed={getattr(record, 'verification_observed', '')}",
                            f"verification_status={getattr(getattr(record, 'verification_status', ''), 'value', '')}",
                            f"execution_exit_code={getattr(record, 'execution_exit_code', None)}",
                        ),
                        detail=(
                            "the verdict rests on the real exit code or a harvested "
                            "artifact, never on a claim"
                        ),
                        extra={"state": verification_state},
                    )
                )

        if verification_state == "NOT_RUN":
            return ["NOT_RUN"]
        if verification_state == "PASSED":
            return ["PASSED"]
        if verification_state == "FAILED":
            return ["FAILED"]
        return ["UNKNOWN"]
