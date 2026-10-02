"""Explicit factory for provider implementations."""

from typing import Dict, Optional, Type

from app.agents.providers.anthropic import AnthropicProvider
from app.agents.providers.base import Provider
from app.agents.providers.config import ProviderConfig
from app.agents.providers.deepseek import DeepSeekProvider
from app.agents.providers.google import GoogleProvider
from app.agents.providers.groq import GroqProvider
from app.agents.providers.mock import MockProvider
from app.agents.providers.openai import OpenAIProvider
from app.agents.providers.openrouter import OpenRouterProvider
from app.agents.providers.xai import XAIProvider
from app.config.secrets import SecretStore


class UnknownProviderError(LookupError):
    """Raised when a factory request names an unsupported provider."""


class ProviderFactory:
    """Create a specifically named provider without choosing one automatically."""

    _provider_types: Dict[str, Type[Provider]] = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "deepseek": DeepSeekProvider,
        "google": GoogleProvider,
        "xai": XAIProvider,
        "openrouter": OpenRouterProvider,
        "groq": GroqProvider,
        "mock": MockProvider,
    }

    def list_providers(self) -> list[str]:
        """List supported canonical provider names in stable order."""
        return list(self._provider_types)

    def create(
        self,
        name: str,
        config: Optional[ProviderConfig] = None,
        *,
        secret_store: Optional[SecretStore] = None,
    ) -> Provider:
        """Create a supported provider by its explicit canonical name."""
        try:
            provider_type = self._provider_types[name]
        except KeyError as exc:
            raise UnknownProviderError(f"Unknown provider '{name}'") from exc
        return provider_type(config, secret_store=secret_store)
