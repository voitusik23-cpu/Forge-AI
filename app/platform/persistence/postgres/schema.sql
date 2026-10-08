-- schema.sql
--
-- Consolidated, self-contained Platform schema: every table, constraint, index,
-- row-level security policy, and helper function created by the migrations in
-- `migrations/`, in one file and one transaction.
--
-- WHY THIS FILE EXISTS, given that migrations already do this:
--
--   1. A single reviewable artefact. A reviewer can read the whole schema in one
--      pass instead of assembling it from fourteen files.
--   2. A single-transaction path. Applying it either fully succeeds or leaves the
--      database untouched, which the migration ledger deliberately does not
--      guarantee: each migration commits separately, on purpose, so one failure
--      does not roll back earlier work.
--   3. A benchmark for the migrations. The database contract tests assert that
--      this file and the migration set produce the same tables, the same
--      policies, and the same row-level security state.
--
-- GENERATED FILE -- do not edit by hand. Edit the migration, then run:
--     python -m app.platform.persistence.consolidate
--
-- DESTRUCTIVE. It drops the Platform objects and recreates them. Never run it
-- against a database holding data you want to keep; it exists for a fresh schema
-- and for tests.

BEGIN;

-- The Platform reset. Ordered so that dependants go before their targets; the
-- schema and the migration ledger are dropped with them.
DROP TABLE IF EXISTS usage_records CASCADE;
DROP TABLE IF EXISTS run_records CASCADE;
DROP TABLE IF EXISTS api_keys CASCADE;
DROP TABLE IF EXISTS provider_accounts CASCADE;
DROP TABLE IF EXISTS projects CASCADE;
DROP TABLE IF EXISTS memberships CASCADE;
DROP TABLE IF EXISTS organizations CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS platform_schema_migrations CASCADE;
DROP SCHEMA IF EXISTS platform CASCADE;

-- ==================================================================
-- 0001_users.sql
-- ==================================================================
-- 0001_users.sql
--
-- Platform table: users
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section C-1.
--
-- A user is a GLOBAL identity. It carries no organization_id by design: a user
-- owns nothing, and adding a tenant column here would contradict the Stage 1
-- architecture contract.
--
-- There is no credential column of any kind. Authentication is O-2 and remains
-- OPEN, so no password, hash, session, or token field is created here.

CREATE TABLE users (
    id           uuid        NOT NULL,
    email        text        NOT NULL,
    display_name text        NOT NULL DEFAULT '',
    status       text        NOT NULL DEFAULT 'active',
    created_at   timestamptz NOT NULL,
    updated_at   timestamptz,

    CONSTRAINT users_pkey PRIMARY KEY (id),
    CONSTRAINT users_email_unique UNIQUE (email),
    CONSTRAINT users_email_not_blank CHECK (length(btrim(email)) > 0),
    CONSTRAINT users_status_valid CHECK (status IN ('active', 'suspended'))
);

CREATE INDEX users_status_idx ON users (status);

-- ==================================================================
-- 0002_roles.sql
-- ==================================================================
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

-- ==================================================================
-- 0003_rls_helpers.sql
-- ==================================================================
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

-- ==================================================================
-- 0004_organizations.sql
-- ==================================================================
-- 0002_organizations.sql
--
-- Platform table: organizations
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section C-2.
--
-- An organization IS the tenant boundary, so it carries no organization_id.
-- It is the only record for which that is true, and it must not be treated as a
-- child entity.
--
-- `slug` is deliberately NOT unique (K-3 REJECTED for organizations): the
-- contract fixes no global slug namespace, and a global unique constraint would
-- turn an ordinary constraint error into a cross-tenant existence oracle. A
-- non-unique lookup index is created instead.
--
-- There is no money column: no wallet, balance, or payment-customer reference.
-- Billing is a later stage and there is no `billing_account_ref` here.

CREATE TABLE organizations (
    id          uuid        NOT NULL,
    name        text        NOT NULL,
    slug        text,
    is_personal boolean     NOT NULL DEFAULT false,
    status      text        NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL,
    updated_at  timestamptz,

    CONSTRAINT organizations_pkey PRIMARY KEY (id),
    CONSTRAINT organizations_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT organizations_slug_not_blank
        CHECK (slug IS NULL OR length(btrim(slug)) > 0),
    CONSTRAINT organizations_status_valid CHECK (status IN ('active', 'suspended'))
);

CREATE INDEX organizations_slug_idx ON organizations (slug);
CREATE INDEX organizations_status_idx ON organizations (status);

-- ==================================================================
-- 0005_memberships.sql
-- ==================================================================
-- 0003_memberships.sql
--
-- Platform table: memberships
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section C-3.
--
-- This is the ONLY path from an authenticated subject to tenant authority.
-- A role is not a permission: nothing maps `role` to a path, command, tool, or
-- network grant.
--
-- K-2 ACCEPTED: UNIQUE (organization_id, user_id) -- a user has at most one
-- membership in one organization.
--
-- K-6 ACCEPTED: both foreign keys use ON DELETE RESTRICT, never CASCADE.
-- Deleting a tenant or a user must not silently remove authorization links.
-- Because deletes are restricted, a user who leaves and later rejoins is
-- represented by reactivating the existing row (status, role), never by
-- inserting a second one -- which is what keeps K-2 and K-6 compatible.

CREATE TABLE memberships (
    id              uuid        NOT NULL,
    organization_id uuid        NOT NULL,
    user_id         uuid        NOT NULL,
    role            text        NOT NULL,
    status          text        NOT NULL DEFAULT 'active',
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz,

    CONSTRAINT memberships_pkey PRIMARY KEY (id),
    CONSTRAINT memberships_organization_user_unique
        UNIQUE (organization_id, user_id),
    CONSTRAINT memberships_role_valid
        CHECK (role IN ('owner', 'admin', 'member', 'billing')),
    CONSTRAINT memberships_status_valid
        CHECK (status IN ('active', 'inactive', 'revoked')),
    CONSTRAINT memberships_organization_fk
        FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON DELETE RESTRICT,
    CONSTRAINT memberships_user_fk
        FOREIGN KEY (user_id) REFERENCES users (id)
        ON DELETE RESTRICT
);

CREATE INDEX memberships_organization_idx ON memberships (organization_id);
CREATE INDEX memberships_user_idx ON memberships (user_id);
CREATE INDEX memberships_organization_status_idx
    ON memberships (organization_id, status);

-- ==================================================================
-- 0006_projects.sql
-- ==================================================================
-- 0004_projects.sql
--
-- Platform table: projects
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section C-4.
--
-- A project belongs to exactly one organization and never directly to a user.
--
-- K-7 ACCEPTED (SECURITY): UNIQUE (id, organization_id) is not a natural key --
-- it is the referenced target for the composite foreign keys on run_records and
-- usage_records, which make a cross-tenant parent reference structurally
-- impossible rather than merely policy-caught. It is NOT optional.
-- (id) alone is already unique as the primary key; this second uniqueness is
-- what gives a composite FK something valid to reference.
--
-- `workspace_ref` is OPAQUE. It is not a filesystem path and no CHECK may
-- require it to "look like" one: workspace topology is O-5 and remains OPEN,
-- and Core stays cross-platform.
--
-- K-6 ACCEPTED: ON DELETE RESTRICT, never CASCADE.

CREATE TABLE projects (
    id              uuid        NOT NULL,
    organization_id uuid        NOT NULL,
    name            text        NOT NULL,
    slug            text,
    status          text        NOT NULL DEFAULT 'active',
    workspace_ref   text,
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz,

    CONSTRAINT projects_pkey PRIMARY KEY (id),
    CONSTRAINT projects_id_organization_unique UNIQUE (id, organization_id),
    CONSTRAINT projects_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT projects_slug_not_blank
        CHECK (slug IS NULL OR length(btrim(slug)) > 0),
    CONSTRAINT projects_workspace_ref_not_blank
        CHECK (workspace_ref IS NULL OR length(btrim(workspace_ref)) > 0),
    CONSTRAINT projects_status_valid CHECK (status IN ('active', 'archived')),
    CONSTRAINT projects_organization_fk
        FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON DELETE RESTRICT
);

-- K-3 ACCEPTED: tenant-scoped slug uniqueness for tenant-owned entities.
-- Partial, because the domain default is an empty slug stored as NULL.
CREATE UNIQUE INDEX projects_organization_slug_unique
    ON projects (organization_id, slug)
    WHERE slug IS NOT NULL;

CREATE INDEX projects_organization_idx ON projects (organization_id);
CREATE INDEX projects_organization_status_idx
    ON projects (organization_id, status);

-- ==================================================================
-- 0007_provider_accounts.sql
-- ==================================================================
-- 0005_provider_accounts.sql
--
-- Platform table: provider_accounts
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md sections C-5 and F,
-- and D-PLATFORM-16.
--
-- organization_id is THE ONLY NULLABLE TENANT COLUMN IN THIS SCHEMA, and the
-- nullability IS the ownership discriminator:
--
--   organization_id IS NOT NULL  ->  tenant-owned (BYOK)
--   organization_id IS NULL      ->  system-owned / Forge-managed
--
-- A NULL is never a wildcard and never means "all tenants". Tenant-scoped
-- access excludes such rows structurally; see 0011_policies_provider_accounts.sql.
--
-- K-4 ACCEPTED: two partial unique indexes, which is exactly what
-- D-PLATFORM-16 requires. A partial index is available precisely because the two
-- modes are distinguished by nullability, so NO additional ownership column is
-- introduced.
--
-- `secret_ref` is an OPAQUE handle and never credential material. It does not
-- store plaintext, ciphertext, a parsed scheme, or a vendor identifier; the
-- secret backend and reference grammar are O-3 and O-4 and remain OPEN.
--
-- `metadata` is jsonb, which the approved schema design permits for this table
-- only. It holds descriptive provider metadata, never money and never authority.
--
-- K-6 ACCEPTED: ON DELETE RESTRICT. A system-owned row has NULL and is
-- unaffected by any organization delete.

CREATE TABLE provider_accounts (
    id              uuid        NOT NULL,
    organization_id uuid,
    provider_name   text        NOT NULL,
    secret_ref      text        NOT NULL,
    status          text        NOT NULL DEFAULT 'active',
    metadata        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz,

    CONSTRAINT provider_accounts_pkey PRIMARY KEY (id),
    CONSTRAINT provider_accounts_provider_name_not_blank
        CHECK (length(btrim(provider_name)) > 0),
    CONSTRAINT provider_accounts_secret_ref_not_blank
        CHECK (length(btrim(secret_ref)) > 0),
    CONSTRAINT provider_accounts_status_valid
        CHECK (status IN ('active', 'disabled')),
    CONSTRAINT provider_accounts_metadata_is_object
        CHECK (jsonb_typeof(metadata) = 'object'),
    CONSTRAINT provider_accounts_organization_fk
        FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON DELETE RESTRICT
);

-- K-4: one tenant-owned account per provider per organization ...
CREATE UNIQUE INDEX provider_accounts_tenant_provider_unique
    ON provider_accounts (organization_id, provider_name)
    WHERE organization_id IS NOT NULL;

-- ... and at most one system-owned account per provider.
CREATE UNIQUE INDEX provider_accounts_system_provider_unique
    ON provider_accounts (provider_name)
    WHERE organization_id IS NULL;

CREATE INDEX provider_accounts_organization_idx
    ON provider_accounts (organization_id);
CREATE INDEX provider_accounts_provider_name_idx
    ON provider_accounts (provider_name);
CREATE INDEX provider_accounts_metadata_idx
    ON provider_accounts USING gin (metadata jsonb_path_ops);

-- ==================================================================
-- 0008_api_keys.sql
-- ==================================================================
-- 0006_api_keys.sql
--
-- Platform table: api_keys
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section C-6.
--
-- There is NO token column and NO hash column. The plaintext key is shown once
-- at creation and only a safe representation is persisted, but the algorithm of
-- that representation is O-1 and remains OPEN -- creating a field for it now
-- would close O-1 silently.
--
-- K-5 DEFERRED: key_prefix carries NO unique constraint. Whether a prefix must
-- be unique depends on the key format and on whether authentication looks keys
-- up by prefix, both part of O-1. The column exists unconstrained.
--
-- K-9 ACCEPTED (representation only): scopes is text[], matching the domain's
-- tuple[str, ...]. The GIN index is DEFERRED and not created, because scope
-- meaning is still open and indexing it now would presume a query pattern that
-- has not been decided.
--
-- K-6 ACCEPTED:
--   created_by_user_id is NOT NULL in the domain, so ON DELETE SET NULL is not
--   available; RESTRICT is used so that deleting a user cannot orphan or destroy
--   key provenance. The same applies to organization_id.
--
-- The domain invariant "REVOKED <=> revoked_at present" is enforced exactly.

CREATE TABLE api_keys (
    id                 uuid        NOT NULL,
    organization_id    uuid        NOT NULL,
    created_by_user_id uuid        NOT NULL,
    name               text        NOT NULL,
    key_prefix         text,
    scopes             text[]      NOT NULL DEFAULT '{}',
    status             text        NOT NULL DEFAULT 'active',
    expires_at         timestamptz,
    revoked_at         timestamptz,
    last_used_at       timestamptz,
    created_at         timestamptz NOT NULL,
    updated_at         timestamptz,

    CONSTRAINT api_keys_pkey PRIMARY KEY (id),
    CONSTRAINT api_keys_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT api_keys_key_prefix_not_blank
        CHECK (key_prefix IS NULL OR length(btrim(key_prefix)) > 0),
    CONSTRAINT api_keys_status_valid CHECK (status IN ('active', 'revoked')),
    -- the domain contract's bidirectional revocation rule
    CONSTRAINT api_keys_revocation_consistent
        CHECK ((status = 'revoked') = (revoked_at IS NOT NULL)),
    -- scopes must never contain a blank entry. Written as a function call
    -- because a CHECK constraint may not contain a subquery.
    CONSTRAINT api_keys_scopes_no_blank
        CHECK (NOT platform.text_array_has_blank_entry(scopes)),
    CONSTRAINT api_keys_organization_fk
        FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON DELETE RESTRICT,
    CONSTRAINT api_keys_created_by_user_fk
        FOREIGN KEY (created_by_user_id) REFERENCES users (id)
        ON DELETE RESTRICT,
    -- TENANT-SAFE PROVENANCE. The single-column key above proves the creator
    -- exists; it does not prove the creator belongs to THIS organization, which
    -- would let a tenant record another tenant's user as the creator of its key
    -- just by knowing that user's id.
    --
    -- This composite key closes that gap by referencing the membership pair,
    -- which `memberships` already constrains as UNIQUE (organization_id,
    -- user_id). The creator must therefore be a member of the key's own
    -- organization, and PostgreSQL enforces it -- not application code.
    --
    -- ON DELETE RESTRICT, consistent with K-6: a membership row is not silently
    -- removable while key provenance still points at it. Memberships are retired
    -- by setting `status`, not by deleting the row.
    CONSTRAINT api_keys_creator_membership_fk
        FOREIGN KEY (organization_id, created_by_user_id)
        REFERENCES memberships (organization_id, user_id)
        ON DELETE RESTRICT
);

CREATE INDEX api_keys_organization_idx ON api_keys (organization_id);
CREATE INDEX api_keys_organization_status_idx
    ON api_keys (organization_id, status);
CREATE INDEX api_keys_created_by_user_idx ON api_keys (created_by_user_id);

-- ==================================================================
-- 0009_run_records.sql
-- ==================================================================
-- 0007_run_records.sql
--
-- Platform table: run_records
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section C-7.
--
-- TWO IDENTITIES, never one:
--   id           the Platform identity (primary key, uuid)
--   core_run_id  the Core correlation identity (text, distinct from id)
--
-- core_run_id is TEXT, not uuid, because Core mints its own correlation values
-- (run-loop-<hex>, run-accept-*, run-verify-*, run-api-*) and the contract does
-- not make them UUIDs. Typing it as uuid would invent a requirement Core does
-- not satisfy.
--
-- K-8 DEFERRED: core_run_id is NOT UNIQUE. Uniqueness would freeze retry and
-- resume semantics that are neither implemented nor decided. A NON-UNIQUE lookup
-- index is created instead, which is what resolving a correlation id to a
-- Platform run actually needs.
--
-- This table is not Core authorization, not Core execution authority, not a
-- replacement for the Core runtime run, and not a replacement for RunStore.
--
-- LIFECYCLE CONSTRAINTS: exactly the ones the domain contract already fixes.
-- Anything that depends on O-7 (which statuses are terminal, whether a terminal
-- status requires finished_at, whether finished_at may be set for a non-terminal
-- status) is deliberately NOT constrained, so that deciding O-7 needs no data
-- migration.
--
-- `initiated_by_user_id` is defined NULLABLE and then made NOT NULL for every
-- non-queued status by a CHECK. This keeps K-6's ON DELETE SET NULL available
-- for optional audit provenance while still expressing the domain rule that a
-- non-queued run records who initiated it.
--
-- K-6 ACCEPTED: ON DELETE RESTRICT on project_id and organization_id, so
-- execution history is durable truth. SET NULL only on the optional field.
--
-- K-7 ACCEPTED (SECURITY): UNIQUE (id, organization_id) is the referenced
-- target for the composite foreign key from usage_records.

CREATE TABLE run_records (
    id                    uuid        NOT NULL,
    organization_id       uuid        NOT NULL,
    project_id            uuid        NOT NULL,
    core_run_id           text,
    initiated_by_user_id  uuid,
    task_id               text        NOT NULL DEFAULT '',
    status                text        NOT NULL DEFAULT 'queued',
    started_at            timestamptz,
    finished_at           timestamptz,
    attempt_count         integer     NOT NULL DEFAULT 0,
    failure_classification text       NOT NULL DEFAULT '',
    created_at            timestamptz NOT NULL,
    updated_at            timestamptz,

    CONSTRAINT run_records_pkey PRIMARY KEY (id),
    -- K-7 referenced target for the composite FK from usage_records
    CONSTRAINT run_records_id_organization_unique UNIQUE (id, organization_id),
    CONSTRAINT run_records_status_valid CHECK (status IN (
        'queued', 'running', 'succeeded', 'failed',
        'cancelled', 'timed_out', 'interrupted'
    )),
    -- the two identities can never collapse
    CONSTRAINT run_records_core_run_id_distinct
        CHECK (core_run_id IS DISTINCT FROM id::text),
    -- queued: Core has not been reached
    CONSTRAINT run_records_queued_has_no_core_identity
        CHECK (status <> 'queued'
               OR (core_run_id IS NULL AND started_at IS NULL)),
    -- any non-queued state was reached by Core, so both must be present
    CONSTRAINT run_records_started_has_core_identity
        CHECK (status = 'queued'
               OR (core_run_id IS NOT NULL AND started_at IS NOT NULL)),
    -- a non-queued run records who initiated it
    CONSTRAINT run_records_started_has_initiator
        CHECK (status = 'queued' OR initiated_by_user_id IS NOT NULL),
    -- a finish time without a start time is incoherent
    CONSTRAINT run_records_finished_requires_started
        CHECK (finished_at IS NULL OR started_at IS NOT NULL),
    -- a run cannot finish before it started
    CONSTRAINT run_records_finished_after_started
        CHECK (finished_at IS NULL OR finished_at >= started_at),
    -- a succeeded run carries no failure classification
    CONSTRAINT run_records_succeeded_has_no_failure
        CHECK (status <> 'succeeded' OR failure_classification = ''),
    CONSTRAINT run_records_attempt_count_non_negative
        CHECK (attempt_count >= 0),
    CONSTRAINT run_records_organization_fk
        FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON DELETE RESTRICT,
    -- K-7: a project reference can never cross a tenant boundary
    CONSTRAINT run_records_project_organization_fk
        FOREIGN KEY (project_id, organization_id) REFERENCES projects (id, organization_id)
        ON DELETE RESTRICT,
    CONSTRAINT run_records_initiated_by_user_fk
        FOREIGN KEY (initiated_by_user_id) REFERENCES users (id)
        ON DELETE SET NULL,
    -- TENANT-SAFE PROVENANCE. As with api_keys, the single-column key above
    -- proves the initiator exists but not that they belonged to this
    -- organization, which would let a tenant name another tenant's user as the
    -- initiator of its run.
    --
    -- The composite key references the membership pair, so the initiator must be
    -- a member of the run's own organization.
    --
    -- NULL SEMANTICS ARE PRESERVED. `initiated_by_user_id` is legitimately NULL
    -- for a queued run, and by default a composite foreign key is satisfied
    -- whenever ANY of its columns is NULL, so a queued run with no initiator
    -- still passes. Only the case that matters -- a non-null initiator who is not
    -- a member of this organization -- is rejected.
    --
    -- ON DELETE RESTRICT, consistent with K-6. Note that the single-column key
    -- above is SET NULL: the two are not in conflict, because a user cannot be
    -- deleted while they still have runs, and once the membership row is gone
    -- there is nothing left referencing it. Memberships are retired by setting
    -- `status`, not by deleting the row.
    CONSTRAINT run_records_initiator_membership_fk
        FOREIGN KEY (organization_id, initiated_by_user_id)
        REFERENCES memberships (organization_id, user_id)
        ON DELETE RESTRICT
);

CREATE INDEX run_records_organization_idx ON run_records (organization_id);
-- K-8: NON-unique correlation lookup
CREATE INDEX run_records_core_run_id_idx
    ON run_records (core_run_id)
    WHERE core_run_id IS NOT NULL;
CREATE INDEX run_records_organization_status_idx
    ON run_records (organization_id, status);
CREATE INDEX run_records_project_created_idx
    ON run_records (project_id, created_at DESC);
CREATE INDEX run_records_organization_created_idx
    ON run_records (organization_id, created_at DESC);
CREATE INDEX run_records_initiated_by_user_idx
    ON run_records (initiated_by_user_id);

-- ==================================================================
-- 0010_usage_records.sql
-- ==================================================================
-- 0008_usage_records.sql
--
-- Platform table: usage_records
--
-- Contract: docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md section C-8 and O-8.
--
-- THREE IDENTITIES, NEVER ONE:
--   run_record_id   -> run_records.id   (the Platform run)
--   core_run_id                         (the Core correlation identity)
--   attempt_number                      (the physical attempt)
--
-- There is NO `run_id` column. An ambiguous single field is exactly what R-1
-- forbids.
--
-- O-8 RESOLVED AT CONTRACT LEVEL: UNIQUE (run_record_id, attempt_number) is the
-- ONLY contract-level uniqueness. It states that one physical attempt of one
-- Platform run has one usage record. Any further dimension requires a SEPARATE
-- decision and must never be added as a silent widening.
--
-- This is a physical-usage observation. It is NOT a cost record: there is no
-- cost, price, charge, balance, margin, or revenue column, and none may be
-- added. Counters are integers, never floats. `duration_seconds` is a
-- measurement, not money.
--
-- Immutability: a usage record is inserted once and not updated; corrections are
-- new records, and the uniqueness key prevents a silent overwrite of an
-- attempt's measurement. There is deliberately no `updated_at` column.
--
-- K-6 ACCEPTED: ON DELETE RESTRICT, so a measurement is never destroyed by
-- deleting its run.
--
-- K-7 ACCEPTED (SECURITY): the composite FK on (run_record_id, organization_id)
-- makes it impossible to attach a measurement to a run in another tenant.

CREATE TABLE usage_records (
    id                uuid        NOT NULL,
    organization_id   uuid        NOT NULL,
    run_record_id     uuid        NOT NULL,
    core_run_id       text        NOT NULL,
    attempt_number    integer     NOT NULL,
    provider_name     text        NOT NULL DEFAULT '',
    model_name        text        NOT NULL DEFAULT '',
    input_tokens      bigint      NOT NULL DEFAULT 0,
    output_tokens     bigint      NOT NULL DEFAULT 0,
    cached_tokens     bigint      NOT NULL DEFAULT 0,
    duration_seconds  double precision NOT NULL DEFAULT 0,
    success           boolean     NOT NULL DEFAULT false,
    fallback          boolean     NOT NULL DEFAULT false,
    tool_call_count   integer     NOT NULL DEFAULT 0,
    completed_at      timestamptz,
    error_type        text        NOT NULL DEFAULT '',
    created_at        timestamptz NOT NULL,

    CONSTRAINT usage_records_pkey PRIMARY KEY (id),
    -- O-8, resolved at contract level
    CONSTRAINT usage_records_run_attempt_unique
        UNIQUE (run_record_id, attempt_number),
    CONSTRAINT usage_records_core_run_id_not_blank
        CHECK (length(btrim(core_run_id)) > 0),
    CONSTRAINT usage_records_attempt_number_non_negative
        CHECK (attempt_number >= 0),
    CONSTRAINT usage_records_input_tokens_non_negative
        CHECK (input_tokens >= 0),
    CONSTRAINT usage_records_output_tokens_non_negative
        CHECK (output_tokens >= 0),
    CONSTRAINT usage_records_cached_tokens_non_negative
        CHECK (cached_tokens >= 0),
    CONSTRAINT usage_records_tool_call_count_non_negative
        CHECK (tool_call_count >= 0),
    CONSTRAINT usage_records_duration_seconds_non_negative
        CHECK (duration_seconds >= 0),
    -- Rejects all three non-finite doubles. PostgreSQL has no
    -- isfinite(double precision) -- only the date/time overloads -- so the check
    -- is written as a comparison against the infinity values.
    --
    -- NaN needs no separate clause and must NOT be "handled" by a
    -- "duration_seconds = duration_seconds" test: PostgreSQL considers NaN equal
    -- to itself AND greater than every other float, so NaN already fails both the
    -- non-negative check above and the upper bound below. A self-equality clause
    -- would ACCEPT NaN rather than reject it.
    CONSTRAINT usage_records_duration_seconds_finite
        CHECK (duration_seconds >= '-Infinity'::double precision
               AND duration_seconds < 'Infinity'::double precision),
    CONSTRAINT usage_records_organization_fk
        FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON DELETE RESTRICT,
    -- K-7: a measurement can never belong to a run in another tenant
    CONSTRAINT usage_records_run_organization_fk
        FOREIGN KEY (run_record_id, organization_id)
        REFERENCES run_records (id, organization_id)
        ON DELETE RESTRICT
);

CREATE INDEX usage_records_organization_idx ON usage_records (organization_id);
CREATE INDEX usage_records_run_record_idx ON usage_records (run_record_id);
CREATE INDEX usage_records_core_run_id_idx ON usage_records (core_run_id);
CREATE INDEX usage_records_organization_created_idx
    ON usage_records (organization_id, created_at DESC);

-- ==================================================================
-- 0011_rls_enable.sql
-- ==================================================================
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

-- ==================================================================
-- 0012_policies_organizations.sql
-- ==================================================================
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

-- ==================================================================
-- 0013_policies_tenancy.sql
-- ==================================================================
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

-- ==================================================================
-- 0014_policies_users_provider_accounts.sql
-- ==================================================================
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

COMMIT;
