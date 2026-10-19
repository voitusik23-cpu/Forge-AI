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
from app.agents.providers.radox_catalogue import CATALOGUE_SOURCE_NOTE as RADOX_SOURCE_NOTE
from app.agents.providers.radox_catalogue import (
    CURATED_CATALOGUE as RADOX_CURATED_CATALOGUE,
)
from app.agents.providers.radox_catalogue import MODEL_IDS as RADOX_MODEL_IDS


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
    #
    # The ids below are the curated set this owner asked for, and they all exist in the
    # provider's own catalogue: ``GET
    # https://generativelanguage.googleapis.com/v1beta/models`` returned 63 models, 46 of
    # them answering ``generateContent``, including every id registered here. The two ids
    # that were registered before this change were not both real: ``gemini-2.0-flash`` is
    # absent from that answer, and it alone was the CHEAP row, so automatic routing could
    # pick a model that does not exist.
    #
    # Measured through the app's own API on 2026-10-20 (one dispatch per row):
    # ``gemini-2.5-flash`` is listed but this key's call answers
    # ``HTTP 404 NOT_FOUND: This model models/gemini-2.5-flash is no longer available to
    # new users`` -- so it stays selectable by name and is NOT routable automatically;
    # ``gemini-flash-latest`` answered ``PONG`` and the response reported
    # ``model_version=gemini-3.8-flash``, i.e. the alias resolves to the current flash
    # model and that is the id a working default must use.
    #
    # ``cost_tier`` therefore carries its codebase meaning here -- "may this be chosen
    # automatically while ``allow_paid_providers`` is false?" -- and only the model that
    # was observed to answer is CHEAP. Naming a PAID row explicitly still works; the tier
    # only keeps it out of automatic routing.
    _gemini_capabilities = frozenset({
        ModelCapability.ANALYSIS,
        ModelCapability.REVIEW,
        ModelCapability.FAST,
        ModelCapability.LONG_CONTEXT,
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.TOOL_CALLING,
        ModelCapability.STREAMING,
        ModelCapability.VISION,
    })
    registry.register_provider(
        ProviderInfo(
            provider_id="google",
            display_name="Google Gemini",
            protocol=ProviderProtocol.GOOGLE_GENAI,
            default_secret_env="GEMINI_API_KEY",
            supported_models=(
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-flash-latest",
                "gemini-3.5-flash",
            ),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="google",
            model_id="gemini-2.5-flash",
            display_name="Gemini 2.5 Flash",
            context_window=1048576,
            max_output_tokens=8192,
            capabilities=_gemini_capabilities,
            # PAID = not selected automatically: this key cannot call it (404 above).
            cost_tier=CostTier.PAID,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="google",
            model_id="gemini-flash-latest",
            display_name="Gemini Flash (latest)",
            context_window=1048576,
            max_output_tokens=8192,
            capabilities=_gemini_capabilities,
            # CHEAP = the one row automatic routing may pick. Measured to answer.
            cost_tier=CostTier.CHEAP,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="google",
            model_id="gemini-3.5-flash",
            display_name="Gemini 3.5 Flash",
            context_window=1048576,
            max_output_tokens=8192,
            capabilities=_gemini_capabilities,
            # Listed, but not verified with this key: never routed to automatically.
            cost_tier=CostTier.PAID,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="google",
            model_id="gemini-2.5-pro",
            display_name="Gemini 2.5 Pro",
            context_window=1048576,
            max_output_tokens=8192,
            capabilities=_gemini_capabilities | frozenset({ModelCapability.REASONING}),
            cost_tier=CostTier.PAID,
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
            input_cost_per_1m=0.14,
            output_cost_per_1m=0.28,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="deepseek",
            model_id="deepseek-flash",
            display_name="DeepSeek Flash",
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
            input_cost_per_1m=0.14,
            output_cost_per_1m=0.28,
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
            input_cost_per_1m=0.55,
            output_cost_per_1m=2.19,
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
                "anthropic/claude-haiku-4.5",
                "anthropic/claude-sonnet-5.5",
            ),
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="openrouter",
            model_id="anthropic/claude-haiku-4.5",
            display_name="Anthropic Claude Haiku 4.5 (via OpenRouter)",
            context_window=200000,
            max_output_tokens=8192,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.FAST,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.CHEAP,
            input_cost_per_1m=1.00,
            output_cost_per_1m=5.00,
        )
    )
    registry.register_model(
        ProviderModelInfo(
            provider_id="openrouter",
            model_id="anthropic/claude-sonnet-5.5",
            display_name="Anthropic Claude Sonnet 5.5 (via OpenRouter)",
            context_window=200000,
            max_output_tokens=8192,
            capabilities=frozenset({
                ModelCapability.CODE,
                ModelCapability.ANALYSIS,
                ModelCapability.REVIEW,
                ModelCapability.REASONING,
                ModelCapability.STREAMING,
            }),
            cost_tier=CostTier.PAID,
            input_cost_per_1m=3.00,
            output_cost_per_1m=15.00,
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

    # 9. Radox AI (OpenAI-compatible API at https://radoxai.com/v1)
    #
    # One row per model of the owner's curated 10-model set, generated from
    # ``radox_catalogue.CURATED_CATALOGUE`` -- the same tuple
    # ``RadoxProvider.MODEL_IDS`` comes from, so the provider's list and these
    # rows cannot drift apart. The owner pruned the advertised set; the rest of
    # the 33-model catalogue is kept in ``radox_catalogue.CATALOGUE`` as an
    # archive and is deliberately not registered here.
    #
    # Four things are deliberately kept separate from the catalogue facts:
    #
    # * context window, input and output price per 1M tokens are the owner's
    #   figures, and every row's ``metadata["limits_source"]`` says where they
    #   came from. They are a declaration of 2026-10-18, not a probed fact;
    # * ``max_output_tokens`` is absent from the catalogue, so the rows keep the
    #   ``ProviderModelInfo`` default (4096) instead of an invented figure;
    # * ``cost_tier`` is PAID for every row: a price above zero must never enter
    #   automatic routing while ``allow_paid_providers`` is false;
    # * the technical capabilities are the catalogue's own tags. ``web`` has no
    #   ``ModelCapability`` member, so it is preserved in the catalogue data and
    #   not invented here. The ``code``/``analysis``/``review`` task intents are
    #   declared on every row because ``capabilities.py`` already declares those
    #   task categories for the provider as a whole.
    _radox_catalogue_metadata = {"limits_source": RADOX_SOURCE_NOTE}
    _radox_task_intents = frozenset({
        ModelCapability.CODE,
        ModelCapability.ANALYSIS,
        ModelCapability.REVIEW,
    })
    _radox_capability_map = {
        "reasoning": ModelCapability.REASONING,
        "vision": ModelCapability.VISION,
        "tools": ModelCapability.TOOL_CALLING,
        "streaming": ModelCapability.STREAMING,
        "json": ModelCapability.STRUCTURED_OUTPUT,
    }
    registry.register_provider(
        ProviderInfo(
            provider_id="radox",
            display_name="Radox AI",
            protocol=ProviderProtocol.OPENAI_CHAT,
            default_base_url="https://radoxai.com/v1",
            default_secret_env="RADOX_API_KEY",
            supported_models=RADOX_MODEL_IDS,
        )
    )
    for _radox_entry in RADOX_CURATED_CATALOGUE:
        registry.register_model(
            ProviderModelInfo(
                provider_id="radox",
                model_id=_radox_entry.model_id,
                display_name=_radox_entry.display_name,
                context_window=_radox_entry.context_window,
                capabilities=_radox_task_intents
                | {
                    _radox_capability_map[tag]
                    for tag in _radox_entry.provider_capabilities
                    if tag in _radox_capability_map
                },
                cost_tier=CostTier.PAID,
                input_cost_per_1m=_radox_entry.input_cost_per_1m,
                output_cost_per_1m=_radox_entry.output_cost_per_1m,
                metadata=_radox_catalogue_metadata,
            )
        )

    # 10. Mock (Offline testing)
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
