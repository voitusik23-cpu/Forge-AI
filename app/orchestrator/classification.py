"""Deterministic, provider-neutral task intent classification."""

import re
from typing import Any, Iterable

from app.orchestrator.models import Task, TaskCategory


_REVIEW_PATTERNS = (
    r"\breview\b",
    r"\bcheck\s+(?:this\s+)?implementation\b",
    r"\bfind\s+bugs?\b",
    r"\bcode\s+review\b",
    r"\baudit\b",
)
_CODE_PATTERNS = (
    r"\bwrite\s+(?:\w+\s+)?code\b",
    r"\bimplement\b",
    r"\bfix\s+(?:this\s+)?bug\b",
    r"\brefactor\b",
    r"\bcreate\s+(?:a\s+)?class\b",
    r"\bdebug\b",
)
_ANALYSIS_PATTERNS = (
    r"\banaly[sz]e\b",
    r"\banalysis\b",
    r"\bcompare\s+(?:these\s+)?approaches\b",
    r"\binvestigate\b",
    r"\bexplain\s+why\b",
)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


def classify_task(task: Task) -> TaskCategory:
    """Honor an explicit category; otherwise classify text and metadata."""
    if task.category_was_explicit:
        return task.category

    metadata_hints = []
    for metadata in (task.context, task.parameters):
        if not isinstance(metadata, dict):
            continue
        for key in ("category", "task_type", "intent"):
            value = metadata.get(key)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"code", "coding"}:
                    metadata_hints.append("implement code")
                elif normalized in {"review", "analysis", "other"}:
                    metadata_hints.append(normalized)
    text = " ".join(
        [task.description, *_strings(task.context), *_strings(task.parameters), *metadata_hints]
    ).lower()
    if any(re.search(pattern, text) for pattern in _REVIEW_PATTERNS):
        return TaskCategory.REVIEW
    if any(re.search(pattern, text) for pattern in _CODE_PATTERNS):
        return TaskCategory.CODE
    if any(re.search(pattern, text) for pattern in _ANALYSIS_PATTERNS):
        return TaskCategory.ANALYSIS
    return TaskCategory.OTHER
