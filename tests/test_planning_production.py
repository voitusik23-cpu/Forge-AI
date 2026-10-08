"""Production planning: declarative intention, never execution authority.

These tests pin the planning boundary:

* the ``Planner`` produces a deterministic, run-bound, immutable plan from a
  trusted goal, reusing the existing deterministic templates;
* a plan is an *intention* - it has no argv, executable, shell string,
  environment, timeout, or tool grant, and it cannot become an
  ``AuthorizedExecution`` or reach the coordinator, adapter, or filesystem;
* server-side validation rejects a plan that belongs to another run or task, is
  structurally broken, has duplicate/missing/self/cyclic dependencies, uses an
  unknown step type, or carries authority- or credential-shaped keys;
* ``AgentHarness`` remains the only orchestration loop, and planning is a stage
  inside it, between the observation and the first decision.

Real bounded behaviour runs against real temporary workspaces; no tracked
repository file is mutated.
"""

from __future__ import annotations

from pathlib import Path
import dataclasses
import inspect
import json
import tempfile
import unittest

from app.agent_runtime.harness import AgentHarness
from app.agent_runtime.models import HarnessRequest
from app.agent_runtime.policy import AgentHarnessPolicy
from app.agent_runtime.project_discovery import ProjectDiscovery
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.decision.models import Decision, DecisionAction, DecisionType
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.models import EventType, TaskResult
from app.orchestrator.run import RunExecutor
from app.planning.plan import (
    ALLOWED_STEP_TYPES,
    FORBIDDEN_PLAN_KEYS,
    ExecutionPlan,
    PlanStep,
    PlanStepType,
    PlanValidationError,
)
from app.planning.planner import Planner
from app.planning.validation import (
    require_valid_plan,
    validate_plan,
)
from app.runtime.bootstrap import create_agent_harness
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope
from app.tasks.specification import TaskSpecification
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.registry import build_default_tool_registry
from app.tools.workspace import Workspace

DECLARATION_ID = "verify.planning"
GOAL = "build a web application"
RUN_ID = "run-plan-1"
TASK_ID = "task-plan-1"


def api_profile(**overrides) -> ProjectExecutionProfile:
    payload = {
        "profile_id": "api-default",
        "allowed_commands": ("python",),
        "capabilities": frozenset({ExecutionCapability.INTERPRET_TEXT}),
        "network_access": False,
        "working_directory": ".",
    }
    payload.update(overrides)
    return ProjectExecutionProfile(**payload)


def step(step_id="s1", step_type=PlanStepType.OBSERVE, depends_on=(), **kw):
    payload = {
        "step_id": step_id,
        "step_type": step_type,
        "purpose": f"purpose of {step_id}",
        "depends_on": tuple(depends_on),
    }
    payload.update(kw)
    return PlanStep(**payload)


def plan(*steps, run_id=RUN_ID, task_id=TASK_ID, metadata=None, **kw):
    return ExecutionPlan(
        plan_id=kw.pop("plan_id", "plan-test"),
        run_id=run_id,
        task_id=task_id,
        steps=tuple(steps),
        metadata=metadata or {},
        **kw,
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


class _OrchestratorDouble:
    def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
        return TaskResult(task.id, True, output="double")


def _runtime(**overrides) -> RuntimeContext:
    orchestrator = _OrchestratorDouble()
    payload = {
        "settings": None,
        "provider_accounts": {},
        "provider_registry": None,
        "provider_capabilities": None,
        "agent_registry": None,
        "orchestrator": orchestrator,
        "run_executor": RunExecutor(orchestrator),
        "harness": create_agent_harness(with_discovery=False, with_planning=False),
    }
    payload.update(overrides)
    return RuntimeContext(**payload)


class _CapturingDecisionProvider:
    def __init__(self, action: DecisionAction = DecisionAction.EXECUTE) -> None:
        self.action = action
        self.requests: list[object] = []

    def decide(self, request):
        self.requests.append(request)
        return Decision(
            decision_id="capture",
            run_id=request.run_id,
            decision_type=DecisionType.CONTINUE,
            action=self.action,
            reason_code="capture",
        )


class PlannerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "README.md").write_text("# demo\n", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _service(self, *, capture=None, planner=None, intent=GOAL, **kwargs):
        provider = capture or _CapturingDecisionProvider()
        factory_kwargs = {"decision_provider": provider}
        if planner is not None:
            factory_kwargs["project_planner"] = planner
        return ForgeApiService(
            runtime=_runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=api_profile(),
            declarations={
                DECLARATION_ID: ExecutionDeclaration(
                    declaration_id=DECLARATION_ID,
                    command=("python", "-c", "print('ok')"),
                    intent=intent,
                    profile_id="api-default",
                    working_directory=".",
                )
            },
            approval_policy=ApprovalPolicy(),
            harness_factory=lambda **kw: create_agent_harness(**factory_kwargs, **kw),
            **kwargs,
        )

    @staticmethod
    def _events(service, response, name):
        record = service._run_store.load(response.run_id)
        return [e for e in record.events if e.event_type.name == name]


class PlanModelTests(PlannerTestCase):
    def test_plan_is_immutable(self) -> None:
        subject = plan(step())
        self.assertTrue(ExecutionPlan.__dataclass_params__.frozen)
        self.assertTrue(PlanStep.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            subject.steps = ()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            subject.steps[0].purpose = "other"

    def test_step_type_taxonomy_is_closed(self) -> None:
        self.assertEqual(
            {t.value for t in ALLOWED_STEP_TYPES},
            {"observe", "modify", "verify", "accept"},
        )
        with self.assertRaises(PlanValidationError):
            step(step_type="run_command")
        with self.assertRaises(PlanValidationError):
            step(step_type="deploy")

    def test_plan_requires_identity(self) -> None:
        for bad in ("", "   ", None):
            with self.assertRaises(PlanValidationError):
                plan(step(), run_id=bad)  # type: ignore[arg-type]
            with self.assertRaises(PlanValidationError):
                plan(step(), task_id=bad)  # type: ignore[arg-type]

    def test_plan_requires_steps(self) -> None:
        with self.assertRaises(PlanValidationError):
            plan()

    def test_plan_carries_no_authority_fields(self) -> None:
        plan_fields = {f.name for f in dataclasses.fields(ExecutionPlan)}
        step_fields = {f.name for f in dataclasses.fields(PlanStep)}
        for banned in (
            "command", "argv", "executable", "shell", "environment", "timeout",
            "run_scope", "authorized_execution", "approval", "capabilities",
            "allowed_tool_ids", "workspace", "profile",
        ):
            self.assertNotIn(banned, plan_fields, banned)
            self.assertNotIn(banned, step_fields, banned)

    def test_forbidden_keys_are_rejected_at_construction(self) -> None:
        for key in ("command", "argv", "executable", "environment", "timeout",
                    "run_scope", "approval_policy", "credentials", "api_key"):
            with self.assertRaises(PlanValidationError):
                step(metadata={key: "x"})
            with self.assertRaises(PlanValidationError):
                plan(step(), metadata={key: "x"})

    def test_step_self_dependency_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            step("s1", depends_on=("s1",))

    def test_fingerprint_is_deterministic_and_identity_bound(self) -> None:
        a = Planner().plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        b = Planner().plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        c = Planner().plan_for_run(goal=GOAL, run_id="other-run", task_id=TASK_ID)
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertNotEqual(a.fingerprint, c.fingerprint)

    def test_fingerprint_changes_when_content_changes(self) -> None:
        base = plan(step("s1"))
        changed = plan(step("s1", purpose="different"))
        self.assertNotEqual(base.fingerprint, changed.fingerprint)

    def test_context_dict_is_bounded(self) -> None:
        subject = Planner().plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        payload = json.dumps(subject.to_context_dict(), sort_keys=True)
        for banned in FORBIDDEN_PLAN_KEYS:
            self.assertNotIn(f'"{banned}"', payload, banned)


class PlannerGenerationTests(PlannerTestCase):
    def test_plan_for_run_is_deterministic_and_ordered(self) -> None:
        first = Planner().plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        second = Planner().plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        self.assertEqual(first.step_ids, second.step_ids)
        self.assertEqual(first.fingerprint, second.fingerprint)
        report = validate_plan(first, run_id=RUN_ID, task_id=TASK_ID)
        self.assertTrue(report.valid)
        self.assertEqual(set(report.order), set(first.step_ids))

    def test_dependencies_are_acyclic_and_resolvable(self) -> None:
        subject = Planner().plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        ids = set(subject.step_ids)
        for item in subject.steps:
            for dependency in item.depends_on:
                self.assertIn(dependency, ids)
                self.assertNotEqual(dependency, item.step_id)
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertEqual(report.order[0], subject.steps[0].step_id)

    def test_plan_uses_only_known_step_types(self) -> None:
        for goal in (GOAL, "make a telegram bot", "analyze sales data", "vague goal"):
            subject = Planner().plan_for_run(goal=goal, run_id=RUN_ID, task_id=TASK_ID)
            for item in subject.steps:
                self.assertIn(item.step_type, ALLOWED_STEP_TYPES)

    def test_plan_has_no_command_like_content(self) -> None:
        subject = Planner().plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=TASK_ID)
        blob = json.dumps(subject.to_context_dict(), sort_keys=True).lower()
        for banned in ("rm -rf", "subprocess", "popen", "shell", "bash", "cmd.exe",
                       ".exe", "sudo", "&&", "|", ";"):
            self.assertNotIn(banned, blob, banned)

    def test_empty_or_missing_goal_is_refused(self) -> None:
        for bad in ("", "   ", None, 7):
            with self.assertRaises(ValueError):
                Planner().create_plan(bad)  # type: ignore[arg-type]

    def test_plan_for_run_requires_identity(self) -> None:
        for bad in ("", "   ", None):
            with self.assertRaises(ValueError):
                Planner().plan_for_run(goal=GOAL, run_id=bad, task_id=TASK_ID)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                Planner().plan_for_run(goal=GOAL, run_id=RUN_ID, task_id=bad)  # type: ignore[arg-type]

    def test_planner_has_no_io_or_execution_surface(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        code = executable_source(repo / "app/planning/planner.py")
        for banned in (
            "subprocess", "Popen", "os.system", "open(", "Path(",
            "ExecutionCoordinator", "LocalExecutionAdapter", "AuthorizedExecution",
            "RunScope", "Workspace", "shutil", "socket", "requests",
        ):
            self.assertNotIn(banned, code, banned)


class PlanValidationTests(PlannerTestCase):
    def test_cross_run_plan_is_rejected(self) -> None:
        subject = plan(step(), run_id="other-run")
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertFalse(report.valid)
        self.assertIn("plan_identity_mismatch", report.codes)
        with self.assertRaises(PlanValidationError):
            require_valid_plan(subject, run_id=RUN_ID, task_id=TASK_ID)

    def test_task_mismatch_is_rejected(self) -> None:
        subject = plan(step(), task_id="other-task")
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertFalse(report.valid)
        with self.assertRaises(PlanValidationError):
            require_valid_plan(subject, run_id=RUN_ID, task_id=TASK_ID)

    def test_non_plan_object_is_rejected(self) -> None:
        for bad in (None, {}, "plan", 7, []):
            report = validate_plan(bad, run_id=RUN_ID, task_id=TASK_ID)
            self.assertFalse(report.valid)
            self.assertIn("invalid_plan_type", report.codes)

    def test_duplicate_step_ids_are_rejected(self) -> None:
        subject = plan(step("s1"), step("s1"))
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertFalse(report.valid)
        self.assertIn("duplicate_step_id", report.codes)

    def test_unknown_dependency_is_rejected(self) -> None:
        subject = plan(step("s1", depends_on=("ghost",)))
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertFalse(report.valid)
        self.assertIn("unknown_dependency", report.codes)

    def test_self_dependency_is_rejected(self) -> None:
        # Construction already refuses it; validation must also refuse a mutated
        # object that bypassed construction.
        subject = plan(step("s1"))
        object.__setattr__(subject.steps[0], "depends_on", ("s1",))
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertFalse(report.valid)
        self.assertIn("self_dependency", report.codes)

    def test_dependency_cycle_is_rejected(self) -> None:
        subject = plan(
            step("s1", depends_on=("s2",)),
            step("s2", depends_on=("s1",)),
        )
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertFalse(report.valid)
        self.assertIn("dependency_cycle", report.codes)
        with self.assertRaises(PlanValidationError):
            require_valid_plan(subject, run_id=RUN_ID, task_id=TASK_ID)

    def test_forbidden_step_type_is_rejected(self) -> None:
        subject = plan(step("s1"))
        object.__setattr__(subject.steps[0], "step_type", "deploy")
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertFalse(report.valid)
        self.assertIn("forbidden_step_type", report.codes)

    def test_authority_key_in_metadata_is_rejected(self) -> None:
        subject = plan(step("s1"))
        object.__setattr__(subject.steps[0], "metadata", {"run_scope": "x"})
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertFalse(report.valid)
        self.assertIn("authority_leak", report.codes)

    def test_plan_level_authority_key_is_rejected(self) -> None:
        subject = plan(step("s1"))
        object.__setattr__(subject, "metadata", {"approval_policy": "x"})
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertFalse(report.valid)
        self.assertIn("authority_leak", report.codes)

    def test_valid_plan_returns_deterministic_order(self) -> None:
        subject = plan(
            step("s1"),
            step("s2", depends_on=("s1",)),
            step("s3", depends_on=("s2",)),
        )
        report = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertTrue(report.valid)
        self.assertEqual(report.order, ("s1", "s2", "s3"))
        again = validate_plan(subject, run_id=RUN_ID, task_id=TASK_ID)
        self.assertEqual(again.order, report.order)


class PlanningHarnessIntegrationTests(PlannerTestCase):
    def _request(self, run_id: str, *, goal: str = GOAL):
        scope = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=api_profile(),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(
                AcceptanceCriterion(criterion_id="c", description="d"),
            ),
        )
        scope.freeze()
        return HarnessRequest(
            run_id=run_id,
            workspace=self.workspace,
            task_specification=TaskSpecification(
                task_id=TASK_ID,
                title="planning task",
                description=goal,
                requirements=(),
                acceptance_criteria=(
                    AcceptanceCriterion(criterion_id="c", description="d"),
                ),
            ),
            allowed_execution_commands=("python",),
            acceptance_criteria=(AcceptanceCriterion(criterion_id="c", description="d"),),
            run_scope=scope,
            metadata={"task_id": TASK_ID},
        )

    def test_harness_without_planner_emits_no_planning_events(self) -> None:
        harness = AgentHarness(policy=AgentHarnessPolicy(max_actions=1))
        result = harness.run(self._request("no-planning"))
        names = [e.event_type.name for e in result.events]
        self.assertNotIn("PLANNING_STARTED", names)
        self.assertNotIn("PLANNING_COMPLETED", names)

    def test_harness_plans_before_the_first_decision(self) -> None:
        harness = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=1), project_planner=Planner()
        )
        result = harness.run(self._request("with-planning"))
        names = [e.event_type.name for e in result.events]
        self.assertIn("PLANNING_STARTED", names)
        self.assertIn("PLANNING_COMPLETED", names)
        self.assertLess(
            names.index("PLANNING_COMPLETED"), names.index("DECISION_REQUESTED")
        )

    def test_planning_event_metadata_is_bounded(self) -> None:
        harness = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=1), project_planner=Planner()
        )
        result = harness.run(self._request("bounded-meta"))
        completed = [
            e for e in result.events if e.event_type.name == "PLANNING_COMPLETED"
        ][0]
        metadata = dict(completed.metadata)
        self.assertEqual(metadata.get("status"), "completed")
        self.assertIn("plan_fingerprint", metadata)
        self.assertIn("step_types", metadata)
        self.assertIn("step_count", metadata)
        for banned in ("command", "argv", "environment", "stdout", "stderr"):
            self.assertNotIn(banned, metadata)

    def test_planning_belongs_to_the_same_run_and_task(self) -> None:
        harness = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=1), project_planner=Planner()
        )
        result = harness.run(self._request("identity"))
        completed = [
            e for e in result.events if e.event_type.name == "PLANNING_COMPLETED"
        ][0]
        self.assertEqual(completed.metadata.get("run_id"), "identity")
        self.assertEqual(completed.metadata.get("task_id"), TASK_ID)

    def test_no_goal_skips_planning_without_fabricating_a_plan(self) -> None:
        harness = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=1), project_planner=Planner()
        )
        request = self._request("no-goal", goal="")
        # Remove every goal-bearing field: with no descriptive text there is no
        # goal, and planning must skip rather than invent one.
        object.__setattr__(request.task_specification, "title", "")
        object.__setattr__(request.task_specification, "description", "")
        result = harness.run(request)
        completed = [
            e for e in result.events if e.event_type.name == "PLANNING_COMPLETED"
        ][0]
        self.assertEqual(completed.metadata.get("status"), "failed")
        self.assertEqual(completed.metadata.get("failure_category"), "no_goal")
        self.assertNotIn("plan_fingerprint", completed.metadata)

    def test_broken_planner_does_not_abort_the_loop(self) -> None:
        class BrokenPlanner:
            def plan_for_run(self, **kwargs):
                raise RuntimeError("planner exploded")

        harness = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=1), project_planner=BrokenPlanner()
        )
        result = harness.run(self._request("broken-planner"))
        completed = [
            e for e in result.events if e.event_type.name == "PLANNING_COMPLETED"
        ][0]
        self.assertEqual(completed.metadata.get("status"), "failed")
        self.assertEqual(completed.metadata.get("failure_category"), "RuntimeError")

    def test_invalid_plan_from_a_planner_is_reported_not_used(self) -> None:
        class BadPlanPlanner:
            def plan_for_run(self, **kwargs):
                return "not a plan"

        harness = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=1), project_planner=BadPlanPlanner()
        )
        result = harness.run(self._request("bad-plan"))
        completed = [
            e for e in result.events if e.event_type.name == "PLANNING_COMPLETED"
        ][0]
        self.assertEqual(completed.metadata.get("status"), "failed")
        self.assertNotIn("plan_fingerprint", completed.metadata)
        # No plan reached the loop: the decision ran on a request with no plan
        # context, and no execution was authorized.
        self.assertEqual(result.execution_results, ())
        observations = [obs.result_status for obs in result.observations]
        self.assertNotIn("EXECUTION_SUCCESS", observations)
        final = result.final_state.status.value
        self.assertNotEqual(final, "COMPLETED")

    def test_planning_events_are_registered(self) -> None:
        self.assertEqual(EventType.PLANNING_STARTED.value, "planning_started")
        self.assertEqual(EventType.PLANNING_COMPLETED.value, "planning_completed")

    def test_planning_adds_no_orchestration_path(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        for relative in ("app/planning/plan.py", "app/planning/validation.py"):
            code = executable_source(repo / relative)
            for banned in (
                "class AgentHarness", "def run_loop", "ExecutionCoordinator(",
                "LocalExecutionAdapter(", "AuthorizedExecution", "subprocess",
            ):
                self.assertNotIn(banned, code, f"{relative}: {banned}")
        harness_source = (repo / "app/agent_runtime/harness.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(harness_source.count("class AgentHarness"), 1)


class PlanningProductionPathTests(PlannerTestCase):
    def test_production_loop_plans_and_reaches_the_decision(self) -> None:
        """A/H: discovery, context, planning, decision share one run."""
        capture = _CapturingDecisionProvider()
        service = self._service(capture=capture)
        response = service.run_agent_loop(DECLARATION_ID)

        started = self._events(service, response, "PLANNING_STARTED")
        completed = self._events(service, response, "PLANNING_COMPLETED")
        self.assertEqual(len(started), 1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].metadata.get("status"), "completed")

        envelope = capture.requests[-1].context_envelope
        metadata = dict(getattr(envelope, "metadata", {}) or {})
        self.assertIn("plan", metadata)
        plan_payload = metadata["plan"]
        self.assertEqual(plan_payload.get("run_id"), response.run_id)
        self.assertEqual(plan_payload.get("task_id"), response.task_id)
        self.assertTrue(plan_payload.get("steps"))

    def test_plan_reaching_the_decision_carries_no_authority(self) -> None:
        """C/E/F/G: the planning context is intention only."""
        capture = _CapturingDecisionProvider()
        service = self._service(capture=capture)
        service.run_agent_loop(DECLARATION_ID)
        envelope = capture.requests[-1].context_envelope
        plan_payload = dict(getattr(envelope, "metadata", {}) or {})["plan"]
        blob = json.dumps(plan_payload, sort_keys=True).lower()
        for banned in (
            "command", "argv", "executable", "shell", "environment", "timeout",
            "run_scope", "approval", "credential", "secret", "token",
            "authorizedexecution", "allowed_execution_commands",
        ):
            self.assertNotIn(banned, blob, banned)
        # The decision request still carries no authority of its own.
        fields = {f.name for f in dataclasses.fields(capture.requests[-1])}
        for banned in ("run_scope", "workspace", "approval_policy",
                       "allowed_execution_commands", "execution_requests"):
            self.assertNotIn(banned, fields, banned)

    def test_plan_cannot_bypass_the_execution_coordinator(self) -> None:
        """19: the plan is not an execution path."""
        repo = Path(__file__).resolve().parents[1]
        for relative in (
            "app/planning/plan.py",
            "app/planning/planner.py",
            "app/planning/validation.py",
        ):
            code = executable_source(repo / relative)
            self.assertNotIn("AuthorizedExecution.create", code, relative)
            self.assertNotIn("coordinator.execute", code, relative)
            self.assertNotIn("subprocess", code, relative)

    def test_declared_intent_is_the_trusted_goal(self) -> None:
        capture = _CapturingDecisionProvider()
        service = self._service(capture=capture, intent="analyze sales data")
        service.run_agent_loop(DECLARATION_ID)
        plan_payload = dict(
            getattr(capture.requests[-1].context_envelope, "metadata", {}) or {}
        )["plan"]
        self.assertEqual(plan_payload.get("plan_id"), Planner().create_plan("analyze sales data").plan_id)

    def test_empty_intent_yields_no_plan_but_still_runs(self) -> None:
        capture = _CapturingDecisionProvider()
        service = self._service(capture=capture, intent="")
        response = service.run_agent_loop(DECLARATION_ID)
        completed = self._events(service, response, "PLANNING_COMPLETED")
        self.assertNotEqual(completed[0].metadata.get("status"), "completed")
        metadata = dict(
            getattr(capture.requests[-1].context_envelope, "metadata", {}) or {}
        )
        self.assertNotIn("plan", metadata)

    def test_execution_still_passes_the_authority_chain(self) -> None:
        capture = _CapturingDecisionProvider()
        service = self._service(capture=capture)
        response = service.run_agent_loop(DECLARATION_ID)
        names = [
            e.event_type.name for e in service._run_store.load(response.run_id).events
        ]
        self.assertIn("EXECUTION_POLICY_CHECKED", names)
        self.assertIn("EXECUTION_STARTED", names)


class CompatibilityTests(PlannerTestCase):
    def test_run_task_is_unchanged(self) -> None:
        service = self._service()
        response = service.run_task(TaskRunRequest(description="normal task"))
        self.assertTrue(response.run_id.startswith("run-api-"), response.run_id)

    def test_declared_verification_is_unchanged(self) -> None:
        service = self._service()
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertTrue(response.success, response.error)

    def test_run_agent_loop_still_executes(self) -> None:
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        self.assertTrue(response.run_id.startswith("run-loop-"))

    def test_execution_declaration_defaults_intent_to_empty(self) -> None:
        declaration = ExecutionDeclaration(
            declaration_id="d", command=("python",), profile_id="p"
        )
        self.assertEqual(declaration.intent, "")

    def test_taskrunrequest_has_no_planning_authority(self) -> None:
        fields = {f.name for f in dataclasses.fields(TaskRunRequest)}
        for banned in ("plan", "plan_id", "steps", "goal", "intent", "command"):
            self.assertNotIn(banned, fields)


if __name__ == "__main__":
    unittest.main()
