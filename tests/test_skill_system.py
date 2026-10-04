"""Comprehensive test suite for Forge AI Agent Skill System v0.1 (Stage 15)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
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
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.context.assembler import DecisionContextAssembler
from app.context.models import ContextSourceType, ContextTrustLevel
from app.decision.ai_provider import AIDecisionProvider
from app.decision.models import Decision, DecisionAction, DecisionRequest, DecisionType
from app.decision.provider import DeterministicDecisionProvider
from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.policy import ExecutionPolicy
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus, ExecutionRequest
from app.projects.state import ProjectState, ProjectStateStatus
from app.skills.builtin import get_builtin_python_test_runner
from app.skills.evaluator import SkillEvaluator
from app.skills.models import (
    SkillApplicability,
    SkillDefinition,
    SkillManifest,
    SkillProvenance,
    SkillTrustLevel,
)
from app.skills.registry import DuplicateSkillError, SkillNotFoundError, SkillRegistry
from app.tasks.specification import AcceptanceCriterion, Requirement, TaskSpecification
from app.tools.approval import ApprovalPolicy, ApprovalRequest, ApprovalState, InMemoryApprovalResolver
from app.tools.permissions import PermissionDecision, PermissionPolicy
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace


class _ScriptedAIModelProvider(Provider):
    """Deterministic, provider-neutral model test double for scripted multi-turn AI decisions."""

    def __init__(self, responses: list[dict[str, object] | str] | None = None) -> None:
        super().__init__(ProviderConfig(provider_name="scripted_ai", model_name="ai-v1"))
        self._responses: list[dict[str, object] | str] = list(responses or [])
        self.received_requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.received_requests.append(request)
        if not self._responses:
            return ProviderResponse(
                provider_name=self.provider_name,
                model_name=self.model_name,
                output=json.dumps({
                    "decision_type": "WAIT",
                    "action": "WAIT_FOR_APPROVAL",
                    "reason_code": "no_scripted_response",
                    "rationale": "No further response scripted",
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


class TestAgentSkillSystem(unittest.TestCase):
    """Comprehensive test suite verifying Stage 15 Agent Skill contracts and invariants."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_path = Path(self.temp_dir.name).resolve()
        self.workspace = Workspace(root=self.workspace_path)
        self.profile = ProjectExecutionProfile(
            profile_id="test_profile",
            allowed_commands=(sys.executable,),
        )
        self.evaluator = SkillEvaluator()
        self.registry = SkillRegistry()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # =========================================================================
    # Test A: Skill Registration and Retrieval
    # =========================================================================
    def test_scenario_a_registration_and_retrieval(self) -> None:
        """Prove that skills can be registered, listed, retrieved, and checked for presence."""
        skill = get_builtin_python_test_runner()
        self.registry.register(skill)

        self.assertTrue(self.registry.contains("forge.builtin.python_test_runner"))
        retrieved = self.registry.get("forge.builtin.python_test_runner")
        self.assertEqual(retrieved.manifest.skill_id, "forge.builtin.python_test_runner")
        self.assertEqual(retrieved.manifest.name, "Python Test Runner")

        manifests = self.registry.list_manifests()
        self.assertEqual(len(manifests), 1)
        self.assertEqual(manifests[0].skill_id, "forge.builtin.python_test_runner")

        skills = self.registry.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].manifest.skill_id, "forge.builtin.python_test_runner")

        # Unknown skill lookup raises SkillNotFoundError
        with self.assertRaises(SkillNotFoundError):
            self.registry.get("nonexistent.skill")

    # =========================================================================
    # Duplicate skill ID rejection
    # =========================================================================
    def test_duplicate_skill_id_rejection(self) -> None:
        """Prove that registering the same skill ID twice raises DuplicateSkillError."""
        skill = get_builtin_python_test_runner()
        self.registry.register(skill)
        with self.assertRaises(DuplicateSkillError):
            self.registry.register(skill)

    # =========================================================================
    # Immutable skill objects
    # =========================================================================
    def test_immutable_skill_objects(self) -> None:
        """Prove that SkillManifest, SkillDefinition, and SkillProvenance are frozen."""
        skill = get_builtin_python_test_runner()
        with self.assertRaises(FrozenInstanceError):
            skill.manifest.name = "Mutated Name"  # type: ignore

        with self.assertRaises(FrozenInstanceError):
            skill.instructions = "Mutated instructions"  # type: ignore

        with self.assertRaises(FrozenInstanceError):
            skill.manifest.provenance.author = "Attacker"  # type: ignore

    # =========================================================================
    # Malformed metadata rejection
    # =========================================================================
    def test_malformed_skill_metadata_rejection(self) -> None:
        """Prove that metadata containing forbidden sensitive words is strictly rejected."""
        with self.assertRaises(ValueError):
            SkillManifest(
                skill_id="bad.skill.key",
                name="Bad Skill",
                version="1.0.0",
                description="Contains secret key",
                metadata={"api_key": "12345"},
            )

        with self.assertRaises(ValueError):
            SkillManifest(
                skill_id="bad.skill.val",
                name="Bad Skill",
                version="1.0.0",
                description="Contains token value",
                metadata={"notes": "bearer token included"},
            )

    # =========================================================================
    # Raw shell commands rejected in requested_tools
    # =========================================================================
    def test_raw_shell_command_rejected_in_requested_tools(self) -> None:
        """Prove that requested_tools rejects raw shell commands, flags, and spaces."""
        with self.assertRaises(ValueError):
            SkillManifest(
                skill_id="test.bad_cmd",
                name="Bad Command Skill",
                version="1.0.0",
                description="Attempting shell command",
                requested_tools=("python -m unittest",),  # Contains spaces!
            )

        with self.assertRaises(ValueError):
            SkillManifest(
                skill_id="test.bad_flag",
                name="Bad Flag Skill",
                version="1.0.0",
                description="Attempting flag",
                requested_tools=("-rf",),  # Starts with flag!
            )

        with self.assertRaises(ValueError):
            SkillManifest(
                skill_id="test.bad_pipe",
                name="Bad Pipe Skill",
                version="1.0.0",
                description="Attempting pipe",
                requested_tools=("cat|grep",),  # Contains pipe!
            )

    # =========================================================================
    # Provenance hash determinism
    # =========================================================================
    def test_provenance_hash_determinism(self) -> None:
        """Prove that identical instructions produce bit-identical sha256 content hashes."""
        skill_1 = get_builtin_python_test_runner()
        skill_2 = get_builtin_python_test_runner()
        self.assertEqual(
            skill_1.manifest.provenance.content_hash,
            skill_2.manifest.provenance.content_hash,
        )
        expected = hashlib.sha256(skill_1.instructions.encode("utf-8")).hexdigest()
        self.assertEqual(skill_1.manifest.provenance.content_hash, expected)

    # =========================================================================
    # Test B: Deterministic Applicability
    # =========================================================================
    def test_scenario_b_deterministic_applicability(self) -> None:
        """Prove that SkillEvaluator deterministically matches when capabilities and tools align."""
        skill = get_builtin_python_test_runner()
        applicability = self.evaluator.evaluate(
            skill,
            available_capabilities=frozenset({"process:execute"}),
            available_tool_ids=frozenset({"python_test_runner"}),
        )
        self.assertTrue(applicability.is_applicable)
        self.assertEqual(applicability.skill_id, "forge.builtin.python_test_runner")
        self.assertEqual(applicability.reason, "all_requirements_satisfied")
        self.assertIn("process:execute", applicability.matched_capabilities)
        self.assertEqual(applicability.missing_capabilities, ())

    # =========================================================================
    # Test C: Missing Capability Rejection
    # =========================================================================
    def test_scenario_c_missing_capability_rejection(self) -> None:
        """Prove that a skill is rejected when required capabilities are not available."""
        skill = get_builtin_python_test_runner()
        # available_capabilities is empty
        applicability = self.evaluator.evaluate(
            skill,
            available_capabilities=frozenset(),
            available_tool_ids=frozenset({"python_test_runner"}),
        )
        self.assertFalse(applicability.is_applicable)
        self.assertIn("missing_required_capabilities", applicability.reason)
        self.assertEqual(applicability.missing_capabilities, ("process:execute",))

    # =========================================================================
    # Test D: No Execution Authority in Skill
    # =========================================================================
    def test_scenario_d_no_execution_authority(self) -> None:
        """Prove via reflection that Skill classes expose zero execution methods or hooks."""
        skill = get_builtin_python_test_runner()
        forbidden_method_prefixes = ("run", "exec", "call", "invoke", "subprocess", "spawn", "mutate")
        for attr_name in dir(skill):
            if any(attr_name.lower().startswith(p) for p in forbidden_method_prefixes):
                self.fail(f"SkillDefinition contains execution-like method: '{attr_name}'")

        for attr_name in dir(skill.manifest):
            if any(attr_name.lower().startswith(p) for p in forbidden_method_prefixes):
                self.fail(f"SkillManifest contains execution-like method: '{attr_name}'")

        for attr_name in dir(self.evaluator):
            if attr_name in ("execute", "run", "mutate", "grant"):
                self.fail(f"SkillEvaluator contains execution-like method: '{attr_name}'")

    # =========================================================================
    # Test E: Permission Cannot Be Bypassed by Skill
    # =========================================================================
    def test_scenario_e_permission_cannot_be_bypassed_by_skill(self) -> None:
        """Prove that a skill requesting an unallowlisted tool is denied by PermissionPolicy."""
        # Skill advises EXECUTE
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "run_test",
                "rationale": "Following python test runner skill",
                "confidence": 1.0,
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        skill = get_builtin_python_test_runner()
        # execution request targets an unauthorized binary
        forbidden_cmd = ("unauthorized_tool", "--test")
        exec_req = ExecutionRequest(
            command=forbidden_cmd,
            profile=ProjectExecutionProfile("p", allowed_commands=()),
        )
        h_req = HarnessRequest(
            run_id="run-15-e",
            workspace=self.workspace,
            available_skills=(skill,),
            available_capabilities=("process:execute",),
            execution_requests=(exec_req,),
            allowed_execution_commands=(),  # None allowed!
            initial_project_state=ProjectState(
                run_id="run-15-e", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        # Authoritative permission denial occurs without invoking execution coordinator
        self.assertEqual(len(result.execution_results), 0)
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertEqual(result.observations[0].result_status, "permission_denied")

    # =========================================================================
    # Test F: Approval Cannot Be Bypassed by Skill
    # =========================================================================
    def test_scenario_f_approval_cannot_be_bypassed_by_skill(self) -> None:
        """Prove that a skill cannot bypass human approval requirements."""
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "run_test",
                "rationale": "Skill suggests running tests",
                "confidence": 1.0,
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        skill = get_builtin_python_test_runner()
        exec_req = ExecutionRequest(
            command=(sys.executable, "-c", "print('ok')"),
            profile=self.profile,
        )
        # Approval policy requiring approval for sys.executable
        approval_policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        approval_resolver = InMemoryApprovalResolver()  # Empty: unapproved

        h_req = HarnessRequest(
            run_id="run-15-f",
            workspace=self.workspace,
            available_skills=(skill,),
            available_capabilities=("process:execute",),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=approval_policy,
            approval_resolver=approval_resolver,
            initial_project_state=ProjectState(
                run_id="run-15-f", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        self.assertEqual(result.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(result.final_state.phase, HarnessPhase.WAITING)
        self.assertTrue(result.final_state.terminal)

    # =========================================================================
    # Test G: Context Assembly Includes Skill
    # =========================================================================
    def test_scenario_g_context_assembly_includes_skill(self) -> None:
        """Prove that DecisionContextAssembler incorporates skills with source_type=SKILL."""
        assembler = DecisionContextAssembler()
        skill = get_builtin_python_test_runner()

        envelope = assembler.assemble(
            run_id="run-15-g",
            attempt_number=0,
            skills=(skill,),
        )

        skill_items = [it for it in envelope.context_items if it.source_type == ContextSourceType.SKILL]
        self.assertEqual(len(skill_items), 1)
        item = skill_items[0]
        self.assertEqual(item.item_id, "skill:forge.builtin.python_test_runner")
        self.assertEqual(item.item_type, "skill")
        self.assertEqual(item.trust_level, ContextTrustLevel.VERIFIED)

        payload = json.loads(item.value)
        self.assertEqual(payload["skill_id"], "forge.builtin.python_test_runner")
        self.assertIn("python_test_runner", payload["guidance"])

    # =========================================================================
    # Test H: Secret-Safe Skill Context
    # =========================================================================
    def test_scenario_h_secret_safe_skill_context(self) -> None:
        """Prove that skills do not leak secret keys and are validated safely."""
        skill = get_builtin_python_test_runner()
        # Verify instructions do not contain forbidden secrets
        for bad in ("secret", "token", "password", "api_key", "credential", "stdout", "stderr"):
            self.assertNotIn(bad, skill.instructions.lower())

        assembler = DecisionContextAssembler()
        envelope = assembler.assemble(
            run_id="run-15-h",
            attempt_number=0,
            skills=(skill,),
        )
        for item in envelope.context_items:
            for bad in ("secret", "password", "api_key", "credential"):
                self.assertNotIn(bad, item.value.lower())

    # =========================================================================
    # Test I: UNTRUSTED Skill Handling & Permission Separation
    # =========================================================================
    def test_scenario_i_untrusted_skill_handling(self) -> None:
        """Prove that SkillEvaluator checks trust constraints without becoming a PermissionPolicy.

        Invariant:
        - SkillEvaluator decides whether a Skill is applicable.
        - PermissionPolicy decides whether an action/tool is permitted.
        - A Skill being applicable MUST NOT imply permission.
        """
        untrusted_skill = SkillDefinition(
            manifest=SkillManifest(
                skill_id="external.untrusted.script_runner",
                name="Untrusted Script Runner",
                version="0.0.1",
                description="External skill providing execution advice",
                required_capabilities=("process:execute",),
                requested_tools=("custom_tool",),
                trust_level=SkillTrustLevel.UNTRUSTED,
            ),
            instructions="Execute custom tool commands",
        )

        # 1. Trust constraint rejection when evaluator restricts trust levels
        strict_evaluator = SkillEvaluator(
            allowed_trust_levels=frozenset({SkillTrustLevel.BUILTIN, SkillTrustLevel.LOCAL})
        )
        strict_app = strict_evaluator.evaluate(
            untrusted_skill,
            available_capabilities=frozenset({"process:execute"}),
            available_tool_ids=frozenset({"custom_tool"}),
        )
        self.assertFalse(strict_app.is_applicable)
        self.assertIn("trust_level_not_allowed", strict_app.reason)

        # 2. When trust constraints allow UNTRUSTED, SkillEvaluator marks it applicable
        # based purely on capability and tool availability
        open_evaluator = SkillEvaluator()
        applicable_result = open_evaluator.evaluate(
            untrusted_skill,
            available_capabilities=frozenset({"process:execute"}),
            available_tool_ids=frozenset({"custom_tool"}),
        )
        self.assertTrue(applicable_result.is_applicable)
        self.assertEqual(applicable_result.reason, "all_requirements_satisfied")

        # 3. CRITICAL REGRESSION: Skill applicability DOES NOT imply permission.
        # When an applicable untrusted skill is presented, PermissionPolicy still
        # authoritatively blocks unallowlisted execution commands.
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "run_custom_tool",
                "rationale": "Advised by untrusted skill",
                "confidence": 1.0,
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider, skill_evaluator=open_evaluator)

        exec_req = ExecutionRequest(
            command=("custom_tool", "--run"),
            profile=ProjectExecutionProfile("p", allowed_commands=()),
        )
        h_req = HarnessRequest(
            run_id="run-15-untrusted-perm",
            workspace=self.workspace,
            available_skills=(untrusted_skill,),
            available_capabilities=("process:execute",),
            execution_requests=(exec_req,),
            allowed_execution_commands=(),  # custom_tool is NOT permitted!
            initial_project_state=ProjectState(
                run_id="run-15-untrusted-perm", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        # PermissionPolicy authoritatively denies execution despite skill being applicable
        self.assertEqual(len(result.execution_results), 0)
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertEqual(result.observations[0].result_status, "permission_denied")

    # =========================================================================
    # Test J: Multiple Applicable Skills Structurally Supported
    # =========================================================================
    def test_scenario_j_multiple_applicable_skills_structurally_supported(self) -> None:
        """Prove that SkillEvaluator and Assembler accept and order multiple skills."""
        skill_1 = get_builtin_python_test_runner()
        skill_2 = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.builtin.code_review",
                name="Code Reviewer",
                version="0.1.0",
                description="Code quality procedure",
                required_capabilities=("workspace:read",),
                trust_level=SkillTrustLevel.BUILTIN,
            ),
            instructions="Inspect diffs and verify invariants",
        )

        applicable = self.evaluator.find_applicable_skills(
            (skill_1, skill_2),
            available_capabilities=frozenset({"process:execute", "workspace:read"}),
            available_tool_ids=frozenset({"python_test_runner"}),
        )
        self.assertEqual(len(applicable), 2)
        # Deterministically sorted by skill_id
        self.assertEqual(applicable[0].manifest.skill_id, "forge.builtin.code_review")
        self.assertEqual(applicable[1].manifest.skill_id, "forge.builtin.python_test_runner")

        # Assemble into envelope with multiple skills
        assembler = DecisionContextAssembler()
        envelope = assembler.assemble(
            run_id="run-15-j",
            attempt_number=0,
            skills=applicable,
        )
        skill_items = [it for it in envelope.context_items if it.source_type == ContextSourceType.SKILL]
        self.assertEqual(len(skill_items), 2)

    # =========================================================================
    # Test K: Real Bounded End-to-End Run Using python_test_runner
    # =========================================================================
    def test_scenario_k_real_bounded_run_using_python_test_runner(self) -> None:
        """Prove a multi-turn controlled run guided by python_test_runner skill."""
        target_file = self.workspace_path / "test_success.txt"

        # Model follows the python_test_runner procedural steps:
        # Turn 1: EXECUTE
        # Turn 2: RUN_VERIFICATION
        # Turn 3: COMPLETE_RUN
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "run_python_test",
                "rationale": "Follow skill step 2: recommend EXECUTE for test runner",
                "confidence": 1.0,
            },
            {
                "decision_type": "VERIFY",
                "action": "RUN_VERIFICATION",
                "reason_code": "tests_passed",
                "rationale": "Follow skill step 3: tests passed, recommend RUN_VERIFICATION",
                "confidence": 1.0,
            },
            {
                "decision_type": "COMPLETE",
                "action": "COMPLETE_RUN",
                "reason_code": "acceptance_passed",
                "rationale": "Follow skill step 4: acceptance passed, complete run",
                "confidence": 1.0,
            },
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        skill = get_builtin_python_test_runner()

        # Command writes test_success.txt to simulate successful test run
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('tests_passed')",
            ),
            profile=self.profile,
            metadata={"tool_id": "python_test_runner"},
        )
        crit = AcceptanceCriterion(
            criterion_id="crit-15-k",
            requirement_id="req-15-k",
            description="test_success.txt must exist",
        )
        req = Requirement(
            requirement_id="req-15-k",
            description="test suite executed successfully",
        )

        h_req = HarnessRequest(
            run_id="run-15-k",
            workspace=self.workspace,
            available_skills=(skill,),
            available_capabilities=("process:execute",),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            verification_expectations={
                "crit-15-k": VerificationExpectation(relative_path="test_success.txt", exists=True)
            },
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-15-k", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        # 1. Run completed successfully
        self.assertEqual(result.final_state.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.final_state.phase, HarnessPhase.COMPLETE)
        self.assertEqual(result.final_project_state.status, ProjectStateStatus.ACCEPTED)

        # 2. AI model prompt included skill guidance
        self.assertEqual(len(scripted_model.received_requests), 3)
        turn_1_prompt = scripted_model.received_requests[0].prompt
        self.assertIn("forge.builtin.python_test_runner", turn_1_prompt)
        self.assertIn("Python Test Runner", turn_1_prompt)

    # =========================================================================
    # Test L: Deterministic Provider Regression
    # =========================================================================
    def test_scenario_l_deterministic_provider_regression(self) -> None:
        """Prove that DeterministicDecisionProvider works without regression with skills present."""
        deterministic_provider = DeterministicDecisionProvider()
        harness = AgentHarness(decision_provider=deterministic_provider)
        skill = get_builtin_python_test_runner()

        target_file = self.workspace_path / "det_test.txt"
        exec_req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path(r'{target_file}').write_text('det_done')",
            ),
            profile=self.profile,
        )
        crit = AcceptanceCriterion(
            criterion_id="crit-15-l",
            requirement_id="req-15-l",
            description="det_test.txt must exist",
        )
        req = Requirement(
            requirement_id="req-15-l",
            description="det test executed successfully",
        )
        h_req = HarnessRequest(
            run_id="run-15-l",
            workspace=self.workspace,
            available_skills=(skill,),
            available_capabilities=("process:execute",),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            verification_expectations={
                "crit-15-l": VerificationExpectation(relative_path="det_test.txt", exists=True)
            },
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-15-l", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)
        # Deterministic provider executes cleanly and completes through verification and acceptance
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_text(), "det_done")
        self.assertEqual(result.final_state.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.final_project_state.status, ProjectStateStatus.ACCEPTED)

    # =========================================================================
    # Cross-run skill and context isolation
    # =========================================================================
    def test_cross_run_skill_and_context_isolation(self) -> None:
        """Prove that skill context items do not introduce cross-run pollution."""
        assembler = DecisionContextAssembler()
        skill = get_builtin_python_test_runner()

        env_1 = assembler.assemble(run_id="run-1", attempt_number=0, skills=(skill,))
        env_2 = assembler.assemble(run_id="run-2", attempt_number=0, skills=(skill,))

        # Fingerprints are unique per run_id
        self.assertNotEqual(env_1.context_fingerprint, env_2.context_fingerprint)
        self.assertEqual(env_1.run_id, "run-1")
        self.assertEqual(env_2.run_id, "run-2")


if __name__ == "__main__":
    unittest.main()
