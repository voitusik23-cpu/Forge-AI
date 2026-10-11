"""Focused tests for read-time classification of persisted run records.

These tests pin **observed behaviour**, never the presence of a class or a string
constant. Every test uses its own ``RunStore(root=<temp dir>)``: the default store
root is a shared per-user location and the idempotency ledger lives beside it, so
no test here may touch it.

The behaviour under test is the trustworthiness of a durable run record. A record
proves only what its writer observed at the moment of the write; it carries no
heartbeat, no owner identity, no lease, and no expiry. These tests therefore
assert that a record claiming to be in progress is **not** reported as an active
success, and that an unknown, contradictory, or refused record is never dressed up
as one.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.orchestrator.models import EventType, Run, RunState, Task
from app.runtime.run_store import (
    ExecutionClassification,
    RunStateSnapshot,
    RunStore,
)

# The exact key set ``PersistedRun.summary()`` has always returned. This is a
# compatibility contract, so it is written out literally and not derived from the
# implementation.
_EXPECTED_SUMMARY_KEYS = frozenset(
    {
        "run_id",
        "state",
        "status",
        "task_id",
        "attempt_number",
        "completed_attempt_indexes",
        "project_state",
        "accounting",
        "event_count",
        "last_sequence_number",
        "updated_at",
        "interrupted",
    }
)


class _IsolatedStoreTestCase(unittest.TestCase):
    """Base case that pins every test to a temporary, private storage root."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = RunStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ------------------------------------------------------
    def _write(
        self,
        run_id: str,
        *,
        state: str = "",
        status: str = "",
        interrupted: bool = False,
        accounting: dict | None = None,
        project_state: dict | None = None,
        completed_attempt_indexes: tuple[int, ...] = (),
    ) -> None:
        """Write one snapshot through the store's own writer, then read it back."""
        self.store.write_state(
            RunStateSnapshot(
                run_id=run_id,
                task_id=f"task-{run_id}",
                state=state,
                status=status,
                attempt_number=1,
                completed_attempt_indexes=completed_attempt_indexes,
                project_state=project_state,
                accounting=accounting,
                event_count=2,
                updated_at="2026-10-06T00:00:00+00:00",
                interrupted=interrupted,
            )
        )

    def _write_raw_state(self, run_id: str, payload: dict) -> None:
        """Hand-write ``state.json`` so the on-disk shape is explicit."""
        directory = self.store.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        self.store.state_path(run_id).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _classify(self, run_id: str):
        """Classify through a FRESH store, so nothing is served from memory."""
        return RunStore(self.root).classify_record(run_id)


class TerminalSuccessClassificationTests(_IsolatedStoreTestCase):
    def test_terminal_success_record_reads_back_as_terminal_success(self) -> None:
        self._write("run-ok", state="COMPLETED", status="SUCCESS")

        observed = self._classify("run-ok")

        self.assertEqual(observed.outcome, ExecutionClassification.TERMINAL_SUCCESS)
        self.assertTrue(observed.is_terminal)
        self.assertTrue(observed.is_success)
        # The underlying snapshot is unchanged by classification.
        snapshot = RunStore(self.root).read_state("run-ok")
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.status, "SUCCESS")
        record = RunStore(self.root).load("run-ok")
        self.assertEqual(record.classification().outcome, observed.outcome)


class TerminalFailureClassificationTests(_IsolatedStoreTestCase):
    def test_terminal_failure_record_reads_back_as_terminal_failure(self) -> None:
        self._write("run-fail", state="FAILED", status="FAILED")

        observed = self._classify("run-fail")

        self.assertEqual(observed.outcome, ExecutionClassification.TERMINAL_FAILURE)
        self.assertTrue(observed.is_terminal)
        self.assertFalse(observed.is_success)


class WaitingForApprovalClassificationTests(_IsolatedStoreTestCase):
    def test_waiting_record_is_reported_as_waiting_not_failure_not_success(self) -> None:
        self._write("run-wait", state="WAITING_FOR_APPROVAL", status="WAITING")

        observed = self._classify("run-wait")

        self.assertEqual(observed.outcome, ExecutionClassification.WAITING)
        self.assertFalse(observed.is_success)
        self.assertFalse(observed.is_terminal)
        self.assertNotEqual(observed.outcome, ExecutionClassification.TERMINAL_FAILURE)
        self.assertNotEqual(observed.outcome, ExecutionClassification.UNKNOWN)

    def test_waiting_is_recognised_from_the_outcome_label_alone(self) -> None:
        # ``APPROVAL_WAITING`` is an ExecutionOutcomeStatus label, not a RunState.
        self._write("run-wait-label", state="", status="APPROVAL_WAITING")

        observed = self._classify("run-wait-label")

        self.assertEqual(observed.outcome, ExecutionClassification.WAITING)
        self.assertFalse(observed.is_success)


class InterruptedRecordClassificationTests(_IsolatedStoreTestCase):
    def test_explicitly_interrupted_record_is_reported_as_interrupted(self) -> None:
        self._write(
            "run-interrupted",
            state="RUNNING",
            status="RUNNING",
            interrupted=True,
        )

        observed = self._classify("run-interrupted")

        self.assertEqual(observed.outcome, ExecutionClassification.INDETERMINATE)
        self.assertIn("interrupted=True", observed.evidence)
        self.assertFalse(observed.is_success)
        self.assertFalse(observed.is_terminal)

    def test_interruption_claim_is_never_read_as_success(self) -> None:
        # A record that claims both success and interruption is not trustworthy:
        # the interruption claim is itself a statement that the recorded work was
        # cut short, so the pair cannot be reported as an active success.
        self._write(
            "run-interrupted-claimed-success",
            state="COMPLETED",
            status="SUCCESS",
            interrupted=True,
        )

        observed = self._classify("run-interrupted-claimed-success")

        self.assertEqual(observed.outcome, ExecutionClassification.INDETERMINATE)
        self.assertFalse(observed.is_success)
        self.assertIn("interrupted=True", observed.evidence)


class InProgressClaimIsIndeterminateTests(_IsolatedStoreTestCase):
    def test_a_record_claiming_progress_is_indeterminate_not_running(self) -> None:
        # This is the measured defect: a crashed writer and a live writer produce
        # byte-identical records, so "RUNNING" on disk is not evidence of a live run.
        self._write("run-running", state="RUNNING", status="RUNNING")

        observed = self._classify("run-running")

        self.assertEqual(observed.outcome, ExecutionClassification.INDETERMINATE)
        self.assertFalse(observed.is_success)
        self.assertFalse(observed.is_terminal)
        self.assertNotEqual(observed.outcome, ExecutionClassification.TERMINAL_SUCCESS)

    def test_the_reason_is_derived_from_the_record_not_hard_coded(self) -> None:
        self._write("run-running", state="RUNNING", status="RUNNING")
        self._write("run-revising", state="REVISING", status="REVISING")

        running = self._classify("run-running")
        revising = self._classify("run-revising")

        # Both are indeterminate, but the recorded claim differs, so the detail
        # must differ too: it names the value actually found on disk.
        self.assertEqual(running.outcome, revising.outcome)
        self.assertEqual(running.reason, revising.reason)
        self.assertNotEqual(running.detail, revising.detail)
        self.assertIn("RUNNING", running.detail)
        self.assertIn("REVISING", revising.detail)

    def test_a_record_with_no_outcome_snapshot_at_all_is_unknown(self) -> None:
        # Only events were written: no snapshot proves any outcome.
        self.store.append_event(EventType.ENGINEERING_RUN_STARTED, {}, run_id="run-events")

        observed = self._classify("run-events")

        self.assertEqual(observed.outcome, ExecutionClassification.UNKNOWN)
        self.assertFalse(observed.is_success)
        self.assertIn("snapshot=absent", observed.evidence)


class RefusalIsNeverSuccessTests(_IsolatedStoreTestCase):
    def test_a_denied_status_is_never_reported_as_success(self) -> None:
        cases = (
            ("deny-generic", "FAILED", "DENIED"),
            ("deny-policy", "FAILED", "POLICY_DENIED"),
            ("deny-permission", "", "PERMISSION_DENIED"),
            ("deny-approval", "", "APPROVAL_REJECTED"),
            ("deny-execution-error", "FAILED", "EXECUTION_ERROR"),
        )
        for run_id, state, status in cases:
            with self.subTest(run_id=run_id):
                self._write(run_id, state=state, status=status)
                observed = self._classify(run_id)
                self.assertFalse(
                    observed.is_success,
                    f"{run_id} reported SUCCESS for {state!r}/{status!r}",
                )
                self.assertNotEqual(
                    observed.outcome, ExecutionClassification.TERMINAL_SUCCESS
                )

    def test_a_refusal_keeps_its_own_category_distinct_from_plain_failure(self) -> None:
        self._write("deny-category", state="FAILED", status="POLICY_DENIED")

        observed = self._classify("deny-category")

        self.assertEqual(observed.outcome, ExecutionClassification.TERMINAL_REFUSED)
        self.assertNotEqual(observed.outcome, ExecutionClassification.TERMINAL_FAILURE)
        self.assertFalse(observed.is_success)


class TimeoutAndCancellationAreExpressibleTests(_IsolatedStoreTestCase):
    """CANCELLED/TIMEOUT are expressible without touching the RunState enum."""

    def test_timeout_is_classified_as_timeout_and_not_as_success(self) -> None:
        self._write("run-timeout", state="FAILED", status="EXECUTION_TIMEOUT")

        observed = self._classify("run-timeout")

        self.assertEqual(observed.outcome, ExecutionClassification.TERMINAL_TIMEOUT)
        self.assertTrue(observed.is_terminal)
        self.assertFalse(observed.is_success)

    def test_cancellation_is_classified_as_cancelled_and_not_as_success(self) -> None:
        self._write("run-cancelled", state="FAILED", status="CANCELLED")

        observed = self._classify("run-cancelled")

        self.assertEqual(observed.outcome, ExecutionClassification.TERMINAL_CANCELLED)
        self.assertTrue(observed.is_terminal)
        self.assertFalse(observed.is_success)

    def test_the_runstate_enum_still_has_exactly_its_six_members(self) -> None:
        # The classification is a read-side vocabulary. ``RunState`` is compared by
        # identity across the code base, so its membership is a public contract and
        # must not have grown a CANCELLED or TIMEOUT member.
        self.assertEqual(
            [member.name for member in RunState],
            [
                "CREATED",
                "RUNNING",
                "WAITING_FOR_APPROVAL",
                "REVISING",
                "COMPLETED",
                "FAILED",
            ],
        )


class UnknownFieldIsNotZeroTests(_IsolatedStoreTestCase):
    def test_missing_state_and_status_are_unknown_not_success(self) -> None:
        self._write("run-empty")

        observed = self._classify("run-empty")

        self.assertEqual(observed.outcome, ExecutionClassification.UNKNOWN)
        self.assertFalse(observed.is_success)
        self.assertFalse(observed.is_known)
        self.assertFalse(observed.is_terminal)

    def test_unrecognised_labels_are_unknown_not_success(self) -> None:
        self._write("run-unrecognised", state="SOMETHING_ELSE", status="NO_SUCH_LABEL")

        observed = self._classify("run-unrecognised")

        self.assertEqual(observed.outcome, ExecutionClassification.UNKNOWN)
        self.assertFalse(observed.is_success)
        # The unrecognised values are named, not silently normalised away.
        self.assertIn("SOMETHING_ELSE", observed.detail)

    def test_an_unrecognised_value_never_vetoes_a_real_terminal_outcome(self) -> None:
        self._write("run-partial", state="", status="SUCCESS")

        observed = self._classify("run-partial")

        self.assertEqual(observed.outcome, ExecutionClassification.TERMINAL_SUCCESS)

    def test_contradictory_terminal_labels_are_unknown_not_success(self) -> None:
        self._write("run-contradiction", state="COMPLETED", status="FAILED")

        observed = self._classify("run-contradiction")

        self.assertEqual(observed.outcome, ExecutionClassification.UNKNOWN)
        self.assertFalse(observed.is_success)

    def test_unknown_usage_is_exposed_as_absent_never_as_zero(self) -> None:
        self._write("run-no-accounting", state="COMPLETED", status="SUCCESS")

        # Usage and cost are never recomputed or guessed: an absent accounting
        # block is absent, not zero tokens and not zero cost.
        self.assertIsNone(RunStore(self.root).accounting_summary("run-no-accounting"))
        snapshot = RunStore(self.root).read_state("run-no-accounting")
        self.assertIsNone(snapshot.accounting)
        self.assertEqual(snapshot.event_count, 2)
        observed = self._classify("run-no-accounting")
        self.assertEqual(observed.outcome, ExecutionClassification.TERMINAL_SUCCESS)
        # Nothing but the record's own fields is invented: the classification
        # carries no usage, token, or cost figure at all.
        self.assertEqual(
            set(observed.to_dict()),
            {"outcome", "reason", "detail", "evidence", "is_terminal", "is_success"},
        )

    def test_an_absent_run_record_is_unknown_and_load_still_returns_none(self) -> None:
        self.assertEqual(self._classify("never-written").outcome, ExecutionClassification.UNKNOWN)
        self.assertIsNone(RunStore(self.root).load("never-written"))


class TerminalRecordCompatibilityTests(_IsolatedStoreTestCase):
    """The read surface for already-terminal records must not change shape."""

    def test_summary_and_accounting_summary_keep_their_existing_shape(self) -> None:
        self._write(
            "run-terminal",
            state="COMPLETED",
            status="SUCCESS",
            accounting={"total_tokens": 120, "total_cost": 1.25},
            project_state={"status": "ACCEPTED"},
            completed_attempt_indexes=(0, 1),
        )
        self.store.append_event(EventType.ENGINEERING_RUN_STARTED, {}, run_id="run-terminal")
        self.store.append_event(EventType.ENGINEERING_RUN_COMPLETED, {}, run_id="run-terminal")

        record = RunStore(self.root).load("run-terminal")
        summary = record.summary()

        # Exact key set: classification must not have been grafted onto summary().
        self.assertEqual(set(summary), _EXPECTED_SUMMARY_KEYS)
        self.assertEqual(summary["run_id"], "run-terminal")
        self.assertEqual(summary["state"], "COMPLETED")
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["task_id"], "task-run-terminal")
        self.assertEqual(summary["attempt_number"], 1)
        self.assertEqual(summary["completed_attempt_indexes"], [0, 1])
        self.assertEqual(summary["project_state"], {"status": "ACCEPTED"})
        self.assertEqual(summary["accounting"], {"total_tokens": 120, "total_cost": 1.25})
        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["last_sequence_number"], 1)
        self.assertEqual(summary["updated_at"], "2026-10-06T00:00:00+00:00")
        self.assertFalse(summary["interrupted"])

        accounting = RunStore(self.root).accounting_summary("run-terminal")
        self.assertEqual(accounting, {"total_tokens": 120, "total_cost": 1.25})
        self.assertEqual(RunStore(self.root).read_state("run-terminal").state, "COMPLETED")

    def test_summary_shape_is_unchanged_for_a_record_with_no_snapshot(self) -> None:
        self.store.append_event(EventType.RUN_STARTED, {}, run_id="run-no-snapshot")

        summary = RunStore(self.root).load("run-no-snapshot").summary()

        self.assertEqual(set(summary), _EXPECTED_SUMMARY_KEYS)
        self.assertEqual(summary["state"], "")
        self.assertEqual(summary["status"], "")
        self.assertIsNone(summary["accounting"])
        self.assertIsNone(summary["project_state"])


class OldShapeRecordCompatibilityTests(_IsolatedStoreTestCase):
    """A ``state.json`` written before this change must still read correctly."""

    # The field set an older writer produced: no ``interrupted`` and no
    # ``project_state`` key at all. Written by hand so the old shape is explicit.
    _OLD_SHAPE_FIXTURE = {
        "run_id": "run-legacy",
        "task_id": "task-legacy",
        "state": "COMPLETED",
        "status": "SUCCESS",
        "attempt_number": 4,
        "completed_attempt_indexes": [0, 1, 2, 3],
        "accounting": {"total_tokens": 7, "total_cost": 0.01, "total_attempts": 4},
        "event_count": 9,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    def test_an_old_shape_fixture_still_loads_and_classifies(self) -> None:
        self._write_raw_state("run-legacy", dict(self._OLD_SHAPE_FIXTURE))

        record = RunStore(self.root).load("run-legacy")

        self.assertIsNotNone(record)
        snapshot = record.snapshot
        self.assertEqual(snapshot.run_id, "run-legacy")
        self.assertEqual(snapshot.task_id, "task-legacy")
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.status, "SUCCESS")
        self.assertEqual(snapshot.attempt_number, 4)
        self.assertEqual(snapshot.completed_attempt_indexes, (0, 1, 2, 3))
        self.assertEqual(snapshot.accounting["total_tokens"], 7)
        self.assertEqual(snapshot.event_count, 9)
        # Absent fields default rather than raise.
        self.assertFalse(snapshot.interrupted)
        self.assertIsNone(snapshot.project_state)

        observed = self._classify("run-legacy")
        self.assertEqual(observed.outcome, ExecutionClassification.TERMINAL_SUCCESS)
        self.assertEqual(
            RunStore(self.root).accounting_summary("run-legacy")["total_tokens"], 7
        )
        self.assertEqual(set(record.summary()), _EXPECTED_SUMMARY_KEYS)

    def test_an_old_shape_fixture_claiming_progress_is_indeterminate(self) -> None:
        payload = dict(self._OLD_SHAPE_FIXTURE)
        payload.update({"run_id": "run-legacy-running", "state": "RUNNING", "status": "RUNNING"})
        self._write_raw_state("run-legacy-running", payload)

        observed = self._classify("run-legacy-running")

        self.assertEqual(observed.outcome, ExecutionClassification.INDETERMINATE)
        self.assertFalse(observed.is_success)


class ClassificationIsReadOnlyTests(_IsolatedStoreTestCase):
    def test_classifying_writes_nothing_and_changes_no_file(self) -> None:
        self._write("run-read-only", state="RUNNING", status="RUNNING")
        state_path = self.store.state_path("run-read-only")
        before_bytes = state_path.read_bytes()
        before_entries = sorted(p.name for p in self.store.run_dir("run-read-only").iterdir())

        for _ in range(3):
            self._classify("run-read-only")

        self.assertEqual(state_path.read_bytes(), before_bytes)
        self.assertEqual(
            sorted(p.name for p in self.store.run_dir("run-read-only").iterdir()),
            before_entries,
        )

    def test_classification_reports_tokens_that_were_actually_recorded(self) -> None:
        self._write(
            "run-accounted",
            state="COMPLETED",
            status="SUCCESS",
            accounting={"total_tokens": 0, "total_cost": 0.0, "total_attempts": 1},
        )

        # A recorded zero is reported as a recorded zero; an absent block is None
        # (proved above). The reader never converts one into the other.
        observed = RunStore(self.root).accounting_summary("run-accounted")

        self.assertEqual(observed["total_tokens"], 0)
        self.assertEqual(observed["total_cost"], 0.0)
        self.assertEqual(observed["total_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
