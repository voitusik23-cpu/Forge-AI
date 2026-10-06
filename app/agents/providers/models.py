"""Domain contracts and data models for Provider and Model Registry v0.2."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*\Z")


class CostTier(str, Enum):
    """Coarse provider and model price category."""

    FREE = "free"
    CHEAP = "cheap"
    PAID = "paid"


class ModelCapability(str, Enum):
    """Granular task and technical capabilities for individual models."""

    # Task capabilities (compatible with TaskCategory)
    CODE = "code"
    ANALYSIS = "analysis"
    REVIEW = "review"
    REASONING = "reasoning"

    # Performance / trait capabilities
    FAST = "fast"
    LONG_CONTEXT = "long_context"

    # API / protocol feature capabilities
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    STREAMING = "streaming"
    VISION = "vision"


class ProviderProtocol(str, Enum):
    """Transport protocol and client adapter classification."""

    OPENAI_CHAT = "openai_chat"
    ANTHROPIC_MESSAGES = "anthropic"
    GOOGLE_GENAI = "google_genai"
    MOCK = "mock"


@dataclass(frozen=True)
class ProviderInfo:
    """Static descriptor for a provider service."""

    provider_id: str
    display_name: str
    protocol: ProviderProtocol
    default_base_url: str = ""
    default_secret_env: str = ""
    is_gateway: bool = False
    supported_models: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("display_name must be a non-empty string")
        if not isinstance(self.protocol, ProviderProtocol):
            raise ValueError("protocol must be a ProviderProtocol instance")
        if self.default_secret_env and not _ENV_VAR_RE.fullmatch(self.default_secret_env):
            raise ValueError(
                f"default_secret_env '{self.default_secret_env}' must be an uppercase environment variable name"
            )
        if isinstance(self.supported_models, (list, tuple)):
            object.__setattr__(self, "supported_models", tuple(self.supported_models))
        else:
            raise ValueError("supported_models must be a tuple of model IDs")
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class ProviderModelInfo:
    """Static metadata and granular capabilities for one model under a provider."""

    provider_id: str
    model_id: str
    context_window: int
    capabilities: frozenset[ModelCapability]
    cost_tier: CostTier
    display_name: str = ""
    max_output_tokens: int = 4096
    supports_system_prompt: bool = True
    input_cost_per_1m: float | None = None
    output_cost_per_1m: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        if not isinstance(self.context_window, int) or self.context_window <= 0:
            raise ValueError("context_window must be a positive integer")
        if not isinstance(self.max_output_tokens, int) or self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        if not isinstance(self.cost_tier, CostTier):
            raise ValueError("cost_tier must be a CostTier instance")

        # Normalize capabilities to frozenset of ModelCapability
        if isinstance(self.capabilities, (set, frozenset, list, tuple)):
            norm_caps: set[ModelCapability] = set()
            for cap in self.capabilities:
                if isinstance(cap, ModelCapability):
                    norm_caps.add(cap)
                elif isinstance(cap, str):
                    try:
                        norm_caps.add(ModelCapability(cap))
                    except ValueError:
                        raise ValueError(f"Unknown capability '{cap}'")
                else:
                    raise ValueError(f"Invalid capability object: {cap!r}")
            object.__setattr__(self, "capabilities", frozenset(norm_caps))
        else:
            raise ValueError("capabilities must be a collection of ModelCapability")

        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def canonical_id(self) -> str:
        """Global canonical identifier in the form 'provider_id:model_id'."""
        return f"{self.provider_id}:{self.model_id}"

    def has_capability(self, capability: ModelCapability | str) -> bool:
        """Check if this model supports the given capability."""
        if isinstance(capability, str):
            try:
                cap_enum = ModelCapability(capability)
            except ValueError:
                return False
            return cap_enum in self.capabilities
        return capability in self.capabilities


@dataclass(frozen=True, repr=False)
class ProviderAccount:
    """Account descriptor referencing a credential in SecretStore by variable name."""

    account_id: str
    provider_id: str
    secret_ref: str
    account_email: str | None = None
    enabled: bool = True
    priority: int = 100
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise ValueError("account_id must be a non-empty string")
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        if not isinstance(self.secret_ref, str) or not _ENV_VAR_RE.fullmatch(self.secret_ref):
            raise ValueError(
                f"secret_ref '{self.secret_ref}' must be an uppercase environment variable name"
            )
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    def __repr__(self) -> str:
        return (
            f"ProviderAccount(account_id={self.account_id!r}, "
            f"provider_id={self.provider_id!r}, "
            f"secret_ref={self.secret_ref!r}, "
            f"enabled={self.enabled!r}, "
            f"priority={self.priority!r})"
        )
