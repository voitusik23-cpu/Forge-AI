"""High-level service coordinating telemetries, aggregation, and health monitoring."""

from __future__ import annotations

import datetime
import json
from typing import Dict, List, Optional, Sequence

from app.dashboard.aggregator import DashboardAggregator
from app.dashboard.models import DashboardReport, ProviderAccountStatus
from app.dashboard.provider_health import ProviderHealthMonitor
from app.fabric.fabric import CapabilityFabric
from app.fabric.models import RunAccountingRecord


class CostDashboardService:
    """Consolidated provider health and cost dashboard service."""

    def __init__(
        self,
        fabric: Optional[CapabilityFabric] = None,
        health_monitor: Optional[ProviderHealthMonitor] = None,
        run_records: Optional[Sequence[RunAccountingRecord]] = None,
    ) -> None:
        self._fabric = fabric
        self._health_monitor = health_monitor or ProviderHealthMonitor()
        self._manual_records: List[RunAccountingRecord] = list(run_records or [])

    def record_run_accounting(self, record: RunAccountingRecord) -> None:
        """Add a run accounting record manually or directly."""
        self._manual_records.append(record)

    def get_run_records(self) -> List[RunAccountingRecord]:
        """Fetch all run accounting records from Fabric and manual buffer."""
        records: List[RunAccountingRecord] = []
        if self._fabric is not None:
            records.extend(self._fabric.get_all_run_accounting())
        records.extend(self._manual_records)
        return records

    def refresh_provider_accounts(self) -> Dict[str, ProviderAccountStatus]:
        """Refresh provider health and balance without running LLM completions."""
        return self._health_monitor.check_all(force=True)

    def get_report(self, force_refresh_health: bool = False) -> DashboardReport:
        """Generate a complete, consolidated dashboard report."""
        records = self.get_run_records()
        account_statuses = self._health_monitor.check_all(force=force_refresh_health)

        providers = DashboardAggregator.aggregate_by_provider(records, account_statuses)
        models = DashboardAggregator.aggregate_by_model(records)
        agents = DashboardAggregator.aggregate_by_agent(records)
        projects = DashboardAggregator.aggregate_by_project(records)
        runs = DashboardAggregator.summarize_runs(records)
        totals = DashboardAggregator.calculate_totals(records)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        return DashboardReport(
            providers=tuple(providers),
            models=tuple(models),
            agents=tuple(agents),
            projects=tuple(projects),
            runs=tuple(runs),
            today_totals=totals,
            all_time_totals=totals,
            generated_at=now,
        )

    def export_json(self, indent: int = 2) -> str:
        """Export full dashboard report as serialized JSON."""
        report = self.get_report()
        return json.dumps(report.to_dict(), indent=indent)
