"""Discovery / Clarification layer: the front door of the existing pipeline.

The user describes an idea; this package decides whether the idea says enough to
plan, asks a few short questions when it does not, derives a
:class:`~app.discovery.models.ProjectSpecification`, and refuses to let anything
plan or execute until the specification is confirmed.

Nothing here executes work. The existing Planner / Dispatcher / Run path does
that, and this layer only hands it one task description (see
:func:`app.discovery.service.apply_specification`).

Deterministic by construction: :mod:`app.discovery.analyzer`,
:mod:`app.discovery.domain` and :mod:`app.discovery.banks` use fixed rules in Russian
for the user-facing questions, and no provider call is made by default.
:mod:`app.discovery.llm_questions` is the one optional exception: it is asked only when
the owner turns «Вопросы по смыслу задачи (через модель)» on (default **off**), and any
failure there falls back to the deterministic banks.
"""

from __future__ import annotations

from app.discovery.banks import choose_questions
from app.discovery.domain import DOMAINS, DomainGuess, detect_domain
from app.discovery.models import (
    ClarificationAnswer,
    ClarificationOption,
    ClarificationQuestion,
    DiscoveryError,
    DiscoveryNotConfirmed,
    DiscoveryNotReady,
    DiscoverySession,
    DiscoveryState,
    DiscoveryStateConflict,
    InvalidClarificationAnswer,
    ProjectSpecification,
    RequiredQuestionSkipped,
    UnknownDiscoverySession,
)
from app.discovery.service import (
    DiscoveryService,
    apply_specification,
    value_origins,
)

__all__ = [
    "DOMAINS",
    "ClarificationAnswer",
    "ClarificationOption",
    "ClarificationQuestion",
    "DiscoveryError",
    "DiscoveryNotConfirmed",
    "DiscoveryNotReady",
    "DiscoveryService",
    "DiscoverySession",
    "DiscoveryState",
    "DiscoveryStateConflict",
    "DomainGuess",
    "InvalidClarificationAnswer",
    "ProjectSpecification",
    "RequiredQuestionSkipped",
    "UnknownDiscoverySession",
    "apply_specification",
    "choose_questions",
    "detect_domain",
    "value_origins",
]
