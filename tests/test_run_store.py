"""Contract tests for durable Run history storage.

These tests pin the durability contract, not the implementation:

* Run history and state survive a process restart (a fresh store instance);
* the durable log is append-only, order-preserving, and sanitized;
* a truncated trailing line never hides valid history;
* reading is passive: nothing is resumed, retried, or re-executed.

Automatic execution resume is intentionally **not** implemented, so an
interrupted Run must remain readable and inert.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.orchestrator.models import EventType, Run, RunState, Task
from app.runtime.run_store import RunStore, RunStateSnapshot


class RunStoreEventLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = RunStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_events_persist_with_monotonic_sequence(self) -> None:
        for name in ("RUN_STARTED", "RUN_COMPLETED"):
            self.store.append_event(
                EventType[name], {"status": "ok"}, run_id="run-1"
            )

        events = self.store.read_events("run-1")
        self.assertEqual(len(events), 2)
        self.assertEqual([e.sequence_number for e in events], [0, 1])
        self.assertEqual([e.event_type.name for e in events], ["RUN_STARTED", "RUN_COMPLETED"])

    def test_sequence_continues_across_store_instances(self) -> None:
        self.store.append_event(EventType.RUN_STARTED, {}, run_id="run-2")
        # Simulated restart: a brand new store must not rewind the log.
        fresh = RunStore(self.root)
        fresh.append_event(EventType.RUN_COMPLETED, {}, run_id="run-2")

        events = fresh.read_events("run-2")
        self.assertEqual([e.sequence_number for e in events], [0, 1])

    def test_order_and_correlation_fields_survive_round_trip(self) -> None:
        self.store.append_event(
            EventType.EXECUTION_COMPLETED,
            {"status": "ok"},
            run_id="run-3",
            attempt_number=2,
            task_id="task-3",
            execution_request_id="req-9",
            verification_id="ver-9",
            decision_id="dec-9",
        )
        event = self.store.read_events("run-3")[0]
        self.assertEqual(event.attempt_number, 2)
        self.assertEqual(event.task_id, "task-3")
        self.assertEqual(event.execution_request_id, "req-9")
        self.assertEqual(event.verification_id, "ver-9")
        self.assertEqual(event.decision_id, "dec-9")
        self.assertIsNotNone(event.timestamp)

    def test_forbidden_metadata_is_not_written(self) -> None:
        self.store.append_event(
            EventType.TOOL_EXECUTION_COMPLETED,
            {
                "stdout": "SECRET STDOUT",
                "stderr": "SECRET STDERR",
                "prompt": "SECRET PROMPT",
                "api_key": "SECRET KEY",
                "safe_key": "visible",
            },
            run_id="run-4",
        )
        raw = self.store.events_path("run-4").read_text(encoding="utf-8")
        for forbidden in ("SECRET STDOUT", "SECRET STDERR", "SECRET PROMPT", "SECRET KEY"):
            self.assertNotIn(forbidden, raw)
        self.assertIn("visible", raw)

    def test_empty_log_reads_as_empty(self) -> None:
        self.assertEqual(self.store.read_events("never-written"), ())

    def test_truncated_trailing_line_does_not_hide_valid_history(self) -> None:
        for name in ("RUN_STARTED", "CONTEXT_ASSEMBLED", "RUN_COMPLETED"):
            self.store.append_event(EventType[name], {"n": name}, run_id="run-5")
        path = self.store.events_path("run-5")
        # Simulate a crash mid-append: a partial final line with no newline.
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"run_id": "run-5", "sequence_number": 3, "even')

        events = self.store.read_events("run-5")
        self.assertEqual(len(events), 3, "valid history must survive a partial line")
        self.assertEqual([e.sequence_number for e in events], [0, 1, 2])

    def test_corrupt_middle_line_stops_rather_than_raises(self) -> None:
        self.store.append_event(EventType.RUN_STARTED, {}, run_id="run-6")
        path = self.store.events_path("run-6")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("not json at all\n")
            handle.write(json.dumps({"run_id": "run-6", "event_type": "RUN_COMPLETED"}) + "\n")

        events = self.store.read_events("run-6")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EventType.RUN_STARTED)

    def test_unsafe_run_id_is_rejected(self) -> None:
        for bad in ("../escape", "a/b", "", "x" * 200):
            with self.assertRaises(ValueError):
                self.store.append_event(EventType.RUN_STARTED, {}, run_id=bad)


class RunStoreSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = RunStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_snapshot_round_trips(self) -> None:
        snapshot = RunStateSnapshot(
            run_id="run-snap",
            task_id="task-snap",
            state="COMPLETED",
            status="SUCCESS",
            attempt_number=1,
            completed_attempt_indexes=(0, 1),
            project_state={"status": "ACCEPTED"},
            accounting={"total_tokens": 42, "total_cost": 0.5},
            event_count=7,
            updated_at="2026-10-06T00:00:00+00:00",
            interrupted=False,
        )
        self.store.write_state(snapshot)

        # Fresh instance: nothing shared in memory.
        loaded = RunStore(self.root).read_state("run-snap")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.task_id, "task-snap")
        self.assertEqual(loaded.state, "COMPLETED")
        self.assertEqual(loaded.completed_attempt_indexes, (0, 1))
        self.assertEqual(loaded.project_state, {"status": "ACCEPTED"})
        self.assertEqual(loaded.accounting, {"total_tokens": 42, "total_cost": 0.5})
        self.assertEqual(loaded.event_count, 7)

    def test_missing_snapshot_reads_as_none(self) -> None:
        self.assertIsNone(self.store.read_state("absent"))

    def test_corrupt_snapshot_reads_as_none_without_raising(self) -> None:
        directory = self.store.run_dir("run-bad")
        directory.mkdir(parents=True, exist_ok=True)
        self.store.state_path("run-bad").write_text("{ not json", encoding="utf-8")
        self.assertIsNone(self.store.read_state("run-bad"))

    def test_record_run_state_derives_from_live_run(self) -> None:
        run = Run(task=Task(id="task-x", description="d"), id="run-x")
        run.state = RunState.WAITING_FOR_APPROVAL
        snapshot = self.store.record_run_state(
            run,
            status="WAITING",
            attempt_number=3,
            completed_attempt_indexes=(0, 1, 2),
        )
        self.assertEqual(snapshot.state, "WAITING_FOR_APPROVAL")
        self.assertEqual(snapshot.status, "WAITING")
        self.assertEqual(snapshot.attempt_number, 3)
        reloaded = RunStore(self.root).read_state("run-x")
        self.assertEqual(reloaded.state, "WAITING_FOR_APPROVAL")


class RunStoreRestartTests(unittest.TestCase):
    """Simulated crash/restart: only the files on disk carry state forward."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_history_is_recovered_by_a_new_store(self) -> None:
        writer = RunStore(self.root)
        writer.bind_run("run-restart", task_id="task-restart")
        writer(EventType.ENGINEERING_RUN_STARTED, {"status": "started"})
        writer(EventType.PROVIDER_SELECTED, {"provider": "fixture"})

        # The original store is discarded entirely.
        del writer

        reader = RunStore(self.root)
        record = reader.load("run-restart")
        self.assertIsNotNone(record)
        self.assertEqual(record.event_count, 2)
        self.assertEqual(record.events[0].event_type, EventType.ENGINEERING_RUN_STARTED)
        self.assertEqual(record.snapshot.task_id, "task-restart") if record.snapshot else None
        self.assertEqual(record.summary()["event_count"], 2)

    def test_interrupted_run_is_readable_but_marked_by_absence_of_final_state(self) -> None:
        writer = RunStore(self.root)
        writer.bind_run("run-interrupted")
        writer(EventType.ENGINEERING_RUN_STARTED, {"status": "started"})
        writer(EventType.TOOL_EXECUTION_STARTED, {"tool_id": "write_project_file"})
        # No ENGINEERING_RUN_COMPLETED and no final snapshot: the process died.

        reader = RunStore(self.root)
        record = reader.load("run-interrupted")
        self.assertIsNotNone(record)
        self.assertIsNone(record.snapshot, "no final snapshot was ever written")
        names = [e.event_type.name for e in record.events]
        self.assertIn("TOOL_EXECUTION_STARTED", names)
        self.assertNotIn("ENGINEERING_RUN_COMPLETED", names)

    def test_loading_grants_no_authority_and_executes_nothing(self) -> None:
        writer = RunStore(self.root)
        writer.bind_run("run-inert")
        writer(EventType.EXECUTION_REQUESTED, {"command": "python"})
        writer(EventType.TOOL_INVOCATION_REQUESTED, {"tool_id": "write_project_file"})

        reader = RunStore(self.root)
        record = reader.load("run-inert")
        self.assertIsNotNone(record)
        # The persisted payload is data only: no scope, no approval, no authority.
        summary = record.summary()
        for forbidden in ("run_scope", "approval", "allowed_tool_ids", "allowed_execution_commands"):
            self.assertNotIn(forbidden, json.dumps(summary, default=str))
        # No file was created and nothing was executed by loading.
        self.assertEqual(
            sorted(p.name for p in Path(self.root).iterdir()), ["run-inert"]
        )
        self.assertEqual(
            sorted(p.name for p in (Path(self.root) / "run-inert").iterdir()),
            ["events.jsonl"],
        )

    def test_unknown_run_loads_as_none(self) -> None:
        self.assertIsNone(RunStore(self.root).load("no-such-run"))

    def test_list_run_ids_is_deterministic(self) -> None:
        store = RunStore(self.root)
        for run_id in ("run-b", "run-a", "run-c"):
            store.append_event(EventType.RUN_STARTED, {}, run_id=run_id)
        self.assertEqual(store.list_run_ids(), ("run-a", "run-b", "run-c"))


class RunStoreAccountingReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = RunStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_accounting_summary_is_read_back(self) -> None:
        run = Run(task=Task(id="task-a", description="d"), id="run-a")
        self.store.record_run_state(
            run,
            status="SUCCESS",
            accounting={"total_tokens": 120, "total_cost": 1.25, "total_attempts": 2},
        )
        summary = self.store.accounting_summary("run-a")
        self.assertEqual(summary["total_tokens"], 120)
        self.assertEqual(summary["total_cost"], 1.25)

    def test_accounting_summary_absent_is_none(self) -> None:
        run = Run(task=Task(id="task-b", description="d"), id="run-b")
        self.store.record_run_state(run, status="SUCCESS")
        self.assertIsNone(self.store.accounting_summary("run-b"))


if __name__ == "__main__":
    unittest.main()
