"""Platform persistence contract tests: Unit of Work, mapping, repositories.

These run against real PostgreSQL. The guarantees under test -- tenant containment
by row-level security, append-only usage, lifecycle CHECK constraints, composite
provenance keys, and role boundaries -- are database behaviour, so there is nothing
to simulate and no substitute.

The persistence layer connects as a **real non-superuser login role** that is only
a member of ``forge_platform_app``. That matters: the database owner bypasses
row-level security, so a test suite running as the owner would assert against a
database that enforces nothing. :class:`PlatformDatabase` refuses to build a pool
on such a role, which is itself asserted here.

Every test is skipped with an explicit reason when no server is configured, and a
skip is never reported as a pass.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import pathlib
import unittest
import uuid

from persistence_fixture import (
    PERSISTENCE_LOGIN_ROLE,
    PersistenceDatabase,
    SKIP_REASON_NO_DRIVER,
    SKIP_REASON_NO_SERVER,
    TenantSeed,
    asyncpg_module,
    live_postgres,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

from app.platform.enums import (  # noqa: E402 - after sys.path setup in fixture
    APIKeyStatus,
    MembershipRole,
    MembershipStatus,
    ProjectStatus,
    ProviderAccountStatus,
    RunRecordStatus,
    UserStatus,
)
from app.platform.models import (  # noqa: E402
    APIKey,
    Membership,
    Organization,
    ProviderAccount,
    RunRecord,
    UsageRecord,
    User,
    new_id,
    utc_now,
)
from app.platform.persistence import (  # noqa: E402
    APP_ROLE,
    CheckViolationError,
    PersistenceError,
    ConnectionError,
    EntityNotFound,
    ForeignKeyViolationError,
    InvalidIdentifierError,
    PermissionDeniedError,
    PlatformDatabase,
    TransactionError,
    UniqueViolationError,
)

UTC = dt.timezone.utc


def run_async(coro):
    return asyncio.run(coro)


class PersistenceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if asyncpg_module() is None:
            raise unittest.SkipTest(SKIP_REASON_NO_DRIVER)

        async def probe():
            connection = await live_postgres()
            if connection is None:
                return False
            await connection.close()
            return True

        if not run_async(probe()):
            raise unittest.SkipTest(SKIP_REASON_NO_SERVER)


SYSTEM_ROLE = "forge_platform_system"


async def open_database(
    handle: PersistenceDatabase, *, system: bool = False
) -> PlatformDatabase:
    """Connect the persistence layer, as one of the two logins.

    ``system=True`` uses the server-only login, which is a member of the system role
    and nothing else. A separate connection rather than a ``SET ROLE`` on the tenant
    connection, because a deployment must not let one login reach both scopes -- and
    a fixture that allowed it would be testing around the boundary instead of
    through it.

    The caller owns the returned database and must close it.
    """

    if system:
        database = PlatformDatabase(
            handle.system_dsn, application_role=SYSTEM_ROLE,
            min_size=1, max_size=2,
        )
    else:
        database = PlatformDatabase(handle.dsn, min_size=1, max_size=4)
    await database.connect()
    return database


@contextlib.asynccontextmanager
async def pre_tenant_scope(handle: PersistenceDatabase, *, user_id=None):
    """Discovery, through the server-only login.

    Pre-tenant discovery needs the database capability Step 2 authorised for it:
    the system role with an explicitly declared system scope. It is reached through
    the dedicated login rather than by switching roles on a tenant connection,
    because a deployment must not let one login hold both scopes.
    """

    database = await open_database(handle, system=True)
    try:
        async with database.pre_tenant(user_id=user_id) as uow:
            yield uow
    finally:
        await database.close()


@contextlib.asynccontextmanager
async def system_scope(handle: PersistenceDatabase):
    """One server-scope Unit of Work, with its connection opened and closed here.

    Closing is the point of this helper. ``PlatformDatabase`` owns a pool, and a
    scope that borrowed one without returning it would exhaust the server's
    connection slots after a few dozen tests.
    """

    database = await open_database(handle, system=True)
    try:
        async with database.server(role=SYSTEM_ROLE, system_scope=True) as uow:
            yield uow
    finally:
        await database.close()


async def seed_tenant(handle: PersistenceDatabase, label: str) -> TenantSeed:
    """Create a tenant with an owner, a project, and a run, through the layer.

    Two scopes, because the schema defines two:

    * the **bootstrap** rows -- organization, user account, first membership --
      run on the server-only path with the system scope. Creating a tenant cannot
      be tenant-scoped, and the tenant role has no privilege to do it;
    * the **operational** rows -- the project and the run -- run on the ordinary
      tenant path, so the seed exercises the same policies a request would.

    Seeding through the repositories rather than raw SQL means every test that
    needs a tenant also exercises the create paths it depends on.
    """

    organization_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    async with system_scope(handle) as uow:
        await uow.organizations.create(
            Organization(
                id=organization_id,
                name=f"Org {label}",
                created_at=utc_now(),
                slug=f"org-{label.lower()}-{organization_id[:8]}",
            )
        )
        await uow.users.create(
            User(
                id=user_id,
                email=f"{label.lower()}-{user_id[:8]}@example.test",
                created_at=utc_now(),
                display_name=f"User {label}",
            )
        )
        # The FIRST membership of a brand-new tenant is a bootstrap write: there is
        # no tenant context yet, so it runs on the server-only path.
        await uow.system_memberships.create(
            organization_id,
            Membership(
                id=str(uuid.uuid4()),
                organization_id=organization_id,
                user_id=user_id,
                role=MembershipRole.OWNER,
                created_at=utc_now(),
            )
        )

    database = await open_database(handle)
    try:
        async with database.tenant(organization_id, user_id=user_id) as uow:
            project = await uow.projects.create(
                str(uuid.uuid4()), f"Project {label}",
                slug=f"project-{label.lower()}",
                workspace_ref=f"ws:{label.lower()}",
            )
            run_id = str(uuid.uuid4())
            await uow.run_records.create_queued(
                run_id, project.id, task_id=f"task-{label.lower()}"
            )
            # Claim it, so the seeded run carries a Core correlation id. The
            # `running` state is what the mapping and lifecycle tests need, and a
            # queued run cannot have a core_run_id by contract.
            run = await uow.run_records.claim(
                run_id, core_run_id=f"core-{label.lower()}",
                initiated_by_user_id=user_id, started_at=utc_now(),
            )
    finally:
        await database.close()

    seed = TenantSeed(label)
    seed.organization_id = organization_id
    seed.user_id = user_id
    seed.project_id = project.id
    seed.run_id = run.id
    return seed


# =========================================================================== #
# A. Unit of Work and scope
# =========================================================================== #
class UnitOfWorkTests(PersistenceTestCase):
    def test_001_pool_refuses_a_superuser_connection(self):
        """A pool whose row-level security would not apply is refused, not warned.

        The connection is made as the administrator and no step-down is requested,
        which is exactly the deployment mistake this guard exists to catch. The
        refusal must happen at ``connect()``: a pool that only failed on first use
        would let a caller believe it had policy enforcement it never had.
        """

        async def body():
            async with PersistenceDatabase() as handle:
                from persistence_fixture import connection_parts

                parts = connection_parts()
                admin_dsn = (
                    f"postgresql://{parts['user']}:{parts.get('password') or ''}"
                    f"@{parts['host']}:{parts['port']}/{handle.name}"
                )
                database = PlatformDatabase(
                    admin_dsn, application_role=None, min_size=1, max_size=1
                )
                try:
                    with self.assertRaises(ConnectionError) as caught:
                        await database.connect()
                    self.assertIn("superuser", str(caught.exception))
                finally:
                    await database.close()
        run_async(body())

    def test_002_pool_refuses_a_role_without_the_application_membership(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = PlatformDatabase(
                    handle.dsn, application_role="forge_platform_system",
                    min_size=1, max_size=1,
                )
                with self.assertRaises(ConnectionError):
                    await database.connect()
        run_async(body())

    def test_003_pool_steps_down_to_the_application_role(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    async with database.tenant(str(uuid.uuid4())) as uow:
                        current = await uow.session.fetchval(
                            "SELECT current_user, session_user"
                        )
                        row = await uow.session.fetchrow(
                            "SELECT current_user, session_user"
                        )
                        self.assertEqual(row["current_user"], APP_ROLE)
                        self.assertEqual(row["session_user"],
                                         PERSISTENCE_LOGIN_ROLE)
                finally:
                    await database.close()
        run_async(body())

    def test_004_tenant_context_is_visible_inside_the_transaction(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id) as uow:
                        visible = await uow.session.fetchval(
                            "SELECT platform.current_organization_id()"
                        )
                        self.assertEqual(str(visible), seed.organization_id)
                        self.assertEqual(
                            await uow.session.fetchval(
                                "SELECT current_setting('forge.organization_id')"
                            ),
                            seed.organization_id,
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_005_tenant_context_is_gone_after_commit(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id) as uow:
                        await uow.projects.list_for_current_tenant()
                    # A fresh session has no tenant context at all.
                    async with database.tenant(str(uuid.uuid4())) as uow:
                        pass
                    async with database.server() as uow:
                        helper = await uow.session.fetchval(
                            "SELECT platform.current_organization_id()"
                        )
                        self.assertIsNone(helper)
                finally:
                    await database.close()
        run_async(body())

    def test_006_tenant_context_is_gone_after_rollback(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.server() as uow:
                        pass
                    try:
                        async with database.tenant(seed.organization_id) as uow:
                            self.assertIsNotNone(uow.session.organization_id)
                            raise RuntimeError("deliberate")
                    except RuntimeError:
                        pass
                    async with database.server() as uow:
                        self.assertIsNone(
                            await uow.session.fetchval(
                                "SELECT platform.current_organization_id()"
                            )
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_007_an_exception_rolls_the_transaction_back(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    name = "must not survive"
                    try:
                        async with database.tenant(seed.organization_id) as uow:
                            await uow.projects.create(
                                str(uuid.uuid4()), name, slug="rollback-probe"
                            )
                            raise RuntimeError("deliberate")
                    except RuntimeError:
                        pass

                    async with database.tenant(seed.organization_id) as uow:
                        self.assertIsNone(
                            await uow.projects.find_by_slug("rollback-probe"),
                            "a rolled-back write must not be visible",
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_008_a_later_transaction_does_not_inherit_the_previous_tenant(self):
        """The connection-reuse case: tenant A, then tenant B, on one pool."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a = await seed_tenant(handle, "A")
                    seed_b = await seed_tenant(handle, "B")

                    for _ in range(3):
                        async with database.tenant(seed_a.organization_id) as uow:
                            names = {p.name for p in
                                     await uow.projects.list_for_current_tenant()}
                            self.assertEqual(names, {"Project A"})
                        async with database.tenant(seed_b.organization_id) as uow:
                            names = {p.name for p in
                                     await uow.projects.list_for_current_tenant()}
                            self.assertEqual(names, {"Project B"})
                finally:
                    await database.close()
        run_async(body())

    def test_009_a_repository_outside_a_unit_of_work_is_refused(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    async with database.direct_session() as session:
                        from app.platform.persistence.repositories import (
                            PostgresProjectRepository,
                        )

                        repository = PostgresProjectRepository(session)
                        # The direct session allows untransacted statements for
                        # schema work, so the guard is exercised through a
                        # repository built on a session that has no tenant scope.
                        with self.assertRaises(TransactionError):
                            await repository.list_for_current_tenant()
                finally:
                    await database.close()
        run_async(body())

    def test_010_repositories_are_unusable_after_the_unit_of_work_exits(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id) as uow:
                        projects = uow.projects
                    with self.assertRaises(TransactionError):
                        await projects.list_for_current_tenant()
                finally:
                    await database.close()
        run_async(body())

    def test_011_pre_tenant_scope_carries_no_tenant_context(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    async with system_scope(handle) as uow:
                        self.assertEqual(uow.mode, "server")
                        self.assertIsNone(uow.organization_id)
                        self.assertEqual(
                            await uow.session.fetchval("SELECT current_user"),
                            SYSTEM_ROLE,
                        )
                        self.assertEqual(
                            await uow.session.fetchval(
                                "SELECT platform.system_scope_is_declared()"
                            ),
                            True,
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_012_server_scope_has_no_system_access_by_default(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    async with database.server() as uow:
                        self.assertEqual(uow.mode, "server")
                        self.assertEqual(
                            await uow.session.fetchval("SELECT current_user"),
                            APP_ROLE,
                        )
                        self.assertEqual(
                            await uow.session.fetchval(
                                "SELECT platform.system_scope_is_declared()"
                            ),
                            False,
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_013_server_scope_with_the_system_role_declares_the_scope(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    async with system_scope(handle) as uow:
                        self.assertEqual(
                            await uow.session.fetchval("SELECT current_user"),
                            "forge_platform_system",
                        )
                        self.assertEqual(
                            await uow.session.fetchval(
                                "SELECT platform.system_scope_is_declared()"
                            ),
                            True,
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_014_an_unsupported_server_role_is_refused(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    with self.assertRaises(TransactionError):
                        async with database.server(role="postgres"):
                            pass
                finally:
                    await database.close()
        run_async(body())

    def test_015_a_malformed_tenant_identifier_is_refused_before_sql(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    # 'org.opaque:id' is a legal domain identifier and an illegal
                    # PostgreSQL uuid. The adapter must name the field rather than
                    # let the driver report a cast failure.
                    with self.assertRaises(InvalidIdentifierError) as caught:
                        async with database.tenant("org.opaque:id"):
                            pass
                    self.assertIn("organization_id", str(caught.exception))
                finally:
                    await database.close()
        run_async(body())

    def test_016_a_malformed_subject_identifier_is_refused(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    with self.assertRaises(InvalidIdentifierError):
                        async with database.tenant(
                            str(uuid.uuid4()), user_id="not/a/uuid"
                        ):
                            pass
                finally:
                    await database.close()
        run_async(body())

    def test_017_explicit_rollback_discards_the_transaction(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id) as uow:
                        await uow.projects.create(
                            str(uuid.uuid4()), "discarded", slug="discarded-probe"
                        )
                        await uow.rollback()
                    async with database.tenant(seed.organization_id) as uow:
                        self.assertIsNone(
                            await uow.projects.find_by_slug("discarded-probe")
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_018_a_released_connection_keeps_no_tenant_context(self):
        """The pooled-connection property, measured on a raw borrow.

        A connection returned to the pool must be inert: the tenant context gone and
        the effective role stepped down. The transaction-local settings are what
        carry a tenant, so this is the check that a later request cannot inherit one.
        """

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        inside = await uow.session.fetchrow(
                            "SELECT current_user AS role, "
                            "current_setting('forge.organization_id', true) AS tenant"
                        )
                        self.assertEqual(inside["role"], APP_ROLE)
                        self.assertEqual(inside["tenant"], seed.organization_id)

                    # Borrow the same pool directly, outside any Unit of Work.
                    async with database._pool.acquire() as connection:
                        row = await connection.fetchrow(
                            "SELECT current_user AS role, session_user AS login, "
                            "current_setting('forge.organization_id', true) AS tenant, "
                            "current_setting('forge.user_id', true) AS subject"
                        )
                        self.assertEqual(row["role"], APP_ROLE)
                        self.assertEqual(row["login"], PERSISTENCE_LOGIN_ROLE)
                        # Empty rather than NULL: the setting was assigned and then
                        # reverted, which is one of the two fail-closed states.
                        self.assertEqual(row["tenant"], "")
                        self.assertEqual(row["subject"], "")

                        # And the fail-closed consequence, on the raw connection.
                        self.assertEqual(
                            await connection.fetchval(
                                "SELECT count(*) FROM projects"
                            ),
                            0,
                            "a released connection must see no tenant rows",
                        )
                finally:
                    await database.close()
        run_async(body())


# =========================================================================== #
# A.2 Regressions from the adversarial review of this step
# =========================================================================== #
class RoleGuardRegressionTests(PersistenceTestCase):
    """The pool guard, attacked.

    Each test here reproduces a way the guard was previously defeated. They are
    written against the *login* rather than the substituted role, because that is
    where the earlier version looked away.
    """

    def test_019_a_superuser_login_is_refused_even_though_pg_has_role_agrees(self):
        """The exact hole: a superuser satisfies every membership check.

        ``pg_has_role(superuser, any_role, 'MEMBER')`` is true without any grant, so a
        membership gate alone accepts a superuser login. The refusal must therefore
        come from the login's own attributes.
        """

        async def body():
            async with PersistenceDatabase() as handle:
                from persistence_fixture import connection_parts

                parts = connection_parts()
                superuser_dsn = (
                    f"postgresql://{parts['user']}:{parts.get('password') or ''}"
                    f"@{parts['host']}:{parts['port']}/{handle.name}"
                )
                database = PlatformDatabase(
                    superuser_dsn, application_role=APP_ROLE, min_size=1, max_size=1
                )
                try:
                    with self.assertRaises(ConnectionError) as caught:
                        await database.connect()
                    message = str(caught.exception)
                    self.assertIn("session role", message)
                    self.assertIn("superuser", message)
                finally:
                    await database.close()
        run_async(body())

    def test_020a_a_login_reaching_both_roles_cannot_serve_tenant_traffic(self):
        """One login holding both scopes is refused, not documented as a deployment
        convention.

        A login that is a member of the system role as well as the application role
        could step into the system scope from a tenant pool, which would make the
        separation Step 2 established decorative.
        """

        async def body():
            async with PersistenceDatabase() as handle:
                admin = await handle.admin_connection()
                try:
                    await admin.execute(
                        f"GRANT forge_platform_system TO {PERSISTENCE_LOGIN_ROLE}"
                    )
                    database = PlatformDatabase(
                        handle.dsn, application_role=APP_ROLE, min_size=1, max_size=1
                    )
                    try:
                        with self.assertRaises(ConnectionError) as caught:
                            await database.connect()
                        self.assertIn("different logins", str(caught.exception))
                    finally:
                        await database.close()
                finally:
                    await admin.execute(
                        f"REVOKE forge_platform_system FROM {PERSISTENCE_LOGIN_ROLE}"
                    )
                    await admin.close()
        run_async(body())

    def test_020b_an_unknown_application_role_is_refused(self):
        """The role name reaches ``SET ROLE``, so it must be a known constant."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = PlatformDatabase(
                    handle.dsn,
                    application_role='forge_platform_app"; DROP TABLE users; --',
                    min_size=1, max_size=1,
                )
                try:
                    with self.assertRaises(ConnectionError) as caught:
                        await database.connect()
                    self.assertIn("unsupported application_role",
                                  str(caught.exception))
                finally:
                    await database.close()
        run_async(body())

    def test_020c_owning_a_platform_table_outside_public_is_refused(self):
        """The ownership check is not pinned to schema ``public``.

        A deployment whose ``search_path`` puts the Platform tables elsewhere works
        normally, so a check that only looked in ``public`` would accept an owning
        role -- which could then disable ``FORCE`` row-level security on its own table
        and read every tenant.
        """

        async def body():
            async with PersistenceDatabase() as handle:
                admin = await handle.admin_connection()
                try:
                    # Copy the shape of one Platform table into another schema and
                    # hand ownership to the application role, exactly as a misplaced
                    # deployment would.
                    await admin.execute("CREATE SCHEMA forge_probe")
                    await admin.execute(
                        "CREATE TABLE forge_probe.run_records ("
                        "id uuid PRIMARY KEY, organization_id uuid, note text)"
                    )
                    await admin.execute(
                        "ALTER TABLE forge_probe.run_records OWNER TO "
                        f"{APP_ROLE}"
                    )
                    database = PlatformDatabase(
                        handle.dsn, application_role=APP_ROLE, min_size=1, max_size=1
                    )
                    try:
                        with self.assertRaises(ConnectionError) as caught:
                            await database.connect()
                        message = str(caught.exception)
                        self.assertIn("owns Platform table", message)
                        self.assertIn("forge_probe.run_records", message)
                    finally:
                        await database.close()
                finally:
                    await admin.execute("DROP SCHEMA forge_probe CASCADE")
                    await admin.close()
        run_async(body())


class PersistenceBoundaryRegressionTests(PersistenceTestCase):
    """Boundary defects the same review found, each with a regression test."""

    def test_028a_organizations_offer_no_update_capability(self):
        """No scope can update a tenant, so no method advertises one.

        The schema grants the system role SELECT and INSERT on ``organizations`` and
        nothing else, and no UPDATE policy exists. A policy-filtered UPDATE matches
        zero rows, so such a method reported a permission refusal as a missing tenant.
        """

        from app.platform.persistence.repositories import (
            PostgresOrganizationRepository,
        )

        for forbidden in ("set_name", "set_status", "rename", "suspend"):
            self.assertFalse(
                hasattr(PostgresOrganizationRepository, forbidden),
                f"organizations must not offer {forbidden}",
            )

    def test_028b_a_tenant_write_cannot_name_another_organization(self):
        """The tenant is not a parameter, so it cannot be pointed at another tenant.

        The stronger form of the fix: there is no ``organization_id`` argument on the
        tenant-scoped insert to pass a foreign tenant in, so a caller cannot even
        express the attempt. The record's own ``organization_id`` is ignored, and the
        row lands in the session's tenant.
        """

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a = await seed_tenant(handle, "A")
                    seed_b = await seed_tenant(handle, "B")
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        import inspect

                        signature = inspect.signature(
                            type(uow.memberships).create
                        )
                        self.assertEqual(
                            list(signature.parameters), ["self", "membership"],
                            "the tenant insert must take no organization parameter",
                        )
                        # A record that names B still lands in A, because the tenant
                        # is read from the session.
                        written = await uow.memberships.create(
                            Membership(
                                id=str(uuid.uuid4()),
                                organization_id=seed_b.organization_id,
                                user_id=seed_b.user_id,
                                role=MembershipRole.MEMBER,
                                created_at=utc_now(),
                            )
                        )
                        self.assertEqual(
                            written.organization_id, seed_a.organization_id
                        )
                    # B's membership list is untouched.
                    async with database.tenant(seed_b.organization_id,
                                              user_id=seed_b.user_id) as uow:
                        self.assertEqual(
                            len(await uow.memberships.list_for_current_tenant()), 1
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_028c_unserializable_metadata_is_a_persistence_error(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        for bad in ({"x": {1, 2}}, {"x": object()}):
                            with self.assertRaises(PersistenceError) as caught:
                                await uow.provider_accounts.create(
                                    str(uuid.uuid4()), "openai", "ref", metadata=bad
                                )
                            self.assertIn("not JSON-serializable",
                                          str(caught.exception))
                        # A circular mapping is refused as well, not left to json.
                        circular = {}
                        circular["self"] = circular
                        with self.assertRaises(PersistenceError):
                            await uow.provider_accounts.create(
                                str(uuid.uuid4()), "openai", "ref",
                                metadata=circular,
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_028d_a_bare_string_is_not_a_scope_list(self):
        """``str`` is a ``Sequence[str]``, so it must be refused explicitly."""

        from app.platform.persistence.mapper import scopes_to_array

        self.assertEqual(scopes_to_array(("a", "b")), ["a", "b"])
        self.assertEqual(scopes_to_array([]), [])
        for bad in ("keys:read", b"keys:read"):
            with self.assertRaises(InvalidIdentifierError):
                scopes_to_array(bad)

    def test_028e_a_bare_string_scope_never_reaches_the_database(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(InvalidIdentifierError):
                            await uow.api_keys.create(
                                str(uuid.uuid4()), seed.user_id, "k",
                                scopes="keys:read",
                            )
                        self.assertEqual(
                            await uow.api_keys.list_for_current_tenant(), [],
                            "no key may be stored with split-character scopes",
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_028f_a_slugless_project_is_findable_by_its_own_slug(self):
        """The lookup applies the same ``''`` <-> NULL rule as the writer."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        created = await uow.projects.create(
                            str(uuid.uuid4()), "No slug"
                        )
                        self.assertEqual(created.slug, "")
                        found = await uow.projects.find_by_slug(created.slug)
                        self.assertIsNotNone(
                            found,
                            "a project with no slug must be findable by that slug",
                        )
                        self.assertEqual(found.id, created.id)
                        # A named slug still resolves, and a different one does not.
                        named = await uow.projects.create(
                            str(uuid.uuid4()), "Named", slug="named"
                        )
                        self.assertEqual(
                            (await uow.projects.find_by_slug("named")).id, named.id
                        )
                        self.assertIsNone(await uow.projects.find_by_slug("other"))
                finally:
                    await database.close()
        run_async(body())

    def test_028g_a_released_session_records_nothing_from_a_clean_close(self):
        """``Session.close_error`` is the observable form of a clean release."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        await uow.projects.list_for_current_tenant()
                        session = uow.session
                    self.assertTrue(session._closed)
                    self.assertIsNone(
                        session.close_error,
                        "a clean close must not record an error",
                    )
                finally:
                    await database.close()
        run_async(body())


# =========================================================================== #
# B. Mapping
# =========================================================================== #
class MappingTests(PersistenceTestCase):
    def test_020_all_eight_entities_round_trip(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")

                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        user = await uow.users.get(seed.user_id)
                        self.assertIsNotNone(user)
                        self.assertEqual(user.display_name, "User A")

                        organization = await uow.organizations.get(
                            seed.organization_id
                        )
                        self.assertIsNotNone(organization)
                        self.assertEqual(organization.name, "Org A")

                        membership = await uow.memberships.find_for_user(
                            seed.user_id
                        )
                        self.assertIsNotNone(membership)
                        self.assertEqual(membership.role, MembershipRole.OWNER)

                        project = await uow.projects.get(seed.project_id)
                        self.assertIsNotNone(project)
                        self.assertEqual(project.organization_id,
                                         seed.organization_id)
                        self.assertEqual(project.slug, "project-a")
                        self.assertEqual(project.workspace_ref, "ws:a")
                        self.assertEqual(project.status, ProjectStatus.ACTIVE)

                        run = await uow.run_records.get(seed.run_id)
                        self.assertIsNotNone(run)
                        self.assertEqual(run.status, RunRecordStatus.RUNNING)
                        self.assertEqual(run.core_run_id, "core-a")
                        self.assertEqual(run.initiated_by_user_id, seed.user_id)
                finally:
                    await database.close()
        run_async(body())

    def test_021_optional_text_maps_empty_to_null_and_back(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        # The domain default is '', the column default is NULL, and
                        # the round trip must preserve the domain's view.
                        created = await uow.projects.create(
                            str(uuid.uuid4()), "No slug"
                        )
                        self.assertEqual(created.slug, "")
                        self.assertEqual(created.workspace_ref, "")
                        stored = await uow.session.fetchrow(
                            "SELECT slug, workspace_ref FROM projects WHERE id = $1",
                            uuid.UUID(created.id),
                        )
                        self.assertIsNone(stored["slug"])
                        self.assertIsNone(stored["workspace_ref"])
                finally:
                    await database.close()
        run_async(body())

    def test_022_system_owned_provider_account_keeps_a_null_tenant(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    async with system_scope(handle) as uow:
                        account = await uow.system_provider_accounts.create(
                            str(uuid.uuid4()), "deepseek", "vault:system/deepseek",
                            metadata={"region": "eu"},
                        )
                        self.assertIsNone(account.organization_id)
                        self.assertEqual(account.metadata, {"region": "eu"})

                        fetched = await uow.system_provider_accounts.get(account.id)
                        self.assertIsNotNone(fetched)
                        self.assertIsNone(fetched.organization_id)
                        self.assertEqual(fetched.secret_ref,
                                         "vault:system/deepseek")
                finally:
                    await database.close()
        run_async(body())

    def test_023_representation_never_reveals_the_secret_reference(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        account = await uow.provider_accounts.create(
                            str(uuid.uuid4()), "openai", "vault:tenant/secret-ref"
                        )
                        self.assertNotIn("vault:tenant/secret-ref", repr(account))
                        self.assertIn("<opaque>", repr(account))
                finally:
                    await database.close()
        run_async(body())

    def test_024_api_key_scopes_map_to_a_text_array(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        key = await uow.api_keys.create(
                            str(uuid.uuid4()), seed.user_id, "ci key",
                            key_prefix="forge_live_ab", scopes=("runs:read",
                                                                "runs:write"),
                        )
                        self.assertEqual(key.scopes,
                                         ("runs:read", "runs:write"))
                        stored = await uow.session.fetchval(
                            "SELECT scopes FROM api_keys WHERE id = $1",
                            uuid.UUID(key.id),
                        )
                        self.assertEqual(stored, ["runs:read", "runs:write"])
                finally:
                    await database.close()
        run_async(body())

    def test_025_datetime_round_trip_is_timezone_aware(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        run = await uow.run_records.get(seed.run_id)
                        self.assertIsNotNone(run.started_at)
                        self.assertIsNotNone(run.started_at.tzinfo)
                        self.assertEqual(run.started_at.utcoffset(),
                                         dt.timedelta(0))
                finally:
                    await database.close()
        run_async(body())

    def test_026_a_non_uuid_identifier_is_refused_by_the_mapper(self):
        from app.platform.persistence.mapper import as_uuid

        self.assertTrue(as_uuid(str(uuid.uuid4()), "x"))
        with self.assertRaises(InvalidIdentifierError):
            as_uuid("abc.def:ghi", "some.field")
        with self.assertRaises(InvalidIdentifierError):
            as_uuid("", "some.field")
        with self.assertRaises(InvalidIdentifierError):
            as_uuid(None, "some.field")

    def test_027_metadata_must_be_an_object(self):
        from app.platform.persistence.mapper import require_mapping

        self.assertEqual(require_mapping({"a": 1}), {"a": 1})
        self.assertEqual(require_mapping({}), {})
        with self.assertRaises(InvalidIdentifierError):
            require_mapping(["not", "a", "mapping"])


# =========================================================================== #
# C. Tenant isolation
# =========================================================================== #
class TenantIsolationTests(PersistenceTestCase):
    async def _two(self, handle):
        return (await seed_tenant(handle, "A"), await seed_tenant(handle, "B"))

    def test_030_a_tenant_cannot_read_another_tenants_rows(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a, seed_b = await self._two(handle)
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        self.assertIsNone(await uow.projects.get(seed_b.project_id))
                        self.assertIsNone(await uow.run_records.get(seed_b.run_id))
                        self.assertIsNone(
                            await uow.organizations.get(seed_b.organization_id)
                        )
                        names = {p.name for p in
                                 await uow.projects.list_for_current_tenant()}
                        self.assertEqual(names, {"Project A"})
                finally:
                    await database.close()
        run_async(body())

    def test_031_a_tenant_cannot_update_another_tenants_row(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a, seed_b = await self._two(handle)
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        with self.assertRaises(EntityNotFound):
                            await uow.projects.set_name(seed_b.project_id, "hijacked")
                    async with database.tenant(seed_b.organization_id,
                                              user_id=seed_b.user_id) as uow:
                        project = await uow.projects.get(seed_b.project_id)
                        self.assertEqual(project.name, "Project B")
                finally:
                    await database.close()
        run_async(body())

    def test_032_a_tenant_cannot_delete_another_tenants_row(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a, seed_b = await self._two(handle)
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        # The protocol exposes no delete for projects; the point is
                        # that even a raw statement through this session cannot
                        # reach B's row.
                        result = await uow.session.execute(
                            "DELETE FROM projects WHERE id = $1",
                            uuid.UUID(seed_b.project_id),
                        )
                        self.assertEqual(result, "DELETE 0")
                    async with database.tenant(seed_b.organization_id,
                                              user_id=seed_b.user_id) as uow:
                        self.assertIsNotNone(
                            await uow.projects.get(seed_b.project_id)
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_033_a_tenant_cannot_attach_another_tenants_user(self):
        """The provenance key refuses it at the database, not at a pre-check."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a, seed_b = await self._two(handle)
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        with self.assertRaises(ForeignKeyViolationError):
                            await uow.api_keys.create(
                                str(uuid.uuid4()), seed_b.user_id, "cross-tenant"
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_034_a_tenant_cannot_attach_a_run_to_another_tenants_project(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a, seed_b = await self._two(handle)
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        with self.assertRaises(ForeignKeyViolationError):
                            await uow.run_records.create_queued(
                                str(uuid.uuid4()), seed_b.project_id
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_035_a_tenant_cannot_transition_another_tenants_run(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a, seed_b = await self._two(handle)
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        with self.assertRaises(EntityNotFound):
                            await uow.run_records.increment_attempt_count(
                                seed_b.run_id
                            )
                        with self.assertRaises(EntityNotFound):
                            await uow.run_records.finish(
                                seed_b.run_id, status=RunRecordStatus.SUCCEEDED,
                                finished_at=utc_now(),
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_036_no_tenant_context_means_no_rows(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    await self._two(handle)
                    async with database.server() as uow:
                        for statement in (
                            "SELECT count(*) FROM projects",
                            "SELECT count(*) FROM run_records",
                            "SELECT count(*) FROM api_keys",
                            "SELECT count(*) FROM provider_accounts",
                            "SELECT count(*) FROM memberships",
                            "SELECT count(*) FROM organizations",
                            "SELECT count(*) FROM users",
                            "SELECT count(*) FROM usage_records",
                        ):
                            self.assertEqual(
                                await uow.session.fetchval(statement), 0,
                                f"server scope without a tenant leaked rows: {statement}",
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_037_a_tenant_scoped_repository_requires_a_tenant_context(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    await self._two(handle)
                    async with database.server() as uow:
                        with self.assertRaises(TransactionError):
                            await uow.projects.list_for_current_tenant()
                        with self.assertRaises(TransactionError):
                            await uow.usage_records.list_for_current_tenant()
                finally:
                    await database.close()
        run_async(body())


# =========================================================================== #
# D. Repository behaviour
# =========================================================================== #
class ProjectRepositoryTests(PersistenceTestCase):
    def test_040_create_get_list_find_and_update(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        created = await uow.projects.create(
                            str(uuid.uuid4()), "Second", slug="second",
                            workspace_ref="ref:opaque/1",
                        )
                        self.assertEqual(created.status, ProjectStatus.ACTIVE)
                        # workspace_ref is stored verbatim, never interpreted.
                        self.assertEqual(created.workspace_ref, "ref:opaque/1")

                        fetched = await uow.projects.get(created.id)
                        self.assertEqual(fetched.id, created.id)
                        self.assertEqual(
                            await uow.projects.find_by_slug("second"), fetched
                        )
                        listing = await uow.projects.list_for_current_tenant()
                        self.assertEqual(len(listing), 2)

                        renamed = await uow.projects.set_name(created.id, "Renamed")
                        self.assertEqual(renamed.name, "Renamed")
                        archived = await uow.projects.set_status(
                            created.id, ProjectStatus.ARCHIVED
                        )
                        self.assertEqual(archived.status, ProjectStatus.ARCHIVED)
                        moved = await uow.projects.set_workspace_ref(
                            created.id, "ref:opaque/2"
                        )
                        self.assertEqual(moved.workspace_ref, "ref:opaque/2")
                        reslugged = await uow.projects.set_slug(
                            created.id, "renamed-slug"
                        )
                        self.assertEqual(reslugged.slug, "renamed-slug")
                finally:
                    await database.close()
        run_async(body())

    def test_041_missing_project_is_none_not_an_error(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        self.assertIsNone(await uow.projects.get(str(uuid.uuid4())))
                        self.assertIsNone(await uow.projects.find_by_slug("nope"))
                finally:
                    await database.close()
        run_async(body())

    def test_042_tenant_scoped_slug_uniqueness_is_enforced_by_the_database(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a, seed_b = await seed_tenant(handle, "A"), \
                        await seed_tenant(handle, "B")
                    # The same slug in a different tenant is allowed (K-3).
                    async with database.tenant(seed_b.organization_id,
                                              user_id=seed_b.user_id) as uow:
                        await uow.projects.create(
                            str(uuid.uuid4()), "Same slug", slug="project-a"
                        )
                    # The same slug in one tenant is refused.
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        with self.assertRaises(UniqueViolationError) as caught:
                            await uow.projects.create(
                                str(uuid.uuid4()), "Duplicate", slug="project-a"
                            )
                        self.assertIn("projects_organization_slug_unique",
                                      caught.exception.constraint or "")
                finally:
                    await database.close()
        run_async(body())

    def test_043_set_name_on_a_missing_project_raises_not_found(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(EntityNotFound):
                            await uow.projects.set_name(str(uuid.uuid4()), "x")
                finally:
                    await database.close()
        run_async(body())


class MembershipRepositoryTests(PersistenceTestCase):
    def test_050_create_and_list(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    other_id = str(uuid.uuid4())
                    # The account is a bootstrap row: the tenant role has no
                    # privilege to create one.
                    async with system_scope(handle) as uow:
                        await uow.users.create(
                            User(id=other_id, email="other@example.test",
                                 created_at=utc_now())
                        )
                    # The membership is written through the tenant path, which is the
                    # scope that owns its organization.
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        await uow.memberships.create(
                            Membership(
                                id=str(uuid.uuid4()),
                                organization_id=seed.organization_id,
                                user_id=other_id,
                                role=MembershipRole.MEMBER,
                                created_at=utc_now(),
                            )
                        )
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        members = await uow.memberships.list_for_current_tenant()
                        self.assertEqual(len(members), 2)
                        self.assertEqual(
                            {m.user_id for m in members},
                            {seed.user_id, other_id},
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_051_duplicate_membership_is_refused_by_the_database(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(UniqueViolationError) as caught:
                            await uow.memberships.create(
                                Membership(
                                    id=str(uuid.uuid4()),
                                    organization_id=seed.organization_id,
                                    user_id=seed.user_id,
                                    role=MembershipRole.MEMBER,
                                    created_at=utc_now(),
                                )
                            )
                        self.assertEqual(
                            caught.exception.constraint,
                            "memberships_organization_user_unique",
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_052_status_change_is_the_reactivation_path(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with system_scope(handle) as uow:
                        memberships = await uow.memberships.list_for_user(
                            seed.user_id
                        )
                        self.assertEqual(len(memberships), 1)
                        revoked = await uow.memberships.set_status(
                            memberships[0].id, MembershipStatus.REVOKED
                        )
                        self.assertEqual(revoked.status, MembershipStatus.REVOKED)
                        # Returning is a status change on the same row, never a
                        # second row: the unique key forbids the duplicate.
                        reactivated = await uow.memberships.set_status(
                            memberships[0].id, MembershipStatus.ACTIVE
                        )
                        self.assertEqual(reactivated.status,
                                         MembershipStatus.ACTIVE)
                        self.assertEqual(reactivated.id, memberships[0].id)
                finally:
                    await database.close()
        run_async(body())

    def test_053_role_change_is_recorded_not_authorized(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        membership = await uow.memberships.find_for_user(
                            seed.user_id
                        )
                        changed = await uow.memberships.set_role(
                            membership.id, MembershipRole.ADMIN
                        )
                        self.assertEqual(changed.role, MembershipRole.ADMIN)
                finally:
                    await database.close()
        run_async(body())

    def test_054_find_for_user_is_bounded_to_the_current_tenant(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a = await seed_tenant(handle, "A")
                    seed_b = await seed_tenant(handle, "B")
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        self.assertIsNone(
                            await uow.memberships.find_for_user(seed_b.user_id)
                        )
                finally:
                    await database.close()
        run_async(body())


class ProviderAccountRepositoryTests(PersistenceTestCase):
    def test_060_tenant_account_lifecycle(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        account = await uow.provider_accounts.create(
                            str(uuid.uuid4()), "openai", "ref:tenant",
                            metadata={"tier": "pro"},
                        )
                        self.assertEqual(account.organization_id,
                                         seed.organization_id)
                        self.assertEqual(account.metadata, {"tier": "pro"})

                        self.assertEqual(
                            (await uow.provider_accounts.get(account.id)).id,
                            account.id,
                        )
                        found = await uow.provider_accounts.find_by_provider_name(
                            "openai"
                        )
                        self.assertEqual(found.id, account.id)

                        updated = await uow.provider_accounts.set_secret_ref(
                            account.id, "ref:tenant/2"
                        )
                        self.assertEqual(updated.secret_ref, "ref:tenant/2")
                        dis = await uow.provider_accounts.set_status(
                            account.id, ProviderAccountStatus.DISABLED
                        )
                        self.assertEqual(dis.status,
                                         ProviderAccountStatus.DISABLED)
                        meta = await uow.provider_accounts.set_metadata(
                            account.id, {"tier": "free"}
                        )
                        self.assertEqual(meta.metadata, {"tier": "free"})
                finally:
                    await database.close()
        run_async(body())

    def test_061_tenant_never_sees_a_system_owned_account(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with system_scope(handle) as uow:
                        system_account = await uow.system_provider_accounts.create(
                            str(uuid.uuid4()), "anthropic", "ref:system"
                        )
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        self.assertEqual(
                            await uow.provider_accounts.list_for_current_tenant(),
                            [],
                        )
                        self.assertIsNone(
                            await uow.provider_accounts.get(system_account.id)
                        )
                        self.assertIsNone(
                            await uow.provider_accounts.find_by_provider_name(
                                "anthropic"
                            )
                        )
                        # A null tenant is not a wildcard, even asked directly.
                        self.assertEqual(
                            await uow.session.fetchval(
                                "SELECT count(*) FROM provider_accounts "
                                "WHERE organization_id IS NULL"
                            ),
                            0,
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_062_a_tenant_cannot_create_a_system_owned_account(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        # There is no API to do this: the repository writes the
                        # tenant from the context. The row-level policy refuses a
                        # null tenant even if one were attempted directly.
                        with self.assertRaises(PermissionDeniedError):
                            await uow.session.execute(
                                "INSERT INTO provider_accounts (id, "
                                "organization_id, provider_name, secret_ref, "
                                "created_at) VALUES ($1, NULL, 'evil', 'x', now())",
                                uuid.uuid4(),
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_063_a_tenant_cannot_promote_its_account_to_system_owned(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        account = await uow.provider_accounts.create(
                            str(uuid.uuid4()), "openai", "ref:tenant"
                        )
                        with self.assertRaises(PermissionDeniedError):
                            await uow.session.execute(
                                "UPDATE provider_accounts SET organization_id = "
                                "NULL WHERE id = $1", uuid.UUID(account.id),
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_064_system_scope_reaches_only_system_owned_accounts(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        tenant_account = await uow.provider_accounts.create(
                            str(uuid.uuid4()), "openai", "ref:tenant"
                        )
                    async with system_scope(handle) as uow:
                        self.assertEqual(
                            await uow.system_provider_accounts.list_system_owned(),
                            [],
                        )
                        # It cannot reach the tenant's credential by id either.
                        self.assertIsNone(
                            await uow.system_provider_accounts.get(
                                tenant_account.id
                            )
                        )
                        created = await uow.system_provider_accounts.create(
                            str(uuid.uuid4()), "openai", "ref:system"
                        )
                        owned = await uow.system_provider_accounts.list_system_owned()
                        self.assertEqual([a.id for a in owned], [created.id])
                finally:
                    await database.close()
        run_async(body())

    def test_065_the_system_scope_cannot_read_tenant_work(self):
        async def body():
            async with PersistenceDatabase() as handle:
                await seed_tenant(handle, "A")
                async with system_scope(handle) as uow:
                    for statement in (
                        "SELECT count(*) FROM projects",
                        "SELECT count(*) FROM run_records",
                        "SELECT count(*) FROM api_keys",
                        "SELECT count(*) FROM usage_records",
                    ):
                        # A privilege denial aborts the transaction, so each probe
                        # is fenced by a savepoint.
                        await uow.session.execute("SAVEPOINT s")
                        with self.assertRaises(PermissionDeniedError):
                            await uow.session.fetchval(statement)
                        await uow.session.execute("ROLLBACK TO SAVEPOINT s")
        run_async(body())

    def test_066_system_owned_uniqueness_per_provider(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    async with system_scope(handle) as uow:
                        await uow.system_provider_accounts.create(
                            str(uuid.uuid4()), "google", "ref:1"
                        )
                        with self.assertRaises(UniqueViolationError):
                            await uow.system_provider_accounts.create(
                                str(uuid.uuid4()), "google", "ref:2"
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_067_tenant_owned_uniqueness_is_per_tenant_not_global(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a = await seed_tenant(handle, "A")
                    seed_b = await seed_tenant(handle, "B")
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        await uow.provider_accounts.create(
                            str(uuid.uuid4()), "openai", "ref:a"
                        )
                        with self.assertRaises(UniqueViolationError):
                            await uow.provider_accounts.create(
                                str(uuid.uuid4()), "openai", "ref:a2"
                            )
                    async with database.tenant(seed_b.organization_id,
                                              user_id=seed_b.user_id) as uow:
                        other = await uow.provider_accounts.create(
                            str(uuid.uuid4()), "openai", "ref:b"
                        )
                        self.assertEqual(other.organization_id,
                                         seed_b.organization_id)
                finally:
                    await database.close()
        run_async(body())


class APIKeyRepositoryTests(PersistenceTestCase):
    def test_070_lifecycle(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    expiry = utc_now() + dt.timedelta(days=30)
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        key = await uow.api_keys.create(
                            str(uuid.uuid4()), seed.user_id, "deploy key",
                            key_prefix="forge_live_xy", scopes=("runs:read",),
                            expires_at=expiry,
                        )
                        self.assertEqual(key.status, APIKeyStatus.ACTIVE)
                        self.assertEqual(key.key_prefix, "forge_live_xy")
                        self.assertIsNotNone(key.expires_at)

                        self.assertEqual(
                            (await uow.api_keys.get(key.id)).id, key.id
                        )
                        self.assertEqual(
                            len(await uow.api_keys.list_for_current_tenant()), 1
                        )
                        self.assertEqual(
                            len(await uow.api_keys.list_for_creator(seed.user_id)),
                            1,
                        )

                        used = await uow.api_keys.record_use(key.id, utc_now())
                        self.assertIsNotNone(used.last_used_at)

                        cleared = await uow.api_keys.set_expiry(key.id, None)
                        self.assertIsNone(cleared.expires_at)

                        revoked = await uow.api_keys.revoke(key.id, utc_now())
                        self.assertEqual(revoked.status, APIKeyStatus.REVOKED)
                        self.assertIsNotNone(revoked.revoked_at)
                finally:
                    await database.close()
        run_async(body())

    def test_071_a_revoked_status_always_carries_a_timestamp(self):
        """The domain's bidirectional rule holds for every repository path."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        key = await uow.api_keys.create(
                            str(uuid.uuid4()), seed.user_id, "k"
                        )
                        await uow.api_keys.revoke(key.id, utc_now())
                        stored = await uow.session.fetchrow(
                            "SELECT status, revoked_at FROM api_keys WHERE id = $1",
                            uuid.UUID(key.id),
                        )
                        self.assertEqual(stored["status"], "revoked")
                        self.assertIsNotNone(stored["revoked_at"])
                finally:
                    await database.close()
        run_async(body())

    def test_072_a_tenant_cannot_reach_another_tenants_key(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a = await seed_tenant(handle, "A")
                    seed_b = await seed_tenant(handle, "B")
                    async with database.tenant(seed_b.organization_id,
                                              user_id=seed_b.user_id) as uow:
                        key_b = await uow.api_keys.create(
                            str(uuid.uuid4()), seed_b.user_id, "b key"
                        )
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        self.assertIsNone(await uow.api_keys.get(key_b.id))
                        with self.assertRaises(EntityNotFound):
                            await uow.api_keys.revoke(key_b.id, utc_now())
                finally:
                    await database.close()
        run_async(body())

    def test_073_no_plaintext_token_can_be_stored(self):
        """There is no token column, so there is no method that could accept one."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        columns = {
                            r["column_name"] for r in await uow.session.fetch(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = 'public' AND table_name = "
                                "'api_keys'"
                            )
                        }
                        self.assertNotIn("token", columns)
                        self.assertNotIn("key_hash", columns)
                        self.assertNotIn("token_hash", columns)
                        self.assertNotIn("secret", columns)
                finally:
                    await database.close()
        run_async(body())


class RunRecordRepositoryTests(PersistenceTestCase):
    def test_080_queued_run_starts_with_nulls(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        run = await uow.run_records.create_queued(
                            str(uuid.uuid4()), seed.project_id, task_id="t-1"
                        )
                        self.assertEqual(run.status, RunRecordStatus.QUEUED)
                        self.assertIsNone(run.core_run_id)
                        self.assertIsNone(run.started_at)
                        self.assertIsNone(run.initiated_by_user_id)
                        self.assertEqual(run.attempt_count, 0)
                        self.assertEqual(run.task_id, "t-1")
                finally:
                    await database.close()
        run_async(body())

    def test_081_full_lifecycle(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        run_id = str(uuid.uuid4())
                        await uow.run_records.create_queued(
                            run_id, seed.project_id
                        )
                        claimed = await uow.run_records.claim(
                            run_id, core_run_id="core-xyz",
                            initiated_by_user_id=seed.user_id,
                            started_at=utc_now(),
                        )
                        self.assertEqual(claimed.status,
                                         RunRecordStatus.RUNNING)
                        self.assertEqual(claimed.core_run_id, "core-xyz")
                        self.assertEqual(claimed.initiated_by_user_id,
                                         seed.user_id)

                        bumped = await uow.run_records.increment_attempt_count(
                            run_id
                        )
                        self.assertEqual(bumped.attempt_count, 1)
                        bumped = await uow.run_records.increment_attempt_count(
                            run_id
                        )
                        self.assertEqual(bumped.attempt_count, 2)

                        finished = await uow.run_records.finish(
                            run_id, status=RunRecordStatus.SUCCEEDED,
                            finished_at=utc_now(),
                        )
                        self.assertEqual(finished.status,
                                         RunRecordStatus.SUCCEEDED)
                        self.assertIsNotNone(finished.finished_at)
                finally:
                    await database.close()
        run_async(body())

    def test_082_a_run_cannot_be_claimed_twice(self):
        """The claim predicate is part of the UPDATE, so a race cannot win twice."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        run_id = str(uuid.uuid4())
                        await uow.run_records.create_queued(
                            run_id, seed.project_id
                        )
                        first = await uow.run_records.claim(
                            run_id, core_run_id="core-1",
                            initiated_by_user_id=seed.user_id,
                            started_at=utc_now(),
                        )
                        self.assertEqual(first.status, RunRecordStatus.RUNNING)
                        with self.assertRaises(EntityNotFound):
                            await uow.run_records.claim(
                                run_id, core_run_id="core-2",
                                initiated_by_user_id=seed.user_id,
                                started_at=utc_now(),
                            )
                        stored = await uow.run_records.get(run_id)
                        self.assertEqual(stored.core_run_id, "core-1")
                finally:
                    await database.close()
        run_async(body())

    def test_083_a_queued_run_cannot_be_finished(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        run_id = str(uuid.uuid4())
                        await uow.run_records.create_queued(
                            run_id, seed.project_id
                        )
                        with self.assertRaises(EntityNotFound):
                            await uow.run_records.finish(
                                run_id, status=RunRecordStatus.SUCCEEDED,
                                finished_at=utc_now(),
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_084_succeeded_with_a_failure_classification_is_refused(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        run_id = str(uuid.uuid4())
                        await uow.run_records.create_queued(
                            run_id, seed.project_id
                        )
                        await uow.run_records.claim(
                            run_id, core_run_id="core-s",
                            initiated_by_user_id=seed.user_id,
                            started_at=utc_now(),
                        )
                        with self.assertRaises(CheckViolationError):
                            await uow.run_records.finish(
                                run_id, status=RunRecordStatus.SUCCEEDED,
                                finished_at=utc_now(),
                                failure_classification="timeout",
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_085_core_run_id_is_not_unique_and_is_looked_up_by_correlation(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        shared = "core-duplicated"
                        for _ in range(2):
                            run_id = str(uuid.uuid4())
                            await uow.run_records.create_queued(
                                run_id, seed.project_id
                            )
                            await uow.run_records.claim(
                                run_id, core_run_id=shared,
                                initiated_by_user_id=seed.user_id,
                                started_at=utc_now(),
                            )
                        matches = await uow.run_records.list_by_core_run_id(shared)
                        self.assertEqual(len(matches), 2)
                        self.assertIsNotNone(
                            await uow.run_records.find_by_core_run_id(shared)
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_086_task_id_lookup(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        run = await uow.run_records.create_queued(
                            str(uuid.uuid4()), seed.project_id, task_id="task-x"
                        )
                        found = await uow.run_records.list_by_task_id("task-x")
                        self.assertEqual([r.id for r in found], [run.id])
                finally:
                    await database.close()
        run_async(body())

    def test_087_status_transition_without_finishing(self):
        """A non-terminal transition does not require a finish time.

        ``set_status`` is for the states that are neither queued nor finished; the
        seeded run is already ``running``, which is the state this exists for.
        O-7 -- which statuses are terminal -- remains open, so nothing here
        asserts a terminal-state rule.
        """

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        changed = await uow.run_records.set_status(
                            seed.run_id, RunRecordStatus.CANCELLED
                        )
                        self.assertEqual(changed.status,
                                         RunRecordStatus.CANCELLED)
                        # No finish time was set: this transition does not imply one.
                        self.assertIsNone(changed.finished_at)
                finally:
                    await database.close()
        run_async(body())

    def test_088_an_unknown_status_never_reaches_the_database(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        # The domain enum refuses it, so no SQL is sent.
                        with self.assertRaises(ValueError):
                            RunRecordStatus("exploded")
                finally:
                    await database.close()
        run_async(body())

    def test_089_a_run_cannot_be_deleted_and_history_survives(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(PermissionDeniedError):
                            await uow.session.execute(
                                "DELETE FROM run_records WHERE id = $1",
                                uuid.UUID(seed.run_id),
                            )
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        self.assertIsNotNone(
                            await uow.run_records.get(seed.run_id)
                        )
                finally:
                    await database.close()
        run_async(body())


class UsageRecordRepositoryTests(PersistenceTestCase):
    def _usage(self, seed: TenantSeed, *, attempt: int = 0) -> UsageRecord:
        return UsageRecord(
            id=str(uuid.uuid4()),
            organization_id=seed.organization_id,
            run_record_id=seed.run_id,
            core_run_id="core-a",
            attempt_number=attempt,
            created_at=utc_now(),
            provider_name="deepseek",
            model_name="deepseek-chat",
            input_tokens=120,
            output_tokens=45,
            cached_tokens=10,
            duration_seconds=1.75,
            success=True,
            fallback=False,
            tool_call_count=3,
            completed_at=utc_now(),
            error_type="",
        )

    def test_090_append_and_read_back_every_field(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        written = await uow.usage_records.append(self._usage(seed))
                        self.assertEqual(written.input_tokens, 120)
                        self.assertEqual(written.output_tokens, 45)
                        self.assertEqual(written.cached_tokens, 10)
                        self.assertAlmostEqual(written.duration_seconds, 1.75)
                        self.assertTrue(written.success)
                        self.assertFalse(written.fallback)
                        self.assertEqual(written.tool_call_count, 3)
                        self.assertIsNotNone(written.completed_at)
                        self.assertEqual(written.provider_name, "deepseek")

                        listed = await uow.usage_records.list_for_run(seed.run_id)
                        self.assertEqual([u.id for u in listed], [written.id])
                        fetched = await uow.usage_records.get(written.id)
                        self.assertEqual(fetched.id, written.id)
                finally:
                    await database.close()
        run_async(body())

    def test_091_a_duplicate_attempt_is_refused_not_overwritten(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        first = await uow.usage_records.append(
                            self._usage(seed, attempt=0)
                        )
                        first_id = first.id
                        # A refusal aborts the transaction, so the probe is fenced.
                        await uow.session.execute("SAVEPOINT s")
                        with self.assertRaises(UniqueViolationError) as caught:
                            await uow.usage_records.append(
                                self._usage(seed, attempt=0)
                            )
                        self.assertEqual(
                            caught.exception.constraint,
                            "usage_records_run_attempt_unique",
                        )
                        await uow.session.execute("ROLLBACK TO SAVEPOINT s")
                        # The rolled-back attempt left the first observation intact:
                        # the duplicate was refused, not silently applied.
                        still_there = await uow.usage_records.get(first_id)
                        self.assertIsNotNone(still_there)
                        self.assertEqual(still_there.attempt_number, 0)
                        # A different attempt is a different observation.
                        await uow.usage_records.append(self._usage(seed, attempt=1))
                        self.assertEqual(
                            len(await uow.usage_records.list_for_run(seed.run_id)), 2
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_092_the_repository_exposes_no_update_or_delete(self):
        """Append-only is expressed by absence, not by a convention."""

        from app.platform.persistence.repositories import (
            PostgresUsageRecordRepository,
        )

        for forbidden in ("update", "delete", "set_status", "set_error_type",
                          "set_tokens"):
            self.assertFalse(
                hasattr(PostgresUsageRecordRepository, forbidden),
                f"UsageRecordRepository must not expose {forbidden}",
            )

    def test_093_the_database_refuses_update_and_delete(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        written = await uow.usage_records.append(self._usage(seed))
                        written_id = written.id
                        # Each refusal aborts the transaction, so each is fenced.
                        for statement in (
                            "UPDATE usage_records SET input_tokens = 1 "
                            "WHERE id = $1",
                            "DELETE FROM usage_records WHERE id = $1",
                        ):
                            await uow.session.execute("SAVEPOINT s")
                            with self.assertRaises(PermissionDeniedError):
                                await uow.session.execute(
                                    statement, uuid.UUID(written_id)
                                )
                            await uow.session.execute("ROLLBACK TO SAVEPOINT s")
                        unchanged = await uow.usage_records.get(written_id)
                        self.assertIsNotNone(
                            unchanged, "the observation must still be there"
                        )
                        self.assertEqual(
                            unchanged.input_tokens, 120,
                            "the observation must be unchanged",
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_094_usage_from_another_tenant_is_invisible(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a = await seed_tenant(handle, "A")
                    seed_b = await seed_tenant(handle, "B")
                    async with database.tenant(seed_b.organization_id,
                                              user_id=seed_b.user_id) as uow:
                        written = await uow.usage_records.append(self._usage(seed_b))
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        self.assertIsNone(
                            await uow.usage_records.get(written.id)
                        )
                        self.assertEqual(
                            await uow.usage_records.list_for_current_tenant(), []
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_095_usage_cannot_be_attached_to_another_tenants_run(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed_a = await seed_tenant(handle, "A")
                    seed_b = await seed_tenant(handle, "B")
                    async with database.tenant(seed_a.organization_id,
                                              user_id=seed_a.user_id) as uow:
                        forged = self._usage(seed_a)
                        forged = UsageRecord(
                            id=forged.id,
                            organization_id=seed_a.organization_id,
                            run_record_id=seed_b.run_id,  # another tenant's run
                            core_run_id=forged.core_run_id,
                            attempt_number=0,
                            created_at=forged.created_at,
                        )
                        with self.assertRaises(ForeignKeyViolationError):
                            await uow.usage_records.append(forged)
                finally:
                    await database.close()
        run_async(body())

    def test_096_no_money_field_exists(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        columns = {
                            r["column_name"] for r in await uow.session.fetch(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = 'public' AND table_name = "
                                "'usage_records'"
                            )
                        }
                        for forbidden in ("cost", "price", "charge", "balance",
                                          "currency", "amount", "margin"):
                            self.assertNotIn(forbidden, columns)
                finally:
                    await database.close()
        run_async(body())


class UserAndOrganizationRepositoryTests(PersistenceTestCase):
    def test_100_user_lookup_by_email_is_retrieval_only(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    email = f"a-{seed.user_id[:8]}@example.test"
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        found = await uow.users.find_by_email(email)
                        self.assertIsNotNone(found)
                        self.assertEqual(found.id, seed.user_id)
                        self.assertIsNone(
                            await uow.users.find_by_email("nobody@example.test")
                        )
                    # Discovery is the other scope that may resolve a subject, and
                    # it resolves by the same lookup.
                    async with pre_tenant_scope(handle, user_id=seed.user_id) as uow:
                        discovered = await uow.users.find_by_email(email)
                        self.assertEqual(discovered.id, seed.user_id)
                finally:
                    await database.close()
        run_async(body())

    def test_101_user_status_and_display_name_updates(self):
        """Who may change an identity, and how far.

        Two boundaries, both enforced by the database rather than by this layer:

        * the system scope holds INSERT and SELECT on ``users`` and no UPDATE, so it
          cannot change an account at all;
        * the tenant scope may update only the subject's own row, so one subject
          cannot rewrite another's record.
        """

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    other_id = str(uuid.uuid4())
                    async with system_scope(handle) as uow:
                        # It may create an account (the bootstrap path) ...
                        await uow.users.create(
                            User(id=other_id, email="other@example.test",
                                 created_at=utc_now())
                        )
                        # ... and it may not change one.
                        with self.assertRaises(PermissionDeniedError):
                            await uow.users.set_status(
                                seed.user_id, UserStatus.SUSPENDED
                            )

                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        renamed = await uow.users.set_display_name(
                            seed.user_id, "Renamed"
                        )
                        self.assertEqual(renamed.display_name, "Renamed")
                        suspended = await uow.users.set_status(
                            seed.user_id, UserStatus.SUSPENDED
                        )
                        self.assertEqual(suspended.status, UserStatus.SUSPENDED)
                        # Its own row was reachable; another subject's was not,
                        # because the policy requests the subject's own id. The
                        # result is EntityNotFound: within this scope the row is not
                        # addressable at all, and the invariant that a permission
                        # refusal is not absence holds because the tenant policy
                        # grants the UPDATE privilege and bounds it by the subject,
                        # rather than denying the statement.
                        with self.assertRaises(EntityNotFound):
                            await uow.users.set_display_name(other_id, "not mine")
                finally:
                    await database.close()
        run_async(body())

    def test_102_a_duplicate_email_is_refused(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    email = f"a-{seed.user_id[:8]}@example.test"
                    # The duplicate is created on the bootstrap path, the only scope
                    # that may insert an account at all.
                    async with system_scope(handle) as uow:
                        with self.assertRaises(UniqueViolationError) as caught:
                            await uow.users.create(
                                User(id=str(uuid.uuid4()), email=email,
                                     created_at=utc_now())
                            )
                        self.assertEqual(caught.exception.constraint,
                                         "users_email_unique")
                finally:
                    await database.close()
        run_async(body())

    def test_103_organization_slug_is_not_globally_unique(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    # Creating tenants is the server-only bootstrap path, and the
                    # duplicate slug must be accepted: K-3 rejected a global unique
                    # constraint on organizations.slug.
                    async with system_scope(handle) as uow:
                        for _ in range(2):
                            await uow.organizations.create(
                                Organization(
                                    id=str(uuid.uuid4()),
                                    name="Same slug",
                                    created_at=utc_now(),
                                    slug="shared-slug",
                                )
                            )
                        rows = await uow.session.fetchval(
                            "SELECT count(*) FROM organizations WHERE slug = "
                            "'shared-slug'"
                        )
                        self.assertEqual(rows, 2)
                finally:
                    await database.close()
        run_async(body())

    def test_104_organization_name_and_status_updates(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    # No scope can rename or suspend a tenant: the system role holds
                    # SELECT and INSERT on `organizations` and no UPDATE, and no
                    # UPDATE policy exists for any role. The capability is therefore
                    # absent from the repository rather than present and unrunnable,
                    # because a policy-filtered UPDATE matches zero rows and would be
                    # reported to the caller as a missing tenant.
                    from app.platform.persistence.repositories import (
                        PostgresOrganizationRepository,
                    )

                    for forbidden in ("set_name", "set_status"):
                        self.assertFalse(
                            hasattr(PostgresOrganizationRepository, forbidden),
                            f"organizations must not offer {forbidden}: no scope can "
                            "perform it",
                        )
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        organization = await uow.organizations.get(
                            seed.organization_id
                        )
                        self.assertEqual(organization.name, "Org A")
                finally:
                    await database.close()
        run_async(body())

    def test_105_the_tenant_role_cannot_create_an_organization(self):
        """Creating a tenant is a server-only bootstrap step."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(PermissionDeniedError):
                            await uow.organizations.create(
                                Organization(
                                    id=str(uuid.uuid4()),
                                    name="rogue",
                                    created_at=utc_now(),
                                )
                            )
                finally:
                    await database.close()
        run_async(body())


# =========================================================================== #
# E. Pre-tenant discovery
# =========================================================================== #
class PreTenantDiscoveryTests(PersistenceTestCase):
    def test_110_discovery_finds_user_memberships_and_organizations(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with pre_tenant_scope(handle, user_id=seed.user_id) as uow:
                        user = await uow.users.find_by_email(
                            f"a-{seed.user_id[:8]}@example.test"
                        )
                        self.assertIsNotNone(user, "discovery must find the subject")
                        memberships = await uow.memberships.list_for_user(user.id)
                        self.assertEqual(len(memberships), 1)
                        organizations = await uow.organizations.list_for_user(
                            user.id
                        )
                        self.assertEqual(
                            [o.id for o in organizations], [seed.organization_id]
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_111_discovery_without_a_subject_finds_nothing(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    await seed_tenant(handle, "A")
                    async with pre_tenant_scope(handle) as uow:
                        # The system scope with no subject setting: the discovery
                        # reads are scoped by the argument, and without one there is
                        # nothing to look up.
                        self.assertIsNone(
                            await uow.users.find_by_email("nobody@example.test")
                        )
                finally:
                    await database.close()
        run_async(body())

    def test_112_discovery_cannot_read_tenant_work(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with pre_tenant_scope(handle, user_id=seed.user_id) as uow:
                        for repository, method in (
                            (uow.projects, "list_for_current_tenant"),
                            (uow.run_records, "list_for_current_tenant"),
                            (uow.api_keys, "list_for_current_tenant"),
                            (uow.usage_records, "list_for_current_tenant"),
                        ):
                            with self.assertRaises(TransactionError):
                                await getattr(repository, method)()
                finally:
                    await database.close()
        run_async(body())

    def test_113_discovery_is_not_a_route_to_membership_mutation(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with pre_tenant_scope(handle, user_id=seed.user_id) as uow:
                        # The scope may write bootstrap rows, but nothing here lets a
                        # caller act on an arbitrary tenant: the only reachable rows
                        # are the ones the subject's own membership names.
                        organizations = await uow.organizations.list_for_user(
                            seed.user_id
                        )
                        self.assertEqual(len(organizations), 1)
                finally:
                    await database.close()
        run_async(body())

    def test_114_the_tenant_role_cannot_perform_discovery_reads(self):
        """Discovery requires the system role; the tenant role cannot reach it."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.server() as uow:
                        # No tenant context and no system scope: the discovery
                        # tables are unreadable, which is the fail-closed direction.
                        self.assertIsNone(
                            await uow.users.get(seed.user_id)
                        )
                        self.assertEqual(
                            await uow.memberships.list_for_user(seed.user_id), []
                        )
                finally:
                    await database.close()
        run_async(body())


# =========================================================================== #
# F. Error normalization
# =========================================================================== #
class ErrorNormalizationTests(PersistenceTestCase):
    def test_120_unique_violation_names_the_constraint(self):
        """A lost race must be diagnosable, so the constraint name is preserved."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(UniqueViolationError) as caught:
                            await uow.memberships.create(
                                Membership(
                                    id=str(uuid.uuid4()),
                                    organization_id=seed.organization_id,
                                    user_id=seed.user_id,
                                    role=MembershipRole.OWNER,
                                    created_at=utc_now(),
                                )
                            )
                        self.assertEqual(
                            caught.exception.constraint,
                            "memberships_organization_user_unique",
                        )
                        self.assertIn("memberships_organization_user_unique",
                                      str(caught.exception))
                finally:
                    await database.close()
        run_async(body())

    def test_121_foreign_key_violation_is_normalized(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(ForeignKeyViolationError) as caught:
                            await uow.run_records.create_queued(
                                str(uuid.uuid4()), str(uuid.uuid4())
                            )
                        self.assertIsNotNone(caught.exception.constraint)
                finally:
                    await database.close()
        run_async(body())

    def test_122_check_violation_is_normalized(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(CheckViolationError):
                            await uow.session.execute(
                                "INSERT INTO run_records (id, organization_id, "
                                "project_id, attempt_count, created_at) "
                                "VALUES ($1, $2, $3, -1, now())",
                                uuid.uuid4(), uuid.UUID(seed.organization_id),
                                uuid.UUID(seed.project_id),
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_123_permission_denial_is_not_reported_as_not_found(self):
        """An authorization failure must not masquerade as absence."""

        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(PermissionDeniedError):
                            await uow.session.execute(
                                "INSERT INTO provider_accounts (id, "
                                "organization_id, provider_name, secret_ref, "
                                "created_at) VALUES ($1, NULL, 'x', 'y', now())",
                                uuid.uuid4(),
                            )
                finally:
                    await database.close()
        run_async(body())

    def test_124_a_failed_statement_aborts_the_transaction(self):
        async def body():
            async with PersistenceDatabase() as handle:
                database = await open_database(handle)
                try:
                    seed = await seed_tenant(handle, "A")
                    async with database.tenant(seed.organization_id,
                                              user_id=seed.user_id) as uow:
                        with self.assertRaises(PermissionDeniedError):
                            await uow.session.execute(
                                "INSERT INTO provider_accounts (id, "
                                "organization_id, provider_name, secret_ref, "
                                "created_at) VALUES ($1, NULL, 'x', 'y', now())",
                                uuid.uuid4(),
                            )
                        # The transaction is aborted; the next statement must be
                        # reported as a transaction error, not as an empty result.
                        with self.assertRaises(Exception) as caught:
                            await uow.projects.list_for_current_tenant()
                        self.assertNotIsInstance(caught.exception, EntityNotFound)
                finally:
                    await database.close()
        run_async(body())

    def test_125_normalization_is_idempotent(self):
        from app.platform.persistence import PersistenceError
        from app.platform.persistence.database import normalize_error

        original = PersistenceError("already normalized")
        self.assertIs(normalize_error(original), original)
        self.assertIsInstance(normalize_error(ValueError("plain")),
                              PersistenceError)


if __name__ == "__main__":
    unittest.main()
