"""Durable storage for persistent projects: one small JSON file per project.

Deliberately separate from the Run Store, and deliberately shaped like
``app.discovery.store``: a project record is not a run, it precedes and then
references runs, and putting it inside the store whose records are execution
evidence would mix a planning artifact with proof of execution. The mechanics are
the proven ones -- a conservative id allow-list, a re-proved containment check, a
temporary file written and fsynced before ``os.replace`` -- without sharing the
Run Store's schema or its directory.

Layout (all under the store root, ``<repository>/.forge/projects`` by default):

```
.forge/projects/
    index.json                  <- listing + discovery-session links
    <project_id>.json           <- one project record
    <project_id>/workspace/     <- the project's controlled working tree
```

The workspace is a *subdirectory of the store*, which is itself inside the
repository, so no project can ever name a path outside the repository: the
containment is re-proved on every resolution instead of being trusted from the id.

Nothing secret is stored here. A record holds user text that was already screened
for credential shapes, plus identifiers, status words, repo-relative paths, sizes
and SHA-256 digests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional

from app.projects.models import LifecycleProject, ProjectError, utc_now

__all__ = [
    "INDEX_FILENAME",
    "INDEX_SCHEMA_VERSION",
    "PROJECTS_ROOT_ENV_VAR",
    "SCHEMA_VERSION",
    "WORKSPACE_DIRECTORY_NAME",
    "ProjectStore",
    "UnknownProject",
    "default_projects_root",
    "is_safe_project_id",
    "new_project_id",
    "repository_root",
]


#: Where a deployment can redirect the project store (tests use a temporary dir).
PROJECTS_ROOT_ENV_VAR = "FORGE_PROJECTS_ROOT"

#: Written into every project file so a future reader can tell what it is reading.
SCHEMA_VERSION = "forge.project.v1"

#: Written into the listing file, which is a cache of summaries and the one place
#: the link from a confirmed discovery session to its project is kept.
INDEX_SCHEMA_VERSION = "forge.projects.index.v1"

INDEX_FILENAME = "index.json"

#: The workspace directory name inside one project directory.
WORKSPACE_DIRECTORY_NAME = "workspace"

#: Project ids become file *and* directory names, so the allow-list is the exact
#: generated shape rather than a permissive charset. ``new_project_id`` is the only
#: producer, and ``path_for`` re-proves containment instead of trusting the pattern.
_SAFE_PROJECT_ID = re.compile(r"^proj-[0-9a-f]{12}$")


def repository_root() -> Path:
    """The repository this module belongs to, from its own location."""

    return Path(__file__).resolve().parents[2]


def default_projects_root() -> Path:
    """The configured project store root.

    Resolution order:

    1. ``FORGE_PROJECTS_ROOT`` when set, which is how tests redirect storage;
    2. ``<repository>/.forge/projects``, derived from this module's own location so
       the answer does not depend on the process working directory.
    """

    configured = os.environ.get(PROJECTS_ROOT_ENV_VAR)
    if configured and configured.strip():
        return Path(configured).expanduser()
    return repository_root() / ".forge" / "projects"


def new_project_id() -> str:
    """A fresh project id in the one documented shape: ``proj-<12 hex>``."""

    import uuid

    return f"proj-{uuid.uuid4().hex[:12]}"


def is_safe_project_id(project_id: object) -> bool:
    """True when ``project_id`` may be used as a file and directory name."""

    return isinstance(project_id, str) and _SAFE_PROJECT_ID.match(project_id) is not None


class UnknownProject(ProjectError):
    """No project is stored under that identifier."""

    code = "unknown_project"

    def __init__(self, project_id: str) -> None:
        super().__init__(f"no project is recorded under '{project_id}'")
        self.project_id = project_id


def _replace_with_retry(source: str, target: Path) -> None:
    """``os.replace`` with a short, bounded tolerance for a Windows sharing violation.

    Measured on this host: replacing a JSON file that had just been created failed
    with ``PermissionError`` (WinError 5) exactly once while an on-access scanner held
    the new file -- a transient, retryable condition, not a permission decision.
    Five attempts spaced by 20 ms resolve it; a real denial still fails, and both
    exceptions are the same ones the caller already handles.
    """

    delay = 0.02
    for attempt in range(5):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(delay)


def _atomic_write_text(target: Path, payload: str) -> Path:
    """Write ``payload`` through a temporary file, fsync, then ``os.replace``."""

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".project-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target


def _atomic_write_bytes(target: Path, data: bytes) -> Path:
    """Write ``data`` through a temporary file, fsync, then ``os.replace``."""

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".artifact-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target


class ProjectStore:
    """Read and write one JSON file per project, plus the listing file."""

    def __init__(self, root: Optional[object] = None) -> None:
        self.root = Path(root) if root is not None else default_projects_root()

    # ------------------------------------------------------------------ paths
    def path_for(self, project_id: object) -> Path:
        """The record path for one project id, or ``ValueError`` when it is unsafe."""

        if not is_safe_project_id(project_id):
            raise ValueError("unsafe project id")
        target = (self.root / f"{project_id}.json").resolve(strict=False)
        root = self.root.resolve(strict=False)
        if target != root and root not in target.parents:
            raise ValueError("project record path escapes the store root")
        return target

    def workspace_path(self, project_id: object, *, create: bool = False) -> Path:
        """The project's workspace directory, re-proved to sit inside the store root."""

        if not is_safe_project_id(project_id):
            raise ValueError("unsafe project id")
        target = (
            self.root / str(project_id) / WORKSPACE_DIRECTORY_NAME
        ).resolve(strict=False)
        root = self.root.resolve(strict=False)
        if target != root and root not in target.parents:
            raise ValueError("project workspace escapes the store root")
        if create:
            target.mkdir(parents=True, exist_ok=True)
        return target

    def project_path_relative(self, project_id: object) -> str:
        """The workspace path as a **repository-relative** string.

        This is the only form that reaches an API payload or a record, so an
        absolute path of the host is never published. When the store root is
        inside the repository -- which it is for every deployment -- the answer is
        the true relative path and therefore ``.forge/projects/<id>/workspace``.
        When a test redirects the root outside the repository, the canonical
        contract string is published instead of leaking an absolute path.
        """

        if not is_safe_project_id(project_id):
            raise ValueError("unsafe project id")
        suffix = f"{project_id}/{WORKSPACE_DIRECTORY_NAME}"
        root = self.root.resolve(strict=False)
        try:
            relative = root.relative_to(repository_root().resolve(strict=False))
        except ValueError:
            return f".forge/projects/{suffix}"
        prefix = relative.as_posix()
        if not prefix or prefix == ".":
            return suffix
        return f"{prefix}/{suffix}"

    def exists(self, project_id: object) -> bool:
        if not is_safe_project_id(project_id):
            return False
        try:
            return self.path_for(project_id).is_file()
        except ValueError:
            return False

    # ------------------------------------------------------------------- I/O
    def write(self, project: LifecycleProject) -> Path:
        """Atomically persist one project record and refresh its index entry."""

        target = self.path_for(project.project_id)
        payload = json.dumps(
            {"schema": SCHEMA_VERSION, **project.to_dict()},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        written = _atomic_write_text(target, payload)
        self._refresh_index(project)
        return written

    def read(self, project_id: object) -> Optional[LifecycleProject]:
        """The stored project, or ``None`` when absent or unreadable.

        A corrupt record is reported as absent rather than partially trusted: a
        half-read project would be a worse answer than "no such project".
        """

        if not is_safe_project_id(project_id):
            return None
        try:
            path = self.path_for(project_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return LifecycleProject.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def require(self, project_id: object) -> LifecycleProject:
        """The stored project, or :class:`UnknownProject`."""

        project = self.read(project_id)
        if project is None:
            raise UnknownProject(str(project_id))
        return project

    def list_projects(self, *, include_archived: bool = True) -> List[LifecycleProject]:
        """Every stored project, newest change first.

        The directory is the truth, not the index: a record present on disk is
        listed even when the index was lost, so a stale cache can never hide a
        project. Ordering is by ``updated_at`` descending with the id as the
        deterministic tie-break.
        """

        found: List[LifecycleProject] = []
        root = self.root
        if not root.is_dir():
            return found
        try:
            entries = list(root.iterdir())
        except OSError:
            return found
        for entry in entries:
            if not entry.is_file() or entry.suffix != ".json":
                continue
            if entry.name == INDEX_FILENAME:
                continue
            if not is_safe_project_id(entry.stem):
                continue
            project = self.read(entry.stem)
            if project is None:
                continue
            if not include_archived and project.archived:
                continue
            found.append(project)
        found.sort(key=lambda item: (item.updated_at, item.project_id), reverse=True)
        return found

    # ----------------------------------------------------------------- index
    @property
    def index_path(self) -> Path:
        return self.root / INDEX_FILENAME

    def read_index(self) -> Dict[str, Any]:
        """The listing file, or an empty structure when it is missing/corrupt."""

        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if not isinstance(data, dict):
            data = {}
        raw_projects = data.get("projects")
        raw_sessions = data.get("sessions")
        return {
            "schema": INDEX_SCHEMA_VERSION,
            "updated_at": str(data.get("updated_at", "") or ""),
            "projects": [
                item for item in (raw_projects or ()) if isinstance(item, Mapping)
            ],
            "sessions": (
                {
                    str(key): str(value)
                    for key, value in dict(raw_sessions).items()
                    if isinstance(value, str)
                }
                if isinstance(raw_sessions, Mapping)
                else {}
            ),
        }

    def _write_index(self, index: Mapping[str, Any]) -> Path:
        payload = json.dumps(
            {
                "schema": INDEX_SCHEMA_VERSION,
                "updated_at": utc_now(),
                "projects": list(index.get("projects") or ()),
                "sessions": dict(index.get("sessions") or {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        return _atomic_write_text(self.index_path, payload)

    def _refresh_index(self, project: LifecycleProject) -> None:
        """Keep the summary cache in step with the record that was just written."""

        index = self.read_index()
        entry = {
            "project_id": project.project_id,
            "name": project.name,
            "status": project.status.value,
            "archived": bool(project.archived),
            "current_version": int(project.current_version),
            "updated_at": project.updated_at,
        }
        entries = [
            item
            for item in index["projects"]
            if str(item.get("project_id", "")) != project.project_id
        ]
        entries.append(entry)
        entries.sort(key=lambda item: str(item.get("project_id", "")))
        index["projects"] = entries
        self._write_index(index)

    def link_session(self, session_id: object, project_id: object) -> None:
        """Record which project a confirmed discovery session produced.

        The link is kept in the index rather than in the project record so the
        record's field set stays exactly the documented contract. Losing the index
        costs the link, never the project: the record and its workspace remain.
        """

        if not is_safe_project_id(project_id):
            raise ValueError("unsafe project id")
        key = str(session_id or "").strip()
        if not key:
            raise ValueError("a discovery session id is required")
        index = self.read_index()
        index["sessions"][key] = str(project_id)
        self._write_index(index)

    def project_for_session(self, session_id: object) -> str:
        """The project id linked to ``session_id``, or an empty string."""

        key = str(session_id or "").strip()
        if not key:
            return ""
        linked = self.read_index()["sessions"].get(key, "")
        return linked if is_safe_project_id(linked) else ""

    # -------------------------------------------------------------- workspace
    def write_workspace_file(
        self, project_id: object, relative_path: str, data: bytes
    ) -> Path:
        """Atomically place one artifact inside the project's workspace."""

        target = self.workspace_file_path(project_id, relative_path)
        return _atomic_write_bytes(target, data)

    def workspace_file_path(self, project_id: object, relative_path: str) -> Path:
        """Resolve one workspace-relative file, re-proving containment."""

        from app.projects.models import is_safe_relative_path

        if not is_safe_relative_path(relative_path):
            raise ValueError("unsafe workspace-relative path")
        workspace = self.workspace_path(project_id)
        target = workspace.joinpath(*str(relative_path).split("/")).resolve(
            strict=False
        )
        root = workspace.resolve(strict=False)
        if root not in target.parents:
            raise ValueError("workspace file escapes the project workspace")
        return target
