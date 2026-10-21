"""Tests for the clarifying questions (BUG B): ask less, and let the user click.

Measured defect. A short idea was answered with a generic questionnaire: the analyzer
derived nothing from the idea's own words, six questions came back, and every answer had
to be typed. The owner's rules are:

* an area the idea already states is **never** asked («приложение для рабочего стола для
  учёта расходов» says the type, the platform and the goal);
* a short idea gets at most three questions and any idea at most five, and a required
  area is never dropped by that bound;
* every question carries a typed control with real options, one honest ``recommended``
  default, «Свой вариант» and «Не знаю — выбери оптимальный вариант»;
* «Пропустить» is allowed only for an optional question, and a required one is refused
  with a typed error;
* the specification records what the user chose and what was auto-selected, separately.

Everything here is deterministic and offline: no provider, no network, no model.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import unittest

from app.discovery import (
    DiscoveryService,
    InvalidClarificationAnswer,
    RequiredQuestionSkipped,
)
from app.discovery.models import CONTROL_KINDS
from app.discovery.questions import MAX_QUESTIONS, clarification_questions
from app.discovery.store import DiscoveryStore
from app.discovery.analyzer import analyze_goal

#: The verify case: a desktop app for expenses. It states the type, the platform and the
#: goal, so none of those may be asked back.
DESKTOP_EXPENSE = "Сделай приложение для рабочего стола для учёта расходов"

#: The owner's short idea, verbatim.
OWNER_IDEA = (
    "Хочу небольшое локальное веб-приложение для учёта домашних расходов: "
    "список трат, категории, месячный отчёт."
)

#: A detailed brief: it states the type, the platform, the users, the features and the
#: storage, and it must not be answered with a duplicate questionnaire.
DETAILED_PROMPT = (
    "Создай небольшое локальное веб-приложение для учёта домашних расходов.\n"
    "\n"
    "Я хочу:\n"
    "\n"
    "- добавлять расходы;\n"
    "- указывать сумму, дату, категорию и комментарий;\n"
    "- видеть список расходов;\n"
    "- видеть итог за выбранный месяц;\n"
    "- фильтровать расходы по категории;\n"
    "- редактировать и удалять записи.\n"
    "\n"
    "Приложение предназначено для одного пользователя и должно работать локально.\n"
    "\n"
    "Не используй внешние сервисы, онлайн-платежи и реальные deployment-платформы."
)

#: The regression case for the negation rule: a refusal is not a requirement.
NEGATIVE_GOAL = (
    "Хочу приложение для учёта личных расходов, не использовать онлайн-платежи"
)


class _DiscoveryCase(unittest.TestCase):
    """A discovery service rooted in a temporary directory, removed afterwards."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-discovery-questions"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        self.base = base
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.discovery = DiscoveryService(DiscoveryStore(root=base / "discovery"))

    def start(self, goal: str):
        return self.discovery.start(goal)


class AlreadyKnownIsNotAskedTests(_DiscoveryCase):
    """An area the idea states is known from the idea and never asked again."""

    def test_01_a_desktop_expense_app_is_not_asked_its_own_type_or_platform(self) -> None:
        session = self.start(DESKTOP_EXPENSE)
        fields = [question.field for question in session.questions]

        self.assertNotIn("goal", fields)
        self.assertNotIn("project_type", fields)
        self.assertNotIn("platform", fields)
        self.assertNotIn("core_features", fields)

        specification = self.discovery.specification(session.session_id)
        self.assertEqual(specification.project_type, "десктоп-приложение")
        self.assertEqual(specification.platform, "десктоп-приложение")
        self.assertTrue(specification.core_features)
        self.assertIn("учёт расходов", specification.core_features)

    def test_02_the_owner_idea_states_its_type_platform_and_features(self) -> None:
        session = self.start(OWNER_IDEA)
        fields = [question.field for question in session.questions]

        self.assertNotIn("project_type", fields)
        self.assertNotIn("platform", fields)
        self.assertNotIn("core_features", fields)
        specification = self.discovery.specification(session.session_id)
        self.assertEqual(specification.project_type, "веб-приложение")
        self.assertEqual(specification.platform, "веб-сайт")
        # The enumeration in the idea itself is read as features.
        self.assertIn("категории", specification.core_features)
        self.assertIn("месячный отчёт", specification.core_features)

    def test_03_storage_users_and_deployment_are_recognised_from_the_words(self) -> None:
        analysis = analyze_goal(
            "Приложение для учёта расходов, данные хранить в SQLite, "
            "пользуется семья, работать локально"
        )

        self.assertEqual(analysis.data_storage, "SQLite")
        self.assertIn("семья", analysis.users)
        self.assertEqual(analysis.platform, "локальный запуск")
        self.assertEqual(
            analyze_goal("десктопное приложение, данные в json-файле").data_storage,
            "файловое хранилище",
        )
        self.assertEqual(
            analyze_goal("нужен api сервис для отчётов").project_type,
            "API-сервис",
        )
        self.assertEqual(
            analyze_goal("сделай cli утилиту для консоли").project_type,
            "CLI-инструмент",
        )
        self.assertEqual(
            analyze_goal("telegram бот для счетов").project_type, "Telegram-бот"
        )
        # «Работать локально» is not a bot.
        self.assertNotIn("бот", analyze_goal("должно работать локально").project_type)


class TypedQuestionTests(_DiscoveryCase):
    """Every question carries a control the user can click, and real labels."""

    def test_01_every_question_has_a_control_and_labelled_options(self) -> None:
        for goal in (DESKTOP_EXPENSE, OWNER_IDEA, DETAILED_PROMPT, "Сделай что-нибудь"):
            session = self.start(goal)
            for question in session.questions:
                with self.subTest(goal=goal[:24], question=question.id):
                    self.assertIn(question.control, CONTROL_KINDS)
                    self.assertTrue(question.why.strip(), "the «зачем» line is missing")
                    if question.control != "text":
                        self.assertTrue(
                            question.options, "a clickable control needs options"
                        )
                    for option in question.options:
                        self.assertTrue(option.label.strip())
                        self.assertTrue(option.value.strip())
                    self.assertTrue(question.allow_unknown)

    def test_02_each_question_marks_one_honest_recommended_option(self) -> None:
        session = self.start(OWNER_IDEA)
        for question in session.questions:
            with self.subTest(question=question.id):
                self.assertTrue(question.recommended_values())

    def test_03_the_required_option_sets_are_the_product_ones(self) -> None:
        session = self.start("Сделай что-нибудь полезное")
        by_field = {question.field: question for question in session.questions}

        self.assertEqual(
            [option.label for option in by_field["project_type"].options],
            [
                "веб-приложение",
                "приложение для Windows",
                "Telegram-бот",
                "мобильное приложение",
                "API-сервис",
                "CLI",
                "другое",
            ],
        )
        self.assertEqual(by_field["project_type"].control, "radio")
        self.assertEqual(
            [option.label for option in by_field["users"].options],
            [
                "только я",
                "моя семья",
                "сотрудники",
                "клиенты",
                "публичные пользователи",
                "другое",
            ],
        )
        self.assertEqual(
            [option.label for option in by_field["platform"].options],
            [
                "только на этом компьютере",
                "в локальной сети",
                "в интернете",
                "пока не знаю",
            ],
        )

    def test_04_the_expense_feature_list_is_the_domain_list(self) -> None:
        session = self.start(DESKTOP_EXPENSE + ", данные в базе")
        questions = {question.field: question for question in session.questions}
        question = questions.get("core_features")
        if question is None:
            # The features are stated by the idea, so the area is not asked at all.
            self.assertNotIn("core_features", questions)
            return
        self.assertEqual(question.control, "checkbox")
        self.assertEqual(
            [option.label for option in question.options],
            [
                "добавление расходов",
                "редактирование",
                "удаление",
                "месячный отчёт",
                "отчёт по категориям",
                "импорт",
                "экспорт",
            ],
        )

    def test_05_a_yes_no_question_has_exactly_two_answers(self) -> None:
        session = self.start(DETAILED_PROMPT)
        yes_no = [question for question in session.questions if question.control == "yes_no"]
        self.assertTrue(yes_no, [question.control for question in session.questions])
        for question in yes_no:
            with self.subTest(question=question.id):
                self.assertEqual(len(question.options), 2)
                self.assertTrue(question.options[0].label.startswith("Да"))
                self.assertTrue(question.options[1].label.startswith("Нет"))
                # A yes/no needs no «Свой вариант»: it has two honest answers.
                self.assertFalse(question.allow_custom)


class AnswerShapeTests(_DiscoveryCase):
    """The typed answer shapes the service accepts, and what they record."""

    def _first(self, goal: str = OWNER_IDEA):
        session = self.start(goal)
        self.assertTrue(session.questions)
        return session, session.questions[0]

    def test_01_values_and_custom_are_recorded(self) -> None:
        session, question = self._first()
        # Pick the recommended value and add the user's own words.
        recommended = question.recommended_values()[0]
        session = self.discovery.answer(
            session.session_id,
            [
                {
                    "question_id": question.id,
                    "values": [recommended],
                    "custom": "и ещё немного для себя",
                }
            ],
        )

        answer = session.answer_for(question.field)
        self.assertIsNotNone(answer)
        self.assertEqual(answer.values, (recommended,))
        self.assertEqual(answer.custom, "и ещё немного для себя")
        self.assertIn(question.label_for(recommended), answer.text)
        self.assertIn("и ещё немного для себя", answer.text)
        self.assertFalse(answer.auto_selected)

        specification = self.discovery.specification(session.session_id)
        self.assertIn(question.field, specification.chosen_values)
        self.assertNotIn(question.field, specification.auto_selected_values)

    def test_02_custom_text_is_stored_as_the_answer(self) -> None:
        session, question = self._first()
        session = self.discovery.answer(
            session.session_id,
            [
                {
                    "question_id": question.id,
                    "values": [question.recommended_values()[0]],
                    "custom": "свой вариант: два человека",
                }
            ],
        )

        answer = session.answer_for(question.field)
        self.assertEqual(answer.custom, "свой вариант: два человека")
        self.assertIn("свой вариант: два человека", answer.text)

    def test_03_unknown_picks_the_recommended_value_and_marks_it_auto_selected(self) -> None:
        session, question = self._first()
        recommended = question.recommended_values()[0]

        session = self.discovery.answer(
            session.session_id, [{"question_id": question.id, "unknown": True}]
        )

        answer = session.answer_for(question.field)
        self.assertTrue(answer.auto_selected)
        self.assertEqual(answer.values, (recommended,))
        self.assertIn(question.label_for(recommended), answer.text)

        specification = self.discovery.specification(session.session_id)
        self.assertIn(question.field, specification.auto_selected_values)
        self.assertNotIn(question.field, specification.chosen_values)
        self.assertEqual(
            specification.auto_selected_values[question.field],
            (question.label_for(recommended),),
        )

    def test_04_a_required_question_cannot_be_skipped(self) -> None:
        session, question = self._first()
        self.assertTrue(question.required)

        with self.assertRaises(RequiredQuestionSkipped) as raised:
            self.discovery.answer(
                session.session_id,
                [{"question_id": question.id, "skipped": True}],
            )

        self.assertEqual(raised.exception.code, "required_question_skipped")
        # The refusal names the question and never stores anything.
        self.assertIn(question.id, str(raised.exception))
        stored = self.discovery.get(session.session_id)
        self.assertIsNone(stored.answer_for(question.field))

    def test_05_an_optional_question_can_be_skipped(self) -> None:
        session = self.start(OWNER_IDEA)
        optional = [question for question in session.questions if question.optional]
        self.assertTrue(optional, session.questions)
        question = optional[0]

        session = self.discovery.answer(
            session.session_id, [{"question_id": question.id, "skipped": True}]
        )

        answer = session.answer_for(question.field)
        self.assertTrue(answer.skipped)
        self.assertEqual(answer.text, "")
        # A skipped question is not asked again.
        self.assertNotIn(question.id, [item.id for item in session.questions])

    def test_06_an_unknown_question_id_is_still_refused(self) -> None:
        session, _ = self._first()
        with self.assertRaises(InvalidClarificationAnswer):
            self.discovery.answer(
                session.session_id,
                [{"question_id": "q-not-a-field", "values": ["x"]}],
            )

    def test_07_the_legacy_text_answer_still_works(self) -> None:
        session, question = self._first()
        session = self.discovery.answer(
            session.session_id,
            [{"question_id": question.id, "text": "просто ответ словами"}],
        )
        answer = session.answer_for(question.field)
        self.assertEqual(answer.text, "просто ответ словами")
        self.assertFalse(answer.auto_selected)
        self.assertEqual(
            session.specification.chosen_values[question.field],
            ("просто ответ словами",),
        )


class QuestionCountTests(_DiscoveryCase):
    """A short idea is a short conversation; a detailed one is not repeated."""

    def test_01_a_short_idea_yields_at_most_three_questions(self) -> None:
        for goal in (DESKTOP_EXPENSE, OWNER_IDEA):
            with self.subTest(goal=goal[:24]):
                self.assertLessEqual(len(clarification_questions(goal)), 3)

    def test_02_a_detailed_prompt_asks_only_what_is_missing(self) -> None:
        questions = clarification_questions(DETAILED_PROMPT)
        fields = [question.field for question in questions]

        self.assertLessEqual(len(questions), MAX_QUESTIONS)
        self.assertNotIn("goal", fields)
        self.assertNotIn("project_type", fields)
        self.assertNotIn("platform", fields)
        self.assertNotIn("core_features", fields)
        # What it really leaves open is still asked.
        self.assertIn("data_storage", fields)

    def test_03_no_question_duplicates_an_answered_area(self) -> None:
        session = self.start(DETAILED_PROMPT)
        answered: list[str] = []
        for _ in range(6):
            if not session.questions:
                break
            answers = [
                {
                    "question_id": question.id,
                    "unknown": True,
                }
                for question in session.questions
            ]
            answered.extend(question.field for question in session.questions)
            session = self.discovery.answer(session.session_id, answers)

        self.assertEqual(
            [question.field for question in session.questions],
            [],
            "a duplicate questionnaire came back",
        )
        self.assertEqual(len(answered), len(set(answered)), "an area was asked twice")

    def test_04_the_negation_rule_still_holds(self) -> None:
        analysis = analyze_goal(NEGATIVE_GOAL)

        self.assertEqual(analysis.integrations, ())
        session = self.start(NEGATIVE_GOAL)
        specification = self.discovery.specification(session.session_id)
        self.assertEqual(specification.integrations, ())
        # The goal line quotes the user's own words verbatim; what must not appear is a
        # derived *Integrations* value, because a refusal is not a requirement.
        self.assertNotIn("Integrations:", specification.as_task_text())


if __name__ == "__main__":
    unittest.main()
