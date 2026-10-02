"""Provider-neutral agent interface."""

from typing import Protocol

from app.orchestrator.models import Task, TaskResult


class Agent(Protocol):
    """An agent capable of processing a task and returning its result."""

    name: str

    def run(self, task: Task) -> TaskResult:
        """Process a task and return its outcome."""
