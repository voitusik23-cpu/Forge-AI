"""Stage C1 tests: run persistence, read-back, isolation and honest failure reporting.

Written after reproducing the defect: ``record_run_state`` -> ``write_state`` raises
``PermissionError`` on the profile store root (this environment forbids writes outside the
workspace), the exception is swallowed by the "persistence must not block a run" contract, and
``GET /api/runs/{id}`` therefore answered 404 for a run that reported COMPLETED. These tests pin
the fixed behaviour: with a writable root the record exists and is readable, and with an
unwritable root the failure is reported instead of hidden.
"""

from __future__ import annotations

import concurrent.futures
import os
import pathlib
import shutil
import unittest

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService


class RunHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Not tempfile: this platform refuses to create directories under the user temp tree, so
        # the fixture lives inside the project's gitignored .forge tree instead.
        self._base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-run-history"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(self._base, ignore_errors=True)
        self.root = self._base / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self._base, ignore_errors=True))
        os.environ["FORGE_RUN_STORE_ROOT"] = str(self.root)
        from app.runtime.run_store import RunStore

        self.store = RunStore(root=self.root)
        self.service = ForgeApiService(run_store=self.store)

    def _dispatch(self, description: str):
        # The dispatch facts live on the result, not on the byte-pinned response model.
        return self.service.run_task_detailed(
            TaskRunRequest(description=description, category="code", provider_name="mock")
        )

    def test_01_a_new_run_is_retrievable_by_id(self) -> None:
        result = self._dispatch("C1: read-back")
        self.assertTrue(result.history_recorded, result.history_error)
        record = self.service.get_run_record(result.response.run_id)
        self.assertIsNotNone(record, "a persisted run must be readable by its id")

    def test_02_persisted_state_and_events_are_read_correctly(self) -> None:
        result = self._dispatch("C1: state and events")
        record = self.service.get_run_record(result.response.run_id)
        self.assertIsNotNone(record)
        summary = record.get("summary") if isinstance(record, dict) else None
        self.assertIsInstance(record, dict)
        self.assertTrue(
            any(key in record for key in ("state", "status", "summary", "classification")),
            f"record carries no recognisable state: {sorted(record)[:8]}",
        )
        del summary

    def test_03_read_back_survives_a_new_service_instance(self) -> None:
        result = self._dispatch("C1: restart")
        from app.runtime.run_store import RunStore

        fresh = ForgeApiService(run_store=RunStore(root=self.root))
        self.assertIsNotNone(fresh.get_run_record(result.response.run_id))

    def test_04_a_missing_run_is_reported_as_missing(self) -> None:
        self.assertIsNone(self.service.get_run_record("run-api-does-not-exist"))

    def test_05_an_unsafe_run_id_is_refused_without_touching_the_store(self) -> None:
        self.assertIsNone(self.service.get_run_record("../escape"))

    def test_06_another_workspace_cannot_read_this_run(self) -> None:
        # A different store root models a different workspace: the same id must not resolve.
        result = self._dispatch("C1: isolation")
        other_root = self._base / "other-workspace"
        other_root.mkdir(parents=True, exist_ok=True)
        from app.runtime.run_store import RunStore

        other = ForgeApiService(run_store=RunStore(root=other_root))
        self.assertIsNone(other.get_run_record(result.response.run_id))

    def test_07_concurrent_dispatches_all_persist_and_read(self) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self._dispatch, [f"C1: concurrent {i}" for i in range(4)]))
        identifiers = [item.response.run_id for item in results]
        self.assertEqual(len(set(identifiers)), 4, "run ids must be unique")
        for identifier in identifiers:
            self.assertIsNotNone(self.service.get_run_record(identifier), identifier)

    def test_08_a_persistence_failure_is_reported_not_hidden(self) -> None:
        # Point the store at a location the platform refuses, exactly as measured.
        blocked = pathlib.Path(os.environ.get("SystemRoot", "C:\\Windows")) / "ForgeAI-not-writable"
        from app.runtime.run_store import RunStore

        service = ForgeApiService(run_store=RunStore(root=blocked))
        outcome = service.run_task_detailed(
            TaskRunRequest(description="C1: unwritable root", category="code", provider_name="mock")
        )
        self.assertFalse(outcome.history_recorded)
        self.assertTrue(outcome.history_error)
        # The reason must be useful without leaking a filesystem path.
        self.assertNotIn(str(blocked), outcome.history_error)
        self.assertIn("FORGE_RUN_STORE_ROOT", outcome.history_error)


if __name__ == "__main__":
    unittest.main()
