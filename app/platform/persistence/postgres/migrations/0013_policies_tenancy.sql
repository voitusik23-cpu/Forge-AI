-- 0013_policies_tenancy.sql
--
-- RLS policies for the operational tenant tables:
-- `projects`, `api_keys`, `run_records`, `usage_records`.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md sections I-3, I-8 and O,
-- D-PLATFORM-19.
--
-- THE OPERATION SPLIT IS THE POINT:
--
--   SELECT / DELETE  ->  USING  (which rows are visible)
--   INSERT / UPDATE  ->  WITH CHECK  (which rows may be written)
--
-- A USING-only policy filters reads while still permitting a write into another
-- tenant. WITH CHECK is what actually prevents a cross-tenant INSERT and, because
-- UPDATE carries both clauses, what prevents a row being MOVED between tenants.
--
-- NO SYSTEM SCOPE REACHES THESE TABLES. The server-only `forge_platform_system`
-- scope is confined to the identity and tenancy layer (users, organizations,
-- memberships) plus system-owned provider credentials. It has no policy and no
-- table privilege here, so it cannot read or write any tenant's projects, keys,
-- runs, or measurements. There is deliberately no `FOR ALL ... USING
-- (system_scope_is_declared())` policy on this file's tables: such a policy would
-- make the system scope a universal cross-tenant reader and writer, and
-- `forge.system_scope` is a GUC, not an authorization mechanism.
--
-- Cross-tenant references are NOT left to RLS. Row-level security sees one table,
-- so it cannot observe that a row points at a parent in another organization.
-- That is the job of the composite foreign keys in 0006_projects.sql,
-- 0008_api_keys.sql, 0009_run_records.sql, and 0010_usage_records.sql. RLS is the
-- second layer, not the only one.
--
-- TENANT CONTEXT IS FAIL-CLOSED. platform.is_current_organization() is false
-- when the transaction-local setting is unset OR empty, so with no tenant context
-- a SELECT returns no rows and every write is rejected. See 0003_rls_helpers.sql
-- for why both no-context states have to be handled explicitly.

---------------------------------------------------------------------
-- projects
---------------------------------------------------------------------
CREATE POLICY projects_tenant_select ON projects
    FOR SELECT TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

CREATE POLICY projects_tenant_insert ON projects
    FOR INSERT TO forge_platform_app
    WITH CHECK (platform.is_current_organization(organization_id));

CREATE POLICY projects_tenant_update ON projects
    FOR UPDATE TO forge_platform_app
    USING (platform.is_current_organization(organization_id))
    WITH CHECK (platform.is_current_organization(organization_id));

CREATE POLICY projects_tenant_delete ON projects
    FOR DELETE TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

---------------------------------------------------------------------
-- api_keys
---------------------------------------------------------------------
-- An API key is never workspace or filesystem authority and can never bypass
-- membership, organization, or project authorization. Row-level security makes it
-- tenant-visible; it does not make it a permission. Revocation is a status change
-- with an explicit `revoked_at`, which is why UPDATE exists here and DELETE is
-- only for an administrative cleanup that is still tenant-scoped.
CREATE POLICY api_keys_tenant_select ON api_keys
    FOR SELECT TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

CREATE POLICY api_keys_tenant_insert ON api_keys
    FOR INSERT TO forge_platform_app
    WITH CHECK (platform.is_current_organization(organization_id));

CREATE POLICY api_keys_tenant_update ON api_keys
    FOR UPDATE TO forge_platform_app
    USING (platform.is_current_organization(organization_id))
    WITH CHECK (platform.is_current_organization(organization_id));

CREATE POLICY api_keys_tenant_delete ON api_keys
    FOR DELETE TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

---------------------------------------------------------------------
-- run_records
---------------------------------------------------------------------
-- Execution history is durable truth. Transitions are expected: a run moves
-- from queued to running to a terminal status, and Core's reported progress sets
-- core_run_id, started_at, finished_at, and attempt_count.
--
-- THERE IS NO TENANT DELETE POLICY. A run is retired by its status, never by
-- removing the row, and the DELETE privilege is withheld in 0011_rls_enable.sql
-- as well. Both layers say no, so a later policy edit cannot re-open deletion.
CREATE POLICY run_records_tenant_select ON run_records
    FOR SELECT TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

CREATE POLICY run_records_tenant_insert ON run_records
    FOR INSERT TO forge_platform_app
    WITH CHECK (platform.is_current_organization(organization_id));

CREATE POLICY run_records_tenant_update ON run_records
    FOR UPDATE TO forge_platform_app
    USING (platform.is_current_organization(organization_id))
    WITH CHECK (platform.is_current_organization(organization_id));

---------------------------------------------------------------------
-- usage_records
---------------------------------------------------------------------
-- APPEND-ONLY. A usage record is a physical observation: inserted once, never
-- rewritten, never removed. Corrections are new records, and
-- UNIQUE (run_record_id, attempt_number) already prevents a silent overwrite of
-- an attempt's measurement.
--
-- SELECT and INSERT policies only. UPDATE and DELETE have no policy AND no
-- table privilege, so the append-only guarantee holds at both layers.
CREATE POLICY usage_records_tenant_select ON usage_records
    FOR SELECT TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

CREATE POLICY usage_records_tenant_insert ON usage_records
    FOR INSERT TO forge_platform_app
    WITH CHECK (platform.is_current_organization(organization_id));
