"""Launcher for the Core API surface.

Until now there was no way to start this server from the repository: ``create_api_app``
existed and tests imported it, but nothing bound a host or served it. That is how the
``allow_origins=["*"], allow_credentials=True`` configuration survived review -- the
surface was never actually run through a launcher that would have made its exposure
concrete, and an operator who wanted it had to write their own uvicorn invocation with
whatever settings they guessed.

This module is the one place the bind address and port are decided:

* **loopback by default** (``FORGE_API_HOST`` / ``FORGE_API_PORT``);
* a **refusal** to bind beyond loopback: :func:`app.api.server.warn_if_exposed` raises
  before the application is built and before ``uvicorn`` starts, because this surface
  has no authentication and exposing it is not something this launcher does on request;
* no deployment topology is invented. Choosing a real production serving model is the
  owner's decision, recorded as an open question, not something this file settles.
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import Any, Optional, Sequence

from app.api.server import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    create_api_app,
    warn_if_exposed,
)

__all__ = ["resolve_bind", "main"]


def resolve_bind(environ: Optional[dict[str, str]] = None) -> tuple[str, int]:
    """Return ``(host, port)`` from the environment, defaulting to loopback."""

    environment = os.environ if environ is None else environ
    host = str(environment.get("FORGE_API_HOST", DEFAULT_API_HOST)).strip()
    if not host:
        host = DEFAULT_API_HOST
    raw_port = str(environment.get("FORGE_API_PORT", DEFAULT_API_PORT)).strip()
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"FORGE_API_PORT must be an integer, got {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"FORGE_API_PORT must be 1..65535, got {port}")
    return host, port


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


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Serve the Core API. Returns a process exit code."""

    import uvicorn

    host, port = resolve_bind()
    warn_if_exposed(host)
    # One guard, created here from the durable root, so every task path this server exposes
    # shares one ledger while the process-wide default stays untouched.
    guard = build_idempotency_guard()
    # The composition, not a request, decides which project this server acts for. ``list_projects``
    # already models this workspace as exactly one project, so the service must be told its id here;
    # without that the service stays unbound and every task is refused (which is the correct
    # fail-closed behaviour, but it also means the application can never run anything).
    project_id = resolve_workspace_project_id()
    app: Any = create_api_app(idempotency_guard=guard, authorized_project_id=project_id)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the launcher
    raise SystemExit(main(sys.argv[1:]))
