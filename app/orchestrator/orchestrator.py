"""Task dispatch through an explicitly selected registered agent."""

from app.agents.registry import AgentRegistry
from app.orchestrator.executor import TaskExecutor
from app.orchestrator.models import Task, TaskResult


class Orchestrator:
    """Dispatch tasks without making an automatic agent-selection decision."""

    def __init__(self, registry: AgentRegistry) -> None:
        self._executor = TaskExecutor(registry)

    def dispatch(self, task: Task, agent_name: str) -> TaskResult:
        """Send a task to the named agent and return its result."""
        return self._executor.execute(task, agent_name)
