"""Provider-neutral records of a persistent project: status, versions, artifacts.

This module is the vocabulary of the project lifecycle and nothing else. It calls
no model, opens no socket, touches no filesystem, and imports nothing outside the
standard library.

Two rules are structural rather than documented:

* **No file contents.** :class:`ArtifactRef` describes a file that a run wrote --
  its path, its size, the SHA-256 of its bytes, whether it was created or updated,
  and whether the run's verification judged it. The bytes themselves stay in the
  project workspace; a record can therefore be listed, compared and archived
  without ever carrying the code (or a credential that ended up in it).
* **No secrets.** A record holds user text (the goal, the answers, the change
  request) that was already screened for credential shapes before it reached this
  module, plus identifiers, status words, sizes and hashes. There is no field here
  that could carry an API key, a token or a header value.

The statuses are exactly the eleven the product uses. The allowed transitions
between them live in :mod:`app.projects.lifecycle`; this module only names them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "ArtifactRef",
    "ChangeRequest",
    "LifecycleProject",
    "ProjectError",
    "ProjectStatus",
    "ProjectVersion",
    "ACCEPTANCE_FAILED",
    "ACCEPTANCE_NOT_EVALUATED",
    "ACCEPTANCE_PASSED",
    "ACCEPTANCE_PENDING",
    "ACTIVE_PROJECT_STATUSES",
    "ARTIFACT_CHANGE_CREATE",
    "ARTIFACT_CHANGE_UPDATE",
    "ARTIFACT_KINDS",
    "MAX_VERSION_FILES",
    "VERIFICATION_FAILED",
    "VERIFICATION_NOT_APPLICABLE",
    "VERIFICATION_NOT_RUN",
    "VERIFICATION_PASSED",
    "VERIFICATION_PENDING",
    "VERIFICATION_VALUES",
    "kind_for_path",
    "utc_now",
    "verification_relevance_for_path",
]


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #


class ProjectStatus(str, Enum):
    """The eleven states of a persistent project.

    The names travel through the API unchanged, so a client never has to
    translate them. ``ARCHIVED`` is reachable from every other state and keeps
    the record intact; nothing here deletes a project.
    """

    DISCOVERY = "DISCOVERY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY_FOR_PLANNING = "READY_FOR_PLANNING"
    PLANNED = "PLANNED"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    RUNNING = "RUNNING"
    REVIEWING = "REVIEWING"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


#: Every status a project can be *working* in. ``ARCHIVED`` is the one that is not:
#: an archived project is frozen until ``restore`` returns it to the status it had.
ACTIVE_PROJECT_STATUSES = frozenset(
    status for status in ProjectStatus if status is not ProjectStatus.ARCHIVED
)


# --------------------------------------------------------------------------- #
# verdict vocabularies
#
# Four fixed values, used by both the run path (``code_writer``) and this layer,
# so "the same word" means "the same fact" across the two.
# --------------------------------------------------------------------------- #

#: The run wrote files and every one of them passed the run's own check.
VERIFICATION_PASSED = "passed"
#: The run wrote files and the check failed; the run rolled them back.
VERIFICATION_FAILED = "failed"
#: The run wrote no Python file, so there was nothing to compile.
VERIFICATION_NOT_APPLICABLE = "not_applicable"
#: Nothing was checked: no files were written, or no write was attempted.
VERIFICATION_NOT_RUN = "not_run"
#: A version slot exists but has not been filled by a run yet.
VERIFICATION_PENDING = "pending"

VERIFICATION_VALUES = frozenset(
    {
        VERIFICATION_PASSED,
        VERIFICATION_FAILED,
        VERIFICATION_NOT_APPLICABLE,
        VERIFICATION_NOT_RUN,
        VERIFICATION_PENDING,
    }
)

#: The run's own outcome was judged a success by the existing acceptance path.
ACCEPTANCE_PASSED = "passed"
#: The run's own outcome was judged a failure by the existing acceptance path.
ACCEPTANCE_FAILED = "failed"
#: The run finished without an outcome the acceptance path could judge.
ACCEPTANCE_NOT_EVALUATED = "not_evaluated"
#: A version slot exists but has no run behind it yet.
ACCEPTANCE_PENDING = "pending"

ACCEPTANCE_VALUES = frozenset(
    {
        ACCEPTANCE_PASSED,
        ACCEPTANCE_FAILED,
        ACCEPTANCE_NOT_EVALUATED,
        ACCEPTANCE_PENDING,
    }
)


# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #

ARTIFACT_CHANGE_CREATE = "create"
ARTIFACT_CHANGE_UPDATE = "update"
ARTIFACT_CHANGES = frozenset({ARTIFACT_CHANGE_CREATE, ARTIFACT_CHANGE_UPDATE})

#: The most files one version may record. The code writer's own cap is 20 per
#: answer, so this bound can never truncate a real run; it keeps a hand-made or
#: corrupted record from making a payload unbounded.
MAX_VERSION_FILES = 50

#: Bounded, closed set of artifact kinds. The kind is derived from the file name
#: only; it never depends on reading the content.
ARTIFACT_KINDS = frozenset({"python", "markup", "style", "script", "data", "config", "text", "file"})

_KIND_BY_SUFFIX: Mapping[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".html": "markup",
    ".htm": "markup",
    ".md": "text",
    ".txt": "text",
    ".rst": "text",
    ".css": "style",
    ".js": "script",
    ".mjs": "script",
    ".ts": "script",
    ".json": "data",
    ".csv": "data",
    ".yml": "config",
    ".yaml": "config",
    ".toml": "config",
    ".ini": "config",
    ".cfg": "config",
}

#: A workspace-relative path: forward slashes, no drive letter, no leading slash,
#: no ``..`` component, printable, bounded. This is the same conservative shape the
#: discovery and run stores use for an id, applied to a path.
_SAFE_RELATIVE_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,400}$")

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def kind_for_path(relative_path: str) -> str:
    """The artifact kind of ``relative_path``, from its name alone."""

    lowered = str(relative_path or "").casefold()
    for suffix, kind in _KIND_BY_SUFFIX.items():
        if lowered.endswith(suffix):
            return kind
    return "file"


def verification_relevance_for_path(relative_path: str) -> str:
    """How the run's verification judged this artifact.

    The guarded writer compiles every ``.py`` file it writes and rolls the whole
    run back if one fails, so a recorded Python artifact is one that compiled. Any
    other kind was written but not compiled, which is stated rather than implied.
    """

    return "compiled" if kind_for_path(relative_path) == "python" else "not_compiled"


def is_safe_relative_path(value: object) -> bool:
    """True when ``value`` may name a file inside a workspace."""

    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if "\\" in value or value.startswith("/"):
        return False
    if _SAFE_RELATIVE_PATH.match(value) is None:
        return False
    parts = value.split("/")
    return all(part not in ("", ".", "..") for part in parts)


@dataclass(frozen=True)
class ArtifactRef:
    """One file a run wrote, described without its contents.

    ``path`` is workspace-relative and always uses ``/``, so the same artifact is
    described identically on every platform and can be joined to a project path
    without a second normalization step. ``sha256`` is the digest of the bytes that
    were copied into the project workspace, which is what makes a later integrity
    re-check possible without keeping the bytes in the record.
    """

    path: str
    kind: str = "file"
    size: int = 0
    sha256: str = ""
    change: str = ARTIFACT_CHANGE_CREATE
    verification_relevance: str = "not_compiled"

    def __post_init__(self) -> None:
        if not is_safe_relative_path(self.path):
            raise ValueError("artifact path must be a safe workspace-relative path")
        if self.kind not in ARTIFACT_KINDS:
            raise ValueError("unknown artifact kind")
        if self.change not in ARTIFACT_CHANGES:
            raise ValueError("artifact change must be 'create' or 'update'")
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise ValueError("artifact size must be a non-negative integer")
        if self.sha256 and _SHA256_HEX.match(self.sha256) is None:
            raise ValueError("artifact sha256 must be 64 hexadecimal characters")

    @property
    def is_python(self) -> bool:
        return self.kind == "python"

    def to_dict(self) -> Dict[str, Any]:
        """The record's public shape. There is no ``content`` key, by design."""

        return {
            "path": self.path,
            "kind": self.kind,
            "size": int(self.size),
            "sha256": self.sha256,
            "change": self.change,
            "verification_relevance": self.verification_relevance,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactRef":
        return cls(
            path=str(data["path"]),
            kind=str(data.get("kind") or "file"),
            size=int(data.get("size", 0) or 0),
            sha256=str(data.get("sha256", "") or ""),
            change=str(data.get("change") or ARTIFACT_CHANGE_CREATE),
            verification_relevance=str(
                data.get("verification_relevance") or "not_compiled"
            ),
        )


# --------------------------------------------------------------------------- #
# versions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChangeRequest:
    """What the user asked to change for one version, and what it was screened to.

    ``text`` holds the user's own words after the repository's existing
    credential-shape screen, so a request that looked like a secret is stored as a
    redaction marker instead of the value.
    """

    text: str
    version: int = 0
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "version": int(self.version),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ChangeRequest":
        return cls(
            text=str(data.get("text", "")),
            version=int(data.get("version", 0) or 0),
            created_at=str(data.get("created_at", "")),
        )


@dataclass(frozen=True)
class ProjectVersion:
    """One recorded attempt at a project: its run, its files, its verdicts.

    A version is *pending* until a run fills it. A pending slot carries no
    ``run_id``; every filled version keeps the run it came from, so a later
    version can never overwrite the evidence of an earlier one. Versions are
    append-only by construction: this object is immutable and the service only
    ever appends a new one or fills the pending one.
    """

    version: int
    created_at: str = ""
    run_id: str = ""
    files: Tuple[ArtifactRef, ...] = ()
    verification: str = VERIFICATION_PENDING
    acceptance: str = ACCEPTANCE_PENDING
    provider: str = ""
    model: str = ""
    change_request: Optional[ChangeRequest] = None
    #: True only when the owner explicitly stated that this version needs no local
    #: preview. It is recorded **on the version**, not inferred from the entry kind:
    #: a Python project has no static preview either, but "no entry point could be
    #: served" is not the same fact as "the owner does not want a preview", and only
    #: the second one may satisfy the completion gate's preview requirement.
    preview_not_required: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.files, list):
            object.__setattr__(self, "files", tuple(self.files))
        if not isinstance(self.files, tuple):
            raise ValueError("version files must be a tuple of ArtifactRef")
        for item in self.files:
            if not isinstance(item, ArtifactRef):
                raise ValueError("version files must be ArtifactRef records")
        if len(self.files) > MAX_VERSION_FILES:
            raise ValueError("a version may not record more than MAX_VERSION_FILES files")
        if self.verification not in VERIFICATION_VALUES:
            raise ValueError("unknown verification value")
        if self.acceptance not in ACCEPTANCE_VALUES:
            raise ValueError("unknown acceptance value")
        if not isinstance(self.version, int) or isinstance(self.version, bool):
            raise ValueError("version must be an integer")
        if self.version < 1:
            raise ValueError("version numbers start at 1")

    @property
    def is_pending(self) -> bool:
        """True while no run has filled this slot."""

        return not self.run_id and self.verification == VERIFICATION_PENDING

    @property
    def file_count(self) -> int:
        return len(self.files)

    def with_files(self, files: Tuple[ArtifactRef, ...]) -> "ProjectVersion":
        return replace(self, files=files)

    def to_dict(self, *, include_files: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "version": int(self.version),
            "created_at": self.created_at,
            "run_id": self.run_id,
            "verification": self.verification,
            "acceptance": self.acceptance,
            "provider": self.provider,
            "model": self.model,
            "file_count": self.file_count,
            "pending": self.is_pending,
            "preview_not_required": bool(self.preview_not_required),
            "change_request": (
                self.change_request.to_dict()
                if self.change_request is not None
                else None
            ),
        }
        if include_files:
            payload["files"] = [item.to_dict() for item in self.files]
        return payload

    def summary(self) -> Dict[str, Any]:
        return self.to_dict(include_files=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectVersion":
        raw_change = data.get("change_request")
        return cls(
            version=int(data["version"]),
            created_at=str(data.get("created_at", "")),
            run_id=str(data.get("run_id", "")),
            files=tuple(
                ArtifactRef.from_dict(item) for item in (data.get("files") or ())
            ),
            verification=str(data.get("verification") or VERIFICATION_PENDING),
            acceptance=str(data.get("acceptance") or ACCEPTANCE_PENDING),
            provider=str(data.get("provider", "")),
            model=str(data.get("model", "")),
            preview_not_required=bool(data.get("preview_not_required", False)),
            change_request=(
                ChangeRequest.from_dict(raw_change)
                if isinstance(raw_change, Mapping)
                else None
            ),
        )


# --------------------------------------------------------------------------- #
# project
# --------------------------------------------------------------------------- #


class ProjectError(Exception):
    """Base class for every refusal of the project lifecycle layer."""

    code = "project_error"


@dataclass
class LifecycleProject:
    """A persistent project: one specification, one workspace, many versions.

    Deliberately mutable as a value object: the service reads it, derives an
    updated copy through :func:`app.projects.lifecycle.advance`, and persists that
    copy. ``versions`` is an append-only list of :class:`ProjectVersion`.

    ``project_path`` is **repository-relative** (``.forge/projects/<id>/workspace``)
    so an API payload never publishes an absolute path of the host. ``workspace_id``
    names the workspace the project owns; today it is the project id itself, because
    one project owns exactly one workspace -- it is a separate named field so a
    future multi-workspace project can set it differently without redefining the
    record. ``previous_status`` is the status ``restore`` returns an archived
    project to; it is only ever set by archiving.
    """

    project_id: str
    name: str
    status: ProjectStatus
    workspace_id: str = ""
    project_path: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_run_at: str = ""
    current_version: int = 0
    versions: List[ProjectVersion] = field(default_factory=list)
    archived: bool = False
    specification: Mapping[str, Any] = field(default_factory=dict)
    plan_ref: str = ""
    previous_status: Optional[ProjectStatus] = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str) and not isinstance(self.status, ProjectStatus):
            self.status = ProjectStatus(self.status)
        if self.previous_status is not None and not isinstance(
            self.previous_status, ProjectStatus
        ):
            self.previous_status = ProjectStatus(self.previous_status)
        if not isinstance(self.versions, list):
            self.versions = list(self.versions)
        if not isinstance(self.specification, Mapping):
            raise ValueError("specification must be a mapping")
        self.specification = dict(self.specification)
        if not isinstance(self.current_version, int) or isinstance(
            self.current_version, bool
        ):
            raise ValueError("current_version must be an integer")

    # ------------------------------------------------------------------ views
    @property
    def status_value(self) -> str:
        return self.status.value

    @property
    def version_numbers(self) -> Tuple[int, ...]:
        return tuple(item.version for item in self.versions)

    @property
    def pending_version(self) -> Optional[ProjectVersion]:
        """The staged version slot with no run behind it, when there is one."""

        for item in reversed(self.versions):
            if item.is_pending:
                return item
        return None

    @property
    def latest_version(self) -> Optional[ProjectVersion]:
        return self.versions[-1] if self.versions else None

    def version(self, number: int) -> Optional[ProjectVersion]:
        for item in self.versions:
            if item.version == int(number):
                return item
        return None

    def current(self) -> Optional[ProjectVersion]:
        """The version that describes what is in the workspace now.

        A project that has never had a recorded run has none. ``current_version``
        is the number the lifecycle advanced to, so a failed attempt (which keeps
        the previous version current) does not answer here.
        """

        return self.version(self.current_version) if self.current_version else None

    def file_count(self) -> int:
        current = self.current()
        return current.file_count if current is not None else 0

    def specification_goal(self) -> str:
        return str(self.specification.get("goal", "") or "")

    def to_dict(self) -> Dict[str, Any]:
        """The full record, in the shape the API publishes.

        It contains the specification (user text that was screened before storage),
        identifiers, repo-relative paths, status words, sizes and hashes. It carries
        no absolute path, no environment value and no file content.
        """

        return {
            "project_id": self.project_id,
            "name": self.name,
            "status": self.status.value,
            "workspace_id": self.workspace_id,
            "project_path": self.project_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run_at": self.last_run_at,
            "current_version": self.current_version,
            "versions": [item.to_dict() for item in self.versions],
            "archived": bool(self.archived),
            "specification": dict(self.specification),
            "plan_ref": self.plan_ref,
            "previous_status": (
                self.previous_status.value if self.previous_status is not None else None
            ),
        }

    def summary(self) -> Dict[str, Any]:
        """The one-line projection the lifecycle list publishes."""

        return {
            "project_id": self.project_id,
            "name": self.name,
            "status": self.status.value,
            "archived": bool(self.archived),
            "current_version": self.current_version,
            "last_run_at": self.last_run_at,
            "file_count": self.file_count(),
            "project_path": self.project_path,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LifecycleProject":
        raw_specification = data.get("specification")
        return cls(
            project_id=str(data["project_id"]),
            name=str(data.get("name", "")),
            status=ProjectStatus(str(data["status"])),
            workspace_id=str(data.get("workspace_id", "")),
            project_path=str(data.get("project_path", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            last_run_at=str(data.get("last_run_at", "")),
            current_version=int(data.get("current_version", 0) or 0),
            versions=[
                ProjectVersion.from_dict(item) for item in (data.get("versions") or ())
            ],
            archived=bool(data.get("archived", False)),
            specification=(
                dict(raw_specification)
                if isinstance(raw_specification, Mapping)
                else {}
            ),
            plan_ref=str(data.get("plan_ref", "")),
            previous_status=(
                ProjectStatus(str(data["previous_status"]))
                if data.get("previous_status")
                else None
            ),
        )


def utc_now() -> str:
    """The timestamp written into a record, in UTC ISO form."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
