"""Domain models and contracts for Forge Knowledge Governance v0.1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any


class KnowledgeScope(str, Enum):
    """Governed scope for reusable knowledge."""

    DOMAIN = "DOMAIN"
    FORGE_GLOBAL = "FORGE_GLOBAL"


class KnowledgeCategory(str, Enum):
    """Functional category of reusable engineering knowledge."""

    BEST_PRACTICE = "BEST_PRACTICE"
    PLATFORM_CONSTRAINT = "PLATFORM_CONSTRAINT"
    PITFALL_AVOIDANCE = "PITFALL_AVOIDANCE"
    DESIGN_PATTERN = "DESIGN_PATTERN"
    WORKAROUND = "WORKAROUND"


class KnowledgeStatus(str, Enum):
    """Lifecycle status of a knowledge item."""

    CANDIDATE = "CANDIDATE"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEPRECATED = "DEPRECATED"
    SUPERSEDED = "SUPERSEDED"


class EvidenceTier(str, Enum):
    """Deterministic, discrete evidence tiers supporting knowledge claims."""

    SINGLE_OBSERVATION = "SINGLE_OBSERVATION"
    REPEATED_OBSERVATION = "REPEATED_OBSERVATION"
    VERIFIED_OBSERVATION = "VERIFIED_OBSERVATION"
    CROSS_PROJECT = "CROSS_PROJECT"
    USER_CONFIRMED = "USER_CONFIRMED"
    EXTERNAL_AUTHORITATIVE = "EXTERNAL_AUTHORITATIVE"


_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.:]+$")


@dataclass(frozen=True)
class KnowledgeApplicability:
    """Structured, deterministic applicability criteria."""

    runtimes: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    operating_systems: tuple[str, ...] = ()
    project_types: tuple[str, ...] = ()
    execution_modes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for attr in ("runtimes", "frameworks", "operating_systems", "project_types", "execution_modes", "tags"):
            val = getattr(self, attr)
            if isinstance(val, (list, tuple)):
                cleaned = tuple(str(x).strip().lower() for x in val if str(x).strip())
                object.__setattr__(self, attr, cleaned)

    def is_empty(self) -> bool:
        """Return True if no applicability constraints are declared."""
        return not (
            self.runtimes
            or self.frameworks
            or self.operating_systems
            or self.project_types
            or self.execution_modes
            or self.tags
        )

    def matches(self, context: Mapping[str, Any]) -> bool:
        """Evaluate whether target context satisfies all non-empty declared dimensions.

        For each non-empty dimension in self, the target context must provide a value
        or collection that intersects with the declared allowed values.
        """
        if self.is_empty():
            return True

        def _get_context_set(key: str) -> set[str]:
            raw = context.get(key)
            if raw is None:
                return set()
            if isinstance(raw, str):
                return {raw.strip().lower()}
            if isinstance(raw, (list, tuple, set, frozenset)):
                return {str(x).strip().lower() for x in raw if str(x).strip()}
            return {str(raw).strip().lower()}

        if self.runtimes:
            target_runtimes = _get_context_set("runtimes") | _get_context_set("runtime")
            if not target_runtimes or not any(r in target_runtimes for r in self.runtimes):
                return False

        if self.frameworks:
            target_frameworks = _get_context_set("frameworks") | _get_context_set("framework")
            if not target_frameworks or not any(f in target_frameworks for f in self.frameworks):
                return False

        if self.operating_systems:
            target_os = _get_context_set("operating_systems") | _get_context_set("os")
            if not target_os or not any(o in target_os for o in self.operating_systems):
                return False

        if self.project_types:
            target_pt = _get_context_set("project_types") | _get_context_set("project_type")
            if not target_pt or not any(p in target_pt for p in self.project_types):
                return False

        if self.execution_modes:
            target_em = _get_context_set("execution_modes") | _get_context_set("execution_mode")
            if not target_em or not any(e in target_em for e in self.execution_modes):
                return False

        if self.tags:
            target_tags = _get_context_set("tags") | _get_context_set("tag")
            if not target_tags or not all(t in target_tags for t in self.tags):
                return False

        return True

    def matches_exception(self, context: Mapping[str, Any]) -> bool:
        """Return True if context triggers any non-empty exception in this specification."""
        if self.is_empty():
            return False

        def _get_context_set(key: str) -> set[str]:
            raw = context.get(key)
            if raw is None:
                return set()
            if isinstance(raw, str):
                return {raw.strip().lower()}
            if isinstance(raw, (list, tuple, set, frozenset)):
                return {str(x).strip().lower() for x in raw if str(x).strip()}
            return {str(raw).strip().lower()}

        if self.runtimes:
            target = _get_context_set("runtimes") | _get_context_set("runtime")
            if any(r in target for r in self.runtimes):
                return True

        if self.frameworks:
            target = _get_context_set("frameworks") | _get_context_set("framework")
            if any(f in target for f in self.frameworks):
                return True

        if self.operating_systems:
            target = _get_context_set("operating_systems") | _get_context_set("os")
            if any(o in target for o in self.operating_systems):
                return True

        if self.project_types:
            target = _get_context_set("project_types") | _get_context_set("project_type")
            if any(p in target for p in self.project_types):
                return True

        if self.execution_modes:
            target = _get_context_set("execution_modes") | _get_context_set("execution_mode")
            if any(e in target for e in self.execution_modes):
                return True

        if self.tags:
            target = _get_context_set("tags") | _get_context_set("tag")
            if any(t in target for t in self.tags):
                return True

        return False

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "runtimes": list(self.runtimes),
            "frameworks": list(self.frameworks),
            "operating_systems": list(self.operating_systems),
            "project_types": list(self.project_types),
            "execution_modes": list(self.execution_modes),
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class KnowledgeEvidence:
    """Discrete evidence supporting a knowledge item."""

    tier: EvidenceTier
    observation_count: int = 1
    corroborating_project_ids: tuple[str, ...] = ()
    verification_ids: tuple[str, ...] = ()
    acceptance_ids: tuple[str, ...] = ()
    external_reference: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.corroborating_project_ids, (list, tuple)):
            object.__setattr__(self, "corroborating_project_ids", tuple(self.corroborating_project_ids))
        if isinstance(self.verification_ids, (list, tuple)):
            object.__setattr__(self, "verification_ids", tuple(self.verification_ids))
        if isinstance(self.acceptance_ids, (list, tuple)):
            object.__setattr__(self, "acceptance_ids", tuple(self.acceptance_ids))
        if self.observation_count < 1:
            raise ValueError("observation_count must be at least 1")

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier.value if hasattr(self.tier, "value") else str(self.tier),
            "observation_count": self.observation_count,
            "corroborating_project_ids": list(self.corroborating_project_ids),
            "verification_ids": list(self.verification_ids),
            "acceptance_ids": list(self.acceptance_ids),
            "external_reference": self.external_reference,
        }


@dataclass(frozen=True)
class KnowledgeProvenance:
    """Auditable evidence origin explaining why Forge records this knowledge."""

    origin_source: str
    source_memory_ids: tuple[str, ...] = ()
    source_run_ids: tuple[str, ...] = ()
    created_by: str = "system"
    created_at: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.source_memory_ids, (list, tuple)):
            object.__setattr__(self, "source_memory_ids", tuple(self.source_memory_ids))
        if isinstance(self.source_run_ids, (list, tuple)):
            object.__setattr__(self, "source_run_ids", tuple(self.source_run_ids))

    def to_dict(self) -> dict[str, object]:
        return {
            "origin_source": self.origin_source,
            "source_memory_ids": list(self.source_memory_ids),
            "source_run_ids": list(self.source_run_ids),
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class KnowledgeReview:
    """Formal audit record of a human or governance review."""

    reviewer: str
    reviewed_at: str
    verdict: KnowledgeStatus
    rationale: str
    evidence_tier_at_review: EvidenceTier

    def __post_init__(self) -> None:
        if not self.reviewer or not isinstance(self.reviewer, str) or not self.reviewer.strip():
            raise ValueError("reviewer must be a non-empty string")
        if not self.reviewed_at or not isinstance(self.reviewed_at, str) or not self.reviewed_at.strip():
            raise ValueError("reviewed_at must be a non-empty string")
        if not self.rationale or not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("rationale must be a non-empty string")
        if self.verdict not in (KnowledgeStatus.APPROVED, KnowledgeStatus.REJECTED):
            raise ValueError(f"Review verdict must be APPROVED or REJECTED, got {self.verdict}")

    def to_dict(self) -> dict[str, object]:
        return {
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "verdict": self.verdict.value if hasattr(self.verdict, "value") else str(self.verdict),
            "rationale": self.rationale,
            "evidence_tier_at_review": (
                self.evidence_tier_at_review.value
                if hasattr(self.evidence_tier_at_review, "value")
                else str(self.evidence_tier_at_review)
            ),
        }


@dataclass(frozen=True)
class KnowledgeItem:
    """Immutable, auditable unit of governed Forge knowledge."""

    knowledge_id: str
    scope: KnowledgeScope
    category: KnowledgeCategory
    statement: str
    rationale: str
    applicability: KnowledgeApplicability
    non_applicability: KnowledgeApplicability = field(default_factory=KnowledgeApplicability)
    provenance: KnowledgeProvenance = field(default_factory=lambda: KnowledgeProvenance("manual"))
    evidence: KnowledgeEvidence = field(
        default_factory=lambda: KnowledgeEvidence(EvidenceTier.SINGLE_OBSERVATION)
    )
    status: KnowledgeStatus = KnowledgeStatus.CANDIDATE
    version: int = 1
    superseded_by: str | None = None
    review: KnowledgeReview | None = None
    domain: str | None = None
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.tags, (list, tuple)):
            object.__setattr__(self, "tags", tuple(self.tags))
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "knowledge_id": self.knowledge_id,
            "scope": self.scope.value if hasattr(self.scope, "value") else str(self.scope),
            "category": self.category.value if hasattr(self.category, "value") else str(self.category),
            "statement": self.statement,
            "rationale": self.rationale,
            "applicability": self.applicability.to_dict(),
            "non_applicability": self.non_applicability.to_dict(),
            "provenance": self.provenance.to_dict(),
            "evidence": self.evidence.to_dict(),
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "version": self.version,
            "superseded_by": self.superseded_by,
            "review": self.review.to_dict() if self.review else None,
            "domain": self.domain,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class KnowledgeQuery:
    """Deterministic, structured query for governed knowledge retrieval."""

    scopes: tuple[KnowledgeScope, ...] = ()
    domain: str | None = None
    categories: tuple[KnowledgeCategory, ...] = ()
    status: KnowledgeStatus | None = KnowledgeStatus.APPROVED
    min_evidence_tier: EvidenceTier | None = None
    applicability_context: Mapping[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    max_items: int = 10

    def __post_init__(self) -> None:
        if isinstance(self.scopes, (list, tuple)):
            object.__setattr__(self, "scopes", tuple(self.scopes))
        if isinstance(self.categories, (list, tuple)):
            object.__setattr__(self, "categories", tuple(self.categories))
        if isinstance(self.tags, (list, tuple)):
            object.__setattr__(self, "tags", tuple(self.tags))
        if self.max_items <= 0:
            raise ValueError("max_items must be greater than 0")
