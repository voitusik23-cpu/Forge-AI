"""Offline tests for the Together AI OpenAI-compatible provider.

Together must integrate through the existing Provider interface, the shared
OpenAI-compatible adapter, declarative factory registration and declarative
capability metadata. No provider-specific branch is expected anywhere in Core.
"""

import ast
import pathlib
import unittest
from types import SimpleNamespace

from app.agents.mock_agent import MockAgent
from app.agents.providers.base import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRequest,
)
from app.agents.providers.capabilities import (
    CostTier,
    ProviderCapabilitiesRegistry,
    capabilities_for,
)
from app.agents.providers.config import ProviderConfig
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.openai_compatible import (
    CompatibleAuthenticationError,
    CompatibleRateLimitError,
    CompatibleRequestError,
    CompatibleResponseError,
    CompatibleTimeoutError,
)
from app.agents.providers.registry import ProviderRegistry
from app.agents.providers.together import TogetherProvider
from app.agents.registry import AgentRegistry
from app.config.secrets import SecretStore
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.models import Task
from app.runtime.bootstrap import create_runtime
from app.config.settings import RuntimeSettings

TOGETHER_BASE_URL = "https://api.together.ai/v1"
TOGETHER_KEY_VAR = "TOGETHER_API_KEY"
TOGETHER_PROVIDER_NAME = "together"


class FakeAuthenticationError(Exception):
    pass


class FakeRateLimitError(Exception):
    pass


class FakeAPITimeoutError(Exception):
    pass


class FakeAPIConnectionError(Exception):
    pass


class FakeAPIError(Exception):
    pass


FAKE_SDK = SimpleNamespace(
    AuthenticationError=FakeAuthenticationError,
    RateLimitError=FakeRateLimitError,
    APITimeoutError=FakeAPITimeoutError,
    APIConnectionError=FakeAPIConnectionError,
    APIError=FakeAPIError,
)


class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


def response_with(content="Together reply", model="together-test-model", usage=None):
    if usage is None:
        usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model=model,
        usage=usage,
    )


class TogetherProviderTests(unittest.TestCase):
    def make_provider(self, *, model="together-test-model", response=None, error=None):
        completions = FakeCompletions(response=response, error=error)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        provider = TogetherProvider(
            ProviderConfig(
                provider_name=TOGETHER_PROVIDER_NAME,
                model_name=model,
                api_key_env_var=TOGETHER_KEY_VAR,
            ),
            client=client,
            sdk_module=FAKE_SDK,
            secret_store=SecretStore(
                env_file="missing-together-test-env",
                environ={TOGETHER_KEY_VAR: "test-placeholder"},
            ),
        )
        return provider, completions

    # 1. registration
    def test_factory_registers_together(self) -> None:
        factory = ProviderFactory()
        self.assertIn("together", factory.list_providers())
        self.assertIsInstance(factory.create("together"), TogetherProvider)

    # 2. provider name
    def test_provider_name(self) -> None:
        provider = ProviderFactory().create("together")
        self.assertEqual(provider.provider_name, "together")
        self.assertEqual(TogetherProvider.PROVIDER_NAME, "together")

    # 3. API key environment variable
    def test_api_key_environment_variable(self) -> None:
        provider = ProviderFactory().create("together")
        self.assertEqual(provider.API_KEY_ENV_VAR, TOGETHER_KEY_VAR)
        self.assertEqual(provider.config.api_key_env_var, TOGETHER_KEY_VAR)

    # 4. base URL
    def test_base_url(self) -> None:
        provider = ProviderFactory().create("together")
        self.assertEqual(provider.BASE_URL, TOGETHER_BASE_URL)

    # 4b. the client is built from the resolved key and the Together base URL
    def test_sdk_client_uses_expected_key_and_base_url(self) -> None:
        completions = FakeCompletions(response=response_with())
        captured = {}

        def create_client(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(chat=SimpleNamespace(completions=completions))

        sdk = SimpleNamespace(OpenAI=create_client, **vars(FAKE_SDK))
        provider = TogetherProvider(
            ProviderConfig(provider_name="together", model_name="model-id"),
            sdk_module=sdk,
            secret_store=SecretStore(
                env_file="missing-together-test-env",
                environ={TOGETHER_KEY_VAR: "resolved-value"},
            ),
        )
        self.assertEqual(captured, {})
        provider.generate(ProviderRequest(prompt="hello"))
        self.assertEqual(
            captured, {"api_key": "resolved-value", "base_url": TOGETHER_BASE_URL}
        )

    # 5. successful generation through a fake client
    def test_successful_generation(self) -> None:
        provider, completions = self.make_provider(response=response_with())
        result = provider.generate(
            ProviderRequest(prompt="Summarize", context={"scope": "module"})
        )

        self.assertEqual(completions.kwargs["model"], "together-test-model")
        self.assertEqual(completions.kwargs["messages"][0]["role"], "user")
        self.assertIn('"scope": "module"', completions.kwargs["messages"][0]["content"])
        self.assertEqual(result.output, "Together reply")
        self.assertEqual(result.provider_name, "together")
        self.assertEqual(result.model_name, "together-test-model")
        self.assertEqual(result.usage.input_tokens, 7)
        self.assertEqual(result.usage.output_tokens, 3)
        self.assertIsNone(result.usage.estimated_cost)

    def test_explicit_model_override_is_used(self) -> None:
        provider, completions = self.make_provider(response=response_with(model="other"))
        result = provider.generate(
            ProviderRequest(prompt="hi", model_name="deepseek-ai/DeepSeek-V4-Flash-0731")
        )
        self.assertEqual(
            completions.kwargs["model"], "deepseek-ai/DeepSeek-V4-Flash-0731"
        )
        self.assertEqual(result.model_name, "other")

    # 6. malformed / empty response
    def test_empty_and_malformed_responses_fail_closed(self) -> None:
        cases = (
            ("empty content", response_with(content="")),
            ("whitespace content", response_with(content="   ")),
            ("no choices", SimpleNamespace(choices=[], model="m", usage=None)),
            ("missing message", SimpleNamespace(choices=[SimpleNamespace()], model="m")),
            (
                "malformed usage",
                SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="x"))],
                    model="m",
                    usage=SimpleNamespace(prompt_tokens="many", completion_tokens=None),
                ),
            ),
        )
        for label, response in cases:
            with self.subTest(case=label):
                provider, _ = self.make_provider(response=response)
                with self.assertRaises(CompatibleResponseError):
                    provider.generate(ProviderRequest(prompt="offline"))

    # 7-9. authentication, rate limit, timeout/connection and generic API errors
    def test_sdk_errors_are_sanitized(self) -> None:
        cases = (
            (FakeAuthenticationError("sensitive"), CompatibleAuthenticationError),
            (FakeRateLimitError("sensitive"), CompatibleRateLimitError),
            (FakeAPITimeoutError("sensitive"), CompatibleTimeoutError),
            (FakeAPIConnectionError("sensitive"), CompatibleRequestError),
            (FakeAPIError("sensitive"), CompatibleRequestError),
        )
        for sdk_error, expected in cases:
            with self.subTest(error=type(sdk_error).__name__):
                provider, _ = self.make_provider(error=sdk_error)
                with self.assertRaises(expected) as raised:
                    provider.generate(ProviderRequest(prompt="test"))
                self.assertNotIn("sensitive", str(raised.exception))

    def test_unexpected_errors_propagate(self) -> None:
        provider, _ = self.make_provider(error=ValueError("unexpected"))
        with self.assertRaisesRegex(ValueError, "unexpected"):
            provider.generate(ProviderRequest(prompt="test"))

    def test_missing_key_fails_without_calling_client(self) -> None:
        provider, completions = self.make_provider()
        provider.secret_store = SecretStore(
            env_file="missing-together-test-env", environ={}
        )
        with self.assertRaisesRegex(ProviderNotConfiguredError, TOGETHER_KEY_VAR):
            provider.generate(ProviderRequest(prompt="offline"))
        self.assertIsNone(completions.kwargs)

    def test_unconfigured_model_fails_closed(self) -> None:
        provider, completions = self.make_provider(model="unconfigured")
        with self.assertRaises(ProviderNotConfiguredError):
            provider.generate(ProviderRequest(prompt="offline"))
        self.assertIsNone(completions.kwargs)

    # 10. capability lookup
    def test_capability_lookup(self) -> None:
        factory = ProviderFactory()
        provider = factory.create(
            "together",
            config=ProviderConfig(
                provider_name="together",
                model_name="m",
                enabled=True,
                api_key_env_var=TOGETHER_KEY_VAR,
            ),
        )
        caps = capabilities_for(provider)
        self.assertEqual(caps.provider_name, "together")
        self.assertEqual(caps.api_key_env, TOGETHER_KEY_VAR)
        self.assertEqual(caps.cost_tier, CostTier.PAID)
        self.assertTrue(caps.supports_streaming)
        self.assertTrue(caps.supports_tools)
        self.assertTrue(caps.enabled_by_config)
        self.assertEqual(caps.task_categories, ("code", "analysis", "review"))

    def test_capabilities_registry_registers_together(self) -> None:
        registry = ProviderCapabilitiesRegistry()
        provider = ProviderFactory().create("together")
        registered = registry.register_provider(provider)
        self.assertEqual(registered.cost_tier, CostTier.PAID)
        self.assertEqual(registry.get("together").provider_name, "together")

    # 14. no credential leakage into provider-visible surfaces
    def test_no_credential_leakage_in_result_or_config(self) -> None:
        secret = "ghs_" + "A1b2C3d4E5f6G7h8I9j0" * 26
        completions = FakeCompletions(response=response_with())
        provider = TogetherProvider(
            ProviderConfig(
                provider_name="together",
                model_name="m",
                api_key_env_var=TOGETHER_KEY_VAR,
            ),
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            sdk_module=FAKE_SDK,
            secret_store=SecretStore(
                env_file="missing-together-test-env", environ={TOGETHER_KEY_VAR: secret}
            ),
        )
        result = provider.generate(ProviderRequest(prompt="test"))
        rendered = repr(result) + repr(provider.config) + repr(provider.provider_name)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, str(completions.kwargs))
        self.assertNotIn(secret, repr(provider.config))


class TogetherRoutingAndCostPolicyTests(unittest.TestCase):
    """Together participates only through capability metadata and cost policy."""

    def make_dispatcher(self, *, allow_paid=False, enabled=True, key_present=True):
        agents = AgentRegistry()
        providers = ProviderRegistry()
        capabilities = ProviderCapabilitiesRegistry()
        factory = ProviderFactory()

        agents.register(MockAgent(name="together"))
        key_env = factory.create("together").config.api_key_env_var
        environ = {key_env: "offline-placeholder"} if key_present else {}
        provider = factory.create(
            "together",
            config=ProviderConfig(
                provider_name="together",
                model_name="together-test-model",
                enabled=enabled,
                api_key_env_var=key_env,
            ),
            secret_store=SecretStore(
                env_file="missing-together-test-env", environ=environ
            ),
        )
        providers.register(provider)
        capabilities.register_provider(provider)
        dispatcher = Dispatcher(
            agents,
            provider_registry=providers,
            capabilities_registry=capabilities,
            allow_paid_providers=allow_paid,
        )
        return dispatcher

    def test_automatic_routing_is_not_used_when_paid_providers_disabled(self) -> None:
        dispatcher = self.make_dispatcher(allow_paid=False)
        decision = dispatcher._policy.decide(
            Task(id="t", description="write code"),
            "mock",
            capabilities=dispatcher._capabilities_registry,
            registered_provider_names=("together",),
            registered_agent_names=("together",),
            allow_paid_providers=False,
        )
        self.assertNotIn("together", decision.candidates)

    def test_automatic_routing_rejects_together_when_disabled_by_config(self) -> None:
        dispatcher = self.make_dispatcher(enabled=False)
        task = Task(id="t", description="write code")
        reason = dispatcher._automatic_route_rejection("together", task)
        self.assertEqual(reason, "provider is disabled by configuration")

    def test_automatic_routing_rejects_together_under_cost_policy(self) -> None:
        dispatcher = self.make_dispatcher(allow_paid=False)
        task = Task(id="t", description="write code")
        reason = dispatcher._automatic_route_rejection("together", task)
        self.assertEqual(reason, "paid provider is disabled by cost policy")

    def test_automatic_routing_requires_the_key_when_permitted(self) -> None:
        dispatcher = self.make_dispatcher(allow_paid=True, key_present=False)
        task = Task(id="t", description="write code")
        reason = dispatcher._automatic_route_rejection("together", task)
        self.assertEqual(reason, f"required key {TOGETHER_KEY_VAR} is not configured")

    def test_automatic_routing_admits_together_when_paid_permitted(self) -> None:
        dispatcher = self.make_dispatcher(allow_paid=True, key_present=True)
        task = Task(id="t", description="write code")
        self.assertIsNone(dispatcher._automatic_route_rejection("together", task))

    def test_explicit_selection_is_independent_of_automatic_cost_policy(self) -> None:
        """An explicit provider_name is honoured rather than filtered by cost policy."""
        dispatcher = self.make_dispatcher(allow_paid=False)
        result = dispatcher.dispatch(
            Task(id="t", description="write code"), provider_name="together"
        )
        # The mock agent registered under this name succeeds, proving the explicit
        # path reached execution instead of being rejected by the paid policy.
        self.assertTrue(result.success)
        self.assertEqual(result.agent, "together")

    def test_runtime_bootstrap_registers_together_without_special_casing(self) -> None:
        runtime = create_runtime(
            RuntimeSettings(enabled_providers=("mock", "together"))
        )
        self.assertIn("together", runtime.provider_registry.list_providers())
        capability = runtime.provider_capabilities.get("together")
        self.assertEqual(capability.cost_tier, CostTier.PAID)
        self.assertTrue(capability.enabled_by_config)
        self.assertIn("together", runtime.agent_registry.list_agents())


class TogetherArchitectureInvariantTests(unittest.TestCase):
    """Adding a provider must require only registry rows, never a Core branch."""

    CORE_MODULES = (
        "app/orchestrator/dispatcher.py",
        "app/orchestrator/orchestrator.py",
        "app/orchestrator/executor.py",
        "app/orchestrator/run.py",
        "app/execution/authorizer.py",
        "app/execution/policy.py",
        "app/execution/intent.py",
        "app/agents/providers/base.py",
        "app/agents/provider_agent.py",
        "app/config/settings.py",
    )

    def test_no_together_branch_in_core_modules(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for relative in self.CORE_MODULES:
            path = root / relative
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            # String/identifier comparisons against the provider name are the
            # thing to forbid; comments and docstrings are not code paths.
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value.strip().lower() == "together":
                        offenders.append(f"{relative}:{node.lineno}")
                if isinstance(node, ast.Name) and node.id.lower() == "together":
                    offenders.append(f"{relative}:{node.lineno}")
                if isinstance(node, ast.Attribute) and node.attr.lower() == "together":
                    offenders.append(f"{relative}:{node.lineno}")
        self.assertEqual(offenders, [], f"provider-specific branch found: {offenders}")

    def test_registry_rows_are_the_only_integration_points(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        mentions = {}
        for path in (root / "app").rglob("*.py"):
            if path.name == "together.py":
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            # Only real code references count: a string constant or an
            # identifier equal to the provider name. Prose in comments and
            # docstrings (for example the English word "together") is not an
            # integration point.
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value.strip().lower() == "together":
                        key = path.relative_to(root).as_posix()
                        mentions[key] = mentions.get(key, 0) + 1
        self.assertEqual(
            sorted(mentions),
            [
                "app/agents/providers/capabilities.py",
                "app/agents/providers/factory.py",
                "app/agents/providers/model_registry.py",
            ],
            f"unexpected integration points: {mentions}",
        )


if __name__ == "__main__":
    unittest.main()
