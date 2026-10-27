"""The questions must be about *the owner's own task*, not a generic questionnaire.

Measured complaint: «тупые вопросы, одни и те же, целую кучу, вообще не по теме». The
questions were produced by keyword rules over twelve fixed project areas, so a mail
client and a hotel booking system were asked the same things.

What is asserted here, offline (no provider, no network, a fake model at most):

* the idea is classified into a domain with a confidence and the words that decided it;
* the questions come from that domain's own bank -- mail asks about mailboxes, spam and
  a digest; booking asks about guests, entry, payment and slots; a visit-card site asks
  about style and search and nothing about auth or integrations;
* a question whose answer is already in the idea, or whose field is already answered, is
  not asked again; duplicate fields and duplicate texts collapse;
* the caps hold: four questions for a short idea, six for a detailed one, and the
  product's own round bound is never exceeded;
* a negative instruction («без оплаты») removes every payment question;
* the relevance guard rejects a question whose field and words are not in the bank;
* the optional model path merges three validated questions before the deterministic ones,
  and on malformed JSON, a timeout or the mock provider the deterministic questions are
  returned unchanged -- never an exception;
* the session payload carries the domain and its confidence, which is what the card's
  chip renders.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import unittest
from types import SimpleNamespace

from app.discovery import DiscoveryService
from app.discovery.banks import (
    LONG_IDEA_MAX_QUESTIONS,
    SHORT_IDEA_MAX_QUESTIONS,
    choose_questions,
    is_relevant,
    merge_questions,
)
from app.discovery.domain import DOMAINS, MIN_CONFIDENCE, detect_domain
from app.discovery.llm_questions import (
    MODEL_QUESTION_LIMIT,
    ModelQuestionAssistant,
    parse_model_questions,
)
from app.discovery.models import (
    ClarificationOption,
    ClarificationQuestion,
)
from app.discovery.questions import clarification_questions
from app.discovery.store import DiscoveryStore


#: The owner's own kind of idea: a mail client. It states no mailboxes, no spam rule and
#: no digest, so those are exactly what must be asked.
MAIL_IDEA = (
    "Хочу приложение, которое читает мою почту и не даёт пропустить важное письмо."
)

#: The same idea, with the users already stated. The «кто будет пользоваться» question
#: must not come back.
MAIL_IDEA_WITH_USERS = (
    "Хочу приложение только для себя: читает мою почту и показывает важные письма."
)

#: A booking idea: it states the platform, so the booking bank asks about its own
#: decisions instead.
BOOKING_IDEA = "Хочу сайт для бронирования отелей."

#: The same booking idea with a negative instruction: no payment question may survive.
BOOKING_WITHOUT_PAYMENT = (
    "Хочу сайт для бронирования отелей, но без оплаты — только заявки."
)

#: A static visit card: no integrations, no auth, at most three questions.
SITE_IDEA = (
    "Нужна одностраничная сайт-визитка для мастерской: услуги, примеры работ, "
    "контакты. Без формы заявки и без регистрации."
)

#: One long brief (over 240 characters), so the six-question cap applies.
LONG_MAIL_IDEA = MAIL_IDEA + " " + "Очень длинное описание задачи без лишних деталей. " * 8


class _DiscoveryCase(unittest.TestCase):
    """A discovery service rooted in a temporary directory, removed afterwards."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-discovery-domain"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        self.base = base
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.discovery = DiscoveryService(DiscoveryStore(root=base / "discovery"))

    def start(self, goal: str):
        return self.discovery.start(goal)


# --------------------------------------------------------------------------- #
# 1. the domain model
# --------------------------------------------------------------------------- #


class DomainDetectionTests(unittest.TestCase):
    """The classification is deterministic, published with evidence, and honest."""

    def test_01_the_owner_mail_idea_is_a_mail_task(self) -> None:
        guess = detect_domain(MAIL_IDEA)

        self.assertEqual(guess.domain, "mail")
        self.assertGreaterEqual(guess.confidence, MIN_CONFIDENCE)
        self.assertLess(guess.confidence, 1.0, "a rule system never claims certainty")
        joined = " ".join(guess.evidence)
        self.assertIn("почт", joined)
        self.assertIn("письм", joined)

    def test_02_booking_and_finance_are_recognised_by_their_own_words(self) -> None:
        self.assertEqual(detect_domain(BOOKING_IDEA).domain, "booking")
        self.assertEqual(
            detect_domain(
                "Хочу небольшое локальное веб-приложение для учёта домашних расходов"
            ).domain,
            "finance",
        )
        self.assertEqual(detect_domain(SITE_IDEA).domain, "site")

    def test_03_an_idea_it_does_not_understand_stays_other(self) -> None:
        for idea in ("Сделай что-нибудь полезное.", "", None):
            with self.subTest(idea=idea):
                guess = detect_domain(idea)
                self.assertEqual(guess.domain, "other")
                self.assertFalse(guess.recognised)

    def test_04_the_classification_is_deterministic(self) -> None:
        self.assertEqual(detect_domain(MAIL_IDEA), detect_domain(MAIL_IDEA))

    def test_05_a_negated_bot_is_not_a_bot(self) -> None:
        # «работать» contains the letters of «бота»; the word boundary is the fix, and a
        # negated «это не бот» must not classify the idea as a chat-bot either.
        self.assertNotEqual(
            detect_domain("Это не бот, а сайт для бронирования отелей").domain,
            "bot",
        )
        self.assertEqual(detect_domain("Должно работать локально").domain, "other")

    def test_06_every_domain_has_a_bank_of_the_promised_size(self) -> None:
        from app.discovery.banks import BANKS

        self.assertIn("other", DOMAINS)
        for domain in DOMAINS:
            if domain == "other":
                self.assertEqual(BANKS[domain], ())
                continue
            with self.subTest(domain=domain):
                self.assertGreaterEqual(len(BANKS[domain]), 5)
                self.assertLessEqual(len(BANKS[domain]), 9)
                fields = [entry.field for entry in BANKS[domain]]
                self.assertEqual(len(fields), len(set(fields)), "a field is asked twice")
                for entry in BANKS[domain]:
                    self.assertIn(entry.control, {"radio", "checkbox", "select", "yes_no", "text"})
                    if entry.control != "text":
                        self.assertTrue(entry.options)
                    recommended = [item for item in entry.options if item[2]]
                    self.assertEqual(
                        len(recommended),
                        1,
                        f"{domain}/{entry.field} needs exactly one recommended option",
                    )
                    self.assertTrue(entry.relevance.strip(), "the relevance line is missing")


# --------------------------------------------------------------------------- #
# 2. the owner's mail idea
# --------------------------------------------------------------------------- #


class MailQuestionsTests(_DiscoveryCase):
    """The owner's idea gets mail questions -- and never a payment question."""

    def test_01_the_questions_are_about_mail(self) -> None:
        session = self.start(MAIL_IDEA)
        self.assertEqual(session.domain, "mail")
        self.assertTrue(session.questions)
        texts = " ".join(question.text.casefold() for question in session.questions)
        self.assertTrue(
            any(word in texts for word in ("ящик", "спам", "письм")),
            texts,
        )
        for question in session.questions:
            with self.subTest(question=question.id):
                self.assertTrue(question.relevance.strip(), "«Почему этот вопрос» is empty")
                self.assertIn(question.control, {"radio", "checkbox", "select", "yes_no"})
                self.assertTrue(question.options)
                self.assertTrue(question.recommended_values())

    def test_02_a_mail_app_is_never_asked_about_payment(self) -> None:
        session = self.start(MAIL_IDEA)
        haystack = " ".join(
            [question.text for question in session.questions]
            + [option.label for question in session.questions for option in question.options]
        ).casefold()
        self.assertNotIn("оплат", haystack)
        self.assertNotIn("платеж", haystack)
        self.assertNotIn("платёж", haystack)

    def test_03_users_are_not_asked_when_the_idea_says_them(self) -> None:
        session = self.start(MAIL_IDEA_WITH_USERS)
        fields = [question.field for question in session.questions]
        self.assertNotIn("users", fields)
        for question in session.questions:
            self.assertNotIn("кто будет пользоваться", question.text.casefold())

    def test_04_the_card_receives_the_domain_and_its_confidence(self) -> None:
        session = self.start(MAIL_IDEA)
        payload = session.to_dict()

        self.assertEqual(payload["domain"], "mail")
        self.assertEqual(payload["domain_label"], "почтовый клиент")
        self.assertGreaterEqual(payload["domain_confidence"], MIN_CONFIDENCE)
        self.assertTrue(payload["domain_evidence"])
        # Everything the chip and the relevance line render travels in the payload.
        self.assertEqual(payload["questions"][0]["relevance"], session.questions[0].relevance)


# --------------------------------------------------------------------------- #
# 3. the selection rules
# --------------------------------------------------------------------------- #


class SelectionRulesTests(unittest.TestCase):
    """Only unasked, unrefused, relevant questions, deduplicated and bounded."""

    def test_01_an_answered_field_is_never_asked_again(self) -> None:
        fields = [
            question.field
            for question in choose_questions(MAIL_IDEA, "mail", {"integrations"})
        ]
        self.assertNotIn("integrations", fields)
        self.assertIn("spam_action", fields)

    def test_02_duplicate_fields_and_duplicate_texts_collapse(self) -> None:
        def question(field: str, text: str) -> ClarificationQuestion:
            return ClarificationQuestion(
                id=f"q-{field}",
                text=text,
                field=field,
                why="зачем",
                control="radio",
                options=(ClarificationOption("да", "Да", True),),
            )

        merged = merge_questions(
            [question("a", "Первый вопрос?"), question("b", "Первый вопрос?")],
            [question("a", "Другой текст?"), question("c", "Третий вопрос?")],
            limit=6,
        )
        self.assertEqual([item.field for item in merged], ["a", "c"])

    def test_03_the_short_and_long_caps_hold(self) -> None:
        short = choose_questions(MAIL_IDEA, "mail", ())
        long = choose_questions(LONG_MAIL_IDEA, "mail", ())

        self.assertEqual(len(short), SHORT_IDEA_MAX_QUESTIONS)
        self.assertEqual(SHORT_IDEA_MAX_QUESTIONS, 4)
        self.assertEqual(len(long), LONG_IDEA_MAX_QUESTIONS)
        self.assertEqual(LONG_IDEA_MAX_QUESTIONS, 6)

    def test_04_the_product_round_bound_is_never_exceeded(self) -> None:
        # The service's own round keeps the older bound: a short idea is at most three.
        self.assertLessEqual(len(clarification_questions(MAIL_IDEA)), 3)

    def test_05_a_negative_instruction_removes_every_payment_question(self) -> None:
        fields = [
            question.field
            for question in choose_questions(BOOKING_WITHOUT_PAYMENT, "booking", ())
        ]
        self.assertNotIn("integrations", fields)
        haystack = " ".join(
            [question.text for question in clarification_questions(BOOKING_WITHOUT_PAYMENT)]
            + [
                option.label
                for question in clarification_questions(BOOKING_WITHOUT_PAYMENT)
                for option in question.options
            ]
        ).casefold()
        self.assertNotIn("оплат", haystack)

    def test_06_the_relevance_guard_rejects_a_foreign_question(self) -> None:
        foreign = ClarificationQuestion(
            id="q-weather",
            text="Какая сегодня погода?",
            field="weather",
            why="зачем",
        )
        self.assertFalse(is_relevant(foreign, "mail"))
        self.assertFalse(is_relevant(foreign, "booking"))
        # The same guard accepts a bank question, and a new question about the bank's
        # own topic (that is what a model's question has to be).
        bank_question = choose_questions(MAIL_IDEA, "mail", ())[0]
        self.assertTrue(is_relevant(bank_question, "mail"))
        on_topic = ClarificationQuestion(
            id="q-mailboxes",
            text="Сколько ящиков подключаем в первую версии?",
            field="mailboxes",
            why="зачем",
        )
        self.assertTrue(is_relevant(on_topic, "mail"))
        self.assertFalse(is_relevant(on_topic, "booking"))

    def test_07_the_booking_idea_asks_booking_questions(self) -> None:
        fields = [
            question.field
            for question in clarification_questions(BOOKING_IDEA)
        ]
        self.assertIn("users", fields)
        self.assertIn("auth", fields)
        self.assertNotIn("platform", fields, "the platform is stated by the idea")

    def test_08_a_visit_card_asks_at_most_three_and_no_auth(self) -> None:
        questions = clarification_questions(SITE_IDEA)
        self.assertLessEqual(len(questions), 3)
        self.assertGreaterEqual(len(questions), 1)
        haystack = " ".join(
            [question.text for question in questions]
            + [option.label for question in questions for option in question.options]
        ).casefold()
        for forbidden in ("авторизац", "регистрац", "вход", "оплат"):
            self.assertNotIn(forbidden, haystack)


# --------------------------------------------------------------------------- #
# 4. answers to the new questions
# --------------------------------------------------------------------------- #


class DomainAnswerTests(_DiscoveryCase):
    """A domain question is answerable, stored, and never asked twice."""

    def test_01_a_domain_answer_reaches_the_specification(self) -> None:
        session = self.start(MAIL_IDEA)
        self.assertIn("spam_action", [question.field for question in session.questions])

        session = self.discovery.answer(
            session.session_id,
            [{"question_id": "q-spam_action", "values": ["delete"]}],
        )

        answer = session.answer_for("spam_action")
        self.assertIsNotNone(answer)
        self.assertEqual(answer.text, "удалять в корзину")
        # It is not asked again ...
        self.assertNotIn(
            "spam_action", [question.field for question in session.questions]
        )
        # ... and it reaches the text the planner receives.
        specification = self.discovery.specification(session.session_id)
        self.assertTrue(specification.domain_answers)
        self.assertIn("удалять в корзину", json.dumps(
            specification.domain_answers, ensure_ascii=False
        ))
        self.assertIn("Task answers:", specification.as_task_text())

    def test_02_a_skipped_domain_question_is_not_asked_again(self) -> None:
        session = self.start(MAIL_IDEA)
        session = self.discovery.answer(
            session.session_id,
            [{"question_id": "q-spam_action", "skipped": True}],
        )

        answer = session.answer_for("spam_action")
        self.assertTrue(answer.skipped)
        self.assertEqual(answer.text, "")
        self.assertNotIn(
            "q-spam_action", [question.id for question in session.questions]
        )

    def test_03_a_domain_question_can_be_corrected(self) -> None:
        session = self.start(MAIL_IDEA)
        session = self.discovery.answer(
            session.session_id,
            [{"question_id": "q-spam_action", "values": ["mark"]}],
        )
        session = self.discovery.answer(
            session.session_id,
            [{"question_id": "q-spam_action", "values": ["keep"]}],
        )

        self.assertEqual(session.answer_for("spam_action").text, "оставлять как есть")
        stored = [a for a in session.answers if a.question_id == "q-spam_action"]
        self.assertEqual(len(stored), 1)

    def test_04_an_invented_domain_field_is_still_refused(self) -> None:
        from app.discovery import InvalidClarificationAnswer

        session = self.start(MAIL_IDEA)
        with self.assertRaises(InvalidClarificationAnswer):
            self.discovery.answer(
                session.session_id,
                [{"question_id": "q-not-in-any-bank", "text": "x"}],
            )

    def test_05_the_session_stays_confirmable_when_the_bank_is_exhausted(self) -> None:
        # A mail idea does not state a project type; the mail bank does not ask for one.
        # Once every bank question is answered, the session is ready -- asking a generic
        # «тип проекта» question instead is exactly the defect this change removes.
        session = self.start(MAIL_IDEA)
        for _ in range(6):
            if not session.questions:
                break
            answers = [
                {"question_id": question.id, "unknown": True}
                for question in session.questions
            ]
            session = self.discovery.answer(session.session_id, answers)

        self.assertEqual(session.questions, ())
        self.assertEqual(session.state.value, "READY_FOR_PLANNING")
        self.discovery.confirm(session.session_id)


# --------------------------------------------------------------------------- #
# 5. the optional model path
# --------------------------------------------------------------------------- #


_VALID_MODEL_ANSWER = json.dumps(
    {
        "questions": [
            {
                "id": "mailbox_choice",
                "text": "Сколько ящиков подключаем в первой версии?",
                "field": "mailbox_choice",
                "why": "От числа ящиков зависит экран.",
                "relevance": "Ящик — основная единица почтового клиента.",
                "control": "radio",
                "options": [
                    {"value": "one", "label": "один ящик", "recommended": True},
                    {"value": "many", "label": "несколько ящиков"},
                ],
            },
            {
                "id": "spam_folder",
                "text": "Куда складывать спам — в отдельную папку?",
                "field": "spam_folder",
                "why": "Папка нужна, чтобы не терять письма.",
                "relevance": "Спам разбирают каждый день.",
                "control": "radio",
                "options": [
                    {"value": "folder", "label": "в папку «Спам»", "recommended": True},
                    {"value": "delete", "label": "удалять"},
                ],
            },
            {
                "id": "signature",
                "text": "Нужна ли подпись в письмах?",
                "field": "signature",
                "why": "Подпись — отдельная настройка.",
                "relevance": "Подпись отличается в деловой переписке.",
                "control": "yes_no",
                "options": [
                    {"value": "да", "label": "Да, нужна", "recommended": True},
                    {"value": "нет", "label": "Нет, не нужна"},
                ],
            },
        ]
    },
    ensure_ascii=False,
)


class _FakeProvider:
    """A provider double: records the prompt, returns one output, or raises."""

    def __init__(self, output: str = "", *, provider_name: str = "fake-live", error: object = None):
        self.provider_name = provider_name
        self._output = output
        self._error = error
        self.requests: list = []

    def generate(self, request):
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(output=self._output)


class ModelQuestionTests(_DiscoveryCase):
    """Opt-in, validated, merged first -- and invisible when it fails."""

    def _assistant(self, provider, *, enabled: bool = True) -> ModelQuestionAssistant:
        return ModelQuestionAssistant(
            lambda: provider,
            enabled=lambda: enabled,
            cache={},
        )

    def test_01_valid_model_questions_are_merged_first_and_capped(self) -> None:
        assistant = self._assistant(_FakeProvider(_VALID_MODEL_ANSWER))
        questions = clarification_questions(
            MAIL_IDEA, limit=6, model_questions=assistant.questions
        )

        self.assertEqual([question.field for question in questions[:MODEL_QUESTION_LIMIT]], [
            "mailbox_choice",
            "spam_folder",
            "signature",
        ])
        self.assertEqual(len(questions), 6)
        fields = [question.field for question in questions]
        self.assertEqual(len(fields), len(set(fields)), "a field was asked twice")
        for question in questions[:MODEL_QUESTION_LIMIT]:
            with self.subTest(question=question.id):
                self.assertFalse(question.required, "a model question never blocks a session")
                self.assertTrue(question.relevance.strip())
                self.assertTrue(question.options)

    def test_02_malformed_json_falls_back_without_an_exception(self) -> None:
        assistant = self._assistant(_FakeProvider("это не JSON"))
        deterministic = clarification_questions(MAIL_IDEA)

        self.assertEqual(assistant.questions(MAIL_IDEA, "mail"), ())
        self.assertEqual(
            clarification_questions(MAIL_IDEA, model_questions=assistant.questions),
            deterministic,
        )

    def test_03_a_timeout_falls_back(self) -> None:
        assistant = self._assistant(_FakeProvider(error=TimeoutError("timeout")))

        self.assertEqual(assistant.questions(MAIL_IDEA, "mail"), ())
        self.assertTrue(clarification_questions(MAIL_IDEA, model_questions=assistant.questions))

    def test_04_the_mock_provider_is_never_called(self) -> None:
        provider = _FakeProvider(_VALID_MODEL_ANSWER, provider_name="mock")
        assistant = self._assistant(provider)

        self.assertEqual(assistant.questions(MAIL_IDEA, "mail"), ())
        self.assertEqual(provider.requests, [])

    def test_05_a_disabled_setting_never_calls_the_provider(self) -> None:
        provider = _FakeProvider(_VALID_MODEL_ANSWER)
        assistant = self._assistant(provider, enabled=False)

        self.assertEqual(assistant.questions(MAIL_IDEA, "mail"), ())
        self.assertEqual(provider.requests, [])

    def test_06_answered_fields_and_missing_options_are_rejected(self) -> None:
        payload = json.dumps(
            {
                "questions": [
                    {
                        "text": "Кто будет читать письма?",
                        "field": "users",
                        "options": [{"value": "я", "label": "я"}],
                    },
                    {"text": "Что-то без вариантов?", "field": "no_options"},
                    {
                        "text": "Какая сегодня погода?",
                        "field": "weather",
                        "options": [{"value": "x", "label": "x"}],
                    },
                ]
            },
            ensure_ascii=False,
        )
        questions = parse_model_questions(
            payload, domain="mail", answered_fields=("users",)
        )

        self.assertEqual(questions, ())

    def test_07_the_cache_spends_one_call_per_idea_and_domain(self) -> None:
        provider = _FakeProvider(_VALID_MODEL_ANSWER)
        assistant = self._assistant(provider)

        assistant.questions(MAIL_IDEA, "mail")
        assistant.questions(MAIL_IDEA, "mail")

        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(assistant.questions(MAIL_IDEA, "mail")), MODEL_QUESTION_LIMIT)

    def test_08_the_relevance_guard_applies_to_the_model_path(self) -> None:
        payload = json.dumps(
            {
                "questions": [
                    {
                        "text": "Какая сегодня погода?",
                        "field": "weather",
                        "options": [{"value": "x", "label": "x"}],
                    }
                ]
            },
            ensure_ascii=False,
        )
        self.assertEqual(parse_model_questions(payload, domain="mail"), ())


# --------------------------------------------------------------------------- #
# 6. the session payload through the API
# --------------------------------------------------------------------------- #


class SessionPayloadTests(_DiscoveryCase):
    """The routes publish the domain, its confidence and each question's relevance."""

    def test_01_the_created_session_carries_the_domain_chip_data(self) -> None:
        from fastapi.testclient import TestClient

        from app.api.server import create_api_app
        from app.api.service import ForgeApiService
        from app.projects.service import ProjectService
        from app.projects.store import ProjectStore
        from app.runtime.run_store import RunStore
        from offline_runtime import offline_runtime

        service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.base / "runs"),
            authorized_project_id="discovery-domain-test",
        )
        app = create_api_app(
            service,
            discovery_service=self.discovery,
            project_service=ProjectService(ProjectStore(root=self.base / "projects")),
        )
        client = TestClient(app)

        created = client.post("/api/discovery/sessions", json={"goal": MAIL_IDEA})
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        session = body["session"]

        self.assertEqual(session["domain"], "mail")
        self.assertEqual(session["domain_label"], "почтовый клиент")
        self.assertGreaterEqual(session["domain_confidence"], MIN_CONFIDENCE)
        self.assertTrue(body["questions"])
        self.assertTrue(all(question["relevance"] for question in body["questions"]))


class SettingsToggleTests(_DiscoveryCase):
    """«Вопросы по смыслу задачи (через модель)»: off by default, and one key only."""

    def _client(self, settings_path: pathlib.Path):
        from fastapi.testclient import TestClient

        from app.api.server import create_api_app
        from app.api.service import ForgeApiService
        from app.config.app_settings import AppSettingsStore
        from app.projects.service import ProjectService
        from app.projects.store import ProjectStore
        from app.runtime.run_store import RunStore
        from offline_runtime import offline_runtime

        settings = AppSettingsStore(path=settings_path)
        service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.base / "runs"),
            authorized_project_id="discovery-settings-test",
        )
        app = create_api_app(
            service,
            discovery_service=self.discovery,
            project_service=ProjectService(ProjectStore(root=self.base / "projects")),
            settings_store=settings,
        )
        return TestClient(app), settings

    def test_01_the_model_toggle_is_off_by_default_and_can_be_turned_off_again(self) -> None:
        path = self.base / "settings.json"
        client, settings = self._client(path)

        self.assertIs(client.get("/api/settings").json()["questions_from_model"], False)
        self.assertFalse(settings.questions_from_model())

        written = client.post("/api/settings", json={"questions_from_model": True})
        self.assertEqual(written.status_code, 200, written.text)
        self.assertIs(written.json()["questions_from_model"], True)
        self.assertIs(client.get("/api/settings").json()["questions_from_model"], True)

        again = client.post("/api/settings", json={"questions_from_model": False})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertIs(again.json()["questions_from_model"], False)

        # A value that is not a boolean is refused, and the stored one is untouched.
        refused = client.post("/api/settings", json={"questions_from_model": "yes"})
        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertIs(client.get("/api/settings").json()["questions_from_model"], False)
        # It is one key of the settings file, not a text field in it.
        self.assertIn("questions_from_model", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
