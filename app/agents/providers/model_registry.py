"""Central Model and Provider Capability Registry for Forge AI v0.2."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Dict, Optional, Set

from app.agents.providers.models import (
    CostTier,
    ModelCapability,
    ProviderAccount,
    ProviderInfo,
    ProviderModelInfo,
    ProviderProtocol,
)


class ModelRegistryError(Exception):
    """Base exception for model registry errors."""


class ProviderNotFoundError(ModelRegistryError, LookupError):
    """Raised when looking up an unregistered provider."""


class ModelNotFoundError(ModelRegistryError, LookupError):
    """Raised when looking up an unregistered or ambiguous model."""


class DuplicateRegistrationError(ModelRegistryError, ValueError):
    """Raised when attempting to register a duplicate provider or model ID."""


class ModelRegistry:
    """In-memory, deterministic lookup and filtering registry for providers and models."""

    def __init__(self) -> None:
        self._providers: Dict[str, ProviderInfo] = {}
        self._models: Dict[str, ProviderModelInfo] = {}  # canonical_id -> ProviderModelInfo
        self._accounts: Dict[str, ProviderAccount] = {}  # account_id -> ProviderAccount
        self._models_by_provider: Dict[str, Set[str]] = {}  # provider_id -> Set[model_id]

    def register_provider(self, provider: ProviderInfo) -> ProviderInfo:
        """Register a new provider descriptor."""
        if not isinstance(provider, ProviderInfo):
            raise TypeError("provider must be a ProviderInfo instance")
        if provider.provider_id in self._providers:
            raise DuplicateRegistrationError(
                f"Provider '{provider.provider_id}' is already registered"
            )
        self._providers[provider.provider_id] = provider
        self._models_by_provider.setdefault(provider.provider_id, set())
        return provider

    def register_model(self, model: ProviderModelInfo) -> ProviderModelInfo:
        """Register a model descriptor under an existing provider."""
        if not isinstance(model, ProviderModelInfo):
            raise TypeError("model must be a ProviderModelInfo instance")
        if model.provider_id not in self._providers:
            raise ProviderNotFoundError(
                f"Cannot register model '{model.model_id}': provider '{model.provider_id}' is not registered"
            )
        canonical_id = model.canonical_id
        if canonical_id in self._models:
            raise DuplicateRegistrationError(
                f"Model '{canonical_id}' is already registered"
            )
        self._models[canonical_id] = model
        self._models_by_provider[model.provider_id].add(model.model_id)
        return model

    def register_account(self, account: ProviderAccount) -> ProviderAccount:
        """Register a configured provider account reference."""
        if not isinstance(account, ProviderAccount):
            raise TypeError("account must be a ProviderAccount instance")
        if account.provider_id not in self._providers:
            raise ProviderNotFoundError(
                f"Cannot register account '{account.account_id}': provider '{account.provider_id}' is not registered"
            )
        if account.account_id in self._accounts:
            raise DuplicateRegistrationError(
                f"Account '{account.account_id}' is already registered"
            )
        self._accounts[account.account_id] = account
        return account

    def get_provider(self, provider_id: str) -> ProviderInfo:
        """Retrieve provider descriptor by provider_id."""
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        clean_id = provider_id.strip()
        if clean_id not in self._providers:
            raise ProviderNotFoundError(f"Provider '{clean_id}' is not registered")
        return self._providers[clean_id]

    def has_provider(self, provider_id: str) -> bool:
        """Check if provider is registered."""
        return isinstance(provider_id, str) and provider_id.strip() in self._providers

    def get_model(
        self,
        canonical_or_model_id: str,
        provider_id: Optional[str] = None,
    ) -> ProviderModelInfo:
        """Retrieve model descriptor by canonical ID ('provider:model') or short model_id."""
        if not isinstance(canonical_or_model_id, str) or not canonical_or_model_id.strip():
            raise ValueError("model identifier must be a non-empty string")
        clean_id = canonical_or_model_id.strip()

        # Case 1: explicit provider_id passed
        if provider_id is not None:
            clean_provider = provider_id.strip()
            canonical_id = f"{clean_provider}:{clean_id}"
            if canonical_id in self._models:
                return self._models[canonical_id]
            raise ModelNotFoundError(
                f"Model '{clean_id}' under provider '{clean_provider}' is not registered"
            )

        # Case 2: canonical format passed ('provider:model')
        if ":" in clean_id:
            if clean_id in self._models:
                return self._models[clean_id]
            raise ModelNotFoundError(f"Model '{clean_id}' is not registered")

        # Case 3: short model_id search across all registered models
        matches = [m for m in self._models.values() if m.model_id == clean_id]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            matching_ids = ", ".join(m.canonical_id for m in matches)
            raise ModelNotFoundError(
                f"Ambiguous model ID '{clean_id}'. Matches multiple providers: {matching_ids}. "
                "Specify canonical ID ('provider:model') or provider_id."
            )
        raise ModelNotFoundError(f"Model '{clean_id}' is not registered")

    def has_model(
        self,
        canonical_or_model_id: str,
        provider_id: Optional[str] = None,
    ) -> bool:
        """Check if model exists in registry."""
        try:
            self.get_model(canonical_or_model_id, provider_id=provider_id)
            return True
        except ModelRegistryError:
            return False

    def list_providers(self) -> tuple[ProviderInfo, ...]:
        """Return all registered providers in deterministic order."""
        return tuple(self._providers[k] for k in sorted(self._providers.keys()))

    def list_models(self, provider_id: Optional[str] = None) -> tuple[ProviderModelInfo, ...]:
        """Return registered models in deterministic order."""
        if provider_id is not None:
            clean_provider = provider_id.strip()
            if clean_provider not in self._providers:
                raise ProviderNotFoundError(f"Provider '{clean_provider}' is not registered")
            models = [
                m for m in self._models.values() if m.provider_id == clean_provider
            ]
        else:
            models = list(self._models.values())
        models.sort(key=lambda m: m.canonical_id)
        return tuple(models)

    def list_accounts(self, provider_id: Optional[str] = None) -> tuple[ProviderAccount, ...]:
        """Return registered accounts in deterministic order."""
        if provider_id is not None:
            clean_provider = provider_id.strip()
            accounts = [a for a in self._accounts.values() if a.provider_id == clean_provider]
        else:
            accounts = list(self._accounts.values())
        accounts.sort(key=lambda a: (a.provider_id, -a.priority, a.account_id))
        return tuple(accounts)

    def find_models(
        self,
        *,
        capabilities: Optional[Iterable[ModelCapability | str]] = None,
        task_category: Optional[str] = None,
        min_context: Optional[int] = None,
        max_cost_tier: Optional[CostTier] = None,
        allowed_providers: Optional[Sequence[str]] = None,
        provider_id: Optional[str] = None,
    ) -> tuple[ProviderModelInfo, ...]:
        """Find and filter models matching capability, context, and cost constraints."""
        candidates = list(self._models.values())

        if provider_id is not None:
            clean_provider = provider_id.strip()
            candidates = [m for m in candidates if m.provider_id == clean_provider]

        if allowed_providers is not None:
            allowed_set = {p.strip() for p in allowed_providers if p.strip()}
            candidates = [m for m in candidates if m.provider_id in allowed_set]

        # Task category filter (matches ModelCapability)
        if task_category is not None:
            cat_str = task_category.strip().lower()
            try:
                cat_cap = ModelCapability(cat_str)
                candidates = [m for m in candidates if cat_cap in m.capabilities]
            except ValueError:
                # If task_category does not directly match a capability enum, no models match
                candidates = []

        # Required capabilities filter
        if capabilities is not None:
            required_caps: set[ModelCapability] = set()
            for cap in capabilities:
                if isinstance(cap, ModelCapability):
                    required_caps.add(cap)
                elif isinstance(cap, str):
                    try:
                        required_caps.add(ModelCapability(cap.strip()))
                    except ValueError:
                        # Unknown required capability -> fail closed (no match)
                        return ()
            if required_caps:
                candidates = [
                    m for m in candidates if required_caps.issubset(m.capabilities)
                ]

        # Context window bounds
        if min_context is not None and min_context > 0:
            candidates = [m for m in candidates if m.context_window >= min_context]

        # Cost tier bounds
        if max_cost_tier is not None:
            cost_order = {CostTier.FREE: 0, CostTier.CHEAP: 1, CostTier.PAID: 2}
            max_val = cost_order[max_cost_tier]
            candidates = [m for m in candidates if cost_order[m.cost_tier] <= max_val]

        # Deterministic sorting: CostTier ascending (FREE -> CHEAP -> PAID), then context descending, then canonical_id
        cost_order = {CostTier.FREE: 0, CostTier.CHEAP: 1, CostTier.PAID: 2}
        candidates.sort(
            key=lambda m: (cost_order[m.cost_tier], -m.context_window, m.canonical_id)
        )
        return tuple(candidates)


def create_default_registry() -> ModelRegistry:
    """Create and return a pre-populated registry with default Forge-supported models."""
    registry = ModelRegistry()

    # 1. OpenAI
    registry.register_provider(
        ProviderInfo(
            provider_id="openai",
            display_name="OpenAI",
            protocol=ProviderProtocol.OPENAI_CHAT,
            default_base_url="https://api.openai.com/v1",
            default_secret_env="OPENAI_API_KEY",
            supported_models=("gpt-4o", "gpt-4o-mini", "o3-mini"),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="openai",
            model_id="gpt-4o",
            display_name="GPT-4o",
            context_window=131072,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.REASONING,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STREAMING,
                ModelCapability.VISION,
            }),
            cost_tier=CostTier.PAID,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="openai",
            model_id="gpt-4o-mini",
            display_name="GPT-4o Mini",
            context_window=131072,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STREAMING,
                ModelCapability.VISION,
            }),
            cost_tier=CostTier.CHEAP,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="openai",
            model_id="o3-mini",
            display_name="o3-mini",
            context_window=200000,
            max_output_tokens=100000,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REASONING,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.PAID,
        )
    )

    # 2. Anthropic
    registry.register_provider(
        ProviderInfo(
            provider_id="anthropic",
            display_name="Anthropic",
            protocol=ProviderProtocol.ANTHROPIC_MESSAGES,
            default_base_url="https://api.anthropic.com/v1",
            default_secret_env="ANTHROPIC_API_KEY",
            supported_models=("claude-3-7-sonnet-20250219", "claude-3-5-haiku-20241022"),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="anthropic",
            model_id="claude-3-7-sonnet-20250219",
            display_name="Claude 3.7 Sonnet",
            context_window=200000,
            max_output_tokens=8192,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.REASONING,
                ModelCapability.LONG_CONTEXT,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STREAMING,
                ModelCapability.VISION,
            }),
            cost_tier=CostTier.PAID,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="anthropic",
            model_id="claude-3-5-haiku-20241022",
            display_name="Claude 3.5 Haiku",
            context_window=200000,
            max_output_tokens=8192,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
                ModelCapability.LONG_CONTEXT,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.CHEAP,
        )
    )

    # 3. Google Gemini
    registry.register_provider(
        ProviderInfo(
            provider_id="google",
            display_name="Google Gemini",
            protocol=ProviderProtocol.GOOGLE_GENAI,
            default_secret_env="GEMINI_API_KEY",
            supported_models=("gemini-3.8-flash", "gemini-2.0-flash"),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="google",
            model_id="gemini-3.8-flash",
            display_name="Gemini 3.8 Flash",
            context_window=1048576,
            max_output_tokens=8192,
            capabilities=frozenset({
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
                ModelCapability.LONG_CONTEXT,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STREAMING,
                ModelCapability.VISION,
            }),
            cost_tier=CostTier.PAID,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="google",
            model_id="gemini-2.0-flash",
            display_name="Gemini 2.0 Flash",
            context_window=1048576,
            max_output_tokens=8192,
            capabilities=frozenset({
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
                ModelCapability.LONG_CONTEXT,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STREAMING,
                ModelCapability.VISION,
            }),
            cost_tier=CostTier.CHEAP,
        )
    )

    # 4. DeepSeek
    registry.register_provider(
        ProviderInfo(
            provider_id="deepseek",
            display_name="DeepSeek",
            protocol=ProviderProtocol.OPENAI_CHAT,
            default_base_url="https://api.deepseek.com",
            default_secret_env="DEEPSEEK_API_KEY",
            supported_models=("deepseek-chat", "deepseek-reasoner"),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="deepseek",
            model_id="deepseek-chat",
            display_name="DeepSeek Chat",
            context_window=131072,
            max_output_tokens=8192,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.FAST,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.CHEAP,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="deepseek",
            model_id="deepseek-reasoner",
            display_name="DeepSeek Reasoner (R1)",
            context_window=131072,
            max_output_tokens=8192,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.CHEAP,
        )
    )

    # 5. OpenRouter (Gateway)
    registry.register_provider(
        ProviderInfo(
            provider_id="openrouter",
            display_name="OpenRouter",
            protocol=ProviderProtocol.OPENAI_CHAT,
            default_base_url="https://openrouter.ai/api/v1",
            default_secret_env="OPENROUTER_API_KEY",
            is_gateway=True,
            supported_models=(
                "cohere/north-mini-code:free",
                "openrouter/free",
                "meta-llama/llama-3.3-70b-instruct",
            ),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="openrouter",
            model_id="cohere/north-mini-code:free",
            display_name="Cohere North Mini Code (Free)",
            context_window=131072,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.FREE,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="openrouter",
            model_id="openrouter/free",
            display_name="OpenRouter Free Route",
            context_window=131072,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.FREE,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="openrouter",
            model_id="meta-llama/llama-3.3-70b-instruct",
            display_name="Llama 3.3 70B Instruct",
            context_window=131072,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.CHEAP,
        )
    )

    # 6. Groq
    registry.register_provider(
        ProviderInfo(
            provider_id="groq",
            display_name="Groq",
            protocol=ProviderProtocol.OPENAI_CHAT,
            default_base_url="https://api.groq.com/openai/v1",
            default_secret_env="GROQ_API_KEY",
            supported_models=("llama-3.3-70b-versatile", "qwen-2.5-coder-32b"),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="groq",
            model_id="llama-3.3-70b-versatile",
            display_name="Llama 3.3 70B Versatile",
            context_window=131072,
            max_output_tokens=8192,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.FAST,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.CHEAP,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="groq",
            model_id="qwen-2.5-coder-32b",
            display_name="Qwen 2.5 Coder 32B",
            context_window=32768,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.FAST,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.CHEAP,
        )
    )

    # 7. Together AI
    registry.register_provider(
        ProviderInfo(
            provider_id="together",
            display_name="Together AI",
            protocol=ProviderProtocol.OPENAI_CHAT,
            default_base_url="https://api.together.ai/v1",
            default_secret_env="TOGETHER_API_KEY",
            supported_models=("Qwen/Qwen2.5-Coder-32B-Instruct",),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="together",
            model_id="Qwen/Qwen2.5-Coder-32B-Instruct",
            display_name="Qwen 2.5 Coder 32B (Together)",
            context_window=32768,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.PAID,
        )
    )

    # 8. xAI
    registry.register_provider(
        ProviderInfo(
            provider_id="xai",
            display_name="xAI (Grok)",
            protocol=ProviderProtocol.OPENAI_CHAT,
            default_base_url="https://api.x.ai/v1",
            default_secret_env="XAI_API_KEY",
            supported_models=("grok-2",),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="xai",
            model_id="grok-2",
            display_name="Grok 2",
            context_window=131072,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.PAID,
        )
    )

    # 9. Mock (Offline testing)
    registry.register_provider(
        ProviderInfo(
            provider_id="mock",
            display_name="Mock Provider",
            protocol=ProviderProtocol.MOCK,
            default_secret_env="",
            supported_models=("mock-v1", "mock-default"),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="mock",
            model_id="mock-v1",
            display_name="Mock Model v1",
            context_window=32768,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
            }),
            cost_tier=CostTier.FREE,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="mock",
            model_id="mock-default",
            display_name="Mock Model Default",
            context_window=32768,
            max_output_tokens=4096,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
            }),
            cost_tier=CostTier.FREE,
        )
    )

    return registry
