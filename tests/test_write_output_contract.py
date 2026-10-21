"""The app asks the model correctly, and asks exactly once more when it must.

The product problem these tests pin down is not a model problem. A model answered a
code task with a plan and the question "which option, A/B/C?" and Forge reported a
finished run with no files. Nobody using the app should have to know that a file has
to arrive inside a fenced block with a ``path=`` header, so the code path appends a
deterministic output contract to the prompt it assembles, and -- if an answer still
carries no file block at all -- asks **once** more with a shorter, harder
instruction and then stops.

Every test here is offline and deterministic: the provider is a local fake plugged in
at the executor seam, so the service's real provider branch, run scope, run store,
writer and response construction all run unchanged and only the model's text is
supplied by the test. No socket, no credential, no cost.

What is proved, one rule per group:

* the contract is appended for a write-mode code run and never rewrites the owner's
  own words;
* it states every fact the product requires: one fenced block per file with
  ``path=``, the allowed root, the forbidden ``.env``/secret/``.git`` paths, "only
  what the plan needs", and that a ``code`` run in write mode owes files;
* prose is retried once with the hard instruction, a plan with questions is retried
  once, a proper fenced file is **not** retried;
* the total number of provider dispatches is at most two, always;
* a retry that finally returns files really writes them.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import unittest
from dataclasses import replace

from app.agent_runtime.code_writer import carries_file_changes
from app.agent_runtime.output_contract import (
    MAX_WRITE_RETRIES,
    OUTPUT_CONTRACT,
    RETRY_INSTRUCTION,
    append_output_contract,
    build_retry_prompt,
)
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.orchestrator.models import Run, RunState, TaskResult
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

#: The owner's measured answer, in its essential shape: a plan and a question, no
#: file, no fenced block, no diff.
PROSE_ANSWER = (
    "I need to stop here and give you an honest status report. "
    "Экран проверки провайдеров можно сделать тремя способами. "
    "Какой вариант выбираем: A, B или C?"
)

#: A plan plus the numbered question list the clarification classifier recognises.
PLAN_AND_QUESTIONS = """План работы:
1. Добавить маршрут проверки провайдеров.
2. Добавить экран со статусами.
Уточните, пожалуйста:
1. Каких провайдеров нужно проверять?
2. Нужен ли запуск проверки по кнопке?
"""

#: One real file, in the fenced form the guarded writer parses.
FENCED_ANSWER = """Сделал один модуль.

```python path=api_screen.py
\"\"\"A deterministic module for the retry test.\"\"\"


def status_of(name):
    return "unknown" if not name else name
```
"""


class _ScriptedProviderExecutor:
    """A local fake for the provider dispatch: one canned answer per call.

    The last answer repeats if the service ever asked more times than the script
    has entries, so a runaway retry shows up as a call count rather than an
    ``IndexError`` that would hide it.
    """

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers) or [""]
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):  # noqa: ANN003 - mirrors the real executor signature
        self.calls.append(kwargs)
        task = kwargs["task"]
        index = min(len(self.calls) - 1, len(self.answers) - 1)
        run = Run(task=task, id=str(kwargs.get("run_id") or ""))
        run.state = RunState.COMPLETED
        run.result = TaskResult(
            task_id=task.id,
            success=True,
            output=self.answers[index],
            provider="fake-provider",
            model_name="fake/model",
        )
        return run

    @property
    def prompts(self) -> list[str]:
        return [str(call["task"].description) for call in self.calls]


class _ServiceCase(unittest.TestCase):
    """The real service path over a throwaway workspace and run store."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-write-output-contract"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.root = base / "workspace"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs = base / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.service = ForgeApiService(
            runtime=offline_runtime(),
            workspace=Workspace(root=self.root),
            run_store=RunStore(root=self.runs),
            authorized_project_id="write-contract-project",
        )

    def _install(self, *answers: str) -> _ScriptedProviderExecutor:
        fake = _ScriptedProviderExecutor(list(answers))
        self.service.runtime = replace(self.service.runtime, run_executor=fake)
        return fake

    def _request(self, **overrides) -> TaskRunRequest:
        payload = {
            "description": "создать безопасный экран проверки настроенных API-провайдеров",
            "category": "code",
            "provider_name": "fake-provider",
            "model_name": "fake/model",
            "project_id": "write-contract-project",
            "write_mode": True,
        }
        payload.update(overrides)
        return TaskRunRequest(**payload)

    def _files(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file()
        )

    def retry_fact_of(self, response) -> dict:
        """The ``project_state.write_retry`` record of the run, or ``{}`` when absent.

        The retry is deliberately *not* a ``TaskRunResponse`` field: that response is
        byte-pinned by the repository's contract tests, so the fact travels in the run's
        durable state beside ``code_write`` -- the same rule the provider key index
        follows.
        """

        record = self.service.get_run_record(response.run_id) or {}
        return (record.get("project_state") or {}).get("write_retry") or {}

    def assert_retry_recorded(self, response) -> dict:
        fact = self.retry_fact_of(response)
        self.assertIs(fact.get("attempted"), True, "the retry was not recorded")
        self.assertEqual(fact.get("attempts"), 1)
        self.assertEqual(fact.get("max_attempts"), MAX_WRITE_RETRIES)
        return fact

    def assert_retry_not_recorded(self, response) -> None:
        self.assertEqual(self.retry_fact_of(response), {})


# --------------------------------------------------------------------------- #
# 1. the contract text and the retry text, on their own
# --------------------------------------------------------------------------- #


class PromptContractTests(unittest.TestCase):
    """The two deterministic texts, and the read-only answer check they key on."""

    def test_01_the_contract_states_every_fact_the_product_requires(self) -> None:
        text = OUTPUT_CONTRACT
        # The answer shape: one fenced block per file, with a path header.
        self.assertIn("path=", text)
        self.assertIn("ровно один блок", text)
        # The rule the measured case broke: no plan or report instead of code.
        self.assertIn("Никаких вопросов", text)
        self.assertIn("A/B/C", text)
        self.assertIn("никаких отчётов вместо кода", text)
        # What to do when data is missing, and how many questions at most.
        self.assertIn("НЕ БОЛЕЕ 3 вопросов", text)
        self.assertIn("intent=CLARIFICATION_REQUIRED", text)
        # The allowed root, and the forbidden paths.
        self.assertIn("Разрешённый корень записи", text)
        self.assertIn("только относительные", text)
        self.assertIn(".env", text)
        self.assertIn("секрет", text)
        self.assertIn(".git", text)
        # Only what the plan needs, and that a code write run owes files.
        self.assertIn("только те файлы, которые нужны плану", text)
        self.assertIn("категории code в режиме записи", text)
        self.assertIn("ожидаются файлы", text)

    def test_02_appending_is_additive_idempotent_and_never_rewrites_the_task(self) -> None:
        task_text = "создать безопасный экран проверки настроенных API-провайдеров"
        prompt = append_output_contract(f"{task_text}\n\nWorkspace context: (none)")
        # The owner's own words come first, unchanged, and are not replaced.
        self.assertTrue(prompt.startswith(task_text))
        self.assertIn(task_text, prompt)
        # Appending twice does not double the contract.
        self.assertEqual(
            append_output_contract(prompt).count(OUTPUT_CONTRACT), 1
        )
        # Nothing is appended to nothing: an empty prompt stays empty.
        self.assertEqual(append_output_contract(""), "")
        self.assertEqual(append_output_contract("   "), "   ")

    def test_03_the_retry_text_is_shorter_and_harder(self) -> None:
        retry = build_retry_prompt("Workspace context (bounded file list):\n- app.py")
        self.assertIn("Ты не вернул ни одного файла", retry)
        self.assertIn("Верни только блоки с path=", retry)
        self.assertIn("- app.py", retry, "the retry must still name real files")
        # The retry is not the full contract: it drops the task and the long prose.
        self.assertNotIn(OUTPUT_CONTRACT, retry)
        self.assertLess(len(retry), len(OUTPUT_CONTRACT) + 200)
        # With no context the instruction still stands alone.
        self.assertEqual(build_retry_prompt(""), RETRY_INSTRUCTION)

    def test_04_the_one_retry_is_one(self) -> None:
        self.assertEqual(MAX_WRITE_RETRIES, 1)

    def test_05_carries_file_changes_reads_the_answer_and_writes_nothing(self) -> None:
        self.assertFalse(carries_file_changes(PROSE_ANSWER))
        self.assertFalse(carries_file_changes(""))
        self.assertFalse(carries_file_changes(None))
        self.assertTrue(carries_file_changes(FENCED_ANSWER))
        # A fenced block with no path is a code sample in prose, not a change.
        self.assertFalse(carries_file_changes("```python\nprint(1)\n```\n"))
        # A unified diff is a change as well.
        self.assertTrue(
            carries_file_changes(
                "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
            )
        )


# --------------------------------------------------------------------------- #
# 2. the contract reaches the prompt the model is given
# --------------------------------------------------------------------------- #


class PromptReachesTheModelTests(_ServiceCase):
    def test_06_a_write_mode_prompt_carries_the_contract_and_the_context(self) -> None:
        fake = self._install(FENCED_ANSWER)
        outcome = self.service.run_task_detailed(self._request())

        self.assertEqual(len(fake.calls), 1, "a proper file answer must not be retried")
        prompt = fake.prompts[0]
        self.assertIn(
            "создать безопасный экран проверки настроенных API-провайдеров", prompt
        )
        self.assertIn(OUTPUT_CONTRACT, prompt)
        self.assertIn("Workspace context", prompt)
        # The contract is the last thing the model reads: it decides the shape.
        self.assertTrue(prompt.rstrip().endswith(OUTPUT_CONTRACT.rstrip()))
        record = self.service.get_run_record(outcome.response.run_id) or {}
        self.assertNotIn("write_retry", record.get("project_state") or {})

    def test_07_a_run_that_does_not_write_keeps_its_prompt_byte_for_byte(self) -> None:
        fake = self._install(FENCED_ANSWER)
        self.service.run_task_detailed(self._request(write_mode=False))

        self.assertEqual(len(fake.calls), 1)
        self.assertNotIn(OUTPUT_CONTRACT, fake.prompts[0])
        self.assertEqual(
            fake.prompts[0],
            "создать безопасный экран проверки настроенных API-провайдеров",
        )

    def test_08_an_analysis_category_keeps_its_prompt_byte_for_byte(self) -> None:
        fake = self._install(PROSE_ANSWER)
        self.service.run_task_detailed(self._request(category="analysis"))

        # No write mode, so no contract and no retry: the answer stands as it is.
        self.assertEqual(len(fake.calls), 1)
        self.assertNotIn(OUTPUT_CONTRACT, fake.prompts[0])


# --------------------------------------------------------------------------- #
# 3. the retry rule: exactly one, and only for an answer with no file block
# --------------------------------------------------------------------------- #


class RetryRuleTests(_ServiceCase):
    def test_09_prose_is_retried_once_with_the_hard_instruction(self) -> None:
        fake = self._install(PROSE_ANSWER, PROSE_ANSWER)
        outcome = self.service.run_task_detailed(self._request())
        response = outcome.response

        self.assertEqual(len(fake.calls), 2, "prose must be asked exactly once more")
        self.assertIn(OUTPUT_CONTRACT, fake.prompts[0])
        retry_prompt = fake.prompts[1]
        self.assertIn(RETRY_INSTRUCTION, retry_prompt)
        self.assertNotIn(OUTPUT_CONTRACT, retry_prompt)
        self.assertNotIn("создать безопасный экран", retry_prompt)

        # The retry is recorded as a fact, not inferred from the call count.
        retry_fact = self.assert_retry_recorded(response)
        record = self.service.get_run_record(response.run_id)
        self.assertIsNotNone(record)
        self.assertIn("no fenced block", retry_fact["reason"])

        # Nothing was written and nothing is claimed.
        self.assertFalse(response.success)
        self.assertEqual(response.files_created, 0)
        self.assertEqual(self._files(), [])
        self.assertNotEqual(response.outcome, "PROJECT_COMPLETED")
        self.assertEqual(
            record["project_state"]["code_write"]["status"], "no_changes"
        )

    def test_10_a_plan_with_questions_is_retried_and_the_final_answer_decides(
        self,
    ) -> None:
        fake = self._install(PLAN_AND_QUESTIONS, PLAN_AND_QUESTIONS)
        outcome = self.service.run_task_detailed(self._request())
        response = outcome.response

        self.assertEqual(len(fake.calls), 2)
        self.assert_retry_recorded(response)
        self.assertEqual(response.files_created, 0)
        # The questions the card shows are the ones the final answer asked, and they
        # survive the write failure: an answer that asked instead of writing is the
        # measured case, so the questions must still be listed.
        self.assertEqual(len(response.clarification_questions), 2)
        self.assertNotEqual(response.outcome, "PROJECT_COMPLETED")

    def test_11_a_proper_fenced_file_is_not_retried_and_is_really_written(
        self,
    ) -> None:
        fake = self._install(FENCED_ANSWER)
        outcome = self.service.run_task_detailed(self._request())
        response = outcome.response

        self.assertEqual(len(fake.calls), 1, "a file answer must not be retried")
        self.assert_retry_not_recorded(response)
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.files_created, 1)
        self.assertEqual(self._files(), ["api_screen.py"])
        self.assertIn(
            "status_of", (self.root / "api_screen.py").read_text(encoding="utf-8")
        )
        record = self.service.get_run_record(response.run_id)
        self.assertEqual(
            record["project_state"]["code_write"]["status"], "applied"
        )
        self.assertEqual(
            record["project_state"]["code_write"]["verification"], "passed"
        )
        self.assertNotIn("write_retry", record["project_state"])

    def test_12_a_retry_that_returns_files_writes_them(self) -> None:
        fake = self._install(PROSE_ANSWER, FENCED_ANSWER)
        outcome = self.service.run_task_detailed(self._request())
        response = outcome.response

        self.assertEqual(len(fake.calls), 2)
        self.assert_retry_recorded(response)
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.files_created, 1)
        self.assertEqual(self._files(), ["api_screen.py"])

    def test_13_the_retry_never_becomes_a_loop(self) -> None:
        # Three prose answers in a row: the service must stop at two dispatches.
        fake = self._install(PROSE_ANSWER, PROSE_ANSWER, PROSE_ANSWER)
        outcome = self.service.run_task_detailed(self._request())

        self.assertEqual(
            len(fake.calls), 1 + MAX_WRITE_RETRIES, "the retry must be capped at one"
        )
        self.assertFalse(outcome.response.success)
        self.assertEqual(self._files(), [])

    def test_14_an_empty_answer_is_not_retried(self) -> None:
        # An empty answer is not "no file block in a plan": there is nothing to ask
        # again about, and the writer's own honest outcome stands.
        fake = self._install("")
        outcome = self.service.run_task_detailed(self._request())

        self.assertEqual(len(fake.calls), 1)
        self.assert_retry_not_recorded(outcome.response)
        self.assertFalse(outcome.response.success)
        self.assertEqual(self._files(), [])


if __name__ == "__main__":  # pragma: no cover - convenience only
    unittest.main()
