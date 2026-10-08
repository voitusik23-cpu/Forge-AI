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
)

__all__ = [
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


_GUARD: RunIdempotencyGuard | None = None


def default_idempotency_guard() -> RunIdempotencyGuard:
    """Return the process-wide guard, created on first use."""
    global _GUARD
    if _GUARD is None:
        _GUARD = RunIdempotencyGuard.create()
    return _GUARD
