-- 0012_policies_organizations.sql
--
-- RLS policies for `organizations` and `memberships`.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md sections I-5, O,
-- D-PLATFORM-19.
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
-- changed for the CURRENT tenant.
--
-- THE SYSTEM SCOPE IS THE BOOTSTRAP AND DISCOVERY SCOPE, AND NOTHING MORE. It is
-- confined to exactly three things here and in 0014:
--
--   1. discovering a subject BEFORE any tenant context exists: find the user,
--      find their memberships, find the organizations those memberships name;
--   2. the bootstrap that cannot itself be tenant-scoped: creating an
--      organization and its first OWNER membership;
--   3. system-owned provider credentials.
--
-- It is NOT a general cross-tenant reader or writer, and it does not reach the
-- operational tables at all (see 0013). There is no `FOR ALL` policy anywhere on
-- this scope: every capability is enumerated, so a new table or a new operation
-- is denied by default rather than permitted by a wildcard.
--
-- `forge.system_scope` IS NOT AN AUTHORIZATION MECHANISM. Any session can set a
-- custom GUC. What gates system access is the policy's `TO forge_platform_system`
-- clause plus that role's membership, which the deployment controls. The GUC only
-- narrows the scope further and keeps system-owned rows invisible by default.
--
-- CREATING AN ORGANIZATION AND ITS FIRST OWNER MEMBERSHIP IS A SERVER-ONLY PATH.
-- There is deliberately no policy that lets the tenant role create an
-- organization, because the role that creates it must also be able to create its
-- first membership -- a bootstrap step that cannot itself be tenant-scoped.

---------------------------------------------------------------------
-- memberships
---------------------------------------------------------------------
-- The EXISTS conjunct on the INSERT is not decorative: it prevents an actor from
-- inserting a membership into an organization it cannot itself read. On INSERT
-- the SELECT policy is evaluated against the new row, so this is also what makes
-- the bootstrap order work -- the organization must become visible before its
-- first membership can be written.
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
-- memberships: system scope, enumerated
---------------------------------------------------------------------
-- Discovery reads. UPDATE exists so the bootstrap path can reactivate an
-- existing membership (K-2 allows one row per organization and user pair, so a
-- returning member is a status change, not a second row). DELETE is granted
-- because membership removal is the administrative counterpart of the RESTRICT
-- keys: provenance rows must not block it forever.
CREATE POLICY memberships_system_select ON memberships
    FOR SELECT TO forge_platform_system
    USING (platform.system_scope_is_declared());

CREATE POLICY memberships_system_insert ON memberships
    FOR INSERT TO forge_platform_system
    WITH CHECK (platform.system_scope_is_declared());

CREATE POLICY memberships_system_update ON memberships
    FOR UPDATE TO forge_platform_system
    USING (platform.system_scope_is_declared())
    WITH CHECK (platform.system_scope_is_declared());

CREATE POLICY memberships_system_delete ON memberships
    FOR DELETE TO forge_platform_system
    USING (platform.system_scope_is_declared());

---------------------------------------------------------------------
-- organizations: membership-mediated visibility, read-only for tenants
---------------------------------------------------------------------
-- Two distinct readings are permitted:
--
--   * the organization named by the CURRENT TENANT CONTEXT. This is a lookup of
--     an identifier the server already resolved and put into the transaction, not
--     a client-supplied value, and it is what makes tenancy work at all: an
--     actor must be able to read the tenant it is acting in before any membership
--     row is written for it.
--   * an organization the subject SHARES through an active membership.
--
-- What is NOT permitted is enumerating organizations. Both branches require the
-- tenant context to be already set; a null context matches neither.
CREATE POLICY organizations_tenant_select ON organizations
    FOR SELECT TO forge_platform_app
    USING (
        platform.current_organization_id() IS NOT NULL
        AND (
            -- the tenant this transaction is acting in
            id = platform.current_organization_id()
            -- or one the subject shares through an active membership
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.organization_id = organizations.id
                  AND m.status = 'active'
                  AND m.user_id =
                        nullif(current_setting('forge.user_id', true), '')::uuid
            )
        )
    );

-- No INSERT/UPDATE/DELETE policy for forge_platform_app: creating an
-- organization and its first OWNER membership is a server-only bootstrap step.
-- An absent policy denies, which is the fail-closed direction.
CREATE POLICY organizations_system_select ON organizations
    FOR SELECT TO forge_platform_system
    USING (platform.system_scope_is_declared());

CREATE POLICY organizations_system_insert ON organizations
    FOR INSERT TO forge_platform_system
    WITH CHECK (platform.system_scope_is_declared());

-- There is deliberately NO system UPDATE or DELETE policy on organizations, and
-- neither privilege is granted. Suspending a tenant and destroying a tenant are
-- not operations any accepted contract requires; the RESTRICT keys mean a delete
-- could not succeed while any tenant data existed anyway. Omitting both keeps
-- "the system scope can mutate or erase a tenant" off the list of things a reader
-- has to rule out, and makes adding either a deliberate expansion.
