"""Task dispatch with category policy and explicit provider override."""

from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.executor import TaskExecutor
from app.orchestrator.models import Task, TaskResult
from typing import Optional


class Orchestrator:
    """Dispatch tasks by simple category policy or explicit caller selection."""

    def __init__(
        self,
        registry: AgentRegistry,
        default_provider: str = "mock",
        fallback_chain: tuple[str, ...] = (),
        provider_registry: Optional[ProviderRegistry] = None,
        capabilities_registry: Optional[ProviderCapabilitiesRegistry] = None,
    ) -> None:
        self._executor = TaskExecutor(registry)
        self._dispatcher = Dispatcher(
            registry,
            default_provider=default_provider,
            fallback_chain=fallback_chain,
            provider_registry=provider_registry,
            capabilities_registry=capabilities_registry,
        )

    def dispatch(
        self,
        task: Task,
        agent_name: Optional[str] = None,
        *,
        provider_name: Optional[str] = None,
    ) -> TaskResult:
        """Dispatch automatically, or use a legacy agent/provider override."""
        if agent_name is not None and provider_name is not None:
            self._executor.validate_task(task)
            return TaskResult(
                task_id=task.id,
                success=False,
                error="Specify either agent_name or provider_name, not both",
            )
        if agent_name is not None:
            return self._executor.execute(task, agent_name)
        return self._dispatcher.dispatch(task, provider_name=provider_name)
