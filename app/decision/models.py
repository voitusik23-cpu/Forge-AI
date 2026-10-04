"""Deterministic, provider-neutral decision contract for Engineering Runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from app.projects.state import ProjectState

FORBIDDEN_DECISION_METADATA_SUBSTRINGS: tuple[str, ...] = (
    "stdout",
    "stderr",
    "raw_output",
    "prompt",
    "chain_of_thought",
    "secret",
    "token",
    "password",
    "api_key",
    "credential",
    "source_code",
    "file_content",
)


def sanitize_decision_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    """Deterministically sanitize metadata by removing forbidden or sensitive keys."""
    if not metadata:
        return {}
    sanitized: dict[str, object] = {}
    for key, val in metadata.items():
        key_str = str(key).lower().strip()
        if any(bad in key_str for bad in FORBIDDEN_DECISION_METADATA_SUBSTRINGS):
            continue
        if isinstance(val, str) and len(val) > 4096:
            continue
        sanitized[str(key)] = val
    return sanitized


class DecisionType(str, Enum):
    """Canonical control-flow decision types indicating what a Run should attempt next."""

    CONTINUE = "CONTINUE"
    REVISE = "REVISE"
    VERIFY = "VERIFY"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    WAIT = "WAIT"
    FAIL = "FAIL"
    COMPLETE = "COMPLETE"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            normalized = value.strip().upper()
            for member in cls:
                if member.value == normalized or member.name == normalized:
                    return member
        return None


class DecisionAction(str, Enum):
    """Typed actionable operations connected to existing Forge capabilities."""

    EXECUTE = "EXECUTE"
    RUN_VERIFICATION = "RUN_VERIFICATION"
    REQUEST_REVISION = "REQUEST_REVISION"
    REQUEST_USER_APPROVAL = "REQUEST_USER_APPROVAL"
    WAIT_FOR_APPROVAL = "WAIT_FOR_APPROVAL"
    COMPLETE_RUN = "COMPLETE_RUN"
    FAIL_RUN = "FAIL_RUN"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            normalized = value.strip().upper()
            for member in cls:
                if member.value == normalized or member.name == normalized:
                    return member
        return None


@dataclass(frozen=True)
class DecisionRequest:
    """Structured input context presented to a DecisionProvider."""

    run_id: str
    decision_id: str = field(default_factory=lambda: str(uuid4()))
    attempt_number: int | None = None
    task_id: str | None = None
    current_project_state: ProjectState | None = None
    acceptance_status: str | None = None
    verification_status_summary: str | None = None
    available_actions: tuple[DecisionAction, ...] = ()
    blocking_conditions: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    context_id: str | None = None
    context_fingerprint: str | None = None
    context_envelope: Any | None = None

    def __post_init__(self) -> None:
        if not self.run_id or not isinstance(self.run_id, str):
            raise ValueError("run_id must be a non-empty string")
        if self.attempt_number is not None and self.attempt_number < 0:
            raise ValueError(f"attempt_number cannot be negative, got {self.attempt_number}")
        if isinstance(self.available_actions, list):
            object.__setattr__(self, "available_actions", tuple(self.available_actions))
        if isinstance(self.blocking_conditions, list):
            object.__setattr__(self, "blocking_conditions", tuple(self.blocking_conditions))
        sanitized = sanitize_decision_metadata(self.metadata)
        object.__setattr__(self, "metadata", sanitized)

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "attempt_number": self.attempt_number,
            "task_id": self.task_id,
            "current_project_state": self.current_project_state.to_dict()
            if self.current_project_state
            else None,
            "acceptance_status": self.acceptance_status,
            "verification_status_summary": self.verification_status_summary,
            "available_actions": [a.value for a in self.available_actions],
            "blocking_conditions": list(self.blocking_conditions),
            "metadata": dict(self.metadata),
            "context_id": self.context_id,
            "context_fingerprint": self.context_fingerprint,
        }


@dataclass(frozen=True)
class Decision:
    """Safe, immutable control-flow recommendation output by a DecisionProvider.

    Decision represents WHAT to do next; it does NOT grant permission or bypass
    security boundaries.
    """

    decision_id: str
    run_id: str
    decision_type: DecisionType
    action: DecisionAction
    reason_code: str = ""
    rationale: str = ""
    confidence: float | None = 1.0
    attempt_number: int | None = None
    references: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.decision_id or not isinstance(self.decision_id, str):
            raise ValueError("decision_id must be a non-empty string")
        if not self.run_id or not isinstance(self.run_id, str):
            raise ValueError("run_id must be a non-empty string")
        if isinstance(self.decision_type, str) and not isinstance(self.decision_type, DecisionType):
            object.__setattr__(self, "decision_type", DecisionType(self.decision_type))
        if isinstance(self.action, str) and not isinstance(self.action, DecisionAction):
            object.__setattr__(self, "action", DecisionAction(self.action))
        if not self.reason_code and not self.rationale:
            raise ValueError("reason_code or rationale must be provided")
        if not self.reason_code:
            object.__setattr__(self, "reason_code", str(self.rationale))
        if not self.rationale:
            object.__setattr__(self, "rationale", str(self.reason_code))
        sanitized = sanitize_decision_metadata(self.metadata)
        object.__setattr__(self, "metadata", sanitized)
        if isinstance(self.references, dict):
            object.__setattr__(self, "references", dict(self.references))

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "decision_type": self.decision_type.value,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "attempt_number": self.attempt_number,
            "references": dict(self.references),
            "metadata": dict(self.metadata),
        }
