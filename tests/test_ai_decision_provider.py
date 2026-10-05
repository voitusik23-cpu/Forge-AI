"""Comprehensive unit and adversarial test suite for Forge AI Real AI Decision Provider (Stage 13).

Validates the invariant:
AI recommendation != authorization != execution.
"""

from __future__ import annotations

import json
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
)
from app.agent_runtime.policy import AgentHarnessPolicy
from app.agents.providers.base import (
    Provider,
    ProviderConfig,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
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
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest
from app.projects.state import ProjectState, ProjectStateStatus
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalState,
)
from app.tools.workspace import Workspace


class _ConfigurableModelProvider(Provider):
    """Test model provider returning configured response text and capturing requests."""

    def __init__(self, output_text: str = "") -> None:
        super().__init__(ProviderConfig(provider_name="test_ai", model_name="test-model-v1"))
        self.output_text = output_text
        self.received_requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.received_requests.append(request)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output=self.output_text,
        )


class _FailingModelProvider(Provider):
    """Test model provider that always raises a ProviderError."""

    def __init__(self, message: str = "Simulated upstream LLM API outage") -> None:
        super().__init__(ProviderConfig(provider_name="failing_ai", model_name="fail-v1"))
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


class TestAIDecisionProvider(unittest.TestCase):
    """Stage 13 verification suite for Real AI Decision Provider."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="forge_stage13_test_")
        self.workspace_root = pathlib.Path(self.temp_dir)
        self.workspace = Workspace(root=self.workspace_root)
        self.profile = ProjectExecutionProfile(
            profile_id="prof-stage13",
            allowed_commands=(sys.executable,),
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # Scenario A: Valid AI Decision
    # =========================================================================
    def test_scenario_a_valid_ai_decision(self) -> None:
        """Prove that valid JSON from an LLM is parsed into a verified Decision and executed cleanly."""
        target_file = self.workspace_root / "ai_step.txt"
        model_output = json.dumps({
            "decision_type": "CONTINUE",
            "action": "EXECUTE",
            "reason_code": "execution_required",
            "rationale": "Initial state requires command execution to generate output",
            "confidence": 0.95,
        })
        provider = _ConfigurableModelProvider(model_output)
        ai_decision_provider = AIDecisionProvider(provider=provider)

        from app.execution.identity import CommandIdentity

        cmd = (
            sys.executable,
            "-c",
            f"import pathlib; pathlib.Path(r'{target_file}').write_text('ai_generated')",
        )
        cid = CommandIdentity(cmd[0], cmd[1:])
        exec_profile = ProjectExecutionProfile(
            profile_id=self.profile.profile_id,
            allowed_commands=(*self.profile.allowed_commands, cid),
        )
        exec_req = ExecutionRequest(
            command=cmd,
            profile=exec_profile,
        )
        h_req = HarnessRequest(
            run_id="run-a-ai",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable, cid),
            initial_project_state=ProjectState(
                run_id="run-a-ai", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        harness = AgentHarness(
            decision_provider=ai_decision_provider,
            policy=AgentHarnessPolicy(max_actions=1),
        )
        result = harness.run(h_req)

        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_text(), "ai_generated")
        self.assertEqual(len(result.decisions), 1)
        self.assertEqual(result.decisions[0].action, DecisionAction.EXECUTE)
        self.assertEqual(result.decisions[0].decision_type, DecisionType.CONTINUE)
        self.assertEqual(result.decisions[0].reason_code, "execution_required")
        self.assertEqual(result.decisions[0].confidence, 0.95)
        self.assertEqual(result.final_state.status, HarnessStatus.LIMIT_REACHED)

    # =========================================================================
    # Scenario B: Malformed JSON
    # =========================================================================
    def test_scenario_b_malformed_json_fails_closed(self) -> None:
        """Prove that unparseable or conversational model output fails closed safely."""
        provider = _ConfigurableModelProvider("I recommend that we proceed with the build now.")
        ai_decision_provider = AIDecisionProvider(provider=provider)

        dec_req = DecisionRequest(run_id="run-b-malformed", attempt_number=0)
        decision = ai_decision_provider.decide(dec_req)

        self.assertEqual(decision.decision_type, DecisionType.FAIL)
        self.assertEqual(decision.action, DecisionAction.FAIL_RUN)
        self.assertEqual(decision.reason_code, "malformed_json")
        self.assertTrue(decision.references.get("fail_closed"))

        # In harness, it must transition safely to FAILED
        h_req = HarnessRequest(
            run_id="run-b-malformed",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-b-malformed", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )
        harness = AgentHarness(decision_provider=ai_decision_provider)
        result = harness.run(h_req)

        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertEqual(result.final_state.phase, HarnessPhase.FAILED)
        self.assertTrue(result.final_state.terminal)

    # =========================================================================
    # Scenario C: Unknown Action
    # =========================================================================
    def test_scenario_c_unknown_action_fails_closed(self) -> None:
        """Prove that non-existent or unsupported actions trigger a safe fail-closed decision."""
        model_output = json.dumps({
            "decision_type": "CONTINUE",
            "action": "ARBITRARY_SHELL_COMMAND",
            "reason_code": "unknown_action_test",
        })
        provider = _ConfigurableModelProvider(model_output)
        ai_decision_provider = AIDecisionProvider(provider=provider)

        dec_req = DecisionRequest(run_id="run-c-unknown", attempt_number=0)
        decision = ai_decision_provider.decide(dec_req)

        self.assertEqual(decision.decision_type, DecisionType.FAIL)
        self.assertEqual(decision.action, DecisionAction.FAIL_RUN)
        self.assertEqual(decision.reason_code, "unknown_action")
        self.assertTrue(decision.references.get("fail_closed"))

    # =========================================================================
    # Scenario D: Premature COMPLETE_RUN
    # =========================================================================
    def test_scenario_d_premature_complete_run_rejected_by_validator(self) -> None:
        """Prove that AI cannot prematurely complete a run when acceptance criteria have not passed."""
        model_output = json.dumps({
            "decision_type": "COMPLETE",
            "action": "COMPLETE_RUN",
            "reason_code": "skip_checks_and_complete",
            "rationale": "Model claims the work is perfect without running verification",
        })
        provider = _ConfigurableModelProvider(model_output)
        ai_decision_provider = AIDecisionProvider(provider=provider)

        dec_req = DecisionRequest(
            run_id="run-d-premature",
            attempt_number=0,
            acceptance_status="fail",  # Not pass!
            current_project_state=ProjectState(
                run_id="run-d-premature", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )
        decision = ai_decision_provider.decide(dec_req)

        # Decision is COMPLETE_RUN, but validation report must reject it
        report = validate_decision(decision, dec_req, dec_req.current_project_state)
        self.assertFalse(report.valid)
        self.assertIn("premature_completion:acceptance_not_passed", report.errors)

        # Inside harness, the harness validator rejects it and halts in FAILED
        h_req = HarnessRequest(
            run_id="run-d-premature",
            workspace=self.workspace,
            initial_project_state=ProjectState(
                run_id="run-d-premature", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )
        harness = AgentHarness(decision_provider=ai_decision_provider)
        result = harness.run(h_req)

        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertNotEqual(result.final_state.status, HarnessStatus.COMPLETED)

    # =========================================================================
    # Scenario E: EXECUTE with Permission DENY
    # =========================================================================
    def test_scenario_e_execute_with_permission_deny(self) -> None:
        """Prove that AI decision proposing EXECUTE cannot bypass PermissionPolicy."""
        model_output = json.dumps({
            "decision_type": "CONTINUE",
            "action": "EXECUTE",
            "reason_code": "execute_binary",
        })
        provider = _ConfigurableModelProvider(model_output)
        ai_decision_provider = AIDecisionProvider(provider=provider)

        forbidden_target = self.workspace_root / "forbidden.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{forbidden_target}').write_text('bad')",
            ),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-e-deny",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(),  # strictly disallow all!
            initial_project_state=ProjectState(
                run_id="run-e-deny", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        harness = AgentHarness(decision_provider=ai_decision_provider)
        result = harness.run(h_req)

        self.assertFalse(forbidden_target.exists())
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertEqual(result.observations[0].result_status, "permission_denied")

    # =========================================================================
    # Scenario F: Approval Required
    # =========================================================================
    def test_scenario_f_approval_required(self) -> None:
        """Prove that AI decision proposing EXECUTE halts when human approval is required."""
        model_output = json.dumps({
            "decision_type": "CONTINUE",
            "action": "EXECUTE",
            "reason_code": "needs_privileged_action",
        })
        provider = _ConfigurableModelProvider(model_output)
        ai_decision_provider = AIDecisionProvider(provider=provider)

        pending_target = self.workspace_root / "pending.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{pending_target}').write_text('pending')",
            ),
            profile=self.profile,
        )
        resolver = _TrackingApprovalResolver(decision=ApprovalState.REQUIRED)
        h_req = HarnessRequest(
            run_id="run-f-approval",
            workspace=self.workspace,
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=ApprovalPolicy(approval_required_tools=("execute", sys.executable)),
            approval_resolver=resolver,
            initial_project_state=ProjectState(
                run_id="run-f-approval", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        harness = AgentHarness(decision_provider=ai_decision_provider)
        result = harness.run(h_req)

        self.assertFalse(pending_target.exists())
        self.assertEqual(result.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(result.final_state.phase, HarnessPhase.WAITING)

    # =========================================================================
    # Scenario G: Provider Failure
    # =========================================================================
    def test_scenario_g_provider_failure_fails_closed(self) -> None:
        """Prove that network, SDK, or provider exceptions fail closed safely without crashing."""
        failing_provider = _FailingModelProvider("OpenAI rate limit exceeded")
        ai_decision_provider = AIDecisionProvider(provider=failing_provider)

        dec_req = DecisionRequest(run_id="run-g-fail", attempt_number=0)
        decision = ai_decision_provider.decide(dec_req)

        self.assertEqual(decision.decision_type, DecisionType.FAIL)
        self.assertEqual(decision.action, DecisionAction.FAIL_RUN)
        self.assertEqual(decision.reason_code, "provider_failure")
        self.assertEqual(decision.references.get("error_type"), "ProviderError")
        self.assertTrue(decision.references.get("fail_closed"))

    # =========================================================================
    # Scenario H: Deterministic Provider Still Works
    # =========================================================================
    def test_scenario_h_deterministic_provider_remains_operational(self) -> None:
        """Verify that the baseline DeterministicDecisionProvider is completely intact and operational."""
        det_provider = DeterministicDecisionProvider()
        dec_req = DecisionRequest(
            run_id="run-h-det",
            attempt_number=0,
            current_project_state=ProjectState(
                run_id="run-h-det", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )
        decision = det_provider.decide(dec_req)
        self.assertEqual(decision.action, DecisionAction.EXECUTE)
        self.assertEqual(decision.decision_type, DecisionType.CONTINUE)

    # =========================================================================
    # Scenario I: No Secret Leakage
    # =========================================================================
    def test_scenario_i_no_secret_leakage(self) -> None:
        """Prove that secrets, tokens, passwords, and sensitive context are never leaked into prompt or trace."""
        secret_val = "SECRET_TOKEN_XYZ_98765"
        password_val = "SUPER_SECRET_DB_PASSWORD"

        provider = _ConfigurableModelProvider(json.dumps({
            "decision_type": "CONTINUE",
            "action": "EXECUTE",
            "reason_code": "execution_step",
        }))
        ai_decision_provider = AIDecisionProvider(provider=provider)

        sensitive_item = ContextItem(
            item_id="item-secret",
            item_type="credential",
            source_type=ContextSourceType.SYSTEM_POLICY,
            value=f"token={secret_val}",
            trust_level=ContextTrustLevel.CONFIRMED,
            freshness=ContextFreshness.CURRENT,
            sensitivity=ContextSensitivity.CONFIDENTIAL,
        )
        envelope = DecisionContextEnvelope(
            context_id="ctx-secret",
            run_id="run-i-secrets",
            attempt_number=0,
            context_items=(sensitive_item,),
            metadata={"password": password_val, "safe_metric": 42},
        )
        dec_req = DecisionRequest(
            run_id="run-i-secrets",
            attempt_number=0,
            context_envelope=envelope,
            metadata={"api_key": secret_val},
        )

        decision = ai_decision_provider.decide(dec_req)

        # 1. Prompt sent to LLM must not contain the secret or password
        self.assertEqual(len(provider.received_requests), 1)
        prompt = provider.received_requests[0].prompt
        self.assertNotIn(secret_val, prompt)
        self.assertNotIn(password_val, prompt)

        # 2. Decision object must not contain the secret or password
        self.assertNotIn(secret_val, str(decision.references))
        self.assertNotIn(secret_val, str(decision.metadata))

    # =========================================================================
    # Scenario J: Same Input Produces Stable Normalized Prompt Structure
    # =========================================================================
    def test_scenario_j_stable_normalized_prompt_structure(self) -> None:
        """Prove that identical inputs produce bit-identical, normalized prompt structures."""
        ai_provider = AIDecisionProvider(provider=_ConfigurableModelProvider())

        # Construct two requests with identical semantic content but varying unordered inputs
        req1 = DecisionRequest(
            run_id="run-j-stable",
            attempt_number=1,
            task_id="task-j",
            blocking_conditions=("approval_pending", "revision_limit_reached"),
            available_actions=(DecisionAction.RUN_VERIFICATION, DecisionAction.EXECUTE),
        )
        req2 = DecisionRequest(
            run_id="run-j-stable",
            attempt_number=1,
            task_id="task-j",
            blocking_conditions=("revision_limit_reached", "approval_pending"),  # reversed order
            available_actions=(DecisionAction.EXECUTE, DecisionAction.RUN_VERIFICATION),  # reversed order
        )

        prompt1 = ai_provider.build_prompt(req1)
        prompt2 = ai_provider.build_prompt(req2)

        self.assertEqual(prompt1, prompt2)

    # =========================================================================
    # Scenario K: AI Provider Cannot Execute Tools Directly
    # =========================================================================
    def test_scenario_k_ai_provider_cannot_execute_tools_directly(self) -> None:
        """Prove that AIDecisionProvider has no tool execution capabilities and causes zero filesystem side-effects."""
        ai_provider = AIDecisionProvider(provider=_ConfigurableModelProvider(json.dumps({
            "decision_type": "CONTINUE",
            "action": "EXECUTE",
            "reason_code": "dummy",
        })))

        # 1. Verify absence of execution interfaces on the provider
        self.assertFalse(hasattr(ai_provider, "execute"))
        self.assertFalse(hasattr(ai_provider, "run_command"))
        self.assertFalse(hasattr(ai_provider, "execute_tool"))

        # 2. Verify filesystem remains untouched
        initial_files = list(self.workspace_root.iterdir())
        dec_req = DecisionRequest(run_id="run-k-no-exec", attempt_number=0)
        _ = ai_provider.decide(dec_req)
        after_files = list(self.workspace_root.iterdir())

        self.assertEqual(initial_files, after_files)

    # =========================================================================
    # Scenario L: Cross-Run Isolation
    # =========================================================================
    def test_scenario_l_cross_run_isolation(self) -> None:
        """Prove that decisions are strictly bound to their originating run_id."""
        ai_provider = AIDecisionProvider(provider=_ConfigurableModelProvider(json.dumps({
            "decision_type": "CONTINUE",
            "action": "EXECUTE",
            "reason_code": "isolated_step",
        })))

        req_a = DecisionRequest(run_id="run-L-alpha", attempt_number=0)
        req_b = DecisionRequest(run_id="run-L-beta", attempt_number=0)

        dec_a = ai_provider.decide(req_a)
        dec_b = ai_provider.decide(req_b)

        self.assertEqual(dec_a.run_id, "run-L-alpha")
        self.assertEqual(dec_b.run_id, "run-L-beta")
        self.assertNotEqual(dec_a.run_id, dec_b.run_id)

        # Cross-run validation rejection
        cross_validation = validate_decision(dec_a, req_b)
        self.assertFalse(cross_validation.valid)
        self.assertIn("run_id_mismatch", cross_validation.errors)


if __name__ == "__main__":
    unittest.main()
