"""Small provider-neutral data contracts for read-only tool execution."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class ToolInvocation:
    tool_id: str
    input: dict[str, Any] = field(default_factory=dict)
    invocation_id: str = field(default_factory=lambda: str(uuid4()))


class ToolStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"


@dataclass(frozen=True)
class ToolResult:
    invocation_id: str
    status: ToolStatus
    output: Any = None
    error: str | None = None
