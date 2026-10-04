"""Unit tests for secret redaction contracts and default implementation."""

import unittest

from app.execution.redaction import (
    DefaultSecretRedactor,
    REDACTED_PLACEHOLDER,
    SecretRedactor,
)
from app.execution.request import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)


class TestSecretRedaction(unittest.TestCase):
    def test_protocol_implementation(self) -> None:
        redactor = DefaultSecretRedactor()
        self.assertIsInstance(redactor, SecretRedactor)

    def test_redact_registered_secrets(self) -> None:
        redactor = DefaultSecretRedactor(registered_secrets=["my_super_secret_token", "12345-secret"])
        text = "Connecting with my_super_secret_token to server (id: 12345-secret)"
        cleaned = redactor.redact_text(text)
        self.assertEqual(
            cleaned,
            f"Connecting with {REDACTED_PLACEHOLDER} to server (id: {REDACTED_PLACEHOLDER})",
        )

    def test_redact_common_secret_patterns(self) -> None:
        redactor = DefaultSecretRedactor()
        samples = [
            ("API key sk-proj1234567890123456789012345 in header", f"API key {REDACTED_PLACEHOLDER} in header"),
            ("GitHub token ghp_123456789012345678901234567890", f"GitHub token {REDACTED_PLACEHOLDER}"),
            ("Bearer token: Bearer abcdef1234567890abcdef", f"Bearer token: {REDACTED_PLACEHOLDER}"),
        ]
        for original, expected in samples:
            self.assertEqual(redactor.redact_text(original), expected)

    def test_redact_mapping_sensitive_keys(self) -> None:
        redactor = DefaultSecretRedactor()
        data = {
            "normal_key": "visible",
            "api_key": "raw_secret_value",
            "auth_token": "token123",
            "DATABASE_PASSWORD": "pass",
            "nested": {
                "SECRET_VAL": "hidden",
                "safe": "visible",
            },
            "list_data": ["normal", "api_key=sk-proj1234567890123456789012345"],
        }
        sanitized = redactor.redact_mapping(data)
        self.assertEqual(sanitized["normal_key"], "visible")
        self.assertEqual(sanitized["api_key"], REDACTED_PLACEHOLDER)
        self.assertEqual(sanitized["auth_token"], REDACTED_PLACEHOLDER)
        self.assertEqual(sanitized["DATABASE_PASSWORD"], REDACTED_PLACEHOLDER)
        self.assertEqual(sanitized["nested"]["SECRET_VAL"], REDACTED_PLACEHOLDER)  # type: ignore
        self.assertEqual(sanitized["nested"]["safe"], "visible")  # type: ignore
        self.assertEqual(
            sanitized["list_data"],  # type: ignore
            ["normal", f"api_key={REDACTED_PLACEHOLDER}"],
        )

    def test_redact_command_arguments(self) -> None:
        redactor = DefaultSecretRedactor(registered_secrets=["very_secret"])
        cmd = (
            "curl",
            "-H",
            "Authorization: Bearer abcdef1234567890abcdef",
            "--token=my_secret_token",
            "--password=admin_pass",
            "--secret-key=xyz",
            "--data=very_secret",
            "--verbose",
        )
        redacted_cmd = redactor.redact_command(cmd)
        self.assertEqual(redacted_cmd[0], "curl")
        self.assertEqual(redacted_cmd[1], "-H")
        self.assertEqual(redacted_cmd[2], f"Authorization: {REDACTED_PLACEHOLDER}")
        self.assertEqual(redacted_cmd[3], f"--token={REDACTED_PLACEHOLDER}")
        self.assertEqual(redacted_cmd[4], f"--password={REDACTED_PLACEHOLDER}")
        self.assertEqual(redacted_cmd[5], f"--secret-key={REDACTED_PLACEHOLDER}")
        self.assertEqual(redacted_cmd[6], f"--data={REDACTED_PLACEHOLDER}")
        self.assertEqual(redacted_cmd[7], "--verbose")

    def test_redact_execution_request(self) -> None:
        redactor = DefaultSecretRedactor(registered_secrets=["s3cr3t"])
        req = ExecutionRequest(
            command=("deploy", "--token=secret_val"),
            environment_variables={"API_KEY": "secret_key_123", "PORT": "8080"},
            metadata={"secret_owner": "admin", "debug": True},
        )
        safe_req = redactor.redact_request(req)
        self.assertEqual(safe_req.command, ("deploy", f"--token={REDACTED_PLACEHOLDER}"))
        self.assertEqual(safe_req.environment_variables["API_KEY"], REDACTED_PLACEHOLDER)
        self.assertEqual(safe_req.environment_variables["PORT"], "8080")
        self.assertEqual(safe_req.metadata["secret_owner"], REDACTED_PLACEHOLDER)
        self.assertEqual(safe_req.metadata["debug"], True)

    def test_redact_execution_result_and_safe_event_data(self) -> None:
        redactor = DefaultSecretRedactor(registered_secrets=["secret_token_123"])
        res = ExecutionResult(
            request_id="req-99",
            status=ExecutionStatus.FAILURE,
            exit_code=1,
            stdout="Logged in using secret_token_123 successfully",
            stderr="Failed connecting with sk-proj1234567890123456789012345",
            duration_seconds=1.2,
            metadata={"secret_hash": "abc", "env": "prod"},
        )
        safe_res = redactor.redact_result(res)
        self.assertNotIn("secret_token_123", safe_res.stdout)
        self.assertIn(REDACTED_PLACEHOLDER, safe_res.stdout)
        self.assertNotIn("sk-proj1234567890123456789012345", safe_res.stderr)
        self.assertIn(REDACTED_PLACEHOLDER, safe_res.stderr)
        self.assertEqual(safe_res.metadata["secret_hash"], REDACTED_PLACEHOLDER)

        event_data = redactor.safe_event_data(res)
        self.assertEqual(event_data["request_id"], "req-99")
        self.assertEqual(event_data["status"], "FAILURE")
        self.assertEqual(event_data["exit_code"], 1)
        self.assertEqual(event_data["duration_seconds"], 1.2)
        # Event data preserves lengths and sanitized metadata without raw stdout/stderr dumps
        self.assertIn("stdout_len", event_data)
        self.assertIn("stderr_len", event_data)
        self.assertNotIn("secret_token_123", str(event_data))


if __name__ == "__main__":
    unittest.main()
