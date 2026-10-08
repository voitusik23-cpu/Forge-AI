"""Deterministic SQL migration runner for the Platform PostgreSQL schema.

Design constraints, in order of importance:

1. **Driver-agnostic and dependency-free.** This module imports only the standard
   library. It never imports a PostgreSQL driver. The caller supplies a
   connection object, so the executor works with ``asyncpg``, ``psycopg``, or a
   test double, and Core never gains a database dependency through this module.
2. **Plain, readable, reviewable SQL.** Every migration is a ``.sql`` file that a
   reviewer can read in full. There is no ORM and no generated DDL.
3. **Deterministic.** Migrations are ordered by filename, each is applied exactly
   once, and the ledger records a checksum so a later edit to an already-applied
   migration is detected instead of silently ignored.
4. **No hidden transaction.** The runner does not open transactions. Migration
   files that must be atomic say so themselves with ``BEGIN``/``COMMIT``, and
   PostgreSQL runs each statement in its own transaction when the connection is
   in autocommit mode. This keeps the runner's behaviour visible in the SQL.

The connection protocol is intentionally minimal: an object exposing
``async execute(sql: str, *args) -> object``. That single method covers DDL and
the ledger's parameterised inserts.
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass
from typing import Iterable, List, Protocol, Sequence, runtime_checkable

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent / "postgres" / "migrations"

LEDGER_TABLE = "platform_schema_migrations"

LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    filename   text        NOT NULL,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT {LEDGER_TABLE}_pkey PRIMARY KEY (filename)
)
"""


class MigrationError(RuntimeError):
    """Raised when a migration cannot be discovered, validated, or applied."""


@runtime_checkable
class MigrationConnection(Protocol):
    """The minimum a connection must provide.

    ``execute`` runs a statement and may return anything (``asyncpg`` returns a
    status string, which is why the ledger read below does not use it).

    ``fetch`` returns the result rows of a query as a sequence of indexable rows.
    ``asyncpg.Connection`` satisfies both directly.
    """

    async def execute(self, sql: str, *args: object) -> object:  # pragma: no cover
        ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Sequence[object]]:  # pragma: no cover
        ...


@dataclass(frozen=True)
class Migration:
    """One migration file, identified by its filename."""

    filename: str
    path: pathlib.Path
    sql: str
    checksum: str

    @property
    def name(self) -> str:
        return self.filename


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def discover_migrations(directory: pathlib.Path | None = None) -> List[Migration]:
    """Return every migration in the directory, ordered by filename.

    Ordering is by filename, which is why the files are numbered. A missing or
    empty directory is an error rather than an empty migration set: silently
    applying nothing would look like success.
    """

    target = pathlib.Path(directory) if directory is not None else MIGRATIONS_DIR
    if not target.is_dir():
        raise MigrationError(f"migration directory does not exist: {target}")

    migrations: List[Migration] = []
    seen: set[str] = set()
    for path in sorted(target.glob("*.sql")):
        if path.name in seen:
            raise MigrationError(f"duplicate migration filename: {path.name}")
        seen.add(path.name)
        sql = path.read_text(encoding="utf-8")
        if not sql.strip():
            raise MigrationError(f"migration is empty: {path.name}")
        migrations.append(
            Migration(
                filename=path.name,
                path=path,
                sql=sql,
                checksum=_checksum(sql),
            )
        )

    if not migrations:
        raise MigrationError(f"no migrations found in {target}")
    return migrations


async def _applied_migrations(connection: MigrationConnection) -> dict[str, str]:
    rows = await connection.fetch(
        f"SELECT filename, checksum FROM {LEDGER_TABLE} ORDER BY filename"
    )
    return {str(row[0]): str(row[1]) for row in (rows or [])}


async def apply_migrations(
    connection: MigrationConnection,
    migrations: Sequence[Migration] | None = None,
    directory: pathlib.Path | None = None,
) -> List[str]:
    """Apply every pending migration in order and return the applied filenames.

    Re-running against an up-to-date database applies nothing and returns an
    empty list, so the call is idempotent.

    A checksum mismatch on an already-applied migration is an error: editing
    history would otherwise make the recorded state a lie.
    """

    pending = list(migrations) if migrations is not None else discover_migrations(directory)

    await connection.execute(LEDGER_DDL)
    applied = await _applied_migrations(connection)

    newly_applied: List[str] = []
    for migration in pending:
        previous = applied.get(migration.filename)
        if previous is not None:
            if previous != migration.checksum:
                raise MigrationError(
                    f"migration {migration.filename} was already applied with a "
                    f"different checksum; history must not be rewritten"
                )
            continue

        try:
            await connection.execute(migration.sql)
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise MigrationError(f"migration {migration.filename} failed: {exc}") from exc

        await connection.execute(
            f"INSERT INTO {LEDGER_TABLE} (filename, checksum) VALUES ($1, $2)",
            migration.filename,
            migration.checksum,
        )
        newly_applied.append(migration.filename)

    return newly_applied


def migration_filenames(migrations: Iterable[Migration]) -> List[str]:
    """Convenience helper for reporting and tests."""

    return [m.filename for m in migrations]
