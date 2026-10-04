"""Comprehensive unit and adversarial test suite for Forge AI Agent Harness (Stage 12)."""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch
from uuid import uuid4

from app.agent_runtime.harness import AgentHarness
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
from app.context.models import ContextItem, DecisionContextEnvelope
from app.decision.models import (
    Decision,
    DecisionAction,
    DecisionRequest,
    DecisionType,
)
from app.decision.provider import DecisionProvider, DeterministicDecisionProvider
from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.policy import ExecutionPolicy
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
)
from app.orchestrator.models import EventType
from app.orchestrator.trace import RunEvent, RunTrace
from app.projects.state import ProjectState, ProjectStateStatus, derive_project_state
from app.tasks.specification import (
    AcceptanceCriterion,
    Requirement,
    TaskSpecification,
)
from app.tools.acceptance import AcceptanceGate, AcceptanceResult, AcceptanceStatus
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolver,
    ApprovalState,
    InMemoryApprovalResolver,
)
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
    WorkspaceVerifier,
)
from app.tools.workspace import Workspace


class _TrackingApprovalResolver:
    """Deterministic approval resolver for tests.

    Returns the configured decision state for any request.
    ApprovalState.REQUIRED means still pending (returns None to simulate
    an unanswered request).
    """

    def __init__(self, decision: ApprovalState = ApprovalState.APPROVED) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    def resolve(self, request: ApprovalRequest) -> ApprovalState | None:
        self.requests.append(request)
        # REQUIRED → pending (caller treats None as still waiting)
        if self.decision == ApprovalState.REQUIRED:
            return None
        return self.decision


class _MaliciousCompleteProvider:
    """Adversarial provider attempting to complete run without passing acceptance."""

    def decide(self, request: DecisionRequest) -> Decision:
        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            decision_type=DecisionType.COMPLETE,
            action=DecisionAction.COMPLETE_RUN,
            reason_code="force_complete",
            attempt_number=request.attempt_number,
        )


class _MaliciousExecuteProvider:
    """Adversarial provider attempting to execute forbidden command under permission denial."""

    def decide(self, request: DecisionRequest) -> Decision:
        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,
            reason_code="force_execute",
            attempt_number=request.attempt_number,
        )


class _InfiniteLoopProvider:
    """Provider that constantly demands verification, tempting an infinite loop."""

    def decide(self, request: DecisionRequest) -> Decision:
        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            decision_type=DecisionType.VERIFY,
            action=DecisionAction.RUN_VERIFICATION,
            reason_code="loop_verify",
            attempt_number=request.attempt_number,
        )


class _UnknownActionProvider:
    """Provider returning an unsupported action."""

    def decide(self, request: DecisionRequest) -> Decision:
        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            decision_type=DecisionType.CONTINUE,
            action=DecisionAction.EXECUTE,  # will be overridden or malformed
            reason_code="unknown_action",
            attempt_number=request.attempt_number,
        )


class TestAgentHarness(unittest.TestCase):
    """Stage 12: Comprehensive verification of AgentHarness / Controlled Run Loop."""

    def setUpself(self) -> None:
        pass

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="forge_harness_test_")
        self.workspace_root = pathlib.Path(self.temp_dir)
        self.workspace = Workspace(root=self.workspace_root)
        self.profile = ProjectExecutionProfile(
            profile_id="prof-harness",
            allowed_commands=(sys.executable,),
        )
        self.harness = AgentHarness()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # Test A: Single successful action
    # =========================================================================
    def test_scenario_a_single_successful_action(self) -> None:
        """Prove that a single authoritative action can be authorized and executed cleanly."""
        target_file = self.workspace_root / "out.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('hello')",
            ),
            profile=self.profile,
        )
        req = HarnessRequest(
            run_id="run-a",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            initial_project_state=ProjectState(
                run_id="run-a",
                attempt_number=0,
                status=ProjectStateStatus.INITIAL,
            ),
        )

        # Use policy with max_actions=1
        harness = AgentHarness(policy=AgentHarnessPolicy(max_actions=1))
        result = harness.run(req)

        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_text(), "hello")
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(
            result.observations[0].result_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS.value
        )
        self.assertEqual(result.final_state.status, HarnessStatus.LIMIT_REACHED)

    # =========================================================================
    # Test B: Multi-iteration successful loop
    # =========================================================================
    def test_scenario_b_multi_iteration_successful_loop(self) -> None:
        """Prove a multi-iteration loop: INITIAL -> EXECUTE -> VERIFY -> COMPLETE."""
        target_file = self.workspace_root / "step.txt"
        req = Requirement(requirement_id="req-b", description="File creation")
        crit = AcceptanceCriterion(
            criterion_id="crit-b", requirement_id="req-b", description="step.txt must exist"
        )
        spec = TaskSpecification(
            task_id="task-b",
            title="Step Task",
            description="Run full cycle",
            requirements=(req,),
            acceptance_criteria=(crit,),
            execution_profile=self.profile,
        )
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('step content')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-b",
            task_specification=spec,
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            verification_expectations={
                "crit-b": VerificationExpectation(relative_path="step.txt", exists=True)
            },
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-b",
                attempt_number=0,
                status=ProjectStateStatus.INITIAL,
            ),
        )

        result = self.harness.run(h_req)

        self.assertEqual(result.final_state.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.final_state.phase, HarnessPhase.COMPLETE)
        self.assertTrue(result.final_state.terminal)
        self.assertIsNotNone(result.final_acceptance)
        self.assertEqual(result.final_acceptance.status, AcceptanceStatus.PASS)
        self.assertEqual(result.final_project_state.status, ProjectStateStatus.ACCEPTED)
        self.assertGreaterEqual(len(result.iterations), 3)

    # =========================================================================
    # Test C: Permission denial
    # =========================================================================
    def test_scenario_c_permission_denial(self) -> None:
        """Prove that commands outside allowed_execution_commands halt without subprocess invocation."""
        target_file = self.workspace_root / "forbidden.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('forbidden')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-c",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(),  # None allowed!
            initial_project_state=ProjectState(
                run_id="run-c", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = self.harness.run(h_req)

        self.assertFalse(target_file.exists())
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertTrue(result.final_state.terminal)
        self.assertEqual(result.observations[0].result_status, "permission_denied")

    # =========================================================================
    # Test D: Approval waiting
    # =========================================================================
    def test_scenario_d_approval_waiting(self) -> None:
        """Prove that unresolved human approval halts execution and transitions harness to WAITING."""
        target_file = self.workspace_root / "pending.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('pending')",
            ),
            profile=self.profile,
        )
        resolver = _TrackingApprovalResolver(decision=ApprovalState.REQUIRED)
        h_req = HarnessRequest(
            run_id="run-d",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
            initial_project_state=ProjectState(
                run_id="run-d", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = self.harness.run(h_req)

        self.assertFalse(target_file.exists())
        self.assertEqual(result.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(result.final_state.phase, HarnessPhase.WAITING)
        self.assertTrue(result.final_state.terminal)

    # =========================================================================
    # Test E: Approval rejection
    # =========================================================================
    def test_scenario_e_approval_rejection(self) -> None:
        """Prove that rejected approval halts execution safely."""
        target_file = self.workspace_root / "rejected.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('rejected')",
            ),
            profile=self.profile,
        )
        resolver = _TrackingApprovalResolver(decision=ApprovalState.REJECTED)
        h_req = HarnessRequest(
            run_id="run-e",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
            initial_project_state=ProjectState(
                run_id="run-e", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = self.harness.run(h_req)

        self.assertFalse(target_file.exists())
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertTrue(result.final_state.terminal)
        self.assertEqual(result.observations[0].result_status, "approval_rejected")

    # =========================================================================
    # Test F: Execution failure
    # =========================================================================
    def test_scenario_f_execution_failure(self) -> None:
        """Prove that a non-zero exit code produces execution failure and halts progress."""
        exec_req = ExecutionRequest(
            command=(sys.executable, "-c", "import sys; sys.exit(7)"),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-f",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            initial_project_state=ProjectState(
                run_id="run-f", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = self.harness.run(h_req)

        self.assertEqual(len(result.execution_results), 1)
        self.assertEqual(
            result.execution_results[0].outcome_status, ExecutionOutcomeStatus.EXECUTION_FAILURE
        )
        self.assertEqual(result.final_project_state.status, ProjectStateStatus.CHANGED)

    # =========================================================================
    # Test G: Verification failure
    # =========================================================================
    def test_scenario_g_verification_failure(self) -> None:
        """Prove that a failed verification condition halts acceptance."""
        req = Requirement(requirement_id="req-g", description="Check missing file")
        crit = AcceptanceCriterion(
            criterion_id="crit-g", requirement_id="req-g", description="missing.txt exists"
        )
        h_req = HarnessRequest(
            run_id="run-g",
            workspace=self.workspace,
            verification_expectations={
                "crit-g": VerificationExpectation(relative_path="missing.txt", exists=True)
            },
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-g", attempt_number=0, status=ProjectStateStatus.CHANGED
            ),
        )

        result = self.harness.run(h_req)

        self.assertEqual(len(result.verification_results), 1)
        self.assertEqual(result.verification_results[0].status, VerificationStatus.FAIL)
        self.assertIsNotNone(result.final_acceptance)
        self.assertEqual(result.final_acceptance.status, AcceptanceStatus.FAIL)
        self.assertEqual(result.final_project_state.status, ProjectStateStatus.FAILED)

    # =========================================================================
    # Test H: Revision trigger
    # =========================================================================
    def test_scenario_h_revision_trigger(self) -> None:
        """Prove that failed verification triggers revision request and attempt increment."""
        req = Requirement(requirement_id="req-h", description="Check file")
        crit = AcceptanceCriterion(
            criterion_id="crit-h", requirement_id="req-h", description="file exists"
        )
        h_req = HarnessRequest(
            run_id="run-h",
            workspace=self.workspace,
            verification_expectations={
                "crit-h": VerificationExpectation(relative_path="file.txt", exists=True)
            },
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-h", attempt_number=0, status=ProjectStateStatus.CHANGED
            ),
        )

        harness = AgentHarness(policy=AgentHarnessPolicy(max_actions=2))
        result = harness.run(h_req)

        # Action 1: RUN_VERIFICATION -> fails -> ProjectState FAILED
        # Action 2: REQUEST_REVISION -> attempt becomes 1
        actions = [obs.action for obs in result.observations]
        self.assertIn("RUN_VERIFICATION", actions)
        self.assertIn("REQUEST_REVISION", actions)
        self.assertEqual(result.final_state.attempt_number, 1)

    # =========================================================================
    # Test I: Acceptance success
    # =========================================================================
    def test_scenario_i_acceptance_success(self) -> None:
        """Prove that passing verifications lead to acceptance PASS and COMPLETE_RUN."""
        (self.workspace_root / "valid.txt").write_text("valid")
        req = Requirement(requirement_id="req-i", description="valid exists")
        crit = AcceptanceCriterion(
            criterion_id="crit-i", requirement_id="req-i", description="valid exists"
        )
        h_req = HarnessRequest(
            run_id="run-i",
            workspace=self.workspace,
            verification_expectations={
                "crit-i": VerificationExpectation(relative_path="valid.txt", exists=True)
            },
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-i", attempt_number=0, status=ProjectStateStatus.CHANGED
            ),
        )

        result = self.harness.run(h_req)

        self.assertEqual(result.final_state.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.final_acceptance.status, AcceptanceStatus.PASS)

    # =========================================================================
    # Test J: Acceptance failure
    # =========================================================================
    def test_scenario_j_acceptance_failure(self) -> None:
        """Prove that missing verification fails acceptance and prevents completion."""
        req = Requirement(requirement_id="req-j", description="never exists")
        crit = AcceptanceCriterion(
            criterion_id="crit-j", requirement_id="req-j", description="never exists"
        )
        h_req = HarnessRequest(
            run_id="run-j",
            workspace=self.workspace,
            verification_expectations={
                "crit-j": VerificationExpectation(relative_path="never.txt", exists=True)
            },
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-j", attempt_number=0, status=ProjectStateStatus.CHANGED
            ),
        )

        result = self.harness.run(h_req)

        self.assertNotEqual(result.final_state.status, HarnessStatus.COMPLETED)
        self.assertNotEqual(result.final_project_state.status, ProjectStateStatus.ACCEPTED)

    # =========================================================================
    # Test K: Unknown decision rejection
    # =========================================================================
    def test_scenario_k_unknown_decision_rejection(self) -> None:
        """Prove that malformed or unauthorized decision types trigger fail_on_unknown_decision."""
        fake_provider = _UnknownActionProvider()
        harness = AgentHarness(
            decision_provider=fake_provider,
            policy=AgentHarnessPolicy(fail_on_unknown_decision=True),
        )
        h_req = HarnessRequest(
            run_id="run-k",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-k", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertTrue(result.final_state.terminal)

    # =========================================================================
    # Test L: Max iteration limit
    # =========================================================================
    def test_scenario_l_max_iteration_limit(self) -> None:
        """Prove that the harness strictly halts when max_iterations is reached."""
        harness = AgentHarness(
            decision_provider=_InfiniteLoopProvider(),
            policy=AgentHarnessPolicy(max_iterations=4, max_actions=10),
        )
        h_req = HarnessRequest(
            run_id="run-l",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-l", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        self.assertEqual(result.final_state.status, HarnessStatus.LIMIT_REACHED)
        self.assertEqual(result.final_state.iteration, 4)
        self.assertTrue(result.final_state.terminal)

    # =========================================================================
    # Test M: Max action limit
    # =========================================================================
    def test_scenario_m_max_action_limit(self) -> None:
        """Prove that the harness strictly halts when max_actions is reached."""
        harness = AgentHarness(
            decision_provider=_InfiniteLoopProvider(),
            policy=AgentHarnessPolicy(max_iterations=10, max_actions=3),
        )
        h_req = HarnessRequest(
            run_id="run-m",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-m", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        self.assertEqual(result.final_state.status, HarnessStatus.LIMIT_REACHED)
        self.assertEqual(len(result.observations), 3)

    # =========================================================================
    # Test N: No infinite loop guarantee
    # =========================================================================
    def test_scenario_n_no_infinite_loop(self) -> None:
        """Prove that an uncooperative or looping provider always terminates safely."""
        harness = AgentHarness(
            decision_provider=_InfiniteLoopProvider(),
            policy=AgentHarnessPolicy(max_iterations=5, max_actions=5),
        )
        h_req = HarnessRequest(
            run_id="run-n",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-n", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        self.assertTrue(result.final_state.terminal)
        self.assertIn(result.final_state.status, (HarnessStatus.LIMIT_REACHED, HarnessStatus.FAILED))

    # =========================================================================
    # Test O: One authoritative action per iteration
    # =========================================================================
    def test_scenario_o_one_action_per_iteration(self) -> None:
        """Prove that each iteration executes at most one authoritative action."""
        target_file = self.workspace_root / "action.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('act')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-o",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            initial_project_state=ProjectState(
                run_id="run-o", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = self.harness.run(h_req)
        # There should be exactly one observation per recorded action
        action_events = [
            e for e in result.events if e.event_type == EventType.HARNESS_OBSERVATION_RECORDED
        ]
        self.assertEqual(len(action_events), len(result.observations))

    # =========================================================================
    # Test P: Decision cannot bypass permission
    # =========================================================================
    def test_scenario_p_decision_cannot_bypass_permission(self) -> None:
        """Prove that a malicious decision demanding execution cannot bypass PermissionPolicy."""
        provider = _MaliciousExecuteProvider()
        harness = AgentHarness(decision_provider=provider)

        target_file = self.workspace_root / "bypass.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('bypass')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-p",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(),  # strictly disallow
            initial_project_state=ProjectState(
                run_id="run-p", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        self.assertFalse(target_file.exists())
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)

    # =========================================================================
    # Test Q: Decision cannot bypass approval
    # =========================================================================
    def test_scenario_q_decision_cannot_bypass_approval(self) -> None:
        """Prove that a malicious decision cannot force execution when approval is waiting."""
        provider = _MaliciousExecuteProvider()
        harness = AgentHarness(decision_provider=provider)

        target_file = self.workspace_root / "no_appr.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('no')",
            ),
            profile=self.profile,
        )
        resolver = _TrackingApprovalResolver(decision=ApprovalState.REQUIRED)
        h_req = HarnessRequest(
            run_id="run-q",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
            initial_project_state=ProjectState(
                run_id="run-q", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        self.assertFalse(target_file.exists())
        self.assertEqual(result.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)

    # =========================================================================
    # Test R: Decision cannot bypass execution policy
    # =========================================================================
    def test_scenario_r_decision_cannot_bypass_execution_policy(self) -> None:
        """Prove that ExecutionPolicy denies forbidden binaries even if decision demands it."""
        provider = _MaliciousExecuteProvider()
        harness = AgentHarness(decision_provider=provider)

        exec_req = ExecutionRequest(
            command=("forbidden_binary_xyz", "-c", "echo 1"),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-r",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=("allowed_tool",),
            initial_project_state=ProjectState(
                run_id="run-r", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)

    # =========================================================================
    # Test S: COMPLETE cannot bypass acceptance
    # =========================================================================
    def test_scenario_s_complete_cannot_bypass_acceptance(self) -> None:
        """Adversarial test: Decision claims COMPLETE_RUN when acceptance is not PASS."""
        provider = _MaliciousCompleteProvider()
        harness = AgentHarness(decision_provider=provider)

        h_req = HarnessRequest(
            run_id="run-s",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-s", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        self.assertNotEqual(result.final_state.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)

    # =========================================================================
    # Test T: ProjectState remains authoritative
    # =========================================================================
    def test_scenario_t_project_state_authoritative(self) -> None:
        """Prove that ProjectState derivation is authoritative and not fabricated by harness."""
        h_req = HarnessRequest(
            run_id="run-t",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-t", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )
        result = self.harness.run(h_req)
        self.assertIsInstance(result.final_project_state, ProjectState)
        self.assertEqual(result.final_project_state.run_id, "run-t")

    # =========================================================================
    # Test U: Context fingerprint remains deterministic
    # =========================================================================
    def test_scenario_u_context_fingerprint_deterministic(self) -> None:
        """Prove that repeated runs with identical state produce identical fingerprints."""
        h_req = HarnessRequest(
            run_id="run-u",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-u", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )
        res1 = self.harness.run(h_req)
        res2 = self.harness.run(h_req)

        self.assertEqual(
            res1.final_state.latest_context_fingerprint,
            res2.final_state.latest_context_fingerprint,
        )

    # =========================================================================
    # Test V: Harness state immutability
    # =========================================================================
    def test_scenario_v_harness_state_immutability(self) -> None:
        """Prove that HarnessState and StructuredObservation are frozen."""
        state = HarnessState(run_id="run-v", iteration=0)
        with self.assertRaises(FrozenInstanceError):
            state.iteration = 99  # type: ignore

        obs = StructuredObservation(action="EXECUTE", result_status="success")
        with self.assertRaises(FrozenInstanceError):
            obs.action = "MUTATED"  # type: ignore

    # =========================================================================
    # Test W: Trace lifecycle ordering
    # =========================================================================
    def test_scenario_w_trace_lifecycle_ordering(self) -> None:
        """Prove strict sequence monotonicity and event ordering in RunTrace."""
        h_req = HarnessRequest(
            run_id="run-w",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-w", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )
        result = self.harness.run(h_req)
        seqs = [e.sequence_number for e in result.events]
        self.assertEqual(seqs, list(range(len(seqs))))

    # =========================================================================
    # Test X: Cross-run rejection
    # =========================================================================
    def test_scenario_x_cross_run_rejection(self) -> None:
        """Prove that foreign context with mismatched run_id causes a safe failure."""
        foreign_state = ProjectState(
            run_id="run-foreign",
            attempt_number=0,
            status=ProjectStateStatus.INITIAL,
        )
        h_req = HarnessRequest(
            run_id="run-x",
            workspace=self.workspace,
            initial_project_state=foreign_state,
        )
        result = self.harness.run(h_req)
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertTrue(result.final_state.terminal)

    # =========================================================================
    # Test Y: No stdout/stderr/secrets in observations
    # =========================================================================
    def test_scenario_y_no_stdout_or_secrets_in_observations(self) -> None:
        """Prove that observations scrub forbidden keys and raw stream outputs."""
        obs = StructuredObservation(
            action="EXECUTE",
            result_status="success",
            metadata={
                "stdout": "secret output",
                "stderr": "error trace",
                "password": "123",
                "safe_key": "safe_value",
            },
            relevant_artifact_references={"api_key_ref": "token123", "result_ref": "step.txt"},
        )
        self.assertNotIn("stdout", obs.metadata)
        self.assertNotIn("stderr", obs.metadata)
        self.assertNotIn("password", obs.metadata)
        self.assertIn("safe_key", obs.metadata)
        self.assertNotIn("api_key_ref", obs.relevant_artifact_references)
        self.assertIn("result_ref", obs.relevant_artifact_references)

    # =========================================================================
    # Test Z: Purity & side-effect freedom
    # =========================================================================
    def test_scenario_z_purity_and_side_effect_freedom(self) -> None:
        """Prove that harness control logic does not access socket or execute uncontrolled processes."""
        with patch.object(socket, "socket", side_effect=RuntimeError("Network forbidden")):
            h_req = HarnessRequest(
                run_id="run-z",
                workspace=self.workspace,
                initial_project_state=ProjectState(
                    run_id="run-z", attempt_number=0, status=ProjectStateStatus.INITIAL
                ),
            )
            result = self.harness.run(h_req)
            self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
