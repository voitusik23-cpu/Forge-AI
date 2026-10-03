"""Deterministic offline agent for provider-neutral execution examples."""

from collections.abc import Iterable

from app.orchestrator.models import Task, TaskResult
from app.tools.contracts import ToolInvocation
from app.usage import Usage


class MockAgent:
    """Return fixed output and optional explicitly configured tool proposals."""

    def __init__(
        self,
        name: str = "mock",
        *,
        tool_invocations: Iterable[ToolInvocation] = (),
        output: str | None = None,
        repeat_tool_invocations_after_results: bool = False,
    ) -> None:
        self.name = name
        self.provider_name = name
        self._tool_invocations = tuple(tool_invocations)
        self._output = output
        self._repeat_tool_invocations_after_results = repeat_tool_invocations_after_results
        self.received_tool_results = []

    def run(self, task: Task) -> TaskResult:
        returned_results = task.context.get("forge_tool_results", [])
        self.received_tool_results = (
            returned_results if isinstance(returned_results, list) else []
        )
        has_tool_results = bool(self.received_tool_results)
        return TaskResult(
            task_id=task.id,
            success=True,
            output=(
                self._output
                if self._output is not None
                else f"MockAgent processed task: {task.description}"
            ),
            usage=Usage(),
            provider=self.provider_name,
            agent=self.name,
            model_name="mock-agent",
            tool_invocations=(
                list(self._tool_invocations)
                if not has_tool_results or self._repeat_tool_invocations_after_results
                else []
            ),
        )
