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
