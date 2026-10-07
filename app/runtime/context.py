"""Application runtime dependencies assembled by the bootstrap function."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from app.agents.providers.registry import ProviderRegistry
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.registry import AgentRegistry
from app.config.settings import RuntimeSettings
from app.config.provider_accounts import ProviderAccountConfig
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.agent_runtime.harness import AgentHarness


@dataclass(frozen=True)
class RuntimeContext:
    """Shared, explicitly constructed application components."""

    settings: RuntimeSettings
    provider_accounts: ProviderAccountConfig
    provider_registry: ProviderRegistry
    provider_capabilities: ProviderCapabilitiesRegistry
    agent_registry: AgentRegistry
    orchestrator: Orchestrator
    run_executor: Optional[RunExecutor] = None
    # Canonical production orchestration loop. It is stateless with respect to a
    # run: the per-run security perimeter arrives through HarnessRequest, so one
    # harness instance serves every run. Bootstrap always supplies it; it is
    # optional only so existing callers that build a RuntimeContext by hand keep
    # working.
    harness: Optional["AgentHarness"] = None
