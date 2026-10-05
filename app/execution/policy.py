"""Execution policy boundary for validating and authorizing ExecutionRequests."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest
from app.execution.identity import command_identity, command_is_allowed, dangerous_interpreter_argv


_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ExecutionPolicyDecision:
    """Decision produced by an ExecutionPolicy evaluation."""

    allowed: bool
    reason: str = ""


class ExecutionPolicy:
    """Deterministic security and operational policy boundary.

    Enforces default-deny on command execution: only commands explicitly allowed
    by the ProjectExecutionProfile may run, within bounded timeouts, safe paths,
    and valid profiles.
    """

    def evaluate(self, request: ExecutionRequest | None) -> ExecutionPolicyDecision:
        """Evaluate an ExecutionRequest against policy invariants and profile constraints."""
        if request is None or not isinstance(request, ExecutionRequest):
            return ExecutionPolicyDecision(allowed=False, reason="request_missing_or_invalid")

        if not request.command or not isinstance(request.command, tuple):
            return ExecutionPolicyDecision(allowed=False, reason="command_empty_or_invalid")

        for part in request.command:
            if not isinstance(part, str) or not part.strip():
                return ExecutionPolicyDecision(allowed=False, reason="invalid_command_part")

        profile = request.profile
        if profile is None or not isinstance(profile, ProjectExecutionProfile):
            return ExecutionPolicyDecision(allowed=False, reason="profile_missing_or_invalid")

        profile_val = profile.validate()
        if not profile_val.valid:
            return ExecutionPolicyDecision(
                allowed=False,
                reason=f"profile_invalid:{', '.join(profile_val.errors)}",
            )

        # Default deny: allowed_commands must be non-empty and must include the executable
        if not profile.allowed_commands:
            return ExecutionPolicyDecision(allowed=False, reason="no_commands_allowed_by_profile")

        identity = command_identity(request.command)
        if not command_is_allowed(request.command, profile.allowed_commands):
            return ExecutionPolicyDecision(
                allowed=False,
                reason=f"command_not_allowed:{request.command[0]}",
            )

        # Interpreter evaluation flags require a full CommandIdentity entry,
        # preventing an executable-only allowlist from authorizing arbitrary code.
        if dangerous_interpreter_argv(identity) and \
                not command_is_allowed(request.command, profile.allowed_commands, exact_argv=True):
            return ExecutionPolicyDecision(allowed=False, reason="argv_not_authorized")

        # Check command-specific forbidden flags
        exec_name = identity.executable.rsplit("/", 1)[-1].lower()
        exec_stem = exec_name.rsplit(".", 1)[0]
        if exec_name == "git" or exec_stem == "git":
            for part in request.command[1:]:
                clean_part = part.strip().lower()
                if clean_part == "--git-dir" or clean_part.startswith("--git-dir="):
                    return ExecutionPolicyDecision(
                        allowed=False,
                        reason="forbidden_flag:--git-dir",
                    )
                if clean_part == "--work-tree" or clean_part.startswith("--work-tree="):
                    return ExecutionPolicyDecision(
                        allowed=False,
                        reason="forbidden_flag:--work-tree",
                    )

        # Check working directory safety
        if not isinstance(request.working_directory, str) or not request.working_directory.strip():
            return ExecutionPolicyDecision(allowed=False, reason="working_directory_required")

        norm_cwd = request.working_directory.replace("\\", "/")
        if norm_cwd.startswith("/") or norm_cwd.startswith("..") or "/../" in norm_cwd:
            return ExecutionPolicyDecision(allowed=False, reason="unsafe_working_directory")

        # Check timeout bounds
        if request.timeout_seconds is not None:
            if not isinstance(request.timeout_seconds, (int, float)) or request.timeout_seconds <= 0:
                return ExecutionPolicyDecision(allowed=False, reason="invalid_timeout_seconds")
            if request.timeout_seconds > profile.timeout_seconds:
                return ExecutionPolicyDecision(
                    allowed=False,
                    reason=f"timeout_exceeds_profile_limit:{request.timeout_seconds}>{profile.timeout_seconds}",
                )

        # Check environment variable names
        for key, val in request.environment_variables.items():
            if not isinstance(key, str) or not _ENV_VAR_NAME.match(key):
                return ExecutionPolicyDecision(allowed=False, reason=f"invalid_env_var_name:{key}")
            if not isinstance(val, str):
                return ExecutionPolicyDecision(allowed=False, reason=f"invalid_env_var_value:{key}")

        return ExecutionPolicyDecision(allowed=True, reason="authorized")

    @staticmethod
    def _is_command_allowed(executable: str, allowed_commands: tuple[str, ...]) -> bool:
        """Compatibility wrapper over the authoritative identity matcher."""
        return command_is_allowed((executable,), allowed_commands)
