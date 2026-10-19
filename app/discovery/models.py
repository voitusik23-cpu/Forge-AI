"""Provider-neutral models for the Discovery / Clarification layer.

This layer sits **in front of** the existing pipeline: it turns a one-line user
idea into a confirmed :class:`ProjectSpecification`, and then the existing
Planner / Dispatcher / Run path executes. Nothing here calls a model, opens a
socket, or touches the filesystem; the models are plain, immutable values.

No secret material belongs in these objects. An answer that looks like a
credential is replaced by a redaction marker *before* it is stored, so a
session file can never carry one (see :mod:`app.discovery.service`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "DiscoveryState",
    "TERMINAL_DISCOVERY_STATES",
    "ClarificationQuestion",
    "ClarificationAnswer",
    "ProjectSpecification",
    "DiscoverySession",
    "DiscoveryError",
    "UnknownDiscoverySession",
    "DiscoveryNotConfirmed",
    "DiscoveryNotReady",
    "DiscoveryStateConflict",
    "InvalidClarificationAnswer",
    "REDACTION_MARKER_PREFIX",
]


#: The four states of a discovery session. The names travel through the API
#: unchanged, so a client never has to translate them.
class DiscoveryState(str, Enum):
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY_FOR_PLANNING = "READY_FOR_PLANNING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


#: States a session can never leave (except that ``CANCELLED`` stays cancelled).
TERMINAL_DISCOVERY_STATES = frozenset(
    {DiscoveryState.CONFIRMED, DiscoveryState.CANCELLED}
)

#: Stored in place of an answer that looks like a credential. The suffix is the
#: reason word reported by the existing context validator, so an operator can see
#: *that* something was redacted and *why* without ever seeing the value.
REDACTION_MARKER_PREFIX = "[REDACTED"


def redaction_marker(reason: str) -> str:
    """The marker stored instead of a secret-shaped answer."""

    return f"{REDACTION_MARKER_PREFIX}:{reason}]"


@dataclass(frozen=True)
class ClarificationQuestion:
    """One short, practical question the user can answer in a sentence."""

    id: str
    text: str
    field: str
    why: str
    required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "field": self.field,
            "why": self.why,
            "required": bool(self.required),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClarificationQuestion":
        return cls(
            id=str(data["id"]),
            text=str(data["text"]),
            field=str(data["field"]),
            why=str(data.get("why", "")),
            required=bool(data.get("required", True)),
        )


@dataclass(frozen=True)
class ClarificationAnswer:
    """One answer (or an explicit skip) to a known question.

    ``text`` holds the user's own words, or the redaction marker when the words
    looked like a credential. ``redacted`` records that replacement, so the API
    and the session file can both say "this answer was removed" without keeping
    it.
    """

    question_id: str
    text: str
    skipped: bool = False
    redacted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "skipped": bool(self.skipped),
            "redacted": bool(self.redacted),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClarificationAnswer":
        return cls(
            question_id=str(data["question_id"]),
            text=str(data.get("text", "")),
            skipped=bool(data.get("skipped", False)),
            redacted=bool(data.get("redacted", False)),
        )


def _as_text_tuple(value: object) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


@dataclass(frozen=True)
class ProjectSpecification:
    """What Forge understood, in the terms the existing planner works in.

    Every field is derived deterministically from the goal text where the text
    already answers it, and otherwise taken from the user's own answers. An
    empty field means "still unknown"; it is never filled with a guess.
    """

    goal: str
    project_type: str = ""
    users: str = ""
    core_features: Tuple[str, ...] = ()
    platform: str = ""
    integrations: Tuple[str, ...] = ()
    data_storage: str = ""
    auth: str = ""
    notifications: str = ""
    constraints: Tuple[str, ...] = ()
    acceptance_criteria: Tuple[str, ...] = ()
    priorities: Tuple[str, ...] = ()
    #: Where the values came from: ``field`` -> the stored answer text (or the
    #: redaction marker). Never contains a secret, by construction.
    source_answers: Mapping[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ view
    def as_task_text(self) -> str:
        """The specification as task text, for the existing task description.

        Deterministic and bounded: one line per known field, unknown fields
        simply omitted. This is prepended to the caller's own task description,
        so the model receives the confirmed project context and the immediate
        request in one string, and the existing pipeline is otherwise untouched.
        """

        lines: List[str] = ["PROJECT SPECIFICATION"]

        def add(label: str, value: object) -> None:
            if isinstance(value, str):
                text = value.strip()
            else:
                text = "; ".join(str(item) for item in value or ())
            if text:
                lines.append(f"{label}: {text}")

        add("Goal", self.goal)
        add("Project type", self.project_type)
        add("Users", self.users)
        add("Core features", self.core_features)
        add("Platform", self.platform)
        add("Integrations", self.integrations)
        add("Data storage", self.data_storage)
        add("Auth", self.auth)
        add("Notifications", self.notifications)
        add("Constraints", self.constraints)
        add("Acceptance criteria", self.acceptance_criteria)
        add("Priorities", self.priorities)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "project_type": self.project_type,
            "users": self.users,
            "core_features": list(self.core_features),
            "platform": self.platform,
            "integrations": list(self.integrations),
            "data_storage": self.data_storage,
            "auth": self.auth,
            "notifications": self.notifications,
            "constraints": list(self.constraints),
            "acceptance_criteria": list(self.acceptance_criteria),
            "priorities": list(self.priorities),
            "source_answers": dict(self.source_answers),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectSpecification":
        raw_sources = data.get("source_answers") or {}
        sources = {
            str(key): str(value)
            for key, value in dict(raw_sources).items()
        } if isinstance(raw_sources, Mapping) else {}
        return cls(
            goal=str(data.get("goal", "")),
            project_type=str(data.get("project_type", "")),
            users=str(data.get("users", "")),
            core_features=_as_text_tuple(data.get("core_features")),
            platform=str(data.get("platform", "")),
            integrations=_as_text_tuple(data.get("integrations")),
            data_storage=str(data.get("data_storage", "")),
            auth=str(data.get("auth", "")),
            notifications=str(data.get("notifications", "")),
            constraints=_as_text_tuple(data.get("constraints")),
            acceptance_criteria=_as_text_tuple(data.get("acceptance_criteria")),
            priorities=_as_text_tuple(data.get("priorities")),
            source_answers=sources,
        )


@dataclass(frozen=True)
class DiscoverySession:
    """One conversation: the idea, its questions, the answers, the specification.

    The object is immutable; the service derives a new value on every transition
    and persists it, so a reader can never observe a half-applied answer.
    """

    session_id: str
    initial_goal: str
    state: DiscoveryState
    questions: Tuple[ClarificationQuestion, ...] = ()
    answers: Tuple[ClarificationAnswer, ...] = ()
    specification: Optional[ProjectSpecification] = None
    created_at: str = ""
    updated_at: str = ""
    skipped: bool = False
    #: Why the user chose to skip clarification. Screened like an answer, so it
    #: can never carry a credential either.
    skip_reason: str = ""

    # ------------------------------------------------------------------ view
    def answer_for(self, field_name: str) -> Optional[ClarificationAnswer]:
        """The stored answer for one specification field, when there is one."""

        for answer in self.answers:
            if answer.question_id == f"q-{field_name}":
                return answer
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "initial_goal": self.initial_goal,
            "state": self.state.value,
            "questions": [question.to_dict() for question in self.questions],
            "answers": [answer.to_dict() for answer in self.answers],
            "specification": (
                self.specification.to_dict() if self.specification is not None else None
            ),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "skipped": bool(self.skipped),
            "skip_reason": self.skip_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DiscoverySession":
        raw_specification = data.get("specification")
        specification = (
            ProjectSpecification.from_dict(raw_specification)
            if isinstance(raw_specification, Mapping)
            else None
        )
        return cls(
            session_id=str(data["session_id"]),
            initial_goal=str(data.get("initial_goal", "")),
            state=DiscoveryState(str(data["state"])),
            questions=tuple(
                ClarificationQuestion.from_dict(item)
                for item in (data.get("questions") or ())
            ),
            answers=tuple(
                ClarificationAnswer.from_dict(item)
                for item in (data.get("answers") or ())
            ),
            specification=specification,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            skipped=bool(data.get("skipped", False)),
            skip_reason=str(data.get("skip_reason", "")),
        )


# ---------------------------------------------------------------------------
# typed failures
#
# Each carries a stable ``code`` the API publishes verbatim (the existing error
# contract maps an unmapped code to the state-conflict class and keeps the code
# itself), and never carries user text.
# ---------------------------------------------------------------------------
class DiscoveryError(Exception):
    """Base class for every refusal of this layer."""

    code = "discovery_error"


class UnknownDiscoverySession(DiscoveryError):
    """No session is stored under that identifier."""

    code = "unknown_discovery_session"

    def __init__(self, session_id: str) -> None:
        super().__init__(f"no discovery session is recorded under '{session_id}'")
        self.session_id = session_id


class DiscoveryNotConfirmed(DiscoveryError):
    """Planning or execution was attempted before a session was confirmed."""

    code = "discovery_not_confirmed"


class DiscoveryNotReady(DiscoveryError):
    """Confirmation was attempted while essential areas are still unanswered."""

    code = "discovery_not_ready"


class DiscoveryStateConflict(DiscoveryError):
    """The requested transition is impossible from the session's state."""

    code = "discovery_state_conflict"


class InvalidClarificationAnswer(DiscoveryError):
    """An answer names a question this session never asked."""

    code = "invalid_discovery_answer"
