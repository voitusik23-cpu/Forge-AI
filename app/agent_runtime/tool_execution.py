"""Trusted composition boundary between a declared tool intent and execution.

A **Tool Intent** is data: a tool identity plus arguments. It is not authority,
and on its own it can do nothing. This module is the single server-side place
that turns a declared intent into an executable invocation, and it does so only
from the run's own frozen perimeter:

* the tool identity must be in the run's trusted ``allowed_tool_ids``;
* the invocation is composed here, never supplied by a decision or a model;
* the execution context is derived from the run and its frozen ``RunScope``;
* a fresh invocation identity is minted per attempt, so a previous attempt's
  result can never be replayed or reused as authority.

Nothing in this module executes anything. It composes the input for the existing
``ToolExecutor``, which owns permission checks, approval, and the tool call
itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.tools.contracts import ToolInvocation
from app.tools.permissions import ToolExecutionContext
from app.tools.workspace import Workspace


class ToolAuthorizationError(ValueError):
    """Raised when a declared tool intent cannot be trusted for this run."""


#: Argument keys that would name execution authority rather than data. Their
#: presence means the intent is not pure data, so it is refused outright.
FORBIDDEN_ARGUMENT_KEYS = frozenset(
    {
        "command", "commands", "argv", "executable", "shell", "cmd",
        "run_scope", "runscope", "authorizedexecution", "authorized_execution",
        "executioncoordinator", "execution_coordinator", "localexecutionadapter",
        "local_execution_adapter", "approval", "approval_policy",
        "approval_resolver", "allowed_execution_commands", "allowed_tool_ids",
        "environment", "env", "environment_variables", "timeout",
        "timeout_seconds", "network", "network_access", "capabilities",
        "profile", "profile_id", "workspace", "workspace_root", "cwd",
        "credential", "credentials", "secret", "secrets", "token", "password",
        "api_key", "private_key",
    }
)

#: Hard bounds on a declared intent's arguments. Module constants, not
#: parameters, so nothing per-run can widen them.
MAX_ARGUMENT_KEYS = 16
MAX_ARGUMENT_KEY_CHARS = 64
MAX_ARGUMENT_VALUE_CHARS = 65536


@dataclass(frozen=True)
class ToolIntent:
    """Immutable, non-authoritative declaration that a run may invoke a tool.

    It carries identity and arguments only. It cannot name an executable, an
    environment, a workspace, a timeout, a capability, a scope, or an approval.
    """

    tool_id: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.tool_id, str) or not self.tool_id.strip():
            raise ToolAuthorizationError("tool_id must be a non-empty string")
        if not isinstance(self.arguments, Mapping):
            raise ToolAuthorizationError("arguments must be a mapping")

        arguments = dict(self.arguments)
        if len(arguments) > MAX_ARGUMENT_KEYS:
            raise ToolAuthorizationError("too many argument keys")
        for key, value in arguments.items():
            if not isinstance(key, str) or not key.strip():
                raise ToolAuthorizationError("argument keys must be non-empty strings")
            if len(key) > MAX_ARGUMENT_KEY_CHARS:
                raise ToolAuthorizationError("argument key is too long")
            normalized = key.lower().strip().replace("-", "_").replace(" ", "_")
            if normalized in FORBIDDEN_ARGUMENT_KEYS:
                # Arguments are data. A key that names authority is refused rather
                # than ignored, so a widened intent can never slip through.
                raise ToolAuthorizationError(
                    f"argument {key!r} names execution authority, not data"
                )
            if isinstance(value, str) and len(value) > MAX_ARGUMENT_VALUE_CHARS:
                raise ToolAuthorizationError(f"argument {key!r} is too large")
            if isinstance(value, (bytes, bytearray)):
                raise ToolAuthorizationError(f"argument {key!r} must be text, not bytes")
        object.__setattr__(self, "tool_id", self.tool_id.strip())
        object.__setattr__(self, "arguments", arguments)

    def bounded_summary(self) -> dict[str, object]:
        """Sanitized shape only: identity and argument names, never values."""
        return {
            "tool_id": self.tool_id,
            "argument_keys": sorted(str(key) for key in self.arguments),
            "argument_count": len(self.arguments),
        }


@dataclass(frozen=True)
class AuthorizedToolCall:
    """A server-composed invocation bound to one run and its frozen perimeter.

    Constructing one requires the trusted composition function below; the
    constructor is not a public authorization path.
    """

    run_id: str
    task_id: str
    tool_id: str
    invocation: ToolInvocation
    context: ToolExecutionContext
    intent_summary: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("task_id", self.task_id),
            ("tool_id", self.tool_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ToolAuthorizationError(f"{name} must be a non-empty string")
        if not isinstance(self.invocation, ToolInvocation):
            raise ToolAuthorizationError("invocation must be a ToolInvocation")
        if not isinstance(self.context, ToolExecutionContext):
            raise ToolAuthorizationError("context must be a ToolExecutionContext")
        if self.invocation.tool_id != self.tool_id:
            raise ToolAuthorizationError("invocation does not match the authorized tool")
        if self.context.run_id != self.run_id:
            raise ToolAuthorizationError("context is not bound to this run")
        object.__setattr__(self, "intent_summary", dict(self.intent_summary or {}))

    def assert_belongs_to(self, run_id: str, task_id: str) -> None:
        if self.run_id != run_id:
            raise ToolAuthorizationError(
                f"tool call belongs to run {self.run_id!r}, not {run_id!r}"
            )
        if self.task_id != task_id:
            raise ToolAuthorizationError(
                f"tool call belongs to task {self.task_id!r}, not {task_id!r}"
            )

    def event_metadata(self) -> dict[str, object]:
        """Bounded metadata for the tool stage's events."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "tool_id": self.tool_id,
            "invocation_id": self.invocation.invocation_id,
            "argument_keys": list(self.intent_summary.get("argument_keys", ())),
        }


def authorize_tool_intent(
    intent: object,
    *,
    run_id: str,
    task_id: str,
    allowed_tool_ids: frozenset[str] | object,
    context_fingerprint: str,
    workspace: Workspace | None = None,
    run_scope: object | None = None,
    round_number: int = 0,
    attempt_number: int = 0,
) -> AuthorizedToolCall:
    """Compose one executable tool call from a declared intent, or refuse.

    Every authority-bearing value comes from the run's own perimeter. The intent
    contributes only its tool identity and its arguments.
    """
    if not isinstance(intent, ToolIntent):
        raise ToolAuthorizationError("intent must be a ToolIntent")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ToolAuthorizationError("run_id must be a non-empty string")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolAuthorizationError("task_id must be a non-empty string")
    if not isinstance(allowed_tool_ids, frozenset):
        raise ToolAuthorizationError("allowed_tool_ids must be a frozenset")
    if intent.tool_id not in allowed_tool_ids:
        # An intent is never self-authorizing: the run's own perimeter decides.
        raise ToolAuthorizationError(
            f"tool {intent.tool_id!r} is not authorized for this run"
        )
    if not isinstance(context_fingerprint, str) or not context_fingerprint.strip():
        raise ToolAuthorizationError("context_fingerprint must be a non-empty string")

    return AuthorizedToolCall(
        run_id=run_id,
        task_id=task_id,
        tool_id=intent.tool_id,
        # A fresh invocation identity per attempt: the earlier attempt's identity
        # and result can never be reused as authority for this one.
        invocation=ToolInvocation(tool_id=intent.tool_id, input=dict(intent.arguments)),
        context=ToolExecutionContext(
            run_id=run_id,
            context_fingerprint=context_fingerprint,
            allowed_tool_ids=allowed_tool_ids,
            round_number=round_number,
            attempt_number=attempt_number,
            workspace=workspace,
            run_scope=run_scope,
        ),
        intent_summary=intent.bounded_summary(),
    )
