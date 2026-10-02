"""Generic adapter that lets a provider satisfy the existing Agent interface."""

from app.agents.base import Agent
from app.agents.providers.base import Provider, ProviderRequest
from app.orchestrator.models import Task, TaskResult


class ProviderAgent:
    """Adapt an explicitly supplied provider to ``Agent.run(Task)``."""

    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        """Use the provider's canonical name for AgentRegistry lookup."""
        return self._provider.provider_name

    def run(self, task: Task) -> TaskResult:
        """Convert a task into a provider request and adapt its response."""
        response = self._provider.generate(
            ProviderRequest(prompt=task.description, context=task.context)
        )
        return TaskResult(
            task_id=task.id,
            success=True,
            output=response.output,
        )
