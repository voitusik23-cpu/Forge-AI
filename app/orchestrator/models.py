"""Small provider-neutral models used by the orchestrator."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class TaskPriority(Enum):
    """Relative priority assigned to a task by its caller."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Task:
    """A unit of work submitted to a specifically selected agent."""

    id: str
    description: str
    context: Dict[str, Any] = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL


@dataclass
class Usage:
    """Token and optional estimated-cost information for a result."""

    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Optional[float] = None


@dataclass
class TaskResult:
    """Outcome returned by an agent after processing a task."""

    task_id: str
    success: bool
    output: str = ""
    error: Optional[str] = None
    usage: Optional[Usage] = None
