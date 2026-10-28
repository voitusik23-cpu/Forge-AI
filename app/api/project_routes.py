"""Read-mostly API additions for the persistent project lifecycle.

```
GET  /api/projects/lifecycle                     -> [project summary]
GET  /api/projects/{id}                          -> full record + versions + files
POST /api/projects/{id}/versions  {change}       -> new version + the task text
POST /api/projects/{id}/archive                  -> archived record
POST /api/projects/{id}/restore                  -> record back at its old status
GET  /api/projects/{id}/history                  -> versions, newest first
GET  /api/projects/{id}/files?version=           -> one version's artifact manifest
POST /api/projects/{id}/attachments {filename,data} -> keep one screenshot with the project
POST /api/projects/{id}/verify                   -> re-judged record
GET  /api/projects/{id}/entrypoint               -> the one detected entry point, or null
POST /api/projects/{id}/preview  {action}        -> start/stop the read-only preview
GET  /api/projects/{id}/preview                  -> preview runtime status
GET  /api/projects/{id}/run                      -> local process state, command, pid, log tail
POST /api/projects/{id}/run  {confirm, digest}   -> start it, only with an explicit approval
POST /api/projects/{id}/run/stop                 -> terminate the process tree
POST /api/projects/{id}/run/restart              -> stop, then start again under a fresh approval
GET  /api/projects/{id}/run/log                  -> the redacted, bounded log
POST /api/projects/{id}/open  {target}           -> really open folder / VS Code / terminal
POST /api/projects/{id}/launch  {confirm, entrypoint_digest, fallback}
                                                 -> start it and open the result in its own window
POST /api/projects/{id}/move-to-visible-folder   -> deliver the files to the owner's folder
DELETE|POST /api/projects/{id}                   -> move the project into projects-deleted
POST /api/projects/delete-empty                  -> the same move, for empty finished projects
GET  /api/projects/deleted                       -> what the trash holds now
POST /api/projects/{id}/restore-deleted          -> move the newest trashed copy back
```

Four properties are deliberate:

* **The existing ``GET /api/projects`` is untouched.** It still returns the
  workspace project from ``ForgeApiService.list_projects``; the lifecycle list is a
  different path (``/api/projects/lifecycle``), and it is registered *before*
  ``/api/projects/{project_id}`` so the literal segment can never be captured as an
  id by the parameterised route.
* **No absolute path and no file content** reaches a payload, with exactly one
  deliberate exception: the card's ``diagnostics`` block carries the absolute folder,
  so the owner can see and paste where his project really is. Everything above the
  fold publishes the record's own relative form.
* **The entry point comes from the manifest, never from prose.** The detail payload
  and the dedicated route both call
  :func:`app.projects.entrypoint.detect_entry_point` over the current version's
  artifacts, so a model's sentence cannot become a launch decision.
* **Failures use the surface's existing error contract.** An unknown project is
  ``404``, a malformed body is ``400``, and an impossible lifecycle move (including
  starting a preview of an archived project) is ``409`` with the layer's own code.
  A mutating call against an id that is in ``projects-deleted`` is
  ``409 project_deleted``: the record is not written and the trash is not touched.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, List, Mapping, Optional

# ``Request`` is imported here, at module level, for one reason: the upload route annotates
# its parameter with it, and with ``from __future__ import annotations`` that annotation is
# resolved against this module's globals. A type imported inside the route would leave
# FastAPI reading ``request`` as a required *query* parameter (422 on every upload).
# ``app/api/provider_key_routes.py`` imports it at module level for the same reason.
from fastapi import Request

from app.projects.attachments import (
    MAX_ATTACHMENTS_PER_CHANGE,
    MAX_ATTACHMENT_BYTES,
    AttachmentRefused,
    AttachmentTooLarge,
    existing_attachment,
    store_attachment,
    with_attachment_notices,
)
from app.projects.entrypoint import (
    SERVE_KIND_STATIC,
    EntryPoint,
    resolve_entry,
)
from app.projects.folders import count_files
from app.projects.launcher import LaunchRefusal, ProjectLauncher
from app.projects.opener import OPEN_TARGETS, OpenRefusal, ProjectOpener
from app.projects.ports import manifest_port_claim
from app.projects.preview import (
    LOCAL_RUN_NOT_WIRED,
    NO_ENTRY_POINT,
    PREVIEW_OWNED_BY_PROCESS,
    PreviewRegistry,
)
from app.execution.containment import sandbox_settings_from_environment
from app.projects.runner import LocalRunner, RunTarget
from app.projects.stage import project_stage
from app.reports.evidence import build_evidence
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
    runner: Any = None,
    opener: Any = None,
    window_opener: Any = None,
    window_policy: Any = None,
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

    ``runner`` is the process's one local runner
    (:class:`app.projects.runner.LocalRunner`). Like the registry it is created
    here when it is not supplied, so the routes and the tests share one composed
    object rather than two views of the same project.

    ``opener`` is the one OS-integration opener (:class:`app.projects.opener.ProjectOpener`).
    A test supplies one whose ``spawn`` records the argument vector, so the open buttons
    can be proved without a real Explorer window opening on the machine running the
    suite; the composition supplies the real one.

    ``window_opener`` is the OS window integration the launch route uses
    (:class:`app.projects.app_window.AppWindowOpener`). It is injected for the same
    reason as ``opener``: the launch is proved by the exact argument vector it would
    have started, without a browser window opening on the test machine. The launcher
    itself is always composed here, so it shares this process's one runner and one
    preview registry.

    ``window_policy`` is the owner's «Как открываются окна» switch: a zero-argument
    callable answering whether a browser whose own sandbox cannot start may be retried
    once without it. It is handed to the opener the launcher composes when none was
    injected; the composition reads it from the same settings store «Настройки» writes.
    """

    from fastapi import Body, HTTPException

    from app.api.routes import body_error_exception, error_body
    from app.projects.lifecycle import IllegalTransition
    from app.projects.models import ProjectError
    from app.projects.presentation import build_project_presentation
    from app.projects.service import InvalidChangeRequest, UnknownProjectVersion
    from app.projects.store import ProjectAlreadyExists, ProjectDeleted, UnknownProject

    app.state.forge_projects = projects
    registry = preview_registry if preview_registry is not None else PreviewRegistry()
    app.state.forge_previews = registry
    project_opener = opener if opener is not None else ProjectOpener()
    app.state.forge_opener = project_opener
    local_runner = (
        runner
        if runner is not None
        else LocalRunner(
            store=getattr(projects, "store", None),
            previews=registry,
            **sandbox_settings_from_environment(),
        )
    )
    app.state.forge_runner = local_runner
    # The launch is one request that shows the result: it resolves the entry point,
    # starts (or reuses) the project through the runner/registry composed above, waits
    # for the port the runner really observed, and opens the URL in its own window.
    project_launcher = ProjectLauncher(
        runner=local_runner,
        previews=registry,
        windows=window_opener,
        window_policy=window_policy,
    )
    app.state.forge_launcher = project_launcher

    def _refusal(exc: Exception) -> "HTTPException":
        if isinstance(exc, (UnknownProject, UnknownProjectVersion)):
            status = 404
        elif isinstance(exc, InvalidChangeRequest):
            status = 400
        elif isinstance(exc, (IllegalTransition, ProjectAlreadyExists, ProjectDeleted)):
            status = 409
        elif isinstance(exc, ProjectError):
            # A typed refusal of this layer that has no more specific status: still a
            # conflict with recorded state, never a 500 and never an untyped crash.
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

    def _manifest_port(project: Any) -> int:
        """The port the project's **manifest** claims, or ``0`` when it claims none.

        The claim lives in the record's own specification -- the text the owner asked
        for and the project was generated from («ЗАПУСК: ... открывает
        http://127.0.0.1:8765»). It is read here, at the route, because the record is
        the manifest; it is used for one thing only: the launch ``notice`` that names
        both numbers when the port reality chose is a different one. Nothing opens on
        the claim, and no URL is ever built from it.
        """

        specification = getattr(project, "specification", None)
        texts: List[str] = []
        if isinstance(specification, dict):
            goal = str(specification.get("goal", "") or "")
            if goal:
                texts.append(goal)
            for name, value in specification.items():
                if name == "goal":
                    continue
                if isinstance(value, str):
                    texts.append(value)
                elif isinstance(value, (list, tuple)):
                    texts.extend(str(item) for item in value if isinstance(item, str))
        return manifest_port_claim("\n".join(texts))

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

    def _folder_facts(project: Any) -> Dict[str, Any]:
        """What the card needs to offer a real entrance into the project.

        ``path`` is the record's own relative folder (what the owner reads), ``exists``
        says whether that folder is there *now*, and ``files`` is counted on disk rather
        than read from the record: a version can name files the owner has since deleted,
        and "open" must refuse in that case instead of opening an empty folder.
        """

        workspace = _workspace_of(project)
        exists = False
        files = 0
        if workspace is not None:
            try:
                exists = bool(workspace.is_dir())
            except OSError:  # pragma: no cover - defensive
                exists = False
            if exists:
                files = count_files(workspace)
        return {
            "path": str(project.project_path or ""),
            "exists": bool(exists),
            "files": int(files),
            "workspace": workspace,
        }

    def _open_block(project: Any, folder: Dict[str, Any]) -> Dict[str, Any]:
        """Per-target availability plus the two facts every refusal rests on."""

        try:
            targets = project_opener.availability(
                folder["workspace"] if folder["exists"] else None,
                files=int(folder["files"]),
            )
        except Exception:  # noqa: BLE001 - an unreadable opener is "unavailable"
            targets = {
                target: {
                    "target": target,
                    "available": False,
                    "reason": "Открытие недоступно в этой сборке.",
                }
                for target in OPEN_TARGETS
            }
        return {
            "targets": targets,
            "folder": folder["path"],
            "folder_exists": bool(folder["exists"]),
            "files": int(folder["files"]),
        }

    def _declared_entry(project: Any) -> object:
        """The entry the project's own specification declares, if it declares one.

        The specification is the owner's/first-run statement about the project; an
        explicit ``entry`` (or ``entrypoint``) there is the documented override that
        outranks the ranking in :mod:`app.projects.entrypoint`. It is passed through
        unchanged and validated there -- a value naming no real file is ignored.
        """

        specification = getattr(project, "specification", None)
        if not isinstance(specification, Mapping):
            return None
        for key in ("entry", "entrypoint"):
            value = specification.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _entry_resolution(project: Any) -> Any:
        """The one entry point of the *current* project state, recomputed per read.

        Returns :class:`app.projects.entrypoint.EntryResolution`, so the payload can
        say *why* there is no entry point instead of leaving the field empty.

        The entry point is **never stored**: it is derived here, from the current
        version's manifest *and* the files that are really at the project root. That
        is what stops a run that wrote only ``index.html`` from re-labelling a
        project that still has ``app.py`` as a static page -- the card shows the
        recomputed entry, never the old manifest's one.
        """

        current = project.current()
        if current is None or not current.files:
            return resolve_entry(())
        try:
            return resolve_entry(
                current.files,
                workspace=_workspace_of(project),
                declared_entry=_declared_entry(project),
            )
        except Exception:  # noqa: BLE001 - detection never raises into a response
            return resolve_entry(())

    def _entry_of(project: Any) -> Optional[EntryPoint]:
        return _entry_resolution(project).entry

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
        """The preview block: runtime state plus the honest reason it is unavailable.

        Three cases, and only three:

        * no entry point -- ``NO_ENTRY_POINT``;
        * a static page -- this module serves it read-only, ``available: true``;
        * a runnable process -- ``available`` only when *the process is up* and a
          port was observed, in which case the URL is the one the child bound. A
          project whose process is not running gets ``available: false`` and the
          sentence that says exactly that; no URL is ever invented.
        """

        current = project.current()
        files = tuple(getattr(current, "files", ()) or ())
        running = registry.status(project.project_id)
        is_static = entry is not None and entry.serve_kind == SERVE_KIND_STATIC
        process_preview = registry.process_session(project.project_id)
        if entry is None:
            reason = NO_ENTRY_POINT
            available = False
        elif is_static:
            reason = ""
            available = True
        elif process_preview is not None:
            reason = ""
            available = True
        else:
            reason = LOCAL_RUN_NOT_WIRED
            available = False
        return {
            "available": bool(available),
            "running": bool(running.get("running", False)),
            "source": str(running.get("source", "static" if is_static else "") or ""),
            "reason": reason,
            "url": str(running.get("url", "") or "") if available else "",
            "port": int(running.get("port", 0) or 0) if available else 0,
            "entry_path": str(entry.path) if entry is not None else "",
            "entry_kind": str(entry.kind) if entry is not None else "",
            "serve_kind": str(entry.serve_kind) if entry is not None else "",
            "run_command": str(entry.run_command) if entry is not None else "",
            "entrypoint_digest": str(entry.digest) if entry is not None else "",
            "runnable": bool(entry.runnable) if entry is not None else False,
            "project_folder": str(project.project_path or ""),
            "files_created": len(files),
            # Never an absolute path outside ``diagnostics``.
            "diagnostics": (
                dict(running.get("diagnostics", {}) or {})
                if running.get("running", False)
                else {}
            ),
        }

    def _run_target(project: Any) -> RunTarget:
        return RunTarget(
            project_id=project.project_id,
            workspace=_workspace_of(project),
            entry=_entry_of(project),
            project_path=str(project.project_path or ""),
        )

    def _stage_of(project: Any) -> str:
        current = project.current()
        entry = _entry_of(project)
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
            run_state=local_runner.state(project.project_id),
            runnable=bool(entry is not None and entry.runnable),
        )

    def _presentation(project: Any, *, folder: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
        entry = _entry_of(project)
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
            folder_absolute=(
                str(folder.get("workspace") or "") if folder is not None else ""
            ),
            # The **recomputed** entry point, so the card's launch action names the
            # program it will really start (not the page the old manifest named).
            entry_kind=str(entry.kind) if entry is not None else "",
            entry_path=str(entry.path) if entry is not None else "",
        ).to_dict()

    def _evidence_of(project: Any) -> Dict[str, Any]:
        """What is proven, assumed and unknown about the **current** version.

        The facts are the version's own records: its files (path, size and the
        SHA-256 written when the run recorded them), its verification word with the
        command a person would repeat, its acceptance verdict and its number. The
        model's text is *not* stored on a version, so ``assumed`` is empty here --
        which is the honest answer, not a gap to fill with a guess.

        A URL is deliberately not passed as a served fact: the runner proves that a
        ``GET /`` answered when it published a URL, but it does not keep the status
        code, and evidence must name (URL + status) or say it has none. The block
        therefore reports the live run as unknown instead of dressing a remembered
        address as a fresh answer.
        """

        current = project.current()
        latest = project.latest_version
        # A rolled-back change is never the current version (the previous one stays
        # current), so its verdict is read from the newest attempt -- that is the
        # version the card's «изменения откатаны» sentence is about.
        recorded_rollback = (
            dict(getattr(latest, "rollback") or {}) if latest is not None else {}
        )
        return build_evidence(
            outcome=_stage_of(project),
            files=tuple(getattr(current, "files", ()) or ()) if current is not None else (),
            verification={
                "status": (
                    str(getattr(current, "verification", "") or "")
                    if current is not None
                    else ""
                ),
                "command": (
                    _verification_command(project.project_path, current)
                    if current is not None
                    else ""
                ),
            },
            acceptance=(
                str(getattr(current, "acceptance", "") or "")
                if current is not None
                else ""
            ),
            model_summary="",
            answer_excerpt="",
            run={
                "run_id": (
                    str(getattr(current, "run_id", "") or "")
                    if current is not None
                    else ""
                ),
                "url": "",
                "http_status": 0,
            },
            project={
                "version": (
                    int(getattr(current, "version", 0) or 0)
                    if current is not None
                    else 0
                ),
                "status": str(getattr(project.status, "value", project.status) or ""),
            },
            # THE ROLLBACK OF THE VERSION THE CARD SHOWS. A change that was put back
            # keeps its proof on its own version, so the block prints the restored
            # files with their digests instead of pretending the change never existed.
            rollback=recorded_rollback or None,
        )

    def _detail(project: Any) -> Dict[str, Any]:
        current = project.current()
        files = tuple(current.files) if current is not None else ()
        entry = _entry_of(project)
        folder = _folder_facts(project)
        preview = _preview_payload(project, entry)
        run = local_runner.status(_run_target(project))
        return {
            "project": project.to_dict(),
            "versions": [item.summary() for item in reversed(project.versions)],
            "files": [_artifact_payload(project, artifact) for artifact in files],
            "presentation": _presentation(project, folder=folder),
            # Additive, derived facts the card's top block and preview block read.
            # They are computed here (not stored) so a stale record cannot advertise
            # a file, an entry point or a running server it does not have.
            "stage": _stage_of(project),
            "files_created": len(files),
            "entrypoint": _entry_payload(project, entry),
            "entrypoint_reason": _entry_resolution(project).reason,
            "preview": preview,
            "run": run,
            "verification_command": _verification_command(project.project_path, current),
            # ------------------------------------------------ the last change's verdict
            # Published beside the stage so the card can say «Изменения откатаны» in
            # words instead of inferring it: the files of the change are gone, and the
            # previous version is current again. ``rolled_back`` is true only for a
            # restore that completed and was proved by digest.
            "rollback": (
                dict(getattr(project.latest_version, "rollback") or {})
                if getattr(project.latest_version, "rollback", None)
                else None
            ),
            "rolled_back": bool(
                getattr(project.latest_version, "rolled_back", False)
            ),
            # ------------------------------------------------ what is proven, what is not
            # The evidence block for the current version: file digests, the verification
            # command and the acceptance verdict as `verified`; the model's words (none
            # are stored on a version) as `assumed`; every gap as `unknown`. It is
            # derived per read, exactly like `stage` and `preview`, so a stale record
            # cannot make a deleted file or an old verdict look like a fresh proof.
            "evidence": _evidence_of(project),
            # ------------------------------------------------ the entrance into the project
            # ``folder`` is what the card prints beside its open buttons; ``open`` says,
            # per target, whether anything can really be started and why not. Both are
            # derived from the folder on disk, never from the request.
            "folder": {
                "path": folder["path"],
                "exists": bool(folder["exists"]),
                "files": int(folder["files"]),
            },
            "open": _open_block(project, folder),
            # The one place this surface publishes an absolute path on purpose: the
            # owner needs to be able to see and paste where his project really is. It is
            # nested under ``diagnostics``, which the card renders collapsed.
            "diagnostics": {
                "folder_absolute": (
                    str(folder["workspace"]) if folder["workspace"] is not None else ""
                ),
                "project_root": _project_root_text(projects),
            },
        }

    def _project_root_text(owner: Any) -> str:
        """The configured project root, as text, or "" when none is configured."""

        try:
            root = owner.store.visible_root()
        except Exception:  # noqa: BLE001 - an unreadable root is "not configured"
            return ""
        return str(root) if root is not None else ""

    def _guard(call: Any) -> Any:
        try:
            return call()
        except (
            UnknownProject,
            UnknownProjectVersion,
            InvalidChangeRequest,
            IllegalTransition,
            ProjectAlreadyExists,
            ProjectDeleted,
            ProjectError,
        ) as exc:
            raise _refusal(exc) from exc

    # ------------------------------------------------------- screenshots of a problem
    #
    # «Как сказать Forge, что приложение сломалось?» The owner has a screenshot, not a
    # sentence. It is kept with the project and named in the task text -- but it is NOT
    # model input, and the sentence the model reads says so
    # (:data:`app.projects.attachments.ATTACHMENT_NOTICE`).

    #: Where an upload may come from. The surface binds loopback and refuses any other
    #: bind at composition (``enforce_loopback_bind``), so this check is the route-level
    #: half of the same promise; ``testclient`` is Starlette's in-process transport, which
    #: is this same process and never a network peer.
    _UPLOAD_CLIENTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})

    def _require_local_uploader(request: Any) -> None:
        """Refuse an upload that did not arrive over this machine's loopback transport."""

        client = getattr(request, "client", None)
        host = str(getattr(client, "host", "") or "").strip().lower()
        if host in _UPLOAD_CLIENTS:
            return
        code = "attachment_not_local"
        raise HTTPException(
            status_code=403,
            detail={
                **error_body(error_code=code, code=code),
                "error": "Скриншоты принимаются только с этого компьютера.",
            },
        )

    def _attachment_refusal(exc: Exception, status: int) -> "HTTPException":
        """A refused upload with the status that names the real reason.

        ``400`` is a body that is not an image or a reference that is not this project's
        file; ``413`` is the size limit. Never ``500``: every one of these is the caller's
        input, and the sentence is the layer's own.
        """

        code = str(getattr(exc, "code", "attachment_refused"))
        return HTTPException(
            status_code=status,
            detail={**error_body(error_code=code, code=code), "error": str(exc)},
        )

    @app.post("/api/projects/{project_id}/attachments")
    def create_project_attachment(
        project_id: str,
        payload: Dict[str, Any],
        request: Request,
    ) -> Dict[str, Any]:
        """Keep one screenshot with this project and answer with its published path.

        JSON with base64 ``data`` rather than multipart: this surface has one body
        contract, one error envelope and no multipart dependency, and a screenshot is a
        single small blob. What the answer names is ``path`` -- the project-relative
        location (``.forge/projects/.../.forge/attachments/<timestamp>-<name>``), never an
        absolute host path -- plus ``bytes`` and the ``kind`` the bytes really are.
        """

        _require_local_uploader(request)
        body = dict(payload or {})
        data_base64 = body.get("data")
        if not isinstance(data_base64, str) or not data_base64.strip():
            raise body_error_exception("'data' must be base64-encoded image bytes")
        # A base64 string longer than 4/3 of the limit cannot decode into anything
        # smaller, so it is refused before the bytes are allocated.
        if len(data_base64) > ((MAX_ATTACHMENT_BYTES * 4) // 3) + 8:
            raise _attachment_refusal(
                AttachmentTooLarge(
                    "Скриншот больше 5 МБ: уменьшите его и приложите снова."
                ),
                413,
            )
        try:
            blob = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise body_error_exception(
                "'data' must be base64-encoded image bytes"
            ) from exc

        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        workspace = _workspace_of(project)
        try:
            stored = store_attachment(workspace, blob, body.get("filename"))
        except AttachmentTooLarge as exc:
            raise _attachment_refusal(exc, 413) from exc
        except AttachmentRefused as exc:
            raise _attachment_refusal(exc, 400) from exc
        return {
            # ONE composed location, the same convention as an artifact's ``location``:
            # the client never joins two fields and cannot build an absolute path. It is
            # repository-relative for a project whose files live inside the repository,
            # and relative to the owner's configured project root for one delivered
            # outside it -- never an absolute host path either way.
            "path": f"{project.project_path}/{stored['relative']}",
            "bytes": stored["bytes"],
            "kind": stored["kind"],
            # The owner's own name, sanitised -- what the chip shows him back.
            "name": stored["name"],
        }

    @app.post("/api/projects/{project_id}/versions")
    def create_project_version(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        change = payload.get("change")
        if not isinstance(change, str) or not change.strip():
            raise body_error_exception("'change' must be a non-empty string")
        attachments = payload.get("attachments")
        if attachments is not None:
            if not isinstance(attachments, list) or any(
                not isinstance(item, str) for item in attachments
            ):
                raise body_error_exception(
                    "'attachments' must be a list of screenshot paths"
                )
            if len(attachments) > MAX_ATTACHMENTS_PER_CHANGE:
                raise body_error_exception(
                    "'attachments' accepts at most "
                    f"{MAX_ATTACHMENTS_PER_CHANGE} screenshots"
                )
        # THE REFERENCES ARE RE-DERIVED, NEVER TRUSTED, and that happens BEFORE the
        # version is staged: a path outside this project's own attachments folder cannot
        # create a version and then fail.
        kept: List[str] = []
        project: Any = None
        if attachments:
            project = _guard(lambda: projects.open_project(_project_id(project_id)))
            workspace = _workspace_of(project)
            for item in attachments:
                try:
                    kept.append(
                        existing_attachment(workspace, project.project_path, item)
                    )
                except AttachmentRefused as exc:
                    raise _attachment_refusal(exc, 400) from exc
        result = _guard(
            lambda: projects.new_version(_project_id(project_id), change)
        )
        # THE HONEST REFERENCE. The staged version keeps the owner's own words (so the
        # same sentence is still the same change); only the text the run executes -- the
        # text the model reads -- names the screenshot and says it cannot be seen.
        task_text = result.task_text
        if kept and task_text:
            task_text = with_attachment_notices(task_text, kept)
        return {
            "project": result.project.to_dict(),
            "version": result.version,
            "task_text": task_text,
            # The screenshots this change names, project-relative. Additive: a caller that
            # sends none gets the list it always got (an empty one).
            "attachments": kept,
            "change_request": result.change_request.to_dict(),
            # THE SAME WORDS ARE ONE CHANGE. ``already_applied`` means the last version
            # recorded for this exact text is current and holds files, so ``task_text``
            # is empty and no run may be dispatched; ``retargeted`` means the retry
            # writes into the same version number instead of appending another empty
            # one. Both are published so a client can say what happened rather than
            # pressing again.
            "already_applied": bool(result.already_applied),
            "retargeted": bool(result.retargeted),
            "previous_run_id": result.previous_run_id,
        }

    # Registered BEFORE the ``{project_id}`` route below: FastAPI matches in
    # registration order, so a literal path must come first or it would be read as
    # an id.
    @app.get("/api/projects/lifecycle")
    def list_project_lifecycle(archived: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Every project, newest change first, as one bounded summary line each.

        Each row carries one additive fact, ``in_visible_root``: whether the project's
        files really live under a configured **visible** project root (a folder the owner
        can open), as opposed to the legacy hidden workspace that every test and scratch
        script leaves behind. The list itself is not filtered here -- the whole listing
        stays the whole listing, so a client can count what it hides and offer to show
        it -- and the flag is derived per request from the record and the deployment's
        own roots, never from a request value.
        """

        include_archived = True if archived is None else bool(archived)
        return [dict(item) for item in projects.summaries(include_archived=include_archived)]

    @app.get("/api/projects/deleted")
    def list_deleted_projects(
        limit: Optional[int] = None, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """What is in ``projects-deleted`` right now: names and what each holds.

        Registered before ``/api/projects/{project_id}`` for the same reason
        ``/api/projects/lifecycle`` is: a literal segment must not be captured as an id.

        ``limit``/``offset`` are **additive**: omitted (both defaults) the answer is the
        whole trash, one flat list of the same rows in the same newest-first order it
        has always been -- no caller that exists today has to change. Given, they window
        that ordering, so a client can page through a trash of dozens of entries without
        rendering them all. A non-positive ``limit`` or a negative ``offset`` is refused
        with the surface's own 400 instead of being silently reinterpreted.
        """

        if limit is not None and limit <= 0:
            raise body_error_exception("'limit' must be a positive integer when given")
        if offset < 0:
            raise body_error_exception("'offset' must be zero or a positive integer")
        return [
            dict(item)
            for item in projects.deleted_projects(limit=limit, offset=offset)
        ]

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> Dict[str, Any]:
        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        return _detail(project)

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

    # ---------------------------------------------------------------- the entrance
    #
    # «Из Forge должен быть вход в проект». Three integrations exist and nothing else:
    # the folder, VS Code, a terminal. The request names one of those three words; the
    # folder is resolved here, from the record, and is never accepted from the body. A
    # target that cannot be performed answers 409 with the opener's own sentence and
    # starts nothing, so a disabled button on the card and a refused request say the
    # same thing.
    @app.post("/api/projects/{project_id}/open")
    def open_project(
        project_id: str,
        payload: Optional[Dict[str, Any]] = Body(default=None),
    ) -> Dict[str, Any]:
        """Really open the project: its folder, VS Code, or a terminal at it."""

        body = dict(payload or {})
        target = body.get("target")
        if not isinstance(target, str) or target not in OPEN_TARGETS:
            raise body_error_exception(
                "'target' must be one of: " + ", ".join(OPEN_TARGETS)
            )
        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        folder = _folder_facts(project)
        try:
            result = project_opener.open(
                target,
                folder["workspace"] if folder["exists"] else None,
                files=int(folder["files"]),
            )
        except OpenRefusal as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    **error_body(error_code=exc.code, code=exc.code),
                    "error": str(exc),
                    "target": target,
                    "project_id": project.project_id,
                    "folder": str(project.project_path or ""),
                },
            ) from exc
        return {
            "project_id": project.project_id,
            "folder": str(project.project_path or ""),
            **result.to_dict(),
        }

    @app.post("/api/projects/{project_id}/launch")
    def launch_project(
        project_id: str,
        payload: Optional[Dict[str, Any]] = Body(default=None),
    ) -> Dict[str, Any]:
        """Start the project and open the **result** in its own window.

        This is the owner's «Запустить приложение»: one click that delivers the
        running project on the desktop instead of another Forge screen or a URL to
        copy. The entry point comes from the manifest (via
        :mod:`app.projects.entrypoint`), a process is started only through the
        runner's existing approval chain -- ``{"confirm": true}`` plus the
        ``entrypoint_digest`` the owner was shown -- and a static page is served by
        the existing read-only preview. The window is
        :class:`app.projects.app_window.AppWindowOpener`'s: Chrome in app mode,
        then Edge, then the default browser.

        A running server is **reused** (``reused: true``) and the window is simply
        opened again, so a second click is the same request. Every impossibility is
        a typed ``409`` with one plain sentence (no files, no entry point, no
        approval, the process died without a port, no browser): a launch is never a
        silent no-op.

        The port is **discovered, never assumed**: the supervisor reads the address the
        child's own output announced and the sockets its pid tree owns, and every
        candidate must answer a real ``GET /`` (:mod:`app.projects.ports`). The answer
        carries ``observed_port``. A child that exits 0 while an address it printed
        already answers is a success with ``reused: true`` -- that is «the application
        is running» even though Forge started nothing new. When the manifest's own
        claimed port differs from the observed one, ``notice`` names both («Порт из
        манифеста 8765, фактический 8790»): the manifest is a claim, reality wins.

        A project that did not become a server is **not** replaced by its static page
        behind the owner's back: the refusal carries ``fallback_available`` and a real
        ``fallback_url`` when the folder holds a page, and only a second request with
        ``{"fallback": true}`` serves that page. No browser is opened when no port was
        observed.
        """

        body = dict(payload or {})
        confirm = body.get("confirm", False)
        if not isinstance(confirm, bool):
            raise body_error_exception("'confirm' must be a boolean")
        digest = body.get("entrypoint_digest")
        if digest is not None and not isinstance(digest, str):
            raise body_error_exception("'entrypoint_digest' must be a string")
        # The explicit request for the project's static page. It is a boolean, and it is
        # the *only* way the page can be shown for a process that never bound a port.
        static_fallback = body.get("fallback", False)
        if not isinstance(static_fallback, bool):
            raise body_error_exception("'fallback' must be a boolean")
        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        if project.archived:
            raise _refusal(
                IllegalTransition(
                    "an archived project cannot be launched: restore it first",
                    status=project.status,
                    event="launch",
                )
            )
        folder = _folder_facts(project)
        try:
            result = project_launcher.launch(
                project_id=project.project_id,
                target=_run_target(project),
                entry=_entry_of(project),
                files=int(folder["files"]),
                confirm=confirm,
                digest=digest,
                archived=bool(project.archived),
                static_fallback=static_fallback,
                manifest_port=_manifest_port(project),
            )
        except LaunchRefusal as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    **error_body(error_code=exc.code, code=exc.code),
                    "error": str(exc),
                    **dict(exc.facts),
                },
            ) from exc
        return {
            **result.to_dict(),
            "stage": _stage_of(project),
        }

    @app.post("/api/projects/{project_id}/move-to-visible-folder")
    def move_project_to_visible_folder(project_id: str) -> Dict[str, Any]:
        """Deliver an existing project's files into the owner's visible folder.

        The record keeps its versions, manifest and run links; only the files move. The
        answer names what moved and where, and the absolute folder stays inside
        ``diagnostics`` like every other absolute path on this surface.
        """

        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        if _has_live_process(project.project_id):
            # A running child holds the folder open; moving it out from under the process
            # is how a launched project ends up writing into a directory nobody can find.
            raise _running_refusal(project.project_id)
        result = _guard(
            lambda: projects.move_to_visible_folder(project.project_id)
        )
        moved = list(result.get("moved") or ())
        updated = result.get("project")
        workspace = None
        try:
            workspace = projects.store.workspace_path(project.project_id)
        except Exception:  # noqa: BLE001 - an unreadable workspace is an unknown fact
            workspace = None
        return {
            "project_id": project.project_id,
            "already_visible": bool(result.get("already_visible", False)),
            "folder": str(result.get("folder") or ""),
            "moved": moved,
            "moved_count": len(moved),
            "project": updated.to_dict() if updated is not None else project.to_dict(),
            "diagnostics": {
                "folder_absolute": str(workspace) if workspace is not None else "",
                "project_root": _project_root_text(projects),
            },
        }

    # --------------------------------------------------------------- deletion
    #
    # A delete never erases: the store moves the project's folder and its record into
    # ``projects-deleted`` (see ``app.projects.store.ProjectStore.delete``), removes the
    # entry from the listing, and answers with what was moved. The two conditions this
    # layer can prove and the store cannot are enforced here:
    #
    # * a project with a **live process** is refused -- the caller is told to stop it
    #   first, because moving a folder out from under a running child is how a launched
    #   process ends up writing into the trash;
    # * a **bulk** cleanup only touches projects that are empty *and* in a terminal
    #   status, and it reports the ones it skipped with the reason.

    def _process_state(project_id: str) -> str:
        try:
            return str(local_runner.state(project_id) or "")
        except Exception:  # noqa: BLE001 - an unreadable runner is "no live process"
            return ""

    def _has_live_process(project_id: str) -> bool:
        if _process_state(project_id) in ("STARTING", "RUNNING", "STOPPING"):
            return True
        try:
            return registry.process_session(project_id) is not None
        except Exception:  # noqa: BLE001 - an unreadable registry is "no live process"
            return False

    def _running_refusal(project_id: str) -> "HTTPException":
        error = (
            "the project has a running process: stop it first "
            f"(POST /api/projects/{project_id}/run/stop), then delete"
        )
        return HTTPException(
            status_code=409,
            detail={
                **error_body(error_code="project_is_running", code="project_is_running"),
                "error": error,
                "project_id": project_id,
                "run_state": _process_state(project_id),
            },
        )

    def _delete_one(project_id: str) -> Dict[str, Any]:
        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        if _has_live_process(project.project_id):
            raise _running_refusal(project.project_id)
        # A static preview holds no child process but does hold a port serving the
        # folder that is about to move; stopping it keeps the answer honest.
        try:
            registry.stop(project.project_id)
        except Exception:  # noqa: BLE001 - a preview that is already gone is not a failure
            pass
        return _guard(lambda: projects.delete(project.project_id))

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str) -> Dict[str, Any]:
        """Move one project into ``projects-deleted``; never a hard delete."""

        return _delete_one(project_id)

    @app.post("/api/projects/{project_id}/delete")
    def delete_project_post(project_id: str) -> Dict[str, Any]:
        """The same move, for a client that only sends POST."""

        return _delete_one(project_id)

    @app.post("/api/projects/delete-empty")
    def delete_empty_projects() -> Dict[str, Any]:
        """Remove every project that is empty **and** finished.

        The rule is the store's record, not the client's opinion: the current version
        holds no file and the status is ``NEEDS_CHANGES``, ``FAILED`` or ``ARCHIVED``.
        A project with a live process is skipped and reported, never stopped
        implicitly.
        """

        deleted: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for candidate in projects.empty_cleanup_candidates():
            if _has_live_process(candidate.project_id):
                skipped.append(
                    {
                        "project_id": candidate.project_id,
                        "status": candidate.status.value,
                        "reason": "project_is_running",
                    }
                )
                continue
            try:
                deleted.append(projects.delete(candidate.project_id))
            except (UnknownProject, ValueError) as exc:
                skipped.append(
                    {
                        "project_id": candidate.project_id,
                        "status": candidate.status.value,
                        "reason": str(exc),
                    }
                )
        return {
            "deleted": deleted,
            "deleted_count": len(deleted),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "rule": "file_count == 0 and status in {NEEDS_CHANGES, FAILED, ARCHIVED}",
        }

    @app.post("/api/projects/{project_id}/restore-deleted")
    def restore_deleted_project(project_id: str) -> Dict[str, Any]:
        """Move the newest trashed copy of a project back into the store."""

        return _guard(lambda: projects.restore_deleted(project_id))

    # -------------------------------------------------------- entry point/preview
    @app.get("/api/projects/{project_id}/entrypoint")
    def project_entrypoint(project_id: str) -> Dict[str, Any]:
        """The one entry point the current manifest supports, or ``null``.

        Never guessed from the model's answer: the detection reads the recorded
        artifact list only (see :mod:`app.projects.entrypoint`). ``reason`` says why
        there is none, and ``blocked`` says that a recognised shape cannot be
        launched here (a missing interpreter).
        """

        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        resolution = _entry_resolution(project)
        return {
            "project_id": project.project_id,
            "entrypoint": _entry_payload(project, resolution.entry),
            "reason": resolution.reason if resolution.entry is None else resolution.entry.reason,
            "blocked": resolution.blocked,
        }

    @app.get("/api/projects/{project_id}/preview")
    def get_project_preview(project_id: str) -> Dict[str, Any]:
        """Whether a preview is listening for this project, and where."""

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
        underneath it -- except when the preview is served by the project's **own
        process**: that URL cannot be closed without stopping the process, so the
        answer says so instead of leaving a dead URL behind. ``{"preview_not_required":
        true|false}`` records the owner's explicit statement on the current version;
        it is **not** a substitute for a preview a project could have served, and by
        itself it starts nothing.
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
            process_preview = registry.process_session(project.project_id)
            if process_preview is not None:
                return {
                    "project_id": project.project_id,
                    "action": "stop",
                    "stopped": False,
                    "reason": PREVIEW_OWNED_BY_PROCESS,
                    **_preview_payload(project, entry),
                    "stage": _stage_of(project),
                }
            registry.stop(project.project_id)
            return {
                "project_id": project.project_id,
                "action": "stop",
                "stopped": True,
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

    # ------------------------------------------------------------- local run
    def _run_body(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        body = dict(payload or {})
        digest = body.get("entrypoint_digest")
        if digest is not None and not isinstance(digest, str):
            raise body_error_exception("'entrypoint_digest' must be a string")
        confirm = body.get("confirm", False)
        if not isinstance(confirm, bool):
            raise body_error_exception("'confirm' must be a boolean")
        offline = body.get("offline", False)
        if not isinstance(offline, bool):
            raise body_error_exception("'offline' must be a boolean")
        return body

    def _run_response(project: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        """One shape for every run answer: the supervisor's facts plus the stage."""

        return {
            "project_id": project.project_id,
            **payload,
            "stage": _stage_of(project),
        }

    @app.get("/api/projects/{project_id}/run")
    def get_project_run(project_id: str) -> Dict[str, Any]:
        """The project's local process: state, command, pid, exit code, log tail."""

        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        return _run_response(project, local_runner.status(_run_target(project)))

    @app.post("/api/projects/{project_id}/run")
    def start_project_run(
        project_id: str,
        payload: Optional[Dict[str, Any]] = Body(default=None),
    ) -> Dict[str, Any]:
        """Start the declared command, only with ``{"confirm": true}`` + the digest.

        The command itself is never in the body: it comes from the manifest and the
        files on disk, resolved server-side. The body can only say *yes, start that
        exact command*, and a command that changed makes the approval stale.
        """

        body = _run_body(payload)
        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        if project.archived:
            raise _refusal(
                IllegalTransition(
                    "an archived project cannot be run: restore it first",
                    status=project.status,
                    event="run",
                )
            )
        result = local_runner.start_confirmed(
            _run_target(project),
            confirm=body.get("confirm", False),
            digest=body.get("entrypoint_digest"),
            offline=body.get("offline", False),
        )
        return _run_response(project, result)

    @app.post("/api/projects/{project_id}/run/stop")
    def stop_project_run(project_id: str) -> Dict[str, Any]:
        """Terminate the process tree. Always accepted: stopping is the safe move."""

        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        return _run_response(project, local_runner.stop(project.project_id))

    @app.post("/api/projects/{project_id}/run/restart")
    def restart_project_run(
        project_id: str,
        payload: Optional[Dict[str, Any]] = Body(default=None),
    ) -> Dict[str, Any]:
        """Stop any current process, then start the same command under a fresh approval."""

        body = _run_body(payload)
        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        if project.archived:
            raise _refusal(
                IllegalTransition(
                    "an archived project cannot be run: restore it first",
                    status=project.status,
                    event="run",
                )
            )
        result = local_runner.restart_confirmed(
            _run_target(project),
            confirm=body.get("confirm", False),
            digest=body.get("entrypoint_digest"),
            offline=body.get("offline", False),
        )
        return _run_response(project, result)

    @app.get("/api/projects/{project_id}/run/log")
    def project_run_log(project_id: str) -> Dict[str, Any]:
        """The redacted, bounded log of the last local run."""

        project = _guard(lambda: projects.open_project(_project_id(project_id)))
        return local_runner.log(_run_target(project))
