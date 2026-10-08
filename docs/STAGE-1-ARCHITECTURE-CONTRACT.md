# Forge AI — Stage 1 Architecture Contract

> **Authority.** This document is **normative** for Stage 1 implementation. It binds
> the Platform work that follows. Where it disagrees with a proposal, a review, or a
> stage note, this document wins; where it disagrees with the code, the code is
> wrong only if Core behaviour described here is contradicted, otherwise the code
> wins and this document is wrong.
>
> **Scope.** Stage 1 = Platform MVP identity, tenancy, persistence, API access, and
> the Platform -> Core boundary. Billing is **out of scope** and appears here only
> as a frozen direction.
>
> **Baseline.** Frozen Stage 0 decisions `D-PLATFORM-01..12` (`db21d65`), Stage 0.1
> durable physical telemetry `D-PLATFORM-13` (`2ea8124`), Stage 0.1 final gate: GO.
>
> **Implementation status: NOT STARTED.** Nothing in this document exists in the
> repository. Nothing in this document may be described in the present tense.

---

## 1. Dependency direction

The only permitted dependency direction is downward:

```
Clients
  |
  v
API / Platform Gateway
  |
  v
Platform Control Plane  +  Billing (future)
  |
  v  Platform -> Core port (section 5)
Core
```

**Core must not import:** Platform, Billing, PostgreSQL or any database driver, an
ORM, a payment library, users/orgs/memberships, or any financial domain module.
Verified at baseline `2ea8124`: `sqlalchemy`, `psycopg`, `sqlite3`, `alembic`, and
`stripe` have **zero** imports in `app/`.

**Core must not know:** who owns an organization; a wallet balance; a model's
price; a tariff; payment state; reseller or partner state. It does not receive any
of these and has no field for them.

**Asymmetry of power.** Platform may **refuse** to start a run. Platform may **not**
expand what a run is allowed to do. The authority ceiling is Core's; Platform can
only lower it.

## 2. Platform MVP entities

Nine entities for Stage 1. No additional entity may be introduced without a
separate decision record.

| Entity | Purpose | Tenant boundary | Key fields | Authority / not authority |
| --- | --- | --- | --- | --- |
| `User` | Authenticated natural person | none (global) | `id`, `email` (unique), `password_hash` or external identity ref, `status`, `created_at` | **Is** the authentication subject. **Is not** a tenant and **is not** an execution authority |
| `Organization` | Tenant: isolation, ownership and (future) billing root | the boundary itself | `id`, `name`, `slug` (unique), `is_personal`, `billing_account_ref` (nullable, future), `referred_by_partner_id` (nullable, future), `created_at` | **Is** the isolation and ownership root. **Is not** an execution authority |
| `Membership` | Authorization link `User` <-> `Organization` with a role | belongs to Organization | `id`, `user_id`, `organization_id`, `role`, `created_at` | **Is** the only path from a subject to tenant authority. **Is not** an execution authority |
| `Project` | Unit of work inside a tenant | belongs to Organization | `id`, `organization_id`, `name`, `slug`, `status`, `created_at` | **Is** the owner of runs and workspace roots. **Is not** an execution authority |
| `ProviderAccount` | Reference to a provider credential. Has exactly two ownership modes (see section 10) | **tenant-owned**: belongs to one Organization, `organization_id` non-null. **system-owned / Forge-managed**: belongs to no Organization, `organization_id` null, never reachable through a tenant-scoped read | `id`, `organization_id` (null only for a system-owned account), `provider_name`, `secret_ref`, `enabled`, `metadata` | **Is** a credential *reference*. **Is not** a Core authority and holds **no** plaintext secret, wholesale price, or cost. Ownership mode is not authority |
| `APIKey` | Programmatic access token for one Organization | belongs to Organization | `id`, `organization_id`, `created_by_user_id`, safe representation (hash) + `prefix`, `scopes`, `expires_at`, `revoked_at`, `created_at`, `last_used_at` | **Is** an authentication credential. **Is not** a filesystem, workspace, command, or tool authority |
| `RunRecord` | Durable external lifecycle truth of one run | belongs to Project | `id`, `project_id`, `initiated_by_user_id`, `task_id`, `status`, `started_at`, `completed_at`, `core_run_id` (unique correlation id) | **Is** the external lifecycle record. **Is not** Core authorization |
| `UsageRecord` | Immutable physical measurement attached to a run | derived from RunRecord | `id`, `run_id`, `attempt_number`, `provider_name`, `model_name`, `input_tokens`, `output_tokens`, `cached_tokens`, `duration`, `request_count`, outcome metadata | **Is** a measurement. **Is not** money, price, charge, or balance |
| `ProjectMembership`-free note | Roles are carried by `Membership`; no separate role entity is introduced in Stage 1 | — | — | — |

**Billing entities — later stages, separate gate.** `Wallet`,
`CreditTransaction`, `CostRecord`, `PricingPlan`, `PriceRule`, `Payment`. These
**must not** appear in Stage 1 implementation without a separate architecture gate.

**Deferred beyond Stage 1:** `Invoice`, `Partner`, `Commission`,
`ResellerAccount`, MLM, API reseller, KeyCore-Hub integration.

## 3. Organization-first identity

Registration creates **atomically**:

```
User  +  Personal Organization  +  OWNER Membership
```

There is no code path that creates a `User` without an `Organization` and an
`OWNER` `Membership`. A "personal" organization is a full tenant: it is the owner
of projects, and it is the future billing root. The user-facing product may present
a personal account; the data model never does.

`Membership` is the **only** link between an authentication subject and
tenant-scoped authority.

**Never proof of ownership or permission:**

- a client-provided `organization_id`;
- a client-provided `project_id`;
- a client-provided workspace path;
- any client-provided authority claim (roles, scopes, capabilities, flags).

## 4. Authority chain

The only permitted derivation:

```
authenticated subject
  -> Membership
  -> Organization
  -> Project
  -> server-derived workspace
  -> trusted execution request
  -> Core execution boundary
  -> AuthorizedExecution
  -> effect
```

**Critical rule: CLIENT IDs ARE CLAIMS, NOT AUTHORITY.**

Client-provided `organization_id`, `project_id`, workspace, command, allowed tools,
network permission, quota, and provider credentials must never be converted
directly into execution authority. Each is a *request* that the server resolves
against the authenticated subject and either accepts, narrows, or rejects.

**Baseline note.** `TaskRunRequest.project_id` is already caller-supplied and
already reaches the API layer. Today that is harmless **only** because `RunScope`
has no `project_id` field and `ForgeApiService` holds one constant workspace. Stage 1
must not depend on that accident.

## 5. Trusted execution request (Platform -> Core port)

Stage 1 must introduce a single, narrow, typed port contract — a
**TrustedExecutionRequest** or an equivalent internal contract — that carries only
server-derived trusted execution context into Core.

It carries: the trusted run correlation id, the trusted workspace root, the
execution profile with its authority ceiling, the declared execution intent, the
acceptance/verification expectations, the idempotency identity, and audit-only
scalars (`organization_id`, `project_id`, `initiated_by_user_id`) that are **data,
never authority**.

**Forbidden contract shape.** The port must **not** accept a mapping of plaintext
provider secret material as an ordinary DTO field, in any spelling. A contract such
as `provider_secret_environ: Mapping[str, str]` is explicitly **rejected**.

**Required credential abstraction.** Secrets are addressed by an **opaque secret
reference** and resolved only at a controlled boundary:

- stored as a reference, never as plaintext in a durable record;
- resolved at a controlled boundary immediately before use;
- placed into an **ephemeral** execution context;
- never written to persistent `RunRecord`;
- never written to `PhysicalTelemetry`;
- never returned in ordinary API responses.

**Not frozen here:** a specific KMS or vault vendor, and any concrete reference
grammar. A scheme such as `kms:v1:<base64_ciphertext>` is **not** mandated
architecture; the reference stays opaque to Core. The existing reference model —
`ProviderAccount.secret_ref`, currently a validated environment-variable name — is
the seed of this abstraction and is extended, not replaced.

## 6. Workspace authority

The workspace is **server-derived** from the resolved tenant and project. A client
never selects a filesystem path.

The contract is expressed as an abstract server-derived **resource root**, not as a
platform-specific path. A Linux-only layout such as `/var/forge/tenants/...` must
**not** be baked into the architecture: Core stays cross-platform, and the existing
`WorkspaceBoundary` (canonical containment, traversal and junction refusal, enforced
at the scope and re-checked immediately before spawn) is the enforcement primitive
on whatever root the server derives.

**Boundaries that are not authorization:**

- `Workspace` **is not** permission;
- `WorkspaceBoundary` **is not** authorization;
- `RunScope` **is not** authorization.

They bound *where* and *how* an already-authorized effect may happen; they do not
grant it.

## 7. Run model

Three distinct things, never two authorities over one state:

| Thing | Nature | Owns |
| --- | --- | --- |
| **Core `Run`** | mutable in-memory runtime execution state | the internal execution flow and `AgentHarness` phases |
| **Platform `RunRecord`** | durable external lifecycle truth | queue/lifecycle state visible to Platform and clients |
| **Core `RunStore`** | Core-local durable observation, audit, and history | the observable record of what happened inside one run |

`RunStore` **is not**: a Platform lifecycle authority, an authorization source, or a
resume authority. It is not a Platform database, and no Platform semantics may be
pushed into it. It preserves its existing self-description: deliberately not an
execution authority and not a resume engine.

Platform never mutates a Core `Run`. Core never reads a Platform `RunRecord` to make
an execution decision.

## 8. Run lifecycle (future Stage 1 flow)

1. Platform authenticates the request.
2. Platform resolves membership -> organization -> project.
3. Platform creates or claims the `RunRecord`.
4. Platform derives the trusted workspace and execution context.
5. Platform sends the `TrustedExecutionRequest` across the Platform -> Core port.
6. Core executes.
7. Core emits `PhysicalTelemetry`.
8. Platform accepts telemetry and result.
9. Platform updates the `RunRecord` lifecycle.
10. Platform creates the `UsageRecord` from the physical measurement plus
    Platform-owned enrichment.

**Core never polls Platform for authority.** Authority is decided before the run
starts and is frozen for its duration. A run that needs a new permission needs a new
run, not a callback.

## 9. Physical telemetry and usage

`PhysicalTelemetry` remains an exclusively physical measurement. It carries
identity, measured counts, duration, and outcome. It carries **no** price, charge,
wallet, balance, ownership, organization authority, or user authority. It is
write-only in Core: nothing in `app/` reads it back to decide anything.

The only permitted flow:

```
PhysicalTelemetry  ->  Platform enrichment  ->  UsageRecord
```

`UsageRecord` is **not** money and must never become a `CostRecord` inside Core.

Future billing direction (`D-PLATFORM-07`, `D-PLATFORM-11`):

```
Usage -> Cost -> Price -> Charge -> Ledger
```

This pipeline is **not** implemented in Stage 1.

## 10. Provider account and BYOK

`ProviderAccount` is a **Platform-side** record. Core does not own it, never imports
it, and never reads it.

### Two ownership modes, and only two

A `ProviderAccount` is exactly one of:

1. **tenant-owned** — it belongs to one `Organization`, and `organization_id` is
   **required and non-null**. This is the BYOK case: a tenant supplies its own
   provider credential.
2. **system-owned** (Forge-managed) — it belongs to **no** `Organization`, and
   `organization_id` is **null**. This is the Forge-managed case: the credential is
   the platform's own and is not a tenant resource.

The ownership mode is a **server-side domain fact**. It is never a
client-supplied claim, and a client can never manufacture system ownership by
sending a null or empty organization reference.

### Rules that follow from a null `organization_id`

These are normative, because a null tenant column is the classic place a tenant
filter silently stops filtering:

- a null `organization_id` **never** means "all tenants";
- a null `organization_id` **never** widens a tenant scope;
- a **tenant-scoped read must select only records explicitly belonging to that
  organization**, and must never match a system-owned record merely because
  `organization_id IS NULL`;
- a system-owned record is **not** available through an ordinary tenant-scoped
  client read;
- a client request **cannot** select or reference a system-owned
  `ProviderAccount` unless a separate, explicitly server-authorized path permits
  exactly that;
- a tenant-scoped query must treat `NULL` as an absence of ownership, not as a
  wildcard.

### What a persistent record holds

Provider identity, its ownership mode, a `secret_ref`, and metadata. It holds **no**
plaintext secret.

### What system ownership is not

System-owned means **only** an ownership boundary. It introduces no new entity, no
new authority mechanism, and no new permission object. A `ProviderAccount` — of
either mode — is still not Core authority, and system ownership by itself grants no
execution authority. Core receives a resolved credential context for one execution
and learns nothing about which commercial model produced it.

BYOK versus Forge-managed **commercial** semantics are separated at the
**Platform/Billing** level, not in Core. No provider wholesale pricing, cost, or
margin may be added to Core's provider account model, and none is added to the
Platform record by this contract.

## 11. API key

`APIKey` belongs to the **Organization**.

Safe-storage principles:

- the plaintext key is shown **only** at creation;
- persistence stores only a safe representation (a hash) plus a short `prefix` used
  for identification;
- there is a `revoked` state, `created_at`, `last_used_at`, and `scopes`.

**Not frozen here:** a specific prefix format or an exact cryptographic scheme.
Those require a separate security decision.

**An API key is never direct filesystem or workspace authority.** Authority still
flows `authenticated subject -> membership -> organization -> project`. A key
authenticates a caller; it does not authorize an effect.

## 12. Idempotency

Two levels, deliberately separate:

**Platform idempotency** accounts for at least: tenant/organization identity, the
operation class, the server-derived project/resource identity, and the client
idempotency key. A bare global client key must **not** be used as tenant-wide
authority.

**Core local idempotency** remains the local protection of execution side effects.
Its existing hierarchy (`TaskIdentity -> OperationIdentity -> RunIdentity ->
AttemptIdentity`), atomic claim before durable writes, and terminal-state protection
are unchanged by Stage 1.

Not solved inside Stage 0.1 and not solved here: distributed compare-and-set and
leases. **F-46-03** (concurrent writers can duplicate a telemetry record) and
**F-46-04** (lossy `run_id` sanitizer; `list_run_ids` returns the sanitized stem)
remain **deferred** until a persistent storage layer exists.

## 13. Persistence

**Platform:** PostgreSQL is the future durable authority.

**Core:** local filesystem/runtime persistence stays as it is.

A Platform PostgreSQL dependency must **not** be moved into Core, and Core must stay
runnable with no database at all.

The future Platform database must provide tenant containment — row-level security or
an equivalent defense-in-depth scheme — so that a query bug cannot cross a tenant
boundary. RLS is **not** implemented in this document.

## 14. Money boundary

Core does not know money semantics.

When billing is implemented, the frozen contract is:

- integer minor units / micro-credits; **no `float` for financial truth**;
- immutable financial records;
- atomic ledger operations;
- a charge cannot exceed the available balance;
- financial operations are idempotent;
- the price is snapshotted at charge time, so a later tariff change cannot
  retroactively alter a past charge.

This is the **future** billing architecture contract, **not** Stage 1
implementation.

## 15. Fail-closed telemetry invariant

Already accepted in `D-PLATFORM-13`, and restated normatively here:

> Before a `Charge` can exist, the absence of durable `PhysicalTelemetry` for an
> attempt must make the execution **financially incomplete**.

Permitted future implementations:

- a fail-closed terminal state `physical_telemetry_unavailable`; or
- a compensating marker in the ledger that the billing pipeline cannot accept as
  complete usage truth.

Billing is **not** implemented now. On Stage 0.1 a lost measurement never blocks or
reclassifies a run, which is correct while no money moves.

## 16. Security invariants

These are permanent. Violating one is an architecture defect, not a feature choice.

1. Client IDs are not authority.
2. Workspace is not permission.
3. `RunScope` is not permission.
4. Tool registration is not authorization.
5. Tool discovery is not authorization.
6. AI decision is not authorization.
7. `ProviderAccount` is not Core authority.
8. `RunRecord` ownership is not execution authority.
9. `PhysicalTelemetry` is measurement, not money.
10. Secrets never enter persistent telemetry.
11. Secrets never enter normal API responses.
12. Core remains Platform-independent.
13. Platform may deny a launch but cannot expand Core authority.

## 17. Stage 1 implementation order

1. DTO / domain contracts.
2. Platform persistence schema.
3. Organization-first registration.
4. Authentication.
5. Membership / RBAC.
6. Project + server-derived workspace.
7. `ProviderAccount` + secret abstraction.
8. `APIKey`.
9. `RunRecord` + Platform idempotency.
10. `TrustedExecutionRequest` / Platform -> Core port.
11. Core execution integration.
12. `PhysicalTelemetry` -> `UsageRecord` bridge.
13. End-to-end tenant and security tests.

**Do not start** Billing, Wallet, Pricing, Stripe, Partner, or Reseller before a
separate architecture gate.

## 18. Required Stage 1 test invariants

- a client cannot select another organization;
- a client cannot select another project;
- a client cannot escape the server-derived workspace;
- a client cannot inject arbitrary command, tool, or network authority;
- membership is required;
- a revoked API key is rejected;
- cross-tenant `RunRecord` access is denied;
- cross-tenant `ProviderAccount` access is denied;
- secrets are absent from persistence;
- secrets are absent from telemetry;
- Platform cannot grant Core authority beyond Core policy;
- Core works without Platform imports;
- `RunRecord` is not Core authorization;
- `UsageRecord` contains no money;
- `PhysicalTelemetry` contains no ownership or authority fields;
- a duplicate Platform request is idempotent;
- a duplicate Core execution is locally protected;
- terminal telemetry is durable before future financial completion.

## 19. Open decisions

Not decided, and deliberately not invented here. Each must be decided before the
implementation step that depends on it.

| # | Open decision | Needed before |
| --- | --- | --- |
| O-1 | Exact API key format | step 8 |
| O-2 | Exact session / authentication mechanism | step 4 |
| O-3 | Exact secret backend / KMS or vault vendor | step 7 |
| O-4 | Exact reference grammar for `secret_ref` | step 7 |
| O-5 | Exact workspace filesystem topology | step 6 |
| O-6 | Exact Platform -> Core transport (in-process, gRPC, HTTP, queue) | step 10 |
| O-7 | Exact `RunRecord` status enum. The *required semantics* are no longer open; see section 21 (R-6) | step 9 |
| O-8 | **RESOLVED FOR CONTRACT** — `UNIQUE(run_record_id, attempt_number)`; see section 21 (R-1) | — |
| O-9 | Exact RLS implementation. The *tenant-containment principle* is no longer open; see section 21 (R-3) | step 2 |
| O-10 | Exact provider pricing source | billing stage |
| O-11 | Payment processor | billing stage |
| O-12 | Tax and invoice handling | billing stage |
| O-13 | Reseller / partner economics | deferred stage |

## 21. Refinements (normative)

These refinements were added after an independent architecture review of this
contract. The review is an **input**, not a source of truth; what binds Stage 1 is
the text below. Sections 1-20 stay in force except where a refinement restates a
rule more precisely. Nothing here creates code, a dependency, or a runtime
component.

### R-1 - Platform run identity and Core run identity are distinct

`RunRecord` carries **two** identities, and they must never be conflated:

| Field | Meaning | Nature |
| --- | --- | --- |
| `RunRecord.id` | the **Platform** run identity | Platform-owned; the primary key of the external lifecycle record |
| `RunRecord.core_run_id` | the **Core** execution correlation identity | immutable; produced by Core and carried in `PhysicalTelemetry.run_id` |

`UsageRecord` must carry both, plus the physical attempt number, under explicitly
distinct names:

| Field | Meaning |
| --- | --- |
| `run_record_id` | reference to `RunRecord.id` (Platform identity) |
| `core_run_id` | immutable correlation identity from Core `PhysicalTelemetry` |
| `attempt_number` | the physical execution attempt number |

`UsageRecord` must **not** use an ambiguous single field named `run_id`.

**Uniqueness:** `UNIQUE(run_record_id, attempt_number)`.

This uniqueness is **contract-level, not financial truth**. It states that one
physical attempt of one Platform run has one usage record. It authorizes nothing,
it does not replace Core's local idempotency, and it is not a financial record. If
implementation later needs additional dimensions, that is a **separate decision
record**, never a silent change.

**The bridge is one-way and explicit:**

```
PhysicalTelemetry.run_id
  -> core_run_id
  -> resolves the Platform RunRecord
  -> creates UsageRecord.run_record_id + UsageRecord.core_run_id + attempt_number
```

### R-2 - Credential resolution boundary

`TrustedExecutionRequest` **never** contains plaintext provider secrets. It may
contain only an **opaque credential or secret reference**, or an equivalent
non-secret credential handle.

Core depends only on an **abstract port** for obtaining ephemeral credential
material; the concept is named **`EphemeralSecretResolver`** here for clarity. The
Core-side interface:

- imports no Platform module, no KMS or vault SDK, no database driver, and no
  payment library;
- carries no vendor-specific reference grammar;
- is injected by the Platform / application composition layer, which supplies the
  adapter implementation.

```
TrustedExecutionRequest
  -> opaque credential reference
  -> Core execution boundary
  -> abstract EphemeralSecretResolver
  -> injected adapter
  -> ephemeral in-memory credential
  -> provider invocation
```

A plaintext secret **never**:

- enters a serialized `TrustedExecutionRequest`;
- is stored in a `RunRecord`;
- is stored in a `UsageRecord`;
- enters `PhysicalTelemetry`;
- appears in an ordinary API response;
- is logged.

This is a **boundary** statement, not an implementation. It does **not** assert that
Platform hands plaintext to Core, does **not** say Core knows about KMS or Vault,
and does **not** freeze a resolver implementation. No secret backend is chosen here.

### R-3 - Tenant containment before DTO implementation

Every tenant-owned Platform entity carries an explicit `organization_id` as an
**application-level containment invariant**, independent of the database. This is in
force **before** step 1 (DTO / domain contracts) begins, and it does not replace
future PostgreSQL RLS.

**One explicit exception exists, and it is the only one.** `ProviderAccount`
supports two ownership modes:

- **tenant-owned** — `organization_id` required and non-null;
- **system-owned / Forge-managed** — `organization_id` null.

A system-owned `ProviderAccount` is outside tenant ownership. It is not a tenant
resource, it does not belong to any organization, and it must not be reachable
through an ordinary tenant-scoped read.

**`NULL` `organization_id` is never a tenant wildcard.** The containment invariant
is: a tenant-scoped query selects **only** records that explicitly belong to that
organization. A system-owned record must not appear merely because
`organization_id IS NULL`, and a null tenant column must never be read as "any
tenant".

The three layers are **defense-in-depth**, not alternatives:

```
application authorization
  + explicit organization_id containment on tenant-owned entities
  + explicit exclusion of system-owned records from tenant-scoped reads
  + PostgreSQL RLS (future)
```

O-9 remains open for the *exact RLS implementation*. The tenant-containment
invariant itself is no longer open.

### R-4 - Project workspace roots are mutually isolated

The workspace root of each `Project` must be isolated from the workspace roots of
**all other `Project`s**, including two projects inside the **same**
`Organization`.

A run in Project A must not be able to **traverse into, read, write, or execute
against** files of Project B's workspace. This is an invariant about isolation, not
about mechanism.

Not fixed here: a specific filesystem topology, a Linux path, or a specific OS
isolation mechanism. Only the invariant is fixed.

### R-5 - API key authority semantics and revocation

The `APIKey` authority model is **open**, and exactly one of two models must be
chosen before step 8:

- **A - Organization-scoped service principal:** the key's authority derives from
  the Organization, independent of any individual member's membership.
- **B - Creator-membership-derived credential:** the key's authority derives from
  the creating member's membership.

Whichever model is chosen, the contract **requires** an unambiguous revocation
behavior. This state is specifically **forbidden**:

> the creator loses or has their membership revoked, while an API key they
> previously created silently retains authority.

If model **B** is chosen, revoking the creator's membership must revoke or disable
the derived keys, deterministically and observably.

**Not fixed here:** a token format, JWT, a hash algorithm, a token prefix, or any
concrete scheme such as `forge_live_<base62>`. O-1 remains open.

### R-6 - RunRecord failure and orphan semantics

The final `RunRecord` status enum stays open (O-7), but the lifecycle **must** have
explicit and distinguishable semantics for at least:

- normal success;
- normal failure;
- cancellation;
- timeout;
- execution interruption, crash, or orphaned run.

There **must** be a mechanism that prevents a `RunRecord` from remaining
indefinitely in a running or claimed state after its execution worker is lost.

Not fixed here: whether that mechanism is a lease, a heartbeat, a timeout, or a
reconciliation sweep. The mechanism is left to the design stage that owns run
lifecycle; the **requirement** that such a mechanism exist is normative now.

### R-7 - Decisions that are explicitly NOT frozen

The following appeared as review recommendations and are **not** normative parts of
this contract. They may become future implementation decisions:

- a concrete token format such as `forge_live_<base62>`;
- JWT;
- stateful sessions;
- a concrete envelope-encryption scheme (for example AES-256-GCM plus a
  `SECRET_KEY`);
- a concrete reference grammar such as `env:<VAR_NAME>` or `vault:<secret_id>`;
- a specific KMS or vault vendor;
- a specific workspace filesystem topology;
- Linux-specific paths;
- a specific payment provider.

They must not be read as Stage 1 architecture. Each requires its own decision
record before it becomes binding.

### R-8 - Additional security invariants

Added to the permanent list in section 16:

14. Platform `RunRecord` ID and Core run ID are **distinct identities**.
15. `UsageRecord` must **preserve both** identities.
16. Tenant-owned Platform entities **carry `organization_id`**.
17. Project workspace roots are **isolated from each other**.
18. Credential references are **non-secret handles**.
19. Plaintext provider credentials **never cross a serialized Platform -> Core
    boundary**.
20. `APIKey` authority semantics must have **explicit revocation behavior**.
21. `RunRecord` must **not remain indefinitely running** after worker loss.
22. A null `organization_id` **never means any tenant**. It is never a wildcard and
    never widens a tenant scope.
23. System-owned `ProviderAccount` records are **outside tenant ownership** and are
    **not exposed through ordinary tenant-scoped reads**.
24. **Client-controlled identifiers cannot select system-owned provider
    credentials.** Ownership mode is a server-side fact, not a client claim.

---

## STATUS

```
Stage 0.1  =  FROZEN / GO

Stage 1    =  ARCHITECTURE CONTRACT READY

Implementation status:  NOT STARTED
```

## OPEN DECISIONS

**Open:** O-1 API key format; O-2 session/auth mechanism; O-3 secret backend/KMS;
O-4 `secret_ref` grammar; O-5 workspace topology; O-6 Platform -> Core transport;
O-7 `RunRecord` status enum (required semantics fixed in R-6); O-9 exact RLS
implementation (containment principle fixed in R-3).

**Resolved for contract:** O-8 - `UNIQUE(run_record_id, attempt_number)` (R-1).

**Deferred to a later stage:** O-10 provider pricing source; O-11 payment
processor; O-12 tax/invoice; O-13 reseller/partner economics.

---

# Forge AI — архитектурный контракт Stage 1 — русская версия

> **Authority (нормативность).** Этот документ **нормативен** для реализации
> Stage 1. Он обязателен для последующей работы над Platform. Там, где он
> расходится с предложением, ревью или stage-заметкой, побеждает этот документ;
> там, где он расходится с кодом, код неверен только если противоречит описанному
> здесь поведению Core, иначе побеждает код, а этот документ неверен.
>
> **Область.** Stage 1 = идентичность Platform MVP, аренда, персистентность,
> доступ к API и граница Platform -> Core. Billing **вне области** и присутствует
> здесь только как замороженное направление.
>
> **Базовая точка.** Замороженные решения Stage 0 `D-PLATFORM-01..12` (`db21d65`),
> durable физическая телеметрия Stage 0.1 `D-PLATFORM-13` (`2ea8124`), финальный
> gate Stage 0.1: GO.
>
> **Статус реализации: НЕ НАЧАТА.** Ничего из этого документа не существует в
> репозитории. Ничто из этого документа нельзя описывать в настоящем времени.

---

## 1. Направление зависимостей

Единственно допустимое направление зависимостей — вниз:

```
Клиенты
  |
  v
API / Platform Gateway
  |
  v
Platform Control Plane  +  Billing (будущее)
  |
  v  порт Platform -> Core (раздел 5)
Core
```

**Core не должен импортировать:** Platform, Billing, PostgreSQL или любой драйвер
базы данных, ORM, платёжную библиотеку, users/orgs/memberships или любой модуль
финансового домена. Проверено на базовой точке `2ea8124`: `sqlalchemy`, `psycopg`,
`sqlite3`, `alembic` и `stripe` имеют **ноль** импортов в `app/`.

**Core не должен знать:** кто владелец организации; сколько на кошельке; какая цена
у модели; какой тариф; состояние платежей; состояние reseller/partner. Он не
получает ничего из этого и не имеет для этого полей.

**Асимметрия полномочий.** Platform может **отказать** в запуске. Platform **не
может** расширить то, что разрешено запуску. Потолок authority принадлежит Core;
Platform может его только понизить.

## 2. Сущности Platform MVP

Девять сущностей для Stage 1. Ни одна дополнительная сущность не вводится без
отдельной записи решения.

| Сущность | Назначение | Граница арендатора | Ключевые поля | Authority / не authority |
| --- | --- | --- | --- | --- |
| `User` | Аутентифицированное физическое лицо | нет (глобальная) | `id`, `email` (unique), `password_hash` или ссылка на внешнюю идентичность, `status`, `created_at` | **Является** субъектом аутентификации. **Не является** арендатором и **не является** execution authority |
| `Organization` | Арендатор: изоляция, владение и (в будущем) корень биллинга | сама граница | `id`, `name`, `slug` (unique), `is_personal`, `billing_account_ref` (nullable, будущее), `referred_by_partner_id` (nullable, будущее), `created_at` | **Является** корнем изоляции и владения. **Не является** execution authority |
| `Membership` | Связь авторизации `User` <-> `Organization` с ролью | принадлежит Organization | `id`, `user_id`, `organization_id`, `role`, `created_at` | **Является** единственным путём от субъекта к authority арендатора. **Не является** execution authority |
| `Project` | Единица работы внутри арендатора | принадлежит Organization | `id`, `organization_id`, `name`, `slug`, `status`, `created_at` | **Является** владельцем запусков и корней workspace. **Не является** execution authority |
| `ProviderAccount` | Ссылка на провайдерский кредил. Имеет ровно два режима владения (см. раздел 10) | **tenant-owned**: принадлежит одной Organization, `organization_id` non-null. **system-owned / Forge-managed**: не принадлежит никакой Organization, `organization_id` null, никогда не доступен через tenant-scoped чтение | `id`, `organization_id` (null только для system-owned аккаунта), `provider_name`, `secret_ref`, `enabled`, `metadata` | **Является** *ссылкой* на кредил. **Не является** authority Core и не хранит **ни** plaintext-секрета, **ни** оптовой цены, **ни** себестоимости. Режим владения не является authority |
| `APIKey` | Токен программного доступа для одной Organization | принадлежит Organization | `id`, `organization_id`, `created_by_user_id`, безопасное представление (hash) + `prefix`, `scopes`, `expires_at`, `revoked_at`, `created_at`, `last_used_at` | **Является** учётным данным аутентификации. **Не является** authority на filesystem, workspace, команды или инструменты |
| `RunRecord` | Durable истина внешнего жизненного цикла запуска | принадлежит Project | `id`, `project_id`, `initiated_by_user_id`, `task_id`, `status`, `started_at`, `completed_at`, `core_run_id` (уникальная корреляционная идентичность) | **Является** записью внешнего жизненного цикла. **Не является** authorization для Core |
| `UsageRecord` | Неизменяемое физическое измерение, привязанное к запуску | выводится из RunRecord | `id`, `run_id`, `attempt_number`, `provider_name`, `model_name`, `input_tokens`, `output_tokens`, `cached_tokens`, `duration`, `request_count`, метаданные исхода | **Является** измерением. **Не является** деньгами, ценой, списанием или балансом |

**Сущности биллинга — последующие этапы, отдельный gate.** `Wallet`,
`CreditTransaction`, `CostRecord`, `PricingPlan`, `PriceRule`, `Payment`. Они **не
должны** появиться в реализации Stage 1 без отдельного архитектурного gate.

**Отложено за пределы Stage 1:** `Invoice`, `Partner`, `Commission`,
`ResellerAccount`, MLM, API reseller, интеграция KeyCore-Hub.

## 3. Organization-first идентичность

Регистрация создаёт **атомарно**:

```
User  +  Personal Organization  +  OWNER Membership
```

Не существует пути кода, создающего `User` без `Organization` и `OWNER`
`Membership`. «Персональная» организация — полноценный арендатор: она владелец
проектов и будущий корень биллинга. Пользовательский интерфейс может показывать
персональный аккаунт; модель данных — никогда.

`Membership` — **единственная** связь между субъектом аутентификации и authority
уровня арендатора.

**Никогда не является доказательством владения или разрешения:**

- клиентский `organization_id`;
- клиентский `project_id`;
- клиентский путь workspace;
- любое клиентское заявление о полномочиях (роли, scopes, capabilities, флаги).

## 4. Цепочка authority

Единственно допустимый вывод:

```
аутентифицированный субъект
  -> Membership
  -> Organization
  -> Project
  -> server-derived workspace
  -> trusted execution request
  -> граница исполнения Core
  -> AuthorizedExecution
  -> эффект
```

**Критическое правило: КЛИЕНТСКИЕ ID — ЭТО ЗАЯВКИ, А НЕ AUTHORITY.**

Клиентские `organization_id`, `project_id`, workspace, команда, разрешённые
инструменты, сетевое разрешение, квота и провайдерские кредилы никогда не должны
превращаться напрямую в execution authority. Каждое из них — *запрос*, который
сервер сверяет с аутентифицированным субъектом и либо принимает, либо сужает, либо
отклоняет.

**Замечание о базовой точке.** `TaskRunRequest.project_id` уже поставляется
вызывающим и уже доходит до слоя API. Сегодня это безвредно **только** потому, что
у `RunScope` нет поля `project_id`, а `ForgeApiService` держит один постоянный
workspace. Stage 1 не должен опираться на эту случайность.

## 5. Trusted execution request (порт Platform -> Core)

Stage 1 должен ввести единый узкий типизированный контракт порта —
**TrustedExecutionRequest** или эквивалентный внутренний контракт, — передающий в
Core только server-derived доверенный контекст исполнения.

Он передаёт: доверенную корреляционную идентичность запуска, доверенный корень
workspace, профиль исполнения с его потолком authority, объявленный интент
исполнения, ожидания acceptance/verification, идентичность идемпотентности и
audit-скаляры (`organization_id`, `project_id`, `initiated_by_user_id`), которые
являются **данными, а не authority**.

**Запрещённая форма контракта.** Порт **не должен** принимать отображение
plaintext-секретов провайдера как обычное поле DTO ни в каком написании. Контракт
вида `provider_secret_environ: Mapping[str, str]` явно **отклонён**.

**Требуемая абстракция кредилов.** Секреты адресуются **непрозрачной ссылкой на
секрет** и разрешаются только на контролируемой границе:

- хранится ссылка, а не plaintext в durable-записи;
- разрешается на контролируемой границе непосредственно перед использованием;
- попадает в **ephemeral** контекст исполнения;
- никогда не пишется в persistent `RunRecord`;
- никогда не пишется в `PhysicalTelemetry`;
- никогда не возвращается в обычных ответах API.

**Здесь не фиксируется:** конкретный вендор KMS или vault и конкретная грамматика
ссылки. Схема вида `kms:v1:<base64_ciphertext>` **не** является обязательной
архитектурой; ссылка остаётся непрозрачной для Core. Существующая ссылочная модель
— `ProviderAccount.secret_ref`, сегодня валидируемое имя переменной окружения, —
является зародышем этой абстракции и расширяется, а не заменяется.

## 6. Authority workspace

Workspace является **server-derived** от разрешённых арендатора и проекта. Клиент
никогда не выбирает путь файловой системы.

Контракт выражается как абстрактный server-derived **корень ресурса**, а не как
платформенно-зависимый путь. Linux-only раскладка вида `/var/forge/tenants/...` не
должна **зашиваться** в архитектуру: Core остаётся кросс-платформенным, а
существующий `WorkspaceBoundary` (каноническая вложенность, отказ от traversal и
junction, применение на уровне scope и повторная проверка непосредственно перед
spawn) является enforcement-примитивом на том корне, который сервер выведет.

**Границы, которые не являются авторизацией:**

- `Workspace` **не является** разрешением;
- `WorkspaceBoundary` **не является** авторизацией;
- `RunScope` **не является** авторизацией.

Они ограничивают *где* и *как* может произойти уже разрешённый эффект; они его не
разрешают.

## 7. Модель Run

Три разные вещи, и никогда две authority над одним состоянием:

| Вещь | Природа | Чем владеет |
| --- | --- | --- |
| **Core `Run`** | mutable in-memory runtime-состояние исполнения | внутренним потоком исполнения и фазами `AgentHarness` |
| **Platform `RunRecord`** | durable истина внешнего жизненного цикла | состоянием очереди/жизненного цикла, видимым Platform и клиентам |
| **Core `RunStore`** | Core-локальное durable наблюдение, аудит и история | наблюдаемой записью того, что произошло внутри запуска |

`RunStore` **не является**: authority жизненного цикла Platform, источником
авторизации или authority для resume. Он не является базой данных Platform, и
никакая семантика Platform не должна протаскиваться в него. Он сохраняет
существующее самоописание: намеренно не execution authority и не resume-движок.

Platform никогда не мутирует Core `Run`. Core никогда не читает Platform
`RunRecord` для принятия execution-решений.

## 8. Жизненный цикл запуска (будущий поток Stage 1)

1. Platform аутентифицирует запрос.
2. Platform разрешает membership -> organization -> project.
3. Platform создаёт или захватывает `RunRecord`.
4. Platform выводит доверенный workspace и контекст исполнения.
5. Platform отправляет `TrustedExecutionRequest` через порт Platform -> Core.
6. Core исполняет.
7. Core испускает `PhysicalTelemetry`.
8. Platform принимает телеметрию и результат.
9. Platform обновляет жизненный цикл `RunRecord`.
10. Platform создаёт `UsageRecord` из физического измерения плюс Platform-owned
    обогащение.

**Core никогда не опрашивает Platform ради authority.** Authority решается до
старта запуска и замораживается на его время. Запуску, которому нужно новое
разрешение, нужен новый запуск, а не обратный вызов.

## 9. Физическая телеметрия и usage

`PhysicalTelemetry` остаётся исключительно физическим измерением. Она несёт
идентичность, измеренные счётчики, длительность и исход. Она не несёт **ни** цены,
**ни** списания, **ни** кошелька, **ни** баланса, **ни** владения, **ни** authority
организации или пользователя. В Core она write-only: ничто в `app/` не читает её
обратно, чтобы что-то решать.

Единственно допустимый поток:

```
PhysicalTelemetry  ->  Platform enrichment  ->  UsageRecord
```

`UsageRecord` — **не** деньги, и он никогда не должен становиться `CostRecord`
внутри Core.

Будущее направление биллинга (`D-PLATFORM-07`, `D-PLATFORM-11`):

```
Usage -> Cost -> Price -> Charge -> Ledger
```

Этот конвейер **не** реализуется в Stage 1.

## 10. Провайдерский аккаунт и BYOK

`ProviderAccount` — **Platform-side** запись. Core им не владеет, никогда его не
импортирует и никогда его не читает.

### Два режима владения, и только два

`ProviderAccount` — это ровно одно из:

1. **tenant-owned** — принадлежит одной `Organization`, и `organization_id` **обязателен и
   non-null**. Это кейс BYOK: арендатор поставляет свой собственный кредил
   провайдера.
2. **system-owned** (Forge-managed) — не принадлежит **никакой** `Organization`, и
   `organization_id` **null**. Это кейс Forge-managed: кредил принадлежит самой платформе и не
   является ресурсом арендатора.

Режим владения — это **server-side доменный факт**. Он никогда не является
заявлением клиента, и клиент никогда не может создать системное владение,
отправив пустую или null-ссылку на организацию.

### Правила, следующие из null `organization_id`

Они нормативны, потому что null-колонка арендатора — классическое место,
где фильтр по арендатору молча перестаёт фильтровать:

- null `organization_id` **никогда** не означает «все арендаторы»;
- null `organization_id` **никогда** не расширяет область арендатора;
- **tenant-scoped чтение должно выбирать только записи, явно
  принадлежащие этой организации**, и никогда не должно совпадать с
  system-owned записью просто потому, что `organization_id IS NULL`;
- system-owned запись **не** доступна через обычное tenant-scoped
  клиентское чтение;
- клиентский запрос **не может** выбрать или сослаться на system-owned
  `ProviderAccount`, если только отдельный, явно авторизованный на сервере путь
  разрешает именно это;
- tenant-scoped запрос должен трактовать `NULL` как отсутствие
  владения, а не как подстановочный символ.

### Что хранит persistent-запись

Идентичность провайдера, режим владения, `secret_ref` и метаданные. Она
**не** хранит plaintext-секрет.

### Чем системное владение НЕ является

Системное владение означает **только** границу владения. Оно не вводит ни
новой сущности, ни нового механизма authority, ни нового permission-объекта.
`ProviderAccount` любого режима по-прежнему не является authority Core, и системное
владение само по себе не даёт execution authority. Core получает разрешённый контекст
кредила на одно исполнение и не узнаёт, какая коммерческая модель его породила.

Коммерческая семантика BYOK против Forge-managed разделяется на уровне
**Platform/Billing**, а не в Core. Никакая оптовая цена провайдера, себестоимость или
маржа не должны добавляться в модель провайдерского аккаунта Core, и ни одна из
них не добавлена в Platform-запись этим контрактом.

## 11. API key

`APIKey` принадлежит **Organization**.

Принципы безопасного хранения:

- plaintext-ключ показывается **только** при создании;
- персистентность хранит только безопасное представление (hash) плюс короткий
  `prefix` для идентификации;
- есть состояние `revoked`, `created_at`, `last_used_at` и `scopes`.

**Здесь не фиксируется:** конкретный формат префикса и точная криптографическая
схема. Они требуют отдельного security-решения.

**API key никогда не является прямой authority на filesystem или workspace.**
Authority всё равно проходит
`аутентифицированный субъект -> membership -> organization -> project`. Ключ
аутентифицирует вызывающего; он не авторизует эффект.

## 12. Идемпотентность

Два уровня, намеренно раздельные:

**Platform idempotency** учитывает минимум: идентичность арендатора/организации,
класс операции, server-derived идентичность проекта/ресурса и клиентский ключ
идемпотентности. Голый глобальный клиентский ключ **не должен** использоваться как
authority уровня арендатора.

**Core local idempotency** остаётся локальной защитой side effects исполнения. Его
существующая иерархия (`TaskIdentity -> OperationIdentity -> RunIdentity ->
AttemptIdentity`), атомарный claim до durable-записей и защита терминальных
состояний не меняются в Stage 1.

Не решается внутри Stage 0.1 и не решается здесь: распределённые
compare-and-set и lease. **F-46-03** (конкурентные писатели могут дублировать
запись телеметрии) и **F-46-04** (lossy-санитайзер `run_id`; `list_run_ids`
возвращает санитизированный stem) остаются **deferred** до появления слоя
постоянного хранилища.

## 13. Персистентность

**Platform:** PostgreSQL — будущая durable authority.

**Core:** локальная файловая/runtime-персистентность остаётся как есть.

Зависимость Platform от PostgreSQL **не должна** переноситься в Core, и Core должен
оставаться запускаемым вообще без базы данных.

Будущая база Platform должна обеспечивать containment арендаторов — row-level
security или эквивалентную схему defense-in-depth, — чтобы ошибка в запросе не
могла пересечь границу арендатора. RLS **не** реализуется в этом документе.

## 14. Граница денег

Core не знает семантики денег.

Когда биллинг будет реализовываться, замороженный контракт таков:

- целые минорные единицы / micro-credits; **никакого `float` для финансовой
  истины**;
- неизменяемые финансовые записи;
- атомарные операции с ledger;
- списание не может превысить доступный баланс;
- финансовые операции идемпотентны;
- цена снимается снимком в момент списания, поэтому последующее изменение тарифа не
  может задним числом изменить прошлое списание.

Это **будущий** архитектурный контракт биллинга, **не** реализация Stage 1.

## 15. Fail-closed инвариант телеметрии

Уже принято в `D-PLATFORM-13` и нормативно повторяется здесь:

> До появления `Charge` отсутствие durable `PhysicalTelemetry` для попытки обязано
> делать исполнение **финансово незавершённым**.

Допустимые будущие реализации:

- fail-closed терминальное состояние `physical_telemetry_unavailable`; либо
- компенсирующая отметка в ledger, которую billing-конвейер не может принять как
  полноценную usage truth.

Billing **сейчас не реализуется**. На Stage 0.1 потерянное измерение никогда не
блокирует и не переклассифицирует запуск, и это правильно, пока не двигаются деньги.

## 16. Инварианты безопасности

Они постоянны. Нарушение любого — архитектурный дефект, а не выбор фичи.

1. Клиентские ID — не authority.
2. Workspace — не разрешение.
3. `RunScope` — не разрешение.
4. Регистрация инструмента — не авторизация.
5. Discovery инструментов — не авторизация.
6. Решение AI — не авторизация.
7. `ProviderAccount` — не authority Core.
8. Владение `RunRecord` — не execution authority.
9. `PhysicalTelemetry` — измерение, а не деньги.
10. Секреты никогда не попадают в persistent-телеметрию.
11. Секреты никогда не попадают в обычные ответы API.
12. Core остаётся независимым от Platform.
13. Platform может отказать в запуске, но не может расширить authority Core.

## 17. Порядок реализации Stage 1

1. DTO / доменные контракты.
2. Схема персистентности Platform.
3. Organization-first регистрация.
4. Аутентификация.
5. Membership / RBAC.
6. Project + server-derived workspace.
7. `ProviderAccount` + абстракция секретов.
8. `APIKey`.
9. `RunRecord` + Platform idempotency.
10. `TrustedExecutionRequest` / порт Platform -> Core.
11. Интеграция исполнения Core.
12. Мост `PhysicalTelemetry` -> `UsageRecord`.
13. End-to-end тесты аренды и безопасности.

**Не начинать** Billing, Wallet, Pricing, Stripe, Partner или Reseller до
отдельного архитектурного gate.

## 18. Обязательные тестовые инварианты Stage 1

- клиент не может выбрать другую организацию;
- клиент не может выбрать другой проект;
- клиент не может выйти за пределы server-derived workspace;
- клиент не может внедрить произвольную команду, инструмент или сетевое
  разрешение;
- membership обязателен;
- отозванный API key отклоняется;
- кросс-арендаторный доступ к `RunRecord` отклоняется;
- кросс-арендаторный доступ к `ProviderAccount` отклоняется;
- секреты отсутствуют в персистентности;
- секреты отсутствуют в телеметрии;
- Platform не может выдать Core authority сверх политики Core;
- Core работает без импортов Platform;
- `RunRecord` не является authorization для Core;
- `UsageRecord` не содержит денег;
- `PhysicalTelemetry` не содержит полей владения или authority;
- дублирующийся запрос Platform идемпотентен;
- дублирующееся исполнение Core локально защищено;
- терминальная телеметрия durable до будущего финансового завершения.

## 19. Открытые решения

Не решены и намеренно не выдумываются здесь. Каждое должно быть принято до
соответствующего шага реализации.

| # | Открытое решение | Нужно до |
| --- | --- | --- |
| O-1 | Точный формат API key | шаг 8 |
| O-2 | Точный механизм сессий / аутентификации | шаг 4 |
| O-3 | Точный бэкенд секретов / вендор KMS или vault | шаг 7 |
| O-4 | Точная грамматика ссылки `secret_ref` | шаг 7 |
| O-5 | Точная топология файловой системы workspace | шаг 6 |
| O-6 | Точный транспорт Platform -> Core (in-process, gRPC, HTTP, очередь) | шаг 10 |
| O-7 | Точный enum статусов `RunRecord`. *Требуемая семантика* больше не открыта; см. раздел 21 (R-6) | шаг 9 |
| O-8 | **РЕШЕНО ДЛЯ КОНТРАКТА** — `UNIQUE(run_record_id, attempt_number)`; см. раздел 21 (R-1) | — |
| O-9 | Точная реализация RLS. *Принцип containment арендаторов* больше не открыт; см. раздел 21 (R-3) | шаг 2 |
| O-10 | Точный источник провайдерских цен | этап billing |
| O-11 | Платёжный процессор | этап billing |
| O-12 | Налоги и инвойсы | этап billing |
| O-13 | Экономика reseller/partner | отложенный этап |

## 21. Уточнения (нормативно)

Эти уточнения добавлены после независимого архитектурного ревью этого
контракта. Ревью — это **входные данные**, а не источник истины; Stage 1
обязывает текст ниже. Разделы 1–20 остаются в силе, кроме тех мест, где уточнение
переформулирует правило точнее. Ничто здесь не создаёт код, зависимость или
runtime-компонент.

### R-1 — Идентичность запуска Platform и идентичность запуска Core различны

`RunRecord` несёт **две** идентичности, и их никогда нельзя смешивать:

| Поле | Значение | Природа |
| --- | --- | --- |
| `RunRecord.id` | идентичность запуска **Platform** | принадлежит Platform; первичный ключ записи внешнего жизненного цикла |
| `RunRecord.core_run_id` | корреляционная идентичность исполнения **Core** | неизменяемая; порождается Core и переносится в `PhysicalTelemetry.run_id` |

`UsageRecord` должен нести обе, плюс номер физической попытки, под явно
различными именами:

| Поле | Значение |
| --- | --- |
| `run_record_id` | ссылка на `RunRecord.id` (идентичность Platform) |
| `core_run_id` | неизменяемая корреляционная идентичность из Core `PhysicalTelemetry` |
| `attempt_number` | номер физической попытки исполнения |

`UsageRecord` **не должен** использовать неоднозначное единое поле с именем `run_id`.

**Уникальность:** `UNIQUE(run_record_id, attempt_number)`.

Эта уникальность — **уровня контракта, а не финансовая истина**. Она
утверждает, что у одной физической попытки одного запуска Platform одна запись
потребления. Она ничего не авторизует, не заменяет локальную
идемпотентность Core и не является финансовой записью. Если реализации позже
потребуются дополнительные измерения, это **отдельная запись решения**, и
никогда — молчаливое изменение.

**Мост однонаправленный и явный:**

```
PhysicalTelemetry.run_id
  -> core_run_id
  -> разрешает Platform RunRecord
  -> создаёт UsageRecord.run_record_id + UsageRecord.core_run_id + attempt_number
```

### R-2 — Граница разрешения кредилов

`TrustedExecutionRequest` **никогда** не содержит plaintext-секретов провайдера. Он
может содержать только **непрозрачную ссылку на кредил или секрет** или
эквивалентный non-secret хэндл кредила.

Core зависит только от **абстрактного порта** для получения ephemeral
материала кредила; концепция названа здесь **`EphemeralSecretResolver`** для
ясности. Интерфейс на стороне Core:

- не импортирует ни модуль Platform, ни SDK KMS или vault, ни драйвер базы
  данных, ни платёжную библиотеку;
- не несёт вендор-специфичной грамматики ссылок;
- внедряется слоем композиции Platform / приложения, который поставляет
  реализацию адаптера.

```
TrustedExecutionRequest
  -> непрозрачная ссылка на кредил
  -> граница исполнения Core
  -> абстрактный EphemeralSecretResolver
  -> внедрённый адаптер
  -> ephemeral кредил в памяти
  -> вызов провайдера
```

Plaintext-секрет **никогда**:

- не входит в сериализованный `TrustedExecutionRequest`;
- не хранится в `RunRecord`;
- не хранится в `UsageRecord`;
- не попадает в `PhysicalTelemetry`;
- не появляется в обычном ответе API;
- не логируется.

Это утверждение о **границе**, а не о реализации. Оно **не** утверждает, что
Platform передаёт plaintext в Core, **не** говорит, что Core знает о KMS или Vault, и **не**
замораживает реализацию resolver'а. Никакой бэкенд секретов здесь не выбирается.

### R-3 — Containment арендаторов до реализации DTO

Каждая tenant-owned сущность Platform несёт явный `organization_id` как
**application-level инвариант containment**, независимо от базы данных. Это
действует **до** начала шага 1 (DTO / доменные контракты) и не заменяет
будущий PostgreSQL RLS.

**Существует ровно одно явное исключение.** `ProviderAccount`
поддерживает два режима владения:

- **tenant-owned** — `organization_id` обязателен и non-null;
- **system-owned / Forge-managed** — `organization_id` null.

System-owned `ProviderAccount` находится вне владения арендатора. Он не
является ресурсом арендатора, не принадлежит никакой организации и не
должен быть доступен через обычное tenant-scoped чтение.

**`NULL` в `organization_id` никогда не является подстановочным
символом арендатора.** Инвариант containment таков: tenant-scoped запрос выбирает
**только** записи, явно принадлежащие этой организации. System-owned
запись не должна появляться только потому, что `organization_id IS NULL`, и null
колонка арендатора никогда не должна читаться как «любой арендатор».

Три слоя — это **defense-in-depth**, а не альтернативы:

```
авторизация на уровне приложения
  + явный containment organization_id на tenant-owned сущностях
  + явное исключение system-owned записей из tenant-scoped чтений
  + PostgreSQL RLS (будущее)
```

O-9 остаётся открытым для *точной реализации RLS*. Сам инвариант
containment'а арендаторов больше не открыт.

### R-4 — Workspace-корни проектов взаимно изолированы

Workspace-корень каждого `Project` должен быть изолирован от workspace-корней
**всех остальных `Project`**, включая два проекта внутри **одной и той же**
`Organization`.

Запуск в Project A не должен иметь возможности **пройти внутрь, читать,
писать или исполнять в отношении файлов** workspace'а Project B. Это инвариант
об изоляции, а не о механизме.

Здесь не фиксируется: конкретная топология файловой системы, Linux-путь или
конкретный механизм изоляции ОС. Фиксируется только инвариант.

### R-5 — Authority API key и отзыв

Модель authority `APIKey` **открыта**, и до шага 8 должна быть выбрана ровно
одна из двух моделей:

- **A — сервисный принципал уровня Organization:** authority ключа выводится из
  Organization, независимо от membership конкретного участника.
- **B — кредил, выведенный из membership создателя:** authority ключа
  выводится из membership создавшего участника.

Какая бы модель ни была выбрана, контракт **требует** однозначного
поведения отзыва. Следующее состояние специально **запрещено**:

> создатель теряет или лишается membership, а созданный им ранее API key
> молча сохраняет authority.

Если выбрана модель **B**, отзыв membership создателя должен детерминированно
и наблюдаемо отозвать или отключать производные ключи.

**Здесь не фиксируется:** формат токена, JWT, алгоритм хэша, префикс
токена или любая конкретная схема вида `forge_live_<base62>`. O-1 остаётся открытым.

### R-6 — Семантика отказа и orphan для RunRecord

Итоговый enum статусов `RunRecord` остаётся открытым (O-7), но жизненный цикл
**должен** иметь явную и различимую семантику минимум для:

- нормального успеха;
- нормального отказа;
- отмены;
- таймаута;
- прерывания исполнения, краша или orphaned run.

**Должен** существовать механизм, предотвращающий бесконечное
зависание `RunRecord` в состоянии running или claimed после потери execution
worker'а.

Здесь не фиксируется: является ли этот механизм lease, heartbeat,
таймаутом или reconciliation-обходом. Механизм оставлен этапу дизайна,
который владеет жизненным циклом запуска; **требование** его существования
нормативно уже сейчас.

### R-7 — Решения, которые явно НЕ заморожены

Следующее появилось как рекомендации ревью и **не** является нормативной
частью этого контракта. Они могут стать будущими implementation-решениями:

- конкретный формат токена, например `forge_live_<base62>`;
- JWT;
- stateful-сессии;
- конкретная схема envelope-шифрования (например AES-256-GCM плюс
  `SECRET_KEY`);
- конкретная грамматика ссылки, например `env:<VAR_NAME>` или `vault:<secret_id>`;
- конкретный вендор KMS или vault;
- конкретная топология файловой системы workspace;
- Linux-специфичные пути;
- конкретный платёжный провайдер.

Их нельзя читать как архитектуру Stage 1. Каждое требует собственной записи
решения, прежде чем стать обязательным.

### R-8 — Дополнительные инварианты безопасности

Добавлены к постоянному списку в разделе 16:

14. ID `RunRecord` и Core run ID — **различные идентичности**.
15. `UsageRecord` должен **сохранять обе** идентичности.
16. Tenant-owned сущности Platform **несут `organization_id`**.
17. Workspace-корни проектов **изолированы друг от друга**.
18. Ссылки на кредилы — **non-secret хэндлы**.
19. Plaintext-кредилы провайдера **никогда не пересекают
    сериализованную границу Platform -> Core**.
20. Семантика authority `APIKey` должна иметь **явное поведение отзыва**.
21. `RunRecord` **не должен оставаться бесконечно в running** после
    потери worker'а.
22. Нулевой `organization_id` **никогда не означает любого арендатора**. Он
    никогда не является подстановочным символом и никогда не расширяет
    область арендатора.
23. System-owned записи `ProviderAccount` находятся **вне владения
    арендатора** и **не показываются через обычные tenant-scoped чтения**.
24. **Клиентские идентификаторы не могут выбрать system-owned
    провайдерские кредилы.** Режим владения — server-side факт, а не заявление
    клиента.

---

## STATUS

```
Stage 0.1  =  FROZEN / GO

Stage 1    =  ARCHITECTURE CONTRACT READY

Implementation status:  NOT STARTED
```

## OPEN DECISIONS

**Открыты:** O-1 формат API key; O-2 механизм сессий/аутентификации; O-3 бэкенд
секретов/KMS; O-4 грамматика `secret_ref`; O-5 топология workspace; O-6 транспорт
Platform -> Core; O-7 enum статусов `RunRecord` (требуемая семантика зафиксирована в R-6); O-9
точная реализация RLS (принцип containment зафиксирован в R-3).

**Решено для контракта:** O-8 — `UNIQUE(run_record_id, attempt_number)` (R-1).

**Отложено на более поздний этап:** O-10 источник провайдерских цен; O-11
платёжный процессор; O-12 налоги/инвойсы; O-13 экономика reseller/partner.
