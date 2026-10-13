"""Harvesting must never read credential material, whatever named it.

Why this guard exists
---------------------
``harvest_artifacts`` opens a declared artifact and publishes its SHA-256 into the
run's evidence.  The set of targets it is given is not purely the operator's: a step
graph arrives from the client with its own ``verification``, and a verification's
artifact target is *added* to the request's targets (the documented model: "the
artifact targets this verification forces the step to declare").  A client could
therefore name a credential-shaped file -- ``.env`` was measured being read and its
digest returned -- and turn the server into a reader of a file the operator never
intended to expose.

Everywhere else in Forge this material is withheld by the same shared recognizer:
staging refuses to copy it into the scratch tree and the project scanner refuses to
fingerprint it.  Harvesting now agrees with them.  The refusal is by *name*, not by
existence, so a missing ``.env`` is refused too.

RED BEFORE THE FIX: tests 01, 02 and 04 fail (the credential file is harvested and
hashed).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.execution.workspace_manager import EphemeralWorkspaceManager


class HarvestRefusesCredentialMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="forge_harvest_")
        self.root = Path(self._temp_dir.name).resolve()
        (self.root / "notes.txt").write_text("ordinary project note\n", encoding="utf-8")
        (self.root / ".env").write_text("OPENAI_API_KEY=local-test-value\n", encoding="utf-8")
        (self.root / "keys").mkdir()
        (self.root / "keys" / "id_rsa").write_text("PRIVATE KEY\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _harvest(self, *targets: str):
        manager = EphemeralWorkspaceManager()
        return manager.harvest_artifacts(list(targets), run_id="harvest-run", root=self.root)

    def test_01_a_dotenv_target_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._harvest(".env")

    def test_02_a_credential_directory_target_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._harvest("keys/id_rsa")

    def test_03_an_ordinary_file_is_still_harvested_with_its_digest(self) -> None:
        artifacts = self._harvest("notes.txt")

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].relative_path, "notes.txt")
        self.assertTrue(str(artifacts[0].sha256))

    def test_04_the_refusal_is_by_name_not_by_existence(self) -> None:
        """A missing credential file is refused too: the rule is the name."""

        with self.assertRaises(ValueError):
            self._harvest(".env.production")

    def test_05_a_credential_file_is_not_harvested_alongside_an_ordinary_one(self) -> None:
        with self.assertRaises(ValueError):
            self._harvest("notes.txt", ".env")


if __name__ == "__main__":
    unittest.main()
