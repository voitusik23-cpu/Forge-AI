-- 0014_policies_users_provider_accounts.sql
--
-- RLS policies for `users` and `provider_accounts`.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md sections I-4, I-5, O, and F,
-- D-PLATFORM-16, D-PLATFORM-19.
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

-- The system scope needs to resolve a subject BEFORE a tenant context exists.
-- That is a read of identity, and it is granted as a read only: this scope cannot
-- change an account or erase one.
CREATE POLICY users_system_select ON users
    FOR SELECT TO forge_platform_system
    USING (platform.system_scope_is_declared());

-- It does need to CREATE an account: registering an organization together with its
-- first OWNER membership is a bootstrap step that cannot itself be tenant-scoped,
-- and the membership's provenance key requires the user row to exist first.
--
-- This closes a real gap: the migration granted INSERT on this table to the system
-- role without granting a policy to match, so the privilege was unreachable and the
-- bootstrap path could not actually run. A capability that is granted but not
-- policied is a latent inconsistency, not a safety feature -- the tests caught it.
CREATE POLICY users_system_insert ON users
    FOR INSERT TO forge_platform_system
    WITH CHECK (platform.system_scope_is_declared());

-- A subject may change its OWN record, and nothing else. The predicate requests the
-- subject's own id, so the write is bounded to the row the transaction has already
-- declared it is acting as.
--
-- This policy closes a second real gap found the same way as the first: the
-- migration granted UPDATE on `users` to the tenant role, but with no UPDATE policy
-- the statement matched zero rows and the repository reported a missing record. An
-- unreachable privilege is a silent wrong answer, not a safeguard.
CREATE POLICY users_tenant_update ON users
    FOR UPDATE TO forge_platform_app
    USING (id = platform.current_user_id())
    WITH CHECK (id = platform.current_user_id());

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

-- The system-owned path. BOTH conditions are required: the row must have a null
-- organization_id AND the server must have declared the system scope. This is the
-- only table where the system scope may write operational data, and the reason is
-- structural rather than discretionary -- a system-owned credential belongs to no
-- organization, so no tenant context can ever reach it.
--
-- `FOR ALL` is used here, unlike every other system policy, because the row shape
-- predicate is exact and is applied to every operation: there is no row that
-- satisfies it other than a system-owned one, and none of these operations can
-- touch a tenant-owned row. On a table where the predicate were merely a scope
-- flag, `FOR ALL` would be the wildcard this design forbids.
CREATE POLICY provider_accounts_system_owned ON provider_accounts
    FOR ALL TO forge_platform_system
    USING (platform.is_system_owned_provider_account(organization_id))
    WITH CHECK (platform.is_system_owned_provider_account(organization_id));
