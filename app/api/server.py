"""FastAPI / HTTP server construction for Forge API."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService


def create_api_app(service: Optional[ForgeApiService] = None) -> Any:
    """Create a FastAPI application exposing Forge API endpoints."""
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware

    api_service = service or ForgeApiService()
    app = FastAPI(title="Forge AI API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return api_service.get_health().to_dict()

    @app.get("/api/status")
    def status() -> Dict[str, Any]:
        return api_service.get_system_status().to_dict()

    @app.get("/api/projects")
    def list_projects() -> list[Dict[str, Any]]:
        return [p.to_dict() for p in api_service.list_projects()]

    @app.get("/api/agents")
    def list_agents() -> list[Dict[str, Any]]:
        return [a.to_dict() for a in api_service.list_agents()]

    @app.get("/api/dashboard")
    def get_dashboard(force_refresh: bool = False) -> Dict[str, Any]:
        return api_service.get_dashboard_report(force_refresh=force_refresh).to_dict()

    @app.post("/api/dashboard/refresh")
    def refresh_dashboard() -> Dict[str, Any]:
        return api_service.refresh_dashboard()

    @app.get("/api/capabilities")
    def list_capabilities() -> list[Dict[str, Any]]:
        """List the capabilities currently available to Forge.

        Read-only enumeration of the registered model, tool, and workspace
        capabilities. It reports only descriptors and performs no probing or
        scanning. Host environment discovery is not part of this endpoint.
        """
        return api_service.list_capabilities()

    @app.get("/api/tools")
    def list_tools() -> list[Dict[str, Any]]:
        """List the tools currently registered for execution."""
        return api_service.list_tools()

    @app.post("/api/tasks/run")
    def run_task(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload.get("description"):
            raise HTTPException(status_code=400, detail="Missing required field 'description'")
        req = TaskRunRequest(
            description=payload["description"],
            category=payload.get("category", "code"),
            task_id=payload.get("task_id"),
            provider_name=payload.get("provider_name"),
            project_id=payload.get("project_id"),
            context=payload.get("context", {}),
        )
        return api_service.run_task(req).to_dict()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> Dict[str, Any]:
        """Return the durable history of a Run, including after a restart.

        Read-only: this reports persisted state and never resumes, retries, or
        re-executes anything.
        """
        record = api_service.get_run_record(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id '{run_id}'")
        return record

    return app
