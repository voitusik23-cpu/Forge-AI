"""Deterministic mock provider for tests and offline development."""

from typing import Optional

from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig


class MockProvider(Provider):
    """Return a predictable response without any external service."""

    def __init__(self, config: Optional[ProviderConfig] = None) -> None:
        resolved_config = config or ProviderConfig(
            provider_name="mock",
            model_name="mock-v1",
            enabled=True,
        )
        if resolved_config.provider_name != "mock":
            raise ValueError("Expected config for 'mock'")
        super().__init__(resolved_config)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Return a deterministic response derived from the supplied prompt."""
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output=f"MockProvider response: {request.prompt}",
        )
