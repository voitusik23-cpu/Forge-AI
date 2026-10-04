"""Small provider-neutral models used by the orchestrator."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from app.artifacts import Artifact, ChangeSet
from app.snapshots import ProjectSnapshot
from app.usage import Usage
from app.tools.contracts import ToolInvocation, ToolResult


class TaskPriority(Enum):
    """Relative priority assigned to a task by its caller."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskCategory(str, Enum):
    """Task intent for deterministic routing, plus legacy route categories."""

    CODE = "code"
    ANALYSIS = "analysis"
    REVIEW = "review"
    # Legacy categories remain supported for callers already using v0.2.
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
    category: Optional[TaskCategory] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    _category_was_explicit: bool = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Keep the historical OTHER value while tracking caller intent."""
        self._category_was_explicit = self.category is not None
        if self.category is None:
            self.category = TaskCategory.OTHER

    @property
    def category_was_explicit(self) -> bool:
        """Whether the caller supplied a category instead of requesting inference."""
        return self._category_was_explicit


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
    model_name: Optional[str] = None
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


class RunState(str, Enum):
    """Lifecycle state for one observed task execution."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    REVISING = "REVISING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EventType(str, Enum):
    """Small event vocabulary for reconstructing a Run's execution path."""

    RUN_STARTED = "run_started"
    CONTEXT_ASSEMBLED = "context_assembled"
    PROVIDER_SELECTED = "provider_selected"
    PROVIDER_ATTEMPT = "provider_attempt"
    PROVIDER_RESULT = "provider_result"
    FALLBACK = "fallback"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    TOOL_INVOCATION_REQUESTED = "tool_invocation_requested"
    TOOL_EXECUTION_STARTED = "tool_execution_started"
    TOOL_EXECUTION_COMPLETED = "tool_execution_completed"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    PERMISSION_CHECKED = "permission_checked"
    TOOL_INVOCATION_DENIED = "tool_invocation_denied"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    VERIFICATION_REQUESTED = "verification_requested"
    VERIFICATION_COMPLETED = "verification_completed"
    ACCEPTANCE_COMPLETED = "acceptance_completed"
    REVISION_STARTED = "revision_started"
    REVISION_COMPLETED = "revision_completed"
    CHANGESET_CREATED = "changeset_created"
    SNAPSHOT_CREATED = "snapshot_created"
    ENGINEERING_RUN_STARTED = "engineering_run_started"
    ENGINEERING_RUN_COMPLETED = "engineering_run_completed"
    EXECUTION_REQUESTED = "execution_requested"
    EXECUTION_POLICY_CHECKED = "execution_policy_checked"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_DENIED = "execution_denied"


@dataclass(frozen=True)
class RunError:
    """Structured, provider-neutral failure information."""

    error_type: str
    message: str


@dataclass
class Event:
    """One timestamped observation; it never contains model reasoning."""

    run_id: str
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Run:
    """In-memory execution record around an existing Task dispatch."""

    task: Task
    id: str = field(default_factory=lambda: str(uuid4()))
    state: RunState = RunState.CREATED
    events: list[Event] = field(default_factory=list)
    result: Optional[TaskResult] = None
    error: Optional[RunError] = None
    change_sets: list[ChangeSet] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    project_snapshots: list[ProjectSnapshot] = field(default_factory=list)
