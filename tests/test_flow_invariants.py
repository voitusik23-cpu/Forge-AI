"""The five cheap invariants that catch a shipped bug, offline and pure where possible.

The owner's critique: 3000+ tests passed while two minutes of clicking found a
clarification loop that never ended, a dead «Открыть» that showed nothing, and
``COMPLETED`` on a zero-file run. The cause was that no test asserted the rule a
person can feel. This file is the rule set, one invariant per historical failure:

1. **No repeated screen.** Two consecutive runs must never present the same
   normalised question set. Catches the shipped loop: the same two questions came
   back forever after the owner answered them.
2. **Bounded rounds.** A clarification chain terminates in at most
   ``MAX_CLARIFICATION_ROUNDS`` (2) rounds and ends in a terminal affordance that is
   not «Ответить». Catches the loop again, from the other side: the card kept
   offering a way to answer with nothing left to answer.
3. **No dead action.** Every action the presentation layer offers must map to a
   route or a state change that exists. Catches the dead «Открыть»: the button
   switched screens and selected nothing, so the click was indistinguishable from
   nothing happening.
4. **No false completion.** A terminal state may never claim the project is finished
   unless the files exist and are verified. Catches ``COMPLETED`` on a zero-file
   run, asserted through the owner-facing card rather than the run's own word.
5. **Progress monotonicity.** The visible stage never goes backwards within one
   run. Catches the card that showed «Файлы записаны» after it had shown the files
   were gone -- the shape that makes a user distrust every later step.

Everything here is offline. Invariant 1 and 4 exercise the real service and the real
HTTP surface; the fake provider is installed **at composition time**
(``runtime.agent_registry`` before ``ForgeApiService`` is constructed), because that
constructor rebuilds the executor and a fake executor patched in afterwards is
discarded -- the mistake that once caused a real call. Invariants 2, 3 and 5 are pure
functions: the clarification counters, the presentation layer, the route table and the
card rule executed from ``ui/src/forge-result.ts`` itself with Node.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

from app.agent_runtime.clarification import (
    MAX_CLARIFICATION_ROUNDS,
    ClarificationAnswer,
    next_round,
    normalise_question,
    round_is_final,
    unanswered_questions,
)
from app.agent_runtime.outcome import RunOutcome, derive_outcome, honest_state
from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.presentation import ACTIONS, build_project_presentation
from app.projects.service import ProjectService
from app.projects.stage import (
    COMPLETED as STAGE_COMPLETED,
    FAILED as STAGE_FAILED,
    FILES_CREATED,
    NEEDS_CHANGES as STAGE_NEEDS_CHANGES,
    PREVIEW_AVAILABLE,
    PROJECT_NOT_CREATED,
    READY_TO_RUN,
    RUNNING as STAGE_RUNNING,
    VERIFIED,
    VERIFICATION_RUNNING,
    project_stage,
)
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
CARD_MODULE = REPOSITORY / "ui" / "src" / "forge-result.ts"
UI_SOURCE = REPOSITORY / "ui" / "src"
APP_TSX = UI_SOURCE / "App.tsx"
FORGE_TSX = UI_SOURCE / "screens" / "Forge.tsx"
PROJECTS_TSX = UI_SOURCE / "screens" / "Projects.tsx"
I18N = UI_SOURCE / "i18n.ts"
FORGE_CLIENT = UI_SOURCE / "api" / "forge.ts"
#: The `POST /api/tasks/run` body is composed here and `dispatchTask` calls it, so a
#: target the client "calls" may be named in either module. Both are searched, and the
#: client is separately required to use the builder -- otherwise the builder could be
#: dead code and the actions with it.
TASK_RUN_BODY = UI_SOURCE / "task-run-body.ts"

WORKSPACE_PROJECT_ID = "flow-invariants-workspace"
GOAL = "Хочу небольшой локальный справочник сотрудников с поиском по имени."

QUESTION_ONE = "Сколько человек будет пользоваться справочником одновременно?"
QUESTION_TWO = "Нужна ли авторизация или доступ без входа?"

CLARIFY_ANSWER = json.dumps(
    {
        "intent_type": "CLARIFICATION_REQUIRED",
        "summary": "Two answers are needed before any file can be written.",
        "questions": [QUESTION_ONE, QUESTION_TWO],
    },
    ensure_ascii=False,
)

TEXT_ANSWER = "Проект описан в плане: разделы, поля и поиск. Файлы пока не созданы."


# --------------------------------------------------------------------------- #
# the card rule, executed from its own TypeScript source
# --------------------------------------------------------------------------- #


def _node(script: str) -> str:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - node is present on this host
        raise unittest.SkipTest("node is not installed; the UI rule cannot be executed")
    completed = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=str(REPOSITORY),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout


def card_of(*, files: int, questions, needs_clarification: bool, round_number: int,
            write_status: str = "", verification: str = "", error: str = "",
            write_requested: bool = False) -> dict:
    """The real headline and actions for one set of facts."""

    facts = {
        "filesCreated": int(files),
        "writeStatus": write_status,
        "verification": verification,
        "writeReason": "",
        "questions": list(questions or ()),
        "needsClarification": bool(needs_clarification),
        "clarificationRound": int(round_number),
        "error": error,
        "writeRequested": bool(write_requested),
    }
    script = (
        f"import {{ deriveRunHeadline }} from {json.dumps(CARD_MODULE.as_uri())};"
        "process.stdout.write(JSON.stringify(deriveRunHeadline('ru', "
        + json.dumps(facts)
        + ")));"
    )
    return json.loads(_node(script))


# --------------------------------------------------------------------------- #
# the real application over a throwaway store and a fake provider
# --------------------------------------------------------------------------- #


class _ScriptedProvider(Provider):
    """One canned answer per call; the last repeats. No socket, no key, no cost."""

    def __init__(self, answer: str, *, name: str = "deepseek", model: str = "deepseek-chat") -> None:
        super().__init__(ProviderConfig(provider_name=name, model_name=model))
        self.answer = answer
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=request.model_name or self.model_name,
            output=self.answer,
        )


class _AppCase(unittest.TestCase):
    """A real app over temporary stores, with the fake provider composed in first."""

    def setUp(self) -> None:
        self.base = pathlib.Path(
            tempfile.mkdtemp(prefix="flow-invariants-", dir=str(REPOSITORY / ".forge"))
        )
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.root = self.base / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.base / "workspace").mkdir(parents=True, exist_ok=True)
        (self.base / "runs").mkdir(parents=True, exist_ok=True)
        previous = os.environ.get(PROJECTS_ROOT_ENV_VAR)
        os.environ[PROJECTS_ROOT_ENV_VAR] = str(self.root)
        self.addCleanup(self._restore_projects_root, previous)
        self.projects = ProjectService(ProjectStore(root=self.root))
        self.discovery = DiscoveryService(DiscoveryStore(root=self.base / "discovery"))

    def _restore_projects_root(self, previous) -> None:
        if previous is None:
            os.environ.pop(PROJECTS_ROOT_ENV_VAR, None)
        else:
            os.environ[PROJECTS_ROOT_ENV_VAR] = previous

    def compose(self, answer: str):
        """Build the real app, installing the fake provider at composition time."""

        runtime = offline_runtime()
        fake = _ScriptedProvider(answer)
        # BEFORE the service exists: ForgeApiService.__init__ rebuilds the executor, so
        # a fake executor handed in here would be discarded and the real provider left
        # in place. The agent registry is the seam that survives.
        runtime.agent_registry._agents[fake.provider_name] = ProviderAgent(fake)
        service = ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.base / "runs"),
            workspace=Workspace(self.base / "workspace"),
            authorized_project_id=WORKSPACE_PROJECT_ID,
        )
        from fastapi.testclient import TestClient

        self.app = create_api_app(
            service,
            discovery_service=self.discovery,
            project_service=self.projects,
        )
        self.addCleanup(self._stop_everything)
        return TestClient(self.app), fake

    def _stop_everything(self) -> None:
        app = getattr(self, "app", None)
        state = getattr(app, "state", None)
        for name in ("forge_runner", "forge_previews"):
            target = getattr(state, name, None)
            if target is not None:
                try:
                    target.stop_all()
                except Exception:  # noqa: BLE001 - shutdown is best effort
                    pass

    # ---------------------------------------------------------------- helpers
    def dispatch(self, client, *, write_mode: bool = False, answers=None, run_id: str = ""):
        payload: dict = {
            "description": GOAL,
            "category": "code" if write_mode else "analysis",
            "provider_name": "deepseek",
            "model_name": "deepseek-chat",
        }
        if write_mode:
            payload["write_mode"] = True
        if answers:
            payload["clarification_answers"] = [
                {"question": question, "answer": answer} for question, answer in answers
            ]
            payload["clarification_run_id"] = run_id
        return client.post("/api/tasks/run", json=payload)

    def confirmed_session(self, client) -> str:
        """A confirmed discovery session with a durable project record."""

        opened = client.post("/api/discovery/sessions", json={"goal": GOAL}).json()
        session_id = str(opened["session"]["session_id"])
        answers = []
        for question in opened["questions"]:
            options = list(question.get("options") or ())
            item: dict = {"question_id": str(question["id"])}
            if options:
                item["values"] = [str(options[0]["value"])]
                item["text"] = str(options[0]["label"])
            else:
                item["text"] = "Достаточно простого варианта без внешних сервисов."
            answers.append(item)
        client.post(f"/api/discovery/sessions/{session_id}/answers", json={"answers": answers})
        session = client.get(f"/api/discovery/sessions/{session_id}").json()["session"]
        if session["state"] == "NEEDS_CLARIFICATION":
            client.post(f"/api/discovery/sessions/{session_id}/skip", json={"reason": "некритично"})
        confirmed = client.post(f"/api/discovery/sessions/{session_id}/confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        return session_id


# --------------------------------------------------------------------------- #
# 1. no repeated screen
# --------------------------------------------------------------------------- #


class NoRepeatedScreenTests(_AppCase):
    """Two consecutive runs never present the same normalised question set.

    The shipped sequence: run 1 asks Q1/Q2; the owner answers both; run 2 came back
    with Q1/Q2 again, so answering changed nothing. Here the same words are re-asked
    on purpose, and the rule that stops the screen from repeating is what is asserted.
    """

    def _presented(self, response: dict) -> tuple:
        """The question set the card would put in front of the user."""

        return tuple(
            sorted(normalise_question(item) for item in response.get("clarification_questions") or ())
        )

    def test_01_a_second_run_never_asks_the_questions_the_first_one_asked(self) -> None:
        client, fake = self.compose(CLARIFY_ANSWER)

        first = self.dispatch(client)
        self.assertEqual(first.status_code, 200, first.text)
        run_one = first.json()
        self.assertTrue(self._presented(run_one), "the first run asked nothing")
        self.assertEqual(len(self._presented(run_one)), 2)
        self.assertTrue(run_one["needs_clarification"])
        self.assertEqual(int(run_one["clarification_round"]), 1)
        card_one = card_of(
            files=0,
            questions=run_one["clarification_questions"],
            needs_clarification=True,
            round_number=1,
        )
        self.assertEqual(card_one["kind"], "questions")
        self.assertIn("answer", card_one["actions"])

        # The owner answers in his own words, through the payload the UI sends.
        second = self.dispatch(
            client,
            answers=[
                (QUESTION_ONE, "Один-два человека, не больше."),
                (QUESTION_TWO, "Без авторизации, локальный доступ."),
            ],
            run_id=run_one["run_id"],
        )
        self.assertEqual(second.status_code, 200, second.text)
        run_two = second.json()
        self.assertEqual(len(fake.requests), 2, "the second dispatch did not reach the provider")
        self.assertEqual(int(run_two["clarification_round"]), 2)

        # THE INVARIANT: the set the user sees is not the set they just answered.
        self.assertNotEqual(
            self._presented(run_two),
            self._presented(run_one),
            "the second run presented the same questions as the first",
        )
        self.assertEqual(
            self._presented(run_two),
            (),
            "an answered question was presented again",
        )
        card_two = card_of(
            files=0,
            questions=run_two["clarification_questions"],
            needs_clarification=bool(run_two["needs_clarification"]),
            round_number=int(run_two["clarification_round"]),
        )
        self.assertNotIn("answer", card_two["actions"], card_two)
        self.assertEqual(card_two["kind"], "questions_exhausted")

        # A third dispatch of the same context is the same screen as the second: it is
        # the terminal card, not another round. The visible set never repeats a *new*
        # question set, and the chain has no round 3.
        third = self.dispatch(
            client,
            answers=[
                (QUESTION_ONE, "Один-два человека, не больше."),
                (QUESTION_TWO, "Без авторизации, локальный доступ."),
            ],
            run_id=run_two["run_id"],
        )
        self.assertEqual(third.status_code, 200, third.text)
        run_three = third.json()
        self.assertEqual(int(run_three["clarification_round"]), MAX_CLARIFICATION_ROUNDS)
        self.assertEqual(self._presented(run_three), ())
        card_three = card_of(
            files=0,
            questions=run_three["clarification_questions"],
            needs_clarification=bool(run_three["needs_clarification"]),
            round_number=int(run_three["clarification_round"]),
        )
        self.assertNotIn("answer", card_three["actions"], card_three)
        self.assertIn("retry", card_three["actions"], card_three)

    def test_02_the_comparison_is_normalisation_not_string_equality(self) -> None:
        # A re-worded question is the same question: edge punctuation, case, a list
        # marker, a leading filler and ё/е are ignored. This is the rule that makes
        # the invariant above hold for a model that repeats itself in other words.
        self.assertEqual(
            normalise_question("  Уточните, пожалуйста: нужна ли авторизация? "),
            normalise_question("Нужна ли авторизация"),
        )
        self.assertEqual(
            normalise_question("1) Нужна ли авторизация?"),
            normalise_question("нужна ли АВТОРИЗАЦИЯ!"),
        )
        self.assertNotEqual(
            normalise_question("Нужна ли авторизация?"),
            normalise_question("Нужны ли уведомления?"),
        )
        answered = (ClarificationAnswer(question="Нужна ли авторизация?", answer="Нет"),)
        self.assertEqual(
            unanswered_questions(["Уточните, пожалуйста: нужна ли авторизация"], answered),
            (),
            "a re-worded but identical question was not recognised as answered",
        )


# --------------------------------------------------------------------------- #
# 2. bounded rounds
# --------------------------------------------------------------------------- #


class BoundedRoundsTests(unittest.TestCase):
    """The chain is finite by construction, and it ends without «Ответить»."""

    def test_01_the_python_counter_terminates_in_two_rounds(self) -> None:
        self.assertEqual(MAX_CLARIFICATION_ROUNDS, 2)
        self.assertFalse(round_is_final(1))
        self.assertTrue(round_is_final(2))

        previous = 0
        rounds = []
        for _ in range(10):
            previous = next_round(previous, has_answers=True)
            rounds.append(previous)
            self.assertLessEqual(previous, MAX_CLARIFICATION_ROUNDS)
        self.assertEqual(rounds, [MAX_CLARIFICATION_ROUNDS] * 10)
        # A dispatch with no answers is round 1, and a round 2 never reopens round 3.
        self.assertEqual(next_round(0, has_answers=False), 1)
        self.assertEqual(next_round(2, has_answers=True), MAX_CLARIFICATION_ROUNDS)
        # A dispatch that already belongs to the final round stays there: the counter
        # never walks back, so a retry cannot reopen the loop.
        self.assertEqual(next_round(2, has_answers=False), MAX_CLARIFICATION_ROUNDS)

    def test_02_the_ts_mirror_of_the_bound_equals_the_backend(self) -> None:
        source = CARD_MODULE.read_text(encoding="utf-8")
        match = re.search(
            r"export const MAX_CLARIFICATION_ROUNDS = (\d+);", source
        )
        self.assertIsNotNone(match, "the card no longer mirrors the round bound")
        self.assertEqual(int(match.group(1)), MAX_CLARIFICATION_ROUNDS)

    def test_03_the_terminal_round_offers_a_terminal_affordance_not_answer(self) -> None:
        # Round 1 may ask and offers «Ответить».
        round_one = card_of(files=0, questions=[QUESTION_ONE, QUESTION_TWO],
                            needs_clarification=True, round_number=1)
        self.assertEqual(round_one["kind"], "questions")
        self.assertIn("answer", round_one["actions"])

        # Round 2 is the last one, even when the model repeats questions verbatim:
        # there is nothing left to answer into, so the affordance is terminal.
        round_two = card_of(files=0, questions=[QUESTION_ONE, QUESTION_TWO],
                            needs_clarification=True, round_number=2)
        self.assertEqual(round_two["kind"], "questions_exhausted")
        self.assertNotIn("answer", round_two["actions"])
        self.assertEqual(round_two["actions"], ["retry"])
        self.assertTrue(round_two["actions"], "the terminal card offers nothing at all")

        # And the terminal state is a *state*, not a guess about the wording: no
        # questions at all on round 2 is still the same terminal card.
        empty_round_two = card_of(files=0, questions=[], needs_clarification=False,
                                  round_number=2)
        self.assertEqual(empty_round_two["kind"], "questions_exhausted")
        self.assertNotIn("answer", empty_round_two["actions"])

    def test_04_a_run_that_asked_can_never_publish_a_third_round(self) -> None:
        # The response publishes the round; the loop's own counter caps it at 2 no
        # matter how many times the same context is retried.
        self.assertEqual(next_round(1, has_answers=True), 2)
        for previous in (2, 3, 99):
            self.assertEqual(next_round(previous, has_answers=True), MAX_CLARIFICATION_ROUNDS)


# --------------------------------------------------------------------------- #
# 3. no dead action
# --------------------------------------------------------------------------- #

#: Every action id the presentation layer may offer -> the route or screen that
#: implements it. ``route`` is checked against the real FastAPI route table; ``screen``
#: against the navigation table in ``App.tsx``; ``ui_path`` is the string the client
#: must call, so a removed/renamed endpoint fails here instead of on the owner's click.
ACTION_TARGETS: dict[str, dict] = {
    # app/projects/presentation.py::ACTIONS (the project card)
    "continue_creation": {
        "route": ("POST", "/api/tasks/run"),
        "ui_path": "/api/tasks/run",
    },
    "answer_questions": {
        "route": ("POST", "/api/discovery/sessions/{session_id}/answers"),
        "ui_path": "/answers",
        "screen": "forge",
    },
    "add_feature": {
        "route": ("POST", "/api/projects/{project_id}/versions"),
        "ui_path": "/versions",
        "screen": "projects",
    },
    "new_version": {
        "route": ("POST", "/api/projects/{project_id}/versions"),
        "ui_path": "/versions",
        "screen": "projects",
    },
    "view_files": {
        "route": ("GET", "/api/projects/{project_id}/files"),
        "ui_path": "/files",
        "screen": "projects",
    },
    "reverify": {
        "route": ("POST", "/api/projects/{project_id}/verify"),
        "ui_path": "/verify",
        "screen": "projects",
    },
    "launch_local": {
        "route": ("POST", "/api/projects/{project_id}/run"),
        "ui_path": "/run",
        "screen": "projects",
    },
    "open_app": {
        "route": ("POST", "/api/projects/{project_id}/preview"),
        "ui_path": "/preview",
        "screen": "projects",
    },
    # The owner's «вход в проект»: three real OS integrations, and the delivery of an
    # existing project into the visible folder. They are rendered by the card's own
    # «Проект на диске» block (beside the folder path) rather than by the generic action
    # row, so `_actions` never emits them -- but they are part of the same closed
    # vocabulary, and a route that disappears must fail here rather than on the click.
    "open_folder": {
        "route": ("POST", "/api/projects/{project_id}/open"),
        "ui_path": "/open",
        "screen": "projects",
    },
    "open_editor": {
        "route": ("POST", "/api/projects/{project_id}/open"),
        "ui_path": "/open",
        "screen": "projects",
    },
    "open_terminal": {
        "route": ("POST", "/api/projects/{project_id}/open"),
        "ui_path": "/open",
        "screen": "projects",
    },
    "move_to_visible": {
        "route": ("POST", "/api/projects/{project_id}/move-to-visible-folder"),
        "ui_path": "/move-to-visible-folder",
        "screen": "projects",
    },
    # ui/src/forge-result.ts::ResultActionId (the run result card)
    "open_files": {
        "route": ("GET", "/api/projects/{project_id}/files"),
        "ui_path": "/files",
        "screen": "projects",
    },
    "verify": {
        "route": ("POST", "/api/projects/{project_id}/verify"),
        "ui_path": "/verify",
        "screen": "projects",
    },
    "answer": {
        "route": ("POST", "/api/tasks/run"),
        "ui_path": "clarification_answers",
        "screen": "forge",
    },
    "retry": {
        "route": ("POST", "/api/tasks/run"),
        "ui_path": "/api/tasks/run",
        "screen": "forge",
    },
}

_NAV_SCREENS = (
    "forge",
    "overview",
    "projects",
    "providers",
    "agents",
    "models",
    "runs",
    "tools",
    "usage",
    "settings",
    "help",
)


def _route_table() -> set[tuple[str, str]]:
    """The real (method, path) pairs ``create_api_app`` publishes."""

    from fastapi.testclient import TestClient  # noqa: F401 - the app needs no client

    from app.api.server import create_api_app as build

    base = pathlib.Path(tempfile.mkdtemp(prefix="route-table-", dir=str(REPOSITORY / ".forge")))
    (base / "workspace").mkdir(parents=True, exist_ok=True)
    (base / "runs").mkdir(parents=True, exist_ok=True)
    (base / "projects").mkdir(parents=True, exist_ok=True)
    (base / "discovery").mkdir(parents=True, exist_ok=True)
    try:
        runtime = offline_runtime()
        service = ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=base / "runs"),
            workspace=Workspace(base / "workspace"),
            authorized_project_id=WORKSPACE_PROJECT_ID,
        )
        app = build(
            service,
            discovery_service=DiscoveryService(DiscoveryStore(root=base / "discovery")),
            project_service=ProjectService(ProjectStore(root=base / "projects")),
        )
        table: set[tuple[str, str]] = set()
        for route in app.routes:
            methods = getattr(route, "methods", None) or ()
            path = str(getattr(route, "path", "") or "")
            for method in methods:
                if method in ("HEAD", "OPTIONS"):
                    continue
                table.add((str(method).upper(), path))
        return table
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _ts_result_action_ids() -> tuple[str, ...]:
    """The ``ResultActionId`` union, read from the production TypeScript source."""

    source = CARD_MODULE.read_text(encoding="utf-8")
    match = re.search(r"export type ResultActionId =([^;]+);", source)
    if match is None:
        raise AssertionError("ui/src/forge-result.ts no longer declares ResultActionId")
    return tuple(re.findall(r'"([a-z_]+)"', match.group(1)))


def _ts_result_action_labels() -> tuple[str, ...]:
    """The keys of ``RESULT_ACTION_LABELS`` in Forge.tsx."""

    source = FORGE_TSX.read_text(encoding="utf-8")
    match = re.search(r"const RESULT_ACTION_LABELS[^=]*=\s*\{(.*?)\};", source, re.S)
    if match is None:
        raise AssertionError("Forge.tsx no longer declares RESULT_ACTION_LABELS")
    return tuple(re.findall(r"^\s*([a-z_]+):", match.group(1), re.M))


def _ui_sources() -> dict[pathlib.Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in sorted(UI_SOURCE.rglob("*"))
        if path.suffix in (".ts", ".tsx") and path.is_file()
    }


class NoDeadActionTests(unittest.TestCase):
    """Every offered action names something that exists and does something."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.routes = _route_table()
        cls.sources = _ui_sources()

    def test_01_every_offered_action_has_a_target(self) -> None:
        offered = set(ACTIONS) | set(_ts_result_action_ids()) | set(_ts_result_action_labels())
        self.assertTrue(offered)
        missing = sorted(offered - set(ACTION_TARGETS))
        self.assertEqual(
            missing,
            [],
            f"the presentation layer offers actions with no target: {missing}; "
            "a click on one of them does nothing",
        )
        # And the TS declaration and its labels cannot drift apart either.
        self.assertEqual(set(_ts_result_action_ids()), set(_ts_result_action_labels()))

    def test_02_every_target_route_exists_on_the_real_surface(self) -> None:
        for action_id, target in ACTION_TARGETS.items():
            method, path = target["route"]
            self.assertIn(
                (method, path),
                self.routes,
                f"action {action_id!r} targets {method} {path}, which the app does not publish",
            )

    def test_03_every_target_screen_exists_in_the_navigation_table(self) -> None:
        app_source = APP_TSX.read_text(encoding="utf-8")
        for action_id, target in ACTION_TARGETS.items():
            screen = target.get("screen")
            if not screen:
                continue
            self.assertIn(screen, _NAV_SCREENS, action_id)
            self.assertIn(f'"{screen}"', app_source, action_id)
            self.assertIn(f'nav === "{screen}"', app_source, action_id)

    def test_04_the_client_really_calls_each_action_target(self) -> None:
        client_source = FORGE_CLIENT.read_text(encoding="utf-8")
        # The request body has one composer (`ui/src/task-run-body.ts`) and the client
        # must really use it, or a field asserted below would be dead.
        self.assertIn("taskRunBody(", client_source)
        searched = client_source + TASK_RUN_BODY.read_text(encoding="utf-8")
        for action_id, target in ACTION_TARGETS.items():
            needle = str(target["ui_path"])
            self.assertIn(
                needle,
                searched,
                f"action {action_id!r} claims target {needle!r}, but neither "
                "ui/src/api/forge.ts nor the task-run body it calls ever sends it: "
                "the action is dead",
            )

    def test_05_the_open_files_action_carries_a_concrete_project_id(self) -> None:
        # The measured dead «Открыть»: the result card switched to «Проекты» and
        # selected nothing. The state change exists only if the id travels with the
        # click and the projects screen consumes it -- so all four links are asserted.
        app_source = APP_TSX.read_text(encoding="utf-8")
        forge_source = FORGE_TSX.read_text(encoding="utf-8")
        projects_source = PROJECTS_TSX.read_text(encoding="utf-8")
        self.assertIn("const openProjects = useCallback((projectId?: string)", app_source)
        self.assertIn("setProjectFocus((previous) => ({ id: projectId ?? \"\", nonce: previous.nonce + 1 }))", app_source)
        self.assertIn('setNav("projects")', app_source)
        self.assertIn("onOpenProjects(resultProjectId)", forge_source)
        self.assertIn('if (resultProjectId === "") return;', forge_source)
        self.assertIn("focusProjectId", projects_source)
        self.assertIn('if (focusProjectId === "") return;', projects_source)
        self.assertIn("openProject(focusProjectId)", projects_source)

    def test_06_the_two_dispatch_actions_share_one_live_route(self) -> None:
        # «Ответить» and «Повторить» are two front-end affordances over the same real
        # endpoint; this pins that the endpoint the client calls is published, with the
        # method the client uses, so neither affordance can become a dead click.
        self.assertIn(("POST", "/api/tasks/run"), self.routes)
        self.assertIn("/api/tasks/run", FORGE_CLIENT.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- the entrance
    def test_07_the_card_really_opens_the_project(self) -> None:
        """The three open buttons exist, call the route, and carry their own target.

        The measured failure this guards: «Открыть папку» printed a sentence saying a
        browser cannot open folders. A button that performs nothing is the same defect as
        a button that does not exist, so the wiring is asserted from the component's own
        source: the handler exists, it calls the client function, and each of the three
        targets is passed to it.
        """

        source = PROJECTS_TSX.read_text(encoding="utf-8")
        self.assertIn('data-testid="project-disk"', source)
        for target in ("folder", "editor", "terminal"):
            self.assertIn(f'data-testid={{`project-open-${{target}}`}}', source, target)
        self.assertIn('(["folder", "editor", "terminal"] as const)', source)
        self.assertIn("onOpenTarget(target)", source)
        self.assertIn("openProjectTarget(selectedId, target)", source)
        self.assertIn('from "../api/forge"', source)
        self.assertIn("openProject as openProjectTarget", source)
        # The disabled state is the backend's, and its sentence is printed.
        self.assertIn("line.available === true", source)
        self.assertIn("line.reason", source)

    def test_08_the_correction_loop_is_a_first_class_block(self) -> None:
        """«Что поправить?» is rendered above the run/facts blocks, not buried below.

        It is the default way to edit a project, so its position is part of the promise:
        a textarea and exactly ONE button -- «Внести изменения в проект» -- before the
        technical blocks, whose result names the new version and what it changed.

        THE DEFECT THIS PINS. The card used to offer two primary buttons for one change
        («Начать изменения» and «Исправить проект»): the owner pressed one, then the other,
        and could not tell whether anything had started. The second label must not come
        back, and the click must produce a status strip that names the run.
        """

        source = PROJECTS_TSX.read_text(encoding="utf-8")
        i18n = I18N.read_text(encoding="utf-8")
        self.assertIn('t(lang, "projects.newversion.title")', source)
        self.assertIn("const changeBlock = (", source)
        self.assertIn("{changeBlock}", source)
        # ONE action, and its label is the owner's word for it.
        self.assertIn('t(lang, "projects.newversion.start")', source)
        self.assertIn('"Внести изменения в проект"', i18n)
        for duplicate in (
            "projects.newversion.submit",
            "projects.newversion.submitting",
            "projects.newversion.staged",
        ):
            self.assertNotIn(duplicate, source, f"the second change control is back: {duplicate}")
            self.assertNotIn(duplicate, i18n, f"the second change control is back: {duplicate}")
        # The status strip: the run id, the live stage, the honest end.
        self.assertIn('data-testid="change-status"', source)
        self.assertIn('data-testid="change-status-line"', source)
        self.assertIn('data-testid="change-stage-line"', source)
        self.assertIn('t(lang, "projects.change.launched")', source)
        self.assertIn('t(lang, "projects.change.done")', source)
        self.assertIn("isTerminalRecord", source)
        self.assertIn('data-testid="change-outcome"', source)
        self.assertIn('data-testid="change-files"', source)
        self.assertIn('t(lang, "projects.newversion.changed")', source)
        self.assertIn('t(lang, "projects.newversion.changed.none")', source)
        # Position: the correction block is rendered before the preview, the local-run
        # panel, the artifact table and the collapsed diagnostics, i.e. it is not
        # something the owner has to scroll past the technical detail to find.
        rendered_at = source.index("{changeBlock}")
        for later in ('{/* ---------------------------------------------------------- the preview */}',
                      "<RunPanel",
                      'id="project-artifacts"',
                      '<details className="tech-details">',
                      't(lang, "projects.preview.title")',
                      't(lang, "projects.artifacts.title")'):
            self.assertLess(rendered_at, source.index(later), later)

    def test_08b_the_noise_is_collapsed_and_the_actions_are_one_row(self) -> None:
        """Second complaint: too much on the card. The blocks stay, collapsed.

        Below the primary block the card shows the strip, the folder, one row of secondary
        actions and the versions. The launch, the preview, the local run, the save facts,
        the facts, the specification, the artifact table and the technical details are
        behind one summary -- reachable (nothing was deleted) but not in the way.
        """

        source = PROJECTS_TSX.read_text(encoding="utf-8")
        self.assertIn('t(lang, "projects.card.details")', source)
        self.assertIn('data-testid="project-details"', source)
        # One compact row carries the whole existing action vocabulary.
        self.assertIn('data-testid="project-secondary"', source)
        for action in (
            "project-secondary-open",
            "project-secondary-launch",
            "project-secondary-verify",
            "project-secondary-archive",
            "project-secondary-restore",
            "project-secondary-delete",
        ):
            self.assertIn(action, source, action)
        # Each of those handlers is still wired to a real call, not to a sentence.
        for call in ("onLaunch", "onOpenTarget", "onVerify", "onArchive", "onRestore", "onDelete"):
            self.assertIn(call, source, call)
        # The visible part of the card is exactly: change block, folder, row, versions.
        # (Their JSX is defined once, as values, and rendered in this order.)
        visible = source[source.index("{changeBlock}"): source.index('data-testid="project-details"')]
        for kept in ("{diskVisible}", "{secondaryRow}", "{versionsBlock}"):
            self.assertIn(kept, visible, kept)
        # Each of those values is a real definition, not a dangling name.
        for defined in (
            'data-testid="project-disk"',
            'data-testid="project-secondary"',
            "const versionsBlock = (",
        ):
            self.assertIn(defined, source, defined)
        # The collapsed part holds the blocks that used to fill the card.
        collapsed = source[source.index('data-testid="project-details"'):]
        for folded in (
            'data-testid="project-launch"',
            'id="project-artifacts"',
            "<RunPanel",
            't(lang, "projects.preview.title")',
            't(lang, "projects.spec.title")',
            't(lang, "projects.artifacts.title")',
        ):
            self.assertIn(folded, collapsed, folded)

    def test_09_the_absolute_folder_is_diagnostics_only(self) -> None:
        """The card prints the relative folder above the fold and the absolute one below.

        `presentation.facts` carries the record's own relative path; the absolute path is
        a field of the collapsed technical block. This is the rule the acceptance test
        checks against a live payload; here it is pinned structurally so a refactor
        cannot quietly move it up the card.
        """

        card = build_project_presentation(
            project={
                "project_id": "proj-000000000abc",
                "status": "COMPLETED",
                "current_version": 1,
                "project_path": "Forge Projects/Проект (proj-000000000abc)",
                "workspace_id": "w",
            },
            versions=[{"version": 1, "file_count": 1, "verification": "passed",
                       "acceptance": "passed", "run_id": "run-1"}],
            files=[{"path": "app.py", "sha256": "0" * 64, "size": 10}],
            workspace_exists=True,
            folder_absolute=r"C:\Users\owner\Desktop\Forge Projects\Проект (proj-000000000abc)",
        ).to_dict()
        relative = "Forge Projects/Проект (proj-000000000abc)"
        absolute = r"C:\Users\owner\Desktop\Forge Projects\Проект (proj-000000000abc)"
        facts = {label: value for label, value in card["facts"]}
        self.assertEqual(facts["Папка проекта"], relative)
        for label, value in card["facts"]:
            self.assertNotIn(absolute, value, label)
        self.assertNotIn(absolute, card["headline"].__str__())
        fields = {field["key"]: field["value"] for field in card["technical"]["fields"]}
        self.assertEqual(fields["folder_absolute"], absolute)
        self.assertTrue(card["technical"]["collapsed"])


# --------------------------------------------------------------------------- #
# 4. no false completion
# --------------------------------------------------------------------------- #


class NoFalseCompletionTests(_AppCase):
    """ГОТОВО is a fact set, never a word the run chose."""

    def _presentation(self, **overrides) -> dict:
        facts: dict = {
            "project": {"project_id": "proj-000000000abc", "status": "COMPLETED",
                        "current_version": 1, "project_path": "p", "workspace_id": "w"},
            "versions": [{"version": 1, "file_count": 1, "verification": "passed",
                          "acceptance": "passed", "run_id": "run-1"}],
            "files": [{"path": "app.py", "sha256": "0" * 64, "size": 10}],
            "workspace_exists": True,
        }
        facts.update(overrides)
        return build_project_presentation(**facts).to_dict()

    def test_01_the_card_never_says_ready_without_the_facts(self) -> None:
        ready = self._presentation()
        self.assertEqual(ready["project_state"], "ГОТОВО")

        for label, facts in {
            "no files": {"files": [], "versions": [{"version": 1, "file_count": 0,
                                                    "verification": "passed",
                                                    "acceptance": "passed",
                                                    "run_id": "run-1"}]},
            "verification not run": {"versions": [{"version": 1, "file_count": 1,
                                                   "verification": "not_run",
                                                   "acceptance": "passed",
                                                   "run_id": "run-1"}]},
            "verification failed": {"versions": [{"version": 1, "file_count": 1,
                                                  "verification": "failed",
                                                  "acceptance": "passed",
                                                  "run_id": "run-1"}]},
            "acceptance failed": {"versions": [{"version": 1, "file_count": 1,
                                                "verification": "passed",
                                                "acceptance": "failed",
                                                "run_id": "run-1"}]},
            "workspace missing": {"workspace_exists": False},
            "record not completed": {"project": {"project_id": "proj-000000000abc",
                                                 "status": "NEEDS_CHANGES",
                                                 "current_version": 1,
                                                 "project_path": "p",
                                                 "workspace_id": "w"}},
        }.items():
            card = self._presentation(**facts)
            self.assertNotEqual(card["project_state"], "ГОТОВО", label)
            # The six save indicators are independent, and the completion one follows
            # the same fact set as the headline -- never "да" on its own.
            self.assertEqual(card["save_facts"][-1]["key"], "project_ready")
            self.assertNotEqual(card["save_facts"][-1]["value"], "да", label)

    def test_02_the_completion_gate_is_the_existing_one(self) -> None:
        complete = dict(
            run_state="COMPLETED",
            run_success=True,
            project_exists=True,
            project_workspace_exists=True,
            artifact_count=1,
            artifact_manifest=({"path": "app.py"},),
            verification="passed",
            acceptance="passed",
            specification_confirmed=True,
        )
        self.assertIs(derive_outcome(**complete), RunOutcome.PROJECT_COMPLETED)
        for missing in ("artifact_count", "project_workspace_exists", "verification",
                        "acceptance", "specification_confirmed"):
            broken = dict(complete)
            broken[missing] = 0 if missing == "artifact_count" else False if "exists" in missing or "confirmed" in missing else "failed"
            self.assertIsNot(
                derive_outcome(**broken),
                RunOutcome.PROJECT_COMPLETED,
                f"completion survived a missing {missing}",
            )
        # The user-facing word follows the outcome, not the run state.
        self.assertNotEqual(
            honest_state("COMPLETED", RunOutcome.SPECIFICATION_READY), "COMPLETED"
        )
        self.assertEqual(
            honest_state("COMPLETED", RunOutcome.PROJECT_COMPLETED), "COMPLETED"
        )

    def test_03_a_zero_file_write_run_is_never_completed_on_the_surface(self) -> None:
        client, fake = self.compose(TEXT_ANSWER)
        session_id = self.confirmed_session(client)
        response = client.post(
            "/api/tasks/run",
            json={
                "description": GOAL,
                "category": "code",
                "provider_name": "deepseek",
                "model_name": "deepseek-chat",
                "write_mode": True,
                "discovery_session_id": session_id,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        # Two calls: the answer, and the single forced retry Forge makes when an
        # answer carries no file block. Both are the fake -- no real provider exists.
        self.assertEqual(len(fake.requests), 2, "the fake provider was not asked")
        self.assertEqual(int(body["files_created"]), 0)
        self.assertNotEqual(body["state"], "COMPLETED", body)
        self.assertNotEqual(body["outcome"], "PROJECT_COMPLETED", body)

        # Through the card the owner reads: nothing was created, so it says so.
        project_id = str(body.get("lifecycle_project_id") or "")
        self.assertTrue(project_id, body)
        detail = client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(int(detail["files_created"]), 0)
        self.assertEqual(detail["presentation"]["project_state"], "НЕ СОЗДАН")
        self.assertIn(detail["stage"], (PROJECT_NOT_CREATED, STAGE_FAILED))
        card = card_of(
            files=int(body["files_created"]),
            questions=[],
            needs_clarification=False,
            round_number=0,
            write_status="no_changes",
            write_requested=True,
        )
        self.assertNotEqual(card["state"], "ГОТОВО", card)
        self.assertIn("retry", card["actions"], card)


# --------------------------------------------------------------------------- #
# 5. progress monotonicity
# --------------------------------------------------------------------------- #

#: How much of the work each word *claims*. A word with a higher number can never be
#: replaced by one with a lower number as more facts arrive within one run.
_STAGE_PROGRESS = {
    PROJECT_NOT_CREATED: 0,
    FILES_CREATED: 1,
    VERIFICATION_RUNNING: 2,
    VERIFIED: 3,
    READY_TO_RUN: 3,
    PREVIEW_AVAILABLE: 4,
    STAGE_COMPLETED: 5,
}


class ProgressMonotonicityTests(unittest.TestCase):
    """The visible stage never goes backwards within one run."""

    def test_01_one_run_walks_forward_only(self) -> None:
        snapshots = [
            dict(files_count=0),
            dict(files_count=2, verification="pending"),
            dict(files_count=2, verification="passed", runnable=True),
            dict(files_count=2, verification="passed", preview_available=True),
            dict(files_count=2, verification="passed", preview_available=True,
                 lifecycle_status="COMPLETED", acceptance="passed"),
        ]
        stages = [project_stage(**snapshot) for snapshot in snapshots]
        levels = [_STAGE_PROGRESS[stage] for stage in stages]
        self.assertEqual(stages[0], PROJECT_NOT_CREATED)
        self.assertEqual(stages[-1], STAGE_COMPLETED)
        for index in range(1, len(levels)):
            self.assertGreaterEqual(
                levels[index],
                levels[index - 1],
                f"the stage went backwards: {stages[index - 1]} -> {stages[index]}",
            )

    def test_02_the_same_facts_always_produce_the_same_word(self) -> None:
        facts = dict(files_count=1, verification="passed", lifecycle_status="COMPLETED",
                     acceptance="passed", preview_available=True)
        self.assertEqual(project_stage(**facts), project_stage(**facts))
        # And the word is derived, never stored: a different acceptance flips it.
        self.assertEqual(
            project_stage(**{**facts, "acceptance": "failed"}), STAGE_NEEDS_CHANGES
        )

    def test_03_no_stage_above_nothing_with_no_files(self) -> None:
        for verification in ("", "not_run", "pending", "passed", "failed", "not_applicable"):
            for acceptance in ("", "passed", "failed"):
                for status in ("PLANNED", "COMPLETED", "NEEDS_CHANGES", "FAILED"):
                    for preview in (False, True):
                        stage = project_stage(
                            files_count=0,
                            verification=verification,
                            acceptance=acceptance,
                            lifecycle_status=status,
                            preview_available=preview,
                        )
                        self.assertEqual(
                            stage,
                            PROJECT_NOT_CREATED,
                            f"zero files produced {stage!r}",
                        )

    def test_04_a_live_process_never_reads_as_finished(self) -> None:
        stage = project_stage(
            files_count=1,
            verification="passed",
            acceptance="passed",
            lifecycle_status="COMPLETED",
            preview_available=True,
            run_state="RUNNING",
        )
        self.assertEqual(stage, STAGE_RUNNING)
        self.assertNotEqual(stage, STAGE_COMPLETED)


class OnePressChangeTests(unittest.TestCase):
    """Invariant 6: one press, one dispatch, one plain-language end.

    Measured defect: the owner pressed «Внести изменения в проект», saw nothing that said
    anything had started, and pressed again. TWO versions (v4, v5) were created with zero
    files each, and the run that failed had refused his sentence as a «secret».

    The rules live in ``ui/src/change-flight.ts``, which imports nothing, so they are
    executed here from their own source with Node -- not re-implemented in Python.
    """

    @staticmethod
    def _flight_import() -> str:
        return f"import * as f from {json.dumps((UI_SOURCE / 'change-flight.ts').as_uri())};"

    @staticmethod
    def _flight(facts: str) -> str:
        """Evaluate one expression from change-flight.ts and return its JSON answer."""

        script = (
            OnePressChangeTests._flight_import()
            + f"process.stdout.write(JSON.stringify({facts}));"
        )
        return _node(script).strip()

    # ----------------------------------------------------------- one dispatch
    def test_01_a_double_click_produces_exactly_one_dispatch(self) -> None:
        """The guard is synchronous: the second click of a double click is dropped."""

        script = (
            self._flight_import()
            + "const g = f.createChangeGuard();"
            + "const calls = [];"
            + "const press = () => { if (g.tryAcquire()) calls.push('dispatch'); };"
            + "press(); press();"  # a double click delivers both clicks in one tick
            + "process.stdout.write(JSON.stringify({calls, held: g.held()}));"
        )
        answer = json.loads(_node(script))
        self.assertEqual(answer["calls"], ["dispatch"], "a double click dispatched twice")
        self.assertTrue(answer["held"])
        # After the dispatch answers the guard is free again -- the next real change must be
        # dispatchable, so the guard cannot latch.
        script = (
            self._flight_import()
            + "const g = f.createChangeGuard();"
            + "const calls = [];"
            + "const press = () => { if (g.tryAcquire()) calls.push('dispatch'); };"
            + "press(); g.release(); press();"
            + "process.stdout.write(JSON.stringify({calls}));"
        )
        self.assertEqual(json.loads(_node(script))["calls"], ["dispatch", "dispatch"])

    def test_02_the_button_is_off_while_the_run_is_in_flight(self) -> None:
        """From the click to the terminal state, and not a moment less."""

        cases = (
            # starting, runId, finished, expected
            ("true", '""', "false", True),      # the click, before the dispatch answers
            ('false', '"run-1"', "false", True),  # the run exists and has not ended
            ('false', '"run-1"', "true", False),  # it ended: the button comes back
            ('false', '""', "false", False),      # nothing started
            ('true', '""', "true", False),        # a refusal came back while sending
        )
        for starting, run_id, finished, expected in cases:
            with self.subTest(starting=starting, runId=run_id, finished=finished):
                answer = json.loads(
                    self._flight(
                        "f.changeInFlight({starting: %s, runId: %s, finished: %s})"
                        % (starting, run_id, finished)
                    )
                )
                self.assertIs(answer, expected)

    def test_03_the_screen_wires_the_guard_and_the_lock(self) -> None:
        """The rules above are used by the real screen, in the right order."""

        source = PROJECTS_TSX.read_text(encoding="utf-8")
        # The button is off for the whole flight, not only while the request travels.
        self.assertIn("const changeBusy = busy === \"version\" || changeLocked;", source)
        # ... and the decision itself lives in `change-state.ts`, where it is executed by
        # `ChangeBlockTests` below. The screen hands it the flight facts.
        self.assertIn("inFlight: changeStarting || changeLocked,", source)
        self.assertIn("disabled={controls.primaryDisabled}", source)
        self.assertIn("changeLocked: boolean;", source)
        self.assertIn("changeLocked={changeLocked}", source)
        self.assertIn("from \"../change-flight\"", source)
        # The guard is taken synchronously, BEFORE the first await of the handler. The
        # handler takes an optional provider so the zero-file retry can switch it in the
        # same press; the guard still comes first.
        handler = source[source.index("const startChange = async (overrideProvider?: string) => {"):]
        handler = handler[: handler.index("\n  };")]
        acquired = handler.index("changeGuardRef.current.tryAcquire()")
        first_await = handler.index("await ")
        self.assertLess(
            acquired,
            first_await,
            "the guard must be acquired before the first await, or a double click wins",
        )
        self.assertIn("changeGuardRef.current.release()", handler)
        self.assertIn("useRef(createChangeGuard())", source)
        # The strip appears at once, on the click, with the words the owner asked for.
        self.assertIn('data-testid="change-status"', source)
        self.assertIn('t(lang, "projects.change.starting")', source)
        self.assertIn('t(lang, "projects.change.launched")', source)
        self.assertIn('data-testid="change-start-busy"', source)

    # ------------------------------------------------------- the plain refusal
    def test_04_the_refusal_wording_is_the_plain_one(self) -> None:
        """The owner's own failure is reworded, and the rule id survives it."""

        raw = (
            "Critical Tier 1 context item 'task:task-9c1f' cannot fit into available "
            "budget (128000 tokens, max_items=16). Reason: "
            "security_forbidden_content:secret. Текст содержит значение, похожее на "
            "секрет (rule=high_entropy, length_bucket=40-64, "
            "location=task_description)"
        )
        answer = json.loads(
            self._flight(f"f.classifyChangeRefusal({json.dumps(raw)})")
        )
        self.assertEqual(answer["kind"], "context_refused")
        self.assertEqual(answer["rule"], "high_entropy")
        # A real refusal keeps its own rule id, and an unrelated failure stays unknown.
        for text, rule in (
            ("… Rule: security_forbidden_content:api_key … (rule=provider_key_prefix)",
             "provider_key_prefix"),
            ("Timeout while calling the provider", ""),
        ):
            with self.subTest(text=text[:20]):
                classified = json.loads(
                    self._flight(f"f.classifyChangeRefusal({json.dumps(text)})")
                )
                self.assertEqual(classified["rule"], rule)
        self.assertEqual(
            json.loads(self._flight('f.classifyChangeRefusal("boom")'))["kind"], "unknown"
        )
        # The wording is the owner's, in both languages, and the raw text is folded away.
        i18n = I18N.read_text(encoding="utf-8")
        self.assertIn("Текст изменения отклонён проверкой контекста", i18n)
        self.assertIn("(rule={rule})", i18n)
        self.assertIn('"projects.change.technical": "Технические детали"', i18n)
        source = PROJECTS_TSX.read_text(encoding="utf-8")
        self.assertIn('t(lang, "projects.change.refused.context")', source)
        self.assertIn('changeStrip.refusalRule', source)
        self.assertIn('data-testid="change-refusal"', source)
        self.assertIn('data-testid="change-refusal-detail"', source)
        self.assertIn('t(lang, "projects.change.technical")', source)


class MockIsLoudTests(unittest.TestCase):
    """Invariant 7: mock never looks like a change, and a zero-file run never a success.

    Measured defect (two projects, the same shape): the owner pressed «Внести изменения»
    three times on one sentence, `mock` (``deterministic-offline``) or a provider that
    answered nothing was behind the run, and **each press recorded another version with
    zero files** (v3, v4, v5 in one record; v4 in another). The card said nothing about
    the offline engine before the press and nothing honest after it.

    The rules live in ``ui/src/change-state.ts`` and are executed here from their own
    source with Node, so this file tests the code the screen runs -- not a copy of it.
    """

    @staticmethod
    def _state_import() -> str:
        return f"import * as s from {json.dumps((UI_SOURCE / 'change-state.ts').as_uri())};"

    #: Node's own stdout is decoded with the host's code page here, so every answer is
    #: emitted as pure ASCII (``\uXXXX`` escapes are valid JSON) and read back as UTF-8
    #: text by ``json.loads``. Without it «Изменения внесены» arrived as mojibake.
    _ASCII_JSON = (
        r"const __a=(v)=>JSON.stringify(v).replace(/[\u007f-\uffff]/g,"
        r"(c)=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));"
    )

    @classmethod
    def _ask(cls, expression: str):
        script = (
            cls._state_import()
            + cls._ASCII_JSON
            + f"process.stdout.write(__a({expression}));"
        )
        return json.loads(_node(script))

    # ------------------------------------------------------------------ the warning
    def test_01_a_mock_change_carries_a_warning_before_the_press(self) -> None:
        """The offline engine is named as such on the button's own block."""

        for name, expected in (
            ("mock", True),
            ("MOCK", True),
            ("deterministic-offline", True),
            (" offline ", True),
            ("deepseek", False),
            ("", False),  # unknown is not an accusation
        ):
            with self.subTest(provider=name):
                self.assertIs(self._ask(f"s.isMockProvider({json.dumps(name)})"), expected)

        # «Авто» is the backend's own default, and the warning follows the engine that
        # will really answer -- not the empty string the select carries.
        self.assertEqual(
            self._ask('s.effectiveProvider("", "deepseek")'), "deepseek"
        )
        self.assertEqual(self._ask('s.effectiveProvider("mock", "deepseek")'), "mock")
        self.assertIs(
            self._ask('s.isMockProvider(s.effectiveProvider("", "mock"))'), True
        )

    def test_02_the_warning_is_printed_above_the_button_in_words_the_owner_reads(self) -> None:
        """The exact sentence, above the button, in both screens that can start a run."""

        i18n = I18N.read_text(encoding="utf-8")
        expected = (
            "Выбран mock — файлы НЕ изменятся, это офлайн-заглушка. "
            "Для настоящих изменений выберите deepseek/openrouter/gemini."
        )
        self.assertIn(f'"forge.mock.warning": "{expected}"', i18n)
        projects = PROJECTS_TSX.read_text(encoding="utf-8")
        forge = FORGE_TSX.read_text(encoding="utf-8")
        for source, marker in (
            (projects, 'data-testid="change-mock-warning"'),
            (forge, 'data-testid="launch-mock-warning"'),
        ):
            self.assertIn(marker, source)
            self.assertIn('t(lang, "forge.mock.warning")', source)
            # The warning is rendered BEFORE the run button it belongs to.
            self.assertLess(
                source.index("forge.mock.warning"),
                source.index('data-testid="change-start"')
                if source is projects
                else source.index("runs.form.submit"),
                "the mock warning must sit above the button, not below it",
            )
        # The change block asks the module, so the decision is not re-implemented inline.
        self.assertIn("changeControls({", projects)
        self.assertIn("mocked: effectiveMock,", projects)

    # ------------------------------------------------- one press, then another button
    def test_03_a_successful_change_replaces_the_button_instead_of_inviting_it(self) -> None:
        """«Изменения внесены (vN)» + the smaller «Внести ещё изменения»."""

        applied = self._ask(
            's.changeControls({empty: false, inFlight: false, finished: true,'
            " filesWritten: 2, version: 3, mocked: false, realProvider: 'deepseek',"
            " armed: false})"
        )
        self.assertEqual(applied["primary"], "applied")
        self.assertIs(applied["primaryDisabled"], True)
        self.assertEqual(applied["primaryVersion"], 3)
        self.assertIs(applied["secondaryVisible"], True)
        self.assertIs(applied["formFrozen"], True)
        self.assertEqual(applied["outcome"], "applied")
        # Only the smaller control re-arms the form, and then the ordinary action is back.
        armed = self._ask(
            's.changeControls({empty: false, inFlight: false, finished: true,'
            " filesWritten: 2, version: 3, mocked: false, realProvider: 'deepseek',"
            " armed: true})"
        )
        self.assertEqual(armed["primary"], "start")
        self.assertIs(armed["primaryDisabled"], False)
        self.assertIs(armed["secondaryVisible"], False)
        self.assertIs(armed["formFrozen"], False)

    def test_04_a_zero_file_change_offers_the_real_model_and_never_success(self) -> None:
        """«Повторить с реальной моделью», the best non-mock provider, honest words."""

        empty = self._ask(
            's.changeControls({empty: false, inFlight: false, finished: true,'
            " filesWritten: 0, version: 3, mocked: true, realProvider: 'deepseek',"
            " armed: false})"
        )
        self.assertEqual(empty["primary"], "retry")
        self.assertIs(empty["primaryDisabled"], False)
        self.assertEqual(empty["outcome"], "empty")
        self.assertIs(empty["secondaryVisible"], True)
        self.assertIs(empty["formFrozen"], True)
        # With no real provider the button is off rather than silently retrying mock.
        blocked = self._ask(
            's.changeControls({empty: false, inFlight: false, finished: true,'
            " filesWritten: 0, version: 3, mocked: true, realProvider: '', armed: false})"
        )
        self.assertEqual(blocked["primary"], "retry")
        self.assertIs(blocked["primaryDisabled"], True)
        # While the run is in flight nothing but «Отправляю…» is offered.
        flying = self._ask(
            's.changeControls({empty: false, inFlight: true, finished: false,'
            " filesWritten: 0, version: 0, mocked: false, realProvider: 'deepseek',"
            " armed: false})"
        )
        self.assertEqual(flying["primary"], "sending")
        self.assertIs(flying["primaryDisabled"], True)
        self.assertIs(flying["secondaryVisible"], False)

    def test_05_the_best_non_mock_provider_is_the_deployment_default(self) -> None:
        """One click switches to a provider that can really change files."""

        options = ["mock", "openrouter", "radox", "deepseek", "google"]
        self.assertEqual(
            self._ask(f"s.bestRealProvider({json.dumps(options)}, 'deepseek')"),
            "deepseek",
        )
        # A default that IS the mock is skipped instead of being offered again.
        self.assertEqual(
            self._ask(f"s.bestRealProvider({json.dumps(['mock', 'openrouter'])}, 'mock')"),
            "openrouter",
        )
        self.assertEqual(
            self._ask("s.bestRealProvider(['mock', 'deterministic-offline'], 'mock')"), ""
        )

    def test_06_the_honest_words_exist_in_both_languages(self) -> None:
        """«файлов 0 — код не менялся», «Модель не вернула файлы», the retry and the rearm."""

        i18n = I18N.read_text(encoding="utf-8")
        for text in (
            "файлов 0 — код не менялся",
            "Модель не вернула файлы",
            "Повторить с реальной моделью",
            "Внести ещё изменения",
            "Изменения внесены ({version})",
            "пустая запись: файлов 0 — код не менялся",
            "0 files — the code did not change",
            "The model returned no files",
            "Retry with a real model",
            "Make one more change",
        ):
            self.assertIn(text, i18n)
        # The version table labels an empty record as an empty record.
        projects = PROJECTS_TSX.read_text(encoding="utf-8")
        self.assertIn('t(lang, "projects.version.empty")', projects)

    # ------------------------------------------------------- «Что сделал Forge»
    def test_07_what_forge_did_comes_from_the_models_own_answer(self) -> None:
        """Summary + files with sizes, and the answer's first two lines when no file."""

        wrote = self._ask(
            "s.whatForgeDid({summary: "
            + json.dumps("Добавил подпись в подвал")
            + ", files: [{path: 'index.html', size: 1200, change: 'update'},"
            " {path: 'notes.md', size: 40, change: 'create'}],"
            " answerExcerpt: "
            + json.dumps("Готово.\nПодпись добавлена.")
            + "})"
        )
        self.assertEqual(wrote["summary"], "Добавил подпись в подвал")
        self.assertIs(wrote["noFiles"], False)
        self.assertEqual(
            wrote["files"],
            [
                {"path": "index.html", "size": 1200, "change": "update"},
                {"path": "notes.md", "size": 40, "change": "create"},
            ],
        )
        # No file from the model: the block says exactly that and quotes the answer.
        nothing = self._ask(
            "s.whatForgeDid({summary: "
            + json.dumps("Не могу изменить файлы")
            + ", files: [], answerExcerpt: "
            + json.dumps("Первая строка ответа.\nВторая строка.\nТретья не нужна.")
            + "})"
        )
        self.assertIs(nothing["noFiles"], True)
        self.assertEqual(nothing["files"], [])
        self.assertEqual(
            nothing["answerLines"], ["Первая строка ответа.", "Вторая строка."]
        )
        # Empty inputs stay empty: nothing is composed in their place.
        blank = self._ask("s.whatForgeDid({})")
        self.assertEqual(blank["summary"], "")
        self.assertEqual(blank["files"], [])
        self.assertEqual(blank["answerLines"], [])
        self.assertIs(blank["noFiles"], True)
        # The block is rendered by the card, from the response's own fields.
        projects = PROJECTS_TSX.read_text(encoding="utf-8")
        self.assertIn('data-testid="change-what-forge-did"', projects)
        self.assertIn('t(lang, "projects.change.did.no_files")', projects)
        self.assertIn("dispatched?.response.answer_excerpt", projects)
        self.assertIn("model_intent", projects)

    def test_08_an_empty_version_is_labelled_as_an_empty_version(self) -> None:
        """The count alone read like a file count; the label says what it means."""

        self.assertEqual(self._ask("s.versionFilesWord(0)"), "empty")
        self.assertEqual(self._ask("s.versionFilesWord(3)"), "files")
        self.assertEqual(self._ask("s.versionFilesWord(undefined)"), "empty")

    def test_09_the_screen_switches_the_provider_in_the_retry_press(self) -> None:
        """«Повторить с реальной моделью» is one action: switch and dispatch the same text."""

        projects = PROJECTS_TSX.read_text(encoding="utf-8")
        self.assertIn("const retryWithRealModel = () => {", projects)
        self.assertIn("setProvider(realProvider);", projects)
        self.assertIn("void startChange(realProvider);", projects)
        self.assertIn('onClick={\n                    controls.primary === "retry" ? onRetryWithRealModel : onStartChange\n                  }', projects)
        # The second press of the same words is answered by the backend, not by a new
        # version, and the card says so.
        self.assertIn("stagedOutcome.value.already_applied === true", projects)
        self.assertIn('t(lang, "projects.change.already_applied")', projects)


if __name__ == "__main__":
    unittest.main()
