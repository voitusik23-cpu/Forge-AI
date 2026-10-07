"""Provider-neutral orchestration facade for a complete Engineering Run."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum

from app.context import (
    ContextItem,
    DecisionContextAssembler,
    DecisionContextEnvelope,
)
from app.orchestrator.models import Event, EventType, Run, RunState, Task
from app.runtime.run_scope import RunScope, RunScopeError, require_active_scope
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
from uuid import uuid4

from app.decision import (
    Decision,
    DecisionAction,
    DecisionProvider,
    DecisionRequest,
    DecisionType,
    DeterministicDecisionProvider,
    validate_decision,
)
from app.projects.state import ProjectState, derive_project_state
from app.orchestrator.trace import RunEvent, RunTrace, build_trace_from_run
from app.understanding.models import UnderstandingSnapshot
from app.understanding.snapshotter import UnderstandingSnapshotter


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
    decision_provider: DecisionProvider | None = None
    context_assembler: DecisionContextAssembler | None = None
    # Immutable security perimeter for this run.
    run_scope: object | None = None
    understanding_snapshot: UnderstandingSnapshot | None = None

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
    project_states: tuple[ProjectState, ...] = ()
    trace: RunTrace | None = None
    decisions: tuple[Decision, ...] = ()
    context_envelopes: tuple[DecisionContextEnvelope, ...] = ()

    @property
    def final_project_state(self) -> ProjectState | None:
        return self.project_states[-1] if self.project_states else None

    @property
    def final_decision(self) -> Decision | None:
        return self.decisions[-1] if self.decisions else None

    @property
    def final_context_envelope(self) -> DecisionContextEnvelope | None:
        return self.context_envelopes[-1] if self.context_envelopes else None

    @property
    def events(self) -> tuple[RunEvent, ...]:
        return self.trace.events if self.trace is not None else ()

    @property
    def detailed_acceptance_report(self) -> DetailedAcceptanceReport | None:
        return self.final_acceptance.report if self.final_acceptance else None


class EngineeringRunExecutor:
    """Coordinate existing revision, workspace, snapshot, and acceptance contracts."""

    def __init__(
        self,
        revision_executor: RevisionLoopExecutor,
        execution_coordinator: ExecutionCoordinator | None = None,
        decision_provider: DecisionProvider | None = None,
        context_assembler: DecisionContextAssembler | None = None,
    ) -> None:
        self._revision_executor = revision_executor
        self._execution_coordinator = execution_coordinator
        self._decision_provider = decision_provider or DeterministicDecisionProvider()
        self._context_assembler = context_assembler or DecisionContextAssembler()

    def execute(self, request: EngineeringRunRequest) -> EngineeringRunResult:
        # DECISION 1: the run identity is established before the scope is used and
        # is never taken from the scope. DECISION 2: the scope is frozen once and
        # only narrowed downstream.
        scope = getattr(request, "run_scope", None)
        if scope is None:
            # NO DISPATCH AUTHORITY -> NO RUNSCOPE REQUIRED. A run that declares
            # no dispatch authority cannot reach the command or tool paths, so it
            # needs no perimeter. No scope is created, substituted, or inferred.
            #
            # Note: EngineeringRunRequest carries no run identity, so there is no
            # key here for the "frozen scope exists but was not provided" check
            # that the Agent Harness entry point performs; that check applies
            # wherever a run id is available.
            declares_dispatch_authority = bool(
                getattr(request, "execution_requests", ())
                or getattr(request, "allowed_execution_commands", None)
                or getattr(request, "allowed_tool_ids", ())
            )
            if declares_dispatch_authority:
                raise RunScopeError(
                    "engineering run declares dispatch authority and requires an "
                    "explicit RunScope before it may dispatch"
                )
            scope_run_id = ""
        else:
            # A supplied scope is still validated exactly as before: type-checked,
            # frozen, and bound to the run identity it carries.
            if not isinstance(scope, RunScope):
                raise RunScopeError("run_scope must be a RunScope")
            scope_run_id = scope.run_id
            require_active_scope(scope_run_id, scope)

        task, criteria, requirements, task_id, execution_profile = self._resolve_input(request)

        # Resolve Project Understanding Snapshot (informational context)
        effective_understanding_snapshot: UnderstandingSnapshot | None = None
        if request.understanding_snapshot is not None:
            effective_understanding_snapshot = request.understanding_snapshot
        elif request.workspace is not None:
            snapshotter = UnderstandingSnapshotter()
            effective_understanding_snapshot = snapshotter.create_snapshot(
                request.workspace,
                project_id=task_id or scope_run_id or "default_project",
            )

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
            run_id=scope_run_id,
            run_scope=scope,
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
                    workspace_root=request.workspace.root if request.workspace else None,
                    run_id=run.id,
                    allowed_commands=allowed_cmds,
                    approval_policy=request.approval_policy,
                    approval_resolver=request.approval_resolver,
                    run_scope=scope,
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
        if effective_understanding_snapshot is not None:
            start_data["workspace_fingerprint"] = effective_understanding_snapshot.workspace_fingerprint
        start_ts = run.events[0].timestamp if run.events else datetime.now(timezone.utc)
        run.events.insert(0, Event(
            run_id=run.id,
            type=EventType.ENGINEERING_RUN_STARTED,
            data=start_data,
            timestamp=start_ts,
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
        project_states: list[ProjectState] = []
        if attempts:
            for idx, attempt in enumerate(attempts):
                attempt_exec_results = (
                    execution_results if idx == len(attempts) - 1 else ()
                )
                st = derive_project_state(
                    run_id=run.id,
                    attempt_number=attempt.attempt_number,
                    task_id=task_id,
                    snapshots=attempt.snapshots,
                    changesets=attempt.changesets,
                    execution_results=attempt_exec_results,
                    verification_results=attempt.verification_results,
                    acceptance_result=attempt.acceptance_result,
                )
                project_states.append(st)
        else:
            st = derive_project_state(
                run_id=run.id,
                attempt_number=0,
                task_id=task_id,
                snapshots=run.project_snapshots,
                changesets=run.change_sets,
                execution_results=execution_results,
                verification_results=(),
                acceptance_result=final_acceptance,
            )
            project_states.append(st)

        for st in project_states:
            run.events.append(
                Event(
                    run_id=run.id,
                    type=EventType.PROJECT_STATE_UPDATED,
                    data={
                        "run_id": run.id,
                        "attempt_number": st.attempt_number,
                        "status": st.status.value,
                        "project_state_status": st.status.value,
                        "snapshot_id": st.snapshot_id,
                        "changeset_id": st.changeset_id,
                        "acceptance_status": st.acceptance_status,
                    },
                )
            )

        provider = request.decision_provider or self._decision_provider
        context_assembler = request.context_assembler or self._context_assembler
        decisions: list[Decision] = []
        context_envelopes: list[DecisionContextEnvelope] = []
        for idx, st in enumerate(project_states):
            conditions: list[str] = []
            if idx == len(project_states) - 1:
                if final_status == EngineeringRunStatus.LIMIT_REACHED:
                    conditions.append("revision_limit_reached")
                if run.state == RunState.WAITING_FOR_APPROVAL:
                    conditions.append("approval_pending")
                for res in execution_results:
                    if res.outcome_status == ExecutionOutcomeStatus.PERMISSION_DENIED:
                        conditions.append("permission_denied")
                    elif res.outcome_status in (
                        ExecutionOutcomeStatus.POLICY_DENIED,
                        ExecutionOutcomeStatus.APPROVAL_REJECTED,
                    ):
                        conditions.append("policy_denied")
                    elif res.outcome_status == ExecutionOutcomeStatus.APPROVAL_WAITING:
                        conditions.append("approval_pending")

            attempt_obj = attempts[idx] if idx < len(attempts) else None
            attempt_verifs = attempt_obj.verification_results if attempt_obj else ()
            attempt_acc = attempt_obj.acceptance_result if attempt_obj else final_acceptance

            # Assemble Decision Context Envelope
            envelope = context_assembler.assemble(
                run_id=run.id,
                attempt_number=st.attempt_number,
                task_id=task_id,
                task_specification=request.task_specification,
                project_state=st,
                verification_results=attempt_verifs,
                acceptance_result=attempt_acc,
                revision_result=revision_result if idx == len(project_states) - 1 else None,
                blocking_conditions=tuple(conditions),
                requirements=requirements or (),
                acceptance_criteria=criteria or (),
                understanding_snapshot=effective_understanding_snapshot,
            )
            context_envelopes.append(envelope)

            run.events.append(
                Event(
                    run_id=run.id,
                    type=EventType.CONTEXT_DECISION_READY,
                    data={
                        "context_id": envelope.context_id,
                        "context_fingerprint": envelope.context_fingerprint,
                        "run_id": run.id,
                        "attempt_number": envelope.attempt_number,
                        "item_count": envelope.item_count,
                    },
                )
            )

            dec_req = DecisionRequest(
                decision_id=str(uuid4()),
                run_id=run.id,
                attempt_number=st.attempt_number,
                current_project_state=st,
                blocking_conditions=tuple(conditions),
                context_id=envelope.context_id,
                context_fingerprint=envelope.context_fingerprint,
                context_envelope=envelope,
            )
            run.events.append(
                Event(
                    run_id=run.id,
                    type=EventType.DECISION_REQUESTED,
                    data={
                        "decision_id": dec_req.decision_id,
                        "run_id": run.id,
                        "attempt_number": dec_req.attempt_number,
                        "project_state_status": st.status.value,
                        "context_id": dec_req.context_id,
                        "context_fingerprint": dec_req.context_fingerprint,
                    },
                )
            )
            decision = provider.decide(dec_req)
            val = validate_decision(decision, dec_req, st)
            if val.valid:
                run.events.append(
                    Event(
                        run_id=run.id,
                        type=EventType.DECISION_MADE,
                        data={
                            "decision_id": decision.decision_id,
                            "run_id": run.id,
                            "attempt_number": decision.attempt_number,
                            "decision_type": decision.decision_type.value,
                            "action": decision.action.value,
                            "rationale": decision.rationale,
                            "metadata": dict(decision.metadata),
                            "context_id": decision.references.get("context_id") if decision.references else None,
                            "context_fingerprint": decision.references.get("context_fingerprint") if decision.references else None,
                        },
                    )
                )
                decisions.append(decision)
            else:
                run.events.append(
                    Event(
                        run_id=run.id,
                        type=EventType.DECISION_REJECTED,
                        data={
                            "decision_id": decision.decision_id,
                            "run_id": run.id,
                            "attempt_number": decision.attempt_number,
                            "errors": list(val.errors),
                        },
                    )
                )

        run.events.append(
            Event(
                run_id=run.id,
                type=EventType.RUN_COMPLETED,
                data={
                    "run_id": run.id,
                    "status": final_status.value,
                    "task_id": task_id,
                },
            )
        )

        trace = build_trace_from_run(run, project_states=tuple(project_states))

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
            project_states=tuple(project_states),
            trace=trace,
            decisions=tuple(decisions),
            context_envelopes=tuple(context_envelopes),
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
