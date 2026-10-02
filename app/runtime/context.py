"""Application runtime dependencies assembled by the bootstrap function."""

from dataclasses import dataclass

from app.agents.providers.registry import ProviderRegistry
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.registry import AgentRegistry
from app.config.settings import RuntimeSettings
from app.config.provider_accounts import ProviderAccountConfig
from app.orchestrator.orchestrator import Orchestrator


@dataclass(frozen=True)
class RuntimeContext:
    """Shared, explicitly constructed application components."""

    settings: RuntimeSettings
    provider_accounts: ProviderAccountConfig
    provider_registry: ProviderRegistry
    provider_capabilities: ProviderCapabilitiesRegistry
    agent_registry: AgentRegistry
    orchestrator: Orchestrator
