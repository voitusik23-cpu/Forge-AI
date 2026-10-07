"""Comprehensive test suite for Capability & Resource Fabric Stage 3.

Tests:
1. Deterministic candidate ordering (CostTier -> context_window -> canonical_id).
2. Capability requirement filtering across domains (model, tool, execution, task).
3. Cost tier constraint enforcement (free/cheap vs paid authorization).
4. Usage dataclass enhancements (cached_tokens, total_tokens, backward compatibility).
5. Cost accounting (authoritative provider-reported cost vs estimated cost calculation).
6. Multi-attempt fallback chain telemetry aggregation under single run_id.
7. Multi-agent attribution per attempt within a unified run_id.
8. Backward compatibility when fabric=None.
9. Security & authority ceiling (Fabric has zero execution authority).
"""

import unittest
from app.agents.base import AgentExecutionError
from app.agents.providers.model_registry import ModelRegistry, create_default_registry
from app.agents.providers.models import (
    CostTier,
    ModelCapability,
    ProviderInfo,
    ProviderModelInfo,
    ProviderProtocol,
)
from app.agents.registry import AgentRegistry
from app.execution.capabilities import ExecutionCapability
from app.fabric import (
    AttemptUsageRecord,
    CapabilityDescriptor,
    CapabilityDomain,
    CapabilityFabric,
    CapabilityRequirement,
    FabricRequest,
    FabricResolution,
    ResourceDescriptor,
    ResourceType,
    RunAccountingRecord,
    UsageAccountingAdapter,
)
from app.orchestrator.dispatcher import DispatchPolicy, Dispatcher
from app.orchestrator.models import Task, TaskCategory, TaskResult
from app.tools.contracts import ToolDefinition, ToolInvocation, ToolResult, ToolStatus
from app.tools.registry import ToolRegistry
from app.usage import Usage


class StubAgent:
    def __init__(self, name: str, model_name: str = "default-model", usage: Usage | None = None) -> None:
        self.name = name
        self.provider_name = name
        self.model_name = model_name
        self.usage = usage or Usage(input_tokens=100, output_tokens=50, cached_tokens=20, estimated_cost=0.0005)
        self.calls = 0

    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        return TaskResult(
            task_id=task.id,
            success=True,
            output=f"handled by {self.name}",
            provider=self.name,
            agent=self.name,
            model_name=self.model_name,
            usage=self.usage,
        )


class FailingAgent(StubAgent):
    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        raise AgentExecutionError(f"{self.name} failed due to rate limit")


class DummyTool:
    def __init__(self, tool_id: str, name: str) -> None:
        self._definition = ToolDefinition(id=tool_id, name=name, description=f"Test tool {name}")

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def execute(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.COMPLETED, output="ok")

    def validate_input(self, tool_input: object) -> bool:
        return True


class FabricStage3RoutingTests(unittest.TestCase):
    """Test deterministic candidate ordering and cross-domain capability resolution."""

    def setUp(self) -> None:
        self.model_registry = ModelRegistry()
        # Register providers
        for p_id in ("mock", "openrouter", "deepseek", "openai", "anthropic"):
            self.model_registry.register_provider(
                ProviderInfo(
                    provider_id=p_id,
                    display_name=p_id.title(),
                    protocol=ProviderProtocol.OPENAI_CHAT if p_id != "mock" else ProviderProtocol.MOCK,
                )
            )

        # Register models with varied cost tiers, context windows, and capabilities
        self.model_registry.register_model(
            ProviderModelInfo(
                provider_id="mock",
                model_id="mock-fast",
                context_window=32_000,
                capabilities=frozenset([ModelCapability.FAST, ModelCapability.CODE]),
                cost_tier=CostTier.FREE,
            )
        )
        self.model_registry.register_model(
            ProviderModelInfo(
                provider_id="openrouter",
                model_id="free-code-large",
                context_window=128_000,
                capabilities=frozenset([ModelCapability.CODE, ModelCapability.FAST]),
                cost_tier=CostTier.FREE,
            )
        )
        self.model_registry.register_model(
            ProviderModelInfo(
                provider_id="deepseek",
                model_id="deepseek-coder",
                context_window=64_000,
                capabilities=frozenset([ModelCapability.CODE, ModelCapability.REASONING]),
                cost_tier=CostTier.CHEAP,
                input_cost_per_1m=0.14,
                output_cost_per_1m=0.28,
            )
        )
        self.model_registry.register_model(
            ProviderModelInfo(
                provider_id="openai",
                model_id="gpt-4o",
                context_window=128_000,
                capabilities=frozenset([ModelCapability.CODE, ModelCapability.REASONING]),
                cost_tier=CostTier.PAID,
                input_cost_per_1m=2.50,
                output_cost_per_1m=10.0,
            )
        )

        self.tool_registry = ToolRegistry()
        self.tool_registry.register(
            DummyTool(tool_id="read_project_file", name="read_project_file")
        )

        self.fabric = CapabilityFabric(
            model_registry=self.model_registry,
            tool_registry=self.tool_registry,
        )

    def test_01_deterministic_candidate_ordering_free_cheap_paid(self) -> None:
        """Verify sorting order: FREE (by desc context, asc id) -> CHEAP -> PAID."""
        req = FabricRequest(
            subject_agent="coder",
            intent_description="Write algorithm",
            required_capabilities=(CapabilityRequirement.model(ModelCapability.CODE),),
            allow_paid_providers=True,
        )
        res = self.fabric.resolve(req)
        self.assertTrue(res.can_proceed)

        candidate_ids = [m.canonical_id for m in res.candidate_models]
        # openrouter:free-code-large (FREE, 128k) -> mock:mock-fast (FREE, 32k) -> deepseek:deepseek-coder (CHEAP, 64k) -> openai:gpt-4o (PAID, 128k)
        expected_order = [
            "openrouter:free-code-large",
            "mock:mock-fast",
            "deepseek:deepseek-coder",
            "openai:gpt-4o",
        ]
        self.assertEqual(candidate_ids, expected_order)

        # candidate_providers should preserve order without duplicates
        self.assertEqual(
            res.candidate_providers,
            ("openrouter", "mock", "deepseek", "openai"),
        )
        self.assertEqual(res.selected_model, "openrouter:free-code-large")
        self.assertEqual(res.selected_provider, "openrouter")

    def test_02_paid_models_excluded_when_paid_disallowed(self) -> None:
        """Verify PAID models are filtered out when allow_paid_providers=False."""
        req = FabricRequest(
            subject_agent="coder",
            intent_description="Write algorithm within free/cheap budget",
            required_capabilities=(CapabilityRequirement.model(ModelCapability.CODE),),
            allow_paid_providers=False,
        )
        res = self.fabric.resolve(req)
        self.assertTrue(res.can_proceed)

        candidate_ids = [m.canonical_id for m in res.candidate_models]
        self.assertNotIn("openai:gpt-4o", candidate_ids)
        self.assertEqual(
            candidate_ids,
            ["openrouter:free-code-large", "mock:mock-fast", "deepseek:deepseek-coder"],
        )

    def test_03_multi_domain_capability_resolution(self) -> None:
        """Verify resolution with both model and tool capabilities."""
        req = FabricRequest(
            subject_agent="architect",
            intent_description="Inspect and reason on architecture",
            required_capabilities=(
                CapabilityRequirement.model(ModelCapability.REASONING),
                CapabilityRequirement.tool("read_project_file"),
                CapabilityRequirement.execution(ExecutionCapability.EXEC_CHILD),
            ),
            allow_paid_providers=True,
        )
        res = self.fabric.resolve(req)
        self.assertTrue(res.can_proceed)
        self.assertIn("read_project_file", res.selected_tools)
        # deepseek (CHEAP) and openai (PAID) have reasoning
        self.assertEqual(
            [m.canonical_id for m in res.candidate_models],
            ["deepseek:deepseek-coder", "openai:gpt-4o"],
        )
        self.assertEqual(res.selected_provider, "deepseek")

    def test_04_unmet_tool_capability_fails_closed(self) -> None:
        """Verify missing tool capability fails closed with rejection reason."""
        req = FabricRequest(
            subject_agent="worker",
            intent_description="Execute dangerous action",
            required_capabilities=(
                CapabilityRequirement.tool("non_existent_dangerous_tool"),
            ),
        )
        res = self.fabric.resolve(req)
        self.assertFalse(res.can_proceed)
        self.assertFalse(res.is_capable)
        self.assertTrue(any("Unmet required capabilities" in r for r in res.rejection_reasons))


class UsageDataclassTests(unittest.TestCase):
    """Test Usage dataclass extensions and backward compatibility."""

    def test_01_default_usage(self) -> None:
        u = Usage()
        self.assertEqual(u.input_tokens, 0)
        self.assertEqual(u.output_tokens, 0)
        self.assertEqual(u.cached_tokens, 0)
        self.assertIsNone(u.estimated_cost)
        self.assertEqual(u.total_tokens, 0)

    def test_02_keyword_usage_with_cached_tokens(self) -> None:
        u = Usage(input_tokens=1000, output_tokens=250, cached_tokens=400, estimated_cost=0.005)
        self.assertEqual(u.input_tokens, 1000)
        self.assertEqual(u.output_tokens, 250)
        self.assertEqual(u.cached_tokens, 400)
        self.assertEqual(u.estimated_cost, 0.005)
        self.assertEqual(u.total_tokens, 1250)

    def test_03_legacy_positional_usage_three_arguments(self) -> None:
        """Test legacy Usage(0, 0, 0.0) where 3rd arg was float estimated_cost."""
        u = Usage(0, 0, 0.0)
        self.assertEqual(u.input_tokens, 0)
        self.assertEqual(u.output_tokens, 0)
        self.assertEqual(u.cached_tokens, 0)
        self.assertEqual(u.estimated_cost, 0.0)
        self.assertEqual(u.total_tokens, 0)

    def test_04_legacy_positional_usage_two_arguments(self) -> None:
        u = Usage(150, 75)
        self.assertEqual(u.input_tokens, 150)
        self.assertEqual(u.output_tokens, 75)
        self.assertEqual(u.cached_tokens, 0)
        self.assertIsNone(u.estimated_cost)
        self.assertEqual(u.total_tokens, 225)


class CostAccountingAndTelemetryTests(unittest.TestCase):
    """Test provider-reported cost vs estimated cost calculation and run accounting."""

    def setUp(self) -> None:
        self.fabric = CapabilityFabric()
        self.model_info = ProviderModelInfo(
            provider_id="deepseek",
            model_id="deepseek-chat",
            context_window=64_000,
            capabilities=frozenset([ModelCapability.CODE]),
            cost_tier=CostTier.CHEAP,
            input_cost_per_1m=0.14,
            output_cost_per_1m=0.28,
            metadata={"cached_cost_per_1m": 0.014},
        )

    def test_01_calculate_cost_with_cached_tokens(self) -> None:
        cost = UsageAccountingAdapter.calculate_cost(
            input_tokens=1_000_000,
            output_tokens=500_000,
            cached_tokens=2_000_000,
            model_info=self.model_info,
        )
        # input: 1M * 0.14 = $0.14
        # output: 0.5M * 0.28 = $0.14
        # cached: 2M * 0.014 = $0.028
        # total: 0.14 + 0.14 + 0.028 = $0.308
        self.assertAlmostEqual(cost, 0.308, places=5)

    def test_02_create_attempt_usage_uses_model_pricing_when_estimated_cost_missing(self) -> None:
        usage = Usage(input_tokens=100_000, output_tokens=50_000, cached_tokens=0, estimated_cost=None)
        attempt = UsageAccountingAdapter.create_attempt_usage(
            attempt_index=1,
            agent_name="deepseek",
            provider_name="deepseek",
            model_name="deepseek-chat",
            usage=usage,
            duration_seconds=1.5,
            model_info=self.model_info,
        )
        # input: 0.1M * 0.14 = $0.014, output: 0.05M * 0.28 = $0.014 => 0.028
        self.assertAlmostEqual(attempt.estimated_cost, 0.028, places=5)
        self.assertAlmostEqual(attempt.effective_cost, 0.028, places=5)
        self.assertEqual(attempt.total_tokens, 150_000)

    def test_03_provider_reported_cost_takes_precedence_over_estimated_cost(self) -> None:
        attempt = AttemptUsageRecord(
            attempt_index=1,
            agent_name="openai",
            provider_name="openai",
            model_name="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            cached_tokens=200,
            provider_reported_cost=0.015,
            estimated_cost=0.010,
        )
        self.assertEqual(attempt.effective_cost, 0.015)

    def test_04_multi_attempt_run_accounting_aggregation(self) -> None:
        run_id = "run-stage3-multi-attempt-001"
        attempt1 = AttemptUsageRecord(
            attempt_index=1,
            agent_name="deepseek",
            provider_name="deepseek",
            model_name="deepseek-chat",
            input_tokens=500,
            output_tokens=100,
            cached_tokens=50,
            duration_seconds=0.8,
            estimated_cost=0.001,
            success=False,
            error_message="Rate limit exceeded",
            is_fallback=False,
        )
        attempt2 = AttemptUsageRecord(
            attempt_index=2,
            agent_name="openai",
            provider_name="openai",
            model_name="gpt-4o",
            input_tokens=600,
            output_tokens=200,
            cached_tokens=100,
            duration_seconds=1.2,
            provider_reported_cost=0.005,
            success=True,
            is_fallback=True,
        )

        self.fabric.record_usage(run_id, attempt1)
        self.fabric.record_usage(run_id, attempt2)

        accounting = self.fabric.get_run_accounting(
            run_id=run_id,
            task_id="task-001",
            project_id="proj-forge",
            retry_count=1,
            fallback_events=("deepseek -> openai",),
            associated_artifact_ids=("art-1", "art-2"),
            total_duration_seconds=2.0,
        )

        self.assertEqual(accounting.run_id, run_id)
        self.assertEqual(accounting.task_id, "task-001")
        self.assertEqual(accounting.project_id, "proj-forge")
        self.assertEqual(accounting.total_attempts, 2)
        self.assertEqual(accounting.total_input_tokens, 1100)
        self.assertEqual(accounting.total_output_tokens, 300)
        self.assertEqual(accounting.total_cached_tokens, 150)
        self.assertEqual(accounting.total_tokens, 1400)
        self.assertAlmostEqual(accounting.total_cost, 0.006, places=5)
        self.assertAlmostEqual(accounting.total_estimated_cost, 0.001, places=5)
        self.assertAlmostEqual(accounting.total_provider_reported_cost, 0.005, places=5)
        self.assertEqual(accounting.retry_count, 1)
        self.assertEqual(accounting.fallback_events, ("deepseek -> openai",))
        self.assertEqual(accounting.associated_artifact_ids, ("art-1", "art-2"))
        self.assertIsNotNone(accounting.successful_attempt)
        self.assertEqual(accounting.successful_attempt.agent_name, "openai")


class FabricDispatcherEndToEndTests(unittest.TestCase):
    """Test full integration between Dispatcher, Fabric, and Fallback chains."""

    def setUp(self) -> None:
        self.agent_registry = AgentRegistry()
        self.model_registry = create_default_registry()
        self.fabric = CapabilityFabric(model_registry=self.model_registry)

        self.agents = {}
        for name in ("openai", "anthropic", "google", "deepseek", "mock"):
            agent = StubAgent(
                name,
                model_name=f"{name}-chat-v1",
                usage=Usage(input_tokens=200, output_tokens=80, cached_tokens=50, estimated_cost=0.001),
            )
            self.agents[name] = agent
            self.agent_registry.register(agent)

        self.dispatcher = Dispatcher(
            agent_registry=self.agent_registry,
            default_provider="mock",
            fallback_chain=("deepseek", "anthropic", "openai"),
            model_registry=self.model_registry,
            fabric=self.fabric,
            allow_paid_providers=True,
        )

    def test_01_dispatcher_records_usage_with_cached_tokens(self) -> None:
        run_id = "run-e2e-cached-tokens"
        task = Task(
            id="task-cached-1",
            description="Run analysis with cached token tracking",
            category=TaskCategory.ANALYSIS,
            context={"run_id": run_id},
        )
        result = self.dispatcher.dispatch(task)
        self.assertTrue(result.success)

        accounting = self.fabric.get_run_accounting(run_id=run_id, task_id=task.id)
        self.assertEqual(accounting.total_attempts, 1)
        self.assertEqual(accounting.total_input_tokens, 200)
        self.assertEqual(accounting.total_output_tokens, 80)
        self.assertEqual(accounting.total_cached_tokens, 50)
        self.assertEqual(accounting.total_tokens, 280)

    def test_02_multi_attempt_fallback_preserves_attribution_and_accounting(self) -> None:
        failing_deepseek = FailingAgent("deepseek", model_name="deepseek-chat")
        self.agent_registry._agents["deepseek"] = failing_deepseek

        run_id = "run-e2e-fallback-chain"
        task = Task(
            id="task-fb-chain",
            description="Refactoring task triggering fallback",
            category=TaskCategory.CODE,
            context={"run_id": run_id},
            parameters={"model": "deepseek:deepseek-chat"},
        )
        result = self.dispatcher.dispatch(task)
        self.assertTrue(result.success)
        self.assertNotEqual(result.provider, "deepseek")

        accounting = self.fabric.get_run_accounting(run_id=run_id, task_id=task.id)
        self.assertTrue(accounting.total_attempts >= 2)
        # First attempt is deepseek (failed)
        self.assertEqual(accounting.attempt_records[0].provider_name, "deepseek")
        self.assertFalse(accounting.attempt_records[0].success)
        # Succeeded on fallback
        self.assertTrue(accounting.successful_attempt.success)
        self.assertNotEqual(accounting.successful_attempt.provider_name, "deepseek")

    def test_03_legacy_dispatcher_when_fabric_is_none(self) -> None:
        legacy_dispatcher = Dispatcher(
            agent_registry=self.agent_registry,
            default_provider="mock",
            fallback_chain=("deepseek", "anthropic", "openai"),
            model_registry=self.model_registry,
            fabric=None,
            allow_paid_providers=True,
        )
        task = Task(
            id="task-legacy-1",
            description="Legacy dispatch without fabric",
            category=TaskCategory.CODE,
        )
        result = legacy_dispatcher.dispatch(task)
        self.assertTrue(result.success)
        self.assertIsNotNone(result.provider)

    def test_04_multi_agent_attribution_under_unified_run_id(self) -> None:
        run_id = "run-multi-agent-pipeline-99"

        # 1. Planner task
        task_plan = Task(
            id="task-plan-1",
            description="Create architecture plan",
            category=TaskCategory.ANALYSIS,
            context={"run_id": run_id, "project_id": "proj-alpha"},
        )
        res_plan = self.dispatcher.dispatch(task_plan, provider_name="anthropic")
        self.assertTrue(res_plan.success)

        # 2. Coder task
        task_code = Task(
            id="task-code-2",
            description="Implement plan in code",
            category=TaskCategory.CODE,
            context={"run_id": run_id, "project_id": "proj-alpha"},
        )
        res_code = self.dispatcher.dispatch(task_code, provider_name="openai")
        self.assertTrue(res_code.success)

        # 3. Reviewer task
        task_rev = Task(
            id="task-rev-3",
            description="Review pull request changes",
            category=TaskCategory.REVIEW,
            context={"run_id": run_id, "project_id": "proj-alpha"},
        )
        res_rev = self.dispatcher.dispatch(task_rev, provider_name="google")
        self.assertTrue(res_rev.success)

        # Aggregated RunAccountingRecord
        accounting = self.fabric.get_run_accounting(
            run_id=run_id,
            project_id="proj-alpha",
            associated_artifact_ids=("plan.md", "diff.patch", "review.md"),
        )
        self.assertEqual(accounting.run_id, run_id)
        self.assertEqual(accounting.project_id, "proj-alpha")
        self.assertEqual(accounting.total_attempts, 3)
        self.assertEqual(
            [r.agent_name for r in accounting.attempt_records],
            ["anthropic", "openai", "google"],
        )
        self.assertEqual(accounting.total_input_tokens, 600)
        self.assertEqual(accounting.total_output_tokens, 240)
        self.assertEqual(accounting.total_cached_tokens, 150)
        self.assertEqual(accounting.total_tokens, 840)
        self.assertEqual(
            accounting.associated_artifact_ids,
            ("plan.md", "diff.patch", "review.md"),
        )

    def test_05_fabric_authority_ceiling_and_security(self) -> None:
        """Verify Fabric has zero execution authority and only produces static proposals."""
        req = FabricRequest(
            subject_agent="agent_untrusted",
            intent_description="Attempt privilege escalation",
            required_capabilities=(
                CapabilityRequirement.model(ModelCapability.REASONING),
                CapabilityRequirement.tool("read_project_file"),
            ),
        )
        resolution = self.fabric.resolve(req)
        # Resolution is a descriptor
        self.assertIsInstance(resolution, FabricResolution)
        self.assertFalse(hasattr(resolution, "execute"))
        self.assertFalse(hasattr(resolution, "grant_permission"))
        self.assertFalse(hasattr(self.fabric, "execute"))
        self.assertFalse(hasattr(self.fabric, "mutate_scope"))


if __name__ == "__main__":
    unittest.main()
