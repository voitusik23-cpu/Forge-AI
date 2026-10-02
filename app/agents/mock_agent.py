"""Local mock agent for development and tests."""

from app.orchestrator.models import Task, TaskResult, Usage


class MockAgent:
    """Predictable agent implementation that never calls external services."""

    name = "mock"

    def run(self, task: Task) -> TaskResult:
        """Return a successful result identifying the mock implementation."""
        return TaskResult(
            task_id=task.id,
            success=True,
            output=f"MockAgent processed task: {task.description}",
            usage=Usage(),
        )
