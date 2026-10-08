"""Database connection, transaction sessions, scope, and the Unit of Work.

This is the only module in the Platform layer that talks to a driver, and the only
one that translates ``asyncpg`` exceptions into the persistence errors of
:mod:`app.platform.persistence.errors`. It is imported by the repository
implementations and never by ``app/platform/models.py``, which stays stdlib-only.
"""

from __future__ import annotations

import contextlib
from typing import Any, AsyncIterator, Optional, Sequence

from app.platform.persistence.errors import (
    CheckViolationError,
    ConnectionError,
    ForeignKeyViolationError,
    InvalidIdentifierError,
    PermissionDeniedError,
    PersistenceError,
    TransactionError,
    UniqueViolationError,
)

__all__ = [
    "APP_ROLE",
    "SYSTEM_ROLE",
    "TENANT_SETTING",
    "SUBJECT_SETTING",
    "SYSTEM_SCOPE_SETTING",
    "PLATFORM_TABLES",
    "PlatformDatabase",
    "Session",
    "UnitOfWork",
    "normalize_error",
]

APP_ROLE = "forge_platform_app"
SYSTEM_ROLE = "forge_platform_system"

TENANT_SETTING = "forge.organization_id"
SUBJECT_SETTING = "forge.user_id"
SYSTEM_SCOPE_SETTING = "forge.system_scope"

PLATFORM_TABLES = (
    "users",
    "organizations",
    "memberships",
    "projects",
    "provider_accounts",
    "api_keys",
    "run_records",
    "usage_records",
)


# --------------------------------------------------------------------------- #
# error normalization
# --------------------------------------------------------------------------- #
def normalize_error(exc: BaseException) -> PersistenceError:
    """Translate a driver exception into a persistence error.

    ``asyncpg`` exposes the SQLSTATE on ``sqlstate`` and the violated constraint on
    ``constraint_name``. Both are preserved, because a caller reacting to a lost
    race needs to know *which* unique constraint rejected the write, and an
    operator reading a log needs the original message rather than a paraphrase.

    An already-normalized error passes through unchanged, so this is idempotent.
    """

    if isinstance(exc, PersistenceError):
        return exc

    # Imported lazily so this module can be imported and exercised without a
    # driver present, and so a missing driver is reported as a persistence error.
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - driver absence is reported loudly
        return ConnectionError(f"no PostgreSQL driver available: {exc}")

    sqlstate = getattr(exc, "sqlstate", None)
    constraint = getattr(exc, "constraint_name", None)
    message = str(exc).strip() or type(exc).__name__

    if isinstance(exc, asyncpg.UniqueViolationError):
        return UniqueViolationError(message, constraint)
    if isinstance(exc, asyncpg.ForeignKeyViolationError):
        return ForeignKeyViolationError(message, constraint)
    if isinstance(exc, asyncpg.CheckViolationError):
        return CheckViolationError(message, constraint)
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        return PermissionDeniedError(message)
    if isinstance(exc, asyncpg.InvalidTextRepresentationError):
        return InvalidIdentifierError("identifier", message)
    if isinstance(
        exc,
        (asyncpg.PostgresConnectionError, ConnectionRefusedError, OSError),
    ):
        return ConnectionError(message)
    if isinstance(exc, asyncpg.InterfaceError):
        return TransactionError(message)
    if sqlstate is not None and sqlstate.startswith("08"):
        return ConnectionError(message)
    if sqlstate in {"25P02", "25P01", "25000"}:
        return TransactionError(message)
    if sqlstate is not None and sqlstate.startswith("23"):
        return PersistenceError(message)
    if isinstance(exc, asyncpg.PostgresError):
        return PersistenceError(message)
    return PersistenceError(message)


# --------------------------------------------------------------------------- #
# session
# --------------------------------------------------------------------------- #
class Session:
    """A borrowed connection with an explicit transaction lifecycle.

    The session owns two guarantees the repositories rely on:

    * **No implicit transactions.** :meth:`begin` must run before any statement.
      Using a repository outside a transaction is an error rather than a quietly
      auto-committed write, because a tenant-scoped statement with no tenant
      context would be denied by row-level security and would return an empty
      result instead of raising -- a silent wrong answer.
    * **A clean connection on release.** Whatever happens -- commit, rollback, or
      an exception mid-flight -- the transaction is ended and the role is reset,
      so a pooled connection cannot carry one request's scope into the next.
      :meth:`close` is the single place that does this.
    """

    def __init__(self, connection: Any, *, allow_untransacted: bool = False) -> None:
        self._connection = connection
        self._in_transaction = False
        self._closed = False
        self._organization_id: Optional[str] = None
        # Migration and schema work runs statement-by-statement in autocommit mode
        # on purpose, so a failed migration does not roll back silently. Only the
        # direct session sets this; a repository can never reach it.
        self._allow_untransacted = allow_untransacted
        # A rollback or role reset that fails while closing is recorded rather than
        # raised: close() runs from a `finally` block, where raising would replace the
        # caller's real exception. It must still be observable, though, because a
        # connection whose reset failed is not known to be clean.
        self._close_error: Optional[BaseException] = None

    # -- state ------------------------------------------------------------- #
    @property
    def connection(self) -> Any:
        return self._connection

    @property
    def in_transaction(self) -> bool:
        return self._in_transaction and not self._closed

    @property
    def organization_id(self) -> Optional[str]:
        """The tenant this transaction is scoped to, if any."""

        return self._organization_id

    def require_transaction(self) -> None:
        """Fail loudly when a statement is attempted outside a transaction."""

        if self._closed:
            raise TransactionError("session is closed")
        if not self._in_transaction and not self._allow_untransacted:
            raise TransactionError(
                "no active transaction: open a Unit of Work before using a "
                "repository"
            )

    # -- lifecycle --------------------------------------------------------- #
    async def begin(self) -> None:
        if self._closed:
            raise TransactionError("session is closed")
        if self._in_transaction:
            raise TransactionError("a transaction is already open on this session")
        try:
            await self._connection.execute("BEGIN")
        except Exception as exc:  # noqa: BLE001 - normalized at the boundary
            raise normalize_error(exc) from exc
        self._in_transaction = True

    async def set_tenant(self, organization_id: str) -> None:
        """Set the transaction-local tenant context, parameterized.

        ``set_config(..., is_local => true)`` is used rather than a literal
        ``SET LOCAL`` for two reasons: ``SET`` accepts no placeholder, so building
        the statement would mean interpolating a caller-supplied value into SQL;
        and ``set_config`` is the documented parameterized form. The setting is
        transaction-local, so it cannot outlive the transaction even when the
        connection is reused.
        """

        self.require_transaction()
        try:
            await self._connection.execute(
                "SELECT set_config($1, $2, true)", TENANT_SETTING, organization_id
            )
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc
        self._organization_id = organization_id

    async def set_subject(self, user_id: Optional[str]) -> None:
        """Set the transaction-local subject, read only by the ``users`` policy."""

        self.require_transaction()
        try:
            await self._connection.execute(
                "SELECT set_config($1, $2, true)",
                SUBJECT_SETTING,
                "" if user_id is None else user_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc

    async def set_system_scope(self, declared: bool) -> None:
        """Declare, or withdraw, the server-only system scope for this transaction.

        **This flag is not an authorization mechanism on its own.** Any session can
        set a custom setting. What gates the system scope is the database role and
        the policies attached to it; the flag only narrows the scope further and
        keeps system-owned rows invisible by default.
        """

        self.require_transaction()
        try:
            await self._connection.execute(
                "SELECT set_config($1, $2, true)",
                SYSTEM_SCOPE_SETTING,
                "on" if declared else "off",
            )
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc

    async def commit(self) -> None:
        """Commit the transaction, or do nothing if it has already ended.

        Idempotent on purpose: a caller may roll the transaction back explicitly
        (``UnitOfWork.rollback()``) and then leave the context manager normally. The
        exit path must not turn that into an error, and it must not silently open a
        second transaction either.
        """

        if not self._in_transaction:
            return
        try:
            await self._connection.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc
        finally:
            self._in_transaction = False
            self._organization_id = None

    async def rollback(self) -> None:
        if not self._in_transaction:
            return
        try:
            await self._connection.execute("ROLLBACK")
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc
        finally:
            self._in_transaction = False
            self._organization_id = None

    async def close(self) -> None:
        """End the transaction if any, reset the role, and mark the session closed.

        Called from every Unit of Work ``finally`` block, so an exception on the
        way out cannot leave a half-open transaction on a connection that is about
        to return to a pool.

        What "clean" means here, stated precisely because the difference matters:

        * the **transaction** is ended, so every transaction-local setting --
          ``forge.organization_id``, ``forge.user_id``, ``forge.system_scope``, and
          any ``SET LOCAL ROLE`` -- is already gone before this runs;
        * the **session-level role** set at pool setup remains, and that is
          intended: a pooled connection stays stepped down. ``RESET ROLE`` below is
          therefore a no-op in the normal case, and is kept as a backstop for the
          case where a connection was touched outside a transaction;
        * a released connection is consequently ``forge_platform_app`` with **no**
          tenant context, which is the fail-closed state: the policies deny, and the
          tenant-scoped repositories refuse to run without a tenant.
        """

        if self._closed:
            return
        self._closed = True
        self._organization_id = None
        if self._in_transaction:
            try:
                await self._connection.execute("ROLLBACK")
            except Exception as exc:  # noqa: BLE001
                self._close_error = exc
            self._in_transaction = False
        try:
            await self._connection.execute("RESET ROLE")
        except Exception as exc:  # noqa: BLE001
            if self._close_error is None:
                self._close_error = exc

    @property
    def close_error(self) -> Optional[BaseException]:
        """The failure that occurred while closing, if any.

        ``None`` in the normal case. A non-``None`` value means the connection could
        not be proven clean, so a caller that cares -- a pool health check, a test --
        can see it instead of trusting a suppressed exception.
        """

        return self._close_error

    # -- statements -------------------------------------------------------- #
    async def fetch(self, sql: str, *args: object) -> Sequence[Any]:
        self.require_transaction()
        try:
            return await self._connection.fetch(sql, *args)
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc

    async def fetchrow(self, sql: str, *args: object) -> Optional[Any]:
        self.require_transaction()
        try:
            return await self._connection.fetchrow(sql, *args)
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc

    async def fetchval(self, sql: str, *args: object) -> Any:
        self.require_transaction()
        try:
            return await self._connection.fetchval(sql, *args)
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc

    async def execute(self, sql: str, *args: object) -> str:
        self.require_transaction()
        try:
            return await self._connection.execute(sql, *args)
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc


# --------------------------------------------------------------------------- #
# unit of work
# --------------------------------------------------------------------------- #
class UnitOfWork:
    """One transaction, one scope, one set of repositories.

    This object is the only supported way to reach a repository, because it owns
    the three things a repository must not manage itself:

    * the **transaction boundary** -- repositories never open or commit one, so a
      set of related writes either lands together or not at all;
    * the **scope** -- a tenant or subject set once, transaction-locally, from a
      validated value, parameterized and never interpolated into SQL;
    * the **repository instances** -- built around this session, so a repository
      cannot outlive the transaction that gave it its scope.

    It is an async context manager that **commits on clean exit and rolls back on
    exception**, then closes the session in ``finally``. There is no way to leave
    it open by accident, and no way to use a repository afterwards.

    The three modes are created by :meth:`PlatformDatabase.tenant`,
    :meth:`PlatformDatabase.pre_tenant`, and :meth:`PlatformDatabase.server`.
    """

    def __init__(self, session: Session, *, mode: str,
                 organization_id: Optional[str] = None) -> None:
        self._session = session
        self._mode = mode
        self._organization_id = organization_id
        self._repositories: dict[str, Any] = {}

    # -- introspection ----------------------------------------------------- #
    @property
    def session(self) -> Session:
        return self._session

    @property
    def mode(self) -> str:
        """``tenant``, ``pre_tenant``, or ``server``."""

        return self._mode

    @property
    def organization_id(self) -> Optional[str]:
        return self._organization_id

    # -- repositories ------------------------------------------------------ #
    def _repository(self, name: str) -> Any:
        if self._session is None or not self._repositories:
            raise TransactionError(
                "this Unit of Work has no repositories; it was not created by "
                "PlatformDatabase.tenant/pre_tenant/server, or it has already exited"
            )
        if self._session._closed:  # noqa: SLF001 - same module owns this state
            raise TransactionError(
                "this Unit of Work has exited; its repositories are no longer usable"
            )
        return self._repositories[name]

    @property
    def users(self) -> Any:
        return self._repository("users")

    @property
    def organizations(self) -> Any:
        return self._repository("organizations")

    @property
    def memberships(self) -> Any:
        """Tenant-scoped membership access. The tenant comes from this transaction."""

        return self._repository("memberships")

    @property
    def system_memberships(self) -> Any:
        """Membership bootstrap on the server-only scope.

        The one membership path that takes an organization explicitly, because a
        brand-new tenant has no members and therefore no tenant context to derive one
        from. Reachable only from the server scope with the system role, which is the
        only role holding ``INSERT`` on ``memberships``.
        """

        return self._repository("system_memberships")

    @property
    def projects(self) -> Any:
        return self._repository("projects")

    @property
    def provider_accounts(self) -> Any:
        """Tenant-owned provider credentials (BYOK)."""

        return self._repository("provider_accounts")

    @property
    def system_provider_accounts(self) -> Any:
        """System-owned provider credentials. Server scope only."""

        return self._repository("system_provider_accounts")

    @property
    def api_keys(self) -> Any:
        return self._repository("api_keys")

    @property
    def run_records(self) -> Any:
        return self._repository("run_records")

    @property
    def usage_records(self) -> Any:
        """Append-only. The protocol has no update and no delete."""

        return self._repository("usage_records")

    # -- explicit transaction control -------------------------------------- #
    async def rollback(self) -> None:
        """Abandon the transaction. The context manager will still exit cleanly."""

        await self._session.rollback()

    # -- context management ------------------------------------------------ #
    async def __aenter__(self) -> "UnitOfWork":
        from app.platform.persistence import repositories as repo_module

        self._repositories = {
            "users": repo_module.PostgresUserRepository(self._session),
            "organizations": repo_module.PostgresOrganizationRepository(self._session),
            "memberships": repo_module.PostgresMembershipRepository(self._session),
            "system_memberships":
                repo_module.PostgresSystemMembershipRepository(self._session),
            "projects": repo_module.PostgresProjectRepository(self._session),
            "provider_accounts": repo_module.PostgresProviderAccountRepository(
                self._session
            ),
            "system_provider_accounts":
                repo_module.PostgresSystemProviderAccountRepository(self._session),
            "api_keys": repo_module.PostgresAPIKeyRepository(self._session),
            "run_records": repo_module.PostgresRunRecordRepository(self._session),
            "usage_records": repo_module.PostgresUsageRecordRepository(self._session),
        }
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        except PersistenceError:
            # A rollback failure must not replace the caller's original exception,
            # and it must not vanish either: `Session.close` records it on the
            # session, which is where a connection-health check can see it. Nothing
            # is stored on the Unit of Work, which is about to be discarded.
            if exc_type is None:
                raise
        finally:
            self._repositories = {}
            await self._session.close()
        return False


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
class PlatformDatabase:
    """Owns the connection pool and validates that the effective role is safe.

    THE ROLE CHECK IS NOT DECORATION. Row-level security does not apply to a
    table's owner, and a superuser or a ``BYPASSRLS`` role ignores it entirely. A
    deployment that connected as the owner would satisfy every test in this
    repository while enforcing nothing in production, so the pool refuses to be
    built on such a role instead of discovering the problem later.

    ``application_role`` is applied once per physical connection, at pool setup,
    so every session starts already stepped down. The scope factories may step
    down further, for the duration of one transaction.
    """

    def __init__(
        self,
        dsn: str,
        *,
        application_role: Optional[str] = APP_ROLE,
        min_size: int = 1,
        max_size: int = 10,
        command_timeout: Optional[float] = 30.0,
    ) -> None:
        self._dsn = dsn
        self._application_role = application_role
        self._min_size = min_size
        self._max_size = max_size
        self._command_timeout = command_timeout
        self._pool: Any = None

    @property
    def application_role(self) -> Optional[str]:
        return self._application_role

    # -- pool -------------------------------------------------------------- #
    async def _setup_connection(self, connection: Any) -> None:
        """Switch to the application role and prove the switch is safe.

        The question this answers is **"is the role that will actually run the
        statements subject to row-level security"**, and three situations make the
        policies decorative. Each is refused rather than logged:

        * a superuser or a ``BYPASSRLS`` role, whether it is the login or the
          substituted role, because ``pg_has_role`` reports a superuser as holding
          every role -- so a membership check alone is vacuous for one;
        * a role that owns a Platform table, because an owner is exempt from its own
          policies even when the table is marked ``FORCE``;
        * a login that is a member of the *system* role while the tenant role is
          requested, because that login could step into the system scope and the
          separation Step 2 established would exist only in the policy text.

        Every role name is validated against the two role constants before it is
        interpolated into ``SET ROLE``, so this method cannot be a place where an
        unvalidated value reaches SQL.
        """

        if self._application_role is not None:
            self._require_known_role(self._application_role)

        current = await connection.fetchrow(
            "SELECT current_user AS current_user, session_user AS session_user"
        )
        if current is None:
            raise ConnectionError("could not determine the current database role")
        current_user = current["current_user"]
        session_user = current["session_user"]

        # The LOGIN's own attributes. Checked before any switch, because after the
        # switch `current_user` no longer names the login.
        await self._require_not_privileged(connection, session_user, "session role")

        if self._application_role == APP_ROLE:
            # A login that may become the system role must never serve tenant
            # traffic: the pool is a tenant pool, and one login holding both scopes
            # is exactly the configuration the schema forbids.
            reaches_system = await connection.fetchval(
                "SELECT pg_has_role($1, $2, 'MEMBER')",
                session_user,
                SYSTEM_ROLE,
            )
            if reaches_system:
                raise ConnectionError(
                    f"the login {session_user!r} is a member of {SYSTEM_ROLE!r} as well "
                    f"as {APP_ROLE!r}; tenant traffic and system traffic must be served "
                    "by different logins, because one login holding both scopes makes "
                    "the role boundary decorative"
                )

        if self._application_role is not None and current_user != self._application_role:
            # The switch is proven possible before it is attempted, so an insufficient
            # privilege is reported as this layer's own ConnectionError rather than as
            # a raw driver error a caller would have to know how to interpret.
            #
            # `pg_has_role(..., 'MEMBER')` is NOT sufficient on its own -- it is
            # vacuously true for a superuser, which is why the login's attributes are
            # checked first -- but for a role that is genuinely not a member it is
            # exactly the right question.
            is_member = await connection.fetchval(
                "SELECT pg_has_role($1, $2, 'MEMBER')", session_user,
                self._application_role,
            )
            if not is_member:
                raise ConnectionError(
                    f"the login {session_user!r} is not a member of "
                    f"{self._application_role!r}; the Platform repositories require the "
                    "role boundary the migrations establish"
                )
            await connection.execute(f'SET ROLE "{self._application_role}"')
            current_user = self._application_role

        # The attributes are checked on the EFFECTIVE role, whichever way it was
        # reached. An earlier version checked them only when no substitution was
        # requested, which accepted a superuser login -- exactly the case this check
        # exists to refuse.
        await self._require_not_privileged(
            connection, current_user, "effective role"
        )

        await self._require_no_owned_tables(connection, current_user)

    @staticmethod
    def _require_known_role(role: str) -> None:
        """Refuse a role name that is not one of the two the schema defines.

        The name is interpolated into ``SET ROLE``, which accepts no placeholder, so
        it must be a known constant rather than a configuration string that could
        carry SQL.
        """

        if role not in (APP_ROLE, SYSTEM_ROLE):
            raise ConnectionError(
                f"unsupported application_role {role!r}; the Platform schema defines "
                f"only {APP_ROLE!r} and {SYSTEM_ROLE!r}"
            )

    @staticmethod
    async def _require_not_privileged(
        connection: Any, role: str, label: str
    ) -> None:
        """Refuse a role that is a superuser or bypasses row-level security."""

        row = await connection.fetchrow(
            "SELECT rolsuper, rolbypassrls, "
            "pg_has_role(rolname, 'postgres', 'USAGE') AS super_like "
            "FROM pg_roles WHERE rolname = $1",
            role,
        )
        if row is None:
            raise ConnectionError(f"could not read the attributes of {label} {role!r}")
        if row["rolsuper"] or row["rolbypassrls"] or row["super_like"]:
            raise ConnectionError(
                f"{label} {role!r} is a superuser or bypasses row-level security; "
                "refusing to build a pool whose row-level security would not apply"
            )

    @staticmethod
    async def _require_no_owned_tables(connection: Any, role: str) -> None:
        """Refuse a role that owns a Platform table, in ANY reachable schema.

        The schema is deliberately not pinned to ``public``. The migrations create
        the tables unqualified, so they live wherever ``search_path`` resolves, and
        repositories use unqualified names too. Pinning the check to ``public`` meant
        a deployment whose search path put the tables elsewhere was accepted -- and
        then the owning role could run ``ALTER TABLE ... NO FORCE ROW LEVEL
        SECURITY`` and read every tenant. The check therefore looks at every schema
        the role could reach, which is what "owns a Platform table" actually means.
        """

        owned = await connection.fetch(
            """
            SELECT n.nspname AS schema_name, c.relname AS table_name
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'f')
              AND c.relname = ANY($1::text[])
              AND pg_get_userbyid(c.relowner) = $2
              AND n.nspname NOT LIKE 'pg%'
            ORDER BY n.nspname, c.relname
            """,
            list(PLATFORM_TABLES),
            role,
        )
        if owned:
            described = ", ".join(
                f"{row['schema_name']}.{row['table_name']}" for row in owned
            )
            raise ConnectionError(
                f"effective role {role!r} owns Platform table(s) {described}; an "
                "owner is exempt from its own row-level security, so the application "
                "role must not own the schema"
            )

    async def connect(self) -> "PlatformDatabase":
        if self._pool is not None:
            return self
        try:
            import asyncpg
        except ImportError as exc:  # pragma: no cover
            raise ConnectionError(f"no PostgreSQL driver available: {exc}") from exc

        try:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                command_timeout=self._command_timeout,
                setup=self._setup_connection,
            )
            # Acquire one connection eagerly. The pool's `setup` hook runs when a
            # connection is first handed out, so without this an unsafe role would
            # only be discovered on the first query -- and a caller that never
            # queried would believe it had a working, policy-enforcing pool.
            async with self._pool.acquire():
                pass
        except PersistenceError:
            await self.close()
            raise
        except Exception as exc:  # noqa: BLE001
            await self.close()
            raise normalize_error(exc) from exc
        return self

    async def close(self) -> None:
        if self._pool is not None:
            pool, self._pool = self._pool, None
            with contextlib.suppress(Exception):
                await pool.close()

    @contextlib.asynccontextmanager
    async def _borrow(self, *, allow_untransacted: bool = False) -> AsyncIterator[Session]:
        if self._pool is None:
            raise TransactionError("database is not connected; call connect() first")
        async with self._pool.acquire() as connection:
            session = Session(connection, allow_untransacted=allow_untransacted)
            try:
                yield session
            finally:
                await session.close()

    @contextlib.asynccontextmanager
    async def direct_session(self) -> AsyncIterator[Session]:
        """A session with no transaction and no scope, for schema and migration work.

        It deliberately does not open a transaction: running DDL inside one would
        make a failed migration roll back silently, and the migration runner
        reports each failure instead.
        """

        async with self._borrow(allow_untransacted=True) as session:
            yield session

    # -- unit of work factories -------------------------------------------- #
    @contextlib.asynccontextmanager
    async def tenant(
        self, organization_id: str, *, user_id: Optional[str] = None
    ) -> AsyncIterator[UnitOfWork]:
        """The ordinary tenant request path.

        Sets the transaction-local tenant context from a **validated** identifier.
        A malformed identifier is refused before it reaches :func:`set_config`, so
        it cannot become an opaque driver error, and it certainly cannot decay into
        an empty context that silently returns nothing.

        The transaction lifecycle belongs to the Unit of Work: it commits on clean
        exit, rolls back on exception, and closes the session in ``finally``. These
        factories only establish the scope before handing over.
        """

        from app.platform.persistence.mapper import as_uuid

        validated = str(as_uuid(organization_id, "organization_id"))
        subject = None if user_id is None else str(as_uuid(user_id, "user_id"))
        async with self._borrow() as session:
            await session.begin()
            try:
                await session.execute(f'SET LOCAL ROLE "{APP_ROLE}"')
                await session.set_tenant(validated)
                if subject is not None:
                    await session.set_subject(subject)
            except BaseException:
                await session.close()
                raise
            async with UnitOfWork(
                session, mode="tenant", organization_id=validated
            ) as unit:
                yield unit

    @contextlib.asynccontextmanager
    async def pre_tenant(
        self, *, user_id: Optional[str] = None
    ) -> AsyncIterator[UnitOfWork]:
        """Discovery, before a tenant has been chosen.

        This is the database capability Step 2 authorised for pre-tenant work: the
        server-only scope with an explicitly declared system scope, which can read
        identity, memberships, and organizations and nothing else. It is not a
        generic bypass -- the system scope has no privilege and no policy on
        projects, api keys, runs, or usage -- and it carries **no tenant context**,
        so a tenant-scoped repository used here is denied by row-level security.

        The result of this path is the set of tenants a subject may choose between.
        The choice is an application decision; this layer only retrieves candidates.
        """

        from app.platform.persistence.mapper import as_uuid

        subject = None if user_id is None else str(as_uuid(user_id, "user_id"))
        async with self._borrow() as session:
            await session.begin()
            try:
                await session.execute(f'SET LOCAL ROLE "{SYSTEM_ROLE}"')
                await session.set_system_scope(True)
                if subject is not None:
                    await session.set_subject(subject)
            except BaseException:
                await session.close()
                raise
            async with UnitOfWork(
                session, mode="pre_tenant", organization_id=None
            ) as unit:
                yield unit

    @contextlib.asynccontextmanager
    async def server(
        self, *, role: str = APP_ROLE, system_scope: bool = False
    ) -> AsyncIterator[UnitOfWork]:
        """The server-only path.

        Two distinct uses, and the caller must choose which one it means:

        * ``server()`` -- no system scope. The effective role is the tenant role
          with **no tenant context**, so row-level security denies every read of
          existing rows. This is how server code writes the bootstrap rows for a
          known tenant: the same privilege level as a tenant request, with the
          caller stating the tenant explicitly on each row it writes.
        * ``server(role=SYSTEM_ROLE, system_scope=True)`` -- the system scope, for
          system-owned provider credentials and membership administration. It is
          not a general tenant repository: the system role has no access to tenant
          work at all.
        """

        if role not in {APP_ROLE, SYSTEM_ROLE}:
            raise TransactionError(f"unsupported server role: {role!r}")
        async with self._borrow() as session:
            await session.begin()
            try:
                await session.execute(f'SET LOCAL ROLE "{role}"')
                if system_scope:
                    await session.set_system_scope(True)
            except BaseException:
                await session.close()
                raise
            async with UnitOfWork(session, mode="server", organization_id=None) as unit:
                yield unit
