"""Provider-neutral orchestration facade for a complete Engineering Run."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from app.context import ContextItem
from app.orchestrator.models import Event, EventType, Run, RunState, Task
from app.orchestrator.revision import (
    RevisionAttemptResult,
    RevisionLoopExecutor,
    RevisionResult,
    RevisionStatus,
)
from app.tools.acceptance import AcceptanceCriterion, AcceptanceResult, AcceptanceStatus
from app.tools.changesets import ChangeSet
from app.tools.verification import VerificationExpectation, VerificationResult
from app.tools.workspace import Workspace
from app.snapshots import ProjectSnapshot


class EngineeringRunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass(frozen=True)
class EngineeringRunRequest:
    task: Task
    workspace: Workspace
    snapshot_paths: tuple[str, ...]
    verification_expectations: Mapping[str, VerificationExpectation]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    max_revision_attempts: int = 1
    provider_name: str | None = None
    agent_name: str | None = None
    allowed_tool_ids: tuple[str, ...] = ()
    explicit_inputs: tuple[str | ContextItem, ...] = ()


@dataclass(frozen=True)
class EngineeringRunResult:
    run_id: str
    final_status: EngineeringRunStatus
    final_acceptance: AcceptanceResult | None
    attempts: tuple[RevisionAttemptResult, ...]
    changesets: tuple[ChangeSet, ...]
    snapshots: tuple[ProjectSnapshot, ...]
    verification_results: tuple[VerificationResult, ...]
    acceptance_results: tuple[AcceptanceResult, ...]
    revision_result: RevisionResult
    run: Run = field(compare=False, repr=False)


class EngineeringRunExecutor:
    """Coordinate existing revision, workspace, snapshot, and acceptance contracts."""

    def __init__(self, revision_executor: RevisionLoopExecutor) -> None:
        self._revision_executor = revision_executor

    def execute(self, request: EngineeringRunRequest) -> EngineeringRunResult:
        revision_result = self._revision_executor.execute(
            request.task,
            provider_name=request.provider_name,
            agent_name=request.agent_name,
            allowed_tool_ids=request.allowed_tool_ids,
            explicit_inputs=request.explicit_inputs,
            workspace=request.workspace,
            snapshot_paths=request.snapshot_paths,
            verification_expectations=request.verification_expectations,
            criteria=request.acceptance_criteria,
            max_revision_attempts=request.max_revision_attempts,
        )
        run = revision_result.run
        final_status = self._final_status(run, revision_result)
        final_acceptance = next(
            (
                attempt.acceptance_result
                for attempt in reversed(revision_result.attempts)
                if attempt.acceptance_result is not None
            ),
            None,
        )
        run.events.insert(0, Event(
            run_id=run.id,
            type=EventType.ENGINEERING_RUN_STARTED,
            data={"run_id": run.id, "status": "started"},
            timestamp=run.events[0].timestamp,
        ))
        run.events.append(Event(
            run_id=run.id,
            type=EventType.ENGINEERING_RUN_COMPLETED,
            data={
                "run_id": run.id,
                "status": final_status.value,
                "attempt_count": len(revision_result.attempts),
                "snapshot_count": len(run.project_snapshots),
                "changeset_count": len(run.change_sets),
                "verification_count": sum(
                    len(item.verification_results) for item in revision_result.attempts
                ),
                "acceptance_count": sum(
                    item.acceptance_result is not None
                    for item in revision_result.attempts
                ),
                "reason": self._reason_code(final_status, final_acceptance, run),
            },
        ))

        attempts = revision_result.attempts
        return EngineeringRunResult(
            run_id=run.id,
            final_status=final_status,
            final_acceptance=final_acceptance,
            attempts=attempts,
            changesets=tuple(item for attempt in attempts for item in attempt.changesets),
            snapshots=tuple(item for attempt in attempts for item in attempt.snapshots),
            verification_results=tuple(
                item for attempt in attempts for item in attempt.verification_results
            ),
            acceptance_results=tuple(
                attempt.acceptance_result
                for attempt in attempts
                if attempt.acceptance_result is not None
            ),
            revision_result=revision_result,
            run=run,
        )

    @staticmethod
    def _final_status(run: Run, result: RevisionResult) -> EngineeringRunStatus:
        if run.state == RunState.WAITING_FOR_APPROVAL:
            return EngineeringRunStatus.WAITING_FOR_APPROVAL
        if result.status == RevisionStatus.LIMIT_REACHED:
            return EngineeringRunStatus.LIMIT_REACHED
        if (
            result.status == RevisionStatus.COMPLETED
            and result.acceptance_result is not None
            and result.acceptance_result.status == AcceptanceStatus.PASS
        ):
            return EngineeringRunStatus.SUCCESS
        return EngineeringRunStatus.FAILED

    @staticmethod
    def _reason_code(status, acceptance, run) -> str:
        if status == EngineeringRunStatus.SUCCESS:
            return "acceptance_passed"
        if status == EngineeringRunStatus.WAITING_FOR_APPROVAL:
            return "approval_required"
        if status == EngineeringRunStatus.LIMIT_REACHED:
            return "revision_limit_reached"
        if acceptance is not None:
            return acceptance.code
        if run.error is not None:
            return run.error.error_type.lower()
        return "execution_failed"
