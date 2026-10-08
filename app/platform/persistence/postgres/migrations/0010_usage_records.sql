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
