"""Deterministic project state contract and derivation for Engineering Run attempts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.tools.acceptance import AcceptanceResult
from app.tools.verification import VerificationStatus


class ProjectStateStatus(str, Enum):
    """Deterministic lifecycle state of a project within an Engineering Run attempt."""

    INITIAL = "INITIAL"
    IN_PROGRESS = "IN_PROGRESS"
    CHANGED = "CHANGED"
    VERIFIED = "VERIFIED"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProjectState:
    """Immutable, provider-neutral representation of project state for a run attempt.

    Integrates snapshots, changesets, execution, verification, and acceptance
    into a structured traceability record without storing raw stdout/stderr,
    secrets, source contents, or model reasoning.
    """

    run_id: str
    attempt_number: int
    status: ProjectStateStatus
    project_id: str = ""
    task_id: str = ""
    snapshot_ids: tuple[str, ...] = ()
    changeset_ids: tuple[str, ...] = ()
    execution_result_ids: tuple[str, ...] = ()
    verification_result_ids: tuple[str, ...] = ()
    acceptance_status: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.snapshot_ids, list):
            object.__setattr__(self, "snapshot_ids", tuple(self.snapshot_ids))
        if isinstance(self.changeset_ids, list):
            object.__setattr__(self, "changeset_ids", tuple(self.changeset_ids))
        if isinstance(self.execution_result_ids, list):
            object.__setattr__(self, "execution_result_ids", tuple(self.execution_result_ids))
        if isinstance(self.verification_result_ids, list):
            object.__setattr__(self, "verification_result_ids", tuple(self.verification_result_ids))
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def snapshot_id(self) -> str | None:
        """Convenience accessor for the primary snapshot ID if present."""
        return self.snapshot_ids[-1] if self.snapshot_ids else None

    @property
    def changeset_id(self) -> str | None:
        """Convenience accessor for the primary changeset ID if present."""
        return self.changeset_ids[-1] if self.changeset_ids else None

    def to_dict(self) -> dict[str, object]:
        """Serialize state for audit and inspection without leaking secrets or streams."""
        return {
            "run_id": self.run_id,
            "attempt_number": self.attempt_number,
            "status": self.status.value,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "snapshot_ids": list(self.snapshot_ids),
            "changeset_ids": list(self.changeset_ids),
            "execution_result_ids": list(self.execution_result_ids),
            "verification_result_ids": list(self.verification_result_ids),
            "acceptance_status": self.acceptance_status,
            "metadata": dict(self.metadata),
        }


def derive_project_state(
    *,
    run_id: str,
    attempt_number: int = 0,
    project_id: str = "",
    task_id: str = "",
    snapshots: Iterable[Any] = (),
    changesets: Iterable[Any] = (),
    execution_results: Iterable[Any] = (),
    verification_results: Iterable[Any] = (),
    acceptance_result: Optional[AcceptanceResult] = None,
    is_running: bool = False,
    metadata: Optional[Mapping[str, object]] = None,
) -> ProjectState:
    """Deterministically derive ProjectState from run and attempt artifacts.

    State flow:
        INITIAL -> IN_PROGRESS -> CHANGED -> VERIFIED -> ACCEPTED / FAILED

    Rules:
    - If acceptance_result exists: ACCEPTED if pass, else FAILED.
    - Else if any verification failed: FAILED.
    - Else if verifications exist and passed: VERIFIED.
    - Else if changesets or execution results exist: CHANGED.
    - Else if is_running: IN_PROGRESS.
    - Else: INITIAL.
    """
    snapshot_ids: list[str] = []
    for s in snapshots:
        if isinstance(s, str):
            snapshot_ids.append(s)
        elif hasattr(s, "id"):
            snapshot_ids.append(str(s.id))
        elif hasattr(s, "snapshot_id"):
            snapshot_ids.append(str(s.snapshot_id))

    changeset_ids: list[str] = []
    for c in changesets:
        if isinstance(c, str):
            changeset_ids.append(c)
        elif hasattr(c, "id"):
            changeset_ids.append(str(c.id))
        elif hasattr(c, "changeset_id"):
            changeset_ids.append(str(c.changeset_id))

    execution_result_ids: list[str] = []
    for e in execution_results:
        if isinstance(e, str):
            execution_result_ids.append(e)
        elif hasattr(e, "request_id"):
            execution_result_ids.append(str(e.request_id))

    verification_result_ids: list[str] = []
    has_failed_verification = False
    has_verifications = False
    for v in verification_results:
        has_verifications = True
        if isinstance(v, str):
            verification_result_ids.append(v)
        elif hasattr(v, "verification_id"):
            verification_result_ids.append(str(v.verification_id))
            if hasattr(v, "status") and v.status != VerificationStatus.PASS:
                has_failed_verification = True

    acceptance_status: str | None = None
    if acceptance_result is not None:
        if hasattr(acceptance_result.status, "value"):
            acceptance_status = str(acceptance_result.status.value)
        else:
            acceptance_status = str(acceptance_result.status)

    if acceptance_result is not None:
        if acceptance_status == "pass":
            status = ProjectStateStatus.ACCEPTED
        else:
            status = ProjectStateStatus.FAILED
    elif has_failed_verification:
        status = ProjectStateStatus.FAILED
    elif has_verifications:
        status = ProjectStateStatus.VERIFIED
    elif changeset_ids or execution_result_ids:
        status = ProjectStateStatus.CHANGED
    elif is_running:
        status = ProjectStateStatus.IN_PROGRESS
    else:
        status = ProjectStateStatus.INITIAL

    sanitized_metadata: dict[str, object] = dict(metadata or {})
    sanitized_metadata.pop("stdout", None)
    sanitized_metadata.pop("stderr", None)
    sanitized_metadata.pop("raw_output", None)

    return ProjectState(
        run_id=run_id,
        attempt_number=attempt_number,
        status=status,
        project_id=project_id,
        task_id=task_id,
        snapshot_ids=tuple(snapshot_ids),
        changeset_ids=tuple(changeset_ids),
        execution_result_ids=tuple(execution_result_ids),
        verification_result_ids=tuple(verification_result_ids),
        acceptance_status=acceptance_status,
        metadata=sanitized_metadata,
    )
