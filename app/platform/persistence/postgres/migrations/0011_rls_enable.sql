-- 0011_rls_enable.sql
--
-- Row-level security enablement and table access grants.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section I, section O,
-- D-PLATFORM-18, D-PLATFORM-19.
--
-- THE CENTRAL POINT: row-level security does NOT apply to a table's owner,
-- because the owner bypasses RLS by default. A test or deployment that connects
-- AS THE OWNER and expects policies to filter rows will silently observe every
-- row. FORCE ROW LEVEL SECURITY removes that exemption.
--
-- ENABLE + FORCE are both written on every table. FORCE alone is not enough:
-- it modifies the behaviour of an already-enabled table.
--
-- GRANTS ARE THE OUTER OF TWO LAYERS. A table privilege says an operation is
-- possible at all; a policy says which rows it may touch. Both must permit an
-- operation for it to succeed, so a revoked privilege is a second, independent
-- way to forbid something. That is why the append-only rule for `usage_records`
-- and the no-delete rule for `run_records` are enforced in BOTH places rather
-- than in the policy alone: a future policy edit cannot re-open a privilege that
-- was never granted.
--
-- GRANTS ARE DELIBERATELY NARROW. A capability that no accepted contract needs is
-- not granted, so it cannot be reached by accident.

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

---------------------------------------------------------------------
-- forge_platform_app: the ordinary tenant request path
---------------------------------------------------------------------
-- One operation at a time on purpose: no ALL, and therefore no TRUNCATE, which
-- row-level security does not filter.
GRANT SELECT, INSERT, UPDATE, DELETE ON users             TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON organizations     TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON memberships       TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON projects          TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON provider_accounts TO forge_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys          TO forge_platform_app;
-- run_records: lifecycle transitions are needed, deletion is not. The DELETE
-- privilege is withheld because no accepted contract requires deleting execution
-- history; without the privilege, no policy can make it possible.
GRANT SELECT, INSERT, UPDATE ON run_records TO forge_platform_app;
-- usage_records is APPEND-ONLY: a measurement is inserted once and never
-- rewritten or removed. Corrections are new records, and O-8's
-- UNIQUE (run_record_id, attempt_number) already prevents a silent overwrite of
-- an attempt's measurement. UPDATE and DELETE are not granted, so the
-- append-only rule survives any future policy change.
GRANT SELECT, INSERT ON usage_records TO forge_platform_app;

---------------------------------------------------------------------
-- forge_platform_system: the server-only scope
---------------------------------------------------------------------
-- This scope exists for two things and nothing else:
--
--   1. reading the identity and tenancy layer to resolve a subject BEFORE a
--      tenant context exists (find the user, their memberships, their
--      organizations);
--   2. the bootstrap that cannot itself be tenant-scoped -- creating an
--      organization and its first OWNER membership -- plus the system-owned
--      provider credentials, which belong to no tenant by definition.
--
-- It is NOT a general cross-tenant reader or writer. The operational tables that
-- carry tenant work -- projects, api_keys, run_records, usage_records -- are
-- deliberately absent from the list below. There is no grant to revoke because
-- there is no grant: this scope cannot read or write another tenant's runs or
-- measurements at all.
--
-- The grants are held to the minimum each numbered capability needs, and an
-- undesired operation is removed rather than merely left unpoliced:
--
--   users:         SELECT (resolve a subject) + INSERT (bootstrap the account).
--                  NO UPDATE -- changing an account, including its email, is not
--                  part of this scope, and an UPDATE privilege that no capability
--                  requires is a capability waiting to be used.
--   organizations: SELECT (resolve a tenant) + INSERT (bootstrap the tenant).
--                  NO UPDATE -- suspending a tenant is not required by any
--                  accepted contract, so it is not granted; adding it later is a
--                  deliberate expansion with a policy to match.
--   memberships:   SELECT (discovery) + INSERT (the first OWNER) + UPDATE
--                  (reactivating a returning member, which K-2 forces to be a
--                  status change rather than a second row) + DELETE (the
--                  administrative counterpart of the RESTRICT keys, so that
--                  provenance rows do not block user removal forever).
GRANT SELECT, INSERT ON users             TO forge_platform_system;
GRANT SELECT, INSERT ON organizations     TO forge_platform_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON memberships TO forge_platform_system;
-- System-owned provider credentials are the one operational exception: they
-- belong to no tenant, so no tenant context can ever reach them. The policy
-- restricts this scope to rows with a NULL organization_id and requires the
-- explicit system scope flag.
GRANT SELECT, INSERT, UPDATE, DELETE ON provider_accounts TO forge_platform_system;

-- The migration ledger is written by the migration role, never by the platform
-- roles, so it is deliberately not granted to either.
REVOKE ALL ON SCHEMA platform FROM PUBLIC;
GRANT USAGE ON SCHEMA platform TO forge_platform_app, forge_platform_system;
