"""Test Verification Adapter translating test verification intent into execution and verification requests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest, ExecutionResult
from app.orchestrator.models import EventType
from app.tools.verification import (
    VerificationEvaluator,
    VerificationRequest,
    VerificationResult,
    VerificationType,
)


class TestFramework(str, Enum):
    """Supported test framework categories."""

    GENERIC = "generic"
    PYTEST = "pytest"
    UNITTEST = "unittest"
    CUSTOM = "custom"


@dataclass(frozen=True)
class TestVerificationIntent:
    """Declarative specification of a test verification check.

    Specifies what test command to run as an argv sequence without shell execution.
    """

    criterion_id: str
    command: tuple[str, ...]
    verification_id: str = field(default_factory=lambda: str(uuid4()))
    framework: TestFramework | str = TestFramework.GENERIC
    expected_exit_code: int = 0
    working_directory: str = "."
    timeout_seconds: float | None = None
    profile: ProjectExecutionProfile | None = None
    environment_variables: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)
    requirement_id: str = ""

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
        """Validate intent invariants: explicit criterion_id, non-empty command argv, safe paths."""
        errors: list[str] = []
        if not isinstance(self.criterion_id, str) or not self.criterion_id.strip():
            errors.append("criterion_id_required")
        if not isinstance(self.verification_id, str) or not self.verification_id.strip():
            errors.append("verification_id_required")

        if isinstance(self.command, str):
            errors.append("command_must_be_argv_tuple_not_string")
        elif not isinstance(self.command, tuple) or not self.command:
            errors.append("command_required")
        else:
            for part in self.command:
                if not isinstance(part, str) or not part.strip():
                    errors.append("invalid_command_part")
                    break

        if not isinstance(self.expected_exit_code, int):
            errors.append("invalid_expected_exit_code")

        if not isinstance(self.working_directory, str) or not self.working_directory.strip():
            errors.append("working_directory_required")
        else:
            norm = self.working_directory.replace("\\", "/")
            if norm.startswith("/") or norm.startswith("..") or "/../" in norm:
                errors.append("unsafe_working_directory")

        if self.timeout_seconds is not None:
            if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
                errors.append("invalid_timeout_seconds")

        return tuple(errors)


class TestVerificationAdapter:
    """Translates test verification intent into ExecutionRequest and VerificationRequest.

    Does NOT run subprocesses or make authorization decisions.
    All execution is delegated to ExecutionCoordinator; evaluation to VerificationEvaluator.
    """

    def __init__(
        self,
        evaluator: Optional[VerificationEvaluator] = None,
    ) -> None:
        self._evaluator = evaluator or VerificationEvaluator()

    @property
    def evaluator(self) -> VerificationEvaluator:
        """Access the underlying verification evaluator."""
        return self._evaluator

    def build_execution_request(self, intent: TestVerificationIntent) -> ExecutionRequest:
        """Transform a validated test verification intent into a declarative ExecutionRequest."""
        errors = intent.validate()
        if errors:
            raise ValueError(f"Invalid TestVerificationIntent: {', '.join(errors)}")

        framework_str = (
            intent.framework.value
            if hasattr(intent.framework, "value")
            else str(intent.framework)
        )
        metadata = {
            **intent.metadata,
            "verification_id": intent.verification_id,
            "criterion_id": intent.criterion_id,
            "requirement_id": intent.requirement_id,
            "test_framework": framework_str,
            "expected_exit_code": intent.expected_exit_code,
        }

        return ExecutionRequest(
            command=intent.command,
            working_directory=intent.working_directory,
            environment_variables=intent.environment_variables,
            timeout_seconds=intent.timeout_seconds,
            profile=intent.profile,
            metadata=metadata,
        )

    def build_verification_request(
        self,
        intent: TestVerificationIntent,
        execution_request_id: Optional[str] = None,
    ) -> VerificationRequest:
        """Create the paired VerificationRequest for objective evaluation against ExecutionResult."""
        errors = intent.validate()
        if errors:
            raise ValueError(f"Invalid TestVerificationIntent: {', '.join(errors)}")

        framework_str = (
            intent.framework.value
            if hasattr(intent.framework, "value")
            else str(intent.framework)
        )
        metadata = {
            **intent.metadata,
            "test_framework": framework_str,
            "requirement_id": intent.requirement_id,
        }

        return VerificationRequest(
            verification_id=intent.verification_id,
            criterion_id=intent.criterion_id,
            verification_type=VerificationType.COMMAND_EXECUTION,
            execution_request_id=execution_request_id,
            expected_exit_code=intent.expected_exit_code,
            metadata=metadata,
        )

    def evaluate_result(
        self,
        intent: TestVerificationIntent,
        execution_result: Optional[ExecutionResult],
        *,
        run_id: str = "",
        observer: Optional[Callable[[EventType, dict[str, object]], None]] = None,
    ) -> VerificationResult:
        """Evaluate an execution result using the existing VerificationEvaluator."""
        exec_req_id = execution_result.request_id if execution_result else None
        v_req = self.build_verification_request(intent, execution_request_id=exec_req_id)
        result = self._evaluator.evaluate_execution(
            v_req,
            execution_result,
            run_id=run_id,
            observer=observer,
        )
        return result

    @staticmethod
    def build_pytest_intent(
        criterion_id: str,
        *,
        test_args: tuple[str, ...] = (),
        python_executable: str = "pytest",
        verification_id: Optional[str] = None,
        expected_exit_code: int = 0,
        working_directory: str = ".",
        timeout_seconds: Optional[float] = None,
        profile: Optional[ProjectExecutionProfile] = None,
        environment_variables: Optional[Mapping[str, str]] = None,
        metadata: Optional[Mapping[str, object]] = None,
        requirement_id: str = "",
    ) -> TestVerificationIntent:
        """Helper to create a PYTEST test verification intent."""
        command = (python_executable, *test_args)
        return TestVerificationIntent(
            criterion_id=criterion_id,
            command=command,
            verification_id=verification_id or str(uuid4()),
            framework=TestFramework.PYTEST,
            expected_exit_code=expected_exit_code,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            profile=profile,
            environment_variables=environment_variables or {},
            metadata=metadata or {},
            requirement_id=requirement_id,
        )

    @staticmethod
    def build_unittest_intent(
        criterion_id: str,
        *,
        test_args: tuple[str, ...] = (),
        python_executable: str = "python",
        verification_id: Optional[str] = None,
        expected_exit_code: int = 0,
        working_directory: str = ".",
        timeout_seconds: Optional[float] = None,
        profile: Optional[ProjectExecutionProfile] = None,
        environment_variables: Optional[Mapping[str, str]] = None,
        metadata: Optional[Mapping[str, object]] = None,
        requirement_id: str = "",
    ) -> TestVerificationIntent:
        """Helper to create a UNITTEST test verification intent."""
        command = (python_executable, "-m", "unittest", *test_args)
        return TestVerificationIntent(
            criterion_id=criterion_id,
            command=command,
            verification_id=verification_id or str(uuid4()),
            framework=TestFramework.UNITTEST,
            expected_exit_code=expected_exit_code,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            profile=profile,
            environment_variables=environment_variables or {},
            metadata=metadata or {},
            requirement_id=requirement_id,
        )

    @staticmethod
    def build_generic_intent(
        criterion_id: str,
        command: tuple[str, ...],
        *,
        verification_id: Optional[str] = None,
        expected_exit_code: int = 0,
        working_directory: str = ".",
        timeout_seconds: Optional[float] = None,
        profile: Optional[ProjectExecutionProfile] = None,
        environment_variables: Optional[Mapping[str, str]] = None,
        metadata: Optional[Mapping[str, object]] = None,
        requirement_id: str = "",
    ) -> TestVerificationIntent:
        """Helper to create a GENERIC test verification intent with explicit command argv."""
        return TestVerificationIntent(
            criterion_id=criterion_id,
            command=command,
            verification_id=verification_id or str(uuid4()),
            framework=TestFramework.GENERIC,
            expected_exit_code=expected_exit_code,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            profile=profile,
            environment_variables=environment_variables or {},
            metadata=metadata or {},
            requirement_id=requirement_id,
        )
