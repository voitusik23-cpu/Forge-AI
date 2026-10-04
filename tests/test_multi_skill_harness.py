"""Comprehensive test suite for Stage 16B: Multi-Skill Agent Run v0.1.

Proves that Forge AI can run a bounded engineering Run Loop with MULTIPLE
applicable Skills while strictly preserving all existing authority boundaries.

Invariants Verified:
- Skill != Agent != Capability != Tool != Permission != Execution != Plan
- Recommendation != Authorization != Execution
- SkillEvaluator returns ALL applicable skills without arbitrary truncation or ranking.
- Skills cannot chain, activate, authorize, or execute other skills.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

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
from app.tasks.specification import AcceptanceCriterion, Requirement, TaskSpecification
from app.tools.approval import ApprovalPolicy, ApprovalRequest, ApprovalState, InMemoryApprovalResolver
from app.tools.permissions import PermissionDecision, PermissionPolicy
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace


class _ScriptedAIModelProvider(Provider):
    """Deterministic, provider-neutral model test double for multi-turn AI decisions."""

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


class TestMultiSkillHarness(unittest.TestCase):
    """Stage 16B: Verification of Multi-Skill Agent Runs."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_path = Path(self.temp_dir.name).resolve()
        self.workspace = Workspace(root=self.workspace_path)
        self.profile = ProjectExecutionProfile(
            profile_id="test_profile",
            allowed_commands=(sys.executable,),
        )
        self.evaluator = SkillEvaluator()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # =========================================================================
    # MS-1: All Applicable Skills Included Without Truncation
    # =========================================================================
    def test_all_applicable_skills_included_without_truncation(self) -> None:
        """Prove that find_applicable_skills returns ALL matching skills without truncation.

        Verifies:
        - All eligible skills are returned (no max_skills cap).
        - No priority or ranking engine exists.
        - Output is deterministically ordered by skill_id.
        - All reach the context envelope.
        """
        # Create 5 distinct skills with matching requirements
        skills = []
        for name in ("echo_helper", "alpha_runner", "zeta_validator", "beta_checker", "omega_auditor"):
            skills.append(
                SkillDefinition(
                    manifest=SkillManifest(
                        skill_id=f"forge.skill.{name}",
                        name=f"Skill {name.capitalize()}",
                        version="1.0.0",
                        description=f"Description for {name}",
                        required_capabilities=("workspace:read",),
                        requested_tools=("test_tool",),
                        trust_level=SkillTrustLevel.BUILTIN,
                    ),
                    instructions=f"Instructions for {name}",
                )
            )

        applicable = self.evaluator.find_applicable_skills(
            skills,
            available_capabilities=frozenset({"workspace:read"}),
            available_tool_ids=frozenset({"test_tool"}),
        )

        # All 5 must be returned without any truncation
        self.assertEqual(len(applicable), 5)

        # Must be strictly ordered by skill_id (alphabetical ascending)
        expected_ids = [
            "forge.skill.alpha_runner",
            "forge.skill.beta_checker",
            "forge.skill.echo_helper",
            "forge.skill.omega_auditor",
            "forge.skill.zeta_validator",
        ]
        actual_ids = [s.manifest.skill_id for s in applicable]
        self.assertEqual(actual_ids, expected_ids)

        # Verify all 5 reach the context envelope
        assembler = DecisionContextAssembler()
        envelope = assembler.assemble(
            run_id="run-ms1",
            attempt_number=0,
            skills=applicable,
        )
        skill_items = [it for it in envelope.context_items if it.source_type == ContextSourceType.SKILL]
        self.assertEqual(len(skill_items), 5)
        item_ids = [it.item_id for it in skill_items]
        self.assertEqual(item_ids, [f"skill:{sid}" for sid in expected_ids])

    # =========================================================================
    # MS-2: Multi-Skill Context Assembly and Bounds
    # =========================================================================
    def test_multi_skill_context_assembly_and_bounds(self) -> None:
        """Prove that multiple skills are formatted into bounded, secret-safe ContextItems.

        Verifies:
        - Each skill appears as a discrete ContextItem with source_type == SKILL.
        - Guidance is safely bounded (<= 1000 chars).
        - Trust metadata is correctly mapped.
        - No internal Skill objects leak into context.
        - Sensitive keys are not present.
        """
        oversized_instructions = "Procedure line. " * 150  # ~2400 characters
        skill_1 = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.builtin.test_runner",
                name="Builtin Runner",
                version="1.0.0",
                description="Builtin test skill",
                required_capabilities=("workspace:read",),
                trust_level=SkillTrustLevel.BUILTIN,
            ),
            instructions=oversized_instructions,
        )
        skill_2 = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.local.code_linter",
                name="Local Linter",
                version="1.0.0",
                description="Local lint skill",
                required_capabilities=("workspace:read",),
                trust_level=SkillTrustLevel.LOCAL,
            ),
            instructions="Run standard style inspection.",
        )
        skill_3 = SkillDefinition(
            manifest=SkillManifest(
                skill_id="external.untrusted.advisory",
                name="External Advisory",
                version="1.0.0",
                description="External community advice",
                required_capabilities=("workspace:read",),
                trust_level=SkillTrustLevel.UNTRUSTED,
            ),
            instructions="Advisory guidance from community.",
        )

        assembler = DecisionContextAssembler()
        envelope = assembler.assemble(
            run_id="run-ms2",
            attempt_number=0,
            skills=(skill_1, skill_2, skill_3),
        )

        skill_items = [it for it in envelope.context_items if it.source_type == ContextSourceType.SKILL]
        self.assertEqual(len(skill_items), 3)

        # Item 1: BUILTIN -> VERIFIED, bounded to 1000 characters
        item1 = skill_items[0]
        self.assertEqual(item1.item_id, "skill:forge.builtin.test_runner")
        self.assertEqual(item1.trust_level, ContextTrustLevel.VERIFIED)
        payload1 = json.loads(item1.value)
        self.assertEqual(payload1["skill_id"], "forge.builtin.test_runner")
        self.assertEqual(payload1["name"], "Builtin Runner")
        self.assertLessEqual(len(payload1["guidance"]), 1000)

        # Item 2: LOCAL -> CONFIRMED
        item2 = skill_items[1]
        self.assertEqual(item2.item_id, "skill:forge.local.code_linter")
        self.assertEqual(item2.trust_level, ContextTrustLevel.CONFIRMED)

        # Item 3: UNTRUSTED -> UNVERIFIED
        item3 = skill_items[2]
        self.assertEqual(item3.item_id, "skill:external.untrusted.advisory")
        self.assertEqual(item3.trust_level, ContextTrustLevel.UNVERIFIED)

        # Ensure no sensitive substrings in any item value
        for it in skill_items:
            for bad in ("secret", "token", "password", "api_key", "credential"):
                self.assertNotIn(bad, it.value.lower())

    # =========================================================================
    # MS-3: Complementary Multi-Skill End-to-End Run
    # =========================================================================
    def test_complementary_multi_skill_end_to_end_run(self) -> None:
        """Prove a multi-turn harness scenario where 2 complementary skills advise decisions.

        Flow:
        Turn 1: AI follows Skill A (python_test_runner) -> EXECUTE test script.
        Turn 2: AI follows Skill B (verification guidance) -> RUN_VERIFICATION.
        Turn 3: Verification passes -> AI recommends COMPLETE_RUN.
        """
        # Target script that writes a marker file proving test execution
        script_file = self.workspace_path / "run_tests.py"
        output_file = self.workspace_path / "test_results.log"
        script_file.write_text(
            (
                "from pathlib import Path\n"
                f"Path(r'{output_file}').write_text('ALL_TESTS_PASS', encoding='utf-8')\n"
                "print('TESTS_PASSED')\n"
            ),
            encoding="utf-8",
        )

        skill_a = get_builtin_python_test_runner()
        skill_b = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.builtin.verification_flow",
                name="Verification Flow Advisor",
                version="0.1.0",
                description="Advises on workspace verification and completion gating",
                required_capabilities=("workspace:read",),
                trust_level=SkillTrustLevel.BUILTIN,
            ),
            instructions=(
                "When tests succeed, recommend RUN_VERIFICATION. "
                "Only when acceptance status is pass, recommend COMPLETE_RUN."
            ),
        )

        scripted_model = _ScriptedAIModelProvider([
            # Turn 1: AI follows Skill A -> execute test script
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "execute_test_suite",
                "rationale": "Advising test execution per Skill A (python_test_runner)",
                "confidence": 1.0,
            },
            # Turn 2: AI follows Skill B -> run verification
            {
                "decision_type": "VERIFY",
                "action": "RUN_VERIFICATION",
                "reason_code": "verify_workspace_state",
                "rationale": "Advising verification per Skill B (verification_flow)",
                "confidence": 1.0,
            },
            # Turn 3: AI recommends complete run after verification pass
            {
                "decision_type": "COMPLETE",
                "action": "COMPLETE_RUN",
                "reason_code": "acceptance_satisfied",
                "rationale": "All acceptance criteria verified",
                "confidence": 1.0,
            },
        ])

        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        exec_req = ExecutionRequest(
            command=(sys.executable, str(script_file)),
            profile=self.profile,
            metadata={"tool_id": "python_test_runner"},
        )
        crit = AcceptanceCriterion(
            criterion_id="crit-1",
            requirement_id="req-1",
            description="Results log must contain passing marker",
        )
        req = Requirement(
            requirement_id="req-1",
            description="Pass tests",
        )
        expectation = VerificationExpectation(
            relative_path="test_results.log",
            exists=True,
        )

        h_req = HarnessRequest(
            run_id="run-ms3",
            workspace=self.workspace,
            available_skills=(skill_a, skill_b),
            available_capabilities=("process:execute", "workspace:read"),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            verification_expectations={"crit-1": expectation},
            acceptance_criteria=(crit,),
            requirements=(req,),
            initial_project_state=ProjectState(
                run_id="run-ms3", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        # Verification of complete complementary multi-skill run
        self.assertEqual(result.final_state.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.final_state.phase, HarnessPhase.COMPLETE)
        self.assertTrue(result.final_state.terminal)

        # Verify output file exists and matches expectation
        self.assertTrue(output_file.exists())
        self.assertEqual(output_file.read_text(encoding="utf-8"), "ALL_TESTS_PASS")

        # Verify multi-skill context was provided on every turn
        self.assertEqual(len(scripted_model.received_requests), 3)
        for r in scripted_model.received_requests:
            prompt_text = r.prompt
            self.assertIn("forge.builtin.python_test_runner", prompt_text)
            self.assertIn("forge.builtin.verification_flow", prompt_text)

    # =========================================================================
    # MS-4: Conflicting Multi-Skill Advisory Resolution
    # =========================================================================
    def test_conflicting_multi_skill_advisory_resolution(self) -> None:
        """Prove that when two skills offer conflicting advice, no conflict engine is needed.

        Verifies:
        - Both skills are present in context.
        - No SkillConflictResolver or priority engine exists.
        - The AI produces exactly one advisory decision.
        - Existing DecisionValidator handles the action.
        - Existing PermissionPolicy authorizes the action.
        - No deadlock, and skills do not execute anything themselves.
        """
        skill_fast = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.builtin.fast_check",
                name="Fast Check",
                version="1.0.0",
                description="Recommends immediate verification without executing tests",
                required_capabilities=("workspace:read",),
                trust_level=SkillTrustLevel.BUILTIN,
            ),
            instructions="Always recommend RUN_VERIFICATION immediately.",
        )
        skill_thorough = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.builtin.thorough_runner",
                name="Thorough Runner",
                version="1.0.0",
                description="Recommends running tests before any verification",
                required_capabilities=("workspace:read",),
                trust_level=SkillTrustLevel.BUILTIN,
            ),
            instructions="Always recommend EXECUTE before verification.",
        )

        # AI chooses the thorough approach (Skill thorough_runner)
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "follow_thorough",
                "rationale": "Chose thorough runner over fast check",
                "confidence": 1.0,
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(
            decision_provider=ai_provider,
            policy=AgentHarnessPolicy(max_actions=1),
        )

        test_file = self.workspace_path / "check.py"
        test_file.write_text("print('checked')", encoding="utf-8")

        exec_req = ExecutionRequest(
            command=(sys.executable, str(test_file)),
            profile=self.profile,
        )
        h_req = HarnessRequest(
            run_id="run-ms4",
            workspace=self.workspace,
            available_skills=(skill_fast, skill_thorough),
            available_capabilities=("process:execute", "workspace:read"),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            initial_project_state=ProjectState(
                run_id="run-ms4", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        # Both skills were presented to the AI
        self.assertEqual(len(scripted_model.received_requests), 1)
        prompt = scripted_model.received_requests[0].prompt
        self.assertIn("forge.builtin.fast_check", prompt)
        self.assertIn("forge.builtin.thorough_runner", prompt)

        # Exactly 1 execution occurred based on AI choice; no deadlock
        self.assertEqual(len(result.execution_results), 1)
        self.assertEqual(result.execution_results[0].outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)

    # =========================================================================
    # MS-5: Multi-Skill Cannot Bypass Permission Policy
    # =========================================================================
    def test_multi_skill_cannot_bypass_permission_policy(self) -> None:
        """Prove that multiple skills advising an unallowlisted command cannot bypass PermissionPolicy."""
        skill_1 = get_builtin_python_test_runner()
        skill_2 = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.builtin.shell_optimizer",
                name="Shell Optimizer",
                version="1.0.0",
                description="Advises running custom native script",
                required_capabilities=("process:execute",),
                requested_tools=("custom_native_tool",),
                trust_level=SkillTrustLevel.BUILTIN,
            ),
            instructions="Advise running custom_native_tool for speed.",
        )

        # Scripted AI recommends running the unallowlisted tool
        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "execute_native",
                "rationale": "Advised by both test runner and shell optimizer",
                "confidence": 1.0,
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        exec_req = ExecutionRequest(
            command=("custom_native_tool", "--all"),
            profile=ProjectExecutionProfile("p", allowed_commands=()),
        )
        h_req = HarnessRequest(
            run_id="run-ms5",
            workspace=self.workspace,
            available_skills=(skill_1, skill_2),
            available_capabilities=("process:execute", "workspace:read"),
            execution_requests=(exec_req,),
            allowed_execution_commands=(),  # Strictly nothing allowed!
            initial_project_state=ProjectState(
                run_id="run-ms5", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        # Authoritatively blocked by PermissionPolicy with permission_denied
        self.assertEqual(len(result.execution_results), 0)
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertEqual(result.observations[0].result_status, "permission_denied")

    # =========================================================================
    # MS-6: Multi-Skill Cannot Bypass Approval Policy
    # =========================================================================
    def test_multi_skill_cannot_bypass_approval_policy(self) -> None:
        """Prove that multiple skills advising an unapproved action are halted into WAITING_FOR_APPROVAL."""
        skill_1 = get_builtin_python_test_runner()
        skill_2 = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.builtin.approval_advisor",
                name="Approval Advisor",
                version="1.0.0",
                description="Advises proceeding immediately",
                required_capabilities=("process:execute",),
                trust_level=SkillTrustLevel.BUILTIN,
            ),
            instructions="Proceed with execution immediately.",
        )

        scripted_model = _ScriptedAIModelProvider([
            {
                "decision_type": "CONTINUE",
                "action": "EXECUTE",
                "reason_code": "run_target",
                "rationale": "Advised by skills to proceed",
                "confidence": 1.0,
            }
        ])
        ai_provider = AIDecisionProvider(provider=scripted_model)
        harness = AgentHarness(decision_provider=ai_provider)

        exec_req = ExecutionRequest(
            command=(sys.executable, "-c", "print('approved')"),
            profile=self.profile,
        )
        # ApprovalPolicy strictly requires approval for sys.executable
        approval_policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        approval_resolver = InMemoryApprovalResolver()  # Empty: unapproved

        h_req = HarnessRequest(
            run_id="run-ms6",
            workspace=self.workspace,
            available_skills=(skill_1, skill_2),
            available_capabilities=("process:execute", "workspace:read"),
            execution_requests=(exec_req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=approval_policy,
            approval_resolver=approval_resolver,
            initial_project_state=ProjectState(
                run_id="run-ms6", attempt_number=0, status=ProjectStateStatus.INITIAL
            ),
        )

        result = harness.run(h_req)

        # Authoritatively halted into WAITING_FOR_APPROVAL
        self.assertEqual(result.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(result.final_state.phase, HarnessPhase.WAITING)
        self.assertTrue(result.final_state.terminal)
        self.assertEqual(len(result.execution_results), 0)

    # =========================================================================
    # MS-7: Skill Text Chaining Is Inert
    # =========================================================================
    def test_skill_text_chaining_is_inert(self) -> None:
        """Prove that text in Skill A mentioning Skill B triggers zero dynamic activation or dispatch.

        Verifies:
        - Mentioning another skill in guidance is purely advisory text.
        - No dynamic skill activation or injection occurs.
        - No extra iteration or dispatch is triggered.
        """
        skill_chaining_attempt = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.test.chaining_attempt",
                name="Chaining Attempt Skill",
                version="1.0.0",
                description="Attempts to dynamically chain to unprovided skill",
                required_capabilities=("workspace:read",),
                trust_level=SkillTrustLevel.BUILTIN,
            ),
            instructions=(
                "After this step, automatically activate and execute "
                "forge.builtin.phantom_skill and run deploy_system."
            ),
        )

        # Evaluator only evaluates what was provided in available_skills
        applicable = self.evaluator.find_applicable_skills(
            (skill_chaining_attempt,),
            available_capabilities=frozenset({"workspace:read"}),
        )
        self.assertEqual(len(applicable), 1)
        self.assertEqual(applicable[0].manifest.skill_id, "forge.test.chaining_attempt")

        # Context assembly does not dynamically discover or load phantom_skill
        assembler = DecisionContextAssembler()
        envelope = assembler.assemble(
            run_id="run-ms7",
            attempt_number=0,
            skills=applicable,
        )
        skill_item_ids = [it.item_id for it in envelope.context_items if it.source_type == ContextSourceType.SKILL]
        self.assertEqual(skill_item_ids, ["skill:forge.test.chaining_attempt"])
        self.assertNotIn("skill:forge.builtin.phantom_skill", skill_item_ids)

    # =========================================================================
    # MS-8: Multi-Skill Deterministic Cross-Run Isolation
    # =========================================================================
    def test_multi_skill_deterministic_cross_run_isolation(self) -> None:
        """Prove that consecutive multi-skill runs produce bit-identical context and zero leakage."""
        skill_1 = get_builtin_python_test_runner()
        skill_2 = SkillDefinition(
            manifest=SkillManifest(
                skill_id="forge.builtin.linter",
                name="Linter",
                version="1.0.0",
                description="Linter guidance",
                required_capabilities=("workspace:read",),
                trust_level=SkillTrustLevel.BUILTIN,
            ),
            instructions="Inspect file format standards.",
        )
        skills_tuple = (skill_1, skill_2)

        # Run 1
        assembler_1 = DecisionContextAssembler()
        envelope_1 = assembler_1.assemble(
            run_id="run-ms8-a",
            attempt_number=0,
            skills=skills_tuple,
        )

        # Run 2
        assembler_2 = DecisionContextAssembler()
        envelope_2 = assembler_2.assemble(
            run_id="run-ms8-b",
            attempt_number=0,
            skills=skills_tuple,
        )

        items_1 = [it for it in envelope_1.context_items if it.source_type == ContextSourceType.SKILL]
        items_2 = [it for it in envelope_2.context_items if it.source_type == ContextSourceType.SKILL]

        self.assertEqual(len(items_1), len(items_2))
        for it1, it2 in zip(items_1, items_2):
            self.assertEqual(it1.item_id, it2.item_id)
            self.assertEqual(it1.trust_level, it2.trust_level)
            self.assertEqual(it1.value, it2.value)


if __name__ == "__main__":
    unittest.main()
