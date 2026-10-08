-- 0002_roles.sql
--
-- Platform roles, and the checks that keep the two scopes from being merged.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section I and section O,
-- D-PLATFORM-18, D-PLATFORM-19.
--
-- TWO ROLES, AND THEY MUST STAY TWO ROLES:
--
--   forge_platform_app     the ordinary tenant request path
--   forge_platform_system  the server-only path for system-owned rows and for
--                          pre-tenant discovery
--
-- Both are NOLOGIN. They are authorization scopes reached with SET ROLE inside a
-- login session, not login accounts, so no password exists anywhere in this
-- schema. A NOLOGIN role cannot be connected to directly, which is what makes the
-- SET ROLE step an unavoidable, explicit act rather than an accident of who
-- happened to connect.
--
-- WHY `forge.system_scope = 'on'` IS NOT AN AUTHORIZATION MECHANISM. Any session
-- can set a custom GUC, so a GUC can never be the thing that grants access. The
-- system policies are written `TO forge_platform_system`, which is what actually
-- gates them; the GUC only narrows what the system scope may touch and keeps the
-- system rows invisible by default. Setting the GUC on the tenant connection
-- grants nothing, and the tests assert that.
--
-- WHAT THIS MIGRATION CANNOT DECIDE. Which login role a deployment uses, and which
-- of the two scopes that login role holds. That is a deployment decision, and
-- section O of the schema document states the requirement: tenant traffic and
-- system traffic must be served by different login roles, so that a connection
-- serving a tenant request is not also able to SET ROLE into the system scope.
-- What this migration does instead is make the dangerous configuration detectable
-- and the safe one self-asserting, from inside the database.
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
--
-- NOINHERIT is load-bearing, not tidiness. Because both roles are NOINHERIT, a
-- login role that somehow held both memberships would still not pick up the
-- system policies merely by connecting; it would have to issue SET ROLE
-- explicitly, which is the auditable act the boundary depends on. Inheritance
-- would silently hand the system scope to every query on the tenant connection.
ALTER ROLE forge_platform_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
ALTER ROLE forge_platform_system NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;

-- Neither scope is reachable from the other. Written explicitly, and re-run on
-- every migration, so a stray grant is undone rather than silently kept.
REVOKE forge_platform_system FROM forge_platform_app;
REVOKE forge_platform_app FROM forge_platform_system;

-- The tenant scope must never be a member of the system scope. This is checked
-- from a helper so the same check can be asserted by a test, and so a deployment
-- can run it as a health check against its own database.
CREATE SCHEMA IF NOT EXISTS platform;

CREATE OR REPLACE FUNCTION platform.platform_scopes_are_separate()
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT NOT pg_has_role('forge_platform_app', 'forge_platform_system', 'MEMBER')
       AND NOT pg_has_role('forge_platform_system', 'forge_platform_app', 'MEMBER')
$$;

COMMENT ON FUNCTION platform.platform_scopes_are_separate() IS
    'True when neither platform scope is a member of the other. A deployment must '
    'be able to assert this; if it is false, the two scopes have been merged and '
    'forge.system_scope stops being a boundary.';

-- Fail the migration loudly rather than leaving a merged pair behind. The
-- REVOKE statements above make this unreachable in a fresh database; this check
-- is what catches a database where somebody re-granted the membership by hand
-- before re-running the migrations.
DO $$
BEGIN
    IF NOT platform.platform_scopes_are_separate() THEN
        RAISE EXCEPTION
            'platform scopes are merged: forge_platform_app and '
            'forge_platform_system must not be members of each other';
    END IF;
END
$$;
