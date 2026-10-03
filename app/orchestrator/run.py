"""In-memory Run wrapper around the existing Orchestrator dispatch flow."""

import time
from dataclasses import replace
from typing import Iterable, Optional

from app.agents.base import AgentExecutionError
from app.context import (
    ContextAssembler,
    ContextFreshness,
    ContextItem,
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
from app.tools.executor import ToolExecutor
from app.tools.contracts import ToolResult, ToolStatus
from app.tools.permissions import ToolExecutionContext
from app.tools.registry import ToolRegistry
from app.tools.workspace import Workspace


class RunExecutor:
    """Create a Run record while leaving routing and fallback to Dispatcher."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        context_assembler: Optional[ContextAssembler] = None,
        tool_executor: Optional[ToolExecutor] = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._context_assembler = context_assembler or ContextAssembler()
        self._tool_executor = tool_executor or ToolExecutor(ToolRegistry())

    def execute(
        self,
        task: Task,
        *,
        provider_name: Optional[str] = None,
        agent_name: Optional[str] = None,
        explicit_inputs: Iterable[str | ContextItem] = (),
        allowed_tool_ids: Iterable[str] = (),
        workspace: Optional[Workspace] = None,
        _run: Run | None = None,
    ) -> Run:
        """Execute through Orchestrator and retain a safe in-memory event trace."""
        run = _run or Run(task=task)
        started = time.perf_counter()
        self._record(run, EventType.RUN_STARTED, task_id=getattr(task, "id", None))
        run.state = RunState.RUNNING if _run is None else RunState.REVISING

        try:
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
                    run, event_type, **data
                ),
            )
            if result.success and result.tool_invocations:
                permission_context = ToolExecutionContext(
                    run_id=run.id,
                    context_fingerprint=execution_context.fingerprint,
                    allowed_tool_ids=frozenset(allowed_tool_ids),
                    workspace=workspace,
                )
                initial_invocations = list(result.tool_invocations)
                tool_results = [
                    self._tool_executor.execute(
                        invocation,
                        context=permission_context,
                        observer=lambda event_type, data: self._record(
                            run, event_type, **data
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
                        run, event_type, **data
                    ),
                )
                followup_invocations = list(result.tool_invocations)
                followup_context = ToolExecutionContext(
                    run_id=run.id,
                    context_fingerprint=execution_context.fingerprint,
                    allowed_tool_ids=permission_context.allowed_tool_ids,
                    round_number=1,
                    workspace=workspace,
                )
                tool_results.extend(
                    self._tool_executor.execute(
                        invocation,
                        context=followup_context,
                        observer=lambda event_type, data: self._record(
                            run, event_type, **data
                        ),
                    )
                    for invocation in followup_invocations
                )
                result.tool_invocations = initial_invocations + followup_invocations
                result.tool_results = tool_results
            run.result = result
            if result.success:
                run.state = RunState.COMPLETED
                run.error = None
                self._record(
                    run,
                    EventType.RUN_COMPLETED,
                    provider=result.provider,
                    model=result.model_name,
                    duration_seconds=max(0.0, time.perf_counter() - started),
                    usage=self._usage_data(result),
                )
            else:
                message = result.error or "Task execution failed"
                run.state = RunState.FAILED
                run.error = RunError("TaskExecutionError", message)
                self._record(
                    run,
                    EventType.RUN_FAILED,
                    error_type=run.error.error_type,
                    error=run.error.message,
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
        return run

    @staticmethod
    def _record(run: Run, event_type: EventType, **data: object) -> None:
        run.events.append(Event(run_id=run.id, type=event_type, data=data))

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
        return replace(task, context={"forge_execution_context": execution_context.to_dict()})

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
