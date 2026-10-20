"""The beta path, proven end to end: sixteen steps and seven negative checks.

Two halves, both offline:

* :class:`BetaPathTests` walks the path the owner actually takes -- idea, questions,
  chip answers, specification, plan, confirmation, project, real files, verification,
  a real Python start, a preview that answers 200, the result, the stop, a new version,
  and the earlier version still byte-identical. The local runner is exercised for
  **real** (it is a local process, not a provider), the materialiser and the code
  writer are the real implementations, and no provider is called. The last step of this
  class is the honest-failure case: a script that exits non-zero is reported as failed.
* :class:`NegativeCheckTests` pins the seven refusals the beta must make: no files is
  not ``ГОТОВО``; provider text without files creates nothing; an unknown command is
  not started; a path outside the workspace is refused; a cancelled confirmation starts
  no process; a paid model without an explicit choice is refused; and no API-key value
  ever appears in a payload, a log, an event or a result.

The steps that cannot be deterministic here (a real provider round trip, a real paid
call, the Electron/desktop shell) are **not** faked into this file: they are reported
as manual evidence in the task report instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import types
import unittest
import urllib.request

from app.agent_runtime.code_writer import apply_changes
from app.agent_runtime.outcome import RunOutcome, derive_outcome
from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService, PaidModelRequiresConfirmation
from app.config.settings import RuntimeSettings
from app.discovery.models import DiscoveryStateConflict
from app.discovery.service import DiscoveryService, apply_specification
from app.discovery.store import DiscoveryStore
from app.planning.planner import Planner
from app.projects.entrypoint import (
    ENTRY_KIND_PYTHON_SCRIPT,
    REASON_NODE_MISSING,
    detect_entry_point,
    resolve_entry,
)
from app.projects.materialize import VERIFIED, materialize_into
from app.projects.presentation import NOT_CREATED, READY, build_project_presentation
from app.projects.preview import PreviewRegistry
from app.projects.runner import (
    REFUSED,
    RUN_BLOCKED,
    TERMINAL_STATES,
    LocalRunner,
    RunTarget,
)
from app.projects.service import ProjectService
from app.projects.store import ProjectStore, default_projects_root
from app.runtime.bootstrap import create_runtime
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRATCH = REPO_ROOT / ".forge" / "test-beta-path"

GOAL = (
    "Хочу небольшой веб-инструмент для учёта домашних расходов: добавление трат, "
    "категории и месячный отчёт."
)

#: The answer the materialiser writes. A stdlib server on port 0, so the operating
#: system picks the port and the supervisor has to observe it.
SERVER_ANSWER = """A tiny local server.

```python path=app.py
import http.server
import socketserver


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"BETA PATH OK\\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
    print("listening %d" % httpd.server_address[1], flush=True)
    httpd.serve_forever()
```
"""

#: The change for version 2: one more real file, written by the real code writer.
CHANGE_ANSWER = """The change adds one small module.

```python path=report_export.py
\"\"\"Export a month of expenses as CSV text.\"\"\"


def export_month(rows):
    lines = ["category,amount"]
    for category, amount in rows:
        lines.append(f"{category},{amount}")
    return "\\n".join(lines) + "\\n"
```
"""

FAILING_SCRIPT = 'print("about to fail", flush=True)\nraise SystemExit(3)\n'

#: A credential-shaped string this file uses to prove absence. It is never printed.
SYNTHETIC_KEY = "sk-" + "beta" * 12

#: The scratch project ids, in the one shape the store accepts (``proj-<12 hex>``).
FAILING_PROJECT = "proj-fa11ed000001"
NODE_PROJECT = "proj-000000000de1"
CMD_PROJECT = "proj-000000000c0d"
ESCAPE_PROJECT = "proj-000000000e5c"
CANCEL_PROJECT = "proj-000000000ca0"
KEY_PROJECT = "proj-00000000a1b2"
CARD_PROJECT = "proj-00000000ca4d"


class _AnsweringProvider(Provider):
    """A provider that returns a fixed answer. No socket, no key, no cost."""

    def __init__(
        self,
        answer: str,
        provider_name: str = "deepseek",
        model_name: str = "deepseek-chat",
    ) -> None:
        super().__init__(
            ProviderConfig(provider_name=provider_name, model_name=model_name)
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


def _offline_runtime():
    """A runtime whose only enabled provider is the offline one. No network."""

    return create_runtime(
        settings=RuntimeSettings(
            default_provider="mock",
            enabled_providers=("mock",),
            provider_fallback_chain=(),
            allow_paid_providers=False,
        ),
        test_mode=True,
    )


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    completed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, check=False
    )
    return str(pid) in (completed.stdout or b"").decode("utf-8", errors="replace")


def _answer_every_question(discovery: DiscoveryService, session):
    """Answer with canned "chip" text, then confirm. Real derived specification."""

    defaults = {
        "users": "один пользователь, домашний бюджет",
        "core_features": "добавление трат, категории, месячный отчёт",
        "platform": "веб-приложение в браузере",
        "integrations": "нет внешних интеграций",
        "data_storage": "локальный JSON-файл",
        "auth": "не требуется",
        "notifications": "не требуются",
        "constraints": "без внешних зависимостей",
        "acceptance_criteria": "трата сохраняется, отчёт строится за месяц",
        "priorities": "простота и офлайн-работа",
    }
    answers = [
        {
            "question_id": question.id,
            "text": defaults.get(question.field, f"уточнение по {question.field}"),
        }
        for question in session.questions
    ]
    if answers:
        session = discovery.answer(session.session_id, answers)
    if session.state.value == "NEEDS_CLARIFICATION":
        session = discovery.skip(session.session_id, "остальное не критично")
    return discovery.confirm(session.session_id)


def _record_for(run_id: str, write, *, provider: str = "stand-in") -> dict:
    """The run record ``attach_run`` reads, built from the writer's own verdict."""

    return {
        "run_id": run_id,
        "state": "COMPLETED",
        "status": "COMPLETED",
        "provider_used": provider,
        "model_used": "deterministic-stand-in",
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


class _BetaCase(unittest.TestCase):
    """A scratch store, a scratch discovery store and one runner per test."""

    def setUp(self) -> None:
        self.base = SCRATCH / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(self.base, ignore_errors=True)
        self.base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.project_root = self.base / "projects"
        self.store = ProjectStore(root=self.project_root)
        self.projects = ProjectService(self.store)
        self.previews = PreviewRegistry()
        self.addCleanup(self.previews.stop_all)
        self.runner = LocalRunner(
            store=self.store, previews=self.previews, timeout=60.0, startup_timeout=15.0
        )
        self.addCleanup(self.runner.stop_all)
        self.discovery = DiscoveryService(DiscoveryStore(root=self.base / "discovery"))
        self.run_root = self.base / "runs"
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._previous_run_root = os.environ.get("FORGE_RUN_STORE_ROOT")
        os.environ["FORGE_RUN_STORE_ROOT"] = str(self.run_root)
        self.addCleanup(self._restore_run_root)

    def _restore_run_root(self) -> None:
        if self._previous_run_root is None:
            os.environ.pop("FORGE_RUN_STORE_ROOT", None)
        else:
            os.environ["FORGE_RUN_STORE_ROOT"] = self._previous_run_root

    # ------------------------------------------------------------------ helpers
    def target_for(self, project_id: str, workspace: pathlib.Path, entry) -> RunTarget:
        return RunTarget(
            project_id=project_id,
            workspace=workspace,
            entry=entry,
            project_path=self.store.project_path_relative(project_id),
        )

    def wait_for(self, target: RunTarget, *, running: bool = False, limit: float = 30.0) -> dict:
        status = self.runner.status(target)
        deadline = time.time() + limit
        while time.time() < deadline:
            if running and status["state"] == "RUNNING" and status.get("port"):
                return status
            if not running and status["state"] in TERMINAL_STATES:
                return status
            time.sleep(0.1)
            status = self.runner.status(target)
        return status


# --------------------------------------------------------------------------- #
# 1. the sixteen steps
# --------------------------------------------------------------------------- #


class BetaPathTests(_BetaCase):
    """One path, sixteen asserted steps, in the order the owner walks it."""

    def test_30_the_sixteen_step_beta_path(self) -> None:
        steps: list[str] = []

        # ---------------------------------------------------------- 1. the idea
        session = self.discovery.start(GOAL)
        self.assertEqual(session.state.value, "NEEDS_CLARIFICATION")
        self.assertEqual(session.initial_goal, GOAL)
        steps.append("1. idea -> discovery session opened")

        # ----------------------------------------------------------- 2. questions
        self.assertGreater(len(session.questions), 0)
        first_question = session.questions[0]
        self.assertTrue(first_question.text.strip())
        steps.append(f"2. questions -> {len(session.questions)} asked")

        # ------------------------------------------------------- 3. chip answers
        answered = self.discovery.answer(
            session.session_id,
            [{"question_id": q.id, "text": "один пользователь"} for q in session.questions],
        )
        self.assertGreater(len(answered.answers), 0)
        steps.append(f"3. chip answers -> {len(answered.answers)} recorded")

        # ------------------------------------------------------ 4. specification
        session = _answer_every_question(self.discovery, session)
        specification = session.specification
        self.assertIsNotNone(specification)
        self.assertEqual(session.state.value, "CONFIRMED")
        steps.append("4. specification derived and confirmed")

        # --------------------------------------------------------------- 5. plan
        plan = Planner().create_plan(specification.goal)
        self.assertTrue(plan.plan_id)
        self.assertGreater(len(plan.tasks), 0)
        steps.append(f"5. plan -> {len(plan.tasks)} tasks")

        # ------------------------------------------------------- 6. confirmation
        project = self.projects.create_from_discovery(session, plan_ref=plan.plan_id)
        self.assertEqual(project.status.value, "WAITING_FOR_CONFIRMATION")
        project = self.projects.confirm(project.project_id, plan_ref=plan.plan_id)
        self.assertEqual(project.status.value, "PLANNED")
        self.assertEqual(project.plan_ref, plan.plan_id)
        steps.append("6. confirmation -> PLANNED")

        # ------------------------------------------------------ 7. project created
        workspace = self.store.workspace_path(project.project_id, create=True)
        self.assertTrue(workspace.is_dir())
        relative = self.store.project_path_relative(project.project_id)
        # The published path is the repository-relative workspace path, never an
        # absolute host path, and the record carries exactly that string.
        self.assertTrue(relative.endswith(f"{project.project_id}/workspace"), relative)
        self.assertEqual(project.project_path, relative)
        steps.append(f"7. project created -> {relative}")

        # ------------------------------------------------------------ 8. real files
        # The run writes into its own workspace through the guarded writer; the
        # attach below copies the artifacts into the project workspace, which is
        # exactly the product's flow. No provider is involved.
        task_text = apply_specification(specification, "создать локальный сервер")
        self.assertIn("создать локальный сервер", task_text)
        run_one_id = f"run-beta-{project.project_id}"
        run_workspace = self.base / "run-workspace-1"
        run_workspace.mkdir(parents=True, exist_ok=True)
        write = apply_changes(
            SERVER_ANSWER, workspace=Workspace(run_workspace), run_id=run_one_id
        )
        self.assertEqual(write.status, "applied", write.reason)
        written = sorted(item["path"] for item in write.files)
        self.assertIn("app.py", written)
        self.assertTrue((run_workspace / "app.py").is_file())
        # The attach is what makes them the project's files.
        project = self.projects.attach_run(
            project.project_id,
            _record_for(run_one_id, write),
            source_root=run_workspace,
        )
        self.assertEqual(project.status.value, "COMPLETED")
        self.assertTrue((workspace / "app.py").is_file())
        steps.append(f"8. real files -> {written} in the project workspace")

        # --------------------------------------------------------- 9. verification
        self.assertEqual(write.verification, "passed")
        resolution = resolve_entry(write.files, workspace=workspace)
        entry = resolution.entry
        self.assertIsNotNone(entry, resolution.reason)
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(entry.path, "app.py")
        self.assertTrue(entry.runnable)
        version_one = project.version(1)
        self.assertIsNotNone(version_one)
        self.assertEqual(version_one.verification, "passed")
        self.assertEqual(version_one.acceptance, "passed")
        steps.append(f"9. verification -> {write.verification}, entry {entry.kind}")

        target = self.target_for(project.project_id, workspace, entry)

        # ----------------------------------------------------- 10. python starts
        token = self.runner.confirm(target, confirm=True, digest=entry.digest)
        self.assertIsNotNone(token, "the shown digest must be approvable")
        started = self.runner.start(target, token)
        self.assertNotIn(started["state"], TERMINAL_STATES, started)
        self.assertEqual(started["entry_kind"], ENTRY_KIND_PYTHON_SCRIPT)
        steps.append(f"10. python start -> {started['state']} pid {started['pid']}")

        status = self.wait_for(target, running=True)
        self.assertEqual(status["state"], "RUNNING", status)
        self.assertTrue(status["serving"])
        self.assertGreater(int(status["port"] or 0), 0)

        # ------------------------------------------------------- 11. preview 200
        url = status["url"]
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        with urllib.request.urlopen(url, timeout=10) as response:
            code = int(response.status)
            body = response.read()
        self.assertEqual(code, 200)
        self.assertIn(b"BETA PATH OK", body)
        self.assertTrue(self.previews.is_running(project.project_id))
        steps.append(f"11. preview -> GET {url} {code}")

        # -------------------------------------------------------------- 12. result
        self.assertGreater(status["log"]["count"], 0)
        self.assertIn("listening", "\n".join(status["log"]["lines"]))
        self.assertNotIn(started["pid"], (None, 0))
        steps.append("12. result -> status, port and log published")

        # ---------------------------------------------------------------- 13. stop
        stopped = self.runner.stop(project.project_id)
        self.assertEqual(stopped["state"], "STOPPED", stopped)
        self.assertFalse(self.previews.is_running(project.project_id))
        self.assertFalse(_alive(status["pid"]), "the process must be gone after stop")
        steps.append("13. stop -> STOPPED, preview unregistered, process gone")

        # ------------------------------------- 14. a new version with a real change
        version_one_hashes = {
            path.name: _sha256(path)
            for path in sorted(workspace.iterdir())
            if path.is_file()
        }
        staged = self.projects.new_version(
            project.project_id, "добавить экспорт месячного отчёта в CSV"
        )
        self.assertEqual(staged.version, 2)
        self.assertIn("CSV", staged.change_request.text)
        change_workspace = self.base / "run-workspace-2"
        change_workspace.mkdir(parents=True, exist_ok=True)
        change_write = apply_changes(
            CHANGE_ANSWER,
            workspace=Workspace(change_workspace),
            run_id="run-beta-change",
        )
        self.assertEqual(change_write.status, "applied", change_write.reason)
        project = self.projects.attach_run(
            project.project_id,
            _record_for("run-beta-change", change_write),
            source_root=change_workspace,
        )
        steps.append(f"14. new version -> v{staged.version}, {len(change_write.files)} file(s)")

        # ------------------------------------------ 15. the old version is untouched
        first = project.version(1)
        self.assertIsNotNone(first)
        self.assertEqual(first.run_id, run_one_id)
        self.assertEqual(first.verification, "passed")
        self.assertEqual(first.acceptance, "passed")
        # Version 1's own artifact record still describes the bytes on disk.
        for artifact in first.files:
            on_disk = workspace / artifact.path
            self.assertTrue(on_disk.is_file(), artifact.path)
            self.assertEqual(_sha256(on_disk), artifact.sha256, artifact.path)
        # ... and the files themselves are byte-identical to the snapshot.
        for name, digest in version_one_hashes.items():
            self.assertEqual(_sha256(workspace / name), digest, name)
        steps.append("15. old version -> byte-identical, v1 still current record")

        # ------------------------------------- 16. a failing run is shown honestly
        failing_id = FAILING_PROJECT
        failing_workspace = self.store.workspace_path(failing_id, create=True)
        failing_write = materialize_into(
            failing_workspace,
            "```python path=app.py\n" + FAILING_SCRIPT + "```\n",
            "run-beta-failing",
            project_id=failing_id,
            project_path=self.store.project_path_relative(failing_id),
        )
        self.assertEqual(failing_write.status, VERIFIED, failing_write.reason)
        self.assertTrue((failing_workspace / "app.py").is_file())
        failing_resolution = resolve_entry(failing_write.files, workspace=failing_workspace)
        failing_target = self.target_for(
            failing_id, failing_workspace, failing_resolution.entry
        )
        failing_token = self.runner.confirm(
            failing_target, confirm=True, digest=failing_resolution.entry.digest
        )
        self.runner.start(failing_target, failing_token)
        failed = self.wait_for(failing_target)
        self.assertEqual(failed["state"], "FAILED", failed)
        self.assertEqual(failed["exit_code"], 3)
        # The failure is never dressed up as a finished project: the classifier
        # refuses it on the run's own facts.
        self.assertIsNot(
            derive_outcome(
                run_state=failed["state"],
                run_success=False,
                error="exit 3",
                intent_type="GENERATE_PROJECT",
            ),
            RunOutcome.PROJECT_COMPLETED,
        )
        # ... and the version-2 project still stands on its own real write.
        self.assertEqual(project.status.value, "COMPLETED")
        steps.append("16. failing run -> FAILED, exit code 3, no COMPLETED")

        self.assertEqual(len(steps), 16, steps)

    def test_31_the_default_store_root_is_the_documented_path(self) -> None:
        # The layout the manual promises: <repository>/.forge/projects/<id>/workspace.
        previous = os.environ.pop("FORGE_PROJECTS_ROOT", None)
        try:
            self.assertEqual(default_projects_root(), REPO_ROOT / ".forge" / "projects")
            default_store = ProjectStore()
            self.assertEqual(
                default_store.project_path_relative("proj-0123456789ab"),
                ".forge/projects/proj-0123456789ab/workspace",
            )
        finally:
            if previous is not None:
                os.environ["FORGE_PROJECTS_ROOT"] = previous

    def test_32_node_entry_is_honest_in_both_environments(self) -> None:
        # Node may or may not be installed. The *rule* is what is asserted: either a
        # runnable node entry, or a blocked resolution naming the missing runtime.
        workspace = self.store.workspace_path(NODE_PROJECT, create=True)
        (workspace / "server.js").write_text(
            "require('http').createServer((q, s) => s.end('ok')).listen(0)\n",
            encoding="utf-8",
        )
        entry = detect_entry_point([{"path": "server.js"}], workspace=workspace)
        if entry.runnable:
            self.assertTrue(entry.command)
            self.assertEqual(entry.path, "server.js")
        else:
            self.assertIn(REASON_NODE_MISSING, entry.reason)


# --------------------------------------------------------------------------- #
# 2. the seven negative checks
# --------------------------------------------------------------------------- #


class NegativeCheckTests(_BetaCase):
    """What the beta must refuse, each pinned with the refusal it really produces."""

    def test_40_no_files_is_not_gotovo(self) -> None:
        card = build_project_presentation(
            project={"project_id": CARD_PROJECT, "status": "COMPLETED"},
            versions=[
                {
                    "version": 1,
                    "verification": "passed",
                    "acceptance": "passed",
                    "file_count": 0,
                }
            ],
            files=[],
            workspace_exists=True,
        )
        self.assertNotEqual(card.project_state, READY)
        self.assertEqual(card.project_state, NOT_CREATED)
        self.assertFalse(card.is_ready)

    def test_41_provider_text_without_files_creates_nothing(self) -> None:
        provider = _AnsweringProvider(
            "Небольшое приложение для расходов: список трат и отчёт."
        )
        runtime = _offline_runtime()
        runtime.agent_registry._agents["deepseek"] = ProviderAgent(provider)
        run_workspace = self.base / "run-workspace-negative"
        run_workspace.mkdir(parents=True, exist_ok=True)
        service = ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.run_root),
            workspace=Workspace(run_workspace),
            authorized_project_id="beta-negative-project",
        )
        result = service.run_task_detailed(
            TaskRunRequest(
                description="Хочу небольшое локальное приложение для учёта расходов",
                category="code",
                provider_name="deepseek",
                model_name="deepseek-chat",
                write_mode=True,
            )
        )
        response = result.response
        self.assertEqual(len(provider.requests), 1)
        # The writer was asked and refused: prose is not a change.
        self.assertFalse(response.success, response.error)
        self.assertEqual(response.files_created, 0)
        self.assertFalse(response.project_created)
        self.assertFalse(response.project_files_created)
        self.assertNotEqual(response.outcome, "PROJECT_COMPLETED")
        self.assertNotEqual(response.state, "COMPLETED")
        # No project record was created by a text-only run.
        self.assertEqual(self.projects.list_projects(), ())

    def test_42_an_unknown_command_is_not_started(self) -> None:
        project_id = CMD_PROJECT
        workspace = self.store.workspace_path(project_id, create=True)
        (workspace / "app.py").write_text('print("hi")\n', encoding="utf-8")
        entry = types.SimpleNamespace(
            kind="python_script",
            path="app.py",
            run_command="cmd /c echo pwned",
            command=("cmd.exe", "/c", "echo", "pwned"),
            reason="",
            serve_kind="process",
        )
        target = self.target_for(project_id, workspace, entry)
        token = self.runner.confirm(target, confirm=True, digest=self.runner.digest_for(target))
        status = self.runner.start(target, token)
        self.assertEqual(status["state"], REFUSED, status)
        self.assertEqual(status["blocked_code"], "command_not_allowed")
        self.assertIsNone(status["pid"])

    def test_43_a_path_outside_the_workspace_is_refused(self) -> None:
        project_id = ESCAPE_PROJECT
        workspace = self.store.workspace_path(project_id, create=True)
        (workspace / "app.py").write_text('print("hi")\n', encoding="utf-8")
        (self.base / "outside.py").write_text('print("outside")\n', encoding="utf-8")
        entry = types.SimpleNamespace(
            kind="python_script",
            path="../outside.py",
            run_command="python ../outside.py",
            command=(sys.executable, "../outside.py"),
            reason="",
            serve_kind="process",
        )
        target = self.target_for(project_id, workspace, entry)
        token = self.runner.confirm(target, confirm=True, digest=self.runner.digest_for(target))
        status = self.runner.start(target, token)
        self.assertEqual(status["state"], REFUSED, status)
        self.assertIn(
            status["blocked_code"],
            ("unsafe_entry_path", "entry_missing", "command_not_allowed"),
        )
        self.assertIsNone(status["pid"])

    def test_44_a_cancelled_confirmation_starts_no_process(self) -> None:
        # (a) the discovery session: a cancelled session cannot be confirmed ...
        session = self.discovery.start(GOAL)
        cancelled = self.discovery.cancel(session.session_id)
        self.assertEqual(cancelled.state.value, "CANCELLED")
        with self.assertRaises(DiscoveryStateConflict):
            self.discovery.confirm(session.session_id)

        # (b) the run approval: a refused confirmation issues no token, so no process.
        project_id = CANCEL_PROJECT
        workspace = self.store.workspace_path(project_id, create=True)
        (workspace / "app.py").write_text('print("hi")\n', encoding="utf-8")
        entry = detect_entry_point([{"path": "app.py"}], workspace=workspace)
        target = self.target_for(project_id, workspace, entry)
        for answer in (False, "true", None):
            self.assertIsNone(
                self.runner.confirm(target, confirm=answer, digest=entry.digest), answer
            )
        status = self.runner.start(target, None)
        self.assertEqual(status["state"], REFUSED, status)
        self.assertEqual(status["blocked_code"], "approval_required")
        self.assertIsNone(status["pid"])
        # A cancelled run is never RUN_BLOCKED by being cancelled: it is refused.
        self.assertNotEqual(status["state"], RUN_BLOCKED)

    def test_45_a_paid_model_without_an_explicit_choice_is_refused(self) -> None:
        paid_model = "Qwen/Qwen2.5-Coder-32B-Instruct"
        provider = _AnsweringProvider(
            "paid answer", provider_name="together", model_name=paid_model
        )
        settings = RuntimeSettings(
            default_provider="together",
            enabled_providers=("together",),
            provider_fallback_chain=(),
            allow_paid_providers=False,
            default_model=paid_model,
        )
        # ``test_mode`` would narrow the runtime to the offline provider, and the paid
        # refusal must be measured against the composition the caller actually named.
        runtime = create_runtime(settings=settings)
        runtime.agent_registry._agents["together"] = ProviderAgent(provider)
        service = ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.run_root),
            authorized_project_id="beta-paid-project",
        )
        with self.assertRaises(PaidModelRequiresConfirmation) as caught:
            service.run_task_detailed(
                TaskRunRequest(description="посчитай расходы", category="analysis")
            )
        self.assertEqual(caught.exception.code, "paid_model_requires_confirmation")
        self.assertEqual(provider.requests, [], "nothing may reach the provider")
        message = str(caught.exception)
        self.assertIn("nothing was spent", message)
        # The refusal names the pair and never a key.
        for value in _configured_key_values():
            self.assertNotIn(value, message)

    def test_46_no_key_reaches_a_payload_a_log_an_event_or_a_result(self) -> None:
        os.environ["DEEPSEEK_API_KEY"] = SYNTHETIC_KEY
        self.addCleanup(os.environ.pop, "DEEPSEEK_API_KEY", None)
        values = _configured_key_values()
        # The value really is configured where the providers would read it.
        self.assertIn(SYNTHETIC_KEY, values)

        # (a) a run response, its durable record and the run's events
        provider = _AnsweringProvider("answer with no file")
        runtime = _offline_runtime()
        runtime.agent_registry._agents["deepseek"] = ProviderAgent(provider)
        service = ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.run_root),
            authorized_project_id="beta-key-project",
        )
        result = service.run_task_detailed(
            TaskRunRequest(
                description="посчитай расходы",
                category="analysis",
                provider_name="deepseek",
                model_name="deepseek-chat",
            )
        )
        payloads = [json.dumps(result.response.to_dict(), ensure_ascii=False)]
        record = service.get_run_record(result.response.run_id)
        payloads.append(json.dumps(record, ensure_ascii=False))
        event_text = "\n".join(
            json.dumps(event.to_dict(), ensure_ascii=False)
            if hasattr(event, "to_dict")
            else str(event)
            for event in RunStore(root=self.run_root).read_events(result.response.run_id)
        )
        payloads.append(event_text)

        # (b) a run log from a real local process, and the project card
        project_id = KEY_PROJECT
        workspace = self.store.workspace_path(project_id, create=True)
        (workspace / "app.py").write_text(
            'print("api_key ' + SYNTHETIC_KEY + '")\n', encoding="utf-8"
        )
        entry = detect_entry_point([{"path": "app.py"}], workspace=workspace)
        target = self.target_for(project_id, workspace, entry)
        token = self.runner.confirm(target, confirm=True, digest=entry.digest)
        self.runner.start(target, token)
        status = self.wait_for(target)
        payloads.append(json.dumps(status, ensure_ascii=False))
        log_file = self.project_root / project_id / "run.log"
        payloads.append(
            log_file.read_text(encoding="utf-8") if log_file.is_file() else ""
        )
        payloads.append(
            json.dumps(
                build_project_presentation(
                    project={"project_id": project_id, "status": "NEEDS_CHANGES"},
                    versions=[],
                    files=[],
                    workspace_exists=True,
                ).to_dict(),
                ensure_ascii=False,
            )
        )

        combined = "\n".join(payloads)
        for value in values:
            self.assertNotIn(value, combined)
        # The redaction really happened rather than the value never being printed.
        self.assertIn("[redacted]", combined.lower())


def _configured_key_values() -> list[str]:
    """Every API-key value configured in this process's environment.

    The values are collected to be asserted absent, never printed and never logged.
    """

    values = [
        value
        for name, value in os.environ.items()
        if name.upper().endswith("_API_KEY") and value and len(value) >= 8
    ]
    return values


if __name__ == "__main__":
    unittest.main()
