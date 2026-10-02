"""Simple, provider-neutral task routing for Orchestrator v0.2."""

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from app.agents.base import AgentExecutionError
from app.agents.providers.base import ProviderNotConfiguredError
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.providers.registry import ProviderNotFoundError, ProviderRegistry
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
    """Basic ordered provider preference policy without scoring."""

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
        fallback_chain: Sequence[str] = (),
        provider_registry: Optional[ProviderRegistry] = None,
        capabilities_registry: Optional[ProviderCapabilitiesRegistry] = None,
    ) -> None:
        self._registry = agent_registry
        self._default_provider = default_provider
        self._policy = policy or DispatchPolicy()
        self._executor = TaskExecutor(agent_registry)
        self._provider_registry = provider_registry
        self._capabilities_registry = capabilities_registry
        if isinstance(fallback_chain, str):
            fallback_chain = tuple(
                name.strip() for name in fallback_chain.split(",") if name.strip()
            )
        if any(not isinstance(name, str) or not name.strip() for name in fallback_chain):
            raise ValueError("fallback_chain must contain non-empty provider names")
        self._fallback_chain = tuple(dict.fromkeys(name.strip() for name in fallback_chain))

    def dispatch(self, task: Task, provider_name: Optional[str] = None) -> TaskResult:
        """Route to the primary and optional fallbacks unless provider is explicit."""
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
        allow_fallback = provider_name is None
        attempts = [selected]
        if allow_fallback:
            attempts.extend(
                name
                for name in self._fallback_chain
                if name != selected and name not in attempts
            )

        failures = []
        for name in attempts:
            if self._provider_registry is not None:
                try:
                    provider = self._provider_registry.get(name)
                except ProviderNotFoundError:
                    failures.append(f"{name}: provider is unavailable or not registered")
                    continue
                if self._capabilities_registry is not None:
                    try:
                        capability = self._capabilities_registry.get(name)
                    except LookupError:
                        failures.append(f"{name}: provider capabilities are not registered")
                        continue
                    if capability.provider_name != provider.provider_name:
                        failures.append(f"{name}: provider capability metadata does not match")
                        continue
            try:
                self._registry.get(name)
            except AgentNotFoundError:
                failures.append(f"{name}: provider is unavailable or not registered")
                continue

            try:
                result = self._executor.execute(
                    task, name, raise_execution_errors=True
                )
            except (AgentExecutionError, ProviderNotConfiguredError) as exc:
                failures.append(f"{name}: {str(exc) or 'provider execution failed'}")
                continue

            if result.success:
                return result
            failures.append(f"{name}: {result.error or 'provider returned failure'}")

        error = "Provider fallback chain exhausted: " + "; ".join(failures)
        return TaskResult(
            task_id=task.id,
            success=False,
            error=error,
            provider=selected,
            agent=selected,
        )
