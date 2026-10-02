"""Explicit construction of the Forge AI runtime."""

from dataclasses import replace
from typing import Optional

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.registry import ProviderRegistry
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.registry import AgentRegistry
from app.config.settings import RuntimeSettings, load_settings
from app.config.provider_accounts import load_provider_account_config
from app.config.secrets import SecretStore
from app.orchestrator.orchestrator import Orchestrator
from app.runtime.context import RuntimeContext
from app.runtime.logging import configure_logging


def create_runtime(settings: Optional[RuntimeSettings] = None) -> RuntimeContext:
    """Build registries and orchestrator without making external API calls."""
    resolved_settings = settings if settings is not None else load_settings()
    configure_logging(resolved_settings)

    provider_factory = ProviderFactory()
    secret_store = SecretStore()
    provider_registry = ProviderRegistry()
    capabilities_registry = ProviderCapabilitiesRegistry()
    agent_registry = AgentRegistry()
    for provider_name in provider_factory.list_providers():
        provider = provider_factory.create(provider_name, secret_store=secret_store)
        if provider_name == resolved_settings.default_provider:
            provider = provider_factory.create(
                provider_name,
                config=replace(
                    provider.config, model_name=resolved_settings.default_model
                ),
                secret_store=secret_store,
            )
        provider_registry.register(provider)
        capabilities_registry.register_provider(provider)
        agent_registry.register(ProviderAgent(provider))

    return RuntimeContext(
        settings=resolved_settings,
        provider_accounts=load_provider_account_config(),
        provider_registry=provider_registry,
        provider_capabilities=capabilities_registry,
        agent_registry=agent_registry,
        orchestrator=Orchestrator(
            agent_registry, default_provider=resolved_settings.default_provider
        ),
    )
