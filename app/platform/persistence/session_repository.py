"""PostgreSQL session repository.

Storage for the session protocol declared in ``app.platform.application.sessions``. Two rules are structural
here, not stylistic:

* the tenant is read from the transaction (``_TenantBound._tenant()``) and written explicitly as
  ``organization_id`` — no operation accepts an organization from a caller, so a client cannot name a tenant;
* only a token **hash** is ever stored or queried. There is no method that takes a raw token and no logging
  in this module, so a raw token cannot reach the database or a log line through the repository.

Row-level security in migration ``0016_sessions.sql`` is the second line of defence for the same property.
"""

from __future__ import annotations


from typing import Optional



from app.platform.application.sessions import SessionRecord
from app.platform.persistence.repositories import _TenantBound

from app.platform.persistence.mapper import (
    api_key_from_row,
    as_uuid,
    membership_from_row,
    optional_text,
    organization_from_row,
    project_from_row,
    provider_account_from_row,
    require_mapping,
    run_record_from_row,
    scopes_to_array,
    usage_record_from_row,
    user_from_row,
)


# The columns the domain record is built from; kept in one place so SELECT and RETURNING cannot drift apart.
SESSION_COLUMNS = (
    "id, organization_id, subject_id, issued_at, expires_at, last_seen_at, revoked_at"
)


def _to_session_record(row: "Record") -> SessionRecord:
    """Map a row to the domain record. The raw token is not a column, so it cannot appear here."""

    return SessionRecord(
        session_id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        subject_id=str(row["subject_id"]),
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        last_seen_at=row["last_seen_at"],
        revoked_at=row["revoked_at"],
    )


class PostgresSessionRepository(_TenantBound):
    """Tenant-scoped sessions, bound to the surrounding unit of work like every other repository."""

    async def create(
        self,
        session_id: str,
        subject_id: str,
        token_hash: str,
        issued_at,
        expires_at,
    ) -> SessionRecord:
        """Store one session in the current tenant and return the record (never the token).

        The row starts with ``last_seen_at`` equal to ``issued_at`` and no revocation: a fresh session has just
        been seen.
        """

        row = await self._session.fetchrow(
            "INSERT INTO sessions (id, organization_id, subject_id, token_hash, issued_at, expires_at, "
            "last_seen_at) VALUES ($1, $2, $3, $4, $5, $6, $5) "
            f"RETURNING {SESSION_COLUMNS}",
            as_uuid(session_id, "Session.id"),
            as_uuid(self._tenant(), "organization_id"),
            as_uuid(subject_id, "Session.subject_id"),
            token_hash,
            issued_at,
            expires_at,
        )
        return _to_session_record(row)

    async def find_by_token_hash(self, token_hash: str) -> Optional[SessionRecord]:
        """The lookup behind every authenticated request: one hash in, at most one row out."""

        row = await self._session.fetchrow(
            f"SELECT {SESSION_COLUMNS} FROM sessions WHERE token_hash = $1",
            token_hash,
        )
        return None if row is None else _to_session_record(row)

    async def touch(self, session_id: str, seen_at) -> None:
        """Record activity so the idle timeout is measured from the last real use."""

        await self._session.execute(
            "UPDATE sessions SET last_seen_at = $2 WHERE id = $1",
            as_uuid(session_id, "Session.id"),
            seen_at,
        )

    async def revoke_subject(self, subject_id: str, revoked_at) -> int:
        """Revoke every live session of one subject in the current tenant; returns how many were revoked."""

        status = await self._session.execute(
            "UPDATE sessions SET revoked_at = $2 WHERE subject_id = $1 AND revoked_at IS NULL",
            as_uuid(subject_id, "Session.subject_id"),
            revoked_at,
        )
        try:
            return int(str(status).rsplit(" ", 1)[-1])
        except ValueError:  # pragma: no cover - a driver that reports no count is not an error here
            return 0


__all__ = ["PostgresSessionRepository", "SESSION_COLUMNS"]
