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
