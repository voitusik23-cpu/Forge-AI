"""Deterministic, read-only assembly of task and explicitly supplied context."""

import json
from dataclasses import replace
from typing import Iterable

from app.context.models import (
    ContextFreshness,
    ContextItem,
    ContextSource,
    ContextTrust,
    ExecutionContext,
)
from app.orchestrator.models import Task


class ContextAssemblyError(ValueError):
    """Raised when explicit context cannot be assembled safely."""


class ContextBudgetExceededError(ContextAssemblyError):
    """Raised when an explicit context set exceeds configured bounds."""

    def __init__(
        self,
        *,
        item_count: int,
        character_count: int,
        max_items: int,
        max_characters: int,
    ) -> None:
        self.item_count = item_count
        self.character_count = character_count
        self.max_items = max_items
        self.max_characters = max_characters
        super().__init__(
            "Execution context exceeds configured budget "
            f"(items {item_count}/{max_items}, characters "
            f"{character_count}/{max_characters})"
        )


class ContextAssembler:
    """Assemble only caller-provided content; never reads files or networks."""

    def __init__(self, *, max_items: int = 16, max_characters: int = 20_000) -> None:
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1:
            raise ValueError("max_items must be a positive integer")
        if (
            isinstance(max_characters, bool)
            or not isinstance(max_characters, int)
            or max_characters < 1
        ):
            raise ValueError("max_characters must be a positive integer")
        self.max_items = max_items
        self.max_characters = max_characters

    def assemble(
        self,
        task: Task,
        run_id: str,
        explicit_inputs: Iterable[str | ContextItem] = (),
    ) -> ExecutionContext:
        """Build a bounded context with caller inputs marked as explicit/untrusted."""
        if not isinstance(task, Task):
            raise ContextAssemblyError("task must be a Task instance")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ContextAssemblyError("run_id must not be empty")

        items = [
            ContextItem(
                id=f"task:{task.id}",
                kind="user_task",
                content=task.description,
                source=ContextSource.USER_TASK,
                trust=ContextTrust.TRUSTED,
                freshness=ContextFreshness.CURRENT,
            )
        ]
        if task.context:
            try:
                task_context_content = json.dumps(
                    task.context, ensure_ascii=False, sort_keys=True
                )
            except (TypeError, ValueError) as exc:
                raise ContextAssemblyError(
                    "task context must contain JSON-compatible values"
                ) from exc
            items.append(
                ContextItem(
                    id=f"task-context:{task.id}",
                    kind="task_context",
                    content=task_context_content,
                    source=ContextSource.USER_TASK,
                    trust=ContextTrust.UNTRUSTED,
                    freshness=ContextFreshness.CURRENT,
                )
            )
        character_count = sum(len(item.content) for item in items)
        self._check_budget(len(items), character_count)
        if isinstance(explicit_inputs, str):
            explicit_inputs = (explicit_inputs,)
        for index, value in enumerate(explicit_inputs, start=1):
            if isinstance(value, str):
                item = ContextItem(
                    id=f"input:{index}",
                    kind="explicit_input",
                    content=value,
                    source=ContextSource.EXPLICIT_INPUT,
                    trust=ContextTrust.UNTRUSTED,
                    freshness=ContextFreshness.UNKNOWN,
                )
            elif isinstance(value, ContextItem):
                # The caller supplied this value for this run; don't allow it to
                # claim SYSTEM provenance through the explicit-input channel.
                item = replace(value, source=ContextSource.EXPLICIT_INPUT)
            else:
                raise ContextAssemblyError(
                    "explicit inputs must be text or ContextItem values"
                )
            items.append(item)
            character_count += len(item.content)
            self._check_budget(len(items), character_count)
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ContextAssemblyError("context item IDs must be unique")
        return ExecutionContext(run_id=run_id, items=tuple(items))

    def _check_budget(self, item_count: int, character_count: int) -> None:
        if item_count > self.max_items or character_count > self.max_characters:
            raise ContextBudgetExceededError(
                item_count=item_count,
                character_count=character_count,
                max_items=self.max_items,
                max_characters=self.max_characters,
            )
