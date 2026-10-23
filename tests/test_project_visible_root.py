"""The project root: where a delivered project really is, and what may not escape it.

The owner's first complaint was concrete: *«проект лежит в скрытой папке»*. The files
of a lifecycle project were a hidden subdirectory of ``.forge/projects``, so there was
no folder he could open in Explorer or in VS Code. This file proves the fix as a
property, offline and with throwaway stores:

1. the setting exists, defaults to ``<USERPROFILE>\\Desktop\\Forge Projects``, is stored
   in the application's own JSON file (never ``.env``), and refuses values that cannot
   name one folder;
2. a **new** project's files land in ``<root>/<name> (proj-<id>)``, a normal folder;
3. the **record** stays in ``.forge/projects/<id>/`` -- versions, manifest and run links
   are not moved -- and the published path is root-relative while the absolute one is
   confined to the card's diagnostics;
4. a project created before this existed keeps working, and «Переместить в видимую
   папку» moves it with **byte identity** proved by SHA-256;
5. the boundary is re-proved for the new root: ``..``, an absolute path outside it, a
   junction that leaves it, ``.git`` and ``.env`` are refused;
6. deletion still moves the folder into the store's trash and restore brings it back to
   the visible root, unchanged.

No model, no network, no browser: every object here is the production one, and the
only thing a test replaces is a path.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import unittest
import unittest.mock

from app.config.app_settings import (
    DEFAULT_PROJECT_ROOT_NAME,
    AppSettingsError,
    AppSettingsStore,
    default_project_root,
    default_settings_path,
)
from app.projects.folders import (
    UnsafeProjectFolder,
    prove_folder,
    published_folder_path,
    safe_project_folder_name,
    visible_folder,
)
from app.projects.models import is_safe_relative_path
from app.projects.service import ProjectService
from app.projects.store import (
    DELETED_DIRECTORY_NAME,
    ProjectStore,
    PROJECTS_ROOT_ENV_VAR,
)

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
GOAL = "Небольшой справочник сотрудников с поиском по имени."


class _Session:
    """The minimum a discovery session must expose for ``create_from_discovery``."""

    def __init__(self, goal: str = GOAL) -> None:
        self.session_id = f"disc-visible-{os.getpid()}-{id(self)}"
        self.initial_goal = goal
        self.specification = {"goal": goal, "platform": "веб-приложение"}


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Base(unittest.TestCase):
    """One throwaway tree: a record store, a visible root, and a run workspace."""

    def setUp(self) -> None:
        base = REPOSITORY / ".forge" / "test-visible-root" / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        self.base = base.resolve()
        self.root = self.base / "records"
        self.visible = self.base / "Desktop" / DEFAULT_PROJECT_ROOT_NAME
        self.runs = self.base / "runs"
        for directory in (self.root, self.visible, self.runs):
            directory.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

    def _service(self, *, visible: bool = True) -> ProjectService:
        store = ProjectStore(
            root=self.root, visible_root=self.visible if visible else None
        )
        return ProjectService(store)

    def _write_run_files(self, service: ProjectService, project_id: str, files: dict) -> None:
        """Put the run's written files into the project workspace, as a run would."""

        for relative, data in files.items():
            service.store.write_workspace_file(project_id, relative, data)


# --------------------------------------------------------------------------- #
# 1. the setting
# --------------------------------------------------------------------------- #


class SettingsStoreTests(_Base):
    """The project root is the application's own setting, in its own file."""

    def test_01_the_default_is_the_owner_desktop(self) -> None:
        settings = AppSettingsStore(path=self.base / "settings.json")
        self.assertEqual(
            settings.project_root(),
            default_project_root(),
            "an unset root must be the documented default",
        )
        self.assertEqual(default_project_root().name, DEFAULT_PROJECT_ROOT_NAME)
        self.assertEqual(default_project_root().parent.name, "Desktop")
        self.assertTrue(default_project_root().is_absolute())
        self.assertFalse(
            (self.base / "settings.json").exists(),
            "reading a setting must not create the file",
        )

    def test_02_the_default_file_is_not_dot_env(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_SETTINGS_FILE", None)
            path = default_settings_path()
        self.assertEqual(path.name, "settings.json")
        self.assertNotEqual(path.name, ".env")
        self.assertIn(".forge", path.parts)
        self.assertFalse(path.exists(), "the repository must not carry a settings file")

    def test_03_a_value_survives_a_restart_and_the_previous_root_is_remembered(self) -> None:
        path = self.base / "settings.json"
        first = AppSettingsStore(path=path).set_project_root(str(self.visible))
        self.assertEqual(first, self.visible.resolve(strict=False))
        # A second store is a restart: the value is read from the file, not memory.
        again = AppSettingsStore(path=path)
        self.assertEqual(
            again.project_root().resolve(strict=False), self.visible.resolve(strict=False)
        )
        other = self.base / "Elsewhere"
        again.set_project_root(str(other))
        self.assertIn(
            self.visible.resolve(strict=False),
            [item.resolve(strict=False) for item in again.known_project_roots()],
            "the previous root must stay known, or a project already delivered to it "
            "would stop resolving",
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "forge.settings.v1")
        self.assertNotIn("key", document)
        self.assertNotIn("token", document)

    def test_04_use_desktop_restores_the_default(self) -> None:
        settings = AppSettingsStore(path=self.base / "settings.json")
        settings.set_project_root(str(self.base / "Somewhere"))
        restored = settings.use_desktop()
        self.assertEqual(restored, default_project_root())
        self.assertEqual(settings.project_root(), default_project_root())

    def test_05_a_value_that_cannot_name_one_folder_is_refused(self) -> None:
        settings = AppSettingsStore(path=self.base / "settings.json")
        settings.set_project_root(str(self.visible))
        before = settings.project_root()
        file_path = self.base / "notes.txt"
        file_path.write_text("not a folder", encoding="utf-8")
        for bad in ("", "   ", "Projects", "..\\escape", ".\\relative", "C:\\", str(file_path)):
            with self.assertRaises(AppSettingsError, msg=bad):
                settings.set_project_root(bad)
            self.assertEqual(
                settings.project_root(),
                before,
                f"a refused value must not replace the stored one ({bad!r})",
            )

    def test_06_a_corrupt_file_falls_back_without_being_rewritten(self) -> None:
        path = self.base / "settings.json"
        path.write_text("{not json", encoding="utf-8")
        settings = AppSettingsStore(path=path)
        self.assertEqual(settings.project_root(), default_project_root())
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_07_ensure_root_creates_the_folder(self) -> None:
        target = self.base / "Created" / "Projects"
        settings = AppSettingsStore(path=self.base / "settings.json")
        settings.set_project_root(str(target))
        self.assertFalse(target.is_dir())
        self.assertEqual(settings.ensure_root(), target)
        self.assertTrue(target.is_dir())


# --------------------------------------------------------------------------- #
# 2. where a new project lands
# --------------------------------------------------------------------------- #


class VisibleProjectTests(_Base):
    """A new project is a normal folder under the configured root."""

    def test_01_a_new_project_lands_under_the_configured_root(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        workspace = service.store.workspace_path(project.project_id)
        self.assertTrue(workspace.is_dir(), "the project folder must exist")
        self.assertEqual(workspace.parent, self.visible.resolve(strict=False))
        self.assertIn(
            project.project_id, workspace.name, "the folder name must carry the project id"
        )
        self.assertTrue(project.project_id in workspace.name)

    def test_02_the_folder_name_is_readable_and_carries_the_id(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        folder = service.store.workspace_path(project.project_id)
        self.assertTrue(
            folder.name.startswith(safe_project_folder_name(project.name)),
            folder.name,
        )
        self.assertTrue(folder.name.endswith(f"({project.project_id})"), folder.name)
        self.assertNotIn("..", folder.name)
        for forbidden in '<>:"/\\|?*':
            self.assertNotIn(forbidden, folder.name)

    def test_03_the_record_stays_in_the_store_and_publishes_a_relative_path(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        record = self.root / f"{project.project_id}.json"
        self.assertTrue(record.is_file(), "the record must stay in .forge/projects")
        self.assertFalse(pathlib.Path(project.project_path).is_absolute())
        self.assertEqual(
            project.project_path,
            published_folder_path(self.visible, service.store.workspace_path(project.project_id)),
        )
        # The absolute location is in the record file, and NOT in the published shape.
        stored = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(stored["workspace_dir"], str(service.store.workspace_path(project.project_id)))
        self.assertNotIn("workspace_dir", project.to_dict())
        self.assertNotIn(str(self.visible), json.dumps(project.to_dict(), ensure_ascii=False))

    def test_04_a_second_project_never_reuses_the_first_folder(self) -> None:
        service = self._service()
        one = service.create_from_discovery(_Session(GOAL))
        two = service.create_from_discovery(_Session(GOAL))
        self.assertNotEqual(
            service.store.workspace_path(one.project_id),
            service.store.workspace_path(two.project_id),
        )

    def test_05_a_store_without_a_configured_root_keeps_the_hidden_workspace(self) -> None:
        service = self._service(visible=False)
        project = service.create_from_discovery(_Session())
        self.assertEqual(project.workspace_dir, "")
        self.assertTrue(project.project_path.startswith(".forge/"))
        self.assertIn(self.root.resolve(), service.store.workspace_path(project.project_id).parents)

    def test_06_a_legacy_project_still_resolves_after_a_root_is_configured(self) -> None:
        legacy = self._service(visible=False)
        project = legacy.create_from_discovery(_Session())
        legacy._store.write_workspace_file(project.project_id, "app.py", b"print('hi')\n")
        # Same record directory, now with a root configured: the old project must keep
        # resolving to the workspace it was created in.
        configured = self._service(visible=True)
        resolved = configured.store.workspace_path(project.project_id)
        self.assertTrue(resolved.is_dir())
        self.assertTrue((resolved / "app.py").is_file())
        self.assertNotEqual(resolved.parent, self.visible.resolve(strict=False))


# --------------------------------------------------------------------------- #
# 3. the boundary, re-proved for the new root
# --------------------------------------------------------------------------- #


class BoundaryTests(_Base):
    """The allowed root is the configured project root, and a store root."""

    def test_01_a_relative_recorded_path_is_refused(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        stored = json.loads((self.root / f"{project.project_id}.json").read_text(encoding="utf-8"))
        stored["workspace_dir"] = "relative/folder"
        (self.root / f"{project.project_id}.json").write_text(
            json.dumps(stored), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            service.store.workspace_path(project.project_id)

    def test_02_a_traversal_path_is_refused(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        with self.assertRaises(UnsafeProjectFolder):
            prove_folder(
                str(self.visible / ".." / ".." / "escape"),
                allowed_roots=(self.visible,),
            )

    def test_03_a_path_outside_the_root_is_refused(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        outside = self.base / "Outside"
        outside.mkdir(parents=True, exist_ok=True)
        stored = json.loads((self.root / f"{project.project_id}.json").read_text(encoding="utf-8"))
        stored["workspace_dir"] = str(outside)
        (self.root / f"{project.project_id}.json").write_text(
            json.dumps(stored), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            service.store.workspace_path(project.project_id)
        # And the record itself cannot be used to escape: a file write for it is refused
        # as well, because the workspace cannot be resolved any more.
        with self.assertRaises(ValueError):
            service.store.write_workspace_file(project.project_id, "escape.py", b"x")

    def test_04_a_junction_that_leaves_the_root_is_refused(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        outside = self.base / "OutsideLinkTarget"
        outside.mkdir(parents=True, exist_ok=True)
        link = self.visible / "junction-to-outside"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:  # pragma: no cover - junctions are available on NTFS
            self.skipTest(f"a junction could not be created: {created.stderr.strip()}")
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        with self.assertRaises(UnsafeProjectFolder):
            prove_folder(str(link / "inner"), allowed_roots=(self.visible,), must_exist=False)
        with self.assertRaises(UnsafeProjectFolder):
            prove_folder(str(link), allowed_roots=(self.visible,))

    def test_05_a_git_folder_is_refused(self) -> None:
        with self.assertRaises(UnsafeProjectFolder):
            prove_folder(str(self.visible / ".git" / "hooks"), allowed_roots=(self.visible,))

    def test_06_the_project_id_still_bounds_the_workspace(self) -> None:
        service = self._service()
        for bad in ("../../etc", "proj-0123", "", "index"):
            with self.assertRaises(ValueError):
                service.store.workspace_path(bad)

    def test_07_a_workspace_file_cannot_escape_the_project_folder(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        for bad in ("../escape.py", "/abs.py", "a/../../b.py", "a\\b.py", ".env", ".git/config"):
            with self.assertRaises(ValueError, msg=bad):
                service.store.workspace_file_path(project.project_id, bad)
        # The refusals above are the repository's own recognizers, not a second list.
        from app.execution.secret_files import is_secret_file

        self.assertTrue(is_secret_file(".env"))


# --------------------------------------------------------------------------- #
# 4. migration of an existing project
# --------------------------------------------------------------------------- #


class MoveToVisibleFolderTests(_Base):
    """«Переместить в видимую папку» moves files, not history."""

    def _legacy_project_with_files(self):
        legacy = self._service(visible=False)
        project = legacy.create_from_discovery(_Session())
        self._write_run_files(
            legacy,
            project.project_id,
            {
                "app.py": b"print('hello')\n",
                "docs/readme.md": "# Проект\n".encode("utf-8"),
                "data/rows.csv": b"a,b\n1,2\n",
            },
        )
        return legacy, project

    def test_01_the_move_keeps_bytes_and_rewrites_the_record(self) -> None:
        legacy, project = self._legacy_project_with_files()
        old = legacy.store.workspace_path(project.project_id)
        before = {path.relative_to(old).as_posix(): _sha256(path) for path in old.rglob("*") if path.is_file()}

        configured = self._service(visible=True)
        result = configured.move_to_visible_folder(project.project_id)
        updated = result["project"]
        new = configured.store.workspace_path(project.project_id)

        self.assertFalse(result["already_visible"])
        self.assertEqual(new.parent, self.visible.resolve(strict=False))
        self.assertEqual(
            {path.relative_to(new).as_posix(): _sha256(path) for path in new.rglob("*") if path.is_file()},
            before,
            "every file must arrive byte-identically",
        )
        self.assertEqual(updated.workspace_dir, str(new))
        self.assertEqual(updated.project_path, published_folder_path(self.visible, new))
        self.assertEqual(
            [entry["sha256"] for entry in result["moved"]],
            [before[entry["path"]] for entry in result["moved"]],
        )
        self.assertFalse(old.is_dir(), "the emptied legacy workspace must be removed")
        self.assertEqual(len(updated.versions), len(project.versions))

    def test_02_the_record_and_its_history_are_not_rewritten(self) -> None:
        legacy, project = self._legacy_project_with_files()
        record_before = json.loads(
            (self.root / f"{project.project_id}.json").read_text(encoding="utf-8")
        )
        configured = self._service(visible=True)
        configured.move_to_visible_folder(project.project_id)
        record_after = json.loads(
            (self.root / f"{project.project_id}.json").read_text(encoding="utf-8")
        )
        for key in ("versions", "specification", "created_at", "status", "current_version"):
            self.assertEqual(record_before[key], record_after[key], key)
        self.assertNotEqual(record_before["project_path"], record_after["project_path"])

    def test_03_moving_twice_is_a_reported_no_op(self) -> None:
        legacy, project = self._legacy_project_with_files()
        configured = self._service(visible=True)
        configured.move_to_visible_folder(project.project_id)
        again = configured.move_to_visible_folder(project.project_id)
        self.assertTrue(again["already_visible"])
        self.assertEqual(again["moved"], [])

    def test_04_a_refused_move_leaves_the_project_where_it_was(self) -> None:
        legacy, project = self._legacy_project_with_files()
        old = legacy.store.workspace_path(project.project_id)
        before = sorted(path.name for path in old.rglob("*") if path.is_file())
        configured = self._service(visible=True)
        # A destination file that already holds different bytes must be refused, never
        # overwritten: the move may not destroy what is already in the visible folder.
        target, _published, _recorded = configured.store.allocate_workspace(
            project.project_id, project.name
        )
        (target / "app.py").write_bytes(b"DIFFERENT\n")
        with self.assertRaises(Exception):
            configured.move_to_visible_folder(project.project_id)
        self.assertEqual(
            sorted(path.name for path in old.rglob("*") if path.is_file()), before
        )
        self.assertEqual(
            json.loads((self.root / f"{project.project_id}.json").read_text(encoding="utf-8"))[
                "workspace_dir"
            ],
            "",
        )

    def test_05_no_configured_root_is_a_typed_refusal(self) -> None:
        legacy, project = self._legacy_project_with_files()
        with self.assertRaises(Exception):
            legacy.move_to_visible_folder(project.project_id)


# --------------------------------------------------------------------------- #
# 5. deletion stays reversible, in the visible folder
# --------------------------------------------------------------------------- #


class DeletionTests(_Base):
    """Delete moves the visible folder into the store's trash; restore puts it back."""

    def test_01_delete_moves_the_visible_folder_into_the_trash(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        self._write_run_files(service, project.project_id, {"app.py": b"print(1)\n"})
        folder = service.store.workspace_path(project.project_id)
        digest = _sha256(folder / "app.py")

        deleted = service.delete(project.project_id)
        self.assertFalse(folder.exists(), "the visible folder must move, not be copied")
        trash = self.root.parent / DELETED_DIRECTORY_NAME / deleted["trash_name"]
        self.assertTrue((trash / project.project_id / "app.py").is_file())
        self.assertEqual(_sha256(trash / project.project_id / "app.py"), digest)
        self.assertNotIn(str(self.visible), json.dumps(deleted, ensure_ascii=False))

    def test_02_restore_brings_it_back_to_the_visible_root_unchanged(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        self._write_run_files(service, project.project_id, {"app.py": b"print(1)\n"})
        folder = service.store.workspace_path(project.project_id)
        digest = _sha256(folder / "app.py")
        versions = project.versions

        service.delete(project.project_id)
        restored = service.restore_deleted(project.project_id)
        self.assertTrue(restored["restored"])
        back = service.store.workspace_path(project.project_id)
        self.assertEqual(back, folder, "the same visible folder must be restored")
        self.assertTrue((back / "app.py").is_file())
        self.assertEqual(_sha256(back / "app.py"), digest)
        record = service.open_project(project.project_id)
        self.assertEqual(record.versions, versions)
        self.assertEqual(record.workspace_dir, str(back))

    def test_03_a_record_that_cannot_be_placed_is_restored_to_the_store(self) -> None:
        service = self._service()
        project = service.create_from_discovery(_Session())
        self._write_run_files(service, project.project_id, {"app.py": b"print(1)\n"})
        service.delete(project.project_id)
        # The configured root is gone (a settings change), so the recorded folder can no
        # longer be trusted: the restore must fall back rather than write outside.
        elsewhere = ProjectService(ProjectStore(root=self.root, visible_root=self.base / "Other"))
        restored = elsewhere.restore_deleted(project.project_id)
        self.assertTrue(restored["restored"])
        resolved = elsewhere.store.workspace_path(project.project_id)
        self.assertIn(self.root.resolve(), resolved.parents)
        self.assertTrue((resolved / "app.py").is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
