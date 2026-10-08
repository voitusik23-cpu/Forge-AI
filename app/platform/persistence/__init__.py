"""PostgreSQL persistence for the Platform layer.

This package owns the Platform database schema, its migrations, and the
row-level security policies that enforce tenant containment. It is deliberately
separate from the Platform domain contracts in ``app.platform.models``:

* the domain layer stays stdlib-only and imports nothing from here;
* nothing in Core (``app/agent_runtime``, ``app/agents``, ``app/orchestrator``,
  and the rest of ``app/`` outside ``app/platform``) may import this package.

Nothing here is an ORM, a repository, or a service. The schema is plain SQL, and
the migration runner executes that SQL through whatever database connection the
caller supplies.
"""

from app.platform.persistence.migrations import (
    MIGRATIONS_DIR,
    Migration,
    MigrationError,
    apply_migrations,
    discover_migrations,
)

__all__ = [
    "MIGRATIONS_DIR",
    "Migration",
    "MigrationError",
    "apply_migrations",
    "discover_migrations",
]
