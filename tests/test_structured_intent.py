"""Structured intent v0.1: the parser, its validation matrix and its truth rules.

Three things are asserted here, in the order the module is built:

* :class:`EnvelopeParsingTests` -- a valid JSON envelope for each of the five intent
  types, in each of the three accepted shapes (a bare object, a ```json fence, an
  object embedded in prose), and the fields it carries through;
* :class:`EnvelopeFailureTests` -- malformed JSON, an unknown ``intent_type`` and a
  missing required field, per type. **Nothing raises**: the answer is reported as
  ``FINAL_ANSWER`` (or as the recognised type with ``valid=False``) with the error
  named, so the run continues;
* :class:`HeuristicTests` -- the deterministic fallback when there is no envelope, one
  case per branch;
* :class:`FalseCompletionTests` -- the truth rule: ``FINAL_ANSWER``,
  ``CLARIFICATION_REQUIRED`` and ``PLAN_READY`` never produce a completed project, even
  when the run succeeded. ``GENERATE_PROJECT`` without real file writes does not
  complete either, and the only positive control is a generation *with* files.

The parser is pure, so most of this file needs no runtime at all. The two dispatch
tests use :class:`_AnsweringProvider` -- an ordinary ``Provider`` registered under the
real ``deepseek`` name, exactly as ``tests/test_false_completion_guard.py`` does -- so
the production dispatcher, executor, run scope and response construction all run
unchanged. No socket is opened and no key is read.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import unittest

from app.agent_runtime.intent import (
    INTENT_TYPES,
    IntentType,
    ModelIntent,
    intent_allows_completion,
    parse_model_intent,
)
from app.agent_runtime.outcome import RunOutcome, derive_outcome
from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.config.settings import RuntimeSettings
from app.runtime.bootstrap import create_runtime
from app.runtime.run_store import RunStore

#: Every completion fact, present. A run with exactly these facts is a finished
#: project -- unless the answer's own declared intent forbids it.
COMPLETE_FACTS = dict(
    run_state="COMPLETED",
    run_success=True,
    project_exists=True,
    project_workspace_exists=True,
    artifact_count=1,
    artifact_manifest=("app.py",),
    verification="passed",
    acceptance="passed",
    specification_confirmed=True,
)

#: A perfectly ordinary successful answer that declares no work.
PLAIN_ANSWER = (
    "Небольшое локальное приложение для учёта домашних расходов: список трат, "
    "категории и месячный отчёт."
)

FINAL_ANSWER_ENVELOPE = json.dumps(
    {
        "intent_type": "FINAL_ANSWER",
        "summary": "The expenses app can be built as a small local web app.",
    }
)

CLARIFICATION_ENVELOPE = json.dumps(
    {
        "intent_type": "CLARIFICATION_REQUIRED",
        "summary": "Three answers are needed before planning.",
        "questions": [
            "Какая валюта должна быть в учёте?",
            "Нужен ли многопользовательский режим?",
        ],
    }
)

PLAN_ENVELOPE = json.dumps(
    {
        "intent_type": "PLAN_READY",
        "summary": "A three-step plan.",
        "plan": ["write app.py", "add the report", "verify"],
    }
)

GENERATE_ENVELOPE = json.dumps(
    {
        "intent_type": "GENERATE_PROJECT",
        "summary": "Two files.",
        "files": {"app.py": "print('hi')\n", "README.md": "# Expenses\n"},
    }
)

REVIEW_ENVELOPE = json.dumps(
    {
        "intent_type": "REVIEW",
        "summary": "The change is correct but the report is untested.",
    }
)


class _AnsweringProvider(Provider):
    """A provider whose answer is a fixed string, whatever it is asked. No network."""

    def __init__(self, answer: str) -> None:
        super().__init__(
            ProviderConfig(provider_name="deepseek", model_name="deepseek-chat")
        )
        self.answer = answer
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=request.model_name or self.model_name,
            output=self.answer,
        )


# --------------------------------------------------------------------------- #
# 1. a valid envelope, for each of the five types
# --------------------------------------------------------------------------- #


class EnvelopeParsingTests(unittest.TestCase):
    """Each accepted shape, and the fields each type requires."""

    def test_01_every_type_is_exactly_the_five_documented_words(self) -> None:
        self.assertEqual(
            INTENT_TYPES,
            (
                "CLARIFICATION_REQUIRED",
                "PLAN_READY",
                "GENERATE_PROJECT",
                "REVIEW",
                "FINAL_ANSWER",
            ),
        )
        self.assertEqual(len(IntentType), 5)

    def test_02_a_bare_object_parses_for_each_type(self) -> None:
        for envelope, expected in (
            (CLARIFICATION_ENVELOPE, IntentType.CLARIFICATION_REQUIRED),
            (PLAN_ENVELOPE, IntentType.PLAN_READY),
            (GENERATE_ENVELOPE, IntentType.GENERATE_PROJECT),
            (REVIEW_ENVELOPE, IntentType.REVIEW),
            (FINAL_ANSWER_ENVELOPE, IntentType.FINAL_ANSWER),
        ):
            intent = parse_model_intent(envelope)
            self.assertIs(intent.intent_type, expected, envelope)
            self.assertTrue(intent.valid, intent.errors)
            self.assertEqual(intent.source, "envelope")
            self.assertEqual(intent.raw_text, envelope)
            self.assertTrue(intent.response_payload()["raw_text_preserved"])
            self.assertEqual(
                intent.next_action,
                {
                    IntentType.CLARIFICATION_REQUIRED: "answer_questions",
                    IntentType.PLAN_READY: "confirm_plan",
                    IntentType.GENERATE_PROJECT: "apply_files",
                    IntentType.REVIEW: "decide_on_review",
                    IntentType.FINAL_ANSWER: "read_answer",
                }[expected],
            )

    def test_03_a_json_fence_parses(self) -> None:
        text = "Here is my answer.\n\n```json\n" + PLAN_ENVELOPE + "\n```\n"
        intent = parse_model_intent(text)
        self.assertIs(intent.intent_type, IntentType.PLAN_READY)
        self.assertTrue(intent.valid, intent.errors)

    def test_04_an_object_embedded_in_prose_parses(self) -> None:
        text = "plan: " + PLAN_ENVELOPE + " — shall I proceed?"
        intent = parse_model_intent(text)
        self.assertIs(intent.intent_type, IntentType.PLAN_READY)
        self.assertEqual(intent.plan, ("write app.py", "add the report", "verify"))

    def test_05_the_questions_and_the_plan_are_carried_through(self) -> None:
        clarification = parse_model_intent(CLARIFICATION_ENVELOPE)
        self.assertEqual(len(clarification.questions), 2)
        self.assertTrue(clarification.questions[0].endswith("?"))
        self.assertEqual(
            clarification.response_payload()["validation_errors"], []
        )
        plan = parse_model_intent(PLAN_ENVELOPE)
        self.assertEqual(len(plan.plan), 3)
        # A type that carries another field keeps it empty instead of inventing one.
        self.assertEqual(clarification.plan, ())
        self.assertEqual(plan.questions, ())

    def test_06_files_are_a_mapping_and_a_purpose_list_is_accepted(self) -> None:
        mapping = parse_model_intent(GENERATE_ENVELOPE)
        self.assertEqual(
            dict(mapping.files), {"app.py": "print('hi')\n", "README.md": "# Expenses\n"}
        )
        self.assertEqual(mapping.file_paths, ("app.py", "README.md"))

        purpose_only = json.dumps(
            {
                "intent_type": "GENERATE_PROJECT",
                "summary": "A file with a declared purpose only.",
                "files": [{"path": "app.py", "purpose": "the local server"}],
            }
        )
        intent = parse_model_intent(purpose_only)
        self.assertIs(intent.intent_type, IntentType.GENERATE_PROJECT)
        self.assertTrue(intent.valid, intent.errors)
        # The purpose is recorded where a body would be; no content is invented.
        self.assertEqual(dict(intent.files), {"app.py": "the local server"})

    def test_07_the_response_payload_never_echoes_the_raw_answer(self) -> None:
        secret = "sk-" + "Q7x" * 12
        answer = json.dumps(
            {"intent_type": "FINAL_ANSWER", "summary": "done", "note": secret}
        )
        intent = parse_model_intent(answer)
        payload = intent.response_payload()
        self.assertEqual(
            sorted(payload),
            sorted(
                [
                    "intent_type",
                    "summary",
                    "valid",
                    "validation_errors",
                    "next_action",
                    "raw_text_preserved",
                ]
            ),
        )
        self.assertIs(payload["raw_text_preserved"], True)
        self.assertNotIn(secret, json.dumps(payload))
        # ... and the run's own copy still has the original text.
        self.assertIn(secret, intent.raw_text)


# --------------------------------------------------------------------------- #
# 2. malformed input: never an exception, always a named reason
# --------------------------------------------------------------------------- #


class EnvelopeFailureTests(unittest.TestCase):
    """Malformed JSON, unknown types and missing fields, per type."""

    def test_10_malformed_json_is_final_answer_with_the_error_named(self) -> None:
        for text in (
            '{"intent_type": "PLAN_READY", "summary": "x", "plan": [1,',
            '{"intent_type": "GENERATE_PROJECT", "summary": }',
            "```json\n{bad json}\n```",
            '{"intent_type": "FINAL_ANSWER" "summary": "x"}',
        ):
            intent = parse_model_intent(text)
            self.assertIs(intent.intent_type, IntentType.FINAL_ANSWER, text)
            self.assertFalse(intent.valid, text)
            self.assertEqual(intent.source, "envelope", text)
            self.assertTrue(
                any(error.startswith("invalid_json") for error in intent.errors),
                intent.errors,
            )
            self.assertEqual(intent.raw_text, text)

    def test_11_an_unknown_intent_type_is_never_coerced(self) -> None:
        intent = parse_model_intent(
            json.dumps({"intent_type": "DEPLOY_NOW", "summary": "x"})
        )
        self.assertIs(intent.intent_type, IntentType.FINAL_ANSWER)
        self.assertFalse(intent.valid)
        self.assertTrue(intent.errors[0].startswith("unknown_intent_type"))
        self.assertIn("DEPLOY_NOW", intent.errors[0])
        # A non-string intent_type is "missing", not a crash.
        numeric = parse_model_intent(json.dumps({"intent_type": 7, "summary": "x"}))
        self.assertIs(numeric.intent_type, IntentType.FINAL_ANSWER)
        self.assertFalse(numeric.valid)

    def test_12_a_missing_summary_is_named_for_every_type(self) -> None:
        bodies = {
            "CLARIFICATION_REQUIRED": {"questions": ["Why?"]},
            "PLAN_READY": {"plan": ["one"]},
            "GENERATE_PROJECT": {"files": {"app.py": "print(1)"}},
            "REVIEW": {},
            "FINAL_ANSWER": {},
        }
        for intent_type, extra in bodies.items():
            for summary in (None, "", "   ", 42):
                envelope = {"intent_type": intent_type, **extra}
                if summary is not None:
                    envelope["summary"] = summary
                intent = parse_model_intent(json.dumps(envelope))
                self.assertIs(
                    intent.intent_type,
                    IntentType(intent_type),
                    f"{intent_type} must keep its recognised type",
                )
                self.assertFalse(intent.valid, (intent_type, summary))
                self.assertTrue(
                    any(error.startswith("summary_missing") for error in intent.errors),
                    (intent_type, summary, intent.errors),
                )

    def test_13_missing_required_field_per_type(self) -> None:
        cases = (
            ("CLARIFICATION_REQUIRED", {}, "questions_invalid"),
            ("CLARIFICATION_REQUIRED", {"questions": []}, "questions_invalid"),
            ("CLARIFICATION_REQUIRED", {"questions": "Why?"}, "questions_invalid"),
            ("CLARIFICATION_REQUIRED", {"questions": ["ok", 3]}, "questions_invalid"),
            ("PLAN_READY", {}, "plan_invalid"),
            ("PLAN_READY", {"plan": []}, "plan_invalid"),
            ("PLAN_READY", {"plan": "one"}, "plan_invalid"),
            ("GENERATE_PROJECT", {}, "files_invalid"),
            ("GENERATE_PROJECT", {"files": {}}, "files_empty"),
            ("GENERATE_PROJECT", {"files": []}, "files_empty"),
            ("GENERATE_PROJECT", {"files": "app.py"}, "files_invalid"),
            ("GENERATE_PROJECT", {"files": {"": "x"}}, "files_invalid"),
            ("GENERATE_PROJECT", {"files": {"a.py": 1}}, "files_invalid"),
            (
                "GENERATE_PROJECT",
                {"files": [{"purpose": "no path"}]},
                "files_invalid",
            ),
        )
        for intent_type, extra, expected in cases:
            envelope = {"intent_type": intent_type, "summary": "s", **extra}
            intent = parse_model_intent(json.dumps(envelope))
            self.assertIs(intent.intent_type, IntentType(intent_type), (extra, expected))
            self.assertFalse(intent.valid, extra)
            self.assertTrue(
                any(error.startswith(expected) for error in intent.errors),
                (extra, intent.errors),
            )

    def test_14_a_recognised_type_with_an_error_keeps_the_type(self) -> None:
        envelope = {"intent_type": "PLAN_READY", "summary": "s"}
        intent = parse_model_intent(json.dumps(envelope))
        self.assertIs(intent.intent_type, IntentType.PLAN_READY)
        self.assertFalse(intent.valid)
        self.assertEqual(intent.next_action, "confirm_plan")
        self.assertEqual(intent.response_payload()["intent_type"], "PLAN_READY")
        self.assertIs(intent.response_payload()["valid"], False)

    def test_15_the_parser_never_raises_on_junk(self) -> None:
        for raw in (
            None,
            "",
            0,
            7.5,
            True,
            {},
            [],
            "{{{{",
            '{"intent_type":',
            '{"intent_type": null}',
            '{"intent_type": "CLARIFICATION_REQUIRED"}',
            "```json\n[]\n```",
            "no json at all, just prose",
        ):
            intent = parse_model_intent(raw)
            self.assertIsInstance(intent, ModelIntent)
            self.assertIsInstance(intent.intent_type, IntentType)
            self.assertEqual(intent.raw_text, "" if raw is None else str(raw))


# --------------------------------------------------------------------------- #
# 3. the deterministic heuristic, one case per branch
# --------------------------------------------------------------------------- #


class HeuristicTests(unittest.TestCase):
    """No envelope: the fallback order is fixed and testable."""

    def test_20_two_question_lines_are_clarification_required(self) -> None:
        text = (
            "Прежде чем начать, уточните:\n"
            "1. Какая валюта должна быть в учёте?\n"
            "2. Нужен ли многопользовательский режим?\n"
        )
        intent = parse_model_intent(text)
        self.assertIs(intent.intent_type, IntentType.CLARIFICATION_REQUIRED)
        self.assertTrue(intent.valid, intent.errors)
        self.assertEqual(intent.source, "heuristic")
        self.assertEqual(len(intent.questions), 2)
        self.assertFalse(intent.questions[0].startswith("1."))
        self.assertEqual(intent.summary, "Прежде чем начать, уточните:")

    def test_21_one_question_line_is_not_a_clarification_list(self) -> None:
        text = "Готово: локальное приложение для расходов. Подходит ли такой вариант?"
        intent = parse_model_intent(text)
        self.assertIs(intent.intent_type, IntentType.FINAL_ANSWER)
        self.assertEqual(intent.questions, ())

    def test_22_a_fenced_path_is_a_generation(self) -> None:
        text = (
            "The local server:\n\n"
            "```python path=app.py\n"
            "print('listening')\n"
            "```\n"
        )
        intent = parse_model_intent(text)
        self.assertIs(intent.intent_type, IntentType.GENERATE_PROJECT)
        self.assertEqual(dict(intent.files), {"app.py": "print('listening')\n"})
        self.assertEqual(intent.summary, "The local server:")

    def test_23_a_file_marker_is_a_generation(self) -> None:
        text = "**File: tools/report.py**\n```python\nprint('report')\n```\n"
        intent = parse_model_intent(text)
        self.assertIs(intent.intent_type, IntentType.GENERATE_PROJECT)
        self.assertEqual(dict(intent.files), {"tools/report.py": "print('report')\n"})

        marker_only = "**File: README.md**\nI will write the readme next."
        intent = parse_model_intent(marker_only)
        self.assertIs(intent.intent_type, IntentType.GENERATE_PROJECT)
        # A declared path with no body is recorded as an empty body, not omitted.
        self.assertEqual(dict(intent.files), {"README.md": ""})

    def test_24_a_numbered_plan_with_no_fence_is_plan_ready(self) -> None:
        text = "План:\n1. Написать app.py\n2. Добавить отчёт\n3. Проверить\n"
        intent = parse_model_intent(text)
        self.assertIs(intent.intent_type, IntentType.PLAN_READY)
        self.assertEqual(
            intent.plan, ("Написать app.py", "Добавить отчёт", "Проверить")
        )
        self.assertEqual(intent.next_action, "confirm_plan")

    def test_25_a_numbered_list_with_a_plain_fence_is_not_a_plan(self) -> None:
        # The branch is documented as "a numbered plan with NO code fence".
        text = (
            "1. first\n2. second\n3. third\n\n"
            "```python\nprint('example only')\n```\n"
        )
        intent = parse_model_intent(text)
        self.assertIs(intent.intent_type, IntentType.FINAL_ANSWER)

    def test_26_plain_prose_is_a_final_answer(self) -> None:
        intent = parse_model_intent(PLAIN_ANSWER)
        self.assertIs(intent.intent_type, IntentType.FINAL_ANSWER)
        self.assertTrue(intent.valid, intent.errors)
        self.assertEqual(intent.summary, PLAIN_ANSWER)
        self.assertEqual(intent.plan, ())
        self.assertEqual(dict(intent.files), {})

    def test_27_an_empty_answer_is_an_invalid_final_answer(self) -> None:
        for raw in ("", "   \n\n  ", None):
            intent = parse_model_intent(raw)
            self.assertIs(intent.intent_type, IntentType.FINAL_ANSWER)
            self.assertFalse(intent.valid)
            self.assertEqual(intent.raw_text, "" if raw is None else raw)


# --------------------------------------------------------------------------- #
# 4. the truth rule: an intent never invents a completed project
# --------------------------------------------------------------------------- #


class FalseCompletionTests(unittest.TestCase):
    """The three intents that must never complete, and the write path that may."""

    def test_30_the_three_text_intents_never_complete_any_facts(self) -> None:
        for intent_type in (
            IntentType.FINAL_ANSWER,
            IntentType.CLARIFICATION_REQUIRED,
            IntentType.PLAN_READY,
            # Not one of the three named intents, but still not a generation.
            IntentType.REVIEW,
        ):
            self.assertIsNot(
                derive_outcome(intent_type=intent_type.value, **COMPLETE_FACTS),
                RunOutcome.PROJECT_COMPLETED,
                intent_type.value,
            )

    def test_31_only_a_generation_may_complete_and_only_with_files(self) -> None:
        self.assertIs(
            derive_outcome(
                intent_type=IntentType.GENERATE_PROJECT.value, **COMPLETE_FACTS
            ),
            RunOutcome.PROJECT_COMPLETED,
        )
        # The same intent with no artifact: the facts still refuse.
        self.assertIs(
            derive_outcome(
                intent_type=IntentType.GENERATE_PROJECT.value,
                **{
                    **COMPLETE_FACTS,
                    "artifact_count": 0,
                    "artifact_manifest": (),
                },
            ),
            RunOutcome.NEEDS_CHANGES,
        )

    def test_32_the_added_fact_only_lowers_the_outcome(self) -> None:
        # No intent (the pre-existing call) is unchanged ...
        self.assertIs(derive_outcome(**COMPLETE_FACTS), RunOutcome.PROJECT_COMPLETED)
        # ... and a declared clarification or plan names its own outcome.
        self.assertIs(
            derive_outcome(
                intent_type=IntentType.CLARIFICATION_REQUIRED.value,
                run_state="COMPLETED",
                run_success=True,
            ),
            RunOutcome.CLARIFICATION_REQUIRED,
        )
        self.assertIs(
            derive_outcome(
                intent_type=IntentType.PLAN_READY.value,
                run_state="COMPLETED",
                run_success=True,
            ),
            RunOutcome.PLAN_READY,
        )
        # A failure is still a failure, whatever the answer declared.
        self.assertIs(
            derive_outcome(
                intent_type=IntentType.GENERATE_PROJECT.value,
                run_state="FAILED",
                run_success=False,
            ),
            RunOutcome.FAILED,
        )

    def test_33_the_rule_itself_is_explicit(self) -> None:
        # "No declared intent" -- the pre-existing callers -- stays allowed.
        self.assertTrue(intent_allows_completion(""))
        self.assertTrue(intent_allows_completion(None))
        self.assertTrue(intent_allows_completion(IntentType.GENERATE_PROJECT))
        for word in (
            IntentType.FINAL_ANSWER,
            IntentType.CLARIFICATION_REQUIRED,
            IntentType.PLAN_READY,
            IntentType.REVIEW,
            "SOMETHING_ELSE",
        ):
            self.assertFalse(intent_allows_completion(word), word)


class ResponseIntegrationTests(unittest.TestCase):
    """The response publishes the intent, and the run still refuses to complete."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-structured-intent"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.run_root = base / "runs"
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self._previous_root = os.environ.get("FORGE_RUN_STORE_ROOT")
        os.environ["FORGE_RUN_STORE_ROOT"] = str(self.run_root)

        def _restore() -> None:
            if self._previous_root is None:
                os.environ.pop("FORGE_RUN_STORE_ROOT", None)
            else:
                os.environ["FORGE_RUN_STORE_ROOT"] = self._previous_root

        self.addCleanup(_restore)

    def _dispatch(self, answer: str, **request):
        provider = _AnsweringProvider(answer)
        service = ForgeApiService(
            runtime=self._runtime(provider),
            run_store=RunStore(root=self.run_root),
            authorized_project_id="structured-intent-project",
        )
        fields = dict(
            description="Хочу небольшое локальное приложение для учёта расходов",
            category="analysis",
            provider_name="deepseek",
            model_name="deepseek-chat",
        )
        fields.update(request)
        result = service.run_task_detailed(TaskRunRequest(**fields))
        return service, provider, result

    def _runtime(self, provider: Provider):
        # The only enabled provider is the offline mock; the fake sits under the real
        # ``deepseek`` name and is reached by an explicit ``provider_name``, so no
        # automatic route and no fallback can select a live provider.
        runtime = create_runtime(
            settings=RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock",),
                provider_fallback_chain=(),
                allow_paid_providers=False,
            )
        )
        runtime.agent_registry._agents["deepseek"] = ProviderAgent(provider)
        return runtime

    def test_40_a_plan_envelope_is_plan_ready_not_completed(self) -> None:
        _service, provider, result = self._dispatch(PLAN_ENVELOPE)
        response = result.response
        self.assertEqual(len(provider.requests), 1)
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.model_intent["intent_type"], "PLAN_READY")
        self.assertIs(response.model_intent["valid"], True)
        self.assertEqual(response.model_intent["next_action"], "confirm_plan")
        self.assertEqual(response.outcome, "PLAN_READY")
        self.assertNotEqual(response.state, "COMPLETED")
        self.assertFalse(response.project_created)
        self.assertFalse(response.project_files_created)
        self.assertEqual(response.files_created, 0)

    def test_41_a_clarification_envelope_supplies_the_questions(self) -> None:
        _service, _provider, result = self._dispatch(CLARIFICATION_ENVELOPE)
        response = result.response
        self.assertEqual(response.outcome, "CLARIFICATION_REQUIRED")
        self.assertTrue(response.needs_clarification)
        self.assertEqual(
            list(response.clarification_questions),
            [
                "Какая валюта должна быть в учёте?",
                "Нужен ли многопользовательский режим?",
            ],
        )
        self.assertEqual(response.model_intent["intent_type"], "CLARIFICATION_REQUIRED")

    def test_42_a_generation_without_real_writes_does_not_complete(self) -> None:
        # The answer declares two files, but this run is read-only: nothing is written.
        _service, provider, result = self._dispatch(GENERATE_ENVELOPE)
        response = result.response
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(response.model_intent["intent_type"], "GENERATE_PROJECT")
        self.assertIs(response.model_intent["valid"], True)
        self.assertEqual(response.files_created, 0)
        self.assertFalse(response.project_created)
        self.assertFalse(response.project_files_created)
        self.assertNotEqual(response.outcome, "PROJECT_COMPLETED")
        self.assertNotEqual(response.state, "COMPLETED")

    def test_43_a_malformed_envelope_does_not_stop_the_run(self) -> None:
        broken = '{"intent_type": "GENERATE_PROJECT", "summary": "x", "files": {'
        service, provider, result = self._dispatch(broken)
        response = result.response
        self.assertEqual(len(provider.requests), 1, "the run must continue")
        self.assertEqual(response.model_intent["intent_type"], "FINAL_ANSWER")
        self.assertIs(response.model_intent["valid"], False)
        self.assertEqual(
            response.model_intent["validation_errors"],
            [
                "invalid_json: the answer carries an intent envelope but no JSON "
                "object in it parses"
            ],
        )
        self.assertNotEqual(response.state, "COMPLETED")
        # The durable record agrees with the response for the outcome it stored.
        record = service.get_run_record(response.run_id)
        self.assertIsNotNone(record)
        self.assertEqual(
            record["project_state"]["outcome"], response.outcome
        )

    def test_44_the_explicit_provider_and_model_survive(self) -> None:
        for answer in (PLAN_ENVELOPE, GENERATE_ENVELOPE, PLAIN_ANSWER):
            _service, provider, result = self._dispatch(answer)
            response = result.response
            self.assertEqual(response.provider_used, "deepseek", answer[:40])
            self.assertEqual(response.model_used, "deepseek-chat", answer[:40])
            self.assertEqual(provider.requests[0].model_name, "deepseek-chat")

    def test_45_a_plain_answer_is_unchanged_the_regression(self) -> None:
        _service, provider, result = self._dispatch(PLAIN_ANSWER)
        response = result.response
        self.assertEqual(len(provider.requests), 1)
        self.assertTrue(response.success, response.error)
        # Exactly the outcome this answer produced before structured intent existed.
        self.assertEqual(response.outcome, "SPECIFICATION_READY")
        self.assertEqual(response.state, "SPECIFICATION_READY")
        # And the new field reports the deterministic fallback, not a guess.
        self.assertEqual(response.model_intent["intent_type"], "FINAL_ANSWER")
        self.assertIs(response.model_intent["valid"], True)
        self.assertIs(response.model_intent["raw_text_preserved"], True)
        # The payload carries the row, so an HTTP caller sees it too.
        payload = response.to_dict()
        self.assertEqual(payload["model_intent"]["intent_type"], "FINAL_ANSWER")
        self.assertNotIn(PLAIN_ANSWER, json.dumps(payload["model_intent"]))


if __name__ == "__main__":
    unittest.main()
