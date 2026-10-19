"""Regression tests for the context security filter (D3: the word "token").

Measured defect: a task description that named ``tokens_in``/``tokens_out`` was rejected
with ``security_forbidden_content:token`` and the run never reached a provider. Naming a
*field* is normal engineering text, not secret material, so the value scanner now ignores
"token" when every occurrence belongs to a recognised field name. Real secret material is
refused exactly as before, with the same reasons, so a genuine positive keeps its existing
message.
"""

import unittest

from app.context.assembler import ContextAssembler
from app.context.models import ContextItem, ContextSourceType
from app.context.selector import (
    ContextBudgetPolicy,
    ContextSelector,
    CriticalContextUnfitError,
)
from app.context.validation import find_forbidden_value_content
from app.orchestrator.models import Task

#: A realistic brief: it *describes* token fields and carries no credential.
ENGINEERING_BRIEF = (
    "Задача: посчитать tokens_in и tokens_out для каждого запроса, "
    "лимит max_tokens = 4096, token_usage записывать в отчёт, "
    "токенизатор не менять. token_count считать по input_tokens + output_tokens."
)

#: A credential-shaped value: mixed case, digits, forty-odd characters.
LONG_RANDOM_SECRET = "aB3dE9fG2hJ7kL4mN8pQ1rS6tU0vW5xY2zA4cB7d"

SECRET_CASES = (
    ("api_key", "key: sk-live-" + "a1b2c3d4" * 5),
    ("token", "Authorization: Bearer " + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2"),
    ("api_key", "use ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"),
    ("api_key", "API_KEY=value0123456789abcdef"),
    (
        "token",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ),
    ("secret", "random blob " + LONG_RANDOM_SECRET),
)


class TokenFieldIsNotASecretTests(unittest.TestCase):
    """Describing a token *field* in the operator's own instruction is allowed."""

    def test_01_a_field_description_has_no_forbidden_marker(self) -> None:
        self.assertIsNone(find_forbidden_value_content(ENGINEERING_BRIEF))

    def test_02_the_selector_keeps_the_item(self) -> None:
        selector = ContextSelector(
            policy=ContextBudgetPolicy(
                total_context_window=32_768,
                max_output_tokens=4_096,
                safety_headroom_tokens=2_048,
            )
        )
        item = ContextItem(
            item_id="task:token-fields",
            item_type="user_task",
            source_type=ContextSourceType.USER_TASK,
            value=ENGINEERING_BRIEF,
        )

        selected, report = selector.select([item])

        self.assertEqual([entry.item_id for entry in selected], ["task:token-fields"])
        self.assertNotIn(
            "task:token-fields",
            report.dropped_reasons,
            f"the prompt was refused: {report.dropped_reasons}",
        )

    def test_03_a_task_naming_token_fields_reaches_context_assembly(self) -> None:
        """The exact shape of the defect: the Tier 1 task item must not be dropped."""

        context = ContextAssembler().assemble(
            Task(id="token-fields", description=ENGINEERING_BRIEF), "run-token-fields"
        )

        self.assertEqual(len(context.items), 1)
        self.assertEqual(context.items[0].value, ENGINEERING_BRIEF)

    def test_04_a_bare_token_word_is_still_refused(self) -> None:
        """Only the field-name form is relaxed; secret-shaped prose is not."""

        self.assertEqual(find_forbidden_value_content("the token is rotated nightly"), "token")


class SecretMaterialIsStillRefusedTests(unittest.TestCase):
    """Real secret material keeps its existing refusal reason and message."""

    def test_01_each_material_shape_is_refused_with_its_reason(self) -> None:
        for expected_reason, text in SECRET_CASES:
            with self.subTest(reason=expected_reason):
                self.assertEqual(find_forbidden_value_content(text), expected_reason)

    def test_02_the_selector_reports_the_existing_reason(self) -> None:
        policy = ContextBudgetPolicy(
            total_context_window=32_768,
            max_output_tokens=4_096,
            safety_headroom_tokens=2_048,
        )
        selector = ContextSelector(policy=policy)
        for expected_reason, text in SECRET_CASES:
            with self.subTest(reason=expected_reason):
                item = ContextItem(
                    item_id="task:leak",
                    item_type="user_task",
                    source_type=ContextSourceType.USER_TASK,
                    value=text,
                )
                selected, report = selector.select([item], raise_on_critical_unfit=False)

                self.assertEqual(selected, ())
                self.assertEqual(
                    report.dropped_reasons["task:leak"],
                    f"security_forbidden_content:{expected_reason}",
                )

    def test_03_context_assembly_still_fails_closed(self) -> None:
        for expected_reason, text in SECRET_CASES:
            with self.subTest(reason=expected_reason):
                with self.assertRaises(CriticalContextUnfitError) as raised:
                    ContextAssembler().assemble(
                        Task(id="leak", description=text), "run-leak"
                    )
                self.assertIn(
                    f"security_forbidden_content:{expected_reason}",
                    str(raised.exception),
                )


if __name__ == "__main__":
    unittest.main()
