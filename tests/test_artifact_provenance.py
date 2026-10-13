"""ARTIFACT_PRODUCED must be proven by this attempt, not by a matching path.

The harvest measures a file after the command ran. On its own that measurement says
"the file is there", not "this attempt put it there". These tests pin the comparison
the harvest performs between the state recorded **immediately before the child was
created** and the state it measures afterwards:

* a file that was already there and did not change is **not** production;
* a file that was absent and is now present **is** production;
* a file that changed content **is** production, even if its timestamp was preserved;
* a replacement at the same path is decided by content, not by path or time;
* without a baseline the question is unanswered, not answered "yes";
* the recorded answer names the attempt that measured it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.execution.workspace_manager import (
    EphemeralWorkspaceManager,
    snapshot_artifact_states,
)

RUN_ID = "provenance-run"


class ArtifactProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="forge_prov_")
        self.root = Path(self._temp_dir.name).resolve()
        self.manager = EphemeralWorkspaceManager()

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _provenance(self, target: str, *, baseline) -> dict:
        artifacts = self.manager.harvest_artifacts(
            [target], run_id=RUN_ID, root=self.root, baseline=baseline
        )
        self.assertEqual(len(artifacts), 1)
        return dict(artifacts[0].metadata)

    def test_01_a_file_that_existed_before_and_did_not_change_is_not_production(self) -> None:
        (self.root / "notes.txt").write_text("original\n", encoding="utf-8")
        baseline = snapshot_artifact_states(self.root, ["notes.txt"])

        provenance = self._provenance("notes.txt", baseline=baseline)

        self.assertIs(provenance["existed_before"], True)
        self.assertIs(provenance["changed_by_this_attempt"], False)

    def test_02_a_file_created_by_this_attempt_is_production(self) -> None:
        baseline = snapshot_artifact_states(self.root, ["made.txt"])
        (self.root / "made.txt").write_text("made\n", encoding="utf-8")

        provenance = self._provenance("made.txt", baseline=baseline)

        self.assertIs(provenance["existed_before"], False)
        self.assertIs(provenance["changed_by_this_attempt"], True)

    def test_03_a_pre_existing_file_changed_by_this_attempt_is_production(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("before\n", encoding="utf-8")
        baseline = snapshot_artifact_states(self.root, ["notes.txt"])
        target.write_text("after\n", encoding="utf-8")

        provenance = self._provenance("notes.txt", baseline=baseline)

        self.assertIs(provenance["changed_by_this_attempt"], True)
        self.assertTrue(provenance["sha256_before"])

    def test_04_a_replacement_is_decided_by_content_not_by_path_or_time(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("before\n", encoding="utf-8")
        baseline = snapshot_artifact_states(self.root, ["notes.txt"])
        stamp = target.stat().st_mtime_ns
        target.write_text("replaced\n", encoding="utf-8")
        os.utime(target, ns=(stamp, stamp))  # keep the timestamp identical on purpose

        provenance = self._provenance("notes.txt", baseline=baseline)

        self.assertIs(provenance["changed_by_this_attempt"], True)

    def test_05_without_a_baseline_the_question_is_unanswered(self) -> None:
        (self.root / "made.txt").write_text("made\n", encoding="utf-8")

        provenance = self._provenance("made.txt", baseline=None)

        self.assertIsNone(provenance["changed_by_this_attempt"])
        self.assertIsNone(provenance["existed_before"])

    def test_06_a_pre_existing_state_is_never_reported_as_production(self) -> None:
        """The current contract cannot express "verify an existing state"."""

        (self.root / "config.json").write_text("{}\n", encoding="utf-8")
        baseline = snapshot_artifact_states(self.root, ["config.json"])

        provenance = self._provenance("config.json", baseline=baseline)

        self.assertIsNot(provenance["changed_by_this_attempt"], True)

    def test_07_the_recorded_answer_names_the_attempt_that_measured_it(self) -> None:
        (self.root / "made.txt").write_text("made\n", encoding="utf-8")
        baseline = snapshot_artifact_states(self.root, ["made.txt"])

        provenance = self._provenance("made.txt", baseline=baseline)

        self.assertEqual(provenance["baseline_recorded_for"], RUN_ID)

    def test_08_the_snapshot_reads_only_inside_the_given_root(self) -> None:
        outside = self.root.parent / "outside-provenance.txt"
        outside.write_text("outside\n", encoding="utf-8")
        (self.root / "notes.txt").write_text("inside\n", encoding="utf-8")
        try:
            snapshot = snapshot_artifact_states(
                self.root,
                ["../outside-provenance.txt", "/etc/passwd", "C:/Windows/win.ini", "notes.txt"],
            )
        finally:
            outside.unlink(missing_ok=True)

        # Nothing outside the root is ever read, and an absolute target is refused
        # rather than rewritten into a relative one.
        self.assertEqual(sorted(snapshot), ["notes.txt"])
        self.assertTrue(snapshot["notes.txt"]["exists"])

    def test_09_a_credential_shaped_target_is_not_even_snapshotted(self) -> None:
        (self.root / ".env").write_text("SECRET=value\n", encoding="utf-8")

        self.assertEqual(snapshot_artifact_states(self.root, [".env"]), {})


if __name__ == "__main__":
    unittest.main()
