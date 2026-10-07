"""Domain contracts and data models for Forge AI Provider & Cost Dashboard v0.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple


class ProviderStatus(str, Enum):
    """Operational and authentication status of an AI provider account."""

    READY = "READY"                      # Direct provider configured, authenticated, and has positive balance
    READY_GATEWAY = "READY/GATEWAY"      # Provider accessible via gateway (e.g. Claude via OpenRouter)
    FREE_TIER = "FREE_TIER"              # Active free or quota-based tier (e.g. Gemini)
    NO_CREDITS = "NO_CREDITS"            # Authenticated but exhausted balance / 0 credits
    NETWORK_BLOCKED = "NETWORK_BLOCKED"  # Connection error or geo/firewall block
    AUTH_FAILED = "AUTH_FAILED"          # API key invalid / unauthorized
    NOT_CONFIGURED = "NOT_CONFIGURED"    # Key not set in environment or secret store
    UNAVAILABLE = "UNAVAILABLE"          # Provider disabled or service error


class AlertLevel(str, Enum):
    """Visual threshold status for provider balance and quota."""

    HEALTHY = "GREEN"
    WARNING = "YELLOW"
    CRITICAL = "RED"
    INFO = "INFO"


@dataclass(frozen=True)
class ProviderThresholdConfig:
    """Configurable balance thresholds for alerting."""

    warning_balance: float = 2.00
    critical_balance: float = 0.50

    @classmethod
    def default_for_provider(cls, provider_name: str) -> ProviderThresholdConfig:
        norm = provider_name.strip().lower()
        if norm in ("deepseek", "deepseek direct"):
            return cls(warning_balance=1.00, critical_balance=0.20)
        if norm in ("openrouter", "openrouter gateway"):
            return cls(warning_balance=2.00, critical_balance=0.50)
        if norm in ("openai", "openai direct", "xai", "anthropic"):
            return cls(warning_balance=5.00, critical_balance=1.00)
        return cls()


@dataclass(frozen=True)
class ProviderAccountStatus:
    """Snapshot of a provider account's health, balance, and alert status."""

    provider: str
    status: ProviderStatus
    balance_display: str
    balance_numeric: Optional[float] = None
    quota_limit: Optional[float] = None
    quota_usage: Optional[float] = None
    alert: AlertLevel = AlertLevel.HEALTHY
    last_checked: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.details, dict):
            object.__setattr__(self, "details", dict(self.details))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "balance_display": self.balance_display,
            "balance_numeric": self.balance_numeric,
            "quota_limit": self.quota_limit,
            "quota_usage": self.quota_usage,
            "alert": self.alert.value,
            "last_checked": self.last_checked,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ProviderUsageSummary:
    """Aggregated usage and cost metrics per provider."""

    provider: str
    account_status: ProviderAccountStatus
    forge_spent: float = 0.0
    total_requests: int = 0
    total_runs: int = 0
    total_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    provider_reported_cost: float = 0.0
    effective_cost: float = 0.0
    retry_count: int = 0
    fallback_count: int = 0
    success_count: int = 0
    failure_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "account_status": self.account_status.to_dict(),
            "forge_spent": self.forge_spent,
            "total_requests": self.total_requests,
            "total_runs": self.total_runs,
            "total_attempts": self.total_attempts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": self.estimated_cost,
            "provider_reported_cost": self.provider_reported_cost,
            "effective_cost": self.effective_cost,
            "retry_count": self.retry_count,
            "fallback_count": self.fallback_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }


@dataclass(frozen=True)
class ModelUsageSummary:
    """Aggregated usage and cost metrics per model."""

    provider: str
    model: str
    total_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    effective_cost: float = 0.0
    success_count: int = 0
    failure_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "total_requests": self.total_requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "effective_cost": self.effective_cost,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }


@dataclass(frozen=True)
class AgentUsageSummary:
    """Aggregated usage and cost metrics per agent."""

    agent: str
    provider: str
    model: str
    total_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    effective_cost: float = 0.0
    total_runs: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "provider": self.provider,
            "model": self.model,
            "total_requests": self.total_requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "effective_cost": self.effective_cost,
            "total_runs": self.total_runs,
        }


@dataclass(frozen=True)
class ProjectUsageSummary:
    """Aggregated usage and cost metrics per project."""

    project_id: str
    total_runs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    effective_cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "total_runs": self.total_runs,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "effective_cost": self.effective_cost,
        }


@dataclass(frozen=True)
class RunSummary:
    """Single run telemetry summary with attempt and artifact linkages."""

    run_id: str
    task_id: str = ""
    project_id: str = ""
    agent: str = ""
    provider: str = ""
    model: str = ""
    attempts: int = 1
    requests: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    effective_cost: float = 0.0
    duration_seconds: float = 0.0
    success: bool = True
    associated_artifacts: Tuple[str, ...] = ()
    fallback_events: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.associated_artifacts, (list, tuple)):
            object.__setattr__(self, "associated_artifacts", tuple(self.associated_artifacts))
        if isinstance(self.fallback_events, (list, tuple)):
            object.__setattr__(self, "fallback_events", tuple(self.fallback_events))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "agent": self.agent,
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "effective_cost": self.effective_cost,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "associated_artifacts": list(self.associated_artifacts),
            "fallback_events": list(self.fallback_events),
        }


@dataclass(frozen=True)
class DashboardTotals:
    """Overall metric totals for a timeframe (e.g. Today or All Time)."""

    total_spent: float = 0.0
    total_runs: int = 0
    total_requests: int = 0
    total_tokens: int = 0
    avg_run_cost: float = 0.0
    successful_runs: int = 0
    failed_runs: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_spent": self.total_spent,
            "total_runs": self.total_runs,
            "total_requests": self.total_requests,
            "total_tokens": self.total_tokens,
            "avg_run_cost": self.avg_run_cost,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
        }


@dataclass(frozen=True)
class DashboardReport:
    """Complete consolidated read-model report for the Dashboard."""

    providers: Tuple[ProviderUsageSummary, ...] = ()
    models: Tuple[ModelUsageSummary, ...] = ()
    agents: Tuple[AgentUsageSummary, ...] = ()
    projects: Tuple[ProjectUsageSummary, ...] = ()
    runs: Tuple[RunSummary, ...] = ()
    today_totals: DashboardTotals = field(default_factory=DashboardTotals)
    all_time_totals: DashboardTotals = field(default_factory=DashboardTotals)
    generated_at: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.providers, list):
            object.__setattr__(self, "providers", tuple(self.providers))
        if isinstance(self.models, list):
            object.__setattr__(self, "models", tuple(self.models))
        if isinstance(self.agents, list):
            object.__setattr__(self, "agents", tuple(self.agents))
        if isinstance(self.projects, list):
            object.__setattr__(self, "projects", tuple(self.projects))
        if isinstance(self.runs, list):
            object.__setattr__(self, "runs", tuple(self.runs))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "providers": [p.to_dict() for p in self.providers],
            "models": [m.to_dict() for m in self.models],
            "agents": [a.to_dict() for a in self.agents],
            "projects": [pr.to_dict() for pr in self.projects],
            "runs": [r.to_dict() for r in self.runs],
            "today_totals": self.today_totals.to_dict(),
            "all_time_totals": self.all_time_totals.to_dict(),
            "generated_at": self.generated_at,
        }
