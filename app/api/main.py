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


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Serve the Core API. Returns a process exit code."""

    import uvicorn

    host, port = resolve_bind()
    warn_if_exposed(host)
    app: Any = create_api_app()
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the launcher
    raise SystemExit(main(sys.argv[1:]))
