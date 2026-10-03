"""Deterministic, provider-neutral policy checks for tool invocations."""

from dataclasses import dataclass
from enum import Enum

from app.tools.contracts import ToolInvocation


class PermissionDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class PermissionReason(str, Enum):
    ALLOWED = "ALLOWED"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    NOT_ALLOWED = "NOT_ALLOWED"
    INVALID_INVOCATION = "INVALID_INVOCATION"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    TOOL_LOOP_LIMIT = "TOOL_LOOP_LIMIT"


@dataclass(frozen=True)
class ToolExecutionContext:
    run_id: str
    context_fingerprint: str
    allowed_tool_ids: frozenset[str]
    round_number: int = 0


@dataclass(frozen=True)
class PermissionCheck:
    run_id: str
    tool_id: str
    invocation_id: str
    decision: PermissionDecision
    reason: PermissionReason


class PermissionPolicy:
    """Allow only registered tools explicitly authorized for this Run."""

    def check(
        self,
        invocation: ToolInvocation,
        *,
        context: ToolExecutionContext | None,
        tool_registered: bool,
        input_valid: bool | None = None,
    ) -> PermissionCheck:
        run_id = context.run_id if isinstance(context, ToolExecutionContext) else ""
        tool_id = getattr(invocation, "tool_id", "")
        invocation_id = getattr(invocation, "invocation_id", "")

        if (
            not isinstance(invocation, ToolInvocation)
            or not isinstance(tool_id, str)
            or not tool_id.strip()
            or not isinstance(invocation_id, str)
            or not invocation_id.strip()
            or not isinstance(invocation.input, dict)
            or not invocation.input
            or any(not isinstance(key, str) for key in invocation.input)
            or input_valid is False
        ):
            return self._decision(
                run_id, tool_id, invocation_id,
                PermissionDecision.DENY, PermissionReason.INVALID_INVOCATION,
            )
        if (
            not isinstance(context, ToolExecutionContext)
            or not isinstance(context.run_id, str)
            or not context.run_id.strip()
            or not isinstance(context.context_fingerprint, str)
            or not context.context_fingerprint.strip()
            or not isinstance(context.allowed_tool_ids, frozenset)
            or any(not isinstance(value, str) or not value.strip() for value in context.allowed_tool_ids)
            or isinstance(context.round_number, bool)
            or not isinstance(context.round_number, int)
            or context.round_number < 0
        ):
            return self._decision(
                run_id, tool_id, invocation_id,
                PermissionDecision.DENY, PermissionReason.MISSING_CONTEXT,
            )
        if not tool_registered:
            return self._decision(
                context.run_id, tool_id, invocation_id,
                PermissionDecision.DENY, PermissionReason.UNKNOWN_TOOL,
            )
        if tool_id not in context.allowed_tool_ids:
            return self._decision(
                context.run_id, tool_id, invocation_id,
                PermissionDecision.DENY, PermissionReason.NOT_ALLOWED,
            )
        if context.round_number > 0:
            return self._decision(
                context.run_id, tool_id, invocation_id,
                PermissionDecision.DENY, PermissionReason.TOOL_LOOP_LIMIT,
            )
        return self._decision(
            context.run_id, tool_id, invocation_id,
            PermissionDecision.ALLOW, PermissionReason.ALLOWED,
        )

    @staticmethod
    def _decision(run_id, tool_id, invocation_id, decision, reason) -> PermissionCheck:
        return PermissionCheck(
            run_id=str(run_id or ""),
            tool_id=str(tool_id or ""),
            invocation_id=str(invocation_id or ""),
            decision=decision,
            reason=reason,
        )
