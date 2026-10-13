"""Durable, local storage for Run history and state.

This module owns exactly one responsibility: persisting the observable history of
a Run so it survives process restart. It is deliberately **not** an execution
authority and **not** a resume engine:

* it never re-runs a tool, an execution request, or a provider call;
* it never restores an approval, a frozen ``RunScope``, or any permission;
* it never decides what to do next.

An interrupted Run therefore remains readable as durable history, but nothing
about it is continued automatically. Automatic resume is intentionally deferred
until side-effect idempotency is designed, because replaying an interrupted
tool or process would duplicate an irreversible action.

Storage layout (deterministic, inspectable, no database)::

    <root>/<run_id>/events.jsonl    append-only sanitized event log
    <root>/<run_id>/state.json      latest run/attempt/accounting snapshot

The event schema is the existing ``RunEvent`` projection and the existing
``sanitize_event_metadata`` policy; this module introduces no second sanitizer
and no second event model.

Read-time trust classification
------------------------------

A persisted snapshot proves only what its writer observed **at the moment of the
write**. It carries a state label, a status label, an attempt number, and the
time of the write — and nothing that establishes whether the process that wrote
it is still alive. There is no heartbeat, no owner or process identity, no lease,
and no expiry. A record that says ``RUNNING`` therefore means "the writer believed
this run was in progress when it last wrote", which is a strictly weaker claim
than "this run is executing now".

:meth:`RunStore.classify_record` turns that distinction into an explicit,
inspectable value (:class:`ExecutionClassification`) computed from the record
itself. It never guesses, never polls a process, and never upgrades an absent or
unrecognised field into a known result: an in-progress claim whose liveness the
record cannot establish is reported as ``INDETERMINATE``, and a record whose
fields contradict each other, or which carries no recognisable outcome at all, is
reported as ``UNKNOWN``. The classification is derived at read time, so no
existing ``state.json`` needs rewriting and no second store exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.orchestrator.models import EventType, Run
from app.orchestrator.trace import RunEvent, sanitize_event_metadata

__all__ = [
    "RunStore",
    "RunStateSnapshot",
    "PersistedRun",
    "default_run_store_root",
    "project_run_store_root",
    "default_store_enabled",
    "ExecutionClassification",
    "RunRecordClassification",
]

# Run ids are used as directory names, so they are restricted to a conservative
# charset. ``"."`` and ``".."`` satisfy a character-class pattern while naming the
# directory itself and its parent -- which is how a crafted id escaped the storage
# root -- so they are refused explicitly, and ``run_dir`` re-proves containment
# instead of trusting the pattern alone.
_SAFE_RUN_ID = re.compile(r"^(?!\.\.?$)[A-Za-z0-9._-]{1,128}$")


def is_safe_run_id(run_id: object) -> bool:
    """True when ``run_id`` may be used as a directory name inside the store root."""

    return isinstance(run_id, str) and _SAFE_RUN_ID.match(run_id) is not None

_ENV_ROOT_VAR = "FORGE_RUN_STORE_ROOT"
_ENV_ENABLE_VAR = "FORGE_RUN_STORE_ENABLED"


def default_store_enabled() -> bool:
    """Report whether default (uninjected) Run history persistence is enabled.

    Persistence is opt-in so that ordinary library use and test execution never
    write runtime state as a side effect. The API service injects a store
    explicitly and therefore does not depend on this flag.
    """
    return str(os.environ.get(_ENV_ENABLE_VAR, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def default_run_store_root() -> Path:
    """Return the configured local storage root.

    Resolution order:

    1. ``FORGE_RUN_STORE_ROOT`` when set, which is how tests redirect storage to
       a temporary directory;
    2. a platform-appropriate per-user location, so durable history never lands
       inside the source checkout and cannot be committed by accident.

    The location is deliberately outside the repository: run history is runtime
    state, not source.
    """
    configured = os.environ.get(_ENV_ROOT_VAR)
    if configured and configured.strip():
        return Path(configured).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base and base.strip():
            return Path(base) / "ForgeAI" / "runs"
    home = Path.home()
    return home / ".forge" / "runs"


def project_run_store_root(workspace_root: "str | os.PathLike[str]") -> Path:
    """Return the run-store root that belongs to ONE workspace.

    ``default_run_store_root()`` is per USER, not per project. Two projects on one
    machine therefore resolved to the same directory, and a ``graph_run_id`` used in
    project A was found by project B and returned as its own ``COMPLETED`` result while
    B's artifact was never created (measured: cross-project replay in D1 and D2).

    The space is derived from the CANONICAL workspace path -- the trusted context the
    application already holds, never a value supplied by the caller -- and the directory
    name is a digest, so the absolute path is disclosed neither in a directory name nor
    in a public response. ``resolve`` folds symlinks, junctions, ``..`` and relative
    components, and ``casefold`` folds case, so the two spellings of one directory on a
    case-insensitive filesystem cannot become two spaces. The mapping is deterministic,
    so a project finds its own history again after a restart.

    Records written before this change live directly under
    ``default_run_store_root()``. They are deliberately NOT consulted here: a record
    with no provable project owner must not be replayed as the current project's work,
    and no fallback into that directory is provided for execution.
    """

    canonical = Path(workspace_root).expanduser().resolve(strict=False)
    digest = hashlib.sha256(str(canonical).casefold().encode("utf-8")).hexdigest()[:32]
    return default_run_store_root() / "projects" / f"ws-{digest}"


# ----------------------------------------------------------------------
# read-time trust classification
# ----------------------------------------------------------------------
class ExecutionClassification(str, Enum):
    """What a persisted run record actually proves about the run's outcome.

    This is deliberately **not** a new ``RunState`` member. ``RunState`` is the
    orchestrator's *live, in-memory* lifecycle and is compared by identity across
    the code base (``run.state == RunState.COMPLETED`` decides success in the API
    service), so adding ``CANCELLED``/``TIMEOUT`` to it would silently create a
    third, unhandled live state in every one of those comparisons. The executor
    produces no such state and this module does not invent one. Cancellation and
    timeout are *outcome labels* that arrive through ``status`` (for example
    ``ExecutionOutcomeStatus.EXECUTION_TIMEOUT``) or through the run state
    ``FAILED``; the categories below make them expressible on the read side
    without touching the enum the rest of the system consumes.

    A ``str`` enum, so a value serialises as its own name and compares equal to
    the plain string ``"INDETERMINATE"``.

    Values
    ------
    ``UNKNOWN``
        The record proves no outcome: it carries no recognised state or status,
        its fields contradict each other, or it holds no snapshot at all.
    ``INDETERMINATE``
        The record claims the run was in progress (or claims it was interrupted)
        but establishes nothing about the present. A claim of progress is a
        statement about the *writer's* past, and nothing in the record proves the
        writer is still alive.
    ``TERMINAL_SUCCESS``
        The record carries a terminal success label.
    ``TERMINAL_FAILURE``
        The record carries a terminal failure label.
    ``TERMINAL_TIMEOUT``
        The record carries an explicit timeout label.
    ``TERMINAL_CANCELLED``
        The record carries an explicit cancellation label.
    ``TERMINAL_REFUSED``
        The record carries a refusal/denial label: permission, policy, or
        approval rejection. Distinct from ``TERMINAL_FAILURE`` so a refusal is
        never flattened into a plain failure — and never into a success.
    ``WAITING``
        The record carries a waiting-for-approval label.
    """

    UNKNOWN = "UNKNOWN"
    INDETERMINATE = "INDETERMINATE"
    TERMINAL_SUCCESS = "TERMINAL_SUCCESS"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    TERMINAL_TIMEOUT = "TERMINAL_TIMEOUT"
    TERMINAL_CANCELLED = "TERMINAL_CANCELLED"
    TERMINAL_REFUSED = "TERMINAL_REFUSED"
    WAITING = "WAITING"


# Reason-code vocabulary. Each code names the evidence that produced the
# classification, so a consumer can re-derive the decision from the record
# instead of trusting an opaque label.
CLASSIFICATION_REASON_INTERRUPTED_FLAG = "interrupted_flag_set"
CLASSIFICATION_REASON_INTERRUPTED_FLAG_AND_IN_PROGRESS = (
    "interrupted_flag_set_with_in_progress_state"
)
CLASSIFICATION_REASON_CONTRADICTORY_EVIDENCE = "contradictory_state_and_status"
CLASSIFICATION_REASON_IN_PROGRESS_CLAIM = "claimed_in_progress"
CLASSIFICATION_REASON_DECLARED_OUTCOME = "declared_outcome_label"
CLASSIFICATION_REASON_EMPTY_FIELDS = "empty_state_and_status"
CLASSIFICATION_REASON_UNRECOGNISED_FIELDS = "unrecognised_state_and_status"
CLASSIFICATION_REASON_NO_SNAPSHOT = "no_persisted_snapshot"
CLASSIFICATION_REASON_FINAL_EVENT = "final_event"
CLASSIFICATION_REASON_STEP_GRAPH_STATUS = "step_graph_status"

# Classification carried by the *status* label. ``APPROVAL_WAITING`` and
# ``APPROVAL_REJECTED`` are execution-outcome labels, not run states.
_STATUS_CLASSIFICATION: Mapping[str, ExecutionClassification] = {
    "SUCCESS": ExecutionClassification.TERMINAL_SUCCESS,
    "COMPLETED": ExecutionClassification.TERMINAL_SUCCESS,
    "WAITING": ExecutionClassification.WAITING,
    "WAITING_FOR_APPROVAL": ExecutionClassification.WAITING,
    "APPROVAL_WAITING": ExecutionClassification.WAITING,
    "APPROVAL_REJECTED": ExecutionClassification.TERMINAL_REFUSED,
    "FAILED": ExecutionClassification.TERMINAL_FAILURE,
    "FAILURE": ExecutionClassification.TERMINAL_FAILURE,
    "EXECUTION_FAILURE": ExecutionClassification.TERMINAL_FAILURE,
    "LIMIT_REACHED": ExecutionClassification.TERMINAL_FAILURE,
    "ERROR": ExecutionClassification.TERMINAL_FAILURE,
    "EXECUTION_ERROR": ExecutionClassification.TERMINAL_FAILURE,
    "TIMEOUT": ExecutionClassification.TERMINAL_TIMEOUT,
    "EXECUTION_TIMEOUT": ExecutionClassification.TERMINAL_TIMEOUT,
    "CANCELLED": ExecutionClassification.TERMINAL_CANCELLED,
    "CANCELED": ExecutionClassification.TERMINAL_CANCELLED,
    "DENIED": ExecutionClassification.TERMINAL_REFUSED,
    "POLICY_DENIED": ExecutionClassification.TERMINAL_REFUSED,
    "PERMISSION_DENIED": ExecutionClassification.TERMINAL_REFUSED,
}

# Classification carried by the *run state*. Only the six real ``RunState``
# members plus the harness's ``INITIAL`` appear, so an unrecognised state stays
# unrecognised rather than being folded into a neighbouring meaning.
_STATE_CLASSIFICATION: Mapping[str, ExecutionClassification] = {
    "COMPLETED": ExecutionClassification.TERMINAL_SUCCESS,
    "WAITING_FOR_APPROVAL": ExecutionClassification.WAITING,
    "FAILED": ExecutionClassification.TERMINAL_FAILURE,
    "RUNNING": ExecutionClassification.INDETERMINATE,
    "REVISING": ExecutionClassification.INDETERMINATE,
    "CREATED": ExecutionClassification.INDETERMINATE,
    "INITIAL": ExecutionClassification.INDETERMINATE,
}

_IN_PROGRESS_STATES = frozenset({"RUNNING", "REVISING", "CREATED", "INITIAL"})

#: Classification carried by a *step-graph* record's derived status.
#:
#: A managed multi-step attempt is written through this same store with its own
#: ``state``/``status`` deliberately left empty, because that run id is
#: bookkeeping for one attempt and not a live ``Run``. Its verdict lives in the
#: step-graph document instead, and it is a *derived* verdict: Block #6's
#: ``derive_graph_status`` reads it from the per-step records with a stated
#: pessimistic precedence, so a graph status of ``INDETERMINATE`` genuinely means
#: "at least one step's outcome is not established" and never a liveness guess.
#: Reading it here keeps a single classifier instead of teaching every consumer a
#: second vocabulary.
_STEP_GRAPH_STATUS_CLASSIFICATION: Mapping[str, "ExecutionClassification"] = {
    "COMPLETED": ExecutionClassification.TERMINAL_SUCCESS,
    "FAILED": ExecutionClassification.TERMINAL_FAILURE,
    # CANCELLED is the one graph status that is genuinely two cases in Block #6:
    # cancellation requested before start (nothing was dispatched) and a step that
    # was dispatched and never settled. Both are reported as CANCELLED today, so
    # the honest read-side answer is INDETERMINATE: nothing in the record proves
    # that no process ran.
    "CANCELLED": ExecutionClassification.INDETERMINATE,
    "REFUSED": ExecutionClassification.TERMINAL_REFUSED,
    "INDETERMINATE": ExecutionClassification.INDETERMINATE,
    "INCOMPLETE": ExecutionClassification.INDETERMINATE,
}

# The outcomes that state what happened, as opposed to WAITING / INDETERMINATE /
# UNKNOWN, which state only that the outcome is not established yet.
_TERMINAL_OUTCOMES = frozenset(
    {
        ExecutionClassification.TERMINAL_SUCCESS,
        ExecutionClassification.TERMINAL_FAILURE,
        ExecutionClassification.TERMINAL_TIMEOUT,
        ExecutionClassification.TERMINAL_CANCELLED,
        ExecutionClassification.TERMINAL_REFUSED,
    }
)

# Polarity: whether a terminal label claims the run succeeded or claims it did
# not. Two labels of *opposite* polarity can never be reconciled, so they are a
# genuine contradiction. Specificity only ever breaks a tie *within* one polarity:
# the generic run state ``FAILED`` beside a status such as ``EXECUTION_TIMEOUT``
# says "it failed" and "here is why", so the specific label wins and the reason is
# preserved rather than discarded.
_POLARITY_SUCCEEDED = 1
_POLARITY_DID_NOT_SUCCEED = 0

_TERMINAL_POLARITY: Mapping[ExecutionClassification, int] = {
    ExecutionClassification.TERMINAL_SUCCESS: _POLARITY_SUCCEEDED,
    ExecutionClassification.TERMINAL_FAILURE: _POLARITY_DID_NOT_SUCCEED,
    ExecutionClassification.TERMINAL_TIMEOUT: _POLARITY_DID_NOT_SUCCEED,
    ExecutionClassification.TERMINAL_CANCELLED: _POLARITY_DID_NOT_SUCCEED,
    ExecutionClassification.TERMINAL_REFUSED: _POLARITY_DID_NOT_SUCCEED,
}

# Relative specificity of a terminal outcome, used only within one polarity.
_TERMINAL_SPECIFICITY: Mapping[ExecutionClassification, int] = {
    ExecutionClassification.TERMINAL_FAILURE: 1,
    ExecutionClassification.TERMINAL_TIMEOUT: 2,
    ExecutionClassification.TERMINAL_CANCELLED: 2,
    ExecutionClassification.TERMINAL_REFUSED: 2,
    ExecutionClassification.TERMINAL_SUCCESS: 2,
}

# Terminal event names, in priority order. Events are consulted only when no
# snapshot exists, because a snapshot is the writer's own final projection and is
# strictly more informative than the event trail.
_TERMINAL_EVENT_CLASSIFICATION: tuple[tuple[str, ExecutionClassification], ...] = (
    ("ENGINEERING_RUN_FAILED", ExecutionClassification.TERMINAL_FAILURE),
    ("HARNESS_FAILED", ExecutionClassification.TERMINAL_FAILURE),
    ("HARNESS_LIMIT_REACHED", ExecutionClassification.TERMINAL_FAILURE),
    ("RUN_FAILED", ExecutionClassification.TERMINAL_FAILURE),
    ("ENGINEERING_RUN_COMPLETED", ExecutionClassification.TERMINAL_SUCCESS),
    ("HARNESS_COMPLETED", ExecutionClassification.TERMINAL_SUCCESS),
    ("RUN_COMPLETED", ExecutionClassification.TERMINAL_SUCCESS),
)

# At most this many of the most recent events are scanned when classifying.
_CLASSIFICATION_EVENT_WINDOW = 50


@dataclass(frozen=True)
class RunRecordClassification:
    """The classification of one persisted run record, with its provenance.

    ``reason`` is a stable code from the ``CLASSIFICATION_REASON_*`` vocabulary
    and ``detail`` names the exact field/value pair that produced the result, so
    the decision is derivable from the record rather than hard-coded.
    """

    outcome: ExecutionClassification
    reason: str
    detail: str = ""
    evidence: tuple[str, ...] = ()

    @property
    def is_terminal(self) -> bool:
        """True only for outcomes that prove the run finished one way or another."""
        return self.outcome.value.startswith("TERMINAL_")

    @property
    def is_success(self) -> bool:
        """True only for a proven terminal success.

        An unknown, indeterminate, waiting, interrupted, refused, or failed
        record is never a success, so no caller can accidentally read a refusal
        or an unknown as one.
        """
        return self.outcome is ExecutionClassification.TERMINAL_SUCCESS

    @property
    def is_known(self) -> bool:
        """True when the record proves any outcome beyond "we cannot tell"."""
        return self.outcome not in {
            ExecutionClassification.UNKNOWN,
            ExecutionClassification.INDETERMINATE,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reason": self.reason,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "is_terminal": self.is_terminal,
            "is_success": self.is_success,
        }


def _step_graph_status_of(snapshot: RunStateSnapshot | None) -> str:
    """The derived status of a step-graph attempt recorded in this snapshot.

    Returns ``""`` when the snapshot holds no step-graph document, so an ordinary
    ``Run`` record is unaffected. The document is read structurally rather than
    through :mod:`app.workflows.step_graph`, because that module imports the
    execution plane and this one is deliberately dependency-light; the only field
    consulted is the status label, and an unrecognised shape yields ``""``.
    """
    if snapshot is None or not snapshot.project_state:
        return ""
    payload = snapshot.project_state.get("step_graph")
    if not isinstance(payload, Mapping):
        return ""
    status = payload.get("status")
    return "" if status is None else str(status)


def _normalize_label(value: object) -> str:
    """Normalize one persisted label for vocabulary lookup.

    Persisted labels are free-form strings, so an absent, empty, or differently
    cased value must not raise and must not be coerced into a known meaning.
    """
    if value is None:
        return ""
    text = str(getattr(value, "value", value)).strip().upper()
    return text


def _classify_label(
    value: object, vocabulary: Mapping[str, ExecutionClassification]
) -> ExecutionClassification | None:
    """Return the classification a label declares, or None when unrecognised."""
    return vocabulary.get(_normalize_label(value))


def _terminal_event_classification(
    event_types: Iterable[object],
) -> tuple[ExecutionClassification, str] | None:
    """Classify from the event trail's last terminal event, if it has one.

    Only the most recent window is scanned and only the **last** terminal event
    decides, because the trail is append-ordered and a later terminal event
    supersedes an earlier one.
    """
    names = [
        _normalize_label(getattr(event, "event_type", event)) for event in event_types
    ]
    for name in names[-_CLASSIFICATION_EVENT_WINDOW:][::-1]:
        for event_name, outcome in _TERMINAL_EVENT_CLASSIFICATION:
            if name == event_name:
                return outcome, event_name
    return None


def classify_run_record(
    *,
    state: object = "",
    status: object = "",
    interrupted: bool = False,
    event_types: Iterable[object] = (),
    has_snapshot: bool = True,
    step_graph_status: object = "",
) -> RunRecordClassification:
    """Classify a persisted run record from the evidence it actually carries.

    Inputs are the record's own fields, never wall-clock time and never a process
    probe: a record cannot be trusted *more* because it is recent, and this module
    does not invent a lease, heartbeat, or expiry that the on-disk format does not
    have. ``has_snapshot=False`` means no ``state.json`` exists, so the event trail
    is the only evidence.

    A claimed-in-progress record is **not** automatically trusted, and is not
    reported as actively running. ``state='RUNNING'`` records a belief held at one
    past instant, with no owner identity, heartbeat, lease, or terminal marker, so
    a run whose owning process died and a run still executing are byte-for-byte
    indistinguishable on disk. The honest read-side answer is therefore
    ``INDETERMINATE`` with the reason naming the exact claim that was found.

    The label vocabularies are disjoint by classification. Two *terminal* labelled
    fields that disagree are resolved by polarity first: "succeeded" beside
    "failed" is a self-contradiction, so the result is ``UNKNOWN`` and never
    success. Within one polarity the more specific label wins, so the generic run
    state ``FAILED`` beside ``EXECUTION_TIMEOUT``, ``CANCELLED``, or
    ``POLICY_DENIED`` keeps the reason instead of discarding it. A field that
    claims progress, or a field that says the run is waiting for approval, does
    **not** conflict with a terminal label — that pairing is an ordinary temporal
    snapshot (the run had not finished when the other label was written), so the
    terminal label wins. An unrecognised value is only ever neutral: it can neither
    promote a record to success nor demote a real terminal outcome to unknown.
    """
    state_label = _normalize_label(state)
    status_label = _normalize_label(status)
    evidence: list[str] = []
    if state_label:
        evidence.append(f"state={state_label}")
    if status_label:
        evidence.append(f"status={status_label}")
    if interrupted:
        evidence.append("interrupted=True")
    if not has_snapshot:
        evidence.append("snapshot=absent")

    # 1. An explicit interruption claim. It is checked first because it is a
    #    direct statement that the recorded work was cut short.
    if interrupted:
        if state_label in _IN_PROGRESS_STATES:
            return RunRecordClassification(
                outcome=ExecutionClassification.INDETERMINATE,
                reason=CLASSIFICATION_REASON_INTERRUPTED_FLAG_AND_IN_PROGRESS,
                detail=f"state={state_label}",
                evidence=tuple(evidence),
            )
        return RunRecordClassification(
            outcome=ExecutionClassification.INDETERMINATE,
            reason=CLASSIFICATION_REASON_INTERRUPTED_FLAG,
            detail="interrupted=True",
            evidence=tuple(evidence),
        )

    state_outcome = _classify_label(state, _STATE_CLASSIFICATION)
    status_outcome = _classify_label(status, _STATUS_CLASSIFICATION)

    # 2. Two *terminal* fields that declare different outcomes. Opposite polarity
    #    ("succeeded" beside "failed") is a self-contradiction: neither label may be
    #    preferred, so the result is UNKNOWN and never success. Within one polarity
    #    the more specific label wins, so ``FAILED`` beside ``EXECUTION_TIMEOUT``,
    #    ``CANCELLED``, or ``POLICY_DENIED`` keeps the reason instead of discarding
    #    it. An in-progress claim beside a terminal label is not a conflict at all
    #    (the run was still going when the terminal label was written) and is
    #    resolved in step 3.
    if (
        state_outcome is not None
        and status_outcome is not None
        and state_outcome in _TERMINAL_OUTCOMES
        and status_outcome in _TERMINAL_OUTCOMES
        and state_outcome is not status_outcome
    ):
        if _TERMINAL_POLARITY[state_outcome] != _TERMINAL_POLARITY[status_outcome]:
            return RunRecordClassification(
                outcome=ExecutionClassification.UNKNOWN,
                reason=CLASSIFICATION_REASON_CONTRADICTORY_EVIDENCE,
                detail=f"state={state_label} status={status_label}",
                evidence=tuple(evidence),
            )
        preferred_state = _TERMINAL_SPECIFICITY[
            state_outcome
        ] > _TERMINAL_SPECIFICITY[status_outcome]
        return RunRecordClassification(
            outcome=state_outcome if preferred_state else status_outcome,
            reason=CLASSIFICATION_REASON_DECLARED_OUTCOME,
            detail=(
                f"state={state_label}" if preferred_state else f"status={status_label}"
            ),
            evidence=tuple(evidence),
        )

    # 3. A declared outcome. A terminal label outranks an in-progress claim: the
    #    terminal label states what happened, while the in-progress claim only
    #    states that the writer had not finished when it wrote. A claimed-progress
    #    state on its own is INDETERMINATE -- the claim is recorded, but nothing in
    #    the record establishes that it is still true.
    if state_outcome is not None and state_outcome in _TERMINAL_OUTCOMES:
        declared, provenance = state_outcome, f"state={state_label}"
    elif status_outcome is not None and status_outcome in _TERMINAL_OUTCOMES:
        declared, provenance = status_outcome, f"status={status_label}"
    elif state_outcome is not None:
        declared, provenance = state_outcome, f"state={state_label}"
    else:
        declared, provenance = status_outcome, f"status={status_label}"

    if declared is not None:
        if declared is ExecutionClassification.INDETERMINATE:
            return RunRecordClassification(
                outcome=ExecutionClassification.INDETERMINATE,
                reason=CLASSIFICATION_REASON_IN_PROGRESS_CLAIM,
                detail=provenance,
                evidence=tuple(evidence),
            )
        return RunRecordClassification(
            outcome=declared,
            reason=CLASSIFICATION_REASON_DECLARED_OUTCOME,
            detail=provenance,
            evidence=tuple(evidence),
        )

    # 4. No snapshot: the event trail is the only evidence there is.
    if not has_snapshot:
        from_events = _terminal_event_classification(event_types)
        if from_events is not None:
            outcome, event_name = from_events
            return RunRecordClassification(
                outcome=outcome,
                reason=CLASSIFICATION_REASON_FINAL_EVENT,
                detail=f"last_terminal_event={event_name}",
                evidence=tuple(evidence),
            )
        return RunRecordClassification(
            outcome=ExecutionClassification.UNKNOWN,
            reason=CLASSIFICATION_REASON_NO_SNAPSHOT,
            evidence=tuple(evidence),
        )

    # 5. A step-graph attempt. Its own ``state``/``status`` are deliberately empty
    #    (it is bookkeeping, not a live Run), so the verdict has to come from the
    #    step-graph document. This is consulted only when neither the run state nor
    #    the status declares anything: a real lifecycle label always outranks it,
    #    and an unrecognised step-graph status stays unrecognised rather than being
    #    folded into a neighbouring meaning.
    if not state_label and not status_label:
        graph_label = _normalize_label(step_graph_status)
        if graph_label:
            evidence.append(f"step_graph_status={graph_label}")
            graph_outcome = _STEP_GRAPH_STATUS_CLASSIFICATION.get(graph_label)
            if graph_outcome is not None:
                return RunRecordClassification(
                    outcome=graph_outcome,
                    reason=CLASSIFICATION_REASON_STEP_GRAPH_STATUS,
                    detail=f"step_graph_status={graph_label}",
                    evidence=tuple(evidence),
                )
            return RunRecordClassification(
                outcome=ExecutionClassification.UNKNOWN,
                reason=CLASSIFICATION_REASON_UNRECOGNISED_FIELDS,
                detail=f"step_graph_status={graph_label}",
                evidence=tuple(evidence),
            )

    # 6. A snapshot whose fields are absent or carry no recognised meaning.
    if not state_label and not status_label:
        return RunRecordClassification(
            outcome=ExecutionClassification.UNKNOWN,
            reason=CLASSIFICATION_REASON_EMPTY_FIELDS,
            evidence=tuple(evidence),
        )
    return RunRecordClassification(
        outcome=ExecutionClassification.UNKNOWN,
        reason=CLASSIFICATION_REASON_UNRECOGNISED_FIELDS,
        detail=f"state={state_label} status={status_label}",
        evidence=tuple(evidence),
    )


@dataclass(frozen=True)
class RunStateSnapshot:
    """Durable summary of a Run's progress at one point in time.

    Contains only identifiers, state labels, and already-sanitized telemetry.
    It deliberately carries no approval, no scope, and no credentials.
    """

    run_id: str
    task_id: str = ""
    state: str = ""
    status: str = ""
    attempt_number: int = 0
    completed_attempt_indexes: tuple[int, ...] = ()
    project_state: Mapping[str, Any] | None = None
    accounting: Mapping[str, Any] | None = None
    event_count: int = 0
    updated_at: str = ""
    interrupted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "state": self.state,
            "status": self.status,
            "attempt_number": self.attempt_number,
            "completed_attempt_indexes": list(self.completed_attempt_indexes),
            "project_state": dict(self.project_state) if self.project_state else None,
            "accounting": dict(self.accounting) if self.accounting else None,
            "event_count": self.event_count,
            "updated_at": self.updated_at,
            "interrupted": self.interrupted,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunStateSnapshot":
        return cls(
            run_id=str(data.get("run_id", "")),
            task_id=str(data.get("task_id", "") or ""),
            state=str(data.get("state", "") or ""),
            status=str(data.get("status", "") or ""),
            attempt_number=int(data.get("attempt_number", 0) or 0),
            completed_attempt_indexes=tuple(
                int(i) for i in (data.get("completed_attempt_indexes") or ())
            ),
            project_state=data.get("project_state") or None,
            accounting=data.get("accounting") or None,
            event_count=int(data.get("event_count", 0) or 0),
            updated_at=str(data.get("updated_at", "") or ""),
            interrupted=bool(data.get("interrupted", False)),
        )


@dataclass
class PersistedRun:
    """A Run read back from storage.

    This is data, not authority: it grants nothing and continues nothing.
    """

    run_id: str
    snapshot: RunStateSnapshot | None
    events: tuple[RunEvent, ...] = ()

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def last_sequence_number(self) -> int | None:
        return self.events[-1].sequence_number if self.events else None

    def classification(self) -> RunRecordClassification:
        """Classify what this record proves about the run's outcome.

        Reads only what is present; a record with no snapshot is classified from
        its event trail, and a record with neither is ``UNKNOWN``.
        """
        snapshot = self.snapshot
        return classify_run_record(
            state=snapshot.state if snapshot else "",
            status=snapshot.status if snapshot else "",
            interrupted=snapshot.interrupted if snapshot else False,
            event_types=self.events,
            has_snapshot=snapshot is not None,
            step_graph_status=_step_graph_status_of(snapshot),
        )

    def summary(self) -> dict[str, Any]:
        """Return a compact, safe projection suitable for an API response."""
        snapshot = self.snapshot
        return {
            "run_id": self.run_id,
            "state": snapshot.state if snapshot else "",
            "status": snapshot.status if snapshot else "",
            "task_id": snapshot.task_id if snapshot else "",
            "attempt_number": snapshot.attempt_number if snapshot else 0,
            "completed_attempt_indexes": (
                list(snapshot.completed_attempt_indexes) if snapshot else []
            ),
            "project_state": snapshot.project_state if snapshot else None,
            "accounting": snapshot.accounting if snapshot else None,
            "event_count": self.event_count,
            "last_sequence_number": self.last_sequence_number,
            "updated_at": snapshot.updated_at if snapshot else "",
            "interrupted": snapshot.interrupted if snapshot else False,
        }


class RunStore:
    """Local, append-only, deterministic persistence for Run history.

    Every write is atomic per file: events are appended as single lines, and the
    state snapshot is written through a temporary file plus ``os.replace``. A
    crash can therefore leave at most one truncated trailing event line, which
    the reader tolerates.
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
    ) -> None:
        self._root = Path(root) if root is not None else default_run_store_root()
        # Bound run context. ``EngineeringRunRequest.observer`` receives only
        # ``(event_type, data)``, so the store is attached to one Run before use.
        self._bound_run_id: str | None = None
        self._bound_task_id: str | None = None
        # Monotonic per-run sequence counter. Seed it from what is already on
        # disk so a second writer for the same run never rewinds the log.
        self._sequence: dict[str, int] = {}

    # ------------------------------------------------------------------
    # binding
    # ------------------------------------------------------------------
    def bind_run(self, run_id: str, *, task_id: str | None = None) -> "RunStore":
        """Attach this store to one Run so it can serve as an observer.

        Binding grants no authority; it only tells the writer where to append.
        """
        if not isinstance(run_id, str) or not _SAFE_RUN_ID.match(run_id):
            raise ValueError(f"unsafe run_id for storage: {run_id!r}")
        self._bound_run_id = run_id
        self._bound_task_id = task_id
        return self

    @property
    def bound_run_id(self) -> str | None:
        return self._bound_run_id

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------
    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------
    def run_dir(self, run_id: str) -> Path:
        if not is_safe_run_id(run_id):
            raise ValueError(f"unsafe run_id for storage: {run_id!r}")
        candidate = self._root / run_id
        # Defence in depth: the pattern is the first check, containment is the
        # second, so a later change to the pattern cannot silently reopen the escape.
        try:
            candidate.resolve(strict=False).relative_to(self._root.resolve())
        except ValueError as exc:
            raise ValueError(f"unsafe run_id for storage: {run_id!r}") from exc
        return candidate

    def events_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    def state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state.json"

    def exists(self, run_id: str) -> bool:
        try:
            directory = self.run_dir(run_id)
        except ValueError:
            return False
        return directory.is_dir()

    # ------------------------------------------------------------------
    # observation bridge
    # ------------------------------------------------------------------
    def __call__(self, event_type: EventType, data: dict) -> None:
        """Persist one observed event as part of this Run's durable history.

        The store is usable directly as an ``EngineeringRunRequest.observer``
        after :meth:`bind_run`. It only writes: forwarding to a caller-supplied
        observer and containing that observer's failures remain the caller's
        responsibility, so there is no second notification pipeline here.
        """
        if self._bound_run_id is None:
            raise RuntimeError("RunStore must be bound to a run before use as an observer")
        self.append_event(
            event_type,
            data,
            run_id=self._bound_run_id,
            task_id=self._bound_task_id,
        )

    # ------------------------------------------------------------------
    # event log
    # ------------------------------------------------------------------
    def append_event(
        self,
        event_type: EventType,
        data: Mapping[str, Any] | None,
        *,
        run_id: str,
        sequence_number: int = 0,
        timestamp: datetime | None = None,
        attempt_number: int | None = None,
        task_id: str | None = None,
        **correlation: Any,
    ) -> None:
        """Append one sanitized event line to the durable log."""
        directory = self.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        safe_data = sanitize_event_metadata(data)
        if run_id not in self._sequence:
            existing = self.read_events(run_id)
            self._sequence[run_id] = (
                existing[-1].sequence_number + 1 if existing else 0
            )
        assigned = self._sequence[run_id]
        self._sequence[run_id] = assigned + 1
        record: dict[str, Any] = {
            "run_id": run_id,
            "sequence_number": assigned,
            "event_type": getattr(event_type, "name", str(event_type)),
            "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
            "metadata": safe_data,
        }
        if attempt_number is not None:
            record["attempt_number"] = int(attempt_number)
        if task_id:
            record["task_id"] = str(task_id)
        for key, value in correlation.items():
            if value is not None:
                record[key] = value
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        with open(self.events_path(run_id), "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read_events(self, run_id: str) -> tuple[RunEvent, ...]:
        """Read the durable event log, tolerating a truncated trailing line.

        A crash during append can leave a partial final line. Such a line is
        discarded; every previously written and complete line is recovered, so a
        damaged write never hides valid history and never raises.
        """
        path = self.events_path(run_id)
        if not path.is_file():
            return ()
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ()
        events: list[RunEvent] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                # Truncated or corrupt trailing line: stop at the last valid one.
                break
            if not isinstance(record, dict):
                continue
            event = self._event_from_record(record)
            if event is not None:
                events.append(event)
        return tuple(events)

    @staticmethod
    def _event_from_record(record: Mapping[str, Any]) -> RunEvent | None:
        from app.orchestrator.trace import RunEventType

        name = str(record.get("event_type", ""))
        try:
            event_type = RunEventType[name]
        except KeyError:
            return None
        raw_ts = record.get("timestamp")
        try:
            timestamp = datetime.fromisoformat(str(raw_ts)) if raw_ts else datetime.now(timezone.utc)
        except ValueError:
            timestamp = datetime.now(timezone.utc)
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        attempt = record.get("attempt_number")
        return RunEvent(
            run_id=str(record.get("run_id", "")),
            sequence_number=int(record.get("sequence_number", 0) or 0),
            event_type=event_type,
            timestamp=timestamp,
            attempt_number=int(attempt) if attempt is not None else None,
            task_id=str(record["task_id"]) if record.get("task_id") else None,
            requirement_id=str(record["requirement_id"]) if record.get("requirement_id") else None,
            criterion_id=str(record["criterion_id"]) if record.get("criterion_id") else None,
            execution_request_id=(
                str(record["execution_request_id"])
                if record.get("execution_request_id")
                else None
            ),
            execution_result_id=(
                str(record["execution_result_id"])
                if record.get("execution_result_id")
                else None
            ),
            verification_id=(
                str(record["verification_id"]) if record.get("verification_id") else None
            ),
            changeset_id=str(record["changeset_id"]) if record.get("changeset_id") else None,
            snapshot_id=str(record["snapshot_id"]) if record.get("snapshot_id") else None,
            project_state_status=(
                str(record["project_state_status"])
                if record.get("project_state_status")
                else None
            ),
            acceptance_status=(
                str(record["acceptance_status"]) if record.get("acceptance_status") else None
            ),
            decision_id=str(record["decision_id"]) if record.get("decision_id") else None,
            context_id=str(record["context_id"]) if record.get("context_id") else None,
            context_fingerprint=(
                str(record["context_fingerprint"])
                if record.get("context_fingerprint")
                else None
            ),
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # state snapshot
    # ------------------------------------------------------------------
    def write_state(self, snapshot: RunStateSnapshot) -> Path:
        """Atomically persist a Run state snapshot."""
        directory = self.run_dir(snapshot.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = self.state_path(snapshot.run_id)
        payload = json.dumps(
            snapshot.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, default=str
        )
        descriptor, temp_name = tempfile.mkstemp(
            dir=str(directory), prefix=".state-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return target

    def read_state(self, run_id: str) -> RunStateSnapshot | None:
        """Read the persisted snapshot, or None when absent or unreadable."""
        path = self.state_path(run_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return RunStateSnapshot.from_dict(data)

    def record_run_state(
        self,
        run: Run,
        *,
        status: str = "",
        attempt_number: int = 0,
        completed_attempt_indexes: Sequence[int] = (),
        project_state: Mapping[str, Any] | None = None,
        accounting: Mapping[str, Any] | None = None,
        interrupted: bool = False,
    ) -> RunStateSnapshot:
        """Derive and persist a snapshot from the live Run plus supplied summaries.

        Only values the caller already holds are stored; nothing is recomputed
        and no authority is captured.
        """
        snapshot = RunStateSnapshot(
            run_id=run.id,
            task_id=run.task.id if run.task else "",
            state=getattr(run.state, "value", str(run.state)),
            status=status,
            attempt_number=int(attempt_number),
            completed_attempt_indexes=tuple(int(i) for i in completed_attempt_indexes),
            project_state=dict(project_state) if project_state else None,
            accounting=dict(accounting) if accounting else None,
            event_count=len(run.events),
            updated_at=datetime.now(timezone.utc).isoformat(),
            interrupted=bool(interrupted),
        )
        self.write_state(snapshot)
        return snapshot

    def accounting_summary(self, run_id: str) -> dict[str, Any] | None:
        """Return the persisted accounting summary for a run, when one exists.

        Reading is passive: it reports what was recorded and never re-derives,
        re-charges, or resumes anything.

        An absent accounting block is reported as ``None`` — never as zeros. Zero
        tokens or zero cost is a positive claim that would have to be *measured*,
        so this reader does not fabricate it.
        """
        snapshot = self.read_state(run_id)
        if snapshot is None or not snapshot.accounting:
            return None
        return dict(snapshot.accounting)

    def classify_record(self, run_id: str) -> RunRecordClassification:
        """Classify what the persisted record for ``run_id`` proves.

        Derived at read time from the record on disk, so it works for records
        written by any earlier version and rewrites nothing. A run id with no
        persisted record at all is reported as ``UNKNOWN`` with
        ``CLASSIFICATION_REASON_UNRECOGNISED_FIELDS`` and ``detail='no_record'``:
        nothing was recorded, so nothing is proven. :meth:`load` keeps returning
        ``None`` for that case.

        This method never executes, resumes, or re-charges anything.
        """
        if not self.exists(run_id):
            return RunRecordClassification(
                outcome=ExecutionClassification.UNKNOWN,
                reason=CLASSIFICATION_REASON_UNRECOGNISED_FIELDS,
                detail="no_record",
            )
        snapshot = self.read_state(run_id)
        return classify_run_record(
            state=snapshot.state if snapshot else "",
            status=snapshot.status if snapshot else "",
            interrupted=snapshot.interrupted if snapshot else False,
            event_types=self.read_events(run_id),
            has_snapshot=snapshot is not None,
            step_graph_status=_step_graph_status_of(snapshot),
        )

    # ------------------------------------------------------------------
    # load
    # ------------------------------------------------------------------
    def load(self, run_id: str) -> PersistedRun | None:
        """Load a Run's durable history and latest snapshot.

        Returns None when nothing was ever persisted for this run id. Loading
        never executes anything and never grants authority.
        """
        if not self.exists(run_id):
            return None
        events = self.read_events(run_id)
        snapshot = self.read_state(run_id)
        if snapshot is None and not events:
            return None
        return PersistedRun(run_id=run_id, snapshot=snapshot, events=events)

    def list_run_ids(self) -> tuple[str, ...]:
        """Return every persisted run id, sorted for deterministic output.

        This order is LEXICOGRAPHIC and carries no claim about time. A caller that
        needs "newest first" must use :meth:`list_run_ids_by_recency`; reading
        ``[-1]`` from this tuple as "the latest run" is wrong, because it only means
        "the id that sorts last".
        """
        if not self._root.is_dir():
            return ()
        found = [
            entry.name
            for entry in self._root.iterdir()
            if entry.is_dir() and _SAFE_RUN_ID.match(entry.name)
        ]
        return tuple(sorted(found))

    def list_run_ids_by_recency(self) -> tuple[str, ...]:
        """Return every persisted run id, **newest first** by recorded time.

        The key is the run's own ``updated_at`` from its durable snapshot, which is
        what the record actually tells us about when it last changed. It is parsed
        as a timestamp rather than compared as text so a difference in timezone
        suffix or offset cannot silently reorder the list.

        A record whose ``updated_at`` is missing or unparseable falls back to the
        mtime of its own run directory -- still a real observation about the file
        that was written. The run id is the final tie-break so the order is total
        and deterministic: two runs written in the same clock tick still produce one
        stable sequence rather than depending on directory iteration order.
        """

        from datetime import datetime, timezone

        def _moment(run_id: str) -> tuple[float, str]:
            stamp = ""
            try:
                snapshot = self.read_state(run_id)
            except Exception:  # noqa: BLE001 - ordering must never break a read path
                snapshot = None
            if snapshot is not None:
                stamp = str(getattr(snapshot, "updated_at", "") or "")
            if stamp:
                try:
                    parsed = datetime.fromisoformat(stamp)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed.timestamp(), run_id
                except ValueError:
                    pass
            try:
                return self.run_dir(run_id).stat().st_mtime, run_id
            except OSError:
                return 0.0, run_id

        # Sort the keys, not the (time, id) pairs: run_id is the tie-break within a
        # timestamp, and it must stay part of the returned value.
        return tuple(sorted(self.list_run_ids(), key=_moment, reverse=True))
