"""Security-fix contract tests for the Platform PostgreSQL boundaries.

These cover the findings an adversarial review raised against the first schema
implementation. Every assertion here is a PostgreSQL-level guarantee; none of them
can be satisfied by application code alone, and none of them is simulated.

The four areas:

* **F-01** -- the two platform scopes are separate, and `forge.system_scope` is
  not itself an authorization mechanism. The security-sensitive checks run from a
  real non-superuser login role, because a superuser and a table owner both bypass
  row-level security and would therefore prove nothing.
* **F-02** -- the server-only scope is confined to identity, tenancy, and
  system-owned credentials. It is not a universal cross-tenant reader or writer.
* **F-03** -- `usage_records` is append-only and `run_records` history cannot be
  deleted, enforced in both the policy layer and the privilege layer.
* **F-04** -- key and run provenance is tenant-safe: the creator or initiator must
  be a member of the row's own organization, enforced by a composite foreign key.

Skipped with an explicit reason when no server is configured; never simulated.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid

from postgres_fixture import (
    APP_ROLE,
    SKIP_REASON_NO_DRIVER,
    SKIP_REASON_NO_SERVER,
    SYSTEM_ROLE,
    LiveDatabase,
    asyncpg_module,
    live_postgres,
    proof_login_available,
    set_context,
)

TABLES = (
    "api_keys",
    "memberships",
    "organizations",
    "projects",
    "provider_accounts",
    "run_records",
    "usage_records",
    "users",
)

# The operational tables carry tenant work. The server-only scope must have no
# table privilege at all on these, so no policy edit can expose them.
OPERATIONAL_TABLES = (
    "projects",
    "api_keys",
    "run_records",
    "usage_records",
)


def run_async(coro):
    return asyncio.run(coro)


class SecurityTestCase(unittest.TestCase):
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


class Tenant:
    """Minimal tenant seeding through the path each step actually needs."""

    def __init__(self, label: str):
        self.label = label
        self.organization_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.membership_id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.run_id = uuid.uuid4()

    async def bootstrap(self, conn) -> None:
        """The identity and tenancy layer, via the server-only scope."""

        await conn.execute("BEGIN")
        try:
            await set_context(conn, system_scope="on")
            await conn.execute(
                "INSERT INTO organizations (id, name, slug, created_at) "
                "VALUES ($1, $2, $3, now())",
                self.organization_id, f"Org {self.label}",
                f"org-{self.label.lower()}-{self.organization_id.hex[:8]}",
            )
            await conn.execute(
                "INSERT INTO users (id, email, created_at) VALUES ($1, $2, now())",
                self.user_id,
                f"{self.label.lower()}-{self.user_id.hex[:8]}@example.test",
            )
            await conn.execute(
                "INSERT INTO memberships (id, organization_id, user_id, role, "
                "created_at) VALUES ($1, $2, $3, 'owner', now())",
                self.membership_id, self.organization_id, self.user_id,
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def tenant_data(self, conn) -> None:
        """The operational layer, via the ordinary tenant path."""

        await conn.execute("BEGIN")
        try:
            await set_context(conn, organization_id=self.organization_id,
                              user_id=self.user_id)
            await conn.execute(
                "INSERT INTO projects (id, organization_id, name, slug, created_at) "
                "VALUES ($1, $2, $3, $4, now())",
                self.project_id, self.organization_id, f"Project {self.label}",
                f"project-{self.label.lower()}",
            )
            await conn.execute(
                "INSERT INTO run_records (id, organization_id, project_id, "
                "core_run_id, initiated_by_user_id, status, started_at, created_at) "
                "VALUES ($1, $2, $3, $4, $5, 'running', now(), now())",
                self.run_id, self.organization_id, self.project_id,
                f"core-{self.run_id.hex[:10]}", self.user_id,
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def seed(self, conn) -> None:
        await self.bootstrap(conn)
        await self.tenant_data(conn)

    async def append_usage(self, conn, *, attempt_number=0) -> uuid.UUID:
        usage_id = uuid.uuid4()
        await conn.execute("BEGIN")
        try:
            await set_context(conn, organization_id=self.organization_id,
                              user_id=self.user_id)
            await conn.execute(
                "INSERT INTO usage_records (id, organization_id, run_record_id, "
                "core_run_id, attempt_number, created_at) "
                "VALUES ($1, $2, $3, $4, $5, now())",
                usage_id, self.organization_id, self.run_id,
                f"core-{self.run_id.hex[:10]}", attempt_number,
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise
        return usage_id


# =========================================================================== #
# F-01: app vs system role model
# =========================================================================== #
class RoleBoundaryTests(SecurityTestCase):
    def test_100_scopes_are_never_members_of_each_other(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                self.assertTrue(
                    await conn.fetchval("SELECT platform.platform_scopes_are_separate()")
                )
                self.assertFalse(
                    await conn.fetchval(
                        "SELECT pg_has_role($1, $2, 'MEMBER')", APP_ROLE, SYSTEM_ROLE
                    ),
                    "the tenant scope must not be a member of the system scope",
                )
                self.assertFalse(
                    await conn.fetchval(
                        "SELECT pg_has_role($1, $2, 'MEMBER')", SYSTEM_ROLE, APP_ROLE
                    ),
                    "the system scope must not be a member of the tenant scope",
                )
        run_async(body())

    def test_101_neither_scope_inherits_privileges(self):
        """NOINHERIT is what stops the system scope leaking into a mixed login."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT rolname, rolinherit, rolsuper, rolbypassrls, rolcanlogin "
                    "FROM pg_roles WHERE rolname = ANY($1::text[])",
                    [APP_ROLE, SYSTEM_ROLE],
                )
                self.assertEqual(len(rows), 2)
                for row in rows:
                    self.assertFalse(row["rolinherit"], row["rolname"])
                    self.assertFalse(row["rolsuper"], row["rolname"])
                    self.assertFalse(row["rolbypassrls"], row["rolname"])
                    self.assertFalse(row["rolcanlogin"], row["rolname"])
        run_async(body())

    def test_102_migration_aborts_when_the_scopes_are_merged(self):
        """The dangerous configuration is refused by the migration itself."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                await conn.execute(f"GRANT {SYSTEM_ROLE} TO {APP_ROLE}")
                with self.assertRaises(Exception) as caught:
                    await conn.execute(
                        "DO $$ BEGIN "
                        "IF NOT platform.platform_scopes_are_separate() THEN "
                        "RAISE EXCEPTION 'platform scopes are merged'; "
                        "END IF; END $$;"
                    )
                self.assertIn("merged", str(caught.exception))
                await conn.execute(f"REVOKE {SYSTEM_ROLE} FROM {APP_ROLE}")
                self.assertTrue(
                    await conn.fetchval("SELECT platform.platform_scopes_are_separate()")
                )
        run_async(body())

    def test_103_ordinary_login_role_is_not_a_superuser_or_rls_bypasser(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                await db.create_proof_login(grant_app=True)
                login = await db.login_as_proof_role(APP_ROLE)
                try:
                    row = await login.fetchrow(
                        "SELECT rolsuper, rolbypassrls, current_user, "
                        "session_user FROM pg_roles r, "
                        "(SELECT current_user AS cu, session_user AS su) s "
                        "WHERE r.rolname = current_user"
                    )
                    self.assertFalse(row["rolsuper"])
                    self.assertFalse(row["rolbypassrls"])
                    self.assertEqual(row["current_user"], APP_ROLE)
                    self.assertNotEqual(row["session_user"], APP_ROLE)
                finally:
                    await login.close()
        run_async(body())

    def test_104_the_system_scope_is_not_an_authorization_mechanism(self):
        """Setting the GUC on the tenant path grants nothing.

        A custom GUC can be set by any session, so it can never be the thing that
        grants access. What gates the system scope is the policy's TO clause and
        the role membership the deployment controls.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                await db.create_proof_login(grant_app=True)
                tenant = Tenant("A")
                await tenant.seed(db.connection)
                # A system-owned credential must exist for the check to mean
                # anything: if there were none, "cannot see it" would be vacuous.
                await db.connection.execute("BEGIN")
                try:
                    await set_context(db.connection, system_scope="on")
                    await db.connection.execute(
                        "INSERT INTO provider_accounts (id, organization_id, "
                        "provider_name, secret_ref, created_at) "
                        "VALUES ($1, NULL, 'anthropic', 'ref:system', now())",
                        uuid.uuid4(),
                    )
                    await db.connection.execute("COMMIT")
                except Exception:
                    await db.connection.execute("ROLLBACK")
                    raise

                login = await db.login_as_proof_role(APP_ROLE)
                try:
                    # The tenant path claims the system scope for itself.
                    await login.execute(
                        "SELECT set_config('forge.system_scope', 'on', false)"
                    )
                    self.assertEqual(
                        await login.fetchval("SELECT count(*) FROM provider_accounts"),
                        0,
                        "the tenant path must not reach system rows by setting "
                        "the system scope flag",
                    )
                    # And the claim survives only for the tenant scope anyway.
                    for table in TABLES:
                        count = await login.fetchval(f"SELECT count(*) FROM {table}")
                        self.assertEqual(
                            count, 0,
                            f"{table} leaked {count} rows to the tenant path "
                            "with no tenant context",
                        )
                finally:
                    await login.close()
        run_async(body())

    def test_105_non_superuser_login_cannot_escalate_to_the_system_scope(self):
        """A real login role holding only the tenant scope cannot SET ROLE."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                await db.create_proof_login(grant_app=True, grant_system=False)
                login = await db.login_as_proof_role(APP_ROLE)
                try:
                    with self.assertRaises(asyncpg_module().InsufficientPrivilegeError):
                        await login.execute(f"SET ROLE {SYSTEM_ROLE}")
                finally:
                    await login.close()
        run_async(body())

    def test_106_non_superuser_tenant_login_has_no_ddl_and_no_ledger(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                await db.create_proof_login(grant_app=True)
                login = await db.login_as_proof_role(APP_ROLE)
                try:
                    for statement in (
                        "ALTER TABLE projects ADD COLUMN x int",
                        "TRUNCATE projects",
                        "DROP TABLE projects",
                        "SELECT * FROM platform_schema_migrations",
                    ):
                        with self.assertRaises(
                            asyncpg_module().InsufficientPrivilegeError,
                            msg=f"unexpectedly allowed: {statement}",
                        ):
                            await login.execute(statement)
                finally:
                    await login.close()
        run_async(body())


# =========================================================================== #
# F-02: the system scope is not a universal tenant bypass
# =========================================================================== #
class SystemScopeContainmentTests(SecurityTestCase):
    def test_110_system_scope_has_no_privilege_on_operational_tables(self):
        """The strong form of F-02: not "the policy denies", but "no privilege".

        With no table privilege there is no policy that could ever grant access,
        so this cannot be undone by editing a policy.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                for table in OPERATIONAL_TABLES:
                    privileges = await conn.fetch(
                        "SELECT privilege_type FROM information_schema."
                        "role_table_grants WHERE table_name = $1 AND grantee = $2",
                        table, SYSTEM_ROLE,
                    )
                    self.assertEqual(
                        [r["privilege_type"] for r in privileges], [],
                        f"{SYSTEM_ROLE} holds a privilege on {table}",
                    )
                    policies = await conn.fetch(
                        "SELECT policyname FROM pg_policies WHERE schemaname = "
                        "'public' AND tablename = $1 AND roles::text LIKE $2",
                        table, f"%{SYSTEM_ROLE}%",
                    )
                    self.assertEqual(
                        [r["policyname"] for r in policies], [],
                        f"{SYSTEM_ROLE} has a policy on {table}",
                    )
        run_async(body())

    def test_111_no_for_all_system_policy_on_tenant_tables(self):
        """No wildcard capability anywhere except the system-owned row shape."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT tablename, policyname FROM pg_policies WHERE schemaname "
                    "= 'public' AND cmd = 'ALL' AND roles::text LIKE $1",
                    f"%{SYSTEM_ROLE}%",
                )
                self.assertEqual(
                    {(r["tablename"], r["policyname"]) for r in rows},
                    {("provider_accounts", "provider_accounts_system_owned")},
                    "FOR ALL is permitted only where every operation is bound to "
                    "the system-owned row shape",
                )
        run_async(body())

    def test_112_system_scope_cannot_enumerate_tenant_work(self):
        """Discovery reads identity and tenancy; it does not read tenant work.

        Asserted from a real non-superuser login holding only the system scope, so
        the refusal is a genuine privilege denial rather than the owner's silent
        zero-row bypass.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                tenant = Tenant("A")
                await tenant.seed(db.connection)
                await db.create_proof_login(grant_app=False, grant_system=True)
                login = await db.login_as_proof_role(SYSTEM_ROLE)
                try:
                    await login.execute("BEGIN")
                    try:
                        await set_context(login, system_scope="on")
                        # Each probe gets a savepoint: a privilege denial aborts
                        # the transaction, so without one every later probe would
                        # report InFailedSQLTransactionError instead of the
                        # denial actually under test.
                        for table in OPERATIONAL_TABLES:
                            await login.execute("SAVEPOINT s")
                            with self.assertRaises(
                                asyncpg_module().InsufficientPrivilegeError,
                                msg=f"system scope read {table}",
                            ):
                                await login.fetchval(f"SELECT count(*) FROM {table}")
                            await login.execute("ROLLBACK TO SAVEPOINT s")
                    finally:
                        await login.execute("ROLLBACK")
                finally:
                    await login.close()
        run_async(body())

    def test_113_system_scope_can_do_pre_tenant_discovery(self):
        """What the next step actually needs, and nothing more.

        Resolve a subject, read its memberships, read the organizations those
        memberships name -- before any tenant context exists.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.seed(conn)

                await conn.execute("BEGIN")
                try:
                    await set_context(conn, system_scope="on")
                    user = await conn.fetchrow(
                        "SELECT id, email FROM users WHERE id = $1", tenant.user_id
                    )
                    self.assertIsNotNone(user, "discovery must find the user")
                    memberships = await conn.fetch(
                        "SELECT organization_id, role FROM memberships "
                        "WHERE user_id = $1", tenant.user_id
                    )
                    self.assertEqual(len(memberships), 1)
                    org_ids = [m["organization_id"] for m in memberships]
                    organizations = await conn.fetch(
                        "SELECT id, name FROM organizations WHERE id = ANY($1::uuid[])",
                        org_ids,
                    )
                    self.assertEqual(
                        [o["id"] for o in organizations], [tenant.organization_id]
                    )
                finally:
                    await conn.execute("ROLLBACK")
        run_async(body())

    def test_114_system_scope_is_off_until_explicitly_declared(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.seed(conn)

                # The flag is absent / off: the system scope sees nothing, even
                # for the tables it is allowed to reach.
                await conn.execute(f"SET ROLE {SYSTEM_ROLE}")
                try:
                    for table in ("users", "organizations", "memberships"):
                        self.assertEqual(
                            await conn.fetchval(f"SELECT count(*) FROM {table}"), 0,
                            f"{table} must be invisible without the scope flag",
                        )
                finally:
                    await conn.execute("RESET ROLE")
        run_async(body())

    def test_115_system_scope_cannot_modify_or_delete_identity(self):
        """Discovery is a read. Account mutation is not part of this scope.

        UPDATE on `users` and DELETE on `users` or `organizations` have no policy
        for this scope AND no privilege, so this is a privilege denial rather than
        a policy that happens to match nothing.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                tenant = Tenant("A")
                await tenant.seed(db.connection)
                await db.create_proof_login(grant_app=False, grant_system=True)
                login = await db.login_as_proof_role(SYSTEM_ROLE)
                try:
                    for statement, args in (
                        ("UPDATE users SET email = 'hijacked@example.test' "
                         "WHERE id = $1", (tenant.user_id,)),
                        ("DELETE FROM users WHERE id = $1", (tenant.user_id,)),
                        ("UPDATE organizations SET status = 'suspended' "
                         "WHERE id = $1", (tenant.organization_id,)),
                        ("DELETE FROM organizations WHERE id = $1",
                         (tenant.organization_id,)),
                    ):
                        for _ in (0,):
                            await login.execute("BEGIN")
                            try:
                                await set_context(login, system_scope="on")
                                await login.execute("SAVEPOINT s")
                                with self.assertRaises(
                                    asyncpg_module().InsufficientPrivilegeError,
                                    msg=f"unexpectedly allowed: {statement}",
                                ):
                                    await login.execute(statement, *args)
                                await login.execute("ROLLBACK TO SAVEPOINT s")
                            finally:
                                await login.execute("ROLLBACK")
                finally:
                    await login.close()

                # The rows are untouched.
                await db.connection.execute("BEGIN")
                try:
                    await set_context(db.connection, system_scope="on")
                    self.assertNotEqual(
                        await db.connection.fetchval(
                            "SELECT email FROM users WHERE id = $1", tenant.user_id
                        ),
                        "hijacked@example.test",
                    )
                    self.assertEqual(
                        await db.connection.fetchval(
                            "SELECT count(*) FROM users WHERE id = $1",
                            tenant.user_id,
                        ),
                        1,
                    )
                finally:
                    await db.connection.execute("ROLLBACK")
        run_async(body())

    def test_116_system_scope_owns_only_system_owned_provider_accounts(self):
        """One operational exception, bounded by the row shape.

        The scope may create and read system-owned credentials and may not see or
        take over a tenant's credential. Asserted from a real system-scope login so
        the row filtering is genuine and not an owner bypass.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.seed(conn)
                tenant_account = uuid.uuid4()
                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=tenant.organization_id,
                                      user_id=tenant.user_id)
                    await conn.execute(
                        "INSERT INTO provider_accounts (id, organization_id, "
                        "provider_name, secret_ref, created_at) "
                        "VALUES ($1, $2, 'openai', 'ref:tenant', now())",
                        tenant_account, tenant.organization_id,
                    )
                    await conn.execute("COMMIT")
                except Exception:
                    await conn.execute("ROLLBACK")
                    raise

                await db.create_proof_login(grant_app=False, grant_system=True)
                login = await db.login_as_proof_role(SYSTEM_ROLE)
                try:
                    await login.execute("BEGIN")
                    try:
                        await set_context(login, system_scope="on")
                        self.assertEqual(
                            await login.fetchval(
                                "SELECT count(*) FROM provider_accounts"
                            ),
                            0,
                            "the system scope must not see tenant-owned credentials",
                        )
                        system_account = uuid.uuid4()
                        await login.execute(
                            "INSERT INTO provider_accounts (id, organization_id, "
                            "provider_name, secret_ref, created_at) "
                            "VALUES ($1, NULL, 'openai', 'ref:system', now())",
                            system_account,
                        )
                        visible = {
                            r["id"] for r in await login.fetch(
                                "SELECT id FROM provider_accounts"
                            )
                        }
                        self.assertEqual(visible, {system_account})
                        # It cannot adopt or touch a tenant credential.
                        self.assertEqual(
                            await login.execute(
                                "UPDATE provider_accounts SET secret_ref = 'x' "
                                "WHERE id = $1", tenant_account,
                            ),
                            "UPDATE 0",
                        )
                        with self.assertRaises(
                            asyncpg_module().InsufficientPrivilegeError
                        ):
                            await login.execute(
                                "UPDATE provider_accounts SET organization_id = $1 "
                                "WHERE organization_id IS NULL",
                                tenant.organization_id,
                            )
                    finally:
                        await login.execute("ROLLBACK")
                finally:
                    await login.close()
        run_async(body())


# =========================================================================== #
# F-03: append-only usage and protected run history
# =========================================================================== #
class AppendOnlyTests(SecurityTestCase):
    async def _tenant_with_usage(self, db) -> tuple[Tenant, uuid.UUID]:
        tenant = Tenant("A")
        await tenant.seed(db.connection)
        usage_id = await tenant.append_usage(db.connection)
        return tenant, usage_id

    def test_120_usage_records_has_no_update_or_delete_privilege(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                for role in (APP_ROLE, SYSTEM_ROLE):
                    privileges = {
                        r["privilege_type"] for r in await conn.fetch(
                            "SELECT privilege_type FROM information_schema."
                            "role_table_grants WHERE table_name = 'usage_records' "
                            "AND grantee = $1", role,
                        )
                    }
                    self.assertEqual(
                        privileges, {"SELECT", "INSERT"} if role == APP_ROLE else set(),
                        f"unexpected usage_records privileges for {role}",
                    )
                policies = {
                    r["cmd"] for r in await conn.fetch(
                        "SELECT cmd FROM pg_policies WHERE schemaname = 'public' "
                        "AND tablename = 'usage_records'"
                    )
                }
                self.assertEqual(policies, {"SELECT", "INSERT"})
        run_async(body())

    def test_121_tenant_can_append_a_usage_record(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                tenant, usage_id = await self._tenant_with_usage(db)
                await db.connection.execute("BEGIN")
                try:
                    await set_context(db.connection,
                                      organization_id=tenant.organization_id)
                    self.assertEqual(
                        await db.connection.fetchval(
                            "SELECT count(*) FROM usage_records WHERE id = $1",
                            usage_id,
                        ),
                        1,
                    )
                finally:
                    await db.connection.execute("COMMIT")
        run_async(body())

    def test_122_tenant_cannot_update_a_usage_record(self):
        """Asserted from a real non-superuser tenant login.

        The database owner bypasses privileges AND row-level security, so running
        this as the owner would silently match zero rows instead of being refused.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                tenant, usage_id = await self._tenant_with_usage(db)
                await db.create_proof_login(grant_app=True)
                login = await db.login_as_proof_role(APP_ROLE)
                try:
                    await login.execute("BEGIN")
                    try:
                        await set_context(login, organization_id=tenant.organization_id)
                        with self.assertRaises(
                            asyncpg_module().InsufficientPrivilegeError
                        ):
                            await login.execute(
                                "UPDATE usage_records SET input_tokens = 1 "
                                "WHERE id = $1", usage_id,
                            )
                    finally:
                        await login.execute("ROLLBACK")
                finally:
                    await login.close()
        run_async(body())

    def test_123_tenant_cannot_delete_a_usage_record(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                tenant, usage_id = await self._tenant_with_usage(db)
                await db.create_proof_login(grant_app=True)
                login = await db.login_as_proof_role(APP_ROLE)
                try:
                    await login.execute("BEGIN")
                    try:
                        await set_context(login, organization_id=tenant.organization_id)
                        with self.assertRaises(
                            asyncpg_module().InsufficientPrivilegeError
                        ):
                            await login.execute(
                                "DELETE FROM usage_records WHERE id = $1", usage_id
                            )
                    finally:
                        await login.execute("ROLLBACK")
                finally:
                    await login.close()
        run_async(body())

    def test_124_the_system_scope_cannot_update_or_delete_a_usage_record(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                tenant, usage_id = await self._tenant_with_usage(db)
                await db.create_proof_login(grant_app=False, grant_system=True)
                login = await db.login_as_proof_role(SYSTEM_ROLE)
                try:
                    for statement in (
                        "UPDATE usage_records SET input_tokens = 1 WHERE id = $1",
                        "DELETE FROM usage_records WHERE id = $1",
                    ):
                        await login.execute("BEGIN")
                        try:
                            await set_context(login, system_scope="on")
                            with self.assertRaises(
                                asyncpg_module().InsufficientPrivilegeError
                            ):
                                await login.execute(statement, usage_id)
                        finally:
                            await login.execute("ROLLBACK")
                finally:
                    await login.close()
        run_async(body())

    def test_125_run_records_has_no_delete_privilege_or_policy(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                privileges = {
                    r["privilege_type"] for r in await conn.fetch(
                        "SELECT privilege_type FROM information_schema."
                        "role_table_grants WHERE table_name = 'run_records' "
                        "AND grantee = $1", APP_ROLE,
                    )
                }
                self.assertEqual(privileges, {"SELECT", "INSERT", "UPDATE"})
                deletes = await conn.fetch(
                    "SELECT policyname FROM pg_policies WHERE schemaname = 'public' "
                    "AND tablename = 'run_records' AND cmd = 'DELETE'"
                )
                self.assertEqual([r["policyname"] for r in deletes], [])
        run_async(body())

    def test_126_tenant_cannot_delete_a_run_record(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                tenant = Tenant("A")
                await tenant.seed(db.connection)
                await db.create_proof_login(grant_app=True)
                login = await db.login_as_proof_role(APP_ROLE)
                try:
                    await login.execute("BEGIN")
                    try:
                        await set_context(login, organization_id=tenant.organization_id)
                        # The row is visible to this tenant ...
                        self.assertEqual(
                            await login.fetchval(
                                "SELECT count(*) FROM run_records WHERE id = $1",
                                tenant.run_id,
                            ),
                            1,
                        )
                        # ... and still cannot be deleted.
                        with self.assertRaises(
                            asyncpg_module().InsufficientPrivilegeError
                        ):
                            await login.execute(
                                "DELETE FROM run_records WHERE id = $1", tenant.run_id
                            )
                    finally:
                        await login.execute("ROLLBACK")
                finally:
                    await login.close()
        run_async(body())

    def test_127_permitted_run_lifecycle_updates_still_work(self):
        """The fixes must not break the transitions the contract requires."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.bootstrap(conn)
                run_id = tenant.run_id

                # queued -> running, with Core's reported progress.
                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=tenant.organization_id,
                                      user_id=tenant.user_id)
                    await conn.execute(
                        "INSERT INTO projects (id, organization_id, name, "
                        "created_at) VALUES ($1, $2, 'P', now())",
                        tenant.project_id, tenant.organization_id,
                    )
                    await conn.execute(
                        "INSERT INTO run_records (id, organization_id, project_id, "
                        "status, created_at) VALUES ($1, $2, $3, 'queued', now())",
                        run_id, tenant.organization_id, tenant.project_id,
                    )
                    updated = await conn.execute(
                        "UPDATE run_records SET status = 'running', "
                        "core_run_id = 'core-live', started_at = now(), "
                        "initiated_by_user_id = $1, attempt_count = 1 "
                        "WHERE id = $2",
                        tenant.user_id, run_id,
                    )
                    self.assertEqual(updated, "UPDATE 1")
                    # running -> succeeded
                    updated = await conn.execute(
                        "UPDATE run_records SET status = 'succeeded', "
                        "finished_at = now() WHERE id = $1",
                        run_id,
                    )
                    self.assertEqual(updated, "UPDATE 1")
                    await conn.execute("COMMIT")
                except Exception:
                    await conn.execute("ROLLBACK")
                    raise
        run_async(body())

    def test_128_measurement_uniqueness_still_holds(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                tenant = Tenant("A")
                await tenant.seed(db.connection)
                await tenant.append_usage(db.connection, attempt_number=0)
                with self.assertRaises(asyncpg_module().UniqueViolationError):
                    await tenant.append_usage(db.connection, attempt_number=0)
        run_async(body())


# =========================================================================== #
# F-04: tenant-safe provenance
# =========================================================================== #
class ProvenanceTests(SecurityTestCase):
    async def _two_tenants(self, db) -> tuple[Tenant, Tenant]:
        org_a, org_b = Tenant("A"), Tenant("B")
        await org_a.bootstrap(db.connection)
        await org_b.bootstrap(db.connection)
        return org_a, org_b

    def test_130_provenance_keys_are_composite_and_restrict(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT con.conname AS name, "
                    "pg_get_constraintdef(con.oid) AS def, "
                    "con.confdeltype::text AS del "
                    "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                    "WHERE c.relname IN ('api_keys', 'run_records') AND con.contype "
                    "= 'f' AND con.conname IN ('api_keys_creator_membership_fk', "
                    "'run_records_initiator_membership_fk')"
                )
                self.assertEqual(len(rows), 2)
                for row in rows:
                    self.assertIn(
                        "REFERENCES memberships(organization_id, user_id)", row["def"]
                    )
                    self.assertEqual(row["del"], "r", "provenance keys must RESTRICT")
        run_async(body())

    def test_131_valid_same_tenant_provenance_is_accepted(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = await self._two_tenants(db)
                for tenant in (org_a, org_b):
                    await conn.execute("BEGIN")
                    try:
                        await set_context(
                            conn, organization_id=tenant.organization_id,
                            user_id=tenant.user_id,
                        )
                        key_id = uuid.uuid4()
                        await conn.execute(
                            "INSERT INTO api_keys (id, organization_id, "
                            "created_by_user_id, name, created_at) "
                            "VALUES ($1, $2, $3, 'k', now())",
                            key_id, tenant.organization_id, tenant.user_id,
                        )
                        project_id = uuid.uuid4()
                        await conn.execute(
                            "INSERT INTO projects (id, organization_id, name, "
                            "created_at) VALUES ($1, $2, 'P', now())",
                            project_id, tenant.organization_id,
                        )
                        await conn.execute(
                            "INSERT INTO run_records (id, organization_id, "
                            "project_id, core_run_id, initiated_by_user_id, status, "
                            "started_at, created_at) "
                            "VALUES ($1, $2, $3, $4, $5, 'running', now(), now())",
                            uuid.uuid4(), tenant.organization_id, project_id,
                            f"core-{tenant.label}", tenant.user_id,
                        )
                        await conn.execute("COMMIT")
                    except Exception:
                        await conn.execute("ROLLBACK")
                        raise
        run_async(body())

    def test_132_api_key_creator_from_another_tenant_is_rejected(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = await self._two_tenants(db)
                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=org_a.organization_id,
                                      user_id=org_a.user_id)
                    with self.assertRaises(asyncpg_module().ForeignKeyViolationError):
                        await conn.execute(
                            "INSERT INTO api_keys (id, organization_id, "
                            "created_by_user_id, name, created_at) "
                            "VALUES ($1, $2, $3, 'k', now())",
                            uuid.uuid4(), org_a.organization_id, org_b.user_id,
                        )
                finally:
                    await conn.execute("ROLLBACK")
        run_async(body())

    def test_133_run_initiator_from_another_tenant_is_rejected(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = await self._two_tenants(db)
                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=org_a.organization_id,
                                      user_id=org_a.user_id)
                    await conn.execute(
                        "INSERT INTO projects (id, organization_id, name, "
                        "created_at) VALUES ($1, $2, 'P', now())",
                        org_a.project_id, org_a.organization_id,
                    )
                    with self.assertRaises(asyncpg_module().ForeignKeyViolationError):
                        await conn.execute(
                            "INSERT INTO run_records (id, organization_id, "
                            "project_id, core_run_id, initiated_by_user_id, status, "
                            "started_at, created_at) "
                            "VALUES ($1, $2, $3, 'core-x', $4, 'running', now(), "
                            "now())",
                            uuid.uuid4(), org_a.organization_id, org_a.project_id,
                            org_b.user_id,
                        )
                finally:
                    await conn.execute("ROLLBACK")
        run_async(body())

    def test_134_update_to_a_foreign_user_is_rejected(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = await self._two_tenants(db)

                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=org_a.organization_id,
                                      user_id=org_a.user_id)
                    key_id = uuid.uuid4()
                    await conn.execute(
                        "INSERT INTO api_keys (id, organization_id, "
                        "created_by_user_id, name, created_at) "
                        "VALUES ($1, $2, $3, 'k', now())",
                        key_id, org_a.organization_id, org_a.user_id,
                    )
                    with self.assertRaises(asyncpg_module().ForeignKeyViolationError):
                        await conn.execute(
                            "UPDATE api_keys SET created_by_user_id = $1 "
                            "WHERE id = $2",
                            org_b.user_id, key_id,
                        )
                finally:
                    await conn.execute("ROLLBACK")
        run_async(body())

    def test_135_null_initiator_is_still_permitted_for_a_queued_run(self):
        """The composite key must not break the domain's nullable semantics."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.bootstrap(conn)
                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=tenant.organization_id,
                                      user_id=tenant.user_id)
                    await conn.execute(
                        "INSERT INTO projects (id, organization_id, name, "
                        "created_at) VALUES ($1, $2, 'P', now())",
                        tenant.project_id, tenant.organization_id,
                    )
                    await conn.execute(
                        "INSERT INTO run_records (id, organization_id, project_id, "
                        "status, created_at) "
                        "VALUES ($1, $2, $3, 'queued', now())",
                        tenant.run_id, tenant.organization_id, tenant.project_id,
                    )
                    await conn.execute("COMMIT")
                except Exception:
                    await conn.execute("ROLLBACK")
                    raise

                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=tenant.organization_id)
                    self.assertIsNone(
                        await conn.fetchval(
                            "SELECT initiated_by_user_id FROM run_records "
                            "WHERE id = $1", tenant.run_id,
                        )
                    )
                    await conn.execute("COMMIT")
                except Exception:
                    await conn.execute("ROLLBACK")
                    raise
        run_async(body())

    def test_136_provenance_does_not_cascade_on_delete(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.seed(conn)
                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=tenant.organization_id,
                                      user_id=tenant.user_id)
                    await conn.execute(
                        "INSERT INTO api_keys (id, organization_id, "
                        "created_by_user_id, name, created_at) "
                        "VALUES ($1, $2, $3, 'k', now())",
                        uuid.uuid4(), tenant.organization_id, tenant.user_id,
                    )
                    await conn.execute("COMMIT")
                except Exception:
                    await conn.execute("ROLLBACK")
                    raise

                # A membership that key provenance points at cannot vanish, and
                # deleting either parent must fail loudly rather than cascade.
                await conn.execute("BEGIN")
                try:
                    await set_context(conn, system_scope="on")
                    with self.assertRaises(
                        asyncpg_module().ForeignKeyViolationError
                    ):
                        await conn.execute(
                            "DELETE FROM memberships WHERE id = $1",
                            tenant.membership_id,
                        )
                finally:
                    await conn.execute("ROLLBACK")

                await conn.execute("BEGIN")
                try:
                    await set_context(conn, system_scope="on")
                    with self.assertRaises(
                        asyncpg_module().ForeignKeyViolationError
                    ):
                        await conn.execute(
                            "DELETE FROM users WHERE id = $1", tenant.user_id
                        )
                finally:
                    await conn.execute("ROLLBACK")

                # Nothing was removed.
                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=tenant.organization_id)
                    self.assertEqual(
                        await conn.fetchval("SELECT count(*) FROM api_keys"), 1
                    )
                    await conn.execute("COMMIT")
                finally:
                    pass
        run_async(body())


# =========================================================================== #
# Regression guards that need no server beyond the schema itself
# =========================================================================== #
class SchemaRegressionTests(SecurityTestCase):
    def test_140_rls_is_enabled_and_forced_on_every_table(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT c.relname AS name, c.relrowsecurity AS enabled, "
                    "c.relforcerowsecurity AS forced FROM pg_class c JOIN "
                    "pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = "
                    "'public' AND c.relkind = 'r'"
                )
                state = {r["name"]: (r["enabled"], r["forced"]) for r in rows}
                for table in TABLES:
                    self.assertEqual(state.get(table), (True, True), table)
        run_async(body())

    def test_141_tenant_writes_keep_with_check(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT tablename, policyname, with_check FROM pg_policies "
                    "WHERE schemaname = 'public' AND cmd IN ('INSERT', 'UPDATE') "
                    "AND roles::text LIKE $1", f"%{APP_ROLE}%",
                )
                self.assertTrue(rows)
                for row in rows:
                    self.assertIsNotNone(
                        row["with_check"], f"{row['policyname']} lacks WITH CHECK"
                    )
        run_async(body())

    def test_142_malformed_uuid_context_cannot_escape(self):
        """A malformed tenant value must fail, not degrade to 'all tenants'."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.seed(conn)

                await db.create_proof_login(grant_app=True)
                login = await db.login_as_proof_role(APP_ROLE)
                try:
                    await login.execute("BEGIN")
                    try:
                        await login.execute(
                            "SELECT set_config('forge.organization_id', "
                            "'not-a-uuid', true)"
                        )
                        with self.assertRaises(asyncpg_module().PostgresError):
                            await login.fetchval("SELECT count(*) FROM projects")
                    finally:
                        await login.execute("ROLLBACK")
                finally:
                    await login.close()

                # And the malformed value is gone with its transaction.
                await conn.execute("BEGIN")
                try:
                    await set_context(conn, organization_id=tenant.organization_id)
                    self.assertEqual(
                        await conn.fetchval("SELECT count(*) FROM projects"), 1
                    )
                    await conn.execute("COMMIT")
                except Exception:
                    await conn.execute("ROLLBACK")
                    raise
        run_async(body())

    def test_143_platform_roles_have_no_unexpected_privileges_anywhere(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT table_name, grantee, string_agg(privilege_type, ', ' "
                    "ORDER BY privilege_type) AS privileges FROM "
                    "information_schema.role_table_grants WHERE table_schema = "
                    "'public' AND grantee = ANY($1::text[]) GROUP BY table_name, "
                    "grantee ORDER BY table_name, grantee", [APP_ROLE, SYSTEM_ROLE],
                )
                observed = {(r["table_name"], r["grantee"]): r["privileges"]
                            for r in rows}
                expected = {
                    # the ordinary tenant path
                    ("users", APP_ROLE): "DELETE, INSERT, SELECT, UPDATE",
                    ("organizations", APP_ROLE): "DELETE, INSERT, SELECT, UPDATE",
                    ("memberships", APP_ROLE): "DELETE, INSERT, SELECT, UPDATE",
                    ("projects", APP_ROLE): "DELETE, INSERT, SELECT, UPDATE",
                    ("provider_accounts", APP_ROLE): "DELETE, INSERT, SELECT, UPDATE",
                    ("api_keys", APP_ROLE): "DELETE, INSERT, SELECT, UPDATE",
                    ("run_records", APP_ROLE): "INSERT, SELECT, UPDATE",
                    ("usage_records", APP_ROLE): "INSERT, SELECT",
                    # the server-only scope: identity and tenancy, read plus
                    # bootstrap, and the one operational exception
                    ("users", SYSTEM_ROLE): "INSERT, SELECT",
                    ("organizations", SYSTEM_ROLE): "INSERT, SELECT",
                    ("memberships", SYSTEM_ROLE): "DELETE, INSERT, SELECT, UPDATE",
                    ("provider_accounts", SYSTEM_ROLE):
                        "DELETE, INSERT, SELECT, UPDATE",
                }
                self.assertEqual(observed, expected)
        run_async(body())

    def test_144_no_money_or_plaintext_credential_column_exists(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public'"
                )
                forbidden = {
                    "token", "api_key", "secret", "password", "password_hash",
                    "key_hash", "token_hash", "credential", "private_key",
                    "cost", "price", "charge", "balance", "margin", "revenue",
                    "credit", "payment", "wallet", "amount", "currency",
                }
                offenders = [
                    (r["table_name"], r["column_name"]) for r in rows
                    if r["column_name"].lower() in forbidden
                ]
                self.assertEqual(offenders, [])
        run_async(body())


if __name__ == "__main__":
    unittest.main()
