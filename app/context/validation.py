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

#: The words that mark a *structural* thing a prompt must not carry (a raw stream, a
#: chain of thought, a file body). They stay refused when they appear in a context
#: value, because they are not prose about the work: an item that says "stdout" is a
#: raw stream being pushed into the model's context.
STRUCTURAL_VALUE_MARKERS: tuple[str, ...] = (
    "stdout",
    "stderr",
    "raw_output",
    "prompt",
    "chain_of_thought",
    "source_code",
    "file_content",
)

#: The credential words. Measured defect: a *word* was refused as if it were material.
#: «Секреты нельзя добавлять в проект», a sentence naming an ``API key``, the noun
#: ``credentials``, the word ``password`` in architecture prose, an ``.env.example`` with
#: empty values and a ``Bearer <placeholder>`` are all normal text -- a user task that
#: mentioned them was refused with ``security_forbidden_content:secret``, one event was
#: recorded and the provider was never called.
#:
#: They are therefore no longer matched on their own in a *value*: the words "secret",
#: "password", "api_key" and "credential" name a thing, they are not the thing. They
#: still block a metadata *key*, where they name what is being carried (see
#: ``sanitize_context_metadata``), and each of them still refuses a secret-shaped VALUE
#: through the patterns below.
CREDENTIAL_WORDS: tuple[str, ...] = ("secret", "password", "api_key", "credential")

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

#: patterns for an actual secret *material*, each paired with the reason word it reports.
#: The reasons stay the existing vocabulary (``token``/``api_key``/``password``/
#: ``secret``/``credential``) so a real positive keeps its existing refusal message.
#: A bare word such as "token" is not material on its own, but these are: a ``Bearer``
#: credential, a JWT, a provider key prefix, and an assigned credential. A long
#: random-looking value is checked separately by :func:`_random_match`.
#:
#: Every pattern describes a *value*, never a word:
#:
#: * the assignment patterns require the value on the **same line**, so
#:   ``API_KEY=`` followed by the next line's ``SECRET_KEY=`` is not read as one
#:   assignment (that measure alone refused an ``.env.example`` with empty values);
#: * the value has to have the shape of key material -- 20+ characters, or a shorter
#:   but clearly machine-made string -- so ``API_KEY=your-key-here`` stays an example;
#: * a recognisable placeholder is never material, whatever its length.
_SECRET_VALUE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("bearer_token", "token", re.compile(r"\bbearer\s+[A-Za-z0-9\-_.=+/]{40,}", re.IGNORECASE)),
    ("jwt", "token", re.compile(r"\beyJ[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{8,}")),
    (
        "provider_key_prefix",
        "api_key",
        re.compile(
            r"\b(?:sk|pk|rk)[-_][A-Za-z0-9\-_]{16,}"
            r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"
            r"|\bxox[abposr]-[A-Za-z0-9\-]{10,}"
            r"|\bAKIA[0-9A-Z]{16}\b"
        ),
    ),
    (
        "assigned_api_key",
        "api_key",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?key|secret[_-]?key|auth[_-]?token|client[_-]?secret)"
            r"\b[ \t]*[:=][ \t]*(?P<value>[^\s\"']+)"
        ),
    ),
    (
        "password_assignment",
        "password",
        re.compile(r"(?i)\bpassw(?:or)?d\b[ \t]*[:=][ \t]*(?P<value>[^\s\"']+)"),
    ),
    (
        "credentials_assignment",
        "credential",
        re.compile(r"(?i)\bcredentials?\b[ \t]*[:=][ \t]*(?P<value>[^\s\"']+)"),
    ),
)

#: The alphabet a generated key is drawn from: ASCII letters, digits and the four symbols
#: base64 / url-safe base64 add. **Nothing else can be part of a high-entropy candidate.**
#:
#: MEASURED DEFECT (the owner's own change request). A normal Russian sentence --
#: «я хочу добавить в проект еще пароль от почты и если возможно что б была возможность
#: выбирать …» -- was refused as ``security_forbidden_content:secret`` with
#: ``rule=high_entropy`` and the run never reached a provider. The rule was a bare
#: "a long run of token characters" regex over the whole value, so any text the regex
#: happened to find a long run in could be read as a key.
#:
#: The rule is therefore **token-scoped**: a candidate is one *whitespace-free run* whose
#: characters are all from this alphabet. A sentence can never produce one -- its words are
#: separated by spaces, and Russian words are not in this alphabet at all -- while a real
#: key is exactly one such run, even when it sits inside a sentence.
TOKEN_ALPHABET: frozenset[str] = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-"
)

#: The shortest run that can be a generated key. Shorter runs are identifiers, field names
#: and words; a generated key is at least this long.
_HIGH_ENTROPY_MIN_LENGTH = 32

#: Characters that may *wrap* a token in ordinary text but are not part of it. They are
#: trimmed from both ends of a run before the alphabet test, so «ключ», ``(token)`` and
#: ``key,`` still count as a standalone token, while a run whose *middle* is not token
#: alphabet (Cyrillic prose, a Windows path, a quoted phrase) never does.
_RUN_TRIM_CHARACTERS = (
    " \t\r\n\f\v"
    "«»\"'“”„‘’….,;:!?()[]{}<>|\\`~*#@$%^&"
)


def is_prose(value: str) -> bool:
    """Whether a value reads as human text rather than as a single token.

    Prose is the shape this rule must never refuse: two or more words, or a sentence with
    punctuation. The high-entropy rule itself is *run-scoped* (see
    :data:`TOKEN_ALPHABET`), so prose produces no candidate at all; this predicate names
    the invariant and is what the regression tests assert against.
    """

    words = value.split()
    if len(words) >= 2:
        return True
    return any(character in value for character in "«»…")


def _token_candidates(value: str) -> list[str]:
    """Every whitespace-free run of ``value`` that could be a generated key.

    A run qualifies only when it is at least :data:`_HIGH_ENTROPY_MIN_LENGTH` characters
    long and **every** character is from :data:`TOKEN_ALPHABET` after trimming the
    punctuation that may wrap it. Prose yields nothing: its words are separate runs, and a
    Russian word is not in the alphabet at all.
    """

    candidates: list[str] = []
    for run in value.split():
        candidate = run.strip(_RUN_TRIM_CHARACTERS)
        if len(candidate) < _HIGH_ENTROPY_MIN_LENGTH:
            continue
        if any(character not in TOKEN_ALPHABET for character in candidate):
            continue
        candidates.append(candidate)
    return candidates


#: A value that names no secret at all: an angle/quoted placeholder, a repeat of one
#: character, an ellipsis, or a word that says "put your own value here". Such a value is
#: documentation, so an assignment carrying it stays admissible.
_PLACEHOLDER_VALUE_MARKERS: tuple[str, ...] = (
    "<",
    ">",
    "...",
    "${",
    "%",
    "your",
    "example",
    "placeholder",
    "change_me",
    "changeme",
    "dummy",
    "sample",
    "redacted",
    "todo",
    "tbd",
)

#: A short value still counts as material when it is machine-made: at least one digit and
#: at least one letter, which no ordinary English or Russian word has.
_SHORT_VALUE_MIN_LENGTH = 20
_SHORT_VALUE_CREDENTIAL_SHAPED_LENGTH = 8


def _has_bare_token(text_lower: str) -> bool:
    """Whether "token" appears as a word of its own, outside a field name.

    Two narrowings, both measured. A recognised field name (``max_tokens``,
    ``tokens_in``) is scrubbed first, and the remaining match must then be a standalone
    word: an identifier such as ``AUTH_TOKEN`` or ``FORGE_TOKEN`` names a *variable*, so
    an ``.env.example`` that documents it -- with an empty or placeholder value -- is not
    a credential. A sentence such as "the token is rotated nightly" still is prose about
    a credential and keeps its refusal.
    """

    scrubbed = text_lower
    for name in sorted(TOKEN_FIELD_NAMES, key=len, reverse=True):
        scrubbed = scrubbed.replace(name, " ")
    return re.search(r"(?<![a-z0-9_])tokens?(?![a-z0-9_])", scrubbed) is not None


def _random_match(value: str) -> str | None:
    """The credential-shaped random run inside ``value``, or ``None``.

    Only a *token-shaped* run can qualify (see :func:`_token_candidates`), and it must be
    machine-made: mixed upper and lower case plus at least four digits. A lowercase hex
    digest is an identifier, not a secret -- the product emits workspace fingerprints and
    snapshot ids of exactly that shape, and flagging those failed every run that assembled
    an understanding snapshot.
    """

    for candidate in _token_candidates(value):
        digits = sum(character.isdigit() for character in candidate)
        if digits < 4:
            continue
        has_lower = any(character.islower() for character in candidate)
        has_upper = any(character.isupper() for character in candidate)
        if has_lower and has_upper:
            return candidate
    return None


def _has_bare_marker(text_lower: str, marker: str) -> bool:
    """Whether a structural marker appears as a word of its own."""

    if marker == "raw_output":
        return "raw_output" in text_lower or "raw output" in text_lower
    if marker == "chain_of_thought":
        return "chain_of_thought" in text_lower or "chain of thought" in text_lower
    if marker == "source_code":
        return "source_code" in text_lower or "source code" in text_lower
    if marker == "file_content":
        return "file_content" in text_lower or "file content" in text_lower
    pattern = rf"(?<![a-z0-9_]){re.escape(marker)}(?![a-z0-9_])"
    return re.search(pattern, text_lower) is not None


def _is_placeholder_value(value: str) -> bool:
    """Whether an assigned value is documentation rather than material."""

    lowered = value.strip().strip("\"'").casefold()
    if not lowered:
        return True
    if len(set(lowered)) == 1:
        # "xxx", "***", "---": one repeated character is never a key.
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_VALUE_MARKERS)


def _is_material_value(value: str) -> bool:
    """Whether a value that follows ``=``/``:`` is secret-shaped rather than a placeholder."""

    if _is_placeholder_value(value):
        return False
    if len(value) >= _SHORT_VALUE_MIN_LENGTH:
        return True
    if len(value) < _SHORT_VALUE_CREDENTIAL_SHAPED_LENGTH:
        return False
    has_digit = any(character.isdigit() for character in value)
    has_letter = any(character.isalpha() for character in value)
    return has_digit and has_letter


def length_bucket(length: int) -> str:
    """A coarse, content-free size class for a refused fragment.

    A bucket is a length *range*, never a value and never a prefix, so an operator learns
    what kind of thing tripped the rule without the refused text being recoverable.
    """

    if length <= 0:
        return "0"
    if length < 8:
        return "1-7"
    if length < 20:
        return "8-19"
    if length < 40:
        return "20-39"
    if length <= 64:
        return "40-64"
    return "65+"


@dataclass(frozen=True)
class SecretFinding:
    """One deterministic classification: which rule fired, and which reason it reports.

    ``reason`` keeps the refusal vocabulary the product already publishes
    (``token``/``api_key``/``password``/``secret``/``credential`` plus the structural
    markers), so an existing positive keeps its existing message. ``rule`` is a stable,
    human-readable rule id for diagnostics. Nothing here carries the refused text: only
    a coarse ``length_bucket`` survives, and the fragment itself is discarded.
    """

    reason: str
    rule: str
    length_bucket: str = "0"

    def describe(self, location: str = "context_item") -> str:
        """A human sentence plus a content-free hint, safe to log and to show.

        The sentence never contains the refused fragment, not even a prefix: only the
        rule id, a length *bucket* and where the text came from.
        """

        return (
            "Текст содержит значение, похожее на секрет "
            f"(rule={self.rule}, length_bucket={self.length_bucket}, location={location})"
        )


def scan_value_content(value: object) -> SecretFinding | None:
    """Classify one context value, or return ``None`` when nothing fires.

    The security boundary is unchanged where it matters: every secret-shaped VALUE is
    still refused, with the reason word it always had. What changed is the opposite
    direction -- a word that merely *names* a credential is not a credential, so prose,
    documentation and an ``.env.example`` with empty values are admissible.

    The ``high_entropy`` rule is **token-scoped**: it fires on a single whitespace-free run
    of :data:`TOKEN_ALPHABET` characters that is machine-made, never on a sentence. The
    owner's own change request is prose and carries no material, so it produces no finding
    at all (see the measured defect beside :data:`TOKEN_ALPHABET`).
    """

    if not isinstance(value, str) or not value:
        return None
    for rule, reason, pattern in _SECRET_VALUE_PATTERNS:
        match = pattern.search(value)
        if match is None:
            continue
        candidate = match.group("value") if "value" in pattern.groupindex else match.group(0)
        if "value" in pattern.groupindex and not _is_material_value(candidate):
            continue
        return SecretFinding(
            reason=reason,
            rule=rule,
            length_bucket=length_bucket(len(candidate)),
        )
    random_run = _random_match(value)
    if random_run is not None:
        return SecretFinding(
            reason="secret",
            rule="high_entropy",
            length_bucket=length_bucket(len(random_run)),
        )
    text_lower = value.lower()
    for marker in STRUCTURAL_VALUE_MARKERS:
        if _has_bare_marker(text_lower, marker):
            return SecretFinding(
                reason=marker,
                rule=f"marker_{marker}",
                length_bucket=length_bucket(len(value)),
            )
    if _has_bare_token(text_lower):
        return SecretFinding(
            reason="token",
            rule="bare_token_word",
            length_bucket=length_bucket(len(value)),
        )
    return None


def location_for_item(item: object) -> str:
    """Where a refused value came from, for the diagnostic. Never the value itself."""

    item_type = str(getattr(item, "item_type", "") or "").lower()
    if item_type in {"user_task", "task_description"}:
        return "task_description"
    if item_type == "task_context":
        return "task_context"
    return "context_item"


def find_forbidden_value_content(value: str) -> str | None:
    """The forbidden marker present in one context *value*, or ``None`` when it is clean.

    Secret material is refused exactly as before, with the same reason words. Two
    narrowings apply, both measured:

    * "token" no longer matches when every occurrence is part of a field name
      (``tokens_in``, ``max_tokens``, ...), because describing such a field is normal
      engineering text and not a credential;
    * the credential nouns (``secret``, ``password``, ``api_key``, ``credential``) no
      longer match on their own. A sentence that *names* a credential used to refuse the
      whole user task before dispatch; a secret-shaped value still refuses it.
    """

    finding = scan_value_content(value)
    return finding.reason if finding is not None else None



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
