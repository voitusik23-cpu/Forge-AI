"""Regenerate the consolidated ``schema.sql`` from the migration files.

The migrations are the source of truth; ``schema.sql`` is a derived artefact. This
module concatenates the migration bodies under a single ``BEGIN``/``COMMIT`` with
a reset preamble, so the consolidated file and the migration set cannot drift.

Usage::

    python -m app.platform.persistence.consolidate
    python -m app.platform.persistence.consolidate --check   # fail if stale

``--check`` is what the database contract tests use: it asserts that the committed
``schema.sql`` still matches the migrations, so editing a migration without
regenerating is caught rather than silently tolerated.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

POSTGRES_DIR = pathlib.Path(__file__).resolve().parent / "postgres"
MIGRATIONS_DIR = POSTGRES_DIR / "migrations"
SCHEMA_PATH = POSTGRES_DIR / "schema.sql"

HEADER = """-- schema.sql
--
-- Consolidated, self-contained Platform schema: every table, constraint, index,
-- row-level security policy, and helper function created by the migrations in
-- `migrations/`, in one file and one transaction.
--
-- WHY THIS FILE EXISTS, given that migrations already do this:
--
--   1. A single reviewable artefact. A reviewer can read the whole schema in one
--      pass instead of assembling it from fourteen files.
--   2. A single-transaction path. Applying it either fully succeeds or leaves the
--      database untouched, which the migration ledger deliberately does not
--      guarantee: each migration commits separately, on purpose, so one failure
--      does not roll back earlier work.
--   3. A benchmark for the migrations. The database contract tests assert that
--      this file and the migration set produce the same tables, the same
--      policies, and the same row-level security state.
--
-- GENERATED FILE -- do not edit by hand. Edit the migration, then run:
--     python -m app.platform.persistence.consolidate
--
-- DESTRUCTIVE. It drops the Platform objects and recreates them. Never run it
-- against a database holding data you want to keep; it exists for a fresh schema
-- and for tests.

BEGIN;

-- The Platform reset. Ordered so that dependants go before their targets; the
-- schema and the migration ledger are dropped with them.
DROP TABLE IF EXISTS usage_records CASCADE;
DROP TABLE IF EXISTS run_records CASCADE;
DROP TABLE IF EXISTS api_keys CASCADE;
DROP TABLE IF EXISTS provider_accounts CASCADE;
DROP TABLE IF EXISTS projects CASCADE;
DROP TABLE IF EXISTS memberships CASCADE;
DROP TABLE IF EXISTS organizations CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS platform_schema_migrations CASCADE;
DROP SCHEMA IF EXISTS platform CASCADE;
"""

FOOTER = "\nCOMMIT;\n"


def _body(path: pathlib.Path) -> str:
    """Return a migration's body with its leading filename banner adjusted."""

    text = path.read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    # The migration's own first line is a comment; keep it, it names the source.
    return "\n".join(lines)


def build_consolidated() -> str:
    parts = [HEADER]
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        parts.append(
            "\n-- ==================================================================\n"
            f"-- {path.name}\n"
            "-- ==================================================================\n"
        )
        parts.append(_body(path) + "\n")
    parts.append(FOOTER)
    return "".join(parts)


def write_consolidated() -> pathlib.Path:
    SCHEMA_PATH.write_text(build_consolidated(), encoding="utf-8", newline="\n")
    return SCHEMA_PATH


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the committed schema.sql is out of date",
    )
    args = parser.parse_args(argv)

    expected = build_consolidated()
    if args.check:
        actual = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.is_file() else ""
        if actual != expected:
            print(
                "schema.sql is out of date with the migrations; "
                "run: python -m app.platform.persistence.consolidate",
                file=sys.stderr,
            )
            return 1
        print("schema.sql is up to date")
        return 0

    path = write_consolidated()
    print(f"wrote {path} ({len(expected.splitlines())} lines)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
