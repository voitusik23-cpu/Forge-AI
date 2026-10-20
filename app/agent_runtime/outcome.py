"""The outcome of a run, stated as one provider-neutral value.

This module answers exactly one question: *what is the truthful state of this
run's work?* It is deliberately **pure and deterministic** -- standard library
only, no clock, no I/O, no provider, no project store -- so the same facts always
produce the same answer, and a caller can test the rule without a run.

Why it exists
-------------

``TaskResult.success`` says "the provider returned text". It does not say that a
project was created, that files were written, that the specification was
confirmed, that verification passed, or that acceptance passed. Reporting
"COMPLETED" from ``success`` alone therefore told the owner a project existed
when the model had only asked clarifying questions. :func:`derive_outcome`
replaces that single boolean with the eight outcomes the product actually has,
and :func:`app.projects.lifecycle.completion_blockers` and
:func:`app.api.service.ForgeApiService` both call *this* function, so there is
exactly one completion rule in the codebase.

The precedence is fixed and is implemented in this order
--------------------------------------------------------

1. a provider/execution error, or a settled run that is not a success -> ``FAILED``;
2. an answer that is a list of clarifying questions (or a discovery session
   still ``NEEDS_CLARIFICATION``) -> ``CLARIFICATION_REQUIRED``;
3. no project record, or an unconfirmed specification -> ``PLAN_READY`` when a
   plan exists and waits for confirmation, otherwise ``SPECIFICATION_READY``;
4. a run still in flight -> ``EXECUTION_RUNNING``;
5. verification not run -> ``VERIFICATION_REQUIRED``;
6. verification or acceptance failed -> ``NEEDS_CHANGES``;
7. otherwise ``PROJECT_COMPLETED`` -- but only when **every** fact holds:
   the project exists, its workspace exists, it recorded at least one artifact
   (or the run explicitly produced no files), verification passed, acceptance
   passed, and the artifact manifest is non-empty. Anything short of that is
   ``NEEDS_CHANGES``.

The last rule is the guard the owner needed: a text answer can never reach it,
because no project exists to carry the evidence.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Iterable, Optional, Tuple

__all__ = [
    "CLARIFICATION_REQUIRED",
    "COMPLETED_STATES",
    "FAILED_RUN_STATES",
    "IN_FLIGHT_RUN_STATES",
    "RunOutcome",
    "TERMINAL_SUCCESS_RUN_STATES",
    "USER_FACING_STATES",
    "completion_blockers",
    "derive_outcome",
    "extract_clarifying_questions",
    "honest_state",
    "looks_like_clarification",
    "state_for_outcome",
]


#: The verification word the rest of the codebase records for a passing check.
#: ``app.projects.models.VERIFICATION_PASSED`` has the same value; it is repeated
#: here as a literal because this module is stdlib-only and must not import the
#: project layer (which imports this module).
VERIFICATION_PASSED_WORD = "passed"

#: The acceptance word recorded for a passing acceptance judgement.
ACCEPTANCE_PASSED_WORD = "passed"

#: Verification values that mean "no verification was performed". They are named
#: separately from "failed": nothing was checked, so nothing passed or failed.
VERIFICATION_NOT_RUN_WORDS = frozenset({"", "not_run", "pending"})

#: Run-state labels the run store uses for a terminal success.
TERMINAL_SUCCESS_RUN_STATES = frozenset({"COMPLETED", "SUCCESS", "PASSED"})

#: Run-state labels that are already a failure, whatever the answer text says.
FAILED_RUN_STATES = frozenset(
    {"FAILED", "ERROR", "CANCELLED", "CANCELED", "ABORTED", "TIMED_OUT", "TIMEOUT", "REJECTED"}
)

#: Run-state labels that mean the run has not settled yet. A run in one of these
#: states is *not* a failure merely because it has not succeeded yet.
IN_FLIGHT_RUN_STATES = frozenset(
    {"CREATED", "PENDING", "QUEUED", "DISPATCHED", "STARTED", "RUNNING", "IN_PROGRESS", "REVISING", "WAITING_FOR_APPROVAL"}
)

#: The state reserved for a genuinely finished project. Nothing else maps to it.
COMPLETED_STATES = frozenset({"COMPLETED", "SUCCESS"})


class RunOutcome(str, Enum):
    """What a run truthfully produced, independent of any provider."""

    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    SPECIFICATION_READY = "SPECIFICATION_READY"
    PLAN_READY = "PLAN_READY"
    EXECUTION_RUNNING = "EXECUTION_RUNNING"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
    PROJECT_COMPLETED = "PROJECT_COMPLETED"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    FAILED = "FAILED"


CLARIFICATION_REQUIRED = RunOutcome.CLARIFICATION_REQUIRED

#: The user-facing ``state``/``status`` word for each outcome.
#:
#: ``COMPLETED`` is intentionally reachable from exactly one outcome. Every other
#: success-ish outcome names what is still missing instead of borrowing the word
#: "completed", because that word is what the owner reads as "the project exists".
USER_FACING_STATES = {
    RunOutcome.CLARIFICATION_REQUIRED: "CLARIFICATION_REQUIRED",
    RunOutcome.SPECIFICATION_READY: "SPECIFICATION_READY",
    RunOutcome.PLAN_READY: "PLAN_READY",
    RunOutcome.EXECUTION_RUNNING: "RUNNING",
    RunOutcome.VERIFICATION_REQUIRED: "VERIFICATION_REQUIRED",
    RunOutcome.PROJECT_COMPLETED: "COMPLETED",
    RunOutcome.NEEDS_CHANGES: "NEEDS_CHANGES",
    RunOutcome.FAILED: "FAILED",
}

#: A leading list marker ("- ", "* ", "1. ", "1) ", "a) ") on an answer line.
_LIST_MARKER = re.compile(r"^\s*(?:[-*\u2022\u2013\u2014]|\d+[.)]|[a-zA-Z\u0430-\u044f]\))\s+")


def _word(value: object) -> str:
    """Normalise a state/verification/acceptance value to a bare upper-case word."""

    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw).strip().upper()


def _plain(value: object) -> str:
    """Normalise a verification/acceptance value to a bare lower-case word."""

    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()


def extract_clarifying_questions(text: object) -> Tuple[str, ...]:
    """The questions an answer asks, in order, without duplicates.

    The heuristic is intentionally narrow: a question is a line that **ends** with
    ``?``, with any leading list marker removed. A sentence that merely mentions a
    question is not one, and prose with a single trailing question mark is not a
    clarification request by itself (see :func:`looks_like_clarification`).
    """

    questions: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or not line.endswith("?"):
            continue
        cleaned = _LIST_MARKER.sub("", line).strip()
        if not cleaned.endswith("?"):
            continue
        if cleaned not in questions:
            questions.append(cleaned)
    return tuple(questions)


def looks_like_clarification(text: object) -> bool:
    """Whether an answer is a *list* of clarifying questions.

    Two or more question lines is a list. One question line is not: a model may
    end an otherwise complete answer with a rhetorical question, and refusing to
    record its result over a single ``?`` would be the mirror image of the defect
    this module exists to fix.
    """

    return len(extract_clarifying_questions(text)) >= 2


def derive_outcome(
    *,
    run_state: object = "",
    run_success: Optional[bool] = None,
    error: object = "",
    answer_text: object = "",
    project_exists: bool = False,
    project_workspace_exists: bool = False,
    artifact_count: int = 0,
    artifact_manifest: Iterable[object] = (),
    verification: object = "",
    acceptance: object = "",
    specification_confirmed: bool = False,
    plan_exists: bool = False,
    plan_awaiting_confirmation: bool = False,
    discovery_state: object = "",
    no_file_result: bool = False,
) -> RunOutcome:
    """Classify one run. Pure: no I/O, no clock, no ambient state.

    Every input is a fact the caller already holds; a fact the caller does not
    hold stays at its default, which is the conservative value (``False``/``0``/
    empty), so an unknown fact can never *raise* the outcome.

    ``no_file_result`` stands in for both the artifact count and the manifest: a
    run that explicitly produced no files has no artifact to record, and claiming
    one would be a fabrication. It never substitutes for ``verification`` or
    ``acceptance``.
    """

    state = _word(run_state)
    in_flight = state in IN_FLIGHT_RUN_STATES
    settled_success = state in TERMINAL_SUCCESS_RUN_STATES

    # 1. A provider/execution error, or a settled run that is not a success.
    if str(error or "").strip():
        return RunOutcome.FAILED
    if state in FAILED_RUN_STATES:
        return RunOutcome.FAILED
    if run_success is False and not in_flight:
        return RunOutcome.FAILED
    if state and not in_flight and not settled_success and run_success is not True:
        return RunOutcome.FAILED

    # 2. An answer that is a list of clarifying questions.
    if _word(discovery_state) == "NEEDS_CLARIFICATION":
        return RunOutcome.CLARIFICATION_REQUIRED
    if looks_like_clarification(answer_text):
        return RunOutcome.CLARIFICATION_REQUIRED

    # 3. No project record, or an unconfirmed specification: text is all there is.
    if not project_exists or not specification_confirmed:
        if plan_exists and plan_awaiting_confirmation:
            return RunOutcome.PLAN_READY
        return RunOutcome.SPECIFICATION_READY

    # 4. Still in flight.
    if in_flight:
        return RunOutcome.EXECUTION_RUNNING

    # 5. Nothing verified the work yet.
    verification_word = _plain(verification)
    if verification_word in VERIFICATION_NOT_RUN_WORDS:
        return RunOutcome.VERIFICATION_REQUIRED

    # 6. Verification or acceptance did not pass.
    if (
        verification_word != VERIFICATION_PASSED_WORD
        or _plain(acceptance) != ACCEPTANCE_PASSED_WORD
    ):
        return RunOutcome.NEEDS_CHANGES

    # 7. Completion needs every fact, not merely a finished run.
    manifest = tuple(artifact_manifest or ())
    recorded_files = int(artifact_count or 0) > 0 or bool(no_file_result)
    manifest_recorded = len(manifest) > 0 or bool(no_file_result)
    if (
        bool(project_exists)
        and bool(project_workspace_exists)
        and recorded_files
        and manifest_recorded
    ):
        return RunOutcome.PROJECT_COMPLETED
    return RunOutcome.NEEDS_CHANGES


def completion_blockers(
    *,
    run_state: object = "",
    failed: bool = False,
    verification: object = "",
    acceptance: object = "",
    artifact_count: int = 0,
    wrote_files: bool = False,
    reverified: bool = False,
) -> Tuple[str, ...]:
    """Why a lifecycle event does not justify ``COMPLETED``; empty when it does.

    This is the single completion gate. :func:`derive_outcome` decides, and the
    tuple only *names* the facts that are missing so a refusal can say all of them
    at once. The last line is the safety net: if no named blocker was produced but
    the classifier still refused completion (a missing manifest, for example), the
    refusal is reported rather than silently becoming an empty tuple.

    ``reverified`` carries the same meaning it has in the lifecycle: the run
    already finished and the recorded acceptance is its own judgement, so the
    run-state row is not re-read -- verification, acceptance and the artifact row
    all still apply.
    """

    state = _word(run_state)
    terminal_success = (
        not failed and state in TERMINAL_SUCCESS_RUN_STATES
    )
    verdict = derive_outcome(
        run_state=run_state,
        run_success=True if reverified else (terminal_success and not failed),
        error="" if not failed else "run_failed",
        answer_text="",
        project_exists=True,
        project_workspace_exists=True,
        artifact_count=artifact_count,
        artifact_manifest=("artifact",) if int(artifact_count or 0) > 0 else (),
        verification=verification,
        acceptance=acceptance,
        specification_confirmed=True,
        no_file_result=not wrote_files,
    )
    if verdict is RunOutcome.PROJECT_COMPLETED:
        return ()

    blockers: list[str] = []
    if not reverified and not terminal_success:
        blockers.append("run_not_terminal_success")
    verification_word = _plain(verification)
    if verification_word != VERIFICATION_PASSED_WORD:
        blockers.append(f"verification_{verification_word or 'missing'}")
    acceptance_word = _plain(acceptance)
    if acceptance_word != ACCEPTANCE_PASSED_WORD:
        blockers.append(f"acceptance_{acceptance_word or 'missing'}")
    if wrote_files and int(artifact_count or 0) <= 0:
        blockers.append("artifacts_missing")
    if not blockers:
        # The classifier refused completion for a reason no named row covers (the
        # manifest). Saying "no blockers" here would contradict the verdict.
        blockers.append("project_incomplete")
    return tuple(blockers)


def state_for_outcome(outcome: object) -> str:
    """The user-facing state word for ``outcome`` ("" for an unknown outcome)."""

    if isinstance(outcome, RunOutcome):
        return USER_FACING_STATES[outcome]
    word = str(getattr(outcome, "value", outcome) or "").strip().upper()
    for member in RunOutcome:
        if member.value == word:
            return USER_FACING_STATES[member]
    return ""


def honest_state(raw_state: object, outcome: Optional[RunOutcome]) -> str:
    """The user-facing state for a run whose raw state is ``raw_state``.

    The gate may only ever *downgrade* a success claim. A run whose raw state is
    ``COMPLETED``/``SUCCESS`` is reported as the outcome's own state word, so a
    text-only answer can no longer surface as ``COMPLETED``; every other raw state
    is passed through untouched, because this function is not a lifecycle and has
    no business rewriting a failure, a denial or a replay.
    """

    text = str(getattr(raw_state, "value", raw_state) or "").strip()
    if outcome is None:
        return text
    if _word(text) not in COMPLETED_STATES:
        return text
    if outcome is RunOutcome.PROJECT_COMPLETED:
        return text
    return state_for_outcome(outcome) or text
