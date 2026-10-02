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
    supports_large_context: bool = False

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
        if not isinstance(self.supports_large_context, bool):
            raise ValueError("supports_large_context must be a boolean")


# Traits describe provider API capability in general. Tool support may depend
# on the selected model; these flags do not claim the current adapter executes
# streaming or tool calls. Per-token or per-request pricing is out of scope.
_DECLARATIONS = {
    "openai": (True, True, CostTier.PAID, False),
    "anthropic": (True, True, CostTier.PAID, False),
    "google": (True, True, CostTier.PAID, True),
    "xai": (True, True, CostTier.PAID, False),
    "deepseek": (True, True, CostTier.CHEAP, False),
    "openrouter": (True, True, CostTier.CHEAP, False),
    "groq": (True, True, CostTier.CHEAP, False),
    "mock": (False, False, CostTier.FREE, False),
}


def capabilities_for(provider: Provider) -> ProviderCapabilities:
    """Build metadata for a provider using its canonical config references."""
    try:
        streaming, tools, cost_tier, large_context = _DECLARATIONS[
            provider.provider_name
        ]
    except KeyError as exc:
        raise ValueError(
            f"No capability declaration for provider '{provider.provider_name}'"
        ) from exc
    # OpenRouter model IDs carry the actual routing price class. The provider
    # can route free models even though its broader catalog has paid models.
    if provider.provider_name == "openrouter" and (
        provider.model_name.endswith(":free")
        or provider.model_name == "openrouter/free"
    ):
        cost_tier = CostTier.FREE
    return ProviderCapabilities(
        provider_name=provider.provider_name,
        api_key_env=provider.config.api_key_env_var,
        supports_streaming=streaming,
        supports_tools=tools,
        cost_tier=cost_tier,
        enabled_by_config=provider.config.enabled,
        supports_large_context=large_context,
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
