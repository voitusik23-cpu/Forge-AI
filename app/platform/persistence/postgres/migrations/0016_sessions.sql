-- 0016_sessions.sql
--
-- Platform table: sessions (Phase 7, PR-1 — session infrastructure).
--
-- An opaque session token is proved by possession: the server stores only the SHA-256 hash of the token,
-- so the database never holds a value that could be replayed. The raw token is returned once by
-- SessionService.issue() and is deliberately not a column here, for the same reason 0001_users.sql records
-- that users carries no credential column at all.
--
-- Tenancy. `users` is a GLOBAL identity and has no organization_id (0001_users.sql), so the tenant of a
-- session cannot be inherited from the subject: it is carried by this table's own organization_id, in the
-- same shape projects uses (0006_projects.sql), and it is enforced by the policies below exactly as
-- 0013_policies_tenancy.sql enforces them for the other tenant-owned tables. ENABLE alone would leave the
-- table owner exempt, which is why FORCE is applied as well — the same pairing 0011_rls_enable.sql uses for
-- every platform table.
--
-- No backfill: the table starts empty. No existing migration is touched, because already-applied migrations
-- are verified by checksum and history must not be rewritten.

CREATE TABLE sessions (
    id              uuid        NOT NULL,
    organization_id uuid        NOT NULL,
    subject_id      uuid        NOT NULL,
    token_hash      text        NOT NULL,
    issued_at       timestamptz NOT NULL,
    expires_at      timestamptz NOT NULL,
    last_seen_at    timestamptz NOT NULL,
    revoked_at      timestamptz NULL,

    CONSTRAINT sessions_pkey PRIMARY KEY (id),
    CONSTRAINT sessions_token_hash_unique UNIQUE (token_hash),
    FOREIGN KEY (organization_id) REFERENCES organizations (id),
    FOREIGN KEY (subject_id) REFERENCES users (id)
);

-- Lookup by hash is the hot path of every authenticated request: one row, one index.
CREATE UNIQUE INDEX sessions_token_hash_idx ON sessions (token_hash);

-- The tenant column, mirroring projects_organization_idx.
CREATE INDEX sessions_organization_idx ON sessions (organization_id);

-- Revoking a subject's live sessions is one indexed statement, not a scan.
CREATE INDEX sessions_subject_live_idx ON sessions (subject_id, revoked_at);

-- Row-level security, following 0011_rls_enable.sql and 0013_policies_tenancy.sql.
-- USING filters which rows are visible; WITH CHECK is what actually prevents a cross-tenant INSERT.
-- The predicate is the canonical one: platform.is_current_organization() is fail-closed when no tenant
-- context is declared, so a session row cannot be read or written outside the transaction's own tenant.
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions FORCE  ROW LEVEL SECURITY;

CREATE POLICY sessions_tenant_select ON sessions
    USING (platform.is_current_organization(organization_id));

CREATE POLICY sessions_tenant_insert ON sessions
    WITH CHECK (platform.is_current_organization(organization_id));

CREATE POLICY sessions_tenant_update ON sessions
    USING (platform.is_current_organization(organization_id))
    WITH CHECK (platform.is_current_organization(organization_id));

CREATE POLICY sessions_tenant_delete ON sessions
    USING (platform.is_current_organization(organization_id));

-- Table privileges for the application role, matching the convention used by the other
-- platform tables in this directory: the policy decides which rows, the grant decides whether
-- the role may touch the table at all. RLS stays enabled and forced above.
GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO forge_platform_app;
