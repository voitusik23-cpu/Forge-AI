"""Deterministic ContextSelector, ContextBudgetPolicy, and SelectionReport for Forge AI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
import json
from typing import Any, Optional, TYPE_CHECKING

from app.context.models import (
    ContextFreshness,
    ContextItem,
    ContextSensitivity,
    ContextSourceType,
    ContextTrustLevel,
)
from app.context.validation import FORBIDDEN_CONTEXT_SUBSTRINGS

if TYPE_CHECKING:
    from app.agents.providers.models import ProviderModelInfo


class ContextAssemblyError(ValueError):
    """Raised when explicit context or a decision context envelope cannot be assembled safely."""


class InsufficientContextBudgetError(ContextAssemblyError):
    """Raised when usable input context budget is non-positive or physically insufficient."""


class CriticalContextUnfitError(ContextAssemblyError):
    """Raised when mandatory Tier 1 context items (requirements, acceptance, task spec) cannot fit in budget."""


def estimate_tokens(text: str) -> int:
    """Conservative, deterministic token estimation heuristic.

    Assumes ~3 characters per token on average for code/JSON/mixed English text,
    ensuring we never underestimate token consumption.
    """
    if not text:
        return 0
    return max(1, (len(text) + 2) // 3)


@dataclass(frozen=True)
class ContextBudgetPolicy:
    """Model-aware budget policy defining context limits and headroom bounds."""

    total_context_window: int = 32_768
    max_output_tokens: int = 4_096
    safety_headroom_tokens: int = 2_048
    min_input_budget_tokens: int = 0
    max_input_budget_tokens: int | None = None
    max_items: int = 64
    max_item_characters: int = 4_096
    allow_truncation: bool = True
    min_retained_characters: int = 120
    model_name: Optional[str] = None

    def __post_init__(self) -> None:
        if self.total_context_window < 1:
            raise ValueError("total_context_window must be a positive integer")
        if self.max_output_tokens < 0:
            raise ValueError("max_output_tokens cannot be negative")
        if self.safety_headroom_tokens < 0:
            raise ValueError("safety_headroom_tokens cannot be negative")
        if self.min_input_budget_tokens < 0:
            raise ValueError("min_input_budget_tokens cannot be negative")
        if self.max_items < 1:
            raise ValueError("max_items must be a positive integer")

    @property
    def input_budget_tokens(self) -> int:
        """Calculate usable input budget tokens after reserving output and headroom.

        Invariant: input_budget <= total_context_window - max_output_tokens - safety_headroom_tokens.
        Never artificially inflates budget above physical capacity.
        """
        raw_budget = self.total_context_window - self.max_output_tokens - self.safety_headroom_tokens
        if raw_budget <= 0:
            raise InsufficientContextBudgetError(
                f"Available input budget is non-positive ({raw_budget}): "
                f"total_context_window={self.total_context_window}, "
                f"max_output_tokens={self.max_output_tokens}, "
                f"safety_headroom_tokens={self.safety_headroom_tokens}"
            )
        if self.min_input_budget_tokens > 0 and raw_budget < self.min_input_budget_tokens:
            raise InsufficientContextBudgetError(
                f"Available input budget ({raw_budget}) is below required minimum "
                f"({self.min_input_budget_tokens}): "
                f"total_context_window={self.total_context_window}, "
                f"max_output_tokens={self.max_output_tokens}, "
                f"safety_headroom_tokens={self.safety_headroom_tokens}"
            )
        budget = raw_budget
        if self.max_input_budget_tokens is not None and self.max_input_budget_tokens > 0:
            budget = min(budget, self.max_input_budget_tokens)
        return budget

    @classmethod
    def from_model_info(
        cls,
        model_info: Optional[ProviderModelInfo] = None,
        *,
        safety_headroom_tokens: int = 2_048,
        min_input_budget_tokens: int = 0,
        max_items: int = 64,
        allow_truncation: bool = True,
    ) -> ContextBudgetPolicy:
        """Create a model-aware budget policy from ProviderModelInfo or safe fallback defaults."""
        if model_info is not None:
            ctx_window = getattr(model_info, "context_window", 32_768) or 32_768
            max_out = getattr(model_info, "max_output_tokens", 4_096) or 4_096
            model_id = getattr(model_info, "canonical_id", getattr(model_info, "model_id", None))
            return cls(
                total_context_window=ctx_window,
                max_output_tokens=max_out,
                safety_headroom_tokens=safety_headroom_tokens,
                min_input_budget_tokens=min_input_budget_tokens,
                max_items=max_items,
                allow_truncation=allow_truncation,
                model_name=str(model_id) if model_id else None,
            )
        # Safe default when model is unknown
        return cls(
            total_context_window=32_768,
            max_output_tokens=4_096,
            safety_headroom_tokens=safety_headroom_tokens,
            min_input_budget_tokens=min_input_budget_tokens,
            max_items=max_items,
            allow_truncation=allow_truncation,
            model_name=None,
        )


@dataclass(frozen=True)
class SelectionReport:
    """Audit record detailing context selection, priority scoring, drops, and headroom."""

    total_candidates: int
    selected_count: int
    dropped_count: int
    truncated_count: int
    selected_item_ids: tuple[str, ...]
    dropped_item_ids: tuple[str, ...]
    dropped_reasons: Mapping[str, str] = field(default_factory=dict)
    truncated_item_ids: tuple[str, ...] = field(default_factory=tuple)
    estimated_input_tokens: int = 0
    available_budget_tokens: int = 0
    reserved_headroom_tokens: int = 0
    model_name: Optional[str] = None
    item_scores: Mapping[str, float] = field(default_factory=dict)
    critical_context_unfit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_candidates": self.total_candidates,
            "selected_count": self.selected_count,
            "dropped_count": self.dropped_count,
            "truncated_count": self.truncated_count,
            "selected_item_ids": list(self.selected_item_ids),
            "dropped_item_ids": list(self.dropped_item_ids),
            "dropped_reasons": dict(self.dropped_reasons),
            "truncated_item_ids": list(self.truncated_item_ids),
            "estimated_input_tokens": self.estimated_input_tokens,
            "available_budget_tokens": self.available_budget_tokens,
            "reserved_headroom_tokens": self.reserved_headroom_tokens,
            "model_name": self.model_name,
            "item_scores": dict(self.item_scores),
            "critical_context_unfit": self.critical_context_unfit,
        }


class ContextSelector:
    """Deterministic, model-aware selector for ranking and budgeting candidate context items."""

    # Tier 1 sources: Mission critical, non-evictable and non-truncatable
    _TIER_1_SOURCES: frozenset[ContextSourceType] = frozenset({
        ContextSourceType.TASK_SPECIFICATION,
        ContextSourceType.REQUIREMENT,
        ContextSourceType.ACCEPTANCE,
        ContextSourceType.USER_TASK,
    })

    @classmethod
    def is_tier_1(cls, item: ContextItem) -> bool:
        """Return True if item represents a mandatory mission-critical specification/criterion."""
        if item.source_type in (
            ContextSourceType.TASK_SPECIFICATION,
            ContextSourceType.REQUIREMENT,
            ContextSourceType.ACCEPTANCE,
        ):
            return True
        if item.source_type == ContextSourceType.USER_TASK and item.item_type != "task_context":
            return True
        return False

    # Base score weights by ContextSourceType
    _SOURCE_WEIGHTS: Mapping[ContextSourceType, float] = {
        # Tier 1 - Mission critical: Task, Requirements, Acceptance
        ContextSourceType.TASK_SPECIFICATION: 1000.0,
        ContextSourceType.REQUIREMENT: 950.0,
        ContextSourceType.ACCEPTANCE: 900.0,
        ContextSourceType.USER_TASK: 900.0,
        # Tier 2 - Execution state & Verification
        ContextSourceType.PROJECT_STATE: 800.0,
        ContextSourceType.VERIFICATION: 750.0,
        ContextSourceType.REVISION: 700.0,
        # Tier 3 - Directives & Active Skills
        ContextSourceType.SYSTEM_POLICY: 650.0,
        ContextSourceType.SKILL: 600.0,
        ContextSourceType.DECISION: 550.0,
        ContextSourceType.USER_DECISION: 550.0,
        # Tier 4 - Project Understanding & Project Memory
        ContextSourceType.PROJECT_UNDERSTANDING: 450.0,
        ContextSourceType.PROJECT_MEMORY: 400.0,
        ContextSourceType.EXPLICIT_INPUT: 350.0,
        ContextSourceType.SYSTEM: 350.0,
        # Tier 5 - Reusable Governed Knowledge
        ContextSourceType.FORGE_KNOWLEDGE: 250.0,
        # Tier 6 - Historical Trace
        ContextSourceType.RUN_TRACE: 100.0,
    }

    _TRUST_MULTIPLIERS: Mapping[ContextTrustLevel, float] = {
        ContextTrustLevel.VERIFIED: 1.2,
        ContextTrustLevel.CONFIRMED: 1.0,
        ContextTrustLevel.TRUSTED: 1.2,
        ContextTrustLevel.INFERRED: 0.8,
        ContextTrustLevel.UNVERIFIED: 0.6,
        ContextTrustLevel.UNTRUSTED: 0.6,
    }

    _FRESHNESS_MULTIPLIERS: Mapping[ContextFreshness, float] = {
        ContextFreshness.CURRENT: 1.0,
        ContextFreshness.UNKNOWN: 0.8,
        ContextFreshness.STALE: 0.5,
    }

    # Sources that may be truncated to fit remaining headroom (Tier 3-6)
    _TRUNCATABLE_SOURCES: frozenset[ContextSourceType] = frozenset({
        ContextSourceType.SKILL,
        ContextSourceType.PROJECT_UNDERSTANDING,
        ContextSourceType.PROJECT_MEMORY,
        ContextSourceType.FORGE_KNOWLEDGE,
        ContextSourceType.RUN_TRACE,
        ContextSourceType.EXPLICIT_INPUT,
    })

    def __init__(self, policy: Optional[ContextBudgetPolicy] = None) -> None:
        self.policy = policy or ContextBudgetPolicy()

    def score_item(self, item: ContextItem) -> float:
        """Compute deterministic priority score for a ContextItem."""
        base = self._SOURCE_WEIGHTS.get(item.source_type, 300.0)
        trust_mult = self._TRUST_MULTIPLIERS.get(item.trust_level, 1.0)
        fresh_mult = self._FRESHNESS_MULTIPLIERS.get(item.freshness, 1.0)
        return round(base * trust_mult * fresh_mult, 4)

    def select(
        self,
        candidates: Iterable[ContextItem],
        *,
        budget_policy: Optional[ContextBudgetPolicy] = None,
        raise_on_critical_unfit: bool = True,
    ) -> tuple[tuple[ContextItem, ...], SelectionReport]:
        """Rank candidates, enforce sensitivity boundaries, and pack within budget with headroom."""
        policy = budget_policy or self.policy
        budget_tokens = policy.input_budget_tokens
        max_items = policy.max_items

        candidate_list = list(candidates)
        total_candidates = len(candidate_list)

        dropped_reasons: dict[str, str] = {}
        item_scores: dict[str, float] = {}
        valid_candidates: list[tuple[float, ContextItem]] = []

        # 1. Pre-filter by security & sensitivity
        for item in candidate_list:
            item_id = item.item_id
            sens = item.sensitivity
            sens_val = sens.value if hasattr(sens, "value") else str(sens)
            if sens_val in ("CONFIDENTIAL", "RESTRICTED"):
                dropped_reasons[item_id] = f"security_sensitivity_exclusion:{sens_val}"
                continue

            val_lower = item.value.lower()
            bad_found = next((bad for bad in FORBIDDEN_CONTEXT_SUBSTRINGS if bad in val_lower), None)
            if bad_found:
                dropped_reasons[item_id] = f"security_forbidden_content:{bad_found}"
                continue

            score = self.score_item(item)
            item_scores[item_id] = score
            valid_candidates.append((score, item))

        # 2. Deterministic sorting: Tier 1 items strictly first, then highest score, source_type, item_id
        valid_candidates.sort(
            key=lambda entry: (
                0 if self.is_tier_1(entry[1]) else 1,
                -entry[0],
                entry[1].source_type.value,
                entry[1].item_id,
            )
        )

        selected_items: list[ContextItem] = []
        truncated_item_ids: list[str] = []
        accumulated_tokens = 0

        # 3. Budget packing
        for score, item in valid_candidates:
            item_id = item.item_id
            if len(selected_items) >= max_items:
                dropped_reasons[item_id] = "max_items_budget_exceeded"
                continue

            item_tokens = estimate_tokens(item.value)

            if accumulated_tokens + item_tokens <= budget_tokens:
                selected_items.append(item)
                accumulated_tokens += item_tokens
            elif (
                policy.allow_truncation
                and item.source_type in self._TRUNCATABLE_SOURCES
                and (budget_tokens - accumulated_tokens) >= estimate_tokens(" " * policy.min_retained_characters)
            ):
                remaining_tokens = budget_tokens - accumulated_tokens
                suffix = "\n... [TRUNCATED DUE TO CONTEXT BUDGET]"
                suffix_tokens = estimate_tokens(suffix)
                available_tokens = remaining_tokens - suffix_tokens
                if available_tokens >= estimate_tokens(" " * policy.min_retained_characters):
                    max_chars = available_tokens * 3
                    truncated_val = item.value[:max_chars].rstrip() + suffix
                    truncated_item = replace(item, value=truncated_val)
                    truncated_tokens = estimate_tokens(truncated_val)

                    if accumulated_tokens + truncated_tokens <= budget_tokens:
                        selected_items.append(truncated_item)
                        truncated_item_ids.append(item_id)
                        accumulated_tokens += truncated_tokens
                    else:
                        dropped_reasons[item_id] = "insufficient_headroom_for_truncation"
                else:
                    dropped_reasons[item_id] = "insufficient_headroom_for_truncation"
            else:
                dropped_reasons[item_id] = "context_token_budget_exceeded"

        dropped_item_ids = tuple(
            c.item_id for c in candidate_list if c.item_id in dropped_reasons
        )
        selected_item_ids = tuple(it.item_id for it in selected_items)

        # 4. Check for Tier 1 drop (critical unfit)
        critical_dropped = [
            c.item_id
            for c in candidate_list
            if self.is_tier_1(c) and c.item_id in dropped_reasons
        ]
        critical_context_unfit = len(critical_dropped) > 0

        report = SelectionReport(
            total_candidates=total_candidates,
            selected_count=len(selected_items),
            dropped_count=len(dropped_item_ids),
            truncated_count=len(truncated_item_ids),
            selected_item_ids=selected_item_ids,
            dropped_item_ids=dropped_item_ids,
            dropped_reasons=dropped_reasons,
            truncated_item_ids=tuple(truncated_item_ids),
            estimated_input_tokens=accumulated_tokens,
            available_budget_tokens=budget_tokens,
            reserved_headroom_tokens=policy.safety_headroom_tokens + policy.max_output_tokens,
            model_name=policy.model_name,
            item_scores=item_scores,
            critical_context_unfit=critical_context_unfit,
        )

        if critical_context_unfit and raise_on_critical_unfit:
            first_crit_id = critical_dropped[0]
            reason = dropped_reasons.get(first_crit_id, "unknown")
            raise CriticalContextUnfitError(
                f"Critical Tier 1 context item '{first_crit_id}' cannot fit into available budget "
                f"({budget_tokens} tokens, max_items={max_items}). Reason: {reason}"
            )

        return tuple(selected_items), report
