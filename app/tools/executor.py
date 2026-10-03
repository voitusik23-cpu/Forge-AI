"""Validate and execute one explicitly permitted read-only tool invocation."""

from collections.abc import Callable, Iterable
from hashlib import sha256

from app.orchestrator.models import EventType
from app.tools.contracts import ToolInvocation, ToolResult, ToolStatus
from app.tools.registry import ToolNotFoundError, ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        allowed_tool_ids: Iterable[str] = (),
    ) -> None:
        self._registry = registry
        self._allowed_tool_ids = frozenset(allowed_tool_ids)

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        observer: Callable[[EventType, dict[str, object]], None],
    ) -> ToolResult:
        self._emit(observer, EventType.TOOL_INVOCATION_REQUESTED, invocation)
        try:
            tool = self._registry.get(invocation.tool_id)
        except ToolNotFoundError:
            return self._failed(
                invocation, observer, "tool is not registered", denied=True
            )
        if invocation.tool_id not in self._allowed_tool_ids:
            return self._failed(
                invocation, observer, "tool is not explicitly allowed", denied=True
            )

        observer(
            EventType.TOOL_EXECUTION_STARTED,
            {"invocation_id": invocation.invocation_id, "tool_id": invocation.tool_id},
        )
        try:
            result = tool.execute(invocation)
            if (
                not isinstance(result, ToolResult)
                or result.invocation_id != invocation.invocation_id
                or not isinstance(result.status, ToolStatus)
            ):
                raise TypeError("tool returned invalid result")
        except Exception as exc:
            result = ToolResult(
                invocation.invocation_id,
                ToolStatus.FAILED,
                error=f"tool execution failed ({type(exc).__name__})",
            )

        if result.status == ToolStatus.COMPLETED:
            output_bytes = (
                result.output
                if isinstance(result.output, bytes)
                else str(result.output or "").encode("utf-8")
            )
            observer(
                EventType.TOOL_EXECUTION_COMPLETED,
                {
                    "invocation_id": invocation.invocation_id,
                    "tool_id": invocation.tool_id,
                    "output_sha256": sha256(output_bytes).hexdigest(),
                },
            )
        else:
            observer(
                EventType.TOOL_EXECUTION_FAILED,
                {
                    "invocation_id": invocation.invocation_id,
                    "tool_id": invocation.tool_id,
                    "status": result.status.value,
                },
            )
        return result

    @staticmethod
    def _emit(observer, event_type, invocation) -> None:
        observer(
            event_type,
            {"invocation_id": invocation.invocation_id, "tool_id": invocation.tool_id},
        )

    @classmethod
    def _failed(cls, invocation, observer, message, *, denied):
        result = ToolResult(
            invocation.invocation_id,
            ToolStatus.DENIED if denied else ToolStatus.FAILED,
            error=message,
        )
        observer(
            EventType.TOOL_EXECUTION_FAILED,
            {
                "invocation_id": invocation.invocation_id,
                "tool_id": invocation.tool_id,
                "status": result.status.value,
            },
        )
        return result
