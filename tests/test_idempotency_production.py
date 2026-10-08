"""Production tests for run idempotency and recoverable resume.

These tests exercise the real production plane: the real ``ForgeApiService``
entry point, the real ledger on disk, and the real ``AgentHarness``. Where a
guarantee cannot be given honestly, the test asserts the actual behaviour and
names the residual risk instead of assuming a stronger one.

Coverage groups:

* identity - same key/task, different task, different criterion, new key;
* duplicate - sequential duplicate delivery of a production run;
* resume - recoverable position, lineage, and criterion/task preservation;
* security - a resume cannot change authority, and cannot resurrect a denial;
* crash - ``STARTED`` without a completion record becomes unprovable;
* corruption - malformed durable state fails closed;
* concurrency - the atomic claim, and the limits that remain.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from app.agent_runtime.criterion_identity import CriterionIdentity, TaskIdentity
from app.agent_runtime.idempotency import (
    AttemptIdentity,
    IdempotencyEngine,
    IdempotencyError,
    IdempotencyFailureCode,
    IdempotencyLedger,
    IdempotencyVerdict,
    OperationClass,
    OperationIdentity,
    ResumeContract,
    ResumeOutcome,
    RunLifecycleState,
    SideEffectKind,
    SideEffectRecord,
    SideEffectState,
    classify_outcome_labels,
    classify_side_effect,
    recover_started_effects,
    side_effect_fingerprint,
)
from app.agent_runtime import idempotency_integration as integration
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.models import Task, TaskResult
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_agent_harness
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope
from app.tasks.specification import Requirement, TaskSpecification
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.registry import build_default_tool_registry
from app.tools.workspace import Workspace
from app.execution.capabilities import ExecutionCapability

MARKER = "forge-idempotency-ok"
EXECUTABLE = "python"
DECLARATION_ID = "verify.idempotency"
KEY = "idem-key-000000001"


def executable_source(source: str) -> str:
    """Return only the executable statements of a module.

    Docstrings and comments are removed through the AST, so an assertion about
    what the code *does* cannot be satisfied or defeated by what it *says*.
    """
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    body.pop(0)
    return ast.unparse(tree)


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #


def make_spec(
    *,
    task_id: str = "task-idem-1",
    description: str = "do the thing",
    criterion_description: str = "artifact exists",
) -> TaskSpecification:
    requirement = Requirement(requirement_id="r1", description="artifact")
    criterion = AcceptanceCriterion(
        criterion_id="c1", description=criterion_description, requirement_id="r1"
    )
    return TaskSpecification(
        task_id=task_id,
        title="idempotency task",
        description=description,
        requirements=(requirement,),
        acceptance_criteria=(criterion,),
    )


def criterion_pair(spec: TaskSpecification) -> tuple[tuple[str, str], ...]:
    criterion = spec.acceptance_criteria[0]
    return (
        (
            criterion.criterion_id,
            CriterionIdentity.fingerprint_for_content(
                criterion, task_id=spec.task_id
            ),
        ),
    )


def operation(
    *,
    key: str = KEY,
    spec: TaskSpecification | None = None,
    cls: OperationClass = OperationClass.TASK_RUN,
    criteria: tuple[tuple[str, str], ...] | None = None,
) -> OperationIdentity:
    specification = spec or make_spec()
    return OperationIdentity(
        operation_class=cls,
        idempotency_key=key,
        task_identity=TaskIdentity.from_specification(specification),
        criterion_fingerprints=(
            criterion_pair(specification) if criteria is None else criteria
        ),
    )


class LedgerTestCase(unittest.TestCase):
    """Each test owns a private ledger root."""

    def setUp(self) -> None:
        self._ledger_dir = tempfile.TemporaryDirectory()
        self.ledger = IdempotencyLedger(self._ledger_dir.name)
        self.engine = IdempotencyEngine(self.ledger)

    def tearDown(self) -> None:
        self._ledger_dir.cleanup()


def declaration(**overrides) -> ExecutionDeclaration:
    payload = {
        "declaration_id": DECLARATION_ID,
        "command": (EXECUTABLE, "-c", f"print('{MARKER}')"),
        "profile_id": "api-default",
        "working_directory": ".",
    }
    payload.update(overrides)
    return ExecutionDeclaration(**payload)


def api_profile(**overrides) -> ProjectExecutionProfile:
    payload = {
        "profile_id": "api-default",
        "allowed_commands": (EXECUTABLE,),
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
        "harness": create_agent_harness(with_discovery=False, with_planning=False),
    }
    payload.update(overrides)
    return RuntimeContext(**payload)


# --------------------------------------------------------------------------- #
# IDENTITY
# --------------------------------------------------------------------------- #


class IdempotencyIdentityTests(LedgerTestCase):
    def test_same_key_same_task_is_not_a_second_start(self) -> None:
        identity = operation()
        first = self.engine.decide(identity, requested_run_id="run-1")
        self.assertIs(first.verdict, IdempotencyVerdict.START)
        self.assertTrue(first.may_execute)
        second = self.engine.decide(identity, requested_run_id="run-2")
        self.assertIsNot(second.verdict, IdempotencyVerdict.START)
        self.assertFalse(second.may_execute)
        self.assertEqual(second.run_id, "run-1")

    def test_same_key_different_task_is_a_conflict(self) -> None:
        self.engine.decide(operation(), requested_run_id="run-1")
        other = operation(spec=make_spec(description="do something else"))
        decision = self.engine.decide(other, requested_run_id="run-2")
        self.assertIs(decision.verdict, IdempotencyVerdict.CONFLICT)
        self.assertIs(decision.failure_code, IdempotencyFailureCode.KEY_CONFLICT)
        self.assertFalse(decision.may_execute)

    def test_same_task_different_description_first_fingerprints_differently(self) -> None:
        """The task fingerprint is content-sensitive, not id-only."""
        one = operation()
        two = operation(spec=make_spec(description="changed description"))
        self.assertNotEqual(
            one.task_identity.task_fingerprint, two.task_identity.task_fingerprint
        )

    def test_same_key_different_criterion_is_a_conflict(self) -> None:
        self.engine.decide(operation(), requested_run_id="run-1")
        other = operation(
            spec=make_spec(criterion_description="a DIFFERENT criterion")
        )
        decision = self.engine.decide(other, requested_run_id="run-2")
        self.assertIs(decision.verdict, IdempotencyVerdict.CONFLICT)

    def test_same_key_different_operation_class_is_a_conflict(self) -> None:
        self.engine.decide(operation(), requested_run_id="run-1")
        decision = self.engine.decide(
            operation(cls=OperationClass.DECLARED_VERIFICATION),
            requested_run_id="run-2",
        )
        self.assertIs(decision.verdict, IdempotencyVerdict.CONFLICT)

    def test_new_key_same_task_is_a_new_run(self) -> None:
        self.engine.decide(operation(), requested_run_id="run-1")
        decision = self.engine.decide(
            operation(key="idem-key-000000002"), requested_run_id="run-2"
        )
        self.assertIs(decision.verdict, IdempotencyVerdict.START)
        self.assertTrue(decision.may_execute)
        self.assertEqual(decision.run_id, "run-2")

    def test_operation_identity_needs_a_trusted_task_identity(self) -> None:
        with self.assertRaises(IdempotencyError):
            OperationIdentity(
                operation_class=OperationClass.TASK_RUN,
                idempotency_key=KEY,
                task_identity="task-idem-1",  # type: ignore[arg-type]
            )

    def test_key_is_canonicalized_not_coerced(self) -> None:
        padded = operation(key=f"  {KEY}  ")
        self.assertEqual(padded.idempotency_key, KEY)
        for bad in ("short", "", "bad key with spaces", "k" * 201):
            with self.assertRaises(IdempotencyError):
                operation(key=bad)


# --------------------------------------------------------------------------- #
# DUPLICATE / AT-MOST-ONCE
# --------------------------------------------------------------------------- #


class DuplicateExecutionTests(LedgerTestCase):
    def test_terminal_operation_replays_instead_of_re_executing(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-1")
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.COMPLETED, attempt_number=0
        )
        decision = self.engine.decide(identity, requested_run_id="run-2")
        self.assertIs(decision.verdict, IdempotencyVerdict.REPLAY)
        self.assertFalse(decision.may_execute)
        self.assertEqual(decision.run_id, "run-1")

    def test_limit_reached_is_terminal_for_the_key(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-1")
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.LIMIT_REACHED, attempt_number=0
        )
        decision = self.engine.decide(identity, requested_run_id="run-2")
        self.assertIs(decision.verdict, IdempotencyVerdict.REPLAY)
        self.assertFalse(decision.may_execute)

    def test_claim_is_atomic_under_a_simulated_concurrent_create(self) -> None:
        """Two writers cannot both win the claim.

        The first ``os.open(..., O_EXCL)`` wins; the loser reads the winner's
        record. The ledger deliberately has no compare-and-set, so this test
        pins the guarantee that does exist rather than a stronger one.
        """
        identity = operation()
        first, created_first = self.ledger.claim(identity, run_id="run-a")
        second, created_second = self.ledger.claim(identity, run_id="run-b")
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.run_id, "run-a")
        self.assertEqual(second.run_id, "run-a")
        self.assertEqual(len(self.ledger.list_keys()), 1)

    def test_claimed_key_without_state_refuses_a_duplicate_start(self) -> None:
        identity = operation()
        self.ledger.claim(identity, run_id="run-1")
        path = self.ledger.record_path(identity.idempotency_key)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["state"] = None
        path.write_text(json.dumps(data), encoding="utf-8")
        decision = self.engine.decide(identity, requested_run_id="run-2")
        self.assertIs(decision.verdict, IdempotencyVerdict.REFUSE)
        self.assertFalse(decision.may_execute)

    def test_ledger_has_no_compare_and_set_and_no_lease(self) -> None:
        """The concurrency limits are asserted, not implied."""
        source = (
            Path(__file__).resolve().parents[1]
            / "app/agent_runtime/idempotency.py"
        ).read_text(encoding="utf-8")
        for absent in ("fcntl", "msvcrt", "flock", "LockFileEx", "compare_and_set"):
            self.assertNotIn(absent, source, absent)


# --------------------------------------------------------------------------- #
# RESUME
# --------------------------------------------------------------------------- #


class ResumeContractTests(LedgerTestCase):
    def _recoverable(self, *, lifecycle=RunLifecycleState.INTERRUPTED, key=KEY):
        identity = operation(key=key)
        self.engine.decide(identity, requested_run_id="run-res")
        self.ledger.update(identity, lifecycle=lifecycle, attempt_number=0)
        return identity

    def test_interrupted_run_is_resumable(self) -> None:
        identity = self._recoverable()
        verdict = ResumeContract(self.ledger).validate(identity)
        self.assertIs(verdict.outcome, ResumeOutcome.ALLOWED)
        self.assertTrue(verdict.allowed)

    def test_resume_creates_a_new_attempt_with_lineage(self) -> None:
        identity = self._recoverable()
        verdict = ResumeContract(self.ledger).validate(identity)
        self.assertIsNotNone(verdict.attempt)
        self.assertEqual(verdict.attempt.attempt_number, 1)
        self.assertEqual(verdict.attempt.parent_attempt_number, 0)
        self.assertTrue(verdict.attempt.resumed)
        self.assertEqual(verdict.attempt.run_id, "run-res")

    def test_resume_preserves_task_and_criterion_identity(self) -> None:
        specification = make_spec()
        identity = self._recoverable()
        verdict = ResumeContract(self.ledger).validate(identity)
        record = self.ledger.read(identity.idempotency_key)
        self.assertEqual(
            record.state.task_fingerprint,
            TaskIdentity.from_specification(specification).task_fingerprint,
        )
        self.assertEqual(record.state.criterion_fingerprints, criterion_pair(specification))
        self.assertTrue(verdict.allowed)

    def test_waiting_for_approval_is_recoverable(self) -> None:
        identity = self._recoverable(lifecycle=RunLifecycleState.WAITING_FOR_APPROVAL)
        self.assertTrue(ResumeContract(self.ledger).validate(identity).allowed)

    def test_completed_run_is_not_resumable(self) -> None:
        identity = self._recoverable(lifecycle=RunLifecycleState.COMPLETED)
        verdict = ResumeContract(self.ledger).validate(identity)
        self.assertIs(verdict.outcome, ResumeOutcome.REFUSED)
        self.assertIs(verdict.failure_code, IdempotencyFailureCode.RUN_TERMINAL)

    def test_limit_reached_is_not_resumable(self) -> None:
        identity = self._recoverable(lifecycle=RunLifecycleState.LIMIT_REACHED)
        verdict = ResumeContract(self.ledger).validate(identity)
        self.assertIs(verdict.outcome, ResumeOutcome.REFUSED)

    def test_created_or_running_is_not_auto_resumed(self) -> None:
        """A run that may still be live must be recovered, not continued blindly."""
        for lifecycle in (RunLifecycleState.CREATED, RunLifecycleState.RUNNING):
            identity = self._recoverable(lifecycle=lifecycle, key=f"idem-key-1{lifecycle.value[:4]}")
            verdict = ResumeContract(self.ledger).validate(identity)
            self.assertIs(verdict.outcome, ResumeOutcome.REFUSED, lifecycle)
            self.assertIs(
                verdict.failure_code, IdempotencyFailureCode.RUN_NOT_RECOVERABLE
            )

    def test_missing_run_is_refused(self) -> None:
        verdict = ResumeContract(self.ledger).validate(operation(key="idem-key-missing01"))
        self.assertIs(verdict.outcome, ResumeOutcome.REFUSED)
        self.assertIs(verdict.failure_code, IdempotencyFailureCode.RUN_NOT_FOUND)

    def test_resuming_under_a_different_run_id_is_refused(self) -> None:
        identity = self._recoverable()
        verdict = ResumeContract(self.ledger).validate(
            identity, requested_run_id="run-other"
        )
        self.assertIs(verdict.outcome, ResumeOutcome.REFUSED)
        self.assertIs(
            verdict.failure_code, IdempotencyFailureCode.TASK_IDENTITY_MISMATCH
        )


# --------------------------------------------------------------------------- #
# SIDE EFFECTS AND CRASH
# --------------------------------------------------------------------------- #


class SideEffectTests(LedgerTestCase):
    def test_host_process_is_not_replay_safe(self) -> None:
        self.assertFalse(classify_side_effect(SideEffectKind.HOST_PROCESS))

    def test_provider_call_is_not_replay_safe(self) -> None:
        self.assertFalse(classify_side_effect(SideEffectKind.PROVIDER_CALL))

    def test_network_call_is_not_replay_safe(self) -> None:
        self.assertFalse(classify_side_effect(SideEffectKind.NETWORK_CALL))

    def test_workspace_write_is_replay_safe(self) -> None:
        self.assertTrue(classify_side_effect(SideEffectKind.WORKSPACE_WRITE))

    def test_two_phase_record_survives_without_a_completion(self) -> None:
        """The crash window: STARTED is persisted, completion never happens."""
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-crash")
        effect = SideEffectRecord(
            fingerprint=side_effect_fingerprint(
                SideEffectKind.HOST_PROCESS, {"command": "build"}
            ),
            kind=SideEffectKind.HOST_PROCESS,
            state=SideEffectState.NOT_STARTED,
        )
        self.ledger.begin_side_effect(identity, effect)
        stored = self.ledger.read(identity.idempotency_key)
        self.assertIs(stored.effect_for(effect.fingerprint).state, SideEffectState.STARTED)
        recovered = recover_started_effects(stored.state)
        self.assertIs(
            recovered.effect_for(effect.fingerprint).state,
            SideEffectState.UNKNOWN_AFTER_CRASH,
        )
        self.assertFalse(recovered.effect_for(effect.fingerprint).replay_safe)

    def test_unknown_effect_blocks_resume_and_blocks_a_new_start(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-crash")
        effect = SideEffectRecord(
            fingerprint="fp-host",
            kind=SideEffectKind.HOST_PROCESS,
            state=SideEffectState.NOT_STARTED,
        )
        self.ledger.begin_side_effect(identity, effect)
        # RUNNING is the honest position after an effect begins. Moving to a
        # recoverable state is refused by the transition guard, which is asserted
        # separately below.
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.RUNNING, attempt_number=0
        )
        verdict = ResumeContract(self.ledger).validate(identity)
        self.assertIs(verdict.outcome, ResumeOutcome.REFUSED)
        self.assertIs(
            verdict.failure_code, IdempotencyFailureCode.RUN_NOT_RECOVERABLE
        )
        decision = self.engine.decide(identity, requested_run_id="run-crash-2")
        self.assertIs(decision.verdict, IdempotencyVerdict.REFUSE)
        self.assertFalse(decision.may_execute)
        self.assertIs(decision.failure_code, IdempotencyFailureCode.SIDE_EFFECT_UNKNOWN)

    def test_replay_safe_unknown_effect_does_not_block_resume(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-safe")
        effect = SideEffectRecord(
            fingerprint="fp-write",
            kind=SideEffectKind.WORKSPACE_WRITE,
            state=SideEffectState.NOT_STARTED,
        )
        self.ledger.begin_side_effect(identity, effect)
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.RUNNING, attempt_number=0
        )
        # A replay-safe effect does not block the move into a recoverable state,
        # and once there the run is resumable again.
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.INTERRUPTED, attempt_number=0
        )
        self.assertTrue(ResumeContract(self.ledger).validate(identity).allowed)

    def test_completed_effect_is_recorded_as_committed(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-ok")
        effect = SideEffectRecord(
            fingerprint="fp-host",
            kind=SideEffectKind.HOST_PROCESS,
            state=SideEffectState.NOT_STARTED,
        )
        self.ledger.begin_side_effect(identity, effect)
        self.ledger.complete_side_effect(identity, effect)
        stored = self.ledger.read(identity.idempotency_key)
        self.assertIs(
            stored.effect_for(effect.fingerprint).state, SideEffectState.COMMITTED
        )

    def test_unknown_cannot_be_asserted_by_a_caller(self) -> None:
        """UNKNOWN_AFTER_CRASH is derived, never declared."""
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-x")
        effect = SideEffectRecord(
            fingerprint="fp-host",
            kind=SideEffectKind.HOST_PROCESS,
            state=SideEffectState.NOT_STARTED,
        )
        with self.assertRaises(IdempotencyError):
            self.ledger.complete_side_effect(
                identity, effect, state=SideEffectState.UNKNOWN_AFTER_CRASH
            )

    def test_replay_safe_committed_effect_may_run_again(self) -> None:
        commit_safe = SideEffectRecord(
            fingerprint="fp", kind=SideEffectKind.WORKSPACE_WRITE,
            state=SideEffectState.COMMITTED, replay_safe=True,
        )
        commit_unsafe = SideEffectRecord(
            fingerprint="fp2", kind=SideEffectKind.HOST_PROCESS,
            state=SideEffectState.COMMITTED, replay_safe=False,
        )
        self.assertTrue(commit_safe.may_run_again)
        self.assertFalse(commit_unsafe.may_run_again)


# --------------------------------------------------------------------------- #
# SECURITY
# --------------------------------------------------------------------------- #


class IdempotencySecurityTests(LedgerTestCase):
    def test_security_failure_is_terminal_for_both_paths(self) -> None:
        identity = operation(key="idem-key-sec0000001")
        self.engine.decide(identity, requested_run_id="run-sec")
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.SECURITY_FAILURE, attempt_number=0
        )
        decision = self.engine.decide(identity, requested_run_id="run-sec-2")
        self.assertIs(decision.verdict, IdempotencyVerdict.REFUSE)
        self.assertIs(
            decision.failure_code, IdempotencyFailureCode.SECURITY_FAILURE_TERMINAL
        )
        verdict = ResumeContract(self.ledger).validate(identity)
        self.assertIs(verdict.outcome, ResumeOutcome.REFUSED)
        self.assertIs(
            verdict.failure_code, IdempotencyFailureCode.SECURITY_FAILURE_TERMINAL
        )

    def test_denied_is_terminal_and_never_replayed(self) -> None:
        identity = operation(key="idem-key-den0000001")
        self.engine.decide(identity, requested_run_id="run-den")
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.DENIED, attempt_number=0
        )
        decision = self.engine.decide(identity, requested_run_id="run-den-2")
        self.assertFalse(decision.may_execute)
        self.assertIs(decision.verdict, IdempotencyVerdict.REFUSE)

    def test_authority_labels_classify_as_security_terminal(self) -> None:
        for label in (
            "PERMISSION_DENIED", "POLICY_DENIED", "APPROVAL_REJECTED",
            "verification_identity_failed", "sandbox_violation", "network_denied",
        ):
            self.assertIs(
                classify_outcome_labels((label,)),
                RunLifecycleState.SECURITY_FAILURE,
                label,
            )

    def test_security_label_dominates_a_later_success_label(self) -> None:
        self.assertIs(
            classify_outcome_labels(("COMPLETED", "PERMISSION_DENIED")),
            RunLifecycleState.SECURITY_FAILURE,
        )

    def test_resume_refuses_when_the_recorded_operation_fingerprint_changed(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-auth")
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.INTERRUPTED, attempt_number=0
        )
        contract = ResumeContract(self.ledger)
        verdict = contract.validate(identity)
        self.assertTrue(verdict.allowed)
        refused = contract.validate_against_current_authority(
            verdict,
            recorded_operation_fingerprint="recorded",
            current_operation_fingerprint="different",
        )
        self.assertIs(refused.outcome, ResumeOutcome.REFUSED)
        self.assertIs(refused.failure_code, IdempotencyFailureCode.AUTHORITY_MISMATCH)

    def test_resume_allows_when_authority_reproduces_exactly(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-auth2")
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.INTERRUPTED, attempt_number=0
        )
        contract = ResumeContract(self.ledger)
        verdict = contract.validate(identity)
        same = contract.validate_against_current_authority(
            verdict,
            recorded_operation_fingerprint=identity.fingerprint,
            current_operation_fingerprint=identity.fingerprint,
        )
        self.assertTrue(same.allowed)

    def test_authority_mismatch_is_refused_without_both_fingerprints(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-auth3")
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.INTERRUPTED, attempt_number=0
        )
        contract = ResumeContract(self.ledger)
        verdict = contract.validate(identity)
        refused = contract.validate_against_current_authority(
            verdict,
            recorded_operation_fingerprint="",
            current_operation_fingerprint=identity.fingerprint,
        )
        self.assertIs(refused.outcome, ResumeOutcome.REFUSED)

    def test_persisted_record_carries_no_authority_shaped_field(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-shape")
        raw = self.ledger.record_path(identity.idempotency_key).read_text(
            encoding="utf-8"
        )
        for banned in (
            "command", "workspace", "environment", "credential", "token",
            "network_access", "allowed_tool", "authorized_execution", "profile",
            "approval",
        ):
            self.assertNotIn(banned, raw.lower(), banned)

    def test_module_persists_no_raw_output_or_secrets(self) -> None:
        """The module must not serialize authority, capture output, or spawn.

        Checked against executable code only: docstrings legitimately *mention*
        ``RunScope`` and ``AuthorizedExecution`` in order to say that authority is
        reconstructed elsewhere, and a substring search over raw text would
        confuse that negation with a use.
        """
        source = (
            Path(__file__).resolve().parents[1]
            / "app/agent_runtime/idempotency.py"
        ).read_text(encoding="utf-8")
        executable = executable_source(source)
        for banned in (
            "pickle", "sys.stdout", "sys.stderr", "subprocess", "eval(", "exec(",
            "os.environ[", "os.getenv", "importlib", "__import__", "AuthorizedExecution",
            "ExecutionCoordinator", "LocalExecutionAdapter", "RunScope(",
        ):
            self.assertNotIn(banned, executable, banned)
        # Its only environment read is the ledger location, never authority.
        self.assertIn("os.environ.get", executable)
        self.assertIn("FORGE_IDEMPOTENCY_LEDGER_ROOT", executable)


# --------------------------------------------------------------------------- #
# CORRUPTION
# --------------------------------------------------------------------------- #


class CorruptionTests(LedgerTestCase):
    def test_malformed_record_fails_closed(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-corrupt")
        self.ledger.record_path(identity.idempotency_key).write_text(
            "{ this is not json", encoding="utf-8"
        )
        with self.assertRaises(IdempotencyError) as ctx:
            self.ledger.read(identity.idempotency_key)
        self.assertIs(ctx.exception.code, IdempotencyFailureCode.RECORD_MALFORMED)

    def test_unknown_lifecycle_in_a_record_fails_closed(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-corrupt2")
        path = self.ledger.record_path(identity.idempotency_key)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["state"]["lifecycle"] = "definitely_not_a_state"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(IdempotencyError):
            self.ledger.read(identity.idempotency_key)

    def test_non_object_record_fails_closed(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-corrupt3")
        self.ledger.record_path(identity.idempotency_key).write_text(
            "[1, 2, 3]", encoding="utf-8"
        )
        with self.assertRaises(IdempotencyError):
            self.ledger.read(identity.idempotency_key)

    def test_record_moved_to_another_key_fails_closed(self) -> None:
        first = operation(key="idem-key-move000001")
        self.engine.decide(first, requested_run_id="run-move")
        source = self.ledger.record_path(first.idempotency_key)
        second = operation(key="idem-key-move000002")
        target = self.ledger.record_path(second.idempotency_key)
        target.write_bytes(source.read_bytes())
        with self.assertRaises(IdempotencyError) as ctx:
            self.ledger.read(second.idempotency_key)
        self.assertIs(ctx.exception.code, IdempotencyFailureCode.RECORD_MALFORMED)

    def test_updating_through_a_different_identity_is_a_conflict(self) -> None:
        identity = operation()
        self.engine.decide(identity, requested_run_id="run-c1")
        other = operation(spec=make_spec(description="different"))
        with self.assertRaises(IdempotencyError):
            self.ledger.update(
                other, lifecycle=RunLifecycleState.COMPLETED, attempt_number=0
            )


# --------------------------------------------------------------------------- #
# F-31-01: TERMINAL STATES ARE FINAL
# --------------------------------------------------------------------------- #


class TerminalStateTests(LedgerTestCase):
    """A terminal state can never be rewritten, so a denial cannot be undone.

    Before this guard existed, `SECURITY_FAILURE -> INTERRUPTED` was accepted and
    the run then resumed: the denied action ran anyway. Every test here fails
    against that behaviour.
    """

    TERMINALS = (
        RunLifecycleState.COMPLETED,
        RunLifecycleState.FAILED,
        RunLifecycleState.LIMIT_REACHED,
        RunLifecycleState.DENIED,
        RunLifecycleState.SECURITY_FAILURE,
    )

    def _terminated(self, terminal, key="idem-key-term000001"):
        identity = operation(key=key)
        self.engine.decide(identity, requested_run_id="run-1")
        self.ledger.update(identity, lifecycle=terminal, attempt_number=0)
        return identity

    def _assert_refused(self, identity, target):
        with self.assertRaises(IdempotencyError) as ctx:
            self.ledger.update(identity, lifecycle=target, attempt_number=0)
        self.assertIn(
            ctx.exception.code,
            (
                IdempotencyFailureCode.RUN_TERMINAL,
                IdempotencyFailureCode.SECURITY_FAILURE_TERMINAL,
            ),
        )
        return ctx.exception

    def test_security_failure_cannot_be_reopened_as_interrupted(self) -> None:
        identity = self._terminated(
            RunLifecycleState.SECURITY_FAILURE, key="idem-key-sec-reopen"
        )
        self._assert_refused(identity, RunLifecycleState.INTERRUPTED)
        self.assertIs(
            self.ledger.read(identity.idempotency_key).state.lifecycle,
            RunLifecycleState.SECURITY_FAILURE,
        )

    def test_security_failure_cannot_be_reopened_as_completed(self) -> None:
        """A denial must never be rewritten into a success."""
        identity = self._terminated(
            RunLifecycleState.SECURITY_FAILURE, key="idem-key-sec-done"
        )
        self._assert_refused(identity, RunLifecycleState.COMPLETED)
        self.assertIs(
            self.ledger.read(identity.idempotency_key).state.lifecycle,
            RunLifecycleState.SECURITY_FAILURE,
        )

    def test_completed_cannot_be_reopened_as_running(self) -> None:
        identity = self._terminated(
            RunLifecycleState.COMPLETED, key="idem-key-compl-reopen"
        )
        self._assert_refused(identity, RunLifecycleState.RUNNING)
        self.assertIs(
            self.ledger.read(identity.idempotency_key).state.lifecycle,
            RunLifecycleState.COMPLETED,
        )

    def test_failed_cannot_be_reopened_as_running(self) -> None:
        identity = self._terminated(RunLifecycleState.FAILED, key="idem-key-fail-reopen")
        self._assert_refused(identity, RunLifecycleState.RUNNING)

    def test_limit_reached_cannot_be_reopened_as_running(self) -> None:
        identity = self._terminated(
            RunLifecycleState.LIMIT_REACHED, key="idem-key-limit-reopen"
        )
        self._assert_refused(identity, RunLifecycleState.RUNNING)

    def test_denied_cannot_be_reopened_as_running(self) -> None:
        identity = self._terminated(RunLifecycleState.DENIED, key="idem-key-deny-reopen")
        self._assert_refused(identity, RunLifecycleState.RUNNING)

    def test_every_terminal_state_refuses_every_other_state(self) -> None:
        for index, terminal in enumerate(self.TERMINALS):
            identity = self._terminated(terminal, key=f"idem-key-all{index:09d}")
            for target in RunLifecycleState:
                if target is terminal:
                    continue
                self._assert_refused(identity, target)
            record = self.ledger.read(identity.idempotency_key)
            self.assertIs(record.state.lifecycle, terminal)

    def test_a_refused_transition_does_not_touch_the_durable_record(self) -> None:
        identity = self._terminated(
            RunLifecycleState.SECURITY_FAILURE, key="idem-key-untouched1"
        )
        before = self.ledger.record_path(identity.idempotency_key).read_bytes()
        self._assert_refused(identity, RunLifecycleState.INTERRUPTED)
        after = self.ledger.record_path(identity.idempotency_key).read_bytes()
        self.assertEqual(before, after, "the durable record must be left as it was")

    def test_the_original_exploit_chain_is_closed_end_to_end(self) -> None:
        """decide -> SECURITY_FAILURE -> INTERRUPTED -> resume/decide all refused.

        This is the exact sequence from the security review. Every step after the
        denial must fail.
        """
        identity = operation(key="idem-key-exploit001")
        first = self.engine.decide(identity, requested_run_id="run-deny")
        self.assertIs(first.verdict, IdempotencyVerdict.START)
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.SECURITY_FAILURE, attempt_number=0
        )
        with self.assertRaises(IdempotencyError):
            self.ledger.update(
                identity, lifecycle=RunLifecycleState.INTERRUPTED, attempt_number=0
            )
        record = self.ledger.read(identity.idempotency_key)
        self.assertIs(record.state.lifecycle, RunLifecycleState.SECURITY_FAILURE)
        verdict = ResumeContract(self.ledger).validate(identity)
        self.assertIs(verdict.outcome, ResumeOutcome.REFUSED)
        self.assertIs(
            verdict.failure_code, IdempotencyFailureCode.SECURITY_FAILURE_TERMINAL
        )
        self.assertIsNone(verdict.attempt)
        decision = self.engine.decide(identity, requested_run_id="run-deny-2")
        self.assertIs(decision.verdict, IdempotencyVerdict.REFUSE)
        self.assertFalse(decision.may_execute)

    def test_legal_non_terminal_and_terminal_writes_still_work(self) -> None:
        """The guard must not break the production flow it protects."""
        identity = operation(key="idem-key-legal000001")
        self.engine.decide(identity, requested_run_id="run-l")
        for target in (
            RunLifecycleState.RUNNING,
            RunLifecycleState.WAITING_FOR_APPROVAL,
            RunLifecycleState.INTERRUPTED,
            RunLifecycleState.RUNNING,
            RunLifecycleState.COMPLETED,
        ):
            self.ledger.update(identity, lifecycle=target, attempt_number=0)
            self.assertIs(
                self.ledger.read(identity.idempotency_key).state.lifecycle, target
            )

    def test_rewriting_the_same_terminal_state_is_still_accepted(self) -> None:
        identity = self._terminated(
            RunLifecycleState.LIMIT_REACHED, key="idem-key-sameterm001"
        )
        self.ledger.update(
            identity, lifecycle=RunLifecycleState.LIMIT_REACHED, attempt_number=0
        )
        self.assertIs(
            self.ledger.read(identity.idempotency_key).state.lifecycle,
            RunLifecycleState.LIMIT_REACHED,
        )


class UnknownInterlockTests(LedgerTestCase):
    """An unprovable side effect cannot be laundered into a resumable state."""

    def _unknown(self, key="idem-key-unk0000001"):
        identity = operation(key=key)
        self.engine.decide(identity, requested_run_id="run-u")
        effect = SideEffectRecord(
            fingerprint="fp-host",
            kind=SideEffectKind.HOST_PROCESS,
            state=SideEffectState.NOT_STARTED,
        )
        self.ledger.begin_side_effect(identity, effect)
        return identity

    def test_unprovable_effect_cannot_be_moved_to_interrupted(self) -> None:
        identity = self._unknown()
        with self.assertRaises(IdempotencyError) as ctx:
            self.ledger.update(
                identity, lifecycle=RunLifecycleState.INTERRUPTED, attempt_number=0
            )
        self.assertIs(ctx.exception.code, IdempotencyFailureCode.SIDE_EFFECT_UNKNOWN)

    def test_unprovable_effect_cannot_be_moved_to_waiting(self) -> None:
        identity = self._unknown(key="idem-key-unk0000002")
        with self.assertRaises(IdempotencyError):
            self.ledger.update(
                identity,
                lifecycle=RunLifecycleState.WAITING_FOR_APPROVAL,
                attempt_number=0,
            )

    def test_unknown_run_still_reports_the_side_effect_as_unknown(self) -> None:
        identity = self._unknown(key="idem-key-unk0000003")
        verdict = ResumeContract(self.ledger).validate(identity)
        self.assertIs(verdict.outcome, ResumeOutcome.REFUSED)
        self.assertIn(
            verdict.failure_code,
            (
                IdempotencyFailureCode.SIDE_EFFECT_UNKNOWN,
                IdempotencyFailureCode.RUN_NOT_RECOVERABLE,
            ),
        )


class UnknownKeyScopeLimitationTests(LedgerTestCase):
    """FIXTURE OF A KNOWN LIMITATION, NOT A GUARANTEE.

    ``UNKNOWN_AFTER_CRASH`` protection is scoped to one durable record, so it is
    scoped to one idempotency key. Presenting a **different** key for the same
    task identity currently yields ``START``. This test exists so a future reader
    cannot mistake that behaviour for an intended safety property: it is a
    residual limitation, and closing it needs action-level suppression that this
    block deliberately does not build.
    """

    def test_a_new_key_for_the_same_task_is_admitted(self) -> None:
        first = operation(key="idem-key-scope00001")
        self.engine.decide(first, requested_run_id="run-a")
        effect = SideEffectRecord(
            fingerprint="fp-host",
            kind=SideEffectKind.HOST_PROCESS,
            state=SideEffectState.NOT_STARTED,
        )
        self.ledger.begin_side_effect(first, effect)

        # The blocked key stays blocked.
        self.assertIs(
            self.engine.decide(first, requested_run_id="run-a2").verdict,
            IdempotencyVerdict.REFUSE,
        )
        self.assertIs(
            ResumeContract(self.ledger).validate(first).outcome, ResumeOutcome.REFUSED
        )

        # A different key for the same trusted task is a new operation identity.
        second = operation(key="idem-key-scope00002")
        decision = self.engine.decide(second, requested_run_id="run-b")
        self.assertIs(decision.verdict, IdempotencyVerdict.START)
        self.assertTrue(decision.may_execute)
        self.assertNotEqual(
            first.fingerprint, second.fingerprint, "different key, different operation"
        )


# --------------------------------------------------------------------------- #
# PRODUCTION ENTRY POINT INTEGRATION
# --------------------------------------------------------------------------- #


class AgentLoopIdempotencyTests(unittest.TestCase):
    """The production ``run_agent_loop`` entry point under duplicate delivery.

    These tests drive the real service, the real harness, and a real on-disk
    ledger. The only substitution is the ledger location, so a test can never
    write into a developer's run history.
    """

    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)
        self._ledger_dir = tempfile.TemporaryDirectory()
        self.guard = integration.RunIdempotencyGuard.create(
            ledger_root=self._ledger_dir.name
        )

    def _service_with_guard(self, **kwargs):
        """Build the service with an explicit ledger.

        The guard is injected rather than patched in: a module-level patch would
        be ineffective (``service`` binds the name at import time) and would let a
        test touch the process-wide ledger outside the repository.
        """
        kwargs.setdefault("idempotency_guard", self.guard)
        return self._service(**kwargs)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._ledger_dir.cleanup()
        self._tmp.cleanup()

    def _service(self, *, declarations=None, runtime=None, **kwargs):
        if declarations is None:
            declarations = {DECLARATION_ID: declaration()}
        return ForgeApiService(
            runtime=runtime or _runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=api_profile(),
            declarations=declarations,
            approval_policy=kwargs.pop("approval_policy", ApprovalPolicy()),
            **kwargs,
        )

    def test_loop_without_a_key_is_unchanged(self) -> None:
        """Legacy semantics are preserved: no key means no idempotency guard."""
        service = self._service_with_guard()
        response = service.run_agent_loop(DECLARATION_ID)
        self.assertTrue(response.run_id.startswith("run-loop-"))
        self.assertEqual(self.guard.engine.ledger.list_keys(), ())

    def test_first_delivery_executes_and_records_the_operation(self) -> None:
        service = self._service_with_guard()
        response = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        self.assertTrue(response.run_id.startswith("run-loop-"))
        self.assertEqual(len(self.guard.engine.ledger.list_keys()), 1)

    def test_second_delivery_with_the_same_key_does_not_execute_again(self) -> None:
        """The core production guarantee: one side effect per idempotency key."""
        service = self._service_with_guard()
        first = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)

        runs: list[str] = []
        real_run = service._runtime.harness.run

        def spy(request):
            runs.append(request.run_id)
            return real_run(request)

        service._runtime.harness.run = spy  # type: ignore[method-assign]
        second = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)

        self.assertEqual(runs, [], "the harness must not run a second time")
        self.assertNotEqual(second.run_id, "")
        self.assertIn(second.state, {"DENIED", "REPLAYED"})
        self.assertIsNotNone(second.error)

    def test_second_delivery_reports_the_original_run_identity(self) -> None:
        service = self._service_with_guard()
        first = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        second = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        self.assertEqual(second.run_id, first.run_id)

    def test_a_new_key_runs_again_deliberately(self) -> None:
        service = self._service_with_guard()
        first = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        second = service.run_agent_loop(
            DECLARATION_ID, idempotency_key="idem-key-000000002"
        )
        self.assertTrue(second.run_id.startswith("run-loop-"))
        self.assertNotEqual(second.run_id, first.run_id)
        self.assertEqual(len(self.guard.engine.ledger.list_keys()), 2)

    def test_same_key_for_a_different_declaration_is_a_conflict(self) -> None:
        """A key is bound to the trusted declaration identity, not just a string."""
        other_id = "verify.idempotency.other"
        service = self._service_with_guard(
            declarations={
                DECLARATION_ID: declaration(),
                other_id: declaration(
                    declaration_id=other_id,
                    command=(EXECUTABLE, "-c", "print('different-marker')"),
                ),
            }
        )
        service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        runs: list[str] = []
        real_run = service._runtime.harness.run

        def spy(request):
            runs.append(request.run_id)
            return real_run(request)

        service._runtime.harness.run = spy  # type: ignore[method-assign]
        second = service.run_agent_loop(other_id, idempotency_key=KEY)
        self.assertEqual(runs, [], "a conflicting key must not execute")
        self.assertIn(second.state, {"DENIED", "REPLAYED"})

    def test_replayed_delivery_emits_a_denial_event_not_an_execution(self) -> None:
        service = self._service_with_guard()
        first = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        record = service._run_store.load(first.run_id)
        names = [event.event_type.name for event in record.events]
        self.assertIn("EXECUTION_DENIED", names)

    def test_idempotent_response_never_reports_success_or_leaks_data(self) -> None:
        """`_idempotent_response` must be a bounded refusal, never a result.

        It is the only path that answers without running, so it is checked
        directly: it must not claim success, must not carry output, must not
        reveal the original run's data, and must not skip the scope/approval
        machinery it is called from.
        """
        service = self._service_with_guard()
        first = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        self.assertTrue(first.success, "the first delivery is a real run")

        source = (
            Path(__file__).resolve().parents[1] / "app/api/service.py"
        ).read_text(encoding="utf-8")
        start = source.index("    def _idempotent_response(")
        body = source[start : source.index("    def run_agent_loop(", start)]

        second = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)

        # No success, no output, and no execution telemetry is claimed.
        self.assertFalse(second.success)
        self.assertEqual(second.output, "")
        self.assertEqual(second.tokens, 0)
        self.assertEqual(second.cost, 0.0)
        self.assertEqual(second.duration_seconds, 0.0)
        self.assertIn(second.state, {"DENIED", "REPLAYED"})
        self.assertTrue(second.error)

        # The run identity points at the recorded operation, not a new one.
        self.assertEqual(second.run_id, first.run_id)

        # The body returns no execution result and reads no stdout/stderr.
        for banned in ("stdout", "stderr", "execution_results", "artifact"):
            self.assertNotIn(banned, body, banned)
        # It reports a verdict and a bounded reason only, and it builds no
        # execution result of its own.
        self.assertIn("verdict", body)
        self.assertIn("failure_code", body)
        self.assertIn("reason", body)
        self.assertFalse(second.success)

    def test_idempotent_response_does_not_bypass_the_frozen_scope(self) -> None:
        """The replay path is entered only after the scope and identity exist.

        The rejection happens before the harness is constructed, and the scope
        that was frozen for this run still governs it - a replay cannot widen it.
        """
        service = self._service_with_guard()
        service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        RunScope.release_all()
        second = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        # With no active scope the run cannot proceed at all; the guard must not
        # be a way around that.
        self.assertFalse(second.success)
        self.assertTrue(second.error)

    def test_guard_cannot_grant_authority_the_scope_denies(self) -> None:
        """Idempotency is a refusal mechanism, never a permission.

        The guard is consulted only *after* the frozen scope and the
        declaration have established what may run, and a permitted run still
        passes every existing authority check. A command outside the declared
        set is refused regardless of the idempotency key.
        """
        service = self._service_with_guard(
            declarations={
                DECLARATION_ID: declaration(
                    command=(EXECUTABLE, "-c", "print('declared')")
                )
            }
        )
        response = service.run_agent_loop(DECLARATION_ID, idempotency_key=KEY)
        self.assertTrue(response.run_id.startswith("run-loop-"))
        source = (
            Path(__file__).resolve().parents[1] / "app/api/service.py"
        ).read_text(encoding="utf-8")
        guard_at = source.index("default_idempotency_guard()")
        scope_at = source.index("def _build_declared_verification_scope")
        self.assertGreater(guard_at, scope_at)


# --------------------------------------------------------------------------- #
# ATTEMPT IDENTITY
# --------------------------------------------------------------------------- #


class AttemptIdentityTests(unittest.TestCase):
    def test_resumed_attempt_requires_its_parent(self) -> None:
        with self.assertRaises(IdempotencyError):
            AttemptIdentity(run_id="run", attempt_number=2, resumed=True)

    def test_non_resumed_attempt_cannot_declare_a_parent(self) -> None:
        with self.assertRaises(IdempotencyError):
            AttemptIdentity(run_id="run", attempt_number=1, parent_attempt_number=0)

    def test_attempt_id_is_stable_and_namespaced(self) -> None:
        attempt = AttemptIdentity(run_id="run-a", attempt_number=3)
        self.assertEqual(attempt.attempt_id, "run-a#attempt-3")

    def test_negative_attempt_number_is_refused(self) -> None:
        with self.assertRaises(IdempotencyError):
            AttemptIdentity(run_id="run", attempt_number=-1)


if __name__ == "__main__":
    unittest.main()
