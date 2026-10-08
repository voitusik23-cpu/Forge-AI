"""Production criterion, verification, and acceptance slice.

These tests pin the difference between "the command ran" and "the task is done".

Criterion identity is created by trusted server-side composition
(``AcceptanceSpec`` declared next to the execution declaration, frozen per run
into ``RunAcceptanceCriteria``). Verification is deterministic and file-based
through the existing ``WorkspaceVerifier``, and the verdict comes from the
existing ``AcceptanceGate``. No LLM, no decision, and no caller can choose a
criterion, choose a verifier, or declare the verdict.

The slice is intentionally narrow: one authorized action, then its verification.
"""

from __future__ import annotations

from pathlib import Path
import dataclasses
import hashlib
import inspect
import tempfile
import unittest

from app.agent_runtime.acceptance_spec import (
    AcceptanceSpec,
    AcceptanceSpecError,
    RunAcceptanceCriteria,
)
from app.agent_runtime.frozen_verifier import FrozenCriteriaVerifier
from app.agent_runtime.models import HarnessRequest
from app.api.models import TaskRunRequest
from app.api.service import ACCEPTANCE_LOOP_POLICY, ForgeApiService
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus
from app.orchestrator.models import EventType, TaskResult
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_agent_harness
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope
from app.tools.acceptance import AcceptanceCriterion, AcceptanceStatus
from app.tools.approval import ApprovalPolicy
from app.tools.registry import build_default_tool_registry
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
)
from app.tools.workspace import Workspace

ARTIFACT = "artifact.txt"
DECLARATION_ID = "verify.acceptance"
CRITERION_ID = "artifact-present"

PRODUCE_ARTIFACT = ("python", "-c", "open('artifact.txt','w').write('ok')")
DO_NOTHING = ("python", "-c", "print('no artifact')")
FAIL_PROCESS = ("python", "-c", "import sys; sys.exit(3)")


def criterion(**overrides) -> AcceptanceCriterion:
    payload = {
        "criterion_id": CRITERION_ID,
        "description": "the action produced its artifact",
        "requirement_id": "req-artifact",
    }
    payload.update(overrides)
    return AcceptanceCriterion(**payload)


def api_profile(**overrides) -> ProjectExecutionProfile:
    payload = {
        "profile_id": "api-default",
        "allowed_commands": ("python",),
        "capabilities": frozenset({ExecutionCapability.INTERPRET_TEXT}),
        "network_access": False,
        "working_directory": ".",
    }
    payload.update(overrides)
    return ProjectExecutionProfile(**payload)


class _OrchestratorDouble:
    def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
        return TaskResult(task.id, True, output="double")


def _runtime(**overrides) -> RuntimeContext:
    orchestrator = _OrchestratorDouble()
    payload = {
        "settings": None,
        "provider_accounts": {},
        "provider_registry": None,
        "provider_capabilities": None,
        "agent_registry": None,
        "orchestrator": orchestrator,
        "run_executor": RunExecutor(orchestrator),
        "harness": create_agent_harness(),
    }
    payload.update(overrides)
    return RuntimeContext(**payload)


class AcceptanceSliceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _service(
        self,
        *,
        command=PRODUCE_ARTIFACT,
        expectations=None,
        criteria=None,
        with_spec=True,
        **kwargs,
    ):
        if expectations is None:
            expectations = {
                CRITERION_ID: VerificationExpectation(ARTIFACT, True)
            }
        if criteria is None:
            criteria = (criterion(),)
        declarations = {
            DECLARATION_ID: ExecutionDeclaration(
                declaration_id=DECLARATION_ID,
                command=command,
                profile_id="api-default",
                working_directory=".",
            )
        }
        specs = {}
        if with_spec:
            specs[DECLARATION_ID] = AcceptanceSpec(
                declaration_id=DECLARATION_ID,
                criteria=criteria,
                expectations=expectations,
            )
        return ForgeApiService(
            runtime=kwargs.pop("runtime", _runtime()),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=api_profile(),
            declarations=declarations,
            acceptance_specs=specs,
            approval_policy=kwargs.pop("approval_policy", ApprovalPolicy()),
            **kwargs,
        )

    @staticmethod
    def _acceptance_of(service, response) -> dict:
        record = service._run_store.load(response.run_id)
        events = [
            event
            for event in record.events
            if event.event_type.name == "ACCEPTANCE_COMPLETED"
        ]
        return dict(events[-1].metadata) if events else {}

    @staticmethod
    def _event_names(service, response) -> list[str]:
        record = service._run_store.load(response.run_id)
        return [event.event_type.name for event in record.events]


class CriterionIdentityTests(AcceptanceSliceTestCase):
    def test_spec_requires_a_declaration_and_criteria(self) -> None:
        with self.assertRaises(AcceptanceSpecError):
            AcceptanceSpec(declaration_id="", criteria=(criterion(),),
                           expectations={CRITERION_ID: VerificationExpectation(ARTIFACT, True)})
        with self.assertRaises(AcceptanceSpecError):
            AcceptanceSpec(declaration_id=DECLARATION_ID, criteria=(),
                           expectations={CRITERION_ID: VerificationExpectation(ARTIFACT, True)})
        with self.assertRaises(AcceptanceSpecError):
            AcceptanceSpec(declaration_id=DECLARATION_ID, criteria=(criterion(),),
                           expectations={})

    def test_every_criterion_needs_an_expectation(self) -> None:
        """A criterion that cannot be checked would only ever be missing."""
        with self.assertRaises(AcceptanceSpecError) as ctx:
            AcceptanceSpec(
                declaration_id=DECLARATION_ID,
                criteria=(criterion(), criterion(criterion_id="other")),
                expectations={CRITERION_ID: VerificationExpectation(ARTIFACT, True)},
            )
        self.assertIn("without a verification expectation", str(ctx.exception))

    def test_expectation_for_unknown_criterion_is_rejected(self) -> None:
        with self.assertRaises(AcceptanceSpecError):
            AcceptanceSpec(
                declaration_id=DECLARATION_ID,
                criteria=(criterion(),),
                expectations={"ghost": VerificationExpectation(ARTIFACT, True)},
            )

    def test_duplicate_criterion_ids_are_rejected(self) -> None:
        with self.assertRaises(AcceptanceSpecError):
            AcceptanceSpec(
                declaration_id=DECLARATION_ID,
                criteria=(criterion(), criterion()),
                expectations={CRITERION_ID: VerificationExpectation(ARTIFACT, True)},
            )

    def test_invalid_digest_expectation_is_rejected(self) -> None:
        for bad in (
            VerificationExpectation(ARTIFACT, True, "not-a-digest"),
            VerificationExpectation(ARTIFACT, False, "a" * 64),
        ):
            with self.assertRaises(AcceptanceSpecError):
                AcceptanceSpec(
                    declaration_id=DECLARATION_ID,
                    criteria=(criterion(),),
                    expectations={CRITERION_ID: bad},
                )

    def test_criteria_are_immutable(self) -> None:
        spec = AcceptanceSpec(
            declaration_id=DECLARATION_ID,
            criteria=(criterion(),),
            expectations={CRITERION_ID: VerificationExpectation(ARTIFACT, True)},
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            spec.criteria = ()
        frozen = spec.bind(run_id="r", task_id="t")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            frozen.criteria = ()

    def test_bound_criteria_are_pinned_to_one_run_and_task(self) -> None:
        """Replaying criteria against another run fails closed."""
        spec = AcceptanceSpec(
            declaration_id=DECLARATION_ID,
            criteria=(criterion(),),
            expectations={CRITERION_ID: VerificationExpectation(ARTIFACT, True)},
        )
        frozen = spec.bind(run_id="run-a", task_id="task-a")
        frozen.assert_belongs_to("run-a", "task-a")
        with self.assertRaises(AcceptanceSpecError):
            frozen.assert_belongs_to("run-b", "task-a")
        with self.assertRaises(AcceptanceSpecError):
            frozen.assert_belongs_to("run-a", "task-b")

    def test_run_reports_one_consistent_criterion_identity(self) -> None:
        """run_id, task_id, criterion_id, and verdict share one identity."""
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        acceptance = self._acceptance_of(service, response)
        self.assertEqual(acceptance.get("run_id"), response.run_id)
        self.assertEqual(acceptance.get("task_id"), response.task_id)
        self.assertEqual(acceptance.get("criterion_ids"), [CRITERION_ID])
        record = service._run_store.load(response.run_id)
        self.assertEqual(record.snapshot.task_id, response.task_id)
        self.assertEqual(
            {event.task_id for event in record.events if event.task_id},
            {response.task_id},
        )


class TrustedCriteriaTests(AcceptanceSliceTestCase):
    def test_no_declared_criteria_means_no_acceptance(self) -> None:
        """Absent criteria are not a permissive default."""
        service = self._service(with_spec=False)
        with self.assertRaises(AcceptanceSpecError):
            service.run_accepted_task(DECLARATION_ID)

    def test_unknown_declaration_is_refused(self) -> None:
        service = self._service()
        from app.execution.declaration import UnknownExecutionDeclarationError

        with self.assertRaises(UnknownExecutionDeclarationError):
            service.run_accepted_task("not.declared")

    def test_caller_cannot_pass_criteria_or_expectations(self) -> None:
        """A: no authority parameter exists on the entry point.

        ``idempotency_key`` is admitted by a later block; it selects a durable
        operation record and confers nothing, so it is listed below among the
        names that must never appear rather than being an exception to the rule.
        """
        parameters = list(
            inspect.signature(ForgeApiService.run_accepted_task).parameters
        )
        self.assertEqual(parameters, ["self", "declaration_id", "idempotency_key"])
        service = self._service()
        for kwargs in (
            {"criterion_id": "forged"},
            {"criteria": (criterion(criterion_id="forged"),)},
            {"expectations": {"forged": VerificationExpectation(ARTIFACT, True)}},
            {"command": ("rm", "-rf", "/")},
            {"workspace": self.workspace},
            {"acceptance_spec": None},
            # The idempotency key is not an authority input: it cannot carry a
            # command, a criterion, a workspace, or an approval.
            {"run_scope": None},
            {"authorized_execution": None},
            {"allowed_tool_ids": frozenset()},
        ):
            with self.assertRaises(TypeError):
                service.run_accepted_task(DECLARATION_ID, **kwargs)

    def test_taskrunrequest_has_no_acceptance_authority(self) -> None:
        fields = {f.name for f in dataclasses.fields(TaskRunRequest)}
        self.assertEqual(
            fields,
            {"description", "category", "task_id", "provider_name", "project_id", "context"},
        )
        for banned in (
            "criterion_id", "criteria", "acceptance_criteria",
            "verification_expectations", "declaration_id", "command",
        ):
            self.assertNotIn(banned, fields)

    def test_criteria_come_only_from_operator_composition(self) -> None:
        """The frozen criteria are the operator's, verbatim."""
        service = self._service()
        captured: dict[str, object] = {}
        real_factory = service._harness_factory

        def spy(**kwargs):
            harness = real_factory(**kwargs)
            original = harness.run

            def run(request):
                captured["criteria"] = request.acceptance_criteria
                captured["expectations"] = dict(request.verification_expectations)
                return original(request)

            harness.run = run  # type: ignore[method-assign]
            return harness

        service._harness_factory = spy
        service.run_accepted_task(DECLARATION_ID)
        self.assertEqual(
            [c.criterion_id for c in captured["criteria"]], [CRITERION_ID]
        )
        self.assertEqual(list(captured["expectations"]), [CRITERION_ID])

    def test_criteria_are_frozen_before_the_action_runs(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        names = self._event_names(service, response)
        self.assertIn("CRITERION_DEFINED", names)
        self.assertLess(
            names.index("CRITERION_DEFINED"),
            min(
                names.index(n)
                for n in ("EXECUTION_STARTED", "VERIFICATION_COMPLETED")
                if n in names
            ),
        )

    def test_decision_provider_cannot_see_criteria_or_expectations(self) -> None:
        """C/D: a decision request carries no criterion or verifier input."""
        seen: list[object] = []

        class Probe:
            def decide(self, request):
                seen.append(request)
                from app.decision.models import Decision, DecisionAction, DecisionType

                return Decision(
                    decision_id="probe", run_id=request.run_id,
                    decision_type=DecisionType.CONTINUE,
                    action=DecisionAction.EXECUTE, reason_code="probe",
                )

        service = self._service(
            harness_factory=lambda **kw: create_agent_harness(
                decision_provider=Probe(), **kw
            )
        )
        service.run_accepted_task(DECLARATION_ID)
        self.assertTrue(seen)
        fields = {f.name for f in dataclasses.fields(seen[0])}
        for banned in (
            "criterion_id", "criteria", "acceptance_criteria",
            "verification_expectations", "verifier", "command", "run_scope",
        ):
            self.assertNotIn(banned, fields)


class AcceptanceSemanticsTests(AcceptanceSliceTestCase):
    def test_successful_execution_and_verification_is_accepted(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        acceptance = self._acceptance_of(service, response)
        self.assertEqual(acceptance.get("status"), "pass")
        self.assertTrue(response.success, response.error)
        self.assertTrue((self.root / ARTIFACT).exists())

    def test_execution_success_alone_is_not_acceptance(self) -> None:
        """J/K: a passing process with a failing criterion is rejected."""
        service = self._service(command=DO_NOTHING)
        response = service.run_accepted_task(DECLARATION_ID)
        acceptance = self._acceptance_of(service, response)
        self.assertEqual(acceptance.get("status"), "fail")
        self.assertFalse(response.success)
        self.assertEqual(response.error, "required_criteria_failed")
        self.assertFalse((self.root / ARTIFACT).exists())

    def test_verification_failure_is_distinguishable_from_execution(self) -> None:
        service = self._service(command=DO_NOTHING)
        response = service.run_accepted_task(DECLARATION_ID)
        names = self._event_names(service, response)
        self.assertIn("EXECUTION_COMPLETED", names)
        self.assertIn("VERIFICATION_COMPLETED", names)
        completion = [
            event
            for event in service._run_store.load(response.run_id).events
            if event.event_type.name == "EXECUTION_COMPLETED"
        ]
        self.assertTrue(completion)
        # The acceptance verdict is FAIL, while the execution itself completed.
        self.assertFalse(response.success)

    def test_execution_failure_is_never_accepted(self) -> None:
        """L: a failed process cannot reach acceptance."""
        service = self._service(command=FAIL_PROCESS)
        response = service.run_accepted_task(DECLARATION_ID)
        acceptance = self._acceptance_of(service, response)
        self.assertEqual(response.state, "FAILED")
        self.assertFalse(response.success)
        self.assertNotEqual(acceptance.get("status"), "pass")
        self.assertEqual(acceptance.get("status"), "not_evaluated")

    def test_missing_verification_is_never_accepted(self) -> None:
        """K: a criterion without a result cannot pass."""
        from app.tools.acceptance import AcceptanceGate

        gate = AcceptanceGate()
        result = gate.evaluate(
            (criterion(),), {}, run_id="run", observer=lambda *a, **k: None
        )
        self.assertIs(result.status, AcceptanceStatus.FAIL)
        self.assertEqual(result.code, "required_criteria_failed")
        self.assertEqual(result.results[0].code, "verification_missing")

    def test_verifier_error_is_never_accepted(self) -> None:
        """M: a verifier that cannot work reports ERROR, not PASS."""

        class BrokenVerifier:
            def verify(self, *args, **kwargs):
                raise RuntimeError("verifier exploded")

        verifier = FrozenCriteriaVerifier(verifier=BrokenVerifier())
        results = verifier.verify_all(
            {CRITERION_ID: VerificationExpectation(ARTIFACT, True)},
            workspace=self.workspace,
            run_id="run",
            observer=lambda *a, **k: None,
        )
        self.assertEqual(len(results), 1)
        self.assertIs(results[0].status, VerificationStatus.ERROR)
        self.assertEqual(results[0].code, "verifier_error")
        self.assertNotEqual(results[0].status, VerificationStatus.PASS)

    def test_verifier_error_produces_rejection_not_acceptance(self) -> None:
        from app.tools.acceptance import AcceptanceGate

        broken = VerificationResult(
            verification_id="v",
            status=VerificationStatus.ERROR,
            code="verifier_error",
            criterion_id=CRITERION_ID,
        )
        result = AcceptanceGate().evaluate(
            (criterion(),),
            {CRITERION_ID: broken},
            run_id="run",
            observer=lambda *a, **k: None,
        )
        self.assertIs(result.status, AcceptanceStatus.FAIL)
        self.assertEqual(result.results[0].code, "verification_failed")

    def test_verification_pass_requires_the_real_artifact(self) -> None:
        """The verdict follows the workspace fact, not the exit code."""
        service = self._service(command=PRODUCE_ARTIFACT)
        response = service.run_accepted_task(DECLARATION_ID)
        self.assertEqual(self._acceptance_of(service, response).get("status"), "pass")

        # A digest criterion fails against a different artifact content, and the
        # per-criterion outcome is recorded with its own code.
        expected = hashlib.sha256(b"different").hexdigest()
        service2 = self._service(
            command=PRODUCE_ARTIFACT,
            expectations={CRITERION_ID: VerificationExpectation(ARTIFACT, True, expected)},
        )
        response2 = service2.run_accepted_task(DECLARATION_ID)
        acceptance2 = self._acceptance_of(service2, response2)
        self.assertEqual(acceptance2.get("status"), "fail")
        self.assertFalse(response2.success)
        self.assertEqual(response2.error, "required_criteria_failed")
        criterion_results = acceptance2.get("criterion_results") or []
        self.assertEqual(criterion_results[0]["code"], "verification_failed")
        verification = [
            event
            for event in service2._run_store.load(response2.run_id).events
            if event.event_type.name == "VERIFICATION_COMPLETED"
        ]
        self.assertEqual(verification[-1].metadata.get("code"), "content_hash_mismatch")


class VerificationOrderTests(AcceptanceSliceTestCase):
    def test_verification_runs_after_the_execution(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        names = self._event_names(service, response)
        self.assertIn("EXECUTION_STARTED", names)
        self.assertIn("VERIFICATION_COMPLETED", names)
        self.assertLess(
            names.index("EXECUTION_STARTED"), names.index("VERIFICATION_COMPLETED")
        )

    def test_acceptance_runs_after_the_verification(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        names = self._event_names(service, response)
        self.assertIn("VERIFICATION_COMPLETED", names)
        self.assertIn("ACCEPTANCE_COMPLETED", names)
        self.assertLess(
            names.index("VERIFICATION_COMPLETED"),
            names.index("ACCEPTANCE_COMPLETED"),
        )

    def test_at_most_one_execution_attempt(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        names = self._event_names(service, response)
        self.assertEqual(names.count("EXECUTION_STARTED"), 1)
        self.assertEqual(ACCEPTANCE_LOOP_POLICY.max_execution_attempts, 1)


class VerifierAuthorityTests(AcceptanceSliceTestCase):
    def test_verifier_cannot_change_command_authority(self) -> None:
        """G: the verifier has no execution interface at all."""
        verifier = FrozenCriteriaVerifier()
        for banned in ("execute", "run", "spawn", "command", "argv"):
            self.assertFalse(
                hasattr(verifier, banned), f"verifier must not expose {banned}"
            )
        signature = inspect.signature(FrozenCriteriaVerifier.verify_all)
        self.assertEqual(
            list(signature.parameters),
            ["self", "expectations", "workspace", "run_id", "observer",
             "execution_result_id"],
        )

    def test_verifier_cannot_change_the_workspace(self) -> None:
        """H: the verifier resolves inside the workspace it is given, only."""
        from app.tools.verification import WorkspaceVerifier

        result = WorkspaceVerifier().verify(
            VerificationExpectation("../outside.txt", True),
            workspace=self.workspace,
            run_id="run",
            observer=lambda *a, **k: None,
            criterion_id=CRITERION_ID,
        )
        self.assertIsNot(result.status, VerificationStatus.PASS)

    def test_verifier_cannot_change_the_run_scope(self) -> None:
        """I: verification happens outside any authority path."""
        scope = RunScope(
            run_id="verify-scope",
            workspace=self.workspace,
            execution_profile=api_profile(),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(criterion(),),
        )
        scope.freeze()
        before = scope.fingerprint
        FrozenCriteriaVerifier().verify_all(
            {CRITERION_ID: VerificationExpectation(ARTIFACT, True)},
            workspace=self.workspace,
            run_id="verify-scope",
            observer=lambda *a, **k: None,
        )
        self.assertEqual(scope.fingerprint, before)

    def test_expectation_is_not_command_authority(self) -> None:
        """A criterion names a workspace fact, never an executable."""
        fields = {f.name for f in dataclasses.fields(VerificationExpectation)}
        self.assertEqual(fields, {"relative_path", "exists", "sha256"})
        for banned in ("command", "argv", "executable", "environment", "timeout"):
            self.assertNotIn(banned, fields)

    def test_empty_expectations_verify_nothing(self) -> None:
        results = FrozenCriteriaVerifier().verify_all(
            {},
            workspace=self.workspace,
            run_id="run",
            observer=lambda *a, **k: None,
        )
        self.assertEqual(results, ())


class RunStoreAcceptanceTests(AcceptanceSliceTestCase):
    def test_history_contains_the_acceptance_lifecycle(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        names = self._event_names(service, response)
        self.assertIn("RUN_STARTED", names)
        self.assertIn("CRITERION_DEFINED", names)
        self.assertIn("EXECUTION_STARTED", names)
        self.assertIn("VERIFICATION_COMPLETED", names)
        self.assertIn("ACCEPTANCE_COMPLETED", names)

    def test_lifecycle_events_carry_criterion_identity(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        for event in record.events:
            if event.event_type.name in (
                "CRITERION_DEFINED", "ACCEPTANCE_COMPLETED"
            ):
                self.assertEqual(event.metadata.get("run_id"), response.run_id)
        criterion_event = [
            event
            for event in record.events
            if event.event_type.name == "CRITERION_DEFINED"
        ][-1]
        self.assertEqual(criterion_event.metadata.get("criterion_ids"), [CRITERION_ID])

    def test_verification_events_carry_the_criterion_id(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        verification = [
            event
            for event in record.events
            if event.event_type.name == "VERIFICATION_COMPLETED"
            and event.metadata.get("criterion_id")
        ]
        self.assertTrue(verification)
        self.assertEqual(verification[-1].metadata["criterion_id"], CRITERION_ID)

    def test_history_metadata_is_sanitized(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        for event in record.events:
            for banned in ("stdout", "stderr", "_token", "environment_variables"):
                self.assertNotIn(banned, event.metadata)
        raw = service._run_store.events_path(response.run_id).read_text(encoding="utf-8")
        self.assertNotIn("SECRET", raw)

    def test_snapshot_records_the_acceptance_status(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        self.assertEqual(record.snapshot.project_state.get("acceptance_status"), "pass")

    def test_one_run_one_task_identity(self) -> None:
        service = self._service()
        response = service.run_accepted_task(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        identities = {response.task_id, record.snapshot.task_id}
        identities |= {event.task_id for event in record.events if event.task_id}
        self.assertEqual(len(identities), 1, identities)
        self.assertEqual(response.task_id, f"task-accept-{DECLARATION_ID}")
        self.assertTrue(response.run_id.startswith("run-accept-"))


class BackwardCompatibilityTests(AcceptanceSliceTestCase):
    def test_declared_verification_still_works(self) -> None:
        service = self._service()
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.state, "COMPLETED")

    def test_run_task_is_unchanged(self) -> None:
        service = self._service()
        response = service.run_task(TaskRunRequest(description="normal task"))
        self.assertTrue(response.run_id.startswith("run-api-"), response.run_id)

    def test_agent_loop_slice_still_works(self) -> None:
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        self.assertTrue(response.run_id.startswith("run-loop-"))

    def test_criterion_defined_event_is_registered(self) -> None:
        """The durable history can read back every event name it writes."""
        self.assertTrue(hasattr(EventType, "CRITERION_DEFINED"))
        self.assertEqual(EventType.CRITERION_DEFINED.value, "criterion_defined")

    def test_existing_harness_verification_path_is_unchanged(self) -> None:
        """The harness helper still verifies and evaluates acceptance."""
        harness = create_agent_harness()
        scope = RunScope(
            run_id="harness-verify",
            workspace=self.workspace,
            execution_profile=api_profile(),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(criterion(),),
        )
        scope.freeze()
        (self.root / ARTIFACT).write_text("ok", encoding="utf-8")
        request = HarnessRequest(
            run_id="harness-verify",
            workspace=self.workspace,
            acceptance_criteria=(criterion(),),
            verification_expectations={
                CRITERION_ID: VerificationExpectation(ARTIFACT, True)
            },
            run_scope=scope,
        )
        verified, acceptance = harness.run_verification(
            request, lambda *a, **k: None
        )
        self.assertEqual(len(verified), 1)
        self.assertIsNotNone(acceptance)
        self.assertIs(acceptance.status, AcceptanceStatus.PASS)

    def test_harness_without_expectations_yields_no_acceptance(self) -> None:
        """Missing verification is reported as absent, not as a pass."""
        harness = create_agent_harness()
        scope = RunScope(
            run_id="harness-empty",
            workspace=self.workspace,
            execution_profile=api_profile(),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(criterion(),),
        )
        scope.freeze()
        request = HarnessRequest(
            run_id="harness-empty",
            workspace=self.workspace,
            acceptance_criteria=(criterion(),),
            run_scope=scope,
        )
        verified, acceptance = harness.run_verification(
            request, lambda *a, **k: None
        )
        self.assertEqual(verified, ())
        self.assertIsNone(acceptance)


if __name__ == "__main__":
    unittest.main()
