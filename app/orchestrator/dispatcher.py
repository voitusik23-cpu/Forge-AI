"""Simple, provider-neutral task routing for Orchestrator v0.2."""

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from app.agents.base import AgentExecutionError
from app.agents.providers.base import ProviderNotConfiguredError
from app.agents.providers.capabilities import (
    CostTier,
    ProviderCapabilitiesRegistry,
)
from app.agents.providers.registry import ProviderNotFoundError, ProviderRegistry
from app.agents.registry import AgentNotFoundError, AgentRegistry
from app.orchestrator.executor import TaskExecutor
from app.orchestrator.models import Task, TaskCategory, TaskResult


@dataclass(frozen=True)
class DispatchDecision:
    """A routing choice and ordered candidates retained for future policies."""

    category: TaskCategory
    candidates: Tuple[str, ...]
    selected_provider: Optional[str]
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

    def decide(
        self,
        task: Task,
        default_provider: str,
        *,
        capabilities: Optional[ProviderCapabilitiesRegistry] = None,
        registered_provider_names: Optional[Sequence[str]] = None,
        registered_agent_names: Optional[Sequence[str]] = None,
        allow_paid_providers: bool = False,
    ) -> DispatchDecision:
        """Return the preferred provider and retain the remaining candidates."""
        if task.category == TaskCategory.OTHER and capabilities is not None:
            provider_names = set(registered_provider_names or ())
            agent_names = set(registered_agent_names or ())
            cost_order = {
                CostTier.FREE: 0,
                CostTier.CHEAP: 1,
                CostTier.PAID: 2,
            }
            eligible = [
                capability
                for capability in capabilities.list_capabilities()
                if capability.provider_name in provider_names
                and capability.provider_name in agent_names
                and (allow_paid_providers or capability.cost_tier != CostTier.PAID)
            ]
            eligible.sort(key=lambda item: cost_order[item.cost_tier])
            candidates = tuple(item.provider_name for item in eligible)
            selected = candidates[0] if candidates else None
            return DispatchDecision(
                category=task.category,
                candidates=candidates,
                selected_provider=selected,
                reason="Cost tier preference: free, then cheap, then permitted paid",
            )

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
        allow_paid_providers: bool = False,
    ) -> None:
        self._registry = agent_registry
        self._default_provider = default_provider
        self._policy = policy or DispatchPolicy()
        self._executor = TaskExecutor(agent_registry)
        self._provider_registry = provider_registry
        self._capabilities_registry = capabilities_registry
        self._allow_paid_providers = allow_paid_providers
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
        decision = self._policy.decide(
            task,
            self._default_provider,
            capabilities=self._capabilities_registry,
            registered_provider_names=(
                self._provider_registry.list_providers()
                if self._provider_registry is not None
                else None
            ),
            registered_agent_names=self._registry.list_agents(),
            allow_paid_providers=self._allow_paid_providers,
        )
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
        cost_aware = (
            task.category == TaskCategory.OTHER
            and self._capabilities_registry is not None
        )
        if provider_name is not None:
            attempts = [selected]
        elif cost_aware:
            attempts = list(decision.candidates)
        else:
            attempts = [selected] if selected is not None else []
        if allow_fallback:
            if not cost_aware:
                if not attempts:
                    attempts.append(self._default_provider)
                attempts.extend(
                    name
                    for name in self._fallback_chain
                    if name != selected and name not in attempts
                )

        if (
            allow_fallback
            and task.category == TaskCategory.OTHER
            and self._capabilities_registry is not None
        ):
            permitted_fallbacks = []
            for name in self._fallback_chain:
                if name in attempts:
                    continue
                try:
                    fallback_capability = self._capabilities_registry.get(name)
                except LookupError:
                    if (
                        self._provider_registry is not None
                        and name not in self._provider_registry.list_providers()
                    ):
                        permitted_fallbacks.append(name)
                    continue
                if (
                    fallback_capability.cost_tier == CostTier.PAID
                    and not self._allow_paid_providers
                ):
                    continue
                permitted_fallbacks.append(name)
            attempts.extend(permitted_fallbacks)

        failures = []
        for name in attempts:
            if provider_name is None:
                unavailable_reason = self._automatic_route_rejection(name, task)
                if unavailable_reason is not None:
                    failures.append(f"{name}: {unavailable_reason}")
                    continue
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

        error = (
            "No provider candidates are permitted by the cost policy"
            if not attempts
            else "Provider fallback chain exhausted: " + "; ".join(failures)
        )
        return TaskResult(
            task_id=task.id,
            success=False,
            error=error,
            provider=selected,
            agent=selected,
        )

    def _automatic_route_rejection(self, name: str, task: Task) -> Optional[str]:
        """Reject auto-route candidates that are disabled, unconfigured, or incapable."""
        if self._provider_registry is None or self._capabilities_registry is None:
            return None
        try:
            provider = self._provider_registry.get(name)
        except ProviderNotFoundError:
            return "provider is unavailable or not registered"
        try:
            capability = self._capabilities_registry.get(name)
        except LookupError:
            return "provider capabilities are not registered"

        if not capability.enabled_by_config:
            return "provider is disabled by configuration"
        if capability.cost_tier == CostTier.PAID and not self._allow_paid_providers:
            return "paid provider is disabled by cost policy"
        if capability.api_key_env:
            try:
                key_available = provider.secret_store.get_secret(capability.api_key_env)
            except (OSError, RuntimeError):
                key_available = None
            if not key_available:
                return f"required key {capability.api_key_env} is not configured"

        requirements = {
            "requires_tools": capability.supports_tools,
            "requires_streaming": capability.supports_streaming,
            "requires_large_context": capability.supports_large_context,
        }
        if task.category == TaskCategory.LARGE_CONTEXT:
            requirements["requires_large_context"] = capability.supports_large_context
        for parameter, supported in requirements.items():
            if task.parameters.get(parameter) is True and not supported:
                return f"provider does not support {parameter.removeprefix('requires_')}"
        return None
