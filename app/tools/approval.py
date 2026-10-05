"""Small in-memory approval boundary for tool and execution authorization."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class ApprovalState(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WAITING = "WAITING"


@dataclass(frozen=True)
class ApprovalRequest:
    """Safe reference to the exact invocation awaiting authorization."""

    run_id: str
    invocation_id: str
    tool_id: str
    reason: str
    intent_fingerprint: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    approval_id: str = ""


@dataclass(frozen=True)
class ApprovalResolution:
    """Resolution returned by an ApprovalResolver."""

    decision: ApprovalState
    approved_fingerprint: str = ""
    approval_id: str = ""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ApprovalState):
            return self.decision == other
        if isinstance(other, ApprovalResolution):
            return (
                self.decision == other.decision
                and self.approved_fingerprint == other.approved_fingerprint
                and self.approval_id == other.approval_id
            )
        return False


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
                    if (
                        executable_matches(tool_or_cmd.executable, required.executable)
                        and tool_or_cmd.argv == required.argv
                    ):
                        return ApprovalState.REQUIRED
                elif isinstance(required, str) and executable_matches(
                    tool_or_cmd.executable, required
                ):
                    return ApprovalState.REQUIRED
        elif isinstance(tool_or_cmd, str):
            for required in self._required_tools:
                if isinstance(required, str) and executable_matches(tool_or_cmd, required):
                    return ApprovalState.REQUIRED
        return ApprovalState.NOT_REQUIRED


class ApprovalResolver(Protocol):
    """Resolve a request independently from the agent's proposal."""

    def resolve(self, request: ApprovalRequest) -> ApprovalResolution | ApprovalState | None:
        """Return an approval resolution, or None while pending."""
        ...


@dataclass
class _ApprovalEntry:
    decision: ApprovalState
    intent_fingerprint: str | None
    approval_id: str
    consumed: bool = False


class InMemoryApprovalResolver:
    """One-process, exact-run/invocation resolver suitable for offline use."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _ApprovalEntry] = {}

    def submit(
        self,
        run_id: str,
        invocation_id: str,
        decision: ApprovalState,
        intent_fingerprint: str | None = None,
        approval_id: str = "",
    ) -> str:
        if not isinstance(decision, ApprovalState) or decision not in (
            ApprovalState.APPROVED,
            ApprovalState.REJECTED,
        ):
            raise ValueError("approval decision must be APPROVED or REJECTED")

        app_id = approval_id or f"appr-{uuid.uuid4().hex[:12]}"
        key = (run_id, invocation_id)
        self._entries[key] = _ApprovalEntry(
            decision=decision,
            intent_fingerprint=intent_fingerprint,
            approval_id=app_id,
            consumed=False,
        )
        return app_id

    def resolve(self, request: ApprovalRequest) -> ApprovalResolution | None:
        key = (request.run_id, request.invocation_id)
        if key not in self._entries:
            return None

        entry = self._entries[key]

        # Single-use enforcement: cannot reuse consumed approval (Invariant I8)
        if entry.consumed:
            return None

        # Invariant I1: Intent A cannot authorize Intent B
        if entry.intent_fingerprint is not None:
            if request.intent_fingerprint != entry.intent_fingerprint:
                # Fingerprint mismatch
                return None
        elif request.intent_fingerprint is not None:
            # Approval had no fingerprint (wildcard) but request requires fingerprint
            return None

        # Mark consumed on consumption
        entry.consumed = True

        return ApprovalResolution(
            decision=entry.decision,
            approved_fingerprint=entry.intent_fingerprint or (request.intent_fingerprint or ""),
            approval_id=entry.approval_id,
        )
