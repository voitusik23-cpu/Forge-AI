-- 0014_policies_users_provider_accounts.sql
--
-- RLS policies for `users` and `provider_accounts`.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md sections I-4, I-5, and F,
-- D-PLATFORM-16.
--
-- These are the two tables where the ordinary tenant predicate is NOT enough or
-- NOT applicable, and both are easy to get wrong in the same way: by widening a
-- predicate until a NULL satisfies it.

---------------------------------------------------------------------
-- users
---------------------------------------------------------------------
-- A user is a GLOBAL identity and carries no organization_id, so the tenant
-- predicate does not apply. The approved rule is:
--
--   * a subject may see itself;
--   * other users are visible only through a shared organization.
--
-- The current user is read from a second transaction-local setting,
-- forge.user_id. It is read through the same nullif(..., '') treatment as the
-- tenant, for the same reason: an unset setting is an empty string, and an empty
-- string would otherwise compare unequal to every id but still not fail closed in
-- an expression that only tested IS NOT NULL.
--
-- A blanket SELECT on users would be a cross-tenant leak of identities, so it is
-- not granted. INSERT/UPDATE/DELETE have no policy for the tenant role at all,
-- which denies them; account creation and status changes are server-side.

CREATE POLICY users_tenant_select ON users
    FOR SELECT TO forge_platform_app
    USING (
        -- the subject itself
        id = nullif(current_setting('forge.user_id', true), '')::uuid
        -- or a user in the current tenant that the subject shares
        OR (
            platform.current_organization_id() IS NOT NULL
            AND EXISTS (
                SELECT 1
                FROM memberships m_self
                JOIN memberships m_other
                  ON m_other.organization_id = m_self.organization_id
                WHERE m_self.user_id =
                          nullif(current_setting('forge.user_id', true), '')::uuid
                  AND m_self.organization_id = platform.current_organization_id()
                  AND m_other.user_id = users.id
            )
        )
    );

CREATE POLICY users_system_scope ON users
    FOR ALL TO forge_platform_system
    USING (platform.system_scope_is_declared())
    WITH CHECK (platform.system_scope_is_declared());

---------------------------------------------------------------------
-- provider_accounts
---------------------------------------------------------------------
-- D-PLATFORM-16 in SQL. This is the one table with two ownership modes, and the
-- tenant rule must EXCLUDE the system-owned rows rather than include them.
--
-- The tenant predicate is written as
--     organization_id IS NOT NULL AND organization_id = current tenant
-- so the exclusion requirement is visible in the policy text itself, instead of
-- being left to the reader to infer from SQL's three-valued logic.
--
-- TWO PATTERNS ARE FORBIDDEN HERE AND MUST NOT BE REINTRODUCED, because each one
-- silently turns a NULL tenant column into a wildcard:
--
--     organization_id IS NULL OR organization_id = current_tenant()
--     current_tenant() IS NULL OR ...
--
-- System-owned rows (organization_id IS NULL) are therefore invisible to
-- forge_platform_app in every operation. They are reachable only through the
-- explicit system-owned policy below, which requires BOTH a null
-- organization_id AND an explicitly declared server-side system scope.
--
-- That system policy is a SEPARATE AUTHORIZATION BOUNDARY, not a tenant RLS
-- bypass: it is attached to a different role, is off unless the server declares
-- it, and grants no execution authority. System ownership is an ownership
-- boundary and nothing more.

CREATE POLICY provider_accounts_tenant_select ON provider_accounts
    FOR SELECT TO forge_platform_app
    USING (
        organization_id IS NOT NULL
        AND platform.is_current_organization(organization_id)
    );

CREATE POLICY provider_accounts_tenant_insert ON provider_accounts
    FOR INSERT TO forge_platform_app
    WITH CHECK (
        organization_id IS NOT NULL
        AND platform.is_current_organization(organization_id)
    );

CREATE POLICY provider_accounts_tenant_update ON provider_accounts
    FOR UPDATE TO forge_platform_app
    USING (
        organization_id IS NOT NULL
        AND platform.is_current_organization(organization_id)
    )
    WITH CHECK (
        organization_id IS NOT NULL
        AND platform.is_current_organization(organization_id)
    );

CREATE POLICY provider_accounts_tenant_delete ON provider_accounts
    FOR DELETE TO forge_platform_app
    USING (
        organization_id IS NOT NULL
        AND platform.is_current_organization(organization_id)
    );

-- The system-owned path. Both the row shape and an explicit server-side
-- declaration are required, so no tenant request can reach these rows and no
-- client can manufacture system ownership by sending a null organization.
CREATE POLICY provider_accounts_system_owned ON provider_accounts
    FOR ALL TO forge_platform_system
    USING (platform.is_system_owned_provider_account(organization_id))
    WITH CHECK (platform.is_system_owned_provider_account(organization_id));
