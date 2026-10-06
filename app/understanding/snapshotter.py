"""Coordinator creating immutable, verifiable UnderstandingSnapshot instances."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Optional, Sequence
from uuid import uuid4

from app.tools.workspace import Workspace
from app.understanding.extractors.base import BaseExtractor
from app.understanding.models import (
    ScanLimits,
    UnderstandingSnapshot,
)
from app.understanding.scanner import BoundedProjectScanner
from app.understanding.topology import ProjectTopologyBuilder


class UnderstandingSnapshotter:
    """Create and verify immutable, reproducible UnderstandingSnapshot artifacts."""

    def __init__(
        self,
        limits: Optional[ScanLimits] = None,
        extractors: Optional[Sequence[BaseExtractor]] = None,
    ) -> None:
        self.scanner = BoundedProjectScanner(limits=limits, extractors=extractors)
        self.topology_builder = ProjectTopologyBuilder()

    def create_snapshot(
        self,
        workspace: Workspace,
        *,
        project_id: str = "default_project",
        parent_snapshot_id: Optional[str] = None,
    ) -> UnderstandingSnapshot:
        """Perform a full bounded scan and return a complete UnderstandingSnapshot."""
        files, facts, manifests, warnings = self.scanner.scan(workspace)
        topology = self.topology_builder.build(facts=facts, files=files)

        # Calculate reproducible workspace fingerprint
        fingerprint_digest = hashlib.sha256()
        for f in sorted(files, key=lambda x: x.relative_path):
            fingerprint_digest.update(f"{f.relative_path}:{f.fingerprint or 'none'}\n".encode("utf-8"))
        workspace_fingerprint = fingerprint_digest.hexdigest()

        extractor_versions = {
            ext.extractor_id: type(ext).__name__
            for ext in self.scanner.extractors
        }

        snapshot_id = f"snapshot-{uuid4()}"
        created_at = datetime.now(timezone.utc).isoformat()

        return UnderstandingSnapshot(
            snapshot_id=snapshot_id,
            project_id=project_id,
            workspace_fingerprint=workspace_fingerprint,
            files=files,
            manifests=manifests,
            topology=topology,
            facts=facts,
            warnings=warnings,
            extractor_versions=extractor_versions,
            parent_snapshot_id=parent_snapshot_id,
            created_at=created_at,
        )

    def check_drift(
        self,
        snapshot: UnderstandingSnapshot,
        workspace: Workspace,
    ) -> tuple[bool, tuple[str, ...]]:
        """Check if workspace disk state has drifted from snapshot.

        Returns (is_fresh: bool, drifted_files: tuple[str, ...]).
        """
        drifted: list[str] = []
        # Re-scan file inventory quickly
        current_files, _, _, _ = self.scanner.scan(workspace)
        current_map = {f.relative_path: f.fingerprint for f in current_files}
        snapshot_map = {f.relative_path: f.fingerprint for f in snapshot.files}

        for path, snap_fp in snapshot_map.items():
            curr_fp = current_map.get(path)
            if curr_fp != snap_fp:
                drifted.append(path)

        for path in current_map:
            if path not in snapshot_map:
                drifted.append(path)

        is_fresh = len(drifted) == 0
        return (is_fresh, tuple(sorted(drifted)))
