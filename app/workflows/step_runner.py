"""Managed multi-step execution v0.1: the runner.

WHAT THIS DRIVES
================

For every executing step this runner calls the **existing** declared-execution
entry point -- ``RunExecutor.execute(..., generate_decision=False)`` -- with the
same arguments the existing API service uses for one declared verification:

    frozen RunScope -> RunExecutor -> ExecutionCoordinator
                    -> AuthorizedExecution -> LocalExecutionAdapter

It mints nothing. It builds no second authorizer, no second approval decision,
no second workspace perimeter and no second executor. The coordinator it uses is
*handed to it by the composition* (there is no default), so this module cannot
even choose an execution backend. ``generate_decision=False`` is correct here for
the same reason it is correct in ``run_declared_verification``: the decision is
the operator's declaration, which was resolved before the run started. A model is
never asked, so nothing a model could say can influence a step.

WHAT IT ADDS, AND ONLY THIS
===========================

1. **Graph validation before anything runs.** ``StepGraph.assert_valid`` refuses
   missing dependencies, cycles, duplicate ids, malformed references, undeclared
   actions and parameter values that try to widen a declared limit *before* any
   scope is frozen and before any claim is made.
2. **Deterministic ready-step selection.** Ready steps come from
   ``StepGraph.ready_steps`` in topological order, so no answer depends on dict
   or set iteration order. Execution is **strictly sequential**: one step is
   dispatched at a time and the next begins only after the previous one has a
   recorded outcome. Parallel dispatch is not attempted, because nothing in this
   architecture proves that two concurrent declared executions inside one
   workspace are safe, and a plan must not be able to create that concurrency.
3. **Durable per-step progress** through the existing ``RunStore`` under the
   graph run id, and a real per-step run history for each step that dispatches.
4. **A durable claim per step** through the existing ``RunIdempotencyGuard`` and
   its ledger, plus a ``HOST_PROCESS`` side-effect record written *before* the
   dispatch and settled after it. That ordering is what lets a restart tell
   "never started" apart from "started, outcome unproven".
5. **Blocking.** A step is marked ``BLOCKED`` when any dependency is not
   ``SUCCEEDED``, and it is never dispatched.

FAIL-CLOSED ORDERING (the load-bearing invariant)
=================================================

For each step, in this order:

1. the step's claim is read from the durable ledger. Only ``START`` -- or
   ``RESUME`` for a step explicitly declared ``repeatable`` -- permits dispatch;
2. the ``RUNNING`` transition is **persisted**, then the side-effect record is
   written;
3. only then is the process dispatched.

So a durable ``RUNNING`` with no outcome means "the action may have run". A
restart reads that as ``UNKNOWN``, never as "it did not run", and never retries it
unless the step is declared ``repeatable``.

WHAT IS NOT GUARANTEED
======================

* **Not exactly-once.** At most once per claim, *as observed through a local
  filesystem ledger*, and only while that ledger is readable. There is no
  distributed transaction and no cross-process lock.
* **Not atomic side effects.** A host process is a whole program; the ledger
  records that it was claimed and how it ended, never that its effects were
  atomic.
* **Not a sandbox.** The workspace boundary, the environment allowlist and the
  secret filter are the existing ones; the adapter's isolation setting is the
  composition's choice and is not changed here.
* **Not automatic retry.** A ``FAILED`` step is not retried, a ``WAITING_APPROVAL``
  step is not re-attempted when approval later arrives, and an ``UNKNOWN`` step is
  never repeated automatically. A further attempt is a **new graph run id**, which
  means a new claim, a new scope and a fresh approval check.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from app.agent_runtime.criterion_identity import TaskIdentity
from app.agent_runtime.idempotency import (
    IdempotencyError,
    IdempotencyVerdict,
    OperationClass,
    RunLifecycleState,
    SideEffectKind,
    SideEffectRecord,
    SideEffectState,
)
from app.agent_runtime.idempotency_integration import (
    RunIdempotencyGuard,
    operation_identity_from_binding,
)
from app.execution.authorizer import ExecutionCoordinator
from app.execution.declaration import ExecutionDeclaration
from app.execution.intent import IntentBuilder
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest
from app.orchestrator.models import Task
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope, require_active_scope
from app.runtime.run_store import RunStateSnapshot, RunStore
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy, ApprovalResolver
from app.tools.workspace import Workspace
from app.workflows.step_graph import (
    StepDefinition,
    StepGraph,
    StepGraphError,
    StepGraphSnapshot,
    StepGraphStateError,
    StepGraphStatus,
    StepRecord,
    StepStatus,
    VerificationKind,
    VerificationStatus,
    build_step_request,
    declared_plan_of,
    derive_graph_status,
    graph_from_declared_plan,
    planned_payload,
    read_step_graph_record,
)

__all__ = [
    "STEP_GRAPH_CRITERION",
    "StepGraphResult",
    "StepGraphRunner",
]

#: ``RunScope`` requires a non-empty acceptance declaration. A step-graph run is
#: judged by its step records, not by this criterion, which exists only so the
#: perimeter is never silently empty. Mirrors ``API_RUN_CRITERION``.
_ABSENT_STEP_GRAPH_RECORD_REASONS = frozenset(
    {"no_persisted_state", "no_project_state", "no_step_graph_record"}
)
"""Reasons the reader returns when there genuinely is no record to protect.

Anything else means a record exists in a form this build refuses to trust, which is
a different state from "nothing was attempted" -- see ``_attempt``.
"""


STEP_GRAPH_CRITERION = AcceptanceCriterion(
    criterion_id="step-graph-attempt-recorded",
    description="Every declared step of a managed step-graph attempt reaches a recorded verdict",
    requirement_id="step-graph",
)

_SCHEMA_KEY = "step_graph"
_MAX_GRAPH_RUN_ID = 100
_MAX_DERIVED_RUN_ID = 128

#: A run id acceptable to the existing ``RunStore``. A graph run id is used both
#: as a run id and as an idempotency-key component, so it shares that alphabet.
_RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1," + str(_MAX_GRAPH_RUN_ID) + r"}$")

# Lifecycle each settled step status is recorded under in the existing durable
# ledger. A refusal is recorded as DENIED (security-terminal) so it can never be
# resumed into a second attempt.
_SETTLED_LIFECYCLE: Mapping[StepStatus, tuple[RunLifecycleState, tuple[str, ...]]] = {
    StepStatus.SUCCEEDED: (RunLifecycleState.COMPLETED, ("completed", "success")),
    StepStatus.FAILED: (RunLifecycleState.FAILED, ("failed",)),
    StepStatus.WAITING_APPROVAL: (
        RunLifecycleState.WAITING_FOR_APPROVAL,
        ("waiting_for_approval",),
    ),
    StepStatus.UNKNOWN: (RunLifecycleState.INTERRUPTED, ("unknown",)),
}

#: Outcome labels that make a settled step an authority refusal. The ledger maps
#: these to a security-terminal state, which is what forbids a later retry.
_REFUSAL_OUTCOMES: Mapping[str, tuple[RunLifecycleState, tuple[str, ...]]] = {
    "APPROVAL_REJECTED": (RunLifecycleState.DENIED, ("approval_rejected",)),
    "PERMISSION_DENIED": (RunLifecycleState.DENIED, ("permission_denied",)),
    "POLICY_DENIED": (RunLifecycleState.DENIED, ("policy_denied",)),
}

#: Process outcomes that prove the child ran and ended, whatever it decided.
_PROCESS_ENDED = frozenset(
    {"EXECUTION_SUCCESS", "EXECUTION_FAILURE", "EXECUTION_TIMEOUT"}
)

_IN_FLIGHT_LOCK = threading.Lock()
_IN_FLIGHT: set[str] = set()


@dataclass(frozen=True)
class StepGraphResult:
    """The honest verdict of one managed attempt, with every step's evidence."""

    graph_id: str
    graph_run_id: str
    status: StepGraphStatus
    steps: tuple[StepRecord, ...] = ()
    stopped_reason: str = ""
    executed_steps: int = 0
    # WHAT ``executed`` MEANS, STATED. It is the runner's own "this call drove the
    # graph rather than reading a recorded attempt back": TRUE for a run that
    # processed steps and reached a verdict, FALSE for a replay of an existing record
    # and for a refused in-flight claim. It is NOT "a command ran". Block #10
    # measured the confusion this caused: with an approval-required policy and no
    # resolver, ``POST /api/product/graph`` answered ``executed: true`` next to
    # ``status: "REFUSED"`` and ``WAITING_APPROVAL``, although no process was ever
    # created. ``command_started`` below is the field that answers the question a
    # caller actually asks, and the HTTP surface publishes both.
    executed: bool = True
    cancelled: bool = False
    idempotency_degraded: bool = False
    started_at: str = ""
    finished_at: str = ""

    @property
    def commands_started(self) -> int:
        """How many steps in this attempt actually started a command.

        Derived from the step records -- never a counter maintained alongside them,
        which could drift from the records it claims to summarize. A step whose
        outcome is a refusal from the authorization chain (``APPROVAL_WAITING``,
        ``POLICY_DENIED``, ...) is NOT counted: no process was created for it. A step
        that really ran and failed IS counted, and so is one dispatched whose outcome
        the runner could not read, because a process may exist.
        """

        return sum(1 for record in self.steps if record.command_started)

    @property
    def command_started(self) -> bool:
        """Whether ANY command in this attempt actually started. See above."""

        return self.commands_started > 0

    def step(self, step_id: str) -> StepRecord:
        for record in self.steps:
            if record.step_id == step_id:
                return record
        raise KeyError(step_id)

    def status_of(self, step_id: str) -> StepStatus:
        return self.step(step_id).status

    def statuses(self) -> dict[str, StepStatus]:
        return {record.step_id: record.status for record in self.steps}

    def by_status(self, status: StepStatus) -> tuple[str, ...]:
        """Step ids with this status, in declaration order."""
        return tuple(r.step_id for r in self.steps if r.status is status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "graph_run_id": self.graph_run_id,
            "status": self.status.value,
            "stopped_reason": self.stopped_reason,
            "executed_steps": int(self.executed_steps),
            # THE TWO ANSWERS, BOTH PUBLISHED. ``executed`` keeps its historical
            # meaning (the runner drove this attempt rather than replaying it), and
            # ``command_started``/``commands_started`` answer the different question
            # "did a process exist". Neither is derived from the other.
            "executed": bool(self.executed),
            "command_started": self.command_started,
            "commands_started": self.commands_started,
            "cancelled": bool(self.cancelled),
            "idempotency_degraded": bool(self.idempotency_degraded),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "steps": [record.to_dict() for record in self.steps],
        }


class StepGraphRunner:
    """Drive a validated step graph through the existing execution plane.

    The runner holds no authority of its own: ``execution_coordinator``,
    ``approval_policy`` and ``approval_resolver`` are supplied by the composition
    and every executing step is re-authorized by them.
    """

    def __init__(
        self,
        *,
        runtime: RuntimeContext,
        workspace: Workspace,
        execution_profile: ProjectExecutionProfile,
        declarations: Mapping[str, ExecutionDeclaration],
        run_store: RunStore,
        idempotency_guard: RunIdempotencyGuard,
        approval_policy: ApprovalPolicy,
        execution_coordinator: ExecutionCoordinator,
        approval_resolver: ApprovalResolver | None = None,
        allowed_tool_ids: frozenset[str] = frozenset(),
        acceptance_criteria: Sequence[AcceptanceCriterion] = (),
        artifacts_are_harvested: bool = False,
        observer: Callable[[object, dict], None] | None = None,
    ) -> None:
        if runtime is None or getattr(runtime, "run_executor", None) is None:
            raise StepGraphError(
                "a step-graph runner requires a runtime with a composed run executor"
            )
        if not isinstance(workspace, Workspace):
            raise StepGraphError("workspace must be a Workspace")
        if not isinstance(execution_profile, ProjectExecutionProfile):
            raise StepGraphError("execution_profile must be a ProjectExecutionProfile")
        if not isinstance(run_store, RunStore):
            raise StepGraphError("run_store must be a RunStore")
        if not isinstance(idempotency_guard, RunIdempotencyGuard):
            raise StepGraphError("idempotency_guard must be a RunIdempotencyGuard")
        if not isinstance(approval_policy, ApprovalPolicy):
            # Mandatory, exactly as in the executor's host-execution branch: an
            # absent approval policy must never silently mean "no approval needed".
            raise StepGraphError("approval_policy must be an ApprovalPolicy")
        if not isinstance(execution_coordinator, ExecutionCoordinator):
            raise StepGraphError(
                "execution_coordinator must be an ExecutionCoordinator; the runner "
                "never constructs an authority component of its own"
            )
        self._runtime = runtime
        self._workspace = workspace
        self._execution_profile = execution_profile
        self._declarations = dict(declarations)
        self._run_store = run_store
        self._idempotency_guard = idempotency_guard
        self._approval_policy = approval_policy
        self._execution_coordinator = execution_coordinator
        self._approval_resolver = approval_resolver
        self._allowed_tool_ids = frozenset(allowed_tool_ids)
        self._acceptance_criteria = (
            tuple(acceptance_criteria) if acceptance_criteria else (STEP_GRAPH_CRITERION,)
        )
        self._artifacts_are_harvested = bool(artifacts_are_harvested)
        self._observer = observer
        self._statuses: dict[str, StepStatus] = {}
        self._statuses_lock = threading.Lock()

    # ------------------------------------------------------------------
    # public helpers
    # ------------------------------------------------------------------
    @staticmethod
    def step_run_id(graph_run_id: str, step_id: str) -> str:
        """The durable run id of one step, derived only from safe components."""
        return f"step.{graph_run_id}.{step_id}"

    @staticmethod
    def step_request_id(graph_run_id: str, step_id: str) -> str:
        """The execution-request id of one step.

        Deterministic on purpose. The request id is *not* what binds an approval
        -- the execution intent fingerprint is (:mod:`app.execution.authorizer`)
        -- so a stable id cannot be used to spend an approval on different
        parameters: the fingerprint comparison refuses it. A stable id is what
        makes "the same step, asked twice" the same operation, which is what the
        durable claim is keyed on.
        """
        return f"{StepGraphRunner.step_run_id(graph_run_id, step_id)}#request"

    def status_of(self, step_id: str) -> StepStatus | None:
        """In-memory status of a step of the attempt currently in flight."""
        with self._statuses_lock:
            return self._statuses.get(step_id)

    def recorded(self, graph_run_id: str) -> StepGraphSnapshot | None:
        """Read the durable step-graph record, or ``None`` when there is none."""
        snapshot = self._run_store.read_state(graph_run_id)
        if snapshot is None:
            return None
        record, _reason = read_step_graph_record(snapshot.project_state)
        return record

    def recorded_or_reason(
        self, graph_run_id: str
    ) -> tuple[StepGraphSnapshot | None, str]:
        """Like :meth:`recorded`, but also reports *why* there is no record.

        A recovery scan must be able to tell "no attempt was recorded" from "an
        attempt was recorded in a form this build refuses to trust"; the plain
        accessor deliberately collapses both into ``None``.
        """
        snapshot = self._run_store.read_state(graph_run_id)
        if snapshot is None:
            return None, "no_persisted_state"
        return read_step_graph_record(snapshot.project_state)

    def ledger_guard(self) -> RunIdempotencyGuard:
        """A second guard over the same durable ledger this runner writes.

        Read-only observation: it lets an operator (or a test) ask what the
        ledger now permits for a step without executing anything, and without
        reaching into this runner's private state.
        """
        return RunIdempotencyGuard.create(
            ledger_root=self._idempotency_guard.engine.ledger.root
        )

    def intent_fingerprint(self, graph: StepGraph, step_id: str) -> str:
        """The fingerprint the coordinator will bind an approval to for this step.

        Exposed so an operator-side test (or an approval UI) can grant approval
        for exactly one parameter set, and so a test can prove that changing a
        parameter invalidates an already-granted approval. Pure: it builds a
        value object and starts nothing.
        """
        definition = graph.step(step_id)
        declaration = self._declaration(definition)
        request = self._build_request(
            definition, declaration, graph_run_id="fingerprint", step_id=step_id
        )
        return IntentBuilder.from_request(request, self._execution_profile).build().fingerprint

    # ------------------------------------------------------------------
    # entry points
    # ------------------------------------------------------------------
    def run(
        self,
        graph: StepGraph,
        graph_run_id: str,
        *,
        cancel_event: object | None = None,
        step_budget: int | None = None,
    ) -> StepGraphResult:
        """Start one attempt of ``graph`` under ``graph_run_id``.

        A second call with a graph run id that already has a durable record
        **executes nothing** and returns the recorded attempt. Continuing an
        unfinished attempt is an explicit :meth:`resume`, never an implicit
        side effect of asking twice.
        """
        return self._attempt(
            graph,
            graph_run_id,
            resume=False,
            cancel_event=cancel_event,
            step_budget=step_budget,
        )

    def resume(
        self,
        graph: StepGraph | None = None,
        graph_run_id: str = "",
        *,
        cancel_event: object | None = None,
        step_budget: int | None = None,
    ) -> StepGraphResult:
        """Continue an existing attempt after a restart.

        Before anything runs, the saved record is re-validated against the plan
        that was **declared** when the attempt started. That plan is read back
        from the record itself (:func:`app.workflows.step_graph.graph_from_declared_plan`),
        so a restart does not need a caller who still remembers it: the record is
        the source of the plan, and re-validation is against what was declared
        rather than against what someone now claims.

        ``graph`` is therefore **optional**, and supplying it is an independent
        cross-check rather than the source of truth:

        * ``resume(graph_run_id="r")`` -- the declaration is recovered from the
          record and re-validated (schema recognised, every saved step
          self-consistent, step set and action fingerprints match their records);
        * ``resume(graph, "r")`` -- the same, plus the supplied graph must be equal
          to the recovered declaration. A graph whose steps or parameters differ
          is refused with ``StepGraphStateError``, because resuming would re-plan
          work under an old verdict.

        A record written before Block #7 carries no declaration; that case is
        reported as ``StepGraphStateError`` naming the missing plan, so an old
        record is never silently re-validated against an unverified assumption.

        Steps that already have a recorded verdict are never executed again. A
        step saved as ``RUNNING`` is read back as ``UNKNOWN`` -- the process that
        wrote it may have died after dispatching -- and is never repeated unless
        the step was declared ``repeatable``.
        """
        if graph is None and not graph_run_id:
            # Historical positional call shape: resume(graph, graph_run_id).
            raise StepGraphError(
                "resume requires a graph_run_id; the declared plan is read from the "
                "record, so the graph argument is optional but the run id is not"
            )
        if graph is None and isinstance(graph_run_id, StepGraph):
            # Tolerate the reversed positional order rather than silently using a
            # StepGraph instance as a run id.
            graph, graph_run_id = graph_run_id, ""
        if graph is not None and not isinstance(graph, StepGraph):
            raise StepGraphError("graph must be a StepGraph when supplied")
        return self._attempt(
            graph,
            graph_run_id,
            resume=True,
            cancel_event=cancel_event,
            step_budget=step_budget,
        )

    # ------------------------------------------------------------------
    # attempt orchestration
    # ------------------------------------------------------------------
    def _attempt(
        self,
        graph: StepGraph | None,
        graph_run_id: str,
        *,
        resume: bool,
        cancel_event: object | None,
        step_budget: int | None,
    ) -> StepGraphResult:
        started_at = self._now()
        graph_run_id = self._validate_graph_run_id(graph_run_id)
        budget = self._validate_budget(step_budget)
        if graph is None:
            # Reading the record is the only way to learn the declared plan. It is
            # deliberately done **before** the in-flight claim, because a missing or
            # untrustworthy record is a refusal that needs no claim at all, and
            # because the claim's refusal path must not read the store.
            existing_for_plan, plan_reason = self.recorded_or_reason(graph_run_id)
            if existing_for_plan is None:
                if plan_reason not in _ABSENT_STEP_GRAPH_RECORD_REASONS:
                    # A record is there; it just cannot be trusted. Claiming there is
                    # none would be false and would invite a fresh attempt over
                    # evidence that still exists on disk.
                    raise StepGraphStateError(
                        f"the persisted step-graph record for {graph_run_id!r} cannot "
                        f"be trusted ({plan_reason}); a resume never starts a new "
                        "attempt over an unreadable record"
                    )
                raise StepGraphStateError(
                    f"no persisted step-graph record for {graph_run_id!r}; a resume "
                    "requires one and never starts a new attempt"
                )
            graph = graph_from_declared_plan(
                existing_for_plan.graph_id, existing_for_plan.declared_plan
            )
        # Refused before any scope, claim or process: the whole graph, against the
        # operator's own declaration registry.
        graph.assert_valid(declarations=self._declarations)
        self._validate_derived_ids(graph, graph_run_id)

        # The process-local in-flight claim comes first: "is this graph run
        # already being driven here?" is a more primitive question than "is there
        # a durable record?", and asking it first is what makes a race between two
        # callers in this process a refusal rather than two attempts.
        if not self._claim_graph_run(graph_run_id):
            # Deliberately performs **no** durable read. On Windows a concurrent
            # read of the state file can make the writer's atomic ``os.replace``
            # fail, so the refused caller must not touch the store while the other
            # caller holds the claim. The refusal therefore reports no step
            # records: the other caller is still producing them.
            return StepGraphResult(
                graph_id=graph.graph_id,
                graph_run_id=graph_run_id,
                status=StepGraphStatus.REFUSED,
                steps=(),
                stopped_reason="graph_run_in_progress",
                executed_steps=0,
                executed=False,
                idempotency_degraded=self._idempotency_guard.degraded,
                started_at=started_at,
                finished_at=self._now(),
            )
        try:
            # Read the record *and* the reason it is absent. ``recorded()`` collapses
            # "no attempt was recorded" and "an attempt was recorded in a form this
            # build refuses to trust" into the same ``None``; that collapse is what let
            # a damaged record be treated as absent and then overwritten.
            existing, read_reason = self.recorded_or_reason(graph_run_id)
            if existing is None and read_reason not in _ABSENT_STEP_GRAPH_RECORD_REASONS:
                # Refuse before any snapshot is built, any command runs or any byte is
                # written: the existing record is preserved exactly as it is and the
                # reason travels with the result instead of being discarded.
                return StepGraphResult(
                    graph_id=graph.graph_id,
                    graph_run_id=graph_run_id,
                    status=StepGraphStatus.REFUSED,
                    steps=(),
                    stopped_reason=f"untrustworthy_step_graph_record:{read_reason}",
                    executed_steps=0,
                    executed=False,
                    idempotency_degraded=self._idempotency_guard.degraded,
                    started_at=started_at,
                    finished_at=self._now(),
                )
            if existing is not None and not resume:
                # The same screen ``resume`` applies. A saved status is a claim, and
                # a claim its own records do not support is not a verdict: echoing it
                # here is exactly what let one record be COMPLETED through ``run``
                # and INDETERMINATE through ``resume``. Screening in one place is
                # what keeps the two entry points from disagreeing again.
                return self._result_from_snapshot(
                    self._screen_snapshot(existing),
                    stopped_reason="already_recorded",
                    executed=False,
                    started_at=started_at,
                )
            if existing is None and resume:
                raise StepGraphStateError(
                    f"no persisted step-graph record for {graph_run_id!r}; a resume "
                    "requires one and never starts a new attempt"
                )
            snapshot = (
                self._resume_snapshot(graph, existing)
                if existing is not None
                else self._fresh_snapshot(graph, graph_run_id)
            )
            with self._statuses_lock:
                self._statuses = {r.step_id: r.status for r in snapshot.steps}
            snapshot.attempts += 1
            snapshot.cancelled = self._cancel_requested(cancel_event)
            self._persist(snapshot)
            return self._drive(
                graph,
                snapshot,
                cancel_event=cancel_event,
                budget=budget,
                started_at=started_at,
            )
        finally:
            self._release_graph_run(graph_run_id)

    def _drive(
        self,
        graph: StepGraph,
        snapshot: StepGraphSnapshot,
        *,
        cancel_event: object | None,
        budget: int | None,
        started_at: str,
    ) -> StepGraphResult:
        records: dict[str, StepRecord] = {r.step_id: r for r in snapshot.steps}
        dependencies = graph.dependency_map()
        executed = 0
        stopped_reason = "all_steps_resolved"

        while True:
            if self._cancel_requested(cancel_event):
                # Cancellation stops NEW steps. Every step that has not started is
                # cancelled; nothing that is running is touched, and nothing that
                # already has a verdict is rewritten.
                for step_id in graph.topological_order():
                    record = records[step_id]
                    if record.status is StepStatus.PENDING:
                        record.status = StepStatus.CANCELLED
                        record.add_evidence("cancelled_before_start")
                        record.updated_at = self._now()
                snapshot.cancelled = True
                stopped_reason = "cancelled"
                break

            statuses = {sid: record.status for sid, record in records.items()}
            ready = graph.ready_steps(statuses)
            if not ready:
                blocked = graph.blocked_steps(statuses)
                for step_id in blocked:
                    record = records[step_id]
                    record.status = StepStatus.BLOCKED
                    record.add_evidence(
                        "dependency_not_succeeded:"
                        + ",".join(
                            dependency
                            for dependency in dependencies[step_id]
                            if records[dependency].status is not StepStatus.SUCCEEDED
                        )
                    )
                    record.updated_at = self._now()
                stopped_reason = "blocked" if blocked else "all_steps_resolved"
                break

            if budget is not None and executed >= budget:
                stopped_reason = "step_budget_exhausted"
                break

            step_id = ready[0]
            cancel_before = self._cancel_requested(cancel_event)
            self._execute_step(graph, snapshot, records, records[step_id], cancel_event)
            executed += 1
            if not cancel_before and self._cancel_requested(cancel_event):
                # Cancellation arrived while this step was in flight. It was NOT
                # stopped and is NOT reported as cancelled: the record says what
                # actually happened, and says that a cancel arrived meanwhile.
                records[step_id].add_evidence("cancel_requested_while_running")
            self._persist(snapshot)

        ordered = tuple(records[sid] for sid in graph.topological_order())
        snapshot.status = derive_graph_status(
            ordered,
            cancelled=bool(snapshot.cancelled),
            budget_exhausted=stopped_reason == "step_budget_exhausted",
        )
        snapshot.stopped_reason = stopped_reason
        snapshot.idempotency_degraded = bool(
            snapshot.idempotency_degraded or self._idempotency_guard.degraded
        )
        snapshot.updated_at = self._now()
        self._persist(snapshot)
        with self._statuses_lock:
            self._statuses = {r.step_id: r.status for r in ordered}
        return self._result_from_snapshot(
            snapshot,
            stopped_reason=stopped_reason,
            executed=True,
            executed_steps=executed,
            started_at=started_at,
        )

    # ------------------------------------------------------------------
    # one step
    # ------------------------------------------------------------------
    def _execute_step(
        self,
        graph: StepGraph,
        snapshot: StepGraphSnapshot,
        records: Mapping[str, StepRecord],
        record: StepRecord,
        cancel_event: object | None,
    ) -> StepStatus:
        definition = graph.step(record.step_id)
        declaration = self._declaration(definition)
        graph_run_id = snapshot.graph_run_id
        step_run_id = self.step_run_id(graph_run_id, record.step_id)
        request_id = self.step_request_id(graph_run_id, record.step_id)
        identity = self.operation_identity(graph, definition, graph_run_id)

        record.attempts += 1
        record.action_fingerprint = definition.fingerprint()
        record.declaration_id = definition.action.declaration_id
        record.parameters = definition.action.parameters
        record.step_run_id = step_run_id
        record.request_id = request_id
        record.performed_executable = declaration.executable
        record.performed_working_directory = declaration.working_directory
        record.verification_kind = (
            definition.verification.kind.value if definition.verification else ""
        )
        record.verification_expected = (
            definition.verification.expectation_text() if definition.verification else ""
        )
        record.updated_at = self._now()

        # 1. the durable claim decides whether this delivery may dispatch at all.
        decision = self._idempotency_guard.begin(identity, requested_run_id=step_run_id)
        if decision.verdict is not IdempotencyVerdict.START and not (
            decision.verdict is IdempotencyVerdict.RESUME and definition.repeatable
        ):
            return self._refuse_before_dispatch(record, snapshot, decision)

        # 2. build the request and freeze the perimeter for this one step run.
        try:
            request = self._build_request(
                definition, declaration, graph_run_id=graph_run_id, step_id=record.step_id
            )
            scope = self._build_scope(step_run_id, declaration)
        except Exception as exc:  # noqa: BLE001 - a refusal, not a crash
            return self._abort_claimed(
                identity,
                record,
                snapshot,
                reason=f"refused_before_dispatch:{type(exc).__name__}",
                lifecycle=(
                    RunLifecycleState.DENIED
                    if "RunScopeError" in type(exc).__name__
                    else RunLifecycleState.FAILED
                ),
            )
        record.performed_working_directory = request.working_directory

        # 3. RUNNING becomes durable BEFORE the process exists. A record that says
        #    RUNNING with no outcome is the only thing that lets a restart tell
        #    "may have run" from "never ran".
        record.status = StepStatus.RUNNING
        record.updated_at = self._now()
        with self._statuses_lock:
            self._statuses[record.step_id] = StepStatus.RUNNING
        self._persist(snapshot)

        # 4. the side effect is recorded as about to happen, for the same reason.
        effect = SideEffectRecord(
            fingerprint=identity.fingerprint,
            kind=SideEffectKind.HOST_PROCESS,
            state=SideEffectState.NOT_STARTED,
            action_id=step_run_id,
            attempt_number=0,
            replay_safe=False,
        )
        try:
            self._idempotency_guard.engine.ledger.begin_side_effect(identity, effect)
        except (IdempotencyError, OSError) as exc:
            RunScope.release(step_run_id)
            self._idempotency_guard.degraded = True
            record.performed_executable = ""
            record.performed_working_directory = ""
            record.step_run_id = ""
            record.request_id = ""
            return self._abort_claimed(
                identity,
                record,
                snapshot,
                reason=f"refused_before_dispatch:side_effect_not_recorded:{type(exc).__name__}",
                lifecycle=RunLifecycleState.FAILED,
            )

        # 5. dispatch through the one existing chain.
        escaped: BaseException | None = None
        run: Any = None
        try:
            run = self._runtime.run_executor.execute(
                task=Task(
                    id=f"task.{graph_run_id}.{record.step_id}",
                    description="Managed step-graph execution.",
                    context={"run_id": step_run_id},
                ),
                workspace=self._workspace,
                run_id=step_run_id,
                run_scope=scope,
                allowed_tool_ids=self._allowed_tool_ids,
                run_store=self._step_store(step_run_id, record.step_id),
                allowed_execution_commands=scope.allowed_execution_commands,
                execution_request_factory=self._request_factory(request),
                execution_coordinator=self._execution_coordinator,
                approval_policy=self._approval_policy,
                approval_resolver=self._approval_resolver,
                generate_decision=False,
                observer=self._observer,
            )
        except BaseException as exc:  # noqa: BLE001 - a real interruption
            escaped = exc
        finally:
            RunScope.release(step_run_id)

        if escaped is not None:
            # The claim and the dispatch are durable and nothing after them is
            # provable. This is not a failure and not a cancellation: it is
            # UNKNOWN, and the side-effect record is deliberately left STARTED so
            # the ledger refuses a later automatic retry.
            record.status = StepStatus.UNKNOWN
            record.execution_outcome = "UNKNOWN"
            record.execution_error = ""
            record.add_evidence(
                f"dispatched_outcome_unknown:{type(escaped).__name__}"
            )
            record.updated_at = self._now()
            with self._statuses_lock:
                self._statuses[record.step_id] = StepStatus.UNKNOWN
            snapshot.status = derive_graph_status(
                tuple(records[sid] for sid in graph.topological_order())
            )
            snapshot.stopped_reason = "unknown_outcome"
            snapshot.updated_at = self._now()
            self._persist(snapshot)
            raise escaped

        return self._settle(
            snapshot,
            record,
            definition,
            identity,
            effect,
            run,
        )

    def _settle(
        self,
        snapshot: StepGraphSnapshot,
        record: StepRecord,
        definition: StepDefinition,
        identity: object,
        effect: SideEffectRecord,
        run: Any,
    ) -> StepStatus:
        results = list(getattr(run, "execution_results", ()) or ())
        result = results[-1] if results else None
        outcome = ""
        if result is not None:
            outcome = (
                result.outcome_status.value
                if getattr(result, "outcome_status", None) is not None
                else str(getattr(result.status, "value", result.status))
            )
        record.execution_outcome = outcome or "UNKNOWN"
        record.execution_exit_code = (
            getattr(result, "exit_code", None) if result is not None else None
        )

        status = self._execution_status(record, result, outcome, definition)
        (
            verification_status,
            verification_observed,
            verification_error,
            verification_evidence,
        ) = self._evaluate_verification(definition, result, outcome, record)
        record.verification_status = verification_status
        record.verification_observed = verification_observed
        record.verification_error = verification_error
        record.add_evidence(*verification_evidence)
        if status is StepStatus.SUCCEEDED and definition.verification is not None:
            if verification_status is VerificationStatus.FAILED:
                # The command succeeded; the declared check did not hold.
                status = StepStatus.FAILED
                record.add_evidence("verification_failed")
            elif verification_status is not VerificationStatus.PASSED:
                # A declared check that could not be evaluated cannot certify a
                # success either. FAILED, with the ERROR still recorded separately.
                status = StepStatus.FAILED
                record.add_evidence("verification_not_passed")
        if status is StepStatus.FAILED and record.execution_error:
            record.add_evidence("execution_error")
        record.status = status
        record.updated_at = self._now()

        # Settle the side effect first: the ledger refuses a lifecycle write that
        # would launder an unsettled effect into a recoverable state.
        process_ended = outcome in _PROCESS_ENDED
        try:
            self._idempotency_guard.engine.ledger.complete_side_effect(
                identity,
                effect,
                state=(
                    SideEffectState.COMMITTED if process_ended else SideEffectState.FAILED
                ),
                detail=f"execution_outcome={record.execution_outcome}",
            )
        except (IdempotencyError, OSError):
            self._idempotency_guard.degraded = True
            record.add_evidence("idempotency_side_effect_outcome_not_recorded")

        lifecycle, labels = _REFUSAL_OUTCOMES.get(
            record.execution_outcome
        ) or _SETTLED_LIFECYCLE[status]
        try:
            self._idempotency_guard.finish(
                identity,
                lifecycle=lifecycle,
                attempt_number=0,
                outcome_labels=labels,
            )
        except (IdempotencyError, OSError):
            # Deliberately surfaced, never swallowed into a success: if the
            # terminal position could not be recorded, a later delivery could be
            # admitted to run this step again, and the result must say so.
            self._idempotency_guard.degraded = True
            record.add_evidence("idempotency_lifecycle_not_recorded")

        with self._statuses_lock:
            self._statuses[record.step_id] = status
        snapshot.idempotency_degraded = bool(
            snapshot.idempotency_degraded or self._idempotency_guard.degraded
        )
        snapshot.updated_at = self._now()
        self._persist(snapshot)
        return status

    # ------------------------------------------------------------------
    # interpretation
    # ------------------------------------------------------------------
    @staticmethod
    def _execution_status(
        record: StepRecord,
        result: Any,
        outcome: str,
        definition: StepDefinition,
    ) -> StepStatus:
        """Map the real execution outcome onto a step status, fail-closed.

        This answers only "did the process succeed". Whether a declared
        verification holds is answered separately in
        :meth:`_evaluate_verification`, and a declared check that does not pass
        downgrades an otherwise-successful step to ``FAILED`` in :meth:`_settle`
        -- while ``execution_outcome`` stays ``EXECUTION_SUCCESS``, so "the
        command worked, the check did not" stays readable and is never collapsed
        into a plain execution failure.
        """
        if result is None:
            record.add_evidence("dispatched_outcome_unknown:no_execution_result")
            record.execution_outcome = "UNKNOWN"
            return StepStatus.UNKNOWN
        if outcome == "APPROVAL_WAITING":
            record.add_evidence("approval_waiting")
            return StepStatus.WAITING_APPROVAL
        if outcome in _REFUSAL_OUTCOMES:
            record.add_evidence(f"refused_by_authorization_chain:{outcome}")
            record.execution_error = outcome
            return StepStatus.FAILED
        if outcome == "EXECUTION_TIMEOUT":
            record.add_evidence("execution_timeout")
            record.execution_error = "EXECUTION_TIMEOUT"
            return StepStatus.FAILED
        if outcome == "EXECUTION_ERROR":
            record.add_evidence("execution_error")
            # Bounded: the class of the failure, never a message that could carry
            # a path or a value from the child's environment.
            record.execution_error = "EXECUTION_ERROR"
            return StepStatus.FAILED
        if outcome != "EXECUTION_SUCCESS":
            record.add_evidence(f"execution_did_not_succeed:{outcome or 'UNKNOWN'}")
            record.execution_error = outcome or "UNKNOWN"
            return StepStatus.FAILED
        if record.execution_exit_code is None:
            record.add_evidence("exit_code_unavailable")
            record.execution_error = "EXECUTION_SUCCESS_WITHOUT_EXIT_CODE"
            return StepStatus.FAILED
        if record.execution_exit_code != 0:
            record.add_evidence(f"exit_code_not_zero:{record.execution_exit_code}")
            record.execution_error = f"exit_code={record.execution_exit_code}"
            return StepStatus.FAILED
        return StepStatus.SUCCEEDED

    def _evaluate_verification(
        self,
        definition: StepDefinition,
        result: Any,
        outcome: str,
        record: StepRecord,
    ) -> tuple[VerificationStatus, str, str, tuple[str, ...]]:
        """Evaluate a declared check, distinguishing FAILED from ERROR.

        ``FAILED`` -- the check was evaluated and did not hold.
        ``ERROR`` -- the check could not be evaluated, so nothing is proven.
        """
        verification = definition.verification
        if verification is None:
            return (VerificationStatus.NOT_RUN, "", "", ("no_verification_declared",))
        if result is None:
            return (
                VerificationStatus.ERROR,
                "no_execution_result",
                "the execution plane produced no result to verify",
                ("verification_error:no_execution_result",),
            )
        if outcome != "EXECUTION_SUCCESS":
            # The command itself did not succeed, so the check was never reached.
            return (
                VerificationStatus.NOT_RUN,
                f"execution_outcome={outcome or 'UNKNOWN'}",
                "",
                ("verification_not_run:execution_did_not_succeed",),
            )

        if verification.kind is VerificationKind.EXIT_CODE:
            observed = f"exit_code={record.execution_exit_code}"
            if record.execution_exit_code is None:
                return (
                    VerificationStatus.ERROR,
                    observed,
                    "the execution result carries no exit code",
                    ("verification_error:exit_code_unavailable",),
                )
            if record.execution_exit_code == verification.expected_exit_code:
                return (VerificationStatus.PASSED, observed, "", ())
            return (
                VerificationStatus.FAILED,
                observed,
                "",
                (f"exit_code_mismatch:expected={verification.expected_exit_code}",),
            )

        target = verification.artifact_target
        artifacts = tuple(getattr(result, "artifacts", ()) or ())
        for artifact in artifacts:
            if getattr(artifact, "relative_path", None) != target:
                continue
            # A matching path is not proof that THIS attempt produced the file. The
            # harvest compares the state it measured against the baseline taken just
            # before the child was created, and records the answer. Only a proven
            # change is a PASSED; an unchanged pre-existing file and an unavailable
            # provenance are both unevaluable, never success.
            provenance = dict(getattr(artifact, "metadata", {}) or {})
            changed = provenance.get("changed_by_this_attempt")
            if changed is True:
                return (
                    VerificationStatus.PASSED,
                    f"artifact_produced={target} sha256={str(artifact.sha256)[:16]}",
                    "",
                    (),
                )
            if changed is False:
                return (
                    VerificationStatus.ERROR,
                    f"artifact_preexisting_and_unchanged={target}",
                    "the declared artifact already existed before this attempt and "
                    "was not changed by it, so production is not demonstrated",
                    ("verification_error:artifact_preexisting_and_unchanged",),
                )
            return (
                VerificationStatus.ERROR,
                f"artifact_provenance_unavailable={target}",
                "the harvest did not record whether this attempt produced the "
                "artifact",
                ("verification_error:artifact_provenance_unavailable",),
            )
        metadata = dict(getattr(result, "metadata", {}) or {})
        if metadata.get("artifact_error"):
            return (
                VerificationStatus.ERROR,
                f"artifact_absent={target}",
                "the adapter could not harvest this artifact target",
                ("verification_error:artifact_harvest_failed",),
            )
        if not self._artifacts_are_harvested:
            # Absence is not evidence of absence when the composition's adapter
            # does not harvest: report "unevaluable" rather than a false FAILED.
            return (
                VerificationStatus.ERROR,
                f"artifact_absent={target}",
                "the composed adapter does not harvest artifacts, so this check "
                "cannot be evaluated",
                ("verification_error:adapter_does_not_harvest",),
            )
        return (
            VerificationStatus.FAILED,
            f"artifact_absent={target}",
            "",
            (f"artifact_not_produced:{target}",),
        )

    # ------------------------------------------------------------------
    # refusals
    # ------------------------------------------------------------------
    def _refuse_before_dispatch(
        self,
        record: StepRecord,
        snapshot: StepGraphSnapshot,
        decision: object,
    ) -> StepStatus:
        verdict = decision.verdict
        code = decision.failure_code.value if decision.failure_code else ""
        if verdict is IdempotencyVerdict.REPLAY:
            # The durable ledger says this operation already reached a terminal
            # position, but this step's own evidence did not survive with it. The
            # honest answer is UNKNOWN: repeating it is forbidden and proving its
            # result is impossible here.
            record.status = StepStatus.UNKNOWN
            record.execution_outcome = "UNKNOWN"
            record.add_evidence("recorded_outcome_without_local_evidence")
        elif verdict is IdempotencyVerdict.CONFLICT:
            record.status = StepStatus.FAILED
            record.execution_outcome = "REFUSED_IDEMPOTENCY_CONFLICT"
            record.execution_error = "idempotency_key_conflict"
            record.add_evidence("refused_before_dispatch:idempotency_conflict")
        elif verdict is IdempotencyVerdict.REFUSE:
            record.status = StepStatus.UNKNOWN
            record.execution_outcome = f"REFUSED_{code.upper() or 'REFUSE'}"
            record.add_evidence(
                f"refused_before_dispatch:idempotency_{code or 'refuse'}"
            )
        else:  # RESUME of a step that is not declared repeatable
            record.status = StepStatus.UNKNOWN
            record.execution_outcome = "REFUSED_CLAIM_UNFINISHED"
            record.add_evidence(
                "refused_before_dispatch:idempotency_claim_unfinished"
            )
        record.step_run_id = ""
        record.request_id = ""
        record.performed_executable = ""
        record.performed_working_directory = ""
        record.updated_at = self._now()
        with self._statuses_lock:
            self._statuses[record.step_id] = record.status
        snapshot.updated_at = self._now()
        self._persist(snapshot)
        return record.status

    def _abort_claimed(
        self,
        identity: object,
        record: StepRecord,
        snapshot: StepGraphSnapshot,
        *,
        reason: str,
        lifecycle: RunLifecycleState,
    ) -> StepStatus:
        """Close a claim whose step never dispatched, then mark the step FAILED.

        The claim must be closed: leaving it ``CREATED`` would make every later
        delivery a ``RESUME``, which is exactly the state this runner refuses to
        act on.
        """
        record.status = StepStatus.FAILED
        record.execution_outcome = "REFUSED_BEFORE_DISPATCH"
        record.execution_error = reason
        record.add_evidence(reason)
        # Nothing was performed, so nothing may be reported as performed.
        record.step_run_id = ""
        record.request_id = ""
        record.performed_executable = ""
        record.performed_working_directory = ""
        record.updated_at = self._now()
        try:
            self._idempotency_guard.finish(
                identity, lifecycle=lifecycle, attempt_number=0, outcome_labels=("failed",)
            )
        except (IdempotencyError, OSError):
            self._idempotency_guard.degraded = True
            record.add_evidence("idempotency_lifecycle_not_recorded")
        with self._statuses_lock:
            self._statuses[record.step_id] = StepStatus.FAILED
        snapshot.updated_at = self._now()
        self._persist(snapshot)
        return StepStatus.FAILED

    # ------------------------------------------------------------------
    # construction helpers
    # ------------------------------------------------------------------
    def _declaration(self, definition: StepDefinition) -> ExecutionDeclaration:
        declaration = self._declarations.get(definition.action.declaration_id)
        if declaration is None:
            raise StepGraphError(
                f"undeclared execution: {definition.action.declaration_id!r}"
            )
        return declaration

    def _build_request(
        self,
        definition: StepDefinition,
        declaration: ExecutionDeclaration,
        *,
        graph_run_id: str,
        step_id: str,
    ) -> ExecutionRequest:
        return build_step_request(
            definition,
            declaration,
            profile=self._execution_profile,
            run_id=self.step_run_id(graph_run_id, step_id),
            request_id=self.step_request_id(graph_run_id, step_id),
        )

    def _build_scope(self, run_id: str, declaration: ExecutionDeclaration) -> RunScope:
        """Build and freeze the perimeter for one step run.

        The same construction the API service performs for one declared
        verification: the service's own workspace root and execution profile, the
        server-side tool allowlist, and a command authority derived from the one
        declaration this step names. Nothing comes from the graph.
        """
        scope = RunScope(
            run_id=run_id,
            workspace=self._workspace,
            execution_profile=self._execution_profile,
            allowed_tool_ids=self._allowed_tool_ids,
            allowed_execution_commands=frozenset({declaration.executable}),
            acceptance_criteria=self._acceptance_criteria,
        )
        scope.freeze()
        require_active_scope(run_id, scope)
        return scope

    @staticmethod
    def _request_factory(request: ExecutionRequest) -> Callable[..., Sequence[ExecutionRequest]]:
        """A factory that returns exactly the one request it was built with.

        The factory can only *ask*; the coordinator still re-validates the
        command against the frozen scope, and the approval policy is still
        consulted for the real intent fingerprint.
        """

        def _factory(task: object, profile: object) -> Sequence[ExecutionRequest]:
            if not isinstance(profile, ProjectExecutionProfile):
                raise StepGraphError("execution profile is unavailable for this step")
            return (request,)

        return _factory

    def _step_store(self, step_run_id: str, step_id: str) -> RunStore:
        """A store bound to one step run.

        ``RunStore.bind_run`` mutates and returns the *same* object, so a fresh
        store over the same root is used per step: binding the caller's store
        would silently re-point every other writer that shares the instance.
        """
        return RunStore(root=self._run_store.root).bind_run(
            step_run_id, task_id=f"task.{step_id}"
        )

    def operation_identity(
        self,
        graph: StepGraph,
        definition: StepDefinition,
        graph_run_id: str,
    ) -> object:
        """The trusted operation identity of one step, composed server-side.

        The task identity covers the operator's declaration (its command, limits,
        and profile) and the step's plan, so an idempotency key can never be
        reused for a different action, and nothing in it is read from a request,
        a model, or a tool result.

        Exposed (like :meth:`intent_fingerprint`) so an operator-side tool can
        ask the durable ledger what a step is allowed to do without executing
        anything.
        """
        declaration = self._declaration(definition)
        payload = {
            "graph_id": graph.graph_id,
            "graph_run_id": graph_run_id,
            "step": planned_payload(definition),
            "declaration": {
                "declaration_id": declaration.declaration_id,
                "command": list(declaration.command),
                "profile_id": declaration.profile_id,
                "working_directory": declaration.working_directory,
                "timeout_seconds": declaration.timeout_seconds,
                "expected_exit_code": declaration.expected_exit_code,
                "artifact_targets": list(declaration.artifact_targets),
            },
        }
        blob = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        key = f"stepgraph:{graph_run_id}:{definition.step_id}"
        if len(key) > 200:
            raise StepGraphError(
                "the derived idempotency key exceeds the ledger's 200 character "
                f"limit for step {definition.step_id!r}"
            )
        return operation_identity_from_binding(
            operation_class=OperationClass.DECLARED_VERIFICATION,
            idempotency_key=key,
            declaration_id=declaration.declaration_id,
            task_identity=TaskIdentity(
                task_id=f"stepgraph.{graph_run_id}.{definition.step_id}"[:120],
                task_fingerprint=digest,
            ),
            criterion_identities=(),
        )

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def _fresh_snapshot(self, graph: StepGraph, graph_run_id: str) -> StepGraphSnapshot:
        records: list[StepRecord] = []
        for definition in graph.steps:
            record = StepRecord(
                step_id=definition.step_id,
                status=StepStatus.PENDING,
                action_fingerprint=definition.fingerprint(),
                declaration_id=definition.action.declaration_id,
                parameters=definition.action.parameters,
                verification_kind=(
                    definition.verification.kind.value
                    if definition.verification
                    else ""
                ),
                verification_expected=(
                    definition.verification.expectation_text()
                    if definition.verification
                    else ""
                ),
                updated_at=self._now(),
            )
            records.append(record)
        return StepGraphSnapshot(
            graph_id=graph.graph_id,
            graph_run_id=graph_run_id,
            status=StepGraphStatus.INCOMPLETE,
            steps=tuple(records),
            stopped_reason="not_started",
            updated_at=self._now(),
            declared_plan=declared_plan_of(graph),
        )

    def _resume_snapshot(
        self, graph: StepGraph, existing: StepGraphSnapshot
    ) -> StepGraphSnapshot:
        """Re-validate a saved record against the DECLARED plan before continuing.

        The authority for "what was declared" is the record itself. When the
        record carries a plan (Block #7 and later), the supplied graph is checked
        for **equality** with that plan, so a caller cannot widen, re-order or
        re-parameterise an attempt while reusing its verdicts. A record written
        before the plan was persisted is reported as missing its declaration
        rather than being re-validated against whatever the caller passes.
        """
        if existing.graph_id != graph.graph_id:
            raise StepGraphStateError(
                f"saved record belongs to graph {existing.graph_id!r}, not "
                f"{graph.graph_id!r}"
            )
        if not existing.declared_plan:
            raise StepGraphStateError(
                f"the saved record for {existing.graph_run_id!r} carries no declared "
                "plan, so the plan it was validated against cannot be recovered and "
                "a resume cannot prove the caller's graph is the one that was declared"
            )
        declared = graph_from_declared_plan(existing.graph_id, existing.declared_plan)
        if declared != graph:
            raise StepGraphStateError(
                "the caller's graph is not the plan this attempt declared: a saved step "
                "was planned differently, so resuming against it would re-plan work "
                "under an old verdict"
            )
        saved_ids = [record.step_id for record in existing.steps]
        declared_ids = list(graph.step_ids)
        if saved_ids != declared_ids:
            raise StepGraphStateError(
                "saved step set does not match the graph being resumed: "
                f"saved={saved_ids!r} declared={declared_ids!r}"
            )
        for record in existing.steps:
            definition = graph.step(record.step_id)
            expected = definition.fingerprint()
            if record.action_fingerprint != expected:
                raise StepGraphStateError(
                    f"saved step {record.step_id!r} was planned differently; its "
                    "recorded action fingerprint does not match the definition, so "
                    "resuming it would re-plan work under an old verdict"
                )
            self._screen_saved_record(record)
        # The declaration is re-persisted on every resume, so a record written by
        # an earlier build gains it without the attempt's verdicts being touched.
        existing.declared_plan = declared_plan_of(declared)
        existing.updated_at = self._now()
        return existing

    def _screen_saved_record(self, record: StepRecord) -> None:
        """Apply the evidence screen to one record read back from a save.

        Shared by every entry point that turns a saved record into a verdict, so a
        stored status can never be trusted on one path and distrusted on another.
        The screen changes the in-memory record only; it never rewrites the saved
        claim to hide the gap.
        """

        if record.status is StepStatus.RUNNING:
            # A durable RUNNING with no outcome means the process that wrote
            # it may have died after dispatching. That is the unprovable case.
            record.status = StepStatus.UNKNOWN
            record.add_evidence("interrupted_after_dispatch_outcome_unproven")
            record.updated_at = self._now()
        if record.status is StepStatus.SUCCEEDED:
            # A saved SUCCEEDED is a claim, not proof that a command ran. Before
            # it may be summarised as COMPLETED, the record has to carry something
            # that actually shows a successful execution, and it must not
            # contradict itself. Nothing universal is demanded here: a step that
            # declares no check is judged by its recorded execution alone, so a
            # legitimate no-verification step is unaffected. The rule downgrades
            # the in-memory record to UNKNOWN -- which derives INDETERMINATE --
            # and never rewrites the saved record to hide the gap.
            outcome = str(record.execution_outcome or "").upper()
            contradicts = bool(outcome) and "SUCCESS" not in outcome
            unrun_declared_check = (
                str(record.verification_kind or "").upper() == "EXIT_CODE"
                and record.verification_status is not VerificationStatus.PASSED
            )
            has_execution_evidence = (
                bool(outcome)
                or record.execution_exit_code is not None
                or record.verification_status is VerificationStatus.PASSED
            )
            if contradicts or unrun_declared_check or not has_execution_evidence:
                record.status = StepStatus.UNKNOWN
                if not has_execution_evidence:
                    tag = "resumed_success_without_execution_evidence"
                elif contradicts:
                    tag = "resumed_success_contradicts_recorded_execution"
                else:
                    tag = "resumed_success_with_unrun_declared_verification"
                record.add_evidence(tag)
                # The original claim is preserved rather than dropped, so the
                # record still says what was asserted and why it is not accepted.
                record.add_evidence(
                    "saved_claim_was_succeeded_and_is_not_proof"
                )
                record.updated_at = self._now()

    def _screen_snapshot(self, snapshot: StepGraphSnapshot) -> StepGraphSnapshot:
        """Screen a saved snapshot's records and re-derive its status from them.

        The stored ``status`` is a claim made when the record was written. Deriving
        the verdict from the screened records is what makes ``run`` and ``resume``
        agree: a record whose steps do not justify completion comes back
        ``INDETERMINATE`` on both, and a fully evidenced record still comes back
        ``COMPLETED`` on both.
        """

        for record in snapshot.steps:
            self._screen_saved_record(record)
        snapshot.status = derive_graph_status(
            tuple(snapshot.steps), cancelled=bool(snapshot.cancelled)
        )
        return snapshot

    def _persist(self, snapshot: StepGraphSnapshot) -> None:
        """Write the graph's progress through the existing ``RunStore``.

        Deliberately stored under ``project_state`` with the store's own
        ``state``/``status`` left empty: this run id is a bookkeeping record for
        one attempt, not a live ``Run``, and it must not be mistaken for one by
        the read-time run classifier.
        """
        self._run_store.write_state(
            RunStateSnapshot(
                run_id=snapshot.graph_run_id,
                task_id="",
                state="",
                status="",
                project_state={_SCHEMA_KEY: snapshot.to_dict()},
                updated_at=self._now(),
            )
        )

    def _result_from_snapshot(
        self,
        snapshot: StepGraphSnapshot,
        *,
        stopped_reason: str,
        executed: bool,
        executed_steps: int = 0,
        started_at: str = "",
    ) -> StepGraphResult:
        return StepGraphResult(
            graph_id=snapshot.graph_id,
            graph_run_id=snapshot.graph_run_id,
            status=snapshot.status,
            steps=tuple(self._copy(record) for record in snapshot.steps),
            stopped_reason=stopped_reason or snapshot.stopped_reason,
            executed_steps=executed_steps,
            executed=executed,
            cancelled=bool(snapshot.cancelled),
            idempotency_degraded=bool(
                snapshot.idempotency_degraded or self._idempotency_guard.degraded
            ),
            started_at=started_at,
            finished_at=self._now(),
        )

    @staticmethod
    def _copy(record: StepRecord) -> StepRecord:
        """Return an independent copy, so a result can never be mutated later."""
        return StepRecord.from_dict(record.to_dict())

    # ------------------------------------------------------------------
    # small utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _cancel_requested(cancel_event: object | None) -> bool:
        if cancel_event is None:
            return False
        is_set = getattr(cancel_event, "is_set", None)
        if callable(is_set):
            return bool(is_set())
        return bool(getattr(cancel_event, "cancelled", False))

    @staticmethod
    def _validate_budget(step_budget: int | None) -> int | None:
        if step_budget is None:
            return None
        if isinstance(step_budget, bool) or not isinstance(step_budget, int):
            raise StepGraphError("step_budget must be a non-negative int or None")
        if step_budget < 0:
            raise StepGraphError("step_budget must be a non-negative int or None")
        return step_budget

    @staticmethod
    def _validate_graph_run_id(graph_run_id: str) -> str:
        if not isinstance(graph_run_id, str) or not graph_run_id.strip():
            raise StepGraphError("graph_run_id must be a non-empty string")
        candidate = graph_run_id.strip()
        if not _RUN_ID.match(candidate):
            raise StepGraphError(
                "graph_run_id must be 1-100 characters of [A-Za-z0-9._-] so it can "
                f"be used as a run id and an idempotency key: {candidate!r}"
            )
        return candidate

    @staticmethod
    def _validate_derived_ids(graph: StepGraph, graph_run_id: str) -> None:
        """Refuse an id combination whose derived run id would not be storable.

        No truncation and no hashing: a silently shortened id could collide with
        another step's record, so an over-long combination is refused instead.
        """
        for step in graph.steps:
            derived = StepGraphRunner.step_run_id(graph_run_id, step.step_id)
            if len(derived) > _MAX_DERIVED_RUN_ID:
                raise StepGraphError(
                    f"the derived run id for step {step.step_id!r} is "
                    f"{len(derived)} characters, over the {_MAX_DERIVED_RUN_ID} "
                    "character limit; shorten graph_run_id or the step id"
                )

    @staticmethod
    def _claim_graph_run(graph_run_id: str) -> bool:
        with _IN_FLIGHT_LOCK:
            if graph_run_id in _IN_FLIGHT:
                return False
            _IN_FLIGHT.add(graph_run_id)
            return True

    @staticmethod
    def _release_graph_run(graph_run_id: str) -> None:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(graph_run_id)
