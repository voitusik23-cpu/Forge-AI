"""System Integration & Failure Matrix Test Suite v0.1 (Stage 11).

Exercises end-to-end integration and proves security, authority, and failure boundaries:
Scenario A: Happy Path
Scenario B: Permission Denial
Scenario C: Approval Waiting
Scenario D: Approval Rejected
Scenario E: Execution Failure
Scenario F: Verification Failure
Scenario G: Revision Limit
Scenario H: Acceptance Failure
Scenario I: Cross-Run Contamination
Scenario J: Secret Injection
Scenario K: Malformed Decision
Scenario L: Determinism
Scenario M: Trace Integrity
Scenario N: Authority Boundary Test
Scenario O: No Side Effects From Context/Decision
"""

from __future__ import annotations

import builtins
import subprocess
import socket
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from app.agents.registry import AgentRegistry
from app.context.assembler import (
    ContextBudgetExceededError,
    DecisionContextAssembler,
)
from app.context.models import (
    ContextItem,
    ContextSourceType,
    ContextTrustLevel,
    DecisionContextEnvelope,
)
from app.context.validation import (
    sanitize_context_metadata,
    validate_decision_context,
)
from app.decision import (
    Decision,
    DecisionAction,
    DecisionRequest,
    DecisionType,
    sanitize_decision_metadata,
    validate_decision,
)
from app.decision.provider import DeterministicDecisionProvider
from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.policy import ExecutionPolicy
from app.execution.profile import (
    ExecutionEnvironmentType,
    ProjectExecutionProfile,
    TargetOS,
)
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionStatus,
)
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunStatus,
)
from app.orchestrator.models import (
    EventType,
    Run,
    RunState,
    Task,
    TaskResult,
)
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.revision import (
    RevisionAttemptResult,
    RevisionLoopExecutor,
    RevisionResult,
    RevisionStatus,
)
from app.orchestrator.run import RunExecutor
from app.orchestrator.trace import (
    FORBIDDEN_METADATA_SUBSTRINGS,
    RunEvent,
    RunTrace,
    sanitize_event_metadata,
    validate_event_metadata,
)
from app.projects.state import (
    ProjectState,
    ProjectStateStatus,
    derive_project_state,
)
from app.snapshots import ProjectSnapshot
from app.tasks.specification import Requirement, TaskSpecification
from app.tools.acceptance import (
    AcceptanceCriterion,
    AcceptanceGate,
    AcceptanceResult,
    AcceptanceStatus,
)
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolver,
    ApprovalState,
)
from app.tools.changesets import ChangeSet
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
    WorkspaceVerifier,
)
from app.tools.workspace import Workspace


class _StubAgent:
    name = "stub_agent"
    provider_name = "fixture"

    def run(self, task: Task) -> TaskResult:
        return TaskResult(task.id, True, output="stub_success")


class _TrackingResolver:
    def __init__(self, decision: ApprovalState = ApprovalState.APPROVED) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    def resolve(self, request: ApprovalRequest) -> ApprovalState:
        self.requests.append(request)
        return self.decision


class TestIntegrationFailureMatrix(unittest.TestCase):
    """End-to-end integration and failure matrix tests across Forge contracts."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp.name)
        self.workspace = Workspace(self.workspace_root)

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
            profile_id="py-test-profile",
            environment_type=ExecutionEnvironmentType.HOST,
            runtime_name="python",
            target_os=TargetOS.WINDOWS if sys.platform == "win32" else TargetOS.LINUX,
            allowed_commands=(sys.executable, "python", "python.exe"),
            timeout_seconds=10.0,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    # =========================================================================
    # Scenario A — Happy Path
    # =========================================================================
    def test_scenario_a_happy_path(self) -> None:
        """Prove complete successful pipeline from TaskSpecification to ACCEPTED."""
        output_file = self.workspace_root / "output.txt"
        output_file.write_text("initial", encoding="utf-8")

        req = Requirement(requirement_id="req-happy", description="Run process successfully")
        crit = AcceptanceCriterion(
            criterion_id="crit-happy",
            requirement_id="req-happy",
            description="output.txt exists",
        )
        spec = TaskSpecification(
            task_id="task-happy",
            title="Happy Path Task",
            description="Task that executes and passes verification",
            requirements=(req,),
            acceptance_criteria=(crit,),
            execution_profile=self.profile,
        )
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('happy'); sys.exit(0)",
            ),
            profile=self.profile,
        )
        run_req = EngineeringRunRequest(
            task_specification=spec,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations={
                "crit-happy": VerificationExpectation(relative_path="output.txt", exists=True)
            },
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )

        result = self.engineering_executor.execute(run_req)

        # 1. Run completes successfully
        self.assertEqual(result.final_status, EngineeringRunStatus.SUCCESS)

        # 2. Final ProjectState is ACCEPTED
        self.assertIsNotNone(result.final_project_state)
        self.assertEqual(result.final_project_state.status, ProjectStateStatus.ACCEPTED)

        # 3. Acceptance is PASS
        self.assertIsNotNone(result.final_acceptance)
        self.assertEqual(result.final_acceptance.status, AcceptanceStatus.PASS)

        # 4. Required verification is PASS
        self.assertTrue(len(result.verification_results) > 0)
        self.assertTrue(all(v.status == VerificationStatus.PASS for v in result.verification_results))

        # 5. Execution result is SUCCESS
        self.assertEqual(len(result.execution_results), 1)
        self.assertEqual(
            result.execution_results[0].outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS
        )

        # 6. Decision history exists
        self.assertTrue(len(result.decisions) > 0)
        self.assertEqual(result.final_decision.decision_type, DecisionType.COMPLETE)
        self.assertEqual(result.final_decision.action, DecisionAction.COMPLETE_RUN)

        # 7. Context envelope exists
        self.assertTrue(len(result.context_envelopes) > 0)
        self.assertIsNotNone(result.final_context_envelope)
        self.assertEqual(result.final_context_envelope.task_id, "task-happy")

        # 8. Trace exists and ordering is strictly monotonic
        self.assertIsNotNone(result.trace)
        seqs = [e.sequence_number for e in result.events]
        self.assertEqual(seqs, list(range(len(result.events))))

        # 9. Final event is RUN_COMPLETED
        self.assertEqual(result.events[-1].event_type, EventType.RUN_COMPLETED)

        # 10. No forbidden metadata appears anywhere in trace
        for ev in result.events:
            self.assertEqual(validate_event_metadata(ev.metadata), ())

    # =========================================================================
    # Scenario B — Permission Denial
    # =========================================================================
    def test_scenario_b_permission_denial(self) -> None:
        """Prove that Permission DENY halts execution and cannot be bypassed by Decision."""
        target_file = self.workspace_root / "denied_output.txt"

        req = Requirement(requirement_id="req-b", description="Must run command")
        crit = AcceptanceCriterion(
            criterion_id="crit-b",
            requirement_id="req-b",
            description="denied_output.txt created by command",
        )
        spec = TaskSpecification(
            task_id="task-b",
            title="Permission Denied Task",
            description="Command execution is disallowed",
            requirements=(req,),
            acceptance_criteria=(crit,),
            execution_profile=self.profile,
        )
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('danger')",
            ),
            profile=self.profile,
        )

        # Allowed execution commands explicitly does NOT contain sys.executable
        run_req = EngineeringRunRequest(
            task_specification=spec,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations={
                "crit-b": VerificationExpectation(relative_path="denied_output.txt", exists=True)
            },
            execution_requests=(exec_req,),
            allowed_execution_commands=("some_disallowed_command",),
        )

        result = self.engineering_executor.execute(run_req)

        # 1. Permission DENY occurred
        self.assertEqual(len(result.execution_results), 1)
        exec_res = result.execution_results[0]
        self.assertEqual(exec_res.status, ExecutionStatus.DENIED)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.PERMISSION_DENIED)

        # 2. Execution process was NOT started
        self.assertFalse(target_file.exists())

        # 3. No ExecutionResult SUCCESS exists
        self.assertFalse(
            any(r.outcome_status == ExecutionOutcomeStatus.EXECUTION_SUCCESS for r in result.execution_results)
        )

        # 4. No verification PASS can be fabricated
        self.assertTrue(all(v.status != VerificationStatus.PASS for v in result.verification_results))

        # 5. Project cannot become ACCEPTED
        self.assertNotEqual(result.final_project_state.status, ProjectStateStatus.ACCEPTED)
        self.assertNotEqual(result.final_status, EngineeringRunStatus.SUCCESS)

        # 6. Trace contains safe denial event
        event_types = [e.event_type for e in result.events]
        self.assertIn(EventType.EXECUTION_DENIED, event_types)

        # 7. No bypass possible through Decision: final decision reflects failure
        self.assertIsNotNone(result.final_decision)
        self.assertEqual(result.final_decision.decision_type, DecisionType.FAIL)
        self.assertEqual(result.final_decision.action, DecisionAction.FAIL_RUN)

    # =========================================================================
    # Scenario C — Approval Waiting
    # =========================================================================
    def test_scenario_c_approval_waiting(self) -> None:
        """Prove that pending human approval halts execution and transitions run to WAITING."""
        target_file = self.workspace_root / "approval_output.txt"

        req = Requirement(requirement_id="req-c", description="Requires approval")
        crit = AcceptanceCriterion(
            criterion_id="crit-c",
            requirement_id="req-c",
            description="approval_output.txt exists",
        )
        spec = TaskSpecification(
            task_id="task-c",
            title="Approval Waiting Task",
            description="Execution requires human approval",
            requirements=(req,),
            acceptance_criteria=(crit,),
            execution_profile=self.profile,
        )
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('approved')",
            ),
            profile=self.profile,
        )

        resolver = _TrackingResolver(decision=ApprovalState.REQUIRED)
        run_req = EngineeringRunRequest(
            task_specification=spec,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations={
                "crit-c": VerificationExpectation(relative_path="approval_output.txt", exists=True)
            },
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
        )

        result = self.engineering_executor.execute(run_req)

        # 1. Run becomes WAITING_FOR_APPROVAL
        self.assertEqual(result.final_status, EngineeringRunStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(result.run.state, RunState.WAITING_FOR_APPROVAL)

        # 2. Execution does NOT occur
        self.assertFalse(target_file.exists())
        self.assertEqual(
            result.execution_results[0].outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING
        )

        # 3. No verification/acceptance PASS fabricated
        if result.final_acceptance:
            self.assertNotEqual(result.final_acceptance.status, AcceptanceStatus.PASS)

        # 4. Approval event appears in trace
        event_types = [e.event_type for e in result.events]
        self.assertIn(EventType.EXECUTION_DENIED, event_types)
        denied_ev = next(e for e in result.events if e.event_type == EventType.EXECUTION_DENIED)
        self.assertEqual(denied_ev.metadata.get("reason"), "approval_waiting")

        # 5. Decision requests approval
        self.assertIsNotNone(result.final_decision)
        self.assertEqual(result.final_decision.decision_type, DecisionType.REQUEST_APPROVAL)

    # =========================================================================
    # Scenario D — Approval Rejected
    # =========================================================================
    def test_scenario_d_approval_rejected(self) -> None:
        """Prove that explicit approval rejection prevents execution and run fails safely."""
        target_file = self.workspace_root / "rejected_output.txt"

        req = Requirement(requirement_id="req-d", description="Rejection test")
        crit = AcceptanceCriterion(
            criterion_id="crit-d",
            requirement_id="req-d",
            description="rejected_output.txt exists",
        )
        spec = TaskSpecification(
            task_id="task-d",
            title="Approval Rejected Task",
            description="Approval is rejected by user",
            requirements=(req,),
            acceptance_criteria=(crit,),
            execution_profile=self.profile,
        )
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('bad')",
            ),
            profile=self.profile,
        )

        resolver = _TrackingResolver(decision=ApprovalState.REJECTED)
        run_req = EngineeringRunRequest(
            task_specification=spec,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations={
                "crit-d": VerificationExpectation(relative_path="rejected_output.txt", exists=True)
            },
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
        )

        result = self.engineering_executor.execute(run_req)

        # 1. Execution does NOT happen
        self.assertFalse(target_file.exists())
        self.assertEqual(
            result.execution_results[0].outcome_status, ExecutionOutcomeStatus.APPROVAL_REJECTED
        )

        # 2. Run cannot become SUCCESS
        self.assertNotEqual(result.final_status, EngineeringRunStatus.SUCCESS)

        # 3. Rejection represented in result / trace
        denied_ev = next(e for e in result.events if e.event_type == EventType.EXECUTION_DENIED)
        self.assertEqual(denied_ev.metadata.get("reason"), "approval_rejected")

        # 4. Decision cannot override rejection
        self.assertIsNotNone(result.final_decision)
        self.assertEqual(result.final_decision.decision_type, DecisionType.FAIL)
        self.assertEqual(result.final_decision.action, DecisionAction.FAIL_RUN)

    # =========================================================================
    # Scenario E — Execution Failure
    # =========================================================================
    def test_scenario_e_execution_failure(self) -> None:
        """Prove that non-zero subprocess exit halts progress towards acceptance."""
        req = Requirement(requirement_id="req-e", description="Failing process")
        crit = AcceptanceCriterion(
            criterion_id="crit-e",
            requirement_id="req-e",
            description="Process must succeed",
        )
        spec = TaskSpecification(
            task_id="task-e",
            title="Execution Failure Task",
            description="Process exits with code 1",
            requirements=(req,),
            acceptance_criteria=(crit,),
            execution_profile=self.profile,
        )
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.exit(1)",
            ),
            profile=self.profile,
        )
        run_req = EngineeringRunRequest(
            task_specification=spec,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations={
                "crit-e": VerificationExpectation(relative_path="nonexistent.txt", exists=True)
            },
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )

        result = self.engineering_executor.execute(run_req)

        # 1. ExecutionResult is failure
        self.assertEqual(len(result.execution_results), 1)
        self.assertEqual(
            result.execution_results[0].outcome_status, ExecutionOutcomeStatus.EXECUTION_FAILURE
        )
        self.assertEqual(result.execution_results[0].exit_code, 1)

        # 2. Verification does not become PASS
        self.assertTrue(all(v.status != VerificationStatus.PASS for v in result.verification_results))

        # 3. Acceptance does not become PASS
        if result.final_acceptance:
            self.assertNotEqual(result.final_acceptance.status, AcceptanceStatus.PASS)

        # 4. ProjectState cannot become ACCEPTED
        self.assertNotEqual(result.final_project_state.status, ProjectStateStatus.ACCEPTED)
        self.assertNotEqual(result.final_status, EngineeringRunStatus.SUCCESS)

        # 5. Decision identifies need for revision or failure
        self.assertIn(result.final_decision.decision_type, (DecisionType.REVISE, DecisionType.FAIL))

    # =========================================================================
    # Scenario F — Verification Failure
    # =========================================================================
    def test_scenario_f_verification_failure(self) -> None:
        """Prove that successful execution with failed verification condition halts acceptance."""
        req = Requirement(requirement_id="req-f", description="Expect missing file")
        crit = AcceptanceCriterion(
            criterion_id="crit-f",
            requirement_id="req-f",
            description="missing_file.txt must exist",
        )
        spec = TaskSpecification(
            task_id="task-f",
            title="Verification Failure Task",
            description="Process succeeds but expected file is missing",
            requirements=(req,),
            acceptance_criteria=(crit,),
            execution_profile=self.profile,
        )
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.exit(0)",  # Successful process execution
            ),
            profile=self.profile,
        )
        run_req = EngineeringRunRequest(
            task_specification=spec,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations={
                "crit-f": VerificationExpectation(relative_path="missing_file.txt", exists=True)
            },
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
        )

        result = self.engineering_executor.execute(run_req)

        # 1. Execution is SUCCESS
        self.assertEqual(
            result.execution_results[0].outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS
        )

        # 2. Verification is FAIL
        self.assertTrue(len(result.verification_results) >= 1)
        self.assertTrue(all(v.status == VerificationStatus.FAIL for v in result.verification_results))

        # 3. Acceptance cannot be PASS
        self.assertIsNotNone(result.final_acceptance)
        self.assertEqual(result.final_acceptance.status, AcceptanceStatus.FAIL)

        # 4. ProjectState cannot become ACCEPTED
        self.assertNotEqual(result.final_project_state.status, ProjectStateStatus.ACCEPTED)
        self.assertNotEqual(result.final_status, EngineeringRunStatus.SUCCESS)

        # 5. Decision recommends revision or fail
        self.assertIn(result.final_decision.decision_type, (DecisionType.REVISE, DecisionType.FAIL))

    # =========================================================================
    # Scenario G — Revision Limit
    # =========================================================================
    def test_scenario_g_revision_limit_reached(self) -> None:
        """Prove that revision attempts never exceed configured limit and trigger terminal FAIL."""
        req = Requirement(requirement_id="req-g", description="Always failing requirement")
        crit = AcceptanceCriterion(
            criterion_id="crit-g",
            requirement_id="req-g",
            description="nonexistent file",
        )
        spec = TaskSpecification(
            task_id="task-g",
            title="Revision Limit Task",
            description="Fails repeatedly until limit reached",
            requirements=(req,),
            acceptance_criteria=(crit,),
            execution_profile=self.profile,
        )
        run_req = EngineeringRunRequest(
            task_specification=spec,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations={
                "crit-g": VerificationExpectation(relative_path="nonexistent.txt", exists=True)
            },
            max_revision_attempts=2,
        )

        result = self.engineering_executor.execute(run_req)

        # 1. Revision attempts never exceed configured limit (2 revisions + 1 initial = 3 total attempts)
        self.assertEqual(len(result.attempts), 3)
        self.assertEqual(sum(e.event_type == EventType.REVISION_STARTED for e in result.events), 2)

        # 2. Final status is LIMIT_REACHED
        self.assertEqual(result.final_status, EngineeringRunStatus.LIMIT_REACHED)

        # 3. Decision eventually produces FAIL / FAIL_RUN with revision_limit_reached
        self.assertIsNotNone(result.final_decision)
        self.assertEqual(result.final_decision.decision_type, DecisionType.FAIL)
        self.assertEqual(result.final_decision.action, DecisionAction.FAIL_RUN)
        self.assertEqual(result.final_decision.reason_code, "revision_limit_reached")

        # 4. Final ProjectState is FAILED
        self.assertEqual(result.final_project_state.status, ProjectStateStatus.FAILED)

        # 5. Trace preserves each attempt
        attempt_nums = {e.attempt_number for e in result.events if e.attempt_number is not None}
        self.assertIn(1, attempt_nums)
        self.assertIn(2, attempt_nums)

    # =========================================================================
    # Scenario H — Acceptance Failure
    # =========================================================================
    def test_scenario_h_acceptance_failure(self) -> None:
        """Prove that required requirement failure fails acceptance and cannot produce COMPLETE."""
        (self.workspace_root / "opt.txt").write_text("ok", encoding="utf-8")

        req_required = Requirement(
            requirement_id="req-req", description="Required", required=True
        )
        req_optional = Requirement(
            requirement_id="req-opt", description="Optional", required=False
        )
        crit_required = AcceptanceCriterion(
            criterion_id="crit-req",
            requirement_id="req-req",
            description="required file exists",
            required=True,
        )
        crit_optional = AcceptanceCriterion(
            criterion_id="crit-opt",
            requirement_id="req-opt",
            description="optional file exists",
            required=False,
        )

        spec = TaskSpecification(
            task_id="task-h",
            title="Multi-Requirement Task",
            description="One required fails, one optional passes",
            requirements=(req_required, req_optional),
            acceptance_criteria=(crit_required, crit_optional),
            execution_profile=self.profile,
        )
        run_req = EngineeringRunRequest(
            task_specification=spec,
            agent_name=self.agent.name,
            workspace=self.workspace,
            verification_expectations={
                "crit-req": VerificationExpectation(relative_path="missing.txt", exists=True),
                "crit-opt": VerificationExpectation(relative_path="opt.txt", exists=True),
            },
        )

        result = self.engineering_executor.execute(run_req)

        # 1. Required requirement failure causes Acceptance failure
        self.assertIsNotNone(result.final_acceptance)
        self.assertEqual(result.final_acceptance.status, AcceptanceStatus.FAIL)

        # 2. Detailed acceptance report tracks evaluations
        report = result.detailed_acceptance_report
        self.assertIsNotNone(report)
        self.assertEqual(len(report.failed_requirements), 1)
        self.assertEqual(report.failed_requirements[0].requirement_id, "req-req")
        self.assertEqual(len(report.passed_requirements), 1)
        self.assertEqual(report.passed_requirements[0].requirement_id, "req-opt")

        # 3. Project cannot become ACCEPTED
        self.assertEqual(result.final_project_state.status, ProjectStateStatus.FAILED)
        self.assertNotEqual(result.final_status, EngineeringRunStatus.SUCCESS)

        # 4. Decision cannot return COMPLETE_RUN
        self.assertNotEqual(result.final_decision.action, DecisionAction.COMPLETE_RUN)

    # =========================================================================
    # Scenario I — Cross-Run Contamination
    # =========================================================================
    def test_scenario_i_cross_run_contamination(self) -> None:
        """Prove that foreign artifacts belonging to Run A are rejected in Run B context."""
        foreign_state = ProjectState(
            run_id="run-A",
            attempt_number=0,
            status=ProjectStateStatus.INITIAL,
        )
        foreign_item = ContextItem(
            item_id="item-foreign",
            item_type="project_state",
            source_type=ContextSourceType.PROJECT_STATE,
            value="foreign_val",
            source_id="run:run-A",
        )

        # 1. Foreign ProjectState in envelope rejected by validator
        env_foreign_state = DecisionContextEnvelope(
            context_id="ctx-b-1",
            run_id="run-B",
            attempt_number=0,
            project_state=foreign_state,
        )
        report_state = validate_decision_context(env_foreign_state)
        self.assertFalse(report_state.valid)
        self.assertTrue(any("cross_run_reference" in err for err in report_state.errors))

        # 2. Foreign ContextItem in envelope rejected by validator
        env_foreign_item = DecisionContextEnvelope(
            context_id="ctx-b-2",
            run_id="run-B",
            attempt_number=0,
            context_items=(foreign_item,),
        )
        report_item = validate_decision_context(env_foreign_item)
        self.assertFalse(report_item.valid)
        self.assertTrue(any("cross_run_reference" in err for err in report_item.errors))

        # 3. Foreign Decision cannot control Run B
        foreign_decision = Decision(
            decision_id="dec-foreign",
            run_id="run-A",
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="ok",
        )
        req_b = DecisionRequest(
            decision_id="dec-req-b",
            run_id="run-B",
            attempt_number=0,
        )
        state_b = ProjectState(run_id="run-B", attempt_number=0, status=ProjectStateStatus.INITIAL)
        val_decision = validate_decision(foreign_decision, req_b, state_b)
        self.assertFalse(val_decision.valid)
        self.assertTrue(any("run_id_mismatch" in err for err in val_decision.errors))

        # 4. Foreign trace event cannot be appended to Run B trace
        trace_b = RunTrace("run-B")
        foreign_event = RunEvent(
            run_id="run-A",
            sequence_number=0,
            event_type=EventType.ENGINEERING_RUN_STARTED,
        )
        with self.assertRaises(ValueError):
            trace_b.append(foreign_event)

    # =========================================================================
    # Scenario J — Secret Injection
    # =========================================================================
    def test_scenario_j_secret_injection(self) -> None:
        """Prove that unsafe secrets and output streams are sanitized or rejected."""
        dirty_meta = {
            "password": "secret_password",
            "token": "bearer_token_123",
            "api_key": "sk-secret-12345",
            "secret": "confidential_data",
            "credential": "user:pass",
            "stdout": "raw stream output",
            "stderr": "raw error output",
            "raw_output": "raw process bytes",
            "prompt": "system prompt here",
            "chain_of_thought": "hidden reasoning",
            "source_code": "def foo(): pass",
            "file_content": "binary contents",
            "safe_key": "safe_value",
            "oversized": "a" * 5000,
        }

        # 1. Context metadata sanitization
        sanitized_ctx = sanitize_context_metadata(dirty_meta)
        self.assertIn("safe_key", sanitized_ctx)
        for bad in FORBIDDEN_METADATA_SUBSTRINGS:
            self.assertNotIn(bad, sanitized_ctx)
        self.assertNotIn("oversized", sanitized_ctx)

        # 2. Context validation rejects unsanitized envelope metadata
        env = DecisionContextEnvelope(
            context_id="ctx-dirty",
            run_id="run-1",
            attempt_number=0,
            metadata={"api_key": "sk-123"},
        )
        rep = validate_decision_context(env)
        self.assertFalse(rep.valid)
        self.assertTrue(any("forbidden_metadata_key" in err for err in rep.errors))

        # 3. Trace event sanitizes metadata automatically
        ev = RunEvent(
            run_id="run-1",
            sequence_number=0,
            event_type=EventType.ENGINEERING_RUN_STARTED,
            metadata=dirty_meta,
        )
        self.assertIn("safe_key", ev.metadata)
        for bad in FORBIDDEN_METADATA_SUBSTRINGS:
            self.assertNotIn(bad, ev.metadata)

        # 4. to_dict() never exposes raw secrets
        d = ev.to_dict()
        meta_d = d["metadata"]
        for bad in FORBIDDEN_METADATA_SUBSTRINGS:
            self.assertNotIn(bad, meta_d)

    # =========================================================================
    # Scenario K — Malformed Decision
    # =========================================================================
    def test_scenario_k_malformed_decisions_rejected(self) -> None:
        """Prove that invalid decisions are strictly rejected by the validator."""
        req = DecisionRequest(
            decision_id="req-k",
            run_id="run-k",
            attempt_number=1,
            available_actions=(DecisionAction.RUN_VERIFICATION,),
            blocking_conditions=("revision_limit_reached",),
            acceptance_status="fail",
        )
        state = ProjectState(
            run_id="run-k",
            attempt_number=1,
            status=ProjectStateStatus.FAILED,
        )

        # 1. COMPLETE when acceptance is not PASS
        dec_premature_complete = Decision(
            decision_id="d1",
            run_id="run-k",
            decision_type=DecisionType.COMPLETE,
            action=DecisionAction.COMPLETE_RUN,
            reason_code="acceptance_passed",
            attempt_number=1,
        )
        rep1 = validate_decision(dec_premature_complete, req, state)
        self.assertFalse(rep1.valid)
        self.assertTrue(any("premature_completion" in err for err in rep1.errors))

        # 2. Action not in available_actions
        dec_unavail_action = Decision(
            decision_id="d2",
            run_id="run-k",
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="exec",
            attempt_number=1,
        )
        rep2 = validate_decision(dec_unavail_action, req, state)
        self.assertFalse(rep2.valid)
        self.assertTrue(any("unavailable_action" in err for err in rep2.errors))

        # 3. REVISE after revision limit reached
        dec_revise_past_limit = Decision(
            decision_id="d-rev",
            run_id="run-k",
            decision_type=DecisionType.REVISE,
            action=DecisionAction.REQUEST_REVISION,
            reason_code="revise",
            attempt_number=1,
        )
        rep_rev = validate_decision(dec_revise_past_limit, req, state)
        self.assertFalse(rep_rev.valid)
        self.assertTrue(any("invalid_revision" in err for err in rep_rev.errors))

        # 4. Wrong attempt_number
        dec_wrong_attempt = Decision(
            decision_id="d3",
            run_id="run-k",
            decision_type=DecisionType.VERIFY,
            action=DecisionAction.RUN_VERIFICATION,
            reason_code="ver",
            attempt_number=999,
        )
        rep3 = validate_decision(dec_wrong_attempt, req, state)
        self.assertFalse(rep3.valid)
        self.assertTrue(any("attempt_number_mismatch" in err for err in rep3.errors))

        # 5. Incompatible decision_type and action
        dec_incompatible = Decision(
            decision_id="d4",
            run_id="run-k",
            decision_type=DecisionType.COMPLETE,
            action=DecisionAction.FAIL_RUN,
            reason_code="mismatch",
            attempt_number=1,
        )
        rep4 = validate_decision(dec_incompatible, req, state)
        self.assertFalse(rep4.valid)
        self.assertTrue(any("incompatible_action" in err for err in rep4.errors))

    # =========================================================================
    # Scenario L — Determinism
    # =========================================================================
    def test_scenario_l_determinism(self) -> None:
        """Prove identical structured state produces identical fingerprint and decision."""
        assembler = DecisionContextAssembler()
        provider = DeterministicDecisionProvider()

        req = Requirement(requirement_id="req-l", description="Determinism test")
        spec = TaskSpecification(
            task_id="task-l",
            title="Determinism Task",
            description="Testing determinism across iterations",
            requirements=(req,),
            acceptance_criteria=(
                AcceptanceCriterion(
                    criterion_id="crit-l", requirement_id="req-l", description="crit"
                ),
            ),
        )
        state = ProjectState(
            run_id="run-l",
            attempt_number=1,
            status=ProjectStateStatus.CHANGED,
            task_id="task-l",
        )

        fingerprints: list[str] = []
        decisions: list[Decision] = []

        for _ in range(5):
            env = assembler.assemble(
                run_id="run-l",
                attempt_number=1,
                task_specification=spec,
                project_state=state,
                blocking_conditions=("verification_required",),
            )
            fingerprints.append(env.context_fingerprint)

            d_req = DecisionRequest(
                decision_id="dec-req-static",
                run_id="run-l",
                attempt_number=1,
                current_project_state=state,
                blocking_conditions=("verification_required",),
                context_id=env.context_id,
                context_fingerprint=env.context_fingerprint,
                context_envelope=env,
            )
            dec = provider.decide(d_req)
            decisions.append(dec)

        # 1. Identical fingerprints across all runs
        self.assertEqual(len(set(fingerprints)), 1)

        # 2. Identical decision properties
        types = {d.decision_type for d in decisions}
        actions = {d.action for d in decisions}
        reasons = {d.reason_code for d in decisions}
        self.assertEqual(types, {DecisionType.VERIFY})
        self.assertEqual(actions, {DecisionAction.RUN_VERIFICATION})
        self.assertEqual(reasons, {"verification_required"})

    # =========================================================================
    # Scenario M — Trace Integrity
    # =========================================================================
    def test_scenario_m_trace_integrity(self) -> None:
        """Prove strict sequence monotonicity, run binding, and immutability of RunTrace."""
        trace = RunTrace("run-m")
        ev0 = RunEvent(
            run_id="run-m",
            sequence_number=0,
            event_type=EventType.ENGINEERING_RUN_STARTED,
        )
        trace.append(ev0)

        # 1. Duplicate sequence number rejected
        ev_dup = RunEvent(
            run_id="run-m",
            sequence_number=0,
            event_type=EventType.DECISION_REQUESTED,
        )
        with self.assertRaises(ValueError):
            trace.append(ev_dup)

        # 2. Sequence gap rejected (expected 1, got 2)
        ev_gap = RunEvent(
            run_id="run-m",
            sequence_number=2,
            event_type=EventType.DECISION_REQUESTED,
        )
        with self.assertRaises(ValueError):
            trace.append(ev_gap)

        # 3. Negative sequence rejected at event creation
        with self.assertRaises(ValueError):
            RunEvent(
                run_id="run-m",
                sequence_number=-1,
                event_type=EventType.DECISION_REQUESTED,
            )

        # 4. Wrong run_id rejected
        ev_foreign = RunEvent(
            run_id="wrong-run",
            sequence_number=1,
            event_type=EventType.DECISION_REQUESTED,
        )
        with self.assertRaises(ValueError):
            trace.append(ev_foreign)

        # 5. Trace events tuple is immutable
        events_tuple = trace.events
        self.assertIsInstance(events_tuple, tuple)
        with self.assertRaises(FrozenInstanceError):
            ev0.sequence_number = 99  # type: ignore

    # =========================================================================
    # Scenario N — Authority Boundary Test
    # =========================================================================
    def test_scenario_n_authority_boundaries_cannot_be_bypassed(self) -> None:
        """Explicitly prove Decision cannot grant permissions, approve, or alter state."""
        # 1. Decision cannot grant Permission
        decision = Decision(
            decision_id="dec-force",
            run_id="run-n",
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="force_execute",
        )
        policy = ExecutionPolicy()
        coord = ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.workspace_root),
            policy=policy,
        )
        req = ExecutionRequest(
            command=("forbidden_binary",),
            profile=self.profile,
        )
        res = coord.execute(req, run_id="run-n")
        self.assertEqual(res.outcome_status, ExecutionOutcomeStatus.PERMISSION_DENIED)

        # 2. Decision cannot approve approval
        resolver = _TrackingResolver(decision=ApprovalState.REQUIRED)
        res_appr = coord.execute(
            ExecutionRequest(command=(sys.executable, "-c", "pass"), profile=self.profile),
            run_id="run-n",
            allowed_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
        )
        self.assertEqual(res_appr.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)

        # 3. Decision cannot create Verification PASS
        verifier = WorkspaceVerifier()
        fake_exp = VerificationExpectation(relative_path="fake.txt", exists=True)
        v_res = verifier.verify(
            fake_exp,
            workspace=self.workspace,
            run_id="run-n",
            observer=lambda t, d: None,
        )
        self.assertEqual(v_res.status, VerificationStatus.FAIL)

        # 4. Decision cannot create Acceptance PASS
        gate = AcceptanceGate()
        acc_result = gate.evaluate(
            criteria=[AcceptanceCriterion(criterion_id="crit-n", description="fake")],
            verifications={"crit-n": v_res},
            run_id="run-n",
            observer=lambda t, d: None,
        )
        self.assertEqual(acc_result.status, AcceptanceStatus.FAIL)

        # 5. Decision cannot directly change ProjectState
        state = ProjectState(
            run_id="run-n",
            attempt_number=0,
            status=ProjectStateStatus.INITIAL,
        )
        with self.assertRaises(FrozenInstanceError):
            state.status = ProjectStateStatus.ACCEPTED  # type: ignore

    # =========================================================================
    # Scenario O — No Side Effects From Context / Decision
    # =========================================================================
    def test_scenario_o_no_side_effects_from_context_and_decision(self) -> None:
        """Prove DecisionContextAssembler and DecisionProvider are pure and side-effect free."""
        assembler = DecisionContextAssembler()
        provider = DeterministicDecisionProvider()

        req = Requirement(requirement_id="req-o", description="Side effects check")
        crit = AcceptanceCriterion(
            criterion_id="crit-o", requirement_id="req-o", description="check"
        )
        spec = TaskSpecification(
            task_id="task-o",
            title="Side Effects Task",
            description="Testing purity",
            requirements=(req,),
            acceptance_criteria=(crit,),
        )
        state = ProjectState(
            run_id="run-o",
            attempt_number=0,
            status=ProjectStateStatus.INITIAL,
            task_id="task-o",
        )

        with patch.object(subprocess, "Popen", side_effect=RuntimeError("Subprocess forbidden")), \
             patch.object(subprocess, "run", side_effect=RuntimeError("Subprocess forbidden")), \
             patch.object(socket, "socket", side_effect=RuntimeError("Network forbidden")):

            # 1. Assemble context
            env = assembler.assemble(
                run_id="run-o",
                attempt_number=0,
                task_specification=spec,
                project_state=state,
            )
            self.assertIsNotNone(env)

            # 2. Make decision
            d_req = DecisionRequest(
                decision_id="dec-req-o",
                run_id="run-o",
                attempt_number=0,
                current_project_state=state,
                context_id=env.context_id,
                context_fingerprint=env.context_fingerprint,
                context_envelope=env,
            )
            decision = provider.decide(d_req)
            self.assertIsNotNone(decision)

        # 3. Ensure input objects were not mutated
        self.assertEqual(state.status, ProjectStateStatus.INITIAL)
        self.assertEqual(spec.task_id, "task-o")


if __name__ == "__main__":
    unittest.main()
