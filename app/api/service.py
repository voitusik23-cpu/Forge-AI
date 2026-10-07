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
from app.runtime.bootstrap import create_runtime
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope, require_active_scope
from app.runtime.run_store import RunStore
from app.tools.approval import ApprovalPolicy, ApprovalResolver
from app.tools.acceptance import AcceptanceCriterion
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
        verification_resolver: Optional[
            Callable[[Task, ProjectExecutionProfile], str | None]
        ] = None,
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
            # A declared verification run is a host process run, not a tool run.
            allowed_tool_ids=frozenset(),
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
