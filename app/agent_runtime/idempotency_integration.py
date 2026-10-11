"""Integration seam that gives production runs idempotency without new authority.

This module exists so that the production entry points do not have to import the
ledger, the identity model, and the state machine directly, and so that the
safety posture is decided in exactly one place.

Two rules govern everything here:

* **Idempotency never grants authority.** A decision can refuse a run, replay a
  recorded outcome, or permit a resume. It can never permit a command, widen a
  workspace, enable network access, or authorize a tool.
* **A ledger that cannot be consulted must not silently become "no record".**
  Treating an unreadable ledger as absence is precisely how a duplicate
  execution happens. When the durable ledger is unavailable the guard falls back
  to an in-process registry and reports ``degraded=True``, so the gap is visible
  rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from app.agent_runtime.criterion_identity import TaskIdentity
from app.agent_runtime.idempotency import (
    IdempotencyDecision,
    IdempotencyEngine,
    IdempotencyError,
    IdempotencyFailureCode,
    IdempotencyLedger,
    IdempotencyVerdict,
    OperationClass,
    OperationIdentity,
    RunLifecycleState,
    RunStateRecord,
    SideEffectRecord,
    classify_side_effect,
    recover_started_effects,
)

__all__ = [
    "RepeatGuarantee",
    "RepeatDecision",
    "RunIdempotencyGuard",
    "default_idempotency_guard",
    "operation_identity_from_binding",
]


def operation_identity_from_binding(
    *,
    operation_class: OperationClass,
    idempotency_key: str,
    declaration_id: str,
    task_identity: object | None,
    criterion_identities: object = (),
) -> OperationIdentity:
    """Build the trusted operation identity for one production entry point.

    The identity is composed server-side from the operator's own declaration and
    the frozen task/criterion binding. Nothing here is read from a request, a
    decision, a plan, a tool result, or metadata.
    """
    if isinstance(task_identity, TaskIdentity):
        identity = task_identity
    else:
        raise IdempotencyError(
            IdempotencyFailureCode.TASK_IDENTITY_MISMATCH,
            "operation identity requires the trusted task identity for this run",
        )
    try:
        pairs = tuple(
            sorted(
                (
                    str(item.criterion_id),
                    str(item.criterion_fingerprint),
                )
                for item in (criterion_identities or ())
            )
        )
    except AttributeError as exc:
        raise IdempotencyError(
            IdempotencyFailureCode.CRITERION_IDENTITY_MISMATCH,
            "criterion identities must expose criterion_id and criterion_fingerprint",
        ) from exc
    return OperationIdentity(
        operation_class=operation_class,
        idempotency_key=idempotency_key,
        task_identity=identity,
        criterion_fingerprints=pairs,
    )


@dataclass
class RunIdempotencyGuard:
    """Decision seam over the durable ledger for one entry point.

    ``begin`` decides what a delivery may do. ``finish`` records the outcome so a
    later delivery replays instead of re-executing. Nothing here is an authority
    check: callers still validate the command, the tools, the approval, and the
    scope exactly as before.
    """

    engine: IdempotencyEngine
    degraded: bool = False

    @classmethod
    def create(cls, *, ledger_root: object | None = None) -> "RunIdempotencyGuard":
        ledger = IdempotencyLedger(ledger_root)
        return cls(engine=IdempotencyEngine(ledger))

    def begin(
        self,
        identity: OperationIdentity,
        *,
        requested_run_id: str,
    ) -> IdempotencyDecision:
        """Decide whether this delivery may start a new execution.

        A ledger failure degrades to a refusal rather than an unrecorded start,
        because an unrecorded start is what a duplicate execution looks like.
        """
        try:
            return self.engine.decide(identity, requested_run_id=requested_run_id)
        except (IdempotencyError, OSError) as exc:
            # Fail closed for every failure, including a malformed record: a start
            # that cannot be recorded is indistinguishable from a duplicate, so it
            # is refused rather than allowed through.
            self.degraded = True
            code = (
                exc.code
                if isinstance(exc, IdempotencyError)
                else IdempotencyFailureCode.RECORD_UNREADABLE
            )
            return IdempotencyDecision(
                verdict=IdempotencyVerdict.REFUSE,
                run_id=requested_run_id,
                failure_code=code,
                reason=(
                    "the durable idempotency ledger could not record or confirm "
                    f"this start, so a duplicate cannot be excluded ({code.value})"
                ),
            )

    def finish(
        self,
        identity: OperationIdentity,
        *,
        lifecycle: RunLifecycleState,
        attempt_number: int = 0,
        outcome_labels: tuple[str, ...] = (),
    ) -> bool:
        """Record a terminal or recoverable outcome for a claimed operation.

        Returns True when the outcome was durably recorded. A refused transition
        is **not** silently swallowed: the durable state machine refused to move
        the record, so reporting success would tell the caller its position is
        recorded when it is not. Such a refusal raises, and the caller must decide
        what to report - it must never assume the record was updated.
        """
        try:
            self.engine.ledger.update(
                identity,
                lifecycle=lifecycle,
                attempt_number=attempt_number,
                outcome_labels=outcome_labels,
            )
            return True
        except (IdempotencyError, OSError) as exc:
            # Deliberately NOT swallowed. If the durable position cannot be
            # recorded, a later delivery would read an earlier state and could be
            # admitted to run again, so the caller must be told rather than
            # handed a success it cannot rely on.
            self.degraded = True
            raise IdempotencyError(
                exc.code
                if isinstance(exc, IdempotencyError)
                else IdempotencyFailureCode.RECORD_UNREADABLE,
                "outcome could not be durably recorded for this operation",
            ) from exc

    def recorded_state(self, identity: OperationIdentity) -> object | None:
        try:
            record = self.engine.ledger.read(identity.idempotency_key)
        except (IdempotencyError, OSError):
            return None
        return record.state if record is not None else None

    def bounded_summary(self) -> Mapping[str, object]:
        return {
            "ledger_root": str(self.engine.ledger.root),
            "degraded": self.degraded,
        }

    def repeat_decision(self, identity: OperationIdentity) -> RepeatDecision:
        """What repeating this operation would mean, measured from the ledger.

        This adds **no** new durability and **no** new decision table: it reads
        the record the ledger already holds, runs the ledger's own
        ``recover_started_effects`` over it, and reports which of the documented
        repeat guarantees that evidence supports. It never writes, never
        dispatches, and never upgrades an unprovable position into a proven one.

        The rules it implements, per evidence:

        * no record -> ``FIRST_ATTEMPT``; nothing has been claimed, so this is not
          a repeat at all and ordinary authorization applies unchanged;
        * a security-terminal record (denied, policy, security failure) ->
          ``REFUSED_SECURITY_TERMINAL``; repeating would run a denied action;
        * any other terminal record -> ``REPLAY_OF_RECORD`` or, when every effect
          kind is replay-safe by construction, ``CONVERGENT_REPEAT``;
        * an effect recorded as started with no completion, whose kind is not
          replay-safe -> ``REFUSED_SIDE_EFFECT_UNKNOWN``; this is the case Block
          #6 established, where the ledger refuses with ``SIDE_EFFECT_UNKNOWN``;
        * a replay-safe uncommitted effect -> ``CONVERGENT_REPEAT``;
        * a recoverable claim that has no unprovable effect ->
          ``REQUIRES_REAPPROVAL``: the work may continue, but only through the
          normal authority path, never through this decision;
        * a ledger that cannot be read -> ``REFUSED_RECORD_UNREADABLE``, because
          an unreadable ledger must never be treated as an empty one.
        """
        try:
            record = self.engine.ledger.read(identity.idempotency_key)
        except (IdempotencyError, OSError) as exc:
            code = (
                exc.code.value
                if isinstance(exc, IdempotencyError)
                else IdempotencyFailureCode.RECORD_UNREADABLE.value
            )
            return RepeatDecision(
                guarantee=RepeatGuarantee.REFUSED_RECORD_UNREADABLE,
                reason=f"the durable ledger could not be read ({code})",
                degraded=True,
            )
        if record is None:
            return RepeatDecision(
                guarantee=RepeatGuarantee.FIRST_ATTEMPT,
                reason="no durable record exists for this operation",
            )
        if record.operation_fingerprint != identity.fingerprint:
            # The key names different work. Repeating under it would run an action
            # this record never described.
            return RepeatDecision(
                guarantee=RepeatGuarantee.REFUSED_TERMINAL,
                reason=(
                    "the durable record under this key describes a different trusted "
                    "operation, so a repeat cannot be authorized from it"
                ),
            )
        state = record.state
        if state is None:
            return RepeatDecision(
                guarantee=RepeatGuarantee.UNKNOWN,
                reason="the claim exists but carries no durable state",
            )
        recovered = recover_started_effects(state)
        unprovable = (
            recovered.unreplayable_unknown_effects()
            + recovered.unreplayable_uncommitted_effects()
        )
        if state.is_security_terminal:
            return RepeatDecision(
                guarantee=RepeatGuarantee.REFUSED_SECURITY_TERMINAL,
                reason="the operation ended in an authority failure and is terminal",
                lifecycle=state.lifecycle,
                unprovable_effects=unprovable,
            )
        if state.is_terminal:
            replay_safe = bool(state.side_effects) and all(
                effect.replay_safe or classify_side_effect(effect.kind)
                for effect in state.side_effects
            )
            return RepeatDecision(
                guarantee=(
                    RepeatGuarantee.CONVERGENT_REPEAT
                    if replay_safe
                    else RepeatGuarantee.REPLAY_OF_RECORD
                ),
                reason=(
                    "the operation already reached a terminal state; its recorded "
                    "outcome is replayed rather than re-executed"
                ),
                lifecycle=state.lifecycle,
                unprovable_effects=unprovable,
            )
        if unprovable:
            return RepeatDecision(
                guarantee=RepeatGuarantee.REFUSED_SIDE_EFFECT_UNKNOWN,
                reason=(
                    f"side effect {unprovable[0].kind.value!r} began without a "
                    "surviving completion record; whether it landed is unproven, so "
                    "it is never repeated automatically and the outcome stays UNKNOWN"
                ),
                lifecycle=state.lifecycle,
                unprovable_effects=unprovable,
            )
        if state.uncommitted_effects() or recovered.uncommitted_effects():
            # Replay-safe by construction: repeating converges on the same state.
            #
            # Two details are load-bearing here. First, the raw ``state`` is
            # consulted as well as ``recovered``: ``recover_started_effects``
            # rewrites every ``STARTED`` effect to ``UNKNOWN_AFTER_CRASH``, so
            # ``recovered.uncommitted_effects()`` is *always* empty and a check
            # against it alone can never fire. Second, this branch must precede the
            # generic recoverable-claim branch below, or a settled-in-flight
            # replay-safe effect would fall through to REQUIRES_REAPPROVAL and a
            # legitimate convergent repeat would be reported as needing
            # re-approval. The harder refusal above still wins: ``unprovable``
            # never contains a replay-safe effect.
            return RepeatDecision(
                guarantee=RepeatGuarantee.CONVERGENT_REPEAT,
                reason=(
                    "the unfinished effect is replay-safe by construction, so "
                    "repeating it converges on the same result"
                ),
                lifecycle=state.lifecycle,
                unprovable_effects=(),
            )
        if state.lifecycle in {
            RunLifecycleState.WAITING_FOR_APPROVAL,
            RunLifecycleState.CREATED,
            RunLifecycleState.INTERRUPTED,
        }:
            return RepeatDecision(
                guarantee=RepeatGuarantee.REQUIRES_REAPPROVAL,
                reason=(
                    f"the claim is recoverable from {state.lifecycle.value!r}; "
                    "continuation still requires the normal authorization path"
                ),
                lifecycle=state.lifecycle,
            )
        return RepeatDecision(
            guarantee=RepeatGuarantee.UNKNOWN,
            reason=f"unrecognised durable position {state.lifecycle.value!r}",
            lifecycle=state.lifecycle,
        )


_GUARD: RunIdempotencyGuard | None = None


class RepeatGuarantee(str, Enum):
    """What a repeat of one already-claimed operation is guaranteed to be.

    The vocabulary is deliberately about **observed evidence**, never about a
    hoped-for property. There is no ``EXACTLY_ONCE`` member, because nothing in
    this architecture can provide one: the ledger is a local filesystem record
    consulted and written without a distributed transaction, so two deliveries
    racing on a lost claim can both be admitted before either record exists.
    """

    FIRST_ATTEMPT = "FIRST_ATTEMPT"
    """No record exists, so this delivery is a first attempt of this operation."""

    REPLAY_OF_RECORD = "REPLAY_OF_RECORD"
    """A terminal outcome is durably recorded; repeat means re-read it, not re-run it."""

    CONVERGENT_REPEAT = "CONVERGENT_REPEAT"
    """Repetition is observably convergent for this effect kind (workspace writes)."""

    REFUSED_SECURITY_TERMINAL = "REFUSED_SECURITY_TERMINAL"
    """The record is an authority refusal. Repeating would re-run a denied action."""

    REFUSED_TERMINAL = "REFUSED_TERMINAL"
    """The record is terminal for another reason; repetition is not admitted."""

    REFUSED_SIDE_EFFECT_UNKNOWN = "REFUSED_SIDE_EFFECT_UNKNOWN"
    """A non-replay-safe effect started without a surviving completion record."""

    REFUSED_RECORD_UNREADABLE = "REFUSED_RECORD_UNREADABLE"
    """The ledger could not be read, so absence of a record cannot be assumed."""

    REQUIRES_REAPPROVAL = "REQUIRES_REAPPROVAL"
    """The claim is recoverable but authority must be re-established before a repeat."""

    UNKNOWN = "UNKNOWN"
    """The durable position cannot be established; the outcome stays unproven."""

    @property
    def permits_automatic_repeat(self) -> bool:
        """True only for the two cases where repeating is provably safe."""
        return self in {
            RepeatGuarantee.FIRST_ATTEMPT,
            RepeatGuarantee.CONVERGENT_REPEAT,
        }


@dataclass(frozen=True)
class RepeatDecision:
    """The measured repeat semantics of one operation, with its evidence."""

    guarantee: RepeatGuarantee
    reason: str = ""
    lifecycle: RunLifecycleState | None = None
    unprovable_effects: tuple[SideEffectRecord, ...] = ()
    degraded: bool = False

    @property
    def permits_automatic_repeat(self) -> bool:
        return self.guarantee.permits_automatic_repeat

    def to_dict(self) -> dict[str, object]:
        return {
            "guarantee": self.guarantee.value,
            "reason": self.reason,
            "lifecycle": self.lifecycle.value if self.lifecycle else "",
            "unprovable_effects": [
                effect.bounded_summary() for effect in self.unprovable_effects
            ],
            "degraded": bool(self.degraded),
            "permits_automatic_repeat": self.permits_automatic_repeat,
        }


def default_idempotency_guard() -> RunIdempotencyGuard:
    """Return the process-wide guard, created on first use."""
    global _GUARD
    if _GUARD is None:
        _GUARD = RunIdempotencyGuard.create()
    return _GUARD
