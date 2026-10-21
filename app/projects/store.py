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
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.projects.models import LifecycleProject, ProjectError, utc_now

__all__ = [
    "DELETED_DIRECTORY_NAME",
    "INDEX_FILENAME",
    "INDEX_SCHEMA_VERSION",
    "PROJECTS_ROOT_ENV_VAR",
    "SCHEMA_VERSION",
    "WORKSPACE_DIRECTORY_NAME",
    "ProjectStore",
    "ProjectAlreadyExists",
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

#: Where a deleted project is *moved*, never erased. It is a sibling of the store
#: root (``.forge/projects-deleted`` for the default ``.forge/projects``), so the
#: listing walk can never mistake a trash folder for a live project and a restore
#: is a rename away. Deletion in this layer is always this move.
DELETED_DIRECTORY_NAME = "projects-deleted"

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


class ProjectAlreadyExists(ProjectError):
    """A live project already uses the id a restore was about to take.

    A refusal, not a server fault: restoring must never overwrite a record the owner
    can see, so the trash entry stays where it is and the answer says why. It carries
    a machine code so the HTTP layer can answer 409 with the same sentence instead of
    turning the condition into an unhandled ``ValueError``.
    """

    code = "project_already_exists"

    def __init__(self, project_id: str) -> None:
        super().__init__(f"project '{project_id}' already exists in the store")
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

    # ------------------------------------------------------------------ deletion
    #
    # Deleting a project here is a *move* into ``projects-deleted`` and nothing else.
    # The record JSON travels with the folder, so the record (and therefore a restore)
    # survives the deletion; no file is unlinked and no directory is recursed into.
    #
    # Both paths are re-proved at the moment they are used: the source comes from the
    # id allow-list and is checked to sit inside the store root, and the destination is
    # composed inside ``deleted_root`` and checked the same way. An id that is not the
    # generated shape is refused by ``require``/``path_for`` before any move can start,
    # so a request can never name a directory outside this store.

    def deleted_root(self) -> Path:
        """The trash directory: a **sibling** of the store root, never inside it."""

        return self.root.parent / DELETED_DIRECTORY_NAME

    def _relative(self, path: Path) -> str:
        """A repository-relative rendering of ``path``, for an API payload.

        When the store is redirected outside the repository (tests do that), the
        absolute path is never published: the folder name is returned instead, which
        still identifies what moved.
        """

        resolved = path.resolve(strict=False)
        try:
            return resolved.relative_to(repository_root().resolve(strict=False)).as_posix()
        except ValueError:
            return resolved.name

    def _live_paths(self, project_id: str) -> Tuple[Path, Path]:
        """The project's folder and its record, both proved inside the store root."""

        record = self.path_for(project_id)
        folder = (self.root / project_id).resolve(strict=False)
        root = self.root.resolve(strict=False)
        if folder != root and root not in folder.parents:
            raise ValueError("project folder escapes the store root")
        return folder, record

    def delete(
        self, project_id: object, *, stamp: str = ""
    ) -> Dict[str, Any]:
        """Move one project's folder and record into ``projects-deleted``.

        Returns what was moved. Raises :class:`UnknownProject` when no record exists
        and ``ValueError`` when an id or a path fails the containment checks; a
        failure while moving is rolled back so a half-moved project is never left
        behind.
        """

        project = self.require(project_id)
        identifier = str(project.project_id)
        folder, record = self._live_paths(identifier)
        if not folder.is_dir() and not record.is_file():
            raise UnknownProject(identifier)

        trash_root = self.deleted_root().resolve(strict=False)
        token = stamp or time.strftime("%Y%m%dT%H%M%S")
        name = f"{identifier}-{token}"
        suffix = 2
        while (trash_root / name).exists():
            name = f"{identifier}-{token}-{suffix}"
            suffix += 1
        target = (trash_root / name).resolve(strict=False)
        if trash_root not in target.parents:
            raise ValueError("trash path escapes the trash root")

        target.mkdir(parents=True, exist_ok=True)
        moved: List[Dict[str, str]] = []
        undo: List[Tuple[Path, Path]] = []
        try:
            if folder.is_dir():
                destination = target / identifier
                _move_path(folder, destination)
                undo.append((folder, destination))
                moved.append(
                    {"from": self._relative(folder), "to": self._relative(destination)}
                )
            if record.is_file():
                destination = target / record.name
                _move_path(record, destination)
                undo.append((record, destination))
                moved.append(
                    {"from": self._relative(record), "to": self._relative(destination)}
                )
        except BaseException:
            # Put back whatever already moved, then let the caller see the failure.
            for source, destination in reversed(undo):
                try:
                    _move_path(destination, source)
                except OSError:
                    pass
            raise

        self._forget_index(identifier)
        return {
            "project_id": identifier,
            "deleted": True,
            "trash_name": name,
            "trash_path": self._relative(target),
            "moved": moved,
            "timestamp": token,
            "project": project.to_dict(),
        }

    def _forget_index(self, project_id: str) -> None:
        """Remove the project from the listing cache and from the session links."""

        index = self.read_index()
        index["projects"] = [
            item
            for item in index["projects"]
            if str(item.get("project_id", "")) != project_id
        ]
        index["sessions"] = {
            key: value
            for key, value in index["sessions"].items()
            if value != project_id
        }
        self._write_index(index)

    def list_deleted(self) -> List[Dict[str, Any]]:
        """Every trash entry, newest deletion first. Read-only, names and record facts.

        Beyond the names, the answer carries what the «Удалённые проекты» section has
        to show: when the project was deleted, how many files its current version
        held and what status it had. The record travelled with the folder, so those
        two facts are read from it rather than guessed; an unreadable record is
        reported empty instead of claiming zero files.
        """

        root = self.deleted_root()
        if not root.is_dir():
            return []
        found: List[Dict[str, Any]] = []
        for entry in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
            if not entry.is_dir():
                continue
            project_id = entry.name.split("-", 2)
            identifier = "-".join(project_id[:2]) if len(project_id) >= 2 else ""
            if not is_safe_project_id(identifier):
                continue
            record_path = entry / f"{identifier}.json"
            status = ""
            file_count: Optional[int] = None
            name = ""
            if record_path.is_file():
                try:
                    data = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
                if isinstance(data, dict):
                    try:
                        stored = LifecycleProject.from_dict(data)
                    except (KeyError, TypeError, ValueError):
                        stored = None
                    if stored is not None:
                        status = stored.status.value
                        file_count = stored.file_count()
                        name = stored.name
            found.append(
                {
                    "trash_name": entry.name,
                    "project_id": identifier,
                    "trash_path": self._relative(entry),
                    "record_present": record_path.is_file(),
                    "folder_present": (entry / identifier).is_dir(),
                    # The deletion moment is the timestamp token of the folder the
                    # delete created; an unrecognisable name reports an empty date.
                    "deleted_at": _deleted_at_from_name(entry.name),
                    "name": name,
                    "status": status,
                    "file_count": file_count,
                }
            )
        # Newest deletion first, as the docstring promises. The name alone sorts by
        # project id; the deletion token is the fact the reader cares about.
        found.sort(
            key=lambda item: (str(item.get("deleted_at") or ""), item["trash_name"]),
            reverse=True,
        )
        return found

    def restore_deleted(self, project_id: object) -> Dict[str, Any]:
        """Move the newest trash entry for ``project_id`` back into the store.

        Refuses -- rather than overwriting -- when a live project already uses the
        id, because a restore must never replace a record the owner can see.
        """

        if not is_safe_project_id(project_id):
            raise UnknownProject(str(project_id))
        identifier = str(project_id)
        if self.exists(identifier):
            raise ProjectAlreadyExists(identifier)
        root = self.deleted_root()
        candidates = (
            [entry for entry in root.iterdir() if entry.is_dir() and entry.name.startswith(f"{identifier}-")]
            if root.is_dir()
            else []
        )
        if not candidates:
            raise UnknownProject(identifier)
        source = sorted(candidates, key=lambda item: item.name, reverse=True)[0]
        record = source / f"{identifier}.json"
        if not record.is_file():
            raise UnknownProject(identifier)

        folder = self.root / identifier
        moved: List[Dict[str, str]] = []
        trash_folder = source / identifier
        if trash_folder.is_dir():
            _move_path(trash_folder, folder)
            moved.append(
                {"from": self._relative(trash_folder), "to": self._relative(folder)}
            )
        _move_path(record, self.path_for(identifier))
        moved.append(
            {
                "from": self._relative(record),
                "to": self._relative(self.path_for(identifier)),
            }
        )
        try:
            source.rmdir()
        except OSError:
            pass
        restored = self.read(identifier)
        if restored is not None:
            self._refresh_index(restored)
        return {
            "project_id": identifier,
            "restored": restored is not None,
            "trash_name": source.name,
            "moved": moved,
            "project": restored.to_dict() if restored is not None else None,
        }


def _deleted_at_from_name(name: str) -> str:
    """The deletion moment encoded in a trash folder name, or ``""`` when absent.

    ``delete`` names the folder ``<project-id>-<YYYYmmddTHHMMSS>`` (with at most a
    ``-N`` collision suffix), so the token is the third dash-separated part. It is
    rendered ISO-like for display; no timezone is claimed, because none was recorded.
    """

    parts = name.split("-", 2)
    if len(parts) != 3:
        return ""
    token = parts[2].split("-", 1)[0]
    try:
        moment = time.strptime(token, "%Y%m%dT%H%M%S")
    except ValueError:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%S", moment)


def _move_path(source: Path, destination: Path) -> None:
    """``shutil.move`` with the destination's parent created first.

    Deliberately not ``os.replace``: the source may be a directory, and a directory
    rename across the same volume is what ``shutil.move`` reduces to, while a target
    on another volume still works (Windows makes that case real).
    """

    import shutil

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(str(destination))
    shutil.move(str(source), str(destination))
