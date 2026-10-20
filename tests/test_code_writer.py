"""The guarded code-writing path: a model answer becomes applied, verified files.

This is the capability the product did not have: before
``app/agent_runtime/code_writer.py`` existed, no path turned a model answer into a
change on disk - the offline harness is read-only and no executor applied model
output. These tests pin the rails, not only the happy path:

* both supported answer shapes really write the right bytes (a fenced block with a
  path header, and a unified diff);
* a path escape, a deny-listed path, and a cap violation are **refused** and write
  nothing at all;
* every modified file is backed up before it is touched;
* a Python file that fails ``py_compile`` rolls back **every** file of the run, so
  a broken tree is never left behind;
* an answer with no code is ``no_changes`` - never a guess;
* the returned diff is the real change, with exact hunk lines;
* write mode on the service path persists its status and diff in the run record,
  and claims success only when a change was applied and verified.

No network, no model call, no shell: every workspace is a temporary directory and
the provider is a local fake.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import unittest
from dataclasses import replace

from app.agent_runtime.code_writer import (
    MAX_FILES,
    MAX_TOTAL_BYTES,
    STATUS_APPLIED,
    STATUS_NO_CHANGES,
    STATUS_REJECTED,
    STATUS_VERIFICATION_FAILED,
    apply_changes,
    build_workspace_context,
    deny_reason,
    render_write_report,
)
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.orchestrator.models import Run, RunState, TaskResult
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime


class _TempWorkspaceCase(unittest.TestCase):
    """One throwaway workspace per test, and no artifact left behind."""

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-code-writer"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.root = base / "workspace"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.workspace = Workspace(root=self.root)
        self.run_id = "run-api-codewrite01"

    # -- helpers ---------------------------------------------------------- #

    def _files(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file()
        )

    def _temp_leftovers(self) -> list[str]:
        return [path for path in self._files() if path.endswith(".forge-new")]

    def _read(self, relative: str) -> str:
        with (self.root / relative).open("r", encoding="utf-8", newline="") as stream:
            return stream.read()

    def _write(self, relative: str, text: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as stream:
            stream.write(text)

    def _apply(self, answer: str, run_id: str | None = None):
        return apply_changes(
            answer,
            workspace=self.workspace,
            run_id=run_id or self.run_id,
        )


class FencedBlockParsingTests(_TempWorkspaceCase):
    def test_01_a_fenced_block_with_a_path_creates_the_exact_file(self) -> None:
        answer = (
            "Here is the tool you asked for.\n\n"
            "```python path=tools/foo.py\n"
            "def greet(name: str) -> str:\n"
            '    return f"hello {name}"\n'
            "```\n"
        )
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_APPLIED, result.reason)
        self.assertEqual(
            self._read("tools/foo.py"),
            'def greet(name: str) -> str:\n    return f"hello {name}"\n',
        )
        self.assertEqual(
            result.files,
            (
                {
                    "path": "tools/foo.py",
                    "action": "create",
                    "added": 2,
                    "removed": 0,
                },
            ),
        )
        self.assertEqual(result.verification, "passed")
        self.assertEqual(self._temp_leftovers(), [])

    def test_02_a_preceding_file_marker_also_names_the_path(self) -> None:
        answer = (
            "**File: docs/notes.txt**\n"
            "```\n"
            "one line\n"
            "```\n"
        )
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_APPLIED, result.reason)
        self.assertEqual(self._read("docs/notes.txt"), "one line\n")

    def test_03_a_fenced_block_without_a_path_is_not_a_change(self) -> None:
        result = self._apply("```python\nprint('a sample, not a target')\n```\n")

        self.assertEqual(result.status, STATUS_NO_CHANGES)
        self.assertEqual(self._files(), [])


class UnifiedDiffParsingTests(_TempWorkspaceCase):
    def test_04_a_unified_diff_updates_the_file_correctly(self) -> None:
        self._write("notes.txt", "alpha\nbeta\ngamma\n")
        answer = (
            "Applied as requested.\n\n"
            "--- a/notes.txt\n"
            "+++ b/notes.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " alpha\n"
            "-beta\n"
            "+BETA\n"
            " gamma\n"
            "\nThat is the whole change.\n"
        )
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_APPLIED, result.reason)
        self.assertEqual(self._read("notes.txt"), "alpha\nBETA\ngamma\n")
        self.assertEqual(
            result.files,
            (
                {
                    "path": "notes.txt",
                    "action": "update",
                    "added": 1,
                    "removed": 1,
                },
            ),
        )

    def test_05_a_unified_diff_creates_a_file_from_dev_null(self) -> None:
        answer = (
            "--- /dev/null\n"
            "+++ b/made/hello.txt\n"
            "@@ -0,0 +1,2 @@\n"
            "+first\n"
            "+second\n"
        )
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_APPLIED, result.reason)
        self.assertEqual(self._read("made/hello.txt"), "first\nsecond\n")

    def test_06_a_diff_whose_context_does_not_match_is_refused(self) -> None:
        self._write("notes.txt", "alpha\nbeta\ngamma\n")
        answer = (
            "--- a/notes.txt\n"
            "+++ b/notes.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " alpha\n"
            "-delta\n"
            "+DELTA\n"
            " gamma\n"
        )
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_REJECTED)
        self.assertIn("diff_context_mismatch", result.reason)
        self.assertEqual(self._read("notes.txt"), "alpha\nbeta\ngamma\n")


class PathRailsTests(_TempWorkspaceCase):
    def test_07_path_traversal_and_absolute_paths_are_refused(self) -> None:
        for path in ("../escape.txt", "a/../../escape.txt", "C:/Windows/escape.txt"):
            with self.subTest(path=path):
                result = self._apply(f"```text path={path}\nwritten\n```\n")
                self.assertEqual(result.status, STATUS_REJECTED, path)
                self.assertIn("invalid_path", result.reason)
                self.assertEqual(self._files(), [])
        self.assertFalse((self.root.parent / "escape.txt").exists())

    def test_08_deny_listed_paths_are_refused(self) -> None:
        cases = {
            ".env": "denied_environment_file",
            ".env.local": "denied_environment_file",
            ".git/config": "denied_directory:.git",
            ".venv/lib/site.py": "denied_directory:.venv",
            "node_modules/pkg/index.js": "denied_directory:node_modules",
            "dist/bundle.js": "denied_directory:dist",
            "app/security/authorizer.py": "denied_file:authorizer.py",
            "app/platform/persistence/postgres/migrations/0017_x.sql": (
                "denied_migration_history"
            ),
            "config/api_token.txt": "denied_credential_like_name",
            "config/client_secret.json": "denied_credential_like_name",
            "deploy/id_rsa": "denied_secret_file:credential_file_name",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                result = self._apply(f"```text path={path}\ncontent\n```\n")
                self.assertEqual(result.status, STATUS_REJECTED, path)
                self.assertIn(expected, result.reason, path)
                self.assertEqual(self._files(), [])

    def test_09_the_deny_list_is_a_pure_function_of_the_path(self) -> None:
        self.assertIsNone(deny_reason("app/api/service.py"))
        self.assertEqual(deny_reason(".env"), "denied_environment_file")
        self.assertEqual(deny_reason("docs/.env.example"), "denied_environment_file")
        self.assertEqual(
            deny_reason("app/platform/persistence/postgres/migrations/0001_a.sql"),
            "denied_migration_history",
        )


class CapTests(_TempWorkspaceCase):
    def test_10_the_file_cap_is_enforced_and_nothing_is_written(self) -> None:
        answer = "".join(
            f"```text path=f{index}.txt\nhello {index}\n```\n"
            for index in range(MAX_FILES + 1)
        )
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_REJECTED)
        self.assertIn(f"file_cap_exceeded:{MAX_FILES}", result.reason)
        self.assertEqual(self._files(), [])

    def test_11_the_total_byte_cap_is_enforced_and_nothing_is_written(self) -> None:
        body = "x" * (MAX_TOTAL_BYTES + 1)
        result = self._apply(f"```text path=big.txt\n{body}\n```\n")

        self.assertEqual(result.status, STATUS_REJECTED)
        self.assertIn(f"total_bytes_cap_exceeded:{MAX_TOTAL_BYTES}", result.reason)
        self.assertEqual(self._files(), [])

    def test_12_exactly_at_the_caps_is_allowed(self) -> None:
        answer = "".join(
            f"```text path=ok{index}.txt\nok\n```\n" for index in range(MAX_FILES)
        )
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_APPLIED, result.reason)
        self.assertEqual(len(result.files), MAX_FILES)


class BackupAndRollbackTests(_TempWorkspaceCase):
    def test_13_an_updated_file_has_a_backup_of_its_previous_bytes(self) -> None:
        self._write("app/thing.py", "value = 1\n")
        answer = "```python path=app/thing.py\nvalue = 2\n```\n"
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_APPLIED, result.reason)
        backup = self.root / ".forge" / "backups" / self.run_id / "app" / "thing.py"
        self.assertTrue(backup.is_file(), "no backup was written for the updated file")
        self.assertEqual(backup.read_text(encoding="utf-8"), "value = 1\n")
        self.assertEqual(self._read("app/thing.py"), "value = 2\n")

    def test_14_a_failing_py_compile_rolls_back_every_file_of_the_run(self) -> None:
        self._write("kept.py", "kept = 'original'\n")
        answer = (
            "```text path=created.txt\nbrand new\n```\n"
            "```python path=kept.py\nkept = 'changed'\n```\n"
            "```python path=broken.py\ndef oops(:\n    pass\n```\n"
        )
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_VERIFICATION_FAILED, result.reason)
        self.assertEqual(result.verification, "failed")
        self.assertIn("broken.py", result.reason)
        # The updated file is restored from its backup and the created file is gone.
        self.assertEqual(self._read("kept.py"), "kept = 'original'\n")
        self.assertFalse((self.root / "created.txt").exists())
        self.assertFalse((self.root / "broken.py").exists())
        self.assertEqual(self._temp_leftovers(), [])

    def test_15_a_valid_run_leaves_no_temporary_or_bytecode_artifact(self) -> None:
        answer = "```python path=pkg/mod.py\nnumber = 41 + 1\n```\n"
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_APPLIED, result.reason)
        self.assertEqual(self._files(), ["pkg/mod.py"])


class NoChangesAndDiffTests(_TempWorkspaceCase):
    def test_16_an_answer_without_code_is_no_changes(self) -> None:
        for answer in ("", "   \n", "I would suggest creating a file, but I have not."):
            with self.subTest(answer=answer):
                result = self._apply(answer)
                self.assertEqual(result.status, STATUS_NO_CHANGES)
                self.assertEqual(result.files, ())
                self.assertEqual(result.diff, "")
                self.assertEqual(self._files(), [])

    def test_17_the_returned_diff_is_the_real_change_with_exact_hunk_lines(self) -> None:
        self._write("config.ini", "[a]\nkey = 1\n[b]\n")
        answer = (
            "--- a/config.ini\n"
            "+++ b/config.ini\n"
            "@@ -1,3 +1,3 @@\n"
            " [a]\n"
            "-key = 1\n"
            "+key = 2\n"
            " [b]\n"
        )
        result = self._apply(answer)

        self.assertEqual(result.status, STATUS_APPLIED, result.reason)
        self.assertEqual(
            result.diff,
            "--- a/config.ini\n"
            "+++ b/config.ini\n"
            "@@ -1,3 +1,3 @@\n"
            " [a]\n"
            "-key = 1\n"
            "+key = 2\n"
            " [b]\n",
        )
        self.assertEqual(self._read("config.ini"), "[a]\nkey = 2\n[b]\n")

    def test_18_the_report_never_invents_a_success(self) -> None:
        rejected = self._apply("```text path=.env\nSECRET=1\n```\n")
        self.assertEqual(rejected.status, STATUS_REJECTED)
        report = render_write_report(rejected)
        self.assertIn("CODE WRITER: rejected", report)
        self.assertNotIn("applied", report)


class ServiceWriteModeTests(unittest.TestCase):
    """Write mode on the real service path, with a local fake provider."""

    ANSWER = (
        "Created both files.\n\n"
        "```text path=hello.txt\nhello from forge\n```\n"
        "```python path=tools/demo_add.py\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "```\n"
    )

    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-code-writer"
            / f"{os.getpid()}-{id(self)}-service"
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
            authorized_project_id="code-writer-project",
        )

    def _install_fake_provider(self, answer: str) -> "_FakeProviderExecutor":
        """A deterministic stand-in for a live provider's answer.

        It plugs in at the executor seam, so the service's real provider branch,
        run scope, run store and response construction all run unchanged; only the
        model's text is supplied by the test.
        """

        fake = _FakeProviderExecutor(answer)
        self.service.runtime = replace(self.service.runtime, run_executor=fake)
        return fake

    def _request(self, **overrides) -> TaskRunRequest:
        payload = {
            "description": "Create hello.txt and tools/demo_add.py",
            "category": "code",
            "provider_name": "fake-provider",
            # REAL_PROVIDER_EXPLICIT requires the caller to name both the provider and
            # the model: the default here names the pair the fake executor reports, so
            # the write mode this suite exercises is the one a real caller would get.
            "model_name": "fake/model",
            "project_id": "code-writer-project",
            "write_mode": True,
        }
        payload.update(overrides)
        return TaskRunRequest(**payload)

    def test_19_write_mode_applies_the_answer_and_persists_it_in_the_run(self) -> None:
        fake = self._install_fake_provider(self.ANSWER)
        outcome = self.service.run_task_detailed(self._request())
        response = outcome.response

        self.assertEqual(len(fake.calls), 1, "the provider was not dispatched")
        self.assertTrue(response.success, response.error)
        self.assertIsNone(response.error)
        # The model was given the task plus a bounded workspace context.
        prompt = fake.calls[0]["task"].description
        self.assertIn("Create hello.txt and tools/demo_add.py", prompt)
        self.assertIn("Workspace context", prompt)

        self.assertEqual(
            (self.root / "hello.txt").read_text(encoding="utf-8"),
            "hello from forge\n",
        )
        self.assertEqual(
            (self.root / "tools" / "demo_add.py").read_text(encoding="utf-8"),
            "def add(a: int, b: int) -> int:\n    return a + b\n",
        )
        self.assertIn("CODE WRITER: applied", response.output)
        self.assertIn("+hello from forge", response.output)

        record = self.service.get_run_record(response.run_id)
        self.assertIsNotNone(record)
        payload = record["project_state"]["code_write"]
        self.assertEqual(payload["status"], "applied")
        self.assertEqual(payload["verification"], "passed")
        self.assertEqual(
            [item["path"] for item in payload["files"]],
            ["hello.txt", "tools/demo_add.py"],
        )
        self.assertIn("--- /dev/null", payload["diff"])
        self.assertIn("+    return a + b", payload["diff"])

    def test_20_write_mode_does_not_change_a_request_that_does_not_ask_for_it(self) -> None:
        self._install_fake_provider(self.ANSWER)
        outcome = self.service.run_task_detailed(self._request(write_mode=False))

        self.assertTrue(outcome.response.success, outcome.response.error)
        self.assertIn("Created both files.", outcome.response.output)
        self.assertNotIn("CODE WRITER", outcome.response.output)
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()), []
        )
        record = self.service.get_run_record(outcome.response.run_id)
        self.assertIsNotNone(record)
        self.assertNotIn("code_write", record.get("project_state") or {})

    def test_21_write_mode_is_not_a_success_when_nothing_was_applied(self) -> None:
        # The offline provider's deterministic plan text carries no code, so the
        # honest outcome is `no_changes`: success must be false.
        self.service.runtime = offline_runtime()
        outcome = self.service.run_task_detailed(
            self._request(provider_name="mock", write_mode=True)
        )

        self.assertFalse(outcome.response.success)
        self.assertIn("write mode", outcome.response.error or "")
        self.assertIn("CODE WRITER: no_changes", outcome.response.output)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), [])
        record = self.service.get_run_record(outcome.response.run_id)
        self.assertIsNotNone(record)
        self.assertEqual(
            record["project_state"]["code_write"]["status"], "no_changes"
        )

    def test_22_write_mode_is_refused_for_a_non_code_category(self) -> None:
        self._install_fake_provider(self.ANSWER)
        outcome = self.service.run_task_detailed(
            self._request(category="analysis", write_mode=True)
        )

        self.assertTrue(outcome.response.success, outcome.response.error)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), [])
        record = self.service.get_run_record(outcome.response.run_id)
        self.assertNotIn("code_write", record.get("project_state") or {})

    def test_23_the_workspace_context_lists_names_and_never_contents(self) -> None:
        (self.root / "visible.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=value\n", encoding="utf-8")
        (self.root / "secrets.json").write_text("{}\n", encoding="utf-8")

        context = build_workspace_context(Workspace(root=self.root))

        self.assertIn("- visible.py", context)
        self.assertNotIn(".env", context)
        self.assertNotIn("secrets.json", context)
        self.assertNotIn("a = 1", context, "a file's contents must not reach the prompt")

    def test_24_an_unavailable_provider_is_the_existing_honest_error_not_a_write(self) -> None:
        # No provider is enabled and the named one is not registered: the existing
        # dispatch failure stands, nothing is written, and write mode does not
        # force a provider on.
        outcome = self.service.run_task_detailed(
            self._request(provider_name="not-a-registered-provider", write_mode=True)
        )

        self.assertFalse(outcome.response.success)
        self.assertTrue(outcome.response.error)
        self.assertNotIn("CODE WRITER", outcome.response.output or "")
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), [])
        record = self.service.get_run_record(outcome.response.run_id)
        self.assertIsNotNone(record)
        self.assertNotIn("code_write", record.get("project_state") or {})


class _FakeProviderExecutor:
    """A local fake for the provider dispatch: it returns one canned answer."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):  # noqa: ANN003 - mirrors the real executor signature
        self.calls.append(kwargs)
        task = kwargs["task"]
        run = Run(task=task, id=str(kwargs.get("run_id") or ""))
        run.state = RunState.COMPLETED
        run.result = TaskResult(
            task_id=task.id,
            success=True,
            output=self.answer,
            provider="fake-provider",
            model_name="fake/model",
        )
        return run


if __name__ == "__main__":  # pragma: no cover - convenience only
    unittest.main()
