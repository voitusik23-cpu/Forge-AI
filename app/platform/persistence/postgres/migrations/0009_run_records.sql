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
        ON DELETE SET NULL
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
