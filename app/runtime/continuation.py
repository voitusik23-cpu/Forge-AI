"""Project Factory continuation: recover context, then decide what may continue.

WHAT THIS MODULE IS
===================

The integration piece of Block #7. Before a Project Factory task continues, this
module answers, from evidence on disk:

1. **What is the project now?** -- a real bounded scan of the workspace, through
   the existing :class:`app.understanding.snapshotter.UnderstandingSnapshotter`,
   producing the same ``workspace_fingerprint`` formula the snapshotter already
   uses.
2. **What did we believe when we last looked?** -- the last saved
   :class:`ProjectFactoryContext`, read back from the existing ``RunStore``.
3. **What unfinished operations exist?** -- the recovery planner's discovery over
   the same store, plus the workflow's own recorded build/verify attempts.
4. **Which evidence is stale?** -- the Project Memory freshness evaluation,
   applied to the memory document and the saved context's own pinned files.
5. **What may continue?** -- a :class:`ContinuationStrategy`: a *plan*, with the
   per-attempt recovery decisions and the stale-evidence markers.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
=========================================

* It never builds a ``RunScope``, never consults an ``ApprovalPolicy`` as a grant,
  never constructs an adapter or a coordinator, and never dispatches anything.
* It never returns an approval, a token of authority, or a widened allowlist. A
  ``ContinuationStrategy`` is data.
* It never treats saved memory as authorization. Memory influences *what is worth
  trying*; it never influences *what is permitted*. The only thing that can permit
  an action remains the existing chain -- frozen ``RunScope`` ->
  ``RunExecutor`` -> ``ExecutionCoordinator`` -> ``AuthorizedExecution`` ->
  ``LocalExecutionAdapter`` -- re-evaluated for every dispatch, including every
  dispatch a continuation causes.

An explicit, testable expression of that separation: :meth:`ContinuationStrategy.to_dict`
has no member that can be read as a permission, and this module contains no import
of ``ExecutionCoordinator``, ``LocalExecutionAdapter``, ``ApprovalPolicy``'s
resolution path, or ``RunScope``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.context.models import ContextSourceType
from app.runtime.recovery import (
    RecoveryDecision,
    RecoveryFinding,
    RecoveryPlan,
    RecoveryPlanner,
)
from app.runtime.run_store import RunStateSnapshot, RunStore
from app.tools.workspace import Workspace
from app.understanding.memory import (
    FreshnessIndex,
    FreshnessOutcome,
    MemoryRecord,
    MemoryStatus,
    ProjectMemory,
    ProjectMemoryStore,
    build_freshness_index,
    freshness_of,
)
from app.understanding.models import UnderstandingSnapshot

__all__ = [
    "PROJECT_FACTORY_CONTEXT_KEY",
    "PROJECT_FACTORY_CONTEXT_SCHEMAS",
    "ContinuationStrategy",
    "ContextDrift",
    "ContextRecall",
    "ProjectFactoryContext",
    "ProjectFactoryContextStore",
    "UnfinishedOperation",
    "build_workspace_fingerprint",
    "plan_continuation",
    "recall_context",
]

#: The ``project_state`` key the factory context occupies inside a ``RunStore``
#: snapshot. One store, three keys -- step graph, recovery claims, factory context.
PROJECT_FACTORY_CONTEXT_KEY = "project_factory_context"

#: Accepted context document versions. ``v1`` predates the recall boundary; it is
#: still readable, but it cannot express *why* a file was unrecallable, so a
#: ``v1`` document is upgraded on the next save rather than rejected.
PROJECT_FACTORY_CONTEXT_SCHEMAS = ("forge.project_factory_context.v1", "forge.project_factory_context.v2")
_CONTEXT_SCHEMA = PROJECT_FACTORY_CONTEXT_SCHEMAS[-1]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_workspace_fingerprint(snapshot: UnderstandingSnapshot) -> str:
    """The workspace fingerprint formula the snapshotter already uses.

    Kept identical on purpose: the fingerprint must be comparable with one an
    earlier ``UnderstandingSnapshotter.create_snapshot`` produced, or a saved
    context could never be compared with a fresh scan. No second hashing scheme.
    """
    digest = hashlib.sha256()
    for entry in sorted(snapshot.files, key=lambda item: item.relative_path):
        digest.update(
            f"{entry.relative_path}:{entry.fingerprint or 'none'}\n".encode("utf-8")
        )
    return digest.hexdigest()


@dataclass(frozen=True)
class ContextRecall:
    """What was and was not recallable when the context was saved.

    Named explicitly so a reader can never mistake a partial context for a
    complete one. ``unrecallable_paths`` is recorded rather than silently dropped:
    an absence that is not recorded is indistinguishable from an absence that was
    never looked for.
    """

    workspace_root: str = ""
    unrecallable_paths: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_root": self.workspace_root,
            "unrecallable_paths": list(self.unrecallable_paths),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextRecall":
        raw = data.get("unrecallable_paths") or ()
        return cls(
            workspace_root=str(data.get("workspace_root", "")),
            unrecallable_paths=tuple(str(item) for item in raw),
            note=str(data.get("note", "")),
        )


@dataclass(frozen=True)
class ProjectFactoryContext:
    """The saved understanding of one Project Factory task.

    **Data, not authority.** ``workspace_root`` is recorded so drift can be
    reported, and it is never used to widen anything: a continuation re-derives its
    workspace from the caller's own composition, and the existing ``RunScope``
    boundary is what actually contains execution.

    ``unrecallable_paths`` records what the scan could not fingerprint (secret
    files and unreadable files) using the scanner's own exclusion decision. The
    path *names* are recorded; no content, digest, or value from those files is,
    which is what keeps credential material out of memory.
    """

    context_id: str
    project_id: str = ""
    workspace_fingerprint: str = ""
    task_id: str = ""
    goal: str = ""
    build_declaration_id: str = ""
    verification_declaration_id: str = ""
    planned_step_ids: tuple[str, ...] = ()
    recorded_attempts: tuple[Mapping[str, Any], ...] = ()
    evidence_memory_ids: tuple[str, ...] = ()
    #: The relative paths the scan saw when this context was captured, so a later
    #: comparison can name what was added and what was removed rather than only
    #: reporting that the aggregate fingerprint moved.
    planned_paths: tuple[str, ...] = ()
    recall: ContextRecall = field(default_factory=ContextRecall)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": _CONTEXT_SCHEMA,
            "context_id": self.context_id,
            "project_id": self.project_id,
            "workspace_fingerprint": self.workspace_fingerprint,
            "task_id": self.task_id,
            "goal": self.goal,
            "build_declaration_id": self.build_declaration_id,
            "verification_declaration_id": self.verification_declaration_id,
            "planned_step_ids": list(self.planned_step_ids),
            "recorded_attempts": [dict(item) for item in self.recorded_attempts],
            "evidence_memory_ids": list(self.evidence_memory_ids),
            "planned_paths": list(self.planned_paths),
            "recall": self.recall.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectFactoryContext":
        if not isinstance(data, Mapping):
            raise ValueError("a saved factory context must be a mapping")
        schema = str(data.get("schema", ""))
        if schema not in PROJECT_FACTORY_CONTEXT_SCHEMAS:
            raise ValueError(f"unsupported factory context schema: {schema!r}")
        context_id = data.get("context_id")
        if not isinstance(context_id, str) or not context_id:
            raise ValueError("a saved factory context has no context_id")
        raw_steps = data.get("planned_step_ids") or ()
        raw_attempts = data.get("recorded_attempts") or ()
        raw_evidence = data.get("evidence_memory_ids") or ()
        raw_paths = data.get("planned_paths") or ()
        recall = data.get("recall")
        return cls(
            context_id=context_id,
            project_id=str(data.get("project_id", "")),
            workspace_fingerprint=str(data.get("workspace_fingerprint", "")),
            task_id=str(data.get("task_id", "")),
            goal=str(data.get("goal", "")),
            build_declaration_id=str(data.get("build_declaration_id", "")),
            verification_declaration_id=str(
                data.get("verification_declaration_id", "")
            ),
            planned_step_ids=tuple(str(item) for item in raw_steps),
            recorded_attempts=tuple(
                dict(item) for item in raw_attempts if isinstance(item, Mapping)
            ),
            evidence_memory_ids=tuple(str(item) for item in raw_evidence),
            planned_paths=tuple(str(item) for item in raw_paths),
            recall=ContextRecall.from_dict(recall) if isinstance(recall, Mapping) else ContextRecall(),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass(frozen=True)
class ContextDrift:
    """How the workspace differs from the one the context was saved against."""

    changed: bool = False
    saved_fingerprint: str = ""
    current_fingerprint: str = ""
    fingerprint_changed: bool = False
    workspace_root_changed: bool = False
    changed_paths: tuple[str, ...] = ()
    added_paths: tuple[str, ...] = ()
    removed_paths: tuple[str, ...] = ()
    unrecallable_paths: tuple[str, ...] = ()

    @property
    def reason_codes(self) -> tuple[str, ...]:
        codes: list[str] = []
        if self.fingerprint_changed:
            codes.append("workspace_fingerprint_changed")
        if self.workspace_root_changed:
            codes.append("workspace_root_changed")
        if self.changed_paths:
            codes.append("paths_changed")
        if self.added_paths:
            codes.append("paths_added")
        if self.removed_paths:
            codes.append("paths_removed")
        return tuple(codes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": bool(self.changed),
            "saved_fingerprint": self.saved_fingerprint,
            "current_fingerprint": self.current_fingerprint,
            "fingerprint_changed": bool(self.fingerprint_changed),
            "workspace_root_changed": bool(self.workspace_root_changed),
            "changed_paths": list(self.changed_paths),
            "added_paths": list(self.added_paths),
            "removed_paths": list(self.removed_paths),
            "unrecallable_paths": list(self.unrecallable_paths),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class UnfinishedOperation:
    """One operation the evidence says has not reached a proven terminus."""

    operation_id: str
    kind: str
    classification: str = ""
    recorded_status: str = ""
    decision: str = ""
    reason: str = ""
    step_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "classification": self.classification,
            "recorded_status": self.recorded_status,
            "decision": self.decision,
            "reason": self.reason,
            "step_ids": list(self.step_ids),
        }


@dataclass(frozen=True)
class ContinuationStrategy:
    """A safe continuation plan. Data, and therefore not a permission.

    Every member answers a question about *understanding*. None of them is, or can
    be read as, an authorization: there is no approval, no scope, no allowlist, no
    token of authority, and no executable. ``permitted_actions`` is intentionally
    absent -- the permitted set is decided by the existing chain at dispatch time,
    not by this object.
    """

    context_id: str = ""
    project_id: str = ""
    context_found: bool = False
    drift: ContextDrift = field(default_factory=ContextDrift)
    unfinished: tuple[UnfinishedOperation, ...] = ()
    stale_evidence_memory_ids: tuple[str, ...] = ()
    invalidated_memory_ids: tuple[str, ...] = ()
    contradiction_memory_ids: tuple[str, ...] = ()
    recovery_plans: tuple[RecoveryPlan, ...] = ()
    continuable_step_ids: tuple[str, ...] = ()
    needs_owner: bool = False
    notes: tuple[str, ...] = ()
    generated_at: str = ""

    @property
    def requires_reapproval(self) -> bool:
        """True when any attempt's evidence demands authority be re-established.

        This is a statement about evidence, not a decision about permission: it
        says a fresh approval path is unavoidable, and it can never say one is
        unnecessary.
        """
        for plan in self.recovery_plans:
            for decision in plan.steps:
                if decision.decision in {
                    RecoveryDecision.NEEDS_OWNER,
                    RecoveryDecision.REFUSED,
                }:
                    return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "project_id": self.project_id,
            "context_found": bool(self.context_found),
            "drift": self.drift.to_dict(),
            "unfinished": [item.to_dict() for item in self.unfinished],
            "stale_evidence_memory_ids": list(self.stale_evidence_memory_ids),
            "invalidated_memory_ids": list(self.invalidated_memory_ids),
            "contradiction_memory_ids": list(self.contradiction_memory_ids),
            "continuable_step_ids": list(self.continuable_step_ids),
            "needs_owner": bool(self.needs_owner),
            "requires_reapproval": self.requires_reapproval,
            "notes": list(self.notes),
            "generated_at": self.generated_at,
            "recovery_plans": [plan.to_dict() for plan in self.recovery_plans],
        }


class ProjectFactoryContextStore:
    """Persist the factory context through the existing ``RunStore``.

    One more ``project_state`` key beside the step graph and the recovery claims.
    It merges, so it can never damage them.
    """

    def __init__(self, store: RunStore, *, context_run_id: str = "project-factory-context") -> None:
        if not isinstance(store, RunStore):
            raise TypeError("ProjectFactoryContextStore requires a RunStore")
        if not context_run_id or not context_run_id.strip():
            raise ValueError("context_run_id must be a non-empty run id")
        self._store = store
        self._context_run_id = context_run_id.strip()

    @property
    def context_run_id(self) -> str:
        return self._context_run_id

    def load(self) -> ProjectFactoryContext | None:
        snapshot = self._store.read_state(self._context_run_id)
        if snapshot is None or not snapshot.project_state:
            return None
        payload = snapshot.project_state.get(PROJECT_FACTORY_CONTEXT_KEY)
        if not isinstance(payload, Mapping):
            return None
        try:
            return ProjectFactoryContext.from_dict(payload)
        except ValueError:
            # A damaged document is reported as absent rather than guessed at.
            return None

    def save(self, context: ProjectFactoryContext) -> None:
        snapshot = self._store.read_state(self._context_run_id)
        project_state: dict[str, Any] = (
            dict(snapshot.project_state)
            if snapshot is not None and snapshot.project_state
            else {}
        )
        project_state[PROJECT_FACTORY_CONTEXT_KEY] = context.to_dict()
        self._store.write_state(
            RunStateSnapshot(
                run_id=self._context_run_id,
                task_id=snapshot.task_id if snapshot else "",
                state=snapshot.state if snapshot else "",
                status=snapshot.status if snapshot else "",
                attempt_number=snapshot.attempt_number if snapshot else 0,
                completed_attempt_indexes=(
                    snapshot.completed_attempt_indexes if snapshot else ()
                ),
                project_state=project_state,
                updated_at=_iso_now(),
            )
        )


# ---------------------------------------------------------------------------
# capture and recall
# ---------------------------------------------------------------------------


def _unrecallable_paths(snapshot: UnderstandingSnapshot) -> tuple[str, ...]:
    """Files the scan recorded but could not fingerprint, by path only.

    These are the secret files the scanner deliberately does not read and any file
    it could not read. Only the *names* are kept: recording a digest of credential
    material would be recording a fact about credential material, which memory has
    no business holding.
    """
    return tuple(
        sorted(
            entry.relative_path
            for entry in snapshot.files
            if entry.fingerprint is None
        )
    )


def capture_context(
    snapshot: UnderstandingSnapshot,
    *,
    workspace: Workspace,
    context_id: str = "project-factory-context",
    task_id: str = "",
    goal: str = "",
    build_declaration_id: str = "",
    verification_declaration_id: str = "",
    planned_step_ids: Sequence[str] = (),
    recorded_attempts: Sequence[Mapping[str, Any]] = (),
    evidence_memory_ids: Sequence[str] = (),
    existing: ProjectFactoryContext | None = None,
) -> ProjectFactoryContext:
    """Build a factory context from a real scan. Writes nothing and permits nothing."""
    now = _iso_now()
    return ProjectFactoryContext(
        context_id=context_id,
        project_id=snapshot.project_id,
        workspace_fingerprint=build_workspace_fingerprint(snapshot),
        task_id=task_id,
        goal=goal,
        build_declaration_id=build_declaration_id,
        verification_declaration_id=verification_declaration_id,
        planned_step_ids=tuple(str(item) for item in planned_step_ids),
        recorded_attempts=tuple(dict(item) for item in recorded_attempts),
        evidence_memory_ids=tuple(str(item) for item in evidence_memory_ids),
        planned_paths=tuple(
            sorted(entry.relative_path for entry in snapshot.files)
        ),
        recall=ContextRecall(
            workspace_root=str(workspace.root),
            unrecallable_paths=_unrecallable_paths(snapshot),
            note=(
                "files the bounded scanner could not fingerprint are named but "
                "their contents, digests and values are never recorded"
            ),
        ),
        created_at=existing.created_at if existing is not None else now,
        updated_at=now,
    )


def recall_context(
    context: ProjectFactoryContext,
    *,
    snapshot: UnderstandingSnapshot,
    workspace: Workspace,
    memory: ProjectMemory | None = None,
    freshness: FreshnessIndex | None = None,
) -> ContinuationStrategy:
    """Compare a saved context with the project as it is now.

    Read-only. Produces a strategy: drift, stale evidence, unfinished operations.
    It performs no scan of its own beyond the snapshot it is given, and it never
    dispatches anything.
    """
    current_fingerprint = build_workspace_fingerprint(snapshot)
    index = freshness if freshness is not None else FreshnessIndex.build(snapshot.files)

    saved_paths = {str(item) for item in context.recall.unrecallable_paths}
    current_paths = {entry.relative_path for entry in snapshot.files}
    unrecallable = _unrecallable_paths(snapshot)

    drift = ContextDrift(
        changed=current_fingerprint != context.workspace_fingerprint,
        saved_fingerprint=context.workspace_fingerprint,
        current_fingerprint=current_fingerprint,
        fingerprint_changed=current_fingerprint != context.workspace_fingerprint,
        workspace_root_changed=(
            bool(context.recall.workspace_root)
            and str(workspace.root) != context.recall.workspace_root
        ),
        added_paths=tuple(sorted(current_paths - set(context.planned_paths))),
        removed_paths=tuple(sorted(set(context.planned_paths) - current_paths)),
        unrecallable_paths=tuple(sorted({*saved_paths, *unrecallable})),
    )

    stale_ids: list[str] = []
    invalidated_ids: list[str] = []
    contradiction_ids: list[str] = []
    notes: list[str] = []
    if memory is not None:
        for record in memory.records:
            if record.status is MemoryStatus.STALE:
                stale_ids.append(record.memory_id)
            elif record.status is MemoryStatus.INVALIDATED:
                invalidated_ids.append(record.memory_id)
            elif record.kind.value == "CONTRADICTION":
                contradiction_ids.append(record.memory_id)
        for memory_id in context.evidence_memory_ids:
            record = memory.by_id(memory_id)
            if record is None:
                notes.append(f"saved evidence {memory_id!r} is no longer in memory")
                continue
            if record.status is MemoryStatus.CURRENT:
                verdict = freshness_of(record, index)
                if not verdict.is_fresh:
                    stale_ids.append(memory_id)
                    notes.append(
                        f"saved evidence {memory_id!r} is {verdict.outcome.value}: "
                        f"{verdict.reason or 'unpinned'}"
                    )
            else:
                if record.memory_id not in stale_ids and record.memory_id not in invalidated_ids:
                    stale_ids.append(memory_id)

    return ContinuationStrategy(
        context_id=context.context_id,
        project_id=context.project_id,
        context_found=True,
        drift=drift,
        stale_evidence_memory_ids=tuple(sorted(set(stale_ids))),
        invalidated_memory_ids=tuple(sorted(set(invalidated_ids))),
        contradiction_memory_ids=tuple(sorted(set(contradiction_ids))),
        notes=tuple(notes),
        generated_at=_iso_now(),
    )


def plan_continuation(
    *,
    planner: RecoveryPlanner,
    context: ProjectFactoryContext | None,
    snapshot: UnderstandingSnapshot,
    workspace: Workspace,
    memory: ProjectMemory | None = None,
) -> ContinuationStrategy:
    """The safe continuation strategy for a Project Factory task.

    Combines three independent evidence sources -- saved context, current project
    state, and unfinished recorded operations -- into one plan. The plan carries no
    authority; every dispatch it eventually causes is re-authorized by the existing
    chain exactly as a first attempt is.
    """
    if context is None:
        strategy = ContinuationStrategy(
            context_found=False,
            notes=(
                "no saved Project Factory context exists for this project, so there "
                "is nothing to compare against; a continuation starts from the "
                "current project state and the existing authorization path",
            ),
            generated_at=_iso_now(),
        )
    else:
        strategy = recall_context(
            context, snapshot=snapshot, workspace=workspace, memory=memory
        )

    unfinished: list[UnfinishedOperation] = []
    recovery_plans: list[RecoveryPlan] = []
    continuable: list[str] = []
    for finding in planner.unfinished_runs():
        plan = planner.plan(finding.graph_run_id)
        recovery_plans.append(plan)
        continuable.extend(
            plan.by_decision(RecoveryDecision.CONTINUE)
            + plan.by_decision(RecoveryDecision.REVERIFY)
        )
        unfinished.append(
            UnfinishedOperation(
                operation_id=finding.graph_run_id,
                kind="step_graph_attempt",
                classification=(
                    finding.classification.value
                    if hasattr(finding.classification, "value")
                    else str(finding.classification)
                ),
                recorded_status=(
                    finding.graph_status.value
                    if finding.graph_status is not None
                    else ""
                ),
                reason=finding.reason,
                step_ids=tuple(
                    decision.step_id
                    for decision in plan.steps
                    if decision.decision
                    in {RecoveryDecision.CONTINUE, RecoveryDecision.REVERIFY}
                ),
            )
        )

    for attempt in (context.recorded_attempts if context is not None else ()):
        run_id = str(attempt.get("run_id", ""))
        if not run_id:
            continue
        classification = planner.store.classify_record(run_id)
        if classification.is_terminal and classification.is_success:
            continue
        unfinished.append(
            UnfinishedOperation(
                operation_id=run_id,
                kind="project_factory_workflow",
                classification=classification.outcome.value,
                recorded_status=str(attempt.get("status", "")),
                reason=classification.reason,
            )
        )

    needs_owner = any(
        plan.needs_owner for plan in recovery_plans
    ) or bool(strategy.stale_evidence_memory_ids)
    return ContinuationStrategy(
        context_id=strategy.context_id,
        project_id=strategy.project_id,
        context_found=strategy.context_found,
        drift=strategy.drift,
        unfinished=tuple(unfinished),
        stale_evidence_memory_ids=strategy.stale_evidence_memory_ids,
        invalidated_memory_ids=strategy.invalidated_memory_ids,
        contradiction_memory_ids=strategy.contradiction_memory_ids,
        recovery_plans=tuple(recovery_plans),
        continuable_step_ids=tuple(sorted(set(continuable))),
        needs_owner=needs_owner,
        notes=strategy.notes,
        generated_at=_iso_now(),
    )
