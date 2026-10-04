"""Bounded, provider-neutral models for explicitly assembled run context and decision envelopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Optional, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from app.projects.state import ProjectState


class ContextSourceType(str, Enum):
    """Explicit provenance origin for a context item."""

    TASK_SPECIFICATION = "TASK_SPECIFICATION"
    PROJECT_STATE = "PROJECT_STATE"
    REQUIREMENT = "REQUIREMENT"
    VERIFICATION = "VERIFICATION"
    ACCEPTANCE = "ACCEPTANCE"
    REVISION = "REVISION"
    RUN_TRACE = "RUN_TRACE"
    DECISION = "DECISION"
    USER_DECISION = "USER_DECISION"
    SYSTEM_POLICY = "SYSTEM_POLICY"
    SKILL = "SKILL"
    # Legacy backward-compatible types
    USER_TASK = "USER_TASK"
    EXPLICIT_INPUT = "EXPLICIT_INPUT"
    SYSTEM = "SYSTEM"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            normalized = value.strip().upper()
            for member in cls:
                if member.value == normalized or member.name == normalized:
                    return member
        return None


ContextSource = ContextSourceType


class ContextTrustLevel(str, Enum):
    """Trust categorization based on origin and verification status."""

    VERIFIED = "VERIFIED"
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    UNVERIFIED = "UNVERIFIED"
    # Legacy backward-compatible levels
    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            normalized = value.strip().upper()
            for member in cls:
                if member.value == normalized or member.name == normalized:
                    return member
        return None


ContextTrust = ContextTrustLevel


class ContextFreshness(str, Enum):
    """Freshness of the item relative to current execution attempt."""

    CURRENT = "CURRENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            normalized = value.strip().upper()
            for member in cls:
                if member.value == normalized or member.name == normalized:
                    return member
        return None


class ContextSensitivity(str, Enum):
    """Sensitivity tier for information exposure control."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    RESTRICTED = "RESTRICTED"
    CONFIDENTIAL = "CONFIDENTIAL"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            normalized = value.strip().upper()
            for member in cls:
                if member.value == normalized or member.name == normalized:
                    return member
        return None


@dataclass(frozen=True)
class ContextItem:
    """One bounded, immutable context value with explicit provenance and sensitivity metadata."""

    item_id: str
    item_type: str
    source_type: ContextSourceType
    value: str
    trust_level: ContextTrustLevel = ContextTrustLevel.CONFIRMED
    freshness: ContextFreshness = ContextFreshness.CURRENT
    sensitivity: ContextSensitivity = ContextSensitivity.INTERNAL
    source_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __init__(
        self,
        item_id: str | None = None,
        item_type: str | None = None,
        source_type: ContextSourceType | str | None = None,
        value: str | None = None,
        trust_level: ContextTrustLevel | str | None = None,
        freshness: ContextFreshness | str | None = None,
        sensitivity: ContextSensitivity | str | None = None,
        source_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
        *,
        id: str | None = None,
        kind: str | None = None,
        content: str | None = None,
        source: ContextSourceType | str | None = None,
        trust: ContextTrustLevel | str | None = None,
    ) -> None:
        eff_id = item_id if item_id is not None else id
        eff_type = item_type if item_type is not None else kind
        eff_val = value if value is not None else content
        eff_source = source_type if source_type is not None else source
        eff_trust = trust_level if trust_level is not None else trust

        if not eff_id or not isinstance(eff_id, str) or not eff_id.strip():
            raise ValueError("context item id must not be empty")
        if not eff_type or not isinstance(eff_type, str) or not eff_type.strip():
            raise ValueError("context item kind must not be empty")
        if eff_val is None or not isinstance(eff_val, str):
            raise ValueError("context item content must be text")
        if eff_source is None:
            raise ValueError("context item source must be provided")
        if isinstance(eff_source, str):
            eff_source = ContextSourceType(eff_source)
        if eff_trust is None:
            eff_trust = ContextTrustLevel.CONFIRMED
        elif isinstance(eff_trust, str):
            eff_trust = ContextTrustLevel(eff_trust)
        eff_freshness = freshness or ContextFreshness.CURRENT
        if isinstance(eff_freshness, str):
            eff_freshness = ContextFreshness(eff_freshness)
        eff_sensitivity = sensitivity or ContextSensitivity.INTERNAL
        if isinstance(eff_sensitivity, str):
            eff_sensitivity = ContextSensitivity(eff_sensitivity)

        object.__setattr__(self, "item_id", str(eff_id))
        object.__setattr__(self, "item_type", str(eff_type))
        object.__setattr__(self, "source_type", eff_source)
        object.__setattr__(self, "value", str(eff_val))
        object.__setattr__(self, "trust_level", eff_trust)
        object.__setattr__(self, "freshness", eff_freshness)
        object.__setattr__(self, "sensitivity", eff_sensitivity)
        object.__setattr__(self, "source_id", str(source_id) if source_id is not None else None)
        object.__setattr__(self, "metadata", dict(metadata) if metadata else {})

    @property
    def id(self) -> str:
        return self.item_id

    @property
    def kind(self) -> str:
        return self.item_type

    @property
    def content(self) -> str:
        return self.value

    @property
    def source(self) -> ContextSourceType:
        return self.source_type

    @property
    def trust(self) -> ContextTrustLevel:
        return self.trust_level

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "source_type": self.source_type.value,
            "value": self.value,
            "trust_level": self.trust_level.value,
            "freshness": self.freshness.value,
            "sensitivity": self.sensitivity.value,
            "source_id": self.source_id,
            "metadata": dict(self.metadata),
            # Legacy compatibility keys:
            "id": self.item_id,
            "kind": self.item_type,
            "content": self.value,
            "source": self.source_type.value,
            "trust": self.trust_level.value,
        }


@dataclass(frozen=True)
class ExecutionContext:
    """The bounded context assembled for one Run (legacy model)."""

    run_id: str
    items: tuple[ContextItem, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "items": [item.to_dict() for item in self.items]}

    @property
    def fingerprint(self) -> str:
        canonical_items = json.dumps(
            [item.to_dict() for item in self.items],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical_items.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TraceSummary:
    """Bounded, safe summary of RunTrace events without dumping payloads."""

    event_count: int = 0
    latest_event_types: tuple[str, ...] = ()
    latest_sequence_number: int | None = None
    latest_decision_reference: str | None = None
    latest_verification_reference: str | None = None
    latest_acceptance_reference: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.latest_event_types, list):
            object.__setattr__(self, "latest_event_types", tuple(self.latest_event_types))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "latest_event_types": list(self.latest_event_types),
            "latest_sequence_number": self.latest_sequence_number,
            "latest_decision_reference": self.latest_decision_reference,
            "latest_verification_reference": self.latest_verification_reference,
            "latest_acceptance_reference": self.latest_acceptance_reference,
        }


@dataclass(frozen=True)
class DecisionContextEnvelope:
    """Bounded, immutable, provenance-aware context object for Decision Layer."""

    context_id: str
    run_id: str
    attempt_number: int
    task_id: str | None = None
    project_state_status: str | None = None
    requirements_summary: Mapping[str, object] = field(default_factory=dict)
    acceptance_summary: Mapping[str, object] = field(default_factory=dict)
    verification_summary: Mapping[str, object] = field(default_factory=dict)
    revision_summary: Mapping[str, object] = field(default_factory=dict)
    blocking_conditions: tuple[str, ...] = ()
    available_actions: tuple[str, ...] = ()
    trace_summary: TraceSummary | None = None
    context_items: tuple[ContextItem, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    project_state: Any | None = None

    def __post_init__(self) -> None:
        if not self.context_id or not isinstance(self.context_id, str):
            raise ValueError("context_id must be a non-empty string")
        if not self.run_id or not isinstance(self.run_id, str):
            raise ValueError("run_id must be a non-empty string")
        if self.attempt_number < 0:
            raise ValueError(f"attempt_number cannot be negative: {self.attempt_number}")
        if isinstance(self.blocking_conditions, (list, set)):
            object.__setattr__(self, "blocking_conditions", tuple(sorted(self.blocking_conditions)))
        if isinstance(self.available_actions, (list, set)):
            object.__setattr__(self, "available_actions", tuple(self.available_actions))
        if isinstance(self.context_items, list):
            object.__setattr__(self, "context_items", tuple(self.context_items))
        if isinstance(self.requirements_summary, dict):
            object.__setattr__(self, "requirements_summary", dict(self.requirements_summary))
        if isinstance(self.acceptance_summary, dict):
            object.__setattr__(self, "acceptance_summary", dict(self.acceptance_summary))
        if isinstance(self.verification_summary, dict):
            object.__setattr__(self, "verification_summary", dict(self.verification_summary))
        if isinstance(self.revision_summary, dict):
            object.__setattr__(self, "revision_summary", dict(self.revision_summary))
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def context_fingerprint(self) -> str:
        """Deterministic SHA-256 hash of structured state, excluding non-deterministic fields."""
        canonical_data = {
            "run_id": self.run_id,
            "attempt_number": self.attempt_number,
            "task_id": self.task_id,
            "project_state_status": self.project_state_status,
            "requirements_summary": dict(self.requirements_summary),
            "acceptance_summary": dict(self.acceptance_summary),
            "verification_summary": dict(self.verification_summary),
            "revision_summary": dict(self.revision_summary),
            "blocking_conditions": sorted(self.blocking_conditions),
            "available_actions": list(self.available_actions),
            "trace_summary": self.trace_summary.to_dict() if self.trace_summary else None,
            "context_items": [
                {
                    "item_id": item.item_id,
                    "item_type": item.item_type,
                    "source_type": item.source_type.value,
                    "value": item.value,
                    "trust_level": item.trust_level.value,
                    "freshness": item.freshness.value,
                    "sensitivity": item.sensitivity.value,
                    "source_id": item.source_id,
                }
                for item in self.context_items
            ],
            "metadata": dict(self.metadata),
        }
        serialized = json.dumps(canonical_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @property
    def item_count(self) -> int:
        return len(self.context_items)

    @property
    def fingerprint(self) -> str:
        return self.context_fingerprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "run_id": self.run_id,
            "attempt_number": self.attempt_number,
            "task_id": self.task_id,
            "project_state_status": self.project_state_status,
            "requirements_summary": dict(self.requirements_summary),
            "acceptance_summary": dict(self.acceptance_summary),
            "verification_summary": dict(self.verification_summary),
            "revision_summary": dict(self.revision_summary),
            "blocking_conditions": list(self.blocking_conditions),
            "available_actions": list(self.available_actions),
            "trace_summary": self.trace_summary.to_dict() if self.trace_summary else None,
            "context_items": [item.to_dict() for item in self.context_items],
            "metadata": dict(self.metadata),
            "context_fingerprint": self.context_fingerprint,
        }
