"""Report the PostgreSQL server the Platform tests will run against.

A one-line version of this check lived inside the workflow, where it could not be
exercised locally. As a script it is runnable on any host, and it exits non-zero
when there is no server to inspect -- never a silent success.

Exit codes: 0 = reachable and reported, 2 = nothing to check (no DSN or no driver),
1 = the DSN is set but the server could not be reached.
"""

from __future__ import annotations

import asyncio
import os
import sys

DSN_ENV = "FORGE_PLATFORM_TEST_DSN"


async def _report(dsn: str) -> int:
    import asyncpg

    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        version = await connection.fetchval("SHOW server_version")
        rolname = await connection.fetchval("SELECT current_user")
        superuser = await connection.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        )
        print(f"server_version={version} admin_role={rolname} superuser={superuser}")
        return 0
    finally:
        await connection.close()


def main() -> int:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        print(f"{DSN_ENV} is not set; there is no server to check", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_report(dsn))
    except ImportError:
        print("asyncpg is not installed; nothing was checked", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - report, never pretend success
        print(f"could not reach the server: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
