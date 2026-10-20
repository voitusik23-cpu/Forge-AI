"""Data contracts and schemas for Forge API v0.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.build_info import FORGE_VERSION


@dataclass(frozen=True)
class HealthResponse:
    """Read-only health check response for Forge API and Core."""

    status: str
    core_status: str
    version: str = FORGE_VERSION
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
    #: Opt-in write mode. When true *and* the category is ``code``, the model answer
    #: the live provider produced is passed to the guarded code writer
    #: (``app/agent_runtime/code_writer.py``), which applies it inside the run's own
    #: workspace after validating every path. Default false, so every existing caller
    #: keeps the read-only behaviour it had.
    write_mode: bool = False
    #: The model to run THIS dispatch on, for the provider named above. It is a
    #: per-run override of that provider's configured model and nothing more: it
    #: selects no provider, grants no capability, and defaults to ``None``, which
    #: keeps the configured model (and therefore every existing caller byte-for-byte
    #: unchanged). Before this field existed the HTTP layer dropped a caller's
    #: ``model_name`` entirely, so every non-default model reached the provider as
    #: ``"unconfigured"`` and failed as "model is not configured".
    model_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "category": self.category,
            "task_id": self.task_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "project_id": self.project_id,
            "context": dict(self.context),
            "write_mode": bool(self.write_mode),
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
    #: ``None`` means the usage is **unknown**, which is not the same as zero. A zero
    #: appears only when nothing was consumed or a provider reported zero, and the
    #: dispatch basis (X-Forge-Usage-Basis) says which kind of value this is.
    tokens: Optional[int] = None
    cost: Optional[float] = None
    duration_seconds: float = 0.0
    provider_used: str = ""
    model_used: str = ""
    #: The provider-neutral outcome of the run's *work*
    #: (``app.agent_runtime.outcome.RunOutcome``). ``state`` is the user-facing
    #: word derived from it, so ``COMPLETED`` appears only for
    #: ``PROJECT_COMPLETED``. The empty string means "this entry point produces no
    #: project outcome" -- an unknown outcome, never a completed one.
    outcome: str = ""
    #: The questions the answer asked, when ``outcome`` is
    #: ``CLARIFICATION_REQUIRED``. A plain list of strings, in answer order.
    clarification_questions: List[str] = field(default_factory=list)
    #: True exactly when the work stopped because the model asked for answers.
    needs_clarification: bool = False
    #: Four independent save facts. They are separate booleans on purpose: one
    #: combined "saved" indicator hid an empty project behind a stored run record.
    run_record_saved: bool = False
    project_created: bool = False
    project_files_created: bool = False
    artifact_manifest_saved: bool = False
    #: How many files this run actually wrote (0 for a text-only answer).
    files_created: int = 0
    #: The coarse price category of the provider/model this run used:
    #: ``FREE``, ``CHEAP`` or ``PAID``; ``None`` when the registries do not know it.
    cost_tier: Optional[str] = None
    #: The run's own estimated cost in USD, or ``None`` when no estimate exists.
    #: It is never invented from a zero: unknown is reported as unknown.
    estimated_cost: Optional[float] = None

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
            "outcome": self.outcome,
            "clarification_questions": list(self.clarification_questions),
            "needs_clarification": self.needs_clarification,
            "run_record_saved": self.run_record_saved,
            "project_created": self.project_created,
            "project_files_created": self.project_files_created,
            "artifact_manifest_saved": self.artifact_manifest_saved,
            "files_created": self.files_created,
            "cost_tier": self.cost_tier,
            "estimated_cost": self.estimated_cost,
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
