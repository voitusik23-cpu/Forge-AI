"""Exactly one generative decision per declared verification, and it is the declaration.

Defect this pins (pre-existing, not introduced by the generation slice):
``ForgeApiService.run_declared_verification`` dispatched a generative task whose
entire output was discarded, and then ran the operator-declared host command. Two
independent measurements of one workflow reported two provider calls, the first
with the prompt ``Declared verification execution.``. Worse than the cost, the
discarded dispatch **gated** the execution: ``RunExecutor`` reached its
host-execution branch only when the generative dispatch succeeded, so when no
provider could answer, the declared command silently did not run.

The tests below assert observed behaviour through the real entry point:

* a counting transport, injected through the existing provider seam, records every
  generative call -- the count is of genuine ``Provider.generate`` invocations,
  not of a stub standing in for Forge's own code;
* a ``counted`` orchestrator double counts any dispatch at all, so a reverted fix
  that calls the model again fails these tests even if the transport is not the
  thing being asked;
* the decision carrier is asserted to be marked as declared rather than generated,
  so no model output can be read as a decision.

No provider is contacted and no network is used: the counting transport returns a
constant string in-process.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.api.service import ForgeApiService
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.models import EventType, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope
from app.runtime.run_store import RunStore
from app.tools.approval import ApprovalPolicy
from app.tools.workspace import Workspace
from app.usage import Usage

DECLARATION_ID = "probe.declared"
MARKER = "DECLARED-VERIFICATION-RAN"

#: What the counting transport returns. It is deliberately a value that must never
#: be mistaken for the declared command's own output.
TRANSPORT_OUTPUT = "this text came from a model and must never be the run output"


class CountingTransport(Provider):
    """A provider that records every generative call and opens no socket."""

    PROVIDER_NAME = "probe_transport"

    def __init__(self, calls: list[ProviderRequest]) -> None:
        super().__init__(
            ProviderConfig(
                provider_name=self.PROVIDER_NAME,
                model_name="probe-model",
                enabled=True,
            )
        )
        self.calls = calls

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            provider_name=self.PROVIDER_NAME,
            model_name="probe-model",
            output=TRANSPORT_OUTPUT,
            usage=Usage(input_tokens=1, output_tokens=1, estimated_cost=0.0),
        )


class CountingOrchestrator:
    """An orchestrator double that counts dispatches instead of answering them.

    ``RunExecutor`` reaches its host-execution branch only through a
    ``TaskResult``, so this double returns one -- but it records every call, which
    is what makes a reverted double-dispatch fail the tests below. The recorded
    count is the number of *decision-generating* dispatches, independent of which
    transport would have served them.
    """

    def __init__(self) -> None:
        self.dispatches: list[tuple[str, str]] = []

    def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
        self.dispatches.append((task.id, task.description))
        return TaskResult(task.id, True, output=TRANSPORT_OUTPUT)


class DeclaredVerificationDispatchTests(unittest.TestCase):
    """One workflow, one decision, and it is the operator's declaration."""

    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "ws"
        self.root.mkdir()
        self.calls: list[ProviderRequest] = []
        self.transport = CountingTransport(self.calls)

        providers = ProviderRegistry()
        providers.register(self.transport)
        agents = AgentRegistry()
        agents.register(ProviderAgent(self.transport))
        self.orchestrator = CountingOrchestrator()
        self.real_orchestrator = Orchestrator(
            agents,
            default_provider=CountingTransport.PROVIDER_NAME,
            provider_registry=providers,
        )

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    # ---------------------------------------------------------------- helpers
    def _service(self, *, orchestrator=None, command=None, approval_policy=None, exit_code=0):
        declaration = ExecutionDeclaration(
            declaration_id=DECLARATION_ID,
            command=command
            or ("python", "-c", f"print('{MARKER}')"),
            profile_id="api-default",
            working_directory=".",
            expected_exit_code=exit_code,
        )
        return ForgeApiService(
            runtime=RuntimeContext(
                settings=None,
                provider_accounts={},
                provider_registry=None,
                provider_capabilities=None,
                agent_registry=None,
                orchestrator=orchestrator or self.real_orchestrator,
                run_executor=RunExecutor(orchestrator or self.real_orchestrator),
            ),
            workspace=Workspace(self.root),
            execution_profile=ProjectExecutionProfile(
                profile_id="api-default",
                allowed_commands=("python",),
                capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
                network_access=False,
                working_directory=".",
            ),
            declarations={DECLARATION_ID: declaration},
            approval_policy=approval_policy or ApprovalPolicy(),
            run_store=RunStore(root=Path(self._tmp.name) / "store"),
        )

    # --------------------------------------------------------- the core claim
    def test_one_declared_verification_makes_exactly_one_decision(self) -> None:
        """The declared command runs, and the model is asked exactly zero times.

        The generative dispatch that used to happen produced a billable provider
        call whose output was discarded. The decision this run executes is the
        operator's declaration, which is already recorded before the run starts, so
        the correct count of *generated* decisions is zero -- there is nothing for a
        model to decide. The assertion is written as ``<= 1`` with the exact zero
        recorded explicitly so the intent ("no second decision, and in fact none at
        all") is legible rather than implied.
        """

        service = self._service()
        response = service.run_declared_verification(DECLARATION_ID)

        self.assertEqual(
            len(self.calls),
            0,
            "the declared verification asked a model for a decision: "
            f"{[request.prompt for request in self.calls]!r}",
        )
        self.assertLessEqual(len(self.calls), 1, "at most one generative decision")
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.state, "COMPLETED")
        self.assertIn(MARKER, response.output or "")

    def test_the_models_text_is_never_the_run_output_or_a_decision(self) -> None:
        """The discarded dispatch's output must not resurface anywhere.

        With the fix no dispatch happens, so the transport is never called. When a
        counting *orchestrator* is used instead, the double's output -- standing in
        for whatever a model would have said -- must still not become the run's
        output, and the run's recorded decision must be marked as declared.
        """

        service = self._service(orchestrator=self.orchestrator)
        response = service.run_declared_verification(DECLARATION_ID)

        self.assertEqual(
            self.orchestrator.dispatches,
            [],
            "run_declared_verification dispatched a generative task: "
            f"{self.orchestrator.dispatches!r}",
        )
        self.assertNotIn(
            TRANSPORT_OUTPUT,
            response.output or "",
            "a model's text became the declared verification's reported output",
        )
        self.assertIn(MARKER, response.output or "")
        self.assertEqual(response.provider_used, "")
        self.assertEqual(response.model_used, "")
        self.assertEqual(response.tokens, 0)
        self.assertEqual(response.cost, 0.0)

        record = service._run_store.load(response.run_id)
        self.assertIsNotNone(record)
        decisions = [
            event for event in record.events
            if event.event_type == EventType.DECISION_MADE
        ]
        self.assertEqual(len(decisions), 1, "exactly one recorded decision per run")
        metadata = decisions[0].metadata
        self.assertEqual(metadata.get("decision_source"), "declared_execution")
        self.assertIsNone(metadata.get("decision_provider"))
        self.assertIsNone(metadata.get("decision_model"))
        self.assertIs(metadata.get("generative_dispatch"), False)
        # No provider-selection event may be recorded for this run at all.
        chosen = [
            event for event in record.events
            if event.event_type in (EventType.PROVIDER_SELECTED, EventType.PROVIDER_ATTEMPT)
        ]
        self.assertEqual(chosen, [], f"a provider was selected: {chosen!r}")

    def test_the_real_exit_code_still_decides_the_reported_state(self) -> None:
        """Authorization and the command's own exit code are untouched.

        A declared command that exits non-zero must still be reported as a failure,
        so removing the generative call cannot have turned the run into a rubber
        stamp.
        """

        service = self._service(
            command=("python", "-c", "import sys; print('boom'); sys.exit(3)"),
            exit_code=0,
        )
        response = service.run_declared_verification(DECLARATION_ID)

        self.assertEqual(len(self.calls), 0)
        self.assertFalse(response.success)
        self.assertEqual(response.state, "FAILED")

    def test_the_declared_command_runs_even_when_no_provider_is_installed(self) -> None:
        """The regression that made this a defect, not just a cost.

        ``RunExecutor`` used to reach its host-execution branch only when the
        generative dispatch succeeded. With no reachable provider, the declared
        command therefore never ran and the run reported "no declared execution was
        requested" -- an authorized operator command silently skipped because a
        model was unavailable. The orchestrator here cannot answer at all.
        """

        class _UnavailableTransport(Provider):
            PROVIDER_NAME = "unavailable_transport"

            def __init__(self) -> None:
                super().__init__(
                    ProviderConfig(
                        provider_name=self.PROVIDER_NAME,
                        model_name="unavailable-model",
                    )
                )

            def generate(self, request: ProviderRequest) -> ProviderResponse:
                raise AssertionError(
                    "the declared verification must not ask a provider for a decision"
                )

        unavailable = _UnavailableTransport()
        providers = ProviderRegistry()
        providers.register(unavailable)
        agents = AgentRegistry()
        agents.register(ProviderAgent(unavailable))
        unavailable_orchestrator = Orchestrator(
            agents,
            default_provider=_UnavailableTransport.PROVIDER_NAME,
            provider_registry=providers,
        )

        service = self._service(orchestrator=unavailable_orchestrator)
        response = service.run_declared_verification(DECLARATION_ID)

        self.assertTrue(response.success, response.error)
        self.assertEqual(response.state, "COMPLETED")
        self.assertIn(MARKER, response.output or "")

    # ------------------------------------------------- authorization untouched
    def test_approval_policy_is_still_mandatory(self) -> None:
        """Removing the model call removed no authorization gate."""

        service = ForgeApiService(
            runtime=RuntimeContext(
                settings=None,
                provider_accounts={},
                provider_registry=None,
                provider_capabilities=None,
                agent_registry=None,
                orchestrator=self.real_orchestrator,
                run_executor=RunExecutor(self.real_orchestrator),
            ),
            workspace=Workspace(self.root),
            execution_profile=ProjectExecutionProfile(
                profile_id="api-default",
                allowed_commands=("python",),
                capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
                network_access=False,
                working_directory=".",
            ),
            declarations={
                DECLARATION_ID: ExecutionDeclaration(
                    declaration_id=DECLARATION_ID,
                    command=("python", "-c", f"print('{MARKER}')"),
                    profile_id="api-default",
                    working_directory=".",
                )
            },
            approval_policy=None,
            run_store=RunStore(root=Path(self._tmp.name) / "store2"),
        )

        response = service.run_declared_verification(DECLARATION_ID)

        self.assertFalse(response.success)
        self.assertEqual(response.error, "approval_policy_required")
        self.assertNotIn(MARKER, response.output or "")

    def test_an_approval_requirement_still_stops_the_command(self) -> None:
        """A policy that demands approval still blocks execution."""

        service = self._service(approval_policy=ApprovalPolicy(("execute",)))
        response = service.run_declared_verification(DECLARATION_ID)

        self.assertFalse(response.success)
        self.assertEqual(response.state, "WAITING_FOR_APPROVAL")
        self.assertNotIn(MARKER, response.output or "")

    def test_the_declared_command_still_reaches_the_coordinator(self) -> None:
        """The execution chain still produces real coordinator events."""

        service = self._service()
        response = service.run_declared_verification(DECLARATION_ID)

        record = service._run_store.load(response.run_id)
        self.assertIsNotNone(record)
        names = [event.event_type.name for event in record.events]
        self.assertIn("EXECUTION_STARTED", names)
        self.assertIn("EXECUTION_COMPLETED", names)
        self.assertIn("EXECUTION_REQUESTED", names)


if __name__ == "__main__":
    unittest.main()
