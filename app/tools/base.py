"""Provider-neutral interfaces for project tools."""

from typing import Protocol

from app.tools.contracts import ToolDefinition, ToolInvocation, ToolResult
from app.tools.permissions import ToolExecutionContext


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        """Execute a validated invocation within the supplied Run context."""

    def validate_input(self, tool_input: object) -> bool:
        """Validate input shape without performing the requested operation."""


class ReadOnlyTool(Tool, Protocol):
    """Compatibility name for tools that do not mutate project state."""
