"""Offline tests for local provider account metadata."""

import tempfile
import unittest
from pathlib import Path

from app.config.provider_accounts import ProviderAccountConfig, load_provider_account_config


class ProviderAccountConfigTests(unittest.TestCase):
    def test_loads_allowlisted_email_fields_separately_from_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "OPENAI_API_KEY=must-not-be-loaded-here\n"
                "PROVIDER_OPENAI_EMAIL=local@example.test\n"
                "PROVIDER_GROQ_EMAIL=\"groq@example.test\"\n",
                encoding="utf-8",
            )

            config = load_provider_account_config(env_file=env_file, environ={})

        self.assertIsInstance(config, ProviderAccountConfig)
        self.assertEqual(config.openai_email, "local@example.test")
        self.assertEqual(config.email_for("groq"), "groq@example.test")
        self.assertIsNone(config.email_for("anthropic"))
        self.assertIsNone(config.email_for("unknown"))
        self.assertNotIn("example.test", repr(config))
        self.assertNotIn("must-not-be-loaded-here", repr(config))

    def test_process_environment_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "PROVIDER_GROQ_EMAIL=file@example.test\n", encoding="utf-8"
            )

            config = load_provider_account_config(
                env_file=env_file,
                environ={"PROVIDER_GROQ_EMAIL": " process@example.test "},
            )

        self.assertEqual(config.groq_email, "process@example.test")

    def test_missing_metadata_file_returns_empty_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_provider_account_config(
                env_file=Path(directory) / "missing.env", environ={}
            )

        self.assertEqual(config, ProviderAccountConfig())


if __name__ == "__main__":
    unittest.main()
