"""Server-side project discovery for a production run.

``ProjectDiscovery`` is the observation layer of the agent loop. It produces one
bounded, immutable ``UnderstandingSnapshot`` for the workspace of a run, using the
existing bounded scanner and snapshotter - there is no second discovery system.

Trust boundaries:

* the **only** input is a ``Workspace`` supplied by trusted server-side
  composition. A caller, a task request, a decision provider, or an LLM cannot
  choose the root, choose a scanner, change the scan limits, disable a boundary,
  or obtain a filesystem handle;
* discovery reads through the existing ``BoundedProjectScanner``, which already
  enforces file count, per-file size, total byte, symlink, ignored-directory, and
  secret-file policies. Those policies are not duplicated here;
* discovery never executes anything: no subprocess, no shell, no
  ``ExecutionCoordinator``, no adapter. It is pure filesystem observation inside
  the workspace;
* it grants no authority. A snapshot informs the decision; it can never widen a
  ``RunScope``, add a command, a tool, a profile, an environment value, or a
  timeout.

The snapshot is immutable (``UnderstandingSnapshot`` is frozen) and belongs to
exactly one run: it is created fresh at the start of the run and is never reused
across runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.tools.workspace import Workspace
from app.understanding.models import ScanLimits, UnderstandingSnapshot
from app.understanding.snapshotter import UnderstandingSnapshotter


class ProjectDiscoveryError(RuntimeError):
    """Raised when bounded discovery cannot complete."""


@dataclass(frozen=True)
class DiscoveryOutcome:
    """Result of one bounded discovery pass.

    ``snapshot`` is ``None`` only when discovery failed; a failure is never
    presented as an empty-but-successful observation.
    """

    run_id: str
    snapshot: UnderstandingSnapshot | None
    duration_seconds: float
    failure_category: str = ""

    @property
    def succeeded(self) -> bool:
        return self.snapshot is not None

    def event_metadata(self) -> Mapping[str, object]:
        """Sanitized, bounded metadata for the durable history.

        Only counts, the bounded fingerprint, and a safe failure category are
        reported. No file contents, no paths outside the workspace, no secrets,
        no raw environment, and no stdin/stdout ever appear here.
        """
        snapshot = self.snapshot
        if snapshot is None:
            return {
                "run_id": self.run_id,
                "status": "failed",
                "failure_category": self.failure_category,
                "duration_seconds": round(self.duration_seconds, 3),
            }
        summary = {
            "snapshot_id": snapshot.snapshot_id,
            "workspace_fingerprint": snapshot.workspace_fingerprint,
            "file_count": len(snapshot.files),
            "manifest_count": len(snapshot.manifests),
            "fact_count": len(snapshot.facts),
            "warning_count": len(snapshot.warnings),
            "node_count": len(snapshot.topology.nodes),
            "edge_count": len(snapshot.topology.edges),
        }
        return {
            "run_id": self.run_id,
            "status": "completed",
            "duration_seconds": round(self.duration_seconds, 3),
            **summary,
        }


class ProjectDiscovery:
    """Bounded, deterministic, non-authoritative project observation."""

    def __init__(
        self,
        limits: ScanLimits | None = None,
        snapshotter: UnderstandingSnapshotter | None = None,
    ) -> None:
        self._snapshotter = snapshotter or UnderstandingSnapshotter(limits=limits)

    def observe(self, workspace: Workspace, *, run_id: str) -> DiscoveryOutcome:
        """Produce one bounded snapshot of ``workspace`` for ``run_id``.

        The workspace is the trusted root; nothing here can redirect it. Any
        failure is reported as a failure category rather than a partial snapshot,
        so a run can never treat an unusable observation as a complete one.
        """
        import time

        if not isinstance(workspace, Workspace):
            raise ProjectDiscoveryError("discovery requires a trusted Workspace")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ProjectDiscoveryError("discovery requires a run id")

        started = time.perf_counter()
        try:
            snapshot = self._snapshotter.create_snapshot(
                workspace, project_id=run_id
            )
        except Exception as exc:  # noqa: BLE001 - classify, never mask
            return DiscoveryOutcome(
                run_id=run_id,
                snapshot=None,
                duration_seconds=time.perf_counter() - started,
                failure_category=type(exc).__name__,
            )
        return DiscoveryOutcome(
            run_id=run_id,
            snapshot=snapshot,
            duration_seconds=time.perf_counter() - started,
        )
