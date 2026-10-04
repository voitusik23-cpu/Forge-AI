"""Domain models and contracts for Forge Project Memory v0.1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from app.context.models import ContextSourceType, ContextTrustLevel


class MemoryCategory(str, Enum):
    """Categorization of durable project knowledge in v0.1."""

    FACT = "FACT"
    CONSTRAINT = "CONSTRAINT"
    ASSUMPTION = "ASSUMPTION"
    DECISION_RECORD = "DECISION_RECORD"
    OUTCOME = "OUTCOME"
    LESSON = "LESSON"


class MemoryStatus(str, Enum):
    """Lifecycle status of a project memory item."""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    DEPRECATED = "DEPRECATED"


@dataclass(frozen=True)
class MemoryProvenance:
    """Auditable evidence explaining why Forge holds this memory."""

    source_type: ContextSourceType
    source_id: str
    run_id: str
    actor: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_type": self.source_type.value if hasattr(self.source_type, "value") else str(self.source_type),
            "source_id": self.source_id,
            "run_id": self.run_id,
            "actor": self.actor,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MemoryItem:
    """Immutable, auditable unit of project-scoped memory."""

    memory_id: str
    project_id: str
    category: MemoryCategory
    title: str
    content: str
    provenance: MemoryProvenance
    trust_level: ContextTrustLevel = ContextTrustLevel.CONFIRMED
    status: MemoryStatus = MemoryStatus.ACTIVE
    revision: int = 1
    superseded_by: str | None = None
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.tags, (list, tuple)):
            object.__setattr__(self, "tags", tuple(self.tags))
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "project_id": self.project_id,
            "category": self.category.value if hasattr(self.category, "value") else str(self.category),
            "title": self.title,
            "content": self.content,
            "provenance": self.provenance.to_dict(),
            "trust_level": self.trust_level.value if hasattr(self.trust_level, "value") else str(self.trust_level),
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "revision": self.revision,
            "superseded_by": self.superseded_by,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MemoryQuery:
    """Deterministic, structured query for project memory retrieval."""

    project_id: str
    categories: tuple[MemoryCategory, ...] = ()
    status: MemoryStatus | None = MemoryStatus.ACTIVE
    tags: tuple[str, ...] = ()
    min_trust_level: ContextTrustLevel | None = None
    max_items: int = 10

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError("project_id is required and must be a non-empty string")
        if isinstance(self.categories, (list, tuple)):
            object.__setattr__(self, "categories", tuple(self.categories))
        if isinstance(self.tags, (list, tuple)):
            object.__setattr__(self, "tags", tuple(self.tags))
        if self.max_items <= 0:
            raise ValueError("max_items must be greater than 0")
