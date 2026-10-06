"""Small in-memory approval boundary for tool and execution authorization."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


def tool_invocation_fingerprint(tool_id: object, tool_input: object) -> str:
    """Return a deterministic canonical SHA-256 fingerprint of a concrete tool invocation.

    The approval boundary binds cryptographically to the tool payload. An approval
    granted for one tool invocation (e.g. write_project_file with path A) cannot
    authorize a different payload (e.g. path B) reusing the same invocation id.

    Canonicalisation is recursively order-insensitive for mappings. Sets/frozensets
    are sorted deterministically. Nested lists, primitives, and nulls are preserved.
    """
    def _canonical(value: object) -> object:
        if isinstance(value, Mapping):
            return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
        if isinstance(value, (list, tuple)):
            return [_canonical(v) for v in value]
        if isinstance(value, (set, frozenset)):
            items = [_canonical(v) for v in value]
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":")))
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    payload = json.dumps(
        {"input": _canonical(tool_input), "tool_id": str(tool_id)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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

    def resolve(self, request: ApprovalRequest) -> ApprovalResolution | None:
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

        # Fail closed: fingerprint must be non-empty and match exactly (None is never a wildcard)
        if not request.intent_fingerprint or not entry.intent_fingerprint:
            return None

        # Invariant I1: Intent A cannot authorize Intent B
        if request.intent_fingerprint != entry.intent_fingerprint:
            return None

        # Mark consumed on consumption (single-use)
        entry.consumed = True

        return ApprovalResolution(
            decision=entry.decision,
            approved_fingerprint=entry.intent_fingerprint,
            approval_id=entry.approval_id,
        )
