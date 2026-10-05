"""Immutable RunScope: the security perimeter of one run.

The trust boundary this module enforces is:

    External content is DATA, never AUTHORITY.

A ``RunScope`` captures the authority-relevant inputs of a run once, before any
external or untrusted content is read, and then makes later expansion
detectable. It is deliberately not a new permission, approval, or execution
system: it reuses the existing immutable contracts (``Workspace``,
``ProjectExecutionProfile``, ``AcceptanceCriterion``) and the existing
default-deny policies, and only adds the missing pieces:

1. one object that binds those inputs together and validates that they agree;
2. a canonical fingerprint so a scope cannot be swapped for a wider one;
3. a fail-closed freeze registry that rejects a second, different scope for the
   same run;
4. request validation that rejects any request trying to expand the frozen set.

Fields intentionally NOT included (and why):

- repository id / target branch / base commit: no repository, branch, or commit
  concept exists in this codebase yet. Storing unverifiable strings would add
  fields without adding enforcement. They belong to the Git/GitHub integration
  block, which must extend this scope rather than invent a parallel one.
- approval state, verification results, project state, memory, and knowledge:
  these legitimately change during a run. RunScope fixes the perimeter, not the
  runtime state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from app.execution.identity import command_is_allowed
from app.execution.profile import ProjectExecutionProfile
from app.tools.acceptance import AcceptanceCriterion
from app.tools.workspace import Workspace

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.execution.request import ExecutionRequest


class RunScopeError(ValueError):
    """Raised when a run scope is invalid or an authority expansion is attempted."""


def _normalize_frozen_set(
    value: object, *, field_name: str, allow_none: bool = False
) -> frozenset[str]:
    """Return an immutable, validated set of non-empty strings."""
    if value is None and allow_none:
        return frozenset()
    if isinstance(value, str):
        raise RunScopeError(f"{field_name} must be a collection, not a string")
    if not isinstance(value, (set, frozenset, list, tuple)):
        raise RunScopeError(f"{field_name} must be a set, list, or tuple")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RunScopeError(f"{field_name} entries must be non-empty strings")
        items.append(item.strip())
    return frozenset(items)


@dataclass(frozen=True)
class RunScope:
    """The immutable security perimeter of a single run.

    The scope is created before any external or untrusted content is read and
    must not be replaced or widened afterwards.
    """

    run_id: str
    workspace: Workspace
    execution_profile: ProjectExecutionProfile
    allowed_tool_ids: frozenset[str] = frozenset()
    allowed_execution_commands: frozenset[str] = frozenset()
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()

    # Process-local freeze registry. This is an accident/regression guard, not a
    # defence against a hostile in-process adversary; see the module docstring.
    _FROZEN: ClassVar[dict[str, str]] = {}

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise RunScopeError("run_id must be a non-empty string")
        object.__setattr__(self, "run_id", self.run_id.strip())

        if not isinstance(self.workspace, Workspace):
            raise RunScopeError("workspace must be a Workspace contract")
        if not isinstance(self.execution_profile, ProjectExecutionProfile):
            raise RunScopeError("execution_profile must be a ProjectExecutionProfile")

        profile_validation = self.execution_profile.validate()
        if not profile_validation.valid:
            raise RunScopeError(
                "execution_profile is invalid: " + ", ".join(profile_validation.errors)
            )
        if not self.execution_profile.allowed_commands:
            raise RunScopeError(
                "execution_profile must declare at least one allowed command"
            )

        object.__setattr__(
            self,
            "allowed_tool_ids",
            _normalize_frozen_set(
                self.allowed_tool_ids, field_name="allowed_tool_ids", allow_none=True
            ),
        )
        object.__setattr__(
            self,
            "allowed_execution_commands",
            _normalize_frozen_set(
                self.allowed_execution_commands,
                field_name="allowed_execution_commands",
                allow_none=True,
            ),
        )

        criteria = self.acceptance_criteria
        if isinstance(criteria, list):
            criteria = tuple(criteria)
        if not isinstance(criteria, tuple):
            raise RunScopeError("acceptance_criteria must be a tuple")
        if not criteria:
            raise RunScopeError("acceptance_criteria must not be empty")
        for criterion in criteria:
            if not isinstance(criterion, AcceptanceCriterion):
                raise RunScopeError(
                    "acceptance_criteria entries must be AcceptanceCriterion values"
                )
            if not isinstance(criterion.criterion_id, str) or not criterion.criterion_id.strip():
                raise RunScopeError("acceptance criterion must declare a criterion_id")
        if len({c.criterion_id for c in criteria}) != len(criteria):
            raise RunScopeError("acceptance criterion ids must be unique")
        object.__setattr__(self, "acceptance_criteria", criteria)

        # A run may never authorise an execution the profile itself rejects.
        for command in sorted(self.allowed_execution_commands):
            try:
                permitted = command_is_allowed((command,), self.execution_profile.allowed_commands)
            except ValueError as exc:
                raise RunScopeError(
                    f"allowed execution command is not canonical: {command!r}"
                ) from exc
            if not permitted:
                raise RunScopeError(
                    "allowed_execution_commands may not exceed the execution profile: "
                    f"{command!r}"
                )

    # ------------------------------------------------------------------
    # Canonical identity
    # ------------------------------------------------------------------
    def authority_payload(self) -> dict[str, object]:
        """Return the canonical authority-relevant projection of this scope."""
        return {
            "run_id": self.run_id,
            "workspace_root": str(self.workspace.root),
            "workspace_root_casefold": str(self.workspace.root).casefold(),
            "execution_profile": self.execution_profile.to_dict(),
            "allowed_tool_ids": sorted(self.allowed_tool_ids),
            "allowed_execution_commands": sorted(self.allowed_execution_commands),
            "acceptance_criteria": [
                {
                    "criterion_id": criterion.criterion_id,
                    "requirement_id": getattr(criterion, "requirement_id", ""),
                    "description": getattr(criterion, "description", ""),
                }
                for criterion in self.acceptance_criteria
            ],
        }

    @property
    def fingerprint(self) -> str:
        """Deterministic fingerprint over every authority-relevant input."""
        serialized = json.dumps(
            self.authority_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    # ------------------------------------------------------------------
    # Freeze / anti-replacement guard
    # ------------------------------------------------------------------
    def freeze(self) -> str:
        """Register this scope as the frozen scope for its run.

        Raises ``RunScopeError`` when a *different* scope is already frozen for
        the same run id, which is exactly the "second scope after untrusted
        content" case that must fail closed.
        """
        existing = self._FROZEN.get(self.run_id)
        if existing is not None and existing != self.fingerprint:
            raise RunScopeError(
                f"a different run scope is already frozen for run '{self.run_id}'"
            )
        self._FROZEN[self.run_id] = self.fingerprint
        return self.fingerprint

    def is_frozen(self) -> bool:
        """Report whether a scope with this fingerprint is frozen for the run."""
        return self._FROZEN.get(self.run_id) == self.fingerprint

    def assert_current(self) -> None:
        """Fail closed unless this scope is the frozen scope for its run."""
        expected = self._FROZEN.get(self.run_id)
        if expected is None:
            raise RunScopeError(f"run '{self.run_id}' has no frozen scope")
        if expected != self.fingerprint:
            raise RunScopeError(
                f"run scope for '{self.run_id}' is not the frozen scope"
            )

    @classmethod
    def frozen_fingerprint(cls, run_id: str) -> str | None:
        """Return the frozen fingerprint for a run, if any."""
        return cls._FROZEN.get(run_id)

    @classmethod
    def release(cls, run_id: str) -> None:
        """Forget the frozen scope for a run. Intended for test isolation."""
        cls._FROZEN.pop(run_id, None)

    @classmethod
    def release_all(cls) -> None:
        """Forget every frozen scope. Intended for test isolation."""
        cls._FROZEN.clear()

    # ------------------------------------------------------------------
    # Authority checks
    # ------------------------------------------------------------------
    def allows_tool(self, tool_id: object) -> bool:
        """Report whether a tool id is inside the frozen perimeter."""
        return isinstance(tool_id, str) and tool_id in self.allowed_tool_ids

    def allows_command(self, command: Iterable[object]) -> bool:
        """Report whether an execution command is inside the frozen perimeter."""
        requested = tuple(command)
        if not requested:
            return False
        try:
            identity_permitted = command_is_allowed(
                requested, self.execution_profile.allowed_commands
            )
        except ValueError:
            return False
        if not identity_permitted:
            return False
        if not self.allowed_execution_commands:
            return False
        return requested[0] in self.allowed_execution_commands

    def assert_tool_allowed(self, tool_id: object) -> None:
        """Fail closed when a tool is outside the perimeter, or the scope is not the frozen one."""
        self._assert_authoritative()
        if not self.allows_tool(tool_id):
            raise RunScopeError(f"tool '{tool_id}' is outside the run scope")

    def assert_command_allowed(self, command: Iterable[object]) -> None:
        """Fail closed when a command is outside the perimeter, or the scope is not the frozen one."""
        self._assert_authoritative()
        if not self.allows_command(command):
            requested = tuple(command)
            raise RunScopeError(
                f"command '{requested[0] if requested else ''}' is outside the run scope"
            )

    def _assert_authoritative(self) -> None:
        """Reject authorisation from a scope that is not the frozen one.

        Before a run id is frozen this is a no-op, so a scope remains usable while
        it is being established. Once a run has a frozen scope, no clone, copy, or
        ``dataclasses.replace`` result can authorise anything.
        """
        expected = self._FROZEN.get(self.run_id)
        if expected is not None and expected != self.fingerprint:
            raise RunScopeError(
                f"run scope for '{self.run_id}' is not the frozen scope"
            )

    def validate_command_set(self, commands: Sequence[str] | None) -> None:
        """Reject a command set that is not a subset of the frozen perimeter."""
        if commands is None:
            return
        normalized = _normalize_frozen_set(
            commands, field_name="allowed_execution_commands", allow_none=True
        )
        unknown = sorted(normalized - self.allowed_execution_commands)
        if unknown:
            raise RunScopeError(
                "run scope cannot authorise additional execution commands: "
                + ", ".join(unknown)
            )

    def validate_tool_set(self, tool_ids: Iterable[str] | None) -> None:
        """Reject a tool set that is not a subset of the frozen perimeter."""
        if tool_ids is None:
            return
        normalized = _normalize_frozen_set(tool_ids, field_name="allowed_tool_ids")
        unknown = sorted(normalized - self.allowed_tool_ids)
        if unknown:
            raise RunScopeError(
                "run scope cannot authorise additional tools: " + ", ".join(unknown)
            )

    def validate_workspace(self, workspace: Workspace | None) -> None:
        """Reject a workspace that differs from the frozen perimeter."""
        if workspace is None:
            return
        if not isinstance(workspace, Workspace):
            raise RunScopeError("workspace must be a Workspace contract")
        if workspace.root != self.workspace.root:
            raise RunScopeError("run scope cannot change the workspace root")

    def validate_acceptance_criteria(
        self, criteria: Sequence[AcceptanceCriterion] | None
    ) -> None:
        """Reject acceptance criteria that differ from the frozen perimeter."""
        if criteria is None:
            return
        requested = {
            criterion.criterion_id
            for criterion in criteria
            if isinstance(criterion, AcceptanceCriterion)
        }
        expected = {criterion.criterion_id for criterion in self.acceptance_criteria}
        if requested != expected:
            raise RunScopeError(
                "run scope cannot change the acceptance criteria"
            )

    def validate_execution_profile(
        self, profile: ProjectExecutionProfile | None
    ) -> None:
        """Reject an execution profile that differs from the frozen perimeter."""
        if profile is None:
            return
        if not isinstance(profile, ProjectExecutionProfile):
            raise RunScopeError("execution_profile must be a ProjectExecutionProfile")
        if profile.to_dict() != self.execution_profile.to_dict():
            raise RunScopeError("run scope cannot change the execution profile")

    def validate_execution_request(self, request: ExecutionRequest) -> None:
        """Reject an execution request that leaves the frozen perimeter."""
        self.validate_execution_profile(getattr(request, "profile", None))
        self.assert_command_allowed(getattr(request, "command", ()))
        working_directory = getattr(request, "working_directory", ".")
        if not isinstance(working_directory, str) or not working_directory.strip():
            raise RunScopeError("execution request must declare a working directory")

    def assert_consistent(self, **authority: object) -> None:
        """Validate every supplied authority input against the frozen perimeter.

        Unknown keyword names are rejected so a caller cannot silently believe it
        has validated something that this method does not check.
        """
        handlers: Mapping[str, object] = {
            "allowed_execution_commands": self.validate_command_set,
            "allowed_tool_ids": self.validate_tool_set,
            "workspace": self.validate_workspace,
            "acceptance_criteria": self.validate_acceptance_criteria,
            "execution_profile": self.validate_execution_profile,
        }
        for name, value in authority.items():
            handler = handlers.get(name)
            if handler is None:
                raise RunScopeError(f"unknown run scope authority field: {name}")
            handler(value)  # type: ignore[operator]


def freeze_run_scope(scope: RunScope) -> str:
    """Freeze a scope and return its fingerprint."""
    if not isinstance(scope, RunScope):
        raise RunScopeError("scope must be a RunScope")
    return scope.freeze()


def scope_for_run(run_id: str) -> str | None:
    """Return the frozen fingerprint for a run id, if any."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise RunScopeError("run_id must be a non-empty string")
    return RunScope.frozen_fingerprint(run_id.strip())
