"""Launcher for the Core API surface.

Until now there was no way to start this server from the repository: ``create_api_app``
existed and tests imported it, but nothing bound a host or served it. That is how the
``allow_origins=["*"], allow_credentials=True`` configuration survived review -- the
surface was never actually run through a launcher that would have made its exposure
concrete, and an operator who wanted it had to write their own uvicorn invocation with
whatever settings they guessed.

This module is the one place the bind address, the port and the access token are decided:

* **the owner's settings file decides** (``api_bind_host`` / ``api_port`` / ``access_token``
  in ``.forge/settings.json``), because that is deployment shape, not a credential. An
  explicitly set ``FORGE_API_HOST`` / ``FORGE_API_PORT`` still wins, so the desktop launcher
  -- which passes both -- keeps working unchanged, and **loopback is the default** either way;
* the token is resolved through the existing secrets layer (``FORGE_ACCESS_TOKEN``:
  environment, then ``.env``, then the operating system's vault) and only then from the
  settings file, in that order, so a real deployment keeps its secret in ``.env``;
* **a bind beyond loopback without a token refuses to start**, before the application is
  built and before ``uvicorn`` runs, and the exact reason is printed to `stderr` as well
  as raised -- the operator reads it in a terminal and in ``journalctl``;
* one startup line states the bind address, whether auth is on and the token's
  **fingerprint** (six hex characters of its SHA-256, never the token).
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
from typing import Any, Optional, Sequence, Tuple

from app.api.server import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    create_api_app,
    warn_if_exposed,
)

__all__ = [
    "compose_access_policy",
    "main",
    "resolve_bind",
    "resolve_workspace_project_id",
]

logger = logging.getLogger("forge.api")


def resolve_bind(
    environ: Optional[dict[str, str]] = None,
    settings: Optional[object] = None,
) -> Tuple[str, int]:
    """Return ``(host, port)`` for this deployment.

    Precedence, stated once here because two independent readers -- this launcher and a
    test -- must not be able to disagree about which value wins:

    1. an **explicitly set** ``FORGE_API_HOST`` / ``FORGE_API_PORT`` (what the desktop
       launcher passes, and what a service manager may override);
    2. the owner's settings file, when one is supplied (the production launcher always
       supplies it);
    3. loopback and the documented port.

    A variable that is present but empty is an error for the port and the default for the
    host: ``FORGE_API_PORT=""`` used to mean "port 0" to nobody, and refusing it keeps the
    launcher from binding something the operator did not name.
    """

    environment = os.environ if environ is None else environ

    host = DEFAULT_API_HOST
    if "FORGE_API_HOST" in environment:
        host = str(environment.get("FORGE_API_HOST") or "").strip() or DEFAULT_API_HOST
    elif settings is not None:
        reader = getattr(settings, "api_bind_host", None)
        if callable(reader):
            try:
                host = str(reader()).strip() or DEFAULT_API_HOST
            except Exception:  # noqa: BLE001 - an unreadable setting is the default
                host = DEFAULT_API_HOST

    if "FORGE_API_PORT" in environment:
        raw_port = str(environment.get("FORGE_API_PORT") or "").strip()
        try:
            port = int(raw_port)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"FORGE_API_PORT must be an integer, got {raw_port!r}"
            ) from exc
    elif settings is not None:
        reader = getattr(settings, "api_port", None)
        try:
            port = int(reader()) if callable(reader) else DEFAULT_API_PORT
        except Exception:  # noqa: BLE001 - an unreadable setting is the default
            port = DEFAULT_API_PORT
    else:
        port = DEFAULT_API_PORT
    if not 1 <= port <= 65535:
        raise ValueError(f"FORGE_API_PORT must be 1..65535, got {port}")
    return host, port


def compose_access_policy(
    *, settings: Optional[object] = None, secret_store: Optional[object] = None
) -> Any:
    """The access policy this process serves with, from the token it can resolve.

    No token means no policy enforcement **and** no public bind -- the refusal is made by
    :func:`app.api.server.warn_if_exposed` immediately after this returns, so the two facts
    cannot drift apart.
    """

    from app.api.access import AccessPolicy, resolve_access_token

    token = resolve_access_token(secret_store=secret_store, settings=settings)
    return AccessPolicy(token=token)


def build_idempotency_guard() -> Any:
    """Create the durable guard the standard composition uses.

    The ledger root is derived from the run-store root -- the repository's existing durable-location
    contract (``FORGE_RUN_STORE_ROOT``, resolved by ``app.runtime.run_store.default_run_store_root``).
    Two rules are deliberate:

    * the **process-wide default guard is not used**: its own docstring records that it shares one
      ledger root across every caller, and this project has already measured false failures from
      shared default roots;
    * nothing here converts a failure into permission. The guard's ``begin`` degrades a ledger
      failure to a refusal, and the surface composes no step-graph runner without a guard, so both
      paths stay fail-closed.
    """

    from app.agent_runtime.idempotency_integration import RunIdempotencyGuard
    from app.runtime.run_store import default_run_store_root

    return RunIdempotencyGuard.create(
        ledger_root=str(pathlib.Path(default_run_store_root()) / "idempotency")
    )


def resolve_workspace_project_id(workspace_root: Optional[pathlib.Path] = None) -> str:
    """Return the stable project id that belongs to this workspace.

    The id is not invented here: ``app.runtime.run_store.project_run_store_root`` already derives one
    per canonical workspace path (``ws-<digest>``) and every run, snapshot and artifact is stored under
    it, so two workspaces can never resolve to the same id and one workspace always resolves to the
    same one. Reusing that mechanism keeps the project id stable across restarts, bound to exactly one
    workspace, and independent of anything a request can supply.
    """

    from app.runtime.run_store import project_run_store_root

    root = pathlib.Path(workspace_root) if workspace_root is not None else pathlib.Path(__file__).resolve().parents[2]
    return project_run_store_root(root).name


def compose_project_service(settings: Any) -> Any:
    """The lifecycle service the desktop application delivers projects with.

    This is the one place the *product* decision about a project's folder is made: the
    store is built with a **visible root** read from the owner's own settings file, so a
    new project's files appear in ``%USERPROFILE%\\Desktop\\Forge Projects`` (or whatever
    he chose in «Настройки») instead of a hidden directory under the repository. The root
    is supplied as a callable, so editing it takes effect for the next project without a
    restart.

    The preference is resolved, not trusted. On the owner's machine the desktop folder
    cannot be written to by this process (`PermissionError` on a low-integrity token), and
    that must cost him a sentence, not a project: the policy probes the configured root,
    falls through the documented candidates, **remembers** the one it used in
    ``.forge/settings.json`` (``project_root_fallback``), and keeps every root this
    deployment has used in the boundary check so already-delivered projects stay openable.

    The record directory (``.forge/projects``) is untouched: versions, the artifact
    manifest and run links stay there. ``create_api_app`` composes no such service by
    itself -- a test or a laboratory script that builds its own app keeps the legacy
    hidden workspace and never writes into the owner's desktop.
    """

    from app.projects.root_selection import ProjectRootPolicy
    from app.projects.service import ProjectService
    from app.projects.store import ProjectStore

    policy = ProjectRootPolicy(
        lambda: settings.project_root(),
        remember=settings.remember_fallback_root,
        known=settings.known_project_roots,
    )
    return ProjectService(ProjectStore(root_policy=policy))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Serve the Core API. Returns a process exit code."""

    import uvicorn

    from app.api.access import access_log_line
    from app.config.app_settings import AppSettingsStore

    # The owner's settings are read once and shared: they decide the bind, whether a token
    # from that same file is in force, the projects folder and everything the interface can
    # change. Composed before the bind is resolved, because both readers must agree.
    settings = AppSettingsStore()
    host, port = resolve_bind(settings=settings)
    access = compose_access_policy(settings=settings)
    # THE REFUSAL, BEFORE ANYTHING IS BUILT. A non-loopback bind without a token is not a
    # configuration this product starts; the reason is printed as well as raised, because a
    # service manager shows stderr and an operator reads it there.
    try:
        warn_if_exposed(host, access.token)
    except RuntimeError as refusal:
        print(f"Forge API: {refusal}", file=sys.stderr, flush=True)
        logger.error("%s", refusal)
        raise
    # One guard, created here from the durable root, so every task path this server exposes
    # shares one ledger while the process-wide default stays untouched.
    guard = build_idempotency_guard()
    # The composition, not a request, decides which project this server acts for. ``list_projects``
    # already models this workspace as exactly one project, so the service must be told its id here;
    # without that the service stays unbound and every task is refused (which is the correct
    # fail-closed behaviour, but it also means the application can never run anything).
    project_id = resolve_workspace_project_id()
    # The configured projects folder is created here, at startup, so it is visible before
    # the first project exists ("я вставляю промт и в конце получаю на рабочий стол готовый
    # проект" -- the destination has to be there to be a destination). A folder that cannot
    # be created is not fatal: the first project reports the refusal where it happens.
    try:
        settings.ensure_root()
    except Exception:  # noqa: BLE001 - an unusable root must not stop the API from serving
        pass
    app: Any = create_api_app(
        idempotency_guard=guard,
        authorized_project_id=project_id,
        project_service=compose_project_service(settings),
        settings_store=settings,
        access=access,
    )
    # The one line an operator needs after a restart: where it listens, whether auth is on,
    # and which token (by fingerprint only) this process holds.
    line = access_log_line(host=host, port=port, policy=access)
    print(line, flush=True)
    logger.info("%s", line)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the launcher
    raise SystemExit(main(sys.argv[1:]))
