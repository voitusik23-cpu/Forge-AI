-- 0011_rls_enable.sql
--
-- Row-level security enablement and table access grants.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section I, D-PLATFORM-18.
--
-- THE CENTRAL POINT: row-level security does NOT apply to a table's owner,
-- because the owner bypasses RLS by default. A test or deployment that connects
-- AS THE OWNER and expects policies to filter rows will silently observe every
-- row. FORCE ROW LEVEL SECURITY removes that exemption.
--
-- ENABLE + FORCE are both written on every table. FORCE alone is not enough:
-- it modifies the behaviour of an already-enabled table.
--
-- Tables are granted one operation at a time (SELECT, INSERT, UPDATE, DELETE)
-- rather than with ALL. An accidental future grant of TRUNCATE must not arrive
-- bundled with DML, because TRUNCATE is not filtered by row-level security.

-- Enable and force row-level security on every table. FORCE is what makes the
-- policies apply to the table owner as well.
ALTER TABLE users             ENABLE ROW LEVEL SECURITY;
ALTER TABLE users             FORCE  ROW LEVEL SECURITY;
ALTER TABLE organizations     ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations     FORCE  ROW LEVEL SECURITY;
ALTER TABLE memberships       ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships       FORCE  ROW LEVEL SECURITY;
ALTER TABLE projects          ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects          FORCE  ROW LEVEL SECURITY;
ALTER TABLE provider_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_accounts FORCE  ROW LEVEL SECURITY;
ALTER TABLE api_keys          ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys          FORCE  ROW LEVEL SECURITY;
ALTER TABLE run_records       ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_records       FORCE  ROW LEVEL SECURITY;
ALTER TABLE usage_records     ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_records     FORCE  ROW LEVEL SECURITY;

-- One operation at a time on purpose: no ALL, and therefore no TRUNCATE.
GRANT SELECT, INSERT, UPDATE, DELETE ON users             TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON organizations     TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON memberships       TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON projects          TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON provider_accounts TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys          TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON run_records       TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON usage_records     TO forge_platform_app;

-- The system role is granted access too; what separates it is not the table
-- privilege but which policies exist for it (see 0012_..., 0013_..., 0014_...).
GRANT SELECT, INSERT, UPDATE, DELETE ON users             TO forge_platform_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON organizations     TO forge_platform_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON memberships       TO forge_platform_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON projects          TO forge_platform_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON provider_accounts TO forge_platform_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys          TO forge_platform_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON run_records       TO forge_platform_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON usage_records     TO forge_platform_system;

-- The migration ledger is written by the migration role, never by the platform
-- roles, so it is deliberately not granted to either.
REVOKE ALL ON SCHEMA platform FROM PUBLIC;
GRANT USAGE ON SCHEMA platform TO forge_platform_app, forge_platform_system;
