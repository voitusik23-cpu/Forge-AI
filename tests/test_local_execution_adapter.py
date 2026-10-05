"""Offline integration tests for LocalExecutionAdapter using safe local commands."""

import sys
import tempfile
import unittest
from pathlib import Path

from app.execution.adapter import ExecutionAuthorizationError, LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.identity import CommandIdentity
from app.execution.policy import ExecutionPolicy
from app.execution.profile import (
    ExecutionEnvironmentType,
    ProjectExecutionProfile,
)
from app.execution.redaction import DefaultSecretRedactor, REDACTED_PLACEHOLDER
from app.execution.request import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)


class TestLocalExecutionAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.redactor = DefaultSecretRedactor(registered_secrets=["sensitive_token_999"])
        self.policy = ExecutionPolicy()
        self.adapter = LocalExecutionAdapter(
            workspace_root=self.workspace_root,
            policy=self.policy,
            redactor=self.redactor,
        )
        self.coordinator = ExecutionCoordinator(
            adapter=self.adapter,
            policy=self.policy,
            redactor=self.redactor,
        )

        self.profile = ProjectExecutionProfile(
            profile_id="local-py",
            environment_type=ExecutionEnvironmentType.HOST,
            runtime_name="python",
            allowed_commands=(
                sys.executable,
                "python",
                "python.exe",
                CommandIdentity(sys.executable, ("-c", "import sys; sys.stdout.write('forge_stdout'); sys.exit(0)")),
                CommandIdentity(sys.executable, ("-c", "import sys; sys.stderr.write('forge_failed'); sys.exit(42)")),
                CommandIdentity(sys.executable, ("-c", "import time; time.sleep(3)")),
                CommandIdentity(sys.executable, ("-c", "print('A' * 200)")),
                CommandIdentity(sys.executable, ("-c", "print('Found token: sensitive_token_999 in stream')")),
                CommandIdentity(sys.executable, ("-c", "import os, pathlib; print(pathlib.Path.cwd().name)")),
                CommandIdentity(sys.executable, ("-c", "print(1)")),
            ),
            timeout_seconds=10.0,
            max_output_bytes=1048576,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _execute(self, req: ExecutionRequest) -> ExecutionResult:
        allowed = frozenset(
            cmd.executable if hasattr(cmd, "executable") else str(cmd)
            for cmd in (req.profile.allowed_commands if req.profile else ())
        )
        return self.coordinator.execute(
            req,
            workspace_root=self.workspace_root,
            allowed_commands=allowed,
        )

    def test_direct_call_without_authorized_execution_rejected(self) -> None:
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print(1)"),
            profile=self.profile,
        )
        with self.assertRaises(ExecutionAuthorizationError):
            self.adapter.execute(req)

    def test_successful_execution(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('forge_stdout'); sys.exit(0)",
            ),
            profile=self.profile,
        )
        result = self._execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "forge_stdout")
        self.assertEqual(result.stderr, "")
        self.assertFalse(result.truncated)
        self.assertTrue(result.duration_seconds > 0)
        self.assertTrue(result.success)

    def test_failure_non_zero_exit_code(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('forge_failed'); sys.exit(42)",
            ),
            profile=self.profile,
        )
        result = self._execute(req)
        self.assertEqual(result.status, ExecutionStatus.FAILURE)
        self.assertEqual(result.exit_code, 42)
        self.assertIn("forge_failed", result.stderr)
        self.assertFalse(result.success)

    def test_timeout_execution(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import time; time.sleep(3)",
            ),
            timeout_seconds=0.2,
            profile=self.profile,
        )
        result = self._execute(req)
        self.assertEqual(result.status, ExecutionStatus.TIMEOUT)
        self.assertIsNone(result.exit_code)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.success)

    def test_output_truncation(self) -> None:
        small_output_profile = ProjectExecutionProfile(
            profile_id="small-output",
            allowed_commands=(
                sys.executable,
                CommandIdentity(sys.executable, ("-c", "print('A' * 200)")),
            ),
            max_output_bytes=25,
            timeout_seconds=5.0,
        )
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "print('A' * 200)",
            ),
            profile=small_output_profile,
        )
        result = self._execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 25)

    def test_policy_denial_prevents_process_execution(self) -> None:
        restricted_profile = ProjectExecutionProfile(
            profile_id="restricted",
            allowed_commands=("allowed_only_bin",),
        )
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print('should not run')"),
            profile=restricted_profile,
        )
        result = self.coordinator.execute(
            req,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable, "allowed_only_bin"}),
        )
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIsNone(result.exit_code)
        self.assertIn("Execution denied by policy", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_secret_redaction_in_output(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "print('Found token: sensitive_token_999 in stream')",
            ),
            profile=self.profile,
        )
        result = self._execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertNotIn("sensitive_token_999", result.stdout)
        self.assertIn(REDACTED_PLACEHOLDER, result.stdout)

    def test_working_directory_enforcement(self) -> None:
        sub_dir = self.workspace_root / "nested"
        sub_dir.mkdir()
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import os, pathlib; print(pathlib.Path.cwd().name)",
            ),
            working_directory="nested",
            profile=self.profile,
        )
        result = self._execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.stdout.strip(), "nested")

    def test_missing_working_directory_returns_error(self) -> None:
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print(1)"),
            working_directory="non_existent_subdir",
            profile=self.profile,
        )
        result = self._execute(req)
        self.assertEqual(result.status, ExecutionStatus.ERROR)
        self.assertIn("Working directory does not exist", result.stderr)


if __name__ == "__main__":
    unittest.main()
