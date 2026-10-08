"""Deterministic Agent Harness and controlled Run Loop for Forge AI."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any
from uuid import uuid4

from app.agent_runtime.models import (
    HarnessPhase,
    HarnessRequest,
    HarnessResult,
    HarnessState,
    HarnessStatus,
    StructuredObservation,
)
from app.agent_runtime.policy import AgentHarnessPolicy
from app.context.assembler import DecisionContextAssembler
from app.decision.models import Decision, DecisionAction, DecisionRequest, DecisionType
from app.decision.provider import DecisionProvider, DeterministicDecisionProvider
from app.decision.validator import DecisionValidationReport, validate_decision
from app.planning.validation import require_valid_plan
from app.agent_runtime.revision_decision import (
    RevisionBudget,
    RevisionDecision,
    RevisionError,
)
from app.agent_runtime.tool_execution import (
    AuthorizedToolCall,
    ToolAuthorizationError,
    ToolIntent,
    authorize_tool_intent,
)
from app.tools.bounded import BoundedToolResult, bound_tool_result
from app.tools.contracts import ToolStatus
from app.tools.executor import ToolExecutor
from app.agent_runtime.revision_policy import (
    RevisionEligibility,
    evaluate_revision,
    plan_structure_signature,
)
from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.identity import command_is_allowed
from app.execution.request import ExecutionOutcomeStatus, ExecutionResult
from app.runtime.run_scope import RunScope, RunScopeError, require_active_scope
from app.orchestrator.models import EventType
from app.orchestrator.trace import RunEvent, RunEventCollector, RunTrace
from app.projects.state import ProjectState, ProjectStateStatus, derive_project_state
from app.tools.acceptance import AcceptanceGate, AcceptanceResult, AcceptanceStatus
from app.tools.approval import ApprovalPolicy, ApprovalRequest, ApprovalState
from app.tools.verification import VerificationExpectation, VerificationResult, WorkspaceVerifier
from app.skills.evaluator import SkillEvaluator
from app.skills.models import SkillDefinition


class AgentHarness:
    """Bounded, deterministic runtime harness coordinating the Run Loop.

    Explicit Lifecycle:
        OBSERVE
        -> ASSEMBLE_CONTEXT
        -> DECIDE
        -> VALIDATE_DECISION
        -> AUTHORIZE
        -> ACT (at most one authoritative action)
        -> OBSERVE_RESULT
        -> UPDATE_STATE
        -> REPEAT or COMPLETE
    """

    def __init__(
        self,
        *,
        context_assembler: DecisionContextAssembler | None = None,
        decision_provider: DecisionProvider | None = None,
        decision_validator: (
            Callable[[Decision, DecisionRequest, ProjectState | None], DecisionValidationReport]
            | None
        ) = None,
        execution_coordinator: ExecutionCoordinator | None = None,
        verifier: WorkspaceVerifier | None = None,
        acceptance_gate: AcceptanceGate | None = None,
        state_derivator: Callable[..., ProjectState] | None = None,
        policy: AgentHarnessPolicy | None = None,
        skill_evaluator: SkillEvaluator | None = None,
        memory_store: Any | None = None,
        observer: Callable[[EventType, Mapping[str, object]], None] | None = None,
        project_discovery: Any | None = None,
        project_planner: Any | None = None,
        revision_budget: RevisionBudget | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self._context_assembler = context_assembler or DecisionContextAssembler()
        self._decision_provider = decision_provider or DeterministicDecisionProvider()
        self._decision_validator = decision_validator or validate_decision
        self._execution_coordinator = execution_coordinator
        self._verifier = verifier or WorkspaceVerifier()
        self._acceptance_gate = acceptance_gate or AcceptanceGate()
        self._state_derivator = state_derivator or derive_project_state
        self.policy = policy or AgentHarnessPolicy()
        self._skill_evaluator = skill_evaluator or SkillEvaluator()
        self._memory_store = memory_store
        # Optional sink for the same events the harness collects. It receives
        # exactly the events the harness already emits - it is a second consumer,
        # never a second event stream - so a production caller can persist the
        # loop's observable stages without changing the run loop.
        self._observer = observer
        # Optional server-side observation layer. It holds no authority: it reads
        # the workspace the request carries and returns a bounded snapshot.
        self._project_discovery = project_discovery
        # Optional declarative planning layer. It turns the trusted goal and the
        # observation into an immutable intention; it holds no execution authority
        # and cannot reach the filesystem, a coordinator, or an adapter.
        self._project_planner = project_planner
        # Server-side bound on the revision loop. When no explicit budget is
        # supplied it is derived from the existing policy, so the loop stays
        # bounded either way and nothing per-run can widen it.
        self._revision_budget = revision_budget or RevisionBudget(
            max_revisions=self.policy.max_revision_attempts
        )
        if not isinstance(self._revision_budget, RevisionBudget):
            raise RevisionError("revision_budget must be a RevisionBudget")
        # The existing production ToolExecutor owns permission checks, approval,
        # and the tool call itself. The harness only composes the input for it
        # from the run's frozen perimeter.
        self._tool_executor = tool_executor

    def _enforce_run_scope(self, request: HarnessRequest) -> None:
        """Freeze and enforce the run's security perimeter before any read.

        This runs before context assembly, memory, knowledge, or provider output
        is touched. It fails closed when the request tries to carry authority
        that the frozen scope does not grant.
        """
        scope = getattr(request, "run_scope", None)
        if scope is None:
            # A frozen scope must not be silently abandoned, and a run that
            # declares dispatch authority must carry an explicit scope.
            if RunScope.frozen_scope(request.run_id) is not None:
                raise RunScopeError(
                    f"run '{request.run_id}' has a frozen scope and cannot run without it"
                )
            if getattr(request, "execution_requests", ()) or getattr(
                request, "allowed_execution_commands", None
            ):
                raise RunScopeError(
                    f"run '{request.run_id}' declares dispatch authority and requires "
                    "an explicit RunScope before it may dispatch"
                )
            return
        if not isinstance(scope, RunScope):
            raise RunScopeError("run_scope must be a RunScope")
        if scope.run_id != request.run_id:
            raise RunScopeError("run scope does not belong to this run")

        scope.validate_workspace(request.workspace)
        scope.validate_command_set(request.allowed_execution_commands)
        scope.validate_acceptance_criteria(request.acceptance_criteria)
        scope.validate_tool_set(getattr(request, "allowed_tool_ids", None))
        # Declared tool intents may only narrow the frozen tool perimeter. A
        # request that declares an intent for a tool the run does not authorize is
        # rejected before anything is planned, decided, or executed.
        declared_tool_ids = {
            str(getattr(intent, "tool_id", ""))
            for intent in (getattr(request, "tool_requests", ()) or ())
        }
        declared_tool_ids.discard("")
        if declared_tool_ids - set(scope.allowed_tool_ids):
            raise RunScopeError(
                "declared tool intents exceed the frozen run tool set"
            )

        scope.freeze()
        require_active_scope(request.run_id, scope)

    def _planning_goal(self, request: HarnessRequest) -> str:
        """Extract the trusted goal the planner may work from.

        The goal comes only from the task specification's own text, which the
        trusted server-side composition supplies. Nothing here reads a decision,
        an LLM output, or a client-supplied authority field.
        """
        spec = request.task_specification
        if spec is None:
            return ""
        # Only descriptive text is a goal. The task id is an identity, not an
        # intention, so it is never used to fabricate one.
        for attribute in ("description", "title"):
            value = getattr(spec, attribute, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def run_verification(
        self,
        request: HarnessRequest,
        emit: Callable[[EventType, Mapping[str, object]], None],
    ) -> tuple[tuple[VerificationResult, ...], AcceptanceResult | None]:
        """Verify the request's frozen expectations and evaluate acceptance.

        This is the single verification/acceptance implementation: the run loop's
        ``RUN_VERIFICATION`` action calls it, and a trusted production caller may
        call it directly. It uses only expectations and criteria carried by the
        request, so neither a decision nor a caller of this method can choose what
        is verified or declare the verdict itself.
        """
        verified: list[VerificationResult] = []
        for crit_id, exp in request.verification_expectations.items():
            v_res = self._verifier.verify(
                exp,
                workspace=request.workspace,
                run_id=request.run_id,
                observer=emit,
                criterion_id=crit_id,
            )
            verified.append(v_res)

        if not verified:
            # Nothing was verified, so there is nothing to accept. Returning no
            # acceptance result keeps missing verification distinguishable from a
            # pass - a caller must not read this as acceptance.
            return (), None

        v_dict = {
            (v.criterion_id or f"crit-{idx}"): v
            for idx, v in enumerate(verified)
        }
        acceptance = self._acceptance_gate.evaluate(
            criteria=request.acceptance_criteria,
            verifications=v_dict,
            run_id=request.run_id,
            observer=emit,
            requirements=request.requirements if request.requirements else None,
        )
        return tuple(verified), acceptance

    def run(self, request: HarnessRequest) -> HarnessResult:
        """Execute the controlled, bounded Run Loop according to configured policy."""
        self._enforce_run_scope(request)
        collector = RunEventCollector(request.run_id)

        current_state = HarnessState(
            run_id=request.run_id,
            attempt_number=request.attempt_number,
            iteration=0,
            phase=HarnessPhase.OBSERVE,
            status=HarnessStatus.RUNNING,
        )

        def emit(event_type: EventType, data: Mapping[str, object] | None = None) -> None:
            payload = data or {}
            # Every emitted event carries the same run and task identity, so a
            # discovery, decision, or execution event can never be attributed to
            # a different run. The identity comes from the task specification when
            # present, otherwise from the server-side request metadata.
            task_id = (
                request.task_specification.task_id
                if request.task_specification
                else request.metadata.get("task_id")
            )
            if task_id is not None and "task_id" not in payload:
                payload = {**payload, "task_id": task_id}
            collector.emit(
                event_type,
                attempt_number=current_state.attempt_number,
                task_id=task_id,
                metadata=payload,
            )
            if self._observer is not None:
                # A failing observer must never abort or alter the run loop: the
                # harness owns control flow, the observer only records.
                try:
                    self._observer(event_type, payload)
                except Exception:  # noqa: BLE001 - observation must not break a run
                    pass

        emit(EventType.HARNESS_STARTED, {"policy": repr(self.policy)})
        iterations_history: list[HarnessState] = []
        observations: list[StructuredObservation] = []
        decisions: list[Decision] = []
        execution_results: list[ExecutionResult] = []
        verification_results: list[VerificationResult] = []
        acceptance_result: AcceptanceResult | None = None

        action_count = 0
        execution_count = 0
        revision_count = 0
        exec_index = 0

        # Tool stage bookkeeping. The trusted tool set comes from the run's frozen
        # scope, never from the request, a decision, or a model.
        tool_results: list[BoundedToolResult] = []
        tool_index = 0
        tool_count = 0
        tool_requests = tuple(getattr(request, "tool_requests", ()) or ())
        scope_for_tools = request.run_scope
        allowed_tool_ids = (
            frozenset(getattr(scope_for_tools, "allowed_tool_ids", frozenset()) or ())
            if scope_for_tools is not None
            else frozenset()
        )
        # A tool stage exists only when the run truly authorizes tools and has a
        # registered executor. Otherwise the action is never offered.
        tools_available = bool(
            tool_requests and allowed_tool_ids and self._tool_executor is not None
        )
        authorized_tool_call: AuthorizedToolCall | None = None
        tool_denial_reason = ""
        last_tool_outcome = ""

        # Revision loop bookkeeping. Every value here is server-side; nothing
        # per-run can change the budget, the counter, or the no-progress history.
        revision_decision: RevisionDecision | None = None
        revision_number = 0
        revision_plan_pending = False
        seen_plan_fingerprints: list[str] = []
        seen_plan_structures: list[str] = []
        plan_structure = ""
        verification_attempted = False
        last_execution_outcome = ""
        base_goal = ""

        current_project_state = request.initial_project_state or self._state_derivator(
            run_id=request.run_id,
            attempt_number=request.attempt_number,
            task_id=request.task_specification.task_id if request.task_specification else "",
        )

        # Bounded, server-side project observation for this run. It is produced
        # once, before the first context assembly, from the request's trusted
        # workspace. A discovery failure is never silently downgraded: the run
        # fails closed and terminally rather than deciding on a fabricated or
        # partial picture of the project.
        understanding_snapshot: object | None = None
        discovery_failed = False
        if self._project_discovery is not None and request.workspace is not None:
            emit(
                EventType.PROJECT_DISCOVERY_STARTED,
                {"run_id": request.run_id},
            )
            try:
                outcome = self._project_discovery.observe(
                    request.workspace, run_id=request.run_id
                )
            except Exception as exc:  # noqa: BLE001 - a broken observer is a failure
                # Discovery is observation only, so a raising implementation is
                # reported as a discovery failure rather than being allowed to
                # abort the loop with an untyped exception.
                from app.agent_runtime.project_discovery import DiscoveryOutcome

                outcome = DiscoveryOutcome(
                    run_id=request.run_id,
                    snapshot=None,
                    duration_seconds=0.0,
                    failure_category=type(exc).__name__,
                )
            understanding_snapshot = outcome.snapshot
            discovery_failed = not outcome.succeeded
            emit(EventType.PROJECT_DISCOVERY_COMPLETED, outcome.event_metadata())
            if discovery_failed:
                # Fail closed and terminally. A run whose observation could not be
                # trusted must not proceed to decide or act on a fabricated or
                # partial picture of the project, and it must not report a wait
                # state that implies it could continue.
                emit(
                    EventType.HARNESS_FAILED,
                    {"reason": "project_discovery_failed", "stage": "discover"},
                )
                final_state = replace(
                    current_state,
                    phase=HarnessPhase.FAILED,
                    status=HarnessStatus.FAILED,
                    terminal=True,
                    metadata={
                        "reason": "project_discovery_failed",
                        "failure_category": outcome.failure_category,
                    },
                )
                return HarnessResult(
                    run_id=request.run_id,
                    final_state=final_state,
                    iterations=(final_state,),
                    observations=(),
                    decisions=(),
                    execution_results=(),
                    verification_results=(),
                    final_acceptance=None,
                    final_project_state=current_project_state,
                    events=tuple(collector.events),
                )



        # Declarative planning stage, extracted so the revision loop can replan
        # by calling the same existing Planner. It runs after the observation and
        # before the decision, so the decision is informed by a validated
        # intention. A plan is not authority: it names intentions only, and any
        # real action still has to pass the existing authorization chain.
        base_goal = self._planning_goal(request)
        execution_plan: object | None = None
        plan_fingerprint = ""

        def plan_once(revision_goal: str = "") -> object | None:
            """Produce and validate one plan; returns None when unusable."""
            nonlocal execution_plan, plan_fingerprint, plan_structure
            emit(EventType.PLANNING_STARTED, {"run_id": request.run_id})
            started_at = time.perf_counter()
            candidate_plan: object | None = None
            failure = ""
            goal_text = revision_goal or base_goal
            try:
                if goal_text:
                    candidate = self._project_planner.plan_for_run(
                        goal=goal_text,
                        run_id=request.run_id,
                        task_id=request.metadata.get("task_id") or request.run_id,
                    )
                    # A plan is usable only once server-side validation accepts it
                    # as this run's plan. An unusable plan is reported, never used.
                    validated, _order = require_valid_plan(
                        candidate,
                        run_id=request.run_id,
                        task_id=request.metadata.get("task_id") or request.run_id,
                    )
                    candidate_plan = validated
                failure = "" if candidate_plan is not None else "no_goal"
            except Exception as exc:  # noqa: BLE001 - a broken planner is a failure
                candidate_plan = None
                failure = type(exc).__name__
            elapsed = time.perf_counter() - started_at

            if candidate_plan is None:
                emit(
                    EventType.PLANNING_COMPLETED,
                    {
                        "run_id": request.run_id,
                        "status": "failed",
                        "failure_category": failure or "planner_error",
                        "duration_seconds": round(elapsed, 3),
                    },
                )
                return None

            summary = getattr(candidate_plan, "bounded_summary", None)
            payload = summary() if callable(summary) else {}
            emit(
                EventType.PLANNING_COMPLETED,
                {
                    "run_id": request.run_id,
                    "status": "completed",
                    "duration_seconds": round(elapsed, 3),
                    **dict(payload),
                },
            )
            execution_plan = candidate_plan
            plan_fingerprint = str(payload.get("plan_fingerprint") or "")
            plan_structure = plan_structure_signature(
                getattr(candidate_plan, "steps", ())
            )
            if plan_fingerprint:
                seen_plan_fingerprints.append(plan_fingerprint)
            if plan_structure:
                seen_plan_structures.append(plan_structure)
            return candidate_plan

        if self._project_planner is not None:
            plan_once()

        while not current_state.terminal:
            # 0. Check bounds
            if current_state.iteration >= self.policy.max_iterations:
                current_state = replace(
                    current_state,
                    phase=HarnessPhase.FAILED,
                    status=HarnessStatus.LIMIT_REACHED,
                    terminal=True,
                    metadata={"limit": "max_iterations", "value": self.policy.max_iterations},
                )
                emit(
                    EventType.HARNESS_LIMIT_REACHED,
                    {"reason": "max_iterations_reached", "iteration": current_state.iteration},
                )
                iterations_history.append(current_state)
                break

            if action_count >= self.policy.max_actions:
                current_state = replace(
                    current_state,
                    phase=HarnessPhase.FAILED,
                    status=HarnessStatus.LIMIT_REACHED,
                    terminal=True,
                    metadata={"limit": "max_actions", "value": self.policy.max_actions},
                )
                emit(
                    EventType.HARNESS_LIMIT_REACHED,
                    {"reason": "max_actions_reached", "action_count": action_count},
                )
                iterations_history.append(current_state)
                break

            emit(EventType.HARNESS_ITERATION_STARTED, {"iteration": current_state.iteration})

            # PHASE 1: OBSERVE
            current_state = replace(current_state, phase=HarnessPhase.OBSERVE)
            emit(
                EventType.HARNESS_PHASE_CHANGED,
                {"phase": HarnessPhase.OBSERVE.value, "iteration": current_state.iteration},
            )

            # PHASE 2: CONTEXT
            current_state = replace(current_state, phase=HarnessPhase.CONTEXT)
            emit(
                EventType.HARNESS_PHASE_CHANGED,
                {"phase": HarnessPhase.CONTEXT.value, "iteration": current_state.iteration},
            )

            conditions: list[str] = []
            if current_state.status == HarnessStatus.WAITING_FOR_APPROVAL:
                conditions.append("approval_pending")
            if revision_count >= self.policy.max_revision_attempts:
                conditions.append("revision_limit_reached")
            if discovery_failed:
                # Discovery could not produce a trustworthy observation, so the
                # decision must not proceed as though the context were complete.
                conditions.append("discovery_failed")
            for res in execution_results:
                if res.outcome_status == ExecutionOutcomeStatus.PERMISSION_DENIED:
                    conditions.append("permission_denied")
                elif res.outcome_status in (
                    ExecutionOutcomeStatus.POLICY_DENIED,
                    ExecutionOutcomeStatus.APPROVAL_REJECTED,
                ):
                    conditions.append("policy_denied")
                elif res.outcome_status == ExecutionOutcomeStatus.APPROVAL_WAITING:
                    conditions.append("approval_pending")

            # Evaluate available skills
            applicable_skills: tuple[SkillDefinition, ...] = ()
            if getattr(request, "available_skills", None):
                avail_tools: set[str] = set()
                if getattr(request, "allowed_execution_commands", None):
                    avail_tools.update(request.allowed_execution_commands)
                    for cmd in request.allowed_execution_commands:
                        if "python" in str(cmd).lower():
                            avail_tools.add("python_test_runner")
                for ex in getattr(request, "execution_requests", ()) or ():
                    tool_id = getattr(ex, "tool_id", None)
                    if tool_id:
                        avail_tools.add(str(tool_id))
                    if hasattr(ex, "metadata") and isinstance(ex.metadata, dict):
                        m_tid = ex.metadata.get("tool_id")
                        if m_tid:
                            avail_tools.add(str(m_tid))
                applicable_skills = self._skill_evaluator.find_applicable_skills(
                    request.available_skills,
                    task=request.task_specification,
                    available_capabilities=frozenset(getattr(request, "available_capabilities", ()) or ()),
                    available_tool_ids=frozenset(avail_tools),
                )

            project_memory: tuple[Any, ...] = ()
            if self._memory_store is not None:
                proj_id = getattr(request, "project_id", None)
                if not proj_id and hasattr(request, "metadata") and isinstance(request.metadata, dict):
                    proj_id = request.metadata.get("project_id")
                if not proj_id and request.task_specification:
                    proj_id = getattr(request.task_specification, "project_id", None)
                if not proj_id:
                    proj_id = request.run_id

                if hasattr(self._memory_store, "get_active_memory"):
                    project_memory = tuple(self._memory_store.get_active_memory(str(proj_id)))
                elif hasattr(self._memory_store, "query"):
                    from app.memory.models import MemoryQuery

                    project_memory = tuple(self._memory_store.query(MemoryQuery(project_id=str(proj_id))))

            try:
                model_info = None
                dp_model = getattr(self._decision_provider, "model_name", None)
                dp_provider = getattr(self._decision_provider, "provider_name", None)
                if dp_model:
                    try:
                        from app.agents.providers.model_registry import create_default_registry

                        mreg = getattr(request, "model_registry", None) or create_default_registry()
                        model_info = mreg.get_model(dp_model, provider_id=dp_provider)
                    except Exception:
                        model_info = None

                # The plan reaches the decision as bounded planning context. It
                # is a declarative intention, not authority, and only its counted
                # summary and step purposes are exposed.
                planning_context: dict[str, object] = {}
                if execution_plan is not None:
                    plan_dict = getattr(execution_plan, "to_context_dict", None)
                    planning_context = {
                        "plan": plan_dict() if callable(plan_dict) else {}
                    }

                context_envelope = self._context_assembler.assemble(
                    run_id=request.run_id,
                    attempt_number=current_state.attempt_number,
                    task_id=request.task_specification.task_id if request.task_specification else "",
                    task_specification=request.task_specification,
                    project_state=current_project_state,
                    verification_results=tuple(verification_results),
                    acceptance_result=acceptance_result,
                    blocking_conditions=tuple(conditions),
                    requirements=request.requirements,
                    acceptance_criteria=request.acceptance_criteria,
                    skills=applicable_skills,
                    project_memory=project_memory,
                    understanding_snapshot=understanding_snapshot,
                    metadata={**dict(request.metadata), **planning_context},
                    model_info=model_info,
                    budget_policy=getattr(request, "budget_policy", None),
                )
            except Exception as exc:
                current_state = replace(
                    current_state,
                    phase=HarnessPhase.FAILED,
                    status=HarnessStatus.FAILED,
                    terminal=True,
                    metadata={"error": str(exc)},
                )
                emit(EventType.HARNESS_FAILED, {"error": str(exc)})
                iterations_history.append(current_state)
                break

            # PHASE 3: DECIDE
            current_state = replace(
                current_state,
                phase=HarnessPhase.DECIDE,
                latest_context_id=context_envelope.context_id,
                latest_context_fingerprint=context_envelope.context_fingerprint,
            )
            emit(
                EventType.HARNESS_PHASE_CHANGED,
                {"phase": HarnessPhase.DECIDE.value, "iteration": current_state.iteration},
            )

            acc_status = (
                current_project_state.acceptance_status
                if current_project_state and current_project_state.acceptance_status
                else (acceptance_result.status.value if acceptance_result else None)
            )
            verif_summary = (
                f"passed={sum(1 for v in verification_results if getattr(v.status, 'value', str(v.status)) == 'passed')},"
                f"failed={sum(1 for v in verification_results if getattr(v.status, 'value', str(v.status)) != 'passed')}"
            ) if verification_results else None

            dec_req = DecisionRequest(
                decision_id=str(uuid4()),
                run_id=request.run_id,
                attempt_number=current_state.attempt_number,
                task_id=request.task_specification.task_id if request.task_specification else "",
                current_project_state=current_project_state,
                acceptance_status=acc_status,
                verification_status_summary=verif_summary,
                blocking_conditions=tuple(conditions),
                context_id=context_envelope.context_id,
                context_fingerprint=context_envelope.context_fingerprint,
                context_envelope=context_envelope,
                # Only a run whose frozen scope authorizes tools, that declared a
                # tool intent, and that has a registered executor may be offered
                # the tool action. A single tool action is offered per run.
                available_actions=(
                    (DecisionAction.INVOKE_TOOL,)
                    if tools_available and tool_count == 0
                    else ()
                ),
            )
            emit(
                EventType.DECISION_REQUESTED,
                {"decision_id": dec_req.decision_id, "iteration": current_state.iteration},
            )
            decision = self._decision_provider.decide(dec_req)
            emit(
                EventType.DECISION_MADE,
                {
                    "decision_id": decision.decision_id,
                    "action": decision.action.value,
                    "decision_type": decision.decision_type.value,
                },
            )
            decisions.append(decision)
            current_state = replace(current_state, latest_decision_id=decision.decision_id)

            # PHASE 4: VALIDATE
            current_state = replace(current_state, phase=HarnessPhase.VALIDATE)
            emit(
                EventType.HARNESS_PHASE_CHANGED,
                {"phase": HarnessPhase.VALIDATE.value, "iteration": current_state.iteration},
            )

            val = self._decision_validator(decision, dec_req, current_project_state)
            if not val.valid:
                emit(
                    EventType.DECISION_REJECTED,
                    {"decision_id": decision.decision_id, "errors": list(val.errors)},
                )
                if self.policy.fail_on_unknown_decision or any(
                    "premature" in e or "invalid" in e for e in val.errors
                ):
                    current_state = replace(
                        current_state,
                        phase=HarnessPhase.FAILED,
                        status=HarnessStatus.FAILED,
                        terminal=True,
                        metadata={"validation_errors": list(val.errors)},
                    )
                    emit(
                        EventType.HARNESS_FAILED,
                        {"reason": "decision_validation_failed", "errors": list(val.errors)},
                    )
                    iterations_history.append(current_state)
                    break

            # PHASE 5: AUTHORIZE
            current_state = replace(current_state, phase=HarnessPhase.AUTHORIZE)
            emit(
                EventType.HARNESS_PHASE_CHANGED,
                {"phase": HarnessPhase.AUTHORIZE.value, "iteration": current_state.iteration},
            )

            authorized = False
            auth_denial_reason = ""
            auth_waiting = False

            action = decision.action
            if action == DecisionAction.COMPLETE_RUN:
                if (
                    acceptance_result is not None
                    and acceptance_result.status == AcceptanceStatus.PASS
                ):
                    authorized = True
                else:
                    authorized = False
                    auth_denial_reason = "acceptance_not_passed"

            elif action == DecisionAction.FAIL_RUN:
                authorized = True

            elif action in (
                DecisionAction.WAIT_FOR_APPROVAL,
                DecisionAction.REQUEST_USER_APPROVAL,
            ):
                authorized = True
                auth_waiting = True

            elif action == DecisionAction.RUN_VERIFICATION:
                verification_attempted = True
                authorized = True

            elif action == DecisionAction.INVOKE_TOOL:
                # A tool action is authorized here, server-side, from the run's
                # frozen tool perimeter. The decision selects an index; it never
                # supplies a tool identity, an argument, or any authority.
                authorized_tool_call = None
                tool_denial_reason = ""
                if self._tool_executor is None:
                    tool_denial_reason = "no_tool_executor"
                elif not allowed_tool_ids:
                    tool_denial_reason = "no_tools_authorized"
                elif tool_count > 0:
                    # One tool action per run keeps the loop bounded and stops a
                    # decision from driving repeated invocations.
                    tool_denial_reason = "tool_limit_reached"
                elif tool_index >= len(tool_requests):
                    tool_denial_reason = "no_tool_request"
                else:
                    intent = tool_requests[tool_index]
                    if not isinstance(intent, ToolIntent):
                        tool_denial_reason = "invalid_tool_intent"
                    elif intent.tool_id not in allowed_tool_ids:
                        # An intent is never self-authorizing.
                        tool_denial_reason = "tool_not_allowed"
                    else:
                        try:
                            authorized_tool_call = authorize_tool_intent(
                                intent,
                                run_id=request.run_id,
                                task_id=(
                                    request.metadata.get("task_id") or request.run_id
                                ),
                                allowed_tool_ids=allowed_tool_ids,
                                context_fingerprint=context_envelope.context_fingerprint,
                                workspace=request.workspace,
                                run_scope=request.run_scope,
                                round_number=tool_count,
                                attempt_number=current_state.attempt_number,
                            )
                        except ToolAuthorizationError as exc:
                            tool_denial_reason = f"tool_authorization_failed:{type(exc).__name__}"
                authorized = authorized_tool_call is not None
                if not authorized:
                    auth_denial_reason = tool_denial_reason or "tool_not_authorized"

            elif action == DecisionAction.REQUEST_REVISION:
                # A revision is never granted merely because a decision asked for
                # one. Eligibility is objective and server-side: a real
                # verification outcome, an actionable failure, and remaining
                # budget. Security, authority, validation, and no-progress
                # failures terminate instead of being retried.
                revision_decision = evaluate_revision(
                    eligibility=RevisionEligibility(
                        run_id=request.run_id,
                        task_id=request.metadata.get("task_id") or request.run_id,
                        revision_number=revision_count,
                        verification_attempted=verification_attempted,
                        acceptance_passed=bool(
                            acceptance_result is not None
                            and acceptance_result.status == AcceptanceStatus.PASS
                        ),
                        execution_outcome=last_execution_outcome,
                        plan_id=getattr(execution_plan, "plan_id", ""),
                        plan_fingerprint=plan_fingerprint,
                        plan_structure=plan_structure,
                    ),
                    budget=self._revision_budget,
                    base_goal=base_goal,
                )
                if revision_decision.revision_allowed:
                    authorized = True
                    # The revision about to run is numbered from one; `revision_count`
                    # is the number of revisions already completed.
                    revision_number = revision_count + 1
                else:
                    authorized = False
                    auth_denial_reason = revision_decision.reason

            elif action == DecisionAction.EXECUTE:
                if execution_count >= self.policy.max_execution_attempts:
                    authorized = False
                    auth_denial_reason = "execution_limit_reached"
                else:
                    if exec_index < len(request.execution_requests):
                        current_exec_req = request.execution_requests[exec_index]
                        allowed_cmds = request.allowed_execution_commands
                        if allowed_cmds is None:
                            authorized = False
                            auth_denial_reason = "permission_missing"
                        elif not command_is_allowed(current_exec_req.command, allowed_cmds):
                            authorized = False
                            auth_denial_reason = "permission_denied"
                        else:
                            authorized = True
                    else:
                        authorized = False
                        auth_denial_reason = "no_execution_request"
            else:
                authorized = False
                auth_denial_reason = "unknown_action"

            # PHASE 6: ACT (at most one authoritative action)
            current_state = replace(current_state, phase=HarnessPhase.ACT)
            emit(
                EventType.HARNESS_PHASE_CHANGED,
                {"phase": HarnessPhase.ACT.value, "iteration": current_state.iteration},
            )
            action_count += 1

            action_outcome = ""
            latest_exec_id = None
            latest_verif_id = None

            if not authorized:
                if auth_waiting or auth_denial_reason == "approval_waiting":
                    action_outcome = "approval_waiting"
                    current_state = replace(
                        current_state,
                        phase=HarnessPhase.WAITING,
                        status=HarnessStatus.WAITING_FOR_APPROVAL,
                        terminal=True,
                        metadata={"reason": "approval_waiting"},
                    )
                    emit(EventType.EXECUTION_DENIED, {"reason": "approval_waiting"})
                    emit(EventType.HARNESS_PHASE_CHANGED, {"phase": HarnessPhase.WAITING.value})
                elif auth_denial_reason == "permission_denied":
                    action_outcome = "permission_denied"
                    current_state = replace(
                        current_state,
                        phase=HarnessPhase.FAILED,
                        status=HarnessStatus.FAILED,
                        terminal=True,
                        metadata={"reason": "permission_denied"},
                    )
                    emit(EventType.EXECUTION_DENIED, {"reason": "permission_denied"})
                    emit(EventType.HARNESS_FAILED, {"reason": "permission_denied"})
                elif auth_denial_reason == "approval_rejected":
                    action_outcome = "approval_rejected"
                    current_state = replace(
                        current_state,
                        phase=HarnessPhase.FAILED,
                        status=HarnessStatus.FAILED,
                        terminal=True,
                        metadata={"reason": "approval_rejected"},
                    )
                    emit(EventType.EXECUTION_DENIED, {"reason": "approval_rejected"})
                    emit(EventType.HARNESS_FAILED, {"reason": "approval_rejected"})
                elif action == DecisionAction.REQUEST_REVISION:
                    # A refused revision is a bounded refusal, not a failure of the
                    # run: the decision was evaluated server-side and denied for a
                    # concrete reason, and the loop continues until one of its
                    # configured bounds ends it. Nothing was executed.
                    action_outcome = f"revision_denied:{auth_denial_reason}"
                    emit(
                        EventType.EXECUTION_DENIED,
                        {"reason": auth_denial_reason, "action": "REQUEST_REVISION"},
                    )
                else:
                    action_outcome = f"denied:{auth_denial_reason}"
                    current_state = replace(
                        current_state,
                        phase=HarnessPhase.FAILED,
                        status=HarnessStatus.FAILED,
                        terminal=True,
                        metadata={"reason": auth_denial_reason},
                    )
                    emit(EventType.HARNESS_FAILED, {"reason": auth_denial_reason})
            else:
                if action == DecisionAction.COMPLETE_RUN:
                    action_outcome = "completed"
                    current_state = replace(
                        current_state,
                        phase=HarnessPhase.COMPLETE,
                        status=HarnessStatus.COMPLETED,
                        terminal=True,
                    )
                    emit(EventType.HARNESS_COMPLETED, {"status": "completed"})

                elif action == DecisionAction.FAIL_RUN:
                    action_outcome = "failed"
                    current_state = replace(
                        current_state,
                        phase=HarnessPhase.FAILED,
                        status=HarnessStatus.FAILED,
                        terminal=True,
                    )
                    emit(EventType.HARNESS_FAILED, {"status": "failed"})

                elif action in (
                    DecisionAction.WAIT_FOR_APPROVAL,
                    DecisionAction.REQUEST_USER_APPROVAL,
                ):
                    action_outcome = "approval_waiting"
                    current_state = replace(
                        current_state,
                        phase=HarnessPhase.WAITING,
                        status=HarnessStatus.WAITING_FOR_APPROVAL,
                        terminal=True,
                    )
                    emit(EventType.HARNESS_PHASE_CHANGED, {"phase": HarnessPhase.WAITING.value})

                elif action == DecisionAction.REQUEST_REVISION:
                    assert revision_decision is not None
                    revision_count += 1
                    action_outcome = "revision_requested"
                    current_state = replace(
                        current_state,
                        attempt_number=current_state.attempt_number + 1,
                    )
                    emit(
                        EventType.REVISION_STARTED,
                        {
                            # The server-side revision number wins over the
                            # decision's own record of the opportunity index.
                            **revision_decision.bounded_summary(),
                            "revision_number": revision_number,
                        },
                    )
                    # The old plan stays immutable. The next iteration asks the
                    # same Planner for a plan against the bounded revision goal,
                    # and that plan is validated again before it is used.
                    previous_plan_fingerprint = plan_fingerprint
                    previous_plan_structure = plan_structure
                    replanned = plan_once(revision_decision.revision_goal)
                    new_fingerprint = plan_fingerprint
                    new_structure = plan_structure
                    # Progress means a genuinely different intention set: a new
                    # identity describing the same ordered intentions is a blind
                    # retry, and so is reusing any earlier shape.
                    # `plan_once` has just appended the new shape, so the history
                    # without its final entry is the set of superseded shapes.
                    superseded_structures = seen_plan_structures[:-1]
                    progressed = (
                        replanned is not None
                        and bool(new_fingerprint)
                        and new_fingerprint != previous_plan_fingerprint
                        and bool(new_structure)
                        and new_structure != previous_plan_structure
                        and new_structure not in superseded_structures
                    )
                    emit(
                        EventType.REVISION_COMPLETED,
                        {
                            "revision_number": revision_number,
                            "run_id": request.run_id,
                            "status": "replanned" if progressed else "no_progress",
                            "previous_plan_fingerprint": previous_plan_fingerprint,
                            "plan_fingerprint": new_fingerprint,
                            "plan_id": getattr(replanned, "plan_id", ""),
                        },
                    )
                    if not progressed:
                        # A revision that cannot produce a genuinely new plan is a
                        # blind retry. Terminate rather than loop.
                        current_state = replace(
                            current_state,
                            phase=HarnessPhase.FAILED,
                            status=HarnessStatus.FAILED,
                            terminal=True,
                            metadata={"reason": "revision_no_progress"},
                        )
                        emit(
                            EventType.HARNESS_FAILED,
                            {"reason": "revision_no_progress"},
                        )
                    revision_decision = None

                elif action == DecisionAction.INVOKE_TOOL:
                    assert authorized_tool_call is not None
                    tool_count += 1
                    tool_index += 1
                    action_outcome = "tool_requested"
                    # No extra request event is emitted here: the existing
                    # ToolExecutor already emits TOOL_INVOCATION_REQUESTED,
                    # PERMISSION_CHECKED (the authorizing record), and the terminal
                    # tool event, so the trail keeps exactly one source.
                    try:
                        # The existing production ToolExecutor owns the permission
                        # check, the approval flow, and the tool call itself. The
                        # harness supplies only the server-composed invocation and
                        # the context derived from the frozen scope.
                        raw_result = self._tool_executor.execute(
                            authorized_tool_call.invocation,
                            context=authorized_tool_call.context,
                            observer=lambda event_type, data: emit(event_type, dict(data)),
                        )
                    except Exception as exc:  # noqa: BLE001 - a raising tool is a failure
                        raw_result = None
                        last_tool_outcome = f"tool_executor_error:{type(exc).__name__}"

                    bounded = bound_tool_result(
                        raw_result, tool_id=authorized_tool_call.tool_id
                    )
                    if not last_tool_outcome:
                        last_tool_outcome = bounded.status.value
                    tool_results.append(bounded)
                    # The executor already recorded the tool's own terminal event.
                    # This is the harness's bounded projection of the result: one
                    # sanitized record per invocation, never the raw output.
                    emit(
                        EventType.TOOL_RESULT_BOUNDED,
                        {
                            **authorized_tool_call.event_metadata(),
                            **bounded.bounded_summary(),
                            "revision_number": revision_number,
                        },
                    )
                    if bounded.status == ToolStatus.DENIED:
                        # A denial is a policy/security outcome: it ends the run
                        # rather than being retried or worked around.
                        current_state = replace(
                            current_state,
                            phase=HarnessPhase.FAILED,
                            status=HarnessStatus.FAILED,
                            terminal=True,
                            metadata={"reason": "tool_denied"},
                        )
                        emit(EventType.HARNESS_FAILED, {"reason": "tool_denied"})
                    elif bounded.status == ToolStatus.WAITING_FOR_APPROVAL:
                        current_state = replace(
                            current_state,
                            phase=HarnessPhase.WAITING,
                            status=HarnessStatus.WAITING_FOR_APPROVAL,
                            terminal=True,
                            metadata={"reason": "tool_approval_waiting"},
                        )
                        emit(
                            EventType.HARNESS_PHASE_CHANGED,
                            {"phase": HarnessPhase.WAITING.value},
                        )
                    authorized_tool_call = None

                elif action == DecisionAction.EXECUTE:
                    execution_count += 1
                    current_exec_req = request.execution_requests[exec_index]
                    exec_index += 1
                    coordinator = self._execution_coordinator or ExecutionCoordinator(
                        LocalExecutionAdapter(
                            workspace_root=request.workspace.root if request.workspace else None
                        )
                    )
                    allowed_cmds = (
                        frozenset(request.allowed_execution_commands)
                        if request.allowed_execution_commands is not None
                        else None
                    )
                    exec_res = coordinator.execute(
                        current_exec_req,
                        workspace_root=request.workspace.root if request.workspace else None,
                        run_id=request.run_id,
                        allowed_commands=allowed_cmds,
                        approval_policy=request.approval_policy,
                        approval_resolver=request.approval_resolver,
                        run_scope=request.run_scope,
                        observer=lambda event_type, data: emit(event_type, data),
                    )
                    latest_exec_id = exec_res.request_id

                    # Record the trusted execution outcome so revision eligibility
                    # can classify the failure objectively.
                    last_execution_outcome = exec_res.outcome_status.value
                    if exec_res.outcome_status == ExecutionOutcomeStatus.APPROVAL_WAITING:
                        action_outcome = "approval_waiting"
                        current_state = replace(
                            current_state,
                            phase=HarnessPhase.WAITING,
                            status=HarnessStatus.WAITING_FOR_APPROVAL,
                            terminal=True,
                            metadata={"reason": "approval_waiting"},
                        )
                        emit(EventType.HARNESS_PHASE_CHANGED, {"phase": HarnessPhase.WAITING.value})
                    elif exec_res.outcome_status == ExecutionOutcomeStatus.APPROVAL_REJECTED:
                        action_outcome = "approval_rejected"
                        current_state = replace(
                            current_state,
                            phase=HarnessPhase.FAILED,
                            status=HarnessStatus.FAILED,
                            terminal=True,
                            metadata={"reason": "approval_rejected"},
                        )
                        emit(EventType.HARNESS_FAILED, {"reason": "approval_rejected"})
                    elif exec_res.outcome_status in (
                        ExecutionOutcomeStatus.PERMISSION_DENIED,
                        ExecutionOutcomeStatus.POLICY_DENIED,
                    ):
                        action_outcome = exec_res.outcome_status.value
                        current_state = replace(
                            current_state,
                            phase=HarnessPhase.FAILED,
                            status=HarnessStatus.FAILED,
                            terminal=True,
                            metadata={"reason": exec_res.outcome_status.value},
                        )
                        emit(EventType.HARNESS_FAILED, {"reason": exec_res.outcome_status.value})
                    else:
                        execution_results.append(exec_res)
                        action_outcome = exec_res.outcome_status.value
                        if exec_res.outcome_status != ExecutionOutcomeStatus.EXECUTION_SUCCESS:
                            current_state = replace(
                                current_state,
                                phase=HarnessPhase.FAILED,
                                status=HarnessStatus.FAILED,
                                terminal=True,
                                metadata={"reason": exec_res.outcome_status.value},
                            )
                            emit(EventType.HARNESS_FAILED, {"reason": exec_res.outcome_status.value})

                elif action == DecisionAction.RUN_VERIFICATION:
                    current_state = replace(current_state, phase=HarnessPhase.VERIFY)
                    emit(
                        EventType.HARNESS_PHASE_CHANGED,
                        {"phase": HarnessPhase.VERIFY.value, "iteration": current_state.iteration},
                    )

                    verifs_for_this_round, acceptance_result = self.run_verification(
                        request,
                        emit,
                    )
                    verification_attempted = True
                    verification_results.extend(verifs_for_this_round)
                    if verifs_for_this_round:
                        latest_verif_id = verifs_for_this_round[-1].verification_id
                    action_outcome = (
                        acceptance_result.status.value
                        if acceptance_result is not None
                        else "not_evaluated"
                    )

            # PHASE 7: OBSERVE_RESULT
            obs = StructuredObservation(
                action=action.value if hasattr(action, "value") else str(action),
                result_status=action_outcome,
                execution_result_id=latest_exec_id,
                verification_id=latest_verif_id,
                acceptance_status=acceptance_result.status.value if acceptance_result else None,
                project_state=current_project_state.status.value if current_project_state else None,
                metadata={"action_count": action_count, "iteration": current_state.iteration},
            )
            observations.append(obs)
            emit(
                EventType.HARNESS_OBSERVATION_RECORDED,
                {
                    "action": obs.action,
                    "result_status": obs.result_status,
                    "iteration": current_state.iteration,
                },
            )

            # PHASE 8: UPDATE_STATE
            # Preserve the terminal phase (e.g. COMPLETE, WAITING, FAILED) so that
            # the state derivation bookkeeping does not overwrite it.
            terminal_phase = current_state.phase if current_state.terminal else None
            terminal_status = current_state.status if current_state.terminal else None

            if not current_state.terminal:
                current_state = replace(current_state, phase=HarnessPhase.UPDATE)
                emit(
                    EventType.HARNESS_PHASE_CHANGED,
                    {"phase": HarnessPhase.UPDATE.value, "iteration": current_state.iteration},
                )

            current_project_state = self._state_derivator(
                run_id=request.run_id,
                attempt_number=current_state.attempt_number,
                task_id=request.task_specification.task_id if request.task_specification else "",
                execution_results=execution_results,
                verification_results=verification_results,
                acceptance_result=acceptance_result,
            )
            emit(
                EventType.PROJECT_STATE_UPDATED,
                {
                    "run_id": request.run_id,
                    "attempt_number": current_project_state.attempt_number,
                    "status": current_project_state.status.value,
                },
            )

            current_state = replace(
                current_state,
                project_state_status=current_project_state.status.value,
                latest_execution_result_id=latest_exec_id or current_state.latest_execution_result_id,
                latest_verification_id=latest_verif_id or current_state.latest_verification_id,
            )

            # Restore terminal phase / status so result.final_state has the
            # correct phase (COMPLETE, WAITING, FAILED) rather than UPDATE.
            if terminal_phase is not None and terminal_status is not None:
                current_state = replace(
                    current_state,
                    phase=terminal_phase,
                    status=terminal_status,
                )

            iterations_history.append(current_state)

            if current_state.terminal:
                break


            current_state = replace(
                current_state,
                iteration=current_state.iteration + 1,
                phase=HarnessPhase.OBSERVE,
            )

        return HarnessResult(
            run_id=request.run_id,
            final_state=current_state,
            iterations=tuple(iterations_history),
            observations=tuple(observations),
            decisions=tuple(decisions),
            execution_results=tuple(execution_results),
            verification_results=tuple(verification_results),
            final_acceptance=acceptance_result,
            final_project_state=current_project_state,
            events=tuple(collector.events),
        )
