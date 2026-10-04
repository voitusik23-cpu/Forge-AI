"""Unit tests for ProjectExecutionProfile domain model and validation."""

import unittest

from app.execution.profile import (
    ExecutionEnvironmentType,
    ProfileValidationStatus,
    ProjectExecutionProfile,
    TargetOS,
)


class TestProjectExecutionProfile(unittest.TestCase):
    def test_valid_profile_defaults(self) -> None:
        profile = ProjectExecutionProfile(profile_id="test-profile")
        result = profile.validate()
        self.assertTrue(result.valid)
        self.assertEqual(result.status, ProfileValidationStatus.PASS)
        self.assertEqual(profile.environment_type, ExecutionEnvironmentType.HOST)
        self.assertEqual(profile.runtime_name, "generic")
        self.assertIsNone(profile.runtime_version)
        self.assertEqual(profile.target_os, TargetOS.ANY)
        self.assertEqual(profile.working_directory, ".")
        self.assertEqual(profile.allowed_commands, ())
        self.assertEqual(profile.environment_variables, {})
        self.assertEqual(profile.timeout_seconds, 30.0)
        self.assertEqual(profile.max_output_bytes, 1048576)
        self.assertFalse(profile.network_access)

    def test_custom_valid_profile(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="python-3.11-test",
            environment_type=ExecutionEnvironmentType.VIRTUALENV,
            runtime_name="python",
            runtime_version="3.11",
            target_os=TargetOS.LINUX,
            working_directory="src/backend",
            allowed_commands=("pytest", "ruff", "python"),
            environment_variables={"PYTHONPATH": "src", "DEBUG": "0"},
            timeout_seconds=60.0,
            max_output_bytes=2097152,
            network_access=False,
        )
        result = profile.validate()
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, ())
        data = profile.to_dict()
        self.assertEqual(data["profile_id"], "python-3.11-test")
        self.assertEqual(data["environment_type"], "VIRTUALENV")
        self.assertEqual(data["target_os"], "LINUX")
        self.assertEqual(data["allowed_commands"], ["pytest", "ruff", "python"])
        self.assertEqual(data["environment_variables"], {"PYTHONPATH": "src", "DEBUG": "0"})

    def test_list_inputs_converted_to_immutable_collections(self) -> None:
        commands = ["pytest", "python"]
        profile = ProjectExecutionProfile(
            profile_id="p1",
            allowed_commands=commands,  # type: ignore[arg-type]
        )
        self.assertIsInstance(profile.allowed_commands, tuple)
        self.assertEqual(profile.allowed_commands, ("pytest", "python"))

    def test_profile_id_required(self) -> None:
        profile = ProjectExecutionProfile(profile_id="   ")
        result = profile.validate()
        self.assertFalse(result.valid)
        self.assertIn("profile_id_required", result.errors)

    def test_runtime_name_required(self) -> None:
        profile = ProjectExecutionProfile(profile_id="p1", runtime_name="")
        result = profile.validate()
        self.assertFalse(result.valid)
        self.assertIn("runtime_name_required", result.errors)

    def test_unsafe_working_directory_rejected(self) -> None:
        for unsafe in ("/root", "../escape", "subdir/../../escape", "\\windows\\abs"):
            profile = ProjectExecutionProfile(profile_id="p1", working_directory=unsafe)
            result = profile.validate()
            self.assertFalse(result.valid, f"Expected {unsafe} to fail")
            self.assertIn("unsafe_working_directory", result.errors)

    def test_invalid_timeouts_and_output_limits(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p1",
            timeout_seconds=0.0,
            max_output_bytes=-10,
        )
        result = profile.validate()
        self.assertFalse(result.valid)
        self.assertIn("invalid_timeout_seconds", result.errors)
        self.assertIn("invalid_max_output_bytes", result.errors)

    def test_invalid_allowed_commands(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p1",
            allowed_commands=("pytest", "", "  "),
        )
        result = profile.validate()
        self.assertFalse(result.valid)
        self.assertIn("invalid_allowed_command", result.errors)

    def test_invalid_env_var_names(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p1",
            environment_variables={"INVALID-NAME": "val", "123NUM": "val", "VALID_KEY": "ok"},
        )
        result = profile.validate()
        self.assertFalse(result.valid)
        self.assertIn("invalid_env_var_name:INVALID-NAME", result.errors)
        self.assertIn("invalid_env_var_name:123NUM", result.errors)
        self.assertNotIn("invalid_env_var_name:VALID_KEY", result.errors)


if __name__ == "__main__":
    unittest.main()
