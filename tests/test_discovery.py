"""Offline tests for the Discovery / Clarification layer.

Every test here is offline and uses fake providers only. The layer itself makes no
provider call at all, so what is exercised is the real behaviour: the
deterministic derivation, the question list, the state machine, the redaction of
secret-shaped answers, the confirmation guard, and the hand-off of a confirmed
specification to the *existing* task path.

Nothing here reads ``.env`` and nothing here reaches the network: the run tests
use ``offline_runtime()`` (the TEST runtime whose only enabled provider is the
offline one) or an ordinary ``Provider`` double registered under a real provider's
name.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import unittest

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import (
    Provider,
    ProviderRequest,
    ProviderResponse,
)
from app.agents.providers.config import ProviderConfig
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery import (
    DiscoveryNotConfirmed,
    DiscoveryNotReady,
    DiscoveryService,
    DiscoveryState,
    DiscoveryStateConflict,
    InvalidClarificationAnswer,
    UnknownDiscoverySession,
)
from app.discovery.store import DiscoveryStore
from app.runtime.run_store import RunStore
from offline_runtime import offline_runtime


#: The hotel-booking goal from the brief: it states the idea and the platform and
#: leaves users, integrations, storage, auth, notifications and constraints open.
HOTEL_GOAL = "Хочу сайт для бронирования отелей."

#: A goal that already states every area, so nothing needs asking.
COMPLETE_GOAL = (
    "Хочу сайт для бронирования отелей для гостей и администраторов: "
    "бронирование, поиск и оплата; платформа веб-сайт; "
    "интеграции: оплата картой, email, SMS; хранение в PostgreSQL; "
    "авторизация по email; уведомления на email; "
    "бюджет ограничен, срок 6 недель; "
    "критерии приёмки: тесты проходят; приоритет — скорость."
)

#: A goal that says nothing useful.
SPARSE_GOAL = "Сделай что-нибудь полезное."


class _RecordingProvider(Provider):
    """Record every request handed to the provider, then answer with its model.

    This is the same double ``tests/test_model_override_dispatch.py`` uses: an
    ordinary ``Provider`` under a real provider's name, so the production
    dispatcher, executor, run scope and response construction all run unchanged.
    """

    def __init__(self, configured_model: str = "unconfigured") -> None:
        super().__init__(
            ProviderConfig(provider_name="deepseek", model_name=configured_model)
        )
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        model = request.model_name or self.model_name
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=model,
            output=f"recorded answer from {model}",
        )


class _DiscoveryTestCase(unittest.TestCase):
    """A discovery service rooted in a temporary directory, removed afterwards."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-discovery"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.root = base / "discovery"
        self.run_root = base / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.store = DiscoveryStore(root=self.root)
        self.discovery = DiscoveryService(self.store)

    # ------------------------------------------------------------------ helpers
    def _session_file_text(self, session_id: str) -> str:
        return self.store.path_for(session_id).read_text(encoding="utf-8")

    def _answer_all(self, session, overrides=None):
        overrides = dict(overrides or {})
        answers = [
            {
                "question_id": question.id,
                "text": overrides.get(
                    question.field, f"ответ для {question.field}"
                ),
            }
            for question in session.questions
        ]
        return self.discovery.answer(session.session_id, answers)


class DiscoverySessionTests(_DiscoveryTestCase):
    """The conversation, its state machine, and its guard."""

    def test_01_a_sparse_goal_asks_a_few_short_practical_questions(self) -> None:
        session = self.discovery.start(SPARSE_GOAL)

        self.assertEqual(session.state, DiscoveryState.NEEDS_CLARIFICATION)
        self.assertEqual(session.initial_goal, SPARSE_GOAL)
        # One to six questions, never an interrogation.
        self.assertGreaterEqual(len(session.questions), 1)
        self.assertLessEqual(len(session.questions), 6)
        ids = [question.id for question in session.questions]
        self.assertEqual(len(ids), len(set(ids)), "question ids must be unique")
        for question in session.questions:
            self.assertTrue(question.text.strip())
            self.assertTrue(question.why.strip())
            self.assertIn(question.field, {
                "goal", "project_type", "users", "core_features", "platform",
                "integrations", "data_storage", "auth", "notifications",
                "constraints", "acceptance_criteria", "priorities",
            })
        # The goal itself is known, so it is not asked about.
        self.assertNotIn("q-goal", ids)
        # And an essential area is asked with required=True.
        self.assertTrue(any(question.required for question in session.questions))

    def test_02_a_goal_that_answers_everything_goes_straight_to_ready(self) -> None:
        session = self.discovery.start(COMPLETE_GOAL)

        self.assertEqual(session.state, DiscoveryState.READY_FOR_PLANNING)
        self.assertEqual(session.questions, ())
        specification = self.discovery.specification(session.session_id)
        self.assertEqual(specification.project_type, "система бронирования")
        self.assertEqual(specification.platform, "веб-сайт")
        self.assertIn("гости", specification.users)
        self.assertIn("бронирование", specification.core_features)
        self.assertIn("платежи", specification.integrations)
        self.assertEqual(specification.data_storage, "PostgreSQL")
        self.assertTrue(specification.auth)
        self.assertTrue(specification.notifications)
        self.assertTrue(specification.constraints)
        self.assertTrue(specification.acceptance_criteria)
        self.assertTrue(specification.priorities)
        # Being ready is not being confirmed: execution is still refused.
        with self.assertRaises(DiscoveryNotConfirmed):
            self.discovery.require_confirmed(session.session_id)

    def test_03_answering_fills_the_specification_and_moves_state(self) -> None:
        session = self.discovery.start(HOTEL_GOAL)
        self.assertEqual(session.state, DiscoveryState.NEEDS_CLARIFICATION)
        self.assertIn("q-users", [question.id for question in session.questions])
        # The platform and project type are derived, so they are not asked.
        self.assertNotIn("q-platform", [question.id for question in session.questions])

        session = self._answer_all(
            session,
            {
                "users": "гости отеля и администратор стойки",
                "integrations": "оплата картой, email",
                "data_storage": "PostgreSQL",
                "auth": "вход по email для администратора",
                "notifications": "подтверждение брони по email",
                "constraints": "бюджет ограничен, срок 6 недель",
            },
        )

        self.assertEqual(session.state, DiscoveryState.READY_FOR_PLANNING)
        specification = self.discovery.specification(session.session_id)
        self.assertEqual(specification.users, "гости отеля и администратор стойки")
        self.assertEqual(
            specification.integrations, ("оплата картой", "email")
        )
        self.assertEqual(specification.data_storage, "PostgreSQL")
        self.assertEqual(
            specification.auth, "вход по email для администратора"
        )
        self.assertEqual(
            specification.constraints, ("бюджет ограничен", "срок 6 недель")
        )
        self.assertIn("users", specification.source_answers)
        # Answering again replaces rather than duplicates.
        session = self.discovery.answer(
            session.session_id,
            [{"question_id": "q-users", "text": "только гости"}],
        )
        self.assertEqual(
            self.discovery.specification(session.session_id).users, "только гости"
        )
        stored = [a for a in session.answers if a.question_id == "q-users"]
        self.assertEqual(len(stored), 1)

    def test_04_an_unknown_question_is_refused(self) -> None:
        session = self.discovery.start(HOTEL_GOAL)
        with self.assertRaises(InvalidClarificationAnswer):
            self.discovery.answer(
                session.session_id,
                [{"question_id": "q-not-a-field", "text": "что-нибудь"}],
            )

    def test_05_skip_is_recorded_and_still_requires_confirmation(self) -> None:
        session = self.discovery.start(HOTEL_GOAL)
        session = self.discovery.skip(session.session_id, "потом уточню")

        self.assertEqual(session.state, DiscoveryState.READY_FOR_PLANNING)
        self.assertTrue(session.skipped)
        self.assertEqual(session.skip_reason, "потом уточню")
        # The skip is NOT a confirmation: the guard still refuses.
        with self.assertRaises(DiscoveryNotConfirmed):
            self.discovery.require_confirmed(session.session_id)

        session = self.discovery.confirm(session.session_id)
        self.assertEqual(session.state, DiscoveryState.CONFIRMED)
        self.assertTrue(session.skipped)
        specification = self.discovery.require_confirmed(session.session_id)
        self.assertEqual(specification.goal, HOTEL_GOAL)
        # What the goal itself stated is still in the specification after a skip.
        self.assertEqual(specification.platform, "веб-сайт")

    def test_06_confirm_and_cancel_transitions(self) -> None:
        # Confirmation before the questions are answered is refused.
        session = self.discovery.start(HOTEL_GOAL)
        with self.assertRaises(DiscoveryNotReady):
            self.discovery.confirm(session.session_id)

        # Cancel is available, and final.
        cancelled = self.discovery.cancel(session.session_id)
        self.assertEqual(cancelled.state, DiscoveryState.CANCELLED)
        self.assertEqual(
            self.discovery.cancel(session.session_id).state,
            DiscoveryState.CANCELLED,
        )
        with self.assertRaises(DiscoveryStateConflict):
            self.discovery.confirm(session.session_id)
        with self.assertRaises(DiscoveryStateConflict):
            self.discovery.answer(
                session.session_id, [{"question_id": "q-users", "text": "x"}]
            )
        with self.assertRaises(DiscoveryNotConfirmed):
            self.discovery.require_confirmed(session.session_id)

        # A ready session can be confirmed, confirm is idempotent, and a confirmed
        # session cannot be cancelled or re-answered.
        ready = self._answer_all(self.discovery.start(SPARSE_GOAL))
        confirmed = self.discovery.confirm(ready.session_id)
        self.assertEqual(confirmed.state, DiscoveryState.CONFIRMED)
        self.assertEqual(
            self.discovery.confirm(ready.session_id).state,
            DiscoveryState.CONFIRMED,
        )
        with self.assertRaises(DiscoveryStateConflict):
            self.discovery.cancel(ready.session_id)
        with self.assertRaises(DiscoveryStateConflict):
            self.discovery.answer(
                ready.session_id, [{"question_id": "q-users", "text": "x"}]
            )

    def test_07_require_confirmed_refuses_execution_before_confirmation(self) -> None:
        session = self.discovery.start(HOTEL_GOAL)
        with self.assertRaises(DiscoveryNotConfirmed):
            self.discovery.require_confirmed(session.session_id)
        with self.assertRaises(UnknownDiscoverySession):
            self.discovery.require_confirmed("disc-does-not-exist")
        with self.assertRaises(UnknownDiscoverySession):
            self.discovery.require_confirmed("../../etc/passwd")

    def test_08_a_secret_shaped_answer_is_redacted_and_never_stored(self) -> None:
        secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
        session = self.discovery.start(HOTEL_GOAL)
        session = self.discovery.answer(
            session.session_id,
            [
                {"question_id": "q-auth", "text": f"ключ {secret}"},
                {"question_id": "q-users", "text": "гости отеля"},
            ],
        )

        redacted = [a for a in session.answers if a.question_id == "q-auth"]
        self.assertEqual(len(redacted), 1)
        self.assertTrue(redacted[0].redacted)
        self.assertIn("[REDACTED", redacted[0].text)

        # The value never reaches the specification, the file, or the API view.
        specification = self.discovery.specification(session.session_id)
        self.assertEqual(specification.auth, "")
        file_text = self._session_file_text(session.session_id)
        self.assertNotIn(secret, file_text)
        self.assertIn("[REDACTED", file_text)
        self.assertNotIn(secret, json.dumps(session.to_dict(), ensure_ascii=False))
        # A redacted answer carries no information, so the area is askable again.
        self.assertIn("q-auth", [question.id for question in session.questions])

    def test_09_the_specification_is_deterministic(self) -> None:
        first = self.discovery.start(COMPLETE_GOAL)
        second = self.discovery.start(COMPLETE_GOAL)
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(
            first.specification.to_dict(), second.specification.to_dict()
        )
        self.assertEqual(
            first.questions, second.questions
        )


class DiscoveryApiTests(_DiscoveryTestCase):
    """The HTTP surface and the hand-off into the existing task path."""

    def _api_client(self, service: ForgeApiService):
        from fastapi.testclient import TestClient

        from app.projects.service import ProjectService
        from app.projects.store import ProjectStore

        # Confirming a session now also materialises its persistent project record,
        # so this test redirects that store exactly as it already redirects the
        # discovery and run stores: a test must never write into the deployment's
        # own project store.
        app = create_api_app(
            service,
            discovery_service=self.discovery,
            project_service=ProjectService(ProjectStore(root=self.base / "projects")),
        )
        return TestClient(app)

    def _offline_service(self) -> ForgeApiService:
        return ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.run_root),
            authorized_project_id="discovery-test-project",
        )

    def _recording_service(self, provider: Provider) -> ForgeApiService:
        from app.config.settings import RuntimeSettings
        from app.runtime.bootstrap import create_runtime

        runtime = create_runtime(
            settings=RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock",),
                provider_fallback_chain=(),
            )
        )
        runtime.agent_registry._agents["deepseek"] = ProviderAgent(provider)
        return ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.run_root),
            authorized_project_id="discovery-test-project",
        )

    # ------------------------------------------------------------------ routes
    def test_10_the_http_routes_expose_the_conversation(self) -> None:
        client = self._api_client(self._offline_service())

        created = client.post("/api/discovery/sessions", json={"goal": HOTEL_GOAL})
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        session_id = body["session"]["session_id"]
        self.assertEqual(body["session"]["state"], "NEEDS_CLARIFICATION")
        self.assertEqual(
            [question["field"] for question in body["questions"]],
            [question["field"] for question in body["session"]["questions"]],
        )

        fetched = client.get(f"/api/discovery/sessions/{session_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["session"]["initial_goal"], HOTEL_GOAL)

        answers = [
            {"question_id": question["id"], "text": "ответ"}
            for question in body["questions"]
        ]
        answered = client.post(
            f"/api/discovery/sessions/{session_id}/answers",
            json={"answers": answers},
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(answered.json()["session"]["state"], "READY_FOR_PLANNING")

        specification = client.get(
            f"/api/discovery/sessions/{session_id}/specification"
        )
        self.assertEqual(specification.status_code, 200, specification.text)
        self.assertIn("PROJECT SPECIFICATION", specification.json()["task_text"])

        confirmed = client.post(f"/api/discovery/sessions/{session_id}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["session"]["state"], "CONFIRMED")

        unknown = client.get("/api/discovery/sessions/disc-missing")
        self.assertEqual(unknown.status_code, 404, unknown.text)

        # skip is reachable with no body at all, and it is not a confirmation.
        second = client.post(
            "/api/discovery/sessions", json={"goal": HOTEL_GOAL}
        ).json()["session"]["session_id"]
        skipped = client.post(f"/api/discovery/sessions/{second}/skip")
        self.assertEqual(skipped.status_code, 200, skipped.text)
        self.assertTrue(skipped.json()["session"]["skipped"])
        self.assertEqual(
            skipped.json()["session"]["state"], "READY_FOR_PLANNING"
        )

    def test_11_an_unconfirmed_session_refuses_the_run_and_a_confirmed_one_runs(
        self,
    ) -> None:
        provider = _RecordingProvider()
        client = self._api_client(self._recording_service(provider))
        session = self.discovery.start(HOTEL_GOAL)

        refused = client.post(
            "/api/tasks/run",
            json={
                "description": "Сделай первую версию",
                "category": "analysis",
                "provider_name": "deepseek",
                "discovery_session_id": session.session_id,
            },
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(
            refused.json()["detail"]["code"], "discovery_not_confirmed"
        )
        self.assertFalse(refused.json()["detail"]["executed"])
        self.assertEqual(
            provider.requests, [], "an unconfirmed session reached the provider"
        )

        # The user skips the remaining questions and confirms: now it may run.
        self.discovery.skip(session.session_id, "достаточно")
        self.discovery.confirm(session.session_id)
        accepted = client.post(
            "/api/tasks/run",
            json={
                "description": "Сделай первую версию",
                "category": "analysis",
                "provider_name": "deepseek",
                "model_name": "deepseek-chat",
                "discovery_session_id": session.session_id,
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertTrue(accepted.json()["success"], accepted.text)
        self.assertEqual(len(provider.requests), 1)
        # Explicit provider and model selection is preserved unchanged.
        self.assertEqual(accepted.json()["provider_used"], "deepseek")
        self.assertEqual(accepted.json()["model_used"], "deepseek-chat")

    def test_12_the_specification_text_reaches_the_task_description(self) -> None:
        provider = _RecordingProvider()
        client = self._api_client(self._recording_service(provider))

        session = self.discovery.start(HOTEL_GOAL)
        session = self._answer_all(
            session,
            {
                "users": "гости отеля",
                "integrations": "оплата картой, email",
                "data_storage": "PostgreSQL",
                "auth": "вход по email для администратора",
                "notifications": "подтверждение брони по email",
                "constraints": "бюджет ограничен",
            },
        )
        self.discovery.confirm(session.session_id)

        response = client.post(
            "/api/tasks/run",
            json={
                "description": "Опиши первый шаг реализации",
                "category": "analysis",
                "provider_name": "deepseek",
                "model_name": "deepseek-chat",
                "discovery_session_id": session.session_id,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(provider.requests), 1)
        prompt = provider.requests[0].prompt

        # The specification is prepended, the caller's own request is preserved
        # after it, and both are the one task description the existing path got.
        self.assertTrue(prompt.startswith("PROJECT SPECIFICATION"), prompt)
        self.assertIn("Goal:", prompt)
        self.assertIn("Project type: система бронирования", prompt)
        self.assertIn("Users: гости отеля", prompt)
        self.assertIn("Опиши первый шаг реализации", prompt)
        # The model selection still wins over the configured default.
        self.assertEqual(provider.requests[0].model_name, "deepseek-chat")

    def test_13_a_cancelled_or_unknown_session_refuses_the_run(self) -> None:
        provider = _RecordingProvider()
        client = self._api_client(self._recording_service(provider))

        session = self.discovery.start(HOTEL_GOAL)
        self.discovery.cancel(session.session_id)
        cancelled = client.post(
            "/api/tasks/run",
            json={
                "description": "run it",
                "category": "analysis",
                "provider_name": "deepseek",
                "discovery_session_id": session.session_id,
            },
        )
        self.assertEqual(cancelled.status_code, 409, cancelled.text)

        unknown = client.post(
            "/api/tasks/run",
            json={
                "description": "run it",
                "category": "analysis",
                "provider_name": "deepseek",
                "discovery_session_id": "disc-does-not-exist",
            },
        )
        self.assertEqual(unknown.status_code, 404, unknown.text)
        self.assertEqual(provider.requests, [])

    def test_14_a_run_without_a_session_is_unchanged(self) -> None:
        provider = _RecordingProvider()
        client = self._api_client(self._recording_service(provider))

        response = client.post(
            "/api/tasks/run",
            json={
                "description": "Обычная задача без discovery",
                "category": "analysis",
                "provider_name": "deepseek",
                "model_name": "deepseek-chat",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(provider.requests[0].prompt, "Обычная задача без discovery")

    def test_15_the_offline_task_path_also_runs_a_confirmed_specification(
        self,
    ) -> None:
        client = self._api_client(self._offline_service())
        session = self.discovery.start(HOTEL_GOAL)
        self.discovery.skip(session.session_id)
        self.discovery.confirm(session.session_id)

        response = client.post(
            "/api/tasks/run",
            json={
                "description": "перечислить содержимое корня репозитория",
                "category": "code",
                "discovery_session_id": session.session_id,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["state"], "COMPLETED", body)
        self.assertTrue(body["success"], body)
        # The existing offline path reports its own loop and received both texts.
        self.assertIn("OFFLINE TASK EXECUTION", body["output"])
        self.assertIn("PROJECT SPECIFICATION", body["output"])
        self.assertIn("перечислить содержимое корня репозитория", body["output"])


if __name__ == "__main__":
    unittest.main()
