"""Bounded projection of a tool result for run history and events.

A tool may return arbitrary output. The run's durable history and its event trail
must not carry that output raw: it can contain file contents, credentials, or
unbounded text. This module is the single place that converts a ``ToolResult``
into a bounded, sanitized record.

Nothing here inspects or executes anything; it only reduces a result the existing
``ToolExecutor`` already produced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Mapping

from app.tools.contracts import ToolStatus


class BoundedResultError(ValueError):
    """Raised when a result cannot be projected into a trusted bounded form."""


#: Hard ceilings. They are module constants, never parameters, so a caller cannot
#: widen the bound for convenience.
MAX_PREVIEW_CHARS = 512
MAX_PREVIEW_BYTES = 2048
MAX_METADATA_ITEMS = 8
MAX_METADATA_STRING = 256
MAX_ERROR_CHARS = 200

#: The only result metadata keys that may reach history. They are structural
#: facts about the operation, never file content.
_SAFE_METADATA_KEYS = frozenset(
    {"relative_path", "bytes_written", "content_sha256", "line_count", "encoding"}
)


@dataclass(frozen=True)
class BoundedToolResult:
    """Immutable, bounded, authority-free record of one tool invocation.

    It deliberately has no field for a command, argv, executable, environment,
    workspace, credentials, or raw stdout/stderr, so a bounded result can never
    become an input to execution authorization.
    """

    invocation_id: str
    tool_id: str
    status: ToolStatus
    error: str = ""
    output_sha256: str = ""
    output_bytes: int = 0
    preview: str = ""
    truncated: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, str) or not self.invocation_id.strip():
            raise BoundedResultError("invocation_id must be a non-empty string")
        if not isinstance(self.tool_id, str) or not self.tool_id.strip():
            raise BoundedResultError("tool_id must be a non-empty string")
        if not isinstance(self.status, ToolStatus):
            raise BoundedResultError("status must be a ToolStatus")
        if not isinstance(self.output_bytes, int) or isinstance(self.output_bytes, bool):
            raise BoundedResultError("output_bytes must be an integer")
        if self.output_bytes < 0:
            raise BoundedResultError("output_bytes must not be negative")
        if len(self.preview) > MAX_PREVIEW_CHARS:
            raise BoundedResultError("preview exceeds the bounded maximum")
        if len(self.error) > MAX_ERROR_CHARS:
            raise BoundedResultError("error exceeds the bounded maximum")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def succeeded(self) -> bool:
        return self.status == ToolStatus.COMPLETED

    @property
    def denied(self) -> bool:
        return self.status in (ToolStatus.DENIED, ToolStatus.WAITING_FOR_APPROVAL)

    def bounded_summary(self) -> dict[str, object]:
        """JSON-safe, sanitized projection for events and run history."""
        return {
            "invocation_id": self.invocation_id,
            "tool_id": self.tool_id,
            "status": self.status.value,
            "output_bytes": self.output_bytes,
            "output_sha256": self.output_sha256,
            "truncated": self.truncated,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


def bound_tool_result(
    result: object,
    *,
    tool_id: str = "",
) -> BoundedToolResult:
    """Project a ``ToolResult`` into a bounded record.

    Never raises for a malformed result: an unusable result becomes an explicit
    failure record rather than an exception that could be mistaken for a crash.
    """
    invocation_id = getattr(result, "invocation_id", "")
    resolved_tool_id = tool_id or getattr(result, "tool_id", "")
    status = getattr(result, "status", None)
    if not isinstance(status, ToolStatus):
        return BoundedToolResult(
            invocation_id=str(invocation_id or "unknown"),
            tool_id=str(resolved_tool_id or "unknown"),
            status=ToolStatus.FAILED,
            error="malformed tool result",
        )

    raw_error = getattr(result, "error", None)
    error = raw_error[:MAX_ERROR_CHARS] if isinstance(raw_error, str) else ""

    output = getattr(result, "output", None)
    digest, size, preview, truncated = _bounded_output(output)

    return BoundedToolResult(
        invocation_id=str(invocation_id or "unknown"),
        tool_id=str(resolved_tool_id or "unknown"),
        status=status,
        error=error,
        output_sha256=digest,
        output_bytes=size,
        preview=preview,
        truncated=truncated,
        metadata=_safe_metadata(getattr(result, "metadata", None)),
    )


def _bounded_output(output: object) -> tuple[str, int, str, bool]:
    """Return digest, byte size, a capped preview, and whether it was truncated."""
    if output is None:
        return "", 0, "", False
    if isinstance(output, bytes):
        payload = output
        text = ""
    elif isinstance(output, str):
        payload = output.encode("utf-8")
        text = output
    elif isinstance(output, (dict, list, tuple)):
        # Structured output: hash the canonical form, preview only its shape.
        try:
            encoded = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            encoded = str(output)
        payload = encoded.encode("utf-8")
        text = ""
    else:
        text = str(output)
        payload = text.encode("utf-8")

    digest = sha256(payload).hexdigest()
    size = len(payload)
    if text:
        preview = text[:MAX_PREVIEW_CHARS]
        truncated = len(text) > MAX_PREVIEW_CHARS or size > MAX_PREVIEW_BYTES
    else:
        preview = ""
        truncated = True
    return digest, size, preview, truncated


def _safe_metadata(metadata: object) -> dict[str, object]:
    """Keep only structural metadata keys with bounded scalar values."""
    if not isinstance(metadata, dict):
        return {}
    safe: dict[str, object] = {}
    for key in sorted(metadata):
        if len(safe) >= MAX_METADATA_ITEMS:
            break
        if not isinstance(key, str) or key not in _SAFE_METADATA_KEYS:
            continue
        value = metadata[key]
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if value >= 0:
                safe[key] = value
        elif isinstance(value, str):
            safe[key] = value[:MAX_METADATA_STRING]
    return safe
