"""Provider-neutral orchestration facade for a complete Engineering Run."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum

from app.context import ContextItem
from app.orchestrator.models import Event, EventType, Run, RunState, Task
from app.orchestrator.revision import (
    RevisionAttemptResult,
    RevisionLoopExecutor,
    RevisionResult,
    RevisionStatus,
)
from app.tools.acceptance import (
    AcceptanceCriterion,
    AcceptanceResult,
    AcceptanceStatus,
    DetailedAcceptanceReport,
)
from app.tools.approval import ApprovalPolicy, ApprovalResolver
from app.tools.changesets import ChangeSet
from app.tools.verification import VerificationExpectation, VerificationResult
from app.tools.workspace import Workspace
from app.snapshots import ProjectSnapshot
from app.tasks.specification import InvalidTaskSpecificationError, TaskSpecification
from app.execution.profile import ProjectExecutionProfile
from app.execution.authorizer import ExecutionCoordinator
from app.execution.adapter import LocalExecutionAdapter
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
)


class EngineeringRunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass(frozen=True)
class EngineeringRunRequest:
    # The original positional field order remains available for legacy callers.
    task: Task | None = None
    workspace: Workspace | None = None
    snapshot_paths: tuple[str, ...] = ()
    verification_expectations: Mapping[str, VerificationExpectation] = field(default_factory=dict)
    acceptance_criteria: tuple[AcceptanceCriterion, ...] | None = None
    max_revision_attempts: int = 1
    provider_name: str | None = None
    agent_name: str | None = None
    allowed_tool_ids: tuple[str, ...] = ()
    explicit_inputs: tuple[str | ContextItem, ...] = ()
    task_specification: TaskSpecification | None = None
    execution_profile: ProjectExecutionProfile | None = None
    execution_requests: tuple[ExecutionRequest, ...] = ()
    allowed_execution_commands: tuple[str, ...] | None = None
    approval_policy: ApprovalPolicy | None = None
    approval_resolver: ApprovalResolver | None = None

    def __post_init__(self) -> None:
        if isinstance(self.execution_requests, list):
            object.__setattr__(self, "execution_requests", tuple(self.execution_requests))
        if isinstance(self.allowed_execution_commands, list):
            object.__setattr__(
                self, "allowed_execution_commands", tuple(self.allowed_execution_commands)
            )


@dataclass(frozen=True)
class EngineeringRunResult:
    run_id: str
    task_id: str
    final_status: EngineeringRunStatus
    final_acceptance: AcceptanceResult | None
    attempts: tuple[RevisionAttemptResult, ...]
    changesets: tuple[ChangeSet, ...]
    snapshots: tuple[ProjectSnapshot, ...]
    verification_results: tuple[VerificationResult, ...]
    acceptance_results: tuple[AcceptanceResult, ...]
    revision_result: RevisionResult
    run: Run = field(compare=False, repr=False)
    execution_profile: ProjectExecutionProfile | None = None
    execution_results: tuple[ExecutionResult, ...] = ()

    @property
    def detailed_acceptance_report(self) -> DetailedAcceptanceReport | None:
        return self.final_acceptance.report if self.final_acceptance else None


class EngineeringRunExecutor:
    """Coordinate existing revision, workspace, snapshot, and acceptance contracts."""

    def __init__(
        self,
        revision_executor: RevisionLoopExecutor,
        execution_coordinator: ExecutionCoordinator | None = None,
    ) -> None:
        self._revision_executor = revision_executor
        self._execution_coordinator = execution_coordinator

    def execute(self, request: EngineeringRunRequest) -> EngineeringRunResult:
        task, criteria, requirements, task_id, execution_profile = self._resolve_input(request)
        revision_result = self._revision_executor.execute(
            task,
            provider_name=request.provider_name,
            agent_name=request.agent_name,
            allowed_tool_ids=request.allowed_tool_ids,
            explicit_inputs=request.explicit_inputs,
            workspace=request.workspace,
            snapshot_paths=request.snapshot_paths,
            verification_expectations=request.verification_expectations,
            criteria=criteria,
            requirements=requirements,
            max_revision_attempts=request.max_revision_attempts,
        )
        run = revision_result.run

        execution_results: list[ExecutionResult] = []
        if request.execution_requests:
            coordinator = self._execution_coordinator or ExecutionCoordinator(
                LocalExecutionAdapter(
                    workspace_root=request.workspace.root if request.workspace else None
                )
            )
            allowed_cmds = (
                frozenset(request.allowed_execution_commands)
                if request.allowed_execution_commands is not None
                else None
            )
            for exec_req in request.execution_requests:
                effective_req = (
                    replace(exec_req, profile=execution_profile)
                    if exec_req.profile is None and execution_profile is not None
                    else exec_req
                )
                res = coordinator.execute(
                    effective_req,
                    run_id=run.id,
                    allowed_commands=allowed_cmds,
                    approval_policy=request.approval_policy,
                    approval_resolver=request.approval_resolver,
                    observer=lambda event_type, data: run.events.append(
                        Event(run_id=run.id, type=event_type, data=data)
                    ),
                )
                execution_results.append(res)
                if res.outcome_status == ExecutionOutcomeStatus.APPROVAL_WAITING:
                    run.state = RunState.WAITING_FOR_APPROVAL

        final_status = self._final_status(run, revision_result)
        final_acceptance = next(
            (
                attempt.acceptance_result
                for attempt in reversed(revision_result.attempts)
                if attempt.acceptance_result is not None
            ),
            None,
        )
        start_data: dict[str, object] = {"run_id": run.id, "task_id": task_id, "status": "started"}
        if execution_profile is not None:
            start_data["profile_id"] = execution_profile.profile_id
        run.events.insert(0, Event(
            run_id=run.id,
            type=EventType.ENGINEERING_RUN_STARTED,
            data=start_data,
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
                "execution_count": len(execution_results),
                "reason": self._reason_code(final_status, final_acceptance, run),
            },
        ))

        attempts = revision_result.attempts
        return EngineeringRunResult(
            run_id=run.id,
            task_id=task_id,
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
            execution_profile=execution_profile,
            execution_results=tuple(execution_results),
        )

    @staticmethod
    def _resolve_input(request: EngineeringRunRequest):
        specification = request.task_specification
        if specification is not None:
            if request.task is not None or request.acceptance_criteria is not None:
                raise ValueError(
                    "task_specification cannot be combined with legacy task or acceptance_criteria"
                )
            if not isinstance(specification, TaskSpecification):
                raise ValueError("task_specification must be a TaskSpecification")
            if request.execution_profile is not None and specification.execution_profile is not None:
                if request.execution_profile != specification.execution_profile:
                    raise ValueError(
                        "Conflicting execution_profile specified in request and task_specification"
                    )
            execution_profile = specification.execution_profile or request.execution_profile
            if execution_profile is not None and specification.execution_profile is None:
                if not isinstance(execution_profile, ProjectExecutionProfile):
                    raise ValueError("execution_profile must be a ProjectExecutionProfile")
                profile_val = execution_profile.validate()
                if not profile_val.valid:
                    raise ValueError(f"Invalid execution_profile: {', '.join(profile_val.errors)}")
            validation = specification.validate()
            if not validation.valid:
                raise InvalidTaskSpecificationError(validation)
            task_context: dict[str, object] = {
                "task_specification": specification.to_context_data()
            }
            if execution_profile is not None and "execution_profile" not in task_context["task_specification"]:
                task_context["execution_profile"] = execution_profile.to_dict()
            task = Task(
                id=specification.task_id,
                description="Execute the supplied TaskSpecification.",
                context=task_context,
            )
            return (
                task,
                specification.acceptance_criteria,
                specification.requirements,
                specification.task_id,
                execution_profile,
            )

        if not isinstance(request.task, Task):
            raise ValueError("task or task_specification is required")
        if request.acceptance_criteria is None:
            raise ValueError("acceptance_criteria is required for legacy task input")

        execution_profile = request.execution_profile
        if execution_profile is not None:
            if not isinstance(execution_profile, ProjectExecutionProfile):
                raise ValueError("execution_profile must be a ProjectExecutionProfile")
            profile_val = execution_profile.validate()
            if not profile_val.valid:
                raise ValueError(f"Invalid execution_profile: {', '.join(profile_val.errors)}")
            if "execution_profile" not in request.task.context:
                request.task.context["execution_profile"] = execution_profile.to_dict()

        return request.task, request.acceptance_criteria, None, request.task.id, execution_profile

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
