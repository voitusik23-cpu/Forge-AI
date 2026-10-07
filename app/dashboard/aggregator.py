"""Pure, read-only aggregation engine for RunAccountingRecord telemetry."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from app.dashboard.models import (
    AgentUsageSummary,
    AlertLevel,
    DashboardTotals,
    ModelUsageSummary,
    ProjectUsageSummary,
    ProviderAccountStatus,
    ProviderStatus,
    ProviderUsageSummary,
    RunSummary,
)
from app.fabric.models import AttemptUsageRecord, RunAccountingRecord


class DashboardAggregator:
    """Aggregates RunAccountingRecord sequences across providers, models, agents, projects, and runs."""

    @staticmethod
    def aggregate_by_provider(
        records: Sequence[RunAccountingRecord],
        account_statuses: Optional[Mapping[str, ProviderAccountStatus]] = None,
    ) -> List[ProviderUsageSummary]:
        """Aggregate telemetry and costs grouped by provider."""
        statuses = account_statuses or {}

        # Aggregation state per provider
        prov_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "requests": 0,
            "runs": set(),
            "attempts": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0.0,
            "provider_reported_cost": 0.0,
            "effective_cost": 0.0,
            "retry_count": 0,
            "fallback_count": 0,
            "success_count": 0,
            "failure_count": 0,
        })

        for run in records:
            for att in run.attempt_records:
                prov = att.provider_name or "unknown"
                st = prov_stats[prov]
                st["requests"] += att.request_count
                st["runs"].add(run.run_id)
                st["attempts"] += 1
                st["input_tokens"] += att.input_tokens
                st["output_tokens"] += att.output_tokens
                st["cached_tokens"] += att.cached_tokens
                st["total_tokens"] += att.total_tokens
                st["estimated_cost"] += (att.estimated_cost or 0.0)
                st["provider_reported_cost"] += (att.provider_reported_cost or 0.0)
                st["effective_cost"] += att.effective_cost
                if att.is_fallback:
                    st["fallback_count"] += 1
                if att.success:
                    st["success_count"] += 1
                else:
                    st["failure_count"] += 1

            if run.retry_count > 0 and run.attempt_records:
                # Attribute run retry count to the providers involved
                for att in run.attempt_records:
                    prov_stats[att.provider_name or "unknown"]["retry_count"] += run.retry_count
                    break

        # Include all known configured providers even if 0 requests were made
        all_providers = set(prov_stats.keys()) | set(statuses.keys())
        result: List[ProviderUsageSummary] = []

        for prov in sorted(all_providers):
            st = prov_stats[prov]
            acc_status = statuses.get(
                prov,
                ProviderAccountStatus(
                    provider=prov,
                    status=ProviderStatus.READY if st["requests"] > 0 else ProviderStatus.NOT_CONFIGURED,
                    balance_display="UNKNOWN",
                    alert=AlertLevel.HEALTHY if st["requests"] > 0 else AlertLevel.INFO,
                ),
            )
            result.append(
                ProviderUsageSummary(
                    provider=prov,
                    account_status=acc_status,
                    forge_spent=round(st["effective_cost"], 6),
                    total_requests=st["requests"],
                    total_runs=len(st["runs"]),
                    total_attempts=st["attempts"],
                    input_tokens=st["input_tokens"],
                    output_tokens=st["output_tokens"],
                    cached_tokens=st["cached_tokens"],
                    total_tokens=st["total_tokens"],
                    estimated_cost=round(st["estimated_cost"], 6),
                    provider_reported_cost=round(st["provider_reported_cost"], 6),
                    effective_cost=round(st["effective_cost"], 6),
                    retry_count=st["retry_count"],
                    fallback_count=st["fallback_count"],
                    success_count=st["success_count"],
                    failure_count=st["failure_count"],
                )
            )

        return result

    @staticmethod
    def aggregate_by_model(
        records: Sequence[RunAccountingRecord],
    ) -> List[ModelUsageSummary]:
        """Aggregate usage and costs grouped by (provider, model)."""
        model_stats: Dict[tuple[str, str], Dict[str, Any]] = defaultdict(lambda: {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "effective_cost": 0.0,
            "success_count": 0,
            "failure_count": 0,
        })

        for run in records:
            for att in run.attempt_records:
                prov = att.provider_name or "unknown"
                model = att.model_name or "unknown"
                st = model_stats[(prov, model)]
                st["requests"] += att.request_count
                st["input_tokens"] += att.input_tokens
                st["output_tokens"] += att.output_tokens
                st["cached_tokens"] += att.cached_tokens
                st["total_tokens"] += att.total_tokens
                st["effective_cost"] += att.effective_cost
                if att.success:
                    st["success_count"] += 1
                else:
                    st["failure_count"] += 1

        result: List[ModelUsageSummary] = []
        for (prov, model), st in sorted(model_stats.items()):
            result.append(
                ModelUsageSummary(
                    provider=prov,
                    model=model,
                    total_requests=st["requests"],
                    input_tokens=st["input_tokens"],
                    output_tokens=st["output_tokens"],
                    cached_tokens=st["cached_tokens"],
                    total_tokens=st["total_tokens"],
                    effective_cost=round(st["effective_cost"], 6),
                    success_count=st["success_count"],
                    failure_count=st["failure_count"],
                )
            )
        return result

    @staticmethod
    def aggregate_by_agent(
        records: Sequence[RunAccountingRecord],
    ) -> List[AgentUsageSummary]:
        """Aggregate usage and costs grouped by agent."""
        agent_stats: Dict[tuple[str, str, str], Dict[str, Any]] = defaultdict(lambda: {
            "requests": 0,
            "runs": set(),
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "effective_cost": 0.0,
        })

        for run in records:
            for att in run.attempt_records:
                agent = att.agent_name or "unknown"
                prov = att.provider_name or "unknown"
                model = att.model_name or "unknown"
                st = agent_stats[(agent, prov, model)]
                st["requests"] += att.request_count
                st["runs"].add(run.run_id)
                st["input_tokens"] += att.input_tokens
                st["output_tokens"] += att.output_tokens
                st["cached_tokens"] += att.cached_tokens
                st["total_tokens"] += att.total_tokens
                st["effective_cost"] += att.effective_cost

        result: List[AgentUsageSummary] = []
        for (agent, prov, model), st in sorted(agent_stats.items()):
            result.append(
                AgentUsageSummary(
                    agent=agent,
                    provider=prov,
                    model=model,
                    total_requests=st["requests"],
                    input_tokens=st["input_tokens"],
                    output_tokens=st["output_tokens"],
                    cached_tokens=st["cached_tokens"],
                    total_tokens=st["total_tokens"],
                    effective_cost=round(st["effective_cost"], 6),
                    total_runs=len(st["runs"]),
                )
            )
        return result

    @staticmethod
    def aggregate_by_project(
        records: Sequence[RunAccountingRecord],
    ) -> List[ProjectUsageSummary]:
        """Aggregate usage and costs grouped by project_id."""
        proj_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "runs": set(),
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "effective_cost": 0.0,
        })

        for run in records:
            pid = run.project_id or "default"
            st = proj_stats[pid]
            st["runs"].add(run.run_id)
            st["input_tokens"] += run.total_input_tokens
            st["output_tokens"] += run.total_output_tokens
            st["cached_tokens"] += run.total_cached_tokens
            st["total_tokens"] += run.total_tokens
            st["effective_cost"] += run.total_cost

        result: List[ProjectUsageSummary] = []
        for pid, st in sorted(proj_stats.items()):
            result.append(
                ProjectUsageSummary(
                    project_id=pid,
                    total_runs=len(st["runs"]),
                    input_tokens=st["input_tokens"],
                    output_tokens=st["output_tokens"],
                    cached_tokens=st["cached_tokens"],
                    total_tokens=st["total_tokens"],
                    effective_cost=round(st["effective_cost"], 6),
                )
            )
        return result

    @staticmethod
    def summarize_runs(
        records: Sequence[RunAccountingRecord],
    ) -> List[RunSummary]:
        """Produce tabular run summaries linking attempts, costs, and artifacts."""
        summaries: List[RunSummary] = []
        for run in records:
            last_att = run.attempt_records[-1] if run.attempt_records else None
            success = any(att.success for att in run.attempt_records) if run.attempt_records else True

            summaries.append(
                RunSummary(
                    run_id=run.run_id,
                    task_id=run.task_id,
                    project_id=run.project_id,
                    agent=last_att.agent_name if last_att else "",
                    provider=last_att.provider_name if last_att else "",
                    model=last_att.model_name if last_att else "",
                    attempts=run.total_attempts,
                    requests=run.total_requests,
                    input_tokens=run.total_input_tokens,
                    output_tokens=run.total_output_tokens,
                    cached_tokens=run.total_cached_tokens,
                    total_tokens=run.total_tokens,
                    effective_cost=round(run.total_cost, 6),
                    duration_seconds=round(run.total_duration_seconds, 3),
                    success=success,
                    associated_artifacts=run.associated_artifact_ids,
                    fallback_events=run.fallback_events,
                )
            )
        return summaries

    @staticmethod
    def calculate_totals(
        records: Sequence[RunAccountingRecord],
    ) -> DashboardTotals:
        """Calculate macro totals for all runs in the sequence."""
        if not records:
            return DashboardTotals()

        total_spent = sum(r.total_cost for r in records)
        total_runs = len(records)
        total_requests = sum(r.total_requests for r in records)
        total_tokens = sum(r.total_tokens for r in records)
        successful_runs = sum(
            1 for r in records if any(att.success for att in r.attempt_records)
        )
        failed_runs = total_runs - successful_runs
        avg_cost = total_spent / total_runs if total_runs > 0 else 0.0

        return DashboardTotals(
            total_spent=round(total_spent, 6),
            total_runs=total_runs,
            total_requests=total_requests,
            total_tokens=total_tokens,
            avg_run_cost=round(avg_cost, 6),
            successful_runs=successful_runs,
            failed_runs=failed_runs,
        )
