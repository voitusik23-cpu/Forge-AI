"""Tests for local secret loading and precedence."""

import tempfile
import unittest
from pathlib import Path

from app.config.secrets import SecretStore


class SecretStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        )
        self.addCleanup(self.temp_dir.cleanup)
        self.env_file = Path(self.temp_dir.name) / ".env.test"

    def test_reads_simple_dotenv_values_lazily(self) -> None:
        store = SecretStore(env_file=self.env_file, environ={})
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
        store = SecretStore(
            env_file=self.env_file,
            environ={"OPENAI_API_KEY": "process-value"},
        )

        self.assertEqual(store.get_secret("OPENAI_API_KEY"), "process-value")

    def test_blank_or_missing_values_are_not_secrets(self) -> None:
        self.env_file.write_text(
            "OPENAI_API_KEY=\nANTHROPIC_API_KEY=   \n", encoding="utf-8"
        )
        store = SecretStore(env_file=self.env_file, environ={})

        self.assertIsNone(store.get_secret("OPENAI_API_KEY"))
        self.assertIsNone(store.get_secret("ANTHROPIC_API_KEY"))
        self.assertIsNone(store.get_secret("GEMINI_API_KEY"))

    def test_missing_env_file_is_allowed(self) -> None:
        store = SecretStore(env_file=self.env_file, environ={})

        self.assertIsNone(store.get_secret("OPENAI_API_KEY"))

    def test_secret_names_are_validated(self) -> None:
        store = SecretStore(env_file=self.env_file, environ={})

        with self.assertRaisesRegex(ValueError, "environment variable name"):
            store.get_secret("not a key")


if __name__ == "__main__":
    unittest.main()
