"""Regenerate the committed Platform schema snapshot.

The snapshot is a verification artefact, not a migration: it records the fully
expanded schema and the row-level security policy text exactly as PostgreSQL
stores them, so a reviewer or a diff can see the real object definitions instead
of reconstructing them from migration files by hand.

Usage (against a database that already has the migrations applied)::

    python -m app.platform.persistence.snapshot --dsn postgresql://...

The module is import-safe and imports no database driver: ``asyncpg`` is imported
inside the function that needs it, so importing this module never makes a driver
a Platform dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

SNAPSHOT_PATH = (
    pathlib.Path(__file__).resolve().parent / "postgres" / "schema.snapshot.sql"
)

TABLES = (
    "users",
    "organizations",
    "memberships",
    "projects",
    "provider_accounts",
    "api_keys",
    "run_records",
    "usage_records",
)

COLUMNS_SQL = """
SELECT table_name, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = ANY($1::text[])
ORDER BY table_name, ordinal_position
"""

CONSTRAINTS_SQL = """
SELECT c.relname AS table_name,
       con.conname AS constraint_name,
       con.contype AS constraint_type,
       pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY($1::text[])
ORDER BY c.relname, con.conname
"""

INDEXES_SQL = """
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = ANY($1::text[])
ORDER BY tablename, indexname
"""

POLICIES_SQL = """
SELECT tablename, policyname, roles::text AS roles,
       cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public' AND tablename = ANY($1::text[])
ORDER BY tablename, policyname
"""

RLS_SQL = """
SELECT c.relname AS table_name, c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY($1::text[])
ORDER BY c.relname
"""

FUNCTIONS_SQL = """
SELECT p.proname, pg_get_functiondef(p.oid) AS definition
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'platform'
ORDER BY p.proname
"""


async def build_snapshot(dsn: str) -> str:
    """Return the snapshot text for the database at ``dsn``."""

    import asyncpg  # imported lazily: see the module docstring

    conn = await asyncpg.connect(dsn)
    try:
        tables = list(TABLES)
        lines: list[str] = []
        add = lines.append

        add("-- Generated file. Do not edit by hand.")
        add("-- Regenerate with: python -m app.platform.persistence.snapshot --dsn <dsn>")
        add("--")
        add("-- Committed snapshot of the Platform PostgreSQL schema and its")
        add("-- row-level security policies, as PostgreSQL itself stores them.")
        add("-- See 0015_schema_snapshot.sql for why this file exists.")
        add("")

        add("-- ============================================================")
        add("-- Row-level security state (E and F must both be true on every table)")
        add("-- ============================================================")
        for row in await conn.fetch(RLS_SQL, tables):
            add(
                f"-- {row['table_name']}: "
                f"ENABLE={row['relrowsecurity']} FORCE={row['relforcerowsecurity']}"
            )
        add("")

        add("-- ============================================================")
        add("-- Columns")
        add("-- ============================================================")
        current = None
        for row in await conn.fetch(COLUMNS_SQL, tables):
            if row["table_name"] != current:
                current = row["table_name"]
                add("")
                add(f"-- {current}")
            default = row["column_default"] or "-"
            add(
                f"--   {row['column_name']} {row['data_type']} "
                f"null={row['is_nullable']} default={default}"
            )
        add("")

        add("-- ============================================================")
        add("-- Constraints")
        add("-- ============================================================")
        for row in await conn.fetch(CONSTRAINTS_SQL, tables):
            add(
                f"-- {row['table_name']}.{row['constraint_name']} "
                f"[{row['constraint_type']}]: {row['definition']}"
            )
        add("")

        add("-- ============================================================")
        add("-- Indexes")
        add("-- ============================================================")
        for row in await conn.fetch(INDEXES_SQL, tables):
            add(f"-- {row['tablename']}: {row['indexdef']}")
        add("")

        add("-- ============================================================")
        add("-- Row-level security policies")
        add("-- ============================================================")
        for row in await conn.fetch(POLICIES_SQL, tables):
            add(f"-- policy {row['tablename']}.{row['policyname']} "
                f"cmd={row['cmd']} roles={row['roles']}")
            add(f"--   USING      {row['qual']}")
            add(f"--   WITH CHECK {row['with_check']}")
        add("")

        add("-- ============================================================")
        add("-- Helper functions in schema platform")
        add("-- ============================================================")
        for row in await conn.fetch(FUNCTIONS_SQL):
            add("")
            for line in str(row["definition"]).splitlines():
                add(f"-- {line}")

        return "\n".join(lines) + "\n"
    finally:
        await conn.close()


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="PostgreSQL DSN")
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the snapshot to %s" % SNAPSHOT_PATH,
    )
    args = parser.parse_args(argv)

    text = asyncio.run(build_snapshot(args.dsn))
    if args.write:
        SNAPSHOT_PATH.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {SNAPSHOT_PATH} ({len(text.splitlines())} lines)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
