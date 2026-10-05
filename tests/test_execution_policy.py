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
        from app.execution.identity import CommandIdentity

        req1 = ExecutionRequest(command=("python", "-c", "print(1)"), profile=self.profile)
        self.assertFalse(self.policy.evaluate(req1).allowed)
        self.assertEqual(self.policy.evaluate(req1).reason, "argv_not_authorized")

        # sys.executable with dangerous eval flag is also denied without exact argv authorization
        req2 = ExecutionRequest(command=(sys.executable, "-c", "print(1)"), profile=self.profile)
        self.assertFalse(self.policy.evaluate(req2).allowed)
        self.assertEqual(self.policy.evaluate(req2).reason, "argv_not_authorized")

        # When exact CommandIdentity is authorized, it is allowed
        exact_profile = ProjectExecutionProfile(
            profile_id="exact-profile",
            allowed_commands=(
                "python",
                CommandIdentity(sys.executable, ("-c", "print(1)")),
            ),
            timeout_seconds=30.0,
        )
        req3 = ExecutionRequest(command=(sys.executable, "-c", "print(1)"), profile=exact_profile)
        self.assertTrue(self.policy.evaluate(req3).allowed)

        # Standard non-eval Python command is allowed with executable-only allowlist
        req_script = ExecutionRequest(command=(sys.executable, "test.py"), profile=self.profile)
        self.assertTrue(self.policy.evaluate(req_script).allowed)

        # String-only python does NOT authorize arbitrary or test -m modules
        req_unittest = ExecutionRequest(
            command=(sys.executable, "-m", "unittest", "discover", "-s", "tests"),
            profile=self.profile,
        )
        self.assertFalse(self.policy.evaluate(req_unittest).allowed)
        self.assertEqual(self.policy.evaluate(req_unittest).reason, "argv_not_authorized")

        req_pytest = ExecutionRequest(
            command=(sys.executable, "-m", "pytest", "tests"),
            profile=self.profile,
        )
        self.assertFalse(self.policy.evaluate(req_pytest).allowed)
        self.assertEqual(self.policy.evaluate(req_pytest).reason, "argv_not_authorized")

        req_module = ExecutionRequest(
            command=(sys.executable, "-m", "http.server", "8000"),
            profile=self.profile,
        )
        self.assertFalse(self.policy.evaluate(req_module).allowed)
        self.assertEqual(self.policy.evaluate(req_module).reason, "argv_not_authorized")

        # Exact approved CommandIdentity for -m unittest and -m pytest is allowed
        cmd_unittest = CommandIdentity(sys.executable, ("-m", "unittest", "discover", "-s", "tests"))
        cmd_pytest = CommandIdentity(sys.executable, ("-m", "pytest", "tests"))
        exact_module_profile = ProjectExecutionProfile(
            profile_id="p-exact-mod",
            allowed_commands=(sys.executable, "python", cmd_unittest, cmd_pytest),
            timeout_seconds=30.0,
        )

        self.assertTrue(self.policy.evaluate(ExecutionRequest(command=(sys.executable, "-m", "unittest", "discover", "-s", "tests"), profile=exact_module_profile)).allowed)
        self.assertTrue(self.policy.evaluate(ExecutionRequest(command=(sys.executable, "-m", "pytest", "tests"), profile=exact_module_profile)).allowed)

        # Modified argv for approved module command is rejected
        modified_unittest = ExecutionRequest(
            command=(sys.executable, "-m", "unittest", "discover", "-s", "other_tests"),
            profile=exact_module_profile,
        )
        self.assertFalse(self.policy.evaluate(modified_unittest).allowed)
        self.assertEqual(self.policy.evaluate(modified_unittest).reason, "argv_not_authorized")

        # python -c "EVIL" -m unittest is rejected
        evil_combined = ExecutionRequest(
            command=(sys.executable, "-c", "import os", "-m", "unittest"),
            profile=exact_module_profile,
        )
        self.assertFalse(self.policy.evaluate(evil_combined).allowed)
        self.assertEqual(self.policy.evaluate(evil_combined).reason, "argv_not_authorized")


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
