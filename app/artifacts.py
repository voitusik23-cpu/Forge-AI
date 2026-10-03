"""In-memory change and artifact contracts linked to one Run."""

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4


class FileChangeType(str, Enum):
    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"  # Reserved until a delete tool exists.


class ChangeSetStatus(str, Enum):
    COMPLETED = "COMPLETED"


class ArtifactType(str, Enum):
    CHANGESET = "CHANGESET"
    PROJECT_SNAPSHOT = "PROJECT_SNAPSHOT"


class ArtifactStatus(str, Enum):
    AVAILABLE = "AVAILABLE"


@dataclass(frozen=True)
class FileChange:
    relative_path: str
    change_type: FileChangeType
    before_fingerprint: str | None
    after_fingerprint: str | None


@dataclass
class ChangeSet:
    changeset_id: str = field(default_factory=lambda: str(uuid4()))
    run_id: str = ""
    attempt_number: int = 0
    status: ChangeSetStatus = ChangeSetStatus.COMPLETED
    changes: tuple[FileChange, ...] = ()
    verification_status: str | None = None
    acceptance_status: str | None = None


@dataclass(frozen=True)
class Artifact:
    artifact_id: str = field(default_factory=lambda: str(uuid4()))
    run_id: str = ""
    artifact_type: ArtifactType = ArtifactType.CHANGESET
    changeset_id: str | None = None
    project_snapshot_id: str | None = None
    status: ArtifactStatus = ArtifactStatus.AVAILABLE
