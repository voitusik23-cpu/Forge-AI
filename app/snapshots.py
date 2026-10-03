"""In-memory contracts for explicit, read-only project snapshots."""

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4


class ProjectSnapshotStatus(str, Enum):
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class SnapshotFile:
    relative_path: str
    exists: bool
    fingerprint: str | None


@dataclass(frozen=True)
class ProjectSnapshot:
    snapshot_id: str = field(default_factory=lambda: str(uuid4()))
    run_id: str = ""
    attempt_number: int = 0
    files: tuple[SnapshotFile, ...] = ()
    status: ProjectSnapshotStatus = ProjectSnapshotStatus.COMPLETED
