"""Managed multi-step execution v0.1, verified from outside.

WHAT THIS FILE PROVES, AND HOW
==============================

Every claim is proved by **observed data**: a real child process's exit code, a
file that does or does not exist on disk, a durable record read back from an
isolated ``RunStore``, or a decision the *existing* idempotency engine returned.
Nothing here patches a private method, replaces an internal collaborator, or
asserts that a class or a string exists.

The twelve scenarios the owner listed are each covered by a named test:

1. a successful chain of several dependent steps -- ``ChainTests`` (the second
   step's command can only succeed if the first step's process really ran);
2. failure of an intermediate step -- ``ChainTests``;
3. dependent steps blocked -- ``ChainTests``;
4. cyclic graph refused -- ``GraphValidationTests``;
5. cancellation before start and during execution -- ``CancellationTests``;
6. no authorization bypass -- ``AuthorityBoundaryTests``;
7. parameter mismatch against a previously approved action --
   ``AuthorityBoundaryTests``;
8. verification failure while the command itself succeeded --
   ``VerificationTests``;
9. recovery after restart -- ``RecoveryTests``;
10. indeterminate result for an action that may already have run --
    ``RecoveryTests``;
11. repeated request to start the same step -- ``RepeatRequestTests``;
12. workspace isolation with user changes preserved --
    ``WorkspaceIsolationTests``.

ISOLATION
=========

Every test builds its own ``RunStore`` and its own ``RunIdempotencyGuard`` under
temporary directories, and its own workspace, because the default store and
ledger roots are shared per-user and have produced FALSE failures in this
repository before. ``RunScope.release_all()`` runs before and after every test.

NO REAL PROVIDER
================

``create_runtime(test_mode=True)`` is used everywhere, so the only registered
agent is the offline mock, and every step executes with
``generate_decision=False``: the model is never asked. No test contacts a
network, reads a key, or prints a secret value.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.agent_runtime import idempotency_integration as integration
from app.agent_runtime.idempotency import (
    IdempotencyFailureCode,
    IdempotencyVerdict,
)
from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.runtime.bootstrap import create_runtime
from app.runtime.run_scope import RunScope
from app.runtime.run_store import RunStateSnapshot, RunStore
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalState,
    InMemoryApprovalResolver,
)
from app.tools.workspace import Workspace
from app.workflows.step_graph import (
    REASON_CYCLE,
    REASON_DUPLICATE_STEP_ID,
    REASON_INVALID_PARAMETER_VALUE,
    REASON_SELF_DEPENDENCY,
    REASON_UNKNOWN_DECLARATION,
    REASON_UNKNOWN_DEPENDENCY,
    StepAction,
    StepDefinition,
    StepGraph,
    StepGraphSnapshot,
    StepGraphStateError,
    StepGraphStatus,
    StepGraphValidationError,
    StepRecord,
    StepStatus,
    StepVerification,
    VerificationKind,
    VerificationStatus,
    declared_plan_of,
)
from app.workflows.step_runner import StepGraphRunner

# -- operator-declared programmes -------------------------------------------
#
# Each is a real program run by a real interpreter in a real child process. The
# read/append programmes are what make a dependency observable: a missing input
# file makes the child fail, so a succeeding step *proves* its predecessor ran.

WRITE_ONE = (
    "python",
    "-c",
    "open('one.txt','w',encoding='utf-8').write('1'); print('wrote one')",
)
READ_ONE = (
    "python",
    "-c",
    "print('read ' + open('one.txt',encoding='utf-8').read())",
)
WRITE_TWO = (
    "python",
    "-c",
    "open('two.txt','w',encoding='utf-8').write('2'); print('wrote two')",
)
EXIT_TWO = ("python", "-c", "raise SystemExit(2)")
APPEND_COUNT = (
    "python",
    "-c",
    "open('count.txt','a',encoding='utf-8').write('x')",
)


def append_programme(name: str) -> tuple[str, ...]:
    """A real command that appends one marker to ``<name>.txt``.

    Appending (not overwriting) is what makes a *repeat* observable: a second
    execution leaves two markers.
    """
    return (
        "python",
        "-c",
        f"open('{name}.txt','a',encoding='utf-8').write('x')",
    )


MAKE_ARTIFACT = (
    "python",
    "-c",
    "open('made.txt','w',encoding='utf-8').write('payload')",
)
MAKE_NOTHING = ("python", "-c", "print('no artifact here')")
PROFILE_ID = "step-graph-test-profile"


def _drain(directory: tempfile.TemporaryDirectory) -> None:
    try:
        directory.cleanup()
    except (PermissionError, OSError):
        pass


class StepGraphCase(unittest.TestCase):
    """One workspace, one run store, one idempotency ledger, no real provider."""

    def setUp(self) -> None:
        RunScope.release_all()
        self._dirs: list[tempfile.TemporaryDirectory] = []
        self.base = self._temp_dir()
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        self.run_store = RunStore(root=str(self._temp_dir()))
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
            intent=f"test declaration {declaration_id}",
        )

    def guard(self) -> integration.RunIdempotencyGuard:
        return integration.RunIdempotencyGuard.create(ledger_root=str(self.ledger_root))

    def runner(
        self,
        declarations: dict[str, ExecutionDeclaration],
        *,
        isolate_workspace: bool = False,
        approval_policy: ApprovalPolicy | None = None,
        approval_resolver: object | None = None,
        artifacts_are_harvested: bool = False,
        observer=None,
    ) -> StepGraphRunner:
        """Compose a runner with an explicitly chosen adapter, as an operator would.

        ``isolate_workspace`` is the adapter's own documented choice; this test
        exercises both settings and asserts the difference rather than assuming
        it. The coordinator is built here and handed in, because the runner never
        constructs an authority component of its own.
        """
        return StepGraphRunner(
            runtime=self.runtime,
            workspace=self.workspace,
            execution_profile=self.profile(),
            declarations=declarations,
            run_store=self.run_store,
            idempotency_guard=self.guard(),
            approval_policy=approval_policy or ApprovalPolicy(),
            execution_coordinator=ExecutionCoordinator(
                LocalExecutionAdapter(
                    workspace_root=self.root, isolate_workspace=isolate_workspace
                )
            ),
            approval_resolver=approval_resolver,
            allowed_tool_ids=frozenset(),
            artifacts_are_harvested=artifacts_are_harvested,
            observer=observer,
        )

    # -- observation helpers ----------------------------------------------
    def count_lines(self, name: str) -> int:
        path = self.root / name
        if not path.exists():
            return 0
        return len([line for line in path.read_text(encoding="utf-8").splitlines() if line])

    def events_of(self, run_id: str) -> list[str]:
        persisted = self.run_store.load(run_id)
        if persisted is None:
            return []
        return [str(getattr(event.event_type, "value", event.event_type)) for event in persisted.events]


# ---------------------------------------------------------------------------
# 4. graph validation before any execution
# ---------------------------------------------------------------------------


class GraphValidationTests(StepGraphCase):
    """A malformed or cyclic graph is refused before anything is created."""

    def declarations(self) -> dict[str, ExecutionDeclaration]:
        return {
            "one": self.declaration("one", WRITE_ONE),
            "two": self.declaration("two", WRITE_TWO),
        }

    def test_a_cyclic_graph_is_refused_before_anything_runs(self) -> None:
        graph = StepGraph(
            graph_id="cyclic",
            steps=(
                StepDefinition(step_id="a", action=StepAction("one"), depends_on=("b",)),
                StepDefinition(step_id="b", action=StepAction("two"), depends_on=("a",)),
            ),
        )
        runner = self.runner(self.declarations())

        with self.assertRaises(StepGraphValidationError) as caught:
            runner.run(graph, "cycle-run")

        reasons = caught.exception.reasons
        self.assertTrue(
            any(reason.startswith(REASON_CYCLE) for reason in reasons), reasons
        )
        # Nothing was created: no run directory, no file, no claim.
        self.assertFalse(self.run_store.exists("cycle-run"))
        self.assertFalse(self.run_store.exists(StepGraphRunner.step_run_id("cycle-run", "a")))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [])

    def test_a_missing_dependency_is_refused(self) -> None:
        graph = StepGraph(
            graph_id="missing-dep",
            steps=(StepDefinition(step_id="a", action=StepAction("one"), depends_on=("ghost",)),),
        )
        runner = self.runner(self.declarations())
        with self.assertRaises(StepGraphValidationError) as caught:
            runner.run(graph, "missing-run")
        self.assertIn(f"{REASON_UNKNOWN_DEPENDENCY}:a->ghost", caught.exception.reasons)
        self.assertFalse(self.run_store.exists("missing-run"))

    def test_duplicate_and_self_dependencies_are_refused(self) -> None:
        graph = StepGraph(
            graph_id="self-dep",
            steps=(StepDefinition(step_id="a", action=StepAction("one"), depends_on=("a",)),),
        )
        reasons = graph.validate()
        self.assertIn(f"{REASON_SELF_DEPENDENCY}:a", reasons)
        self.assertIn(f"{REASON_CYCLE}:a", reasons)

    def test_duplicate_step_ids_are_refused(self) -> None:
        graph = StepGraph(
            graph_id="dup",
            steps=(
                StepDefinition(step_id="a", action=StepAction("one")),
                StepDefinition(step_id="a", action=StepAction("two")),
            ),
        )
        self.assertIn(f"{REASON_DUPLICATE_STEP_ID}:a", graph.validate())

    def test_an_undeclared_action_is_refused_against_the_real_registry(self) -> None:
        graph = StepGraph(
            graph_id="undeclared",
            steps=(StepDefinition(step_id="a", action=StepAction("not-declared")),),
        )
        runner = self.runner(self.declarations())
        with self.assertRaises(StepGraphValidationError) as caught:
            runner.run(graph, "undeclared-run")
        self.assertIn(
            f"{REASON_UNKNOWN_DECLARATION}:a->not-declared", caught.exception.reasons
        )
        self.assertFalse(self.root.joinpath("one.txt").exists())

    def test_a_workspace_escaping_working_directory_is_refused(self) -> None:
        graph = StepGraph(
            graph_id="escape",
            steps=(
                StepDefinition(
                    step_id="a",
                    action=StepAction("one", (("working_directory", "../outside"),)),
                ),
            ),
        )
        runner = self.runner(self.declarations())
        with self.assertRaises(StepGraphValidationError) as caught:
            runner.run(graph, "escape-run")
        self.assertIn(
            f"{REASON_INVALID_PARAMETER_VALUE}:a->working_directory",
            caught.exception.reasons,
        )
        # Nothing outside the workspace was created either.
        outside = self.base / "outside"
        self.assertFalse(outside.exists())

    def test_a_parameter_may_not_extend_a_declared_timeout(self) -> None:
        graph = StepGraph(
            graph_id="widen",
            steps=(
                StepDefinition(
                    step_id="a",
                    action=StepAction("one", (("timeout_seconds", "600"),)),
                ),
            ),
        )
        runner = self.runner(self.declarations())
        with self.assertRaises(StepGraphValidationError) as caught:
            runner.run(graph, "widen-run")
        self.assertIn("parameter_widens_declared_limit:a->timeout_seconds", caught.exception.reasons)

    def test_ready_order_is_deterministic_and_topological(self) -> None:
        graph = StepGraph(
            graph_id="order",
            steps=(
                StepDefinition(step_id="c", action=StepAction("two"), depends_on=("a",)),
                StepDefinition(step_id="a", action=StepAction("one")),
                StepDefinition(step_id="b", action=StepAction("one")),
            ),
        )
        statuses = {step_id: StepStatus.PENDING for step_id in graph.step_ids}
        # c is declared first but depends on a, so a must precede it.
        self.assertEqual(graph.topological_order(), ("a", "c", "b"))
        # Among ready steps the declaration order breaks the tie, not set order.
        self.assertEqual(graph.ready_steps(statuses), ("a", "b"))
        statuses["a"] = StepStatus.SUCCEEDED
        self.assertEqual(graph.ready_steps(statuses), ("c", "b"))
        statuses["c"] = StepStatus.SUCCEEDED
        self.assertEqual(graph.ready_steps(statuses), ("b",))
        # b declares no dependency, so it is ready, not blocked; only c is.
        self.assertEqual(
            graph.blocked_steps(
                {"a": StepStatus.FAILED, "b": StepStatus.PENDING, "c": StepStatus.PENDING}
            ),
            ("c",),
        )


# ---------------------------------------------------------------------------
# 1, 2, 3. the chain, its failure, and blocking
# ---------------------------------------------------------------------------


class ChainTests(StepGraphCase):
    """A dependent chain runs for real; a failure blocks what depends on it."""

    def declarations(self) -> dict[str, ExecutionDeclaration]:
        return {
            "one": self.declaration("one", WRITE_ONE),
            "read": self.declaration("read", READ_ONE),
            "two": self.declaration("two", WRITE_TWO),
            "exit2": self.declaration("exit2", EXIT_TWO),
        }

    def test_a_dependent_chain_succeeds_with_real_workspace_effects(self) -> None:
        graph = StepGraph(
            graph_id="chain",
            steps=(
                StepDefinition(
                    step_id="write",
                    action=StepAction("one"),
                    verification=StepVerification(VerificationKind.EXIT_CODE, 0),
                ),
                StepDefinition(
                    step_id="read",
                    action=StepAction("read"),
                    depends_on=("write",),
                    verification=StepVerification(VerificationKind.EXIT_CODE, 0),
                ),
                StepDefinition(
                    step_id="second",
                    action=StepAction("two"),
                    depends_on=("read",),
                    verification=StepVerification(VerificationKind.EXIT_CODE, 0),
                ),
            ),
        )
        runner = self.runner(self.declarations())
        result = runner.run(graph, "chain-run")

        self.assertEqual(result.status, StepGraphStatus.COMPLETED)
        self.assertEqual(result.executed_steps, 3)
        for record in result.steps:
            self.assertEqual(record.status, StepStatus.SUCCEEDED, record.to_dict())
            self.assertEqual(record.execution_exit_code, 0)
            self.assertEqual(record.execution_outcome, "EXECUTION_SUCCESS")
            self.assertEqual(record.verification_status, VerificationStatus.PASSED)
            self.assertEqual(record.execution_error, "")
            self.assertTrue(record.step_run_id)

        # The read step can only have succeeded if the write step's child process
        # really created the file it reads.
        self.assertEqual((self.root / "one.txt").read_text(encoding="utf-8"), "1")
        self.assertEqual((self.root / "two.txt").read_text(encoding="utf-8"), "2")

        # Every step has its own durable run history, produced by the existing
        # chain: the policy check, the coordinator's dispatch, and the outcome.
        for step_id in ("write", "read", "second"):
            events = self.events_of(StepGraphRunner.step_run_id("chain-run", step_id))
            self.assertIn("run_started", events)
            self.assertIn("decision_made", events)
            self.assertIn("execution_policy_checked", events)
            self.assertIn("execution_started", events)
            self.assertIn("execution_completed", events)

        # No generative decision was made for any step: exactly one offline agent
        # is registered, and every decision was the declared one.
        self.assertEqual(len(self.runtime.agent_registry.list_agents()), 1)
        persisted = self.run_store.load(StepGraphRunner.step_run_id("chain-run", "write"))
        decisions = [
            event for event in persisted.events
            if str(getattr(event.event_type, "value", "")) == "decision_made"
        ]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].metadata.get("decision_source"), "declared_execution")
        self.assertIs(decisions[0].metadata.get("generative_dispatch"), False)

    def test_an_intermediate_failure_fails_the_graph_and_the_step(self) -> None:
        graph = StepGraph(
            graph_id="failing",
            steps=(
                StepDefinition(step_id="write", action=StepAction("one")),
                StepDefinition(
                    step_id="boom",
                    action=StepAction("exit2"),
                    depends_on=("write",),
                    verification=StepVerification(VerificationKind.EXIT_CODE, 0),
                ),
            ),
        )
        runner = self.runner(self.declarations())
        result = runner.run(graph, "failing-run")

        self.assertEqual(result.status, StepGraphStatus.FAILED)
        boom = result.step("boom")
        self.assertEqual(boom.status, StepStatus.FAILED)
        self.assertEqual(boom.execution_exit_code, 2)
        self.assertEqual(boom.execution_outcome, "EXECUTION_FAILURE")
        # The declared check was never reached, and that is recorded as NOT_RUN --
        # not as a verification failure, because the two are different facts.
        self.assertEqual(boom.verification_status, VerificationStatus.NOT_RUN)
        self.assertIn(
            "verification_not_run:execution_did_not_succeed", boom.evidence
        )
        self.assertEqual(result.step("write").status, StepStatus.SUCCEEDED)

    def test_dependent_steps_are_blocked_and_never_dispatched(self) -> None:
        graph = StepGraph(
            graph_id="blocked",
            steps=(
                StepDefinition(step_id="boom", action=StepAction("exit2")),
                StepDefinition(
                    step_id="after", action=StepAction("two"), depends_on=("boom",)
                ),
                StepDefinition(
                    step_id="after_after",
                    action=StepAction("one"),
                    depends_on=("after",),
                ),
            ),
        )
        runner = self.runner(self.declarations())
        result = runner.run(graph, "blocked-run")

        self.assertEqual(result.status, StepGraphStatus.FAILED)
        self.assertEqual(result.step("boom").status, StepStatus.FAILED)
        for step_id in ("after", "after_after"):
            record = result.step(step_id)
            self.assertEqual(record.status, StepStatus.BLOCKED, record.to_dict())
            self.assertEqual(record.attempts, 0)
            self.assertEqual(record.step_run_id, "")
            self.assertTrue(
                any(code.startswith("dependency_not_succeeded") for code in record.evidence),
                record.evidence,
            )
            # Blocked means never dispatched: no run history, no claim.
            self.assertFalse(
                self.run_store.exists(StepGraphRunner.step_run_id("blocked-run", step_id))
            )
        self.assertFalse((self.root / "two.txt").exists())
        self.assertFalse((self.root / "one.txt").exists())


# ---------------------------------------------------------------------------
# 6, 7. the authority boundary
# ---------------------------------------------------------------------------


class AuthorityBoundaryTests(StepGraphCase):
    """A plan is not permission; approval binds to the exact parameters."""

    def declarations(self) -> dict[str, ExecutionDeclaration]:
        return {
            "one": self.declaration("one", WRITE_ONE),
            "two": self.declaration("two", WRITE_TWO),
        }

    def test_required_but_unresolved_approval_performs_nothing(self) -> None:
        graph = StepGraph(
            graph_id="approval",
            steps=(
                StepDefinition(step_id="first", action=StepAction("one")),
                StepDefinition(
                    step_id="second", action=StepAction("two"), depends_on=("first",)
                ),
            ),
        )
        policy = ApprovalPolicy(approval_required_tools=("python",))
        runner = self.runner(
            self.declarations(),
            approval_policy=policy,
            approval_resolver=InMemoryApprovalResolver(),
        )
        result = runner.run(graph, "approval-run")

        first = result.step("first")
        self.assertEqual(first.status, StepStatus.WAITING_APPROVAL)
        self.assertEqual(first.execution_outcome, "APPROVAL_WAITING")
        self.assertIsNone(first.execution_exit_code)
        self.assertEqual(first.verification_status, VerificationStatus.NOT_RUN)
        self.assertEqual(result.step("second").status, StepStatus.BLOCKED)
        self.assertEqual(result.status, StepGraphStatus.REFUSED)
        # Nothing was performed, anywhere.
        self.assertFalse((self.root / "one.txt").exists())
        self.assertFalse((self.root / "two.txt").exists())

    def test_a_missing_approval_resolver_also_performs_nothing(self) -> None:
        graph = StepGraph(
            graph_id="no-resolver",
            steps=(StepDefinition(step_id="first", action=StepAction("one")),),
        )
        runner = self.runner(
            self.declarations(),
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=None,
        )
        result = runner.run(graph, "no-resolver-run")
        self.assertEqual(result.step("first").status, StepStatus.WAITING_APPROVAL)
        self.assertFalse((self.root / "one.txt").exists())

    def test_a_parameter_change_invalidates_a_previously_granted_approval(self) -> None:
        (self.root / "sub").mkdir()
        declarations = {
            "plain": self.declaration("plain", WRITE_ONE),
            "elsewhere": self.declaration("elsewhere", WRITE_ONE),
        }
        # The same declaration, two different parameter sets. ``elsewhere``
        # writes its file into sub/ because that is the step's working directory.
        graph_mismatch = StepGraph(
            graph_id="param-mismatch",
            steps=(
                StepDefinition(
                    step_id="one",
                    action=StepAction("elsewhere", (("working_directory", "sub"),)),
                ),
            ),
        )
        graph_match = StepGraph(
            graph_id="param-match",
            steps=(StepDefinition(step_id="one", action=StepAction("plain")),),
        )
        policy = ApprovalPolicy(approval_required_tools=("python",))
        resolver = InMemoryApprovalResolver()
        runner = self.runner(
            declarations, approval_policy=policy, approval_resolver=resolver
        )

        # An approval granted for the declared parameter set (working directory
        # "."), submitted against the step run id and request id the runner will
        # actually use **for the mismatching attempt**. If it were submitted for
        # another run id the refusal below would prove nothing: the approval has
        # to exist, bound to the old parameters, for the mismatch to be the cause.
        approved = runner.intent_fingerprint(graph_match, "one")
        for graph_run_id in ("mismatch-run", "match-run"):
            resolver.submit(
                StepGraphRunner.step_run_id(graph_run_id, "one"),
                StepGraphRunner.step_request_id(graph_run_id, "one"),
                ApprovalState.APPROVED,
                intent_fingerprint=approved,
            )

        # The graph asks for a *different* working directory. The intent
        # fingerprint changes, so the approval no longer matches this action.
        mismatch_fingerprint = runner.intent_fingerprint(graph_mismatch, "one")
        self.assertNotEqual(approved, mismatch_fingerprint)

        refused = runner.run(graph_mismatch, "mismatch-run")
        record = refused.step("one")
        self.assertEqual(record.status, StepStatus.WAITING_APPROVAL, record.to_dict())
        self.assertEqual(record.declaration_id, "elsewhere")
        self.assertEqual(record.parameters, (("working_directory", "sub"),))
        self.assertEqual(record.execution_outcome, "APPROVAL_WAITING")
        # The stale approval did not authorize the changed action.
        self.assertFalse((self.root / "sub" / "one.txt").exists())
        self.assertFalse((self.root / "one.txt").exists())

        # Control: the *same* approval mechanics, with the parameters it was
        # bound to, do authorize the action. So the refusal above is caused by the
        # parameter change and not by approval being broken in general.
        allowed = runner.run(graph_match, "match-run")
        self.assertEqual(allowed.step("one").status, StepStatus.SUCCEEDED)
        self.assertEqual((self.root / "one.txt").read_text(encoding="utf-8"), "1")


# ---------------------------------------------------------------------------
# 8. execution success with a failed verification
# ---------------------------------------------------------------------------


class VerificationTests(StepGraphCase):
    """Execution error and verification error are separate observations."""

    def declarations(self) -> dict[str, ExecutionDeclaration]:
        return {
            "one": self.declaration("one", WRITE_ONE),
            "nothing": self.declaration("nothing", MAKE_NOTHING),
            # Declares the artifact the step verifies but never writes it, so the
            # check can only pass if a matching path is mistaken for production.
            "nothing_declared": self.declaration(
                "nothing_declared", MAKE_NOTHING, artifact_targets=("made.txt",)
            ),
            "artifact": self.declaration(
                "artifact", MAKE_ARTIFACT, artifact_targets=("made.txt",)
            ),
        }

    def test_a_preexisting_artifact_is_never_reported_as_produced(self) -> None:
        """_INJECTION_TARGET_: existence is not production.

        The file is written BEFORE the attempt and the step writes nothing, so no
        attempt produced it. Goes red if a matching path alone yields PASSED --
        the graph then reports COMPLETED for work that never happened, which is
        exactly the behaviour measured before this fix.
        """

        (self.root / "made.txt").write_text("predates the run\n", encoding="utf-8")
        graph = StepGraph(
            graph_id="provenance-preexisting",
            steps=(
                StepDefinition(
                    step_id="checked",
                    action=StepAction("nothing_declared"),
                    verification=StepVerification(
                        VerificationKind.ARTIFACT_PRODUCED, artifact_target="made.txt"
                    ),
                ),
            ),
        )
        runner = self.runner(
            self.declarations(),
            isolate_workspace=True,
            artifacts_are_harvested=True,
        )
        result = runner.run(graph, "provenance-preexisting-run")

        record = result.step("checked")
        self.assertNotEqual(
            record.verification_status,
            VerificationStatus.PASSED,
            record.to_dict(),
        )
        self.assertNotEqual(
            result.status,
            StepGraphStatus.COMPLETED,
            record.to_dict(),
        )

    def test_a_verification_failure_while_the_command_succeeded(self) -> None:
        graph = StepGraph(
            graph_id="verify-fail",
            steps=(
                StepDefinition(
                    step_id="checked",
                    action=StepAction("nothing"),
                    # The command exits 0 but never produces this artifact.
                    verification=StepVerification(
                        VerificationKind.ARTIFACT_PRODUCED, artifact_target="made.txt"
                    ),
                ),
                StepDefinition(
                    step_id="after",
                    action=StepAction("one"),
                    depends_on=("checked",),
                ),
            ),
        )
        runner = self.runner(
            self.declarations(),
            isolate_workspace=True,
            artifacts_are_harvested=True,
        )
        result = runner.run(graph, "verify-fail-run")

        record = result.step("checked")
        # The command really succeeded: exit code 0, outcome EXECUTION_SUCCESS.
        self.assertEqual(record.execution_exit_code, 0, record.to_dict())
        self.assertEqual(record.execution_outcome, "EXECUTION_SUCCESS")
        self.assertEqual(record.execution_error, "")
        # The declared check did not hold, and that is a different failure.
        self.assertEqual(record.verification_status, VerificationStatus.FAILED)
        self.assertEqual(record.verification_error, "")
        self.assertIn("artifact_not_produced:made.txt", record.evidence)
        self.assertEqual(record.status, StepStatus.FAILED)
        self.assertEqual(result.status, StepGraphStatus.FAILED)
        self.assertEqual(result.step("after").status, StepStatus.BLOCKED)

    def test_a_produced_artifact_passes_by_its_harvested_digest(self) -> None:
        graph = StepGraph(
            graph_id="verify-pass",
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
        runner = self.runner(
            self.declarations(),
            isolate_workspace=True,
            artifacts_are_harvested=True,
        )
        result = runner.run(graph, "verify-pass-run")
        record = result.step("made")
        self.assertEqual(record.status, StepStatus.SUCCEEDED, record.to_dict())
        self.assertEqual(record.verification_status, VerificationStatus.PASSED)
        self.assertIn("artifact_produced=made.txt", record.verification_observed)
        self.assertIn("sha256=", record.verification_observed)

    def test_an_unevaluable_artifact_check_is_an_error_not_a_failure(self) -> None:
        graph = StepGraph(
            graph_id="verify-unevaluable",
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
        # The adapter is not isolating, so it harvests nothing: absence of the
        # artifact is not evidence, and the check is reported as unevaluable
        # rather than as a failure.
        runner = self.runner(
            self.declarations(),
            isolate_workspace=False,
            artifacts_are_harvested=False,
        )
        result = runner.run(graph, "verify-unevaluable-run")
        record = result.step("made")
        self.assertEqual(record.verification_status, VerificationStatus.ERROR)
        self.assertIn(
            "verification_error:adapter_does_not_harvest", record.evidence
        )
        self.assertEqual(record.status, StepStatus.FAILED)

    def test_a_declared_check_that_cannot_be_evaluated_never_completes_the_graph(self) -> None:
        graph = StepGraph(
            graph_id="never-complete",
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
        runner = self.runner(
            self.declarations(),
            isolate_workspace=False,
            artifacts_are_harvested=False,
        )
        result = runner.run(graph, "never-complete-run")
        self.assertEqual(result.status, StepGraphStatus.FAILED)
        self.assertNotEqual(result.status, StepGraphStatus.COMPLETED)
        record = result.step("made")
        self.assertEqual(record.verification_status, VerificationStatus.ERROR)
        self.assertEqual(record.status, StepStatus.FAILED)
        self.assertFalse(record.verified)


# ---------------------------------------------------------------------------
# 5. cancellation
# ---------------------------------------------------------------------------


class CancellationTests(StepGraphCase):
    """Cancellation stops new steps; a running step is never called cancelled."""

    def declarations(self) -> dict[str, ExecutionDeclaration]:
        return {
            "one": self.declaration("one", APPEND_COUNT),
            "sleepy": self.declaration(
                "sleepy",
                (
                    "python",
                    "-c",
                    "import time; time.sleep(1.5); "
                    "open('slow.txt','w',encoding='utf-8').write('done')",
                ),
            ),
            "two": self.declaration("two", append_programme("second")),
        }

    def test_cancellation_before_start_prevents_every_step(self) -> None:
        graph = StepGraph(
            graph_id="cancel-early",
            steps=(
                StepDefinition(step_id="a", action=StepAction("one")),
                StepDefinition(step_id="b", action=StepAction("two"), depends_on=("a",)),
            ),
        )
        cancel = threading.Event()
        cancel.set()
        runner = self.runner(self.declarations())
        result = runner.run(graph, "cancel-early-run", cancel_event=cancel)

        self.assertEqual(result.status, StepGraphStatus.CANCELLED)
        self.assertEqual(result.executed_steps, 0)
        for step_id in ("a", "b"):
            record = result.step(step_id)
            self.assertEqual(record.status, StepStatus.CANCELLED, record.to_dict())
            self.assertEqual(record.attempts, 0)
            self.assertIn("cancelled_before_start", record.evidence)
            self.assertFalse(
                self.run_store.exists(
                    StepGraphRunner.step_run_id("cancel-early-run", step_id)
                )
            )
        self.assertEqual(self.count_lines("count.txt"), 0)

    def test_cancellation_during_a_running_step_reports_what_happened(self) -> None:
        graph = StepGraph(
            graph_id="cancel-mid",
            steps=(
                StepDefinition(step_id="slow", action=StepAction("sleepy")),
                StepDefinition(step_id="next", action=StepAction("two"), depends_on=("slow",)),
            ),
        )
        cancel = threading.Event()
        runner = self.runner(self.declarations())

        observed: dict[str, object] = {}

        def canceller() -> None:
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if runner.status_of("slow") is StepStatus.RUNNING:
                    observed["status_when_cancelled"] = StepStatus.RUNNING
                    cancel.set()
                    return
                time.sleep(0.01)
            observed["timed_out"] = True

        thread = threading.Thread(target=canceller, daemon=True)
        thread.start()
        result = runner.run(graph, "cancel-mid-run", cancel_event=cancel)
        thread.join(timeout=20)

        self.assertNotIn("timed_out", observed)
        self.assertEqual(observed.get("status_when_cancelled"), StepStatus.RUNNING)

        slow = result.step("slow")
        # The step was already running, so it is NOT reported as cancelled: it was
        # allowed to finish, and the record says both facts.
        self.assertEqual(slow.status, StepStatus.SUCCEEDED, slow.to_dict())
        self.assertEqual(slow.execution_exit_code, 0)
        self.assertIn("cancel_requested_while_running", slow.evidence)
        self.assertTrue((self.root / "slow.txt").exists())

        nxt = result.step("next")
        self.assertEqual(nxt.status, StepStatus.CANCELLED)
        self.assertIn("cancelled_before_start", nxt.evidence)
        self.assertEqual(self.count_lines("second.txt"), 0)
        self.assertEqual(result.status, StepGraphStatus.CANCELLED)


# ---------------------------------------------------------------------------
# 9, 10. recovery, restart and the unknown
# ---------------------------------------------------------------------------


class RecoveryTests(StepGraphCase):
    """Progress survives a restart; an unprovable outcome is never guessed."""

    def declarations(self) -> dict[str, ExecutionDeclaration]:
        return {
            "a": self.declaration("a", append_programme("a")),
            "b": self.declaration("b", append_programme("b")),
            "c": self.declaration("c", append_programme("c")),
            "one": self.declaration("one", WRITE_ONE),
        }

    def chain(self) -> StepGraph:
        return StepGraph(
            graph_id="recovery",
            steps=(
                StepDefinition(step_id="a", action=StepAction("a")),
                StepDefinition(step_id="b", action=StepAction("b"), depends_on=("a",)),
                StepDefinition(step_id="c", action=StepAction("c"), depends_on=("b",)),
            ),
        )

    def test_resume_continues_pending_steps_without_repeating_settled_ones(self) -> None:
        first = self.runner(self.declarations())
        partial = first.run(self.chain(), "recovery-run", step_budget=1)

        self.assertEqual(partial.status, StepGraphStatus.INCOMPLETE)
        self.assertEqual(partial.stopped_reason, "step_budget_exhausted")
        self.assertEqual(partial.executed_steps, 1)
        self.assertEqual(partial.step("a").status, StepStatus.SUCCEEDED)
        self.assertEqual(partial.step("b").status, StepStatus.PENDING)
        self.assertEqual(partial.step("c").status, StepStatus.PENDING)
        self.assertEqual(self.count_lines("a.txt"), 1)
        self.assertEqual(self.count_lines("b.txt"), 0)

        # A restart: a brand new runner, over the same durable store and ledger.
        restarted = self.runner(self.declarations())
        self.assertIsNotNone(restarted.recorded("recovery-run"))
        resumed = restarted.resume(self.chain(), "recovery-run")

        self.assertEqual(resumed.status, StepGraphStatus.COMPLETED)
        self.assertEqual(resumed.executed_steps, 2)
        # The already-settled step was not run again: its side effect did not
        # happen a second time.
        self.assertEqual(self.count_lines("a.txt"), 1)
        self.assertEqual(self.count_lines("b.txt"), 1)
        self.assertEqual(self.count_lines("c.txt"), 1)
        self.assertEqual(resumed.step("a").attempts, 1)
        self.assertEqual(resumed.step("b").attempts, 1)

    def test_resume_refuses_a_saved_plan_that_no_longer_matches(self) -> None:
        first = self.runner(self.declarations())
        first.run(self.chain(), "recovery-mismatch", step_budget=1)

        changed = StepGraph(
            graph_id="recovery",
            steps=(
                StepDefinition(step_id="a", action=StepAction("a")),
                # The same step id, a different plan.
                StepDefinition(
                    step_id="b",
                    action=StepAction("b", (("timeout_seconds", "5"),)),
                    depends_on=("a",),
                ),
                StepDefinition(step_id="c", action=StepAction("c"), depends_on=("b",)),
            ),
        )
        restarted = self.runner(self.declarations())
        # The record must exist first, so the refusal below cannot be the
        # "no record at all" refusal wearing the same exception type.
        self.assertIsNotNone(restarted.recorded("recovery-mismatch"))
        with self.assertRaises(StepGraphStateError) as caught:
            restarted.resume(changed, "recovery-mismatch")
        self.assertIn("planned differently", str(caught.exception))
        self.assertEqual(self.count_lines("b.txt"), 0)
        self.assertEqual(self.count_lines("c.txt"), 0)

    def test_resume_without_a_record_never_starts_a_new_attempt(self) -> None:
        runner = self.runner(self.declarations())
        with self.assertRaises(StepGraphStateError) as caught:
            runner.resume(self.chain(), "never-started")
        self.assertIn("no persisted step-graph record", str(caught.exception))
        self.assertEqual(self.count_lines("a.txt"), 0)
        self.assertFalse(self.run_store.exists("never-started"))

    def test_an_interrupted_step_is_unknown_and_is_never_repeated(self) -> None:
        """A real interruption while a step is dispatched leaves UNKNOWN.

        The resolver interrupts while the step is waiting for approval inside the
        coordinator -- i.e. after the runner's durable ``RUNNING`` write and
        before the adapter spawns. The runner cannot prove afterwards whether the
        action completed, so it must say UNKNOWN and must never retry it.
        """
        graph = StepGraph(
            graph_id="interrupt",
            steps=(
                StepDefinition(step_id="a", action=StepAction("a")),
                StepDefinition(step_id="b", action=StepAction("b"), depends_on=("a",)),
                StepDefinition(step_id="c", action=StepAction("c"), depends_on=("b",)),
            ),
        )
        resolver = _InterruptingResolver(
            self.run_store, "interrupt-run", interrupt_step="b"
        )
        runner = self.runner(
            self.declarations(),
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=resolver,
        )

        with self.assertRaises(KeyboardInterrupt):
            runner.run(graph, "interrupt-run")

        # The claim and the dispatch were durable before the interrupt: the
        # resolver read RUNNING back from the store while the step was in flight.
        self.assertEqual(resolver.persisted_status, StepStatus.RUNNING)
        # The step before it really ran and really succeeded.
        self.assertEqual(self.count_lines("a.txt"), 1)

        restarted = self.runner(self.declarations())
        recorded = restarted.recorded("interrupt-run")
        self.assertIsNotNone(recorded)
        by_id = {record.step_id: record for record in recorded.steps}
        self.assertEqual(by_id["a"].status, StepStatus.SUCCEEDED)
        self.assertEqual(by_id["b"].status, StepStatus.UNKNOWN)
        self.assertIn(
            "dispatched_outcome_unknown:KeyboardInterrupt", by_id["b"].evidence
        )
        self.assertEqual(by_id["c"].status, StepStatus.PENDING)

        # The unproven step is not repeated: the durable ledger now refuses it,
        # because the HOST_PROCESS side effect was recorded as started and never
        # settled, so its outcome is unprovable.
        fresh_guard = restarted.ledger_guard()
        identity = restarted.operation_identity(graph, graph.step("b"), "interrupt-run")
        decision = fresh_guard.begin(
            identity,
            requested_run_id=StepGraphRunner.step_run_id("interrupt-run", "b"),
        )
        self.assertEqual(decision.verdict, IdempotencyVerdict.REFUSE)
        self.assertEqual(
            decision.failure_code, IdempotencyFailureCode.SIDE_EFFECT_UNKNOWN
        )

        resumed = restarted.resume(graph, "interrupt-run")
        # b stays UNKNOWN and c can only be blocked behind it; nothing is retried.
        self.assertEqual(resumed.status, StepGraphStatus.INDETERMINATE)
        self.assertEqual(resumed.step("b").status, StepStatus.UNKNOWN)
        self.assertEqual(resumed.step("c").status, StepStatus.BLOCKED)
        self.assertEqual(self.count_lines("b.txt"), 0)

    def test_a_persisted_running_step_is_read_back_as_unknown(self) -> None:
        """What a hard process kill leaves behind in the RUNNING window.

        The record below is written through the same public ``RunStore`` and the
        same ``StepGraphSnapshot`` codec the runner itself uses -- it is the
        state a kill leaves between the durable ``RUNNING`` write and the
        settlement, and the runner must read it back as UNKNOWN rather than as
        "never ran" or "ran and succeeded".

        ``declared_plan`` is included because a resume re-validates against the
        plan the attempt **declared**, read back from the record; a record that
        carries no declaration is refused outright (see
        ``test_resume_refuses_a_record_without_a_declared_plan`` in
        ``tests/test_recovery_and_project_memory.py``).
        """
        graph = StepGraph(
            graph_id="hard-kill",
            steps=(
                StepDefinition(step_id="a", action=StepAction("a")),
                StepDefinition(step_id="b", action=StepAction("b"), depends_on=("a",)),
            ),
        )
        snapshot = StepGraphSnapshot(
            graph_id="hard-kill",
            graph_run_id="hard-kill-run",
            status=StepGraphStatus.INCOMPLETE,
            steps=(
                StepRecord(
                    step_id="a",
                    status=StepStatus.SUCCEEDED,
                    action_fingerprint=graph.step("a").fingerprint(),
                    declaration_id="a",
                    execution_exit_code=0,
                    execution_outcome="EXECUTION_SUCCESS",
                    verification_status=VerificationStatus.NOT_RUN,
                    evidence=("executed_without_declared_verification",),
                    attempts=1,
                ),
                StepRecord(
                    step_id="b",
                    status=StepStatus.RUNNING,
                    action_fingerprint=graph.step("b").fingerprint(),
                    declaration_id="b",
                    step_run_id=StepGraphRunner.step_run_id("hard-kill-run", "b"),
                    attempts=1,
                ),
            ),
            stopped_reason="in_flight",
            declared_plan=declared_plan_of(graph),
        )
        self.run_store.write_state(
            RunStateSnapshot(
                run_id="hard-kill-run",
                project_state={"step_graph": snapshot.to_dict()},
            )
        )

        runner = self.runner(self.declarations())
        resumed = runner.resume(graph, "hard-kill-run")
        self.assertEqual(resumed.status, StepGraphStatus.INDETERMINATE)
        record = resumed.step("b")
        self.assertEqual(record.status, StepStatus.UNKNOWN)
        self.assertIn("interrupted_after_dispatch_outcome_unproven", record.evidence)
        # It is not retried, and nothing dependent on it may start.
        self.assertEqual(self.count_lines("b.txt"), 0)
        self.assertEqual(self.count_lines("a.txt"), 0)

    def test_a_saved_success_without_execution_evidence_is_not_completion(self) -> None:
        """_INJECTION_TARGET_: a saved status is a claim, not proof it ran.

        The record is written through the same public ``RunStore`` and
        ``StepGraphSnapshot`` codec the runner itself uses -- the state a record
        has if it survived a crash after the durable ``SUCCEEDED`` write but
        before anything justified it. It declares an ``EXIT_CODE`` verification
        that never ran, carries no execution outcome and no exit code, and has
        run nothing. Goes red if a restored ``SUCCEEDED`` alone is summarised as
        ``COMPLETED``, which would credit work that never happened.
        """

        graph = StepGraph(
            graph_id="unproven-success",
            steps=(
                StepDefinition(
                    step_id="a",
                    action=StepAction("a"),
                    verification=StepVerification(VerificationKind.EXIT_CODE, 0),
                ),
            ),
        )
        snapshot = StepGraphSnapshot(
            graph_id="unproven-success",
            graph_run_id="unproven-success-run",
            status=StepGraphStatus.COMPLETED,
            steps=(
                StepRecord(
                    step_id="a",
                    status=StepStatus.SUCCEEDED,
                    action_fingerprint=graph.step("a").fingerprint(),
                    declaration_id="a",
                    verification_kind=VerificationKind.EXIT_CODE.value,
                    verification_status=VerificationStatus.NOT_RUN,
                    attempts=0,
                ),
            ),
            declared_plan=declared_plan_of(graph),
        )
        self.run_store.write_state(
            RunStateSnapshot(
                run_id="unproven-success-run",
                project_state={"step_graph": snapshot.to_dict()},
            )
        )
        def stored_step() -> dict:
            state = self.run_store.read_state("unproven-success-run")
            return dict(state.project_state["step_graph"]["steps"][0])

        stored_before = stored_step()

        resumed = self.runner(self.declarations()).resume(graph, "unproven-success-run")

        self.assertNotEqual(resumed.status, StepGraphStatus.COMPLETED)
        self.assertEqual(resumed.status, StepGraphStatus.INDETERMINATE)
        record = resumed.step("a")
        self.assertEqual(record.status, StepStatus.UNKNOWN)
        self.assertTrue(
            any("execution_evidence" in tag for tag in record.evidence), record.evidence
        )
        # Nothing was executed to obtain new proof. The saved record did claim
        # SUCCEEDED, and restoration records that the claim is unsupported instead of
        # letting it stand as proof or dropping what was asserted.
        self.assertEqual(self.count_lines("a.txt"), 0)
        self.assertEqual(stored_before["status"], StepStatus.SUCCEEDED.value)
        stored_after = stored_step()
        self.assertEqual(stored_after["status"], StepStatus.UNKNOWN.value)
        self.assertTrue(
            any("not_proof" in tag for tag in stored_after["evidence"]),
            stored_after["evidence"],
        )

        # Resuming again answers the same way.
        again = self.runner(self.declarations()).resume(graph, "unproven-success-run")
        self.assertEqual(again.status, StepGraphStatus.INDETERMINATE)

    def test_a_declared_exit_code_verification_that_never_ran_is_not_confirmation(self) -> None:
        """An unrun declared check cannot be what makes a step successful."""

        graph = StepGraph(
            graph_id="unrun-check",
            steps=(
                StepDefinition(
                    step_id="a",
                    action=StepAction("a"),
                    verification=StepVerification(VerificationKind.EXIT_CODE, 0),
                ),
            ),
        )
        snapshot = StepGraphSnapshot(
            graph_id="unrun-check",
            graph_run_id="unrun-check-run",
            status=StepGraphStatus.COMPLETED,
            steps=(
                StepRecord(
                    step_id="a",
                    status=StepStatus.SUCCEEDED,
                    action_fingerprint=graph.step("a").fingerprint(),
                    declaration_id="a",
                    verification_kind=VerificationKind.EXIT_CODE.value,
                    verification_status=VerificationStatus.NOT_RUN,
                    execution_outcome="EXECUTION_SUCCESS",
                    execution_exit_code=0,
                    attempts=1,
                ),
            ),
            declared_plan=declared_plan_of(graph),
        )
        self.run_store.write_state(
            RunStateSnapshot(
                run_id="unrun-check-run",
                project_state={"step_graph": snapshot.to_dict()},
            )
        )

        resumed = self.runner(self.declarations()).resume(graph, "unrun-check-run")

        self.assertNotEqual(resumed.status, StepGraphStatus.COMPLETED)
        self.assertEqual(resumed.step("a").status, StepStatus.UNKNOWN)

    def test_contradictory_execution_evidence_is_not_success(self) -> None:
        """A step cannot be SUCCEEDED while its own record says the run failed."""

        graph = StepGraph(
            graph_id="contradiction",
            steps=(
                StepDefinition(step_id="a", action=StepAction("a")),
            ),
        )
        snapshot = StepGraphSnapshot(
            graph_id="contradiction",
            graph_run_id="contradiction-run",
            status=StepGraphStatus.COMPLETED,
            steps=(
                StepRecord(
                    step_id="a",
                    status=StepStatus.SUCCEEDED,
                    action_fingerprint=graph.step("a").fingerprint(),
                    declaration_id="a",
                    verification_status=VerificationStatus.NOT_RUN,
                    execution_outcome="EXECUTION_TIMEOUT",
                    attempts=1,
                ),
            ),
            declared_plan=declared_plan_of(graph),
        )
        self.run_store.write_state(
            RunStateSnapshot(
                run_id="contradiction-run",
                project_state={"step_graph": snapshot.to_dict()},
            )
        )

        resumed = self.runner(self.declarations()).resume(graph, "contradiction-run")

        self.assertNotEqual(resumed.status, StepGraphStatus.COMPLETED)
        self.assertEqual(resumed.step("a").status, StepStatus.UNKNOWN)

    def test_a_full_record_with_consistent_evidence_resumes_as_completed(self) -> None:
        """The positive control: complete, consistent evidence must still complete.

        The step declares an ``EXIT_CODE`` check, that check passed, an execution
        outcome and an exit code are recorded -- nothing here is a bare claim, so
        a conservative rule must not turn it into a failure.
        """

        graph = StepGraph(
            graph_id="proven-success",
            steps=(
                StepDefinition(
                    step_id="a",
                    action=StepAction("a"),
                    verification=StepVerification(VerificationKind.EXIT_CODE, 0),
                ),
            ),
        )
        snapshot = StepGraphSnapshot(
            graph_id="proven-success",
            graph_run_id="proven-success-run",
            status=StepGraphStatus.COMPLETED,
            steps=(
                StepRecord(
                    step_id="a",
                    status=StepStatus.SUCCEEDED,
                    action_fingerprint=graph.step("a").fingerprint(),
                    declaration_id="a",
                    verification_kind=VerificationKind.EXIT_CODE.value,
                    verification_status=VerificationStatus.PASSED,
                    execution_outcome="EXECUTION_SUCCESS",
                    execution_exit_code=0,
                    attempts=1,
                ),
            ),
            declared_plan=declared_plan_of(graph),
        )
        self.run_store.write_state(
            RunStateSnapshot(
                run_id="proven-success-run",
                project_state={"step_graph": snapshot.to_dict()},
            )
        )

        resumed = self.runner(self.declarations()).resume(graph, "proven-success-run")

        self.assertEqual(resumed.status, StepGraphStatus.COMPLETED)
        self.assertEqual(resumed.step("a").status, StepStatus.SUCCEEDED)

    def test_a_step_that_declares_no_check_keeps_its_execution_evidence(self) -> None:
        """A legitimate no-verification step is not broken by a universal rule.

        It declares no check, so ``NOT_RUN`` is correct for it; what makes it
        trustworthy is the recorded execution, not the absence of a check.
        """

        graph = StepGraph(
            graph_id="no-check",
            steps=(
                StepDefinition(step_id="a", action=StepAction("a")),
            ),
        )
        snapshot = StepGraphSnapshot(
            graph_id="no-check",
            graph_run_id="no-check-run",
            status=StepGraphStatus.COMPLETED,
            steps=(
                StepRecord(
                    step_id="a",
                    status=StepStatus.SUCCEEDED,
                    action_fingerprint=graph.step("a").fingerprint(),
                    declaration_id="a",
                    verification_status=VerificationStatus.NOT_RUN,
                    execution_outcome="EXECUTION_SUCCESS",
                    execution_exit_code=0,
                    evidence=("executed_without_declared_verification",),
                    attempts=1,
                ),
            ),
            declared_plan=declared_plan_of(graph),
        )
        self.run_store.write_state(
            RunStateSnapshot(
                run_id="no-check-run",
                project_state={"step_graph": snapshot.to_dict()},
            )
        )

        resumed = self.runner(self.declarations()).resume(graph, "no-check-run")

        self.assertEqual(resumed.status, StepGraphStatus.COMPLETED)
        self.assertEqual(resumed.step("a").status, StepStatus.SUCCEEDED)


class _InterruptingResolver:
    """An operator approval channel that is interrupted by a real KeyboardInterrupt.

    It is a collaborator supplied by the composition, not a replacement for an
    internal: the runner consults it exactly as it would consult a human
    approval path, and the interrupt models the operator pressing Ctrl-C while a
    step waits for approval. Before interrupting, it reads the step's status back
    from the *store the runner uses* -- so the assertion that a durable RUNNING
    existed before the dispatch is an observation, not an assumption.
    """

    def __init__(
        self,
        run_store: RunStore,
        graph_run_id: str,
        *,
        interrupt_step: str,
    ) -> None:
        self._run_store = run_store
        self._graph_run_id = graph_run_id
        self._interrupt_step = interrupt_step
        self.persisted_status: StepStatus | None = None
        self._interrupted = False
        self.consulted = 0

    def resolve(self, request: object):
        from app.tools.approval import ApprovalResolution

        self.consulted += 1
        step_run_id = str(getattr(request, "run_id", ""))
        state = self._run_store.read_state(self._graph_run_id)
        payload = (state.project_state or {}).get("step_graph") if state else None
        if not isinstance(payload, dict):
            return None
        recorded = StepGraphSnapshot.from_dict(payload)
        for record in recorded.steps:
            if not record.step_run_id or record.step_run_id != step_run_id:
                continue
            if record.step_id == self._interrupt_step:
                # The step is in flight: its durable status is what the runner
                # wrote before dispatching, and the operator now interrupts.
                self.persisted_status = record.status
                if not self._interrupted:
                    self._interrupted = True
                    raise KeyboardInterrupt()
                return None
            # Every other step is approved for exactly the action requested.
            return ApprovalResolution(
                decision=ApprovalState.APPROVED,
                approved_fingerprint=str(
                    getattr(request, "intent_fingerprint", "") or ""
                ),
                approval_id="interrupt-resolver-approval",
            )
        return None


# ---------------------------------------------------------------------------
# 11. repeated requests
# ---------------------------------------------------------------------------


class RepeatRequestTests(StepGraphCase):
    """Asking twice never executes twice, and never re-executes a settled step."""

    def declarations(self) -> dict[str, ExecutionDeclaration]:
        return {"count": self.declaration("count", APPEND_COUNT)}

    def graph(self) -> StepGraph:
        return StepGraph(
            graph_id="repeat",
            steps=(StepDefinition(step_id="once", action=StepAction("count")),),
        )

    def test_a_repeated_start_request_executes_nothing(self) -> None:
        runner = self.runner(self.declarations())
        first = runner.run(self.graph(), "repeat-run")
        self.assertEqual(first.status, StepGraphStatus.COMPLETED)
        self.assertEqual(self.count_lines("count.txt"), 1)

        events_before = len(self.events_of(StepGraphRunner.step_run_id("repeat-run", "once")))
        second = runner.run(self.graph(), "repeat-run")

        self.assertFalse(second.executed)
        self.assertEqual(second.stopped_reason, "already_recorded")
        self.assertEqual(second.status, StepGraphStatus.COMPLETED)
        self.assertEqual(second.step("once").attempts, 1)
        self.assertEqual(second.step("once").step_run_id, first.step("once").step_run_id)
        # No second child process: the side effect happened once and the step's
        # own durable run history gained no new event.
        self.assertEqual(self.count_lines("count.txt"), 1)
        self.assertEqual(
            len(self.events_of(StepGraphRunner.step_run_id("repeat-run", "once"))),
            events_before,
        )

    def test_a_lost_progress_record_still_cannot_re_execute_the_step(self) -> None:
        """The durable claim, not the local snapshot, is what forbids a repeat."""
        runner = self.runner(self.declarations())
        runner.run(self.graph(), "repeat-lost")
        self.assertEqual(self.count_lines("count.txt"), 1)

        # Simulate a lost progress snapshot: the graph's own state file is gone.
        self.assertTrue(self.run_store.exists("repeat-lost"))
        self.run_store.state_path("repeat-lost").unlink()
        self.assertIsNone(runner.recorded("repeat-lost"))

        again = runner.run(self.graph(), "repeat-lost")
        record = again.step("once")
        self.assertEqual(record.status, StepStatus.UNKNOWN, record.to_dict())
        self.assertIn("recorded_outcome_without_local_evidence", record.evidence)
        self.assertNotEqual(again.status, StepGraphStatus.COMPLETED)
        # Nothing ran a second time.
        self.assertEqual(self.count_lines("count.txt"), 1)

    def test_a_concurrent_second_start_of_the_same_graph_is_refused(self) -> None:
        gate = threading.Event()
        runner = self.runner(
            self.declarations(),
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=_GatedResolver(gate),
        )
        results: dict[str, object] = {}

        def first() -> None:
            results["first"] = runner.run(self.graph(), "race-run")

        thread = threading.Thread(target=first, daemon=True)
        thread.start()
        # Wait until the first caller really is inside the step.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if runner.status_of("once") is StepStatus.RUNNING:
                break
            time.sleep(0.01)
        self.assertEqual(runner.status_of("once"), StepStatus.RUNNING)

        second = runner.run(self.graph(), "race-run")
        self.assertFalse(second.executed)
        self.assertEqual(second.stopped_reason, "graph_run_in_progress")
        self.assertEqual(second.status, StepGraphStatus.REFUSED)
        self.assertEqual(second.executed_steps, 0)

        gate.set()
        thread.join(timeout=20)
        self.assertIn("first", results)
        # The one admitted attempt ran exactly once, and the refused caller never
        # created a second child process.
        self.assertEqual(self.count_lines("count.txt"), 1)


class _GatedResolver:
    """Blocks one step inside the coordinator until the test releases it.

    Once released it approves exactly the action that was requested -- using the
    request's own intent fingerprint -- so the step really runs and the test can
    count how many times.
    """

    def __init__(self, gate: threading.Event) -> None:
        self._gate = gate

    def resolve(self, request: object):
        from app.tools.approval import ApprovalResolution

        self._gate.wait(timeout=30)
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=str(getattr(request, "intent_fingerprint", "") or ""),
            approval_id="gated-approval",
        )


# ---------------------------------------------------------------------------
# 12. workspace isolation
# ---------------------------------------------------------------------------


class WorkspaceIsolationTests(StepGraphCase):
    """Operations stay inside the workspace and user changes survive."""

    def declarations(self) -> dict[str, ExecutionDeclaration]:
        return {
            "one": self.declaration("one", WRITE_ONE),
            "artifact": self.declaration(
                "artifact", MAKE_ARTIFACT, artifact_targets=("made.txt",)
            ),
        }

    def graph(self, *, declaration_id: str = "one") -> StepGraph:
        return StepGraph(
            graph_id="isolation",
            steps=(StepDefinition(step_id="step", action=StepAction(declaration_id)),),
        )

    def test_user_changes_are_preserved_under_both_adapter_settings(self) -> None:
        notes = self.root / "user_notes.txt"
        notes.write_bytes(b"owner's own uncommitted work\n")
        before = notes.read_bytes()

        for isolate in (True, False):
            with self.subTest(isolate_workspace=isolate):
                RunScope.release_all()
                runner = self.runner(self.declarations(), isolate_workspace=isolate)
                result = runner.run(self.graph(), f"isolation-run-{isolate}")
                self.assertEqual(result.status, StepGraphStatus.COMPLETED)
                self.assertEqual(notes.read_bytes(), before)

    def test_an_isolated_step_cannot_write_into_the_real_workspace(self) -> None:
        runner = self.runner(self.declarations(), isolate_workspace=True)
        result = runner.run(self.graph(), "isolated-run")
        record = result.step("step")
        self.assertEqual(record.status, StepStatus.SUCCEEDED, record.to_dict())
        self.assertEqual(record.execution_exit_code, 0)
        # The child ran in an ephemeral copy: its write did not reach the
        # operator's workspace, and only a harvested digest survives.
        self.assertFalse((self.root / "one.txt").exists())

    def test_a_non_isolated_step_writes_inside_the_workspace_only(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        runner = self.runner(self.declarations(), isolate_workspace=False)
        result = runner.run(self.graph(), "inside-run")
        self.assertEqual(result.status, StepGraphStatus.COMPLETED)
        self.assertEqual((self.root / "one.txt").read_text(encoding="utf-8"), "1")
        # Nothing was created outside the frozen workspace root **by the plane**:
        # the declared working directory is the workspace root.
        self.assertEqual(sorted(p.name for p in outside.iterdir()), [])
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["outside", "workspace"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
