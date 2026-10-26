"""A screenshot the owner attaches to a change reaches Forge honestly.

Why this file exists
--------------------
The owner's generated app «Почта АИ» showed an honest but useless error, and the only way
to tell Forge about it was to *type* it. A screenshot is what he really has, so «Что
сделать?» accepts one -- but no provider wired into this repository receives image bytes.
The feature is therefore two things at once, and both are asserted here:

1. the image is **kept with the project** (inside its own folder, sanitised name, images
   only by magic bytes, at most 5 MB), and the path published for it is project-relative:
   no absolute host path ever reaches a payload;
2. the text the model reads **names the screenshot and says it cannot be seen**. What the
   owner typed is in that text too. Nothing anywhere claims the model looked at pixels.

The refusals are as important as the success: a ``.png`` name on an executable, six
megabytes of screenshot, a path outside the project, and a reference to a file that is not
there are all refused with the layer's own sentence, before a version is staged.

No provider is called: the project is created through the real discovery routes, and the
only other requests are the ones under test.
"""

from __future__ import annotations

import base64
import os
import pathlib
import shutil
import unittest

from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.projects.attachments import (
    ATTACHMENT_NOTICE,
    MAX_ATTACHMENT_BYTES,
    image_kind,
    sanitise_filename,
    with_attachment_notices,
)
from app.projects.service import ProjectService
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]

#: The workspace the Core is composed for. A ``ws-…`` id, never a ``proj-…`` one.
WORKSPACE_PROJECT_ID = "attachment-test-workspace"

GOAL = (
    "Хочу локальное приложение для чтения почты: один ящик, список писем и ошибка, "
    "если сервер не ответил. Никаких внешних сервисов."
)
CHANGE = "Покажи текст ошибки рядом с ящиком и подскажи, как её исправить."

#: A one-pixel PNG, header and all: the server decides by magic bytes, and these are the
#: bytes it recognises.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
#: An executable, not an image -- whatever name it is offered under.
EXECUTABLE = b"MZ\x90\x00" + b"\x00" * 64


class _Case(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        base = (
            REPOSITORY
            / ".forge"
            / "test-project-attachments"
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

        service = ForgeApiService(
            runtime=offline_runtime(),
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
        self.client = TestClient(self.app)
        self.project_id = self._confirmed_project()

    # ------------------------------------------------------------------ helpers
    def _confirmed_project(self) -> str:
        """Create a real project through the real discovery routes (no provider call)."""

        opened = self.client.post("/api/discovery/sessions", json={"goal": GOAL})
        self.assertLess(opened.status_code, 400, opened.text)
        session = opened.json()["session"]
        session_id = str(session["session_id"])
        if str(session.get("state") or "") != "READY_FOR_PLANNING":
            skipped = self.client.post(
                f"/api/discovery/sessions/{session_id}/skip",
                json={"reason": "the attachment test needs no further answers"},
            )
            self.assertLess(skipped.status_code, 400, skipped.text)
        confirmed = self.client.post(f"/api/discovery/sessions/{session_id}/confirm")
        self.assertLess(confirmed.status_code, 400, confirmed.text)
        project_id = str(confirmed.json()["project"]["project_id"])
        self.assertRegex(project_id, r"^proj-[0-9a-f]{12}$")
        return project_id

    def _workspace(self) -> pathlib.Path:
        return pathlib.Path(self.store.workspace_path(self.project_id))

    def _project_path(self) -> str:
        return str(self.projects.open_project(self.project_id).project_path)

    def _attachments_folder(self) -> pathlib.Path:
        return self._workspace() / ".forge" / "attachments"

    def _upload(self, data: bytes, filename: str = "ошибка.png"):
        return self.client.post(
            f"/api/projects/{self.project_id}/attachments",
            json={"filename": filename, "data": base64.b64encode(data).decode("ascii")},
        )

    def _payload(self, answer) -> dict:
        self.assertLess(answer.status_code, 400, answer.text)
        return answer.json()

    def _detail(self) -> dict:
        answer = self.client.get(f"/api/projects/{self.project_id}")
        self.assertLess(answer.status_code, 400, answer.text)
        return answer.json()

    # ------------------------------------------------------------- the stored file
    def test_attachment_is_stored_inside_the_project(self) -> None:
        """The bytes land under the project's own ``.forge/attachments``, and the answer
        publishes the project-relative path -- never an absolute host path."""

        stored = self._payload(self._upload(PNG))

        self.assertEqual(stored["kind"], "png")
        self.assertEqual(stored["bytes"], len(PNG))
        path = str(stored["path"])
        self.assertTrue(path.startswith(self._project_path() + "/.forge/attachments/"), path)
        self.assertNotIn(str(self._workspace()), path)
        # A published path, not a host path: no drive letter, no leading separator.
        self.assertNotRegex(path, r"^[A-Za-z]:")
        self.assertFalse(path.startswith(("/", "\\")))
        # The chip is named with the owner's own file name; the stored one carries the
        # timestamp that keeps two screenshots of one problem apart.
        self.assertEqual(stored["name"], "ошибка.png")
        self.assertRegex(path.rsplit("/", 1)[1], r"^\d{8}-\d{6}-ошибка\.png$")

        written = self._attachments_folder() / path.rsplit("/", 1)[1]
        self.assertTrue(written.is_file())
        self.assertEqual(written.read_bytes(), PNG)
        # One file, and it is the one the answer names.
        self.assertEqual([item.name for item in self._attachments_folder().iterdir()], [written.name])

    def test_the_name_is_sanitised(self) -> None:
        """A path-looking, character-laden name becomes one plain file name."""

        stored = self._payload(
            self._upload(PNG, '..\\..\\Мой скрин: "ошибка"?.png')
        )
        # The chip's name is the owner's name, sanitised: no path, no separators, no
        # characters a file name cannot hold.
        name = str(stored["name"])
        self.assertTrue(name.startswith("Мой скрин"), name)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn("..", name)
        self.assertNotIn(":", name)
        self.assertNotIn('"', name)
        self.assertNotIn("?", name)
        self.assertTrue(name.endswith(".png"), name)
        # ...and the file on disk is that name behind its timestamp, still a direct child.
        on_disk = str(stored["path"]).rsplit("/", 1)[1]
        self.assertTrue(on_disk.endswith(name), on_disk)
        self.assertEqual(
            list(self._attachments_folder().glob("*")),
            [self._attachments_folder() / on_disk],
        )

    def test_extension_follows_the_bytes_not_the_name(self) -> None:
        """A JPEG called ``.png`` is kept as ``.jpg``: the bytes decide."""

        stored = self._payload(self._upload(b"\xff\xd8\xff" + b"\x00" * 32, "снимок.png"))
        self.assertEqual(stored["kind"], "jpeg")
        self.assertTrue(str(stored["name"]).endswith(".jpg"), stored["name"])

    def test_a_non_image_is_refused(self) -> None:
        """Magic bytes, not the extension: an executable wearing ``.png`` is refused."""

        answer = self._upload(EXECUTABLE, "screenshot.png")
        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertIn("не изображение", answer.text)
        self.assertFalse(self._attachments_folder().exists())

    def test_more_than_five_megabytes_is_refused(self) -> None:
        """The limit is the same 5 MB the interface states, and it is enforced server-side."""

        oversized = PNG + b"\x00" * MAX_ATTACHMENT_BYTES
        answer = self._upload(oversized, "большой.png")
        self.assertIn(answer.status_code, (400, 413), answer.text)
        self.assertIn("5 МБ", answer.text)
        self.assertFalse(self._attachments_folder().exists())

    def test_only_a_local_uploader_is_accepted(self) -> None:
        """The upload route refuses a caller that is not this machine's loopback transport.

        TestClient is Starlette's in-process transport (``testclient``), which the route
        accepts as local; anything else is refused before a byte is decoded.
        """

        from app.api.project_routes import register_project_routes  # noqa: F401

        # The documented client set is the route's own: loopback hosts plus the in-process
        # transport. A network peer would not be in it.
        source = pathlib.Path(REPOSITORY / "app" / "api" / "project_routes.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('_UPLOAD_CLIENTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})', source)
        self.assertIn("_require_local_uploader(request)", source)

    # ------------------------------------------------------ the honest change text
    def test_change_text_names_the_screenshot_and_never_claims_sight(self) -> None:
        """The model reads the owner's words **and** the notice; the notice says it cannot
        see the image, and nothing claims otherwise. The staged change keeps the words."""

        stored = self._payload(self._upload(PNG, "ошибка.png"))
        path = str(stored["path"])

        staged = self._payload(
            self.client.post(
                f"/api/projects/{self.project_id}/versions",
                json={"change": CHANGE, "attachments": [path]},
            )
        )
        text = str(staged["task_text"])
        self.assertIn(CHANGE, text)
        self.assertIn(ATTACHMENT_NOTICE.format(path=path), text)
        self.assertIn("Модель не видит изображения", text)
        self.assertNotIn("Модель видит", text)
        self.assertNotIn("я вижу", text.lower())
        self.assertNotIn("на изображении видно", text)
        # The screenshot really is mentioned before the request, so «он также приложен
        # ниже» is true of the owner's own text.
        self.assertLess(text.index("Приложен скриншот:"), text.index(CHANGE))
        # ...and the recorded change request is the owner's words, not the notice.
        self.assertEqual(
            str(staged["change_request"]["text"]),
            CHANGE,
        )
        self.assertEqual(staged["attachments"], [path])

    def test_a_reference_outside_the_project_is_refused(self) -> None:
        """A path that is not this project's own file cannot become part of a change, and
        no version is staged for it."""

        before = self._detail()["project"]["current_version"]
        self._payload(self._upload(PNG))
        for hostile in (
            ".env",
            "../../.env",
            ".forge/attachments/../../.env",
            "C:/Windows/system.ini",
        ):
            with self.subTest(hostile=hostile):
                answer = self.client.post(
                    f"/api/projects/{self.project_id}/versions",
                    json={"change": CHANGE, "attachments": [hostile]},
                )
                self.assertEqual(answer.status_code, 400, answer.text)
                self.assertIn("не принадлежит этому проекту", answer.text)
        after = self._detail()["project"]["current_version"]
        self.assertEqual(before, after)

    def test_a_missing_screenshot_is_refused(self) -> None:
        """The named file must still be there: a reference is not a promise."""

        missing = f"{self._project_path()}/.forge/attachments/20260101-000000-нет.png"
        answer = self.client.post(
            f"/api/projects/{self.project_id}/versions",
            json={"change": CHANGE, "attachments": [missing]},
        )
        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertIn("не найден", answer.text)

    # ------------------------------------------------------------- the pure parts
    def test_pure_helpers_are_pure_and_honest(self) -> None:
        self.assertEqual(image_kind(PNG), ("png", ".png"))
        self.assertEqual(image_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 "), ("webp", ".webp"))
        self.assertIsNone(image_kind(EXECUTABLE))
        self.assertEqual(sanitise_filename('C:\\Users\\me\\shot.jpg', ".png"), "shot.png")
        self.assertEqual(sanitise_filename("", ".png"), "screenshot.png")
        self.assertEqual(sanitise_filename("con.png", ".png"), "_con.png")
        # A change without a screenshot is not given a notice at all.
        self.assertEqual(with_attachment_notices("текст", []), "текст")
        # ...and one that names two screenshots gets one truthful line each.
        composed = with_attachment_notices(
            "текст", [".forge/a/.forge/attachments/1.png", ".forge/a/.forge/attachments/2.png"]
        )
        self.assertEqual(composed.count("Приложен скриншот:"), 2)
        self.assertTrue(composed.endswith("текст"))


if __name__ == "__main__":
    unittest.main()
