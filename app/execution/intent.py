"""Immutable execution intent value, intent builder, and authorized execution token."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.execution.capabilities import ExecutionCapability, classify_invocation
from app.execution.paths import (
    PathSecurityError,
    canonical_executable,
    normalize_workspace_relative_path,
)

_COORDINATOR_SENTINEL = object()


@dataclass(frozen=True)
class ExecutionIntent:
    """Immutable execution value object containing all parameters of an authorized run."""

    executable: str
    argv: tuple[str, ...]
    working_directory: str
    environment_variables: tuple[tuple[str, str], ...]
    timeout_seconds: float
    max_output_bytes: int
    artifact_targets: tuple[str, ...]
    profile_id: str
    network_access: bool
    capabilities: frozenset[ExecutionCapability]
    profile_environment_variables: tuple[tuple[str, str], ...] = ()

    @property
    def fingerprint(self) -> str:
        """Deterministic canonical fingerprint of this complete intent."""
        payload = {
            "artifact_targets": list(self.artifact_targets),
            "argv": list(self.argv),
            "capabilities": sorted(
                c.value if hasattr(c, "value") else str(c) for c in self.capabilities
            ),
            "environment_variables": [[k, v] for k, v in self.environment_variables],
            "executable": self.executable,
            "max_output_bytes": self.max_output_bytes,
            "network_access": self.network_access,
            "profile_environment_variables": [
                [k, v] for k, v in self.profile_environment_variables
            ],
            "profile_id": self.profile_id,
            "timeout_seconds": self.timeout_seconds,
            "working_directory": self.working_directory,
        }
        serialized = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "executable": self.executable,
            "argv": list(self.argv),
            "working_directory": self.working_directory,
            "environment_variables": dict(self.environment_variables),
            "profile_environment_variables": dict(self.profile_environment_variables),
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "artifact_targets": list(self.artifact_targets),
            "profile_id": self.profile_id,
            "network_access": self.network_access,
            "capabilities": sorted(
                c.value if hasattr(c, "value") else str(c) for c in self.capabilities
            ),
            "fingerprint": self.fingerprint,
        }


IntentBody = ExecutionIntent


class IntentBuilder:
    """The authoritative builder constructing validated ExecutionIntents."""

    def __init__(self) -> None:
        self._executable: str | None = None
        self._argv: tuple[str, ...] = ()
        self._working_directory: str = "."
        self._environment_variables: dict[str, str] = {}
        self._profile_environment_variables: dict[str, str] = {}
        self._timeout_seconds: float = 30.0
        self._max_output_bytes: int = 1048576
        self._artifact_targets: set[str] = set()
        self._profile_id: str = ""
        self._network_access: bool = False
        self._capabilities: set[ExecutionCapability] | None = None

    def with_command(self, command: Sequence[str]) -> IntentBuilder:
        if not command:
            raise ValueError("command must not be empty")
        for part in command:
            if not isinstance(part, str) or not part.strip():
                raise ValueError("command parts must be non-empty strings")
        self._executable = canonical_executable(command[0])
        self._argv = tuple(str(a) for a in command[1:])
        return self

    def with_working_directory(self, working_directory: str) -> IntentBuilder:
        self._working_directory = normalize_workspace_relative_path(working_directory)
        return self

    def with_environment_variables(
        self, env_vars: Mapping[str, str]
    ) -> IntentBuilder:
        self._environment_variables = {str(k): str(v) for k, v in env_vars.items()}
        return self

    def with_profile_environment_variables(
        self, env_vars: Mapping[str, str]
    ) -> IntentBuilder:
        self._profile_environment_variables = {str(k): str(v) for k, v in env_vars.items()}
        return self

    def with_timeout_seconds(self, timeout_seconds: float) -> IntentBuilder:
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError(f"invalid timeout_seconds: {timeout_seconds}")
        self._timeout_seconds = float(timeout_seconds)
        return self

    def with_max_output_bytes(self, max_output_bytes: int) -> IntentBuilder:
        if not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
            raise ValueError(f"invalid max_output_bytes: {max_output_bytes}")
        self._max_output_bytes = max_output_bytes
        return self

    def with_artifact_targets(
        self, artifact_targets: Sequence[str]
    ) -> IntentBuilder:
        normalized_targets: set[str] = set()
        for target in artifact_targets:
            normalized_targets.add(normalize_workspace_relative_path(target))
        self._artifact_targets = normalized_targets
        return self

    def with_profile_id(self, profile_id: str) -> IntentBuilder:
        self._profile_id = str(profile_id)
        return self

    def with_network_access(self, network_access: bool) -> IntentBuilder:
        self._network_access = bool(network_access)
        return self

    def with_capabilities(
        self, capabilities: frozenset[ExecutionCapability]
    ) -> IntentBuilder:
        self._capabilities = set(capabilities)
        return self

    def build(self) -> ExecutionIntent:
        if self._executable is None:
            raise ValueError("executable is required to build ExecutionIntent")

        # Classify required capabilities if not explicitly overridden
        if self._capabilities is None:
            detected_caps = classify_invocation(self._executable, self._argv)
        else:
            detected_caps = frozenset(self._capabilities)

        sorted_env = tuple(sorted(self._environment_variables.items()))
        sorted_prof_env = tuple(sorted(self._profile_environment_variables.items()))
        sorted_artifacts = tuple(sorted(self._artifact_targets))

        return ExecutionIntent(
            executable=self._executable,
            argv=self._argv,
            working_directory=self._working_directory,
            environment_variables=sorted_env,
            timeout_seconds=self._timeout_seconds,
            max_output_bytes=self._max_output_bytes,
            artifact_targets=sorted_artifacts,
            profile_id=self._profile_id,
            network_access=self._network_access,
            capabilities=detected_caps,
            profile_environment_variables=sorted_prof_env,
        )

    @classmethod
    def from_request(
        cls,
        request: object,
        profile: object | None = None,
    ) -> IntentBuilder:
        builder = cls()
        command = getattr(request, "command", ())
        if command:
            builder.with_command(command)

        cwd = getattr(request, "working_directory", ".")
        builder.with_working_directory(cwd)

        prof = profile or getattr(request, "profile", None)

        env = getattr(request, "environment_variables", None)
        if env:
            builder.with_environment_variables(env)

        if prof is not None and getattr(prof, "environment_variables", None):
            builder.with_profile_environment_variables(prof.environment_variables)

        req_timeout = getattr(request, "timeout_seconds", None)

        if req_timeout is not None:
            builder.with_timeout_seconds(req_timeout)
        elif prof is not None and getattr(prof, "timeout_seconds", None) is not None:
            builder.with_timeout_seconds(prof.timeout_seconds)

        if prof is not None and getattr(prof, "max_output_bytes", None) is not None:
            builder.with_max_output_bytes(prof.max_output_bytes)

        artifacts = getattr(request, "artifact_targets", ())
        if artifacts:
            builder.with_artifact_targets(artifacts)

        if prof is not None:
            builder.with_profile_id(getattr(prof, "profile_id", ""))
            builder.with_network_access(getattr(prof, "network_access", False))

        return builder


@dataclass(frozen=True)
class AuthorizedExecution:
    """Internal authority marker that authorizes the backend to spawn a process.

    Can ONLY be produced by ExecutionCoordinator following complete authorization.
    The ``_intent_fingerprint`` field is bound at creation time so that any
    post-creation mutation of the intent (e.g. via ``dataclasses.replace``) is
    detected by ``is_valid()``.
    """

    intent: ExecutionIntent
    workspace_root: Path
    run_id: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    _token: object = field(default=None, repr=False)
    _intent_fingerprint: str = field(default="", repr=False)

    @property
    def request_id(self) -> str:
        return str(self.metadata.get("request_id") or self.run_id or "authorized")

    @property
    def command(self) -> tuple[str, ...]:
        return (self.intent.executable,) + self.intent.argv

    @classmethod
    def create(
        cls,
        intent: ExecutionIntent,
        workspace_root: Path,
        *,
        run_id: str = "",
        metadata: dict[str, object] | None = None,
        coordinator_token: object = None,
    ) -> AuthorizedExecution:
        if coordinator_token is not _COORDINATOR_SENTINEL:
            raise PermissionError("AuthorizedExecution can only be created by ExecutionCoordinator")
        if not isinstance(intent, ExecutionIntent):
            raise TypeError("intent must be an ExecutionIntent")
        if not isinstance(workspace_root, Path) or not workspace_root.is_absolute():
            raise ValueError("workspace_root must be an absolute Path")
        # Capture fingerprint before constructing – this binds the token to the
        # exact intent so that any post-creation swap is detected in is_valid().
        fingerprint = intent.fingerprint
        instance = cls(
            intent=intent,
            workspace_root=workspace_root.resolve(),
            run_id=run_id,
            metadata=dict(metadata or {}),
            _token=_COORDINATOR_SENTINEL,
            _intent_fingerprint=fingerprint,
        )
        return instance

    def is_valid(self) -> bool:
        """Check if this authorized execution token was legitimately created.

        Verifies:
        - token is the internal coordinator sentinel
        - intent and workspace_root are the correct types
        - workspace_root is absolute (prevents relative-path bypass)
        - stored fingerprint still matches the current intent fingerprint
          (detects post-creation mutation via dataclasses.replace or similar)
        """
        return (
            self._token is _COORDINATOR_SENTINEL
            and isinstance(self.intent, ExecutionIntent)
            and isinstance(self.workspace_root, Path)
            and self.workspace_root.is_absolute()
            and bool(self._intent_fingerprint)
            and self._intent_fingerprint == self.intent.fingerprint
        )
