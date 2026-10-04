"""Knowledge governance state machine, promotion rules, and audit lifecycle."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.knowledge.models import (
    EvidenceTier,
    KnowledgeApplicability,
    KnowledgeCategory,
    KnowledgeEvidence,
    KnowledgeItem,
    KnowledgeProvenance,
    KnowledgeReview,
    KnowledgeScope,
    KnowledgeStatus,
)
from app.knowledge.validator import KnowledgeValidator, KnowledgeValidationError
from app.orchestrator.models import EventType
from app.orchestrator.trace import sanitize_event_metadata


class KnowledgeGovernanceError(ValueError):
    """Raised when an invalid state transition or promotion threshold violation occurs."""


class KnowledgeGovernanceEngine:
    """Manages the governed lifecycle of reusable engineering knowledge.

    Invariants:
    1. Knowledge is information, not authority.
    2. Automatic promotion is strictly impossible.
    3. Promotion to DOMAIN or FORGE_GLOBAL requires explicit review and evidence thresholds.
    4. Project Memory never automatically becomes global knowledge.
    """

    def __init__(
        self,
        store: Any,
        validator: KnowledgeValidator | None = None,
    ) -> None:
        self._store = store
        self._validator = validator or KnowledgeValidator()

    def propose_candidate(
        self,
        item: KnowledgeItem,
        collector: Any | None = None,
    ) -> KnowledgeItem:
        """Propose a new candidate knowledge item.

        Status is forced to CANDIDATE. Automatic promotion is forbidden.
        """
        candidate = replace(
            item,
            status=KnowledgeStatus.CANDIDATE,
            version=1,
            superseded_by=None,
            review=None,
        )
        self._validator.validate(candidate)
        saved = self._store.put(candidate)

        if collector is not None and hasattr(collector, "emit"):
            collector.emit(
                EventType.KNOWLEDGE_PROPOSED,
                metadata=sanitize_event_metadata({
                    "knowledge_id": saved.knowledge_id,
                    "scope": saved.scope.value,
                    "category": saved.category.value,
                    "statement": saved.statement,
                }),
            )
        return saved

    def extract_candidate_from_memory(
        self,
        memory_item: Any,
        *,
        statement: str,
        rationale: str,
        scope: KnowledgeScope,
        applicability: KnowledgeApplicability,
        category: KnowledgeCategory,
        domain: str | None = None,
        non_applicability: KnowledgeApplicability | None = None,
        created_by: str = "governance_agent",
        collector: Any | None = None,
    ) -> KnowledgeItem:
        """Explicitly extract a candidate knowledge item from a project memory.

        Crucial boundary: Sets status to CANDIDATE. Does NOT auto-promote.
        Preserves provenance to memory_id and project_id while generalizing statement.
        """
        mem_id = getattr(memory_item, "memory_id", str(uuid4()))
        proj_id = getattr(memory_item, "project_id", "unknown_project")
        prov_obj = getattr(memory_item, "provenance", None)
        run_id = getattr(prov_obj, "run_id", "") if prov_obj else ""

        candidate = KnowledgeItem(
            knowledge_id=f"k-{uuid4()}",
            scope=scope,
            domain=domain,
            category=category,
            statement=statement,
            rationale=rationale,
            applicability=applicability,
            non_applicability=non_applicability or KnowledgeApplicability(),
            provenance=KnowledgeProvenance(
                origin_source="project_memory",
                source_memory_ids=(mem_id,),
                source_run_ids=(run_id,) if run_id else (),
                created_by=created_by,
                created_at=datetime.now(timezone.utc).isoformat(),
            ),
            evidence=KnowledgeEvidence(
                tier=EvidenceTier.SINGLE_OBSERVATION,
                observation_count=1,
                corroborating_project_ids=(proj_id,),
            ),
            status=KnowledgeStatus.CANDIDATE,
        )
        return self.propose_candidate(candidate, collector=collector)

    def submit_for_review(self, knowledge_id: str) -> KnowledgeItem:
        """Transition a CANDIDATE to UNDER_REVIEW."""
        item = self._store.get(knowledge_id)
        if not item:
            raise KnowledgeGovernanceError(f"Knowledge item not found: {knowledge_id}")
        if item.status != KnowledgeStatus.CANDIDATE:
            raise KnowledgeGovernanceError(
                f"Cannot submit item for review: current status is {item.status.value}, expected CANDIDATE"
            )

        updated = replace(item, status=KnowledgeStatus.UNDER_REVIEW)
        self._validator.validate(updated)
        return self._store.put(updated)

    def review(
        self,
        knowledge_id: str,
        review: KnowledgeReview,
        collector: Any | None = None,
    ) -> KnowledgeItem:
        """Perform formal review on an item in CANDIDATE or UNDER_REVIEW status.

        Enforces promotion evidence thresholds and produces audit events.
        """
        item = self._store.get(knowledge_id)
        if not item:
            raise KnowledgeGovernanceError(f"Knowledge item not found: {knowledge_id}")

        if item.status not in (KnowledgeStatus.CANDIDATE, KnowledgeStatus.UNDER_REVIEW):
            raise KnowledgeGovernanceError(
                f"Cannot review item: current status is {item.status.value}, must be CANDIDATE or UNDER_REVIEW"
            )

        if not isinstance(review, KnowledgeReview):
            raise KnowledgeGovernanceError("review must be a valid KnowledgeReview instance")

        if review.verdict == KnowledgeStatus.APPROVED:
            # Promote to APPROVED
            approved_item = replace(
                item,
                status=KnowledgeStatus.APPROVED,
                review=review,
            )
            # Full validation including promotion evidence tier rules
            try:
                self._validator.validate(approved_item)
            except KnowledgeValidationError as exc:
                raise KnowledgeGovernanceError(f"Promotion rejected: {exc}") from exc

            saved = self._store.put(approved_item)
            if collector is not None and hasattr(collector, "emit"):
                collector.emit(
                    EventType.KNOWLEDGE_APPROVED,
                    metadata=sanitize_event_metadata({
                        "knowledge_id": saved.knowledge_id,
                        "scope": saved.scope.value,
                        "reviewer": review.reviewer,
                        "verdict": review.verdict.value,
                    }),
                )
            return saved

        elif review.verdict == KnowledgeStatus.REJECTED:
            # Transition to REJECTED
            rejected_item = replace(
                item,
                status=KnowledgeStatus.REJECTED,
                review=review,
            )
            self._validator.validate(rejected_item)
            saved = self._store.put(rejected_item)
            if collector is not None and hasattr(collector, "emit"):
                collector.emit(
                    EventType.KNOWLEDGE_REJECTED,
                    metadata=sanitize_event_metadata({
                        "knowledge_id": saved.knowledge_id,
                        "reviewer": review.reviewer,
                        "rationale": review.rationale,
                    }),
                )
            return saved

        else:
            raise KnowledgeGovernanceError(
                f"Invalid review verdict: {review.verdict}, must be APPROVED or REJECTED"
            )

    def deprecate(
        self,
        knowledge_id: str,
        *,
        reviewer: str,
        rationale: str,
        collector: Any | None = None,
    ) -> KnowledgeItem:
        """Mark an APPROVED knowledge item as DEPRECATED."""
        item = self._store.get(knowledge_id)
        if not item:
            raise KnowledgeGovernanceError(f"Knowledge item not found: {knowledge_id}")
        if item.status != KnowledgeStatus.APPROVED:
            raise KnowledgeGovernanceError(
                f"Cannot deprecate item: current status is {item.status.value}, must be APPROVED"
            )

        review = KnowledgeReview(
            reviewer=reviewer,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            verdict=KnowledgeStatus.REJECTED,  # Marks rejection of future use
            rationale=rationale,
            evidence_tier_at_review=item.evidence.tier,
        )
        deprecated_item = replace(
            item,
            status=KnowledgeStatus.DEPRECATED,
            review=review,
        )
        saved = self._store.put(deprecated_item)

        if collector is not None and hasattr(collector, "emit"):
            collector.emit(
                EventType.KNOWLEDGE_REVISED,
                metadata=sanitize_event_metadata({
                    "knowledge_id": saved.knowledge_id,
                    "action": "deprecated",
                    "reviewer": reviewer,
                }),
            )
        return saved

    def supersede(
        self,
        old_knowledge_id: str,
        new_item: KnowledgeItem,
        *,
        reviewer: str,
        rationale: str,
        collector: Any | None = None,
    ) -> KnowledgeItem:
        """Supersede an APPROVED knowledge item with a revised, approved successor."""
        old_item = self._store.get(old_knowledge_id)
        if not old_item:
            raise KnowledgeGovernanceError(f"Old knowledge item not found: {old_knowledge_id}")
        if old_item.status != KnowledgeStatus.APPROVED:
            raise KnowledgeGovernanceError(
                f"Cannot supersede item: current status is {old_item.status.value}, must be APPROVED"
            )

        # Build successor item
        successor_review = KnowledgeReview(
            reviewer=reviewer,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            verdict=KnowledgeStatus.APPROVED,
            rationale=rationale,
            evidence_tier_at_review=new_item.evidence.tier,
        )
        successor = replace(
            new_item,
            version=max(new_item.version, old_item.version + 1),
            status=KnowledgeStatus.APPROVED,
            review=successor_review,
            superseded_by=None,
        )
        self._validator.validate(successor)

        # Mark old item as superseded
        superseded_old = replace(
            old_item,
            status=KnowledgeStatus.SUPERSEDED,
            superseded_by=successor.knowledge_id,
        )

        self._store.put(superseded_old)
        saved_new = self._store.put(successor)

        if collector is not None and hasattr(collector, "emit"):
            collector.emit(
                EventType.KNOWLEDGE_REVISED,
                metadata=sanitize_event_metadata({
                    "old_knowledge_id": old_knowledge_id,
                    "new_knowledge_id": saved_new.knowledge_id,
                    "action": "superseded",
                    "new_version": saved_new.version,
                }),
            )
        return saved_new
