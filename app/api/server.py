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

    return app
