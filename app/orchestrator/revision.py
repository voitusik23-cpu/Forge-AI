"""Bounded, acceptance-triggered revision through the existing RunExecutor."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum

from app.orchestrator.models import Event, EventType, Run, RunState, Task
from app.orchestrator.run import RunExecutor
from app.artifacts import ChangeSet
from app.tools.acceptance import (
    AcceptanceCriterion,
    AcceptanceGate,
    AcceptanceResult,
    AcceptanceStatus,
    DetailedAcceptanceReport,
    RequirementEvaluation,
    RequirementStatus,
)
from app.tools.contracts import ToolStatus
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
    WorkspaceVerifier,
)
from app.tools.workspace import Workspace
from app.tools.project_snapshots import ProjectSnapshotter
from app.snapshots import ProjectSnapshot


class RevisionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass(frozen=True)
class FailedCriterion:
    criterion_id: str
    code: str
    requirement_id: str = ""
    verification_code: str = ""


@dataclass(frozen=True)
class FailedRequirement:
    requirement_id: str
    status: str
    failed_criteria: tuple[FailedCriterion, ...]
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RevisionRequest:
    run_id: str
    revision_id: str
    reason: str
    failed_criteria: tuple[FailedCriterion, ...]
    attempt_number: int
    failed_requirements: tuple[FailedRequirement, ...] = ()
    detailed_report: DetailedAcceptanceReport | None = None


@dataclass(frozen=True)
class RevisionAttemptResult:
    attempt_number: int
    snapshots: tuple[ProjectSnapshot, ...]
    changesets: tuple[ChangeSet, ...]
    verification_results: tuple[VerificationResult, ...]
    acceptance_result: AcceptanceResult | None


@dataclass(frozen=True)
class RevisionResult:
    status: RevisionStatus
    attempt_number: int
    acceptance_result: AcceptanceResult | None
    run: Run = field(compare=False, repr=False)
    attempts: tuple[RevisionAttemptResult, ...] = ()


class RevisionLoopExecutor:
    """Run at most the configured revisions, preserving all existing boundaries."""

    def __init__(
        self,
        run_executor: RunExecutor,
        *,
        max_revision_attempts: int = 1,
        verifier: WorkspaceVerifier | None = None,
        acceptance_gate: AcceptanceGate | None = None,
        snapshotter: ProjectSnapshotter | None = None,
    ) -> None:
        if (
            isinstance(max_revision_attempts, bool)
            or not isinstance(max_revision_attempts, int)
            or max_revision_attempts < 0
        ):
            raise ValueError("max_revision_attempts must be a non-negative integer")
        self._run_executor = run_executor
        self.max_revision_attempts = max_revision_attempts
        self._verifier = verifier or WorkspaceVerifier()
        self._acceptance_gate = acceptance_gate or AcceptanceGate()
        self._snapshotter = snapshotter or ProjectSnapshotter()

    def execute(
        self,
        task: Task,
        *,
        criteria: Iterable[AcceptanceCriterion],
        verification_expectations: Mapping[str, VerificationExpectation],
        requirements: Iterable[Any] | None = None,
        provider_name: str | None = None,
        agent_name: str | None = None,
        explicit_inputs=(),
        allowed_tool_ids=(),
        workspace: Workspace | None = None,
        snapshot_paths: Iterable[str] | None = None,
        max_revision_attempts: int | None = None,
        run_id: str = "",
        run_scope: object | None = None,
    ) -> RevisionResult:
        """Execute once, then revise only after a safe, observed Acceptance FAIL."""
        criteria = tuple(criteria)
        expectations = dict(verification_expectations)
        explicit_inputs = tuple(explicit_inputs)
        allowed_tool_ids = tuple(allowed_tool_ids)
        requested_snapshot_paths = (
            None if snapshot_paths is None else tuple(snapshot_paths)
        )
        max_attempts = self.max_revision_attempts if max_revision_attempts is None else max_revision_attempts
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 0:
            raise ValueError("max_revision_attempts must be a non-negative integer")
        run = Run(task=task, **({"id": run_id} if run_id else {}))
        initial_before = None
        if requested_snapshot_paths is not None:
            initial_before = self._snapshotter.create(
                requested_snapshot_paths,
                run=run,
                attempt_number=0,
                workspace=workspace,
            )
        run = self._run_executor.execute(
            task,
            provider_name=provider_name,
            agent_name=agent_name,
            explicit_inputs=explicit_inputs,
            allowed_tool_ids=allowed_tool_ids,
            workspace=workspace,
            run_scope=run_scope,
            _run=run,
            _attempt_number=0,
        )
        initial_after = None
        if requested_snapshot_paths is not None:
            initial_after = self._snapshotter.create(
                requested_snapshot_paths,
                run=run,
                attempt_number=0,
                workspace=workspace,
            )
        acceptance, can_revise, verification_results = self._verify_and_accept(
            run, criteria, expectations, workspace, requirements=requirements, attempt_number=0
        )
        attempts = [self._attempt_result(
            run, 0, (initial_before, initial_after), verification_results, acceptance
        )]
        attempt = 0
        if acceptance is None or not can_revise:
            return RevisionResult(RevisionStatus.FAILED, attempt, acceptance, run, tuple(attempts))
        if acceptance.status == AcceptanceStatus.PASS:
            return RevisionResult(RevisionStatus.COMPLETED, attempt, acceptance, run, tuple(attempts))
        if max_attempts == 0:
            return RevisionResult(RevisionStatus.LIMIT_REACHED, attempt, acceptance, run, tuple(attempts))

        failed = self._failed_criteria(acceptance)
        for attempt in range(1, max_attempts + 1):
            request = RevisionRequest(
                run_id=run.id,
                revision_id=f"{run.id}:revision:{attempt}",
                reason=acceptance.code,
                failed_criteria=failed,
                attempt_number=attempt,
                failed_requirements=self._failed_requirements(acceptance),
                detailed_report=acceptance.report,
            )
            self._emit(run, EventType.REVISION_STARTED, {
                "run_id": run.id,
                "revision_id": request.revision_id,
                "attempt_number": attempt,
                "reason": request.reason,
            })
            run.state = RunState.REVISING
            revision_before = None
            if requested_snapshot_paths is not None:
                revision_before = self._snapshotter.create(
                    requested_snapshot_paths,
                    run=run,
                    attempt_number=attempt,
                    workspace=workspace,
                )
            revision_task = self._revision_task(task, request)
            self._run_executor.execute(
                revision_task,
                provider_name=provider_name,
                agent_name=agent_name,
                explicit_inputs=explicit_inputs,
                allowed_tool_ids=allowed_tool_ids,
                workspace=workspace,
                run_scope=run_scope,
                _run=run,
                _attempt_number=attempt,
            )
            revision_after = None
            if requested_snapshot_paths is not None:
                revision_after = self._snapshotter.create(
                    requested_snapshot_paths,
                    run=run,
                    attempt_number=attempt,
                    workspace=workspace,
                )
            next_acceptance, can_revise, verification_results = self._verify_and_accept(
                run, criteria, expectations, workspace, requirements=requirements, attempt_number=attempt
            )
            attempts.append(self._attempt_result(
                run, attempt, (revision_before, revision_after),
                verification_results, next_acceptance,
            ))
            if next_acceptance is None or not can_revise:
                self._emit_revision_completed(run, request, RevisionStatus.FAILED, next_acceptance)
                return RevisionResult(RevisionStatus.FAILED, attempt, next_acceptance, run, tuple(attempts))
            acceptance = next_acceptance
            if acceptance.status == AcceptanceStatus.PASS:
                self._emit_revision_completed(run, request, RevisionStatus.COMPLETED, acceptance)
                return RevisionResult(RevisionStatus.COMPLETED, attempt, acceptance, run, tuple(attempts))
            failed = self._failed_criteria(acceptance)
            if attempt == max_attempts:
                self._emit_revision_completed(run, request, RevisionStatus.LIMIT_REACHED, acceptance)
                return RevisionResult(RevisionStatus.LIMIT_REACHED, attempt, acceptance, run, tuple(attempts))

        return RevisionResult(RevisionStatus.LIMIT_REACHED, attempt, acceptance, run, tuple(attempts))

    def _verify_and_accept(
        self, run, criteria, expectations, workspace, *, requirements=None, attempt_number
    ):
        # Hard execution/policy failures and unresolved approvals stop before acceptance.
        if run.state != RunState.COMPLETED or run.result is None:
            return None, False, ()
        tool_results = run.result.tool_results
        if any(result.status != ToolStatus.COMPLETED for result in tool_results):
            return None, False, ()

        verification_results: dict[str, VerificationResult] = {}
        denied = False
        for criterion in criteria:
            expectation = expectations.get(criterion.criterion_id)
            if expectation is None:
                continue
            verification = self._verifier.verify(
                expectation,
                workspace=workspace,
                run_id=run.id,
                observer=lambda event_type, data: self._emit(run, event_type, data),
            )
            verification_results[criterion.criterion_id] = verification
            denied = denied or verification.status == VerificationStatus.DENIED
        acceptance = self._acceptance_gate.evaluate(
            criteria,
            verification_results,
            requirements=requirements,
            run_id=run.id,
            observer=lambda event_type, data: self._emit(run, event_type, data),
        )
        verification_status = (
            "denied" if denied else
            "unavailable" if not verification_results else
            "fail" if any(item.status != VerificationStatus.PASS for item in verification_results.values()) else
            "pass"
        )
        self._run_executor.attach_attempt_outcomes(
            run,
            attempt_number,
            verification_status=verification_status,
            acceptance_status=acceptance.status.value,
        )
        return acceptance, not denied, tuple(verification_results.values())

    @staticmethod
    def _attempt_result(run, attempt_number, snapshots, verification_results, acceptance):
        return RevisionAttemptResult(
            attempt_number=attempt_number,
            snapshots=tuple(item for item in snapshots if item is not None),
            changesets=tuple(
                item for item in run.change_sets
                if item.attempt_number == attempt_number
            ),
            verification_results=verification_results,
            acceptance_result=acceptance,
        )

    @staticmethod
    def _failed_criteria(result: AcceptanceResult) -> tuple[FailedCriterion, ...]:
        return tuple(
            FailedCriterion(
                criterion_id=item.criterion_id,
                code=item.code,
                requirement_id=item.requirement_id,
                verification_code=(
                    item.verification_result.code
                    if item.verification_result is not None
                    else ""
                ),
            )
            for item in result.results
            if item.status == AcceptanceStatus.FAIL
        )

    @staticmethod
    def _failed_requirements(result: AcceptanceResult) -> tuple[FailedRequirement, ...]:
        if result.report is None:
            return ()
        failed_reqs = []
        for req_eval in result.report.requirement_evaluations:
            if req_eval.status == RequirementStatus.FAIL:
                failed_c = tuple(
                    FailedCriterion(
                        criterion_id=c.criterion_id,
                        code=c.code,
                        requirement_id=c.requirement_id,
                        verification_code=(
                            c.verification_result.code
                            if c.verification_result is not None
                            else ""
                        ),
                    )
                    for c in req_eval.criterion_results
                    if c.status == AcceptanceStatus.FAIL
                )
                failed_reqs.append(
                    FailedRequirement(
                        requirement_id=req_eval.requirement_id,
                        status=req_eval.status.value,
                        failed_criteria=failed_c,
                        reasons=req_eval.failure_reasons,
                    )
                )
        return tuple(failed_reqs)

    @staticmethod
    def _revision_task(task: Task, request: RevisionRequest) -> Task:
        context = dict(task.context)
        failed_criteria_payload = []
        for item in request.failed_criteria:
            entry: dict[str, object] = {"criterion_id": item.criterion_id, "code": item.code}
            if item.requirement_id:
                entry["requirement_id"] = item.requirement_id
                entry["verification_code"] = item.verification_code
            failed_criteria_payload.append(entry)

        context["forge_revision"] = {
            "revision_id": request.revision_id,
            "reason": request.reason,
            "attempt_number": request.attempt_number,
            "failed_criteria": failed_criteria_payload,
            "failed_requirements": [
                {
                    "requirement_id": item.requirement_id,
                    "status": item.status,
                    "reasons": list(item.reasons),
                    "failed_criteria": [
                        {
                            "criterion_id": c.criterion_id,
                            "code": c.code,
                            "requirement_id": c.requirement_id,
                            "verification_code": c.verification_code,
                        }
                        for c in item.failed_criteria
                    ],
                }
                for item in request.failed_requirements
            ],
        }
        return replace(task, context=context)

    @staticmethod
    def _emit(run: Run, event_type: EventType, data: dict[str, object]) -> None:
        run.events.append(Event(run_id=run.id, type=event_type, data=data))

    @classmethod
    def _emit_revision_completed(cls, run, request, status, acceptance):
        cls._emit(run, EventType.REVISION_COMPLETED, {
            "run_id": run.id,
            "revision_id": request.revision_id,
            "attempt_number": request.attempt_number,
            "status": status.value,
            "acceptance_status": acceptance.status.value if acceptance else "unavailable",
        })
