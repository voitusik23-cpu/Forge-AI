"""Server-side execution declarations: the only source of host command authority.

An ``ExecutionDeclaration`` is an operator-owned statement of the form "this
Forge process allows exactly this command, under exactly these limits". It is
declared at composition time, next to the tool and command allowlists, and it is
never derived from a request, a request context, a task description, a skill, the
capability fabric, the tool registry, or an execution profile.

What this module deliberately is **not**:

* not a permission system - the existing ``RunScope``, ``PermissionPolicy``,
  ``ExecutionPolicy``, and ``ApprovalPolicy`` checks remain the only gates;
* not a tool - declarations have no ``definition``, no ``validate_input``, and
  never enter ``ToolRegistry``;
* not an authorization decision - a declaration carries one authority-bearing
  field (``command``) and otherwise only adds limits;
* not a place a client can reach - nothing here reads an HTTP request, a
  ``Task.context``, or a task description.

v0.1 is restricted to verification purpose. Producing a request is a pure
translation: no coordinator, no adapter, no process, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from app.execution.identity import CommandIdentity, executable_matches
from app.execution.paths import PathSecurityError, normalize_workspace_relative_path
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest

__all__ = [
    "ExecutionDeclarationError",
    "UnknownExecutionDeclarationError",
    "VERIFICATION_PURPOSE",
    "ExecutionDeclaration",
    "to_execution_request",
    "validate_declarations_against_profile",
]

# v0.1 supports exactly one purpose. Keeping this a closed string rather than an
# open enum avoids implying that wider purposes are merely unconfigured.
VERIFICATION_PURPOSE = "verification"

# Mirrors the profile's environment-name rule so a declaration cannot introduce a
# key shape the profile would reject later.
_ENV_VAR_NAME = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExecutionDeclarationError(ValueError):
    """A declaration is malformed, or disagrees with the active profile."""


class UnknownExecutionDeclarationError(LookupError):
    """A resolver named a declaration that the operator never declared.

    Raised rather than silently skipped so an unresolvable binding surfaces as an
    explicit ``execution_denied`` record instead of a quiet no-op.
    """


def _normalize_command(command: object) -> tuple[str, ...]:
    if isinstance(command, str):
        raise ExecutionDeclarationError(
            "command must be an argv tuple, not a string"
        )
    if not isinstance(command, (tuple, list)) or not command:
        raise ExecutionDeclarationError("command must be a non-empty argv sequence")
    parts: list[str] = []
    for part in command:
        if not isinstance(part, str) or not part.strip():
            raise ExecutionDeclarationError("command parts must be non-empty strings")
        parts.append(part)
    return tuple(parts)


def _normalize_environment(env: object) -> dict[str, str]:
    if env is None:
        return {}
    if not isinstance(env, Mapping):
        raise ExecutionDeclarationError("environment_variables must be a mapping")
    normalized: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_VAR_NAME.match(key):
            raise ExecutionDeclarationError(f"invalid environment variable name: {key!r}")
        if not isinstance(value, str):
            raise ExecutionDeclarationError(
                f"environment variable value must be a string: {key!r}"
            )
        normalized[key] = value
    return normalized


def _normalize_artifacts(targets: object) -> tuple[str, ...]:
    if targets is None:
        return ()
    if isinstance(targets, str) or not isinstance(targets, (tuple, list)):
        raise ExecutionDeclarationError("artifact_targets must be a sequence of paths")
    normalized: list[str] = []
    for target in targets:
        try:
            normalized.append(normalize_workspace_relative_path(target))
        except PathSecurityError as exc:
            raise ExecutionDeclarationError(
                f"unsafe artifact target: {target!r}"
            ) from exc
    return tuple(normalized)


def _normalize_metadata(metadata: object) -> dict[str, object]:
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise ExecutionDeclarationError("metadata must be a mapping")
    normalized: dict[str, object] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.strip():
            raise ExecutionDeclarationError("metadata keys must be non-empty strings")
        normalized[key] = value
    return normalized


@dataclass(frozen=True)
class ExecutionDeclaration:
    """An operator-declared, immutable permission to run one exact command.

    Only ``command`` carries execution authority. Every other field either
    identifies the declaration or narrows it further, and each is re-validated
    against the frozen ``RunScope`` and the execution profile before any token is
    minted.
    """

    declaration_id: str
    command: tuple[str, ...]
    purpose: str = VERIFICATION_PURPOSE
    working_directory: str = "."
    timeout_seconds: float | None = None
    expected_exit_code: int = 0
    profile_id: str = ""
    # Operator-declared intent: what this declared execution is meant to achieve.
    # It is the trusted goal the planning stage may work from. It is descriptive
    # text only and grants no authority; an empty intent simply means the run has
    # no planning goal.
    intent: str = ""
    environment_variables: Mapping[str, str] = field(default_factory=dict)
    artifact_targets: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.declaration_id, str) or not self.declaration_id.strip():
            raise ExecutionDeclarationError("declaration_id must be a non-empty string")
        object.__setattr__(self, "declaration_id", self.declaration_id.strip())

        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ExecutionDeclarationError("purpose must be a non-empty string")
        object.__setattr__(self, "purpose", self.purpose.strip())
        if self.purpose != VERIFICATION_PURPOSE:
            raise ExecutionDeclarationError(
                f"unsupported declaration purpose: {self.purpose!r}; "
                f"v0.1 supports only {VERIFICATION_PURPOSE!r}"
            )

        object.__setattr__(self, "command", _normalize_command(self.command))

        if not isinstance(self.intent, str):
            raise ExecutionDeclarationError("intent must be a string")
        object.__setattr__(self, "intent", self.intent.strip())

        if (
            not isinstance(self.working_directory, str)
            or not self.working_directory.strip()
        ):
            raise ExecutionDeclarationError("working_directory must be a non-empty string")
        try:
            object.__setattr__(
                self,
                "working_directory",
                normalize_workspace_relative_path(self.working_directory),
            )
        except PathSecurityError as exc:
            raise ExecutionDeclarationError(
                f"unsafe working_directory: {self.working_directory!r}"
            ) from exc

        if self.timeout_seconds is not None:
            if (
                isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (int, float))
                or self.timeout_seconds <= 0
            ):
                raise ExecutionDeclarationError("timeout_seconds must be positive")

        if isinstance(self.expected_exit_code, bool) or not isinstance(
            self.expected_exit_code, int
        ):
            raise ExecutionDeclarationError("expected_exit_code must be an int")
        if not 0 <= self.expected_exit_code <= 255:
            raise ExecutionDeclarationError(
                "expected_exit_code must be a conventional process exit code (0-255)"
            )

        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ExecutionDeclarationError("profile_id must be a non-empty string")
        object.__setattr__(self, "profile_id", self.profile_id.strip())

        object.__setattr__(
            self, "environment_variables", _normalize_environment(self.environment_variables)
        )
        object.__setattr__(self, "artifact_targets", _normalize_artifacts(self.artifact_targets))
        object.__setattr__(self, "metadata", _normalize_metadata(self.metadata))

    @property
    def executable(self) -> str:
        """The command's executable, which is the entry that carries authority."""
        return self.command[0]


def validate_declarations_against_profile(
    declarations: Mapping[str, ExecutionDeclaration],
    profile: ProjectExecutionProfile,
) -> None:
    """Fail closed when a declaration disagrees with the active execution profile.

    This is configuration consistency, not authorization: the profile is the
    ceiling, and a declaration that the ceiling cannot admit is a deployment
    error that must surface at composition time rather than as a runtime denial.
    """
    if not isinstance(profile, ProjectExecutionProfile):
        raise ExecutionDeclarationError("profile must be a ProjectExecutionProfile")

    profile_validation = profile.validate()
    if not profile_validation.valid:
        raise ExecutionDeclarationError(
            "execution profile is invalid: " + ", ".join(profile_validation.errors)
        )

    for declaration_id, declaration in declarations.items():
        if not isinstance(declaration, ExecutionDeclaration):
            raise ExecutionDeclarationError(
                f"declaration {declaration_id!r} is not an ExecutionDeclaration"
            )
        if declaration.declaration_id != declaration_id:
            raise ExecutionDeclarationError(
                f"declaration key {declaration_id!r} does not match its id "
                f"{declaration.declaration_id!r}"
            )

        if declaration.profile_id != profile.profile_id:
            raise ExecutionDeclarationError(
                f"declaration {declaration_id!r} targets profile "
                f"{declaration.profile_id!r} but the active profile is "
                f"{profile.profile_id!r}"
            )

        # The command must be admitted by the profile ceiling as a full identity,
        # so a pinned argv in the profile cannot be swapped for another argv.
        admitted = False
        for allowed in profile.allowed_commands:
            if isinstance(allowed, CommandIdentity):
                if executable_matches(
                    declaration.executable, allowed.executable
                ) and tuple(declaration.command[1:]) == tuple(allowed.argv):
                    admitted = True
                    break
            elif isinstance(allowed, str) and executable_matches(
                declaration.executable, allowed
            ):
                admitted = True
                break
        if not admitted:
            raise ExecutionDeclarationError(
                f"declaration {declaration_id!r} command executable "
                f"{declaration.executable!r} is not allowed by the execution profile"
            )

        if declaration.working_directory != profile.working_directory:
            raise ExecutionDeclarationError(
                f"declaration {declaration_id!r} working_directory "
                f"{declaration.working_directory!r} differs from the profile's "
                f"{profile.working_directory!r}"
            )

        for key, value in declaration.environment_variables.items():
            if key not in profile.environment_variables:
                raise ExecutionDeclarationError(
                    f"declaration {declaration_id!r} declares environment variable "
                    f"{key!r} that the profile does not authorize"
                )
            if profile.environment_variables[key] != value:
                raise ExecutionDeclarationError(
                    f"declaration {declaration_id!r} changes the value of environment "
                    f"variable {key!r}"
                )

        if (
            declaration.timeout_seconds is not None
            and declaration.timeout_seconds > profile.timeout_seconds
        ):
            raise ExecutionDeclarationError(
                f"declaration {declaration_id!r} timeout "
                f"{declaration.timeout_seconds} exceeds the profile limit "
                f"{profile.timeout_seconds}"
            )


def to_execution_request(
    declaration: ExecutionDeclaration,
    *,
    profile: ProjectExecutionProfile,
    run_id: str,
) -> ExecutionRequest:
    """Translate a trusted declaration into a declarative execution request.

    Pure function: it does not authorize, does not consult a coordinator or
    adapter, does not spawn anything, and reads no request or task context. The
    profile is supplied by the caller from the frozen run scope, never from a
    declaration and never from user input.

    ``approval_required`` is intentionally left at its default: approval stays a
    server-side policy decision and a declaration must not be able to weaken it.
    """
    if not isinstance(declaration, ExecutionDeclaration):
        raise ExecutionDeclarationError("declaration must be an ExecutionDeclaration")
    if not isinstance(profile, ProjectExecutionProfile):
        raise ExecutionDeclarationError("profile must be a ProjectExecutionProfile")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ExecutionDeclarationError("run_id must be a non-empty string")

    metadata: dict[str, object] = {
        "declaration_id": declaration.declaration_id,
        "purpose": declaration.purpose,
        "expected_exit_code": declaration.expected_exit_code,
        "run_id": run_id.strip(),
    }
    for key, value in declaration.metadata.items():
        # Declaration metadata is operator-authored, but the execution event
        # sanitizer still filters forbidden keys by name; skip exact collisions
        # with the canonical fields so a declaration cannot rewrite them.
        if key in metadata:
            continue
        metadata[key] = value

    return ExecutionRequest(
        command=tuple(declaration.command),
        working_directory=declaration.working_directory,
        environment_variables=dict(declaration.environment_variables),
        timeout_seconds=declaration.timeout_seconds,
        profile=profile,
        artifact_targets=tuple(declaration.artifact_targets),
        metadata=metadata,
    )
