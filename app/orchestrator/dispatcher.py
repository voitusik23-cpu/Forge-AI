"""Simple, provider-neutral task routing for Orchestrator v0.2."""

import time
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional, Sequence, Tuple

from app.agents.base import AgentExecutionError
from app.agents.providers.base import ProviderNotConfiguredError
from app.agents.providers.capabilities import (
    CostTier,
    ProviderCapabilitiesRegistry,
)
from app.agents.providers.registry import ProviderNotFoundError, ProviderRegistry
from app.agents.registry import AgentNotFoundError, AgentRegistry
from app.orchestrator.executor import TaskExecutor
from app.orchestrator.classification import classify_task
from app.orchestrator.models import EventType, Task, TaskCategory, TaskResult


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
        TaskCategory.CODE: ("openrouter", "deepseek", "openai", "anthropic", "groq"),
        TaskCategory.ANALYSIS: (
            "openrouter", "deepseek", "anthropic", "openai", "google", "xai"
        ),
        TaskCategory.REVIEW: (
            "openrouter", "anthropic", "openai", "google", "xai"
        ),
        TaskCategory.CODING: ("openai", "anthropic"),
        TaskCategory.REASONING: ("anthropic", "openai"),
        TaskCategory.LARGE_CONTEXT: ("google",),
        TaskCategory.CHEAP_FREE: ("openrouter",),
        TaskCategory.FAST_CHEAP: ("deepseek",),
    }
    _SPECIALIZED_CATEGORIES = {
        TaskCategory.CODE,
        TaskCategory.ANALYSIS,
        TaskCategory.REVIEW,
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
        if task.category in self._SPECIALIZED_CATEGORIES and capabilities is not None:
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
                and task.category.value in capability.task_categories
                and (allow_paid_providers or capability.cost_tier != CostTier.PAID)
            ]
            eligible.sort(key=lambda item: cost_order[item.cost_tier])
            candidates = tuple(item.provider_name for item in eligible)
            selected = candidates[0] if candidates else None
            return DispatchDecision(
                category=task.category,
                candidates=candidates,
                selected_provider=selected,
                reason=(
                    f"Category '{task.category.value}' uses provider capability "
                    "and cost-tier metadata"
                ),
            )

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

    _SPECIALIZED_CATEGORIES = DispatchPolicy._SPECIALIZED_CATEGORIES

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

    def dispatch(
        self,
        task: Task,
        provider_name: Optional[str] = None,
        *,
        observer: Optional[Callable[[EventType, Dict[str, object]], None]] = None,
    ) -> TaskResult:
        """Route to the primary and optional fallbacks unless provider is explicit."""
        self._executor.validate_task(task)
        task = replace(task, category=classify_task(task))
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
        self._emit(
            observer,
            EventType.PROVIDER_SELECTED,
            provider=selected,
            category=task.category.value,
            reason=("explicit provider selection" if provider_name is not None else decision.reason),
        )
        allow_fallback = provider_name is None
        cost_aware = (
            task.category == TaskCategory.OTHER
            or task.category in self._SPECIALIZED_CATEGORIES
        ) and self._capabilities_registry is not None
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
            and (
                task.category == TaskCategory.OTHER
                or task.category in self._SPECIALIZED_CATEGORIES
            )
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
        previous_name = None
        for attempt_number, name in enumerate(attempts, start=1):
            if previous_name is not None:
                self._emit(
                    observer,
                    EventType.FALLBACK,
                    source=previous_name,
                    target=name,
                    attempt=attempt_number,
                )
            attempt_started = time.perf_counter()
            model_name = None
            if self._provider_registry is not None:
                try:
                    model_name = self._provider_registry.get(name).model_name
                except ProviderNotFoundError:
                    pass
            self._emit(
                observer,
                EventType.PROVIDER_ATTEMPT,
                provider=name,
                model=model_name,
                attempt=attempt_number,
            )
            if provider_name is None:
                unavailable_reason = self._automatic_route_rejection(name, task)
                if unavailable_reason is not None:
                    failures.append(f"{name}: {unavailable_reason}")
                    self._emit_provider_result(
                        observer, name, model_name, attempt_number,
                        attempt_started, False, unavailable_reason,
                    )
                    previous_name = name
                    continue
            if self._provider_registry is not None:
                try:
                    provider = self._provider_registry.get(name)
                except ProviderNotFoundError:
                    reason = "provider is unavailable or not registered"
                    failures.append(f"{name}: {reason}")
                    self._emit_provider_result(
                        observer, name, model_name, attempt_number,
                        attempt_started, False, reason,
                    )
                    previous_name = name
                    continue
                if self._capabilities_registry is not None:
                    try:
                        capability = self._capabilities_registry.get(name)
                    except LookupError:
                        reason = "provider capabilities are not registered"
                        failures.append(f"{name}: {reason}")
                        self._emit_provider_result(
                            observer, name, model_name, attempt_number,
                            attempt_started, False, reason,
                        )
                        previous_name = name
                        continue
                    if capability.provider_name != provider.provider_name:
                        reason = "provider capability metadata does not match"
                        failures.append(f"{name}: {reason}")
                        self._emit_provider_result(
                            observer, name, model_name, attempt_number,
                            attempt_started, False, reason,
                        )
                        previous_name = name
                        continue
            try:
                self._registry.get(name)
            except AgentNotFoundError:
                reason = "provider is unavailable or not registered"
                failures.append(f"{name}: {reason}")
                self._emit_provider_result(
                    observer, name, model_name, attempt_number,
                    attempt_started, False, reason,
                )
                previous_name = name
                continue

            try:
                result = self._executor.execute(
                    task, name, raise_execution_errors=True
                )
            except (AgentExecutionError, ProviderNotConfiguredError) as exc:
                reason = str(exc) or "provider execution failed"
                failures.append(f"{name}: {reason}")
                self._emit_provider_result(
                    observer, name, model_name, attempt_number,
                    attempt_started, False, reason,
                )
                previous_name = name
                continue
            except Exception as exc:
                # Preserve the dispatch exception while recording a non-secret
                # failure marker for the Run observer.
                reason = f"Execution raised {type(exc).__name__}"
                self._emit_provider_result(
                    observer, name, model_name, attempt_number,
                    attempt_started, False, reason,
                )
                raise

            if result.success:
                self._emit_provider_result(
                    observer,
                    name,
                    result.model_name or model_name,
                    attempt_number,
                    attempt_started,
                    True,
                    None,
                    result,
                )
                return result
            reason = result.error or "provider returned failure"
            failures.append(f"{name}: {reason}")
            self._emit_provider_result(
                observer,
                name,
                result.model_name or model_name,
                attempt_number,
                attempt_started,
                False,
                reason,
                result,
            )
            previous_name = name

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

    @staticmethod
    def _emit(
        observer: Optional[Callable[[EventType, Dict[str, object]], None]],
        event_type: EventType,
        **data: object,
    ) -> None:
        if observer is not None:
            observer(event_type, data)

    @classmethod
    def _emit_provider_result(
        cls,
        observer: Optional[Callable[[EventType, Dict[str, object]], None]],
        provider: str,
        model: Optional[str],
        attempt: int,
        started: float,
        success: bool,
        error: Optional[str],
        result: Optional[TaskResult] = None,
    ) -> None:
        data: Dict[str, object] = {
            "provider": provider,
            "model": model,
            "attempt": attempt,
            "duration_seconds": max(0.0, time.perf_counter() - started),
            "success": success,
        }
        if result is not None and result.usage is not None:
            data["usage"] = {
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "estimated_cost": result.usage.estimated_cost,
            }
        if error is not None:
            data["error"] = error
        cls._emit(observer, EventType.PROVIDER_RESULT, **data)

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
