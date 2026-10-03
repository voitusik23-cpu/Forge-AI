"""Offline tests for bounded provider-neutral context assembly."""

import unittest
import json

from app.context import (
    ContextAssembler,
    ContextBudgetExceededError,
    ContextFreshness,
    ContextItem,
    ContextSource,
    ContextTrust,
)
from app.orchestrator.models import Task


class ContextAssemblerTests(unittest.TestCase):
    def test_task_is_present_as_user_task(self):
        context = ContextAssembler().assemble(
            Task(id="task-1", description="Build a small service"), "run-1"
        )

        self.assertEqual(context.run_id, "run-1")
        self.assertEqual(len(context.items), 1)
        self.assertEqual(context.items[0].content, "Build a small service")
        self.assertEqual(context.items[0].source, ContextSource.USER_TASK)

    def test_text_explicit_input_receives_untrusted_unknown_metadata(self):
        context = ContextAssembler().assemble(
            Task(id="task-1", description="Task"), "run-1", ["reference notes"]
        )
        item = context.items[1]

        self.assertEqual(item.source, ContextSource.EXPLICIT_INPUT)
        self.assertEqual(item.trust, ContextTrust.UNTRUSTED)
        self.assertEqual(item.freshness, ContextFreshness.UNKNOWN)

    def test_existing_task_context_is_assembled_and_included_in_the_budget(self):
        task = Task(id="task-1", description="Task", context={"notes": "prior context"})
        context = ContextAssembler().assemble(task, "run-1")

        task_context = context.items[1]
        self.assertEqual(task_context.kind, "task_context")
        self.assertEqual(task_context.source, ContextSource.USER_TASK)
        self.assertEqual(json.loads(task_context.content), task.context)
        with self.assertRaises(ContextBudgetExceededError):
            ContextAssembler(max_characters=10).assemble(task, "run-1")

    def test_context_item_metadata_is_preserved_but_source_is_enforced(self):
        supplied = ContextItem(
            id="reference-1",
            kind="document_excerpt",
            content="provided excerpt",
            source=ContextSource.SYSTEM,
            trust=ContextTrust.UNTRUSTED,
            freshness=ContextFreshness.CURRENT,
        )

        context = ContextAssembler().assemble(
            Task(id="task-1", description="Task"), "run-1", [supplied]
        )

        item = context.items[1]
        self.assertEqual(item.id, supplied.id)
        self.assertEqual(item.source, ContextSource.EXPLICIT_INPUT)
        self.assertEqual(item.trust, ContextTrust.UNTRUSTED)
        self.assertEqual(item.freshness, ContextFreshness.CURRENT)
        self.assertEqual(supplied.source, ContextSource.SYSTEM)

    def test_item_and_character_budgets_are_enforced(self):
        task = Task(id="task-1", description="task")
        with self.assertRaises(ContextBudgetExceededError):
            ContextAssembler(max_items=1).assemble(task, "run-1", ["extra"])
        with self.assertRaises(ContextBudgetExceededError):
            ContextAssembler(max_characters=3).assemble(task, "run-1")

    def test_duplicate_ids_and_invalid_input_are_rejected(self):
        item = ContextItem(
            id="task:task-1",
            kind="note",
            content="duplicate",
            source=ContextSource.EXPLICIT_INPUT,
            trust=ContextTrust.UNTRUSTED,
            freshness=ContextFreshness.UNKNOWN,
        )
        assembler = ContextAssembler()
        with self.assertRaisesRegex(ValueError, "IDs must be unique"):
            assembler.assemble(Task(id="task-1", description="Task"), "run-1", [item])
        with self.assertRaisesRegex(ValueError, "must be text or ContextItem"):
            assembler.assemble(Task(id="task-1", description="Task"), "run-1", [object()])


if __name__ == "__main__":
    unittest.main()
