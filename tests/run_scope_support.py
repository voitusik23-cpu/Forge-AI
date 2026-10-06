"""Explicit RunScope construction for tests.

No derivation, no fallback, no shim. Every value is supplied by the caller,
mirroring production where the run identity and the perimeter are established by
the authoritative orchestration layer before any dispatch.

DECISION 1 - Run identity: run_id is always an explicit argument. It is never
inferred from a request, a coordinator, or an adapter.
DECISION 2 - One Run, One Immutable Scope: a scope is frozen once per run and a
caller may only declare a subset of it. A different perimeter means a different
run id, never a widened scope.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from app.agent_runtime.models import HarnessRequest
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.engineering import EngineeringRunRequest
from app.runtime.run_scope import RunScope
from app.tools.acceptance import AcceptanceCriterion
from app.tools.workspace import Workspace

DEFAULT_CRITERION = AcceptanceCriterion(
    criterion_id="test-criterion", description="test acceptance criterion"
)


def make_scope(
    run_id: str,
    *,
    commands: Iterable[object],
    workspace: Workspace,
    tools: Iterable[str] = (),
    profile: Optional[ProjectExecutionProfile] = None,
    criteria: Sequence[AcceptanceCriterion] = (DEFAULT_CRITERION,),
) -> RunScope:
    """Build a scope for an already-established run id (returned unfrozen)."""
    entries = tuple(commands)
    if profile is None:
        declared = sorted({str(getattr(e, "executable", e)) for e in entries}) or ["python"]
        profile = ProjectExecutionProfile("test-profile", allowed_commands=tuple(declared))
    return RunScope(
        run_id=run_id,
        workspace=workspace,
        execution_profile=profile,
        allowed_tool_ids=frozenset(tools),
        allowed_execution_commands=frozenset(entries),
        acceptance_criteria=tuple(criteria),
    )


def freeze_scope(
    run_id: str,
    *,
    commands: Iterable[object],
    workspace: Workspace,
    tools: Iterable[str] = (),
    profile: Optional[ProjectExecutionProfile] = None,
    criteria: Sequence[AcceptanceCriterion] = (DEFAULT_CRITERION,),
) -> RunScope:
    """Build and freeze the single scope for a run."""
    scope = make_scope(
        run_id,
        commands=commands,
        workspace=workspace,
        tools=tools,
        profile=profile,
        criteria=criteria,
    )
    scope.freeze()
    return scope


def harness_request(scope: RunScope, **kwargs) -> HarnessRequest:
    """Build a HarnessRequest bound to an already-frozen scope.

    run_id is never defaulted here: it comes from the scope the caller built,
    which itself required an explicit run id.
    """
    return HarnessRequest(**{**kwargs, "run_id": scope.run_id, "run_scope": scope})


def engineering_request(
    scope: RunScope, *, command=(), tools=(), allowed_execution_commands=None, **kwargs
) -> EngineeringRunRequest:
    """Build an EngineeringRunRequest bound to an already-frozen scope.

    `command` and `allowed_execution_commands` state what this Run declares.
    Each is validated against the frozen scope so it can only be a subset, and
    then forwarded: the engineering path passes the declaration to the
    coordinator's permission check, so dropping it would fail closed.
    """
    for declaration in (command, allowed_execution_commands):
        if declaration:
            scope.validate_command_set(declaration)
    if tools:
        scope.validate_tool_set(tools)
    # The scope is the single source of truth for the workspace root; the request
    # restates it so the execution path re-validates the same value.
    kwargs.setdefault("workspace", scope.workspace)
    if allowed_execution_commands is not None:
        kwargs["allowed_execution_commands"] = tuple(allowed_execution_commands)
    return EngineeringRunRequest(**{**kwargs, "run_scope": scope})