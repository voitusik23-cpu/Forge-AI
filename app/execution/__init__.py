"""Contracts and domain models for execution profile and execution plane."""

from app.execution.adapter import (
    ExecutionBackend,
    LocalExecutionAdapter,
    LocalProcessExecutionBackend,
)
from app.execution.artifacts import (
    DEFAULT_MAX_ARTIFACT_BYTES,
    Artifact,
    create_artifact_from_file,
)
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
from app.execution.workspace_manager import EphemeralWorkspaceManager

__all__ = [
    "Artifact",
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "DefaultSecretRedactor",
    "EphemeralWorkspaceManager",
    "ExecutionBackend",
    "ExecutionCoordinator",
    "ExecutionEnvironmentType",
    "ExecutionOutcomeStatus",
    "ExecutionPolicy",
    "ExecutionPolicyDecision",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "LocalExecutionAdapter",
    "LocalProcessExecutionBackend",
    "ProfileValidationResult",
    "ProfileValidationStatus",
    "ProjectExecutionProfile",
    "REDACTED_PLACEHOLDER",
    "SecretRedactor",
    "TargetOS",
    "create_artifact_from_file",
]
