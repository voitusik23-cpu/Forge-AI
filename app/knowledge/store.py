"""Thread-safe, deterministic in-memory store for Forge Knowledge Governance v0.1."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol, runtime_checkable

from app.knowledge.models import (
    EvidenceTier,
    KnowledgeCategory,
    KnowledgeItem,
    KnowledgeQuery,
    KnowledgeScope,
    KnowledgeStatus,
)
from app.knowledge.validator import KnowledgeValidator


_EVIDENCE_RANK: dict[EvidenceTier, int] = {
    EvidenceTier.EXTERNAL_AUTHORITATIVE: 6,
    EvidenceTier.USER_CONFIRMED: 5,
    EvidenceTier.CROSS_PROJECT: 4,
    EvidenceTier.VERIFIED_OBSERVATION: 3,
    EvidenceTier.REPEATED_OBSERVATION: 2,
    EvidenceTier.SINGLE_OBSERVATION: 1,
}


@runtime_checkable
class KnowledgeStore(Protocol):
    """Protocol for governed knowledge persistence and deterministic retrieval."""

    def put(self, item: KnowledgeItem) -> KnowledgeItem:
        """Store or update a knowledge item."""
        ...

    def get(self, knowledge_id: str) -> KnowledgeItem | None:
        """Retrieve a specific knowledge item by ID."""
        ...

    def query(self, query: KnowledgeQuery) -> tuple[KnowledgeItem, ...]:
        """Perform deterministic, bounded retrieval against governed knowledge."""
        ...


class InMemoryKnowledgeStore:
    """Thread-safe in-memory store for governed Forge knowledge.

    Invariants:
    1. Zero vector database / embeddings / semantic ranking.
    2. Default queries strictly return APPROVED knowledge items only.
    3. Retrieval matches positive applicability and excludes negative exceptions.
    4. Deterministic sorting and bounded results.
    """

    def __init__(self, validator: KnowledgeValidator | None = None) -> None:
        self._validator = validator or KnowledgeValidator()
        self._lock = threading.RLock()
        self._storage: dict[str, KnowledgeItem] = {}

    def put(self, item: KnowledgeItem) -> KnowledgeItem:
        """Store a knowledge item after schema validation."""
        self._validator.validate(item)
        with self._lock:
            self._storage[item.knowledge_id] = item
            return item

    def get(self, knowledge_id: str) -> KnowledgeItem | None:
        """Retrieve a knowledge item by ID."""
        if not knowledge_id or not isinstance(knowledge_id, str):
            raise ValueError("knowledge_id must be a non-empty string")
        with self._lock:
            return self._storage.get(knowledge_id)

    def query(self, query: KnowledgeQuery) -> tuple[KnowledgeItem, ...]:
        """Perform deterministic, bounded retrieval against stored knowledge."""
        if not isinstance(query, KnowledgeQuery):
            raise ValueError("query must be a KnowledgeQuery instance")

        with self._lock:
            candidates: list[KnowledgeItem] = []
            for item in self._storage.values():
                # 1. Status filter (default: APPROVED)
                if query.status is not None and item.status != query.status:
                    continue

                # 2. Scope filter
                if query.scopes and item.scope not in query.scopes:
                    continue

                # 3. Domain filter (only for DOMAIN scope items)
                if query.domain and item.scope == KnowledgeScope.DOMAIN:
                    if item.domain != query.domain:
                        continue

                # 4. Category filter
                if query.categories and item.category not in query.categories:
                    continue

                # 5. Tags filter (conjunction: all queried tags must match)
                if query.tags and not all(t in item.tags for t in query.tags):
                    continue

                # 6. Minimum evidence tier
                if query.min_evidence_tier is not None:
                    item_rank = _EVIDENCE_RANK.get(item.evidence.tier, 0)
                    min_rank = _EVIDENCE_RANK.get(query.min_evidence_tier, 0)
                    if item_rank < min_rank:
                        continue

                # 7. Applicability matching (if context provided)
                if query.applicability_context:
                    # Positive match required
                    if not item.applicability.matches(query.applicability_context):
                        continue
                    # Negative exception must NOT match
                    if item.non_applicability.matches_exception(query.applicability_context):
                        continue

                candidates.append(item)

            # Deterministic sorting:
            # 1. Evidence tier rank descending
            # 2. Version descending
            # 3. Provenance created_at descending
            # 4. Knowledge ID ascending
            candidates.sort(
                key=lambda it: (
                    -_EVIDENCE_RANK.get(it.evidence.tier, 0),
                    -it.version,
                    it.provenance.created_at if it.provenance else "",
                    it.knowledge_id,
                )
            )

            # Bounded retrieval
            return tuple(candidates[: query.max_items])
