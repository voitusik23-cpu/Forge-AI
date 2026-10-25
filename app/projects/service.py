"""The project lifecycle service: turn a confirmed run into a durable project.

This module is the *only* thing that creates a project record, materialises a run's
files into a project workspace, and advances a project's status. It creates no
executor and no second run system:

* the planning, dispatch, execution, verification and acceptance of a run are the
  repository's existing ones -- this layer is called *after* the existing
  ``ForgeApiService.run_task_detailed`` returned, and it reads that run's durable
  record rather than re-deriving anything;
* the files it copies into the project workspace are the files the *existing*
  ``app.agent_runtime.code_writer.apply_changes`` already validated, wrote and
  compiled, and it copies them rather than writing code itself;
* the verification word it records is the writer's own ``verification`` value, and
  the acceptance word it records is the existing run-record classification. Neither
  is invented here.

What it adds is durability and judgement: a project record with versions, an
append-only artifact manifest (size + SHA-256 + create/update + verification
relevance, never contents), and the lifecycle gate from
:mod:`app.projects.lifecycle`, which refuses ``COMPLETED`` unless the run really
was a terminal success, verified, accepted, and (when it wrote files) recorded at
least one artifact.

The two repository dependencies are deliberate and are the repository's own
existing implementations rather than re-written ones: the credential-shape screen
(``app.context.validation.find_forbidden_value_content``) and its redaction marker
(``app.discovery.models.redaction_marker``). Everything else in this package is
standard library only.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.context.validation import find_forbidden_value_content
from app.discovery.models import redaction_marker
from app.projects.folders import UnsafeProjectFolder
from app.projects.lifecycle import (
    IllegalTransition,
    LifecycleEvent,
    advance,
    completion_blockers,
)
from app.projects.models import (
    ACCEPTANCE_FAILED,
    ACCEPTANCE_NOT_EVALUATED,
    ACCEPTANCE_PASSED,
    ACCEPTANCE_PENDING,
    ARTIFACT_CHANGE_CREATE,
    ARTIFACT_CHANGE_UPDATE,
    MAX_VERSION_FILES,
    LifecycleProject,
    ProjectError,
    ProjectStatus,
    ProjectVersion,
    ChangeRequest,
    ArtifactRef,
    VERIFICATION_FAILED,
    VERIFICATION_NOT_APPLICABLE,
    VERIFICATION_NOT_RUN,
    VERIFICATION_PASSED,
    VERIFICATION_PENDING,
    is_safe_relative_path,
    kind_for_path,
    utc_now,
    verification_relevance_for_path,
)
from app.projects.store import (
    ProjectDeleted,
    ProjectStore,
    UnknownProject,
    is_safe_project_id,
    new_project_id,
)

__all__ = [
    "MAX_ARTIFACT_BYTES",
    "NewVersionTask",
    "ProjectService",
    "RunFacts",
    "UnknownProjectVersion",
    "InvalidChangeRequest",
    "run_facts",
]


#: The largest single artifact this layer will copy into a project workspace. The
#: writer's own cap is 200 KiB for a whole run, so this can never truncate a real
#: run; it keeps a corrupted or hand-made record from making one copy unbounded.
MAX_ARTIFACT_BYTES = 512 * 1024

#: The longest change request that is stored and handed to the run path.
MAX_CHANGE_REQUEST_CHARS = 4000

#: Classification outcomes (from ``app.runtime.run_store``) that prove a success.
_TERMINAL_SUCCESS = "TERMINAL_SUCCESS"

#: Classification outcomes that prove a failure of some kind.
_TERMINAL_FAILURES = frozenset(
    {
        "TERMINAL_FAILURE",
        "TERMINAL_TIMEOUT",
        "TERMINAL_CANCELLED",
        "TERMINAL_REFUSED",
    }
)


# --------------------------------------------------------------------------- #
# the per-project critical section
# --------------------------------------------------------------------------- #

#: One lock per (store root, project id), shared by every ``ProjectService`` instance
#: in this process that uses the same store. A mutating operation holds it across
#: *read -> marker check -> write*, so a delete and a verification can never
#: interleave their two halves: whichever arrives second waits for the first.
#:
#: The ordering this buys is the one the owner allowed: DELETE waits for an in-flight
#: verification to finish and then moves the project. The ordering is not the only
#: defence -- another *process* holds no such lock, so the durable marker (the trash
#: entry, ``ProjectStore.is_deleted``) is re-checked by ``ProjectStore.write`` on the
#: exact write path. Locks are kept for the process lifetime: there is one per
#: project id this process ever touched, which is a handful, and dropping one while
#: a second caller waits on it would let a third caller create a different lock and
#: run concurrently with the waiter.
_PROJECT_LOCKS: Dict[str, threading.Lock] = {}
_PROJECT_LOCKS_GUARD = threading.Lock()


def _lock_for_project(store_root: object, project_id: str) -> threading.Lock:
    """The lock a read-then-write of ``project_id`` in this process must hold."""

    key = f"{Path(store_root).resolve(strict=False)}|{project_id}"
    with _PROJECT_LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROJECT_LOCKS[key] = lock
        return lock


class InvalidChangeRequest(ProjectError):
    """The requested change is empty or is not usable as task text."""

    code = "invalid_change_request"


class UnknownProjectVersion(ProjectError):
    """No version is recorded under that number for this project."""

    code = "unknown_project_version"


# --------------------------------------------------------------------------- #
# the run facts this layer reads
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunFacts:
    """What one finished run proves, read from its own record and nothing else.

    ``written_files`` is the writer's own file list (path + action), never a
    re-parse of the model's answer. ``verification`` is the writer's own value.
    ``acceptance`` is derived from the existing run-record classification, which is
    computed from the persisted snapshot rather than from the run object in memory.
    """

    run_id: str
    run_state: str
    failed: bool
    failure_reason: str
    provider: str
    model: str
    verification: str
    acceptance: str
    wrote_files: bool
    written_files: Tuple[Mapping[str, Any], ...] = ()
    recorded_at: str = ""


def _field(source: object, *names: str) -> Any:
    """Read the first present, non-empty value of ``names`` from a dict or object."""

    for name in names:
        value: Any = None
        found = False
        if isinstance(source, Mapping):
            if name in source:
                value = source[name]
                found = True
        else:
            value = getattr(source, name, None)
            found = value is not None
        if not found or value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _code_write_of(run: object) -> Mapping[str, Any]:
    """The guarded writer's own outcome for this run, when it recorded one."""

    direct = _field(run, "code_write")
    if isinstance(direct, Mapping):
        return dict(direct)
    for holder_name in ("project_state", "snapshot"):
        holder = _field(run, holder_name)
        if holder is None:
            continue
        state = _field(holder, "project_state") if holder_name == "snapshot" else holder
        if isinstance(state, Mapping):
            candidate = state.get("code_write")
            if isinstance(candidate, Mapping):
                return dict(candidate)
    return {}


def _normalise_verification(code_write: Mapping[str, Any]) -> str:
    """The writer's verdict, in this layer's four-value vocabulary.

    Nothing is upgraded on the way: a run that wrote nothing stays ``not_run`` even
    though it finished, because "no check was performed" is not "the check passed".
    """

    status = str(code_write.get("status", "") or "").strip()
    verification = str(code_write.get("verification", "") or "").strip()
    if status == "applied":
        if verification in (VERIFICATION_PASSED, VERIFICATION_NOT_APPLICABLE):
            return verification
        return VERIFICATION_FAILED if verification == VERIFICATION_FAILED else VERIFICATION_NOT_RUN
    if status == "verification_failed":
        return VERIFICATION_FAILED
    # ``rejected``, ``no_changes``, or no writer record at all: nothing was checked.
    return VERIFICATION_NOT_RUN


def run_facts(run: object) -> RunFacts:
    """Normalise whatever the run path returned into :class:`RunFacts`.

    Accepts the API's own run record (a mapping, with ``state``, ``error``,
    ``classification`` and ``project_state.code_write``), a persisted run, or a
    ``Run``/``TaskRunResponse`` object. A fact that cannot be read stays empty, and
    empty never means success: an unknown outcome is treated as not-a-success.
    """

    run_id = str(_field(run, "run_id", "id") or "")
    raw_state = _field(run, "state", "status") or ""
    run_state = str(getattr(raw_state, "value", raw_state) or "")
    error_text = str(_field(run, "error") or "")
    recorded_at = str(_field(run, "updated_at", "completed_at", "timestamp") or "")

    classification = _field(run, "classification")
    outcome = ""
    classified_success: Optional[bool] = None
    if isinstance(classification, Mapping):
        outcome = str(classification.get("outcome", "") or "")
        raw_success = classification.get("is_success")
        if isinstance(raw_success, bool):
            classified_success = raw_success
    elif classification is not None:
        raw_outcome = getattr(classification, "outcome", None)
        outcome = str(getattr(raw_outcome, "value", raw_outcome) or "")
        raw_success = getattr(classification, "is_success", None)
        if isinstance(raw_success, bool):
            classified_success = raw_success

    if classified_success is not None:
        terminal_success = classified_success
    elif outcome:
        terminal_success = outcome == _TERMINAL_SUCCESS
    else:
        terminal_success = run_state.strip().upper() in {"COMPLETED", "SUCCESS"}

    failed = (not terminal_success) or bool(error_text)
    failure_reason = error_text or ("" if terminal_success else (outcome or run_state))

    explicit_acceptance = str(_field(run, "acceptance") or "").strip()
    if explicit_acceptance in {
        ACCEPTANCE_PASSED,
        ACCEPTANCE_FAILED,
        ACCEPTANCE_NOT_EVALUATED,
    }:
        acceptance = explicit_acceptance
    elif failed:
        acceptance = ACCEPTANCE_FAILED
    elif terminal_success:
        acceptance = ACCEPTANCE_PASSED
    else:
        acceptance = ACCEPTANCE_NOT_EVALUATED

    code_write = _code_write_of(run)
    verification = _normalise_verification(code_write) if code_write else VERIFICATION_NOT_RUN
    raw_files = code_write.get("files") if code_write else None
    written: List[Mapping[str, Any]] = []
    if isinstance(raw_files, Sequence) and not isinstance(raw_files, (str, bytes)):
        for item in list(raw_files)[:MAX_VERSION_FILES]:
            if isinstance(item, Mapping) and str(item.get("path", "") or "").strip():
                written.append(
                    {
                        "path": str(item.get("path", "")),
                        "action": str(item.get("action", "") or ""),
                    }
                )
    wrote_files = (
        str(code_write.get("status", "") or "").strip() == "applied" and bool(written)
    )

    return RunFacts(
        run_id=run_id,
        run_state=run_state,
        failed=failed,
        failure_reason=failure_reason,
        provider=str(_field(run, "provider_used", "provider") or ""),
        model=str(_field(run, "model_used", "model_name") or ""),
        verification=verification,
        acceptance=acceptance,
        wrote_files=wrote_files,
        written_files=tuple(written),
        recorded_at=recorded_at,
    )


# --------------------------------------------------------------------------- #
# results of a service call
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NewVersionTask:
    """What ``new_version`` hands back: the staged version and the task to submit.

    ``task_text`` is what the caller submits to the **existing** task route. The
    service does not run it; the existing run path does, and the confirmed
    discovery session's specification is still prepended there.

    Two facts make a repeated press harmless, and both are published so a caller can
    say them in plain words instead of running a second time:

    ``already_applied``
        the last version recorded for **these same words** is the current version and
        it holds files, so there is nothing left to do. ``task_text`` is empty and a
        caller must not submit anything.
    ``retargeted``
        the last version recorded for these same words holds **no files**, so the
        retry writes into that same version number instead of appending another empty
        one. ``task_text`` is a real task and may be submitted.
    """

    project: LifecycleProject
    version: int
    task_text: str
    change_request: ChangeRequest
    already_applied: bool = False
    retargeted: bool = False
    previous_run_id: str = ""


def change_fingerprint(text: object) -> str:
    """The identity of a change request: its words, ignoring spacing and letter case.

    Measured defect: the owner pressed «Внести изменения» three times on one sentence
    and each press recorded another version with zero files (v3, v4, v5). The presses
    differed by nothing at all, so the identity of a change is its text -- collapsed
    whitespace, case folded -- and never the moment it was typed.
    """

    return " ".join(str(text or "").split()).casefold()


# --------------------------------------------------------------------------- #
# the service
# --------------------------------------------------------------------------- #


class ProjectService:
    """Create, advance, version and archive persistent projects."""

    def __init__(self, store: Optional[ProjectStore] = None) -> None:
        self._store = store if store is not None else ProjectStore()

    # ------------------------------------------------------------------ reads
    @property
    def store(self) -> ProjectStore:
        return self._store

    def open_project(self, project_id: object) -> LifecycleProject:
        """The stored record, or :class:`UnknownProject`. A **read**, never a write."""

        if not is_safe_project_id(project_id):
            raise UnknownProject(str(project_id))
        return self._store.require(project_id)

    def require_active(self, project_id: object) -> LifecycleProject:
        """The record of an **active** project, or a typed refusal.

        Called at the top of every mutating operation, inside that operation's
        per-project lock, immediately before the operation reads what it needs. The
        deletion marker is checked *before* the record: a record that a late writer
        already put back is still a deleted project, and that resurrected shape is
        precisely what this guard exists to refuse.

        ``ProjectDeleted`` (``409 project_deleted``) when the id is in the trash,
        ``UnknownProject`` (``404``) when no record exists at all.
        """

        identifier = _project_id(project_id)
        if self._store.is_deleted(identifier):
            raise ProjectDeleted(identifier)
        return self._store.require(identifier)

    def _project_lock(self, project_id: str) -> threading.Lock:
        """The per-project lock this service uses for ``project_id``."""

        return _lock_for_project(self._store.root, project_id)

    def list_projects(self, *, include_archived: bool = True) -> Tuple[LifecycleProject, ...]:
        return tuple(
            self._store.list_projects(include_archived=include_archived)
        )

    def in_visible_root(self, project: object) -> bool:
        """True when the project's files really live in a configured visible root.

        Delegated to the store, which owns the record and the configured roots. It is
        the fact the «Проекты» list filters on: a project delivered into a folder the
        owner can open, versus one left in the hidden workspace by a test.
        """

        return self._store.in_visible_root(project)

    def summaries(self, *, include_archived: bool = True) -> Tuple[Dict[str, Any], ...]:
        """The lifecycle list: one bounded summary per project, each with its root fact.

        The extra ``in_visible_root`` field is additive -- every existing key of
        :meth:`LifecycleProject.summary` is unchanged -- and it is derived here rather
        than stored, so a record whose folder moved reports where it is *now*. The
        visible roots are resolved once for the whole listing: they are probed on disk,
        and one answer per request is enough.
        """

        roots = self._store.visible_delivery_roots()
        return tuple(
            {
                **project.summary(),
                "in_visible_root": self._store.in_visible_root(project, roots=roots),
            }
            for project in self.list_projects(include_archived=include_archived)
        )

    def project_for_session(self, session_id: object) -> str:
        """The project a confirmed discovery session produced, or ``""``."""

        return self._store.project_for_session(session_id)

    def history(self, project_id: object) -> Tuple[ProjectVersion, ...]:
        """Every recorded version, **newest first**."""

        project = self.open_project(project_id)
        return tuple(
            sorted(project.versions, key=lambda item: item.version, reverse=True)
        )

    def files(
        self, project_id: object, version: Optional[int] = None
    ) -> Tuple[ArtifactRef, ...]:
        """The artifact manifest of one version (the current one by default)."""

        project = self.open_project(project_id)
        if version is None:
            target = project.current()
            if target is None:
                return ()
            return target.files
        number = int(version)
        target = project.version(number)
        if target is None:
            raise UnknownProjectVersion(
                f"project '{project.project_id}' has no version {number}"
            )
        return target.files

    def artifact_location(self, project_id: object, artifact: ArtifactRef) -> str:
        """Where the artifact lives, as a **repository-relative** path."""

        project = self.open_project(project_id)
        return f"{project.project_path}/{artifact.path}"

    # ------------------------------------------------------------- creation
    def _derived_name(self, idea: object) -> str:
        """The short project name, numbered only when nothing can be derived from it."""

        from app.projects.folders import (
            derive_project_name,
            is_fallback_project_name,
        )

        name = derive_project_name(idea)
        if not is_fallback_project_name(name):
            return name
        # «Проект <N>»: the ordinal is the count of projects this store already holds, so
        # two ideas that carry no name of their own do not produce two «Проект 1».
        try:
            ordinal = 1 + len(self._store.list_projects())
        except Exception:  # noqa: BLE001 - a damaged listing must not block creation
            ordinal = 1
        return derive_project_name(idea, ordinal=ordinal)

    def project_root_decision(self) -> Optional[Any]:
        """Where the next project's folder really goes, and why that folder.

        ``None`` when the deployment configured no root (the legacy hidden workspace).
        The answer is the **writable** root: the configured one when it can hold a
        project, otherwise the first documented fallback. Only when every candidate
        fails does the resolution raise
        :class:`app.projects.root_selection.ProjectRootUnavailable` -- a typed conflict
        that names every attempted folder and the OS reason for each.
        """

        return self._store.root_decision()

    def create_from_discovery(
        self, session: object, *, plan_ref: str = ""
    ) -> LifecycleProject:
        """Create the durable project record for a confirmed discovery session.

        The record is born in ``WAITING_FOR_CONFIRMATION``: its specification is
        stored, and the confirmation step (``confirm``) is what advances it to
        ``PLANNED``. The discovery session id is linked to the project in the
        store's index, which is how the run path finds the project a confirmed run
        belongs to. Calling this twice for one session returns the same project
        rather than creating a second one.
        """

        session_id = str(_field(session, "session_id") or "").strip()
        if not session_id:
            raise ProjectError("a discovery session id is required to create a project")
        existing = self._store.project_for_session(session_id)
        if existing and self._store.exists(existing):
            return self._store.require(existing)

        specification = _specification_dict(session)
        goal = str(_field(session, "initial_goal") or "").strip() or str(
            specification.get("goal", "") or ""
        ).strip()

        project_id = new_project_id()
        now = utc_now()
        # The **name** is short and human; the full idea stays in the specification.
        # Measured on the owner's machine: naming the folder from the raw description
        # produced «Создай локальное приложение «Почтовый центр» — только Python (ст»,
        # truncated mid-word. The derivation rules live in ``app.projects.folders``.
        name = self._derived_name(goal or specification.get("goal", ""))
        # The workspace is allocated **before** the record that names it is written, and
        # which folder it is depends on the deployment's own project root: a visible
        # folder on the owner's desktop when one is configured, the legacy hidden
        # workspace otherwise. ``allocate_workspace`` creates it and refuses a folder it
        # cannot prove to sit inside that root.
        workspace, project_path, workspace_dir = self._store.allocate_workspace(
            project_id, name
        )
        project = LifecycleProject(
            project_id=project_id,
            name=name,
            status=ProjectStatus.WAITING_FOR_CONFIRMATION,
            workspace_id=project_id,
            project_path=project_path,
            workspace_dir=workspace_dir,
            created_at=now,
            updated_at=now,
            last_run_at="",
            current_version=0,
            versions=[],
            archived=False,
            specification=specification,
            plan_ref=str(plan_ref or ""),
            previous_status=None,
        )
        # The workspace must exist before anything can be copied into it, and the
        # record is only written once that is true -- a project that points at a
        # directory it does not have would be a lie the API would publish.
        if not workspace.is_dir():
            raise ProjectError("the project workspace could not be created")
        self._store.write(project)
        self._store.link_session(session_id, project_id)
        return project

    def confirm(self, project_id: object, *, plan_ref: str = "") -> LifecycleProject:
        """Advance a new project from ``WAITING_FOR_CONFIRMATION`` to ``PLANNED``.

        Confirming an already planned project is a no-op (it is the same fact), not
        a coercion: no other status is accepted here, and anything else raises the
        lifecycle's own typed refusal.
        """

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            project = self.require_active(identifier)
            if plan_ref:
                project = replace(project, plan_ref=str(plan_ref))
            if project.status is ProjectStatus.PLANNED:
                return project
            advanced = advance(project, LifecycleEvent(name="confirmed"))
            self._store.write(advanced)
            return advanced

    # --------------------------------------------------------------- attach
    def attach_run(
        self,
        project_id: object,
        run: object,
        *,
        source_root: Optional[object] = None,
        now: str = "",
    ) -> LifecycleProject:
        """Record a finished run against a project and advance its lifecycle.

        Order of operations, all derived from the run's own record:

        1. ``run_started`` -> ``RUNNING`` (the run really did start and finish, so
           both events are applied; neither status is skipped or invented);
        2. the run's written files are copied into the project workspace and
           described as :class:`ArtifactRef` records (size, SHA-256, create/update,
           verification relevance -- never contents);
        3. the version is filled (or appended) with the run id, the artifact
           manifest, the writer's verification and the run's acceptance;
        4. the lifecycle advances to ``COMPLETED`` **only** if the gate in
           :func:`app.projects.lifecycle.completion_blockers` is satisfied. Anything
           else lands in ``NEEDS_CHANGES`` or ``FAILED``.

        A failed run fills no *current* version: the previous version stays current,
        and the failure is still recorded as a numbered attempt so the history does
        not hide it.
        """

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            return self._attach_run_locked(
                identifier, run, source_root=source_root, now=now
            )

    def _attach_run_locked(
        self,
        project_id: str,
        run: object,
        *,
        source_root: Optional[object],
        now: str,
    ) -> LifecycleProject:
        """``attach_run`` under the per-project lock, on an active project."""

        project = self.require_active(project_id)
        facts = run_facts(run)
        timestamp = now or utc_now()

        started = LifecycleEvent(
            name="run_started",
            run_id=facts.run_id,
            run_state=facts.run_state,
            provider=facts.provider,
            model=facts.model,
        )
        project = advance(project, started, now=timestamp)

        if facts.failed:
            project = self._fill_version(
                project,
                facts,
                artifacts=(),
                verification=facts.verification,
                acceptance=facts.acceptance,
                becomes_current=False,
                now=timestamp,
            )
            project = advance(
                project,
                LifecycleEvent(
                    name="run_failed",
                    run_id=facts.run_id,
                    run_state=facts.run_state,
                    failed=True,
                    verification=facts.verification,
                    acceptance=facts.acceptance,
                    reason=facts.failure_reason,
                ),
                now=timestamp,
            )
            self._store.write(project)
            return project

        project = advance(
            project,
            LifecycleEvent(name="run_finished", run_id=facts.run_id, run_state=facts.run_state),
            now=timestamp,
        )

        artifacts, copy_problem = self._materialise(project, facts, source_root)
        verification = facts.verification
        if copy_problem:
            # A run whose files could not be recorded is not a verified run. The
            # reason travels with the refusal rather than being swallowed.
            verification = VERIFICATION_FAILED
        evidence = LifecycleEvent(
            name="completed",
            run_id=facts.run_id,
            run_state=facts.run_state,
            failed=facts.failed,
            verification=verification,
            acceptance=facts.acceptance,
            artifact_count=len(artifacts),
            wrote_files=facts.wrote_files,
            provider=facts.provider,
            model=facts.model,
            reason=copy_problem,
        )
        # A **change** request that wrote nothing does not advance the project. The
        # version it staged is still recorded (so the attempt is not hidden), but the
        # previous version stays current, because what the workspace contains is still
        # the previous version's files. Without this rule a text-only model answer
        # moved the project to a newer, empty version and the card read "the current
        # version has no files" while the earlier files were still on disk.
        staged = project.pending_version
        requested_change = staged is not None and staged.change_request is not None
        becomes_current = bool(artifacts) or not facts.failed
        if requested_change and not artifacts and not facts.failed:
            becomes_current = False
        project = self._fill_version(
            project,
            facts,
            artifacts=artifacts,
            verification=verification,
            acceptance=facts.acceptance,
            becomes_current=becomes_current,
            now=timestamp,
        )
        event_name = "completed" if not completion_blockers(evidence) else "changes_required"
        project = advance(
            project, replace(evidence, name=event_name), now=timestamp
        )
        self._store.write(project)
        return project

    # -------------------------------------------------------------- versions
    def new_version(self, project_id: object, change_request_text: object) -> NewVersionTask:
        """Stage the next version and return the task text the run path executes.

        The previous versions are untouched: a new slot is appended (or an already
        staged slot is re-targeted), and nothing is overwritten. The task text is
        what the caller submits; the **existing** run path executes it, and
        ``attach_run`` fills the slot when that run finishes. When the run fails,
        the previous version stays current and the project becomes ``FAILED``.
        """

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            return self._new_version_locked(identifier, change_request_text)

    def _new_version_locked(
        self, project_id: str, change_request_text: object
    ) -> NewVersionTask:
        """``new_version`` under the per-project lock, on an active project."""

        project = self.require_active(project_id)
        text, _redacted = self._screen(change_request_text)
        text = str(text or "").strip()
        if not text:
            raise InvalidChangeRequest(
                "a change request must be non-empty text describing what to change"
            )
        text = text[:MAX_CHANGE_REQUEST_CHARS]

        if project.status not in {
            ProjectStatus.PLANNED,
            ProjectStatus.WAITING_FOR_CONFIRMATION,
            ProjectStatus.COMPLETED,
            ProjectStatus.NEEDS_CHANGES,
            ProjectStatus.FAILED,
        }:
            raise IllegalTransition(
                "a new version can only be requested once a run has been recorded "
                f"(status: {project.status.value})",
                status=project.status,
                event="plan_created",
            )

        timestamp = utc_now()
        # A record created from a confirmed session is WAITING_FOR_CONFIRMATION until
        # its confirmation arrives. Requesting a version is that confirmation in
        # practice, so the documented move to PLANNED is applied explicitly here
        # rather than skipped over.
        if project.status is ProjectStatus.WAITING_FOR_CONFIRMATION:
            project = advance(project, LifecycleEvent(name="confirmed"), now=timestamp)

        pending = project.pending_version
        if pending is not None:
            # An already staged slot is re-targeted instead of a second empty slot
            # being appended; the state genuinely does not change, so no
            # transition is forced through the table.
            number = pending.version
            request = ChangeRequest(
                text=text, version=number, created_at=pending.created_at or timestamp
            )
            versions = [
                replace(item, change_request=request) if item.version == number else item
                for item in project.versions
            ]
            project = replace(project, versions=versions, updated_at=timestamp)
        else:
            # IDEMPOTENCY BY (PROJECT, CHANGE TEXT). The last version recorded for the
            # very same words decides what a repeated press does, and it never appends a
            # second entry for the same sentence:
            #
            # * it holds files and is the current version -> the change is already in the
            #   project, so nothing is staged and ``task_text`` stays empty (a caller must
            #   not dispatch a run);
            # * it holds no files -> the retry writes into that same version number, so a
            #   failed attempt cannot breed v4 and v5 out of one sentence.
            #
            # Only the newest version is compared: when the project moved on with other
            # words since, asking for the old change again is a genuinely new request.
            repeated = self._version_for_change_text(project, text)
            if repeated is not None:
                if repeated.files and repeated.version == project.current_version:
                    return NewVersionTask(
                        project=project,
                        version=repeated.version,
                        task_text="",
                        change_request=repeated.change_request
                        or ChangeRequest(text=text, version=repeated.version, created_at=timestamp),
                        already_applied=True,
                        previous_run_id=repeated.run_id,
                    )
                if not repeated.files:
                    request = ChangeRequest(
                        text=text, version=repeated.version, created_at=timestamp
                    )
                    retry_slot = replace(
                        repeated,
                        run_id="",
                        files=(),
                        verification=VERIFICATION_PENDING,
                        acceptance=ACCEPTANCE_PENDING,
                        provider="",
                        model="",
                        change_request=request,
                    )
                    versions = [
                        retry_slot if item.version == repeated.version else item
                        for item in project.versions
                    ]
                    project = replace(project, versions=versions, updated_at=timestamp)
                    self._store.write(project)
                    return NewVersionTask(
                        project=project,
                        version=repeated.version,
                        task_text=_version_task_text(project, repeated.version, text),
                        change_request=request,
                        retargeted=True,
                        previous_run_id=repeated.run_id,
                    )
            number = max(project.version_numbers, default=0) + 1
            request = ChangeRequest(text=text, version=number, created_at=timestamp)
            slot = ProjectVersion(
                version=number, created_at=timestamp, change_request=request
            )
            project = replace(
                project, versions=list(project.versions) + [slot], updated_at=timestamp
            )
            if project.status is not ProjectStatus.PLANNED:
                project = advance(project, LifecycleEvent(name="plan_created"), now=timestamp)

        self._store.write(project)
        return NewVersionTask(
            project=project,
            version=number,
            task_text=_version_task_text(project, number, text),
            change_request=request,
        )

    def _version_for_change_text(
        self, project: LifecycleProject, text: str
    ) -> Optional[ProjectVersion]:
        """The newest version recorded for the same words, or ``None``.

        A version with no recorded change request (a first creation) never matches, and
        only the highest version number is considered: once the project has moved on
        with other words, the old sentence names a new request rather than a repeat.
        """

        wanted = change_fingerprint(text)
        if not wanted:
            return None
        candidates = [
            item
            for item in project.versions
            if item.change_request is not None
            and change_fingerprint(item.change_request.text) == wanted
        ]
        if not candidates:
            return None
        newest = max(candidates, key=lambda item: item.version)
        if newest.version != max(project.version_numbers, default=0):
            return None
        return newest

    # -------------------------------------------------------------- archive
    def archive(self, project_id: object) -> LifecycleProject:
        """Freeze a project without deleting anything.

        Every field is kept, the workspace is kept, and ``previous_status`` records
        the status ``restore`` returns it to.
        """

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            project = self.require_active(identifier)
            archived = advance(project, LifecycleEvent(name="archived"))
            self._store.write(archived)
            return archived

    def restore(self, project_id: object) -> LifecycleProject:
        """Return an archived project to the status it had before archiving."""

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            project = self.require_active(identifier)
            restored = advance(project, LifecycleEvent(name="restore"))
            self._store.write(restored)
            return restored

    # ------------------------------------------------------------- visible folder
    def move_to_visible_folder(self, project_id: object) -> Dict[str, Any]:
        """Deliver an existing project's files into the configured visible folder.

        The owner's «Переместить в видимую папку». The **files** move; the record does
        not: the same versions, the same manifest, the same run links stay in
        ``.forge/projects/<id>/`` and only ``workspace_dir``/``project_path`` are
        rewritten to name the new folder.

        Byte identity is proved, not assumed: every file's SHA-256 is taken before the
        copy and compared after it, and a destination that already holds *different*
        bytes is refused instead of overwritten. The source is removed only once every
        file has arrived and matched, so a failure leaves the project exactly where it
        was. A project that is already visible is a no-op, reported as such.
        """

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            project = self.require_active(identifier)
            if self._store.visible_root() is None:
                raise ProjectError(
                    "the project root is not configured, so there is no visible folder "
                    "to move this project into"
                )
            current = self._store.workspace_path(identifier)
            try:
                target, published, recorded = self._store.allocate_workspace(
                    identifier, project.name
                )
            except UnsafeProjectFolder as exc:
                raise ProjectError(str(exc)) from exc

            if _same_folder(current, target):
                if str(project.workspace_dir or "").strip():
                    return {
                        "project": project,
                        "moved": [],
                        "already_visible": True,
                        "folder": project.project_path,
                    }
                # The record still says "legacy" although the folder resolved to the
                # visible one: nothing to move, but the record must be corrected so the
                # workspace keeps resolving after a restart.
                updated = replace(
                    project,
                    workspace_dir=str(target),
                    project_path=published,
                    updated_at=utc_now(),
                )
                self._store.write(updated)
                return {
                    "project": updated,
                    "moved": [],
                    "already_visible": True,
                    "folder": published,
                }

            if not current.is_dir():
                raise ProjectError(
                    "the project workspace is missing, so there is nothing to move"
                )

            moved, problem = self._copy_workspace(current, target)
            if problem:
                raise ProjectError(problem)

            updated = replace(
                project,
                workspace_dir=recorded,
                project_path=published,
                updated_at=utc_now(),
            )
            self._store.write(updated)
            self._remove_emptied_workspace(current)
            return {
                "project": updated,
                "moved": moved,
                "already_visible": False,
                "folder": published,
            }

    def _copy_workspace(
        self, source: Path, destination: Path
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Copy every file, prove its digest, then remove the source tree.

        Returns the manifest of what moved and a problem code (empty on success). Every
        digest is compared before a single source file is removed, so a mismatch leaves
        both trees intact and the project readable where it was.
        """

        entries: List[Tuple[Path, Path, str]] = []
        try:
            candidates = sorted(path for path in source.rglob("*") if path.is_file())
        except OSError:
            return [], "source_workspace_unreadable"
        for path in candidates:
            if path.is_symlink():
                return [], "source_workspace_contains_a_link"
            relative = path.relative_to(source)
            destination_path = destination / relative
            try:
                data = path.read_bytes()
            except OSError:
                return [], "source_file_unreadable"
            digest = hashlib.sha256(data).hexdigest()
            if destination_path.exists():
                try:
                    existing = destination_path.read_bytes()
                except OSError:
                    return [], "destination_file_unreadable"
                if hashlib.sha256(existing).hexdigest() != digest:
                    return [], "destination_file_differs"
            entries.append((path, destination_path, digest))

        moved: List[Dict[str, Any]] = []
        for source_path, destination_path, digest in entries:
            try:
                data = source_path.read_bytes()
            except OSError:
                return [], "source_file_unreadable"
            try:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(data)
                arrived = destination_path.read_bytes()
            except OSError:
                return [], "copy_failed"
            arrived_digest = hashlib.sha256(arrived).hexdigest()
            if arrived_digest != digest:
                # The copy did not reproduce the bytes: refuse, and leave the source.
                return [], "byte_identity_not_preserved"
            moved.append(
                {
                    "path": destination_path.relative_to(destination).as_posix(),
                    "size": len(arrived),
                    "sha256": arrived_digest,
                }
            )

        for source_path, _destination_path, _digest in entries:
            try:
                source_path.unlink()
            except OSError:  # pragma: no cover - a leftover copy is not a data loss
                pass
        return moved, ""

    def _remove_emptied_workspace(self, source: Path) -> None:
        """Remove the now-empty legacy workspace and its project directory.

        Bounded and best effort: it only removes directories that are empty, and it
        never walks above the project's own folder inside the store.
        """

        try:
            for path in sorted(source.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                if path.is_dir():
                    path.rmdir()
            source.rmdir()
        except OSError:
            return
        parent = source.parent
        try:
            if parent != self._store.root and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            return

    # ------------------------------------------------------------- deletion
    #
    # Deletion is deliberately not a lifecycle transition: a deleted project has no
    # status, it has a place in ``projects-deleted``. It is also not a hard delete --
    # the store moves the folder and the record, so nothing this layer did is lost and
    # a restore is a move back. The only refusal this layer itself makes is the one it
    # can prove from the record; "is a process running for it" is runtime state and is
    # checked by the HTTP composition, which owns the runner.
    #
    # The move is also the durable deletion marker: the trash entry is created before
    # anything moves and is removed only by a successful restore, so "this id is
    # deleted" is a fact on disk rather than a flag in memory. Every mutating method
    # above holds the per-project lock across read -> marker check -> write, and
    # ``ProjectStore.write`` re-checks the marker at the write itself; that combination
    # makes a resurrected record impossible in this process and refused across
    # processes.

    #: The statuses a *bulk* cleanup may remove. A project that is still being worked
    #: on (PLANNED, RUNNING, WAITING_FOR_CONFIRMATION, ...) is never swept away, and a
    #: project that produced files is never "empty" however it ended.
    EMPTY_CLEANUP_STATUSES = frozenset(
        {
            ProjectStatus.NEEDS_CHANGES,
            ProjectStatus.FAILED,
            ProjectStatus.ARCHIVED,
        }
    )

    def delete(self, project_id: object) -> Dict[str, Any]:
        """Move one project into ``projects-deleted`` and out of the listing.

        Under the per-project lock, so an in-flight verification either finishes
        first (and is then moved by this call) or is refused by the marker this call
        leaves behind. The record is read here rather than through
        :meth:`require_active`: deleting an already deleted id stays the ``404`` it
        has always been, and this operation is the one *making* the marker.
        """

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            self._store.require(identifier)
            return self._store.delete(identifier)

    def restore_deleted(self, project_id: object) -> Dict[str, Any]:
        """Move the newest trash entry for ``project_id`` back into the store.

        The store moves the folder and the record back and clears the marker, all
        inside this lock, so no concurrent mutation can observe the project as
        active before its record is back or as deleted after it is.
        """

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            return self._store.restore_deleted(identifier)

    def deleted_projects(
        self, *, limit: Optional[int] = None, offset: int = 0
    ) -> Tuple[Dict[str, Any], ...]:
        """The trash listing, newest first. Read-only.

        ``limit``/``offset`` window the same newest-first list; omitted, nothing
        changes for a caller that reads the whole trash.
        """

        return tuple(self._store.list_deleted(limit=limit, offset=offset))

    def empty_cleanup_candidates(self) -> Tuple[LifecycleProject, ...]:
        """The projects a bulk cleanup is allowed to remove.

        Two facts decide it, both read from the record: the current version holds no
        file, and the status is one of :attr:`EMPTY_CLEANUP_STATUSES`. A running
        process is *not* checked here (this layer has no runner); the caller that owns
        the runner adds that condition and reports the projects it skipped.
        """

        candidates: List[LifecycleProject] = []
        for project in self.list_projects(include_archived=True):
            if project.status not in self.EMPTY_CLEANUP_STATUSES:
                continue
            current = project.current()
            if current is not None and int(current.file_count) > 0:
                continue
            candidates.append(project)
        return tuple(candidates)

    # --------------------------------------------------------------- re-verify
    def set_preview_not_required(
        self, project_id: object, value: bool = True
    ) -> LifecycleProject:
        """Record, on the current version, that no local preview is required.

        This is an explicit statement by the caller, not an inference: a project with
        no servable entry point is *not* a project whose preview is unnecessary. The
        flag is stored on the version so the history keeps the fact per attempt, and
        only ``True`` here can satisfy the completion gate's preview requirement
        (``app.projects.stage.project_stage``).
        """

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            project = self.require_active(identifier)
            version = project.current() or project.latest_version
            if version is None:
                raise UnknownProjectVersion(
                    f"project '{project.project_id}' has no version to annotate"
                )
            versions = [
                replace(item, preview_not_required=bool(value))
                if item.version == version.version
                else item
                for item in project.versions
            ]
            updated = replace(project, versions=versions, updated_at=utc_now())
            self._store.write(updated)
            return updated

    def verify_again(self, project_id: object) -> LifecycleProject:
        """Re-judge the recorded artifacts, then advance the lifecycle.

        No provider is called and no run is started. The re-check is real: every
        recorded artifact must still be in the project workspace with the recorded
        size and SHA-256, and every recorded Python artifact must still compile.
        The acceptance judgement is the one already recorded for the version, which
        is a statement about the run and is not re-derived here.

        The read, the marker check and the write are one critical section: this is
        the operation that was measured re-creating a deleted project's record, and
        it now cannot read a live record and write it back after the id was moved to
        ``projects-deleted``.
        """

        identifier = _project_id(project_id)
        with self._project_lock(identifier):
            return self._verify_again_locked(identifier)

    def _verify_again_locked(self, project_id: str) -> LifecycleProject:
        """``verify_again`` under the per-project lock, on an active project."""

        project = self.require_active(project_id)
        if project.status not in {
            ProjectStatus.COMPLETED,
            ProjectStatus.NEEDS_CHANGES,
        }:
            raise IllegalTransition(
                "re-verification applies to a project that already has a recorded "
                f"version (status: {project.status.value})",
                status=project.status,
                event="verified",
            )
        version = project.current() or project.latest_version
        if version is None or version.is_pending:
            raise UnknownProjectVersion(
                f"project '{project.project_id}' has no recorded version to re-verify"
            )

        verification = self._recompute_verification(project, version)
        versions = [
            replace(item, verification=verification) if item.version == version.version else item
            for item in project.versions
        ]
        timestamp = utc_now()
        project = replace(project, versions=versions, updated_at=timestamp)

        evidence = LifecycleEvent(
            name="completed",
            run_id=version.run_id,
            verification=verification,
            acceptance=version.acceptance,
            artifact_count=version.file_count,
            wrote_files=bool(version.files),
            reverified=True,
        )
        blockers = completion_blockers(evidence)
        desired = ProjectStatus.COMPLETED if not blockers else ProjectStatus.NEEDS_CHANGES
        if desired is project.status:
            self._store.write(project)
            return project
        event_name = "completed" if desired is ProjectStatus.COMPLETED else "changes_required"
        project = advance(
            project, replace(evidence, name=event_name), now=timestamp
        )
        self._store.write(project)
        return project

    # -------------------------------------------------------------- internals
    def _screen(self, text: object) -> Tuple[str, bool]:
        """Replace secret-shaped text with the repository's existing marker."""

        value = str(text or "")
        reason = find_forbidden_value_content(value)
        if reason is None:
            return value, False
        return redaction_marker(reason), True

    def _fill_version(
        self,
        project: LifecycleProject,
        facts: RunFacts,
        *,
        artifacts: Tuple[ArtifactRef, ...],
        verification: str,
        acceptance: str,
        becomes_current: bool,
        now: str,
    ) -> LifecycleProject:
        """Fill the staged version (or append one) with this run's evidence."""

        pending = project.pending_version
        if pending is not None:
            number = pending.version
            created_at = pending.created_at or now
            change_request = pending.change_request
            # The owner's explicit "no preview required" belongs to the version
            # slot, so filling the slot keeps it rather than silently resetting it.
            preview_not_required = bool(pending.preview_not_required)
            others = [item for item in project.versions if item.version != number]
        else:
            number = max(project.version_numbers, default=0) + 1
            created_at = now
            change_request = None
            preview_not_required = False
            others = list(project.versions)

        filled = ProjectVersion(
            version=number,
            created_at=created_at,
            run_id=facts.run_id,
            files=tuple(artifacts),
            verification=verification,
            acceptance=acceptance,
            provider=facts.provider,
            model=facts.model,
            change_request=change_request,
            preview_not_required=preview_not_required,
        )
        versions = sorted(others + [filled], key=lambda item: item.version)
        return replace(
            project,
            versions=versions,
            current_version=(number if becomes_current else project.current_version),
            last_run_at=facts.recorded_at or now,
            updated_at=now,
        )

    def _materialise(
        self,
        project: LifecycleProject,
        facts: RunFacts,
        source_root: Optional[object],
    ) -> Tuple[Tuple[ArtifactRef, ...], str]:
        """Copy the run's written files into the project workspace.

        Returns the manifest and a problem code. Every entry is validated and read
        *before* a single byte is written, so a refusal leaves the project workspace
        exactly as it was -- the same fail-closed shape the writer itself uses.
        """

        if not facts.written_files:
            return (), ""
        if source_root is None:
            return (), "source_workspace_unknown"

        try:
            source = Path(source_root).resolve(strict=True)
        except (OSError, RuntimeError):
            return (), "source_workspace_unavailable"
        if not source.is_dir():
            return (), "source_workspace_unavailable"

        staged: List[Tuple[str, str, bytes]] = []
        for entry in facts.written_files:
            raw_path = str(entry.get("path", "") or "")
            if not is_safe_relative_path(raw_path):
                return (), "written_file_path_unsafe"
            try:
                candidate = source.joinpath(*raw_path.split("/")).resolve(strict=False)
            except (OSError, RuntimeError):
                return (), "written_file_path_unsafe"
            if source not in candidate.parents:
                return (), "written_file_outside_source_workspace"
            if candidate.is_symlink() or not candidate.is_file():
                return (), "written_file_missing"
            try:
                data = candidate.read_bytes()
            except OSError:
                return (), "written_file_unreadable"
            if len(data) > MAX_ARTIFACT_BYTES:
                return (), "written_file_too_large"
            action = str(entry.get("action", "") or "").strip()
            if action not in {ARTIFACT_CHANGE_CREATE, ARTIFACT_CHANGE_UPDATE}:
                action = ""
            staged.append((raw_path, action, data))

        artifacts: List[ArtifactRef] = []
        for raw_path, action, data in staged:
            try:
                destination = self._store.workspace_file_path(project.project_id, raw_path)
            except ValueError:
                return (), "written_file_path_unsafe"
            change = action or (
                ARTIFACT_CHANGE_UPDATE if destination.exists() else ARTIFACT_CHANGE_CREATE
            )
            try:
                self._store.write_workspace_file(project.project_id, raw_path, data)
            except (OSError, ValueError):
                return (), "artifact_copy_failed"
            artifacts.append(
                ArtifactRef(
                    path=raw_path,
                    kind=kind_for_path(raw_path),
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    change=change,
                    verification_relevance=verification_relevance_for_path(raw_path),
                )
            )
        return tuple(artifacts), ""

    def _recompute_verification(
        self, project: LifecycleProject, version: ProjectVersion
    ) -> str:
        """Re-check the recorded artifacts against what is on disk now."""

        if not version.files:
            return VERIFICATION_NOT_RUN
        has_python = False
        for artifact in version.files:
            try:
                path = self._store.workspace_file_path(project.project_id, artifact.path)
            except ValueError:
                return VERIFICATION_FAILED
            if path.is_symlink() or not path.is_file():
                return VERIFICATION_FAILED
            try:
                data = path.read_bytes()
            except OSError:
                return VERIFICATION_FAILED
            if len(data) != artifact.size:
                return VERIFICATION_FAILED
            if hashlib.sha256(data).hexdigest() != artifact.sha256:
                return VERIFICATION_FAILED
            if artifact.is_python:
                has_python = True
                try:
                    compile(data.decode("utf-8"), str(path), "exec")
                except (SyntaxError, ValueError, UnicodeDecodeError):
                    return VERIFICATION_FAILED
        return VERIFICATION_PASSED if has_python else VERIFICATION_NOT_APPLICABLE


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _project_id(project_id: object) -> str:
    if not is_safe_project_id(project_id):
        raise UnknownProject(str(project_id))
    return str(project_id)


def _same_folder(left: object, right: object) -> bool:
    """Whether two paths name the same folder, Windows casing included."""

    try:
        one = Path(left).resolve(strict=False)
        two = Path(right).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    try:
        return os.path.normcase(str(one)) == os.path.normcase(str(two))
    except (OSError, ValueError):  # pragma: no cover - defensive
        return False


def _specification_dict(session: object) -> Dict[str, Any]:
    """The session's specification as a plain JSON-safe mapping."""

    raw = _field(session, "specification")
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    to_dict = getattr(raw, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
        except (TypeError, ValueError):
            return {}
        if isinstance(converted, Mapping):
            return dict(converted)
    return {}


def _version_task_text(project: LifecycleProject, version: int, change_text: str) -> str:
    """The task text for one version, bounded and self-describing.

    It names the project, the version and the change. The confirmed
    specification is prepended by the existing task route when the caller names the
    discovery session, so this text deliberately does not repeat the whole
    specification.
    """

    lines = [
        f"PROJECT: {project.name}",
        f"VERSION: {version}",
        "CHANGE REQUEST:",
        change_text.strip(),
    ]
    goal = project.specification_goal()
    if goal:
        lines.append(f"PROJECT GOAL: {goal}")
    return "\n".join(lines)
