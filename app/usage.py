from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class Usage:
    """Usage information carried from providers through task results."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost: Optional[float] = None

    def __init__(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: Optional[Union[int, float]] = 0,
        estimated_cost: Optional[float] = None,
    ) -> None:
        # Support legacy positional call Usage(input_tokens, output_tokens, estimated_cost)
        # where the 3rd argument was a float (or None when estimated_cost is float/None and 4th is omitted)
        if estimated_cost is None and isinstance(cached_tokens, float):
            self.input_tokens = int(input_tokens)
            self.output_tokens = int(output_tokens)
            self.cached_tokens = 0
            self.estimated_cost = float(cached_tokens)
        else:
            self.input_tokens = int(input_tokens)
            self.output_tokens = int(output_tokens)
            self.cached_tokens = int(cached_tokens or 0)
            self.estimated_cost = float(estimated_cost) if estimated_cost is not None else None

    @property
    def total_tokens(self) -> int:
        """Total tokens processed in this usage record."""
        return self.input_tokens + self.output_tokens
