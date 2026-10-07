"""Comprehensive architectural tests for Capability & Resource Fabric v0.1."""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from app.agents.providers.model_registry import (
    ModelRegistry,
    create_default_registry,
)
from app.agents.providers.models import (
    CostTier,
    ModelCapability,
    ProviderInfo,
    ProviderModelInfo,
    ProviderProtocol,
)
from app.fabric import (
    AgentUsageRecord,
    AttemptUsageRecord,
    CapabilityDescriptor,
    CapabilityDomain,
    CapabilityFabric,
    CapabilityRequirement,
    CapabilityType,
    ExecutionBoundary,
    FabricRequest,
    FabricResolution,
    ModelRegistryAdapter,
    ResourceAccessMode,
    ResourceDescriptor,
    ResourceType,
    RunAccountingRecord,
    SkillRegistryAdapter,
    ToolRegistryAdapter,
    UsageAccountingAdapter,
    WorkspaceResourceAdapter,
)
from app.skills.models import SkillDefinition, SkillManifest, SkillTrustLevel
from app.skills.registry import SkillRegistry
from app.tools.base import Tool
from app.tools.contracts import ToolDefinition, ToolInvocation, ToolResult, ToolStatus
from app.tools.registry import ToolRegistry
from app.tools.workspace import Workspace
from app.usage import Usage


class DummyTool(Tool):
    def __init__(self, tool_id: str, name: str) -> None:
        self._definition = ToolDefinition(id=tool_id, name=name, description=f"Test tool {name}")

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def execute(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.COMPLETED, output="ok")


class CapabilityFabricTests(unittest.TestCase):
    """Test resolution, authorization, resource checking, and telemetry accounting in Fabric."""

    def setUp(self) -> None:
        self.model_registry = create_default_registry()
        self.tool_registry = ToolRegistry()
        self.tool_registry.register(DummyTool("read_project_file", "Read Project File"))
        self.tool_registry.register(DummyTool("write_project_file", "Write Project File"))

        self.skill_registry = SkillRegistry()
        manifest = SkillManifest(
            skill_id="skill.python_refactor",
            name="Python Refactoring",
            version="1.0.0",
            description="Refactor python code",
            required_capabilities=("code", "review"),
            requested_tools=("read_project_file", "write_project_file"),
        )
        self.skill_registry.register(SkillDefinition(manifest=manifest, instructions="Refactor step-by-step"))

        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp_dir.name))

        self.fabric = CapabilityFabric(
            model_registry=self.model_registry,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            workspace=self.workspace,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_01_successful_resolution_with_capabilities_and_tools(self) -> None:
        request = FabricRequest(
            subject_agent="agent_code",
            intent_description="Write a python function",
            required_capabilities=("code", "read_project_file"),
            allow_paid_providers=False,
            run_id="run-101",
        )
        resolution = self.fabric.resolve(request)

        self.assertTrue(resolution.is_capable)
        self.assertTrue(resolution.is_authorized)
        self.assertTrue(resolution.can_proceed)
        self.assertIn("read_project_file", resolution.selected_tools)
        self.assertIsNotNone(resolution.selected_model)
        self.assertTrue(len(resolution.allocated_resources) > 0)

    def test_02_unmet_capability_fails_closed(self) -> None:
        request = FabricRequest(
            subject_agent="agent_quantum",
            intent_description="Quantum computing simulation",
            required_capabilities=("quantum_simulation_non_existent",),
        )
        resolution = self.fabric.resolve(request)

        self.assertFalse(resolution.is_capable)
        self.assertFalse(resolution.can_proceed)
        self.assertTrue(any("Unmet required capabilities" in r for r in resolution.rejection_reasons))

    def test_03_paid_model_unauthorized_when_paid_disabled(self) -> None:
        request = FabricRequest(
            subject_agent="agent_premium",
            intent_description="Deep analysis with GPT-4o",
            explicit_model="openai:gpt-4o",
            allow_paid_providers=False,
        )
        resolution = self.fabric.resolve(request)

        self.assertFalse(resolution.is_authorized)
        self.assertFalse(resolution.can_proceed)
        self.assertTrue(any("requires paid authorization" in r for r in resolution.rejection_reasons))

    def test_04_paid_model_authorized_when_paid_enabled(self) -> None:
        request = FabricRequest(
            subject_agent="agent_premium",
            intent_description="Deep analysis with GPT-4o",
            explicit_model="openai:gpt-4o",
            allow_paid_providers=True,
        )
        resolution = self.fabric.resolve(request)

        self.assertTrue(resolution.is_authorized)
        self.assertTrue(resolution.can_proceed)
        self.assertEqual(resolution.selected_provider, "openai")
        self.assertEqual(resolution.selected_model, "openai:gpt-4o")

    def test_05_unknown_explicit_model_fails_closed(self) -> None:
        request = FabricRequest(
            subject_agent="agent_test",
            intent_description="Test unknown model",
            explicit_model="unknown:fake-model-xyz",
        )
        resolution = self.fabric.resolve(request)

        self.assertFalse(resolution.is_capable)
        self.assertFalse(resolution.can_proceed)
        self.assertTrue(any("not registered" in r for r in resolution.rejection_reasons))

    def test_06_unavailable_resource_blocks_resolution(self) -> None:
        unavailable_db = ResourceDescriptor(
            resource_id="res:db:postgres_main",
            resource_type=ResourceType.DATABASE,
            uri_or_path="postgresql://localhost:5432/main",
            is_available=False,
        )
        request = FabricRequest(
            subject_agent="agent_db",
            intent_description="Query customer data",
            required_resources=(unavailable_db,),
        )
        resolution = self.fabric.resolve(request)

        self.assertFalse(resolution.can_proceed)
        self.assertTrue(any("is unavailable" in r for r in resolution.rejection_reasons))

    def test_07_workspace_resource_adaptation(self) -> None:
        adapter = WorkspaceResourceAdapter(self.workspace)
        res_desc = adapter.to_resource_descriptor(ResourceAccessMode.READ)

        self.assertEqual(res_desc.resource_type, ResourceType.WORKSPACE)
        self.assertEqual(res_desc.uri_or_path, str(self.workspace.root))
        self.assertTrue(res_desc.is_available)

    def test_08_skill_adapter_does_not_grant_execution_authority(self) -> None:
        adapter = SkillRegistryAdapter(self.skill_registry)
        req_caps = adapter.get_skill_required_capabilities("skill.python_refactor")
        req_tools = adapter.get_skill_requested_tools("skill.python_refactor")

        self.assertEqual(req_caps, ("code", "review"))
        self.assertEqual(req_tools, ("read_project_file", "write_project_file"))

        # Skill by itself is purely declarative; it requires a FabricRequest to resolve permissions
        skill_def = self.skill_registry.get("skill.python_refactor")
        self.assertIsNotNone(skill_def)
        self.assertEqual(skill_def.manifest.trust_level, SkillTrustLevel.BUILTIN)

    def test_09_run_accounting_and_telemetry_correlation(self) -> None:
        run_id = "run-sample-202"
        agent_record = UsageAccountingAdapter.create_agent_usage(
            agent_name="code_agent",
            provider_name="deepseek",
            model_name="deepseek-chat",
            usage=Usage(input_tokens=1000, output_tokens=250, estimated_cost=0.002),
            duration_seconds=1.45,
            provider_reported_cost=0.0018,
        )

        self.fabric.record_usage(run_id, agent_record)
        accounting = self.fabric.get_run_accounting(
            run_id=run_id,
            task_id="task-001",
            project_id="proj-alpha",
            total_duration_seconds=1.5,
        )

        self.assertEqual(accounting.run_id, run_id)
        self.assertEqual(accounting.total_requests, 1)
        self.assertEqual(accounting.total_input_tokens, 1000)
        self.assertEqual(accounting.total_output_tokens, 250)
        self.assertEqual(accounting.total_tokens, 1250)
        # Prefers provider_reported_cost
        self.assertEqual(accounting.total_cost, 0.0018)

    def test_10_multi_agent_cost_and_usage_aggregation(self) -> None:
        run_id = "run-multi-303"

        # Agent 1 (Planner / DeepSeek)
        self.fabric.record_usage(
            run_id,
            UsageAccountingAdapter.create_agent_usage(
                agent_name="planner_agent",
                provider_name="deepseek",
                model_name="deepseek-reasoner",
                usage=Usage(input_tokens=2000, output_tokens=500),
                provider_reported_cost=0.005,
            ),
        )

        # Agent 2 (Reviewer / Anthropic)
        self.fabric.record_usage(
            run_id,
            UsageAccountingAdapter.create_agent_usage(
                agent_name="reviewer_agent",
                provider_name="anthropic",
                model_name="claude-3-5-haiku-20241022",
                usage=Usage(input_tokens=3000, output_tokens=400),
                provider_reported_cost=0.008,
            ),
        )

        accounting = self.fabric.get_run_accounting(
            run_id=run_id,
            task_id="task-multi",
            retry_count=1,
            fallback_events=("deepseek -> mock",),
            associated_artifact_ids=("art-1", "art-2"),
        )

        self.assertEqual(accounting.total_requests, 2)
        self.assertEqual(accounting.total_input_tokens, 5000)
        self.assertEqual(accounting.total_output_tokens, 900)
        self.assertEqual(accounting.total_tokens, 5900)
        self.assertAlmostEqual(accounting.total_cost, 0.013, places=5)
        self.assertEqual(accounting.retry_count, 1)
        self.assertEqual(len(accounting.fallback_events), 1)
        self.assertEqual(len(accounting.associated_artifact_ids), 2)

    def test_11_cost_estimation_from_model_rates_when_cost_missing(self) -> None:
        model_info = ProviderModelInfo(
            provider_id="mock_paid",
            model_id="paid-v1",
            context_window=32768,
            capabilities=frozenset({ModelCapability.CODE}),
            cost_tier=CostTier.PAID,
            input_cost_per_1m=3.00,    # $3 per 1M input tokens
            output_cost_per_1m=15.00,  # $15 per 1M output tokens
        )

        # 100,000 input tokens = $0.30, 20,000 output tokens = $0.30 -> Total $0.60
        record = UsageAccountingAdapter.create_agent_usage(
            agent_name="agent_worker",
            provider_name="mock_paid",
            model_name="paid-v1",
            usage=Usage(input_tokens=100_000, output_tokens=20_000, estimated_cost=None),
            model_info=model_info,
        )

        self.assertIsNone(record.provider_reported_cost)
        self.assertIsNotNone(record.estimated_cost)
        self.assertAlmostEqual(record.effective_cost, 0.60, places=4)

    def test_12_serialization_contains_zero_secrets(self) -> None:
        run_id = "run-sec-404"
        self.fabric.record_usage(
            run_id,
            AgentUsageRecord(
                agent_name="test_agent",
                provider_name="openai",
                model_name="gpt-4o",
                input_tokens=500,
                output_tokens=100,
                estimated_cost=0.01,
            ),
        )
        accounting = self.fabric.get_run_accounting(run_id=run_id)
        as_dict = accounting.to_dict()

        self.assertEqual(as_dict["run_id"], run_id)
        self.assertNotIn("API_KEY", str(as_dict))
        self.assertNotIn("secret", str(as_dict))


    def test_13_typed_capability_domains_prevent_domain_confusion(self) -> None:
        # A requirement for model reasoning cannot accidentally match a tool named reasoning
        req_model = CapabilityRequirement.model("reasoning")
        req_tool = CapabilityRequirement.tool("read_project_file")
        req_exec = CapabilityRequirement.execution("NETWORK")

        self.assertEqual(req_model.domain, CapabilityDomain.MODEL)
        self.assertEqual(req_tool.domain, CapabilityDomain.TOOL)
        self.assertEqual(req_exec.domain, CapabilityDomain.EXECUTION)

        request = FabricRequest(
            subject_agent="agent_typed",
            intent_description="Test domain isolation",
            required_capabilities=(req_model, req_tool, req_exec),
        )
        resolution = self.fabric.resolve(request)
        self.assertTrue(resolution.is_capable)

        matched_domains = {c.domain for c in resolution.matched_capabilities}
        self.assertIn(CapabilityDomain.MODEL, matched_domains)
        self.assertIn(CapabilityDomain.TOOL, matched_domains)
        self.assertIn(CapabilityDomain.EXECUTION, matched_domains)

    def test_14_multi_attempt_fallback_run_accounting(self) -> None:
        run_id = "run-fallback-909"

        # Attempt 1: DeepSeek failed
        att1 = UsageAccountingAdapter.create_attempt_usage(
            attempt_index=1,
            agent_name="code_agent",
            provider_name="deepseek",
            model_name="deepseek-chat",
            usage=Usage(input_tokens=1000, output_tokens=0),
            duration_seconds=1.2,
            provider_reported_cost=0.001,
            success=False,
            error_message="Rate limit exceeded",
            is_fallback=False,
        )
        self.fabric.record_usage(run_id, att1)

        # Attempt 2: Anthropic failed
        att2 = UsageAccountingAdapter.create_attempt_usage(
            attempt_index=2,
            agent_name="code_agent",
            provider_name="anthropic",
            model_name="claude-3-5-sonnet",
            usage=Usage(input_tokens=1000, output_tokens=50),
            duration_seconds=2.0,
            provider_reported_cost=0.004,
            success=False,
            error_message="Context timeout",
            is_fallback=True,
        )
        self.fabric.record_usage(run_id, att2)

        # Attempt 3: OpenAI succeeded
        att3 = UsageAccountingAdapter.create_attempt_usage(
            attempt_index=3,
            agent_name="code_agent",
            provider_name="openai",
            model_name="gpt-4o-mini",
            usage=Usage(input_tokens=1000, output_tokens=300),
            duration_seconds=1.1,
            provider_reported_cost=0.0015,
            success=True,
            is_fallback=True,
        )
        self.fabric.record_usage(run_id, att3)

        accounting = self.fabric.get_run_accounting(
            run_id=run_id,
            task_id="task-fb",
            retry_count=2,
            fallback_events=("deepseek -> anthropic", "anthropic -> openai"),
            total_duration_seconds=4.3,
        )

        self.assertEqual(accounting.total_attempts, 3)
        self.assertEqual(accounting.total_input_tokens, 3000)
        self.assertEqual(accounting.total_output_tokens, 350)
        self.assertEqual(accounting.total_tokens, 3350)
        self.assertAlmostEqual(accounting.total_cost, 0.0065, places=5)
        self.assertIsNotNone(accounting.successful_attempt)
        self.assertEqual(accounting.successful_attempt.provider_name, "openai")
        self.assertEqual(len(accounting.fallback_events), 2)

    def test_15_skill_adapter_typed_declarations(self) -> None:
        adapter = SkillRegistryAdapter(self.skill_registry)
        typed_reqs = adapter.get_skill_typed_requirements("skill.python_refactor")
        self.assertTrue(len(typed_reqs) >= 2)
        # All requirements are declarative descriptors
        self.assertTrue(all(isinstance(r, CapabilityRequirement) for r in typed_reqs))


if __name__ == "__main__":
    unittest.main()
