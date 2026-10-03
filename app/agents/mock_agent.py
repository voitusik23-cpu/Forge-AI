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
    ) -> None:
        self.name = name
        self.provider_name = name
        self._tool_invocations = tuple(tool_invocations)
        self._output = output

    def run(self, task: Task) -> TaskResult:
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
            tool_invocations=list(self._tool_invocations),
        )
