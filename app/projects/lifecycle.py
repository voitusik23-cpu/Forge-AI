"""The deterministic project lifecycle: an explicit table and one gate.

Two things live here and nothing else:

1. **The allowed-transition table.** Every move a project record may make is a row
   in :data:`ALLOWED_TRANSITIONS`. A move that is not in the table raises
   :class:`IllegalTransition`; nothing is silently coerced, and there is no
   "nearest legal state" fallback. That is what makes the record auditable: a
   status can only be the result of a named event from a named status.

2. **The completion gate.** ``COMPLETED`` is not a status a caller may simply
   request. :func:`completion_blockers` states the evidence a run must carry, and
   :func:`advance` refuses the transition unless every one of them holds.

The gate, stated exactly -- and it is *one* rule, implemented in
:func:`app.agent_runtime.outcome.derive_outcome`, which the run response calls too:

* the run state is terminal-success (``COMPLETED``/``SUCCESS``) and the run did
  not fail with a provider or execution error;
* ``verification == passed``;
* ``acceptance == passed``;
* if the run wrote files, at least one artifact was recorded -- a run that wrote
  nothing has no artifact to record, and claiming one would be a fabrication.

A run that finished but does not satisfy all four is ``NEEDS_CHANGES`` (its
verification or acceptance did not pass, or its artifacts are missing) or
``FAILED`` (it carried a provider/execution error). "Finished" is never treated as
"successful", and there is no optimistic completion path anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Optional, Tuple

from app.projects.models import (
    LifecycleProject,
    ProjectError,
    ProjectStatus,
    utc_now,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "CompletionNotAllowed",
    "IllegalTransition",
    "LifecycleEvent",
    "ProjectLifecycleError",
    "TERMINAL_SUCCESS_STATES",
    "advance",
    "completion_blockers",
    "describe_transitions",
]


#: Run-state labels the existing run store classifies as a terminal success.
TERMINAL_SUCCESS_STATES = frozenset({"COMPLETED", "SUCCESS"})


class ProjectLifecycleError(ProjectError):
    """Base class for every refusal of the lifecycle."""

    code = "project_lifecycle_error"


class IllegalTransition(ProjectLifecycleError):
    """The requested event cannot happen from the project's current status."""

    code = "illegal_project_transition"

    def __init__(self, message: str, *, status: Optional[ProjectStatus] = None,
                 event: str = "") -> None:
        super().__init__(message)
        self.status = status.value if status is not None else ""
        self.event = event


class CompletionNotAllowed(IllegalTransition):
    """``COMPLETED`` was requested without the evidence that justifies it."""

    code = "project_not_completable"

    def __init__(self, blockers: Iterable[str]) -> None:
        self.blockers: Tuple[str, ...] = tuple(blockers)
        super().__init__(
            "the project cannot become COMPLETED: " + ", ".join(self.blockers)
        )


@dataclass(frozen=True)
class LifecycleEvent:
    """One thing that happened to a project, with the evidence it carries.

    ``name`` is the event's only routing input. The remaining fields are the
    evidence the completion gate reads: they are filled from the run's own durable
    record, never guessed, and they are never used to pick the event -- a caller
    that wants ``completed`` must state it as ``completed`` and be refused if the
    evidence does not support it.
    """

    name: str
    run_id: str = ""
    run_state: str = ""
    failed: bool = False
    verification: str = ""
    acceptance: str = ""
    artifact_count: int = 0
    wrote_files: bool = False
    provider: str = ""
    model: str = ""
    reason: str = ""
    #: True only for the re-judgement of an **already recorded** version
    #: (:meth:`app.projects.service.ProjectService.verify_again`). That judgement has
    #: no run to read a state from, and does not need one: the version's recorded
    #: ``acceptance == passed`` *is* the existing classification's statement that its
    #: run reached a terminal success. Every other event keeps the full gate.
    reverified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("a lifecycle event needs a non-empty name")
        if isinstance(self.artifact_count, bool) or not isinstance(
            self.artifact_count, int
        ):
            raise ValueError("artifact_count must be an integer")

    @property
    def is_terminal_success(self) -> bool:
        """True when the run finished successfully, by the run store's own labels."""

        if self.failed:
            return False
        return str(self.run_state or "").strip().upper() in TERMINAL_SUCCESS_STATES


#: Every legal move. ``from_status -> event name -> to_status``.
#:
#: Read this table as the whole lifecycle contract. It is data, not code, so a
#: reader can check it against the product rules without following branches, and a
#: test can iterate it to prove that every row works and every other pair refuses.
ALLOWED_TRANSITIONS: Mapping[ProjectStatus, Mapping[str, ProjectStatus]] = {
    ProjectStatus.DISCOVERY: {
        "clarification_required": ProjectStatus.NEEDS_CLARIFICATION,
        "ready_for_planning": ProjectStatus.READY_FOR_PLANNING,
    },
    ProjectStatus.NEEDS_CLARIFICATION: {
        "ready_for_planning": ProjectStatus.READY_FOR_PLANNING,
    },
    ProjectStatus.READY_FOR_PLANNING: {
        "plan_created": ProjectStatus.PLANNED,
    },
    ProjectStatus.PLANNED: {
        "await_confirmation": ProjectStatus.WAITING_FOR_CONFIRMATION,
        "run_started": ProjectStatus.RUNNING,
    },
    ProjectStatus.WAITING_FOR_CONFIRMATION: {
        "confirmed": ProjectStatus.PLANNED,
        "run_started": ProjectStatus.RUNNING,
    },
    ProjectStatus.RUNNING: {
        "run_finished": ProjectStatus.REVIEWING,
        "run_failed": ProjectStatus.FAILED,
    },
    ProjectStatus.REVIEWING: {
        "completed": ProjectStatus.COMPLETED,
        "changes_required": ProjectStatus.NEEDS_CHANGES,
        "run_failed": ProjectStatus.FAILED,
    },
    ProjectStatus.NEEDS_CHANGES: {
        "plan_created": ProjectStatus.PLANNED,
        "run_started": ProjectStatus.RUNNING,
        "completed": ProjectStatus.COMPLETED,
    },
    ProjectStatus.COMPLETED: {
        "plan_created": ProjectStatus.PLANNED,
        "run_started": ProjectStatus.RUNNING,
        "changes_required": ProjectStatus.NEEDS_CHANGES,
    },
    ProjectStatus.FAILED: {
        "plan_created": ProjectStatus.PLANNED,
        "run_started": ProjectStatus.RUNNING,
    },
    ProjectStatus.ARCHIVED: {
        "restore": ProjectStatus.PLANNED,  # the real target is ``previous_status``
    },
}

#: Archiving is legal from every working status; it is added here rather than
#: repeated eleven times in the table above.
_ARCHIVE_EVENT = "archived"


def completion_blockers(event: LifecycleEvent) -> Tuple[str, ...]:
    """Why ``event`` does not justify ``COMPLETED``; empty when it does.

    The **decision** is not made here. It is made by
    :func:`app.agent_runtime.outcome.derive_outcome`, the single completion rule
    shared with the API response assembly, so the lifecycle and the run response
    can never disagree about what ``COMPLETED`` means. This function only
    translates the event into that classifier's facts and names the missing ones.

    A tuple rather than a boolean so the refusal names every missing fact at once,
    and so a test can assert the exact rule that fired. The last rule is
    conditional on purpose: a run that wrote nothing has no artifact to record, and
    claiming one would be a fabrication.

    An event with ``reverified=True`` skips only the run-state row, and only because
    its run already finished and its acceptance is the recorded judgement of that:
    verification, acceptance and the artifact rule all still apply.
    """

    # Imported here, not at module scope: ``app.agent_runtime`` pulls in the
    # canonical harness, which imports ``app.projects.state``, so a module-scope
    # import would close a cycle between the project layer and the runtime.
    from app.agent_runtime.outcome import completion_blockers as _gate

    return _gate(
        run_state=event.run_state,
        failed=event.failed,
        verification=event.verification,
        acceptance=event.acceptance,
        artifact_count=event.artifact_count,
        wrote_files=event.wrote_files,
        reverified=event.reverified,
    )


def _coerce_event(event: object) -> LifecycleEvent:
    if isinstance(event, LifecycleEvent):
        return event
    if isinstance(event, str):
        return LifecycleEvent(name=event)
    raise TypeError("a lifecycle event must be a LifecycleEvent or an event name")


def advance(project: LifecycleProject, event: object, *, now: str = "") -> LifecycleProject:
    """Apply one event to ``project`` and return the updated record.

    The input record is not mutated: the caller persists the returned copy, so a
    refusal leaves the stored record exactly as it was. ``now`` exists so a caller
    can stamp the transition with the run's own time instead of the wall clock.
    """

    resolved = _coerce_event(event)
    status = project.status
    timestamp = now or utc_now()

    if resolved.name == "restore":
        if status is not ProjectStatus.ARCHIVED:
            raise IllegalTransition(
                f"only an archived project can be restored (status: {status.value})",
                status=status,
                event=resolved.name,
            )
        previous = project.previous_status
        if previous is None or previous is ProjectStatus.ARCHIVED:
            raise IllegalTransition(
                "the archived project carries no recorded previous status, so "
                "restoring it would have to invent one",
                status=status,
                event=resolved.name,
            )
        return replace(
            project,
            status=previous,
            archived=False,
            previous_status=None,
            updated_at=timestamp,
        )

    if status is ProjectStatus.ARCHIVED:
        raise IllegalTransition(
            "an archived project is frozen: restore it before any other event",
            status=status,
            event=resolved.name,
        )

    if resolved.name == _ARCHIVE_EVENT:
        return replace(
            project,
            status=ProjectStatus.ARCHIVED,
            archived=True,
            previous_status=status,
            updated_at=timestamp,
        )

    table = ALLOWED_TRANSITIONS.get(status, {})
    target = table.get(resolved.name)
    if target is None:
        allowed = ", ".join(sorted(table)) or "nothing"
        raise IllegalTransition(
            f"'{resolved.name}' is not allowed from {status.value} "
            f"(allowed: {allowed})",
            status=status,
            event=resolved.name,
        )

    if target is ProjectStatus.COMPLETED:
        blockers = completion_blockers(resolved)
        if blockers:
            raise CompletionNotAllowed(blockers)

    return replace(project, status=target, archived=False, updated_at=timestamp)


def describe_transitions() -> Tuple[Tuple[str, str, str], ...]:
    """The table as ``(from, event, to)`` rows, for documentation and reporting."""

    rows: list[Tuple[str, str, str]] = []
    for status in ProjectStatus:
        for event_name, target in ALLOWED_TRANSITIONS.get(status, {}).items():
            rows.append((status.value, event_name, target.value))
        if status is not ProjectStatus.ARCHIVED:
            rows.append((status.value, _ARCHIVE_EVENT, ProjectStatus.ARCHIVED.value))
    return tuple(rows)
