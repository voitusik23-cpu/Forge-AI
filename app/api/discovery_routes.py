"""Read-only additions to the existing API surface for Discovery / Clarification.

Seven routes, all thin wrappers over :class:`app.discovery.service.DiscoveryService`:

```
POST /api/discovery/sessions                  {goal}            -> session + questions
GET  /api/discovery/sessions/{id}                               -> session
POST /api/discovery/sessions/{id}/answers     {answers:[...]}   -> session
POST /api/discovery/sessions/{id}/skip        {reason?}         -> session
GET  /api/discovery/sessions/{id}/specification                -> specification
POST /api/discovery/sessions/{id}/confirm                       -> session
POST /api/discovery/sessions/{id}/cancel                        -> session
```

They add no authority. A session is the user's own text and the derived
specification; answers are screened before storage, so a route can only ever
return a redaction marker where a credential-shaped answer was given. The
planning/execution guard lives on the existing task route, not here.

Failures use the surface's existing error contract: the status keeps its meaning
(404 unknown session, 400 malformed body, 409 wrong state) and the machine code
is published verbatim.

Confirmation is also where a **persistent project record** is created, when the
composition supplied a project service. The confirm route is the only moment at
which the specification is frozen, so it is the only correct moment to materialise
the record: ``create_from_discovery`` stores the confirmed specification in
``WAITING_FOR_CONFIRMATION`` and the confirmation itself advances it to
``PLANNED``, before any run exists. The response gains an additive ``project``
field; every field it already had is unchanged.
"""

from __future__ import annotations

from typing import Any, Optional

from app.discovery.models import (
    DiscoveryError,
    InvalidClarificationAnswer,
    UnknownDiscoverySession,
)
from app.discovery.service import DiscoveryService

__all__ = ["register_discovery_routes"]


def register_discovery_routes(
    app: Any, discovery: DiscoveryService, projects: Optional[Any] = None
) -> None:
    """Attach the discovery routes to ``app`` and expose the service on its state.

    The service instance is created once, at composition, so every request of one
    process shares one store and one view of the sessions. It is placed on
    ``app.state`` because that is how the rest of this surface publishes a
    composition-time dependency (see ``forge_operating_surface``).

    ``projects`` is the optional project lifecycle service. When it is supplied, a
    confirmed session produces its durable project record; when it is not, the
    confirmation behaves exactly as it did before this parameter existed.
    """

    from fastapi import Body, HTTPException

    from app.api.routes import body_error_exception, error_body

    app.state.forge_discovery = discovery

    def _refusal(exc: DiscoveryError) -> "HTTPException":
        """Map one typed refusal onto the surface's existing error contract."""

        if isinstance(exc, UnknownDiscoverySession):
            status = 404
        elif isinstance(exc, InvalidClarificationAnswer):
            status = 400
        else:
            status = 409
        return HTTPException(
            status_code=status,
            detail={
                **error_body(error_code=exc.code, code=exc.code),
                "error": str(exc),
            },
        )

    def _session_id(session_id: str) -> str:
        # The store refuses an unsafe id itself; this is only the empty case, so
        # an empty path segment answers 404 instead of reaching the store.
        if not str(session_id or "").strip():
            raise _refusal(UnknownDiscoverySession(""))
        return session_id

    def _session_payload(session: Any, project: Optional[Any] = None) -> dict[str, Any]:
        payload = {
            "session": session.to_dict(),
            "questions": [question.to_dict() for question in session.questions],
        }
        if project is not None:
            # Additive: the record's own summary, so a client learns the project id
            # and status without a second request.
            payload["project"] = project.summary()
        return payload

    @app.post("/api/discovery/sessions")
    def create_session(payload: dict[str, Any]) -> Any:
        goal = payload.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise body_error_exception("'goal' must be a non-empty string")
        try:
            session = discovery.start(goal)
        except DiscoveryError as exc:  # pragma: no cover - start cannot refuse today
            raise _refusal(exc) from exc
        return _session_payload(session)

    @app.get("/api/discovery/sessions/{session_id}")
    def get_session(session_id: str) -> Any:
        try:
            session = discovery.get(_session_id(session_id))
        except DiscoveryError as exc:
            raise _refusal(exc) from exc
        return _session_payload(session)

    @app.post("/api/discovery/sessions/{session_id}/answers")
    def answer_session(session_id: str, payload: dict[str, Any]) -> Any:
        raw_answers = payload.get("answers")
        if not isinstance(raw_answers, list) or not raw_answers:
            raise body_error_exception(
                "'answers' must be a non-empty list of {question_id, text} items"
            )
        answers = []
        for item in raw_answers:
            if not isinstance(item, dict):
                raise body_error_exception(
                    "each answer must be an object with 'question_id' and 'text'"
                )
            answers.append(
                {
                    "question_id": item.get("question_id"),
                    "text": item.get("text", ""),
                    "skipped": bool(item.get("skipped", False)),
                }
            )
        try:
            session = discovery.answer(_session_id(session_id), answers)
        except DiscoveryError as exc:
            raise _refusal(exc) from exc
        return _session_payload(session)

    @app.post("/api/discovery/sessions/{session_id}/skip")
    def skip_session(
        session_id: str,
        payload: Optional[dict[str, Any]] = Body(default=None),
    ) -> Any:
        reason = payload.get("reason") if isinstance(payload, dict) else None
        try:
            session = discovery.skip(_session_id(session_id), reason)
        except DiscoveryError as exc:
            raise _refusal(exc) from exc
        return _session_payload(session)

    @app.get("/api/discovery/sessions/{session_id}/specification")
    def specification_for_session(session_id: str) -> Any:
        try:
            specification = discovery.specification(_session_id(session_id))
        except DiscoveryError as exc:
            raise _refusal(exc) from exc
        return {
            "specification": specification.to_dict(),
            "task_text": specification.as_task_text(),
        }

    @app.post("/api/discovery/sessions/{session_id}/confirm")
    def confirm_session(session_id: str) -> Any:
        try:
            session = discovery.confirm(_session_id(session_id))
        except DiscoveryError as exc:
            raise _refusal(exc) from exc
        project = None
        if projects is not None:
            # The specification is frozen exactly here, so the durable record is
            # created exactly here: created in WAITING_FOR_CONFIRMATION and then
            # confirmed to PLANNED, before any run can be attached to it.
            project = projects.create_from_discovery(session)
            project = projects.confirm(project.project_id)
        return _session_payload(session, project)

    @app.post("/api/discovery/sessions/{session_id}/cancel")
    def cancel_session(session_id: str) -> Any:
        try:
            session = discovery.cancel(_session_id(session_id))
        except DiscoveryError as exc:
            raise _refusal(exc) from exc
        return _session_payload(session)
