"""PostgreSQL persistence for the Platform layer.

This package owns the Platform database schema, its migrations, the row-level
security policies that enforce tenant containment, and the repositories and Unit
of Work that application services use. It is deliberately separate from the
Platform domain contracts in ``app.platform.models``:

* the domain layer stays stdlib-only and imports nothing from here;
* nothing in Core (``app/agent_runtime``, ``app/agents``, ``app/orchestrator``,
  ``app/runtime``, ``app/execution``, ``app/api``, and the rest of ``app/``
  outside ``app/platform``) may import this package.

Layering, from the outside in::

    application services
      -> protocols          (interfaces, no database knowledge)
      -> UnitOfWork         (transaction, scope, repository set)
      -> repositories       (parameterized SQL, no transaction management)
      -> Session            (transaction guard, error normalization)
      -> PlatformDatabase   (pool, role safety check)
      -> PostgreSQL + RLS

Nothing here is an ORM. The domain records stay frozen dataclasses, the schema is
plain SQL, and ``mapper.py`` is the only module that knows both a domain record and
a physical column.

Typical use::

    database = await PlatformDatabase(dsn).connect()
    async with database.tenant(organization_id, user_id=subject) as uow:
        project = await uow.projects.create(new_id(), "Example")
        await uow.run_records.create_queued(new_id(), project.id)
    await database.close()
"""

from app.platform.persistence.database import (
    APP_ROLE,
    SYSTEM_ROLE,
    PlatformDatabase,
    Session,
    UnitOfWork,
    normalize_error,
)
from app.platform.persistence.errors import (
    CheckViolationError,
    ConnectionError,
    EntityNotFound,
    ForeignKeyViolationError,
    InvalidIdentifierError,
    PermissionDeniedError,
    PersistenceError,
    TransactionError,
    UniqueViolationError,
)
from app.platform.persistence.migrations import (
    MIGRATIONS_DIR,
    Migration,
    MigrationError,
    apply_migrations,
    discover_migrations,
)

__all__ = [
    # database and scope
    "PlatformDatabase",
    "Session",
    "UnitOfWork",
    "APP_ROLE",
    "SYSTEM_ROLE",
    "normalize_error",
    # errors
    "PersistenceError",
    "EntityNotFound",
    "InvalidIdentifierError",
    "UniqueViolationError",
    "ForeignKeyViolationError",
    "CheckViolationError",
    "PermissionDeniedError",
    "TransactionError",
    "ConnectionError",
    # migrations
    "MIGRATIONS_DIR",
    "Migration",
    "MigrationError",
    "apply_migrations",
    "discover_migrations",
]
