"""Offline integration coverage for context through the normal runtime path."""

import copy
import unittest
from unittest.mock import patch

from app.agents.providers.base import ProviderRequest
from app.agents.providers.mock import MockProvider
from app.config.settings import RuntimeSettings
from app.context import (
    ContextFreshness,
    ContextItem,
    ContextSource,
    ContextTrust,
    ExecutionContext,
)
from app.orchestrator.models import EventType, Task
from app.runtime.bootstrap import create_runtime


class ContextExecutionPathTests(unittest.TestCase):
    def test_run_context_reaches_mock_provider_through_dispatcher(self):
        runtime = create_runtime(
            RuntimeSettings(default_provider="mock", enabled_providers=("mock",))
        )
        provider = runtime.provider_registry.get("mock")
        self.assertIsInstance(provider, MockProvider)
        task = Task(
            id="context-flow-1",
            description="Summarize the supplied reference.",
            context={"locale": "en", "request": "summary"},
        )
        original_task = copy.deepcopy(task)
        supplied = ContextItem(
            id="reference-1",
            kind="reference",
            content="Fixed offline reference material.",
            source=ContextSource.SYSTEM,
            trust=ContextTrust.TRUSTED,
            freshness=ContextFreshness.CURRENT,
        )
        captured = []
        original_generate = provider.generate

        def capture_and_generate(request):
            self.assertIsInstance(request, ProviderRequest)
            captured.append(request)
            return original_generate(request)

        with patch.object(provider, "generate", side_effect=capture_and_generate) as generate:
            run = runtime.run_executor.execute(task, explicit_inputs=[supplied])

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(len(captured), 1)
        self.assertEqual(run.task, original_task)
        self.assertTrue(run.result.success)
        self.assertEqual(run.result.provider, "mock")
        self.assertIn("MockProvider response:", run.result.output)

        payload = captured[0].context["forge_execution_context"]
        self.assertEqual(payload["run_id"], run.id)
        items = payload["items"]
        self.assertEqual(
            [item["kind"] for item in items],
            ["user_task", "task_context", "reference"],
        )
        self.assertEqual(items[0]["content"], task.description)
        self.assertEqual(items[1]["content"], '{"locale": "en", "request": "summary"}')
        self.assertEqual(items[2]["content"], supplied.content)
        self.assertEqual(items[2]["source"], "EXPLICIT_INPUT")
        self.assertEqual(items[2]["trust"], "TRUSTED")
        self.assertEqual(items[2]["freshness"], "CURRENT")

        passed_context = ExecutionContext(
            run_id=payload["run_id"],
            items=tuple(
                ContextItem(
                    id=item["id"],
                    kind=item["kind"],
                    content=item["content"],
                    source=ContextSource(item["source"]),
                    trust=ContextTrust(item["trust"]),
                    freshness=ContextFreshness(item["freshness"]),
                )
                for item in items
            ),
        )

        assembled = next(
            event for event in run.events if event.type == EventType.CONTEXT_ASSEMBLED
        )
        self.assertEqual(assembled.run_id, run.id)
        self.assertEqual(assembled.data["item_count"], 3)
        self.assertEqual(
            assembled.data["context_fingerprint"], passed_context.fingerprint
        )
        self.assertNotIn(supplied.content, repr(assembled.data))
        self.assertTrue(
            any(event.type == EventType.PROVIDER_ATTEMPT for event in run.events)
        )
        self.assertTrue(
            any(event.type == EventType.PROVIDER_RESULT for event in run.events)
        )

    def test_context_fingerprint_is_deterministic_for_same_items(self):
        runtime = create_runtime(
            RuntimeSettings(default_provider="mock", enabled_providers=("mock",))
        )
        assembler = runtime.run_executor._context_assembler
        task = Task(id="stable-context", description="Stable task")

        first = assembler.assemble(task, "run-one", ["same supplied material"])
        second = assembler.assemble(task, "run-two", ["same supplied material"])

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(first.fingerprint, second.fingerprint)


if __name__ == "__main__":
    unittest.main()
