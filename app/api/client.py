"""Client abstraction for Desktop UI and external clients to interact with Forge API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from app.api.models import (
    AgentInfo,
    HealthResponse,
    ProjectInfo,
    SystemStatusResponse,
    TaskRunRequest,
    TaskRunResponse,
)
from app.api.service import ForgeApiService
from app.dashboard.models import DashboardReport


class ForgeApiClient:
    """Client for communicating with Forge API in-process or over HTTP."""

    def __init__(
        self,
        api_service: Optional[ForgeApiService] = None,
        base_url: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self._service = api_service
        self._base_url = base_url.rstrip("/") if base_url else None
        self._timeout = timeout

    @property
    def is_in_process(self) -> bool:
        return self._service is not None

    def check_health(self) -> HealthResponse:
        """Check API and Core health."""
        if self._service is not None:
            return self._service.get_health()

        if self._base_url:
            req = urllib.request.Request(f"{self._base_url}/health")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    data = json.loads(resp.read().decode())
                    return HealthResponse(
                        status=data.get("status", "unknown"),
                        core_status=data.get("core_status", "unknown"),
                        version=data.get("version", "0.1.0"),
                        timestamp=data.get("timestamp", ""),
                    )
            except Exception as e:
                return HealthResponse(
                    status="error",
                    core_status="disconnected",
                    version="0.1.0",
                    timestamp="",
                )

        return HealthResponse(status="disconnected", core_status="disconnected")

    def get_system_status(self) -> SystemStatusResponse:
        """Get comprehensive system status."""
        if self._service is not None:
            return self._service.get_system_status()

        if self._base_url:
            req = urllib.request.Request(f"{self._base_url}/api/status")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    data = json.loads(resp.read().decode())
                    return SystemStatusResponse(
                        core=data.get("core", "disconnected"),
                        api=data.get("api", "disconnected"),
                        provider_status=data.get("provider_status", "UNKNOWN"),
                        providers_ready=data.get("providers_ready", 0),
                        providers_total=data.get("providers_total", 0),
                        active_project=data.get("active_project", "default"),
                        timestamp=data.get("timestamp", ""),
                        details=data.get("details", {}),
                    )
            except Exception:
                return SystemStatusResponse(
                    core="disconnected",
                    api="error",
                    provider_status="UNKNOWN",
                    providers_ready=0,
                    providers_total=0,
                )

        return SystemStatusResponse(
            core="disconnected",
            api="disconnected",
            provider_status="UNKNOWN",
            providers_ready=0,
            providers_total=0,
        )

    def list_projects(self) -> List[ProjectInfo]:
        """List available workspace projects."""
        if self._service is not None:
            return self._service.list_projects()
        return [ProjectInfo(project_id="default", name="Forge Main Workspace", path="", status="active")]

    def list_agents(self) -> List[AgentInfo]:
        """List configured agents."""
        if self._service is not None:
            return self._service.list_agents()
        return []

    def get_dashboard(self, force_refresh: bool = False) -> DashboardReport:
        """Retrieve full provider and cost dashboard report."""
        if self._service is not None:
            return self._service.get_dashboard_report(force_refresh=force_refresh)
        return DashboardReport()

    def refresh_dashboard(self) -> Dict[str, Any]:
        """Trigger lightweight provider health refresh."""
        if self._service is not None:
            return self._service.refresh_dashboard()
        return {"refreshed_at": "", "providers_checked": 0}

    def run_task(
        self,
        description: str,
        category: str = "code",
        provider_name: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> TaskRunResponse:
        """Dispatch a task to Forge Core."""
        req = TaskRunRequest(
            description=description,
            category=category,
            provider_name=provider_name,
            project_id=project_id,
        )
        if self._service is not None:
            return self._service.run_task(req)

        return TaskRunResponse(
            run_id="",
            task_id="",
            project_id="",
            state="FAILED",
            success=False,
            output="",
            error="API service unavailable",
        )
