"""Core API service bridging HTTP/UI endpoints to Forge Core subsystems."""

from __future__ import annotations

import datetime
import time
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence

from app.api.models import (
    AgentInfo,
    HealthResponse,
    ProjectInfo,
    SystemStatusResponse,
    TaskRunRequest,
    TaskRunResponse,
)
from app.dashboard.models import DashboardReport, ProviderStatus
from app.dashboard.provider_health import ProviderHealthMonitor
from app.dashboard.service import CostDashboardService
from app.agent_runtime.acceptance_spec import (
    AcceptanceSpec,
    AcceptanceSpecError,
    RunAcceptanceCriteria,
)
from app.agent_runtime.criterion_identity import TaskIdentity, bind_run_criteria
from app.agent_runtime.idempotency import (
    IdempotencyDecision,
    IdempotencyError,
    IdempotencyVerdict,
    OperationClass,
    RunLifecycleState,
)
from app.agent_runtime.idempotency import (
    OperationIdentity,
    canonical_idempotency_key,
)
from app.agent_runtime.idempotency_integration import (
    RunIdempotencyGuard,
    default_idempotency_guard,
    operation_identity_from_binding,
)
from app.agent_runtime.tool_execution import ToolIntent
from app.agent_runtime.models import HarnessRequest
from app.agent_runtime.policy import AgentHarnessPolicy
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import (
    ExecutionDeclaration,
    ExecutionDeclarationError,
    UnknownExecutionDeclarationError,
    to_execution_request,
    validate_declarations_against_profile,
)
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus
from app.fabric.fabric import CapabilityFabric
from app.orchestrator.models import EventType, RunState, Task, TaskCategory
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import (
    create_agent_harness,
    create_loop_coordinator,
    create_runtime,
)
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope, require_active_scope
from app.runtime.run_store import RunStateSnapshot, RunStore
from app.tasks.specification import TaskSpecification
from app.tools.approval import ApprovalPolicy, ApprovalResolver
from app.tools.acceptance import AcceptanceCriterion, AcceptanceStatus
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, build_default_tool_registry
from app.tools.workspace import Workspace

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.execution.request import ExecutionRequest


def default_api_execution_profile() -> ProjectExecutionProfile:
    """Return the execution profile bound into every API RunScope.

    The profile is the ceiling, never a grant. It declares a small surface so
    that declared executions can be admitted at all:

    * ``allowed_commands=("python",)`` - ``RunScope`` rejects a profile with an
      empty command set, and ``ExecutionPolicy`` requires the declared
      executable to appear here;
    * ``capabilities={INTERPRET_TEXT}`` - ``ExecutionPolicy`` requires every
      capability the argv implies to be declared here. ``python -c`` classifies
      as ``INTERPRET_TEXT``, so without this the declared execution would be
      denied with ``argv_not_authorized``. This widens the ceiling of what a
      *declaration* could ask for; it does not grant anything on its own,
      because only an operator declaration can produce a request and the
      ``RunScope`` command set is derived from that declaration.
    * ``working_directory="."`` and empty ``environment_variables`` - both are
      ceilings ``RunScope`` enforces exactly, so a declaration may only reuse
      what is declared here.

    ``network_access=False`` is preserved. The API creates the RunScope with a
    command set derived from the operator's declarations, which is empty when
    nothing is declared, so no process is reachable by default.
    """
    return ProjectExecutionProfile(
        profile_id="api-default",
        allowed_commands=("python",),
        capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        network_access=False,
    )


# Minimal acceptance criterion required by RunScope. The API does not run an
# acceptance gate today; the criterion exists only because RunScope requires a
# non-empty declaration and must never be silently empty.
API_RUN_CRITERION = AcceptanceCriterion(
    criterion_id="api-run-completes",
    description="API run reaches a terminal state",
    requirement_id="api-run",
)

# Bounds for the first production vertical slice of the agent loop: exactly one
# action and one execution attempt. `max_revision_attempts` must stay positive:
# the decision provider treats a zero revision budget as an already-exhausted
# budget and would fail the run before it acts. The single action is enforced by
# `max_actions`, not by starving the revision budget. These bounds add no
# authority; they only narrow what the existing policy already permits.
SINGLE_ACTION_LOOP_POLICY = AgentHarnessPolicy(
    max_iterations=2,
    max_actions=1,
    max_execution_attempts=1,
    max_revision_attempts=1,
)

# Bounds for the acceptance slice. Exactly one execution attempt is allowed, and
# the budget must admit the verification stage that follows it, so the slice is
# "one execution plus its verification" rather than "one action then stop".
# Verification is not a decision here: the criteria and expectations it uses are
# frozen by the operator composition, and the decision provider cannot add,
# remove, or replace them.
ACCEPTANCE_LOOP_POLICY = AgentHarnessPolicy(
    max_iterations=3,
    max_actions=2,
    max_execution_attempts=1,
    max_revision_attempts=1,
)


# Bounds for a loop run whose criteria are the operator's own. Like the
# acceptance slice it needs one action to execute and one to verify, so the
# budget must admit the verification stage that follows the action. It grants no
# extra authority: the criteria, their expectations, and the command all come
# from the operator composition, and the decision provider can neither add,
# remove, nor replace any of them.
IDENTITY_LOOP_POLICY = AgentHarnessPolicy(
    max_iterations=3,
    max_actions=2,
    max_execution_attempts=1,
    max_revision_attempts=1,
)


from pathlib import Path


class ForgeApiService:
    """Service handling API requests and delegating to Forge Core without exposing secrets."""

    def __init__(
        self,
        runtime: Optional[RuntimeContext] = None,
        fabric: Optional[CapabilityFabric] = None,
        dashboard_service: Optional[CostDashboardService] = None,
        workspace: Optional[Workspace] = None,
        run_store: Optional[RunStore] = None,
        tool_registry: Optional[ToolRegistry] = None,
        allowed_tool_ids: frozenset[str] = frozenset(),
        execution_profile: Optional[ProjectExecutionProfile] = None,
        approval_resolver: Optional[ApprovalResolver] = None,
        allowed_execution_commands: frozenset[str] = frozenset(),
        approval_policy: Optional[ApprovalPolicy] = None,
        execution_request_factory: (
            Callable[..., Sequence["ExecutionRequest"]] | None
        ) = None,
        declarations: Optional[Mapping[str, ExecutionDeclaration]] = None,
        acceptance_specs: Optional[Mapping[str, AcceptanceSpec]] = None,
        loop_coordinator_factory: Optional[Callable[..., object]] = None,
        verification_resolver: Optional[
            Callable[[Task, ProjectExecutionProfile], str | None]
        ] = None,
        harness_factory: Callable[..., object] | None = None,
        idempotency_guard: object | None = None,
        # Composition-supplied durable sink for physical execution telemetry
        # (Stage 0.1). It measures a terminal attempt and grants no authority, so
        # supplying one cannot widen what a run may do.
        telemetry_sink: object | None = None,
        # Operator-supplied, server-side tool intents for this run. Each is a
        # ToolIntent: a tool identity plus bounded data arguments. A ToolIntent
        # grants nothing on its own - the run's frozen allowed_tool_ids decides
        # whether it may be invoked. With no declared intents no tool action is
        # ever offered.
        tool_intents: Sequence["ToolIntent"] = (),
    ) -> None:
        self._workspace = workspace or Workspace(Path.cwd())

        # SERVER-SIDE OPERATOR AUTHORITY. The tool allowlist is declared here, at
        # composition time, by whoever starts the process. It is never derived
        # from an HTTP request, request context, skill manifest, or the fabric,
        # and it is never "all registered tools". The empty default is the
        # fail-closed starting point.
        self._allowed_tool_ids = frozenset(allowed_tool_ids)

        # SERVER-SIDE OPERATOR COMMAND AUTHORITY. Since the declared execution
        # block, command authority comes from operator-owned declarations, not
        # from an independently configured allowlist: two sources could disagree
        # and one of them would silently widen the perimeter. The retained
        # parameter is accepted for compatibility but no longer grants anything.
        self._allowed_execution_commands = frozenset()
        self._approval_policy = approval_policy
        # Server-side only: a callable that *asks* for execution. It cannot
        # authorise anything, and it is never populated from client input. It is
        # deliberately not exposed on the HTTP contract.
        self._execution_request_factory = execution_request_factory
        # Composition-time factory for the canonical orchestration loop. It is the
        # same factory the runtime uses, so there is exactly one loop
        # implementation; the service only asks it for a harness with the narrowed
        # bounds of the first vertical slice.
        self._harness_factory = harness_factory or create_agent_harness
        # Composition-time durable sink for the physical measurement of a terminal
        # attempt (Stage 0.1). It is supplied by the composition - exactly like the
        # run store and the idempotency guard - so a service written by an
        # embedding caller records into its own storage rather than the process
        # default. It measures; it grants no authority.
        self._telemetry_sink = telemetry_sink
        # The idempotency decision seam. Injected explicitly by tests and by
        # any composition that wants its own ledger; otherwise the lazy
        # process-wide guard is used. It decides whether a second execution
        # may start and grants no authority of any kind.
        self._idempotency_guard = idempotency_guard

        # Operator-declared execution intents. Declarations are data, never a
        # gate: an unmatched declaration simply cannot run.
        self._declarations: Mapping[str, ExecutionDeclaration] = dict(
            declarations or {}
        )
        # Server-side binding from a task to a declaration. It receives only
        # server-derived inputs and must never read a task description, a request
        # context, or any other client-controlled text, because that would turn
        # natural language into command selection. No resolver means no host
        # execution, which is the fail-closed default.
        self._acceptance_specs: Mapping[str, AcceptanceSpec] = dict(
            acceptance_specs or {}
        )
        # Composition-time factory for the coordinator the acceptance slice
        # dispatches through. The API asks for a coordinator; it never imports the
        # adapter or spawns anything itself.
        self._loop_coordinator_factory = (
            loop_coordinator_factory or create_loop_coordinator
        )
        self._verification_resolver = verification_resolver

        # One registry serves both execution and discovery, so capability
        # enumeration can never report a tool that execution cannot reach.
        self._tool_registry = tool_registry or build_default_tool_registry(
            self._workspace.root
        )
        self._execution_profile = execution_profile or default_api_execution_profile()
        # Fail closed at composition time: a declaration the ceiling cannot admit
        # is a deployment error, not a runtime surprise.
        if self._declarations:
            validate_declarations_against_profile(
                self._declarations, self._execution_profile
            )
        # Reuses the canonical registry, permission policy, and approval policy.
        # No resolver is invented: write tools therefore stay WAITING_FOR_APPROVAL.
        self._approval_resolver = approval_resolver
        self._tool_intents: tuple[object, ...] = tuple(tool_intents or ())
        self._tool_executor = ToolExecutor(
            self._tool_registry,
            approval_resolver=approval_resolver,
        )

        resolved_runtime = runtime or create_runtime(tool_registry=self._tool_registry)
        # The shared ToolExecutor must reach the executor that actually dispatches
        # tools. RunExecutor carries no mutable state beyond immutable references,
        # so rebinding it to the same orchestrator preserves behavior exactly.
        self._runtime = replace(
            resolved_runtime,
            run_executor=RunExecutor(
                resolved_runtime.orchestrator,
                tool_executor=self._tool_executor,
            ),
        )

        self._fabric = fabric or CapabilityFabric(
            workspace=self._workspace,
            tool_registry=self._tool_registry,
        )
        self._run_store = run_store or RunStore()
        self._health_monitor = ProviderHealthMonitor()
        self._dashboard_service = dashboard_service or CostDashboardService(
            fabric=self._fabric,
            health_monitor=self._health_monitor,
        )
        self._active_project_id = "default"

    def _effective_tool_ids(self) -> frozenset[str]:
        """Compute the tools this service may authorize for a Run.

        Effective tools are the operator allowlist intersected with the tools
        that are actually registered. Intersecting in this direction means:
        a tool that is registered but not allow-listed is never authorized, and
        an allow-listed id with no registered implementation never appears.
        """
        registered = {definition.id for definition in self._tool_registry.list_tools()}
        return frozenset(self._allowed_tool_ids & registered)

    def _declared_tool_intents(self) -> tuple[object, ...]:
        """Return the declared tool intents whose tools this run truly authorizes.

        Two independent conditions must hold: the operator declared the intent,
        and the run's own tool perimeter authorizes that tool. An intent for a
        tool outside ``_effective_tool_ids()`` is dropped here rather than being
        offered to a decision, so a declaration can never widen the perimeter.
        """
        if not self._tool_intents:
            return ()
        authorizable = self._effective_tool_ids()
        declared: list[object] = []
        seen: set[str] = set()
        for intent in self._tool_intents:
            tool_id = str(getattr(intent, "tool_id", "") or "")
            if not tool_id or tool_id in seen or tool_id not in authorizable:
                continue
            seen.add(tool_id)
            declared.append(intent)
        return tuple(declared)

    def _resolve_declaration_id(self, task: Task) -> str | None:
        """Resolve the declaration for one task using server-side inputs only.

        The resolver is operator-supplied and receives the task plus the active
        execution profile. Nothing here reads the task description, the request
        context, or any other client-controlled text, because that would make
        natural language select a command. No resolver means no declaration,
        which means no host execution.
        """
        if self._verification_resolver is None:
            return None
        resolved = self._verification_resolver(task, self._execution_profile)
        if resolved is None:
            return None
        if not isinstance(resolved, str) or not resolved.strip():
            raise ExecutionDeclarationError(
                "verification resolver must return a non-empty declaration id or None"
            )
        return resolved.strip()

    def _build_declared_verification_scope(
        self, run_id: str, declaration: ExecutionDeclaration
    ) -> RunScope:
        """Build and freeze the perimeter for one declared verification run.

        Deliberately separate from ``_build_run_scope``, which is task-bound. A
        declared verification run has no client task, and its only authority is
        the operator-declared command it was asked for, so the command set is
        derived from that one declaration rather than from a resolver.

        Everything else comes from the service's own trusted values - its
        workspace root, its execution profile, and the server-side tool
        allowlist - and nothing is accepted from the caller. ``RunScope``
        remains the only perimeter mechanism and is frozen before any
        privileged action.
        """
        scope = RunScope(
            run_id=run_id,
            workspace=self._workspace,
            execution_profile=self._execution_profile,
            # Declared verification is primarily a host process run. Its tool
            # perimeter stays empty unless the operator actually declared tool
            # intents for this service, in which case it is exactly the operator
            # allowlist intersected with the registry - never anything wider.
            allowed_tool_ids=(
                self._effective_tool_ids() if self._tool_intents else frozenset()
            ),
            allowed_execution_commands=frozenset({declaration.executable}),
            # The frozen acceptance set must match what the run will actually be
            # judged by. When the operator declared acceptance criteria for this
            # declaration those criteria are the task's criteria; otherwise the
            # run keeps the API's own terminal-state criterion.
            acceptance_criteria=self._criteria_for(declaration),
        )
        scope.freeze()
        require_active_scope(run_id, scope)
        return scope

    def _criteria_for(
        self, declaration: ExecutionDeclaration
    ) -> tuple[AcceptanceCriterion, ...]:
        """The operator's criteria for one declaration, server-side only.

        Criteria come from the operator's acceptance declaration and nowhere else.
        A request, a decision, a plan, a tool result, or a model cannot contribute
        to this set.
        """
        spec = self._acceptance_specs.get(declaration.declaration_id)
        if spec is None:
            return (API_RUN_CRITERION,)
        return tuple(spec.criteria)

    def _build_declared_verification_factory(
        self, run_id: str, declaration: ExecutionDeclaration
    ) -> Callable[..., Sequence["ExecutionRequest"]]:
        """Build the factory that turns one named declaration into a request.

        The declaration is already resolved and validated, so this closure
        cannot select a different command and cannot be influenced by a task, a
        request, or a context. ``purpose_run_id`` correlation is not threaded
        here; it is attached by the caller of this factory.

        ``approval_required`` is left at its default: approval stays a
        server-side policy decision and a declaration cannot weaken it.
        """

        def _factory(task: object, profile: object) -> Sequence["ExecutionRequest"]:
            if not isinstance(profile, ProjectExecutionProfile):
                raise ExecutionDeclarationError(
                    "execution profile is unavailable for this run"
                )
            return (
                to_execution_request(declaration, profile=profile, run_id=run_id),
            )

        return _factory

    def _effective_execution_commands(self, task: Optional[Task]) -> frozenset[str]:
        """Derive a run's command authority from the declaration that resolved.

        Only a resolved declaration contributes authority, so a scope never
        advertises a command this run cannot actually reach. An unresolved or
        unknown declaration yields the empty set, which disables host execution
        for the run and keeps the ``RunScope`` gate fail-closed. A missing task
        cannot resolve anything, so it also yields the empty set.
        """
        if task is None:
            return frozenset()
        try:
            declaration_id = self._resolve_declaration_id(task)
        except ExecutionDeclarationError:
            raise
        except Exception:  # noqa: BLE001 - an unusable resolver grants nothing
            return frozenset()
        if declaration_id is None:
            return frozenset()
        declaration = self._declarations.get(declaration_id)
        if declaration is None:
            return frozenset()
        return frozenset({declaration.executable})

    def _build_execution_request_factory(
        self, run_id: str
    ) -> Callable[..., Sequence["ExecutionRequest"]] | None:
        """Build the server-side factory that turns a declaration into a request.

        Returns ``None`` when the operator declared nothing, so the executor's own
        gate keeps host execution disabled. An unknown declaration raises a typed
        error, which the executor records as an explicit denial rather than
        silently skipping.
        """
        if not self._declarations:
            return None

        def _factory(task: Task, profile: object) -> Sequence["ExecutionRequest"]:
            declaration_id = self._resolve_declaration_id(task)
            if declaration_id is None:
                return ()
            declaration = self._declarations.get(declaration_id)
            if declaration is None:
                raise UnknownExecutionDeclarationError(
                    f"verification resolver selected an undeclared execution: "
                    f"{declaration_id!r}"
                )
            if not isinstance(profile, ProjectExecutionProfile):
                raise ExecutionDeclarationError(
                    "execution profile is unavailable for this run"
                )
            return (
                to_execution_request(
                    declaration, profile=profile, run_id=run_id
                ),
            )

        return _factory

    def _build_run_scope(self, run_id: str, task: Optional[Task] = None) -> RunScope:
        """Build and freeze the security perimeter for one API run.

        The scope binds the run to the service's own Workspace as the explicit
        execution root, to the fail-closed execution profile, to the server-side
        tool allowlist, and to the command authority derived from the operator's
        declared executions. It is frozen before any privileged action so a later
        expansion attempt is detectable and rejected.
        """
        scope = RunScope(
            run_id=run_id,
            workspace=self._workspace,
            execution_profile=self._execution_profile,
            # The frozen tool perimeter mirrors the operator allowlist intersected
            # with the registry. It is an outer boundary only: the harness still
            # authorizes each invocation against the run's own declared intents, so
            # a wider perimeter never by itself authorizes a tool call.
            allowed_tool_ids=self._effective_tool_ids(),
            # Derived from the resolved declaration, never from an independently
            # configured list. An empty set means no host process is reachable
            # through this scope; RunScope.allows_command rejects every command.
            allowed_execution_commands=self._effective_execution_commands(task),
            acceptance_criteria=(API_RUN_CRITERION,),
        )
        scope.freeze()
        require_active_scope(run_id, scope)
        return scope

    @property
    def runtime(self) -> RuntimeContext:
        return self._runtime

    @property
    def fabric(self) -> CapabilityFabric:
        return self._fabric

    @property
    def dashboard_service(self) -> CostDashboardService:
        return self._dashboard_service

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    def list_capabilities(self) -> List[Dict[str, object]]:
        """Return the capabilities currently available, as plain dictionaries.

        Read-only enumeration over the registries this service actually holds. It
        reports what is registered right now, performs no probing or scanning, and
        exposes only descriptor fields - never credentials, approval state, or a
        frozen execution scope. Host environment discovery is deliberately not
        part of this: it is a separate architectural stage.
        """
        return [descriptor.to_dict() for descriptor in self._fabric.list_capabilities()]

    def list_tools(self) -> List[Dict[str, object]]:
        """Return the tools registered for execution, as plain dictionaries.

        Uses the same registry instance the service configures capabilities from,
        so this listing cannot report a tool that execution cannot reach.
        """
        return [
            {"id": definition.id, "name": definition.name, "description": definition.description}
            for definition in self._tool_registry.list_tools()
        ]

    @property
    def run_store(self) -> RunStore:
        return self._run_store

    def get_run_record(self, run_id: str) -> Optional[Dict[str, object]]:
        """Return the durable history of a Run, or None when unknown.

        Strictly read-only. It reports what was persisted and never resumes,
        retries, or re-executes an interrupted Run, and never returns secrets or
        raw payloads: the stored history is already sanitized by the canonical
        event-metadata policy.
        """
        if not isinstance(run_id, str) or not run_id.strip():
            return None
        try:
            record = self._run_store.load(run_id.strip())
        except ValueError:
            # Unsafe run id for the storage layout: treat as unknown.
            return None
        if record is None:
            return None
        return record.summary()

    def get_health(self) -> HealthResponse:
        """Return basic liveness and Core connection status."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        core_status = "connected" if self._runtime is not None else "disconnected"
        return HealthResponse(
            status="ok",
            core_status=core_status,
            version="0.1.0",
            timestamp=now,
        )

    def get_system_status(self) -> SystemStatusResponse:
        """Return detailed status of Core, API, and Provider layers."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        core_status = "connected" if self._runtime is not None else "disconnected"

        # Check provider accounts status
        account_statuses = self._health_monitor.check_all(force=False)
        ready_count = sum(
            1 for st in account_statuses.values()
            if st.status in (ProviderStatus.READY, ProviderStatus.READY_GATEWAY, ProviderStatus.FREE_TIER)
        )
        total_count = len(account_statuses)

        provider_layer_status = "READY" if ready_count > 0 else "DEGRADED"

        return SystemStatusResponse(
            core=core_status,
            api="connected",
            provider_status=provider_layer_status,
            providers_ready=ready_count,
            providers_total=total_count,
            active_project=self._active_project_id,
            timestamp=now,
            details={
                "enabled_providers": list(self._runtime.settings.enabled_providers),
                "default_provider": self._runtime.settings.default_provider,
            },
        )

    def list_projects(self) -> List[ProjectInfo]:
        """List active and known projects."""
        return [
            ProjectInfo(
                project_id=self._active_project_id,
                name="Forge Main Workspace",
                path=str(self._workspace.root),
                status="active",
            )
        ]

    def list_agents(self) -> List[AgentInfo]:
        """List registered agents and models from AgentRegistry."""
        agents: List[AgentInfo] = []
        for name in self._runtime.agent_registry.list_agents():
            try:
                agent = self._runtime.agent_registry.get(name)
                prov_name = getattr(agent, "provider_name", name)
                model_name = getattr(agent, "model_name", "default")
                agents.append(
                    AgentInfo(
                        name=name,
                        provider=prov_name,
                        model=model_name,
                        status="available",
                        capabilities=["code", "analysis", "review"],
                    )
                )
            except Exception:
                agents.append(
                    AgentInfo(
                        name=name,
                        provider=name,
                        model="unknown",
                        status="error",
                    )
                )
        return agents

    def _build_trusted_specification_and_binding(
        self, run_id: str, declaration: ExecutionDeclaration, task_id: str
    ) -> tuple[object | None, object | None]:
        """Derive the trusted task specification and criterion binding.

        One server-side construction, used both by the harness request builder
        and by the idempotency claim so the two can never disagree about the
        identity of a run. It is created whenever the operator declared either an
        intent or an acceptance spec, because it is also the trusted task
        identity the criterion binding is built from. It carries no authority:
        only the task id and the operator's own descriptive text and criteria.
        """
        acceptance_spec = self._acceptance_specs.get(declaration.declaration_id)
        specification = None
        if declaration.intent or acceptance_spec is not None:
            specification = TaskSpecification(
                task_id=task_id,
                title=declaration.declaration_id,
                description=declaration.intent,
                requirements=(),
                acceptance_criteria=(
                    acceptance_spec.criteria
                    if acceptance_spec is not None
                    else (API_RUN_CRITERION,)
                ),
            )

        # Trusted criterion identity. Only the operator's own acceptance
        # declaration can produce criteria; a request, a decision, a plan, a tool
        # result, and a model all contribute nothing here.
        task_binding = None
        if acceptance_spec is not None:
            task_binding = bind_run_criteria(
                run_id=run_id,
                specification=specification,
                criteria=acceptance_spec.criteria,
                expectations=acceptance_spec.expectations,
                source=acceptance_spec.source,
            )
            task_binding.assert_belongs_to(run_id, specification.task_id)
        return specification, task_binding

    def _build_harness_request(
        self,
        run_id: str,
        declaration: ExecutionDeclaration,
        scope: RunScope,
        task_id: str,
    ) -> HarnessRequest:
        """Assemble the per-run input for the canonical orchestration loop.

        Everything authority-bearing is derived from the operator's declaration
        and the frozen scope, never from a request, a context, or a decision:

        * ``execution_requests`` is the operator's declared command, already
          translated once. The loop selects among pre-authorized actions by index;
          a decision can never author one.
        * ``allowed_execution_commands`` is the scope's derived command set, so the
          loop's own authorization step and the coordinator both re-check the
          same operator-granted set.

        When the operator has declared an ``AcceptanceSpec`` for this
        declaration, the run also receives its frozen task/criterion identity
        binding: the criteria, their expectations, and a fingerprint over each,
        bound to this ``run_id`` and this task. That identity is what lets
        verification run against a real criterion instead of ``not_evaluated``,
        and what lets a later revision prove it is judged by the same criteria.
        Without a declared spec nothing is invented: there is no criterion to
        verify, so the acceptance stage stays honestly deferred.
        """
        # The planning goal is the operator's declared intent, delivered as a
        # TaskSpecification so the planner has a trusted goal and the loop keeps
        # one task identity. It is descriptive text, never authority. With no
        # declared intent there is no goal, and the planning stage is skipped
        # rather than given a fabricated one.
        #
        # The specification is also the trusted task identity, so it is created
        # whenever the operator declared either an intent or an acceptance spec.
        # It carries no authority: only the task id and its descriptive text.
        specification, task_binding = self._build_trusted_specification_and_binding(
            run_id, declaration, task_id
        )
        if task_binding is not None:
            metadata_extra: dict[str, object] = {
                "criterion_ids": list(task_binding.criterion_ids),
                "criterion_source": task_binding.source,
            }
        else:
            metadata_extra = {}

        return HarnessRequest(
            run_id=run_id,
            workspace=self._workspace,
            task_specification=specification,
            task_binding=task_binding,
            tool_requests=self._declared_tool_intents(),
            execution_requests=(
                to_execution_request(
                    declaration,
                    profile=self._execution_profile,
                    run_id=run_id,
                ),
            ),
            allowed_execution_commands=tuple(scope.allowed_execution_commands),
            acceptance_criteria=(
                specification.acceptance_criteria
                if specification is not None
                else (API_RUN_CRITERION,)
            ),
            verification_expectations=(
                self._acceptance_specs[declaration.declaration_id].expectations
                if declaration.declaration_id in self._acceptance_specs
                else {}
            ),
            approval_policy=self._approval_policy,
            approval_resolver=self._approval_resolver,
            run_scope=scope,
            # Server-side identity for the loop's own events, so discovery,
            # decision, and execution share one task identity with the run record.
            metadata={
                "declaration_id": declaration.declaration_id,
                "task_id": task_id,
                **metadata_extra,
            },
        )

    @staticmethod
    @staticmethod
    def _idempotent_lifecycle_labels(labels: tuple[str, ...]) -> RunLifecycleState:
        """Map recorded outcome labels onto the durable idempotency lifecycle."""
        from app.agent_runtime.idempotency import classify_outcome_labels

        return classify_outcome_labels(labels)

    @staticmethod
    def _idempotent_lifecycle(result: object, execution_result: object) -> RunLifecycleState:
        """Map one harness outcome onto the durable idempotency lifecycle.

        An authority or approval denial becomes a terminal security failure, so a
        later delivery can never retry a denied action. Everything else maps onto
        the ordinary terminal/recoverable set.
        """
        status = ""
        final_state = getattr(result, "final_state", None)
        if final_state is not None:
            status = str(getattr(getattr(final_state, "status", None), "value", "") or "")
        labels = {status.lower()}
        if execution_result is not None:
            outcome = getattr(execution_result, "outcome_status", None)
            labels.add(str(getattr(outcome, "value", "") or "").lower())
        from app.agent_runtime.idempotency import classify_outcome_labels

        return classify_outcome_labels(tuple(labels))

    def _build_run_task_specification(
        self, declaration: ExecutionDeclaration
    ) -> TaskSpecification:
        """Build the trusted task specification for one declaration.

        One server-side construction shared by every idempotent entry point, so
        three paths cannot drift into three different identities for the same
        declaration. Nothing here is read from a caller, a decision, a plan, or
        metadata; the declaration id is the task id, exactly as the sibling
        entry points derive their stored task ids from the declaration.
        """
        return TaskSpecification(
            task_id=declaration.declaration_id,
            title=declaration.declaration_id,
            description=declaration.intent,
            requirements=(),
            acceptance_criteria=self._criteria_for(declaration),
        )

    def _begin_run_operation(
        self,
        declaration: ExecutionDeclaration,
        operation_class: OperationClass,
        idempotency_key: Optional[str],
        run_id: str,
        *,
        task_identity: object | None = None,
        criterion_identities: object = (),
    ) -> tuple[
        Optional[RunIdempotencyGuard],
        Optional[OperationIdentity],
        object | None,
    ]:
        """Claim this operation before anything durable is written.

        Returns ``(guard, identity, decision)``. With no key all three are None
        and the caller keeps its previous behaviour exactly. With a key the claim
        happens here, which is deliberately *before* the frozen scope, the
        ``RunStore`` binding, and the first durable event: a duplicate delivery
        must not write a run-start event for a run it is not allowed to perform.

        The identity is composed from the operator's declaration and, when the
        path has one, the frozen task/criterion binding. This helper grants no
        authority: it cannot build a command, a scope, a profile, an approval, or
        an ``AuthorizedExecution``.
        """
        if idempotency_key is None:
            return None, None, None
        guard = self._idempotency_guard or default_idempotency_guard()
        identity = operation_identity_from_binding(
            operation_class=operation_class,
            idempotency_key=canonical_idempotency_key(idempotency_key),
            declaration_id=declaration.declaration_id,
            task_identity=(
                task_identity
                if task_identity is not None
                else TaskIdentity.from_specification(
                    self._build_run_task_specification(declaration)
                )
            ),
            criterion_identities=criterion_identities,
        )
        decision = guard.begin(identity, requested_run_id=run_id)
        return guard, identity, decision

    def _finish_run_operation(
        self,
        guard: Optional[RunIdempotencyGuard],
        identity: Optional[OperationIdentity],
        *,
        lifecycle: RunLifecycleState,
        outcome_labels: tuple[str, ...] = (),
    ) -> IdempotencyError | None:
        """Record the durable outcome, returning the error instead of raising.

        Callers must surface a returned error as an unrecorded outcome rather
        than a success: if the position could not be recorded, a later delivery
        would read an earlier state and could be admitted again.
        """
        if guard is None or identity is None:
            return None
        try:
            guard.finish(
                identity, lifecycle=lifecycle, outcome_labels=outcome_labels
            )
            return None
        except IdempotencyError as exc:
            return exc

    def _idempotent_response(
        self,
        *,
        run_id: str,
        declaration: ExecutionDeclaration,
        decision: object,
        loop_store: Optional[RunStore],
    ) -> TaskRunResponse:
        """Report a refusal or a replay instead of executing a second time.

        No action, no execution, and no tool is attempted on this path. The
        response carries the recorded verdict and the run identity that owns the
        operation, so an operator can inspect the original run rather than
        trigger a duplicate.
        """
        verdict = getattr(getattr(decision, "verdict", None), "value", "")
        code = getattr(getattr(decision, "failure_code", None), "value", "")
        reason = str(getattr(decision, "reason", "") or "")
        recorded_run_id = str(getattr(decision, "run_id", "") or run_id) or run_id
        # Record the refusal against the run that already owns this operation,
        # not against the run id this delivery generated. A duplicate must leave
        # the durable history of a run it is not allowed to perform untouched, so
        # the event lands on the recorded run when one exists.
        if loop_store is None and recorded_run_id:
            try:
                loop_store = self._run_store.bind_run(recorded_run_id)
            except Exception:  # noqa: BLE001 - persistence must not block a run
                loop_store = None
        self._emit_acceptance_event(
            loop_store,
            recorded_run_id,
            f"task-loop-{declaration.declaration_id}",
            EventType.EXECUTION_DENIED,
            {
                "denial_reason": f"idempotent_{verdict}" if verdict else "idempotent",
                "idempotency_verdict": verdict,
                "idempotency_failure_code": code,
                "declaration_id": declaration.declaration_id,
                "recorded_run_id": recorded_run_id,
            },
        )
        return TaskRunResponse(
            run_id=recorded_run_id,
            task_id=f"task-loop-{declaration.declaration_id}",
            project_id=self._active_project_id,
            state="DENIED" if verdict in {"conflict", "refuse"} else "REPLAYED",
            # No execution happened on this path, so it is not a success and the
            # zeroed telemetry is real rather than a placeholder.
            success=False,
            output="",
            error=reason or f"idempotency verdict: {verdict}",
            tokens=0,
            cost=0.0,
            duration_seconds=0.0,
        )

    def run_agent_loop(
        self,
        declaration_id: str,
        *,
        purpose_run_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> TaskRunResponse:
        """Run the canonical agent loop once through the production harness.

        This is a trusted server-side entry point, like
        ``run_declared_verification``: it is called by operator-controlled code
        inside the process and is not reachable from the HTTP contract.

        The loop is intentionally a single-action vertical slice: one context
        assembly, one decision, one operator-authorized action, one execution
        result, recorded in the durable run history. ``decision`` never grants
        authority - the decision provider returns a recommendation, and both the
        harness's authorization step and the ``ExecutionCoordinator`` validate it
        against the frozen ``RunScope``.

        ``run_task`` is unchanged and does not route through this method, so the
        existing API task path keeps its previous semantics.
        """
        if self._runtime.harness is None:
            raise ExecutionDeclarationError(
                "no agent harness is configured in this runtime"
            )
        if not self._declarations:
            raise ExecutionDeclarationError(
                "no execution declarations are configured, so the agent loop has "
                "no pre-authorized action to select"
            )
        if not isinstance(declaration_id, str) or not declaration_id.strip():
            raise ExecutionDeclarationError("declaration_id must be a non-empty string")
        declaration = self._declarations.get(declaration_id.strip())
        if declaration is None:
            raise UnknownExecutionDeclarationError(
                f"undeclared execution: {declaration_id.strip()!r}"
            )
        if purpose_run_id is not None and (
            not isinstance(purpose_run_id, str) or not purpose_run_id.strip()
        ):
            raise ExecutionDeclarationError(
                "purpose_run_id must be a non-empty string when provided"
            )

        run_id = f"run-loop-{uuid.uuid4().hex[:8]}"
        task_id = f"task-loop-{declaration.declaration_id}"

        # Trusted identity first, and nothing durable before it. The claim below
        # must precede the frozen scope, the RunStore binding, and the first
        # durable event: a duplicate delivery must not write a run-start event
        # for a run it is not allowed to perform.
        specification, binding = self._build_trusted_specification_and_binding(
            run_id, declaration, task_id
        )
        guard, guard_identity, guard_decision = self._begin_run_operation(
            declaration,
            OperationClass.AGENT_LOOP,
            idempotency_key,
            run_id,
            task_identity=(
                binding.task_identity if binding is not None else None
            ),
            criterion_identities=(
                binding.criterion_identities if binding is not None else ()
            ),
        )
        if guard_decision is not None and (
            guard_decision.verdict is not IdempotencyVerdict.START
        ):
            return self._idempotent_response(
                run_id=guard_decision.run_id or run_id,
                declaration=declaration,
                decision=guard_decision,
                loop_store=None,
            )

        scope = self._build_declared_verification_scope(run_id, declaration)

        loop_store: Optional[RunStore] = None
        try:
            loop_store = self._run_store.bind_run(run_id, task_id=task_id)
        except Exception:  # noqa: BLE001 - persistence must not block a run
            loop_store = None

        if loop_store is not None:
            try:
                loop_store(
                    "RUN_STARTED",
                    {"run_id": run_id, "task_id": task_id, "entry_point": "run_agent_loop"},
                )
            except Exception:  # noqa: BLE001 - persistence must not block a run
                pass

        request = self._build_harness_request(
            run_id, declaration, scope, task_id
        )

        # The frozen criterion identity, recorded before the loop runs so the
        # durable history can answer "which task, which criterion, which run?".
        # Only bounded identity metadata is stored - never raw criterion text.
        if binding is not None:
            self._emit_acceptance_event(
                loop_store,
                run_id,
                task_id,
                EventType.CRITERION_DEFINED,
                {
                    "declaration_id": declaration.declaration_id,
                    "criteria_source": binding.source,
                    "expectation_count": len(binding.criterion_ids),
                    **binding.bounded_summary(),
                },
            )

        # Built by the same composition factory that supplies the runtime's
        # canonical harness, differing only in the narrowed bounds of this slice.
        # No second loop implementation and no second authority object exist.
        # A run judged by the operator's own criteria needs the same budget as the
        # acceptance slice: one action to execute and one to verify.
        # The production ToolExecutor is supplied explicitly. With no declared
        # tool intents and no allow-listed tools nothing reaches it, so the safe
        # default is unchanged; when a tool is authorized the executor keeps
        # owning its permission check and approval.
        harness = self._harness_factory(
            policy=(
                IDENTITY_LOOP_POLICY
                if binding is not None
                else SINGLE_ACTION_LOOP_POLICY
            ),
            tool_executor=self._tool_executor,
            telemetry_sink=self._telemetry_sink,
        )
        t0 = time.perf_counter()
        result = harness.run(request)
        duration = time.perf_counter() - t0

        if loop_store is not None:
            self._persist_harness_events(loop_store, result)
            self._persist_loop_state(loop_store, run_id, task_id, result)

        execution_result = (
            result.execution_results[0] if result.execution_results else None
        )

        if guard is not None and guard_identity is not None:
            # Record the durable position so the next delivery replays instead of
            # re-executing. An authority failure is recorded as terminal, which is
            # what stops a later delivery from retrying a denied action.
            #
            # A refusal here is not swallowed: if the position could not be
            # recorded, a later delivery would read an earlier state and could be
            # admitted to run again, so the run is reported as unrecorded rather
            # than as a success this code cannot stand behind.
            failure = self._finish_run_operation(
                guard,
                guard_identity,
                lifecycle=self._idempotent_lifecycle(result, execution_result),
                outcome_labels=(result.final_state.status.value,),
            )
            if failure is not None:
                return self._idempotent_response(
                    run_id=run_id,
                    declaration=declaration,
                    decision=IdempotencyDecision(
                        verdict=IdempotencyVerdict.REFUSE,
                        run_id=run_id,
                        failure_code=failure.code,
                        reason=(
                            "the durable idempotency outcome could not be "
                            f"recorded ({failure.code.value}), so this run is not "
                            "reported as successful"
                        ),
                    ),
                    loop_store=loop_store,
                )

        output = (execution_result.stdout if execution_result else "") or ""
        error: Optional[str] = None
        if execution_result is None:
            error = result.final_state.status.value
        elif execution_result.outcome_status != ExecutionOutcomeStatus.EXECUTION_SUCCESS:
            error = str(
                execution_result.metadata.get("denial_reason")
                or getattr(
                    execution_result.outcome_status,
                    "value",
                    execution_result.outcome_status,
                )
            )
        if not result.decisions and error is None:
            error = "no_decision_produced"

        succeeded = (
            execution_result is not None
            and execution_result.outcome_status == ExecutionOutcomeStatus.EXECUTION_SUCCESS
        )
        return TaskRunResponse(
            run_id=run_id,
            task_id=task_id,
            project_id=self._active_project_id,
            state=result.final_state.status.value,
            # Execution success, not task acceptance: the acceptance stage is
            # deferred until criterion identity exists (GAP-F).
            success=succeeded,
            output=output or "(Agent loop produced no process output)",
            error=error,
            tokens=0,
            cost=0.0,
            duration_seconds=round(duration, 3),
            provider_used="",
            model_used="",
        )

    def _persist_harness_events(self, store: RunStore, result: object) -> None:
        """Record the loop's collected events into the existing durable history.

        Reuses the run store's existing append path; no second event store is
        created and the run loop itself is not modified.
        """
        run_id = store.bound_run_id
        if not run_id:
            return
        for event in getattr(result, "events", ()) or ():
            event_type = getattr(event, "event_type", None)
            if event_type is None:
                continue
            try:
                store.append_event(
                    event_type,
                    dict(getattr(event, "metadata", {}) or {}),
                    run_id=run_id,
                    attempt_number=getattr(event, "attempt_number", 0) or 0,
                    task_id=getattr(event, "task_id", None),
                )
            except Exception:  # noqa: BLE001 - persistence must not block a run
                continue

    def _persist_loop_state(
        self, store: RunStore, run_id: str, task_id: str, result: object
    ) -> None:
        """Persist the loop's terminal snapshot with the existing state writer.

        The harness produces its own result object rather than an orchestrator
        ``Run``, so the snapshot is derived from that result directly instead of
        from ``record_run_state``. It uses the same ``RunStateSnapshot`` type and
        the same atomic writer, so the durable history keeps one shape and the
        ``task_id`` matches the events exactly.
        """
        try:
            events = tuple(getattr(result, "events", ()) or ())
            state = getattr(getattr(result, "final_state", None), "status", None)
            store.write_state(
                RunStateSnapshot(
                    run_id=run_id,
                    task_id=task_id,
                    state=state.value if hasattr(state, "value") else str(state or ""),
                    status=state.value if hasattr(state, "value") else str(state or ""),
                    attempt_number=0,
                    event_count=len(events),
                    updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                )
            )
        except Exception:  # noqa: BLE001 - persistence must not block a run
            pass

    def run_accepted_task(
        self,
        declaration_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> TaskRunResponse:
        """Run one authorized action and decide real task acceptance.

        This is the production acceptance entry point. It is trusted in-process
        code, not an HTTP route, and its only input is a declaration id. Criteria,
        expectations, command, workspace, profile, environment, timeout, tools, and
        approval all come from the operator composition - never from the caller.

        The flow is fixed by this composition, not by a decision provider:

            one operator-authorized action
              -> deterministic verification against the frozen expectations
              -> AcceptanceGate PASS/FAIL
              -> durable history

        Verification and acceptance are produced by the existing harness stages
        (``RUN_VERIFICATION`` -> ``VerificationEvaluator``/``WorkspaceVerifier`` ->
        ``AcceptanceGate``). This method deliberately does not evaluate acceptance
        itself: it consumes ``final_acceptance`` and refuses to treat a missing
        verdict as a pass.

        ``EXECUTION_SUCCESS`` is deliberately **not** task acceptance. The
        response's ``success`` means the action ran successfully *and* its
        verification passed; the acceptance verdict is recorded as
        ``acceptance_status`` in the durable run record. Redefining the public
        ``success`` field is a separate contract decision.
        """
        if self._runtime.harness is None:
            raise ExecutionDeclarationError(
                "no agent harness is configured in this runtime"
            )
        if not isinstance(declaration_id, str) or not declaration_id.strip():
            raise ExecutionDeclarationError("declaration_id must be a non-empty string")
        declaration_key = declaration_id.strip()
        declaration = self._declarations.get(declaration_key)
        if declaration is None:
            raise UnknownExecutionDeclarationError(
                f"undeclared execution: {declaration_key!r}"
            )
        spec = self._acceptance_specs.get(declaration_key)
        if spec is None:
            # No trusted criteria means no acceptance. This is not a permissive
            # default: without criteria nothing can legitimately be accepted.
            raise AcceptanceSpecError(
                f"no acceptance criteria are declared for {declaration_key!r}, so "
                "task acceptance cannot be evaluated"
            )

        run_id = f"run-accept-{uuid.uuid4().hex[:8]}"
        # `spec.bind` here is the existing per-run criterion binding. The run's
        # task id is derived server-side from the operator's declaration, and the
        # specification below carries that very same id, so the criterion binding
        # and the trusted task identity always agree.
        task_id = f"task-accept-{declaration.declaration_id}"
        criteria = spec.bind(run_id=run_id, task_id=task_id)
        criteria.assert_belongs_to(run_id, task_id)

        # Trusted task/criterion identity for this run. The task identity comes
        # from the operator's own declaration, and the criteria from the operator's
        # acceptance spec; nothing here is reachable from the caller, a decision, a
        # plan, a tool result, or a model.
        task_specification = TaskSpecification(
            task_id=task_id,
            title=declaration.declaration_id,
            description=declaration.intent,
            requirements=(),
            acceptance_criteria=criteria.criteria,
        )
        task_binding = bind_run_criteria(
            run_id=run_id,
            specification=task_specification,
            criteria=criteria.criteria,
            expectations=criteria.expectations,
            source=criteria.source,
        )
        task_binding.assert_belongs_to(run_id, task_id)

        # Server-side idempotency, claimed before the scope is frozen and before
        # any durable write. Without a key every value below is None and this
        # path keeps its previous behaviour exactly.
        guard, guard_identity, guard_decision = self._begin_run_operation(
            declaration,
            OperationClass.ACCEPTED_TASK,
            idempotency_key,
            run_id,
            task_identity=task_binding.task_identity,
            criterion_identities=task_binding.criterion_identities,
        )
        if guard_decision is not None and (
            guard_decision.verdict is not IdempotencyVerdict.START
        ):
            return self._idempotent_response(
                run_id=guard_decision.run_id or run_id,
                declaration=declaration,
                decision=guard_decision,
                loop_store=None,
            )

        scope = RunScope(
            run_id=run_id,
            workspace=self._workspace,
            execution_profile=self._execution_profile,
            # The acceptance slice is a host-process slice, not a tool slice.
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({declaration.executable}),
            acceptance_criteria=criteria.criteria,
        )
        scope.freeze()
        require_active_scope(run_id, scope)

        record_store: Optional[RunStore] = None
        try:
            record_store = self._run_store.bind_run(run_id, task_id=task_id)
        except Exception:  # noqa: BLE001 - persistence must not block a run
            record_store = None

        # Identity and criteria are recorded before anything executes, so the
        # frozen criteria are part of the run's durable history rather than a
        # later claim.
        self._emit_acceptance_event(
            record_store,
            run_id,
            task_id,
            "RUN_STARTED",
            {"run_id": run_id, "task_id": task_id, "entry_point": "run_accepted_task"},
        )
        self._emit_acceptance_event(
            record_store,
            run_id,
            task_id,
            "CRITERION_DEFINED",
            {
                "run_id": run_id,
                "task_id": task_id,
                "declaration_id": declaration.declaration_id,
                "criterion_ids": list(criteria.criterion_ids),
                "criteria_source": criteria.source,
                "expectation_count": len(criteria.expectations),
            },
        )

        request = HarnessRequest(
            run_id=run_id,
            workspace=self._workspace,
            # The trusted task identity the criterion binding was derived from, so
            # the harness proves the very same task id the binding carries.
            task_specification=task_specification,
            execution_requests=(
                to_execution_request(
                    declaration,
                    profile=self._execution_profile,
                    run_id=run_id,
                ),
            ),
            allowed_execution_commands=tuple(scope.allowed_execution_commands),
            # The same frozen task/criterion identity the acceptance binding was
            # built from, so the harness proves before verifying that the task and
            # every criterion fingerprint still match.
            task_binding=task_binding,
            # Frozen server-side criteria and their expectations. A decision
            # cannot add, drop, or replace any of them.
            acceptance_criteria=criteria.criteria,
            verification_expectations=criteria.expectations,
            approval_policy=self._approval_policy,
            approval_resolver=self._approval_resolver,
            run_scope=scope,
            metadata={
                "declaration_id": declaration.declaration_id,
                "task_id": task_id,
                "criterion_ids": list(criteria.criterion_ids),
                "binding_fingerprint": task_binding.fingerprint,
            },
        )

        harness = self._harness_factory(
            policy=ACCEPTANCE_LOOP_POLICY,
            coordinator=self._acceptance_coordinator(),
            tool_executor=self._tool_executor,
            telemetry_sink=self._telemetry_sink,
        )
        t0 = time.perf_counter()
        result = harness.run(request)
        duration = time.perf_counter() - t0

        # The loop's own stages land in the same durable history through the
        # existing writer.
        if record_store is not None:
            self._persist_harness_events(record_store, result)

        execution_result = (
            result.execution_results[0] if result.execution_results else None
        )
        execution_outcome = (
            execution_result.outcome_status.value
            if execution_result is not None
            else "NOT_RUN"
        )
        acceptance = result.final_acceptance

        # The acceptance verdict is written with the run's own identity, using the
        # same store and snapshot writer as every other run. A missing verdict is
        # recorded as not evaluated, never as a pass.
        acceptance_status = "not_evaluated"
        acceptance_code = "verification_not_evaluated"
        if acceptance is not None:
            acceptance_status = acceptance.status.value
            acceptance_code = acceptance.code or ""
        self._emit_acceptance_event(
            record_store,
            run_id,
            task_id,
            "ACCEPTANCE_COMPLETED",
            {
                "run_id": run_id,
                "task_id": task_id,
                "status": acceptance_status,
                "code": acceptance_code,
                "criterion_ids": list(criteria.criterion_ids),
                "criteria_source": criteria.source,
                "criterion_results": [
                    {
                        "criterion_id": item.criterion_id,
                        "status": item.status.value,
                        "code": item.code,
                    }
                    for item in (getattr(acceptance, "results", ()) or ())
                ],
            },
        )
        if record_store is not None:
            try:
                record_store.write_state(
                    RunStateSnapshot(
                        run_id=run_id,
                        task_id=task_id,
                        state=result.final_state.status.value,
                        status=result.final_state.status.value,
                        attempt_number=0,
                        event_count=len(result.events),
                        updated_at=datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                        project_state={"acceptance_status": acceptance_status},
                    )
                )
            except Exception:  # noqa: BLE001 - persistence must not block a run
                pass

        failure = self._finish_run_operation(
            guard,
            guard_identity,
            lifecycle=self._idempotent_lifecycle(result, execution_result),
            outcome_labels=(result.final_state.status.value,),
        )
        if failure is not None:
            return self._idempotent_response(
                run_id=run_id,
                declaration=declaration,
                decision=IdempotencyDecision(
                    verdict=IdempotencyVerdict.REFUSE,
                    run_id=run_id,
                    failure_code=failure.code,
                    reason=(
                        "the durable idempotency outcome could not be recorded "
                        f"({failure.code.value}), so this run is not reported as "
                        "successful"
                    ),
                ),
                loop_store=record_store,
            )

        output = (execution_result.stdout if execution_result else "") or ""
        error: Optional[str] = None
        if execution_result is None:
            error = result.final_state.status.value
        elif execution_result.outcome_status != ExecutionOutcomeStatus.EXECUTION_SUCCESS:
            error = str(
                execution_result.metadata.get("denial_reason") or execution_outcome
            )
        elif acceptance is None:
            error = "verification_not_evaluated"
        elif acceptance.status is not AcceptanceStatus.PASS:
            error = acceptance.code or "acceptance_rejected"

        accepted = acceptance is not None and acceptance.status is AcceptanceStatus.PASS
        return TaskRunResponse(
            run_id=run_id,
            task_id=task_id,
            project_id=self._active_project_id,
            state=result.final_state.status.value,
            # The action ran successfully *and* its verification passed. Task
            # acceptance is reported separately as `acceptance_status` in the
            # durable run record, because redefining this field is a public
            # contract decision.
            success=(
                execution_result is not None
                and execution_result.outcome_status
                == ExecutionOutcomeStatus.EXECUTION_SUCCESS
                and accepted
            ),
            output=output or "(Accepted task produced no process output)",
            error=error,
            tokens=0,
            cost=0.0,
            duration_seconds=round(duration, 3),
            provider_used="",
            model_used="",
        )

    def _acceptance_coordinator(self) -> object:
        """Coordinator for the acceptance slice, built by composition.

        It runs with workspace COPY-staging disabled on purpose: the criterion
        asserts a fact about the workspace the action was told to work in, and a
        scratch copy would make that fact unverifiable. This does not widen
        authority - ``RunScope``, ``ExecutionPolicy``, approval, and the
        coordinator's sentinel are unchanged - it only keeps the process and the
        verification pointed at the same directory. The API layer never imports
        the adapter; it asks the composition for a coordinator.
        """
        return self._loop_coordinator_factory(isolate_workspace=False)

    def _make_store_observer(self, store: Optional[RunStore]) -> Callable[..., None]:
        """Return an observer that mirrors events into the durable history."""
        run_id = store.bound_run_id if store is not None else None

        def observe(event_type: object, data: object) -> None:
            if store is None or not run_id:
                return
            try:
                store.append_event(
                    event_type,
                    dict(data) if isinstance(data, dict) else {},
                    run_id=run_id,
                )
            except Exception:  # noqa: BLE001 - persistence must not block a run
                pass

        return observe

    def _emit_acceptance_event(
        self,
        store: Optional[RunStore],
        run_id: str,
        task_id: str,
        event_type: object,
        data: Mapping[str, object],
    ) -> None:
        if store is None:
            return
        try:
            store.append_event(event_type, dict(data), run_id=run_id, task_id=task_id)
        except Exception:  # noqa: BLE001 - persistence must not block a run
            pass

    def run_declared_verification(
        self,
        declaration_id: str,
        *,
        purpose_run_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> TaskRunResponse:
        """Run one operator-declared verification execution.

        This is a trusted server-side entry point. It must be called by
        operator-controlled code inside the process; it is intentionally not
        reachable from the HTTP contract, which has no caller-trust model.

        The only selection input is ``declaration_id``, and it is resolved
        exclusively against the operator-configured declaration registry. No
        command, workspace, profile, environment, timeout, allowlist, or
        approval flag is accepted - a caller can choose *which* declared
        verification runs, never *what* runs.

        ``purpose_run_id`` is correlation only: it is recorded as sanitized
        metadata and never becomes execution authority.

        Fail-closed behaviour:

        * no declarations configured -> refused, so verification execution is
          disabled and there is no default or implicit command;
        * unknown ``declaration_id`` -> ``UnknownExecutionDeclarationError``,
          no scope is frozen, no request is built, no process is started.

        Execution itself reuses the existing chain unchanged: frozen
        ``RunScope`` -> ``RunExecutor`` -> ``ExecutionCoordinator`` ->
        ``AuthorizedExecution`` -> ``LocalExecutionAdapter``. Approval remains
        the sole decision of the server-side ``ApprovalPolicy``.
        """
        if not self._declarations:
            raise ExecutionDeclarationError(
                "no execution declarations are configured, so declared "
                "verification execution is disabled"
            )
        if not isinstance(declaration_id, str) or not declaration_id.strip():
            raise ExecutionDeclarationError("declaration_id must be a non-empty string")
        declaration = self._declarations.get(declaration_id.strip())
        if declaration is None:
            raise UnknownExecutionDeclarationError(
                f"undeclared execution: {declaration_id.strip()!r}"
            )
        if purpose_run_id is not None and (
            not isinstance(purpose_run_id, str) or not purpose_run_id.strip()
        ):
            raise ExecutionDeclarationError(
                "purpose_run_id must be a non-empty string when provided"
            )

        run_id = f"run-verify-{uuid.uuid4().hex[:8]}"

        # Server-side idempotency, claimed before the scope is frozen and before
        # any durable write. ``purpose_run_id`` stays correlation metadata and is
        # never part of the operation identity, so it cannot be used to obtain a
        # second execution of the same declaration under one key. Without a key
        # every value below is None and this path keeps its previous behaviour.
        guard, guard_identity, guard_decision = self._begin_run_operation(
            declaration, OperationClass.DECLARED_VERIFICATION, idempotency_key, run_id
        )
        if guard_decision is not None and (
            guard_decision.verdict is not IdempotencyVerdict.START
        ):
            return self._idempotent_response(
                run_id=guard_decision.run_id or run_id,
                declaration=declaration,
                decision=guard_decision,
                loop_store=None,
            )

        scope = self._build_declared_verification_scope(run_id, declaration)

        # One canonical task identity for this run. It is derived from the
        # declaration only as a naming source, exactly as ``run_task`` derives
        # ``task_id`` and passes that same value to both the stored ``Task`` and
        # ``bind_run``. The declaration id itself stays declaration identity and
        # is never stored as a task id.
        verification_task_id = f"task-verify-{declaration.declaration_id}"

        verification_store: Optional[RunStore] = None
        try:
            verification_store = self._run_store.bind_run(
                run_id, task_id=verification_task_id
            )
        except Exception:  # noqa: BLE001 - persistence must not block a run
            verification_store = None

        base_factory = self._build_declared_verification_factory(run_id, declaration)
        correlation = purpose_run_id.strip() if purpose_run_id else None

        def _factory(task: object, profile: object) -> Sequence["ExecutionRequest"]:
            requests = base_factory(task, profile)
            if correlation is None:
                return requests
            return tuple(
                replace(
                    request,
                    metadata={**dict(request.metadata), "purpose_run_id": correlation},
                )
                for request in requests
            )

        t0 = time.perf_counter()
        run = self._runtime.run_executor.execute(
            task=Task(
                id=verification_task_id,
                description="Declared verification execution.",
                # Built entirely server-side. Nothing here is read back for
                # declaration selection: the entry point is handed its
                # declaration directly and does not consult a resolver.
                context={"run_id": run_id},
            ),
            workspace=self._workspace,
            run_id=run_id,
            run_scope=scope,
            allowed_tool_ids=frozenset(),
            run_store=verification_store,
            allowed_execution_commands=scope.allowed_execution_commands,
            execution_request_factory=_factory,
            approval_policy=self._approval_policy,
            approval_resolver=self._approval_resolver,
        )
        duration = time.perf_counter() - t0

        if verification_store is not None:
            try:
                verification_store.record_run_state(
                    run,
                    status=run.state.value,
                    attempt_number=0,
                )
            except Exception:  # noqa: BLE001 - persistence must not block a run
                pass

        result = run.execution_results[0] if run.execution_results else None

        # Record the durable position. A denial classifies as a terminal security
        # failure through the existing lifecycle mapper, so a later delivery can
        # never retry a denied verification.
        verification_labels = [str(run.state.value)]
        if result is not None and result.outcome_status != ExecutionOutcomeStatus.EXECUTION_SUCCESS:
            verification_labels.append(str(getattr(result.outcome_status, "value", "")))
        failure = self._finish_run_operation(
            guard,
            guard_identity,
            lifecycle=self._idempotent_lifecycle_labels(tuple(verification_labels)),
            outcome_labels=tuple(verification_labels),
        )
        if failure is not None:
            return self._idempotent_response(
                run_id=run_id,
                declaration=declaration,
                decision=IdempotencyDecision(
                    verdict=IdempotencyVerdict.REFUSE,
                    run_id=run_id,
                    failure_code=failure.code,
                    reason=(
                        "the durable idempotency outcome could not be recorded "
                        f"({failure.code.value}), so this run is not reported as "
                        "successful"
                    ),
                ),
                loop_store=verification_store,
            )

        output = ""
        error = None
        if result is not None:
            output = result.stdout or ""
            if result.outcome_status != ExecutionOutcomeStatus.EXECUTION_SUCCESS:
                error = str(
                    result.metadata.get("denial_reason")
                    or getattr(result.outcome_status, "value", result.outcome_status)
                )
        else:
            # The executor refused before producing a request - for example when
            # no server-side approval policy is configured. Surface the recorded
            # denial reason so a misconfiguration is diagnosable instead of
            # silently reporting a run that executed nothing.
            for event in reversed(run.events):
                if event.type.value == "execution_denied":
                    error = str(event.data.get("reason") or "execution_denied")
                    break
            if error is None:
                error = "no declared execution was requested"

        return TaskRunResponse(
            run_id=run_id,
            # The same canonical task identity that the stored Task, the bound
            # history store, and the execution events use. The declaration id
            # remains separately visible in the request metadata.
            task_id=verification_task_id,
            project_id=self._active_project_id,
            state=run.state.value,
            success=run.state == RunState.COMPLETED,
            output=output or "(Declared verification execution produced no output)",
            error=error,
            tokens=0,
            cost=0.0,
            duration_seconds=round(duration, 3),
            provider_used="",
            model_used="",
        )

    def get_dashboard_report(self, force_refresh: bool = False) -> DashboardReport:
        """Retrieve consolidated cost and provider dashboard report."""
        return self._dashboard_service.get_report(force_refresh_health=force_refresh)

    def refresh_dashboard(self) -> Dict[str, Any]:
        """Trigger lightweight provider health refresh."""
        statuses = self._dashboard_service.refresh_provider_accounts()
        return {
            "refreshed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "providers_checked": len(statuses),
        }

    def run_task(self, req: TaskRunRequest) -> TaskRunResponse:
        """Execute a task through Forge Core and return execution telemetry."""
        run_id = f"run-api-{uuid.uuid4().hex[:8]}"
        task_id = req.task_id or f"task-{uuid.uuid4().hex[:6]}"
        project_id = req.project_id or self._active_project_id

        category = TaskCategory.CODE
        try:
            category = TaskCategory(req.category.lower())
        except ValueError:
            category = TaskCategory.CODE

        context = dict(req.context)
        context.update({"run_id": run_id, "project_id": project_id})

        task = Task(
            id=task_id,
            description=req.description,
            category=category,
            context=context,
            parameters={"run_id": run_id},
        )

        t0 = time.perf_counter()
        # Durable history: bind the store to this run so its events persist and
        # remain queryable through GET /api/runs/{run_id} after a restart. The
        # store is a sink only and never resumes or re-executes anything.
        api_run_store: Optional[RunStore] = None
        try:
            api_run_store = self._run_store.bind_run(run_id, task_id=task_id)
        except Exception:  # noqa: BLE001 - persistence must not block a run
            api_run_store = None

        # Freeze this run's perimeter before any privileged action. The scope
        # binds the run id, the service's own Workspace as the explicit execution
        # root, the fail-closed execution profile, the server-side tool allowlist,
        # and the command authority derived from the operator's declared
        # executions. Nothing here is taken from the HTTP request.
        scope = self._build_run_scope(run_id, task)
        effective_tool_ids = scope.allowed_tool_ids
        execution_request_factory = self._build_execution_request_factory(run_id)

        run = self._runtime.run_executor.execute(
            task=task,
            provider_name=req.provider_name,
            workspace=self._workspace,
            run_id=run_id,
            run_scope=scope,
            allowed_tool_ids=effective_tool_ids,
            run_store=api_run_store,
            # Host process execution authority. With no declarations the factory
            # is None and the derived allowlist is empty, so the executor requests
            # nothing and no process is reachable. The factory is server-side only
            # and never sourced from the request.
            allowed_execution_commands=scope.allowed_execution_commands,
            execution_request_factory=(
                execution_request_factory or self._execution_request_factory
            ),
            approval_policy=self._approval_policy,
            approval_resolver=self._approval_resolver,
        )
        duration = time.perf_counter() - t0
        if api_run_store is not None:
            try:
                api_run_store.record_run_state(
                    run,
                    status=run.state.value if hasattr(run.state, "value") else str(run.state),
                    attempt_number=0,
                )
            except Exception:  # noqa: BLE001 - persistence must not block a run
                pass

        # Retrieve run accounting
        run_acc = self._fabric.get_run_accounting(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            total_duration_seconds=duration,
        )

        # Determine output
        output = ""
        error = None
        success = (run.state == RunState.COMPLETED)

        last_att = run_acc.attempt_records[-1] if run_acc.attempt_records else None
        provider_used = last_att.provider_name if last_att else ""
        model_used = last_att.model_name if last_att else ""

        # Check events for result or errors
        for ev in reversed(run.events):
            if ev.data.get("result"):
                res_obj = ev.data.get("result")
                output = getattr(res_obj, "output", "") or str(res_obj)
                break
            if ev.data.get("error"):
                error = str(ev.data.get("error"))

        if not output and run.result:
            output = run.result.output
            if run.result.error:
                error = run.result.error
                success = False

        return TaskRunResponse(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            state=run.state.value,
            success=success,
            output=output or "(Task execution completed)",
            error=error,
            tokens=run_acc.total_tokens,
            cost=round(run_acc.total_cost, 6),
            duration_seconds=round(duration, 3),
            provider_used=provider_used,
            model_used=model_used,
        )
