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
from app.api.service import (
    ForgeApiService,
    IdempotencyUnavailable,
    PaidModelRequiresConfirmation,
    RunIdempotencyConflict,
)
from app.reports.evidence import build_evidence
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


def _provider_registry_ids(api_service: Any) -> list[str]:
    """The provider ids this composition actually registered, or ``[]``.

    Read from the service's own runtime rather than from settings: a provider that is
    configured but not registered cannot serve a run, and the card's technical block
    must name what exists, not what is hoped for. Any failure yields an empty list,
    which the card prints as "—" instead of a guess.
    """

    runtime = getattr(api_service, "runtime", None)
    registry = getattr(runtime, "provider_registry", None)
    lister = getattr(registry, "list_providers", None)
    if not callable(lister):
        return []
    try:
        names = lister()
    except Exception:  # noqa: BLE001 - an unreadable registry is an unknown fact
        return []
    return sorted({str(name) for name in names if str(name or "").strip()})



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


def _compose_provider_key_service(api_service: object, settings: object) -> object:
    """Build the «Провайдеры и ключи» service over *this* composition's facts.

    Three read-only inputs, all from the existing runtime -- never a copy of them:

    * the model registry's providers, so the section lists what this build knows and
      each entry's secret variable is the one its provider really reads;
    * the composition's ``enabled_providers``, so an entry can honestly be reported as
      «отключён» rather than «готов»;
    * the owner's settings file, shared with the projects-folder setting.

    The vault is not resolved here: the service resolves it per call, which is what lets
    a test (and the owner) see a key change without a restart.
    """

    from app.config.provider_keys import ProviderKeyService, ProviderKeyStore

    def _registry_providers() -> tuple:
        registry = getattr(api_service, "_model_registry", None)
        if registry is None:
            runtime = getattr(api_service, "_runtime", None)
            registry = getattr(runtime, "model_registry", None) if runtime is not None else None
        if registry is None:
            try:
                from app.agents.providers.model_registry import create_default_registry

                registry = create_default_registry()
            except Exception:  # noqa: BLE001 - an absent registry is reported, not faked
                registry = None
        if registry is None:
            return ()
        try:
            return tuple(registry.list_providers())
        except Exception:  # noqa: BLE001 - an unreadable registry lists nothing
            return ()

    def _enabled_providers():
        from app.api.service import _enabled_provider_names

        return _enabled_provider_names(getattr(api_service, "_runtime", None))

    return ProviderKeyService(
        store=ProviderKeyStore(settings),
        registry_providers=_registry_providers,
        enabled_providers=_enabled_providers,
    )


def _compose_question_model(api_service: object, settings: object) -> object:
    """The optional «вопросы по смыслу задачи (через модель)» assistant.

    Two independent conditions must hold before anything is called:

    * the owner turned ``questions_from_model`` on (it is read on every refresh, so
      turning it off takes effect without a restart);
    * the composition's default provider is ready: registered, enabled, not the offline
      provider, and its key variable resolves -- the same facts «Провайдеры и ключи»
      derives its «готов» from.

    The resolver runs only when both hold, and every failure answers ``None``, which the
    discovery layer treats exactly as "no model": the deterministic bank is asked.
    Nothing here starts a call at composition time, and nothing here can block a session.
    """

    from app.discovery.llm_questions import ModelQuestionAssistant

    def _resolve_provider():
        runtime = getattr(api_service, "runtime", None)
        runtime_settings = getattr(runtime, "settings", None)
        name = str(
            getattr(runtime_settings, "default_provider", "") or ""
        ).strip().lower()
        if not name or name == "mock":
            return None
        enabled = getattr(runtime_settings, "enabled_providers", ()) or ()
        if name not in {str(item).strip().lower() for item in enabled}:
            return None
        registry = getattr(runtime, "provider_registry", None)
        getter = getattr(registry, "get", None)
        if not callable(getter):
            return None
        try:
            provider = getter(name)
        except Exception:  # noqa: BLE001 - an unregistered provider is not ready
            return None
        config = getattr(provider, "config", None)
        if config is None or not bool(getattr(config, "enabled", False)):
            return None
        secret_name = getattr(config, "api_key_env_var", None)
        if secret_name:
            secret_store = getattr(provider, "secret_store", None)
            has_secret = getattr(secret_store, "has_secret", None)
            try:
                if not callable(has_secret) or not has_secret(secret_name):
                    return None
            except Exception:  # noqa: BLE001 - an unreadable secret is an absent one
                return None
        return provider

    return ModelQuestionAssistant(
        _resolve_provider,
        enabled=settings.questions_from_model,
    )


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
    idempotency_guard: Optional[object] = None,
    authorized_project_id: Optional[str] = None,
    discovery_service: Optional[object] = None,
    project_service: Optional[object] = None,
    project_runner: Optional[object] = None,
    settings_store: Optional[object] = None,
    project_opener: Optional[object] = None,
    window_opener: Optional[object] = None,
    provider_key_service: Optional[object] = None,
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

    ``discovery_service`` is the Discovery/Clarification layer that sits *in front
    of* the pipeline (``app/discovery``). It defaults to a service over the
    deployment's own store, and a test can supply one rooted at a temporary
    directory. It adds no executor: the task route below only uses it to refuse an
    unconfirmed run and to prepend a confirmed specification to the task text.

    ``project_service`` is the persistent project lifecycle
    (``app.projects.service``). It defaults to a service over the deployment's own
    store and a test can supply one rooted at a temporary directory. It creates no
    executor either: a confirmed discovery session creates a project record, and the
    task route copies the *existing* run's written files into that project's
    workspace after the run finished.

    ``settings_store`` is the owner's own settings file
    (:class:`app.config.app_settings.AppSettingsStore`). It is registered here so the
    «Настройки» screen can read and change the project root. It deliberately does **not**
    decide where a project's files go by itself: that is the ``project_service``'s store,
    composed by the launcher with the same settings. A caller that composes its own
    service (a test, a laboratory script) therefore keeps the layout it asked for and
    never writes into the owner's desktop.

    ``window_opener`` is the OS window integration the launch route
    (``POST /api/projects/{id}/launch``) uses to show a project in its own window. It
    defaults to the real :class:`app.projects.app_window.AppWindowOpener` and is
    injectable for the same reason ``project_opener`` is: a test proves the exact
    argument vector without a browser window opening on the test machine.

    ``provider_key_service`` is the «Провайдеры и ключи» behaviour
    (:class:`app.config.provider_keys.ProviderKeyService`): it stores a provider's
    non-secret metadata in the owner's settings file and its key in the operating
    system's credential vault. It defaults to a service over the same settings store,
    this runtime's provider registry and this runtime's enabled set, so a provider added
    through the routes is visible to the existing provider layer on the next request. A
    test supplies its own with an in-memory vault, so no real credential is written.
    """

    from fastapi import FastAPI, HTTPException, Request, Header
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from app.api.operating_surface import build_operating_surface
    from app.api.discovery_routes import register_discovery_routes
    from app.api.project_routes import register_project_routes
    from app.api.settings_routes import register_settings_routes
    from app.config.app_settings import AppSettingsStore
    from app.execution.containment import sandbox_settings_from_environment
    from app.projects.preview import PreviewRegistry
    from app.projects.runner import LocalRunner
    from app.api.routes import (
        body_error_exception,
        error_body,
        http_exception_response,
        register_operating_routes,
        set_request_id,
    )
    from app.discovery.models import (
        DiscoveryError,
        DiscoveryNotConfirmed,
        UnknownDiscoverySession,
    )
    from app.discovery.service import DiscoveryService, apply_specification
    from app.projects.service import ProjectService
    from app.projects.store import ProjectDeleted, UnknownProject
    from app.tools.approval import ApprovalPolicy

    api_service = service or ForgeApiService(
        idempotency_guard=idempotency_guard,
        authorized_project_id=authorized_project_id,
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
    def _authorized_surface():
        """Build the operating surface, then bind the service to the project that surface resolved.

        The surface is built lazily by the route registration below, so binding must happen here, at the
        moment the project actually exists — not earlier, when no project has been resolved yet. The value
        is the server's own: `project_overview()` is what `app.state.forge_project_id` is taken from, so
        there is one authority, not two. The binding runs once, because the surface is built once.
        """

        surface = build_operating_surface(api_service)
        project = ""
        if surface is not None:
            project = str(surface.project_overview().get("project_id") or "")
        if project:
            api_service.bind_authorized_project(project)
        return surface

    # The composition is the sole authority for the project. When the caller supplies the
    # server-derived project, bind it here, before any route exists and before any run can be
    # created. A request can never supply this value; a missing value leaves the service
    # unbound, and project-sensitive execution then fails closed.
    if authorized_project_id:
        api_service.bind_authorized_project(str(authorized_project_id))

    register_operating_routes(app, _authorized_surface)

    # One project lifecycle service for this process, composed here exactly like the
    # operating surface and the discovery service: the store root comes from the
    # deployment, never from a request. It creates no executor and starts no run.
    projects = (
        project_service if project_service is not None else ProjectService()
    )
    # One preview registry for this process. It holds *runtime* state (a local HTTP
    # server that is listening right now), so it is created at composition -- not
    # stored on a project record, where it would be a claim that outlives the socket.
    # The registry's server threads are daemons, so nothing keeps listening after the
    # process exits, and ``POST /api/projects/{id}/preview {"action": "stop"}`` is the
    # deterministic way to end one while the process keeps running.
    previews = PreviewRegistry()
    app.state.forge_previews = previews
    # One local runner for this process, composed beside the registry and sharing its
    # store. It holds only *runtime* state (a live child, its job object, its bounded
    # log), which is exactly why it is created here and never written to a record. It
    # starts nothing at composition: every start needs the owner's explicit approval
    # through ``POST /api/projects/{id}/run``.
    runner = (
        project_runner
        if project_runner is not None
        else LocalRunner(
            store=getattr(projects, "store", None),
            previews=previews,
            **sandbox_settings_from_environment(),
        )
    )
    app.state.forge_runner = runner
    # The owner's settings, composed once for this process. They are read here -- before
    # the launch route exists -- because the window it opens consults one of them: whether
    # a browser whose own sandbox cannot start may be retried without it. The same store
    # backs «Настройки», so the switch and the launch can never disagree.
    owner_settings = settings_store if settings_store is not None else AppSettingsStore()
    # The card's technical block needs facts the project store does not hold: the
    # run's own durable record, the provider registry ids and the route list. They
    # are supplied as read-only callables from this composition, so the project
    # layer keeps knowing nothing about the run store or the HTTP surface.
    register_project_routes(
        app,
        projects,
        run_record_loader=api_service.get_run_record,
        preview_registry=previews,
        runner=runner,
        opener=project_opener,
        window_opener=window_opener,
        window_policy=owner_settings.window_downgrade_allowed,
        environment_facts=lambda: {
            "provider_registry_ids": _provider_registry_ids(api_service),
            "api_routes": sorted(
                {
                    str(getattr(route, "path", "") or "")
                    for route in app.routes
                    if getattr(route, "path", None)
                }
            ),
        },
    )

    # One discovery service for this process, composed here exactly like the
    # operating surface: the store root comes from the deployment, never from a
    # request. It creates no executor and starts no run. It is handed the project
    # service so that confirming a session materialises its project record, and the
    # optional model assistant -- which is inert while the setting is off.
    discovery = (
        discovery_service
        if discovery_service is not None
        else DiscoveryService(
            question_model=_compose_question_model(api_service, owner_settings)
        )
    )
    register_discovery_routes(app, discovery, projects)

    # The routes of that same settings store are registered here. The value it holds is
    # consumed by the launcher (``app/api/main.py``), which builds the project store with
    # it, and by the launch route's window policy, composed above -- so «Настройки» and
    # where a project lands, and how its window opens, cannot disagree.
    register_settings_routes(app, owner_settings)

    # «Провайдеры и ключи»: the place the owner could not find. The non-secret half of an
    # entry is written to the *same* settings file as the projects folder; the API key goes
    # to the operating system's credential vault and never to a file, a log or a response.
    # The registry and the enabled set come from this runtime, so a provider added here is
    # resolvable by the existing provider layer on the very next request -- no restart, no
    # second registry.
    from app.api.provider_key_routes import register_provider_key_routes

    register_provider_key_routes(
        app,
        provider_key_service
        if provider_key_service is not None
        else _compose_provider_key_service(api_service, owner_settings),
    )

    def _require_run_target(project_id: str) -> str:
        """The id of a **live, active** project this run may be attached to.

        The same three refusals the project routes make, applied *before* anything is
        dispatched -- so a run for an id no record names writes nothing at all:

        * ``404 unknown_project`` when no record exists (a malformed id included);
        * ``409 project_deleted`` when the id is in ``projects-deleted``;
        * ``409 illegal_project_transition`` when the record is archived.

        The guard is the project service's own (``require_active``: deletion marker
        first, then the record), never a re-implementation of it here.
        """

        try:
            project = projects.require_active(project_id)
        except UnknownProject as unknown:
            raise HTTPException(
                status_code=404,
                detail={
                    **error_body(error_code=unknown.code, code=unknown.code),
                    "error": str(unknown),
                },
            ) from unknown
        except ProjectDeleted as deleted:
            raise HTTPException(
                status_code=409,
                detail={
                    **error_body(error_code=deleted.code, code=deleted.code),
                    "error": str(deleted),
                },
            ) from deleted
        if bool(getattr(project, "archived", False)):
            message = (
                f"an archived project cannot receive a run: restore '{project.project_id}' "
                "first"
            )
            raise HTTPException(
                status_code=409,
                detail={
                    **error_body(
                        error_code="illegal_project_transition",
                        code="illegal_project_transition",
                    ),
                    "error": message,
                },
            )
        return str(project.project_id)

    def _attach_project(
        project_id: str, outcome: Any
    ) -> tuple[str, Optional[dict[str, Any]], str, int]:
        """Attach one finished run to the project it belongs to.

        ``project_id`` is the project the **composition** resolved before the run was
        dispatched -- from the linked discovery session or from the ``proj-…`` id the
        caller named. It is never taken from the answer, the plan or the task text.

        Returns ``(status, facts, lifecycle_project_id, version_number)``. ``status``
        is the fact for the ``X-Forge-Project-Attach`` header: ``none`` when no
        project is known, ``ok`` when the run was recorded, ``already_attached`` when
        that very run id is already a version (an idempotent replay must not produce a
        second version), and ``error:<Type>`` when the attachment failed -- in which
        case the failure is logged here and the run's own response is left exactly as
        it was.

        ``facts`` is the project evidence the outcome gate needs *after* the attach:
        the project's status, whether its workspace exists, its artifact manifest,
        and the version's verification and acceptance. It is ``None`` when nothing
        was attached, so the caller publishes the outcome it already has rather than
        inventing a project.

        ``lifecycle_project_id`` is the ``proj-…`` id of the project the attach step
        actually found. It is returned separately from ``facts`` so a replay -- which
        adds no version and therefore has no facts to re-derive the outcome from --
        still publishes the id of the project the run already belongs to. It is the
        record's own identifier, never the workspace id and never synthesised.

        ``version_number`` is the number of the version **this run** produced (or
        already owned, on a replay), and ``0`` when no project was attached. It is
        the number the evidence block cites as a verified fact; it is never guessed
        from ``current_version``, because a run that wrote nothing leaves the
        previous version current.
        """

        if not isinstance(project_id, str) or not project_id.strip():
            return "none", None, "", 0
        try:
            run_id = str(getattr(outcome.response, "run_id", "") or "")
            project = projects.open_project(project_id)
            already = next(
                (item for item in project.versions if item.run_id == run_id), None
            )
            if already is not None:
                return "already_attached", None, project_id, int(already.version)
            # The durable record is the evidence: it carries the classification the
            # run store derived from the persisted snapshot and the writer's own
            # outcome. ``provider_used``/``model_used`` are fields of the response,
            # not of the record, so they are merged in from the response rather than
            # invented. A missing record is reported honestly from the response
            # instead of being treated as a success.
            record = api_service.get_run_record(run_id) if run_id else None
            source: Any = dict(record) if record is not None else {}
            source["run_id"] = run_id
            source["state"] = source.get("state") or getattr(outcome.response, "state", "")
            source["error"] = source.get("error") or getattr(outcome.response, "error", None)
            source["provider_used"] = source.get("provider_used") or getattr(
                outcome.response, "provider_used", ""
            )
            source["model_used"] = source.get("model_used") or getattr(
                outcome.response, "model_used", ""
            )
            if not source.get("acceptance"):
                source["acceptance"] = (
                    "passed" if getattr(outcome.response, "success", False) else "failed"
                )
            # The written files live in the project's own workspace, so that folder is
            # the source the manifest is recorded from. A run that wrote nothing has no
            # entry to copy and this argument is never read.
            try:
                source_root: Any = projects.store.workspace_path(project_id)
            except Exception:  # noqa: BLE001 - fall back to the run's own workspace
                source_root = api_service.workspace.root
            attached = projects.attach_run(
                project_id, source, source_root=source_root
            )
            # The version THIS RUN produced, not whatever happens to be current. A
            # change that wrote nothing records its attempt but leaves the previous
            # version current; reporting the previous version's manifest, file count
            # and verdicts as this run's result would be the "a finished run is a new
            # version" lie this whole path exists to prevent.
            version = None
            if run_id:
                version = next(
                    (item for item in attached.versions if item.run_id == run_id), None
                )
            # The evidence block cites the version THIS RUN produced, so the number is
            # kept before the fallback below points ``version`` at whatever is current.
            run_version = int(getattr(version, "version", 0) or 0) if version else 0
            # The change-regression verdict is the run's own fact, so it is read from
            # the version this run produced -- never from whatever version happens to
            # be current after a rollback (which is the *previous* one).
            run_rollback: dict[str, Any] = {}
            if version is not None:
                recorded = getattr(version, "rollback", None)
                if isinstance(recorded, Mapping):
                    run_rollback = dict(recorded)
            version = version or attached.current() or attached.latest_version
            files = tuple(getattr(version, "files", ()) or ())
            facts = {
                "project_status": getattr(
                    getattr(attached, "status", None), "value", attached.status
                ),
                # The workspace identity is created with the record; a project that
                # has one has a workspace the artifacts live in.
                "workspace_exists": bool(getattr(attached, "workspace_id", "")),
                "files": files,
                "verification": getattr(version, "verification", "") if version else "",
                "acceptance": getattr(version, "acceptance", "") if version else "",
                # THE HONEST WORD FOR A ROLLED-BACK CHANGE. ``rolled_back`` is derived
                # from this verdict in ``record_project_attach`` (one source, one
                # truth), and it is true only when every written file was restored and
                # proved by digest -- so a refusal («изменения откатаны») can never be
                # read as a success.
                "rollback": run_rollback,
                # The record's own state word, not the response's. The response's
                # ``state`` has already been downgraded by the outcome gate, so feeding
                # it back in would make the re-derivation keep the downgraded word even
                # when the project really does justify ``COMPLETED``: measured as
                # ``outcome=PROJECT_COMPLETED`` beside ``state=SPECIFICATION_READY``.
                "run_state": str(source.get("state") or "")
                or str(getattr(outcome.response, "state", "") or ""),
                "run_success": bool(getattr(outcome.response, "success", False)),
                "error": str(getattr(outcome.response, "error", "") or ""),
            }
            return "ok", facts, project_id, run_version
        except Exception as exc:  # noqa: BLE001 - the run's own result stands
            logger.exception(
                "project attach failed for lifecycle project %s", project_id
            )
            return f"error:{type(exc).__name__}", None, "", 0

    @app.get("/health")
    def health() -> dict[str, Any]:
        return api_service.get_health().to_dict()

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return api_service.get_system_status().to_dict()

    @app.get("/api/version")
    def version() -> dict[str, Any]:
        """Which build answered: version, commit, build time, backend and UI build.

        Read-only and additive. The commit is read from ``git rev-parse`` once, when the
        backend starts, and an unresolved fact is reported as ``"unknown"`` (or
        ``"missing"`` for an unbuilt interface) rather than guessed. The payload contains
        no secret and no environment value -- see ``app.build_info``.
        """

        return api_service.get_version_info()

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        return [p.to_dict() for p in api_service.list_projects()]

    @app.get("/api/agents")
    def list_agents() -> list[dict[str, Any]]:
        return [a.to_dict() for a in api_service.list_agents()]

    @app.get("/api/models")
    def list_models() -> dict[str, Any]:
        """Read-only model catalogue from the existing ModelRegistry.

        Membership, configuration and availability stay distinct: this reports the registry,
        never claims a model is reachable, and exposes no secret value.
        """
        return api_service.list_models_catalogue()

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

    @app.get("/api/costs/states")
    def cost_states() -> Any:
        """Which of the six cost conditions applies to each provider (read-only)."""

        return api_service.cost_states()

    @app.get("/api/providers/balance")
    def provider_balance(provider: Optional[str] = None, refresh: int = 0) -> Any:
        """Read-only balances from the providers' own official endpoints.

        Free endpoints only: no completion is requested, and a provider without a balance
        endpoint is reported as unsupported rather than as zero.
        """

        return api_service.provider_balance(provider, force=bool(refresh))

    @app.get("/api/providers/radox/keys")
    def radox_key_pool() -> Any:
        """Read-only counters for the Radox key pool, per position.

        The keys themselves are re-read from the environment/``.env`` by the service
        and only counted: this route can report how many accounts exist, which one is
        next in the rotation and how each has behaved, and it never returns a key, a
        hash, a prefix or a length.
        """

        return api_service.radox_keys_status()

    @app.post("/api/tasks/run")
    def run_task(
        payload: dict[str, Any],
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        # The typed body parameter must stay: the project's request-schema guard matches a body
        # against it, and without it every POST here is refused as an unknown shape. The
        # idempotency key therefore arrives through FastAPI's declared Header mechanism, which
        # adds no Request object and no field to the byte-pinned request model.
        if not payload.get("description"):
            raise HTTPException(status_code=400, detail="Missing required field 'description'")

        # THE DISCOVERY HAND-OFF. A caller may name the session whose specification
        # this run is executing. The field is read here, at the HTTP boundary, and is
        # deliberately NOT a field of `TaskRunRequest`: that model's field set is the
        # contract several tests pin, and a discovery session id is context for the
        # task text, never a source of execution authority (no command, workspace,
        # profile, environment, timeout or allowlist can be named through it).
        #
        # The guard is HARD: an unconfirmed, cancelled, unknown or still-unanswered
        # session refuses the run with 409/404 before anything is dispatched. When the
        # session IS confirmed, the specification is prepended to the caller's own
        # description and the run continues on the existing path -- same Planner, same
        # Dispatcher, same routing/fallback/cost policy, same provider selection.
        description = payload["description"]

        # THE DRY RUN. ``estimate: true`` answers the automatic routing decision for this
        # text and dispatches nothing: no provider is called, no run is created, no
        # idempotency key is claimed and nothing can be spent. It runs the same pure
        # chooser over the same catalogue the interface reads, which is what lets the
        # launch step preview the decision while the owner types instead of after pressing.
        # A confirmed discovery session contributes its specification when one is named,
        # exactly as the real run would see it; an unconfirmed or unknown session simply
        # contributes nothing here (the hard refusal belongs to the dispatching path).
        if payload.get("estimate") is True:
            estimate_text = str(description)
            preview_session = payload.get("discovery_session_id")
            if isinstance(preview_session, str) and preview_session.strip():
                try:
                    preview_specification = discovery.require_confirmed(
                        preview_session.strip()
                    )
                except DiscoveryError:
                    preview_specification = None
                if preview_specification is not None:
                    estimate_text = apply_specification(
                        preview_specification, estimate_text
                    )
            return JSONResponse(
                api_service.estimate_route(
                    description=estimate_text,
                    category=payload.get("category", "code"),
                    provider_name=payload.get("provider_name"),
                    model_name=payload.get("model_name"),
                    write_mode=bool(payload.get("write_mode", False)),
                    existing_project=bool(
                        str(payload.get("lifecycle_project_id") or "").strip()
                    ),
                )
            )
        discovery_session_id = payload.get("discovery_session_id")
        if discovery_session_id is not None:
            if not isinstance(discovery_session_id, str) or not discovery_session_id.strip():
                raise body_error_exception(
                    "'discovery_session_id' must be a non-empty string when supplied"
                )
            try:
                specification = discovery.require_confirmed(discovery_session_id)
            except UnknownDiscoverySession as unknown:
                raise HTTPException(
                    status_code=404,
                    detail={
                        **error_body(
                            error_code=unknown.code,
                            code=unknown.code,
                        ),
                        "error": str(unknown),
                    },
                ) from unknown
            except DiscoveryNotConfirmed as not_confirmed:
                raise HTTPException(
                    status_code=409,
                    detail={
                        **error_body(
                            error_code=not_confirmed.code,
                            code=not_confirmed.code,
                        ),
                        "error": str(not_confirmed),
                    },
                ) from not_confirmed
            except DiscoveryError as refused:
                raise HTTPException(
                    status_code=409,
                    detail={
                        **error_body(
                            error_code=refused.code,
                            code=refused.code,
                        ),
                        "error": str(refused),
                    },
                ) from refused
            description = apply_specification(specification, description)

        # THE CLARIFICATION HAND-OFF. When the previous answer asked questions instead of
        # writing files, the owner's answers travel with the next dispatch here. They are
        # read at the HTTP boundary and are deliberately NOT fields of `TaskRunRequest`
        # (that model's field set is the contract several tests pin, exactly like the
        # discovery session id above): they are task text and durable context, never a
        # source of execution authority -- no command, workspace, profile, environment,
        # timeout or allowlist can be named through them.
        #
        # ``clarification_run_id`` names the run whose questions were answered. The
        # service reads that run's own record, so a retry keeps the answers already
        # given instead of asking for them again. An unknown id is not an error: it
        # simply means a fresh clarification context (the confirmed-session case, where
        # there is no earlier run to inherit from).
        clarification_answers = payload.get("clarification_answers")
        if clarification_answers is not None:
            if not isinstance(clarification_answers, list):
                raise body_error_exception(
                    "'clarification_answers' must be a list of {question, answer} items"
                )
            for item in clarification_answers:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("question"), str)
                    or not isinstance(item.get("answer"), str)
                ):
                    raise body_error_exception(
                        "each clarification answer must be an object with string "
                        "'question' and 'answer'"
                    )
        clarification_run_id = payload.get("clarification_run_id")
        if clarification_run_id is not None:
            if not isinstance(clarification_run_id, str) or not clarification_run_id.strip():
                raise body_error_exception(
                    "'clarification_run_id' must be a non-empty string when supplied"
                )
            if not is_safe_run_id(clarification_run_id):
                raise body_error_exception(
                    "'clarification_run_id' is not a usable run id"
                )

        # THE LIFECYCLE PROJECT. A caller that already knows *which* project this run
        # belongs to names that ``proj-…`` id here -- «Проекты» does it for a change
        # request, whose staged version is waiting for exactly one run. Like the
        # discovery session id above it is read at the HTTP boundary and is
        # deliberately NOT a field of `TaskRunRequest`: it grants nothing (no
        # command, workspace, profile, environment or timeout), it only says where
        # the result belongs.
        #
        # It is validated HERE, before anything is dispatched: a run for an unknown,
        # archived or deleted project is refused with 404/409 instead of writing
        # files into a workspace that no record will ever name. When the caller also
        # names a discovery session, both must point at the same project -- a run
        # whose specification and whose target disagree is refused rather than
        # attached to the one the caller did not ask for.
        requested_project_id = payload.get("lifecycle_project_id")
        attach_project_id = ""
        if requested_project_id is not None:
            if not isinstance(requested_project_id, str) or not requested_project_id.strip():
                raise body_error_exception(
                    "'lifecycle_project_id' must be a non-empty string when supplied"
                )
            attach_project_id = _require_run_target(requested_project_id.strip())
        if attach_project_id and discovery_session_id is not None:
            linked_by_session = projects.project_for_session(discovery_session_id)
            if linked_by_session and linked_by_session != attach_project_id:
                message = (
                    "the named discovery session belongs to project "
                    f"'{linked_by_session}', not to '{attach_project_id}'"
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        **error_body(
                            error_code="project_mismatch", code="project_mismatch"
                        ),
                        "error": message,
                    },
                )

        # THE PROJECT WRITE TARGET. When this run belongs to a lifecycle project, the
        # tree a write-mode answer may touch is that project's own controlled folder
        # (``.forge/projects/<id>/workspace``), resolved here from the project store.
        # No request value can name it: the input is the confirmed discovery session
        # this route already validated and/or the ``proj-…`` id it just proved names
        # a live, active record. When no project is linked, the target is absent and
        # the guarded writer keeps the run's own workspace -- which is the
        # read-only-per-project behaviour this route always had.
        target_project = attach_project_id or (
            projects.project_for_session(discovery_session_id)
            if isinstance(discovery_session_id, str) and discovery_session_id.strip()
            else ""
        )
        project_workspace: Optional[Path] = None
        lifecycle_project_id = ""
        lifecycle_project_path = ""
        if target_project and bool(payload.get("write_mode", False)):
            lifecycle_project_id = target_project
            project_workspace = projects.store.workspace_path(
                target_project, create=True
            )
            lifecycle_project_path = projects.store.published_project_path(
                target_project
            )

        req = TaskRunRequest(
            description=description,
            category=payload.get("category", "code"),
            task_id=payload.get("task_id"),
            provider_name=payload.get("provider_name"),
            # The model the caller named for THIS dispatch. It used to be missing
            # here, so the field never reached the service and a caller's model was
            # silently replaced by the provider's configured one.
            model_name=payload.get("model_name"),
            project_id=payload.get("project_id"),
            context=payload.get("context", {}),
            # Opt-in, and false when absent: a caller that never sends it keeps the
            # read-only task path unchanged.
            write_mode=bool(payload.get("write_mode", False)),
        )
        try:
            outcome = api_service.run_task_detailed(
                req,
                idempotency_key=idempotency_key,
                project_workspace=project_workspace,
                lifecycle_project_id=lifecycle_project_id,
                lifecycle_project_path=lifecycle_project_path,
                clarification_answers=clarification_answers,
                clarification_run_id=clarification_run_id,
            )
        except PaidModelRequiresConfirmation as paid:
            # A refusal, not an error: the request was well-formed and the server
            # declined to spend money. 409 says exactly that, and nothing was
            # dispatched. The body carries the fixed error code so a client can act
            # on it without parsing prose.
            raise HTTPException(
                status_code=409,
                detail={
                    **error_body(
                        error_code=paid.code,
                        code=paid.code,
                    ),
                    "error": str(paid),
                },
            ) from paid
        except RunIdempotencyConflict as conflict:
            raise HTTPException(status_code=409, detail=str(conflict)) from conflict
        except IdempotencyUnavailable as unavailable:
            # Fail closed: nothing was executed, and the caller is told why.
            raise HTTPException(status_code=503, detail=str(unavailable)) from unavailable
        # THE PROJECT ATTACH. When the caller named a confirmed discovery session or a
        # ``proj-…`` id, the run that just finished belongs to that project: its
        # written files are copied into the project workspace, a version is recorded,
        # and the lifecycle advances. This runs AFTER the existing run path returned
        # and reads the run's durable record, so nothing above it changes and a
        # failure here can never reinterpret the run's own result. A change request
        # fills the version it staged; a run that wrote no file leaves the previous
        # version current (``attach_run``'s own rule).
        #
        # It runs BEFORE the body is serialized so that the one outcome the caller
        # sees is the truthful one: while the response was assembled no project
        # existed, but once the attach has happened the same classifier is applied
        # to the project's own facts and the response is updated with them. Without
        # this the response would say "specification ready" for a project that was
        # in fact created and completed a moment later.
        attach_status, attach_facts, lifecycle_project_id, attached_version = _attach_project(
            target_project, outcome
        )
        body = outcome.response.to_dict()
        # The lifecycle id is published whenever the attach step found the project,
        # including an idempotent replay: the run really does belong to it even though
        # no second version was added. ``None`` when no lifecycle project exists.
        if lifecycle_project_id:
            body["lifecycle_project_id"] = lifecycle_project_id
        if attach_facts:
            body.update(
                api_service.record_project_attach(
                    outcome.response.run_id,
                    lifecycle_project_id=lifecycle_project_id,
                    **attach_facts,
                )
            )
        # WHAT WAS CHECKED, WHAT WAS ASSUMED, WHAT IS UNKNOWN. Built last, from the
        # facts that actually arrived: the version the attach recorded (its files carry
        # the SHA-256 digests), its verification and its acceptance verdict. A run that
        # attached no project has no manifest here, so its ``verified`` list is empty by
        # construction and the model's own words go to ``assumed``. The run's outcome
        # word is passed for context only and never becomes a verified claim.
        facts_for_evidence = attach_facts or {}
        intent = body.get("model_intent")
        model_summary = (
            str(intent.get("summary") or "")
            if isinstance(intent, Mapping)
            else ""
        )
        body["evidence"] = build_evidence(
            outcome=body.get("outcome", ""),
            files=facts_for_evidence.get("files") or (),
            verification=facts_for_evidence.get("verification", ""),
            acceptance=facts_for_evidence.get("acceptance", ""),
            model_summary=model_summary,
            answer_excerpt=body.get("answer_excerpt", ""),
            run={
                "run_id": str(body.get("run_id") or ""),
                "url": "",
                "http_status": 0,
            },
            project={"version": attached_version},
            # THE ROLLBACK IS EVIDENCE IN ITSELF. When a change was put back, the
            # restored files are a *verified* fact (their digests match the snapshot)
            # and the change is an *unknown* one (nothing of it is on disk) -- so the
            # block cannot present a rolled-back run as a checked result.
            rollback=facts_for_evidence.get("rollback") or None,
        )
        response = JSONResponse(body)
        response.headers["X-Forge-Idempotent-Replay"] = "true" if outcome.idempotent_replay else "false"
        response.headers["X-Forge-History-Recorded"] = "true" if outcome.history_recorded else "false"
        response.headers["X-Forge-Usage-Basis"] = outcome.usage_basis
        response.headers["X-Forge-Cost-Basis"] = outcome.cost_basis
        if outcome.history_error:
            response.headers["X-Forge-History-Error"] = outcome.history_error
        response.headers["X-Forge-Project-Attach"] = attach_status
        return response

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
