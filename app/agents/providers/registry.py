"""In-memory registry for provider implementations."""

from typing import Dict, List

from app.agents.providers.base import Provider


class DuplicateProviderError(ValueError):
    """Raised when a provider name is already registered."""


class ProviderNotFoundError(LookupError):
    """Raised when a provider name is not registered."""


class ProviderRegistry:
    """Register and retrieve providers by their canonical name."""

    def __init__(self) -> None:
        self._providers: Dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        """Register a provider, rejecting duplicate provider names."""
        if provider.provider_name in self._providers:
            raise DuplicateProviderError(
                f"Provider '{provider.provider_name}' is already registered"
            )
        self._providers[provider.provider_name] = provider

    def get(self, name: str) -> Provider:
        """Return a registered provider or raise a clear lookup error."""
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ProviderNotFoundError(f"Provider '{name}' is not registered") from exc

    def list_providers(self) -> List[str]:
        """Return registered provider names in registration order."""
        return list(self._providers)
