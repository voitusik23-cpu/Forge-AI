"""The false-completion guard: a text answer is never a finished project.

The defect this file pins was measured by the owner. A short idea ("Хочу небольшое
локальное веб-приложение для учёта домашних расходов…") was answered by the model
with **clarifying questions**, and the UI reported ``COMPLETED`` / «Запись
сохранена: да» -- although no project existed, no file was written, the
specification was never confirmed and the questions were never answered.

Three independent things are asserted here, because the guard has three layers:

* :class:`ClassifierPrecedenceTests` -- the pure classifier
  (:func:`app.agent_runtime.outcome.derive_outcome`), rule by rule, including every
  single missing fact that must block ``PROJECT_COMPLETED``;
* :class:`ResponseGateTests` -- the run response assembled by
  :class:`app.api.service.ForgeApiService`, driven by a **fake provider** (no
  network, no paid call): a questions answer and a plain text answer are never
  reported as a completed project, and the four save indicators stay independent;
* :class:`ProjectCompletionGateTests` -- the positive path through the real
  project lifecycle: a real ``code_writer`` write, verified and accepted, does
  complete a project, so the guard is a gate and not a blanket refusal.

The fake provider is an ordinary ``Provider`` registered under the real
``deepseek`` name, exactly as ``tests/test_model_override_dispatch.py`` does, so the
production dispatcher, executor, run scope and response construction all run
unchanged and separately from this guard.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import unittest
from types import SimpleNamespace

from app.agent_runtime.code_writer import apply_changes
from app.agent_runtime.outcome import (
    RunOutcome,
    completion_blockers,
    derive_outcome,
    extract_clarifying_questions,
    honest_state,
)
from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import (
    Provider,
    ProviderRequest,
    ProviderResponse,
)
from app.agents.providers.config import ProviderConfig
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.config.settings import RuntimeSettings
from app.projects.models import ProjectStatus
from app.projects.service import ProjectService
from app.projects.store import ProjectStore
from app.runtime.bootstrap import create_runtime
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace

#: The shape of the owner's measured answer: a short list of clarifying questions.
QUESTIONS_ANSWER = """Прежде чем начинать, уточните, пожалуйста:
1. Какая валюта должна быть в учёте?
2. Нужен ли многопользовательский режим?
3. Нужен ли экспорт расходов в Excel?
"""

#: A perfectly ordinary successful text answer that produced no file.
TEXT_ANSWER = (
    "Приложение для учёта домашних расходов можно сделать как небольшое "
    "локальное веб-приложение: список трат, категории и месячный отчёт."
)

#: One written file, in the fenced form the guarded code writer parses.
WRITE_ANSWER = """```python path=expenses.py
def total(rows):
    return sum(row["amount"] for row in rows)
```
"""


class _QuestionAskingProvider(Provider):
    """A provider whose answer is a list of clarifying questions. No network."""

    def __init__(self, answer: str = QUESTIONS_ANSWER) -> None:
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


class _FakeSpecification:
    """Only ``to_dict`` is used, which is all the project layer requires."""

    def __init__(self, value: dict) -> None:
        self._value = dict(value)

    def to_dict(self) -> dict:
        return dict(self._value)


def _session(goal: str = "Небольшое приложение для учёта домашних расходов"):
    from app.projects.store import new_project_id

    return SimpleNamespace(
        session_id=f"disc-{new_project_id()[5:]}",
        initial_goal=goal,
        specification=_FakeSpecification(
            {
                "goal": goal,
                "project_type": "web app",
                "core_features": ["add expenses", "monthly report"],
            }
        ),
    )


# --------------------------------------------------------------------------- #
# 1. the classifier, rule by rule
# --------------------------------------------------------------------------- #


class ClassifierPrecedenceTests(unittest.TestCase):
    """Every rule of ``derive_outcome`` and the precedence between them."""

    def test_01_a_provider_error_wins_over_everything(self) -> None:
        self.assertIs(
            derive_outcome(
                run_state="COMPLETED",
                run_success=True,
                error="provider exploded",
                answer_text=QUESTIONS_ANSWER,
                project_exists=True,
                project_workspace_exists=True,
                artifact_count=3,
                artifact_manifest=("a.py",),
                verification="passed",
                acceptance="passed",
                specification_confirmed=True,
            ),
            RunOutcome.FAILED,
        )

    def test_02_a_settled_unsuccessful_run_is_failed(self) -> None:
        self.assertIs(
            derive_outcome(run_state="FAILED", run_success=False),
            RunOutcome.FAILED,
        )
        self.assertIs(
            derive_outcome(run_state="COMPLETED", run_success=False),
            RunOutcome.FAILED,
        )

    def test_03_a_question_list_is_clarification_required(self) -> None:
        questions = extract_clarifying_questions(QUESTIONS_ANSWER)
        self.assertEqual(len(questions), 3)
        self.assertNotIn("1.", questions[0])
        self.assertIs(
            derive_outcome(
                run_state="COMPLETED", run_success=True, answer_text=QUESTIONS_ANSWER
            ),
            RunOutcome.CLARIFICATION_REQUIRED,
        )

    def test_04_one_trailing_question_is_not_a_clarification_list(self) -> None:
        answer = "Готово: локальное приложение для расходов. Подходит ли такой вариант?"
        # The line *is* a question, so it is extracted ...
        self.assertEqual(len(extract_clarifying_questions(answer)), 1)
        # ... but one question is not a list, and it does not stop the run.
        self.assertIsNot(
            derive_outcome(run_state="COMPLETED", run_success=True, answer_text=answer),
            RunOutcome.CLARIFICATION_REQUIRED,
        )

    def test_05_a_discovery_session_still_asking_is_clarification_required(self) -> None:
        self.assertIs(
            derive_outcome(
                run_state="COMPLETED",
                run_success=True,
                answer_text=TEXT_ANSWER,
                discovery_state="NEEDS_CLARIFICATION",
            ),
            RunOutcome.CLARIFICATION_REQUIRED,
        )

    def test_06_without_a_project_the_work_is_a_ready_specification_or_plan(self) -> None:
        self.assertIs(
            derive_outcome(
                run_state="COMPLETED", run_success=True, answer_text=TEXT_ANSWER
            ),
            RunOutcome.SPECIFICATION_READY,
        )
        self.assertIs(
            derive_outcome(
                run_state="COMPLETED",
                run_success=True,
                answer_text=TEXT_ANSWER,
                plan_exists=True,
                plan_awaiting_confirmation=True,
            ),
            RunOutcome.PLAN_READY,
        )
        # A project record that exists without a confirmed specification is still
        # not an executable project.
        self.assertIs(
            derive_outcome(
                run_state="COMPLETED",
                run_success=True,
                project_exists=True,
                project_workspace_exists=True,
                specification_confirmed=False,
            ),
            RunOutcome.SPECIFICATION_READY,
        )

    def test_07_an_in_flight_run_is_execution_running(self) -> None:
        self.assertIs(
            derive_outcome(
                run_state="RUNNING",
                run_success=None,
                project_exists=True,
                project_workspace_exists=True,
                specification_confirmed=True,
            ),
            RunOutcome.EXECUTION_RUNNING,
        )

    def test_08_unverified_work_needs_verification(self) -> None:
        for verification in ("", "not_run", "pending"):
            self.assertIs(
                derive_outcome(
                    run_state="COMPLETED",
                    run_success=True,
                    project_exists=True,
                    project_workspace_exists=True,
                    artifact_count=1,
                    artifact_manifest=("a.py",),
                    verification=verification,
                    acceptance="passed",
                    specification_confirmed=True,
                ),
                RunOutcome.VERIFICATION_REQUIRED,
                verification,
            )

    def test_09_a_failed_verification_or_acceptance_needs_changes(self) -> None:
        base = dict(
            run_state="COMPLETED",
            run_success=True,
            project_exists=True,
            project_workspace_exists=True,
            artifact_count=1,
            artifact_manifest=("a.py",),
            specification_confirmed=True,
        )
        self.assertIs(
            derive_outcome(**{**base, "verification": "failed", "acceptance": "passed"}),
            RunOutcome.NEEDS_CHANGES,
        )
        self.assertIs(
            derive_outcome(**{**base, "verification": "passed", "acceptance": "failed"}),
            RunOutcome.NEEDS_CHANGES,
        )
        self.assertIs(
            derive_outcome(**{**base, "verification": "not_applicable", "acceptance": "passed"}),
            RunOutcome.NEEDS_CHANGES,
        )

    def test_10_completion_needs_every_single_fact(self) -> None:
        complete = dict(
            run_state="COMPLETED",
            run_success=True,
            project_exists=True,
            project_workspace_exists=True,
            artifact_count=1,
            artifact_manifest=("a.py",),
            verification="passed",
            acceptance="passed",
            specification_confirmed=True,
        )
        self.assertIs(derive_outcome(**complete), RunOutcome.PROJECT_COMPLETED)
        # Each fact removed, one at a time, must alone block completion.
        for missing in (
            "project_exists",
            "project_workspace_exists",
            "verification",
            "acceptance",
            "specification_confirmed",
        ):
            facts = dict(complete)
            facts[missing] = "" if missing in {"verification", "acceptance"} else False
            self.assertIsNot(
                derive_outcome(**facts),
                RunOutcome.PROJECT_COMPLETED,
                f"a run missing {missing} was reported as a completed project",
            )
        # An empty artifact list can never be a completed project unless the run
        # explicitly states that it produced no file *and* the project says so.
        self.assertIsNot(
            derive_outcome(
                **{
                    **complete,
                    "artifact_count": 0,
                    "artifact_manifest": (),
                }
            ),
            RunOutcome.PROJECT_COMPLETED,
        )

    def test_11_success_alone_never_completes(self) -> None:
        self.assertIsNot(
            derive_outcome(run_state="COMPLETED", run_success=True),
            RunOutcome.PROJECT_COMPLETED,
        )

    def test_12_the_user_facing_state_never_says_completed_for_another_outcome(self) -> None:
        self.assertEqual(honest_state("COMPLETED", RunOutcome.SPECIFICATION_READY),
                         "SPECIFICATION_READY")
        self.assertEqual(honest_state("COMPLETED", RunOutcome.CLARIFICATION_REQUIRED),
                         "CLARIFICATION_REQUIRED")
        self.assertEqual(honest_state("COMPLETED", RunOutcome.PROJECT_COMPLETED),
                         "COMPLETED")
        # A failure is never rewritten into something softer.
        self.assertEqual(honest_state("FAILED", RunOutcome.SPECIFICATION_READY), "FAILED")

    def test_13_the_lifecycle_gate_is_the_same_rule(self) -> None:
        # The lifecycle refuses the same facts the classifier refuses, so the two
        # layers cannot disagree.
        blockers = completion_blockers(
            run_state="COMPLETED",
            verification="not_run",
            acceptance="passed",
            artifact_count=1,
            wrote_files=True,
        )
        self.assertIn("verification_not_run", blockers)
        self.assertEqual(
            completion_blockers(
                run_state="COMPLETED",
                verification="passed",
                acceptance="passed",
                artifact_count=1,
                wrote_files=True,
            ),
            (),
        )


# --------------------------------------------------------------------------- #
# 2. the server-side gate, through a fake provider
# --------------------------------------------------------------------------- #


class ResponseGateTests(unittest.TestCase):
    """The run response and the run record agree, and neither claims a project."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-false-completion"
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

    def _service(self, provider: Provider) -> ForgeApiService:
        # Explicit settings, so no ``.env`` is consulted, and the only enabled
        # provider is the offline mock. The fake provider is registered under the
        # real ``deepseek`` name and reached by an explicit ``provider_name``, so no
        # fallback and no automatic route can select a live provider.
        runtime = create_runtime(
            settings=RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock",),
                provider_fallback_chain=(),
                allow_paid_providers=False,
            )
        )
        runtime.agent_registry._agents["deepseek"] = ProviderAgent(provider)
        return ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.run_root),
            authorized_project_id="false-completion-project",
        )

    def _dispatch(self, answer: str):
        provider = _QuestionAskingProvider(answer)
        service = self._service(provider)
        result = service.run_task_detailed(
            TaskRunRequest(
                description="Хочу небольшое локальное веб-приложение для учёта домашних расходов",
                category="analysis",
                provider_name="deepseek",
                model_name="deepseek-chat",
            )
        )
        return service, provider, result

    def test_20_a_questions_answer_is_clarification_required_not_completed(self) -> None:
        service, provider, result = self._dispatch(QUESTIONS_ANSWER)
        response = result.response
        self.assertEqual(len(provider.requests), 1, "the fake provider was not asked")
        # The provider really answered and the run really succeeded ...
        self.assertTrue(response.success, response.error)
        # ... and that is exactly why this guard matters: success is not completion.
        self.assertEqual(response.outcome, "CLARIFICATION_REQUIRED")
        self.assertNotEqual(response.state, "COMPLETED")
        self.assertTrue(response.needs_clarification)
        self.assertEqual(len(response.clarification_questions), 3)
        self.assertTrue(
            all(item.endswith("?") for item in response.clarification_questions)
        )
        self.assertEqual(response.files_created, 0)
        self.assertFalse(response.project_created)
        self.assertFalse(response.project_files_created)
        self.assertFalse(response.artifact_manifest_saved)
        self.assertTrue(response.run_record_saved)

        # No project record was produced, so no project status can be COMPLETED.
        projects = ProjectService(ProjectStore(root=self.base / "projects"))
        self.assertEqual(projects.list_projects(), ())
        self.assertNotEqual(
            response.state, ProjectStatus.COMPLETED.value
        )

        # The durable record agrees: it carries the outcome and the questions.
        record = service.get_run_record(response.run_id)
        self.assertIsNotNone(record)
        project_state = record["project_state"]
        self.assertEqual(project_state["outcome"], "CLARIFICATION_REQUIRED")
        self.assertIs(project_state["needs_clarification"], True)
        self.assertEqual(
            project_state["clarification_questions"], list(response.clarification_questions)
        )
        self.assertIs(project_state["run_record_saved"], True)
        self.assertIs(project_state["project_created"], False)
        self.assertEqual(project_state["files_created"], 0)
        self.assertEqual(record["outcome"], "CLARIFICATION_REQUIRED")

    def test_21_a_text_answer_without_files_is_never_completed(self) -> None:
        for answer in (TEXT_ANSWER, "", "  "):
            _service, _provider, result = self._dispatch(answer)
            response = result.response
            self.assertNotEqual(
                response.outcome,
                "PROJECT_COMPLETED",
                f"a text-only answer was reported as a completed project: {answer!r}",
            )
            self.assertNotEqual(response.state, "COMPLETED")
            self.assertEqual(response.files_created, 0)
            self.assertFalse(response.project_created)
            self.assertFalse(response.project_files_created)
            # A real answer reaches "the specification is ready"; an empty answer is
            # a failed run, and neither is a project.
            if answer.strip():
                self.assertIn(
                    response.outcome,
                    ("SPECIFICATION_READY", "CLARIFICATION_REQUIRED"),
                    response.outcome,
                )

    def test_22_an_empty_workspace_is_never_a_completed_project(self) -> None:
        # A project whose workspace exists but holds nothing: every save indicator
        # must say so, and the classifier must refuse completion on the facts alone.
        self.assertIs(
            derive_outcome(
                run_state="COMPLETED",
                run_success=True,
                project_exists=True,
                project_workspace_exists=True,
                artifact_count=0,
                artifact_manifest=(),
                verification="passed",
                acceptance="passed",
                specification_confirmed=True,
            ),
            RunOutcome.NEEDS_CHANGES,
        )
        # The same facts through the service's post-attach gate: a project record
        # that is not completed can never be reported as completed.
        service = self._service(_QuestionAskingProvider(TEXT_ANSWER))
        fields = service.record_project_attach(
            "run-empty-workspace",
            project_status="NEEDS_CHANGES",
            workspace_exists=True,
            files=(),
            verification="passed",
            acceptance="passed",
            run_state="COMPLETED",
            run_success=True,
        )
        self.assertNotEqual(fields["outcome"], "PROJECT_COMPLETED")
        self.assertEqual(fields["files_created"], 0)
        self.assertFalse(fields["project_files_created"])
        self.assertFalse(fields["artifact_manifest_saved"])


# --------------------------------------------------------------------------- #
# 3. the positive path: a real write, verified and accepted, completes
# --------------------------------------------------------------------------- #


class ProjectCompletionGateTests(unittest.TestCase):
    """The gate admits a genuinely completed project, so it is not a blanket no."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-false-completion"
            / f"{os.getpid()}-project-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.store = ProjectStore(root=base / "projects")
        self.projects = ProjectService(self.store)
        self.run_workspace = base / "run-workspace"
        self.run_workspace.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

    def test_30_a_real_write_that_passes_verification_and_acceptance_completes(self) -> None:
        # 1. A REAL write through the guarded code writer, in its own workspace.
        write = apply_changes(
            WRITE_ANSWER,
            workspace=Workspace(self.run_workspace),
            run_id="run-positive-1",
        )
        self.assertEqual(write.status, "applied")
        self.assertEqual(write.verification, "passed")
        self.assertEqual(len(write.files), 1)

        # 2. The run is attached to a project that goes through the real lifecycle.
        project = self.projects.create_from_discovery(_session())
        project = self.projects.confirm(project.project_id)
        record = {
            "run_id": "run-positive-1",
            "state": "COMPLETED",
            "status": "COMPLETED",
            "classification": {
                "outcome": "TERMINAL_SUCCESS",
                "is_success": True,
                "is_terminal": True,
            },
            "acceptance": "passed",
            "project_state": {
                "code_write": {
                    "status": write.status,
                    "verification": write.verification,
                    "files": [dict(item) for item in write.files],
                }
            },
        }
        attached = self.projects.attach_run(
            project.project_id, record, source_root=self.run_workspace
        )
        self.assertIs(attached.status, ProjectStatus.COMPLETED)
        version = attached.current()
        self.assertIsNotNone(version)
        self.assertEqual(len(version.files), 1)
        self.assertEqual(version.verification, "passed")
        self.assertEqual(version.acceptance, "passed")

        # 3. The classifier, given the project's own facts, agrees.
        self.assertIs(
            derive_outcome(
                run_state="COMPLETED",
                run_success=True,
                project_exists=True,
                project_workspace_exists=bool(attached.workspace_id),
                artifact_count=len(version.files),
                artifact_manifest=version.files,
                verification=version.verification,
                acceptance=version.acceptance,
                specification_confirmed=True,
            ),
            RunOutcome.PROJECT_COMPLETED,
        )
        # 4. And so does the response path's post-attach gate.
        service = ForgeApiService(
            runtime=create_runtime(
                settings=RuntimeSettings(
                    default_provider="mock",
                    enabled_providers=("mock",),
                    provider_fallback_chain=(),
                )
            ),
            run_store=RunStore(root=self.base / "runs"),
            authorized_project_id=project.project_id,
        )
        fields = service.record_project_attach(
            "run-positive-1",
            project_status=attached.status.value,
            workspace_exists=bool(attached.workspace_id),
            files=version.files,
            verification=version.verification,
            acceptance=version.acceptance,
            run_state="COMPLETED",
            run_success=True,
        )
        self.assertEqual(fields["outcome"], "PROJECT_COMPLETED")
        self.assertEqual(fields["state"], "COMPLETED")
        self.assertTrue(fields["project_created"])
        self.assertTrue(fields["project_files_created"])
        self.assertTrue(fields["artifact_manifest_saved"])
        self.assertEqual(fields["files_created"], 1)


if __name__ == "__main__":
    unittest.main()
