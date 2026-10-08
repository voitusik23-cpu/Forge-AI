-- 0012_policies_organizations.sql
--
-- RLS policies for `organizations` and `memberships`.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section I-5 and I-3.
--
-- NEITHER TABLE USES THE ORDINARY TENANT PREDICATE.
--
-- `organizations` has no organization_id -- it IS the tenant boundary -- so a
-- tenant predicate does not apply to it. Its visibility rule is instead
-- membership-mediated: a subject may see an organization only if it holds an
-- ACTIVE membership in it.
--
-- `memberships` DOES carry organization_id and uses the ordinary containment
-- predicate. The extra nuance is on writing: a membership may only be created or
-- changed for the CURRENT tenant, and only while the current tenant is itself
-- visible. Otherwise an actor could insert a membership into an organization it
-- cannot even read.
--
-- CREATING AN ORGANIZATION AND ITS FIRST OWNER MEMBERSHIP IS A SERVER-ONLY PATH.
-- There is deliberately no policy here that lets the ordinary tenant role create
-- an organization, because the role that creates it must also be able to create
-- its first membership -- a bootstrap step that cannot itself be tenant-scoped.
-- The system role owns that step; see the policies at the end of this file.
--
-- FOR THE ORDINARY TENANT ROLE, ONLY SELECT POLICIES ARE DEFINED. An absent
-- policy means denied, so INSERT, UPDATE, and DELETE on organizations are denied
-- outright, which is the fail-closed direction.

---------------------------------------------------------------------
-- memberships: ordinary tenant containment
---------------------------------------------------------------------
CREATE POLICY memberships_tenant_select ON memberships
    FOR SELECT TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

CREATE POLICY memberships_tenant_insert ON memberships
    FOR INSERT TO forge_platform_app
    WITH CHECK (
        platform.is_current_organization(organization_id)
        AND EXISTS (
            SELECT 1 FROM organizations o
            WHERE o.id = organization_id
              AND o.id = platform.current_organization_id()
        )
    );

CREATE POLICY memberships_tenant_update ON memberships
    FOR UPDATE TO forge_platform_app
    USING (platform.is_current_organization(organization_id))
    WITH CHECK (platform.is_current_organization(organization_id));

CREATE POLICY memberships_tenant_delete ON memberships
    FOR DELETE TO forge_platform_app
    USING (platform.is_current_organization(organization_id));

---------------------------------------------------------------------
-- memberships: system role, gated by the explicit system scope flag
---------------------------------------------------------------------
CREATE POLICY memberships_system_scope ON memberships
    FOR ALL TO forge_platform_system
    USING (platform.system_scope_is_declared())
    WITH CHECK (platform.system_scope_is_declared());

---------------------------------------------------------------------
-- organizations: membership-mediated visibility, read-only for tenants
---------------------------------------------------------------------
-- Visible only through an ACTIVE membership of the current tenant. The
-- o.id = platform.current_organization_id() conjunct is re-stated here so that
-- this policy does not depend on the memberships policy still being in force.
CREATE POLICY organizations_tenant_select ON organizations
    FOR SELECT TO forge_platform_app
    USING (
        platform.current_organization_id() IS NOT NULL
        AND id = platform.current_organization_id()
        AND EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.organization_id = organizations.id
              AND m.status = 'active'
        )
    );

-- No INSERT/UPDATE/DELETE policy for forge_platform_app: creating an
-- organization and its first OWNER membership is a server-only bootstrap step.
CREATE POLICY organizations_system_scope ON organizations
    FOR ALL TO forge_platform_system
    USING (platform.system_scope_is_declared())
    WITH CHECK (platform.system_scope_is_declared());
