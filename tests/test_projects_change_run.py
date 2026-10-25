"""A change request started from «Проекты» reaches the project as its next version.

Why this file exists
--------------------
The owner staged a change on the «Проекты» screen, pressed «Отправить задачу в
Core», watched the run finish -- and the card promised a new version that never
appeared. The API-level journey passed because it names the **confirmed discovery
session** on the change run, and the run route attaches through that session. The
screen had no such field to send, so the same change ran into a workspace no record
named.

This file walks the screen's path, not a hand-written one: the HTTP body is built by
the product's own module (``ui/src/task-run-body.ts``), executed by node, and
``dispatchTask`` uses that same function. What is asserted:

1. the run is attached to the named project, and the project reaches version N+1
   that names the run id;
2. the previous version's record is byte-identical afterwards, and so are the files
   the change did not write (in-process the scripted change writes one new file);
3. a change run that writes **zero** files does not advance the version, and the
   card says «Изменения не записаны: новой версии нет» -- the backend's own sentence,
   asserted against the presentation and against the interface's own text;
4. an unknown, malformed, archived or deleted project id is refused with 404/409
   **before** anything is dispatched, and a session that belongs to another project
   is refused too;
5. replaying the same dispatch (same ``Idempotency-Key``) does not add a second
   version for one run.

No real model call: in-process the fake provider is installed **at composition
time** (``ForgeApiService.__init__`` rebuilds the executor, so a fake patched in
afterwards is discarded and the real provider would answer), and every request the
fake received is counted at the end. With ``FORGE_JOURNEY_API`` set the same script
talks to the running application over HTTP, pinned to the offline ``mock`` provider in
write mode -- Forge's own deterministic scaffold, so no model, no network, no cost.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import unittest
import urllib.error
import urllib.request

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.api.server import create_api_app
from app.api.service import (
    NO_FILES_FROM_MODEL_SENTENCE,
    WRITE_MODE_REAL_PROVIDER_EXPLICIT,
    ForgeApiService,
)
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.presentation import RUN_NO_CHANGE_NOTICE
from app.projects.service import ProjectService
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
#: The product module that composes the `POST /api/tasks/run` body. These tests
#: execute it instead of writing a body of their own.
BODY_MODULE = REPOSITORY / "ui" / "src" / "task-run-body.ts"
PROJECTS_SCREEN = REPOSITORY / "ui" / "src" / "screens" / "Projects.tsx"
FORGE_CLIENT = REPOSITORY / "ui" / "src" / "api" / "forge.ts"
I18N = REPOSITORY / "ui" / "src" / "i18n.ts"

#: The workspace the Core is composed for: a ``ws-…`` id, never a ``proj-…`` one, so
#: a response that published it instead of the lifecycle id fails here.
WORKSPACE_PROJECT_ID = "change-run-workspace"

GOAL = (
    "Хочу простой локальный сервер-визитку: одна страница с приветствием. "
    "Никаких внешних сервисов и баз данных."
)
CHANGE = "Добавь на страницу строку с номером текущей версии проекта."
ZERO_FILE_CHANGE = "Опиши словами, что уже есть в проекте."

FAKE_PROVIDER = "deepseek"
FAKE_MODEL = "deepseek-chat"
MARKER = "FORGE-CHANGE-RUN-OK"

APP_PY = '''"""The change-run project: a tiny loopback page."""

import http.server
import socketserver


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"%s\\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
    print("listening %%d" %% httpd.server_address[1], flush=True)
    httpd.serve_forever()
''' % MARKER

CREATE_ANSWER = "Вот проект.\n\n```python path=app.py\n" + APP_PY + "```\n"

#: The change writes ONE new file and leaves ``app.py`` alone: that is what makes
#: "the previous version is byte-identical" checkable on disk, not only in the record.
CHANGE_ANSWER = (
    "Вот доработка.\n\n"
    "```python path=report.py\n"
    '"""Export rows as CSV text."""\n\n\n'
    "def export(rows):\n"
    '    lines = ["category,amount"]\n'
    "    for category, amount in rows:\n"
    '        lines.append(f"{category},{amount}")\n'
    '    return "\\n".join(lines) + "\\n"\n'
    "```\n"
)

#: A provider answer that carries no fenced file block. In write mode Forge retries
#: once and then reports ``no_changes`` -- the run really did write nothing.
TEXT_ANSWER = "В проекте уже есть страница с приветствием и модуль приложения."


class _FakeProvider(Provider):
    """A scripted provider that records every request; the last answer repeats."""

    def __init__(
        self,
        answers: list[str],
        *,
        provider_name: str = FAKE_PROVIDER,
        model_name: str = FAKE_MODEL,
    ) -> None:
        from app.agents.providers.config import ProviderConfig

        super().__init__(
            ProviderConfig(provider_name=provider_name, model_name=model_name)
        )
        self.answers = list(answers)
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.answers) - 1)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=request.model_name or self.model_name,
            output=self.answers[index],
        )


# --------------------------------------------------------------------------- #
# clients: the real app in-process, or the running app over HTTP
# --------------------------------------------------------------------------- #


class _Answer:
    def __init__(self, status_code: int, body: bytes, headers: object = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = body.decode("utf-8", errors="replace")
        self.headers: dict[str, str] = {}
        for key, value in dict(headers or {}).items():
            self.headers[str(key).lower()] = str(value)

    def json(self):
        return json.loads(self._body.decode("utf-8"))


class _HttpClient:
    """The subset of ``TestClient`` this file uses, over urllib (evidence mode)."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def _call(self, method: str, path: str, payload=None, headers=None) -> _Answer:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        merged = {"accept": "application/json", "content-type": "application/json"}
        merged.update(headers or {})
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=merged
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return _Answer(response.status, response.read(), response.headers)
        except urllib.error.HTTPError as error:
            return _Answer(error.code, error.read(), error.headers)

    def get(self, path: str) -> _Answer:
        return self._call("GET", path)

    def post(self, path: str, payload=None, headers=None) -> _Answer:
        return self._call("POST", path, payload if payload is not None else {}, headers)

    def delete(self, path: str) -> _Answer:
        return self._call("DELETE", path)


class _TestClientAdapter:
    """``TestClient`` plus the same ``json``/``text``/``headers`` helpers."""

    def __init__(self, client) -> None:
        self._client = client

    def _wrap(self, response) -> _Answer:
        return _Answer(response.status_code, response.content, response.headers)

    def get(self, path: str) -> _Answer:
        return self._wrap(self._client.get(path))

    def post(self, path: str, payload=None, headers=None) -> _Answer:
        return self._wrap(
            self._client.post(
                path, json=payload if payload is not None else {}, headers=headers
            )
        )

    def delete(self, path: str) -> _Answer:
        return self._wrap(self._client.delete(path))


# --------------------------------------------------------------------------- #
# the product's own payload builder, executed from its source
# --------------------------------------------------------------------------- #


def _run_node(script: str) -> str:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - node is present on this host
        raise unittest.SkipTest("node is not installed; the payload cannot be executed")
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


def screen_change_payload(**choices) -> dict:
    """The exact body ``Projects.tsx::submitTask`` sends.

    The choices are the component's own: ``description`` is the staged task text,
    ``category`` is the literal ``"code"``, and the provider/model/write/lifecycle
    values are the four it reads from its form. The *body* is composed by
    ``ui/src/task-run-body.ts`` -- the function ``dispatchTask`` calls -- so a field
    the screen forgets to pass cannot be added by this test.
    """

    script = (
        f"import {{ taskRunBody }} from {json.dumps(BODY_MODULE.as_uri())};"
        "process.stdout.write(JSON.stringify(taskRunBody("
        + json.dumps(choices)
        + ")));"
    )
    return json.loads(_run_node(script))


# --------------------------------------------------------------------------- #
# the tests
# --------------------------------------------------------------------------- #


class _Case(unittest.TestCase):
    """One composed application (in-process) or the running one (evidence mode)."""

    maxDiff = None

    def setUp(self) -> None:
        self.steps: list[str] = []
        live = os.environ.get("FORGE_JOURNEY_API", "").strip()
        self.live = bool(live)
        self.fake: _FakeProvider | None = None
        self.store: ProjectStore | None = None

        if self.live:
            self.client = _HttpClient(live)
            self.provider = "mock"
            self.model = ""
            return

        base = (
            REPOSITORY
            / ".forge"
            / "test-projects-change-run"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.root = base / "projects"
        self.discovery_root = base / "discovery"
        self.run_root = base / "runs"
        self.run_workspace = base / "run-workspace"
        for directory in (
            self.root,
            self.discovery_root,
            self.run_root,
            self.run_workspace,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        previous = os.environ.get(PROJECTS_ROOT_ENV_VAR)
        os.environ[PROJECTS_ROOT_ENV_VAR] = str(self.root)

        def _restore() -> None:
            if previous is None:
                os.environ.pop(PROJECTS_ROOT_ENV_VAR, None)
            else:
                os.environ[PROJECTS_ROOT_ENV_VAR] = previous

        self.addCleanup(_restore)

        self.store = ProjectStore(root=self.root)
        self.projects = ProjectService(self.store)
        self.discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))

        # THE FAKE IS INSTALLED AT COMPOSITION TIME. The runtime is built first, its
        # ``deepseek`` agent is replaced, and only then is the service constructed:
        # ``ForgeApiService.__init__`` rebuilds the executor, so a fake executor put
        # into the runtime afterwards is discarded and the real provider would be
        # left in place (the mistake that once caused a real paid call).
        runtime = offline_runtime()
        self.fake = _FakeProvider([CREATE_ANSWER, CHANGE_ANSWER, TEXT_ANSWER])
        runtime.agent_registry._agents[FAKE_PROVIDER] = ProviderAgent(self.fake)

        service = ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.run_root),
            workspace=Workspace(self.run_workspace),
            authorized_project_id=WORKSPACE_PROJECT_ID,
        )
        from fastapi.testclient import TestClient

        self.app = create_api_app(
            service,
            discovery_service=self.discovery,
            project_service=self.projects,
        )
        self.addCleanup(self._stop_everything)
        self.client = _TestClientAdapter(TestClient(self.app))
        self.provider = FAKE_PROVIDER
        self.model = FAKE_MODEL

    def _stop_everything(self) -> None:
        state = getattr(getattr(self, "app", None), "state", None)
        for name in ("forge_runner", "forge_previews"):
            target = getattr(state, name, None) if state is not None else None
            if target is not None:
                try:
                    target.stop_all()
                except Exception:  # noqa: BLE001 - shutdown is best effort
                    pass

    # ------------------------------------------------------------------ helpers
    def _json(self, answer: _Answer, label: str) -> dict:
        self.assertLess(
            answer.status_code, 400, f"{label}: {answer.status_code} {answer.text}"
        )
        return answer.json()

    def _provider_calls(self) -> int:
        return 0 if self.fake is None else len(self.fake.requests)

    def _confirmed_project(self, goal: str) -> tuple[str, str]:
        """A project created through the real discovery routes; no provider is called.

        Returns ``(session_id, project_id)``: the same pair a screen would hold, the
        session for the creation hand-off and the id for the change association.
        """

        opened = self._json(
            self.client.post("/api/discovery/sessions", {"goal": goal}), "open session"
        )
        session_id = str(opened["session"]["session_id"])
        if str(opened["session"].get("state") or "") != "READY_FOR_PLANNING":
            self._json(
                self.client.post(
                    f"/api/discovery/sessions/{session_id}/skip",
                    {"reason": "the change-run test needs no further answers"},
                ),
                "skip the questions",
            )
        confirmed = self._json(
            self.client.post(f"/api/discovery/sessions/{session_id}/confirm"),
            "confirm",
        )
        self.assertEqual(confirmed["session"]["state"], "CONFIRMED")
        project_id = str(confirmed["project"]["project_id"])
        self.assertRegex(project_id, r"^proj-[0-9a-f]{12}$")
        return session_id, project_id

    def _create_first_version(self, project_id: str, session_id: str) -> dict:
        """The creation hand-off Кузница makes: the confirmed session names the project."""

        payload = screen_change_payload(
            description=GOAL,
            category="code",
            providerName=self.provider or None,
            modelName=self.model or None,
            writeMode=True,
            discoverySessionId=session_id,
        )
        created = self._json(
            self.client.post("/api/tasks/run", payload), "create the project"
        )
        self.assertEqual(
            created.get("lifecycle_project_id"), project_id,
            "the creation run did not attach to its session's project",
        )
        self.assertGreater(int(created.get("files_created", 0)), 0, created)
        return created

    def _detail(self, project_id: str) -> dict:
        detail = self._json(
            self.client.get(f"/api/projects/{project_id}"), "project detail"
        )
        self.assertEqual(detail["project"]["project_id"], project_id)
        return detail

    def _version_record(self, project_id: str, version: int) -> str:
        """One version's full record, as a stable string, for a byte comparison."""

        history = self._json(
            self.client.get(f"/api/projects/{project_id}/history"), "history"
        )
        for item in history["versions"]:
            if int(item["version"]) == version:
                return json.dumps(item, sort_keys=True, ensure_ascii=False)
        self.fail(f"version {version} is not in the history: {history}")

    def _workspace_dir(self, project_id: str) -> pathlib.Path:
        if self.store is not None:
            return self.store.workspace_path(project_id)
        # Evidence mode: the record publishes the workspace repository-relative, so
        # the same files are reachable from the checkout.
        detail = self._detail(project_id)
        return REPOSITORY / str(detail["project"]["project_path"])

    def _workspace_files(self, project_id: str) -> dict[str, bytes]:
        """The project's files on disk (no record is consulted)."""

        workspace = self._workspace_dir(project_id)
        found: dict[str, bytes] = {}
        if not workspace.is_dir():
            return found
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            found[path.relative_to(workspace).as_posix()] = path.read_bytes()
        return found

    def _stage(self, project_id: str, change: str) -> dict:
        """``POST /api/projects/{id}/versions`` -- the «Создать версию» click."""

        staged = self._json(
            self.client.post(f"/api/projects/{project_id}/versions", {"change": change}),
            "stage the change",
        )
        self.assertTrue(str(staged["task_text"]).strip(), staged)
        return staged

    def _delete_quietly(self, project_id: str) -> None:
        self.client.delete(f"/api/projects/{project_id}")


class ChangeFromProjectsScreenTests(_Case):
    """The «Проекты» change path: attach, advance, keep the old version, no false version."""

    def test_01_a_change_from_the_screen_becomes_the_next_version(self) -> None:
        session_id, project_id = self._confirmed_project(GOAL)

        # ------------------------------------------------------- version 1 exists
        created = self._create_first_version(project_id, session_id)
        first = self._detail(project_id)
        self.assertEqual(int(first["project"]["current_version"]), 1, first["project"])
        self.assertGreater(int(first["files_created"]), 0, first["files"])
        first_version_record = self._version_record(project_id, 1)
        first_files = self._workspace_files(project_id)
        self.assertIn("app.py", first_files)
        app_sha = hashlib.sha256(first_files["app.py"]).hexdigest()
        recorded = {item["path"]: item["sha256"] for item in first["files"]}
        self.assertEqual(recorded["app.py"], app_sha, first["files"])
        self.steps.append(
            f"v1 recorded from run {created['run_id']} with files {sorted(first_files)} "
            f"(app.py sha256 {app_sha[:12]}…)"
        )

        # --------------------------------------------------- the screen's change
        staged = self._stage(project_id, CHANGE)
        self.assertEqual(int(staged["version"]), 2)
        payload = screen_change_payload(
            description=str(staged["task_text"]),
            category="code",
            providerName=self.provider or None,
            modelName=self.model or None,
            writeMode=True,
            lifecycleProjectId=project_id,
        )
        # The body is the product's, so this also pins that nothing invented is sent.
        expected_fields = {"description", "category", "write_mode", "lifecycle_project_id"}
        if self.provider:
            expected_fields.add("provider_name")
        if self.model:
            expected_fields.add("model_name")
        self.assertEqual(set(payload), expected_fields, payload)
        self.assertEqual(payload["lifecycle_project_id"], project_id)
        self.assertIs(payload["write_mode"], True)

        key = "change-run-idempotency-key"
        answer = self.client.post(
            "/api/tasks/run", payload, headers={"Idempotency-Key": key}
        )
        change_run = self._json(answer, "run the change")
        self.assertEqual(
            str(answer.headers.get("x-forge-project-attach", "")), "ok",
            f"the attach header did not report ok: {answer.headers}",
        )
        self.assertEqual(
            str(change_run.get("lifecycle_project_id", "")), project_id, change_run
        )
        self.assertGreater(int(change_run.get("files_created", 0)), 0, change_run)

        after = self._detail(project_id)
        self.assertEqual(
            str(after["project"]["current_version"]), "2",
            "the change run did not become the current version",
        )
        newest = after["versions"][0]
        self.assertEqual(int(newest["version"]), 2)
        self.assertEqual(str(newest["run_id"]), str(change_run["run_id"]))
        self.assertIsNotNone(newest.get("change_request"))
        self.assertGreater(int(newest["file_count"]), 0, newest)
        self.assertIs(after["presentation"]["change_not_written"], False)
        self.assertEqual(
            int(change_run["files_created"]), int(newest["file_count"]), change_run
        )
        # THE OLD VERSION IS UNTOUCHED: its record -- run id, verdicts and the
        # sha256 of every file it names -- is byte-identical.
        self.assertEqual(
            self._version_record(project_id, 1), first_version_record,
            "the change rewrote version 1's record",
        )
        after_files = self._workspace_files(project_id)
        if not self.live:
            # The scripted change writes only the new file, so the previous
            # version's file is byte-identical on disk as well.
            self.assertEqual(
                after_files["app.py"], first_files["app.py"],
                "the change rewrote a file it did not name",
            )
            self.assertEqual(hashlib.sha256(after_files["app.py"]).hexdigest(), app_sha)
        self.assertNotEqual(after_files, first_files, "the change wrote nothing to disk")
        self.steps.append(
            f"change {CHANGE!r} -> run {change_run['run_id']} filled v2 "
            f"(current_version=2, {newest['file_count']} files); v1 record byte-identical"
            + ("" if self.live else "; app.py bytes unchanged")
        )

        # ------------------------------------- the same dispatch does not duplicate
        replay = self._json(
            self.client.post(
                "/api/tasks/run", payload, headers={"Idempotency-Key": key}
            ),
            "replay the change",
        )
        self.assertEqual(str(replay["run_id"]), str(change_run["run_id"]))
        replayed = self._detail(project_id)
        self.assertEqual(
            [int(item["version"]) for item in replayed["versions"]],
            [int(item["version"]) for item in after["versions"]],
            "one run produced more than one version",
        )
        self.assertEqual(int(replayed["project"]["current_version"]), 2)
        self.steps.append(
            f"the same dispatch replayed kept run {change_run['run_id']} and versions "
            f"{[int(item['version']) for item in replayed['versions']]}"
        )

        # -------------------------------------- a change that writes nothing at all
        zero_staged = self._stage(project_id, ZERO_FILE_CHANGE)
        self.assertEqual(int(zero_staged["version"]), 3)
        zero_payload = screen_change_payload(
            description=str(zero_staged["task_text"]),
            category="code",
            providerName=self.provider or None,
            modelName=self.model or None,
            # In-process the scripted provider answers with prose, and Forge makes
            # its one allowed retry; the live app's offline path writes nothing
            # without the flag. Both are the same user-visible fact: no file.
            writeMode=(not self.live),
            lifecycleProjectId=project_id,
        )
        zero_run = self._json(
            self.client.post("/api/tasks/run", zero_payload),
            "run a change that writes nothing",
        )
        self.assertEqual(int(zero_run.get("files_created", -1)), 0, zero_run)
        self.assertEqual(str(zero_run.get("lifecycle_project_id", "")), project_id)
        # THE HONEST END. The model answered, the answer named no file, the one retry
        # named none either: nothing was written and the run says so in the owner's
        # words instead of substituting Forge's own scaffold. The write mode it really
        # used is in the durable record, beside the same string in the response.
        if not self.live:
            self.assertEqual(
                str(zero_run.get("write_mode", "")), WRITE_MODE_REAL_PROVIDER_EXPLICIT
            )
            self.assertIn(NO_FILES_FROM_MODEL_SENTENCE, str(zero_run.get("error") or ""))
            zero_record = self._json(
                self.client.get(f"/api/runs/{zero_run['run_id']}"), "zero-file run record"
            )
            self.assertEqual(
                str((zero_record.get("project_state") or {}).get("write_mode")),
                str(zero_run.get("write_mode")),
            )
        unchanged = self._detail(project_id)
        self.assertEqual(
            str(unchanged["project"]["current_version"]), "2",
            "a change that wrote no file advanced the version",
        )
        attempt = next(
            item for item in unchanged["versions"] if int(item["version"]) == 3
        )
        self.assertEqual(str(attempt["run_id"]), str(zero_run["run_id"]))
        self.assertEqual(int(attempt["file_count"]), 0)
        # THE HONEST CARD: the backend's own sentence, in the presentation ...
        self.assertIs(unchanged["presentation"]["change_not_written"], True)
        self.assertIn(RUN_NO_CHANGE_NOTICE, unchanged["presentation"]["notices"])
        self.assertEqual(RUN_NO_CHANGE_NOTICE, "Изменения не записаны: новой версии нет")
        # ... and in the interface, which prints that fact for a change run.
        screen = PROJECTS_SCREEN.read_text(encoding="utf-8")
        # The strip prints the owner's own measure of the same fact: «файлов 0 — код не
        # менялся». The sentence above stays in the interface's vocabulary for the
        # refusals that are not about a file count, so both are asserted here.
        self.assertIn("projects.change.zero_files", screen)
        i18n = I18N.read_text(encoding="utf-8")
        self.assertIn(f'"{RUN_NO_CHANGE_NOTICE}"', i18n)
        self.assertIn("файлов 0 — код не менялся", i18n)
        # The files on disk did not change either: the current version is still the
        # one describing what the workspace holds.
        self.assertEqual(self._workspace_files(project_id), after_files)
        self.steps.append(
            f"a zero-file change kept current_version=2 (v3 recorded as run "
            f"{zero_run['run_id']} with 0 files); the card says "
            f"{RUN_NO_CHANGE_NOTICE!r}"
        )

        # --------------------------------------------------------- the UI wiring
        self.assertIn(
            "lifecycleProjectId: selectedId", screen,
            "«Проекты» no longer sends the project association",
        )
        self.assertIn(
            "taskRunBody", FORGE_CLIENT.read_text(encoding="utf-8"),
            "dispatchTask no longer builds its body with the product's own builder",
        )

        if not self.live:
            self.assertIsNotNone(self.fake)
            # The creation, the change, and the zero-file run's single retry: four
            # calls, every one of them the fake.
            self.assertEqual(
                len(self.fake.requests), 4, "the fake provider was not asked four times"
            )
        if self.live:
            self._delete_quietly(project_id)
        print("\n".join(self.steps))


class ChangeRefusalTests(_Case):
    """A run for a project that cannot receive one is refused before dispatch."""

    def test_01_unknown_archived_deleted_and_mismatched_projects_are_refused(self) -> None:
        session_id, project_id = self._confirmed_project(GOAL)
        self._create_first_version(project_id, session_id)
        before = self._detail(project_id)
        files_before = self._workspace_files(project_id)
        calls_before = self._provider_calls()

        def refused(payload: dict, status: int, code: str, label: str) -> dict:
            answer = self.client.post("/api/tasks/run", payload)
            self.assertEqual(answer.status_code, status, f"{label}: {answer.text}")
            body = answer.json()
            detail = body.get("detail") if isinstance(body, dict) else None
            self.assertIsInstance(detail, dict, body)
            self.assertEqual(str(detail.get("code", "")), code, body)
            message = str(detail.get("error", ""))
            # A safe message: no absolute path of the host, no secret material.
            self.assertNotIn(str(REPOSITORY), message)
            self.assertNotIn(str(REPOSITORY).replace("\\", "/"), message)
            self.assertNotIn(".env", message)
            # Nothing was dispatched: the refusal came before the run path.
            self.assertEqual(self._provider_calls(), calls_before, label)
            return body

        def payload_for(target: object) -> dict:
            return screen_change_payload(
                description="Добавь строку с версией.",
                category="code",
                providerName=self.provider or None,
                modelName=self.model or None,
                writeMode=True,
                lifecycleProjectId=target,
            )

        # An id no record names -- well-formed and malformed alike.
        refused(payload_for("proj-000000000000"), 404, "unknown_project", "unknown")
        refused(payload_for("proj-../../../escape"), 404, "unknown_project", "traversal")
        refused(payload_for("not-a-project"), 404, "unknown_project", "no id shape")

        # An archived project is frozen: restore it first (the same 409 code the
        # version route uses for the same condition).
        archived = self._json(
            self.client.post(f"/api/projects/{project_id}/archive"), "archive"
        )
        self.assertEqual(archived["project"]["status"], "ARCHIVED")
        refused(payload_for(project_id), 409, "illegal_project_transition", "archived")
        restored = self._json(
            self.client.post(f"/api/projects/{project_id}/restore"), "restore"
        )
        self.assertEqual(restored["project"]["status"], before["project"]["status"])

        # A deleted project is in the trash: its id is refused, never resurrected.
        _, throwaway = self._confirmed_project("Проект, который удаляется в этом тесте.")
        deleted = self._json(
            self.client.delete(f"/api/projects/{throwaway}"), "delete the throwaway"
        )
        self.assertTrue(deleted["deleted"], deleted)
        refused(payload_for(throwaway), 409, "project_deleted", "deleted")

        # A session that belongs to another project cannot attach a run to this one.
        other_session, other_project = self._confirmed_project(
            "Ещё один проект: проверка несовпадения."
        )
        mismatch = screen_change_payload(
            description="Добавь строку.",
            category="code",
            providerName=self.provider or None,
            modelName=self.model or None,
            writeMode=True,
            discoverySessionId=other_session,
            lifecycleProjectId=project_id,
        )
        answer = self.client.post("/api/tasks/run", mismatch)
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(answer.json()["detail"]["code"], "project_mismatch")
        self.assertEqual(self._provider_calls(), calls_before, "mismatch dispatched a run")

        # Nothing above advanced the project or wrote a file.
        after = self._detail(project_id)
        self.assertEqual(
            int(after["project"]["current_version"]),
            int(before["project"]["current_version"]),
        )
        self.assertEqual(
            [int(item["version"]) for item in after["versions"]],
            [int(item["version"]) for item in before["versions"]],
        )
        self.assertEqual(self._workspace_files(project_id), files_before)

        # The route's own validation of the association: an empty or non-string value
        # is a 400. (These bodies are the *route's* boundary, not a screen payload --
        # the screen always passes a project id or nothing at all.)
        for bad in (7, ["proj-000000000000"]):
            answer = self.client.post(
                "/api/tasks/run",
                {
                    "description": "Добавь строку.",
                    "category": "code",
                    "lifecycle_project_id": bad,
                },
            )
            self.assertEqual(answer.status_code, 400, f"{bad!r}: {answer.text}")

        self.steps.append(
            "unknown/malformed/archived/deleted/mismatched targets were refused "
            "(404/409) with no dispatch, no version and no file"
        )
        if self.live:
            # Leave the owner's store as it was: the throwaway comes back, the extra
            # project disappears.
            self.client.post(f"/api/projects/{throwaway}/restore-deleted")
            self._delete_quietly(project_id)
            self._delete_quietly(other_project)
        print("\n".join(self.steps))


class RepeatedChangeTextTests(_Case):
    """The same words are one change: a repeated press never records a second version.

    Measured defect: one record held v3, v4 and v5, all with the same change text and
    **zero files each**, because the failed run filled its slot (so the slot was no
    longer "pending") and the next press appended v4, then v5. Nothing refused the
    duplicate and nothing said the model had returned no files.

    Three rules are asserted here, through the real HTTP surface and the real service:

    1. a change that wrote files is ``already_applied`` on the second stage request:
       no new version, an empty ``task_text``, and no provider call;
    2. a change that wrote nothing is ``retargeted`` on the second request: the retry
       writes into the **same** version number instead of appending another empty one;
    3. every run response publishes a screened ``answer_excerpt`` of the model's own
       answer, so the card can say what the model said.
    """

    def _stage_change(self, project_id: str, change: str) -> dict:
        return self._json(
            self.client.post(f"/api/projects/{project_id}/versions", {"change": change}),
            f"stage {change[:24]!r}",
        )

    def _run_change(self, project_id: str, task_text: str) -> dict:
        payload = screen_change_payload(
            description=task_text,
            category="code",
            providerName=self.provider or None,
            modelName=self.model or None,
            writeMode=(not self.live),
            lifecycleProjectId=project_id,
        )
        return self._json(self.client.post("/api/tasks/run", payload), "run the change")

    def _version_numbers(self, project_id: str) -> list[int]:
        # The detail publishes versions newest first; the numbers are what matters here.
        return sorted(
            int(item["version"]) for item in self._detail(project_id)["versions"]
        )

    def test_a_repeated_press_does_not_become_another_version(self) -> None:
        session_id, project_id = self._confirmed_project(GOAL)
        self._create_first_version(project_id, session_id)
        self.assertEqual(self._version_numbers(project_id), [1])

        # ---------------------------------------------- press 1: a change that writes
        staged = self._stage_change(project_id, CHANGE)
        self.assertEqual(int(staged["version"]), 2)
        self.assertIs(staged.get("already_applied"), False)
        self.assertIs(staged.get("retargeted"), False)
        first = self._run_change(project_id, str(staged["task_text"]))
        self.assertGreater(int(first.get("files_created", 0)), 0, first)
        self.assertEqual(int(self._detail(project_id)["project"]["current_version"]), 2)
        self.assertEqual(self._version_numbers(project_id), [1, 2])
        calls_after_first = self._provider_calls()

        # ... «Что сделал Forge» is quoted from the model's own answer, not composed:
        # the first two non-empty lines of it, exactly.
        excerpt = str(first.get("answer_excerpt") or "")
        self.assertTrue(excerpt.strip(), first)
        expected = [
            line.strip() for line in CHANGE_ANSWER.splitlines() if line.strip()
        ][:2]
        self.assertEqual(excerpt, "\n".join(expected))
        self.assertLessEqual(len(excerpt.splitlines()), 2)

        # ------------------------------------- press 2: the same words, same sentence
        # Spacing and letter case are not a different request.
        repeat = self._stage_change(project_id, "  " + CHANGE.upper() + "\n\n")
        self.assertIs(repeat.get("already_applied"), True, repeat)
        self.assertEqual(str(repeat.get("task_text") or ""), "", repeat)
        self.assertEqual(int(repeat["version"]), 2)
        self.assertEqual(
            self._version_numbers(project_id), [1, 2],
            "a repeated press recorded another version",
        )
        # Nothing was dispatched for it: the same answer, no second run.
        self.assertEqual(self._provider_calls(), calls_after_first)
        # And the project still says what it said.
        self.assertEqual(int(self._detail(project_id)["project"]["current_version"]), 2)
        self.steps.append(
            "a repeated change text was answered with already_applied=True and kept "
            f"versions {self._version_numbers(project_id)}"
        )

        # ------------------------------- a change that writes nothing, twice in a row
        zero_staged = self._stage_change(project_id, ZERO_FILE_CHANGE)
        self.assertEqual(int(zero_staged["version"]), 3)
        zero = self._run_change(project_id, str(zero_staged["task_text"]))
        self.assertEqual(int(zero.get("files_created", -1)), 0, zero)
        # The model answered; the answer is quoted, screened, and says nothing was written.
        self.assertTrue(str(zero.get("answer_excerpt") or "").strip(), zero)
        detail = self._detail(project_id)
        self.assertEqual(int(detail["project"]["current_version"]), 2, detail["project"])
        attempt = next(item for item in detail["versions"] if int(item["version"]) == 3)
        self.assertEqual(int(attempt["file_count"]), 0, attempt)
        self.assertIs(detail["presentation"]["change_not_written"], True)
        calls_after_zero = self._provider_calls()

        # The owner presses again on the same sentence: the retry targets v3, never v4.
        retry = self._stage_change(project_id, ZERO_FILE_CHANGE)
        self.assertIs(retry.get("retargeted"), True, retry)
        self.assertIs(retry.get("already_applied"), False, retry)
        self.assertEqual(int(retry["version"]), 3)
        self.assertTrue(str(retry.get("task_text") or "").strip(), retry)
        self.assertEqual(
            self._version_numbers(project_id), [1, 2, 3],
            "an empty change pressed twice recorded another version",
        )
        self.assertEqual(self._provider_calls(), calls_after_zero)
        self.steps.append(
            "a zero-file change pressed twice re-targeted v3 instead of appending v4"
        )

    def test_the_empty_slot_is_labelled_and_the_answer_is_screened(self) -> None:
        """The words the card prints, and the screen's own use of them."""

        from app.projects.service import change_fingerprint  # noqa: PLC0415 - local import
        from app.projects.what_forge_did import answer_excerpt  # noqa: PLC0415

        # The fingerprint is spacing- and case-insensitive, and empty stays empty.
        self.assertEqual(change_fingerprint("  Добавь   строку \n"), "добавь строку")
        self.assertEqual(change_fingerprint(""), "")
        self.assertEqual(change_fingerprint(None), "")

        # Two lines, collapsed, bounded -- and a secret-shaped answer never leaves.
        self.assertEqual(
            answer_excerpt("Первая строка.\n\nВторая строка.\nТретья."),
            "Первая строка.\nВторая строка.",
        )
        self.assertEqual(answer_excerpt(""), "")
        self.assertEqual(answer_excerpt(None), "")
        self.assertEqual(
            answer_excerpt("sk-" + "a1B2c3D4" * 8), "[REDACTED:api_key]"
        )
        long_line = "x" * 900
        self.assertLessEqual(len(answer_excerpt(long_line)), 400)

        # The card prints all three facts, and the interface has the words for them.
        screen = PROJECTS_SCREEN.read_text(encoding="utf-8")
        for marker in (
            'data-testid="change-did-summary"',
            'data-testid="change-did-no-files"',
            'data-testid="change-did-answer"',
            "answer_excerpt",
        ):
            self.assertIn(marker, screen)
        i18n = I18N.read_text(encoding="utf-8")
        self.assertIn('"projects.change.did.title": "Что сделал Forge"', i18n)
        self.assertIn('"projects.change.did.no_files": "Модель не вернула файлы"', i18n)
        self.assertIn("projects.change.zero_files", screen)


class MockChangeCannotOverwriteTests(_Case):
    """The offline scaffold fills an empty project and never rewrites a real one.

    ``MOCK_DETERMINISTIC`` answers with a generic ``index.html`` + ``app.py``. Applied to
    a project that already holds the owner's own code it would replace that code with a
    template while the card said "the project changed" -- measured in the same class of
    failure as a zero-file run that looked like a success. So the offline engine answers
    a change on a non-empty project with a text that names no file: nothing is written,
    the run says so, and the files on disk stay byte-identical.
    """

    def test_a_mock_change_writes_nothing_and_leaves_the_files_alone(self) -> None:
        session_id, project_id = self._confirmed_project(GOAL)
        self._create_first_version(project_id, session_id)
        before_files = self._workspace_files(project_id)
        self.assertGreater(len(before_files), 0, before_files)

        staged = self._json(
            self.client.post(
                f"/api/projects/{project_id}/versions",
                {"change": "Переделай страницу под тёмную тему."},
            ),
            "stage a mock change",
        )
        self.assertEqual(int(staged["version"]), 2)

        # The offline provider, in write mode, exactly as the card would send it.
        payload = screen_change_payload(
            description=str(staged["task_text"]),
            category="code",
            providerName="mock",
            modelName="deterministic-offline",
            writeMode=True,
            lifecycleProjectId=project_id,
        )
        run = self._json(self.client.post("/api/tasks/run", payload), "run the mock change")

        self.assertEqual(int(run.get("files_created", -1)), 0, run)
        self.assertIs(bool(run.get("success")), False, run)
        self.assertEqual(str(run.get("provider_used") or ""), "mock")
        # The mode really was the deterministic scaffold's -- and it was not applied.
        self.assertEqual(
            str((run.get("write_mode") or "")).upper(), "MOCK_DETERMINISTIC", run
        )
        detail = self._detail(project_id)
        self.assertEqual(int(detail["project"]["current_version"]), 1, detail["project"])
        attempt = next(item for item in detail["versions"] if int(item["version"]) == 2)
        self.assertEqual(int(attempt["file_count"]), 0, attempt)
        # THE PROJECT IS UNTOUCHED, byte for byte: no template replaced its code.
        self.assertEqual(self._workspace_files(project_id), before_files)

    def test_an_empty_project_still_gets_the_scaffold(self) -> None:
        """The other half of the same rule: a creation may be scaffolded by Forge."""

        session_id, project_id = self._confirmed_project(GOAL)
        # The project record exists with no files yet; the mock creation fills it.
        payload = screen_change_payload(
            description=GOAL,
            category="code",
            providerName="mock",
            modelName="deterministic-offline",
            writeMode=True,
            discoverySessionId=session_id,
        )
        created = self._json(self.client.post("/api/tasks/run", payload), "mock creation")
        self.assertEqual(created.get("lifecycle_project_id"), project_id, created)
        self.assertGreater(int(created.get("files_created", 0)), 0, created)
        self.assertEqual(
            str((created.get("write_mode") or "")).upper(), "MOCK_DETERMINISTIC", created
        )


if __name__ == "__main__":
    unittest.main()
