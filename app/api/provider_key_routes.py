"""The owner's providers and their keys: three routes and nothing else.

```
GET    /api/settings/providers             -> the configured providers, presets, storage facts
POST   /api/settings/providers             -> add or update one provider (key -> vault)
DELETE /api/settings/providers/{provider}  -> remove that provider's key and entry
```

Why these routes exist
----------------------
The owner could not find where to add a provider with a key: providers came from
``.env``, and he is not supposed to edit it. These routes are that missing place.

What they never do
------------------
* **never return a key.** The payload carries ``has_key`` (one boolean) and
  ``key_source`` (``environment`` / ``vault`` / empty). No value, no length, no
  prefix, no hash. A ``DELETE`` removes the key from the credential vault and
  answers with the same boolean-shaped payload;
* **never write a key to a file.** The non-secret half of an entry (provider id,
  base URL, default model) goes to ``.forge/settings.json``; the key goes to the
  operating system's credential vault (:mod:`app.config.windows_creds`);
* **never echo an error value.** A refusal is this surface's usual envelope with a
  plain sentence chosen by the layer below, and the offending value is not copied
  into it.

Loopback only
-------------
The whole surface refuses a non-loopback bind (``app/api/server.py``). These three
routes add their own request-side check as well, because they are the only ones that
*store* a credential: a request whose peer address is not loopback is refused with
``403 loopback_required``. The in-process test transport has no socket peer at all,
so it is allowed -- the bind guard is what keeps that transport unreachable from
another machine.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# ``Request`` and ``Body`` are imported at module level on purpose: this module uses
# ``from __future__ import annotations``, and FastAPI resolves a route's parameter
# annotations by name -- a name that exists only inside another function's body cannot be
# resolved, so the request object would be read as a missing query parameter.
from fastapi import Body, Request

from app.config.provider_keys import ProviderKeyError

__all__ = ["register_provider_key_routes", "require_loopback"]


#: Peer addresses that are "this machine". ``testclient`` is the in-process ASGI
#: transport's placeholder (no socket), which is only reachable from inside this process.
_LOCAL_PEERS = frozenset({"testclient", ""})


def require_loopback(request: Any) -> None:
    """Refuse a request whose peer is not this machine.

    Exported (rather than inlined) so a test can prove the refusal with a stub request
    instead of needing a second network interface.
    """

    from fastapi import HTTPException

    from app.api.routes import error_body
    from app.api.server import is_loopback_host

    client = getattr(request, "client", None)
    peer = str(getattr(client, "host", "") or "") if client is not None else ""
    if peer in _LOCAL_PEERS or is_loopback_host(peer):
        return
    raise HTTPException(
        status_code=403,
        detail={
            **error_body(error_code="loopback_required", code="loopback_required"),
            "error": "провайдеры и ключи доступны только с этого компьютера",
        },
    )


def _refusal(exc: ProviderKeyError) -> Any:
    """One provider refusal, in the surface's common error envelope, never echoing a value."""

    from fastapi import HTTPException

    from app.api.routes import error_body

    code = str(getattr(exc, "code", "") or "invalid_provider_entry")
    return HTTPException(
        status_code=400,
        detail={
            **error_body(error_code=code, code=code),
            "error": str(exc),
            "field": str(getattr(exc, "field", "") or ""),
        },
    )


def register_provider_key_routes(app: Any, service: Any) -> None:
    """Attach the provider/key routes to ``app`` for one composed service.

    ``service`` is an :class:`app.config.provider_keys.ProviderKeyService` built by the
    composition: it already holds the settings store and the vault, and it already knows
    the registry and the enabled set of *this* runtime -- so a provider added here is
    visible to the existing provider layer on the next request, with no restart and no
    second registry.
    """

    app.state.forge_provider_keys = service

    @app.get("/api/settings/providers")
    def list_providers(request: Request) -> Dict[str, Any]:
        """The configured providers and the presets. Never a key."""

        require_loopback(request)
        return service.describe()

    @app.post("/api/settings/providers")
    def add_provider(
        request: Request, payload: Optional[Dict[str, Any]] = Body(default=None)
    ) -> Dict[str, Any]:
        """Add (or update) one provider: metadata to the settings file, key to the vault.

        The body is ``{"preset", "base_url", "model", "api_key", ["provider_id"]}``. A
        ``provider_id`` is only read for the «Свой» preset: a known preset names its own
        provider, so a request cannot store a key under another provider's variable.
        """

        require_loopback(request)
        body = dict(payload or {})
        for forbidden in ("key", "secret", "token"):
            if forbidden in body:
                raise _refusal(
                    ProviderKeyError(
                        "Передайте ключ в поле api_key.",
                        field=forbidden,
                        code="invalid_provider_entry",
                    )
                )
        try:
            return service.add(body)
        except ProviderKeyError as exc:
            raise _refusal(exc) from None

    @app.delete("/api/settings/providers/{provider_id}")
    def delete_provider(request: Request, provider_id: str) -> Dict[str, Any]:
        """Remove one provider's key from the vault and drop its stored entry."""

        require_loopback(request)
        try:
            return service.delete(provider_id)
        except ProviderKeyError as exc:
            raise _refusal(exc) from None
