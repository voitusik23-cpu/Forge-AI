"""Offline tests for explicit, read-only project snapshots."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.artifacts import ArtifactType
from app.orchestrator.models import EventType, Run, Task
from app.tools.project_snapshots import ProjectSnapshotError, ProjectSnapshotter
from app.tools.workspace import Workspace


class ProjectSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.snapshotter = ProjectSnapshotter()
        self.run = Run(Task(id="snapshot-test", description="inspect chosen files"))

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, paths, *, attempt=0, workspace=None):
        return self.snapshotter.create(
            paths,
            run=self.run,
            attempt_number=attempt,
            workspace=self.workspace if workspace is None else workspace,
        )

    def test_existing_file_has_sha256_fingerprint(self):
        (self.root / "readme.txt").write_bytes(b"snapshot bytes")
        result = self.snapshot(["readme.txt"])
        file = result.files[0]
        self.assertTrue(file.exists)
        self.assertEqual(file.fingerprint, hashlib.sha256(b"snapshot bytes").hexdigest())

    def test_missing_file_is_recorded_without_failure(self):
        result = self.snapshot(["missing.txt"])
        self.assertEqual(result.files[0].exists, False)
        self.assertIsNone(result.files[0].fingerprint)

    def test_multiple_explicit_files_are_sorted_and_missing_is_counted(self):
        (self.root / "z.txt").write_text("z", encoding="utf-8")
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        result = self.snapshot(["z.txt", "missing.txt", "a.txt"])
        self.assertEqual(len(result.files), 3)
        self.assertEqual([item.relative_path for item in result.files], ["a.txt", "missing.txt", "z.txt"])
        event = next(item for item in self.run.events if item.type == EventType.SNAPSHOT_CREATED)
        self.assertEqual(event.data["number_of_files"], 3)
        self.assertEqual(event.data["number_existing"], 2)
        self.assertEqual(event.data["number_missing"], 1)

    def test_only_requested_paths_are_included(self):
        for index in range(10):
            (self.root / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
        result = self.snapshot(["file-1.txt", "file-8.txt"])
        self.assertEqual([item.relative_path for item in result.files], ["file-1.txt", "file-8.txt"])

    def test_fingerprints_are_stable_for_same_content_and_change_with_content(self):
        target = self.root / "same.txt"
        target.write_bytes(b"same")
        first = self.snapshot(["same.txt"])
        second = self.snapshot(["same.txt"])
        self.assertEqual(first.files[0].fingerprint, second.files[0].fingerprint)
        target.write_bytes(b"changed")
        third = self.snapshot(["same.txt"])
        self.assertNotEqual(second.files[0].fingerprint, third.files[0].fingerprint)

    def test_traversal_and_absolute_paths_are_rejected_before_read(self):
        outside = self.root.parent / "snapshot-outside.txt"
        outside.write_text("outside", encoding="utf-8")
        for path in (
            "../snapshot-outside.txt",
            str(outside),
            "C:\\outside\\file.txt",
            "file.txt:stream",
        ):
            with self.subTest(path=path):
                with patch.object(Path, "open", side_effect=AssertionError("must not read outside")):
                    with self.assertRaises(ProjectSnapshotError):
                        self.snapshot([path])

    def test_missing_workspace_is_rejected(self):
        with self.assertRaises(ProjectSnapshotError):
            self.snapshotter.create(
                ["file.txt"], run=self.run, attempt_number=0, workspace=None
            )

    def test_snapshot_is_read_only_and_does_not_leak_content(self):
        payload = "private snapshot fixture"
        (self.root / "file.txt").write_text(payload, encoding="utf-8")
        before = {
            p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.root.rglob("*") if p.is_file()
        }
        result = self.snapshot(["file.txt"])
        after = {
            p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.root.rglob("*") if p.is_file()
        }
        event = next(item for item in self.run.events if item.type == EventType.SNAPSHOT_CREATED)
        self.assertEqual(before, after)
        self.assertNotIn(payload, repr(result))
        self.assertNotIn(payload, repr(event.data))

    def test_snapshot_is_linked_to_project_snapshot_artifact_and_run_attempt(self):
        (self.root / "file.txt").write_text("fixture", encoding="utf-8")
        result = self.snapshot(["file.txt"], attempt=3)
        artifact = self.run.artifacts[0]
        event = next(item for item in self.run.events if item.type == EventType.SNAPSHOT_CREATED)
        self.assertEqual(result.run_id, self.run.id)
        self.assertEqual(result.attempt_number, 3)
        self.assertEqual(artifact.run_id, self.run.id)
        self.assertEqual(artifact.artifact_type, ArtifactType.PROJECT_SNAPSHOT)
        self.assertEqual(artifact.project_snapshot_id, result.snapshot_id)
        self.assertEqual(event.data["snapshot_id"], result.snapshot_id)
        self.assertEqual(event.data["attempt_number"], 3)

    def test_duplicate_requested_path_is_one_file_and_empty_set_is_valid(self):
        (self.root / "file.txt").write_text("x", encoding="utf-8")
        result = self.snapshot(["file.txt", "file.txt"])
        empty = self.snapshot([])
        self.assertEqual(len(result.files), 1)
        self.assertEqual(empty.files, ())
        self.assertEqual(len(self.run.artifacts), 1)
        self.assertEqual(self.run.artifacts[0].project_snapshot_id, result.snapshot_id)


if __name__ == "__main__":
    unittest.main()
