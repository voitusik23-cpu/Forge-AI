"""Explicit registry for available provider-neutral tools."""

from app.tools.base import Tool
from app.tools.contracts import ToolDefinition


class ToolNotFoundError(LookupError):
    """Raised when an invocation references an unregistered tool."""


class DuplicateToolError(ValueError):
    """Raised when a tool ID is registered more than once."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        tool_id = tool.definition.id
        if tool_id in self._tools:
            raise DuplicateToolError(f"Tool '{tool_id}' is already registered")
        self._tools[tool_id] = tool

    def get(self, tool_id: str) -> Tool:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool '{tool_id}' is not registered") from exc

    def list_tools(self) -> list[ToolDefinition]:
        return [self._tools[key].definition for key in sorted(self._tools)]

    def contains(self, tool_id: str) -> bool:
        return tool_id in self._tools
