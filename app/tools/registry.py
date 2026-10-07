"""Explicit registry for available provider-neutral tools."""

from pathlib import Path
from typing import Iterable

from app.tools.base import Tool
from app.tools.contracts import ToolDefinition
from app.tools.read_project_file import ReadProjectFile
from app.tools.write_project_file import WriteProjectFile


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


# The tools that make up Forge's execution plane today. Tools are registered
# explicitly and by name: nothing is discovered by scanning Python classes, so a
# new tool is only reachable once it is added here on purpose.
def build_default_tool_registry(
    project_root: str | Path | None = None,
    *,
    allowed_files: Iterable[str] = (),
) -> ToolRegistry:
    """Return the registry of Forge's built-in tools.

    This is the single place where the production tool set is declared. The same
    registry instance is meant to serve both execution and capability discovery,
    so enumeration can never report a tool that execution cannot reach.

    ``ReadProjectFile`` is deny-by-default: with no allow-listed files it is still
    registered but permits nothing. Registration is therefore driven by whether a
    project root is known, not by how permissive the allow-list is, so the
    registry stays deterministic and the read boundary is never widened to make a
    listing look fuller.
    """
    registry = ToolRegistry()
    registry.register(WriteProjectFile())
    if project_root is not None:
        registry.register(ReadProjectFile(project_root, allowed_files))
    return registry
