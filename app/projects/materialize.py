"""Materialise one model answer into a project's **own** controlled workspace.

What this module adds, and what it deliberately reuses
------------------------------------------------------

Before this module a write-mode run applied its answer inside the *repository*
workspace. For a lifecycle project that is the wrong tree: the project owns
``.forge/projects/<project_id>/workspace``, and a file that lands anywhere else is
not part of the project. :func:`materialize` closes that gap **without writing a
second code path**: it calls the existing
:func:`app.agent_runtime.code_writer.apply_changes` with the project workspace as
its only perimeter, so every rail that already exists still decides:

* the workspace boundary (``WorkspaceBoundary.resolve_relative``) refuses ``..``,
  an absolute path, and any path that resolves outside the workspace;
* the writer's deny list refuses ``.env``/``.env.*``, ``.git/**``, ``.venv``,
  ``node_modules``, ``dist``, the migration history, ``authorizer.py`` and
  credential-shaped names (``secret``/``token``/``credential``) at any depth;
* the writer's own caps (20 files, 200 KiB), atomic writes with backups,
  ``py_compile`` verification and full rollback are unchanged.

This module re-implements none of that. Its only job is the two things the writer
does not do:

1. **build the artifact manifest** from the bytes that are really on disk now --
   ``path``, ``kind``, ``size``, a SHA-256 digest, whether the file was created or
   modified, and how the run's verification judged it -- and
2. **state one typed result**: ``PROJECT_NOT_CREATED`` (the write path was
   unavailable, the answer was rejected, or it wrote zero files), ``FILES_CREATED``
   (files exist, nothing was compiled), ``VERIFIED`` (files exist and every Python
   file compiled) or ``FAILED`` (the verification failed, or an artifact could not
   be read back).

Contents never leave this module. The manifest holds sizes and digests; the bytes
stay in the project workspace, so an event, a record or an API payload can describe
a file without ever carrying the code -- or a credential that ended up in it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.agent_runtime.code_writer import (
    STATUS_APPLIED,
    STATUS_VERIFICATION_FAILED,
    WriteResult,
    apply_changes,
)
from app.projects.models import (
    ARTIFACT_CHANGE_CREATE,
    ARTIFACT_CHANGE_UPDATE,
    ArtifactRef,
    VERIFICATION_FAILED,
    VERIFICATION_NOT_APPLICABLE,
    VERIFICATION_NOT_RUN,
    VERIFICATION_PASSED,
    kind_for_path,
    utc_now,
    verification_relevance_for_path,
)
from app.projects.store import ProjectStore, is_safe_project_id
from app.tools.workspace import Workspace, WorkspacePathError

__all__ = [
    "FILES_CREATED",
    "FAILED",
    "MaterializationResult",
    "PROJECT_NOT_CREATED",
    "VERIFIED",
    "materialize",
    "materialize_into",
]


#: No artifact exists: the write path was unavailable, the answer was refused, or
#: it wrote nothing. This is the honest word for an empty project, and it is the
#: only status from which no later stage may be claimed.
PROJECT_NOT_CREATED = "PROJECT_NOT_CREATED"
#: Files exist and the run's verification judged none of them (no Python file).
FILES_CREATED = "FILES_CREATED"
#: Files exist and every Python file the run wrote compiled.
VERIFIED = "VERIFIED"
#: The verification failed, or an applied artifact could not be read back.
FAILED = "FAILED"

#: The largest artifact this module will hash into a manifest. The writer's own
#: per-run cap is 200 KiB, so this can never truncate a real run; it keeps a
#: corrupted record from making one read unbounded.
MAX_ARTIFACT_BYTES = 512 * 1024


@dataclass(frozen=True)
class MaterializationResult:
    """What one answer did to one project workspace.

    ``files`` is the artifact manifest, described with the same
    :class:`app.projects.models.ArtifactRef` the version record stores, so the run
    path and the project record can never describe the same file two ways.
    ``write_result`` is the writer's own report, kept for the run store; it is not
    serialised into this result's public shape.
    """

    status: str
    project_id: str = ""
    run_id: str = ""
    #: The project's folder, **repository-relative** (``.forge/projects/<id>/workspace``).
    project_path: str = ""
    files: Tuple[ArtifactRef, ...] = ()
    verification: str = VERIFICATION_NOT_RUN
    reason: str = ""
    timestamp: str = ""
    write_result: Optional[WriteResult] = field(default=None, repr=False, compare=False)

    @property
    def files_created(self) -> int:
        """How many files this materialisation recorded (0 for a refusal)."""

        if self.status == PROJECT_NOT_CREATED:
            return 0
        return len(self.files)

    @property
    def is_success(self) -> bool:
        """True when files were really written (``FILES_CREATED``/``VERIFIED``)."""

        return self.status in (FILES_CREATED, VERIFIED)

    def manifest(self) -> List[Dict[str, Any]]:
        """The manifest, path- and hash-only, with the joined project location.

        ``change`` is the created/modified marker (``create``/``update``); it is the
        writer's own action word, never inferred here. ``timestamp`` is when this
        materialisation recorded the file.
        """

        rows: List[Dict[str, Any]] = []
        for artifact in self.files:
            rows.append(
                {
                    **artifact.to_dict(),
                    "location": (
                        f"{self.project_path}/{artifact.path}"
                        if self.project_path
                        else artifact.path
                    ),
                    "timestamp": self.timestamp,
                }
            )
        return rows

    def to_dict(self) -> Dict[str, Any]:
        """The public shape: status, identifiers, counts and the manifest.

        There is no content, no diff and no absolute path in it.
        """

        return {
            "status": self.status,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "project_path": self.project_path,
            "files_created": self.files_created,
            "verification": self.verification,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "files": self.manifest(),
        }


def _refused(
    *,
    reason: str,
    status: str = PROJECT_NOT_CREATED,
    project_id: str = "",
    run_id: str = "",
    project_path: str = "",
    verification: str = VERIFICATION_NOT_RUN,
    write_status: str = "rejected",
) -> MaterializationResult:
    return MaterializationResult(
        status=status,
        project_id=project_id,
        run_id=run_id,
        project_path=project_path,
        verification=verification,
        reason=reason,
        timestamp=utc_now(),
        write_result=WriteResult(status=write_status, reason=reason, verification=verification),
    )


def _manifest_of(
    workspace: Path,
    files: Tuple[Mapping[str, Any], ...],
    *,
    timestamp: str,
) -> Tuple[Tuple[ArtifactRef, ...], str]:
    """Describe every written file from the bytes on disk; never keep the bytes.

    Returns ``(manifest, problem)``. A file that cannot be read back is reported as
    a problem rather than described from the writer's own report: the manifest's
    digest must be a fact about the workspace, not a restatement of what the
    writer said it wrote.
    """

    recorded: List[ArtifactRef] = []
    for entry in files:
        path = str(entry.get("path", "") or "")
        if not path:
            return (), "written_file_path_missing"
        action = str(entry.get("action", "") or "").strip()
        if action not in (ARTIFACT_CHANGE_CREATE, ARTIFACT_CHANGE_UPDATE):
            action = ARTIFACT_CHANGE_CREATE
        target = workspace.joinpath(*path.split("/"))
        try:
            if target.is_symlink() or not target.is_file():
                return (), f"written_file_missing:{path}"
            data = target.read_bytes()
        except OSError:
            return (), f"written_file_unreadable:{path}"
        if len(data) > MAX_ARTIFACT_BYTES:
            return (), f"written_file_too_large:{path}"
        recorded.append(
            ArtifactRef(
                path=path,
                kind=kind_for_path(path),
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                change=action,
                verification_relevance=verification_relevance_for_path(path),
            )
        )
    return tuple(recorded), ""


def materialize_into(
    workspace: object,
    answer_text: object,
    run_id: str,
    *,
    project_id: str = "",
    project_path: str = "",
) -> MaterializationResult:
    """Apply ``answer_text`` inside ``workspace`` and return the typed result.

    ``workspace`` is the project's own workspace directory -- an absolute
    ``Path`` or an existing :class:`app.tools.workspace.Workspace`. It is the whole
    perimeter: the writer cannot touch anything outside it, and this function adds
    no second rule about paths.

    Used by the run path, which resolved the workspace from the project store at
    composition time; :func:`materialize` is the same operation for a caller that
    holds the project record instead.
    """

    try:
        boundary = workspace if isinstance(workspace, Workspace) else Workspace(root=workspace)
        root = Path(boundary.root)
    except (WorkspacePathError, OSError, RuntimeError, TypeError, ValueError):
        return _refused(
            reason="project_workspace_unavailable",
            project_id=project_id,
            run_id=run_id,
            project_path=project_path,
        )

    if not root.is_dir():
        return _refused(
            reason="project_workspace_missing",
            project_id=project_id,
            run_id=run_id,
            project_path=project_path,
        )

    if not isinstance(answer_text, str) or not answer_text.strip():
        return _refused(
            reason="the model answer is empty",
            project_id=project_id,
            run_id=run_id,
            project_path=project_path,
            write_status="no_changes",
        )

    try:
        write_result = apply_changes(answer_text, workspace=boundary, run_id=run_id)
    except Exception as error:  # noqa: BLE001 - a writer failure is never a write
        return _refused(
            reason=f"writer_error:{type(error).__name__}",
            project_id=project_id,
            run_id=run_id,
            project_path=project_path,
        )

    timestamp = utc_now()

    if write_result.status == STATUS_VERIFICATION_FAILED:
        return MaterializationResult(
            status=FAILED,
            project_id=project_id,
            run_id=run_id,
            project_path=project_path,
            verification=VERIFICATION_FAILED,
            reason=write_result.reason or "verification_failed",
            timestamp=timestamp,
            write_result=write_result,
        )

    if write_result.status != STATUS_APPLIED or not write_result.files:
        # ``rejected`` and ``no_changes`` are the same fact for the owner: nothing
        # was written. The writer's own reason travels with it.
        return MaterializationResult(
            status=PROJECT_NOT_CREATED,
            project_id=project_id,
            run_id=run_id,
            project_path=project_path,
            verification=VERIFICATION_NOT_RUN,
            reason=write_result.reason or "no files were written",
            timestamp=timestamp,
            write_result=write_result,
        )

    manifest, problem = _manifest_of(
        root, tuple(write_result.files), timestamp=timestamp
    )
    if problem:
        return MaterializationResult(
            status=FAILED,
            project_id=project_id,
            run_id=run_id,
            project_path=project_path,
            files=manifest,
            verification=VERIFICATION_FAILED,
            reason=problem,
            timestamp=timestamp,
            write_result=write_result,
        )

    verification = str(write_result.verification or VERIFICATION_NOT_RUN)
    if verification == VERIFICATION_PASSED:
        status = VERIFIED
    elif verification == VERIFICATION_NOT_APPLICABLE:
        # Files exist; nothing was compiled. Claiming "verified" here would be the
        # exact overstatement this module exists to prevent.
        status = FILES_CREATED
    elif verification == VERIFICATION_FAILED:
        status = FAILED
    else:
        status = FILES_CREATED

    return MaterializationResult(
        status=status,
        project_id=project_id,
        run_id=run_id,
        project_path=project_path,
        files=manifest,
        verification=verification,
        reason=write_result.reason,
        timestamp=timestamp,
        write_result=write_result,
    )


def materialize(
    project: object,
    answer_text: object,
    run_id: str,
    *,
    store: Optional[ProjectStore] = None,
    workspace: Optional[object] = None,
) -> MaterializationResult:
    """Materialise one answer into the workspace the project owns.

    The workspace is resolved from the project store -- never from the answer, the
    run, or the process directory -- and re-proved to be the project's own
    directory before a single byte is written. When the caller already resolved it
    (the API composition does), the supply is checked against the store's own
    answer, so a caller cannot point this function at the repository tree by
    naming a different directory.
    """

    project_id = str(getattr(project, "project_id", "") or "")
    project_path = str(getattr(project, "project_path", "") or "")
    if not is_safe_project_id(project_id):
        return _refused(
            reason="unsafe_project_id",
            run_id=run_id,
        )

    resolved_store = store if store is not None else ProjectStore()
    try:
        expected = resolved_store.workspace_path(project_id)
    except ValueError:
        return _refused(
            reason="project_workspace_unavailable",
            project_id=project_id,
            project_path=project_path,
            run_id=run_id,
        )

    if workspace is None:
        target: object = expected
    else:
        try:
            supplied = (
                workspace
                if isinstance(workspace, Workspace)
                else Workspace(root=workspace)
            )
        except (WorkspacePathError, OSError, RuntimeError, TypeError, ValueError):
            return _refused(
                reason="project_workspace_unavailable",
                project_id=project_id,
                project_path=project_path,
                run_id=run_id,
            )
        try:
            same = Path(supplied.root).resolve(strict=False) == expected.resolve(
                strict=False
            )
        except (OSError, RuntimeError):
            same = False
        if not same:
            return _refused(
                reason="workspace_is_not_the_project_workspace",
                project_id=project_id,
                project_path=project_path,
                run_id=run_id,
            )
        target = supplied

    if not project_path:
        project_path = resolved_store.project_path_relative(project_id)

    return materialize_into(
        target,
        answer_text,
        run_id,
        project_id=project_id,
        project_path=project_path,
    )
