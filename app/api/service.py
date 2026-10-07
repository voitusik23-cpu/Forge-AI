"""Core API service bridging HTTP/UI endpoints to Forge Core subsystems."""

from __future__ import annotations

import datetime
import time
import uuid
from typing import Any, Dict, List, Optional

from app.api.models import (
    AgentInfo,
    HealthResponse,
    ProjectInfo,
    SystemStatusResponse,
    TaskRunRequest,
    TaskRunResponse,
)
from app.dashboard.models import DashboardReport, ProviderStatus
from app.dashboard.provider_health import ProviderHealthMonitor
from app.dashboard.service import CostDashboardService
from app.fabric.fabric import CapabilityFabric
from app.orchestrator.models import RunState, Task, TaskCategory
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_runtime
from app.runtime.context import RuntimeContext
from app.tools.workspace import Workspace


from pathlib import Path


class ForgeApiService:
    """Service handling API requests and delegating to Forge Core without exposing secrets."""

    def __init__(
        self,
        runtime: Optional[RuntimeContext] = None,
        fabric: Optional[CapabilityFabric] = None,
        dashboard_service: Optional[CostDashboardService] = None,
        workspace: Optional[Workspace] = None,
    ) -> None:
        self._runtime = runtime or create_runtime()
        self._workspace = workspace or Workspace(Path.cwd())
        self._fabric = fabric or CapabilityFabric(
            workspace=self._workspace,
        )
        self._health_monitor = ProviderHealthMonitor()
        self._dashboard_service = dashboard_service or CostDashboardService(
            fabric=self._fabric,
            health_monitor=self._health_monitor,
        )
        self._active_project_id = "default"

    @property
    def runtime(self) -> RuntimeContext:
        return self._runtime

    @property
    def fabric(self) -> CapabilityFabric:
        return self._fabric

    @property
    def dashboard_service(self) -> CostDashboardService:
        return self._dashboard_service

    def get_health(self) -> HealthResponse:
        """Return basic liveness and Core connection status."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        core_status = "connected" if self._runtime is not None else "disconnected"
        return HealthResponse(
            status="ok",
            core_status=core_status,
            version="0.1.0",
            timestamp=now,
        )

    def get_system_status(self) -> SystemStatusResponse:
        """Return detailed status of Core, API, and Provider layers."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        core_status = "connected" if self._runtime is not None else "disconnected"

        # Check provider accounts status
        account_statuses = self._health_monitor.check_all(force=False)
        ready_count = sum(
            1 for st in account_statuses.values()
            if st.status in (ProviderStatus.READY, ProviderStatus.READY_GATEWAY, ProviderStatus.FREE_TIER)
        )
        total_count = len(account_statuses)

        provider_layer_status = "READY" if ready_count > 0 else "DEGRADED"

        return SystemStatusResponse(
            core=core_status,
            api="connected",
            provider_status=provider_layer_status,
            providers_ready=ready_count,
            providers_total=total_count,
            active_project=self._active_project_id,
            timestamp=now,
            details={
                "enabled_providers": list(self._runtime.settings.enabled_providers),
                "default_provider": self._runtime.settings.default_provider,
            },
        )

    def list_projects(self) -> List[ProjectInfo]:
        """List active and known projects."""
        return [
            ProjectInfo(
                project_id=self._active_project_id,
                name="Forge Main Workspace",
                path=str(self._workspace.root),
                status="active",
            )
        ]

    def list_agents(self) -> List[AgentInfo]:
        """List registered agents and models from AgentRegistry."""
        agents: List[AgentInfo] = []
        for name in self._runtime.agent_registry.list_agents():
            try:
                agent = self._runtime.agent_registry.get(name)
                prov_name = getattr(agent, "provider_name", name)
                model_name = getattr(agent, "model_name", "default")
                agents.append(
                    AgentInfo(
                        name=name,
                        provider=prov_name,
                        model=model_name,
                        status="available",
                        capabilities=["code", "analysis", "review"],
                    )
                )
            except Exception:
                agents.append(
                    AgentInfo(
                        name=name,
                        provider=name,
                        model="unknown",
                        status="error",
                    )
                )
        return agents

    def get_dashboard_report(self, force_refresh: bool = False) -> DashboardReport:
        """Retrieve consolidated cost and provider dashboard report."""
        return self._dashboard_service.get_report(force_refresh_health=force_refresh)

    def refresh_dashboard(self) -> Dict[str, Any]:
        """Trigger lightweight provider health refresh."""
        statuses = self._dashboard_service.refresh_provider_accounts()
        return {
            "refreshed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "providers_checked": len(statuses),
        }

    def run_task(self, req: TaskRunRequest) -> TaskRunResponse:
        """Execute a task through Forge Core and return execution telemetry."""
        run_id = f"run-api-{uuid.uuid4().hex[:8]}"
        task_id = req.task_id or f"task-{uuid.uuid4().hex[:6]}"
        project_id = req.project_id or self._active_project_id

        category = TaskCategory.CODE
        try:
            category = TaskCategory(req.category.lower())
        except ValueError:
            category = TaskCategory.CODE

        context = dict(req.context)
        context.update({"run_id": run_id, "project_id": project_id})

        task = Task(
            id=task_id,
            description=req.description,
            category=category,
            context=context,
            parameters={"run_id": run_id},
        )

        t0 = time.perf_counter()
        run = self._runtime.run_executor.execute(
            task=task,
            provider_name=req.provider_name,
            workspace=self._workspace,
            run_id=run_id,
        )
        duration = time.perf_counter() - t0

        # Retrieve run accounting
        run_acc = self._fabric.get_run_accounting(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            total_duration_seconds=duration,
        )

        # Determine output
        output = ""
        error = None
        success = (run.state == RunState.COMPLETED)

        last_att = run_acc.attempt_records[-1] if run_acc.attempt_records else None
        provider_used = last_att.provider_name if last_att else ""
        model_used = last_att.model_name if last_att else ""

        # Check events for result or errors
        for ev in reversed(run.events):
            if ev.data.get("result"):
                res_obj = ev.data.get("result")
                output = getattr(res_obj, "output", "") or str(res_obj)
                break
            if ev.data.get("error"):
                error = str(ev.data.get("error"))

        if not output and run.result:
            output = run.result.output
            if run.result.error:
                error = run.result.error
                success = False

        return TaskRunResponse(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            state=run.state.value,
            success=success,
            output=output or "(Task execution completed)",
            error=error,
            tokens=run_acc.total_tokens,
            cost=round(run_acc.total_cost, 6),
            duration_seconds=round(duration, 3),
            provider_used=provider_used,
            model_used=model_used,
        )
