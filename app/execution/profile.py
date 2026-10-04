"""Domain models and contracts for project execution environments."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExecutionEnvironmentType(str, Enum):
    """Supported target environment classifications."""

    HOST = "HOST"
    VIRTUALENV = "VIRTUALENV"
    CONTAINER = "CONTAINER"
    CUSTOM = "CUSTOM"


class TargetOS(str, Enum):
    """Target operating system family."""

    ANY = "ANY"
    LINUX = "LINUX"
    WINDOWS = "WINDOWS"
    DARWIN = "DARWIN"


class ProfileValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class ProfileValidationResult:
    status: ProfileValidationStatus
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status == ProfileValidationStatus.PASS


@dataclass(frozen=True)
class ProjectExecutionProfile:
    """Declares the environment in which a project or task must execute."""

    profile_id: str
    environment_type: ExecutionEnvironmentType = ExecutionEnvironmentType.HOST
    runtime_name: str = "generic"
    runtime_version: str | None = None
    target_os: TargetOS = TargetOS.ANY
    working_directory: str = "."
    allowed_commands: tuple[str, ...] = ()
    environment_variables: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1048576  # 1 MB
    network_access: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.allowed_commands, list):
            object.__setattr__(self, "allowed_commands", tuple(self.allowed_commands))
        if isinstance(self.environment_variables, dict):
            object.__setattr__(
                self, "environment_variables", dict(self.environment_variables)
            )

    def validate(self) -> ProfileValidationResult:
        errors: list[str] = []

        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            errors.append("profile_id_required")

        if not isinstance(self.environment_type, ExecutionEnvironmentType):
            try:
                ExecutionEnvironmentType(self.environment_type)
            except (ValueError, TypeError):
                errors.append("invalid_environment_type")

        if not isinstance(self.runtime_name, str) or not self.runtime_name.strip():
            errors.append("runtime_name_required")

        if self.runtime_version is not None:
            if not isinstance(self.runtime_version, str) or not self.runtime_version.strip():
                errors.append("invalid_runtime_version")

        if not isinstance(self.target_os, TargetOS):
            try:
                TargetOS(self.target_os)
            except (ValueError, TypeError):
                errors.append("invalid_target_os")

        if not isinstance(self.working_directory, str) or not self.working_directory.strip():
            errors.append("working_directory_required")
        else:
            norm = self.working_directory.replace("\\", "/")
            if norm.startswith("/") or norm.startswith("..") or "/../" in norm:
                errors.append("unsafe_working_directory")

        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            errors.append("invalid_timeout_seconds")

        if not isinstance(self.max_output_bytes, int) or self.max_output_bytes <= 0:
            errors.append("invalid_max_output_bytes")

        if not isinstance(self.network_access, bool):
            errors.append("network_access_must_be_bool")

        if not isinstance(self.allowed_commands, tuple):
            errors.append("allowed_commands_must_be_tuple")
        else:
            for cmd in self.allowed_commands:
                if not isinstance(cmd, str) or not cmd.strip():
                    errors.append("invalid_allowed_command")
                    break

        if not isinstance(self.environment_variables, Mapping):
            errors.append("environment_variables_must_be_mapping")
        else:
            for key, val in self.environment_variables.items():
                if not isinstance(key, str) or not _ENV_VAR_NAME.match(key):
                    errors.append(f"invalid_env_var_name:{key}")
                if not isinstance(val, str):
                    errors.append(f"invalid_env_var_value:{key}")

        return ProfileValidationResult(
            ProfileValidationStatus.FAIL if errors else ProfileValidationStatus.PASS,
            tuple(errors),
        )

    def to_dict(self) -> dict[str, object]:
        """Deterministic serializable representation of the profile."""
        env_type = (
            self.environment_type.value
            if isinstance(self.environment_type, ExecutionEnvironmentType)
            else str(self.environment_type)
        )
        target_os = (
            self.target_os.value
            if isinstance(self.target_os, TargetOS)
            else str(self.target_os)
        )
        return {
            "profile_id": self.profile_id,
            "environment_type": env_type,
            "runtime_name": self.runtime_name,
            "runtime_version": self.runtime_version,
            "target_os": target_os,
            "working_directory": self.working_directory,
            "allowed_commands": list(self.allowed_commands),
            "environment_variables": dict(self.environment_variables),
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "network_access": self.network_access,
        }
