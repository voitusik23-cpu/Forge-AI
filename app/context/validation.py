"""Deterministic validation and budget enforcement for Decision Context Envelopes."""

from __future__ import annotations

import re
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

#: Field names an operator's engineering text may legitimately mention. The word "token"
#: inside one of these is a *count* or a *limit*, not secret material. Measured: a task
#: description that named ``tokens_in``/``tokens_out`` was refused with
#: ``security_forbidden_content:token``, so the run never reached a provider.
TOKEN_FIELD_NAMES: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "token_usage",
    "token_count",
    "token_budget",
    "token_limit",
    "tokens_in",
    "tokens_out",
    "max_tokens",
    "tokens",
    "tokenizer",
    "tokenization",
)

#: Patterns for actual secret *material*, each paired with the reason word it reports.
#: The reasons stay the existing vocabulary (``token``/``api_key``/``password``/
#: ``secret``/``credential``) so a real positive keeps its existing refusal message.
#: A bare word such as "token" is not material on its own, but these are: a ``Bearer``
#: credential, a JWT, a provider key prefix, and an assigned credential. A long
#: random-looking value is checked separately by :func:`_looks_random`.
_SECRET_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("token", re.compile(r"\bbearer\s+[A-Za-z0-9\-_.=+/]{40,}", re.IGNORECASE)),
    ("token", re.compile(r"\beyJ[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{8,}")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9\-_]{16,}")),
    ("api_key", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    (
        "api_key",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?key|secret[_-]?key|auth[_-]?token)\b"
            r"\s*[:=]\s*\S{8,}"
        ),
    ),
    ("password", re.compile(r"(?i)\bpassw(?:or)?d\b\s*[:=]\s*\S{1,}")),
    ("credential", re.compile(r"(?i)\bcredentials?\b\s*[:=]\s*\S{8,}")),
)

#: A single long, unbroken, machine-looking run of characters: the shape of a generated
#: key. It must be *credential-shaped* -- mixed upper and lower case plus digits -- because
#: a lowercase hex digest is an identifier, not a secret: the product emits workspace
#: fingerprints and snapshot ids of exactly that shape, and flagging those failed every
#: run that assembled an understanding snapshot.
_RANDOM_LOOKING_CANDIDATE = re.compile(r"\b[A-Za-z0-9+/_\-]{40,}={0,2}\b")


def _has_bare_token(text_lower: str) -> bool:
    """Whether "token" appears outside a recognised field name such as ``max_tokens``."""

    scrubbed = text_lower
    for name in sorted(TOKEN_FIELD_NAMES, key=len, reverse=True):
        scrubbed = scrubbed.replace(name, " ")
    return "token" in scrubbed


def _looks_random(value: str) -> bool:
    """Whether ``value`` carries a credential-shaped random run, i.e. key-like material."""

    for candidate in _RANDOM_LOOKING_CANDIDATE.findall(value):
        digits = sum(character.isdigit() for character in candidate)
        if digits < 4:
            continue
        has_lower = any(character.islower() for character in candidate)
        has_upper = any(character.isupper() for character in candidate)
        if has_lower and has_upper:
            return True
    return False


def find_forbidden_value_content(value: str) -> str | None:
    """The forbidden marker present in one context *value*, or ``None`` when it is clean.

    Secret material is refused exactly as before, with the same reason words. The one
    narrowing is that "token" no longer matches when every occurrence is part of a field
    name (``tokens_in``, ``max_tokens``, ...), because describing such a field is normal
    engineering text and not a credential.
    """

    if not isinstance(value, str) or not value:
        return None
    for reason, pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(value):
            return reason
    if _looks_random(value):
        return "secret"
    text_lower = value.lower()
    for bad in FORBIDDEN_CONTEXT_SUBSTRINGS:
        if bad == "token":
            if _has_bare_token(text_lower):
                return "token"
            continue
        if bad in text_lower:
            return bad
    return None


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
        bad_value = find_forbidden_value_content(item.value)
        if bad_value is not None:
            errors.append(f"forbidden_content:{bad_value}:{item.item_id}")

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
