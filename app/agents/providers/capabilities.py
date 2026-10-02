"""Declarative provider capabilities for future dispatcher policies."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from app.agents.providers.base import Provider


class CostTier(str, Enum):
    """Coarse provider price category; it is not a cost estimate."""

    FREE = "free"
    CHEAP = "cheap"
    PAID = "paid"


@dataclass(frozen=True)
class ProviderCapabilities:
    """Static provider traits plus the existing config enabled flag."""

    provider_name: str
    api_key_env: Optional[str]
    supports_streaming: bool
    supports_tools: bool
    cost_tier: CostTier
    enabled_by_config: bool

    def __post_init__(self) -> None:
        if not self.provider_name.strip():
            raise ValueError("provider_name must not be empty")
        if self.api_key_env is not None and not self.api_key_env.strip():
            raise ValueError("api_key_env must be non-empty when provided")
        if not isinstance(self.supports_streaming, bool):
            raise ValueError("supports_streaming must be a boolean")
        if not isinstance(self.supports_tools, bool):
            raise ValueError("supports_tools must be a boolean")
        if not isinstance(self.cost_tier, CostTier):
            raise ValueError("cost_tier must be a CostTier")
        if not isinstance(self.enabled_by_config, bool):
            raise ValueError("enabled_by_config must be a boolean")


# Traits describe provider API capability in general. Tool support may depend
# on the selected model; these flags do not claim the current adapter executes
# streaming or tool calls. Per-token or per-request pricing is out of scope.
_DECLARATIONS = {
    "openai": (True, True, CostTier.PAID),
    "anthropic": (True, True, CostTier.PAID),
    "google": (True, True, CostTier.PAID),
    "xai": (True, True, CostTier.PAID),
    "deepseek": (True, True, CostTier.CHEAP),
    "openrouter": (True, True, CostTier.CHEAP),
    "groq": (True, True, CostTier.CHEAP),
    "mock": (False, False, CostTier.FREE),
}


def capabilities_for(provider: Provider) -> ProviderCapabilities:
    """Build metadata for a provider using its canonical config references."""
    try:
        streaming, tools, cost_tier = _DECLARATIONS[provider.provider_name]
    except KeyError as exc:
        raise ValueError(
            f"No capability declaration for provider '{provider.provider_name}'"
        ) from exc
    return ProviderCapabilities(
        provider_name=provider.provider_name,
        api_key_env=provider.config.api_key_env_var,
        supports_streaming=streaming,
        supports_tools=tools,
        cost_tier=cost_tier,
        enabled_by_config=provider.config.enabled,
    )


class ProviderCapabilitiesRegistry:
    """In-memory lookup for built-in provider capability metadata."""

    def __init__(self) -> None:
        self._items: Dict[str, ProviderCapabilities] = {}

    def register_provider(self, provider: Provider) -> ProviderCapabilities:
        """Create and register capabilities for a provider instance."""
        capabilities = capabilities_for(provider)
        if capabilities.provider_name in self._items:
            raise ValueError(
                f"Capabilities for '{capabilities.provider_name}' are already registered"
            )
        self._items[capabilities.provider_name] = capabilities
        return capabilities

    def get(self, provider_name: str) -> ProviderCapabilities:
        """Return capabilities for a provider or raise a clear lookup error."""
        try:
            return self._items[provider_name]
        except KeyError as exc:
            raise LookupError(
                f"Capabilities for provider '{provider_name}' are not registered"
            ) from exc

    def list_capabilities(self) -> List[ProviderCapabilities]:
        """Return capabilities in provider registration order."""
        return list(self._items.values())
