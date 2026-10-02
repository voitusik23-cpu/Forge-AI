"""Synchronous execution boundary for explicitly selected agents."""

from dataclasses import replace
from typing import Optional

from app.agents.base import AgentExecutionError
from app.agents.providers.base import ProviderNotConfiguredError
from app.agents.registry import AgentRegistry
from app.orchestrator.models import Task, TaskCategory, TaskPriority, TaskResult
from app.usage import Usage


class InvalidTaskError(ValueError):
    """Raised when a task does not satisfy the execution contract."""


class TaskExecutor:
    """Validate tasks, invoke one named agent, and normalize its result."""

    def __init__(self, agent_registry: AgentRegistry) -> None:
        self._agent_registry = agent_registry

    def execute(
        self,
        task: Task,
        agent_name: str,
        *,
        raise_execution_errors: bool = False,
    ) -> TaskResult:
        """Run a task through one named agent, optionally propagating run errors."""
        self.validate_task(task)
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise ValueError("agent_name must not be empty")

        agent = self._agent_registry.get(agent_name)
        provider_name = getattr(agent, "provider_name", None)
        try:
            result = agent.run(task)
        except (AgentExecutionError, ProviderNotConfiguredError) as exc:
            if raise_execution_errors:
                raise
            return TaskResult(
                task_id=task.id,
                success=False,
                error=str(exc) or "Agent execution failed",
                provider=provider_name,
                agent=agent_name,
            )

        if not isinstance(result, TaskResult):
            return self._failure(task, agent_name, "Agent returned an invalid result")
        if result.task_id != task.id:
            return self._failure(
                task, agent_name, "Agent returned a result for a different task",
                provider=provider_name,
            )
        if not isinstance(result.success, bool):
            return self._failure(task, agent_name, "Agent returned an invalid success flag")
        if not isinstance(result.output, str):
            return self._failure(task, agent_name, "Agent returned a non-text result")
        if result.usage is not None and not isinstance(result.usage, Usage):
            return self._failure(task, agent_name, "Agent returned invalid usage metadata")
        if result.success and not result.output.strip():
            return replace(
                result,
                success=False,
                error="Agent returned an empty result",
                provider=result.provider or provider_name,
                agent=result.agent or agent_name,
            )
        if not result.success and not result.error:
            result = replace(result, error="Agent reported task failure")
        return replace(
            result,
            provider=result.provider or provider_name,
            agent=result.agent or agent_name,
        )

    @staticmethod
    def validate_task(task: Task) -> None:
        if not isinstance(task, Task):
            raise InvalidTaskError("task must be a Task instance")
        if not isinstance(task.id, str) or not task.id.strip():
            raise InvalidTaskError("task id must not be empty")
        if not isinstance(task.description, str) or not task.description.strip():
            raise InvalidTaskError("task description must not be empty")
        if not isinstance(task.context, dict):
            raise InvalidTaskError("task context must be a dictionary")
        if not isinstance(task.priority, TaskPriority):
            raise InvalidTaskError("task priority must be a TaskPriority")
        if not isinstance(task.category, TaskCategory):
            raise InvalidTaskError("task category must be a TaskCategory")
        if not isinstance(task.parameters, dict):
            raise InvalidTaskError("task parameters must be a dictionary")
        model_name = task.parameters.get("model")
        if model_name is not None and (
            not isinstance(model_name, str) or not model_name.strip()
        ):
            raise InvalidTaskError("task model parameter must be a non-empty string")

    @staticmethod
    def _failure(
        task: Task,
        agent_name: str,
        message: str,
        provider: Optional[str] = None,
    ) -> TaskResult:
        return TaskResult(
            task_id=task.id,
            success=False,
            error=message,
            provider=provider,
            agent=agent_name,
        )
