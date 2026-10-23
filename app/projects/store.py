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
    <project_id>/workspace/     <- the project's controlled working tree (legacy)
```

**Where a project's files live.** The default layout above is kept for every project
that already exists, and it is still what a store with no configured *project root*
produces. A deployment that configures one (the desktop application does; see
:mod:`app.config.app_settings`) delivers each **new** project into a visible folder
instead:

```
<project root>/<project name> (proj-<id>)    <- the files the owner opens
.forge/projects/<id>.json                    <- the record, unchanged
```

The record carries that folder's absolute location in ``workspace_dir`` (written here,
never published) and its root-relative form in ``project_path``. Which one is in use is
a property of the **record**, not of the store: a project keeps the folder it was
delivered to even after the owner changes the root setting, and a project created
before this existed keeps working in the legacy workspace. So the store resolves the
workspace from the record first and falls back to the legacy layout.

Every resolved path is re-proved against roots the *deployment* configured -- the
current project root, the roots it used before, and this store's own root -- never
against a value from a request or from the record being read. Nothing secret is
written here.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.projects.models import LifecycleProject, ProjectError, utc_now
from app.projects.folders import (
    UnsafeProjectFolder,
    canonical,
    is_within,
    prove_folder,
    published_folder_path,
    visible_folder,
)
from app.projects.root_selection import (
    ProjectRootDecision,
    ProjectRootPolicy,
)

__all__ = [
    "DELETED_DIRECTORY_NAME",
    "INDEX_FILENAME",
    "INDEX_SCHEMA_VERSION",
    "PROJECTS_ROOT_ENV_VAR",
    "SCHEMA_VERSION",
    "WORKSPACE_DIRECTORY_NAME",
    "ProjectStore",
    "ProjectAlreadyExists",
    "ProjectDeleted",
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


class ProjectDeleted(ProjectError):
    """The id names a project that is in ``projects-deleted``, not a live project.

    A refusal, not a server fault, and the one code a *late* writer gets when it
    tried to persist a record it had read before the deletion finished. It exists
    because a resumed verification could otherwise re-create a deleted project's
    record while its workspace sits in the trash (see ``ProjectStore.write``).
    """

    code = "project_deleted"

    def __init__(self, project_id: str) -> None:
        super().__init__(
            f"project '{project_id}' was deleted and can no longer be modified"
        )
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
    """Read and write one JSON file per project, plus the listing file.

    ``visible_root`` is where **new** projects' files are delivered, or ``None`` to keep
    the legacy hidden workspace. It may be a path or a zero-argument callable returning
    one: the desktop application passes a callable over the owner's settings, so editing
    the root in «Настройки» takes effect for the next project without a restart. It is
    only ever consulted for a project that is being **created**; an existing project is
    resolved from its own record.

    ``visible_root`` is a *preference*, not a promise. A root the process cannot write to
    (measured: the owner's protected Desktop) must never make a project creation fail, so
    the store resolves it through :class:`ProjectRootPolicy`: the configured root first,
    then the documented fallback folders, and only when **none** of them can be written
    does :class:`ProjectRootUnavailable` travel to the HTTP layer as a typed refusal.
    ``root_policy`` replaces that default policy when the deployment also wants the chosen
    fallback **remembered** (in ``.forge/settings.json``) and its previous roots kept
    reachable.
    """

    def __init__(
        self,
        root: Optional[object] = None,
        *,
        visible_root: object = None,
        root_policy: Optional[ProjectRootPolicy] = None,
    ) -> None:
        self.root = Path(root) if root is not None else default_projects_root()
        self._visible_root = visible_root
        if root_policy is not None:
            self._policy: Optional[ProjectRootPolicy] = root_policy
        elif visible_root is not None:
            self._policy = ProjectRootPolicy(visible_root)
        else:
            self._policy = None

    # ------------------------------------------------------- the visible root
    def visible_root(self) -> Optional[Path]:
        """The folder the next project is delivered into, or ``None`` when not configured.

        The answer is the **writable** one: a configured root that cannot hold a project
        is replaced by the first fallback that can, and only a complete refusal raises
        (:class:`ProjectRootUnavailable`, a typed conflict, never a server fault).

        An unusable *value* is ``None`` rather than an exception: a broken setting must
        not make the project list unreadable, and a project created while it is broken
        simply lands in the legacy workspace (which still works).
        """

        decision = self.root_decision()
        return None if decision is None else decision.root

    def root_decision(self) -> Optional[ProjectRootDecision]:
        """Where the next project goes and why, or ``None`` when no root is configured."""

        if self._policy is None:
            return None
        return self._policy.resolve()

    def allowed_workspace_roots(self) -> Tuple[Path, ...]:
        """The roots a project folder may live under: the project root, then the store.

        Both come from the deployment's configuration. Nothing here is derived from a
        request, and the only record-derived value is *which* of these roots a project's
        folder is proved against. Every root this deployment has used is included, so a
        project delivered to a fallback folder -- or to a folder chosen before the owner
        changed the setting -- stays reachable instead of becoming unreadable.
        """

        roots: List[Path] = []
        visible = self.visible_root()
        if visible is not None:
            roots.append(visible)
        if self._policy is not None:
            for known in self._policy.known_roots():
                if known not in roots:
                    roots.append(known)
        roots.append(self.root)
        return tuple(roots)

    def visible_delivery_roots(self) -> Tuple[Path, ...]:
        """The roots a project's **own folder** is delivered into, store root excluded.

        This is the classification the «Проекты» list uses to tell the owner's projects
        from the ones a test or a demo script left behind: a project whose files live in
        one of these roots has a folder he can open in Explorer, and one that lives in
        the legacy hidden workspace (``<store>/<id>/workspace``) was created by a process
        that never configured a root -- the test suite and every scratch script.

        Every root *this deployment has used* counts, not only the current one, so
        changing the setting never makes an existing project look like junk. The store
        root itself is never included: it is where the records live, and its subfolders
        are exactly the legacy workspaces this method classifies as "not the owner's".
        """

        try:
            roots = self.allowed_workspace_roots()
        except ProjectError:  # pragma: no cover - an unusable root is "no visible root"
            return ()
        store_root = canonical(self.root)
        found: List[Path] = []
        for root in roots:
            try:
                path = canonical(root)
            except ProjectError:  # pragma: no cover - defensive
                continue
            if path != store_root and path not in found:
                found.append(path)
        return tuple(found)

    def in_visible_root(
        self, project: object, *, roots: Optional[Tuple[Path, ...]] = None
    ) -> bool:
        """True when the record's files really live in one of the visible roots.

        Read from the record's own ``workspace_dir`` and re-proved per call, never
        inferred from the id or the name: a project is "visible" because its folder is,
        and a record that carries no folder (the legacy layout) is not.

        ``False`` -- not an exception -- when the recorded folder does not prove to be
        inside a configured root: a list must stay readable even for a broken record, and
        "we cannot prove it is the owner's folder" is honestly not a yes.

        ``roots`` lets a caller that classifies a whole listing resolve the roots
        (:meth:`visible_delivery_roots`, which probes the folders) **once** instead of
        once per row.
        """

        recorded = str(getattr(project, "workspace_dir", "") or "").strip()
        if not recorded:
            return False
        try:
            folder = canonical(recorded)
        except ProjectError:  # pragma: no cover - defensive
            return False
        known = self.visible_delivery_roots() if roots is None else roots
        return any(is_within(folder, root) for root in known)

    def _legacy_workspace_path(self, project_id: object) -> Path:
        """``<store>/<id>/workspace``, re-proved to sit inside the store root."""

        if not is_safe_project_id(project_id):
            raise ValueError("unsafe project id")
        target = (
            self.root / str(project_id) / WORKSPACE_DIRECTORY_NAME
        ).resolve(strict=False)
        root = self.root.resolve(strict=False)
        if target != root and root not in target.parents:
            raise ValueError("project workspace escapes the store root")
        return target

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
        """The project's workspace directory, proved to sit under an allowed root.

        The record decides: a project delivered to a visible folder is resolved from
        its own ``workspace_dir`` (and refused when that path no longer proves to be
        inside a configured root), and a project that predates that keeps the legacy
        ``<store>/<id>/workspace``. The containment is re-proved on every resolution
        instead of being trusted from the id.
        """

        if not is_safe_project_id(project_id):
            raise ValueError("unsafe project id")
        record = self.read(project_id)
        recorded = str(getattr(record, "workspace_dir", "") or "") if record is not None else ""
        if recorded:
            try:
                target = prove_folder(
                    recorded, allowed_roots=self.allowed_workspace_roots()
                )
            except UnsafeProjectFolder as exc:
                raise ValueError(
                    "project workspace is not inside a configured project root"
                ) from exc
            if create:
                target.mkdir(parents=True, exist_ok=True)
            return target
        target = self._legacy_workspace_path(project_id)
        if create:
            target.mkdir(parents=True, exist_ok=True)
        return target

    def allocate_workspace(
        self, project_id: object, name: object = ""
    ) -> Tuple[Path, str, str]:
        """Create the workspace for a **new** project and describe it.

        Returns ``(workspace, published_path, recorded_dir)``:

        * a visible folder when a project root is configured --
          ``<root>/<name> (proj-<id>)``, published as ``<root name>/<folder name>`` and
          recorded as its absolute path;
        * the legacy ``<store>/<id>/workspace`` otherwise, published repository-relative
          and recorded as ``""`` (meaning "the legacy layout").

        The folder is created here, before the record that names it is written: a record
        pointing at a folder it does not have would be a lie the API would publish.
        """

        if not is_safe_project_id(project_id):
            raise ValueError("unsafe project id")
        identifier = str(project_id)
        root = self.visible_root()
        if root is None:
            workspace = self._legacy_workspace_path(identifier)
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace, self.project_path_relative(identifier), ""
        # A project that already has a record owns the folder of its own name: a retry, a
        # move, or a second delivery reuses it instead of inventing ``… (2)``. A folder
        # that exists without a record is not this project's, so the name is numbered.
        folder = visible_folder(
            root, identifier, name, allow_existing=self.exists(identifier)
        )
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UnsafeProjectFolder(
                f"папку проекта не удалось создать: {folder} ({type(exc).__name__})"
            ) from exc
        proved = prove_folder(folder, allowed_roots=(root,), must_exist=True)
        return proved, published_folder_path(root, proved), str(proved)

    def published_project_path(self, project_id: object) -> str:
        """The project's folder as a payload may print it: the record's own value.

        Falls back to the legacy repository-relative form when a record is missing or
        carries no path, so a caller that only knows the id still gets an honest answer.
        """

        record = self.read(project_id)
        if record is not None and str(record.project_path or "").strip():
            return str(record.project_path)
        return self.project_path_relative(project_id)

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
        """Atomically persist one project record and refresh its index entry.

        The deletion marker is re-checked here, at the write itself, and not only at
        the caller: a verification (or an attach) that read the record *before* a
        concurrent delete finished must not be able to put the record back. This is
        the exact path the measured defect used -- read, delete, write -- so the
        refusal lives on the path, not in a caller's convention.
        """

        if self.is_deleted(project.project_id):
            raise ProjectDeleted(project.project_id)
        target = self.path_for(project.project_id)
        payload = json.dumps(
            {
                "schema": SCHEMA_VERSION,
                **project.to_dict(),
                # Written here and nowhere else: ``to_dict`` is the *published* shape and
                # must never carry an absolute path of the host, while the store needs
                # one to find the files again after a restart.
                "workspace_dir": str(project.workspace_dir or ""),
            },
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
        """Resolve one workspace-relative file, re-proving containment.

        Two refusals beyond containment, both from the repository's existing shared
        recognizers rather than a second list: a path that enters a ``.git`` directory,
        and a path the secret recognizer already knows (``.env``, a private key, a
        credential file). A delivered project is a normal folder the owner can commit,
        so a secret must never be written into it by Forge.
        """

        from app.execution.secret_files import is_secret_directory, is_secret_file
        from app.projects.models import is_safe_relative_path

        if not is_safe_relative_path(relative_path):
            raise ValueError("unsafe workspace-relative path")
        parts = str(relative_path).split("/")
        if any(part.lower() == ".git" for part in parts):
            raise ValueError("a project file may not enter a .git directory")
        if is_secret_file(relative_path) or any(
            is_secret_directory(part) for part in parts[:-1]
        ):
            raise ValueError("a secret file is never written into a project workspace")
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

    #: The trash entry *is* the durable deletion marker. ``delete`` creates it before
    #: it moves anything and only a successful ``restore_deleted`` clears it, so the
    #: fact "this id is deleted" survives a restart, needs no in-memory state, and is
    #: readable by a writer that read the record before the deletion finished. A
    #: leftover entry is also how a half-finished delete stays visible instead of
    #: silently resurrecting the project.

    def trash_entries(self, project_id: object) -> List[Path]:
        """Every trash entry for ``project_id``, the newest move first.

        Names are ``<project-id>-<YYYYmmddTHHMMSS>`` (with a numeric collision
        suffix), and project ids contain no ``-`` after their ``proj-`` prefix, so
        the ``<id>-`` prefix cannot accidentally match another project.
        """

        if not is_safe_project_id(project_id):
            return []
        identifier = str(project_id)
        root = self.deleted_root()
        if not root.is_dir():
            return []
        prefix = f"{identifier}-"
        try:
            entries = [
                entry
                for entry in root.iterdir()
                if entry.is_dir() and entry.name.startswith(prefix)
            ]
        except OSError:
            return []
        return sorted(entries, key=lambda item: item.name, reverse=True)

    def newest_trash_entry(self, project_id: object) -> Optional[Path]:
        """The newest move of ``project_id`` into the trash, or ``None``."""

        entries = self.trash_entries(project_id)
        return entries[0] if entries else None

    def is_deleted(self, project_id: object) -> bool:
        """True when the id is in the trash, i.e. must not be written any more.

        Durable and cross-process: it is derived from the trash directory on every
        call, so a late writer in another process sees the same answer a restart
        would.
        """

        return self.newest_trash_entry(project_id) is not None

    def _clear_trash_entry(self, entry: Path) -> bool:
        """Remove the now-empty trash entry, so the id stops being deleted.

        ``rmdir`` is retried for the same measured Windows reason
        :func:`_replace_with_retry` exists (a transient sharing violation). When the
        directory is not empty -- something this layer did not put there -- it is
        renamed instead, so the marker is cleared without erasing anything. ``False``
        means the marker survived both attempts and the caller must not report a
        completed restore.
        """

        delay = 0.02
        for attempt in range(5):
            try:
                entry.rmdir()
                return True
            except FileNotFoundError:
                return True
            except OSError:
                if attempt == 4:
                    break
                time.sleep(delay)
        aside = entry.parent / f"restored-{entry.name}"
        if aside.exists():
            return False
        for attempt in range(5):
            try:
                entry.rename(aside)
                return True
            except FileNotFoundError:
                return True
            except OSError:
                if attempt == 4:
                    return False
                time.sleep(delay)
        return False

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
        """The project's folder and its record, both proved inside a configured root.

        For a project delivered to a visible folder the folder is that folder itself
        (its contents are the project's files). For a legacy project it is
        ``<store>/<id>``, which holds the ``workspace/`` directory -- exactly what it
        always moved, so an existing trash entry and an existing restore keep working.
        """

        record = self.read(project_id)
        recorded = str(getattr(record, "workspace_dir", "") or "") if record else ""
        if recorded:
            folder = prove_folder(recorded, allowed_roots=self.allowed_workspace_roots())
            return folder, self.path_for(project_id)
        record_path = self.path_for(project_id)
        folder = (self.root / project_id).resolve(strict=False)
        root = self.root.resolve(strict=False)
        if folder != root and root not in folder.parents:
            raise ValueError("project folder escapes the store root")
        return folder, record_path

    def delete(
        self, project_id: object, *, stamp: str = ""
    ) -> Dict[str, Any]:
        """Move one project's folder and record into ``projects-deleted``.

        **One trash root, and it is the existing one.** A deleted project -- whether its
        files were in the legacy hidden workspace or in a visible folder on the desktop
        -- is moved to ``<store parent>/projects-deleted/<id>-<timestamp>``, the directory
        :meth:`deleted_root` names. That choice is deliberate: the listing
        (:meth:`list_deleted`), the restore (:meth:`restore_deleted`) and the durable
        "this id is deleted" marker all already work from that one directory, so deletion
        stays a *move* and stays reversible. The visible folder is not copied and not
        emptied: the same folder, with the same files, is renamed into the trash, and the
        record travels beside it.

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
            # An entry that holds nothing is not a deletion: leaving it would
            # tombstone a project that is still live, because the entry is the
            # marker every later write consults.
            try:
                target.rmdir()
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

    def list_deleted(
        self, *, limit: Optional[int] = None, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Every trash entry, newest deletion first. Read-only, names and record facts.

        Beyond the names, the answer carries what the «Удалённые проекты» section has
        to show: when the project was deleted, how many files its current version
        held and what status it had. The record travelled with the folder, so those
        two facts are read from it rather than guessed; an unreadable record is
        reported empty instead of claiming zero files.

        ``limit``/``offset`` are **additive and optional**: called with neither (the
        default, and what every existing caller does) the whole trash is returned in
        exactly the order it always was. Given, they window that same ordered list, so
        a caller paging through dozens of entries sees each one once and in one order.
        Out-of-range values are the caller's to reject; here they simply select
        nothing.
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
        start = max(0, int(offset))
        if limit is None:
            return found[start:]
        return found[start : start + max(0, int(limit))]

    def restore_deleted(self, project_id: object) -> Dict[str, Any]:
        """Move the newest trash entry for ``project_id`` back where the project lived.

        The trashed **record** travels with the folder, so it -- not the current setting
        -- says where the folder belongs: a project that was delivered to a visible
        folder is put back into that exact folder, and a legacy project goes back to
        ``<store>/<id>``. A recorded folder that no longer proves to be inside a
        configured root is not trusted; the project is restored into the legacy location
        instead and the record is rewritten to name it, so a restored project can never
        point at a folder that is not there.

        Refuses -- rather than overwriting -- when a genuinely *active* project
        already uses the id, because a restore must never replace a record the owner
        can see. The move and the clearing of the entry (the deletion marker) happen
        together: callers that mutate a project hold the same per-project lock, so no
        reader observes the folder back while the id still answers "deleted". If the
        entry cannot be cleared the move is rolled back, because a restored project
        that is still tombstoned would be a worse answer than a retryable refusal.
        """

        if not is_safe_project_id(project_id):
            raise UnknownProject(str(project_id))
        identifier = str(project_id)
        if self.exists(identifier):
            raise ProjectAlreadyExists(identifier)
        source = self.newest_trash_entry(identifier)
        if source is None:
            raise UnknownProject(identifier)
        record = source / f"{identifier}.json"
        if not record.is_file():
            raise UnknownProject(identifier)

        folder, rewrite_legacy = self._restore_destination(identifier, record)
        record_target = self.path_for(identifier)
        moved: List[Dict[str, str]] = []
        undo: List[Tuple[Path, Path]] = []
        trash_folder = source / identifier
        try:
            if trash_folder.is_dir():
                _move_path(trash_folder, folder)
                undo.append((folder, trash_folder))
                moved.append(
                    {"from": self._relative(trash_folder), "to": self._relative(folder)}
                )
            _move_path(record, record_target)
            undo.append((record_target, record))
            moved.append(
                {
                    "from": self._relative(record),
                    "to": self._relative(record_target),
                }
            )
        except BaseException:
            for live, trashed in reversed(undo):
                try:
                    _move_path(live, trashed)
                except OSError:
                    pass
            raise

        if not self._clear_trash_entry(source):
            for live, trashed in reversed(undo):
                try:
                    _move_path(live, trashed)
                except OSError:
                    pass
            raise ProjectError(
                f"project '{identifier}' could not be restored: its trash entry is "
                "still there and nothing was moved"
            )

        restored = self.read(identifier)
        if restored is not None and rewrite_legacy:
            # The record named a visible folder this deployment may no longer use; the
            # files are back in the legacy workspace, so the record says so.
            restored = replace(
                restored,
                workspace_dir="",
                project_path=self.project_path_relative(identifier),
            )
            try:
                self.write(restored)
            except ProjectDeleted:  # pragma: no cover - the marker was just cleared
                pass
        if restored is not None:
            self._refresh_index(restored)
        return {
            "project_id": identifier,
            "restored": restored is not None,
            "trash_name": source.name,
            "moved": moved,
            "project": restored.to_dict() if restored is not None else None,
        }

    def _restore_destination(
        self, identifier: str, trashed_record: Path
    ) -> Tuple[Path, bool]:
        """Where a restore puts the folder back, and whether the record must be rewritten.

        The answer comes from the trashed record, proved against the roots this
        deployment configured. Three cases, and each one puts the folder where the files
        will actually resolve:

        * a recorded visible folder that still proves to sit inside an allowed root -- the
          folder itself goes back there, and the record is left alone;
        * a recorded visible folder that does **not** prove any more (the root setting
          changed, the record was edited) -- the files go into the legacy workspace
          ``<store>/<id>/workspace`` and the record is rewritten to say so, because a
          restored project must never point at a folder that is not there;
        * no recorded folder (a legacy project, or an unreadable record) -- the legacy
          project directory ``<store>/<id>``, which is exactly what a legacy delete moved.
        """

        legacy_project = self.root / identifier
        try:
            data = json.loads(trashed_record.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return legacy_project, False
        if not isinstance(data, dict):
            return legacy_project, False
        recorded = str(data.get("workspace_dir", "") or "")
        if not recorded:
            return legacy_project, False
        try:
            return (
                prove_folder(recorded, allowed_roots=self.allowed_workspace_roots()),
                False,
            )
        except UnsafeProjectFolder:
            return self._legacy_workspace_path(identifier), True


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
