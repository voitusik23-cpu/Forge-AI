-- 0002_roles.sql
--
-- Platform roles.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section I, D-PLATFORM-18.
--
-- Two roles separate the ordinary tenant request path from the server-only
-- path that may reach system-owned rows:
--
--   forge_platform_app     the ordinary tenant request path
--   forge_platform_system  the server-only path for system-owned rows
--
-- Both roles are NOLOGIN. They are authorization scopes reached with SET ROLE
-- inside the server's own session, not login accounts, so there is no password
-- and no secret anywhere in this migration.
--
-- THE APPLICATION ROLE IS NOT THE TABLE OWNER. Row-level security does not
-- apply to a table's owner, because the owner bypasses it by default. Keeping the
-- roles separate from the migration role is what makes the policies observable --
-- see 0011_rls_enable.sql.
--
-- `platform` schema USAGE and function EXECUTE grants are issued in
-- 0003_rls_helpers.sql, immediately after those functions exist.

-- The app role. It must not be a superuser and must not have BYPASSRLS.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'forge_platform_app') THEN
        CREATE ROLE forge_platform_app
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOBYPASSRLS;
    END IF;
END
$$;

-- The server-only system role. Same constraints: it is a distinct principal,
-- not a privilege escalation.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'forge_platform_system') THEN
        CREATE ROLE forge_platform_system
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOBYPASSRLS;
    END IF;
END
$$;

-- The two roles must never be superusers or bypass RLS, even if they already
-- existed with different attributes. Re-asserted so the migration is the source
-- of truth for this invariant.
ALTER ROLE forge_platform_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE forge_platform_system NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
