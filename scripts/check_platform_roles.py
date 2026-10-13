"""Assert the Platform test logins cannot bypass row-level security.

The Platform persistence tests are only meaningful if the connections they use are
subject to the policies: a superuser, or a role with ``BYPASSRLS``, or (for a table)
its owner, gets a result that says nothing about the policy. ``tests/persistence_fixture.py``
provisions its two logins with ``NOSUPERUSER NOBYPASSRLS`` precisely for that reason;
this script re-checks the attributes in the live cluster after a run, so a fixture that
ever regressed would fail the job instead of quietly weakening the evidence.

Usage::

    FORGE_PLATFORM_TEST_DSN=postgresql://postgres:...@127.0.0.1:5432/postgres \\
        python -B -X utf8 scripts/check_platform_roles.py

Exit codes: 0 = the logins are constrained, 1 = a check failed, 2 = nothing to check
(no DSN configured, driver missing, or the logins do not exist yet) -- never a silent
success.
"""

from __future__ import annotations

import asyncio
import os
import sys

LOGINS = ("forge_persistence_login", "forge_persistence_system_login")
EXPECTED_MEMBERSHIP = {
    "forge_persistence_login": {"forge_platform_app"},
    "forge_persistence_system_login": {"forge_platform_system"},
}
DSN_ENV = "FORGE_PLATFORM_TEST_DSN"


async def _inspect(dsn: str) -> int:
    try:
        import asyncpg  # noqa: PLC0415 - optional, test-only dependency
    except ImportError:
        print("asyncpg is not installed; nothing was checked", file=sys.stderr)
        return 2

    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        failures: list[str] = []
        for login in LOGINS:
            row = await connection.fetchrow(
                "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                "FROM pg_roles WHERE rolname = $1",
                login,
            )
            if row is None:
                print(f"{login}: NOT PRESENT (the fixture creates it during a run)")
                failures.append(f"{login} is missing")
                continue
            print(
                f"{login}: superuser={row['rolsuper']} bypassrls={row['rolbypassrls']} "
                f"createdb={row['rolcreatedb']} createrole={row['rolcreaterole']}"
            )
            if row["rolsuper"]:
                failures.append(f"{login} is a superuser")
            if row["rolbypassrls"]:
                failures.append(f"{login} has BYPASSRLS")
            members = await connection.fetch(
                "SELECT r.rolname FROM pg_auth_members m "
                "JOIN pg_roles r ON r.oid = m.roleid "
                "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = $1",
                login,
            )
            memberships = {record["rolname"] for record in members}
            expected = EXPECTED_MEMBERSHIP[login]
            print(f"{login}: member of {sorted(memberships)}")
            if not expected.issubset(memberships):
                failures.append(f"{login} is not a member of {sorted(expected)}")
            # One login in both platform roles would let a tenant connection step
            # into the system scope; the boundary would then exist only in prose.
            extra = memberships - expected
            if extra:
                failures.append(f"{login} holds unexpected memberships {sorted(extra)}")

        if failures:
            for failure in failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            return 1
        print("OK: both logins are NOSUPERUSER, NOBYPASSRLS and scoped to one role each")
        return 0
    finally:
        await connection.close()


def main() -> int:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        print(
            f"{DSN_ENV} is not set, so there is no cluster to inspect; "
            "nothing was checked",
            file=sys.stderr,
        )
        return 2
    try:
        return asyncio.run(_inspect(dsn))
    except Exception as exc:  # noqa: BLE001 - report, never pretend success
        print(f"could not inspect the cluster: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
