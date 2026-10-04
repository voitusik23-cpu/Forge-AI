"""Deterministic, thread-safe in-memory store for Project Memory v0.1."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol, runtime_checkable

from app.context.models import ContextTrustLevel
from app.memory.models import (
    MemoryCategory,
    MemoryItem,
    MemoryQuery,
    MemoryStatus,
)
from app.memory.validator import MemoryValidator


from app.orchestrator.models import EventType


_TRUST_RANK: dict[ContextTrustLevel, int] = {
    ContextTrustLevel.VERIFIED: 4,
    ContextTrustLevel.CONFIRMED: 3,
    ContextTrustLevel.INFERRED: 2,
    ContextTrustLevel.UNVERIFIED: 1,
    ContextTrustLevel.TRUSTED: 4,
    ContextTrustLevel.UNTRUSTED: 1,
}


@runtime_checkable
class MemoryStore(Protocol):
    """Protocol for project-scoped memory persistence and deterministic retrieval."""

    def put(self, item: MemoryItem, collector: Any | None = None) -> MemoryItem:
        """Store a new memory item after validating structural and security invariants."""
        ...

    def get(self, project_id: str, memory_id: str) -> MemoryItem | None:
        """Retrieve a specific memory item within a project boundary."""
        ...

    def supersede(
        self,
        project_id: str,
        old_memory_id: str,
        new_item: MemoryItem,
        collector: Any | None = None,
    ) -> MemoryItem:
        """Supersede an existing memory item with an updated revision."""
        ...

    def query(self, query: MemoryQuery) -> tuple[MemoryItem, ...]:
        """Perform deterministic, bounded retrieval against project memory."""
        ...

    def get_active_memory(
        self,
        project_id: str,
        limit: int = 20,
    ) -> tuple[MemoryItem, ...]:
        """Retrieve active memories for a project up to limit."""
        ...


class InMemoryMemoryStore:
    """Thread-safe, strictly partitioned in-memory store for Project Memory."""

    def __init__(self, validator: MemoryValidator | None = None) -> None:
        self._validator = validator or MemoryValidator()
        self._lock = threading.RLock()
        self._storage: dict[str, dict[str, MemoryItem]] = {}

    def put(self, item: MemoryItem, collector: Any | None = None) -> MemoryItem:
        """Store a new memory item. Validates schema and secret bounds."""
        self._validator.validate(item)
        with self._lock:
            if item.project_id not in self._storage:
                self._storage[item.project_id] = {}
            self._storage[item.project_id][item.memory_id] = item
            if collector is not None and hasattr(collector, "emit"):
                collector.emit(
                    EventType.MEMORY_RECORDED,
                    metadata={
                        "memory_id": item.memory_id,
                        "category": item.category.value if hasattr(item.category, "value") else str(item.category),
                        "project_id": item.project_id,
                        "title": item.title,
                        "revision": item.revision,
                    },
                )
            return item

    def record(self, item: MemoryItem, collector: Any | None = None) -> MemoryItem:
        """Convenience alias for put."""
        return self.put(item, collector=collector)

    def get(self, project_id: str, memory_id: str) -> MemoryItem | None:
        """Retrieve a specific memory item strictly within the specified project."""
        if not project_id or not isinstance(project_id, str):
            raise ValueError("project_id must be a non-empty string")
        with self._lock:
            return self._storage.get(project_id, {}).get(memory_id)

    def supersede(
        self,
        project_id: str,
        old_memory_id: str,
        new_item: MemoryItem,
        collector: Any | None = None,
    ) -> MemoryItem:
        """Supersede an existing memory item with an updated revision."""
        if not project_id or not isinstance(project_id, str):
            raise ValueError("project_id must be a non-empty string")

        with self._lock:
            proj_dict = self._storage.get(project_id)
            if not proj_dict or old_memory_id not in proj_dict:
                raise KeyError(f"Memory item {old_memory_id} not found in project {project_id}")

            old_item = proj_dict[old_memory_id]
            if old_item.status == MemoryStatus.SUPERSEDED:
                raise ValueError(f"Memory item {old_memory_id} is already superseded")

            # Prepare new item with incremented revision and project binding
            updated_new = replace(
                new_item,
                project_id=project_id,
                revision=max(new_item.revision, old_item.revision + 1),
                status=MemoryStatus.ACTIVE,
                superseded_by=None,
            )
            self._validator.validate(updated_new)

            # Mark old item as superseded
            superseded_old = replace(
                old_item,
                status=MemoryStatus.SUPERSEDED,
                superseded_by=updated_new.memory_id,
            )
            self._validator.validate(superseded_old)

            proj_dict[old_memory_id] = superseded_old
            proj_dict[updated_new.memory_id] = updated_new
            if collector is not None and hasattr(collector, "emit"):
                collector.emit(
                    EventType.MEMORY_REVISED,
                    metadata={
                        "old_memory_id": old_memory_id,
                        "new_memory_id": updated_new.memory_id,
                        "revision": updated_new.revision,
                        "project_id": project_id,
                    },
                )
            return updated_new

    def get_active_memory(
        self,
        project_id: str,
        limit: int = 20,
    ) -> tuple[MemoryItem, ...]:
        """Convenience method to retrieve active memories for a project."""
        return self.query(
            MemoryQuery(
                project_id=project_id,
                status=MemoryStatus.ACTIVE,
                max_items=limit,
            )
        )

    def query(self, query: MemoryQuery) -> tuple[MemoryItem, ...]:
        """Perform deterministic, bounded retrieval against project memory.

        Enforces:
        1. project_id isolation (zero cross-project leakage)
        2. Explicit filtering (category, status, tags, trust)
        3. Deterministic sort order
        4. Bounded result count
        """
        if not isinstance(query, MemoryQuery):
            raise ValueError("query must be a MemoryQuery instance")

        with self._lock:
            proj_dict = self._storage.get(query.project_id)
            if not proj_dict:
                return ()

            candidates: list[MemoryItem] = []
            for item in proj_dict.values():
                # 1. Status filter
                if query.status is not None and item.status != query.status:
                    continue

                # 2. Category filter
                if query.categories and item.category not in query.categories:
                    continue

                # 3. Tags filter (all queried tags must match)
                if query.tags and not all(t in item.tags for t in query.tags):
                    continue

                # 4. Minimum trust level filter
                if query.min_trust_level is not None:
                    item_rank = _TRUST_RANK.get(item.trust_level, 0)
                    min_rank = _TRUST_RANK.get(query.min_trust_level, 0)
                    if item_rank < min_rank:
                        continue

                candidates.append(item)

            # Deterministic sort order: revision descending, provenance created_at descending, memory_id ascending
            candidates.sort(
                key=lambda it: (
                    -it.revision,
                    it.provenance.created_at if it.provenance else "",
                    it.memory_id,
                )
            )

            # Bounded retrieval
            bounded = candidates[:query.max_items]
            return tuple(bounded)
