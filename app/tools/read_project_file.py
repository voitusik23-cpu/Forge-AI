"""Read a file only when its relative path is explicitly allow-listed."""

from pathlib import Path
from typing import Iterable

from app.tools.contracts import ToolDefinition, ToolInvocation, ToolResult, ToolStatus
from app.tools.permissions import ToolExecutionContext


class ReadProjectFile:
    TOOL_ID = "read_project_file"

    def __init__(self, project_root: str | Path, allowed_files: Iterable[str]) -> None:
        self._root = Path(project_root).resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("project_root must be a directory")
        normalized: set[str] = set()
        for value in allowed_files:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("allowed file paths must be non-empty relative paths")
            candidate = Path(value)
            if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
                raise ValueError("allowed file paths must stay within project_root")
            normalized.add(candidate.as_posix())
        self._allowed_files = frozenset(normalized)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            id=self.TOOL_ID,
            name="ReadProjectFile",
            description="Read one explicitly allow-listed project file as UTF-8 text.",
        )

    def validate_input(self, tool_input: object) -> bool:
        if not isinstance(tool_input, dict):
            return False
        relative_path = tool_input.get("path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            return False
        requested = Path(relative_path)
        return (
            not requested.is_absolute()
            and not requested.drive
            and ".." not in requested.parts
        )

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        relative_path = invocation.input.get("path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.FAILED,
                error="input.path must be a non-empty relative path",
            )
        requested = Path(relative_path)
        if requested.is_absolute() or requested.drive or ".." in requested.parts:
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.DENIED,
                error="file path is outside the explicit allow-list",
            )
        normalized = requested.as_posix()
        if normalized not in self._allowed_files:
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.DENIED,
                error="file path is outside the explicit allow-list",
            )
        target = (self._root / requested).resolve(strict=False)
        try:
            target.relative_to(self._root)
        except ValueError:
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.DENIED,
                error="file path is outside the project root",
            )
        try:
            if not target.is_file():
                raise OSError("not a regular file")
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.FAILED,
                error="allow-listed project file could not be read as UTF-8 text",
            )
        return ToolResult(invocation.invocation_id, ToolStatus.COMPLETED, output=content)
