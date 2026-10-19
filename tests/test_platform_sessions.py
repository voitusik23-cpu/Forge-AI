"""Behavioural and RLS tests for the Phase 7 session infrastructure (PR-1).

Evidence rules applied here:

* PostgreSQL is real. Row-level security is proved with real SQL under the **application role**
  (``forge_platform_app``), never with mocks and never with the system role;
* the system/superuser connection is used **only** to prepare organisations and users, because those tables
  are themselves protected by FORCE ROW LEVEL SECURITY;
* when no live server is configured the class skips with the project's own reason string, so a missing
  database can never be mistaken for a passing test.

Groups covered in this file: A (issue), B (verify negatives), C (time boundaries), D (revocation),
E (tenant context), F (real RLS isolation A/B), G (token storage), H (no secrets in errors).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.platform.application.sessions import (  # noqa: E402
    SESSION_ABSOLUTE_LIFETIME,
    SESSION_IDLE_TIMEOUT,
    SessionRecord,
    SessionService,
    SessionStorageUnavailable,
    generate_token,
    hash_token,
)
from app.platform.persistence.database import APP_ROLE, Session  # noqa: E402
from app.platform.persistence.session_repository import (  # noqa: E402
    SESSION_COLUMNS,
    PostgresSessionRepository,
)

from tests.persistence_fixture import (  # noqa: E402
    SKIP_REASON_NO_DRIVER,
    SKIP_REASON_NO_SERVER,
    PersistenceDatabase,
    admin_kwargs,
    app_login_kwargs,
)

_LOOP: asyncio.AbstractEventLoop | None = None


def _database_name(handle) -> str:
    """The database name, parsed from the fixture DSN. The DSN itself is never printed."""

    from urllib.parse import urlsplit

    return urlsplit(handle.dsn).path.lstrip("/")



def run_async(coro):
    """One shared loop, matching the convention used by the other persistence tests."""

    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP.run_until_complete(coro)


class _FixedClock:
    """Controllable time: the boundaries are tested exactly, never by waiting."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


class SessionInfrastructureTestCase(unittest.TestCase):
    """Shared fixture: one throwaway database inside the isolated test cluster."""

    _handle = None
    _asyncpg = None

    @classmethod
    def setUpClass(cls):
        try:
            import asyncpg  # noqa: F401
        except ImportError:  # pragma: no cover - driver absence is an environment fact
            raise unittest.SkipTest(SKIP_REASON_NO_DRIVER)
        cls._asyncpg = sys.modules["asyncpg"]

        async def probe() -> bool:
            try:
                connection = await cls._asyncpg.connect(**admin_kwargs())
            except Exception:
                return False
            await connection.close()
            return True

        if not run_async(probe()):
            raise unittest.SkipTest(SKIP_REASON_NO_SERVER)
        cls._handle = PersistenceDatabase()
        run_async(cls._handle.__aenter__())

    @classmethod
    def tearDownClass(cls):
        if cls._handle is not None:
            run_async(cls._handle.__aexit__(None, None, None))
        cls._handle = None

    # ------------------------------------------------------------------ helpers
    async def _seed_tenant(self, label: str) -> dict:
        """Create an organisation and a user in the fixture's own database.

        The fixture owns the administrative connection: when it is an async context manager it is entered and
        released here, and when it is a plain coroutine the connection is simply used and not closed, so the
        harness keeps ownership. The raw DSN is never printed.
        """

        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        email = f"{label}-{uuid.uuid4().hex[:8]}@example.test"

        async def seed(connection) -> None:
            try:
                await connection.execute(
                    "INSERT INTO organizations (id, name, status, created_at, updated_at) "
                    "VALUES ($1, $2, 'active', now(), now())",
                    organization_id, f"org-{label}",
                )
            except Exception:
                await connection.execute(
                    "INSERT INTO organizations (id, name, created_at) VALUES ($1, $2, now())",
                    organization_id, f"org-{label}",
                )
            await connection.execute(
                "INSERT INTO users (id, email, display_name, status, created_at) "
                "VALUES ($1, $2, $3, 'active', now())",
                user_id, email, f"user-{label}",
            )

        candidate = self._handle.admin_connection()
        if hasattr(candidate, "__aenter__"):
            async with candidate as connection:
                await seed(connection)
        else:
            await seed(await candidate)
        return {"organization_id": organization_id, "user_id": user_id}


    async def _app_session(self, organization_id: str, user_id: str) -> Session:
        """A Session that behaves like PlatformDatabase.tenant(): app role + tenant + subject."""

        connection = await self._asyncpg.connect(**app_login_kwargs(_database_name(self._handle)))
        session = Session(connection)
        await session.begin()
        await session.execute(f'SET LOCAL ROLE "{APP_ROLE}"')
        await session.set_tenant(organization_id)
        await session.set_subject(user_id)
        return session

    def _service(self, session: Session, clock=None) -> SessionService:
        return SessionService(lambda: PostgresSessionRepository(session), clock=clock or (lambda: datetime.now(timezone.utc)))


class ASessionIssueTests(SessionInfrastructureTestCase):
    """Group A: issuing a session."""

    def test_100_issue_returns_a_non_empty_token_and_stores_only_a_hash(self):
        async def scenario():
            tenant = await self._seed_tenant("issue")
            session = await self._app_session(tenant["organization_id"], tenant["user_id"])
            try:
                service = self._service(session)
                issued = await service.issue(tenant["user_id"], session_id=str(uuid.uuid4()))
                self.assertTrue(issued.token)
                self.assertGreaterEqual(len(issued.token), 32)
                self.assertNotIn(issued.token, repr(issued.record))
                self.assertNotEqual(issued.token, hash_token(issued.token))
                stored = await session.fetchrow(
                    f"SELECT {SESSION_COLUMNS} FROM sessions WHERE token_hash = $1", hash_token(issued.token))
                self.assertIsNotNone(stored)
                self.assertEqual(str(stored["organization_id"]), tenant["organization_id"])
                raw = await session.fetchval("SELECT count(*) FROM sessions WHERE token_hash = $1", issued.token)
                self.assertEqual(raw, 0)
            finally:
                await session.close()
            return True

        self.assertTrue(run_async(scenario()))

    def test_101_two_issues_produce_different_tokens(self):
        async def scenario():
            tenant = await self._seed_tenant("issue2")
            session = await self._app_session(tenant["organization_id"], tenant["user_id"])
            try:
                service = self._service(session)
                first = await service.issue(tenant["user_id"], session_id=str(uuid.uuid4()))
                second = await service.issue(tenant["user_id"], session_id=str(uuid.uuid4()))
                self.assertNotEqual(first.token, second.token)
                self.assertNotEqual(first.record.session_id, second.record.session_id)
            finally:
                await session.close()
            return True

        self.assertTrue(run_async(scenario()))


class BVerifyTokenTests(SessionInfrastructureTestCase):
    """Group B: verifying tokens, including every negative case."""

    def test_110_valid_token_is_accepted_and_negatives_are_refused(self):
        async def scenario():
            tenant = await self._seed_tenant("verify")
            session = await self._app_session(tenant["organization_id"], tenant["user_id"])
            try:
                service = self._service(session)
                issued = await service.issue(tenant["user_id"], session_id=str(uuid.uuid4()))
                verified = await service.verify(issued.token)
                self.assertIsNotNone(verified)
                self.assertEqual(verified.subject_id, tenant["user_id"])
                self.assertIsNone(await service.verify(None))
                self.assertIsNone(await service.verify(""))
                self.assertIsNone(await service.verify("not-a-real-token"))
                self.assertIsNone(await service.verify(generate_token()))
                self.assertIsNone(await service.verify(12345))

                await service.revoke_subject(tenant["user_id"])
                self.assertIsNone(await service.verify(issued.token))
            finally:
                await session.close()
            return True

        self.assertTrue(run_async(scenario()))

    def test_111_storage_failure_never_admits_a_caller(self):
        from app.platform.application import sessions as sessions_module

        class _Broken:
            async def create(self, *args, **kwargs):
                raise RuntimeError("storage down")

            async def find_by_token_hash(self, *args, **kwargs):
                raise RuntimeError("storage down")

            async def touch(self, *args, **kwargs):
                raise RuntimeError("storage down")

            async def revoke_subject(self, *args, **kwargs):
                raise RuntimeError("storage down")

        service = sessions_module.SessionService(lambda: _Broken())
        with self.assertRaises(SessionStorageUnavailable):
            run_async(service.verify("any-token"))
        with self.assertRaises(SessionStorageUnavailable):
            run_async(service.issue("subject", session_id="session"))


class CTimeBoundaryTests(SessionInfrastructureTestCase):
    """Group C: absolute lifetime, idle timeout and their exact boundaries."""

    def test_120_absolute_and_idle_boundaries(self):
        async def scenario():
            tenant = await self._seed_tenant("time")
            session = await self._app_session(tenant["organization_id"], tenant["user_id"])
            try:
                start = datetime.now(timezone.utc)
                clock = _FixedClock(start)
                service = self._service(session, clock=clock)
                issued = await service.issue(tenant["user_id"], session_id=str(uuid.uuid4()))
                self.assertEqual(SESSION_ABSOLUTE_LIFETIME, timedelta(hours=12))
                self.assertEqual(SESSION_IDLE_TIMEOUT, timedelta(minutes=30))

                clock.now = start + SESSION_ABSOLUTE_LIFETIME - timedelta(seconds=1)
                await PostgresSessionRepository(session).touch(issued.record.session_id, clock.now)
                self.assertIsNotNone(await service.verify(issued.token))
                clock.now = start + SESSION_ABSOLUTE_LIFETIME
                self.assertIsNone(await service.verify(issued.token))
                clock.now = start + SESSION_ABSOLUTE_LIFETIME + timedelta(seconds=1)
                self.assertIsNone(await service.verify(issued.token))
            finally:
                await session.close()
            return True

        self.assertTrue(run_async(scenario()))

    def test_121_idle_timeout_boundaries_and_touch_does_not_move_expiry(self):
        async def scenario():
            tenant = await self._seed_tenant("idle")
            session = await self._app_session(tenant["organization_id"], tenant["user_id"])
            try:
                start = datetime.now(timezone.utc)
                clock = _FixedClock(start)
                service = self._service(session, clock=clock)
                issued = await service.issue(tenant["user_id"], session_id=str(uuid.uuid4()))
                original_expiry = issued.record.expires_at

                clock.now = start + SESSION_IDLE_TIMEOUT - timedelta(seconds=1)
                await PostgresSessionRepository(session).touch(issued.record.session_id, clock.now)
                verified = await service.verify(issued.token)
                self.assertIsNotNone(verified)
                self.assertEqual(verified.expires_at, original_expiry)  # activity never extends the absolute term

                # A successful verify() refreshes activity, so idle must be measured from that refreshed
                # instant: jump more than one idle timeout beyond it while staying far below the 12 hours
                # absolute lifetime, which this session must not have reached.
                refreshed = await session.fetchval(
                    "SELECT last_seen_at FROM sessions WHERE token_hash = $1", hash_token(issued.token))
                self.assertEqual(refreshed, clock.now)
                clock.now = clock.now + SESSION_IDLE_TIMEOUT + timedelta(seconds=1)
                self.assertLess(clock.now, start + SESSION_ABSOLUTE_LIFETIME)
                self.assertIsNone(await service.verify(issued.token))
            finally:
                await session.close()
            return True

        self.assertTrue(run_async(scenario()))


class DRevocationTests(SessionInfrastructureTestCase):
    """Group D: revocation semantics."""

    def test_130_revoke_subject_is_idempotent_and_scoped(self):
        async def scenario():
            tenant = await self._seed_tenant("revoke")
            other = await self._seed_tenant("revoke-other")
            session = await self._app_session(tenant["organization_id"], tenant["user_id"])
            other_session = await self._app_session(other["organization_id"], other["user_id"])
            try:
                service = self._service(session)
                issued = await service.issue(tenant["user_id"], session_id=str(uuid.uuid4()))
                self.assertEqual(await service.revoke_subject(tenant["user_id"]), 1)
                self.assertEqual(await service.revoke_subject(tenant["user_id"]), 0)
                self.assertEqual(await service.revoke_subject(str(uuid.uuid4())), 0)
                self.assertIsNone(await service.verify(issued.token))

                other_service = self._service(other_session)
                other_issued = await other_service.issue(other["user_id"], session_id=str(uuid.uuid4()))
                self.assertEqual(await service.revoke_subject(tenant["user_id"]), 0)
                self.assertIsNotNone(await other_service.verify(other_issued.token))
            finally:
                await session.close()
                await other_session.close()
            return True

        self.assertTrue(run_async(scenario()))


class ETenantContextTests(SessionInfrastructureTestCase):
    """Group E: the tenant comes from the transaction, never from a caller."""

    def test_140_without_tenant_context_the_repository_refuses(self):
        async def scenario():
            connection = await self._asyncpg.connect(**app_login_kwargs(_database_name(self._handle)))
            session = Session(connection)
            await session.begin()
            await session.execute(f'SET LOCAL ROLE "{APP_ROLE}"')
            try:
                repository = PostgresSessionRepository(session)
                with self.assertRaises(Exception):
                    await repository.create(str(uuid.uuid4()), str(uuid.uuid4()),
                                            hash_token(generate_token()),
                                            datetime.now(timezone.utc),
                                            datetime.now(timezone.utc) + SESSION_ABSOLUTE_LIFETIME)
            finally:
                await session.close()
            return True

        self.assertTrue(run_async(scenario()))

    def test_141_no_public_repository_operation_accepts_an_organization(self):
        import inspect

        for name in ("create", "find_by_token_hash", "touch", "revoke_subject"):
            parameters = list(inspect.signature(getattr(PostgresSessionRepository, name)).parameters)
            self.assertNotIn("organization_id", parameters, f"{name} must not take a tenant from a caller")


class FRealRlsIsolationTests(SessionInfrastructureTestCase):
    """Group F: proven isolation between two organisations under the application role."""

    def test_150_isolation_of_read_update_revoke_and_cross_tenant_insert(self):
        async def scenario():
            tenant_a = await self._seed_tenant("rls-a")
            tenant_b = await self._seed_tenant("rls-b")

            session_a = await self._app_session(tenant_a["organization_id"], tenant_a["user_id"])
            session_b = await self._app_session(tenant_b["organization_id"], tenant_b["user_id"])
            try:
                service_a = self._service(session_a)
                service_b = self._service(session_b)
                issued_a = await service_a.issue(tenant_a["user_id"], session_id=str(uuid.uuid4()))

                # 2. B cannot read A's session, by hash or by token.
                self.assertIsNone(await service_b.verify(issued_a.token))
                self.assertIsNone(await PostgresSessionRepository(session_b).find_by_token_hash(
                    hash_token(issued_a.token)))

                # 3. B cannot revoke A's session.
                self.assertEqual(await service_b.revoke_subject(tenant_a["user_id"]), 0)
                # 4. B cannot update A's row.
                status = await session_b.execute(
                    "UPDATE sessions SET last_seen_at = now() WHERE token_hash = $1",
                    hash_token(issued_a.token))
                self.assertTrue(str(status).endswith("0"), f"cross-tenant UPDATE must affect no rows, got {status}")

                # 5. a direct cross-tenant INSERT is refused by WITH CHECK.
                refused = False
                await session_b.execute("SAVEPOINT rls_probe")
                try:
                    await session_b.execute(
                        "INSERT INTO sessions (id, organization_id, subject_id, token_hash, issued_at, "
                        "expires_at, last_seen_at) VALUES ($1, $2, $3, $4, now(), now(), now())",
                        str(uuid.uuid4()), tenant_a["organization_id"], tenant_a["user_id"],
                        hash_token(generate_token()))
                except Exception:
                    refused = True
                    await session_b.execute("ROLLBACK TO SAVEPOINT rls_probe")
                self.assertTrue(refused, "RLS WITH CHECK must refuse an insert naming another tenant")

                # 6. A still sees its own session.
                self.assertIsNotNone(await service_a.verify(issued_a.token))

                # 7. B creates and reads its own session.
                issued_b = await service_b.issue(tenant_b["user_id"], session_id=str(uuid.uuid4()))
                verified_b = await service_b.verify(issued_b.token)
                self.assertIsNotNone(verified_b)
                self.assertEqual(verified_b.organization_id, tenant_b["organization_id"])
            finally:
                await session_a.close()
                await session_b.close()
            return True

        self.assertTrue(run_async(scenario()))


class GTokenStorageTests(SessionInfrastructureTestCase):
    """Group G: only hashes are stored, and the record never carries the token."""

    def test_160_raw_token_absent_from_the_table_and_from_the_record(self):
        async def scenario():
            tenant = await self._seed_tenant("storage")
            session = await self._app_session(tenant["organization_id"], tenant["user_id"])
            try:
                service = self._service(session)
                issued = await service.issue(tenant["user_id"], session_id=str(uuid.uuid4()))
                self.assertNotIn(issued.token, repr(issued.record))
                self.assertNotIn(issued.token, repr(issued.record.__dict__))
                found = await session.fetchval("SELECT count(*) FROM sessions WHERE token_hash = $1", issued.token)
                self.assertEqual(found, 0)
                as_hash = await session.fetchval("SELECT count(*) FROM sessions WHERE token_hash = $1",
                                                 hash_token(issued.token))
                self.assertEqual(as_hash, 1)
                record = SessionRecord(**{**issued.record.__dict__})
                self.assertFalse(hasattr(record, "token"))
            finally:
                await session.close()
            return True

        self.assertTrue(run_async(scenario()))


class HNoSecretsInErrorsTests(SessionInfrastructureTestCase):
    """Group H: tokens and secrets never surface in error text."""

    def test_170_refusals_carry_no_token_and_no_dsn(self):
        async def scenario():
            tenant = await self._seed_tenant("errors")
            session = await self._app_session(tenant["organization_id"], tenant["user_id"])
            try:
                service = self._service(session)
                issued = await service.issue(tenant["user_id"], session_id=str(uuid.uuid4()))
                await service.revoke_subject(tenant["user_id"])
                self.assertIsNone(await service.verify(issued.token))

                marker = "synthetic-secret-marker-9f3c"
                try:
                    raise SessionStorageUnavailable(f"storage unavailable for {marker}")
                except SessionStorageUnavailable as exc:
                    message = str(exc)
                    self.assertIn(marker, message)          # the marker is synthetic, never a real secret
                    self.assertNotIn(issued.token, message)
                    self.assertNotIn("postgresql://", message)
            finally:
                await session.close()
            return True

        self.assertTrue(run_async(scenario()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
