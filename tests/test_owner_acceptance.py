"""The owner's promise, asserted literally, in one offline end-to-end run.

His words: *«Он должен работать как швейцарские часы: я вставляю промт и в конце
получаю на рабочий стол готовый проект. Из Forge должен быть вход в проект — кнопка
перехода. И как мне корректировать проект — как в терминале VS Code?»*

So this file walks that sentence and stops at every clause:

```
idea -> questions -> confirm -> run -> files exist in the visible project root
     -> "открыть папку" resolves to that folder and really issues the launch
     -> correction -> v2 -> the old version is byte-identical
     -> the project really runs and its preview answers 200
     -> delete -> restore -> still in the visible folder, same bytes, same versions
```

Nothing here is a simulation of the product: the application is ``create_api_app``,
the lifecycle service and store are the shipped ones, the run is the real
``POST /api/tasks/run`` path, the writer really compiles and writes files, the local
runner really starts a child process on ``127.0.0.1`` and the preview really answers.
Two things are injected, and both are named here so nobody mistakes them for the real
thing: the **model** is an in-process scripted provider installed at composition time
(``ForgeApiService.__init__`` rebuilds the executor, so a fake patched in later is
discarded and a real provider would answer), and the **OS opener**'s spawn function
records the argv instead of opening a real Explorer window.

The visible root is a throwaway directory under ``.forge`` -- never the owner's real
desktop -- because a test must not write to his machine. What is *asserted* is the
product property: a project's files are in a normal folder under the configured root,
the record is untouched, and every owner action reaches the folder that folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import time
import unittest
import urllib.request

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.config.app_settings import AppSettingsStore
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.opener import ProjectOpener
from app.projects.service import ProjectService
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]

#: The Core workspace this server is composed for; never a ``proj-…`` id.
WORKSPACE_PROJECT_ID = "owner-acceptance-workspace"

GOAL = (
    "Хочу маленький локальный сервер-визитку: одна страница с приветствием. "
    "Никаких внешних сервисов, баз данных и ключей."
)
CHANGE = "Добавь модуль, который печатает версию проекта."

FAKE_PROVIDER = "deepseek"
FAKE_MODEL = "deepseek-chat"

MARKER = "FORGE-OWNER-ACCEPTANCE-OK"

APP_PY = '''"""The delivered project: a tiny loopback page."""

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

CREATE_ANSWER = (
    "Вот проект.\n\n"
    "```python path=app.py\n" + APP_PY + "```\n\n"
    "```markdown path=README.md\n"
    "# Визитка\n\n"
    "Одна локальная страница с приветствием.\n"
    "```\n"
)

#: The correction **updates** the entry file and **creates** one new file. That is the
#: shape the owner will use in practice («добавь/поправь …»), and it is what makes both
#: halves of the promise checkable: the previous version's record is byte-identical, the
#: file the correction did not touch is byte-identical, and the project keeps its entry
#: point so it can be started again.
APP_PY_V2 = APP_PY.replace(
    'body = b"%s\\n"' % MARKER,
    'body = b"%s (v1.1)\\n"' % MARKER,
)
CHANGE_ANSWER = (
    "Вот доработка.\n\n"
    "```python path=app.py\n" + APP_PY_V2 + "```\n\n"
    "```python path=version.py\n"
    '"""The project version, as one constant."""\n\n'
    'VERSION = "1.1"\n'
    "```\n"
)

QUESTION_ANSWER = "Простой вариант: одна страница и никаких зависимостей."


class _FakeProvider(Provider):
    """A scripted provider that records every request; the last answer repeats."""

    def __init__(self, answers: list[str]) -> None:
        from app.agents.providers.config import ProviderConfig

        super().__init__(
            ProviderConfig(provider_name=FAKE_PROVIDER, model_name=FAKE_MODEL)
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


class _RecordingSpawn:
    """Records the launched argv instead of opening a real window."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, vector, **kwargs):
        self.calls.append([str(item) for item in vector])
        kwargs.setdefault("shell", False)
        assert kwargs.get("shell") is False

        class _Child:
            pid = 24680

        return _Child()


class _FakeWhich:
    """What this composition has installed: a file manager and a shell, no VS Code."""

    INSTALLED = {
        "explorer": r"C:\Windows\explorer.exe",
        "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    }

    def __call__(self, program: str):
        return self.INSTALLED.get(program)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version_json(history: dict, number: int) -> str:
    for item in history["versions"]:
        if int(item["version"]) == number:
            return json.dumps(item, sort_keys=True, ensure_ascii=False)
    raise AssertionError(f"version {number} is not in the history: {history}")


class OwnerAcceptanceTest(unittest.TestCase):
    """One journey, from the prompt to the folder on disk, offline."""

    maxDiff = None

    def setUp(self) -> None:
        base = REPOSITORY / ".forge" / "test-owner-acceptance" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        self.base = base.resolve()
        self.records = self.base / "records"
        self.visible = self.base / "Desktop" / "Forge Projects"
        self.discovery_root = self.base / "discovery"
        self.run_root = self.base / "runs"
        self.run_workspace = self.base / "run-workspace"
        self.settings_path = self.base / "settings.json"
        for directory in (
            self.records,
            self.visible,
            self.discovery_root,
            self.run_root,
            self.run_workspace,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        previous = os.environ.get(PROJECTS_ROOT_ENV_VAR)
        os.environ[PROJECTS_ROOT_ENV_VAR] = str(self.records)

        def _restore() -> None:
            if previous is None:
                os.environ.pop(PROJECTS_ROOT_ENV_VAR, None)
            else:
                os.environ[PROJECTS_ROOT_ENV_VAR] = previous

        self.addCleanup(_restore)

        # THE SETTING. The owner's project root is the application's own file, and the
        # store reads it through a callable, exactly as the launcher composes it.
        self.settings = AppSettingsStore(path=self.settings_path)
        self.settings.set_project_root(str(self.visible))
        self.store = ProjectStore(
            root=self.records, visible_root=lambda: self.settings.project_root()
        )
        self.projects = ProjectService(self.store)
        self.discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))

        # THE FAKE IS INSTALLED AT COMPOSITION TIME, never after it.
        runtime = offline_runtime()
        self.fake = _FakeProvider([CREATE_ANSWER, CHANGE_ANSWER])
        runtime.agent_registry._agents[FAKE_PROVIDER] = ProviderAgent(self.fake)

        service = ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.run_root),
            workspace=Workspace(self.run_workspace),
            authorized_project_id=WORKSPACE_PROJECT_ID,
        )
        self.spawn = _RecordingSpawn()
        self.opener = ProjectOpener(
            which=_FakeWhich(),
            spawn=self.spawn,
            # The suite must not depend on what is installed on this machine.
            exists=lambda item: False,
        )
        from fastapi.testclient import TestClient

        self.app = create_api_app(
            service,
            discovery_service=self.discovery,
            project_service=self.projects,
            project_opener=self.opener,
            settings_store=self.settings,
        )
        self.addCleanup(self._stop_everything)
        self.client = TestClient(self.app)
        self.steps: list[str] = []

    def _stop_everything(self) -> None:
        state = getattr(self.app, "state", None)
        for name in ("forge_runner", "forge_previews"):
            target = getattr(state, name, None) if state is not None else None
            if target is not None:
                try:
                    target.stop_all()
                except Exception:  # noqa: BLE001 - shutdown is best effort
                    pass

    # ------------------------------------------------------------------ helpers
    def _json(self, answer, label: str) -> dict:
        self.assertLess(
            answer.status_code, 400, f"{label}: {answer.status_code} {answer.text}"
        )
        return answer.json()

    def _detail(self, identifier: str) -> dict:
        return self._json(
            self.client.get(f"/api/projects/{identifier}"), "project detail"
        )

    def _wait(self, call, predicate, label: str, timeout: float = 40.0):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = call()
            if predicate(last):
                return last
            time.sleep(0.2)
        self.fail(f"timed out waiting for {label}; last answer was {last!r}")

    def _folder_on_disk(self, detail: dict) -> pathlib.Path:
        return pathlib.Path(detail["diagnostics"]["folder_absolute"])

    def _files_on_disk(self, folder: pathlib.Path) -> dict[str, bytes]:
        """The project's own files; the writer's ``.forge/backups`` are not project files.

        The guarded writer keeps a byte-for-byte backup of a file it overwrites under
        ``<workspace>/.forge/backups/run-<id>/`` -- that is the existing reversibility
        mechanism, unchanged by this work, and it is filtered out here so a comparison
        of *the project's* files is not confused by it.
        """

        return {
            path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*"))
            if path.is_file()
            and not path.relative_to(folder).as_posix().startswith(".forge/")
        }

    # -------------------------------------------------------------------- steps
    def test_00_the_owner_promise(self) -> None:
        client = self.client

        # ------------------------------------------------- 0. the root setting
        settings = self._json(client.get("/api/settings"), "0. settings")
        self.assertEqual(settings["project_root"], str(self.visible))
        self.assertTrue(settings["project_root_exists"])
        self.assertNotIn("key", json.dumps(settings).lower())

        # ------------------------------------------------- 1. the idea, questions
        opened = self._json(
            client.post("/api/discovery/sessions", json={"goal": GOAL}), "1. idea"
        )
        session_id = str(opened["session"]["session_id"])
        self.assertTrue(session_id)
        questions = list(opened.get("questions") or ())
        self.assertEqual(opened["session"]["state"], "NEEDS_CLARIFICATION", opened)
        self.assertTrue(questions, "the flow must ask something before it writes")
        self.steps.append(f"1. idea -> {len(questions)} questions")

        # ------------------------------------------------------- 2. the answers
        answers = []
        for question in questions:
            options = list(question.get("options") or ())
            recommended = [
                option
                for option in options
                if option.get("recommended")
                or option.get("value") in (question.get("recommended") or ())
            ]
            chosen = recommended or options[:1]
            item: dict = {"question_id": str(question["id"])}
            if chosen:
                item["values"] = [str(option.get("value", "")) for option in chosen]
                item["text"] = "; ".join(str(option.get("label", "")) for option in chosen)
            else:
                item["text"] = QUESTION_ANSWER
            answers.append(item)
        answered = self._json(
            client.post(
                f"/api/discovery/sessions/{session_id}/answers",
                json={"answers": answers},
            ),
            "2. answers",
        )
        if answered["session"]["state"] != "READY_FOR_PLANNING":
            answered = self._json(
                client.post(
                    f"/api/discovery/sessions/{session_id}/skip",
                    json={"reason": "остальное некритично"},
                ),
                "2. skip the rest",
            )
        self.assertEqual(answered["session"]["state"], "READY_FOR_PLANNING", answered)
        self.steps.append("2. answered -> READY_FOR_PLANNING")

        # ---------------------------------------------------------- 3. confirm
        confirmed = self._json(
            client.post(f"/api/discovery/sessions/{session_id}/confirm"), "3. confirm"
        )
        project_id = str(confirmed["project"]["project_id"])
        self.assertRegex(project_id, r"^proj-[0-9a-f]{12}$")
        self.assertEqual(confirmed["project"]["status"], "PLANNED")
        self.steps.append(f"3. confirmed -> project {project_id}")

        # -------------------------------------------------------------- 4. run
        dispatched = self._json(
            client.post(
                "/api/tasks/run",
                json={
                    "description": "Собери проект по подтверждённой спецификации.",
                    "category": "code",
                    "write_mode": True,
                    "discovery_session_id": session_id,
                    "provider_name": FAKE_PROVIDER,
                    "model_name": FAKE_MODEL,
                },
            ),
            "4. run",
        )
        self.assertTrue(dispatched.get("success"), dispatched)
        self.assertEqual(str(dispatched["lifecycle_project_id"]), project_id)
        self.assertEqual(len(self.fake.requests), 1, "the fake provider was not asked once")
        detail = self._detail(project_id)
        self.assertGreater(detail["files_created"], 0, detail)

        # ------------------------------------ 5. the files are in the visible root
        folder = self._folder_on_disk(detail)
        self.assertTrue(folder.is_dir(), f"the project folder does not exist: {folder}")
        self.assertEqual(
            folder.parent,
            self.visible.resolve(),
            "the project's files must be a normal folder under the configured root",
        )
        self.assertIn(project_id, folder.name)
        on_disk = self._files_on_disk(folder)
        self.assertEqual(
            sorted(on_disk),
            sorted(str(item["path"]) for item in detail["files"]),
            "the manifest and the folder disagree",
        )
        for item in detail["files"]:
            self.assertEqual(
                hashlib.sha256(on_disk[str(item["path"])]).hexdigest(),
                str(item["sha256"]),
                f"{item['path']} on disk is not the recorded bytes",
            )
        # The record stayed where it was: only the files are visible.
        self.assertTrue((self.records / f"{project_id}.json").is_file())
        self.assertFalse(
            (self.records / project_id).exists(),
            "the record directory must not hold the project's files any more",
        )
        # The card: relative above the fold, absolute only in the diagnostics block.
        self.assertFalse(pathlib.Path(detail["project"]["project_path"]).is_absolute())
        self.assertTrue(
            detail["project"]["project_path"].endswith(folder.name),
            detail["project"]["project_path"],
        )
        presentation = detail["presentation"]
        above_the_fold = json.dumps(
            {
                "headline": presentation.get("headline"),
                "facts": presentation.get("facts"),
                "stage_label": presentation.get("stage_label"),
                "project_state": presentation.get("project_state"),
            },
            ensure_ascii=False,
        )
        self.assertNotIn(
            str(self.visible),
            above_the_fold,
            "an absolute path leaked above the fold",
        )
        fields = {
            field["key"]: field["value"]
            for field in presentation["technical"]["fields"]
        }
        self.assertEqual(fields["folder_absolute"], str(folder))
        self.assertTrue(fields["folder_absolute"].startswith(str(self.base)))
        self.steps.append(
            f"4-5. {detail['files_created']} files in {folder.name!r} under "
            f"{self.visible.name!r}, record untouched"
        )

        # ------------------------------------------------ 6. «Открыть папку»
        availability = detail["open"]["targets"]
        self.assertTrue(availability["folder"]["available"], availability)
        opened_folder = self._json(
            client.post(
                f"/api/projects/{project_id}/open", json={"target": "folder"}
            ),
            "6. open folder",
        )
        self.assertTrue(opened_folder["opened"], opened_folder)
        self.assertEqual(opened_folder["diagnostics"]["folder"], str(folder))
        self.assertEqual(len(self.spawn.calls), 1, "the open action issued no launch")
        launched = self.spawn.calls[0]
        self.assertEqual(
            pathlib.Path(launched[1]).resolve(), folder.resolve(),
            "the launch did not name the project's folder",
        )
        self.steps.append(
            f"6. «Открыть папку» -> {pathlib.Path(launched[0]).name} {launched[1]!r}"
        )

        # ---------------------------------------------------- 7. the correction
        history_before = self._json(
            client.get(f"/api/projects/{project_id}/history"), "7. history"
        )
        version_one = _version_json(history_before, 1)
        readme_before = _sha256(folder / "README.md")

        staged = self._json(
            client.post(f"/api/projects/{project_id}/versions", json={"change": CHANGE}),
            "7. stage the correction",
        )
        self.assertEqual(int(staged["version"]), 2)
        self.assertIn("CHANGE REQUEST", str(staged["task_text"]))
        change_run = self._json(
            client.post(
                "/api/tasks/run",
                json={
                    "description": str(staged["task_text"]),
                    "category": "code",
                    "write_mode": True,
                    "lifecycle_project_id": project_id,
                    "provider_name": FAKE_PROVIDER,
                    "model_name": FAKE_MODEL,
                },
            ),
            "7. run the correction",
        )
        self.assertTrue(change_run.get("success"), change_run)
        self.assertEqual(str(change_run["lifecycle_project_id"]), project_id)
        self.assertEqual(len(self.fake.requests), 2, "the correction asked no model")

        after = self._detail(project_id)
        self.assertEqual(str(after["project"]["current_version"]), "2")
        self.assertEqual(int(after["versions"][0]["version"]), 2)
        self.assertEqual(
            str(after["versions"][0]["run_id"]), str(change_run["run_id"])
        )
        # v2 is in the same visible folder…
        again = self._folder_on_disk(after)
        self.assertEqual(again, folder)
        self.assertTrue((again / "version.py").is_file())
        # …and v1 is byte-identical: its record and its file on disk.
        history_after = self._json(
            client.get(f"/api/projects/{project_id}/history"), "7. history after"
        )
        self.assertEqual(_version_json(history_after, 1), version_one)
        # The file the correction did not touch is byte-identical…
        self.assertEqual(_sha256(again / "README.md"), readme_before)
        # …and the correction is described as what it did: one update, one create.
        self.assertEqual(
            {str(item["path"]): str(item["change"]) for item in after["files"]},
            {"app.py": "update", "version.py": "create"},
            "v2's manifest is what *this* run changed",
        )
        self.assertEqual(
            sorted(self._files_on_disk(again)),
            ["README.md", "app.py", "version.py"],
            "the correction must add to the folder, not replace it",
        )
        self.assertTrue(int(after["files_created"]) == 2, after["files_created"])
        self.steps.append(
            f"7. correction {CHANGE!r} -> v2 changed {after['files_created']} files "
            "(app.py updated, version.py created); v1 record and README.md byte-identical"
        )

        # ------------------------------------------------ 8. run and preview 200
        digest = str((after.get("run") or {}).get("entrypoint_digest") or "")
        self.assertTrue(digest, f"no runnable entry point: {after.get('run')}")
        started = self._json(
            client.post(
                f"/api/projects/{project_id}/run",
                json={"confirm": True, "entrypoint_digest": digest, "offline": True},
            ),
            "8. start the project",
        )
        self.assertGreater(int(started.get("pid") or 0), 0, started)
        status = self._wait(
            lambda: self._json(client.get(f"/api/projects/{project_id}/run"), "8. run status"),
            lambda value: value["state"] == "RUNNING" and str(value.get("url", "")) != "",
            "the project to bind a port",
        )
        url = str(status["url"])
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        preview = self._json(
            client.get(f"/api/projects/{project_id}/preview"), "8. preview"
        )
        self.assertTrue(preview["available"], preview)
        self.assertTrue(preview["running"], preview)
        self.assertEqual(str(preview["url"]), url)
        with urllib.request.urlopen(url, timeout=20) as response:
            self.assertEqual(response.status, 200)
            served = response.read()
        self.assertIn(MARKER.encode("utf-8"), served)
        stopped = self._json(
            client.post(f"/api/projects/{project_id}/run/stop"), "8. stop"
        )
        self.assertIn(str(stopped.get("state", "")), {"STOPPED", "EXITED", "FAILED"}, stopped)
        self.steps.append(f"8. run -> {url} answered 200 with {MARKER!r}, then stopped")

        # ---------------------------------------- 9. delete, 10. restore
        before_delete = self._files_on_disk(folder)
        deleted = self._json(
            client.delete(f"/api/projects/{project_id}"), "9. delete"
        )
        self.assertTrue(deleted["deleted"], deleted)
        self.assertFalse(folder.exists(), "the visible folder must move into the trash")
        self.assertEqual(
            client.get(f"/api/projects/{project_id}").status_code, 404
        )
        trash = self._json(client.get("/api/projects/deleted"), "9. trash")
        row = next(
            (item for item in trash if item["project_id"] == project_id), None
        )
        self.assertIsNotNone(row, trash)
        self.assertEqual(int(row["file_count"]), after["files_created"])
        self.assertNotIn(str(self.visible), json.dumps(row, ensure_ascii=False))

        restored = self._json(
            client.post(f"/api/projects/{project_id}/restore-deleted"), "10. restore"
        )
        self.assertTrue(restored["restored"], restored)
        back = self._detail(project_id)
        self.assertEqual(self._folder_on_disk(back), folder)
        self.assertEqual(
            self._files_on_disk(folder),
            before_delete,
            "the restored project's files are not the bytes that were deleted",
        )
        self.assertEqual(
            [int(item["version"]) for item in back["versions"]],
            [int(item["version"]) for item in after["versions"]],
        )
        self.assertEqual(
            str(back["project"]["current_version"]),
            str(after["project"]["current_version"]),
        )
        self.assertEqual(back["project"]["project_path"], after["project"]["project_path"])
        self.steps.append(
            f"9-10. delete -> trash {row['trash_name']!r}, restore -> same visible "
            f"folder, {len(before_delete)} files byte-identical, v2 current"
        )

        # ------------------------------------------- 11. the options stay offered
        final = self._detail(project_id)
        self.assertTrue(final["open"]["targets"]["folder"]["available"], final["open"])
        self.assertTrue(final["open"]["targets"]["editor"]["reason"] != "")
        # The editor stays unavailable only because this composition installed none;
        # the reason is a sentence, not an empty string.
        self.assertIsNotNone(final["entrypoint"], final["entrypoint_reason"])
        print("\n".join(self.steps))


#: The idea the owner really submitted, copied character for character from the live
#: failure. The project's folder was named
#: «Создай локальное приложение «Почтовый центр» — только Python (ст» -- the first 60
#: characters of this paragraph, cut mid-word.
OWNER_LIVE_IDEA = (
    "Создай локальное приложение «Почтовый центр» — только Python (стек: FastAPI), "
    "чтобы я мог читать и отправлять почту с рабочего стола"
)


class OwnerLiveCaseTest(unittest.TestCase):
    """The owner's exact case, end to end: a long Russian idea with a quoted name.

    Two defects met in one HTTP answer on his machine:

    * the folder was named from the raw description, truncated mid-word;
    * an unwritable preferred root turned his own action into a refusal.

    This test drives the real surface with his own text: the folder is the quoted name,
    short and valid; the whole idea is still in the specification; the project lands in
    the configured root when it is writable; and when it is **not**, the same confirm
    succeeds in the documented fallback and says so in plain language.
    """

    def setUp(self) -> None:
        base = (
            REPOSITORY
            / ".forge"
            / "test-owner-live-case"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.base = base.resolve()
        self.records = self.base / "records"
        self.discovery_root = self.base / "discovery"
        self.run_workspace = self.base / "workspace"
        for directory in (self.records, self.discovery_root, self.run_workspace):
            directory.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.base / "settings.json"
        self.visible = self.base / "profile" / "Forge Projects"

    def _app(self, *, root: pathlib.Path, candidates=None):
        """The real surface, composed exactly as ``main.py`` composes it.

        The settings file is this test's own, and the policy reads the *same* settings the
        routes read -- including ``remember``/``known`` -- so "which folder is used" and
        "which folder is reported" cannot disagree, which is the property the owner needs.
        """

        from fastapi.testclient import TestClient

        from app.projects.root_selection import ProjectRootPolicy
        from app.projects.service import ProjectService
        from app.projects.store import ProjectStore

        settings = AppSettingsStore(path=self.settings_path)
        settings.set_project_root(str(root))
        policy = ProjectRootPolicy(
            lambda: settings.project_root(),
            remember=settings.remember_fallback_root,
            known=settings.known_project_roots,
            candidates=candidates,
        )
        projects = ProjectService(
            ProjectStore(root=self.records, root_policy=policy)
        )
        service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.base / "runs"),
            workspace=Workspace(self.run_workspace),
            authorized_project_id=WORKSPACE_PROJECT_ID,
        )
        app = create_api_app(
            service,
            discovery_service=DiscoveryService(
                DiscoveryStore(root=self.discovery_root)
            ),
            project_service=projects,
            project_opener=ProjectOpener(
                which=_FakeWhich(),
                spawn=_RecordingSpawn(),
                exists=lambda item: False,
            ),
            settings_store=settings,
        )
        return TestClient(app), projects

    def _session(self, client, idea: str) -> str:
        opened = client.post("/api/discovery/sessions", json={"goal": idea}).json()
        session_id = str(opened["session"]["session_id"])
        if opened["session"].get("state") != "READY_FOR_PLANNING":
            client.post(
                f"/api/discovery/sessions/{session_id}/skip",
                json={"reason": "достаточно для первой версии"},
            )
        return session_id

    def test_01_his_idea_becomes_a_short_quoted_folder_name(self) -> None:
        self.visible.mkdir(parents=True, exist_ok=True)
        client, projects = self._app(
            root=self.visible, candidates=lambda preferred: [preferred]
        )
        session_id = self._session(client, OWNER_LIVE_IDEA)
        answer = client.post(f"/api/discovery/sessions/{session_id}/confirm")
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        project_id = str(body["project"]["project_id"])
        # The short, human name and the folder that carries it.
        self.assertEqual(body["project"]["name"], "Почтовый центр")
        folder = projects.store.workspace_path(project_id)
        self.assertEqual(folder.name, f"Почтовый центр ({project_id})")
        self.assertNotIn("Создай", folder.name)
        self.assertLessEqual(len(folder.name), 40 + len(project_id) + 3)
        # The full idea is not lost: it is in the specification, where it belongs.
        record = projects.open_project(project_id)
        self.assertEqual(record.specification["goal"], OWNER_LIVE_IDEA)
        # A writable root is used as it is, with nothing announced.
        self.assertNotIn("project_root", body)
        settings = client.get("/api/settings").json()
        self.assertFalse(settings["project_root_used_fallback"])
        self.assertEqual(settings["project_root_effective"], str(self.visible))

    def test_02_the_same_confirm_survives_an_unwritable_desktop_root(self) -> None:
        # The owner's real situation: the configured Desktop root cannot be created by
        # this process, so the documented fallback is used instead -- and said out loud.
        blocker = self.base / "not-a-folder"
        blocker.write_text("x", encoding="utf-8")
        blocked = blocker / "Forge Projects"
        self.visible.mkdir(parents=True, exist_ok=True)
        client, projects = self._app(
            root=blocked, candidates=lambda preferred: [blocked, self.visible]
        )
        session_id = self._session(client, OWNER_LIVE_IDEA)
        answer = client.post(f"/api/discovery/sessions/{session_id}/confirm")
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        project_id = str(body["project"]["project_id"])
        notice = body["project_root"]
        self.assertTrue(notice["fallback"])
        self.assertEqual(notice["root"], str(self.visible))
        self.assertIn("недоступна для записи", notice["notice"])
        self.assertIn(str(self.visible), notice["notice"])
        self.assertTrue(notice["attempted"][0]["reason"])
        folder = projects.store.workspace_path(project_id)
        self.assertTrue(folder.is_dir())
        self.assertEqual(folder.parent, self.visible.resolve())
        # The name is short here too: the fallback changes *where*, not *what*.
        self.assertEqual(body["project"]["name"], "Почтовый центр")
        self.assertEqual(folder.name, f"Почтовый центр ({project_id})")
        # The settings screen agrees with the response, and the choice is remembered.
        settings = client.get("/api/settings").json()
        self.assertTrue(settings["project_root_used_fallback"])
        self.assertEqual(settings["project_root_effective"], str(self.visible))
        self.assertIn(str(self.visible), settings["project_root_notice"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()