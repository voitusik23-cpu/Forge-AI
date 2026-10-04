"""Deterministic DecisionProvider implementation for Engineering Runs."""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from app.decision.models import (
    Decision,
    DecisionAction,
    DecisionRequest,
    DecisionType,
)
from app.projects.state import ProjectStateStatus


class DecisionProvider(Protocol):
    """Protocol for provider-neutral run control decision providers."""

    def decide(self, request: DecisionRequest) -> Decision:
        """Evaluate request and return a structured Decision recommendation."""
        ...


class DeterministicDecisionProvider:
    """Rule-based, deterministic decision engine for Engineering Run control flow.

    Operates strictly on structured state and request context.
    Never executes actions, calls LLMs, or accesses external networks.
    """

    def decide(self, request: DecisionRequest) -> Decision:
        dec_id = str(uuid4())
        conditions = set(request.blocking_conditions)
        ps = request.current_project_state
        state_status = ps.status if ps else None

        # Rule A: Unrecoverable security, permission, or policy failure
        if "security_denied" in conditions or "permission_denied" in conditions:
            return Decision(
                decision_id=dec_id,
                run_id=request.run_id,
                decision_type=DecisionType.FAIL,
                action=DecisionAction.FAIL_RUN,
                reason_code="security_denied",
                attempt_number=request.attempt_number,
                references={"blocking_condition": "security_denied"},
            )
        if "policy_denied" in conditions:
            return Decision(
                decision_id=dec_id,
                run_id=request.run_id,
                decision_type=DecisionType.FAIL,
                action=DecisionAction.FAIL_RUN,
                reason_code="policy_denied",
                attempt_number=request.attempt_number,
                references={"blocking_condition": "policy_denied"},
            )

        # Rule B: Revision limit reached
        if "revision_limit_reached" in conditions:
            return Decision(
                decision_id=dec_id,
                run_id=request.run_id,
                decision_type=DecisionType.FAIL,
                action=DecisionAction.FAIL_RUN,
                reason_code="revision_limit_reached",
                attempt_number=request.attempt_number,
                references={"blocking_condition": "revision_limit_reached"},
            )

        # Rule C: Approval pending or required
        if "approval_pending" in conditions or "approval_required" in conditions:
            action = (
                DecisionAction.REQUEST_USER_APPROVAL
                if "approval_required" in conditions
                else DecisionAction.WAIT_FOR_APPROVAL
            )
            return Decision(
                decision_id=dec_id,
                run_id=request.run_id,
                decision_type=DecisionType.REQUEST_APPROVAL,
                action=action,
                reason_code="approval_pending",
                attempt_number=request.attempt_number,
                references={"blocking_condition": "approval_pending"},
            )

        # Rule D: Acceptance is PASS
        if (
            request.acceptance_status == "pass"
            or state_status == ProjectStateStatus.ACCEPTED
        ):
            return Decision(
                decision_id=dec_id,
                run_id=request.run_id,
                decision_type=DecisionType.COMPLETE,
                action=DecisionAction.COMPLETE_RUN,
                reason_code="acceptance_passed",
                attempt_number=request.attempt_number,
                references={"acceptance_status": "pass"},
            )

        # Rule E: Required verification failed
        if (
            request.verification_status_summary == "fail"
            or state_status == ProjectStateStatus.FAILED
            or "verification_failed" in conditions
        ):
            return Decision(
                decision_id=dec_id,
                run_id=request.run_id,
                decision_type=DecisionType.REVISE,
                action=DecisionAction.REQUEST_REVISION,
                reason_code="verification_failed",
                attempt_number=request.attempt_number,
                references={"verification_status": "fail"},
            )

        # Rule F: Verification is required but has not run
        if (
            state_status == ProjectStateStatus.CHANGED
            or "verification_required" in conditions
        ):
            return Decision(
                decision_id=dec_id,
                run_id=request.run_id,
                decision_type=DecisionType.VERIFY,
                action=DecisionAction.RUN_VERIFICATION,
                reason_code="verification_required",
                attempt_number=request.attempt_number,
                references={"project_state": "CHANGED"},
            )

        # Rule G: Execution is required and has not happened
        if (
            state_status == ProjectStateStatus.INITIAL
            or "execution_required" in conditions
            or (ps is None and not conditions)
        ):
            return Decision(
                decision_id=dec_id,
                run_id=request.run_id,
                decision_type=DecisionType.CONTINUE,
                action=DecisionAction.EXECUTE,
                reason_code="execution_required",
                attempt_number=request.attempt_number,
                references={"project_state": "INITIAL"},
            )

        # Rule H: Otherwise wait
        return Decision(
            decision_id=dec_id,
            run_id=request.run_id,
            decision_type=DecisionType.WAIT,
            action=DecisionAction.WAIT_FOR_APPROVAL,
            reason_code="idle",
            attempt_number=request.attempt_number,
        )
