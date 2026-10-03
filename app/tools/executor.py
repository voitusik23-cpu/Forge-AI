"""Run policy checks before executing one registered read-only tool."""

from collections.abc import Callable
from hashlib import sha256

from app.orchestrator.models import EventType
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolver,
    ApprovalState,
)
from app.tools.contracts import ToolInvocation, ToolResult, ToolStatus
from app.tools.permissions import (
    PermissionCheck,
    PermissionDecision,
    PermissionPolicy,
    ToolExecutionContext,
)
from app.tools.registry import ToolNotFoundError, ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_policy: PermissionPolicy | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approval_resolver: ApprovalResolver | None = None,
    ) -> None:
        self._registry = registry
        self._permission_policy = permission_policy or PermissionPolicy()
        self._approval_policy = approval_policy or ApprovalPolicy()
        self._approval_resolver = approval_resolver

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        context: ToolExecutionContext | None = None,
        observer: Callable[[EventType, dict[str, object]], None],
    ) -> ToolResult:
        tool_id = getattr(invocation, "tool_id", "")
        invocation_id = getattr(invocation, "invocation_id", "")
        self._emit(
            observer,
            EventType.TOOL_INVOCATION_REQUESTED,
            run_id=getattr(context, "run_id", ""),
            invocation_id=invocation_id,
            tool_id=tool_id,
        )
        tool = None
        if isinstance(tool_id, str) and self._registry.contains(tool_id):
            try:
                tool = self._registry.get(tool_id)
            except ToolNotFoundError:
                tool = None
        input_valid = isinstance(getattr(invocation, "input", None), dict)
        if tool is not None:
            try:
                input_valid = bool(tool.validate_input(invocation.input))
            except Exception:
                input_valid = False
        permission = self._permission_policy.check(
            invocation,
            context=context,
            tool_registered=tool is not None,
            input_valid=input_valid,
        )
        self._emit_permission_check(observer, permission)
        if permission.decision == PermissionDecision.DENY:
            self._emit(
                observer,
                EventType.TOOL_INVOCATION_DENIED,
                run_id=permission.run_id,
                invocation_id=permission.invocation_id,
                tool_id=permission.tool_id,
                reason=permission.reason.value,
            )
            return ToolResult(
                invocation_id=permission.invocation_id,
                status=ToolStatus.DENIED,
                error=permission.reason.value,
            )

        approval_state = self._approval_policy.evaluate(permission.tool_id)
        if approval_state == ApprovalState.REQUIRED:
            request = ApprovalRequest(
                run_id=permission.run_id,
                invocation_id=permission.invocation_id,
                tool_id=permission.tool_id,
                reason="tool requires explicit approval",
            )
            self._emit(
                observer,
                EventType.APPROVAL_REQUESTED,
                run_id=request.run_id,
                invocation_id=request.invocation_id,
                tool_id=request.tool_id,
                state=ApprovalState.REQUIRED.value,
                reason=request.reason,
            )
            resolution = (
                self._approval_resolver.resolve(request)
                if self._approval_resolver is not None
                else None
            )
            if not isinstance(resolution, ApprovalState) or resolution not in (
                ApprovalState.APPROVED,
                ApprovalState.REJECTED,
            ):
                return ToolResult(
                    permission.invocation_id,
                    ToolStatus.WAITING_FOR_APPROVAL,
                    error="approval required",
                )
            self._emit(
                observer,
                EventType.APPROVAL_RESOLVED,
                run_id=request.run_id,
                invocation_id=request.invocation_id,
                tool_id=request.tool_id,
                resolution=resolution.value,
            )
            if resolution == ApprovalState.REJECTED:
                return ToolResult(
                    permission.invocation_id,
                    ToolStatus.DENIED,
                    error="approval rejected",
                )

        if tool is None:
            # The registry should not change between check and execution. Keep
            # this defensive failure distinct from a policy denial.
            self._emit(
                observer,
                EventType.TOOL_EXECUTION_FAILED,
                run_id=permission.run_id,
                invocation_id=permission.invocation_id,
                tool_id=permission.tool_id,
                status=ToolStatus.FAILED.value,
            )
            return ToolResult(
                permission.invocation_id,
                ToolStatus.FAILED,
                error="tool became unavailable after permission check",
            )

        self._emit(
            observer,
            EventType.TOOL_EXECUTION_STARTED,
            run_id=permission.run_id,
            invocation_id=permission.invocation_id,
            tool_id=permission.tool_id,
        )
        try:
            result = tool.execute(invocation)
            if (
                not isinstance(result, ToolResult)
                or result.invocation_id != permission.invocation_id
                or not isinstance(result.status, ToolStatus)
            ):
                raise TypeError("tool returned invalid result")
        except Exception as exc:
            result = ToolResult(
                permission.invocation_id,
                ToolStatus.FAILED,
                error=f"tool execution failed ({type(exc).__name__})",
            )

        if result.status == ToolStatus.COMPLETED:
            output_bytes = (
                result.output
                if isinstance(result.output, bytes)
                else str(result.output or "").encode("utf-8")
            )
            self._emit(
                observer,
                EventType.TOOL_EXECUTION_COMPLETED,
                run_id=permission.run_id,
                invocation_id=permission.invocation_id,
                tool_id=permission.tool_id,
                output_sha256=sha256(output_bytes).hexdigest(),
            )
        else:
            self._emit(
                observer,
                EventType.TOOL_EXECUTION_FAILED,
                run_id=permission.run_id,
                invocation_id=permission.invocation_id,
                tool_id=permission.tool_id,
                status=result.status.value,
            )
        return result

    @staticmethod
    def _emit_permission_check(
        observer: Callable[[EventType, dict[str, object]], None],
        permission: PermissionCheck,
    ) -> None:
        observer(
            EventType.PERMISSION_CHECKED,
            {
                "run_id": permission.run_id,
                "invocation_id": permission.invocation_id,
                "tool_id": permission.tool_id,
                "decision": permission.decision.value,
                "reason": permission.reason.value,
            },
        )

    @staticmethod
    def _emit(observer, event_type, **data) -> None:
        observer(event_type, data)
