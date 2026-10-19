"""Sessions: opaque tokens, stored only as hashes, verified server-side.

Scope of this module (Phase 7, PR-1): the domain and the service for issuing, verifying and revoking a
session. It deliberately contains **no** HTTP endpoint, **no** login/logout route, **no** password check and
**no** tenant mechanism of its own — the repository implementation is tenant-scoped through the existing
``_TenantBound`` session, exactly like ``PostgresProjectRepository``.

Three properties this module is responsible for:

* a token is 256 bits from ``secrets`` and is handed to the caller **once**; only its SHA-256 hash is ever
  given to storage, and the raw token is never part of a stored record, a log line or an error message;
* absolute lifetime and idle timeout are decided in one place, and both are checked on every verify;
* a storage failure is a **refusal**, never an admission — infrastructure errors must not become permission.

The preliminary lifetimes below are placeholders pending the owner's testing and final agreement (D7).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Protocol, Sequence, runtime_checkable

# ------------------------------------------------------------------------------------------------
# Preliminary values, centralised on purpose. D7: 12 hours absolute, 30 minutes idle — provisional
# until the owner tests them on the target machine and agrees final numbers.
# ------------------------------------------------------------------------------------------------
SESSION_ABSOLUTE_LIFETIME: timedelta = timedelta(hours=12)
SESSION_IDLE_TIMEOUT: timedelta = timedelta(minutes=30)

# 32 bytes = 256 bits of entropy, as required for an opaque session token.
TOKEN_ENTROPY_BYTES: int = 32

# The hash algorithm is part of the storage contract: storage never sees a raw token.
TOKEN_HASH_ALGORITHM: str = "sha256"


class SessionStorageUnavailable(RuntimeError):
    """Raised by a repository when the session store cannot be read or written.

    The service converts this into a refusal. It must never be caught and treated as "no session found"
    in a way that admits a caller, and it must never be swallowed into success.
    """


def generate_token() -> str:
    """A fresh opaque token with at least 256 bits of entropy."""

    return secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def hash_token(token: str) -> str:
    """The only form of a token that storage is allowed to see."""

    return hashlib.new(TOKEN_HASH_ALGORITHM, token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionRecord:
    """A stored session. Deliberately has **no** field for a raw token."""

    session_id: str
    organization_id: str
    subject_id: str
    issued_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    revoked_at: Optional[datetime] = None

    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def is_idle(self, now: datetime) -> bool:
        return now - self.last_seen_at >= SESSION_IDLE_TIMEOUT


@runtime_checkable
class SessionRepository(Protocol):
    """Tenant-scoped storage for sessions.

    Note what is **absent**: no method takes an ``organization_id``. The tenant comes from the transaction
    the repository is bound to (``_TenantBound``), so a caller cannot name a tenant and have it honoured.
    ``find_by_token_hash`` receives only a hash, never a raw token.
    """

    async def create(
        self,
        session_id: str,
        subject_id: str,
        token_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> SessionRecord:
        """Store a new session in the current tenant."""

    async def find_by_token_hash(self, token_hash: str) -> Optional[SessionRecord]:
        """Return the session with this token hash in the current tenant, or ``None``."""

    async def touch(self, session_id: str, seen_at: datetime) -> None:
        """Record activity for the idle timeout."""

    async def revoke_subject(self, subject_id: str, revoked_at: datetime) -> int:
        """Revoke every live session of one subject; returns how many were revoked."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class IssuedSession:
    """What ``issue`` returns: the raw token **once**, plus the stored record (which has no token)."""

    token: str
    record: SessionRecord


class SessionService:
    """Issue, verify and revoke sessions. Holds no tenant state of its own."""

    def __init__(
        self,
        repository_factory: Callable[[], SessionRepository],
        *,
        clock: Callable[[], datetime] = _utc_now,
        absolute_lifetime: timedelta = SESSION_ABSOLUTE_LIFETIME,
        idle_timeout: timedelta = SESSION_IDLE_TIMEOUT,
    ) -> None:
        # A factory, not an instance: the repository is bound to one transaction, so a long-lived service
        # must obtain a repository per unit of work rather than capture one.
        self._repository_factory = repository_factory
        self._clock = clock
        self._absolute_lifetime = absolute_lifetime
        self._idle_timeout = idle_timeout

    @property
    def absolute_lifetime(self) -> timedelta:
        return self._absolute_lifetime

    @property
    def idle_timeout(self) -> timedelta:
        return self._idle_timeout

    def idle_exceeded(self, record: SessionRecord, now: datetime) -> bool:
        """Idle is measured against the service's own timeout, not a module constant a test cannot move."""

        return now - record.last_seen_at >= self._idle_timeout

    async def issue(self, subject_id: str, *, session_id: str) -> IssuedSession:
        """Create a session and return the raw token to the caller, once.

        The raw token is not written to the record, not returned anywhere else, and not logged. Errors from
        storage propagate as :class:`SessionStorageUnavailable` — issuing must not appear to succeed when the
        store did not accept the row.
        """

        if not subject_id:
            raise ValueError("a session needs a subject")
        if not session_id:
            raise ValueError("a session needs an identifier")
        token = generate_token()
        issued_at = self._clock()
        expires_at = issued_at + self._absolute_lifetime
        try:
            record = await self._repository_factory().create(
                session_id, subject_id, hash_token(token), issued_at, expires_at
            )
        except Exception as exc:  # noqa: BLE001 - normalised to one storage failure
            raise SessionStorageUnavailable(type(exc).__name__) from exc
        return IssuedSession(token=token, record=record)

    async def verify(self, token: Optional[str]) -> Optional[SessionRecord]:
        """Return the stored session when the token is valid, otherwise ``None``.

        Every negative case is a plain ``None`` — absent, malformed, unknown, expired, idle, revoked — so a
        caller cannot distinguish them, and no reason string is produced that could leak an identifier. A
        storage failure raises :class:`SessionStorageUnavailable` rather than returning ``None``: the caller
        must turn that into a refusal, never into an admission.
        """

        if not token or not isinstance(token, str):
            return None
        try:
            record = await self._repository_factory().find_by_token_hash(hash_token(token))
        except Exception as exc:  # noqa: BLE001 - normalised to one storage failure
            raise SessionStorageUnavailable(type(exc).__name__) from exc
        if record is None:
            return None
        now = self._clock()
        if record.is_revoked() or record.is_expired(now) or self.idle_exceeded(record, now):
            return None
        try:
            await self._repository_factory().touch(record.session_id, now)
        except Exception:  # noqa: BLE001 - a failed touch must not admit anyone
            raise SessionStorageUnavailable("touch failed") from None
        return record

    async def revoke_subject(self, subject_id: str) -> int:
        """Revoke every live session of one subject (used by an operator password reset, later PR)."""

        if not subject_id:
            raise ValueError("revoking needs a subject")
        try:
            return await self._repository_factory().revoke_subject(subject_id, self._clock())
        except Exception as exc:  # noqa: BLE001 - normalised to one storage failure
            raise SessionStorageUnavailable(type(exc).__name__) from exc


def redact(text: str, tokens: Sequence[str]) -> str:
    """A helper for callers that must log: any known raw token is replaced before the line is emitted.

    Provided so that a caller never has to remember the rule; the service itself keeps no logger and writes
    nothing, which is why the raw token cannot leave through this module.
    """

    result = text
    for token in tokens:
        if token:
            result = result.replace(token, "<redacted-session-token>")
    return result
