"""Formatters for rendering DashboardReport as Markdown and CLI tables."""

from __future__ import annotations

from typing import Optional
from app.dashboard.models import AlertLevel, DashboardReport


def format_dashboard_markdown(report: DashboardReport) -> str:
    """Render full DashboardReport as a clean GitHub-flavored Markdown document."""
    lines: list[str] = [
        "# Forge AI — Provider & Cost Dashboard",
        f"*Generated at: {report.generated_at}*",
        "",
        "## 1. Provider Accounts & Forge Consumption",
        "",
        "| Provider | Status | Balance / Quota | Forge Spent | Requests | Runs | Total Tokens | Cost | Alert |",
        "|:---|:---|:---|:---|:---|:---|:---|:---|:---|",
    ]

    for p in report.providers:
        bal = p.account_status.balance_display
        status = p.account_status.status.value
        alert_badge = f"**{p.account_status.alert.value}**"
        spent_str = f"${p.forge_spent:.4f}" if p.forge_spent > 0 else "$0.00"
        cost_str = f"${p.effective_cost:.4f}" if p.effective_cost > 0 else "$0.00"
        tok_str = f"{p.total_tokens:,}" if p.total_tokens > 0 else "0"

        lines.append(
            f"| `{p.provider}` | `{status}` | {bal} | {spent_str} | {p.total_requests} | {p.total_runs} | {tok_str} | {cost_str} | {alert_badge} |"
        )

    tot = report.all_time_totals
    lines.extend([
        "",
        "## 2. Macro Totals",
        "",
        f"- **Total Forge Spent:** `${tot.total_spent:.4f}`",
        f"- **Total Runs:** `{tot.total_runs}` (`{tot.successful_runs}` successful, `{tot.failed_runs}` failed)",
        f"- **Total Requests:** `{tot.total_requests}`",
        f"- **Total Tokens:** `{tot.total_tokens:,}`",
        f"- **Average Run Cost:** `${tot.avg_run_cost:.4f}`",
        "",
    ])

    if report.models:
        lines.extend([
            "## 3. Model Consumption Breakdown",
            "",
            "| Provider | Model | Requests | In Tokens | Out Tokens | Cached Tokens | Total Tokens | Cost |",
            "|:---|:---|:---|:---|:---|:---|:---|:---|",
        ])
        for m in report.models:
            cost_str = f"${m.effective_cost:.4f}" if m.effective_cost > 0 else "$0.00"
            lines.append(
                f"| `{m.provider}` | `{m.model}` | {m.total_requests} | {m.input_tokens:,} | {m.output_tokens:,} | {m.cached_tokens:,} | {m.total_tokens:,} | {cost_str} |"
            )
        lines.append("")

    if report.agents:
        lines.extend([
            "## 4. Agent Attribution Breakdown",
            "",
            "| Agent | Provider | Model | Runs | Requests | Total Tokens | Cost |",
            "|:---|:---|:---|:---|:---|:---|:---|",
        ])
        for a in report.agents:
            cost_str = f"${a.effective_cost:.4f}" if a.effective_cost > 0 else "$0.00"
            lines.append(
                f"| `{a.agent}` | `{a.provider}` | `{a.model}` | {a.total_runs} | {a.total_requests} | {a.total_tokens:,} | {cost_str} |"
            )
        lines.append("")

    if report.projects:
        lines.extend([
            "## 5. Project Attribution Breakdown",
            "",
            "| Project ID | Runs | Total Tokens | Total Cost |",
            "|:---|:---|:---|:---|",
        ])
        for pr in report.projects:
            cost_str = f"${pr.effective_cost:.4f}" if pr.effective_cost > 0 else "$0.00"
            lines.append(
                f"| `{pr.project_id}` | {pr.total_runs} | {pr.total_tokens:,} | {cost_str} |"
            )
        lines.append("")

    if report.runs:
        lines.extend([
            "## 6. Recent Runs",
            "",
            "| Run ID | Task ID | Project | Provider / Model | Tokens | Cost | Duration | Status |",
            "|:---|:---|:---|:---|:---|:---|:---|:---|",
        ])
        for r in report.runs:
            status = "SUCCESS" if r.success else "FAILED"
            prov_mod = f"{r.provider} ({r.model})" if r.provider else "N/A"
            cost_str = f"${r.effective_cost:.4f}" if r.effective_cost > 0 else "$0.00"
            lines.append(
                f"| `{r.run_id[:16]}...` | `{r.task_id}` | `{r.project_id}` | `{prov_mod}` | {r.total_tokens:,} | {cost_str} | {r.duration_seconds:.2f}s | `{status}` |"
            )
        lines.append("")

    return "\n".join(lines)
