"""Base extractor interface for static structural fact extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from app.understanding.models import ScanWarning, StructuralFact


class BaseExtractor(ABC):
    """Abstract base class implemented by language- and format-specific extractors."""

    @property
    @abstractmethod
    def extractor_id(self) -> str:
        """Unique identifier and version of this extractor (e.g. 'python_ast_v1')."""

    @abstractmethod
    def can_extract(self, relative_path: str) -> bool:
        """Return True if this extractor can process the given relative file path."""

    @abstractmethod
    def extract(
        self,
        relative_path: str,
        content: str,
        lines: Sequence[str],
    ) -> tuple[tuple[StructuralFact, ...], tuple[ScanWarning, ...]]:
        """Statically extract deterministic structural facts from file content without execution."""
