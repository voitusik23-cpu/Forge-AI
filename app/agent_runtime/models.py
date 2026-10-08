"""Models for Forge AI Agent Harness and controlled Run Loop."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.orchestrator.trace import FORBIDDEN_METADATA_SUBSTRINGS, sanitize_event_metadata
from app.skills.models import SkillDefinition

if TYPE_CHECKING:
    from app.decision.models import Decision, DecisionAction
    from app.execution.models import ExecutionRequest, ExecutionResult, ProjectExecutionProfile
    from app.orchestrator.models import ApprovalPolicy, ApprovalResolver, Workspace
    from app.orchestrator.trace import RunEvent
    from app.projects.state import ProjectState, ProjectStateStatus
    from app.tasks.models import Task
    from app.tasks.specification import AcceptanceCriterion, Requirement, TaskSpecification
    from app.tools.acceptance import AcceptanceResult
    from app.tools.verification import VerificationExpectation, VerificationResult


class HarnessPhase(str, Enum):
    """Explicit phases in the Agent Harness controlled loop."""

    OBSERVE = "OBSERVE"
    CONTEXT = "CONTEXT"
    DECIDE = "DECIDE"
    VALIDATE = "VALIDATE"
    AUTHORIZE = "AUTHORIZE"
    ACT = "ACT"
    VERIFY = "VERIFY"
    UPDATE = "UPDATE"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    WAITING = "WAITING"


class HarnessStatus(str, Enum):
    """Lifecycle status of an Agent Harness execution."""

    INITIAL = "INITIAL"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass(frozen=True)
class StructuredObservation:
    """Bounded, safe observation produced after each authoritative action.

    Never contains raw stdout/stderr, secrets, passwords, or raw tokens.
    """

    action: str
    result_status: str
    execution_result_id: str | None = None
    verification_id: str | None = None
    acceptance_status: str | None = None
    project_state: str | None = None
    relevant_artifact_references: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        sanitized = sanitize_event_metadata(self.metadata)
        object.__setattr__(self, "metadata", sanitized)

        # Ensure no forbidden keywords in relevant_artifact_references
        sanitized_refs: dict[str, str] = {}
        for k, v in self.relevant_artifact_references.items():
            k_lower = str(k).lower()
            if not any(bad in k_lower for bad in FORBIDDEN_METADATA_SUBSTRINGS):
                sanitized_refs[str(k)] = str(v)
        object.__setattr__(self, "relevant_artifact_references", sanitized_refs)


@dataclass(frozen=True)
class HarnessState:
    """Immutable snapshot of the Agent Harness state at a specific iteration."""

    run_id: str
    attempt_number: int = 0
    iteration: int = 0
    phase: HarnessPhase = HarnessPhase.OBSERVE
    latest_decision_id: str | None = None
    latest_context_id: str | None = None
    latest_context_fingerprint: str | None = None
    latest_execution_result_id: str | None = None
    latest_verification_id: str | None = None
    project_state_status: str | None = None
    terminal: bool = False
    status: HarnessStatus = HarnessStatus.INITIAL
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id or not isinstance(self.run_id, str):
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(self.iteration, int) or self.iteration < 0:
            raise ValueError("iteration must be a non-negative integer")
        if not isinstance(self.attempt_number, int) or self.attempt_number < 0:
            raise ValueError("attempt_number must be a non-negative integer")
        sanitized = sanitize_event_metadata(self.metadata)
        object.__setattr__(self, "metadata", sanitized)


@dataclass(frozen=True)
class HarnessRequest:
    """Request contract providing inputs and policies for an Agent Harness run."""

    run_id: str
    task_specification: TaskSpecification | None = None
    workspace: Workspace | None = None
    attempt_number: int = 0
    execution_requests: tuple[ExecutionRequest, ...] = ()
    allowed_execution_commands: tuple[str, ...] | None = None
    verification_expectations: Mapping[str, VerificationExpectation] = field(default_factory=dict)
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    approval_policy: ApprovalPolicy | None = None
    approval_resolver: ApprovalResolver | None = None
    initial_project_state: ProjectState | None = None
    available_skills: tuple[SkillDefinition, ...] = ()
    available_capabilities: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    # Per-run bounded project observation produced by the harness's discovery
    # stage. It is populated inside the run loop, never supplied by a caller, a
    # decision, or an LLM, and it carries no authority.
    understanding_snapshot: object | None = None
    # Per-run declarative execution intention produced by the harness's planning
    # stage. Like the snapshot it is produced inside the run loop, is bound to
    # this run and task, and carries no execution authority.
    execution_plan: object | None = None
    # The frozen task/criterion identity binding for this run, created only by
    # trusted server-side composition. Identity is not authority: it makes a
    # verdict attributable and lets verification refuse a substituted criterion,
    # but it grants no execution, tool, workspace, network, or approval authority.
    task_binding: object | None = None
    # Declared tool intents this run may invoke, composed server-side exactly like
    # ``execution_requests``. A ToolIntent is data - a tool identity plus bounded
    # arguments - and grants nothing: the harness authorizes each invocation
    # against the run's frozen ``allowed_tool_ids``, and the existing
    # ``ToolExecutor`` performs the permission check and approval.
    tool_requests: tuple[object, ...] = ()
    # Immutable security perimeter for this run. When present it binds the
    # authority-relevant inputs and is validated before any context, memory,
    # knowledge, or provider output is read.
    run_scope: object | None = None
    # Optional immutable security perimeter for this run. When present it binds
    # the authority-relevant inputs and is validated before any context, memory,
    # knowledge, or provider output is read.
    run_scope: object | None = None

    def __post_init__(self) -> None:
        if not self.run_id or not isinstance(self.run_id, str):
            raise ValueError("run_id must be a non-empty string")
        if isinstance(self.execution_requests, list):
            object.__setattr__(self, "execution_requests", tuple(self.execution_requests))
        if isinstance(self.allowed_execution_commands, list):
            object.__setattr__(
                self, "allowed_execution_commands", tuple(self.allowed_execution_commands)
            )
        if isinstance(self.acceptance_criteria, list):
            object.__setattr__(self, "acceptance_criteria", tuple(self.acceptance_criteria))
        if isinstance(self.requirements, list):
            object.__setattr__(self, "requirements", tuple(self.requirements))
        if isinstance(self.tool_requests, list):
            object.__setattr__(self, "tool_requests", tuple(self.tool_requests))
        if isinstance(self.available_skills, (list, tuple)):
            for s in self.available_skills:
                if not isinstance(s, SkillDefinition):
                    raise ValueError(f"available_skills elements must be SkillDefinition, got {type(s)}")
            object.__setattr__(self, "available_skills", tuple(self.available_skills))
        if isinstance(self.available_capabilities, list):
            object.__setattr__(self, "available_capabilities", tuple(self.available_capabilities))


@dataclass(frozen=True)
class HarnessResult:
    """Final immutable output of an Agent Harness execution."""

    run_id: str
    final_state: HarnessState
    iterations: tuple[HarnessState, ...] = ()
    observations: tuple[StructuredObservation, ...] = ()
    decisions: tuple[Decision, ...] = ()
    execution_results: tuple[ExecutionResult, ...] = ()
    verification_results: tuple[VerificationResult, ...] = ()
    final_acceptance: AcceptanceResult | None = None
    final_project_state: ProjectState | None = None
    events: tuple[RunEvent, ...] = ()
