"""Data contracts and schemas for Forge API v0.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class HealthResponse:
    """Read-only health check response for Forge API and Core."""

    status: str
    core_status: str
    version: str = "0.1.0"
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "core_status": self.core_status,
            "version": self.version,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class SystemStatusResponse:
    """Comprehensive system status across Core, API, and Provider layer."""

    core: str
    api: str
    provider_status: str
    providers_ready: int
    providers_total: int
    active_project: str = "default"
    timestamp: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "core": self.core,
            "api": self.api,
            "provider_status": self.provider_status,
            "providers_ready": self.providers_ready,
            "providers_total": self.providers_total,
            "active_project": self.active_project,
            "timestamp": self.timestamp,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class TaskRunRequest:
    """Request payload for dispatching a task through Forge Core."""

    description: str
    category: str = "code"
    task_id: Optional[str] = None
    provider_name: Optional[str] = None
    project_id: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "category": self.category,
            "task_id": self.task_id,
            "provider_name": self.provider_name,
            "project_id": self.project_id,
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class TaskRunResponse:
    """Response payload returning results and telemetry from Forge Core."""

    run_id: str
    task_id: str
    project_id: str
    state: str
    success: bool
    output: str
    error: Optional[str] = None
    tokens: int = 0
    cost: float = 0.0
    duration_seconds: float = 0.0
    provider_used: str = ""
    model_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "state": self.state,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "tokens": self.tokens,
            "cost": self.cost,
            "duration_seconds": self.duration_seconds,
            "provider_used": self.provider_used,
            "model_used": self.model_used,
        }


@dataclass(frozen=True)
class ProjectInfo:
    """Metadata describing a Forge project or workspace."""

    project_id: str
    name: str
    path: str
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "path": self.path,
            "status": self.status,
        }


@dataclass(frozen=True)
class AgentInfo:
    """Metadata describing an available agent or provider model."""

    name: str
    provider: str
    model: str
    status: str = "available"
    capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "capabilities": list(self.capabilities),
        }
