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
    intent_fingerprint: str | None = None


class ApprovalPolicy:
    """Require approval for explicitly listed tool IDs."""

    MANDATORY_APPROVAL_TOOLS = frozenset({"write_project_file"})

    def __init__(self, approval_required_tools=()) -> None:
        self._required_tools = frozenset(approval_required_tools) | self.MANDATORY_APPROVAL_TOOLS

    def evaluate(self, tool_or_cmd: object) -> ApprovalState:
        if tool_or_cmd in self._required_tools:
            return ApprovalState.REQUIRED
        from app.execution.identity import CommandIdentity, executable_matches
        if isinstance(tool_or_cmd, CommandIdentity):
            for required in self._required_tools:
                if isinstance(required, CommandIdentity):
                    if executable_matches(tool_or_cmd.executable, required.executable) and tool_or_cmd.argv == required.argv:
                        return ApprovalState.REQUIRED
                elif isinstance(required, str) and executable_matches(tool_or_cmd.executable, required):
                    return ApprovalState.REQUIRED
        elif isinstance(tool_or_cmd, str):
            for required in self._required_tools:
                if isinstance(required, str) and executable_matches(tool_or_cmd, required):
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
        self._fingerprints: dict[tuple[str, str], str] = {}

    def submit(
        self,
        run_id: str,
        invocation_id: str,
        decision: ApprovalState,
        intent_fingerprint: str | None = None,
    ) -> None:
        if not isinstance(decision, ApprovalState) or decision not in (
            ApprovalState.APPROVED,
            ApprovalState.REJECTED,
        ):
            raise ValueError("approval decision must be APPROVED or REJECTED")
        key = (run_id, invocation_id)
        self._decisions[key] = decision
        if intent_fingerprint is not None:
            self._fingerprints[key] = intent_fingerprint

    def resolve(self, request: ApprovalRequest) -> ApprovalState | None:
        key = (request.run_id, request.invocation_id)
        if key not in self._decisions:
            return None
        if key in self._fingerprints:
            expected_fp = self._fingerprints[key]
            if request.intent_fingerprint != expected_fp:
                return None
        elif request.intent_fingerprint is not None:
            return None
        self._fingerprints.pop(key, None)
        return self._decisions.pop(key, None)
