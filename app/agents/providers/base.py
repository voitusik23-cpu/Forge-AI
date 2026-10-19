"""Provider-neutral request/response types and provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.config.secrets import SecretStore
from app.agents.providers.config import ProviderConfig
from app.usage import Usage


@dataclass
class ProviderRequest:
    """A provider-neutral prompt and the task context supplied by an agent."""

    prompt: str
    context: Dict[str, Any] = field(default_factory=dict)
    model_name: Optional[str] = None


@dataclass
class ProviderResponse:
    """A provider-neutral text response."""

    provider_name: str
    model_name: str
    output: str
    usage: Usage = field(default_factory=Usage)


class ProviderError(RuntimeError):
    """A safe, expected provider execution failure.

    ``failover_eligible`` tells the orchestrator's fallback chain whether *another*
    provider may legitimately serve the same request after this failure. The default is
    ``True`` because that is the historical behaviour of the chain: an unknown provider
    error is treated as the provider's problem, not the request's. A subclass that
    positively identifies a *request* error -- the caller's payload or model id is wrong,
    so every provider would reject it -- sets it to ``False`` and stops the chain.
    """

    failover_eligible = True


class ProviderNotConfiguredError(ProviderError):
    """Raised when a provider has no real integration configured.

    Missing configuration is the provider's problem, so the chain may still advance to a
    provider that is configured.
    """


class Provider(ABC):
    """Common interface implemented by each provider adapter."""

    def __init__(
        self, config: ProviderConfig, *, secret_store: Optional[SecretStore] = None
    ) -> None:
        self.config = config
        self.secret_store = secret_store or SecretStore()

    @property
    def provider_name(self) -> str:
        return self.config.provider_name

    @property
    def model_name(self) -> str:
        return self.config.model_name

    @abstractmethod
    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate a response, without exposing provider SDK details."""


class UnconfiguredProvider(Provider):
    """Base for provider placeholders that deliberately make no API calls."""

    PROVIDER_NAME = ""
    API_KEY_ENV_VAR = ""

    def __init__(
        self,
        config: Optional[ProviderConfig] = None,
        *,
        secret_store: Optional[SecretStore] = None,
    ) -> None:
        resolved_config = config or ProviderConfig(
            provider_name=self.PROVIDER_NAME,
            api_key_env_var=self.API_KEY_ENV_VAR,
        )
        if resolved_config.provider_name != self.PROVIDER_NAME:
            raise ValueError(
                f"Expected config for '{self.PROVIDER_NAME}', "
                f"got '{resolved_config.provider_name}'"
            )
        super().__init__(resolved_config, secret_store=secret_store)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Fail predictably until this provider has a real integration."""
        raise ProviderNotConfiguredError("Provider integration not configured")
