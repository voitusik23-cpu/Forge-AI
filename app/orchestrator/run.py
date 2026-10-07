"""In-memory Run wrapper around the existing Orchestrator dispatch flow."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Iterable, Optional

from app.agents.base import AgentExecutionError
from app.context import (
    ContextAssembler,
    ContextBudgetPolicy,
    ContextFreshness,
    ContextItem,
    ContextSelector,
    ContextTrust,
    ExecutionContext,
)
from app.orchestrator.models import (
    Event,
    EventType,
    Run,
    RunError,
    RunState,
    Task,
    TaskResult,
)
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.trace import sanitize_event_metadata
from app.tools.executor import ToolExecutor
from app.tools.contracts import ToolResult, ToolStatus
from app.tools.permissions import ToolExecutionContext
from app.tools.registry import ToolRegistry
from app.tools.workspace import Workspace

# Maps a host execution outcome onto the lifecycle event that records it. A
# denied outcome is reported with the same terminal event shape as any other
# unsuccessful execution so a Run never looks successful after a denial.
_EXECUTION_OUTCOME_EVENTS: dict[str, EventType] = {
    "EXECUTION_SUCCESS": EventType.EXECUTION_COMPLETED,
    "EXECUTION_FAILURE": EventType.EXECUTION_COMPLETED,
    "EXECUTION_TIMEOUT": EventType.EXECUTION_COMPLETED,
    "EXECUTION_ERROR": EventType.EXECUTION_COMPLETED,
    "PERMISSION_DENIED": EventType.EXECUTION_DENIED,
    "POLICY_DENIED": EventType.EXECUTION_DENIED,
    "APPROVAL_WAITING": EventType.EXECUTION_DENIED,
    "APPROVAL_REJECTED": EventType.EXECUTION_DENIED,
}

# Outcomes that mean the run must not be reported as completed. Approval waiting
# is distinct: it is a terminal wait state, not a failure.
_SUCCESSFUL_EXECUTION_OUTCOMES = frozenset({"EXECUTION_SUCCESS"})
_APPROVAL_WAITING_OUTCOMES = frozenset({"APPROVAL_WAITING"})

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.execution.authorizer import ExecutionCoordinator
    from app.execution.profile import ProjectExecutionProfile
    from app.execution.request import ExecutionRequest, ExecutionResult
    from app.tools.approval import ApprovalPolicy, ApprovalResolver


class RunExecutor:
    """Create a Run record while leaving routing and fallback to Dispatcher."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        context_assembler: Optional[ContextAssembler] = None,
        tool_executor: Optional[ToolExecutor] = None,
        selector: Optional[ContextSelector] = None,
        budget_policy: Optional[ContextBudgetPolicy] = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._context_assembler = context_assembler or ContextAssembler(
            selector=selector,
            budget_policy=budget_policy,
        )
        self._tool_executor = tool_executor or ToolExecutor(ToolRegistry())

    def _resolve_model_info(
        self,
        task: Task,
        provider_name: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> object | None:
        """Deterministically lookup ProviderModelInfo if ModelRegistry is configured."""
        if self._orchestrator is None:
            return None
        dispatcher = getattr(self._orchestrator, "_dispatcher", None)
        if dispatcher is None:
            return None
        model_registry = getattr(dispatcher, "_model_registry", None)
        if model_registry is None:
            return None

        target_provider = provider_name
        if not target_provider and agent_name:
            agent_reg = getattr(dispatcher, "_registry", None)
            if agent_reg is not None:
                try:
                    agent = agent_reg.get(agent_name)
                    target_provider = getattr(agent, "provider_name", None)
                    if not target_provider and hasattr(agent, "provider"):
                        target_provider = getattr(agent.provider, "name", None)
                except Exception:
                    pass
        if not target_provider:
            target_provider = getattr(dispatcher, "_default_provider", None)

        if target_provider:
            prov_reg = getattr(dispatcher, "_provider_registry", None)
            model_name = None
            if prov_reg is not None:
                try:
                    provider = prov_reg.get(target_provider)
                    if hasattr(provider, "config"):
                        model_name = getattr(provider.config, "model_name", None)
                except Exception:
                    pass
            if model_name:
                try:
                    return model_registry.get(target_provider, model_name)
                except Exception:
                    pass
            try:
                return model_registry.get_default(target_provider)
            except Exception:
                pass
        return None

    def execute(
        self,
        task: Task,
        *,
        provider_name: Optional[str] = None,
        agent_name: Optional[str] = None,
        explicit_inputs: Iterable[str | ContextItem] = (),
        allowed_tool_ids: Iterable[str] = (),
        workspace: Optional[Workspace] = None,
        run_id: str = "",
        run_scope: object | None = None,
        observer: Optional[Callable[[EventType, dict], None]] = None,
        run_store: object | None = None,
        execution_request_factory: (
            Callable[[Task, "ProjectExecutionProfile"], Sequence["ExecutionRequest"]] | None
        ) = None,
        allowed_execution_commands: "frozenset[str] | None" = None,
        execution_coordinator: "ExecutionCoordinator | None" = None,
        approval_policy: "ApprovalPolicy | None" = None,
        approval_resolver: "ApprovalResolver | None" = None,
        _run: Run | None = None,
        _attempt_number: int = 0,
    ) -> Run:
        """Execute through Orchestrator and retain a safe in-memory event trace."""
        run = _run or Run(task=task, **({"id": run_id} if run_id else {}))
        started = time.perf_counter()
        self._record(run, EventType.RUN_STARTED, task_id=getattr(task, "id", None))
        run.state = RunState.RUNNING if _attempt_number == 0 else RunState.REVISING

        try:
            model_info = self._resolve_model_info(
                task=task,
                provider_name=provider_name,
                agent_name=agent_name,
            )
            try:
                execution_context = self._context_assembler.assemble(
                    task,
                    run.id,
                    explicit_inputs,
                    model_info=model_info,
                )
            except TypeError:
                execution_context = self._context_assembler.assemble(
                    task, run.id, explicit_inputs
                )
            self._record_context_assembled(run, execution_context)
            dispatch_task = self._task_with_execution_context(task, execution_context)
            result = self._orchestrator.dispatch(
                dispatch_task,
                agent_name=agent_name,
                provider_name=provider_name,
                observer=lambda event_type, data: self._record(
                    run, event_type, observer, run_store, **data
                ),
            )
            if result.success and result.tool_invocations:
                permission_context = ToolExecutionContext(
                    run_id=run.id,
                    context_fingerprint=execution_context.fingerprint,
                    allowed_tool_ids=frozenset(allowed_tool_ids),
                    attempt_number=_attempt_number,
                    workspace=workspace,
                    run_scope=run_scope,
                )
                initial_invocations = list(result.tool_invocations)
                tool_results = [
                    self._tool_executor.execute(
                        invocation,
                        context=permission_context,
                        observer=lambda event_type, data: self._record(
                            run, event_type, observer, run_store, **data
                        ),
                    )
                    for invocation in initial_invocations
                ]
                result.tool_results = tool_results
                if any(
                    item.status == ToolStatus.WAITING_FOR_APPROVAL
                    for item in tool_results
                ):
                    run.result = result
                    run.state = RunState.WAITING_FOR_APPROVAL
                    return run
                followup_task = self._task_with_tool_results(
                    dispatch_task, tool_results
                )
                result = self._orchestrator.dispatch(
                    followup_task,
                    agent_name=agent_name,
                    provider_name=provider_name,
                    observer=lambda event_type, data: self._record(
                        run, event_type, observer, run_store, **data
                    ),
                )
                followup_invocations = list(result.tool_invocations)
                followup_context = ToolExecutionContext(
                    run_id=run.id,
                    context_fingerprint=execution_context.fingerprint,
                    allowed_tool_ids=permission_context.allowed_tool_ids,
                    round_number=1,
                    attempt_number=_attempt_number,
                    workspace=workspace,
                    run_scope=run_scope,
                )
                tool_results.extend(
                    self._tool_executor.execute(
                        invocation,
                        context=followup_context,
                        observer=lambda event_type, data: self._record(
                            run, event_type, observer, run_store, **data
                        ),
                    )
                    for invocation in followup_invocations
                )
                result.tool_invocations = initial_invocations + followup_invocations
                result.tool_results = tool_results
            run.result = result

            # Host process execution. This is a separate branch beside the tool
            # path, not part of it. It runs only when the server injected an
            # execution request factory AND the run declares command authority;
            # both defaults are inert, so an ordinary Run never reaches the
            # execution plane. The coordinator remains the only component that
            # may mint an AuthorizedExecution, and it re-validates every request
            # against this Run's frozen scope before doing so.
            if execution_request_factory is not None and result.success:
                self._execute_host_requests(
                    run,
                    result=result,
                    execution_request_factory=execution_request_factory,
                    allowed_execution_commands=allowed_execution_commands,
                    execution_coordinator=execution_coordinator,
                    approval_policy=approval_policy,
                    approval_resolver=approval_resolver,
                    workspace=workspace,
                    run_scope=run_scope,
                    observer=observer,
                    run_store=run_store,
                )

            if result.success:
                run.state = RunState.COMPLETED
                run.error = None
            else:
                message = result.error or "Task execution failed"
                if self._host_approval_pending(run):
                    # Host execution is waiting for approval. This is a terminal
                    # wait state, not a failure, and it must not be reported as a
                    # failed workaround for a completed dispatch.
                    run.state = RunState.WAITING_FOR_APPROVAL
                    run.error = None
                else:
                    run.state = RunState.FAILED
                    run.error = RunError("TaskExecutionError", message)

            if run.state == RunState.WAITING_FOR_APPROVAL:
                self._record(
                    run,
                    EventType.APPROVAL_REQUESTED,
                    run_id=run.id,
                    state=RunState.WAITING_FOR_APPROVAL.value,
                    reason="host_execution_approval_pending",
                )
            elif run.state == RunState.COMPLETED:
                self._record(
                    run,
                    EventType.RUN_COMPLETED,
                    provider=result.provider,
                    model=result.model_name,
                    duration_seconds=max(0.0, time.perf_counter() - started),
                    usage=self._usage_data(result),
                )
            else:
                self._record(
                    run,
                    EventType.RUN_FAILED,
                    error_type=run.error.error_type if run.error else "TaskExecutionError",
                    error=run.error.message if run.error else "Task execution failed",
                    duration_seconds=max(0.0, time.perf_counter() - started),
                )
        except Exception as exc:
            run.state = RunState.FAILED
            message = (
                str(exc) or type(exc).__name__
                if isinstance(exc, (ValueError, LookupError, AgentExecutionError))
                else f"Execution failed ({type(exc).__name__})"
            )
            run.error = RunError(type(exc).__name__, message)
            self._record(
                run,
                EventType.RUN_FAILED,
                error_type=run.error.error_type,
                error=run.error.message,
                duration_seconds=max(0.0, time.perf_counter() - started),
            )
        finally:
            self._tool_executor.change_set_collector.finalize(
                run, _attempt_number
            )
        return run

    @staticmethod
    def _record(
        run: Run,
        event_type: EventType,
        observer: Optional[Callable[[EventType, dict], None]] = None,
        run_store: object | None = None,
        **data: object,
    ) -> None:
        """Store one canonical Run event, persist it, then optionally notify.

        The canonical record is written first so it survives any later failure.
        Persistence and observation each receive a sanitized projection only, and
        an exception from either is contained rather than propagated.
        """
        run.events.append(Event(run_id=run.id, type=event_type, data=data))
        if run_store is not None:
            try:
                run_store(event_type, sanitize_event_metadata(data))
            except Exception:  # noqa: BLE001 - persistence must not abort a Run
                logging.getLogger("forge_ai").warning(
                    "run history persistence failed for event %s of run %s",
                    getattr(event_type, "value", event_type),
                    run.id,
                    exc_info=True,
                )
        if observer is None:
            return
        try:
            observer(event_type, sanitize_event_metadata(data))
        except Exception:  # noqa: BLE001 - observational failures must not abort a Run
            logging.getLogger("forge_ai").warning(
                "run observer failed for event %s of run %s",
                getattr(event_type, "value", event_type),
                run.id,
                exc_info=True,
            )

    def _execute_host_requests(
        self,
        run: Run,
        *,
        result: TaskResult,
        execution_request_factory: Callable[..., Sequence["ExecutionRequest"]],
        allowed_execution_commands: "frozenset[str] | None",
        execution_coordinator: "ExecutionCoordinator | None",
        approval_policy: "ApprovalPolicy | None",
        approval_resolver: "ApprovalResolver | None",
        workspace: Workspace | None,
        run_scope: object | None,
        observer: Optional[Callable[[EventType, dict], None]],
        run_store: object | None,
    ) -> None:
        """Run host process execution requests through the execution coordinator.

        Trust model: the factory is server-side. It may only *ask* for a command;
        it cannot authorise one. Every request is re-validated by the coordinator
        against this Run's frozen scope, and a request whose command is outside
        the operator's allowlist is denied before any token is minted.

        Fail-closed rules applied here, before the coordinator is reached:

        1. no declared command authority -> nothing is requested at all;
        2. a run scope is mandatory, because the coordinator is the only
           perimeter that can reject an out-of-perimeter request;
        3. an ``ApprovalPolicy`` is mandatory, so approval never depends solely
           on the request's own ``approval_required`` flag;
        4. a missing workspace root means no execution root, so no execution.
        """
        command_authority = allowed_execution_commands or frozenset()
        if not command_authority:
            return
        if run_scope is None:
            self._record(
                run,
                EventType.EXECUTION_DENIED,
                run_id=run.id,
                status="DENIED",
                reason="run_scope_required",
            )
            result.success = False
            return
        if approval_policy is None:
            self._record(
                run,
                EventType.EXECUTION_DENIED,
                run_id=run.id,
                status="DENIED",
                reason="approval_policy_required",
            )
            result.success = False
            return
        if workspace is None:
            self._record(
                run,
                EventType.EXECUTION_DENIED,
                run_id=run.id,
                status="DENIED",
                reason="workspace_root_required",
            )
            result.success = False
            return

        profile = getattr(run_scope, "execution_profile", None)
        try:
            requests = tuple(execution_request_factory(run.task, profile))
        except Exception as exc:  # noqa: BLE001 - a failed request must not execute
            self._record(
                run,
                EventType.EXECUTION_DENIED,
                run_id=run.id,
                status="DENIED",
                reason=f"execution_request_failed:{type(exc).__name__}",
            )
            result.success = False
            return

        if not requests:
            return

        coordinator = execution_coordinator
        if coordinator is None:
            from app.execution.adapter import LocalExecutionAdapter
            from app.execution.authorizer import ExecutionCoordinator as _Coordinator

            coordinator = _Coordinator(
                LocalExecutionAdapter(workspace_root=workspace.root)
            )

        execution_results: list["ExecutionResult"] = []
        for request in requests:
            execution_result = coordinator.execute(
                request,
                workspace_root=workspace.root,
                run_id=run.id,
                allowed_commands=command_authority,
                approval_policy=approval_policy,
                approval_resolver=approval_resolver,
                run_scope=run_scope,
                observer=lambda event_type, data: self._record(
                    run, event_type, observer, run_store, **data
                ),
            )
            execution_results.append(execution_result)
            self._record_execution_outcome(run, execution_result)

        run.execution_results = list(execution_results)
        outcomes = {
            str(item.outcome_status.value)
            for item in execution_results
            if item.outcome_status is not None
        }
        if outcomes & _APPROVAL_WAITING_OUTCOMES:
            # Approval waiting is a terminal wait state, not a failure. The
            # state is applied after the caller's success/failure branch so a
            # completed dispatch cannot mask the pending approval.
            result.success = False
        elif not outcomes <= _SUCCESSFUL_EXECUTION_OUTCOMES:
            result.success = False

    @staticmethod
    def _host_approval_pending(run: Run) -> bool:
        """Report whether any recorded host execution is waiting for approval."""
        return any(
            item.outcome_status is not None
            and str(item.outcome_status.value) in _APPROVAL_WAITING_OUTCOMES
            for item in getattr(run, "execution_results", ())
        )

    def _record_execution_outcome(
        self, run: Run, execution_result: "ExecutionResult"
    ) -> None:
        """Record one host execution outcome with sanitized, non-secret metadata.

        Only identifiers, status, and a reason are recorded. Stdout, stderr, raw
        environment values, and the AuthorizedExecution token are never written,
        so a durable record can never act as execution authority later.
        """
        outcome = (
            execution_result.outcome_status.value
            if execution_result.outcome_status is not None
            else str(execution_result.status.value)
        )
        event_type = _EXECUTION_OUTCOME_EVENTS.get(outcome, EventType.EXECUTION_DENIED)
        metadata = dict(execution_result.metadata or {})
        self._record(
            run,
            event_type,
            run_id=run.id,
            request_id=execution_result.request_id,
            status=outcome,
            reason=metadata.get("denial_reason") or outcome,
            exit_code=execution_result.exit_code,
        )

    def attach_attempt_outcomes(
        self,
        run: Run,
        attempt_number: int,
        *,
        verification_status: str | None,
        acceptance_status: str | None,
    ) -> None:
        self._tool_executor.change_set_collector.attach_outcomes(
            run,
            attempt_number,
            verification_status=verification_status,
            acceptance_status=acceptance_status,
        )

    @classmethod
    def _record_context_assembled(
        cls, run: Run, execution_context: ExecutionContext
    ) -> None:
        items = execution_context.items
        cls._record(
            run,
            EventType.CONTEXT_ASSEMBLED,
            item_count=len(items),
            context_fingerprint=execution_context.fingerprint,
            kinds=sorted({item.kind for item in items}),
            sources=sorted({item.source.value for item in items}),
            trust={
                trust.value: sum(item.trust == trust for item in items)
                for trust in ContextTrust
            },
            freshness={
                freshness.value: sum(item.freshness == freshness for item in items)
                for freshness in ContextFreshness
            },
        )

    @staticmethod
    def _task_with_execution_context(
        task: Task, execution_context: ExecutionContext
    ) -> Task:
        context = dict(task.context) if isinstance(task.context, dict) else {}
        context["forge_execution_context"] = execution_context.to_dict()
        return replace(task, context=context)

    @staticmethod
    def _task_with_tool_results(task: Task, tool_results: list[ToolResult]) -> Task:
        context = dict(task.context)
        context["forge_tool_results"] = [
            {
                "invocation_id": result.invocation_id,
                "status": result.status.value,
                "output": result.output,
                "error": result.error,
            }
            for result in tool_results
        ]
        return replace(task, context=context)

    @staticmethod
    def _usage_data(result: TaskResult) -> Optional[dict[str, object]]:
        usage = result.usage
        if usage is None:
            return None
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "estimated_cost": usage.estimated_cost,
        }
