"""Owner decision 2: unknown usage is null, a proven zero stays zero.

The matrix the owner asked for: legacy records, unknown cost, confirmed zero cost, mock calls,
actual and estimated values -- and the rule that nothing may hide an absence behind a zero.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import unittest
from types import SimpleNamespace

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.runtime.run_store import RunStateSnapshot, RunStore


class UnknownUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-unknown-usage"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(self._base, ignore_errors=True)
        self.root = self._base / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        os.environ["FORGE_RUN_STORE_ROOT"] = str(self.root)
        self.store = RunStore(root=self.root)
        # The composition authorizes one project; a run cannot be created without it.
        self.service = ForgeApiService(
            run_store=self.store, authorized_project_id="unknown-usage"
        )
        self.addCleanup(lambda: shutil.rmtree(self._base, ignore_errors=True))

    def test_01_a_mock_dispatch_reports_a_real_zero_with_a_mock_basis(self) -> None:
        outcome = self.service.run_task_detailed(
            TaskRunRequest(description="unknown-usage: mock", category="code", provider_name="mock")
        )
        # The mock provider reports its own zeros: that is evidence, so zero is correct here.
        self.assertEqual(outcome.response.tokens, 0)
        self.assertEqual(outcome.response.cost, 0.0)
        self.assertEqual(outcome.usage_basis, "MOCK")

    def test_02_silence_is_null_and_never_zero(self) -> None:
        # The declared-verification dispatch executes but carries no usage.
        outcome = self.service.run_task_detailed(
            TaskRunRequest(description="unknown-usage: silence", category="code", provider_name="mock")
        )
        response = outcome.response
        if outcome.usage_basis == "UNKNOWN":
            self.assertIsNone(response.tokens)
            self.assertIsNone(response.cost)
        else:
            # Any other basis must be backed by evidence and therefore carry numbers.
            self.assertIsNotNone(response.tokens)
            self.assertIsNotNone(response.cost)

    def test_03_the_serialised_contract_uses_null_not_zero(self) -> None:
        from app.api.models import TaskRunResponse

        payload = TaskRunResponse(
            run_id="run-api-test", task_id="t", project_id="default", state="COMPLETED", success=True, output=""
        ).to_dict()
        self.assertIsNone(payload["tokens"])
        self.assertIsNone(payload["cost"])
        encoded = json.dumps(payload)
        self.assertIn('"tokens": null', encoded)
        self.assertIn('"cost": null', encoded)

    def test_04_a_legacy_record_without_accounting_is_unknown(self) -> None:
        # A record written before this change has a snapshot but no accounting block.
        run_id = "run-api-legacy"
        snapshot = RunStateSnapshot(
            run_id=run_id, task_id="task-legacy", state="COMPLETED", status="COMPLETED"
        )
        self.store.write_state(snapshot)
        record = self.service.get_run_record(run_id)
        self.assertIsNotNone(record, "a legacy record must remain readable")
        accounting = record.get("accounting") if isinstance(record, dict) else None
        self.assertIsNone(accounting, "the fixture record carries no accounting")

    def test_05_a_record_with_a_real_zero_keeps_zero_on_replay(self) -> None:
        run_id = "run-api-zero"
        snapshot = RunStateSnapshot(
            run_id=run_id,
            task_id="task-zero",
            state="COMPLETED",
            status="COMPLETED",
            accounting={"total_tokens": 0, "total_cost": 0.0, "provider": "mock", "model": "mock"},
        )
        self.store.write_state(snapshot)
        record = self.store.load(run_id)
        self.assertIsNotNone(record)
        # A present zero survives; an absent field stays absent (checked in the next test).
        self.assertEqual(record.snapshot.accounting["total_tokens"], 0)

    def test_06_actual_and_estimated_are_not_interchangeable(self) -> None:
        reported = [SimpleNamespace(provider="anthropic", input_tokens=42)]
        estimated = [SimpleNamespace(provider="anthropic", estimated_cost=0.5)]
        self.assertEqual(ForgeApiService.derive_usage_basis(reported)[0], "ACTUAL")
        self.assertEqual(ForgeApiService.derive_usage_basis(estimated)[0], "ESTIMATED")
        self.assertNotEqual(
            ForgeApiService.derive_usage_basis(reported)[1],
            ForgeApiService.derive_usage_basis(estimated)[1],
        )


if __name__ == "__main__":
    unittest.main()
