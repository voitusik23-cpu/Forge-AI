"""Run policy checks before executing one registered read-only tool."""

from collections.abc import Callable
from hashlib import sha256

from app.orchestrator.models import EventType
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolution,
    ApprovalResolver,
    ApprovalState,
    tool_invocation_fingerprint,
)
from app.tools.contracts import ToolInvocation, ToolResult, ToolStatus
from app.tools.changesets import ChangeSetCollector
from app.tools.permissions import (
    PermissionCheck,
    PermissionDecision,
    PermissionPolicy,
    ToolExecutionContext,
)
from app.tools.registry import ToolNotFoundError, ToolRegistry
from app.tools.workspace import Workspace, WorkspacePathError


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_policy: PermissionPolicy | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approval_resolver: ApprovalResolver | None = None,
        change_set_collector: ChangeSetCollector | None = None,
    ) -> None:
        self._registry = registry
        self._permission_policy = permission_policy or PermissionPolicy()
        self._approval_policy = approval_policy or ApprovalPolicy()
        self._approval_resolver = approval_resolver
        self.change_set_collector = change_set_collector or ChangeSetCollector()

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
            # The approval is cryptographically bound to this exact payload.
            fingerprint = tool_invocation_fingerprint(
                permission.tool_id, getattr(invocation, "input", None)
            )
            request = ApprovalRequest(
                run_id=permission.run_id,
                invocation_id=permission.invocation_id,
                tool_id=permission.tool_id,
                reason="tool requires explicit approval",
                intent_fingerprint=fingerprint,
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
            if resolution is None:
                return ToolResult(
                    permission.invocation_id,
                    ToolStatus.WAITING_FOR_APPROVAL,
                    error="approval required",
                )
            if not isinstance(resolution, ApprovalResolution):
                return ToolResult(
                    permission.invocation_id,
                    ToolStatus.FAILED,
                    error=f"approval resolver returned unexpected type: {type(resolution).__name__}",
                )

            if resolution.decision not in (
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
                resolution=resolution.decision.value,
            )
            if resolution.decision == ApprovalState.REJECTED:
                return ToolResult(
                    permission.invocation_id,
                    ToolStatus.DENIED,
                    error="approval rejected",
                )

            # Cryptographic payload binding check: resolution.approved_fingerprint must match request.intent_fingerprint
            if (
                not resolution.approved_fingerprint
                or resolution.approved_fingerprint != request.intent_fingerprint
            ):
                return ToolResult(
                    permission.invocation_id,
                    ToolStatus.DENIED,
                    error="approval payload fingerprint mismatch",
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
        mutation_snapshot = None
        if tool is not None and getattr(tool, "mutates", False):
            mutation_snapshot = self.change_set_collector.capture_before(invocation, context)
        try:
            result = tool.execute(invocation, context=context)
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
            if mutation_snapshot is not None:
                self.change_set_collector.record_after(mutation_snapshot)
            output_bytes = (
                result.output
                if isinstance(result.output, bytes)
                else str(result.output or "").encode("utf-8")
            )
            safe_metadata = self._safe_metadata(result.metadata)
            self._emit(
                observer,
                EventType.TOOL_EXECUTION_COMPLETED,
                run_id=permission.run_id,
                invocation_id=permission.invocation_id,
                tool_id=permission.tool_id,
                output_sha256=sha256(output_bytes).hexdigest(),
                **safe_metadata,
            )
        else:
            self._emit(
                observer,
                EventType.TOOL_EXECUTION_FAILED,
                run_id=permission.run_id,
                invocation_id=permission.invocation_id,
                tool_id=permission.tool_id,
                status=result.status.value,
                **self._safe_metadata(result.metadata),
            )
        return result

    @staticmethod
    def _safe_metadata(metadata: object) -> dict[str, object]:
        """Copy only the write tool's small, non-content result metadata."""
        if not isinstance(metadata, dict):
            return {}
        safe: dict[str, object] = {}
        relative_path = metadata.get("relative_path")
        bytes_written = metadata.get("bytes_written")
        content_sha256 = metadata.get("content_sha256")
        if isinstance(relative_path, str):
            try:
                safe["relative_path"] = "/".join(
                    Workspace.normalize_relative_path(relative_path)
                )
            except WorkspacePathError:
                pass
        if isinstance(bytes_written, int) and not isinstance(bytes_written, bool) and bytes_written >= 0:
            safe["bytes_written"] = bytes_written
        if (
            isinstance(content_sha256, str)
            and len(content_sha256) == 64
            and all(char in "0123456789abcdef" for char in content_sha256)
        ):
            safe["content_sha256"] = content_sha256
        return safe

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
