"""Production idempotency and recoverable resume for Forge AI runs.

This module answers one question at a time, and never grants authority:

* **May this logical operation start a new run?** Only when its trusted
  idempotency key has no durable record bound to a *different* identity. A
  duplicate delivery of the same identity is refused a second execution.
* **May this interrupted run be resumed?** Only when it is in a recoverable
  state, its identity still matches the trusted current identity, and no side
  effect has an unknown outcome.
* **May this side effect be performed again?** Only when its recorded state says
  it was never started, or when it is explicitly replay-safe.

Design constraints that shape everything here:

* ``RunStore`` is an observation sink and stays one. It is not extended into a
  decision engine, and this module does not write through it.
* Nothing in this module creates, restores, or widens authority. A resume
  request carries *intent*; the caller must still reconstruct the frozen
  ``RunScope`` and a fresh ``AuthorizedExecution`` from trusted server-side state,
  and pass the outcome through :meth:`ResumeContract.validate_against_current_authority`
  before anything is allowed to run.
* No ``pickle``, no raw tokens, no raw stdout/stderr, no environment capture, no
  serialized authority object is ever persisted. Records hold identifiers,
  fingerprints, bounded labels, and timestamps only.

Terminology is deliberately precise. This module never claims "exactly once" or
"crash proof": it provides *at-most-once* execution per idempotency key,
*replay-safety* for a classified set of side effects, and an explicit
``UNKNOWN_AFTER_CRASH`` state for the case where a side effect began but no
completion record survived - which is never replayed automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from app.agent_runtime.criterion_identity import TaskIdentity


__all__ = [
    "AttemptIdentity",
    "Checkpoint",
    "CheckpointKind",
    "IdempotencyConflict",
    "IdempotencyDecision",
    "IdempotencyEngine",
    "IdempotencyError",
    "IdempotencyFailureCode",
    "IdempotencyLedger",
    "IdempotencyRecord",
    "IdempotencyVerdict",
    "OperationClass",
    "OperationIdentity",
    "RECORD_VERSION",
    "ResumeContract",
    "ResumeOutcome",
    "ResumeVerdict",
    "RunLifecycleState",
    "RunStateRecord",
    "SideEffectKind",
    "SideEffectRecord",
    "SideEffectState",
    "TERMINAL_LIFECYCLE_STATES",
    "RECOVERABLE_LIFECYCLE_STATES",
    "UNRESUMABLE_LIFECYCLE_STATES",
    "canonical_idempotency_key",
    "classify_side_effect",
    "default_ledger_root",
    "operation_fingerprint",
    "recover_started_effects",
    "side_effect_fingerprint",
]


# --------------------------------------------------------------------------- #
# failure taxonomy
# --------------------------------------------------------------------------- #


class IdempotencyFailureCode(str, Enum):
    """Closed taxonomy of idempotency and resume refusals."""

    KEY_INVALID = "key_invalid"
    KEY_CONFLICT = "key_conflict"
    RECORD_MALFORMED = "record_malformed"
    RECORD_UNREADABLE = "record_unreadable"
    TASK_IDENTITY_MISMATCH = "task_identity_mismatch"
    CRITERION_IDENTITY_MISMATCH = "criterion_identity_mismatch"
    OPERATION_CLASS_MISMATCH = "operation_class_mismatch"
    RUN_NOT_FOUND = "run_not_found"
    RUN_TERMINAL = "run_terminal"
    RUN_NOT_RECOVERABLE = "run_not_recoverable"
    SECURITY_FAILURE_TERMINAL = "security_failure_terminal"
    SIDE_EFFECT_UNKNOWN = "side_effect_unknown"
    AUTHORITY_MISMATCH = "authority_mismatch"
    CONCURRENT_CLAIM = "concurrent_claim"


class IdempotencyError(ValueError):
    """Raised when an idempotency request is malformed or conflicts."""

    def __init__(self, code: IdempotencyFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class IdempotencyConflict(IdempotencyError):
    """Raised when one key is presented for a different trusted identity."""


# --------------------------------------------------------------------------- #
# identity hierarchy
# --------------------------------------------------------------------------- #
#
# TaskIdentity      - what logical work is this? (trusted task content)
#      |
# OperationIdentity - which logical request of that work is this?
#      |              (operation class + trusted canonical idempotency key)
#      |
# RunIdentity       - which run record carries that operation?
#      |
# AttemptIdentity   - which execution attempt of that run is this?
#                     (attempt number + parent attempt for lineage)
#
# An action identity is derived separately per side effect from the action's own
# content, so two different actions can never collide on one identity.

_SAFE_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")


class OperationClass(str, Enum):
    """The kind of trusted server-side operation an idempotency key scopes.

    A key is only ever meaningful inside one operation class, so the same key
    string used for a task run and for a verification run is a conflict rather
    than a replay.
    """

    TASK_RUN = "task_run"
    AGENT_LOOP = "agent_loop"
    ACCEPTED_TASK = "accepted_task"
    DECLARED_VERIFICATION = "declared_verification"


def canonical_idempotency_key(raw: object) -> str:
    """Canonicalize and validate a caller-supplied idempotency key.

    The key is *input*, never authority: it selects which durable record a
    request belongs to, and nothing more. A malformed key is refused rather than
    coerced, because a silently rewritten key would silently change identity.
    """
    if not isinstance(raw, str):
        raise IdempotencyError(
            IdempotencyFailureCode.KEY_INVALID,
            "idempotency key must be a string",
        )
    candidate = raw.strip()
    if not candidate:
        raise IdempotencyError(
            IdempotencyFailureCode.KEY_INVALID,
            "idempotency key must not be empty",
        )
    if not _SAFE_KEY.match(candidate):
        raise IdempotencyError(
            IdempotencyFailureCode.KEY_INVALID,
            "idempotency key must be 8-200 chars of [A-Za-z0-9._:-]",
        )
    return candidate


def _digest(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def operation_fingerprint(
    *,
    operation_class: OperationClass,
    idempotency_key: str,
    task_identity: TaskIdentity,
    criterion_fingerprints: Sequence[tuple[str, str]] = (),
) -> str:
    """Fingerprint the logical operation a durable record belongs to.

    Covering the operation class, the canonical key, the trusted task identity,
    and every criterion identity means a record cannot be replayed for a
    different class, a different task, or a different criterion set.
    """
    if not isinstance(operation_class, OperationClass):
        raise IdempotencyError(
            IdempotencyFailureCode.OPERATION_CLASS_MISMATCH,
            "operation class must be an OperationClass",
        )
    if not isinstance(task_identity, TaskIdentity):
        raise IdempotencyError(
            IdempotencyFailureCode.TASK_IDENTITY_MISMATCH,
            "operation identity requires a trusted TaskIdentity",
        )
    return _digest(
        {
            "kind": "operation",
            "operation_class": operation_class.value,
            "idempotency_key": idempotency_key,
            "task_id": task_identity.task_id,
            "task_fingerprint": task_identity.task_fingerprint,
            "criteria": sorted(
                [list(item) for item in criterion_fingerprints], key=lambda p: p[0]
            ),
        }
    )


def side_effect_fingerprint(kind: "SideEffectKind", payload: Mapping[str, Any]) -> str:
    """Fingerprint one side-effecting action from its own bounded content.

    Used to detect "the same action again" without trusting a caller-supplied
    label, and to keep two distinct actions from sharing one identity.
    """
    if not isinstance(kind, SideEffectKind):
        raise IdempotencyError(
            IdempotencyFailureCode.RECORD_MALFORMED,
            "side effect kind must be a SideEffectKind",
        )
    return _digest({"kind": kind.value, "payload": dict(payload)})


@dataclass(frozen=True)
class OperationIdentity:
    """The logical request identity: one operation class plus one trusted key."""

    operation_class: OperationClass
    idempotency_key: str
    task_identity: TaskIdentity
    criterion_fingerprints: tuple[tuple[str, str], ...] = ()
    fingerprint: str = ""

    def __post_init__(self) -> None:
        key = canonical_idempotency_key(self.idempotency_key)
        object.__setattr__(self, "idempotency_key", key)
        if not isinstance(self.task_identity, TaskIdentity):
            raise IdempotencyError(
                IdempotencyFailureCode.TASK_IDENTITY_MISMATCH,
                "operation identity requires a trusted TaskIdentity",
            )
        expected = operation_fingerprint(
            operation_class=self.operation_class,
            idempotency_key=key,
            task_identity=self.task_identity,
            criterion_fingerprints=self.criterion_fingerprints,
        )
        if self.fingerprint and self.fingerprint != expected:
            raise IdempotencyError(
                IdempotencyFailureCode.TASK_IDENTITY_MISMATCH,
                "operation fingerprint does not match its identity",
            )
        object.__setattr__(self, "fingerprint", expected)

    @property
    def task_id(self) -> str:
        return self.task_identity.task_id

    def bounded_summary(self) -> dict[str, Any]:
        return {
            "operation_class": self.operation_class.value,
            "idempotency_key": self.idempotency_key,
            "task_id": self.task_identity.task_id,
            "task_fingerprint": self.task_identity.task_fingerprint,
            "operation_fingerprint": self.fingerprint,
            "criterion_count": len(self.criterion_fingerprints),
        }


@dataclass(frozen=True)
class AttemptIdentity:
    """One execution attempt of one run, with explicit lineage.

    ``attempt_number`` 0 is the original attempt. A resume creates the next
    number and records which attempt it continues, so lineage survives restarts
    without persisting any authority.
    """

    run_id: str
    attempt_number: int
    parent_attempt_number: int | None = None
    resumed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise IdempotencyError(
                IdempotencyFailureCode.RUN_NOT_FOUND,
                "attempt identity requires a run id",
            )
        if not isinstance(self.attempt_number, int) or self.attempt_number < 0:
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "attempt number must be a non-negative integer",
            )
        if self.resumed:
            expected_parent = self.attempt_number - 1
            if self.parent_attempt_number != expected_parent:
                raise IdempotencyError(
                    IdempotencyFailureCode.RECORD_MALFORMED,
                    "a resumed attempt must name the attempt it continues",
                )
        elif self.parent_attempt_number is not None:
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "a non-resumed attempt cannot declare a parent",
            )

    @property
    def attempt_id(self) -> str:
        return f"{self.run_id}#attempt-{self.attempt_number}"

    def bounded_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "attempt_number": self.attempt_number,
            "parent_attempt_number": self.parent_attempt_number,
            "resumed": self.resumed,
        }


# --------------------------------------------------------------------------- #
# durable run lifecycle
# --------------------------------------------------------------------------- #


class RunLifecycleState(str, Enum):
    """Canonical durable run states.

    Deliberately small. Every state is either a known non-terminal position, a
    recoverable interruption, or a terminal verdict, and the terminal set is
    split so that authority failures can never be resumed.
    """

    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    LIMIT_REACHED = "limit_reached"
    DENIED = "denied"
    SECURITY_FAILURE = "security_failure"


#: Reaching one of these means the run is over; nothing continues it.
TERMINAL_LIFECYCLE_STATES = frozenset(
    {
        RunLifecycleState.COMPLETED,
        RunLifecycleState.FAILED,
        RunLifecycleState.LIMIT_REACHED,
        RunLifecycleState.DENIED,
        RunLifecycleState.SECURITY_FAILURE,
    }
)

#: States a resume may legitimately continue from.
RECOVERABLE_LIFECYCLE_STATES = frozenset(
    {
        RunLifecycleState.CREATED,
        RunLifecycleState.INTERRUPTED,
        RunLifecycleState.WAITING_FOR_APPROVAL,
    }
)

#: States that must never be resumed, including every authority failure.
UNRESUMABLE_LIFECYCLE_STATES = frozenset(
    {
        RunLifecycleState.COMPLETED,
        RunLifecycleState.LIMIT_REACHED,
        RunLifecycleState.DENIED,
        RunLifecycleState.SECURITY_FAILURE,
    }
)

#: Harness/execution outcome labels that are authority or identity failures.
#: A run reaching one of these is terminal and can never be resumed, which is
#: how a security denial is prevented from being retried into a second attempt.
_SECURITY_OUTCOME_LABELS = frozenset(
    {
        "permission_denied",
        "policy_denied",
        "approval_rejected",
        "security_failure",
        "verification_identity_failed",
        "validation_failed",
        "identity_mismatch",
        "sandbox_violation",
        "network_denied",
    }
)

_UNKNOWN_OUTCOME_LABELS = frozenset(
    {
        # The harness records the failure but the durable snapshot may not have
        # survived; an explicit unknown is never auto-resumed.
        "unknown",
        "unknown_after_crash",
    }
)


def classify_outcome_labels(labels: Sequence[str]) -> RunLifecycleState:
    """Map recorded outcome labels onto one durable lifecycle state.

    Order matters: a security label always dominates, because a run that hit an
    authority failure is terminal even if a later label looked recoverable.
    """
    normalised = {str(label).strip().lower() for label in labels if str(label).strip()}
    if normalised & _SECURITY_OUTCOME_LABELS:
        return RunLifecycleState.SECURITY_FAILURE
    if normalised & _UNKNOWN_OUTCOME_LABELS:
        return RunLifecycleState.INTERRUPTED
    if "completed" in normalised or "success" in normalised:
        return RunLifecycleState.COMPLETED
    if "limit_reached" in normalised:
        return RunLifecycleState.LIMIT_REACHED
    if "waiting_for_approval" in normalised or "approval_waiting" in normalised:
        return RunLifecycleState.WAITING_FOR_APPROVAL
    if "failed" in normalised or "failure" in normalised or "error" in normalised:
        return RunLifecycleState.FAILED
    return RunLifecycleState.RUNNING


def validate_lifecycle_transition(
    current: "RunLifecycleState | None",
    requested: "RunLifecycleState",
    *,
    unprovable_effects: Sequence["SideEffectRecord"] = (),
) -> None:
    """Refuse a lifecycle write that the durable state machine does not allow.

    Two rules carry the security weight:

    1. **A terminal state is final.** Once a run is `COMPLETED`, `FAILED`,
       `LIMIT_REACHED`, `DENIED`, or `SECURITY_FAILURE`, no later write may move
       it anywhere else. Without this, a `SECURITY_FAILURE` could be rewritten as
       `INTERRUPTED` and then resumed, which is the denied action running anyway.
    2. **An unprovable side effect cannot be laundered into a recoverable
       state.** If an effect may or may not have landed, no write may move the run
       into a state a resume would accept. Conversion to `UNKNOWN_AFTER_CRASH` is
       deliberately *not* a lifecycle write, so it stays available.

    Every other transition between non-terminal states is allowed, because the
    run loop legitimately moves between them and inventing a stricter graph would
    add no safety.
    """
    if not isinstance(requested, RunLifecycleState):
        raise IdempotencyError(
            IdempotencyFailureCode.RECORD_MALFORMED,
            "lifecycle must be a RunLifecycleState",
        )
    if current is None:
        return
    if not isinstance(current, RunLifecycleState):
        raise IdempotencyError(
            IdempotencyFailureCode.RECORD_MALFORMED,
            "recorded lifecycle is not a RunLifecycleState",
        )
    if current is requested:
        return
    if current in TERMINAL_LIFECYCLE_STATES:
        code = (
            IdempotencyFailureCode.SECURITY_FAILURE_TERMINAL
            if current is RunLifecycleState.SECURITY_FAILURE
            else IdempotencyFailureCode.RUN_TERMINAL
        )
        raise IdempotencyError(
            code,
            (
                f"run is terminal in state {current.value!r} and cannot be moved "
                f"to {requested.value!r}; a further attempt requires a new "
                "operation identity rather than a rewritten record"
            ),
        )
    if requested in RECOVERABLE_LIFECYCLE_STATES and unprovable_effects:
        raise IdempotencyError(
            IdempotencyFailureCode.SIDE_EFFECT_UNKNOWN,
            (
                f"cannot move the run into recoverable state {requested.value!r}: "
                f"side effect {unprovable_effects[0].kind.value!r} began without a "
                "completion record, so its outcome is unproven and an automatic "
                "resume is forbidden"
            ),
        )


@dataclass(frozen=True)
class RunStateRecord:
    """Durable, authority-free description of one run's position.

    Carries identifiers, fingerprints, bounded state labels, and timestamps.
    It carries no scope, no approval, no command, no environment, and no token,
    so it cannot be used to reconstruct authority.
    """

    run_id: str
    operation_fingerprint: str
    task_id: str
    task_fingerprint: str
    lifecycle: RunLifecycleState
    attempt_number: int = 0
    criterion_fingerprints: tuple[tuple[str, str], ...] = ()
    side_effects: tuple["SideEffectRecord", ...] = ()
    updated_at: str = ""
    outcome_labels: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": RECORD_VERSION,
            "run_id": self.run_id,
            "operation_fingerprint": self.operation_fingerprint,
            "task_id": self.task_id,
            "task_fingerprint": self.task_fingerprint,
            "lifecycle": self.lifecycle.value,
            "attempt_number": int(self.attempt_number),
            "criterion_fingerprints": [list(p) for p in self.criterion_fingerprints],
            "side_effects": [s.to_dict() for s in self.side_effects],
            "updated_at": self.updated_at,
            "outcome_labels": list(self.outcome_labels),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunStateRecord":
        try:
            lifecycle = RunLifecycleState(str(data["lifecycle"]))
        except (KeyError, ValueError) as exc:
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                f"unknown lifecycle state in durable record: {data.get('lifecycle')!r}",
            ) from exc
        raw_effects = data.get("side_effects") or ()
        if not isinstance(raw_effects, (list, tuple)):
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "side_effects must be a sequence",
            )
        criteria: list[tuple[str, str]] = []
        for entry in data.get("criterion_fingerprints") or ():
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise IdempotencyError(
                    IdempotencyFailureCode.RECORD_MALFORMED,
                    "criterion_fingerprints entries must be pairs",
                )
            criteria.append((str(entry[0]), str(entry[1])))
        return cls(
            run_id=str(data.get("run_id", "")),
            operation_fingerprint=str(data.get("operation_fingerprint", "")),
            task_id=str(data.get("task_id", "")),
            task_fingerprint=str(data.get("task_fingerprint", "")),
            lifecycle=lifecycle,
            attempt_number=int(data.get("attempt_number", 0) or 0),
            criterion_fingerprints=tuple(criteria),
            side_effects=tuple(SideEffectRecord.from_dict(e) for e in raw_effects),
            updated_at=str(data.get("updated_at", "")),
            outcome_labels=tuple(str(l) for l in (data.get("outcome_labels") or ())),
        )

    @property
    def is_terminal(self) -> bool:
        return self.lifecycle in TERMINAL_LIFECYCLE_STATES

    @property
    def is_security_terminal(self) -> bool:
        return self.lifecycle in {
            RunLifecycleState.SECURITY_FAILURE,
            RunLifecycleState.DENIED,
        }

    def has_unknown_side_effect(self) -> bool:
        return any(
            effect.state is SideEffectState.UNKNOWN_AFTER_CRASH
            for effect in self.side_effects
        )

    def uncommitted_effects(self) -> tuple["SideEffectRecord", ...]:
        """Effects whose outcome is not proven by a surviving record.

        A record persisted as ``STARTED`` with no completion is exactly the
        crash window: the effect may or may not have landed. Read on its own it
        does not *claim* the process died, so a live run can still complete it;
        :func:`recover_started_effects` is what turns it into the explicit
        ``UNKNOWN_AFTER_CRASH`` verdict once recovery has established that.
        """
        return tuple(
            effect
            for effect in self.side_effects
            if effect.state is SideEffectState.STARTED
        )

    def unreplayable_unknown_effects(self) -> tuple["SideEffectRecord", ...]:
        """Effects whose outcome is unknown and which must not be replayed."""
        return tuple(
            effect
            for effect in self.side_effects
            if effect.state is SideEffectState.UNKNOWN_AFTER_CRASH
            and not effect.replay_safe
        )

    def unreplayable_uncommitted_effects(self) -> tuple["SideEffectRecord", ...]:
        """Non-replay-safe effects left ``STARTED`` without a completion record."""
        return tuple(
            effect
            for effect in self.uncommitted_effects()
            if not effect.replay_safe
        )

    def effect_for(self, fingerprint: str) -> "SideEffectRecord | None":
        for effect in self.side_effects:
            if effect.fingerprint == fingerprint:
                return effect
        return None

    def bounded_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "lifecycle": self.lifecycle.value,
            "attempt_number": self.attempt_number,
            "task_id": self.task_id,
            "operation_fingerprint": self.operation_fingerprint,
            "side_effect_count": len(self.side_effects),
            "unknown_side_effects": sum(
                1
                for e in self.side_effects
                if e.state is SideEffectState.UNKNOWN_AFTER_CRASH
            ),
            "updated_at": self.updated_at,
        }


RECORD_VERSION = 1


# --------------------------------------------------------------------------- #
# side effects
# --------------------------------------------------------------------------- #


class SideEffectKind(str, Enum):
    """The classes of side effect this runtime can perform."""

    TOOL_ACTION = "tool_action"
    HOST_PROCESS = "host_process"
    WORKSPACE_WRITE = "workspace_write"
    NETWORK_CALL = "network_call"
    PROVIDER_CALL = "provider_call"


class SideEffectState(str, Enum):
    """Durable position of one side effect.

    ``STARTED`` without a matching ``COMMITTED`` is what a crash between the
    effect and its completion record leaves behind, and it is reconstructed as
    ``UNKNOWN_AFTER_CRASH`` rather than assumed either way.
    """

    NOT_STARTED = "not_started"
    STARTED = "started"
    COMMITTED = "committed"
    FAILED = "failed"
    UNKNOWN_AFTER_CRASH = "unknown_after_crash"


#: Which kinds may be repeated when their outcome is unknown or already done.
#:
#: Only effects whose repetition is observably convergent are listed here. Host
#: processes and provider calls are absent on purpose: repeating them can
#: duplicate an irreversible action, so they require explicit operator retry.
_REPLAY_SAFE_KINDS = frozenset({SideEffectKind.WORKSPACE_WRITE})


@dataclass(frozen=True)
class SideEffectRecord:
    """Durable description of one side effect, without any of its content."""

    fingerprint: str
    kind: SideEffectKind
    state: SideEffectState
    action_id: str = ""
    attempt_number: int = 0
    replay_safe: bool = False
    started_at: str = ""
    completed_at: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "kind": self.kind.value,
            "state": self.state.value,
            "action_id": self.action_id,
            "attempt_number": int(self.attempt_number),
            "replay_safe": bool(self.replay_safe),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SideEffectRecord":
        try:
            kind = SideEffectKind(str(data["kind"]))
            state = SideEffectState(str(data["state"]))
        except (KeyError, ValueError) as exc:
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                f"unknown side effect kind or state: {data!r}",
            ) from exc
        return cls(
            fingerprint=str(data.get("fingerprint", "")),
            kind=kind,
            state=state,
            action_id=str(data.get("action_id", "")),
            attempt_number=int(data.get("attempt_number", 0) or 0),
            replay_safe=bool(data.get("replay_safe", False)),
            started_at=str(data.get("started_at", "")),
            completed_at=str(data.get("completed_at", "")),
            detail=str(data.get("detail", "")),
        )

    @property
    def may_run_again(self) -> bool:
        """Whether this effect may be performed again without operator action."""
        if self.state is SideEffectState.NOT_STARTED:
            return True
        if self.state is SideEffectState.COMMITTED:
            return self.replay_safe
        if self.state is SideEffectState.FAILED:
            return self.replay_safe
        # STARTED and UNKNOWN_AFTER_CRASH are never auto-replayed for a
        # non-replay-safe effect: whether the effect landed is unproven.
        return False

    @property
    def needs_operator_retry(self) -> bool:
        return (
            self.state is SideEffectState.UNKNOWN_AFTER_CRASH and not self.replay_safe
        ) or (self.state is SideEffectState.STARTED and not self.replay_safe)

    def bounded_summary(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "kind": self.kind.value,
            "state": self.state.value,
            "action_id": self.action_id,
            "replay_safe": self.replay_safe,
        }


def classify_side_effect(kind: SideEffectKind) -> bool:
    """Report whether a side effect kind is replay-safe by construction."""
    return kind in _REPLAY_SAFE_KINDS


def recover_started_effects(record: RunStateRecord) -> RunStateRecord:
    """Reconstruct ``UNKNOWN_AFTER_CRASH`` for effects left mid-flight.

    An effect recorded as ``STARTED`` with no completion record means the
    process died between performing it and recording the outcome. That is
    exactly the unprovable case, so it becomes ``UNKNOWN_AFTER_CRASH`` and is
    never replayed automatically.
    """
    rebuilt: list[SideEffectRecord] = []
    changed = False
    for effect in record.side_effects:
        if effect.state is SideEffectState.STARTED:
            changed = True
            rebuilt.append(
                replace(
                    effect,
                    state=SideEffectState.UNKNOWN_AFTER_CRASH,
                    detail="started but no completion record survived",
                )
            )
        else:
            rebuilt.append(effect)
    if not changed:
        return record
    return replace(record, side_effects=tuple(rebuilt))


# --------------------------------------------------------------------------- #
# durable ledger
# --------------------------------------------------------------------------- #

_SAFE_RECORD_NAME = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


@dataclass(frozen=True)
class IdempotencyRecord:
    """The durable claim and lifecycle record for one idempotency key."""

    idempotency_key: str
    operation_fingerprint: str
    operation_class: str
    run_id: str = ""
    state: RunStateRecord | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": RECORD_VERSION,
            "idempotency_key": self.idempotency_key,
            "operation_fingerprint": self.operation_fingerprint,
            "operation_class": self.operation_class,
            "run_id": self.run_id,
            "state": self.state.to_dict() if self.state else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IdempotencyRecord":
        raw_state = data.get("state")
        state = None
        if raw_state is not None:
            if not isinstance(raw_state, Mapping):
                raise IdempotencyError(
                    IdempotencyFailureCode.RECORD_MALFORMED,
                    "state must be an object or null",
                )
            # Deliberately NOT recovered here: a record must be reported exactly
            # as persisted. Whether an in-flight side effect is now unprovable is
            # a decision for the resume path (``recover_started_effects``), not a
            # side effect of reading.
            state = RunStateRecord.from_dict(raw_state)
        return cls(
            idempotency_key=str(data.get("idempotency_key", "")),
            operation_fingerprint=str(data.get("operation_fingerprint", "")),
            operation_class=str(data.get("operation_class", "")),
            run_id=str(data.get("run_id", "")),
            state=state,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )

    def bounded_summary(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "operation_class": self.operation_class,
            "run_id": self.run_id,
            "has_state": self.state is not None,
            "lifecycle": self.state.lifecycle.value if self.state else "",
        }

    def effect_for(self, fingerprint: str) -> "SideEffectRecord | None":
        """Return the recorded position of one side effect, if it has one."""
        if self.state is None:
            return None
        return self.state.effect_for(fingerprint)


class IdempotencyLedger:
    """Durable, file-per-key idempotency ledger with atomic claims.

    Storage layout::

        <root>/<sanitized-key>.json

    Concurrency guarantee: **claiming a key is atomic**. The first writer creates
    the record with ``O_EXCL``, so two simultaneous deliveries of one key cannot
    both win; the loser reads the winner's record and is treated as a replay. The
    ledger deliberately does not offer compare-and-set or a lease, so a stale
    ``RUNNING`` record is not stolen automatically - that would be an
    unprovable "did the other writer die?" guess. Such a record is reported as
    needing recovery, and recovery is subject to the resume contract.

    Reading is fail-closed: a malformed or unreadable record raises rather than
    being treated as absent, because treating corruption as absence would allow
    a duplicate execution.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._root = Path(root) if root is not None else default_ledger_root()

    @property
    def root(self) -> Path:
        return self._root

    def record_path(self, idempotency_key: str) -> Path:
        key = canonical_idempotency_key(idempotency_key)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
        if not _SAFE_RECORD_NAME.match(safe):
            raise IdempotencyError(
                IdempotencyFailureCode.KEY_INVALID,
                "idempotency key cannot be mapped to a safe record name",
            )
        return self._root / f"{safe}.json"

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # read
    # ------------------------------------------------------------------ #
    def read(self, idempotency_key: str) -> IdempotencyRecord | None:
        """Read one record, or None when the key was never claimed."""
        path = self.record_path(idempotency_key)
        if not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_UNREADABLE,
                f"idempotency record for {idempotency_key!r} could not be read",
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                f"idempotency record for {idempotency_key!r} is not valid JSON",
            ) from exc
        if not isinstance(data, Mapping):
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                f"idempotency record for {idempotency_key!r} is not an object",
            )
        record = IdempotencyRecord.from_dict(data)
        if record.idempotency_key and record.idempotency_key != idempotency_key:
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "idempotency record key does not match its location",
            )
        return record

    # ------------------------------------------------------------------ #
    # claim
    # ------------------------------------------------------------------ #
    def claim(
        self,
        identity: OperationIdentity,
        *,
        run_id: str,
        lifecycle: RunLifecycleState = RunLifecycleState.CREATED,
    ) -> tuple[IdempotencyRecord, bool]:
        """Atomically claim one idempotency key for one operation identity.

        Returns ``(record, created)``. ``created`` is True only for the writer
        that won the atomic create. An existing record is returned unchanged and
        the caller decides whether it is a replay, a conflict, or recoverable;
        this method never overwrites a claim.
        """
        if not isinstance(identity, OperationIdentity):
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "claim requires an OperationIdentity",
            )
        if not isinstance(run_id, str) or not run_id.strip():
            raise IdempotencyError(
                IdempotencyFailureCode.RUN_NOT_FOUND,
                "claim requires a run id",
            )
        path = self.record_path(identity.idempotency_key)
        self._root.mkdir(parents=True, exist_ok=True)
        now = self._now()
        state = RunStateRecord(
            run_id=run_id,
            operation_fingerprint=identity.fingerprint,
            task_id=identity.task_id,
            task_fingerprint=identity.task_identity.task_fingerprint,
            lifecycle=lifecycle,
            criterion_fingerprints=identity.criterion_fingerprints,
            updated_at=now,
        )
        record = IdempotencyRecord(
            idempotency_key=identity.idempotency_key,
            operation_fingerprint=identity.fingerprint,
            operation_class=identity.operation_class.value,
            run_id=run_id,
            state=state,
            created_at=now,
            updated_at=now,
        )
        payload = json.dumps(
            record.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, default=str
        )
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            descriptor = os.open(str(path), flags, 0o600)
        except FileExistsError:
            existing = self.read(identity.idempotency_key)
            if existing is None:
                # Lost the create race but the winner has not been observed yet.
                raise IdempotencyError(
                    IdempotencyFailureCode.CONCURRENT_CLAIM,
                    "another writer claimed this key; retry the read",
                )
            return existing, False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return record, True

    # ------------------------------------------------------------------ #
    # update
    # ------------------------------------------------------------------ #
    def update(
        self,
        identity: OperationIdentity,
        *,
        lifecycle: RunLifecycleState,
        attempt_number: int,
        side_effects: Sequence[SideEffectRecord] = (),
        append_effects: Sequence[SideEffectRecord] = (),
        outcome_labels: Sequence[str] = (),
    ) -> IdempotencyRecord:
        """Persist a new position for one claimed key.

        Refuses to touch a record bound to a different operation identity, and
        refuses to write a lifecycle state that would contradict the claim, so a
        conflict can never be laundered into a successful replay.
        """
        existing = self.read(identity.idempotency_key)
        if existing is None:
            raise IdempotencyError(
                IdempotencyFailureCode.RUN_NOT_FOUND,
                "cannot update an unclaimed idempotency key",
            )
        if existing.operation_fingerprint != identity.fingerprint:
            raise IdempotencyConflict(
                IdempotencyFailureCode.KEY_CONFLICT,
                "idempotency key is bound to a different trusted operation identity",
            )
        # Fail closed before anything is merged or written: a refused transition
        # must leave the durable record exactly as it was, not partially updated.
        if existing.state is not None:
            validate_lifecycle_transition(
                existing.state.lifecycle,
                lifecycle,
                unprovable_effects=(
                    existing.state.unreplayable_unknown_effects()
                    or existing.state.unreplayable_uncommitted_effects()
                ),
            )
        merged = list(side_effects)
        if existing.state is not None:
            seen = {e.fingerprint for e in merged}
            for effect in existing.state.side_effects:
                if effect.fingerprint not in seen:
                    merged.append(effect)
        seen_append: set[str] = set()
        for effect in append_effects:
            if effect.fingerprint in {e.fingerprint for e in merged}:
                merged = [
                    effect if e.fingerprint == effect.fingerprint else e for e in merged
                ]
            else:
                merged.append(effect)
            seen_append.add(effect.fingerprint)
        state = RunStateRecord(
            run_id=existing.run_id,
            operation_fingerprint=identity.fingerprint,
            task_id=identity.task_id,
            task_fingerprint=identity.task_identity.task_fingerprint,
            lifecycle=lifecycle,
            attempt_number=int(attempt_number),
            criterion_fingerprints=identity.criterion_fingerprints,
            side_effects=tuple(merged),
            updated_at=self._now(),
            outcome_labels=tuple(str(l) for l in outcome_labels),
        )
        record = replace(
            existing,
            state=state,
            updated_at=state.updated_at,
        )
        self._write_atomic(record)
        return record

    def _write_atomic(self, record: IdempotencyRecord) -> Path:
        """Replace a record atomically through a temporary file plus rename."""
        path = self.record_path(record.idempotency_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            record.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, default=str
        )
        descriptor, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".claim-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return path

    # ------------------------------------------------------------------ #
    # side-effect lifecycle
    # ------------------------------------------------------------------ #
    def begin_side_effect(
        self,
        identity: OperationIdentity,
        effect: SideEffectRecord,
    ) -> IdempotencyRecord:
        """Durably record that a side effect is about to happen.

        This must be written *before* the effect runs. If the process then dies,
        the surviving record says the effect started, which is what lets a
        recovery reconstruct ``UNKNOWN_AFTER_CRASH`` instead of assuming the
        effect never happened and repeating it.
        """
        if not isinstance(effect, SideEffectRecord):
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "begin_side_effect requires a SideEffectRecord",
            )
        existing = self.read(identity.idempotency_key)
        if existing is None or existing.state is None:
            raise IdempotencyError(
                IdempotencyFailureCode.RUN_NOT_FOUND,
                "cannot record a side effect for an unclaimed operation",
            )
        if existing.operation_fingerprint != identity.fingerprint:
            raise IdempotencyConflict(
                IdempotencyFailureCode.KEY_CONFLICT,
                "idempotency key is bound to a different trusted operation identity",
            )
        current = existing.state.effect_for(effect.fingerprint)
        if current is not None and current.state is SideEffectState.COMMITTED:
            raise IdempotencyError(
                IdempotencyFailureCode.SIDE_EFFECT_UNKNOWN,
                "side effect is already recorded as committed",
            )
        started = replace(
            effect,
            state=SideEffectState.STARTED,
            started_at=effect.started_at or self._now(),
            replay_safe=effect.replay_safe or classify_side_effect(effect.kind),
        )
        return self._rewrite(existing, append_effects=(started,))

    def complete_side_effect(
        self,
        identity: OperationIdentity,
        effect: SideEffectRecord,
        *,
        state: SideEffectState = SideEffectState.COMMITTED,
        detail: str = "",
    ) -> IdempotencyRecord:
        """Durably record how a side effect ended.

        Only ``COMMITTED`` and ``FAILED`` are accepted here. Marking an effect
        ``UNKNOWN_AFTER_CRASH`` by hand is refused: that state is derived from a
        missing completion record, never asserted.
        """
        if state not in {SideEffectState.COMMITTED, SideEffectState.FAILED}:
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "a completion record must be COMMITTED or FAILED",
            )
        existing = self.read(identity.idempotency_key)
        if existing is None or existing.state is None:
            raise IdempotencyError(
                IdempotencyFailureCode.RUN_NOT_FOUND,
                "cannot complete a side effect for an unclaimed operation",
            )
        if existing.operation_fingerprint != identity.fingerprint:
            raise IdempotencyConflict(
                IdempotencyFailureCode.KEY_CONFLICT,
                "idempotency key is bound to a different trusted operation identity",
            )
        completed = replace(
            effect,
            state=state,
            started_at=effect.started_at or self._now(),
            completed_at=self._now(),
            detail=detail or effect.detail,
            replay_safe=effect.replay_safe or classify_side_effect(effect.kind),
        )
        return self._rewrite(existing, append_effects=(completed,))

    def _rewrite(
        self,
        existing: IdempotencyRecord,
        *,
        lifecycle: RunLifecycleState | None = None,
        attempt_number: int | None = None,
        append_effects: Sequence[SideEffectRecord] = (),
        outcome_labels: Sequence[str] | None = None,
    ) -> IdempotencyRecord:
        """Rewrite a claimed record, merging side-effect positions by identity."""
        state = existing.state
        if state is None:
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "cannot rewrite a claim that has no state",
            )
        merged: list[SideEffectRecord] = []
        replacements = {effect.fingerprint: effect for effect in append_effects}
        for effect in state.side_effects:
            merged.append(replacements.pop(effect.fingerprint, effect))
        merged.extend(replacements.values())
        updated_state = replace(
            state,
            lifecycle=lifecycle or state.lifecycle,
            attempt_number=(
                state.attempt_number if attempt_number is None else int(attempt_number)
            ),
            side_effects=tuple(merged),
            outcome_labels=(
                state.outcome_labels
                if outcome_labels is None
                else tuple(str(l) for l in outcome_labels)
            ),
            updated_at=self._now(),
        )
        record = replace(existing, state=updated_state, updated_at=updated_state.updated_at)
        self._write_atomic(record)
        return record

    def list_keys(self) -> tuple[str, ...]:
        """Return every claimed key, sorted, for diagnostics and tests."""
        if not self._root.is_dir():
            return ()
        return tuple(
            sorted(
                entry.stem
                for entry in self._root.iterdir()
                if entry.is_file() and entry.suffix == ".json"
            )
        )


def default_ledger_root() -> Path:
    """Return the configured local idempotency-ledger root.

    Uses its own environment variable so the ledger can be relocated
    independently of run history, and never lands inside the source checkout.
    """
    configured = os.environ.get("FORGE_IDEMPOTENCY_LEDGER_ROOT")
    if configured and configured.strip():
        return Path(configured).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base and base.strip():
            return Path(base) / "ForgeAI" / "idempotency"
    return Path.home() / ".forge" / "idempotency"


# --------------------------------------------------------------------------- #
# verdicts
# --------------------------------------------------------------------------- #


class IdempotencyVerdict(str, Enum):
    """What a caller may do with one idempotency key right now."""

    START = "start"
    REPLAY = "replay"
    RESUME = "resume"
    CONFLICT = "conflict"
    REFUSE = "refuse"


@dataclass(frozen=True)
class IdempotencyDecision:
    """The server-side answer for one delivery, with its reason."""

    verdict: IdempotencyVerdict
    run_id: str = ""
    attempt_number: int = 0
    failure_code: IdempotencyFailureCode | None = None
    reason: str = ""
    state: RunStateRecord | None = None

    @property
    def may_execute(self) -> bool:
        """Only a genuinely new operation may start executing."""
        return self.verdict is IdempotencyVerdict.START

    def bounded_summary(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "run_id": self.run_id,
            "attempt_number": self.attempt_number,
            "failure_code": self.failure_code.value if self.failure_code else "",
            "lifecycle": self.state.lifecycle.value if self.state else "",
        }


class IdempotencyEngine:
    """Server-side idempotency decisions over one durable ledger.

    This is a *decision* component, not an authority component: it can refuse a
    run, replay a recorded outcome, or permit a resume, and it can never grant
    permission, widen a scope, or authorize an execution.
    """

    def __init__(self, ledger: IdempotencyLedger | None = None) -> None:
        self._ledger = ledger or IdempotencyLedger()

    @property
    def ledger(self) -> IdempotencyLedger:
        return self._ledger

    def decide(
        self,
        identity: OperationIdentity,
        *,
        requested_run_id: str,
    ) -> IdempotencyDecision:
        """Decide what one delivery of a logical operation may do.

        The key is bound to the trusted ``OperationIdentity`` computed from the
        trusted task and criterion identities, so a caller cannot reuse a key for
        different work, and cannot declare its own operation idempotent.
        """
        if not isinstance(identity, OperationIdentity):
            raise IdempotencyError(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "decide requires an OperationIdentity",
            )
        existing = self._ledger.read(identity.idempotency_key)
        if existing is None:
            self._ledger.claim(identity, run_id=requested_run_id)
            return IdempotencyDecision(
                verdict=IdempotencyVerdict.START,
                run_id=requested_run_id,
                attempt_number=0,
                reason="new idempotency key claimed for this operation",
            )
        if existing.operation_fingerprint != identity.fingerprint:
            return IdempotencyDecision(
                verdict=IdempotencyVerdict.CONFLICT,
                run_id=existing.run_id,
                failure_code=IdempotencyFailureCode.KEY_CONFLICT,
                reason=(
                    "idempotency key is already bound to a different trusted "
                    "task, operation class, or criterion set"
                ),
                state=existing.state,
            )
        state = existing.state
        if state is None:
            # A claimed key with no state is incomplete; never start a second run.
            return IdempotencyDecision(
                verdict=IdempotencyVerdict.REFUSE,
                run_id=existing.run_id,
                failure_code=IdempotencyFailureCode.RECORD_MALFORMED,
                reason="claimed key has no durable state; refusing a duplicate start",
            )
        if state.is_security_terminal:
            return IdempotencyDecision(
                verdict=IdempotencyVerdict.REFUSE,
                run_id=existing.run_id,
                attempt_number=state.attempt_number,
                failure_code=IdempotencyFailureCode.SECURITY_FAILURE_TERMINAL,
                reason="run ended in an authority failure and is terminal",
                state=state,
            )
        if state.is_terminal:
            return IdempotencyDecision(
                verdict=IdempotencyVerdict.REPLAY,
                run_id=existing.run_id,
                attempt_number=state.attempt_number,
                reason="this operation already reached a terminal state; replaying its record",
                state=state,
            )
        # An effect recorded as started without a completion is the unprovable
        # case. Both the already-recovered marker and an uncommitted non-replay-safe
        # effect must refuse a fresh execution, because re-running would duplicate
        # an action whose outcome nobody can prove.
        blocked = (
            state.unreplayable_unknown_effects()
            or state.unreplayable_uncommitted_effects()
        )
        if blocked:
            return IdempotencyDecision(
                verdict=IdempotencyVerdict.REFUSE,
                run_id=existing.run_id,
                attempt_number=state.attempt_number,
                failure_code=IdempotencyFailureCode.SIDE_EFFECT_UNKNOWN,
                reason=(
                    f"side effect {blocked[0].kind.value!r} started without a "
                    "surviving completion record; its outcome is unknown and it is "
                    "never replayed automatically"
                ),
                state=state,
            )
        return IdempotencyDecision(
            verdict=IdempotencyVerdict.RESUME,
            run_id=existing.run_id,
            attempt_number=state.attempt_number + 1,
            reason="recoverable operation is already claimed; resume rather than restart",
            state=state,
        )


# --------------------------------------------------------------------------- #
# resume contract
# --------------------------------------------------------------------------- #


class ResumeOutcome(str, Enum):
    """The result of validating one resume request against current authority."""

    ALLOWED = "allowed"
    REFUSED = "refused"


@dataclass(frozen=True)
class ResumeVerdict:
    """A resume decision plus the attempt identity it would create."""

    outcome: ResumeOutcome
    attempt: AttemptIdentity | None = None
    failure_code: IdempotencyFailureCode | None = None
    reason: str = ""
    next_action_fingerprint: str = ""

    @property
    def allowed(self) -> bool:
        return self.outcome is ResumeOutcome.ALLOWED

    def bounded_summary(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "failure_code": self.failure_code.value if self.failure_code else "",
            "attempt": self.attempt.bounded_summary() if self.attempt else None,
            "next_action_fingerprint": self.next_action_fingerprint,
        }


def _refuse(code: IdempotencyFailureCode, reason: str) -> ResumeVerdict:
    return ResumeVerdict(outcome=ResumeOutcome.REFUSED, failure_code=code, reason=reason)


@dataclass(frozen=True)
class ResumeContract:
    """Validate one resume request and derive the attempt it may create.

    Mandatory properties enforced here:

    1. the durable run must exist and belong to the requesting identity;
    2. a criterion-bound run must still match every criterion fingerprint;
    3. a terminal run - and especially an authority failure - is never resumed;
    4. an unprovable side effect blocks resume entirely;
    5. a fresh attempt identity is created, with lineage to the previous attempt;
    6. the caller must prove the current authority ceiling still covers the
       recorded operation, because authority is reconstructed from trusted
       server-side state and never from the persisted record.

    This contract restores *position*, never *permission*.
    """

    ledger: IdempotencyLedger

    def validate(
        self,
        identity: OperationIdentity,
        *,
        requested_run_id: str = "",
    ) -> ResumeVerdict:
        existing = self.ledger.read(identity.idempotency_key)
        if existing is None:
            return _refuse(
                IdempotencyFailureCode.RUN_NOT_FOUND,
                "no durable run is recorded for this idempotency key",
            )
        if existing.operation_fingerprint != identity.fingerprint:
            return _refuse(
                IdempotencyFailureCode.KEY_CONFLICT,
                "durable run belongs to a different trusted operation identity",
            )
        if requested_run_id and requested_run_id != existing.run_id:
            return _refuse(
                IdempotencyFailureCode.TASK_IDENTITY_MISMATCH,
                "requested run id does not match the durable run for this key",
            )
        state = existing.state
        if state is None:
            return _refuse(
                IdempotencyFailureCode.RECORD_MALFORMED,
                "durable run has no state; refusing to guess a resume position",
            )
        if state.task_fingerprint != identity.task_identity.task_fingerprint:
            return _refuse(
                IdempotencyFailureCode.TASK_IDENTITY_MISMATCH,
                "durable run was bound to a different task fingerprint",
            )
        if state.criterion_fingerprints != identity.criterion_fingerprints:
            return _refuse(
                IdempotencyFailureCode.CRITERION_IDENTITY_MISMATCH,
                "durable run was bound to a different criterion identity",
            )
        if state.lifecycle is RunLifecycleState.SECURITY_FAILURE:
            return _refuse(
                IdempotencyFailureCode.SECURITY_FAILURE_TERMINAL,
                "run ended in an authority failure; a resume would retry a denied action",
            )
        if state.lifecycle in UNRESUMABLE_LIFECYCLE_STATES:
            return _refuse(
                IdempotencyFailureCode.RUN_TERMINAL,
                f"run is terminal in state {state.lifecycle.value!r}",
            )
        # A run recorded as CREATED or RUNNING may still be executing in another
        # worker, so a plain retry must not be read as permission to continue it.
        if state.lifecycle in {RunLifecycleState.CREATED, RunLifecycleState.RUNNING}:
            return _refuse(
                IdempotencyFailureCode.RUN_NOT_RECOVERABLE,
                (
                    f"run is recorded as {state.lifecycle.value!r}, which may still "
                    "be live; recovery must first establish that it is interrupted"
                ),
            )
        if state.lifecycle not in RECOVERABLE_LIFECYCLE_STATES:
            return _refuse(
                IdempotencyFailureCode.RUN_NOT_RECOVERABLE,
                f"run state {state.lifecycle.value!r} is not recoverable",
            )
        # Recovery is applied here, and only here: a resume is the moment at
        # which "was this effect started?" becomes "is its outcome provable?".
        recovered = recover_started_effects(state)
        blocked = recovered.unreplayable_unknown_effects()
        if blocked:
            return _refuse(
                IdempotencyFailureCode.SIDE_EFFECT_UNKNOWN,
                (
                    "a side effect began without a surviving completion record "
                    f"({blocked[0].kind.value}); automatic resume is forbidden and "
                    "an operator retry or deterministic reconciliation is required"
                ),
            )
        attempt = AttemptIdentity(
            run_id=state.run_id,
            attempt_number=state.attempt_number + 1,
            parent_attempt_number=state.attempt_number,
            resumed=True,
        )
        return ResumeVerdict(
            outcome=ResumeOutcome.ALLOWED,
            attempt=attempt,
            reason="durable position is recoverable and identity still matches",
        )

    def validate_against_current_authority(
        self,
        verdict: ResumeVerdict,
        *,
        recorded_operation_fingerprint: str,
        current_operation_fingerprint: str,
    ) -> ResumeVerdict:
        """Prove the current authority ceiling still covers the recorded work.

        The recorded fingerprint is reconstructed from trusted server-side state
        for this resume, never read out of the persisted record as permission, so
        a resume cannot widen a workspace, a command set, a tool set, a network
        policy, or an execution profile. Any divergence refuses the resume.
        """
        if not verdict.allowed:
            return verdict
        if not recorded_operation_fingerprint or not current_operation_fingerprint:
            return _refuse(
                IdempotencyFailureCode.AUTHORITY_MISMATCH,
                "resume requires both the recorded and the current operation fingerprint",
            )
        if recorded_operation_fingerprint != current_operation_fingerprint:
            return _refuse(
                IdempotencyFailureCode.AUTHORITY_MISMATCH,
                (
                    "current authority ceiling does not reproduce the recorded "
                    "operation; a resume must not run under different authority"
                ),
            )
        return verdict


# --------------------------------------------------------------------------- #
# checkpoint model
# --------------------------------------------------------------------------- #


class CheckpointKind(str, Enum):
    """The safe, durable checkpoints a run may record.

    Each is a *completed* stage, never an intention: a checkpoint is only
    appended after the stage it names has actually finished.
    """

    DISCOVERY_COMPLETED = "discovery_completed"
    CONTEXT_COMPLETED = "context_completed"
    PLAN_COMPLETED = "plan_completed"
    DECISION_COMPLETED = "decision_completed"
    TOOL_ACTION_COMPLETED = "tool_action_completed"
    EXECUTION_COMPLETED = "execution_completed"
    VERIFICATION_COMPLETED = "verification_completed"
    ACCEPTANCE_COMPLETED = "acceptance_completed"


@dataclass(frozen=True)
class Checkpoint:
    """One bounded durable checkpoint.

    Holds identifiers, fingerprints, a bounded label, and a timestamp. It never
    holds raw output, a token, a credential, an environment, or any serialized
    authority object, so it cannot be replayed into authority.
    """

    kind: CheckpointKind
    run_id: str
    task_fingerprint: str
    attempt_number: int = 0
    criterion_fingerprints: tuple[tuple[str, str], ...] = ()
    action_id: str = ""
    status: str = ""
    detail: str = ""
    recorded_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "run_id": self.run_id,
            "task_fingerprint": self.task_fingerprint,
            "attempt_number": int(self.attempt_number),
            "criterion_fingerprints": [list(p) for p in self.criterion_fingerprints],
            "action_id": self.action_id,
            "status": self.status,
            "detail": self.detail,
            "recorded_at": self.recorded_at,
        }

    def bounded_summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "run_id": self.run_id,
            "attempt_number": self.attempt_number,
            "action_id": self.action_id,
            "status": self.status,
        }
