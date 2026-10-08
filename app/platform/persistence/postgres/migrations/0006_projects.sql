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
