"""What a project card may say, derived from facts, in the owner's own words.

This module is the **one** place where a backend status word plus the project's
facts become a sentence the owner reads. It replaces nothing: the backend keeps
``ProjectStatus`` and ``RunOutcome`` exactly as they were, every payload still
carries the raw words, and the classification below is a *presentation* of those
facts -- so a disagreement between the record and the card can always be traced
back to the fact that produced it.

Why it exists
-------------

The defect it answers was a perception defect, not a data defect: a card could
read ``Files: 0``, ``Verification: NOT RUN`` and ``Status: NEEDS_CHANGES`` while
the run that filled it said ``COMPLETED``, and a reader took that as a finished
project. The facts were all there and each one was true; what was missing was one
place that put them in the order the owner reads them, and a rule that cannot say
"done" when there is nothing to be done with.

Three rules are structural rather than documented:

* **``Files: 0`` can never render ``ГОТОВО``.** The state word is derived from the
  facts, in this order: an archived record is ``АРХИВ``; a project that satisfies
  *every* completion fact is ``ГОТОВО``; a project with no file is ``НЕ СОЗДАН``;
  anything else is ``НУЖНЫ ДОРАБОТКИ``. ``ГОТОВО`` therefore requires
  ``files > 0``, ``verification == passed``, ``acceptance == passed``, a
  ``COMPLETED`` record **and** an existing workspace -- a missing fact is a
  ``False``, never a guess.
* **Nothing technical sits above the fold.** The run id, the classification, the
  event counters, the provider registry ids, the route list and the fingerprints
  are *all* returned in one ``technical`` block that the interface renders
  collapsed, and the tests assert that none of those keys appears in the headline
  or in the fact block.
* **An unknown fact is reported as unknown.** Six save facts are returned
  separately -- a single "saved" indicator is what hid an empty project behind a
  stored run record -- and a fact that cannot be established is ``неизвестно``,
  never ``да``.

Usage
-----

    presentation = build_project_presentation(
        project=project.to_dict(),
        versions=[item.summary() for item in project.versions],
        files=[artifact.to_dict() for artifact in current.files],
        workspace_exists=True,
        run_record=run_record,
    ).to_dict()

The function is pure: no clock, no I/O, no provider, no store. Everything it says
comes from the arguments, so the same facts always produce the same card and a
test can build a card without a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "ACTIONS",
    "ActionLine",
    "BACKEND_STATUS_LABELS",
    "BACKEND_STATUS_ORDER",
    "DECLARED_OUTCOME_LABELS",
    "FILES_WRITTEN",
    "NO_FILES_NOTICE",
    "OWNER_STATE_LABELS",
    "ProjectPresentation",
    "RUN_NO_CHANGE_NOTICE",
    "SAVE_FACT_KEYS",
    "SAVE_FACT_LABELS",
    "SaveFact",
    "TechnicalField",
    "VERIFICATION_NOT_RUN_LINE",
    "build_project_presentation",
    "declared_outcome_label",
    "owner_status_label",
]


# --------------------------------------------------------------------------- #
# the owner's vocabulary
#
# These are the product's words. They are Russian because that is the language the
# owner reads the card in; the backend words they replace stay in the payload and
# are printed verbatim in the technical block, so nothing is lost in translation.
# --------------------------------------------------------------------------- #

DRAFT = "ЧЕРНОВИК"
AWAITING_CLARIFICATION = "ОЖИДАЮТ УТОЧНЕНИЯ"
PLAN_READY = "ПЛАН ГОТОВ"
AWAITING_CONFIRMATION = "ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ"
IN_PROGRESS = "В РАБОТЕ"
FILES_WRITTEN = "ФАЙЛЫ СОЗДАНЫ"
VERIFYING = "ПРОВЕРКА"
READY = "ГОТОВО"
NEEDS_WORK = "НУЖНЫ ДОРАБОТКИ"
BROKEN = "ОШИБКА"
ARCHIVED_LABEL = "АРХИВ"
NOT_CREATED = "НЕ СОЗДАН"
UNKNOWN_LABEL = "НЕИЗВЕСТНО"

#: Every backend status, in the order the product walks through them. It is the
#: complete list, so a status the mapping does not know is visibly *not* in it.
BACKEND_STATUS_ORDER: Tuple[str, ...] = (
    "DISCOVERY",
    "NEEDS_CLARIFICATION",
    "READY_FOR_PLANNING",
    "PLANNED",
    "WAITING_FOR_CONFIRMATION",
    "RUNNING",
    "REVIEWING",
    "NEEDS_CHANGES",
    "COMPLETED",
    "FAILED",
    "ARCHIVED",
)

#: Backend status -> the owner's word for that stage.
#:
#: ``COMPLETED`` maps to ``ГОТОВО`` here and is *re-decided* in
#: :func:`build_project_presentation` against the project's facts, because the
#: status alone never justified the word. ``READY_FOR_PLANNING`` is a draft with a
#: gathered specification and no plan, which the vocabulary has no separate word
#: for; it is reported as ``ЧЕРНОВИК`` and the raw word stays in the facts block.
BACKEND_STATUS_LABELS: Mapping[str, str] = {
    "DISCOVERY": DRAFT,
    "NEEDS_CLARIFICATION": AWAITING_CLARIFICATION,
    "READY_FOR_PLANNING": DRAFT,
    "PLANNED": PLAN_READY,
    "WAITING_FOR_CONFIRMATION": AWAITING_CONFIRMATION,
    "RUNNING": IN_PROGRESS,
    "REVIEWING": IN_PROGRESS,
    "NEEDS_CHANGES": NEEDS_WORK,
    "COMPLETED": READY,
    "FAILED": BROKEN,
    "ARCHIVED": ARCHIVED_LABEL,
}

#: The three words the card's top line may use for a *working* project, plus the
#: one word an archived record gets. A test asserts any other word is unreachable.
OWNER_STATE_LABELS: Tuple[str, ...] = (NOT_CREATED, NEEDS_WORK, READY)

#: A run's declared outcome (``app.agent_runtime.outcome.RunOutcome``) -> the same
#: owner vocabulary. It is used only for ``declared_outcome_label`` in the
#: technical block, which reports what the *run* claimed next to what the
#: *project* holds, so a disagreement is visible instead of hidden.
DECLARED_OUTCOME_LABELS: Mapping[str, str] = {
    "CLARIFICATION_REQUIRED": AWAITING_CLARIFICATION,
    "SPECIFICATION_READY": DRAFT,
    "PLAN_READY": PLAN_READY,
    "EXECUTION_RUNNING": IN_PROGRESS,
    "VERIFICATION_REQUIRED": VERIFYING,
    "PROJECT_COMPLETED": READY,
    "NEEDS_CHANGES": NEEDS_WORK,
    "FAILED": BROKEN,
}


# --------------------------------------------------------------------------- #
# the fixed sentences
# --------------------------------------------------------------------------- #

#: Printed exactly when the project has no file. It states the folder and the
#: absence, and promises nothing else.
NO_FILES_NOTICE = "Папка создана, но проект ещё не содержит файлов."

#: Printed when a change run was recorded but wrote no file, so the version it
#: targeted was not advanced. The previous version stays current.
RUN_NO_CHANGE_NOTICE = "Изменения не записаны: новой версии нет"

#: The verification sentences. "Not run" is a state, never a pass word.
VERIFICATION_NOT_RUN_LINE = "Проверка ещё не выполнялась"
VERIFICATION_PENDING_LINE = "Проверка выполняется"
VERIFICATION_PASSED_LINE = "Проверка пройдена"
VERIFICATION_FAILED_LINE = "Проверка не пройдена"
VERIFICATION_NOT_APPLICABLE_LINE = "Проверять нечего: Python-файлов нет"

YES = "да"
NO = "нет"
UNKNOWN_VALUE = "неизвестно"

#: The six save facts, each with its own label and its own answer. They are
#: separate on purpose: one combined "saved" line hid an empty project behind a
#: stored run record.
SAVE_FACT_KEYS: Tuple[str, ...] = (
    "run_record",
    "specification",
    "plan",
    "files",
    "verification",
    "project_ready",
)
SAVE_FACT_LABELS: Mapping[str, str] = {
    "run_record": "Запуск сохранён",
    "specification": "Спецификация сохранена",
    "plan": "План сохранён",
    "files": "Файлы проекта сохранены",
    "verification": "Проверка выполнена",
    "project_ready": "Проект готов",
}


# --------------------------------------------------------------------------- #
# the actions a card may offer
# --------------------------------------------------------------------------- #

#: The closed action vocabulary, with the owner's label for each. Keeping it here
#: (and not in the interface) means a state cannot offer an action the product
#: does not have, and a test can assert the offered set for a state.
ACTIONS: Mapping[str, str] = {
    "continue_creation": "Продолжить создание проекта",
    "answer_questions": "Ответить на вопросы",
    "add_feature": "Добавить функцию",
    "new_version": "Создать новую версию",
    "view_files": "Просмотреть файлы",
    "reverify": "Повторить проверку",
    "launch_local": "Запустить локально",
    "open_app": "Открыть приложение",
    # The three real OS integrations and the delivery action. They are in this
    # vocabulary because they are actions a card offers, but they are rendered by the
    # card's own «Проект на диске» block (which shows the path beside them) rather than
    # by the generic action row, so `_actions` never emits them.
    "open_folder": "Открыть папку",
    "open_editor": "Открыть в VS Code",
    "open_terminal": "Открыть терминал",
    "move_to_visible": "Переместить в видимую папку",
}

#: The statuses in which the lifecycle accepts a new version at all
#: (``app.projects.service.ProjectService.new_version``). Anything else is a
#: backend refusal, and the card says so with the backend's own sentence.
NEW_VERSION_STATUSES = frozenset(
    {"PLANNED", "WAITING_FOR_CONFIRMATION", "COMPLETED", "NEEDS_CHANGES", "FAILED"}
)

#: The statuses in which re-verification applies
#: (``app.projects.service.ProjectService.verify_again``).
REVERIFY_STATUSES = frozenset({"COMPLETED", "NEEDS_CHANGES"})

#: How an artifact can be launched. An HTML page is opened; an entry file is run.
_MARKUP_SUFFIXES = (".html", ".htm")
_ENTRY_NAMES = (
    "main.py",
    "app.py",
    "run.py",
    "manage.py",
    "server.py",
    "index.js",
    "main.js",
    "app.js",
)


# --------------------------------------------------------------------------- #
# the presentation records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SaveFact:
    """One of the six save facts, answered separately and never by default."""

    key: str
    label: str
    value: str
    known: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "known": bool(self.known),
        }


@dataclass(frozen=True)
class ActionLine:
    """One action the card may offer: whether it is there, and why not if not."""

    action_id: str
    label: str
    enabled: bool = True
    visible: bool = True
    primary: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.action_id,
            "label": self.label,
            "enabled": bool(self.enabled),
            "visible": bool(self.visible),
            "primary": bool(self.primary),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TechnicalField:
    """One technical fact. It is only ever rendered inside the collapsed block."""

    key: str
    label: str
    value: str

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "value": self.value}


def _pair_list(rows: Sequence[Tuple[str, str]]) -> List[List[str]]:
    return [[label, value] for label, value in rows]


@dataclass(frozen=True)
class ProjectPresentation:
    """The card, as data. The interface renders this and adds nothing to it.

    ``headline`` is the four lines the owner reads first, in this order: the state,
    the file count, what happened last, and what to do next. ``facts`` is the block
    below it (workspace, repository-relative folder, files, last version, last
    run). ``technical`` is the collapsed block, and ``technical_collapsed`` is the
    interface's instruction to keep it closed until asked.
    """

    project_state: str
    stage_label: str
    backend_status: str
    files_count: int
    last_action: str
    next_step: str
    verification_line: str
    verification_not_run: bool
    notices: Tuple[str, ...]
    headline: Tuple[Tuple[str, str], ...]
    facts: Tuple[Tuple[str, str], ...]
    save_facts: Tuple[SaveFact, ...]
    actions: Tuple[ActionLine, ...]
    technical: Tuple[TechnicalField, ...]
    technical_collapsed: bool = True
    change_not_written: bool = False
    #: What can be launched, if anything: ``"open"`` for an HTML page, ``"run"`` for
    #: an entry file, ``""`` when the manifest holds neither. ``launch_path`` is the
    #: artifact's workspace-relative path. The card uses it to name the file and to
    #: refuse the action honestly when there is nothing to launch.
    launch_role: str = ""
    launch_path: str = ""

    @property
    def notice(self) -> str:
        """The one notice the card prints above the facts ("" when there is none)."""

        return self.notices[0] if self.notices else ""

    @property
    def is_ready(self) -> bool:
        """True only when this card says ``ГОТОВО``."""

        return self.project_state == READY

    def action(self, action_id: str) -> Optional[ActionLine]:
        for line in self.actions:
            if line.action_id == action_id:
                return line
        return None

    def offers(self, action_id: str) -> bool:
        """True when ``action_id`` is visible *and* enabled on this card."""

        line = self.action(action_id)
        return line is not None and line.visible and line.enabled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_state": self.project_state,
            "stage_label": self.stage_label,
            "backend_status": self.backend_status,
            "files_count": int(self.files_count),
            "last_action": self.last_action,
            "next_step": self.next_step,
            "verification_line": self.verification_line,
            "verification_not_run": bool(self.verification_not_run),
            "notices": list(self.notices),
            "notice": self.notice,
            "headline": _pair_list(self.headline),
            "facts": _pair_list(self.facts),
            "save_facts": [item.to_dict() for item in self.save_facts],
            "actions": [item.to_dict() for item in self.actions],
            "technical": {
                "collapsed": bool(self.technical_collapsed),
                "fields": [item.to_dict() for item in self.technical],
            },
            "change_not_written": bool(self.change_not_written),
            "launch_role": self.launch_role,
            "launch_path": self.launch_path,
        }


# --------------------------------------------------------------------------- #
# small, total helpers
# --------------------------------------------------------------------------- #


def _word(value: object) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().upper()


def _text(value: object, fallback: str = "—") -> str:
    if value is None:
        return fallback
    rendered = str(getattr(value, "value", value)).strip()
    return rendered or fallback


def _as_list(value: object) -> List[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def owner_status_label(backend_status: object) -> str:
    """The owner's word for one backend status (``НЕИЗВЕСТНО`` when unknown)."""

    return BACKEND_STATUS_LABELS.get(_word(backend_status), UNKNOWN_LABEL)


def declared_outcome_label(outcome: object) -> str:
    """The owner's word for one *declared* run outcome (``НЕИЗВЕСТНО`` unknown).

    It is used only to report what the run claimed, beside what the project holds.
    It never decides the card's own state word.
    """

    return DECLARED_OUTCOME_LABELS.get(_word(outcome), UNKNOWN_LABEL)


def _verification_line(word: str) -> str:
    if word == "passed":
        return VERIFICATION_PASSED_LINE
    if word == "failed":
        return VERIFICATION_FAILED_LINE
    if word == "not_applicable":
        return VERIFICATION_NOT_APPLICABLE_LINE
    if word == "pending":
        return VERIFICATION_PENDING_LINE
    if word in ("", "not_run"):
        return VERIFICATION_NOT_RUN_LINE
    return f"Проверка: {word.lower()}"


def _launch_target(files: Sequence[Mapping[str, Any]]) -> Tuple[str, str]:
    """The first launchable artifact: ``("open"|"run"|"", path)``.

    An HTML page is the only thing this product can *open*; an entry file is the
    only thing it can *run*. Anything else is not a launchable project, and the
    card says so instead of offering a button that would do nothing.
    """

    for artifact in files:
        path = str(artifact.get("path", "") or "")
        if path.lower().endswith(_MARKUP_SUFFIXES):
            return "open", path
    for artifact in files:
        path = str(artifact.get("path", "") or "")
        name = path.rsplit("/", 1)[-1].lower()
        if name in _ENTRY_NAMES:
            return "run", path
    return "", ""


def _save_fact(key: str, answer: Optional[bool]) -> SaveFact:
    """One save fact: ``да``, ``нет``, or ``неизвестно`` -- never ``да`` by default."""

    if answer is None:
        return SaveFact(key=key, label=SAVE_FACT_LABELS[key], value=UNKNOWN_VALUE, known=False)
    return SaveFact(
        key=key,
        label=SAVE_FACT_LABELS[key],
        value=YES if answer else NO,
        known=True,
    )


# --------------------------------------------------------------------------- #
# the one mapping function
# --------------------------------------------------------------------------- #


def build_project_presentation(
    *,
    project: Optional[Mapping[str, Any]] = None,
    versions: Sequence[Mapping[str, Any]] = (),
    files: Optional[Sequence[Mapping[str, Any]]] = None,
    run_record: Optional[Mapping[str, Any]] = None,
    workspace_exists: Optional[bool] = None,
    session_open: bool = False,
    questions_open: bool = False,
    change_request_text: str = "",
    run_record_saved: Optional[bool] = None,
    provider_registry_ids: Sequence[str] = (),
    api_routes: Sequence[str] = (),
    folder_absolute: str = "",
    entry_kind: str = "",
    entry_path: str = "",
) -> ProjectPresentation:
    """Turn a project's facts into the card the owner reads.

    Every argument is a fact the caller already holds. A fact the caller does not
    hold stays ``None``/empty, which is the conservative value: an unknown fact can
    never *raise* the card to ``ГОТОВО`` and is reported as ``неизвестно`` instead of
    being guessed.

    ``files`` is the current version's artifact manifest. ``None`` means "not read",
    in which case the recorded file count is used (and the count is then a record,
    not an observation, which the facts block still prints the same way because it
    is the same number the record published).
    """

    record = _mapping(project)
    status = _word(record.get("status"))
    archived = bool(record.get("archived", False)) or status == "ARCHIVED"
    version_lines = _as_list(versions)
    current_number = _int(record.get("current_version"))
    current = _current_version(version_lines, current_number)
    artifacts = _as_list(files) if files is not None else None
    if artifacts is not None:
        files_count = len(artifacts)
    else:
        files_count = _int(current.get("file_count")) if current else 0
        artifacts = []

    verification_word = str(current.get("verification", "") or "").strip().lower() if current else ""
    acceptance_word = str(current.get("acceptance", "") or "").strip().lower() if current else ""
    verification_not_run = verification_word in ("", "not_run")

    # ---------------------------------------------------------------- the state
    # ``ready`` is the completion fact set, all of it. Anything missing is False.
    workspace_present = workspace_exists is True
    ready = bool(
        not archived
        and status == "COMPLETED"
        and files_count > 0
        and verification_word == "passed"
        and acceptance_word == "passed"
        and workspace_present
    )
    if archived:
        project_state = ARCHIVED_LABEL
    elif ready:
        project_state = READY
    elif files_count == 0:
        project_state = NOT_CREATED
    else:
        project_state = NEEDS_WORK

    stage_label = _stage_label(
        status,
        ready=ready,
        files_count=files_count,
        verification_word=verification_word,
        archived=archived,
    )

    # ------------------------------------------------------ a change that wrote nothing
    # A version carrying a change request whose run recorded no file never became
    # current: the previous version is still what the workspace contains, and the
    # card says exactly that instead of showing a newer, empty version.
    latest = _newest_version(version_lines)
    change_not_written = bool(
        latest is not None
        and latest.get("change_request") is not None
        and _int(latest.get("file_count")) == 0
        and _int(latest.get("version")) != current_number
        and current_number > 0
    )

    # -------------------------------------------------------------- the sentences
    specification_present = bool(_mapping(record.get("specification")))
    plan_present = bool(str(record.get("plan_ref", "") or "").strip()) or status in {
        "PLANNED",
        "WAITING_FOR_CONFIRMATION",
        "RUNNING",
        "REVIEWING",
        "NEEDS_CHANGES",
        "COMPLETED",
        "FAILED",
    }
    run_seen = bool(current and str(current.get("run_id", "") or "").strip())
    run_state_word = _word(_mapping(run_record).get("state"))
    classification = _mapping(_mapping(run_record).get("classification"))
    # ``run_record["outcome"]`` is the run's own ``RunOutcome`` word; the
    # classification's ``outcome`` is a different vocabulary (``TERMINAL_SUCCESS``
    # &c) and must never be printed as the declared project outcome.
    declared_outcome = _word(_mapping(run_record).get("outcome"))

    last_action = _last_action(
        status=status,
        files_count=files_count,
        verification_word=verification_word,
        acceptance_word=acceptance_word,
        plan_present=plan_present,
        run_seen=run_seen,
        specification_present=specification_present,
        change_not_written=change_not_written,
        archived=archived,
    )
    next_step = _next_step(
        status=status,
        files_count=files_count,
        ready=ready,
        verification_not_run=verification_not_run,
        questions_open=questions_open or status == "NEEDS_CLARIFICATION",
        archived=archived,
        change_not_written=change_not_written,
    )

    notices: List[str] = []
    if files_count == 0:
        notices.append(NO_FILES_NOTICE)
    if change_not_written:
        notices.append(RUN_NO_CHANGE_NOTICE)

    # ------------------------------------------------------------------ the actions
    # The **recomputed** entry point, when the caller has one, decides which action
    # this project offers: a process entry is «run», a page is «open». The artifact
    # scan below is only the fallback for a caller that passes no entry point, so the
    # card can never say "open index.html" while Forge would really start app.py.
    launch_role, launch_path = _launch_target(artifacts)
    if entry_kind:
        launch_path = entry_path or launch_path
        # ``static_html`` is the only entry kind a browser can open without a
        # process; every other kind is a program the product starts.
        launch_role = "open" if entry_kind == "static_html" else "run"
    actions = _actions(
        status=status,
        files_count=files_count,
        archived=archived,
        session_open=session_open,
        verification_not_run=verification_not_run,
        launch_role=launch_role,
        launch_path=launch_path,
        change_request_text=change_request_text,
        ready=ready,
    )

    # ------------------------------------------------------------------- the save facts
    if run_record_saved is None:
        run_saved: Optional[bool] = True if run_record is not None else (False if not run_seen else None)
    else:
        run_saved = bool(run_record_saved)
    save_facts = (
        _save_fact("run_record", run_saved),
        _save_fact("specification", None if project is None else specification_present),
        _save_fact("plan", None if project is None else plan_present),
        _save_fact("files", None if project is None else files_count > 0),
        _save_fact(
            "verification",
            None
            if project is None
            else verification_word in ("passed", "failed", "not_applicable"),
        ),
        _save_fact("project_ready", None if project is None else ready),
    )

    # ------------------------------------------------------------------- the card
    headline: Tuple[Tuple[str, str], ...] = (
        ("Статус проекта", project_state),
        ("Файлы", str(files_count)),
        ("Последнее действие", last_action),
        ("Следующий шаг", next_step),
    )
    facts: Tuple[Tuple[str, str], ...] = (
        ("Workspace", _text(record.get("workspace_id") or record.get("project_id"))),
        ("Папка проекта", _text(record.get("project_path"))),
        ("Файлы", str(files_count)),
        ("Последняя версия", f"v{current_number}" if current_number > 0 else "—"),
        ("Последний запуск", _text(record.get("last_run_at"))),
    )

    technical = _technical(
        record=record,
        run_record=_mapping(run_record),
        current=current,
        artifacts=artifacts,
        status=status,
        verification_word=verification_word,
        acceptance_word=acceptance_word,
        run_state_word=run_state_word,
        declared_outcome=declared_outcome,
        provider_registry_ids=provider_registry_ids,
        api_routes=api_routes,
        folder_absolute=folder_absolute,
    )

    return ProjectPresentation(
        project_state=project_state,
        stage_label=stage_label,
        backend_status=status,
        files_count=files_count,
        last_action=last_action,
        next_step=next_step,
        verification_line=_verification_line(verification_word),
        verification_not_run=verification_not_run,
        notices=tuple(notices),
        headline=headline,
        facts=facts,
        save_facts=save_facts,
        actions=actions,
        technical=technical,
        technical_collapsed=True,
        change_not_written=change_not_written,
        launch_role=launch_role,
        launch_path=launch_path,
    )


# --------------------------------------------------------------------------- #
# the pieces the mapping function composes
# --------------------------------------------------------------------------- #


def _current_version(
    versions: Sequence[Mapping[str, Any]], current_number: int
) -> Optional[Mapping[str, Any]]:
    """The version that describes what is in the workspace, or ``None``.

    ``current_version == 0`` means the lifecycle never advanced to a version: a
    staged-but-unfilled slot is not what the workspace contains, so it is not
    returned here and its verdicts are not reported as the project's.
    """

    if current_number <= 0:
        return None
    for item in versions:
        if _int(item.get("version")) == current_number:
            return item
    return None


def _newest_version(
    versions: Sequence[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    if not versions:
        return None
    return max(versions, key=lambda item: _int(item.get("version")))


def _stage_label(
    status: str,
    *,
    ready: bool,
    files_count: int,
    verification_word: str,
    archived: bool,
) -> str:
    """The owner's word for the *stage*, after the facts have been applied.

    ``ГОТОВО`` is reachable here only through ``ready``, which already required
    every completion fact; a ``COMPLETED`` record whose facts do not hold is
    reported as what it actually is.
    """

    if archived:
        return ARCHIVED_LABEL
    if status == "COMPLETED":
        if ready:
            return READY
        return NEEDS_WORK if files_count > 0 else NOT_CREATED
    if status in ("RUNNING", "REVIEWING"):
        if verification_word == "pending":
            return VERIFYING
        if files_count > 0 and verification_word in ("", "not_run"):
            return FILES_WRITTEN
        return IN_PROGRESS
    return BACKEND_STATUS_LABELS.get(status, UNKNOWN_LABEL)


def _last_action(
    *,
    status: str,
    files_count: int,
    verification_word: str,
    acceptance_word: str,
    plan_present: bool,
    run_seen: bool,
    specification_present: bool,
    change_not_written: bool,
    archived: bool,
) -> str:
    if archived:
        return "проект отправлен в архив"
    if change_not_written:
        return "получен ответ модели, но изменения не записаны"
    if files_count > 0 and verification_word in ("passed", "failed", "not_applicable"):
        return "проверка выполнена"
    if files_count > 0:
        return "файлы записаны"
    if status == "FAILED":
        return "запуск завершился ошибкой"
    if run_seen:
        return "получен ответ модели"
    if plan_present:
        return "создан план"
    if specification_present:
        return "собрана спецификация"
    return "ничего ещё не сделано"


def _next_step(
    *,
    status: str,
    files_count: int,
    ready: bool,
    verification_not_run: bool,
    questions_open: bool,
    archived: bool,
    change_not_written: bool,
) -> str:
    if archived:
        return "восстановить проект из архива"
    if questions_open:
        return "ответить на вопросы"
    # A record that waits for its confirmation has one thing to do; a PLANNED record
    # does not -- its confirmation arrives by requesting the version that creates it,
    # so the actionable step there is the creation itself (handled by the file rule).
    if status in ("WAITING_FOR_CONFIRMATION", "READY_FOR_PLANNING"):
        return "подтвердить план"
    if files_count == 0:
        return "запустить создание проекта"
    if verification_not_run:
        return "запустить проверку"
    if change_not_written:
        return "повторить доработку: запрос не изменил файлы"
    if ready:
        return "проект готов: просмотреть файлы или запустить локально"
    if status == "FAILED":
        return "исправить ошибку и запустить снова"
    return "добавить доработку"


def _reason_from_backend(status: str, message: str) -> str:
    """The backend's own refusal sentence, kept verbatim beside the owner's reason."""

    return (
        f"{message} (текущий статус: {status or '—'}); "
        "сервер ответит 409 illegal_project_transition"
    )


def _actions(
    *,
    status: str,
    files_count: int,
    archived: bool,
    session_open: bool,
    verification_not_run: bool,
    launch_role: str,
    launch_path: str,
    change_request_text: str,
    ready: bool,
) -> Tuple[ActionLine, ...]:
    """Which actions the card offers, and why the others are not offered.

    The file rule is structural: with no file the only creation action is
    "continue creating the project" (plus answering open questions), because every
    other action assumes something exists to act on.
    """

    no_files = "Файлы проекта ещё не созданы: действовать не с чем."
    archived_reason = "Проект в архиве: сначала восстановите его (409 illegal_project_transition)."
    lines: List[ActionLine] = []

    if files_count == 0:
        lines.append(
            ActionLine(
                action_id="continue_creation",
                label=ACTIONS["continue_creation"],
                enabled=not archived,
                visible=True,
                primary=True,
                reason=archived_reason if archived else "",
            )
        )
        if session_open:
            lines.append(
                ActionLine(
                    action_id="answer_questions",
                    label=ACTIONS["answer_questions"],
                    enabled=not archived,
                    visible=True,
                    reason=archived_reason if archived else "",
                )
            )
        for hidden, why in (
            ("add_feature", no_files),
            ("new_version", no_files),
            ("view_files", "Файлов ещё нет: просматривать нечего."),
            ("reverify", VERIFICATION_NOT_RUN_LINE + ": проверять нечего."),
            ("launch_local", no_files),
            ("open_app", no_files),
        ):
            lines.append(
                ActionLine(
                    action_id=hidden,
                    label=ACTIONS[hidden],
                    enabled=False,
                    visible=False,
                    reason=why,
                )
            )
        return tuple(lines)

    # ---- files exist
    if archived:
        for action_id in ("add_feature", "new_version", "view_files", "reverify", "launch_local", "open_app"):
            lines.append(
                ActionLine(
                    action_id=action_id,
                    label=ACTIONS[action_id],
                    enabled=False,
                    visible=True,
                    reason=archived_reason,
                )
            )
        return tuple(lines)

    lines.append(
        ActionLine(action_id="add_feature", label=ACTIONS["add_feature"], enabled=True, primary=True)
    )

    status_allows_version = status in NEW_VERSION_STATUSES
    has_change_text = bool(str(change_request_text or "").strip())
    new_version_reason = ""
    if not status_allows_version:
        new_version_reason = _reason_from_backend(
            status,
            "a new version can only be requested once a run has been recorded",
        )
    elif not has_change_text:
        # The lifecycle refuses an empty request with 400 invalid_change_request; the
        # card states the same rule before the click, and the button becomes usable
        # as soon as the owner has described the change.
        new_version_reason = (
            "Сначала опишите изменение: пустой запрос сервер отклонит "
            "(400 invalid_change_request)."
        )
    lines.append(
        ActionLine(
            action_id="new_version",
            label=ACTIONS["new_version"],
            enabled=status_allows_version and has_change_text,
            visible=True,
            reason=new_version_reason,
        )
    )
    lines.append(ActionLine(action_id="view_files", label=ACTIONS["view_files"], enabled=True))

    reverify_enabled = status in REVERIFY_STATUSES and not verification_not_run
    reverify_reason = ""
    if status not in REVERIFY_STATUSES:
        reverify_reason = _reason_from_backend(
            status,
            "re-verification applies to a project that already has a recorded version",
        )
    elif verification_not_run:
        reverify_reason = VERIFICATION_NOT_RUN_LINE + ": проверять ещё нечего."
    lines.append(
        ActionLine(
            action_id="reverify",
            label=ACTIONS["reverify"],
            enabled=reverify_enabled,
            visible=True,
            reason=reverify_reason,
        )
    )

    if launch_role == "open":
        # A page is the one thing this product can launch with no process: the card's
        # two buttons both go through the existing read-only preview route, so both
        # are enabled. Reporting the page as "nothing to start" was the measured
        # defect -- the preview route served it with HTTP 200 while the card refused.
        lines.append(
            ActionLine(
                action_id="open_app",
                label=ACTIONS["open_app"],
                enabled=True,
                reason=f"Открывается {launch_path}",
            )
        )
        lines.append(
            ActionLine(
                action_id="launch_local",
                label=ACTIONS["launch_local"],
                enabled=True,
                reason=(
                    f"{launch_path}: статическая страница, сервер не требуется "
                    "(открывается локальным предпросмотром)"
                ),
            )
        )
    elif launch_role == "run":
        lines.append(
            ActionLine(
                action_id="launch_local",
                label=ACTIONS["launch_local"],
                enabled=True,
                reason=f"Запускается точка входа {launch_path}",
            )
        )
        lines.append(
            ActionLine(
                action_id="open_app",
                label=ACTIONS["open_app"],
                enabled=False,
                visible=False,
                reason="Открывать нечего: в проекте нет HTML-страницы.",
            )
        )
    else:
        no_launch = (
            "Нет файла запуска: в манифесте нет ни HTML-страницы, ни точки входа "
            "(main.py, app.py, index.js)."
        )
        for action_id in ("launch_local", "open_app"):
            lines.append(
                ActionLine(
                    action_id=action_id,
                    label=ACTIONS[action_id],
                    enabled=False,
                    visible=False,
                    reason=no_launch,
                )
            )
    return tuple(lines)


def _technical(
    *,
    record: Mapping[str, Any],
    run_record: Mapping[str, Any],
    current: Optional[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    status: str,
    verification_word: str,
    acceptance_word: str,
    run_state_word: str,
    declared_outcome: str,
    provider_registry_ids: Sequence[str],
    api_routes: Sequence[str],
    folder_absolute: str = "",
) -> Tuple[TechnicalField, ...]:
    """Every technical fact, in one block that is collapsed by default."""

    classification = _mapping(run_record.get("classification"))
    fingerprints = [
        f"{artifact.get('path', '')}={str(artifact.get('sha256', '') or '')[:16]}"
        for artifact in artifacts
        if str(artifact.get("sha256", "") or "")
    ]
    record_fingerprint = str(run_record.get("fingerprint", "") or "").strip()
    if record_fingerprint:
        fingerprints.append(f"run={record_fingerprint[:16]}")
    event_count = run_record.get("event_count")
    sequence_number = run_record.get("last_sequence_number")
    return (
        TechnicalField("run_id", "run_id", _text(current.get("run_id") if current else "")),
        TechnicalField(
            "classification_outcome",
            "classification outcome",
            _text(classification.get("outcome")),
        ),
        TechnicalField(
            "declared_outcome",
            "declared outcome",
            _text(declared_outcome),
        ),
        TechnicalField(
            "declared_outcome_label",
            "declared_outcome_label",
            declared_outcome_label(declared_outcome),
        ),
        TechnicalField("state", "state", _text(run_state_word or _mapping(run_record).get("state"))),
        TechnicalField(
            "event_count",
            "event count",
            _text(event_count, "неизвестно"),
        ),
        TechnicalField(
            "sequence_number",
            "sequence number",
            _text(sequence_number, "неизвестно"),
        ),
        TechnicalField(
            "provider_registry_ids",
            "provider registry ids",
            ", ".join(str(item) for item in provider_registry_ids) or "—",
        ),
        TechnicalField(
            "api_routes",
            "API route list",
            ", ".join(str(item) for item in api_routes) or "—",
        ),
        TechnicalField(
            "fingerprints",
            "fingerprints",
            ", ".join(fingerprints) or "—",
        ),
        TechnicalField("backend_status", "backend status", _text(status)),
        TechnicalField(
            "verification_word",
            "verification",
            _text(verification_word, "not_run"),
        ),
        TechnicalField(
            "acceptance_word",
            "acceptance",
            _text(acceptance_word, "not_evaluated"),
        ),
        TechnicalField(
            "project_version",
            "project version",
            _text(_int(record.get("current_version")), "0"),
        ),
        TechnicalField("project_id", "project id", _text(record.get("project_id"))),
        TechnicalField("workspace_id", "workspace id", _text(record.get("workspace_id"))),
        TechnicalField(
            "project_path",
            "published folder (relative)",
            _text(record.get("project_path")),
        ),
        # The one absolute path the card may show, and it is in the collapsed block on
        # purpose: the owner needs it to paste into Explorer, while the block above the
        # fold keeps publishing the record's own relative folder.
        TechnicalField(
            "folder_absolute",
            "folder (absolute)",
            _text(folder_absolute, "неизвестно"),
        ),
        TechnicalField("plan_ref", "plan_ref", _text(record.get("plan_ref"))),
        TechnicalField("created_at", "created_at", _text(record.get("created_at"))),
        TechnicalField("updated_at", "updated_at", _text(record.get("updated_at"))),
        TechnicalField(
            "previous_status",
            "previous status",
            _text(record.get("previous_status")),
        ),
        TechnicalField(
            "classification_reason",
            "classification reason",
            _text(classification.get("reason")),
        ),
        TechnicalField(
            "classification_detail",
            "classification detail",
            _text(classification.get("detail")),
        ),
        TechnicalField(
            "archived",
            "archived",
            YES if bool(record.get("archived", False)) else NO,
        ),
    )
