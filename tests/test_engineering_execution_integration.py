"""Integration tests for Engineering Run, Execution, and Verification integration."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from app.agents.registry import AgentRegistry
from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.policy import ExecutionPolicy
from app.execution.profile import (
    ExecutionEnvironmentType,
    ProjectExecutionProfile,
    TargetOS,
)
from app.execution.redaction import DefaultSecretRedactor, REDACTED_PLACEHOLDER
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunStatus,
)
from app.orchestrator.models import EventType, RunState, Task, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.revision import RevisionLoopExecutor
from app.orchestrator.run import RunExecutor
from app.tasks.specification import Requirement, TaskSpecification
from app.tools.acceptance import AcceptanceCriterion, AcceptanceStatus
from app.tools.approval import ApprovalPolicy, ApprovalState
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
    create_execution_verification_evidence,
)
from app.tools.workspace import Workspace


class _StubAgent:
    name = "stub_agent"
    provider_name = "fixture"

    def run(self, task: Task) -> TaskResult:
        return TaskResult(task.id, True, output="ok")


class _TrackingResolver:
    def __init__(self, decision=ApprovalState.APPROVED):
        self.decision = decision
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return self.decision


class TestEngineeringExecutionIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp.name)
        self.workspace = Workspace(self.workspace_root)
        (self.workspace_root / "result.txt").write_text("ok", encoding="utf-8")

        self.agent = _StubAgent()
        self.agent_registry = AgentRegistry()
        self.agent_registry.register(self.agent)
        self.tool_registry = ToolRegistry()
        self.tool_executor = ToolExecutor(
            self.tool_registry,
            approval_policy=ApprovalPolicy(),
        )
        self.orchestrator = Orchestrator(self.agent_registry, default_provider="fixture")
        self.run_executor = RunExecutor(
            self.orchestrator,
            tool_executor=self.tool_executor,
        )
        self.revision_executor = RevisionLoopExecutor(self.run_executor)
        self.engineering_executor = EngineeringRunExecutor(self.revision_executor)

        self.profile = ProjectExecutionProfile(
            profile_id="py-env-stage4",
            environment_type=ExecutionEnvironmentType.HOST,
            runtime_name="python",
            target_os=TargetOS.WINDOWS if sys.platform == "win32" else TargetOS.LINUX,
            allowed_commands=(sys.executable, "python", "python.exe"),
            timeout_seconds=10.0,
        )

        self.req = Requirement(requirement_id="req-1", description="Implement test execution")
        self.crit = AcceptanceCriterion(
            criterion_id="crit-1",
            requirement_id="req-1",
            description="File verification",
        )
        self.expectations = {
            "crit-1": VerificationExpectation(relative_path="result.txt", exists=True)
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_successful_execution_in_engineering_run(self) -> None:
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('run_ok'); sys.exit(0)",
            ),
            profile=self.profile,
        )
        request = EngineeringRunRequest(
            task=Task(id="run-exec-1", description="Run with execution request"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )
        result = self.engineering_executor.execute(request)

        self.assertEqual(result.final_status, EngineeringRunStatus.SUCCESS)
        self.assertEqual(len(result.execution_results), 1)
        exec_res = result.execution_results[0]
        self.assertEqual(exec_res.status, ExecutionStatus.SUCCESS)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(exec_res.exit_code, 0)
        self.assertEqual(exec_res.stdout, "run_ok")

        event_types = [e.type for e in result.run.events]
        self.assertIn(EventType.EXECUTION_REQUESTED, event_types)
        self.assertIn(EventType.EXECUTION_POLICY_CHECKED, event_types)
        self.assertIn(EventType.EXECUTION_STARTED, event_types)
        self.assertIn(EventType.EXECUTION_COMPLETED, event_types)

    def test_permission_denied_prevents_subprocess(self) -> None:
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.exit(0)",
            ),
            profile=self.profile,
        )
        request = EngineeringRunRequest(
            task=Task(id="run-exec-denied", description="Permission denied run"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=("some_disallowed_command",),
        )
        result = self.engineering_executor.execute(request)

        self.assertEqual(len(result.execution_results), 1)
        exec_res = result.execution_results[0]
        self.assertEqual(exec_res.status, ExecutionStatus.DENIED)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.PERMISSION_DENIED)
        self.assertIn("permission check", exec_res.stderr.lower())

        event_types = [e.type for e in result.run.events]
        self.assertIn(EventType.EXECUTION_REQUESTED, event_types)
        self.assertIn(EventType.EXECUTION_DENIED, event_types)
        self.assertNotIn(EventType.EXECUTION_STARTED, event_types)
        self.assertNotIn(EventType.EXECUTION_COMPLETED, event_types)

        denied_event = next(e for e in result.run.events if e.type == EventType.EXECUTION_DENIED)
        self.assertEqual(denied_event.data.get("reason"), "permission_denied")

    def test_approval_waiting_prevents_subprocess(self) -> None:
        exec_req = ExecutionRequest(
            command=(sys.executable, "-c", "import sys; sys.exit(0)"),
            profile=self.profile,
        )
        resolver = _TrackingResolver(decision=ApprovalState.REQUIRED)
        request = EngineeringRunRequest(
            task=Task(id="run-exec-waiting", description="Approval waiting run"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
        )
        result = self.engineering_executor.execute(request)

        self.assertEqual(result.final_status, EngineeringRunStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(result.run.state, RunState.WAITING_FOR_APPROVAL)
        self.assertEqual(len(result.execution_results), 1)
        exec_res = result.execution_results[0]
        self.assertEqual(exec_res.status, ExecutionStatus.DENIED)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)

        event_types = [e.type for e in result.run.events]
        self.assertNotIn(EventType.EXECUTION_STARTED, event_types)
        self.assertNotIn(EventType.EXECUTION_COMPLETED, event_types)

    def test_approval_rejected_prevents_subprocess(self) -> None:
        exec_req = ExecutionRequest(
            command=(sys.executable, "-c", "import sys; sys.exit(0)"),
            profile=self.profile,
        )
        resolver = _TrackingResolver(decision=ApprovalState.REJECTED)
        request = EngineeringRunRequest(
            task=Task(id="run-exec-rejected", description="Approval rejected run"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
        )
        result = self.engineering_executor.execute(request)

        self.assertEqual(len(result.execution_results), 1)
        exec_res = result.execution_results[0]
        self.assertEqual(exec_res.status, ExecutionStatus.DENIED)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.APPROVAL_REJECTED)

        event_types = [e.type for e in result.run.events]
        self.assertIn(EventType.EXECUTION_DENIED, event_types)
        self.assertNotIn(EventType.EXECUTION_STARTED, event_types)
        self.assertNotIn(EventType.EXECUTION_COMPLETED, event_types)

        denied_event = next(e for e in result.run.events if e.type == EventType.EXECUTION_DENIED)
        self.assertEqual(denied_event.data.get("reason"), "approval_rejected")

    def test_policy_denied_prevents_subprocess(self) -> None:
        # Profile does not allow sys.executable -> ExecutionPolicy denies
        restricted_profile = ProjectExecutionProfile(
            profile_id="restricted-env",
            environment_type=ExecutionEnvironmentType.HOST,
            runtime_name="python",
            allowed_commands=("some_other_binary",),
        )
        exec_req = ExecutionRequest(
            command=(sys.executable, "-c", "import sys; sys.exit(0)"),
            profile=restricted_profile,
        )
        request = EngineeringRunRequest(
            task=Task(id="run-exec-policy-denied", description="Policy denied run"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )
        result = self.engineering_executor.execute(request)

        self.assertEqual(len(result.execution_results), 1)
        exec_res = result.execution_results[0]
        self.assertEqual(exec_res.status, ExecutionStatus.DENIED)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)

        event_types = [e.type for e in result.run.events]
        self.assertIn(EventType.EXECUTION_POLICY_CHECKED, event_types)
        self.assertIn(EventType.EXECUTION_DENIED, event_types)
        self.assertNotIn(EventType.EXECUTION_STARTED, event_types)
        self.assertNotIn(EventType.EXECUTION_COMPLETED, event_types)

        denied_event = next(e for e in result.run.events if e.type == EventType.EXECUTION_DENIED)
        self.assertIn("command_not_allowed", denied_event.data.get("reason", ""))

    def test_no_stdout_or_stderr_in_lifecycle_events(self) -> None:
        secret_leak = "super_secret_payload_12345"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import sys; sys.stdout.write('{secret_leak}'); sys.stderr.write('err_msg'); sys.exit(0)",
            ),
            profile=self.profile,
        )
        request = EngineeringRunRequest(
            task=Task(id="run-leak-check", description="Leak check"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )
        result = self.engineering_executor.execute(request)

        for event in result.run.events:
            event_repr = str(event.data)
            self.assertNotIn("stdout", event.data, f"stdout key found in event {event.type}")
            self.assertNotIn("stderr", event.data, f"stderr key found in event {event.type}")
            self.assertNotIn(secret_leak, event_repr, f"Secret leaked in event {event.type}")
            self.assertNotIn("err_msg", event_repr, f"Error message leaked in event {event.type}")

    def test_secret_redaction_in_execution_result(self) -> None:
        secret = "very_secret_api_token"
        redactor = DefaultSecretRedactor(registered_secrets=[secret])
        coordinator = ExecutionCoordinator(redactor=redactor)
        engineering_executor = EngineeringRunExecutor(
            self.revision_executor,
            execution_coordinator=coordinator,
        )
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import sys; sys.stdout.write('Token is {secret}'); sys.exit(0)",
            ),
            profile=self.profile,
        )
        request = EngineeringRunRequest(
            task=Task(id="run-redaction", description="Redaction run"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )
        result = engineering_executor.execute(request)

        exec_res = result.execution_results[0]
        self.assertNotIn(secret, exec_res.stdout)
        self.assertIn(REDACTED_PLACEHOLDER, exec_res.stdout)

    def test_execution_timeout(self) -> None:
        timeout_profile = ProjectExecutionProfile(
            profile_id="timeout-profile",
            environment_type=ExecutionEnvironmentType.HOST,
            runtime_name="python",
            allowed_commands=(sys.executable,),
            timeout_seconds=0.2,
        )
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import time; time.sleep(3)",
            ),
            profile=timeout_profile,
        )
        request = EngineeringRunRequest(
            task=Task(id="run-timeout", description="Timeout run"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )
        result = self.engineering_executor.execute(request)

        exec_res = result.execution_results[0]
        self.assertEqual(exec_res.status, ExecutionStatus.TIMEOUT)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.EXECUTION_TIMEOUT)

    def test_execution_failure_non_zero_exit_code(self) -> None:
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('failed_job'); sys.exit(42)",
            ),
            profile=self.profile,
        )
        request = EngineeringRunRequest(
            task=Task(id="run-fail-code", description="Fail exit code run"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )
        result = self.engineering_executor.execute(request)

        exec_res = result.execution_results[0]
        self.assertEqual(exec_res.status, ExecutionStatus.FAILURE)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.EXECUTION_FAILURE)
        self.assertEqual(exec_res.exit_code, 42)

    def test_legacy_engineering_run_unaffected(self) -> None:
        request = EngineeringRunRequest(
            task=Task(id="run-legacy", description="Legacy run without execution"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
        )
        result = self.engineering_executor.execute(request)

        self.assertEqual(result.final_status, EngineeringRunStatus.SUCCESS)
        self.assertEqual(result.execution_results, ())
        event_types = [e.type for e in result.run.events]
        self.assertNotIn(EventType.EXECUTION_REQUESTED, event_types)
        self.assertNotIn(EventType.EXECUTION_STARTED, event_types)
        self.assertNotIn(EventType.EXECUTION_COMPLETED, event_types)

    def test_verification_result_linked_to_execution_result(self) -> None:
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('test_suite_passed'); sys.exit(0)",
            ),
            profile=self.profile,
        )
        coordinator = ExecutionCoordinator()
        exec_res = coordinator.execute_request(
            request=exec_req,
            workspace_root=self.workspace_root,
            allowed_commands=(sys.executable,),
        )

        evidence = create_execution_verification_evidence("crit-exec", exec_res)
        self.assertEqual(evidence["execution_result_id"], exec_res.request_id)
        self.assertEqual(evidence["outcome_status"], ExecutionOutcomeStatus.EXECUTION_SUCCESS.value)
        self.assertEqual(evidence["exit_code"], 0)

        verif_result = VerificationResult(
            verification_id="verif-1",
            status=VerificationStatus.PASS,
            code="execution_success",
            execution_result_id=exec_res.request_id,
            metadata=evidence,
        )
        self.assertEqual(verif_result.execution_result_id, exec_res.request_id)
        self.assertEqual(verif_result.metadata["execution_result_id"], exec_res.request_id)

    def test_unmapped_execution_failure_does_not_fail_acceptance(self) -> None:
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.exit(1)",
            ),
            profile=self.profile,
        )
        # The acceptance criteria here only care about result.txt existing, not execution
        request = EngineeringRunRequest(
            task=Task(id="run-unmapped-fail", description="Unmapped fail run"),
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.crit,),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )
        result = self.engineering_executor.execute(request)

        self.assertEqual(len(result.execution_results), 1)
        self.assertEqual(result.execution_results[0].outcome_status, ExecutionOutcomeStatus.EXECUTION_FAILURE)
        # Final acceptance passes because result.txt exists and matches expectation
        self.assertEqual(result.final_status, EngineeringRunStatus.SUCCESS)
        self.assertEqual(result.final_acceptance.status, AcceptanceStatus.PASS)


if __name__ == "__main__":
    unittest.main()
