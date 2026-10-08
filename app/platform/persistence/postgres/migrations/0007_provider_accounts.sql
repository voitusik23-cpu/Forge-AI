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
