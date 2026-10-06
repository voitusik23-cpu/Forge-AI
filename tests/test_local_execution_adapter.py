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
from app.runtime.run_scope import RunScope
from app.tools.workspace import Workspace
from run_scope_support import freeze_scope


class TestLocalExecutionAdapter(unittest.TestCase):
    def setUp(self) -> None:
        # Each test is its own Run with its own perimeter.
        RunScope.release_all()
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

        # A binary the policy is expected to reject. Declaring it as a capability
        # of this Run is what lets the permission check, rather than the scope,
        # decide. It is never executed.
        self.allowed_only_bin = "allowed_only_bin"
        scope_commands = tuple(self.profile.allowed_commands) + (self.allowed_only_bin,)
        scope_profile = ProjectExecutionProfile(
            profile_id=self.profile.profile_id,
            environment_type=self.profile.environment_type,
            runtime_name=self.profile.runtime_name,
            allowed_commands=scope_commands,
            timeout_seconds=self.profile.timeout_seconds,
            max_output_bytes=self.profile.max_output_bytes,
        )
        # DECISION 1/2: the run identity is established here and the full perimeter
        # is declared once, then frozen before any dispatch. A different perimeter
        # would require a new run id, never a widened scope.
        self.run_id = "local-adapter-run"
        self.scope = freeze_scope(
            self.run_id,
            workspace=Workspace(self.workspace_root),
            commands=scope_commands,
            profile=scope_profile,
        )

    def tearDown(self) -> None:
        RunScope.release_all()
        self.temp_dir.cleanup()

    def _execute(self, req: ExecutionRequest) -> ExecutionResult:
        # The declared permission set keeps full command identities, so a pinned
        # argv is not collapsed to a bare executable. It may only narrow the scope.
        declared = tuple(req.profile.allowed_commands) if req.profile else ()
        return self.coordinator.execute(
            req,
            workspace_root=self.workspace_root,
            run_id=self.run_id,
            allowed_commands=declared,
            run_scope=self.scope,
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
        # The profile stays inside the Run's declared envelope so that the
        # *policy* is what denies the command, not the scope. The command itself
        # is declared in the perimeter: the permission check, not the scope,
        # decides whether it may run.
        restricted_profile = ProjectExecutionProfile(
            profile_id="restricted",
            allowed_commands=("allowed_only_bin",),
            timeout_seconds=self.scope.execution_profile.timeout_seconds,
            max_output_bytes=self.scope.execution_profile.max_output_bytes,
        )
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print('should not run')"),
            profile=restricted_profile,
        )
        result = self.coordinator.execute(
            req,
            workspace_root=self.workspace_root,
            run_id=self.run_id,
            allowed_commands=frozenset({sys.executable}),
            run_scope=self.scope,
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
