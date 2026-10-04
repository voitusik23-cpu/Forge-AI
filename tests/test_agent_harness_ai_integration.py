"""Stage 14 — Real AI Agent Harness Integration Test Suite.

Proves the controlled engineering loop with AIDecisionProvider:
Task
-> Agent Harness
-> Decision Context
-> AIDecisionProvider
-> Decision validation
-> Permission/Approval
-> Execution
-> Observation
-> Verification
-> Project State
-> next decision
-> terminal result

Invariant:
AI recommendation != authorization != execution.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from uuid import uuid4

from app.agent_runtime.harness import AgentHarness
from app.agent_runtime.models import (
    HarnessPhase,
    HarnessRequest,
    HarnessResult,
    HarnessStatus,
    StructuredObservation,
)
from app.agent_runtime.policy import AgentHarnessPolicy
from app.agents.providers.base import (
    Provider,
    ProviderConfig,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from app.context.assembler import DecisionContextAssembler
from app.context.models import (
    ContextFreshness,
    ContextItem,
    ContextSensitivity,
    ContextSourceType,
    ContextTrustLevel,
    DecisionContextEnvelope,
)
from app.decision.ai_provider import AIDecisionProvider
from app.decision.models import (
    Decision,
    DecisionAction,
    DecisionRequest,
    DecisionType,
)
from app.decision.provider import DeterministicDecisionProvider
from app.decision.validator import validate_decision
from app.execution.authorizer import ExecutionCoordinator
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus, ExecutionRequest
from app.orchestrator.models import EventType
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
    ApprovalState,
)
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
    WorkspaceVerifier,
)
from app.tools.workspace import Workspace


class _ScriptedAIModelProvider(Provider):
    """Deterministic, provider-neutral model test double for scripted multi-turn AI decisions."""

    def __init__(self, responses: list[dict[str, object] | str] | None = None) -> None:
        super().__init__(ProviderConfig(provider_name="scripted_ai", model_name="ai-v1"))
        self._responses: list[dict[str, object] | str] = list(responses or [])
        self.received_requests: list[ProviderRequest] = []

    def set_responses(self, responses: list[dict[str, object] | str]) -> None:
        self._responses = list(responses)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.received_requests.append(request)
        if not self._responses:
            # Default fallback: recommend WAIT
            return ProviderResponse(
                provider_name=self.provider_name,
                model_name=self.model_name,
                output=json.dumps({
                    "decision_type": "WAIT",
                    "action": "WAIT_FOR_APPROVAL",
                    "reason_code": "no_scripted_response",
                    "rationale": "No further scripted response available",
                    "confidence": 1.0,
                }),
            )
        resp = self._responses.pop(0)
        output_str = resp if isinstance(resp, str) else json.dumps(resp)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output=output_str,
        )


class _OutageModelProvider(Provider):
    """Model provider simulating an upstream service failure."""

    def __init__(self, message: str = "Rate limit reached / service timeout") -> None:
        super().__init__(ProviderConfig(provider_name="outage_ai", model_name="fail-v1"))
        self.message = message

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        raise ProviderError(self.message)


class _TrackingApprovalResolver:
    """Approval resolver for human-in-the-loop tests."""

    def __init__(self, decision: ApprovalState = ApprovalState.REQUIRED) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    def resolve(self, request: ApprovalRequest) -> ApprovalState | None:
        self.requests.append(request)
        if self.decision == ApprovalState.REQUIRED:
            return None
        return self.decision


class TestRealAIAgentHarnessIntegration(unittest.TestCase):
    """Stage 14: End-to-end integration of AIDecisionProvider with AgentHarness."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="forge_stage14_test_")
        self.workspace_root = pathlib.Path(self.temp_dir)
        self.workspace = Workspace(root=self.workspace_root)
        self.profile = ProjectExecutionProfile(
            profile_id="prof-stage14",
            allowed_commands=(sys.executable,),
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # Test A: Real AI decision enters harness
    # =========================================================================
    def test_scenario_a_real_ai_decision_enters_harness(self) -> None:
        """Prove that a real AI decision enters the harness loop and is emitted in trace."""
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "initial_execute",
                "rationale": "Initial build script must execute",
                "confidence": 0.95,
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(
            decision_provider=ai_provider,
            policy=AgentHarnessPolicy(max_actions=1),
        )

        exec_file = self.workspace_root / "test_a.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{exec_file}').write_text('scenario_a')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-14-a",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            initial_project_state=ProjectState(
                run_id="run-14-a", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        # 1. AI Decision was made and stored
        self.assertEqual(len(result.decisions), 1)
        dec = result.decisions[0]
        self.assertEqual(dec.action, DecisionAction.EXECUTE)
        self.assertEqual(dec.decision_type, DecisionType.CONTINUE)
        self.assertEqual(dec.references.get("source"), "ai_decision_provider")

        # 2. DECISION_MADE event in trace
        dec_events = [e for e in result.events if e.event_type == EventType.DECISION_MADE]
        self.assertEqual(len(dec_events), 1)
        self.assertEqual(dec_events[0].metadata.get("action"), "EXECUTE")

        # 3. File was created
        self.assertTrue(exec_file.exists())
        self.assertEqual(exec_file.read_text(), "scenario_a")

    # =========================================================================
    # Test B: Valid AI EXECUTE decision reaches authorization
    # =========================================================================
    def test_scenario_b_valid_ai_execute_decision_reaches_authorization(self) -> None:
        """Prove that AI EXECUTE recommendation triggers authorization and coordinator execution."""
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "execute_tool",
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(
            decision_provider=ai_provider,
            policy=AgentHarnessPolicy(max_actions=1),
        )

        exec_file = self.workspace_root / "test_b.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{exec_file}').write_text('scenario_b')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-14-b",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            initial_project_state=ProjectState(
                run_id="run-14-b", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        # Trace must contain authoritative lifecycle events
        event_types = [e.event_type for e in result.events]
        self.assertIn(EventType.EXECUTION_REQUESTED, event_types)
        self.assertIn(EventType.EXECUTION_POLICY_CHECKED, event_types)
        self.assertIn(EventType.EXECUTION_STARTED, event_types)
        self.assertIn(EventType.EXECUTION_COMPLETED, event_types)
        self.assertEqual(len(result.execution_results), 1)
        self.assertEqual(
            result.execution_results[0].outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS
        )

    # =========================================================================
    # Test C: Permission DENY prevents execution even when AI says EXECUTE
    # =========================================================================
    def test_scenario_c_permission_deny_prevents_execution(self) -> None:
        """Prove that AI recommendation cannot bypass PermissionPolicy allowlist."""
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "ai_demands_execution",
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        forbidden_file = self.workspace_root / "forbidden.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{forbidden_file}').write_text('denied')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-14-c",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(),  # Strictly empty allowlist!
            initial_project_state=ProjectState(
                run_id="run-14-c", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        self.assertFalse(forbidden_file.exists())
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertEqual(result.observations[0].result_status, "permission_denied")

    # =========================================================================
    # Test D: Approval requirement prevents execution until approved
    # =========================================================================
    def test_scenario_d_approval_requirement_prevents_execution(self) -> None:
        """Prove that AI recommendation cannot bypass human approval gate."""
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "execute_privileged",
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        pending_file = self.workspace_root / "pending.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{pending_file}').write_text('never')",
            ),
            profile=self.profile,
        )
        resolver = _TrackingApprovalResolver(decision=ApprovalState.REQUIRED)
        h_req = HarnessRequest(
            run_id="run-14-d",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
            initial_project_state=ProjectState(
                run_id="run-14-d", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        self.assertFalse(pending_file.exists())
        self.assertEqual(result.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(result.final_state.phase, HarnessPhase.WAITING)
        self.assertTrue(result.final_state.terminal)

    # =========================================================================
    # Test E: Execution result becomes bounded observation
    # =========================================================================
    def test_scenario_e_execution_result_becomes_bounded_observation(self) -> None:
        """Prove that execution result is transformed into a secret-safe StructuredObservation."""
        scripted_model = _ScriptedAIModelProvider([
            {"decision_type": "CONTINUE", "action": "EXECUTE", "reason_code": "act"}
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(
            decision_provider=ai_provider,
            policy=AgentHarnessPolicy(max_actions=1),
        )

        exec_req = ExecutionRequest(
            command=(sys.executable, "-c", "print('hello_stdout')"),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-14-e",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            initial_project_state=ProjectState(
                run_id="run-14-e", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.action, "EXECUTE")
        self.assertEqual(obs.result_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS.value)
        self.assertIsNotNone(obs.execution_result_id)
        # Raw stdout must be scrubbed from observation metadata
        self.assertNotIn("stdout", obs.metadata)
        self.assertNotIn("hello_stdout", str(obs.metadata))

    # =========================================================================
    # Test F: Verification result becomes bounded observation
    # =========================================================================
    def test_scenario_f_verification_result_becomes_bounded_observation(self) -> None:
        """Prove that running verification produces a bounded StructuredObservation."""
        scripted_model = _ScriptedAIModelProvider([
            {"decision_type": "VERIFY", "action": "RUN_VERIFICATION", "reason_code": "verify"}
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(
            decision_provider=ai_provider,
            policy=AgentHarnessPolicy(max_actions=1),
        )

        # Pre-create verified file
        verified_file = self.workspace_root / "verified.txt"
        verified_file.write_text("verified_content")

        req = Requirement(requirement_id="req-f", description="verified file exists")
        crit = AcceptanceCriterion(
            criterion_id="crit-f", requirement_id="req-f", description="verified file exists"
        )
        h_req = HarnessRequest(
            run_id="run-14-f",
            workspace=self.workspace,
            verification_expectations={
                "crit-f": VerificationExpectation(relative_path="verified.txt", exists=True)
            },
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-14-f", attempt_number=0, status=ProjectStateStatus.CHANGED
            ),
        )

        result = harness.run(h_req)

        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.action, "RUN_VERIFICATION")
        self.assertEqual(obs.acceptance_status, "pass")
        self.assertIsNotNone(obs.verification_id)

    # =========================================================================
    # Test G: Second AI decision sees updated bounded context
    # =========================================================================
    def test_scenario_g_second_ai_decision_sees_updated_context(self) -> None:
        """Prove a multi-turn controlled loop: AI executes, sees CHANGED state, verifies, sees ACCEPTED state, completes."""
        target_file = self.workspace_root / "app_result.txt"
        req = Requirement(requirement_id="req-g", description="app_result exists")
        crit = AcceptanceCriterion(
            criterion_id="crit-g", requirement_id="req-g", description="app_result exists"
        )

        scripted_model = _ScriptedAIModelProvider([
            # Turn 1: execute build
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "build_artifact",
                "rationale": "Initial state requires build",
            },
            # Turn 2: verify
            {
                "decision_type": "VERIFY",
                "action": "RUN_VERIFICATION",
                "reason_code": "verify_artifact",
                "rationale": "Changed state requires verification",
            },
            # Turn 3: complete
            {
                "decision_type": "COMPLETE",
                "action": "COMPLETE_RUN",
                "reason_code": "acceptance_passed",
                "rationale": "Verified state satisfies acceptance criteria",
            },
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('artifact')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-14-g",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            verification_expectations={
                "crit-g": VerificationExpectation(relative_path="app_result.txt", exists=True)
            },
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-14-g", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        # 1. Run reached terminal completion through authoritative acceptance
        self.assertEqual(result.final_state.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.final_state.phase, HarnessPhase.COMPLETE)
        self.assertEqual(result.final_project_state.status, ProjectStateStatus.ACCEPTED)

        # 2. Verify model was queried 3 times
        self.assertEqual(len(scripted_model.received_requests), 3)

        # 3. Turn 1 prompt saw INITIAL
        prompt_1 = scripted_model.received_requests[0].prompt
        self.assertIn("Project State Status: INITIAL", prompt_1)

        # 4. Turn 2 prompt saw CHANGED
        prompt_2 = scripted_model.received_requests[1].prompt
        self.assertIn("Project State Status: CHANGED", prompt_2)

        # 5. Turn 3 prompt saw ACCEPTED / pass
        prompt_3 = scripted_model.received_requests[2].prompt
        self.assertIn("Acceptance Status: pass", prompt_3)

    # =========================================================================
    # Test H: AI cannot terminate successfully before authoritative acceptance
    # =========================================================================
    def test_scenario_h_ai_cannot_terminate_before_authoritative_acceptance(self) -> None:
        """Prove that AI proposing premature COMPLETE_RUN is rejected by validator and halts in FAILED."""
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "COMPLETE",
                "action": "COMPLETE_RUN",
                "reason_code": "premature_complete",
                "rationale": "Model claims completion prematurely",
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        h_req = HarnessRequest(
            run_id="run-14-h",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-14-h", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        self.assertNotEqual(result.final_state.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertTrue(result.final_state.terminal)

        # Verify DECISION_REJECTED event
        rejected_events = [e for e in result.events if e.event_type == EventType.DECISION_REJECTED]
        self.assertEqual(len(rejected_events), 1)
        self.assertIn(
            "premature_completion:acceptance_not_passed",
            rejected_events[0].metadata.get("errors", []),
        )

    # =========================================================================
    # Test I: Malformed AI decision fails closed
    # =========================================================================
    def test_scenario_i_malformed_ai_decision_fails_closed(self) -> None:
        """Prove that conversational or corrupt AI responses halt the harness in FAILED."""
        scripted_model = _ScriptedAIModelProvider(["Sorry, I cannot assist with this task."])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        h_req = HarnessRequest(
            run_id="run-14-i",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-14-i", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertTrue(result.final_state.terminal)
        self.assertEqual(result.decisions[0].reason_code, "malformed_json")

    # =========================================================================
    # Test J: Provider failure fails closed
    # =========================================================================
    def test_scenario_j_provider_failure_fails_closed(self) -> None:
        """Prove that model provider exceptions halt the harness safely without crashing."""
        failing_provider = _OutageModelProvider("503 Service Unavailable")
        ai_provider = AIDecisionProvider(provider=failing_provider)
        harness = AgentHarness(decision_provider=ai_provider)

        h_req = HarnessRequest(
            run_id="run-14-j",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-14-j", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertTrue(result.final_state.terminal)
        self.assertEqual(result.decisions[0].reason_code, "provider_failure")

    # =========================================================================
    # Test K: Iteration and action limits are enforced
    # =========================================================================
    def test_scenario_k_limits_are_enforced_against_infinite_ai_loop(self) -> None:
        """Prove that harness strictly caps looping AI decisions at configured bounds."""
        # Provider that continuously recommends REVISE (non-terminal loop)
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "REVISE",
                "action": "REQUEST_REVISION",
                "reason_code": "looping_revision",
                "rationale": "Model repeatedly requests revisions",
                "confidence": 1.0,
            }
            for _ in range(10)
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(
            decision_provider=ai_provider,
            policy=AgentHarnessPolicy(max_iterations=4, max_actions=10, max_revision_attempts=10),
        )

        h_req = HarnessRequest(
            run_id="run-14-k",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-14-k", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        self.assertEqual(result.final_state.status, HarnessStatus.LIMIT_REACHED)
        self.assertEqual(result.final_state.iteration, 4)
        self.assertTrue(result.final_state.terminal)

    # =========================================================================
    # Test L: Cross-run isolation
    # =========================================================================
    def test_scenario_l_cross_run_isolation(self) -> None:
        """Prove that foreign context with mismatched run_id causes a safe failure."""
        scripted_model = _ScriptedAIModelProvider([
            {"decision_type": "CONTINUE", "action": "EXECUTE", "reason_code": "ok"}
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        foreign_state = ProjectState(
            run_id="foreign-run-id",
            attempt_number=0,
            status=ProjectStateStatus.INITIAL,
        )
        h_req = HarnessRequest(
            run_id="run-14-l",
            workspace=self.workspace,
            initial_project_state=foreign_state,
        )

        result = harness.run(h_req)

        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertTrue(result.final_state.terminal)

    # =========================================================================
    # Test M: Secret-safe trace
    # =========================================================================
    def test_scenario_m_secret_safe_trace(self) -> None:
        """Prove that secrets present in metadata are never leaked into prompt, observations, or events."""
        secret_key = "MY_SUPER_SECRET_KEY_1414"
        scripted_model = _ScriptedAIModelProvider([
            {"decision_type": "CONTINUE", "action": "EXECUTE", "reason_code": "ok"}
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(
            decision_provider=ai_provider,
            policy=AgentHarnessPolicy(max_actions=1),
        )

        exec_req = ExecutionRequest(
            command=(sys.executable, "-c", "import sys; sys.exit(0)"),
            profile=self.profile,
            metadata={"api_key": secret_key, "password": "pass"},
        )
        h_req = HarnessRequest(
            run_id="run-14-m",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            initial_project_state=ProjectState(
                run_id="run-14-m", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
            metadata={"secret_token": secret_key},
        )

        result = harness.run(h_req)

        # Check prompt
        for req in scripted_model.received_requests:
            self.assertNotIn(secret_key, req.prompt)

        # Check trace events
        for event in result.events:
            self.assertNotIn(secret_key, str(event.metadata))

        # Check observations
        for obs in result.observations:
            self.assertNotIn(secret_key, str(obs.metadata))

    # =========================================================================
    # Test N: No direct tool execution by AIDecisionProvider
    # =========================================================================
    def test_scenario_n_no_direct_tool_execution_by_ai_provider(self) -> None:
        """Prove that AIDecisionProvider contains zero tool execution methods."""
        ai_provider = AIDecisionProvider(provider=_ScriptedAIModelProvider())
        for forbidden_method in ("execute", "run_command", "execute_tool", "execute_action"):
            self.assertFalse(hasattr(ai_provider, forbidden_method))

    # =========================================================================
    # Test O: Deterministic provider path still works
    # =========================================================================
    def test_scenario_o_deterministic_provider_path_still_works(self) -> None:
        """Prove that existing DeterministicDecisionProvider works without regression in the harness."""
        harness = AgentHarness(
            decision_provider=DeterministicDecisionProvider(),
            policy=AgentHarnessPolicy(max_actions=1),
        )

        target_file = self.workspace_root / "det.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('deterministic')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-14-o",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            initial_project_state=ProjectState(
                run_id="run-14-o", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_text(), "deterministic")
        self.assertEqual(result.final_state.status, HarnessStatus.LIMIT_REACHED)

    # =========================================================================
    # Optional Live Provider Smoke Test
    # =========================================================================
    @unittest.skipUnless(os.environ.get("OPENAI_API_KEY"), "OpenAI API key not set in environment")
    def test_optional_live_provider_smoke(self) -> None:
        """Optional smoke test against real OpenAI API when credentials are provided."""
        from app.agents.providers.openai import OpenAIProvider

        real_provider = OpenAIProvider()
        ai_decision_provider = AIDecisionProvider(provider=real_provider)
        dec_req = DecisionRequest(
            run_id="run-14-live-smoke",
            attempt_number=0,
            current_project_state=ProjectState(
                run_id="run-14-live-smoke", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
            available_actions=(DecisionAction.EXECUTE, DecisionAction.WAIT_FOR_APPROVAL),
        )
        decision = ai_decision_provider.decide(dec_req)
        self.assertIn(decision.action, (DecisionAction.EXECUTE, DecisionAction.WAIT_FOR_APPROVAL))
        self.assertEqual(decision.run_id, "run-14-live-smoke")


if __name__ == "__main__":
    unittest.main()
