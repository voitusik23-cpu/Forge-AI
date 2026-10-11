"""Reliable recovery and Project Memory (Block #7), verified from outside.

WHAT THIS FILE PROVES, AND HOW
==============================

Every claim is proved by **observed data**: a durable record read back from an
isolated ``RunStore``, a decision the *existing* idempotency ledger returned, a
file that does or does not exist on disk, or a digest computed from the bytes of a
file the scanner really read. Nothing patches a private method to make a test pass,
and no test contacts a network or reads a key.

The twelve scenarios the owner listed:

1.  recovery after a process restart -- ``RestartRecoveryTests`` (a **new** store
    and a **new** planner over the same temp root: a real restart, not a mock);
2.  recovery of a multi-step graph -- ``RestartRecoveryTests``;
3.  detection of an unfinished step -- ``RestartRecoveryTests``;
4.  safe continuation of an idempotent operation -- ``RepeatControlTests``;
5.  refusal to auto-repeat a dangerous operation -- ``RepeatControlTests``;
6.  transition to indeterminate -- ``RepeatControlTests``;
7.  protection against concurrent recovery -- ``ConcurrentRecoveryTests``;
8.  invalidation of stale facts after a file changes -- ``FreshnessTests``;
9.  detection of contradictory evidence -- ``FreshnessTests``;
10. recovery of Project Factory context -- ``FactoryContextTests``;
11. preservation of workspace and authorization boundaries --
    ``BoundaryTests``;
12. no secret leakage into memory or logs -- ``SecretBoundaryTests``.

ISOLATION
=========

Every test builds its own ``RunStore(root=...)``, its own
``RunIdempotencyGuard(root=...)`` and its own workspace under temporary
directories, because the default store and ledger roots are shared per-user and
have produced FALSE failures in this repository before. ``RunScope.release_all()``
runs before and after every test.

NAMING THE TESTS THAT MUST FAIL BEFORE A FIX
============================================

Tests that are the target of a deliberate source injection carry
``_INJECTION_TARGET`` in their docstring. See ``tests/INJECTION-EVIDENCE.md`` and
the report for the before/after SHA-256 of every patched file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import threading
import unittest

from app.agent_runtime import idempotency_integration as integration
from app.agent_runtime.idempotency import (
    IdempotencyFailureCode,
    IdempotencyVerdict,
    OperationClass,
    RunLifecycleState,
    SideEffectKind,
    SideEffectRecord,
    SideEffectState,
)
from app.agent_runtime.idempotency_integration import (
    RepeatGuarantee,
    RunIdempotencyGuard,
    operation_identity_from_binding,
)
from app.agent_runtime.criterion_identity import TaskIdentity
from app.context.models import ContextSourceType, ContextTrustLevel
from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.runtime.bootstrap import create_runtime
from app.runtime.continuation import (
    ProjectFactoryContextStore,
    capture_context,
    plan_continuation,
    recall_context,
)
from app.runtime.recovery import (
    RECOVERY_CLAIMS_KEY,
    RecoveryClaimLog,
    RecoveryDecision,
    RecoveryExecutor,
    RecoveryPlanner,
    RecoveryRefused,
    RecoveryStepRequest,
)
from app.runtime.run_scope import RunScope
from app.runtime.run_store import (
    ExecutionClassification,
    RunStateSnapshot,
    RunStore,
)
from app.tools.approval import ApprovalPolicy
from app.tools.workspace import Workspace
from app.understanding.memory import (
    STALE_REASON_AMBIGUOUS_MOVE,
    STALE_REASON_CONTENT_CHANGED,
    STALE_REASON_DELETED,
    STALE_REASON_RENAMED,
    EvidencePin,
    FreshnessIndex,
    FreshnessOutcome,
    MemoryItemKind,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    ProjectMemory,
    ProjectMemoryStore,
    build_freshness_index,
    freshness_of,
    memory_from_snapshot,
    record_from_inferred_fact,
    record_from_structural_fact,
)
from app.understanding.models import InferredFact, StructuralFact, StructuralFactType
from app.understanding.snapshotter import UnderstandingSnapshotter
from app.workflows.step_graph import (
    StepAction,
    StepDefinition,
    StepGraph,
    StepGraphSnapshot,
    StepGraphStateError,
    StepGraphStatus,
    StepRecord,
    StepStatus,
    StepVerification,
    VerificationKind,
    VerificationStatus,
    declared_plan_of,
)
from app.workflows.step_runner import StepGraphRunner

# -- operator-declared programmes -------------------------------------------

APPEND_A = (
    "python",
    "-c",
    "open('a.txt','a',encoding='utf-8').write('x')",
)
APPEND_B = (
    "python",
    "-c",
    "open('b.txt','a',encoding='utf-8').write('x')",
)
APPEND_C = (
    "python",
    "-c",
    "open('c.txt','a',encoding='utf-8').write('x')",
)
#: Writes a declared artifact, so a recorded success can be re-checked against the
#: workspace afterwards.
MAKE_ARTIFACT = (
    "python",
    "-c",
    "open('made.txt','w',encoding='utf-8').write('payload')",
)
PROFILE_ID = "block7-test-profile"


def _drain(directory: tempfile.TemporaryDirectory) -> None:
    try:
        directory.cleanup()
    except (PermissionError, OSError):
        pass


class Block7Case(unittest.TestCase):
    """One workspace, one run store, one ledger, no real provider."""

    def setUp(self) -> None:
        RunScope.release_all()
        self._dirs: list[tempfile.TemporaryDirectory] = []
        self.base = self._temp_dir()
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        self.store_root = self._temp_dir()
        self.ledger_root = self._temp_dir()
        self.runtime = create_runtime(test_mode=True)

    def tearDown(self) -> None:
        RunScope.release_all()
        for directory in self._dirs:
            _drain(directory)

    def _temp_dir(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self._dirs.append(directory)
        return Path(directory.name)

    # -- composition -------------------------------------------------------
    def new_store(self) -> RunStore:
        """A **brand new** store over the same root: what a restart produces."""
        return RunStore(root=str(self.store_root))

    def guard(self) -> RunIdempotencyGuard:
        return RunIdempotencyGuard.create(ledger_root=str(self.ledger_root))

    def profile(self, *, timeout_seconds: float = 60.0) -> ProjectExecutionProfile:
        return ProjectExecutionProfile(
            profile_id=PROFILE_ID,
            allowed_commands=("python",),
            capabilities=frozenset(
                {
                    ExecutionCapability.INTERPRET_TEXT,
                    ExecutionCapability.INTERPRET_MODULE,
                }
            ),
            network_access=False,
            working_directory=".",
            timeout_seconds=timeout_seconds,
        )

    def declaration(
        self,
        declaration_id: str,
        command: tuple[str, ...],
        *,
        artifact_targets: tuple[str, ...] = (),
        timeout_seconds: float | None = 30.0,
    ) -> ExecutionDeclaration:
        return ExecutionDeclaration(
            declaration_id=declaration_id,
            command=command,
            profile_id=PROFILE_ID,
            working_directory=".",
            artifact_targets=artifact_targets,
            timeout_seconds=timeout_seconds,
            intent=f"block7 test declaration {declaration_id}",
        )

    def runner(
        self,
        declarations: dict[str, ExecutionDeclaration],
        *,
        store: RunStore | None = None,
        ledger_root: Path | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approval_resolver: object | None = None,
        artifacts_are_harvested: bool = False,
        isolate_workspace: bool = False,
    ) -> StepGraphRunner:
        return StepGraphRunner(
            runtime=self.runtime,
            workspace=self.workspace,
            execution_profile=self.profile(),
            declarations=declarations,
            run_store=store if store is not None else self.new_store(),
            idempotency_guard=RunIdempotencyGuard.create(
                ledger_root=str(ledger_root if ledger_root is not None else self.ledger_root)
            ),
            approval_policy=approval_policy or ApprovalPolicy(),
            execution_coordinator=ExecutionCoordinator(
                LocalExecutionAdapter(
                    workspace_root=self.root, isolate_workspace=isolate_workspace
                )
            ),
            approval_resolver=approval_resolver,
            allowed_tool_ids=frozenset(),
            artifacts_are_harvested=artifacts_are_harvested,
        )

    def planner(
        self,
        declarations: dict[str, ExecutionDeclaration],
        *,
        store: RunStore | None = None,
        ledger_root: Path | None = None,
        with_workspace: bool = True,
    ) -> RecoveryPlanner:
        bound = store if store is not None else self.new_store()
        root = ledger_root if ledger_root is not None else self.ledger_root

        def factory() -> StepGraphRunner:
            return self.runner(
                declarations, store=bound, ledger_root=root
            )

        return RecoveryPlanner(
            run_store=bound,
            idempotency_guard=RunIdempotencyGuard.create(ledger_root=str(root)),
            declarations=declarations,
            workspace=self.workspace if with_workspace else None,
            runner_factory=factory,
        )

    # -- observation helpers -----------------------------------------------
    def count_lines(self, name: str) -> int:
        path = self.root / name
        if not path.exists():
            return 0
        return len(
            [line for line in path.read_text(encoding="utf-8").splitlines() if line]
        )

    def three_step_graph(self, graph_id: str = "recovery") -> StepGraph:
        return StepGraph(
            graph_id=graph_id,
            steps=(
                StepDefinition(step_id="a", action=StepAction("a")),
                StepDefinition(step_id="b", action=StepAction("b"), depends_on=("a",)),
                StepDefinition(step_id="c", action=StepAction("c"), depends_on=("b",)),
            ),
        )

    def abc_declarations(self) -> dict[str, ExecutionDeclaration]:
        return {
            "a": self.declaration("a", APPEND_A),
            "b": self.declaration("b", APPEND_B),
            "c": self.declaration("c", APPEND_C),
        }


# ---------------------------------------------------------------------------
# 1, 2, 3. restart, multi-step recovery, unfinished step detection
# ---------------------------------------------------------------------------


class RestartRecoveryTests(Block7Case):
    """A real restart: new store, new planner, same temporary root."""

    def test_recovery_after_a_process_restart(self) -> None:
        """A brand new store and planner over the same root recover the attempt.

        _INJECTION_TARGET: deleting the declared-plan re-validation in
        ``StepGraphRunner._resume_snapshot`` makes the ``resume`` below accept a
        changed plan instead of refusing it.
        """
        declarations = self.abc_declarations()
        first = self.runner(declarations)
        partial = first.run(self.three_step_graph(), "restart-run", step_budget=1)
        self.assertEqual(partial.status, StepGraphStatus.INCOMPLETE)
        self.assertEqual(partial.step("a").status, StepStatus.SUCCEEDED)
        self.assertEqual(partial.step("b").status, StepStatus.PENDING)
        self.assertEqual(self.count_lines("a.txt"), 1)
        self.assertEqual(self.count_lines("b.txt"), 0)

        # -- the process ends here. Everything below is a new object graph over
        #    the same durable root, and it has never seen the graph in memory.
        restarted_store = self.new_store()
        restored = self.runner(declarations, store=restarted_store)

        # The declared plan survived on disk, byte-identically.
        recorded = restored.recorded("restart-run")
        self.assertIsNotNone(recorded)
        self.assertEqual(
            recorded.declared_plan, declared_plan_of(self.three_step_graph())
        )

        # The runner can resume from the record alone: no graph argument at all.
        resumed = restored.resume(graph_run_id="restart-run")
        self.assertEqual(resumed.status, StepGraphStatus.COMPLETED)
        self.assertEqual(self.count_lines("a.txt"), 1, "the settled step was repeated")
        self.assertEqual(self.count_lines("b.txt"), 1)
        self.assertEqual(self.count_lines("c.txt"), 1)
        self.assertEqual(resumed.step("a").attempts, 1)

    def test_recovery_of_a_multi_step_graph_decides_every_step(self) -> None:
        declarations = self.abc_declarations()
        first = self.runner(declarations)
        first.run(self.three_step_graph(), "multi-run", step_budget=1)

        planner = self.planner(declarations)
        findings = planner.unfinished_runs()
        self.assertEqual([f.graph_run_id for f in findings], ["multi-run"])

        plan = planner.plan("multi-run")
        self.assertTrue(plan.recoverable, plan.blocked_reason)
        self.assertEqual(plan.record_classification, ExecutionClassification.INDETERMINATE)
        # Every declared step has a decision, in the record's own order.
        self.assertEqual([s.step_id for s in plan.steps], ["a", "b", "c"])
        self.assertEqual(plan.step("a").decision, RecoveryDecision.ALREADY_SETTLED)
        self.assertEqual(plan.step("b").decision, RecoveryDecision.CONTINUE)
        # c's dependency b has not succeeded, so c may not start yet.
        self.assertEqual(plan.step("c").decision, RecoveryDecision.BLOCKED)
        self.assertEqual(plan.by_decision(RecoveryDecision.CONTINUE), ("b",))

    def test_detection_of_an_unfinished_step(self) -> None:
        declarations = self.abc_declarations()
        first = self.runner(declarations)
        first.run(self.three_step_graph(), "detect-run", step_budget=2)
        self.assertEqual(self.count_lines("a.txt"), 1)
        self.assertEqual(self.count_lines("b.txt"), 1)
        self.assertEqual(self.count_lines("c.txt"), 0)

        planner = self.planner(declarations)
        plan = planner.plan("detect-run")
        unfinished = plan.by_decision(RecoveryDecision.CONTINUE)
        self.assertEqual(unfinished, ("c",))
        self.assertEqual(plan.step("c").status, StepStatus.PENDING)
        self.assertEqual(plan.step("c").reason, "pending_and_every_dependency_succeeded")
        # A finished step is never offered for continuation.
        self.assertNotIn("a", unfinished)
        self.assertNotIn("b", unfinished)

    def test_a_recorded_success_whose_declared_artifact_is_gone_needs_reverification(
        self,
    ) -> None:
        """A success is not current when the artifact it was checked by is gone.

        _INJECTION_TARGET: making ``RecoveryPlanner._artifact_state`` always return
        ``unknown`` turns the REVERIFY below into NEEDS_OWNER.
        """
        declarations = {
            "artifact": self.declaration(
                "artifact", MAKE_ARTIFACT, artifact_targets=("made.txt",)
            )
        }
        graph = StepGraph(
            graph_id="artifact-graph",
            steps=(
                StepDefinition(
                    step_id="made",
                    action=StepAction("artifact"),
                    verification=StepVerification(
                        VerificationKind.ARTIFACT_PRODUCED, artifact_target="made.txt"
                    ),
                ),
            ),
        )
        # The recorded success is written through the same public codec the runner
        # writes it with, so what is under test below is the planner's re-check and
        # nothing else. What the runner itself does with a real harvest is Block
        # #6's subject and is asserted in its own file.
        self.new_store().write_state(
            RunStateSnapshot(
                run_id="artifact-run",
                project_state={
                    "step_graph": StepGraphSnapshot(
                        graph_id=graph.graph_id,
                        graph_run_id="artifact-run",
                        status=StepGraphStatus.COMPLETED,
                        steps=(
                            StepRecord(
                                step_id="made",
                                status=StepStatus.SUCCEEDED,
                                action_fingerprint=graph.step("made").fingerprint(),
                                declaration_id="artifact",
                                step_run_id=StepGraphRunner.step_run_id(
                                    "artifact-run", "made"
                                ),
                                execution_exit_code=0,
                                execution_outcome="EXECUTION_SUCCESS",
                                verification_kind="ARTIFACT_PRODUCED",
                                verification_status=VerificationStatus.PASSED,
                                verification_expected="artifact_produced=made.txt",
                                verification_observed=(
                                    "artifact_produced=made.txt sha256=deadbeef"
                                ),
                                attempts=1,
                            ),
                        ),
                        stopped_reason="all_steps_resolved",
                        declared_plan=declared_plan_of(graph),
                    ).to_dict()
                },
            )
        )
        # The artifact really is where the recorded check found it.
        (self.root / "made.txt").write_text("payload", encoding="utf-8")
        planner = self.planner(declarations)
        present = planner.plan("artifact-run")
        self.assertEqual(present.record_classification, ExecutionClassification.TERMINAL_SUCCESS)
        self.assertEqual(present.step("made").decision, RecoveryDecision.ALREADY_SETTLED)
        self.assertEqual(
            present.step("made").reason, "declared_artifact_is_present_in_the_workspace"
        )

        # Now the project really changes: the artifact the check rested on is gone.
        (self.root / "made.txt").unlink()
        stale = planner.plan("artifact-run")
        self.assertEqual(stale.step("made").decision, RecoveryDecision.REVERIFY)
        self.assertEqual(
            stale.step("made").reason,
            "declared_artifact_is_absent_from_the_workspace",
        )
        # The graph's own verdict is untouched: only the check is in doubt.
        self.assertEqual(
            stale.record_classification, ExecutionClassification.TERMINAL_SUCCESS
        )

        # And a workspace the planner cannot inspect never produces a fabricated
        # verdict: it asks the owner rather than claiming the artifact is absent.
        replacement_root = self._temp_dir() / "after-change"
        replacement_root.mkdir()
        uninspectable = RecoveryPlanner(
            run_store=self.new_store(),
            idempotency_guard=self.guard(),
            declarations=declarations,
            workspace=None,
            runner_factory=lambda: self.runner(declarations),
        )
        unknown = uninspectable.plan("artifact-run")
        self.assertEqual(unknown.step("made").decision, RecoveryDecision.NEEDS_OWNER)
        self.assertEqual(
            unknown.step("made").reason, "declared_artifact_cannot_be_located"
        )

    def test_a_record_without_a_declared_plan_is_refused_not_guessed(self) -> None:
        declarations = self.abc_declarations()
        graph = self.three_step_graph()
        # A record written the way a pre-Block-7 build wrote it: no declared plan.
        self.new_store().write_state(
            RunStateSnapshot(
                run_id="no-plan-run",
                project_state={
                    "step_graph": StepGraphSnapshot(
                        graph_id=graph.graph_id,
                        graph_run_id="no-plan-run",
                        steps=(
                            StepRecord(
                                step_id="a",
                                status=StepStatus.PENDING,
                                action_fingerprint=graph.step("a").fingerprint(),
                            ),
                        ),
                    ).to_dict()
                },
            )
        )
        planner = self.planner(declarations)
        plan = planner.plan("no-plan-run")
        self.assertFalse(plan.recoverable)
        self.assertEqual(plan.blocked_reason, "declared_plan_missing")
        self.assertEqual(plan.step("a").decision, RecoveryDecision.UNKNOWN)

        runner = self.runner(declarations)
        with self.assertRaises(StepGraphStateError) as caught:
            runner.resume(graph, "no-plan-run")
        self.assertIn("carries no declared plan", str(caught.exception))
        self.assertEqual(self.count_lines("a.txt"), 0)


# ---------------------------------------------------------------------------
# 4, 5, 6. repeat control
# ---------------------------------------------------------------------------


class RepeatControlTests(Block7Case):
    """Repeats are decided from ledger evidence, never from optimism."""

    def _write_running_record(
        self, graph: StepGraph, graph_run_id: str, *, step_ids: tuple[str, ...]
    ) -> None:
        """A durable record whose named steps are RUNNING: the crash window, on disk.

        ``declared_plan`` is written from ``graph`` itself, because a resume
        re-validates against the plan the attempt declared -- a record whose
        declaration disagrees with its own steps would be refused, and rightly so.
        """
        self.new_store().write_state(
            RunStateSnapshot(
                run_id=graph_run_id,
                project_state={
                    "step_graph": StepGraphSnapshot(
                        graph_id=graph.graph_id,
                        graph_run_id=graph_run_id,
                        status=StepGraphStatus.INCOMPLETE,
                        steps=tuple(
                            StepRecord(
                                step_id=step_id,
                                status=StepStatus.RUNNING,
                                action_fingerprint=graph.step(step_id).fingerprint(),
                                declaration_id=graph.step(step_id).action.declaration_id,
                                step_run_id=StepGraphRunner.step_run_id(
                                    graph_run_id, step_id
                                ),
                                execution_outcome="UNKNOWN",
                                verification_status=VerificationStatus.NOT_RUN,
                                attempts=1,
                            )
                            for step_id in step_ids
                        ),
                        stopped_reason="in_flight",
                        declared_plan=declared_plan_of(graph),
                    ).to_dict()
                },
            )
        )

    def _crash_window(
        self,
        *,
        graph_id: str,
        run_id: str,
        declaration_id: str,
        command: tuple[str, ...],
        effect_kind: SideEffectKind,
    ) -> tuple[StepGraph, dict[str, ExecutionDeclaration]]:
        """Build the exact on-disk shape a crash between dispatch and settlement leaves.

        The graph is a single step, so the record's declared plan and its step set
        agree -- which is what makes the record resumable at all. The ledger claim
        is made through the runner's own ``operation_identity``, so the key is the
        real one, and the side effect is left ``STARTED`` with no completion.
        """
        declarations = {declaration_id: self.declaration(declaration_id, command)}
        graph = StepGraph(
            graph_id=graph_id,
            steps=(StepDefinition(step_id="s", action=StepAction(declaration_id)),),
        )
        runner = self.runner(declarations)
        identity = runner.operation_identity(graph, graph.step("s"), run_id)
        guard = self.guard()
        decision = guard.begin(
            identity,
            requested_run_id=StepGraphRunner.step_run_id(run_id, "s"),
        )
        self.assertEqual(decision.verdict, IdempotencyVerdict.START, decision.reason)
        guard.engine.ledger.begin_side_effect(
            identity,
            SideEffectRecord(
                fingerprint=identity.fingerprint,
                kind=effect_kind,
                state=SideEffectState.NOT_STARTED,
                action_id=StepGraphRunner.step_run_id(run_id, "s"),
                # Deliberately NOT replay_safe: the ledger decides that from the
                # kind, exactly as the runner relies on.
                replay_safe=False,
            ),
        )
        self._write_running_record(graph, run_id, step_ids=("s",))
        return graph, declarations

    def test_safe_continuation_of_an_idempotent_operation(self) -> None:
        """Forward progress continues automatically; nothing is dispatched twice.

        A graph that stopped with steps still unstarted continues safely: their
        side effects have not happened, so continuing cannot duplicate one. The
        ``RecoveryExecutor`` claims each continuable step durably, and the executor
        really obtains the step's success from a real child process.
        """
        declarations = self.abc_declarations()
        first = self.runner(declarations)
        partial = first.run(self.three_step_graph(), "continue-run", step_budget=1)
        self.assertEqual(partial.step("a").status, StepStatus.SUCCEEDED)
        self.assertEqual(partial.step("b").status, StepStatus.PENDING)
        self.assertEqual(self.count_lines("a.txt"), 1)

        planner = self.planner(declarations)
        plan = planner.plan("continue-run")
        self.assertTrue(plan.recoverable)
        self.assertEqual(plan.by_decision(RecoveryDecision.CONTINUE), ("b",))
        # A settled step is never offered for a second dispatch.
        self.assertEqual(plan.step("a").decision, RecoveryDecision.ALREADY_SETTLED)

        claim = planner.claim_step("continue-run", "b", note="forward progress")
        self.assertIsNotNone(claim)
        self.assertTrue(planner.claims.claim_path("continue-run", "b").is_file())
        executor = RecoveryExecutor(
            planner=planner, runner_factory=lambda: self.runner(declarations)
        )
        result = executor.continue_plan(
            plan,
            (RecoveryStepRequest(step_id="b", token=claim.token),),
            step_budget=1,
        )
        self.assertEqual(result.executed_steps, 1, result.stopped_reason)
        self.assertEqual(result.step("b").status, StepStatus.SUCCEEDED)
        self.assertEqual(result.step("b").execution_exit_code, 0)
        # The already-settled step was not run a second time.
        self.assertEqual(self.count_lines("a.txt"), 1)
        self.assertEqual(self.count_lines("b.txt"), 1)

    def test_a_convergent_repeat_is_convergent_in_evidence_but_not_redelivered(
        self,
    ) -> None:
        """A replay-safe in-flight effect: convergent in principle, not re-dispatched.

        This is the honest boundary, and the test states both halves. The ledger's
        evidence really does say the repetition converges
        (``CONVERGENT_REPEAT``), and that is reported. But the step also carries a
        durable claim with a possibly-dispatched process, and Block #6's
        ``resume`` admits no second delivery under such a claim -- it reads the
        record back as ``UNKNOWN``. So the recovery decision is ``NEEDS_OWNER``
        with that guarantee as its evidence, never ``CONTINUE``: a decision this
        module could not honour would be worse than no decision at all.
        """
        graph, declarations = self._crash_window(
            graph_id="convergent-graph",
            run_id="convergent-run",
            declaration_id="a",
            command=APPEND_A,
            effect_kind=SideEffectKind.WORKSPACE_WRITE,
        )
        planner = self.planner(declarations)
        plan = planner.plan("convergent-run")
        decision = plan.step("s")
        self.assertEqual(decision.classification, ExecutionClassification.INDETERMINATE)
        self.assertEqual(decision.decision, RecoveryDecision.NEEDS_OWNER)
        self.assertEqual(
            decision.reason,
            "dispatched_outcome_unproven_and_the_claim_is_not_redeliverable",
        )
        self.assertIsNotNone(decision.repeat)
        self.assertEqual(decision.repeat.guarantee, RepeatGuarantee.CONVERGENT_REPEAT)
        self.assertTrue(decision.repeat.permits_automatic_repeat)
        self.assertEqual(decision.repeat.unprovable_effects, ())

        # No claim may be taken: the step is not continuable, so continuing it is
        # not merely refused, it is unstateable.
        self.assertIsNone(planner.claim_step("convergent-run", "s"))

        # And the runner really does not redeliver it: the outcome stays UNKNOWN
        # and no child process runs.
        resumed = self.runner(declarations).resume(graph_run_id="convergent-run")
        self.assertEqual(resumed.executed_steps, 0)
        self.assertEqual(resumed.step("s").status, StepStatus.UNKNOWN)
        self.assertEqual(self.count_lines("a.txt"), 0)

    def test_refusal_to_auto_repeat_a_dangerous_operation(self) -> None:
        """A non-replay-safe effect that never settled is never repeated.

        _INJECTION_TARGET: making ``RunIdempotencyGuard.repeat_decision`` ignore
        ``unprovable_effects`` turns the NEEDS_OWNER below into CONTINUE and lets a
        host process be launched a second time.
        """
        graph, declarations = self._crash_window(
            graph_id="dangerous-graph",
            run_id="dangerous-run",
            declaration_id="a",
            command=APPEND_A,
            effect_kind=SideEffectKind.HOST_PROCESS,
        )
        planner = self.planner(declarations)
        plan = planner.plan("dangerous-run")
        decision = plan.step("s")
        self.assertEqual(decision.decision, RecoveryDecision.NEEDS_OWNER)
        self.assertEqual(
            decision.reason,
            "dispatched_outcome_unproven_and_effect_is_not_replay_safe",
        )
        self.assertEqual(
            decision.repeat.guarantee, RepeatGuarantee.REFUSED_SIDE_EFFECT_UNKNOWN
        )
        self.assertFalse(decision.repeat.permits_automatic_repeat)
        self.assertIn("host_process", decision.unproven_effects)

        # A claim is refused outright for a step nobody may continue.
        self.assertIsNone(planner.claim_step("dangerous-run", "s"))

        # And the ledger itself already refuses the dispatch, which is the
        # existing Block #6 behaviour this decision is read from.
        fresh_guard = self.guard()
        identity = self.runner(declarations).operation_identity(
            graph, graph.step("s"), "dangerous-run"
        )
        ledger_decision = fresh_guard.begin(
            identity,
            requested_run_id=StepGraphRunner.step_run_id("dangerous-run", "s"),
        )
        self.assertEqual(ledger_decision.verdict, IdempotencyVerdict.REFUSE)
        self.assertEqual(
            ledger_decision.failure_code, IdempotencyFailureCode.SIDE_EFFECT_UNKNOWN
        )
        self.assertEqual(self.count_lines("a.txt"), 0)

    def test_transition_to_indeterminate(self) -> None:
        """An unprovable outcome becomes INDETERMINATE, never success or failure."""
        graph, declarations = self._crash_window(
            graph_id="indeterminate-graph",
            run_id="indeterminate-run",
            declaration_id="a",
            command=APPEND_A,
            effect_kind=SideEffectKind.HOST_PROCESS,
        )
        planner = self.planner(declarations)
        plan = planner.plan("indeterminate-run")
        self.assertEqual(
            plan.record_classification, ExecutionClassification.INDETERMINATE
        )
        self.assertEqual(
            plan.step("s").classification, ExecutionClassification.INDETERMINATE
        )
        self.assertNotEqual(
            plan.step("s").classification, ExecutionClassification.TERMINAL_SUCCESS
        )
        self.assertNotEqual(
            plan.step("s").classification, ExecutionClassification.TERMINAL_FAILURE
        )
        # The read-time classifier independently reaches the same answer from the
        # step-graph verdict the record carries, rather than from the empty fields.
        self.assertEqual(
            plan.record_reason, "step_graph_status"
        )

        # A caller who tries anyway gets the changed plan back, still unproven:
        # nothing is substituted for the unknown outcome.
        runner = self.runner(declarations)
        resumed = runner.resume(graph_run_id="indeterminate-run")
        self.assertEqual(resumed.status, StepGraphStatus.INDETERMINATE)
        self.assertEqual(resumed.step("s").status, StepStatus.UNKNOWN)
        self.assertEqual(resumed.step("s").execution_outcome, "UNKNOWN")
        self.assertIsNone(resumed.step("s").execution_exit_code)
        self.assertEqual(self.count_lines("a.txt"), 0)
        # A second, independent reading is still indeterminate: the resume did not
        # settle it into success or failure.
        self.assertEqual(
            self.planner(declarations).plan("indeterminate-run").step("s").classification,
            ExecutionClassification.INDETERMINATE,
        )

    def test_a_step_waiting_for_approval_is_never_continued_automatically(self) -> None:
        """An unresolved approval is the owner's decision, not a resumable state."""
        declarations = {"a": self.declaration("a", APPEND_A)}
        graph = StepGraph(
            graph_id="approval-graph",
            steps=(StepDefinition(step_id="s", action=StepAction("a")),),
        )
        runner = self.runner(
            declarations,
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=None,
        )
        first = runner.run(graph, "approval-run")
        self.assertEqual(first.step("s").status, StepStatus.WAITING_APPROVAL)
        self.assertEqual(self.count_lines("a.txt"), 0)

        planner = self.planner(declarations)
        plan = planner.plan("approval-run")
        decision = plan.step("s")
        self.assertEqual(decision.status, StepStatus.WAITING_APPROVAL)
        self.assertEqual(decision.classification, ExecutionClassification.WAITING)
        self.assertEqual(decision.decision, RecoveryDecision.NEEDS_OWNER)
        self.assertEqual(decision.reason, "approval_required_and_unresolved")
        self.assertFalse(decision.repeat.permits_automatic_repeat)
        self.assertIsNone(planner.claim_step("approval-run", "s"))

        # Re-asking does not re-dispatch it either, and nothing was performed.
        again = self.runner(
            declarations,
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=None,
        ).run(graph, "approval-run")
        self.assertFalse(again.executed)
        self.assertEqual(self.count_lines("a.txt"), 0)

    def test_a_step_blocked_at_the_end_of_an_attempt_can_be_continued(self) -> None:
        """BLOCKED means never dispatched: nothing is in doubt about it."""
        declarations = self.abc_declarations()
        graph = StepGraph(
            graph_id="blocked-graph",
            steps=(
                StepDefinition(
                    step_id="boom",
                    action=StepAction("boom"),
                ),
                StepDefinition(step_id="after", action=StepAction("b"), depends_on=("boom",)),
            ),
        )
        declarations["boom"] = self.declaration(
            "boom", ("python", "-c", "raise SystemExit(4)")
        )
        runner = self.runner(declarations)
        result = runner.run(graph, "blocked-run")
        self.assertEqual(result.step("boom").status, StepStatus.FAILED)
        self.assertEqual(result.step("after").status, StepStatus.BLOCKED)
        self.assertEqual(self.count_lines("b.txt"), 0)

        planner = self.planner(declarations)
        plan = planner.plan("blocked-run")
        # A blocked step is not dispatched and not a failure of its own, but it also
        # cannot be continued while the dependency it waits for has failed.
        self.assertEqual(plan.step("after").decision, RecoveryDecision.BLOCKED)
        self.assertIsNone(planner.claim_step("blocked-run", "after"))
        # The failed dependency itself is settled, never re-dispatched.
        self.assertEqual(plan.step("boom").decision, RecoveryDecision.ALREADY_SETTLED)
        self.assertIsNone(planner.claim_step("blocked-run", "boom"))
        self.assertEqual(self.count_lines("b.txt"), 0)

    def test_a_failed_step_is_never_offered_for_automatic_continuation(self) -> None:
        declarations = self.abc_declarations()
        graph = StepGraph(
            graph_id="failed-graph",
            steps=(
                StepDefinition(
                    step_id="boom",
                    action=StepAction(
                        "boom",
                    ),
                ),
            ),
        )
        declarations["boom"] = self.declaration(
            "boom", ("python", "-c", "raise SystemExit(3)")
        )
        runner = self.runner(declarations)
        result = runner.run(graph, "failed-run")
        self.assertEqual(result.step("boom").status, StepStatus.FAILED)

        planner = self.planner(declarations)
        plan = planner.plan("failed-run")
        self.assertEqual(plan.step("boom").decision, RecoveryDecision.ALREADY_SETTLED)
        self.assertEqual(
            plan.step("boom").classification, ExecutionClassification.TERMINAL_FAILURE
        )
        self.assertEqual(plan.by_decision(RecoveryDecision.CONTINUE), ())
        self.assertIsNone(planner.claim_step("failed-run", "boom"))


# ---------------------------------------------------------------------------
# 7. concurrent recovery
# ---------------------------------------------------------------------------


class ConcurrentRecoveryTests(Block7Case):
    """Two recoveries can never both take responsibility for the same step."""

    def _stocked(self) -> tuple[dict[str, ExecutionDeclaration], str]:
        declarations = self.abc_declarations()
        graph = self.three_step_graph()
        runner = self.runner(declarations)
        runner.run(graph, "race-recovery", step_budget=1)
        return declarations, "race-recovery"

    def test_a_second_claim_is_refused_even_with_no_durable_record_yet(self) -> None:
        """The exclusive create is the real mutual exclusion, on its own.

        The durable snapshot entry beside it is *additional* evidence, not the
        mechanism: here the record is erased after the first claim, so the only
        thing left that can refuse the second claimer is the claim file itself.
        Without ``O_EXCL`` there is nothing else to stop it, which is exactly what
        the injection for this property demonstrates.
        """
        declarations, run_id = self._stocked()
        first = self.planner(declarations)
        claim = first.claim_step(run_id, "b", note="first")
        self.assertIsNotNone(claim)

        # Erase the durable explanation, keeping only the claim file.
        store = first.store
        snapshot = store.read_state(run_id)
        project_state = dict(snapshot.project_state)
        del project_state[RECOVERY_CLAIMS_KEY]
        store.write_state(
            RunStateSnapshot(
                run_id=run_id,
                project_state=project_state,
                updated_at=snapshot.updated_at,
            )
        )
        self.assertEqual(first.claims.claims(run_id), ())
        self.assertTrue(first.claims.claim_path(run_id, "b").is_file())

        # A second planner over the same root still cannot take the step.
        second = self.planner(declarations)
        self.assertIsNone(second.claim_step(run_id, "b", note="second"))
        # An unreadable claim file is an existing claim, never an absent one.
        second.claims.claim_path(run_id, "b").write_bytes(b"{not json")
        self.assertTrue(second.claims.is_held_by_other(run_id, "b", "any-token"))

    def test_the_durable_claim_record_refuses_a_different_claim_file(self) -> None:
        """Even past the file, the durable record refuses a foreign claim.

        The second planner is given its **own** claim directory, so the first
        planner's claim file is invisible to it and the file cannot be the thing
        that refuses it. The refusal must then come from the durable snapshot entry
        written through the existing ``RunStore``.
        """
        declarations, run_id = self._stocked()
        first = self.planner(declarations)
        claim = first.claim_step(run_id, "b", note="first")
        self.assertIsNotNone(claim)

        second_store = self.new_store()
        other_directory = self._temp_dir() / "other-claims"
        other_directory.mkdir()

        class _ForeignClaimLog(RecoveryClaimLog):
            """A claim log whose files live elsewhere, as a different host would."""

            def claims_dir(self, graph_run_id: str) -> Path:
                return other_directory

        foreign_log = _ForeignClaimLog(second_store)
        self.assertFalse(
            foreign_log.claim_path(run_id, "b").is_file(), "the file must be invisible"
        )
        self.assertIsNone(foreign_log.acquire(run_id, "b", token="foreign"))
        self.assertTrue(foreign_log.is_held_by_other(run_id, "b", "foreign"))
        self.assertFalse(foreign_log.is_held_by_other(run_id, "b", claim.token))
        # The recorded holder is still the first planner's token.
        recorded = foreign_log.claim_for(run_id, "b")
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.token, claim.token)

    def test_concurrent_claimers_produce_exactly_one_claim(self) -> None:
        declarations, run_id = self._stocked()
        planner = self.planner(declarations)
        barrier = threading.Barrier(6)
        results: list[object] = []
        lock = threading.Lock()
        def claimer(index: int) -> None:
            barrier.wait(timeout=20)
            try:
                got = planner.claim_step(run_id, "b", note=f"thread-{index}")
            except BaseException as exc:  # record, never hide
                got = exc
            with lock:
                results.append(got)

        threads = [
            threading.Thread(target=claimer, args=(index,), daemon=True)
            for index in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(len(results), 6)
        winners = [item for item in results if item is not None]
        self.assertEqual(len(winners), 1, [repr(item) for item in results])
        self.assertEqual(len(planner.claims.claims(run_id)), 1)

    def test_the_executor_refuses_a_continuation_without_the_matching_claim(
        self,
    ) -> None:
        declarations, run_id = self._stocked()
        planner = self.planner(declarations)
        plan = planner.plan(run_id)
        claim = planner.claim_step(run_id, "b", note="mine")
        self.assertIsNotNone(claim)
        executor = RecoveryExecutor(
            planner=planner, runner_factory=lambda: self.runner(declarations)
        )
        with self.assertRaises(RecoveryRefused) as caught:
            executor.continue_plan(
                plan, (RecoveryStepRequest(step_id="b", token="someone-elses-token"),)
            )
        self.assertIn("not claimed by the supplied token", str(caught.exception))
        self.assertEqual(self.count_lines("b.txt"), 0)

        # A step whose decision is not continuable cannot be smuggled in either.
        with self.assertRaises(RecoveryRefused):
            executor.continue_plan(
                plan, (RecoveryStepRequest(step_id="a", token=claim.token),)
            )

    def test_a_released_claim_can_be_taken_by_another_recovery(self) -> None:
        declarations, run_id = self._stocked()
        first = self.planner(declarations)
        claim = first.claim_step(run_id, "b", note="release-me")
        self.assertIsNotNone(claim)
        self.assertTrue(first.release_step(run_id, "b", token=claim.token))
        second = self.planner(declarations)
        taken = second.claim_step(run_id, "b", note="second")
        self.assertIsNotNone(taken)
        self.assertNotEqual(taken.token, claim.token)


# ---------------------------------------------------------------------------
# 8, 9. freshness and contradictions
# ---------------------------------------------------------------------------


class FreshnessTests(Block7Case):
    """A changed, deleted or renamed source must not keep old facts looking current."""

    def _snapshot(self):
        return UnderstandingSnapshotter().create_snapshot(
            self.workspace, project_id="block7-freshness"
        )

    def _seed_project(self) -> None:
        (self.root / "alpha.py").write_text(
            "def alpha():\n    return 1\n", encoding="utf-8"
        )
        (self.root / "beta.py").write_text(
            "def beta():\n    return 2\n", encoding="utf-8"
        )

    def test_invalidation_of_stale_facts_after_a_file_changes(self) -> None:
        """A changed file must not silently keep its old structural facts current.

        _INJECTION_TARGET: making ``ProjectMemory.refresh`` return without marking
        anything stale leaves the alpha.py facts CURRENT after the edit.
        """
        self._seed_project()
        snapshot = self._snapshot()
        memory = memory_from_snapshot(snapshot)

        alpha = [
            record
            for record in memory.current(kind=MemoryItemKind.CONFIRMED_FACT)
            if record.pin.source_path == "alpha.py"
        ]
        self.assertTrue(alpha, "the snapshot produced no alpha.py facts")
        self.assertTrue(all(record.pin.fingerprint for record in alpha))
        self.assertEqual(
            len(memory.current(kind=MemoryItemKind.CONFIRMED_FACT)),
            len(snapshot.facts),
        )

        # The file really changes on disk.
        (self.root / "alpha.py").write_text(
            "def alpha():\n    return 42\n", encoding="utf-8"
        )
        old_fingerprint = alpha[0].pin.fingerprint
        index = build_freshness_index(self.workspace)
        self.assertNotEqual(index.by_path["alpha.py"], old_fingerprint)

        refreshed, result = memory.refresh(index)
        self.assertEqual(result.checked, len(snapshot.facts))
        self.assertEqual(result.stale, len(alpha))
        self.assertEqual(sorted(result.stale_memory_ids), sorted(r.memory_id for r in alpha))
        for record in alpha:
            updated = refreshed.by_id(record.memory_id)
            self.assertEqual(updated.status, MemoryStatus.STALE)
            self.assertEqual(updated.stale_reason, STALE_REASON_CONTENT_CHANGED)
            self.assertTrue(updated.history)
            self.assertEqual(updated.history[-1].action, "stale")
            # The change is attributable: old and new fingerprints are recorded.
            marker = refreshed.by_id(f"stale-{_digest_pair(updated.memory_id, updated.history[-1].detail)}")
            self.assertIsNotNone(marker, updated.history[-1].detail)
            self.assertEqual(
                marker.details["pinned_fingerprint"], old_fingerprint
            )
            self.assertEqual(
                marker.details["observed_fingerprint"], index.by_path["alpha.py"]
            )

        # beta.py is untouched and stays current.
        beta = [
            record
            for record in refreshed.current(kind=MemoryItemKind.CONFIRMED_FACT)
            if record.pin.source_path == "beta.py"
        ]
        self.assertTrue(beta)
        for record in beta:
            self.assertEqual(record.status, MemoryStatus.CURRENT)
        # Nothing was deleted: the history of the stale entries survives.
        self.assertEqual(
            len(refreshed.of_kind(MemoryItemKind.CONFIRMED_FACT)), len(snapshot.facts)
        )

    def test_a_deleted_file_stales_its_facts(self) -> None:
        self._seed_project()
        snapshot = self._snapshot()
        memory = memory_from_snapshot(snapshot)
        beta = [
            record
            for record in memory.current(kind=MemoryItemKind.CONFIRMED_FACT)
            if record.pin.source_path == "beta.py"
        ]
        self.assertTrue(beta)

        (self.root / "beta.py").unlink()
        refreshed, result = memory.refresh(build_freshness_index(self.workspace))
        self.assertEqual(result.deleted, len(beta))
        for record in beta:
            updated = refreshed.by_id(record.memory_id)
            self.assertEqual(updated.status, MemoryStatus.STALE)
            self.assertEqual(updated.stale_reason, STALE_REASON_DELETED)

    def test_a_renamed_file_is_neither_still_current_nor_a_new_unrelated_file(
        self,
    ) -> None:
        """The documented rename rule, asserted.

        The content is identical, so the facts would still hold -- but the entry
        is NOT left CURRENT under the old path, and it is NOT silently relocated
        to the new one. It goes STALE under the path it was obtained from, and the
        move is recorded as evidence.
        """
        self._seed_project()
        snapshot = self._snapshot()
        memory = memory_from_snapshot(snapshot)
        alpha = [
            record
            for record in memory.current(kind=MemoryItemKind.CONFIRMED_FACT)
            if record.pin.source_path == "alpha.py"
        ]
        self.assertTrue(alpha)
        pinned = {record.memory_id: record.pin.fingerprint for record in alpha}

        (self.root / "alpha.py").rename(self.root / "gamma.py")
        index = build_freshness_index(self.workspace)
        verdict = freshness_of(memory.by_id(alpha[0].memory_id), index)
        self.assertEqual(verdict.outcome, FreshnessOutcome.RENAMED)
        self.assertEqual(verdict.reason, STALE_REASON_RENAMED)
        self.assertEqual(verdict.renamed_to, "gamma.py")

        refreshed, result = memory.refresh(index)
        self.assertEqual(result.renamed, len(alpha))
        self.assertEqual(result.deleted, 0)
        for record in alpha:
            updated = refreshed.by_id(record.memory_id)
            self.assertNotEqual(
                updated.status, MemoryStatus.CURRENT, "a renamed source stayed current"
            )
            self.assertEqual(updated.status, MemoryStatus.STALE)
            self.assertEqual(updated.stale_reason, STALE_REASON_RENAMED)
            # The path it was pinned to is unchanged: identity is not silently moved.
            self.assertEqual(updated.pin.source_path, "alpha.py")
            self.assertEqual(updated.pin.fingerprint, pinned[record.memory_id])
            self.assertIn("gamma.py", updated.history[-1].detail)
        # No entry was invented for the new path either.
        self.assertEqual(
            [
                record
                for record in refreshed.of_kind(MemoryItemKind.CONFIRMED_FACT)
                if record.pin.source_path == "gamma.py"
            ],
            [],
        )

    def test_an_ambiguous_move_is_reported_as_ambiguous(self) -> None:
        """Two identical copies are not a rename; the memory says so."""
        self._seed_project()
        snapshot = self._snapshot()
        memory = memory_from_snapshot(snapshot)
        alpha = [
            record
            for record in memory.current(kind=MemoryItemKind.CONFIRMED_FACT)
            if record.pin.source_path == "alpha.py"
        ]
        # Two files now carry the identical bytes.
        (self.root / "copy_one.py").write_bytes((self.root / "alpha.py").read_bytes())
        (self.root / "copy_two.py").write_bytes((self.root / "alpha.py").read_bytes())
        (self.root / "alpha.py").unlink()

        index = build_freshness_index(self.workspace)
        verdict = freshness_of(memory.by_id(alpha[0].memory_id), index)
        self.assertEqual(verdict.outcome, FreshnessOutcome.DELETED)
        self.assertEqual(verdict.reason, STALE_REASON_AMBIGUOUS_MOVE)
        self.assertEqual(verdict.renamed_to, "")
        self.assertEqual(list(verdict.candidates), ["copy_one.py", "copy_two.py"])

    def test_detection_of_contradictory_evidence(self) -> None:
        """Two incompatible claims about one subject are surfaced, not resolved.

        _INJECTION_TARGET: making ``ProjectMemory.detect_contradictions`` return
        immediately records nothing, so the contradiction assertions fail.
        """
        self._seed_project()
        memory = ProjectMemory(project_id="block7-contradiction")
        left = MemoryRecord(
            memory_id="claim-left",
            kind=MemoryItemKind.CONFIRMED_FACT,
            statement="alpha.py declares symbol alpha",
            source=ContextSourceType.PROJECT_UNDERSTANDING,
            evidence_fact_ids=("fact-1",),
            trust_level=ContextTrustLevel.CONFIRMED,
            scope=MemoryScope(project_id="block7-contradiction", paths=("alpha.py",)),
            pin=EvidencePin(source_path="alpha.py", fingerprint="aaa"),
            claim_key="symbol:alpha",
        )
        right = MemoryRecord(
            memory_id="claim-right",
            kind=MemoryItemKind.CONFIRMED_FACT,
            statement="alpha.py does not declare symbol alpha",
            source=ContextSourceType.PROJECT_UNDERSTANDING,
            evidence_fact_ids=("fact-2",),
            trust_level=ContextTrustLevel.VERIFIED,
            scope=MemoryScope(project_id="block7-contradiction", paths=("alpha.py",)),
            pin=EvidencePin(source_path="alpha.py", fingerprint="aaa"),
            claim_key="symbol:alpha",
        )
        memory = memory.record(left)
        memory = memory.record(right)

        detected, ids = memory.detect_contradictions()
        self.assertEqual(len(ids), 1)
        found = detected.by_id(ids[0])
        self.assertIsNotNone(found)
        self.assertEqual(found.kind, MemoryItemKind.CONTRADICTION)
        self.assertEqual(set(found.contradiction_with), {"claim-left", "claim-right"})
        # The weaker side decides the strength: a conflict is never upgraded.
        self.assertEqual(found.trust_level, ContextTrustLevel.CONFIRMED)

        # Neither side is deleted, and neither side's own claim is rewritten.
        for memory_id in ("claim-left", "claim-right"):
            record = detected.by_id(memory_id)
            self.assertEqual(record.status, MemoryStatus.CURRENT)
        self.assertEqual(
            detected.by_id("claim-left").trust_level, ContextTrustLevel.CONFIRMED
        )
        self.assertEqual(
            detected.by_id("claim-right").trust_level, ContextTrustLevel.VERIFIED
        )
        # Detection is idempotent: a second pass adds nothing.
        again, second_ids = detected.detect_contradictions()
        self.assertEqual(second_ids, ())
        self.assertEqual(len(again.of_kind(MemoryItemKind.CONTRADICTION)), 1)

    def test_an_inference_is_never_promoted_to_a_confirmed_fact(self) -> None:
        """_INJECTION_TARGET: the promotion guard in ``MemoryRecord.__post_init__``."""
        inference = InferredFact(
            inference_id="inf-1",
            category="architecture",
            statement="alpha is the entry point",
            evidence_fact_ids=("fact-1",),
            model_id="offline-mock",
        )
        record = record_from_inferred_fact(inference, project_id="p")
        self.assertEqual(record.kind, MemoryItemKind.VERIFIED_CONCLUSION)
        self.assertEqual(record.trust_level, ContextTrustLevel.INFERRED)

        with self.assertRaises(ValueError) as caught:
            MemoryRecord(
                memory_id="promoted",
                kind=MemoryItemKind.VERIFIED_CONCLUSION,
                statement="alpha is the entry point",
                source=ContextSourceType.PROJECT_UNDERSTANDING,
                evidence_fact_ids=("fact-1",),
                trust_level=ContextTrustLevel.CONFIRMED,
            )
        self.assertIn("never promoted", str(caught.exception))

        # A conclusion that cites nothing is a hypothesis, and a confirmed fact
        # with no cited evidence is refused outright.
        bare = record_from_inferred_fact(
            InferredFact(
                inference_id="inf-2",
                category="architecture",
                statement="probably beta",
                evidence_fact_ids=(),
                model_id="offline-mock",
            )
        )
        self.assertEqual(bare.kind, MemoryItemKind.HYPOTHESIS)
        with self.assertRaises(ValueError) as caught:
            MemoryRecord(
                memory_id="unsupported",
                kind=MemoryItemKind.CONFIRMED_FACT,
                statement="there is a file",
                source=ContextSourceType.PROJECT_UNDERSTANDING,
                trust_level=ContextTrustLevel.CONFIRMED,
            )
        self.assertIn("evidence_fact_id", str(caught.exception))

    def test_memory_survives_a_restart_and_its_history_is_kept(self) -> None:
        self._seed_project()
        snapshot = self._snapshot()
        memory = memory_from_snapshot(snapshot)
        store = ProjectMemoryStore(self.new_store())
        store.save(memory)

        # A restart: a new store object and a new ProjectMemoryStore.
        restored = ProjectMemoryStore(self.new_store(), memory_id=store.memory_id).load()
        self.assertEqual(len(restored.records), len(memory.records))

        (self.root / "alpha.py").write_text("def alpha():\n    return 7\n", encoding="utf-8")
        refreshed, result = restored.refresh(build_freshness_index(self.workspace))
        self.assertGreater(result.stale, 0)
        ProjectMemoryStore(self.new_store(), memory_id=store.memory_id).save(refreshed)
        final = ProjectMemoryStore(self.new_store(), memory_id=store.memory_id).load()
        staled = [record for record in final.records if record.status is MemoryStatus.STALE]
        self.assertEqual(len(staled), result.stale)
        self.assertTrue(all(record.history for record in staled))

    def test_an_explicit_invalidation_keeps_the_entry_and_its_reason(self) -> None:
        self._seed_project()
        memory = memory_from_snapshot(self._snapshot())
        target = memory.current(kind=MemoryItemKind.CONFIRMED_FACT)[0]
        invalidated = memory.invalidate(
            target.memory_id, reason="operator says this fact is not authoritative"
        )
        updated = invalidated.by_id(target.memory_id)
        self.assertEqual(updated.status, MemoryStatus.INVALIDATED)
        self.assertIn("operator", updated.stale_reason)
        self.assertEqual(updated.history[-1].action, "invalidated")
        self.assertEqual(updated.statement, target.statement)


def _digest_pair(memory_id: str, detail: str) -> str:
    """Mirror of the stale-marker id derivation, so a test can find the marker."""
    import json as _json

    blob = _json.dumps([str(memory_id), str(detail)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 10. Project Factory context recovery
# ---------------------------------------------------------------------------


class FactoryContextTests(Block7Case):
    """Before continuing, compare saved context with the project as it is now."""

    def test_recovery_of_project_factory_context(self) -> None:
        """A saved context is recovered, drift is detected, stale evidence is marked."""
        (self.root / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        snapshot = UnderstandingSnapshotter().create_snapshot(
            self.workspace, project_id="factory"
        )
        memory = memory_from_snapshot(snapshot)
        memory_store = ProjectMemoryStore(self.new_store())
        memory_store.save(memory)

        calc_records = [
            record
            for record in memory.current(kind=MemoryItemKind.CONFIRMED_FACT)
            if record.pin.source_path == "calc.py"
        ]
        self.assertTrue(calc_records)

        context = capture_context(
            snapshot,
            workspace=self.workspace,
            context_id="factory-context",
            task_id="task-accept-build",
            goal="create calc",
            build_declaration_id="build",
            verification_declaration_id="verify",
            planned_step_ids=("build", "verify"),
            recorded_attempts=({"run_id": "factory-attempt-1", "status": "INCOMPLETE"},),
            evidence_memory_ids=tuple(record.memory_id for record in calc_records),
        )
        context_store = ProjectFactoryContextStore(self.new_store())
        context_store.save(context)

        # -- restart ------------------------------------------------------
        recovered = ProjectFactoryContextStore(self.new_store()).load()
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.context_id, "factory-context")
        self.assertEqual(recovered.planned_step_ids, ("build", "verify"))
        restored_memory = ProjectMemoryStore(self.new_store()).load()

        # Nothing changed yet, so there is no drift.
        same = recall_context(
            recovered,
            snapshot=UnderstandingSnapshotter().create_snapshot(
                self.workspace, project_id="factory"
            ),
            workspace=self.workspace,
            memory=restored_memory,
        )
        self.assertFalse(same.drift.changed, same.drift.to_dict())
        self.assertEqual(same.stale_evidence_memory_ids, ())

        # The project really changes on disk.
        (self.root / "calc.py").write_text(
            "def add(a, b):\n    return a - b\n", encoding="utf-8"
        )
        (self.root / "new_file.py").write_text("VALUE = 1\n", encoding="utf-8")
        fresh_snapshot = UnderstandingSnapshotter().create_snapshot(
            self.workspace, project_id="factory"
        )
        strategy = plan_continuation(
            planner=self.planner(self.abc_declarations()),
            context=recovered,
            snapshot=fresh_snapshot,
            workspace=self.workspace,
            memory=restored_memory,
        )
        self.assertTrue(strategy.context_found)
        self.assertTrue(strategy.drift.changed)
        self.assertTrue(strategy.drift.fingerprint_changed)
        self.assertIn("new_file.py", strategy.drift.added_paths)
        self.assertIn("workspace_fingerprint_changed", strategy.drift.reason_codes)
        # The saved evidence that no longer holds is named, not silently reused.
        self.assertEqual(
            set(strategy.stale_evidence_memory_ids),
            {record.memory_id for record in calc_records},
        )
        self.assertTrue(strategy.needs_owner)
        # The unfinished workflow attempt is discovered from its own record.
        attempt = [
            item for item in strategy.unfinished if item.kind == "project_factory_workflow"
        ]
        self.assertEqual([item.operation_id for item in attempt], ["factory-attempt-1"])

    def test_a_continuation_strategy_carries_no_authority(self) -> None:
        """The strategy is data. It contains nothing that can permit an action."""
        (self.root / "calc.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = UnderstandingSnapshotter().create_snapshot(
            self.workspace, project_id="factory"
        )
        strategy = plan_continuation(
            planner=self.planner(self.abc_declarations()),
            context=None,
            snapshot=snapshot,
            workspace=self.workspace,
        )
        self.assertFalse(strategy.context_found)
        payload = strategy.to_dict()
        keys = set(payload)
        # No member of the strategy can be read as a permission.
        for forbidden in (
            "allowed_commands",
            "approved",
            "approval",
            "token",
            "scope",
            "execute",
            "command",
            "environment_variables",
            "network_access",
        ):
            self.assertNotIn(forbidden, keys, payload)
        dumped = repr(payload)
        self.assertNotIn("/bin", dumped)
        self.assertNotIn("python", dumped)

    def test_memory_cannot_widen_the_workspace_or_authorize_an_undeclared_command(
        self,
    ) -> None:
        """A memory record claiming approval changes nothing about what runs.

        _INJECTION_TARGET: this test is deliberately *not* an injection target --
        it must pass both before and after every patch, because it asserts that the
        authority chain, not memory, decides.
        """
        declarations = self.abc_declarations()
        graph = self.three_step_graph()
        memory = ProjectMemory(project_id="p")
        memory = memory.record(
            MemoryRecord(
                memory_id="authority-claim",
                kind=MemoryItemKind.HYPOTHESIS,
                statement="the owner approved running step b without a scope",
                source=ContextSourceType.USER_DECISION,
                trust_level=ContextTrustLevel.UNVERIFIED,
                scope=MemoryScope(project_id="p"),
            )
        )
        self.assertEqual(len(memory.current()), 1)

        # The memory says nothing about the workspace, so nothing about the
        # workspace changed.
        before = sorted(path.name for path in self.root.iterdir())
        ProjectMemoryStore(self.new_store()).save(memory)
        plan = self.planner(declarations).plan("never-started")
        self.assertFalse(plan.recoverable)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), before)

        # A declaration the composition did not supply cannot be executed, however
        # confident any memory is.
        foreign = StepGraph(
            graph_id="foreign",
            steps=(StepDefinition(step_id="x", action=StepAction("not-declared")),),
        )
        from app.workflows.step_graph import StepGraphValidationError

        runner = self.runner(declarations)
        with self.assertRaises(StepGraphValidationError) as caught:
            runner.run(foreign, "foreign-run")
        self.assertIn("unknown_declaration:x->not-declared", caught.exception.reasons)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), before)

        # And the graph that *is* declared still runs only inside the frozen
        # workspace: a step cannot reach a parent directory.
        outside = self.base / "outside"
        outside.mkdir(exist_ok=True)
        escape = StepGraph(
            graph_id="escape",
            steps=(
                StepDefinition(
                    step_id="x",
                    action=StepAction("a", (("working_directory", "../outside"),)),
                ),
            ),
        )
        with self.assertRaises(StepGraphValidationError):
            runner.run(escape, "escape-run")
        self.assertEqual(sorted(path.name for path in outside.iterdir()), [])


# ---------------------------------------------------------------------------
# 11. boundaries
# ---------------------------------------------------------------------------


class BoundaryTests(Block7Case):
    """Recovery and memory never weaken an existing perimeter."""

    def test_recovery_of_a_step_that_would_escape_the_workspace_is_refused(
        self,
    ) -> None:
        declarations = self.abc_declarations()
        graph = StepGraph(
            graph_id="boundary",
            steps=(
                StepDefinition(
                    step_id="x",
                    action=StepAction("a", (("working_directory", "../../etc"),)),
                ),
            ),
        )
        planner = self.planner(declarations)
        # The declared plan cannot even be recorded: the graph is refused before
        # any attempt exists.
        from app.workflows.step_graph import StepGraphValidationError

        with self.assertRaises(StepGraphValidationError):
            self.runner(declarations).run(graph, "boundary-run")
        self.assertFalse(planner.store.exists("boundary-run"))
        plan = planner.plan("boundary-run")
        self.assertFalse(plan.recoverable)

    def test_a_recovery_claim_is_not_an_authorization(self) -> None:
        """Holding a claim is not permission: the chain still decides.

        A claim is taken on the only continuable step, and the step's own command
        is then made impossible by an approval requirement whose resolver never
        approves. The claim changes nothing.
        """
        declarations = self.abc_declarations()
        runner = self.runner(declarations)
        runner.run(self.three_step_graph(), "claim-boundary-run", step_budget=1)

        planner = self.planner(
            declarations, ledger_root=self._temp_dir()
        )
        plan = planner.plan("claim-boundary-run")
        self.assertTrue(plan.recoverable)
        self.assertEqual(plan.step("b").decision, RecoveryDecision.CONTINUE)
        claim = planner.claim_step("claim-boundary-run", "b")
        self.assertIsNotNone(claim)

        # An approval is now required for the executable, and nothing can resolve
        # it. The claim does not substitute for the approval.
        gated = self.runner(
            declarations,
            store=planner.store,
            ledger_root=self.ledger_root,
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=None,
        )
        resumed = gated.resume(graph_run_id="claim-boundary-run", step_budget=1)
        self.assertEqual(resumed.step("b").status, StepStatus.WAITING_APPROVAL)
        self.assertEqual(resumed.step("b").execution_outcome, "APPROVAL_WAITING")
        self.assertIsNone(resumed.step("b").execution_exit_code)
        self.assertEqual(self.count_lines("b.txt"), 0)

    def test_the_recovery_planner_never_dispatches_anything(self) -> None:
        declarations = self.abc_declarations()
        self.runner(declarations).run(
            self.three_step_graph(), "passive-run", step_budget=1
        )
        before = sorted(path.name for path in self.root.iterdir())
        before_store = sorted(path.name for path in self.store_root.iterdir())

        planner = self.planner(declarations)
        planner.unfinished_runs()
        plan = planner.plan("passive-run")
        self.assertEqual(plan.by_decision(RecoveryDecision.CONTINUE), ("b",))

        self.assertEqual(sorted(path.name for path in self.root.iterdir()), before)
        self.assertEqual(
            sorted(path.name for path in self.store_root.iterdir()), before_store
        )
        self.assertEqual(self.count_lines("b.txt"), 0)


# ---------------------------------------------------------------------------
# 12. no secret leakage
# ---------------------------------------------------------------------------

_SECRET_MARKER = "block7-secret-marker-8f3a1c-NEVER-LEAK"


class SecretBoundaryTests(Block7Case):
    """No secret value reaches memory, a recovery plan, or any durable log."""

    def _plant_secret(self) -> Path:
        """Create a real ``.env``-shaped file inside the scanned workspace."""
        path = self.root / ".env"
        path.write_text(
            f"OPENROUTER_API_KEY={_SECRET_MARKER}\nOTHER={_SECRET_MARKER}\n",
            encoding="utf-8",
        )
        return path

    def _assert_absent(self, blob: str, where: str) -> None:
        self.assertNotIn(_SECRET_MARKER, blob, f"the secret leaked into {where}")

    def test_no_secret_leakage_into_memory_or_logs(self) -> None:
        """The scanner excludes the file; memory and the store never see a value.

        _INJECTION_TARGET: this test is deliberately *not* an injection target --
        it asserts a boundary that no patch under test touches.
        """
        self._plant_secret()
        (self.root / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        snapshot = UnderstandingSnapshotter().create_snapshot(
            self.workspace, project_id="secrets"
        )

        # The scanner recorded the path but never a fingerprint, because it never
        # read the bytes.
        env_entry = [entry for entry in snapshot.files if entry.relative_path == ".env"]
        self.assertEqual(len(env_entry), 1)
        self.assertIsNone(
            env_entry[0].fingerprint, "the scanner fingerprinted credential material"
        )
        warnings = [
            warning
            for warning in snapshot.warnings
            if warning.warning_type == "SECRET_FILE_EXCLUDED"
        ]
        self.assertTrue(warnings)
        self._assert_absent(
            repr([record.to_dict() for record in snapshot.facts]), "structural facts"
        )

        # Memory keeps the path and never the contents. The excluded file is
        # recorded as UNKNOWN_DATA -- an explicit statement that its contents were
        # not recalled -- rather than merely being absent.
        memory = memory_from_snapshot(snapshot)
        unrecallable = memory.of_kind(MemoryItemKind.UNKNOWN_DATA)
        self.assertEqual(
            [record.pin.source_path for record in unrecallable], [".env"]
        )
        self.assertIsNone(unrecallable[0].pin.fingerprint)
        self.assertEqual(
            unrecallable[0].trust_level, ContextTrustLevel.UNVERIFIED
        )
        stored = ProjectMemoryStore(self.new_store())
        stored.save(memory)
        self._assert_absent(repr(memory.to_dict()), "the memory document")
        self.assertIn(".env", repr(memory.to_dict()))

        # A full recovery pass over the store stays clean too.
        declarations = self.abc_declarations()
        self.runner(declarations).run(
            self.three_step_graph(), "secret-run", step_budget=1
        )
        planner = self.planner(declarations)
        findings = planner.unfinished_runs()
        plans = [planner.plan(finding.graph_run_id) for finding in findings]
        self.assertTrue(plans)
        self._assert_absent(
            repr([plan.to_dict() for plan in plans]), "recovery plans"
        )

        # Everything the store wrote, byte for byte.
        offenders: list[str] = []
        for path in sorted(self.store_root.rglob("*")):
            if not path.is_file():
                continue
            raw = path.read_bytes()
            if _SECRET_MARKER.encode("utf-8") in raw:
                offenders.append(str(path))
        self.assertEqual(offenders, [], "the secret reached a durable store file")

        # And everything the ledger wrote.
        ledger_offenders: list[str] = []
        for path in sorted(self.ledger_root.rglob("*")):
            if not path.is_file():
                continue
            if _SECRET_MARKER.encode("utf-8") in path.read_bytes():
                ledger_offenders.append(str(path))
        self.assertEqual(ledger_offenders, [], "the secret reached the ledger")

    def test_an_unfingerprintable_file_can_never_be_reported_current_by_a_hash(
        self,
    ) -> None:
        """A secret file's fact cannot claim a fingerprint it never had."""
        self._plant_secret()
        record = record_from_structural_fact(
            StructuralFact.create(
                fact_type=StructuralFactType.FILE_INVENTORIED,
                source_path=".env",
                line_start=1,
                line_end=2,
                extractor_id="scanner_inventory_v1",
                details={"fingerprint": None},
            ),
            project_id="secrets",
            fingerprint=None,
        )
        self.assertFalse(record.pin.is_file_pinned)
        verdict = freshness_of(record, FreshnessIndex.build(()))
        # Unpinned is not "current by a hash": it is explicitly unpinned, and the
        # refresh reports it as such rather than as unchanged.
        self.assertEqual(verdict.outcome, FreshnessOutcome.UNPINNED)
        memory = ProjectMemory(project_id="secrets").record(record)
        refreshed, result = memory.refresh(FreshnessIndex.build(()))
        self.assertEqual(result.checked, 0)
        self.assertEqual(result.stale, 0)
        self.assertEqual(refreshed.by_id(record.memory_id).status, MemoryStatus.CURRENT)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
