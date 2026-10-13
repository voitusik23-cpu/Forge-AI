"""HTTP routes for the Block #8 operating surface.

Every route here is a thin adapter: it validates the **shape** of a request,
performs one explicit authorization check, calls exactly one
``OperatingSurface`` method, and serializes the result. No route holds state, and
no route can set an outcome.

Authorization on this surface, stated exactly
---------------------------------------------
This API has no authentication (see ``app/api/server.py``): it is a loopback
surface for a local operator, and adding an identity mechanism here would be a
new architecture decision. What every route below *does* enforce is the boundary
that does exist, and each route's docstring names its own check:

* **project boundary** -- the app is bound to one authoritative project at
  construction. A request that names a different ``project_id`` is refused with
  403 before any read. A caller cannot ask for another project's data because
  there is no code path that would resolve one.
* **run boundary** -- a ``run_id`` must be a safe, allow-listed identifier *and*
  must already be recorded in this service's own ``RunStore``. An id that is not
  recorded here is reported as 404, so a foreign run is indistinguishable from an
  unknown one and leaks nothing.
* **action boundary** -- the caller may name *which* operator-declared action to
  run (``declaration_id``), never *what* it is. The declaration registry, the
  workspace, the execution profile, the command set, the environment and the
  approval policy all come from the server composition. An undeclared name is
  refused.
* **authority boundary** -- **no route in THIS module constructs, substitutes or
  bypasses** ``RunScope``, ``ExecutionCoordinator``, ``AuthorizedExecution`` or
  ``ApprovalPolicy``. Every route delegates: to a ``ForgeApiService`` entry point,
  or to the ``OperatingSurface``, which performs every one of those checks itself on
  every dispatch.

Where the assembly lives, and why it is not here
------------------------------------------------
**Decision (Block #8).** Composition of the execution coordinator and the
``StepGraphRunner`` lives in ``app/api/operating_surface.py``. It does **not** live in
this module, and it was deliberately moved **out** of ``app/api/service.py``. This is
an architectural choice with one reason per layer:

* ``app/api/service.py`` **delegates; it does not spawn.** That is asserted
  structurally by ``test_api_layer_has_no_direct_adapter_or_process_access``, which
  fails if the API *service* names the execution adapter, ``AuthorizedExecution``,
  ``subprocess`` or ``Popen``. Returning assembly to the service would require
  relaxing that assertion -- a security-relevant boundary test weakened to
  accommodate a refactor. The smallest change that satisfies both is to move
  assembly rather than to loosen the check.
* **This module is the HTTP boundary.** It validates request shape, applies the
  per-route authorization check above, and serializes a result. It constructs
  nothing that authorizes anything, so a route can never become the place where a
  missing check is forgotten.
* **``app/api/operating_surface.py`` is the single composition point.** Exactly one
  place builds the coordinator, exactly one place decides the approval policy, and
  exactly one object for the routes to talk to. Spreading assembly across routes
  would let two routes disagree about a policy, which is the failure mode the
  authority boundary exists to prevent.

So the invariant is stated precisely, not weakened: **no *route* constructs or
bypasses an authority component**; the operating surface constructs the one
coordinator, from ingredients the service hands out, and the surface itself grants
nothing -- the runner re-checks ``RunScope``, ``ApprovalPolicy``,
``ExecutionCoordinator`` and the workspace on every dispatch, and the surface refuses
(``None``, or a typed configuration error) rather than ever building a weaker policy.

``test_71``, ``test_72`` and ``test_73`` pin that placement, and this is what each one
actually asserts:

* ``test_71`` checks that **no executable line** of ``app/api/routes.py`` or
  ``app/api/service.py`` names ``LocalExecutionAdapter``, ``AuthorizedExecution``,
  ``subprocess`` or ``Popen`` -- the process-spawning and adapter-level components.
  It does **not** ban ``RunScope``, ``ExecutionCoordinator`` or ``ApprovalPolicy``
  from those two modules, and that distinction is deliberate rather than an
  omission: ``app/api/service.py`` legitimately constructs ``RunScope`` and reads an
  ``ApprovalPolicy`` to hand them to the execution chain. What it may not do is name
  the adapter or spawn a process itself;
* ``test_71`` additionally asserts a **wider** ban against ``app/api/routes.py``
  alone -- ``RunScope``, ``ExecutionCoordinator``, ``ApprovalPolicy`` and
  ``LocalExecutionAdapter`` all absent -- because the HTTP boundary is held to the
  stricter rule that it constructs no authority component at all;
* ``test_72`` checks the other direction: ``operating_surface.py`` is the only module
  under ``app/api/`` that imports and constructs the adapter and the coordinator, and
  it constructs exactly one of each;
* ``test_73`` checks that one surface yields one cached coordinator, so two runs
  cannot be handed two authorities.

An earlier revision of this paragraph claimed those tests pinned that "neither this
module nor ``app/api/service.py`` may name an authority component". That was false as
written: ``service.py`` names ``RunScope`` and ``ApprovalPolicy``, and ``test_71``'s
banned list contained neither. The criterion itself held -- ``routes.py`` names none of
the four -- so the statement was corrected to match what runs, rather than the test
being relaxed to match the statement. The same decision is recorded in both halves of
``docs/SOURCE_OF_TRUTH.md`` §3.5.5.

What this is **not**: process isolation. These are text and contract checks
inside one operating-system process. A process already running as this user is
not confined by them.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any, Mapping

from app.api.operating_surface import (
    GraphRunInFlight,
    OperatingSurface,
    OperatingSurfaceError,
    Refused,
    SurfaceCompositionError,
)

#: The correlation handle of the request being served, set by the composition's
#: middleware. A context variable rather than a parameter because the route
#: callables are registered by decorator and cannot carry one, and because a
#: context variable is per-task, so two concurrent requests never see each other's.
_REQUEST_ID: ContextVar[str] = ContextVar("forge_error_request_id", default="")


def set_request_id(value: object) -> None:
    """Record the correlation handle for the request now being served."""

    _REQUEST_ID.set(str(value or "")[:200])


def current_request_id() -> str:
    """The correlation handle recorded by the middleware, or ``""``."""

    return _REQUEST_ID.get()

__all__ = [
    "authorize_project",
    "validate_idempotency_key",
    "register_operating_routes",
    "ERROR_CODES",
    "body_error_exception",
    "error_body",
    "error_code_for",
    "error_code_for_code",
    "envelope_from_exception",
    "http_exception_response",
    "message_for",
    "set_request_id",
    "current_request_id",
]

# ---------------------------------------------------------------------------
# The stable error contract (Block #10)
# ---------------------------------------------------------------------------
#
# WHAT WAS WRONG, MEASURED ON A LIVE SERVER.
# ------------------------------------------
# This surface had **four** different error shapes and one of them carried no
# information at all:
#
# 1. a typed refusal from ``_run_catching`` -- a FLAT JSON object
#    (``{"refused":true,"code":...,"error":...,"executed":false}``);
# 2. an ``HTTPException`` raised by the verify/accept routes -- the same facts but
#    NESTED by FastAPI under ``"detail"``, so a client had to know which route it
#    hit before it could parse a refusal;
# 3. FastAPI's own validation shape (``{"detail":[{...}]}``) for a malformed query
#    or path parameter;
# 4. an **unhandled exception**, which produced ``500`` with the plain-text body
#    ``Internal Server Error`` -- measured on the live surface for
#    ``POST /api/product/graph`` with ``[{"step_id":"s1","declaration_id":"..."}]``
#    (no ``action`` mapping): ``StepGraphStateError`` escaped the route, the
#    ``_run_catching`` tuple did not include it, and the caller received a
#    status with no machine code, no message and no way to tell a caller error
#    from a server fault.
#
# THE FIX IS ONE COMMON MECHANISM, NOT PER-ROUTE try/except.
# ---------------------------------------------------------
# Every error a caller can receive is produced by :func:`_error_response` (for a
# typed refusal) or by :func:`register_operating_routes`'s app-level exception
# handlers (for anything else), and both agree on one envelope:
#
# * ``error_code`` -- a stable, closed-vocabulary machine code (never a class name,
#   never exception text);
# * ``message`` -- a bounded, sanitized description chosen by the server, never the
#   exception's own string (which can quote a path, a command line or a configured
#   value);
# * ``code`` / ``refused`` / ``configuration_error`` / ``executed`` -- the existing
#   keys, unchanged, so no consumer breaks. ``error_code`` is an ALIAS of ``code``
#   when the surface already had one, and the refusal vocabulary is untouched;
# * ``request_id`` -- only when the transport supplied one (the ``X-Request-ID``
#   header) or the route has a request in scope. It is a correlation handle, never
#   a secret, and its absence is honest rather than invented.
#
# The two shapes stay where they are: the graph routes answer FLAT because that is
# the contract ``test_70`` and ``test_d1`` already pin, and verify/accept keep
# their ``"detail"`` nesting because that is what FastAPI does with an
# ``HTTPException``. What changes is that BOTH now carry ``error_code`` and
# ``message``, so one client rule reads every error on this surface.

#: The stable error vocabulary of this surface. Every code below is reachable and
#: is asserted by a test in ``tests/test_block10_reliability_and_errors.py``.
ERROR_CODES = frozenset(
    {
        # a dependency this composition requires was never supplied
        "missing_required_dependency",
        # the request body/path/query does not have the declared shape
        "invalid_request_schema",
        # the request was well-formed and conflicts with recorded server state
        "state_conflict",
        # approval is required and has not been granted (yet)
        "approval_refused",
        "approval_waiting",
        # the caller reached outside the project/run/action boundary
        "forbidden_action",
        # the server failed in a way that is not the caller's fault
        "internal_execution_error",
        # the command was authorized and really ran, and did not succeed
        "execution_error",
    }
)

#: The refusal codes this surface already published under ``code``, and the class
#: each of them belongs to. Kept as an explicit table so the classification is a
#: lookup rather than a guess, and so a new code cannot silently become
#: "operating_surface_error".
_KNOWN_CODE_CLASS = {
    # -- shape of the request / recorded plan
    "empty_graph": "invalid_request_schema",
    "graph_invalid": "invalid_request_schema",
    "invalid_request_schema": "invalid_request_schema",
    "graph_state_refused": "state_conflict",
    "unknown_parameter": "invalid_request_schema",
    "invalid_parameter_value": "invalid_request_schema",
    "parameter_widens_declared_limit": "forbidden_action",
    "cycle": "invalid_request_schema",
    "unknown_dependency": "invalid_request_schema",
    "idempotency_key_required": "invalid_request_schema",
    "declaration_required": "invalid_request_schema",
    "unknown_baseline": "state_conflict",
    "unknown_kind": "invalid_request_schema",
    "empty_statement": "invalid_request_schema",
    "kind_not_recordable": "forbidden_action",
    "unknown_run": "state_conflict",
    "unknown_memory": "state_conflict",
    "unsafe_identifier": "forbidden_action",
    "project_mismatch": "forbidden_action",
    # -- composition / dependency
    "step_graph_unavailable": "missing_required_dependency",
    "recovery_unavailable": "missing_required_dependency",
    "operating_surface_unavailable": "missing_required_dependency",
    "approval_policy_required": "missing_required_dependency",
    "approval_policy_missing": "missing_required_dependency",
    "surface_composition_invalid": "missing_required_dependency",
    "step_graph_composition_invalid": "missing_required_dependency",
    # -- approval
    "approval_waiting": "approval_waiting",
    "approval_rejected": "approval_refused",
    "approval_unresolved": "approval_waiting",
    "reapproval_required": "approval_waiting",
    # -- concurrency / state
    "graph_run_in_progress": "state_conflict",
    "already_recorded": "state_conflict",
    # -- execution
    "execution_error": "execution_error",
    "verification_refused": "state_conflict",
    "accept_refused": "state_conflict",
    "graph_failed": "internal_execution_error",
    "operating_surface_error": "state_conflict",
    "refused": "state_conflict",
}

#: HTTP status -> the code used when nothing more specific is known. This is a
#: *fallback*, never an override: a typed refusal keeps its own ``code``.
_STATUS_FALLBACK = {
    400: "invalid_request_schema",
    403: "forbidden_action",
    404: "state_conflict",
    409: "state_conflict",
    422: "invalid_request_schema",
    500: "internal_execution_error",
    503: "missing_required_dependency",
}

#: Refusal codes that are specifically about approval, so a client can route them
#: without parsing prose.
_APPROVAL_CODES = frozenset({"approval_waiting", "approval_rejected", "approval_refused"})

#: Exception class names that name a request-shape error, checked before any code,
#: because a malformed request document must be the caller's 400 even when the
#: exception type is one the project uses for persisted state too.
_SCHEMA_EXCEPTION_NAMES = frozenset({"StepGraphValidationError"})

#: A bounded, closed vocabulary of safe messages. The message a caller sees is
#: chosen from this table by CODE -- never sliced out of the exception, because an
#: exception's text can quote a configured path, a command line, or a secret-shaped
#: environment value.
_MESSAGES = {
    "missing_required_dependency": (
        "the server composition is missing a dependency this operation requires, so "
        "nothing was executed"
    ),
    "invalid_request_schema": (
        "the request does not have the shape this route declares, and nothing was "
        "executed"
    ),
    "state_conflict": (
        "the request conflicts with recorded server state, and nothing was executed"
    ),
    "approval_refused": "approval was refused, so nothing was executed",
    "approval_waiting": (
        "approval is required and has not been granted, so nothing was executed"
    ),
    "forbidden_action": (
        "the request is outside the project, run or action boundary this instance "
        "serves"
    ),
    "internal_execution_error": (
        "the server could not carry out the request and nothing was executed; the "
        "detail is in the server's own log"
    ),
    "execution_error": (
        "the action was authorized and really ran, and did not succeed; the recorded "
        "outcome is available"
    ),
    "refused": "the request was refused before anything changed",
    "graph_run_in_progress": (
        "the same graph run is already being driven in this process, so this request "
        "was refused and nothing was read or repeated"
    ),
    "operating_surface_error": (
        "the server could not carry out the request, and nothing was executed"
    ),
    "unsafe_identifier": (
        "the supplied identifier is not one this instance accepts, and nothing was "
        "executed"
    ),
    "unknown_run": "no run is recorded under that identifier",
    "step_graph_unavailable": (
        "no step-graph runner is composed on this instance, so host execution "
        "through this route is disabled"
    ),
    "recovery_unavailable": "recovery is not composed on this instance",
    "approval_policy_required": (
        "this composition states no approval policy, and a missing policy authorises "
        "no host execution"
    ),
    "approval_policy_missing": (
        "this composition does not supply the approval policy this operation "
        "requires, and a missing policy authorises no host execution"
    ),
    "surface_composition_invalid": (
        "the process was assembled with a composition this surface cannot use, so "
        "nothing was executed"
    ),
    "step_graph_composition_invalid": (
        "the step-graph composition does not satisfy the contract this surface "
        "requires, so nothing was executed"
    ),
    "empty_graph": "a step graph must declare at least one step, and nothing ran",
    "graph_invalid": (
        "the step graph was refused before anything ran; the reasons name the "
        "offending step or declaration"
    ),
    "graph_state_refused": (
        "the recorded attempt cannot be trusted for a resume, so nothing was resumed"
    ),
    "graph_failed": "the step graph could not be driven, and nothing was repeated",
}


def error_code_for(exc: BaseException, status: int) -> str:
    """The stable machine code for one error, from type and code -- never text.

    The order is: the exception's own ``code`` through the closed classification
    table, then its exception type for the two graph errors this project raises, then
    the HTTP status as a fallback. No branch reads ``str(exc)``.
    """

    if type(exc).__name__ in _SCHEMA_EXCEPTION_NAMES:
        return "invalid_request_schema"
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _KNOWN_CODE_CLASS:
        return _KNOWN_CODE_CLASS[code]
    if isinstance(exc, PermissionError):
        return "forbidden_action"
    if type(exc).__name__ == "StepGraphStateError":
        # A persisted record that cannot be trusted, or a step document with the
        # wrong shape: the second is repainted as 400 at the route that can tell
        # them apart (see ``_run_catching(..., errors_as_400=...)``). Reaching here
        # means the record is what could not be read, which is a state conflict.
        return "state_conflict"
    if isinstance(code, str) and code:
        # An unmapped code of our own is still published verbatim under ``code``;
        # the class is reported as a state conflict rather than invented.
        return "state_conflict"
    return _STATUS_FALLBACK.get(status, "state_conflict")


def message_for(error_code: str) -> str:
    """The bounded, sanitized message for one code. See ``_MESSAGES``."""

    return _MESSAGES.get(
        error_code, "the server refused the request, and nothing was executed"
    )


def _detail_envelope(status: int, detail: object) -> dict[str, Any]:
    """Turn one ``HTTPException.detail`` into the one envelope, whatever it was.

    A ``str`` detail (the shape every ``raise HTTPException(400, "...")`` in this
    codebase used) becomes the envelope's ``error`` field, so the plain string a
    route chose is preserved verbatim while the machine code and the safe message
    are added around it. A ``dict`` detail that is already an envelope is returned
    as-is. Anything else is wrapped without being interpreted.
    """

    if isinstance(detail, Mapping):
        enclosing = dict(detail)
        enclosing.setdefault("refused", True)
        enclosing.setdefault("executed", False)
        code = enclosing.get("code") or enclosing.get("error_code")
        if isinstance(code, str) and code:
            enclosing.setdefault("error_code", error_code_for_code(code))
            enclosing.setdefault(
                "message", message_for(str(enclosing["error_code"]))
            )
        else:
            enclosing.setdefault(
                "error_code", _STATUS_FALLBACK.get(status, "state_conflict")
            )
            enclosing.setdefault("message", message_for(str(enclosing["error_code"])))
        handle = current_request_id()
        if handle:
            enclosing.setdefault("request_id", handle)
        return enclosing
    handle = current_request_id()
    return {
        **error_body(
            error_code=_STATUS_FALLBACK.get(status, "state_conflict"),
            request_id=handle,
        ),
        "error": str(detail),
    }


def error_code_for_code(code: str) -> str:
    """The failure class of one already-published refusal code."""

    return _KNOWN_CODE_CLASS.get(code, _STATUS_FALLBACK.get(409, "state_conflict"))


def body_error_exception(message: str) -> Any:
    """The one 400 HTTPException for a malformed request body on this surface.

    Lives here, module level, because it is used both by the routes and by the
    app-level handler that repaints an ``HTTPException`` raised anywhere else into
    the same envelope. One definition, so the two cannot drift.
    """

    from fastapi import HTTPException

    return HTTPException(
        status_code=400,
        detail={
            **error_body(
                error_code="invalid_request_schema",
                code="invalid_request_schema",
            ),
            "error": message,
        },
    )


def http_exception_response(exc: Any) -> Any:
    """Serialize one ``HTTPException`` into the single error contract.

    MEASURED BEFORE THIS EXISTED: ``raise HTTPException(status_code=400,
    detail="'steps' must be a non-empty list")`` produced
    ``{"detail":"'steps' must be a non-empty list"}`` -- no machine code, no
    classification, and a different shape from every typed refusal on the same
    surface. This is the one place that shape is produced now.
    """

    from fastapi.responses import JSONResponse

    status = int(getattr(exc, "status_code", 500))
    detail = getattr(exc, "detail", "")
    content = _detail_envelope(status, detail)
    headers = getattr(exc, "headers", None)
    return JSONResponse(status_code=status, content={"detail": content}, headers=headers)


def error_body(
    *,
    error_code: str,
    code: str = "",
    configuration_error: bool = False,
    request_id: str = "",
) -> dict[str, Any]:
    """Build the one error envelope every route on this surface now returns.

    Backwards compatible by construction: ``refused``, ``code``,
    ``configuration_error`` and ``executed`` keep exactly the values they had, and
    ``error`` is deliberately **not** set here (it is the one field that used to
    carry exception text, and it is filled only where the surface already published
    a safe reason).

    ``request_id`` defaults to the correlation handle of the request being served,
    so a middleware that reads ``X-Request-ID`` is enough and no call site has to
    remember to forward it.
    """

    body: dict[str, Any] = {
        "refused": True,
        "error_code": error_code,
        "message": message_for(error_code),
        "executed": False,
    }
    if code:
        body["code"] = code
    if configuration_error:
        body["configuration_error"] = True
    handle = request_id or current_request_id()
    if handle:
        body["request_id"] = handle
    return body


def envelope_from_exception(
    exc: BaseException, *, request_id: str = ""
) -> tuple[int, dict[str, Any]]:
    """Classify one exception into ``(status, body)`` -- the common mechanism.

    This is the single place an unhandled error becomes an HTTP answer, and the
    only place that decides what a caller may learn about it. For our own typed
    refusals ``error`` carries the refusal's own message -- composed by the surface
    from a closed vocabulary plus the identifier the caller supplied -- while the
    human-readable ``message`` is chosen from the closed table by code. Arbitrary
    exception text never reaches a caller: an exception this function does not
    recognise is not typed, so it is answered by the sanitized application handler
    in :mod:`app.api.server` instead. The residual is stated rather than hidden: a
    typed refusal's message may echo a client-supplied identifier it names, which is
    why every such message is written to contain an identifier and nothing else.
    """

    handle = request_id or current_request_id()
    if isinstance(exc, PermissionError):
        return 403, {
            **error_body(
                error_code="forbidden_action",
                code="unsafe_identifier",
                request_id=handle,
            ),
            "error": str(exc),
        }
    if isinstance(exc, SurfaceCompositionError):
        return 500, {
            **error_body(
                error_code=error_code_for(exc, 500),
                code=exc.code,
                configuration_error=True,
                request_id=handle,
            ),
            "error": str(exc),
        }
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or not code:
        code = "operating_surface_error"
    return 409, {
        **error_body(
            error_code=error_code_for(exc, 409),
            code=code,
            request_id=handle,
        ),
        "error": str(exc),
    }

#: The shape the idempotency ledger accepts. Duplicated here deliberately: this is
#: a *request shape* check that belongs at the boundary, so a malformed key is
#: answered with a 400 naming the field rather than surfacing as an opaque ledger
#: refusal. The ledger remains the authority -- this only refuses earlier, and it
#: refuses strictly *more*, never less, than the ledger would.
_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")


def validate_idempotency_key(key: object) -> str:
    """Return the canonical key, or raise ``ValueError`` for a malformed one.

    A key is *input*, never authority: it selects which durable record a request
    belongs to. A malformed key is refused rather than coerced, because a silently
    rewritten key would silently change identity -- and a silently changed
    identity is how a repeat stops being refusable.
    """

    if not isinstance(key, str) or not key.strip():
        raise ValueError("'idempotency_key' is required")
    candidate = key.strip()
    if not _SAFE_IDEMPOTENCY_KEY.match(candidate):
        raise ValueError(
            "'idempotency_key' must be 8-200 characters of [A-Za-z0-9._:-]"
        )
    return candidate


def authorize_project(app: Any, project_id: object) -> str:
    """The one authorization check for the project boundary.

    ``project_id`` is optional at the HTTP layer: when it is absent the request is
    about the app's own project, which is what a single-project deployment means.
    When it IS supplied it must match exactly, because a mismatch means the caller
    is asking for a different project than the one this app is bound to -- and the
    answer to that is a refusal, not an empty list.

    Returns the authoritative project id. Raises ``PermissionError`` on a
    mismatch, which the caller converts into a 403.
    """

    authoritative = str(app.state.forge_project_id)
    if project_id is None or project_id == "":
        return authoritative
    if not isinstance(project_id, str) or project_id != authoritative:
        # The supplied value is deliberately NOT echoed back: reflecting a
        # caller-supplied string into an error body is how a response becomes a
        # probe for what exists.
        raise PermissionError(
            "this Forge API instance is not bound to the requested project"
        )


def _refusal(exc: BaseException, code: str) -> HTTPException:
    """Classify one host-execution-path failure into the right HTTP status.

    ``run_declared_verification`` and ``run_accepted_task`` used to wrap *every*
    exception into a 409 ``verification_refused`` / ``accept_refused``. A 409 means
    "the request was well-formed and conflicts with the server's own state", which
    is the right answer for a refusal -- and the wrong answer for a server-side
    configuration or programming error, because it blames the caller for a fault
    that is not theirs and invites a retry that cannot succeed. ``_error_response``
    already draws that distinction for the rest of the surface, so these two routes
    share it rather than re-deriving a flatter rule:

    * a ``SurfaceCompositionError`` becomes the documented 500
      ``configuration_error``, with its own machine code;
    * a ``PermissionError`` keeps its existing classification from
      ``_error_response``;
    * everything else remains the refusal the caller already contracts for, under
      the route's own ``code`` (``verification_refused`` / ``accept_refused``), so
      the refusal vocabulary is unchanged.

    No route status is guessed from the exception's *text*: only its type and its
    declared ``code`` are consulted.

    Block #10: this now delegates to the SAME :func:`envelope_from_exception` the
    graph routes use, so a client reads one contract on both paths -- the envelope
    is nested under ``"detail"`` here only because that is what FastAPI does with an
    ``HTTPException``, and the fields inside it are identical.
    """

    # Imported here for the same reason the route module does it: the refusal
    # classifier must be usable, and importable, without FastAPI installed.
    from fastapi import HTTPException

    if isinstance(exc, SurfaceCompositionError) or isinstance(exc, PermissionError):
        status, body = envelope_from_exception(exc)
        return HTTPException(status_code=status, detail=body)
    status, body = envelope_from_exception(exc)
    # The route's own refusal code is kept (``verification_refused`` /
    # ``accept_refused``): it tells the caller WHICH entry point refused, which the
    # generic class name cannot. ``error_code`` still names the failure class.
    body = {**body, "code": code, "error": type(exc).__name__}
    return HTTPException(status_code=status, detail=body)


def _error_response(exc: BaseException) -> tuple[int, dict[str, Any]]:
    """Map a typed refusal onto (status, body) without leaking exception text.

    A ``Refused`` is a 409 -- the request was well-formed and the server said no,
    which is a conflict with the server's own state, not a client syntax error. A
    ``GraphRunInFlight`` is also a 409 but carries its own code, because a caller
    must be able to tell "somebody else is driving this" from "this was refused".
    A ``PermissionError`` is a 403: the caller asked for something outside the
    boundary this instance serves.

    The classification itself lives in :func:`envelope_from_exception`; this is the
    compatibility shim that keeps the historical call sites and signature.
    """

    return envelope_from_exception(exc)


def register_operating_routes(app: Any, surface_factory: Any) -> None:
    """Attach the operating routes to ``app``.

    ``surface_factory`` builds the single ``OperatingSurface`` the app will use.
    It is constructed once, at app construction, so every request shares one view
    of the same workspace and the same run store.

    A service object that cannot support the operating surface -- a test double
    built with ``ForgeApiService.__new__`` and only one attribute, which several
    existing contract tests use to exercise a single route -- does **not** stop the
    app from being built. The operating routes are then registered as *unavailable*
    and answer 503, so those tests keep testing what they were written to test while
    nothing pretends the surface works. Fail-closed, not fail-open: the alternative
    (registering routes that would raise on the first request) reports the same
    failure later and less clearly.
    """

    from fastapi import Body, HTTPException, Query
    from fastapi.responses import FileResponse, JSONResponse

    surface = None
    unavailable_reason = ""
    try:
        surface = surface_factory()
    except (AttributeError, TypeError) as exc:
        unavailable_reason = (
            "the composed service does not support the operating surface "
            f"({type(exc).__name__})"
        )

    app.state.forge_operating_surface = surface
    app.state.forge_operating_unavailable = unavailable_reason
    # The project boundary is fixed at construction from the service's own
    # composition. It is never read from a request, so a caller cannot move it.
    app.state.forge_project_id = (
        surface.project_overview()["project_id"] if surface is not None else ""
    )

    def _require_surface() -> Any:
        if surface is None:
            raise HTTPException(
                status_code=503,
                detail={
                    **error_body(
                        error_code="missing_required_dependency",
                        code="operating_surface_unavailable",
                    ),
                    "error": (
                        "the operating surface is not available in this composition: "
                        f"{unavailable_reason}"
                    ),
                },
            )
        return surface

    def _guard(project_id: object) -> None:
        _require_surface()
        try:
            authorize_project(app, project_id)
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail={
                    **error_body(
                        error_code="forbidden_action",
                        code="unsafe_identifier",
                    ),
                    "error": str(exc),
                },
            ) from exc

    def _body_identifier(value: object, field: str) -> str:
        """Validate an identifier that arrived in a JSON body.

        A malformed body field is a 400, not a 403: the caller is not reaching
        across the boundary, they are sending something the contract does not
        accept. The allow-list itself is the surface's, so there is one rule.
        """

        try:
            return surface.validate_run_id(value)
        except PermissionError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    **error_body(
                        error_code="invalid_request_schema",
                        code="invalid_request_schema",
                    ),
                    "error": f"'{field}': {exc}",
                },
            ) from exc

    def _body_error(message: str) -> HTTPException:
        """The one 400 for a malformed request body on this surface.

        A thin wrapper over :func:`body_error_exception` -- kept as a nested name so
        every route in this function reads the same way, while the envelope itself
        has exactly one definition, module level, shared with the app-level handlers
        in ``app/api/server.py``.
        """

        return body_error_exception(message)

    def _request_id() -> str:
        """A correlation handle for this request, or ``""`` when there is none.

        The handle is the caller's own ``X-Request-ID`` header, read by the
        composition's middleware into a context variable. It is deliberately not
        generated here: an invented id would look like a correlation with the
        server's log while correlating with nothing. A request that supplies no
        header gets no ``request_id`` key, which is the honest answer.
        """

        return current_request_id()

    def _run_catching(call, *, errors_as_400: tuple[type, ...] = ()):
        """Run one surface call, converting typed refusals into HTTP responses.

        ``errors_as_400`` names exception classes that mean "the request the caller
        sent has the wrong SHAPE", as opposed to "the request conflicts with recorded
        state". A malformed step document raises ``StepGraphStateError`` -- the same
        class a damaged persisted record raises -- and the two must not be reported
        with the same status: the first is the caller's error (400) and the second is
        a conflict with server state (409). Naming the class per call site is how that
        distinction is made without guessing from the exception's text.
        """

        try:
            return call()
        except (
            GraphRunInFlight,
            Refused,
            SurfaceCompositionError,
            OperatingSurfaceError,
        ) as exc:
            status, body = _error_response(exc)
            return JSONResponse(status_code=status, content=body)
        except PermissionError as exc:
            status, body = _error_response(exc)
            return JSONResponse(status_code=status, content=body)
        except BaseException as exc:  # noqa: BLE001 - classified by a closed rule
            if not errors_as_400 or not isinstance(exc, errors_as_400):
                raise
            status, body = envelope_from_exception(exc)
            # The class name is published, as it already is for a typed refusal: it
            # is a stable identifier of the failing layer and carries no value.
            body = {**body, "error": type(exc).__name__}
            return JSONResponse(status_code=400, content=body)

    def _step_graph_state_error() -> type:
        """``StepGraphStateError``, imported lazily so this module needs no workflows.

        Returned as a one-tuple so a call site reads as the closed set it is, and so
        the route module keeps its property of importing no execution or workflow
        component at module import time.

        NOTE (open, Block #10): ``StepDefinition.from_dict`` raises this for a step
        with no ``action`` mapping, but ``StepAction``'s own validation raises the
        broader ``StepGraphError`` for an ``action`` whose ``declaration_id`` is
        missing, empty, or not a string. That second case therefore escapes this
        tuple and reaches the caller as an unhandled 500. Widening this tuple to
        ``StepGraphError`` was tried and REJECTED: it also catches the same class
        raised on the normal execution path, which made a valid graph stop executing
        (``executed: false``, artifact absent). The fix must narrow the catch to the
        document-parse step rather than widen it to the whole ``start_graph`` call.
        """

        from app.workflows.step_graph import StepGraphStateError

        return StepGraphStateError

    # -- read-only projections -----------------------------------------
    @app.get("/api/product/onboarding")
    def onboarding(project_id: str = Query(default="")) -> dict[str, Any]:
        """Project list, current stage, and which operations this build composes.

        Authorization: project boundary only. Read-only; starts nothing.
        """

        _guard(project_id)
        return surface.project_overview()

    @app.get("/api/product/requirements")
    def requirements(project_id: str = Query(default="")) -> dict[str, Any]:
        """Operator requirements and acceptance criteria, with expectations.

        Authorization: project boundary only. Read-only over the operator's own
        declarations; this route cannot add or relax a criterion.
        """

        _guard(project_id)
        return surface.requirements()

    @app.get("/api/product/plan")
    def plan(project_id: str = Query(default="")) -> dict[str, Any]:
        """The declared plan: requirements, declared actions, and their limits.

        Authorization: project boundary only. Reports the operator's declared
        ceiling; it grants nothing and starts nothing.
        """

        _guard(project_id)
        return surface.plan()

    @app.post("/api/product/plan")
    def plan_with_steps(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        """The same plan, with a proposed step/dependency list reflected back.

        Authorization: project boundary only. The steps are echoed as *proposed*
        dependencies; they are validated for real only when a run is requested,
        against the operator's declaration registry. Echoing them here cannot
        authorize them.
        """

        _guard(payload.get("project_id", ""))
        steps = payload.get("steps") or {}
        if not isinstance(steps, Mapping):
            raise HTTPException(status_code=400, detail="'steps' must be an object")
        return surface.plan(declarations=steps)

    @app.get("/api/product/understanding")
    def understanding(project_id: str = Query(default="")) -> dict[str, Any]:
        """A bounded scan of the authoritative workspace, summarized.

        Authorization: project boundary. The workspace is the server's own and is
        never taken from the request, so this route cannot be pointed at another
        directory. Descriptors only; secret-recognised files are never read.
        """

        _guard(project_id)
        return surface.understanding()

    @app.get("/api/product/memory")
    def memory(project_id: str = Query(default="")) -> dict[str, Any]:
        """Project Memory, with confirmed facts separated from hypotheses.

        Authorization: project boundary only. Reading memory grants nothing: the
        memory module imports no execution authority and no route derives an
        execution input from it.
        """

        _guard(project_id)
        return surface.memory_view()

    @app.get("/api/product/change-impact")
    def change_impact(
        project_id: str = Query(default=""),
        baseline: str = Query(default=""),
    ) -> Any:
        """Changed files linked to dependent facts, checks, and stale memory.

        Authorization: project boundary, plus an allow-listed baseline id that
        must be a record this server holds. Never returns file contents.

        Block #9 **extends this same report** rather than adding a parallel one: it
        now also carries each finding's ``classification`` (``CONFIRMED`` /
        ``PROBABLE`` / ``UNKNOWN``) with the ``basis`` that produced it, the stale
        structural facts with their invalidation reason, the dependent memory
        conclusions, the derived ``verification_plan``, and the ``evidence_graph``.
        Every Block #8 key keeps its Block #8 meaning; ``probable_rename_to`` still
        never becomes an established rename, and an ambiguous move is reported as
        ``ambiguous_move`` with ``move_candidates`` and is never auto-resolved.
        """

        _guard(project_id)
        return _run_catching(lambda: surface.change_impact(baseline=baseline))

    @app.get("/api/product/evidence-graph")
    def evidence_graph(
        project_id: str = Query(default=""),
        graph_run_id: str = Query(default=""),
    ) -> Any:
        """The traceability chain from the goal to a computed final status.

        Authorization: project boundary, plus the run boundary when a
        ``graph_run_id`` is named -- the id must be allow-listed and must be a
        record this server holds, so a foreign attempt is indistinguishable from an
        unknown one.

        Read-only. Every transition cites concrete identifiers, and a transition
        with no evidence is returned as ``status="MISSING"`` with a
        ``missing_code`` naming which link is absent. When a mandatory link is
        missing the final status is ``ACCEPTED_REFUSED_INCOMPLETE_TRACE`` rather
        than ``ACCEPTED``: a task is not accepted because an agent said so, and not
        while traceability is incomplete.
        """

        _guard(project_id)
        return _run_catching(lambda: surface.evidence_graph(graph_run_id=graph_run_id))

    @app.get("/api/product/verification-plan")
    def verification_plan(project_id: str = Query(default="")) -> Any:
        """The checks this change set implies, each in one of six explicit states.

        Authorization: project boundary only. Read-only. The plan is chosen from
        the operator's declared expectations, the scanner's own topology and the
        recorded step results -- no model is asked, and a check with no evidence is
        never reported as ``PASSED``.
        """

        _guard(project_id)
        return _run_catching(surface.verification_plan)

    @app.post("/api/product/stale-facts")
    def stale_facts(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
        """Structural facts that are no longer current, with their reason.

        Authorization: project boundary, plus an allow-listed baseline id. Read-only
        by default. ``invalidate=true`` additionally re-evaluates every file-pinned
        memory entry through the existing ``ProjectMemory.refresh``, which moves a
        changed, deleted or renamed source to STALE and appends a history entry --
        it never deletes anything and never promotes a claim.
        """

        _guard(payload.get("project_id", ""))
        raw_invalidate = payload.get("invalidate", False)
        if not isinstance(raw_invalidate, bool):
            # A truthy string is not consent. ``bool("false")``, ``bool("no")`` and
            # ``bool("0")`` are all True, so a caller who wrote one of them in the
            # JSON body -- believing it disabled the flag -- triggered a durable
            # invalidation of every file-pinned memory entry. Only a real JSON
            # boolean means what the caller wrote; anything else is refused.
            raise HTTPException(
                status_code=400, detail="'invalidate' must be a JSON boolean"
            )
        return _run_catching(
            lambda: surface.stale_facts(
                baseline=payload.get("baseline", ""),
                invalidate=raw_invalidate,
            )
        )

    @app.post("/api/product/repair-plan")
    def repair_plan(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
        """A bounded, no-progress-aware repair decision for a failed attempt.

        Authorization: project boundary, plus the run boundary through the
        surface's own allow-list. This route **plans**; it starts no repair.
        ``budget``, ``attempt_cap``, ``no_progress`` and the refusal path for an
        unknown side effect are all decided server-side from the durable ledger,
        and the response names the rule that stopped the loop.
        """

        _guard(payload.get("project_id", ""))
        return _run_catching(
            lambda: surface.repair_plan(
                payload.get("graph_run_id", ""),
                attempt_cap=payload.get("attempt_cap"),
                step_budget=payload.get("step_budget"),
                seconds_budget=payload.get("seconds_budget"),
                # THE NO-PROGRESS RULE NEEDS A PREVIOUS OBSERVATION, and the route is
                # where the caller supplies it. It was omitted, so the loop always saw
                # ``""``, never compared two markers, and ``NO_PROGRESS`` was
                # unreachable through HTTP while the module-level rule looked correct.
                previous_progress_marker=payload.get("previous_progress_marker", ""),
            )
        )

    @app.get("/api/product/runs")
    def runs(
        project_id: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        """Run history, newest first, each with its honest classification.

        Authorization: project boundary. Read-only: this reports persisted state
        and never resumes, retries, or re-executes anything.
        """

        _guard(project_id)
        return surface.run_history(limit=limit)

    @app.get("/api/product/runs/{run_id}")
    def run_detail(run_id: str, project_id: str = Query(default="")) -> Any:
        """One run's record, including its classification.

        Authorization: project boundary **and** the run boundary -- the id must be
        allow-listed and must already be recorded in this service's own store. An
        unrecorded id returns 404, so another deployment's run is indistinguishable
        from a run that never existed.
        """

        _guard(project_id)
        # One refusal shape for an unsafe identifier in a path: the same mapping the
        # other routes use, so a caller does not have to know which route it hit to
        # parse a refusal.
        try:
            surface.validate_run_id_for_path(run_id)
        except PermissionError as exc:
            status, body = _error_response(exc)
            return JSONResponse(status_code=status, content=body)
        record = surface.run_record(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown run")
        return record

    @app.get("/api/product/recovery")
    def recovery(project_id: str = Query(default="")) -> Any:
        """Unfinished attempts and what may happen next, per step.

        Authorization: project boundary. Read-only: the planner never executes and
        never writes a claim, so this route cannot resume anything.
        """

        _guard(project_id)
        return _run_catching(surface.recovery_scan)

    @app.get("/api/product/recovery/{graph_run_id}")
    def recovery_plan(graph_run_id: str, project_id: str = Query(default="")) -> Any:
        """The per-step decision for one recorded attempt.

        Authorization: project boundary plus the run boundary.
        """

        _guard(project_id)
        return _run_catching(lambda: surface.recovery_plan(graph_run_id))

    @app.get("/api/product/graph/{graph_run_id}")
    def graph_status(graph_run_id: str, project_id: str = Query(default="")) -> Any:
        """The recorded state of one step-graph run. Read-only.

        Authorization: project boundary plus the run boundary. This route never
        resumes a graph: continuing is a separate, explicit request.
        """

        _guard(project_id)
        return _run_catching(lambda: surface.graph_status(graph_run_id))

    @app.get("/api/product/approvals/{graph_run_id}")
    def approvals(graph_run_id: str, project_id: str = Query(default="")) -> Any:
        """Approval requests, with their parameters and consequences.

        Authorization: project boundary plus the run boundary. This route reports
        and cannot grant: approval stays the sole decision of the server-side
        ``ApprovalPolicy`` and its resolver. ``granted_by_server`` is always false
        for an outstanding request, and there is no request field that could make
        it true.
        """

        _guard(project_id)
        return _run_catching(lambda: surface.approvals(graph_run_id))

    # -- state-changing operations -------------------------------------
    @app.post("/api/product/goal")
    def set_goal(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
        """Record the operator's goal as a hypothesis and return the requirements.

        Authorization: project boundary. Writing the goal changes no execution
        input: the command a run uses still comes from the operator composition, so
        a goal can never select a command.
        """

        _guard(payload.get("project_id", ""))
        return _run_catching(lambda: surface.capture_goal(payload.get("goal", "")))

    @app.post("/api/product/memory")
    def record_memory(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
        """Record an operator statement at hypothesis-or-unknown strength.

        Authorization: project boundary. ``CONFIRMED_FACT`` and
        ``VERIFIED_CONCLUSION`` are refused with a machine code: a confirmed fact
        must cite evidence fact ids the understanding layer produced, which a
        request cannot supply. An assumption therefore cannot be promoted to a
        confirmed fact through this route.
        """

        _guard(payload.get("project_id", ""))
        return _run_catching(
            lambda: surface.record_hypothesis(
                payload.get("statement", ""),
                kind=str(payload.get("kind", "HYPOTHESIS")),
                subject=str(payload.get("subject", "") or ""),
            )
        )

    @app.post("/api/product/memory/refresh")
    def refresh_memory(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
        """Re-evaluate every pinned memory entry against the workspace as it is now.

        Authorization: project boundary. This is where a changed file invalidates a
        fact: the entry's pin carries the scanner's own fingerprint, and a mismatch
        moves it to STALE with a reason code naming the observation.
        """

        _guard(payload.get("project_id", ""))
        return _run_catching(surface.refresh_memory)

    @app.post("/api/product/memory/{memory_id}/invalidate")
    def invalidate_memory(
        memory_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> Any:
        """Invalidate one memory entry explicitly, keeping its whole history.

        Authorization: project boundary. The only mutation of an existing entry on
        this surface, and it can only weaken a claim -- never strengthen one.
        Explicit rather than a GET, because it writes.
        """

        _guard(payload.get("project_id", ""))
        return _run_catching(
            lambda: surface.invalidate_memory(memory_id, reason=payload.get("reason", ""))
        )

    @app.post("/api/product/baseline")
    def capture_baseline(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
        """Record the workspace as the reference point for future change reports.

        Authorization: project boundary. Writes one snapshot and starts nothing.
        Explicit rather than implicit, so "what changed since I looked" has a real
        answer instead of one the server redefines on each request.
        """

        _guard(payload.get("project_id", ""))
        return _run_catching(surface.capture_baseline)

    @app.post("/api/product/graph")
    def start_graph(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
        """Start one attempt of a step graph through the existing runner.

        Authorization: project boundary, an allow-listed ``graph_run_id``, and the
        runner's own checks. The caller composes *which operator-declared actions*
        run in what order; it cannot widen one. ``StepGraph.assert_valid`` refuses
        an unknown declaration, a widened parameter, a cycle, or a missing
        dependency before any ``RunScope`` is frozen.

        A repeat with a graph run id that already has a record executes nothing and
        returns the recorded attempt, so a dangerous action cannot be re-run by
        asking twice.

        This route is also where the "invalid step schema" defect was measured. A
        step document without an ``action`` mapping used to reach
        ``StepDefinition.from_dict`` inside the surface and raise
        ``StepGraphStateError``, which is NOT in the refusal tuple the route caught:
        the caller received ``500 Internal Server Error`` with a body of 21 bytes and
        no machine code. The two hardening checks below are the fix -- the route
        refuses a step that is not an object or has no ``action`` mapping BEFORE
        calling, with the common 400 envelope, and ``_run_catching`` additionally
        classifies ``StepGraphStateError`` as 400 (a request-shape error) while a
        damaged persisted record still answers 409 (a state conflict).
        """

        _guard(payload.get("project_id", ""))
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            raise _body_error("'steps' must be a non-empty list")
        for index, step in enumerate(steps):
            if not isinstance(step, Mapping):
                raise _body_error(
                    f"'steps[{index}]' must be an object with a 'step_id' and an "
                    "'action' mapping"
                )
            if not isinstance(step.get("action"), Mapping):
                raise _body_error(
                    f"'steps[{index}]' has no 'action' mapping; a step must name "
                    "{\"declaration_id\": ...}, never a bare 'declaration_id'"
                )
        _body_identifier(payload.get("graph_run_id", ""), "graph_run_id")
        return _run_catching(
            lambda: surface.start_graph(
                payload.get("graph_run_id", ""),
                steps,
                graph_id=str(payload.get("graph_id", "operator-plan")),
            ),
            errors_as_400=(_step_graph_state_error(),),
        )

    @app.post("/api/product/graph/{graph_run_id}/resume")
    def resume_graph(
        graph_run_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> Any:
        """Continue a recorded attempt through the existing runner.

        Authorization: project boundary, the run boundary, and the runner's own
        re-validation. The declared plan is read back from the record, so a restart
        needs no caller who remembers it. A step recorded as RUNNING is read back as
        UNKNOWN and is never repeated -- an unknown result is not permission to
        retry.
        """

        _guard(payload.get("project_id", ""))
        steps = payload.get("steps")
        if steps is not None and not isinstance(steps, list):
            raise _body_error("'steps' must be a list when supplied")
        for index, step in enumerate(steps or ()):
            if not isinstance(step, Mapping):
                raise _body_error(f"'steps[{index}]' must be an object")
            if not isinstance(step.get("action"), Mapping):
                raise _body_error(
                    f"'steps[{index}]' has no 'action' mapping; a step must name "
                    "{\"declaration_id\": ...}, never a bare 'declaration_id'"
                )
        return _run_catching(
            lambda: surface.resume_graph(
                graph_run_id,
                steps,
                graph_id=str(payload.get("graph_id", "operator-plan")),
            ),
            errors_as_400=(_step_graph_state_error(),),
        )

    @app.post("/api/product/verify")
    def declared_verification(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
        """Run one operator-declared verification execution.

        Authorization: project boundary, plus the declaration boundary -- the
        ``declaration_id`` must exist in the operator's registry, and nothing else
        about the request reaches the execution. A repeat under the same
        ``idempotency_key`` is refused rather than repeated; the key is required so
        that "asked twice" can never silently mean "ran twice".
        """

        _guard(payload.get("project_id", ""))
        declaration_id = payload.get("declaration_id")
        if not isinstance(declaration_id, str) or not declaration_id.strip():
            raise _body_error("'declaration_id' is required")
        try:
            key = validate_idempotency_key(payload.get("idempotency_key"))
        except ValueError as exc:
            raise _body_error(
                f"{exc}: a declared verification is a non-idempotent host "
                "action, and a repeat must be refusable"
            ) from exc
        try:
            result = surface.run_declared_verification(
                declaration_id.strip(), idempotency_key=key
            )
        except Exception as exc:  # noqa: BLE001 - classified, never leaked verbatim
            raise _refusal(exc, "verification_refused") from exc
        return result.to_dict()

    @app.post("/api/product/accept")
    def accept_cycle(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
        """Run one authorized action and decide real task acceptance.

        Authorization: project boundary, plus the declaration boundary, plus the
        existing accept cycle's own checks. The caller names a declaration; the
        criteria, expectations, command, workspace, profile, environment, timeout,
        tools and approval all come from the operator composition.

        ``idempotency_key`` is required for the same reason as declared
        verification: without it, "the client retried" and "the client asked for a
        second run" are indistinguishable, and a dangerous action must not be
        silently re-run. A repeat under the same key is refused by the existing
        ledger.
        """

        _guard(payload.get("project_id", ""))
        declaration_id = payload.get("declaration_id")
        if not isinstance(declaration_id, str) or not declaration_id.strip():
            raise _body_error("'declaration_id' is required")
        try:
            key = validate_idempotency_key(payload.get("idempotency_key"))
        except ValueError as exc:
            raise _body_error(
                f"{exc}: the accept cycle performs a non-idempotent host "
                "action, and a repeat must be refusable"
            ) from exc
        try:
            result = surface.run_accepted_task(
                declaration_id.strip(), idempotency_key=key
            )
        except Exception as exc:  # noqa: BLE001
            raise _refusal(exc, "accept_refused") from exc
        return result.to_dict()

    # -- the UI itself -------------------------------------------------
    # An explicit allow-list of three files, never a directory mount: a static
    # mount under a path a client influences is how a served UI becomes a file
    # read. There is no path parameter here at all.
    assets = getattr(app.state, "forge_ui_assets", {}) or {}

    @app.get("/")
    def ui_index() -> Any:
        """Serve the operating console. No authorization: it contains no data.

        The page is a shell. Everything it displays is fetched from the routes
        above, each of which performs its own check, so serving the shell to
        anyone who can reach the port discloses nothing.
        """

        if "index.html" not in assets:
            raise HTTPException(status_code=404, detail="UI assets are not installed")
        return FileResponse(assets["index.html"], media_type="text/html; charset=utf-8")

    @app.get("/app.css")
    def ui_css() -> Any:
        if "app.css" not in assets:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(assets["app.css"], media_type="text/css; charset=utf-8")

    @app.get("/app.js")
    def ui_js() -> Any:
        if "app.js" not in assets:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(assets["app.js"], media_type="text/javascript; charset=utf-8")
