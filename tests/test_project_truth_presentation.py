"""What a project card may say, proved on pure functions and fake data.

Every test here builds its facts by hand -- a project record, its versions, its
artifact manifest -- and asserts what :func:`app.projects.presentation.build_project_presentation`
makes of them. No provider, no socket, no run, no HTTP. The two service-level tests
at the end use the real lifecycle with a real workspace, but still with a fixed
answer and no network.

The point of the module is the owner's manual check: a card read ``Files: 0``,
``Verification: NOT RUN`` and ``Status: NEEDS_CHANGES`` while the run said
``COMPLETED``, and a reader took it for a finished project. The assertions below are
the rules that stop that reading, and each one fails if its rule is removed.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import unittest
from types import SimpleNamespace

from app.agent_runtime.code_writer import apply_changes
from app.projects.models import ProjectStatus
from app.projects.presentation import (
    ACTIONS,
    NO_FILES_NOTICE,
    RUN_NO_CHANGE_NOTICE,
    SAVE_FACT_KEYS,
    SAVE_FACT_LABELS,
    VERIFICATION_NOT_RUN_LINE,
    build_project_presentation,
    declared_outcome_label,
    owner_status_label,
)
from app.projects.service import ProjectService
from app.projects.store import ProjectStore
from app.tools.workspace import Workspace

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRATCH = REPO_ROOT / ".forge" / "test-project-truth"

SHA = "a" * 64

#: The answer the guarded writer applies. It is a fenced block with a path header,
#: so the writer really writes it and really compiles it.
WRITE_ANSWER = """Один модуль.

```python path=truth_tool.py
\"\"\"A deterministic module for the truth test.\"\"\"


def double(value):
    \"\"\"Return twice the value.\"\"\"
    return value * 2
```
"""

#: The technical facts the card must keep out of the headline.
TECHNICAL_KEYS = (
    "run_id",
    "classification_outcome",
    "declared_outcome_label",
    "state",
    "event_count",
    "sequence_number",
    "provider_registry_ids",
    "api_routes",
    "fingerprints",
)


def _project(**overrides: object) -> dict:
    """A project record shaped exactly like ``LifecycleProject.to_dict()``."""

    record = {
        "project_id": "proj-000000000001",
        "name": "Учёт расходов",
        "status": "PLANNED",
        "workspace_id": "proj-000000000001",
        "project_path": ".forge/projects/proj-000000000001/workspace",
        "created_at": "2026-01-01T10:00:00+00:00",
        "updated_at": "2026-01-01T10:05:00+00:00",
        "last_run_at": "",
        "current_version": 0,
        "versions": [],
        "archived": False,
        "specification": {"goal": "Учёт домашних расходов"},
        "plan_ref": "plan-1",
        "previous_status": None,
    }
    record.update(overrides)
    return record


def _version(**overrides: object) -> dict:
    record = {
        "version": 1,
        "created_at": "2026-01-01T10:05:00+00:00",
        "run_id": "run-00000001",
        "verification": "not_run",
        "acceptance": "passed",
        "provider": "mock",
        "model": "deterministic-offline",
        "file_count": 0,
        "pending": False,
        "change_request": None,
    }
    record.update(overrides)
    return record


def _artifact(path: str = "index.html") -> dict:
    return {
        "path": path,
        "kind": "markup" if path.endswith(".html") else "python",
        "size": 64,
        "sha256": SHA,
        "change": "create",
        "verification_relevance": "not_compiled"
        if path.endswith(".html")
        else "compiled",
        "location": f".forge/projects/proj-000000000001/workspace/{path}",
    }


def _completed_with_files(**overrides: object):
    """A project that satisfies every completion fact, unless overridden."""

    project = _project(status="COMPLETED", current_version=1)
    project.update(overrides.pop("project", {}))
    version = _version(verification="passed", acceptance="passed", file_count=1)
    version.update(overrides.pop("version", {}))
    files = overrides.pop("files", [_artifact()])
    return build_project_presentation(
        project=project,
        versions=[version],
        files=files,
        workspace_exists=overrides.pop("workspace_exists", True),
        **overrides,
    )


class StatusVocabularyTests(unittest.TestCase):
    """The mapping from a backend status to the owner's words, and its hard rule."""

    def test_a_project_with_no_file_never_says_ready(self) -> None:
        # The exact card the owner's check produced: the record says COMPLETED, the
        # card says there is nothing in it. The state word must not be "ГОТОВО".
        card = build_project_presentation(
            project=_project(status="COMPLETED", current_version=1),
            versions=[_version(verification="passed", acceptance="passed")],
            files=[],
            workspace_exists=True,
        )
        self.assertNotEqual(card.project_state, "ГОТОВО")
        self.assertNotEqual(card.stage_label, "ГОТОВО")
        self.assertEqual(card.project_state, "НЕ СОЗДАН")
        self.assertFalse(card.is_ready)
        self.assertEqual(card.files_count, 0)
        # And it says why, in the exact words the product uses.
        self.assertIn(NO_FILES_NOTICE, card.notices)
        self.assertIn(NO_FILES_NOTICE, card.to_dict()["notices"])

    def test_the_three_owner_states_are_the_only_working_ones(self) -> None:
        seen = set()
        for status in ("DISCOVERY", "NEEDS_CLARIFICATION", "PLANNED", "RUNNING", "NEEDS_CHANGES", "COMPLETED", "FAILED"):
            seen.add(
                build_project_presentation(
                    project=_project(status=status), versions=[], files=[]
                ).project_state
            )
        self.assertEqual(seen, {"НЕ СОЗДАН"})

        with_files = _completed_with_files()
        self.assertEqual(with_files.project_state, "ГОТОВО")
        archived = _completed_with_files(project={"archived": True, "status": "ARCHIVED"})
        self.assertEqual(archived.project_state, "АРХИВ")

    def test_the_backend_status_keeps_its_own_word(self) -> None:
        # The mapping is additive: the raw word is still published, in the block that
        # is collapsed, so nothing is hidden by the translation.
        card = build_project_presentation(
            project=_project(status="NEEDS_CLARIFICATION"), versions=[], files=[]
        )
        self.assertEqual(card.backend_status, "NEEDS_CLARIFICATION")
        self.assertEqual(card.stage_label, "ОЖИДАЮТ УТОЧНЕНИЯ")
        self.assertEqual(owner_status_label("PLANNED"), "ПЛАН ГОТОВ")
        self.assertEqual(owner_status_label("WAITING_FOR_CONFIRMATION"), "ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ")
        self.assertEqual(owner_status_label("REVIEWING"), "В РАБОТЕ")
        self.assertEqual(owner_status_label("FAILED"), "ОШИБКА")
        self.assertEqual(owner_status_label("ARCHIVED"), "АРХИВ")
        self.assertEqual(owner_status_label("NOT_A_STATUS"), "НЕИЗВЕСТНО")

    def test_files_that_exist_but_are_unverified_say_so(self) -> None:
        # A review in progress with files on disk has its own word, and the check is
        # not misreported as passed.
        card = build_project_presentation(
            project=_project(status="REVIEWING", current_version=1),
            versions=[_version(verification="not_run", file_count=1)],
            files=[_artifact("truth_tool.py")],
            workspace_exists=True,
        )
        self.assertEqual(card.stage_label, "ФАЙЛЫ СОЗДАНЫ")
        self.assertEqual(card.verification_line, VERIFICATION_NOT_RUN_LINE)
        self.assertTrue(card.verification_not_run)
        pending = build_project_presentation(
            project=_project(status="REVIEWING", current_version=1),
            versions=[_version(verification="pending", file_count=1)],
            files=[_artifact("truth_tool.py")],
            workspace_exists=True,
        )
        self.assertEqual(pending.stage_label, "ПРОВЕРКА")

    def test_an_unrun_verification_shows_the_explicit_phrase(self) -> None:
        card = build_project_presentation(
            project=_project(status="NEEDS_CHANGES", current_version=1),
            versions=[_version(verification="not_run", acceptance="passed")],
            files=[],
            workspace_exists=True,
        )
        self.assertTrue(card.verification_not_run)
        self.assertEqual(card.verification_line, VERIFICATION_NOT_RUN_LINE)
        # No pass word anywhere in the verification statements.
        for _label, value in card.facts:
            self.assertNotIn("пройдена", value)
        sentence = dict(card.headline).get("Следующий шаг", "")
        self.assertNotIn("пройдена", sentence)
        # The save facts report the check as not performed, never as unknown-by-default.
        fact = {item.key: item for item in card.save_facts}["verification"]
        self.assertEqual(fact.value, "нет")
        self.assertTrue(fact.known)

    def test_acceptance_alone_does_not_imply_creation(self) -> None:
        # Acceptance is the run's own verdict; it says nothing about a file existing.
        card = build_project_presentation(
            project=_project(status="COMPLETED", current_version=1),
            versions=[_version(verification="passed", acceptance="passed")],
            files=[],
            workspace_exists=True,
        )
        self.assertNotEqual(card.project_state, "ГОТОВО")
        facts = {item.key: item for item in card.save_facts}
        self.assertEqual(facts["files"].value, "нет")
        self.assertEqual(facts["project_ready"].value, "нет")
        self.assertEqual(facts["verification"].value, "да")

    def test_completion_requires_verification(self) -> None:
        card = _completed_with_files(version={"verification": "not_run"})
        self.assertNotEqual(card.project_state, "ГОТОВО")
        self.assertEqual(card.project_state, "НУЖНЫ ДОРАБОТКИ")
        facts = {item.key: item for item in card.save_facts}
        self.assertEqual(facts["verification"].value, "нет")
        self.assertEqual(facts["project_ready"].value, "нет")
        self.assertIn("проверк", dict(card.headline)["Следующий шаг"].lower())

    def test_completion_requires_the_workspace(self) -> None:
        card = _completed_with_files(workspace_exists=False)
        self.assertNotEqual(card.project_state, "ГОТОВО")
        # An *unknown* workspace is not an existing one: the conservative value decides.
        unknown = _completed_with_files(workspace_exists=None)
        self.assertNotEqual(unknown.project_state, "ГОТОВО")

    def test_a_completed_card_needs_every_fact(self) -> None:
        card = _completed_with_files()
        self.assertEqual(card.project_state, "ГОТОВО")
        self.assertEqual(card.stage_label, "ГОТОВО")
        self.assertTrue(card.is_ready)
        facts = {item.key: item for item in card.save_facts}
        self.assertEqual(facts["files"].value, "да")
        self.assertEqual(facts["verification"].value, "да")
        self.assertEqual(facts["project_ready"].value, "да")
        self.assertEqual(card.notices, ())

    def test_the_declared_outcome_is_labelled_in_the_same_words(self) -> None:
        self.assertEqual(declared_outcome_label("PROJECT_COMPLETED"), "ГОТОВО")
        self.assertEqual(declared_outcome_label("NEEDS_CHANGES"), "НУЖНЫ ДОРАБОТКИ")
        self.assertEqual(declared_outcome_label("PROJECT_COMPLETED"), "ГОТОВО")
        self.assertEqual(declared_outcome_label(""), "НЕИЗВЕСТНО")
        # A classification word is not an outcome word.
        self.assertEqual(declared_outcome_label("TERMINAL_SUCCESS"), "НЕИЗВЕСТНО")


class CardLayoutTests(unittest.TestCase):
    """The order of the card, and what must stay out of the visible part."""

    def test_the_headline_is_the_four_owner_lines_in_order(self) -> None:
        card = _completed_with_files()
        self.assertEqual(
            [label for label, _value in card.headline],
            ["Статус проекта", "Файлы", "Последнее действие", "Следующий шаг"],
        )
        self.assertEqual(dict(card.headline)["Статус проекта"], "ГОТОВО")
        self.assertEqual(dict(card.headline)["Файлы"], "1")
        self.assertEqual(dict(card.headline)["Последнее действие"], "проверка выполнена")
        self.assertIn("готов", dict(card.headline)["Следующий шаг"].lower())

    def test_the_fact_block_names_the_repository_relative_folder(self) -> None:
        card = build_project_presentation(
            project=_project(), versions=[], files=[], workspace_exists=True
        )
        facts = dict(card.facts)
        self.assertEqual(
            facts["Папка проекта"], ".forge/projects/proj-000000000001/workspace"
        )
        # Repository-relative: no drive letter, no backslash, no absolute prefix.
        for _label, value in card.facts:
            self.assertNotIn(":\\", value)
            self.assertNotIn("\\\\", value)
            self.assertNotIn(str(REPO_ROOT), value)
        self.assertEqual(len(facts), len(card.facts))
        self.assertIn("Workspace", facts)
        self.assertIn("Последняя версия", facts)
        self.assertIn("Последний запуск", facts)

    def test_technical_fields_are_in_the_collapsed_block(self) -> None:
        card = _completed_with_files(
            run_record={
                "run_id": "run-00000001",
                "state": "COMPLETED",
                "outcome": "PROJECT_COMPLETED",
                "event_count": 7,
                "last_sequence_number": 6,
                "classification": {
                    "outcome": "TERMINAL_SUCCESS",
                    "reason": "state_terminal_success",
                    "detail": "state=COMPLETED",
                    "evidence": ["state=COMPLETED", "no_error"],
                },
            },
            provider_registry_ids=("mock", "openrouter"),
            api_routes=("/api/projects/lifecycle", "/api/projects/{project_id}"),
        )
        # Collapsed by default, and the interface is told so.
        self.assertTrue(card.technical_collapsed)
        payload = card.to_dict()["technical"]
        self.assertIs(payload["collapsed"], True)
        fields = {field.key: field for field in card.technical}
        for key in TECHNICAL_KEYS:
            self.assertIn(key, fields, f"{key} must be a technical field")
        self.assertEqual(fields["run_id"].value, "run-00000001")
        self.assertEqual(fields["classification_outcome"].value, "TERMINAL_SUCCESS")
        self.assertEqual(fields["declared_outcome_label"].value, "ГОТОВО")
        self.assertEqual(fields["event_count"].value, "7")
        self.assertEqual(fields["sequence_number"].value, "6")
        self.assertIn("mock", fields["provider_registry_ids"].value)
        self.assertIn("/api/projects/lifecycle", fields["api_routes"].value)
        self.assertIn("index.html", fields["fingerprints"].value)
        # None of them is visible above the fold.
        visible_labels = {label for label, _value in card.headline} | {
            label for label, _value in card.facts
        }
        for key in TECHNICAL_KEYS:
            self.assertNotIn(key, visible_labels)
        visible_text = " ".join(
            [value for _label, value in card.headline]
            + [value for _label, value in card.facts]
        )
        for raw in ("TERMINAL_SUCCESS", "run-00000001", "mock, openrouter"):
            self.assertNotIn(raw, visible_text)

    def test_a_missing_run_record_is_reported_as_unknown(self) -> None:
        card = _completed_with_files()
        fields = {field.key: field for field in card.technical}
        self.assertEqual(fields["event_count"].value, "неизвестно")
        self.assertEqual(fields["sequence_number"].value, "неизвестно")
        self.assertEqual(fields["declared_outcome_label"].value, "НЕИЗВЕСТНО")
        self.assertEqual(fields["api_routes"].value, "—")


class SaveFactTests(unittest.TestCase):
    """Six facts, each answered separately, and never "да" by default."""

    def test_all_six_facts_are_present_and_separate(self) -> None:
        card = _completed_with_files()
        self.assertEqual(
            [item.key for item in card.save_facts], list(SAVE_FACT_KEYS)
        )
        for item in card.save_facts:
            self.assertEqual(item.label, SAVE_FACT_LABELS[item.key])

    def test_an_empty_project_answers_each_fact_honestly(self) -> None:
        card = build_project_presentation(
            project=_project(status="PLANNED", current_version=0),
            versions=[],
            files=[],
            workspace_exists=True,
        )
        answers = {item.key: item for item in card.save_facts}
        self.assertEqual(answers["run_record"].value, "нет")  # no run was ever recorded
        self.assertEqual(answers["specification"].value, "да")
        self.assertEqual(answers["plan"].value, "да")
        self.assertEqual(answers["files"].value, "нет")
        self.assertEqual(answers["verification"].value, "нет")
        self.assertEqual(answers["project_ready"].value, "нет")

    def test_an_unreadable_fact_is_unknown_not_yes(self) -> None:
        # No project record at all: nothing may be claimed, and nothing defaults to да.
        card = build_project_presentation(
            project=None, versions=[], files=[], workspace_exists=False
        )
        for item in card.save_facts:
            self.assertNotEqual(item.value, "да", item.key)
            if item.key != "run_record":
                self.assertFalse(item.known, item.key)
                self.assertEqual(item.value, "неизвестно")

    def test_the_run_record_fact_is_unknown_when_the_record_cannot_be_read(self) -> None:
        # A version names a run, but the record itself is unreadable.
        card = build_project_presentation(
            project=_project(status="NEEDS_CHANGES", current_version=1),
            versions=[_version(verification="not_run")],
            files=[],
            workspace_exists=True,
            run_record=None,
        )
        fact = {item.key: item for item in card.save_facts}["run_record"]
        self.assertEqual(fact.value, "неизвестно")
        self.assertFalse(fact.known)


class ActionGatingTests(unittest.TestCase):
    """Which actions a state may offer, and why the others are not offered."""

    def test_only_continuing_is_offered_with_no_files(self) -> None:
        card = build_project_presentation(
            project=_project(status="PLANNED"),
            versions=[],
            files=[],
            workspace_exists=True,
        )
        offered = [line.action_id for line in card.actions if line.visible]
        self.assertEqual(offered, ["continue_creation"])
        self.assertTrue(card.offers("continue_creation"))
        self.assertEqual(
            card.action("continue_creation").label, ACTIONS["continue_creation"]
        )
        # Answering the questions is offered only while a session is open.
        with_session = build_project_presentation(
            project=_project(status="NEEDS_CLARIFICATION"),
            versions=[],
            files=[],
            workspace_exists=True,
            session_open=True,
        )
        self.assertEqual(
            [line.action_id for line in with_session.actions if line.visible],
            ["continue_creation", "answer_questions"],
        )
        self.assertTrue(with_session.offers("answer_questions"))

    def test_the_launch_action_is_hidden_with_zero_files_and_says_why(self) -> None:
        card = build_project_presentation(
            project=_project(status="PLANNED"),
            versions=[],
            files=[],
            workspace_exists=True,
        )
        for action_id in ("launch_local", "open_app"):
            line = card.action(action_id)
            self.assertIsNotNone(line)
            self.assertFalse(line.visible)
            self.assertFalse(line.enabled)
            self.assertNotEqual(line.reason, "")

    def test_launching_requires_a_real_launchable_artifact(self) -> None:
        # Files exist, but none of them can be opened or run: both actions stay hidden
        # with a reason rather than offering a button that would do nothing.
        card = build_project_presentation(
            project=_project(status="COMPLETED", current_version=1),
            versions=[_version(verification="passed", acceptance="passed", file_count=1)],
            files=[_artifact("notes.md")],
            workspace_exists=True,
        )
        self.assertFalse(card.action("open_app").visible)
        self.assertFalse(card.action("launch_local").visible)
        self.assertIn("Нет файла запуска", card.action("open_app").reason)

    def test_an_html_page_offers_opening_and_an_entry_file_offers_running(self) -> None:
        page = build_project_presentation(
            project=_project(status="COMPLETED", current_version=1),
            versions=[_version(verification="passed", acceptance="passed", file_count=1)],
            files=[_artifact("index.html")],
            workspace_exists=True,
        )
        self.assertTrue(page.offers("open_app"))
        self.assertFalse(page.action("launch_local").visible)
        self.assertEqual(page.launch_role, "open")
        self.assertEqual(page.launch_path, "index.html")

        entry = build_project_presentation(
            project=_project(status="COMPLETED", current_version=1),
            versions=[_version(verification="passed", acceptance="passed", file_count=2)],
            files=[_artifact("truth_tool.py"), _artifact("main.py")],
            workspace_exists=True,
        )
        self.assertTrue(entry.offers("launch_local"))
        self.assertFalse(entry.action("open_app").visible)
        self.assertEqual(entry.launch_role, "run")
        self.assertEqual(entry.launch_path, "main.py")

    def test_opening_requires_an_existing_project(self) -> None:
        # No record: nothing can be opened, and the card does not offer to.
        nothing = build_project_presentation(
            project=None, versions=[], files=[], workspace_exists=False
        )
        self.assertFalse(nothing.offers("open_app"))
        self.assertFalse(nothing.offers("launch_local"))
        self.assertFalse(nothing.offers("view_files"))

    def test_a_project_with_files_offers_the_full_set(self) -> None:
        card = _completed_with_files()
        for action_id in ("add_feature", "view_files", "reverify"):
            self.assertTrue(card.offers(action_id), action_id)

    def test_a_new_version_requires_a_real_change_request(self) -> None:
        without_text = _completed_with_files()
        self.assertFalse(without_text.offers("new_version"))
        self.assertIn("опишите изменение", without_text.action("new_version").reason)
        with_text = _completed_with_files(change_request_text="добавить экспорт в CSV")
        self.assertTrue(with_text.offers("new_version"))

    def test_an_archived_project_offers_nothing_that_would_be_refused(self) -> None:
        card = build_project_presentation(
            project=_project(status="ARCHIVED", archived=True, current_version=1),
            versions=[_version(verification="passed", acceptance="passed", file_count=1)],
            files=[_artifact()],
            workspace_exists=True,
        )
        self.assertEqual(card.project_state, "АРХИВ")
        for line in card.actions:
            self.assertFalse(line.enabled, line.action_id)
            self.assertNotEqual(line.reason, "", line.action_id)
        self.assertIn("архив", card.next_step.lower())

    def test_the_next_step_is_the_one_actionable_one(self) -> None:
        # A record waiting for its confirmation has one thing to do; a planned record
        # is confirmed by requesting the creation, so its next step is the creation.
        waiting = build_project_presentation(
            project=_project(status="WAITING_FOR_CONFIRMATION"), versions=[], files=[]
        )
        self.assertEqual(waiting.next_step, "подтвердить план")
        planned = build_project_presentation(
            project=_project(status="PLANNED"), versions=[], files=[]
        )
        self.assertEqual(planned.next_step, "запустить создание проекта")
        asking = build_project_presentation(
            project=_project(status="NEEDS_CLARIFICATION"),
            versions=[],
            files=[],
            questions_open=True,
        )
        self.assertEqual(asking.next_step, "ответить на вопросы")

    def test_the_fact_block_has_no_verification_line_of_its_own(self) -> None:
        # The verification sentence lives in the truth block, once; the facts block
        # carries the five facts the product defines for it.
        card = _completed_with_files()
        self.assertEqual(
            [label for label, _value in card.facts],
            ["Workspace", "Папка проекта", "Файлы", "Последняя версия", "Последний запуск"],
        )

    def test_needs_changes_states_the_next_action(self) -> None:
        card = build_project_presentation(
            project=_project(status="NEEDS_CHANGES", current_version=1),
            versions=[_version(verification="not_run", acceptance="passed")],
            files=[],
            workspace_exists=True,
        )
        # Nothing was written, so the honest state is "not created" -- and the card
        # still names the next step instead of leaving the reader without one.
        self.assertEqual(card.project_state, "НЕ СОЗДАН")
        self.assertNotEqual(card.next_step, "")
        self.assertTrue(card.offers("continue_creation"))
        self.assertIn(NO_FILES_NOTICE, card.notices)

        # The same status with a file on disk is a project that needs work, not a
        # missing one, and it offers the follow-up.
        with_files = build_project_presentation(
            project=_project(status="NEEDS_CHANGES", current_version=1),
            versions=[_version(verification="not_run", acceptance="failed", file_count=1)],
            files=[_artifact("truth_tool.py")],
            workspace_exists=True,
            change_request_text="исправить падающую проверку",
        )
        self.assertEqual(with_files.project_state, "НУЖНЫ ДОРАБОТКИ")
        self.assertEqual(dict(with_files.headline)["Следующий шаг"], "запустить проверку")
        self.assertTrue(with_files.offers("add_feature"))
        self.assertTrue(with_files.offers("new_version"))

    def test_a_successful_answer_with_no_file_names_what_happened(self) -> None:
        card = build_project_presentation(
            project=_project(status="NEEDS_CHANGES", current_version=1),
            versions=[_version(verification="not_run", acceptance="passed")],
            files=[],
            workspace_exists=True,
        )
        self.assertEqual(card.last_action, "получен ответ модели")
        self.assertEqual(card.verification_line, VERIFICATION_NOT_RUN_LINE)


class _LifecycleCase(unittest.TestCase):
    """A real lifecycle in its own scratch tree, with a fixed answer."""

    def setUp(self) -> None:
        base = SCRATCH / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.store = ProjectStore(root=base / "projects")
        self.projects = ProjectService(self.store)
        self.run_workspace = base / "run-workspace"
        self.run_workspace.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

    def _session(self):
        """A confirmed-session stand-in: only ``to_dict`` is used by the project layer."""

        from app.projects.store import new_project_id

        goal = "Небольшое приложение для учёта домашних расходов"

        class _Specification:
            def to_dict(self) -> dict:
                return {
                    "goal": goal,
                    "project_type": "web app",
                    "core_features": ["add expenses", "monthly report"],
                }

        return SimpleNamespace(
            session_id=f"disc-{new_project_id()[5:]}",
            initial_goal=goal,
            specification=_Specification(),
        )

    def _write_record(self, run_id: str, relative: str = "truth_tool.py") -> dict:
        write = apply_changes(
            WRITE_ANSWER.replace("truth_tool.py", relative),
            workspace=Workspace(self.run_workspace),
            run_id=run_id,
        )
        self.assertEqual(write.status, "applied")
        self.assertEqual(write.verification, "passed")
        return {
            "run_id": run_id,
            "state": "COMPLETED",
            "status": "COMPLETED",
            "classification": {
                "outcome": "TERMINAL_SUCCESS",
                "is_success": True,
                "is_terminal": True,
            },
            "acceptance": "passed",
            "provider_used": "fake",
            "model_used": "deterministic-fake",
            "project_state": {
                "code_write": {
                    "status": write.status,
                    "verification": write.verification,
                    "files": [dict(item) for item in write.files],
                }
            },
        }

    def _empty_record(self, run_id: str) -> dict:
        return {
            "run_id": run_id,
            "state": "COMPLETED",
            "status": "COMPLETED",
            "classification": {
                "outcome": "TERMINAL_SUCCESS",
                "is_success": True,
                "is_terminal": True,
            },
            "acceptance": "passed",
            "provider_used": "fake",
            "model_used": "deterministic-fake",
            "project_state": {
                "code_write": {
                    "status": "no_changes",
                    "verification": "not_run",
                    "files": [],
                }
            },
        }

    def _present(self, project) -> object:
        current = project.current()
        files = [item.to_dict() for item in current.files] if current else []
        return build_project_presentation(
            project=project.to_dict(),
            versions=[item.summary() for item in reversed(project.versions)],
            files=files,
            workspace_exists=self.store.workspace_path(project.project_id).is_dir(),
        )


class ManifestAndVersionTests(_LifecycleCase):
    """Real files produce a real manifest; a second version preserves the first."""

    def test_real_files_produce_a_manifest_and_a_completed_card(self) -> None:
        project = self.projects.create_from_discovery(self._session())
        project = self.projects.confirm(project.project_id)
        project = self.projects.attach_run(
            project.project_id,
            self._write_record("run-manifest-1"),
            source_root=self.run_workspace,
        )
        version = project.current()
        self.assertIsNotNone(version)
        self.assertEqual(len(version.files), 1)
        self.assertEqual(version.files[0].path, "truth_tool.py")
        self.assertEqual(version.files[0].verification_relevance, "compiled")
        self.assertEqual(version.verification, "passed")
        self.assertEqual(version.acceptance, "passed")
        self.assertIs(project.status, ProjectStatus.COMPLETED)

        card = self._present(project)
        self.assertEqual(card.files_count, 1)
        self.assertEqual(card.project_state, "ГОТОВО")
        facts = {item.key: item for item in card.save_facts}
        self.assertEqual(facts["files"].value, "да")
        self.assertEqual(
            dict(card.facts)["Папка проекта"], project.project_path
        )

    def test_a_second_version_preserves_the_first_and_writes_nothing(self) -> None:
        project = self.projects.create_from_discovery(self._session())
        project = self.projects.confirm(project.project_id)
        project = self.projects.attach_run(
            project.project_id,
            self._write_record("run-first"),
            source_root=self.run_workspace,
        )
        first_before = json.dumps(project.version(1).to_dict(), sort_keys=True)
        staged = self.projects.new_version(project.project_id, "добавить экспорт в CSV")
        self.assertEqual(staged.version, 2)

        # The change run answered, but wrote nothing: the version is recorded and is
        # NOT advanced, and the first version stays exactly as it was.
        project = self.projects.attach_run(
            project.project_id,
            self._empty_record("run-second"),
            source_root=self.run_workspace,
        )
        self.assertEqual(project.current_version, 1)
        self.assertEqual(json.dumps(project.version(1).to_dict(), sort_keys=True), first_before)
        self.assertEqual(project.version(2).files, ())
        self.assertEqual(project.current().run_id, "run-first")
        self.assertIs(project.status, ProjectStatus.NEEDS_CHANGES)

        card = self._present(project)
        self.assertTrue(card.change_not_written)
        self.assertIn(RUN_NO_CHANGE_NOTICE, card.notices)
        self.assertEqual(card.files_count, 1)
        self.assertEqual(card.project_state, "НУЖНЫ ДОРАБОТКИ")
        self.assertNotEqual(card.project_state, "ГОТОВО")


if __name__ == "__main__":
    unittest.main()
