"""Small in-memory approval boundary for tool execution."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ApprovalState(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ApprovalRequest:
    """Safe reference to the exact invocation awaiting human authorization."""

    run_id: str
    invocation_id: str
    tool_id: str
    reason: str


class ApprovalPolicy:
    """Require approval for explicitly listed tool IDs."""

    def __init__(self, approval_required_tools=()) -> None:
        self._required_tools = frozenset(approval_required_tools)

    def evaluate(self, tool_id: str) -> ApprovalState:
        if tool_id in self._required_tools:
            return ApprovalState.REQUIRED
        return ApprovalState.NOT_REQUIRED


class ApprovalResolver(Protocol):
    """Resolve a request independently from the agent's proposal."""

    def resolve(self, request: ApprovalRequest) -> ApprovalState | None:
        """Return an approval decision, or None while it remains pending."""


class InMemoryApprovalResolver:
    """One-process, exact-run/invocation resolver suitable for offline use."""

    def __init__(self) -> None:
        self._decisions: dict[tuple[str, str], ApprovalState] = {}

    def submit(
        self, run_id: str, invocation_id: str, decision: ApprovalState
    ) -> None:
        if not isinstance(decision, ApprovalState) or decision not in (
            ApprovalState.APPROVED,
            ApprovalState.REJECTED,
        ):
            raise ValueError("approval decision must be APPROVED or REJECTED")
        self._decisions[(run_id, invocation_id)] = decision

    def resolve(self, request: ApprovalRequest) -> ApprovalState | None:
        return self._decisions.pop((request.run_id, request.invocation_id), None)
