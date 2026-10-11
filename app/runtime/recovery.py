"""Reliable recovery v0.1: what a restart may continue, and what it may not.

WHAT THIS MODULE IS
===================

A **read-mostly planner** over infrastructure that already exists. It adds:

* discovery of unfinished attempts, by reading the existing ``RunStore``;
* a per-step decision -- continue, re-verify, needs the owner -- derived from the
  real evidence on disk;
* a **durable, atomic, per-step recovery claim** that stops two recoveries
  claiming the same step;
* the executor seam that pairs a plan with the existing
  :meth:`app.workflows.step_runner.StepGraphRunner.resume`.

It does **not** add a second store, a second ledger, a second orchestrator, a
second memory system, or a second recovery mechanism. The durable state it reads
is ``RunStore`` state written by Block #6; the dispatch authority it defers to is
``RunScope`` -> ``RunExecutor`` -> ``ExecutionCoordinator`` ->
``AuthorizedExecution`` -> ``LocalExecutionAdapter``; the repeat evidence it
consults is the existing idempotency ledger, read through
:meth:`app.agent_runtime.idempotency_integration.RunIdempotencyGuard.repeat_decision`.

WHY A PLAN IS NOT A PERMISSION
==============================

``RecoveryPlan`` says what *could* be continued. It grants nothing:

* it never builds a ``RunScope`` and never releases one;
* it never constructs an approval, an authorization, a workspace, or an adapter;
* it never dispatches a process -- :class:`RecoveryExecutor` delegates every
  dispatch to ``StepGraphRunner.resume``, which re-validates the declared plan and
  re-authorizes each step exactly as a first attempt does.

A step the planner marks ``CONTINUE`` therefore still fails closed if the ledger,
the scope, the policy, or the approval refuses it when it is actually attempted.

WHY INDETERMINATE, AND WHY NOT HEARTBEATS
=========================================

A persisted record proves only what its writer observed **at the instant of the
write**. There is no owner identity, no heartbeat, no lease, and no expiry in the
on-disk format, so a run that died and a run still executing are byte-for-byte
indistinguishable. This module therefore never probes a process and never infers
liveness from recency, and it reports a claim of progress as
:attr:`app.runtime.run_store.ExecutionClassification.INDETERMINATE`.

Building heartbeat/lease/owner-identity machinery was explicitly declined by the
project owner absent proven necessity, and the evidence that it is unnecessary for
*recovery safety* is concrete:

1. **Safety does not depend on the distinction.** The recoverable unit is one
   step, and its branch is chosen from evidence that survives a crash -- the
   ledger's side-effect record and the step's own status -- never from "is the
   other process alive". A live process and a dead one produce the *same*, and the
   same *safe*, decision.
2. **Correctness under a race is already provided a level down.** If another
   recovery is in fact running, the atomic claim in
   :class:`RecoveryClaimLog` refuses the second claimer, and if it got past that,
   the ledger's ``begin`` would answer ``RESUME``/``REFUSE`` rather than ``START``.
   A heartbeat would only make the *report* nicer, never the outcome safer.
3. **A heartbeat would add a new failure mode.** A wrongly-expired lease converts
   "unknown, ask the owner" into "safe to repeat", which is exactly the
   duplicate-side-effect bug this design exists to prevent. An unverifiable
   liveness claim is not worth that risk.

THE REPEAT GUARANTEE, STATED PRECISELY
======================================

Per action category, measured from the ledger, never assumed:

``FIRST_ATTEMPT`` (no record) -> the declared action may be dispatched, subject to
the ordinary authorization path. This is not a repeat.

``CONVERGENT_REPEAT`` (an effect kind that is replay-safe by construction -- the
ledger's ``_REPLAY_SAFE_KINDS``, i.e. workspace writes) -> repeating is permitted
and converges on the same result.

``REPLAY_OF_RECORD`` (a terminal outcome is durably recorded) -> the recorded
outcome is read back; the process is **not** re-executed.

``REFUSED_SECURITY_TERMINAL`` (denied / policy / security failure) ->
**re-approval and a new operation identity are required.** Repeating is forbidden.

``REFUSED_SIDE_EFFECT_UNKNOWN`` (a non-replay-safe effect began with no surviving
completion record) -> the outcome becomes ``UNKNOWN``. It is neither success nor
failure, it is never auto-repeated, and the owner must decide. This is Block #6's
existing ``SIDE_EFFECT_UNKNOWN`` refusal, reused rather than re-derived.

``REFUSED_TERMINAL`` (any other terminal record) -> not admitted.

``REQUIRES_REAPPROVAL`` -> continuation is possible, but only after authority is
re-established through the normal path.

WHAT IS EXPLICITLY NOT GUARANTEED
=================================

* **Not exactly-once.** The guarantee is at-most-once dispatch *per claim, as
  observed through a local filesystem ledger, and only while that ledger is
  readable*. Two processes racing on a lost claim can both be admitted in the
  window before either record exists; there is no distributed transaction.
* **Not atomic side effects.** A host process is a whole program; the ledger
  records that it was claimed and how it ended, never that its effects were atomic.
* **Not a substitute of success or failure for an unknown.** An unprovable outcome
  stays ``UNKNOWN``/``INDETERMINATE`` in every projection this module produces.
* **Not a retry engine.** Nothing here repeats a ``FAILED`` step, re-attempts a
  ``WAITING_APPROVAL`` step, or re-runs an ``UNKNOWN`` step.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import uuid4

from app.agent_runtime.idempotency import (
    IdempotencyError,
    OperationIdentity,
    SideEffectState,
)
from app.agent_runtime.idempotency_integration import (
    RepeatDecision,
    RepeatGuarantee,
    RunIdempotencyGuard,
)
from app.runtime.run_store import (
    ExecutionClassification,
    RunRecordClassification,
    RunStateSnapshot,
    RunStore,
)
from app.tools.workspace import Workspace
from app.workflows.step_graph import (
    STEP_GRAPH_PROJECT_STATE_KEY,
    StepGraphError,
    StepGraphSnapshot,
    StepGraphStateError,
    StepGraphStatus,
    StepStatus,
    VerificationStatus,
    declared_plan_of,
    graph_from_declared_plan,
    read_step_graph_record,
)

__all__ = [
    "RECOVERY_CLAIMS_KEY",
    "RecoveryClaim",
    "RecoveryClaimLog",
    "RecoveryDecision",
    "RecoveryExecutor",
    "RecoveryFinding",
    "RecoveryPlan",
    "RecoveryPlanner",
    "RecoveryRefused",
    "RecoveryStepDecision",
    "RecoveryStepRequest",
]

#: The ``project_state`` key under which recovery claims are stored. Deliberately
#: distinct from ``STEP_GRAPH_PROJECT_STATE_KEY`` so a claim write can never
#: damage the step-graph record it sits beside.
RECOVERY_CLAIMS_KEY = "recovery_claims"


class RecoveryDecision(str, Enum):
    """What a restart may do with one step. Distinct from what is *known*.

    ``ExecutionClassification`` (in :mod:`app.runtime.run_store`) answers "what
    does the record prove?". This enum answers the different question "what may be
    done next?". They are deliberately not the same vocabulary: a step whose
    outcome is unprovable and a step that is provably failed are both *known*, but
    they lead to different actions.
    """

    CONTINUE = "CONTINUE"
    """Safe to continue: nothing irreversible is in doubt for this step."""

    REVERIFY = "REVERIFY"
    """The work may have completed, but its declared check must be re-established."""

    NEEDS_OWNER = "NEEDS_OWNER"
    """No automatic action is safe; a human decides. Never a failure, never a success."""

    ALREADY_SETTLED = "ALREADY_SETTLED"
    """A recorded verdict exists and is still supported; do not re-execute."""

    BLOCKED = "BLOCKED"
    """Not dispatched, and a dependency must resolve first."""

    REFUSED = "REFUSED"
    """The authorization chain refused; no repeat is permitted."""

    UNKNOWN = "UNKNOWN"
    """The record cannot be trusted, so nothing about this step is established."""


#: Reason codes. Each names the exact evidence the decision rests on, so a
#: consumer re-derives the decision instead of trusting an opaque label.
RECOVERY_REASON_NO_RECORD = "no_persisted_step_graph_record"
RECOVERY_REASON_RECORD_UNTRUSTWORTHY = "untrustworthy_step_graph_record"
RECOVERY_REASON_DECLARED_PLAN_MISSING = "declared_plan_missing"
RECOVERY_REASON_DECLARED_PLAN_INVALID = "declared_plan_not_reconstructible"
RECOVERY_REASON_DECLARATION_UNRESOLVED = "declaration_not_in_this_build_registry"
RECOVERY_REASON_TERMINAL_SUCCESS = "recorded_terminal_success"
RECOVERY_REASON_TERMINAL_FAILURE = "recorded_terminal_failure"
RECOVERY_REASON_CANCELLED = "cancelled_before_start_never_dispatched"
RECOVERY_REASON_BLOCKED = "dependency_not_succeeded"
RECOVERY_REASON_PENDING_READY = "pending_and_every_dependency_succeeded"
RECOVERY_REASON_UNKNOWN_OUTCOME_REPLAY_SAFE = (
    "dispatched_outcome_unproven_but_effect_is_replay_safe"
)
RECOVERY_REASON_UNKNOWN_OUTCOME_NOT_REPLAY_SAFE = (
    "dispatched_outcome_unproven_and_effect_is_not_replay_safe"
)
RECOVERY_REASON_ALREADY_CLAIMED_NOT_REDELIVERABLE = (
    "dispatched_outcome_unproven_and_the_claim_is_not_redeliverable"
)
RECOVERY_REASON_VERIFICATION_NOT_RUN = "declared_verification_was_never_run"
RECOVERY_REASON_VERIFICATION_ERROR = "declared_verification_could_not_be_evaluated"
RECOVERY_REASON_VERIFICATION_PASSED = "declared_verification_passed"
RECOVERY_REASON_VERIFICATION_FAILED = "declared_verification_failed"
RECOVERY_REASON_ARTIFACT_MISSING = "declared_artifact_is_absent_from_the_workspace"
RECOVERY_REASON_ARTIFACT_PRESENT = "declared_artifact_is_present_in_the_workspace"
RECOVERY_REASON_ARTIFACT_UNCHECKABLE = "declared_artifact_cannot_be_located"
RECOVERY_REASON_APPROVAL_UNRESOLVED = "approval_required_and_unresolved"
RECOVERY_REASON_REFUSED_BY_AUTHORITY = "refused_by_the_authorization_chain"
RECOVERY_REASON_REPEATABLE_FAILURE = "failed_but_declared_repeatable"
RECOVERY_REASON_TERMINAL_OK = "terminal_verdict_still_supported"
RECOVERY_REASON_IDENTITY_UNAVAILABLE = "operation_identity_unavailable"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# durable per-step recovery claim
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryClaim:
    """One recovery's durable claim on one step of one attempt.

    A claim is **not** authority: holding it lets this recovery proceed to ask the
    runner to resume, and the runner still re-authorizes every dispatch. Its only
    job is to make two concurrent recoveries unable to *both* take responsibility
    for the same step.
    """

    graph_run_id: str
    step_id: str
    token: str
    claimed_at: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_run_id": self.graph_run_id,
            "step_id": self.step_id,
            "token": self.token,
            "claimed_at": self.claimed_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecoveryClaim":
        return cls(
            graph_run_id=str(data.get("graph_run_id", "")),
            step_id=str(data.get("step_id", "")),
            token=str(data.get("token", "")),
            claimed_at=str(data.get("claimed_at", "")),
            note=str(data.get("note", "")),
        )


class RecoveryClaimLog:
    """Atomic, durable, per-step recovery claims over the existing ``RunStore``.

    Two mechanisms, both reused rather than invented:

    * an **atomic exclusive file creation** (``O_CREAT | O_EXCL``) per
      ``(graph run, step)``, which is a real cross-process mutual exclusion built
      from the filesystem's own create-if-absent semantics -- no lock service, no
      lease, no owner registry;
    * a **`project_state` entry** written through the existing
      :meth:`app.runtime.run_store.RunStore.write_state`, which survives the
      process and is what a later recovery reads to learn that a step is already
      claimed by somebody else.

    The file is the mutual exclusion; the snapshot entry is the durable
    explanation. Neither is a second storage backend: both live inside the run
    directory ``RunStore`` already owns.
    """

    def __init__(self, store: RunStore) -> None:
        if not isinstance(store, RunStore):
            raise TypeError("RecoveryClaimLog requires a RunStore")
        self._store = store

    @property
    def store(self) -> RunStore:
        return self._store

    def claims_dir(self, graph_run_id: str) -> Path:
        return self._store.run_dir(graph_run_id) / "recovery-claims"

    def claim_path(self, graph_run_id: str, step_id: str) -> Path:
        # ``step_id`` is validated by ``StepGraph.validate`` to
        # ``[A-Za-z0-9._-]{1,64}`` before any runner will touch it, so it is a safe
        # single path component. The check is repeated here anyway, because this
        # method is callable before validation has happened.
        if not step_id or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for ch in step_id):
            raise ValueError(f"unsafe step id for a recovery claim: {step_id!r}")
        return self.claims_dir(graph_run_id) / f"{step_id}.claim"

    # -- read -----------------------------------------------------------
    def claims(self, graph_run_id: str) -> tuple[RecoveryClaim, ...]:
        """Every durable claim recorded for this attempt, sorted by step id."""
        snapshot = self._store.read_state(graph_run_id)
        if snapshot is None or not snapshot.project_state:
            return ()
        payload = snapshot.project_state.get(RECOVERY_CLAIMS_KEY)
        if not isinstance(payload, Mapping):
            return ()
        raw = payload.get("claims")
        if not isinstance(raw, (list, tuple)):
            return ()
        claims: list[RecoveryClaim] = []
        for entry in raw:
            if isinstance(entry, Mapping):
                claims.append(RecoveryClaim.from_dict(entry))
        return tuple(sorted(claims, key=lambda claim: claim.step_id))

    def claim_for(self, graph_run_id: str, step_id: str) -> RecoveryClaim | None:
        for claim in self.claims(graph_run_id):
            if claim.step_id == step_id:
                return claim
        return None

    def is_held_by_other(
        self, graph_run_id: str, step_id: str, token: str
    ) -> bool:
        """True when a claim exists and it was not made by ``token``.

        Deliberately checks **both** records. The durable snapshot entry is the
        primary evidence; the claim file is the mutual exclusion. Requiring both to
        be absent (or ours) is what makes a half-written claim fail closed rather
        than fail open.
        """
        existing = self.claim_for(graph_run_id, step_id)
        if existing is not None and existing.token != token:
            return True
        path = self.claim_path(graph_run_id, step_id)
        if not path.is_file():
            return False
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # An unreadable claim file is an existing claim. Absence must never be
            # inferred from a failed read.
            return True
        if not isinstance(recorded, Mapping):
            return True
        return str(recorded.get("token", "")) != token

    # -- write ----------------------------------------------------------
    def acquire(
        self,
        graph_run_id: str,
        step_id: str,
        *,
        token: str,
        note: str = "",
    ) -> RecoveryClaim | None:
        """Take the claim, or return ``None`` when somebody else already holds it.

        Two independent refusals protect the step, and both are needed:

        1. the **exclusive create** of the claim file (``O_CREAT | O_EXCL``), which
           is a real cross-process mutual exclusion built from the filesystem's own
           create-if-absent semantics;
        2. the **durable snapshot entry**, re-checked *after* the file exists. A
           claim file is a per-directory artifact: two recoveries whose claim
           directories are not the same directory do not see each other's files at
           all. Without this second check a foreign recovery could create its own
           file and believe it holds a step the run's own record says somebody else
           holds.

        A refusal from either leaves nothing behind: a file this call created is
        removed again, so a refused attempt can never wedge the step.
        """
        directory = self.claims_dir(graph_run_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.claim_path(graph_run_id, step_id)
        claim = RecoveryClaim(
            graph_run_id=graph_run_id,
            step_id=step_id,
            token=token,
            claimed_at=_iso_now(),
            note=note,
        )
        payload = json.dumps(claim.to_dict(), ensure_ascii=False, sort_keys=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return None
        except OSError:
            return None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                os.unlink(path)
            except OSError:
                pass
            return None

        if self.is_held_by_other(graph_run_id, step_id, token):
            # Our own freshly created file is not evidence of anybody else's claim,
            # so at this point the refusal can only have come from the durable
            # record -- which is the authoritative one. Drop the file we created.
            try:
                os.unlink(path)
            except OSError:
                pass
            return None

        try:
            self._write_claim_entry(claim)
        except BaseException:
            try:
                os.unlink(path)
            except OSError:
                pass
            return None
        return claim

    def _write_claim_entry(self, claim: RecoveryClaim) -> None:
        """Merge one claim into the run's durable record, preserving everything else."""
        snapshot = self._store.read_state(claim.graph_run_id)
        project_state: dict[str, Any] = (
            dict(snapshot.project_state)
            if snapshot is not None and snapshot.project_state
            else {}
        )
        existing: list[dict[str, Any]] = []
        payload = project_state.get(RECOVERY_CLAIMS_KEY)
        if isinstance(payload, Mapping):
            raw = payload.get("claims")
            if isinstance(raw, (list, tuple)):
                existing = [
                    dict(entry) for entry in raw if isinstance(entry, Mapping)
                ]
        merged = [entry for entry in existing if entry.get("step_id") != claim.step_id]
        merged.append(claim.to_dict())
        project_state[RECOVERY_CLAIMS_KEY] = {"claims": merged}
        self._store.write_state(
            RunStateSnapshot(
                run_id=claim.graph_run_id,
                task_id=snapshot.task_id if snapshot else "",
                state=snapshot.state if snapshot else "",
                status=snapshot.status if snapshot else "",
                attempt_number=snapshot.attempt_number if snapshot else 0,
                completed_attempt_indexes=(
                    snapshot.completed_attempt_indexes if snapshot else ()
                ),
                project_state=project_state,
                updated_at=_iso_now(),
            )
        )

    def release(self, graph_run_id: str, step_id: str, *, token: str) -> bool:
        """Release our own claim. Another token's claim is left untouched."""
        path = self.claim_path(graph_run_id, step_id)
        if path.is_file():
            try:
                recorded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                recorded = None
            if isinstance(recorded, Mapping) and str(
                recorded.get("token", "")
            ) != token:
                return False
            try:
                os.unlink(path)
            except OSError:
                return False
        try:
            self._remove_claim_entry(graph_run_id, step_id, token)
        except BaseException:
            return False
        return True

    def _remove_claim_entry(
        self, graph_run_id: str, step_id: str, token: str
    ) -> None:
        snapshot = self._store.read_state(graph_run_id)
        if snapshot is None or not snapshot.project_state:
            return
        project_state = dict(snapshot.project_state)
        payload = project_state.get(RECOVERY_CLAIMS_KEY)
        if not isinstance(payload, Mapping):
            return
        raw = payload.get("claims")
        if not isinstance(raw, (list, tuple)):
            return
        kept = [
            dict(entry)
            for entry in raw
            if isinstance(entry, Mapping)
            and not (
                entry.get("step_id") == step_id and str(entry.get("token", "")) == token
            )
        ]
        project_state[RECOVERY_CLAIMS_KEY] = {"claims": kept}
        self._store.write_state(
            RunStateSnapshot(
                run_id=graph_run_id,
                task_id=snapshot.task_id,
                state=snapshot.state,
                status=snapshot.status,
                attempt_number=snapshot.attempt_number,
                completed_attempt_indexes=snapshot.completed_attempt_indexes,
                project_state=project_state,
                updated_at=_iso_now(),
            )
        )


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryStepDecision:
    """The decision for one step, with the evidence and the repeat semantics."""

    step_id: str
    status: StepStatus
    decision: RecoveryDecision
    classification: ExecutionClassification
    reason: str
    detail: str = ""
    repeat: RepeatDecision | None = None
    unproven_effects: tuple[str, ...] = ()
    claimed: bool = False
    claim_holder: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "decision": self.decision.value,
            "classification": self.classification.value,
            "reason": self.reason,
            "detail": self.detail,
            "repeat": self.repeat.to_dict() if self.repeat else None,
            "unproven_effects": list(self.unproven_effects),
            "claimed": bool(self.claimed),
            "claim_holder": self.claim_holder,
        }


@dataclass(frozen=True)
class RecoveryPlan:
    """What a restart may do with one unfinished attempt.

    Data, not authority. Nothing in this object can dispatch, approve, or widen
    anything.
    """

    graph_run_id: str
    graph_id: str
    record_classification: ExecutionClassification
    record_reason: str = ""
    recorded_status: StepGraphStatus = StepGraphStatus.INCOMPLETE
    recoverable: bool = False
    steps: tuple[RecoveryStepDecision, ...] = ()
    blocked_reason: str = ""
    declared_plan: tuple[Mapping[str, Any], ...] = ()
    generated_at: str = ""

    def step(self, step_id: str) -> RecoveryStepDecision:
        for decision in self.steps:
            if decision.step_id == step_id:
                return decision
        raise KeyError(step_id)

    def by_decision(self, decision: RecoveryDecision) -> tuple[str, ...]:
        """Step ids with this decision, in recorded order."""
        return tuple(
            entry.step_id for entry in self.steps if entry.decision is decision
        )

    @property
    def continuation_required(self) -> bool:
        """True when anything could be continued or must be decided by the owner."""
        return any(
            entry.decision in {RecoveryDecision.CONTINUE, RecoveryDecision.REVERIFY}
            for entry in self.steps
        )

    @property
    def needs_owner(self) -> bool:
        return bool(self.by_decision(RecoveryDecision.NEEDS_OWNER)) or not self.recoverable

    def continuation_graph(self):
        """Rebuild the graph the attempt declared, or raise ``StepGraphStateError``.

        This is the only way this module produces a graph: it comes from the
        record's own declaration, never from an argument. That is what stops a
        recovery from continuing an attempt under a plan nobody declared.
        """
        return graph_from_declared_plan(self.graph_id, self.declared_plan)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_run_id": self.graph_run_id,
            "graph_id": self.graph_id,
            "record_classification": self.record_classification.value,
            "record_reason": self.record_reason,
            "recorded_status": self.recorded_status.value,
            "recoverable": bool(self.recoverable),
            "blocked_reason": self.blocked_reason,
            "generated_at": self.generated_at,
            "needs_owner": self.needs_owner,
            "continuation_required": self.continuation_required,
            "steps": [entry.to_dict() for entry in self.steps],
        }


@dataclass(frozen=True)
class RecoveryFinding:
    """One discovered attempt that is not proven finished."""

    graph_run_id: str
    classification: ExecutionClassification
    graph_status: StepGraphStatus | None = None
    graph_id: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_run_id": self.graph_run_id,
            "classification": self.classification.value,
            "graph_status": (
                self.graph_status.value if self.graph_status is not None else ""
            ),
            "graph_id": self.graph_id,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# the executor seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryStepRequest:
    """One step the caller has chosen to continue, together with its claim token.

    The caller supplies a token it got from :meth:`RecoveryPlanner.claim_step`, so a
    continuation can only happen for a step this caller actually claimed.
    """

    step_id: str
    token: str


class RecoveryExecutor:
    """Drive a claimed continuation through the *existing* runner.

    This class exists so tests and operators do not have to know how a plan maps
    onto ``StepGraphRunner.resume``. It adds no dispatch path of its own: every
    execution goes through ``resume``, and every refusal the chain produces comes
    back unchanged.
    """

    def __init__(
        self,
        *,
        planner: RecoveryPlanner,
        runner_factory: Callable[[], Any],
    ) -> None:
        self._planner = planner
        self._runner_factory = runner_factory

    def continue_plan(
        self,
        plan: RecoveryPlan,
        requests: Sequence[RecoveryStepRequest],
        *,
        step_budget: int | None = None,
        cancel_event: object | None = None,
    ) -> Any:
        """Resume ``plan`` if and only if every requested step is claimed by this token.

        Refuses before touching the runner when:

        * the plan is not recoverable at all;
        * a requested step is not in the plan;
        * a requested step's decision is not ``CONTINUE`` or ``REVERIFY``;
        * a requested step is held by a different claim token.

        Note what the last check does **not** do: it does not let a claim substitute
        for authorization. After this returns, the runner performs the full
        re-validation and re-authorization for every step it dispatches.
        """
        if not plan.recoverable:
            raise RecoveryRefused(
                f"attempt {plan.graph_run_id!r} is not recoverable: {plan.blocked_reason}"
            )
        if not requests:
            raise RecoveryRefused("a continuation must name at least one claimed step")
        for request in requests:
            decision = plan.step(request.step_id)
            if decision.decision not in {
                RecoveryDecision.CONTINUE,
                RecoveryDecision.REVERIFY,
            }:
                raise RecoveryRefused(
                    f"step {request.step_id!r} is {decision.decision.value} "
                    f"({decision.reason}); it may not be continued"
                )
            holder = self._planner.claims.claim_for(plan.graph_run_id, request.step_id)
            if holder is None or holder.token != request.token:
                raise RecoveryRefused(
                    f"step {request.step_id!r} is not claimed by the supplied token, "
                    "so this caller may not continue it"
                )
        graph = plan.continuation_graph()
        runner = self._runner_factory()
        return runner.resume(
            graph,
            plan.graph_run_id,
            step_budget=step_budget,
            cancel_event=cancel_event,
        )


class RecoveryRefused(RuntimeError):
    """A continuation was refused before any dispatch. Nothing was executed."""


# ---------------------------------------------------------------------------
# the planner
# ---------------------------------------------------------------------------


class RecoveryPlanner:
    """Discover unfinished attempts and decide, per step, what may happen next.

    Read-only: :meth:`plan` never executes, never writes a claim, and never
    dispatches. Claiming is a separate, explicit call so a caller can inspect a
    plan without taking responsibility for it.
    """

    def __init__(
        self,
        *,
        run_store: RunStore,
        idempotency_guard: RunIdempotencyGuard,
        declarations: Mapping[str, Any] | None = None,
        workspace: Workspace | None = None,
        runner_factory: Callable[[], Any] | None = None,
    ) -> None:
        if not isinstance(run_store, RunStore):
            raise TypeError("RecoveryPlanner requires a RunStore")
        if not isinstance(idempotency_guard, RunIdempotencyGuard):
            raise TypeError("RecoveryPlanner requires a RunIdempotencyGuard")
        self._store = run_store
        self._guard = idempotency_guard
        self._declarations = dict(declarations or {})
        self._workspace = workspace
        self._runner_factory = runner_factory
        self._claims = RecoveryClaimLog(run_store)

    @property
    def store(self) -> RunStore:
        return self._store

    @property
    def claims(self) -> RecoveryClaimLog:
        return self._claims

    # -- discovery ------------------------------------------------------
    def unfinished_runs(self) -> tuple[RecoveryFinding, ...]:
        """Every recorded attempt whose outcome is not proven terminal-success.

        ``RunStore.list_run_ids()`` is the index; nothing new is scanned. A record
        that carries a step-graph document is treated as an attempt. A record that
        claims in-progress is reported as ``INDETERMINATE`` -- never as alive and
        never as dead -- by the existing
        :meth:`app.runtime.run_store.RunStore.classify_record`.
        """
        findings: list[RecoveryFinding] = []
        for run_id in self._store.list_run_ids():
            snapshot = self._store.read_state(run_id)
            if snapshot is None:
                continue
            record, reason = read_step_graph_record(snapshot.project_state)
            if record is None:
                if reason in {"no_step_graph_record", "no_project_state"}:
                    continue
                findings.append(
                    RecoveryFinding(
                        graph_run_id=run_id,
                        classification=ExecutionClassification.UNKNOWN,
                        reason=reason,
                    )
                )
                continue
            if record.status is StepGraphStatus.COMPLETED:
                # Proven finished and fully verified: nothing to recover.
                continue
            classification = self._store.classify_record(run_id)
            findings.append(
                RecoveryFinding(
                    graph_run_id=run_id,
                    classification=classification.outcome,
                    graph_status=record.status,
                    graph_id=record.graph_id,
                    reason=classification.reason,
                )
            )
        return tuple(
            sorted(findings, key=lambda finding: finding.graph_run_id)
        )

    # -- planning -------------------------------------------------------
    def plan(self, graph_run_id: str) -> RecoveryPlan:
        """Build the recovery plan for one attempt. Executes nothing, writes nothing."""
        generated_at = _iso_now()
        snapshot = self._store.read_state(graph_run_id)
        if snapshot is None:
            return RecoveryPlan(
                graph_run_id=graph_run_id,
                graph_id="",
                record_classification=ExecutionClassification.UNKNOWN,
                record_reason=RECOVERY_REASON_NO_RECORD,
                recoverable=False,
                blocked_reason=RECOVERY_REASON_NO_RECORD,
                generated_at=generated_at,
            )
        record, reason = read_step_graph_record(snapshot.project_state)
        if record is None:
            return RecoveryPlan(
                graph_run_id=graph_run_id,
                graph_id="",
                record_classification=ExecutionClassification.UNKNOWN,
                record_reason=reason,
                recoverable=False,
                blocked_reason=(
                    RECOVERY_REASON_RECORD_UNTRUSTWORTHY
                    if reason not in {"no_step_graph_record", "no_project_state"}
                    else RECOVERY_REASON_NO_RECORD
                ),
                generated_at=generated_at,
            )

        classification = self._store.classify_record(graph_run_id)
        plan_blocked = ""
        graph = None
        if not record.declared_plan:
            plan_blocked = RECOVERY_REASON_DECLARED_PLAN_MISSING
        else:
            try:
                graph = graph_from_declared_plan(record.graph_id, record.declared_plan)
            except StepGraphStateError:
                plan_blocked = RECOVERY_REASON_DECLARED_PLAN_INVALID

        unknown_declarations: frozenset[str] = frozenset()
        if graph is not None and self._declarations:
            unknown_declarations = frozenset(
                definition.action.declaration_id
                for definition in graph.steps
                if definition.action.declaration_id not in self._declarations
            )

        dependencies: Mapping[str, tuple[str, ...]] = (
            graph.dependency_map() if graph is not None else {}
        )
        statuses = {r.step_id: r.status for r in record.steps}

        decisions: list[RecoveryStepDecision] = []
        for step_record in record.steps:
            decisions.append(
                self._decide_step(
                    graph_run_id=graph_run_id,
                    graph=graph,
                    record=record,
                    step_record=step_record,
                    statuses=statuses,
                    dependencies=dependencies,
                    unknown_declarations=unknown_declarations,
                    plan_blocked=plan_blocked,
                )
            )

        recoverable = bool(plan_blocked) is False and graph is not None
        return RecoveryPlan(
            graph_run_id=graph_run_id,
            graph_id=record.graph_id,
            record_classification=classification.outcome,
            record_reason=classification.reason,
            recorded_status=record.status,
            recoverable=recoverable,
            steps=tuple(decisions),
            blocked_reason=plan_blocked,
            declared_plan=tuple(record.declared_plan),
            generated_at=generated_at,
        )

    def _decide_step(
        self,
        *,
        graph_run_id: str,
        graph: Any,
        record: StepGraphSnapshot,
        step_record: Any,
        statuses: Mapping[str, StepStatus],
        dependencies: Mapping[str, tuple[str, ...]],
        unknown_declarations: frozenset[str],
        plan_blocked: str,
    ) -> RecoveryStepDecision:
        step_id = step_record.step_id
        status = step_record.status

        def build(
            decision: RecoveryDecision,
            classification: ExecutionClassification,
            reason: str,
            *,
            detail: str = "",
            repeat: RepeatDecision | None = None,
            effects: tuple[str, ...] = (),
        ) -> RecoveryStepDecision:
            existing_claim = self._claims.claim_for(graph_run_id, step_id)
            return RecoveryStepDecision(
                step_id=step_id,
                status=status,
                decision=decision,
                classification=classification,
                reason=reason,
                detail=detail,
                repeat=repeat,
                unproven_effects=effects,
                claimed=existing_claim is not None,
                claim_holder=(
                    existing_claim.token if existing_claim is not None else ""
                ),
            )

        if plan_blocked:
            return build(
                RecoveryDecision.UNKNOWN,
                ExecutionClassification.UNKNOWN,
                plan_blocked,
                detail=f"step {step_id} cannot be classified without a declared plan",
            )

        definition = None
        if graph is not None:
            try:
                definition = graph.step(step_id)
            except StepGraphError:
                return build(
                    RecoveryDecision.UNKNOWN,
                    ExecutionClassification.UNKNOWN,
                    RECOVERY_REASON_RECORD_UNTRUSTWORTHY,
                    detail=f"the declared plan has no step {step_id!r}",
                )

        if definition is not None and unknown_declarations:
            if definition.action.declaration_id in unknown_declarations:
                return build(
                    RecoveryDecision.NEEDS_OWNER,
                    ExecutionClassification.UNKNOWN,
                    RECOVERY_REASON_DECLARATION_UNRESOLVED,
                    detail=(
                        f"declaration {definition.action.declaration_id!r} is not in "
                        "this build's registry, so nothing may be dispatched through it"
                    ),
                )

        repeat = self._repeat_decision(graph, definition, graph_run_id, step_id)
        effects = (
            tuple(
                effect.bounded_summary()["kind"] for effect in repeat.unprovable_effects
            )
            if repeat is not None
            else ()
        )

        # -- terminal, in status order -----------------------------------
        if status is StepStatus.WAITING_APPROVAL:
            return build(
                RecoveryDecision.NEEDS_OWNER,
                ExecutionClassification.WAITING,
                RECOVERY_REASON_APPROVAL_UNRESOLVED,
                repeat=repeat,
                effects=effects,
            )

        if status is StepStatus.FAILED:
            if step_record.execution_outcome in {
                "APPROVAL_REJECTED",
                "PERMISSION_DENIED",
                "POLICY_DENIED",
            }:
                return build(
                    RecoveryDecision.REFUSED,
                    ExecutionClassification.TERMINAL_REFUSED,
                    RECOVERY_REASON_REFUSED_BY_AUTHORITY,
                    detail=step_record.execution_outcome,
                    repeat=repeat,
                    effects=effects,
                )
            if definition is not None and definition.repeatable:
                return build(
                    RecoveryDecision.NEEDS_OWNER,
                    ExecutionClassification.TERMINAL_FAILURE,
                    RECOVERY_REASON_REPEATABLE_FAILURE,
                    repeat=repeat,
                    effects=effects,
                )
            return build(
                RecoveryDecision.ALREADY_SETTLED,
                ExecutionClassification.TERMINAL_FAILURE,
                RECOVERY_REASON_TERMINAL_FAILURE,
                repeat=repeat,
                effects=effects,
            )

        if status is StepStatus.CANCELLED:
            # Never dispatched: the claim was never made for it.
            return build(
                RecoveryDecision.CONTINUE,
                ExecutionClassification.INDETERMINATE,
                RECOVERY_REASON_CANCELLED,
                repeat=repeat,
            )

        if status is StepStatus.BLOCKED:
            return build(
                RecoveryDecision.BLOCKED,
                ExecutionClassification.INDETERMINATE,
                RECOVERY_REASON_BLOCKED,
                detail="dependency did not succeed when the attempt stopped",
                repeat=repeat,
            )

        if status is StepStatus.PENDING:
            deps = dependencies.get(step_id, ())
            unmet = tuple(
                dependency
                for dependency in deps
                if statuses.get(dependency) is not StepStatus.SUCCEEDED
            )
            if unmet:
                return build(
                    RecoveryDecision.BLOCKED,
                    ExecutionClassification.INDETERMINATE,
                    RECOVERY_REASON_BLOCKED,
                    detail="unmet dependencies: " + ",".join(unmet),
                    repeat=repeat,
                )
            return build(
                RecoveryDecision.CONTINUE,
                ExecutionClassification.INDETERMINATE,
                RECOVERY_REASON_PENDING_READY,
                repeat=repeat,
            )

        if status is StepStatus.SUCCEEDED:
            return self._decide_succeeded(
                build=build,
                step_record=step_record,
                definition=definition,
                repeat=repeat,
                effects=effects,
            )

        # RUNNING (only reachable if a record was written directly) and UNKNOWN.
        # A step in this state has a durable claim and possibly a dispatched
        # process. Block #6's ``resume`` deliberately never re-dispatches such a
        # step: it reads the durable ``RUNNING`` back as ``UNKNOWN`` and admits no
        # second delivery under the same claim. So no recovery can *continue* it,
        # and reporting ``CONTINUE`` here would be a promise the executor cannot
        # keep -- which is why the replay-safe case is reported as ``NEEDS_OWNER``
        # with the convergent-repeat guarantee as its evidence, not as permission.
        if repeat is not None and repeat.guarantee is RepeatGuarantee.REFUSED_SECURITY_TERMINAL:
            return build(
                RecoveryDecision.REFUSED,
                ExecutionClassification.TERMINAL_REFUSED,
                RECOVERY_REASON_REFUSED_BY_AUTHORITY,
                repeat=repeat,
                effects=effects,
            )
        if repeat is not None and repeat.guarantee is RepeatGuarantee.FIRST_ATTEMPT:
            # Nothing durable was ever claimed for this step, so there is no
            # in-flight effect to doubt and ordinary forward progress applies.
            return build(
                RecoveryDecision.CONTINUE,
                ExecutionClassification.INDETERMINATE,
                RECOVERY_REASON_UNKNOWN_OUTCOME_REPLAY_SAFE,
                detail=repeat.reason,
                repeat=repeat,
                effects=effects,
            )
        if repeat is not None and repeat.guarantee in {
            RepeatGuarantee.CONVERGENT_REPEAT,
            RepeatGuarantee.REPLAY_OF_RECORD,
        }:
            return build(
                RecoveryDecision.NEEDS_OWNER,
                ExecutionClassification.INDETERMINATE,
                RECOVERY_REASON_ALREADY_CLAIMED_NOT_REDELIVERABLE,
                detail=repeat.reason,
                repeat=repeat,
                effects=effects,
            )
        if repeat is not None and repeat.guarantee is RepeatGuarantee.UNKNOWN:
            return build(
                RecoveryDecision.UNKNOWN,
                ExecutionClassification.INDETERMINATE,
                RECOVERY_REASON_IDENTITY_UNAVAILABLE,
                detail=repeat.reason,
                repeat=repeat,
                effects=effects,
            )
        return build(
            RecoveryDecision.NEEDS_OWNER,
            ExecutionClassification.INDETERMINATE,
            RECOVERY_REASON_UNKNOWN_OUTCOME_NOT_REPLAY_SAFE,
            detail=repeat.reason if repeat is not None else "",
            repeat=repeat,
            effects=effects,
        )

    def _decide_succeeded(
        self,
        *,
        build: Callable[..., RecoveryStepDecision],
        step_record: Any,
        definition: Any,
        repeat: RepeatDecision | None,
        effects: tuple[str, ...],
    ) -> RecoveryStepDecision:
        """A recorded success is re-checked against the workspace where checkable."""
        verification = definition.verification if definition is not None else None
        if verification is None:
            if step_record.verification_status is VerificationStatus.NOT_RUN:
                return build(
                    RecoveryDecision.ALREADY_SETTLED,
                    ExecutionClassification.TERMINAL_SUCCESS,
                    RECOVERY_REASON_TERMINAL_SUCCESS,
                    detail="no declared check, and the record says one was not run",
                    repeat=repeat,
                    effects=effects,
                )
            return build(
                RecoveryDecision.ALREADY_SETTLED,
                ExecutionClassification.TERMINAL_SUCCESS,
                RECOVERY_REASON_TERMINAL_SUCCESS,
                repeat=repeat,
                effects=effects,
            )

        if step_record.verification_status is VerificationStatus.PASSED:
            # The check may rest on an artifact that a later change removed. That is
            # the one thing recovery can still test about a recorded success, and
            # testing it is what stops a stale success from looking current.
            targets = verification.artifact_targets()
            if not targets:
                return build(
                    RecoveryDecision.ALREADY_SETTLED,
                    ExecutionClassification.TERMINAL_SUCCESS,
                    RECOVERY_REASON_TERMINAL_SUCCESS,
                    detail=step_record.verification_observed,
                    repeat=repeat,
                    effects=effects,
                )
            state = self._artifact_state(targets)
            if state == "absent":
                return build(
                    RecoveryDecision.REVERIFY,
                    ExecutionClassification.INDETERMINATE,
                    RECOVERY_REASON_ARTIFACT_MISSING,
                    detail=",".join(targets),
                    repeat=repeat,
                    effects=effects,
                )
            if state == "unknown":
                return build(
                    RecoveryDecision.NEEDS_OWNER,
                    ExecutionClassification.INDETERMINATE,
                    RECOVERY_REASON_ARTIFACT_UNCHECKABLE,
                    detail=",".join(targets),
                    repeat=repeat,
                    effects=effects,
                )
            return build(
                RecoveryDecision.ALREADY_SETTLED,
                ExecutionClassification.TERMINAL_SUCCESS,
                RECOVERY_REASON_ARTIFACT_PRESENT,
                detail=",".join(targets),
                repeat=repeat,
                effects=effects,
            )

        if step_record.verification_status is VerificationStatus.FAILED:
            # A recorded SUCCEEDED beside a FAILED check is a self-contradictory
            # record. It is surfaced, never resolved in the permissive direction.
            return build(
                RecoveryDecision.NEEDS_OWNER,
                ExecutionClassification.UNKNOWN,
                RECOVERY_REASON_VERIFICATION_FAILED,
                detail=step_record.verification_expected,
                repeat=repeat,
                effects=effects,
            )

        if step_record.verification_status is VerificationStatus.ERROR:
            return build(
                RecoveryDecision.NEEDS_OWNER,
                ExecutionClassification.INDETERMINATE,
                RECOVERY_REASON_VERIFICATION_ERROR,
                detail=step_record.verification_error,
                repeat=repeat,
                effects=effects,
            )

        # NOT_RUN beside a declared check: the step was recorded as successful
        # without its own declared check ever having been evaluated.
        return build(
            RecoveryDecision.NEEDS_OWNER,
            ExecutionClassification.UNKNOWN,
            RECOVERY_REASON_VERIFICATION_NOT_RUN,
            detail=step_record.verification_expected,
            repeat=repeat,
            effects=effects,
        )

    def _artifact_state(self, targets: Iterable[str]) -> str:
        """Whether the declared artifacts are present. ``unknown`` when untestable.

        Absence is only ever reported when the workspace can actually be inspected.
        No workspace was composed -> ``unknown``, never a fabricated ``absent``.
        """
        if self._workspace is None:
            return "unknown"
        root = Path(self._workspace.root)
        for target in targets:
            try:
                candidate = root.joinpath(*str(target).split("/"))
            except (ValueError, OSError):
                return "unknown"
            if not candidate.exists():
                return "absent"
        return "present"

    # -- repeat evidence -------------------------------------------------
    def _repeat_decision(
        self,
        graph: Any,
        definition: Any,
        graph_run_id: str,
        step_id: str,
    ) -> RepeatDecision | None:
        """Read the existing ledger's verdict for this step. Never writes."""
        if self._runner_factory is None or graph is None or definition is None:
            return None
        try:
            runner = self._runner_factory()
            identity = runner.operation_identity(graph, definition, graph_run_id)
        except (StepGraphError, StepGraphStateError, TypeError, ValueError, KeyError):
            return None
        if not isinstance(identity, OperationIdentity):
            return None
        try:
            return self._guard.repeat_decision(identity)
        except (IdempotencyError, OSError):
            return RepeatDecision(
                guarantee=RepeatGuarantee.REFUSED_RECORD_UNREADABLE,
                reason="the durable ledger could not be read",
                degraded=True,
            )

    # -- claiming --------------------------------------------------------
    def claim_step(
        self,
        graph_run_id: str,
        step_id: str,
        *,
        token: str | None = None,
        note: str = "",
    ) -> RecoveryClaim | None:
        """Take this recovery's claim on one step, or ``None`` when another holds it.

        Refuses to claim a step whose plan decision is neither ``CONTINUE`` nor
        ``REVERIFY``: taking a claim on a step nobody may continue would be
        meaningless state that a later reader could mistake for intent.
        """
        plan = self.plan(graph_run_id)
        try:
            decision = plan.step(step_id)
        except KeyError:
            return None
        if decision.decision not in {
            RecoveryDecision.CONTINUE,
            RecoveryDecision.REVERIFY,
        }:
            return None
        if not plan.recoverable:
            return None
        return self._claims.acquire(
            graph_run_id, step_id, token=token or uuid4().hex, note=note
        )

    def release_step(self, graph_run_id: str, step_id: str, *, token: str) -> bool:
        return self._claims.release(graph_run_id, step_id, token=token)
