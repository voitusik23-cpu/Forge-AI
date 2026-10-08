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
        ON DELETE RESTRICT
);

CREATE INDEX api_keys_organization_idx ON api_keys (organization_id);
CREATE INDEX api_keys_organization_status_idx
    ON api_keys (organization_id, status);
CREATE INDEX api_keys_created_by_user_idx ON api_keys (created_by_user_id);
