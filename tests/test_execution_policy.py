"""Unit tests for ExecutionPolicy evaluation and default-deny enforcement."""

import sys
import unittest

from app.execution.policy import ExecutionPolicy, ExecutionPolicyDecision
from app.execution.profile import (
    ExecutionEnvironmentType,
    ProjectExecutionProfile,
    TargetOS,
)
from app.execution.request import ExecutionRequest


class TestExecutionPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ExecutionPolicy()
        self.profile = ProjectExecutionProfile(
            profile_id="safe-profile",
            allowed_commands=("python", "pytest", sys.executable),
            timeout_seconds=30.0,
        )

    def test_missing_or_invalid_request(self) -> None:
        decision = self.policy.evaluate(None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "request_missing_or_invalid")

    def test_empty_command(self) -> None:
        req = ExecutionRequest(command=(), profile=self.profile)
        decision = self.policy.evaluate(req)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "command_empty_or_invalid")

    def test_invalid_command_part(self) -> None:
        req = ExecutionRequest(command=("python", ""), profile=self.profile)
        decision = self.policy.evaluate(req)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "invalid_command_part")

    def test_missing_profile(self) -> None:
        req = ExecutionRequest(command=("python", "--version"), profile=None)
        decision = self.policy.evaluate(req)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "profile_missing_or_invalid")

    def test_invalid_profile(self) -> None:
        bad_profile = ProjectExecutionProfile(profile_id="")
        req = ExecutionRequest(command=("python", "--version"), profile=bad_profile)
        decision = self.policy.evaluate(req)
        self.assertFalse(decision.allowed)
        self.assertIn("profile_invalid", decision.reason)

    def test_default_deny_when_allowed_commands_empty(self) -> None:
        empty_allowed_profile = ProjectExecutionProfile(
            profile_id="empty-allowed",
            allowed_commands=(),
        )
        req = ExecutionRequest(command=("python", "--version"), profile=empty_allowed_profile)
        decision = self.policy.evaluate(req)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "no_commands_allowed_by_profile")

    def test_disallowed_command_denied(self) -> None:
        req = ExecutionRequest(command=("rm", "-rf", "/"), profile=self.profile)
        decision = self.policy.evaluate(req)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "command_not_allowed:rm")

    def test_allowed_command_matches(self) -> None:
        req1 = ExecutionRequest(command=("python", "-c", "print(1)"), profile=self.profile)
        self.assertTrue(self.policy.evaluate(req1).allowed)

        req2 = ExecutionRequest(command=(sys.executable, "-c", "print(1)"), profile=self.profile)
        self.assertTrue(self.policy.evaluate(req2).allowed)

    def test_unsafe_working_directory_denied(self) -> None:
        for unsafe in ("/root", "../escape", "subdir/../../escape"):
            req = ExecutionRequest(
                command=("python", "--version"),
                working_directory=unsafe,
                profile=self.profile,
            )
            decision = self.policy.evaluate(req)
            self.assertFalse(decision.allowed, f"Expected {unsafe} to be denied")
            self.assertEqual(decision.reason, "unsafe_working_directory")

    def test_timeout_exceeding_profile_limit_denied(self) -> None:
        req = ExecutionRequest(
            command=("python", "--version"),
            timeout_seconds=60.0,  # profile max is 30.0
            profile=self.profile,
        )
        decision = self.policy.evaluate(req)
        self.assertFalse(decision.allowed)
        self.assertIn("timeout_exceeds_profile_limit", decision.reason)

    def test_invalid_env_var_names_denied(self) -> None:
        req = ExecutionRequest(
            command=("python", "--version"),
            environment_variables={"123BAD": "val"},
            profile=self.profile,
        )
        decision = self.policy.evaluate(req)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "invalid_env_var_name:123BAD")


if __name__ == "__main__":
    unittest.main()
