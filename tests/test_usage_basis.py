"""Stage C3 tests: the basis of usage and cost stays explicit, and unknown is not zero.

No provider is called: the derivation is exercised with synthetic attempts, and one integration
case proves a real mock dispatch is labelled MOCK rather than presented as a real execution.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import unittest
from types import SimpleNamespace

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.runtime.run_store import RunStore


class UsageBasisTests(unittest.TestCase):
    def test_01_nothing_to_stand_on_is_unknown_not_zero(self) -> None:
        self.assertEqual(ForgeApiService.derive_usage_basis([]), ("UNKNOWN", "UNKNOWN"))
        empty = [SimpleNamespace()]
        self.assertEqual(ForgeApiService.derive_usage_basis(empty), ("UNKNOWN", "UNKNOWN"))

    def test_02_a_mock_execution_is_labelled_mock(self) -> None:
        attempts = [SimpleNamespace(provider="mock", tokens=0, cost=0.0)]
        self.assertEqual(ForgeApiService.derive_usage_basis(attempts), ("MOCK", "MOCK"))

    def test_03_provider_reported_usage_is_actual(self) -> None:
        attempts = [SimpleNamespace(provider="anthropic", input_tokens=120, cost=None)]
        self.assertEqual(ForgeApiService.derive_usage_basis(attempts)[0], "ACTUAL")

    def test_04_a_computed_price_without_provider_evidence_is_estimated(self) -> None:
        attempts = [SimpleNamespace(provider="anthropic", estimated_cost=0.002)]
        self.assertEqual(ForgeApiService.derive_usage_basis(attempts), ("ESTIMATED", "ESTIMATED"))

    def test_05_reported_cost_makes_the_cost_actual(self) -> None:
        attempts = [SimpleNamespace(provider="openai", provider_reported_cost=0.01)]
        self.assertEqual(ForgeApiService.derive_usage_basis(attempts)[1], "ACTUAL")

    def test_06_a_real_mock_dispatch_is_reported_as_mock(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-usage-basis"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        root = base / "runs"
        root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        os.environ["FORGE_RUN_STORE_ROOT"] = str(root)
        service = ForgeApiService(run_store=RunStore(root=root))
        outcome = service.run_task_detailed(
            TaskRunRequest(description="C3: basis", category="code", provider_name="mock")
        )
        self.assertEqual(outcome.usage_basis, "MOCK")
        self.assertEqual(outcome.cost_basis, "MOCK")
        # The basis must be traceable to the persisted record, not only to the response.
        self.assertIsNotNone(service.get_run_record(outcome.response.run_id))


if __name__ == "__main__":
    unittest.main()
