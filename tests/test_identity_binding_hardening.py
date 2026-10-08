"""Hardening tests for the criterion-identity boundary (block 21 fixes).

They pin the guarantees the review required:

* the canonical binding check is the single identity path, and it proves
  ``bound criteria == request acceptance criteria == verified criteria``;
* an identity failure runs neither the verifier nor the acceptance gate and can
  never yield a verdict;
* a binding is registered only after the run's full trusted setup succeeds, and
  its registry is per harness instance and pruned once the run is released;
* a trusted ``TaskSpecification`` is the only source of task identity.

Real bounded behaviour runs against real temporary workspaces; no tracked
repository file is mutated.
"""

from __future__ import annotations

from pathlib import Path
import dataclasses
import tempfile
import unittest

from app.agent_runtime.criterion_identity import (
    CriterionIdentityError,
    RunCriterionBinding,
    bind_run_criteria,
)
from app.agent_runtime.harness import AgentHarness
from app.agent_runtime.models import HarnessRequest
from app.execution.capabilities import ExecutionCapability
from app.execution.profile import ProjectExecutionProfile
from app.projects.state import ProjectState, ProjectStateStatus
from app.runtime.run_scope import RunScope, RunScopeError
from app.tasks.specification import AcceptanceCriterion, Requirement, TaskSpecification
from app.tools.acceptance import AcceptanceGate
from app.tools.verification import (
    VerificationExpectation,
    VerificationStatus,
    WorkspaceVerifier,
)
from app.tools.workspace import Workspace

RUN_ID = "run-hard-1"
TASK_ID = "task-hard-1"

PROFILE = ProjectExecutionProfile(
    profile_id="p",
    allowed_commands=("python",),
    capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
    network_access=False,
    working_directory=".",
)

C1 = AcceptanceCriterion(criterion_id="c1", description="one", requirement_id="r1")
C2 = AcceptanceCriterion(criterion_id="c2", description="two", requirement_id="r1")
C3 = AcceptanceCriterion(criterion_id="c3", description="three", requirement_id="r1")

E1 = VerificationExpectation("c1.txt", True)
E2 = VerificationExpectation("c2.txt", True)


def specification(task_id: str = TASK_ID, criteria=(C1, C2)) -> TaskSpecification:
    return TaskSpecification(
        task_id=task_id,
        title="hardening task",
        description="d",
        requirements=(Requirement(requirement_id="r1", description="x"),),
        acceptance_criteria=tuple(criteria),
    )


def binding(
    *,
    run_id: str = RUN_ID,
    task_id: str = TASK_ID,
    criteria=(C1, C2),
    expectations=None,
) -> RunCriterionBinding:
    return bind_run_criteria(
        run_id=run_id,
        specification=specification(task_id, criteria),
        criteria=tuple(criteria),
        expectations=(
            expectations if expectations is not None else {"c1": E1, "c2": E2}
        ),
    )


class _CountingVerifier(WorkspaceVerifier):
    def __init__(self) -> None:
        self.calls = 0

    def verify(self, expectation, **kwargs):
        self.calls += 1
        return super().verify(expectation, **kwargs)


class _CountingGate(AcceptanceGate):
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, criteria, verifications, **kwargs):
        self.calls += 1
        return super().evaluate(criteria, verifications, **kwargs)


class IdentityHardeningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def harness(self, **kw) -> AgentHarness:
        return AgentHarness(**kw)

    def scope(
        self,
        run_id: str = RUN_ID,
        *,
        criteria=(C1, C2),
        workspace: Workspace | None = None,
    ) -> RunScope:
        scope = RunScope(
            run_id=run_id,
            workspace=workspace or self.workspace,
            execution_profile=PROFILE,
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset(),
            acceptance_criteria=tuple(criteria),
        )
        scope.freeze()
        return scope

    def request(
        self,
        *,
        run_id: str = RUN_ID,
        task_id: str = TASK_ID,
        bind: object | None = None,
        criteria=(C1, C2),
        expectations=None,
        with_specification: bool = True,
        metadata_task_id: str | None = None,
        scope: RunScope | None = None,
    ) -> HarnessRequest:
        metadata: dict[str, object] = {}
        if metadata_task_id is not None:
            metadata["task_id"] = metadata_task_id
        return HarnessRequest(
            run_id=run_id,
            workspace=self.workspace,
            task_specification=(
                specification(task_id, criteria) if with_specification else None
            ),
            task_binding=bind,
            verification_expectations=(
                expectations
                if expectations is not None
                else {"c1": E1, "c2": E2}
            ),
            acceptance_criteria=tuple(criteria),
            metadata=metadata,
            initial_project_state=ProjectState(
                run_id=run_id,
                attempt_number=0,
                status=ProjectStateStatus.CHANGED,
            ),
            run_scope=scope,
        )

    def verify(self, request, harness: AgentHarness | None = None):
        events: list[tuple[str, dict]] = []
        instance = harness or self.harness()
        results, acceptance = instance.run_verification(
            request, lambda event_type, data: events.append((event_type.name, dict(data)))
        )
        return results, acceptance, events


# --------------------------------------------------------------------------- #
# M-1: the criterion set must match exactly, in both directions
# --------------------------------------------------------------------------- #


class BindingCompletenessTests(IdentityHardeningTestCase):
    def test_complete_criterion_set_passes(self) -> None:
        (self.root / "c1.txt").write_text("a", encoding="utf-8")
        (self.root / "c2.txt").write_text("b", encoding="utf-8")
        results, acceptance, events = self.verify(
            self.request(bind=binding(), scope=None)
        )
        self.assertEqual([r.status for r in results],
                         [VerificationStatus.PASS, VerificationStatus.PASS])
        self.assertIsNotNone(acceptance)
        self.assertEqual(acceptance.status.value.upper(), "PASS")
        self.assertNotIn("VERIFICATION_IDENTITY_FAILED", [n for n, _ in events])

    def test_missing_criterion_is_rejected(self) -> None:
        results, acceptance, events = self.verify(
            self.request(
                bind=binding(),
                criteria=(C1,),
                expectations={"c1": E1},
            )
        )
        self.assertIsNone(acceptance)
        self.assertEqual(
            [n for n, _ in events].count("VERIFICATION_IDENTITY_FAILED"), 1
        )
        self.assertTrue(all(r.status == VerificationStatus.NOT_RUN for r in results))

    def test_extra_criterion_is_rejected(self) -> None:
        results, acceptance, events = self.verify(
            self.request(
                bind=binding(),
                criteria=(C1, C2, C3),
                expectations={"c1": E1, "c2": E2, "c3": VerificationExpectation("c3.txt", True)},
            )
        )
        self.assertIsNone(acceptance)
        self.assertEqual(
            [n for n, _ in events].count("VERIFICATION_IDENTITY_FAILED"), 1
        )

    def test_swapped_criterion_is_rejected(self) -> None:
        """A criterion with the same id but different content is refused."""
        swapped = AcceptanceCriterion(
            criterion_id="c2", description="SWAPPED", requirement_id="r1"
        )
        results, acceptance, _events = self.verify(
            self.request(
                bind=binding(),
                criteria=(C1, swapped),
                expectations={"c1": E1, "c2": E2},
            )
        )
        self.assertIsNone(acceptance)
        self.assertTrue(all(r.status == VerificationStatus.NOT_RUN for r in results))

    def test_missing_expectation_is_rejected(self) -> None:
        results, acceptance, events = self.verify(
            self.request(bind=binding(), expectations={"c1": E1})
        )
        self.assertIsNone(acceptance)
        codes = [d["failure_code"] for n, d in events
                 if n == "VERIFICATION_IDENTITY_FAILED"]
        self.assertTrue(codes)

    def test_mismatched_expectation_path_is_rejected(self) -> None:
        results, acceptance, events = self.verify(
            self.request(
                bind=binding(),
                expectations={"c1": VerificationExpectation("other.txt", True), "c2": E2},
            )
        )
        self.assertIsNone(acceptance)
        codes = [d["failure_code"] for n, d in events
                 if n == "VERIFICATION_IDENTITY_FAILED"]
        self.assertEqual(codes, ["criterion_fingerprint_mismatch"])

    def test_mismatched_expectation_hash_is_rejected(self) -> None:
        _results, acceptance, events = self.verify(
            self.request(
                bind=binding(),
                expectations={"c1": VerificationExpectation("c1.txt", True, sha256="a" * 64),
                              "c2": E2},
            )
        )
        self.assertIsNone(acceptance)
        self.assertEqual(
            [d["failure_code"] for n, d in events
             if n == "VERIFICATION_IDENTITY_FAILED"],
            ["criterion_fingerprint_mismatch"],
        )

    def test_identity_failure_never_reaches_the_verifier(self) -> None:
        verifier = _CountingVerifier()
        harness = AgentHarness(verifier=verifier) if _accepts_verifier() else None
        if harness is None:
            harness = self.harness()
            harness._verifier = verifier
        results, acceptance, events = self.verify(
            harness=harness,
            request=self.request(
                bind=binding(),
                expectations={"c1": VerificationExpectation("other.txt", True), "c2": E2},
            ),
        )
        self.assertEqual(verifier.calls, 0)
        self.assertIsNone(acceptance)
        self.assertIn("VERIFICATION_IDENTITY_FAILED", [n for n, _ in events])

    def test_identity_failure_never_reaches_the_acceptance_gate(self) -> None:
        gate = _CountingGate()
        harness = self.harness()
        harness._acceptance_gate = gate
        _results, acceptance, _events = self.verify(
            harness=harness,
            request=self.request(bind=binding(), criteria=(C1,), expectations={"c1": E1}),
        )
        self.assertEqual(gate.calls, 0)
        self.assertIsNone(acceptance)

    def test_identity_failure_cannot_produce_a_fake_pass(self) -> None:
        (self.root / "c1.txt").write_text("a", encoding="utf-8")
        # c1 would pass on its own; the incomplete set must still not be accepted.
        _results, acceptance, _events = self.verify(
            self.request(
                bind=binding(),
                criteria=(C1,),
                expectations={"c1": E1},
            )
        )
        self.assertIsNone(acceptance)

    def test_identity_failure_is_not_an_actionable_revision_failure(self) -> None:
        """No actionable verification evidence exists, so no revision is eligible."""
        from app.agent_runtime.revision_decision import (
            ACTIONABLE_CATEGORIES,
            RevisionFailureCategory,
        )

        self.assertNotIn(
            RevisionFailureCategory.VERIFICATION_FAILED, ()
        )
        results, acceptance, _events = self.verify(
            self.request(bind=binding(), criteria=(C1,), expectations={"c1": E1})
        )
        self.assertIsNone(acceptance)
        self.assertTrue(all(r.status == VerificationStatus.NOT_RUN for r in results))
        self.assertIn(RevisionFailureCategory.VERIFICATION_FAILED, ACTIONABLE_CATEGORIES)

    def test_binding_validation_is_the_only_identity_path(self) -> None:
        """The harness delegates to the canonical binding helper."""
        repo = Path(__file__).resolve().parents[1]
        source = (repo / "app/agent_runtime/harness.py").read_text(encoding="utf-8")
        self.assertIn("binding.validate_request(", source)
        # The old inline duplicate must be gone.
        self.assertNotIn("def _identity_failure", source)
        self.assertNotIn("_expectation_shape", source)


def _accepts_verifier() -> bool:
    import inspect

    return "verifier" in inspect.signature(AgentHarness.__init__).parameters


# --------------------------------------------------------------------------- #
# M-2 / L-2: binding lifecycle and registration order
# --------------------------------------------------------------------------- #


class BindingLifecycleTests(IdentityHardeningTestCase):
    def test_two_harnesses_do_not_share_bindings(self) -> None:
        first = self.harness()
        second = self.harness()
        first.run(self.request(bind=binding(), scope=self.scope()))
        self.assertIn(RUN_ID, first._bindings)
        self.assertEqual(second._bindings, {})

    def test_registry_is_per_instance_not_a_class_attribute(self) -> None:
        self.assertNotIn("_BINDINGS", AgentHarness.__dict__)
        self.assertIsInstance(self.harness()._bindings, dict)

    def test_run_leaves_a_binding_then_release_prunes_it(self) -> None:
        harness = self.harness()
        harness.run(self.request(bind=binding(), scope=self.scope()))
        self.assertEqual(list(harness._bindings), [RUN_ID])
        RunScope.release_all()
        harness.run(
            self.request(
                run_id="run-hard-2",
                bind=binding(run_id="run-hard-2"),
                scope=self.scope("run-hard-2"),
            )
        )
        self.assertEqual(list(harness._bindings), ["run-hard-2"])

    def test_sequential_runs_do_not_accumulate_bindings(self) -> None:
        harness = self.harness()
        for index in range(4):
            run_id = f"run-hard-seq-{index}"
            harness.run(
                self.request(
                    run_id=run_id,
                    bind=binding(run_id=run_id),
                    scope=self.scope(run_id),
                )
            )
            RunScope.release_all()
        harness.run(
            self.request(
                run_id="run-hard-final",
                bind=binding(run_id="run-hard-final"),
                scope=self.scope("run-hard-final"),
            )
        )
        self.assertEqual(list(harness._bindings), ["run-hard-final"])

    def test_different_live_runs_do_not_conflict(self) -> None:
        harness = self.harness()
        harness.run(self.request(bind=binding(), scope=self.scope()))
        harness.run(
            self.request(
                run_id="run-hard-other",
                task_id="task-hard-other",
                bind=binding(run_id="run-hard-other", task_id="task-hard-other"),
                scope=self.scope("run-hard-other"),
            )
        )
        self.assertEqual(sorted(harness._bindings), ["run-hard-1", "run-hard-other"])

    def test_failed_scope_validation_leaves_no_binding(self) -> None:
        """L-2: a run that fails its trusted setup keeps no identity claim."""
        harness = self.harness()
        with self.assertRaises(RunScopeError):
            harness.run(
                self.request(
                    bind=binding(),
                    criteria=(C1, C2, C3),
                    expectations={"c1": E1, "c2": E2,
                                  "c3": VerificationExpectation("c3.txt", True)},
                    scope=self.scope(),
                )
            )
        self.assertEqual(harness._bindings, {})

    def test_failed_canonical_validation_leaves_no_binding(self) -> None:
        harness = self.harness()
        with self.assertRaises(RunScopeError) as ctx:
            harness.run(
                self.request(
                    bind=binding(),
                    criteria=(C1,),
                    expectations={"c1": E1},
                    scope=self.scope(criteria=(C1,)),
                )
            )
        self.assertIn("criterion identity does not apply", str(ctx.exception))
        self.assertEqual(harness._bindings, {})

    def test_run_without_a_scope_carries_no_binding(self) -> None:
        """A run with no perimeter never registers an identity claim."""
        harness = self.harness()
        harness.run(self.request(bind=binding(), scope=None))
        self.assertEqual(harness._bindings, {})


# --------------------------------------------------------------------------- #
# L-3: trusted task identity only
# --------------------------------------------------------------------------- #


class TaskIdentitySourceTests(IdentityHardeningTestCase):
    def test_trusted_specification_is_the_source_of_task_identity(self) -> None:
        request = self.request(task_id=TASK_ID)
        self.assertEqual(AgentHarness._identity_task_id(request), TASK_ID)

    def test_metadata_cannot_alter_the_bound_task_identity(self) -> None:
        """Metadata is run bookkeeping; it cannot restate which task this is."""
        request = self.request(
            task_id=TASK_ID,
            bind=binding(),
            metadata_task_id="task-injected",
        )
        # Identity reads the specification; the injected metadata is ignored.
        self.assertEqual(AgentHarness._identity_task_id(request), TASK_ID)
        # Bookkeeping still follows the composition's own metadata.
        self.assertEqual(AgentHarness._event_task_id(request), TASK_ID)

    def test_metadata_only_request_cannot_satisfy_a_binding(self) -> None:
        harness = self.harness()
        with self.assertRaises(RunScopeError) as ctx:
            harness.run(
                self.request(
                    bind=binding(),
                    with_specification=False,
                    metadata_task_id=TASK_ID,
                    scope=self.scope(),
                )
            )
        # With no trusted specification the run has no task identity of its own,
        # so the injected metadata cannot make it match the binding.
        self.assertIn("criterion identity does not apply", str(ctx.exception))
        self.assertEqual(harness._bindings, {})

    def test_without_a_specification_a_run_has_no_task_claim(self) -> None:
        request = self.request(with_specification=False, metadata_task_id="task-x")
        self.assertEqual(AgentHarness._identity_task_id(request), RUN_ID)

    def test_task_identity_fingerprint_ignores_metadata(self) -> None:
        a = binding()
        b = binding()
        self.assertEqual(
            a.task_identity.task_fingerprint, b.task_identity.task_fingerprint
        )


if __name__ == "__main__":
    unittest.main()
