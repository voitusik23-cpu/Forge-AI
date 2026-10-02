"""Simple, provider-neutral task routing for Orchestrator v0.2."""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.agents.registry import AgentNotFoundError, AgentRegistry
from app.orchestrator.executor import TaskExecutor
from app.orchestrator.models import Task, TaskCategory, TaskResult


@dataclass(frozen=True)
class DispatchDecision:
    """A routing choice and ordered candidates retained for future policies."""

    category: TaskCategory
    candidates: Tuple[str, ...]
    selected_provider: str
    reason: str


class DispatchPolicy:
    """Basic ordered provider preference policy; it performs no scoring/fallback."""

    _PREFERENCES: Dict[TaskCategory, Tuple[str, ...]] = {
        TaskCategory.CODING: ("openai", "anthropic"),
        TaskCategory.REASONING: ("anthropic", "openai"),
        TaskCategory.LARGE_CONTEXT: ("google",),
        TaskCategory.CHEAP_FREE: ("openrouter",),
        TaskCategory.FAST_CHEAP: ("deepseek",),
    }

    def decide(self, task: Task, default_provider: str) -> DispatchDecision:
        """Return the preferred provider and retain the remaining candidates."""
        candidates = self._PREFERENCES.get(task.category, (default_provider,))
        return DispatchDecision(
            category=task.category,
            candidates=candidates,
            selected_provider=candidates[0],
            reason=(
                f"Policy for '{task.category.value}' prefers '{candidates[0]}'"
                if task.category in self._PREFERENCES
                else f"Category '{task.category.value}' uses configured default"
            ),
        )


class Dispatcher:
    """Choose one registered provider agent and delegate execution."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        default_provider: str = "mock",
        policy: Optional[DispatchPolicy] = None,
    ) -> None:
        self._registry = agent_registry
        self._default_provider = default_provider
        self._policy = policy or DispatchPolicy()
        self._executor = TaskExecutor(agent_registry)

    def dispatch(self, task: Task, provider_name: Optional[str] = None) -> TaskResult:
        """Route to the policy choice or explicit provider, without fallback."""
        self._executor.validate_task(task)
        decision = self._policy.decide(task, self._default_provider)
        if provider_name is not None and (
            not isinstance(provider_name, str) or not provider_name.strip()
        ):
            return TaskResult(
                task_id=task.id,
                success=False,
                error="provider_name must not be empty",
            )
        selected = provider_name if provider_name is not None else decision.selected_provider
        try:
            self._registry.get(selected)
        except AgentNotFoundError:
            return TaskResult(
                task_id=task.id,
                success=False,
                error=f"Selected provider '{selected}' is unavailable or not registered",
                provider=selected,
                agent=selected,
            )
        return self._executor.execute(task, selected)
