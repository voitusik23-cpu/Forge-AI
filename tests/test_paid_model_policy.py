"""The paid-model policy: no run is billed without an explicit, named choice.

The owner was billed once by accident: a run went to
``openrouter / anthropic/claude-haiku-4.5`` while nothing in the request asked for
that provider or that model. Two defects combined to allow it:

1. the model was registered ``CHEAP`` even though its published rate is above
   zero, so automatic routing treated it as free to run;
2. nothing at the dispatch boundary asked "is this pair paid, and did the caller
   choose it?" before spending money.

These tests pin the fix, layer by layer:

* the registry: a priced model is ``PAID``, so automatic routing cannot select it
  while ``allow_paid_providers`` is false, and even when paid is allowed the free
  rows come first;
* the API service: a paid pair the request did not name is refused **before**
  dispatch with ``paid_model_requires_confirmation`` (409 over HTTP), and the fake
  provider receives nothing;
* the payloads: ``cost_tier`` and ``estimated_cost`` reach the run response and the
  models catalogue so a UI can show them.

Every provider here is a local fake -- no network call is made and no key is read.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import unittest

from fastapi.testclient import TestClient

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.agents.providers.model_registry import create_default_registry
from app.agents.providers.models import CostTier
from app.api.models import TaskRunRequest
from app.api.server import create_api_app
from app.api.service import ForgeApiService, PaidModelRequiresConfirmation
from app.config.settings import RuntimeSettings
from app.projects.service import ProjectService
from app.projects.store import ProjectStore
from app.runtime.bootstrap import create_runtime
from app.runtime.run_store import RunStore

#: The model the owner was billed for. It is priced (1.00 / 5.00 USD per 1M), so it
#: must never enter automatic routing.
BILLED_MODEL = "anthropic/claude-haiku-4.5"

#: A registry row that is genuinely free.
FREE_MODEL = "openrouter/free"

#: A paid registry row, reachable only by an explicit name.
PAID_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"


class _RecordingProvider(Provider):
    """Record every call, then answer. No socket, no key, no cost."""

    def __init__(self, provider_name: str, model_name: str) -> None:
        super().__init__(
            ProviderConfig(provider_name=provider_name, model_name=model_name)
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


class _PaidPolicyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-paid-policy"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        self.base = base
        self.run_root = base / "runs"
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

    def _service(
        self,
        *,
        provider: Provider,
        provider_name: str,
        default_provider: str = "mock",
        default_model: str = "",
        allow_paid_providers: bool = False,
    ) -> ForgeApiService:
        settings = RuntimeSettings(
            default_provider=default_provider,
            enabled_providers=(default_provider,),
            provider_fallback_chain=(),
            allow_paid_providers=allow_paid_providers,
            **({"default_model": default_model} if default_model else {}),
        )
        runtime = create_runtime(settings=settings)
        # The real provider instance stays registered for its configured model, but
        # the *agent* that would answer is this local double, so a dispatch that
        # should have been refused still cannot reach the network.
        runtime.agent_registry._agents[provider_name] = ProviderAgent(provider)
        return ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.run_root),
            authorized_project_id="paid-policy-project",
        )


class AutomaticRoutingTests(_PaidPolicyTestCase):
    """Automatic routing must never choose a paid pair while the flag is false."""

    def _paid_service(self, *, allow_paid_providers: bool = False, **kwargs):
        provider = _RecordingProvider("together", PAID_MODEL)
        service = self._service(
            provider=provider,
            provider_name="together",
            default_provider="together",
            default_model=PAID_MODEL,
            allow_paid_providers=allow_paid_providers,
            **kwargs,
        )
        return service, provider

    def test_01_an_unnamed_paid_run_is_refused_before_dispatch(self) -> None:
        service, provider = self._paid_service(allow_paid_providers=False)
        with self.assertRaises(PaidModelRequiresConfirmation) as caught:
            service.run_task_detailed(
                TaskRunRequest(description="посчитай домашние расходы", category="analysis")
            )
        refusal = caught.exception
        self.assertEqual(refusal.code, "paid_model_requires_confirmation")
        self.assertEqual(
            getattr(refusal, "code", ""), "paid_model_requires_confirmation"
        )
        # Nothing was dispatched: the fake provider was never called.
        self.assertEqual(provider.requests, [], "a paid run reached the provider")
        # The refusal says which pair would have been used, and nothing else.
        message = str(refusal)
        self.assertIn("together", message)
        self.assertIn(PAID_MODEL, message)
        self.assertIn("nothing was spent", message)

    def test_02_the_refusal_names_no_key(self) -> None:
        service, _provider = self._paid_service(allow_paid_providers=False)
        with self.assertRaises(PaidModelRequiresConfirmation) as caught:
            service.run_task_detailed(TaskRunRequest(description="x", category="analysis"))
        message = str(caught.exception)
        # No credential-shaped text of any kind.
        for token in ("API_KEY", "api_key", "sk-", "Bearer ", "secret", "token="):
            self.assertNotIn(token, message, f"the refusal leaked {token!r}")
        # And no environment value (the same check the catalogue test makes).
        for name, value in os.environ.items():
            if not value or len(value) < 8:
                continue
            if not any(
                marker in name.upper()
                for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
            ):
                continue
            self.assertNotIn(value, message, f"the refusal leaked {name}")

    def test_03_the_http_route_answers_409_with_the_fixed_code(self) -> None:
        service, provider = self._paid_service(allow_paid_providers=False)
        app = create_api_app(
            service,
            project_service=ProjectService(ProjectStore(root=self.base / "projects")),
        )
        client = TestClient(app)
        response = client.post(
            "/api/tasks/run", json={"description": "x", "category": "analysis"}
        )
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        detail = body.get("detail", body)
        self.assertEqual(
            detail.get("error_code"), "paid_model_requires_confirmation", body
        )
        self.assertIn("paid_model_requires_confirmation", json.dumps(body))
        self.assertEqual(provider.requests, [], "a paid run reached the provider")

    def test_04_automatic_model_selection_skips_priced_rows_and_prefers_free(self) -> None:
        registry = create_default_registry()
        # The shape automatic routing uses while ``allow_paid_providers`` is false.
        routable = registry.find_models(
            task_category="code", max_cost_tier=CostTier.CHEAP
        )
        self.assertTrue(routable)
        self.assertIs(routable[0].cost_tier, CostTier.FREE)
        self.assertNotIn(BILLED_MODEL, {model.model_id for model in routable})
        self.assertNotIn(
            CostTier.PAID, {model.cost_tier for model in routable}, "a paid row is routable"
        )
        # With paid allowed, the free/cheap rows still come first: a paid row is
        # only ever reached by naming it.
        everything = registry.find_models(task_category="code")
        self.assertIn(BILLED_MODEL, {model.model_id for model in everything})
        self.assertIs(everything[0].cost_tier, CostTier.FREE)
        # And the row that billed the owner is no longer tiered as free-to-run.
        billed = registry.get_model(BILLED_MODEL)
        self.assertIs(billed.cost_tier, CostTier.PAID)
        self.assertIsNotNone(billed.input_cost_per_1m)

    def test_05_a_free_model_still_runs_and_reports_its_tier(self) -> None:
        provider = _RecordingProvider("openrouter", FREE_MODEL)
        service = self._service(
            provider=provider,
            provider_name="openrouter",
            default_provider="mock",
        )
        result = service.run_task_detailed(
            TaskRunRequest(
                description="посчитай домашние расходы",
                category="analysis",
                provider_name="openrouter",
                model_name=FREE_MODEL,
            )
        )
        response = result.response
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.cost_tier, "FREE")
        self.assertEqual(len(provider.requests), 1)
        self.assertTrue(result.history_recorded, result.history_error)


class ExplicitPaidChoiceTests(_PaidPolicyTestCase):
    """A paid pair runs only when the operator allowed it and the caller named it."""

    def _paid_service(self, *, allow_paid_providers: bool):
        provider = _RecordingProvider("together", PAID_MODEL)
        service = self._service(
            provider=provider,
            provider_name="together",
            default_provider="together",
            default_model=PAID_MODEL,
            allow_paid_providers=allow_paid_providers,
        )
        return service, provider

    def test_10_an_explicitly_named_paid_model_proceeds(self) -> None:
        service, provider = self._paid_service(allow_paid_providers=True)
        result = service.run_task_detailed(
            TaskRunRequest(
                description="сгенерируй отчёт по расходам",
                category="analysis",
                provider_name="together",
                model_name=PAID_MODEL,
            )
        )
        response = result.response
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.provider_used, "together")
        self.assertEqual(response.model_used, PAID_MODEL)
        self.assertEqual(response.cost_tier, "PAID")
        self.assertEqual(len(provider.requests), 1, "the named paid model did not run")

    def test_11_an_explicitly_named_paid_model_is_refused_while_the_flag_is_off(self) -> None:
        service, provider = self._paid_service(allow_paid_providers=False)
        with self.assertRaises(PaidModelRequiresConfirmation) as caught:
            service.run_task_detailed(
                TaskRunRequest(
                    description="сгенерируй отчёт по расходам",
                    category="analysis",
                    provider_name="together",
                    model_name=PAID_MODEL,
                )
            )
        self.assertIn("paid providers are disabled", str(caught.exception))
        self.assertEqual(provider.requests, [])

    def test_12_naming_only_the_provider_is_not_an_explicit_paid_choice(self) -> None:
        service, provider = self._paid_service(allow_paid_providers=True)
        with self.assertRaises(PaidModelRequiresConfirmation):
            service.run_task_detailed(
                TaskRunRequest(
                    description="x",
                    category="analysis",
                    provider_name="together",
                )
            )
        self.assertEqual(provider.requests, [])


class CostPayloadTests(_PaidPolicyTestCase):
    """The tier and the price are published; neither is invented."""

    def test_20_the_models_payload_publishes_the_tier_and_the_unit_price(self) -> None:
        provider = _RecordingProvider("mock-v1", "mock-default")
        service = self._service(
            provider=provider, provider_name="mock", default_provider="mock"
        )
        catalogue = service.list_models_catalogue()
        rows = {model["model_id"]: model for model in catalogue["models"]}
        self.assertIn(BILLED_MODEL, rows)
        billed = rows[BILLED_MODEL]
        self.assertEqual(billed["cost_tier"], "PAID")
        self.assertEqual(billed["estimated_cost"], 1.0, billed)
        for model in catalogue["models"]:
            self.assertIn(model["cost_tier"], {"FREE", "CHEAP", "PAID"}, model)
            self.assertIn("estimated_cost", model)
            self.assertTrue(
                model["estimated_cost"] is None
                or isinstance(model["estimated_cost"], float),
                model,
            )
        # A free row reports no price rather than a fabricated one.
        for model in catalogue["models"]:
            if model["cost_tier"] == "FREE":
                self.assertIsNone(model["estimated_cost"], model)

    def test_21_the_run_response_publishes_the_tier(self) -> None:
        provider = _RecordingProvider("together", PAID_MODEL)
        service = self._service(
            provider=provider,
            provider_name="together",
            default_provider="together",
            default_model=PAID_MODEL,
            allow_paid_providers=True,
        )
        response = service.run_task_detailed(
            TaskRunRequest(
                description="x",
                category="analysis",
                provider_name="together",
                model_name=PAID_MODEL,
            )
        ).response
        self.assertEqual(response.cost_tier, "PAID")
        payload = response.to_dict()
        self.assertIn("cost_tier", payload)
        self.assertIn("estimated_cost", payload)
        # An unknown estimate is None -- never a zero pretending to be a measurement.
        self.assertTrue(
            payload["estimated_cost"] is None
            or isinstance(payload["estimated_cost"], float)
        )


if __name__ == "__main__":
    unittest.main()
