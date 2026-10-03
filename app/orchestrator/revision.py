"""Bounded, acceptance-triggered revision through the existing RunExecutor."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum

from app.orchestrator.models import Event, EventType, Run, RunState, Task
from app.orchestrator.run import RunExecutor
from app.tools.acceptance import (
    AcceptanceCriterion,
    AcceptanceGate,
    AcceptanceResult,
    AcceptanceStatus,
)
from app.tools.contracts import ToolStatus
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
    WorkspaceVerifier,
)
from app.tools.workspace import Workspace


class RevisionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass(frozen=True)
class FailedCriterion:
    criterion_id: str
    code: str


@dataclass(frozen=True)
class RevisionRequest:
    run_id: str
    revision_id: str
    reason: str
    failed_criteria: tuple[FailedCriterion, ...]
    attempt_number: int


@dataclass(frozen=True)
class RevisionResult:
    status: RevisionStatus
    attempt_number: int
    acceptance_result: AcceptanceResult | None
    run: Run = field(compare=False, repr=False)


class RevisionLoopExecutor:
    """Run at most the configured revisions, preserving all existing boundaries."""

    def __init__(
        self,
        run_executor: RunExecutor,
        *,
        max_revision_attempts: int = 1,
        verifier: WorkspaceVerifier | None = None,
        acceptance_gate: AcceptanceGate | None = None,
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

    def execute(
        self,
        task: Task,
        *,
        criteria: Iterable[AcceptanceCriterion],
        verification_expectations: Mapping[str, VerificationExpectation],
        provider_name: str | None = None,
        agent_name: str | None = None,
        explicit_inputs=(),
        allowed_tool_ids=(),
        workspace: Workspace | None = None,
    ) -> RevisionResult:
        """Execute once, then revise only after a safe, observed Acceptance FAIL."""
        criteria = tuple(criteria)
        expectations = dict(verification_expectations)
        explicit_inputs = tuple(explicit_inputs)
        allowed_tool_ids = tuple(allowed_tool_ids)
        run = self._run_executor.execute(
            task,
            provider_name=provider_name,
            agent_name=agent_name,
            explicit_inputs=explicit_inputs,
            allowed_tool_ids=allowed_tool_ids,
            workspace=workspace,
        )
        acceptance, can_revise = self._verify_and_accept(
            run, criteria, expectations, workspace
        )
        attempt = 0
        if acceptance is None or not can_revise:
            return RevisionResult(RevisionStatus.FAILED, attempt, acceptance, run)
        if acceptance.status == AcceptanceStatus.PASS:
            return RevisionResult(RevisionStatus.COMPLETED, attempt, acceptance, run)
        if self.max_revision_attempts == 0:
            return RevisionResult(RevisionStatus.LIMIT_REACHED, attempt, acceptance, run)

        failed = self._failed_criteria(acceptance)
        for attempt in range(1, self.max_revision_attempts + 1):
            request = RevisionRequest(
                run_id=run.id,
                revision_id=f"{run.id}:revision:{attempt}",
                reason=acceptance.code,
                failed_criteria=failed,
                attempt_number=attempt,
            )
            self._emit(run, EventType.REVISION_STARTED, {
                "run_id": run.id,
                "revision_id": request.revision_id,
                "attempt_number": attempt,
                "reason": request.reason,
            })
            run.state = RunState.REVISING
            revision_task = self._revision_task(task, request)
            self._run_executor.execute(
                revision_task,
                provider_name=provider_name,
                agent_name=agent_name,
                explicit_inputs=explicit_inputs,
                allowed_tool_ids=allowed_tool_ids,
                workspace=workspace,
                _run=run,
            )
            next_acceptance, can_revise = self._verify_and_accept(
                run, criteria, expectations, workspace
            )
            if next_acceptance is None or not can_revise:
                self._emit_revision_completed(run, request, RevisionStatus.FAILED, next_acceptance)
                return RevisionResult(RevisionStatus.FAILED, attempt, next_acceptance, run)
            acceptance = next_acceptance
            if acceptance.status == AcceptanceStatus.PASS:
                self._emit_revision_completed(run, request, RevisionStatus.COMPLETED, acceptance)
                return RevisionResult(RevisionStatus.COMPLETED, attempt, acceptance, run)
            failed = self._failed_criteria(acceptance)
            if attempt == self.max_revision_attempts:
                self._emit_revision_completed(run, request, RevisionStatus.LIMIT_REACHED, acceptance)
                return RevisionResult(RevisionStatus.LIMIT_REACHED, attempt, acceptance, run)

        return RevisionResult(RevisionStatus.LIMIT_REACHED, attempt, acceptance, run)

    def _verify_and_accept(self, run, criteria, expectations, workspace):
        # Hard execution/policy failures and unresolved approvals stop before acceptance.
        if run.state != RunState.COMPLETED or run.result is None:
            return None, False
        tool_results = run.result.tool_results
        if any(result.status != ToolStatus.COMPLETED for result in tool_results):
            return None, False

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
            run_id=run.id,
            observer=lambda event_type, data: self._emit(run, event_type, data),
        )
        return acceptance, not denied

    @staticmethod
    def _failed_criteria(result: AcceptanceResult) -> tuple[FailedCriterion, ...]:
        return tuple(
            FailedCriterion(item.criterion_id, item.code)
            for item in result.results
            if item.status == AcceptanceStatus.FAIL
        )

    @staticmethod
    def _revision_task(task: Task, request: RevisionRequest) -> Task:
        context = dict(task.context)
        context["forge_revision"] = {
            "revision_id": request.revision_id,
            "reason": request.reason,
            "attempt_number": request.attempt_number,
            "failed_criteria": [
                {"criterion_id": item.criterion_id, "code": item.code}
                for item in request.failed_criteria
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
