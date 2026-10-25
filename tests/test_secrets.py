"""Tests for local secret loading and precedence.

The three sources, in order: process environment, the repository ``.env``, and the
operating system's credential vault. Every test here pins the *vault* explicitly --
an empty in-memory one, or one containing a value -- because a test that read this
machine's real vault would pass or fail depending on which keys the owner happens to
have added, which is not a fact about this code. The vault fallback itself is covered
by ``test_07`` and, end to end, by ``tests/test_provider_keys.py``.
"""

import tempfile
import unittest
from pathlib import Path

from app.config.secrets import SecretStore
from app.config.windows_creds import MemoryCredentialBackend


class SecretStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        )
        self.addCleanup(self.temp_dir.cleanup)
        self.env_file = Path(self.temp_dir.name) / ".env.test"
        # No key on this machine can answer for a test: the vault is this dictionary.
        self.vault = MemoryCredentialBackend()

    def store(self, **kwargs) -> SecretStore:
        kwargs.setdefault("env_file", self.env_file)
        kwargs.setdefault("environ", {})
        kwargs.setdefault("credential_backend", self.vault)
        return SecretStore(**kwargs)

    def test_reads_simple_dotenv_values_lazily(self) -> None:
        store = self.store()
        self.env_file.write_text(
            "# comment\nOPENAI_API_KEY=local-value\n"
            "ANTHROPIC_API_KEY='quoted value'\nexport XAI_API_KEY=from-export\n",
            encoding="utf-8",
        )

        self.assertEqual(store.get_secret("OPENAI_API_KEY"), "local-value")
        self.assertEqual(store.get_secret("ANTHROPIC_API_KEY"), "quoted value")
        self.assertEqual(store.get_secret("XAI_API_KEY"), "from-export")
        self.assertNotIn("local-value", repr(store))

    def test_process_environment_takes_precedence(self) -> None:
        self.env_file.write_text("OPENAI_API_KEY=local-value\n", encoding="utf-8")
        store = self.store(environ={"OPENAI_API_KEY": "process-value"})

        self.assertEqual(store.get_secret("OPENAI_API_KEY"), "process-value")

    def test_blank_or_missing_values_are_not_secrets(self) -> None:
        self.env_file.write_text(
            "OPENAI_API_KEY=\nANTHROPIC_API_KEY=   \n", encoding="utf-8"
        )
        store = self.store()

        self.assertIsNone(store.get_secret("OPENAI_API_KEY"))
        self.assertIsNone(store.get_secret("ANTHROPIC_API_KEY"))
        self.assertIsNone(store.get_secret("GEMINI_API_KEY"))

    def test_missing_env_file_is_allowed(self) -> None:
        store = self.store()

        self.assertIsNone(store.get_secret("OPENAI_API_KEY"))

    def test_secret_names_are_validated(self) -> None:
        store = self.store()

        with self.assertRaisesRegex(ValueError, "environment variable name"):
            store.get_secret("not a key")

    def test_06_the_vault_is_the_last_source_and_an_earlier_source_wins(self) -> None:
        """A key added in the interface is found -- unless the deployment already set one."""

        self.vault.set_secret("DEEPSEEK_API_KEY", "from-the-vault")
        store = self.store()
        self.assertEqual(store.get_secret("DEEPSEEK_API_KEY"), "from-the-vault")
        self.assertEqual(store.secret_source("DEEPSEEK_API_KEY"), "vault")
        self.assertTrue(store.has_secret("DEEPSEEK_API_KEY"))

        # ``.env`` outranks it, so a deployment's own key is not silently replaced by one
        # typed into the screen.
        self.env_file.write_text("DEEPSEEK_API_KEY=from-the-env-file\n", encoding="utf-8")
        preferred = self.store()
        self.assertEqual(preferred.get_secret("DEEPSEEK_API_KEY"), "from-the-env-file")
        self.assertEqual(preferred.secret_source("DEEPSEEK_API_KEY"), "environment")

    def test_07_a_vault_change_is_visible_without_a_new_store(self) -> None:
        """The lookup is per call, which is what makes «no restart» true."""

        store = self.store()
        self.assertIsNone(store.get_secret("GROQ_API_KEY"))
        self.vault.set_secret("GROQ_API_KEY", "gsk-added-while-running")
        self.assertEqual(store.get_secret("GROQ_API_KEY"), "gsk-added-while-running")
        self.vault.delete_secret("GROQ_API_KEY")
        self.assertIsNone(store.get_secret("GROQ_API_KEY"))

    def test_08_the_vault_names_are_not_values(self) -> None:
        self.vault.set_secret("RADOX_API_KEY_1", "rdx-a-value")
        store = self.store()

        names = store.secret_names()
        self.assertIn("RADOX_API_KEY_1", names)
        self.assertNotIn("rdx-a-value", repr(names))


if __name__ == "__main__":
    unittest.main()
