"""Deterministic validation boundary for Forge Knowledge items."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.execution.redaction import (
    _COMMON_SECRET_PATTERNS,
    _SENSITIVE_KEY_PATTERN,
)
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


MAX_STATEMENT_LENGTH: int = 240
MAX_RATIONALE_LENGTH: int = 2000
MAX_DOMAIN_LENGTH: int = 64
MAX_TAGS_COUNT: int = 10
MAX_TAG_LENGTH: int = 32
MAX_METADATA_ITEMS: int = 20
MAX_METADATA_VALUE_LENGTH: int = 1000

_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")

_EXTRA_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*[^\s]{4,}", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|secret[_-]?key)\s*[:=]\s*[^\s]{6,}", re.IGNORECASE),
)


class KnowledgeValidationError(ValueError):
    """Raised when a KnowledgeItem violates schema, bounds, evidence, or security invariants."""


class KnowledgeValidator:
    """Deterministic validator enforcing knowledge bounds, structure, evidence, and secret avoidance."""

    def __init__(
        self,
        custom_secret_patterns: Sequence[re.Pattern[str]] = (),
    ) -> None:
        self._secret_patterns: tuple[re.Pattern[str], ...] = (
            _COMMON_SECRET_PATTERNS + _EXTRA_SECRET_PATTERNS + tuple(custom_secret_patterns)
        )

    def validate(self, item: KnowledgeItem | None) -> None:
        """Validate a KnowledgeItem against structural, evidence, and security invariants."""
        if item is None or not isinstance(item, KnowledgeItem):
            raise KnowledgeValidationError("KnowledgeItem must be a non-null KnowledgeItem instance")

        # 1. Knowledge ID
        if not isinstance(item.knowledge_id, str) or not item.knowledge_id.strip():
            raise KnowledgeValidationError("knowledge_id is required and cannot be empty")
        if not _SAFE_ID_PATTERN.match(item.knowledge_id):
            raise KnowledgeValidationError(f"Invalid knowledge_id characters: '{item.knowledge_id}'")

        # 2. Scope
        if not isinstance(item.scope, KnowledgeScope):
            try:
                KnowledgeScope(item.scope)
            except (ValueError, TypeError) as exc:
                raise KnowledgeValidationError(f"Invalid knowledge scope: {item.scope}") from exc

        if item.scope == KnowledgeScope.DOMAIN:
            if not item.domain or not isinstance(item.domain, str) or not item.domain.strip():
                raise KnowledgeValidationError("domain is required when scope is DOMAIN")
            if len(item.domain) > MAX_DOMAIN_LENGTH:
                raise KnowledgeValidationError(
                    f"domain length {len(item.domain)} exceeds limit of {MAX_DOMAIN_LENGTH}"
                )
            if not _SAFE_ID_PATTERN.match(item.domain.strip()):
                raise KnowledgeValidationError(f"Invalid characters in domain: '{item.domain}'")

        # 3. Category
        if not isinstance(item.category, KnowledgeCategory):
            try:
                KnowledgeCategory(item.category)
            except (ValueError, TypeError) as exc:
                raise KnowledgeValidationError(f"Invalid knowledge category: {item.category}") from exc

        # 4. Status
        if not isinstance(item.status, KnowledgeStatus):
            try:
                KnowledgeStatus(item.status)
            except (ValueError, TypeError) as exc:
                raise KnowledgeValidationError(f"Invalid knowledge status: {item.status}") from exc

        # 5. Statement bounds
        if not isinstance(item.statement, str) or not item.statement.strip():
            raise KnowledgeValidationError("statement is required and cannot be empty")
        if len(item.statement) > MAX_STATEMENT_LENGTH:
            raise KnowledgeValidationError(
                f"statement length {len(item.statement)} exceeds limit of {MAX_STATEMENT_LENGTH}"
            )

        # 6. Rationale bounds
        if not isinstance(item.rationale, str) or not item.rationale.strip():
            raise KnowledgeValidationError("rationale is required and cannot be empty")
        if len(item.rationale) > MAX_RATIONALE_LENGTH:
            raise KnowledgeValidationError(
                f"rationale length {len(item.rationale)} exceeds limit of {MAX_RATIONALE_LENGTH}"
            )

        # 7. Applicability constraints
        if item.applicability is None or not isinstance(item.applicability, KnowledgeApplicability):
            raise KnowledgeValidationError("applicability must be a KnowledgeApplicability instance")
        if item.applicability.is_empty():
            raise KnowledgeValidationError(
                "applicability constraints cannot be empty; knowledge must define specific boundaries"
            )

        if item.non_applicability is not None and not isinstance(
            item.non_applicability, KnowledgeApplicability
        ):
            raise KnowledgeValidationError(
                "non_applicability must be a KnowledgeApplicability instance if provided"
            )

        # 8. Evidence
        if item.evidence is None or not isinstance(item.evidence, KnowledgeEvidence):
            raise KnowledgeValidationError("evidence must be a KnowledgeEvidence instance")
        if not isinstance(item.evidence.tier, EvidenceTier):
            try:
                EvidenceTier(item.evidence.tier)
            except (ValueError, TypeError) as exc:
                raise KnowledgeValidationError(f"Invalid evidence tier: {item.evidence.tier}") from exc

        # 9. Governance and Promotion thresholds
        if item.status == KnowledgeStatus.APPROVED:
            if item.review is None or not isinstance(item.review, KnowledgeReview):
                raise KnowledgeValidationError("Approved knowledge must contain a valid KnowledgeReview")
            if item.review.verdict != KnowledgeStatus.APPROVED:
                raise KnowledgeValidationError(
                    f"Review verdict must be APPROVED for approved knowledge, got {item.review.verdict}"
                )

            # Evidence thresholds per scope
            if item.scope == KnowledgeScope.FORGE_GLOBAL:
                if item.evidence.tier not in (
                    EvidenceTier.CROSS_PROJECT,
                    EvidenceTier.USER_CONFIRMED,
                    EvidenceTier.EXTERNAL_AUTHORITATIVE,
                ):
                    raise KnowledgeValidationError(
                        f"FORGE_GLOBAL scope requires CROSS_PROJECT, USER_CONFIRMED, or EXTERNAL_AUTHORITATIVE evidence, got {item.evidence.tier}"
                    )
                if (
                    item.evidence.tier == EvidenceTier.CROSS_PROJECT
                    and len(set(item.evidence.corroborating_project_ids)) < 2
                ):
                    raise KnowledgeValidationError(
                        "FORGE_GLOBAL scope with CROSS_PROJECT evidence requires at least 2 distinct projects"
                    )

            elif item.scope == KnowledgeScope.DOMAIN:
                if item.evidence.tier not in (
                    EvidenceTier.VERIFIED_OBSERVATION,
                    EvidenceTier.CROSS_PROJECT,
                    EvidenceTier.USER_CONFIRMED,
                    EvidenceTier.EXTERNAL_AUTHORITATIVE,
                ):
                    raise KnowledgeValidationError(
                        f"DOMAIN scope requires VERIFIED_OBSERVATION, CROSS_PROJECT, USER_CONFIRMED, or EXTERNAL_AUTHORITATIVE evidence, got {item.evidence.tier}"
                    )

        if item.status == KnowledgeStatus.REJECTED:
            if item.review is None or not isinstance(item.review, KnowledgeReview):
                raise KnowledgeValidationError("Rejected knowledge must contain a valid KnowledgeReview")
            if item.review.verdict != KnowledgeStatus.REJECTED:
                raise KnowledgeValidationError(
                    f"Review verdict must be REJECTED for rejected knowledge, got {item.review.verdict}"
                )

        # 10. Revision & Superseded checks
        if not isinstance(item.version, int) or item.version < 1:
            raise KnowledgeValidationError(f"version must be an integer >= 1, got {item.version}")

        if item.status == KnowledgeStatus.SUPERSEDED:
            if not item.superseded_by or not isinstance(item.superseded_by, str):
                raise KnowledgeValidationError("superseded_by is required when status is SUPERSEDED")
        elif item.superseded_by is not None:
            raise KnowledgeValidationError("superseded_by cannot be set unless status is SUPERSEDED")

        # 11. Tags bounds
        if len(item.tags) > MAX_TAGS_COUNT:
            raise KnowledgeValidationError(
                f"tags count {len(item.tags)} exceeds limit of {MAX_TAGS_COUNT}"
            )
        for tag in item.tags:
            if not isinstance(tag, str) or not tag.strip():
                raise KnowledgeValidationError("tag must be a non-empty string")
            if len(tag) > MAX_TAG_LENGTH:
                raise KnowledgeValidationError(f"tag length {len(tag)} exceeds limit of {MAX_TAG_LENGTH}")

        # 12. Metadata bounds and sensitive key checks
        if len(item.metadata) > MAX_METADATA_ITEMS:
            raise KnowledgeValidationError(
                f"metadata item count {len(item.metadata)} exceeds limit of {MAX_METADATA_ITEMS}"
            )
        for k, v in item.metadata.items():
            if _SENSITIVE_KEY_PATTERN.search(str(k)):
                raise KnowledgeValidationError(f"Sensitive key forbidden in knowledge metadata: {k}")
            if isinstance(v, str) and len(v) > MAX_METADATA_VALUE_LENGTH:
                raise KnowledgeValidationError(f"metadata value for key '{k}' exceeds length limit")

        # 13. Secret scanning
        self._scan_for_secrets(item.statement, field_name="statement")
        self._scan_for_secrets(item.rationale, field_name="rationale")
        for k, v in item.metadata.items():
            if isinstance(v, str):
                self._scan_for_secrets(v, field_name=f"metadata.{k}")
        for attr in ("runtimes", "frameworks", "operating_systems", "project_types", "execution_modes", "tags"):
            for val in getattr(item.applicability, attr, ()):
                self._scan_for_secrets(str(val), field_name=f"applicability.{attr}")

    def _scan_for_secrets(self, text: str, field_name: str) -> None:
        """Scan text against known secret patterns and reject matches."""
        for pattern in self._secret_patterns:
            if pattern.search(text):
                raise KnowledgeValidationError(f"Secret material detected in knowledge {field_name}")
