"""The owner's settings: read them, and change where new projects are delivered.

Two routes and nothing else:

```
GET  /api/settings   -> the project root, its default, and whether it exists
POST /api/settings   -> set it: {"project_root": "..."} or {"use_desktop": true}
```

Why this is not ``.env``: the value is the folder the owner's own projects live in, he
edits it in «Настройки», and it must survive a restart. Deployment configuration and
credentials stay in the environment; a chosen folder is neither.

**The absolute path is published here on purpose.** Every other payload in this surface
publishes a relative folder and keeps the absolute one inside ``diagnostics``. This one
screen exists so the owner can *see* and *paste* the folder he picked, so it returns it
verbatim -- and it returns only the value the deployment itself holds, never anything a
request supplied.

A value that cannot be used is refused with the layer's own sentence (``400``): empty,
relative, containing ``..``, a device path, a drive root, or a path that is a file. A
refusal never changes the stored value.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.config.app_settings import AppSettingsError

__all__ = ["register_settings_routes"]


def register_settings_routes(app: Any, settings: Any) -> None:
    """Attach the settings routes to ``app`` for one settings store.

    ``settings`` is the composition's own :class:`app.config.app_settings.AppSettingsStore`.
    It is created once, at composition, so the screen and the project store read the same
    file. The store publishes on ``app.state`` so a test can assert what was composed.
    """

    from fastapi import Body

    from app.api.routes import body_error_exception, error_body

    app.state.forge_settings = settings

    def _payload(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = dict(settings.describe())
        body["ok"] = True
        if extra:
            body.update(extra)
        return body

    @app.get("/api/settings")
    def read_settings() -> Dict[str, Any]:
        """The project root as it is stored, its default, and whether it exists yet."""

        return _payload()

    @app.post("/api/settings")
    def write_settings(payload: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
        """Set the project root, or restore the desktop default.

        Exactly one of ``project_root`` (a full path) and ``use_desktop`` (``true``) is
        required. Both are accepted from this route alone, and neither is echoed back
        beyond the stored value.
        """

        body = dict(payload or {})
        use_desktop = body.get("use_desktop")
        if use_desktop is not None and not isinstance(use_desktop, bool):
            raise body_error_exception("'use_desktop' must be a boolean")
        request_root = body.get("project_root")
        if request_root is not None and not isinstance(request_root, str):
            raise body_error_exception("'project_root' must be a string")
        if bool(use_desktop) and request_root is not None:
            raise body_error_exception(
                "send either 'project_root' or 'use_desktop', not both"
            )
        if not bool(use_desktop) and request_root is None:
            raise body_error_exception(
                "send 'project_root' (a full path) or 'use_desktop': true"
            )
        try:
            if bool(use_desktop):
                settings.use_desktop()
            else:
                settings.set_project_root(request_root)
        except AppSettingsError as exc:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400,
                detail={
                    **error_body(error_code=exc.code, code=exc.code),
                    "error": str(exc),
                    "field": getattr(exc, "field", ""),
                },
            ) from exc
        # The folder itself is created here, not at the next project: an owner who sets a
        # root must be able to open it in Explorer immediately, and a typo has to fail
        # where he typed it rather than silently at the next run.
        created = False
        try:
            settings.ensure_root()
            created = True
        except AppSettingsError as exc:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400,
                detail={
                    **error_body(error_code=exc.code, code=exc.code),
                    "error": str(exc),
                    "field": getattr(exc, "field", ""),
                },
            ) from exc
        return _payload({"created": created})


def project_root_of(settings: Any) -> Optional[Any]:
    """The configured root as a ``Path``, or ``None`` when the store cannot answer.

    Used by the composition to build the project store; an unreadable settings file is
    "not configured", which falls back to the legacy hidden workspace rather than
    inventing a folder.
    """

    try:
        return settings.project_root()
    except Exception:  # noqa: BLE001 - an unreadable setting is "not configured"
        return None
