"""Provider-neutral interface for read-only tools."""

from typing import Protocol

from app.tools.contracts import ToolDefinition, ToolInvocation, ToolResult


class ReadOnlyTool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...

    def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute a validated invocation without mutating project state."""
