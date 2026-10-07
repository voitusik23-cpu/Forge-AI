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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    "default_store_enabled",
]

# Run ids are used as directory names, so they are restricted to a conservative
# charset. This prevents a crafted id from escaping the storage root.
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

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
        if not isinstance(run_id, str) or not _SAFE_RUN_ID.match(run_id):
            raise ValueError(f"unsafe run_id for storage: {run_id!r}")
        return self._root / run_id

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
        """
        snapshot = self.read_state(run_id)
        if snapshot is None or not snapshot.accounting:
            return None
        return dict(snapshot.accounting)

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
        """Return every persisted run id, sorted for deterministic output."""
        if not self._root.is_dir():
            return ()
        found = [
            entry.name
            for entry in self._root.iterdir()
            if entry.is_dir() and _SAFE_RUN_ID.match(entry.name)
        ]
        return tuple(sorted(found))
