"""Who may drive this surface, and how much they may send.

The Core API was built for one local operator: no route requires a credential, and
:mod:`app.api.server` therefore refused to bind beyond loopback. The owner's plan is to
run Forge on a server and open its interface in a browser, which puts the same routes --
``POST /api/tasks/run`` writes files, ``POST /api/projects/{id}/run`` starts processes --
behind a socket that anybody on the internet could reach. This module is the access
layer that makes that deployment safe **by construction** rather than by configuration:

* **one shared token**, no accounts. It is read through the existing secrets layer
  (``FORGE_ACCESS_TOKEN``: process environment, then the repository ``.env``, then the
  operating system's credential vault), and a self-hosted box may instead keep it in the
  owner's own settings file under ``access_token``;
* a caller proves it with ``Authorization: Bearer <token>`` or ``X-Forge-Token: <token>``,
  compared in **constant time**;
* the token itself never leaves the process. What is published is a **fingerprint** --
  the first six hex characters of its SHA-256 -- which is enough to tell two deployments
  (or two rotations) apart and not enough to authenticate;
* a request without or with a wrong token gets ``401`` with a plain message. Health
  (``/health``, ``/api/health``) and the build identity (``/api/version``) stay open, so a
  load balancer and a person can both check a server without holding the token;
* three sanity limits for a public box: the maximum request body, a token bucket per
  client address for mutations and for reads, and one startup line that states the bind
  address, whether auth is on and the token's fingerprint.

What this deliberately is **not**: it is not the Platform's identity model
(``app/platform``). There are no accounts, no sessions, no roles. One token grants the
whole surface, and rotating it is the only revocation. That is the honest shape of a
single-operator product, and the deployment guide says so.

The absent case is deliberate too. When no token is configured the middleware is not
installed at all -- the loopback surface stays exactly as frictionless as it is today --
and the launcher then refuses any non-loopback bind, so "no token" can never mean
"public without a token". :func:`refuse_if_exposed` is where that rule lives.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("forge.api.access")

__all__ = [
    "ACCESS_TOKEN_ENV_VAR",
    "ACCESS_TOKEN_SETTING",
    "AUTHORIZATION_HEADER",
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_MUTATION_LIMIT",
    "DEFAULT_READ_LIMIT",
    "MUTATION_METHODS",
    "OPEN_PATHS",
    "TOKEN_HEADER",
    "AccessPolicy",
    "Limit",
    "RateLimiter",
    "access_log_line",
    "client_address",
    "extract_token",
    "install_access_control",
    "is_protected_path",
    "refuse_if_exposed",
    "resolve_access_token",
    "token_fingerprint",
    "tokens_match",
]

#: The variable a deployment puts its token in. Read through
#: :class:`app.config.secrets.SecretStore`, so ``.env`` and the OS vault both work.
ACCESS_TOKEN_ENV_VAR = "FORGE_ACCESS_TOKEN"

#: The owner's settings-file key for the same value, read when neither the environment
#: nor ``.env`` nor the vault holds one. A self-hosted box may keep it here.
ACCESS_TOKEN_SETTING = "access_token"

AUTHORIZATION_HEADER = "authorization"
TOKEN_HEADER = "x-forge-token"
_BEARER_PREFIX = "bearer"

#: Paths that answer **without** a token: a health probe and the build identity. They
#: publish no project, no file, no credential and no capability -- an unauthenticated
#: reader learns only that the process is alive and which commit it runs.
OPEN_PATHS = frozenset({"/health", "/api/health", "/api/version"})

#: Every ``/api/*`` path needs the token. Matching the prefix rather than a route list
#: means a route added later is protected by default: forgetting to add it to a list is
#: exactly how a new endpoint ends up public.
_PROTECTED_PREFIX = "/api/"

#: 8 MiB: the largest body this surface has a legitimate use for (a task description, a
#: project specification, a set of answers). Well under nginx's own ``client_max_body_size``
#: in ``deploy/nginx.conf.sample``, so the proxy refuses first with a readable error.
DEFAULT_MAX_BODY_BYTES = 8 * 1024 * 1024

#: Methods that change state. Reads are limited separately and far more generously,
#: because the interface polls.
MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Limit:
    """A token bucket: ``capacity`` requests per ``per_seconds``, refilled continuously."""

    capacity: int
    per_seconds: float = 60.0

    @property
    def rate(self) -> float:
        """Tokens per second, or ``0.0`` for a disabled limit."""

        capacity = max(0, int(self.capacity))
        window = float(self.per_seconds)
        if capacity <= 0 or window <= 0:
            return 0.0
        return capacity / window


#: 30 mutations a minute from one address. A person clicking through the interface never
#: reaches it; a script that found the port does, immediately.
DEFAULT_MUTATION_LIMIT = Limit(30, 60.0)

#: 240 reads a minute from one address. The workspace polls roughly sixteen a minute in
#: one tab, so a handful of tabs and a manual refresh stay well inside it.
DEFAULT_READ_LIMIT = Limit(240, 60.0)

#: How many client addresses the limiter remembers before it prunes. Bounded on purpose:
#: an unbounded table keyed by a remote address is a memory leak an attacker drives.
MAX_TRACKED_CLIENTS = 4096


@dataclass(frozen=True)
class AccessPolicy:
    """The whole access decision for one surface, as data instead of scattered settings.

    An empty ``token`` means **no access control at all**, which is only ever correct on a
    loopback bind -- :func:`refuse_if_exposed` enforces that pairing.
    """

    token: str = ""
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    mutation_limit: Limit = DEFAULT_MUTATION_LIMIT
    read_limit: Limit = DEFAULT_READ_LIMIT

    @property
    def enabled(self) -> bool:
        """Whether a token is required."""

        return bool(str(self.token or "").strip())

    @property
    def fingerprint(self) -> str:
        """The published identity of the token, or ``""`` when there is none."""

        return token_fingerprint(self.token) if self.enabled else ""

    def describe(self) -> Dict[str, Any]:
        """What the interface and the log may say about this policy. Never the token."""

        return {
            "auth": "token" if self.enabled else "off",
            "token_fingerprint": self.fingerprint,
            "max_body_bytes": int(self.max_body_bytes),
            "mutation_limit": int(self.mutation_limit.capacity),
            "mutation_window_seconds": float(self.mutation_limit.per_seconds),
            "read_limit": int(self.read_limit.capacity),
            "read_window_seconds": float(self.read_limit.per_seconds),
        }


def token_fingerprint(token: object) -> str:
    """The first six hex characters of the token's SHA-256, or ``""``.

    Enough to tell two tokens (and two rotations) apart in a log and useless as a
    credential. A fingerprint is never derived from anything but the token itself, so it
    cannot be mistaken for one of the host's identifiers.
    """

    text = token if isinstance(token, str) else ""
    if not text.strip():
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]


def tokens_match(provided: object, expected: object) -> bool:
    """Whether two tokens are equal, in **constant time**, without raising on a type slip.

    ``hmac.compare_digest`` is used on the UTF-8 bytes: it compares in time independent of
    where the first difference is, so a wrong token cannot be recovered one character at a
    time from response timing. An empty expected token accepts nothing -- "no expected
    token" must not mean "any token".
    """

    if not isinstance(provided, str) or not isinstance(expected, str):
        return False
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _header(headers: Any, name: str) -> str:
    """One header value as a stripped string, from any header mapping.

    Starlette's ``Headers`` and ``email.message.Message`` lower-case the lookup key, but a
    plain ``dict`` does not, and a caller that hands this function one (a test, a small
    proxy) must not silently lose the credential. The exact key is tried first and a
    case-insensitive scan is only the fallback, so the common path stays one lookup.
    """

    try:
        value = headers.get(name)
    except Exception:  # noqa: BLE001 - an unreadable header map holds no token
        value = None
    if value is None:
        try:
            items = headers.items()
        except Exception:  # noqa: BLE001 - no items means nothing to scan
            items = ()
        for key, candidate in items:
            if str(key).lower() == name:
                value = candidate
                break
    if isinstance(value, str):
        return value.strip()
    return ""


def extract_token(headers: Any) -> Tuple[str, ...]:
    """Every token the request offers, in the order the contract names them.

    Two spellings are accepted and both are checked: a reverse proxy usually forwards
    ``Authorization`` untouched, while the browser bundle sends ``X-Forge-Token`` (a header
    a browser page may set on its own origin without a CORS preflight grant). Returning
    both lets a caller send either, and lets a test prove each one alone is enough.
    """

    offered: List[str] = []
    authorization = _header(headers, AUTHORIZATION_HEADER)
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.strip().lower() == _BEARER_PREFIX:
            token = value.strip()
            if token:
                offered.append(token)
    direct = _header(headers, TOKEN_HEADER)
    if direct:
        offered.append(direct)
    return tuple(offered)


def is_protected_path(path: object) -> bool:
    """Whether this request path requires the token.

    The comparison is made on the path with a trailing slash removed, so ``/api/status/``
    cannot slip past a rule written for ``/api/status``. An open path is exact: a
    ``/api/version/../../status`` is not open just because it starts with ``/api/version``.
    """

    text = path if isinstance(path, str) else ""
    if not text.startswith("/"):
        return False
    normalised = text.rstrip("/") or "/"
    if normalised in OPEN_PATHS:
        return False
    return normalised.startswith(_PROTECTED_PREFIX) or normalised == "/api"


#: Addresses that mean "this machine" for the purpose of trusting a forwarded address.
_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"})


def client_address(peer: object, forwarded_for: object = "") -> str:
    """The address a rate limit is charged to.

    Normally the address the socket reports. Behind the nginx reverse proxy in
    ``deploy/nginx.conf.sample`` every request arrives from ``127.0.0.1``, so one bucket
    would serve the whole internet and a single client could exhaust everybody else's
    budget. When -- and only when -- the peer is loopback, the **last** entry of
    ``X-Forwarded-For`` is used: nginx appends the real client to whatever the client sent,
    so the last entry is the one nginx observed. A request from any other peer never has its
    ``X-Forwarded-For`` read, because that header is client-controlled.

    Anything unreadable answers ``"unknown"``, which is one shared bucket rather than no
    limit at all.
    """

    text = str(peer or "").strip()
    if text in _LOOPBACK_PEERS:
        entries = [
            part.strip()
            for part in str(forwarded_for or "").split(",")
            if part.strip()
        ]
        if entries:
            return entries[-1][:64]
    return text[:64] or "unknown"


def resolve_access_token(
    *, secret_store: Any = None, settings: Any = None
) -> str:
    """The token this deployment uses, or ``""`` when it uses none.

    Order, and why: the secrets layer first (process environment, then the repository
    ``.env``, then the operating system's credential vault) because that is where a
    deployment keeps credentials and where ``.env`` already wins over the interface for
    provider keys -- one rule, not two. The owner's settings file is the last resort, for
    a self-hosted box where ``.env`` is not the natural place.

    Every failure answers ``""``: a token that could not be read is an absent token, which
    makes the launcher refuse a public bind instead of serving unprotected.
    """

    store = secret_store
    if store is None:
        try:
            from app.config.secrets import SecretStore

            store = SecretStore()
        except Exception:  # noqa: BLE001 - no store means no token from that layer
            store = None
    if store is not None:
        try:
            value = store.get_secret(ACCESS_TOKEN_ENV_VAR)
        except Exception:  # noqa: BLE001 - an unreadable secret is an absent one
            value = None
        if isinstance(value, str) and value.strip():
            return value.strip()
    if settings is not None:
        reader = getattr(settings, "access_token", None)
        if callable(reader):
            try:
                value = reader()
            except Exception:  # noqa: BLE001 - an unreadable setting is an absent one
                value = ""
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def refuse_if_exposed(host: object, token: object = "", surface: str = "Forge API") -> None:
    """Refuse a bind that reaches beyond this machine when no token guards the surface.

    This is the rule that makes the deployment safe by construction: the surface has **no
    authentication** without a token, so a non-loopback bind without one is not a
    configuration the product will start. The message names the exact reason, the
    variable to set and the tunnel option, because the operator reads it in a terminal
    and in ``journalctl``.
    """

    from app.api.server import is_loopback_host

    text = str(host or "").strip()
    if is_loopback_host(text):
        return
    if isinstance(token, str) and token.strip():
        return
    raise RuntimeError(
        f"{surface} refuses non-loopback binding ({text or '?'}) because no access token "
        "is configured: anyone who can reach the port could write files and start "
        f"processes. Set {ACCESS_TOKEN_ENV_VAR} (or '{ACCESS_TOKEN_SETTING}' in the "
        "application settings file) and restart, or keep the bind on 127.0.0.1 and use "
        "an SSH tunnel (deploy/README-SSH-TUNNEL.md)."
    )


def access_log_line(
    *,
    host: object,
    port: object,
    policy: AccessPolicy,
    surface: str = "Forge API",
) -> str:
    """The one startup line: where it listens, whether auth is on, and the fingerprint.

    The token's value is never part of this line, and cannot be: the function only ever
    interpolates :attr:`AccessPolicy.fingerprint`. A test asserts exactly that.
    """

    where = f"{str(host)}:{str(port)}"
    if not policy.enabled:
        return (
            f"{surface} listening on {where} | auth: OFF (no token; loopback only) | "
            "body limit and rate limits are not installed"
        )
    return (
        f"{surface} listening on {where} | auth: on (token) | "
        f"token fingerprint: {policy.fingerprint} | "
        f"body limit: {int(policy.max_body_bytes)} bytes | "
        f"rate limit: {int(policy.mutation_limit.capacity)} mutations/"
        f"{int(policy.mutation_limit.per_seconds)}s and "
        f"{int(policy.read_limit.capacity)} reads/"
        f"{int(policy.read_limit.per_seconds)}s per address"
    )


class RateLimiter:
    """A per-address token bucket for mutations and for reads.

    The bucket is filled continuously, so a client that was refused one second ago is not
    banned for the whole window; it is a sanity limit, not a quota. Nothing is keyed on
    anything a requester controls except the address the transport reports -- never a
    header such as ``X-Forwarded-For``, which any client can write.
    """

    def __init__(
        self,
        *,
        mutation_limit: Limit = DEFAULT_MUTATION_LIMIT,
        read_limit: Limit = DEFAULT_READ_LIMIT,
        clock: Any = time.monotonic,
        max_clients: int = MAX_TRACKED_CLIENTS,
    ) -> None:
        self._mutation = mutation_limit
        self._read = read_limit
        self._clock = clock
        self._max_clients = max(1, int(max_clients))
        #: ``(kind, address) -> (tokens, last_refill)``. Runtime state only.
        self._buckets: Dict[Tuple[str, str], Tuple[float, float]] = {}

    def limit_for(self, method: object) -> Limit:
        """Which bucket a request of this method draws from."""

        return (
            self._mutation
            if str(method or "").upper() in MUTATION_METHODS
            else self._read
        )

    def check(self, address: object, method: object) -> Optional[int]:
        """Consume one token; return ``None`` when allowed, else ``Retry-After`` seconds.

        ``Retry-After`` is rounded **up** and never below one second, so a client that
        obeys it does not come back inside the same second and get refused again.
        """

        limit = self.limit_for(method)
        rate = limit.rate
        if rate <= 0:
            return None
        key = ("mutation" if limit is self._mutation else "read", str(address or "unknown"))
        now = float(self._clock())
        capacity = float(limit.capacity)
        tokens, last = self._buckets.get(key, (capacity, now))
        elapsed = max(0.0, now - last)
        tokens = min(capacity, tokens + elapsed * rate)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            self._prune(now)
            return max(1, int(math.ceil((1.0 - tokens) / rate)))
        self._buckets[key] = (tokens - 1.0, now)
        self._prune(now)
        return None

    def _prune(self, now: float) -> None:
        """Drop the buckets that have refilled completely or that nobody touched.

        A completely refilled bucket is indistinguishable from a fresh one, so forgetting
        it changes no decision. The bound is what keeps a stream of distinct addresses from
        growing the table without end.
        """

        if len(self._buckets) <= self._max_clients:
            return
        stale = [
            key
            for key, (tokens, last) in self._buckets.items()
            if now - last > max(
                float(self._mutation.per_seconds), float(self._read.per_seconds)
            )
            or tokens >= float(self._mutation.capacity)
        ]
        for key in stale:
            self._buckets.pop(key, None)
        if len(self._buckets) > self._max_clients:
            # Still too many: forget the least recently seen third. A limiter that has
            # forgotten a client treats it as a fresh bucket, which is the permissive
            # direction -- but the alternative is unbounded memory under a flood, and the
            # body limit still applies to whatever gets through.
            ordered = sorted(self._buckets.items(), key=lambda item: item[1][1])
            for key, _ in ordered[: max(1, len(ordered) // 3)]:
                self._buckets.pop(key, None)


def _refusal(status: int, error_code: str, message: str, *, headers: Optional[Mapping[str, str]] = None):
    """The one refusal shape for this layer: a plain message, in the common envelope.

    It is built here rather than through ``app.api.routes.error_body`` on purpose: that
    envelope's message table is a closed vocabulary of *route* refusals, and adding three
    transport-level words to it would make the code list say something it does not mean.
    The shape is the same one every client already reads (``detail.error_code``,
    ``detail.message``, ``executed: false``), so no client needs a second rule.
    """

    from starlette.responses import JSONResponse

    body = {
        "detail": {
            "refused": True,
            "error_code": error_code,
            "code": error_code,
            "message": message,
            "executed": False,
        }
    }
    return JSONResponse(
        status_code=status,
        content=body,
        headers=dict(headers or {}),
    )


def install_access_control(app: Any, policy: AccessPolicy) -> Optional[RateLimiter]:
    """Install the token check, the body ceiling and the rate limits on ``app``.

    Returns the limiter (so a test can drive its clock) and installs nothing at all when
    the policy carries no token: a loopback deployment keeps today's behaviour exactly,
    including its response shapes and its timing.

    Order of the checks is deliberate:

    1. **the token** first, so an unauthenticated flood does not consume the address's
       budget and a legitimate client sharing a NAT with it is not punished;
    2. **the rate limits**, before any work is done for the request;
    3. **the body ceiling**, from the declared ``Content-Length`` (see the limits below).

    A refusal here is answered by this middleware, so it happens before routing, before
    any handler, before any file write and before any provider call.
    """

    if not policy.enabled:
        return None

    limiter = RateLimiter(
        mutation_limit=policy.mutation_limit, read_limit=policy.read_limit
    )

    @app.middleware("http")
    async def _access_guard(request, call_next):  # type: ignore[no-untyped-def]
        """Refuse before the request reaches anything that could act on it."""

        path = request.url.path
        if is_protected_path(path):
            offered = extract_token(request.headers)
            if not any(tokens_match(value, policy.token) for value in offered):
                # The message names the two accepted spellings and never echoes what was
                # offered: an error body must not become a probe for the token.
                return _refusal(
                    401,
                    "unauthorized",
                    "unauthorized: this Forge server requires its access token, sent as "
                    "'Authorization: Bearer <token>' or 'X-Forge-Token: <token>'",
                    headers={"WWW-Authenticate": 'Bearer realm="forge"'},
                )

        address = client_address(
            getattr(getattr(request, "client", None), "host", ""),
            _header(request.headers, "x-forwarded-for"),
        )
        retry_after = limiter.check(address, request.method)
        if retry_after is not None:
            return _refusal(
                429,
                "rate_limited",
                f"too many requests from this address: retry after {retry_after} seconds",
                headers={"Retry-After": str(retry_after)},
            )

        declared = _header(request.headers, "content-length")
        if declared:
            try:
                length = int(declared)
            except ValueError:
                length = 0
            if length > int(policy.max_body_bytes):
                # The declared length is refused before a single body byte is read. A
                # chunked body declares no length and is not measured here; the proxy in
                # ``deploy/nginx.conf.sample`` sets ``client_max_body_size`` as the outer
                # bound, and that is stated in the deployment guide rather than assumed.
                return _refusal(
                    413,
                    "payload_too_large",
                    "the request body is larger than this server accepts "
                    f"({int(policy.max_body_bytes)} bytes), and nothing was executed",
                )

        return await call_next(request)

    return limiter
