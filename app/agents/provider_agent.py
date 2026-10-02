"""Generic adapter that lets a provider satisfy the existing Agent interface."""

from app.agents.base import AgentExecutionError
from app.agents.providers.base import (
    Provider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from app.orchestrator.models import Task, TaskResult


class ProviderAgent:
    """Adapt an explicitly supplied provider to ``Agent.run(Task)``."""

    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        """Use the provider's canonical name for AgentRegistry lookup."""
        return self.provider_name

    @property
    def provider_name(self) -> str:
        """Expose provider identity for execution result metadata."""
        return self._provider.provider_name

    def run(self, task: Task) -> TaskResult:
        """Convert a task into a provider request and adapt its response."""
        try:
            response = self._provider.generate(
                ProviderRequest(
                    prompt=task.description,
                    context=task.context,
                    model_name=task.parameters.get("model"),
                )
            )
        except ProviderError as exc:
            raise AgentExecutionError(
                f"Provider '{self.provider_name}' failed: {exc}"
            ) from exc
        if not isinstance(response, ProviderResponse):
            raise AgentExecutionError("Provider returned an invalid response")
        if not isinstance(response.output, str):
            raise AgentExecutionError("Provider returned a non-text result")
        return TaskResult(
            task_id=task.id,
            success=True,
            output=response.output,
            usage=response.usage,
            provider=response.provider_name,
            agent=self.name,
            model_name=response.model_name,
        )
