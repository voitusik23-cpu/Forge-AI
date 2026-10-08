-- 0013_policies_tenancy.sql
--
-- RLS policies for the remaining tenant-owned tables:
-- `projects`, `api_keys`, `run_records`, `usage_records`.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md sections I-3 and I-8.
--
-- THE SAME FOUR POLICIES ON EVERY TABLE, and the operation split is the point:
--
--   SELECT / DELETE  ->  USING  (which rows are visible)
--   INSERT / UPDATE  ->  WITH CHECK  (which rows may be written)
--
-- A USING-only policy filters reads while still permitting a write into another
-- tenant. WITH CHECK is what actually prevents a cross-tenant INSERT and, because
-- UPDATE carries both clauses, what prevents a row being MOVED between tenants.
--
-- Cross-tenant references are NOT left to RLS. Row-level security sees one table,
-- so it cannot observe that a row points at a parent in another organization.
-- That is K-7's job: the composite foreign keys in 0006_projects.sql,
-- 0009_run_records.sql, and 0010_usage_records.sql make such a reference
-- structurally impossible. RLS is the second layer, not the only one.
--
-- TENANT CONTEXT IS FAIL-CLOSED. platform.is_current_organization() is false
-- when the transaction-local setting is unset OR empty, so with no tenant context
-- a SELECT returns no rows and every write is rejected. See 0003_rls_helpers.sql
-- for why the empty-string case has to be handled explicitly.

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

CREATE POLICY projects_system_scope ON projects
    FOR ALL TO forge_platform_system
    USING (platform.system_scope_is_declared())
    WITH CHECK (platform.system_scope_is_declared());

---------------------------------------------------------------------
-- api_keys
---------------------------------------------------------------------
-- An API key is never workspace or filesystem authority and can never bypass
-- membership, organization, or project authorization. Row-level security makes
-- it tenant-visible; it does not make it a permission.
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

CREATE POLICY api_keys_system_scope ON api_keys
    FOR ALL TO forge_platform_system
    USING (platform.system_scope_is_declared())
    WITH CHECK (platform.system_scope_is_declared());

---------------------------------------------------------------------
-- run_records
---------------------------------------------------------------------
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

CREATE POLICY run_records_tenant_delete ON run_records
    FOR DELETE TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

CREATE POLICY run_records_system_scope ON run_records
    FOR ALL TO forge_platform_system
    USING (platform.system_scope_is_declared())
    WITH CHECK (platform.system_scope_is_declared());

---------------------------------------------------------------------
-- usage_records
---------------------------------------------------------------------
CREATE POLICY usage_records_tenant_select ON usage_records
    FOR SELECT TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

CREATE POLICY usage_records_tenant_insert ON usage_records
    FOR INSERT TO forge_platform_app
    WITH CHECK (platform.is_current_organization(organization_id));

-- A usage record is an observation and is not updated in normal operation. The
-- policy is still written with both clauses so that no future writer can move a
-- measurement between tenants.
CREATE POLICY usage_records_tenant_update ON usage_records
    FOR UPDATE TO forge_platform_app
    USING (platform.is_current_organization(organization_id))
    WITH CHECK (platform.is_current_organization(organization_id));

CREATE POLICY usage_records_tenant_delete ON usage_records
    FOR DELETE TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

CREATE POLICY usage_records_system_scope ON usage_records
    FOR ALL TO forge_platform_system
    USING (platform.system_scope_is_declared())
    WITH CHECK (platform.system_scope_is_declared());
