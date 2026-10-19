"""The model a caller names is the model the provider actually runs.

Regression for the defect measured on ``POST /api/tasks/run``: a request naming
``model_name`` was accepted and then silently ignored, because the field did not
exist on ``TaskRunRequest`` and the route did not forward it. The provider
therefore ran its configured model (``"unconfigured"`` when the deployment did
not set one), and every non-default model failed as
"<provider> model is not configured" even though the caller had named a model.

These tests assert the observable chain rather than an internal attribute: the
model recorded at the provider *call site*, the ``model_used`` the API reports,
and the honesty of the failures that remain. The double is an ordinary
``Provider`` registered under the real provider's name, so the production
dispatcher, executor, run scope and response construction all run unchanged.
"""

from __future__ import annotations

import unittest

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import (
    Provider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from app.agents.providers.config import ProviderConfig
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.config.settings import RuntimeSettings
from app.runtime.bootstrap import create_runtime


class _RecordingProvider(Provider):
    """Record every request handed to the provider, then answer with its model.

    ``configured_model`` is what the deployment's settings produced. It stays
    ``"unconfigured"`` in the override tests on purpose: that is the value the
    live defect surfaced, and a named model must win over it without any
    provider-specific code.
    """

    def __init__(self, configured_model: str = "unconfigured") -> None:
        super().__init__(
            ProviderConfig(provider_name="deepseek", model_name=configured_model)
        )
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        model = request.model_name or self.model_name
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=model,
            output=f"recorded answer from {model}",
        )


class _FailingProvider(_RecordingProvider):
    """A provider that fails for a reason of its own, not for a missing model."""

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        raise ProviderError("DeepSeek authentication failed")


class ModelOverrideDispatchTests(unittest.TestCase):
    """One named model per dispatch, with the configured default kept otherwise."""

    def _runtime(self):
        # Every provider is registered by composition; only ``deepseek`` is replaced
        # below, so an explicit ``provider_name="deepseek"`` reaches this double
        # through the real dispatcher and makes no network call.
        return create_runtime(
            settings=RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock",),
                provider_fallback_chain=(),
            )
        )

    def _service_with(self, provider: Provider) -> ForgeApiService:
        runtime = self._runtime()
        runtime.agent_registry._agents["deepseek"] = ProviderAgent(provider)
        return ForgeApiService(runtime=runtime, authorized_project_id="model-override")

    def test_01_the_requested_model_reaches_the_provider_call(self) -> None:
        provider = _RecordingProvider(configured_model="unconfigured")
        service = self._service_with(provider)

        outcome = service.run_task_detailed(
            TaskRunRequest(
                description="Answer in one word: does it work?",
                category="analysis",
                provider_name="deepseek",
                model_name="deepseek-chat",
            )
        )
        response = outcome.response

        self.assertEqual(len(provider.requests), 1, "the provider was not dispatched")
        # The call site is the assertion: this is the request the provider got.
        self.assertEqual(provider.requests[0].model_name, "deepseek-chat")
        self.assertTrue(response.success, response.error)
        self.assertIsNone(response.error)
        self.assertEqual(response.provider_used, "deepseek")
        self.assertEqual(response.model_used, "deepseek-chat")
        self.assertIn("deepseek-chat", response.output)

    def test_02_no_model_keeps_the_configured_default(self) -> None:
        provider = _RecordingProvider(configured_model="configured-default")
        service = self._service_with(provider)

        outcome = service.run_task_detailed(
            TaskRunRequest(
                description="Answer in one word: does it work?",
                category="analysis",
                provider_name="deepseek",
            )
        )
        response = outcome.response

        self.assertEqual(len(provider.requests), 1, "the provider was not dispatched")
        self.assertIsNone(
            provider.requests[0].model_name,
            "a request that names no model must not carry an override",
        )
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.model_used, "configured-default")

    def test_03_an_unknown_provider_still_fails_honestly(self) -> None:
        provider = _RecordingProvider()
        service = self._service_with(provider)

        outcome = service.run_task_detailed(
            TaskRunRequest(
                description="probe",
                category="analysis",
                provider_name="not-a-real-provider",
                model_name="deepseek-chat",
            )
        )
        response = outcome.response

        self.assertFalse(response.success)
        self.assertEqual(
            provider.requests, [], "an unknown provider must not be replaced by another"
        )
        self.assertIn("not-a-real-provider", response.error)
        self.assertIn("not registered", response.error)
        self.assertNotIn("model is not configured", response.error)

    def test_04_a_provider_failure_is_not_masked_as_a_missing_model(self) -> None:
        provider = _FailingProvider()
        service = self._service_with(provider)

        outcome = service.run_task_detailed(
            TaskRunRequest(
                description="probe",
                category="analysis",
                provider_name="deepseek",
                model_name="deepseek-chat",
            )
        )
        response = outcome.response

        # The override arrived (so the model cannot be what failed), and the
        # provider's own error is the one reported.
        self.assertEqual(provider.requests[0].model_name, "deepseek-chat")
        self.assertFalse(response.success)
        self.assertIn("Provider 'deepseek' failed", response.error)
        self.assertIn("authentication failed", response.error)
        self.assertNotIn("model is not configured", response.error)

    def test_05_the_http_route_forwards_the_model_name(self) -> None:
        """The defect was at the HTTP boundary: the body field was dropped there."""

        from fastapi.testclient import TestClient

        from app.api.server import create_api_app

        provider = _RecordingProvider(configured_model="unconfigured")
        service = self._service_with(provider)
        client = TestClient(create_api_app(service))

        response = client.post(
            "/api/tasks/run",
            json={
                "description": "Answer in one word: does it work?",
                "category": "analysis",
                "provider_name": "deepseek",
                "model_name": "deepseek-chat",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"], body)
        self.assertEqual(body["model_used"], "deepseek-chat")
        self.assertEqual(provider.requests[0].model_name, "deepseek-chat")


class RequestFingerprintTests(unittest.TestCase):
    """A different model under the same idempotency key is a different operation."""

    def test_a_named_model_changes_the_fingerprint_and_its_absence_does_not(self) -> None:
        from app.api.models import TaskRunRequest as Request

        base = dict(description="same text", category="analysis", provider_name="deepseek")
        plain = Request(**base)
        same_plain = Request(**base)
        modelled = Request(**base, model_name="deepseek-chat")
        other_model = Request(**base, model_name="deepseek-reasoner")

        fingerprint = ForgeApiService._request_fingerprint
        self.assertEqual(fingerprint(plain, "p"), fingerprint(same_plain, "p"))
        self.assertNotEqual(fingerprint(plain, "p"), fingerprint(modelled, "p"))
        self.assertNotEqual(fingerprint(modelled, "p"), fingerprint(other_model, "p"))


if __name__ == "__main__":
    unittest.main()
