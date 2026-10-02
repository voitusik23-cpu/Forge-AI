"""Explicit construction of the Forge AI runtime."""

from dataclasses import replace
from typing import Optional

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.config.settings import RuntimeSettings, load_settings
from app.orchestrator.orchestrator import Orchestrator
from app.runtime.context import RuntimeContext
from app.runtime.logging import configure_logging


def create_runtime(settings: Optional[RuntimeSettings] = None) -> RuntimeContext:
    """Build registries and orchestrator without making external API calls."""
    resolved_settings = settings if settings is not None else load_settings()
    configure_logging(resolved_settings)

    provider_factory = ProviderFactory()
    provider_template = provider_factory.create(resolved_settings.default_provider)
    provider_config = replace(
        provider_template.config, model_name=resolved_settings.default_model
    )
    provider = provider_factory.create(
        resolved_settings.default_provider, config=provider_config
    )

    provider_registry = ProviderRegistry()
    provider_registry.register(provider)

    agent_registry = AgentRegistry()
    agent_registry.register(ProviderAgent(provider))

    return RuntimeContext(
        settings=resolved_settings,
        provider_registry=provider_registry,
        agent_registry=agent_registry,
        orchestrator=Orchestrator(agent_registry),
    )
