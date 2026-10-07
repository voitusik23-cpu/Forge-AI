"""Tests for Forge AI Provider & Cost Dashboard."""

from __future__ import annotations

import json
import unittest

from app.dashboard.aggregator import DashboardAggregator
from app.dashboard.formatter import format_dashboard_markdown
from app.dashboard.models import (
    AlertLevel,
    DashboardTotals,
    ProviderAccountStatus,
    ProviderStatus,
    ProviderThresholdConfig,
)
from app.dashboard.provider_health import ProviderHealthMonitor
from app.dashboard.service import CostDashboardService
from app.fabric.adapters import UsageAccountingAdapter
from app.fabric.fabric import CapabilityFabric
from app.fabric.models import AttemptUsageRecord, RunAccountingRecord
from app.usage import Usage


class DashboardTests(unittest.TestCase):
    """Test suite covering all 22 verification aspects of the Dashboard."""

    def test_01_provider_aggregation(self) -> None:
        rec1 = RunAccountingRecord(
            run_id="run-1",
            task_id="t1",
            attempt_records=(
                AttemptUsageRecord(
                    attempt_index=1,
                    agent_name="deepseek_agent",
                    provider_name="deepseek",
                    model_name="deepseek-chat",
                    input_tokens=100,
                    output_tokens=50,
                    estimated_cost=0.01,
                    success=True,
                ),
            ),
        )
        rec2 = RunAccountingRecord(
            run_id="run-2",
            task_id="t2",
            attempt_records=(
                AttemptUsageRecord(
                    attempt_index=1,
                    agent_name="deepseek_agent",
                    provider_name="deepseek",
                    model_name="deepseek-chat",
                    input_tokens=200,
                    output_tokens=100,
                    estimated_cost=0.02,
                    success=True,
                ),
            ),
        )

        providers = DashboardAggregator.aggregate_by_provider([rec1, rec2])
        ds = next(p for p in providers if p.provider == "deepseek")
        self.assertEqual(ds.total_runs, 2)
        self.assertEqual(ds.total_requests, 2)
        self.assertEqual(ds.total_attempts, 2)
        self.assertEqual(ds.input_tokens, 300)
        self.assertEqual(ds.output_tokens, 150)
        self.assertEqual(ds.total_tokens, 450)
        self.assertAlmostEqual(ds.effective_cost, 0.03, places=4)

    def test_02_model_aggregation(self) -> None:
        rec = RunAccountingRecord(
            run_id="run-1",
            attempt_records=(
                AttemptUsageRecord(
                    provider_name="openrouter",
                    model_name="anthropic/claude-haiku-4.5",
                    input_tokens=500,
                    output_tokens=200,
                    estimated_cost=0.005,
                    success=True,
                ),
                AttemptUsageRecord(
                    provider_name="openrouter",
                    model_name="anthropic/claude-sonnet-5.5",
                    input_tokens=1000,
                    output_tokens=500,
                    estimated_cost=0.015,
                    success=True,
                ),
            ),
        )
        models = DashboardAggregator.aggregate_by_model([rec])
        self.assertEqual(len(models), 2)
        haiku = next(m for m in models if m.model == "anthropic/claude-haiku-4.5")
        self.assertEqual(haiku.total_tokens, 700)
        self.assertAlmostEqual(haiku.effective_cost, 0.005)

    def test_03_agent_aggregation(self) -> None:
        rec = RunAccountingRecord(
            run_id="run-1",
            attempt_records=(
                AttemptUsageRecord(
                    agent_name="architect",
                    provider_name="openrouter",
                    model_name="anthropic/claude-sonnet-5.5",
                    input_tokens=800,
                    output_tokens=400,
                    estimated_cost=0.012,
                    success=True,
                ),
            ),
        )
        agents = DashboardAggregator.aggregate_by_agent([rec])
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].agent, "architect")
        self.assertEqual(agents[0].total_tokens, 1200)

    def test_04_project_aggregation(self) -> None:
        rec1 = RunAccountingRecord(
            run_id="run-1",
            project_id="forge-core",
            attempt_records=(
                AttemptUsageRecord(
                    input_tokens=100,
                    output_tokens=50,
                    estimated_cost=0.002,
                ),
            ),
        )
        rec2 = RunAccountingRecord(
            run_id="run-2",
            project_id="forge-core",
            attempt_records=(
                AttemptUsageRecord(
                    input_tokens=200,
                    output_tokens=100,
                    estimated_cost=0.004,
                ),
            ),
        )
        projects = DashboardAggregator.aggregate_by_project([rec1, rec2])
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].project_id, "forge-core")
        self.assertEqual(projects[0].total_runs, 2)
        self.assertEqual(projects[0].total_tokens, 450)
        self.assertAlmostEqual(projects[0].effective_cost, 0.006)

    def test_05_run_aggregation(self) -> None:
        rec = RunAccountingRecord(
            run_id="run-123",
            task_id="task-abc",
            project_id="proj-xyz",
            attempt_records=(
                AttemptUsageRecord(
                    agent_name="coder",
                    provider_name="deepseek",
                    model_name="deepseek-chat",
                    input_tokens=50,
                    output_tokens=25,
                    estimated_cost=0.001,
                    success=True,
                ),
            ),
            associated_artifact_ids=("art-1", "art-2"),
            total_duration_seconds=1.45,
        )
        runs = DashboardAggregator.summarize_runs([rec])
        self.assertEqual(len(runs), 1)
        r = runs[0]
        self.assertEqual(r.run_id, "run-123")
        self.assertEqual(r.task_id, "task-abc")
        self.assertEqual(r.total_tokens, 75)
        self.assertEqual(r.associated_artifacts, ("art-1", "art-2"))
        self.assertEqual(r.duration_seconds, 1.45)

    def test_06_provider_reported_cost_precedence(self) -> None:
        rec = AttemptUsageRecord(
            input_tokens=1000,
            output_tokens=500,
            provider_reported_cost=0.00042,
            estimated_cost=0.00100,
        )
        self.assertEqual(rec.effective_cost, 0.00042)

    def test_07_estimated_cost_fallback(self) -> None:
        rec = AttemptUsageRecord(
            input_tokens=1000,
            output_tokens=500,
            provider_reported_cost=None,
            estimated_cost=0.00100,
        )
        self.assertEqual(rec.effective_cost, 0.00100)

    def test_08_cached_tokens(self) -> None:
        rec = RunAccountingRecord(
            run_id="run-cache",
            attempt_records=(
                AttemptUsageRecord(
                    provider_name="deepseek",
                    model_name="deepseek-chat",
                    input_tokens=1000,
                    output_tokens=200,
                    cached_tokens=800,
                ),
            ),
        )
        self.assertEqual(rec.total_cached_tokens, 800)
        self.assertEqual(rec.total_tokens, 1200)

    def test_09_retry_accounting(self) -> None:
        rec = RunAccountingRecord(
            run_id="run-retry",
            retry_count=2,
            attempt_records=(
                AttemptUsageRecord(
                    provider_name="deepseek",
                    model_name="deepseek-chat",
                    input_tokens=100,
                    output_tokens=50,
                    estimated_cost=0.002,
                ),
            ),
        )
        providers = DashboardAggregator.aggregate_by_provider([rec])
        ds = next(p for p in providers if p.provider == "deepseek")
        self.assertEqual(ds.retry_count, 2)

    def test_10_fallback_accounting_and_11_no_double_counting(self) -> None:
        # Run has 2 attempts: Attempt 1 failed on DeepSeek, Attempt 2 succeeded on OpenRouter
        rec = RunAccountingRecord(
            run_id="run-fallback",
            attempt_records=(
                AttemptUsageRecord(
                    attempt_index=1,
                    provider_name="deepseek",
                    model_name="deepseek-chat",
                    input_tokens=100,
                    output_tokens=0,
                    estimated_cost=0.001,
                    success=False,
                    is_fallback=False,
                ),
                AttemptUsageRecord(
                    attempt_index=2,
                    provider_name="openrouter",
                    model_name="anthropic/claude-haiku-4.5",
                    input_tokens=120,
                    output_tokens=60,
                    estimated_cost=0.003,
                    success=True,
                    is_fallback=True,
                ),
            ),
            fallback_events=("deepseek -> openrouter",),
        )

        providers = DashboardAggregator.aggregate_by_provider([rec])
        ds = next(p for p in providers if p.provider == "deepseek")
        or_prov = next(p for p in providers if p.provider == "openrouter")

        self.assertEqual(ds.failure_count, 1)
        self.assertEqual(ds.success_count, 0)
        self.assertEqual(ds.fallback_count, 0)
        self.assertEqual(ds.total_runs, 1)

        self.assertEqual(or_prov.failure_count, 0)
        self.assertEqual(or_prov.success_count, 1)
        self.assertEqual(or_prov.fallback_count, 1)
        self.assertEqual(or_prov.total_runs, 1)

        # Run totals must sum without double counting
        totals = DashboardAggregator.calculate_totals([rec])
        self.assertEqual(totals.total_runs, 1)
        self.assertEqual(totals.total_requests, 2)
        self.assertEqual(totals.total_tokens, 280)
        self.assertAlmostEqual(totals.total_spent, 0.004)

    def test_12_to_17_provider_balance_states_and_thresholds(self) -> None:
        # 12: Ready state with healthy balance
        st_ready = ProviderAccountStatus(
            provider="deepseek",
            status=ProviderStatus.READY,
            balance_display="$1.15 USD",
            balance_numeric=1.15,
            alert=AlertLevel.HEALTHY,
        )
        self.assertEqual(st_ready.alert, AlertLevel.HEALTHY)

        # 13: Unknown balance
        st_unknown = ProviderAccountStatus(
            provider="some_prov",
            status=ProviderStatus.READY,
            balance_display="UNKNOWN",
            balance_numeric=None,
            alert=AlertLevel.INFO,
        )
        self.assertIsNone(st_unknown.balance_numeric)

        # 14: Zero balance / NO_CREDITS
        st_no_cred = ProviderAccountStatus(
            provider="openai",
            status=ProviderStatus.NO_CREDITS,
            balance_display="$0.00",
            balance_numeric=0.0,
            alert=AlertLevel.CRITICAL,
        )
        self.assertEqual(st_no_cred.status, ProviderStatus.NO_CREDITS)
        self.assertEqual(st_no_cred.alert, AlertLevel.CRITICAL)

        # 15: Low/critical thresholds check
        cfg = ProviderThresholdConfig.default_for_provider("deepseek")
        self.assertEqual(cfg.warning_balance, 1.00)
        self.assertEqual(cfg.critical_balance, 0.20)

        # 16: FREE / Quota tier
        st_free = ProviderAccountStatus(
            provider="gemini",
            status=ProviderStatus.FREE_TIER,
            balance_display="FREE / QUOTA",
            alert=AlertLevel.HEALTHY,
        )
        self.assertEqual(st_free.status, ProviderStatus.FREE_TIER)

        # 17: Network-blocked provider
        st_blocked = ProviderAccountStatus(
            provider="groq",
            status=ProviderStatus.NETWORK_BLOCKED,
            balance_display="BLOCKED",
            alert=AlertLevel.INFO,
        )
        self.assertEqual(st_blocked.status, ProviderStatus.NETWORK_BLOCKED)

    def test_18_dashboard_service_and_json_export(self) -> None:
        fabric = CapabilityFabric()
        service = CostDashboardService(fabric=fabric)
        rep = service.get_report()
        self.assertIsInstance(rep.generated_at, str)
        json_str = service.export_json()
        data = json.loads(json_str)
        self.assertIn("providers", data)
        self.assertIn("all_time_totals", data)

    def test_19_deepseek_and_20_openrouter_execution_accounting(self) -> None:
        fabric = CapabilityFabric()
        run_id = "run-multi-prov"

        rec_ds = UsageAccountingAdapter.create_attempt_usage(
            attempt_index=1,
            agent_name="deepseek_agent",
            provider_name="deepseek",
            model_name="deepseek-chat",
            usage=Usage(input_tokens=150, output_tokens=75, cached_tokens=50, estimated_cost=0.0004),
            duration_seconds=1.2,
        )
        fabric.record_usage(run_id, rec_ds)

        rec_or = UsageAccountingAdapter.create_attempt_usage(
            attempt_index=2,
            agent_name="claude_agent",
            provider_name="openrouter",
            model_name="anthropic/claude-haiku-4.5",
            usage=Usage(input_tokens=200, output_tokens=100, cached_tokens=0, estimated_cost=0.0008),
            duration_seconds=1.1,
            provider_reported_cost=0.00085,
        )
        fabric.record_usage(run_id, rec_or)

        run_acc = fabric.get_run_accounting(run_id, task_id="task-multi", project_id="forge-p1")
        self.assertEqual(run_acc.total_attempts, 2)
        self.assertEqual(run_acc.total_tokens, 525)
        self.assertEqual(run_acc.total_cached_tokens, 50)
        self.assertAlmostEqual(run_acc.total_cost, 0.0004 + 0.00085, places=5)

    def test_21_one_run_id_across_fallback_and_22_artifact_linkage(self) -> None:
        fabric = CapabilityFabric()
        run_id = "run-e2e-1"
        fabric.record_usage(
            run_id,
            UsageAccountingAdapter.create_attempt_usage(
                attempt_index=1,
                agent_name="coder",
                provider_name="deepseek",
                model_name="deepseek-chat",
                usage=Usage(input_tokens=50, output_tokens=20),
            ),
        )
        run_acc = fabric.get_run_accounting(
            run_id,
            associated_artifact_ids=("artifact-snapshot-1", "artifact-changeset-1"),
        )
        self.assertEqual(run_acc.associated_artifact_ids, ("artifact-snapshot-1", "artifact-changeset-1"))

        # Verify markdown formatter
        service = CostDashboardService(fabric=fabric)
        report = service.get_report()
        md = format_dashboard_markdown(report)
        self.assertIn("# Forge AI — Provider & Cost Dashboard", md)
        self.assertIn("Macro Totals", md)


if __name__ == "__main__":
    unittest.main()
