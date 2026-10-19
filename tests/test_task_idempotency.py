"""Stage C2 tests: server-side idempotency, concurrency, restart and conflicts.

Deterministic: the mock provider is used, so no paid call can happen and no network is involved.
The tests count persisted run directories, which is the only honest way to prove that a repeat
did not execute anything.
"""

from __future__ import annotations

import concurrent.futures
import os
import pathlib
import shutil
import unittest

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService, RunIdempotencyConflict
from app.runtime.run_store import RunStore


class IdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        # Not tempfile: this platform refuses to create directories under the user temp tree.
        self._base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-idempotency"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(self._base, ignore_errors=True)
        self.root = self._base / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        os.environ["FORGE_RUN_STORE_ROOT"] = str(self.root)
        self.service = ForgeApiService(authorized_project_id="idempotency-unit", run_store=RunStore(root=self.root))
        self.addCleanup(lambda: shutil.rmtree(self._base, ignore_errors=True))

    def _dispatch(self, key: str | None, description: str = "C2 task", **extra):
        # The key is a parameter of the service, not a field of the byte-pinned request model.
        return self.service.run_task_detailed(
            TaskRunRequest(
                description=description, category="code", provider_name="mock", **extra
            ),
            idempotency_key=key,
        )

    def _run_directories(self) -> int:
        return len(
            [item for item in self.root.iterdir() if item.is_dir() and item.name != "idempotency"]
        )

    def test_01_a_repeated_key_does_not_execute_twice(self) -> None:
        first = self._dispatch("key-a")
        before = self._run_directories()
        second = self._dispatch("key-a")
        self.assertTrue(second.idempotent_replay)
        self.assertEqual(second.response.run_id, first.response.run_id)
        self.assertEqual(self._run_directories(), before, "a replay must not create a run")

    def test_02_a_different_request_with_the_same_key_is_a_conflict(self) -> None:
        self._dispatch("key-b", "first request")
        with self.assertRaises(RunIdempotencyConflict):
            self._dispatch("key-b", "materially different request")

    def test_03_different_keys_execute_separately(self) -> None:
        first = self._dispatch("key-c1")
        second = self._dispatch("key-c2")
        self.assertNotEqual(first.response.run_id, second.response.run_id)
        self.assertFalse(second.idempotent_replay)

    def test_04_a_replay_survives_a_new_service_instance(self) -> None:
        first = self._dispatch("key-d")
        fresh = ForgeApiService(authorized_project_id="idempotency-unit", run_store=RunStore(root=self.root))
        replay = fresh.run_task_detailed(
            TaskRunRequest(description="C2 task", category="code", provider_name="mock"),
            idempotency_key="key-d",
        )
        self.assertTrue(replay.idempotent_replay)
        self.assertEqual(replay.response.run_id, first.response.run_id)

    def test_05_concurrent_requests_with_one_key_execute_once(self) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self._dispatch, "key-e") for _ in range(4)]
            results = []
            for future in futures:
                try:
                    results.append(future.result())
                except RunIdempotencyConflict:
                    # A losing racing thread may legitimately observe the winner, not the request.
                    pass
        identifiers = {
            item.response.run_id for item in results if item.response.run_id
        }
        self.assertEqual(len(identifiers), 1, f"expected one identity, saw {identifiers}")
        executions = [item for item in results if not item.idempotent_replay]
        self.assertEqual(len(executions), 1, "exactly one dispatch may execute")

    def test_06_without_a_key_every_dispatch_executes(self) -> None:
        first = self._dispatch(None)
        second = self._dispatch(None)
        self.assertNotEqual(first.response.run_id, second.response.run_id)
        self.assertFalse(second.idempotent_replay)

    def test_07_the_key_is_scoped_by_project(self) -> None:
        self._dispatch("key-f", project_id="default")
        other = self.service.run_task_detailed(
            TaskRunRequest(
                description="C2 task",
                category="code",
                provider_name="mock",
                project_id="another-project",
            ),
            idempotency_key="key-f",
        )
        self.assertFalse(other.idempotent_replay, "project scoping must not collide")

    def test_08_a_replay_reports_the_original_lifecycle(self) -> None:
        first = self._dispatch("key-g")
        replay = self._dispatch("key-g")
        self.assertEqual(replay.response.state, first.response.state)
        self.assertEqual(replay.response.run_id, first.response.run_id)

    def test_09_an_unusable_ledger_refuses_a_keyed_dispatch(self) -> None:
        # Fail closed: with a ledger that cannot be created, a keyed dispatch must not execute.
        blocked = pathlib.Path(os.environ.get("SystemRoot", "C:\\Windows")) / "ForgeAI-not-writable"
        service = ForgeApiService(authorized_project_id="idempotency-unit", run_store=RunStore(root=self.root))
        service._run_store = RunStore(root=blocked)
        before = self._run_directories()
        from app.api.service import IdempotencyUnavailable

        with self.assertRaises(IdempotencyUnavailable):
            service.run_task_detailed(
                TaskRunRequest(description="C2: unusable ledger", category="code", provider_name="mock"),
                idempotency_key="key-h",
            )
        self.assertEqual(self._run_directories(), before, "a refused dispatch must not execute")

    def test_10_without_a_key_the_legacy_behaviour_still_executes(self) -> None:
        blocked = pathlib.Path(os.environ.get("SystemRoot", "C:\\Windows")) / "ForgeAI-not-writable"
        service = ForgeApiService(authorized_project_id="idempotency-unit", run_store=RunStore(root=self.root))
        service._run_store = RunStore(root=blocked)
        outcome = service.run_task_detailed(
            TaskRunRequest(description="C2: legacy", category="code", provider_name="mock")
        )
        # No key, no protection claimed: the dispatch proceeds and reports that history is missing.
        self.assertFalse(outcome.history_recorded)
        self.assertTrue(outcome.history_error)


if __name__ == "__main__":
    unittest.main()
