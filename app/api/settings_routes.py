"""The owner's settings: read them, change where new projects are delivered, decide
whether a project window may fall back to a browser without its sandbox, and cap the
automatic model tier.

These routes and nothing else:

```
GET  /api/settings   -> the project root, its default, whether it exists,
                        whether the window retry without a sandbox is allowed,
                        which browsers this machine remembers as unable to start
                        their sandbox, whether the discovery questions may be
                        helped by a model, the ceiling on the automatic model
                        tier, and the decision that ceiling produces right now
                        for one sample COMPLEX task (model + price per 1M)
POST /api/settings   -> {"project_root": "..."} | {"use_desktop": true}
                        | {"window_downgrade_allowed": false}
                        | {"forget_window_plan_memory": true}
                        | {"questions_from_model": true}
                        | {"max_cost_tier": "free" | "cheap" | "paid"}
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

from typing import Any, Callable, Dict, Optional

from app.config.app_settings import AppSettingsError

__all__ = ["register_settings_routes"]


def register_settings_routes(
    app: Any,
    settings: Any,
    *,
    routing_preview: Optional[Callable[[], Dict[str, Any]]] = None,
) -> None:
    """Attach the settings routes to ``app`` for one settings store.

    ``settings`` is the composition's own :class:`app.config.app_settings.AppSettingsStore`.
    It is created once, at composition, so the screen and the project store read the same
    file. The store publishes on ``app.state`` so a test can assert what was composed.

    ``routing_preview`` is the composition's read-only answer to *what would the router
    choose right now for a sample complex task, and what does that model cost?* It
    dispatches nothing; a composition that supplies none simply omits the preview, and the
    panel then shows the ceiling without a price rather than inventing one.
    """

    from fastapi import Body

    from app.api.routes import body_error_exception, error_body

    app.state.forge_settings = settings

    def _preview() -> Optional[Dict[str, Any]]:
        if not callable(routing_preview):
            return None
        try:
            payload = routing_preview()
        except Exception:  # noqa: BLE001 - a missing preview is not a broken settings screen
            return None
        return dict(payload) if isinstance(payload, dict) else None

    def _payload(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = dict(settings.describe())
        preview = _preview()
        if preview is not None:
            body["routing_preview"] = preview
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
        """Set the project root, restore the desktop default, forbid a window retry, forget
        the remembered browsers, decide whether the discovery questions may be helped by a
        model, or cap the automatic model tier.

        Exactly one of ``project_root`` (a full path), ``use_desktop`` (``true``),
        ``window_downgrade_allowed``, ``forget_window_plan_memory`` and
        ``questions_from_model`` (booleans), or ``max_cost_tier`` (``"free"`` |
        ``"cheap"`` | ``"paid"``) is required. The third is the «Как открываются
        окна» switch: ``false`` forbids the one retry without a browser's sandbox. The
        fourth is «Забыть» beside it: it clears what this machine has measured about its
        browsers, so the next launch tries their sandbox again. The fifth is «Вопросы по
        смыслу задачи (через модель)»: ``true`` lets the optional model path write up to
        three questions, it is off by default, and it spends the owner's tokens. The sixth
        is «Потолок тарифа»: the highest model tier automatic routing may choose, stored in
        this application's own settings file (never ``.env``). Every other value is refused
        rather than guessed.
        """

        body = dict(payload or {})
        use_desktop = body.get("use_desktop")
        if use_desktop is not None and not isinstance(use_desktop, bool):
            raise body_error_exception("'use_desktop' must be a boolean")
        request_root = body.get("project_root")
        if request_root is not None and not isinstance(request_root, str):
            raise body_error_exception("'project_root' must be a string")
        allow_downgrade = body.get("window_downgrade_allowed")
        if allow_downgrade is not None and not isinstance(allow_downgrade, bool):
            raise body_error_exception("'window_downgrade_allowed' must be a boolean")
        forget_memory = body.get("forget_window_plan_memory")
        if forget_memory is not None and not isinstance(forget_memory, bool):
            raise body_error_exception("'forget_window_plan_memory' must be a boolean")
        from_model = body.get("questions_from_model")
        if from_model is not None and not isinstance(from_model, bool):
            raise body_error_exception("'questions_from_model' must be a boolean")
        max_cost_tier = body.get("max_cost_tier")
        if max_cost_tier is not None and not isinstance(max_cost_tier, str):
            raise body_error_exception("'max_cost_tier' must be 'free', 'cheap' or 'paid'")
        if bool(use_desktop) and request_root is not None:
            raise body_error_exception(
                "send either 'project_root' or 'use_desktop', not both"
            )
        if max_cost_tier is not None and (
            bool(use_desktop)
            or request_root is not None
            or allow_downgrade is not None
            or forget_memory is not None
            or from_model is not None
        ):
            raise body_error_exception(
                "send 'max_cost_tier' alone, not with a project root"
            )
        if allow_downgrade is not None and (bool(use_desktop) or request_root is not None):
            raise body_error_exception(
                "send 'window_downgrade_allowed' alone, not with a project root"
            )
        if forget_memory is not None and (
            bool(use_desktop)
            or request_root is not None
            or allow_downgrade is not None
        ):
            raise body_error_exception(
                "send 'forget_window_plan_memory' alone, not with a project root"
            )
        if from_model is not None and (
            bool(use_desktop)
            or request_root is not None
            or allow_downgrade is not None
            or forget_memory is not None
        ):
            raise body_error_exception(
                "send 'questions_from_model' alone, not with a project root"
            )
        if forget_memory is not None:
            # «Забыть»: one key of the same file. It changes nothing about where projects
            # live, and it never touches the switch that forbids the retry.
            forgotten = settings.forget_window_plan_memory()
            return _payload({"forgotten_window_plan_memory": bool(forgotten)})
        if from_model is not None:
            # «Вопросы по смыслу задачи (через модель)»: one key of the same file, off by
            # default, changing nothing about where projects live.
            try:
                settings.set_questions_from_model(from_model)
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
            return _payload()
        if max_cost_tier is not None:
            # «Потолок тарифа»: one key of the same file. It changes nothing about where
            # projects live, and the router reads it on the next decision -- no restart.
            try:
                settings.set_max_cost_tier(max_cost_tier)
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
            return _payload()
        if allow_downgrade is not None:
            # The window switch is one key of the same file, and it changes nothing about
            # where projects live: no root is created or re-decided here.
            try:
                settings.set_window_downgrade_allowed(allow_downgrade)
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
            return _payload()
        if not bool(use_desktop) and request_root is None:
            raise body_error_exception(
                "send 'project_root' (a full path), 'use_desktop': true, "
                "'window_downgrade_allowed', 'questions_from_model' or 'max_cost_tier'"
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
