"""Regression tests for the provider fallback chain (D2: it did not fall through).

Measured defect: with ``FORGE_PROVIDER_FALLBACK_CHAIN=radox,openrouter,mock`` and every
Radox key returning 402, the run ended with "Provider fallback chain exhausted: radox: ..."
and **openrouter was never tried**. Two causes had to be fixed:

* the API layer resolves the configured default into a provider name and passes it as an
  explicit selection, which the dispatcher treated as a pin (``allow_fallback`` is now
  explicit about that: a resolved *primary* keeps the configured chain available);
* every provider failure was treated as fall-through-worthy, so the chain had no notion
  of a *request* error. A provider-side failure (quota, credential, rate limit, transport)
  advances to the next provider; an invalid request does not, because every provider would
  reject the very same request.

These are offline: the providers are ordinary ``Provider`` doubles registered under the
real providers' names, so the production dispatcher, agent adapter and executor run
unchanged and nothing is spent.
"""

import unittest

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import (
    Provider,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRequest,
    ProviderResponse,
)
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.providers.config import ProviderConfig
from app.agents.providers.openai_compatible import (
    CompatibleConnectionError,
    CompatibleRateLimitError,
    CompatibleRequestError,
    CompatibleUnavailableError,
)
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.models import EventType, RunState, Task
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor

#: The chain the owner configured when the defect was measured.
CHAIN = ("openrouter", "mock")

#: The message the real adapter produces for a spent Radox balance (see radox.py): the
#: status and the reason are appended to the translated SDK message.
INSUFFICIENT_FUNDS = (
    "Radox API request failed (the account has insufficient funds or quota (HTTP 402); "
    "all 5 Radox API key account(s) are cooling down after repeated failures)"
)


class _FakeProvider(Provider):
    """A provider double that answers, or raises the error it was given."""

    def __init__(self, name: str, model: str, *, error: BaseException | None = None) -> None:
        super().__init__(
            ProviderConfig(
                provider_name=name,
                model_name=model,
                enabled=True,
                api_key_env_var=None,
            )
        )
        self.error = error
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output=f"answer from {self.provider_name}",
        )


def _radox(error: BaseException) -> _FakeProvider:
    return _FakeProvider("radox", "kimi-k3", error=error)


def _openrouter() -> _FakeProvider:
    # The ``:free`` model keeps OpenRouter at the free cost tier, so automatic routing
    # would permit it without a paid-provider flag.
    return _FakeProvider("openrouter", "cohere/north-mini-code:free")


def _mock() -> _FakeProvider:
    return _FakeProvider("mock", "mock")


class FallbackChainTests(unittest.TestCase):
    """A provider-side failure advances; a request error does not."""

    def _dispatch(self, providers, *, provider_name, allow_fallback=None, default="openrouter"):
        agents = AgentRegistry()
        provider_registry = ProviderRegistry()
        capabilities = ProviderCapabilitiesRegistry()
        for provider in providers:
            agents.register(ProviderAgent(provider))
            provider_registry.register(provider)
            capabilities.register_provider(provider)
        dispatcher = Dispatcher(
            agents,
            default_provider=default,
            fallback_chain=CHAIN,
            provider_registry=provider_registry,
            capabilities_registry=capabilities,
        )
        task = Task(id="fallback-task", description="write a small function")
        return dispatcher.dispatch(
            task, provider_name=provider_name, allow_fallback=allow_fallback
        )

    def test_01_a_402_on_the_primary_advances_to_the_next_provider(self) -> None:
        radox = _radox(CompatibleUnavailableError(INSUFFICIENT_FUNDS))
        openrouter = _openrouter()
        mock = _mock()

        result = self._dispatch(
            [radox, openrouter, mock], provider_name="radox", allow_fallback=True
        )

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.model_name, "cohere/north-mini-code:free")
        self.assertEqual(len(radox.requests), 1, "the primary must be tried first")
        self.assertEqual(len(openrouter.requests), 1)
        self.assertEqual(mock.requests, [], "the chain stops at the first success")

    def test_02_the_run_record_names_the_provider_that_served_it(self) -> None:
        radox = _radox(CompatibleUnavailableError(INSUFFICIENT_FUNDS))
        openrouter = _openrouter()
        mock = _mock()

        agents = AgentRegistry()
        provider_registry = ProviderRegistry()
        capabilities = ProviderCapabilitiesRegistry()
        for provider in (radox, openrouter, mock):
            agents.register(ProviderAgent(provider))
            provider_registry.register(provider)
            capabilities.register_provider(provider)
        orchestrator = Orchestrator(
            agents,
            default_provider="radox",
            fallback_chain=("openrouter", "mock"),
            provider_registry=provider_registry,
            capabilities_registry=capabilities,
        )

        run = RunExecutor(orchestrator).execute(
            Task(id="fallback-run", description="write a small function"),
            provider_name="radox",
            allow_fallback=True,
            run_id="run-fallback-probe",
        )

        self.assertEqual(run.state, RunState.COMPLETED, run.error)
        self.assertIsNotNone(run.result)
        self.assertEqual(run.result.provider, "openrouter")
        self.assertEqual(run.result.output, "answer from openrouter")
        # The fallback is visible in the run's own event trace: the failed primary, the
        # fallback edge, and the successful attempt that names the serving provider.
        fallback_events = [
            event for event in run.events if event.type is EventType.FALLBACK
        ]
        self.assertEqual(len(fallback_events), 1)
        self.assertEqual(fallback_events[0].data["source"], "radox")
        self.assertEqual(fallback_events[0].data["target"], "openrouter")
        served = [
            event
            for event in run.events
            if event.type is EventType.PROVIDER_RESULT
            and event.data.get("success") is True
        ]
        self.assertEqual([event.data["provider"] for event in served], ["openrouter"])

    def test_03_an_invalid_request_does_not_advance_the_chain(self) -> None:
        # A 400 from the provider: the payload/model id is wrong, so the next provider
        # would reject the identical request.
        radox = _radox(CompatibleRequestError("Radox API request failed"))
        openrouter = _openrouter()
        mock = _mock()

        result = self._dispatch(
            [radox, openrouter, mock], provider_name="radox", allow_fallback=True
        )

        self.assertFalse(result.success)
        self.assertEqual(len(radox.requests), 1)
        self.assertEqual(openrouter.requests, [], "a request error must not spend another account")
        self.assertEqual(mock.requests, [])
        self.assertIn("not advanced", result.error or "")
        self.assertIn("Radox API request failed", result.error or "")

    def test_04_a_missing_key_still_advances(self) -> None:
        radox = _radox(
            ProviderNotConfiguredError(
                "Set RADOX_API_KEY in the local environment to use radox"
            )
        )
        openrouter = _openrouter()

        result = self._dispatch(
            [radox, openrouter], provider_name="radox", allow_fallback=True
        )

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.provider, "openrouter")

    def test_05_transport_and_rate_limit_failures_advance(self) -> None:
        cases = (
            CompatibleConnectionError("Radox connection failed"),
            CompatibleRateLimitError("Radox rate limit exceeded"),
            CompatibleUnavailableError("Radox API request failed"),
            # An unknown ProviderError keeps the historical "advance" behaviour.
            ProviderError("Radox provider defect"),
        )
        for error in cases:
            with self.subTest(error=type(error).__name__):
                radox = _radox(error)
                openrouter = _openrouter()
                result = self._dispatch(
                    [radox, openrouter], provider_name="radox", allow_fallback=True
                )
                self.assertTrue(result.success, result.error)
                self.assertEqual(result.provider, "openrouter")

    def test_06_a_pinned_provider_never_falls_through(self) -> None:
        """A caller that names a provider itself keeps that pin (live defect (a))."""

        radox = _radox(CompatibleUnavailableError(INSUFFICIENT_FUNDS))
        openrouter = _openrouter()

        result = self._dispatch([radox, openrouter], provider_name="radox")

        self.assertFalse(result.success)
        self.assertEqual(openrouter.requests, [])
        self.assertIn("insufficient funds or quota", result.error or "")
        self.assertIn("HTTP 402", result.error or "")

    def test_07_the_automatic_route_still_uses_the_chain(self) -> None:
        """With no named provider the configured default is the primary, chain intact."""

        radox = _radox(CompatibleUnavailableError(INSUFFICIENT_FUNDS))
        openrouter = _openrouter()

        agents = AgentRegistry()
        provider_registry = ProviderRegistry()
        capabilities = ProviderCapabilitiesRegistry()
        for provider in (radox, openrouter):
            agents.register(ProviderAgent(provider))
            provider_registry.register(provider)
            capabilities.register_provider(provider)
        dispatcher = Dispatcher(
            agents,
            default_provider="openrouter",
            fallback_chain=("radox", "openrouter", "mock"),
            provider_registry=provider_registry,
            capabilities_registry=capabilities,
        )

        result = dispatcher.dispatch(Task(id="auto", description="write a small function"))

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.provider, "openrouter")


class FailoverEligibilityTests(unittest.TestCase):
    """The marker the chain reads is set on the error classes, not on message text."""

    def test_01_provider_side_errors_are_eligible(self) -> None:
        for error_type in (
            CompatibleConnectionError,
            CompatibleRateLimitError,
            CompatibleUnavailableError,
            ProviderNotConfiguredError,
            ProviderError,
        ):
            with self.subTest(error=error_type.__name__):
                self.assertTrue(error_type("boom").failover_eligible)

    def test_02_a_request_error_is_not(self) -> None:
        self.assertFalse(CompatibleRequestError("boom").failover_eligible)


if __name__ == "__main__":
    unittest.main()
