"""Block #3 — the API read path carries the execution classification.

Targeted tests, isolated temp store, no provider call, no repository write.
"""

import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(r"C:\Users\User\Desktop\Forge-AI")
sys.path.insert(0, str(REPO))

from app.api.service import ForgeApiService
from app.orchestrator.models import Run, RunState, Task, TaskCategory
from app.runtime.run_store import RunStore

SUMMARY_KEYS = {
    "accounting", "attempt_number", "completed_attempt_indexes", "event_count",
    "interrupted", "last_sequence_number", "project_state", "run_id", "state",
    "status", "task_id", "updated_at",
}


class _Service:
    """Builds a service whose run store is an isolated temp root."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.store = RunStore(root=self.root / "runs")
        self.service = ForgeApiService(run_store=self.store)

    def close(self):
        self._tmp.cleanup()


class ClassificationReadPathTests(unittest.TestCase):
    def setUp(self):
        self.harness = _Service()
        self.service = self.harness.service
        self.store = self.harness.store

    def tearDown(self):
        self.harness.close()

    def _record(self, run_id: str, state: RunState, status: str = "", interrupted: bool = False):
        run = Run(task=Task(id=f"task-{run_id}", description="p", category=TaskCategory.CODE),
                  id=run_id)
        run.state = state
        self.store.record_run_state(run, status=status, interrupted=interrupted)
        return run_id

    # -- compatibility -------------------------------------------------------
    def test_01_every_previous_summary_key_is_still_returned(self):
        run_id = self._record("run-compat", RunState.COMPLETED, "COMPLETED")
        record = self.service.get_run_record(run_id)
        self.assertTrue(SUMMARY_KEYS.issubset(set(record)), sorted(set(record) - SUMMARY_KEYS))
        self.assertEqual(record["run_id"], run_id)
        self.assertEqual(record["state"], "COMPLETED")

    def test_02_unknown_run_is_still_none(self):
        self.assertIsNone(self.service.get_run_record("run-does-not-exist"))

    def test_03_blank_and_unsafe_ids_are_still_refused(self):
        for bad in ("", "   ", "../escape", "a/b", ".."):
            with self.subTest(bad=bad):
                self.assertIsNone(self.service.get_run_record(bad))

    # -- the added key -------------------------------------------------------
    def test_04_classification_is_present_and_shaped(self):
        run_id = self._record("run-shape", RunState.COMPLETED, "COMPLETED")
        record = self.service.get_run_record(run_id)
        self.assertIn("classification", record)
        found = record["classification"]
        for key in ("outcome", "reason", "detail", "evidence", "is_terminal", "is_success"):
            self.assertIn(key, found, key)
        self.assertIsInstance(found["evidence"], list)

    def test_05_terminal_success_is_classified_as_success(self):
        run_id = self._record("run-ok", RunState.COMPLETED, "COMPLETED")
        found = self.service.get_run_record(run_id)["classification"]
        self.assertEqual(found["outcome"], "TERMINAL_SUCCESS")
        self.assertTrue(found["is_success"])
        self.assertTrue(found["is_terminal"])

    def test_06_terminal_failure_is_not_reported_as_success(self):
        run_id = self._record("run-bad", RunState.FAILED, "FAILED")
        found = self.service.get_run_record(run_id)["classification"]
        self.assertEqual(found["outcome"], "TERMINAL_FAILURE")
        self.assertFalse(found["is_success"])
        self.assertTrue(found["is_terminal"])

    def test_07_waiting_is_neither_success_nor_failure(self):
        run_id = self._record("run-wait", RunState.WAITING_FOR_APPROVAL,
                              "WAITING_FOR_APPROVAL")
        found = self.service.get_run_record(run_id)["classification"]
        self.assertEqual(found["outcome"], "WAITING")
        self.assertFalse(found["is_success"])
        self.assertFalse(found["is_terminal"])

    # -- the honest part: a claim of progress is not proof of life ----------
    def test_08_a_record_claiming_progress_is_never_reported_as_running(self):
        run_id = self._record("run-running", RunState.RUNNING, "RUNNING")
        record = self.service.get_run_record(run_id)
        found = record["classification"]
        self.assertEqual(found["outcome"], "INDETERMINATE")
        self.assertFalse(found["is_success"])
        self.assertFalse(found["is_terminal"])
        self.assertEqual(found["reason"], "claimed_in_progress")
        # The raw fields still say RUNNING -- that is the record, not a liveness claim.
        self.assertEqual(record["state"], "RUNNING")
        self.assertEqual(record["status"], "RUNNING")

    def test_09_the_classification_never_asserts_liveness(self):
        """No combination may be reported as 'a live process'."""
        run_id = self._record("run-live-check", RunState.CREATED, "RUNNING")
        found = self.service.get_run_record(run_id)["classification"]
        self.assertIn(found["outcome"], {"INDETERMINATE", "UNKNOWN"})
        self.assertFalse(found["is_success"])
        self.assertNotIn("alive", str(found).lower())
        self.assertNotIn("running process", str(found).lower())

    def test_10_interrupted_is_reported_as_indeterminate(self):
        run_id = self._record("run-int", RunState.RUNNING, "RUNNING", interrupted=True)
        found = self.service.get_run_record(run_id)["classification"]
        self.assertEqual(found["outcome"], "INDETERMINATE")
        self.assertFalse(found["is_success"])

    def test_11_timeout_is_expressible_and_not_success(self):
        run_id = self._record("run-timeout", RunState.FAILED, "EXECUTION_TIMEOUT")
        found = self.service.get_run_record(run_id)["classification"]
        self.assertEqual(found["outcome"], "TERMINAL_TIMEOUT")
        self.assertFalse(found["is_success"])

    def test_12_refusal_is_never_success(self):
        for status in ("PERMISSION_DENIED", "POLICY_DENIED", "EXECUTION_ERROR"):
            with self.subTest(status=status):
                run_id = self._record(f"run-{status}", RunState.FAILED, status)
                found = self.service.get_run_record(run_id)["classification"]
                self.assertFalse(found["is_success"], f"{status} reported success")

    def test_13_contradictory_terminal_labels_are_unknown_not_success(self):
        run_id = self._record("run-contra", RunState.COMPLETED, "FAILED")
        found = self.service.get_run_record(run_id)["classification"]
        self.assertEqual(found["outcome"], "UNKNOWN")
        self.assertFalse(found["is_success"])

    def test_14_the_returned_dict_is_a_fresh_copy(self):
        """A caller must not be able to mutate the persisted snapshot."""
        run_id = self._record("run-copy", RunState.COMPLETED, "COMPLETED")
        first = self.service.get_run_record(run_id)
        first["state"] = "TAMPERED"
        del first["classification"]
        second = self.service.get_run_record(run_id)
        self.assertEqual(second["state"], "COMPLETED")
        self.assertIn("classification", second)


if __name__ == "__main__":
    unittest.main()
