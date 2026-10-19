"""Offline tests for the Radox AI OpenAI-compatible provider.

Radox must integrate through the existing Provider interface, the shared
OpenAI-compatible adapter, declarative factory registration and declarative
capability metadata -- no provider-specific branch anywhere in Core. No test in
this file opens a socket, reads a real ``.env``, or uses a real credential: the
transport is a fake object, and every key used here is a literal placeholder that
exists only inside ``os.environ`` for the duration of one ``patch.dict`` block.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.mock_agent import MockAgent
from app.agents.providers.base import (
    ProviderNotConfiguredError,
    ProviderRequest,
)
from app.agents.providers.capabilities import (
    CostTier,
    ProviderCapabilitiesRegistry,
    capabilities_for,
)
from app.agents.providers.config import ProviderConfig
from app.agents.providers.deepseek import DeepSeekProvider
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.mock import MockProvider
from app.agents.providers.model_registry import create_default_registry
from app.agents.providers.openai_compatible import (
    CompatibleAuthenticationError,
    CompatibleRequestError,
)
from app.agents.providers.radox import RadoxProvider
from app.agents.providers.radox_catalogue import CATALOGUE as SHIPPED_RADOX_ARCHIVE
from app.agents.providers.radox_catalogue import (
    CURATED_MODEL_IDS as SHIPPED_RADOX_CURATED_IDS,
)
from app.agents.providers.radox_catalogue import MODEL_IDS as SHIPPED_RADOX_MODEL_IDS
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.config.secrets import SecretStore
from app.config.settings import RuntimeSettings
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.models import Task
from app.runtime.bootstrap import create_runtime

RADOX_PROVIDER_NAME = "radox"
RADOX_BASE_URL = "https://radoxai.com/v1"
RADOX_KEY_VAR = "RADOX_API_KEY"

#: The owner's whole Radox AI catalogue (33 models), transcribed from the table
#: he supplied on 2026-10-18 -- deliberately NOT imported from the provider or
#: the registry, so a wrong entry in the shipped data cannot agree with a copy of
#: itself. This is the *archive* transcription: the advertised set is the curated
#: subset in ``RADOX_CURATED_IDS``.
#: ``(model_id, display_name, context_window, input $/1M, output $/1M)``.
RADOX_CATALOGUE = (
    ("gemma-2-2b", "Gemma 2 2B", 8192, 0.12, 0.12),
    ("deepseek-v4-flash-0731", "DeepSeek-V4-Flash-0731", 1048576, 0.12, 0.12),
    ("gpt-5.6-luna", "GPT-5.6 Luna", 1050000, 0.36, 0.36),
    ("deepseek-v4-pro-0813", "DeepSeek-V4-Pro-0813", 1048576, 0.55, 0.55),
    ("qwen3.8-27b", "Qwen3.8-27B", 262144, 0.57, 0.57),
    ("seed-2.1-turbo", "Seed 2.1 Turbo", 262144, 0.92, 0.92),
    ("seed-2.1-pro", "Seed 2.1 Pro", 262144, 1.15, 1.15),
    ("kimi-k2.6", "Kimi K2.6", 1000000, 1.22, 1.22),
    ("gemini-3.7-flash", "Gemini 3.7 Flash", 1048576, 1.24, 1.24),
    ("glm-5.3", "GLM-5.3", 1000000, 1.36, 1.36),
    ("glm-5.2", "GLM-5.2", 1000000, 1.36, 1.36),
    ("qwen3.7-max", "Qwen3.7 Max", 1000000, 1.76, 1.76),
    ("qwen3.8-max", "Qwen3.8 Max", 1000000, 1.76, 1.76),
    ("muse-spark-1.1", "Muse Spark 1.1", 1000000, 1.82, 1.82),
    ("deepseek-v4-pro-max", "DeepSeek-V4-Pro-Max", 1048576, 2.05, 2.05),
    ("gemini-3.6-flash", "Gemini 3.6 Flash", 1048576, 2.5, 2.5),
    ("grok-4.5", "Grok 4.5", 500000, 2.81, 2.81),
    ("grok-4.6", "Grok 4.6", 500000, 2.81, 2.81),
    ("claude-sonnet-5", "Claude Sonnet 5", 1000000, 3.32, 3.32),
    ("gpt-5.6-terra", "GPT-5.6 Terra", 1050000, 3.58, 3.58),
    ("gemini-3.1-pro", "Gemini 3.1 Pro", 1048576, 4.47, 4.47),
    ("kimi-k3", "Kimi K3", 1000000, 4.98, 4.98),
    ("claude-opus-5.5", "Claude Opus 5.5", 1000000, 8.3, 8.3),
    ("claude-opus-4.7", "Claude Opus 4.7", 1000000, 8.3, 8.3),
    ("claude-opus-4.6", "Claude Opus 4.6", 1000000, 8.3, 8.3),
    ("claude-opus-4.8", "Claude Opus 4.8", 1000000, 8.3, 8.3),
    ("claude-mythos-preview", "Claude Mythos Preview", 1000000, 8.3, 8.3),
    ("claude-opus-5", "Claude Opus 5", 1000000, 8.3, 8.3),
    ("gpt-5.5-pro", "GPT-5.5 Pro", 1050000, 8.95, 8.95),
    ("gpt-5.5", "GPT-5.5", 1050000, 8.95, 8.95),
    ("gpt-5.6-sol", "GPT-5.6 Sol", 1050000, 8.95, 8.95),
    ("claude-fable-5", "Claude Fable 5", 1000000, 16.61, 16.61),
    ("claude-fable-5.1", "Claude Fable 5.1", 1000000, 16.61, 16.61),
)
RADOX_CATALOGUE_BY_ID = {row[0]: row for row in RADOX_CATALOGUE}
RADOX_ARCHIVE_IDS = tuple(row[0] for row in RADOX_CATALOGUE)

#: The curated 10-model active set the owner kept on 2026-10-18: cheap bulk
#: work, one strong mid tier, and premium models for the biggest tasks.
#: Written out here independently of the shipped data, on purpose.
RADOX_CURATED_IDS = (
    "deepseek-v4-flash-0731",
    "gpt-5.6-luna",
    "qwen3.8-27b",
    "gemini-3.7-flash",
    "glm-5.3",
    "claude-sonnet-5",
    "kimi-k3",
    "claude-opus-5.5",
    "gpt-5.5-pro",
    "claude-fable-5.1",
)
RADOX_CURATED_CATALOGUE = tuple(
    RADOX_CATALOGUE_BY_ID[model_id] for model_id in RADOX_CURATED_IDS
)
#: Everything the owner pruned from the advertised set: kept in the archive.
RADOX_REMOVED_IDS = tuple(
    model_id for model_id in RADOX_ARCHIVE_IDS if model_id not in RADOX_CURATED_IDS
)

#: What the owner's table says, and the only provenance note allowed to stand in
#: for a measurement.
RADOX_SOURCE_NOTE = "owner-supplied Radox catalogue 2026-10-18"

#: A path that cannot exist, so ``SecretStore`` never falls back to a real file.
MISSING_ENV_FILE = "missing-radox-test-env"

#: Where this module's rotation/usage ledger lives.
#:
#: ``RadoxProvider`` advances and writes the ledger on every call, so a test that
#: generated with a placeholder key would otherwise count requests in the
#: repository's real ``.forge/radox-keys.json`` and make the owner's live counters
#: depend on how often the suite ran. The module points the ledger at its own
#: temporary directory for the duration of the run.
_LEDGER_DIRECTORY = tempfile.TemporaryDirectory()
_LEDGER_WAS_SET_BY_CALLER = "FORGE_RADOX_KEYS_PATH" in os.environ
if not _LEDGER_WAS_SET_BY_CALLER:
    os.environ["FORGE_RADOX_KEYS_PATH"] = os.path.join(
        _LEDGER_DIRECTORY.name, "radox-keys.json"
    )


def tearDownModule() -> None:
    if not _LEDGER_WAS_SET_BY_CALLER:
        os.environ.pop("FORGE_RADOX_KEYS_PATH", None)
    _LEDGER_DIRECTORY.cleanup()


class FakeAuthenticationError(Exception):
    """Stand-in for the SDK's 401 error class."""

    status_code = 401


class FakeAPIError(Exception):
    """Stand-in for the SDK's generic error class."""

    status_code = 500


class FakeInternalServerError(FakeAPIError):
    """Stand-in for the SDK's 500 error class (a subclass of its generic error)."""


FAKE_SDK = SimpleNamespace(
    AuthenticationError=FakeAuthenticationError,
    APIError=FakeAPIError,
)


class FakeCompletions:
    """The only transport this file ever reaches: it records the call."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


def response_with(content="Radox reply", model="kimi-k3", usage=None):
    if usage is None:
        usage = SimpleNamespace(prompt_tokens=9, completion_tokens=4)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model=model,
        usage=usage,
    )


class _NoSocket:
    """Fail loudly if anything in the block opens a socket."""

    def __enter__(self):
        self._patch = patch(
            "socket.socket",
            side_effect=AssertionError("this test must not open a socket"),
        )
        self._patch.start()
        return self

    def __exit__(self, *exc_info):
        self._patch.stop()
        return False


def _store(environ):
    """A SecretStore over an explicit mapping, never over the real ``.env``."""

    return SecretStore(env_file=MISSING_ENV_FILE, environ=environ)


class RadoxRegistrationTests(unittest.TestCase):
    """The provider is reachable by name and declares exactly its curated set."""

    def test_01_factory_registers_radox(self) -> None:
        factory = ProviderFactory()
        self.assertIn(RADOX_PROVIDER_NAME, factory.list_providers())
        self.assertIsInstance(factory.create(RADOX_PROVIDER_NAME), RadoxProvider)

    def test_02_provider_identity_and_credential_reference(self) -> None:
        provider = ProviderFactory().create(RADOX_PROVIDER_NAME)
        self.assertEqual(provider.provider_name, RADOX_PROVIDER_NAME)
        self.assertEqual(RadoxProvider.PROVIDER_NAME, RADOX_PROVIDER_NAME)
        self.assertEqual(provider.API_KEY_ENV_VAR, RADOX_KEY_VAR)
        self.assertEqual(provider.config.api_key_env_var, RADOX_KEY_VAR)
        self.assertEqual(provider.BASE_URL, RADOX_BASE_URL)
        # Declaring the reference must not enable the provider: enabling it is
        # the owner's decision in .env, never a code default.
        self.assertFalse(provider.config.enabled)

    def test_03_the_whole_curated_set_is_registered(self) -> None:
        registry = create_default_registry()
        models = registry.list_models(RADOX_PROVIDER_NAME)
        self.assertEqual(len(models), len(RADOX_CURATED_IDS))
        self.assertEqual(
            tuple(model.model_id for model in models),
            tuple(sorted(RADOX_CURATED_IDS)),
        )
        for model in models:
            self.assertEqual(model.provider_id, RADOX_PROVIDER_NAME)
            self.assertEqual(model.cost_tier, CostTier.PAID)
            self.assertTrue(model.display_name)
            self.assertGreater(model.context_window, 0)
            # The context window and the price are declared by the owner, not probed.
            self.assertIn("limits_source", model.metadata)

    def test_04_the_provider_declaration_matches_the_registry_rows(self) -> None:
        provider_info = create_default_registry().get_provider(RADOX_PROVIDER_NAME)
        self.assertEqual(provider_info.display_name, "Radox AI")
        self.assertEqual(provider_info.default_base_url, RADOX_BASE_URL)
        self.assertEqual(provider_info.default_secret_env, RADOX_KEY_VAR)
        self.assertFalse(provider_info.is_gateway)
        self.assertEqual(
            set(provider_info.supported_models), set(RADOX_CURATED_IDS)
        )
        # The adapter's own list is the second copy; this assertion is what keeps
        # the two from drifting apart.
        self.assertEqual(set(RadoxProvider.MODEL_IDS), set(RADOX_CURATED_IDS))

    def test_05_radox_appears_in_the_models_catalogue_route_payload(self) -> None:
        """``/api/models`` is served by ``list_models_catalogue``; assert its payload."""

        from app.api.service import ForgeApiService

        catalogue = ForgeApiService.list_models_catalogue(
            SimpleNamespace(_model_registry=create_default_registry())
        )
        providers = {item["provider_id"]: item for item in catalogue["providers"]}
        self.assertIn(RADOX_PROVIDER_NAME, providers)
        self.assertEqual(
            providers[RADOX_PROVIDER_NAME]["model_count"], len(RADOX_CURATED_IDS)
        )
        self.assertEqual(
            providers[RADOX_PROVIDER_NAME]["secret_env_name"], RADOX_KEY_VAR
        )
        self.assertTrue(providers[RADOX_PROVIDER_NAME]["has_base_url"])
        radox_models = [
            item
            for item in catalogue["models"]
            if item["provider_id"] == RADOX_PROVIDER_NAME
        ]
        self.assertEqual(
            {item["model_id"] for item in radox_models}, set(RADOX_CURATED_IDS)
        )
        for item in radox_models:
            # The catalogue renders the tier with ``str()``, i.e. "CostTier.PAID".
            self.assertIn("PAID", item["cost_tier"])

    def test_06_capabilities_are_declared_for_radox(self) -> None:
        provider = ProviderFactory().create(
            RADOX_PROVIDER_NAME,
            config=ProviderConfig(
                provider_name=RADOX_PROVIDER_NAME,
                model_name="kimi-k3",
                enabled=True,
                api_key_env_var=RADOX_KEY_VAR,
            ),
        )
        capabilities = capabilities_for(provider)
        self.assertEqual(capabilities.provider_name, RADOX_PROVIDER_NAME)
        self.assertEqual(capabilities.api_key_env, RADOX_KEY_VAR)
        self.assertEqual(capabilities.cost_tier, CostTier.PAID)
        self.assertTrue(capabilities.supports_streaming)
        self.assertTrue(capabilities.supports_tools)
        self.assertTrue(capabilities.enabled_by_config)
        self.assertEqual(capabilities.task_categories, ("code", "analysis", "review"))

        registry = ProviderCapabilitiesRegistry()
        registry.register_provider(provider)
        self.assertEqual(registry.get(RADOX_PROVIDER_NAME).cost_tier, CostTier.PAID)


class RadoxCatalogueTests(unittest.TestCase):
    """The curated set is advertised; the whole 33-model catalogue is archived."""

    def setUp(self) -> None:
        self.registry = create_default_registry()
        self.models = self.registry.list_models(RADOX_PROVIDER_NAME)
        self.by_id = {model.model_id: model for model in self.models}

    def test_27_the_provider_advertises_exactly_the_ten_curated_ids(self) -> None:
        self.assertEqual(len(RADOX_CURATED_IDS), 10)
        self.assertEqual(len(set(RADOX_CURATED_IDS)), 10)
        # The provider's list, in the order the owner curated the models.
        self.assertEqual(tuple(RadoxProvider.MODEL_IDS), RADOX_CURATED_IDS)
        # The provider descriptor and the registry rows are the same set.
        self.assertEqual(
            self.registry.get_provider(RADOX_PROVIDER_NAME).supported_models,
            RADOX_CURATED_IDS,
        )
        self.assertEqual(set(self.by_id), set(RADOX_CURATED_IDS))
        self.assertEqual(len(self.models), 10)
        # The five IDs that were registered before the catalogue arrived are not
        # all kept -- only the productive ones; every kept ID is advertised.
        for model_id in (
            "kimi-k3",
            "claude-opus-5.5",
            "qwen3.8-27b",
            "claude-sonnet-5",
            "deepseek-v4-flash-0731",
        ):
            self.assertIn(model_id, self.by_id)

    def test_28_every_kept_row_carries_the_table_context_and_price(self) -> None:
        for model_id, display_name, context, input_price, output_price in (
            RADOX_CURATED_CATALOGUE
        ):
            with self.subTest(model=model_id):
                model = self.by_id[model_id]
                self.assertEqual(model.display_name, display_name)
                self.assertEqual(model.context_window, context)
                self.assertEqual(model.input_cost_per_1m, input_price)
                self.assertEqual(model.output_cost_per_1m, output_price)
                self.assertEqual(model.cost_tier, CostTier.PAID)

    def test_29_representative_rows_match_the_table_verbatim(self) -> None:
        # "1.05M" is 1,050,000 and "262K" is 262,144 -- neither is a round million.
        self.assertEqual(self.by_id["gpt-5.6-luna"].context_window, 1050000)
        self.assertEqual(self.by_id["qwen3.8-27b"].context_window, 262144)
        self.assertEqual(self.by_id["kimi-k3"].context_window, 1000000)
        self.assertEqual(self.by_id["deepseek-v4-flash-0731"].context_window, 1048576)
        # The most expensive pair and the cheapest bulk model.
        self.assertEqual(self.by_id["claude-fable-5.1"].input_cost_per_1m, 16.61)
        self.assertEqual(self.by_id["gpt-5.5-pro"].input_cost_per_1m, 8.95)
        self.assertEqual(self.by_id["deepseek-v4-flash-0731"].input_cost_per_1m, 0.12)

        # Technical capabilities come from the table: DeepSeek-V4-Flash has tools
        # and reasoning but no vision, while Kimi K3 has reasoning, vision, tools
        # and json output.
        kimi = self.by_id["kimi-k3"]
        for capability in ("reasoning", "vision", "tool_calling", "streaming", "structured_output"):
            self.assertTrue(kimi.has_capability(capability), capability)
        # ``web`` has no ModelCapability member, so it must not appear as an
        # invented enum value anywhere in the registry row.
        self.assertFalse(kimi.has_capability("web"))
        flash = self.by_id["deepseek-v4-flash-0731"]
        self.assertTrue(flash.has_capability("reasoning"))
        self.assertTrue(flash.has_capability("tool_calling"))
        self.assertFalse(flash.has_capability("vision"))

    def test_30_no_row_presents_the_catalogue_as_probed_facts(self) -> None:
        for model in self.models:
            with self.subTest(model=model.model_id):
                metadata = dict(model.metadata)
                self.assertEqual(metadata["limits_source"], RADOX_SOURCE_NOTE)
                # Nothing in the provenance note claims a measurement.
                rendered = " ".join(str(value) for value in metadata.values()).lower()
                for claim in ("probe", "measured", "verified", "observed", "confirmed"):
                    self.assertNotIn(claim, rendered)

    def test_31_every_removed_model_is_archived_but_not_advertised(self) -> None:
        """Pruning the active set must not destroy the owner's data."""

        # The shipped archive still holds all 33 rows, matching the independent
        # transcription in this file id for id.
        self.assertEqual(len(SHIPPED_RADOX_ARCHIVE), 33)
        self.assertEqual(
            tuple(entry.model_id for entry in SHIPPED_RADOX_ARCHIVE),
            RADOX_ARCHIVE_IDS,
        )
        self.assertEqual(SHIPPED_RADOX_MODEL_IDS, SHIPPED_RADOX_CURATED_IDS)
        self.assertEqual(
            tuple(SHIPPED_RADOX_CURATED_IDS), RADOX_CURATED_IDS
        )
        # Every removed id is absent from the provider and the registry, and still
        # present in the shipped archive with its exact context and price.
        self.assertEqual(len(RADOX_REMOVED_IDS), 23)
        archived = {entry.model_id: entry for entry in SHIPPED_RADOX_ARCHIVE}
        for model_id in RADOX_REMOVED_IDS:
            with self.subTest(model=model_id):
                self.assertNotIn(model_id, RadoxProvider.MODEL_IDS)
                self.assertNotIn(model_id, self.by_id)
                self.assertFalse(
                    self.registry.has_model(model_id, provider_id=RADOX_PROVIDER_NAME)
                )
                self.assertIn(model_id, archived)
                _, _, context, input_price, output_price = RADOX_CATALOGUE_BY_ID[
                    model_id
                ]
                self.assertEqual(archived[model_id].context_window, context)
                self.assertEqual(archived[model_id].input_cost_per_1m, input_price)
                self.assertEqual(archived[model_id].output_cost_per_1m, output_price)

    def test_32_the_curated_set_follows_the_selection_rule(self) -> None:
        """The rationale, asserted: large context and a real price spread."""

        for model in self.models:
            with self.subTest(model=model.model_id):
                # Token-heavy workhorses only: nothing below 262K was kept.
                self.assertGreaterEqual(model.context_window, 262144)
                # The provenance note survives the pruning unchanged.
                self.assertEqual(
                    dict(model.metadata)["limits_source"], RADOX_SOURCE_NOTE
                )
        # Cheap bulk work is reachable ...
        self.assertTrue(
            any(model.input_cost_per_1m <= 0.6 for model in self.models)
        )
        # ... and so is a premium tier for the biggest tasks.
        self.assertTrue(
            any(model.input_cost_per_1m >= 8.0 for model in self.models)
        )


class RadoxCredentialTests(unittest.TestCase):
    """The key is resolved at call time and never stored, logged or echoed."""

    def make_provider(self, *, model="kimi-k3", response=None, error=None, environ=None):
        completions = FakeCompletions(response=response, error=error)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        provider = RadoxProvider(
            ProviderConfig(
                provider_name=RADOX_PROVIDER_NAME,
                model_name=model,
                api_key_env_var=RADOX_KEY_VAR,
            ),
            client=client,
            sdk_module=FAKE_SDK,
            secret_store=_store({} if environ is None else environ),
        )
        return provider, completions

    def test_07_missing_key_is_reported_honestly_and_never_calls_the_transport(
        self,
    ) -> None:
        provider, completions = self.make_provider()
        with patch.dict(os.environ, {RADOX_KEY_VAR: ""}, clear=False):
            provider.secret_store = _store(os.environ)
            with self.assertRaises(ProviderNotConfiguredError) as raised:
                provider.generate(ProviderRequest(prompt="offline"))

        message = str(raised.exception)
        # The message names the variable to set and the provider, and nothing else.
        self.assertIn(RADOX_KEY_VAR, message)
        self.assertIn(RADOX_PROVIDER_NAME, message)
        self.assertEqual(
            message,
            f"Set {RADOX_KEY_VAR} in the local environment to use {RADOX_PROVIDER_NAME}",
        )
        self.assertIsNone(completions.kwargs)
        self.assertEqual(completions.kwargs, None)

    def test_08_a_fake_key_builds_the_client_against_the_radox_base_url(self) -> None:
        fake_key = "test-key"
        completions = FakeCompletions(response=response_with())
        captured = {}

        def create_client(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(chat=SimpleNamespace(completions=completions))

        sdk = SimpleNamespace(OpenAI=create_client, **vars(FAKE_SDK))

        with patch.dict(os.environ, {RADOX_KEY_VAR: fake_key}, clear=False):
            provider = RadoxProvider(
                ProviderConfig(
                    provider_name=RADOX_PROVIDER_NAME,
                    model_name="kimi-k3",
                    api_key_env_var=RADOX_KEY_VAR,
                ),
                sdk_module=sdk,
                secret_store=_store(os.environ),
            )
            self.assertEqual(captured, {})
            with _NoSocket():
                result = provider.generate(ProviderRequest(prompt="hello"))

        self.assertEqual(
            captured, {"api_key": fake_key, "base_url": RADOX_BASE_URL}
        )
        self.assertEqual(result.provider_name, RADOX_PROVIDER_NAME)
        self.assertEqual(result.output, "Radox reply")

    def test_09_request_payload_and_authentication_are_correct(self) -> None:
        fake_key = "test-key"
        completions = FakeCompletions(response=response_with(model="claude-sonnet-5"))
        captured = {}

        def create_client(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(chat=SimpleNamespace(completions=completions))

        sdk = SimpleNamespace(OpenAI=create_client, **vars(FAKE_SDK))

        with patch.dict(os.environ, {RADOX_KEY_VAR: fake_key}, clear=False):
            provider = RadoxProvider(
                ProviderConfig(
                    provider_name=RADOX_PROVIDER_NAME,
                    model_name="claude-sonnet-5",
                    api_key_env_var=RADOX_KEY_VAR,
                ),
                sdk_module=sdk,
                secret_store=_store(os.environ),
            )
            with _NoSocket():
                result = provider.generate(
                    ProviderRequest(prompt="Summarize", context={"scope": "module"})
                )

        # Transport authentication: the key is handed to the SDK client exactly
        # once, as the bearer credential the OpenAI-compatible endpoint expects.
        self.assertEqual(captured["api_key"], fake_key)
        self.assertEqual(captured["base_url"], RADOX_BASE_URL)
        # Request payload: the model the caller asked for, one user message, and
        # the context appended to the prompt rather than silently dropped.
        self.assertEqual(completions.kwargs["model"], "claude-sonnet-5")
        self.assertEqual(len(completions.kwargs["messages"]), 1)
        self.assertEqual(completions.kwargs["messages"][0]["role"], "user")
        self.assertIn('"scope": "module"', completions.kwargs["messages"][0]["content"])
        self.assertEqual(
            set(completions.kwargs), {"model", "messages"}
        )
        self.assertEqual(result.model_name, "claude-sonnet-5")
        self.assertEqual(result.usage.input_tokens, 9)
        self.assertEqual(result.usage.output_tokens, 4)

    def test_10_per_request_model_override_is_honoured(self) -> None:
        provider, completions = self.make_provider(
            response=response_with(), environ={RADOX_KEY_VAR: "test-key"}
        )
        result = provider.generate(
            ProviderRequest(prompt="hi", model_name="gpt-5.5-pro")
        )
        self.assertEqual(completions.kwargs["model"], "gpt-5.5-pro")
        self.assertEqual(result.provider_name, RADOX_PROVIDER_NAME)

    def test_11_no_key_value_reaches_config_or_result(self) -> None:
        fake_key = "test-key"
        provider, completions = self.make_provider(
            response=response_with(), environ={RADOX_KEY_VAR: fake_key}
        )
        result = provider.generate(ProviderRequest(prompt="test"))
        rendered = repr(result) + repr(provider.config) + repr(provider.provider_name)
        self.assertNotIn(fake_key, rendered)
        self.assertNotIn(fake_key, repr(provider.config))
        # The key travels only in the transport call, which is the SDK's own
        # constructor argument -- never in the request payload.
        self.assertNotIn(fake_key, str(completions.kwargs))


class RadoxErrorSemanticsTests(unittest.TestCase):
    """401/500 map exactly as they do for the other compatible adapters."""

    def make_provider(self, *, provider_type=RadoxProvider, error=None, environ=None):
        completions = FakeCompletions(response=response_with(), error=error)
        provider = provider_type(
            ProviderConfig(
                provider_name=provider_type.PROVIDER_NAME,
                model_name="m",
                api_key_env_var=provider_type.API_KEY_ENV_VAR,
            ),
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            sdk_module=FAKE_SDK,
            secret_store=_store(
                {provider_type.API_KEY_ENV_VAR: "test-key"}
                if environ is None
                else environ
            ),
        )
        return provider

    def test_12_simulated_401_maps_to_authentication_failure(self) -> None:
        provider = self.make_provider(error=FakeAuthenticationError("sensitive 401 body"))
        with self.assertRaises(CompatibleAuthenticationError) as raised:
            provider.generate(ProviderRequest(prompt="test"))
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(str(raised.exception), "Radox authentication failed")

    def test_13_simulated_500_maps_to_a_request_failure(self) -> None:
        provider = self.make_provider(error=FakeInternalServerError("sensitive 500 body"))
        with self.assertRaises(CompatibleRequestError) as raised:
            provider.generate(ProviderRequest(prompt="test"))
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(str(raised.exception), "Radox API request failed")

    def test_14_the_mapping_is_identical_to_another_compatible_provider(self) -> None:
        cases = (FakeAuthenticationError("sensitive"), FakeInternalServerError("sensitive"))
        for error in cases:
            with self.subTest(error=type(error).__name__):
                outcomes = []
                for provider_type in (RadoxProvider, DeepSeekProvider):
                    provider = self.make_provider(provider_type=provider_type, error=error)
                    with self.assertRaises(Exception) as raised:  # noqa: B017 - compared below
                        provider.generate(ProviderRequest(prompt="test"))
                    outcomes.append(type(raised.exception))
                self.assertEqual(outcomes[0], outcomes[1])
                self.assertTrue(issubclass(outcomes[0], (CompatibleAuthenticationError, CompatibleRequestError)))

    def test_15_unexpected_errors_still_propagate(self) -> None:
        provider = self.make_provider(error=ValueError("unexpected"))
        with self.assertRaisesRegex(ValueError, "unexpected"):
            provider.generate(ProviderRequest(prompt="test"))


class RadoxCostGateTests(unittest.TestCase):
    """A paid provider must not become reachable while the flag is false."""

    def make_dispatcher(self, *, allow_paid=False, enabled=True, key_present=True):
        agents = AgentRegistry()
        providers = ProviderRegistry()
        capabilities = ProviderCapabilitiesRegistry()
        factory = ProviderFactory()

        agents.register(MockAgent(name=RADOX_PROVIDER_NAME))
        key_env = factory.create(RADOX_PROVIDER_NAME).config.api_key_env_var
        provider = factory.create(
            RADOX_PROVIDER_NAME,
            config=ProviderConfig(
                provider_name=RADOX_PROVIDER_NAME,
                model_name="kimi-k3",
                enabled=enabled,
                api_key_env_var=key_env,
            ),
            secret_store=_store(
                {key_env: "test-key"} if key_present else {}
            ),
        )
        providers.register(provider)
        capabilities.register_provider(provider)
        return Dispatcher(
            agents,
            provider_registry=providers,
            capabilities_registry=capabilities,
            allow_paid_providers=allow_paid,
        )

    def test_16_automatic_routing_excludes_radox_when_paid_is_disabled(self) -> None:
        dispatcher = self.make_dispatcher(allow_paid=False)
        decision = dispatcher._policy.decide(
            Task(id="t", description="write code"),
            "mock",
            capabilities=dispatcher._capabilities_registry,
            registered_provider_names=(RADOX_PROVIDER_NAME,),
            registered_agent_names=(RADOX_PROVIDER_NAME,),
            allow_paid_providers=False,
        )
        self.assertNotIn(RADOX_PROVIDER_NAME, decision.candidates)

    def test_17_automatic_routing_rejects_radox_under_cost_policy(self) -> None:
        dispatcher = self.make_dispatcher(allow_paid=False)
        reason = dispatcher._automatic_route_rejection(
            RADOX_PROVIDER_NAME, Task(id="t", description="write code")
        )
        self.assertEqual(reason, "paid provider is disabled by cost policy")

    def test_18_automatic_routing_rejects_radox_when_disabled_by_config(self) -> None:
        dispatcher = self.make_dispatcher(enabled=False)
        reason = dispatcher._automatic_route_rejection(
            RADOX_PROVIDER_NAME, Task(id="t", description="write code")
        )
        self.assertEqual(reason, "provider is disabled by configuration")

    def test_19_automatic_routing_requires_the_key_even_when_paid_is_permitted(
        self,
    ) -> None:
        dispatcher = self.make_dispatcher(allow_paid=True, key_present=False)
        reason = dispatcher._automatic_route_rejection(
            RADOX_PROVIDER_NAME, Task(id="t", description="write code")
        )
        self.assertEqual(reason, f"required key {RADOX_KEY_VAR} is not configured")

    def test_20_automatic_routing_admits_radox_only_when_paid_is_permitted(self) -> None:
        dispatcher = self.make_dispatcher(allow_paid=True, key_present=True)
        self.assertIsNone(
            dispatcher._automatic_route_rejection(
                RADOX_PROVIDER_NAME, Task(id="t", description="write code")
            )
        )


class RadoxDefaultsAndOfflinePathTests(unittest.TestCase):
    """Defaults are untouched and the offline path behaves exactly as before."""

    def test_21_settings_defaults_do_not_enable_radox_or_paid_providers(self) -> None:
        defaults = RuntimeSettings()
        self.assertEqual(defaults.enabled_providers, ("mock", "openrouter"))
        self.assertFalse(defaults.allow_paid_providers)
        self.assertEqual(defaults.default_provider, "mock")
        self.assertNotIn(RADOX_PROVIDER_NAME, defaults.enabled_providers)

    def test_22_composition_constructs_radox_disabled_by_default(self) -> None:
        runtime = create_runtime(
            RuntimeSettings(enabled_providers=("mock", "openrouter"))
        )
        self.assertIn(RADOX_PROVIDER_NAME, runtime.provider_registry.list_providers())
        self.assertFalse(
            runtime.provider_registry.get(RADOX_PROVIDER_NAME).config.enabled
        )
        self.assertEqual(
            runtime.provider_capabilities.get(RADOX_PROVIDER_NAME).cost_tier,
            CostTier.PAID,
        )
        self.assertFalse(runtime.settings.allow_paid_providers)

    def test_23_radox_can_be_enabled_by_configuration_alone(self) -> None:
        runtime = create_runtime(
            RuntimeSettings(enabled_providers=("mock", RADOX_PROVIDER_NAME))
        )
        self.assertTrue(
            runtime.provider_registry.get(RADOX_PROVIDER_NAME).config.enabled
        )
        self.assertTrue(
            runtime.provider_capabilities.get(RADOX_PROVIDER_NAME).enabled_by_config
        )
        # Still gated: enabling the provider does not imply permission to spend.
        self.assertFalse(runtime.settings.allow_paid_providers)

    def test_24_the_offline_mock_path_is_unchanged(self) -> None:
        request = ProviderRequest(prompt="summarize this", context={"a": 1})
        first = MockProvider().generate(request)
        second = MockProvider().generate(request)
        self.assertEqual(first, second)
        self.assertEqual(first.provider_name, "mock")
        self.assertEqual(first.output, "MockProvider response: summarize this")

        runtime = create_runtime(test_mode=True)
        self.assertEqual(runtime.settings.enabled_providers, ("mock",))
        self.assertEqual(runtime.settings.default_provider, "mock")
        self.assertEqual(runtime.provider_registry.list_providers(), ["mock"])
        result = runtime.orchestrator.dispatch(
            Task(id="offline", description="review a small change"), agent_name="mock"
        )
        self.assertTrue(result.success)

    def test_25_radox_is_paid_so_automatic_routing_cannot_reach_it_offline(self) -> None:
        """The default composition keeps exactly the one offline provider working."""

        runtime = create_runtime(test_mode=True)
        self.assertNotIn(
            RADOX_PROVIDER_NAME, runtime.provider_registry.list_providers()
        )
        result = runtime.orchestrator.dispatch(
            Task(id="offline-2", description="write code")
        )
        self.assertTrue(result.success)
        self.assertEqual(result.agent, "mock")

    def test_26_radox_appears_in_the_provider_balance_report_without_inventing_money(
        self,
    ) -> None:
        """``/api/providers/balance`` names every canonical provider, Radox included.

        Radox publishes no balance through its OpenAI-compatible API, and this
        project establishes no such endpoint, so the honest answer is "no money
        field" rather than a zero. The probe is given an empty secret store, so
        it cannot dial anything, and a socket guard proves that.
        """

        from app.dashboard.provider_balance import (
            STATUS_UNSUPPORTED,
            ProviderBalanceProbe,
        )

        with _NoSocket():
            reports = ProviderBalanceProbe(secret_store=_store({})).reports()

        self.assertIn(RADOX_PROVIDER_NAME, reports)
        report = reports[RADOX_PROVIDER_NAME]
        self.assertFalse(report.supported)
        self.assertEqual(report.connection_status, STATUS_UNSUPPORTED)
        self.assertIsNone(report.available_minor)
        self.assertIsNone(report.usage_minor)
        self.assertIn("balance", report.error.lower())


if __name__ == "__main__":
    unittest.main()
