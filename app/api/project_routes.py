"""Read-mostly API additions for the persistent project lifecycle.

```
GET  /api/projects/lifecycle                     -> [project summary]
GET  /api/projects/{id}                          -> full record + versions + files
POST /api/projects/{id}/versions  {change}       -> new version + the task text
POST /api/projects/{id}/archive                  -> archived record
POST /api/projects/{id}/restore                  -> record back at its old status
GET  /api/projects/{id}/history                  -> versions, newest first
GET  /api/projects/{id}/files?version=           -> one version's artifact manifest
POST /api/projects/{id}/verify                   -> re-judged record
```

Three properties are deliberate:

* **The existing ``GET /api/projects`` is untouched.** It still returns the
  workspace project from ``ForgeApiService.list_projects``; the lifecycle list is a
  different path (``/api/projects/lifecycle``), and it is registered *before*
  ``/api/projects/{project_id}`` so the literal segment can never be captured as an
  id by the parameterised route.
* **No absolute path and no file content** reaches a payload. A project publishes
  its repository-relative ``project_path``; an artifact publishes a
  workspace-relative path plus a repository-relative ``location``, a size, a
  SHA-256, its create/update change and its verification relevance.
* **Failures use the surface's existing error contract.** An unknown project is
  ``404``, a malformed body is ``400``, and an impossible lifecycle move is
  ``409`` with the layer's own code published verbatim.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["register_project_routes"]


def register_project_routes(app: Any, projects: Any) -> None:
    """Attach the lifecycle routes to ``app`` and publish the service on its state.

    The service is created once, at composition, so every request of one process
    shares one store and one view of the projects -- exactly as the discovery
    service is composed.
    """

    from fastapi import HTTPException

    from app.api.routes import body_error_exception, error_body
    from app.projects.lifecycle import IllegalTransition
    from app.projects.service import InvalidChangeRequest, UnknownProjectVersion
    from app.projects.store import UnknownProject

    app.state.forge_projects = projects

    def _refusal(exc: Exception) -> "HTTPException":
        if isinstance(exc, (UnknownProject, UnknownProjectVersion)):
            status = 404
        elif isinstance(exc, InvalidChangeRequest):
            status = 400
        elif isinstance(exc, IllegalTransition):
            status = 409
        else:
            status = 409
        code = str(getattr(exc, "code", "project_error"))
        return HTTPException(
            status_code=status,
            detail={**error_body(error_code=code, code=code), "error": str(exc)},
        )

    def _project_id(project_id: str) -> str:
        if not str(project_id or "").strip():
            raise _refusal(UnknownProject(""))
        return project_id

    def _artifact_payload(project: Any, artifact: Any) -> Dict[str, Any]:
        return {
            **artifact.to_dict(),
            # The one place the repository-relative location is composed, so a
            # client never has to join two fields itself (and cannot join them
            # into an absolute path).
            "location": f"{project.project_path}/{artifact.path}",
        }

    def _detail(project: Any) -> Dict[str, Any]:
        return {
            "project": project.to_dict(),
            "versions": [item.summary() for item in reversed(project.versions)],
            "files": [
                _artifact_payload(project, artifact)
                for artifact in (project.current().files if project.current() else ())
            ],
        }

    def _guard(call: Any) -> Any:
        try:
            return call()
        except (UnknownProject, UnknownProjectVersion, InvalidChangeRequest, IllegalTransition) as exc:
            raise _refusal(exc) from exc

    # Registered BEFORE the ``{project_id}`` route below: FastAPI matches in
    # registration order, so a literal path must come first or it would be read as
    # an id.
    @app.get("/api/projects/lifecycle")
    def list_project_lifecycle(archived: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Every project, newest change first, as one bounded summary line each."""

        include_archived = True if archived is None else bool(archived)
        return [
            project.summary()
            for project in projects.list_projects(include_archived=include_archived)
        ]

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> Dict[str, Any]:
        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        return _detail(project)

    @app.post("/api/projects/{project_id}/versions")
    def create_project_version(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        change = payload.get("change")
        if not isinstance(change, str) or not change.strip():
            raise body_error_exception("'change' must be a non-empty string")
        result = _guard(
            lambda: projects.new_version(_project_id(project_id), change)
        )
        return {
            "project": result.project.to_dict(),
            "version": result.version,
            "task_text": result.task_text,
            "change_request": result.change_request.to_dict(),
        }

    @app.post("/api/projects/{project_id}/archive")
    def archive_project(project_id: str) -> Dict[str, Any]:
        project = _guard(lambda: projects.archive(_project_id(project_id)))
        return {"project": project.to_dict()}

    @app.post("/api/projects/{project_id}/restore")
    def restore_project(project_id: str) -> Dict[str, Any]:
        project = _guard(lambda: projects.restore(_project_id(project_id)))
        return {"project": project.to_dict()}

    @app.get("/api/projects/{project_id}/history")
    def project_history(project_id: str) -> Dict[str, Any]:
        versions = _guard(lambda: projects.history(_project_id(project_id)))
        return {
            "project_id": str(project_id),
            "count": len(versions),
            "versions": [item.to_dict() for item in versions],
        }

    @app.get("/api/projects/{project_id}/files")
    def project_files(project_id: str, version: Optional[int] = None) -> Dict[str, Any]:
        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        resolved = (
            project.current_version if version is None else int(version)
        )
        artifacts = _guard(
            lambda: projects.files(project.project_id, None if version is None else version)
        )
        return {
            "project_id": project.project_id,
            "version": resolved,
            "count": len(artifacts),
            "files": [_artifact_payload(project, artifact) for artifact in artifacts],
        }

    @app.post("/api/projects/{project_id}/verify")
    def verify_project(project_id: str) -> Dict[str, Any]:
        project = _guard(lambda: projects.verify_again(_project_id(project_id)))
        current = project.current()
        return {
            "project": project.to_dict(),
            "verified": True,
            "verification": current.verification if current is not None else "",
            "acceptance": current.acceptance if current is not None else "",
            "status": project.status.value,
        }
