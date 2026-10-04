"""Execution authorization pipeline enforcing Permission, Approval, and Policy boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Optional

from app.execution.adapter import LocalExecutionAdapter
from app.execution.policy import ExecutionPolicy
from app.execution.redaction import DefaultSecretRedactor, SecretRedactor
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.orchestrator.models import EventType
from app.tools.approval import ApprovalPolicy, ApprovalRequest, ApprovalResolver, ApprovalState


class ExecutionCoordinator:
    """Coordinates deterministic execution authorization and dispatch.

    Enforces strict sequence:
    1. ExecutionRequest
    2. Permission check
    3. Approval check (if required)
    4. ExecutionPolicy
    5. LocalExecutionAdapter
    """

    def __init__(
        self,
        adapter: Optional[LocalExecutionAdapter] = None,
        policy: Optional[ExecutionPolicy] = None,
        redactor: Optional[SecretRedactor] = None,
    ) -> None:
        self._adapter = adapter
        self._policy = policy or ExecutionPolicy()
        self._redactor = redactor or DefaultSecretRedactor()

    def execute(
        self,
        request: ExecutionRequest,
        *,
        workspace_root: Optional[Path] = None,
        run_id: str = "",
        allowed_commands: Optional[frozenset[str]] = None,
        approval_policy: Optional[ApprovalPolicy] = None,
        approval_resolver: Optional[ApprovalResolver] = None,
        observer: Optional[Callable[[EventType, dict[str, object]], None]] = None,
    ) -> ExecutionResult:
        """Run through the strict authorization chain before dispatching to adapter."""
        profile_id = request.profile.profile_id if request.profile else None

        # 1. Notify execution requested
        self._emit(
            observer,
            EventType.EXECUTION_REQUESTED,
            {
                "run_id": run_id,
                "request_id": request.request_id,
                "profile_id": profile_id,
                "status": "REQUESTED",
            },
        )

        # 2. Permission check
        if allowed_commands is not None and request.command:
            executable = request.command[0]
            if not self._is_permission_allowed(executable, allowed_commands):
                return self._deny(
                    request=request,
                    run_id=run_id,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.PERMISSION_DENIED,
                    reason="permission_denied",
                    message="Execution denied by permission check: command not permitted",
                    observer=observer,
                )

        # 3. Approval check
        if approval_policy is not None and request.command:
            executable = request.command[0]
            approval_state = approval_policy.evaluate(executable)
            if approval_state != ApprovalState.REQUIRED:
                approval_state = approval_policy.evaluate("execute")
            if approval_state != ApprovalState.REQUIRED:
                from pathlib import Path as _P
                approval_state = approval_policy.evaluate(_P(executable).name)
            if approval_state == ApprovalState.REQUIRED:
                if approval_resolver is None:
                    return self._deny(
                        request=request,
                        run_id=run_id,
                        profile_id=profile_id,
                        outcome_status=ExecutionOutcomeStatus.APPROVAL_WAITING,
                        reason="approval_waiting",
                        message="Execution waiting for approval",
                        observer=observer,
                    )
                approval_req = ApprovalRequest(
                    run_id=run_id,
                    invocation_id=request.request_id,
                    tool_id=executable,
                    reason="execution approval required",
                )
                decision = approval_resolver.resolve(approval_req)
                if decision is None or decision == ApprovalState.REQUIRED:
                    return self._deny(
                        request=request,
                        run_id=run_id,
                        profile_id=profile_id,
                        outcome_status=ExecutionOutcomeStatus.APPROVAL_WAITING,
                        reason="approval_waiting",
                        message="Execution waiting for approval",
                        observer=observer,
                    )
                if decision == ApprovalState.REJECTED:
                    return self._deny(
                        request=request,
                        run_id=run_id,
                        profile_id=profile_id,
                        outcome_status=ExecutionOutcomeStatus.APPROVAL_REJECTED,
                        reason="approval_rejected",
                        message="Execution rejected by approval resolver",
                        observer=observer,
                    )

        # 4. ExecutionPolicy check
        policy_decision = self._policy.evaluate(request)
        self._emit(
            observer,
            EventType.EXECUTION_POLICY_CHECKED,
            {
                "run_id": run_id,
                "request_id": request.request_id,
                "profile_id": profile_id,
                "allowed": policy_decision.allowed,
                "reason": policy_decision.reason,
            },
        )
        if not policy_decision.allowed:
            return self._deny(
                request=request,
                run_id=run_id,
                profile_id=profile_id,
                outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                reason=policy_decision.reason,
                message=f"Execution denied by policy: {policy_decision.reason}",
                observer=observer,
            )

        # 5. LocalExecutionAdapter execution
        self._emit(
            observer,
            EventType.EXECUTION_STARTED,
            {
                "run_id": run_id,
                "request_id": request.request_id,
                "profile_id": profile_id,
            },
        )

        adapter = self._adapter
        if adapter is None:
            adapter = LocalExecutionAdapter(
                workspace_root=workspace_root or Path("."),
                policy=self._policy,
                redactor=self._redactor,
            )
        raw_result = adapter.execute(request)
        outcome_status = self._map_outcome_status(raw_result.status)
        result = replace(
            raw_result,
            outcome_status=outcome_status,
            metadata={**raw_result.metadata, "outcome_status": outcome_status.value},
        )

        self._emit(
            observer,
            EventType.EXECUTION_COMPLETED,
            {
                "run_id": run_id,
                "request_id": result.request_id,
                "profile_id": profile_id,
                "status": result.status.value,
                "outcome_status": outcome_status.value,
                "exit_code": result.exit_code,
                "duration": result.duration_seconds,
                "truncated": result.truncated,
            },
        )

        return result

    execute_request = execute

    def _deny(
        self,
        *,
        request: ExecutionRequest,
        run_id: str,
        profile_id: Optional[str],
        outcome_status: ExecutionOutcomeStatus,
        reason: str,
        message: str,
        observer: Optional[Callable[[EventType, dict[str, object]], None]],
    ) -> ExecutionResult:
        self._emit(
            observer,
            EventType.EXECUTION_DENIED,
            {
                "run_id": run_id,
                "request_id": request.request_id,
                "profile_id": profile_id,
                "status": "DENIED",
                "outcome_status": outcome_status.value,
                "reason": reason,
            },
        )
        raw_result = ExecutionResult(
            request_id=request.request_id,
            status=ExecutionStatus.DENIED,
            outcome_status=outcome_status,
            exit_code=None,
            stdout="",
            stderr=message,
            duration_seconds=0.0,
            metadata={
                "outcome_status": outcome_status.value,
                "denial_reason": reason,
            },
        )
        return self._redactor.redact_result(raw_result)

    @staticmethod
    def _is_permission_allowed(executable: str, allowed_commands: frozenset[str]) -> bool:
        exec_str = executable.strip()
        if exec_str in allowed_commands:
            return True
        from pathlib import Path
        name = Path(exec_str).name
        stem = Path(exec_str).stem
        for allowed in allowed_commands:
            if name == allowed or stem == allowed or name.lower() == allowed.lower():
                return True
        return False

    @staticmethod
    def _map_outcome_status(status: ExecutionStatus) -> ExecutionOutcomeStatus:
        if status == ExecutionStatus.SUCCESS:
            return ExecutionOutcomeStatus.EXECUTION_SUCCESS
        if status == ExecutionStatus.FAILURE:
            return ExecutionOutcomeStatus.EXECUTION_FAILURE
        if status == ExecutionStatus.TIMEOUT:
            return ExecutionOutcomeStatus.EXECUTION_TIMEOUT
        if status == ExecutionStatus.ERROR:
            return ExecutionOutcomeStatus.EXECUTION_ERROR
        return ExecutionOutcomeStatus.POLICY_DENIED

    @staticmethod
    def _emit(
        observer: Optional[Callable[[EventType, dict[str, object]], None]],
        event_type: EventType,
        data: dict[str, object],
    ) -> None:
        if observer is not None:
            observer(event_type, data)
