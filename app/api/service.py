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
from app.orchestrator.models import RunState, Task, TaskCategory
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
            acceptance_criteria=(API_RUN_CRITERION,),
        )
        scope.freeze()
        require_active_scope(run_id, scope)
        return scope

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

        ``verification_expectations`` is left empty: criterion identity is GAP-F
        and is deliberately not invented here, so the acceptance stage stays
        deferred rather than pretending a successful process is task acceptance.
        """
        # The planning goal is the operator's declared intent, delivered as a
        # TaskSpecification so the planner has a trusted goal and the loop keeps
        # one task identity. It is descriptive text, never authority. With no
        # declared intent there is no goal, and the planning stage is skipped
        # rather than given a fabricated one.
        specification = (
            TaskSpecification(
                task_id=task_id,
                title=declaration.declaration_id,
                description=declaration.intent,
                requirements=(),
                acceptance_criteria=(API_RUN_CRITERION,),
            )
            if declaration.intent
            else None
        )

        return HarnessRequest(
            run_id=run_id,
            workspace=self._workspace,
            task_specification=specification,
            tool_requests=self._declared_tool_intents(),
            execution_requests=(
                to_execution_request(
                    declaration,
                    profile=self._execution_profile,
                    run_id=run_id,
                ),
            ),
            allowed_execution_commands=tuple(scope.allowed_execution_commands),
            acceptance_criteria=(API_RUN_CRITERION,),
            approval_policy=self._approval_policy,
            approval_resolver=self._approval_resolver,
            run_scope=scope,
            # Server-side identity for the loop's own events, so discovery,
            # decision, and execution share one task identity with the run record.
            metadata={
                "declaration_id": declaration.declaration_id,
                "task_id": task_id,
            },
        )

    def run_agent_loop(
        self,
        declaration_id: str,
        *,
        purpose_run_id: Optional[str] = None,
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
        scope = self._build_declared_verification_scope(run_id, declaration)
        task_id = f"task-loop-{declaration.declaration_id}"

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
        # Built by the same composition factory that supplies the runtime's
        # canonical harness, differing only in the narrowed bounds of this slice.
        # No second loop implementation and no second authority object exist.
        # The production ToolExecutor is supplied explicitly. With no declared
        # tool intents and no allow-listed tools nothing reaches it, so the safe
        # default is unchanged; when a tool is authorized the executor keeps
        # owning its permission check and approval.
        harness = self._harness_factory(
            policy=SINGLE_ACTION_LOOP_POLICY,
            tool_executor=self._tool_executor,
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

    def run_accepted_task(self, declaration_id: str) -> TaskRunResponse:
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
        task_id = f"task-accept-{declaration.declaration_id}"
        criteria = spec.bind(run_id=run_id, task_id=task_id)
        criteria.assert_belongs_to(run_id, task_id)

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
            execution_requests=(
                to_execution_request(
                    declaration,
                    profile=self._execution_profile,
                    run_id=run_id,
                ),
            ),
            allowed_execution_commands=tuple(scope.allowed_execution_commands),
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
            },
        )

        harness = self._harness_factory(
            policy=ACCEPTANCE_LOOP_POLICY,
            coordinator=self._acceptance_coordinator(),
            tool_executor=self._tool_executor,
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
