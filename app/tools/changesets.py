"""Collect fingerprints for explicitly targeted file mutations only."""

from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass

from app.artifacts import (
    Artifact,
    ChangeSet,
    FileChange,
    FileChangeType,
)
from app.orchestrator.models import Event, EventType, Run
from app.tools.contracts import ToolInvocation
from app.tools.permissions import ToolExecutionContext
from app.tools.workspace import Workspace, WorkspacePathError


@dataclass(frozen=True)
class _MutationSnapshot:
    run_id: str
    attempt_number: int
    relative_path: str
    parts: tuple[str, ...]
    before_fingerprint: str | None
    workspace: Workspace


@dataclass
class _PendingChange:
    snapshot: _MutationSnapshot
    after_fingerprint: str


class ChangeSetCollector:
    """Build one in-memory ChangeSet per Run attempt from known tool targets."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, int, str], _PendingChange] = {}

    def capture_before(
        self,
        invocation: ToolInvocation,
        context: ToolExecutionContext | None,
    ) -> _MutationSnapshot | None:
        if not isinstance(context, ToolExecutionContext) or not isinstance(context.workspace, Workspace):
            return None
        try:
            target, relative_path, parts = context.workspace.resolve_target(
                invocation.input.get("relative_path")
            )
            context.workspace.verify_target(parts)
            fingerprint = self._fingerprint_if_present(target)
            context.workspace.verify_target(parts)
            return _MutationSnapshot(
                context.run_id,
                context.attempt_number,
                relative_path,
                parts,
                fingerprint,
                context.workspace,
            )
        except (OSError, ValueError, WorkspacePathError):
            return None

    def record_after(self, snapshot: _MutationSnapshot | None) -> None:
        if snapshot is None:
            return
        try:
            target = snapshot.workspace.verify_target(snapshot.parts)
            fingerprint = self._fingerprint_if_present(target)
            snapshot.workspace.verify_target(snapshot.parts)
        except (OSError, ValueError, WorkspacePathError):
            return
        if fingerprint is None:
            return
        key = (snapshot.run_id, snapshot.attempt_number, snapshot.relative_path)
        current = self._pending.get(key)
        if current is None:
            self._pending[key] = _PendingChange(snapshot, fingerprint)
        else:
            current.after_fingerprint = fingerprint

    def finalize(self, run: Run, attempt_number: int) -> ChangeSet | None:
        keys = [
            key for key in self._pending
            if key[0] == run.id and key[1] == attempt_number
        ]
        changes = []
        for key in sorted(keys, key=lambda item: item[2]):
            pending = self._pending.pop(key)
            before = pending.snapshot.before_fingerprint
            after = pending.after_fingerprint
            if before == after:
                continue
            changes.append(FileChange(
                relative_path=pending.snapshot.relative_path,
                change_type=FileChangeType.CREATED if before is None else FileChangeType.MODIFIED,
                before_fingerprint=before,
                after_fingerprint=after,
            ))
        if not changes:
            return None

        changeset = ChangeSet(
            run_id=run.id,
            attempt_number=attempt_number,
            changes=tuple(changes),
        )
        artifact = Artifact(run_id=run.id, changeset_id=changeset.changeset_id)
        run.change_sets.append(changeset)
        run.artifacts.append(artifact)
        run.events.append(Event(
            run_id=run.id,
            type=EventType.CHANGESET_CREATED,
            data={
                "run_id": run.id,
                "changeset_id": changeset.changeset_id,
                "attempt_number": attempt_number,
                "number_of_changes": len(changes),
                "status": changeset.status.value,
                "artifact_id": artifact.artifact_id,
                "changes": [
                    {
                        "relative_path": item.relative_path,
                        "change_type": item.change_type.value,
                        "before_fingerprint": item.before_fingerprint,
                        "after_fingerprint": item.after_fingerprint,
                    }
                    for item in changes
                ],
            },
        ))
        return changeset

    @staticmethod
    def attach_outcomes(
        run: Run,
        attempt_number: int,
        *,
        verification_status: str | None,
        acceptance_status: str | None,
    ) -> None:
        for changeset in reversed(run.change_sets):
            if changeset.attempt_number == attempt_number:
                changeset.verification_status = verification_status
                changeset.acceptance_status = acceptance_status
                return

    @staticmethod
    def _fingerprint_if_present(target) -> str | None:
        try:
            info = target.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise WorkspacePathError("target is not a regular file")
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
