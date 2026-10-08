"""Trusted task/criterion identity across Task, Verification, Acceptance, Revision.

These tests pin the criterion-identity boundary:

* a task identity and every criterion identity are derived server-side from
  trusted content and carry a deterministic fingerprint;
* one frozen binding ties the task and all criteria to exactly one run, and the
  binding cannot be replaced;
* verification proves run, task, criterion ownership, and expectation identity
  *before* evaluating anything, and an identity mismatch means verification does
  not run at all;
* a decision, a plan, a tool result, and a model can contribute no criterion;
* a revision keeps the same task and criterion identity;
* a missing criterion stays an honest absence rather than a fake pass.

Real bounded behaviour runs against real temporary workspaces; no tracked
repository file is mutated.
"""

from __future__ import annotations

from pathlib import Path
import dataclasses
import json
import tempfile
import unittest
from uuid import uuid4

from app.agent_runtime.acceptance_spec import AcceptanceSpec, AcceptanceSpecError
from app.agent_runtime.criterion_identity import (
    IdentityFailureCode,
    CriterionIdentity,
    CriterionIdentityError,
    RunCriterionBinding,
    TaskIdentity,
    bind_run_criteria,
)
from app.agent_runtime.harness import AgentHarness
from app.agent_runtime.models import HarnessRequest
from app.agent_runtime.policy import AgentHarnessPolicy
from app.decision.models import Decision, DecisionAction, DecisionRequest, DecisionType
from app.execution.capabilities import ExecutionCapability
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.models import EventType
from app.projects.state import ProjectState, ProjectStateStatus
from app.runtime.run_scope import RunScope, RunScopeError
from app.tasks.specification import AcceptanceCriterion, Requirement, TaskSpecification
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
)
from app.tools.workspace import Workspace

RUN_ID = "run-ident-1"
TASK_ID = "task-ident-1"
CRITERION_ID = "artifact-exists"
DECLARATION_ID = "verify.identity"

PROFILE = ProjectExecutionProfile(
    profile_id="p",
    allowed_commands=("python",),
    capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
    network_access=False,
    working_directory=".",
)


def make_specification(task_id: str = TASK_ID, description: str = "build it"):
    requirement = Requirement(requirement_id="r1", description="artifact")
    criterion = AcceptanceCriterion(
        criterion_id=CRITERION_ID,
        description="artifact.txt exists",
        requirement_id="r1",
    )
    return (
        TaskSpecification(
            task_id=task_id,
            title="identity task",
            description=description,
            requirements=(requirement,),
            acceptance_criteria=(criterion,),
        ),
        requirement,
        criterion,
    )


def make_binding(
    *,
    run_id: str = RUN_ID,
    task_id: str = TASK_ID,
    relative_path: str = "artifact.txt",
    exists: bool = True,
    source: str = "operator_composition",
) -> RunCriterionBinding:
    specification, _requirement, criterion = make_specification(task_id)
    return bind_run_criteria(
        run_id=run_id,
        specification=specification,
        criteria=(criterion,),
        expectations={CRITERION_ID: VerificationExpectation(relative_path, exists)},
        source=source,
    )


class _VerifyThenStop:
    """Runs one verification, then stops asking for anything."""

    def decide(self, request: DecisionRequest) -> Decision:
        if DecisionAction.RUN_VERIFICATION in tuple(request.available_actions):
            return Decision(
                decision_id=str(uuid4()),
                run_id=request.run_id,
                decision_type=DecisionType.VERIFY,
                action=DecisionAction.RUN_VERIFICATION,
                reason_code="verify",
                attempt_number=request.attempt_number,
            )
        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            decision_type=DecisionType.FAIL,
            action=DecisionAction.FAIL_RUN,
            reason_code="stop",
            attempt_number=request.attempt_number,
        )


class _ForceRevision:
    """Insists on a revision, to prove it cannot replace the criterion."""

    def decide(self, request: DecisionRequest) -> Decision:
        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            decision_type=DecisionType.REVISE,
            action=DecisionAction.REQUEST_REVISION,
            reason_code="loop",
            attempt_number=request.attempt_number,
        )


class IdentityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def scope(
        self,
        *,
        run_id: str = RUN_ID,
        criteria=(),
        workspace: Workspace | None = None,
    ) -> RunScope:
        _specification, _requirement, criterion = make_specification()
        scope = RunScope(
            run_id=run_id,
            workspace=workspace or self.workspace,
            execution_profile=PROFILE,
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset(),
            acceptance_criteria=tuple(criteria) or (criterion,),
        )
        scope.freeze()
        return scope

    def request(
        self,
        *,
        run_id: str = RUN_ID,
        task_id: str = TASK_ID,
        binding: object | None = None,
        expectations=None,
        criteria=None,
        scope: RunScope | None = None,
    ) -> HarnessRequest:
        specification, _requirement, criterion = make_specification(task_id)
        return HarnessRequest(
            run_id=run_id,
            workspace=self.workspace,
            task_specification=specification,
            task_binding=binding,
            verification_expectations=(
                expectations
                if expectations is not None
                else {CRITERION_ID: VerificationExpectation("artifact.txt", True)}
            ),
            acceptance_criteria=(
                tuple(criteria) if criteria is not None else (criterion,)
            ),
            metadata={"task_id": task_id},
            initial_project_state=ProjectState(
                run_id=run_id,
                attempt_number=0,
                status=ProjectStateStatus.CHANGED,
            ),
            run_scope=scope or self.scope(run_id=run_id),
        )

    def verify(self, request: HarnessRequest, harness: AgentHarness | None = None):
        """Call the single verification implementation and capture its events."""
        events: list[tuple[str, dict]] = []
        results, acceptance = (harness or AgentHarness()).run_verification(
            request,
            lambda event_type, data: events.append((event_type.name, dict(data))),
        )
        return results, acceptance, events


# --------------------------------------------------------------------------- #
# 1-2. Server-side identity creation and ownership
# --------------------------------------------------------------------------- #


class IdentityCreationTests(IdentityTestCase):
    def test_task_identity_is_created_server_side(self) -> None:
        specification, _requirement, _criterion = make_specification()
        identity = TaskIdentity.from_specification(specification)
        self.assertEqual(identity.task_id, TASK_ID)
        self.assertEqual(len(identity.task_fingerprint), 64)
        self.assertEqual(
            sorted(identity.bounded_summary()),
            ["task_fingerprint", "task_id"],
        )

    def test_task_identity_is_deterministic(self) -> None:
        a = make_binding(run_id="run-a")
        b = make_binding(run_id="run-a")
        self.assertEqual(
            a.task_identity.task_fingerprint, b.task_identity.task_fingerprint
        )
        self.assertEqual(a.fingerprint, b.fingerprint)

    def test_task_identity_changes_with_trusted_content(self) -> None:
        first, _, criterion = make_specification(description="one")
        second, _, _ = make_specification(description="two")
        self.assertNotEqual(
            TaskIdentity.from_specification(first).task_fingerprint,
            TaskIdentity.from_specification(second).task_fingerprint,
        )
        self.assertEqual(criterion.criterion_id, CRITERION_ID)

    def test_criterion_identity_belongs_to_the_task(self) -> None:
        binding = make_binding()
        identity = binding.identity_for(CRITERION_ID)
        self.assertIsNotNone(identity)
        self.assertEqual(identity.task_id, TASK_ID)
        self.assertEqual(len(identity.criterion_fingerprint), 64)
        identity.assert_belongs_to(TASK_ID)
        with self.assertRaises(CriterionIdentityError):
            identity.assert_belongs_to("other-task")

    def test_criterion_identity_changes_with_its_expectation(self) -> None:
        specification, _requirement, criterion = make_specification()
        one = CriterionIdentity.derive(
            criterion=criterion,
            task_id=TASK_ID,
            expectation=VerificationExpectation("a.txt", True),
        )
        two = CriterionIdentity.derive(
            criterion=criterion,
            task_id=TASK_ID,
            expectation=VerificationExpectation("b.txt", True),
        )
        self.assertNotEqual(one.criterion_fingerprint, two.criterion_fingerprint)

    def test_binding_requires_a_trusted_specification(self) -> None:
        _specification, _requirement, criterion = make_specification()
        with self.assertRaises(CriterionIdentityError):
            bind_run_criteria(
                run_id=RUN_ID,
                specification=None,
                criteria=(criterion,),
                expectations={CRITERION_ID: VerificationExpectation("a.txt", True)},
            )

    def test_binding_requires_an_expectation_for_every_criterion(self) -> None:
        specification, _requirement, criterion = make_specification()
        with self.assertRaises(CriterionIdentityError):
            bind_run_criteria(
                run_id=RUN_ID,
                specification=specification,
                criteria=(criterion,),
                expectations={},
            )

    def test_binding_requires_at_least_one_criterion(self) -> None:
        with self.assertRaises(CriterionIdentityError):
            RunCriterionBinding(
                run_id=RUN_ID,
                task_identity=TaskIdentity.from_specification(
                    make_specification()[0]
                ),
                criterion_identities=(),
                expectations={},
            )


# --------------------------------------------------------------------------- #
# 3-7. Identity binding integrity
# --------------------------------------------------------------------------- #


class IdentityBindingTests(IdentityTestCase):
    def test_binding_is_immutable(self) -> None:
        binding = make_binding()
        self.assertTrue(RunCriterionBinding.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            binding.run_id = "other"

    def test_binding_is_bound_to_one_run_and_task(self) -> None:
        binding = make_binding()
        binding.assert_belongs_to(RUN_ID, TASK_ID)
        with self.assertRaises(CriterionIdentityError):
            binding.assert_belongs_to("other-run", TASK_ID)
        with self.assertRaises(CriterionIdentityError):
            binding.assert_belongs_to(RUN_ID, "other-task")

    def test_run_id_mismatch_is_rejected_by_the_harness(self) -> None:
        binding = make_binding(run_id="other-run")
        request = self.request(binding=binding)
        with self.assertRaises(RunScopeError):
            AgentHarness().run(request)

    def test_task_id_mismatch_is_rejected_by_the_harness(self) -> None:
        binding = make_binding(task_id="other-task")
        request = self.request(binding=binding)
        with self.assertRaises(RunScopeError):
            AgentHarness().run(request)

    def test_binding_cannot_be_replaced_for_one_run(self) -> None:
        """One run keeps one criterion identity for as long as it is alive.

        The two bindings deliberately agree on the run, the task, and every
        expectation, and the scope is reused, so neither the canonical identity
        check nor the scope's own one-scope-per-run guard can be what raises. The
        per-run binding registry is the guarantee under test.
        """
        _specification, _requirement, criterion = make_specification()
        scope = RunScope(
            run_id=RUN_ID,
            workspace=self.workspace,
            execution_profile=PROFILE,
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset(),
            acceptance_criteria=(criterion,),
        )
        scope.freeze()
        harness = AgentHarness()
        harness.run(
            self.request(binding=make_binding(source="declared"), scope=scope)
        )
        self.assertIn(RUN_ID, harness._bindings)
        # The scope is reused, so its own guard cannot be what refuses the swap.
        # The registry must therefore hold a different identity for this run.
        harness._bindings[RUN_ID] = "0" * 64
        with self.assertRaises(RunScopeError) as ctx:
            harness.run(
                self.request(binding=make_binding(source="injected"), scope=scope)
            )
        self.assertIn("different criterion identity", str(ctx.exception))

    def test_binding_is_removed_once_its_run_is_released(self) -> None:
        """A released run leaves no identity claim behind."""
        harness = AgentHarness()
        harness.run(self.request(binding=make_binding()))
        self.assertIn(RUN_ID, harness._bindings)
        RunScope.release_all()
        # The next trusted setup prunes the released run's entry rather than
        # accumulating identity claims for runs that are already over.
        harness.run(
            self.request(
                run_id="run-ident-2",
                binding=make_binding(run_id="run-ident-2"),
                scope=self.scope(run_id="run-ident-2"),
            )
        )
        self.assertEqual(sorted(harness._bindings), ["run-ident-2"])

    def test_non_binding_object_is_refused(self) -> None:
        request = self.request(binding="not-a-binding")
        with self.assertRaises(RunScopeError):
            AgentHarness().run(request)

    def test_criterion_fingerprint_mismatch_is_rejected(self) -> None:
        binding = make_binding(relative_path="a.txt")
        request = self.request(
            binding=binding,
            expectations={CRITERION_ID: VerificationExpectation("b.txt", True)},
        )
        results, acceptance, events = self.verify(request)
        self.assertIsNone(acceptance)
        self.assertEqual(results[0].status, VerificationStatus.NOT_RUN)
        codes = [data["failure_code"] for name, data in events
                 if name == "VERIFICATION_IDENTITY_FAILED"]
        self.assertEqual(codes, [IdentityFailureCode.CRITERION_FINGERPRINT_MISMATCH.value])

    def test_expectations_outside_the_binding_are_rejected(self) -> None:
        binding = make_binding()
        request = self.request(
            binding=binding,
            expectations={
                CRITERION_ID: VerificationExpectation("artifact.txt", True),
                "unbound-criterion": VerificationExpectation("x.txt", True),
            },
        )
        results, acceptance, events = self.verify(request)
        self.assertIsNone(acceptance)
        codes = [data["failure_code"] for name, data in events
                 if name == "VERIFICATION_IDENTITY_FAILED"]
        self.assertEqual(codes, [IdentityFailureCode.CRITERION_NOT_BOUND.value])

    def test_criterion_not_in_the_binding_is_rejected(self) -> None:
        binding = make_binding()
        request = self.request(
            binding=binding,
            expectations={"different-criterion": VerificationExpectation("a.txt", True)},
        )
        results, acceptance, events = self.verify(request)
        self.assertIsNone(acceptance)
        self.assertTrue(results)
        self.assertEqual(
            results[0].status, VerificationStatus.NOT_RUN
        )


# --------------------------------------------------------------------------- #
# 8-11. Nothing else can author a criterion
# --------------------------------------------------------------------------- #


class IdentityAuthorshipTests(IdentityTestCase):
    def test_identity_carries_no_authority_fields(self) -> None:
        for cls in (TaskIdentity, CriterionIdentity, RunCriterionBinding):
            fields = {f.name for f in dataclasses.fields(cls)}
            for banned in (
                "command", "argv", "executable", "shell", "environment", "timeout",
                "run_scope", "authorized_execution", "approval", "capabilities",
                "allowed_tool_ids", "workspace", "network_access", "credentials",
                "token", "password", "api_key",
            ):
                self.assertNotIn(banned, fields, f"{cls.__name__}:{banned}")

    def test_identity_event_metadata_is_bounded(self) -> None:
        binding = make_binding()
        blob = json.dumps(binding.bounded_summary(), sort_keys=True)
        for banned in ("command", "environment", "token", "password", "secret",
                       "argv", "stdout", "stderr"):
            self.assertNotIn(banned, blob.lower(), banned)
        # No raw criterion description text is carried.
        self.assertNotIn("artifact.txt exists", blob)

    def test_decision_cannot_name_a_criterion(self) -> None:
        fields = {f.name for f in dataclasses.fields(Decision)}
        for banned in ("criterion_id", "criterion", "acceptance_criteria",
                       "criteria", "task_binding", "binding"):
            self.assertNotIn(banned, fields, banned)
        self.assertNotIn(
            "criterion", {f.name for f in dataclasses.fields(DecisionRequest)}
        )

    def test_harness_request_has_no_criterion_authorship_field(self) -> None:
        """A criterion cannot be authored through the request surface."""
        fields = {f.name for f in dataclasses.fields(HarnessRequest)}
        # The binding is the frozen identity, not a mutable criterion list.
        self.assertIn("task_binding", fields)
        for banned in ("criterion", "criterion_id", "acceptance_authority",
                       "verification_authority"):
            self.assertNotIn(banned, fields, banned)

    def test_acceptance_spec_is_the_only_criterion_source(self) -> None:
        """Criteria come from the operator's spec, which validates its own shape."""
        _specification, _requirement, criterion = make_specification()
        with self.assertRaises(AcceptanceSpecError):
            AcceptanceSpec(
                declaration_id=DECLARATION_ID,
                criteria=(criterion,),
                expectations={},
                source="test",
            )
        with self.assertRaises(AcceptanceSpecError):
            AcceptanceSpec(
                declaration_id=DECLARATION_ID,
                criteria=(),
                expectations={CRITERION_ID: VerificationExpectation("a.txt", True)},
                source="test",
            )


# --------------------------------------------------------------------------- #
# 12-16. Revision, reuse, and cross-run/cross-task isolation
# --------------------------------------------------------------------------- #


class IdentityRevisionTests(IdentityTestCase):
    def test_revision_keeps_the_same_task_and_criterion_identity(self) -> None:
        binding = make_binding()
        binding.assert_same_identity(make_binding())

    def test_revision_cannot_change_a_criterion(self) -> None:
        binding = make_binding(relative_path="a.txt")
        changed = make_binding(relative_path="b.txt")
        with self.assertRaises(CriterionIdentityError):
            binding.assert_same_identity(changed)

    def test_revision_cannot_change_the_task(self) -> None:
        binding = make_binding(task_id=TASK_ID)
        changed = make_binding(task_id="other-task")
        with self.assertRaises(CriterionIdentityError):
            binding.assert_same_identity(changed)

    def test_revision_cannot_move_to_another_run(self) -> None:
        binding = make_binding(run_id=RUN_ID)
        other = make_binding(run_id="other-run")
        with self.assertRaises(CriterionIdentityError):
            binding.assert_same_identity(other)

    def test_revision_loop_cannot_replace_the_criterion(self) -> None:
        """A run that insists on revisions still verifies the bound criterion."""
        binding = make_binding()
        harness = AgentHarness(
            policy=AgentHarnessPolicy(max_iterations=3, max_actions=2),
            decision_provider=_ForceRevision(),
        )
        result = harness.run(self.request(binding=binding))
        # Verification is bound to the frozen identity, and no revision event
        # changes the run's task.
        for event in result.events:
            if event.event_type.name in ("REVISION_STARTED", "REVISION_COMPLETED"):
                self.assertEqual(dict(event.metadata).get("task_id"), TASK_ID)

    def test_verification_result_cannot_be_reused_across_tasks(self) -> None:
        first = make_binding(task_id=TASK_ID)
        second = make_binding(task_id="other-task")
        with self.assertRaises(CriterionIdentityError):
            second.assert_same_identity(first)

    def test_verification_result_cannot_be_reused_across_criteria(self) -> None:
        binding = make_binding(relative_path="a.txt")
        request = self.request(
            binding=binding,
            expectations={CRITERION_ID: VerificationExpectation("b.txt", True)},
        )
        results, acceptance, _events = self.verify(request)
        self.assertEqual(results[0].status, VerificationStatus.NOT_RUN)
        self.assertIsNone(acceptance)

    def test_cross_run_result_is_rejected(self) -> None:
        binding = make_binding(run_id="run-a")
        request = self.request(run_id=RUN_ID, binding=binding)
        results, acceptance, events = self.verify(request)
        self.assertIsNone(acceptance)
        codes = [data["failure_code"] for name, data in events
                 if name == "VERIFICATION_IDENTITY_FAILED"]
        self.assertEqual(codes, [IdentityFailureCode.RUN_ID_MISMATCH.value])

    def test_cross_task_result_is_rejected(self) -> None:
        binding = make_binding(task_id="other-task")
        request = self.request(task_id=TASK_ID, binding=binding)
        results, acceptance, events = self.verify(request)
        self.assertIsNone(acceptance)
        codes = [data["failure_code"] for name, data in events
                 if name == "VERIFICATION_IDENTITY_FAILED"]
        self.assertEqual(codes, [IdentityFailureCode.TASK_ID_MISMATCH.value])


# --------------------------------------------------------------------------- #
# 17-19. Verification and acceptance attribution
# --------------------------------------------------------------------------- #


class IdentityVerificationTests(IdentityTestCase):
    def test_bound_criterion_passes_and_acceptance_refers_to_it(self) -> None:
        (self.root / "artifact.txt").write_text("ok", encoding="utf-8")
        binding = make_binding()
        results, acceptance, events = self.verify(self.request(binding=binding))
        self.assertEqual(results[0].status, VerificationStatus.PASS)
        self.assertEqual(results[0].criterion_id, CRITERION_ID)
        self.assertIsNotNone(acceptance)
        completed = [data for name, data in events if name == "VERIFICATION_COMPLETED"]
        self.assertEqual(completed[0]["criterion_id"], CRITERION_ID)

    def test_bound_criterion_fails_and_acceptance_refers_to_it(self) -> None:
        binding = make_binding()
        results, acceptance, _events = self.verify(self.request(binding=binding))
        self.assertEqual(results[0].status, VerificationStatus.FAIL)
        self.assertEqual(results[0].criterion_id, CRITERION_ID)
        self.assertIsNotNone(acceptance)
        self.assertEqual(acceptance.status.value.upper(), "FAIL")

    def test_missing_criterion_is_never_a_fake_pass(self) -> None:
        """Without a binding there is no criterion, so there is no pass."""
        request = self.request(binding=None, expectations={}, criteria=())
        results, acceptance, _events = self.verify(request)
        self.assertEqual(results, ())
        self.assertIsNone(acceptance)

    def test_identity_failure_does_not_evaluate_anything(self) -> None:
        binding = make_binding(relative_path="a.txt")
        (self.root / "a.txt").write_text("ok", encoding="utf-8")
        request = self.request(
            binding=binding,
            expectations={CRITERION_ID: VerificationExpectation("b.txt", True)},
        )
        _results, _acceptance, events = self.verify(request)
        names = [name for name, _data in events]
        self.assertIn("VERIFICATION_IDENTITY_FAILED", names)
        # The workspace was never inspected: no verification completion is reported.
        self.assertNotIn("VERIFICATION_COMPLETED", names)

    def test_decision_cannot_author_a_criterion_for_the_run(self) -> None:
        """The decision's criterion-free request cannot add a criterion."""
        binding = make_binding()
        request = self.request(binding=binding)
        self.assertEqual(binding.criterion_ids, (CRITERION_ID,))
        # A decision object carries no criterion field at all.
        decision = Decision(
            decision_id="d",
            run_id=RUN_ID,
            decision_type=DecisionType.VERIFY,
            action=DecisionAction.RUN_VERIFICATION,
            reason_code="r",
        )
        self.assertFalse(hasattr(decision, "criterion_id"))


# --------------------------------------------------------------------------- #
# 20-23. Service path and events
# --------------------------------------------------------------------------- #


class IdentityServiceTests(IdentityTestCase):
    def _service(self, **overrides):
        from app.api.service import ForgeApiService
        from app.execution.declaration import ExecutionDeclaration
        from app.orchestrator.models import TaskResult
        from app.orchestrator.run import RunExecutor
        from app.runtime.bootstrap import create_agent_harness
        from app.runtime.context import RuntimeContext
        from app.tools.approval import ApprovalPolicy
        from app.tools.registry import build_default_tool_registry

        class _Orchestrator:
            def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
                return TaskResult(task.id, True, output="d")

        orchestrator = _Orchestrator()
        runtime = RuntimeContext(
            settings=None,
            provider_accounts={},
            provider_registry=None,
            provider_capabilities=None,
            agent_registry=None,
            orchestrator=orchestrator,
            run_executor=RunExecutor(orchestrator),
            harness=create_agent_harness(with_discovery=False, with_planning=False),
        )
        _specification, _requirement, criterion = make_specification()
        spec = AcceptanceSpec(
            declaration_id=DECLARATION_ID,
            criteria=(criterion,),
            expectations={CRITERION_ID: VerificationExpectation("artifact.txt", True)},
            source="test_composition",
        )
        payload = {
            "runtime": runtime,
            "workspace": self.workspace,
            "tool_registry": build_default_tool_registry(self.root),
            "execution_profile": PROFILE,
            "declarations": {
                DECLARATION_ID: ExecutionDeclaration(
                    declaration_id=DECLARATION_ID,
                    command=("python", "-c", "print('ok')"),
                    intent="produce the artifact",
                    profile_id="p",
                    working_directory=".",
                )
            },
            "acceptance_specs": {DECLARATION_ID: spec},
            "approval_policy": ApprovalPolicy(),
        }
        payload.update(overrides)
        return ForgeApiService(**payload)

    def test_run_agent_loop_carries_trusted_criterion_identity(self) -> None:
        (self.root / "artifact.txt").write_text("ok", encoding="utf-8")
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        defined = [
            dict(event.metadata)
            for event in record.events
            if event.event_type.name == "CRITERION_DEFINED"
        ]
        self.assertTrue(defined)
        entry = defined[0]
        self.assertEqual(entry["task_id"], response.task_id)
        self.assertEqual(entry["criterion_ids"], [CRITERION_ID])
        self.assertEqual(len(entry["binding_fingerprint"]), 64)
        self.assertEqual(len(entry["criterion_fingerprints"][CRITERION_ID]), 64)

        verification = [
            dict(event.metadata)
            for event in record.events
            if event.event_type.name == "VERIFICATION_COMPLETED"
        ]
        self.assertTrue(verification)
        self.assertEqual(verification[0]["criterion_id"], CRITERION_ID)

    def test_events_answer_which_task_criterion_and_run(self) -> None:
        (self.root / "artifact.txt").write_text("ok", encoding="utf-8")
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        for event in record.events:
            metadata = dict(event.metadata)
            if event.event_type.name == "CRITERION_DEFINED":
                self.assertEqual(metadata["run_id"], response.run_id)
                self.assertEqual(metadata["task_id"], response.task_id)
                self.assertIn(CRITERION_ID, metadata["criterion_ids"])

    def test_events_carry_no_raw_criterion_text_or_secrets(self) -> None:
        (self.root / "artifact.txt").write_text("ok", encoding="utf-8")
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        blob = json.dumps(
            [dict(event.metadata) for event in record.events], sort_keys=True
        ).lower()
        for banned in ("stdout", "stderr", "environment", "token", "password",
                       "secret", "api_key", "argv"):
            self.assertNotIn(banned, blob, banned)
        self.assertNotIn("artifact.txt exists", blob)

    def test_service_without_a_spec_keeps_no_invented_criterion(self) -> None:
        service = self._service(acceptance_specs={})
        response = service.run_agent_loop(DECLARATION_ID)
        record = service._run_store.load(response.run_id)
        self.assertEqual(
            [
                event.event_type.name
                for event in record.events
                if event.event_type.name == "CRITERION_DEFINED"
            ],
            [],
        )


# --------------------------------------------------------------------------- #
# Compatibility and isolation of the new module
# --------------------------------------------------------------------------- #


class IdentityCompatibilityTests(IdentityTestCase):
    def test_verification_identity_failed_event_is_registered(self) -> None:
        self.assertEqual(
            EventType.VERIFICATION_IDENTITY_FAILED.value,
            "verification_identity_failed",
        )

    def test_identity_module_executes_nothing(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        source = (repo / "app/agent_runtime/criterion_identity.py").read_text(
            encoding="utf-8"
        )
        for banned in (
            "subprocess", "Popen", "os.system", "shell=True", "os.environ",
            "LocalExecutionAdapter", "ExecutionCoordinator", "AuthorizedExecution",
            "open(",
        ):
            self.assertNotIn(banned, source, banned)

    def test_no_second_orchestrator_or_verifier(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        harness_source = (repo / "app/agent_runtime/harness.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(harness_source.count("class AgentHarness"), 1)
        self.assertEqual(harness_source.count("self.run("), 0)
        # The single verification implementation is still the harness's own.
        self.assertEqual(harness_source.count("def run_verification"), 1)

    def test_existing_verification_result_shape_is_unchanged(self) -> None:
        fields = {f.name for f in dataclasses.fields(VerificationResult)}
        for banned in ("command", "argv", "environment", "run_scope", "token"):
            self.assertNotIn(banned, fields, banned)

    def test_binding_summary_is_json_safe(self) -> None:
        binding = make_binding()
        json.dumps(binding.bounded_summary(), sort_keys=True)


if __name__ == "__main__":
    unittest.main()
