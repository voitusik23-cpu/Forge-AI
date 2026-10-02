"""Provider-neutral models for one-pass agent review."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.usage import Usage


class ReviewStatus(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    FAILED = "failed"


@dataclass
class ReviewResult:
    status: ReviewStatus
    review_text: str
    reviewer: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    usage: Optional[Usage] = None
