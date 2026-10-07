"""Integration tests for model-aware context budgeting and DecisionContextAssembler (Stage 18 / Block 2)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from app.agent_runtime.harness import AgentHarness
from app.agent_runtime.models import HarnessPhase, HarnessRequest, HarnessStatus
from app.agents.providers.models import CostTier, ProviderModelInfo
from app.context.assembler import DecisionContextAssembler
from app.context.models import (
    ContextItem,
    ContextSourceType,
    ContextTrustLevel,
    DecisionContextEnvelope,
)
from app.context.selector import (
    ContextBudgetPolicy,
    ContextSelector,
    CriticalContextUnfitError,
    InsufficientContextBudgetError,
)
from app.decision.ai_provider import AIDecisionProvider
from app.decision.models import Decision, DecisionAction, DecisionRequest, DecisionType
from app.decision.provider import DeterministicDecisionProvider
from app.projects.state import ProjectState, ProjectStateStatus
from app.snapshots import SnapshotFile
from app.tasks.specification import Requirement, TaskSpecification
from app.understanding.models import (
    DeclaredDependency,
    ManifestDescriptor,
    NodeType,
    ProjectTopology,
    StructuralFact,
    StructuralFactType,
    TopologyEdge,
    TopologyNode,
    UnderstandingSnapshot,
)


class TestContextBudgetIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.assembler = DecisionContextAssembler()

    def test_assembler_with_understanding_snapshot(self) -> None:
        nodes = (
            TopologyNode(node_id="n1", name="main", relative_path="app/main.py", node_type=NodeType.MODULE),
            TopologyNode(node_id="n2", name="utils", relative_path="app/utils.py", node_type=NodeType.MODULE),
        )
        facts = (
            StructuralFact(
                fact_id="fact-1",
                fact_type=StructuralFactType.MODULE_DEFINED,
                source_path="app/main.py",
                line_start=1,
                line_end=10,
                extractor_id="py_extractor",
            ),
        )
        manifests = (
            ManifestDescriptor(
                relative_path="pyproject.toml",
                manifest_type="python_pyproject",
                declared_dependencies=(
                    DeclaredDependency(
                        name="fastapi",
                        version_spec=">=0.100.0",
                        manifest_path="pyproject.toml",
                    ),
                ),
            ),
        )
        files = (
            SnapshotFile(relative_path="app/main.py", exists=True, fingerprint="sha1"),
            SnapshotFile(relative_path="pyproject.toml", exists=True, fingerprint="sha2"),
        )
        snapshot = UnderstandingSnapshot(
            snapshot_id="snap-test-01",
            project_id="test-project",
            workspace_fingerprint="ws-sha-123",
            files=files,
            manifests=manifests,
            topology=ProjectTopology(nodes=nodes, edges=()),
            facts=facts,
        )

        envelope = self.assembler.assemble(
            run_id="run-u-1",
            attempt_number=0,
            task_id="task-u",
            understanding_snapshot=snapshot,
        )

        self.assertIsInstance(envelope, DecisionContextEnvelope)
        u_items = [
            it for it in envelope.context_items if it.source_type == ContextSourceType.PROJECT_UNDERSTANDING
        ]
        self.assertEqual(len(u_items), 1)
        self.assertEqual(u_items[0].item_id, "understanding:snap-test-01")
        payload = json.loads(u_items[0].value)
        self.assertEqual(payload["project_id"], "test-project")
        self.assertEqual(payload["total_files"], 2)
        self.assertEqual(payload["total_manifests"], 1)
        self.assertEqual(payload["total_modules"], 1)

    def test_assembler_with_different_model_budgets(self) -> None:
        # Small 32k model (Groq/Qwen)
        small_model = ProviderModelInfo(
            provider_id="groq",
            model_id="qwen-2.5-coder-32b",
            display_name="Qwen 2.5 Coder 32B",
            context_window=32_768,
            capabilities=frozenset(),
            max_output_tokens=4_096,
            cost_tier=CostTier.CHEAP,
        )
        # Large 1M model (Gemini)
        large_model = ProviderModelInfo(
            provider_id="google",
            model_id="gemini-2.0-flash",
            display_name="Gemini 2.0 Flash",
            context_window=1_048_576,
            capabilities=frozenset(),
            max_output_tokens=8_192,
            cost_tier=CostTier.CHEAP,
        )

        # Create 10 items
        items = [
            ContextItem(
                item_id=f"memory-{i}",
                item_type="memory",
                source_type=ContextSourceType.PROJECT_MEMORY,
                value=f"Detailed project memory record #{i} with architectural notes " * 20,
            )
            for i in range(10)
        ]

        small_envelope = self.assembler.assemble(
            run_id="run-small",
            attempt_number=0,
            model_info=small_model,
            context_items=items,
        )
        self.assertIsNotNone(small_envelope.selection_report)
        self.assertEqual(small_envelope.selection_report.model_name, "groq:qwen-2.5-coder-32b")
        self.assertEqual(small_envelope.selection_report.available_budget_tokens, 32_768 - 4_096 - 2_048)

        large_envelope = self.assembler.assemble(
            run_id="run-large",
            attempt_number=0,
            model_info=large_model,
            context_items=items,
        )
        self.assertIsNotNone(large_envelope.selection_report)
        self.assertEqual(large_envelope.selection_report.model_name, "google:gemini-2.0-flash")
        self.assertEqual(large_envelope.selection_report.available_budget_tokens, 1_048_576 - 8_192 - 2_048)

    def test_assembler_insufficient_physical_budget_raises(self) -> None:
        broken_model = ProviderModelInfo(
            provider_id="custom",
            model_id="tiny-broken",
            display_name="Tiny Broken",
            context_window=2_048,
            capabilities=frozenset(),
            max_output_tokens=2_048,
            cost_tier=CostTier.CHEAP,
        )
        with self.assertRaises(InsufficientContextBudgetError):
            self.assembler.assemble(
                run_id="run-insufficient",
                attempt_number=0,
                model_info=broken_model,
            )

    def test_assembler_tier1_unfit_raises_fail_closed(self) -> None:
        tight_policy = ContextBudgetPolicy(
            total_context_window=600,
            max_output_tokens=300,
            safety_headroom_tokens=280,  # 20 tokens budget
        )
        # Giant requirement that cannot fit in 20 tokens
        giant_req = Requirement(
            requirement_id="req-huge",
            description="A very long requirement specification description that is way too large to fit in 20 tokens.",
        )
        task_spec = TaskSpecification(
            task_id="task-huge",
            title="Impossible task",
            description="Task with giant requirement",
            requirements=(giant_req,),
            acceptance_criteria=(),
        )
        with self.assertRaises(CriticalContextUnfitError):
            self.assembler.assemble(
                run_id="run-tier1-fail",
                attempt_number=0,
                task_specification=task_spec,
                budget_policy=tight_policy,
            )

    def test_backward_compatibility_old_assemble_call(self) -> None:
        envelope = self.assembler.assemble(
            run_id="run-legacy",
            attempt_number=0,
            task_id="task-legacy",
            blocking_conditions=["test_blocking"],
        )
        self.assertIsInstance(envelope, DecisionContextEnvelope)
        self.assertEqual(envelope.run_id, "run-legacy")
        self.assertEqual(envelope.blocking_conditions, ("test_blocking",))
        self.assertIsNotNone(envelope.selection_report)

    def test_harness_integration_with_context_budgeting(self) -> None:
        task_spec = TaskSpecification(
            task_id="spec-harness-1",
            title="Implement API endpoint",
            description="Build /health endpoint",
            requirements=(Requirement(requirement_id="req-1", description="Return 200 OK"),),
            acceptance_criteria=(),
        )

        harness = AgentHarness(
            context_assembler=self.assembler,
            decision_provider=DeterministicDecisionProvider(),
        )

        harness_req = HarnessRequest(
            run_id="harness-run-1",
            attempt_number=0,
            task_specification=task_spec,
        )

        result = harness.run(harness_req)
        self.assertIsInstance(result.final_state.status, HarnessStatus)
        self.assertTrue(len(result.decisions) > 0)


if __name__ == "__main__":
    unittest.main()
