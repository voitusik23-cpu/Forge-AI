"""Read-only snapshots of an explicitly requested Workspace file set."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Iterable

from app.artifacts import Artifact, ArtifactType
from app.orchestrator.models import Event, EventType, Run
from app.snapshots import ProjectSnapshot, SnapshotFile
from app.tools.workspace import Workspace, WorkspacePathError


class ProjectSnapshotError(ValueError):
    """A requested snapshot could not be read safely inside its Workspace."""


class ProjectSnapshotter:
    """Observe only the caller's explicit relative file paths."""

    def create(
        self,
        requested_paths: Iterable[str],
        *,
        run: Run,
        attempt_number: int,
        workspace: Workspace | None,
    ) -> ProjectSnapshot:
        if not isinstance(workspace, Workspace):
            raise ProjectSnapshotError("snapshot workspace is unavailable")
        if isinstance(requested_paths, (str, bytes)):
            raise ProjectSnapshotError("snapshot paths must be an explicit iterable")
        try:
            requested = tuple(requested_paths)
        except TypeError as exc:
            raise ProjectSnapshotError("snapshot paths must be iterable") from exc
        if (
            isinstance(attempt_number, bool)
            or not isinstance(attempt_number, int)
            or attempt_number < 0
        ):
            raise ProjectSnapshotError("snapshot attempt number is invalid")

        normalized: dict[str, tuple[str, ...]] = {}
        try:
            for relative_path in requested:
                target, safe_path, parts = workspace.resolve_target(relative_path)
                workspace.verify_target(parts)
                normalized[safe_path] = parts
            files = tuple(
                self._snapshot_file(path, parts, workspace)
                for path, parts in sorted(normalized.items())
            )
        except WorkspacePathError as exc:
            raise ProjectSnapshotError("snapshot path was rejected by Workspace") from exc
        except OSError as exc:
            raise ProjectSnapshotError("snapshot file could not be read safely") from exc

        snapshot = ProjectSnapshot(
            run_id=run.id,
            attempt_number=attempt_number,
            files=files,
        )
        run.project_snapshots.append(snapshot)
        artifact = None
        if files:
            artifact = Artifact(
                run_id=run.id,
                artifact_type=ArtifactType.PROJECT_SNAPSHOT,
                project_snapshot_id=snapshot.snapshot_id,
            )
            run.artifacts.append(artifact)
        run.events.append(Event(
            run_id=run.id,
            type=EventType.SNAPSHOT_CREATED,
            data={
                "run_id": run.id,
                "snapshot_id": snapshot.snapshot_id,
                "attempt_number": attempt_number,
                "number_of_files": len(files),
                "number_existing": sum(item.exists for item in files),
                "number_missing": sum(not item.exists for item in files),
                "status": snapshot.status.value,
                "artifact_id": artifact.artifact_id if artifact else None,
                "files": [
                    {
                        "relative_path": item.relative_path,
                        "exists": item.exists,
                        "fingerprint": item.fingerprint,
                    }
                    for item in files
                ],
            },
        ))
        return snapshot

    @classmethod
    def _snapshot_file(
        cls,
        relative_path: str,
        parts: tuple[str, ...],
        workspace: Workspace,
    ) -> SnapshotFile:
        target = workspace.verify_target(parts)
        try:
            info = target.lstat()
        except FileNotFoundError:
            workspace.verify_target(parts)
            return SnapshotFile(relative_path, False, None)
        if not stat.S_ISREG(info.st_mode):
            raise ProjectSnapshotError("snapshot target is not a regular file")

        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        workspace.verify_target(parts)
        return SnapshotFile(relative_path, True, digest.hexdigest())
