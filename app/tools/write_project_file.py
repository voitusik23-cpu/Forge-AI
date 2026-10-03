"""Write one text file inside the explicitly supplied Run workspace."""

import hashlib
import os
import tempfile

from app.tools.contracts import ToolDefinition, ToolInvocation, ToolResult, ToolStatus
from app.tools.permissions import ToolExecutionContext
from app.tools.workspace import Workspace, WorkspacePathError


class WriteProjectFile:
    TOOL_ID = "write_project_file"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            id=self.TOOL_ID,
            name="WriteProjectFile",
            description="Create or explicitly overwrite one UTF-8 text file in the Run workspace.",
        )

    def validate_input(self, tool_input: object) -> bool:
        if not isinstance(tool_input, dict):
            return False
        if set(tool_input) - {"relative_path", "content", "overwrite"}:
            return False
        if not isinstance(tool_input.get("content"), str):
            return False
        if "overwrite" in tool_input and not isinstance(tool_input["overwrite"], bool):
            return False
        try:
            Workspace.normalize_relative_path(tool_input.get("relative_path"))
        except WorkspacePathError:
            return False
        return True

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        if not self.validate_input(invocation.input):
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.DENIED,
                error="invalid write request",
            )
        workspace = context.workspace if isinstance(context, ToolExecutionContext) else None
        if not isinstance(workspace, Workspace):
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.DENIED,
                error="explicit workspace context is required",
            )

        content = invocation.input["content"]
        overwrite = invocation.input.get("overwrite", False)
        temporary_path = None
        try:
            target, relative_path, parts = workspace.resolve_target(
                invocation.input["relative_path"]
            )
            workspace.create_parent_directories(parts)
            target = workspace.verify_target(parts)
            if target.exists():
                if not target.is_file():
                    return ToolResult(
                        invocation.invocation_id,
                        ToolStatus.DENIED,
                        error="target is not a regular file",
                    )
                if not overwrite:
                    return ToolResult(
                        invocation.invocation_id,
                        ToolStatus.DENIED,
                        error="target already exists; set overwrite=true to replace it",
                        metadata={"relative_path": relative_path},
                    )

            content_bytes = content.encode("utf-8")
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".forge-ai-write-",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary_path = temporary.name
                temporary.write(content_bytes)
                temporary.flush()
                os.fsync(temporary.fileno())

            # Recheck the whole path immediately before the atomic target change.
            target = workspace.verify_target(parts)
            if overwrite:
                os.replace(temporary_path, target)
                temporary_path = None
            else:
                try:
                    # Same-directory hard-link creation is atomic and will not
                    # replace a file created after the initial existence check.
                    os.link(temporary_path, target)
                except FileExistsError:
                    return ToolResult(
                        invocation.invocation_id,
                        ToolStatus.DENIED,
                        error="target already exists; set overwrite=true to replace it",
                        metadata={"relative_path": relative_path},
                    )
                os.unlink(temporary_path)
                temporary_path = None

            return ToolResult(
                invocation.invocation_id,
                ToolStatus.COMPLETED,
                output={
                    "relative_path": relative_path,
                    "bytes_written": len(content_bytes),
                    "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
                },
                metadata={
                    "relative_path": relative_path,
                    "bytes_written": len(content_bytes),
                    "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
                },
            )
        except WorkspacePathError:
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.DENIED,
                error="target is outside the workspace or uses a filesystem link",
            )
        except (OSError, UnicodeError, ValueError):
            return ToolResult(
                invocation.invocation_id,
                ToolStatus.FAILED,
                error="workspace write failed",
            )
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
