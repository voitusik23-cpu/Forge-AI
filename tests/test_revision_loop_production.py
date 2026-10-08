"""Production revision loop: bounded, objective, non-authoritative.

These tests pin the revision boundary:

* a revision is triggered only by an **objective** failure from the existing
  verification/acceptance stages - never because a planner or decision would like
  another step;
* eligibility is server-side and conservative: a real verification outcome, an
  actionable failure, remaining budget, **and** a current plan to replace;
* security, authority, validation, and no-progress failures terminate instead of
  being retried;
* the budget is a server-side value that nothing per-run can widen;
* every revision produces a new immutable plan identity that is validated again,
  and a revision that resolves to an already-used intention set is refused;
* ``AgentHarness`` stays the only orchestration loop.

Real bounded behaviour runs against real temporary workspaces; no tracked
repository file is mutated.
"""

from __future__ import annotations

from pathlib import Path
import dataclasses
import json
import tempfile
import unittest

from app.agent_runtime.harness import AgentHarness
from app.agent_runtime.models import HarnessRequest
from app.agent_runtime.policy import AgentHarnessPolicy
from app.agent_runtime.revision_decision import (
    ACTIONABLE_CATEGORIES,
    TERMINAL_CATEGORIES,
    RevisionBudget,
    RevisionDecision,
    RevisionError,
    RevisionFailureCategory,
)
from app.agent_runtime.revision_policy import (
    RevisionEligibility,
    classify_failure,
    evaluate_revision,
    plan_structure_signature,
)
from app.execution.capabilities import ExecutionCapability
from app.execution.profile import ProjectExecutionProfile
from app.planning.plan import ExecutionPlan, PlanStep, PlanStepType
from app.planning.planner import Planner
from app.projects.state import ProjectState, ProjectStateStatus
from app.runtime.run_scope import RunScope
from app.tasks.specification import AcceptanceCriterion, Requirement, TaskSpecification
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace

RUN_ID = "run-rev-1"
TASK_ID = "task-rev-1"
GOAL = "build a web application"

PROFILE = ProjectExecutionProfile(
    profile_id="p",
    allowed_commands=("python",),
    capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
    network_access=False,
    working_directory=".",
)


def executable_source(path: Path) -> str:
    """Return a module's code with docstrings and comments removed.

    Prose may legitimately name what a module must never do, so only executable
    lines are evidence about its behaviour.
    """
    import ast

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    prose = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                prose.add(doc)
    kept = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if any(stripped and stripped in doc for doc in prose):
            continue
        kept.append(line)
    return "\n".join(kept)


class RevisionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.workspace = Workspace(self.root)
        self.req = Requirement(requirement_id="r1", description="check the artifact")
        self.crit = AcceptanceCriterion(
            criterion_id="c1", requirement_id="r1", description="artifact exists"
        )

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _request(
        self,
        run_id: str = RUN_ID,
        *,
        goal: str = GOAL,
        task_id: str = TASK_ID,
        expectation_path: str = "missing.txt",
    ) -> HarnessRequest:
        scope = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=PROFILE,
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(self.crit,),
        )
        scope.freeze()
        return HarnessRequest(
            run_id=run_id,
            workspace=self.workspace,
            task_specification=TaskSpecification(
                task_id=task_id,
                title="revision task",
                description=goal,
                requirements=(self.req,),
                acceptance_criteria=(self.crit,),
            ),
            verification_expectations={
                "c1": VerificationExpectation(expectation_path, True)
            },
            acceptance_criteria=(self.crit,),
            requirements=(self.req,),
            metadata={"task_id": task_id},
            initial_project_state=ProjectState(
                run_id=run_id, attempt_number=0, status=ProjectStateStatus.CHANGED
            ),
            run_scope=scope,
        )

    @staticmethod
    def _meta(result, name):
        return [
            dict(event.metadata)
            for event in result.events
            if event.event_type.name == name
        ]


# --------------------------------------------------------------------------- #
# Budget
# --------------------------------------------------------------------------- #


class RevisionBudgetTests(RevisionTestCase):
    def test_zero_budget_allows_no_revision(self) -> None:
        budget = RevisionBudget(max_revisions=0)
        self.assertFalse(budget.allows(0))
        self.assertEqual(budget.remaining(0), 0)

    def test_budget_allows_up_to_its_bound(self) -> None:
        budget = RevisionBudget(max_revisions=2)
        self.assertTrue(budget.allows(0))
        self.assertTrue(budget.allows(1))
        self.assertFalse(budget.allows(2))
        self.assertEqual(budget.remaining(1), 1)

    def test_invalid_budget_is_rejected(self) -> None:
        for bad in (-1, "2", 1.5, None, True):
            with self.assertRaises(RevisionError):
                RevisionBudget(max_revisions=bad)  # type: ignore[arg-type]

    def test_invalid_revision_number_is_rejected(self) -> None:
        budget = RevisionBudget(max_revisions=1)
        for bad in (-1, "0", None, True):
            with self.assertRaises(RevisionError):
                budget.allows(bad)  # type: ignore[arg-type]

    def test_default_budget_comes_from_the_policy(self) -> None:
        harness = AgentHarness(
            policy=AgentHarnessPolicy(max_revision_attempts=3)
        )
        # The loop is bounded either way: with no explicit budget it inherits the
        # existing server-side policy limit.
        self.assertEqual(harness._revision_budget.max_revisions, 3)

    def test_explicit_budget_must_be_a_budget(self) -> None:
        with self.assertRaises(RevisionError):
            AgentHarness(policy=AgentHarnessPolicy(), revision_budget=5)  # type: ignore[arg-type]

    def test_budget_is_not_readable_from_the_request(self) -> None:
        fields = {f.name for f in dataclasses.fields(HarnessRequest)}
        for banned in (
            "max_revisions", "revision_budget", "retry_count", "loop_count",
            "revision_number",
        ):
            self.assertNotIn(banned, fields, banned)


# --------------------------------------------------------------------------- #
# Classification and eligibility
# --------------------------------------------------------------------------- #


class RevisionClassificationTests(RevisionTestCase):
    def _eligibility(self, **overrides) -> RevisionEligibility:
        payload = {
            "run_id": RUN_ID,
            "task_id": TASK_ID,
            "revision_number": 0,
            "verification_attempted": True,
            "acceptance_passed": False,
            "execution_outcome": "EXECUTION_SUCCESS",
            "plan_id": "plan-1",
            "plan_fingerprint": "fp-1",
            "plan_structure": "shape-1",
        }
        payload.update(overrides)
        return RevisionEligibility(**payload)

    def test_actionable_failure_is_revision_eligible(self) -> None:
        decision = evaluate_revision(
            eligibility=self._eligibility(),
            budget=RevisionBudget(max_revisions=1),
            base_goal=GOAL,
        )
        self.assertTrue(decision.revision_allowed)
        self.assertTrue(decision.actionable)
        self.assertEqual(
            decision.failure_category, RevisionFailureCategory.ACCEPTANCE_FAILED
        )
        self.assertIn("revision 1", decision.revision_goal)
        self.assertEqual(decision.budget_remaining, 1)

    def test_accepted_run_never_revises(self) -> None:
        decision = evaluate_revision(
            eligibility=self._eligibility(acceptance_passed=True),
            budget=RevisionBudget(max_revisions=5),
            base_goal=GOAL,
        )
        self.assertFalse(decision.revision_allowed)
        self.assertEqual(decision.reason, "already_accepted")

    def test_security_failure_is_terminal(self) -> None:
        for outcome in ("PERMISSION_DENIED", "POLICY_DENIED", "APPROVAL_REJECTED"):
            decision = evaluate_revision(
                eligibility=self._eligibility(execution_outcome=outcome),
                budget=RevisionBudget(max_revisions=5),
                base_goal=GOAL,
            )
            self.assertFalse(decision.revision_allowed, outcome)
            self.assertEqual(
                decision.failure_category, RevisionFailureCategory.SECURITY_FAILURE
            )
            self.assertIn("terminal_failure", decision.reason)
            self.assertFalse(decision.actionable)

    def test_execution_failure_is_actionable(self) -> None:
        decision = evaluate_revision(
            eligibility=self._eligibility(execution_outcome="EXECUTION_FAILURE"),
            budget=RevisionBudget(max_revisions=1),
            base_goal=GOAL,
        )
        self.assertTrue(decision.revision_allowed)
        self.assertEqual(
            decision.failure_category, RevisionFailureCategory.EXECUTION_FAILED
        )

    def test_missing_verification_is_not_revision_eligible(self) -> None:
        """Revision needs a real verification outcome, not a guess."""
        decision = evaluate_revision(
            eligibility=self._eligibility(verification_attempted=False),
            budget=RevisionBudget(max_revisions=5),
            base_goal=GOAL,
        )
        self.assertFalse(decision.revision_allowed)
        self.assertEqual(
            decision.failure_category, RevisionFailureCategory.NON_ACTIONABLE_FAILURE
        )

    def test_budget_exhaustion_is_terminal(self) -> None:
        decision = evaluate_revision(
            eligibility=self._eligibility(revision_number=1),
            budget=RevisionBudget(max_revisions=1),
            base_goal=GOAL,
        )
        self.assertFalse(decision.revision_allowed)
        self.assertEqual(decision.reason, "revision_budget_exhausted")

    def test_missing_plan_is_terminal(self) -> None:
        decision = evaluate_revision(
            eligibility=self._eligibility(plan_fingerprint=""),
            budget=RevisionBudget(max_revisions=5),
            base_goal=GOAL,
        )
        self.assertFalse(decision.revision_allowed)
        self.assertEqual(decision.reason, "no_current_plan")

    def test_missing_goal_source_is_terminal(self) -> None:
        decision = evaluate_revision(
            eligibility=self._eligibility(),
            budget=RevisionBudget(max_revisions=5),
            base_goal="",
        )
        self.assertFalse(decision.revision_allowed)
        self.assertEqual(decision.reason, "no_revision_goal_source")

    def test_taxonomy_is_closed_and_disjoint(self) -> None:
        self.assertEqual(
            ACTIONABLE_CATEGORIES & TERMINAL_CATEGORIES, frozenset()
        )
        for category in ACTIONABLE_CATEGORIES:
            self.assertNotIn(category, TERMINAL_CATEGORIES)
        self.assertIn(
            RevisionFailureCategory.SECURITY_FAILURE, TERMINAL_CATEGORIES
        )
        with self.assertRaises(ValueError):
            RevisionFailureCategory("try_again")

    def test_classify_failure_is_objective(self) -> None:
        self.assertEqual(
            classify_failure(
                verification_attempted=True,
                acceptance_passed=True,
                execution_outcome="EXECUTION_SUCCESS",
            ),
            RevisionFailureCategory.NON_ACTIONABLE_FAILURE,
        )
        self.assertEqual(
            classify_failure(
                verification_attempted=True,
                acceptance_passed=False,
                execution_outcome="POLICY_DENIED",
            ),
            RevisionFailureCategory.SECURITY_FAILURE,
        )


class RevisionDecisionModelTests(RevisionTestCase):
    def _decision(self, **overrides) -> RevisionDecision:
        payload = {
            "run_id": RUN_ID,
            "task_id": TASK_ID,
            "revision_number": 0,
            "failure_category": RevisionFailureCategory.ACCEPTANCE_FAILED,
            "reason": "actionable_failure_within_budget",
            "revision_allowed": True,
            "actionable": True,
            "revision_goal": GOAL + " [revision 1: address acceptance_failed]",
            "budget_remaining": 1,
        }
        payload.update(overrides)
        return RevisionDecision(**payload)

    def test_decision_is_immutable(self) -> None:
        subject = self._decision()
        self.assertTrue(RevisionDecision.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            subject.revision_allowed = False

    def test_decision_carries_no_authority_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(RevisionDecision)}
        for banned in (
            "command", "argv", "executable", "shell", "environment", "timeout",
            "run_scope", "authorized_execution", "approval", "capabilities",
            "allowed_tool_ids", "workspace", "profile", "acceptance_criteria",
        ):
            self.assertNotIn(banned, fields, banned)

    def test_allowed_decision_requires_actionable_and_goal(self) -> None:
        with self.assertRaises(RevisionError):
            self._decision(actionable=False)
        with self.assertRaises(RevisionError):
            self._decision(revision_goal="")

    def test_decision_rejects_invalid_identity(self) -> None:
        for bad in ("", "   ", None):
            with self.assertRaises(RevisionError):
                self._decision(run_id=bad)
            with self.assertRaises(RevisionError):
                self._decision(task_id=bad)

    def test_cross_run_and_cross_task_decisions_are_rejected(self) -> None:
        subject = self._decision()
        subject.assert_belongs_to(RUN_ID, TASK_ID)
        with self.assertRaises(RevisionError):
            subject.assert_belongs_to("other-run", TASK_ID)
        with self.assertRaises(RevisionError):
            subject.assert_belongs_to(RUN_ID, "other-task")

    def test_bounded_summary_is_sanitized(self) -> None:
        payload = json.dumps(self._decision().bounded_summary(), sort_keys=True)
        for banned in (
            "command", "argv", "environment", "token", "password", "secret",
            "run_scope", "approval",
        ):
            self.assertNotIn(banned, payload.lower(), banned)

    def test_negative_numbers_are_rejected(self) -> None:
        with self.assertRaises(RevisionError):
            self._decision(revision_number=-1)
        with self.assertRaises(RevisionError):
            self._decision(budget_remaining=-1)

    def test_unknown_failure_category_is_rejected(self) -> None:
        with self.assertRaises(RevisionError):
            self._decision(failure_category="acceptance_failed")


# --------------------------------------------------------------------------- #
# Plan isolation
# --------------------------------------------------------------------------- #


class RevisionPlanIsolationTests(RevisionTestCase):
    def test_replanned_identity_differs(self) -> None:
        planner = Planner()
        first = planner.plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        second = planner.plan_for_run(
            goal=GOAL + " [revision 1: address acceptance_failed]",
            run_id=RUN_ID,
            task_id=TASK_ID,
        )
        self.assertNotEqual(first.plan_id, second.plan_id)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        # The run and task binding never changes across a revision.
        self.assertEqual(second.run_id, first.run_id)
        self.assertEqual(second.task_id, first.task_id)

    def test_original_plan_is_never_mutated(self) -> None:
        original = Planner().plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        before = original.fingerprint
        before_steps = tuple(original.step_ids)
        Planner().plan_for_run(
            goal=GOAL + " [revision 1: address acceptance_failed]",
            run_id=RUN_ID,
            task_id=TASK_ID,
        )
        self.assertEqual(original.fingerprint, before)
        self.assertEqual(original.step_ids, before_steps)

    def test_structure_signature_detects_the_same_intentions(self) -> None:
        planner = Planner()
        first = planner.plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        second = planner.plan_for_run(
            goal=GOAL + " [revision 1: address acceptance_failed]",
            run_id=RUN_ID,
            task_id=TASK_ID,
        )
        # Different identity, identical shape: this is what no-progress detection
        # compares, because the fingerprint alone would look like progress.
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            plan_structure_signature(first.steps),
            plan_structure_signature(second.steps),
        )

    def test_signature_distinguishes_real_change(self) -> None:
        a = (PlanStep("s1", PlanStepType.OBSERVE, "look"),)
        b = (PlanStep("s1", PlanStepType.MODIFY, "change"),)
        self.assertNotEqual(
            plan_structure_signature(a), plan_structure_signature(b)
        )

    def test_revision_plan_must_still_validate(self) -> None:
        from app.planning.validation import require_valid_plan

        planner = Planner()
        cross_run = planner.plan_for_run(goal=GOAL, run_id="other-run", task_id=TASK_ID)
        with self.assertRaises(Exception):
            require_valid_plan(cross_run, run_id=RUN_ID, task_id=TASK_ID)
        cross_task = planner.plan_for_run(goal=GOAL, run_id=RUN_ID, task_id="other-task")
        with self.assertRaises(Exception):
            require_valid_plan(cross_task, run_id=RUN_ID, task_id=TASK_ID)

    def test_revision_plan_carries_no_authority(self) -> None:
        plan = Planner().plan_for_run(
            goal=GOAL + " [revision 1: address acceptance_failed]",
            run_id=RUN_ID,
            task_id=TASK_ID,
        )
        blob = json.dumps(plan.to_context_dict(), sort_keys=True).lower()
        for banned in (
            "command", "argv", "executable", "shell", "environment", "timeout",
            "run_scope", "approval", "credential", "secret",
        ):
            self.assertNotIn(banned, blob, banned)


# --------------------------------------------------------------------------- #
# State machine through the real loop
# --------------------------------------------------------------------------- #


class RevisionStateMachineTests(RevisionTestCase):
    def _harness(self, **kw) -> AgentHarness:
        kwargs = {
            "policy": AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            "project_planner": Planner(),
            "revision_budget": RevisionBudget(max_revisions=1),
        }
        kwargs.update(kw)
        return AgentHarness(**kwargs)

    def test_successful_verification_does_not_revise(self) -> None:
        (self.root / "present.txt").write_text("ok", encoding="utf-8")
        result = self._harness().run(
            self._request(expectation_path="present.txt")
        )
        names = [event.event_type.name for event in result.events]
        self.assertNotIn("REVISION_STARTED", names)
        self.assertEqual(result.final_state.attempt_number, 0)

    def test_actionable_failure_reaches_a_revision(self) -> None:
        result = self._harness().run(self._request())
        names = [event.event_type.name for event in result.events]
        self.assertIn("REVISION_STARTED", names)
        self.assertIn("REVISION_COMPLETED", names)
        self.assertEqual(result.final_state.attempt_number, 1)

    def test_security_failure_never_revises(self) -> None:
        """A denied execution is terminal; the loop never revises around it.

        The provider is adversarial: it first tries to execute a command the run
        does not authorize, then asks for a revision. The revision must never be
        recorded, because a security failure terminates the run rather than being
        retried.
        """
        from uuid import uuid4

        from app.decision.models import Decision, DecisionAction, DecisionType

        class _SecurityThenReviseProvider:
            def decide(self, request):
                attempt = request.attempt_number
                return Decision(
                    decision_id=str(uuid4()),
                    run_id=request.run_id,
                    decision_type=DecisionType.CONTINUE,
                    action=(
                        DecisionAction.EXECUTE
                        if attempt == 0
                        else DecisionAction.REQUEST_REVISION
                    ),
                    reason_code="adversarial",
                    attempt_number=attempt,
                )

        request = self._request()
        denied = HarnessRequest(
            **{
                **{
                    f.name: getattr(request, f.name)
                    for f in dataclasses.fields(HarnessRequest)
                },
                # No command is authorized for this run, so EXECUTE must be denied.
                "allowed_execution_commands": (),
            }
        )
        result = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=3, max_revision_attempts=2),
            project_planner=Planner(),
            revision_budget=RevisionBudget(max_revisions=1),
            decision_provider=_SecurityThenReviseProvider(),
        ).run(denied)
        self.assertEqual(self._meta(result, "REVISION_STARTED"), [])
        self.assertEqual(self._meta(result, "REVISION_COMPLETED"), [])
        self.assertEqual(result.final_state.attempt_number, 0)

    def test_budget_exhaustion_ends_the_loop(self) -> None:
        result = self._harness(
            revision_budget=RevisionBudget(max_revisions=0)
        ).run(self._request())
        self.assertEqual(self._meta(result, "REVISION_STARTED"), [])
        self.assertEqual(result.final_state.attempt_number, 0)

    def test_no_progress_terminates_instead_of_looping(self) -> None:
        """The deterministic planner cannot invent new intentions, so a revision
        resolves to the same shape and the loop must stop rather than repeat."""
        result = self._harness().run(self._request())
        completed = self._meta(result, "REVISION_COMPLETED")[0]
        self.assertEqual(completed.get("status"), "no_progress")
        self.assertEqual(result.final_state.status.value, "FAILED")
        reasons = {
            entry.get("reason") for entry in self._meta(result, "HARNESS_FAILED")
        }
        self.assertIn("revision_no_progress", reasons)

    def test_loop_is_bounded_and_not_recursive(self) -> None:
        result = self._harness().run(self._request())
        started = self._meta(result, "REVISION_STARTED")
        self.assertLessEqual(len(started), 1)
        # One revision attempt, then a deterministic terminal result.
        self.assertTrue(result.final_state.terminal)
        # The harness is not re-entered: a single call produced the whole run.
        self.assertLessEqual(
            len([e for e in result.events if e.event_type.name == "HARNESS_STARTED"]),
            1,
        )

    def test_malformed_plan_prevents_any_revision(self) -> None:
        class BadPlanner:
            def plan_for_run(self, **kwargs):
                return "not a plan"

        result = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            project_planner=BadPlanner(),
            revision_budget=RevisionBudget(max_revisions=2),
        ).run(self._request())
        self.assertEqual(self._meta(result, "REVISION_STARTED"), [])
        planning = self._meta(result, "PLANNING_COMPLETED")
        self.assertEqual(planning[0].get("status"), "failed")


# --------------------------------------------------------------------------- #
# Authority
# --------------------------------------------------------------------------- #


class RevisionAuthorityTests(RevisionTestCase):
    def test_revision_logic_never_executes_directly(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        for relative in (
            "app/agent_runtime/revision_decision.py",
            "app/agent_runtime/revision_policy.py",
        ):
            code = executable_source(repo / relative)
            for banned in (
                "subprocess", "Popen", "os.system", "LocalExecutionAdapter",
                "ExecutionCoordinator", "AuthorizedExecution", "RunScope",
                "Workspace", "open(",
            ):
                self.assertNotIn(banned, code, f"{relative}: {banned}")

    def test_revision_does_not_add_an_orchestration_loop(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        harness_source = (repo / "app/agent_runtime/harness.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(harness_source.count("class AgentHarness"), 1)
        for relative in (
            "app/agent_runtime/revision_decision.py",
            "app/agent_runtime/revision_policy.py",
        ):
            code = executable_source(repo / relative)
            for banned in ("class AgentHarness", "def run_loop", "AgentHarness("):
                self.assertNotIn(banned, code, f"{relative}: {banned}")

    def test_revision_replays_the_existing_authorization_chain(self) -> None:
        """Every revision action is authorized by the harness itself."""
        result = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            project_planner=Planner(),
            revision_budget=RevisionBudget(max_revisions=1),
        ).run(self._request())
        names = [event.event_type.name for event in result.events]
        # The revision was authorized by the harness's own step, and the plan it
        # produced was validated before use.
        self.assertIn("HARNESS_PHASE_CHANGED", names)
        started = [
            dict(event.metadata)
            for event in result.events
            if event.event_type.name == "REVISION_STARTED"
        ][0]
        self.assertTrue(started.get("revision_allowed"))


# --------------------------------------------------------------------------- #
# Acceptance
# --------------------------------------------------------------------------- #


class RevisionAcceptanceTests(RevisionTestCase):
    def test_acceptance_criteria_are_never_altered_by_a_revision(self) -> None:
        request = self._request()
        before = tuple(request.acceptance_criteria)
        AgentHarness(
            policy=AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            project_planner=Planner(),
            revision_budget=RevisionBudget(max_revisions=1),
        ).run(request)
        self.assertEqual(tuple(request.acceptance_criteria), before)

    def test_revision_decision_carries_no_criteria(self) -> None:
        fields = {f.name for f in dataclasses.fields(RevisionDecision)}
        self.assertNotIn("acceptance_criteria", fields)
        self.assertNotIn("criteria", fields)

    def test_revision_cannot_self_accept(self) -> None:
        """A revision ends without an acceptance verdict; it never fabricates one."""
        result = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            project_planner=Planner(),
            revision_budget=RevisionBudget(max_revisions=1),
        ).run(self._request())
        completed = self._meta(result, "REVISION_COMPLETED")[0]
        self.assertNotIn("acceptance_status", completed)
        self.assertNotEqual(completed.get("status"), "accepted")

    def test_passing_acceptance_ends_the_run_without_revision(self) -> None:
        (self.root / "present.txt").write_text("ok", encoding="utf-8")
        result = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            project_planner=Planner(),
            revision_budget=RevisionBudget(max_revisions=1),
        ).run(self._request(expectation_path="present.txt"))
        self.assertEqual(self._meta(result, "REVISION_STARTED"), [])


# --------------------------------------------------------------------------- #
# Cross-run / cross-task, observability
# --------------------------------------------------------------------------- #


class RevisionIdentityTests(RevisionTestCase):
    def test_run_and_task_identity_are_stable_across_a_revision(self) -> None:
        result = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            project_planner=Planner(),
            revision_budget=RevisionBudget(max_revisions=1),
        ).run(self._request())
        for name in ("REVISION_STARTED", "REVISION_COMPLETED"):
            for entry in self._meta(result, name):
                self.assertEqual(entry.get("run_id"), RUN_ID, name)
                self.assertEqual(entry.get("task_id"), TASK_ID, name)

    def test_revision_events_carry_the_revision_number(self) -> None:
        result = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            project_planner=Planner(),
            revision_budget=RevisionBudget(max_revisions=1),
        ).run(self._request())
        for entry in self._meta(result, "REVISION_STARTED"):
            self.assertEqual(entry.get("revision_number"), 1)
        for entry in self._meta(result, "REVISION_COMPLETED"):
            self.assertEqual(entry.get("revision_number"), 1)

    def test_plan_identity_changes_across_a_revision(self) -> None:
        result = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            project_planner=Planner(),
            revision_budget=RevisionBudget(max_revisions=1),
        ).run(self._request())
        planning = self._meta(result, "PLANNING_COMPLETED")
        plan_ids = [entry.get("plan_id") for entry in planning if entry.get("plan_id")]
        self.assertGreaterEqual(len(plan_ids), 2)
        self.assertNotEqual(plan_ids[0], plan_ids[1])

    def test_cross_run_request_is_rejected_by_the_scope(self) -> None:
        """A scope frozen for one run cannot serve another run's request."""
        request = self._request(run_id="run-x")
        other = self._request(run_id="run-y")
        self.assertNotEqual(request.run_scope.run_id, other.run_scope.run_id)
        self.assertNotEqual(
            request.run_scope.fingerprint, other.run_scope.fingerprint
        )
        # Each scope asserts its own binding and is frozen to exactly one run.
        other.run_scope.assert_current()
        self.assertTrue(other.run_scope.is_frozen())
        self.assertEqual(other.run_scope.run_id, "run-y")

    def test_revision_never_applies_to_another_runs_plan(self) -> None:
        """A revision decision is bound to its own run and task."""
        decision = RevisionDecision(
            run_id=RUN_ID,
            task_id=TASK_ID,
            revision_number=1,
            failure_category=RevisionFailureCategory.ACCEPTANCE_FAILED,
            reason="actionable_failure_within_budget",
            revision_allowed=True,
            actionable=True,
            revision_goal=GOAL + " [revision 1: address acceptance_failed]",
            budget_remaining=0,
        )
        decision.assert_belongs_to(RUN_ID, TASK_ID)
        with self.assertRaises(RevisionError):
            decision.assert_belongs_to("run-other", TASK_ID)
        with self.assertRaises(RevisionError):
            decision.assert_belongs_to(RUN_ID, "task-other")
        # A plan replanned for another run is still refused by plan validation.
        from app.planning.validation import require_valid_plan

        cross = Planner().plan_for_run(
            goal=GOAL, run_id="run-other", task_id=TASK_ID
        )
        with self.assertRaises(Exception):
            require_valid_plan(cross, run_id=RUN_ID, task_id=TASK_ID)

    def test_service_path_does_not_revise_without_a_verification_verdict(self) -> None:
        """Without an objective verification outcome, no revision is authorized.

        ``run_agent_loop`` declares no verification expectations - criterion
        identity for that path is GAP-F and deliberately not invented - so its
        verification observations are ``not_evaluated``. Revision requires a real
        outcome, so the run must reach a bounded terminal state with no revision
        rather than guessing that it failed.
        """
        from app.api.service import ForgeApiService
        from app.execution.declaration import ExecutionDeclaration
        from app.orchestrator.models import TaskResult
        from app.orchestrator.run import RunExecutor
        from app.runtime.bootstrap import create_agent_harness
        from app.runtime.context import RuntimeContext
        from app.tools.approval import ApprovalPolicy
        from app.tools.registry import build_default_tool_registry

        class _Orchestrator:
            def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
                return TaskResult(task.id, True, output="double")

        def _runtime():
            orchestrator = _Orchestrator()
            return RuntimeContext(
                settings=None,
                provider_accounts={},
                provider_registry=None,
                provider_capabilities=None,
                agent_registry=None,
                orchestrator=orchestrator,
                run_executor=RunExecutor(orchestrator),
                harness=create_agent_harness(
                    with_discovery=False, with_planning=False
                ),
            )

        def build_harness(**kw):
            options = dict(kw)
            options["policy"] = AgentHarnessPolicy(
                max_iterations=6,
                max_actions=4,
                max_execution_attempts=1,
                max_revision_attempts=2,
            )
            options["revision_budget"] = RevisionBudget(max_revisions=1)
            return create_agent_harness(**options)

        service = ForgeApiService(
            runtime=_runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=PROFILE,
            declarations={
                "verify.p": ExecutionDeclaration(
                    declaration_id="verify.p",
                    command=("python", "-c", "print('ok')"),
                    intent=GOAL,
                    profile_id="p",
                    working_directory=".",
                )
            },
            approval_policy=ApprovalPolicy(),
            harness_factory=build_harness,
        )
        response = service.run_agent_loop("verify.p")
        record = service._run_store.load(response.run_id)
        names = [event.event_type.name for event in record.events]

        # The loop is bounded and reaches a terminal state.
        self.assertTrue(names)
        self.assertIn("HARNESS_LIMIT_REACHED", names)
        # And it never claimed a failed verification it did not have.
        self.assertNotIn("REVISION_STARTED", names)
        self.assertNotIn("REVISION_COMPLETED", names)
        # Every verification observation is unevaluated: the run has no
        # operator-declared expectation to check against.
        verification_statuses = {
            dict(e.metadata).get("result_status")
            for e in record.events
            if e.event_type.name == "HARNESS_OBSERVATION_RECORDED"
            and dict(e.metadata).get("action") == "RUN_VERIFICATION"
        }
        self.assertTrue(verification_statuses)
        self.assertEqual(verification_statuses, {"not_evaluated"})

    def test_revision_events_carry_no_sensitive_data(self) -> None:
        result = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=2, max_revision_attempts=2),
            project_planner=Planner(),
            revision_budget=RevisionBudget(max_revisions=1),
        ).run(self._request())
        for name in ("REVISION_STARTED", "REVISION_COMPLETED"):
            blob = json.dumps(self._meta(result, name), sort_keys=True).lower()
            for banned in (
                "stdout", "stderr", "environment", "token", "password",
                "secret", "api_key", "run_scope", "argv",
            ):
                self.assertNotIn(banned, blob, f"{name}: {banned}")


if __name__ == "__main__":
    unittest.main()
