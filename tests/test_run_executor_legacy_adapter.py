"""Tests for RunExecutor Legacy Adapter with ContextSelector and model-aware budgeting."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agents.base import AgentExecutionError
from app.agents.providers.base import ProviderRequest
from app.agents.providers.mock import MockProvider
from app.agents.providers.model_registry import ModelRegistry
from app.agents.providers.models import (
    CostTier,
    ModelCapability,
    ProviderInfo,
    ProviderModelInfo,
    ProviderProtocol,
)
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.config.settings import RuntimeSettings
from app.context import (
    ContextAssembler,
    ContextBudgetPolicy,
    ContextFreshness,
    ContextItem,
    ContextSelector,
    ContextSensitivity,
    ContextSource,
    ContextTrust,
    ExecutionContext,
)
from app.orchestrator.models import EventType, RunState, Task, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_runtime


class ContextCapturingAgent:
    def __init__(self, name: str = "mock") -> None:
        self.name = name
        self.provider_name = name
        self.captured_context: dict[str, object] | None = None
        self.call_count: int = 0

    def run(self, task: Task) -> TaskResult:
        self.call_count += 1
        self.captured_context = task.context
        return TaskResult(
            task_id=task.id,
            success=True,
            output=f"Executed task {task.id}",
            provider=self.name,
            model_name="test-model",
        )


class RunExecutorLegacyAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = ContextCapturingAgent("mock")
        self.agent_registry = AgentRegistry()
        self.agent_registry.register(self.agent)

    def test_legacy_run_executor_attaches_forge_execution_context(self) -> None:
        orchestrator = Orchestrator(self.agent_registry, default_provider="mock")
        executor = RunExecutor(orchestrator)
        task = Task(id="task-legacy-1", description="Build web application")

        run = executor.execute(task, explicit_inputs=["reference documentation text"])

        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(self.agent.call_count, 1)
        self.assertIsNotNone(self.agent.captured_context)
        self.assertIn("forge_execution_context", self.agent.captured_context)

        fec = self.agent.captured_context["forge_execution_context"]
        self.assertEqual(fec["run_id"], run.id)
        self.assertEqual(len(fec["items"]), 2)
        self.assertEqual(fec["items"][0]["kind"], "user_task")
        self.assertEqual(fec["items"][0]["content"], "Build web application")
        self.assertEqual(fec["items"][1]["kind"], "explicit_input")
        self.assertEqual(fec["items"][1]["content"], "reference documentation text")

    def test_forbidden_substring_in_explicit_input_is_dropped_by_selector(self) -> None:
        orchestrator = Orchestrator(self.agent_registry, default_provider="mock")
        executor = RunExecutor(orchestrator)
        task = Task(id="task-leak-test", description="Summarize input")

        leaked_input = ContextItem(
            id="leaked-token",
            kind="api_credential",
            content="Here is the api_key for the internal database",
            source=ContextSource.EXPLICIT_INPUT,
            trust=ContextTrust.UNTRUSTED,
            freshness=ContextFreshness.UNKNOWN,
            sensitivity=ContextSensitivity.INTERNAL,
        )
        safe_input = ContextItem(
            id="safe-notes",
            kind="notes",
            content="Valid system architecture notes",
            source=ContextSource.EXPLICIT_INPUT,
            trust=ContextTrust.TRUSTED,
            freshness=ContextFreshness.CURRENT,
            sensitivity=ContextSensitivity.INTERNAL,
        )

        run = executor.execute(task, explicit_inputs=[leaked_input, safe_input])

        self.assertEqual(run.state, RunState.COMPLETED)
        fec = self.agent.captured_context["forge_execution_context"]
        item_ids = [item["id"] for item in fec["items"]]

        self.assertIn("safe-notes", item_ids)
        self.assertNotIn("leaked-token", item_ids)
        self.assertNotIn("api_key", repr(fec))

    def test_confidential_and_restricted_items_are_excluded(self) -> None:
        orchestrator = Orchestrator(self.agent_registry, default_provider="mock")
        executor = RunExecutor(orchestrator)
        task = Task(id="task-sensitivity", description="Process data")

        confidential_item = ContextItem(
            id="conf-1",
            kind="secret_config",
            content="Confidential server endpoint config",
            source=ContextSource.EXPLICIT_INPUT,
            sensitivity=ContextSensitivity.CONFIDENTIAL,
        )
        restricted_item = ContextItem(
            id="restr-1",
            kind="restricted_data",
            content="Restricted data payload",
            source=ContextSource.EXPLICIT_INPUT,
            sensitivity=ContextSensitivity.RESTRICTED,
        )
        public_item = ContextItem(
            id="pub-1",
            kind="public_data",
            content="Public documentation excerpt",
            source=ContextSource.EXPLICIT_INPUT,
            sensitivity=ContextSensitivity.PUBLIC,
        )

        run = executor.execute(
            task,
            explicit_inputs=[confidential_item, restricted_item, public_item],
        )

        self.assertEqual(run.state, RunState.COMPLETED)
        fec = self.agent.captured_context["forge_execution_context"]
        item_ids = [item["id"] for item in fec["items"]]

        self.assertIn("pub-1", item_ids)
        self.assertNotIn("conf-1", item_ids)
        self.assertNotIn("restr-1", item_ids)

    def test_model_aware_budget_resolution_from_model_registry(self) -> None:
        model_registry = ModelRegistry()
        provider_info = ProviderInfo(
            provider_id="mock",
            display_name="Mock Provider",
            protocol=ProviderProtocol.MOCK,
            supported_models=("mock-large",),
        )
        model_registry.register_provider(provider_info)
        model_info = ProviderModelInfo(
            provider_id="mock",
            model_id="mock-large",
            display_name="Mock Large Model",
            context_window=65_536,
            capabilities=frozenset({ModelCapability.CODE}),
            max_output_tokens=8_192,
            cost_tier=CostTier.CHEAP,
        )
        model_registry.register_model(model_info)

        from app.agents.providers.config import ProviderConfig

        provider_registry = ProviderRegistry()
        mock_provider = MockProvider(
            ProviderConfig(provider_name="mock", model_name="mock-large", enabled=True)
        )
        provider_registry.register(mock_provider)

        orchestrator = Orchestrator(
            self.agent_registry,
            default_provider="mock",
            provider_registry=provider_registry,
            model_registry=model_registry,
        )
        executor = RunExecutor(orchestrator)
        task = Task(id="task-model-aware", description="Code generation")

        run = executor.execute(task, provider_name="mock")
        self.assertEqual(run.state, RunState.COMPLETED)

    def test_tier1_fail_closed_prevents_agent_invocation_on_critical_unfit(self) -> None:
        # Tight budget where even the task description violates size or is corrupted
        tight_policy = ContextBudgetPolicy(
            total_context_window=300,
            max_output_tokens=150,
            safety_headroom_tokens=140,  # 10 token budget
        )
        assembler = ContextAssembler(budget_policy=tight_policy)
        orchestrator = Orchestrator(self.agent_registry, default_provider="mock")
        executor = RunExecutor(orchestrator, context_assembler=assembler)

        # Huge task description (> 10 tokens)
        giant_task = Task(
            id="task-too-large",
            description="A very long task description that cannot fit in a 10 token budget at all.",
        )

        run = executor.execute(giant_task)

        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(run.error.error_type, "CriticalContextUnfitError")
        self.assertEqual(self.agent.call_count, 0)
        self.assertTrue(any(event.type == EventType.RUN_FAILED for event in run.events))

    def test_context_fingerprint_deterministic_and_matches_assembled_event(self) -> None:
        orchestrator = Orchestrator(self.agent_registry, default_provider="mock")
        executor = RunExecutor(orchestrator)
        task = Task(id="task-fp", description="Fingerprint check")

        run = executor.execute(task, explicit_inputs=["sample explicit input"])

        self.assertEqual(run.state, RunState.COMPLETED)
        fec = self.agent.captured_context["forge_execution_context"]
        assembled_event = next(
            event for event in run.events if event.type == EventType.CONTEXT_ASSEMBLED
        )

        # Reconstructed ExecutionContext from agent context
        reconstructed = ExecutionContext(
            run_id=fec["run_id"],
            items=tuple(
                ContextItem(
                    id=it["id"],
                    kind=it["kind"],
                    content=it["content"],
                    source=ContextSource(it["source"]),
                    trust=ContextTrust(it["trust"]),
                    freshness=ContextFreshness(it["freshness"]),
                )
                for it in fec["items"]
            ),
        )

        self.assertEqual(assembled_event.data["context_fingerprint"], reconstructed.fingerprint)
        self.assertEqual(assembled_event.data["item_count"], len(reconstructed.items))


if __name__ == "__main__":
    unittest.main()
