"""Provider-neutral token and cost usage metadata."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Usage:
    """Usage information carried from providers through task results."""

    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Optional[float] = None
