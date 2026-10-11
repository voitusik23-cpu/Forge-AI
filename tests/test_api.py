"""Tests for Forge API layer."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.api.client import ForgeApiClient
from app.api.models import TaskRunRequest
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from offline_runtime import offline_runtime


class ApiTests(unittest.TestCase):
    """Verify Forge API endpoints, client abstraction, and security."""

    def setUp(self) -> None:
        patcher = patch("app.api.service.create_runtime", offline_runtime)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = ForgeApiService()
        self.client = ForgeApiClient(api_service=self.service)

    def test_01_health_endpoint(self) -> None:
        health = self.client.check_health()
        self.assertEqual(health.status, "ok")
        self.assertEqual(health.core_status, "connected")
        self.assertEqual(health.version, "0.1.0")
        self.assertTrue(len(health.timestamp) > 0)

    def test_02_system_status(self) -> None:
        status = self.client.get_system_status()
        self.assertEqual(status.core, "connected")
        self.assertEqual(status.api, "connected")
        self.assertIn(status.provider_status, ("READY", "DEGRADED"))
        self.assertTrue(status.providers_total >= 1)

    def test_03_projects_listing(self) -> None:
        projects = self.client.list_projects()
        self.assertTrue(len(projects) >= 1)
        self.assertEqual(projects[0].project_id, "default")
        self.assertEqual(projects[0].status, "active")

    def test_04_agents_listing(self) -> None:
        agents = self.client.list_agents()
        self.assertTrue(len(agents) >= 1)
        agent_names = [a.name for a in agents]
        self.assertTrue(any("mock" in name or "deepseek" in name or "google" in name for name in agent_names))

    def test_05_dashboard_report_via_api(self) -> None:
        report = self.client.get_dashboard()
        self.assertTrue(len(report.providers) >= 1)
        self.assertIsNotNone(report.all_time_totals)

    def test_06_dashboard_refresh(self) -> None:
        res = self.client.refresh_dashboard()
        self.assertIn("refreshed_at", res)
        self.assertTrue(res["providers_checked"] >= 1)

    def test_07_task_run_via_api(self) -> None:
        res = self.client.run_task(
            description="Create a hello world output",
            category="code",
        )
        self.assertTrue(res.run_id.startswith("run-api-"))
        self.assertTrue(res.task_id.startswith("task-"))
        self.assertEqual(res.state, "COMPLETED")
        self.assertTrue(res.success)
        self.assertTrue(len(res.output) > 0)
        self.assertIsNone(res.error)
        # The offline provider is the one that ran, and it is provable from its own
        # response text. `self.setUp` restricts the runtime to mock, so this assertion
        # also documents that the test cannot reach an external provider.
        self.assertIn("MockProvider response:", res.output)
        # And the response reports the provider that actually ran. This is the value a
        # caller uses to attribute work and cost, so it must come from recorded attempt
        # usage rather than from configuration.
        self.assertEqual(res.provider_used, "mock")
        self.assertEqual(res.model_used, "mock")

    def test_11_service_and_runtime_share_one_fabric(self) -> None:
        """The service must not keep a second fabric the dispatcher never records into.

        Measured before this was fixed: the dispatcher held `fabric=None`, so
        `record_usage` was skipped entirely and `provider_used` read an empty attempt
        list. Asserting object identity is what proves the accounting is reachable, and it
        covers BOTH construction paths -- a service-built runtime and an injected one.
        """

        # The path every other test in this module uses: an injected runtime.
        injected = self.service.runtime.orchestrator._dispatcher._fabric
        self.assertIs(injected, self.service.fabric)

        # The path production uses: the service builds its own runtime.
        from app.api.service import ForgeApiService as Service

        built = Service()
        self.assertIs(built.runtime.orchestrator._dispatcher._fabric, built.fabric)

    def test_12_accounting_records_every_attempt_once(self) -> None:
        """One record per run, one attempt entry per attempted provider.

        The isolated runtime disables every live provider, so the policy's preferences are
        each rejected and `mock` succeeds as a fallback. The assertions are on the recorded
        facts: how many attempts, which one succeeded, and that it is marked as a fallback.
        """

        res = self.client.run_task(description="accounting probe", category="code")
        self.assertTrue(res.success)

        records = self.service.fabric.get_all_run_accounting()
        matching = [record for record in records if record.run_id == res.run_id]
        self.assertEqual(len(matching), 1, "exactly one accounting record per run")
        record = matching[0]

        self.assertGreaterEqual(len(record.attempt_records), 1)
        successes = [a for a in record.attempt_records if a.success]
        self.assertEqual(len(successes), 1, "exactly one attempt succeeded")
        self.assertEqual(successes[0].provider_name, "mock")
        self.assertEqual(successes[0].model_name, "mock")
        # `mock` is not the policy's first preference for `code`, so it ran as a fallback.
        self.assertTrue(successes[0].is_fallback)
        self.assertTrue(record.attempt_records[0].is_fallback is False)
        # The last attempt is the successful one, which is what provider_used reads.
        self.assertEqual(record.attempt_records[-1].provider_name, res.provider_used)

    def test_13_a_failed_run_is_attributed_to_the_terminal_result(self) -> None:
        """Attribution must come from the terminal result, not the last attempt.

        With no provider able to succeed, the dispatcher tries every candidate and returns
        a failure naming the **preferred** provider. The last attempt record names the last
        candidate it happened to try, which is a different provider. Reading attribution
        from the attempt list therefore misattributed a failed run; the assertion below
        pins the two fields to the terminal result and shows they differ from the attempt
        tail, so it fails if the extraction reverts.
        """

        from app.api.models import TaskRunRequest
        from app.api.service import ForgeApiService
        from app.config.settings import RuntimeSettings
        from app.fabric.fabric import CapabilityFabric
        from app.runtime.bootstrap import create_runtime

        fabric = CapabilityFabric()
        runtime = create_runtime(
            settings=RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock",),
                provider_fallback_chain=(),
            ),
            fabric=fabric,
        )
        # Remove the only provider that could succeed, so every candidate fails and the
        # configured default cannot be admitted as a fallback.
        del runtime.agent_registry._agents["mock"]
        service = ForgeApiService(runtime=runtime, fabric=fabric)

        response = service.run_task(TaskRunRequest(description="attribution probe"))

        self.assertFalse(response.success)
        self.assertEqual(response.state, "FAILED")
        self.assertNotEqual(response.provider_used, "")

        records = fabric.get_all_run_accounting()
        attempts = records[0].attempt_records
        self.assertGreaterEqual(len(attempts), 2)

        terminal_provider = runtime.orchestrator._dispatcher._default_provider
        self.assertNotEqual(
            attempts[-1].provider_name,
            response.provider_used,
            "the last attempt must not be the source when it differs from the result",
        )
        self.assertEqual(response.provider_used, "openrouter")
        self.assertEqual(terminal_provider, "mock")

    def test_14_a_failed_run_never_reports_a_provider_as_its_model(self) -> None:
        """`model_used` names a model, never the provider.

        The dispatcher's exhaustion `TaskResult` carries `provider=selected` and no
        `model_name`. Falling back to the provider name therefore reported
        `model_used == 'openrouter'` -- a provider where a caller reads a model --
        for a run in which no model ever produced anything. The value must be the
        terminal result's real model, or the last recorded attempt's real model, or
        empty; never the provider.
        """
        from app.api.models import TaskRunRequest
        from app.api.service import ForgeApiService
        from app.config.settings import RuntimeSettings
        from app.fabric.fabric import CapabilityFabric
        from app.runtime.bootstrap import create_runtime

        fabric = CapabilityFabric()
        runtime = create_runtime(
            settings=RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock",),
                provider_fallback_chain=(),
            ),
            fabric=fabric,
        )
        # Every candidate fails, so the dispatcher returns its exhaustion result.
        del runtime.agent_registry._agents["mock"]
        service = ForgeApiService(runtime=runtime, fabric=fabric)

        response = service.run_task(TaskRunRequest(description="model attribution probe"))

        self.assertFalse(response.success)
        self.assertEqual(response.provider_used, "openrouter")
        self.assertNotEqual(
            response.model_used,
            response.provider_used,
            "a provider name must never be reported as the model",
        )
        known_models = {
            attempt.model_name
            for record in fabric.get_all_run_accounting()
            for attempt in record.attempt_records
            if attempt.model_name
        }
        self.assertIn(response.model_used, known_models | {""})

    def test_15_injected_routing_configuration_survives_the_fabric_rebind(self) -> None:
        """The rebind must not rewrite the caller's routing policy.

        The fabric cannot be attached to a dispatcher after construction, so
        `bind_fabric_to_runtime` rebuilds the orchestrator. It used to rebuild it
        from `runtime.settings`, which silently replaced the caller's routing with
        whatever settings happened to say -- in either direction, including the
        permissive one. Both directions are asserted below: the caller's restrictive
        policy must not become permissive, and the caller's permissive policy must
        not be silently narrowed either. What must change is exactly one thing: the
        fabric the dispatcher records into is now the service's own.
        """
        from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
        from app.agents.providers.model_registry import ModelRegistry
        from app.agents.providers.registry import ProviderRegistry
        from app.agents.registry import AgentRegistry
        from app.api.service import ForgeApiService
        from app.config.settings import RuntimeSettings
        from app.fabric.fabric import CapabilityFabric
        from app.orchestrator.orchestrator import Orchestrator
        from app.orchestrator.run import RunExecutor
        from app.runtime.context import RuntimeContext

        def build(*, allow_paid: bool, default_provider: str, chain: tuple[str, ...]):
            agents = AgentRegistry()
            providers = ProviderRegistry()
            capabilities = ProviderCapabilitiesRegistry()
            models = ModelRegistry()
            caller_fabric = CapabilityFabric()
            orchestrator = Orchestrator(
                agents,
                default_provider=default_provider,
                fallback_chain=chain,
                provider_registry=providers,
                capabilities_registry=capabilities,
                model_registry=models,
                fabric=caller_fabric,
                allow_paid_providers=allow_paid,
            )
            # Settings deliberately disagree with the caller's orchestrator on
            # every routing value this method must preserve.
            settings = RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock",),
                provider_fallback_chain=(),
                allow_paid_providers=not allow_paid,
            )
            runtime = RuntimeContext(
                settings=settings,
                provider_accounts={},
                provider_registry=providers,
                provider_capabilities=capabilities,
                agent_registry=agents,
                orchestrator=orchestrator,
                run_executor=RunExecutor(orchestrator),
                harness=None,
            )
            # Captured from the caller's own dispatcher, before the service sees it.
            caller_routing = {
                "default_provider": orchestrator._dispatcher._default_provider,
                "fallback_chain": tuple(orchestrator._dispatcher._fallback_chain),
                "allow_paid_providers": orchestrator._dispatcher._allow_paid_providers,
                "model_registry": orchestrator._dispatcher._model_registry,
                "provider_registry": orchestrator._dispatcher._provider_registry,
                "capabilities_registry": orchestrator._dispatcher._capabilities_registry,
                "registry": orchestrator._dispatcher._registry,
            }
            return runtime, caller_fabric, caller_routing

        for allow_paid, default_provider, chain in (
            (False, "openrouter", ("openrouter",)),
            (True, "mock", ()),
        ):
            with self.subTest(
                allow_paid=allow_paid, default_provider=default_provider, chain=chain
            ):
                runtime, caller_fabric, caller_routing = build(
                    allow_paid=allow_paid,
                    default_provider=default_provider,
                    chain=chain,
                )
                service = ForgeApiService(runtime=runtime)
                dispatcher = service.runtime.orchestrator._dispatcher

                # Exactly one thing changed: the fabric the dispatcher records into.
                self.assertIs(dispatcher._fabric, service.fabric)
                self.assertIsNot(dispatcher._fabric, caller_fabric)
                # Everything routing-relevant is the caller's own value, not settings'.
                self.assertIs(dispatcher._allow_paid_providers, allow_paid)
                self.assertEqual(dispatcher._default_provider, default_provider)
                self.assertEqual(tuple(dispatcher._fallback_chain), chain)
                self.assertIs(dispatcher._model_registry, caller_routing["model_registry"])
                self.assertIs(
                    dispatcher._provider_registry, caller_routing["provider_registry"]
                )
                self.assertIs(
                    dispatcher._capabilities_registry,
                    caller_routing["capabilities_registry"],
                )
                self.assertIs(dispatcher._registry, caller_routing["registry"])

    def test_08_no_secret_exposure(self) -> None:
        # Check system status
        st_dict = self.client.get_system_status().to_dict()
        st_str = str(st_dict)
        self.assertNotIn("sk-ant-", st_str)
        self.assertNotIn("sk-proj-", st_str)
        self.assertNotIn("sk-or-v1-", st_str)
        self.assertNotIn("AIzaSy", st_str)

        # Check health
        h_dict = self.client.check_health().to_dict()
        h_str = str(h_dict)
        self.assertNotIn("sk-ant-", h_str)
        self.assertNotIn("sk-proj-", h_str)
        self.assertNotIn("sk-or-v1-", h_str)
        self.assertNotIn("AIzaSy", h_str)

        # Check task response
        task_res = self.client.run_task(description="Return only: SAFE").to_dict()
        t_str = str(task_res)
        self.assertNotIn("sk-ant-", t_str)
        self.assertNotIn("sk-proj-", t_str)
        self.assertNotIn("sk-or-v1-", t_str)
        self.assertNotIn("AIzaSy", t_str)

    def test_09_disconnected_client_state(self) -> None:
        client = ForgeApiClient(api_service=None, base_url="http://127.0.0.1:9999")
        health = client.check_health()
        self.assertEqual(health.status, "error")
        self.assertEqual(health.core_status, "disconnected")

        status = client.get_system_status()
        self.assertEqual(status.core, "disconnected")

    def test_10_fastapi_app_construction(self) -> None:
        app = create_api_app(self.service)
        self.assertIsNotNone(app)
        self.assertEqual(app.title, "Forge AI API")


if __name__ == "__main__":
    unittest.main()
