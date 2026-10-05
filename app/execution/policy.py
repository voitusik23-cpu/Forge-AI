"""Execution policy boundary for validating and authorizing ExecutionIntents."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.execution.identity import CommandIdentity, executable_matches
from app.execution.intent import ExecutionIntent, IntentBuilder
from app.execution.paths import PathSecurityError, normalize_workspace_relative_path
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest

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
    granted capabilities, and valid profiles.
    """

    def evaluate(
        self,
        intent: ExecutionIntent | ExecutionRequest | None,
        profile: ProjectExecutionProfile | None = None,
    ) -> ExecutionPolicyDecision:
        """Evaluate an ExecutionIntent or ExecutionRequest against policy invariants."""
        if intent is None:
            return ExecutionPolicyDecision(allowed=False, reason="request_missing_or_invalid")

        # Compatibility layer: convert ExecutionRequest to ExecutionIntent
        if isinstance(intent, ExecutionRequest):
            if not intent.command or not isinstance(intent.command, tuple):
                return ExecutionPolicyDecision(allowed=False, reason="command_empty_or_invalid")
            for part in intent.command:
                if not isinstance(part, str) or not part.strip():
                    return ExecutionPolicyDecision(allowed=False, reason="invalid_command_part")

            req_profile = profile or intent.profile
            if req_profile is None or not isinstance(req_profile, ProjectExecutionProfile):
                return ExecutionPolicyDecision(allowed=False, reason="profile_missing_or_invalid")

            try:
                normalize_workspace_relative_path(intent.working_directory)
            except PathSecurityError:
                return ExecutionPolicyDecision(allowed=False, reason="unsafe_working_directory")

            try:
                real_intent = IntentBuilder.from_request(intent, req_profile).build()
            except PathSecurityError as exc:
                if "working" in str(exc).lower() or "relative" in str(exc).lower():
                    return ExecutionPolicyDecision(allowed=False, reason="unsafe_working_directory")
                return ExecutionPolicyDecision(allowed=False, reason=f"path_security_error:{exc}")
            except Exception as exc:
                return ExecutionPolicyDecision(allowed=False, reason=f"intent_construction_failed:{exc}")
            return self.evaluate(real_intent, req_profile)

        if not isinstance(intent, ExecutionIntent):
            return ExecutionPolicyDecision(allowed=False, reason="request_missing_or_invalid")

        if profile is None or not isinstance(profile, ProjectExecutionProfile):
            return ExecutionPolicyDecision(allowed=False, reason="profile_missing_or_invalid")

        profile_val = profile.validate()
        if not profile_val.valid:
            return ExecutionPolicyDecision(
                allowed=False,
                reason=f"profile_invalid:{', '.join(profile_val.errors)}",
            )

        # Default deny: profile allowed_commands must be non-empty
        if not profile.allowed_commands:
            return ExecutionPolicyDecision(allowed=False, reason="no_commands_allowed_by_profile")

        executable = intent.executable
        argv = intent.argv

        # Check explicit CommandIdentity match first (Invariant I5)
        matched_explicit_identity = False
        for allowed in profile.allowed_commands:
            if isinstance(allowed, CommandIdentity):
                if executable_matches(executable, allowed.executable) and argv == allowed.argv:
                    matched_explicit_identity = True
                    break

        if not matched_explicit_identity:
            # Check bare-string allowed commands
            matched_bare_executable = False
            for allowed in profile.allowed_commands:
                if isinstance(allowed, str) and executable_matches(executable, allowed):
                    matched_bare_executable = True
                    break

            if not matched_bare_executable:
                return ExecutionPolicyDecision(
                    allowed=False,
                    reason=f"command_not_allowed:{executable}",
                )

            # Bare-string match: check that all required capabilities are granted by profile
            for cap in intent.capabilities:
                if cap not in profile.capabilities:
                    return ExecutionPolicyDecision(
                        allowed=False,
                        reason="argv_not_authorized",
                    )

        # Check forbidden flags
        exec_name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
        exec_stem = exec_name.rsplit(".", 1)[0]
        if exec_stem == "git":
            for part in argv:
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
        try:
            normalize_workspace_relative_path(intent.working_directory)
        except PathSecurityError:
            return ExecutionPolicyDecision(allowed=False, reason="unsafe_working_directory")

        # Check artifact targets safety
        for target in intent.artifact_targets:
            try:
                normalize_workspace_relative_path(target)
            except PathSecurityError:
                return ExecutionPolicyDecision(allowed=False, reason="unsafe_artifact_target")

        # Check timeout bounds
        if intent.timeout_seconds <= 0:
            return ExecutionPolicyDecision(allowed=False, reason="invalid_timeout_seconds")
        if intent.timeout_seconds > profile.timeout_seconds:
            return ExecutionPolicyDecision(
                allowed=False,
                reason=f"timeout_exceeds_profile_limit:{intent.timeout_seconds}>{profile.timeout_seconds}",
            )

        # Check max output bytes bounds
        if intent.max_output_bytes <= 0:
            return ExecutionPolicyDecision(allowed=False, reason="invalid_max_output_bytes")
        if intent.max_output_bytes > profile.max_output_bytes:
            return ExecutionPolicyDecision(
                allowed=False,
                reason=f"max_output_bytes_exceeds_profile_limit:{intent.max_output_bytes}>{profile.max_output_bytes}",
            )

        # Check environment variable names
        for key, val in intent.environment_variables:
            if not isinstance(key, str) or not _ENV_VAR_NAME.match(key):
                return ExecutionPolicyDecision(allowed=False, reason=f"invalid_env_var_name:{key}")
            if not isinstance(val, str):
                return ExecutionPolicyDecision(allowed=False, reason=f"invalid_env_var_value:{key}")

        return ExecutionPolicyDecision(allowed=True, reason="authorized")

    @staticmethod
    def _is_command_allowed(executable: str, allowed_commands: tuple[object, ...]) -> bool:
        from app.execution.identity import command_is_allowed
        return command_is_allowed((executable,), allowed_commands)
