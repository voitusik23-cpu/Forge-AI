"""Explicit construction of the Forge AI runtime."""

from dataclasses import replace
from typing import Optional

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.registry import ProviderRegistry
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.providers.model_registry import ModelRegistry
from app.agents.registry import AgentRegistry
from app.config.settings import RuntimeSettings, load_settings
from app.config.provider_accounts import load_provider_account_config
from app.config.secrets import SecretStore
from app.fabric.fabric import CapabilityFabric
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.runtime.context import RuntimeContext
from app.runtime.logging import configure_logging
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


def create_runtime(
    settings: Optional[RuntimeSettings] = None,
    fabric: Optional[CapabilityFabric] = None,
    model_registry: Optional[ModelRegistry] = None,
    tool_registry: Optional[ToolRegistry] = None,
) -> RuntimeContext:
    """Build registries and orchestrator without making external API calls."""
    resolved_settings = settings if settings is not None else load_settings()
    configure_logging(resolved_settings)

    provider_factory = ProviderFactory()
    secret_store = SecretStore()
    provider_registry = ProviderRegistry()
    capabilities_registry = ProviderCapabilitiesRegistry()
    agent_registry = AgentRegistry()
    enabled_providers = set(resolved_settings.enabled_providers)
    for provider_name in provider_factory.list_providers():
        provider = provider_factory.create(provider_name, secret_store=secret_store)
        configured_model = None
        if provider_name == "openrouter":
            configured_model = resolved_settings.openrouter_model
        elif provider_name == "google":
            configured_model = resolved_settings.gemini_model
        elif provider_name == resolved_settings.default_provider:
            configured_model = resolved_settings.default_model
        provider_config = replace(
            provider.config,
            enabled=provider_name in enabled_providers,
            **({"model_name": configured_model} if configured_model else {}),
        )
        if provider_config != provider.config:
            provider = provider_factory.create(
                provider_name,
                config=provider_config,
                secret_store=secret_store,
            )
        provider_registry.register(provider)
        capabilities_registry.register_provider(provider)
        agent_registry.register(ProviderAgent(provider))

    orchestrator = Orchestrator(
        agent_registry,
        default_provider=resolved_settings.default_provider,
        fallback_chain=resolved_settings.provider_fallback_chain,
        provider_registry=provider_registry,
        capabilities_registry=capabilities_registry,
        model_registry=model_registry,
        fabric=fabric,
        allow_paid_providers=resolved_settings.allow_paid_providers,
    )
    return RuntimeContext(
        settings=resolved_settings,
        provider_accounts=load_provider_account_config(),
        provider_registry=provider_registry,
        provider_capabilities=capabilities_registry,
        agent_registry=agent_registry,
        orchestrator=orchestrator,
        run_executor=RunExecutor(
            orchestrator,
            # Tools are optional and default to the historical empty registry, so
            # existing callers keep their previous behavior. Supplying a registry
            # makes execution use exactly the tools that discovery enumerates.
            tool_executor=(
                ToolExecutor(tool_registry) if tool_registry is not None else None
            ),
        ),
        harness=create_agent_harness(),
    )


def create_loop_coordinator(isolate_workspace: bool = True) -> "ExecutionCoordinator":
    """Build the coordinator a production loop should dispatch through.

    Composition decides the workspace staging mode; the API layer only asks for
    a coordinator, so it never imports the adapter itself. Authority is
    unaffected: ``RunScope``, ``ExecutionPolicy``, approval, and the
    coordinator's sentinel are unchanged.
    """
    from app.execution.adapter import LocalExecutionAdapter
    from app.execution.authorizer import ExecutionCoordinator

    return ExecutionCoordinator(
        LocalExecutionAdapter(isolate_workspace=isolate_workspace)
    )


def _default_telemetry_sink() -> object:
    """Build the composition's default durable physical-telemetry sink.

    Imported lazily so composition does not pay for the telemetry module unless a
    harness is actually built, and so the sink stays replaceable through the same
    injected port. The root follows the existing per-user storage convention:
    ``FORGE_TELEMETRY_ROOT`` when set, otherwise a per-user location outside the
    source checkout. It is never the user's workspace.
    """
    from app.agent_runtime.physical_telemetry import FileTelemetrySink

    return FileTelemetrySink()


def create_agent_harness(
    *,
    decision_provider: object | None = None,
    coordinator: object | None = None,
    policy: object | None = None,
    project_discovery: object | None = None,
    with_discovery: bool = True,
    project_planner: object | None = None,
    with_planning: bool = True,
    revision_budget: object | None = None,
    tool_executor: object | None = None,
    tool_registry: object | None = None,
    telemetry_sink: object | None = None,
) -> "AgentHarness":
    """Build the canonical production orchestration loop.

    The harness is stateless per run: everything authority-bearing arrives
    through ``HarnessRequest``, and ``AgentHarness._enforce_run_scope`` refuses a
    request that declares dispatch authority without a frozen ``RunScope``. No
    authority is supplied here, so a harness built by composition cannot execute
    anything on its own.

    The production loop enables the bounded server-side discovery stage by
    default, so a decision is taken on an actual observation of the trusted
    workspace. Discovery holds no authority: it reads through the existing bounded
    scanner and can neither widen the perimeter nor choose its own root.

    It also enables the declarative planning stage: the existing deterministic
    ``Planner`` turns the trusted goal plus that observation into an immutable,
    run-bound ``ExecutionPlan``. A plan is an intention, not authority - it has no
    argv, executable, environment, timeout, or tool grant, and it cannot reach the
    coordinator, the adapter, or the filesystem.

    Defaults are the deterministic decision provider and a coordinator over a
    local adapter, matching the historical behaviour; callers may inject a bounded
    policy (for example the one-action policy of the first vertical slice).
    """
    from app.agent_runtime.harness import AgentHarness
    from app.agent_runtime.policy import AgentHarnessPolicy
    from app.agent_runtime.project_discovery import ProjectDiscovery
    from app.tools.executor import ToolExecutor
    from app.tools.registry import build_default_tool_registry
    from app.decision.provider import DeterministicDecisionProvider
    from app.planning.planner import Planner
    from app.execution.adapter import LocalExecutionAdapter
    from app.execution.authorizer import ExecutionCoordinator

    kwargs = {
        "decision_provider": decision_provider or DeterministicDecisionProvider(),
        "policy": policy or AgentHarnessPolicy(),
        # Server-side observation layer, created at composition time with
        # composition-owned limits. Nothing per-run can replace it.
        "project_discovery": (
            project_discovery
            if project_discovery is not None
            else (ProjectDiscovery() if with_discovery else None)
        ),
        # Declarative planning layer, also composition-owned. It produces
        # intentions only and holds no execution authority.
        "project_planner": (
            project_planner
            if project_planner is not None
            else (Planner() if with_planning else None)
        ),
        # Server-side bound on the revision loop. An explicit budget is passed
        # through; otherwise the harness derives it from its own policy.
        **(
            {"revision_budget": revision_budget}
            if revision_budget is not None
            else {}
        ),
        # The existing production ToolExecutor. The harness supplies it with a
        # server-composed invocation and a context derived from the frozen run
        # scope; the executor keeps owning permission checks and approval.
        "tool_executor": (
            tool_executor
            if tool_executor is not None
            else ToolExecutor(build_default_tool_registry(tool_registry))
        ),
        # Durable physical telemetry (Stage 0.1). The composition supplies the
        # local append-only sink, using the same per-user storage convention as
        # the run store; a caller may inject another sink implementing the same
        # port. It measures a terminal attempt and grants no authority, so it can
        # neither widen a run nor change what a run may execute.
        "telemetry_sink": (
            telemetry_sink if telemetry_sink is not None else _default_telemetry_sink()
        ),
    }
    resolved_coordinator = coordinator
    if resolved_coordinator is None:
        resolved_coordinator = ExecutionCoordinator(LocalExecutionAdapter())
    kwargs["execution_coordinator"] = resolved_coordinator
    return AgentHarness(**kwargs)
