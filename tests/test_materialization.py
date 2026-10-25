"""Materialisation into a project workspace, the entry point, the preview and the gate.

Every test here is offline and deterministic: the only provider is the repository's
``mock`` one (for the MOCK_DETERMINISTIC mode) or a fixed in-process stand-in that
returns one canned answer (for REAL_PROVIDER_EXPLICIT). No socket is opened to a
provider, no credential is read and nothing is spent. The preview test does open a
loopback socket -- that is the feature under test -- and closes it in a cleanup.

What is proved here, one rule per test group:

* **PART 1** an answer lands inside the project's own controlled folder
  (``<store>/<project_id>/workspace``), never the repository root; the existing
  boundary and deny list refuse ``..``, absolute paths, ``.env``, ``.git`` and
  credential-shaped names -- this module does not re-implement those rails, so the
  assertions are about the writer's own refusals reaching the caller;
* the artifact manifest carries path, kind, size, sha256, create/update and
  verification relevance, and **no contents**;
* **PART 2** exactly two write modes exist, and a run that does not satisfy one
  writes nothing and says so;
* **PART 3** the entry point is derived from the manifest plus the files really at
  the project root (a program first, a page second); a static preview serves the file
  and refuses traversal; a python/node entry point reports that local run is not
  wired;
* **PART 4** a change request that writes nothing does not advance the version, and
  older versions stay byte-identical;
* no secret-looking value and no file body reaches an event or a project payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import unittest
import urllib.error
import urllib.request
from dataclasses import replace

from app.agent_runtime.outcome import completion_blockers
from app.api.models import TaskRunRequest
from app.api.server import create_api_app
from app.api.service import (
    WRITE_MODE_MOCK_DETERMINISTIC,
    WRITE_MODE_REAL_PROVIDER_EXPLICIT,
    ForgeApiService,
)
from app.orchestrator.models import Run, RunState, TaskResult
from app.projects.entrypoint import (
    ENTRY_KIND_NODE,
    ENTRY_KIND_PYTHON,
    ENTRY_KIND_STATIC_HTML,
    SERVE_KIND_PROCESS,
    SERVE_KIND_STATIC,
    STATIC_RUN_COMMAND,
    detect_entry_point,
)
from app.projects.materialize import (
    FAILED,
    FILES_CREATED,
    PROJECT_NOT_CREATED,
    VERIFIED,
    materialize,
)
from app.projects.models import ArtifactRef, ProjectStatus, ProjectVersion, utc_now
from app.projects.mock_answer import deterministic_project_answer
from app.projects.preview import LOCAL_RUN_NOT_WIRED, NO_ENTRY_POINT, PreviewRegistry
from app.projects.service import ProjectService
from app.projects.stage import (
    COMPLETED,
    NEEDS_CHANGES,
    PREVIEW_AVAILABLE,
    PROJECT_NOT_CREATED as STAGE_PROJECT_NOT_CREATED,
    STAGE_WORDS,
    VERIFIED as STAGE_VERIFIED,
    project_stage,
)
from app.projects.store import ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRATCH = REPO_ROOT / ".forge" / "test-materialization"

#: A marker that must never appear in a manifest, an event or a project payload.
BODY_MARKER = "BODY-MARKER-9f3a"

#: A credential-shaped value in the answer. It is written to the file (the writer
#: does not screen file bodies) but must never reach a record or a payload.
SECRET_LOOKING = "sk-live-9f3a4b5c6d7e8f90a1b2c3d4"

GOAL = "Небольшой веб-инструмент: страница расходов и модуль отчёта."


def _fenced(path: str, body: str, language: str = "") -> str:
    header = f"{language} " if language else ""
    return f"```{header}path={path}\n{body}\n```\n"


ANSWER_STATIC_AND_PYTHON = (
    "Two files.\n\n"
    + _fenced(
        "index.html",
        "<!doctype html>\n<html><body><h1>Учёт</h1>"
        f"<p>{BODY_MARKER}</p></body></html>",
        "html",
    )
    + "\n"
    + _fenced(
        "app.py",
        '"""A tiny module."""\n\n\ndef double(value):\n    return value * 2',
        "python",
    )
)

ANSWER_HTML_ONLY = _fenced(
    "index.html", f"<!doctype html><html><body>{BODY_MARKER}</body></html>", "html"
)

ANSWER_BROKEN_PYTHON = _fenced("broken.py", "def broken(:\n    return 1", "python")

ANSWER_WITH_SECRET = _fenced(
    "config.txt",
    f"endpoint=test\napi_key={SECRET_LOOKING}\n{BODY_MARKER}\n",
    "text",
)


class _Session:
    """The minimum a discovery session must expose for ``create_from_discovery``."""

    def __init__(self, session_id: str, goal: str = GOAL) -> None:
        self.session_id = session_id
        self.initial_goal = goal
        self.specification = {"goal": goal, "platform": "веб-приложение"}


class _FakeProviderExecutor:
    """A deterministic stand-in for a live provider: one canned answer, no socket."""

    def __init__(self, answer: str, provider: str = "fake-provider") -> None:
        self.answer = answer
        self.provider = provider
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):  # noqa: ANN003 - mirrors the executor signature
        self.calls.append(kwargs)
        task = kwargs["task"]
        run = Run(task=task, id=str(kwargs.get("run_id") or ""))
        run.state = RunState.COMPLETED
        run.result = TaskResult(
            task_id=task.id,
            success=True,
            output=self.answer,
            provider=self.provider,
            model_name="fake/model",
        )
        return run


class _MaterializationCase(unittest.TestCase):
    """A project with its own store, workspace and recorded versions."""

    def setUp(self) -> None:
        self.base = SCRATCH / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(self.base, ignore_errors=True)
        self.base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.store = ProjectStore(root=self.base / "projects")
        self.projects = ProjectService(self.store)
        created = self.projects.create_from_discovery(
            _Session(f"sess-{os.getpid()}-{id(self)}")
        )
        self.project = self.projects.confirm(created.project_id)
        self.run_store = RunStore(root=self.base / "runs")
        self.run_workspace = self.base / "run-workspace"
        self.run_workspace.mkdir(parents=True, exist_ok=True)
        self.service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=self.run_store,
            workspace=Workspace(root=self.run_workspace),
            authorized_project_id="test-materialization",
        )

    # ------------------------------------------------------------------ helpers
    def workspace(self) -> pathlib.Path:
        return self.store.workspace_path(self.project.project_id)

    def materialise(self, answer: str, run_id: str = "run-test-1", **kwargs):
        return materialize(
            self.project,
            answer,
            run_id,
            store=self.store,
            **kwargs,
        )

    def install_fake(self, answer: str) -> _FakeProviderExecutor:
        fake = _FakeProviderExecutor(answer)
        self.service.runtime = replace(self.service.runtime, run_executor=fake)
        return fake

    def project_workspace_kwargs(self) -> dict:
        return {
            "project_workspace": self.store.workspace_path(self.project.project_id, create=True),
            "lifecycle_project_id": self.project.project_id,
            "lifecycle_project_path": self.store.project_path_relative(
                self.project.project_id
            ),
        }

    def run_source(self, response) -> dict:
        """The run's durable record plus the attribution only the response carries."""

        record = dict(self.service.get_run_record(response.run_id) or {})
        record["provider_used"] = (
            record.get("provider_used") or getattr(response, "provider_used", "") or ""
        )
        record["model_used"] = (
            record.get("model_used") or getattr(response, "model_used", "") or ""
        )
        return record

    def attach(self, response, *, source_root: object = None):
        return self.projects.attach_run(
            self.project.project_id,
            self.run_source(response),
            source_root=(
                source_root
                if source_root is not None
                else self.store.workspace_path(self.project.project_id)
            ),
        )

    def workspace_files(self) -> list[pathlib.Path]:
        root = self.workspace()
        found: list[pathlib.Path] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            # The writer's own backup/scratch directory is not an artifact.
            if ".forge" in relative.parts:
                continue
            found.append(path)
        return sorted(found)


# --------------------------------------------------------------------------- #
# 1. materialisation into the project's own folder
# --------------------------------------------------------------------------- #


class MaterializeIntoProjectTests(_MaterializationCase):
    def test_01_files_land_in_the_project_workspace_not_the_repository(self) -> None:
        result = self.materialise(ANSWER_STATIC_AND_PYTHON)

        self.assertEqual(result.status, VERIFIED, result.reason)
        self.assertEqual(result.verification, "passed")
        self.assertEqual(result.files_created, 2)
        workspace = self.workspace()
        self.assertEqual(
            sorted(path.relative_to(workspace).as_posix() for path in self.workspace_files()),
            ["app.py", "index.html"],
        )
        for artifact in result.files:
            location = workspace.joinpath(*artifact.path.split("/"))
            self.assertTrue(location.is_file(), artifact.path)
            self.assertEqual(location.stat().st_size, artifact.size)
        # The project folder is the recorded, repository-relative one.
        self.assertEqual(
            result.project_path, self.store.project_path_relative(self.project.project_id)
        )
        # Nothing was written into the repository tree by this test's run.
        self.assertFalse((REPO_ROOT / "index.html").exists())
        self.assertFalse((REPO_ROOT / "app.py").exists())

    def test_02_a_text_only_answer_is_project_not_created(self) -> None:
        result = self.materialise("Here is a plan. I will create the files later.")

        self.assertEqual(result.status, PROJECT_NOT_CREATED)
        self.assertEqual(result.files_created, 0)
        self.assertEqual(result.files, ())
        self.assertEqual(self.workspace_files(), [])
        self.assertIn("no fenced block", result.reason)

    def test_03_the_manifest_holds_size_and_sha256_and_no_contents(self) -> None:
        result = self.materialise(ANSWER_HTML_ONLY)

        self.assertEqual(result.status, FILES_CREATED)
        self.assertEqual(len(result.files), 1)
        artifact = result.files[0]
        data = self.workspace().joinpath("index.html").read_bytes()
        self.assertEqual(artifact.path, "index.html")
        self.assertEqual(artifact.kind, "markup")
        self.assertEqual(artifact.size, len(data))
        self.assertEqual(artifact.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(artifact.change, "create")
        self.assertEqual(artifact.verification_relevance, "not_compiled")

        rows = result.manifest()
        self.assertEqual(
            set(rows[0]),
            {
                "path",
                "kind",
                "size",
                "sha256",
                "change",
                "verification_relevance",
                "location",
                "timestamp",
            },
        )
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn(BODY_MARKER, serialized)
        self.assertNotIn("<!doctype html>", serialized)
        self.assertNotIn("diff", serialized.lower())
        # The absolute workspace path is not part of the public result either.
        self.assertNotIn(str(self.workspace()), serialized)

    def test_04_the_boundary_refuses_parent_and_absolute_paths(self) -> None:
        for bad in ("../escape.py", "sub/../../escape.py", "/abs.py", "C:/Windows/x.py"):
            result = self.materialise(_fenced(bad, "print(1)", "python"))
            self.assertEqual(result.status, PROJECT_NOT_CREATED, bad)
            self.assertEqual(result.files_created, 0, bad)
            self.assertFalse((self.base / "escape.py").exists(), bad)
            self.assertNotEqual(result.reason, "", bad)
        self.assertEqual(self.workspace_files(), [])

    def test_05_the_deny_list_refuses_env_git_and_credential_shaped_names(self) -> None:
        cases = {
            ".env": "denied_environment_file",
            ".env.local": "denied_environment_file",
            ".git/config": "denied_directory",
            "app/secret_notes.txt": "denied_credential_like_name",
            "api_token.json": "denied_credential_like_name",
            "private_credentials.txt": "denied_credential_like_name",
            "authorizer.py": "denied_file",
            "app/platform/persistence/postgres/migrations/0001.py": "denied",
        }
        for path, expected in cases.items():
            result = self.materialise(_fenced(path, "value = 1", "python"))
            self.assertEqual(result.status, PROJECT_NOT_CREATED, path)
            self.assertIn(expected, result.reason, path)
        self.assertEqual(self.workspace_files(), [])
        self.assertFalse((self.workspace() / ".env").exists())

    def test_06_verification_runs_before_verified(self) -> None:
        result = self.materialise(ANSWER_BROKEN_PYTHON)

        self.assertEqual(result.status, FAILED)
        self.assertEqual(result.verification, "failed")
        self.assertIn("py_compile", result.reason)
        # The writer rolled the whole run back: a broken tree is never left behind.
        self.assertEqual(self.workspace_files(), [])

    def test_07_no_python_file_is_files_created_never_verified(self) -> None:
        result = self.materialise(ANSWER_HTML_ONLY)

        self.assertEqual(result.status, FILES_CREATED)
        self.assertEqual(result.verification, "not_applicable")
        self.assertNotEqual(result.status, VERIFIED)

    def test_08_materialize_refuses_a_workspace_it_does_not_own(self) -> None:
        other = self.base / "someone-elses-tree"
        other.mkdir()
        result = self.materialise(ANSWER_HTML_ONLY, workspace=other)

        self.assertEqual(result.status, PROJECT_NOT_CREATED)
        self.assertIn("workspace_is_not_the_project_workspace", result.reason)
        self.assertEqual(list(other.iterdir()), [])

    def test_09_an_update_is_recorded_as_update(self) -> None:
        first = self.materialise(ANSWER_HTML_ONLY, run_id="run-update-1")
        self.assertEqual(first.status, FILES_CREATED)
        second = self.materialise(
            _fenced("index.html", "<!doctype html><html><body>second</body></html>", "html"),
            run_id="run-update-2",
        )
        self.assertEqual(second.status, FILES_CREATED)
        self.assertEqual(second.files[0].change, "update")
        self.assertIn("second", (self.workspace() / "index.html").read_text("utf-8"))


# --------------------------------------------------------------------------- #
# 2. one safe entry point, from the manifest only
# --------------------------------------------------------------------------- #


class EntryPointTests(_MaterializationCase):
    def _write(self, relative: str, body: str = "x") -> None:
        target = self.workspace().joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def test_10_a_python_script_wins_over_a_page(self) -> None:
        # The owner's measured defect: a project with BOTH a server and a page was
        # launched as the page, so Forge served HTML with no backend and the window was
        # empty. A project is a program first and a page second.
        result = self.materialise(ANSWER_STATIC_AND_PYTHON)
        entry = detect_entry_point(result.files, workspace=self.workspace())
        self.assertIsNotNone(entry)
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON, entry)
        self.assertEqual(entry.path, "app.py")
        self.assertEqual(entry.serve_kind, SERVE_KIND_PROCESS)
        self.assertTrue(entry.runnable)
        self.assertEqual(entry.run_command, "python app.py")
        self.assertEqual(entry.command[1:], ("app.py",))
        self.assertFalse(entry.reason)

        # The page is still there and is still the fallback: when the process is not
        # the entry (it was removed), the same manifest resolves to the page.
        without_python = tuple(
            item for item in result.files if not item.path.endswith(".py")
        )
        (self.workspace() / "app.py").unlink()
        page = detect_entry_point(without_python, workspace=self.workspace())
        self.assertIsNotNone(page)
        self.assertEqual(page.kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(page.path, "index.html")
        self.assertEqual(page.serve_kind, SERVE_KIND_STATIC)
        # A static page is launched by the existing read-only preview route: no argv,
        # but runnable, and the command the card prints is the fixed sentence. It used
        # to report ``runnable: false`` with an empty command while the same route
        # answered HTTP 200 -- the measured defect this assertion pins.
        self.assertEqual(page.command, ())
        self.assertIs(page.runnable, True)
        self.assertEqual(page.run_command, STATIC_RUN_COMMAND)
        self.assertEqual(
            STATIC_RUN_COMMAND, "статическая страница, сервер не требуется"
        )
        self.assertEqual(page.to_dict()["runnable"], True)
        self.assertFalse(page.reason)

    def test_11_python_entries_keep_their_priority(self) -> None:
        for path, expected_priority in (("app.py", 0), ("main.py", 1), ("server.py", 2)):
            manifest = [
                ArtifactRef(path=name, kind="python")
                for name in ("app.py", "main.py", "server.py")
                if ("app.py", "main.py", "server.py").index(name) >= expected_priority
            ]
            entry = detect_entry_point(manifest, workspace=self.workspace())
            self.assertIsNotNone(entry)
            self.assertEqual(entry.kind, ENTRY_KIND_PYTHON)
            self.assertEqual(entry.path, path)
            self.assertEqual(entry.serve_kind, SERVE_KIND_PROCESS)
            self.assertEqual(entry.run_command, f"python {path}")

    def test_12_package_json_with_a_start_or_dev_script(self) -> None:
        for script, command in (("start", "npm start"), ("dev", "npm run dev")):
            self._write("package.json", json.dumps({"scripts": {script: "node server.js"}}))
            manifest = [ArtifactRef(path="package.json", kind="data")]
            entry = detect_entry_point(manifest, workspace=self.workspace())
            self.assertIsNotNone(entry, script)
            self.assertEqual(entry.kind, ENTRY_KIND_NODE)
            self.assertEqual(entry.run_command, command)
            self.assertEqual(entry.serve_kind, SERVE_KIND_PROCESS)

    def test_13_package_json_without_a_script_falls_through(self) -> None:
        self._write("package.json", json.dumps({"scripts": {"build": "tsc"}}))
        self._write("landing.html", "<!doctype html><html></html>")
        entry = detect_entry_point(
            [ArtifactRef(path="package.json", kind="data"), ArtifactRef(path="landing.html", kind="markup")],
            workspace=self.workspace(),
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(entry.path, "landing.html")

    def test_14_other_root_html_is_a_static_entry(self) -> None:
        entry = detect_entry_point([ArtifactRef(path="page.html", kind="markup")])
        self.assertIsNotNone(entry)
        self.assertEqual(entry.path, "page.html")
        self.assertEqual(entry.serve_kind, SERVE_KIND_STATIC)

    def test_15_none_when_the_manifest_has_no_entry_point(self) -> None:
        for manifest in (
            (),
            (ArtifactRef(path="notes.md", kind="text"),),
            (ArtifactRef(path="lib/tool.py", kind="python"),),
            (ArtifactRef(path="pages/about.html", kind="markup"),),
        ):
            self.assertIsNone(detect_entry_point(manifest, workspace=self.workspace()))

    def test_16_a_root_file_outside_the_manifest_is_still_a_candidate(self) -> None:
        # A version's manifest lists the files *that run* wrote. A run that writes only
        # ``index.html`` must not hide the server that is still on disk, so the root's
        # names are candidates too -- this is the "stale entry" half of the ranking.
        self._write("index.html", "<html></html>")
        entry = detect_entry_point(
            [ArtifactRef(path="notes.md", kind="text")], workspace=self.workspace()
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.path, "index.html")
        self.assertEqual(entry.serve_kind, SERVE_KIND_STATIC)

    def test_16b_detection_never_recurses_to_a_nested_page(self) -> None:
        # The root is the only place looked at: ``pages/about.html`` is not an entry,
        # and a manifest path is never invented into a root one.
        self._write("pages/about.html", "<html></html>")
        self.assertIsNone(
            detect_entry_point(
                [ArtifactRef(path="notes.md", kind="text")],
                workspace=self.workspace(),
            )
        )
        self.assertIsNone(
            detect_entry_point(
                [ArtifactRef(path="pages/about.html", kind="markup")],
                workspace=self.workspace(),
            )
        )


# --------------------------------------------------------------------------- #
# 3. the safe static preview
# --------------------------------------------------------------------------- #


class PreviewTests(_MaterializationCase):
    def setUp(self) -> None:
        super().setUp()
        self.registry = PreviewRegistry()
        self.addCleanup(self.registry.stop_all)

    def _start(self, manifest) -> dict:
        return self.registry.start(
            self.project.project_id, self.workspace(), detect_entry_point(manifest)
        )

    def _get(self, url: str):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except urllib.error.URLError:
            # A stopped preview refuses the connection; that is the assertion.
            return 0, b""

    def test_17_static_preview_serves_the_written_file(self) -> None:
        result = self.materialise(ANSWER_HTML_ONLY)
        started = self._start(result.files)

        self.assertTrue(started["running"], started)
        self.assertIn("127.0.0.1", started["url"])
        self.assertEqual(started["entry_path"], "index.html")
        base = started["url"].rsplit("/", 1)[0]
        status, body = self._get(base + "/index.html")
        self.assertEqual(status, 200)
        self.assertIn(BODY_MARKER.encode("utf-8"), body)
        # "/" serves the entry as well, so the owner can open the bare URL.
        status_root, body_root = self._get(base + "/")
        self.assertEqual(status_root, 200)
        self.assertEqual(body_root, body)
        # The absolute workspace path is diagnostics only.
        self.assertNotIn(str(self.workspace()), json.dumps(
            {key: value for key, value in started.items() if key != "diagnostics"}
        ))
        self.assertIn("workspace_root", started["diagnostics"])

    def test_18_preview_refuses_traversal_and_secrets(self) -> None:
        (self.base / "outside.txt").write_text("outside", encoding="utf-8")
        result = self.materialise(ANSWER_HTML_ONLY)
        started = self._start(result.files)
        base = started["url"].rsplit("/", 1)[0]

        for probe in (
            base + "/../outside.txt",
            base + "/%2e%2e/outside.txt",
            base + "/..%2foutside.txt",
            base + "/sub/../../outside.txt",
            base + "/.env",
            base + "/api_token.json",
            base + "/%2e%2e%2f%2e%2e%2fAGENTS.md",
        ):
            status, _body = self._get(probe)
            self.assertIn(status, (403, 404), probe)

        # A file that is really in the workspace is served normally.
        status, _body = self._get(base + "/index.html")
        self.assertEqual(status, 200)

    def test_19_preview_is_read_only(self) -> None:
        result = self.materialise(ANSWER_HTML_ONLY)
        started = self._start(result.files)
        request = urllib.request.Request(
            started["url"], data=b"{}", method="POST", headers={"Content-Type": "application/json"}
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 405)

    def test_20_preview_is_unavailable_for_python_and_node(self) -> None:
        for kind, path, extra in (
            (ENTRY_KIND_PYTHON, "app.py", 'print("hi")\n'),
            (ENTRY_KIND_NODE, "package.json", '{"scripts": {"start": "node index.js"}}'),
        ):
            target = self.workspace().joinpath(path)
            target.write_text(extra, encoding="utf-8")
            started = self.registry.start(
                self.project.project_id,
                self.workspace(),
                detect_entry_point([ArtifactRef(path=path)], workspace=self.workspace()),
            )
            self.assertFalse(started["running"], kind)
            self.assertFalse(started["available"], kind)
            self.assertEqual(started["reason"], LOCAL_RUN_NOT_WIRED, kind)
            self.assertEqual(started["url"], "", kind)
        self.assertFalse(self.registry.is_running(self.project.project_id))

    def test_21_no_entry_point_is_reported_as_such(self) -> None:
        started = self._start(())
        self.assertFalse(started["running"])
        self.assertEqual(started["reason"], NO_ENTRY_POINT)

    def test_22_stop_ends_the_server(self) -> None:
        result = self.materialise(ANSWER_HTML_ONLY)
        started = self._start(result.files)
        self.assertTrue(self.registry.is_running(self.project.project_id))
        stopped = self.registry.stop(self.project.project_id)
        self.assertFalse(stopped["running"])
        self.assertFalse(self.registry.is_running(self.project.project_id))
        status, _body = self._get(started["url"])
        self.assertNotEqual(status, 200)


# --------------------------------------------------------------------------- #
# 4. the stage words, and the gate
# --------------------------------------------------------------------------- #


class StageTests(unittest.TestCase):
    def _stage(self, **overrides) -> str:
        facts = {
            "files_count": 2,
            "verification": "passed",
            "acceptance": "passed",
            "lifecycle_status": "COMPLETED",
            "archived": False,
            "preview_available": False,
            "preview_not_required": False,
        }
        facts.update(overrides)
        return project_stage(**facts)

    def test_23_zero_files_never_completes(self) -> None:
        for status in ("COMPLETED", "NEEDS_CHANGES", "REVIEWING", "FAILED", "PLANNED"):
            self.assertNotEqual(
                self._stage(files_count=0, lifecycle_status=status), COMPLETED, status
            )
        self.assertEqual(
            self._stage(files_count=0, lifecycle_status="PLANNED"),
            STAGE_PROJECT_NOT_CREATED,
        )

    def test_24_completion_needs_a_preview_or_the_explicit_flag(self) -> None:
        self.assertEqual(self._stage(), STAGE_VERIFIED)
        self.assertEqual(self._stage(preview_available=True), COMPLETED)
        self.assertEqual(self._stage(preview_not_required=True), COMPLETED)
        # A not-applicable verification never completes, whatever the preview says.
        self.assertEqual(
            self._stage(verification="not_applicable", preview_available=True),
            PREVIEW_AVAILABLE,
        )
        self.assertEqual(
            self._stage(verification="failed", preview_available=True), "FAILED"
        )
        self.assertEqual(
            self._stage(lifecycle_status="NEEDS_CHANGES"), NEEDS_CHANGES
        )
        self.assertEqual(self._stage(lifecycle_status="FAILED"), "FAILED")
        self.assertEqual(self._stage(verification="pending"), "VERIFICATION_RUNNING")
        self.assertEqual(self._stage(verification="not_run"), FILES_CREATED)
        self.assertEqual(self._stage(archived=True), "")

    def test_25_every_stage_word_is_one_of_the_eight(self) -> None:
        produced = {
            self._stage(),
            self._stage(files_count=0),
            self._stage(verification="pending"),
            self._stage(verification="not_run"),
            self._stage(preview_available=True),
            self._stage(lifecycle_status="NEEDS_CHANGES"),
            self._stage(lifecycle_status="FAILED"),
        }
        self.assertTrue(produced.issubset(set(STAGE_WORDS)), produced)

    def test_26_the_lifecycle_gate_still_needs_a_passed_verification(self) -> None:
        # ``not_applicable`` is not a pass; the same rule the classifier applies.
        self.assertTrue(
            completion_blockers(
                run_state="COMPLETED",
                verification="not_applicable",
                acceptance="passed",
                artifact_count=1,
                wrote_files=True,
            )
        )


# --------------------------------------------------------------------------- #
# 5. the two write modes, through the real run path
# --------------------------------------------------------------------------- #


class WriteModeTests(_MaterializationCase):
    def test_27_mock_deterministic_writes_into_the_project_folder(self) -> None:
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description="перечислить содержимое корня проекта",
                category="code",
                provider_name="mock",
                write_mode=True,
            ),
            **self.project_workspace_kwargs(),
        )
        response = outcome.response
        self.assertEqual(response.write_mode, WRITE_MODE_MOCK_DETERMINISTIC)
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.files_created, 2)
        self.assertIn("MOCK_DETERMINISTIC", response.output)

        names = sorted(
            path.relative_to(self.workspace()).as_posix()
            for path in self.workspace_files()
        )
        self.assertEqual(names, ["app.py", "index.html"])
        # The run's own workspace was not used for the write.
        self.assertEqual(list(self.run_workspace.iterdir()), [])

        attached = self.attach(response)
        version = attached.current()
        self.assertIsNotNone(version)
        self.assertEqual(version.file_count, 2)
        self.assertEqual(version.verification, "passed")
        self.assertEqual(version.acceptance, "passed")
        self.assertIs(attached.status, ProjectStatus.COMPLETED)

    def test_28_no_provider_and_model_write_nothing(self) -> None:
        self.install_fake(ANSWER_HTML_ONLY)
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description="create a page",
                category="code",
                provider_name="fake-provider",  # the model is deliberately absent
                write_mode=True,
            ),
            **self.project_workspace_kwargs(),
        )
        response = outcome.response
        self.assertEqual(response.write_mode, "")
        self.assertEqual(response.files_created, 0)
        self.assertNotIn("CODE WRITER", response.output)
        self.assertEqual(self.workspace_files(), [])
        self.assertEqual(list(self.run_workspace.iterdir()), [])

    def test_29_real_provider_explicit_needs_the_flag_provider_and_model(self) -> None:
        self.install_fake(ANSWER_STATIC_AND_PYTHON)
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description="create the project files",
                category="code",
                provider_name="fake-provider",
                model_name="fake/model",
                write_mode=True,
            ),
            **self.project_workspace_kwargs(),
        )
        response = outcome.response
        self.assertEqual(response.write_mode, WRITE_MODE_REAL_PROVIDER_EXPLICIT)
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.files_created, 2)
        self.assertEqual(
            sorted(
                path.relative_to(self.workspace()).as_posix()
                for path in self.workspace_files()
            ),
            ["app.py", "index.html"],
        )

        # The same named pair without the flag writes nothing.
        outcome2 = self.service.run_task_detailed(
            TaskRunRequest(
                description="create the project files",
                category="code",
                provider_name="fake-provider",
                model_name="fake/model",
                write_mode=False,
            ),
            **self.project_workspace_kwargs(),
        )
        self.assertEqual(outcome2.response.write_mode, "")
        self.assertEqual(outcome2.response.files_created, 0)

    def test_30_a_non_code_category_never_writes(self) -> None:
        self.install_fake(ANSWER_HTML_ONLY)
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description="write a report",
                category="analysis",
                provider_name="fake-provider",
                model_name="fake/model",
                write_mode=True,
            ),
            **self.project_workspace_kwargs(),
        )
        self.assertEqual(outcome.response.write_mode, "")
        self.assertEqual(self.workspace_files(), [])

    def test_31_the_run_record_holds_the_manifest_without_contents(self) -> None:
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description="перечислить содержимое корня проекта",
                category="code",
                provider_name="mock",
                write_mode=True,
            ),
            **self.project_workspace_kwargs(),
        )
        record = self.service.get_run_record(outcome.response.run_id) or {}
        project_state = record.get("project_state") or {}
        materialization = project_state.get("project_materialization") or {}
        self.assertEqual(materialization.get("status"), "VERIFIED")
        paths = [item["path"] for item in materialization.get("files", [])]
        self.assertEqual(sorted(paths), ["app.py", "index.html"])
        serialized = json.dumps(materialization, ensure_ascii=False)
        self.assertNotIn("<!doctype html>", serialized)
        self.assertNotIn("def double", serialized)
        self.assertNotIn(str(self.workspace()), serialized)

        events = self.run_store.read_events(outcome.response.run_id)
        self.assertTrue(events)
        trail = json.dumps([dict(event.metadata) for event in events], ensure_ascii=False)
        self.assertNotIn("<!doctype html>", trail)
        self.assertNotIn("def double", trail)
        self.assertNotIn(SECRET_LOOKING, trail)
        materialization_events = [
            dict(event.metadata)
            for event in events
            if "materialization_status" in (event.metadata or {})
        ]
        self.assertTrue(materialization_events)
        for metadata in materialization_events:
            for row in metadata.get("materialization_files", []):
                self.assertEqual(set(row), {"path", "size", "sha256", "kind"})


# --------------------------------------------------------------------------- #
# 6. versions, change requests, archive and restore
# --------------------------------------------------------------------------- #


class VersionTests(_MaterializationCase):
    def _first_version(self) -> object:
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description="перечислить содержимое корня проекта",
                category="code",
                provider_name="mock",
                write_mode=True,
            ),
            **self.project_workspace_kwargs(),
        )
        attached = self.attach(outcome.response)
        self.assertIs(attached.status, ProjectStatus.COMPLETED)
        return attached

    def test_32_a_change_that_writes_nothing_does_not_advance_the_version(self) -> None:
        project = self._first_version()
        before = {
            path.relative_to(self.workspace()).as_posix(): path.read_bytes()
            for path in self.workspace_files()
        }
        staged = self.projects.new_version(project.project_id, "добавить экспорт")
        self.assertEqual(staged.version, 2)

        # A real-provider change whose answer carries no file: nothing is written.
        self.install_fake("I would change the report, but here is only prose.")
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description=staged.task_text,
                category="code",
                provider_name="fake-provider",
                model_name="fake/model",
                write_mode=True,
            ),
            **{
                "project_workspace": self.store.workspace_path(project.project_id, create=True),
                "lifecycle_project_id": project.project_id,
                "lifecycle_project_path": self.store.project_path_relative(project.project_id),
            },
        )
        self.assertEqual(outcome.response.write_mode, WRITE_MODE_REAL_PROVIDER_EXPLICIT)

        attached = self.projects.attach_run(
            project.project_id,
            self.run_source(outcome.response),
            source_root=self.store.workspace_path(project.project_id),
        )
        self.assertEqual(attached.current_version, 1, "a no-file change advanced the version")
        recorded = attached.version(2)
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.file_count, 0)
        self.assertIsNot(recorded, attached.current())
        # The previous version's files are untouched, byte for byte.
        after = {
            path.relative_to(self.workspace()).as_posix(): path.read_bytes()
            for path in self.workspace_files()
        }
        self.assertEqual(after, before)

    def test_33_a_new_version_preserves_the_old_one(self) -> None:
        project = self._first_version()
        first = project.version(1)
        self.assertIsNotNone(first)
        first_manifest = [
            (item.path, item.size, item.sha256) for item in first.files
        ]
        first_bytes = {
            item.path: self.workspace().joinpath(*item.path.split("/")).read_bytes()
            for item in first.files
        }

        staged = self.projects.new_version(project.project_id, "добавить страницу отчёта")
        self.install_fake(
            _fenced("report.html", "<!doctype html><html>report</html>", "html")
            + _fenced("report.py", "def render():\n    return 'report'", "python")
        )
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description=staged.task_text,
                category="code",
                provider_name="fake-provider",
                model_name="fake/model",
                write_mode=True,
            ),
            **{
                "project_workspace": self.store.workspace_path(project.project_id, create=True),
                "lifecycle_project_id": project.project_id,
                "lifecycle_project_path": self.store.project_path_relative(project.project_id),
            },
        )
        attached = self.projects.attach_run(
            project.project_id,
            self.run_source(outcome.response),
            source_root=self.store.workspace_path(project.project_id),
        )
        self.assertEqual(attached.current_version, 2)
        self.assertIs(attached.status, ProjectStatus.COMPLETED)
        preserved = attached.version(1)
        self.assertIsNotNone(preserved)
        self.assertEqual(
            [(item.path, item.size, item.sha256) for item in preserved.files],
            first_manifest,
        )
        for path, data in first_bytes.items():
            self.assertEqual(
                self.workspace().joinpath(*path.split("/")).read_bytes(), data
            )
        self.assertIn("report.html", [item.path for item in attached.current().files])

    def test_34_archive_and_restore_delete_nothing(self) -> None:
        project = self._first_version()
        files_before = {
            path.relative_to(self.workspace()).as_posix(): path.read_bytes()
            for path in self.workspace_files()
        }
        archived = self.projects.archive(project.project_id)
        self.assertIs(archived.status, ProjectStatus.ARCHIVED)
        self.assertEqual(project_stage(
            files_count=1, verification="passed", acceptance="passed",
            lifecycle_status=archived.status.value, archived=True,
        ), "")
        restored = self.projects.restore(project.project_id)
        self.assertIs(restored.status, ProjectStatus.COMPLETED)
        self.assertEqual(restored.current_version, project.current_version)
        files_after = {
            path.relative_to(self.workspace()).as_posix(): path.read_bytes()
            for path in self.workspace_files()
        }
        self.assertEqual(files_after, files_before)


# --------------------------------------------------------------------------- #
# 7. no secret and no body reaches an event or a project payload
# --------------------------------------------------------------------------- #


class NoSecretsInPayloadTests(_MaterializationCase):
    def test_35_a_secret_looking_value_never_reaches_the_record_or_the_payload(self) -> None:
        result = self.materialise(ANSWER_WITH_SECRET, run_id="run-secret-1")
        self.assertEqual(result.status, FILES_CREATED, result.reason)
        # It is on disk (the writer applies the model's text) but not in the record.
        self.assertIn(SECRET_LOOKING, (self.workspace() / "config.txt").read_text("utf-8"))
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn(SECRET_LOOKING, serialized)
        self.assertNotIn(BODY_MARKER, serialized)

        # Record that file as a version so the payload really *reports* it: the path,
        # the size and the digest are published, the bytes never are.
        recorded = ProjectVersion(
            version=1,
            created_at=utc_now(),
            run_id="run-secret-1",
            files=result.files,
            verification="not_applicable",
            acceptance="passed",
            provider="mock",
            model="deterministic-offline",
        )
        self.project = replace(
            self.project,
            versions=[recorded],
            current_version=1,
            status=ProjectStatus.NEEDS_CHANGES,
            updated_at=utc_now(),
        )
        self.projects.store.write(self.project)

        from app.discovery.service import DiscoveryService
        from app.discovery.store import DiscoveryStore

        app = create_api_app(
            self.service,
            project_service=self.projects,
            discovery_service=DiscoveryService(DiscoveryStore(root=self.base / "discovery")),
        )
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get(f"/api/projects/{self.project.project_id}")
        self.assertEqual(response.status_code, 200)
        payload = json.dumps(response.json(), ensure_ascii=False)
        for forbidden in (SECRET_LOOKING, BODY_MARKER, "def double", "<!doctype html>"):
            self.assertNotIn(forbidden, payload)
        self.assertNotIn(str(self.workspace()), payload)
        # The manifest is still reported, path and digest only.
        detail = response.json()
        self.assertEqual([item["path"] for item in detail["files"]], ["config.txt"])
        self.assertEqual(detail["files"][0]["sha256"], result.files[0].sha256)
        self.assertNotIn("content", detail["files"][0])

        # A write run's events and payloads are clean as well.
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description="перечислить содержимое корня проекта",
                category="code",
                provider_name="mock",
                write_mode=True,
            ),
            **self.project_workspace_kwargs(),
        )
        attached = self.attach(outcome.response)
        events = self.run_store.read_events(outcome.response.run_id)
        trail = json.dumps([dict(event.metadata) for event in events], ensure_ascii=False)
        for forbidden in (SECRET_LOOKING, BODY_MARKER, "def double", "<!doctype html>"):
            self.assertNotIn(forbidden, trail)

        entry = client.get(f"/api/projects/{self.project.project_id}/entrypoint")
        self.assertEqual(entry.status_code, 200)
        body = entry.json()
        self.assertEqual(body["entrypoint"]["path"], "index.html")
        self.assertEqual(body["entrypoint"]["kind"], ENTRY_KIND_STATIC_HTML)
        self.assertNotIn(str(self.workspace()), json.dumps(body))

    def test_36_the_api_reports_the_preview_and_the_stage(self) -> None:
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description="перечислить содержимое корня проекта",
                category="code",
                provider_name="mock",
                write_mode=True,
            ),
            **self.project_workspace_kwargs(),
        )
        attached = self.attach(outcome.response)

        from app.discovery.service import DiscoveryService
        from app.discovery.store import DiscoveryStore

        app = create_api_app(
            self.service,
            project_service=self.projects,
            discovery_service=DiscoveryService(DiscoveryStore(root=self.base / "discovery")),
        )
        from fastapi.testclient import TestClient

        client = TestClient(app)
        project_id = attached.project_id

        detail = client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(detail["stage"], STAGE_VERIFIED)
        self.assertEqual(detail["files_created"], 2)
        self.assertEqual(detail["entrypoint"]["path"], "index.html")
        self.assertTrue(detail["preview"]["available"])
        self.assertFalse(detail["preview"]["running"])
        # A static page is RUNNABLE: its launch is the existing preview route, the
        # command the card prints is the fixed sentence, and the card offers both
        # launch buttons. It used to say `runnable: false` with no command while the
        # same route answered 200 -- the measured "unrunnable static site" defect.
        self.assertIs(detail["entrypoint"]["runnable"], True, detail["entrypoint"])
        self.assertEqual(detail["entrypoint"]["run_command"], STATIC_RUN_COMMAND)
        self.assertIs(detail["preview"]["runnable"], True, detail["preview"])
        self.assertEqual(detail["preview"]["run_command"], STATIC_RUN_COMMAND)
        actions = {
            str(item["id"]): item
            for item in detail["presentation"]["actions"]
        }
        for action_id in ("launch_local", "open_app"):
            self.assertIs(actions[action_id]["visible"], True, action_id)
            self.assertIs(actions[action_id]["enabled"], True, action_id)
        self.assertIn("compileall", detail["verification_command"])
        self.assertNotIn(str(self.workspace()), json.dumps(detail))

        started = client.post(f"/api/projects/{project_id}/preview", json={"action": "start"})
        self.assertEqual(started.status_code, 200)
        body = started.json()
        self.assertTrue(body["running"], body)
        self.assertEqual(body["stage"], COMPLETED)
        url = body["url"]
        self.assertIn("127.0.0.1", url)
        with urllib.request.urlopen(url, timeout=5) as served:
            self.assertEqual(served.status, 200)
            self.assertIn(b"<!doctype html>", served.read())

        status = client.get(f"/api/projects/{project_id}/preview").json()
        self.assertTrue(status["running"])
        self.assertEqual(status["url"], url)

        stopped = client.post(f"/api/projects/{project_id}/preview", json={"action": "stop"})
        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(stopped.json()["running"])
        self.assertEqual(stopped.json()["stage"], STAGE_VERIFIED)

        # The explicit "no preview required" is recorded on the version, and only then
        # does a stopped-preview project read COMPLETED. Recording it is a read by
        # default: it must not start a server as a side effect.
        flagged = client.post(
            f"/api/projects/{project_id}/preview", json={"preview_not_required": True}
        )
        self.assertEqual(flagged.status_code, 200)
        self.assertEqual(flagged.json()["action"], "status")
        self.assertFalse(flagged.json()["running"])
        self.assertEqual(flagged.json()["stage"], COMPLETED)
        refreshed = client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(refreshed["stage"], COMPLETED)
        self.assertTrue(
            refreshed["project"]["versions"][0]["preview_not_required"],
            refreshed["project"]["versions"],
        )

    def test_37_a_python_entry_point_reports_local_run_is_not_wired(self) -> None:
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description="перечислить содержимое корня проекта",
                category="code",
                provider_name="mock",
                write_mode=True,
            ),
            **self.project_workspace_kwargs(),
        )
        attached = self.attach(outcome.response)
        # The deterministic scaffold's own module is a page scaffold, not a server (the
        # detector recognises its banner), so put a real Python program at the root and
        # re-point the version's manifest at the module only (a hand-made record, no
        # writer involved).
        (self.workspace() / "app.py").write_text('print("real program")\n', encoding="utf-8")
        current = attached.current()
        python_only = tuple(item for item in current.files if item.path.endswith(".py"))
        versions = [
            replace(item, files=python_only) if item.version == current.version else item
            for item in attached.versions
        ]
        attached = replace(attached, versions=versions)
        self.projects.store.write(attached)

        from app.discovery.service import DiscoveryService
        from app.discovery.store import DiscoveryStore

        app = create_api_app(
            self.service,
            project_service=self.projects,
            discovery_service=DiscoveryService(DiscoveryStore(root=self.base / "discovery")),
        )
        from fastapi.testclient import TestClient

        client = TestClient(app)
        detail = client.get(f"/api/projects/{attached.project_id}").json()
        self.assertEqual(detail["entrypoint"]["kind"], ENTRY_KIND_PYTHON)
        self.assertFalse(detail["preview"]["available"])
        self.assertEqual(detail["preview"]["reason"], LOCAL_RUN_NOT_WIRED)

        started = client.post(
            f"/api/projects/{attached.project_id}/preview", json={"action": "start"}
        )
        self.assertEqual(started.status_code, 200)
        body = started.json()
        self.assertFalse(body["available"])
        self.assertFalse(body["running"])
        self.assertEqual(body["reason"], LOCAL_RUN_NOT_WIRED)
        self.assertEqual(body["url"], "")


if __name__ == "__main__":  # pragma: no cover - convenience only
    unittest.main()
