"""The stage of a project, stated as one of twelve fixed words.

Why this module exists
----------------------

``ProjectStatus`` answers "where is the lifecycle record?" and the owner-facing
words in :mod:`app.projects.presentation` answer "what may the card say?". Neither
answers the question this stage answers: **how far did the work actually get?**
A record can be ``COMPLETED`` while its workspace holds no file, and a record can
be ``NEEDS_CHANGES`` while its files are written, compiled and previewable. The
stage is derived from the *facts* -- the manifest, the writer's verification
verdict, the acceptance verdict, the preview and (for the process words) the local
supervisor's own state -- so it cannot claim a step that did not happen.

The eight work words, in the order the product walks through them:

``PROJECT_NOT_CREATED``
    There is no artifact. Nothing was written, or the write was refused.
``FILES_CREATED``
    Files exist, and nothing has judged them yet (or the run stopped before a
    verdict). ``not_applicable`` verification also lands here: the writer wrote no
    Python file, so nothing was compiled and the word "verified" would be a claim
    it cannot support.
``VERIFICATION_RUNNING``
    A version slot exists and its verdict is still ``pending``.
``VERIFIED``
    The writer's own check passed: every Python file it wrote compiled.
``PREVIEW_AVAILABLE``
    Verified files with a *running* local preview, or verified files whose entry
    kind needs no command.
``COMPLETED``
    ``VERIFIED`` **and** the lifecycle record says ``COMPLETED`` **and** the
    acceptance passed **and** a preview is available or the version recorded an
    explicit ``preview_not_required``. The preview requirement is additional to
    the lifecycle gate in :mod:`app.projects.lifecycle`; it is never a substitute
    for verification.
``NEEDS_CHANGES``
    Files were written and judged, but the lifecycle says the work needs changes
    or the acceptance did not pass.
``FAILED``
    The run failed, or the writer's verification failed.
``READY_TO_RUN``
    Files exist, verification passed, the entry point is a runnable one, and this
    project has not been started locally yet. The word says "there is something to
    start"; it says nothing about the process.
``RUNNING``
    A local process of this project is starting, running or stopping **now**.
``STOPPED``
    The last local run was stopped by the owner and the project is not otherwise
    complete. It is a process fact, never a completion claim.
``BLOCKED``
    The last attempt to run was refused, or a recognised entry point cannot be
    launched (a missing interpreter, dependencies that are not installed).

Two rules are structural:

* **No file, no stage above ``PROJECT_NOT_CREATED``.** Every higher word requires
  a non-empty manifest, so "done" can never describe an empty project.
* **A stage is never invented.** Every argument is a fact the caller holds; a
  missing fact is passed as its conservative value, and the function has no clock,
  no I/O, no store and no provider.

Two further rules apply to the process words:

* **A started process never means ``COMPLETED``.** ``COMPLETED`` still needs the
  writer's verification, the acceptance verdict, the lifecycle record and a preview
  (available, or explicitly not required). ``RUNNING`` is reported *instead of* a
  completion word while a process is up, so a server that is merely alive can never
  be read as a finished project.
* **A process word is never inferred.** ``run_state`` is the supervisor's own word
  (:mod:`app.projects.runner`); a caller that does not have one passes ``""`` and
  the ladder behaves exactly as before.

``ARCHIVED`` is not one of these words. An archived record is not *at* a stage: it
is frozen, and the presentation layer says so with its own word. This function
returns an empty string for it rather than forcing it into the ladder.
"""

from __future__ import annotations

__all__ = [
    "BLOCKED",
    "COMPLETED",
    "FAILED",
    "FILES_CREATED",
    "NEEDS_CHANGES",
    "PREVIEW_AVAILABLE",
    "PROJECT_NOT_CREATED",
    "READY_TO_RUN",
    "RUNNING",
    "STAGE_WORDS",
    "STOPPED",
    "VERIFIED",
    "VERIFICATION_RUNNING",
    "project_stage",
]


PROJECT_NOT_CREATED = "PROJECT_NOT_CREATED"
FILES_CREATED = "FILES_CREATED"
VERIFICATION_RUNNING = "VERIFICATION_RUNNING"
VERIFIED = "VERIFIED"
PREVIEW_AVAILABLE = "PREVIEW_AVAILABLE"
COMPLETED = "COMPLETED"
NEEDS_CHANGES = "NEEDS_CHANGES"
FAILED = "FAILED"
READY_TO_RUN = "READY_TO_RUN"
RUNNING = "RUNNING"
STOPPED = "STOPPED"
BLOCKED = "BLOCKED"

#: Every word this module may return, plus the empty string for an archived record.
#: A test iterates it, so a thirteenth word cannot appear by accident.
STAGE_WORDS = (
    PROJECT_NOT_CREATED,
    FILES_CREATED,
    VERIFICATION_RUNNING,
    VERIFIED,
    PREVIEW_AVAILABLE,
    COMPLETED,
    NEEDS_CHANGES,
    FAILED,
    READY_TO_RUN,
    RUNNING,
    STOPPED,
    BLOCKED,
)

#: Process states that mean "a local process of this project is up right now".
_LIVE_RUN_STATES = frozenset({"STARTING", "RUNNING", "STOPPING"})
#: Process states that mean "the last attempt could not run".
_BLOCKED_RUN_STATES = frozenset({"REFUSED", "RUN_BLOCKED"})


def _word(value: object) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().upper()


def _plain(value: object) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().lower()


def project_stage(
    *,
    files_count: object = 0,
    verification: object = "",
    acceptance: object = "",
    lifecycle_status: object = "",
    archived: bool = False,
    preview_available: bool = False,
    preview_not_required: bool = False,
    run_state: object = "",
    runnable: bool = False,
) -> str:
    """The stage of one project, from the facts only.

    ``lifecycle_status`` is ``ProjectStatus``'s word; ``verification`` and
    ``acceptance`` are the current version's recorded verdicts; ``files_count`` is
    the current version's manifest length. ``preview_available`` is runtime state
    (a server is listening *now*), never a stored claim. ``run_state`` is the local
    supervisor's word, and ``runnable`` says whether the entry point has a command
    that could be started; both default to "unknown", which keeps the ladder
    identical for a caller that does not have them.
    """

    if archived:
        return ""
    try:
        count = int(files_count or 0)
    except (TypeError, ValueError):
        count = 0
    status = _word(lifecycle_status)
    verification_word = _plain(verification)
    acceptance_word = _plain(acceptance)
    process_state = _word(run_state)

    if count <= 0:
        # A failed run that created nothing is still "nothing was created": the
        # absence of the artifact is the fact the owner needs, and the failure is
        # reported by the lifecycle status beside this word.
        return PROJECT_NOT_CREATED

    # A live process is a fact about *now*, so it outranks a stored verdict: while a
    # server is up the card must not claim the work is finished either.
    if process_state in _LIVE_RUN_STATES:
        return RUNNING

    if status == "FAILED":
        return FAILED
    if verification_word == "pending":
        return VERIFICATION_RUNNING
    if verification_word == "failed":
        return FAILED
    if verification_word in ("", "not_run"):
        return FILES_CREATED

    # Verification ran and did not fail. Only ``passed`` supports the word
    # "verified"; ``not_applicable`` means no Python file was compiled, so it can
    # never support "completed" either.
    if verification_word != "passed":
        return PREVIEW_AVAILABLE if preview_available else FILES_CREATED

    if status == "NEEDS_CHANGES" or acceptance_word == "failed":
        return NEEDS_CHANGES
    if status == "COMPLETED" and acceptance_word == "passed":
        if preview_available or preview_not_required:
            return COMPLETED
        return VERIFIED
    if preview_available:
        return PREVIEW_AVAILABLE
    if process_state in _BLOCKED_RUN_STATES:
        # The work is not complete, and the last attempt to run it was refused or
        # blocked. That is the actionable fact, so it is the word.
        return BLOCKED
    if process_state == "STOPPED":
        return STOPPED
    if runnable:
        # Something can be started and nothing has been started yet (or the last
        # start finished): the honest word is "ready", not "verified and done".
        return READY_TO_RUN
    return VERIFIED
