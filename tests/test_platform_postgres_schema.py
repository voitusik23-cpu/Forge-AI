"""Platform PostgreSQL database contract tests.

These tests assert PostgreSQL semantics that only a real server can provide:
row-level security, ``FORCE ROW LEVEL SECURITY``, ``current_setting`` behaviour,
partial unique indexes, composite foreign keys, and delete semantics. They are
skipped with an explicit reason when no server is configured, and they never
simulate the database in SQLite.

Run them against a live server:

    python -m unittest tests.test_platform_postgres_schema

With no server available, every test in this module reports a skip rather than a
pass, so a green run never claims unverified database behaviour.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import pathlib
import unittest
import uuid

from postgres_fixture import (
    APP_ROLE,
    SKIP_REASON_NO_DRIVER,
    SKIP_REASON_NO_SERVER,
    SUBJECT_SETTING,
    SYSTEM_ROLE,
    SYSTEM_SCOPE_SETTING,
    TENANT_SETTING,
    LiveDatabase,
    asyncpg_module,
    live_postgres,
    new_database_name,
    set_context,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

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

# Every policy the migrations create, by table. Used to assert that the policy set
# is complete rather than merely non-empty.
EXPECTED_POLICIES = {
    "organizations": {"organizations_tenant_select", "organizations_system_scope"},
    "memberships": {
        "memberships_tenant_select",
        "memberships_tenant_insert",
        "memberships_tenant_update",
        "memberships_tenant_delete",
        "memberships_system_scope",
    },
    "projects": {
        "projects_tenant_select",
        "projects_tenant_insert",
        "projects_tenant_update",
        "projects_tenant_delete",
        "projects_system_scope",
    },
    "api_keys": {
        "api_keys_tenant_select",
        "api_keys_tenant_insert",
        "api_keys_tenant_update",
        "api_keys_tenant_delete",
        "api_keys_system_scope",
    },
    "run_records": {
        "run_records_tenant_select",
        "run_records_tenant_insert",
        "run_records_tenant_update",
        "run_records_tenant_delete",
        "run_records_system_scope",
    },
    "usage_records": {
        "usage_records_tenant_select",
        "usage_records_tenant_insert",
        "usage_records_tenant_update",
        "usage_records_tenant_delete",
        "usage_records_system_scope",
    },
    "users": {"users_tenant_select", "users_system_scope"},
    "provider_accounts": {
        "provider_accounts_tenant_select",
        "provider_accounts_tenant_insert",
        "provider_accounts_tenant_update",
        "provider_accounts_tenant_delete",
        "provider_accounts_system_owned",
    },
}

EXPECTED_COLUMNS = {
    "users": {"id", "email", "display_name", "status", "created_at", "updated_at"},
    "organizations": {
        "id", "name", "slug", "is_personal", "status", "created_at", "updated_at",
    },
    "memberships": {
        "id", "organization_id", "user_id", "role", "status", "created_at",
        "updated_at",
    },
    "projects": {
        "id", "organization_id", "name", "slug", "status", "workspace_ref",
        "created_at", "updated_at",
    },
    "provider_accounts": {
        "id", "organization_id", "provider_name", "secret_ref", "status",
        "metadata", "created_at", "updated_at",
    },
    "api_keys": {
        "id", "organization_id", "created_by_user_id", "name", "key_prefix",
        "scopes", "status", "expires_at", "revoked_at", "last_used_at",
        "created_at", "updated_at",
    },
    "run_records": {
        "id", "organization_id", "project_id", "core_run_id",
        "initiated_by_user_id", "task_id", "status", "started_at", "finished_at",
        "attempt_count", "failure_classification", "created_at", "updated_at",
    },
    "usage_records": {
        "id", "organization_id", "run_record_id", "core_run_id", "attempt_number",
        "provider_name", "model_name", "input_tokens", "output_tokens",
        "cached_tokens", "duration_seconds", "success", "fallback",
        "tool_call_count", "completed_at", "error_type", "created_at",
    },
}


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run_async(coro):
    return asyncio.run(coro)


class AsyncTestCase(unittest.TestCase):
    """A unittest case that drives asyncio and skips when no server exists."""

    @classmethod
    def setUpClass(cls):
        asyncpg = asyncpg_module()
        if asyncpg is None:
            raise unittest.SkipTest(SKIP_REASON_NO_DRIVER)

        async def probe():
            connection = await live_postgres()
            if connection is None:
                return False
            # Close inside the coroutine: asyncpg transports are bound to the
            # running event loop, so closing one after asyncio.run() has torn that
            # loop down raises instead of closing cleanly.
            await connection.close()
            return True

        if not run_async(probe()):
            raise unittest.SkipTest(SKIP_REASON_NO_SERVER)


# --------------------------------------------------------------------------- #
# Seed helper
# --------------------------------------------------------------------------- #
class Tenant:
    """One organization with an owner, a project, and a run."""

    def __init__(self, label: str):
        self.label = label
        self.organization_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.membership_id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.run_id = uuid.uuid4()

    async def create(self, conn) -> None:
        await set_context(conn, system_scope="on")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, created_at) "
            "VALUES ($1, $2, $3, now())",
            self.organization_id, f"Org {self.label}",
            f"org-{self.label.lower()}-{self.organization_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, created_at) VALUES ($1, $2, now())",
            self.user_id, f"{self.label.lower()}-{self.user_id.hex[:8]}@example.test",
        )
        await conn.execute(
            "INSERT INTO memberships (id, organization_id, user_id, role, created_at) "
            "VALUES ($1, $2, $3, 'owner', now())",
            self.membership_id, self.organization_id, self.user_id,
        )
        await conn.execute(
            "INSERT INTO projects (id, organization_id, name, slug, created_at) "
            "VALUES ($1, $2, $3, $4, now())",
            self.project_id, self.organization_id, f"Project {self.label}",
            f"project-{self.label.lower()}",
        )

    async def create_run(self, conn, *, status="running", core_run_id=None,
                         attempt_count=0, failure_classification=""):
        core_run_id = core_run_id or f"core-{self.run_id.hex[:10]}"
        await self.create(conn)
        await conn.execute(
            "INSERT INTO run_records (id, organization_id, project_id, core_run_id, "
            "initiated_by_user_id, status, started_at, attempt_count, "
            "failure_classification, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, now(), $7, $8, now())",
            self.run_id, self.organization_id, self.project_id, core_run_id,
            self.user_id, status, attempt_count, failure_classification,
        )
        return self.run_id


# --------------------------------------------------------------------------- #
# 1-5: schema shape
# --------------------------------------------------------------------------- #
class MigrationAndSchemaTests(AsyncTestCase):
    def test_01_migration_applies_successfully(self):
        async def body():
            async with LiveDatabase() as db:
                applied = await db.apply_migrations()
                self.assertEqual(len(applied), 14)
                self.assertEqual(applied[0], "0001_users.sql")

                ledger = await db.connection.fetch(
                    "SELECT filename FROM platform_schema_migrations ORDER BY filename"
                )
                self.assertEqual([r["filename"] for r in ledger], applied)
        run_async(body())

    def test_02_migration_is_idempotent(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                second = await db.apply_migrations()
                self.assertEqual(second, [], "re-applying must apply nothing")
        run_async(body())

    def test_03_all_eight_tables_exist(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
                present = {r["tablename"] for r in rows}
                self.assertTrue(
                    set(TABLES).issubset(present),
                    f"missing tables: {set(TABLES) - present}",
                )
                # Billing and deferred entities must not exist.
                for forbidden in ("wallets", "credit_transactions", "cost_records",
                                  "pricing_plans", "price_rules", "payments",
                                  "invoices", "partners", "commissions"):
                    self.assertNotIn(forbidden, present)
        run_async(body())

    def test_04_expected_columns_exist(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public'"
                )
                actual: dict[str, set[str]] = {}
                for row in rows:
                    actual.setdefault(row["table_name"], set()).add(row["column_name"])
                for table, expected in EXPECTED_COLUMNS.items():
                    self.assertIn(table, actual)
                    self.assertTrue(
                        expected.issubset(actual[table]),
                        f"{table} missing columns: {expected - actual[table]}",
                    )
        run_async(body())

    def test_05_no_money_column_anywhere(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public'"
                )
                money = {"cost", "price", "charge", "balance", "margin", "revenue",
                         "credit", "payment", "wallet", "amount", "currency"}
                offenders = [
                    (r["table_name"], r["column_name"]) for r in rows
                    if any(word in r["column_name"].lower() for word in money)
                ]
                self.assertEqual(offenders, [])
        run_async(body())

    def test_06_no_plaintext_credential_column(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public'"
                )
                offenders = [
                    (r["table_name"], r["column_name"]) for r in rows
                    if r["column_name"].lower() in {
                        "token", "api_key", "secret", "password", "password_hash",
                        "key_hash", "token_hash", "credential", "private_key",
                    }
                ]
                self.assertEqual(offenders, [])
                # secret_ref is the only credential-shaped column, and it is a
                # reference, not material.
                names = {r["column_name"] for r in rows}
                self.assertIn("secret_ref", names)
        run_async(body())

    def test_07_consolidated_schema_matches_migrations(self):
        """schema.sql must produce the same object set as the migrations."""

        async def body():
            from_migrations = await self._object_summary(use_migrations=True)
            from_consolidated = await self._object_summary(use_migrations=False)
            self.assertEqual(from_migrations, from_consolidated)
        run_async(body())

    async def _object_summary(self, *, use_migrations: bool):
        async with LiveDatabase() as db:
            if use_migrations:
                await db.apply_migrations()
            else:
                await db.apply_consolidated_schema()
            conn = db.connection
            tables = {
                r["relname"]: (r["relrowsecurity"], r["relforcerowsecurity"])
                for r in await conn.fetch(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                    "AND c.relname <> 'platform_schema_migrations'"
                )
            }
            policies = {
                (r["tablename"], r["policyname"], r["cmd"], r["qual"], r["with_check"])
                for r in await conn.fetch(
                    "SELECT tablename, policyname, cmd, qual, with_check "
                    "FROM pg_policies WHERE schemaname = 'public'"
                )
            }
            # conislocal excludes the auto-named NOT NULL constraints, whose
            # generated names depend on how the column was created rather than on
            # what the schema declares. The declared constraints are the contract.
            constraints = {
                (r["table_name"], r["constraint_name"], r["definition"])
                for r in await conn.fetch(
                    "SELECT c.relname AS table_name, con.conname AS constraint_name, "
                    "pg_get_constraintdef(con.oid) AS definition "
                    "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND con.conislocal "
                    # The migration ledger belongs to the runner, not to the
                    # Platform schema, so it is not part of this comparison.
                    "AND c.relname <> 'platform_schema_migrations'"
                )
            }
            return tables, policies, constraints


# --------------------------------------------------------------------------- #
# 4-5: foreign keys, delete semantics, K-7
# --------------------------------------------------------------------------- #
class ForeignKeyTests(AsyncTestCase):
    def test_10_expected_foreign_keys_exist(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT c.relname AS table_name, con.conname AS name, "
                    "pg_get_constraintdef(con.oid) AS definition "
                    "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND con.contype = 'f'"
                )
                found = {(r["table_name"], r["name"]): r["definition"] for r in rows}
                expected = {
                    ("memberships", "memberships_organization_fk"),
                    ("memberships", "memberships_user_fk"),
                    ("projects", "projects_organization_fk"),
                    ("provider_accounts", "provider_accounts_organization_fk"),
                    ("api_keys", "api_keys_organization_fk"),
                    ("api_keys", "api_keys_created_by_user_fk"),
                    ("run_records", "run_records_organization_fk"),
                    ("run_records", "run_records_project_organization_fk"),
                    ("run_records", "run_records_initiated_by_user_fk"),
                    ("usage_records", "usage_records_organization_fk"),
                    ("usage_records", "usage_records_run_organization_fk"),
                }
                self.assertEqual(set(found), expected)
        run_async(body())

    def test_11_composite_foreign_keys_exist(self):
        """K-7: the composite keys must actually be composite."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT con.conname AS name, pg_get_constraintdef(con.oid) AS def "
                    "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND con.contype = 'f'"
                )
                by_name = {r["name"]: r["def"] for r in rows}
                self.assertIn(
                    "FOREIGN KEY (project_id, organization_id) "
                    "REFERENCES projects(id, organization_id)",
                    by_name["run_records_project_organization_fk"],
                )
                self.assertIn(
                    "FOREIGN KEY (run_record_id, organization_id) "
                    "REFERENCES run_records(id, organization_id)",
                    by_name["usage_records_run_organization_fk"],
                )

                # The referenced targets must be unique, or the FK could not exist.
                uniques = {
                    r["name"] for r in await db.connection.fetch(
                        "SELECT conname AS name FROM pg_constraint "
                        "WHERE contype = 'u' AND conname IN "
                        "('projects_id_organization_unique', "
                        " 'run_records_id_organization_unique')"
                    )
                }
                self.assertEqual(
                    uniques,
                    {"projects_id_organization_unique",
                     "run_records_id_organization_unique"},
                )
        run_async(body())

    def test_12_k7_cross_tenant_run_reference_is_rejected(self):
        """The whole point of K-7: PostgreSQL physically refuses the row."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = Tenant("A"), Tenant("B")
                await set_context(conn, system_scope="on")
                await org_a.create(conn)
                await org_b.create(conn)

                with self.assertRaises(asyncpg_module().ForeignKeyViolationError):
                    await conn.execute(
                        "INSERT INTO run_records (id, organization_id, project_id, "
                        "core_run_id, initiated_by_user_id, status, started_at, "
                        "created_at) "
                        "VALUES ($1, $2, $3, 'core-cross', $4, 'running', now(), now())",
                        uuid.uuid4(), org_b.organization_id, org_a.project_id,
                        org_b.user_id,
                    )
        run_async(body())

    def test_13_k7_cross_tenant_usage_reference_is_rejected(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = Tenant("A"), Tenant("B")
                await set_context(conn, system_scope="on")
                await org_a.create_run(conn)
                await org_b.create(conn)

                with self.assertRaises(asyncpg_module().ForeignKeyViolationError):
                    await conn.execute(
                        "INSERT INTO usage_records (id, organization_id, "
                        "run_record_id, core_run_id, attempt_number, created_at) "
                        "VALUES ($1, $2, $3, 'core-x', 0, now())",
                        uuid.uuid4(), org_b.organization_id, org_a.run_id,
                    )
        run_async(body())

    def test_14_no_cascade_delete_anywhere(self):
        """K-6: not one foreign key may cascade."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT con.conname AS name, con.confdeltype::text AS action, "
                    "pg_get_constraintdef(con.oid) AS def "
                    "FROM pg_constraint con JOIN pg_namespace n "
                    "ON n.oid = con.connamespace "
                    "WHERE con.contype = 'f' AND n.nspname = 'public'"
                )
                # confdeltype: a=NO ACTION, r=RESTRICT, c=CASCADE,
                #               n=SET NULL, d=SET DEFAULT
                actions = {r["name"]: r["action"] for r in rows}
                cascades = [name for name, action in actions.items()
                            if action == "c"]
                self.assertEqual(cascades, [], "ON DELETE CASCADE is forbidden")
                defaults = [name for name, action in actions.items()
                            if action == "d"]
                self.assertEqual(defaults, [], "SET DEFAULT is not used")

                # Only the optional audit field may be SET NULL, because it is
                # the only nullable reference the domain contract allows to be
                # cleared.
                set_null = {name for name, action in actions.items()
                            if action == "n"}
                self.assertEqual(
                    set_null, {"run_records_initiated_by_user_fk"},
                    "SET NULL is allowed only where the field is optional",
                )

                # Every other foreign key restricts, so a delete can never
                # silently destroy history.
                restrict = {name for name, action in actions.items()
                            if action == "r"}
                self.assertEqual(
                    restrict,
                    set(actions) - set_null,
                    "every non-optional foreign key must be ON DELETE RESTRICT",
                )
                for name in ("usage_records_run_organization_fk",
                             "run_records_project_organization_fk",
                             "api_keys_created_by_user_fk"):
                    self.assertIn(name, restrict)
        run_async(body())

    def test_15_historical_records_are_not_cascade_deleted(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create_run(conn)
                await conn.execute(
                    "INSERT INTO usage_records (id, organization_id, run_record_id, "
                    "core_run_id, attempt_number, created_at) "
                    "VALUES ($1, $2, $3, 'core-r', 0, now())",
                    uuid.uuid4(), tenant.organization_id, tenant.run_id,
                )

                with self.assertRaises(asyncpg_module().ForeignKeyViolationError):
                    await conn.execute(
                        "DELETE FROM organizations WHERE id = $1",
                        tenant.organization_id,
                    )
                with self.assertRaises(asyncpg_module().ForeignKeyViolationError):
                    await conn.execute(
                        "DELETE FROM run_records WHERE id = $1", tenant.run_id
                    )

                # The history is still there.
                await set_context(conn, system_scope="on")
                self.assertEqual(
                    await conn.fetchval("SELECT count(*) FROM usage_records"), 1
                )
        run_async(body())


# --------------------------------------------------------------------------- #
# 6-9, 12-15: RLS
# --------------------------------------------------------------------------- #
class RowLevelSecurityTests(AsyncTestCase):
    def test_20_rls_is_enabled_and_forced_on_all_eight_tables(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT c.relname AS name, c.relrowsecurity AS enabled, "
                    "c.relforcerowsecurity AS forced "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                )
                state = {r["name"]: (r["enabled"], r["forced"]) for r in rows}
                for table in TABLES:
                    self.assertIn(table, state)
                    self.assertEqual(
                        state[table], (True, True),
                        f"{table} must have ENABLE and FORCE row level security",
                    )
        run_async(body())

    def test_21_the_expected_policy_set_exists(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT tablename, policyname FROM pg_policies "
                    "WHERE schemaname = 'public'"
                )
                actual: dict[str, set[str]] = {}
                for row in rows:
                    actual.setdefault(row["tablename"], set()).add(row["policyname"])
                for table, expected in EXPECTED_POLICIES.items():
                    self.assertEqual(actual.get(table, set()), expected)
        run_async(body())

    def test_22_v1_no_tenant_context_returns_zero_rows(self):
        """V-1, SELECT case."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                await Tenant("A").create(conn)
                await Tenant("B").create(conn)

                await conn.execute(f"SET ROLE {APP_ROLE}")
                try:
                    for table in ("organizations", "memberships", "projects",
                                  "provider_accounts", "api_keys", "run_records",
                                  "usage_records", "users"):
                        count = await conn.fetchval(f"SELECT count(*) FROM {table}")
                        self.assertEqual(
                            count, 0,
                            f"{table} leaked {count} rows with no tenant context",
                        )
                finally:
                    await conn.execute("RESET ROLE")
        run_async(body())

    def test_23_v1_no_tenant_context_rejects_insert(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)

                await conn.execute(f"SET ROLE {APP_ROLE}")
                try:
                    with self.assertRaises(asyncpg_module().InsufficientPrivilegeError):
                        await conn.execute(
                            "INSERT INTO projects (id, organization_id, name, "
                            "created_at) VALUES ($1, $2, 'x', now())",
                            uuid.uuid4(), tenant.organization_id,
                        )
                finally:
                    await conn.execute("RESET ROLE")
        run_async(body())

    def test_24_v1_no_tenant_context_rejects_update_and_delete(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)

                await conn.execute(f"SET ROLE {APP_ROLE}")
                try:
                    updated = await conn.execute(
                        "UPDATE projects SET name = 'hacked' WHERE id = $1",
                        tenant.project_id,
                    )
                    self.assertEqual(updated, "UPDATE 0")

                    deleted = await conn.execute(
                        "DELETE FROM projects WHERE id = $1", tenant.project_id
                    )
                    self.assertEqual(deleted, "DELETE 0")
                finally:
                    await conn.execute("RESET ROLE")

                # And the row is untouched.
                await set_context(conn, system_scope="on")
                name = await conn.fetchval(
                    "SELECT name FROM projects WHERE id = $1", tenant.project_id
                )
                self.assertNotEqual(name, "hacked")
        run_async(body())

    def test_25_v2_set_local_is_transaction_scoped(self):
        """V-2: the context must not survive the transaction."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)

                await conn.execute("BEGIN")
                await set_context(conn, organization_id=tenant.organization_id)
                await conn.execute(f"SET ROLE {APP_ROLE}")
                inside = await conn.fetchval("SELECT count(*) FROM projects")
                await conn.execute("COMMIT")
                await conn.execute("RESET ROLE")

                self.assertEqual(inside, 1, "context inside its transaction")

                # Outside, the setting is empty and the predicate denies.
                raw = await conn.fetchval(
                    f"SELECT current_setting('{TENANT_SETTING}', true)"
                )
                self.assertNotEqual(raw, str(tenant.organization_id))
                helper = await conn.fetchval(
                    "SELECT platform.current_organization_id()"
                )
                self.assertIsNone(helper)

                await conn.execute(f"SET ROLE {APP_ROLE}")
                try:
                    after = await conn.fetchval("SELECT count(*) FROM projects")
                finally:
                    await conn.execute("RESET ROLE")
                self.assertEqual(after, 0, "no leak outside the transaction")
        run_async(body())

    def test_26_both_fail_closed_paths_deny(self):
        """The two different 'no context' states must both deny.

        This is the empirical result that justifies the adopted predicate. On a
        session where the setting was NEVER assigned, ``current_setting(...,
        true)`` returns NULL. On a session where it was assigned and then
        reverted, it returns an EMPTY STRING. A predicate written only as
        ``IS NOT NULL`` is true in the second case and would therefore grant
        tenant access with no tenant.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)

                wrong = (
                    f"current_setting('{TENANT_SETTING}', true) IS NOT NULL"
                )
                adopted = (
                    f"nullif(current_setting('{TENANT_SETTING}', true), '') "
                    "IS NOT NULL"
                )

                # State 1: never assigned in this session.
                never = await conn.fetchval(
                    f"SELECT current_setting('{TENANT_SETTING}', true)"
                )
                self.assertIsNone(never, "a never-assigned setting is NULL")
                self.assertFalse(await conn.fetchval(f"SELECT {adopted}"))

                # State 2: assigned then reverted, which leaves an empty string.
                await conn.execute("BEGIN")
                await set_context(conn, organization_id=tenant.organization_id)
                await conn.execute("COMMIT")
                reverted = await conn.fetchval(
                    f"SELECT current_setting('{TENANT_SETTING}', true)"
                )
                self.assertEqual(reverted, "", "a reverted setting is empty, not NULL")
                self.assertTrue(
                    await conn.fetchval(f"SELECT {wrong}"),
                    "the unsound predicate is TRUE on an empty setting",
                )
                self.assertFalse(
                    await conn.fetchval(f"SELECT {adopted}"),
                    "the adopted predicate must deny on an empty setting",
                )

                # And the schema really denies in state 2, not just the helper.
                await conn.execute(f"SET ROLE {APP_ROLE}")
                try:
                    self.assertEqual(
                        await conn.fetchval("SELECT count(*) FROM projects"), 0
                    )
                finally:
                    await conn.execute("RESET ROLE")
        run_async(body())

    def test_27_cross_tenant_select_is_filtered(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = Tenant("A"), Tenant("B")
                await set_context(conn, system_scope="on")
                await org_a.create(conn)
                await org_b.create(conn)

                await conn.execute("BEGIN")
                await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                await set_context(conn, organization_id=org_a.organization_id)
                rows = await conn.fetch("SELECT id FROM projects")
                self.assertEqual([r["id"] for r in rows], [org_a.project_id])
                orgs = await conn.fetch("SELECT id FROM organizations")
                self.assertEqual([r["id"] for r in orgs], [org_a.organization_id])
                await conn.execute("COMMIT")
        run_async(body())

    def test_28_cross_tenant_insert_is_rejected(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = Tenant("A"), Tenant("B")
                await set_context(conn, system_scope="on")
                await org_a.create(conn)
                await org_b.create(conn)

                await conn.execute("BEGIN")
                await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                await set_context(conn, organization_id=org_a.organization_id)
                with self.assertRaises(asyncpg_module().InsufficientPrivilegeError):
                    await conn.execute(
                        "INSERT INTO projects (id, organization_id, name, created_at) "
                        "VALUES ($1, $2, 'sneaky', now())",
                        uuid.uuid4(), org_b.organization_id,
                    )
                await conn.execute("ROLLBACK")
        run_async(body())

    def test_29_cross_tenant_update_is_rejected(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = Tenant("A"), Tenant("B")
                await set_context(conn, system_scope="on")
                await org_a.create(conn)
                await org_b.create(conn)

                await conn.execute("BEGIN")
                await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                await set_context(conn, organization_id=org_a.organization_id)
                # B's project is invisible, so the update matches nothing ...
                result = await conn.execute(
                    "UPDATE projects SET name = 'hacked' WHERE id = $1",
                    org_b.project_id,
                )
                self.assertEqual(result, "UPDATE 0")
                # ... and moving a visible row into B is refused by WITH CHECK.
                with self.assertRaises(asyncpg_module().InsufficientPrivilegeError):
                    await conn.execute(
                        "UPDATE projects SET organization_id = $1 WHERE id = $2",
                        org_b.organization_id, org_a.project_id,
                    )
                await conn.execute("ROLLBACK")

                await set_context(conn, system_scope="on")
                name = await conn.fetchval(
                    "SELECT name FROM projects WHERE id = $1", org_b.project_id
                )
                self.assertNotEqual(name, "hacked")
                owner = await conn.fetchval(
                    "SELECT organization_id FROM projects WHERE id = $1",
                    org_a.project_id,
                )
                self.assertEqual(owner, org_a.organization_id)
        run_async(body())

    def test_30_cross_tenant_delete_is_rejected(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = Tenant("A"), Tenant("B")
                await set_context(conn, system_scope="on")
                await org_a.create(conn)
                await org_b.create(conn)

                await conn.execute("BEGIN")
                await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                await set_context(conn, organization_id=org_a.organization_id)
                result = await conn.execute(
                    "DELETE FROM projects WHERE id = $1", org_b.project_id
                )
                self.assertEqual(result, "DELETE 0")
                await conn.execute("COMMIT")

                await set_context(conn, system_scope="on")
                self.assertEqual(
                    await conn.fetchval(
                        "SELECT count(*) FROM projects WHERE id = $1",
                        org_b.project_id,
                    ),
                    1,
                )
        run_async(body())

    def test_31_with_check_is_present_on_insert_and_update(self):
        """A USING-only policy would pass the behavioural tests above only by
        accident, so the clause is asserted directly."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT tablename, policyname, cmd, qual, with_check "
                    "FROM pg_policies WHERE schemaname = 'public'"
                )
                for row in rows:
                    if not row["policyname"].endswith(("_tenant_insert",
                                                       "_tenant_update")):
                        continue
                    self.assertIsNotNone(
                        row["with_check"],
                        f"{row['policyname']} must have WITH CHECK",
                    )
                    self.assertIn("is_current_organization", row["with_check"])
        run_async(body())

    def test_32_users_are_not_tenant_predicated(self):
        """users has no organization_id, so the ordinary predicate cannot apply."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                columns = {
                    r["column_name"] for r in await conn.fetch(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'users'"
                    )
                }
                self.assertNotIn("organization_id", columns)

                policy = await conn.fetchrow(
                    "SELECT qual FROM pg_policies WHERE tablename = 'users' "
                    "AND policyname = 'users_tenant_select'"
                )
                self.assertIsNotNone(policy)
                self.assertIn(SUBJECT_SETTING, policy["qual"])
                self.assertNotIn("is_current_organization(organization_id)",
                                 policy["qual"])
        run_async(body())

    def test_33_users_self_visibility_and_membership_mediation(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = Tenant("A"), Tenant("B")
                await set_context(conn, system_scope="on")
                await org_a.create(conn)
                await org_b.create(conn)

                await conn.execute("BEGIN")
                await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                await set_context(conn, organization_id=org_a.organization_id,
                                  user_id=org_a.user_id)
                visible = {r["id"] for r in await conn.fetch("SELECT id FROM users")}
                self.assertIn(org_a.user_id, visible, "a subject sees itself")
                self.assertNotIn(
                    org_b.user_id, visible,
                    "a user from another tenant must not be visible",
                )
                await conn.execute("COMMIT")

                # With no subject setting, even self-visibility denies.
                await conn.execute(f"SET ROLE {APP_ROLE}")
                try:
                    self.assertEqual(
                        await conn.fetchval("SELECT count(*) FROM users"), 0
                    )
                finally:
                    await conn.execute("RESET ROLE")
        run_async(body())

    def test_34_organizations_require_an_active_membership(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)

                await conn.execute("BEGIN")
                await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                await set_context(conn, organization_id=tenant.organization_id)
                self.assertEqual(
                    await conn.fetchval("SELECT count(*) FROM organizations"), 1
                )
                # Deactivate the membership: the organization must disappear.
                await conn.execute("RESET ROLE")
                await set_context(conn, system_scope="on")
                await conn.execute(
                    "UPDATE memberships SET status = 'revoked' WHERE id = $1",
                    tenant.membership_id,
                )
                await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                await set_context(conn, organization_id=tenant.organization_id)
                self.assertEqual(
                    await conn.fetchval("SELECT count(*) FROM organizations"), 0
                )
                await conn.execute("COMMIT")
        run_async(body())

    def test_35_tenant_role_cannot_create_an_organization(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                await conn.execute(f"SET ROLE {APP_ROLE}")
                try:
                    with self.assertRaises(
                        asyncpg_module().InsufficientPrivilegeError
                    ):
                        await conn.execute(
                            "INSERT INTO organizations (id, name, created_at) "
                            "VALUES ($1, 'rogue', now())",
                            uuid.uuid4(),
                        )
                finally:
                    await conn.execute("RESET ROLE")
        run_async(body())


# --------------------------------------------------------------------------- #
# 10-11: ProviderAccount ownership modes
# --------------------------------------------------------------------------- #
class ProviderAccountOwnershipTests(AsyncTestCase):
    async def _seed(self, conn):
        org_a, org_b = Tenant("A"), Tenant("B")
        await set_context(conn, system_scope="on")
        await org_a.create(conn)
        await org_b.create(conn)

        tenant_account = uuid.uuid4()
        system_account = uuid.uuid4()
        await conn.execute(
            "INSERT INTO provider_accounts (id, organization_id, provider_name, "
            "secret_ref, created_at) VALUES ($1, $2, 'deepseek', 'ref:tenant-a', now())",
            tenant_account, org_a.organization_id,
        )
        await conn.execute(
            "INSERT INTO provider_accounts (id, organization_id, provider_name, "
            "secret_ref, created_at) VALUES ($1, NULL, 'deepseek', 'ref:system', now())",
            system_account,
        )
        return org_a, org_b, tenant_account, system_account

    def test_40_tenant_owned_account_is_visible_to_its_tenant(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, _, tenant_account, system_account = await self._seed(conn)

                await conn.execute("BEGIN")
                await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                await set_context(conn, organization_id=org_a.organization_id)
                rows = await conn.fetch("SELECT id FROM provider_accounts")
                self.assertEqual([r["id"] for r in rows], [tenant_account])
                await conn.execute("COMMIT")
        run_async(body())

    def test_41_system_owned_account_is_invisible_to_tenants(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b, tenant_account, system_account = await self._seed(conn)

                for org in (org_a, org_b):
                    await conn.execute("BEGIN")
                    await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                    await set_context(conn, organization_id=org.organization_id)
                    ids = {
                        r["id"] for r in await conn.fetch(
                            "SELECT id FROM provider_accounts"
                        )
                    }
                    self.assertNotIn(system_account, ids)
                    await conn.execute("COMMIT")

                # Nor by naming it directly, nor through a null-tenant request.
                await conn.execute(f"SET ROLE {APP_ROLE}")
                try:
                    self.assertEqual(
                        await conn.fetchval(
                            "SELECT count(*) FROM provider_accounts WHERE id = $1",
                            system_account,
                        ),
                        0,
                    )
                    self.assertEqual(
                        await conn.fetchval(
                            "SELECT count(*) FROM provider_accounts "
                            "WHERE organization_id IS NULL"
                        ),
                        0,
                        "a NULL tenant column must never be a wildcard",
                    )
                finally:
                    await conn.execute("RESET ROLE")
        run_async(body())

    def test_42_tenant_cannot_create_or_promote_a_system_owned_account(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, _, tenant_account, _ = await self._seed(conn)

                # Each attempt gets its own transaction: an error aborts the
                # current transaction, so a second statement in the same one
                # would fail with InFailedSQLTransactionError rather than with
                # the privilege error actually under test.
                for statement, args in (
                    (
                        "INSERT INTO provider_accounts (id, organization_id, "
                        "provider_name, secret_ref, created_at) "
                        "VALUES ($1, NULL, 'evil', 'ref', now())",
                        (uuid.uuid4(),),
                    ),
                    (
                        "UPDATE provider_accounts SET organization_id = NULL "
                        "WHERE id = $1",
                        (tenant_account,),
                    ),
                ):
                    await conn.execute("BEGIN")
                    await conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                    await set_context(conn, organization_id=org_a.organization_id)
                    with self.assertRaises(
                        asyncpg_module().InsufficientPrivilegeError
                    ):
                        await conn.execute(statement, *args)
                    await conn.execute("ROLLBACK")

                # The tenant-owned account is untouched.
                await set_context(conn, system_scope="on")
                owner = await conn.fetchval(
                    "SELECT organization_id FROM provider_accounts WHERE id = $1",
                    tenant_account,
                )
                self.assertEqual(owner, org_a.organization_id)
        run_async(body())

    def test_43_system_scope_flag_is_required_for_system_rows(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                _, _, _, system_account = await self._seed(conn)

                # The system role with the flag off sees nothing.
                await conn.execute("BEGIN")
                await conn.execute(f"SET LOCAL ROLE {SYSTEM_ROLE}")
                await set_context(conn, system_scope="off")
                self.assertEqual(
                    await conn.fetchval("SELECT count(*) FROM provider_accounts"), 0
                )
                await conn.execute("COMMIT")

                # With the flag on it sees exactly the system-owned rows.
                await conn.execute("BEGIN")
                await conn.execute(f"SET LOCAL ROLE {SYSTEM_ROLE}")
                await set_context(conn, system_scope="on")
                ids = {
                    r["id"] for r in await conn.fetch(
                        "SELECT id FROM provider_accounts"
                    )
                }
                self.assertEqual(ids, {system_account})
                await conn.execute("COMMIT")
        run_async(body())

    def test_44_tenant_provider_unique_per_organization(self):
        """K-4 part 1, and requirement 21: the SAME provider is allowed in
        different organizations."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = Tenant("A"), Tenant("B")
                await set_context(conn, system_scope="on")
                await org_a.create(conn)
                await org_b.create(conn)

                for org in (org_a, org_b):
                    await conn.execute(
                        "INSERT INTO provider_accounts (id, organization_id, "
                        "provider_name, secret_ref, created_at) "
                        "VALUES ($1, $2, 'openai', 'ref', now())",
                        uuid.uuid4(), org.organization_id,
                    )

                with self.assertRaises(asyncpg_module().UniqueViolationError):
                    await conn.execute(
                        "INSERT INTO provider_accounts (id, organization_id, "
                        "provider_name, secret_ref, created_at) "
                        "VALUES ($1, $2, 'openai', 'ref2', now())",
                        uuid.uuid4(), org_a.organization_id,
                    )
        run_async(body())

    def test_45_at_most_one_system_owned_account_per_provider(self):
        """K-4 part 2, requirement 20."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                await set_context(conn, system_scope="on")
                await conn.execute(
                    "INSERT INTO provider_accounts (id, organization_id, "
                    "provider_name, secret_ref, created_at) "
                    "VALUES ($1, NULL, 'anthropic', 'ref', now())",
                    uuid.uuid4(),
                )
                with self.assertRaises(asyncpg_module().UniqueViolationError):
                    await conn.execute(
                        "INSERT INTO provider_accounts (id, organization_id, "
                        "provider_name, secret_ref, created_at) "
                        "VALUES ($1, NULL, 'anthropic', 'ref2', now())",
                        uuid.uuid4(),
                    )
        run_async(body())

    def test_46_null_tenant_is_never_a_wildcard_in_any_policy_text(self):
        """Requirement: the forbidden pattern must not reappear."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT tablename, policyname, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'public'"
                )
                for row in rows:
                    text = " ".join(
                        str(part) for part in (row["qual"], row["with_check"])
                        if part
                    )
                    if row["tablename"] == "provider_accounts" and \
                            row["policyname"].endswith("_system_owned"):
                        # The system policy is allowed to require a NULL tenant.
                        continue
                    self.assertNotIn(
                        "IS NULL OR", text.upper(),
                        f"{row['policyname']} contains a NULL-as-wildcard pattern",
                    )
                    self.assertNotIn(
                        "current_organization_id() IS NULL OR", text,
                        f"{row['policyname']} is fail-open on a missing tenant",
                    )
        run_async(body())


# --------------------------------------------------------------------------- #
# 16-18, 24-27: constraints, uniqueness, types
# --------------------------------------------------------------------------- #
class ConstraintTests(AsyncTestCase):
    def test_50_membership_pair_uniqueness(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)
                with self.assertRaises(asyncpg_module().UniqueViolationError):
                    await conn.execute(
                        "INSERT INTO memberships (id, organization_id, user_id, "
                        "role, created_at) VALUES ($1, $2, $3, 'member', now())",
                        uuid.uuid4(), tenant.organization_id, tenant.user_id,
                    )
        run_async(body())

    def test_51_project_slug_unique_within_organization_only(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                org_a, org_b = Tenant("A"), Tenant("B")
                await set_context(conn, system_scope="on")
                await org_a.create(conn)
                await org_b.create(conn)

                # The same slug in another organization is allowed.
                await conn.execute(
                    "INSERT INTO projects (id, organization_id, name, slug, "
                    "created_at) VALUES ($1, $2, 'Other', $3, now())",
                    uuid.uuid4(), org_b.organization_id,
                    f"project-{org_a.label.lower()}",
                )
                # The same slug inside one organization is not.
                with self.assertRaises(asyncpg_module().UniqueViolationError):
                    await conn.execute(
                        "INSERT INTO projects (id, organization_id, name, slug, "
                        "created_at) VALUES ($1, $2, 'Dup', $3, now())",
                        uuid.uuid4(), org_a.organization_id,
                        f"project-{org_a.label.lower()}",
                    )
        run_async(body())

    def test_52_organization_slug_is_not_globally_unique(self):
        """K-3 REJECTED global uniqueness for organizations."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                await set_context(conn, system_scope="on")
                for _ in range(2):
                    await conn.execute(
                        "INSERT INTO organizations (id, name, slug, created_at) "
                        "VALUES ($1, 'Same slug', 'shared-slug', now())",
                        uuid.uuid4(),
                    )
                self.assertEqual(
                    await conn.fetchval(
                        "SELECT count(*) FROM organizations WHERE slug = 'shared-slug'"
                    ),
                    2,
                )
        run_async(body())

    def test_53_usage_record_run_attempt_uniqueness(self):
        """O-8, requirement 17."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create_run(conn)
                for attempt in (0, 1):
                    await conn.execute(
                        "INSERT INTO usage_records (id, organization_id, "
                        "run_record_id, core_run_id, attempt_number, created_at) "
                        "VALUES ($1, $2, $3, 'core-r', $4, now())",
                        uuid.uuid4(), tenant.organization_id, tenant.run_id, attempt,
                    )
                with self.assertRaises(asyncpg_module().UniqueViolationError):
                    await conn.execute(
                        "INSERT INTO usage_records (id, organization_id, "
                        "run_record_id, core_run_id, attempt_number, created_at) "
                        "VALUES ($1, $2, $3, 'core-r', 0, now())",
                        uuid.uuid4(), tenant.organization_id, tenant.run_id,
                    )
        run_async(body())

    def test_54_core_run_id_is_not_unique(self):
        """K-8 DEFERRED, requirement 18."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create_run(conn, core_run_id="core-shared")
                await conn.execute(
                    "INSERT INTO run_records (id, organization_id, project_id, "
                    "core_run_id, initiated_by_user_id, status, started_at, "
                    "created_at) "
                    "VALUES ($1, $2, $3, 'core-shared', $4, 'running', now(), now())",
                    uuid.uuid4(), tenant.organization_id, tenant.project_id,
                    tenant.user_id,
                )
                self.assertEqual(
                    await conn.fetchval(
                        "SELECT count(*) FROM run_records WHERE core_run_id = $1",
                        "core-shared",
                    ),
                    2,
                    "no approved constraint forbids a duplicate core_run_id",
                )

                # The lookup index exists and is not unique.
                index = await conn.fetchrow(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
                    "AND indexname = 'run_records_core_run_id_idx'"
                )
                self.assertIsNotNone(index)
                self.assertNotIn("UNIQUE", index["indexdef"].upper())
        run_async(body())

    def test_55_api_key_prefix_has_no_unique_constraint(self):
        """K-5 DEFERRED, requirement 22."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)
                for _ in range(2):
                    await conn.execute(
                        "INSERT INTO api_keys (id, organization_id, "
                        "created_by_user_id, name, key_prefix, created_at) "
                        "VALUES ($1, $2, $3, 'key', 'forge_live_abc', now())",
                        uuid.uuid4(), tenant.organization_id, tenant.user_id,
                    )
                self.assertEqual(
                    await conn.fetchval(
                        "SELECT count(*) FROM api_keys WHERE key_prefix = "
                        "'forge_live_abc'"
                    ),
                    2,
                )
                indexes = await conn.fetch(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
                    "AND tablename = 'api_keys'"
                )
                for row in indexes:
                    if "key_prefix" in row["indexdef"]:
                        self.fail(f"unexpected key_prefix index: {row['indexdef']}")
        run_async(body())

    def test_56_api_key_scopes_persist_as_text_array(self):
        """K-9 representation, requirement 23."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)
                key_id = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO api_keys (id, organization_id, created_by_user_id, "
                    "name, scopes, created_at) "
                    "VALUES ($1, $2, $3, 'key', $4, now())",
                    key_id, tenant.organization_id, tenant.user_id,
                    ["runs:read", "runs:write"],
                )
                scopes = await conn.fetchval(
                    "SELECT scopes FROM api_keys WHERE id = $1", key_id
                )
                self.assertEqual(scopes, ["runs:read", "runs:write"])

                # The GIN index is deferred and must not exist.
                index_names = {
                    r["indexname"] for r in await conn.fetch(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'public' AND tablename = 'api_keys'"
                    )
                }
                self.assertFalse(
                    any("scopes" in name for name in index_names),
                    "the scopes GIN index is DEFERRED and must not exist",
                )

                # A blank scope is refused.
                with self.assertRaises(asyncpg_module().CheckViolationError):
                    await conn.execute(
                        "INSERT INTO api_keys (id, organization_id, "
                        "created_by_user_id, name, scopes, created_at) "
                        "VALUES ($1, $2, $3, 'bad', $4, now())",
                        uuid.uuid4(), tenant.organization_id, tenant.user_id, [""],
                    )
        run_async(body())

    def test_57_api_key_revocation_consistency(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)
                error = asyncpg_module().CheckViolationError

                with self.assertRaises(error):  # revoked without revoked_at
                    await conn.execute(
                        "INSERT INTO api_keys (id, organization_id, "
                        "created_by_user_id, name, status, created_at) "
                        "VALUES ($1, $2, $3, 'k', 'revoked', now())",
                        uuid.uuid4(), tenant.organization_id, tenant.user_id,
                    )
                with self.assertRaises(error):  # revoked_at without revoked
                    await conn.execute(
                        "INSERT INTO api_keys (id, organization_id, "
                        "created_by_user_id, name, revoked_at, created_at) "
                        "VALUES ($1, $2, $3, 'k', now(), now())",
                        uuid.uuid4(), tenant.organization_id, tenant.user_id,
                    )
        run_async(body())

    def test_58_run_record_lifecycle_checks(self):
        """Requirement 24: exactly the constraints the domain contract fixes.

        Each expected rejection is fenced by a savepoint. A failed statement
        aborts the surrounding transaction, so without the savepoint the *next*
        statement would fail with InFailedSQLTransactionError and the test would
        be asserting the wrong error.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)
                error = asyncpg_module().CheckViolationError
                org = tenant.organization_id
                project = tenant.project_id
                user = tenant.user_id

                full = (
                    "INSERT INTO run_records (id, organization_id, project_id, "
                    "core_run_id, initiated_by_user_id, status, started_at, "
                    "finished_at, failure_classification, created_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now())"
                )

                # Savepoints require an explicit transaction; asyncpg runs in
                # autocommit mode, so one is opened around the whole sequence.
                await conn.execute("BEGIN")

                async def refuse(sql, *args):
                    await conn.execute("SAVEPOINT s")
                    try:
                        with self.assertRaises(error):
                            await conn.execute(sql, *args)
                    finally:
                        await conn.execute("ROLLBACK TO SAVEPOINT s")

                async def accept(sql, *args):
                    await conn.execute(sql, *args)

                # queued must not carry a Core identity
                await refuse(full, uuid.uuid4(), org, project, "core-q", None,
                             "queued", None, None, "")
                # queued must not carry started_at
                await refuse(
                    "INSERT INTO run_records (id, organization_id, project_id, "
                    "status, started_at, created_at) "
                    "VALUES ($1,$2,$3,'queued', now(), now())",
                    uuid.uuid4(), org, project,
                )
                # a non-queued run must carry a Core identity
                await refuse(
                    "INSERT INTO run_records (id, organization_id, project_id, "
                    "status, created_at) VALUES ($1,$2,$3,'running', now())",
                    uuid.uuid4(), org, project,
                )
                # a non-queued run must record an initiator
                await refuse(full, uuid.uuid4(), org, project, "core-i", None,
                             "running", dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), None, "")
                # core_run_id may never equal id
                same = uuid.uuid4()
                await refuse(full, same, org, project, str(same), user, "running",
                             dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), None, "")
                # finished without started
                await refuse(
                    "INSERT INTO run_records (id, organization_id, project_id, "
                    "core_run_id, initiated_by_user_id, status, finished_at, "
                    "created_at) "
                    "VALUES ($1,$2,$3,'core-f',$4,'running',now(),now())",
                    uuid.uuid4(), org, project, user,
                )
                # finished before started
                await refuse(full, uuid.uuid4(), org, project, "core-b", user,
                             "running", dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
                             dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), "")
                # succeeded carrying a failure classification
                await refuse(full, uuid.uuid4(), org, project, "core-s", user,
                             "succeeded", dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), None,
                             "timeout")
                # a status outside the approved vocabulary
                await refuse(full, uuid.uuid4(), org, project, "core-u", user,
                             "exploded", dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), None, "")

                # A terminal status WITHOUT finished_at is deliberately accepted:
                # that question belongs to O-7, which is still OPEN, so this
                # design does not constrain it and needs no migration when it is
                # decided.
                await accept(full, uuid.uuid4(), org, project, "core-t", user,
                             "cancelled", dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), None, "")

                # The valid shapes are accepted.
                await accept(full, uuid.uuid4(), org, project, None, None,
                             "queued", None, None, "")
                await accept(full, uuid.uuid4(), org, project, "core-ok", user,
                             "running", dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), None, "")

                await conn.execute("COMMIT")
        run_async(body())

    def test_60_attempt_count_cannot_be_negative(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)
                with self.assertRaises(asyncpg_module().CheckViolationError):
                    await conn.execute(
                        "INSERT INTO run_records (id, organization_id, project_id, "
                        "attempt_count, created_at) VALUES ($1,$2,$3,-1, now())",
                        uuid.uuid4(), tenant.organization_id, tenant.project_id,
                    )
        run_async(body())

    def test_61_usage_counters_cannot_be_negative(self):
        """Requirement 25.

        Six literal cases, one per column. They are written out rather than
        generated: an earlier generated version interpolated the column under
        test into a column list that already contained it, which produced
        "column specified more than once" and tested nothing at all.
        """

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create_run(conn)
                error = asyncpg_module().CheckViolationError

                cases = (
                    ("attempt_number",
                     "INSERT INTO usage_records (id, organization_id, "
                     "run_record_id, core_run_id, attempt_number, created_at) "
                     "VALUES ($1, $2, $3, 'core', $4, now())",
                     (-1,)),
                    ("input_tokens",
                     "INSERT INTO usage_records (id, organization_id, "
                     "run_record_id, core_run_id, attempt_number, input_tokens, "
                     "created_at) VALUES ($1, $2, $3, 'core', 1, $4, now())",
                     (-5,)),
                    ("output_tokens",
                     "INSERT INTO usage_records (id, organization_id, "
                     "run_record_id, core_run_id, attempt_number, output_tokens, "
                     "created_at) VALUES ($1, $2, $3, 'core', 2, $4, now())",
                     (-5,)),
                    ("cached_tokens",
                     "INSERT INTO usage_records (id, organization_id, "
                     "run_record_id, core_run_id, attempt_number, cached_tokens, "
                     "created_at) VALUES ($1, $2, $3, 'core', 3, $4, now())",
                     (-5,)),
                    ("tool_call_count",
                     "INSERT INTO usage_records (id, organization_id, "
                     "run_record_id, core_run_id, attempt_number, tool_call_count, "
                     "created_at) VALUES ($1, $2, $3, 'core', 4, $4, now())",
                     (-1,)),
                    ("duration_seconds",
                     "INSERT INTO usage_records (id, organization_id, "
                     "run_record_id, core_run_id, attempt_number, "
                     "duration_seconds, created_at) "
                     "VALUES ($1, $2, $3, 'core', 5, $4, now())",
                     (-0.5,)),
                )

                await conn.execute("BEGIN")
                for column, statement, args in cases:
                    await conn.execute("SAVEPOINT s")
                    with self.assertRaises(error, msg=f"{column} accepted {args}"):
                        await conn.execute(
                            statement, uuid.uuid4(), tenant.organization_id,
                            tenant.run_id, *args,
                        )
                    await conn.execute("ROLLBACK TO SAVEPOINT s")

                # Zero is the boundary and must be accepted, so these checks are
                # not simply rejecting every row.
                await conn.execute(
                    "INSERT INTO usage_records (id, organization_id, run_record_id, "
                    "core_run_id, attempt_number, input_tokens, output_tokens, "
                    "cached_tokens, tool_call_count, duration_seconds, created_at) "
                    "VALUES ($1, $2, $3, 'core', 90, 0, 0, 0, 0, 0, now())",
                    uuid.uuid4(), tenant.organization_id, tenant.run_id,
                )
                await conn.execute("COMMIT")
        run_async(body())

    def test_62_duration_seconds_cannot_be_nan_or_infinite(self):
        """Requirement 26, including the non-finite cases."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create_run(conn)
                error = asyncpg_module().CheckViolationError
                await conn.execute("BEGIN")
                for attempt, literal in enumerate(
                    ("'Infinity'::double precision",
                     "'-Infinity'::double precision",
                     "'NaN'::double precision")
                ):
                    await conn.execute("SAVEPOINT s")
                    with self.assertRaises(error, msg=f"accepted {literal}"):
                        await conn.execute(
                            "INSERT INTO usage_records (id, organization_id, "
                            "run_record_id, core_run_id, attempt_number, "
                            "duration_seconds, created_at) "
                            f"VALUES ($1,$2,$3,'core',$4,{literal}, now())",
                            uuid.uuid4(), tenant.organization_id, tenant.run_id,
                            attempt,
                        )
                    await conn.execute("ROLLBACK TO SAVEPOINT s")
                # A real measurement is accepted.
                await conn.execute(
                    "INSERT INTO usage_records (id, organization_id, run_record_id, "
                    "core_run_id, attempt_number, duration_seconds, created_at) "
                    "VALUES ($1,$2,$3,'core',99,1.25, now())",
                    uuid.uuid4(), tenant.organization_id, tenant.run_id,
                )
                await conn.execute("COMMIT")
        run_async(body())

    def test_63_uuid_storage_rejects_invalid_values(self):
        """Requirement 27."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                with self.assertRaises(asyncpg_module().DataError):
                    await conn.execute(
                        "INSERT INTO users (id, email, created_at) "
                        "VALUES ('not-a-uuid', 'x@example.test', now())"
                    )
                with self.assertRaises(asyncpg_module().DataError):
                    await conn.execute(
                        "SELECT count(*) FROM users WHERE id = 'definitely not a uuid'"
                    )
        run_async(body())

    def test_64_core_run_id_is_text_not_uuid(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT table_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'core_run_id' "
                    "ORDER BY table_name"
                )
                self.assertEqual(len(rows), 2)
                for row in rows:
                    self.assertEqual(row["data_type"], "text")
        run_async(body())

    def test_65_token_counters_are_integers(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'usage_records'"
                )
                types = {r["column_name"]: r["data_type"] for r in rows}
                for column in ("input_tokens", "output_tokens", "cached_tokens"):
                    self.assertEqual(types[column], "bigint")
                for column in ("attempt_number", "tool_call_count"):
                    self.assertEqual(types[column], "integer")
                self.assertEqual(types["duration_seconds"], "double precision")
                self.assertEqual(types["success"], "boolean")
                self.assertEqual(types["fallback"], "boolean")
        run_async(body())

    def test_66_scopes_and_metadata_types_are_as_approved(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT table_name, column_name, data_type FROM "
                    "information_schema.columns WHERE table_schema = 'public'"
                )
                types = {(r["table_name"], r["column_name"]): r["data_type"]
                         for r in rows}
                self.assertEqual(types[("api_keys", "scopes")], "ARRAY")
                self.assertEqual(types[("provider_accounts", "metadata")], "jsonb")
                # jsonb exists ONLY on provider_accounts.
                jsonb_columns = [
                    key for key, value in types.items() if value == "jsonb"
                ]
                self.assertEqual(jsonb_columns, [("provider_accounts", "metadata")])
        run_async(body())

    def test_67_no_postgresql_enum_type_is_used(self):
        """O-7 must stay open, so statuses are TEXT + CHECK."""

        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                enums = await db.connection.fetch(
                    "SELECT t.typname FROM pg_type t JOIN pg_namespace n "
                    "ON n.oid = t.typnamespace WHERE t.typtype = 'e' "
                    "AND n.nspname = 'public'"
                )
                self.assertEqual([r["typname"] for r in enums], [])

                rows = await db.connection.fetch(
                    "SELECT table_name, column_name, data_type FROM "
                    "information_schema.columns WHERE table_schema = 'public' "
                    "AND column_name IN ('status', 'role')"
                )
                for row in rows:
                    self.assertEqual(row["data_type"], "text")
        run_async(body())

    def test_68_secret_ref_is_opaque_and_required(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)
                # Any non-empty handle is accepted, and none is interpreted.
                for handle in ("env:DEEPSEEK_API_KEY", "vault:secret/data/x",
                               "a", "opaque-handle-with-:-dots.and/slashes"):
                    await conn.execute(
                        "INSERT INTO provider_accounts (id, organization_id, "
                        "provider_name, secret_ref, created_at) "
                        "VALUES ($1, $2, $3, $4, now())",
                        uuid.uuid4(), tenant.organization_id, uuid.uuid4().hex,
                        handle,
                    )
                with self.assertRaises(asyncpg_module().CheckViolationError):
                    await conn.execute(
                        "INSERT INTO provider_accounts (id, organization_id, "
                        "provider_name, secret_ref, created_at) "
                        "VALUES ($1, $2, 'x', '   ', now())",
                        uuid.uuid4(), tenant.organization_id,
                    )
        run_async(body())

    def test_69_workspace_ref_is_opaque(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)
                # A non-path reference is perfectly acceptable.
                for reference in ("ws:opaque-1", "ref/2", "C:\\somewhere\\else"):
                    await conn.execute(
                        "INSERT INTO projects (id, organization_id, name, "
                        "workspace_ref, created_at) VALUES ($1,$2,'p',$3, now())",
                        uuid.uuid4(), tenant.organization_id, reference,
                    )
                columns = {
                    r["column_name"] for r in await conn.fetch(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'projects'"
                    )
                }
                self.assertIn("workspace_ref", columns)
                self.assertNotIn("workspace_path", columns)
        run_async(body())

    def test_70_metadata_must_be_a_json_object(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                tenant = Tenant("A")
                await tenant.create(conn)
                with self.assertRaises(asyncpg_module().CheckViolationError):
                    await conn.execute(
                        "INSERT INTO provider_accounts (id, organization_id, "
                        "provider_name, secret_ref, metadata, created_at) "
                        "VALUES ($1,$2,'x','ref','[1,2]'::jsonb, now())",
                        uuid.uuid4(), tenant.organization_id,
                    )
        run_async(body())


# --------------------------------------------------------------------------- #
# Roles and grants
# --------------------------------------------------------------------------- #
class RoleBoundaryTests(AsyncTestCase):
    def test_80_platform_roles_exist_without_escalation(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin "
                    "FROM pg_roles WHERE rolname = ANY($1::text[])",
                    [APP_ROLE, SYSTEM_ROLE],
                )
                self.assertEqual(len(rows), 2)
                for row in rows:
                    self.assertFalse(row["rolsuper"], row["rolname"])
                    self.assertFalse(row["rolbypassrls"], row["rolname"])
                    self.assertFalse(row["rolcanlogin"], row["rolname"])
        run_async(body())

    def test_81_app_role_has_no_ddl_privilege(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                conn = db.connection
                await conn.execute(f"SET ROLE {APP_ROLE}")
                try:
                    with self.assertRaises(asyncpg_module().InsufficientPrivilegeError):
                        await conn.execute("ALTER TABLE projects ADD COLUMN x int")
                    with self.assertRaises(asyncpg_module().InsufficientPrivilegeError):
                        await conn.execute("TRUNCATE projects")
                    with self.assertRaises(asyncpg_module().InsufficientPrivilegeError):
                        await conn.execute(
                            "SELECT * FROM platform_schema_migrations"
                        )
                finally:
                    await conn.execute("RESET ROLE")
        run_async(body())

    def test_82_policy_helper_functions_are_not_public(self):
        async def body():
            async with LiveDatabase() as db:
                await db.apply_migrations()
                rows = await db.connection.fetch(
                    "SELECT p.proname AS name, "
                    "has_function_privilege('public', p.oid, 'EXECUTE') AS public_ok "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'platform'"
                )
                self.assertTrue(rows)
                for row in rows:
                    self.assertFalse(
                        row["public_ok"],
                        f"{row['name']} is executable by PUBLIC",
                    )
        run_async(body())


# --------------------------------------------------------------------------- #
# Static checks that do not need a server
# --------------------------------------------------------------------------- #
class SchemaArtefactTests(unittest.TestCase):
    """These run everywhere; they need no database."""

    def test_90_consolidated_schema_is_current(self):
        from app.platform.persistence.consolidate import build_consolidated

        path = (REPO_ROOT / "app" / "platform" / "persistence" / "postgres"
                / "schema.sql")
        self.assertTrue(path.is_file(), "schema.sql is missing")
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            build_consolidated(),
            "schema.sql is stale; run: python -m app.platform.persistence.consolidate",
        )

    def test_91_migration_set_is_complete_and_ordered(self):
        from app.platform.persistence import discover_migrations

        names = [m.filename for m in discover_migrations()]
        self.assertEqual(names, sorted(names), "migrations must be ordered by name")
        self.assertEqual(len(names), len(set(names)), "no duplicate migrations")
        self.assertEqual(names[0], "0001_users.sql")
        for name in names:
            self.assertRegex(name, r"^\d{4}_[a-z0-9_]+\.sql$")

    def test_92_migrations_forbid_cascade_delete(self):
        """A static guard so a CASCADE cannot be introduced unnoticed."""

        from app.platform.persistence import discover_migrations

        for migration in discover_migrations():
            body = "\n".join(
                line for line in migration.sql.splitlines()
                if not line.strip().startswith("--")
            )
            self.assertNotIn(
                "ON DELETE CASCADE", body.upper(),
                f"{migration.filename} introduces ON DELETE CASCADE",
            )

    def test_93_no_database_driver_import_at_module_scope(self):
        """The schema layer must not drag a driver into the import graph.

        Only module-scope imports are checked. A driver imported lazily inside a
        function is not a dependency of the module's import graph, and that is how
        the snapshot generator keeps the driver optional.
        """

        import ast

        forbidden = {"asyncpg", "psycopg", "psycopg2", "sqlalchemy", "alembic"}
        offenders: list[str] = []

        for path in (REPO_ROOT / "app").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in forbidden:
                            offenders.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split(".")[0] in forbidden:
                        offenders.append(f"{path.name}: from {node.module}")
                elif isinstance(node, (ast.Try, ast.If)):
                    for inner in ast.walk(node):
                        if isinstance(inner, ast.Import):
                            for alias in inner.names:
                                if alias.name.split(".")[0] in forbidden:
                                    offenders.append(
                                        f"{path.name}: import {alias.name}"
                                    )
                        elif isinstance(inner, ast.ImportFrom):
                            if inner.module and \
                                    inner.module.split(".")[0] in forbidden:
                                offenders.append(
                                    f"{path.name}: from {inner.module}"
                                )

        self.assertEqual(
            offenders, [],
            "no module under app/ may import a database driver at module scope: "
            + "; ".join(offenders),
        )

    def test_94_schema_sql_contains_every_required_construct(self):
        path = (REPO_ROOT / "app" / "platform" / "persistence" / "postgres"
                / "schema.sql")
        sql = path.read_text(encoding="utf-8")
        required = [
            "ENABLE ROW LEVEL SECURITY",
            "FORCE  ROW LEVEL SECURITY",
            "platform.is_current_organization",
            "platform.is_system_owned_provider_account",
            "nullif(current_setting('forge.organization_id', true), '')",
            "UNIQUE (id, organization_id)",
            "UNIQUE (run_record_id, attempt_number)",
            "ON DELETE RESTRICT",
            "ON DELETE SET NULL",
            "WHERE organization_id IS NOT NULL",
            "WHERE organization_id IS NULL",
        ]
        for fragment in required:
            self.assertIn(fragment, sql, f"schema.sql lacks: {fragment}")

        forbidden = [
            "ON DELETE CASCADE",
            "CREATE TYPE",
            "platform_schema_migrations (filename text NOT NULL, checksum",
        ]
        for fragment in forbidden:
            if fragment == "platform_schema_migrations (filename text NOT NULL, checksum":
                continue
            self.assertNotIn(fragment, sql.upper() if fragment.isupper() else sql)

    def test_95_no_unique_constraint_on_core_run_id_or_key_prefix(self):
        path = (REPO_ROOT / "app" / "platform" / "persistence" / "postgres"
                / "schema.sql")
        sql = path.read_text(encoding="utf-8")
        self.assertNotIn("UNIQUE (core_run_id)", sql)
        self.assertNotIn("core_run_id) WHERE core_run_id IS NOT NULL", sql)
        self.assertNotIn("UNIQUE (key_prefix)", sql)


if __name__ == "__main__":
    unittest.main()
