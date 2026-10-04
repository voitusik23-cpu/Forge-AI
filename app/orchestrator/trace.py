"""Deterministic, provider-neutral run trace and event contract for Engineering Runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.orchestrator.models import Event, EventType, Run
from app.projects.state import ProjectState

RunEventType = EventType

FORBIDDEN_METADATA_SUBSTRINGS: tuple[str, ...] = (
    "stdout",
    "stderr",
    "raw_output",
    "prompt",
    "chain_of_thought",
    "secret",
    "token",
    "password",
    "api_key",
    "credential",
    "source_code",
    "file_content",
)


def sanitize_event_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    """Deterministically sanitize metadata by removing forbidden or sensitive keys."""
    if not metadata:
        return {}
    sanitized: dict[str, object] = {}
    for key, val in metadata.items():
        key_str = str(key).lower().strip()
        if any(bad in key_str for bad in FORBIDDEN_METADATA_SUBSTRINGS):
            continue
        # Protect against arbitrary large payload strings
        if isinstance(val, str) and len(val) > 4096:
            continue
        sanitized[str(key)] = val
    return sanitized


def validate_event_metadata(metadata: Mapping[str, object]) -> tuple[str, ...]:
    """Return errors if any forbidden keys are present in metadata."""
    errors: list[str] = []
    for key in metadata:
        key_str = str(key).lower().strip()
        for bad in FORBIDDEN_METADATA_SUBSTRINGS:
            if bad in key_str:
                errors.append(f"forbidden_metadata_key:{bad}")
    return tuple(errors)


@dataclass(frozen=True)
class RunEvent:
    """Safe, immutable lifecycle observation in an Engineering Run.

    Contains identifiers, sequence numbering, and bounded metadata.
    Never contains raw stdout/stderr, source code, credentials, or prompts.
    """

    run_id: str
    sequence_number: int
    event_type: RunEventType
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attempt_number: int | None = None
    task_id: str | None = None
    requirement_id: str | None = None
    criterion_id: str | None = None
    execution_request_id: str | None = None
    execution_result_id: str | None = None
    verification_id: str | None = None
    changeset_id: str | None = None
    snapshot_id: str | None = None
    project_state_status: str | None = None
    acceptance_status: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id or not isinstance(self.run_id, str):
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 0:
            raise ValueError(
                f"sequence_number must be a non-negative integer, got {self.sequence_number}"
            )
        if isinstance(self.event_type, str) and not isinstance(self.event_type, RunEventType):
            object.__setattr__(self, "event_type", RunEventType(self.event_type))
        # Sanitize metadata deterministically
        sanitized = sanitize_event_metadata(self.metadata)
        object.__setattr__(self, "metadata", sanitized)

    def to_dict(self) -> dict[str, object]:
        """Serialize event into a clean audit dictionary without sensitive streams."""
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence_number": self.sequence_number,
            "event_type": self.event_type.value
            if hasattr(self.event_type, "value")
            else str(self.event_type),
            "timestamp": self.timestamp.isoformat(),
            "attempt_number": self.attempt_number,
            "task_id": self.task_id,
            "requirement_id": self.requirement_id,
            "criterion_id": self.criterion_id,
            "execution_request_id": self.execution_request_id,
            "execution_result_id": self.execution_result_id,
            "verification_id": self.verification_id,
            "changeset_id": self.changeset_id,
            "snapshot_id": self.snapshot_id,
            "project_state_status": self.project_state_status,
            "acceptance_status": self.acceptance_status,
            "metadata": dict(self.metadata),
        }


class RunTrace:
    """Deterministic, immutable in-memory trace of RunEvents for one Run."""

    def __init__(self, run_id: str, events: Iterable[RunEvent] = ()) -> None:
        if not run_id or not isinstance(run_id, str):
            raise ValueError("run_id must be a non-empty string")
        self._run_id = run_id
        self._events: list[RunEvent] = []
        for ev in events:
            self.append(ev)

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def events(self) -> tuple[RunEvent, ...]:
        return tuple(self._events)

    def append(self, event: RunEvent) -> None:
        """Append an event, verifying run_id binding and monotonic sequence ordering."""
        if not isinstance(event, RunEvent):
            raise TypeError(f"Expected RunEvent, got {type(event).__name__}")
        if event.run_id != self._run_id:
            raise ValueError(
                f"Event run_id '{event.run_id}' does not match trace run_id '{self._run_id}'"
            )
        expected_seq = len(self._events)
        if event.sequence_number != expected_seq:
            raise ValueError(
                f"Invalid sequence ordering: expected sequence_number {expected_seq}, "
                f"got {event.sequence_number}"
            )
        self._events.append(event)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def __getitem__(self, index: int | slice):
        if isinstance(index, slice):
            return tuple(self._events[index])
        return self._events[index]

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self._run_id,
            "event_count": len(self._events),
            "events": [ev.to_dict() for ev in self._events],
        }


class RunEventCollector:
    """Collector abstraction for safely building an ordered RunTrace."""

    def __init__(self, run_id: str) -> None:
        if not run_id or not isinstance(run_id, str):
            raise ValueError("run_id must be a non-empty string")
        self._trace = RunTrace(run_id)

    @property
    def run_id(self) -> str:
        return self._trace.run_id

    @property
    def events(self) -> tuple[RunEvent, ...]:
        return self._trace.events

    def append(self, event: RunEvent) -> None:
        self._trace.append(event)

    def emit(
        self,
        event_type: RunEventType | str,
        *,
        attempt_number: int | None = None,
        task_id: str | None = None,
        requirement_id: str | None = None,
        criterion_id: str | None = None,
        execution_request_id: str | None = None,
        execution_result_id: str | None = None,
        verification_id: str | None = None,
        changeset_id: str | None = None,
        snapshot_id: str | None = None,
        project_state_status: str | None = None,
        acceptance_status: str | None = None,
        metadata: Mapping[str, object] | None = None,
        timestamp: datetime | None = None,
    ) -> RunEvent:
        """Create, append, and return a RunEvent with the next sequence number."""
        seq = len(self._trace)
        ev = RunEvent(
            run_id=self._trace.run_id,
            sequence_number=seq,
            event_type=RunEventType(event_type) if isinstance(event_type, str) else event_type,
            timestamp=timestamp or datetime.now(timezone.utc),
            attempt_number=attempt_number,
            task_id=task_id,
            requirement_id=requirement_id,
            criterion_id=criterion_id,
            execution_request_id=execution_request_id,
            execution_result_id=execution_result_id,
            verification_id=verification_id,
            changeset_id=changeset_id,
            snapshot_id=snapshot_id,
            project_state_status=project_state_status,
            acceptance_status=acceptance_status,
            metadata=metadata or {},
        )
        self._trace.append(ev)
        return ev

    def to_trace(self) -> RunTrace:
        return RunTrace(self._trace.run_id, self._trace.events)


def build_trace_from_run(
    run: Run,
    *,
    project_states: Iterable[ProjectState] = (),
) -> RunTrace:
    """Deterministically convert a Run's recorded events into a structured RunTrace.

    Preserves chronological order, assigns monotonic sequence numbers, extracts
    artifact references, and purges all sensitive or unbounded metadata.
    """
    collector = RunEventCollector(run.id)
    default_task_id = run.task.id if run.task else None

    for raw_event in run.events:
        data = raw_event.data if isinstance(raw_event.data, dict) else {}

        task_id = str(data.get("task_id")) if data.get("task_id") else default_task_id
        attempt_number = data.get("attempt_number")
        if attempt_number is not None:
            try:
                attempt_number = int(attempt_number)
            except (ValueError, TypeError):
                attempt_number = None

        requirement_id = str(data.get("requirement_id")) if data.get("requirement_id") else None
        criterion_id = str(data.get("criterion_id")) if data.get("criterion_id") else None

        req_id = data.get("execution_request_id") or data.get("request_id")
        execution_request_id = str(req_id) if req_id else None

        res_id = data.get("execution_result_id")
        execution_result_id = str(res_id) if res_id else None

        ver_id = data.get("verification_id")
        verification_id = str(ver_id) if ver_id else None

        cs_id = data.get("changeset_id")
        changeset_id = str(cs_id) if cs_id else None

        snap_id = data.get("snapshot_id")
        snapshot_id = str(snap_id) if snap_id else None

        ps_status = data.get("project_state_status")
        project_state_status = str(ps_status) if ps_status else None

        acc_status = data.get("acceptance_status")
        acceptance_status = str(acc_status) if acc_status else None

        # Build clean metadata without duplication of top-level reference keys
        meta = {
            k: v
            for k, v in data.items()
            if k
            not in (
                "task_id",
                "attempt_number",
                "requirement_id",
                "criterion_id",
                "execution_request_id",
                "request_id",
                "execution_result_id",
                "verification_id",
                "changeset_id",
                "snapshot_id",
                "project_state_status",
                "acceptance_status",
            )
        }

        collector.emit(
            event_type=raw_event.type,
            timestamp=raw_event.timestamp,
            attempt_number=attempt_number,
            task_id=task_id,
            requirement_id=requirement_id,
            criterion_id=criterion_id,
            execution_request_id=execution_request_id,
            execution_result_id=execution_result_id,
            verification_id=verification_id,
            changeset_id=changeset_id,
            snapshot_id=snapshot_id,
            project_state_status=project_state_status,
            acceptance_status=acceptance_status,
            metadata=meta,
        )

    return collector.to_trace()
