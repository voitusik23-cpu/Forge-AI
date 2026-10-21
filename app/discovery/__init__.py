"""Discovery / Clarification layer: the front door of the existing pipeline.

The user describes an idea; this package decides whether the idea says enough to
plan, asks a few short questions when it does not, derives a
:class:`~app.discovery.models.ProjectSpecification`, and refuses to let anything
plan or execute until the specification is confirmed.

Nothing here executes work. The existing Planner / Dispatcher / Run path does
that, and this layer only hands it one task description (see
:func:`app.discovery.service.apply_specification`).

Deterministic by construction: :mod:`app.discovery.analyzer` and
:mod:`app.discovery.questions` use fixed rules in Russian for the user-facing
questions, and no provider call is made anywhere in this package.
"""

from __future__ import annotations

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
    "InvalidClarificationAnswer",
    "ProjectSpecification",
    "RequiredQuestionSkipped",
    "UnknownDiscoverySession",
    "apply_specification",
    "value_origins",
]
