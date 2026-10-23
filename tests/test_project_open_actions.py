"""«Вход в проект» really opens something: folder, VS Code, terminal.

The owner's second complaint: *«из Forge должен быть вход в проект — кнопка
перехода»*. The old card's «Открыть папку» printed a sentence saying a browser cannot
open folders, so the button did nothing. This file proves the three real actions
against the real FastAPI surface, with the process launcher replaced by a recorder:

1. ``POST /api/projects/{id}/open {"target": "folder"}`` starts ``explorer.exe`` with
   the project's **absolute folder** -- taken from the record, never from the request;
2. ``{"target": "editor"}`` starts ``code <folder>`` when VS Code resolves, and answers a
   typed ``409`` with a plain sentence when it does not (nothing is started);
3. ``{"target": "terminal"}`` prefers ``wt -d <folder>`` and falls back to
   ``powershell -NoExit -Command Set-Location -LiteralPath '<folder>'``;
4. every target refuses when the project holds **no files** or the folder is **missing**,
   with the same sentence the card shows beside the disabled button;
5. an unknown target is ``400`` and is never passed to the opener; every launch uses
   ``shell=False`` and an argv, so a folder name cannot become a command;
6. the detail payload carries, per target, whether it is available and why not.

No real Explorer window is opened by the suite: ``spawn`` is injected and records the
argument vector. The live proof that a real process is started is in
``.forge/swiss_live_proof.py`` (recorded response, screenshots in
``.forge/screenshots/``).
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import unittest

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.opener import (
    OPEN_TARGETS,
    OpenRefusal,
    ProjectOpener,
    TARGET_EDITOR,
    TARGET_FOLDER,
    TARGET_TERMINAL,
)
from app.projects.service import ProjectService
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
WORKSPACE_PROJECT_ID = "open-actions-workspace"
GOAL = "Локальная визитка: одна страница с приветствием."


#: A program table a test can control: what "is installed" on this run.
class _FakeWhich:
    def __init__(self, installed: dict[str, str]) -> None:
        self.installed = dict(installed)
        self.asked: list[str] = []

    def __call__(self, program: str):
        self.asked.append(program)
        return self.installed.get(program)


class _RecordingSpawn:
    """Records every launch instead of starting a real program."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, vector, **kwargs):
        self.calls.append({"vector": list(vector), "kwargs": dict(kwargs)})

        class _Child:
            pid = 4321

        return _Child()


class _Session:
    def __init__(self, goal: str = GOAL) -> None:
        self.session_id = f"disc-open-{os.getpid()}-{id(self)}"
        self.initial_goal = goal
        self.specification = {"goal": goal, "platform": "веб-приложение"}


class _Case(unittest.TestCase):
    """A throwaway store with one project that holds files, and the real app."""

    installed = {
        "explorer": r"C:\Windows\explorer.exe",
        "code": r"C:\Users\tester\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd",
        "wt": r"C:\Users\tester\AppData\Local\Microsoft\WindowsApps\wt.exe",
        "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    }

    def setUp(self) -> None:
        base = (
            REPOSITORY / ".forge" / "test-open-actions" / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base.resolve()
        self.root = self.base / "projects"
        self.visible = self.base / "visible"
        self.discovery_root = self.base / "discovery"
        self.run_root = self.base / "runs"
        self.run_workspace = self.base / "run-workspace"
        for directory in (
            self.root,
            self.visible,
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

        self.store = ProjectStore(root=self.root, visible_root=self.visible)
        self.projects = ProjectService(self.store)
        self.discovery = DiscoveryService(DiscoveryStore(root=self.discovery_root))
        self.which = _FakeWhich(self.installed)
        self.spawn = _RecordingSpawn()
        self.opener = ProjectOpener(
            which=self.which, spawn=self.spawn, exists=lambda item: False
        )

        runtime = offline_runtime()
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
            project_opener=self.opener,
        )
        self.client = TestClient(self.app)

    # ------------------------------------------------------------------ helpers
    def _project(self, files: dict | None = None):
        project = self.projects.create_from_discovery(_Session())
        self.projects.confirm(project.project_id)
        for relative, data in (files if files is not None else {"app.py": b"print('hi')\n"}).items():
            self.store.write_workspace_file(project.project_id, relative, data)
        return project

    def _detail(self, project_id: str) -> dict:
        answer = self.client.get(f"/api/projects/{project_id}")
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def _open(self, project_id: str, target: str):
        return self.client.post(f"/api/projects/{project_id}/open", json={"target": target})


# --------------------------------------------------------------------------- #
# the opener, in isolation
# --------------------------------------------------------------------------- #


class OpenerUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        base = REPOSITORY / ".forge" / "test-open-actions-unit" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.folder = base / "Проект (proj-abc)"
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "app.py").write_text("print(1)\n", encoding="utf-8")

    def _opener(self, installed: dict) -> tuple[ProjectOpener, _RecordingSpawn]:
        spawn = _RecordingSpawn()
        return (
            ProjectOpener(
                which=_FakeWhich(installed),
                spawn=spawn,
                # Nothing is "installed" beyond what the test named.
                exists=lambda item: False,
            ),
            spawn,
        )

    def test_01_the_three_targets_are_the_whole_vocabulary(self) -> None:
        self.assertEqual(
            OPEN_TARGETS, (TARGET_FOLDER, TARGET_EDITOR, TARGET_TERMINAL)
        )

    def test_02_folder_starts_explorer_with_the_absolute_folder(self) -> None:
        opener, spawn = self._opener({"explorer": r"C:\Windows\explorer.exe"})
        result = opener.open(TARGET_FOLDER, self.folder)
        self.assertEqual(len(spawn.calls), 1)
        vector = spawn.calls[0]["vector"]
        self.assertEqual(pathlib.Path(vector[0]).name.lower(), "explorer.exe")
        self.assertEqual(pathlib.Path(vector[1]).name, self.folder.name)
        self.assertTrue(pathlib.Path(vector[1]).is_absolute())
        self.assertFalse(spawn.calls[0]["kwargs"].get("shell", False))
        self.assertEqual(result.program.lower(), "explorer.exe")
        self.assertEqual(result.pid, 4321)

    def test_03_editor_starts_vs_code_with_the_folder(self) -> None:
        opener, spawn = self._opener({"code": "C:/VS Code/bin/code.cmd"})
        opener.open(TARGET_EDITOR, self.folder)
        vector = spawn.calls[0]["vector"]
        self.assertEqual(pathlib.Path(vector[0]).name.lower(), "code.cmd")
        self.assertEqual(pathlib.Path(vector[1]).name, self.folder.name)

    def test_04_editor_without_vs_code_is_a_typed_refusal(self) -> None:
        opener, spawn = self._opener({})
        availability = opener.availability(self.folder)
        self.assertFalse(availability[TARGET_EDITOR]["available"])
        self.assertIn("VS Code", availability[TARGET_EDITOR]["reason"])
        with self.assertRaises(OpenRefusal):
            opener.open(TARGET_EDITOR, self.folder)
        self.assertEqual(spawn.calls, [], "a refusal must start nothing")

    def test_05_terminal_prefers_windows_terminal(self) -> None:
        opener, spawn = self._opener(
            {"wt": r"C:\Users\t\WindowsApps\wt.exe", "powershell": r"C:\ps\powershell.exe"}
        )
        opener.open(TARGET_TERMINAL, self.folder)
        vector = spawn.calls[0]["vector"]
        self.assertEqual(pathlib.Path(vector[0]).name.lower(), "wt.exe")
        self.assertEqual(vector[1], "-d")
        self.assertEqual(pathlib.Path(vector[2]).name, self.folder.name)

    def test_06_terminal_falls_back_to_powershell_with_a_quoted_literal_path(self) -> None:
        opener, spawn = self._opener({"powershell": r"C:\ps\powershell.exe"})
        opener.open(TARGET_TERMINAL, self.folder)
        vector = spawn.calls[0]["vector"]
        self.assertEqual(pathlib.Path(vector[0]).name.lower(), "powershell.exe")
        self.assertEqual(vector[1:3], ["-NoExit", "-Command"])
        command = vector[3]
        self.assertTrue(command.startswith("Set-Location -LiteralPath '"), command)
        self.assertEqual(command.count("'"), 2, command)
        # The path is quoted, never concatenated as a bare word.
        self.assertNotIn(";", command)
        self.assertNotIn("&", command)

    def test_07_no_files_or_a_missing_folder_refuses_every_target(self) -> None:
        opener, spawn = self._opener(
            {"explorer": "C:/Windows/explorer.exe", "code": "C:/code.cmd", "wt": "C:/wt.exe"}
        )
        empty = self.folder.parent / "empty"
        empty.mkdir()
        for folder, expected in ((empty, "нет файлов"), (None, "не найдена")):
            for target in OPEN_TARGETS:
                with self.assertRaises(OpenRefusal) as raised:
                    opener.open(target, folder)
                self.assertIn(expected, str(raised.exception))
        missing = self.folder.parent / "gone"
        for target in OPEN_TARGETS:
            with self.assertRaises(OpenRefusal):
                opener.open(target, missing)
        self.assertEqual(spawn.calls, [], "a refusal must start nothing")

    def test_08_an_unknown_target_is_never_started(self) -> None:
        opener, spawn = self._opener({"explorer": "C:/Windows/explorer.exe"})
        with self.assertRaises(OpenRefusal):
            opener.open("cmd", self.folder)
        self.assertEqual(spawn.calls, [])

    def test_09_availability_never_starts_a_process(self) -> None:
        opener, spawn = self._opener({"explorer": "C:/Windows/explorer.exe"})
        availability = opener.availability(self.folder)
        self.assertEqual(set(availability), set(OPEN_TARGETS))
        self.assertTrue(availability[TARGET_FOLDER]["available"])
        self.assertEqual(spawn.calls, [])

    def test_10_a_folder_name_cannot_become_a_command(self) -> None:
        hostile = self.folder.parent / "x & calc.exe"
        hostile.mkdir(parents=True, exist_ok=True)
        (hostile / "a.txt").write_text("x", encoding="utf-8")
        opener, spawn = self._opener({"powershell": r"C:\ps\powershell.exe"})
        opener.open(TARGET_TERMINAL, hostile)
        vector = spawn.calls[0]["vector"]
        # The whole path is one argv element, and it is quoted inside the one command
        # that carries text; the shell metacharacter is data, not syntax.
        self.assertEqual(len(vector), 4)
        self.assertIn("x & calc.exe", vector[3])
        self.assertEqual(vector[3].count("'"), 2)


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #


class OpenRouteTests(_Case):
    """The card's three buttons, against the real HTTP surface."""

    def test_01_open_folder_starts_explorer_at_the_recorded_folder(self) -> None:
        project = self._project()
        answer = self._open(project.project_id, TARGET_FOLDER)
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertTrue(body["opened"])
        folder = self.store.workspace_path(project.project_id)
        self.assertEqual(body["diagnostics"]["folder"], str(folder))
        vector = self.spawn.calls[0]["vector"]
        self.assertEqual(pathlib.Path(vector[1]).name, folder.name)
        self.assertTrue(pathlib.Path(vector[1]).is_absolute())

    def test_02_the_request_can_never_name_the_path(self) -> None:
        project = self._project()
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        answer = self.client.post(
            f"/api/projects/{project.project_id}/open",
            json={"target": TARGET_FOLDER, "path": str(elsewhere), "folder": str(elsewhere)},
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        vector = self.spawn.calls[0]["vector"]
        self.assertNotIn(str(elsewhere), vector)

    def test_03_editor_is_a_typed_refusal_without_vs_code(self) -> None:
        self.which.installed.pop("code")
        project = self._project()
        answer = self._open(project.project_id, TARGET_EDITOR)
        self.assertEqual(answer.status_code, 409, answer.text)
        body = answer.json()
        self.assertEqual(body["detail"]["code"], "project_open_refused")
        self.assertIn("VS Code", body["detail"]["error"])
        self.assertEqual(self.spawn.calls, [])

    def test_04_terminal_opens_windows_terminal_at_the_folder(self) -> None:
        project = self._project()
        answer = self._open(project.project_id, TARGET_TERMINAL)
        self.assertEqual(answer.status_code, 200, answer.text)
        vector = self.spawn.calls[0]["vector"]
        self.assertEqual(pathlib.Path(vector[0]).name.lower(), "wt.exe")
        self.assertEqual(vector[1], "-d")

    def test_05_an_empty_project_refuses_all_three(self) -> None:
        project = self._project(files={})
        for target in OPEN_TARGETS:
            answer = self._open(project.project_id, target)
            self.assertEqual(answer.status_code, 409, f"{target}: {answer.text}")
            self.assertIn("нет файлов", answer.json()["detail"]["error"])
        self.assertEqual(self.spawn.calls, [])

    def test_06_a_missing_folder_refuses_and_does_not_recreate_it(self) -> None:
        project = self._project()
        folder = self.store.workspace_path(project.project_id)
        shutil.rmtree(folder)
        self.assertFalse(folder.exists())
        answer = self._open(project.project_id, TARGET_FOLDER)
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertIn("не найдена", answer.json()["detail"]["error"])
        self.assertFalse(folder.exists(), "opening must not recreate the folder")
        self.assertEqual(self.spawn.calls, [])

    def test_07_an_unknown_target_is_400_and_unknown_project_is_404(self) -> None:
        project = self._project()
        bad = self.client.post(
            f"/api/projects/{project.project_id}/open", json={"target": "cmd"}
        )
        self.assertEqual(bad.status_code, 400, bad.text)
        self.assertEqual(self.spawn.calls, [])
        missing = self._open("proj-000000000000", TARGET_FOLDER)
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_08_the_detail_payload_says_what_can_be_opened(self) -> None:
        project = self._project()
        detail = self._detail(project.project_id)
        block = detail["open"]
        self.assertEqual(set(block["targets"]), set(OPEN_TARGETS))
        for target, line in block["targets"].items():
            self.assertTrue(line["available"], f"{target}: {line}")
            self.assertEqual(line["reason"], "")
            self.assertTrue(line["label"])
        self.assertEqual(block["files"], 1)
        self.assertTrue(block["folder_exists"])
        self.assertFalse(pathlib.Path(block["folder"]).is_absolute())

    def test_09_the_detail_payload_names_the_absolute_folder_in_diagnostics(self) -> None:
        project = self._project()
        detail = self._detail(project.project_id)
        folder = self.store.workspace_path(project.project_id)
        self.assertEqual(detail["diagnostics"]["folder_absolute"], str(folder))
        self.assertEqual(detail["diagnostics"]["project_root"], str(self.visible))
        # Above the fold the published path stays relative.
        self.assertFalse(pathlib.Path(detail["project"]["project_path"]).is_absolute())
        self.assertNotIn(str(self.visible), json.dumps(detail["presentation"], ensure_ascii=False))
        # …and the collapsed technical block is where the absolute path may appear.
        fields = {
            field["key"]: field["value"]
            for field in detail["presentation"]["technical"]["fields"]
        }
        self.assertEqual(fields["folder_absolute"], str(folder))

    def test_10_the_unavailable_reason_travels_in_both_places(self) -> None:
        self.which.installed.pop("code")
        self.which.installed.pop("wt")
        self.which.installed.pop("powershell")
        project = self._project()
        detail = self._detail(project.project_id)
        for target in (TARGET_EDITOR, TARGET_TERMINAL):
            line = detail["open"]["targets"][target]
            self.assertFalse(line["available"], target)
            self.assertNotEqual(line["reason"], "")
            refused = self._open(project.project_id, target)
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.json()["detail"]["error"], line["reason"])


# --------------------------------------------------------------------------- #
# the migration route
# --------------------------------------------------------------------------- #


class MoveToVisibleFolderRouteTests(_Case):
    def test_01_a_legacy_project_is_delivered_to_the_visible_root(self) -> None:
        # A project created by a store with no root configured (the old behaviour).
        legacy_store = ProjectStore(root=self.root)
        legacy = ProjectService(legacy_store)
        project = legacy.create_from_discovery(_Session())
        legacy_store.write_workspace_file(project.project_id, "app.py", b"print('x')\n")
        legacy_folder = legacy_store.workspace_path(project.project_id)
        self.assertIn(self.root.resolve(), legacy_folder.parents)

        answer = self.client.post(
            f"/api/projects/{project.project_id}/move-to-visible-folder"
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertFalse(body["already_visible"])
        self.assertEqual(body["moved_count"], 1)
        folder = self.store.workspace_path(project.project_id)
        self.assertEqual(folder.parent, self.visible.resolve())
        self.assertTrue((folder / "app.py").is_file())
        self.assertFalse(legacy_folder.is_dir())
        self.assertEqual(body["diagnostics"]["folder_absolute"], str(folder))

    def test_02_a_visible_project_reports_the_no_op(self) -> None:
        project = self._project()
        answer = self.client.post(
            f"/api/projects/{project.project_id}/move-to-visible-folder"
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertTrue(answer.json()["already_visible"])

    def test_03_an_unknown_project_is_404(self) -> None:
        answer = self.client.post("/api/projects/proj-000000000000/move-to-visible-folder")
        self.assertEqual(answer.status_code, 404, answer.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
