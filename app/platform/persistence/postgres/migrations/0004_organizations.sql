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
