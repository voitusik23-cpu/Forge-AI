"""Deterministic validation and budget enforcement for Decision Context Envelopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.context.models import ContextItem, DecisionContextEnvelope

MAX_CONTEXT_ITEMS: int = 64
MAX_METADATA_ITEMS: int = 32
MAX_STRING_LENGTH: int = 4096

FORBIDDEN_CONTEXT_SUBSTRINGS: tuple[str, ...] = (
    "stdout",
    "stderr",
    "raw_output",
    "prompt",
    "chain_of_thought",
    "secret",
    "token",
    "password",
    "api_key",
    "credential",
    "source_code",
    "file_content",
)


def sanitize_context_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    """Deterministically sanitize metadata by removing forbidden keys and oversized values."""
    if not metadata:
        return {}
    sanitized: dict[str, object] = {}
    for key, val in metadata.items():
        key_str = str(key).lower().strip()
        if any(bad in key_str for bad in FORBIDDEN_CONTEXT_SUBSTRINGS):
            continue
        if isinstance(val, str) and len(val) > MAX_STRING_LENGTH:
            continue
        if len(sanitized) >= MAX_METADATA_ITEMS:
            break
        sanitized[str(key)] = val
    return sanitized


@dataclass(frozen=True)
class ContextValidationReport:
    """Report detailing validation success and any error codes."""

    valid: bool
    errors: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.valid

    def __iter__(self):
        return iter(self.errors)

    def __len__(self) -> int:
        return len(self.errors)


def validate_decision_context(envelope: DecisionContextEnvelope) -> ContextValidationReport:
    """Deterministically validate a DecisionContextEnvelope against budget and security boundaries.

    Ensures run binding, non-negative attempt numbers, item uniqueness, budget limits,
    absence of sensitive strings, and consistency.
    """
    errors: list[str] = []

    # 1. Core identification
    if not envelope.run_id or not isinstance(envelope.run_id, str):
        errors.append("missing_or_invalid_run_id")
    if envelope.attempt_number < 0:
        errors.append("negative_attempt_number")
    if not envelope.context_id or not isinstance(envelope.context_id, str):
        errors.append("missing_or_invalid_context_id")

    # 2. Context items budget
    if len(envelope.context_items) > MAX_CONTEXT_ITEMS:
        errors.append(f"context_items_budget_exceeded:{len(envelope.context_items)}>{MAX_CONTEXT_ITEMS}")

    # 3. Metadata budget
    if len(envelope.metadata) > MAX_METADATA_ITEMS:
        errors.append(f"metadata_budget_exceeded:{len(envelope.metadata)}>{MAX_METADATA_ITEMS}")

    # 4. Forbidden keys in top-level metadata
    for key in envelope.metadata:
        key_str = str(key).lower().strip()
        for bad in FORBIDDEN_CONTEXT_SUBSTRINGS:
            if bad in key_str:
                errors.append(f"forbidden_metadata_key:{bad}")

    # 5. Cross-run references check in top-level metadata and project_state
    ref_run_id = envelope.metadata.get("run_id")
    if ref_run_id is not None and str(ref_run_id) != envelope.run_id:
        errors.append("cross_run_reference:metadata")
    if envelope.project_state is not None:
        ps_run_id = getattr(envelope.project_state, "run_id", None)
        if ps_run_id is not None and str(ps_run_id) != envelope.run_id:
            errors.append(f"cross_run_reference:project_state:{ps_run_id}")

    # 6. Item-level validation
    seen_ids: set[str] = set()
    for item in envelope.context_items:
        if not item.item_id or not isinstance(item.item_id, str):
            errors.append("invalid_item_id")
            continue
        if item.item_id in seen_ids:
            errors.append(f"duplicate_item_id:{item.item_id}")
        seen_ids.add(item.item_id)

        # String length checks
        if len(item.item_id) > MAX_STRING_LENGTH:
            errors.append(f"oversized_item_id:{item.item_id[:32]}")
        if len(item.item_type) > MAX_STRING_LENGTH:
            errors.append(f"oversized_item_type:{item.item_id}")
        if len(item.value) > MAX_STRING_LENGTH:
            errors.append(f"oversized_item_value:{item.item_id}")

        # Forbidden substrings in item value
        val_lower = item.value.lower()
        for bad in FORBIDDEN_CONTEXT_SUBSTRINGS:
            if bad in val_lower:
                errors.append(f"forbidden_content:{bad}:{item.item_id}")

        # Forbidden keys in item metadata
        for m_key in item.metadata:
            m_key_str = str(m_key).lower().strip()
            for bad in FORBIDDEN_CONTEXT_SUBSTRINGS:
                if bad in m_key_str:
                    errors.append(f"forbidden_item_metadata_key:{bad}:{item.item_id}")

        # Item cross-run reference check
        if item.source_id is not None and ":" in item.source_id:
            parts = item.source_id.split(":")
            if len(parts) >= 2 and parts[0] == "run" and parts[1] != envelope.run_id:
                errors.append(f"cross_run_reference:{item.item_id}")
        item_ref_run = item.metadata.get("run_id")
        if item_ref_run is not None and str(item_ref_run) != envelope.run_id:
            errors.append(f"cross_run_reference:{item.item_id}")

    return ContextValidationReport(valid=len(errors) == 0, errors=tuple(errors))
