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
    )
