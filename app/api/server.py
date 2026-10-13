"""FastAPI / HTTP server construction for Forge API.

Trust boundary, stated plainly because the code cannot enforce it yet
--------------------------------------------------------------------
**This surface has no authentication.** No route requires a credential, and no route
verifies an identity. It exists for a local, trusted operator: the desktop client and the
CLI talk to it in-process or over loopback. The Platform's own authentication work
(``app/platform/principal.py`` and the Stage 1 contract) covers a *different* surface and
does not protect these routes.

What this module therefore does, and refuses to do:

* binds to **loopback by default** and refuses non-loopback binds because the surface has
  no authentication;
* refuses a wildcard CORS origin, and never enables credentialed CORS, because
  ``allow_origins=["*"]`` together with ``allow_credentials=True`` is the configuration
  that turns a browser into a confused deputy for whoever can reach the port;
* does **not** invent authentication. Adding an identity mechanism here would be a new
  architecture decision (open decision O-2) and would create a second trust model
  alongside the Platform's. The residual risk is recorded in
  ``docs/SOURCE_OF_TRUTH.md`` and in the audit report rather than papered over.

Reading the CORS setting
------------------------
``FORGE_API_CORS_ORIGINS`` is an optional comma-separated allow-list. A ``*`` entry is
rejected rather than normalized, because silently narrowing a wildcard would leave the
operator believing a wider policy is active.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.runtime.run_store import is_safe_run_id

logger = logging.getLogger("forge.api")

__all__ = [
    "DEFAULT_API_HOST",
    "DEFAULT_API_PORT",
    "DEFAULT_CORS_ORIGINS",
    "CORS_ORIGINS_ENV_VAR",
    "UI_ASSETS_DIRECTORY",
    "resolve_cors_origins",
    "is_loopback_host",
    "warn_if_exposed",
    "resolve_ui_assets",
    "create_api_app",
]


#: Loopback by default. A caller that needs a wider binding must say so explicitly.
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000

#: The desktop client and a local browser page only. No wildcard, ever.
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1",
    "http://localhost",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
)

CORS_ORIGINS_ENV_VAR = "FORGE_API_CORS_ORIGINS"

#: Where the dependency-free operating console lives. It is three plain files --
#: one HTML document, one stylesheet, one script -- and no bundler, framework, or
#: package manager is involved, so a checkout runs the UI as-is.
UI_ASSETS_DIRECTORY = Path(__file__).resolve().parent.parent / "static"

#: The only three files the UI route will ever serve. An explicit allow-list (not
#: a directory mount) is what keeps a served page from becoming a file read.
_UI_ASSET_FILES = ("index.html", "app.css", "app.js")

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "ip6-localhost"})


def resolve_ui_assets(directory: Optional[Path] = None) -> dict[str, Path]:
    """Return the absolute paths of the UI files that exist, by asset name.

    Missing files are simply absent from the mapping, so the UI routes report 404
    rather than serving an empty document. Nothing here reads file contents.
    """

    root = Path(directory) if directory is not None else UI_ASSETS_DIRECTORY
    assets: dict[str, Path] = {}
    for name in _UI_ASSET_FILES:
        candidate = root / name
        try:
            if candidate.is_file():
                assets[name] = candidate.resolve()
        except OSError:  # pragma: no cover - an unreadable asset is simply absent
            continue
    return assets


def is_loopback_host(host: str) -> bool:
    """True when ``host`` names this machine only."""

    return str(host).strip().lower() in _LOOPBACK_HOSTS


def resolve_cors_origins(
    raw: Optional[str] = None, *, environ: Optional[dict[str, str]] = None
) -> tuple[str, ...]:
    """Return the CORS allow-list, refusing a wildcard instead of quietly narrowing it.

    Raises ``ValueError`` for a wildcard entry: an operator who wrote ``*`` believes the
    surface is open, and silently replacing it with a narrow list would leave that belief
    in place while the behaviour changed.
    """

    source = raw
    if source is None:
        environment = os.environ if environ is None else environ
        source = environment.get(CORS_ORIGINS_ENV_VAR)

    if source is None or not str(source).strip():
        return DEFAULT_CORS_ORIGINS

    origins: list[str] = []
    for entry in str(source).split(","):
        origin = entry.strip()
        if not origin:
            continue
        if origin == "*":
            raise ValueError(
                f"{CORS_ORIGINS_ENV_VAR} must not contain a wildcard origin: this "
                "surface has no authentication, so a wildcard would let any page that "
                "can reach the port drive it. List explicit origins."
            )
        origins.append(origin)
    if not origins:
        return DEFAULT_CORS_ORIGINS
    return tuple(origins)


def warn_if_exposed(host: str) -> Optional[str]:
    """Reject non-loopback binds because this surface has no authentication."""

    if is_loopback_host(host):
        return None
    raise RuntimeError(
        "Forge API refuses non-loopback binding because authentication is not configured"
    )


def create_api_app(
    service: Optional[ForgeApiService] = None,
    *,
    cors_origins: Optional[tuple[str, ...]] = None,
    ui_assets_directory: Optional[Path] = None,
) -> Any:
    """Create a FastAPI application exposing Forge API endpoints.

    Two surfaces live here. The original ten routes are unchanged thin wrappers
    over ``ForgeApiService``. The Block #8 operating routes
    (``app/api/routes.py``) expose the operating surface the console consumes: the
    accept cycle, declared verification, the step graph, recovery, Project Memory,
    and Change Impact. Both delegate to the same service instance, so there is one
    workspace, one run store, and one authority chain.

    ``ui_assets_directory`` exists so a test can point the app at a fixture
    directory. It defaults to the repository's own ``app/static``.
    """

    from fastapi import FastAPI, HTTPException
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from app.api.operating_surface import build_operating_surface
    from app.api.routes import (
        body_error_exception,
        error_body,
        http_exception_response,
        register_operating_routes,
        set_request_id,
    )
    from app.tools.approval import ApprovalPolicy

    api_service = service or ForgeApiService(
        # THE COMPOSITION SUPPLIES THE POLICY. `RunExecutor` treats an absent policy
        # as an unconditional denial, so without this a service built by the HTTP
        # factory could not execute a declared command at all. `ApprovalPolicy()`
        # requires approval only for its mandatory tool set, so host commands are
        # unaffected; a deployment that needs more passes its own policy to
        # `ForgeApiService` and this default is not used.
        #
        # The service's own attribute is deliberately left as `None` when no policy
        # is supplied (see `ForgeApiService.__init__`): "no approval configured" is a
        # real fail-closed state that two contract tests assert directly, and it must
        # not be silently rewritten into "approval configured".
        approval_policy=ApprovalPolicy()
    )
    app = FastAPI(title="Forge AI API", version="0.1.0")

    # ------------------------------------------------------------------
    # THE ERROR CONTRACT (Block #10): one shape for every failure.
    #
    # Before this, the surface answered in four different shapes and one of them
    # carried nothing at all. Measured on the live surface:
    #
    #   * a typed refusal from ``_run_catching`` -- flat JSON with a code;
    #   * an ``HTTPException`` from verify/accept -- the same facts nested under
    #     ``"detail"``, so a client had to know which route it hit;
    #   * FastAPI's own validation shape for a malformed parameter;
    #   * an UNHANDLED exception -- ``500`` with the plain-text body
    #     ``Internal Server Error`` (21 bytes), no code and no message. That was
    #     produced by ``StepGraphStateError`` escaping ``POST /api/product/graph``
    #     for a step document with no ``action`` mapping.
    #
    # The three handlers below close the last two. They are registered ONCE, on the
    # application, rather than as per-route ``try/except``: a per-route fix would
    # have to be repeated for every route and would be forgotten on the next one,
    # which is exactly how the fourth shape appeared.
    #
    # NO TRACEBACK, NO SECRET, NO ABSOLUTE PATH EVER LEAVES THESE HANDLERS. The
    # message is chosen from a closed table by code (``app/api/routes.py``), and the
    # exception's own text is never read.
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def _correlate(request, call_next):  # type: ignore[no-untyped-def]
        """Record the caller's own correlation handle for this request.

        Only the caller's ``X-Request-ID`` header is used, and only its first 200
        characters. No id is generated: an invented one would look like a
        correlation with the server's log while correlating with nothing.
        """

        header = request.headers.get("X-Request-ID")
        set_request_id(header if isinstance(header, str) else "")
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def _on_http_exception(request, exc):  # type: ignore[no-untyped-def]
        """Every deliberately raised ``HTTPException``, in the common envelope.

        The status code is NEVER changed here: 400 stays 400, 403 stays 403, 404
        stays 404. What is added is the classification (``error_code``), the bounded
        ``message``, and -- when the caller supplied one -- the correlation handle.
        """

        return http_exception_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _on_request_validation(request, exc):  # type: ignore[no-untyped-def]
        """A malformed query or path parameter: 422, classified, never echoed.

        FastAPI's default body is ``{"detail":[{...}]}`` -- the raw validation errors
        including the offending input. The input is not echoed back, because a
        reflected value is how an error body becomes a probe, and the shape is the
        common envelope so one client rule reads it.
        """

        errors = []
        for item in exc.errors()[:10]:
            location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
            errors.append({"loc": location or "request", "type": str(item.get("type", ""))})
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    **error_body(error_code="invalid_request_schema"),
                    "validation_errors": errors,
                }
            },
        )

    @app.exception_handler(Exception)
    async def _on_unhandled(request, exc):  # type: ignore[no-untyped-def]
        """The last resort: a classified 500 instead of an empty/default body.

        This handler is what makes ``500 Internal Server Error`` (plain text,
        no code) impossible on this surface. It deliberately does NOT convert the
        failure into anything else: the status stays 500, ``executed`` stays false,
        and the response says the detail is in the server's own log -- which is where
        ``logger.exception`` puts it, including the traceback that the caller must
        never see.
        """

        logger.exception(
            "unhandled exception on %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": error_body(error_code="internal_execution_error")},
        )

    # Credentialed CORS is off, and cannot be turned on here: with no authentication
    # there is no credential for a browser to attach, so enabling it would only widen
    # exposure. ``allow_origins`` is an explicit list (see ``resolve_cors_origins``).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins or resolve_cors_origins()),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    # The UI asset allow-list is resolved once, at construction, from a directory
    # the server chose. No request value can add an entry to it.
    app.state.forge_ui_assets = resolve_ui_assets(ui_assets_directory)

    # One operating surface over this exact service instance. Its project boundary
    # is fixed here, from the service's own composition, and is never read from a
    # request.
    register_operating_routes(
        app, lambda: build_operating_surface(api_service)
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return api_service.get_health().to_dict()

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return api_service.get_system_status().to_dict()

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        return [p.to_dict() for p in api_service.list_projects()]

    @app.get("/api/agents")
    def list_agents() -> list[dict[str, Any]]:
        return [a.to_dict() for a in api_service.list_agents()]

    @app.get("/api/dashboard")
    def get_dashboard(force_refresh: bool = False) -> dict[str, Any]:
        return api_service.get_dashboard_report(force_refresh=force_refresh).to_dict()

    @app.post("/api/dashboard/refresh")
    def refresh_dashboard() -> dict[str, Any]:
        return api_service.refresh_dashboard()

    @app.get("/api/capabilities")
    def list_capabilities() -> list[dict[str, Any]]:
        """List the capabilities currently available to Forge.

        Read-only enumeration of the registered model, tool, and workspace
        capabilities. It reports only descriptors and performs no probing or
        scanning. Host environment discovery is not part of this endpoint.
        """
        return api_service.list_capabilities()

    @app.get("/api/tools")
    def list_tools() -> list[dict[str, Any]]:
        """List the tools currently registered for execution."""
        return api_service.list_tools()

    @app.post("/api/tasks/run")
    def run_task(payload: dict[str, Any]) -> dict[str, Any]:
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
    def get_run(run_id: str) -> dict[str, Any]:
        """Return the durable history of a Run, including after a restart.

        Read-only: this reports persisted state and never resumes, retries, or
        re-executes anything.
        """
        # The id is a directory name in the store, so a traversal form is refused
        # here instead of reaching the store and raising an unhandled ValueError.
        # The sibling route in app/api/routes.py applies the same allow-list.
        if not is_safe_run_id(run_id):
            raise HTTPException(status_code=404, detail=f"Unknown run_id '{run_id}'")
        record = api_service.get_run_record(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id '{run_id}'")
        return record

    return app
