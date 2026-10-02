"""Small provider-neutral models used by the orchestrator."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from app.usage import Usage


class TaskPriority(Enum):
    """Relative priority assigned to a task by its caller."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskCategory(str, Enum):
    """Coarse routing category used by the v0.2 dispatcher policy."""

    CODING = "coding"
    REASONING = "reasoning"
    LARGE_CONTEXT = "large-context"
    CHEAP_FREE = "cheap/free"
    FAST_CHEAP = "fast/cheap"
    OTHER = "other"

    @classmethod
    def _missing_(cls, value: object):
        """Accept the common spelling variants used in task descriptions."""
        if isinstance(value, str):
            normalized = value.strip().lower().replace("_", "-")
            aliases = {
                "large context": cls.LARGE_CONTEXT,
                "large-context": cls.LARGE_CONTEXT,
                "cheap/free": cls.CHEAP_FREE,
                "cheap-free": cls.CHEAP_FREE,
                "fast/cheap": cls.FAST_CHEAP,
                "fast-cheap": cls.FAST_CHEAP,
            }
            return aliases.get(normalized)
        return None


@dataclass
class Task:
    """A unit of work with routing category and provider-neutral parameters."""

    id: str
    description: str
    context: Dict[str, Any] = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    category: TaskCategory = TaskCategory.OTHER
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Outcome returned by an agent after processing a task."""

    task_id: str
    success: bool
    output: str = ""
    error: Optional[str] = None
    usage: Optional[Usage] = None
    provider: Optional[str] = None
    agent: Optional[str] = None
