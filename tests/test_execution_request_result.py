"""Unit tests for ExecutionRequest and ExecutionResult domain contracts."""

import unittest

from app.execution.profile import (
    ExecutionEnvironmentType,
    ProjectExecutionProfile,
)
from app.execution.request import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)


class TestExecutionRequestResult(unittest.TestCase):
    def test_valid_execution_request_defaults(self) -> None:
        request = ExecutionRequest(command=("pytest", "-q"))
        errors = request.validate()
        self.assertEqual(errors, ())
        self.assertTrue(len(request.request_id) > 0)
        self.assertEqual(request.working_directory, ".")
        self.assertEqual(request.environment_variables, {})
        self.assertIsNone(request.timeout_seconds)
        self.assertIsNone(request.profile)

    def test_command_must_not_be_empty(self) -> None:
        request = ExecutionRequest(command=())
        errors = request.validate()
        self.assertIn("command_required", errors)

    def test_command_parts_must_be_valid_strings(self) -> None:
        request = ExecutionRequest(command=("pytest", "", "  "))
        errors = request.validate()
        self.assertIn("invalid_command_part", errors)

    def test_unsafe_working_directory_rejected(self) -> None:
        request = ExecutionRequest(command=("pytest",), working_directory="../escape")
        errors = request.validate()
        self.assertIn("unsafe_working_directory", errors)

    def test_request_validation_against_profile_allowed_commands(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="restricted",
            allowed_commands=("pytest", "ruff"),
        )
        allowed_req = ExecutionRequest(
            command=("pytest", "-k", "test_math"),
            profile=profile,
        )
        self.assertEqual(allowed_req.validate(), ())

        disallowed_req = ExecutionRequest(
            command=("rm", "-rf", "/tmp"),
            profile=profile,
        )
        errors = disallowed_req.validate()
        self.assertIn("command_not_allowed:rm", errors)

    def test_request_validation_catches_invalid_profile(self) -> None:
        invalid_profile = ProjectExecutionProfile(
            profile_id="",  # invalid
            timeout_seconds=-5,  # invalid
        )
        request = ExecutionRequest(command=("pytest",), profile=invalid_profile)
        errors = request.validate()
        self.assertIn("profile_error:profile_id_required", errors)
        self.assertIn("profile_error:invalid_timeout_seconds", errors)

    def test_execution_result_properties(self) -> None:
        success_result = ExecutionResult(
            request_id="req-1",
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            stdout="Ran 5 tests\nOK",
            stderr="",
            duration_seconds=0.45,
        )
        self.assertTrue(success_result.success)
        self.assertFalse(success_result.timed_out)
        self.assertEqual(success_result.exit_code, 0)

        failure_result = ExecutionResult(
            request_id="req-2",
            status=ExecutionStatus.FAILURE,
            exit_code=1,
            stdout="",
            stderr="AssertionError: 1 != 2",
            duration_seconds=0.32,
        )
        self.assertFalse(failure_result.success)
        self.assertFalse(failure_result.timed_out)

        timeout_result = ExecutionResult(
            request_id="req-3",
            status=ExecutionStatus.TIMEOUT,
            exit_code=None,
            stdout="",
            stderr="Process timed out after 30s",
            duration_seconds=30.01,
        )
        self.assertFalse(timeout_result.success)
        self.assertTrue(timeout_result.timed_out)

    def test_execution_result_metadata_immutability(self) -> None:
        meta = {"target": "unit_tests", "env": "ci"}
        result = ExecutionResult(
            request_id="req-4",
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            metadata=meta,
        )
        self.assertEqual(result.metadata["target"], "unit_tests")


if __name__ == "__main__":
    unittest.main()
