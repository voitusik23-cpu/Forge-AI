"""The six cost states: unknown is never zero, an error is never zero, an estimate is not a fact.

Derived from the existing provider summary on demand, so these tests pin behaviour without changing
the protected read-model contract.
"""

from __future__ import annotations

import pathlib
import unittest

from app.dashboard.cost_truth import (
    ALL_STATES,
    ERROR,
    KNOWN_NONZERO,
    KNOWN_ZERO,
    MALFORMED,
    UNKNOWN,
    UNSUPPORTED,
    cost_state_report,
    derive_cost_state,
    state_counts,
)


class CostStateTests(unittest.TestCase):
    def test_01_no_runs_is_a_confirmed_zero(self) -> None:
        state = derive_cost_state(provider="deepseek", total_runs=0, provider_reported_cost=0.0)
        self.assertEqual(state, KNOWN_ZERO)

    def test_02_a_reported_positive_cost_is_known_nonzero(self) -> None:
        state = derive_cost_state(provider="deepseek", total_runs=3, provider_reported_cost=0.42)
        self.assertEqual(state, KNOWN_NONZERO)

    def test_03_a_zero_with_runs_is_unknown_not_zero(self) -> None:
        state = derive_cost_state(provider="deepseek", total_runs=3, provider_reported_cost=0.0)
        self.assertEqual(state, UNKNOWN)
        self.assertNotEqual(state, KNOWN_ZERO)

    def test_04_an_estimate_does_not_upgrade_an_unknown(self) -> None:
        # Forge's own estimate must never be presented as a provider-reported fact.
        state = derive_cost_state(
            provider="deepseek", total_runs=2, provider_reported_cost=0.0, estimated_cost=1.75
        )
        self.assertEqual(state, UNKNOWN)

    def test_05_a_provider_without_a_usage_api_is_unsupported(self) -> None:
        for name in ("openai", "anthropic", "gemini", "xai", "groq", "nvidia", "dashscope", "nararouter"):
            state = derive_cost_state(provider=name, total_runs=5, provider_reported_cost=0.0)
            self.assertEqual(state, UNSUPPORTED, name)

    def test_06_an_unreadable_run_is_an_error_not_zero(self) -> None:
        state = derive_cost_state(
            provider="deepseek", total_runs=None, provider_reported_cost=None, runs_unreadable=True
        )
        self.assertEqual(state, ERROR)

    def test_07_a_malformed_record_is_its_own_state(self) -> None:
        state = derive_cost_state(
            provider="deepseek", total_runs=1, provider_reported_cost=None, malformed_record=True
        )
        self.assertEqual(state, MALFORMED)

    def test_08_every_state_is_reachable_and_distinct(self) -> None:
        seen = {
            derive_cost_state(provider="deepseek", total_runs=0, provider_reported_cost=0.0),
            derive_cost_state(provider="deepseek", total_runs=1, provider_reported_cost=1.0),
            derive_cost_state(provider="deepseek", total_runs=1, provider_reported_cost=0.0),
            derive_cost_state(provider="deepseek", total_runs=1, provider_reported_cost=None, runs_unreadable=True),
            derive_cost_state(provider="deepseek", total_runs=1, provider_reported_cost=None, malformed_record=True),
            derive_cost_state(provider="openai", total_runs=1, provider_reported_cost=0.0),
        }
        self.assertEqual(seen, set(ALL_STATES))

    def test_09_the_report_carries_a_reason_for_every_state(self) -> None:
        providers = {
            "deepseek": {"total_runs": 4, "provider_reported_cost": 0.0, "estimated_cost": 2.0},
            "openai": {"total_runs": 1, "provider_reported_cost": 0.0},
            "groq": {"total_runs": 0, "provider_reported_cost": 0.0},
        }
        entries = cost_state_report(providers)
        for name, entry in entries.items():
            self.assertIn(entry["reported_cost_state"], ALL_STATES, name)
            self.assertTrue(entry["reason"], name)
        self.assertEqual(entries["deepseek"]["reported_cost_state"], UNKNOWN)
        self.assertEqual(entries["openai"]["reported_cost_state"], UNSUPPORTED)
        self.assertEqual(entries["groq"]["reported_cost_state"], KNOWN_ZERO)
        counts = state_counts(entries)
        self.assertEqual(counts[UNKNOWN], 1)
        self.assertEqual(counts[UNSUPPORTED], 1)
        self.assertEqual(counts[KNOWN_ZERO], 1)

    def test_10_legacy_records_without_a_basis_stay_unknown(self) -> None:
        # A record written before any basis existed must not become a confirmed zero.
        state = derive_cost_state(
            provider="openrouter", total_runs=7, provider_reported_cost=0.0, estimated_cost=0.0
        )
        self.assertEqual(state, UNKNOWN)

    def test_11_the_route_and_the_service_method_exist(self) -> None:
        server = pathlib.Path("app/api/server.py").read_text(encoding="utf-8")
        self.assertIn("/api/costs/states", server)
        service = pathlib.Path("app/api/service.py").read_text(encoding="utf-8")
        self.assertIn("def cost_states(", service)

    def test_12_the_protected_summary_contract_is_unchanged(self) -> None:
        from app.dashboard.models import (
            AlertLevel,
            ProviderAccountStatus,
            ProviderStatus,
            ProviderUsageSummary,
        )

        status = ProviderAccountStatus(
            provider="x",
            status=ProviderStatus.NOT_CHECKED,
            balance_display="NOT CHECKED",
            alert=AlertLevel.INFO,
        )
        keys = set(ProviderUsageSummary(provider="x", account_status=status).to_dict())
        for required in ("provider_reported_cost", "estimated_cost", "effective_cost", "forge_spent"):
            self.assertIn(required, keys)
        for added in ("reported_cost_state", "cost_state"):
            self.assertNotIn(added, keys, "the read model must not grow cost-state keys here")


if __name__ == "__main__":
    unittest.main()
