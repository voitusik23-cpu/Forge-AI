"""Simple, provider-neutral task routing for Orchestrator v0.2."""

import time
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional, Sequence, Tuple

from app.agents.base import AgentExecutionError
from app.agents.providers.base import ProviderError, ProviderNotConfiguredError
from app.agents.providers.capabilities import (
    CostTier,
    ProviderCapabilitiesRegistry,
)
from app.agents.providers.model_registry import ModelRegistry
from app.agents.providers.registry import ProviderNotFoundError, ProviderRegistry
from app.agents.registry import AgentNotFoundError, AgentRegistry
from app.fabric.adapters import UsageAccountingAdapter
from app.fabric.fabric import CapabilityFabric
from app.fabric.models import CapabilityRequirement, FabricRequest
from app.orchestrator.classification import classify_task
from app.orchestrator.executor import TaskExecutor
from app.orchestrator.models import EventType, Task, TaskCategory, TaskResult


@dataclass(frozen=True)
class DispatchDecision:
    """A routing choice and ordered candidates retained for future policies."""

    category: TaskCategory
    candidates: Tuple[str, ...]
    selected_provider: Optional[str]
    reason: str


def failover_eligible(exc: BaseException) -> bool:
    """Whether another provider may legitimately serve the request that failed.

    The chain advances for an *infrastructure* failure -- credential, quota, rate limit,
    transport -- and stops for a *request* failure: an invalid payload or a model id no
    provider knows is the caller's mistake, and repeating it on the next provider only
    spends that account on a request that cannot succeed.

    The answer is read from the ``ProviderError`` an agent wrapped at its boundary
    (``ProviderAgent`` raises ``AgentExecutionError`` ``from`` the provider's own error),
    so the class, not the message text, decides. A failure with no ``ProviderError`` in
    its cause chain -- a stub agent, an unexpected exception -- carries no such evidence
    and keeps the historical "advance" behaviour.
    """

    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ProviderError):
            return bool(getattr(current, "failover_eligible", True))
        current = current.__cause__ or current.__context__
    return True


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
        model_registry: Optional[ModelRegistry] = None,
        fabric: Optional[CapabilityFabric] = None,
        registered_provider_names: Optional[Sequence[str]] = None,
        registered_agent_names: Optional[Sequence[str]] = None,
        allow_paid_providers: bool = False,
    ) -> DispatchDecision:
        """Return the preferred provider and retain the remaining candidates."""
        provider_names = set(registered_provider_names or ())
        agent_names = set(registered_agent_names or ())

        requested_model = task.parameters.get("model") or task.parameters.get("model_override")
        min_ctx = task.parameters.get("min_context")
        min_ctx_val = int(min_ctx) if isinstance(min_ctx, (int, float)) and min_ctx > 0 else None
        raw_req_caps = task.parameters.get("required_capabilities")

        # 0. CapabilityFabric-based resolution (v0.3 unified coordinator path)
        if fabric is not None:
            req_caps = ()
            if isinstance(raw_req_caps, (list, tuple, set, frozenset)):
                req_caps = tuple(CapabilityRequirement.parse(c) for c in raw_req_caps)
            elif isinstance(raw_req_caps, (str, CapabilityRequirement)):
                req_caps = (CapabilityRequirement.parse(raw_req_caps),)

            fabric_req = FabricRequest(
                subject_agent="dispatcher",
                intent_description=task.description or "Task execution",
                required_capabilities=req_caps,
                task_category=task.category.value if task.category else None,
                explicit_model=str(requested_model) if requested_model and isinstance(requested_model, str) else None,
                explicit_provider=str(task.parameters.get("provider")) if task.parameters.get("provider") else None,
                context_tokens=min_ctx_val or 0,
                allow_paid_providers=allow_paid_providers,
            )
            resolution = fabric.resolve(fabric_req)

            if resolution.can_proceed and resolution.candidate_providers:
                ordered_providers = [
                    p for p in resolution.candidate_providers
                    if (not provider_names or p in provider_names)
                    and (not agent_names or p in agent_names)
                ]
                if ordered_providers:
                    return DispatchDecision(
                        category=task.category,
                        candidates=tuple(ordered_providers),
                        selected_provider=ordered_providers[0],
                        reason=(
                            f"Resolved {len(ordered_providers)} candidate providers "
                            f"via CapabilityFabric ({resolution.reason})"
                        ),
                    )

            if not resolution.is_capable or not resolution.is_authorized or resolution.rejection_reasons:
                if req_caps or requested_model or not resolution.is_authorized or not resolution.is_capable:
                    return DispatchDecision(
                        category=task.category,
                        candidates=(),
                        selected_provider=None,
                        reason=f"CapabilityFabric rejected request: {'; '.join(resolution.rejection_reasons) if resolution.rejection_reasons else resolution.reason}",
                    )

        # 1. ModelRegistry-based routing (v0.2 preferred path)
        if model_registry is not None:
            # Check for model override in task parameters
            if requested_model and isinstance(requested_model, str):
                try:
                    model_info = model_registry.get_model(requested_model)
                    if (
                        (not provider_names or model_info.provider_id in provider_names)
                        and (not agent_names or model_info.provider_id in agent_names)
                        and (allow_paid_providers or model_info.cost_tier != CostTier.PAID)
                    ):
                        return DispatchDecision(
                            category=task.category,
                            candidates=(model_info.provider_id,),
                            selected_provider=model_info.provider_id,
                            reason=f"Explicit model '{requested_model}' selected via ModelRegistry",
                        )
                except LookupError:
                    pass

            # Search by task category and capabilities
            eligible_models = model_registry.find_models(
                task_category=task.category.value if task.category in self._SPECIALIZED_CATEGORIES else None,
                capabilities=raw_req_caps if isinstance(raw_req_caps, (list, tuple, set, frozenset)) else None,
                min_context=min_ctx_val,
                max_cost_tier=None if allow_paid_providers else CostTier.CHEAP,
                allowed_providers=list(provider_names) if provider_names else None,
            )

            # Filter candidates where provider is also in agent_names
            ordered_providers: list[str] = []
            for m in eligible_models:
                if (not agent_names or m.provider_id in agent_names) and m.provider_id not in ordered_providers:
                    ordered_providers.append(m.provider_id)

            if ordered_providers:
                candidates_tuple = tuple(ordered_providers)
                return DispatchDecision(
                    category=task.category,
                    candidates=candidates_tuple,
                    selected_provider=candidates_tuple[0],
                    reason=(
                        f"Category '{task.category.value}' matched {len(eligible_models)} models "
                        "via ModelRegistry capability and cost-tier metadata"
                    ),
                )

        # 2. Legacy ProviderCapabilitiesRegistry routing
        if task.category in self._SPECIALIZED_CATEGORIES and capabilities is not None:
            cost_order = {
                CostTier.FREE: 0,
                CostTier.CHEAP: 1,
                CostTier.PAID: 2,
            }
            eligible = [
                capability
                for capability in capabilities.list_capabilities()
                if (not provider_names or capability.provider_name in provider_names)
                and (not agent_names or capability.provider_name in agent_names)
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
            cost_order = {
                CostTier.FREE: 0,
                CostTier.CHEAP: 1,
                CostTier.PAID: 2,
            }
            eligible = [
                capability
                for capability in capabilities.list_capabilities()
                if (not provider_names or capability.provider_name in provider_names)
                and (not agent_names or capability.provider_name in agent_names)
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

        # 3. Static Preferences Fallback
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
        model_registry: Optional[ModelRegistry] = None,
        fabric: Optional[CapabilityFabric] = None,
        allow_paid_providers: bool = False,
    ) -> None:
        self._registry = agent_registry
        self._default_provider = default_provider
        self._policy = policy or DispatchPolicy()
        self._executor = TaskExecutor(agent_registry)
        self._provider_registry = provider_registry
        self._capabilities_registry = capabilities_registry
        self._model_registry = model_registry
        self._fabric = fabric
        self._allow_paid_providers = allow_paid_providers
        if isinstance(fallback_chain, str):
            fallback_chain = tuple(
                name.strip() for name in fallback_chain.split(",") if name.strip()
            )
        if any(not isinstance(name, str) or not name.strip() for name in fallback_chain):
            raise ValueError("fallback_chain must contain non-empty provider names")
        self._fallback_chain = tuple(dict.fromkeys(name.strip() for name in fallback_chain))

    @property
    def fabric(self) -> Optional[CapabilityFabric]:
        """Return the bound CapabilityFabric instance if configured."""
        return self._fabric

    def dispatch(
        self,
        task: Task,
        provider_name: Optional[str] = None,
        *,
        observer: Optional[Callable[[EventType, Dict[str, object]], None]] = None,
        allow_fallback: Optional[bool] = None,
    ) -> TaskResult:
        """Route to the primary and optional fallbacks unless provider is explicit.

        ``allow_fallback`` decides whether the configured fallback chain may still serve
        when the named provider fails for an infrastructure reason. ``None`` keeps the
        historical rule -- an explicit name is pinned and only the automatic route uses
        the chain -- while ``True`` says the name is the *primary* (the API layer resolves
        the deployment's configured default into a name, so pinning it meant a 402 on the
        default ended the run with ``openrouter`` never tried). A ``False`` is a pin.
        """
        self._executor.validate_task(task)
        task = replace(task, category=classify_task(task))
        decision = self._policy.decide(
            task,
            self._default_provider,
            capabilities=self._capabilities_registry,
            model_registry=self._model_registry,
            fabric=self._fabric,
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
        allow_fallback = (
            provider_name is None if allow_fallback is None else bool(allow_fallback)
        )
        cost_aware = (
            (
                task.category == TaskCategory.OTHER
                or task.category in self._SPECIALIZED_CATEGORIES
            )
            and (self._capabilities_registry is not None or self._model_registry is not None or self._fabric is not None)
        )
        if provider_name is not None:
            attempts = [selected]
            if allow_fallback:
                # A named primary with the chain still available. Every fallback passes
                # the same automatic-route checks a policy candidate passes (registered,
                # capabilities registered, enabled, not a disallowed paid provider, key
                # present), so this cannot reach a provider the deployment did not enable.
                attempts.extend(
                    name
                    for name in self._fallback_chain
                    if name != selected
                    and self._automatic_route_rejection(name, task) is None
                )
        elif cost_aware:
            # The policy's candidates are authoritative and keep their order. The
            # configured default provider is appended by `_with_default_provider` below,
            # because it is the provider this deployment asked to run and a policy
            # preference list is not a reason to ignore that.
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
            attempts = self._with_default_provider(attempts, task)

        if (
            allow_fallback
            and (
                task.category == TaskCategory.OTHER
                or task.category in self._SPECIALIZED_CATEGORIES
            )
            and (self._capabilities_registry is not None or self._model_registry is not None or self._fabric is not None)
            and (decision.candidates or not task.parameters.get("required_capabilities"))
        ):
            permitted_fallbacks = []
            for name in self._fallback_chain:
                if name in attempts:
                    continue
                if self._capabilities_registry is not None:
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
                elif self._model_registry is not None:
                    models = self._model_registry.list_models(name)
                    if not models:
                        continue
                    if (
                        all(m.cost_tier == CostTier.PAID for m in models)
                        and not self._allow_paid_providers
                    ):
                        continue
                permitted_fallbacks.append(name)
            attempts.extend(permitted_fallbacks)

        failures = []
        previous_name = None
        request_error_stopped = False
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
                        task=task,
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
                        task=task,
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
                            task=task,
                        )
                        previous_name = name
                        continue
                    if capability.provider_name != provider.provider_name:
                        reason = "provider capability metadata does not match"
                        failures.append(f"{name}: {reason}")
                        self._emit_provider_result(
                            observer, name, model_name, attempt_number,
                            attempt_started, False, reason,
                            task=task,
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
                    task=task,
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
                    task=task,
                )
                previous_name = name
                if not failover_eligible(exc):
                    request_error_stopped = True
                    break
                continue
            except Exception as exc:
                reason = f"Execution raised {type(exc).__name__}"
                self._emit_provider_result(
                    observer, name, model_name, attempt_number,
                    attempt_started, False, reason,
                    task=task,
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
                    result=result,
                    task=task,
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
                result=result,
                task=task,
            )
            previous_name = name

        if not attempts:
            if "CapabilityFabric rejected" in decision.reason:
                error = decision.reason
            else:
                error = "No provider candidates are permitted by the cost policy"
        elif request_error_stopped:
            # A request error is the caller's, not the provider's: the remaining
            # candidates would reject the very same request and pay for the discovery.
            error = (
                "Provider request rejected; the fallback chain was not advanced: "
                + "; ".join(failures)
            )
        else:
            error = "Provider fallback chain exhausted: " + "; ".join(failures)

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

    def _emit_provider_result(
        self,
        observer: Optional[Callable[[EventType, Dict[str, object]], None]],
        provider: str,
        model: Optional[str],
        attempt: int,
        started: float,
        success: bool,
        error: Optional[str],
        result: Optional[TaskResult] = None,
        task: Optional[Task] = None,
    ) -> None:
        duration = max(0.0, time.perf_counter() - started)
        data: Dict[str, object] = {
            "provider": provider,
            "model": model,
            "attempt": attempt,
            "duration_seconds": duration,
            "success": success,
        }
        if result is not None and result.usage is not None:
            data["usage"] = {
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "cached_tokens": getattr(result.usage, "cached_tokens", 0),
                "total_tokens": getattr(result.usage, "total_tokens", result.usage.input_tokens + result.usage.output_tokens),
                "estimated_cost": result.usage.estimated_cost,
            }
        if error is not None:
            data["error"] = error
        self._emit(observer, EventType.PROVIDER_RESULT, **data)

        if self._fabric is not None and task is not None:
            run_id = str(task.context.get("run_id") or task.parameters.get("run_id") or task.id)
            model_info = None
            if self._model_registry is not None and model:
                try:
                    model_info = self._model_registry.get_model(model, provider_id=provider)
                except LookupError:
                    pass

            attempt_rec = UsageAccountingAdapter.create_attempt_usage(
                attempt_index=attempt,
                agent_name=provider,
                provider_name=provider,
                model_name=result.model_name if (result and result.model_name) else (model or provider),
                usage=result.usage if result else None,
                duration_seconds=duration,
                model_info=model_info,
                provider_reported_cost=result.usage.estimated_cost if (result and result.usage and result.usage.estimated_cost is not None) else None,
                success=success,
                error_message=error,
                is_fallback=(attempt > 1),
            )
            self._fabric.record_usage(run_id, attempt_rec)

    def _with_default_provider(
        self, attempts: list[str], task: Task
    ) -> list[str]:
        """Ensure the configured default provider is actually attempted.

        The policy returns an ordered list of *preferences* per category. For a
        specialized category that list is the whole attempt plan, so a deployment whose
        `default_provider` is not among those preferences -- `mock`, for instance -- never
        attempted the provider it had explicitly asked for. Observed consequence: with
        only the mock provider installed, a `code` task failed with
        "Provider fallback chain exhausted: openrouter: provider is unavailable or not
        registered", because `openrouter` was the first preference and `mock` was never in
        the list at all.

        The policy's order is preserved: the default is **appended**, so every preference
        keeps its priority and the default runs last. The default is included only when it
        would pass the same automatic-route checks every other candidate passes —
        registered, capability registered, enabled by configuration, not a disallowed paid
        provider, and holding whatever key it declares. A default that fails those checks
        is not added, so this cannot route to a provider the deployment did not enable.

        Duplicates are impossible: the name is appended only when absent.
        """

        name = self._default_provider
        if not name or name in attempts:
            return attempts
        if self._provider_registry is None:
            # Without a registry there is no way to prove the provider is registered, and
            # adding an unverifiable name would violate the same rule.
            return attempts
        # `_automatic_route_rejection` consults the *provider* registry. Dispatch needs an
        # *agent*: a name registered as a provider but absent from the agent registry would
        # pass that check and then raise `AgentNotFoundError` when the dispatcher looks it
        # up -- a raise, not a failed `TaskResult`, which loses the reason on the way out.
        # Both registries are populated together in the normal composition, so this is a
        # guard against a half-registered provider rather than a live defect.
        if name not in self._registry.list_agents():
            return attempts
        if self._automatic_route_rejection(name, task) is not None:
            return attempts
        return [*attempts, name]

    def _automatic_route_rejection(self, name: str, task: Task) -> Optional[str]:
        """Reject auto-route candidates that are disabled, unconfigured, or incapable."""
        if self._provider_registry is None:
            return None
        try:
            provider = self._provider_registry.get(name)
        except ProviderNotFoundError:
            return "provider is unavailable or not registered"

        if self._capabilities_registry is not None:
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

        elif self._model_registry is not None:
            if not self._model_registry.has_provider(name):
                return "provider is not registered in ModelRegistry"
            provider_info = self._model_registry.get_provider(name)
            if provider_info.default_secret_env:
                try:
                    key_available = provider.secret_store.get_secret(provider_info.default_secret_env)
                except (OSError, RuntimeError):
                    key_available = None
                if not key_available:
                    return f"required key {provider_info.default_secret_env} is not configured"

        return None
