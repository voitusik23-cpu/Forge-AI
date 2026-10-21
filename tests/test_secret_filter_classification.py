"""Regression tests for the context security filter (BUG A: a word is not a secret).

Measured defect. The owner's own manual run ended after one event::

    Critical Tier 1 context item 'task:task-…' cannot fit … Reason:
    security_forbidden_content:secret

The provider was never called and every later stage stayed «ожидается». The cause was
not a credential. ``find_forbidden_value_content`` matched the bare substring
``secret`` -- the credential *noun* -- inside a user's normal prose about not adding
secrets to a project, and the same rule fired for ``password``, ``credentials`` and
``API_KEY=`` with an empty value. A word that names a credential is not a credential.

What these tests pin:

* safe prose, documentation and an ``.env.example`` with empty values pass;
* every secret-shaped VALUE is still refused, with the reason word it always had;
* the refusal never echoes the blocked fragment -- only a rule id, a length *bucket*
  and the location survive;
* a task that only *mentions* credentials reaches the provider.

No test in this file prints a generated token, and no test reaches the network.
"""

from __future__ import annotations

import json
import os
import pathlib
import secrets
import shutil
import string
import unittest

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.context.assembler import ContextAssembler
from app.context.models import ContextItem, ContextSourceType
from app.context.selector import (
    ContextBudgetPolicy,
    ContextSelector,
    CriticalContextUnfitError,
)
from app.context.validation import (
    find_forbidden_value_content,
    scan_value_content,
)
from app.config.settings import RuntimeSettings
from app.orchestrator.models import Task
from app.runtime.bootstrap import create_runtime
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The owner's short expense-tracker idea, as it was typed.
OWNER_IDEA = (
    "Хочу небольшое локальное веб-приложение для учёта домашних расходов: "
    "список трат, категории, месячный отчёт."
)

#: The prose that used to be refused: every sentence here *names* a credential and
#: carries none.
SAFE_PROSE = (
    OWNER_IDEA
    + "\n\nСекреты нельзя добавлять в проект, а API key, credentials и password "
    "в коде не нужны."
)

#: A documentation snippet: the assignment is real, its value is empty.
EMPTY_ENV_EXAMPLE = (
    "# .env.example — copy to .env and fill in\n"
    "API_KEY=\n"
    "SECRET_KEY=\n"
    "ACCESS_KEY=\n"
    "AUTH_TOKEN=\n"
    "PASSWORD=\n"
    "DB_PASSWORD=\n"
    "CREDENTIALS=\n"
)

#: A documentation snippet whose values are placeholders, not material.
PLACEHOLDER_ENV_EXAMPLE = (
    "API_KEY=your-key-here\n"
    "PASSWORD=your-password-here\n"
    "SECRET_KEY=<вставьте свой ключ>\n"
    "AUTH_TOKEN=${AUTH_TOKEN}\n"
)

_DIGITS = string.digits
_LETTERS = string.ascii_letters


def _random_run(length: int) -> str:
    """A generated, credential-shaped value. Never printed by any test here.

    Credential-shaped means what the classifier means by it: mixed case and at least
    four digits, so the value cannot be mistaken for an ordinary word.
    """

    alphabet = _LETTERS + _DIGITS
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        has_digits = sum(character.isdigit() for character in candidate) >= 4
        has_upper = any(character.isupper() for character in candidate)
        has_lower = any(character.islower() for character in candidate)
        if has_digits and has_upper and has_lower:
            return candidate


class _RefusedValueMixin:
    """Assertions about a refusal that must never carry the refused text."""

    def assert_no_echo(self, refused: str, rendered: str) -> None:
        # The assertion messages deliberately contain no part of ``refused``.
        self.assertFalse(refused in rendered, "the refusal echoed the refused value")
        self.assertFalse(
            refused[:8] in rendered,
            "the refusal echoed the first eight characters of the refused value",
        )


class SafeTextIsAdmissibleTests(unittest.TestCase):
    """Prose, documentation and empty assignments name no material, so they pass."""

    def test_01_credential_nouns_in_prose_are_not_secrets(self) -> None:
        for text in (
            "Секреты нельзя добавлять в проект",
            "Не хранить секреты в репозитории",
            "Здесь описан API key и порядок его выдачи",
            "Никаких credentials в коде",
            "Password хранить только в менеджере паролей",
            SAFE_PROSE,
        ):
            with self.subTest(text=text[:24]):
                self.assertIsNone(find_forbidden_value_content(text))
                self.assertIsNone(scan_value_content(text))

    def test_02_a_documentation_snippet_with_an_empty_value_passes(self) -> None:
        self.assertIsNone(find_forbidden_value_content("API_KEY="))
        self.assertIsNone(find_forbidden_value_content(EMPTY_ENV_EXAMPLE))
        self.assertIsNone(find_forbidden_value_content(PLACEHOLDER_ENV_EXAMPLE))

    def test_03_a_bearer_placeholder_and_an_example_prefix_pass(self) -> None:
        for text in (
            "Authorization: Bearer <ваш-токен>",
            "Authorization: Bearer $FORGE_TOKEN",
            "В документации пример вида sk-… без реального значения",
            "Ключ выглядит так: sk-<ваш ключ>",
        ):
            with self.subTest(text=text[:24]):
                self.assertIsNone(find_forbidden_value_content(text))

    def test_04_a_safe_task_is_not_refused_by_the_assembler(self) -> None:
        context = ContextAssembler().assemble(
            Task(id="safe-prose", description=SAFE_PROSE), "run-safe-prose"
        )

        self.assertEqual(len(context.items), 1)
        self.assertEqual(context.items[0].value, SAFE_PROSE)

    def test_05_an_empty_env_example_is_not_refused_by_the_selector(self) -> None:
        selector = ContextSelector(
            policy=ContextBudgetPolicy(
                total_context_window=32_768,
                max_output_tokens=4_096,
                safety_headroom_tokens=2_048,
            )
        )
        item = ContextItem(
            item_id="task:env-example",
            item_type="user_task",
            source_type=ContextSourceType.USER_TASK,
            value=EMPTY_ENV_EXAMPLE,
        )

        selected, report = selector.select([item])

        self.assertEqual([entry.item_id for entry in selected], ["task:env-example"])
        self.assertEqual(report.dropped_reasons, {})


class SecretMaterialIsStillRefusedTests(_RefusedValueMixin, unittest.TestCase):
    """Every secret-shaped VALUE keeps its refusal and its existing reason word."""

    def test_01_a_generated_provider_token_is_refused(self) -> None:
        value = _random_run(40)
        finding = scan_value_content("ключ sk-" + value)

        self.assertIsNotNone(finding)
        self.assertEqual(finding.reason, "api_key")
        self.assertEqual(finding.rule, "provider_key_prefix")
        self.assert_no_echo(value, json.dumps({"reason": finding.reason, "rule": finding.rule}))
        self.assertNotIn(finding.length_bucket, ("0", "1-7"))

    def test_02_a_bearer_credential_of_forty_plus_characters_is_refused(self) -> None:
        value = _random_run(40)
        finding = scan_value_content("Authorization: Bearer " + value)

        self.assertIsNotNone(finding)
        self.assertEqual(finding.reason, "token")
        self.assertEqual(finding.rule, "bearer_token")
        self.assert_no_echo(value, finding.describe("task_description"))

    def test_03_a_jwt_is_refused(self) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        finding = scan_value_content(jwt)

        self.assertIsNotNone(finding)
        self.assertEqual(finding.reason, "token")
        self.assertEqual(finding.rule, "jwt")
        self.assert_no_echo(jwt, finding.describe("task_description"))

    def test_04_a_github_and_a_slack_token_are_refused(self) -> None:
        for value, rule in (
            ("ghp_" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(36)), "provider_key_prefix"),
            ("xoxb-" + "".join(secrets.choice(string.digits + string.ascii_lowercase) for _ in range(24)), "provider_key_prefix"),
        ):
            with self.subTest(rule=rule):
                finding = scan_value_content("token " + value)
                self.assertIsNotNone(finding)
                self.assertEqual(finding.reason, "api_key")
                self.assertEqual(finding.rule, rule)

    def test_05_an_assigned_api_key_with_material_is_refused(self) -> None:
        value = _random_run(24)
        finding = scan_value_content("API_KEY=" + value)

        self.assertIsNotNone(finding)
        self.assertEqual(finding.reason, "api_key")
        self.assertEqual(finding.rule, "assigned_api_key")

    def test_06_a_password_assignment_with_a_value_is_refused(self) -> None:
        value = _random_run(12)
        finding = scan_value_content("password: " + value)

        self.assertIsNotNone(finding)
        self.assertEqual(finding.reason, "password")
        self.assertEqual(finding.rule, "password_assignment")

    def test_07_a_long_high_entropy_value_is_refused(self) -> None:
        value = _random_run(40)
        finding = scan_value_content("blob " + value)

        self.assertIsNotNone(finding)
        self.assertEqual(finding.reason, "secret")
        self.assertEqual(finding.rule, "high_entropy")
        self.assertEqual(finding.length_bucket, "40-64")
        self.assert_no_echo(value, finding.describe("task_description"))

    def test_08_a_structural_marker_is_still_refused(self) -> None:
        # The narrowing is scoped to the credential nouns: a raw stream pushed into a
        # context value is still refused, exactly as before.
        self.assertEqual(find_forbidden_value_content("the raw stdout of the process"), "stdout")
        self.assertEqual(
            find_forbidden_value_content("the token is rotated nightly"), "token"
        )


class RefusalDiagnosticTests(_RefusedValueMixin, unittest.TestCase):
    """The refusal stays safe to log and safe to show."""

    def _policy(self) -> ContextBudgetPolicy:
        return ContextBudgetPolicy(
            total_context_window=32_768,
            max_output_tokens=4_096,
            safety_headroom_tokens=2_048,
        )

    def test_01_the_selector_reports_the_reason_and_a_content_free_hint(self) -> None:
        value = _random_run(40)
        item = ContextItem(
            item_id="task:leak",
            item_type="user_task",
            source_type=ContextSourceType.USER_TASK,
            value="ключ sk-" + value,
        )

        selected, report = ContextSelector(policy=self._policy()).select(
            [item], raise_on_critical_unfit=False
        )

        self.assertEqual(selected, ())
        # The published reason word is exactly what it always was.
        self.assertEqual(
            report.dropped_reasons["task:leak"],
            "security_forbidden_content:api_key",
        )

    def test_02_the_raised_message_carries_rule_bucket_and_location(self) -> None:
        value = _random_run(40)
        description = "Задача: ключ sk-" + value

        with self.assertRaises(CriticalContextUnfitError) as raised:
            ContextAssembler().assemble(
                Task(id="leak", description=description), "run-leak"
            )

        message = str(raised.exception)
        self.assertIn("security_forbidden_content:api_key", message)
        self.assertIn("Текст содержит значение, похожее на секрет", message)
        self.assertIn("rule=provider_key_prefix", message)
        self.assertIn("length_bucket=", message)
        self.assertIn("location=task_description", message)
        self.assert_no_echo(value, message)

    def test_03_the_hint_for_high_entropy_names_the_rule_and_the_bucket(self) -> None:
        value = _random_run(40)
        finding = scan_value_content("blob " + value)

        self.assertIsNotNone(finding)
        rendered = finding.describe("task_description")
        self.assertIn("rule=high_entropy", rendered)
        self.assertIn("length_bucket=40-64", rendered)
        self.assertIn("location=task_description", rendered)
        self.assert_no_echo(value, rendered)


class _RecordingProvider(Provider):
    """A provider double that records its calls. No socket, no key, no cost."""

    def __init__(self) -> None:
        super().__init__(
            ProviderConfig(provider_name="deepseek", model_name="deepseek-chat")
        )
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=request.model_name or self.model_name,
            output="ok",
        )


class _ServiceCase(unittest.TestCase):
    """A service over a temporary run store, removed afterwards."""

    def setUp(self) -> None:
        base = (
            REPO_ROOT
            / ".forge"
            / "test-secret-filter"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        self.base = base
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.provider = _RecordingProvider()
        runtime = create_runtime(
            settings=RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock", "deepseek"),
                provider_fallback_chain=(),
                allow_paid_providers=False,
            ),
            test_mode=True,
        )
        runtime.agent_registry._agents["deepseek"] = ProviderAgent(self.provider)
        self.service = ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=base / "runs"),
            workspace=Workspace(REPO_ROOT),
            authorized_project_id="secret-filter-test-project",
        )

    def _run(self, description: str):
        return self.service.run_task_detailed(
            TaskRunRequest(
                description=description,
                category="code",
                provider_name="deepseek",
                model_name="deepseek-chat",
            )
        )


class ASafeTaskReachesTheProviderTests(_ServiceCase, _RefusedValueMixin):
    """The measurement that matters: the pipeline is no longer refused before dispatch."""

    def test_01_a_task_naming_credentials_reaches_the_provider(self) -> None:
        outcome = self.service.run_task_detailed(
            TaskRunRequest(
                description=SAFE_PROSE,
                category="code",
                provider_name="deepseek",
                model_name="deepseek-chat",
            )
        )
        response = outcome.response

        self.assertEqual(len(self.provider.requests), 1, response.error)
        self.assertTrue(response.success, response.error)
        self.assertNotIn("security_forbidden_content", str(response.error or ""))
        # The provider saw the user's own text, not a redacted or truncated copy.
        self.assertIn("учёта домашних расходов", self.provider.requests[0].prompt)

    def test_02_the_run_produced_more_than_one_event(self) -> None:
        outcome = self._run(SAFE_PROSE)

        record = self.service.get_run_record(outcome.response.run_id)
        self.assertGreater(record["event_count"], 1, record)

    def test_03_a_real_secret_is_still_refused_and_never_echoed(self) -> None:
        value = _random_run(40)
        response = self._run("Задача: ключ sk-" + value).response

        self.assertEqual(len(self.provider.requests), 0, "the provider was called")
        self.assertFalse(response.success)
        error = str(response.error or "")
        self.assertIn("security_forbidden_content:api_key", error)
        self.assertIn("Текст содержит значение, похожее на секрет", error)
        self.assertIn("rule=provider_key_prefix", error)
        self.assertIn("location=task_description", error)
        self.assert_no_echo(value, error)


if __name__ == "__main__":
    unittest.main()
