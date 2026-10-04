"""Contracts and domain models for execution profile and execution plane."""

from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.policy import (
    ExecutionPolicy,
    ExecutionPolicyDecision,
)
from app.execution.profile import (
    ExecutionEnvironmentType,
    ProfileValidationResult,
    ProfileValidationStatus,
    ProjectExecutionProfile,
    TargetOS,
)
from app.execution.redaction import (
    DefaultSecretRedactor,
    REDACTED_PLACEHOLDER,
    SecretRedactor,
)
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)

__all__ = [
    "DefaultSecretRedactor",
    "ExecutionCoordinator",
    "ExecutionEnvironmentType",
    "ExecutionOutcomeStatus",
    "ExecutionPolicy",
    "ExecutionPolicyDecision",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "LocalExecutionAdapter",
    "ProfileValidationResult",
    "ProfileValidationStatus",
    "ProjectExecutionProfile",
    "REDACTED_PLACEHOLDER",
    "SecretRedactor",
    "TargetOS",
]
