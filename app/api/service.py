"""Core API service bridging HTTP/UI endpoints to Forge Core subsystems."""

from __future__ import annotations

from dataclasses import dataclass

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
from app.agent_runtime.code_writer import (
    STATUS_APPLIED,
    WriteResult,
    apply_changes,
    build_workspace_context,
    render_write_report,
)
from app.agent_runtime.offline_task import (
    OfflineTaskDecisionProvider,
    OfflineWorkspaceVerifier,
    build_offline_task_plan,
    render_offline_task_report,
)
from app.agent_runtime.policy import AgentHarnessPolicy
from app.config.settings import OFFLINE_PROVIDER_NAME
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
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import (
    create_agent_harness,
    create_loop_coordinator,
    create_runtime,
)
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope, require_active_scope
from app.runtime.run_store import RunStateSnapshot, RunStore, project_run_store_root
from app.tasks.specification import TaskSpecification
from app.tools.approval import ApprovalPolicy, ApprovalResolver
from app.tools.acceptance import AcceptanceCriterion, AcceptanceStatus
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, build_default_tool_registry
from app.tools.workspace import Workspace

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.execution.request import ExecutionRequest


#: Distinguishes "the attribute is absent" from "the attribute is present and
#: None". An absent routing value must never be replaced by a guess.
_MISSING = object()


def _dispatcher_route_value(dispatcher: object, name: str) -> object:
    """Read one routing value the caller's orchestrator actually dispatches with.

    Returns ``_MISSING`` when the attribute is absent, so a caller can decide
    what to do instead of silently substituting a different value. The value is
    returned even when it is falsy (``()``, ``False``, ``None``): an empty
    fallback chain and a disabled paid-provider flag are real configuration.
    """
    return getattr(dispatcher, name, _MISSING)


def bind_fabric_to_runtime(
    runtime: RuntimeContext, fabric: CapabilityFabric
) -> RuntimeContext:
    """Return ``runtime`` whose orchestrator dispatches with ``fabric``.

    Provider and model accounting is recorded by the dispatcher, which reads
    ``self._fabric`` and skips the record entirely when it is None
    (``app/orchestrator/dispatcher.py``). The fabric is not a property the dispatcher can
    be handed afterwards, and a runtime supplied by a caller is built by ``create_runtime``
    without one, so a service that keeps its own fabric object observed nothing: the
    response reported empty ``provider_used``/``model_used`` and the dashboard saw no runs.

    The fabric cannot be attached after construction, so a replacement orchestrator
    is built and the orchestrator reference is rebound. Every other runtime field,
    including ``run_executor``, is carried over untouched.

    **What is preserved, and from where.** The replacement carries the *routing
    configuration the caller's own dispatcher was constructed with* -- its
    ``_default_provider``, ``_fallback_chain``, ``_allow_paid_providers``,
    ``_model_registry``, ``_provider_registry``, ``_capabilities_registry``, and
    ``_registry``. It is **not** rebuilt from ``runtime.settings``. Rebuilding
    from settings was a defect: when a caller injected a runtime whose
    orchestrator disagreed with ``runtime.settings``, the rebind silently
    rewrote the routing policy in *either* direction, including the permissive
    one (a caller's ``allow_paid_providers=False`` becoming ``True``). The only
    value taken from settings is a routing value the dispatcher does not expose
    at all, used as a documented fallback; the dispatcher always sets these
    attributes, so in practice nothing is taken from settings.

    This is deliberately tolerant of a runtime the caller assembled by hand: several suite
    doubles stand in for an orchestrator and expose ``dispatch`` but no dispatcher
    internals. Such a runtime is returned unchanged, because there is nothing to rebind and
    replacing a working double would break the caller for no gain.
    """

    orchestrator = runtime.orchestrator
    dispatcher = getattr(orchestrator, "_dispatcher", None)
    if dispatcher is None:
        return runtime
    if getattr(dispatcher, "_fabric", None) is fabric:
        return runtime

    settings = runtime.settings

    def _route(name: str, setting_name: str, default: object) -> object:
        """Prefer the dispatcher's own value; fall back to settings, then default."""
        value = _dispatcher_route_value(dispatcher, name)
        if value is not _MISSING:
            return value
        if settings is not None:
            setting_value = getattr(settings, setting_name, _MISSING)
            if setting_value is not _MISSING:
                return setting_value
        return default

    return replace(
        runtime,
        orchestrator=Orchestrator(
            _route("_registry", "agent_registry", runtime.agent_registry),
            default_provider=_route("_default_provider", "default_provider", "mock"),
            fallback_chain=_route(
                "_fallback_chain", "provider_fallback_chain", ()
            ),
            provider_registry=_route(
                "_provider_registry", "provider_registry", runtime.provider_registry
            ),
            capabilities_registry=_route(
                "_capabilities_registry",
                "provider_capabilities",
                runtime.provider_capabilities,
            ),
            model_registry=_route(
                "_model_registry", "model_registry", None
            ),
            fabric=fabric,
            allow_paid_providers=bool(
                _route("_allow_paid_providers", "allow_paid_providers", False)
            ),
        ),
    )


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


# Bounds for the offline task loop. The offline task path performs read-only
# workspace observation and then reports its verdict, so its control flow is
# "verify, then complete (or fail)" - two actions inside three iterations. No
# hint of a wider bound is intended: the values narrow what the existing policy
# already permits, and `max_execution_attempts` is irrelevant here because the
# offline decision provider never proposes EXECUTE.
OFFLINE_TASK_LOOP_POLICY = AgentHarnessPolicy(
    max_iterations=3,
    max_actions=2,
    max_execution_attempts=1,
    max_revision_attempts=1,
)


# The ``project_state`` key the guarded code writer's outcome occupies inside the
# run's own durable snapshot. It rides beside the existing step-graph, recovery,
# memory and baseline records, so the applied diff, the file list and the
# verification status travel with the run instead of in a second store.
CODE_WRITE_PROJECT_STATE_KEY = "code_write"


# The ``project_state`` key naming which Radox account (by pool position) served the
# run. ``TaskRunResponse`` is byte-pinned by the repository's contract tests, so the
# index travels in the run's durable state -- beside ``code_write`` -- instead of as a
# new response field. It is a position, never a key, a prefix or a hash.
PROVIDER_KEY_INDEX_PROJECT_STATE_KEY = "radox_key_index"


#: The only task category that may write. A write is code work by definition, so a
#: ``write_mode`` flag on a documentation request is not enough on its own.
CODE_WRITE_CATEGORY = "code"


from pathlib import Path


def _utc_now_iso() -> str:
    """Current UTC time as an ISO string, without relying on how ``datetime`` was imported."""

    import datetime as _datetime

    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


# --------------------------------------------------------------- provider states
#
# These three helpers build the *inputs* of the one derivation function
# (``app.agents.providers.provider_state.derive_provider_state``); the vocabulary is
# never decided here. They are module-level rather than service methods on purpose:
# ``list_models_catalogue`` is also called against a stand-in object that only carries
# ``_model_registry`` / ``_runtime``, and a stand-in must keep working.


def _enabled_provider_names(runtime: object) -> Optional[tuple]:
    """The composition's enabled providers, or ``None`` when it cannot be read.

    ``None`` is a real answer, not a failure to hide: without a runtime there is no
    enabled set to read, and the derivation reports that as ``REGISTERED`` (membership
    only) instead of inventing one.
    """

    settings = getattr(runtime, "settings", None)
    enabled = getattr(settings, "enabled_providers", None)
    if enabled is None:
        return None
    try:
        return tuple(str(name).strip() for name in enabled if str(name).strip())
    except TypeError:
        return None


def _provider_credentials_present(env_names) -> Dict[str, bool]:
    """Which of these key variables resolve to a non-empty value.

    Presence only: one boolean per name, so nothing this returns can reconstruct a key,
    its length or a prefix. An unreadable secret file answers "absent" rather than
    raising -- a status payload must never fail because the store could not be read.
    """

    from app.config.secrets import SecretStore

    try:
        store = SecretStore()
    except Exception:  # noqa: BLE001 - an unusable store means "absent", never a crash
        store = None
    present: Dict[str, bool] = {}
    for raw in env_names:
        name = str(raw or "").strip()
        if not name:
            present[name] = False
            continue
        if name in present:
            continue
        if store is None:
            present[name] = False
            continue
        try:
            present[name] = bool(store.get_secret(name))
        except Exception:  # noqa: BLE001 - a malformed secret file is "absent"
            present[name] = False
    return present


def _observed_provider_probes(health_monitor: object) -> Dict[str, Dict[str, Any]]:
    """Provider probe results already recorded in this process, keyed by provider name.

    Cache-only on purpose: this read reports what is already known and starts nothing, so
    asking for the catalogue can never dial a provider. A provider that has not been
    examined is absent here and is reported ``NOT_CHECKED`` by the derivation.
    """

    cached_statuses = getattr(health_monitor, "cached_statuses", None)
    if not callable(cached_statuses):
        return {}
    try:
        observed = cached_statuses() or {}
    except Exception:  # noqa: BLE001 - an unreadable cache is "nothing observed"
        return {}
    probes: Dict[str, Dict[str, Any]] = {}
    for name, status in observed.items():
        clean_name = str(name or "").strip().lower()
        if not clean_name:
            continue
        raw = getattr(status, "status", None)
        value = getattr(raw, "value", raw)
        if not isinstance(value, str) or not value.strip():
            continue
        probes[clean_name] = {"status": value.strip().upper()}
    return probes


class RunIdempotencyConflict(RuntimeError):
    """The same idempotency key arrived with a materially different request."""


class IdempotencyUnavailable(RuntimeError):
    """A key was supplied but the durable ledger cannot be used, so nothing was dispatched."""


@dataclass(frozen=True)
class RunDispatchResult:
    """A dispatch response plus the facts that do not belong to the HTTP contract.

    The response model is byte-pinned by the repository's guard tests, so these travel as
    ``X-Forge-*`` headers instead of new body fields. A header cannot express authority.
    """

    response: "TaskRunResponse"
    idempotent_replay: bool = False
    history_recorded: bool = True
    history_error: str = ""
    usage_basis: str = "UNKNOWN"
    cost_basis: str = "UNKNOWN"


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
        authorized_project_id: Optional[str] = None,
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
        # Deliberately allowed to stay ``None``: "no server-side approval policy was
        # composed" is a real, fail-closed state that `RunExecutor` reports as
        # ``approval_policy_required``, and two existing contract tests assert exactly
        # that (``test_missing_approval_policy_refuses_to_execute``,
        # ``test_default_service_requests_no_execution``). Replacing it with a default
        # policy here would have silently turned "no approval configured" into "approval
        # configured" -- a permissive reading of a security-relevant default. A
        # composition that wants a working policy supplies one explicitly, which is what
        # ``create_api_app`` does.
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

        # The fabric is created FIRST, before the runtime, because the dispatcher records
        # provider usage through it: a runtime built without one produces empty
        # `provider_used`/`model_used` and a dashboard that sees no runs. `bind_fabric_to_runtime`
        # attaches this exact object to the orchestrator that will dispatch, so the service
        # and the runtime never disagree about which fabric is authoritative.
        self._fabric = fabric or CapabilityFabric(
            workspace=self._workspace,
            tool_registry=self._tool_registry,
        )
        # Scoped to THIS workspace. ``RunStore()`` with no root resolves to a per-USER
        # directory, so two projects on one machine shared it: a run id used in one was
        # replayed as the other's ``COMPLETED`` result while its own artifact was never
        # created. The space is derived from the trusted workspace this service was
        # constructed with -- never from anything the caller sends.
        self._run_store = run_store or RunStore(
            root=project_run_store_root(self._workspace.root)
        )

        resolved_runtime = runtime or create_runtime(
            fabric=self._fabric, tool_registry=self._tool_registry
        )
        # The runtime's orchestrator must dispatch with the same fabric the service reads
        # accounting from. `bind_fabric_to_runtime` returns a runtime whose orchestrator
        # carries this exact object; binding it once here keeps every later reference
        # consistent.
        bound_runtime = bind_fabric_to_runtime(resolved_runtime, self._fabric)
        # The health monitor is built from the RESOLVED runtime's settings, never from the
        # ambient environment. `ProviderHealthMonitor` opens real TCP probes to provider
        # endpoints, so a monitor that resolves its own environment would keep probing even
        # when the composition is a test-mode runtime whose providers were all disabled --
        # the offline guarantee has to come from the composition, not from every caller
        # remembering to set `FORGE_ENV=test`. Passing the settings makes test mode
        # structural, and leaves production probing untouched because the resolved settings
        # there are the ordinary development/production ones.
        self._health_monitor = ProviderHealthMonitor(
            settings=getattr(resolved_runtime, "settings", None)
        )
        # The shared ToolExecutor must reach the executor that actually dispatches
        # tools. RunExecutor carries no mutable state beyond immutable references,
        # so rebinding it to the same orchestrator preserves behavior exactly.
        self._runtime = replace(
            bound_runtime,
            run_executor=RunExecutor(
                bound_runtime.orchestrator,
                tool_executor=self._tool_executor,
            ),
        )

        self._dashboard_service = dashboard_service or CostDashboardService(
            fabric=self._fabric,
            health_monitor=self._health_monitor,
        )
        self._active_project_id: Optional[str] = authorized_project_id

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
        """The runtime this service dispatches through.

        Exposed so a trusted in-process caller can *observe* a dispatch (for
        example to read the ``ExecutionResult`` a declared verification really
        produced) instead of re-deriving it, and can put an equivalent runtime
        back afterwards. Assigning a runtime is a composition act: it replaces
        the registries, the executor, and the harness wholesale, so a caller
        that does it owns the result. It is not reachable from the HTTP
        contract, and it grants no authority -- every dispatch still passes the
        frozen ``RunScope``, the approval decision, the execution policy, and
        the workspace check.
        """
        return self._runtime

    @runtime.setter
    def runtime(self, runtime: RuntimeContext) -> None:
        if not isinstance(runtime, RuntimeContext):
            raise TypeError("runtime must be a RuntimeContext")
        self._runtime = runtime

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

    @property
    def workspace(self) -> Workspace:
        """The workspace this service was composed with, for in-process callers.

        Read-only accessor. It exposes the object the service already owns; it
        grants nothing, and it is not reachable from the HTTP contract.
        """
        return self._workspace

    @property
    def declarations(self) -> Mapping[str, ExecutionDeclaration]:
        """The operator-declared executions, read-only.

        The mapping is a copy taken at composition time, so a caller cannot
        widen the declaration set. Declarations are data, never a gate: adding
        one to a copy would not reach any authority check.
        """
        return self._declarations

    @property
    def acceptance_specs(self) -> Mapping[str, AcceptanceSpec]:
        """The operator-declared acceptance specs, read-only.

        Same rule as ``declarations``: this is a copy, and a criterion copied out
        of it carries no authority.
        """
        return self._acceptance_specs

    @property
    def loop_coordinator_factory(self) -> Callable[..., object]:
        """The composition-supplied coordinator factory.

        Exposed so a trusted in-process caller can observe the coordinator the
        acceptance slice dispatches through, and assign it back to restore the
        composition. It mints no authority: the coordinator still performs every
        ``RunScope``, approval, policy, and workspace check on every dispatch.
        """
        return self._loop_coordinator_factory

    @loop_coordinator_factory.setter
    def loop_coordinator_factory(self, factory: Callable[..., object]) -> None:
        if not callable(factory):
            raise TypeError("loop_coordinator_factory must be callable")
        self._loop_coordinator_factory = factory

    @property
    def execution_profile(self) -> ProjectExecutionProfile:
        """The profile every RunScope of this service is built from.

        Read-only. It is the ceiling, never a grant: exposing it lets an
        operator-facing view report *which* commands the deployment admits, and
        reporting a ceiling cannot raise it.
        """
        return self._execution_profile

    # ------------------------------------------------------------------
    # composition accessor for the operating surface
    #
    # This method hands out the *ingredients* of a step-graph or recovery
    # composition and nothing else. It deliberately does NOT construct the runner,
    # the coordinator or the backing adapter. The API layer is asserted by
    # ``test_api_layer_has_no_direct_adapter_or_process_access`` to reach neither
    # the execution adapter nor process spawning, and that boundary is kept: this
    # service delegates, it does not spawn. ``app/api/operating_surface.py`` builds
    # the composition from these values.
    # ------------------------------------------------------------------
    def step_graph_composition(self) -> Optional[Dict[str, Any]]:
        """The values a step-graph runner needs, or ``None`` when not composable.

        Fails closed on two conditions:

        * no runtime carrying a composed run executor;
        * **no injected idempotency guard**. The process-wide default guard shares
          one ledger root across every caller, and this project has already measured
          false failures from shared default roots. An embedding composition that
          wants the HTTP step-graph route supplies its own guard, exactly as the
          tests do.

        The returned mapping reports ``approval_policy`` **as it is**, including
        ``None``. It does not substitute a default: an unresolved policy must never
        silently mean "no approval needed". The consumer of this mapping refuses when
        the policy is absent, which is the same fail-closed answer ``RunExecutor``
        and ``StepGraphRunner`` already give.

        The values returned carry no authority of their own: the runner still
        re-checks ``RunScope``, ``ApprovalPolicy``, ``ExecutionCoordinator`` and the
        workspace on every dispatch.
        """

        runtime = self._runtime
        if runtime is None or getattr(runtime, "run_executor", None) is None:
            return None
        if self._idempotency_guard is None:
            return None
        return {
            "runtime": runtime,
            "workspace": self._workspace,
            "execution_profile": self._execution_profile,
            "declarations": dict(self._declarations),
            "run_store": self._run_store,
            "idempotency_guard": self._idempotency_guard,
            # `None` stays `None`, verbatim. Whether an absent policy is acceptable
            # is the consumer's decision, and the consumer refuses -- it does not
            # weaken it here.
            "approval_policy": self._approval_policy,
            "approval_resolver": self._approval_resolver,
            "allowed_tool_ids": self._effective_tool_ids(),
        }

    def recovery_composition(self) -> Optional[Dict[str, Any]]:
        """The values a ``RecoveryPlanner`` needs, or ``None`` when not composable."""

        if self._idempotency_guard is None:
            return None
        return {
            "run_store": self._run_store,
            "idempotency_guard": self._idempotency_guard,
            "declarations": dict(self._declarations),
            "workspace": self._workspace,
        }

    def can_run_step_graph(self) -> bool:
        """Whether a step-graph run is composed. Fail-closed default: ``False``.

        Requires a composed run executor, an injected idempotency guard, **and a
        stated approval policy**. A deployment with no approval position cannot run
        step graphs, because the alternative is running declared host commands under
        a policy nobody stated.
        """

        return (
            self._approval_policy is not None
            and self.step_graph_composition() is not None
        )

    def can_plan_recovery(self) -> bool:
        return self.recovery_composition() is not None

    def can_run_accept_cycle(self) -> bool:
        """Whether the accept cycle could run at all.

        Requires a harness and an acceptance spec for at least one declaration:
        without trusted criteria nothing can legitimately be accepted.

        It also requires a **stated approval policy**, for the same reason
        ``can_run_declared_verification`` does. The accept cycle dispatches host
        commands through the same chain and receives ``self._approval_policy``;
        ``OperatingSurface.run_accepted_task`` refuses outright when it is ``None``
        (a controlled configuration error, never a 409). Measured before this
        requirement was added: with no policy composed, the flag reported ``True``
        while ``POST /api/product/accept`` returned 500
        ``approval_policy_required`` and nothing ran -- a capability signal that
        disagreed with the behaviour it describes, which is the one thing this
        surface may not do.
        """

        if getattr(self._runtime, "harness", None) is None:
            return False
        if self._approval_policy is None:
            return False
        return any(
            declaration_id in self._acceptance_specs
            for declaration_id in self._declarations
        )

    def can_run_declared_verification(self) -> bool:
        """Whether a declared verification could actually execute anything.

        Requires an operator declaration **and a stated approval policy**. The
        policy requirement is not decoration: every dispatch passes
        ``self._approval_policy`` into the execution chain, and a ``None`` policy is
        an unconditional denial there. Reporting ``True`` on declarations alone told
        an operator "declared verification is available" on a composition where
        every single call was refused -- a capability flag that disagreed with the
        behaviour it describes, which is exactly the kind of claim this surface is
        not allowed to make.
        """

        return bool(self._declarations) and self._approval_policy is not None

    def get_run_record(self, run_id: str) -> Optional[Dict[str, object]]:
        """Return the durable history of a Run, or None when unknown.

        Strictly read-only. It reports what was persisted and never resumes,
        retries, or re-executes an interrupted Run, and never returns secrets or
        raw payloads: the stored history is already sanitized by the canonical
        event-metadata policy.

        The response carries one added key, ``classification``, computed at read time by
        `RunStore.classify_record`. It answers "what does this record actually prove",
        which the raw `state`/`status` pair does not: a record claiming to be in progress
        has no heartbeat, owner or lease, so it classifies as ``INDETERMINATE`` rather
        than being reported as a live run. Every key `summary()` already returned is
        unchanged, so an existing consumer keeps working; the added key is omitted
        entirely rather than guessed when classification cannot be computed.
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
        summary = record.summary()
        try:
            classification = self._run_store.classify_record(run_id.strip())
        except Exception:  # noqa: BLE001 - a read path must not fail on classification
            # Classification is an addition, never a precondition: if it cannot be
            # computed the record is still returned, without the key rather than with a
            # fabricated verdict.
            return summary
        summary["classification"] = classification.to_dict()
        return summary

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
                # The build identity travels beside the provider facts, so an operator
                # can tell which build answered without a second request.
                **self.get_version_info(),
            },
        )

    def get_version_info(self) -> Dict[str, Any]:
        """Version, commit and build timestamps of this running backend (read-only).

        The values come from ``app.build_info``: the commit was read once, when the
        process started, and every part of the payload is a timestamp, a hexadecimal
        object name, a version string, or one fixed status word. No secret and no
        environment value is included, and an unresolved fact is ``"unknown"`` rather
        than a guess.
        """

        from app.build_info import build_version_payload

        return build_version_payload(
            backend="connected" if self._runtime is not None else "disconnected"
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
            tokens=None,
            cost=None,
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
        self,
        store: RunStore,
        run_id: str,
        task_id: str,
        result: object,
        *,
        event_count: Optional[int] = None,
    ) -> None:
        """Persist the loop's terminal snapshot with the existing state writer.

        The harness produces its own result object rather than an orchestrator
        ``Run``, so the snapshot is derived from that result directly instead of
        from ``record_run_state``. It uses the same ``RunStateSnapshot`` type and
        the same atomic writer, so the durable history keeps one shape and the
        ``task_id`` matches the events exactly.

        ``event_count`` lets a caller that also wrote its own events around the
        loop record the count of the **whole** durable trail instead of only the
        loop's share; when it is omitted the loop's own event count is written,
        which is what every existing caller already recorded.
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
                    event_count=(
                        len(events) if event_count is None else max(0, int(event_count))
                    ),
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
            tokens=None,
            cost=None,
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

        No generative provider call is made. The decision this run executes is the
        operator's declaration, which was resolved and validated above; a second,
        model-produced decision would be billable, would be discarded, and would
        wrongly gate an already-authorized command on a model's availability. The
        ``RunExecutor`` is told the decision already exists, so it records that
        fact and goes straight to host execution -- through the same ``RunScope``,
        ``ApprovalPolicy``, ``ExecutionCoordinator``, and adapter as before.
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
            # The decision for this run already exists: it is the operator's
            # declaration, resolved and validated above. Asking a model for a
            # *second* decision would be a pointless, billable provider call whose
            # output this entry point discards, and it would put a model in the
            # position of gating an already-authorized declared command -- when
            # that dispatch failed, the declared command silently did not run.
            #
            # `False` removes exactly that generative call. It removes no
            # authorization: the frozen `RunScope`, this service's mandatory
            # `ApprovalPolicy`, the execution coordinator (still the only component
            # that may mint an authorized execution), and the local adapter all
            # still run, and the declared command's real exit code is still the
            # only thing that decides the reported state.
            generate_decision=False,
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
            # A proven zero, not an invention: measured by
            # test_the_declared_verification_makes_no_provider_call and its two siblings,
            # this path performs no provider call, so nothing was consumed.
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

    def cost_states(self) -> Dict[str, Any]:
        """The six distinguishable states of every provider's reported cost.

        Read-only and additive: the protected provider summary is not changed, and the
        states are derived from it on demand.
        """

        from app.dashboard.cost_truth import NO_USAGE_API, cost_state_report, state_counts

        report = self.get_dashboard_report(force_refresh=False)
        # The dashboard returns a DashboardReport dataclass whose providers are typed summaries,
        # so each one is serialised with its own to_dict before the states are derived.
        providers = {
            str(entry.get("provider", "")): entry
            for entry in (item.to_dict() for item in report.providers)
            if isinstance(entry, dict)
        }
        # Providers the dashboard does not carry (they are outside the pinned registry) are still
        # part of the answer: they publish no cost through any API, so their state is stated
        # explicitly instead of being left out.
        for name in NO_USAGE_API:
            providers.setdefault(
                name, {"provider": name, "total_runs": None, "provider_reported_cost": None}
            )
        entries = cost_state_report(providers)
        return {
            "providers": entries,
            "counts": state_counts(entries),
            "source": "/api/dashboard providers[].provider_reported_cost",
            "states": [
                "KNOWN_ZERO",
                "KNOWN_NONZERO",
                "UNKNOWN",
                "ERROR",
                "MALFORMED",
                "UNSUPPORTED",
            ],
            "note": (
                "a zero in the numeric field alone cannot tell a reported zero from silence; "
                "this endpoint states which of the six conditions applies"
            ),
            "checked_at": _utc_now_iso(),
        }

    def provider_balance(
        self, provider: Optional[str] = None, *, force: bool = True
    ) -> Dict[str, Any]:
        """Balance reports from official provider endpoints, with session history.

        Every figure here is either observed at the named source and timestamped, or
        explicitly absent. A provider with no balance endpoint is reported as such and
        never with a zero.
        """

        from app.dashboard.provider_balance import ProviderBalanceProbe

        probe = getattr(self, "_balance_probe", None)
        if probe is None:
            probe = ProviderBalanceProbe()
            self._balance_probe = probe
        if provider:
            report = probe.report(provider, force=force)
            return {
                "providers": {report.provider: report.to_dict()},
                "history": {report.provider: probe.history(report.provider)},
                "probes_enabled": probe.checks_enabled,
                "checked_at": _utc_now_iso(),
                "history_scope": "this process only; not a billing archive",
            }
        reports = probe.reports(force=force)
        return {
            "providers": {name: item.to_dict() for name, item in reports.items()},
            "history": {name: probe.history(name) for name in reports},
            "probes_enabled": probe.checks_enabled,
            "checked_at": _utc_now_iso(),
            "history_scope": "this process only; not a billing archive",
        }

    def radox_keys_status(self) -> Dict[str, Any]:
        """Read-only report of the Radox key pool and its per-position ledger.

        The keys are re-read from the process environment and the repository ``.env``
        on every call and only *counted*; nothing about a key -- its value, a hash, a
        prefix or its length -- can reach this payload. ``active_index`` is the pool
        position that will serve the next request -- a rotation cursor, not an
        identity and not a claim that the position is healthy; ``keys[].disabled``
        says whether that position is inside a 429 cooldown.
        """

        from app.agents.providers.radox_keys import key_pool_payload
        from app.config.secrets import SecretStore

        return key_pool_payload(SecretStore())

    def list_models_catalogue(self) -> Dict[str, Any]:
        """Read-only catalogue of the model registry: providers, models and accounts.

        Three separate facts are kept separate here, because conflating them is how a UI ends
        up lying:

        * **membership** -- the model is defined in the registry;
        * **configuration** -- an account references a secret (reported as a boolean, never as
          the reference or the value);
        * **availability** -- reported per provider as ``providers[].state``, derived by
          ``app.agents.providers.provider_state.derive_provider_state`` from membership, the
          composition's ``enabled_providers``, whether the provider's key variable resolves to a
          non-empty value, and the last probe **already observed** in this process. This read
          starts no probe and opens no socket, so an unexamined provider is ``NOT_CHECKED``
          rather than being silently probed here.

        The registry is resolved defensively: the service holder, then the runtime, then the
        built-in default registry. When none is available the catalogue is empty rather than
        invented.
        """

        from app.agents.providers.provider_state import (
            derive_provider_state,
            lookup_probe,
            provider_state_counts,
        )

        registry = getattr(self, "_model_registry", None)
        if registry is None:
            runtime = getattr(self, "_runtime", None)
            registry = getattr(runtime, "model_registry", None) if runtime is not None else None
        if registry is None:
            try:
                from app.agents.providers.model_registry import create_default_registry

                registry = create_default_registry()
            except Exception:  # noqa: BLE001 - an absent registry is reported, not faked
                registry = None

        providers: List[Dict[str, Any]] = []
        models: List[Dict[str, Any]] = []
        accounts: List[Dict[str, Any]] = []
        decisions = []
        if registry is not None:
            registry_providers = registry.list_providers()
            runtime = getattr(self, "_runtime", None)
            enabled_names = _enabled_provider_names(runtime)
            credentials = _provider_credentials_present(
                provider.default_secret_env for provider in registry_providers
            )
            probes = _observed_provider_probes(getattr(self, "_health_monitor", None))
            for provider in registry_providers:
                secret_env_name = str(provider.default_secret_env or "").strip()
                decision = derive_provider_state(
                    enabled_providers=enabled_names,
                    provider_id=provider.provider_id,
                    credential_env_var=secret_env_name,
                    credential_present=bool(credentials.get(secret_env_name)),
                    probe=lookup_probe(probes, provider.provider_id),
                )
                decisions.append(decision)
                providers.append(
                    {
                        "provider_id": provider.provider_id,
                        "display_name": provider.display_name,
                        "protocol": str(provider.protocol),
                        "is_gateway": bool(provider.is_gateway),
                        "model_count": len(registry.list_models(provider.provider_id)),
                        "secret_env_name": provider.default_secret_env,
                        "has_base_url": bool(provider.default_base_url),
                        # Membership is not availability: this is the derived state, and the
                        # reason code it rests on. Neither can carry a key value.
                        "state": decision.state.value,
                        "reason": decision.reason,
                        "probed": bool(decision.probed),
                    }
                )
            for model in registry.list_models():
                models.append(
                    {
                        "provider_id": model.provider_id,
                        "model_id": model.model_id,
                        "display_name": model.display_name or model.model_id,
                        "context_window": int(model.context_window),
                        "max_output_tokens": int(model.max_output_tokens),
                        "capabilities": sorted(str(item) for item in model.capabilities),
                        "cost_tier": str(model.cost_tier),
                        "supports_system_prompt": bool(model.supports_system_prompt),
                    }
                )
            for account in registry.list_accounts():
                accounts.append(
                    {
                        "account_id": account.account_id,
                        "provider_id": account.provider_id,
                        "enabled": bool(account.enabled),
                        "priority": int(account.priority),
                        # A reference is not a secret, but the value and the reference itself
                        # are both withheld: only whether one is configured is reported.
                        "secret_configured": bool(account.secret_ref),
                    }
                )
        state_counts = provider_state_counts(decisions)
        return {
            "providers": providers,
            "models": models,
            "accounts": accounts,
            "counts": {
                "providers": len(providers),
                "models": len(models),
                "accounts": len(accounts),
                # "Registered" is every provider in this payload; "ready" is how many the
                # derivation reports READY. They are counted separately because a registry
                # entry on its own says nothing about availability.
                "providers_ready": state_counts["ready_total"],
            },
            "provider_states": state_counts,
            "provider_state_note": (
                "providers[].state is one of REGISTERED, READY, NO_CREDENTIALS, "
                "NETWORK_BLOCKED, NO_CREDITS, NOT_CHECKED, DISABLED, ERROR. It is derived "
                "from registry membership, the composition's enabled_providers, whether the "
                "provider's key variable resolves to a non-empty value, and the last probe "
                "already observed in this process; this read starts no probe. No key value, "
                "length, prefix or hash is reported anywhere in this payload."
            ),
            "source": "ModelRegistry" if registry is not None else "unavailable",
            "availability_note": (
                "Registry membership is not availability: availability comes from "
                "providers[].state, and READY is only reported from a probe that reported "
                "access. A provider that has not been examined is NOT_CHECKED."
            ),
        }

    def _run_store_write_reason(self) -> str:
        """A short, path-free reason why a run record could not be persisted.

        Deliberately does not leak a filesystem path or an internal traceback: the caller only
        needs to know which class of problem it is. Writability is probed directly, because the
        original failure happens inside a ``try/except`` whose contract is to let the run finish.
        """

        root = None
        for attribute in ("root", "_root"):
            candidate = getattr(self._run_store, attribute, None)
            if candidate:
                root = candidate
                break
        if root is None:
            return "run history was not persisted (store root unknown)"
        try:
            directory = Path(root)
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return "run history was not persisted (store rejected the write)"
        except PermissionError:
            return (
                "run history was not persisted: the run store root is not writable here; "
                "set FORGE_RUN_STORE_ROOT to a writable directory"
            )
        except OSError as error:  # noqa: BLE001 - the class of failure is the useful part
            return f"run history was not persisted: {type(error).__name__}"

    # ------------------------------------------------------------------ usage basis
    @staticmethod
    def derive_usage_basis(attempts, *, mock_provider: str = "mock") -> tuple[str, str]:
        """``(usage_basis, cost_basis)`` from the evidence a finished run actually holds.

        Never guesses: a value that is not supported by evidence is reported as ``UNKNOWN`` so a
        caller can render "unknown" instead of a confident zero. The attempt sequence is read
        defensively because several providers and legacy records populate different attributes.
        """

        if not attempts:
            return "UNKNOWN", "UNKNOWN"
        latest = attempts[-1]
        provider = str(
            getattr(latest, "provider", "")
            or getattr(latest, "provider_name", "")
            or getattr(latest, "provider_id", "")
        )
        is_mock = bool(provider) and provider.lower() == mock_provider.lower()

        def first_present(*names):
            for name in names:
                value = getattr(latest, name, None)
                if value not in (None, "", 0):
                    return value
            return None

        reported_tokens = first_present(
            "input_tokens", "prompt_tokens", "total_tokens", "tokens_reported", "usage"
        )
        reported_cost = first_present("provider_reported_cost", "actual_cost", "cost_reported")
        estimated_cost = first_present("estimated_cost", "cost", "total_cost")

        if is_mock:
            # A mock execution is labelled as such even though its numbers are internally real.
            return "MOCK", "MOCK"
        if reported_tokens is not None or reported_cost is not None:
            return "ACTUAL", "ACTUAL" if reported_cost is not None else "UNKNOWN"
        if estimated_cost is not None:
            return "ESTIMATED", "ESTIMATED"
        return "UNKNOWN", "UNKNOWN"

    # ------------------------------------------------------------------ idempotency
    def _idempotency_ledger_instance(self):
        """The durable ledger, rooted beside the run store so it survives a restart."""

        ledger = getattr(self, "_idempotency", None)
        if ledger is not None:
            return ledger
        from app.agent_runtime.idempotency import IdempotencyLedger

        root = None
        for attribute in ("root", "_root"):
            candidate = getattr(self._run_store, attribute, None)
            if candidate:
                root = candidate
                break
        self._idempotency = IdempotencyLedger(
            root=(Path(root) / "idempotency") if root else None
        )
        return self._idempotency

    @staticmethod
    def _request_fingerprint(req: "TaskRunRequest", project_id: str) -> str:
        """A stable fingerprint of the logical request, used to detect a real conflict."""

        import hashlib

        material = "\u0000".join(
            (
                str(req.description or "").strip(),
                str(req.category or ""),
                str(project_id or ""),
                str(req.provider_name or ""),
            )
        )
        if bool(getattr(req, "write_mode", False)):
            # A write-mode dispatch is a different logical operation from a read-only
            # one carrying the same text: it changes files, so it must not replay a
            # stored read-only result under the same key. The marker is appended only
            # in write mode, which keeps every existing fingerprint byte-identical.
            material += "\u0000write_mode"
        requested_model = getattr(req, "model_name", None)
        if requested_model:
            # The same text on a different model is a different logical operation: a
            # caller that names a model and changes it between retries must not be
            # handed the earlier model's stored answer. Appended only when a model was
            # actually named, so a request without one fingerprints exactly as before.
            material += "\u0000model:" + str(requested_model)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _operation_identity(
        self, req: "TaskRunRequest", project_id: str, idempotency_key: str
    ):
        from app.agent_runtime.criterion_identity import TaskIdentity
        from app.agent_runtime.idempotency import OperationClass, OperationIdentity

        fingerprint = self._request_fingerprint(req, project_id)
        scoped_key = f"{project_id}:{idempotency_key}"
        # The ledger derives the canonical operation fingerprint from the identity, and rejects a
        # caller-supplied one that disagrees. The request fingerprint therefore lives inside the
        # task identity, which participates in that derivation: a different request produces a
        # different identity, which is what the conflict check relies on.
        return OperationIdentity(
            operation_class=OperationClass.TASK_RUN,
            idempotency_key=scoped_key,
            task_identity=TaskIdentity(
                task_id=req.task_id or scoped_key, task_fingerprint=fingerprint
            ),
        )

    def _replay_response(self, record, req: "TaskRunRequest", project_id: str) -> "TaskRunResponse":
        """Answer a replay with the original identity, and start nothing."""

        run_id = getattr(record, "run_id", "") or ""
        state = ""
        tokens: Optional[int] = None
        cost: Optional[float] = None
        provider = ""
        model = ""
        record_payload = self.get_run_record(run_id) if run_id else None
        if isinstance(record_payload, dict):
            summary = record_payload.get("summary")
            if isinstance(summary, dict):
                state = str(summary.get("state") or summary.get("status") or "")
            else:
                state = str(record_payload.get("state") or record_payload.get("status") or "")
            accounting = record_payload.get("accounting")
            if isinstance(accounting, dict):
                # An absent field is unknown, not zero; a present one is preserved.
                recorded_tokens = accounting.get("total_tokens")
                recorded_cost = accounting.get("total_cost")
                tokens = (
                    int(recorded_tokens)
                    if isinstance(recorded_tokens, (int, float))
                    else None
                )
                cost = (
                    float(recorded_cost)
                    if isinstance(recorded_cost, (int, float))
                    else None
                )
                provider = str(accounting.get("provider") or "")
                model = str(accounting.get("model") or "")
        return TaskRunResponse(
            run_id=run_id,
            task_id=req.task_id or "",
            project_id=project_id,
            state=state or str(getattr(record, "state", "") or "UNKNOWN"),
            success=state.upper() == "COMPLETED",
            output="",
            error=None,
            tokens=tokens,
            cost=cost,
            provider_used=provider,
            model_used=model,
        )

    def bind_authorized_project(self, project_id: str) -> None:
        """Bind this instance to the project the server authorized at composition time.

        Core serves one authorized project per instance, exactly as ``app.state.forge_project_id`` does,
        so the binding is set once at startup rather than carried per request; an empty value is refused
        and project-sensitive work fails closed while the service is unbound.
        """

        if not project_id:
            raise ValueError("an authorized project is required to bind this service")

        # One project per instance: the composition binds the server's decision once. Rebinding to a
        # different project would silently move an instance already authorized for another one.
        existing = getattr(self, "_active_project_id", None)
        if existing and existing != project_id:
            raise PermissionError(
                "this service is already bound to a different authorized project"
            )
        self._active_project_id = project_id

    def run_task(self, req: TaskRunRequest) -> TaskRunResponse:
        """Backwards-compatible entry point: the response alone."""

        return self.run_task_detailed(req).response

    # ---------------------------------------------------------------- provider
    def _selected_provider_name(self, req: TaskRunRequest) -> str:
        """The provider this task will actually run on.

        The selection is the same one the dispatcher makes: an explicitly named
        provider wins, otherwise the composition's configured default is used.
        Reading it here - rather than re-deriving a routing policy - is what keeps
        "the offline provider was selected" and "the offline path ran" the same
        fact. It is read from the runtime the dispatcher itself holds, so a
        service whose runtime was supplied by a caller resolves it that caller's
        way.
        """

        requested = (req.provider_name or "").strip()
        if requested:
            return requested
        # The configured default decides. It is read from the runtime settings first, because those are
        # the values the operator wrote (``FORGE_DEFAULT_PROVIDER`` in the repository ``.env``); the
        # dispatcher's own copy is a fallback for a runtime whose settings were supplied without one.
        # Reading the dispatcher first silently ignored an operator's choice: settings said "radox"
        # while every unnamed request still ran on the previously configured provider.
        settings = getattr(self._runtime, "settings", None)
        configured = getattr(settings, "default_provider", None)
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
        dispatcher = getattr(
            getattr(self._runtime, "orchestrator", None), "_dispatcher", None
        )
        configured = getattr(dispatcher, "_default_provider", None)
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
        return ""

    # ----------------------------------------------------------------- offline
    @staticmethod
    def _offline_project_discovery() -> object:
        """The bounded observation stage the offline task loop runs with.

        Composition-owned, exactly like every other harness stage: the limits are
        declared here, not per request, and a narrower limit can only make the
        observation smaller. The default scanner bound (5000 files, 50 MB, depth
        32) is measured at ~9 s on this repository, which is too slow for one
        interactive task; the bound below observes the same workspace structure
        within the request budget.
        """

        from app.agent_runtime.project_discovery import ProjectDiscovery
        from app.understanding.models import ScanLimits

        return ProjectDiscovery(
            limits=ScanLimits(
                max_file_size_bytes=128 * 1024,
                max_total_bytes=4 * 1024 * 1024,
                max_file_count=300,
                max_depth=6,
                parse_timeout_seconds=0.5,
            )
        )

    def _offline_telemetry_sink(self) -> object:
        """The durable physical-telemetry sink the offline task loop writes to.

        The composition's own sink is used when it supplied one. Otherwise the
        sink is rooted beside this service's run store -- the same durable
        location the run's history already uses -- instead of the process default
        per-user root. The default root is measurably not writable in this
        environment, and a sink failure is (correctly) reported as a
        ``HARNESS_FAILED`` event: on a client-reachable path that would print a
        failure marker after a run that succeeded, so the measurement is kept
        where the history it belongs to is kept.
        """

        from app.agent_runtime.physical_telemetry import FileTelemetrySink

        root = getattr(self._run_store, "root", None)
        if not root:
            return FileTelemetrySink()
        return FileTelemetrySink(root=Path(root) / "telemetry")

    # -------------------------------------------------------------- write mode
    def _write_mode_enabled(self, req: TaskRunRequest, category: object) -> bool:
        """Whether this request opted into the guarded write path.

        Two independent conditions must hold: the caller asked for it, and the task
        is code work. The flag is never inferred from the description, the
        provider, the project, or the model answer, so a caller that does not send
        it keeps exactly the read-only behaviour it had before write mode existed.
        """

        if not bool(getattr(req, "write_mode", False)):
            return False
        name = getattr(category, "value", category)
        return str(name or "").strip().lower() == CODE_WRITE_CATEGORY

    def _write_task_description(self, description: str) -> str:
        """The task text handed to the model: the task plus bounded context.

        Only the *names* of the workspace's files are added, and only in write
        mode. The listing is produced by the code writer's own bounded walk, so it
        cannot become a way to read credential material, ``.git``, the virtualenv,
        dependencies, or the run store.
        """

        context = build_workspace_context(self._workspace)
        if not context:
            return description
        return f"{description}\n\n{context}"

    @staticmethod
    def _offline_write_answer(plan: object) -> str:
        """The deterministic plan text used as the offline "model answer".

        The offline provider cannot answer for work, so in write mode the answer
        this path applies is the plan it already derived from the task text -
        verbatim, with no fenced block and no invented file. The whole pipeline
        (parse -> validate -> apply -> verify -> persist) is therefore exercised
        end to end, and the honest outcome is ``no_changes``: nothing is written,
        because nothing in that text names a file to write.
        """

        lines = ["Deterministic offline plan (no provider was called):"]
        for index, step in enumerate(getattr(plan, "steps", ()) or (), start=1):
            kind = getattr(step, "kind", "")
            kind_name = getattr(kind, "value", kind)
            target = getattr(step, "target", "") or "."
            title = getattr(step, "title", "")
            lines.append(f"{index}. [{kind_name}] {title} -> {target}")
        return "\n".join(lines)

    def _apply_model_changes(
        self,
        *,
        answer: object,
        workspace: object,
        run_id: str,
        task_id: str,
        run_store: Optional[RunStore],
    ) -> WriteResult:
        """Run one model answer through the guarded writer and record its outcome.

        The workspace is the frozen scope's own workspace, so the files land in the
        perimeter this run already holds. An unexpected failure is a refusal, never
        a success: only the writer's own statuses are reported, and the run's
        durable record is written from them.
        """

        try:
            result = apply_changes(answer, workspace=workspace, run_id=run_id)
        except Exception as error:  # noqa: BLE001 - a writer failure must not write
            result = WriteResult(
                status=STATUS_REJECTED,
                reason=f"writer_error:{type(error).__name__}",
            )
        self._record_write_outcome(run_store, run_id, task_id, result)
        return result

    def _record_write_outcome(
        self,
        store: Optional[RunStore],
        run_id: str,
        task_id: str,
        result: WriteResult,
    ) -> None:
        """Persist the write outcome in the run's own durable record.

        The status, the applied diff, the file list and the verification status go
        into the existing ``RunStateSnapshot.project_state`` (merged beside the
        other records, exactly as the memory, step-graph and recovery stores do),
        and a bounded event marks the same fact in the run's trail. Persistence is
        best effort: it must never decide or rewrite the run's outcome.
        """

        if store is None:
            return
        try:
            snapshot = store.read_state(run_id)
            store.append_event(
                EventType.PROJECT_STATE_UPDATED,
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "code_write_status": result.status,
                    "code_write_verification": result.verification,
                    "code_write_files": [
                        str(item.get("path", "")) for item in result.files
                    ][:20],
                    "code_write_file_count": len(result.files),
                    "code_write_reason": str(result.reason)[:300],
                },
                run_id=run_id,
                task_id=task_id,
            )
            if snapshot is None:
                # No snapshot to merge into. The event above still records the
                # outcome, and inventing an empty state record here would claim the
                # run had no state at all.
                return
            self._write_project_state_entry(
                store, run_id, CODE_WRITE_PROJECT_STATE_KEY, result.to_dict()
            )
        except Exception:  # noqa: BLE001 - persistence must not block a run
            pass

    def _write_project_state_entry(
        self,
        store: RunStore,
        run_id: str,
        key: str,
        value: Any,
    ) -> bool:
        """Merge one key into the run's durable ``project_state``.

        Returns False when the run has no snapshot to merge into: inventing one would
        claim the run had state it never had. The merge rule lives here so every
        outcome that records beside the existing step-graph, recovery, memory and
        baseline records writes the snapshot the same way.
        """

        snapshot = store.read_state(run_id)
        if snapshot is None:
            return False
        project_state: Dict[str, Any] = (
            dict(snapshot.project_state) if snapshot.project_state else {}
        )
        project_state[key] = value
        persisted = None
        try:
            persisted = store.load(run_id)
        except Exception:  # noqa: BLE001 - the count falls back to the snapshot
            persisted = None
        store.write_state(
            RunStateSnapshot(
                run_id=run_id,
                task_id=snapshot.task_id,
                state=snapshot.state,
                status=snapshot.status,
                attempt_number=snapshot.attempt_number,
                completed_attempt_indexes=snapshot.completed_attempt_indexes,
                project_state=project_state,
                event_count=(
                    persisted.event_count
                    if persisted is not None
                    else snapshot.event_count
                ),
                updated_at=_utc_now_iso(),
            )
        )
        return True

    def _provider_key_index(
        self, provider_name: str, run_id: str
    ) -> Optional[int]:
        """Which rotation position of ``provider_name`` served *this* run, if any.

        The position is read from the provider instance the composition registered and
        only when that provider recorded this run's own id with it. The pair is what
        keeps a failed run from inheriting the index a previous run was served by --
        a plain "last used" attribute alone would attribute across runs.
        """

        name = str(provider_name or "").strip().lower()
        if not name:
            return None
        registry = getattr(self._runtime, "provider_registry", None)
        getter = getattr(registry, "get", None)
        if not callable(getter):
            return None
        try:
            provider = getter(name)
        except Exception:  # noqa: BLE001 - an unknown provider has no index
            return None
        index = getattr(provider, "last_key_index", None)
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            return None
        if str(getattr(provider, "last_key_run_id", "")) != str(run_id):
            return None
        return index

    def _record_provider_key_index(
        self,
        store: Optional[RunStore],
        run_id: str,
        task_id: str,
        provider_name: str,
        index: Optional[int],
    ) -> None:
        """Record which pool position served the run, without recording the key.

        The fact goes into the run's durable ``project_state`` (beside ``code_write``)
        and a bounded event marks it in the run's trail, so the UI can show which
        account served a run while the byte-pinned response model stays unchanged.
        Persistence is best effort: it must never decide or rewrite the outcome.
        """

        if store is None or index is None:
            return
        try:
            store.append_event(
                EventType.PROJECT_STATE_UPDATED,
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "provider": provider_name,
                    PROVIDER_KEY_INDEX_PROJECT_STATE_KEY: int(index),
                },
                run_id=run_id,
                task_id=task_id,
            )
            self._write_project_state_entry(
                store,
                run_id,
                PROVIDER_KEY_INDEX_PROJECT_STATE_KEY,
                int(index),
            )
        except Exception:  # noqa: BLE001 - persistence must not block a run
            pass

    def _run_offline_task(
        self,
        req: TaskRunRequest,
        *,
        task: Task,
        run_id: str,
        task_id: str,
        project_id: str,
        identity: object | None,
        run_store: Optional[RunStore],
        started_at: float,
        write_mode: bool = False,
    ) -> "RunDispatchResult":
        """Execute one submitted task with the deterministic offline run loop.

        The offline provider cannot answer for work - its provider call is a fixed
        string - so the task is executed by the repository's canonical
        ``AgentHarness`` loop instead, with the runtime's real stages:

            discovery (bounded workspace observation)
              -> planning (the deterministic ``Planner``, from the task text)
              -> decision (``OfflineTaskDecisionProvider``: verify)
              -> verification (``OfflineWorkspaceVerifier``: real read-only checks)
              -> acceptance (``AcceptanceGate`` over those checks)
              -> decision (complete or fail) -> terminal state

        This path **cannot execute a command and cannot invoke a tool**: it carries
        no execution request, no tool intent, and a frozen ``RunScope`` that
        authorises neither, and its decision provider cannot propose either action.
        It also makes no provider call at all, so the zero tokens/cost it reports
        is a measured zero rather than an estimate.
        """

        plan = build_offline_task_plan(req.description)
        task_specification = TaskSpecification(
            task_id=task_id,
            title=str(getattr(task.category, "value", task.category) or "task"),
            description=plan.description or req.description,
            requirements=(),
            acceptance_criteria=plan.criteria,
        )

        # The frozen perimeter for this run: this service's own workspace, its
        # execution profile, no tool, and no command. The criteria are the checks
        # this path really performs; they are derived server-side and frozen here
        # before the loop starts.
        scope = RunScope(
            run_id=run_id,
            workspace=self._workspace,
            execution_profile=self._execution_profile,
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset(),
            acceptance_criteria=plan.criteria,
        )
        scope.freeze()
        require_active_scope(run_id, scope)

        # What the run is about and what it will be judged by, recorded before it
        # runs, exactly as the other trusted loop entry points do.
        self._emit_acceptance_event(
            run_store,
            run_id,
            task_id,
            "RUN_STARTED",
            {
                "run_id": run_id,
                "task_id": task_id,
                "entry_point": "run_task_offline",
                "provider": OFFLINE_PROVIDER_NAME,
                "step_count": plan.step_count,
            },
        )
        self._emit_acceptance_event(
            run_store,
            run_id,
            task_id,
            "CRITERION_DEFINED",
            {
                "run_id": run_id,
                "task_id": task_id,
                "criterion_ids": [item.criterion_id for item in plan.criteria],
                "criteria_source": "offline_task_derivation",
                "expectation_count": len(plan.checks),
            },
        )

        request = HarnessRequest(
            run_id=run_id,
            workspace=self._workspace,
            task_specification=task_specification,
            # No command, no tool intent: the two action sources this run could
            # otherwise reach are empty by construction, not by a runtime check.
            execution_requests=(),
            allowed_execution_commands=tuple(scope.allowed_execution_commands),
            acceptance_criteria=plan.criteria,
            verification_expectations={
                check.criterion_id: check for check in plan.checks
            },
            run_scope=scope,
            metadata={
                "task_id": task_id,
                "entry_point": "run_task_offline",
                "provider": OFFLINE_PROVIDER_NAME,
                "step_count": plan.step_count,
            },
        )

        harness = self._harness_factory(
            decision_provider=OfflineTaskDecisionProvider(),
            verifier=OfflineWorkspaceVerifier(),
            policy=OFFLINE_TASK_LOOP_POLICY,
            # A *narrowed* observation of this service's workspace. The offline
            # task path is interactive, so its discovery bound is tighter than the
            # default one; a narrower bound can only observe less, never more, and
            # the observation is still produced by the canonical scanner.
            project_discovery=self._offline_project_discovery(),
            telemetry_sink=(self._telemetry_sink or self._offline_telemetry_sink()),
        )
        loop_started = time.perf_counter()
        result = harness.run(request)
        duration = time.perf_counter() - loop_started

        acceptance = getattr(result, "final_acceptance", None)
        acceptance_status = (
            acceptance.status.value if acceptance is not None else "not_evaluated"
        )
        if run_store is not None:
            self._persist_harness_events(run_store, result)
            self._emit_acceptance_event(
                run_store,
                run_id,
                task_id,
                "ACCEPTANCE_COMPLETED",
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "status": acceptance_status,
                    "code": (
                        acceptance.code if acceptance is not None else ""
                    ) or "verification_not_evaluated",
                    "criterion_ids": [item.criterion_id for item in plan.criteria],
                    "criteria_source": "offline_task_derivation",
                },
            )
            persisted = None
            try:
                persisted = run_store.load(run_id)
            except Exception:  # noqa: BLE001 - the count falls back to the loop's own
                persisted = None
            self._persist_loop_state(
                run_store,
                run_id,
                task_id,
                result,
                event_count=(
                    persisted.event_count if persisted is not None else None
                ),
            )

        final_status = getattr(
            getattr(result, "final_state", None), "status", None
        )
        state_name = (
            final_status.value
            if hasattr(final_status, "value")
            else str(final_status or "")
        )
        succeeded = state_name == "COMPLETED" and acceptance_status == "pass"
        error: Optional[str] = None
        if not succeeded:
            metadata = getattr(
                getattr(result, "final_state", None), "metadata", None
            ) or {}
            reason = str(metadata.get("reason") or "") if isinstance(metadata, Mapping) else ""
            error = (
                "offline task did not pass acceptance: "
                + (reason or (acceptance.code if acceptance is not None else "") or state_name)
            )

        output = render_offline_task_report(
            plan,
            result,
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            duration_seconds=duration,
        )

        # WRITE MODE on the offline path. The "model answer" is the deterministic
        # plan text, so the pipeline really runs end to end; it carries no fenced
        # block and no diff, so the honest outcome is `no_changes` and the run is
        # NOT a success. Nothing is written, and nothing is claimed.
        if write_mode:
            write_result = self._apply_model_changes(
                answer=self._offline_write_answer(plan),
                workspace=scope.workspace,
                run_id=run_id,
                task_id=task_id,
                run_store=run_store,
            )
            output = f"{output}\n\n{render_write_report(write_result)}"
            write_succeeded = write_result.status == STATUS_APPLIED
            # The loop's own verdict is never rewritten by a writer that found
            # nothing to apply; the write failure is reported only when the run
            # itself had passed, and the run's verdict always includes it.
            if not write_succeeded and succeeded:
                error = (
                    "write mode: no change was applied "
                    f"({write_result.status})"
                    + (f": {write_result.reason}" if write_result.reason else "")
                )
            succeeded = succeeded and write_succeeded

        # The dispatch outcome decides the durable idempotency lifecycle, exactly
        # as it does on the provider path, so a replay is admitted to replay. It is
        # recorded AFTER write mode has decided the final verdict, so the durable
        # position cannot say COMPLETED for a run that wrote nothing.
        if identity is not None:
            self._record_task_lifecycle(identity, success=succeeded, attempt_number=0)

        history_recorded = bool(
            run_store is not None and self._run_store.exists(run_id)
        )
        return RunDispatchResult(
            response=TaskRunResponse(
                run_id=run_id,
                task_id=task_id,
                project_id=project_id,
                state=state_name or "UNKNOWN",
                success=succeeded,
                output=output,
                error=error,
                # Nothing was consumed because no provider was called: a measured
                # zero, labelled with the same MOCK basis the offline provider's
                # own reports use.
                tokens=0,
                cost=0.0,
                duration_seconds=round(duration, 3),
                provider_used=OFFLINE_PROVIDER_NAME,
                # No model produced anything on this path, so the model field
                # names the deterministic offline engine rather than a provider.
                model_used="deterministic-offline",
            ),
            idempotent_replay=False,
            history_recorded=history_recorded,
            history_error=(
                "" if history_recorded else self._run_store_write_reason()
            ),
            usage_basis="MOCK",
            cost_basis="MOCK",
        )

    def _record_task_lifecycle(
        self, identity: object, *, success: bool, attempt_number: int
    ) -> None:
        """Record the terminal lifecycle of a keyed task dispatch.

        Best effort by contract: the dispatch outcome is already decided, and an
        unusable ledger must not rewrite it. It is called by both task paths so the
        durable position cannot differ between the provider path and the offline
        path.
        """

        try:
            from app.agent_runtime.idempotency import RunLifecycleState

            self._idempotency_ledger_instance().update(
                identity,
                lifecycle=(
                    RunLifecycleState.COMPLETED if success else RunLifecycleState.FAILED
                ),
                attempt_number=int(attempt_number),
            )
        except Exception:  # noqa: BLE001 - the dispatch outcome is already decided
            pass

    def run_task_detailed(
        self, req: TaskRunRequest, *, idempotency_key: Optional[str] = None
    ) -> "RunDispatchResult":
        """Execute a task and return the response with its dispatch facts."""
        run_id = f"run-api-{uuid.uuid4().hex[:8]}"
        task_id = req.task_id or f"task-{uuid.uuid4().hex[:6]}"
        project_id = req.project_id or self._active_project_id
        if not project_id:
            raise PermissionError(
                "this execution has no authorized project: bind one at composition time "
                "before creating any run"
            )

        category = TaskCategory.CODE
        try:
            category = TaskCategory(req.category.lower())
        except ValueError:
            category = TaskCategory.CODE

        # Idempotency is decided before anything is dispatched, so a replay cannot start a
        # second execution even in principle.
        identity = None
        if idempotency_key:
            # Fail closed: a paid dispatch must not proceed unprotected because the ledger is
            # unusable. A request without a key keeps the legacy behaviour.
            import time as _time

            from app.agent_runtime.idempotency import IdempotencyError

            identity = self._operation_identity(req, project_id, idempotency_key)
            record = None
            claimed = False
            last_error: Optional[BaseException] = None
            for attempt in range(6):
                try:
                    record, claimed = self._idempotency_ledger_instance().claim(
                        identity, run_id=run_id
                    )
                    break
                except IdempotencyError as race:
                    # The ledger writes its record with O_EXCL and parses it on the losing path, so
                    # a racing claim can read the file mid-write. That is an expected race, not an
                    # unavailable ledger: wait briefly for the winner to finish writing.
                    last_error = race
                    _time.sleep(0.05 * (attempt + 1))
                except Exception as error:  # noqa: BLE001 - an unusable ledger fails closed now
                    # Anything else (a denied directory, for example) cannot be fixed by waiting.
                    last_error = error
                    break
            if record is None and not claimed:
                raise IdempotencyUnavailable(
                    "idempotency protection is unavailable, so the dispatch was refused "
                    f"({type(last_error).__name__ if last_error else 'unknown'}); "
                    "set FORGE_RUN_STORE_ROOT to a writable directory"
                ) from last_error
            if not claimed:
                if getattr(record, "operation_fingerprint", "") != identity.fingerprint:
                    raise RunIdempotencyConflict(
                        "idempotency_key was already used for a different request"
                    )
                return RunDispatchResult(
                    response=self._replay_response(record, req, project_id),
                    idempotent_replay=True,
                    history_recorded=True,
                    history_error="",
                    usage_basis="UNKNOWN",
                    cost_basis="UNKNOWN",
                )

        context = dict(req.context)
        context.update({"run_id": run_id, "project_id": project_id})

        # WRITE MODE is opt-in and code-only. When it is on, the model is given the
        # task text plus a bounded list of the workspace's file names (never their
        # contents), so an answer can name a real path. When it is off, the task is
        # exactly the description it always was.
        write_mode = self._write_mode_enabled(req, category)
        task_description = (
            self._write_task_description(req.description)
            if write_mode
            else req.description
        )

        task = Task(
            id=task_id,
            description=task_description,
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
        #
        # THE OFFLINE TASK PATH comes first, and it builds its own scope: the
        # criteria it is judged by are derived from the task, and a frozen scope
        # cannot be replaced afterwards. It executes no command and invokes no
        # tool, so it authorises neither, and it is still an ordinary frozen
        # `RunScope` over this service's own workspace.
        if self._selected_provider_name(req).strip().lower() == OFFLINE_PROVIDER_NAME:
            return self._run_offline_task(
                req,
                task=task,
                run_id=run_id,
                task_id=task_id,
                project_id=project_id,
                identity=identity,
                run_store=api_run_store,
                started_at=t0,
                write_mode=write_mode,
            )

        # A model named in the request selects the provider model for THIS run. The dispatcher
        # already honours ``task.parameters["model"]`` as a per-run override; without this bridge a
        # caller's model was silently ignored and every non-default model failed as
        # "<provider> model is not configured" (measured with deepseek and radox).
        if getattr(req, "model_name", None) and isinstance(getattr(task, "parameters", None), dict):
            task.parameters["model"] = str(req.model_name)

        scope = self._build_run_scope(run_id, task)
        effective_tool_ids = scope.allowed_tool_ids
        execution_request_factory = self._build_execution_request_factory(run_id)

        # The SELECTED provider is passed, not ``req.provider_name``: when a request names no provider
        # the selected one is the configured default, and passing the raw request field (``None``)
        # made the dispatcher fall back to its own copy of the default -- so a request with no
        # provider ran on a provider the operator had not configured as default (measured: settings
        # said "radox", every unnamed run executed on "openrouter").
        #
        # ``allow_fallback`` keeps the configured chain reachable for exactly those unnamed runs.
        # Passing the resolved default as an explicit name made it a *pin*, so a 402 on the default
        # ended the run with "Provider fallback chain exhausted" and openrouter was never tried,
        # even though FORGE_PROVIDER_FALLBACK_CHAIN listed it. A run that names a provider itself
        # stays pinned: the caller asked for that provider.
        run = self._runtime.run_executor.execute(
            task=task,
            provider_name=(self._selected_provider_name(req) or None),
            allow_fallback=not (req.provider_name or "").strip(),
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
        # A persisted record is what makes the run auditable; a silent failure would show an
        # empty history and call it normal. Derive the truth from the store itself.
        usage_basis, cost_basis = self.derive_usage_basis(
            getattr(run_acc, "attempt_records", ()) or ()
        )
        history_recorded = bool(api_run_store is not None and self._run_store.exists(run_id))
        history_error = "" if history_recorded else self._run_store_write_reason()
        success = (run.state == RunState.COMPLETED)

        last_att = run_acc.attempt_records[-1] if run_acc.attempt_records else None
        # Provider attribution comes from the terminal `TaskResult`, which the dispatcher
        # sets to the provider it returned. Taking it from the last recorded attempt
        # instead was wrong whenever the run had no success at all: the dispatcher tries
        # every candidate and then returns a failure naming the **preferred** provider
        # (`TaskResult(provider=selected, ...)`), while the last attempt record names the
        # last candidate it happened to try. Measured with the mock provider removed:
        # `run.result.provider == 'openrouter'` but the last attempt was `'groq'`, so a
        # failed run was attributed to a provider the dispatcher never held responsible.
        #
        # On success the two agree by construction -- the dispatcher returns immediately on
        # the first success, so no attempt can follow a successful one -- which is why the
        # divergence only shows up on failure. `attempt_records` remains the fallback for a
        # run whose result is unavailable.
        terminal = run.result
        provider_used = (
            (getattr(terminal, "provider", None) or "")
            if terminal is not None
            else (last_att.provider_name if last_att else "")
        )
        # `model_used` is a MODEL name, never a provider name. The dispatcher's
        # exhaustion `TaskResult` carries `provider=selected` but no `model_name`,
        # so falling back to `provider_used` reported `model_used == 'openrouter'`
        # for a totally failed run -- a value that names a provider, not a model,
        # and that a caller uses to attribute cost. The fallback order is now:
        # the terminal result's real model name; then the last recorded attempt's
        # real model name; then empty, because an unknown model is reported as
        # unknown rather than invented.
        terminal_model = (
            getattr(terminal, "model_name", None) if terminal is not None else None
        )
        model_used = terminal_model or (
            (last_att.model_name or "") if last_att is not None else ""
        )

        # The failure reason comes from `run.error`, which the executor sets on EVERY
        # failure path. Events must not decide failure: a rejected *intermediate* attempt
        # also emits `provider_result(success=False, error=...)`, so scanning events for an
        # error reported a **successful** run as failed with a stale message ("provider is
        # disabled by configuration") -- observed on a completed run whose output was the
        # mock provider's response.
        #
        # `run.result` is not sufficient either, and this is the narrower half of the same
        # mistake: the executor assigns it only *inside* the try block
        # (`app/orchestrator/run.py`), while its generic `except` sets `run.error` alone.
        # A provider that raises something `ProviderAgent` does not translate to
        # `ProviderError` therefore leaves `run.result is None` with a populated
        # `run.error`. Reading only `run.result.error` reported that run as
        # `success=False, error=None`, i.e. a failure with the reason silently dropped.
        if run.error is not None:
            error = getattr(run.error, "message", None) or str(run.error)
            success = False
        elif run.result is not None and run.result.error:
            error = run.result.error
            success = False

        # Events are consulted for the human-readable output only, never for failure.
        for ev in reversed(run.events):
            if ev.data.get("result"):
                res_obj = ev.data.get("result")
                output = getattr(res_obj, "output", "") or str(res_obj)
                break

        if not output and run.result is not None:
            output = run.result.output

        # WRITE MODE on the provider path: the "model answer" is the provider's own
        # output, and the guarded writer applies it inside this run's frozen
        # workspace. `success` survives only when a change was really applied and
        # verified; every other status is reported as a failure with its reason.
        # A dispatch that produced no usable answer is not retried or guessed at -
        # its own honest error stands and nothing is written.
        if write_mode:
            answer = getattr(terminal, "output", "") if terminal is not None else ""
            if success and isinstance(answer, str) and answer.strip():
                write_result = self._apply_model_changes(
                    answer=answer,
                    workspace=scope.workspace,
                    run_id=run_id,
                    task_id=task_id,
                    run_store=api_run_store,
                )
                output = f"{output or ''}\n\n{render_write_report(write_result)}".lstrip("\n")
                success = write_result.status == STATUS_APPLIED
                error = None if success else (
                    "write mode: no change was applied "
                    f"({write_result.status})"
                    + (f": {write_result.reason}" if write_result.reason else "")
                )
            else:
                success = False
                if not error:
                    error = "write mode: the provider produced no usable answer"

        # WHICH ACCOUNT SERVED THE RUN. The response model is byte-pinned, so the
        # rotation position is recorded in the run's durable state beside the write
        # outcome. It is a position in the key pool -- never a key, a prefix or a
        # hash -- and it is written only when the provider recorded this very run id
        # with it, so a failed run cannot inherit another run's index.
        self._record_provider_key_index(
            api_run_store,
            run_id,
            task_id,
            provider_used,
            self._provider_key_index(provider_used, run_id),
        )

        # The lifecycle is recorded through the shared helper, AFTER the verdict is
        # final. The inline version this replaced read `success` one line before it
        # was assigned, so the NameError was swallowed by the surrounding catch-all
        # and the durable position never advanced on the provider path. It now also
        # follows write mode, so a keyed write-mode run cannot be recorded COMPLETED
        # while nothing was written.
        if identity is not None:
            self._record_task_lifecycle(
                identity,
                success=success,
                attempt_number=int(getattr(run_acc, "total_attempts", 0) or 0),
            )

        response = TaskRunResponse(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            state=run.state.value,
            success=success,
            output=output or "(Task execution completed)",
            error=error,
            # Only a basis backed by evidence may produce a number; pure silence is None.
            tokens=None if usage_basis == "UNKNOWN" else int(run_acc.total_tokens),
            cost=None if cost_basis == "UNKNOWN" else round(run_acc.total_cost, 6),
            duration_seconds=round(duration, 3),
            provider_used=provider_used,
            model_used=model_used,
        )
        return RunDispatchResult(
            response=response,
            idempotent_replay=False,
            history_recorded=history_recorded,
            history_error=history_error,
            usage_basis=usage_basis,
            cost_basis=cost_basis,
        )
