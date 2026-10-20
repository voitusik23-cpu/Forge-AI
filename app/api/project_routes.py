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
GET  /api/projects/{id}/entrypoint               -> the one detected entry point, or null
POST /api/projects/{id}/preview  {action}        -> start/stop the read-only preview
GET  /api/projects/{id}/preview                  -> preview runtime status
```

Four properties are deliberate:

* **The existing ``GET /api/projects`` is untouched.** It still returns the
  workspace project from ``ForgeApiService.list_projects``; the lifecycle list is a
  different path (``/api/projects/lifecycle``), and it is registered *before*
  ``/api/projects/{project_id}`` so the literal segment can never be captured as an
  id by the parameterised route.
* **No absolute path and no file content** reaches a payload. A project publishes
  its repository-relative ``project_path``; an artifact publishes a
  workspace-relative path plus a repository-relative ``location``, a size, a
  SHA-256, its create/update change and its verification relevance. The one absolute
  value this module can return is the preview's own workspace root, and it is nested
  inside ``diagnostics``.
* **The entry point comes from the manifest, never from prose.** The detail payload
  and the dedicated route both call
  :func:`app.projects.entrypoint.detect_entry_point` over the current version's
  artifacts, so a model's sentence cannot become a launch decision.
* **Failures use the surface's existing error contract.** An unknown project is
  ``404``, a malformed body is ``400``, and an impossible lifecycle move (including
  starting a preview of an archived project) is ``409`` with the layer's own code.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.projects.entrypoint import (
    SERVE_KIND_STATIC,
    EntryPoint,
    detect_entry_point,
)
from app.projects.preview import (
    LOCAL_RUN_NOT_WIRED,
    NO_ENTRY_POINT,
    PreviewRegistry,
)
from app.projects.stage import project_stage

__all__ = ["register_project_routes"]


def _verification_command(project_path: str, version: Any) -> str:
    """How a person would repeat the writer's check, as a display string.

    The run's own verification is an in-process ``py_compile`` of every ``.py`` the
    writer touched (:mod:`app.agent_runtime.code_writer`), and it already happened
    before a version could say ``passed``. This command is the manual equivalent for
    the owner; nothing in this repository executes it, and an empty string means
    there is no Python file to compile.
    """

    files = tuple(getattr(version, "files", ()) or ())
    if not any(str(getattr(item, "path", "")).lower().endswith(".py") for item in files):
        return ""
    folder = str(project_path or "").rstrip("/")
    if not folder:
        return ""
    return f"python -m compileall -q {folder}"


def register_project_routes(
    app: Any,
    projects: Any,
    *,
    run_record_loader: Any = None,
    environment_facts: Any = None,
    preview_registry: Any = None,
) -> None:
    """Attach the lifecycle routes to ``app`` and publish the service on its state.

    The service is created once, at composition, so every request of one process
    shares one store and one view of the projects -- exactly as the discovery
    service is composed.

    ``run_record_loader`` (``run_id -> record | None``) and ``environment_facts``
    (``() -> {"provider_registry_ids": [...], "api_routes": [...]}``) are optional
    and read-only: they let the detail route publish the *technical* block of the
    card without the project store having to know about the run store or the
    composition. Both are called inside a request and neither can raise into the
    response.

    ``preview_registry`` is the process's one preview registry
    (:class:`app.projects.preview.PreviewRegistry`). When it is omitted a private one
    is created, so a caller that only wants the read routes does not have to supply
    it; the composition always supplies the shared instance.
    """

    from fastapi import Body, HTTPException

    from app.api.routes import body_error_exception, error_body
    from app.projects.lifecycle import IllegalTransition
    from app.projects.presentation import build_project_presentation
    from app.projects.service import InvalidChangeRequest, UnknownProjectVersion
    from app.projects.store import UnknownProject

    app.state.forge_projects = projects
    registry = preview_registry if preview_registry is not None else PreviewRegistry()
    app.state.forge_previews = registry

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

    # ------------------------------------------------------------------ entry point
    def _workspace_of(project: Any) -> Any:
        try:
            return projects.store.workspace_path(project.project_id)
        except Exception:  # noqa: BLE001 - an unreadable workspace is an unknown fact
            return None

    def _entry_of(project: Any) -> Optional[EntryPoint]:
        """The one entry point of the *current* version, from its manifest only."""

        current = project.current()
        if current is None or not current.files:
            return None
        try:
            return detect_entry_point(current.files, workspace=_workspace_of(project))
        except Exception:  # noqa: BLE001 - detection never raises into a response
            return None

    def _entry_payload(project: Any, entry: Optional[EntryPoint]) -> Optional[Dict[str, Any]]:
        if entry is None:
            return None
        return {
            **entry.to_dict(),
            # Repository-relative, composed here for the same reason an artifact's
            # location is: the client never joins two fields into a path.
            "location": f"{project.project_path}/{entry.path}",
        }

    def _preview_payload(project: Any, entry: Optional[EntryPoint]) -> Dict[str, Any]:
        """The preview block: runtime state plus the honest reason it is unavailable."""

        current = project.current()
        files = tuple(getattr(current, "files", ()) or ())
        running = registry.status(project.project_id)
        is_static = entry is not None and entry.serve_kind == SERVE_KIND_STATIC
        if entry is None:
            reason = NO_ENTRY_POINT
        elif not is_static:
            reason = LOCAL_RUN_NOT_WIRED
        else:
            reason = ""
        return {
            "available": bool(is_static),
            "running": bool(running.get("running", False)),
            "reason": reason,
            "url": str(running.get("url", "") or ""),
            "port": int(running.get("port", 0) or 0),
            "entry_path": str(entry.path) if entry is not None else "",
            "entry_kind": str(entry.kind) if entry is not None else "",
            "serve_kind": str(entry.serve_kind) if entry is not None else "",
            "run_command": str(entry.run_command) if entry is not None else "",
            "project_folder": str(project.project_path or ""),
            "files_created": len(files),
            # Never an absolute path outside ``diagnostics``.
            "diagnostics": (
                dict(running.get("diagnostics", {}) or {})
                if running.get("running", False)
                else {}
            ),
        }

    def _stage_of(project: Any) -> str:
        current = project.current()
        return project_stage(
            files_count=current.file_count if current is not None else 0,
            verification=current.verification if current is not None else "",
            acceptance=current.acceptance if current is not None else "",
            lifecycle_status=project.status.value,
            archived=bool(project.archived),
            preview_available=registry.is_running(project.project_id),
            preview_not_required=bool(
                getattr(current, "preview_not_required", False)
            ),
        )

    def _presentation(project: Any) -> Dict[str, Any]:
        """The owner-facing card, derived from this project's own facts.

        Everything the card says comes from the record, its current version's
        manifest and (when it can be read) the run's durable record. A fact that
        cannot be read is passed as unknown and the card reports it as unknown; it
        is never filled in from the project's status alone.
        """

        current = project.current()
        files = tuple(getattr(current, "files", ()) or ())
        run_id = str(getattr(current, "run_id", "") or "")
        run_record: Any = None
        if run_id and callable(run_record_loader):
            try:
                run_record = run_record_loader(run_id)
            except Exception:  # noqa: BLE001 - an unreadable record is an unknown fact
                run_record = None
        facts: Dict[str, Any] = {}
        if callable(environment_facts):
            try:
                facts = dict(environment_facts() or {})
            except Exception:  # noqa: BLE001 - the composition is not a response
                facts = {}
        workspace_exists: Optional[bool] = None
        try:
            workspace_exists = bool(
                projects.store.workspace_path(project.project_id).is_dir()
            )
        except Exception:  # noqa: BLE001 - an unreadable workspace is an unknown fact
            workspace_exists = None
        status_word = str(getattr(project.status, "value", project.status) or "")
        return build_project_presentation(
            project=project.to_dict(),
            versions=[item.summary() for item in reversed(project.versions)],
            files=[_artifact_payload(project, artifact) for artifact in files],
            run_record=(
                run_record if isinstance(run_record, dict) else None
            ),
            workspace_exists=workspace_exists,
            session_open=status_word == "NEEDS_CLARIFICATION",
            questions_open=status_word == "NEEDS_CLARIFICATION",
            provider_registry_ids=tuple(facts.get("provider_registry_ids") or ()),
            api_routes=tuple(facts.get("api_routes") or ()),
        ).to_dict()

    def _detail(project: Any) -> Dict[str, Any]:
        current = project.current()
        files = tuple(current.files) if current is not None else ()
        entry = _entry_of(project)
        return {
            "project": project.to_dict(),
            "versions": [item.summary() for item in reversed(project.versions)],
            "files": [_artifact_payload(project, artifact) for artifact in files],
            "presentation": _presentation(project),
            # Additive, derived facts the card's top block and preview block read.
            # They are computed here (not stored) so a stale record cannot advertise
            # a file, an entry point or a running server it does not have.
            "stage": _stage_of(project),
            "files_created": len(files),
            "entrypoint": _entry_payload(project, entry),
            "preview": _preview_payload(project, entry),
            "verification_command": _verification_command(project.project_path, current),
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

    # -------------------------------------------------------- entry point/preview
    @app.get("/api/projects/{project_id}/entrypoint")
    def project_entrypoint(project_id: str) -> Dict[str, Any]:
        """The one entry point the current manifest supports, or ``null``.

        Never guessed from the model's answer: the detection reads the recorded
        artifact list only (see :mod:`app.projects.entrypoint`).
        """

        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        entry = _entry_of(project)
        return {
            "project_id": project.project_id,
            "entrypoint": _entry_payload(project, entry),
            "reason": "" if entry is not None else NO_ENTRY_POINT,
        }

    @app.get("/api/projects/{project_id}/preview")
    def get_project_preview(project_id: str) -> Dict[str, Any]:
        """Whether a read-only preview is listening for this project, and where."""

        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        return {"project_id": project.project_id, **_preview_payload(project, _entry_of(project))}

    @app.post("/api/projects/{project_id}/preview")
    def change_project_preview(
        project_id: str,
        payload: Optional[Dict[str, Any]] = Body(default=None),
    ) -> Dict[str, Any]:
        """Start, stop, or read the read-only static preview (and record "not required").

        ``{"action": "start"|"stop"|"status"}`` (default ``status``, a read). ``"stop"``
        is always accepted, so a preview can be closed even if the record changed
        underneath it. ``{"preview_not_required": true|false}`` records the owner's
        explicit statement on the current version; it is **not** a substitute for a
        preview a project could have served, and by itself it starts nothing.
        """

        body = dict(payload or {})
        # The default is a read: recording ``preview_not_required`` must not start a
        # server as a side effect. Starting is always an explicit ``"start"``.
        action = body.get("action", "status")
        if not isinstance(action, str) or action not in ("start", "stop", "status"):
            raise body_error_exception("'action' must be 'start', 'stop' or 'status'")
        not_required = body.get("preview_not_required")
        if not_required is not None and not isinstance(not_required, bool):
            raise body_error_exception("'preview_not_required' must be a boolean")

        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        if not_required is not None:
            project = _guard(
                lambda: projects.set_preview_not_required(
                    project.project_id, not_required
                )
            )

        entry = _entry_of(project)
        if action == "stop":
            registry.stop(project.project_id)
            return {
                "project_id": project.project_id,
                "action": "stop",
                **_preview_payload(project, entry),
                "stage": _stage_of(project),
            }

        if action == "status":
            return {
                "project_id": project.project_id,
                "action": "status",
                **_preview_payload(project, entry),
                "stage": _stage_of(project),
            }

        if project.archived:
            raise _refusal(
                IllegalTransition(
                    "an archived project cannot be previewed: restore it first",
                    status=project.status,
                    event="preview",
                )
            )
        registry.start(project.project_id, _workspace_of(project), entry)
        return {
            "project_id": project.project_id,
            "action": "start",
            **_preview_payload(project, entry),
            "stage": _stage_of(project),
        }
