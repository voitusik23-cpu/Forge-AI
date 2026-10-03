"""Small, provider-neutral models for explicitly assembled run context."""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any


class ContextSource(str, Enum):
    """Origin label for a context item."""

    USER_TASK = "USER_TASK"
    EXPLICIT_INPUT = "EXPLICIT_INPUT"
    SYSTEM = "SYSTEM"


class ContextTrust(str, Enum):
    """Provenance label only; it does not grant tool permissions."""

    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"


class ContextFreshness(str, Enum):
    """Whether the item's source is known to reflect current input."""

    CURRENT = "CURRENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ContextItem:
    """One explicit context value with its provenance and policy metadata."""

    id: str
    kind: str
    content: str
    source: ContextSource
    trust: ContextTrust
    freshness: ContextFreshness

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("context item id must not be empty")
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("context item kind must not be empty")
        if not isinstance(self.content, str):
            raise ValueError("context item content must be text")
        if not isinstance(self.source, ContextSource):
            raise ValueError("context item source must be a ContextSource")
        if not isinstance(self.trust, ContextTrust):
            raise ValueError("context item trust must be a ContextTrust")
        if not isinstance(self.freshness, ContextFreshness):
            raise ValueError("context item freshness must be a ContextFreshness")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for provider-neutral context."""
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "source": self.source.value,
            "trust": self.trust.value,
            "freshness": self.freshness.value,
        }


@dataclass(frozen=True)
class ExecutionContext:
    """The bounded context assembled for one Run."""

    run_id: str
    items: tuple[ContextItem, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for Task.context."""
        return {"run_id": self.run_id, "items": [item.to_dict() for item in self.items]}

    @property
    def fingerprint(self) -> str:
        """Stable SHA-256 of assembled items, excluding the per-Run identifier."""
        canonical_items = json.dumps(
            [item.to_dict() for item in self.items],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical_items.encode("utf-8")).hexdigest()
