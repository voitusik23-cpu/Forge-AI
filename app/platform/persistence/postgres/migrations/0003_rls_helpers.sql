-- 0009_rls_helpers.sql
--
-- RLS helper functions and the platform roles.
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section I, D-PLATFORM-18.
--
-- WHY FUNCTIONS. The tenant predicate is repeated across nine policies on eight
-- tables. Writing it once makes the fail-closed property auditable in one place
-- instead of relying on the same expression being copied correctly nine times.
--
-- FAIL-CLOSED, AND THE EMPTY-STRING TRAP. There are TWO distinct "no context"
-- states, and they return DIFFERENT values. Both are verified on PostgreSQL 17:
--
--   1. The setting was NEVER assigned in this session:
--      current_setting('x', true) returns NULL.
--   2. The setting WAS assigned and then reverted (a SET LOCAL that has been
--      committed or rolled back): current_setting('x', true) returns an EMPTY
--      STRING, not NULL.
--
-- A predicate written only as `current_setting(...) IS NOT NULL` is therefore
-- true in state 2, and would grant tenant access with no tenant set. nullif(...,
-- '') collapses both states to NULL, and a NULL comparison is never true, so no
-- rows are returned in either state.
--
-- These functions are STABLE, not IMMUTABLE: current_setting is not immutable.
-- Marking them IMMUTABLE would let the planner constant-fold them and break the
-- fail-closed behaviour.
--
-- FUNCTIONS LIVE IN THE `platform` SCHEMA. `public` is writable by PUBLIC on
-- PostgreSQL 14 and earlier and is therefore unsafe for a SECURITY DEFINER-free
-- helper that policies depend on. The schema is created with no CREATE grant for
-- PUBLIC.

CREATE SCHEMA IF NOT EXISTS platform;

REVOKE CREATE ON SCHEMA platform FROM PUBLIC;

COMMENT ON SCHEMA platform IS
    'Platform governance helpers. Not exposed to clients; the tenant and system '
    'scope predicates that row-level security policies depend on.';


-- The current tenant, or NULL when no tenant context is set / it is empty.
CREATE OR REPLACE FUNCTION platform.current_organization_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT nullif(current_setting('forge.organization_id', true), '')::uuid
$$;

COMMENT ON FUNCTION platform.current_organization_id() IS
    'Tenant of the current transaction from the transaction-local setting '
    'forge.organization_id. NULL when the setting is unset (which yields NULL) or '
    'empty (which yields an empty string), so every tenant predicate denies in '
    'both cases. NULL is never a wildcard.';


-- True only when a server-authorized path has explicitly declared itself.
CREATE OR REPLACE FUNCTION platform.system_scope_is_declared()
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT coalesce(nullif(current_setting('forge.system_scope', true), ''), 'off') = 'on'
$$;

COMMENT ON FUNCTION platform.system_scope_is_declared() IS
    'True only when the server-side system scope flag is set to on. This is a '
    'separate authorization boundary, not a tenant RLS bypass.';


-- The one containment predicate used by every tenant-owned policy.
CREATE OR REPLACE FUNCTION platform.is_current_organization(
    row_organization_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT row_organization_id IS NOT NULL
       AND row_organization_id = platform.current_organization_id()
$$;

COMMENT ON FUNCTION platform.is_current_organization(uuid) IS
    'Tenant containment predicate. Requires a non-null row tenant AND equality '
    'with the transaction tenant, so it is false when no context is set and false '
    'for system-owned rows. The explicit non-null conjunct is kept so that the '
    'containment requirement is readable and cannot be simplified into something '
    'a NULL satisfies.';


-- The current subject, or NULL when unset / empty. Used by the `users` policy,
-- which is membership-mediated rather than tenant-owned.
CREATE OR REPLACE FUNCTION platform.current_user_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT nullif(current_setting('forge.user_id', true), '')::uuid
$$;

COMMENT ON FUNCTION platform.current_user_id() IS
    'Subject of the current transaction from the transaction-local setting '
    'forge.user_id. NULL when unset or empty, so the self-visibility rule denies '
    'rather than defaulting to any user.';


-- Array validation helper for a CHECK constraint.
--
-- A CHECK constraint may not contain a subquery, so the "no scope is blank" rule
-- for api_keys.scopes cannot be written with unnest() inline. It is written as a
-- function instead.
--
-- This one IS marked IMMUTABLE, unlike the context readers above: it depends only
-- on its argument, which is exactly what makes it legal in a CHECK constraint.
CREATE OR REPLACE FUNCTION platform.text_array_has_blank_entry(entries text[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT EXISTS (
        SELECT 1 FROM unnest(entries) AS entry
        WHERE length(btrim(entry)) = 0
    )
$$;

COMMENT ON FUNCTION platform.text_array_has_blank_entry(text[]) IS
    'True when a text array contains an empty or whitespace-only entry. NULL '
    'array elements make the EXISTS unknown, and a CHECK treats unknown as '
    'satisfied, so callers must also require a NOT NULL array column.';


-- The explicit system-owned predicate. Deliberately separate from the tenant
-- predicate so that system-owned rows can never be reached by widening the
-- tenant rule.
CREATE OR REPLACE FUNCTION platform.is_system_owned_provider_account(
    row_organization_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT row_organization_id IS NULL
       AND platform.system_scope_is_declared()
$$;

COMMENT ON FUNCTION platform.is_system_owned_provider_account(uuid) IS
    'System-owned provider account predicate. Requires a null organization_id '
    'AND an explicitly declared server-side system scope.';


REVOKE ALL ON ALL FUNCTIONS IN SCHEMA platform FROM PUBLIC;

-- Public execute on a policy helper would let an unrelated role probe tenant
-- identifiers. The two platform roles are the only callers.
GRANT USAGE ON SCHEMA platform TO forge_platform_app, forge_platform_system;

GRANT EXECUTE ON FUNCTION platform.current_organization_id()
    TO forge_platform_app, forge_platform_system;
GRANT EXECUTE ON FUNCTION platform.current_user_id()
    TO forge_platform_app, forge_platform_system;
GRANT EXECUTE ON FUNCTION platform.system_scope_is_declared()
    TO forge_platform_app, forge_platform_system;
GRANT EXECUTE ON FUNCTION platform.is_current_organization(uuid)
    TO forge_platform_app, forge_platform_system;
GRANT EXECUTE ON FUNCTION platform.is_system_owned_provider_account(uuid)
    TO forge_platform_app, forge_platform_system;
GRANT EXECUTE ON FUNCTION platform.text_array_has_blank_entry(text[])
    TO forge_platform_app, forge_platform_system;
