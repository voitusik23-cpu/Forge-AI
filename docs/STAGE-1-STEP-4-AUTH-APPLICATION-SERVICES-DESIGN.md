# Forge AI — Stage 1 / Step 4: Authentication and Application Services Contracts

> **Status.** Design and contracts. This document fixes the **trust boundary** between a
> verified identity, an authorized operation, and the persistence and execution layers.
> Step 4 is **step 4 of 13** in the implementation order of
> [`STAGE-1-ARCHITECTURE-CONTRACT.md`](STAGE-1-ARCHITECTURE-CONTRACT.md) section 17
> ("Authentication"), and it necessarily reaches into step 5 (Membership / RBAC) and
> step 10 (`TrustedExecutionRequest`) to state where those steps attach. It does not
> implement them.
>
> **What is implemented by this step:** exactly one module,
> `app/platform/principal.py`, which holds the *shape* of a verified identity and the
> port through which one is produced. Application Services, the authorization engine,
> the HTTP surface, and the Platform -> Core port are **not implemented** and no
> placeholder for them exists.
>
> **Authority.** `docs/STAGE-1-ARCHITECTURE-CONTRACT.md` is normative. Sections 1, 3, 4,
> 5, 6, 7, 8, 11, 12, 16, 17, 18 and the refinements R-1..R-8 apply to this step. Where
> this document and the contract disagree, the contract wins and this document is
> wrong. `docs/DECISIONS.md` records what was accepted (`D-PLATFORM-01..22`).

---

## 1. Scope, and the two discrepancies found while writing this

### 1.1 In scope

- the **authentication boundary**: what a verified identity is, who may produce one, and
  what may never be mistaken for one;
- the **authorization flow**: how a verified identity becomes a permitted operation in
  one organization and one project;
- the **Application Services contracts**: the use cases, their inputs, their
  authorization requirements, their transaction boundaries, and their error behaviour;
- the **Platform -> Core trusted execution boundary**, expressed against the Core
  contracts that already exist;
- the explicit treatment of **M-2** (subject-bound discovery) and of the fact that
  `forge.user_id` is not proof;
- the list of **open decisions this step must not close**.

### 1.2 Out of scope

HTTP endpoints, an OAuth/OIDC/JWT implementation, a password store, a session store, an
API-key token format or hashing, a KMS/Vault integration, a complete RBAC model, the
Platform -> Core transport implementation, Billing, and any change to Core's
authorization chain or to Platform persistence.

### 1.3 Discrepancy 1 — `app/platform/domain/` does not exist

The task brief names `app/platform/domain/` as a directory to study. **It does not
exist.** The Platform domain contracts live directly in the package root:

```
app/platform/__init__.py      exports
app/platform/models.py        the 8 frozen domain records + new_id() + utc_now()
app/platform/enums.py         the 8 status/role vocabularies
app/platform/persistence/     Step 3 persistence
```

Recorded rather than silently reinterpreted: the brief's path is resolved to
`app/platform/models.py` + `app/platform/enums.py`. No directory was created to make the
brief's wording true, because a package layout is not a contract and adding a level of
nesting would contradict the module list already recorded in `D-PLATFORM-20`.

### 1.4 Discrepancy 2 — dependency direction and where Step 4 code belongs

Section 1 of the architecture contract fixes the dependency direction as **Core ->
nothing Platform**, and `app/platform/__init__.py` states that Core must never import
the Platform package. The reverse direction — Platform composition code importing Core
contracts — is what `TrustedExecutionRequest` is for, and section 1.4 of this document
records exactly which Core module names it may reference and why that does not create a
Core -> Platform dependency.

The second, sharper discrepancy is **within Platform**: Application Services must sit
**above** persistence and must be the only place that decides authorization. Nothing in
Step 3 does that, and Step 3's own design deliberately leaves the tenant to the session
and the decision to "above this layer". This document is that "above".

---

## 2. Layer responsibility boundaries

```
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ L5  HTTP surface / clients                        NOT IMPLEMENTED          │
  │     may present: a credential. Never: an identity, an authorization, or    │
  │     any server-derived value.                                            │
  └───────────────────────────────┬──────────────────────────────────────────┘
                                  │ credential (opaque to everything below)
  ┌───────────────────────────────▼──────────────────────────────────────────┐
  │ L4  Authentication verifier                       PORT DEFINED (Step 4)   │
  │     the ONE component allowed to say "this credential is subject X".      │
  │     Owns: credential checking. Produces: AuthenticatedPrincipal.          │
  │     Does NOT own: authorization, tenancy, or any database access.         │
  └───────────────────────────────┬──────────────────────────────────────────┘
                                  │ AuthenticatedPrincipal  (verified)
  ┌───────────────────────────────▼──────────────────────────────────────────┐
  │ L3  Application Services                          NOT IMPLEMENTED        │
  │     use cases. Resolves membership -> organization -> project, decides    │
  │     authorization, owns transactions, owns idempotency, builds the        │
  │     TrustedExecutionRequest. The ONLY layer that may authorize.           │
  └───────────────────────────────┬──────────────────────────────────────────┘
                                  │ repository calls inside a chosen scope
  ┌───────────────────────────────▼──────────────────────────────────────────┐
  │ L2  Persistence (Step 3, implemented)                                     │
  │     UnitOfWork + repositories. Guarantees: transaction correctness,       │
  │     containment, one subject per transaction, column-scoped identity      │
  │     writes, compare-and-set lifecycle.                                    │
  │     Does NOT own: business authorization. It is storage and isolation.    │
  └───────────────────────────────┬──────────────────────────────────────────┘
                                  │ SQL
  ┌───────────────────────────────▼──────────────────────────────────────────┐
  │ L1  PostgreSQL + FORCE RLS (Step 2, implemented)                          │
  │     last-resort containment. Assume L3 and L2 both have a bug.            │
  └──────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────────┐
  │ Core execution plane (app/execution, app/runtime)  implemented, unchanged │
  │ Owns: ExecutionCoordinator, ExecutionIntent, AuthorizedExecution,         │
  │ RunScope, WorkspaceBoundary. Core decides what may execute. Platform may  │
  │ DENY a launch; it may never EXPAND Core authority.                        │
  └──────────────────────────────────────────────────────────────────────────┘
```

**The single most important sentence in this document.** Authorization is decided at
**L3** from a verified principal (L4) plus server-side data (L2). It is **never** decided
at L2, and L2's policies are a *backstop*, not the decision.

Non-overlapping responsibilities, stated so that a future change has a place to be
wrong:

| Layer | Owns | Must never own |
| --- | --- | --- |
| L4 Authentication | credential verification; producing the principal | authorization, tenancy, repository access, `organization_id` |
| L3 Application Services | authorization; tenancy resolution; transaction boundary; idempotency; request construction | credential verification; SQL; RLS policy semantics |
| L2 Persistence | transactions; containment; lifecycle compare-and-set; error normalization | business authorization; "is this person an admin?" |
| L1 PostgreSQL | containment backstop; grant/policy enforcement | any business rule not expressible as containment |
| Core | whether and how an effect executes | Platform identity, tenancy, billing, or lifecycle |

---

## 3. Authentication — the trust boundary

### 3.1 What a verified identity is

`AuthenticatedPrincipal` (`app/platform/principal.py`, implemented by this step) is a
**frozen** record with exactly four fields:

| Field | Meaning | Trusted? |
| --- | --- | --- |
| `subject_id` | the Platform `User.id` the verifier established | **trusted**, and only because a verifier produced it |
| `subject_kind` | `user` or `api_key` | **trusted**; states which authority model applies |
| `method` | `session`, `api_key`, or `service` | **trusted**; the evidence class, not the evidence |
| `verified_at` | when the verifier established it | **trusted**; for auditing and for a future freshness policy |

Deliberately **absent**: `organization_id`, `project_id`, role, scopes, email, display
name, workspace, and any credential material. A principal says *who*; it never says
*what they may do*. Every "what" is resolved per operation at L3.

`subject_kind` exists because the contract (R-5) leaves two API-key authority models
open. Naming the kind now lets L3 branch on it later **without** this module having to
decide which model wins — that decision stays with the owner (see section 9).

### 3.2 Who may produce one

One port, one method:

```python
class AuthenticationVerifier(Protocol):
    async def verify(self, credential: Credential) -> AuthenticatedPrincipal: ...
```

`AuthenticatedPrincipal.create(...)` requires a module-private sentinel, the same
mechanism Core already uses for `AuthorizedExecution` (see
`app/execution/intent.py`). A principal therefore cannot be constructed by a caller that
only imports the type; only the factory inside this module can build one, and only the
verifier port is expected to call it. This is deliberately **not** cryptographic
protection against in-process code — an in-process adversary can import anything. It is a
structural guarantee that **no ordinary code path can mint an identity by accident**, so
"a client sent us a `user_id`" has no route to becoming a principal.

### 3.3 What may never be mistaken for authentication

Each of these is a real value in this repository, and each is explicitly **not** proof:

| Value | Why it is not proof | Where it is legitimate |
| --- | --- | --- |
| a client-supplied `user_id` | it is a claim; the client chose it | nowhere as identity; only as a *request* to be resolved and rejected |
| `forge.user_id` (PostgreSQL GUC) | any session may `set_config` it | bounding a discovery read **after** L4 named the subject (section 8) |
| `forge.organization_id`, `forge.system_scope` | same | bounding a transaction's scope, decided by L3 |
| `APIKey.key_prefix` | an identifier, not a secret | displaying a key to its owner |
| `RunRecord.id`, `RunRecord.core_run_id` | opaque identifiers | correlation only (R-1) |
| `RunScope` | a perimeter, not a permission | bounding *where* an already-authorized effect happens |
| a `Membership` row | tenancy data, not identity | resolving authority **after** a principal exists |

**`forge.user_id` is not authentication**, and this is worth stating three times because
Step 3's design, migration `0015`, and `D-PLATFORM-21` all say it: the GUC decides which
subject a transaction is *willing to talk about*; it never decides who the caller *is*.
Step 3 enforces the first half (`Session.require_subject` refuses a second subject).
Step 4 owns the second half: only a verifier may name a subject.

### 3.4 Where the authentication mechanism itself lives, and why it is not here

The verifier port is defined; **no implementation ships in Step 4**, because O-2 is open.
It must not live in `app/platform/persistence/`: persistence must never check a password,
a JWT, or an external token, and a repository that could verify a credential would become
an authentication authority by accident.

The implementation belongs at **L4**, in the composition/API layer that also owns the
HTTP surface. When it arrives it will:

1. read the credential from the transport;
2. resolve it to a `subject_id` — session lookup, API-key hash comparison, or a federated
   assertion;
3. confirm the subject exists and its state permits authentication at all
   (`User.status == ACTIVE`; a `SUSPENDED` account must not authenticate);
4. call `AuthenticatedPrincipal.create(...)`;
5. pass the principal — and nothing else about the caller — down to L3.

### 3.5 Fail-closed requirements on the verifier

The port's contract, which the implementation must satisfy and the tests in section 10
will assert against a **fake** verifier (never against real cryptography):

1. **Missing credential** -> refuse. Never "anonymous principal".
2. **Malformed credential** -> refuse, and do not distinguish "malformed" from
   "unknown" in the message that reaches a client.
3. **Unknown subject** -> refuse.
4. **Suspended or otherwise inadmissible subject** -> refuse.
5. **Ambiguous credential** (resolves to zero or to more than one subject) -> refuse.
   Ambiguity is a refusal, never a "pick the first".
6. **Verifier unavailable** -> refuse. An outage must not degrade to a permissive path.
7. A refusal raises `AuthenticationError` and returns **no** principal.

### 3.6 Threat model, stated so it is not assumed away

| Threat | Stopped by | Not stopped by |
| --- | --- | --- |
| forged client `user_id` | no code path accepts one as identity | RLS (there is no query without a context) |
| stolen API key | revocation (`APIKey.status`), expiry, `last_used_at` observation | the `key_prefix` |
| token replay | out of scope for Step 4; needs O-2's mechanism | anything in this document |
| an L3 bug that picks the wrong tenant | RLS (L1) refuses the read/write | L3's own unit tests |
| a hostile in-process caller | nothing in Python; process isolation is not claimed | the sentinel (it is an accident guard, not a boundary) |

The last row is stated because over-claiming it would be the easiest lie in this
document. `AuthorizedExecution` has the same property and says so in its own docstring
("this is an accident guard, not a defence against a hostile in-process adversary").

---

## 4. Authorization — organization and project

### 4.1 The authority chain, restated as an algorithm

Contract section 4 permits exactly one derivation. As an ordered, fail-closed algorithm:

```
INPUT: principal (verified), operation, client claims (all untrusted)

 1. principal exists and was produced by a verifier      else DENY unauthenticated
 2. subject state admissible                             else DENY subject_not_admissible
 3. resolve candidates: organizations reachable through
    the subject's ACTIVE memberships                     empty -> DENY no_organization
 4. bind ONE organization:
       - if the client named one, it must be in the candidate set
       - otherwise it is the subject's personal/sole organization, or the
         operation requires an explicit choice and is refused as ambiguous
    a client-named organization outside the set -> DENY not_permitted
                                                       (never "switch to the right one")
 5. resolve the membership for (subject, organization) -> must be ACTIVE
                                                       else DENY not_permitted
 6. if the operation names a project: the project must exist AND
    belong to the bound organization                   else DENY not_permitted
 7. evaluate the operation's permission requirement against the
    resolved membership role                           else DENY not_permitted
 8. ONLY NOW open the tenant-scoped Unit of Work and perform the effect
```

Steps 1-7 are **reads against server data**; step 8 is the only step that writes. A
refusal at any step happens **before** a tenant-scoped transaction exists, so a denied
operation cannot leave a partial write.

### 4.2 Client identifiers are claims, not authority

Contract section 4: *"CLIENT IDs ARE CLAIMS, NOT AUTHORITY."* Concretely, for every
identifier a client can send:

| Client sends | Server does | If it disagrees with server data |
| --- | --- | --- |
| `organization_id` | checks it against the subject's ACTIVE memberships | refuse; never silently substitute |
| `project_id` | checks existence **and** `project.organization_id == bound organization` | refuse; a project in another organization is *not found*, not *forbidden* (section 6.3) |
| `user_id` | ignores it entirely; the subject comes from the principal | a client cannot name a subject at all |
| `workspace` / path | ignores it; the workspace is server-derived from the bound project | a client cannot name a path (contract section 6) |
| command, tools, network | may only **narrow** the resolved `RunScope`/profile | a client may never widen; Core re-checks |
| `provider_account_id` / credentials | may only select among accounts the bound organization owns | a system-owned account is unreachable (contract R-8.24) |
| `role` | ignored; a caller cannot state its own authority | — |
| idempotency key | combined with tenant + operation + resource (contract section 12) | a bare key is never tenant-wide authority |

The rule behind the table: **the server never repairs a client claim.** Substituting the
"right" organization for a wrong one is how a confused-deputy bug is born — it teaches
the client that a wrong identifier works, and it hides the bug from the operator.

### 4.3 Membership resolution

The only source of tenancy authority is `Membership`.

- **Required**: a row for `(organization_id, user_id)` with `status == ACTIVE`.
- `MembershipStatus.INACTIVE` and `REVOKED` both deny. They are distinct states in the
  schema (a returning member is a status change, not a second row — Step 2 K-2), so the
  service must not collapse them into one "not a member" answer *internally*, but must
  **not** disclose the difference to a client (section 6.3).
- `organization.status == ACTIVE` is also required. A suspended organization denies even
  an active membership.
- Absence of a membership is a **denial**, never an implicit "personal organization was
  created" and never a fallback to another organization.

### 4.4 Roles: what exists, and what is deliberately not decided

The schema and the domain already define `MembershipRole` =
`OWNER`, `ADMIN`, `MEMBER`, `BILLING`. **This document does not turn that vocabulary into
a permission matrix**, because contract section 17 orders "Membership / RBAC" as step 5
and no accepted contract states what each role may do.

What Step 4 fixes is only the *mechanism*:

- an operation declares a **required authority**: either
  *`any_active_member`* or *`named_roles`*;
- the service resolves the caller's role from the membership (step 5 of the algorithm)
  and compares — it never reads a role from a client claim;
- **`OWNER` may not be manufactured**: a role change is a separate administrative
  operation with its own authorization, and Step 3 deliberately offers
  `memberships.set_role` as a *recording* method that authorizes nothing.

`BILLING` is present in the vocabulary and has **no** Step 4 operation. It is reserved for
the Billing stage; inventing its permissions now would close a decision that belongs to
the owner.

The proposed default for the first implementation, offered as a **proposal and not a
decision** (see section 9, question Q-2):

| Operation class | Proposed required authority | Rationale |
| --- | --- | --- |
| read own organizations / memberships | *any active member* | a member may see where it belongs |
| read projects in the bound organization | *any active member* | ordinary working access |
| create a project | *`OWNER` or `ADMIN`* | creates a workspace root (R-4); not a member-level act |
| read runs in the bound organization | *any active member* | ordinary working access |
| start a run | *`OWNER` or `ADMIN` or `MEMBER`* | execution is the product's core act; excluding `MEMBER` would make the `MEMBER` role useless |
| manage memberships / roles | *`OWNER`* | authority over authority; needs its own accepted decision |
| manage provider accounts / secrets | *`OWNER` or `ADMIN`* | credential administration; O-3/O-4 still open |
| create, read, or revoke API keys | *needs a decision* | depends on the R-5 model (Q-1) |

### 4.5 Fail-closed matrix

Every row denies. There is no "allow by absence of a rule".

| Condition | Result |
| --- | --- |
| no principal | `unauthenticated` |
| principal from an unverified path (impossible by construction) | `unauthenticated` |
| subject not `ACTIVE` | `subject_not_admissible` |
| no ACTIVE membership for the bound organization | `not_permitted` |
| organization suspended | `not_permitted` |
| organization not among the subject's candidates | `not_permitted` |
| ambiguous organization (client named none, several available, operation needs one) | `ambiguous_organization` |
| project does not exist, or exists in another organization | `not_found` |
| membership role not in the operation's required set | `not_permitted` |
| repository raises `PermissionDeniedError` (RLS refused) | `not_permitted`, and it is a **bug signal** — L3 should have denied first |
| repository raises `ConcurrentModificationError` | `conflict` |
| transaction aborted by an earlier caught failure | `transaction_aborted` (Step 3 H-1 refuses the commit) |

The second-to-last-but-one row is deliberate: an RLS refusal reaching L3 means L3's own
authorization was wrong or missing. It must be surfaced as a denial **and** logged as an
internal inconsistency, never retried and never softened into "not found".

---

## 5. Application Services — use case contracts

Nine use cases, each with the eight required attributes. **None is implemented by this
step.** `UoW` means a `UnitOfWork` from `app/platform/persistence/database.py`; scope
names (`tenant`, `pre_tenant`, `server`) are its factory names.

Reading the table: `I` = input DTO, `ID` = identity source, `AZ` = authorization checks,
`R` = repositories / UoW, `SE` = side effects, `TX` = transaction boundary, `E` =
errors, `IDEM` = idempotency.

---

### AS-1 — list organizations available to the caller

- **I**: empty (no client identifiers accepted).
- **ID**: principal.
- **AZ**: step 1-3 of 4.1. No organization is bound; this is the *discovery* read that
  precedes binding.
- **R**: `pre_tenant(user_id=principal.subject_id)` -> `organizations.list_for_user`,
  `memberships.list_for_user`. The subject comes from the principal, never from the DTO.
- **SE**: none (read-only).
- **TX**: one read transaction; no write, so no commit of consequence.
- **E**: `unauthenticated`; empty list for a subject with no memberships (an empty list is
  not an error and not a disclosure — it is about the caller itself).
- **IDEM**: not applicable (idempotent by nature).

### AS-2 — list projects available in the selected organization

- **I**: `organization_id` (a claim).
- **ID**: principal.
- **AZ**: steps 1-6, but the project check is "list what belongs to the bound tenant"
  rather than "check one project".
- **R**: `tenant(organization_id, user_id=principal.subject_id)` -> `projects.list_for_current_tenant`.
- **SE**: none.
- **TX**: one read transaction.
- **E**: `not_permitted` for an organization outside the candidate set (indistinguishable
  from "does not exist", section 6.3).
- **IDEM**: not applicable.

### AS-3 — create a project

- **I**: `organization_id` (claim), `name`, optional `slug`. **Not** accepted:
  `workspace_ref`, `project_id`, status.
- **ID**: principal.
- **AZ**: steps 1-7 with required authority *`OWNER` or `ADMIN`*.
- **R**: `tenant(...)` -> `projects.create(project_id, name, slug=...)`. `project_id` is
  generated **server-side** (`new_id()`); `workspace_ref` is server-derived and set by the
  workspace step, not by this use case.
- **SE**: one `Project` row. When the workspace step lands, one workspace root (R-4).
- **TX**: one tenant transaction. The `Project` insert commits even if a later workspace
  provisioning fails — that is a deliberate choice recorded in section 7.3, and the
  `Project` is then in a state the operator can see and repair rather than a phantom.
- **E**: `not_permitted`; `UniqueViolationError` on a tenant-scoped slug collision ->
  `conflict`; `InvalidIdentifierError` -> `invalid_request`.
- **IDEM**: **required** on the client key. The contract (section 12) requires the key to
  be combined with tenant + operation + resource. A duplicate with the same key returns
  the original project rather than a second one. Needs a decision on where the ledger
  lives (section 9, Q-4).

### AS-4 — prepare a run

- **I**: `project_id` (claim), a task description or `task_id`, and requested execution
  parameters that may only **narrow** what the server resolves.
- **ID**: principal.
- **AZ**: steps 1-6, plus the operation's required authority for starting a run.
- **R**: reads via `tenant(...)`: project, its organization, the organization's provider
  accounts if a provider was named.
- **SE**: **none**. "Prepare" is pure resolution: it produces a validated, server-derived
  execution plan (project, workspace root, profile, provider account reference,
  acceptance expectations). Keeping it side-effect-free is what makes AS-5 cheap to
  refuse.
- **TX**: read-only; may be a single read transaction or several.
- **E**: `not_found` for a project outside the bound tenant; `not_permitted`;
  `provider_unavailable` if the resolved credential reference cannot be satisfied
  (the reference itself is *not* resolved here — resolution is Core-side, R-2).
- **IDEM**: not applicable (no effect).

### AS-5 — check authority before launch

- **I**: the output of AS-4.
- **ID**: principal.
- **AZ**: the full 4.1 algorithm, re-evaluated **at this moment**, not reused from AS-4.
  Authority is decided before the run starts and frozen for its duration (contract
  section 8); a decision made in an earlier request must not be replayed.
- **R**: `tenant(...)` reads: membership status, organization status, project status and
  ownership.
- **SE**: none.
- **TX**: one read transaction.
- **E**: `not_permitted`, `ambiguous_organization`, `not_found`, `subject_not_admissible`.
- **IDEM**: not applicable.

This use case exists as a separate step so that the decision is a **single named
operation** with one implementation. Splitting it from AS-4 also means a future
authorization engine can be swapped in without touching plan construction.

### AS-6 — create the Platform `RunRecord`

- **I**: the validated plan from AS-5, plus the client idempotency key.
- **ID**: principal (becomes `initiated_by_user_id`).
- **AZ**: already decided by AS-5 in the same request; the write itself additionally
  relies on Step 3 containment.
- **R**: `tenant(...)` -> `run_records.create_queued(run_id, project_id, task_id=...)`.
  `run_id` is generated server-side.
- **SE**: one `RunRecord` in `queued`. **No** `core_run_id`: a queued run has no Core
  identity by contract (Step 2 CHECK, and Step 3 refuses to set one).
- **TX**: one tenant transaction containing AS-6 **and** AS-7 (section 7.2).
- **E**: `not_found` for the project; `UniqueViolationError` -> `conflict`;
  `transaction_aborted` if a caught failure preceded it (Step 3 H-1 will refuse the
  commit).
- **IDEM**: the client key is combined with organization + `project_id` + task identity.
  A repeat must resolve to the same `RunRecord`, not a second one. Requires Q-4.

### AS-7 — hand the trusted request to Core

- **I**: the `TrustedExecutionRequest` built by L3 (section 5). **Not** a client DTO.
- **ID**: the principal is already inside the request as one audit scalar.
- **AZ**: none additional. Platform may **deny** a launch; it may not expand Core's
  authority (contract section 16.13). Core re-evaluates its own chain.
- **R**: none for the decision. The `RunRecord` is `claim`ed in the same transaction that
  records Core's identity.
- **SE**: a Core run is started; the `RunRecord` moves `queued -> running` with
  `core_run_id`, `started_at` and `initiated_by_user_id` set together (Step 3 `claim`).
- **TX**: see 7.2. The **ordering problem** — a durable Platform row must exist before
  Core can carry its id, but a Core run started before the Platform commit could outlive a
  rolled-back row — is analysed in 7.2 and is a decision, not an oversight.
- **E**: Core refuses -> `RunRecord` is failed with a classification; the run is **not**
  left `running` (R-6 requires a mechanism against that, and it is still open — Q-3).
  `ConcurrentModificationError` if another worker claimed the run first -> `conflict`,
  which is the correct answer for a duplicate launch.
- **IDEM**: Core has its own local idempotency hierarchy (contract section 12), which Step
  4 does **not** change. The Platform-side protection is the `RunRecord` claim's
  compare-and-set.

### AS-8 — accept the result and record usage

- **I**: a Core-side completion signal plus `PhysicalTelemetry`. **Not** a client DTO:
  this use case is not reachable from a client request at all.
- **ID**: not a user principal. It is a **server-side result intake**, and it must be
  authenticated as such (a service identity, `subject_kind = service`) or be an
  in-process call — this is a decision, Q-5.
- **AZ**: the telemetry's `run_id` (Core correlation) must resolve to a `RunRecord` in a
  tenant the intake is allowed to touch. Unresolvable -> refuse and record, never guess.
- **R**: `tenant(...)` -> `run_records.find_by_core_run_id`, `run_records.finish`,
  `run_records.increment_attempt_count`, `usage_records.append`.
- **SE**: lifecycle transition (`finish`, compare-and-set) **and** one `UsageRecord`.
  Both, or the run is left in a state that says so.
- **TX**: see 7.4. The `RunRecord` transition and the `UsageRecord` insert are one
  transaction **when both are available**. When telemetry never arrives, the run is
  finished with a classification that records the absence — the fail-closed direction
  required by `D-PLATFORM-13` / contract section 15, and the only direction that will
  still be correct once Billing exists.
- **E**: `not_found` (unknown `core_run_id`) -> refuse and record, never create a run;
  `ConcurrentModificationError` on a duplicate completion -> treat as already recorded,
  not as an error that retries; `UniqueViolationError` on
  `(run_record_id, attempt_number)` -> the attempt was already recorded, so it is a
  duplicate, not a conflict.
- **IDEM**: `UNIQUE(run_record_id, attempt_number)` (O-8, resolved) is the authority. A
  redelivered telemetry record must be a no-op, not a second `UsageRecord`.

### AS-9 — error handling and partially completed operations

This is a **cross-cutting contract**, not a tenth endpoint.

- Every refusal in 4.5 maps to exactly one stable error code, and the codes are the API's
  contract.
- **No partial write may be presented as success.** Step 3's H-1 guarantee (a caught
  statement failure cannot be committed silently) is what makes this enforceable: an L3
  that swallows a repository error and exits its Unit of Work cleanly now receives
  `TransactionError`, and the operation fails.
- **Unknown-state outcomes are stated, not hidden.** Two exist and both are named in
  section 7.5: a committed `RunRecord` whose Core launch failed, and a Core run whose
  telemetry never arrived. Each has a recorded, repairable state; neither is silently
  retried.
- A client-visible error never discloses the existence of another tenant, another user,
  or another project (section 6.3).

---

## 6. Error model

### 6.1 Two error vocabularies, deliberately separate

| Vocabulary | Belongs to | Example |
| --- | --- | --- |
| `PersistenceError` hierarchy (Step 3) | L2, storage | `UniqueViolationError`, `ConcurrentModificationError` |
| `ApplicationError` hierarchy (L3, **not implemented**) | use cases | `not_permitted`, `not_found`, `conflict`, `invalid_request` |

L3 **translates** L2 errors; it never leaks them. An API layer that had to catch
`UniqueViolationError` would be coupled to the database, and the error would change
meaning the first time the schema changed.

### 6.2 The refusal vocabulary

Closed list, because an open one grows accidentally:

| Code | Meaning | HTTP analogue (for the future surface) |
| --- | --- | --- |
| `unauthenticated` | no usable principal | 401 |
| `subject_not_admissible` | principal exists, subject may not act | 403 |
| `not_permitted` | no membership, no role, or wrong tenant | 404 or 403, per 6.3 |
| `ambiguous_organization` | an organization must be chosen and was not | 400 |
| `not_found` | the named resource is not in the bound tenant | 404 |
| `conflict` | compare-and-set lost, or a duplicate key | 409 |
| `invalid_request` | the request itself is malformed | 400 |
| `transaction_aborted` | an earlier caught failure poisoned the transaction | 500 |
| `unavailable` | a dependency refused or was unreachable | 503 |

### 6.3 Non-disclosure, and the one place it conflicts with clarity

Contract section 18 requires that a client cannot select another organization or project.
Two ways to deny exist, and they differ:

- **`not_found`** does not disclose whether the resource exists.
- **`not_permitted`** discloses that it exists but is not yours.

**Rule adopted here:** a cross-tenant attempt is answered as **`not_found`**, because
answering `not_permitted` would let a caller enumerate other tenants' identifiers. Within
the caller's own tenant, a genuine permission shortfall is `not_permitted`, because there
the existence is already known to the caller and hiding it would only confuse.

Stated because it is a real tension: this makes cross-tenant and
does-not-exist indistinguishable in the API, which is a small usability cost paid for a
disclosure guarantee. It is a **contract decision recorded here**, and section 9 lists it
as Q-6 in case the owner prefers clarity.

### 6.4 Error content

A client-visible error carries a code, a stable message, and nothing else. It never
carries: the queried identifier, the constraint name, the SQLSTATE, another tenant's
identifier, a count of matching rows, or a stack trace. The **internal** record may carry
all of those, because the operator needs them.

---

## 7. Transaction boundaries

### 7.1 The rule

**After a successful authentication is mocked**, the question a transaction boundary must
answer is: *if the process dies here, what state is left, and can an operator see it?*
One transaction per **use case**, opened only after authorization succeeds (step 8 of
4.1), is the default. A use case that spans two transactions must name its intermediate
state, and the state must be visible and repairable.

### 7.2 AS-6 + AS-7: the ordering problem, stated rather than assumed

A durable Platform `RunRecord` must exist before Core can be told about it, and the
`RunRecord` must carry Core's `core_run_id`, which does not exist until Core starts. Three
orderings are possible, and none is free:

| Ordering | Failure mode | Consequence |
| --- | --- | --- |
| commit `RunRecord`, then launch Core, then claim | launch succeeds but the claim fails | a Core run exists that Platform believes is `queued`. **Orphan.** |
| `RunRecord` + launch inside one transaction | the transaction rolls back after Core started | a Core run exists with **no** Platform row at all. **Worse orphan.** |
| launch Core, then write everything | the write fails | a Core run with no Platform row. **Worst**: Platform never knew. |

**Recommended for the first implementation:** the first ordering, with the orphan made
**visible** rather than impossible — a `queued` `RunRecord` whose launch was attempted
carries a marker, and a reconciliation sweep is the R-6 mechanism. Rationale: it is the
only ordering in which every outcome leaves a Platform row, and R-6 already mandates that
"a `RunRecord` must not remain indefinitely running after worker loss". This is a
**proposal** (Q-3), because R-6 explicitly leaves the mechanism open.

What is **not** acceptable in any ordering: a Core execution whose existence Platform
cannot discover. That would break invariant 12 (Core never polls Platform for authority)
in the other direction — Platform could never account for what Core did.

### 7.3 AS-3: project row and workspace root

The `Project` row commits first; workspace provisioning follows. A failure leaves a
`Project` with no usable workspace, which is visible (the row exists, `workspace_ref` is
empty) and repairable. The reverse — workspace created, row rolled back — leaves an
untracked directory, which is invisible. Prefer the visible failure.

### 7.4 AS-8: lifecycle transition and usage record

Both in one transaction when both inputs are present. If the lifecycle transition
succeeds and the `UsageRecord` insert fails on a constraint, the transaction **must** roll
back — a run marked `succeeded` with its measurement silently missing is exactly the state
`D-PLATFORM-13` says must be financially incomplete, and the cheapest way to honour that
is to refuse to record the success at all.

If telemetry never arrives, the run is finished with a classification that records the
absence. The classification vocabulary is O-7, still open (section 9, Q-7).

### 7.5 Named unknown-state outcomes

| Outcome | State left | Repair |
| --- | --- | --- |
| Platform row committed, Core launch failed | `RunRecord.queued` (or `failed` with a classification) | retry the launch, or fail the run |
| Core run started, Platform claim failed | `RunRecord.queued` for a run Core has run | reconciliation: match by correlation, then claim or fail |
| Core run finished, telemetry never arrived | `RunRecord.running` past its expected duration | reconciliation sweep, then terminal classification |
| catch a repository error and continue | transaction aborted; commit refused (Step 3 H-1) | none needed — the operation fails loudly |

Every row is a state an operator can query. None is silent.

---

## 8. M-2 — subject-bound discovery, and what each layer guarantees

Step 3's hardening pass (task #61) established that the subject binding is enforced at the
**session/adapter** layer, not by the RLS policy, and the reason was measured: a subject
predicate in the policy makes `INSERT ... RETURNING` unsatisfiable for bootstrap, because
`RETURNING` also consults the SELECT policies. That stands. What follows is what L3 must
therefore add, and what must be tested.

### 8.1 The guarantee, per layer

| Layer | Guarantee | Not a guarantee |
| --- | --- | --- |
| L1 policy | the system scope must be declared, or discovery reads nothing | *which* subject is being discovered |
| L2 `Session` | one subject per transaction; a read naming a different subject raises `TransactionError`; `users.list_all` does not exist | that the subject named is *authentic* |
| L3 Application Services | the subject passed to `pre_tenant` is the principal's `subject_id`, and nothing else | anything about the credential |
| L4 verifier | the credential really belongs to that subject | tenancy or authorization |

**The load-bearing sentence:** L2 enforces *"one subject, and always the same one"*; L4
enforces *"this subject is who the caller is"*. Neither alone is the boundary; the
composition is.

### 8.2 Requirements on L3

1. **Only a verified principal may supply a subject.** `pre_tenant(user_id=...)` is called
   with `principal.subject_id` and with nothing derived from the request body.
2. **A client cannot change the subject inside a trusted scenario.** No use case accepts a
   `user_id` input field. A use case that seems to need one (an administrative "inspect
   another user") would be a separate operation with its own authorization **and** its own
   principal — never a parameter on AS-1.
3. **SYSTEM credentials and SYSTEM scope are off the ordinary path.** L3's default
   composition holds a `PlatformDatabase` built on the **tenant login**; the system login
   is a different `PlatformDatabase`, constructed only by the administrative composition
   path, and no use case in section 5 reaches `system_provider_accounts`,
   `system_users` or `system_memberships`. `ProviderAccount` resolution for a run reads
   **tenant-owned** rows only.
4. **Non-disclosure.** Errors and responses must not reveal the existence of another user,
   organization, or membership. Concretely: AS-1 returns only the caller's own
   organizations; no lookup-by-identifier is offered for users; a cross-tenant identifier
   is answered `not_found` (6.3); and a discovery read for a different subject never
   reaches SQL, so it cannot produce a distinguishing database error.

### 8.3 Tests to be written in the implementation step

Each is a real test, not a placeholder. Names are proposals.

| # | Test | Asserts |
| --- | --- | --- |
| M2-1 | `test_discovery_uses_principal_subject_only` | AS-1 calls `pre_tenant` with `principal.subject_id`; a request body containing `user_id` does not change it |
| M2-2 | `test_no_use_case_accepts_a_subject_parameter` | an introspection test over the AS-1..AS-9 input DTOs finds no `user_id` field |
| M2-3 | `test_discovery_refuses_a_second_subject_in_one_transaction` | a second `list_for_user` for another subject raises `TransactionError` inside one `pre_tenant` transaction (this is L2's guarantee, re-asserted through L3) |
| M2-4 | `test_client_cannot_read_another_users_organizations` | AS-1 for subject A never returns an organization reachable only through subject B |
| M2-5 | `test_cross_tenant_identifier_is_reported_as_not_found` | AS-2/AS-4 with another tenant's `organization_id`/`project_id` yields `not_found`, not `not_permitted` |
| M2-6 | `test_tenant_login_cannot_reach_system_credentials` | the default composition's pool cannot read `system_provider_accounts` (L1 `PermissionDeniedError`), and no use case attempts it |
| M2-7 | `test_errors_do_not_disclose_other_tenants` | message and code for a cross-tenant attempt are byte-identical to those for a non-existent identifier |
| M2-8 | `test_unauthenticated_request_never_reaches_discovery` | a missing/invalid credential refuses before any Unit of Work is opened (asserted by a repository spy that records zero calls) |

M2-8 is the one that proves ordering rather than intent: it fails if a future refactor
opens the transaction before authenticating.

---

## 9. Open decisions

O-1..O-13 in the contract remain open/deferred except O-8 (resolved) and O-9 (resolved and
implemented). This step **closes none of them**. What follows is exactly what Step 4 needs
from each relevant one, with options and a recommendation. **Every recommendation here
needs the owner's decision; none is implemented.**

### Q-1 — the API-key authority model (contract R-5, O-1) — **owner decision required**

Two models are permitted and exactly one must be chosen before step 8.

| Option | Consequence |
| --- | --- |
| **A** organization-scoped service principal | a key outlives its creator's membership; revocation is purely a key operation; an offboarded employee's key keeps working until someone revokes it |
| **B** creator-membership-derived credential | offboarding is one action; but a key silently stops working when its creator's role changes, which is a behaviour change an integrator must expect |

**Recommendation:** **B**, with the R-5 obligation made concrete (revoking or inactivating
the creator's membership revokes or disables the derived keys, deterministically and
observably) and with a compensating rule that a key records its own `status` so an
operator can revoke a key directly too. Rationale: the forbidden state in R-5 — "creator
loses membership while a key they created silently retains authority" — is the failure
mode most likely to be an actual security incident, and B makes it structurally
impossible rather than requiring a sweep. **This is not decided here.** `principal.py`
records `subject_kind = api_key` so that either model can be implemented later without
changing the principal contract.

### Q-2 — the first role requirement matrix — **owner decision required**

Section 4.4 proposes a matrix. The parts that need explicit approval: whether `MEMBER` may
start a run, and who may create a project. `BILLING` has no Step 4 operation at all.

### Q-3 — the R-6 mechanism against indefinitely-running runs — **owner decision required**

R-6 requires the mechanism to exist but leaves it open (lease, heartbeat, timeout, or
reconciliation sweep). Section 7.2 recommends reconciliation with a visible `queued`
marker. **Not decided here.** The consequence of deferring it: AS-7 cannot be implemented
without at least choosing where the marker lives.

### Q-4 — where Platform idempotency is recorded — **open**

The contract fixes *what* the Platform idempotency identity includes (tenant, operation
class, server-derived resource, client key) but not *where* it is stored. Options: a
dedicated table, a unique constraint on an existing table, or a ledger. Step 3 built no
such table, and adding one is a schema change requiring its own migration and review
decision. **Consequence of deferring:** AS-3 and AS-6 cannot be made idempotent, so the
first implementation must either refuse to promise idempotency or accept duplicates.

### Q-5 — how AS-8 is authenticated — **open**

The result intake is server-side, not client-facing. Options: an in-process call with no
transport (then "authenticated" means "not reachable from the HTTP surface"), or a service
principal (`subject_kind = service`) over the same verifier port. **Not decided.** The
safe interim answer is in-process only.

### Q-6 — cross-tenant attempts answered as `not_found` rather than `not_permitted` — **decision recorded, and the owner may reverse it**

Section 6.3 adopts `not_found` for disclosure reasons and names the usability cost. This is
a contract choice made in this document; it is listed here so that it is visible as a
choice rather than discovered as behaviour.

### Q-7 — the `RunRecord` status vocabulary and the telemetry-absence classification — **O-7, still open**

Step 3's compare-and-set uses a deliberately conservative terminal set and says O-7 remains
open. Section 7.4 needs a classification for "telemetry never arrived"; that is an O-7
value and is **not** invented here. The interim behaviour is to use the existing
`failure_classification` text field, which the schema already permits, and to leave the
enum alone.

### Explicitly **not** closed, restated so it cannot be closed by accident

- API-key token format, hashing, prefix, expiry semantics (O-1);
- the authentication mechanism itself — session vs JWT vs federated (O-2);
- KMS/Vault vendor and `secret_ref` grammar (O-3, O-4);
- workspace filesystem topology (O-5);
- the Platform -> Core transport (O-6) — section 5 defines the **contract**, not the
  transport;
- the full `RunRecord` lifecycle (O-7);
- any idempotency semantics beyond what the contract already fixed (Q-4);
- the complete RBAC model (Q-2);
- Billing, Wallet, Pricing, Partner, Reseller.

---

## 10. The Platform -> Core trusted execution boundary

This is the key architectural point of Step 4, so it is stated as a contract with an
explicit typed shape, a mapping to the Core contracts that **already exist**, and a
sequence.

### 10.1 The Core contracts this port must respect

Verified in the repository, not assumed:

| Core contract | Where | Property that binds the port |
| --- | --- | --- |
| `ExecutionCoordinator.execute(request, *, workspace_root, run_id, allowed_commands, approval_policy, approval_resolver, run_scope, observer)` | `app/execution/authorizer.py` | the strict chain: validate -> permission -> intent -> approval -> policy -> **workspace_root required** -> `AuthorizedExecution` -> dispatch |
| `ExecutionIntent` / `IntentBuilder` | `app/execution/intent.py` | immutable; canonical `fingerprint` |
| `AuthorizedExecution` | `app/execution/intent.py` | creatable **only** with a module-private coordinator sentinel; `is_valid()` re-checks the fingerprint |
| `RunScope` / `frozen_scope` / `require_active_scope` | `app/runtime/run_scope.py` | a frozen perimeter, not a permission; validates workspace root, request, and command set |
| `PhysicalTelemetry` / `PhysicalTelemetrySink` | `app/agent_runtime/physical_telemetry.py` | 14 physical fields; `run_id` is the Core correlation identity |
| `WorkspaceBoundary` / `EphemeralWorkspaceManager` | `app/execution/sandbox.py`, `app/execution/workspace_manager.py` | canonical containment; per-run scratch isolation (`_isolate_workspace`) |

**No `TrustedExecutionRequest` exists in code.** Confirmed by searching `app/`: the name
appears only in documentation. This step defines the contract; step 10 implements it.

### 10.2 The draft shape

Proposed as a **frozen dataclass** in a future `app/platform/application/` (L3) module.
Three groups, and the grouping is the contract:

**Group 1 — trusted, server-derived, and never client-supplied**

| Field | Source | Why trusted |
| --- | --- | --- |
| `platform_run_id` | server (`new_id()`) | Platform's own identity (R-1) |
| `organization_id` | resolved membership | contract R-8.16 |
| `project_id` | resolved and ownership-checked project | contract section 4 |
| `workspace_root` | server-derived from organization + project | contract section 6; R-4 isolation |
| `execution_profile` | server-resolved profile (its authority ceiling is Core's) | Core owns the ceiling |
| `intent` | `ExecutionIntent` built server-side | Core's immutable value; portable |
| `run_scope` | the frozen `RunScope` | Core's perimeter |
| `idempotency_identity` | derived from tenant + operation + resource + client key | contract section 12 |
| `acceptance_expectations` | server-resolved criteria | traceability, not authority |
| `credential_reference` | **opaque** reference (or `None`) | R-2: never plaintext |
| `initiated_by_user_id` | the principal's `subject_id` | audit-only |

**Group 2 — correlation only, never authority**

`core_run_id` does not exist yet at construction time; Core produces it and returns it in
`PhysicalTelemetry.run_id` (R-1). The request carries `platform_run_id`, and the returned
`core_run_id` is stored beside it. The two are **never** interchangeable, and
`UsageRecord` must carry both under distinct names (R-1). A future `request_id` for
transport correlation is likewise correlation, not authority.

**Group 3 — must never appear in the request at all**

| Forbidden | Because |
| --- | --- |
| plaintext provider secret, in any spelling | contract section 5; R-8.19. A field like `provider_secret_environ: Mapping[str, str]` is **explicitly rejected** by the contract |
| a client-supplied `workspace_root` or path | contract section 6 (workspace is server-derived) |
| a client-supplied command, argv, or tool set that **widens** the resolved profile | contract section 18 ("cannot inject arbitrary command, tool, or network authority") |
| client-supplied `organization_id`, `project_id`, `user_id` passed through unchanged | contract section 4; these are resolved, and a disagreement is refused |
| a client-supplied allowed-command set | same |
| the client's raw credential or token | nothing downstream of L3 needs it |
| `APIKey.key_prefix`, session ids, hashes | irrelevant to execution |

### 10.3 Core's authority is never expanded by Platform

The direction is one-way, and it is enforced by Core's own code, not by Platform's
promise:

```
Platform resolves and MAY NARROW:
    profile            -> server-selected profile (its ceiling is Core's)
    allowed_commands   -> a subset of the profile's
    allowed_tool_ids   -> a subset of the profile's
    network_access     -> the profile's value; a client may only ask for less
    run_scope          -> frozen before dispatch, validated by ExecutionCoordinator
        │
        ▼
ExecutionCoordinator re-derives everything:
    RunScope.frozen_scope / require_active_scope   -> refuses a mismatched run id
    scope.validate_workspace_root(workspace_root)  -> refuses a foreign root
    scope.validate_execution_request(request)      -> full command identity, pinned argv
    scope.validate_command_set(...)                -> the caller may only NARROW further
    ExecutionPolicy                                -> Core's own policy
    ApprovalPolicy + resolver                      -> fingerprint-bound, single-use
    workspace_root required, absolute, a directory -> fail-closed
        │
        ▼
AuthorizedExecution   (only ExecutionCoordinator can mint it)
        │
        ▼
ExecutionBackend / LocalExecutionAdapter -> effect
```

Explicit statements:

- **The client can never create an `AuthorizedExecution`.** The sentinel makes it
  impossible outside the coordinator module, and `is_valid()` re-checks the intent
  fingerprint so a mutated token is refused.
- **The client can never bypass `ExecutionCoordinator`.** It is the only producer of the
  marker, and the adapters accept only that type.
- **Platform cannot grant authority Core withholds.** If Core's policy refuses, the run is
  refused; Platform has no channel to override it, and adding one would violate invariant
  13.
- **Platform's own refusals happen first**, because a denied launch should not consume
  Core resources or reveal anything to the client.

### 10.4 Workspace isolation

R-4 requires every `Project`'s workspace root to be isolated from **all other projects',
including within one organization**. The port therefore carries a
**server-derived** root, and Core's `WorkspaceBoundary` canonicalizes and contains it.

Step 4 does **not** select a topology (O-5 is open, and the contract explicitly refuses to
bake in a Linux path). What Step 4 fixes:

- the root is derived from `(organization_id, project_id)` by a server-side function, never
  from a client value;
- two projects must not share a root, and a run in project A must not reach project B's
  files — enforced by Core's boundary on whatever root the server derives;
- the derivation function is a **single** function, so the invariant has one place to be
  wrong.

`EphemeralWorkspaceManager` and `_isolate_workspace` already provide per-run scratch
isolation for adapters that use it; the port must not fight it. When the adapter isolates,
`ExecutionCoordinator` deliberately does not scope-validate the workspace root, because the
private scratch directory is not a scope-checkable property — the isolation is provided
independently. The port must therefore treat "who owns the workspace root" as Core's
question, and supply the durable **project** root rather than a per-run temporary one.

### 10.5 Secrets

R-2 is explicit, and Step 4 only restates it as a boundary:

```
TrustedExecutionRequest
    -> opaque credential reference            (never plaintext, never a vendor grammar)
    -> Core execution boundary
    -> abstract EphemeralSecretResolver       (port; Core-side interface)
    -> injected adapter                       (supplied by the Platform composition layer)
    -> ephemeral in-memory credential
    -> provider invocation
```

- The port carries an **opaque handle**. `ProviderAccount.secret_ref` is the existing seed
  of that abstraction and is **extended, not replaced** (contract section 5).
- **`EphemeralSecretResolver` is not implemented here.** It is named in the contract
  (R-2) and does not exist in `app/`; Step 4 confirms the boundary and leaves the
  implementation to step 7.
- Core's side of the port imports no Platform module, no KMS/vault SDK, no database driver
  and no payment library (R-2). The adapter is injected by the composition layer, which is
  what keeps the Core -> Platform direction empty.
- A plaintext secret never enters the request, a `RunRecord`, a `UsageRecord`,
  `PhysicalTelemetry`, an ordinary API response, or a log.

### 10.6 The sequence, from verified identity to effect

```
  client
    │  1. credential                     (the ONLY thing a client contributes about itself)
    ▼
  L4 AuthenticationVerifier
    │  2. AuthenticatedPrincipal         (subject_id, subject_kind, method, verified_at)
    ▼
  L3 Application Service (e.g. AS-4 -> AS-5 -> AS-6 -> AS-7)
    │  3. pre_tenant(user_id=principal.subject_id)      -> candidate organizations
    │  4. bind ONE organization (client claim must be IN the candidate set)
    │  5. tenant(org, user_id=principal.subject_id)
    │        membership ACTIVE? organization ACTIVE? role sufficient?
    │  6. project exists AND project.organization_id == bound organization?
    │  7. derive workspace_root + execution_profile + allowed sets   (server-side only)
    │  8. BEGIN
    │        run_records.create_queued(run_id, project_id)   -> platform_run_id
    │     COMMIT
    │  9. build TrustedExecutionRequest (Group 1 above; opaque credential reference)
    ▼
  Platform -> Core port            (transport: O-6, OPEN — not decided here)
    ▼
  Core: ExecutionCoordinator.execute(request, workspace_root=..., run_id=..., run_scope=...)
    │ 10. scope/workspace/request/command validation, policy, approval
    │ 11. AuthorizedExecution  (coordinator-only marker)
    │ 12. adapter dispatch -> EphemeralSecretResolver (injected) -> ephemeral credential
    │ 13. effect; Core writes its own RunStore observation
    │ 14. Core emits PhysicalTelemetry(run_id = core_run_id, attempt_number, ...)
    ▼
  Platform result intake (AS-8)
    │ 15. BEGIN
    │        run_records.claim(...) or finish(..., expected_status=...)
    │        usage_records.append(...)                (UNIQUE(run_record_id, attempt_number))
    │     COMMIT
    ▼
  durable Platform lifecycle truth + measurement (no money anywhere)
```

Trust classification of every value that crosses the boundary:

| Value | Classification |
| --- | --- |
| `workspace_root`, `execution_profile`, `intent`, `run_scope`, allowed sets | **trusted, server-derived** |
| `platform_run_id` | **trusted**; Platform's own identifier |
| `organization_id`, `project_id`, `initiated_by_user_id` | **trusted as data, never as authority** (contract section 5) — they are audit and correlation scalars, and Core must not use them to decide anything |
| `credential_reference` | **trusted as a handle**; carries no secret and no vendor grammar |
| `core_run_id` | **correlation only**, produced by Core, never Platform-chosen |
| `attempt_number`, `PhysicalTelemetry` fields | **measurement**; not money, not authority |
| client `organization_id` / `project_id` / `user_id` / paths / command | **not admitted** to the request at all |

---

## 11. Test strategy

### 11.1 What this step ships

`tests/test_platform_auth_contracts.py` — contract tests for the authentication boundary,
run without PostgreSQL, because they assert the *shape* of the trust boundary and not
database behaviour. They use a **fake** verifier, which is legitimate here: the point is
that the port is the only way in, not that a credential check works (no credential check
ships).

| Test | Asserts |
| --- | --- |
| principal is frozen | `dataclasses.FrozenInstanceError` on mutation |
| principal carries no authority | no field named `organization_id`, `project_id`, `role`, `scopes`, or `workspace` |
| only the factory can create one | `create()` without the module sentinel raises `PermissionError` |
| a principal must be self-consistent | empty/blank `subject_id` refused; unknown `subject_kind`/`method` refused |
| a fake verifier produces a principal | the port is usable, so the contract is not vacuous |
| a refused verification yields **no** principal | `AuthenticationError` carries no principal and no subject id |
| a principal is not an authorization | the 4.1 algorithm's inputs are not present on the type |
| `app/` layering | nothing outside `app/platform/` imports the principal module; the principal module imports no driver, no Core module, and no HTTP framework |

### 11.2 What later steps must add

The 8 M2 tests in section 8.3, the 18 required invariants of contract section 18, and the

per-use-case tests. Those need a live database and the implemented services, so they are
listed as the deliverable of the implementation step, not fabricated now.

**No fake service that pretends authorization is finished is added.** That would make the
suite pass while proving nothing, which the brief explicitly forbids.

---

## 12. Minimal next implementation task

Scoped so that it can be reviewed and reverted as one unit. **Not started in this step.**

**Objective:** the first Application Service — read-only discovery (AS-1, AS-2) — proving
the authentication boundary and the authorization ordering end to end, with the
authorization engine reduced to the single rule "an ACTIVE membership is required".

**Deliverables.**

1. `app/platform/application/` package with:
   - `errors.py` — the `ApplicationError` hierarchy and the closed code list of 6.2;
   - `context.py` — `AuthorizedContext` (principal + bound `organization_id` +
     `membership`), constructible only by the resolver;
   - `authorization.py` — the 4.1 algorithm as an ordered function, returning a refusal or
     a context, with **no** SQL of its own: it composes repository calls;
   - `organizations.py` — AS-1 and AS-2;
   - `__init__.py`.
2. One authentication **port implementation for tests only**, in `tests/`, that maps a
   fixed token to a fixed subject — never in `app/`, so it cannot be mistaken for the real
   mechanism.
3. `tests/test_platform_application_discovery.py`, live-PostgreSQL, covering: AS-1 returns
   only the caller's organizations; AS-2 refuses an organization the caller has no ACTIVE
   membership in; a cross-tenant identifier yields `not_found` (M2-5); a suspended user is
   refused; an inactive membership is refused; **M2-8** — a refused authentication opens no
   Unit of Work; and **M2-2** — no DTO in the package has a `user_id` field.
4. `docs/STAGE-1-STEP-4-AUTH-APPLICATION-SERVICES-DESIGN.md` updated with what was learned
   — in particular the real `AuthorizedContext` shape.

**Explicitly not in that task:** project creation, run preparation, `RunRecord`, the
Platform -> Core port, API keys, member administration, provider accounts, the HTTP
surface, and any schema change. Q-3 and Q-4 must be answered before AS-3 and AS-6 can be
implemented, so the first task deliberately avoids both.

**Acceptance.** The four deliverables exist; the new suite passes against live PostgreSQL
17; the M2-8 ordering test fails if the transaction is opened before authentication (proven
by deliberately inverting the order once and observing the failure); `compileall` and
`git diff --check` are clean; no file outside `app/platform/application/`, `tests/`, and
the named document changes.

---

## STATUS

```
Stage 0.1  =  FROZEN / GO

Stage 1    =  ARCHITECTURE CONTRACT READY
Step 1     =  DOMAIN CONTRACTS READY
Step 2     =  POSTGRESQL SCHEMA AND RLS IMPLEMENTED
Step 3     =  PERSISTENCE, REPOSITORIES, UNIT OF WORK IMPLEMENTED
Step 4     =  AUTHENTICATION AND APPLICATION SERVICES CONTRACTS DESIGNED
              (principal contract implemented; services NOT implemented)

Implementation status:  DESIGN AND CONTRACTS ONLY
```

## OPEN DECISIONS

**Open, and not closed by this step:** O-1 API-key format and authority model (Q-1);
O-2 authentication mechanism; O-3 secret backend/KMS; O-4 `secret_ref` grammar;
O-5 workspace topology; O-6 Platform -> Core transport; O-7 `RunRecord` status enum (Q-7).

**Newly raised by this step, needing a decision before the step that depends on them:**
Q-2 the first role requirement matrix; Q-3 the R-6 mechanism against indefinitely-running
runs; Q-4 where Platform idempotency is recorded; Q-5 how the result intake is
authenticated.

**Decided in this document, and reversible only by an explicit decision:** Q-6 —
cross-tenant attempts are answered `not_found` rather than `not_permitted`, for
disclosure reasons.

**Resolved for contract:** O-8 — `UNIQUE(run_record_id, attempt_number)`.

**Resolved and implemented:** O-9 — the RLS design.

**Deferred:** O-10 pricing source; O-11 payment processor; O-12 tax/invoice; O-13
reseller/partner economics.

---

# Forge AI — Stage 1 / Шаг 4: контракты аутентификации и Application Services — русская версия

> **Статус.** Дизайн и контракты. Документ фиксирует **границу доверия** между
> проверенной идентичностью, разрешённой операцией и слоями персистентности и
> исполнения. Шаг 4 — это **шаг 4 из 13** в порядке реализации из
> [`STAGE-1-ARCHITECTURE-CONTRACT.md`](STAGE-1-ARCHITECTURE-CONTRACT.md), раздел 17
> («Аутентификация»); он неизбежно затрагивает шаг 5 (Membership / RBAC) и шаг 10
> (`TrustedExecutionRequest`), чтобы указать, где эти шаги присоединяются. Он их **не**
> реализует.
>
> **Что реализовано этим шагом:** ровно один модуль, `app/platform/principal.py`,
> который содержит *форму* проверенной идентичности и порт, через который она
> создаётся. Application Services, движок авторизации, HTTP-поверхность и порт
> Platform -> Core **не реализованы**, и никаких заглушек для них не существует.
>
> **Authority.** Нормативен `docs/STAGE-1-ARCHITECTURE-CONTRACT.md`. Разделы 1, 3, 4,
> 5, 6, 7, 8, 11, 12, 16, 17, 18 и уточнения R-1..R-8 применимы к этому шагу. При
> расхождении побеждает контракт, а этот документ неверен. Принятое записано в
> `docs/DECISIONS.md` (`D-PLATFORM-01..22`).

---

## 1. Область и два расхождения, обнаруженных при написании

### 1.1 В области

- **граница аутентификации**: что такое проверенная идентичность, кто вправе её
  создать и что никогда не может быть за неё принято;
- **поток авторизации**: как проверенная идентичность превращается в разрешённую
  операцию в одной организации и одном проекте;
- **контракты Application Services**: use cases, их входы, требования авторизации,
  границы транзакций и поведение при ошибках;
- **граница доверенного исполнения Platform -> Core** в терминах уже существующих
  контрактов Core;
- явный учёт **M-2** (subject-bound discovery) и того факта, что `forge.user_id` не
  является доказательством;
- перечень **открытых решений, которые этот шаг не должен закрывать**.

### 1.2 Вне области

HTTP-эндпоинты, реализация OAuth/OIDC/JWT, хранилище паролей, хранилище сессий,
формат или хеширование токенов API-ключей, интеграция с KMS/Vault, полная RBAC-модель,
реализация транспорта Platform -> Core, Billing и любые изменения цепочки авторизации
Core или персистентности Platform.

### 1.3 Расхождение 1 — `app/platform/domain/` не существует

В задании назван каталог `app/platform/domain/`. **Его не существует.** Доменные
контракты Platform лежат прямо в корне пакета:

```
app/platform/__init__.py      экспорты
app/platform/models.py        8 замороженных доменных записей + new_id() + utc_now()
app/platform/enums.py         8 словарей статусов и ролей
app/platform/persistence/     персистентность Шага 3
```

Зафиксировано, а не переистолковано молча: путь из задания разрешается в
`app/platform/models.py` + `app/platform/enums.py`. Каталог **не** создавался, чтобы
слова задания стали истинными: структура пакета не является контрактом, а лишний
уровень вложенности противоречил бы списку модулей, уже зафиксированному в
`D-PLATFORM-20`.

### 1.4 Расхождение 2 — направление зависимостей и место кода Шага 4

Раздел 1 контракта фиксирует направление **Core -> ничего из Platform**, а
`app/platform/__init__.py` утверждает, что Core никогда не импортирует пакет Platform.
Обратное направление — композиционный код Platform импортирует контракты Core — это и
есть назначение `TrustedExecutionRequest`, и раздел 1.4 ниже фиксирует, какие именно
имена модулей Core он вправе упоминать и почему это не создаёт зависимости
Core -> Platform.

Второе, более острое расхождение — **внутри Platform**: Application Services должны
находиться **над** персистентностью и быть единственным местом, принимающим решение об
авторизации. Ничего в Шаге 3 этого не делает, а дизайн Шага 3 намеренно оставляет
арендатора сессии, а решение — «слою выше». Этот документ и есть это «выше».

---

## 2. Границы ответственности слоёв

```
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ L5  HTTP-поверхность / клиенты                    НЕ РЕАЛИЗОВАНО          │
  │     может предъявить: креденл. Никогда: идентичность, авторизацию или      │
  │     любое серверно-выведенное значение.                                   │
  └───────────────────────────────┬──────────────────────────────────────────┘
                                  │ креденл (непрозрачный для всех ниже)
  ┌───────────────────────────────▼──────────────────────────────────────────┐
  │ L4  Authentication verifier                       ПОРТ ОПРЕДЕЛЁН (Шаг 4) │
  │     ЕДИНСТВЕННЫЙ компонент, вправе сказать «этот креденл — субъект X».    │
  │     Владеет: проверкой креденла. Производит: AuthenticatedPrincipal.      │
  │     НЕ владеет: авторизацией, арендой, доступом к БД.                     │
  └───────────────────────────────┬──────────────────────────────────────────┘
                                  │ AuthenticatedPrincipal (проверен)
  ┌───────────────────────────────▼──────────────────────────────────────────┐
  │ L3  Application Services                          НЕ РЕАЛИЗОВАНО        │
  │     use cases. Разрешает membership -> организацию -> проект, принимает   │
  │     решение об авторизации, владеет транзакциями и идемпотентностью,      │
  │     строит TrustedExecutionRequest. ЕДИНСТВЕННЫЙ слой, кто авторизует.    │
  └───────────────────────────────┬──────────────────────────────────────────┘
                                  │ вызовы репозиториев внутри выбранного скоупа
  ┌───────────────────────────────▼──────────────────────────────────────────┐
  │ L2  Персистентность (Шаг 3, реализовано)                                  │
  │     UnitOfWork + репозитории. Гарантии: корректность транзакций,          │
  │     containment, один субъект на транзакцию, колоночно-ограниченная       │
  │     запись идентичностей, compare-and-set жизненного цикла.               │
  │     НЕ владеет: бизнес-авторизацией. Это хранение и изоляция.             │
  └───────────────────────────────┬──────────────────────────────────────────┘
                                  │ SQL
  ┌───────────────────────────────▼──────────────────────────────────────────┐
  │ L1  PostgreSQL + FORCE RLS (Шаг 2, реализовано)                           │
  │     containment как последний рубеж. Считать, что в L3 и L2 есть баг.     │
  └──────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────────┐
  │ Плоскость исполнения Core (app/execution, app/runtime)  реализована,      │
  │ без изменений. Владеет: ExecutionCoordinator, ExecutionIntent,            │
  │ AuthorizedExecution, RunScope, WorkspaceBoundary. Core решает, что может  │
  │ исполняться. Platform может ОТКАЗАТЬ в запуске; она никогда не может      │
  │ РАСШИРИТЬ полномочия Core.                                                │
  └──────────────────────────────────────────────────────────────────────────┘
```

**Самое важное предложение документа.** Авторизация решается на **L3** из проверенного
principal (L4) плюс серверных данных (L2). Она **никогда** не решается на L2, а политики
L2 — это *подстраховка*, а не решение.

| Слой | Владеет | Не должен владеть |
| --- | --- | --- |
| L4 Аутентификация | проверкой креденла; созданием principal | авторизацией, арендой, доступом к репозиториям, `organization_id` |
| L3 Application Services | авторизацией; разрешением аренды; границей транзакции; идемпотентностью; построением запроса | проверкой креденла; SQL; семантикой RLS-политик |
| L2 Персистентность | транзакциями; containment; compare-and-set; нормализацией ошибок | бизнес-авторизацией; «является ли этот человек админом?» |
| L1 PostgreSQL | containment как рубеж; принуждением грантов и политик | любым бизнес-правилом, не выразимым как containment |
| Core | тем, исполняется ли эффект и как | идентичностью Platform, арендой, биллингом, жизненным циклом |

---

## 3. Аутентификация — граница доверия

### 3.1 Что такое проверенная идентичность

`AuthenticatedPrincipal` (`app/platform/principal.py`, реализован этим шагом) —
**замороженная** запись ровно с четырьмя полями:

| Поле | Значение | Доверенное? |
| --- | --- | --- |
| `subject_id` | `User.id` Platform, установленный верификатором | **доверенное**, и только потому, что его создал верификатор |
| `subject_kind` | `user`, `api_key` или `service` | **доверенное**; указывает, какая модель authority применима |
| `method` | `session`, `api_key` или `service` | **доверенное**; класс доказательства, а не само доказательство |
| `verified_at` | когда верификатор это установил | **доверенное**; для аудита и будущей политики свежести |

Намеренно **отсутствуют**: `organization_id`, `project_id`, роль, scopes, email,
display name, workspace и любой материал креденла. Principal отвечает на вопрос *кто*;
он никогда не отвечает на вопрос *что можно*. Каждое «что» разрешается на L3 для каждой
операции.

`subject_kind` существует потому, что контракт (R-5) оставляет открытыми две модели
authority API-ключа. Именование вида сейчас позволит L3 ветвиться по нему позже,
**не** обязывая этот модуль решать, какая модель победит — это решение владельца
(раздел 9).

### 3.2 Кто вправе создать principal

Один порт, один метод:

```python
class AuthenticationVerifier(Protocol):
    async def verify(self, credential: Credential) -> AuthenticatedPrincipal: ...
```

`AuthenticatedPrincipal.create(...)` требует модульно-приватный sentinel — тот же
механизм, который Core уже использует для `AuthorizedExecution` (см.
`app/execution/intent.py`). Поэтому principal не может быть создан вызывающим кодом,
который лишь импортировал тип; создать его может только фабрика внутри этого модуля, и
предполагается, что её вызывает только порт верификатора. Это намеренно **не**
криптографическая защита от внутрипроцессного кода — внутрипроцессный противник может
импортировать что угодно. Это структурная гарантия, что **никакой обычный путь исполнения
не может случайно создать идентичность**, поэтому «клиент прислал нам `user_id`» не
имеет маршрута к превращению в principal.

### 3.3 Что никогда нельзя принять за аутентификацию

Каждое из этих значений реально существует в репозитории, и каждое явно **не**
доказательство:

| Значение | Почему не доказательство | Где законно |
| --- | --- | --- |
| `user_id` от клиента | это утверждение, его выбрал клиент | нигде как идентичность; только как *запрос*, подлежащий разрешению и отказу |
| `forge.user_id` (GUC PostgreSQL) | любая сессия может вызвать `set_config` | ограничение discovery-чтения **после** того, как L4 назвал субъекта (раздел 8) |
| `forge.organization_id`, `forge.system_scope` | то же | ограничение скоупа транзакции, решаемое L3 |
| `APIKey.key_prefix` | идентификатор, не секрет | показ ключа его владельцу |
| `RunRecord.id`, `RunRecord.core_run_id` | непрозрачные идентификаторы | только корреляция (R-1) |
| `RunScope` | периметр, не разрешение | ограничение *где* происходит уже авторизованный эффект |
| строка `Membership` | данные аренды, не идентичность | разрешение authority **после** появления principal |

**`forge.user_id` — не аутентификация**, и это стоит сказать трижды, потому что дизайн
Шага 3, миграция `0015` и `D-PLATFORM-21` говорят это: GUC решает, о каком субъекте
транзакция *готова говорить*; он никогда не решает, *кем является* вызывающий. Шаг 3
обеспечивает первую половину (`Session.require_subject` отказывает второму субъекту).
Шаг 4 владеет второй половиной: назвать субъекта вправе только верификатор.

### 3.4 Где живёт сам механизм аутентификации и почему не здесь

Порт верификатора определён; **никакой реализации в Шаге 4 не поставляется**, потому что
O-2 открыт. Она не должна жить в `app/platform/persistence/`: персистентность никогда не
должна проверять пароль, JWT или внешний токен, а репозиторий, способный проверить
креденл, стал бы authority аутентификации по случайности.

Реализация принадлежит **L4** — композиционному/API-слою, владеющему и HTTP-поверхностью.
Когда она появится, она будет: читать креденл из транспорта; разрешать его в `subject_id`
(поиск сессии, сравнение хеша API-ключа или федеративное утверждение); подтверждать, что
субъект существует и его состояние вообще допускает аутентификацию
(`User.status == ACTIVE`; `SUSPENDED` не должен аутентифицироваться); вызывать
`AuthenticatedPrincipal.create(...)`; передавать principal — и ничего больше о
вызывающем — на L3.

### 3.5 Требования fail-closed к верификатору

Контракт порта, который реализация обязана соблюдать, а тесты раздела 10 будут проверять
на **fake**-верификаторе (никогда на реальной криптографии):

1. **Отсутствующий креденл** -> отказ. Никогда «анонимный principal».
2. **Некорректный креденл** -> отказ, и в сообщении, доходящем до клиента, «некорректный»
   не отличается от «неизвестный».
3. **Неизвестный субъект** -> отказ.
4. **Приостановленный или иным образом недопустимый субъект** -> отказ.
5. **Неоднозначный креденл** (ноль или более одного субъекта) -> отказ. Неоднозначность —
   это отказ, никогда не «взять первого».
6. **Верификатор недоступен** -> отказ. Сбой не должен деградировать в разрешающий путь.
7. Отказ поднимает `AuthenticationError` и возвращает **никакого** principal.

### 3.6 Модель угроз, изложенная, чтобы её не считали само собой разумеющейся

| Угроза | Останавливается | Не останавливается |
| --- | --- | --- |
| подделанный `user_id` от клиента | ни один путь кода не принимает его как идентичность | RLS (нет запроса без контекста) |
| украденный API-ключ | отзыв (`APIKey.status`), срок действия, наблюдение `last_used_at` | `key_prefix` |
| повторное предъявление токена | вне области Шага 4; нужен механизм из O-2 | ничем в этом документе |
| баг L3, выбравший не того арендатора | RLS (L1) отказывает чтению/записи | собственные unit-тесты L3 |
| враждебный внутрипроцессный вызывающий | ничем в Python; изоляция процессов не заявляется | sentinel (это защита от случайности, не граница) |

Последняя строка названа потому, что преувеличение здесь было бы самой лёгкой ложью в
документе. `AuthorizedExecution` обладает тем же свойством и говорит об этом в своём
docstring («это защита от случайности, а не от враждебного внутрипроцессного противника»).

---

## 4. Авторизация — организация и проект

### 4.1 Цепочка authority как алгоритм

Раздел 4 контракта допускает ровно один вывод. Как упорядоченный fail-closed алгоритм:

```
ВХОД: principal (проверен), операция, утверждения клиента (все недоверенные)

 1. principal существует и создан верификатором         иначе ОТКАЗ unauthenticated
 2. состояние субъекта допустимо                        иначе ОТКАЗ subject_not_admissible
 3. разрешить кандидатов: организации, достижимые через
    ACTIVE membership'ы субъекта                        пусто -> ОТКАЗ no_organization
 4. привязать ОДНУ организацию:
       - если клиент назвал — она должна быть в наборе кандидатов
       - иначе это персональная/единственная организация субъекта, либо
         операция требует явного выбора и отклоняется как неоднозначная
    названная клиентом организация вне набора -> ОТКАЗ not_permitted
                                                  (никогда «подставить правильную»)
 5. разрешить membership для (субъект, организация) -> должен быть ACTIVE
                                                       иначе ОТКАЗ not_permitted
 6. если операция называет проект: проект должен существовать И
    принадлежать привязанной организации                иначе ОТКАЗ not_permitted
 7. оценить требование операции к разрешению против
    роли из разрешённого membership                     иначе ОТКАЗ not_permitted
 8. ТОЛЬКО ТЕПЕРЬ открыть tenant-scoped Unit of Work и выполнить эффект
```

Шаги 1–7 — **чтения серверных данных**; шаг 8 — единственный шаг, который пишет. Отказ на
любом шаге происходит **до** появления tenant-scoped транзакции, поэтому отклонённая
операция не может оставить частичную запись.

### 4.2 Клиентские идентификаторы — утверждения, не authority

Раздел 4 контракта: *«CLIENT IDs ARE CLAIMS, NOT AUTHORITY»*. Конкретно для каждого
идентификатора, который может прислать клиент:

| Клиент присылает | Сервер делает | Если расходится с серверными данными |
| --- | --- | --- |
| `organization_id` | сверяет с ACTIVE membership'ами субъекта | отказ; никогда не подставлять молча |
| `project_id` | проверяет существование **и** `project.organization_id == привязанная организация` | отказ; проект в другой организации — это *не найдено*, а не *запрещено* (6.3) |
| `user_id` | игнорирует полностью; субъект берётся из principal | клиент вообще не может назвать субъекта |
| `workspace` / путь | игнорирует; workspace выводится сервером из привязанного проекта | клиент не может назвать путь (раздел 6 контракта) |
| команда, инструменты, сеть | может только **сузить** разрешённые `RunScope`/профиль | клиент никогда не расширяет; Core перепроверяет |
| `provider_account_id` / креденлы | может выбрать только среди аккаунтов привязанной организации | system-owned аккаунт недостижим (R-8.24) |
| `role` | игнорирует; вызывающий не может заявить собственную authority | — |
| ключ идемпотентности | комбинируется с арендатором + операцией + ресурсом (раздел 12 контракта) | голый ключ никогда не является tenant-wide authority |

Правило за таблицей: **сервер никогда не «ремонтирует» утверждение клиента.** Подстановка
«правильной» организации вместо неправильной — так рождается баг confused deputy: это учит
клиента, что неправильный идентификатор работает, и скрывает баг от оператора.

### 4.3 Разрешение membership

Единственный источник authority аренды — `Membership`.

- **Требуется**: строка для `(organization_id, user_id)` со `status == ACTIVE`.
- `MembershipStatus.INACTIVE` и `REVOKED` оба отказывают. Это разные состояния схемы
  (вернувшийся участник — смена статуса, а не вторая строка, Шаг 2 K-2), поэтому сервис
  не должен схлопывать их в один ответ «не участник» *внутри*, но **не** должен
  раскрывать разницу клиенту (6.3).
- Также требуется `organization.status == ACTIVE`. Приостановленная организация
  отказывает даже активному membership.
- Отсутствие membership — **отказ**, никогда не неявное «создали персональную
  организацию» и никогда не откат к другой организации.

### 4.4 Роли: что существует и что намеренно не решено

Схема и домен уже определяют `MembershipRole` = `OWNER`, `ADMIN`, `MEMBER`, `BILLING`.
**Этот документ не превращает этот словарь в матрицу разрешений**, потому что раздел 17
контракта ставит «Membership / RBAC» шагом 5, и ни один принятый контракт не говорит, что
каждой роли позволено.

Шаг 4 фиксирует только *механизм*:

- операция объявляет **требуемую authority**: либо *`any_active_member`*, либо
  *`named_roles`*;
- сервис разрешает роль вызывающего из membership (шаг 5 алгоритма) и сравнивает — он
  никогда не читает роль из утверждения клиента;
- **`OWNER` нельзя «изготовить»**: смена роли — отдельная административная операция со
  своей авторизацией, и Шаг 3 намеренно предлагает `memberships.set_role` как метод
  *записи*, который ничего не авторизует.

`BILLING` присутствует в словаре и **не имеет** ни одной операции Шага 4. Он
зарезервирован для стадии Billing; выдумывать его разрешения сейчас означало бы закрыть
решение, принадлежащее владельцу.

Предлагаемый default для первой реализации — **предложение, а не решение** (вопрос Q-2 в
разделе 9): чтение организаций/участий/проектов/запусков — *любой активный участник*;
создание проекта — *`OWNER` или `ADMIN`*; запуск — *`OWNER`/`ADMIN`/`MEMBER`*;
управление участниками и ролями — *`OWNER`*; управление провайдерскими аккаунтами —
*`OWNER` или `ADMIN`*; API-ключи — *требует решения* (зависит от модели R-5, Q-1).

### 4.5 Матрица fail-closed

Каждая строка — отказ. Нет «разрешить из-за отсутствия правила».

| Условие | Результат |
| --- | --- |
| нет principal | `unauthenticated` |
| principal из непроверенного пути (невозможно по конструкции) | `unauthenticated` |
| субъект не `ACTIVE` | `subject_not_admissible` |
| нет ACTIVE membership для привязанной организации | `not_permitted` |
| организация приостановлена | `not_permitted` |
| организация не среди кандидатов субъекта | `not_permitted` |
| неоднозначная организация (клиент не назвал, доступно несколько, операция требует одну) | `ambiguous_organization` |
| проект не существует или существует в другой организации | `not_found` |
| роль не в требуемом наборе операции | `not_permitted` |
| репозиторий поднял `PermissionDeniedError` (RLS отказал) | `not_permitted`, и это **сигнал бага** — L3 должен был отказать раньше |
| репозиторий поднял `ConcurrentModificationError` | `conflict` |
| транзакция прервана ранее перехваченной ошибкой | `transaction_aborted` (Шаг 3 H-1 отказывает коммиту) |

Предпоследняя строка намеренна: отказ RLS, дошедший до L3, означает, что собственная
авторизация L3 была неверной или отсутствовала. Его нужно показать как отказ **и**
залогировать как внутреннюю несогласованность, никогда не повторять и никогда не
смягчать в «не найдено».

---

## 5. Application Services — контракты use cases

Девять use cases, каждый с восемью требуемыми атрибутами. **Ни один не реализован этим
шагом.** `UoW` — это `UnitOfWork` из `app/platform/persistence/database.py`; имена скоупов
(`tenant`, `pre_tenant`, `server`) — имена его фабрик.

Обозначения: `I` — входной DTO, `ID` — источник идентичности, `AZ` — проверки
авторизации, `R` — репозитории/UoW, `SE` — побочные эффекты, `TX` — граница транзакции,
`E` — ошибки, `IDEM` — идемпотентность.

### AS-1 — список доступных вызывающему организаций

- **I**: пусто (никакие клиентские идентификаторы не принимаются).
- **ID**: principal.
- **AZ**: шаги 1–3 из 4.1. Организация не привязывается; это *discovery*-чтение, предваряющее привязку.
- **R**: `pre_tenant(user_id=principal.subject_id)` -> `organizations.list_for_user`, `memberships.list_for_user`. Субъект берётся из principal, никогда из DTO.
- **SE**: нет (только чтение).
- **TX**: одна читающая транзакция.
- **E**: `unauthenticated`; пустой список для субъекта без участий (пустой список — не ошибка и не раскрытие: он о самом вызывающем).
- **IDEM**: не применимо.

### AS-2 — список проектов в выбранной организации

- **I**: `organization_id` (утверждение).
- **ID**: principal.
- **AZ**: шаги 1–6, но проверка проекта — «перечислить принадлежащее привязанному арендатору», а не «проверить один проект».
- **R**: `tenant(organization_id, user_id=principal.subject_id)` -> `projects.list_for_current_tenant`.
- **SE**: нет.
- **TX**: одна читающая транзакция.
- **E**: `not_permitted` для организации вне набора кандидатов (неотличимо от «не существует», 6.3).
- **IDEM**: не применимо.

### AS-3 — создание проекта

- **I**: `organization_id` (утверждение), `name`, необязательный `slug`. **Не** принимаются: `workspace_ref`, `project_id`, статус.
- **ID**: principal.
- **AZ**: шаги 1–7, требуемая authority *`OWNER` или `ADMIN`*.
- **R**: `tenant(...)` -> `projects.create(project_id, name, slug=...)`. `project_id` генерируется **сервером** (`new_id()`); `workspace_ref` выводится сервером на шаге workspace, а не здесь.
- **SE**: одна строка `Project`. Когда появится шаг workspace — один workspace-корень (R-4).
- **TX**: одна tenant-транзакция. Вставка `Project` коммитится даже если последующее создание workspace упадёт — это осознанный выбор из 7.3: `Project` остаётся в состоянии, видимом и починяемом оператором, а не фантомом.
- **E**: `not_permitted`; `UniqueViolationError` при коллизии slug в арендаторе -> `conflict`; `InvalidIdentifierError` -> `invalid_request`.
- **IDEM**: **требуется** по клиентскому ключу. Контракт (раздел 12) требует комбинировать ключ с арендатором + операцией + ресурсом. Повтор с тем же ключом возвращает исходный проект, а не второй. Нужно решение о том, где живёт леджер (раздел 9, Q-4).

### AS-4 — подготовка запуска

- **I**: `project_id` (утверждение), описание задачи или `task_id`, и запрошенные параметры исполнения, которые могут только **сужать** разрешаемое сервером.
- **ID**: principal.
- **AZ**: шаги 1–6 плюс требуемая authority для запуска.
- **R**: чтения через `tenant(...)`: проект, его организация, провайдерские аккаунты организации, если провайдер назван.
- **SE**: **нет**. «Подготовка» — чистое разрешение: она производит проверенный, серверно-выведенный план исполнения. Отсутствие побочных эффектов — то, что делает AS-5 дешёвым в отказе.
- **TX**: только чтение.
- **E**: `not_found` для проекта вне привязанного арендатора; `not_permitted`; `provider_unavailable`, если ссылку на креденл нельзя удовлетворить (сама ссылка здесь **не** разрешается — разрешение на стороне Core, R-2).
- **IDEM**: не применимо (нет эффекта).

### AS-5 — проверка полномочий перед запуском

- **I**: выход AS-4.
- **ID**: principal.
- **AZ**: полный алгоритм 4.1, переоценённый **в этот момент**, а не переиспользованный из AS-4. Authority решается до старта запуска и замораживается на его длительность (раздел 8 контракта); решение, принятое в более раннем запросе, не должно воспроизводиться.
- **R**: чтения `tenant(...)`: статус membership, статус организации, статус и владелец проекта.
- **SE**: нет.
- **TX**: одна читающая транзакция.
- **E**: `not_permitted`, `ambiguous_organization`, `not_found`, `subject_not_admissible`.
- **IDEM**: не применимо.

Этот use case выделен отдельным шагом, чтобы решение было **одной именованной операцией**
с одной реализацией. Разделение с AS-4 также означает, что будущий движок авторизации можно
заменить, не трогая построение плана.

### AS-6 — создание Platform `RunRecord`

- **I**: проверенный план из AS-5 плюс клиентский ключ идемпотентности.
- **ID**: principal (становится `initiated_by_user_id`).
- **AZ**: уже решено AS-5 в том же запросе; запись дополнительно опирается на containment Шага 3.
- **R**: `tenant(...)` -> `run_records.create_queued(run_id, project_id, task_id=...)`. `run_id` генерируется сервером.
- **SE**: один `RunRecord` в `queued`. **Никакого** `core_run_id`: у queued-запуска нет идентичности Core по контракту (CHECK Шага 2, и Шаг 3 отказывает его устанавливать).
- **TX**: одна tenant-транзакция, содержащая AS-6 **и** AS-7 (7.2).
- **E**: `not_found` для проекта; `UniqueViolationError` -> `conflict`; `transaction_aborted`, если предшествовала перехваченная ошибка (H-1 Шага 3 откажет коммиту).
- **IDEM**: клиентский ключ комбинируется с организацией + `project_id` + идентичностью задачи. Повтор должен разрешаться в тот же `RunRecord`, а не во второй. Требует Q-4.

### AS-7 — передача доверенного запроса в Core

- **I**: `TrustedExecutionRequest`, построенный L3 (раздел 10). **Не** клиентский DTO.
- **ID**: principal уже внутри запроса как один аудиторский скаляр.
- **AZ**: дополнительной нет. Platform может **отказать** в запуске; она не может расширить authority Core (раздел 16.13 контракта). Core переоценивает свою цепочку.
- **R**: для решения — нет. `RunRecord` переводится через `claim` в той же транзакции, где записывается идентичность Core.
- **SE**: запускается Core-запуск; `RunRecord` переходит `queued -> running` с `core_run_id`, `started_at` и `initiated_by_user_id`, установленными вместе (Шаг 3 `claim`).
- **TX**: см. 7.2. **Проблема порядка** — устойчивая строка Platform должна существовать до того, как Core сможет нести её id, но Core-запуск, стартовавший до коммита Platform, мог бы пережить откаченную строку — разобрана в 7.2 и является решением, а не упущением.
- **E**: Core отказал -> `RunRecord` помечается failed с классификацией; запуск **не** остаётся `running` (R-6 требует механизма против этого, он всё ещё открыт — Q-3). `ConcurrentModificationError`, если запуск уже захвачен другим воркером -> `conflict`, что и является правильным ответом на дублирующий запуск.
- **IDEM**: у Core своя локальная иерархия идемпотентности (раздел 12 контракта), которую Шаг 4 **не** меняет. Защита со стороны Platform — compare-and-set в claim `RunRecord`.

### AS-8 — приём результата и запись usage

- **I**: сигнал завершения со стороны Core плюс `PhysicalTelemetry`. **Не** клиентский DTO: этот use case вообще недостижим из клиентского запроса.
- **ID**: не пользовательский principal. Это **серверный приём результата**, и он должен быть аутентифицирован как таковой (сервисная идентичность, `subject_kind = service`) либо быть внутрипроцессным вызовом — это решение, Q-5.
- **AZ**: `run_id` телеметрии (корреляция Core) должен разрешаться в `RunRecord` в арендаторе, который приёму позволено трогать. Неразрешимый -> отказ и запись, никогда не догадка.
- **R**: `tenant(...)` -> `run_records.find_by_core_run_id`, `run_records.finish`, `run_records.increment_attempt_count`, `usage_records.append`.
- **SE**: переход жизненного цикла (`finish`, compare-and-set) **и** один `UsageRecord`. Оба, иначе запуск остаётся в состоянии, которое об этом говорит.
- **TX**: см. 7.4. Переход `RunRecord` и вставка `UsageRecord` — одна транзакция, **когда доступны оба**. Когда телеметрия не пришла, запуск завершается с классификацией, фиксирующей отсутствие — fail-closed направление, требуемое `D-PLATFORM-13` / разделом 15 контракта, и единственное, которое останется верным, когда появится Billing.
- **E**: `not_found` (неизвестный `core_run_id`) -> отказ и запись, никогда не создание запуска; `ConcurrentModificationError` при дублирующем завершении -> считать уже записанным, а не ошибкой для повтора; `UniqueViolationError` по `(run_record_id, attempt_number)` -> попытка уже записана, значит это дубликат, а не конфликт.
- **IDEM**: `UNIQUE(run_record_id, attempt_number)` (O-8, решён) — авторитет. Повторно доставленная телеметрия должна быть no-op, а не вторым `UsageRecord`.

### AS-9 — обработка ошибок и частично завершённых операций

Это **сквозной контракт**, а не десятый эндпоинт.

- Каждый отказ из 4.5 отображается ровно в один стабильный код ошибки; коды — это контракт API.
- **Ни одна частичная запись не может быть представлена как успех.** Гарантия H-1 Шага 3 (перехваченная ошибка оператора не может быть закоммичена молча) — то, что делает это исполнимым: L3, проглотивший ошибку репозитория и вышедший из Unit of Work чисто, теперь получает `TransactionError`, и операция падает.
- **Исходы с неизвестным состоянием называются, а не прячутся.** Их два, оба названы в 7.5: закоммиченный `RunRecord`, чей запуск в Core упал, и Core-запуск, чья телеметрия не пришла. У каждого — записанное, починяемое состояние; ни один не повторяется молча.
- Клиентская ошибка никогда не раскрывает существование другого арендатора, другого пользователя или другого проекта (6.3).

---

## 6. Модель ошибок

### 6.1 Два словаря ошибок, намеренно раздельных

| Словарь | Принадлежит | Пример |
| --- | --- | --- |
| иерархия `PersistenceError` (Шаг 3) | L2, хранение | `UniqueViolationError`, `ConcurrentModificationError` |
| иерархия `ApplicationError` (L3, **не реализована**) | use cases | `not_permitted`, `not_found`, `conflict`, `invalid_request` |

L3 **транслирует** ошибки L2; он никогда их не протекает. API-слой, которому пришлось бы
ловить `UniqueViolationError`, был бы связан с базой данных, и смысл ошибки изменился бы
при первом изменении схемы.

### 6.2 Словарь отказов

Закрытый список, потому что открытый растёт случайно:

| Код | Значение | HTTP-аналог (для будущей поверхности) |
| --- | --- | --- |
| `unauthenticated` | нет пригодного principal | 401 |
| `subject_not_admissible` | principal есть, субъект не вправе действовать | 403 |
| `not_permitted` | нет membership, нет роли или не тот арендатор | 404 или 403, по 6.3 |
| `ambiguous_organization` | организация должна быть выбрана и не выбрана | 400 |
| `not_found` | названный ресурс не в привязанном арендаторе | 404 |
| `conflict` | compare-and-set проигран или дублирующий ключ | 409 |
| `invalid_request` | сам запрос некорректен | 400 |
| `transaction_aborted` | ранее перехваченная ошибка отравила транзакцию | 500 |
| `unavailable` | зависимость отказала или недостижима | 503 |

### 6.3 Нераскрытие и единственное место, где оно конфликтует с ясностью

Раздел 18 контракта требует, чтобы клиент не мог выбрать другую организацию или проект.
Есть два способа отказать, и они различаются: **`not_found`** не раскрывает, существует ли
ресурс; **`not_permitted`** раскрывает, что он существует, но не ваш.

**Принятое здесь правило:** кросс-арендаторная попытка отвечается как **`not_found`**,
потому что `not_permitted` позволил бы перечислять идентификаторы чужих арендаторов.
Внутри собственного арендатора вызывающего настоящая нехватка прав — это `not_permitted`,
поскольку там существование и так известно вызывающему, и сокрытие только запутало бы.

Названо, потому что это реальное напряжение: так кросс-арендаторное и «не существует»
становятся неразличимыми в API, что является небольшой ценой удобства за гарантию
нераскрытия. Это **контрактное решение, записанное здесь**, и раздел 9 перечисляет его как
Q-6 на случай, если владелец предпочтёт ясность.

### 6.4 Содержимое ошибки

Клиентская ошибка несёт код, стабильное сообщение и ничего больше. Она никогда не несёт:
запрошенный идентификатор, имя ограничения, SQLSTATE, идентификатор другого арендатора,
количество совпавших строк или стек. **Внутренняя** запись может нести всё это, потому что
это нужно оператору.

---

## 7. Границы транзакций

### 7.1 Правило

**После того как успешная аутентификация сымитирована**, вопрос, на который должна
ответить граница транзакции: *если процесс умрёт здесь, какое состояние останется, и
увидит ли его оператор?* По умолчанию — одна транзакция на **use case**, открываемая
только после успешной авторизации (шаг 8 из 4.1). Use case, охватывающий две транзакции,
обязан назвать своё промежуточное состояние, и оно должно быть видимым и починяемым.

### 7.2 AS-6 + AS-7: проблема порядка, названная, а не предположенная

Устойчивый `RunRecord` Platform должен существовать до того, как Core о нём узнает, а
`RunRecord` должен нести `core_run_id`, которого нет, пока Core не стартовал. Возможны три
порядка, и ни один не бесплатен:

| Порядок | Режим отказа | Следствие |
| --- | --- | --- |
| коммит `RunRecord`, затем запуск Core, затем claim | запуск удался, claim упал | существует Core-запуск, который Platform считает `queued`. **Orphan.** |
| `RunRecord` + запуск в одной транзакции | транзакция откатилась после старта Core | Core-запуск существует **без** строки Platform. **Хуже.** |
| запуск Core, затем запись всего | запись упала | Core-запуск без строки Platform. **Худший**: Platform никогда не знала. |

**Рекомендуется для первой реализации:** первый порядок, с orphan, сделанным **видимым**, а
не невозможным — `queued` `RunRecord`, чей запуск был предпринят, несёт маркер, а
сверка-развёртка является механизмом R-6. Обоснование: это единственный порядок, в котором
любой исход оставляет строку Platform, а R-6 уже требует, чтобы «`RunRecord` не оставался
бесконечно running после потери воркера». Это **предложение** (Q-3), потому что R-6 прямо
оставляет механизм открытым.

Что **не** приемлемо ни в одном порядке: исполнение Core, о существовании которого Platform
не может узнать. Это нарушило бы инвариант 12 (Core никогда не опрашивает Platform ради
authority) в обратную сторону — Platform никогда не смогла бы отчитаться за то, что сделал
Core.

### 7.3 AS-3: строка проекта и workspace-корень

Строка `Project` коммитится первой; создание workspace следует за ней. Отказ оставляет
`Project` без пригодного workspace, что видимо (строка есть, `workspace_ref` пуст) и
починяемо. Обратное — создан workspace, строка откачена — оставляет неотслеживаемый
каталог, что невидимо. Предпочитаем видимый отказ.

### 7.4 AS-8: переход жизненного цикла и запись usage

Оба в одной транзакции, когда присутствуют оба входа. Если переход жизненного цикла удался,
а вставка `UsageRecord` упала по ограничению, транзакция **обязана** откатиться — запуск,
помеченный `succeeded` с молча пропавшим измерением, и есть то состояние, которое
`D-PLATFORM-13` называет финансово неполным, и дешевейший способ его почтить — отказаться
записывать успех вообще.

Если телеметрия не пришла, запуск завершается с классификацией, фиксирующей отсутствие.
Словарь классификаций — это O-7, всё ещё открыт (раздел 9, Q-7).

### 7.5 Названные исходы с неизвестным состоянием

| Исход | Оставленное состояние | Починка |
| --- | --- | --- |
| строка Platform закоммичена, запуск Core упал | `RunRecord.queued` (или `failed` с классификацией) | повторить запуск или завалить запуск |
| Core-запуск стартовал, claim Platform упал | `RunRecord.queued` для запуска, который Core выполнил | сверка: сопоставить по корреляции, затем claim или fail |
| Core-запуск завершился, телеметрия не пришла | `RunRecord.running` дольше ожидаемого | сверка-развёртка, затем терминальная классификация |
| перехватить ошибку репозитория и продолжить | транзакция прервана; коммит отклонён (H-1 Шага 3) | не нужна — операция падает громко |

Каждая строка — состояние, которое оператор может запросить. Ни одна не молчалива.

---

## 8. M-2 — subject-bound discovery и что гарантирует каждый слой

Усиление Шага 3 (задание #61) установило, что привязка субъекта обеспечивается на уровне
**сессии/адаптера**, а не RLS-политикой, и причина была **измерена**: предикат субъекта в
политике делает `INSERT ... RETURNING` невыполнимым для bootstrap, потому что `RETURNING`
тоже консультирует SELECT-политики. Это остаётся в силе. Ниже — что L3 обязан поэтому
добавить и что должно быть протестировано.

### 8.1 Гарантия по слоям

| Слой | Гарантия | Не гарантия |
| --- | --- | --- |
| политика L1 | system-скоуп должен быть объявлен, иначе discovery-чтения не видят ничего | *какой* субъект разрешается |
| `Session` L2 | один субъект на транзакцию; чтение, называющее другого субъекта, поднимает `TransactionError`; `users.list_all` не существует | что названный субъект *подлинный* |
| Application Services L3 | субъект, переданный в `pre_tenant`, — это `principal.subject_id`, и ничего больше | ничего о креденле |
| верификатор L4 | креденл действительно принадлежит этому субъекту | аренду и авторизацию |

**Несущее предложение:** L2 обеспечивает *«один субъект, и всегда тот же»*; L4 обеспечивает
*«этот субъект — тот, кем является вызывающий»*. Ни одно в одиночку не является границей;
границей является композиция.

### 8.2 Требования к L3

1. **Только проверенный principal может поставлять субъекта.** `pre_tenant(user_id=...)`
   вызывается с `principal.subject_id` и ни с чем, выведенным из тела запроса.
2. **Клиент не может сменить субъекта внутри доверенного сценария.** Ни один use case не
   принимает входное поле `user_id`. Use case, которому это якобы нужно
   (административное «посмотреть другого пользователя»), был бы отдельной операцией со
   своей авторизацией **и** своим principal — никогда параметром AS-1.
3. **SYSTEM-креденлы и SYSTEM-скоуп вне обычного пути.** Композиция L3 по умолчанию держит
   `PlatformDatabase` на **tenant-логине**; system-логин — это другой `PlatformDatabase`,
   создаваемый только административным композиционным путём, и ни один use case раздела 5
   не достигает `system_provider_accounts`, `system_users` или `system_memberships`.
   Разрешение `ProviderAccount` для запуска читает **только tenant-owned** строки.
4. **Нераскрытие.** Ошибки и ответы не должны раскрывать существование другого
   пользователя, организации или членства. Конкретно: AS-1 возвращает только собственные
   организации вызывающего; поиск пользователя по идентификатору не предлагается;
   кросс-арендаторный идентификатор отвечается `not_found` (6.3); а discovery-чтение для
   другого субъекта никогда не доходит до SQL, поэтому не может дать различающую ошибку БД.

### 8.3 Тесты, которые должны быть написаны на шаге реализации

| # | Тест | Проверяет |
| --- | --- | --- |
| M2-1 | `test_discovery_uses_principal_subject_only` | AS-1 вызывает `pre_tenant` с `principal.subject_id`; наличие `user_id` в теле запроса это не меняет |
| M2-2 | `test_no_use_case_accepts_a_subject_parameter` | интроспекция входных DTO AS-1..AS-9 не находит поля `user_id` |
| M2-3 | `test_discovery_refuses_a_second_subject_in_one_transaction` | второй `list_for_user` для другого субъекта в одной `pre_tenant`-транзакции поднимает `TransactionError` (гарантия L2, перепроверенная через L3) |
| M2-4 | `test_client_cannot_read_another_users_organizations` | AS-1 для субъекта A никогда не возвращает организацию, достижимую только через субъекта B |
| M2-5 | `test_cross_tenant_identifier_is_reported_as_not_found` | AS-2/AS-4 с чужими `organization_id`/`project_id` дают `not_found`, а не `not_permitted` |
| M2-6 | `test_tenant_login_cannot_reach_system_credentials` | пул композиции по умолчанию не может читать `system_provider_accounts` (L1 `PermissionDeniedError`), и ни один use case не пытается |
| M2-7 | `test_errors_do_not_disclose_other_tenants` | сообщение и код для кросс-арендаторной попытки побайтово совпадают с таковыми для несуществующего идентификатора |
| M2-8 | `test_unauthenticated_request_never_reaches_discovery` | отсутствующий/недействительный креденл отказывает до открытия любого Unit of Work (шпион репозитория фиксирует ноль вызовов) |

M2-8 — тот, что доказывает **порядок**, а не намерение: он падает, если будущий рефакторинг
откроет транзакцию до аутентификации.

---

## 9. Открытые решения

O-1..O-13 контракта остаются открытыми/отложенными кроме O-8 (решён) и O-9 (решён и
реализован). Этот шаг **не закрывает ни одного**. Ниже — ровно то, что Шагу 4 нужно от
каждого релевантного, с вариантами и рекомендацией. **Каждая рекомендация требует решения
владельца; ни одна не реализована.**

### Q-1 — модель authority API-ключа (R-5, O-1) — **требуется решение владельца**

Допустимы две модели, и ровно одну нужно выбрать до шага 8.

| Вариант | Следствие |
| --- | --- |
| **A** organization-scoped сервисный principal | ключ переживает membership создателя; отзыв — чисто операция над ключом; ключ уволенного сотрудника работает, пока кто-то его не отзовёт |
| **B** креденл, производный от membership создателя | увольнение — одно действие; но ключ молча перестаёт работать при смене роли создателя, что интегратор обязан ожидать |

**Рекомендация:** **B**, с конкретизацией обязательства R-5 (отзыв или деактивация
membership создателя детерминированно и наблюдаемо отзывает или отключает производные
ключи) и с компенсирующим правилом: ключ записывает собственный `status`, поэтому оператор
может отозвать ключ напрямую. Обоснование: запрещённое в R-5 состояние — «создатель теряет
membership, а созданный им ключ молча сохраняет authority» — это режим отказа, наиболее
вероятно являющийся реальным инцидентом безопасности, и B делает его структурно
невозможным, а не требующим развёртки. **Здесь это не решено.** `principal.py` записывает
`subject_kind = api_key`, чтобы любую модель можно было реализовать позже, не меняя контракт
principal.

### Q-2 — первая матрица требований к ролям — **требуется решение владельца**

Раздел 4.4 предлагает матрицу. Явного одобрения требуют: может ли `MEMBER` запускать
запуск, и кто может создавать проект. У `BILLING` операции Шага 4 нет вовсе.

### Q-3 — механизм R-6 против бесконечно running запусков — **требуется решение владельца**

R-6 требует, чтобы механизм существовал, но оставляет открытым (lease, heartbeat, timeout
или сверка-развёртка). Раздел 7.2 рекомендует сверку с видимым маркером `queued`.
**Здесь не решено.** Следствие отсрочки: AS-7 нельзя реализовать, не выбрав хотя бы, где
живёт маркер.

### Q-4 — где записывается идемпотентность Platform — **открыто**

Контракт фиксирует, *что* включает идентичность идемпотентности Platform (арендатор, класс
операции, серверно-выведенный ресурс, клиентский ключ), но не *где* это хранится. Варианты:
отдельная таблица, уникальное ограничение на существующей таблице или леджер. Шаг 3 такой
таблицы не строил, а её добавление — изменение схемы, требующее своей миграции и решения
ревью. **Следствие отсрочки:** AS-3 и AS-6 нельзя сделать идемпотентными, поэтому первая
реализация должна либо отказаться обещать идемпотентность, либо принять дубликаты.

### Q-5 — как аутентифицируется AS-8 — **открыто**

Приём результата серверный, не клиентский. Варианты: внутрипроцессный вызов без транспорта
(тогда «аутентифицирован» означает «недостижим из HTTP-поверхности») либо сервисный principal
(`subject_kind = service`) через тот же порт верификатора. **Не решено.** Безопасный interim
ответ — только внутрипроцессно.

### Q-6 — кросс-арендаторные попытки отвечаются `not_found`, а не `not_permitted` — **решение записано, владелец может его отменить**

Раздел 6.3 принимает `not_found` по причинам нераскрытия и называет цену удобства. Это
контрактный выбор, сделанный в этом документе; он перечислен здесь, чтобы быть видимым как
выбор, а не обнаруженным как поведение.

### Q-7 — словарь статусов `RunRecord` и классификация отсутствия телеметрии — **O-7, всё ещё открыт**

Compare-and-set Шага 3 использует намеренно консервативный набор терминальных статусов и
говорит, что O-7 остаётся открытым. Разделу 7.4 нужна классификация «телеметрия не пришла»;
это значение O-7, и оно **не** выдумывается здесь. Interim-поведение — использовать
существующее текстовое поле `failure_classification`, которое схема уже допускает, и не
трогать enum.

### Явно **не** закрыто, повторено, чтобы не закрылось случайно

формат токена API-ключа, хеширование, префикс, семантика срока действия (O-1); сам механизм
аутентификации — сессия vs JWT vs федерация (O-2); вендор KMS/Vault и грамматика `secret_ref`
(O-3, O-4); топология файловой системы workspace (O-5); транспорт Platform -> Core (O-6) —
раздел 10 определяет **контракт**, а не транспорт; полный жизненный цикл `RunRecord` (O-7);
любая семантика идемпотентности сверх уже зафиксированного контрактом (Q-4); полная
RBAC-модель (Q-2); Billing, Wallet, Pricing, Partner, Reseller.

---

## 10. Граница доверенного исполнения Platform -> Core

Это ключевой архитектурный пункт Шага 4, поэтому он изложен как контракт с явной типовой
формой, отображением на **уже существующие** контракты Core и последовательностью.

### 10.1 Контракты Core, которые порт обязан уважать

Проверено в репозитории, а не предположено:

| Контракт Core | Где | Свойство, связывающее порт |
| --- | --- | --- |
| `ExecutionCoordinator.execute(...)` | `app/execution/authorizer.py` | строгая цепочка: validate -> permission -> intent -> approval -> policy -> **workspace_root обязателен** -> `AuthorizedExecution` -> dispatch |
| `ExecutionIntent` / `IntentBuilder` | `app/execution/intent.py` | неизменяемый; канонический `fingerprint` |
| `AuthorizedExecution` | `app/execution/intent.py` | создаётся **только** с модульно-приватным sentinel координатора; `is_valid()` перепроверяет отпечаток |
| `RunScope` / `frozen_scope` / `require_active_scope` | `app/runtime/run_scope.py` | замороженный периметр, не разрешение; проверяет workspace-корень, запрос и набор команд |
| `PhysicalTelemetry` / `PhysicalTelemetrySink` | `app/agent_runtime/physical_telemetry.py` | 14 физических полей; `run_id` — корреляционная идентичность Core |
| `WorkspaceBoundary` / `EphemeralWorkspaceManager` | `app/execution/sandbox.py`, `app/execution/workspace_manager.py` | канонический containment; изоляция scratch на запуск (`_isolate_workspace`) |

**`TrustedExecutionRequest` в коде не существует.** Подтверждено поиском по `app/`: имя
встречается только в документации. Этот шаг определяет контракт; шаг 10 его реализует.

### 10.2 Черновая форма

Предлагается как **замороженный dataclass** в будущем модуле `app/platform/application/`
(L3). Три группы, и группировка — это и есть контракт.

**Группа 1 — доверенное, серверно-выведенное, никогда не от клиента**

| Поле | Источник | Почему доверенное |
| --- | --- | --- |
| `platform_run_id` | сервер (`new_id()`) | собственная идентичность Platform (R-1) |
| `organization_id` | разрешённый membership | контракт R-8.16 |
| `project_id` | разрешённый и проверенный по владельцу проект | раздел 4 контракта |
| `workspace_root` | серверно-выведен из организации + проекта | раздел 6 контракта; изоляция R-4 |
| `execution_profile` | серверно-разрешённый профиль (его потолок authority — потолок Core) | потолком владеет Core |
| `intent` | `ExecutionIntent`, построенный сервером | неизменяемое значение Core; переносимо |
| `run_scope` | замороженный `RunScope` | периметр Core |
| `idempotency_identity` | выведена из арендатора + операции + ресурса + клиентского ключа | раздел 12 контракта |
| `acceptance_expectations` | серверно-разрешённые критерии | трассируемость, не authority |
| `credential_reference` | **непрозрачная** ссылка (или `None`) | R-2: никогда открытый текст |
| `initiated_by_user_id` | `subject_id` из principal | только аудит |

**Группа 2 — только корреляция, никогда authority**

`core_run_id` на момент построения ещё не существует; Core производит его и возвращает в
`PhysicalTelemetry.run_id` (R-1). Запрос несёт `platform_run_id`, а возвращённый `core_run_id`
хранится рядом. Они **никогда** не взаимозаменяемы, и `UsageRecord` обязан нести оба под
явно различными именами (R-1). Будущий `request_id` для корреляции транспорта — тоже
корреляция, не authority.

**Группа 3 — не должно появляться в запросе вообще**

| Запрещено | Потому что |
| --- | --- |
| открытый текст провайдерского секрета в любом написании | раздел 5 контракта; R-8.19. Поле вида `provider_secret_environ: Mapping[str, str]` **явно отвергнуто** контрактом |
| `workspace_root` или путь от клиента | раздел 6 контракта (workspace выводится сервером) |
| команда, argv или набор инструментов от клиента, **расширяющие** разрешённый профиль | раздел 18 контракта («не может внедрить произвольные command, tool или network authority») |
| переданные без изменений клиентские `organization_id`, `project_id`, `user_id` | раздел 4 контракта; они разрешаются, а расхождение — отказ |
| набор разрешённых команд от клиента | то же |
| сырой креденл или токен клиента | ничего ниже L3 в нём не нуждается |
| `APIKey.key_prefix`, идентификаторы сессий, хеши | к исполнению не относятся |

### 10.3 Authority Core никогда не расширяется Platform

Направление одностороннее, и оно обеспечивается собственным кодом Core, а не обещанием
Platform:

```
Platform разрешает и МОЖЕТ СУЖАТЬ:
    profile            -> серверно-выбранный профиль (его потолок — потолок Core)
    allowed_commands   -> подмножество профиля
    allowed_tool_ids   -> подмножество профиля
    network_access     -> значение профиля; клиент может просить только меньше
    run_scope          -> заморожен до dispatch, проверен ExecutionCoordinator
        │
        ▼
ExecutionCoordinator заново выводит всё:
    RunScope.frozen_scope / require_active_scope   -> отказ при несовпадении run id
    scope.validate_workspace_root(workspace_root)  -> отказ при чужом корне
    scope.validate_execution_request(request)      -> полная идентичность команды, argv закреплён
    scope.validate_command_set(...)                -> вызывающий может только СУЗИТЬ дальше
    ExecutionPolicy                                -> собственная политика Core
    ApprovalPolicy + resolver                      -> привязано к отпечатку, одноразово
    workspace_root обязателен, абсолютен, каталог  -> fail-closed
        │
        ▼
AuthorizedExecution   (создать может только ExecutionCoordinator)
        │
        ▼
ExecutionBackend / LocalExecutionAdapter -> эффект
```

Явные утверждения: **клиент никогда не может создать `AuthorizedExecution`** (sentinel
делает это невозможным вне модуля координатора, а `is_valid()` перепроверяет отпечаток
намерения, поэтому мутированный токен отвергается); **клиент никогда не может обойти
`ExecutionCoordinator`** (он единственный производитель маркера, и адаптеры принимают только
этот тип); **Platform не может выдать authority, которую Core удерживает** (если политика
Core отказывает, запуск отклонён; у Platform нет канала это обойти, и его добавление
нарушило бы инвариант 13); **отказы самой Platform происходят первыми**, потому что
отклонённый запуск не должен расходовать ресурсы Core и ничего раскрывать клиенту.

### 10.4 Изоляция workspace

R-4 требует, чтобы workspace-корень каждого `Project` был изолирован от корней **всех других
проектов, включая проекты внутри одной организации**. Поэтому порт несёт **серверно-выведенный**
корень, а `WorkspaceBoundary` Core его канонизирует и удерживает.

Шаг 4 **не** выбирает топологию (O-5 открыт, и контракт явно отказывается вшивать Linux-путь).
Что Шаг 4 фиксирует: корень выводится из `(organization_id, project_id)` серверной функцией,
никогда из клиентского значения; два проекта не должны делить корень, и запуск в проекте A не
должен достигать файлов проекта B — это обеспечивает граница Core на любом корне, который
выведет сервер; функция вывода — **единственная**, поэтому у инварианта одно место, где он
может быть нарушен.

`EphemeralWorkspaceManager` и `_isolate_workspace` уже обеспечивают изоляцию scratch на запуск
для адаптеров, которые это используют; порт не должен им мешать. Когда адаптер изолирует,
`ExecutionCoordinator` намеренно не проверяет workspace-корень скоупом, поскольку приватный
scratch-каталог не является проверяемым скоупом свойством — изоляция обеспечена независимо.
Поэтому порт должен считать вопрос «кому принадлежит workspace-корень» вопросом Core и
поставлять устойчивый **проектный** корень, а не временный на запуск.

### 10.5 Секреты

R-2 явен, и Шаг 4 лишь повторяет его как границу:

```
TrustedExecutionRequest
    -> непрозрачная ссылка на креденл          (никогда открытый текст, никогда грамматика вендора)
    -> граница исполнения Core
    -> абстрактный EphemeralSecretResolver     (порт; интерфейс на стороне Core)
    -> внедрённый адаптер                      (поставляется композиционным слоем Platform)
    -> эфемерный креденл в памяти
    -> вызов провайдера
```

- Порт несёт **непрозрачный handle**. `ProviderAccount.secret_ref` — существующее зерно этой
  абстракции, и он **расширяется, а не заменяется** (раздел 5 контракта).
- **`EphemeralSecretResolver` здесь не реализуется.** Он назван в контракте (R-2) и не
  существует в `app/`; Шаг 4 подтверждает границу и оставляет реализацию шагу 7.
- Сторона Core этого порта не импортирует ни модуль Platform, ни SDK KMS/vault, ни драйвер
  БД, ни платёжную библиотеку (R-2). Адаптер внедряется композиционным слоем — именно это
  оставляет направление Core -> Platform пустым.
- Открытый текст секрета никогда не попадает в запрос, `RunRecord`, `UsageRecord`,
  `PhysicalTelemetry`, обычный ответ API или лог.

### 10.6 Последовательность от проверенной идентичности до эффекта

```
  клиент
    │  1. креденл                        (ЕДИНСТВЕННОЕ, что клиент сообщает о себе)
    ▼
  L4 AuthenticationVerifier
    │  2. AuthenticatedPrincipal         (subject_id, subject_kind, method, verified_at)
    ▼
  L3 Application Service (напр. AS-4 -> AS-5 -> AS-6 -> AS-7)
    │  3. pre_tenant(user_id=principal.subject_id)      -> организации-кандидаты
    │  4. привязать ОДНУ организацию (утверждение клиента обязано быть В наборе)
    │  5. tenant(org, user_id=principal.subject_id)
    │        membership ACTIVE? организация ACTIVE? роль достаточна?
    │  6. проект существует И project.organization_id == привязанная организация?
    │  7. вывести workspace_root + execution_profile + allowed-наборы (только сервер)
    │  8. BEGIN
    │        run_records.create_queued(run_id, project_id)   -> platform_run_id
    │     COMMIT
    │  9. построить TrustedExecutionRequest (группа 1; непрозрачная ссылка на креденл)
    ▼
  порт Platform -> Core            (транспорт: O-6, ОТКРЫТ — здесь не решается)
    ▼
  Core: ExecutionCoordinator.execute(request, workspace_root=..., run_id=..., run_scope=...)
    │ 10. валидация scope/workspace/request/команд, policy, approval
    │ 11. AuthorizedExecution  (маркер только координатора)
    │ 12. dispatch адаптера -> EphemeralSecretResolver (внедрён) -> эфемерный креденл
    │ 13. эффект; Core пишет своё наблюдение в RunStore
    │ 14. Core испускает PhysicalTelemetry(run_id = core_run_id, attempt_number, ...)
    ▼
  приём результата Platform (AS-8)
    │ 15. BEGIN
    │        run_records.claim(...) или finish(..., expected_status=...)
    │        usage_records.append(...)          (UNIQUE(run_record_id, attempt_number))
    │     COMMIT
    ▼
  устойчивая правда жизненного цикла Platform + измерение (денег нигде нет)
```

Классификация доверия для каждого значения, пересекающего границу:

| Значение | Классификация |
| --- | --- |
| `workspace_root`, `execution_profile`, `intent`, `run_scope`, allowed-наборы | **доверенное, серверно-выведенное** |
| `platform_run_id` | **доверенное**; собственный идентификатор Platform |
| `organization_id`, `project_id`, `initiated_by_user_id` | **доверенные как данные, никогда как authority** (раздел 5 контракта) — это аудит и корреляция, и Core не должен использовать их для решений |
| `credential_reference` | **доверенное как handle**; не несёт секрета и грамматики вендора |
| `core_run_id` | **только корреляция**, производится Core, никогда не выбирается Platform |
| `attempt_number`, поля `PhysicalTelemetry` | **измерение**; не деньги, не authority |
| клиентские `organization_id` / `project_id` / `user_id` / пути / команда | **вообще не допускаются** в запрос |

---

## 11. Стратегия тестов

### 11.1 Что поставляет этот шаг

`tests/test_platform_auth_contracts.py` — контрактные тесты границы аутентификации, без
PostgreSQL, потому что они проверяют *форму* границы доверия, а не поведение БД. Они
используют **fake**-верификатор, что здесь законно: важна не проверка креденла (она не
поставляется), а то, что порт — единственный вход.

| Тест | Проверяет |
| --- | --- |
| principal заморожен | `FrozenInstanceError` при мутации |
| principal не несёт authority | нет полей `organization_id`, `project_id`, `role`, `scopes`, `workspace` |
| создать может только фабрика | `create()` без sentinel поднимает `PermissionError` |
| principal самосогласован | пустой/пробельный `subject_id` отвергается; неизвестные `subject_kind`/`method` отвергаются |
| fake-верификатор создаёт principal | порт работоспособен, контракт не пуст |
| отказ не даёт principal | `AuthenticationError` не несёт ни principal, ни subject id |
| principal — не авторизация | входы алгоритма 4.1 отсутствуют в типе |
| слоистость `app/` | ничто вне `app/platform/` не импортирует модуль principal; модуль не импортирует драйвер, модуль Core и HTTP-фреймворк |

### 11.2 Что должны добавить последующие шаги

8 тестов M2 из 8.3, 18 обязательных инвариантов раздела 18 контракта и тесты по каждому use
case. Они требуют живой БД и реализованных сервисов, поэтому перечислены как результат шага
реализации, а не сфабрикованы сейчас. **Никакой fake-сервис, делающий вид, что авторизация
готова, не добавлен**: это заставило бы набор проходить, ничего не доказывая, что задание
прямо запрещает.

---

## 12. Минимальное следующее задание на реализацию

Ограничено так, чтобы быть проверяемым и откатываемым одним блоком. **В этом шаге не
начато.**

**Цель:** первый Application Service — read-only discovery (AS-1, AS-2), доказывающий границу
аутентификации и порядок авторизации end-to-end, с движком авторизации, сведённым к одному
правилу «требуется ACTIVE membership».

**Результаты.**

1. Пакет `app/platform/application/`:
   - `errors.py` — иерархия `ApplicationError` и закрытый список кодов из 6.2;
   - `context.py` — `AuthorizedContext` (principal + привязанный `organization_id` +
     `membership`), создаваемый только резолвером;
   - `authorization.py` — алгоритм 4.1 как упорядоченная функция, возвращающая отказ или
     контекст, **без** собственного SQL: он компонует вызовы репозиториев;
   - `organizations.py` — AS-1 и AS-2;
   - `__init__.py`.
2. Одна **реализация порта аутентификации только для тестов**, в `tests/`, отображающая
   фиксированный токен в фиксированного субъекта — никогда в `app/`, чтобы её нельзя было
   принять за реальный механизм.
3. `tests/test_platform_application_discovery.py`, живой PostgreSQL: AS-1 возвращает только
   организации вызывающего; AS-2 отказывает в организации, где у вызывающего нет ACTIVE
   membership; кросс-арендаторный идентификатор даёт `not_found` (M2-5); приостановленный
   пользователь отклонён; неактивный membership отклонён; **M2-8** — отклонённая
   аутентификация не открывает Unit of Work; и **M2-2** — ни у одного DTO пакета нет поля
   `user_id`.
4. Обновление
   `docs/STAGE-1-STEP-4-AUTH-APPLICATION-SERVICES-DESIGN.md` тем, что было выяснено — в
   частности реальной формой `AuthorizedContext`.

**Явно не в том задании:** создание проекта, подготовка запуска, `RunRecord`, порт
Platform -> Core, API-ключи, администрирование участников, провайдерские аккаунты,
HTTP-поверхность и любое изменение схемы. Q-3 и Q-4 должны быть отвечены до реализации AS-3
и AS-6, поэтому первое задание намеренно избегает обоих.

**Приёмка.** Четыре результата существуют; новый набор проходит против живого PostgreSQL 17;
тест порядка M2-8 падает, если транзакция открывается до аутентификации (доказано
однократным намеренным обращением порядка и наблюдением отказа); `compileall` и
`git diff --check` чисты; ни один файл вне `app/platform/application/`, `tests/` и названного
документа не изменён.

---

## STATUS

```
Stage 0.1  =  FROZEN / GO

Stage 1    =  ARCHITECTURE CONTRACT READY
Step 1     =  DOMAIN CONTRACTS READY
Step 2     =  POSTGRESQL SCHEMA AND RLS IMPLEMENTED
Step 3     =  PERSISTENCE, REPOSITORIES, UNIT OF WORK IMPLEMENTED
Step 4     =  AUTHENTICATION AND APPLICATION SERVICES CONTRACTS DESIGNED
              (контракт principal реализован; сервисы НЕ реализованы)

Implementation status:  DESIGN AND CONTRACTS ONLY
```

## OPEN DECISIONS

**Открыты и не закрыты этим шагом:** O-1 формат и модель authority API-ключа (Q-1);
O-2 механизм аутентификации; O-3 бэкенд секретов/KMS; O-4 грамматика `secret_ref`;
O-5 топология workspace; O-6 транспорт Platform -> Core; O-7 enum статусов `RunRecord` (Q-7).

**Вновь подняты этим шагом, требуют решения до зависящего от них шага:** Q-2 первая матрица
требований к ролям; Q-3 механизм R-6 против бесконечно running запусков; Q-4 где
записывается идемпотентность Platform; Q-5 как аутентифицируется приём результата.

**Решено в этом документе и обратимо только явным решением:** Q-6 — кросс-арендаторные
попытки отвечаются `not_found`, а не `not_permitted`, по причинам нераскрытия.

**Решено для контракта:** O-8 — `UNIQUE(run_record_id, attempt_number)`.

**Решено и реализовано:** O-9 — дизайн RLS.

**Отложено:** O-10 источник цен; O-11 платёжный процессор; O-12 налоги/инвойсы; O-13
экономика reseller/partner.
