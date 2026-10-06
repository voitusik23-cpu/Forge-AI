"""Integration tests for ProjectExecutionProfile propagation through Engineering Run."""

import tempfile
import unittest
from pathlib import Path

from app.agents.registry import AgentRegistry
from app.execution.profile import (
    ExecutionEnvironmentType,
    ProjectExecutionProfile,
    TargetOS,
)
from app.runtime.run_scope import RunScope
from app.tools.workspace import Workspace
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunStatus,
)
from app.orchestrator.models import EventType, Task, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.revision import RevisionLoopExecutor
from app.orchestrator.run import RunExecutor
from app.tasks.specification import (
    InvalidTaskSpecificationError,
    Requirement,
    TaskSpecification,
)
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy, ApprovalState
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import VerificationExpectation


class _StubAgent:
    name = "stub_agent"
    provider_name = "mock"

    def run(self, task: Task) -> TaskResult:
        return TaskResult(task.id, True, output="ok")


class TestEngineeringExecutionProfile(unittest.TestCase):
    def setUp(self) -> None:
        # Each test is its own Run with its own perimeter.
        RunScope.release_all()
        self._run_seq = 0
        self.agent = _StubAgent()
        self.agent_registry = AgentRegistry()
        self.agent_registry.register(self.agent)
        self.tool_registry = ToolRegistry()
        self.tool_executor = ToolExecutor(
            self.tool_registry,
            approval_policy=ApprovalPolicy(ApprovalState.APPROVED),
        )
        self.orchestrator = Orchestrator(self.agent_registry)
        self.run_executor = RunExecutor(
            self.orchestrator,
            tool_executor=self.tool_executor,
        )
        self.revision_executor = RevisionLoopExecutor(self.run_executor)
        self.engineering_executor = EngineeringRunExecutor(self.revision_executor)
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name))
        (Path(self.temp.name) / "result.txt").write_text("ok", encoding="utf-8")

        self.req = Requirement(requirement_id="req-1", description="Implement test feature")
        self.crit = AcceptanceCriterion(
            criterion_id="crit-1",
            requirement_id="req-1",
            description="Feature is verified",
        )
        self.expectations = {
            "crit-1": VerificationExpectation(relative_path="result.txt", exists=True)
        }
        self.profile = ProjectExecutionProfile(
            profile_id="py-env-1",
            environment_type=ExecutionEnvironmentType.VIRTUALENV,
            runtime_name="python",
            runtime_version="3.11",
            target_os=TargetOS.LINUX,
            allowed_commands=("pytest", "python"),
        )

    def tearDown(self) -> None:
        RunScope.release_all()
        self.temp.cleanup()

    def _scope_for(self, profile):
        """Freeze this Run's single scope from an explicit execution profile.

        The run identity is established here, before the scope exists, and the
        scope carries no execution-command authority because these Runs exercise
        the profile/revision path rather than the command path.
        """
        self._run_seq += 1
        scope = RunScope(
            run_id=f"eng-profile-run-{self._run_seq}",
            workspace=self.workspace,
            execution_profile=profile,
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset(),
            acceptance_criteria=(self.crit,),
        )
        scope.freeze()
        return scope

    def test_task_specification_with_valid_execution_profile(self) -> None:
        spec = TaskSpecification(
            task_id="task-1",
            title="Feature Task",
            description="Task with profile",
            requirements=(self.req,),
            acceptance_criteria=(self.crit,),
            execution_profile=self.profile,
        )
        val = spec.validate()
        self.assertTrue(val.valid)
        context_data = spec.to_context_data()
        self.assertIn("execution_profile", context_data)
        self.assertEqual(context_data["execution_profile"]["profile_id"], "py-env-1")  # type: ignore

    def test_task_specification_with_invalid_execution_profile(self) -> None:
        bad_profile = ProjectExecutionProfile(
            profile_id="",  # empty
            timeout_seconds=-10,
        )
        spec = TaskSpecification(
            task_id="task-2",
            title="Feature Task",
            description="Task with bad profile",
            requirements=(self.req,),
            acceptance_criteria=(self.crit,),
            execution_profile=bad_profile,
        )
        val = spec.validate()
        self.assertFalse(val.valid)
        self.assertIn("execution_profile_profile_id_required", val.errors)
        self.assertIn("execution_profile_invalid_timeout_seconds", val.errors)

    def test_engineering_run_with_task_specification_profile(self) -> None:
        spec = TaskSpecification(
            task_id="task-run-1",
            title="Feature Task",
            description="Run with profile",
            requirements=(self.req,),
            acceptance_criteria=(self.crit,),
            execution_profile=self.profile,
        )
        request = EngineeringRunRequest(
            task_specification=spec,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            run_scope=self._scope_for(spec.execution_profile),
        )
        result = self.engineering_executor.execute(request)
        self.assertEqual(result.final_status, EngineeringRunStatus.SUCCESS)
        self.assertIsNotNone(result.execution_profile)
        self.assertEqual(result.execution_profile.profile_id, "py-env-1")  # type: ignore

        # Check that ENGINEERING_RUN_STARTED event has profile_id
        start_event = next(e for e in result.run.events if e.type == EventType.ENGINEERING_RUN_STARTED)
        self.assertEqual(start_event.data.get("profile_id"), "py-env-1")

    def test_engineering_run_with_legacy_task_and_profile(self) -> None:
        task = Task(id="legacy-1", description="Legacy run task")
        request = EngineeringRunRequest(
            task=task,
            acceptance_criteria=(self.crit,),
            execution_profile=self.profile,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            run_scope=self._scope_for(self.profile),
        )
        result = self.engineering_executor.execute(request)
        self.assertEqual(result.final_status, EngineeringRunStatus.SUCCESS)
        self.assertIsNotNone(result.execution_profile)
        self.assertEqual(result.execution_profile.profile_id, "py-env-1")  # type: ignore
        self.assertIn("execution_profile", task.context)

    def test_conflicting_profiles_rejected(self) -> None:
        profile_a = ProjectExecutionProfile(profile_id="env-a")
        profile_b = ProjectExecutionProfile(profile_id="env-b")
        spec = TaskSpecification(
            task_id="task-conflict",
            title="Conflict Task",
            description="Conflict",
            requirements=(self.req,),
            acceptance_criteria=(self.crit,),
            execution_profile=profile_a,
        )
        request = EngineeringRunRequest(
            task_specification=spec,
            execution_profile=profile_b,
            run_scope=self._scope_for(self.profile),
        )
        with self.assertRaises(ValueError) as ctx:
            self.engineering_executor.execute(request)
        self.assertIn("Conflicting execution_profile", str(ctx.exception))

    def test_legacy_invalid_profile_rejected(self) -> None:
        bad_profile = ProjectExecutionProfile(profile_id="")
        task = Task(id="legacy-bad", description="Legacy bad profile")
        request = EngineeringRunRequest(
            task=task,
            acceptance_criteria=(self.crit,),
            execution_profile=bad_profile,
            run_scope=self._scope_for(self.profile),
        )
        with self.assertRaises(ValueError) as ctx:
            self.engineering_executor.execute(request)
        self.assertIn("Invalid execution_profile", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
