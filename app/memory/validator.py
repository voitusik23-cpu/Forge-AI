"""Deterministic validation boundary for Project Memory items."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Sequence

from app.context.models import ContextSourceType, ContextTrustLevel
from app.execution.redaction import (
    _COMMON_SECRET_PATTERNS,
    _SENSITIVE_KEY_PATTERN,
)
from app.memory.models import (
    MemoryCategory,
    MemoryItem,
    MemoryProvenance,
    MemoryStatus,
)


MAX_TITLE_LENGTH: int = 120
MAX_CONTENT_LENGTH: int = 2000
MAX_TAGS_COUNT: int = 10
MAX_TAG_LENGTH: int = 32
MAX_METADATA_ITEMS: int = 20
MAX_METADATA_VALUE_LENGTH: int = 1000

_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


class MemoryValidationError(ValueError):
    """Raised when a MemoryItem violates schema, bounds, or security invariants."""


_EXTRA_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*[^\s]{4,}", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|secret[_-]?key)\s*[:=]\s*[^\s]{6,}", re.IGNORECASE),
)


class MemoryValidator:
    """Deterministic validator enforcing memory bounds, structure, and secret avoidance."""

    def __init__(
        self,
        custom_secret_patterns: Sequence[re.Pattern[str]] = (),
    ) -> None:
        self._secret_patterns: tuple[re.Pattern[str], ...] = (
            _COMMON_SECRET_PATTERNS + _EXTRA_SECRET_PATTERNS + tuple(custom_secret_patterns)
        )

    def validate(self, item: MemoryItem | None) -> None:
        """Validate a MemoryItem against structural and security invariants."""
        if item is None or not isinstance(item, MemoryItem):
            raise MemoryValidationError("MemoryItem must be a non-null MemoryItem instance")

        # 1. Project ID
        if not isinstance(item.project_id, str) or not item.project_id.strip():
            raise MemoryValidationError("project_id is required and cannot be empty")
        if not _SAFE_ID_PATTERN.match(item.project_id):
            raise MemoryValidationError(f"Invalid project_id characters: {item.project_id}")

        # 2. Memory ID
        if not isinstance(item.memory_id, str) or not item.memory_id.strip():
            raise MemoryValidationError("memory_id is required and cannot be empty")
        if not _SAFE_ID_PATTERN.match(item.memory_id):
            raise MemoryValidationError(f"Invalid memory_id characters: {item.memory_id}")

        # 3. Category
        if not isinstance(item.category, MemoryCategory):
            try:
                MemoryCategory(item.category)
            except (ValueError, TypeError) as exc:
                raise MemoryValidationError(f"Invalid memory category: {item.category}") from exc

        # 4. Status
        if not isinstance(item.status, MemoryStatus):
            try:
                MemoryStatus(item.status)
            except (ValueError, TypeError) as exc:
                raise MemoryValidationError(f"Invalid memory status: {item.status}") from exc

        # 5. Title bounds
        if not isinstance(item.title, str) or not item.title.strip():
            raise MemoryValidationError("title is required and cannot be empty")
        if len(item.title) > MAX_TITLE_LENGTH:
            raise MemoryValidationError(
                f"title length {len(item.title)} exceeds limit of {MAX_TITLE_LENGTH}"
            )

        # 6. Content bounds
        if not isinstance(item.content, str) or not item.content.strip():
            raise MemoryValidationError("content is required and cannot be empty")
        if len(item.content) > MAX_CONTENT_LENGTH:
            raise MemoryValidationError(
                f"content length {len(item.content)} exceeds limit of {MAX_CONTENT_LENGTH}"
            )

        # 7. Provenance
        prov = item.provenance
        if prov is None or not isinstance(prov, MemoryProvenance):
            raise MemoryValidationError("provenance must be a valid MemoryProvenance instance")
        if not isinstance(prov.source_id, str) or not prov.source_id.strip():
            raise MemoryValidationError("provenance.source_id is required")
        if not isinstance(prov.run_id, str) or not prov.run_id.strip():
            raise MemoryValidationError("provenance.run_id is required")
        if not isinstance(prov.actor, str) or not prov.actor.strip():
            raise MemoryValidationError("provenance.actor is required")
        if not isinstance(prov.source_type, ContextSourceType):
            try:
                ContextSourceType(prov.source_type)
            except (ValueError, TypeError) as exc:
                raise MemoryValidationError(f"Invalid provenance source_type: {prov.source_type}") from exc

        # 8. Revision
        if not isinstance(item.revision, int) or item.revision < 1:
            raise MemoryValidationError(f"revision must be an integer >= 1, got {item.revision}")

        # 9. Superseded by consistency
        if item.status == MemoryStatus.SUPERSEDED:
            if not item.superseded_by or not isinstance(item.superseded_by, str):
                raise MemoryValidationError("superseded_by is required when status is SUPERSEDED")
        elif item.superseded_by is not None:
            raise MemoryValidationError("superseded_by cannot be set unless status is SUPERSEDED")

        # 10. Tags bounds
        if len(item.tags) > MAX_TAGS_COUNT:
            raise MemoryValidationError(
                f"tags count {len(item.tags)} exceeds limit of {MAX_TAGS_COUNT}"
            )
        for tag in item.tags:
            if not isinstance(tag, str) or not tag.strip():
                raise MemoryValidationError("tag must be a non-empty string")
            if len(tag) > MAX_TAG_LENGTH:
                raise MemoryValidationError(
                    f"tag length {len(tag)} exceeds limit of {MAX_TAG_LENGTH}"
                )

        # 11. Metadata bounds
        if len(item.metadata) > MAX_METADATA_ITEMS:
            raise MemoryValidationError(
                f"metadata item count {len(item.metadata)} exceeds limit of {MAX_METADATA_ITEMS}"
            )
        for k, v in item.metadata.items():
            if _SENSITIVE_KEY_PATTERN.search(str(k)):
                raise MemoryValidationError(f"Sensitive key forbidden in memory metadata: {k}")
            if isinstance(v, str) and len(v) > MAX_METADATA_VALUE_LENGTH:
                raise MemoryValidationError(f"metadata value for key '{k}' exceeds length limit")

        # 12. Secret scanning in text
        self._scan_for_secrets(item.title, field_name="title")
        self._scan_for_secrets(item.content, field_name="content")
        for k, v in item.metadata.items():
            if isinstance(v, str):
                self._scan_for_secrets(v, field_name=f"metadata.{k}")

    def _scan_for_secrets(self, text: str, field_name: str) -> None:
        """Scan text against known secret patterns and reject matches."""
        for pattern in self._secret_patterns:
            if pattern.search(text):
                raise MemoryValidationError(
                    f"Secret material detected in memory {field_name}"
                )
