"""Execution authorization pipeline enforcing Permission, Approval, Policy, and AuthorizedExecution boundaries."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Optional

from app.execution.adapter import ExecutionBackend, LocalExecutionAdapter
from app.execution.identity import CommandIdentity, command_identity, command_is_allowed
from app.execution.intent import (
    _COORDINATOR_SENTINEL,
    AuthorizedExecution,
    ExecutionIntent,
    IntentBuilder,
)
from app.execution.paths import PathSecurityError
from app.execution.policy import ExecutionPolicy
from app.execution.redaction import DefaultSecretRedactor, SecretRedactor
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.orchestrator.models import EventType
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolver,
    ApprovalState,
)


class ExecutionCoordinator:
    """Coordinates deterministic execution authorization and dispatch.

    Enforces strict sequence:
    1. Validate ExecutionRequest and profile.
    2. Permission check against allowed_commands.
    3. Build ExecutionIntent via IntentBuilder.
    4. Approval check (intent fingerprint verification and single-use consumption).
    5. ExecutionPolicy check.
    6. Mandatory workspace_root validation (fail-closed if missing, relative, or not dir).
    7. AuthorizedExecution creation (internal marker token).
    8. ExecutionBackend / LocalExecutionAdapter dispatch.
    """

    def __init__(
        self,
        adapter: Optional[ExecutionBackend] = None,
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
        run_scope: Optional[object] = None,
        observer: Optional[Callable[[EventType, dict[str, object]], None]] = None,
    ) -> ExecutionResult:
        """Run through the strict authorization chain before dispatching to adapter."""
        # Imported lazily: run_scope imports the execution identity module and the
        # execution package imports this coordinator, so a module-level import is
        # circular.
        from app.runtime.run_scope import RunScope, RunScopeError, require_active_scope

        profile = request.profile if request else None
        profile_id = profile.profile_id if profile else None

        # DECISION 1 (Block 3.1): the run identity is established before the scope
        # is created and is never recovered from the scope.
        if run_scope is None:
            candidate = run_id or str(getattr(request, "metadata", {}).get("run_id") or "")
            frozen = RunScope.frozen_scope(candidate) if candidate else None
            if frozen is not None or allowed_commands is not None:
                return self._deny(
                    request=request,
                    run_id=candidate,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                    reason="run_scope_required",
                    message=(
                        "Execution denied: this run requires an active frozen "
                        "RunScope before it may dispatch"
                    ),
                    observer=observer,
                )
        else:
            resolved = run_id or str(getattr(request, "metadata", {}).get("run_id") or "")
            scope_run_id = str(getattr(run_scope, "run_id", "") or "")
            if not resolved.strip():
                return self._deny(
                    request=request,
                    run_id=resolved,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                    reason="run_scope_violation",
                    message=(
                        "Execution denied: an active run id is required and is never "
                        "taken from the run scope"
                    ),
                    observer=observer,
                )
            if resolved != scope_run_id:
                return self._deny(
                    request=request,
                    run_id=resolved,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                    reason="run_scope_violation",
                    message="Execution denied: run scope does not belong to this run",
                    observer=observer,
                )
            try:
                scope = require_active_scope(resolved, run_scope)
                # An adapter that runs inside an ephemeral, adapter-managed
                # workspace executes in a private scratch directory, so the
                # execution root is not a scope-checkable property there. That
                # isolation is enforced by EphemeralWorkspaceManager plus the
                # Workspace boundary on the scratch root, independently.
                if not bool(getattr(self._adapter, "_isolate_workspace", False)):
                    scope.validate_workspace_root(workspace_root)
                # validate_execution_request checks the command as a full identity,
                # which preserves a pinned argv. Re-checking only the bare
                # executable here would drop that pinning and reject a legitimate
                # identity-pinned invocation.
                scope.validate_execution_request(request)
                # The request's own command must be inside the perimeter as a full
                # identity, so a pinned argv cannot be swapped for a different
                # invocation of the same executable.
                if request.command:
                    scope.validate_command_set((tuple(request.command),))
                # The caller's declared permission set may only narrow the scope.
                if allowed_commands is not None:
                    scope.validate_command_set(allowed_commands)
            except RunScopeError as exc:
                return self._deny(
                    request=request,
                    run_id=resolved,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                    reason="run_scope_violation",
                    message=f"Execution denied by run scope: {exc}",
                    observer=observer,
                )
            run_id = resolved

        # 1. Notify execution requested
        self._emit(
            observer,
            EventType.EXECUTION_REQUESTED,
            {
                "run_id": run_id,
                "request_id": request.request_id if request else "unknown",
                "profile_id": profile_id,
                "status": "REQUESTED",
            },
        )

        if request is None or not isinstance(request, ExecutionRequest):
            return self._deny(
                request=request or ExecutionRequest(command=()),
                run_id=run_id,
                profile_id=profile_id,
                outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                reason="request_invalid",
                message="Execution denied: execution request is invalid",
                observer=observer,
            )

        if profile is None:
            return self._deny(
                request=request,
                run_id=run_id,
                profile_id=profile_id,
                outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                reason="profile_missing",
                message="Execution denied: profile is required",
                observer=observer,
            )

        # 2. Permission check
        if allowed_commands is None or not request.command:
            return self._deny(
                request=request,
                run_id=run_id,
                profile_id=profile_id,
                outcome_status=ExecutionOutcomeStatus.PERMISSION_DENIED,
                reason="permission_missing",
                message="Execution denied: explicit command permission is required",
                observer=observer,
            )
        if not command_is_allowed(request.command, allowed_commands):
            return self._deny(
                request=request,
                run_id=run_id,
                profile_id=profile_id,
                outcome_status=ExecutionOutcomeStatus.PERMISSION_DENIED,
                reason="permission_denied",
                message="Execution denied by permission check: command not permitted",
                observer=observer,
            )

        # 3. Build ExecutionIntent via IntentBuilder
        try:
            intent = IntentBuilder.from_request(request, profile).build()
        except PathSecurityError as exc:
            return self._deny(
                request=request,
                run_id=run_id,
                profile_id=profile_id,
                outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                reason="unsafe_working_directory" if "working" in str(exc).lower() else f"path_error:{exc}",
                message=f"Execution denied by policy: path security validation failed: {exc}",
                observer=observer,
            )
        except Exception as exc:
            return self._deny(
                request=request,
                run_id=run_id,
                profile_id=profile_id,
                outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                reason=f"intent_build_failed:{exc}",
                message=f"Execution denied: failed to build execution intent: {exc}",
                observer=observer,
            )

        # 4. Approval check
        needs_approval = bool(request.approval_required)
        if not needs_approval and approval_policy is not None and request.command:
            try:
                identity = command_identity(request.command)
            except ValueError:
                return self._deny(
                    request=request,
                    run_id=run_id,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.PERMISSION_DENIED,
                    reason="invalid_command",
                    message="Execution denied: command identity is invalid",
                    observer=observer,
                )
            if approval_policy.evaluate("execute") == ApprovalState.REQUIRED:
                needs_approval = True
            elif approval_policy.evaluate(identity) == ApprovalState.REQUIRED:
                needs_approval = True
            elif approval_policy.evaluate(identity.executable) == ApprovalState.REQUIRED:
                needs_approval = True

        if needs_approval:
            if approval_resolver is None:
                return self._deny(
                    request=request,
                    run_id=run_id,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.APPROVAL_WAITING,
                    reason="approval_waiting",
                    message="Execution waiting for approval: approval resolver is missing",
                    observer=observer,
                )

            approval_req = ApprovalRequest(
                run_id=run_id,
                invocation_id=request.request_id,
                tool_id=request.command[0] if request.command else "execute",
                reason="execution approval required",
                intent_fingerprint=intent.fingerprint,
            )
            resolution = approval_resolver.resolve(approval_req)
            res_decision = getattr(resolution, "decision", resolution)
            res_fp = getattr(resolution, "approved_fingerprint", "")

            if res_decision != ApprovalState.APPROVED:
                outcome = (
                    ExecutionOutcomeStatus.APPROVAL_REJECTED
                    if res_decision == ApprovalState.REJECTED
                    else ExecutionOutcomeStatus.APPROVAL_WAITING
                )
                reason = "approval_rejected" if res_decision == ApprovalState.REJECTED else "approval_waiting"
                msg = (
                    "Execution rejected by approval resolver"
                    if res_decision == ApprovalState.REJECTED
                    else "Execution waiting for approval"
                )
                return self._deny(
                    request=request,
                    run_id=run_id,
                    profile_id=profile_id,
                    outcome_status=outcome,
                    reason=reason,
                    message=msg,
                    observer=observer,
                )

            # Invariant I1: Intent A cannot authorize Intent B
            # Independently verify fingerprint
            if res_fp and res_fp != intent.fingerprint:
                return self._deny(
                    request=request,
                    run_id=run_id,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.APPROVAL_WAITING,
                    reason="intent_fingerprint_mismatch",
                    message="Execution denied: approval fingerprint does not match execution intent",
                    observer=observer,
                )

        # 5. ExecutionPolicy check
        policy_decision = self._policy.evaluate(intent, profile)
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

        # 6. Mandatory workspace_root validation (resolves F2)
        ws_root = workspace_root
        if ws_root is None and hasattr(self._adapter, "_workspace_root") and self._adapter._workspace_root is not None:
            ws_root = self._adapter._workspace_root

        is_local_spawner = self._adapter is None or isinstance(self._adapter, LocalExecutionAdapter)

        if ws_root is None:
            if is_local_spawner:
                return self._deny(
                    request=request,
                    run_id=run_id,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                    reason="workspace_root_required",
                    message="Execution denied: mandatory workspace_root is required",
                    observer=observer,
                )
            else:
                ws_root = Path(tempfile.gettempdir())
        else:
            if not isinstance(ws_root, Path) or not ws_root.is_absolute() or not ws_root.exists() or not ws_root.is_dir():
                return self._deny(
                    request=request,
                    run_id=run_id,
                    profile_id=profile_id,
                    outcome_status=ExecutionOutcomeStatus.POLICY_DENIED,
                    reason="workspace_root_invalid",
                    message="Execution denied: workspace_root must be an existing absolute directory",
                    observer=observer,
                )

        # 7. Create AuthorizedExecution
        authorized = AuthorizedExecution.create(
            intent=intent,
            workspace_root=ws_root,
            run_id=run_id,
            metadata={
                **request.metadata,
                "command_executable": request.command[0] if request.command else "",
                "run_id": run_id,
                "request_id": request.request_id,
            },
            coordinator_token=_COORDINATOR_SENTINEL,
        )

        # 8. LocalExecutionAdapter / ExecutionBackend dispatch
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
                workspace_root=ws_root,
                policy=self._policy,
                redactor=self._redactor,
            )

        raw_result = adapter.execute(authorized)
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
                "artifact_count": len(result.artifacts),
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
        return command_is_allowed((executable,), allowed_commands)

    @classmethod
    def _intent_fingerprint(cls, request: ExecutionRequest, identity: object = None) -> str:
        """Deterministic canonical intent fingerprint computed from complete ExecutionIntent."""
        intent = IntentBuilder.from_request(request, request.profile).build()
        return intent.fingerprint

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
