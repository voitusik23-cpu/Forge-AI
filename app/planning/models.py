"""Provider-neutral project plan models."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List

from app.orchestrator.models import TaskCategory


class PlannedTaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    COMPLETED = "completed"
    FAILED = "failed"


class ProjectPlanStatus(str, Enum):
    PLANNED = "planned"


@dataclass
class PlannedTask:
    task_id: str
    title: str
    description: str
    category: TaskCategory
    dependencies: List[str] = field(default_factory=list)
    status: PlannedTaskStatus = PlannedTaskStatus.PENDING


@dataclass
class ProjectPlan:
    plan_id: str
    title: str
    goal: str
    tasks: List[PlannedTask]
    status: ProjectPlanStatus = ProjectPlanStatus.PLANNED
