"""Offline tests for the in-memory Run and event wrapper."""

import unittest

from app.agents.base import AgentExecutionError
from app.context import ContextAssembler, ContextSource
from app.agents.registry import AgentRegistry
from app.orchestrator.models import EventType, RunState, Task, TaskCategory, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor


class StubAgent:
    def __init__(self, name: str, *, fails: bool = False) -> None:
        self.name = name
        self.provider_name = name
        self.fails = fails
        self.calls = 0

    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        if self.fails:
            raise AgentExecutionError(f"{self.name} unavailable")
        return TaskResult(
            task_id=task.id,
            success=True,
            output=f"result from {self.name}",
            provider=self.name,
            model_name="offline-model",
        )


class ContextAgent(StubAgent):
    def run(self, task: Task) -> TaskResult:
        self.received_context = task.context
        return super().run(task)


class RunExecutorTests(unittest.TestCase):
    def make_executor(self, agents, *, default_provider="mock", fallback_chain=()):
        registry = AgentRegistry()
        for agent in agents:
            registry.register(agent)
        orchestrator = Orchestrator(
            registry, default_provider=default_provider, fallback_chain=fallback_chain
        )
        return RunExecutor(orchestrator)

    def test_run_lifecycle_and_dispatch_result_are_preserved(self):
        agent = StubAgent("mock")
        executor = self.make_executor([agent])
        task = Task(id="run-ok", description="offline task")

        run = executor.execute(task)

        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertTrue(run.result.success)
        self.assertEqual(run.result.output, "result from mock")
        self.assertEqual(run.result.provider, "mock")
        self.assertIsNone(run.error)
        self.assertEqual(run.events[0].type, EventType.RUN_STARTED)
        self.assertEqual(run.events[-1].type, EventType.RUN_COMPLETED)
        self.assertTrue(all(event.run_id == run.id for event in run.events))
        self.assertEqual(len({event.event_id for event in run.events}), len(run.events))

    def test_each_run_has_a_unique_id(self):
        executor = self.make_executor([StubAgent("mock")])

        first = executor.execute(Task(id="first", description="one"))
        second = executor.execute(Task(id="second", description="two"))

        self.assertNotEqual(first.id, second.id)

    def test_provider_attempt_and_result_events_capture_available_metadata(self):
        executor = self.make_executor([StubAgent("openai")], default_provider="openai")

        run = executor.execute(Task(id="provider-events", description="offline"))

        attempt = next(event for event in run.events if event.type == EventType.PROVIDER_ATTEMPT)
        result = next(event for event in run.events if event.type == EventType.PROVIDER_RESULT)
        self.assertEqual(attempt.data["provider"], "openai")
        self.assertEqual(attempt.data["attempt"], 1)
        self.assertEqual(result.data["provider"], "openai")
        self.assertTrue(result.data["success"])
        self.assertEqual(result.data["model"], "offline-model")
        self.assertNotIn("output", result.data)

    def test_context_is_passed_to_dispatch_and_event_contains_only_summary(self):
        agent = ContextAgent("mock")
        executor = self.make_executor([agent])
        raw_input = "private reference content"

        run = executor.execute(
            Task(id="context-run", description="offline task"),
            explicit_inputs=[raw_input],
        )

        execution_context = agent.received_context["forge_execution_context"]
        self.assertEqual(execution_context["run_id"], run.id)
        self.assertEqual(execution_context["items"][0]["source"], "USER_TASK")
        self.assertEqual(execution_context["items"][1]["source"], "EXPLICIT_INPUT")
        event = next(event for event in run.events if event.type == EventType.CONTEXT_ASSEMBLED)
        self.assertEqual(event.data["item_count"], 2)
        self.assertNotIn(raw_input, repr(event.data))

    def test_context_budget_failure_stops_before_agent_execution(self):
        agent = StubAgent("mock")
        registry = AgentRegistry()
        registry.register(agent)
        executor = RunExecutor(
            Orchestrator(registry), ContextAssembler(max_items=1)
        )

        run = executor.execute(
            Task(id="over-budget", description="task"), explicit_inputs=["extra"]
        )

        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(run.error.error_type, "ContextBudgetExceededError")
        self.assertEqual(agent.calls, 0)
        self.assertFalse(any(event.type == EventType.CONTEXT_ASSEMBLED for event in run.events))
        self.assertEqual(run.events[-1].type, EventType.RUN_FAILED)

    def test_unserializable_task_context_fails_before_agent_execution(self):
        agent = StubAgent("mock")
        executor = self.make_executor([agent])

        run = executor.execute(
            Task(id="bad-context", description="task", context={"opaque": object()})
        )

        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(run.error.error_type, "ContextAssemblyError")
        self.assertEqual(agent.calls, 0)
        self.assertEqual(run.events[-1].type, EventType.RUN_FAILED)

    def test_fallback_events_follow_existing_dispatcher_fallback(self):
        primary = StubAgent("openai", fails=True)
        fallback = StubAgent("anthropic")
        executor = self.make_executor(
            [primary, fallback],
            default_provider="mock",
            fallback_chain=("anthropic",),
        )

        run = executor.execute(
            Task(id="fallback-run", description="coding task", category=TaskCategory.CODING)
        )

        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(run.result.provider, "anthropic")
        fallback_events = [event for event in run.events if event.type == EventType.FALLBACK]
        self.assertEqual(len(fallback_events), 1)
        self.assertEqual(fallback_events[0].data["source"], "openai")
        self.assertEqual(fallback_events[0].data["target"], "anthropic")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)

    def test_exhausted_dispatch_records_failure_and_keeps_result_error(self):
        executor = self.make_executor(
            [StubAgent("openai", fails=True)],
            default_provider="openai",
            fallback_chain=("missing",),
        )

        run = executor.execute(
            Task(
                id="failed-run",
                description="coding task",
                category=TaskCategory.CODING,
            )
        )

        self.assertEqual(run.state, RunState.FAILED)
        self.assertFalse(run.result.success)
        self.assertEqual(run.error.error_type, "TaskExecutionError")
        self.assertEqual(run.error.message, run.result.error)
        self.assertEqual(run.events[-1].type, EventType.RUN_FAILED)

    def test_invalid_task_exception_becomes_structured_run_failure(self):
        executor = self.make_executor([StubAgent("mock")])

        run = executor.execute(Task(id=" ", description="invalid"))

        self.assertEqual(run.state, RunState.FAILED)
        self.assertEqual(run.error.error_type, "InvalidTaskError")
        self.assertIn("task id", run.error.message)
        self.assertEqual(run.events[-1].type, EventType.RUN_FAILED)


if __name__ == "__main__":
    unittest.main()
