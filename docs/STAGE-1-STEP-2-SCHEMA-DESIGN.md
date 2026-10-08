# Forge AI — Stage 1 / Step 2: PostgreSQL Schema and RLS Design

> **Status of this document.** This is a **design contract for a future
> implementation task**. It contains no SQL migration, no ORM model, no
> repository, no connector, and no production policy. It fixes the *shape* the
> implementation must have so that the next task is mechanical rather than
> exploratory.
>
> **Authority.** `docs/STAGE-1-ARCHITECTURE-CONTRACT.md` remains normative. If
> this document disagrees with it, the contract wins and this document is wrong.
> Where this document proposes something the contract has not decided, it is
> marked **PROPOSED** or **OPEN** and is not binding.
>
> **Baseline.** `281e7cc` (`docs(platform): formalize system-owned provider
> accounts`), domain contracts from `2ddec41`.
>
> **Implementation status: NOT STARTED.**

---

## A. Scope

**In scope.** The persistence contract for the eight Stage 1 Platform MVP records:
`users`, `organizations`, `memberships`, `projects`, `provider_accounts`,
`api_keys`, `run_records`, `usage_records`. Column types, nullability, keys,
foreign keys, delete semantics, uniqueness, check constraints, indexes, timestamp
semantics, mutability, and the RLS design.

**Out of scope, deliberately.**

- no Billing tables — `wallets`, `credit_transactions`, `cost_records`,
  `pricing_plans`, `price_rules`, `payments`;
- no deferred entities — `Invoice`, `Partner`, `Commission`, `ResellerAccount`;
- no authentication, session, API-key generation, credential resolution, or
  Platform -> Core transport implementation;
- no change to Core. **Core must not import a database driver, an ORM, or this
  schema.** Core keeps its local file-based execution persistence.

## B. Persistence boundary

| | Platform | Core |
| --- | --- | --- |
| Durable store | PostgreSQL (this document) | local filesystem (`RunStore`, idempotency ledger, telemetry sink) |
| Owns | identity, tenancy, projects, provider references, API keys, external run lifecycle, usage observations | execution runtime, `RunScope`, `AuthorizedExecution`, run observation history, physical telemetry |
| Authority | may **refuse** a launch; never expands Core authority | the only execution authority |

The two stores are joined by exactly one value: the **Core correlation id**
(`run_records.core_run_id` ↔ the `run_id` Core emits). Nothing else crosses.

**Schema placement.** The migration and its artifacts belong to the Platform
persistence layer, which is **not** inside Core's import graph. The domain
contracts live in `app/platform/`; the persistence layer is a later artifact and
must not be imported by anything under `app/` outside that layer.

## C. Table-by-table schema contract

Types use PostgreSQL names. `uuid` is used for identifiers because the domain
contract's identifiers are opaque strings minted as UUIDv4 (`app/platform/models.py`,
`new_id()`); see **K-1** for the one part of that mapping that is still a proposed
decision. Enumerations are persisted as `text` with a `CHECK ... IN (...)`
constraint rather than as PostgreSQL `enum` types, so that adding or renaming a
member is an ordinary migration and does not require an `ALTER TYPE` on a live
table. This matters because **O-7 (RunRecord status) is still open.**

Every table carries `created_at timestamptz NOT NULL`. `updated_at timestamptz`
is nullable and is set by the server, never by a caller.

### C-1. `users`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `email` | `text` | NOT NULL | identity; length bounded by the domain contract (320) |
| `display_name` | `text` | NOT NULL | default `''` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','suspended'))`, default `'active'` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Unique:** `UNIQUE (email)`. Identity, so the scope is global, not per tenant.
- **Not tenant-owned.** A user owns nothing; there is no `organization_id` here,
  and adding one would contradict the contract.
- **No credential column.** No password, hash, session, or token: the
  authentication mechanism is **O-2, still open**.

### C-2. `organizations`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `name` | `text` | NOT NULL | |
| `slug` | `text` | NULL | empty string in the domain is stored as `NULL`; see **K-3** |
| `is_personal` | `boolean` | NOT NULL | default `false` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','suspended'))`, default `'active'` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Unique:** `UNIQUE (slug) WHERE slug IS NOT NULL` — **PROPOSED**, because the
  contract does not fix the scope of slug uniqueness (**K-3**).
- **Is the tenant boundary itself**, so it carries no `organization_id`. It is the
  only record for which that is true, and it must not be treated as a child entity.
- **No money column.** No wallet, balance, or payment-customer reference:
  `billing_account_ref` remains a future-stage field and is not created here.

### C-3. `memberships`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `user_id` | `uuid` | NOT NULL | FK -> `users(id)` |
| `role` | `text` | NOT NULL | `CHECK (role IN ('owner','admin','member','billing'))` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','inactive','revoked'))`, default `'active'` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Unique:** `UNIQUE (organization_id, user_id)` — **PROPOSED**, from the model's
  intent that a membership *links* a user to an organization (**K-2**).
- **Tenant-owned:** `organization_id` is NOT NULL, and it is the only path from a
  subject to tenant authority.
- **A role is not a permission.** Nothing maps `role` to a path, a command, a
  tool, or a network grant in this schema.

### C-4. `projects`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `name` | `text` | NOT NULL | |
| `slug` | `text` | NULL | empty string stored as `NULL` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','archived'))`, default `'active'` |
| `workspace_ref` | `text` | NULL | **opaque**; not a filesystem path |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Unique:** `UNIQUE (organization_id, slug) WHERE slug IS NOT NULL` —
  **PROPOSED (K-3)**.
- **Tenant-owned:** `organization_id` NOT NULL. A project belongs to exactly one
  organization and never directly to a user.
- **`workspace_ref` is opaque.** The schema stores a reference; it does not store a
  path, does not validate a path, and does not encode a topology. Workspace
  topology is **O-5, still open**, and Core stays cross-platform. A `CHECK` that a
  value "looks like" a path would be a defect, not a safeguard.

### C-5. `provider_accounts`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | **NULL** | FK -> `organizations(id)`; **the only nullable tenant column in the schema** |
| `provider_name` | `text` | NOT NULL | |
| `secret_ref` | `text` | NOT NULL | **opaque credential reference**, never credential material |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','disabled'))`, default `'active'` |
| `metadata` | `jsonb` | NOT NULL | default `'{}'` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Tenant-owned constraint (PROPOSED):** a partial check that ties the two
  ownership modes together, e.g. `UNIQUE (organization_id, provider_name) WHERE
  organization_id IS NOT NULL` for tenant-owned accounts, plus a separate
  `UNIQUE (provider_name) WHERE organization_id IS NULL` if at most one
  system-owned account per provider is intended. **Whether "at most one
  system-owned account per provider" is a requirement is PROPOSED (K-4).**
- **`secret_ref` never holds credential material.** The schema stores an opaque
  handle. It does not store plaintext, ciphertext, a parsed scheme, or a vendor
  identifier. Secret backend and reference grammar are **O-3 and O-4, still
  open**.

### C-6. `api_keys`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `created_by_user_id` | `uuid` | NOT NULL | FK -> `users(id)` |
| `name` | `text` | NOT NULL | |
| `key_prefix` | `text` | NULL | optional identification metadata; empty string stored as `NULL` |
| `scopes` | `text[]` | NOT NULL | default `'{}'` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','revoked'))`, default `'active'` |
| `expires_at` | `timestamptz` | NULL | |
| `revoked_at` | `timestamptz` | NULL | |
| `last_used_at` | `timestamptz` | NULL | |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Checks:** `CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))` — this is
  the domain contract's bidirectional rule and it is safe as a table constraint
  because the domain already enforces it.
- **No token column and no hash column.** The plaintext key is shown once at
  creation and only a safe representation is persisted, but the representation's
  algorithm is **O-1, still open**, so this schema does not create a field for it.
  A `key_hash` column added now would silently close O-1.
- **Uniqueness (PROPOSED):** `UNIQUE (key_prefix) WHERE key_prefix IS NOT NULL` if
  the future authentication step will look keys up by prefix. This is **K-5**,
  because the prefix format is part of O-1.
- **Authority semantics:** an API key is never filesystem or workspace authority,
  and it can never bypass membership, organization, or project authorization.
  `created_by_user_id` is recorded so that either authority model — organization
  scoped service principal, or creator-membership-derived credential — remains
  implementable. **Which model applies is R-5, still open; this schema chooses
  neither.**

### C-7. `run_records`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | **Platform identity**; primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `project_id` | `uuid` | NOT NULL | FK -> `projects(id)` |
| `core_run_id` | `text` | NULL | **Core correlation identity**; distinct from `id` |
| `initiated_by_user_id` | `uuid` | NULL | FK -> `users(id)` |
| `task_id` | `text` | NOT NULL | default `''` |
| `status` | `text` | NOT NULL | `CHECK (status IN (...))`, default `'queued'` |
| `started_at` | `timestamptz` | NULL | |
| `finished_at` | `timestamptz` | NULL | |
| `attempt_count` | `integer` | NOT NULL | default `0`, `CHECK (attempt_count >= 0)` |
| `failure_classification` | `text` | NOT NULL | default `''` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **`core_run_id` is `text`, not `uuid`.** Core mints its own correlation values
  (`run-loop-<hex>`, `run-accept-*`, `run-verify-*`, `run-api-*`) and the contract
  does not make them UUIDs. Typing it as `uuid` would invent a requirement Core
  does not satisfy.
- **`CHECK (core_run_id IS DISTINCT FROM id::text)`** — an optimization of the
  domain rule that the two identities can never collapse.
- **Lifecycle checks that follow from the domain contract:**

```text
-- queued: Core has not been reached
CHECK (status <> 'queued'
       OR (core_run_id IS NULL AND started_at IS NULL))

-- any non-queued state was reached by Core, so both must be present
CHECK (status = 'queued'
       OR (core_run_id IS NOT NULL AND started_at IS NOT NULL))

-- a finish time without a start time is incoherent
CHECK (finished_at IS NULL OR started_at IS NOT NULL)

-- a succeeded run carries no failure
CHECK (status <> 'succeeded' OR failure_classification = '')
```

- **OPEN, not frozen:** which statuses are terminal, whether a terminal status
  requires `finished_at`, and whether `finished_at` may be set for a non-terminal
  status. These are **O-7** questions. This design deliberately does **not**
  constrain them, so no migration is needed when O-7 is decided. The status member
  list is persisted as a `CHECK IN (...)` list matching the current domain enum; a
  change to that list is an ordinary migration.
- **What this table is not:** not Core authorization, not Core execution
  authority, not a replacement for the Core runtime run, and not a replacement for
  `RunStore`. It owns the outer lifecycle and nothing about phases, decisions,
  tools, or workspace activity.

### C-8. `usage_records`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `run_record_id` | `uuid` | NOT NULL | FK -> `run_records(id)` |
| `core_run_id` | `text` | NOT NULL | Core correlation identity |
| `attempt_number` | `integer` | NOT NULL | `CHECK (attempt_number >= 0)` |
| `provider_name` | `text` | NOT NULL | default `''` |
| `model_name` | `text` | NOT NULL | default `''` |
| `input_tokens` | `bigint` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `output_tokens` | `bigint` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `cached_tokens` | `bigint` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `duration_seconds` | `double precision` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `success` | `boolean` | NOT NULL | default `false` |
| `fallback` | `boolean` | NOT NULL | default `false` |
| `tool_call_count` | `integer` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `completed_at` | `timestamptz` | NULL | |
| `error_type` | `text` | NOT NULL | default `''` |
| `created_at` | `timestamptz` | NOT NULL | |

- **Primary key:** `id`.
- **The contract-level uniqueness, and only it:**
  `UNIQUE (run_record_id, attempt_number)` (O-8, resolved at contract level in
  R-1). Additional dimensions are a **separate decision**, never a silent widening.
- **Three identities, never one.** `run_record_id` and `core_run_id` are separate
  columns and there is **no `run_id` column**; an ambiguous single field is exactly
  what R-1 forbids.
- **Counters are integers, never floats.** `input_tokens`, `output_tokens`,
  `cached_tokens`, and `tool_call_count` are `bigint`/`integer` with a non-negative
  check. A `bigint` is far beyond any real measurement and matches the domain
  contract's bounded counters.
- **`duration_seconds` is a measurement, not money.** It is
  `double precision` with `CHECK (duration_seconds >= 0)`. It is never a currency
  value, and **no financial column exists in this table**: no cost, price, charge,
  balance, margin, or revenue. `usage_records` is a physical-usage observation and
  must never become a cost record.
- **`completed_at` is a plain timestamp.** It is not a billing period or an
  accounting date; there is no financial timestamp anywhere in this schema.
- **Immutability:** a usage record is an observation. It is inserted once and not
  updated; corrections are new records, and the uniqueness key prevents a silent
  overwrite of an attempt's measurement.

## D. Foreign keys and delete semantics

| Child column | Parent | On delete | Rationale |
| --- | --- | --- | --- |
| `memberships.organization_id` | `organizations.id` | `ON DELETE CASCADE` | a membership has no meaning without its tenant |
| `memberships.user_id` | `users.id` | `ON DELETE CASCADE` | a membership has no meaning without its user |
| `projects.organization_id` | `organizations.id` | `ON DELETE CASCADE` | a project is owned by the tenant |
| `provider_accounts.organization_id` | `organizations.id` | `ON DELETE CASCADE` | a tenant-owned credential reference dies with the tenant. **A system-owned row has `NULL` and is unaffected by the delete** |
| `api_keys.organization_id` | `organizations.id` | `ON DELETE CASCADE` | a key belongs to exactly one tenant |
| `api_keys.created_by_user_id` | `users.id` | **`ON DELETE RESTRICT`** — **PROPOSED (K-6)** | deleting a user must not silently orphan the provenance of a live key |
| `run_records.organization_id` | `organizations.id` | `ON DELETE CASCADE` | tenant ownership |
| `run_records.project_id` | `projects.id` | `ON DELETE RESTRICT` — **PROPOSED (K-6)** | a project with run history is not silently deletable |
| `run_records.initiated_by_user_id` | `users.id` | `ON DELETE SET NULL` — **PROPOSED (K-6)** | audit provenance is preserved as absent rather than blocking |
| `usage_records.organization_id` | `organizations.id` | `ON DELETE CASCADE` | tenant ownership |
| `usage_records.run_record_id` | `run_records.id` | `ON DELETE CASCADE` | an observation has no meaning without its run |

**Cross-tenant reference containment.** These foreign keys are necessary but **not
sufficient**: they prove each parent exists, not that the parents agree on the
tenant. Two child-parent pairs can each be valid while spanning two organizations.
Two mechanisms close that, and both are **PROPOSED** rather than frozen, because
neither is a single-column constraint:

1. **Composite foreign keys (preferred).** Add a redundant `UNIQUE (id,
   organization_id)` on `projects` and `run_records`, then point the child at the
   pair: `run_records (project_id, organization_id) REFERENCES projects (id,
   organization_id)` and `usage_records (run_record_id, organization_id)
   REFERENCES run_records (id, organization_id)`. This makes a cross-tenant
   reference structurally impossible rather than merely checked.
2. **Trigger-based validation** as a fallback where composite keys are impractical.

Mechanism 1 is recommended; mechanism 2 is recorded as an alternative. Choosing
between them is **K-7**, and it is the single most important schema decision for
tenant containment, so it must be accepted explicitly rather than assumed.

## E. Tenant containment

| Table | Tenant-owned | `organization_id` |
| --- | --- | --- |
| `users` | no — global identity | absent by design |
| `organizations` | it *is* the tenant boundary | absent by design |
| `memberships` | yes | NOT NULL |
| `projects` | yes | NOT NULL |
| `provider_accounts` | **conditional** | NULL only for a system-owned row |
| `api_keys` | yes | NOT NULL |
| `run_records` | yes | NOT NULL |
| `usage_records` | yes | NOT NULL |

Containment is defence-in-depth, in three layers, exactly as the contract requires:

```text
application authorization
  + explicit organization_id containment on tenant-owned rows
  + explicit exclusion of system-owned rows from tenant-scoped reads
  + PostgreSQL RLS (section I)
```

**A null tenant column is never a wildcard.** No query, no policy, and no
constraint in this design treats `NULL` as "any organization". Section I states how
that is enforced.

## F. ProviderAccount ownership modes

D-PLATFORM-16 is persisted as follows.

| Ownership mode | `organization_id` | Reached how |
| --- | --- | --- |
| tenant-owned (BYOK) | NOT NULL | ordinary tenant-scoped read within that organization |
| system-owned (Forge-managed) | NULL | only through an explicitly server-authorized path, if one is ever defined |

- **`NULL` is not a wildcard.** The tenant policy in section I compares
  `organization_id = current_tenant()`, which is never true for `NULL`; the design
  additionally states the exclusion explicitly so it does not depend on SQL's
  three-valued logic being read correctly by a future author.
- **Clients cannot manufacture system ownership.** Ownership mode is a server-side
  fact. No request field selects it, and an insert path that accepts a caller's
  `organization_id` — including a caller-chosen `NULL` — is forbidden.
- **System-owned rows are outside tenant ownership** and do not appear in
  tenant-scoped reads.
- **System ownership grants no execution authority.** A `provider_accounts` row of
  either mode is a credential reference; it is not a permission.
- **`secret_ref` stays opaque.** The column stores a handle, never credential
  material, and the schema encodes no reference grammar (**O-3, O-4 open**).

**Constraints that protect the semantics (PROPOSED, K-4):**

- a partial unique index for tenant-owned rows: `UNIQUE (organization_id,
  provider_name) WHERE organization_id IS NOT NULL`;
- a partial unique index for system-owned rows: `UNIQUE (provider_name) WHERE
  organization_id IS NULL`, if at most one system-owned account per provider is
  intended;
- a **read path rule**, not a constraint: every tenant-scoped query is written as
  `organization_id = current_tenant()` and never as `organization_id IS NULL OR
  organization_id = current_tenant()`.

## G. RunRecord / UsageRecord identity and uniqueness

```text
run_records.id            Platform identity            (primary key)
run_records.core_run_id   Core correlation identity    (text, distinct from id)
usage_records.run_record_id   -> run_records.id
usage_records.core_run_id     the Core correlation identity, carried forward
usage_records.attempt_number  the physical attempt
```

- **The bridge is one-way.** Core emits a physical measurement carrying
  `run_id`; Platform resolves that value to a `run_records` row by `core_run_id`,
  then inserts a `usage_records` row carrying `run_record_id`, `core_run_id`, and
  `attempt_number`. Platform never writes back into Core.
- **`UNIQUE (run_record_id, attempt_number)`** is the only contract-level
  uniqueness. It states that one physical attempt of one Platform run has one
  usage record.
- **`core_run_id` uniqueness is not fixed here.** Whether one Core correlation id
  may appear on more than one `run_records` row (for example across a resume or a
  retry) is not stated by the contract, so this design leaves it unconstrained and
  records it as **K-8 / OPEN**, noting that a `UNIQUE (core_run_id)` partial index
  is the natural choice if the implementation wants one.
- **Race and idempotency at the schema level:**
  - `UNIQUE (run_record_id, attempt_number)` makes a duplicate measurement a
    constraint violation rather than a silent second row, so a retrying writer
    fails loudly. The writer is expected to treat that as "already recorded".
  - The `run_records` lifecycle checks prevent a queued row from carrying a
    correlation id, and prevent a non-queued row from lacking one, so a partial
    write cannot leave a run claiming to be running with no Core identity.
  - `UNIQUE (organization_id, user_id)` on memberships prevents a duplicate
    authorization link.
  - There is **no** distributed claim, lease, or heartbeat in this schema. Those
    belong to the run-lifecycle design (**R-6**) and the mechanism is left open.

## H. Index strategy

Required indexes, beyond primary keys and the unique constraints already listed:

| Index | Table | Purpose |
| --- | --- | --- |
| `(organization_id)` | `memberships`, `projects`, `api_keys`, `run_records`, `usage_records` | the tenant column of every tenant-scoped read |
| `(user_id)` | `memberships` | resolve a subject's memberships |
| `(organization_id, status)` | `projects`, `api_keys`, `run_records` | listing active resources within a tenant |
| `(project_id, created_at DESC)` | `run_records` | a project's run history, newest first |
| `(run_record_id)` | `usage_records` | reach a run's measurements (the unique key already covers it; kept explicit for intent) |
| `UNIQUE (core_run_id) WHERE core_run_id IS NOT NULL` | `run_records` | **PROPOSED (K-8)** — correlation lookup |
| `(organization_id, provider_name) WHERE organization_id IS NOT NULL` | `provider_accounts` | tenant-owned provider accounts |
| `UNIQUE (provider_name) WHERE organization_id IS NULL` | `provider_accounts` | **PROPOSED (K-4)** — system-owned accounts |
| `GIN (scopes)` | `api_keys` | **PROPOSED (K-9)** — only if scope membership must be queried |
| `GIN (metadata jsonb_path_ops)` | `provider_accounts` | **PROPOSED** — only if metadata is ever queried |

The partial indexes on `provider_accounts` are not an optimization: they are the
schema-level expression of the two ownership modes, so that the semantics cannot be
erased by a later careless query.

## I. RLS design

**Status: PROPOSED. O-9 remains OPEN.** This is a design to be approved, not a
decision that has been taken. Nothing here is implemented, and no policy exists in
any database.

### I-1. How tenant context is conveyed

The tenant is conveyed to the database **out of band**, never as a query
parameter that a caller controls:

- the server opens a transaction and sets a session-local variable, e.g.
  `SET LOCAL forge.organization_id = '<uuid>'`, and optionally
  `SET LOCAL forge.user_id = '<uuid>'` for audit;
- `SET LOCAL` scopes the value to the transaction, so it cannot leak across a
  pooled connection;
- the value comes from the **server's** resolution of authenticated subject ->
  membership -> organization. It is never read from a request body, a query
  string, a header, or a model-supplied field.

**The client never supplies the RLS context.** A request may carry an
`organization_id` as a *claim*; the server compares that claim to the resolved
membership and rejects a mismatch. The database policy does not trust the claim,
because it never sees it.

### I-2. Fail-closed behaviour with no tenant context

If the transaction did not set a tenant, every policy must deny. The design
avoids relying on the subtlety that `NULL = NULL` is `NULL` and therefore not
`true`; it states the condition explicitly:

```sql
-- shape, not literal policy text
current_setting('forge.organization_id', true) IS NOT NULL
AND organization_id = current_setting('forge.organization_id')::uuid
```

The first conjunct makes the intent auditable: no tenant context, no rows. This
is the fail-closed requirement, and it must hold for every operation, not only
`SELECT`.

### I-3. Policies per tenant-owned table

For `memberships`, `projects`, `api_keys`, `run_records`, and `usage_records`:

| Operation | Policy |
| --- | --- |
| `SELECT` | `USING` the containment predicate |
| `INSERT` | `WITH CHECK` the containment predicate, so a row cannot be written into another tenant |
| `UPDATE` | `USING` **and** `WITH CHECK` the predicate, so a row cannot be moved between tenants |
| `DELETE` | `USING` the predicate |

Applying the predicate to `WITH CHECK` on `INSERT`/`UPDATE` is what prevents a
cross-tenant write; a `USING`-only policy would filter reads while still allowing
writes into another tenant.

### I-4. `provider_accounts`: the one exception

Two ownership modes need two rules, and the tenant rule must **exclude** the
system-owned rows rather than include them:

- **tenant policy:** `USING`/`WITH CHECK` the containment predicate. Because the
  predicate is an equality against a concrete tenant, a `NULL` row can never
  satisfy it. Tenant-scoped reads therefore exclude system-owned rows
  structurally, not incidentally.
- **system-owned rows:** reachable only through a separate, **server-only** role
  with its own policy, and only if such a path is ever defined. A tenant role has
  no policy granting it `organization_id IS NULL` rows.
- **forbidden patterns, stated so they cannot be reintroduced:** no policy may use
  `organization_id IS NULL OR organization_id = current_tenant()`, and no policy
  may use `current_tenant() IS NULL OR ...` as a way to mean "all tenants".

### I-5. `users` and `organizations`

Neither is tenant-owned, so an `organization_id` predicate does not apply.

- `organizations`: a subject may only see organizations it holds an `ACTIVE`
  membership in — expressed as a policy that joins `memberships` and requires an
  active membership, not a free read.
- `users`: a subject may see itself, and other users only through a shared
  organization. A blanket `SELECT` on `users` would be a cross-tenant leak of
  identities, so it is not permitted by this design.

### I-6. Server-only operations

RLS is a backstop, not the authorization layer. The following remain
**server-only** and must never be exposed as a caller-influenced query:

- creating an organization and its first `OWNER` membership;
- creating or changing a membership, and therefore any role;
- choosing the ownership mode of a `provider_accounts` row, in either direction;
- reading system-owned `provider_accounts` rows;
- setting `run_records.core_run_id`, `started_at`, `finished_at`, and
  `attempt_count`, which are written from Core's reported progress, never by a
  client;
- inserting `usage_records`, which are derived from Core's physical measurement.

### I-7. What RLS must not be mistaken for

> **RLS does not replace application authorization.**

The chain `identity != authorization != execution authority` is unchanged by any
policy in this document. A database policy decides which **rows** a session may
touch. It does not decide whether a subject may launch a run, does not grant a
role, does not expand a workspace, and does not reach the Core execution authority
chain, where `AuthorizedExecution` remains the only authority. A row being visible
is not a permission, and a `SELECT` succeeding is not an authorization.

## J. Fail-closed requirements

Stated as requirements the implementation must satisfy, each independently
checkable:

1. **No tenant context, no rows.** Every tenant policy denies when the context is
   unset. Missing context is a denial, never an unfiltered read.
2. **`NULL` is never a wildcard.** No query, policy, or constraint in this schema
   treats `organization_id IS NULL` as "any tenant".
3. **Writes are contained, not only reads.** `INSERT` and `UPDATE` carry
   `WITH CHECK`, so a row cannot be created in, or moved into, another tenant.
4. **System-owned rows require a separate authorization path.** They are never a
   side effect of a tenant query.
5. **Ownership mode is server-assigned.** No client input selects it, and a
   client-supplied `NULL` cannot create a system-owned row.
6. **Referential integrity implies tenant agreement**, via the composite keys of
   section D (mechanism **K-7**) or an equivalent check. A foreign key alone does
   not.
7. **The measurement key is enforced by the database.**
   `UNIQUE (run_record_id, attempt_number)` is a constraint, so a duplicate
   measurement fails rather than accumulating.
8. **Credential material never enters a row.** `secret_ref` is a handle; no
   column in this schema can hold a plaintext credential.
9. **No financial column exists.** Nothing in this schema can be mistaken for a
   billing record, and Billing remains a later stage.

## K. Open decisions and proposed decisions

### Still OPEN — unchanged by this document

| # | Decision | Status |
| --- | --- | --- |
| O-1 | Exact API key format | **OPEN** — no token or hash column is created |
| O-2 | Exact session / authentication mechanism | **OPEN** |
| O-3 | Exact secret backend / KMS or vault vendor | **OPEN** |
| O-4 | Exact `secret_ref` reference grammar | **OPEN** |
| O-5 | Exact workspace filesystem topology | **OPEN** — `workspace_ref` stays opaque |
| O-6 | Exact Platform -> Core transport | **OPEN** |
| O-7 | Exact `RunRecord` status enum | **OPEN** — the design constrains only what the domain already fixes |
| O-9 | Exact RLS implementation | **OPEN** — section I is a **PROPOSED** design |
| O-10..O-13 | pricing source, payment processor, tax/invoice, reseller economics | **deferred** |

O-8 remains **resolved at contract level**: `UNIQUE (run_record_id,
attempt_number)`.

### PROPOSED — require an explicit decision before implementation

| # | Proposal | Why it is not frozen |
| --- | --- | --- |
| K-1 | Identifiers are `uuid`, application-generated (matching `new_id()`), with the database not generating them | the contract fixes an opaque string, not a DB representation |
| K-2 | `UNIQUE (organization_id, user_id)` on `memberships` | the model implies one link per pair; the contract does not state it |
| K-3 | `slug` uniqueness: global for organizations, per-organization for projects, both partial on `NOT NULL` | the contract lists `slug` as a key field but fixes no scope |
| K-4 | `provider_accounts` partial unique indexes, including whether at most one system-owned account per provider is required | D-PLATFORM-16 fixes the ownership modes, not their uniqueness |
| K-5 | `UNIQUE (key_prefix) WHERE key_prefix IS NOT NULL` | the prefix format is part of O-1 |
| K-6 | `ON DELETE RESTRICT` for `api_keys.created_by_user_id` and `run_records.project_id`; `SET NULL` for `run_records.initiated_by_user_id` | delete semantics are not stated by the contract |
| K-7 | Composite foreign keys to make cross-tenant references structurally impossible | the mechanism is a design choice with a real cost |
| K-8 | `UNIQUE (core_run_id) WHERE core_run_id IS NOT NULL` | the contract does not fix correlation-id uniqueness |
| K-9 | `text[]` for `scopes` plus a GIN index, if scope membership is ever queried | representation is a design choice |
| K-10 | Nullable `updated_at` set by the server; `created_at` immutable | the domain records timestamps; the mutability contract is a design choice |

## L. Explicitly deferred items

- **Billing tables and any financial column:** `wallets`, `credit_transactions`,
  `cost_records`, `pricing_plans`, `price_rules`, `payments`, and every money
  field. A separate architecture gate is required.
- **Deferred entities:** `Invoice`, `Partner`, `Commission`, `ResellerAccount`,
  MLM, API reseller, KeyCore-Hub integration.
- **Authentication and session storage**, API-key generation and hashing,
  credential resolution, and the Platform -> Core port.
- **Run-lifecycle mechanism** (lease, heartbeat, timeout, or reconciliation) for
  detecting a lost worker (R-6).
- **Data retention, archival, partitioning, and PII strategy.**
- **The migration tool.** No ORM, migration framework, or driver is chosen here.
- **Platform -> Core measurement ingestion path**, beyond the one-way bridge
  described in section G.

## M. Implementation checklist for the next task

1. Accept or amend K-1 … K-10 explicitly; do not implement a proposal silently.
2. Choose the migration tool and register it **outside** Core's import graph.
3. Create the eight tables with the columns, nullability, checks, and indexes of
   sections C and H.
4. Add the foreign keys and the chosen cross-tenant containment mechanism
   (K-7), and prove with a test that a cross-tenant child-parent pair is refused.
5. Add `UNIQUE (run_record_id, attempt_number)` on `usage_records` and prove a
   duplicate measurement is refused.
6. Prove the `run_records` lifecycle checks: a queued row cannot carry
   `core_run_id`/`started_at`, a non-queued row must carry both, and
   `core_run_id` can never equal `id`.
7. Prove `provider_accounts` accepts both ownership modes and that a
   `NULL` row is not returned by a tenant-scoped query.
8. Implement the agreed RLS design (section I) **or** record the decision that
   defers it, and in either case prove the fail-closed requirements of section J.
9. Prove that no column in this schema can hold credential material or money.
10. Prove Core still imports nothing from the Platform layer, and that no
    database dependency entered Core.
11. Keep O-1 … O-9 open unless a decision record closes one of them.

---

## STATUS

```
Stage 0.1  =  FROZEN / GO

Stage 1    =  ARCHITECTURE CONTRACT READY
Step 1     =  DOMAIN CONTRACTS READY
Step 2     =  SCHEMA AND RLS DESIGN READY (design only)

Implementation status:  NOT STARTED
```

## OPEN DECISIONS

**Open:** O-1 API key format; O-2 session/auth mechanism; O-3 secret backend/KMS;
O-4 `secret_ref` grammar; O-5 workspace topology; O-6 Platform -> Core transport;
O-7 `RunRecord` status enum; O-9 exact RLS implementation (section I is a proposed
design).

**Resolved for contract:** O-8 — `UNIQUE (run_record_id, attempt_number)`.

**Proposed, awaiting an explicit decision:** K-1 … K-10 (section K).

**Deferred:** O-10 provider pricing source; O-11 payment processor; O-12
tax/invoice; O-13 reseller/partner economics; all Billing and deferred entities.

---

# Forge AI — Stage 1 / Шаг 2: схема PostgreSQL и дизайн RLS — русская версия

> **Статус документа.** Это **дизайн-контракт для будущей задачи реализации**. Он
> не содержит SQL-миграции, ORM-модели, репозитория, коннектора и production-политик.
> Он фиксирует *форму*, которую реализация обязана иметь, чтобы следующая задача была
> механической, а не исследовательской.
>
> **Authority.** `docs/STAGE-1-ARCHITECTURE-CONTRACT.md` остаётся нормативным. При
> расхождении побеждает контракт, а этот документ неверен. Там, где документ
> предлагает то, что контракт не решил, стоит пометка **PROPOSED** или **OPEN**, и
> это не обязательно к исполнению.
>
> **Базовая точка.** `281e7cc` (`docs(platform): formalize system-owned provider
> accounts`), доменные контракты из `2ddec41`.
>
> **Статус реализации: НЕ НАЧАТА.**

## A. Область

**В области.** Контракт персистентности для восьми записей Platform MVP Stage 1:
`users`, `organizations`, `memberships`, `projects`, `provider_accounts`, `api_keys`,
`run_records`, `usage_records`. Типы колонок, nullability, ключи, внешние ключи,
семантика удаления, уникальность, CHECK-ограничения, индексы, семантика таймстемпов,
изменяемость и дизайн RLS.

**Вне области, намеренно.**

- никаких Billing-таблиц — `wallets`, `credit_transactions`, `cost_records`,
  `pricing_plans`, `price_rules`, `payments`;
- никаких отложенных сущностей — `Invoice`, `Partner`, `Commission`,
  `ResellerAccount`;
- никакой реализации аутентификации, сессий, генерации API-ключей, разрешения
  кредилов или транспорта Platform -> Core;
- никаких изменений Core. **Core не должен импортировать драйвер базы данных, ORM
  или эту схему.** Core сохраняет свою локальную файловую персистентность исполнения.

## B. Граница персистентности

| | Platform | Core |
| --- | --- | --- |
| Durable-хранилище | PostgreSQL (этот документ) | локальная файловая система (`RunStore`, журнал идемпотентности, telemetry sink) |
| Владеет | идентичностью, арендой, проектами, ссылками на провайдеров, API-ключами, внешним жизненным циклом запуска, наблюдениями потребления | runtime исполнения, `RunScope`, `AuthorizedExecution`, историей наблюдения запуска, физической телеметрией |
| Authority | может **отказать** в запуске; никогда не расширяет authority Core | единственная execution authority |

Два хранилища соединяет ровно одно значение: **корреляционная идентичность Core**
(`run_records.core_run_id` ↔ `run_id`, который испускает Core). Больше ничего не
пересекает границу.

**Размещение схемы.** Миграция и её артефакты принадлежат слою персистентности
Platform, который **не** входит в граф импортов Core. Доменные контракты живут в
`app/platform/`; слой персистентности — более поздний артефакт, и его не должен
импортировать никто вне этого слоя.

## C. Контракт схемы по таблицам

Типы — в нотации PostgreSQL. Для идентификаторов используется `uuid`, потому что
идентификаторы доменного контракта — непрозрачные строки, порождаемые как UUIDv4
(`app/platform/models.py`, `new_id()`); единственная часть этого отображения, которая
ещё остаётся предложенным решением, — **K-1**. Перечисления хранятся как `text` с
ограничением `CHECK ... IN (...)`, а не как PostgreSQL `enum`, чтобы добавление или
переименование члена было обычной миграцией и не требовало `ALTER TYPE` на живой
таблице. Это существенно, потому что **O-7 (статус `RunRecord`) всё ещё открыт.**

Каждая таблица несёт `created_at timestamptz NOT NULL`. `updated_at timestamptz`
nullable и устанавливается сервером, никогда вызывающим.

### C-1. `users`

| Колонка | Тип | Null | Примечания |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `email` | `text` | NOT NULL | идентичность; длина ограничена доменным контрактом (320) |
| `display_name` | `text` | NOT NULL | default `''` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','suspended'))`, default `'active'` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Уникальность:** `UNIQUE (email)`. Это идентичность, поэтому область глобальная, а
  не на арендатора.
- **Не tenant-owned.** Пользователь ничем не владеет; `organization_id` здесь нет, и
  его добавление противоречило бы контракту.
- **Нет колонки учётных данных.** Ни пароля, ни хэша, ни сессии, ни токена: механизм
  аутентификации — **O-2, всё ещё открыт**.

### C-2. `organizations`

| Колонка | Тип | Null | Примечания |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `name` | `text` | NOT NULL | |
| `slug` | `text` | NULL | пустая строка в домене хранится как `NULL`; см. **K-3** |
| `is_personal` | `boolean` | NOT NULL | default `false` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','suspended'))`, default `'active'` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Уникальность:** `UNIQUE (slug) WHERE slug IS NOT NULL` — **PROPOSED**, потому что
  контракт не фиксирует область уникальности slug (**K-3**).
- **Это и есть граница арендатора**, поэтому `organization_id` отсутствует. Это
  единственная запись, для которой это верно, и её нельзя трактовать как дочернюю
  сущность.
- **Нет денежной колонки.** Ни кошелька, ни баланса, ни ссылки на платёжного
  клиента: `billing_account_ref` остаётся полем будущего этапа и здесь не создаётся.

### C-3. `memberships`

| Колонка | Тип | Null | Примечания |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `user_id` | `uuid` | NOT NULL | FK -> `users(id)` |
| `role` | `text` | NOT NULL | `CHECK (role IN ('owner','admin','member','billing'))` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','inactive','revoked'))`, default `'active'` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Уникальность:** `UNIQUE (organization_id, user_id)` — **PROPOSED**, из намерения
  модели, что membership *связывает* пользователя с организацией (**K-2**).
- **Tenant-owned:** `organization_id` NOT NULL, и это единственный путь от субъекта к
  authority арендатора.
- **Роль — не разрешение.** Ничто не отображает `role` на путь, команду, инструмент
  или сетевой грант в этой схеме.

### C-4. `projects`

| Колонка | Тип | Null | Примечания |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `name` | `text` | NOT NULL | |
| `slug` | `text` | NULL | пустая строка хранится как `NULL` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','archived'))`, default `'active'` |
| `workspace_ref` | `text` | NULL | **непрозрачная** ссылка; не путь файловой системы |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Уникальность:** `UNIQUE (organization_id, slug) WHERE slug IS NOT NULL` —
  **PROPOSED (K-3)**.
- **Tenant-owned:** `organization_id` NOT NULL. Проект принадлежит ровно одной
  организации и никогда напрямую пользователю.
- **`workspace_ref` непрозрачен.** Схема хранит ссылку; она не хранит путь, не
  валидирует путь и не кодирует топологию. Топология workspace — **O-5, всё ещё
  открыт**, и Core остаётся кросс-платформенным. `CHECK` на то, что значение «похоже
  на путь», был бы дефектом, а не защитой.

### C-5. `provider_accounts`

| Колонка | Тип | Null | Примечания |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | **NULL** | FK -> `organizations(id)`; **единственная nullable tenant-колонка в схеме** |
| `provider_name` | `text` | NOT NULL | |
| `secret_ref` | `text` | NOT NULL | **непрозрачная ссылка на кредил**, никогда не материал кредила |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','disabled'))`, default `'active'` |
| `metadata` | `jsonb` | NOT NULL | default `'{}'` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Ограничение tenant-owned (PROPOSED):** частичное ограничение, связывающее два
  режима владения, например `UNIQUE (organization_id, provider_name) WHERE
  organization_id IS NOT NULL` для tenant-owned аккаунтов, плюс отдельное
  `UNIQUE (provider_name) WHERE organization_id IS NULL`, если предполагается не
  более одного system-owned аккаунта на провайдера. **Является ли «не более одного
  system-owned аккаунта на провайдера» требованием — это PROPOSED (K-4).**
- **`secret_ref` никогда не хранит материал кредила.** Схема хранит непрозрачный
  хэндл. Она не хранит plaintext, шифротекст, разобранную схему или идентификатор
  вендора. Бэкенд секретов и грамматика ссылки — **O-3 и O-4, всё ещё открыты**.

### C-6. `api_keys`

| Колонка | Тип | Null | Примечания |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `created_by_user_id` | `uuid` | NOT NULL | FK -> `users(id)` |
| `name` | `text` | NOT NULL | |
| `key_prefix` | `text` | NULL | опциональные идентификационные метаданные; пустая строка хранится как `NULL` |
| `scopes` | `text[]` | NOT NULL | default `'{}'` |
| `status` | `text` | NOT NULL | `CHECK (status IN ('active','revoked'))`, default `'active'` |
| `expires_at` | `timestamptz` | NULL | |
| `revoked_at` | `timestamptz` | NULL | |
| `last_used_at` | `timestamptz` | NULL | |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **Checks:** `CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))` — это
  двунаправленное правило доменного контракта, и оно безопасно как табличное
  ограничение, потому что домен уже его обеспечивает.
- **Нет колонки токена и нет колонки хэша.** Plaintext-ключ показывается один раз при
  создании, и персистится только безопасное представление, но алгоритм
  представления — **O-1, всё ещё открыт**, поэтому схема не создаёт для него поля.
  Колонка `key_hash`, добавленная сейчас, молча закрыла бы O-1.
- **Уникальность (PROPOSED):** `UNIQUE (key_prefix) WHERE key_prefix IS NOT NULL`, если
  будущий шаг аутентификации будет искать ключи по префиксу. Это **K-5**, потому что
  формат префикса — часть O-1.
- **Семантика authority:** API-ключ никогда не является authority на filesystem или
  workspace и никогда не может обойти авторизацию membership, organization или
  project. `created_by_user_id` записан, чтобы обе модели authority — сервисный
  принципал уровня Organization либо кредил, выведенный из membership создателя, —
  оставались реализуемыми. **Какая модель применяется — это R-5, всё ещё открыт;
  эта схема не выбирает ни одну.**

### C-7. `run_records`

| Колонка | Тип | Null | Примечания |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | **идентичность Platform**; primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `project_id` | `uuid` | NOT NULL | FK -> `projects(id)` |
| `core_run_id` | `text` | NULL | **корреляционная идентичность Core**; отлична от `id` |
| `initiated_by_user_id` | `uuid` | NULL | FK -> `users(id)` |
| `task_id` | `text` | NOT NULL | default `''` |
| `status` | `text` | NOT NULL | `CHECK (status IN (...))`, default `'queued'` |
| `started_at` | `timestamptz` | NULL | |
| `finished_at` | `timestamptz` | NULL | |
| `attempt_count` | `integer` | NOT NULL | default `0`, `CHECK (attempt_count >= 0)` |
| `failure_classification` | `text` | NOT NULL | default `''` |
| `created_at` | `timestamptz` | NOT NULL | |
| `updated_at` | `timestamptz` | NULL | |

- **Primary key:** `id`.
- **`core_run_id` — `text`, не `uuid`.** Core порождает свои корреляционные значения
  (`run-loop-<hex>`, `run-accept-*`, `run-verify-*`, `run-api-*`), и контракт не делает
  их UUID. Тип `uuid` изобрёл бы требование, которому Core не удовлетворяет.
- **`CHECK (core_run_id IS DISTINCT FROM id::text)`** — оптимизация доменного правила,
  что две идентичности никогда не могут схлопнуться.
- **Проверки жизненного цикла, следующие из доменного контракта:**

```text
-- queued: Core ещё не был достигнут
CHECK (status <> 'queued'
       OR (core_run_id IS NULL AND started_at IS NULL))

-- любое не-queued состояние достигнуто через Core, поэтому оба поля обязательны
CHECK (status = 'queued'
       OR (core_run_id IS NOT NULL AND started_at IS NOT NULL))

-- время завершения без времени старта бессвязно
CHECK (finished_at IS NULL OR started_at IS NOT NULL)

-- успешный запуск не несёт отказа
CHECK (status <> 'succeeded' OR failure_classification = '')
```

- **OPEN, не заморожено:** какие статусы терминальны, требует ли терминальный статус
  `finished_at` и может ли `finished_at` быть установлен для нетерминального статуса.
  Это вопросы **O-7**. Дизайн намеренно **не** ограничивает их, чтобы при решении O-7
  не потребовалась миграция. Список членов статуса хранится как `CHECK IN (...)`,
  соответствующий текущему доменному enum; изменение этого списка — обычная миграция.
- **Чем эта таблица не является:** не authorization для Core, не execution authority
  Core, не замена runtime-запуска Core и не замена `RunStore`. Она владеет внешним
  жизненным циклом и ничем о фазах, решениях, инструментах или активности workspace.

### C-8. `usage_records`

| Колонка | Тип | Null | Примечания |
| --- | --- | --- | --- |
| `id` | `uuid` | NOT NULL | primary key |
| `organization_id` | `uuid` | NOT NULL | FK -> `organizations(id)` |
| `run_record_id` | `uuid` | NOT NULL | FK -> `run_records(id)` |
| `core_run_id` | `text` | NOT NULL | корреляционная идентичность Core |
| `attempt_number` | `integer` | NOT NULL | `CHECK (attempt_number >= 0)` |
| `provider_name` | `text` | NOT NULL | default `''` |
| `model_name` | `text` | NOT NULL | default `''` |
| `input_tokens` | `bigint` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `output_tokens` | `bigint` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `cached_tokens` | `bigint` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `duration_seconds` | `double precision` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `success` | `boolean` | NOT NULL | default `false` |
| `fallback` | `boolean` | NOT NULL | default `false` |
| `tool_call_count` | `integer` | NOT NULL | default `0`, `CHECK (>= 0)` |
| `completed_at` | `timestamptz` | NULL | |
| `error_type` | `text` | NOT NULL | default `''` |
| `created_at` | `timestamptz` | NOT NULL | |

- **Primary key:** `id`.
- **Уникальность уровня контракта, и только она:**
  `UNIQUE (run_record_id, attempt_number)` (O-8, решён на уровне контракта в R-1).
  Дополнительные измерения — **отдельное решение**, а не молчаливое расширение.
- **Три идентичности, никогда одна.** `run_record_id` и `core_run_id` — отдельные
  колонки, и **колонки `run_id` нет**; неоднозначное единое поле — именно то, что
  запрещает R-1.
- **Счётчики — целые, никогда не float.** `input_tokens`, `output_tokens`,
  `cached_tokens` и `tool_call_count` — `bigint`/`integer` с проверкой
  неотрицательности. `bigint` намного превышает любое реальное измерение и
  соответствует ограниченным счётчикам доменного контракта.
- **`duration_seconds` — измерение, а не деньги.** Это `double precision` с
  `CHECK (duration_seconds >= 0)`. Это никогда не денежная величина, и **в этой
  таблице нет ни одной финансовой колонки**: ни cost, ни price, ни charge, ни
  balance, ни margin, ни revenue. `usage_records` — наблюдение физического
  потребления и никогда не должно становиться записью о себестоимости.
- **`completed_at` — обычный таймстемп.** Это не биллинговый период и не
  бухгалтерская дата; финансовых таймстемпов в этой схеме нет.
- **Неизменяемость:** запись потребления — наблюдение. Она вставляется один раз и не
  обновляется; исправления — новые записи, а ключ уникальности предотвращает молчаливую
  перезапись измерения попытки.

## D. Внешние ключи и семантика удаления

| Дочерняя колонка | Родитель | On delete | Обоснование |
| --- | --- | --- | --- |
| `memberships.organization_id` | `organizations.id` | `ON DELETE CASCADE` | membership не имеет смысла без своего арендатора |
| `memberships.user_id` | `users.id` | `ON DELETE CASCADE` | membership не имеет смысла без своего пользователя |
| `projects.organization_id` | `organizations.id` | `ON DELETE CASCADE` | проект принадлежит арендатору |
| `provider_accounts.organization_id` | `organizations.id` | `ON DELETE CASCADE` | tenant-owned ссылка на кредил умирает вместе с арендатором. **System-owned строка имеет `NULL` и удалением не затрагивается** |
| `api_keys.organization_id` | `organizations.id` | `ON DELETE CASCADE` | ключ принадлежит ровно одному арендатору |
| `api_keys.created_by_user_id` | `users.id` | **`ON DELETE RESTRICT`** — **PROPOSED (K-6)** | удаление пользователя не должно молча осиротить происхождение живого ключа |
| `run_records.organization_id` | `organizations.id` | `ON DELETE CASCADE` | владение арендатора |
| `run_records.project_id` | `projects.id` | `ON DELETE RESTRICT` — **PROPOSED (K-6)** | проект с историей запусков не удаляется молча |
| `run_records.initiated_by_user_id` | `users.id` | `ON DELETE SET NULL` — **PROPOSED (K-6)** | происхождение для аудита сохраняется как отсутствующее, а не блокирует |
| `usage_records.organization_id` | `organizations.id` | `ON DELETE CASCADE` | владение арендатора |
| `usage_records.run_record_id` | `run_records.id` | `ON DELETE CASCADE` | наблюдение не имеет смысла без своего запуска |

**Containment кросс-арендаторных ссылок.** Эти внешние ключи необходимы, но **не
достаточны**: они доказывают существование родителя, а не согласие родителей по
арендатору. Две пары «ребёнок-родитель» могут быть каждая валидной, охватывая при этом
две организации. Это закрывают два механизма, и оба **PROPOSED**, а не заморожены,
потому что ни один не является одноколоночным ограничением:

1. **Составные внешние ключи (предпочтительно).** Добавить избыточный
   `UNIQUE (id, organization_id)` на `projects` и `run_records`, затем направить
   ребёнка на пару: `run_records (project_id, organization_id) REFERENCES projects (id,
   organization_id)` и `usage_records (run_record_id, organization_id) REFERENCES
   run_records (id, organization_id)`. Это делает кросс-арендаторную ссылку
   структурно невозможной, а не просто проверяемой.
2. **Триггерная валидация** как запасной вариант там, где составные ключи неудобны.

Рекомендуется механизм 1; механизм 2 записан как альтернатива. Выбор между ними —
**K-7**, и это самое важное решение схемы для containment арендаторов, поэтому оно
должно быть принято явно, а не подразумеваться.

## E. Containment арендаторов

| Таблица | Tenant-owned | `organization_id` |
| --- | --- | --- |
| `users` | нет — глобальная идентичность | отсутствует по замыслу |
| `organizations` | она *и есть* граница арендатора | отсутствует по замыслу |
| `memberships` | да | NOT NULL |
| `projects` | да | NOT NULL |
| `provider_accounts` | **условно** | NULL только для system-owned строки |
| `api_keys` | да | NOT NULL |
| `run_records` | да | NOT NULL |
| `usage_records` | да | NOT NULL |

Containment — это defense-in-depth в трёх слоях, ровно как требует контракт:

```text
авторизация на уровне приложения
  + явный containment organization_id на tenant-owned строках
  + явное исключение system-owned строк из tenant-scoped чтений
  + PostgreSQL RLS (раздел I)
```

**Нулевая tenant-колонка никогда не является подстановочным символом.** Ни один
запрос, ни одна политика и ни одно ограничение в этом дизайне не трактует `NULL` как
«любая организация». Как это обеспечивается, изложено в разделе I.

## F. Режимы владения ProviderAccount

D-PLATFORM-16 персистится следующим образом.

| Режим владения | `organization_id` | Как достигается |
| --- | --- | --- |
| tenant-owned (BYOK) | NOT NULL | обычное tenant-scoped чтение внутри этой организации |
| system-owned (Forge-managed) | NULL | только через явно авторизованный на сервере путь, если он когда-либо будет определён |

- **`NULL` — не подстановочный символ.** Tenant-политика в разделе I сравнивает
  `organization_id = current_tenant()`, что никогда не истинно для `NULL`; кроме
  того, исключение сформулировано явно, чтобы не зависеть от того, что будущий автор
  правильно прочитает трёхзначную логику SQL.
- **Клиенты не могут создать системное владение.** Режим владения — server-side факт.
  Ни одно поле запроса его не выбирает, и путь вставки, принимающий `organization_id`
  от вызывающего — включая выбранный вызывающим `NULL` — запрещён.
- **System-owned строки находятся вне владения арендатора** и не появляются в
  tenant-scoped чтениях.
- **Системное владение не даёт execution authority.** Строка `provider_accounts`
  любого режима — это ссылка на кредил; это не разрешение.
- **`secret_ref` остаётся непрозрачным.** Колонка хранит хэндл, никогда материал
  кредила, и схема не кодирует грамматику ссылки (**O-3, O-4 открыты**).

**Ограничения, защищающие семантику (PROPOSED, K-4):**

- частичный уникальный индекс для tenant-owned строк: `UNIQUE (organization_id,
  provider_name) WHERE organization_id IS NOT NULL`;
- частичный уникальный индекс для system-owned строк: `UNIQUE (provider_name) WHERE
  organization_id IS NULL`, если предполагается не более одного system-owned аккаунта
  на провайдера;
- **правило пути чтения**, а не ограничение: каждый tenant-scoped запрос пишется как
  `organization_id = current_tenant()` и никогда как `organization_id IS NULL OR
  organization_id = current_tenant()`.

## G. Идентичность и уникальность RunRecord / UsageRecord

```text
run_records.id            идентичность Platform        (primary key)
run_records.core_run_id   корреляционная идентичность Core (text, отлична от id)
usage_records.run_record_id   -> run_records.id
usage_records.core_run_id     корреляционная идентичность Core, перенесённая дальше
usage_records.attempt_number  физическая попытка
```

- **Мост односторонний.** Core испускает физическое измерение, несущее `run_id`;
  Platform разрешает это значение в строку `run_records` по `core_run_id`, затем
  вставляет строку `usage_records`, несущую `run_record_id`, `core_run_id` и
  `attempt_number`. Platform никогда не пишет обратно в Core.
- **`UNIQUE (run_record_id, attempt_number)`** — единственная уникальность уровня
  контракта. Она утверждает, что у одной физической попытки одного запуска Platform
  одно наблюдение потребления.
- **Уникальность `core_run_id` здесь не фиксируется.** Может ли одна корреляционная
  идентичность Core появиться более чем в одной строке `run_records` (например, при
  resume или retry), контрактом не сказано, поэтому дизайн оставляет это
  неограниченным и записывает как **K-8 / OPEN**, отмечая, что частичный индекс
  `UNIQUE (core_run_id)` — естественный выбор, если реализация захочет его иметь.
- **Гонки и идемпотентность на уровне схемы:**
  - `UNIQUE (run_record_id, attempt_number)` делает дубликат измерения нарушением
    ограничения, а не молчаливой второй строкой, поэтому повторяющий писатель падает
    громко. Ожидается, что писатель трактует это как «уже записано».
  - Проверки жизненного цикла `run_records` не позволяют queued-строке нести
    корреляционную идентичность и не позволяют не-queued строке её не иметь, поэтому
    частичная запись не может оставить запуск, заявляющий, что он выполняется, без
    идентичности Core.
  - `UNIQUE (organization_id, user_id)` на memberships предотвращает дубликат связи
    авторизации.
  - В этой схеме **нет** распределённого claim, lease или heartbeat. Они относятся к
    дизайну жизненного цикла запуска (**R-6**), и механизм оставлен открытым.

## H. Стратегия индексов

Требуемые индексы, помимо первичных ключей и уже перечисленных уникальных ограничений:

| Индекс | Таблица | Назначение |
| --- | --- | --- |
| `(organization_id)` | `memberships`, `projects`, `api_keys`, `run_records`, `usage_records` | tenant-колонка каждого tenant-scoped чтения |
| `(user_id)` | `memberships` | разрешение memberships субъекта |
| `(organization_id, status)` | `projects`, `api_keys`, `run_records` | листинг активных ресурсов внутри арендатора |
| `(project_id, created_at DESC)` | `run_records` | история запусков проекта, новые первыми |
| `(run_record_id)` | `usage_records` | доступ к измерениям запуска (уникальный ключ уже покрывает; оставлен явно для намерения) |
| `UNIQUE (core_run_id) WHERE core_run_id IS NOT NULL` | `run_records` | **PROPOSED (K-8)** — поиск по корреляции |
| `(organization_id, provider_name) WHERE organization_id IS NOT NULL` | `provider_accounts` | tenant-owned провайдерские аккаунты |
| `UNIQUE (provider_name) WHERE organization_id IS NULL` | `provider_accounts` | **PROPOSED (K-4)** — system-owned аккаунты |
| `GIN (scopes)` | `api_keys` | **PROPOSED (K-9)** — только если членство в scope нужно запрашивать |
| `GIN (metadata jsonb_path_ops)` | `provider_accounts` | **PROPOSED** — только если metadata когда-либо запрашивается |

Частичные индексы на `provider_accounts` — не оптимизация: это выражение двух режимов
владения на уровне схемы, чтобы семантику нельзя было стереть небрежным запросом
позже.

## I. Дизайн RLS

**Статус: PROPOSED. O-9 остаётся OPEN.** Это дизайн для утверждения, а не принятое
решение. Ничего здесь не реализовано, и ни одной политики нет ни в какой базе данных.

### I-1. Как передаётся tenant-контекст

Арендатор передаётся в базу **внеполосно**, никогда как параметр запроса, которым
управляет вызывающий:

- сервер открывает транзакцию и устанавливает session-local переменную, например
  `SET LOCAL forge.organization_id = '<uuid>'`, и опционально
  `SET LOCAL forge.user_id = '<uuid>'` для аудита;
- `SET LOCAL` ограничивает значение транзакцией, поэтому оно не может утечь через
  пул соединений;
- значение приходит из **серверного** разрешения
  аутентифицированный субъект -> membership -> organization. Оно никогда не читается
  из тела запроса, query-строки, заголовка или поля, предложенного моделью.

**Клиент никогда не поставляет RLS-контекст.** Запрос может нести `organization_id`
как *заявку*; сервер сверяет заявку с разрешённым membership и отклоняет
несовпадение. Политика базы заявке не доверяет, потому что никогда её не видит.

### I-2. Fail-closed поведение без tenant-контекста

Если транзакция не установила арендатора, каждая политика обязана отказать. Дизайн не
полагается на тонкость, что `NULL = NULL` есть `NULL` и потому не `true`; условие
формулируется явно:

```sql
-- форма, а не буквальный текст политики
current_setting('forge.organization_id', true) IS NOT NULL
AND organization_id = current_setting('forge.organization_id')::uuid
```

Первый конъюнкт делает намерение проверяемым: нет tenant-контекста — нет строк. Это
требование fail-closed, и оно должно выполняться для каждой операции, а не только для
`SELECT`.

### I-3. Политики по tenant-owned таблицам

Для `memberships`, `projects`, `api_keys`, `run_records` и `usage_records`:

| Операция | Политика |
| --- | --- |
| `SELECT` | `USING` предикат containment |
| `INSERT` | `WITH CHECK` предикат containment, чтобы строка не могла быть записана в другого арендатора |
| `UPDATE` | `USING` **и** `WITH CHECK` предикат, чтобы строку нельзя было переместить между арендаторами |
| `DELETE` | `USING` предикат |

Применение предиката к `WITH CHECK` на `INSERT`/`UPDATE` — это то, что предотвращает
кросс-арендаторную запись; политика только с `USING` фильтровала бы чтения, но всё
ещё позволяла запись в другого арендатора.

### I-4. `provider_accounts`: единственное исключение

Два режима владения требуют двух правил, и tenant-правило должно **исключать**
system-owned строки, а не включать их:

- **tenant-политика:** `USING`/`WITH CHECK` предикат containment. Поскольку предикат —
  это равенство конкретному арендатору, `NULL`-строка никогда не может ему
  удовлетворить. Поэтому tenant-scoped чтения исключают system-owned строки
  структурно, а не случайно.
- **system-owned строки:** достижимы только через отдельную, **server-only** роль с
  собственной политикой, и только если такой путь когда-либо будет определён. У
  tenant-роли нет политики, дающей ей строки с `organization_id IS NULL`.
- **запрещённые шаблоны, зафиксированные, чтобы их нельзя было вернуть:** никакая
  политика не может использовать `organization_id IS NULL OR organization_id =
  current_tenant()`, и никакая политика не может использовать `current_tenant() IS
  NULL OR ...` как способ означать «все арендаторы».

### I-5. `users` и `organizations`

Ни одна из них не tenant-owned, поэтому предикат по `organization_id` не применим.

- `organizations`: субъект может видеть только организации, в которых у него есть
  `ACTIVE` membership — это выражается политикой, соединяющей `memberships` и
  требующей активного membership, а не свободным чтением.
- `users`: субъект может видеть себя, а других пользователей — только через общую
  организацию. Сплошной `SELECT` по `users` был бы кросс-арендаторной утечкой
  идентичностей, поэтому этим дизайном он не разрешён.

### I-6. Server-only операции

RLS — это подстраховка, а не слой авторизации. Следующее остаётся **server-only** и
никогда не должно быть доступно как запрос, на который влияет вызывающий:

- создание организации и её первого `OWNER` membership;
- создание или изменение membership, а значит и любой роли;
- выбор режима владения строки `provider_accounts` в любую сторону;
- чтение system-owned строк `provider_accounts`;
- установка `run_records.core_run_id`, `started_at`, `finished_at` и
  `attempt_count`, которые пишутся из сообщённого Core прогресса, а не клиентом;
- вставка `usage_records`, которые выводятся из физического измерения Core.

### I-7. Чем RLS не должно быть ошибочно принято

> **RLS не заменяет авторизацию приложения.**

Цепочка `identity != authorization != execution authority` не изменяется никакой
политикой этого документа. Политика базы решает, к каким **строкам** может
обращаться сессия. Она не решает, может ли субъект запустить run, не выдаёт роль, не
расширяет workspace и не достигает цепочки execution authority Core, где
`AuthorizedExecution` остаётся единственной authority. Видимость строки — не
разрешение, а успешный `SELECT` — не авторизация.

## J. Требования fail-closed

Сформулированы как требования, которые реализация обязана выполнить, и каждое
проверяемо независимо:

1. **Нет tenant-контекста — нет строк.** Каждая tenant-политика отказывает, когда
   контекст не установлен. Отсутствующий контекст — это отказ, никогда не
   нефильтрованное чтение.
2. **`NULL` никогда не подстановочный символ.** Ни один запрос, политика или
   ограничение в этой схеме не трактует `organization_id IS NULL` как «любой
   арендатор».
3. **Записи содержатся, а не только чтения.** `INSERT` и `UPDATE` несут
   `WITH CHECK`, поэтому строка не может быть создана в другом арендаторе или
   перемещена в него.
4. **System-owned строки требуют отдельного пути авторизации.** Они никогда не
   являются побочным эффектом tenant-запроса.
5. **Режим владения назначает сервер.** Ни один клиентский ввод его не выбирает, и
   присланный клиентом `NULL` не может создать system-owned строку.
6. **Ссылочная целостность подразумевает согласие по арендатору** через составные
   ключи раздела D (механизм **K-7**) или эквивалентную проверку. Одного внешнего
   ключа недостаточно.
7. **Ключ измерения обеспечен базой.** `UNIQUE (run_record_id, attempt_number)` — это
   ограничение, поэтому дубликат измерения падает, а не накапливается.
8. **Материал кредилов никогда не попадает в строку.** `secret_ref` — хэндл; ни одна
   колонка этой схемы не может хранить plaintext-кредил.
9. **Финансовой колонки не существует.** Ничто в этой схеме нельзя принять за
   биллинговую запись, и Billing остаётся более поздним этапом.

## K. Открытые и предложенные решения

### Всё ещё OPEN — не изменено этим документом

| # | Решение | Статус |
| --- | --- | --- |
| O-1 | Точный формат API key | **OPEN** — колонка токена или хэша не создаётся |
| O-2 | Точный механизм сессий / аутентификации | **OPEN** |
| O-3 | Точный бэкенд секретов / вендор KMS или vault | **OPEN** |
| O-4 | Точная грамматика ссылки `secret_ref` | **OPEN** |
| O-5 | Точная топология файловой системы workspace | **OPEN** — `workspace_ref` остаётся непрозрачным |
| O-6 | Точный транспорт Platform -> Core | **OPEN** |
| O-7 | Точный enum статусов `RunRecord` | **OPEN** — дизайн ограничивает только то, что уже фиксирует домен |
| O-9 | Точная реализация RLS | **OPEN** — раздел I это **PROPOSED** дизайн |
| O-10..O-13 | источник цен, платёжный процессор, налоги/инвойсы, экономика reseller | **отложено** |

O-8 остаётся **решённым на уровне контракта**: `UNIQUE (run_record_id,
attempt_number)`.

### PROPOSED — требуют явного решения до реализации

| # | Предложение | Почему не заморожено |
| --- | --- | --- |
| K-1 | Идентификаторы — `uuid`, порождаемые приложением (в соответствии с `new_id()`), база их не генерирует | контракт фиксирует непрозрачную строку, а не представление в БД |
| K-2 | `UNIQUE (organization_id, user_id)` на `memberships` | модель подразумевает одну связь на пару; контракт этого не утверждает |
| K-3 | Уникальность `slug`: глобальная для organizations, на организацию для projects, обе частичные по `NOT NULL` | контракт перечисляет `slug` как ключевое поле, но не фиксирует область |
| K-4 | Частичные уникальные индексы `provider_accounts`, включая вопрос, требуется ли не более одного system-owned аккаунта на провайдера | D-PLATFORM-16 фиксирует режимы владения, а не их уникальность |
| K-5 | `UNIQUE (key_prefix) WHERE key_prefix IS NOT NULL` | формат префикса — часть O-1 |
| K-6 | `ON DELETE RESTRICT` для `api_keys.created_by_user_id` и `run_records.project_id`; `SET NULL` для `run_records.initiated_by_user_id` | семантика удаления контрактом не задана |
| K-7 | Составные внешние ключи, делающие кросс-арендаторные ссылки структурно невозможными | механизм — дизайн-выбор с реальной ценой |
| K-8 | `UNIQUE (core_run_id) WHERE core_run_id IS NOT NULL` | контракт не фиксирует уникальность корреляционной идентичности |
| K-9 | `text[]` для `scopes` плюс GIN-индекс, если членство в scope когда-либо будет запрашиваться | представление — дизайн-выбор |
| K-10 | Nullable `updated_at`, устанавливаемый сервером; `created_at` неизменяем | домен записывает таймстемпы; контракт изменяемости — дизайн-выбор |

## L. Явно отложенное

- **Billing-таблицы и любая финансовая колонка:** `wallets`, `credit_transactions`,
  `cost_records`, `pricing_plans`, `price_rules`, `payments` и любое денежное поле.
  Требуется отдельный архитектурный gate.
- **Отложенные сущности:** `Invoice`, `Partner`, `Commission`, `ResellerAccount`,
  MLM, API reseller, интеграция KeyCore-Hub.
- **Аутентификация и хранение сессий**, генерация и хэширование API-ключей,
  разрешение кредилов и порт Platform -> Core.
- **Механизм жизненного цикла запуска** (lease, heartbeat, timeout или reconciliation)
  для обнаружения потерянного worker'а (R-6).
- **Хранение данных, архивирование, партиционирование и стратегия PII.**
- **Инструмент миграций.** Ни ORM, ни фреймворк миграций, ни драйвер здесь не
  выбираются.
- **Путь приёма измерений Platform -> Core**, помимо одностороннего моста из
  раздела G.

## M. Чек-лист реализации для следующей задачи

1. Принять или изменить K-1 … K-10 явно; не реализовывать предложение молча.
2. Выбрать инструмент миграций и зарегистрировать его **вне** графа импортов Core.
3. Создать восемь таблиц с колонками, nullability, проверками и индексами разделов C и H.
4. Добавить внешние ключи и выбранный механизм кросс-арендаторного containment (K-7)
   и доказать тестом, что кросс-арендаторная пара «ребёнок-родитель» отвергается.
5. Добавить `UNIQUE (run_record_id, attempt_number)` на `usage_records` и доказать,
   что дубликат измерения отвергается.
6. Доказать проверки жизненного цикла `run_records`: queued-строка не может нести
   `core_run_id`/`started_at`, не-queued обязана нести оба, а `core_run_id` никогда не
   может быть равен `id`.
7. Доказать, что `provider_accounts` принимает оба режима владения и что `NULL`-строка
   не возвращается tenant-scoped запросом.
8. Реализовать согласованный дизайн RLS (раздел I) **либо** зафиксировать решение о его
   отсрочке, и в любом случае доказать требования fail-closed раздела J.
9. Доказать, что ни одна колонка этой схемы не может хранить материал кредила или
   деньги.
10. Доказать, что Core по-прежнему не импортирует ничего из слоя Platform и что в Core
    не попала зависимость от базы данных.
11. Оставить O-1 … O-9 открытыми, если запись решения не закроет одно из них.

---

## STATUS

```
Stage 0.1  =  FROZEN / GO

Stage 1    =  ARCHITECTURE CONTRACT READY
Step 1     =  DOMAIN CONTRACTS READY
Step 2     =  SCHEMA AND RLS DESIGN READY (design only)

Implementation status:  NOT STARTED
```

## OPEN DECISIONS

**Открыты:** O-1 формат API key; O-2 механизм сессий/аутентификации; O-3 бэкенд
секретов/KMS; O-4 грамматика `secret_ref`; O-5 топология workspace; O-6 транспорт
Platform -> Core; O-7 enum статусов `RunRecord`; O-9 точная реализация RLS (раздел I —
предложенный дизайн).

**Решено для контракта:** O-8 — `UNIQUE (run_record_id, attempt_number)`.

**Предложено, ожидает явного решения:** K-1 … K-10 (раздел K).

**Отложено:** O-10 источник провайдерских цен; O-11 платёжный процессор; O-12
налоги/инвойсы; O-13 экономика reseller/partner; все Billing- и отложенные сущности.
