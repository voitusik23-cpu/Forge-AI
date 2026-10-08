"""Shared live-PostgreSQL fixture for the Platform database contract tests.

The Platform schema relies on semantics that SQLite does not have: row-level
security, ``FORCE ROW LEVEL SECURITY``, ``current_setting``, partial unique
indexes, and composite foreign keys. Simulating those in SQLite would test the
simulation, not the schema, so these tests run against a real PostgreSQL server
or they are skipped.

Configuration, in priority order:

1. ``FORGE_PLATFORM_TEST_DSN`` -- a full DSN, e.g.
   ``postgresql://postgres:secret@127.0.0.1:5432/postgres``.
2. ``FORGE_PLATFORM_TEST_HOST`` / ``_PORT`` / ``_USER`` / ``_PASSWORD`` /
   ``_DATABASE`` -- components, defaulting to 127.0.0.1:5432, user ``postgres``,
   no password, database ``postgres``.

When neither is set, ``live_postgres()`` returns ``None`` and the caller skips
with an explicit reason. Nothing here fabricates a database.
"""

from __future__ import annotations

import os
import pathlib
import sys
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DSN_ENV = "FORGE_PLATFORM_TEST_DSN"

# Roles created by the migrations. The app role is the ordinary tenant path; the
# system role is the separate server-only boundary for system-owned rows.
APP_ROLE = "forge_platform_app"
SYSTEM_ROLE = "forge_platform_system"

TENANT_SETTING = "forge.organization_id"
SUBJECT_SETTING = "forge.user_id"
SYSTEM_SCOPE_SETTING = "forge.system_scope"

SKIP_REASON_NO_DRIVER = (
    "asyncpg is not installed; the Platform database contract tests require a "
    "real PostgreSQL driver because they assert server-side RLS semantics"
)
SKIP_REASON_NO_SERVER = (
    "no live PostgreSQL server configured or reachable; set FORGE_PLATFORM_TEST_DSN "
    "to run the Platform database contract tests. RLS, FORCE RLS, current_setting, "
    "partial unique indexes, and composite foreign keys are PostgreSQL semantics "
    "and are deliberately NOT simulated in SQLite"
)


def asyncpg_module():
    """Return the asyncpg module, or None when it is unavailable."""

    try:
        import asyncpg  # noqa: PLC0415 - optional, test-only dependency
    except ImportError:
        return None
    return asyncpg


def connect_kwargs() -> dict:
    """Connection arguments for the maintenance database."""

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


async def live_postgres():
    """Return a connection to the maintenance database, or None if unavailable."""

    asyncpg = asyncpg_module()
    if asyncpg is None:
        return None
    try:
        return await asyncpg.connect(**connect_kwargs(), timeout=5)
    except Exception:  # noqa: BLE001 - any failure means "no live server"
        return None


def new_database_name() -> str:
    return "forge_platform_test_" + uuid.uuid4().hex[:12]


async def schema_file_sql() -> str:
    path = (
        REPO_ROOT
        / "app"
        / "platform"
        / "persistence"
        / "postgres"
        / "schema.sql"
    )
    return path.read_text(encoding="utf-8")


class LiveDatabase:
    """Create, migrate, and drop one throwaway database.

    Every test class gets its own database so that the contract assertions cannot
    be affected by another class's rows, and so that a failure leaves nothing
    behind.
    """

    def __init__(self) -> None:
        self.name: str | None = None
        self._maintenance = None
        self._connection = None

    async def __aenter__(self):
        asyncpg = asyncpg_module()
        if asyncpg is None:
            raise RuntimeError(SKIP_REASON_NO_DRIVER)

        connect = connect_kwargs()
        self._maintenance = await asyncpg.connect(**connect, timeout=10)
        self.name = new_database_name()
        await self._maintenance.execute(f'CREATE DATABASE "{self.name}"')

        # Reuse the same connection parameters, only replacing the database, so
        # that a DSN and an explicit component set behave identically.
        target = dict(connect)
        target["database"] = self.name
        self._connection = await asyncpg.connect(**target, timeout=10)
        return self

    @property
    def connection(self):
        return self._connection

    async def apply_migrations(self) -> list[str]:
        from app.platform.persistence import apply_migrations

        return await apply_migrations(self._connection)

    async def apply_consolidated_schema(self) -> None:
        await self._connection.execute(await schema_file_sql())

    async def __aexit__(self, exc_type, exc, tb):
        if self._connection is not None:
            await self._connection.close()
        if self._maintenance is not None:
            if self.name:
                await self._maintenance.execute(
                    f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'
                )
            await self._maintenance.close()
        return False


async def set_context(conn, organization_id=None, user_id=None, system_scope=None) -> None:
    """Set the transaction-local RLS context.

    ``set_config(..., is_local => true)`` is used rather than a literal
    ``SET LOCAL`` statement because the value has to be bound as a parameter: the
    ``SET`` statement does not accept placeholders. This is also the reason the
    server layer must never interpolate a client-supplied string into ``SET``.
    """

    if organization_id is not None:
        await conn.execute(
            f"SELECT set_config('{TENANT_SETTING}', $1, true)", str(organization_id)
        )
    if user_id is not None:
        await conn.execute(
            f"SELECT set_config('{SUBJECT_SETTING}', $1, true)", str(user_id)
        )
    if system_scope is not None:
        await conn.execute(
            f"SELECT set_config('{SYSTEM_SCOPE_SETTING}', $1, true)", system_scope
        )
