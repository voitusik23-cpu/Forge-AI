# Forge AI — Source of Truth: Implementation Status

> **Purpose.** This file is the single place where a future AI agent can learn
> what is actually implemented in this repository, what is only planned, what is
> experimental, and what has been rejected or deferred — without access to any
> prior chat history.
>
> **Authority.** For **current state**, the source code and the passing test
> suite are authoritative, not this document. This file is a verified index into
> the code; if it disagrees with the code, the code wins and this file is wrong.
> For **target** architecture, [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md)
> is authoritative. For **current** architecture in detail, see
> [`ARCHITECTURE.md`](ARCHITECTURE.md).
>
> **Verified baseline.** Commit `5ec648f` (`feat(agent): extend idempotency
> across trusted entry points`), working tree at the time of writing. Verification
> command, run per test file:
> `python -m unittest discover -s tests -p <file>` → **1625 tests across 99 test
> files**.
>
> **Two files are not OK, and this file does not claim otherwise.**
> `tests/test_api.py::test_07_task_run_via_api` and
> `tests/test_desktop.py::test_04_home_view_task_dispatch` fail with
> `API service unavailable`, which is a provider-side baseline failure in this
> local environment: the configured default provider is `mock`. Neither test
> executes an idempotency path. Everything else in the suite passes.

## Status legend

| Status | Meaning |
| --- | --- |
| **CURRENT / VERIFIED** | Implemented in `app/` and covered by passing tests. Behaviour described below is observable today. |
| **PARTIAL** | Implemented but incomplete relative to its own principle, or implemented with a known limitation that is documented here. |
| **PLANNED / FUTURE** | Designed or specified, not implemented. Must never be described in the present tense. |
| **EXPERIMENTAL / CANDIDATE** | A proposal awaiting an owner decision; see [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md). |
| **REJECTED / DEFERRED** | Considered and not adopted, or postponed. See the rejected-alternatives register in [`DECISIONS.md`](DECISIONS.md). |

Rule: **architecture status cannot be upgraded without evidence.** A roadmap
entry, a stage document, or an external review is not evidence of implementation.

## 1. Product identity (permanent)

Forge AI is an **AI Engineering Factory**, not a coding assistant, not a
multi-model chat, not a proxy, and not an IDE plugin. Its long-term lifecycle is
the one preserved in [`FORGE_VISION.md`](FORGE_VISION.md) and
[`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md):

```text
Human Goal / Existing Software / Website / Data
        -> Discovery
        -> Evidence + Unknowns
        -> Project Brief
        -> Requirements + Acceptance Criteria
        -> Decisions / Approvals
        -> Architecture
        -> Technical Specification
        -> Plan
        -> Implementation
        -> Review
        -> Verification
        -> Ready-to-run Project
        -> Project Memory
        -> Candidate Forge Knowledge
        -> Evaluation
        -> Approved Forge Knowledge
```

**Status: PLANNED / FUTURE as an end-to-end lifecycle.** Currently implemented
are the orchestration, execution, decision, verification and memory sub-systems
listed in §3. Discovery, Project Brief, Architecture/Specification generation,
Ready-to-run Project generation, and the Candidate -> Approved knowledge
promotion workflow do **not** exist as runnable Forge behaviour.

## 2. Non-negotiable architectural principles

These are permanent and are not implementation claims. Violating one of them is
an architecture defect, not a feature choice.

- `Provider != Model != Agent != Skill != Capability != Tool`.
- `Recommendation != Authorization != Execution`.
- AI/LLM decisions never grant execution authority.
- Forge Core remains provider-neutral.
- Generated projects remain independent from Forge Core unless an explicit
  dependency is approved.
- Secrets never belong in source code, Git, ordinary context, logs, or model
  prompts.
- External skills, tools, and catalogs are untrusted until evaluated.
- MCP is not the permission model.
- More agents does not mean better results.
- Retries, rollback, parallelism and swarm execution are not universalized.
- Architecture status cannot be upgraded without evidence.

## 3. Verified implementation matrix

Evidence column: the module that holds the behaviour, and the test file that
covers it. Test file names are the verification; they run in the default
`unittest discover tests` suite.

### 3.1 Orchestration, providers, routing

| Area | Status | Evidence |
| --- | --- | --- |
| Orchestrator / agent registry / provider-neutral task dispatch | **CURRENT / VERIFIED** | `app/orchestrator/orchestrator.py`, `app/agents/` — `tests/test_orchestrator.py`, `tests/test_routing_smoke.py` |
| Provider layer (OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq, Together, Mock, xAI placeholder) | **CURRENT / VERIFIED** (xAI unconfigured placeholder) | `app/agents/providers/` — `tests/test_providers.py`, `tests/test_openai_provider.py`, `tests/test_anthropic_provider.py`, `tests/test_gemini_provider.py`, `tests/test_openai_compatible_providers.py`, `tests/test_together_provider.py` |
| Provider capabilities metadata | **CURRENT / VERIFIED** | `app/agents/providers/capabilities.py` — `tests/test_provider_capabilities.py` |
| Dispatcher, deterministic classification, cost-aware routing | **CURRENT / VERIFIED** | `app/orchestrator/dispatcher.py`, `app/orchestrator/classification.py` — `tests/test_dispatcher.py`, `tests/test_task_classification.py` |
| Provider fallback (finite, sequential) | **CURRENT / VERIFIED** | `app/orchestrator/` — `tests/test_dispatcher.py` |
| SecretStore (lazy resolution, env before `.env`) | **CURRENT / VERIFIED** | `app/config/secrets.py` — `tests/test_secrets.py`, `tests/test_secret_redaction.py` |

### 3.2 Multi-agent execution, planning, tasks

| Area | Status | Evidence |
| --- | --- | --- |
| Multi-agent primary + independent reviewer | **CURRENT / VERIFIED** | `app/agents/reviewer.py`, `app/orchestrator/multi_agent.py` — `tests/test_multi_agent.py` |
| Bounded revision loop (at most one revision) | **CURRENT / VERIFIED** | `app/orchestrator/revision.py` — `tests/test_revision.py` |
| Deterministic Project Planner v0.1 (static plan, no execution) | **CURRENT / VERIFIED** | `app/planning/planner.py` — `tests/test_planner.py` |
| TaskSpecification input boundary | **CURRENT / VERIFIED** | `app/tasks/specification.py` — `tests/test_engineering_execution_profile.py` |
| Requirement ↔ Acceptance traceability | **CURRENT / VERIFIED** | `app/tools/acceptance.py`, `app/tasks/specification.py` — `tests/test_requirement_acceptance_traceability.py` |
| Project State contract | **CURRENT / VERIFIED** | `app/projects/state.py` — `tests/test_project_state.py` |
| Engineering Run executor | **CURRENT / VERIFIED** | `app/orchestrator/engineering.py` — `tests/test_engineering.py`, `tests/test_engineering_execution_integration.py` |
| Run trace / event contract | **CURRENT / VERIFIED** | `app/orchestrator/trace.py` — `tests/test_run_trace.py` |
| Integration failure matrix | **CURRENT / VERIFIED** | `tests/test_integration_failure_matrix.py` |

### 3.3 Decision layer

| Area | Status | Evidence |
| --- | --- | --- |
| Decision layer / run-control recommendations (no execution authority) | **CURRENT / VERIFIED** | `app/decision/` — `tests/test_decision.py` |
| Decision context envelope | **CURRENT / VERIFIED** | `app/context/` — `tests/test_decision_context.py` |
| Real AI decision provider (advisory only; fails closed) | **CURRENT / VERIFIED** | `app/decision/ai_provider.py` — `tests/test_ai_decision_provider.py` |
| Bounded Agent Harness (explicit phase loop, one action per iteration) | **CURRENT / VERIFIED** | `app/agent_runtime/harness.py`, `app/agent_runtime/models.py` — `tests/test_agent_harness.py` |
| Real-AI + harness integration | **CURRENT / VERIFIED** | `tests/test_agent_harness_ai_integration.py` |
| Agent Skill System (registry, evaluator, builtin skills) | **CURRENT / VERIFIED** | `app/skills/` — `tests/test_skill_system.py` |
| Multi-skill agent run | **CURRENT / VERIFIED** | `tests/test_multi_skill_harness.py` |

Note: `app/reviews/` contains only an empty package marker. There is **no**
review-engine module; the reviewer exists as an agent inside
`app/agents/reviewer.py`.

### 3.4 Execution plane and authorization (v0.2)

| Area | Status | Evidence |
| --- | --- | --- |
| Execution plane contracts (`ProjectExecutionProfile`, `ExecutionRequest`, `ExecutionResult`) | **CURRENT / VERIFIED** | `app/execution/profile.py`, `app/execution/request.py` — `tests/test_execution_profile.py`, `tests/test_execution_request_result.py` |
| Permission boundary | **CURRENT / VERIFIED** | `app/execution/authorizer.py` (`allowed_commands` default-deny) — `tests/test_execution_authorization_hardening.py`, `tests/test_execution_authorization_hardening_v02.py` |
| Approval boundary (intent-bound, single-use) | **CURRENT / VERIFIED** | `app/tools/approval.py`, `app/execution/authorizer.py` — `tests/test_approval_boundary.py`, `tests/test_execution_authorization_hardening_v02.py` |
| Workspace boundary for tools (`Workspace`) | **CURRENT / VERIFIED** | `app/tools/workspace.py` — `tests/test_workspace_boundary.py` |
| Immutable `RunScope` trust boundary (external content is data, never authority) | **CURRENT / VERIFIED** | `app/runtime/run_scope.py`, `app/agent_runtime/harness.py` — `tests/test_run_scope_trust_boundary.py` |
| Canonical path security (`ProgramIdentity`, `WorkspaceRelativePath`) | **CURRENT / VERIFIED** | `app/execution/paths.py` — `tests/test_execution_authorization_hardening_v02.py` |
| Immutable `ExecutionIntent` + canonical fingerprint | **CURRENT / VERIFIED** | `app/execution/intent.py` (`IntentBuilder`, `ExecutionIntent.fingerprint`) — `tests/test_execution_authorization_hardening_v02.py` |
| Capability-based invocation classification | **PARTIAL** — see §4.2 | `app/execution/capabilities.py` |
| `AuthorizedExecution` marker bound to the coordinator | **CURRENT / VERIFIED** | `app/execution/intent.py`, `app/execution/adapter.py` — `tests/test_execution_authorization_hardening_v02.py` |
| Mandatory explicit `workspace_root` (fail-closed) | **CURRENT / VERIFIED** | `app/execution/authorizer.py` — `tests/test_execution_authorization_hardening_v02.py`, `tests/test_execution_plane.py` |
| Execution policy over the intent | **CURRENT / VERIFIED** | `app/execution/policy.py` — `tests/test_execution_policy.py` |
| Local execution adapter (ephemeral workspace, scoped env, `shell=False`) | **CURRENT / VERIFIED** | `app/execution/adapter.py` — `tests/test_local_execution_adapter.py`, `tests/test_execution_plane.py` |
| Verification boundary and acceptance gate | **CURRENT / VERIFIED** | `app/tools/verification.py`, `app/tools/acceptance.py` — `tests/test_verification.py`, `tests/test_verification_contract.py`, `tests/test_acceptance.py` |
| Test verification adapter | **CURRENT / VERIFIED** | `app/tools/test_verification.py` — `tests/test_test_verification_adapter.py` |
| Change sets and project snapshots | **CURRENT / VERIFIED** | `app/tools/changesets.py`, `app/snapshots.py` — `tests/test_changesets.py`, `tests/test_project_snapshots.py` |
| Server-side filesystem/workspace boundary (`WorkspaceBoundary`) | **PARTIAL** — see §4.4 | `app/execution/sandbox.py`, enforced in `app/runtime/run_scope.py` and `app/execution/adapter.py` — `tests/test_execution_sandbox_boundary.py` |
| Network authority deny-by-default, frozen in the scope | **PARTIAL** — see §4.5 | `app/execution/profile.py`, `app/runtime/run_scope.py`, `app/execution/adapter.py` — `tests/test_execution_sandbox_boundary.py` |
| Trusted task / criterion identity for the harness entry points | **CURRENT / VERIFIED** | `app/agent_runtime/criterion_identity.py` — `tests/test_criterion_identity_production.py`, `tests/test_identity_binding_hardening.py` |
| Run-level production idempotency (durable ledger, atomic claim, terminal-state protection) | **PARTIAL** — see §4.8 | `app/agent_runtime/idempotency.py`, `app/agent_runtime/idempotency_integration.py`, `app/api/service.py` — `tests/test_idempotency_production.py` |

### 3.5 Memory and knowledge

| Area | Status | Evidence |
| --- | --- | --- |
| Project Memory (models, store, validator) | **CURRENT / VERIFIED** | `app/memory/` — `tests/test_project_memory.py`; see [`STAGE_PROJECT_MEMORY_V0_1.md`](STAGE_PROJECT_MEMORY_V0_1.md) |
| Knowledge Governance (candidate/review/approval models, store, validator) | **CURRENT / VERIFIED** | `app/knowledge/` — `tests/test_knowledge_governance.py`; see [`STAGE_KNOWLEDGE_GOVERNANCE_V0_1.md`](STAGE_KNOWLEDGE_GOVERNANCE_V0_1.md) |

Do not confuse these with the **lifecycle** stages. The distinguishing rules are:

- **Project Memory** = project-specific durable truth and history. It is scoped
  to a project/run and is not global.
- **Forge Knowledge** = reviewed, reusable knowledge and patterns with
  provenance, applicability, evidence, and an approval decision.
- **Domain Knowledge** = domain-scoped information that remains domain-scoped.

Knowledge does **not** become global because an agent observed it once. Promotion
requires the governance workflow (candidate -> evaluation -> approved).

### 3.6 Platform (Stage 1)

Stage 1 is being built as ordered steps. Steps 1 through 3 are implemented; the
remaining steps are not, and nothing below should be read as a claim about them.

| Area | Status | Evidence |
| --- | --- | --- |
| Platform domain contracts (8 records, 8 status enums, stdlib-only, no driver) | **CURRENT / VERIFIED** | `app/platform/models.py`, `app/platform/enums.py` — `tests/test_platform_domain_contracts.py` |
| PostgreSQL schema with migrations, roles, `FORCE` row-level security, and append-only tables | **CURRENT / VERIFIED** | `app/platform/persistence/postgres/migrations/0001..0014_*.sql`, `postgres/schema.sql` — `tests/test_platform_postgres_schema.py` |
| Tenant containment, role separation, system-scope containment, append-only usage, membership provenance | **CURRENT / VERIFIED** | migrations `0002`, `0011`–`0014` — `tests/test_platform_postgres_security.py` |
| Repositories, Unit of Work, three scopes, error normalization, identifier boundary | **CURRENT / VERIFIED** | `app/platform/persistence/{database,repositories,mapper,protocols,errors}.py` — `tests/test_platform_persistence.py` |
| Platform Application Services over those repositories | **PLANNED / NOT IMPLEMENTED** | no such module exists; `app/platform/persistence/protocols.py` is the interface they will use |
| Platform HTTP API, authentication, API-key token handling | **PLANNED / NOT IMPLEMENTED** | see O-1 and O-2 in `DECISIONS.md`; no endpoint and no token column exist |
| Platform -> Core transport (`TrustedExecutionRequest`) | **PLANNED / NOT IMPLEMENTED** | Stage 0.2; see `ROADMAP.md` |
| Billing (cost records, pricing, wallet, ledger) | **PLANNED / NOT IMPLEMENTED** | Stage 2; no such table and no money column exist |

Two properties of this layer are worth stating explicitly, because a reader will
otherwise assume the opposite:

- **`forge.system_scope` is not an authorization mechanism.** Any session can set a
  custom GUC. What gates the system scope is the policy's `TO` clause together with
  the database role, and a deployment serves tenant and system traffic through two
  different logins. The persistence layer refuses to build a connection pool when the
  effective role is a superuser, has `BYPASSRLS`, or owns the Platform tables.
- **There is no money in the persistence layer.** `usage_records` holds physical
  measurements only: tokens, timings, counts, and flags. No cost, price, charge,
  balance, or currency column exists anywhere.

## 4. Verified limitations

These are current, observed properties of the implementation. They are recorded
so that no future agent re-discovers them, and so that no document claims
otherwise.

### 4.1 Approval fingerprint verification depends on resolver cooperation

`ExecutionCoordinator` builds the `ExecutionIntent`, then asks the resolver with
`ApprovalRequest(intent_fingerprint=intent.fingerprint)`. It verifies the returned
fingerprint **only when the resolver supplies a non-empty one**:

```python
res_fp = getattr(resolution, "approved_fingerprint", "")
...
if res_fp and res_fp != intent.fingerprint:   # empty res_fp short-circuits
```

`InMemoryApprovalResolver` always supplies `approved_fingerprint`, so the shipped
path is safe. A resolver that returns a bare `ApprovalState.APPROVED` (no
fingerprint) authorizes the current intent without any independent binding check.
Observed by probe: an always-`APPROVED`, no-echo resolver produced
`EXECUTION_SUCCESS` for a command that the intent-binding rule is meant to gate.

**Status: PARTIAL (hardening candidate).** See `DECISIONS.md`
("Execution Authorization Contract v0.2" -> unresolved items).

### 4.2 Capability classification is a blacklist, not a capability model

`ExecutionCapability` currently has four members: `EXEC_CHILD`,
`INTERPRET_TEXT`, `INTERPRET_MODULE`, `NETWORK`. `classify_invocation()` derives
them from hard-coded name lists and flag regexes:

- `_WRAPPER_EXECUTABLES` — 15 names (`env`, `xargs`, `sudo`, `doas`, `chroot`,
  `nohup`, `strace`, `ltrace`, `time`, `nice`, `ionice`, `taskset`, `timeout`,
  `watch`, `runas`) plus `find -exec`.
- `_NETWORK_EXECUTABLES` — 16 names plus git network subcommands.
- Interpreter flag regexes for python/node/ruby/perl/php/bash/sh/zsh/dash/ksh,
  powershell/pwsh, cmd.

Anything outside those lists gets `capabilities == frozenset()`, and the policy
then admits a bare-string `allowed_commands` entry with **free-form argv**. This
contradicts the standing principle that security must not depend on an
ever-growing blacklist of executables (`AGENTS.md` §10 and the execution
authorization invariants), and it is the known residual of the F4 class: a
wrapper that nobody has listed yet is not gated.

**Status: PARTIAL.** The invariant enforced today is "a recognised wrapper or
interpreter eval/module flag requires a declared capability", not "unknown
execution behaviour fails closed". The direction recorded for correction is the
declared-invocation / fail-closed-`OPAQUE` model in `DECISIONS.md`.

### 4.3 Intent environment does not include profile environment

`IntentBuilder.from_request()` sets the intent's `environment_variables` from the
**request** only. The adapter later overlays the **profile** environment at
process start (`_build_scoped_environment`). Consequence: a change to
`ProjectExecutionProfile.environment_variables` does not change the intent
fingerprint. Observed by probe: intent env = `{'REQUEST_MARKER': ...}` with the
profile marker absent.

**Status: PARTIAL (hardening candidate).**

### 4.4 There is a server-side filesystem boundary, and no OS-level sandbox

Two different things must not be confused here.

**Implemented (PARTIAL): a server-side filesystem/workspace boundary.**
`app/execution/sandbox.py::WorkspaceBoundary` is the single canonical answer to
"is this path inside the run's workspace?". It canonicalizes the workspace root
with `os.path.realpath` (which follows junctions and substituted drives, not only
symlinks), normalizes the requested path, then canonicalizes the result and proves
containment on canonical paths. It refuses:

- parent traversal (`../`, `sub/../../x`), including traversal that ends back
  inside the workspace;
- absolute paths outside the workspace, and Windows drive paths;
- UNC and device paths, empty paths, and NUL-bearing paths;
- a directory junction or symlink whose name sits inside the workspace but which
  resolves outside it.

It is enforced twice: at the authority boundary in
`RunScope.validate_execution_request` (working directory and artifact targets), and
again inside `LocalExecutionAdapter._run_process` immediately before the child
process is created, which then receives the canonicalized path. The boundary is
always built with `WorkspaceBoundary.for_workspace(...)` from a trusted root,
never from the path being checked. The workspace root always comes from the frozen
scope, never from a task, a request, or metadata.

**Not implemented: OS/kernel-level isolation.** `LocalExecutionAdapter` runs local
processes with `shell=False`, an ephemeral copy of the workspace, a scoped
environment whitelist, timeouts with process-tree termination, output caps, and
secret redaction. It does **not** provide namespaces, cgroups, Job Objects, or
filesystem confinement enforced by the kernel. A spawned process inherits the
parent's security token and may open any path that token may open; a child that
opens an absolute path outside the workspace is not stopped. This residual risk is
recorded in `RESIDUAL_RISK` and asserted by
`tests/test_execution_sandbox_boundary.py::test_child_process_can_still_read_outside_the_workspace`.

**Block 2 split into its actual parts.** The filesystem/workspace boundary is
**PARTIAL/IMPLEMENTED** as above. OS-level sandbox and isolation remain
**DEFERRED**. Workspace-input staging and mirroring remain **PARTIAL** (see §4.5).
The previous blanket statement that Block 2 "is NOT implemented" was stale and has
been removed.

### 4.5 Network authority is deny-by-default; the network is not isolated

Network authority is server-side and fail-closed.
`ProjectExecutionProfile.network_access` defaults to `False`, the value is frozen
into the `RunScope`, and `RunScope.validate_execution_profile` refuses any request
profile that tries to enable it (`run scope cannot enable network access`). A task,
a decision, a plan, a tool intent, a `HarnessRequest`, or metadata cannot enable
it, and `ToolIntent` refuses `network`/`network_access` outright as
authority-shaped argument keys. A resume must reproduce the recorded operation
under the current authority ceiling
(`ResumeContract.validate_against_current_authority`), so a broader current
authority cannot be obtained from persisted state.

**Enforcement mechanism.** When network access is denied, `LocalExecutionAdapter`
points `http_proxy`/`https_proxy`/`all_proxy` (and the uppercase variants) at a
closed port and clears `NO_PROXY`. This stops proxy-honouring HTTP clients and does
nothing to a process that opens a socket itself.

**Not implemented: kernel network isolation.** There is no network namespace, no
firewall rule, and no container. Direct socket creation, DNS resolution, IPv4 and
IPv6 sockets, and direct outbound connections all still work; this is asserted by
`tests/test_execution_sandbox_boundary.py::test_proxy_denial_is_not_a_kernel_sandbox`.
The denial is a **policy boundary, not a kernel sandbox**, and the module and
documentation say so explicitly. GAP-B is therefore PARTIAL.

### 4.6 Staging is mirror-based, not declared-inputs

`EphemeralWorkspaceManager` copies the supplied workspace root (skipping `.git`
and `__pycache__`) into a temporary scratch directory. There is no declared-input
allow-list, no workspace-identity binding, and no content digest for referenced
inputs. Whatever the caller passes as `workspace_root` is what gets staged.

**Status: PARTIAL** (workspace identity, declared inputs, deny-by-default
staging are Block 2 / deferred).

### 4.7 The execution plane has no production entry point

`app/runtime/bootstrap.py::create_runtime` assembles settings, providers,
registries, the Orchestrator and `RunExecutor`. It does **not** construct an
`ExecutionCoordinator`, an adapter, or a workspace root. The coordinator is
constructed by callers: `app/agent_runtime/harness.py` and
`app/orchestrator/engineering.py`, both from caller-supplied request fields
(`HarnessRequest.workspace`, `EngineeringRunRequest.workspace`). When the
workspace is absent, a local spawn is denied with
`workspace_root_required`.

**Partially superseded.** This remains true of `create_runtime` itself, but the
production run loop now exists and is reachable from trusted server-side code:
`ForgeApiService.run_agent_loop`, `run_accepted_task`, and
`run_declared_verification` are the production entry points, and
`ForgeApiService.run_task` remains the HTTP/desktop compatibility path. What is
still latent is the *resume* path, not the execution path — see §4.8.

### 4.8 Production idempotency exists; a resume driver does not

Run-level idempotency is implemented, committed (`0244157`, extended by `5ec648f`),
and covered by tests. A trusted operator may pass an `idempotency_key` to one of the
**three trusted in-process entry points**:

| entry point | operation class |
| --- | --- |
| `ForgeApiService.run_agent_loop` | `AGENT_LOOP` |
| `ForgeApiService.run_accepted_task` | `ACCEPTED_TASK` |
| `ForgeApiService.run_declared_verification` | `DECLARED_VERIFICATION` |

Without a key each call keeps its previous semantics exactly.

What is implemented:

- a **durable idempotency ledger**, one file per key
  (`app/agent_runtime/idempotency.py`), separate from `RunStore`, which remains an
  observation sink and is not a resume engine;
- an **atomic claim** through `O_EXCL` create-if-absent, so two simultaneous
  deliveries of one key cannot both win;
- three outcomes - **claim** (start), **replay** (a terminal record is reported
  instead of re-executed), and **conflict** (the key is bound to a different
  operation identity);
- identity binding over the operation class, the canonical key, the trusted task
  fingerprint, and every criterion fingerprint;
- **terminal-state protection** enforced in code by
  `validate_lifecycle_transition`: `COMPLETED`, `FAILED`, `LIMIT_REACHED`,
  `DENIED`, and `SECURITY_FAILURE` cannot be reopened, a refused write leaves the
  durable record untouched, and an unprovable side effect cannot be moved into a
  recoverable state;
- **current-authority revalidation**, so a resume must reproduce the recorded
  operation under the current authority ceiling;
- the claim is taken **before** the scope freeze, the `RunStore` binding, and the
  durable `RUN_STARTED` event, so a duplicate writes no run-start event, freezes no
  scope, and starts no run.

The **idempotency key is not authority**. It cannot carry a command, a workspace, a
profile, a tool set, network access, or an approval; it only selects which durable
operation record a delivery belongs to.

`run_task`, the HTTP task-run path, and desktop dispatch are **outside** this
protection: their `task_id` is caller-supplied, so there is no trusted task
identity to bind a key to. This is deliberately not "all entry points protected".

Resume is **contract only**. `ResumeContract` plus the durable lifecycle state
machine are implemented and tested, including lineage through
`AttemptIdentity`. There is **no operator resume driver**: `AgentHarness.run()`
always starts at `HarnessPhase.OBSERVE` and has no start-from-checkpoint
parameter, so an automatic crash-resume would re-run the whole loop rather than
continue safely. **Nothing in the current implementation claims working
crash-resume.**

The **`SideEffectLedger` is NOT WIRED into production.** Two-phase side-effect
recording (`NOT_STARTED`/`STARTED`/`COMMITTED`/`FAILED`, plus the derived
`UNKNOWN_AFTER_CRASH`) is implemented and tested as a component, but
`begin_side_effect` and `complete_side_effect` have no caller in `app/` outside the
module, so `UNKNOWN_AFTER_CRASH` is not reachable from the current production run
path. GAP-I and GAP-R are PARTIAL.

## 5. Planning, deferred and rejected

| Item | Status | Where recorded |
| --- | --- | --- |
| Discovery intelligence, evidence, readiness gates | **PLANNED / FUTURE** | `TECHNICAL_SPECIFICATION.md` §21 |
| Project classification, capability selection | **PLANNED / FUTURE** | `TECHNICAL_SPECIFICATION.md` §22 |
| Durable runs, resumable execution | **PLANNED / FUTURE** | [`STAGE_1_5_EXTERNAL_BENCHMARK.md`](STAGE_1_5_EXTERNAL_BENCHMARK.md) |
| Agent/Skill/Tool/Capability governance beyond the current skill system | **PLANNED / FUTURE** | [`STAGE_1_6_AGENT_SKILL_SYSTEM.md`](STAGE_1_6_AGENT_SKILL_SYSTEM.md) |
| Domain patterns and integrations | **CANDIDATE** | [`STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md`](STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md) |
| Declared-invocation / fail-closed capability model | **PLANNED (candidate for Block 1.1)** | `DECISIONS.md` Execution Authorization Contract v0.2 |
| Block 2 filesystem/workspace boundary | **PARTIAL / IMPLEMENTED** — see §4.4 | `DECISIONS.md` (Execution Filesystem Boundary and Network Policy v0.1), `ROADMAP.md` GAP-A |
| Block 2 OS-level sandbox and kernel isolation | **DEFERRED** | `DECISIONS.md`, `ROADMAP.md` |
| Block 2 declared workspace inputs / deny-by-default staging | **DEFERRED** | `DECISIONS.md`, `ROADMAP.md` |
| Operator resume driver for interrupted runs | **PLANNED / FOLLOW-UP** — contract exists, driver does not (§4.8) | `ROADMAP.md` GAP-R, `DECISIONS.md` |
| Side-effect journal wired into production execution | **PLANNED / FOLLOW-UP** — component exists, not wired (§4.8) | `ROADMAP.md`, `DECISIONS.md` |
| Server-derived operation identity for `run_task` / HTTP / desktop idempotency | **PLANNED / FOLLOW-UP** | `ROADMAP.md` GAP-I |
| Compare-and-set, lease or distributed lock for cross-process claims | **DEFERRED** — documented limitation, deliberately not simulated | `ROADMAP.md`, `DECISIONS.md` |
| Global action-level suppression across idempotency keys | **DEFERRED** — protection is key-scoped today | `ROADMAP.md`, `DECISIONS.md` |
| Durable/persistent approval store with expiration | **DEFERRED (separate design)** | `DECISIONS.md` rejected-alternatives register |
| MCP as permission model | **REJECTED** | `DECISIONS.md` |
| Universal retries / rollback / parallelism / swarm execution | **REJECTED as defaults** | `AGENTS.md`, `TECHNICAL_SPECIFICATION.md` §18 |
| Whole-workspace content digests, cryptographic approval tokens, field registries | **REJECTED for the current block** | `DECISIONS.md` rejected-alternatives register |
| Technology candidates (NaraRouter, NVIDIA, QwenCloud, OmniRoute, Headroom, Task Observer, Claude-Mem, Everything Claude Code, Matt Pocock Skills, Ghidra, DBeaver, MarkItDown, MCP, Runnable, Kimi Work, Canva connector) | **CANDIDATE / WATCH** | [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md) |

## 6. How to keep this file true

1. Change code, then change this file — never the reverse.
2. Never move an item from PLANNED to CURRENT without a passing test that
   exercises it and a commit that contains it.
3. When a new execution-affecting field, boundary, or subsystem appears, add a
   row here and record the decision in `DECISIONS.md`.
4. When a limitation in §4 is fixed, delete the limitation and note the fix in
   `DECISIONS.md`; do not leave stale "known limitation" text behind.

---

# Forge AI — Source of Truth: статус реализации — русская версия

> **Назначение.** Этот файл — единственное место, где будущий AI-агент может
> узнать, что реально реализовано в этом репозитории, что только запланировано,
> что экспериментально и что отклонено или отложено, — без доступа к предыдущей
> истории чатов.
>
> **Авторитетность.** Для **текущего состояния** авторитетны исходный код и
> проходящий набор тестов, а не этот документ. Этот файл — проверенный индекс по
> коду; если он расходится с кодом, побеждает код, а этот файл неверен. Для
> **целевой** архитектуры авторитетен
> [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md). Подробное описание
> **текущей** архитектуры — в [`ARCHITECTURE.md`](ARCHITECTURE.md).
>
> **Проверенная база.** Коммит `5ec648f`
> (`feat(agent): extend idempotency across trusted entry points`), рабочее дерево
> на момент написания. Команда проверки, по каждому тестовому файлу:
> `python -m unittest discover -s tests -p <file>` → **1625 тестов в 99 тестовых
> файлах**.
>
> **Два файла не OK, и этот документ не утверждает обратного.**
> `tests/test_api.py::test_07_task_run_via_api` и
> `tests/test_desktop.py::test_04_home_view_task_dispatch` падают с
> `API service unavailable` — это provider-side отказ в текущем локальном
> окружении: настроенный по умолчанию провайдер — `mock`. Ни один из этих тестов
> не исполняет idempotency-путь. Всё остальное в наборе проходит.

## Легенда статусов

| Статус | Значение |
| --- | --- |
| **CURRENT / VERIFIED** | Реализовано в `app/` и покрыто проходящими тестами. Описанное ниже поведение наблюдаемо сегодня. |
| **PARTIAL** | Реализовано, но неполно относительно собственного принципа, либо реализовано с известным ограничением, задокументированным здесь. |
| **PLANNED / FUTURE** | Спроектировано или специфицировано, но не реализовано. Никогда не должно описываться в настоящем времени. |
| **EXPERIMENTAL / CANDIDATE** | Предложение, ожидающее решения владельца; см. [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md). |
| **REJECTED / DEFERRED** | Рассмотрено и не принято либо отложено. См. реестр отклонённых альтернатив в [`DECISIONS.md`](DECISIONS.md). |

Правило: **статус архитектуры нельзя повысить без доказательства.** Пункт
roadmap, stage-документ или внешнее ревью не являются доказательством реализации.

## 1. Идентичность продукта (постоянно)

Forge AI — это **AI Engineering Factory**, а не ассистент для кодинга, не
мультимодельный чат, не прокси и не плагин IDE. Его долгосрочный жизненный цикл —
тот, что зафиксирован в [`FORGE_VISION.md`](FORGE_VISION.md) и
[`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md):

```text
Human Goal / Existing Software / Website / Data
        -> Discovery
        -> Evidence + Unknowns
        -> Project Brief
        -> Requirements + Acceptance Criteria
        -> Decisions / Approvals
        -> Architecture
        -> Technical Specification
        -> Plan
        -> Implementation
        -> Review
        -> Verification
        -> Ready-to-run Project
        -> Project Memory
        -> Candidate Forge Knowledge
        -> Evaluation
        -> Approved Forge Knowledge
```

**Статус: PLANNED / FUTURE как сквозной жизненный цикл.** Сейчас реализованы
подсистемы оркестрации, выполнения, решений, верификации и памяти, перечисленные
в §3. Discovery, Project Brief, генерация Architecture/Specification, генерация
Ready-to-run Project и workflow продвижения знаний Candidate -> Approved **не
существуют** как исполняемое поведение Forge.

## 2. Непреложные архитектурные принципы

Они постоянны и не являются утверждениями о реализации. Нарушение любого из них —
дефект архитектуры, а не выбор функциональности.

- `Provider != Model != Agent != Skill != Capability != Tool`.
- `Recommendation != Authorization != Execution`.
- Решения AI/LLM никогда не выдают полномочия на выполнение.
- Ядро Forge остаётся провайдер-нейтральным.
- Генерируемые проекты остаются независимыми от Forge Core, если явно не одобрена
  зависимость.
- Секреты никогда не должны находиться в исходном коде, Git, обычном контексте,
  логах или промптах моделей.
- Внешние skills, tools и каталоги недоверенны до оценки.
- MCP не является моделью разрешений.
- Больше агентов не означает лучший результат.
- Retries, rollback, параллелизм и swarm-исполнение не универсализируются.
- Статус архитектуры нельзя повысить без доказательства.

## 3. Проверенная матрица реализации

Колонка Evidence: модуль, в котором находится поведение, и тестовый файл, который
его покрывает. Имена тестовых файлов и есть проверка; они запускаются в наборе по
умолчанию `unittest discover tests`.

### 3.1 Оркестрация, провайдеры, маршрутизация

| Область | Статус | Evidence |
| --- | --- | --- |
| Orchestrator / реестр агентов / провайдер-нейтральная диспетчеризация задач | **CURRENT / VERIFIED** | `app/orchestrator/orchestrator.py`, `app/agents/` — `tests/test_orchestrator.py`, `tests/test_routing_smoke.py` |
| Слой провайдеров (OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq, Together, Mock, заглушка xAI) | **CURRENT / VERIFIED** (xAI — ненастроенная заглушка) | `app/agents/providers/` — `tests/test_providers.py`, `tests/test_openai_provider.py`, `tests/test_anthropic_provider.py`, `tests/test_gemini_provider.py`, `tests/test_openai_compatible_providers.py`, `tests/test_together_provider.py` |
| Метаданные capabilities провайдеров | **CURRENT / VERIFIED** | `app/agents/providers/capabilities.py` — `tests/test_provider_capabilities.py` |
| Dispatcher, детерминированная классификация, cost-aware маршрутизация | **CURRENT / VERIFIED** | `app/orchestrator/dispatcher.py`, `app/orchestrator/classification.py` — `tests/test_dispatcher.py`, `tests/test_task_classification.py` |
| Fallback провайдеров (конечный, последовательный) | **CURRENT / VERIFIED** | `app/orchestrator/` — `tests/test_dispatcher.py` |
| SecretStore (ленивое разрешение, окружение процесса раньше `.env`) | **CURRENT / VERIFIED** | `app/config/secrets.py` — `tests/test_secrets.py`, `tests/test_secret_redaction.py` |

### 3.2 Многоагентное выполнение, планирование, задачи

| Область | Статус | Evidence |
| --- | --- | --- |
| Многоагентный primary + независимый reviewer | **CURRENT / VERIFIED** | `app/agents/reviewer.py`, `app/orchestrator/multi_agent.py` — `tests/test_multi_agent.py` |
| Ограниченный цикл ревизии (не более одной ревизии) | **CURRENT / VERIFIED** | `app/orchestrator/revision.py` — `tests/test_revision.py` |
| Детерминированный Project Planner v0.1 (статический план, без выполнения) | **CURRENT / VERIFIED** | `app/planning/planner.py` — `tests/test_planner.py` |
| Входная граница TaskSpecification | **CURRENT / VERIFIED** | `app/tasks/specification.py` — `tests/test_engineering_execution_profile.py` |
| Трассируемость Requirement ↔ Acceptance | **CURRENT / VERIFIED** | `app/tools/acceptance.py`, `app/tasks/specification.py` — `tests/test_requirement_acceptance_traceability.py` |
| Контракт Project State | **CURRENT / VERIFIED** | `app/projects/state.py` — `tests/test_project_state.py` |
| Исполнитель Engineering Run | **CURRENT / VERIFIED** | `app/orchestrator/engineering.py` — `tests/test_engineering.py`, `tests/test_engineering_execution_integration.py` |
| Контракт трассы/событий запуска | **CURRENT / VERIFIED** | `app/orchestrator/trace.py` — `tests/test_run_trace.py` |
| Матрица отказов интеграции | **CURRENT / VERIFIED** | `tests/test_integration_failure_matrix.py` |

### 3.3 Слой решений

| Область | Статус | Evidence |
| --- | --- | --- |
| Слой решений / рекомендации управления запуском (без полномочий выполнения) | **CURRENT / VERIFIED** | `app/decision/` — `tests/test_decision.py` |
| Конверт контекста решений | **CURRENT / VERIFIED** | `app/context/` — `tests/test_decision_context.py` |
| Реальный AI decision provider (только рекомендации; fail-closed) | **CURRENT / VERIFIED** | `app/decision/ai_provider.py` — `tests/test_ai_decision_provider.py` |
| Ограниченный Agent Harness (явный цикл фаз, одно действие за итерацию) | **CURRENT / VERIFIED** | `app/agent_runtime/harness.py`, `app/agent_runtime/models.py` — `tests/test_agent_harness.py` |
| Интеграция реального AI с harness | **CURRENT / VERIFIED** | `tests/test_agent_harness_ai_integration.py` |
| Agent Skill System (реестр, evaluator, встроенные skills) | **CURRENT / VERIFIED** | `app/skills/` — `tests/test_skill_system.py` |
| Запуск агента с несколькими skills | **CURRENT / VERIFIED** | `tests/test_multi_skill_harness.py` |

Примечание: `app/reviews/` содержит только пустой маркер пакета. Модуля
review-engine **нет**; reviewer существует как агент внутри
`app/agents/reviewer.py`.

### 3.4 Execution plane и авторизация (v0.2)

| Область | Статус | Evidence |
| --- | --- | --- |
| Контракты execution plane (`ProjectExecutionProfile`, `ExecutionRequest`, `ExecutionResult`) | **CURRENT / VERIFIED** | `app/execution/profile.py`, `app/execution/request.py` — `tests/test_execution_profile.py`, `tests/test_execution_request_result.py` |
| Граница Permission | **CURRENT / VERIFIED** | `app/execution/authorizer.py` (`allowed_commands` default-deny) — `tests/test_execution_authorization_hardening.py`, `tests/test_execution_authorization_hardening_v02.py` |
| Граница Approval (привязана к интенту, одноразовая) | **CURRENT / VERIFIED** | `app/tools/approval.py`, `app/execution/authorizer.py` — `tests/test_approval_boundary.py`, `tests/test_execution_authorization_hardening_v02.py` |
| Граница Workspace для инструментов (`Workspace`) | **CURRENT / VERIFIED** | `app/tools/workspace.py` — `tests/test_workspace_boundary.py` |
| Неизменяемая граница доверия `RunScope` (внешнее содержимое — данные, а не полномочия) | **CURRENT / VERIFIED** | `app/runtime/run_scope.py`, `app/agent_runtime/harness.py` — `tests/test_run_scope_trust_boundary.py` |
| Каноническая безопасность путей (`ProgramIdentity`, `WorkspaceRelativePath`) | **CURRENT / VERIFIED** | `app/execution/paths.py` — `tests/test_execution_authorization_hardening_v02.py` |
| Неизменяемый `ExecutionIntent` + канонический fingerprint | **CURRENT / VERIFIED** | `app/execution/intent.py` (`IntentBuilder`, `ExecutionIntent.fingerprint`) — `tests/test_execution_authorization_hardening_v02.py` |
| Классификация инвокаций на основе capabilities | **PARTIAL** — см. §4.2 | `app/execution/capabilities.py` |
| Маркер `AuthorizedExecution`, привязанный к координатору | **CURRENT / VERIFIED** | `app/execution/intent.py`, `app/execution/adapter.py` — `tests/test_execution_authorization_hardening_v02.py` |
| Обязательный явный `workspace_root` (fail-closed) | **CURRENT / VERIFIED** | `app/execution/authorizer.py` — `tests/test_execution_authorization_hardening_v02.py`, `tests/test_execution_plane.py` |
| Execution policy по интенту | **CURRENT / VERIFIED** | `app/execution/policy.py` — `tests/test_execution_policy.py` |
| Локальный адаптер выполнения (временный workspace, ограниченное окружение, `shell=False`) | **CURRENT / VERIFIED** | `app/execution/adapter.py` — `tests/test_local_execution_adapter.py`, `tests/test_execution_plane.py` |
| Граница верификации и acceptance gate | **CURRENT / VERIFIED** | `app/tools/verification.py`, `app/tools/acceptance.py` — `tests/test_verification.py`, `tests/test_verification_contract.py`, `tests/test_acceptance.py` |
| Адаптер верификации тестов | **CURRENT / VERIFIED** | `app/tools/test_verification.py` — `tests/test_test_verification_adapter.py` |
| Change sets и снапшоты проектов | **CURRENT / VERIFIED** | `app/tools/changesets.py`, `app/snapshots.py` — `tests/test_changesets.py`, `tests/test_project_snapshots.py` |
| Server-side файловая/workspace-граница (`WorkspaceBoundary`) | **PARTIAL** — см. §4.4 | `app/execution/sandbox.py`, применяется в `app/runtime/run_scope.py` и `app/execution/adapter.py` — `tests/test_execution_sandbox_boundary.py` |
| Сетевая authority deny-by-default, замороженная в scope | **PARTIAL** — см. §4.5 | `app/execution/profile.py`, `app/runtime/run_scope.py`, `app/execution/adapter.py` — `tests/test_execution_sandbox_boundary.py` |
| Доверенная идентичность task/criterion для точек входа harness | **CURRENT / VERIFIED** | `app/agent_runtime/criterion_identity.py` — `tests/test_criterion_identity_production.py`, `tests/test_identity_binding_hardening.py` |
| Run-level production idempotency (durable журнал, атомарный claim, защита терминальных состояний) | **PARTIAL** — см. §4.8 | `app/agent_runtime/idempotency.py`, `app/agent_runtime/idempotency_integration.py`, `app/api/service.py` — `tests/test_idempotency_production.py` |

### 3.5 Память и знания

| Область | Статус | Evidence |
| --- | --- | --- |
| Project Memory (модели, store, validator) | **CURRENT / VERIFIED** | `app/memory/` — `tests/test_project_memory.py`; см. [`STAGE_PROJECT_MEMORY_V0_1.md`](STAGE_PROJECT_MEMORY_V0_1.md) |
| Knowledge Governance (модели candidate/review/approval, store, validator) | **CURRENT / VERIFIED** | `app/knowledge/` — `tests/test_knowledge_governance.py`; см. [`STAGE_KNOWLEDGE_GOVERNANCE_V0_1.md`](STAGE_KNOWLEDGE_GOVERNANCE_V0_1.md) |

Не путайте их со стадиями **жизненного цикла**. Различающие правила таковы:

- **Project Memory** — проектно-специфичная долговременная истина и история. Она
  ограничена проектом/запуском и не является глобальной.
- **Forge Knowledge** — проверенные переиспользуемые знания и паттерны с
  происхождением, применимостью, свидетельствами и решением об одобрении.
- **Domain Knowledge** — информация, ограниченная предметной областью и остающаяся
  в её пределах.

Знание **не** становится глобальным только потому, что агент однажды его
наблюдал. Продвижение требует workflow governance (candidate -> evaluation ->
approved).

### 3.6 Platform (Stage 1) — русская версия

Stage 1 строится упорядоченными шагами. Шаги с 1 по 3 реализованы; остальные — нет,
и ничто ниже не должно читаться как утверждение о них.

| Область | Статус | Evidence |
| --- | --- | --- |
| Доменные контракты Platform (8 записей, 8 enum'ов статусов, только stdlib, без драйвера) | **CURRENT / VERIFIED** | `app/platform/models.py`, `app/platform/enums.py` — `tests/test_platform_domain_contracts.py` |
| Схема PostgreSQL с миграциями, ролями, `FORCE` row-level security и append-only таблицами | **CURRENT / VERIFIED** | `app/platform/persistence/postgres/migrations/0001..0014_*.sql`, `postgres/schema.sql` — `tests/test_platform_postgres_schema.py` |
| Изоляция арендаторов, разделение ролей, сдерживание system-скоупа, append-only потребление, происхождение membership | **CURRENT / VERIFIED** | миграции `0002`, `0011`–`0014` — `tests/test_platform_postgres_security.py` |
| Репозитории, Unit of Work, три скоупа, нормализация ошибок, граница идентификаторов | **CURRENT / VERIFIED** | `app/platform/persistence/{database,repositories,mapper,protocols,errors}.py` — `tests/test_platform_persistence.py` |
| Прикладные сервисы Platform поверх этих репозиториев | **PLANNED / NOT IMPLEMENTED** | такого модуля не существует; `app/platform/persistence/protocols.py` — интерфейс, которым они будут пользоваться |
| HTTP API Platform, аутентификация, обработка токенов API-ключей | **PLANNED / NOT IMPLEMENTED** | см. O-1 и O-2 в `DECISIONS.md`; ни эндпоинта, ни колонки токена не существует |
| Транспорт Platform -> Core (`TrustedExecutionRequest`) | **PLANNED / NOT IMPLEMENTED** | Stage 0.2; см. `ROADMAP.md` |
| Billing (cost records, ценообразование, кошелёк, леджер) | **PLANNED / NOT IMPLEMENTED** | Stage 2; ни такой таблицы, ни денежной колонки не существует |

Два свойства этого слоя стоит назвать прямо, иначе читатель предположит обратное:

- **`forge.system_scope` не является механизмом авторизации.** Любая сессия может
  установить пользовательскую GUC. Доступ к system-скоупу даёт клауза `TO` в
  политике вместе с ролью базы данных, а деплой обслуживает трафик арендатора и
  system-трафик двумя разными логинами. Слой персистентности отказывается создавать
  пул соединений, если эффективная роль — суперпользователь, имеет `BYPASSRLS` или
  владеет таблицами Platform.
- **В слое персистентности нет денег.** `usage_records` хранит только физические
  измерения: токены, тайминги, счётчики и флаги. Ни колонки стоимости, цены, списания,
  баланса или валюты не существует нигде.

## 4. Проверенные ограничения

Это текущие наблюдаемые свойства реализации. Они зафиксированы, чтобы будущий
агент не открывал их заново и чтобы ни один документ не утверждал иного.

### 4.1 Проверка fingerprint одобрения зависит от содействия resolver'а

`ExecutionCoordinator` строит `ExecutionIntent`, затем обращается к resolver'у с
`ApprovalRequest(intent_fingerprint=intent.fingerprint)`. Он проверяет возвращённый
fingerprint **только когда resolver передал непустой**:

```python
res_fp = getattr(resolution, "approved_fingerprint", "")
...
if res_fp and res_fp != intent.fingerprint:   # empty res_fp short-circuits
```

`InMemoryApprovalResolver` всегда передаёт `approved_fingerprint`, поэтому
поставляемый путь безопасен. Resolver, возвращающий голый
`ApprovalState.APPROVED` (без fingerprint), авторизует текущий интент без
независимой проверки привязки. Наблюдено пробой: resolver, всегда возвращающий
`APPROVED` без эха fingerprint, дал `EXECUTION_SUCCESS` для команды, которую
правило привязки к интенту должно было ограничить.

**Статус: PARTIAL (кандидат на усиление).** См. `DECISIONS.md`
("Execution Authorization Contract v0.2" -> нерешённые пункты).

### 4.2 Классификация capabilities — это чёрный список, а не модель capabilities

`ExecutionCapability` сейчас содержит четыре члена: `EXEC_CHILD`,
`INTERPRET_TEXT`, `INTERPRET_MODULE`, `NETWORK`. `classify_invocation()` выводит их
из жёстко заданных списков имён и регулярных выражений флагов:

- `_WRAPPER_EXECUTABLES` — 15 имён (`env`, `xargs`, `sudo`, `doas`, `chroot`,
  `nohup`, `strace`, `ltrace`, `time`, `nice`, `ionice`, `taskset`, `timeout`,
  `watch`, `runas`) плюс `find -exec`.
- `_NETWORK_EXECUTABLES` — 16 имён плюс сетевые подкоманды git.
- Регулярные выражения флагов интерпретаторов для python/node/ruby/perl/php/bash/sh/zsh/dash/ksh,
  powershell/pwsh, cmd.

Всё, что вне этих списков, получает `capabilities == frozenset()`, и тогда политика
допускает строковую запись `allowed_commands` с **произвольным argv**. Это
противоречит действующему принципу, что безопасность не должна зависеть от
бесконечно растущего чёрного списка исполняемых файлов (`AGENTS.md` §10 и
инварианты execution authorization), и это известный остаток класса F4: wrapper,
которого ещё никто не внёс в список, не ограничен.

**Статус: PARTIAL.** Инвариант, действующий сегодня, — «распознанный wrapper или
флаг eval/module интерпретатора требует объявленной capability», а не «неизвестное
поведение выполнения отклоняется fail-closed». Зафиксированное направление
исправления — модель declared-invocation / fail-closed-`OPAQUE` в `DECISIONS.md`.

### 4.3 Окружение интента не включает окружение профиля

`IntentBuilder.from_request()` задаёт `environment_variables` интента только из
**запроса**. Адаптер позже накладывает окружение **профиля** при старте процесса
(`_build_scoped_environment`). Следствие: изменение
`ProjectExecutionProfile.environment_variables` не меняет fingerprint интента.
Наблюдено пробой: окружение интента = `{'REQUEST_MARKER': ...}`, маркер профиля
отсутствует.

**Статус: PARTIAL (кандидат на усиление).**

### 4.4 Есть server-side файловая граница, но нет OS-level песочницы

Здесь нельзя смешивать две разные вещи.

**Реализовано (PARTIAL): server-side файловая/workspace-граница.**
`app/execution/sandbox.py::WorkspaceBoundary` — единственный канонический ответ на
вопрос «внутри ли этот путь workspace'а run?». Он каноникализирует корень
workspace через `os.path.realpath` (это раскрывает junction'ы и substituted
drives, а не только symlink'и), нормализует запрошенный путь, затем каноникализирует
результат и доказывает вложенность по каноническим путям. Он отвергает:

- обход вверх (`../`, `sub/../../x`), включая обход, возвращающийся внутрь
  workspace;
- абсолютные пути вне workspace и Windows drive paths;
- UNC и device paths, пустые пути и пути с NUL;
- directory junction или symlink, имя которого внутри workspace, но который
  разрешается наружу.

Проверка применяется дважды: на границе authority в
`RunScope.validate_execution_request` (рабочая директория и artifact targets) и
повторно внутри `LocalExecutionAdapter._run_process` непосредственно перед созданием
дочернего процесса, который затем получает канонический путь. Граница всегда
строится через `WorkspaceBoundary.for_workspace(...)` от доверенного корня и
никогда — от самой проверяемой директории. Корень workspace всегда берётся из
frozen scope и никогда — из задачи, запроса или metadata.

**Не реализовано: OS/kernel-level изоляция.** `LocalExecutionAdapter` запускает
локальные процессы с `shell=False`, временной копией workspace, ограниченным белым
списком окружения, таймаутами с завершением дерева процессов, лимитами вывода и
маскированием секретов. Он **не** предоставляет namespaces, cgroups, Job Objects
или файловое ограничение на уровне ядра. Дочерний процесс наследует security token
родителя и может открыть любой доступный этому токену путь; ребёнок, открывающий
абсолютный путь вне workspace, не останавливается. Этот остаточный риск записан в
`RESIDUAL_RISK` и доказан тестом
`tests/test_execution_sandbox_boundary.py::test_child_process_can_still_read_outside_the_workspace`.

**Block 2 разделён на фактические части.** Файловая/workspace-граница —
**PARTIAL/IMPLEMENTED**, как выше. OS-level песочница и изоляция остаются
**DEFERRED**. Подготовка/mirroring входов workspace остаётся **PARTIAL** (см. §4.5).
Прежнее утверждение, что Block 2 «НЕ реализован», было устаревшим и удалено.

### 4.5 Сетевая authority — deny-by-default; сеть не изолирована

Сетевая authority — server-side и fail-closed.
`ProjectExecutionProfile.network_access` по умолчанию `False`, значение
замораживается в `RunScope`, а `RunScope.validate_execution_profile` отклоняет любой
профиль запроса, пытающийся его включить (`run scope cannot enable network
access`). Задача, решение, план, tool intent, `HarnessRequest` или metadata не могут
его включить, а `ToolIntent` сразу отвергает `network`/`network_access` как
authority-shaped ключи аргументов. Resume обязан воспроизвести записанную операцию
под текущим authority ceiling
(`ResumeContract.validate_against_current_authority`), поэтому более широкая текущая
authority не может быть получена из persisted-состояния.

**Механизм enforcement.** Когда сетевой доступ запрещён, `LocalExecutionAdapter`
направляет `http_proxy`/`https_proxy`/`all_proxy` (и варианты в верхнем регистре) на
закрытый порт и очищает `NO_PROXY`. Это останавливает HTTP-клиенты, уважающие прокси,
и не делает ничего процессу, открывающему сокет самостоятельно.

**Не реализовано: kernel network isolation.** Нет network namespace, нет правила
firewall, нет контейнера. Создание сокета напрямую, разрешение DNS, сокеты IPv4 и
IPv6 и прямые исходящие соединения по-прежнему работают; это доказано тестом
`tests/test_execution_sandbox_boundary.py::test_proxy_denial_is_not_a_kernel_sandbox`.
Запрет — это **policy boundary, а не kernel sandbox**, и модуль с документацией
говорят это явно. Поэтому GAP-B — PARTIAL.

### 4.6 Подготовка основана на зеркалировании, а не на объявленных входах

`EphemeralWorkspaceManager` копирует переданный корень workspace (пропуская `.git`
и `__pycache__`) во временный scratch-каталог. Нет белого списка объявленных
входов, нет привязки идентичности workspace и нет digest содержимого для
referenced inputs. Что вызывающий передал как `workspace_root`, то и копируется.

**Статус: PARTIAL** (идентичность workspace, объявленные входы, deny-by-default
подготовка относятся к Block 2 / отложены).

### 4.7 У execution plane нет production-точки входа

`app/runtime/bootstrap.py::create_runtime` собирает настройки, провайдеров,
реестры, Orchestrator и `RunExecutor`. Он **не** создаёт `ExecutionCoordinator`,
адаптер или корень workspace. Координатор создаётся вызывающими:
`app/agent_runtime/harness.py` и `app/orchestrator/engineering.py`, оба — из полей
запроса, переданных вызывающим (`HarnessRequest.workspace`,
`EngineeringRunRequest.workspace`). Когда workspace отсутствует, локальный запуск
отклоняется с `workspace_root_required`.

**Частично устарело.** Это по-прежнему верно для самого `create_runtime`, но
production-цикл запуска теперь существует и достижим из доверенного server-side
кода: `ForgeApiService.run_agent_loop`, `run_accepted_task` и
`run_declared_verification` — это production-точки входа, а
`ForgeApiService.run_task` остаётся HTTP/desktop-путём совместимости. Латентным
остаётся путь *resume*, а не путь исполнения — см. §4.8.

### 4.8 Production idempotency существует; драйвера resume нет

Run-level идемпотентность реализована, закоммичена (`0244157`, расширена `5ec648f`)
и покрыта тестами. Доверенный оператор может передать `idempotency_key` в одну из
**трёх доверенных in-process точек входа**:

| точка входа | класс операции |
| --- | --- |
| `ForgeApiService.run_agent_loop` | `AGENT_LOOP` |
| `ForgeApiService.run_accepted_task` | `ACCEPTED_TASK` |
| `ForgeApiService.run_declared_verification` | `DECLARED_VERIFICATION` |

Без ключа каждый вызов сохраняет прежнюю семантику ровно.

Что реализовано:

- **durable журнал идемпотентности**, по одному файлу на ключ
  (`app/agent_runtime/idempotency.py`), отдельный от `RunStore`, который остаётся
  observation sink и не является resume-движком;
- **атомарный claim** через create-if-absent `O_EXCL`, поэтому две одновременные
  доставки одного ключа не могут обе победить;
- три исхода — **claim** (старт), **replay** (терминальная запись отдаётся вместо
  повторного исполнения) и **conflict** (ключ привязан к другой идентичности
  операции);
- привязка идентичности по классу операции, каноническому ключу, fingerprint'у
  доверенной задачи и каждому fingerprint'у criterion;
- **защита терминальных состояний**, обеспеченная кодом через
  `validate_lifecycle_transition`: `COMPLETED`, `FAILED`, `LIMIT_REACHED`,
  `DENIED` и `SECURITY_FAILURE` нельзя переоткрыть, отклонённая запись оставляет
  durable-запись нетронутой, а недоказуемый side effect нельзя перевести в
  recoverable-состояние;
- **перепроверка текущей authority**, поэтому resume обязан воспроизвести
  записанную операцию под текущим authority ceiling;
- claim берётся **до** freeze scope, привязки `RunStore` и durable-события
  `RUN_STARTED`, поэтому дубликат не пишет событие старта run, не фризит scope и не
  запускает run.

**Ключ идемпотентности не является authority.** Он не может нести команду, workspace,
профиль, набор tools, сетевой доступ или одобрение; он лишь выбирает, к какой
durable-записи операции относится доставка.

`run_task`, HTTP-путь task run и desktop dispatch **вне** этой защиты: их `task_id`
поставляет вызывающий, поэтому доверенной идентичности задачи для привязки ключа
нет. Это намеренно **не** «все точки входа защищены».

Resume — **только контракт**. `ResumeContract` и durable state machine жизненного
цикла реализованы и протестированы, включая линию преемственности через
`AttemptIdentity`. **Драйвера operator resume нет**: `AgentHarness.run()` всегда
стартует с `HarnessPhase.OBSERVE` и не имеет параметра старта с чекпоинта, поэтому
автоматический crash-resume повторно прогнал бы весь цикл, а не продолжил
безопасно. **Ничто в текущей реализации не заявляет работающий crash-resume.**

**`SideEffectLedger` НЕ ПОДКЛЮЧЁН к production.** Двухфазная запись side effects
(`NOT_STARTED`/`STARTED`/`COMMITTED`/`FAILED` плюс производный
`UNKNOWN_AFTER_CRASH`) реализована и протестирована как компонент, но у
`begin_side_effect` и `complete_side_effect` нет вызывающих в `app/` вне модуля,
поэтому `UNKNOWN_AFTER_CRASH` недостижим из текущего production-пути run. GAP-I и
GAP-R — PARTIAL.

## 5. Планируемое, отложенное и отклонённое

| Пункт | Статус | Где зафиксировано |
| --- | --- | --- |
| Discovery intelligence, свидетельства, readiness gates | **PLANNED / FUTURE** | `TECHNICAL_SPECIFICATION.md` §21 |
| Классификация проектов, выбор capabilities | **PLANNED / FUTURE** | `TECHNICAL_SPECIFICATION.md` §22 |
| Долговременные запуски, возобновляемое выполнение | **PLANNED / FUTURE** | [`STAGE_1_5_EXTERNAL_BENCHMARK.md`](STAGE_1_5_EXTERNAL_BENCHMARK.md) |
| Governance Agent/Skill/Tool/Capability сверх текущей skill-системы | **PLANNED / FUTURE** | [`STAGE_1_6_AGENT_SKILL_SYSTEM.md`](STAGE_1_6_AGENT_SKILL_SYSTEM.md) |
| Доменные паттерны и интеграции | **CANDIDATE** | [`STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md`](STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md) |
| Модель declared-invocation / fail-closed capability | **PLANNED (кандидат для Block 1.1)** | `DECISIONS.md`, Execution Authorization Contract v0.2 |
| Файловая/workspace-граница (Block 2) | **PARTIAL / IMPLEMENTED** — см. §4.4 | `DECISIONS.md` (Execution Filesystem Boundary and Network Policy v0.1), `ROADMAP.md` GAP-A |
| OS-level песочница и kernel-изоляция (Block 2) | **DEFERRED** | `DECISIONS.md`, `ROADMAP.md` |
| Объявленные входы workspace / deny-by-default подготовка (Block 2) | **DEFERRED** | `DECISIONS.md`, `ROADMAP.md` |
| Драйвер operator resume для прерванных запусков | **PLANNED / FOLLOW-UP** — контракт есть, драйвера нет (§4.8) | `ROADMAP.md` GAP-R, `DECISIONS.md` |
| Подключение журнала side effects к production-исполнению | **PLANNED / FOLLOW-UP** — компонент есть, не подключён (§4.8) | `ROADMAP.md`, `DECISIONS.md` |
| Server-derived идентичность операции для `run_task` / HTTP / desktop идемпотентности | **PLANNED / FOLLOW-UP** | `ROADMAP.md` GAP-I |
| Compare-and-set, lease или distributed lock для межпроцессных claim'ов | **DEFERRED** — задокументированное ограничение, намеренно не имитируется | `ROADMAP.md`, `DECISIONS.md` |
| Глобальное подавление на уровне действия между ключами идемпотентности | **DEFERRED** — сегодня защита привязана к ключу | `ROADMAP.md`, `DECISIONS.md` |
| Долговременное (durable) хранилище одобрений с истечением срока | **DEFERRED (отдельный дизайн)** | реестр отклонённых альтернатив в `DECISIONS.md` |
| MCP как модель разрешений | **REJECTED** | `DECISIONS.md` |
| Универсальные retries / rollback / параллелизм / swarm-исполнение | **REJECTED as defaults** | `AGENTS.md`, `TECHNICAL_SPECIFICATION.md` §18 |
| Digest всего содержимого workspace, криптографические токены одобрения, реестры полей | **REJECTED for the current block** | реестр отклонённых альтернатив в `DECISIONS.md` |
| Технологические кандидаты (NaraRouter, NVIDIA, QwenCloud, OmniRoute, Headroom, Task Observer, Claude-Mem, Everything Claude Code, Matt Pocock Skills, Ghidra, DBeaver, MarkItDown, MCP, Runnable, Kimi Work, Canva connector) | **CANDIDATE / WATCH** | [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md) |

## 6. Как поддерживать актуальность этого файла

1. Меняйте код, затем меняйте этот файл — никогда наоборот.
2. Никогда не переносите пункт из PLANNED в CURRENT без проходящего теста, который
   его проверяет, и без коммита, который его содержит.
3. Когда появляется новое поле, граница или подсистема, влияющая на выполнение,
   добавьте строку здесь и зафиксируйте решение в `DECISIONS.md`.
4. Когда ограничение из §4 исправлено, удалите это ограничение и отметьте
   исправление в `DECISIONS.md`; не оставляйте устаревший текст о «известном
   ограничении».
