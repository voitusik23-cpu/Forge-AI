"""Domain models for execution requests and execution outcomes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from app.execution.profile import ProjectExecutionProfile


class ExecutionStatus(str, Enum):
    """Execution outcome status."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    DENIED = "DENIED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ExecutionRequest:
    """Declared command action intended for execution within an execution profile."""

    command: tuple[str, ...]
    request_id: str = field(default_factory=lambda: str(uuid4()))
    working_directory: str = "."
    environment_variables: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    profile: ProjectExecutionProfile | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.command, list):
            object.__setattr__(self, "command", tuple(self.command))
        if isinstance(self.environment_variables, dict):
            object.__setattr__(
                self, "environment_variables", dict(self.environment_variables)
            )
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    def validate(self) -> tuple[str, ...]:
        """Validate request against domain invariants and optional profile constraints."""
        errors: list[str] = []

        if not isinstance(self.request_id, str) or not self.request_id.strip():
            errors.append("request_id_required")

        if not isinstance(self.command, tuple) or not self.command:
            errors.append("command_required")
        else:
            for part in self.command:
                if not isinstance(part, str) or not part.strip():
                    errors.append("invalid_command_part")
                    break

        if not isinstance(self.working_directory, str) or not self.working_directory.strip():
            errors.append("working_directory_required")
        else:
            norm = self.working_directory.replace("\\", "/")
            if norm.startswith("/") or norm.startswith("..") or "/../" in norm:
                errors.append("unsafe_working_directory")

        if self.timeout_seconds is not None:
            if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
                errors.append("invalid_timeout_seconds")

        if self.profile is not None:
            profile_validation = self.profile.validate()
            if not profile_validation.valid:
                errors.extend(f"profile_error:{err}" for err in profile_validation.errors)
            elif self.profile.allowed_commands and self.command:
                executable = self.command[0]
                if executable not in self.profile.allowed_commands:
                    errors.append(f"command_not_allowed:{executable}")

        return tuple(errors)


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of an execution request."""

    request_id: str
    status: ExecutionStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    truncated: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def success(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS and self.exit_code == 0

    @property
    def timed_out(self) -> bool:
        return self.status == ExecutionStatus.TIMEOUT
