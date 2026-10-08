"""Fixtures for the Platform persistence tests.

These tests exercise real PostgreSQL. The Platform layer's guarantees are
row-level security, ``FORCE`` row-level security, partial unique indexes, composite
foreign keys, and role boundaries -- none of which exist in SQLite, so there is
nothing to simulate and no substitute to fall back on.

Two environment facts make the fixture slightly more involved than the schema
tests:

* the persistence layer **connects as a non-owner, non-superuser role** and
  refuses to build a pool otherwise, so the fixture creates a login role that is a
  member of ``forge_platform_app`` and connects as it;
* a database is created, migrated, and dropped per test class, because the
  migrations create cluster-wide roles and each class wants a clean schema.

Configuration matches the schema tests: ``FORGE_PLATFORM_TEST_DSN`` or the
component variables, plus ``FORGE_PLATFORM_TEST_APP_LOGIN_PASSWORD``.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DSN_ENV = "FORGE_PLATFORM_TEST_DSN"

# THE TWO LOGIN ROLES ARE THE POINT, NOT A TEST CONVENIENCE.
#
# A deployment must serve tenant traffic and system traffic through different
# logins: if one login were a member of both platform roles, a connection handling
# a tenant request could step into the system scope, and the boundary would exist
# only in the policy text. The fixture therefore provisions two logins, each a
# member of exactly one platform role, and the tests reach the system scope through
# the second one. Neither login is a superuser or bypasses row-level security.
PERSISTENCE_LOGIN_ROLE = "forge_persistence_login"
PERSISTENCE_SYSTEM_LOGIN_ROLE = "forge_persistence_system_login"
PERSISTENCE_LOGIN_PASSWORD = os.environ.get(
    "FORGE_PLATFORM_TEST_APP_LOGIN_PASSWORD", "persistence-test-login"
)

SKIP_REASON_NO_DRIVER = (
    "asyncpg is not installed; the Platform persistence tests require a real "
    "PostgreSQL driver"
)
SKIP_REASON_NO_SERVER = (
    "no live PostgreSQL server configured or reachable; set FORGE_PLATFORM_TEST_DSN "
    "to run the Platform persistence tests. Row-level security, FORCE RLS, partial "
    "unique indexes, and composite foreign keys are PostgreSQL semantics and are "
    "deliberately NOT simulated in SQLite"
)


def asyncpg_module():
    try:
        import asyncpg  # noqa: PLC0415 - optional, test-only dependency
    except ImportError:
        return None
    return asyncpg


def admin_kwargs() -> dict:
    """Connection arguments for the migrating administrator."""

    dsn = os.environ.get(DSN_ENV)
    if dsn:
        return {"dsn": dsn}
    return {
        "host": os.environ.get("FORGE_PLATFORM_TEST_HOST", "127.0.0.1"),
        "port": int(os.environ.get("FORGE_PLATFORM_TEST_PORT", "5432")),
        "user": os.environ.get("FORGE_PLATFORM_TEST_USER", "postgres"),
        "password": os.environ.get("FORGE_PLATFORM_TEST_PASSWORD") or None,
        "database": os.environ.get("FORGE_PLATFORM_TEST_DATABASE", "postgres"),
    }


def _split(dsn: str) -> dict:
    """Turn a DSN into keyword arguments, so components can be overridden."""

    from urllib.parse import unquote, urlparse

    parsed = urlparse(dsn)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 5432,
        "user": unquote(parsed.username) if parsed.username else "postgres",
        "password": unquote(parsed.password) if parsed.password else None,
        "database": (parsed.path or "/postgres").lstrip("/") or "postgres",
    }


def connection_parts() -> dict:
    """Keyword connection arguments, whichever configuration form was used."""

    configured = admin_kwargs()
    if "dsn" in configured:
        return _split(configured["dsn"])
    return dict(configured)


def _dsn_for(database: str, user: str) -> str:
    parts = connection_parts()
    password = PERSISTENCE_LOGIN_PASSWORD
    return (
        f"postgresql://{user}:{password}@{parts['host']}:{parts['port']}"
        f"/{database}"
    )


def app_login_kwargs(database: str) -> dict:
    """Connection arguments for the tenant login role."""

    parts = connection_parts()
    parts["user"] = PERSISTENCE_LOGIN_ROLE
    parts["password"] = PERSISTENCE_LOGIN_PASSWORD
    parts["database"] = database
    return parts


def app_dsn(database: str) -> str:
    """A DSN the persistence layer can be pointed at, as the tenant login."""

    return _dsn_for(database, PERSISTENCE_LOGIN_ROLE)


def system_dsn(database: str) -> str:
    """A DSN for the server-only login, which is a member of the system role.

    A separate login, never the tenant one: that separation is the boundary the
    security work established.
    """

    return _dsn_for(database, PERSISTENCE_SYSTEM_LOGIN_ROLE)


async def live_postgres():
    """A connection to the maintenance database, or None when unreachable."""

    asyncpg = asyncpg_module()
    if asyncpg is None:
        return None
    try:
        return await asyncpg.connect(**admin_kwargs(), timeout=5)
    except Exception:  # noqa: BLE001 - any failure means "no live server"
        return None


def new_database_name() -> str:
    return "forge_persistence_test_" + uuid.uuid4().hex[:12]


class PersistenceDatabase:
    """A migrated throwaway database plus a role that can connect to it.

    ``__aenter__`` provisions the role, creates the database, applies the
    migrations, and returns a handle. ``__aexit__`` drops both. Nothing is left
    behind, so a failing test cannot poison a later run.
    """

    def __init__(self) -> None:
        self.name: str | None = None
        self._maintenance = None

    async def __aenter__(self) -> "PersistenceDatabase":
        asyncpg = asyncpg_module()
        if asyncpg is None:
            raise RuntimeError(SKIP_REASON_NO_DRIVER)

        self._maintenance = await asyncpg.connect(**admin_kwargs(), timeout=10)
        await self._provision_login_role()

        self.name = new_database_name()
        await self._maintenance.execute(f'CREATE DATABASE "{self.name}"')

        target = dict(connection_parts())
        target["database"] = self.name
        connection = await asyncpg.connect(**target, timeout=10)
        try:
            from app.platform.persistence import apply_migrations

            await apply_migrations(connection)
        finally:
            await connection.close()

        # The roles are cluster-wide; the grants on the new database's tables were
        # issued by the migration, because it runs in this database. The login role
        # only needs its membership, which is also cluster-wide.
        await self._maintenance.execute(
            f"GRANT CONNECT ON DATABASE \"{self.name}\" TO {PERSISTENCE_LOGIN_ROLE}"
        )
        await self._maintenance.execute(
            f"GRANT CONNECT ON DATABASE \"{self.name}\" TO "
            f"{PERSISTENCE_SYSTEM_LOGIN_ROLE}"
        )
        return self

    async def _provision_login_role(self) -> None:
        """Create one login per platform scope, each a member of exactly one role.

        Deliberately not one login in both: that configuration is the thing the
        security boundary forbids, and a test fixture that used it would prove less
        than it appears to.

        Role DDL touches the shared cluster catalogue, so two processes running these
        tests against one cluster contend on the same role row and the loser gets
        ``tuple concurrently updated``. That is a transient catalogue conflict, not a
        defect, so each statement is retried a bounded number of times. Any other
        error still surfaces immediately.
        """

        assert self._maintenance is not None
        for login, parent in (
            (PERSISTENCE_LOGIN_ROLE, "forge_platform_app"),
            (PERSISTENCE_SYSTEM_LOGIN_ROLE, "forge_platform_system"),
        ):
            await self._retry_catalog_conflict(
                f"DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
                f"'{login}') THEN "
                f"CREATE ROLE {login} LOGIN PASSWORD "
                f"'{PERSISTENCE_LOGIN_PASSWORD}' NOSUPERUSER NOCREATEDB "
                f"NOCREATEROLE NOBYPASSRLS; "
                f"END IF; END $$;"
            )
            await self._retry_catalog_conflict(
                f"ALTER ROLE {login} LOGIN NOSUPERUSER NOBYPASSRLS"
            )
            await self._retry_catalog_conflict(f"GRANT {parent} TO {login}")

    async def _retry_catalog_conflict(self, statement: str, attempts: int = 6) -> None:
        for attempt in range(1, attempts + 1):
            try:
                await self._maintenance.execute(statement)
                return
            except Exception as exc:  # noqa: BLE001
                if "tuple concurrently updated" not in str(exc) or attempt == attempts:
                    raise
                await asyncio.sleep(0.25 * attempt)

    async def admin_connection(self):
        """A connection as the administrator, for seeding and inspection."""

        asyncpg = asyncpg_module()
        target = dict(connection_parts())
        target["database"] = self.name
        return await asyncpg.connect(**target, timeout=10)

    @property
    def dsn(self) -> str:
        """The tenant login's DSN."""

        assert self.name is not None
        return app_dsn(self.name)

    @property
    def system_dsn(self) -> str:
        """The server-only login's DSN."""

        assert self.name is not None
        return system_dsn(self.name)

    async def __aexit__(self, exc_type, exc, tb):
        if self._maintenance is not None:
            if self.name:
                try:
                    await self._maintenance.execute(
                        f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'
                    )
                except Exception:  # noqa: BLE001 - cleanup must not mask a failure
                    pass
            for login, parent in (
                (PERSISTENCE_LOGIN_ROLE, "forge_platform_app"),
                (PERSISTENCE_SYSTEM_LOGIN_ROLE, "forge_platform_system"),
            ):
                try:
                    await self._maintenance.execute(
                        f"REVOKE {parent} FROM {login}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            await self._maintenance.close()
        return False


class TenantSeed:
    """One tenant, seeded directly with SQL as the administrator.

    Seeding bypasses the repositories on purpose: the fixtures must not depend on
    the code under test. The repository tests then read and write through the
    persistence layer and assert against what the fixture put there.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.organization_id = str(uuid.uuid4())
        self.user_id = str(uuid.uuid4())
        self.membership_id = str(uuid.uuid4())
        self.project_id = str(uuid.uuid4())
        self.run_id = str(uuid.uuid4())

    async def seed(self, connection) -> "TenantSeed":
        await connection.execute(
            "INSERT INTO organizations (id, name, slug, created_at) "
            "VALUES ($1, $2, $3, now())",
            uuid.UUID(self.organization_id), f"Org {self.label}",
            f"org-{self.label.lower()}",
        )
        await connection.execute(
            "INSERT INTO users (id, email, display_name, created_at) "
            "VALUES ($1, $2, $3, now())",
            uuid.UUID(self.user_id),
            f"{self.label.lower()}-{self.user_id[:8]}@example.test",
            f"User {self.label}",
        )
        await connection.execute(
            "INSERT INTO memberships (id, organization_id, user_id, role, created_at) "
            "VALUES ($1, $2, $3, 'owner', now())",
            uuid.UUID(self.membership_id), uuid.UUID(self.organization_id),
            uuid.UUID(self.user_id),
        )
        await connection.execute(
            "INSERT INTO projects (id, organization_id, name, slug, workspace_ref, "
            "created_at) VALUES ($1, $2, $3, $4, $5, now())",
            uuid.UUID(self.project_id), uuid.UUID(self.organization_id),
            f"Project {self.label}", f"project-{self.label.lower()}",
            f"ws:{self.label.lower()}",
        )
        await connection.execute(
            "INSERT INTO run_records (id, organization_id, project_id, core_run_id, "
            "initiated_by_user_id, status, started_at, created_at) "
            "VALUES ($1, $2, $3, $4, $5, 'running', now(), now())",
            uuid.UUID(self.run_id), uuid.UUID(self.organization_id),
            uuid.UUID(self.project_id), f"core-{self.label.lower()}",
            uuid.UUID(self.user_id),
        )
        return self
