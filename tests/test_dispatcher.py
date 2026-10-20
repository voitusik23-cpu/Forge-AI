"""Offline tests for category based task dispatch."""

import unittest

from app.agents.base import AgentExecutionError
from app.agents.providers.capabilities import ProviderCapabilitiesRegistry
from app.agents.providers.config import ProviderConfig
from app.agents.providers.factory import ProviderFactory
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.orchestrator.dispatcher import DispatchPolicy, Dispatcher
from app.orchestrator.models import Task, TaskCategory, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.config.secrets import SecretStore


class StubAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.provider_name = name
        self.calls = 0

    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        return TaskResult(task_id=task.id, success=True, output=f"handled by {self.name}")


class UnavailableAgent(StubAgent):
    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        raise AgentExecutionError(f"{self.name} is not configured")


class DispatchPolicyTests(unittest.TestCase):
    def test_categories_choose_ordered_provider_preferences(self) -> None:
        policy = DispatchPolicy()
        cases = {
            TaskCategory.CODING: ("openai", "anthropic"),
            TaskCategory.REASONING: ("anthropic", "openai"),
            TaskCategory.LARGE_CONTEXT: ("google",),
            TaskCategory.CHEAP_FREE: ("openrouter",),
            TaskCategory.FAST_CHEAP: ("deepseek",),
            TaskCategory.OTHER: ("xai",),
        }
        for category, expected in cases.items():
            with self.subTest(category=category):
                decision = policy.decide(
                    Task(id="policy", description="task", category=category), "xai"
                )
                self.assertEqual(decision.candidates, expected)
                self.assertEqual(decision.selected_provider, expected[0])


class DispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AgentRegistry()
        self.agents = {}
        for name in ("openai", "anthropic", "google", "openrouter", "deepseek", "xai"):
            agent = StubAgent(name)
            self.agents[name] = agent
            self.registry.register(agent)
        self.orchestrator = Orchestrator(self.registry, default_provider="xai")

    def make_cost_dispatcher(
        self,
        names,
        *,
        allow_paid=False,
        fallback_chain=(),
        unavailable=(),
        disabled=(),
        missing_key=(),
        default_provider="mock",
    ) -> tuple[Dispatcher, dict[str, StubAgent]]:
        agents = AgentRegistry()
        providers = ProviderRegistry()
        capabilities = ProviderCapabilitiesRegistry()
        factory = ProviderFactory()
        agent_map = {}
        for name in names:
            agent = UnavailableAgent(name) if name in unavailable else StubAgent(name)
            agent_map[name] = agent
            agents.register(agent)
            provider_type_config = ProviderConfig(
                provider_name=name,
                model_name=(
                    "cohere/north-mini-code:free"
                    if name == "openrouter"
                    else "test-model"
                ),
                enabled=name not in disabled,
                api_key_env_var=(
                    None if name == "mock" else factory.create(name).config.api_key_env_var
                ),
            )
            secrets = {
                factory.create(name).config.api_key_env_var: "offline-placeholder"
            }
            if name in missing_key and provider_type_config.api_key_env_var:
                secrets.pop(provider_type_config.api_key_env_var, None)
            provider = factory.create(
                name,
                config=provider_type_config,
                secret_store=SecretStore(
                    env_file="missing-dispatcher-test-env", environ=secrets
                ),
            )
            providers.register(provider)
            capabilities.register_provider(provider)
        return (
            Dispatcher(
                agents,
                default_provider=default_provider,
                provider_registry=providers,
                capabilities_registry=capabilities,
                fallback_chain=fallback_chain,
                allow_paid_providers=allow_paid,
            ),
            agent_map,
        )

    def test_category_dispatches_to_first_policy_choice_only(self) -> None:
        task = Task(id="coding", description="write a function", category=TaskCategory.CODING)

        result = self.orchestrator.dispatch(task)

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(self.agents["openai"].calls, 1)
        self.assertEqual(self.agents["anthropic"].calls, 0)

    def test_other_category_uses_configured_default(self) -> None:
        result = self.orchestrator.dispatch(
            Task(id="default", description="ordinary task")
        )

        self.assertEqual(result.provider, "xai")

    def test_provider_can_be_manually_selected(self) -> None:
        result = self.orchestrator.dispatch(
            Task(id="manual", description="task", category=TaskCategory.CODING),
            provider_name="anthropic",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(self.agents["openai"].calls, 0)

    def test_unavailable_provider_returns_structured_failure_without_fallback(self) -> None:
        registry = AgentRegistry()
        anthropic = StubAgent("anthropic")
        registry.register(anthropic)
        dispatcher = Dispatcher(registry)

        result = dispatcher.dispatch(
            Task(id="unavailable", description="task", category=TaskCategory.CODING)
        )

        self.assertFalse(result.success)
        self.assertIn("openai", result.error)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(anthropic.calls, 0)

    def test_legacy_explicit_agent_dispatch_remains_available(self) -> None:
        result = self.orchestrator.dispatch(
            Task(id="legacy", description="task"), agent_name="anthropic"
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "anthropic")

    def test_successful_primary_does_not_use_fallback(self) -> None:
        dispatcher = Dispatcher(
            self.registry,
            default_provider="xai",
            fallback_chain=("anthropic",),
        )

        result = dispatcher.dispatch(
            Task(id="primary-ok", description="task", category=TaskCategory.CODING)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(self.agents["anthropic"].calls, 0)

    def test_unavailable_primary_uses_next_configured_provider(self) -> None:
        registry = AgentRegistry()
        primary = UnavailableAgent("openai")
        secondary = StubAgent("anthropic")
        registry.register(primary)
        registry.register(secondary)
        dispatcher = Dispatcher(registry, fallback_chain=("anthropic",))

        result = dispatcher.dispatch(
            Task(id="primary-fails", description="task", category=TaskCategory.CODING)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(secondary.calls, 1)

    def test_exhausted_chain_returns_combined_failure_reason(self) -> None:
        registry = AgentRegistry()
        registry.register(UnavailableAgent("openai"))
        dispatcher = Dispatcher(registry, fallback_chain=("missing", "also-missing"))

        result = dispatcher.dispatch(
            Task(id="chain-fails", description="task", category=TaskCategory.CODING)
        )

        self.assertFalse(result.success)
        self.assertIn("Provider fallback chain exhausted", result.error)
        self.assertIn("openai is not configured", result.error)
        self.assertIn("missing", result.error)
        self.assertEqual(result.provider, "openai")

    def test_explicit_provider_failure_does_not_switch_provider(self) -> None:
        registry = AgentRegistry()
        selected = UnavailableAgent("openai")
        fallback = StubAgent("anthropic")
        registry.register(selected)
        registry.register(fallback)
        dispatcher = Dispatcher(registry, fallback_chain=("anthropic",))

        result = dispatcher.dispatch(
            Task(id="explicit", description="task"), provider_name="openai"
        )

        self.assertFalse(result.success)
        self.assertIn("openai is not configured", result.error)
        self.assertEqual(fallback.calls, 0)

    def test_provider_and_capability_registries_guard_dispatch(self) -> None:
        agents = AgentRegistry()
        openai_agent = StubAgent("openai")
        agents.register(openai_agent)
        providers = ProviderRegistry()
        providers.register(ProviderFactory().create("openai"))
        capabilities = ProviderCapabilitiesRegistry()
        dispatcher = Dispatcher(
            agents,
            provider_registry=providers,
            capabilities_registry=capabilities,
        )

        result = dispatcher.dispatch(
            Task(id="missing-capability", description="task", category=TaskCategory.CODING)
        )

        self.assertFalse(result.success)
        self.assertIn("capabilities are not registered", result.error)
        self.assertEqual(openai_agent.calls, 0)

    def test_ordinary_task_prefers_available_free_provider(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("openai", "deepseek", "mock")
        )

        result = dispatcher.dispatch(Task(id="free", description="ordinary task"))

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "mock")
        self.assertEqual(agents["openai"].calls, 0)
        self.assertEqual(agents["deepseek"].calls, 0)

    def test_cheap_provider_precedes_paid_when_no_free_is_registered(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(("openai", "deepseek"))

        result = dispatcher.dispatch(Task(id="cheap", description="ordinary task"))

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "deepseek")
        self.assertEqual(agents["openai"].calls, 0)

    def test_unavailable_free_provider_advances_to_cheap_tier(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("mock", "deepseek", "openai"), unavailable=("mock",)
        )

        result = dispatcher.dispatch(Task(id="free-fails", description="ordinary task"))

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "deepseek")
        self.assertEqual(agents["mock"].calls, 1)
        self.assertEqual(agents["openai"].calls, 0)

    def test_paid_provider_is_selected_only_when_policy_allows(self) -> None:
        forbidden, forbidden_agents = self.make_cost_dispatcher(("openai",))
        forbidden_result = forbidden.dispatch(
            Task(id="paid-forbidden", description="ordinary task")
        )
        self.assertFalse(forbidden_result.success)
        self.assertEqual(forbidden_agents["openai"].calls, 0)

        permitted, permitted_agents = self.make_cost_dispatcher(
            ("openai",), allow_paid=True
        )
        permitted_result = permitted.dispatch(
            Task(id="paid-permitted", description="ordinary task")
        )
        self.assertTrue(permitted_result.success)
        self.assertEqual(permitted_result.provider, "openai")
        self.assertEqual(permitted_agents["openai"].calls, 1)

    def test_explicit_paid_provider_overrides_cost_policy(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("mock", "openai"), allow_paid=False, fallback_chain=("mock",)
        )

        result = dispatcher.dispatch(
            Task(id="explicit-paid", description="ordinary task"),
            provider_name="openai",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "openai")
        self.assertEqual(agents["mock"].calls, 0)

    def test_disabled_provider_is_skipped_during_automatic_routing(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("openrouter", "deepseek"),
            disabled=("openrouter",),
            fallback_chain=("deepseek",),
        )

        result = dispatcher.dispatch(
            Task(id="disabled", description="ordinary task", category=TaskCategory.CHEAP_FREE)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "deepseek")
        self.assertEqual(agents["openrouter"].calls, 0)

    def test_provider_without_key_is_skipped_to_fallback(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("openrouter", "deepseek"),
            missing_key=("openrouter",),
            fallback_chain=("deepseek",),
        )

        result = dispatcher.dispatch(
            Task(id="no-key", description="ordinary task", category=TaskCategory.CHEAP_FREE)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "deepseek")
        self.assertEqual(agents["openrouter"].calls, 0)

    def test_capability_mismatch_is_rejected_for_automatic_route(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(("mock",))

        result = dispatcher.dispatch(
            Task(
                id="capability-mismatch",
                description="use tools",
                parameters={"requires_tools": True},
            )
        )

        self.assertFalse(result.success)
        self.assertIn("does not support tools", result.error)
        self.assertEqual(agents["mock"].calls, 0)

    def test_free_openrouter_model_is_selected_automatically(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("openrouter", "deepseek", "openai")
        )

        result = dispatcher.dispatch(Task(id="free-openrouter", description="task"))

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(agents["deepseek"].calls, 0)
        self.assertEqual(agents["openai"].calls, 0)

    def test_explicit_provider_keeps_priority_over_auto_availability(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("openrouter", "deepseek"),
            disabled=("openrouter",),
            fallback_chain=("deepseek",),
        )

        result = dispatcher.dispatch(
            Task(id="explicit-disabled", description="task"),
            provider_name="openrouter",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(agents["deepseek"].calls, 0)

    def test_category_failure_uses_bounded_fallback_chain(self) -> None:
        dispatcher, agents = self.make_cost_dispatcher(
            ("openrouter", "deepseek"),
            unavailable=("openrouter",),
            fallback_chain=("deepseek",),
        )

        result = dispatcher.dispatch(
            Task(id="fallback", description="task", category=TaskCategory.CHEAP_FREE)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "deepseek")
        self.assertLessEqual(agents["openrouter"].calls, 1)


class DefaultProviderIsAttemptedTests(unittest.TestCase):
    """The configured default provider must be attempted, not merely declared.

    Regression for a routing gap: for a specialized category the attempt plan was built
    from the policy's static preference list, so a deployment whose `default_provider` was
    absent from that list never attempted the provider it had explicitly asked for.
    Observed on a clean checkout: with only the mock provider installed, a `code` task
    failed with "Provider fallback chain exhausted: ... provider is unavailable or not
    registered", because `openrouter` was the first preference and `mock` was not in the
    list at all. Two shipped tests failed for that reason.

    These assert behaviour: which provider actually ran, and in what order attempts were
    made. They do not inspect source text.
    """

    def _policy_order(self, category: TaskCategory) -> tuple:
        return DispatchPolicy._PREFERENCES.get(category, ())

    def test_default_is_attempted_when_absent_from_policy_candidates(self) -> None:
        """1. The default runs even though the policy does not list it.

        `mock` is deliberately not in `_PREFERENCES[CODE]`, which is what made this a gap.
        """

        self.assertNotIn("mock", self._policy_order(TaskCategory.CODE))
        dispatcher, agents = DispatcherTests().make_cost_dispatcher(
            ("openrouter", "deepseek", "groq", "mock"),
            disabled=("openrouter", "deepseek", "groq"),
            default_provider="mock",
        )

        result = dispatcher.dispatch(
            Task(id="default-attempted", description="task", category=TaskCategory.CODE)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "mock")
        self.assertEqual(agents["mock"].calls, 1)

    def test_policy_candidates_keep_their_order_and_priority(self) -> None:
        """2. The default is appended; no preference loses its priority.

        All preferences are unavailable, so the default is reached last -- proving it did
        not displace anything. Each earlier candidate is still attempted first, in order.
        """

        dispatcher, agents = DispatcherTests().make_cost_dispatcher(
            ("openrouter", "deepseek", "groq", "mock"),
            unavailable=("openrouter", "deepseek", "groq"),
            default_provider="mock",
        )

        result = dispatcher.dispatch(
            Task(id="order", description="task", category=TaskCategory.CODE)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "mock")
        for name in ("openrouter", "deepseek", "groq"):
            self.assertEqual(agents[name].calls, 1, f"{name} should be attempted first")
        self.assertEqual(agents["mock"].calls, 1)

    def test_a_default_already_in_the_candidates_is_not_duplicated(self) -> None:
        """3. No duplicate attempt when the default is already a policy candidate.

        `openrouter` is the first preference for `code` and is a free tier, so it is
        permitted by the cost policy. With `default_provider='openrouter'` the name is
        already in the candidate list and must not be appended a second time; the call
        count is what proves the absence of a duplicate. (An earlier version of this test
        used `xai`, which is PAID and therefore rejected outright, and it also asserted the
        raw preference tuple for a category that has no entry in `_PREFERENCES` at all.)
        """

        self.assertIn("openrouter", self._policy_order(TaskCategory.CODE))
        dispatcher, agents = DispatcherTests().make_cost_dispatcher(
            ("openrouter",), default_provider="openrouter"
        )

        result = dispatcher.dispatch(
            Task(id="no-duplicate", description="task", category=TaskCategory.CODE)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(agents["openrouter"].calls, 1)

    def test_an_unregistered_default_is_not_added(self) -> None:
        """4a. A default with no registered provider is never attempted."""

        dispatcher, agents = DispatcherTests().make_cost_dispatcher(
            ("openrouter",), disabled=("openrouter",), default_provider="not-installed"
        )

        result = dispatcher.dispatch(
            Task(id="unregistered-default", description="task", category=TaskCategory.CODE)
        )

        self.assertFalse(result.success)
        self.assertNotEqual(result.provider, "not-installed")
        self.assertNotIn("not-installed", agents)

    def test_a_disabled_default_is_not_added(self) -> None:
        """4b. A default disabled by configuration is never attempted."""

        dispatcher, agents = DispatcherTests().make_cost_dispatcher(
            ("openrouter", "mock"),
            disabled=("openrouter", "mock"),
            default_provider="mock",
        )

        result = dispatcher.dispatch(
            Task(id="disabled-default", description="task", category=TaskCategory.CODE)
        )

        self.assertFalse(result.success)
        self.assertEqual(agents["mock"].calls, 0)

    def test_a_default_without_its_declared_key_is_not_added(self) -> None:
        """4c. A default whose key is required but absent is never attempted."""

        dispatcher, agents = DispatcherTests().make_cost_dispatcher(
            ("openrouter",),
            disabled=("openrouter",),
            missing_key=("openrouter",),
            default_provider="openrouter",
        )

        result = dispatcher.dispatch(
            Task(id="keyless-default", description="task", category=TaskCategory.CODE)
        )

        self.assertFalse(result.success)
        self.assertEqual(agents["openrouter"].calls, 0)

    def test_an_explicit_provider_selection_still_wins_alone(self) -> None:
        """The default is appended only on the automatic route.

        An explicit `provider_name` is a caller decision and must remain the single
        attempt, exactly as `test_explicit_provider_failure_does_not_switch_provider`
        requires.
        """

        dispatcher, agents = DispatcherTests().make_cost_dispatcher(
            ("openrouter", "mock"), default_provider="mock"
        )

        dispatcher.dispatch(
            Task(id="explicit", description="task", category=TaskCategory.CODE),
            provider_name="openrouter",
        )

        self.assertEqual(agents["mock"].calls, 0)

    def test_the_offline_default_runs_with_no_network_provider_enabled(self) -> None:
        """7. The offline path is reachable with every live provider disabled.

        This is the property the two shipped API/desktop tests need: a run that reaches
        its terminal state using only the offline provider, so no external request is made
        and no balance is spent.
        """

        dispatcher, agents = DispatcherTests().make_cost_dispatcher(
            ("openrouter", "deepseek", "groq", "openai", "anthropic", "mock"),
            disabled=("openrouter", "deepseek", "groq", "openai", "anthropic"),
            default_provider="mock",
        )

        result = dispatcher.dispatch(
            Task(id="offline-only", description="task", category=TaskCategory.CODE)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "mock")
        for disabled_name in ("openrouter", "deepseek", "groq", "openai", "anthropic"):
            self.assertEqual(agents[disabled_name].calls, 0)

    def test_a_provider_that_is_not_a_registered_agent_is_not_added(self) -> None:
        """The default must be dispatchable, not merely registered as a provider.

        `_automatic_route_rejection` consults the provider registry. Dispatch needs an
        agent. A name present in one registry and absent from the other would pass that
        check and then raise `AgentNotFoundError` during lookup -- a raise rather than a
        failed `TaskResult`. This builds exactly that half-registered provider and asserts
        the name is left out, so the run fails through the normal failed-result path.
        """

        agents = AgentRegistry()
        providers = ProviderRegistry()
        capabilities = ProviderCapabilitiesRegistry()
        factory = ProviderFactory()

        # `mock` is a fully registered provider with capabilities...
        provider = factory.create(
            "mock",
            config=ProviderConfig(provider_name="mock", model_name="m", enabled=True),
            secret_store=SecretStore(env_file="missing-dispatcher-test-env", environ={}),
        )
        providers.register(provider)
        capabilities.register_provider(provider)
        # ...but deliberately NOT registered as an agent.

        dispatcher = Dispatcher(
            agents,
            default_provider="mock",
            provider_registry=providers,
            capabilities_registry=capabilities,
        )

        result = dispatcher.dispatch(
            Task(id="half-registered", description="task", category=TaskCategory.CODE)
        )

        # The point is that dispatch returns a failed result instead of raising: with the
        # name left out there is nothing to attempt, so the run fails through the normal
        # path. Before the agent-registry guard, `mock` was appended, the lookup raised
        # `AgentNotFoundError`, and the reason was lost on the way out.
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)
        self.assertIn("cost policy", result.error)


class FailedRunReportsItsReasonTests(unittest.TestCase):
    """A run whose provider raises an untranslated exception must still name the reason.

    Regression for a defect in the extraction of the terminal outcome. The executor sets
    `run.error` on every failure path but assigns `run.result` only inside its try block,
    so an exception raised by the provider before that assignment leaves the result unset.
    Reading the reason from `run.result` alone produced `success=False, error=None`: a
    failed run with the reason silently dropped.

    The assertion is on observable API output, not on internal fields, so it stays honest
    if the extraction is refactored again.
    """

    def _offline_runtime(self):
        from app.config.settings import RuntimeSettings
        from app.runtime.bootstrap import create_runtime

        return create_runtime(
            settings=RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock",),
                provider_fallback_chain=(),
            )
        )

    def test_an_untranslated_provider_exception_still_reports_a_reason(self) -> None:
        from app.agents.provider_agent import ProviderAgent
        from app.agents.providers.base import Provider as BaseProvider, ProviderResponse
        from app.agents.providers.config import ProviderConfig as Config
        from app.api.models import TaskRunRequest
        from app.api.service import ForgeApiService

        class ExplodingProvider(BaseProvider):
            """Raises something `ProviderAgent` does not translate to `ProviderError`.

            A real adapter does this whenever an SDK exception is not one of the mapped
            types, so this is the realistic shape of the failure, not a contrived one.
            """

            def __init__(self) -> None:
                super().__init__(Config(provider_name="mock", model_name="boom"))

            def generate(self, request):  # noqa: ANN001, ANN201 - test double
                raise TypeError("unmapped provider defect")

        runtime = self._offline_runtime()
        runtime.agent_registry._agents["mock"] = ProviderAgent(ExplodingProvider())
        # The composition authorizes one project; a run cannot be created without it.
        service = ForgeApiService(
            runtime=runtime, authorized_project_id="dispatcher"
        )

        # Dispatched through the provider path directly: the API task path executes the
        # offline provider with the deterministic harness loop and never reaches the
        # provider chain, so the failure this test is about is driven where it happens.
        from app.orchestrator.models import RunState, Task

        run = service.runtime.run_executor.execute(
            task=Task(id="dispatcher-probe-task", description="probe"),
            provider_name=None,
            workspace=service.workspace,
            run_id="run-dispatcher-probe",
        )

        self.assertEqual(run.state, RunState.FAILED)
        self.assertIsNotNone(
            run.error,
            "a failed run reported no reason; the terminal error was dropped",
        )
        self.assertIn("TypeError", run.error.message)

    def test_a_successful_run_reports_no_error(self) -> None:
        """The guard must not turn a success into a failure."""

        from app.api.models import TaskRunRequest
        from app.api.service import ForgeApiService

        # The composition authorizes one project; a run cannot be created without it.
        service = ForgeApiService(
            runtime=self._offline_runtime(), authorized_project_id="dispatcher"
        )
        response = service.run_task(TaskRunRequest(description="probe"))

        self.assertTrue(response.success)
        # The guard must not turn a success into a failure -- and it must not turn a
        # returned answer into a completed project either. The offline path produces
        # a report and no project, so the state is the honest outcome word.
        self.assertIsNone(response.error)
        self.assertEqual(response.outcome, "SPECIFICATION_READY")
        self.assertEqual(response.state, "SPECIFICATION_READY")


class AttemptAccountingTests(unittest.TestCase):
    """The dispatched attempts are recorded in the fabric the dispatcher holds.

    Provider and model attribution comes from these records, and the dispatcher skips the
    record entirely when it holds no fabric -- which is how `provider_used` came to be
    permanently empty. These assert the recorded facts: one entry per attempted provider,
    the successful provider named, and the fallback position flagged.
    """

    def _runtime_with_fabric(self):
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
        return runtime, fabric

    def test_every_attempt_is_recorded_and_the_last_one_succeeded(self) -> None:
        from app.orchestrator.models import Task as DispatchTask

        runtime, fabric = self._runtime_with_fabric()
        run = runtime.run_executor.execute(
            task=DispatchTask(id="accounting", description="task",
                              category=TaskCategory.CODE),
            run_id="run-accounting-probe",
            workspace=None,
        )

        # The dispatcher keys usage by the run id it can see: `task.context["run_id"]` or
        # `task.parameters["run_id"]` when the caller supplies one, otherwise the task id.
        # This call passes no context, so the task id is the key -- stated here because the
        # `run_id` argument of `execute` is not what reaches the fabric.
        self.assertEqual(fabric.list_run_ids(), ["accounting"])
        records = fabric.get_all_run_accounting()
        self.assertEqual(len(records), 1)
        attempts = records[0].attempt_records

        # Every policy preference is disabled in this runtime, so each is attempted and
        # rejected before the configured default succeeds.
        self.assertGreaterEqual(len(attempts), 2)
        self.assertEqual([a.attempt_index for a in attempts],
                         list(range(1, len(attempts) + 1)))

        succeeded = [a for a in attempts if a.success]
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(succeeded[0].provider_name, "mock")

        rejected = [a for a in attempts if not a.success]
        for record in rejected:
            self.assertIsNotNone(record.error_message)
            self.assertNotEqual(record.provider_name, "mock")

        self.assertFalse(attempts[0].is_fallback)
        self.assertTrue(attempts[-1].is_fallback)
        self.assertEqual(attempts[-1].provider_name, run.result.provider)

    def test_no_attempt_is_recorded_when_the_dispatcher_has_no_fabric(self) -> None:
        """The other half of the contract: without a fabric there is nothing to record.

        This is the defect this suite now guards: a runtime built without a fabric
        dispatches correctly but reports no attribution at all. It is asserted rather than
        left implicit, so the identity check in `test_api.py` has a stated reason.
        """

        from app.config.settings import RuntimeSettings
        from app.orchestrator.models import Task as DispatchTask
        from app.runtime.bootstrap import create_runtime

        runtime = create_runtime(
            settings=RuntimeSettings(
                default_provider="mock",
                enabled_providers=("mock",),
                provider_fallback_chain=(),
            )
        )
        self.assertIsNone(runtime.orchestrator._dispatcher._fabric)

        run = runtime.run_executor.execute(
            task=DispatchTask(id="no-fabric", description="task",
                              category=TaskCategory.CODE),
            run_id="run-no-fabric",
            workspace=None,
        )

        self.assertTrue(run.result.success)
        self.assertIsNone(runtime.orchestrator._dispatcher._fabric)


if __name__ == "__main__":
    unittest.main()
