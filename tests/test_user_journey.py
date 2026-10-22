"""One continuous human script against the real application.

Why this file exists
--------------------
3000+ tests passed while the owner found, in two minutes of clicking, a clarified
question loop that never ended, a dead «Открыть» that showed nothing, and a run
reported ``COMPLETED`` that produced zero files. Every one of those tests asserted a
**function**. None of them walked the **path a person walks**, and none of them ran a
step twice. This file is that path: one deterministic script, in the owner's order,
with a user-visible invariant asserted at every step:

1. type a one-sentence idea;
2. receive questions and answer them through the same payload shape the UI sends;
3. confirm the specification;
4. run and receive files;
5. press the result action that opens the project;
6. start the project and fetch the preview URL;
7. stop it;
8. add a change request (a new version is staged);
9. delete;
10. restore;
11. verify again.

What is real
------------
The whole application: :func:`app.api.server.create_api_app`, the discovery service
and its store, the project lifecycle service, store and lock, the run store, the
preview registry and the local runner. In-process mode additionally installs a
**fake provider at composition time** (``runtime.agent_registry`` is seeded *before*
``ForgeApiService`` is constructed, because that constructor rebuilds the executor --
a fake executor patched in afterwards is silently discarded and the real provider is
left in place). The fake's text is the only "model answer" in this file, and the test
asserts the fake was really asked, so no real provider can have been reached.

Two modes, one script
---------------------
``FORGE_JOURNEY_API`` unset (the suite's mode): the app is composed in a throwaway
directory and the fake provider drives the real dispatcher, so the run produces a
Python project the **real local runner** starts.

``FORGE_JOURNEY_API=http://127.0.0.1:8011`` (evidence mode): the same script talks to
the running application over HTTP, with no composition of its own. There the run is
pinned to the offline provider (``mock``) in write mode, which is Forge's own
deterministic scaffold: no model, no network, no cost. Both modes assert the same
user-visible invariants, and step 6 branches on the entry kind the backend reports
(a real child process for the Python project, the real static preview for the
scaffold) instead of assuming one.

No network: every URL this file fetches is ``127.0.0.1``.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import time
import unittest
import urllib.error
import urllib.request

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.service import ProjectService
from app.projects.store import DELETED_DIRECTORY_NAME, PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
SELECTION_MODULE = REPOSITORY / "ui" / "src" / "project-selection.js"
CARD_MODULE = REPOSITORY / "ui" / "src" / "forge-result.ts"

#: The workspace the Core is composed for. It is deliberately not a ``proj-…`` id:
#: step 5 must never open it.
WORKSPACE_PROJECT_ID = "user-journey-workspace"

#: The owner's idea: one or two sentences, as he would type it.
GOAL = (
    "Хочу простой локальный сервер-визитку: одна страница с приветствием и названием "
    "проекта. Никаких внешних сервисов и баз данных."
)

#: The change the owner asks for at step 8. It is staged, not executed: the staged
#: version and the task text it publishes are the fact this step asserts.
CHANGE = "Добавь на страницу строку с номером текущей версии проекта."

#: The fake provider's identity. ``deepseek`` is a real, CHEAP-declared provider, so
#: the paid gate does not fire and the write mode resolves to the real provider path;
#: the agent behind the name is the fake, registered before the service exists.
FAKE_PROVIDER = "deepseek"
FAKE_MODEL = "deepseek-chat"

#: The only text the fake ever returns in in-process mode: one real file, in the
#: fenced form the guarded writer parses. It is a stdlib server on a port chosen by
#: the operating system, so the runner has to observe the port for real.
MARKER = "FORGE-JOURNEY-OK"
APP_PY = '''"""The journey project: a tiny loopback server."""

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

ANSWER = "Вот проект.\n\n```python path=app.py\n" + APP_PY + "```\n"

#: The fake's second answer: the change request's real files. ``app.py`` is rewritten
#: (a real update the guarded writer diffs) and one new module is created, so version
#: 2 keeps a launchable entry point and a manifest of its own.
CHANGE_ANSWER = (
    "Вот доработка.\n\n"
    "```python path=app.py\n"
    + APP_PY.replace('"""The journey project: a tiny loopback server."""',
                     '"""The journey project: a tiny loopback server (version 2)."""')
    + "```\n"
    "```python path=report.py\n"
    '"""Export rows as CSV text."""\n\n\n'
    "def export(rows):\n"
    '    lines = ["category,amount"]\n'
    "    for category, amount in rows:\n"
    '        lines.append(f"{category},{amount}")\n'
    '    return "\\n".join(lines) + "\\n"\n'
    "```\n"
)


class _FakeProvider(Provider):
    """A provider that returns scripted answers in order, and records every request.

    The last answer repeats if the service asks more often than the script has
    entries, so a runaway retry shows up as a request count instead of an exception.
    """

    def __init__(self, answers: list[str], *, provider_name: str = FAKE_PROVIDER,
                 model_name: str = FAKE_MODEL) -> None:
        from app.agents.providers.config import ProviderConfig

        super().__init__(ProviderConfig(provider_name=provider_name, model_name=model_name))
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
# a client that is the real app in-process, or the running app over HTTP
# --------------------------------------------------------------------------- #


class _Answer:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body
        try:
            self.text = body.decode("utf-8")
        except UnicodeDecodeError:  # pragma: no cover - JSON is always UTF-8 here
            self.text = body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self._body.decode("utf-8"))


class _HttpClient:
    """The subset of ``TestClient`` this file uses, over urllib (evidence mode)."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def _call(self, method: str, path: str, payload=None) -> _Answer:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"accept": "application/json", "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return _Answer(response.status, response.read())
        except urllib.error.HTTPError as error:
            return _Answer(error.code, error.read())

    def get(self, path: str) -> _Answer:
        return self._call("GET", path)

    def post(self, path: str, payload=None) -> _Answer:
        return self._call("POST", path, payload if payload is not None else {})

    def delete(self, path: str) -> _Answer:
        return self._call("DELETE", path)


class _TestClientAdapter:
    """``TestClient`` plus the same ``json``/``text`` helpers ``_HttpClient`` has."""

    def __init__(self, client) -> None:
        self._client = client

    def _wrap(self, response) -> _Answer:
        return _Answer(response.status_code, response.content)

    def get(self, path: str) -> _Answer:
        return self._wrap(self._client.get(path))

    def post(self, path: str, payload=None) -> _Answer:
        return self._wrap(self._client.post(path, json=payload if payload is not None else {}))

    def delete(self, path: str) -> _Answer:
        return self._wrap(self._client.delete(path))


# --------------------------------------------------------------------------- #
# the production UI functions, executed from their own sources
# --------------------------------------------------------------------------- #


def _run_node(script: str) -> str:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - node is present on this host
        raise unittest.SkipTest("node is not installed; the UI rules cannot be executed")
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


def _select_run_project(raw_id: object, lifecycle_ids: list[str]) -> dict:
    """Execute ``ui/src/project-selection.js``: the rule the result card uses."""

    script = f"""
import {{ selectRunProject }} from {json.dumps(SELECTION_MODULE.as_uri())};
process.stdout.write(JSON.stringify(selectRunProject({json.dumps(raw_id)}, {json.dumps(lifecycle_ids)})));
"""
    return json.loads(_run_node(script))


def _card(facts: dict) -> dict:
    """Execute ``ui/src/forge-result.ts``: the headline and actions the card offers."""

    script = (
        f"import {{ deriveRunHeadline }} from {json.dumps(CARD_MODULE.as_uri())};"
        "process.stdout.write(JSON.stringify(deriveRunHeadline('ru', "
        + json.dumps(facts)
        + ")));"
    )
    return json.loads(_run_node(script))


# --------------------------------------------------------------------------- #
# the journey
# --------------------------------------------------------------------------- #


class UserJourneyTests(unittest.TestCase):
    """The owner's path, once, with a user-visible invariant at every step."""

    maxDiff = None

    def setUp(self) -> None:
        self.steps: list[str] = []
        live = os.environ.get("FORGE_JOURNEY_API", "").strip()
        self.live = bool(live)
        self.fake: _FakeProvider | None = None
        self.screens: list[tuple[str, str]] = []
        self._last_screen: str = ""
        self.files_sha: dict[str, str] = {}
        self._stage_seen: list[str] = []

        if self.live:
            self.client = _HttpClient(live)
            return

        base = REPOSITORY / ".forge" / "test-user-journey" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.root = base / "projects"
        self.discovery_root = base / "discovery"
        self.run_root = base / "runs"
        self.run_workspace = base / "run-workspace"
        for directory in (self.root, self.discovery_root, self.run_root, self.run_workspace):
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
        # into the runtime beforehand is discarded and the real provider would be
        # left in place (the mistake that once caused a real call).
        runtime = offline_runtime()
        self.fake = _FakeProvider([ANSWER, CHANGE_ANSWER])
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

    # ------------------------------------------------------------------ helpers
    def _stop_everything(self) -> None:
        runner = getattr(getattr(self, "app", None), "state", None)
        for name in ("forge_runner", "forge_previews"):
            target = getattr(runner, name, None) if runner is not None else None
            if target is not None:
                try:
                    target.stop_all()
                except Exception:  # noqa: BLE001 - shutdown is best effort
                    pass

    def _json(self, answer: _Answer, label: str) -> dict:
        self.assertLess(answer.status_code, 400, f"{label}: {answer.status_code} {answer.text}")
        return answer.json()

    def _wait(self, call, predicate, label: str, timeout: float = 60.0):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = call()
            if predicate(last):
                return last
            time.sleep(0.2)
        self.fail(f"timed out waiting for {label}; last answer was {last!r}")

    def _screen(self, label: str, value: dict, *, expect_change: bool = True) -> None:
        """Record one visible screen; assert progress showed something new.

        ``expect_change`` is True for every step where the user expects the product
        to move. It is False for a step that is deliberately a *repeat* of an earlier
        one (opening a project, verifying again): those must be idempotent, not novel.
        """

        fingerprint = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if expect_change and self._last_screen:
            self.assertNotEqual(
                fingerprint,
                self._last_screen,
                f"the screen at '{label}' is byte-identical to the previous one, so the "
                f"step showed the user nothing: {fingerprint}",
            )
        self._last_screen = fingerprint
        self.screens.append((label, fingerprint))

    def _questions_screen(self, payload: dict) -> tuple:
        normalised = tuple(
            sorted(
                " ".join(str(item.get("text", "")).split()).strip(" ?!.,:;—-").lower()
                for item in payload.get("questions") or ()
            )
        )
        return normalised

    def _project_detail(self, project_id: str) -> dict:
        detail = self._json(self.client.get(f"/api/projects/{project_id}"), "project detail")
        self.assertEqual(detail["project"]["project_id"], project_id)
        return detail

    # ------------------------------------------------------------------- steps
    def test_00_the_journey(self) -> None:
        client = self.client
        PROVIDER = "mock" if self.live else FAKE_PROVIDER
        MODEL = "" if self.live else FAKE_MODEL

        # ------------------------------------------------------------ 1. the idea
        opened = self._json(
            client.post("/api/discovery/sessions", {"goal": GOAL}), "1. open session"
        )
        session = opened["session"]
        session_id = str(session["session_id"])
        self.assertTrue(session_id)
        self.assertEqual(session["state"], "NEEDS_CLARIFICATION")
        questions = list(opened["questions"])
        self.assertTrue(questions, "the flow stopped before asking the user anything")
        self._screen(
            "1. questions asked",
            {"state": session["state"], "questions": self._questions_screen(opened)},
        )
        self.steps.append(
            f"1. idea typed -> session {session_id}, state NEEDS_CLARIFICATION, "
            f"{len(questions)} questions"
        )

        # ------------------------------------------------------ 2. the answers
        # Exactly the payload shape ``ForgeScreen`` sends: the clicked option value
        # and/or the owner's own words, per question id.
        answers = []
        for question in questions:
            options = list(question.get("options") or ())
            recommended = [
                option
                for option in options
                if option.get("recommended") or option.get("value") in (
                    question.get("recommended") or ()
                )
            ]
            chosen = recommended or options[:1]
            item: dict = {"question_id": str(question["id"])}
            if chosen:
                item["values"] = [str(option.get("value", "")) for option in chosen]
                item["text"] = "; ".join(str(option.get("label", "")) for option in chosen)
                item["custom"] = ""
            else:
                item["text"] = "Нужен простой вариант: одна страница и никаких зависимостей."
            answers.append(item)
        answered = self._json(
            client.post(f"/api/discovery/sessions/{session_id}/answers", {"answers": answers}),
            "2. answer questions",
        )
        after_questions = self._questions_screen(answered)
        before_questions = self._questions_screen(opened)
        # THE SHIPPED LOOP: the same question set must never come back.
        self.assertNotEqual(
            after_questions,
            before_questions,
            "the answers produced the same questions again",
        )
        self.assertTrue(
            set(after_questions).isdisjoint(before_questions),
            f"an answered question was asked again: {after_questions} vs {before_questions}",
        )
        if answered["session"]["state"] != "READY_FOR_PLANNING":
            skipped = self._json(
                client.post(f"/api/discovery/sessions/{session_id}/skip", {"reason": "остальное некритично"}),
                "2. skip the round",
            )
            answered = skipped
        self.assertEqual(answered["session"]["state"], "READY_FOR_PLANNING", answered)
        self._screen(
            "2. answers accepted",
            {"state": answered["session"]["state"], "questions": after_questions},
        )
        self.steps.append(
            f"2. answered {len(answers)} questions -> state {answered['session']['state']}, "
            f"remaining questions {len(after_questions)} (was {len(before_questions)})"
        )

        # -------------------------------------------------- 3. the specification
        specification = self._json(
            client.get(f"/api/discovery/sessions/{session_id}/specification"),
            "3. specification",
        )
        self.assertTrue(specification["task_text"].strip())
        self.assertTrue(
            str(specification["specification"].get("goal", "")).strip(),
            "the specification carried no goal",
        )
        confirmed = self._json(
            client.post(f"/api/discovery/sessions/{session_id}/confirm"), "3. confirm"
        )
        self.assertEqual(confirmed["session"]["state"], "CONFIRMED")
        project = confirmed.get("project") or {}
        project_id = str(project.get("project_id", ""))
        self.assertRegex(project_id, r"^proj-[0-9a-f]{12}$")
        self.assertEqual(project.get("status"), "PLANNED")
        self._screen(
            "3. specification confirmed",
            {"state": confirmed["session"]["state"], "project": project_id},
        )
        self.steps.append(
            f"3. specification confirmed -> project {project_id} "
            f"(status {project.get('status')})"
        )

        # ------------------------------------------------------------- 4. the run
        run_payload: dict = {
            "description": "Собери проект по подтверждённой спецификации.",
            "category": "code",
            "write_mode": True,
            "discovery_session_id": session_id,
        }
        if PROVIDER:
            run_payload["provider_name"] = PROVIDER
        if MODEL:
            run_payload["model_name"] = MODEL
        dispatched = self._json(client.post("/api/tasks/run", run_payload), "4. run")
        self.assertTrue(dispatched.get("success"), dispatched)
        self.assertGreater(int(dispatched.get("files_created", 0)), 0, dispatched)
        self.assertEqual(str(dispatched.get("lifecycle_project_id", "")), project_id)
        if not self.live:
            # The two identifiers are different facts: only the lifecycle one is a
            # project «Проекты» can open, and the card reads that field.
            self.assertEqual(str(dispatched.get("project_id", "")), WORKSPACE_PROJECT_ID)
            self.assertNotEqual(WORKSPACE_PROJECT_ID, project_id)
        if not self.live:
            # The fake really answered, so the text this project contains is the one
            # this test supplied -- no other provider can have been reached.
            self.assertIsNotNone(self.fake)
            self.assertEqual(len(self.fake.requests), 1, "the fake provider was not asked once")
            self.assertEqual(str(dispatched.get("provider_used", "")), FAKE_PROVIDER)
        detail = self._project_detail(project_id)
        self.assertEqual(detail["files_created"], int(dispatched["files_created"]))
        self.assertTrue(detail["files"], "the project record names no file")
        names = [str(item["path"]) for item in detail["files"]]
        self.assertTrue(names, detail["files"])
        self.assertEqual(len({item["sha256"] for item in detail["files"]}), len(names))
        self.files_sha = {str(item["path"]): str(item["sha256"]) for item in detail["files"]}
        # NO FALSE COMPLETION: a card that says ГОТОВО must have files, a passed
        # writer verification and a passed acceptance -- read through the card.
        if detail["presentation"]["project_state"] == "ГОТОВО":
            self.assertGreater(detail["files_created"], 0)
            version = detail["versions"][0] if detail["versions"] else {}
            self.assertEqual(str(version.get("verification", "")), "passed", version)
            self.assertEqual(str(version.get("acceptance", "")), "passed", version)
        self.assertTrue(
            detail["presentation"]["project_state"] == "ГОТОВО",
            detail["presentation"],
        )
        self._screen(
            "4. files received",
            {
                "project_state": detail["presentation"]["project_state"],
                "files": detail["files_created"],
                "stage": detail["stage"],
            },
        )
        self.steps.append(
            f"4. run {dispatched['run_id']} -> {detail['files_created']} files "
            f"{names}, card {detail['presentation']['project_state']!r}, stage {detail['stage']}"
        )

        # ------------------------------------------- 5. the result card's action
        lifecycle_ids = [
            str(row["project_id"])
            for row in self._json(client.get("/api/projects/lifecycle"), "5. lifecycle list")
        ]
        self.assertIn(project_id, lifecycle_ids)
        facts = {
            "filesCreated": int(dispatched["files_created"]),
            "writeStatus": "applied",
            "verification": str(
                (detail["versions"][0] if detail["versions"] else {}).get("verification", "")
            ),
            "writeReason": "",
            "questions": [],
            "needsClarification": False,
            "clarificationRound": int(dispatched.get("clarification_round", 0) or 0),
            "error": str(dispatched.get("error", "") or ""),
            "writeRequested": True,
        }
        headline = _card(facts)
        self.assertIn("open_files", headline["actions"], headline)
        target = _select_run_project(project_id, lifecycle_ids)
        self.assertEqual(target, {"projectId": project_id, "reason": ""})
        opened_files = self._json(
            client.get(f"/api/projects/{target['projectId']}/files"), "5. open files"
        )
        self.assertEqual(opened_files["project_id"], project_id)
        self.assertEqual(opened_files["count"], detail["files_created"])
        self.assertNotEqual(target["projectId"], WORKSPACE_PROJECT_ID)
        self.steps.append(
            f"5. card action {headline['actions']} opens {target['projectId']} "
            f"-> files route answers with {opened_files['count']} files"
        )

        # ------------------------------------------------------- 6. start + URL
        entry = detail.get("entrypoint") or {}
        self.assertTrue(entry, "the project has no entry point to start")
        run_url = ""
        live_process = False
        if str(entry.get("serve_kind")) == "static":
            started = self._json(
                client.post(f"/api/projects/{project_id}/preview", {"action": "start"}),
                "6. start preview",
            )
            self.assertTrue(started.get("running"), started)
            run_url = str(started.get("url", ""))
        else:
            digest = str((detail.get("run") or {}).get("entrypoint_digest") or entry.get("digest") or "")
            self.assertTrue(digest, "the digest the button approves is missing")
            started = self._json(
                client.post(
                    f"/api/projects/{project_id}/run",
                    {"confirm": True, "entrypoint_digest": digest},
                ),
                "6. start run",
            )
            self.assertGreater(int(started.get("pid") or 0), 0, started)
            live_process = True
            status = self._wait(
                lambda: self._json(client.get(f"/api/projects/{project_id}/run"), "6. run status"),
                lambda value: value["state"] == "RUNNING" and str(value.get("url", "")).strip() != "",
                "the local process to bind a port",
            )
            run_url = str(status["url"])
        self.assertTrue(run_url.startswith("http://127.0.0.1:"), run_url)
        preview = self._json(client.get(f"/api/projects/{project_id}/preview"), "6. preview")
        self.assertTrue(preview["available"], preview)
        self.assertTrue(preview["running"], preview)
        self.assertEqual(str(preview["url"]), run_url)
        with urllib.request.urlopen(run_url, timeout=20) as response:
            self.assertEqual(response.status, 200)
            served = response.read()
        if live_process:
            self.assertIn(MARKER.encode("utf-8"), served)
            served_note = f"200 from the child, marker {MARKER!r}"
        else:
            # The static preview serves the project's own file, byte for byte.
            entry_path = str(entry.get("path") or "index.html")
            self.assertIn(entry_path, self.files_sha)
            self.assertEqual(hashlib.sha256(served).hexdigest(), self.files_sha[entry_path])
            served_note = f"200, sha256 of {entry_path} matches the artifact"
        self._screen(
            "6. project started",
            {"url": run_url, "entry": str(entry.get("kind")), "preview": preview["source"]},
        )
        self.steps.append(
            f"6. started {entry.get('kind')} -> {run_url}, {served_note}"
        )

        # ------------------------------------------------------------ 7. stop it
        if live_process:
            stopped = self._json(
                client.post(f"/api/projects/{project_id}/run/stop"), "7. stop run"
            )
            self.assertIn(str(stopped.get("state", "")), {"STOPPED", "EXITED", "FAILED"}, stopped)
        else:
            stopped = self._json(
                client.post(f"/api/projects/{project_id}/preview", {"action": "stop"}),
                "7. stop preview",
            )
            self.assertTrue(stopped.get("stopped"), stopped)
        after = self._json(client.get(f"/api/projects/{project_id}/preview"), "7. preview after stop")
        self.assertFalse(after["running"], after)
        self.assertEqual(str(after["url"]), "")
        run_after = self._json(client.get(f"/api/projects/{project_id}/run"), "7. run after stop")
        self.assertNotIn(str(run_after.get("state", "")), {"RUNNING", "STARTING"}, run_after)
        self._screen("7. project stopped", {"url": "", "run": str(run_after.get("state", ""))})
        self.steps.append(
            f"7. stopped -> run {run_after.get('state')!r}, preview not running, no URL"
        )

        # -------------------------------------------------------- 8. change request
        staged = self._json(
            client.post(f"/api/projects/{project_id}/versions", {"change": CHANGE}),
            "8. stage change",
        )
        self.assertEqual(int(staged["version"]), 2)
        self.assertIn(CHANGE.split(":")[0][:20], str(staged["task_text"]))
        history = self._json(
            client.get(f"/api/projects/{project_id}/history"), "8. history"
        )
        self.assertGreaterEqual(history["count"], 2)
        self.assertEqual(int(history["versions"][0]["version"]), 2)
        self.assertIsNotNone(history["versions"][0].get("change_request"))

        # The staged task text is what the existing run path executes (the service's
        # own ``NewVersionTask`` says so). The confirmed session is named again, so
        # the change run is recorded against the same project instead of vanishing
        # into a run workspace.
        change_payload: dict = {
            "description": str(staged["task_text"]),
            "category": "code",
            "write_mode": True,
            "discovery_session_id": session_id,
        }
        if PROVIDER:
            change_payload["provider_name"] = PROVIDER
        if MODEL:
            change_payload["model_name"] = MODEL
        change_run = self._json(
            client.post("/api/tasks/run", change_payload), "8. run the change"
        )
        self.assertTrue(change_run.get("success"), change_run)
        self.assertGreater(int(change_run.get("files_created", 0)), 0, change_run)
        self.assertEqual(str(change_run.get("lifecycle_project_id", "")), project_id)
        after_change = self._project_detail(project_id)
        self.assertEqual(
            str(after_change["project"]["current_version"]), "2",
            "the change run did not become the current version",
        )
        self.assertEqual(int(after_change["versions"][0]["version"]), 2)
        self.assertIsNotNone(after_change["versions"][0].get("change_request"))
        self.assertEqual(
            str(after_change["versions"][0].get("run_id", "")),
            str(change_run.get("run_id", "")),
            "version 2 does not name the run that filled it",
        )
        self.assertIs(after_change["presentation"]["change_not_written"], False)
        self.assertTrue(after_change["files_created"] > 0)
        # The change must not remove the project's entry point: a version that cannot
        # be started again would be a regression dressed up as a new version.
        self.assertIsNotNone(
            after_change["entrypoint"], after_change["entrypoint_reason"]
        )
        if not self.live:
            self.assertIsNotNone(self.fake)
            self.assertEqual(len(self.fake.requests), 2, "the fake provider was not asked twice")
        self._screen(
            "8. change received",
            {
                "version": 2,
                "files": after_change["files_created"],
                "state": after_change["presentation"]["project_state"],
            },
        )
        self.steps.append(
            f"8. change request {CHANGE!r} staged (task text {len(str(staged['task_text']))} "
            f"chars) and run {change_run['run_id']} filled v2 -> "
            f"{after_change['files_created']} files, card "
            f"{after_change['presentation']['project_state']!r}"
        )

        # ------------------------------------------------------------- 9. delete
        deleted = self._json(client.delete(f"/api/projects/{project_id}"), "9. delete")
        self.assertTrue(deleted["deleted"], deleted)
        gone = client.get(f"/api/projects/{project_id}")
        self.assertEqual(gone.status_code, 404, gone.text)
        trash = self._json(client.get("/api/projects/deleted"), "9. trash")
        row = next((item for item in trash if item["project_id"] == project_id), None)
        self.assertIsNotNone(row, f"the deleted project is not in the trash: {trash}")
        self.assertEqual(int(row["file_count"]), after_change["files_created"])
        self._screen("9. project deleted", {"trash": row["trash_name"], "files": row["file_count"]})
        self.steps.append(
            f"9. deleted -> 404 on the project, trash holds it as {row['trash_name']!r} "
            f"with {row['file_count']} files"
        )

        # ------------------------------------------------------------ 10. restore
        restored = self._json(
            client.post(f"/api/projects/{project_id}/restore-deleted"), "10. restore"
        )
        self.assertEqual(restored["project_id"], project_id)
        back = self._project_detail(project_id)
        self.assertEqual(back["files_created"], after_change["files_created"])
        self.assertEqual(
            {str(item["path"]): str(item["sha256"]) for item in back["files"]},
            {str(item["path"]): str(item["sha256"]) for item in after_change["files"]},
            "the restored project's files are not the bytes that were deleted",
        )
        self.assertEqual(
            [int(item["version"]) for item in back["versions"]],
            [int(item["version"]) for item in after_change["versions"]],
            "the restored project lost a version",
        )
        self.assertEqual(
            str(back["project"]["current_version"]),
            str(after_change["project"]["current_version"]),
        )
        self.assertEqual(
            back["presentation"]["project_state"],
            after_change["presentation"]["project_state"],
            "the restored card says something the card before the delete did not",
        )
        self.assertEqual(back["stage"], after_change["stage"])
        self._screen(
            "10. project restored",
            {"files": back["files_created"], "stage": back["stage"], "ready": back["presentation"]["project_state"]},
        )
        self.steps.append(
            f"10. restored -> same {back['files_created']} files (sha256 identical), "
            f"versions {[item['version'] for item in back['versions']]}, stage {back['stage']}"
        )

        # ------------------------------------------------------ 11. verify again
        verified = self._json(
            client.post(f"/api/projects/{project_id}/verify"), "11. verify again"
        )
        self.assertTrue(verified["verified"], verified)
        self.assertIn(str(verified["verification"]), {"passed", "not_applicable"}, verified)
        final = self._project_detail(project_id)
        self.assertEqual(final["files_created"], back["files_created"])
        self.assertNotEqual(final["stage"], "PROJECT_NOT_CREATED", final["stage"])
        # NO FALSE COMPLETION at the end of the journey: the word ГОТОВО is allowed
        # only with the files and both verdicts that justify it.
        if final["presentation"]["project_state"] == "ГОТОВО":
            self.assertGreater(final["files_created"], 0)
            version = final["versions"][0] if final["versions"] else {}
            self.assertEqual(str(version.get("verification", "")), "passed", version)
            self.assertEqual(str(version.get("acceptance", "")), "passed", version)
        self._screen(
            "11. verified again",
            {
                "verification": verified["verification"],
                "stage": final["stage"],
                "state": final["presentation"]["project_state"],
            },
            expect_change=False,
        )
        self.steps.append(
            f"11. verified again -> {verified['verification']}, status {verified['status']}, "
            f"{final['files_created']} files still present, card "
            f"{final['presentation']['project_state']!r}"
        )

        # Every screen a step recorded is distinct from the step before it (enforced by
        # ``_screen``), at most one deliberate repeat is allowed, and the conversation
        # is printed so a report can paste it.
        self.assertGreaterEqual(len(self.screens), 10)
        self.assertGreaterEqual(
            len({fingerprint for _label, fingerprint in self.screens}),
            len(self.screens) - 1,  # step 11 is the deliberate repeat
        )
        print("\n".join(self.steps))


if __name__ == "__main__":
    unittest.main()
