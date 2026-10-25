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
> **Verified baseline.** Commit `d1c007a` plus the uncommitted working tree at the
> time of writing. Every number below was measured on that tree, in a run that did
> not overlap another test process; the tree has uncommitted work, so treat these as
> **dated observations** and re-measure rather than trusting them.
>
> - one process, without a live PostgreSQL
>   (`python -X utf8 -m unittest discover -s tests -t tests`):
>   **2206 test methods collected, `Ran 1971 tests ... OK (skipped=36)`** — 1967
>   passed, 0 failed, 0 errors, 36 skips *reported by the runner*, and **235 test
>   methods were collected but never reported by the run at all**. Of the 36 reported
>   skips, 32 are class-level `setUpClass` entries that the runner counts in its total
>   but never executes or names; only **4** are individual test methods that were
>   actually reached and skipped. The 235 silent methods are the database-backed tests
>   whose class fixture raises `unittest.SkipTest`; see §4.16, which reconciles this
>   against the earlier `112 test files, 2110 tests` figure and explains exactly why a
>   collected method can vanish from the summary.
> - 116 test modules in `tests/` match the discovery pattern `test*.py`; 4 more `.py`
>   files there (`offline_runtime.py`, `persistence_fixture.py`, `postgres_fixture.py`,
>   `run_scope_support.py`) are helpers and are not collected. `git ls-tree -r HEAD
>   tests` lists 109 files; the difference is 11 untracked new test/helper files.
> - the earlier `112 test files, 2110 tests, OK (skipped=3)` figure **is not
>   reproducible on this tree**; §4.16 accounts for the gap rather than restating it.
>
> The two files this block used to list as failing are **fixed** and pass; the
> routing defect behind them is recorded in §4.12. Do not re-add them here on the
> strength of an older report.

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
| Secret files withheld from the staged execution workspace, and never harvested as an artifact, by the shared recognizer the project scanner also uses | **CURRENT / VERIFIED** | `app/execution/secret_files.py`, `app/execution/workspace_manager.py` — `tests/test_execution_secret_staging.py` |
| Wall-clock timeout enforced by process wait, not by pipe EOF; process tree terminated on timeout **and** after a normal exit | **CURRENT / VERIFIED** | `app/execution/adapter.py` — `tests/test_execution_timeout_and_tree_kill.py` |
| Frozen `workspace_root` checked unconditionally, independent of adapter isolation | **CURRENT / VERIFIED** | `app/execution/authorizer.py` — `tests/test_workspace_root_always_checked.py` |
| Profile environment values redacted on every prompt-bound serialization | **CURRENT / VERIFIED** | `app/execution/profile.py` (`to_context_dict`) — `tests/test_profile_prompt_redaction.py` |
| Core API exposure configuration: explicit CORS allow-list, no credentialed CORS, loopback-bound launcher that refuses non-loopback exposure without authentication | **CURRENT / VERIFIED** (no authentication) | `app/api/server.py`, `app/api/main.py` — `tests/test_api_exposure.py` |
| Frozen workspace identity binding | **CURRENT / VERIFIED** | `app/execution/identity.py`, `app/runtime/run_scope.py` — `tests/test_run_scope_trust_boundary.py` |
| Bounded discovery includes a deterministic directory-depth limit | **CURRENT / VERIFIED** | `app/understanding/models.py`, `app/understanding/scanner.py` — `tests/test_understanding_security.py` |
| Program path is pinned in the immutable execution intent | **CURRENT / VERIFIED** (small resolution-to-spawn TOCTOU window remains) | `app/execution/intent.py`, `app/execution/adapter.py` — `tests/test_execution_authorization_hardening_v03.py` |
| Run-level production idempotency (durable ledger, atomic claim, terminal-state protection) | **PARTIAL** — see §4.8 | `app/agent_runtime/idempotency.py`, `app/agent_runtime/idempotency_integration.py`, `app/api/service.py` — `tests/test_idempotency_production.py` |
| First end-to-end vertical workflow (`goal -> specification -> plan -> approval -> execution -> verification -> result`) | **CURRENT / VERIFIED**, with the scope limits in §3.5 | `app/workflows/project_workflow.py` — `tests/test_project_workflow_production.py`, `tests/test_project_factory_end_to_end.py` |
| Controlled Project Factory slice: separated `generation_status` / `execution_status` / `harness_status` / `verification_exit_code` / `acceptance_status`, a per-invocation generation budget, and an explicit not-established state | **CURRENT / VERIFIED**; the four statuses are distinct fields with closed vocabularies — see §3.5.1 | `app/workflows/project_workflow.py` — `tests/test_project_factory_vertical_slice.py` (42 tests) |
| Model-driven project generation: a model response becomes checked project files, which the declared verification command then really tests | **CURRENT / VERIFIED as a path**; a real provider call is **NOT VERIFIED** — see §3.5.2 | `app/workflows/project_generation.py`, `app/workflows/project_workflow.py` — `tests/test_project_generation_production.py`; opt-in real-provider test `tests/test_project_generation_real_provider.py` (**never executed**) |

### 3.5 First end-to-end vertical workflow

`app/workflows/project_workflow.py` composes components that already exist into
one vertical slice, and adds no orchestrator, no memory system, no authorization
mechanism, and no parallel execution path. Its only entry point is
`run_project_workflow(service, registry, build_declaration_id, *, idempotency_key=None)`;
like the sibling entry points it takes an **operator-owned declaration id** and
accepts no command, path, allowlist, criterion, or expectation from a caller.

Each step is performed by an existing component:

| Step | Performed by |
| --- | --- |
| User goal -> task specification + acceptance criteria | `AcceptanceSpec`, `TaskSpecification` |
| Plan | `app.planning.planner.Planner` (the same deterministic planner the harness uses) |
| Approval | `ApprovalPolicy` + `ApprovalResolver` inside `ExecutionCoordinator` |
| Execution | `ForgeApiService.run_accepted_task` |
| Verification (a second declared command) | `ForgeApiService.run_declared_verification` |
| Acceptance verdict | `AcceptanceGate`, through the existing harness |
| Result | `ProjectWorkflowResult` |

The specification view (`build_specification`) is readable **before** anything
runs and performs no execution and no write. The response type
`ProjectWorkflowResult` carries `run_id`, `state`, `acceptance_status`, the
verification outcome, measured `created_files` / `deleted_files`, `error`, and
the durable run record. It is a **new** type: no field of `TaskRunResponse` is
redefined and no field is added to it.

Two separate verdicts are reported, and neither is presented as the other:

- `acceptance_status` is the existing `AcceptanceGate` verdict over the
  operator's frozen workspace facts. On the success path the run's state is
  `LIMIT_REACHED` (the acceptance slice is bounded to two actions — one
  execution plus its verification — while `COMPLETE_RUN` would need a third),
  which is a pre-existing property of that slice, not of this workflow;
- `verification` is the result of really executing the generated project's own
  test suite as a second declared command. Its `observed_exit_code` is the real
  process exit code the local adapter returned; `success` is `False` unless that
  code equals the declaration's declared `expected_exit_code`.

**Honest limit — the code in this slice is not model-generated.** The project is
written by an operator-declared `python` command, not by an LLM. No provider, no
API key, and no model is involved. This slice therefore proves the *workflow and
its verification machinery*; it does **not** prove anything about the quality of
model-generated code. Measured properties and the remaining limits:

- `created_files` / `deleted_files` are a measured diff of a workspace snapshot
  taken before the build against one taken after it, excluding `__pycache__`
  directories (the interpreter writes those as an import side effect);
- a required approval that is not granted leaves the run in
  `WAITING_FOR_APPROVAL` with `success=False`, `acceptance_status` =
  `not_evaluated`, and **no files created**. On this path a missing
  `approval_policy` behaves differently from `run_task` /
  `run_declared_verification`, where a missing policy is itself reported as
  `approval_policy_required`;
- the workspace constraint is enforced for **authority** (a working directory
  that resolves outside the frozen root is denied by `RunScope` and no process
  is spawned). It is **not** an OS sandbox: a declared command can still write
  outside the workspace, and §4.4 records that residual risk. The workflow does
  not hide that — such a run is reported as a failure because the required
  workspace facts are absent;
- the workflow refuses, before freezing any scope, a registry binding whose
  build and verification declarations do not share the operator's profile,
  working directory, and executable. This is a composition-consistency check, not
  a new authorization mechanism;
- the response's `state` is `VERIFIED` only when the action succeeded **and** the
  declared verification matched its declared exit code, and **every** file the
  source named was written. A failing action, a failing verification, and a
  partial materialisation are never reported as success.

### 3.5.1 Separated statuses and the generation budget

`ProjectWorkflowResult` carries four independent stage statuses plus the raw exit
code. Each has its own closed vocabulary, because collapsing any two of them is
how "a model answered", "a process exited 0", and "the operator's criteria held"
get mistaken for one another. Every value is derived from an observed fact and
none is a summary string; `"unknown"` is a real value meaning *the fact could not
be established*, which is neither failure nor success.

| Field | Values | Condition |
| --- | --- | --- |
| `generation_status` | `disabled`, `refused_limit`, `failed_transport`, `failed_response`, `failed_materialisation`, `succeeded`, `unknown` | `disabled` when no provider was composed; `refused_limit` when the budget was already exhausted so the provider was never asked; `failed_transport` when the dispatch returned no response at all (the provider raised, or no executor was composed); `failed_response` when a response arrived and could not become a project; `failed_materialisation` when nothing could be written; `succeeded` when a project was written |
| `execution_status` | `not_dispatched`, `waiting_for_approval`, `failed`, `succeeded`, `unknown` | Derived from the executed action, never from the harness lifecycle. `waiting_for_approval` when the authorizer refused to perform it; `succeeded` when the run's `success` is true (the adapter observed `EXECUTION_SUCCESS` **and** the acceptance gate returned PASS); `failed` for any other terminal state |
| `harness_status` | the harness's own status, verbatim (`COMPLETED`, `LIMIT_REACHED`, `FAILED`, `WAITING_FOR_APPROVAL`), or `""` | The build run's lifecycle status as reported. Kept separate from `execution_status` on purpose — see the note below |
| `verification_exit_code` | an `int`, or `None` | The declared verification command's **real** process exit code, or `None` when no process result was observed. `None` is not `0` |
| `acceptance_status` | `pass`, `fail`, `not_evaluated` | The existing `AcceptanceGate` verdict. `not_evaluated` means no gate ran, and it is never reported as a pass |

Two further fields keep honest unknowns and provenance explicit:

- `generation_attempts_spent` is how many provider attempts **this workflow
  initiated**, or `None` when no generation stage ran (the question does not apply,
  which is not the same answer as `0`). It is correlated with
  `max_generation_attempts`; `0` means the budget was already exhausted so the
  provider was deliberately not asked. It is *not* a raw provider-call count: the
  decision layer exposes no call record, so a raw count is not observable from
  here, and a number this module cannot measure is not reported at all. The
  physical call count is measured in the tests, from the transport the test
  composes;
- `project_source` is `declared_command`, `injected_transport`, or
  `model_transport`. It names how the project **text** arrived. It is not proof
  that a remote model answered; nothing in this module can establish that.

**`LIMIT_REACHED` is reported as itself, and it does not mean the action failed.**
Measured on the ordinary, fully successful declared run: the acceptance harness
ends in `LIMIT_REACHED`, while the action it dispatched really exited 0, the
project's own tests really passed, and the acceptance gate really returned PASS.
The harness's own physical-telemetry code classifies `LIMIT_REACHED` as a terminal
*success* status alongside `COMPLETED`, so this is the harness's intended
behaviour, not a regression. The slice therefore reports `harness_status` verbatim
and derives `execution_status` from the action, so neither fact can be presented as
the other. `execution_status` is never `limit_reached`.

**The generation budget is checked before the call.** `max_generation_attempts`
defaults to `1`; a value below `1` refuses without asking the provider (so
`generation_attempts_spent` is `0` and no call is made), and the refusal is **not**
routed around by falling back to the declared creation command. A budget that is not
a non-negative integer (`-1`, `True`, `"1"`, `1.5`, `None`) raises
`ProjectWorkflowError` rather than being coerced. Usage and cost are never fabricated: when the transport reports no usage,
`GenerationEvidence.usage_present` is `False` and the token/cost fields are
`None`, which is reported as "not reported", not as zero.

**A verified verdict requires a complete materialisation.** When a response
declares a file that a path, protected-area, secret, size, or conflict check
refuses, the surviving files may still be written and their tests may still pass —
but the artefact is not the one the response described. Such a run reports
`state`/`verdict` `MATERIALISATION_INCOMPLETE`, `success=False`, and
`error=materialisation_incomplete`, instead of either hiding the refusal or
discarding the passing run. A refused file is never counted as created.

**Honest limitations of this slice.** Nothing here is a new guarantee:

- **there is no OS sandbox.** The checks are a server-side filesystem boundary over
  text and resolved paths. A program that a response later writes is not prevented
  by them from opening an absolute path at run time; §4.4 records that residual
  risk and this slice does not reduce it. The one thing it *does* add is that the
  workspace walk no longer descends through an NTFS **junction**
  (`DirEntry.is_symlink()` is `False` for a junction on Windows), so a link inside
  the workspace can no longer put files that are not in the workspace into the
  created-file diff;
- **no real provider is verified.** Every generation transport exercised in the
  tests is an in-process fake, and `project_source` / `GenerationEvidence`
  `mock_transport` say so. The verdict `REAL LLM PROJECT GENERATION VERIFIED`
  remains unreached;
- **the dashboard provider-health probes are unchanged.** They still make outbound
  attempts in a normal run (see §4.19). The correct claim is therefore "a
  test-mode runtime makes no provider *decision* call", never "the suite opens no
  socket";
- the generation budget bounds workflow-initiated attempts, not the individual
  provider calls a transport may make internally (a fallback chain is the
  dispatcher's own behaviour and is reported from its own event record);
- `acceptance_status == "pass"` is evidence about the operator's declared
  workspace facts only. It is not evidence that the project's tests passed, and it
  is not evidence that the project is correct.

### 3.5.2 Model-driven project generation (generation path implemented; real provider not verified)

`app/workflows/project_generation.py` turns a **model response** into project files
on disk, and the model path of `run_project_workflow` wires it into the slice above.
It adds no orchestrator, no dispatcher, no memory system, no authorization
mechanism, and no parallel execution path. It validates text and writes files, and
it spawns no process of its own.

The entry point is the same function, with operator-composed additions::

    run_project_workflow(
        service, registry, build_declaration_id,
        ai_provider=AIDecisionProvider(provider, mock_transport=...),
        materialise=True,                        # default
        permitted_overwrite_paths=(),            # default: no overwrite at all
        max_file_bytes=None, max_project_bytes=None, max_file_count=None,
    )

With `ai_provider=None` (the default) nothing in §3.5 changes. With a provider
composed, the model's files **are** the project and the declared creation command is
**not** dispatched — it would overwrite them — while the declared *verification*
command still runs unchanged, so the generated project's own tests are really
executed.

Each step is performed by an existing component:

| Step | Performed by |
| --- | --- |
| Model call | `RunExecutor` -> `Orchestrator.dispatch` -> `Dispatcher` -> `ProviderAgent.run` -> `Provider.generate`; the text is read from `TaskResult.output` |
| Provider identity and usage | `TaskResult.provider` / `model_name` / `usage`, and `AIDecisionProvider.last_provider_name` / `last_model_name` / `last_usage` (the **attempt**), plus `producer_provider_name` / `producer_model_name` / `producer_usage` (the **producer**: empty/`None` unless the reply produced a validated decision) |
| Decision validation | `AIDecisionProvider.decide_from_output` — the existing fail-closed ladder, unchanged |
| Schema, path, protected-area, conflict, secret and size checks | `app/workflows/project_generation.py` |
| Secret recognition | `app.execution.secret_files.is_secret_file` / `is_secret_directory` — the shared recognizer, not a second filter |
| Durable generation history | the service's own `RunStore`, under run id `run-generate-<declaration id>` |
| Verification (second declared command) | `ForgeApiService.run_declared_verification` |
| Generated project's tests | the declared verification command, e.g. `python -m unittest discover -s tests -t . -p "test_*.py"` |

**The model output schema is explicit and strict.** One JSON object::

    {"decision": {"decision_type": "...", "action": "...", "reason_code": "...",
                  "rationale": "...", "confidence": 1.0},
     "files": {"relative/posix/path": "entire file body"}}

Every file body must be text and UTF-8 encodable. Before any byte is written, every
path is refused if it is absolute, carries a drive or UNC prefix, contains a `.` or
`..` segment, uses a backslash, contains a control character, names a Windows device,
enters `.git` / `.forge` / `.env`, names a credential file or directory, resolves
outside the workspace root, collides with an existing file the operator did not
explicitly permit overwriting, or exceeds the size limits.

**Provider configuration required.** The transport is composed by the caller and
passed in as `AIDecisionProvider(provider, mock_transport=...)`. A real adapter comes
from `app/agents/providers/factory.py` with a `ProviderConfig` naming the provider,
the model id, and the environment variable holding the key — for example
`ProviderConfig(provider_name="openrouter", model_name="<model id>", enabled=True,
api_key_env_var="OPENROUTER_API_KEY")`. The flag `mock_transport` is recorded in the
result so that injected text can never be read as a real model's work.

**How to run it.** The scenario is exercised by its focused test module::

    python -X utf8 -m unittest discover -s tests -p "test_project_generation_production.py"

One method::

    python -X utf8 -m unittest discover -s tests -p "test_project_generation_production.py" -k <substring>

**How to run the generated project.** The materialised files are written under the
workspace root the service was composed with (`service.workspace.root`); the file list
is `ProjectWorkflowResult.materialisation.file_list`. Run its tests exactly as the
declared verification command does::

    python -m unittest discover -s tests -t . -p "test_*.py"

**Where results are stored.** In memory, on the returned
`ProjectWorkflowResult`: `generation` (provider, model, usage, decision,
`mock_transport`, `response_text_observed`), `parsed_project` (accepted files and
per-file rejections), `materialisation` (what was created, overwritten, refused),
`verification` (the real exit code and diagnostics), `acceptance_status`, and one
top-level `verdict`. Durably, the generation dispatch writes its history through the
service's own `RunStore` under run id `run-generate-<declaration id>`, where
`provider_attempt` and `provider_result` carry the provider, the model, and the
usage. The raw model text is deliberately **not** persisted or projected:
`to_dict()` reports `raw_model_text_included: false`.

The top-level `verdict` is one of `real_provider_not_verified`, `generation_failed`,
`generation_refused`, `materialisation_failed`, `materialisation_incomplete`,
`build_failed`, `tests_failed`, `acceptance_failed`, `approval_required`,
`project_verified`, or `declared_command_verified`. It is derived from the observed
facts in that order, so a later stage's success can never overwrite an earlier
failure. The two `*_verified` values are reached only on a run that really
succeeded — `declared_command_verified` on the declared-command path (the project's
own tests passed and the acceptance gate returned PASS) and `project_verified` on
the generation path, and neither claims a remote model answered:
`real_provider_not_verified` remains the verdict for a *failure* on this path, which
is what it honestly says. **`verdict` never becomes
`REAL LLM PROJECT GENERATION VERIFIED`**, which is the value
`verdict_expected_with_real_provider` names as the one a real provider call would
have to earn.

**What is verified automatically, and what is not.**

Verified automatically, by `tests/test_project_generation_production.py` with an
injected in-process transport:

- the model is asked through the existing provider path (asserted on the injected
  provider's own call record), and provider identity and usage reach both the result
  and the durable run history;
- the model's response **content** determines the file contents on disk;
- empty output, malformed JSON, a non-object body, a missing or non-object `files`
  member, an empty file set, non-text content, and oversized bodies each fail closed
  with their own reason code and create nothing;
- an empty response reports **no** producer identity, model, usage, or raw text, and
  `response_text_observed: false`; malformed text reports
  `response_text_observed: true` with the same empty identity;
- the **attempt** fields (`last_provider_name` / `last_model_name` / `last_usage`) and
  the **producer** view (`producer_provider_name` / `producer_model_name` /
  `producer_usage`) are asserted to be different things: an empty or malformed reply
  names no producer and reports no usage, while a reply that produced a validated
  decision reports both. Pinned by `tests/test_generation_evidence_contract.py`, whose
  positive control would fail if the view were simply never populated;
- absolute paths, `..` traversal, escaping resolutions, `.git` / `.forge` / `.env`,
  and credential paths are refused, and refused files are never created;
- an existing user file is not overwritten unless the operator names it in
  `permitted_overwrite_paths`, and is written when they do;
- every created file resolves inside the permitted workspace root;
- the generated project's own tests really execute and their real exit code is
  reported; a failing generated test yields a non-zero code and `tests_failed`, never
  "verified";
- an injected transport is marked in data (`mock_transport: true`) and the honest
  limit travels in the result's `messages`;
- a required-but-unresolved approval still performs nothing, and a rejected approval
  executes nothing.

**Not verified — stated plainly.** **No real provider call has been made.** No model,
no provider adapter, and no API key are involved in any passing test in this
repository: every test uses an injected in-process transport that returns a fixed
string. That proves the generation *path*, the schema validation, the materialisation
checks, and the verification machinery. It proves **nothing** about the quality of
code a real model would produce, and nothing about whether a real adapter's network
path works. The one test that would make a real call —
`tests/test_project_generation_real_provider.py` — is skipped unless both
`FORGE_ALLOW_REAL_PROVIDER_GENERATION=1` and the provider's key variable are set, and
**it was never executed**.

Further limits of this slice:

- the path and content checks are a **server-side filesystem boundary over text**, not
  an OS sandbox; code a model later writes is not prevented by them from opening an
  absolute path at run time (§4.4);
- the content secret check is **not** a general secret classifier. It recognises a
  private-key header, a JWT, a provider-shaped key literal, a hard-coded assignment to
  a credential-shaped name, and a reference to a credential *file name* the shared
  recognizer knows. A secret written as ordinary prose is not recognised, exactly as a
  secret inside `notes.txt` is not recognised by the staging filter (§4.10);
- the checks inspect **text**, so they say nothing about what a generated project does
  when executed; the OS sandbox that would answer that does not exist here;
- a refusal is per file. A response with one refused file and several admissible ones
  is partly materialised, and every refusal is reported in
  `materialisation_rejections` rather than being silently dropped;
- **one generation attempt makes TWO provider calls with a real adapter, not one.**
  Call 1 is the generation request whose answer becomes the project. Call 2 happens
  inside `ForgeApiService.run_declared_verification`: that entry point calls
  `self._runtime.run_executor.execute(...)` with no `provider_name`, so the dispatcher
  selects the configured default provider and makes a call whose text is then
  **discarded**, while the declared host command still runs and supplies the exit code
  that is actually reported. This is **pre-existing** behaviour of that entry point,
  not something this slice added, and it is **billable** in production. The existing
  `tests/test_project_workflow_production.py` does not reveal it only because that
  module composes an `_OrchestratorDouble` instead of a real orchestrator. Removing the
  second call would require either not exercising the production verification path or
  changing `run_declared_verification`'s contract; neither was done, and the
  verification step was **not** weakened or removed to save it;
- the provider publishes two separate views deliberately, and a report must read the
  right one. `last_provider_name` / `last_model_name` / `last_usage` describe the
  **attempt** and are populated as soon as a response arrives, so reading them does not
  prove that a model answered. `producer_provider_name` / `producer_model_name` /
  `producer_usage`, gated on `last_response_produced_a_decision`, answer whether a
  usable reply actually produced a decision. `GenerationEvidence` reads the
  `TaskResult` the provider itself filled in first, because on this path the dispatch —
  not the validator — makes the call, so the validator has no attempt of its own to
  report. The validation report is likewise read only when a decision was produced: a
  fail-closed reply does not update it, and reading it unconditionally would return an
  earlier invocation's validation errors beside a decision that failed for an unrelated
  reason;
- the generation prompt's text is neutralised with respect to the markers the existing
  context selection layer refuses to carry (`token`, `secret`, `prompt`, ...), because
  a dropped Tier-1 context item aborts the Run. A goal or criterion containing one of
  those words therefore reaches the model reworded. That is a fidelity limit of this
  path, not a security property;
- a goal or criterion that a model disobeys is not detected: the slice checks that the
  declared verification command exits as declared, not that the generated project
  satisfies the operator's intent.

### 3.5.3 Managed multi-step execution (v0.1)

**Status: CURRENT / VERIFIED** for the mechanism described here, with the limits
listed below. `app/workflows/step_graph.py` is the contract and the validator;
`app/workflows/step_runner.py` is the driver.
`tests/test_step_graph_managed_execution.py` (31 tests, `Ran 31 tests ... OK`) is
the evidence.

| Area | Status | Evidence |
| --- | --- | --- |
| Step graph contract, deterministic order, validation before execution | **CURRENT / VERIFIED** | `app/workflows/step_graph.py` — `StepGraph.validate`, `topological_order`, `ready_steps` |
| Sequential driver through the existing chain | **CURRENT / VERIFIED** | `app/workflows/step_runner.py` — `StepGraphRunner.run` / `resume`; every step calls `RunExecutor.execute(..., generate_decision=False)` with the composition's own `ExecutionCoordinator` |
| Per-step durable progress and restart recovery | **CURRENT / VERIFIED** | `RunStore.write_state` under the graph run id, one bound `RunStore` per step run — `test_resume_continues_pending_steps_without_repeating_settled_ones` |
| Per-step claim and side-effect record | **CURRENT / VERIFIED** | existing `RunIdempotencyGuard` + `IdempotencyLedger.begin_side_effect` (`HOST_PROCESS`, not replay-safe) |
| Per-step verification, separate from execution | **CURRENT / VERIFIED** | `VerificationKind.EXIT_CODE` / `ARTIFACT_PRODUCED`; execution failure and verification failure are separate fields |
| Parallel step dispatch | **REJECTED / NOT IMPLEMENTED** | must not run concurrently unless proven safe |

**The contract.** A `StepGraph` is an ordered tuple of `StepDefinition` values. Each
carries a unique `step_id`, a `StepAction` naming an **operator-declared**
`declaration_id` plus parameters from a closed vocabulary, an explicit `depends_on`
tuple, and an optional `StepVerification`. Every declared step is mandatory: v0.1 has
no optional step that could be skipped while still allowing an overall `COMPLETED`.
The parameter vocabulary is exactly `working_directory` (a containment-checked
relative path) and `timeout_seconds` (**narrowing only** — a step may shorten the
declaration's or the profile's limit and never extend it, which `validate` refuses as
`parameter_widens_declared_limit`). There is no environment parameter, because the
execution policy already requires a request's environment to equal the profile's.

`StepGraph.validate(declarations=...)` returns every reason the graph must be refused,
in a fixed order, before any scope is frozen and before any claim is made: malformed
or duplicate `step_id`, an undeclared `declaration_id`, malformed / duplicate / self
/ unknown dependencies, a parameter outside the vocabulary or carrying an unsafe
value, a parameter that widens a declared limit, and every cycle (reported by its
strongly connected components, as `cycle:a->b`). `assert_valid` raises
`StepGraphValidationError` carrying those reason codes.

`StepStatus` is what is known about one step: `PENDING` (not started, no claim),
`RUNNING` (a claim exists and the step was dispatched — **never a final answer**),
`WAITING_APPROVAL` (the chain refused for unresolved approval; nothing was performed),
`SUCCEEDED` (the process ran, its real exit code was 0, and every declared check
passed), `FAILED` (the process failed, timed out or errored, **or** the process
succeeded but a declared check did not pass, **or** the chain refused it before
dispatch), `CANCELLED` (cancellation was requested before this step started; it was
never dispatched), `BLOCKED` (a dependency did not succeed), `UNKNOWN` (it cannot be
established whether the action completed).

`StepGraphStatus` is what is known about the whole attempt, with pessimistic
precedence — `INDETERMINATE` > `CANCELLED` > `FAILED` > `REFUSED` > `INCOMPLETE` >
`COMPLETED`. `COMPLETED` is produced only when every step is `SUCCEEDED` and every
declared verification passed; nothing else yields it.

**Per-step evidence** is kept as separate observations in `StepRecord`: the planned
action (`declaration_id`, canonical `parameters`, `action_fingerprint`), the operation
actually performed (`performed_executable`, `performed_working_directory`, `step_run_id`,
`request_id` — only the *executable* is stored, never the argv, mirroring the existing
execution-record discipline), the real result (`execution_exit_code`,
`execution_outcome`), the real verification (`verification_status` with `NOT_RUN`,
`PASSED`, `FAILED`, `ERROR`, plus what was expected and observed), the separate
`execution_error` and `verification_error` fields, and the `evidence` codes the status
rests on. A process that exited 0 while a declared check failed is recorded as
`execution_outcome=EXECUTION_SUCCESS` with `status=FAILED` and
`verification_status=FAILED`: the two facts stay readable and are never collapsed.

**Authority is unchanged.** A graph is a plan, not permission. `StepGraphRunner` takes
the `ExecutionCoordinator` from its composition and never constructs one; each step
freezes its own `RunScope` (the service's workspace, profile, tool allowlist and one
declaration's command authority), and the run goes through `RunExecutor.execute(...,
generate_decision=False)` exactly as `run_declared_verification` does. No model is
asked, so nothing a model says can influence a step. The runner adds no approval
mechanism: a step whose approval is required and unresolved performs nothing and is
reported `WAITING_APPROVAL`, with dependents `BLOCKED`. Approval binds to the execution
intent fingerprint, so changing any step parameter changes the fingerprint and the
already-granted approval is refused; two independent components compare it (the
resolver and the coordinator), and defeating either one alone still refuses.

**Cancellation** stops new steps only. A step that was already dispatched is allowed
to finish and is reported by what actually happened — with the extra evidence code
`cancel_requested_while_running` — and is never reported as `CANCELLED`. Steps that
had not started become `CANCELLED` with `cancelled_before_start`.

**Execution is strictly sequential.** One step is dispatched at a time and the next
begins only after the previous one has a recorded outcome. Parallel dispatch is not
attempted, because nothing in this architecture proves that two concurrent declared
executions inside one workspace are safe, and a plan must not be able to create that
concurrency.

**The repeat-execution guarantee, stated precisely.** The runner provides
**at-most-once dispatch per (graph run, step) claim, as observed through the existing
durable idempotency ledger, and only while that ledger is readable**. It is **not**
exactly-once. Before dispatching, the step's claim is read from the ledger: only
`START` — or `RESUME` for a step explicitly declared `repeatable` — permits dispatch.
Then the `RUNNING` transition is persisted and a `HOST_PROCESS` side-effect record is
written as started; only then does the process exist. A durable `RUNNING` with no
outcome is therefore read back as `UNKNOWN`, and a started-but-unsettled host-process
effect makes the ledger refuse a later automatic attempt with
`SIDE_EFFECT_UNKNOWN`. What is explicitly **not** guaranteed: no distributed
transaction and no cross-process lock, so two processes racing on a lost claim can
both be admitted; no atomicity of a host process's external effects; no automatic
retry of a `FAILED`, `WAITING_APPROVAL` or `UNKNOWN` step — a further attempt is a
**new graph run id**, which means a new claim, a new scope and a fresh approval check;
and no sandbox beyond the existing workspace boundary, environment allowlist and
secret filter.

Two further limits of v0.1: a graph run id is one attempt, and asking twice with the
same id executes nothing (`already_recorded`) while continuing an unfinished attempt
requires the explicit `resume`; and a `step_budget` may stop an attempt with steps
still `PENDING` (status `INCOMPLETE`), which is the state `resume` continues from. A
refused caller that races an attempt already in flight in the same process performs no
durable read at all, because on Windows a concurrent read of the state file can make
the writer's atomic `os.replace` fail — that is a limitation of the existing store, and
the runner avoids provoking it rather than working around it.

### 3.5.4 Reliable recovery, repeat control and Project Memory freshness (Block #7)

**Status: CURRENT / VERIFIED** for the mechanism described here, with the limits
listed below. `app/runtime/recovery.py` is the recovery planner and the durable claim;
`app/runtime/continuation.py` is the Project Factory continuation integration;
`app/understanding/memory.py` is the memory layer.
`tests/test_recovery_and_project_memory.py` (31 tests, `Ran 31 tests ... OK`) is the
evidence, and every security-relevant property below is proved fails-before /
passes-after by patching the source in place (8 injections, each restoring its file
byte-identically — SHA-256 before == after).

| Area | Status | Evidence |
| --- | --- | --- |
| Discovery of unfinished attempts | **CURRENT / VERIFIED** | `RecoveryPlanner.unfinished_runs` over `RunStore.list_run_ids()`; `test_recovery_of_a_multi_step_graph_decides_every_step` |
| Per-step recovery decision (continue / re-verify / needs the owner) | **CURRENT / VERIFIED** | `RecoveryPlanner.plan` → `RecoveryStepDecision`; `test_detection_of_an_unfinished_step` |
| The declared plan survives a restart | **CURRENT / VERIFIED** | `StepGraphSnapshot.declared_plan`, `graph_from_declared_plan`; `StepGraphRunner.resume(graph_run_id=...)` with no graph argument — `test_recovery_after_a_process_restart` |
| Durable, atomic, per-step recovery claim | **CURRENT / VERIFIED** | `RecoveryClaimLog.acquire` (`O_CREAT \| O_EXCL` + the `RunStore` entry); `test_concurrent_claimers_produce_exactly_one_claim` |
| Repeat semantics per action category | **CURRENT / VERIFIED** | `RunIdempotencyGuard.repeat_decision` → `RepeatGuarantee`; `test_refusal_to_auto_repeat_a_dangerous_operation` |
| Memory categories, provenance, trust | **CURRENT / VERIFIED** | `app/understanding/memory.py` — `MemoryItemKind`, `EvidencePin`, `_ALLOWED_TRUST`; `test_an_inference_is_never_promoted_to_a_confirmed_fact` |
| Freshness: changed / deleted / renamed sources | **CURRENT / VERIFIED** | `freshness_of`, `ProjectMemory.refresh`; `test_invalidation_of_stale_facts_after_a_file_changes`, `test_a_renamed_file_is_neither_still_current_nor_a_new_unrelated_file` |
| Contradiction detection | **CURRENT / VERIFIED** | `ProjectMemory.detect_contradictions`; `test_detection_of_contradictory_evidence` |
| Project Factory continuation | **CURRENT / VERIFIED** | `capture_context` / `recall_context` / `plan_continuation`; `test_recovery_of_project_factory_context` |
| Heartbeat, lease, owner identity | **REJECTED / NOT IMPLEMENTED** | not needed for recovery *safety*; the reasoning is recorded below |
| Exactly-once execution | **REJECTED / NOT CLAIMED** | impossible through a local filesystem ledger with no distributed transaction |
| Automatic retry of `FAILED` / `WAITING_APPROVAL` / `UNKNOWN` | **REJECTED / NOT IMPLEMENTED** | recovery never re-dispatches these |

**The recovery contract.** On start, `RecoveryPlanner.unfinished_runs()` reads every
record the existing `RunStore` holds and reports each attempt whose outcome is not
proven terminal-success. `RunStore.classify_record` is the classifier: a record that
merely *claims* progress is `INDETERMINATE`, never proof of life and never proof of
death. For each step, `plan()` returns a `RecoveryStepDecision` whose `decision` is one
of `CONTINUE` (safe to continue), `REVERIFY` (the work may have completed but its
declared check must be re-established), `NEEDS_OWNER` (no automatic action is safe),
`ALREADY_SETTLED`, `BLOCKED`, `REFUSED`, `UNKNOWN` — and whose `classification` is the
`ExecutionClassification` the evidence supports. Each decision carries the exact
`reason` code and, where a claim exists, the ledger's `RepeatDecision`.

A managed attempt is written with its `state`/`status` deliberately empty, so its
verdict is read from the step-graph document instead:
`classify_run_record(step_graph_status=...)` maps `COMPLETED` → `TERMINAL_SUCCESS`,
`FAILED` → `TERMINAL_FAILURE`, `REFUSED` → `TERMINAL_REFUSED`, and `INDETERMINATE`,
`INCOMPLETE` **and `CANCELLED`** → `INDETERMINATE`. `CANCELLED` is mapped to
`INDETERMINATE` on purpose: Block #6 writes that status both for cancellation before
start (nothing was dispatched) and for a dispatched step that never settled, and
nothing in the record proves which one happened.

**The declared plan is now persisted.** `StepGraphSnapshot.declared_plan` holds the
serialization of the graph that was declared when the attempt started, and
`graph_from_declared_plan` rebuilds it from the record alone, re-checking every saved
step's recorded `fingerprint` against the fingerprint recomputed from its own saved
fields. `StepGraphRunner.resume` therefore accepts `graph_run_id` **without** a graph:
the record is the source of the plan, and a caller who supplies a graph gets it
compared for equality with the recovered declaration. A record written before Block #7
carries no declaration and is refused with `StepGraphStateError` naming the missing
plan — never re-validated against whatever the caller happens to remember. A malformed
record is likewise refused, and `read_step_graph_record` distinguishes "nothing was
recorded" from "a record exists in a form this build refuses" without raising.

**Concurrent recovery.** A recovery claims a step through `RecoveryClaimLog.acquire`,
which is protected twice: an atomic exclusive file creation (`O_CREAT | O_EXCL`) inside
the run directory the `RunStore` already owns, and a re-check of the durable
`recovery_claims` entry in the same snapshot. Both are needed, because a claim file is
a per-directory artifact and two recoveries whose claim directories differ would not
see each other's files at all. A refusal from either removes any file the call created,
so a lost race never wedges a step. This is **not** a second storage backend: both
records live in the run directory that already exists, and the claim is **not**
authority — `RecoveryExecutor.continue_plan` still delegates every dispatch to
`StepGraphRunner.resume`, which re-validates the declared plan and re-authorizes each
step exactly as a first attempt does.

**Why no heartbeat, lease or owner identity.** The on-disk format has no owner identity,
no heartbeat and no expiry, so a run that died and a run still executing are
byte-for-byte indistinguishable. Adding that machinery was declined by the project
owner absent proven necessity, and the existing evidence shows it is unnecessary for
recovery *safety*: (1) the recoverable unit is one step, and its branch is chosen from
evidence that survives a crash — the ledger's side-effect record and the step's own
status — never from "is the other process alive", so a live process and a dead one
produce the same safe decision; (2) a genuine race is already refused one level down by
the atomic claim and by the ledger answering `RESUME`/`REFUSE` instead of `START`;
(3) a wrongly expired lease would convert "unknown, ask the owner" into "safe to
repeat", which is exactly the duplicate-side-effect bug this design exists to prevent.
A heartbeat would make the *report* nicer, never the outcome safer.

**The repeat guarantee, per action category.** Measured from the ledger, never assumed.
`RepeatGuarantee` has no `EXACTLY_ONCE` member.

| Category | Guarantee | Automatic repeat |
| --- | --- | --- |
| No durable record exists | `FIRST_ATTEMPT` — this delivery is a first attempt, not a repeat | permitted (ordinary authorization applies) |
| A terminal outcome is durably recorded | `REPLAY_OF_RECORD` — the recorded outcome is read back, the process is not re-executed | not a repeat |
| Every effect of a terminal record is replay-safe by construction | `CONVERGENT_REPEAT` | permitted; repetition converges on the same state |
| An effect kind that is replay-safe by construction (`_REPLAY_SAFE_KINDS`, i.e. workspace writes) began without a completion record | `CONVERGENT_REPEAT` | **not re-dispatched** — see below |
| A non-replay-safe effect (`HOST_PROCESS`, provider call) began without a completion record | `REFUSED_SIDE_EFFECT_UNKNOWN` — the outcome is `UNKNOWN` | forbidden; a new operation identity and re-approval are required |
| The record is an authority refusal (`DENIED`, policy, security failure) | `REFUSED_SECURITY_TERMINAL` | forbidden; re-approval and a new operation identity are required |
| Any other terminal record | `REFUSED_TERMINAL` | not admitted |
| A recoverable claim with no unprovable effect | `REQUIRES_REAPPROVAL` | only through the normal authority path |
| A ledger that cannot be read | `REFUSED_RECORD_UNREADABLE` | forbidden — an unreadable ledger is never treated as an empty one |
| The durable position cannot be established | `UNKNOWN` | forbidden |

**What is explicitly NOT guaranteed.** Not exactly-once: the guarantee is at-most-once
dispatch *per claim, as observed through a local filesystem ledger, and only while that
ledger is readable* — two processes racing on a lost claim can both be admitted in the
window before either record exists, because there is no distributed transaction and no
cross-process lock. Not atomic side effects: a host process is a whole program, and the
ledger records that it was claimed and how it ended, never that its effects were atomic.
No substitution of success or failure for an unknown: an unprovable outcome stays
`UNKNOWN`/`INDETERMINATE` in every projection. And no automatic repeat of anything: a
`FAILED`, `WAITING_APPROVAL` or `UNKNOWN` step is never re-dispatched by recovery.

**One honest gap, stated rather than hidden.** A step that was dispatched and whose
effect never settled carries a durable claim, and `StepGraphRunner.resume` deliberately
admits no second delivery under such a claim — it reads the record back as `UNKNOWN`.
Recovery therefore reports such a step as `NEEDS_OWNER` and **not** as `CONTINUE`, even
when the ledger's own evidence says the repetition would converge
(`CONVERGENT_REPEAT`). The convergent guarantee is reported as *evidence for the
owner*, never as permission, because a `CONTINUE` decision the executor could not
honour would be worse than no decision. Re-driving such a step deliberately is a **new
graph run id**, which means a new claim, a new scope and a fresh approval check — the
same rule Block #6 already states for any further attempt.

**The memory item contract.** `app/understanding/memory.py` stores memory entries about
the current source tree. It reuses `StructuralFact.fact_id` and
`InferredFact.evidence_fact_ids` for evidence, `ContextTrustLevel` for trust, and
`SnapshotFile.fingerprint` (the scanner's own SHA-256) for freshness; it adds no second
provenance scheme, no second trust vocabulary and no second hashing scheme. Durability
goes through `RunStore.project_state`, so there is no second storage backend. Every
entry carries a `kind`, a `source` (`ContextSourceType`) and `source_id`, an
`obtained_at`, a `MemoryScope` (project, paths, symbol), `evidence_fact_ids` /
`evidence_ids`, `trust_level`, `status`, an `EvidencePin`, a `claim_key`, and an
append-only `history`.

| `MemoryItemKind` | Exact condition | Trust |
| --- | --- | --- |
| `CONFIRMED_FACT` | a deterministic structural observation; requires at least one `evidence_fact_id` | `CONFIRMED` (or `VERIFIED` when a recorded verdict supports it) |
| `EXECUTION_RESULT` | an observed outcome of a real execution; requires a `run_id`, an `execution_result_id`, or explicit `evidence_ids` | `VERIFIED` |
| `VERIFIED_CONCLUSION` | a conclusion drawn from verified evidence | **`INFERRED`** — enforced, not intended |
| `HYPOTHESIS` | an assumption, or an inference citing no evidence | `INFERRED` or `UNVERIFIED` |
| `UNKNOWN_DATA` | something the project does not know, including a file the scanner would not fingerprint | `UNVERIFIED` |
| `STALE_ENTRY` | written only by freshness evaluation | the replaced entry's trust |
| `CONTRADICTION` | written only by contradiction detection | the **weaker** of the two conflicting entries |

`status` is one of `CURRENT`, `STALE`, `INVALIDATED`, `SUPERSEDED`. Nothing is deleted.

**No automatic promotion.** An `InferredFact` is never promoted to a confirmed fact.
There is no promotion method, no threshold and no scoring. `MemoryRecord.__post_init__`
refuses a `VERIFIED_CONCLUSION` carrying anything but `INFERRED` trust, refuses a
`CONFIRMED_FACT` carrying no `evidence_fact_id`, and refuses an `EXECUTION_RESULT` that
names no run or result. The only route from a conclusion to a confirmed fact is a
*different* entry that cites its own evidence, which marks the conclusion `SUPERSEDED`
without rewriting its `kind` or `trust_level`; and an operator attribution recorded in
`promoted_by`, which still creates a new linked entry rather than editing the old one.

**The freshness rule.** An entry that rests on a file pins that path **and** the
scanner's fingerprint of its bytes.

| Observation | Entry becomes | Reason code | What is recorded |
| --- | --- | --- | --- |
| path present, fingerprint identical | stays `CURRENT` | — | a `revalidated` history entry |
| path present, fingerprint differs | `STALE` | `source_content_changed` | a `STALE_ENTRY` naming the old and the new fingerprint |
| path absent, no file carries the fingerprint | `STALE` | `source_deleted` | a `STALE_ENTRY` |
| path absent, exactly one file carries the fingerprint | `STALE` | `source_renamed` | a `STALE_ENTRY` naming the path the content moved to |
| path absent, two or more files carry the fingerprint | `STALE` | `source_deleted_with_ambiguous_move_candidates` | a `STALE_ENTRY` listing every candidate |
| entry is unpinned (no fingerprint) | stays `CURRENT` | — | nothing: unpinned is not "current by a hash", it is explicitly unpinned |

**The rename decision, stated once.** A renamed file is **neither** treated as
still-current under the old path **nor** treated as a brand-new unrelated file. The
entry is marked `STALE` under the path it was obtained from, and the move is recorded as
evidence — the same facts may well hold at the new path, but nothing silently relocates
an entry's identity, because a coincidentally identical second file is not proof of a
move. Re-obtaining the entry at the new path is an explicit act. An ambiguous move (two
or more identical candidates) is reported as ambiguous and never resolved by picking
one. `test_a_renamed_file_is_neither_still_current_nor_a_new_unrelated_file` asserts
exactly this, including that the pinned path is unchanged and no entry is invented for
the new path.

**Contradictions.** Two `CURRENT` entries contradict when they share a `subject_key`
(the explicit `claim_key`, else kind + scope + pinned source) and their statement
digests differ. One `CONTRADICTION` entry is recorded per pair, carrying the weaker of
the two trust levels. Neither side is deleted, neither side is rewritten, and neither
side's `trust_level` is raised; detection is idempotent.

**Memory grants no authority.** Saved context is data. `ContinuationStrategy` carries no
approval, no scope, no allowlist, no token and no executable, and `app/runtime/
continuation.py` imports none of `ExecutionCoordinator`, `LocalExecutionAdapter`,
`RunScope` or `ApprovalPolicy`. A `RecoveryPlan` never builds a scope and never
dispatches; `RecoveryExecutor` requires a claim that matches the one on disk and still
delegates every dispatch to `StepGraphRunner.resume`, which re-authorizes from scratch.
Memory can influence *what is worth trying* and can never influence *what is permitted*.
`test_memory_cannot_widen_the_workspace_or_authorize_an_undeclared_command` proves it by
recording an "owner approved" memory entry and showing that the undeclared command is
still refused and the workspace perimeter is unchanged.

**Project Factory continuation.** `capture_context` records the workspace fingerprint,
the paths the scan saw, the planned step ids, the recorded attempts, the memory ids that
backed the context, and — explicitly — the paths the scan could **not** fingerprint, by
name only. `recall_context` compares that against a fresh scan and reports drift
(fingerprint changed, workspace root changed, paths added or removed) plus the saved
evidence that no longer holds. `plan_continuation` combines the saved context, the
current project state and the recovery planner's discoveries into one
`ContinuationStrategy`. A strategy is a plan; it is never a permission.

### 3.5.5 Operating surface and product integration (Block #8)

**Status: CURRENT / VERIFIED** for the routes, projections and UI described here, with
the limits listed at the end. `app/api/routes.py` is the HTTP surface;
`app/api/operating_surface.py` is the server-side projection and the only object the
routes call; `app/static/` is the console.
`tests/test_product_integration.py` (93 tests, `Ran 93 tests ... OK`, real exit code 0)
is the evidence, and every security-relevant property below is proved fails-before /
passes-after by patching the source in place. **Measured in the Block #8 recovery pass:**
24 source injections, 63 assertions, every one as expected — each injection restoring its
file byte-identically (SHA-256 before == after), with only the affected module's
`__pycache__` removed and the test run as `python -B`, so no stale bytecode could satisfy a
test. The counts in the previous revision of this paragraph were not re-measurable from the
tree and are replaced by these.

**There was no UI at all before this block.** `app/static/index.html`, `app/static/app.css`
and `app/static/app.js` are the whole client: no framework, no bundler, no build step,
no new dependency. Every control calls a real route, and every route delegates to the
existing `ForgeApiService` or to the existing `StepGraphRunner` / `RecoveryPlanner`. The
client holds **no local truth**: it has no optimistic update, no status inference, and no
code path that turns a missing value into a success. UNKNOWN and INDETERMINATE are
rendered as themselves, in their own colour.

#### The route map, with the authorization check on each route

The ten pre-existing routes are unchanged. Twenty-one operating routes and three asset
routes were added by Block #8; no pre-existing path was removed or altered. Block #9
added five more operating routes and **extended** `/api/product/change-impact` rather
than adding a parallel path.

| Route | Method | Authorization check |
| --- | --- | --- |
| `/api/product/onboarding` | GET | project boundary |
| `/api/product/requirements` | GET | project boundary |
| `/api/product/plan` | GET | project boundary |
| `/api/product/plan` | POST | project boundary; the submitted step list is echoed as *proposed* only and authorizes nothing |
| `/api/product/understanding` | GET | project boundary; scans the service's own workspace, never a request value |
| `/api/product/memory` | GET | project boundary |
| `/api/product/change-impact` | GET | project boundary + allow-listed baseline id that must be a record this server holds. **Block #9 extended this response** with `classification`/`basis` per finding, `stale_facts`, `dependent_conclusions`, `verification_plan` and `evidence_graph`; every Block #8 key keeps its Block #8 meaning |
| `/api/product/evidence-graph` | GET | project boundary + run boundary when `graph_run_id` is named. Read-only; every transition cites concrete identifiers and a transition with no evidence is returned `MISSING` with a `missing_code`. Refuses `ACCEPTED` while a mandatory link is missing |
| `/api/product/verification-plan` | GET | project boundary only. Read-only; the plan is chosen from the operator's declared expectations, the scanner's own topology and the recorded step results — no model is consulted, and `PASSED` is the only state counted as a pass |
| `/api/product/stale-facts` | POST | project boundary + allow-listed baseline id. Read-only unless `invalidate=true`, which re-evaluates file-pinned memory entries through the existing `ProjectMemory.refresh` (moves to STALE, appends history, deletes nothing) |
| `/api/product/repair-plan` | POST | project boundary + run boundary. **Plans and refuses; starts no repair.** `dispatch_performed` is always false on this surface and `dispatch_path` states why |
| `/api/product/runs` | GET | project boundary |
| `/api/product/runs/{run_id}` | GET | project boundary + run boundary (allow-listed id, must be recorded here) |
| `/api/product/recovery` | GET | project boundary |
| `/api/product/recovery/{graph_run_id}` | GET | project boundary + run boundary |
| `/api/product/graph/{graph_run_id}` | GET | project boundary + run boundary |
| `/api/product/approvals/{graph_run_id}` | GET | project boundary + run boundary; reports, cannot grant |
| `/api/product/goal` | POST | project boundary; recorded at hypothesis strength |
| `/api/product/memory` | POST | project boundary; kind allow-list (`HYPOTHESIS`, `UNKNOWN_DATA` only) |
| `/api/product/memory/refresh` | POST | project boundary |
| `/api/product/memory/{memory_id}/invalidate` | POST | project boundary; can only weaken a claim |
| `/api/product/baseline` | POST | project boundary |
| `/api/product/graph` | POST | project boundary + body identifier allow-list + `StepGraph.assert_valid` against the operator's declaration registry + the runner's own `RunScope`/`ApprovalPolicy`/`ExecutionCoordinator` |
| `/api/product/graph/{graph_run_id}/resume` | POST | as above, plus the record's declared plan must equal the caller's graph |
| `/api/product/verify` | POST | project boundary + `declaration_id` must exist in the operator registry + required `idempotency_key` |
| `/api/product/accept` | POST | project boundary + `declaration_id` + required `idempotency_key` |
| `/` | GET | none — the shell contains no data; every panel it renders calls a guarded route |
| `/app.css`, `/app.js` | GET | none — an explicit three-file allow-list, never a directory mount |

**What the boundary is, stated precisely.** There is still **no authentication** on this
surface; the residual risk recorded in §3.4 and in the audit is unchanged and this block
does not claim to close it. What each route above enforces is the boundary that does
exist:

* **project boundary** — the app is bound to one authoritative `project_id` at
  construction, from the service's own composition. A request naming a different project
  is refused 403 before any read, and the refusal **does not echo the requested name**,
  so it cannot be used as a probe for what exists.
* **run boundary** — a run id must match `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` (it reaches
  the storage layout) and must already be recorded in this service's own `RunStore`, so
  another deployment's run is indistinguishable from a run that never existed. The answer
  for an unrecorded id is **route-specific, and both forms leak nothing** — measured:
  `/api/product/runs/{run_id}` returns **404** `{"detail": "unknown run"}` with no record
  content, while the two read *projections* `/api/product/graph/{graph_run_id}` and
  `/api/product/recovery/{graph_run_id}` answer **200** with the not-found verdict in the
  body (`found: false`, or `record_classification: UNKNOWN` with
  `record_reason: no_persisted_step_graph`) and again no record content — the shape
  `test_32` pins. Neither answer names another deployment's data.
* **action boundary** — a caller names *which* operator-declared action runs, never
  *what* it is. The declaration registry, workspace, profile, command set, environment
  and approval policy all come from the server composition.
* **authority boundary** — no route constructs or bypasses `RunScope`,
  `ExecutionCoordinator`, `AuthorizedExecution` or `ApprovalPolicy`.

`authorize_project`, which is the single project-boundary check, is exercised on every
guarded route by `test_33_a_foreign_project_id_is_refused`; disabling it breaks that test.

#### One policy per composition, and an absent policy refuses

`RunExecutor` treats an absent approval policy as an unconditional denial
(`approval_policy_required`), and `StepGraphRunner` will not even be constructed without
an `ApprovalPolicy`. The resolution keeps every one of those fail-closed properties
instead of relaxing one to make the routes work:

* `ForgeApiService._approval_policy` **stays `None`** when no policy is passed. "No
  server-side approval policy was composed" is a real state, and two existing contract
  tests assert it directly (`test_missing_approval_policy_refuses_to_execute`,
  `test_default_service_requests_no_execution`). Replacing it with a default inside the
  service would have silently turned "no approval configured" into "approval configured".
* **A missing policy refuses the step graph, and a malformed one is a controlled
  configuration error.** `_validate_step_graph_ingredients` handles three distinct cases,
  all fail-closed, all **before** any `ExecutionCoordinator` is constructed, and none of
  them a raw `KeyError`/`AttributeError` traceback from inside the runner:

  | Case | Answer | Why it is that answer |
  | --- | --- | --- |
  | the `approval_policy` key is **absent** | `SurfaceCompositionError` → HTTP 500, code `approval_policy_missing` | the composition does not implement the contract at all; a raw `KeyError` would be a traceback rather than a refusal |
  | the value is **`None`** | the ordinary `None` → no runner → `step_graph_unavailable` (409) | "no approval position stated" is a legitimate state, deliberately *not* conflated with a broken composition |
  | the value is the **wrong type** / fails the contract | `SurfaceCompositionError` → HTTP 500, codes `approval_policy_invalid_type`, `approval_policy_contract_unsatisfied` | a policy that cannot be enforced is not a policy |

  A configuration error is reported as **500, never 409**: 409 would tell the caller "you
  asked for something disallowed", which is false, and would train them to retry. The
  refusal is never a policy substitution — a default would require approval only for its
  mandatory tool set, so substituting it would mean a deployment that never stated an
  approval position still ran declared host commands. `test_10c`, `test_64`–`test_69` pin
  the three cases and the ordering (`test_68` replaces the coordinator builder with one
  that records construction and then raises; the record stays empty for every failing
  case), and `test_70` pins the 500. The sensitivity test that reintroduces
  `or ApprovalPolicy()` makes `test_10c` fail.

**The two-layer approval fingerprint check, with each layer observed separately.** The
check has two independent layers: the server-side resolver compares the request's intent
fingerprint against the fingerprint the approval was granted for
(`app/tools/approval.py`), and the coordinator **independently** compares the approval's
`approved_fingerprint` against the intent it is about to run
(`app/execution/authorizer.py`). Acceptance review measured that a test driving the whole
stack passes through the resolver first, so a break in the **coordinator** layer alone was
invisible — probe I12, pass→pass. That gap is now closed, and the layering was measured
rather than assumed:

| Coordinator comparison | Resolver comparison | `test_61` (coordinator layer) | `test_63` (resolver layer) |
| --- | --- | --- | --- |
| intact | intact | PASS | PASS |
| **defeated** | intact | **FAIL** | PASS |
| intact | **defeated** | PASS | PASS |
| **defeated** | **defeated** | **FAIL** | **FAIL** |

This reproduces Block #6's property exactly: defeating **either** layer alone still
refuses the changed parameters (the other layer holds), and defeating **both** is what
breaks it. `test_61` and `test_62` drive `ExecutionCoordinator.execute` directly with an
always-answering resolver, so only the coordinator can refuse; `test_63` uses the
production single-use `InMemoryApprovalResolver` and covers the resolver layer plus the
parameter-change property. Neither test is claimed to cover the other's layer.
* **`create_api_app` states a policy explicitly**, at composition time, so the HTTP
  surface is usable. A deployment that wants its own passes it to `ForgeApiService` and
  the factory default is not used.

Test `test_api_layer_has_no_direct_adapter_or_process_access` also pins a structural
boundary this block had to respect: `app/api/service.py` may contain no reference to the
execution adapter or to process spawning, so the runner and its coordinator are composed
in `app/api/operating_surface.py` from ingredients the service hands out. That placement
is deliberate and is stated in `app/api/routes.py`: `service.py` **delegates** rather than
spawns, the route module only validates and delegates, and the operating surface is the
single composition point — so there is exactly one place that builds the coordinator and
exactly one policy decision, rather than one per route. No route in `routes.py`
constructs, substitutes or bypasses any authority component.

#### What the recovery pass after the crashed fix agent changed

The Block #8 fix agent crashed without a report and left unverified work. The state was
re-measured from the tree, and four defects it left behind were found and repaired. They are
recorded here because each one was a green-test-versus-real-behaviour gap, not a style point:

1. **`LocalExecutionAdapter` stopped harvesting on the isolating path.** The new
   `harvests_artifacts` declaration defaulted to `False` and became the *only* gate on
   harvesting, but harvesting used to live inside the isolation branch. Since
   `isolate_workspace=True` is the adapter's default, every isolating composition silently
   stopped harvesting while the step runner was still told the artifact had been looked for —
   a genuinely produced artifact was reported `FAILED` with `artifact_not_produced`.
   Measured: `test_a_produced_artifact_passes_by_its_harvested_digest` fails in exactly that
   state. The declaration now *adds* harvesting to the non-isolated branch instead of
   replacing the isolation rule, and `harvests_artifacts` reports both cases truthfully.
2. **`can_run_accept_cycle()` advertised a capability the routes refused.** With no approval
   policy composed it returned `True` while `POST /api/product/accept` answered 500
   `approval_policy_required` and wrote nothing — the same dishonesty that had already been
   fixed for `can_run_declared_verification`. It now requires a stated policy, and `test_32d`
   asserts it.
3. **Four committed tests relied on the permissive reading of `None`.**
   `test_09`, `test_10`, `test_11` and `test_12` in
   `tests/test_execution_authorization_hardening_v03.py` drove `ExecutionCoordinator.execute`
   with no `approval_policy`, so the mandatory-policy fix denied them before they reached
   their subject. Each now states the policy it was implicitly testing; nothing about the
   fingerprint check or the unapproved-flow property was relaxed, and that was re-measured:
   defeating the coordinator's fingerprint comparison still fails `test_09` and `test_10`,
   and stating an approval-requiring policy still fails `test_12`.
4. **The route-reachability rule could still pass by coincidence.** Narrowing the prefix
   collision was not enough: `app/static/app.js` *names* `/api/product/memory/{memory_id}/invalidate`
   in a comment, and matching raw asset text counted that comment as a client call. The rule
   now matches quoted string fragments in comment-stripped code, so a route mentioned only in
   prose no longer satisfies it. Measured: removing the real client call fails both `test_03b`
   and `test_03d`, and restoring the old rule together with that removal makes `test_03b` pass
   again — which is the reproduction of the historical false positive.

Two further tests passed for the wrong reason and were repaired: `test_14c` had given its
later-sorting step run the *newest* timestamp, so id-order and time-order agreed and the test
survived the very ordering defect it names (it now makes the two orders disagree and fails
when `reversed(list_run_ids())` is restored); and `test_70` called the error serializer
directly instead of observing the HTTP status a caller receives (it now drives
`POST /api/product/graph` and asserts 500, not 409 and not 200, over the wire).

**One correction to the incident description.** The report named
`app/orchestrator/run.py`'s `if approval_policy is None:` denial as an unauthorised edit. It
is **not** an edit: that code is present unchanged in the committed revision, and
`git diff` shows only the `generate_decision` / `run_store` threading in that file. The
contradiction D6 describes was therefore between committed `run.py` (denies `None`) and
committed `authorizer.py` (skipped the policy when `None`), and only the `authorizer.py` half
was changed.

#### What the console covers

Project list and current stage; goal entry; project understanding; requirements and
acceptance criteria; the declared plan with step dependencies; approval requests with
their parameters and consequences; execution start; live step statuses; verification
results; changed files; run history; one run's full durable record; recovery of a single
attempt; Project Memory split into confirmed facts, verified conclusions, execution
results, hypotheses, unknown data and stale entries. Loading, empty, service-unavailable,
API-refusal, timeout and indeterminate states each have their own visible rendering, and
the states that can be retried offer a retry. A capability the build does not compose is
shown as unavailable **and its controls are disabled**, rather than presented as working.
`test_03b` asserts the completeness property directly: every `/api/product/*` route is
referenced by the client, so no route is API-only and no section is decorative.

**Block #9 added four panels, each driven by a route that really answers.** The Change
Impact panel additionally renders each finding's classification, the basis behind it
(so a reader can audit the label instead of trusting it), the ambiguous-move warning
with its candidate list, the stale facts with their invalidation reason, the dependent
conclusions, and the implied checks with their six-state vocabulary. A new Evidence
Graph panel shows every transition, every missing link with its `missing_code`, the
computed requirement verdicts and the empty `claimed_by_agent` compartment with the
reason it is empty. A Verification Plan panel shows the six state counts with
`PASSED` as the only pass and names the coverage gaps. A Repair Decision panel shows
the outcome, the single `stop_rule`, the budget, the per-step probable cause with its
ordinal confidence and limitations, and that `dispatch_performed` is false. The
"Invalidate stale facts" control is the one write on the Change Impact panel, and it
calls the existing `ProjectMemory.refresh`. Block #9's reachability is asserted by
`test_09`, which requires each new route to appear as an **executable call shape** and
pairs that with a control proving a bare prefix and a comment mention are both
rejected — the exact false positive Block #8 recorded.

**The goal is not an automatic pipeline, and the docs must not imply one.** Goal →
understanding → requirements → plan is a sequence of *screens the operator moves through*,
not an automatic derivation. Recording a goal writes a `HYPOTHESIS` and nothing else; the
requirements, the criteria and their expectations come from the operator composition
(`declarations` and `acceptance_specs`), and the plan is those declarations. A goal
therefore does **not** influence which command may run, which criteria are checked, or
what the plan contains — by design, because natural language must never select a command
or define its own success condition. `test_44` pins that the recorded goal is a
hypothesis, that the requirements returned are the operator's, and that the stored
document agrees.

#### Change Impact: what is proved, and what is not

`GET /api/product/change-impact` compares the scanner's own SHA-256 fingerprints against
a recorded baseline and reports: which files were **added, changed or deleted**; which
structural facts those files carried; which topology edges cite those facts, so a
dependent node is *named*; which memory entries are pinned to a changed path; and which
declared acceptance expectations name a changed path, so the check to re-run is named.
A rename is reported as `probable_rename_to` and never as established, because two files
with identical bytes are indistinguishable from a move. `ChangeImpact` is proved by
`test_37` (a real user edit, both fingerprints reported, the edit preserved on disk),
`test_38` (deletion), `test_39` (probable rename), `test_40` (the response states what it
does **not** prove) and `test_41` (a criterion linked only where a declared expectation
names the changed path, everything else reported as `unlinked_requirements`). Disabling
change detection breaks `test_37`.

Three limits are part of the response rather than omitted:

* a dependent **check** is named only where a declared expectation *literally* names a
  changed path; where a requirement cannot be linked to a changed file by the data at
  hand it is listed under `unlinked_requirements`, and full requirement-to-file
  traceability is **not** claimed;
* the report never returns file contents, so there is no textual diff — the change
  surface is a file-level change set with both fingerprints;
* the report starts no action, promotes no assumption to a confirmed fact, and re-running
  a linked check remains an explicit operator decision.

#### Change Impact reasoning, Evidence Graph, Verification Planning, Repair decision (Block #9)

**Status: IMPLEMENTED AND VERIFIED for the four capabilities below. Two integration
limits are stated in this section and are NOT capabilities.**

##### The component map, and what was deliberately not duplicated

| New component | File | Builds on (existing) | Deliberately NOT a second |
| --- | --- | --- | --- |
| Change Impact Engine | `app/analysis/impact.py` | `SnapshotFile.fingerprint`, `UnderstandingSnapshot.facts` / `.topology` / `.manifests`, `freshness_of` and the invalidation codes from `app/understanding/memory.py`, `AcceptanceSpec` | fact registry, topology graph, memory store, fingerprint scheme |
| Evidence Graph | `app/analysis/evidence.py` | `MemoryRecord.memory_id` (the goal), `AcceptanceCriterion.criterion_id`, `StepGraphSnapshot.declared_plan`, `StepRecord` fields, `StepGraphRunner.intent_fingerprint`, `RunIdempotencyGuard.recorded_state`, `SnapshotFile` sets | goal store, step record, approval mechanism, verdict vocabulary |
| Verification Planning | `app/analysis/verification.py` | the same `AcceptanceSpec` declarations, the scanner's own `TopologyEdge` data, `StepRecord.verification_status` | dependency inference, model-guessed dependencies, test registry |
| Bounded repair decision | `app/analysis/repair.py` | `RepeatGuarantee` / `RepeatDecision` from `app/agent_runtime/idempotency_integration.py`, `ApprovalPolicy`, `StepRecord.attempts` | retry engine, second ledger, second approval mechanism |

The Block #8 `GET /api/product/change-impact` route was **extended**, not duplicated:
`OperatingSurface.change_impact` now builds its `changes` list through
`_surface_changes`, which reads every derived field from ONE engine finding, so the
report and the verification plan cannot disagree about what changed. Every Block #8
key keeps its Block #8 value (`ADDED` / `CHANGED` / `DELETED`), and
`probable_rename_to` still never becomes an established rename.

##### Change Impact classifications, and the basis retained

Every finding carries a `classification` **and** the `basis` that produced it, and
`classify_basis` in `app/analysis/impact.py` is the single place the classification
is decided:

* `CONFIRMED` requires at least one **structural** basis — `FINGERPRINT_MISMATCH`,
  `FINGERPRINT_ABSENT_AT_BASELINE`, `FINGERPRINT_ABSENT_NOW` or
  `SCANNER_FACT_MATCHED_BY_PATH`. These are observations the scanner made;
* otherwise, with at least one **weak** basis (a topology edge that *cites* a fact of
  the changed path, a declared manifest that *is* the path, a declared expectation
  that names the path, a memory entry pinned to the path, a test file whose path
  relates to it), the finding is `PROBABLE`: a real, cited relationship that is not
  by itself proof of impact;
* with an **empty** basis the finding is `UNKNOWN`. An empty basis never classifies
  as `CONFIRMED` — pinned by `test_25`, and proved by injection: making
  `classify_basis` return `CONFIRMED` for an empty basis turns that test red.

A `support_rank` is also reported, and it is **not** a confidence: it counts the
number of *distinct* basis kinds present, it is documented as such in the response
itself, and `classify_basis` ignores it, so it can never promote a finding.

**Ambiguous moves are never resolved.** A deleted path whose bytes appear at exactly
one added path is reported as `probable_rename_to` with a `PROBABLE`-strength basis
containing the words "NOT established". A deleted path whose bytes appear at two or
more added paths is reported with `ambiguous_move=true`, the full
`move_candidates` list, `probable_rename_to=""` and a note saying no candidate was
chosen. `test_22` pins both directions and carries an in-file control that re-runs
the same rule with the `len(candidates) == 1` guard removed, showing the loosened
rule *would* have picked the first candidate. Injection confirms it: that guard
removed turns `test_22` red.

**Stale facts and dependent conclusions.** A structural fact is stale when the same
fact id (which `StructuralFact.create` derives from content) is absent from the
current scan of the same source path. The reason is one of `memory.py`'s own codes —
`source_content_changed`, `source_deleted`, `source_renamed`,
`source_deleted_with_ambiguous_move_candidates` — plus
`fact_not_extracted_from_current_content` for the case where the file is still there
and the *observation* moved, which is a different fact. A memory entry whose
`evidence_fact_ids` cite a stale fact is reported as a dependent conclusion with
`status="PENDING_REVALIDATION"`, and it is **named, not rewritten**: nothing in
`app/analysis/` edits a memory record or promotes one. History is retained: a stale
fact id is merged into the surface's own baseline record under
`stale_fact_history`, and `POST /api/product/stale-facts` with `invalidate=true`
re-evaluates every file-pinned entry through the **existing**
`ProjectMemory.refresh`, which appends a `STALE_ENTRY` rather than deleting
anything.

##### Evidence Graph: transitions, and how a missing link is shown

The chain is `GOAL → REQUIREMENTS → ACCEPTANCE_CRITERIA → PLAN → STEPS → APPROVAL →
AUTHORIZED_ACTIONS → FILE_CHANGES → COMMANDS → VERIFICATION_RESULTS → FINAL_STATUS`,
and each link is a `TraceLink` carrying `from`, `to`, `status`, `source_ref`,
`target_ref`, `evidence`, `detail` and — for a missing link — `missing_code`.

A link with no evidence is **emitted with `status="MISSING"`** and a machine code
naming exactly which transition is absent. It is never dropped and never filled in
with a plausible object. The codes are a closed, checked vocabulary
(`MISSING_CODES`): a code that is not in it raises rather than being reported. Two
examples pinned by tests: a step that never reached an approval yields
`no_approval_decision_recorded` with the reason that no approved fingerprint is
recorded, and a requirement with no acceptance spec yields
`requirement_has_no_declared_criterion` with the reason that nothing can check it.

**The final status does not come from an agent's claim.** `derive_verdict` reads only
the recorded `VerificationStatus` and the requirements' own criteria: `PASSED` is the
only passing state, `ERROR` maps to `UNKNOWN` (never to `FAILED`), and an empty
criterion set is `UNPROVEN`. `StepGraphStatus.COMPLETED` is **not** accepted as proof
and the response says so (`graph_status_used_as_proof: false`). Anything an agent
claimed lives in the separate `claimed_by_agent` compartment, which the verdict
computation never reads; `test_13` shows a claim cannot rescue a graph with a missing
mandatory link.

**`ACCEPTED` is refused when mandatory evidence is missing.** `_acceptance_gate`
refuses on the mandatory codes and returns
`final_status="ACCEPTED_REFUSED_INCOMPLETE_TRACE"` with
`mandatory_missing_codes`, `mandatory_missing_kinds` and
`acceptance_refusal_reason`. `test_11` pairs the refusal with a negative control (a
non-mandatory code does **not** refuse) and a positive control (with no mandatory
code missing, the computed verdict is returned). Injection confirms it: disabling
the refusal turns `test_11` red.

**Approval is bound to the specific action and its parameters.** The
`STEPS → APPROVAL` link carries the step's `parameters`, the `approved_fingerprint`
the runner recomputes through `StepGraphRunner.intent_fingerprint`, the
`observed_fingerprint` and a `parameter_binding_holds` boolean. The **two-layer**
check is untouched: the resolver in `app/tools/approval.py` and the independent
comparison in `app/execution/authorizer.py` (`approved_fingerprint !=
intent.fingerprint` → `intent_fingerprint_mismatch`) are both still present, and the
graph reports the value the coordinator compares rather than re-deriving it.

##### Verification Planning: the six states

`app/analysis/verification.py` plans a check for each declared expectation and each
changed path no expectation covers. The vocabulary is exactly `NOT_RUN`, `RUNNING`,
`PASSED`, `FAILED`, `SKIPPED`, `UNKNOWN`, and `PASSED` is the only state whose
`is_pass` is true — `test_30` asserts that for all six values and
`only_passed_counts_as_a_pass` is stated in the response. The mapping is ordered by
what the attempt establishes: `PASSED`/`FAILED` are real observations; `ERROR`
becomes `UNKNOWN`; then the step status decides — `RUNNING → RUNNING`,
`UNKNOWN → UNKNOWN`, `BLOCKED`/`CANCELLED → SKIPPED`, `FAILED → FAILED`, and
`SUCCEEDED` with no evaluated check → `UNKNOWN`, because a successful command is not
a passing check. Injection confirms the ordering matters: restoring the earlier order
(which read the declared check first and reported an unprovable step as `NOT_RUN`)
turns `test_30` red.

Each plan item keeps the REASON it was chosen, its `driven_by` link to a change, its
`requirements`, its `related_tests`, its `evidence`, its `criticality` and, for a
gap, `uncovered_reason`. **Coverage gaps are named, never rounded up**: a changed
path with no declared expectation becomes a check with `state="UNKNOWN"` and
`uncovered_reason="no_declared_expectation_covers_this_path"`, and a requirement with
no criterion is listed in `requirements_without_checks`. An affected test is reported
with the words "not proof that it covers the change", because it is a path relation.

Dependencies are never guessed: the plan reads the scanner's own `TopologyEdge`
records. No model is consulted anywhere on this path.

##### The bounded repair decision: budget, attempt cap, no-progress, and the refusal

`app/analysis/repair.py` performs five of the six steps of the loop and **deliberately
does not perform the sixth**. It detects failure from the recorded statuses, collects
the step's own evidence codes, names a probable cause with an **ordinal**
`Confidence` (`SUPPORTED` / `INFERRED` / `UNSUPPORTED`, decided by whether a
*specific* recorded evidence code names the cause — never a percentage), builds the
minimal targeted re-verification, and reads the required approval from the
composition's own `ApprovalPolicy` and resolver. It then **dispatches nothing**, and
says so: `dispatch_performed: false` and a `dispatch_path` field explaining that an
admitted repair would have to go through an existing authorized entry point under a
fresh idempotency key.

* **budget** — `attempt_cap` (default 3, ceiling 50), `step_budget` (default 8,
  ceiling 200) and `seconds_budget` (default 900, ceiling 86400). A caller may lower
  a budget but never raise it past the ceiling; a malformed value falls back to the
  default and NaN is not a budget (`test_45`);
* **attempt cap** — the count comes from the attempt's own recorded `attempts`, so it
  cannot be reset by asking again. `test_42` plans the same attempt with cap 1 and
  with cap 5 and shows the difference is caused by the cap alone;
* **no progress** — `progress_marker` is a SHA-256 of *observables only* (each step's
  status, verification status, exit code, evidence codes, ledger guarantee and graph
  status): no timestamp, no counter, no random value, so two observations of an
  unchanged attempt produce the same marker. Feeding the previous marker back stops
  the loop with `stop_rule="no_progress"`. `progress_made` is **three-valued**:
  `null` when no previous observation was supplied, because "no previous
  observation" is not the fact "no progress was made";
* **`REFUSED_SIDE_EFFECT_UNKNOWN` stays a refusal.** When the measured repeat
  guarantee is `REFUSED_SIDE_EFFECT_UNKNOWN` the loop returns that outcome unchanged
  and stops with `stop_rule="side_effect_outcome_unknown"`, performing nothing.
  `test_41` produces that guarantee from a **real** `RunIdempotencyGuard`: it claims
  an operation identity, begins a non-replay-safe `HOST_PROCESS` side effect and
  never completes it, requires the guard's own `repeat_decision` to answer
  `REFUSED_SIDE_EFFECT_UNKNOWN` (so the test cannot pass vacuously), and then shows
  the loop refuses. Injection confirms it: bypassing that branch turns `test_41` red.

##### Two measured defects this block found and fixed

* `OperatingSurface._repeat_guarantees` and `_operation_states` read
  `runner.ledger_guard` **without calling it**. It is a method, so every
  `repeat_decision` raised `AttributeError`, the per-step `except` swallowed it, and
  the repair loop planned against **no measured guarantee at all** — "the ledger says
  nothing" and "the ledger could not be read" looked alike. Both now call it, and
  `test_41c` plus injection pin the behaviour.
* `POST /api/product/repair-plan` did not pass `previous_progress_marker` through, so
  `NO_PROGRESS` was unreachable over HTTP even though the module-level rule was
  correct. The route now forwards it; injection (removing the argument again) turns
  `test_43` red.

##### What is NOT proved here

* **No repair is executed.** Step 6 of the loop is not implemented on this surface
  and is reported as `dispatch_path` rather than simulated. No code path here
  re-runs a `FAILED` step.
* **The `changed-path:*` checks are `UNKNOWN` by construction.** They report an
  absent declared expectation, not a test result.
* **`claimed_by_agent` is always empty in this build**, because the execution path
  makes no generative dispatch. The compartment is shown empty, with that reason,
  rather than omitted.
* No browser-driven walkthrough; the console is asserted through `TestClient` at the
  HTTP boundary. No real-provider path: the whole suite is structurally offline.
* The Block #8 note about the approval-policy contract migration still stands as
  written above.

##### Evidence

`tests/test_block9_evidence_and_verification.py`, 33 tests, three consecutive
isolated runs green (`Ran 33 tests`, `OK`, exit 0). Fails-before was proved by
injection for ten contracts, each restored byte-identically (SHA-256 identical before
and after). The Block #8 regression was re-run on the changed tree: 93 + 31 + 33 +
26 + 14 + 36 + 15 + 16 + 17 + 41 + 7 + 24 + 26 + 9 + 22 + 34 + 12 + 26 + 16 + 24 +
12 + 50 + 15 + 16 + 6 + 32 (1 skipped) + 35 + 6 tests, all `OK`, exit 0.

#### Project Memory on this surface

An operator may record a statement, but only at `HYPOTHESIS` or `UNKNOWN_DATA` strength:
`CONFIRMED_FACT` and `VERIFIED_CONCLUSION` are refused with the machine code
`kind_not_recordable`, because a confirmed fact must cite an evidence fact id the
understanding layer produced and a request cannot supply one. A goal is recorded as a
hypothesis. `POST /api/product/memory/refresh` re-evaluates every **file-pinned** entry
against the workspace as it is now and moves a changed, deleted or renamed source to
STALE with a reason code; an entry with no pin has nothing to compare against and is
honestly excluded from the checked count rather than reported as fresh. Memory still
grants nothing: `app/understanding/memory.py` imports no execution authority and no route
derives an execution input from a memory record.

#### Verified limits of this surface

* **Isolation of the declared-verification route, measured.** `run_declared_verification`
  dispatches without supplying an `ExecutionCoordinator`, so `RunExecutor` builds the
  default one — `LocalExecutionAdapter(..., isolate_workspace=True)` — and the declared
  command runs in a **staged copy**; the operator's workspace is left untouched.
  `test_10` asserts that, and `test_10b` asserts the contrast: a **step-graph** step does
  write the operator's workspace, because that runner's coordinator is composed with
  `isolate_workspace=False`. Both behaviours are documented here because both are load
  bearing for an operator, and neither is claimed to be the other.
* **The step-graph route is composed or absent, never loose.** `build_step_graph_runner`
  requires a runtime with a run executor **and an explicitly injected idempotency guard**.
  The process-wide default guard shares one ledger root across callers, and this project
  has already measured false failures from shared default roots, so the HTTP step-graph
  route refuses (`step_graph_unavailable`) rather than building one. `test_02` and
  `test_29` pin the fail-closed behaviour.
* **Repeat protection comes from the durable record, not from an in-process flag.** Two
  routes with the same graph run id: the second returns the recorded attempt with
  `replayed: true` and executes nothing (`test_30`). **The recorded payload is returned unchanged**: a response never rewrites the record of a past attempt. The per-response fields (`replayed`, `executed`, `command_started`, `commands_started`, `recorded_result`) describe *this* call, while the step records inside `result` describe the attempt that ran, with the values it really had. Disabling
  `StepGraphRunner._claim_graph_run` did **not** break that test, and the finding is
  recorded rather than hidden: the claim is the *in-process concurrency* guard
  (`tests/test_step_graph_managed_execution.py` covers it), while a caller who asks twice
  is stopped by the record. This is a measured correction to an earlier overstatement.
* **`artifacts_are_harvested` on the step-graph runner is DERIVED, never asserted.** It is
  read from the composed adapter's own `harvests_artifacts`, which is true when the adapter
  isolates its runs **or** when the composition declared `harvests_artifacts=True`. Without
  a true value a declared `ARTIFACT_PRODUCED` check is `ERROR` (unevaluable) on every step;
  harvesting records the digest of a file the step already declared in `artifact_targets`
  and grants nothing. A hard-coded `True` over an adapter that harvests nothing is worse
  than the `ERROR`: it converts an honest "cannot be evaluated" into a false `FAILED` with
  `artifact_not_produced`, which then blocks every dependent step. Measured:
  `test_32b` (a real artifact must verify `PASSED` with a measured digest and its dependent
  must still run) fails both when the declaration is removed and when the flag is
  hard-coded back to `True`; `test_31` pins the other direction (exit 0, no artifact ⇒
  `FAILED`); `test_an_unevaluable_artifact_check_is_an_error_not_a_failure` pins `ERROR`
  when the adapter neither isolates nor declares harvesting.
* **No authentication.** See above: this is a loopback surface for a local operator.
* **No process isolation.** These are text and contract checks inside one operating-system
  process. A process already running as this user is not confined by them, and no route
  here claims otherwise.
* **Not verified by this block:** a browser-driven end-to-end walkthrough (the UI is
  asserted through `TestClient` at the HTTP boundary, not through a real browser); visual
  or accessibility testing; long-running operation of the console against a live server;
  and any real-provider path, since the whole suite is structurally offline.
* **OPEN — a mandatory-policy contract migration the crashed fix agent did not complete.**
  Making `None` an unconditional denial in `ExecutionCoordinator` (the D6 fix, which is
  mandated and correct) invalidates the *fixtures* of every test that drove the coordinator
  directly without a policy: measured, **66 tests across 12 files** failed, and restoring the
  pre-fix permissive behaviour for one probe run turned **all 12 files green** — so the
  single cause is the guard, not a second defect. The 12 files are
  `test_credential_redaction_boundary.py`, `test_engineering_execution_integration.py`,
  `test_execution_authorization_hardening.py`, `test_execution_authorization_hardening_v02.py`,
  `test_execution_plane.py`, `test_execution_sandbox_boundary.py`,
  `test_integration_failure_matrix.py`, `test_local_execution_adapter.py`,
  `test_run_scope_contracts.py`, `test_security_hardening_block.py` and
  `test_test_verification_adapter.py` (plus `test_workspace_root_always_checked.py`,
  `test_execution_timeout_and_tree_kill.py` and
  `test_execution_authorization_hardening_v03.py`, which the recovery pass already migrated).
  The migration is *mostly* mechanical — `ApprovalPolicy()` evaluates `NOT_REQUIRED` for
  every tool, so for these fixtures it is behaviourally identical to the old permissive
  `None` and preserves each test's subject — but it is **not** uniformly mechanical: the
  approval-hardening suites contain cases whose *subject* is which kind of refusal a missing
  or wrong piece produces (`test_missing_required_approval_policy_is_denied` among them), and
  those need per-test judgement rather than a blanket substitution. It was deliberately not
  performed in the recovery pass: it is a project-wide contract migration across a dozen
  committed security-test files, which is beyond the authorised surgical scope, and a
  careless sweep there is exactly how a security test is quietly weakened. **Gate 1 cannot
  pass until it is done, deliberately, with per-test review.**

### 3.6 Memory and knowledge

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

### 3.7 Platform (Stage 1)

Stage 1 is being built as ordered steps. Steps 1 through 3 are implemented; the
remaining steps are not, and nothing below should be read as a claim about them.

| Area | Status | Evidence |
| --- | --- | --- |
| Platform domain contracts (8 records, 8 status enums, stdlib-only, no driver) | **CURRENT / VERIFIED** | `app/platform/models.py`, `app/platform/enums.py` — `tests/test_platform_domain_contracts.py` |
| PostgreSQL schema with migrations, roles, `FORCE` row-level security, and append-only tables | **CURRENT / VERIFIED** | `app/platform/persistence/postgres/migrations/0001..0014_*.sql`, `postgres/schema.sql` — `tests/test_platform_postgres_schema.py` |
| Tenant containment, role separation, system-scope containment, append-only usage, membership provenance | **CURRENT / VERIFIED** | migrations `0002`, `0011`–`0014` — `tests/test_platform_postgres_security.py` |
| Repositories, Unit of Work, three scopes, error normalization, identifier boundary | **CURRENT / VERIFIED** | `app/platform/persistence/{database,repositories,mapper,protocols,errors}.py` — `tests/test_platform_persistence.py` |
| Two logins, one scope each, proven from the role catalogue rather than by convention | **CURRENT / VERIFIED** | `app/platform/persistence/database.py` — `tests/test_platform_persistence.py` |
| Aborted-transaction detection: a caught statement failure cannot be committed silently | **CURRENT / VERIFIED** | `app/platform/persistence/database.py` — `tests/test_platform_persistence.py` |
| Subject-bound discovery, one subject per transaction | **CURRENT / VERIFIED** | migration `0015`, `app/platform/persistence/database.py` — `tests/test_platform_persistence.py`, `tests/test_platform_postgres_security.py` |
| Column-limited identity writes: disjoint capabilities per scope | **CURRENT / VERIFIED** | migration `0015` — `tests/test_platform_persistence.py`, `tests/test_platform_postgres_security.py` |
| Compare-and-set run lifecycle transitions | **CURRENT / VERIFIED** | `app/platform/persistence/repositories.py` — `tests/test_platform_persistence.py` |
| Platform Application Services over those repositories | **PLANNED / NOT IMPLEMENTED** | no such module exists; `app/platform/persistence/protocols.py` is the interface they will use |
| Authentication trust boundary: `AuthenticatedPrincipal`, verifier port, fail-closed refusals | **CURRENT / VERIFIED** (contract only; no credential mechanism) | `app/platform/principal.py` — `tests/test_platform_auth_contracts.py` |
| Authentication mechanism (session / JWT / federated) | **PLANNED / NOT IMPLEMENTED** | open decision O-2; the port is defined and no implementation ships |
| Application Services slice: AS-1 discovery and AS-2 project listing, authorization algorithm, application error vocabulary | **CURRENT / VERIFIED** | `app/platform/application/` — `tests/test_platform_application_discovery.py` |
| Application Services AS-3..AS-9 (project creation, run preparation, `RunRecord`, Platform -> Core port) | **DESIGNED / NOT IMPLEMENTED** | `docs/STAGE-1-STEP-4-AUTH-APPLICATION-SERVICES-DESIGN.md` section 5 |
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

### 4.1 Approval fingerprint verification is unconditional (fixed)

**This limitation is closed in both halves of this document, and is kept only as a
record.** It previously said the coordinator verified the returned fingerprint "only when
the resolver supplies a non-empty one", and quoted
`if res_fp and res_fp != intent.fingerprint:` with the comment that an empty fingerprint
short-circuits. That was true; it is not true now.

Commit `1220398` ("security: close execution authority hardening block") replaced the
block. The check at `app/execution/authorizer.py` reads:

```python
if not resolution.approved_fingerprint or resolution.approved_fingerprint != intent.fingerprint:
    return self._deny(..., reason="intent_fingerprint_mismatch")
```

A resolver that returns a bare `ApprovalState.APPROVED`, or a wrong fingerprint, or an
object of an unexpected type, is denied. The regression test
`tests/test_execution_authorization_hardening_v03.py::test_09_empty_approved_fingerprint_denied_fail_closed`
asserts the opposite of the old documented probe result.

**Status: FIXED by `1220398`.** It was wrongly still listed as a hardening candidate.

### 4.2 Capability classification: fail-closed for unrecognized argv, with a residual

**Partly fixed.** The previous text said `ExecutionCapability` had four members and that
"anything outside those lists gets `capabilities == frozenset()`, and the policy then
admits a bare-string `allowed_commands` entry with **free-form argv**". Commit `693dac5`
added a **fifth** member, `OPAQUE` (`app/execution/capabilities.py:17`), and
`classify_invocation` now adds it whenever an invocation is not recognized and carries
arguments (`:272-274`). `app/execution/policy.py:111-117` denies such an invocation with
`argv_not_authorized` unless the profile grants `OPAQUE`, and `capabilities` defaults to
the empty set (`app/execution/profile.py:70`). The fail-closed half of the
declared-invocation model is therefore implemented, and it is covered by
`tests/test_execution_authorization_hardening_v03.py`.

**What genuinely remains open**, and it is a narrower statement than before:

- recognition is still a **name-based list**. `_KNOWN_STANDARD_TOOLS` (41 names) plus
  `_WRAPPER_EXECUTABLES` (15) and `_NETWORK_EXECUTABLES` (17) decide what counts as
  recognized, and `_KNOWN_STANDARD_TOOLS` classifies known tools such as `make`, `rm`,
  `sed` and `tar` to the **empty** capability set, so those need no explicit capability
  grant. The risk moved from "an unknown wrapper is not gated" to "a known tool is gated
  too weakly", which is a real residual rather than an unguarded hole.
- the counts in the previous text were wrong: `_NETWORK_EXECUTABLES` has 17 entries, not
  16, and the enum has five members, not four.

**Status: PARTIAL.** `OPAQUE` closes the fail-closed gap; the recognition-list residual is
recorded in `DECISIONS.md` ("Execution Authorization Contract v0.2").

### 4.3 Profile environment is fingerprinted but not injected into the child (fixed)

**This limitation is closed and is kept only as a record.** It said the adapter overlaid
the *profile* environment at process start, and that a change to
`ProjectExecutionProfile.environment_variables` therefore did not change the intent
fingerprint. Both halves were wrong, in opposite directions:

- the profile environment **is** a first-class intent field
  (`app/execution/intent.py:35`), is populated at `:209-210`, and **is** part of the
  fingerprint at `:50-52`; `tests/test_execution_authorization_hardening_v03.py` asserts
  that changing it changes the fingerprint;
- the adapter copies **only** `intent.environment_variables` into the child
  (`app/execution/adapter.py:298-299`). Profile environment values are an authorization
  list, not a payload delivered to the process.

**Status: FIXED by `693dac5`.** The second half is now stated as intended behaviour rather
than as a defect, because it is one: a profile declares the *values a declared command may
receive*, and the declaration must match them exactly. Delivery is the request's job.

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
secret redaction. It does **not** provide namespaces, cgroups, Job Objects used as an isolation boundary, or
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

**One process kind now does have OS-level containment.** The project process that
the local runner starts (`app/projects/runner.py`) is created inside a Windows job
object with `KILL_ON_JOB_CLOSE`, a per-process memory cap and an active-process cap,
and its primary token is lowered to low integrity before it runs
(`app/execution/containment.py`, `tests/test_run_sandbox.py`). That is not a kernel
filesystem sandbox: the child can still read what its token may read, and it can
still write Forge's own data when that data carries the same low label (the payload
then reports `writes_outside: "allowed"`). The tokens the execution plane itself
uses for build steps, and every other subsystem, are unchanged: this section's
"not implemented" statement is about them, not about the local runner.

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

`EphemeralWorkspaceManager` copies the supplied workspace root into a temporary
scratch directory. What it does **not** copy is `.git`, `__pycache__`, `*.pyc`, and every
path the shared name-based secret recognizer refuses (see §4.10 for that filter and its
residual). There is no declared-input allow-list and no content digest for referenced
inputs. The frozen `RunScope` now binds the workspace by
canonical path plus `WorkspaceIdentity`; whatever the caller passes as `workspace_root` is
what gets staged, minus those exclusions.

**Status: PARTIAL** (declared inputs and content digests remain deferred; workspace
identity binding is implemented and verified).

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

### 4.9 The Core API has no authentication, and the launcher says so

The Core API surface (`app/api/server.py`) has **no authentication**: no route requires a
credential and no route verifies an identity. It exists for a local, trusted operator, and
the Platform's authentication work covers a different surface. Three things were changed in
this pass and one deliberately was not:

- CORS no longer uses `allow_origins=["*"]` with `allow_credentials=True`. The allow-list
  is explicit and local by default, credentialed CORS is off and cannot be turned on from
  this module, and a `*` in `FORGE_API_CORS_ORIGINS` is **refused** rather than silently
  narrowed, because an operator who wrote `*` believes the surface is open.
- `app/api/main.py` is the launcher. There was none before: `create_api_app` existed and
  tests imported it, but nothing bound a host, which is how the permissive CORS survived
  review. It binds to loopback by default and refuses non-loopback binds because the
  surface has no authentication.
- Authentication was **not** invented. Adding an identity mechanism here is open decision
  O-2 and would create a second trust model alongside the Platform's. The residual risk is
  recorded here, and `tests/test_api_exposure.py::test_14` asserts that no route has gained
  an authentication dependency, so the situation cannot change silently in either
  direction.

**Status: PARTIAL — an owner decision is required.** Either the Core API gains an
authentication layer, or it must not be exposed beyond loopback, or the product surface
becomes the Platform and this API stays internal.

### 4.10 Secret staging is a name-based filter, not a declared-input allow-list

The staged execution workspace withholds files whose **name** marks them as credential
material, and prunes credential directories before descent. It does not read file contents
to decide, because reading a file to classify it would be the leak it is meant to prevent.

**One recognizer answers for every traversal.** `app/execution/secret_files.py` holds the
rules, and both the execution staging step (`app/execution/workspace_manager.py`) and the
project scanner (`app/understanding/scanner.py`) call into it — `is_secret_file` for files
and `is_secret_directory` for directories. The scanner previously carried its own
`_IGNORED_DIRS` set naming build artefacts only, so it descended into `.terraform`, `keys`,
`certs`, `.ssh` and friends and inventoried files there whose names carry no secret marker.
That was not a disclosure — recognized files are recorded with `fingerprint=None` and never
read — but it consumed the file-count limit with non-source content and meant the two
traversals disagreed about credential territory.

**Pruned directories:** `.ssh`, `.aws`, `.gnupg`, `.kube`, `.docker`, `.azure`,
`.terraform`, `.terraform.d`, `keys`, `certs`, `private`. A directory is pruned before
descent, so nothing inside it is inspected.

**Recognized names, extensions and patterns.** Exact names include `.env`, `.envrc`,
`credentials[.json]`, `service_account.json`, `application_default_credentials.json`,
`aws_credentials`, `kubeconfig`, `secrets.{yaml,yml,json}`, `secret.{yaml,yml,json}`,
`terraform.tfstate[.backup]`, `.git-credentials`, `.gitconfig`, `.terraformrc`, `.netrc`,
`.pgpass`, `.npmrc`, `.pypirc`, `.htpasswd`, `htpasswd`, `authorized_keys`, `cacerts`,
`keystore`, `secring.gpg`. Patterns cover `firebase-adminsdk-*.json`, `*.tfstate[.backup]`
and **encrypted key material** (`*.pem.enc`, `*.key.enc`, `*.p12.enc`, `*.pfx.enc`).
Extensions include `.pem`, `.key`, `.pfx`, `.p12`, `.pkcs12`, `.kdbx`, `.jks`, `.keystore`,
`.ppk`, `.asc`, `.gpg`, `.ovpn`.

The `.pem.enc` rule is worth naming. What is verifiable is that it is **reachable now**: it
is a name pattern checked before the extension rule, so `server.pem.enc` is recognized while
`notes.enc` and `archive.tar.enc` still return `None` — the rule matches encrypted key
material, not everything ending in `.enc`. A claim that the rule was "unreachable before
this pass" is **not** verifiable and is therefore not made: `app/execution/secret_files.py`
is untracked by Git and `*.pem.enc` appears nowhere in `HEAD`, so there is nothing to check
the earlier behaviour against.

**Deliberately not secrets**, because refusing them is a false positive that protects
nothing: committed templates (`.env.example`, `.env.sample`, `.env.dist`, and any name with
such a dot-separated segment), the **public** half of a key pair (`*.pub`, so `id_rsa.pub`
is staged while `id_rsa` is not), and `known_hosts`, which is public host-key data. The
`.pub` rule is a suffix test, not a general exemption: `notes.txt.pub` is staged because it
was never secret, and no file becomes secret-like by gaining that suffix.

**Normalization is explicit and platform-stated.** Windows resolves `secret.pem.` and
`secret.pem ` to `secret.pem`, so the trailing dots and spaces are stripped before matching;
otherwise a single character bypassed the extension rule. Case is folded. Both were
measured rather than assumed, and the trailing-dot behaviour is a Windows property — the
tests state that rather than implying it holds everywhere.

**What is still not recognized, stated plainly:** a secret stored inside an ordinary file
name (`notes.txt`) is **not** caught, and it *is* read and fingerprinted when it sits in an
ordinary directory. `tests/test_execution_secret_staging.py::test_27` asserts that
limitation rather than hiding it. The complete fix is a declared-input allow-list with
deny-by-default staging, which remains deferred (Block 2).

**Status: PARTIAL.** The leaks measured so far — `.env`, key files, credential JSON, the
25 names an independent re-audit found unhandled, encrypted key material, and the
trailing-dot normalization bypass — are closed, and staging was measured end to end: with
every withheld name and every credential directory present in a source tree, none reached
the staged workspace. The count is not asserted here from memory: it is the length of the
`DENIED` table in `tests/test_execution_secret_staging.py`, pinned by that file's own
`test_04b`, so the document and the table cannot silently diverge again. A deny-list cannot
be complete by construction, which is why the residual case above is documented instead of
implied away.

### 4.11 `Tcl_AsyncDelete` no longer loses the single-process suite summary

A single-process `unittest discover` used to abort during teardown with
`Tcl_AsyncDelete: async handler deleted by the wrong thread`, losing the `Ran N tests`
summary. Verified on a clean checkout of `d1c007a` in a separate worktree: it reported
`Ran 1766 tests` and a failure exit, with the summary truncated.

**Still reproducible in one invocation form (measured, `d1c007a` plus this working
tree):** the summary is produced by the verbosity-1 and verbosity-2 forms, and is lost by
the default verbosity-0 form, where the crash lands before stdout is flushed. Run

```
python -X utf8 -u -m unittest discover -s tests -t tests -v
```

to get the summary in one process: **`Ran 1971 tests ... OK (skipped=36)`** (1967 passed,
4 skipped test methods, 0 failures, 0 errors). The count itself is independent of the
invocation form — an instrumented `unittest.TextTestResult` reports the same 1971 / 1967 /
4 / 2206-collected numbers at verbosity 0 — so nothing about the suite's result depends on
which form is used; only the printed summary does. Earlier revisions of this file quoted
`Ran 1841 tests ... OK (skipped=35)` without a DSN and `Ran 2076 tests ... OK (skipped=3)`
with one; those figures were accurate for the tree they were taken from and are no longer
reproducible because the tree has grown. The abort is an environment/teardown interaction,
not a product defect, and it is recorded because it makes the summary form-dependent. The
counts themselves are reconciled in §4.16.

**Status: PARTIAL — the summary survives at verbosity 1 or higher; verbosity 0 still
aborts during teardown after the same result has been computed.** The two failures this
section used to reference are gone; see §4.12.

### 4.12 Task dispatch attempts the configured default provider (fixed)

`tests/test_api.py::test_07_task_run_via_api` and
`tests/test_desktop.py::test_04_home_view_task_dispatch` used to fail with
`Provider fallback chain exhausted`. They were first recorded here as environmental, which
was wrong in an important way: the trigger was the local token ledger, but the defect was
in routing.

**Root cause.** For a specialized category the attempt plan was built from the policy's
static preference list alone:

```python
elif cost_aware:
    attempts = list(decision.candidates)   # code -> openrouter, deepseek, openai, anthropic, groq
```

`mock` is not in that list, and the branch that inserts `default_provider` runs only for
non-specialized categories. A deployment whose `default_provider` was `mock` therefore
**never attempted the provider it had explicitly configured**. The decisive measurement:
with only the mock provider installed, a `code` task failed with
`Provider fallback chain exhausted: openrouter: provider is unavailable or not registered`.
`TaskRunRequest().category` defaults to `code`, so this was the product's main path.

**Fix.** `Dispatcher._with_default_provider` appends the configured default when it is
absent, after the same admission checks every other candidate passes
(`_automatic_route_rejection`: registered, capabilities registered, enabled by
configuration, not a disallowed paid provider, holding its declared key) plus a check that
it is a registered **agent** and not merely a registered provider. The policy's candidate
order is untouched and `DispatchPolicy.decide` is unchanged; the default runs last.

**Test seam.** Both tests previously ran against the shipped provider configuration, so
their outcome depended on the developer's environment and they could reach a live provider
if a key was present. They now build the runtime with `enabled_providers=("mock",)`, which
makes the offline provider the only candidate and removes the external dependency instead
of masking it.

**Status: FIXED.** Both tests pass, and the full suite reports no failures.

### 4.13 A failure reports its reason from the terminal Run, not from events

Extracting the outcome of a run had two independent defects, and both were fixed together
because each masked the other.

**Failure was decided by scanning events.** A rejected *intermediate* attempt emits
`provider_result(success=False, error=...)`, so a **successful** run whose first preference
had been refused was reported as failed with a stale message. Observed: a completed run
whose output was the mock provider's response reported
`error="provider is disabled by configuration"`.

**The reason was then read from the wrong field.** The first fix read the failure from
`run.result` only. But `RunExecutor` assigns `run.result` inside its `try` block while its
generic `except` sets `run.error` alone, so a provider raising an exception that
`ProviderAgent` does not translate to `ProviderError` leaves `run.result is None` with a
populated `run.error`. That produced `success=False, error=None`: a failed run with the
reason silently dropped — worse than the behaviour it replaced, and reachable with a
one-line provider double.

**Fix.** The reason comes from `run.error`, which the executor sets on every failure path;
`run.result` supplies success and output. Events are consulted only for the human-readable
output. `tests/test_dispatcher.py::FailedRunReportsItsReasonTests` asserts the observable
contract — a failed run names a reason and a successful run reports none — and it fails
against the intermediate version of this code.

**`provider_used` and `model_used` are reported (fixed).** They used to be always `""`:
`ForgeApiService` created its `CapabilityFabric` **after** resolving the runtime, so the
orchestrator received `fabric=None`, the dispatcher's `record_usage` never fired, and the
response read an empty `attempt_records` list. `CostDashboardService` reads the same
object, so the Dashboard reported no runs at all.

The fix is in `app/api/service.py` alone: the fabric is created first and passed to
`create_runtime(fabric=...)`, and `bind_fabric_to_runtime` attaches that exact object to
the orchestrator that will dispatch — which also covers a runtime supplied by the caller,
since such a runtime is built without a fabric too. `Orchestrator` keeps no
`model_registry` attribute, so that one value is read from the dispatcher being replaced.
No change was needed in `app/runtime/bootstrap.py`, which already accepted `fabric`, or in
`app/orchestrator/dispatcher.py`, which already recorded usage when given one.

**Changed observably, not silently:** `GET /api/dashboard` now aggregates real runs
instead of none. `tests/test_dashboard.py::DashboardObservesApiRunsTests` asserts the
Dashboard sees a run it previously could not, and
`tests/test_api.py::test_11_service_and_runtime_share_one_fabric` asserts the object
identity that makes it reachable.

**Status: FIXED — see the end of this section for the remaining open gap.**

**Attribution comes from the terminal result, not from the attempt list.** Taking the
provider from `attempt_records[-1]` was wrong whenever a run had no success at all: the
dispatcher tries every candidate and then returns
`TaskResult(provider=selected, ...)` naming the **preferred** provider, while the last
attempt record names the last candidate it happened to try. Measured with the mock agent
removed: `run.result.provider == 'openrouter'` but the last attempt was `'groq'`, so a
failed run was attributed to a provider the dispatcher never held responsible. On success
the two agree by construction, because the dispatcher returns immediately on the first
success and no attempt can follow a successful one. `provider_used` and `model_used` are
now read from `run.result` first, with `attempt_records` retained as the fallback for a run
whose result is unavailable
(`tests/test_api.py::test_13_a_failed_run_is_attributed_to_the_terminal_result`).

**`model_used` is a model, never a provider (fixed).** The dispatcher's exhaustion
`TaskResult` carries `provider=selected` and no `model_name`, so falling back to
`provider_used` reported `model_used == 'openrouter'` for a run in which no model produced
anything — a value a caller reads as a model and uses to attribute cost. The fallback order
is now: the terminal result's real model name; then the last recorded attempt's real model
name; then `""`, because an unknown model is reported as unknown rather than invented.
`tests/test_api.py::test_14_a_failed_run_never_reports_a_provider_as_its_model` asserts
that on a total failure `model_used` is not the provider name.

**A `FAILED` run always carries a reason.** `RunExecutor` assigns `RunState.FAILED` in
exactly two places, and each *sets* `run.error` — but in the dispatch branch the assignment
comes **after** the state assignment, not before it
(`app/orchestrator/run.py`: `run.state = RunState.FAILED` then
`run.error = RunError("TaskExecutionError", message)`). The substantive conclusion holds:
the state implies `run.error` is set. The reachable shape is an exception escaping before
`run.result` is assigned — the context assembler runs there — which leaves
`run.result is None` with a populated `run.error`; the API reports that reason. No
defensive branch was added, because no path produces `FAILED` without one.

**An approval wait is reported as `success=False, error=None`.** The declared-verification
path asserts `success=False` with `error == "approval_waiting"` alongside
`state == "WAITING_FOR_APPROVAL"` (`tests/test_api_declared_execution.py`), so "not
completed" is the intended meaning of `success` there. Within `run_task` the shape is
**weaker than that**, and this section previously claimed otherwise. On the host path the
precise reasons (`approval_policy_required`, `run_scope_required`, `workspace_root_required`,
and the per-request denials) are written **only into events**
(`RunExecutor._execute_host_requests`); the `Run` itself carries the terminal
`RunError("TaskExecutionError", result.error or "Task execution failed")`, so
`TaskRunResponse.error` reads `"Task execution failed"` rather than the descriptive reason.
On an approval wait the run ends in `RunState.WAITING_FOR_APPROVAL` with `run.error = None`,
so the response is `success=False, error=None` and `state="WAITING_FOR_APPROVAL"`: the
**state** is what distinguishes "waiting for approval" from "failed", not `error`. The tool
path — whose response would also be `success=False, error=None` — remains **not reachable
through the API in the shipped configuration**: `_effective_tool_ids()` is empty unless the
service is constructed with `tool_intents`, so no tool can be invoked. Carrying the
descriptive reason into `TaskRunResponse.error` would change the API contract and is an
owner decision, not a documentation fix; this file now describes what the code does.

**Status: the two extraction defects are FIXED; the reason-carrying gap above is OPEN and
documented.** Both attribution fields are populated, and the fact that the successful
provider is the last recorded attempt is asserted rather than assumed
(`tests/test_dispatcher.py::AttemptAccountingTests`).

### 4.14 A TEST runtime cannot reach a live provider (implemented, structural)

**Defect.** Safety in the test suite depended on every test remembering to substitute
an offline runtime. `ForgeApiService()` builds its runtime through
`app.runtime.bootstrap.create_runtime`, which with no arguments calls
`load_settings()` and therefore reads the developer's real, gitignored `.env`. The
shipped `.env.example` enables `mock,openrouter`, a developer's `.env` contains nine
live provider key names, and `create_runtime` enables every provider named in
`enabled_providers`. One forgotten substitution therefore reaches the network and
spends real credits. It was measured, not feared: `tests/test_desktop.py::test_04`
reached OpenRouter and returned 586 real tokens.

**Architectural guarantee.** `create_runtime` now has an explicit TEST-runtime concept,
built on the switch that already existed rather than on a second one:

* `create_runtime(..., test_mode=True)`, **or** `FORGE_ENV=test` in the environment,
  makes the composition a TEST runtime. `ApplicationEnvironment.TEST` already existed and
  `FORGE_ENV` already selected it; the member was simply inert. Neither signal is the
  default: with no `FORGE_ENV` the configuration is `development`, and `test_mode`
  defaults to `False`, so production composition is byte-for-byte unchanged
  (`tests/test_offline_runtime_guarantee.py::test_01c_production_composition_is_unchanged`
  asserts the full nine-provider registry is still built).
* A TEST runtime **always** narrows `RuntimeSettings`: `default_provider` becomes the
  offline provider, `enabled_providers` becomes `(OFFLINE_PROVIDER_NAME,)`,
  `provider_fallback_chain` is emptied, and `allow_paid_providers` is forced off. This
  applies to caller-supplied settings too, so a caller cannot hand in settings that
  re-enable a live provider in test mode.
* On the **ambient** path — no `settings` argument, so the values come from the process
  environment and `.env` — a TEST runtime additionally constructs **only** the providers
  it enables. A live provider then has no agent, no capability metadata, no registry
  entry, and therefore no transport, so it is unreachable by automatic routing, by a
  configured fallback chain, and by an explicit `provider_name` request alike.

**Why the config flag alone is not enough, and what is not bypassed.** Switching off a
live provider's `enabled` flag is *not* a sufficient bound, and the suite says so
explicitly: `tests/test_dispatcher.py::test_explicit_provider_keeps_priority_over_auto_availability`
pins an explicitly named provider as reaching execution **despite** being disabled by
configuration. Configuring it off therefore still leaves a reachable transport; not
registering it is what removes reachability. An intermediate revision of this pass added
a dispatcher guard that refused an explicitly named disabled provider; that guard was
**reverted** because it changed a behaviour another test deliberately pins, and the
enforcement was moved into composition instead, where it removes the transport rather
than re-deciding an existing contract. No authorization was touched: `RunScope`,
`ApprovalPolicy`, and `ExecutionCoordinator` are unaffected, and no second execution
plane or parallel authorization was created.

**A caller that supplies its own `settings` keeps the historical registry shape** (every
provider constructed, only the enabled ones switched on). That is a deliberate
compatibility boundary, not a gap: `tests/test_runtime.py` pins that shape, and such a
caller has stated the runtime's shape explicitly while the narrowing has still removed
every live provider from `enabled_providers`.

**Enforcement, exactly.** `app/runtime/bootstrap.py`:
`_test_mode_requested()` resolves the signal, `_offline_only_settings()` is the
narrowing, and the `provider_names_to_register` expression is the ambient registration
filter. `OFFLINE_PROVIDER_NAME = "mock"` in `app/config/settings.py` is the single
spelling of "offline". `tests/offline_runtime.py` is the fixture that opts a test into
the guarantee; it now passes `test_mode=True` explicitly, so the guarantee is a property
of the runtime and not of the settings that fixture happens to build.

**Verified by observation, not by the presence of a class:**
`tests/test_offline_runtime_guarantee.py` (10 tests) asserts with artificial fake key
values in the environment that a TEST runtime reports only the offline provider, that a
live provider is absent from the registry and from capabilities, that an explicit request
for one **fails without falling back** (a fallback would let a test pass while proving
nothing about the provider it named), and that a representative workflow makes **zero
outbound connection attempts**. The network assertion intercepts the real
`socket.socket.connect`, `socket.socket.connect_ex`, `socket.create_connection`, and
`socket.getaddrinfo` seams, always restoring them in `finally`, and every such test also
proves the interception observed a real `connect()` call so that a broken spy cannot make
the assertion vacuous. Secrets are asserted to be absent from every captured log record,
rendered with the application's own formatter and including record arguments.

**Residual risk (OPEN).** The guarantee applies when a TEST runtime is requested. A test
that calls `create_runtime()` with neither `test_mode=True` nor `FORGE_ENV=test` still
reads the ambient `.env` exactly as before; `FORGE_ENV=test` is therefore the *ambient*
interlock and is the recommended way to run the whole suite, but nothing in the repository
sets it for you and no test enforces that it is set. Some tests legitimately exercise
production composition and pass explicit settings, so a blanket default of "test" would
have been wrong; the honest statement is that the guarantee is structural **per
composition** and opt-in **per run**.

### 4.15 Declared verification makes no generative dispatch (fixed)

**Defect.** `ForgeApiService.run_declared_verification` dispatched a generative task with
the description `"Declared verification execution."` and then discarded its entire output
(`run.execution_results[0]` is the host result; the dispatch output is never read). One
workflow therefore made **two** provider calls, measured independently twice. The cost was
the smaller half of the defect: `RunExecutor` reached its host-execution branch only when
that dispatch succeeded, so when no provider could answer, the operator's
already-authorized declared command **silently did not run** and the run reported
`"no declared execution was requested"`. It is pre-existing behaviour of that entry point,
not introduced by the generation slice.

**Fix.** The two jobs are separated. `RunExecutor.execute(..., generate_decision=False)`
means "the decision already exists outside this run": no generative dispatch is made, the
run records one `DECISION_MADE` event marked
`decision_source="declared_execution"`, `decision_provider=None`, `decision_model=None`,
`generative_dispatch=False`, and the host-execution branch is reached directly.
`run_declared_verification` passes `generate_decision=False`; the default is `True`, so
`run_task`, the acceptance loop, and every other caller are unchanged.

**What is not bypassed.** The frozen `RunScope`, the mandatory `ApprovalPolicy`, the
`ExecutionCoordinator` (still the only component that may mint an `AuthorizedExecution`),
and the local adapter all still run, and the declared command's real exit code is still
the only thing that decides the reported state. A positive control asserts a non-zero exit
is still reported as a failure, a missing approval policy still refuses, and a policy
requiring approval still stops the command.

**A latent persistence defect found and fixed alongside it.** `RunExecutor.execute` called
`_record` without `run_store` for `RUN_STARTED` and `CONTEXT_ASSEMBLED`, and
`_record_context_assembled` had no way to receive it. Those two events therefore never
reached the durable log, and the store object was swallowed into the event's own metadata
under a `run_store` key. A post-mortem could not see that a run had started. All three
call sites now thread `run_store`.

**Verified.** `tests/test_declared_verification_dispatch.py` (7 tests) counts genuine
`Provider.generate` invocations through a transport injected at the existing provider seam
and independently counts dispatch calls through a counting orchestrator: one declared
verification makes **zero** generative decisions, the model's text never becomes the run
output, and no `PROVIDER_SELECTED`/`PROVIDER_ATTEMPT` event is recorded. Fails-before /
passes-after was proved by patching the fix out of `app/orchestrator/run.py` in place,
running the single test (it failed with
`AssertionError: 1 != 0 ... ['Declared verification execution.']`), restoring the file from
a byte-exact copy held outside the repository, and confirming SHA-256
`61f543ab166afb8cc58e961d34e1ea03e488579ff84da30b9ee16847c8cbfb1d` before and after.

### 4.16 Reconciling the test counts: 2110, 1948/1954, and 2206

Three different numbers have been quoted for "how many tests this repository has". They
are not three measurements of one quantity; they are one wrong figure, one partial figure,
and one complete figure.

**Test methods declared = test methods the loader collects = 2206.** An AST sweep of
every `tests/*.py` file and `unittest.TestLoader().discover(...)` agree exactly, module by
module: every `def test*` in this suite lives in a `unittest.TestCase` subclass, there are
no module-level `test*` functions, no `load_tests` protocol functions, and no import
failures. Number of files and number of tests are different quantities: 116 modules match
`test*.py` and carry 2206 methods between them, so "112 files / 2110 tests" cannot be
checked by comparing file counts alone.

**`Ran 1971 tests` is what one process reports, and it is not the number of tests that
exist.** Measured in a single process on this tree, without a live PostgreSQL:
`Ran 1971 tests ... OK (skipped=36)`. The accounting is:

| Quantity | Count |
| --- | --- |
| Test methods collected by the loader | 2206 |
| `Ran N tests` reported by the runner | 1971 |
| of which passed | 1967 |
| of which skipped, as individual test methods actually reached | 4 |
| of which class-level `setUpClass` skips the runner counts but neither executes nor names | 32 |
| **collected but never reported at all** | **235** |
| failed / errored | 0 |

The arithmetic has to be stated carefully, because the runner's total double-counts the
class-level skips: `1967 passed + 4 real test skips + 235 never reported = 2206
collected`, while `1971 = 1967 + 4 + 32` and the 32 class-level entries are *markers* for
classes whose methods are among the 235, not additional tests. A reader who adds
`1971 + 235` will therefore get 2206 **by coincidence**, not by a sound identity; the
sound identity is the first one.

**Why 235 collected methods vanish.** The database-backed Platform tests inherit
`setUpClass` from a shared base whose first action is `raise unittest.SkipTest(...)` when
`asyncpg` is missing or no live server answers. When a class-level fixture raises
`SkipTest`, `unittest.suite.TestSuite._handleClassSetUp` records the class-level skip and
causes the whole class suite to be discarded **without calling `addSkip` for the individual
test methods**. The summary line still counts the class-level entry (`testsRun` is
incremented for it), which is exactly why the two numbers can be 235 apart while the run
reports no failure. The 235 are:

| Module | Silent methods |
| --- | --- |
| `test_platform_persistence.py` | 107 |
| `test_platform_application_discovery.py` | 34 |
| `test_platform_postgres_schema.py` | 59 |
| `test_platform_postgres_security.py` | 35 |
| **total** | **235** |

The nine methods in those four modules that *do* run are the ones belonging to plain
`unittest.TestCase` subclasses (`NoSubjectParameterTests`, `SchemaArtefactTests`), which do
not inherit the skipping fixture. Exactly **4** test methods were reached and skipped, with
these reasons: `FORGE_ALLOW_REAL_PROVIDER_GENERATION` unset
(`test_project_generation_real_provider.py`), `OPENAI_API_KEY` unset
(`test_agent_harness_ai_integration.py`), and two filesystem-symlink-unavailable skips
(`test_execution_secret_staging.py`, `test_workspace_boundary.py`). Nothing failed and
nothing was silently converted into a pass: a collected-but-unreported test is a **gap**,
and it is reported here as one. The 235 silent methods are not executed, not skipped-in-a-
reported-way, and emphatically **not** passes.

**The 1948 figure was a per-file sweep that excluded `test_desktop.py`** (measured as
`Ran 1954` in this pass, before the two new test modules), and it is the same *kind* of
number as 1971: a runner total, not a declared-test total. It is not an inconsistency with
1954/1971 — it is a different, narrower command.

**The 2110 figure was wrong for this tree.** It is recorded in §4.11 as a
`python -m unittest discover -s tests` observation with a live PostgreSQL. It is not
reproducible on `d1c007a` plus this working tree: the tree now declares 2206 methods and
reports 1971 in one process. Two independent facts explain the gap without appeal to a
mystery: (a) a live DSN turns the 32 class-level skips into real executions, which changes
the reported total but can never make it exceed the number of methods that exist; and (b)
the working tree contains 11 untracked test/helper files and 79 test methods added after
that figure was taken (`test_platform_application_discovery.py` alone contributes 37, and
it was added by `d1c007a`, the current HEAD). The correct statement is: **2110 was a
dated observation of a different tree, not a measurement of this one, and it must not be
quoted as current.** The figures in this file that are measured on `d1c007a` plus the
current working tree are 2206 declared, 1971 reported, 1967 passed, 36 skipped, 0 failed,
235 collected-but-unreported. The measured command and result were:

```
python -X utf8 -u -m unittest discover -s tests -t tests -v
Ran 1971 tests in <elapsed>s
OK (skipped=36)
```

and, from an instrumented `unittest.TextTestResult` at verbosity 0 that reports every
outcome individually (because the default verbosity loses the summary to §4.11's teardown
abort): `collected=2206`, `passed=1967`, `skipped=36` (of which 32 are class-level
entries), `never_reported=235`, `failed=0`, `errors=0`, and the same command's Python exit
code `0`.

**Environment limitation behind the 235 (proven, not assumed).** The Platform database
contract tests need PostgreSQL semantics — row-level security, `FORCE ROW LEVEL
SECURITY`, `current_setting`, partial unique indexes, composite foreign keys — and
deliberately do not simulate them in SQLite; without `FORGE_PLATFORM_TEST_DSN` they skip.
The environment this pass ran in has no server on the fixture's default
`127.0.0.1:5432` (`postgres_fixture.live_postgres()` returned `None`), and the live
PostgreSQL a developer reported on `127.0.0.1:5433` resets the handshake
(`asyncpg.connect()` raises `ConnectionResetError [WinError 64]` — the port accepts TCP
and the protocol then fails). No PostgreSQL or system configuration was changed to work
around this, and **the 235 methods are not reported as passing**: they were never
executed.

### 4.17 Residual: the provider health monitor still dials live endpoints in tests (fixed; see §4.19)

**Status update.** This item was fixed. The measurement, the attribution, and the
reason it was recorded rather than silently dropped are kept below as the
historical record; the behaviour they describe no longer exists. The fix, the
explicit "not checked" verdict, the compatibility boundary, and the verification
are in §4.19.

The §4.14 guarantee removes every path from a **decision** to a live provider. It does not
remove one independent path, and this section records it rather than implying it away.

`app/dashboard/provider_health.py` probes provider reachability by opening **raw TCP
connections** (`_check_deepseek` → `api.deepseek.com:443`, `_check_openrouter` →
`openrouter.ai:443`). `ForgeApiService.get_system_status`, `get_dashboard_report`, and
`refresh_dashboard` all run that monitor, so those calls happen during tests whenever the
service or the dashboard is composed without replacing the monitor — including with the
offline runtime, because the monitor is not part of the provider registry.

**Measured, not inferred.** During a full single-process suite run with the real
`socket.create_connection` seam intercepted, **22 outbound attempts** were observed and
attributed by stack frame: 11 to `api.deepseek.com:443` and 11 to `openrouter.ai:443`,
from five test methods — `test_api.py::test_02_system_status`,
`test_api.py::test_08_no_secret_exposure`,
`test_dashboard.py::test_01_a_run_reaches_the_dashboard_with_its_attempts`,
`test_dashboard.py::test_18_dashboard_service_and_json_export`, and
`test_dashboard.py::test_21_one_run_id_across_fallback_and_22_artifact_linkage`. The
suite still passes because these connections fail and the monitor reports the provider as
unavailable; but the attempts are real network egress from a test run.

**Severity, stated honestly.** These are unauthenticated TCP reachability probes: they
send no credential, commit no prompt, and cannot spend tokens. That is why this is a
residual risk and not a repeat of the §4.14 defect. It is nevertheless **outbound network
access from the test suite**, so the correct claim is narrow: *a test-mode runtime makes
no provider decision call*, not *the suite opens no socket*. `tests/test_offline_runtime_guarantee.py`
proves the former; it deliberately does not claim the latter, and its spy installs its own
service and runtime, so it is not measuring the health monitor.

**Not fixed here, deliberately.** Making the monitor stop probing disabled providers, or
making the test suite replace it by default, changes either a production API contract or
the composition of tests that are legitimately exercising the dashboard. Both are owner
decisions, so neither was taken.

### 4.18 A persisted run record is classified by what it proves; "in progress" is not trusted

**What the record is and is not.** `RunStore` writes two files per run:
`<root>/<run_id>/events.jsonl` and `<root>/<run_id>/state.json`. The snapshot
(`RunStateSnapshot`) carries `run_id`, `task_id`, `state`, `status`,
`attempt_number`, `completed_attempt_indexes`, `project_state`, `accounting`,
`event_count`, `updated_at`, and `interrupted`. It does **not** carry a heartbeat,
an owner or process identity, a lease, an expiry, a start or finish timestamp, or a
terminal marker. A snapshot therefore proves only what its writer observed **at the
moment of the write**. That is the whole of the evidence available on disk.

**The defect.** A record claiming `state='RUNNING'` was previously read back
verbatim as `RUNNING`, with nothing distinguishing a writer that is still
executing from one whose process died. Both cases produce byte-identical files, so
the reader was told a definite state when the honest answer is "the record cannot
establish this". Measured on an isolated `RunStore(root=<temp>)`: the record is
written and read back faithfully (`state='RUNNING'`, `status='RUNNING'`,
`interrupted=False`, identical from a fresh store instance), and before this change
the store offered **no** way to distinguish a live writer from a dead one — the
write path is faithful, so this is a read-side gap, not a write-path bug.
Separately, there is no `CANCELLED` or `TIMEOUT` member of `RunState`; those exist
only as `ExecutionOutcomeStatus` labels (`EXECUTION_TIMEOUT`, `POLICY_DENIED`, …),
so a reader had no vocabulary for expressing them either.

**The fix.** `app/runtime/run_store.py` adds a classification derived **at read
time** from the record: `ExecutionClassification` (a `str` enum),
`RunRecordClassification` (outcome + reason code + the exact field/value provenance
+ evidence list), and `RunStore.classify_record(run_id)`. Values and their exact
conditions:

| Outcome | Condition |
| --- | --- |
| `TERMINAL_SUCCESS` | a terminal success label (`COMPLETED`/`SUCCESS`) on either field |
| `TERMINAL_FAILURE` | a terminal failure label (`FAILED`, `FAILURE`, `EXECUTION_FAILURE`, `LIMIT_REACHED`, `ERROR`, `EXECUTION_ERROR`) |
| `TERMINAL_TIMEOUT` | an explicit timeout label (`TIMEOUT`, `EXECUTION_TIMEOUT`) |
| `TERMINAL_CANCELLED` | an explicit cancellation label (`CANCELLED`, `CANCELED`) |
| `TERMINAL_REFUSED` | a refusal label (`DENIED`, `POLICY_DENIED`, `PERMISSION_DENIED`, `APPROVAL_REJECTED`) |
| `WAITING` | a waiting label (`WAITING`, `WAITING_FOR_APPROVAL`, `APPROVAL_WAITING`) |
| `INDETERMINATE` | `interrupted` is set, or a field claims progress (`CREATED`, `RUNNING`, `REVISING`, `INITIAL`) with no terminal label |
| `UNKNOWN` | no recognised label, both labelled fields absent, two **opposite-polarity** terminal labels, or no snapshot and no terminal event |

Two labelled fields that disagree are resolved by polarity first: "succeeded"
beside "failed" is a contradiction and yields `UNKNOWN`, never success. Within one
polarity the more specific label wins, so `state='FAILED'` with
`status='EXECUTION_TIMEOUT'` reports `TERMINAL_TIMEOUT` and keeps the reason rather
than discarding it. An unrecognised value is only ever neutral: it can neither
promote a record to success nor demote a real terminal outcome. When no snapshot
exists, the event trail is consulted and the **last** terminal event
(`ENGINEERING_RUN_COMPLETED`/`RUN_COMPLETED`/`HARNESS_COMPLETED` versus
`ENGINEERING_RUN_FAILED`/`RUN_FAILED`/`HARNESS_FAILED`/`HARNESS_LIMIT_REACHED`)
decides; with neither, the result is `UNKNOWN`. `RunStore.load` still returns `None`
for a run id that was never persisted, while `classify_record` reports `UNKNOWN`
for it: nothing was recorded, so nothing is proven.

**Why a claimed-in-progress record is not automatically trusted.** No field of the
record can establish liveness. Time-since-`updated_at` was deliberately **not** used
as a proxy: that would invent a lease and an expiry the on-disk format does not
have, and would silently reclassify a slow but live run as dead. A refusal is never
a success, an unknown is never a success, and a contradiction is never a success —
`RunRecordClassification.is_success` is true only for `TERMINAL_SUCCESS`.

**The compatibility boundary, deliberate.** `RunState` is **not** extended:
`CANCELLED` and `TIMEOUT` were not added as members. `RunState` is the live
in-memory lifecycle and is compared by identity across the code base —
`run.state == RunState.COMPLETED` decides success in `app/api/service.py`, and
`app/orchestrator/run.py`, `app/orchestrator/revision.py`, and
`app/orchestrator/engineering.py` branch on it — so a new member would silently
create a third, unhandled live state in every one of those comparisons. The
classification is a separate read-side vocabulary instead, and no code path
contains a value of it. `read_state`, `load`, `accounting_summary`, and `summary()`
are unchanged: `summary()` returns exactly its previous key set and the
classification was not grafted onto it. Classification is computed from fields
already written by every earlier version, so **no existing `state.json` is
migrated, rewritten, or read differently**, and no second store or database was
added. Unknown usage is still unknown: an absent accounting block reads as `None`,
never as zero tokens or zero cost.

**Verified.** `tests/test_execution_record_classification.py`, 26 tests, every one
on its own `RunStore(root=<temp dir>)` — a terminal success, a terminal failure, a
waiting record (reported as waiting, not failure and not success), an explicitly
interrupted record, a claimed-in-progress record reported as indeterminate **with
the reason named from the record** (two different claims give two different
details), refusals never reported as success, unknown/absent fields reported as
unknown, the same-shape compatibility assertions for `summary()` and
`accounting_summary()`, and a hand-written old-shape `state.json` fixture (no
`interrupted`, no `project_state`) that still loads, classifies, and sums. It also
asserts `RunState` still has exactly its six members. Fails-before / passes-after
was proved by patching `app/runtime/run_store.py` **in place** with the pre-fix
behaviour (an in-progress claim passed through as a terminal success), running that
single file (**`Ran 26 tests`, `FAILED (failures=8)`**, including
`test_a_record_claiming_progress_is_indeterminate_not_running` and
`test_terminal_failure_record_reads_back_as_terminal_failure`), restoring the file
from a byte-exact in-memory copy in a `finally` block with **binary**
`read_bytes`/`write_bytes`, and confirming the same SHA-256
`74d882dc55306d5131303af127e0d8fa3dc2f2133de09acaf83b09c7a80acfaf` before and
after, after which the same file reported `Ran 26 tests`, `OK`.

**Not changed.** Nothing about execution, approval, resumption, or accounting
recomputation changed; classification reads files and writes none (asserted by a
test that classifies three times and compares the bytes on disk).


### 4.19 The provider health monitor makes no outbound attempt in test mode

**What changed.** The 22 outbound attempts recorded in §4.17 are removed by
gating the monitor's reachability probes on the composition's own settings.
`app/dashboard/provider_health.py` now accepts an optional `settings`
(`ProviderHealthMonitor(..., settings=...)`); with no argument it resolves the
same `RuntimeSettings` a bare `create_runtime()` resolves, through the same
`load_settings()`. Probes are suppressed when **either** holds:

* `settings.environment is ApplicationEnvironment.TEST` — the pre-existing
  `FORGE_ENV=test` switch, the same one `create_runtime` uses. No second switch
  was invented.
* no enabled provider other than `OFFLINE_PROVIDER_NAME` (`"mock"`) — the
  *resolved* form of the same fact, because in test mode `create_runtime`
  narrows `enabled_providers` to the offline provider alone.

**What an unchecked provider reports.** A new `ProviderStatus.NOT_CHECKED`
(`app/dashboard/models.py`) is returned for every provider the monitor did not
probe: `balance_display="NOT CHECKED"`, `balance_numeric=None`,
`alert=AlertLevel.INFO`, `last_checked=""`, and
`details={"reason": "test_mode_no_network_probe", "environment": ...}`. It never
reports `READY`, `READY_GATEWAY`, `FREE_TIER`, `NO_CREDITS`, `NETWORK_BLOCKED`,
`AUTH_FAILED`, or `UNAVAILABLE`, because none of those was observed.
`last_checked` is left **empty**, not filled with "now": the monitor has no
observation time for a check that did not happen.

**Compatibility boundary.** `NOT_CHECKED` is a new *value* of the existing
`status` key, not a new key. `ProviderAccountStatus.to_dict()` and
`DashboardReport.to_dict()` keep exactly their previous key sets, and
`ProviderStatus` is a `str` enum, so a consumer that switches on `.value`,
compares against a string, or simply renders it keeps working. A consumer that
assumed the previous eight members were exhaustive now sees a ninth; that is the
intended, documented widening, and it is the only way to avoid fabricating a
status. `checks_enabled` is exposed on the monitor so a caller can ask the
question directly.

**Production is unchanged.** A default `RuntimeSettings()` has
`environment=development` and an enabled set containing a live provider, so both
suppression checks are `False` and the real probing runs exactly as before: the
same seven providers, the same `NETWORK_BLOCKED` / `UNKNOWN` / `WARNING` verdict
for a refused connection, the same exception name in `details`, and a real
`last_checked`. The production path was not weakened to quiet the tests, and no
test-only monkeypatch of Forge's own code is involved: the gate reads
configuration, and the tests only install a spy on the process's `socket` module.

**Verified.** `tests/test_dashboard_offline_health.py`, 7 tests, `OK`. Under one
blocking socket spy — installed on `socket.socket.connect`, `connect_ex`,
`socket.create_connection`, and `socket.getaddrinfo`, restored in a `finally`,
with `getaddrinfo` returning a documentation-range address (`203.0.113.1`) so
even DNS cannot leave the machine — a test-mode monitor run records **zero**
outbound attempts while the same spy records the pre-fix
`api.deepseek.com` / `openrouter.ai` attempts. Every test asserts the spy was
armed by making one deliberate local connect attempt, so a zero cannot come from
a broken interception. Measured before the fix, under that same spy and
`FORGE_ENV=test`: `CostDashboardService().get_report()` made 2 outbound connect
attempts; after the fix, 0. On the five defect methods of §4.17
(`test_api.py::test_02`, `test_api.py::test_08`,
`test_dashboard.py::test_01`, `::test_18`, `::test_21`), measured with
`FORGE_ENV=test`: 0 outbound attempts, 0 failures. Fails-before / passes-after
was proved by writing the pre-fix `provider_health.py` back **in place** with
**binary** `write_bytes`, running the regression method
(`test_06_regression_env_selected_test_mode_makes_zero_outbound_attempts`), then
restoring from a base64 backup in a `finally`:

| state | `provider_health.py` SHA-256 | single method | attempts |
| --- | --- | --- | --- |
| pre-fix (written in place) | `922047a2…f6d4` | exit 1, `AssertionError: Lists differ: [('create_connection', 'api.deepseek.com')] != []`; verdict `<ProviderStatus.NETWORK_BLOCKED>` | 2 |
| post-fix (restored) | `7bcb55de…f764` | exit 0, `Ran 1 test`, `OK` | 0 |

The restore SHA-256 `7bcb55dee65c91c8163e5af21663072ff7128084651aefea53ae210d06d7f764`
is identical before the patch and after the restore, and the whole file then
reported `Ran 7 tests`, `OK`.

**Honest boundary.** The default resolution reads `settings` from the process
environment, so a test runner must select test mode (`FORGE_ENV=test`) — or pass
`settings=` — for the suppression to apply. Without that selection a bare
construction is a production construction and still probes, which is exactly why
production behaviour is unchanged. `tests/offline_runtime.py` does **not** set
`FORGE_ENV`, so it narrows the *runtime's* `enabled_providers` but not the
environment of a monitor the service built before the runtime existed; the five
methods above are network-free under `FORGE_ENV=test` because the service's
monitor resolves the environment, not because `offline_runtime()` sets it.

**Not changed.** No provider decision, dispatch, fallback, accounting, or
execution path changed. `app/dashboard/**` and this file are the whole of it;
`app/api/service.py`, `app/runtime/bootstrap.py`, and `app/config/settings.py`
were not modified.

### 4.20 Write mode applies a model answer, but only a live provider can produce real code

**Status: IMPLEMENTED / VERIFIED with a local fake; the live-provider half is not
verified here.** `app/agent_runtime/code_writer.py`, `tests/test_code_writer.py`
(23 tests). `POST /api/tasks/run` with `write_mode: true` and `category: "code"`
passes the provider's answer through the guarded writer, which parses a fenced
block with a path header or a unified diff, validates every path against the run's
frozen workspace boundary and a deny list, writes atomically with backups,
compiles every touched `.py`, and rolls the whole run back on a compile failure.
Measured on a new in-process service over a throwaway workspace: two files created,
the real diff returned and persisted under `project_state["code_write"]`, and
`py_compile` passing; a second answer naming `../escape.txt` was refused with
`rejected` and wrote nothing.

**Honest boundary.** No live provider is enabled in this environment, so the
provider branch is exercised through a local fake at the executor seam and the
offline provider's write mode honestly reports `no_changes` (its deterministic
plan text names no file). End-to-end writing of real project code therefore still
needs an enabled provider whose answer uses a supported shape. Also unverified by
design: an answer that patches a file it never saw is refused
(`unified_diff_not_applicable:diff_context_mismatch`) because only file *names*,
not contents, are sent; deletions are refused; and write mode has no separate
approval gate (an open owner decision).

### 3.8 Project materialization, local run and preview

| Area | Status | Evidence |
| --- | --- | --- |
| Materialization into the project's own workspace | **CURRENT / VERIFIED** | `app/projects/materialize.py` (calls `app/agent_runtime/code_writer.apply_changes`; the writer's boundary and deny list decide) — `tests/test_materialization.py` |
| Entry point from the artifact manifest only | **CURRENT / VERIFIED** | `app/projects/entrypoint.py` — `tests/test_materialization.py`, `tests/test_local_execution.py` |
| Runnable kinds (python script/module, node script, declared npm script) | **CURRENT / VERIFIED** | `app/projects/entrypoint.py` — `tests/test_local_execution.py`; `app/smoke_python_preview.py`, `app/smoke_node_preview.py` |
| A static page is runnable (`runnable: true`, `run_command` = `открыть в браузере (preview)`) through the existing preview route | **CURRENT / VERIFIED** | `app/projects/entrypoint.py` (`STATIC_RUN_COMMAND`, `runnable`), `app/projects/presentation.py` (both launch actions offered), `ui/src/screens/Projects.tsx` (the run panel's launch calls the preview route) — `tests/test_materialization.py`, `tests/test_project_truth_presentation.py`; `tsc --noEmit`, `vite build` |
| Effective write mode in the run record (`project_state["write_mode"]`) and no template for a real provider | **CURRENT / VERIFIED** | `app/api/service.py` (`WRITE_MODE_PROJECT_STATE_KEY`, `NO_FILES_FROM_MODEL_SENTENCE`), `app/projects/mock_answer.py` (`deterministic_answer_for_provider`) — `tests/test_write_output_contract.py`, `tests/test_projects_change_run.py` |
| Approval without dispatch (`ExecutionCoordinator.authorize`) | **CURRENT / VERIFIED** | `app/execution/authorizer.py` — `tests/test_execution_authorization_hardening*.py`, `tests/test_local_execution.py` |
| Supervised local process (states, tree stop, timeout, bounded redacted log) | **CURRENT / VERIFIED** | `app/projects/runner.py` — `tests/test_local_execution.py`, `tests/test_run_sandbox.py` |
| Job-object containment of a project process (kill-on-close, memory cap, active-process cap) | **CURRENT / VERIFIED** | `app/execution/containment.py`, `app/execution/adapter.py` (`set_process_tree_job_limits`), `app/projects/runner.py` — `tests/test_run_sandbox.py` |
| Low-integrity token for a project process, with the measured level reported | **CURRENT / VERIFIED** | `app/execution/containment.py` (`lower_process_integrity`, `process_integrity_level`) — `tests/test_run_sandbox.py`; the payload says `restricted_token`/`integrity` and `detail.integrity_level` |
| Measured write boundary of a project process (`writes_outside`) | **CURRENT / VERIFIED (measured, not guaranteed)** | `app/execution/containment.py` (`probe_write_boundary`) — `tests/test_run_sandbox.py`; it is `allowed` when Forge's own data carries the same integrity label as the child |
| Configured maximum lifetime for a serving process | **CURRENT / VERIFIED** | `LocalRunner(server_lifetime=…)` via `FORGE_SANDBOX_SERVER_LIFETIME_S`, default none — `tests/test_run_sandbox.py` |
| Shared process-tree kill (job object / Toolhelp / `taskkill /T`) | **CURRENT / VERIFIED** | `app/execution/adapter.py` (`terminate_process_tree`) — `tests/test_execution_timeout_and_tree_kill.py`, `tests/test_local_execution.py` |
| Process preview for a running server | **CURRENT / VERIFIED** | `app/projects/preview.py` (`register_process`, observed listening port) — `tests/test_local_execution.py`, `tests/test_materialization.py` |
| Local run / preview routes | **CURRENT / VERIFIED** | `app/api/project_routes.py` — `tests/test_local_execution.py` |
| Launch of a project into its own window (`POST /api/projects/{id}/launch`, observed port only, no console window) | **CURRENT / VERIFIED** | `app/projects/launcher.py`, `app/projects/app_window.py`, `app/api/project_routes.py`, `app/projects/runner.py` (`CREATE_NO_WINDOW`), `app/execution/containment.py` (probe) — `tests/test_project_launch.py` |
| The launch/run port is discovered from the child's own output plus its pid-tree sockets, and every candidate is proved with a real loopback `GET /`; `observed_port` is published, and a notice names the manifest's claim when it differs | **CURRENT / VERIFIED** | `app/projects/ports.py`, `app/projects/runner.py` (`_discover_port`, `_answering_output_port`, `_finish`), `app/projects/launcher.py` (`_launch_notice`), `app/api/project_routes.py` (`_manifest_port`) — `tests/test_project_launch.py` (`PortDiscoveryTests`, `test_12`, `test_13`) |
| A child that exits 0 while an address it printed already answers is a success with `reused: true` (an already-running instance), not a refusal | **CURRENT / VERIFIED** | `app/projects/runner.py` (`_finish`), `app/projects/launcher.py` — `tests/test_project_launch.py` |
| A process that never bound a port is a typed refusal with run state, exit code and redacted log tail; a static page is offered as `fallback_available`/`fallback_url` and shown only with `{"fallback": true}` | **CURRENT / VERIFIED** | `app/projects/launcher.py` (`_port_refusal`, `looks_like_console_prompt`), `app/api/project_routes.py` — `tests/test_project_launch.py` |
| Stage words `READY_TO_RUN` / `RUNNING` / `STOPPED` / `BLOCKED` | **CURRENT / VERIFIED** | `app/projects/stage.py` — `tests/test_local_execution.py`, `tests/test_materialization.py` |
| Project card run block and confirm step | **CURRENT / VERIFIED** (typechecked and built; exercised over HTTP, not by an automated UI test) | `ui/src/screens/Projects.tsx`, `ui/src/api/forge.ts` — `tsc --noEmit`, `vite build` |
| Network isolation of a project process | **REJECTED for this block** — the child inherits the machine's network; the payload says `network_inherited` | `DECISIONS.md` Local run and preview v0.2 |
| Dependency installation (`pip install` / `npm install`) as part of a run | **REJECTED** — a missing dependency is reported as `RUN_BLOCKED` with the next step | `DECISIONS.md`, `app/projects/runner.py` |

**Honest boundaries.** The supervisor states them rather than hiding them: the child is **not**
network-isolated (`network_policy: "inherited"`, `sandbox.network: "inherited (not isolated)"`), the
containment is a **Windows job object plus a low-integrity token**, not a kernel filesystem sandbox:
`writes_outside` is a *measured* answer and is honestly `allowed` when Forge's own data carries the
same integrity label as the child (as it does when Forge itself runs at low integrity), the log is a
bounded 64 KiB-per-stream capture (later output is dropped with a marker), the preview URL exists only
while the process is alive, and a process that outlives a hard process kill of the API server is not
tracked after restart (the state is in memory and starts as `NOT_RUN`).


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
> **Проверенная база.** Коммит `d1c007a` плюс незакоммиченное рабочее дерево на момент
> написания. Каждое число ниже измерено на этом дереве, в прогоне, который не
> пересекался с другим тестовым процессом; в дереве есть незакоммиченная работа, поэтому
> относитесь к этим числам как к **датированным наблюдениям** и перемеряйте их, а не
> доверяйте им.
>
> - один процесс, без живого PostgreSQL
>   (`python -X utf8 -m unittest discover -s tests -t tests -v`):
>   **собрано 2206 тестовых методов, `Ran 1971 tests ... OK (skipped=36)`** — 1967
>   пройдено, 0 падений, 0 ошибок, 36 пропусков, *о которых сообщил runner*, и **235
>   тестовых методов были собраны, но не были сообщены прогоном вообще**. Из 36
>   сообщённых пропусков 32 — это записи уровня класса `setUpClass`, которые runner
>   учитывает в своём итоге, но никогда не исполняет и не называет; реально достигнуты и
>   пропущены только **4** тестовых метода. Эти 235 молчащих методов — тесты с базой
>   данных, у которых классовый фикстур поднимает `unittest.SkipTest`; см. §4.16, где это
>   сверено с прежней цифрой `112 test files, 2110 tests` и точно объяснено, почему
>   собранный метод может исчезнуть из сводки.
> - 116 тестовых модулей в `tests/` соответствуют шаблону обнаружения `test*.py`; ещё 4
>   `.py`-файла там (`offline_runtime.py`, `persistence_fixture.py`, `postgres_fixture.py`,
>   `run_scope_support.py`) — вспомогательные и не собираются. `git ls-tree -r HEAD tests`
>   перечисляет 109 файлов; разница — 11 новых незакоммиченных тестовых/вспомогательных
>   файлов.
> - прежняя цифра `112 test files, 2110 tests, OK (skipped=3)` **на этом дереве не
>   воспроизводится**; §4.16 объясняет расхождение, а не повторяет его.
>
> Два файла, которые этот блок прежде перечислял как падающие, **исправлены** и
> проходят; дефект маршрутизации за ними зафиксирован в §4.12. Не возвращайте их
> сюда на основании более старого отчёта.

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
| Первый сквозной вертикальный workflow (`goal -> specification -> plan -> approval -> execution -> verification -> result`) | **CURRENT / VERIFIED**, с ограничениями области из §3.7 | `app/workflows/project_workflow.py` — `tests/test_project_workflow_production.py`, `tests/test_project_factory_end_to_end.py` |
| Срез Controlled Project Factory: разделённые `generation_status` / `execution_status` / `harness_status` / `verification_exit_code` / `acceptance_status`, бюджет генерации на один вызов и явное состояние «не установлено» | **CURRENT / VERIFIED**; четыре статуса — отдельные поля с закрытыми словарями значений, см. §3.5.1 | `app/workflows/project_workflow.py` — `tests/test_project_factory_vertical_slice.py` (42 теста) |
| Генерация проекта моделью: ответ модели становится проверенными файлами проекта, которые затем реально тестирует объявленная команда верификации | **CURRENT / VERIFIED как путь**; реальный вызов провайдера **НЕ ПРОВЕРЕН** — см. §3.5.2 | `app/workflows/project_generation.py`, `app/workflows/project_workflow.py` — `tests/test_project_generation_production.py`; opt-in тест реального провайдера `tests/test_project_generation_real_provider.py` (**никогда не выполнялся**) |

### 3.5 Первый сквозной вертикальный workflow

`app/workflows/project_workflow.py` собирает уже существующие компоненты в один
вертикальный срез и не добавляет ни второго оркестратора, ни второй системы
памяти, ни второго механизма авторизации, ни параллельного пути выполнения. Его
единственная точка входа — `run_project_workflow(service, registry, build_declaration_id, *, idempotency_key=None)`;
как и родственные точки входа, она принимает **принадлежащий оператору id
декларации** и не принимает от вызывающего ни команду, ни путь, ни allow-list, ни
критерий, ни ожидание.

Каждый шаг выполняет существующий компонент:

| Шаг | Кем выполняется |
| --- | --- |
| Цель пользователя -> спецификация задачи + критерии приёмки | `AcceptanceSpec`, `TaskSpecification` |
| План | `app.planning.planner.Planner` (тот же детерминированный планировщик, что использует harness) |
| Одобрение | `ApprovalPolicy` + `ApprovalResolver` внутри `ExecutionCoordinator` |
| Выполнение | `ForgeApiService.run_accepted_task` |
| Верификация (вторая объявленная команда) | `ForgeApiService.run_declared_verification` |
| Вердикт приёмки | `AcceptanceGate`, через существующий harness |
| Результат | `ProjectWorkflowResult` |

Представление спецификации (`build_specification`) доступно для чтения **до**
любого запуска: оно не выполняет ничего и ничего не пишет. Тип ответа
`ProjectWorkflowResult` несёт `run_id`, `state`, `acceptance_status`, результат
верификации, измеренные `created_files` / `deleted_files`, `error` и durable-запись
запуска. Это **новый** тип: ни одно поле `TaskRunResponse` не переопределено и ни
одно поле в него не добавлено.

Сообщаются два разных вердикта, и ни один не выдаётся за другой:

- `acceptance_status` — вердикт существующего `AcceptanceGate` по замороженным
  оператором фактам workspace. На успешном пути состояние запуска —
  `LIMIT_REACHED` (acceptance-срез ограничен двумя действиями — одно выполнение и
  его верификация — тогда как `COMPLETE_RUN` потребовал бы третьего); это
  свойство самого среза, а не этого workflow;
- `verification` — результат действительного запуска собственного набора тестов
  сгенерированного проекта как второй объявленной команды. Его
  `observed_exit_code` — реальный код возврата процесса, полученный локальным
  адаптером; `success` равно `False`, если этот код не совпадает с объявленным
  `expected_exit_code`.

**Честное ограничение — код в этом срезе не сгенерирован моделью.** Проект
записывается объявленной оператором командой `python`, а не LLM. Ни провайдер, ни
API-ключ, ни модель не участвуют. Поэтому этот срез доказывает *workflow и его
механику верификации*; он **не** доказывает ничего о качестве кода, сгенерированного
моделью. Измеренные свойства и оставшиеся ограничения:

- `created_files` / `deleted_files` — измеренная разница снапшота workspace до
  сборки и после неё, исключая каталоги `__pycache__` (интерпретатор создаёт их
  как побочный эффект импорта);
- требуемое, но не выданное одобрение оставляет запуск в `WAITING_FOR_APPROVAL`
  с `success=False`, `acceptance_status` = `not_evaluated` и **без созданных
  файлов**. На этом пути отсутствующая `approval_policy` ведёт себя не так, как в
  `run_task` / `run_declared_verification`, где отсутствие политики само
  сообщается как `approval_policy_required`;
- workspace-ограничение обеспечивается для **authority** (рабочий каталог, который
  разрешается вне замороженного корня, отклоняется `RunScope`, и процесс не
  порождается). Это **не** OS-песочница: объявленная команда всё ещё может писать
  вне workspace, и §4.4 фиксирует этот остаточный риск. Workflow это не скрывает —
  такой запуск сообщается как неуспешный, потому что требуемых фактов workspace нет;
- workflow отказывает — до заморозки любого scope — в привязке реестра, где
  объявления сборки и верификации не разделяют профиль, рабочий каталог и
  исполняемый файл оператора. Это проверка согласованности композиции, а не новый
  механизм авторизации;
- `state` ответа равно `VERIFIED` только когда действие успешно **и** объявленная
  верификация совпала с объявленным кодом возврата, **и** записан каждый файл,
  названный источником. Неуспешное действие, неуспешная верификация и частичная
  материализация никогда не сообщаются как успех.

### 3.5.1 Разделённые статусы и бюджет генерации

`ProjectWorkflowResult` несёт четыре независимых статуса стадий плюс сам код
возврата. У каждого свой закрытый словарь значений, потому что смешение любых двух
из них — это как раз то, из-за чего «модель ответила», «процесс завершился с кодом
0» и «критерии оператора выполнились» начинают принимать друг за друга. Каждое
значение выводится из наблюдённого факта, ни одно не является строкой-сводкой;
`"unknown"` — полноценное значение, означающее *факт установить не удалось*, что не
является ни отказом, ни успехом.

| Поле | Значения | Условие |
| --- | --- | --- |
| `generation_status` | `disabled`, `refused_limit`, `failed_transport`, `failed_response`, `failed_materialisation`, `succeeded`, `unknown` | `disabled` — провайдер не скомпонован; `refused_limit` — бюджет уже исчерпан, провайдера не спрашивали; `failed_transport` — диспетчеризация не вернула ответа вообще (провайдер бросил исключение либо executor не скомпонован); `failed_response` — ответ пришёл и не смог стать проектом; `failed_materialisation` — записать не удалось ничего; `succeeded` — проект записан |
| `execution_status` | `not_dispatched`, `waiting_for_approval`, `failed`, `succeeded`, `unknown` | Выводится из выполненного действия, никогда из жизненного цикла harness. `waiting_for_approval` — авторизатор отказался выполнять действие; `succeeded` — `success` запуска истинно (адаптер наблюл `EXECUTION_SUCCESS` **и** gate приёмки вернул PASS); `failed` — любое иное терминальное состояние |
| `harness_status` | собственный статус harness дословно (`COMPLETED`, `LIMIT_REACHED`, `FAILED`, `WAITING_FOR_APPROVAL`) либо `""` | Статус жизненного цикла запуска сборки, как он сообщён. Держится отдельно от `execution_status` намеренно — см. примечание ниже |
| `verification_exit_code` | `int` либо `None` | **Реальный** код возврата объявленной команды верификации, либо `None`, если результат процесса не наблюдался. `None` — это не `0` |
| `acceptance_status` | `pass`, `fail`, `not_evaluated` | Вердикт существующего `AcceptanceGate`. `not_evaluated` означает, что gate не выполнялся, и никогда не сообщается как pass |

Ещё два поля явно фиксируют честные неизвестные и происхождение:

- `generation_attempts_spent` — сколько попыток обращения к провайдеру **инициировал
  этот workflow**, либо `None`, если стадия генерации не выполнялась (вопрос
  неприменим, и это не то же самое, что `0`). Поле соотнесено с
  `max_generation_attempts`; `0` означает, что бюджет уже был исчерпан и провайдера
  намеренно не спрашивали. Это **не** сырой счётчик вызовов провайдера: слой
  решений не предоставляет запись вызовов, поэтому сырой счётчик отсюда
  ненаблюдаем, а число, которое этот модуль не может измерить, не сообщается вовсе.
  Физический счётчик вызовов измеряется в тестах — по транспорту, который
  компонует сам тест;
- `project_source` — `declared_command`, `injected_transport` или
  `model_transport`. Он называет, как пришёл **текст** проекта. Это не
  доказательство того, что ответила удалённая модель; установить это данный модуль
  не может.

**`LIMIT_REACHED` сообщается как есть, и он не означает, что действие не
выполнилось.** Измерено на обычном, полностью успешном запуске объявленной команды:
acceptance-harness завершается в `LIMIT_REACHED`, тогда как выполненное им действие
действительно вернуло код 0, собственные тесты проекта действительно прошли, а gate
приёмки действительно вернул PASS. Собственный код физической телеметрии harness
классифицирует `LIMIT_REACHED` как терминальный *успешный* статус наравне с
`COMPLETED`, то есть это задуманное поведение harness, а не регрессия. Поэтому срез
сообщает `harness_status` дословно, а `execution_status` выводит из действия, так
что ни один из этих фактов нельзя выдать за другой. `execution_status` никогда не
равен `limit_reached`.

**Бюджет генерации проверяется до вызова.** `max_generation_attempts` по умолчанию
равен `1`; значение меньше `1` отказывает, не спрашивая провайдера (поэтому
`generation_attempts_spent` равен `0`), и отказ **не** обходится переходом на
объявленную команду создания. Бюджет, не являющийся неотрицательным целым (`-1`,
`True`, `"1"`, `1.5`, `None`), вызывает `ProjectWorkflowError`, а не приводится
молча. Использование и стоимость никогда не выдумываются: когда транспорт не
сообщает usage, `GenerationEvidence.usage_present` равно `False`, а поля
токенов/стоимости — `None`, что сообщается как «не сообщено», а не как ноль.

**Вердикт verified требует полной материализации.** Когда ответ объявляет файл,
который отклоняет проверка пути, защищённой области, секретов, размера или
конфликта, уцелевшие файлы всё ещё могут быть записаны и их тесты всё ещё могут
пройти — но артефакт не тот, который описал ответ. Такой запуск сообщает
`state`/`verdict` `MATERIALISATION_INCOMPLETE`, `success=False` и
`error=materialisation_incomplete`, вместо того чтобы скрыть отказ или отбросить
прошедший прогон. Отклонённый файл никогда не считается созданным.

**Честные ограничения этого среза.** Здесь нет ни одной новой гарантии:

- **OS-песочницы нет.** Проверки — это server-side файловая граница над текстом и
  разрешёнными путями. Программу, которую ответ позднее записал, они не удерживают
  от открытия абсолютного пути во время выполнения; §4.4 фиксирует этот остаточный
  риск, и данный срез его не уменьшает. Единственное, что он *добавляет*: обход
  workspace больше не спускается сквозь NTFS-**junction** (`DirEntry.is_symlink()`
  для junction в Windows возвращает `False`), поэтому ссылка внутри workspace
  больше не может поместить в diff созданных файлов то, чего в workspace нет;
- **реальный провайдер не проверен.** Каждый транспорт генерации, задействованный в
  тестах, — это внутрипроцессная подделка, и `project_source` / `mock_transport` в
  `GenerationEvidence` это сообщают. Вердикт `REAL LLM PROJECT GENERATION VERIFIED`
  остаётся недостигнутым;
- **пробы здоровья провайдеров в dashboard не изменены.** Они по-прежнему делают
  исходящие попытки при обычном запуске (см. §4.19). Поэтому корректное утверждение
  — «test-режимный runtime не делает провайдерного *decision*-вызова», а не «набор
  тестов не открывает сокетов»;
- бюджет генерации ограничивает попытки, инициированные workflow, а не отдельные
  вызовы провайдера, которые транспорт может делать внутри (цепочка fallback —
  это собственное поведение диспетчера, и она сообщается из его собственной записи
  событий);
- `acceptance_status == "pass"` — это свидетельство только об объявленных
  оператором фактах workspace. Это не свидетельство того, что тесты проекта прошли,
  и не свидетельство корректности проекта.

### 3.5.2 Генерация проекта моделью (путь генерации реализован; реальный провайдер не проверен)

`app/workflows/project_generation.py` превращает **ответ модели** в файлы проекта на
диске, а модельный путь `run_project_workflow` встраивает это в срез выше. Он не
добавляет ни оркестратора, ни диспетчера, ни системы памяти, ни механизма
авторизации, ни параллельного пути выполнения. Он проверяет текст и записывает
файлы и не порождает собственных процессов.

Точка входа — та же функция с добавленными параметрами, задаваемыми оператором::

    run_project_workflow(
        service, registry, build_declaration_id,
        ai_provider=AIDecisionProvider(provider, mock_transport=...),
        materialise=True,                        # по умолчанию
        permitted_overwrite_paths=(),            # по умолчанию перезаписи нет вообще
        max_file_bytes=None, max_project_bytes=None, max_file_count=None,
    )

При `ai_provider=None` (значение по умолчанию) в §3.5 ничего не меняется. При
заданном провайдере файлы модели **и есть** проект, а объявленная команда создания
**не** запускается — она бы их перезаписала; объявленная команда *верификации* при
этом выполняется без изменений, поэтому собственные тесты сгенерированного проекта
действительно запускаются.

Каждый шаг выполняет существующий компонент:

| Шаг | Кем выполняется |
| --- | --- |
| Вызов модели | `RunExecutor` -> `Orchestrator.dispatch` -> `Dispatcher` -> `ProviderAgent.run` -> `Provider.generate`; текст читается из `TaskResult.output` |
| Идентичность провайдера и usage | `TaskResult.provider` / `model_name` / `usage` и `AIDecisionProvider.last_provider_name` / `last_model_name` / `last_usage` (**попытка**), а также `producer_provider_name` / `producer_model_name` / `producer_usage` (**производитель**: пусто/`None`, если ответ не дал валидированного решения) |
| Валидация решения | `AIDecisionProvider.decide_from_output` — существующая fail-closed лестница, без изменений |
| Проверки схемы, путей, защищённых областей, конфликтов, секретов и размеров | `app/workflows/project_generation.py` |
| Распознавание секретов | `app.execution.secret_files.is_secret_file` / `is_secret_directory` — общий распознаватель, а не второй фильтр |
| Durable-история генерации | собственный `RunStore` сервиса, под run id `run-generate-<declaration id>` |
| Верификация (вторая объявленная команда) | `ForgeApiService.run_declared_verification` |
| Тесты сгенерированного проекта | объявленная команда верификации, например `python -m unittest discover -s tests -t . -p "test_*.py"` |

**Схема вывода модели явная и строгая.** Один объект JSON::

    {"decision": {"decision_type": "...", "action": "...", "reason_code": "...",
                  "rationale": "...", "confidence": 1.0},
     "files": {"relative/posix/path": "entire file body"}}

Тело каждого файла должно быть текстом и кодироваться в UTF-8. До записи первого
байта каждый путь отклоняется, если он абсолютный, содержит префикс диска или UNC,
содержит сегмент `.` или `..`, использует обратный слэш, содержит управляющий
символ, называет устройство Windows, входит в `.git` / `.forge` / `.env`, называет
credential-файл или каталог, разрешается вне корня workspace, конфликтует с
существующим файлом, который оператор явно не разрешил перезаписывать, или
превышает ограничения размера.

**Требуемая конфигурация провайдера.** Транспорт задаёт вызывающий и передаёт его
как `AIDecisionProvider(provider, mock_transport=...)`. Реальный адаптер берётся из
`app/agents/providers/factory.py` с `ProviderConfig`, называющим провайдера, id
модели и переменную окружения с ключом — например
`ProviderConfig(provider_name="openrouter", model_name="<model id>", enabled=True,
api_key_env_var="OPENROUTER_API_KEY")`. Флаг `mock_transport` записывается в
результат, поэтому инжектированный текст никогда не может быть прочитан как работа
реальной модели.

**Как это запустить.** Сценарий проверяется своим сфокусированным тестовым модулем::

    python -X utf8 -m unittest discover -s tests -p "test_project_generation_production.py"

Один метод::

    python -X utf8 -m unittest discover -s tests -p "test_project_generation_production.py" -k <substring>

**Как запустить сгенерированный проект.** Материализованные файлы записываются под
корень workspace, с которым был собран сервис (`service.workspace.root`); список
файлов — `ProjectWorkflowResult.materialisation.file_list`. Запускайте его тесты
ровно так, как это делает объявленная команда верификации::

    python -m unittest discover -s tests -t . -p "test_*.py"

**Где хранятся результаты.** В памяти — в возвращаемом `ProjectWorkflowResult`:
`generation` (провайдер, модель, usage, решение, `mock_transport`,
`response_text_observed`), `parsed_project` (принятые файлы и отказы по каждому
файлу), `materialisation` (что создано, перезаписано, отклонено), `verification`
(реальный код возврата и диагностика), `acceptance_status` и один верхнеуровневый
`verdict`. Durable — диспетчеризация генерации пишет свою историю через собственный
`RunStore` сервиса под run id `run-generate-<declaration id>`, где
`provider_attempt` и `provider_result` несут провайдера, модель и usage. Сырой текст
модели намеренно **не** сохраняется и не проецируется: `to_dict()` сообщает
`raw_model_text_included: false`.

Верхнеуровневый `verdict` принимает одно из значений `real_provider_not_verified`,
`generation_failed`, `generation_refused`, `materialisation_failed`,
`materialisation_incomplete`, `build_failed`, `tests_failed`, `acceptance_failed`,
`approval_required`, `project_verified` или `declared_command_verified`. Он
выводится из наблюдённых фактов именно в этом порядке, поэтому успех более позднего
шага никогда не перекрывает более ранний отказ. Два значения `*_verified`
достигаются только на действительно успешном запуске —
`declared_command_verified` на пути объявленной команды (собственные тесты проекта
прошли, и gate приёмки вернул PASS), `project_verified` на пути генерации, — и ни
одно из них не утверждает, что ответила удалённая модель:
`real_provider_not_verified` остаётся вердиктом для *отказа* на этом пути, что он
честно и сообщает. **`verdict` никогда не становится
`REAL LLM PROJECT GENERATION VERIFIED`** — это значение
`verdict_expected_with_real_provider` называет как то, которое должен заслужить
реальный вызов провайдера.

**Что проверяется автоматически, а что нет.**

Проверяется автоматически, в `tests/test_project_generation_production.py`, с
инжектированным внутрипроцессным транспортом:

- модель запрашивается через существующий путь провайдера (утверждение на
  собственной записи вызовов инжектированного провайдера), и идентичность
  провайдера и usage доходят и до результата, и до durable-истории запуска;
- **содержимое** ответа модели определяет содержимое файлов на диске;
- пустой вывод, некорректный JSON, не-объект, отсутствующий или не-объектный член
  `files`, пустой набор файлов, нетекстовое содержимое и слишком большие тела —
  каждый случай закрывается отказом со своим кодом причины и не создаёт ничего;
- пустой ответ сообщает **отсутствие** идентичности производителя, модели, usage и
  сырого текста, а `response_text_observed: false`; некорректный текст сообщает
  `response_text_observed: true` при той же пустой идентичности;
- поля **попытки** (`last_provider_name` / `last_model_name` / `last_usage`) и
  представление **производителя** (`producer_provider_name` /
  `producer_model_name` / `producer_usage`) утверждаются как разные вещи: пустой
  или некорректный ответ не называет производителя и не сообщает usage, тогда как
  ответ, давший валидированное решение, сообщает и то, и другое. Закреплено в
  `tests/test_generation_evidence_contract.py`, положительный контроль которого
  упал бы, если бы представление просто никогда не заполнялось;
- абсолютные пути, обход через `..`, разрешения с выходом за корень, `.git` /
  `.forge` / `.env` и credential-пути отклоняются, и отклонённые файлы никогда не
  создаются;
- существующий файл пользователя не перезаписывается, если оператор не назвал его в
  `permitted_overwrite_paths`, и записывается, когда назвал;
- каждый созданный файл разрешается внутри разрешённого корня workspace;
- собственные тесты сгенерированного проекта действительно выполняются, и
  сообщается их реальный код возврата; падающий сгенерированный тест даёт ненулевой
  код и `tests_failed`, но никогда «verified»;
- инжектированный транспорт помечен в данных (`mock_transport: true`), и честное
  ограничение едет вместе с результатом в его `messages`;
- требуемое, но не выданное одобрение по-прежнему не выполняет ничего, а
  отклонённое одобрение не выполняет ничего.

**Не проверено — сказано прямо.** **Ни одного реального вызова провайдера не
сделано.** Ни модель, ни адаптер провайдера, ни API-ключ не участвуют ни в одном
проходящем тесте этого репозитория: каждый тест использует инжектированный
внутрипроцессный транспорт, возвращающий фиксированную строку. Это доказывает *путь*
генерации, валидацию схемы, проверки материализации и механику верификации. Это
**ничего** не доказывает о качестве кода, который выдала бы реальная модель, и ничего
о работоспособности сетевого пути реального адаптера. Единственный тест, который
сделал бы реальный вызов — `tests/test_project_generation_real_provider.py` —
пропускается, если не заданы одновременно `FORGE_ALLOW_REAL_PROVIDER_GENERATION=1` и
переменная с ключом провайдера, и **он никогда не выполнялся**.

Дальнейшие ограничения этого среза:

- проверки путей и содержимого — это **server-side файловая граница по тексту**, а
  не OS-песочница; код, который модель напишет позже, ими не удерживается от
  открытия абсолютного пути во время выполнения (§4.4);
- проверка содержимого на секреты — **не** универсальный классификатор секретов. Она
  распознаёт заголовок приватного ключа, JWT, литерал ключа провайдерской формы,
  жёстко заданное присваивание имени credential-формы и ссылку на *имя файла*
  credential, известное общему распознавателю. Секрет, написанный обычной прозой, не
  распознаётся — ровно как секрет внутри `notes.txt` не распознаётся фильтром
  подготовки (§4.10);
- проверки исследуют **текст**, поэтому они ничего не говорят о том, что
  сгенерированный проект делает при выполнении; OS-песочницы, которая ответила бы на
  это, здесь нет;
- отказ действует по файлу. Ответ с одним отклонённым файлом и несколькими
  допустимыми материализуется частично, и каждый отказ сообщается в
  `materialisation_rejections`, а не отбрасывается молча;
- **одна попытка генерации делает с реальным адаптером ДВА вызова провайдера, а не
  один.** Вызов 1 — запрос генерации, ответ на который становится проектом. Вызов 2
  происходит внутри `ForgeApiService.run_declared_verification`: эта точка входа
  вызывает `self._runtime.run_executor.execute(...)` без `provider_name`, поэтому
  диспетчер выбирает настроенный провайдер по умолчанию и делает вызов, текст
  которого затем **отбрасывается**, тогда как объявленная host-команда всё равно
  выполняется и даёт тот код возврата, который фактически сообщается. Это
  **существовавшее ранее** поведение этой точки входа, а не то, что добавил этот срез,
  и в production оно **тарифицируется**. Существующий
  `tests/test_project_workflow_production.py` его не обнаруживает только потому, что
  этот модуль собирает `_OrchestratorDouble`, а не реальный оркестратор. Удаление
  второго вызова потребовало бы либо не задействовать production-путь верификации,
  либо изменить контракт `run_declared_verification`; не сделано ни то, ни другое, и
  шаг верификации **не** был ослаблен или удалён ради экономии;
- провайдер намеренно публикует два разных представления, и отчёт должен читать
  нужное. `last_provider_name` / `last_model_name` / `last_usage` описывают
  **попытку** и заполняются, как только приходит ответ, поэтому их чтение не
  доказывает, что ответила модель. `producer_provider_name` / `producer_model_name` /
  `producer_usage`, привязанные к `last_response_produced_a_decision`, отвечают на
  вопрос, действительно ли пригодный ответ произвёл решение. `GenerationEvidence`
  читает в первую очередь `TaskResult`, который заполнил сам провайдер, потому что на
  этом пути вызов делает диспетчеризация, а не валидатор, и значит у валидатора нет
  собственной попытки для отчёта. Отчёт валидации также читается только тогда, когда
  решение было произведено: fail-closed ответ его не обновляет, и безусловное чтение
  вернуло бы ошибки валидации предыдущего вызова рядом с решением, которое упало по
  несвязанной причине;
- текст промпта генерации нейтрализуется по маркерам, которые существующий слой
  выбора контекста отказывается нести (`token`, `secret`, `prompt`, ...), потому что
  отброшенный элемент контекста Tier-1 прерывает запуск. Поэтому цель или критерий,
  содержащие одно из этих слов, доходят до модели переформулированными. Это
  ограничение точности этого пути, а не свойство безопасности;
- неподчинение модели цели или критерию не обнаруживается: срез проверяет, что
  объявленная команда верификации завершилась как объявлено, а не что
  сгенерированный проект удовлетворяет намерению оператора.

### 3.5.3 Managed multi-step execution (v0.1)

**Статус: CURRENT / VERIFIED** для описанного здесь механизма, с перечисленными ниже
ограничениями. `app/workflows/step_graph.py` — контракт и валидатор;
`app/workflows/step_runner.py` — драйвер.
`tests/test_step_graph_managed_execution.py` (31 тест, `Ran 31 tests ... OK`) —
доказательство.

| Область | Статус | Evidence |
| --- | --- | --- |
| Контракт графа шагов, детерминированный порядок, валидация до выполнения | **CURRENT / VERIFIED** | `app/workflows/step_graph.py` — `StepGraph.validate`, `topological_order`, `ready_steps` |
| Последовательный драйвер через существующую цепочку | **CURRENT / VERIFIED** | `app/workflows/step_runner.py` — `StepGraphRunner.run` / `resume`; каждый шаг вызывает `RunExecutor.execute(..., generate_decision=False)` с `ExecutionCoordinator` из композиции |
| Пошаговый durable-прогресс и восстановление после перезапуска | **CURRENT / VERIFIED** | `RunStore.write_state` под graph run id, отдельный привязанный `RunStore` на каждый запуск шага — `test_resume_continues_pending_steps_without_repeating_settled_ones` |
| Claim на шаг и запись side effect | **CURRENT / VERIFIED** | существующие `RunIdempotencyGuard` + `IdempotencyLedger.begin_side_effect` (`HOST_PROCESS`, не replay-safe) |
| Верификация шага, отдельная от выполнения | **CURRENT / VERIFIED** | `VerificationKind.EXIT_CODE` / `ARTIFACT_PRODUCED`; ошибка выполнения и ошибка верификации — разные поля |
| Параллельная диспетчеризация шагов | **REJECTED / NOT IMPLEMENTED** | нельзя запускать параллельно, пока безопасность не доказана |

**Контракт.** `StepGraph` — упорядоченный кортеж `StepDefinition`. Каждый несёт
уникальный `step_id`, `StepAction` с **объявленным оператором** `declaration_id` и
параметрами из закрытого словаря, явный кортеж `depends_on` и необязательную
`StepVerification`. Каждый объявленный шаг обязателен: в v0.1 нет необязательного
шага, который можно было бы пропустить и всё равно получить общий `COMPLETED`.
Словарь параметров — ровно `working_directory` (относительный путь с проверкой
вхождения) и `timeout_seconds` (**только сужение**: шаг может сократить лимит
объявления или профиля и никогда не расширить его; `validate` отказывает как
`parameter_widens_declared_limit`). Параметра окружения нет, потому что execution
policy уже требует, чтобы окружение запроса совпадало с окружением профиля.

`StepGraph.validate(declarations=...)` возвращает все причины отказа в фиксированном
порядке — до замораживания scope и до любого claim: некорректный или дублирующийся
`step_id`, необъявленный `declaration_id`, некорректные / дублирующиеся / собственные
/ неизвестные зависимости, параметр вне словаря или с небезопасным значением,
параметр, расширяющий объявленный лимит, и каждый цикл (по компонентам сильной
связности, как `cycle:a->b`). `assert_valid` возбуждает
`StepGraphValidationError` с этими кодами причин.

`StepStatus` — то, что известно об одном шаге: `PENDING` (не начат, claim отсутствует),
`RUNNING` (claim есть, шаг отправлен — **никогда не окончательный ответ**),
`WAITING_APPROVAL` (цепочка отказала из-за неразрешённого одобрения; ничего не
выполнялось), `SUCCEEDED` (процесс выполнился, его реальный код возврата 0, и все
объявленные проверки прошли), `FAILED` (процесс упал, истёк по таймауту или дал
ошибку, **либо** процесс успешен, но объявленная проверка не прошла, **либо** цепочка
отказала до отправки), `CANCELLED` (отмена запрошена до старта этого шага; он не
отправлялся), `BLOCKED` (зависимость не завершилась успехом), `UNKNOWN` (установить,
завершилось ли действие, невозможно).

`StepGraphStatus` — то, что известно о попытке целиком, с пессимистичным приоритетом —
`INDETERMINATE` > `CANCELLED` > `FAILED` > `REFUSED` > `INCOMPLETE` > `COMPLETED`.
`COMPLETED` получается только когда каждый шаг `SUCCEEDED` и каждая объявленная
проверка прошла; ничто иное его не даёт.

**Доказательство по шагу** хранится как отдельные наблюдения в `StepRecord`:
запланированное действие (`declaration_id`, канонические `parameters`,
`action_fingerprint`), фактически выполненная операция (`performed_executable`,
`performed_working_directory`, `step_run_id`, `request_id` — хранится только
*исполняемый файл*, никогда argv, в соответствии с существующей дисциплиной записи
выполнения), реальный результат (`execution_exit_code`, `execution_outcome`), реальная
верификация (`verification_status` со значениями `NOT_RUN`, `PASSED`, `FAILED`,
`ERROR`, плюс ожидаемое и наблюдённое), раздельные поля `execution_error` и
`verification_error`, и коды `evidence`, на которых держится статус. Процесс,
завершившийся с кодом 0, при непройденной объявленной проверке записывается как
`execution_outcome=EXECUTION_SUCCESS` со `status=FAILED` и
`verification_status=FAILED`: оба факта остаются читаемыми и никогда не смешиваются.

**Authority не изменена.** Граф — это план, а не разрешение. `StepGraphRunner`
получает `ExecutionCoordinator` из своей композиции и никогда не создаёт его сам;
каждый шаг замораживает собственный `RunScope` (workspace сервиса, профиль,
allowlist инструментов и командная authority одного объявления), а запуск идёт через
`RunExecutor.execute(..., generate_decision=False)` точно так же, как в
`run_declared_verification`. Модель не спрашивают, поэтому ничто сказанное моделью не
может повлиять на шаг. Runner не добавляет механизма одобрения: шаг, для которого
одобрение требуется и не разрешено, ничего не выполняет и сообщается как
`WAITING_APPROVAL`, а зависимые — `BLOCKED`. Одобрение привязано к fingerprint
намерения выполнения, поэтому изменение любого параметра шага меняет fingerprint, и
уже выданное одобрение отклоняется; его сравнивают два независимых компонента
(resolver и coordinator), и отключение любого одного по отдельности всё равно даёт
отказ.

**Отмена** останавливает только новые шаги. Уже отправленный шаг доводится до конца и
сообщается по тому, что реально произошло — с дополнительным кодом evidence
`cancel_requested_while_running` — и никогда не сообщается как `CANCELLED`. Шаги,
которые не начались, становятся `CANCELLED` с `cancelled_before_start`.

**Выполнение строго последовательное.** В каждый момент отправляется один шаг, и
следующий начинается только после того, как у предыдущего есть записанный результат.
Параллельная отправка не предпринимается, потому что ничто в этой архитектуре не
доказывает безопасность двух одновременных объявленных выполнений внутри одного
workspace, и план не должен иметь возможности создать такую конкурентность.

**Гарантия повторного выполнения, сформулированная точно.** Runner обеспечивает
**at-most-once отправку на claim (graph run, шаг), как это наблюдается через
существующий durable-журнал идемпотентности, и только пока этот журнал читаем**. Это
**не** exactly-once. Перед отправкой claim шага читается из журнала: только `START` —
или `RESUME` для шага, явно объявленного `repeatable` — разрешает отправку. Затем
сохраняется переход `RUNNING` и записывается side effect `HOST_PROCESS` как начатый;
только после этого появляется процесс. Поэтому durable `RUNNING` без результата
читается обратно как `UNKNOWN`, а начатый и не завершённый side effect процесса
заставляет журнал отказать в последующей автоматической попытке с
`SIDE_EFFECT_UNKNOWN`. Чего явно **не** гарантируется: нет распределённой транзакции и
нет межпроцессной блокировки, поэтому два процесса, гонящиеся за потерянным claim,
могут быть допущены оба; нет атомарности внешних эффектов процесса; нет
автоматического повтора шага в состоянии `FAILED`, `WAITING_APPROVAL` или `UNKNOWN` —
следующая попытка это **новый graph run id**, то есть новый claim, новый scope и новая
проверка одобрения; и нет песочницы сверх существующей границы workspace, allowlist
окружения и фильтра секретов.

Ещё два ограничения v0.1: graph run id — это одна попытка, и повторный запрос с тем же
id ничего не выполняет (`already_recorded`), а продолжение незавершённой попытки
требует явного `resume`; и `step_budget` может остановить попытку с ещё `PENDING`
шагами (статус `INCOMPLETE`) — именно из этого состояния продолжает `resume`.
Отклонённый вызывающий, который гонится с уже выполняемой в том же процессе попыткой,
не делает ни одного durable-чтения, потому что на Windows конкурентное чтение файла
состояния может сломать атомарный `os.replace` писателя — это ограничение
существующего хранилища, и runner его избегает, а не обходит.

### 3.5.4 Надёжное восстановление, контроль повторов и freshness Project Memory (Block #7)

**Статус: CURRENT / VERIFIED** для описанного здесь механизма, с перечисленными ниже
ограничениями. `app/runtime/recovery.py` — планировщик восстановления и durable-claim;
`app/runtime/continuation.py` — интеграция продолжения Project Factory;
`app/understanding/memory.py` — слой памяти.
`tests/test_recovery_and_project_memory.py` (31 тест, `Ran 31 tests ... OK`) —
доказательство, и каждое свойство, значимое для безопасности, доказано по схеме
fails-before / passes-after путём правки исходника на месте (8 инъекций, каждая
восстанавливает свой файл побайтово — SHA-256 до == после).

| Область | Статус | Evidence |
| --- | --- | --- |
| Обнаружение незавершённых попыток | **CURRENT / VERIFIED** | `RecoveryPlanner.unfinished_runs` поверх `RunStore.list_run_ids()`; `test_recovery_of_a_multi_step_graph_decides_every_step` |
| Решение по каждому шагу (продолжить / перепроверить / нужен владелец) | **CURRENT / VERIFIED** | `RecoveryPlanner.plan` → `RecoveryStepDecision`; `test_detection_of_an_unfinished_step` |
| Объявленный план переживает перезапуск | **CURRENT / VERIFIED** | `StepGraphSnapshot.declared_plan`, `graph_from_declared_plan`; `StepGraphRunner.resume(graph_run_id=...)` без аргумента графа — `test_recovery_after_a_process_restart` |
| Durable, атомарный claim на шаг | **CURRENT / VERIFIED** | `RecoveryClaimLog.acquire` (`O_CREAT \| O_EXCL` + запись в `RunStore`); `test_concurrent_claimers_produce_exactly_one_claim` |
| Семантика повторов по категориям действий | **CURRENT / VERIFIED** | `RunIdempotencyGuard.repeat_decision` → `RepeatGuarantee`; `test_refusal_to_auto_repeat_a_dangerous_operation` |
| Категории памяти, provenance, trust | **CURRENT / VERIFIED** | `app/understanding/memory.py` — `MemoryItemKind`, `EvidencePin`, `_ALLOWED_TRUST`; `test_an_inference_is_never_promoted_to_a_confirmed_fact` |
| Freshness: изменённые / удалённые / переименованные источники | **CURRENT / VERIFIED** | `freshness_of`, `ProjectMemory.refresh`; `test_invalidation_of_stale_facts_after_a_file_changes`, `test_a_renamed_file_is_neither_still_current_nor_a_new_unrelated_file` |
| Обнаружение противоречий | **CURRENT / VERIFIED** | `ProjectMemory.detect_contradictions`; `test_detection_of_contradictory_evidence` |
| Продолжение Project Factory | **CURRENT / VERIFIED** | `capture_context` / `recall_context` / `plan_continuation`; `test_recovery_of_project_factory_context` |
| Heartbeat, lease, owner identity | **REJECTED / NOT IMPLEMENTED** | не нужны для *безопасности* восстановления; обоснование записано ниже |
| Exactly-once выполнение | **REJECTED / NOT CLAIMED** | невозможно через локальный файловый ledger без распределённой транзакции |
| Автоматический повтор `FAILED` / `WAITING_APPROVAL` / `UNKNOWN` | **REJECTED / NOT IMPLEMENTED** | восстановление никогда их не переотправляет |

**Контракт восстановления.** При старте `RecoveryPlanner.unfinished_runs()` читает
каждую запись, которую хранит существующий `RunStore`, и сообщает о каждой попытке,
исход которой не доказан как terminal-success. Классификатор — `RunStore.classify_record`:
запись, которая лишь *заявляет* прогресс, даёт `INDETERMINATE` — никогда не
доказательство жизни и никогда не доказательство смерти. Для каждого шага `plan()`
возвращает `RecoveryStepDecision`, у которого `decision` — одно из `CONTINUE`
(безопасно продолжить), `REVERIFY` (работа могла завершиться, но объявленная проверка
должна быть восстановлена), `NEEDS_OWNER` (безопасного автоматического действия нет),
`ALREADY_SETTLED`, `BLOCKED`, `REFUSED`, `UNKNOWN` — а `classification` — тот
`ExecutionClassification`, который подтверждается доказательствами. Каждое решение
несёт точный код `reason` и, при наличии claim, `RepeatDecision` из ledger.

Управляемая попытка записывается с намеренно пустыми `state`/`status`, поэтому её
вердикт читается из документа графа шагов: `classify_run_record(step_graph_status=...)`
отображает `COMPLETED` → `TERMINAL_SUCCESS`, `FAILED` → `TERMINAL_FAILURE`, `REFUSED` →
`TERMINAL_REFUSED`, а `INDETERMINATE`, `INCOMPLETE` **и `CANCELLED`** →
`INDETERMINATE`. `CANCELLED` отображается в `INDETERMINATE` намеренно: Block #6 пишет
этот статус и для отмены до старта (ничего не отправлялось), и для отправленного шага,
который так и не завершился, а запись не доказывает, что именно произошло.

**Объявленный план теперь персистится.** `StepGraphSnapshot.declared_plan` хранит
сериализацию графа, объявленного при старте попытки, а `graph_from_declared_plan`
восстанавливает его из одной записи, перепроверяя записанный `fingerprint` каждого
шага против fingerprint, пересчитанного из его же сохранённых полей. Поэтому
`StepGraphRunner.resume` принимает `graph_run_id` **без** графа: источником плана
является запись, а вызывающий, передавший граф, получает сравнение на равенство с
восстановленным объявлением. Запись, созданная до Block #7, не несёт объявления и
отклоняется с `StepGraphStateError`, называющим отсутствующий план — она никогда не
перепроверяется против того, что вызывающий случайно помнит. Повреждённая запись также
отклоняется, а `read_step_graph_record` различает «ничего не записано» и «запись есть в
форме, которую эта сборка не принимает», не возбуждая исключения.

**Конкурентное восстановление.** Восстановление захватывает шаг через
`RecoveryClaimLog.acquire`, защищённый дважды: атомарным эксклюзивным созданием файла
(`O_CREAT | O_EXCL`) внутри каталога запуска, которым уже владеет `RunStore`, и
повторной проверкой durable-записи `recovery_claims` в том же снимке. Нужны оба, потому
что файл claim — это артефакт конкретного каталога, и два восстановления с разными
каталогами claim вообще не увидят файлов друг друга. Отказ от любого из них удаляет
файл, созданный этим вызовом, поэтому проигранная гонка никогда не блокирует шаг
навсегда. Это **не** второй backend хранилища: обе записи лежат в уже существующем
каталоге запуска, и claim **не является** полномочием — `RecoveryExecutor.continue_plan`
по-прежнему передаёт каждую отправку в `StepGraphRunner.resume`, который заново
перепроверяет объявленный план и заново авторизует каждый шаг так же, как при первой
попытке.

**Почему нет heartbeat, lease или owner identity.** В формате на диске нет ни owner
identity, ни heartbeat, ни срока действия, поэтому умерший запуск и всё ещё
выполняющийся побайтово неразличимы. Владелец проекта отклонил добавление такой
инфраструктуры без доказанной необходимости, и существующие доказательства показывают,
что для *безопасности* восстановления она не нужна: (1) единица восстановления — один
шаг, и его ветвь выбирается по доказательствам, переживающим падение, — по записи
side effect в ledger и по собственному статусу шага, — а не по вопросу «жив ли другой
процесс», поэтому живой процесс и мёртвый дают одно и то же безопасное решение;
(2) настоящая гонка уже отклоняется на уровень ниже — атомарным claim и ответом ledger
`RESUME`/`REFUSE` вместо `START`; (3) ошибочно истёкший lease превратил бы «неизвестно,
спросить владельца» в «безопасно повторить», а это ровно тот баг с дублированием
side effect, против которого этот дизайн и существует. Heartbeat сделал бы *отчёт*
красивее, но никогда — исход безопаснее.

**Гарантия повтора по категориям действий.** Измеряется по ledger, никогда не
предполагается. В `RepeatGuarantee` нет члена `EXACTLY_ONCE`.

| Категория | Гарантия | Автоматический повтор |
| --- | --- | --- |
| Durable-записи не существует | `FIRST_ATTEMPT` — это первая попытка, а не повтор | разрешён (действует обычная авторизация) |
| Terminal-исход durable-записан | `REPLAY_OF_RECORD` — записанный исход читается, процесс не перезапускается | это не повтор |
| Все эффекты terminal-записи replay-safe по построению | `CONVERGENT_REPEAT` | разрешён; повтор сходится к тому же состоянию |
| Эффект replay-safe по построению (`_REPLAY_SAFE_KINDS`, т.е. записи в workspace) начался без записи о завершении | `CONVERGENT_REPEAT` | **не переотправляется** — см. ниже |
| Эффект, не являющийся replay-safe (`HOST_PROCESS`, вызов провайдера), начался без записи о завершении | `REFUSED_SIDE_EFFECT_UNKNOWN` — исход `UNKNOWN` | запрещён; требуются новая operation identity и повторное одобрение |
| Запись — отказ авторизации (`DENIED`, policy, security failure) | `REFUSED_SECURITY_TERMINAL` | запрещён; требуются повторное одобрение и новая operation identity |
| Любая другая terminal-запись | `REFUSED_TERMINAL` | не допускается |
| Восстановимый claim без недоказуемого эффекта | `REQUIRES_REAPPROVAL` | только через обычный путь авторизации |
| Ledger, который не читается | `REFUSED_RECORD_UNREADABLE` | запрещён — нечитаемый ledger никогда не считается пустым |
| Durable-позицию установить нельзя | `UNKNOWN` | запрещён |

**Что явно НЕ гарантируется.** Не exactly-once: гарантия — это at-most-once отправка
*на claim, как она наблюдается через локальный файловый ledger, и только пока этот
ledger читается*: два процесса, гоняющиеся за потерянным claim, могут быть оба допущены
в окне, пока ни одной записи ещё нет, потому что нет ни распределённой транзакции, ни
межпроцессной блокировки. Не атомарность side effects: host-процесс — это целая
программа, и ledger фиксирует, что он был заявлен и как завершился, но никогда — что его
эффекты были атомарны. Никакой подмены успеха или неудачи для неизвестного: недоказуемый
исход остаётся `UNKNOWN`/`INDETERMINATE` во всех проекциях. И никакого автоматического
повтора: шаг `FAILED`, `WAITING_APPROVAL` или `UNKNOWN` восстановление никогда не
переотправляет.

**Один честный пробел, названный, а не скрытый.** Шаг, который был отправлен и чей
эффект так и не завершился, несёт durable-claim, и `StepGraphRunner.resume` намеренно не
допускает вторую доставку по такому claim — он читает запись обратно как `UNKNOWN`.
Поэтому восстановление сообщает о таком шаге как `NEEDS_OWNER`, а **не** как
`CONTINUE`, даже когда доказательства самого ledger говорят, что повтор сойдётся
(`CONVERGENT_REPEAT`). Гарантия сходимости сообщается как *доказательство для владельца*,
никогда как разрешение, потому что решение `CONTINUE`, которое исполнитель не смог бы
выполнить, было бы хуже отсутствия решения. Осознанный перезапуск такого шага — это
**новый graph run id**, то есть новый claim, новый scope и свежая проверка одобрения —
то же правило, которое Block #6 уже формулирует для любой дальнейшей попытки.

**Контракт элемента памяти.** `app/understanding/memory.py` хранит записи памяти о
текущем дереве исходников. Он переиспользует `StructuralFact.fact_id` и
`InferredFact.evidence_fact_ids` для доказательств, `ContextTrustLevel` для доверия и
`SnapshotFile.fingerprint` (собственный SHA-256 сканера) для freshness; он не добавляет
ни второй схемы provenance, ни второго словаря доверия, ни второй схемы хеширования.
Durability идёт через `RunStore.project_state`, то есть второго backend хранилища нет.
Каждая запись несёт `kind`, `source` (`ContextSourceType`) и `source_id`, `obtained_at`,
`MemoryScope` (проект, пути, символ), `evidence_fact_ids` / `evidence_ids`,
`trust_level`, `status`, `EvidencePin`, `claim_key` и append-only `history`.

| `MemoryItemKind` | Точное условие | Trust |
| --- | --- | --- |
| `CONFIRMED_FACT` | детерминированное структурное наблюдение; требует хотя бы одного `evidence_fact_id` | `CONFIRMED` (или `VERIFIED`, когда подтверждено записанным вердиктом) |
| `EXECUTION_RESULT` | наблюдаемый исход реального выполнения; требует `run_id`, `execution_result_id` или явных `evidence_ids` | `VERIFIED` |
| `VERIFIED_CONCLUSION` | вывод, сделанный из проверенных доказательств | **`INFERRED`** — это обеспечено кодом, а не намерением |
| `HYPOTHESIS` | предположение или вывод, не ссылающийся ни на какие доказательства | `INFERRED` или `UNVERIFIED` |
| `UNKNOWN_DATA` | то, чего проект не знает, включая файл, который сканер не стал хешировать | `UNVERIFIED` |
| `STALE_ENTRY` | записывается только оценкой freshness | trust заменённой записи |
| `CONTRADICTION` | записывается только обнаружением противоречий | **слабейший** из двух конфликтующих |

`status` — одно из `CURRENT`, `STALE`, `INVALIDATED`, `SUPERSEDED`. Ничего не удаляется.

**Никакого автоматического повышения.** `InferredFact` никогда не повышается до
подтверждённого факта. Нет ни метода повышения, ни порога, ни скоринга.
`MemoryRecord.__post_init__` отклоняет `VERIFIED_CONCLUSION` с любым trust, кроме
`INFERRED`, отклоняет `CONFIRMED_FACT` без `evidence_fact_id` и отклоняет
`EXECUTION_RESULT`, не называющий ни запуска, ни результата. Единственный путь от вывода
к подтверждённому факту — это *другая* запись, ссылающаяся на собственные доказательства,
которая помечает вывод `SUPERSEDED`, не переписывая его `kind` и `trust_level`; и
атрибуция оператора, записанная в `promoted_by`, которая всё равно создаёт новую
связанную запись, а не правит старую.

**Правило freshness.** Запись, опирающаяся на файл, закрепляет этот путь **и** fingerprint
его байтов, вычисленный сканером.

| Наблюдение | Запись становится | Код причины | Что записывается |
| --- | --- | --- | --- |
| путь есть, fingerprint совпадает | остаётся `CURRENT` | — | запись истории `revalidated` |
| путь есть, fingerprint отличается | `STALE` | `source_content_changed` | `STALE_ENTRY` с прежним и новым fingerprint |
| пути нет, ни один файл не несёт такой fingerprint | `STALE` | `source_deleted` | `STALE_ENTRY` |
| пути нет, ровно один файл несёт такой fingerprint | `STALE` | `source_renamed` | `STALE_ENTRY` с путём, куда переехало содержимое |
| пути нет, два или более файлов несут такой fingerprint | `STALE` | `source_deleted_with_ambiguous_move_candidates` | `STALE_ENTRY` со списком всех кандидатов |
| запись не закреплена (нет fingerprint) | остаётся `CURRENT` | — | ничего: незакреплённая — это не «актуальна по хешу», а явно незакреплённая |

**Решение о переименовании, сформулированное один раз.** Переименованный файл **не**
считается ни всё ещё актуальным по старому пути, **ни** совершенно новым посторонним
файлом. Запись помечается `STALE` по тому пути, откуда она была получена, а переезд
записывается как доказательство: те же факты вполне могут выполняться по новому пути, но
ничто не перемещает идентичность записи молча, потому что случайно идентичный второй файл
не является доказательством переезда. Повторное получение записи по новому пути — это
явное действие. Неоднозначный переезд (два и более идентичных кандидата) сообщается как
неоднозначный и никогда не разрешается выбором одного.
`test_a_renamed_file_is_neither_still_current_nor_a_new_unrelated_file` проверяет именно
это, включая то, что закреплённый путь не изменён и для нового пути не создаётся запись.

**Противоречия.** Две записи `CURRENT` противоречат друг другу, когда у них общий
`subject_key` (явный `claim_key`, иначе kind + scope + закреплённый источник), а дайджесты
их утверждений различаются. На каждую пару записывается одна запись `CONTRADICTION`,
несущая слабейший из двух уровней доверия. Ни одна сторона не удаляется, ни одна сторона
не переписывается, ничей `trust_level` не повышается; обнаружение идемпотентно.

**Память не даёт полномочий.** Сохранённый контекст — это данные. `ContinuationStrategy`
не несёт ни одобрения, ни scope, ни allowlist, ни токена, ни исполняемого файла, а
`app/runtime/continuation.py` не импортирует ни `ExecutionCoordinator`, ни
`LocalExecutionAdapter`, ни `RunScope`, ни `ApprovalPolicy`. `RecoveryPlan` никогда не
строит scope и никогда ничего не отправляет; `RecoveryExecutor` требует claim,
совпадающий с записанным на диске, и всё равно передаёт каждую отправку в
`StepGraphRunner.resume`, который авторизует заново с нуля. Память может влиять на то,
*что стоит попробовать*, и никогда не может влиять на то, *что разрешено*.
`test_memory_cannot_widen_the_workspace_or_authorize_an_undeclared_command` доказывает это,
записывая элемент памяти «владелец одобрил» и показывая, что необъявленная команда
по-прежнему отклоняется, а периметр workspace не изменён.

**Продолжение Project Factory.** `capture_context` записывает fingerprint workspace, пути,
увиденные сканированием, планируемые step id, записанные попытки, id элементов памяти,
подкреплявших контекст, и — явно — пути, которые сканирование **не** смогло захешировать,
только по имени. `recall_context` сравнивает это со свежим сканированием и сообщает дрейф
(fingerprint изменился, корень workspace изменился, пути добавлены или удалены), а также
сохранённые доказательства, которые больше не выполняются. `plan_continuation` объединяет
сохранённый контекст, текущее состояние проекта и находки планировщика восстановления в
одну `ContinuationStrategy`. Стратегия — это план; она никогда не является разрешением.

### 3.5.5 Operating surface и продуктовая интеграция (Block #8)

**Статус: CURRENT / VERIFIED** для описанных здесь маршрутов, проекций и UI, с
ограничениями, перечисленными в конце. `app/api/routes.py` — HTTP-поверхность;
`app/api/operating_surface.py` — серверная проекция и единственный объект, который
вызывают маршруты; `app/static/` — консоль.
`tests/test_product_integration.py` (93 теста, `Ran 93 tests ... OK`, реальный код
выхода 0) — доказательство; каждое security-значимое свойство ниже доказано
fails-before / passes-after путём правки исходника на месте. **Измерено в проходе
восстановления Block #8:** 24 инъекции в исходник, 63 утверждения, все — как ожидалось;
каждая инъекция восстановила файл побайтово (SHA-256 до == после), удалялся только
`__pycache__` затронутого модуля, тест запускался как `python -B`, поэтому устаревший
байткод не мог удовлетворить тест. Счётчики из предыдущей редакции этого абзаца
невоспроизводимы по дереву и заменены этими.

**До этого блока UI не существовало вообще.** `app/static/index.html`,
`app/static/app.css` и `app/static/app.js` — это весь клиент: ни фреймворка, ни
сборщика, ни шага сборки, ни новой зависимости. Каждый элемент управления вызывает
реальный маршрут, а каждый маршрут делегирует существующему `ForgeApiService` либо
существующим `StepGraphRunner` / `RecoveryPlanner`. Клиент **не хранит собственную
истину**: в нём нет оптимистичных обновлений, нет вывода статуса и нет пути, который
превращает отсутствующее значение в успех. UNKNOWN и INDETERMINATE отображаются как
есть и своим цветом.

#### Карта маршрутов с проверкой авторизации на каждом

Десять существовавших маршрутов не изменены. Block #8 добавил двадцать один
операционный маршрут и три маршрута ассетов; ни один существовавший путь не удалён и
не изменён. Block #9 добавил ещё пять операционных маршрутов и **расширил**
`/api/product/change-impact`, а не добавил параллельный путь.

| Маршрут | Метод | Проверка авторизации |
| --- | --- | --- |
| `/api/product/onboarding` | GET | граница проекта |
| `/api/product/requirements` | GET | граница проекта |
| `/api/product/plan` | GET | граница проекта |
| `/api/product/plan` | POST | граница проекта; переданный список шагов возвращается только как *предложенный* и ничего не авторизует |
| `/api/product/understanding` | GET | граница проекта; сканируется собственный workspace сервиса, никогда не значение из запроса |
| `/api/product/memory` | GET | граница проекта |
| `/api/product/change-impact` | GET | граница проекта + baseline id по allow-list, который должен быть записью, хранимой этим сервером. **Block #9 расширил этот ответ** полями `classification`/`basis` на каждую находку, `stale_facts`, `dependent_conclusions`, `verification_plan` и `evidence_graph`; каждый ключ Block #8 сохраняет смысл Block #8 |
| `/api/product/evidence-graph` | GET | граница проекта + граница запуска, когда указан `graph_run_id`. Только чтение; каждый переход ссылается на конкретные идентификаторы, а переход без доказательства возвращается как `MISSING` с `missing_code`. Отказывает в `ACCEPTED`, пока отсутствует обязательная связь |
| `/api/product/verification-plan` | GET | только граница проекта. Только чтение; план выбирается из объявленных ожиданий оператора, собственной топологии сканера и записанных результатов шагов — модель не вызывается, и `PASSED` — единственное состояние, считающееся прохождением |
| `/api/product/stale-facts` | POST | граница проекта + baseline id по allow-list. Только чтение, если не `invalidate=true`; тогда привязанные к файлам записи памяти переоцениваются через существующий `ProjectMemory.refresh` (переход в STALE, добавление истории, ничего не удаляется) |
| `/api/product/repair-plan` | POST | граница проекта + граница запуска. **Планирует и отказывает; repair не запускает.** `dispatch_performed` на этой поверхности всегда false, а `dispatch_path` объясняет причину |
| `/api/product/runs` | GET | граница проекта |
| `/api/product/runs/{run_id}` | GET | граница проекта + граница запуска (id по allow-list, должен быть записан здесь) |
| `/api/product/recovery` | GET | граница проекта |
| `/api/product/recovery/{graph_run_id}` | GET | граница проекта + граница запуска |
| `/api/product/graph/{graph_run_id}` | GET | граница проекта + граница запуска |
| `/api/product/approvals/{graph_run_id}` | GET | граница проекта + граница запуска; сообщает, но не может одобрить |
| `/api/product/goal` | POST | граница проекта; записывается на уровне гипотезы |
| `/api/product/memory` | POST | граница проекта; allow-list видов (только `HYPOTHESIS`, `UNKNOWN_DATA`) |
| `/api/product/memory/refresh` | POST | граница проекта |
| `/api/product/memory/{memory_id}/invalidate` | POST | граница проекта; может только ослабить утверждение |
| `/api/product/baseline` | POST | граница проекта |
| `/api/product/graph` | POST | граница проекта + allow-list идентификатора в теле + `StepGraph.assert_valid` против реестра деклараций оператора + собственные `RunScope`/`ApprovalPolicy`/`ExecutionCoordinator` раннера |
| `/api/product/graph/{graph_run_id}/resume` | POST | как выше, плюс объявленный план из записи должен совпадать с графом вызывающего |
| `/api/product/verify` | POST | граница проекта + `declaration_id` должен существовать в реестре оператора + обязательный `idempotency_key` |
| `/api/product/accept` | POST | граница проекта + `declaration_id` + обязательный `idempotency_key` |
| `/` | GET | нет — оболочка не содержит данных; каждая её панель вызывает защищённый маршрут |
| `/app.css`, `/app.js` | GET | нет — явный allow-list из трёх файлов, никогда не монтирование каталога |

**Что именно представляет собой эта граница.** Аутентификации на этой поверхности
по-прежнему **нет**; остаточный риск, зафиксированный в §3.4 и в аудите, не изменился,
и этот блок не заявляет его закрытие. Каждый маршрут выше обеспечивает ту границу,
которая действительно существует:

* **граница проекта** — приложение привязано к одному авторитетному `project_id` при
  конструировании, из собственной композиции сервиса. Запрос, называющий другой проект,
  отклоняется с 403 до любого чтения, и отказ **не возвращает запрошенное имя**, поэтому
  его нельзя использовать как зонд для выяснения того, что существует.
* **граница запуска** — id запуска должен соответствовать
  `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` (он попадает в раскладку хранилища) и уже быть
  записан в собственном `RunStore` этого сервиса, поэтому запуск другого развёртывания
  неотличим от запуска, которого никогда не было. Ответ для незаписанного id
  **зависит от маршрута, и обе формы ничего не раскрывают** — измерено:
  `/api/product/runs/{run_id}` возвращает **404** `{"detail": "unknown run"}` без
  содержимого записи, а две читающие *проекции* `/api/product/graph/{graph_run_id}` и
  `/api/product/recovery/{graph_run_id}` отвечают **200**, помещая вердикт «не найдено»
  в тело (`found: false`, либо `record_classification: UNKNOWN` с
  `record_reason: no_persisted_step_graph`) и снова без содержимого записи — именно эту
  форму фиксирует `test_32`. Ни один из ответов не называет данные другого развёртывания.
* **граница действия** — вызывающий называет, *какое* объявленное оператором действие
  выполняется, и никогда — *что* это такое. Реестр деклараций, workspace, профиль, набор
  команд, окружение и политика одобрения целиком берутся из серверной композиции.
* **граница полномочий** — **ни один маршрут в ЭТОМ модуле не конструирует, не
  подменяет и не обходит** `RunScope`, `ExecutionCoordinator`, `AuthorizedExecution` или
  `ApprovalPolicy`. Каждый маршрут делегирует: либо точке входа `ForgeApiService`, либо
  `OperatingSurface`, который сам выполняет все эти проверки при каждой диспетчеризации.

**Решение (Block #8): где живёт сборка.** Сборка координатора выполнения и
`StepGraphRunner` живёт в `app/api/operating_surface.py`. Она **не** живёт в этом модуле
и была намеренно вынесена **из** `app/api/service.py`:

* `app/api/service.py` **делегирует; он не порождает процессы.** Это структурно
  проверяется тестом `test_api_layer_has_no_direct_adapter_or_process_access`, который
  падает, если API-*сервис* называет адаптер выполнения, `AuthorizedExecution`,
  `subprocess` или `Popen`. Возврат сборки в сервис потребовал бы ослабить эту проверку —
  то есть ослабить security-значимый граничный тест ради рефакторинга. Минимальное
  изменение, удовлетворяющее обоим, — перенести сборку, а не ослабить проверку.
* **Этот модуль — HTTP-граница.** Он проверяет форму запроса, применяет
  per-route проверку авторизации и сериализует результат. Он не конструирует ничего, что
  что-либо авторизует, поэтому маршрут не может стать местом, где забыли проверку.
* **`app/api/operating_surface.py` — единственная точка композиции.** Ровно одно место
  собирает координатор, ровно одно принимает решение о политике одобрения и ровно один
  объект, с которым говорят маршруты. Размазывание сборки по маршрутам позволило бы двум
  маршрутам разойтись в политике — именно тот отказ, ради предотвращения которого
  существует граница полномочий.

Иными словами, инвариант сформулирован точно, а не ослаблен: **ни один *маршрут* не
конструирует и не обходит компонент полномочий**; operating surface собирает единственный
координатор из ингредиентов, которые выдаёт сервис, и сам ничего не даёт — раннер
перепроверяет `RunScope`, `ApprovalPolicy`, `ExecutionCoordinator` и workspace при каждой
диспетчеризации, а поверхность отказывает (`None` или типизированная ошибка
конфигурации), а не собирает когда-либо более слабую политику. `test_71`–`test_73`
фиксируют это размещение в обе стороны.

`authorize_project` — единственная проверка границы проекта — прогоняется на каждом
защищённом маршруте тестом `test_33_a_foreign_project_id_is_refused`; её отключение
ломает этот тест.

#### Одна политика на композицию, и отсутствующая политика отказывает

`RunExecutor` трактует отсутствующую политику одобрения как безусловный отказ
(`approval_policy_required`). Из-за этого точка входа объявленной верификации
отклоняла **каждый** запуск, доходивший до ветки host execution, когда у сервиса не
было политики, тогда как раннер графа шагов подставлял `ApprovalPolicy()` и выполнял —
два ответа на один вопрос.

Решение сохраняет все эти fail-closed свойства, а не ослабляет одно из них, чтобы
маршруты заработали:

* `ForgeApiService._approval_policy` **остаётся `None`**, если политика не передана.
  «Серверная политика одобрения не собрана» — это реальное состояние, и два существующих
  контрактных теста прямо это утверждают
  (`test_missing_approval_policy_refuses_to_execute`,
  `test_default_service_requests_no_execution`). Замена его на значение по умолчанию
  внутри сервиса молча превратила бы «одобрение не настроено» в «одобрение настроено».
* **Отсутствующая политика отказывает графу шагов, а некорректная — это
  контролируемая ошибка конфигурации.** `_validate_step_graph_ingredients` обрабатывает
  три разных случая; все они fail-closed, все происходят **до** создания любого
  `ExecutionCoordinator`, и ни один не является сырым `KeyError`/`AttributeError` из
  недр раннера:

  | Случай | Ответ | Почему именно такой |
  | --- | --- | --- |
  | ключ `approval_policy` **отсутствует** | `SurfaceCompositionError` → HTTP 500, код `approval_policy_missing` | композиция вообще не реализует контракт; сырой `KeyError` был бы трассировкой, а не отказом |
  | значение равно **`None`** | обычный `None` → нет раннера → `step_graph_unavailable` (409) | «позиция по одобрению не заявлена» — легитимное состояние, намеренно **не** смешиваемое со сломанной композицией |
  | значение **неверного типа** / не выполняет контракт | `SurfaceCompositionError` → HTTP 500, коды `approval_policy_invalid_type`, `approval_policy_contract_unsatisfied` | политика, которую нельзя применить, не является политикой |

  Ошибка конфигурации сообщается как **500, никогда как 409**: 409 сказал бы вызывающему
  «ты попросил недозволенное», что неверно, и приучил бы его повторять запрос. Отказ
  никогда не является подстановкой политики: значение по умолчанию требует одобрения
  только для своего обязательного набора инструментов, поэтому подстановка означала бы,
  что развёртывание, никогда не заявлявшее позицию по одобрению, всё равно выполняло
  объявленные host-команды. `test_10c`, `test_64`–`test_69` фиксируют три случая и
  порядок (`test_68` подменяет сборщик координатора на такой, который сначала
  записывает факт сборки, а затем падает; запись остаётся пустой для каждого падающего
  случая), а `test_70` фиксирует 500. Чувствительный тест, возвращающий
  `or ApprovalPolicy()`, заставляет `test_10c` упасть.

**Двухслойная проверка intent fingerprint, где каждый слой наблюдается отдельно.** Проверка
состоит из двух независимых слоёв: серверный resolver сравнивает intent fingerprint
запроса с fingerprint, под который было выдано одобрение (`app/tools/approval.py`), а
координатор **независимо** сравнивает `approved_fingerprint` одобрения с тем intent,
который собирается выполнить (`app/execution/authorizer.py`). При приёмке было измерено,
что тест, проходящий весь стек, сначала проходит через resolver, поэтому поломка
**координаторного** слоя в одиночку была невидима — проба I12, pass→pass. Этот пробел
закрыт, и слоистость была измерена, а не предположена:

| Сравнение координатора | Сравнение resolver | `test_61` (слой координатора) | `test_63` (слой resolver) |
| --- | --- | --- | --- |
| целое | целое | PASS | PASS |
| **сломано** | целое | **FAIL** | PASS |
| целое | **сломано** | PASS | PASS |
| **сломано** | **сломано** | **FAIL** | **FAIL** |

Это в точности воспроизводит свойство Block #6: поражение **любого одного** слоя всё
равно отклоняет изменённые параметры (второй слой держит), а ломает защиту поражение
**обоих**. `test_61` и `test_62` вызывают `ExecutionCoordinator.execute` напрямую с
resolver, который всегда отвечает, поэтому отказать может только координатор; `test_63`
использует production-реализацию одноразового `InMemoryApprovalResolver` и покрывает слой
resolver вместе со свойством «изменение параметров». Ни один из тестов не заявляется как
покрывающий слой другого.
* **`create_api_app` заявляет политику явно**, на этапе композиции, поэтому
  HTTP-поверхность работоспособна. Развёртывание, которому нужна своя, передаёт её в
  `ForgeApiService`, и значение по умолчанию фабрики не используется.

Тест `test_api_layer_has_no_direct_adapter_or_process_access` также фиксирует структурную
границу, которую пришлось соблюсти этому блоку: `app/api/service.py` не может содержать
ссылок на адаптер выполнения или на порождение процессов, поэтому раннер и его
координатор собираются в `app/api/operating_surface.py` из ингредиентов, которые выдаёт
сервис. Это размещение намеренно и заявлено в `app/api/routes.py`: `service.py`
**делегирует**, а не порождает процессы; модуль маршрутов только проверяет и делегирует;
а operating surface — единственная точка композиции, поэтому есть ровно одно место,
собирающее координатор, и ровно одно решение о политике, а не по одному на маршрут. Ни
один маршрут в `routes.py` не конструирует, не подменяет и не обходит ни один компонент
полномочий.

#### Что изменил проход восстановления после упавшего fix-агента

Fix-агент Block #8 упал без отчёта и оставил непроверенную работу. Состояние было
переизмерено по дереву, и четыре оставленных им дефекта найдены и исправлены. Они
записаны здесь, потому что каждый был разрывом между зелёным тестом и реальным
поведением, а не вопросом стиля:

1. **`LocalExecutionAdapter` перестал собирать артефакты на изолирующем пути.** Новое
   объявление `harvests_artifacts` по умолчанию было `False` и стало *единственным*
   условием harvesting, тогда как раньше harvesting жил внутри ветки изоляции. Поскольку
   `isolate_workspace=True` — значение по умолчанию для адаптера, каждая изолирующая
   композиция молча перестала собирать артефакты, а раннеру шагов по-прежнему сообщали,
   что артефакт искали, — реально произведённый артефакт получал `FAILED` с
   `artifact_not_produced`. Измерено: `test_a_produced_artifact_passes_by_its_harvested_digest`
   падает именно в таком состоянии. Теперь объявление *добавляет* harvesting
   неизолирующей ветке вместо замены правила изоляции, и `harvests_artifacts` правдиво
   сообщает оба случая.
2. **`can_run_accept_cycle()` объявлял возможность, в которой маршруты отказывали.** Без
   скомпонованной политики одобрения он возвращал `True`, тогда как
   `POST /api/product/accept` отвечал 500 `approval_policy_required` и ничего не писал, —
   та же нечестность, которая уже была исправлена для `can_run_declared_verification`.
   Теперь требуется заявленная политика, и `test_32d` это утверждает.
3. **Четыре закоммиченных теста опирались на разрешающее чтение `None`.** `test_09`,
   `test_10`, `test_11` и `test_12` из
   `tests/test_execution_authorization_hardening_v03.py` вызывали
   `ExecutionCoordinator.execute` без `approval_policy`, поэтому исправление об
   обязательной политике отклоняло их до достижения предмета проверки. Теперь каждый
   заявляет политику, которую неявно и проверял; ничего в проверке отпечатка или в
   свойстве «поток без одобрения» не ослаблено, и это переизмерено: подавление сравнения
   отпечатков в координаторе по-прежнему валит `test_09` и `test_10`, а заявление политики,
   требующей одобрения, по-прежнему валит `test_12`.
4. **Правило достижимости маршрутов всё ещё могло проходить по совпадению.** Сужения
   коллизии префиксов было недостаточно: `app/static/app.js` *называет*
   `/api/product/memory/{memory_id}/invalidate` в комментарии, и сопоставление с сырым
   текстом ассета считало этот комментарий вызовом клиента. Теперь правило сопоставляет
   строковые фрагменты в коде с удалёнными комментариями, поэтому маршрут, упомянутый
   только в прозе, его больше не удовлетворяет. Измерено: удаление реального вызова
   клиента валит и `test_03b`, и `test_03d`, а возврат старого правила вместе с этим
   удалением снова делает `test_03b` проходящим — это и есть воспроизведение исторического
   ложного срабатывания.

Ещё два теста проходили по неверной причине и были исправлены: `test_14c` давал своему
позже сортирующемуся шаговому запуску *самую новую* метку времени, поэтому порядок по id и
порядок по времени совпадали, и тест выживал при том самом дефекте упорядочивания, который
называет (теперь два порядка расходятся, и тест падает при возврате
`reversed(list_run_ids())`); а `test_70` вызывал сериализатор ошибки напрямую вместо
наблюдения HTTP-статуса, который получает вызывающий (теперь он обращается к
`POST /api/product/graph` и утверждает 500, не 409 и не 200, по сети).

**Одна поправка к описанию инцидента.** В отчёте отказ
`if approval_policy is None:` в `app/orchestrator/run.py` был назван
несанкционированной правкой. Это **не** правка: этот код присутствует без изменений в
закоммиченной ревизии, а `git diff` показывает в том файле только проброс
`generate_decision` / `run_store`. Значит, противоречие, описанное в D6, было между
закоммиченным `run.py` (отказывает при `None`) и закоммиченным `authorizer.py`
(пропускал политику при `None`), и изменена была только половина в `authorizer.py`.

#### Что покрывает консоль

Список проектов и текущая стадия; ввод цели; понимание проекта; требования и критерии
приёмки; объявленный план с зависимостями шагов; запросы на одобрение с их параметрами и
последствиями; старт выполнения; живые статусы шагов; результаты верификации;
изменённые файлы; история запусков; полная долговечная запись одного запуска;
восстановление одной попытки; Project Memory, разделённая на подтверждённые факты,
проверенные выводы, результаты выполнения, гипотезы, неизвестные данные и устаревшие
записи. Состояния загрузки, пустоты, недоступности сервиса, отказа API, таймаута и
неопределённости имеют собственное отображение, а состояния, которые можно повторить,
предлагают повтор. Возможность, не собранная в данной сборке, показывается как
недоступная, **и её элементы управления отключаются**, а не выглядят работающими.
`test_03b` прямо утверждает свойство полноты: на каждый маршрут `/api/product/*` есть
ссылка из клиента, поэтому ни один маршрут не является «только API» и ни одна секция не
является декоративной.

**Block #9 добавил четыре панели, каждая управляется маршрутом, который реально
отвечает.** Панель Change Impact дополнительно показывает классификацию каждой
находки, основание за ней (чтобы читатель мог проверить метку, а не доверять ей),
предупреждение о неоднозначном перемещении со списком кандидатов, устаревшие факты с
причиной инвалидации, зависимые выводы и подразумеваемые проверки с их словарём из
шести состояний. Новая панель Evidence Graph показывает каждый переход, каждую
отсутствующую связь с её `missing_code`, вычисленные вердикты требований и пустой отсек
`claimed_by_agent` с причиной, почему он пуст. Панель Verification Plan показывает
счётчики шести состояний, где `PASSED` — единственное прохождение, и называет пробелы
покрытия. Панель Repair Decision показывает исход, единственный `stop_rule`, бюджет,
вероятную причину по каждому шагу с её порядковой уверенностью и ограничениями, а также
то, что `dispatch_performed` равно false. Элемент «Invalidate stale facts» — единственная
запись на панели Change Impact, и он вызывает существующий `ProjectMemory.refresh`.
Достижимость Block #9 утверждается `test_09`, который требует, чтобы каждый новый
маршрут присутствовал как **исполняемая форма вызова**, и сочетает это с контролем,
доказывающим, что голый префикс и упоминание в комментарии оба отвергаются — ровно тот
ложноположительный результат, который зафиксировал Block #8.

**Цель — это не автоматический конвейер, и документация не должна это подразумевать.**
Цель → understanding → требования → план — это последовательность *экранов, которые
оператор проходит*, а не автоматический вывод. Запись цели создаёт `HYPOTHESIS` и ничего
больше; требования, критерии и их ожидания берутся из композиции оператора
(`declarations` и `acceptance_specs`), а план — это те же объявления. Поэтому цель **не**
влияет на то, какая команда может выполняться, какие критерии проверяются и что содержит
план — намеренно, потому что естественный язык никогда не должен выбирать команду или
определять собственное условие успеха. `test_44` фиксирует, что записанная цель является
гипотезой, что возвращённые требования принадлежат оператору и что сохранённый документ
согласуется с этим.

#### Change Impact: что доказано, а что нет

`GET /api/product/change-impact` сравнивает собственные SHA-256 fingerprints сканера с
записанным baseline и сообщает: какие файлы были **добавлены, изменены или удалены**;
какие структурные факты несли эти файлы; какие рёбра топологии ссылаются на эти факты,
так что зависимый узел *назван*; какие элементы памяти привязаны к изменённому пути; и
какие объявленные ожидания приёмки называют изменённый путь, так что проверка для
повторного запуска названа. Переименование сообщается как `probable_rename_to` и никогда
как установленное, потому что два файла с идентичными байтами неотличимы от переноса.
`ChangeImpact` доказан тестами `test_37` (реальная правка пользователя, сообщены оба
fingerprint, правка сохранена на диске), `test_38` (удаление), `test_39` (вероятное
переименование), `test_40` (ответ заявляет и то, что он **не** доказывает) и `test_41`
(критерий связывается только там, где объявленное ожидание называет изменённый путь,
всё остальное попадает в `unlinked_requirements`). Отключение обнаружения изменений
ломает `test_37`.

Три ограничения являются частью ответа, а не умолчанием:

* зависимая **проверка** называется только там, где объявленное ожидание *буквально*
  называет изменённый путь; там, где требование нельзя связать с изменённым файлом по
  имеющимся данным, оно попадает в `unlinked_requirements`, и полная прослеживаемость
  «требование → файл» **не** заявляется;
* отчёт никогда не возвращает содержимое файлов, поэтому текстового diff нет —
  поверхность изменений это набор изменений на уровне файлов с обоими fingerprints;
* отчёт не запускает никаких действий, не повышает предположение до подтверждённого
  факта, а повторный запуск связанной проверки остаётся явным решением оператора.

#### Рассуждение о влиянии изменений, Evidence Graph, планирование проверок и решение о repair (Block #9)

**Статус: РЕАЛИЗОВАНО И ПРОВЕРЕНО для четырёх возможностей ниже. Два ограничения
интеграции указаны в этом разделе и возможностями НЕ являются.**

##### Карта компонентов и что намеренно не дублировалось

| Новый компонент | Файл | Опирается на (существующее) | Намеренно НЕ является вторым |
| --- | --- | --- | --- |
| Change Impact Engine | `app/analysis/impact.py` | `SnapshotFile.fingerprint`, `UnderstandingSnapshot.facts` / `.topology` / `.manifests`, `freshness_of` и коды инвалидации из `app/understanding/memory.py`, `AcceptanceSpec` | реестром фактов, графом топологии, хранилищем памяти, схемой fingerprint |
| Evidence Graph | `app/analysis/evidence.py` | `MemoryRecord.memory_id` (цель), `AcceptanceCriterion.criterion_id`, `StepGraphSnapshot.declared_plan`, поля `StepRecord`, `StepGraphRunner.intent_fingerprint`, `RunIdempotencyGuard.recorded_state`, наборы `SnapshotFile` | хранилищем целей, записью шага, механизмом одобрения, словарём вердиктов |
| Verification Planning | `app/analysis/verification.py` | те же объявления `AcceptanceSpec`, собственные данные `TopologyEdge` сканера, `StepRecord.verification_status` | выводом зависимостей, зависимостями, угаданными моделью, реестром тестов |
| Ограниченное решение о repair | `app/analysis/repair.py` | `RepeatGuarantee` / `RepeatDecision` из `app/agent_runtime/idempotency_integration.py`, `ApprovalPolicy`, `StepRecord.attempts` | retry-движком, вторым ledger, вторым механизмом одобрения |

Маршрут Block #8 `GET /api/product/change-impact` был **расширен**, а не
продублирован: `OperatingSurface.change_impact` теперь строит список `changes` через
`_surface_changes`, который читает каждое производное поле из ОДНОЙ находки
движка, поэтому отчёт и план проверок не могут расходиться в том, что изменилось.
Каждый ключ Block #8 сохраняет своё значение Block #8 (`ADDED` / `CHANGED` /
`DELETED`), и `probable_rename_to` по-прежнему никогда не становится
установленным переименованием.

##### Классификации Change Impact и сохраняемое основание

Каждая находка несёт `classification` **и** `basis`, который её произвёл, а
`classify_basis` в `app/analysis/impact.py` — единственное место, где
классификация решается:

* `CONFIRMED` требует хотя бы одного **структурного** основания —
  `FINGERPRINT_MISMATCH`, `FINGERPRINT_ABSENT_AT_BASELINE`,
  `FINGERPRINT_ABSENT_NOW` или `SCANNER_FACT_MATCHED_BY_PATH`. Это наблюдения
  сканера;
* иначе, при хотя бы одном **слабом** основании (ребро топологии, которое
  *ссылается* на факт изменённого пути; объявленный манифест, который *является*
  этим путём; объявленное ожидание, называющее путь; запись памяти, привязанная к
  пути; файл теста, чей путь связан с ним), находка — `PROBABLE`: реальная,
  подтверждённая ссылкой связь, которая сама по себе не доказывает влияние;
* при **пустом** основании находка — `UNKNOWN`. Пустое основание никогда не
  классифицируется как `CONFIRMED` — это закреплено `test_25` и доказано
  инъекцией: если `classify_basis` начнёт возвращать `CONFIRMED` для пустого
  основания, тест краснеет.

Дополнительно сообщается `support_rank`, и это **не** уверенность: он считает число
*различных* видов основания, это указано в самом ответе, а `classify_basis` его
игнорирует, поэтому он не может продвинуть находку.

**Неоднозначные перемещения никогда не разрешаются.** Удалённый путь, байты
которого появились ровно по одному добавленному пути, сообщается как
`probable_rename_to` с основанием силы `PROBABLE`, содержащим слова «NOT
established». Удалённый путь, байты которого появились по двум и более добавленным
путям, сообщается с `ambiguous_move=true`, полным списком `move_candidates`,
`probable_rename_to=""` и примечанием, что ни один кандидат не выбран. `test_22`
закрепляет оба направления и несёт встроенный контроль, который перезапускает то же
правило без защиты `len(candidates) == 1` и показывает, что ослабленное правило
*выбрало бы* первого кандидата. Инъекция подтверждает: снятие этой защиты делает
`test_22` красным.

**Устаревшие факты и зависимые выводы.** Структурный факт устарел, когда тот же
`fact_id` (который `StructuralFact.create` выводит из содержимого) отсутствует в
текущем сканировании того же исходного пути. Причина — один из собственных кодов
`memory.py`: `source_content_changed`, `source_deleted`, `source_renamed`,
`source_deleted_with_ambiguous_move_candidates` — плюс
`fact_not_extracted_from_current_content` для случая, когда файл на месте, а
сместилось *наблюдение*, что является другим фактом. Запись памяти, чьи
`evidence_fact_ids` ссылаются на устаревший факт, сообщается как зависимый вывод со
`status="PENDING_REVALIDATION"`, и она **названа, а не переписана**: ничто в
`app/analysis/` не редактирует запись памяти и не продвигает её. История
сохраняется: идентификатор устаревшего факта вливается в собственную запись
baseline этой поверхности под `stale_fact_history`, а
`POST /api/product/stale-facts` с `invalidate=true` переоценивает каждую
привязанную к файлу запись через **существующий** `ProjectMemory.refresh`, который
добавляет `STALE_ENTRY`, а не удаляет что-либо.

##### Evidence Graph: переходы и как показывается отсутствующая связь

Цепочка — `GOAL → REQUIREMENTS → ACCEPTANCE_CRITERIA → PLAN → STEPS → APPROVAL →
AUTHORIZED_ACTIONS → FILE_CHANGES → COMMANDS → VERIFICATION_RESULTS → FINAL_STATUS`,
и каждая связь — это `TraceLink` с полями `from`, `to`, `status`, `source_ref`,
`target_ref`, `evidence`, `detail` и — для отсутствующей связи — `missing_code`.

Связь без доказательства **выдаётся со `status="MISSING"`** и машинным кодом,
называющим точно, какой переход отсутствует. Она никогда не отбрасывается и
никогда не заполняется правдоподобным объектом. Коды — закрытый проверяемый
словарь (`MISSING_CODES`): код вне словаря вызывает исключение, а не отчёт. Два
примера, закреплённых тестами: шаг, который так и не дошёл до одобрения, даёт
`no_approval_decision_recorded` с указанием, что одобренный fingerprint не записан;
требование без acceptance spec даёт `requirement_has_no_declared_criterion` с
указанием, что проверить его нечем.

**Итоговый статус не берётся из заявления агента.** `derive_verdict` читает только
записанный `VerificationStatus` и собственные критерии требований: `PASSED` —
единственное проходящее состояние, `ERROR` отображается в `UNKNOWN` (никогда в
`FAILED`), а пустой набор критериев — `UNPROVEN`. `StepGraphStatus.COMPLETED` **не**
принимается как доказательство, и ответ это сообщает
(`graph_status_used_as_proof: false`). Всё, что заявил агент, лежит в отдельном
отсеке `claimed_by_agent`, который вычисление вердикта не читает; `test_13`
показывает, что заявление не может спасти граф с отсутствующей обязательной связью.

**`ACCEPTED` отказывается при отсутствии обязательного доказательства.**
`_acceptance_gate` отказывает по обязательным кодам и возвращает
`final_status="ACCEPTED_REFUSED_INCOMPLETE_TRACE"` с `mandatory_missing_codes`,
`mandatory_missing_kinds` и `acceptance_refusal_reason`. `test_11` сочетает отказ с
негативным контролем (необязательный код сам по себе **не** отказывает) и
позитивным контролем (когда обязательных пропусков нет, возвращается вычисленный
вердикт). Инъекция подтверждает: отключение отказа делает `test_11` красным.

**Одобрение привязано к конкретному действию и его параметрам.** Связь
`STEPS → APPROVAL` несёт `parameters` шага, `approved_fingerprint`, который runner
пересчитывает через `StepGraphRunner.intent_fingerprint`, `observed_fingerprint` и
булево `parameter_binding_holds`. **Двухслойная** проверка не тронута: и резолвер в
`app/tools/approval.py`, и независимое сравнение в `app/execution/authorizer.py`
(`approved_fingerprint != intent.fingerprint` → `intent_fingerprint_mismatch`)
по-прежнему на месте, а граф сообщает то значение, которое сравнивает координатор,
а не выводит своё.

##### Verification Planning: шесть состояний

`app/analysis/verification.py` планирует проверку для каждого объявленного ожидания
и для каждого изменённого пути, который не покрыт ожиданием. Словарь — ровно
`NOT_RUN`, `RUNNING`, `PASSED`, `FAILED`, `SKIPPED`, `UNKNOWN`, и `PASSED` —
единственное состояние, у которого `is_pass` истинно: `test_30` проверяет это для
всех шести значений, а `only_passed_counts_as_a_pass` указано в самом ответе.
Отображение упорядочено по тому, что устанавливает попытка: `PASSED`/`FAILED` —
реальные наблюдения; `ERROR` становится `UNKNOWN`; далее решает статус шага —
`RUNNING → RUNNING`, `UNKNOWN → UNKNOWN`, `BLOCKED`/`CANCELLED → SKIPPED`,
`FAILED → FAILED`, а `SUCCEEDED` без оценённой проверки → `UNKNOWN`, потому что
успешная команда не является прошедшей проверкой. Инъекция подтверждает, что
порядок важен: возврат прежнего порядка (который читал объявленную проверку первой
и сообщал непроверяемый шаг как `NOT_RUN`) делает `test_30` красным.

Каждый пункт плана сохраняет ПРИЧИНУ выбора, свою связь `driven_by` с изменением,
`requirements`, `related_tests`, `evidence`, `criticality` и, для пробела,
`uncovered_reason`. **Пробелы покрытия называются, а не округляются вверх**:
изменённый путь без объявленного ожидания становится проверкой со
`state="UNKNOWN"` и
`uncovered_reason="no_declared_expectation_covers_this_path"`, а требование без
критерия попадает в `requirements_without_checks`. Затронутый тест сообщается со
словами «not proof that it covers the change», потому что это связь по пути.

Зависимости никогда не угадываются: план читает собственные записи `TopologyEdge`
сканера. Ни одна модель на этом пути не вызывается.

##### Ограниченное решение о repair: бюджет, предел попыток, отсутствие прогресса и отказ

`app/analysis/repair.py` выполняет пять из шести шагов цикла и **намеренно не
выполняет шестой**. Он обнаруживает отказ по записанным статусам, собирает
собственные коды доказательств шага, называет вероятную причину с **порядковой**
`Confidence` (`SUPPORTED` / `INFERRED` / `UNSUPPORTED`, определяемой тем, называет
ли причину *конкретный* записанный код доказательства — никогда не процент),
строит минимальную целевую перепроверку и читает требуемое одобрение из
собственных `ApprovalPolicy` и резолвера композиции. Затем он **ничего не
запускает** и говорит об этом: `dispatch_performed: false` и поле `dispatch_path`,
объясняющее, что допущенный repair должен был бы идти через существующую
авторизованную точку входа под новым ключом идемпотентности.

* **бюджет** — `attempt_cap` (по умолчанию 3, потолок 50), `step_budget` (по
  умолчанию 8, потолок 200) и `seconds_budget` (по умолчанию 900, потолок 86400).
  Вызывающий может понизить бюджет, но никогда не поднять его выше потолка;
  некорректное значение откатывается к значению по умолчанию, а NaN не является
  бюджетом (`test_45`);
* **предел попыток** — счёт берётся из собственного записанного `attempts` попытки,
  поэтому его нельзя сбросить повторным запросом. `test_42` планирует одну и ту же
  попытку с пределом 1 и с пределом 5 и показывает, что разница вызвана только
  пределом;
* **отсутствие прогресса** — `progress_marker` это SHA-256 **только наблюдаемых**
  величин (статус каждого шага, статус проверки, код выхода, коды доказательств,
  гарантия ledger и статус графа): ни отметки времени, ни счётчика, ни случайного
  значения, поэтому два наблюдения неизменной попытки дают одинаковый маркер.
  Возврат предыдущего маркера останавливает цикл с `stop_rule="no_progress"`.
  `progress_made` **трёхзначно**: `null`, когда предыдущее наблюдение не передано,
  потому что «предыдущего наблюдения нет» — это не факт «прогресса не было»;
* **`REFUSED_SIDE_EFFECT_UNKNOWN` остаётся отказом.** Когда измеренная гарантия
  повтора — `REFUSED_SIDE_EFFECT_UNKNOWN`, цикл возвращает этот исход без изменений
  и останавливается с `stop_rule="side_effect_outcome_unknown"`, ничего не выполняя.
  `test_41` получает эту гарантию из **реального** `RunIdempotencyGuard`: он
  захватывает идентичность операции, начинает не-replay-safe побочный эффект
  `HOST_PROCESS` и никогда его не завершает, требует, чтобы собственный
  `repeat_decision` охранника ответил `REFUSED_SIDE_EFFECT_UNKNOWN` (поэтому тест не
  может пройти впустую), и затем показывает, что цикл отказывает. Инъекция
  подтверждает: обход этой ветки делает `test_41` красным.

##### Два измеренных дефекта, найденных и исправленных этим блоком

* `OperatingSurface._repeat_guarantees` и `_operation_states` читали
  `runner.ledger_guard` **без вызова**. Это метод, поэтому каждый
  `repeat_decision` возбуждал `AttributeError`, пошаговый `except` его проглатывал,
  и цикл repair планировал против **полностью отсутствующей измеренной гарантии** —
  «ledger ничего не говорит» и «ledger не удалось прочитать» выглядели одинаково.
  Теперь оба вызывают его, а поведение закреплено `test_41c` и инъекцией.
* `POST /api/product/repair-plan` не передавал `previous_progress_marker` дальше,
  поэтому `NO_PROGRESS` был недостижим по HTTP, хотя правило уровня модуля было
  корректным. Теперь маршрут его передаёт; инъекция (повторное удаление аргумента)
  делает `test_43` красным.

##### Что здесь НЕ доказано

* **Ни один repair не выполняется.** Шестой шаг цикла на этой поверхности не
  реализован и сообщается как `dispatch_path`, а не симулируется. Ни один путь кода
  здесь не перезапускает шаг `FAILED`.
* **Проверки `changed-path:*` являются `UNKNOWN` по построению.** Они сообщают об
  отсутствующем объявленном ожидании, а не о результате теста.
* **`claimed_by_agent` всегда пуст в этой сборке**, потому что путь исполнения не
  делает генеративной диспетчеризации. Отсек показан пустым с этой причиной, а не
  опущен.
* Нет браузерного сквозного прохода; консоль проверяется через `TestClient` на
  границе HTTP. Нет пути к реальному провайдеру: весь набор структурно офлайновый.
* Замечание Block #8 о миграции контракта approval-policy остаётся в силе, как
  написано выше.

##### Доказательства

`tests/test_block9_evidence_and_verification.py`, 33 теста, три последовательных
изолированных прогона зелёные (`Ran 33 tests`, `OK`, exit 0). Fails-before доказан
инъекцией для десяти контрактов, каждый восстановлен побайтово идентично (SHA-256
совпадает до и после). Регрессия Block #8 перезапущена на изменённом дереве:
93 + 31 + 33 + 26 + 14 + 36 + 15 + 16 + 17 + 41 + 7 + 24 + 26 + 9 + 22 + 34 + 12 +
26 + 16 + 24 + 12 + 50 + 15 + 16 + 6 + 32 (1 skipped) + 35 + 6 тестов, все `OK`,
exit 0.

#### Project Memory на этой поверхности

Оператор может записать утверждение, но только на уровне `HYPOTHESIS` или
`UNKNOWN_DATA`: `CONFIRMED_FACT` и `VERIFIED_CONCLUSION` отклоняются с машинным кодом
`kind_not_recordable`, потому что подтверждённый факт обязан ссылаться на evidence fact
id, произведённый слоем understanding, а запрос не может его предоставить. Цель
записывается как гипотеза. `POST /api/product/memory/refresh` переоценивает каждую
**привязанную к файлу** запись относительно текущего workspace и переводит изменённый,
удалённый или переименованный источник в STALE с кодом причины; у записи без привязки
нет того, с чем сравнивать, и она честно исключается из проверенного количества, а не
сообщается как свежая. Память по-прежнему ничего не даёт:
`app/understanding/memory.py` не импортирует полномочия выполнения, и ни один маршрут не
выводит вход выполнения из записи памяти.

#### Проверенные ограничения этой поверхности

* **Изоляция маршрута объявленной верификации, измеренная.** `run_declared_verification`
  выполняет диспетчеризацию, не передавая `ExecutionCoordinator`, поэтому `RunExecutor`
  строит координатор по умолчанию — `LocalExecutionAdapter(...,
  isolate_workspace=True)` — и объявленная команда выполняется в **подготовленной
  копии**; workspace оператора остаётся нетронутым. `test_10` утверждает это, а `test_10b`
  утверждает контраст: шаг **графа шагов** действительно пишет в workspace оператора,
  потому что координатор этого раннера собран с `isolate_workspace=False`. Оба поведения
  задокументированы здесь, поскольку оба значимы для оператора, и ни одно не выдаётся за
  другое.
* **Маршрут графа шагов либо собран, либо отсутствует, но не «свободен».**
  `build_step_graph_runner` требует runtime с run executor **и явно внедрённый
  idempotency guard**. Процессный guard по умолчанию делит один ledger root между
  вызывающими, а в этом проекте уже измерялись ложные падения из-за общих корней по
  умолчанию, поэтому HTTP-маршрут графа шагов отказывает (`step_graph_unavailable`), а не
  строит такой guard. `test_02` и `test_29` фиксируют fail-closed поведение.
* **Защита от повтора исходит из долговечной записи, а не из внутрипроцессного флага.**
  Два маршрута с одним graph run id: второй возвращает записанную попытку с
  `replayed: true` и ничего не выполняет (`test_30`). **Записанный payload возвращается без изменений**: ответ никогда не переписывает запись прошлой попытки. Поля ответа (`replayed`, `executed`, `command_started`, `commands_started`, `recorded_result`) описывают *этот* вызов, а записи шагов внутри `result` описывают попытку, которая действительно выполнялась, с её настоящими значениями. Отключение
  `StepGraphRunner._claim_graph_run` **не** сломало этот тест, и находка зафиксирована, а
  не скрыта: claim — это защита от *внутрипроцессной конкурентности*
  (`tests/test_step_graph_managed_execution.py` покрывает её), тогда как вызывающего,
  спросившего дважды, останавливает запись. Это измеренная поправка к более раннему
  преувеличению.
* **`artifacts_are_harvested` на раннере графа шагов — ВЫВОДИТСЯ, никогда не
  утверждается.** Значение читается из собственного `harvests_artifacts` скомпонованного
  адаптера, а оно истинно, когда адаптер изолирует свои запуски **или** когда композиция
  объявила `harvests_artifacts=True`. Без истинного значения объявленная проверка
  `ARTIFACT_PRODUCED` на каждом шаге даёт `ERROR` (неоцениваема); harvesting записывает
  дайджест файла, который шаг уже объявил в `artifact_targets`, и ничего не даёт.
  Жёстко заданное `True` поверх адаптера, который ничего не собирает, хуже, чем `ERROR`:
  оно превращает честное «невозможно оценить» в ложный `FAILED` с
  `artifact_not_produced`, который затем блокирует все зависимые шаги. Измерено:
  `test_32b` (реально произведённый артефакт должен дать `PASSED` с измеренным дайджестом,
  а зависимый шаг — всё ещё выполниться) падает и когда объявление убрано, и когда флаг
  снова жёстко задан как `True`; `test_31` фиксирует обратное направление (exit 0, артефакта
  нет ⇒ `FAILED`); `test_an_unevaluable_artifact_check_is_an_error_not_a_failure`
  фиксирует `ERROR`, когда адаптер ни изолирует, ни объявляет harvesting.
* **Аутентификации нет.** См. выше: это loopback-поверхность для локального оператора.
* **Изоляции процессов нет.** Это текстовые и контрактные проверки внутри одного
  процесса операционной системы. Процесс, уже работающий от имени этого пользователя,
  ими не ограничивается, и ни один маршрут здесь не заявляет обратного.
* **Не проверено этим блоком:** сквозной проход, управляемый браузером (UI проверяется
  через `TestClient` на границе HTTP, а не через реальный браузер); визуальное и
  accessibility-тестирование; длительная работа консоли против живого сервера; и любой
  путь с реальным провайдером, поскольку весь набор тестов структурно офлайн.

* **ОТКРЫТО — миграция контракта об обязательной политике, которую упавший fix-агент не
  завершил.** Превращение `None` в безусловный отказ в `ExecutionCoordinator` (исправление
  D6, обязательное и верное) делает недействительными *фикстуры* каждого теста, который
  вызывал координатор напрямую без политики: измерено — **66 тестов в 12 файлах** падали, а
  возврат до-фиксового разрешающего поведения на один прогон-зонд сделал **все 12 файлов
  зелёными**, то есть единственная причина — этот guard, а не второй дефект. Эти 12 файлов:
  `test_credential_redaction_boundary.py`, `test_engineering_execution_integration.py`,
  `test_execution_authorization_hardening.py`, `test_execution_authorization_hardening_v02.py`,
  `test_execution_plane.py`, `test_execution_sandbox_boundary.py`,
  `test_integration_failure_matrix.py`, `test_local_execution_adapter.py`,
  `test_run_scope_contracts.py`, `test_security_hardening_block.py` и
  `test_test_verification_adapter.py` (плюс `test_workspace_root_always_checked.py`,
  `test_execution_timeout_and_tree_kill.py` и
  `test_execution_authorization_hardening_v03.py`, которые проход восстановления уже
  мигрировал). Миграция *в основном* механическая — `ApprovalPolicy()` возвращает
  `NOT_REQUIRED` для любого инструмента, поэтому для этих фикстур она поведенчески
  идентична старому разрешающему `None` и сохраняет предмет каждого теста, — но она **не**
  однородно механическая: наборы тестов про ужесточение одобрения содержат случаи, предмет
  которых — *какой именно* отказ даёт отсутствующий или неверный элемент
  (`test_missing_required_approval_policy_is_denied` в их числе), и они требуют
  индивидуального суждения, а не сплошной подстановки. В проходе восстановления она
  намеренно не выполнена: это общепроектная миграция контракта по дюжине закоммиченных
  файлов security-тестов, выходящая за разрешённую хирургическую область, а небрежный
  сплошной проход — именно то, как security-тест тихо ослабляют. **Gate 1 не может пройти,
  пока она не выполнена — намеренно и с поштучным ревью.**

### 3.6 Память и знания

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

### 3.7 Platform (Stage 1) — русская версия

Stage 1 строится упорядоченными шагами. Шаги с 1 по 3 реализованы; остальные — нет,
и ничто ниже не должно читаться как утверждение о них.

| Область | Статус | Evidence |
| --- | --- | --- |
| Доменные контракты Platform (8 записей, 8 enum'ов статусов, только stdlib, без драйвера) | **CURRENT / VERIFIED** | `app/platform/models.py`, `app/platform/enums.py` — `tests/test_platform_domain_contracts.py` |
| Схема PostgreSQL с миграциями, ролями, `FORCE` row-level security и append-only таблицами | **CURRENT / VERIFIED** | `app/platform/persistence/postgres/migrations/0001..0014_*.sql`, `postgres/schema.sql` — `tests/test_platform_postgres_schema.py` |
| Изоляция арендаторов, разделение ролей, сдерживание system-скоупа, append-only потребление, происхождение membership | **CURRENT / VERIFIED** | миграции `0002`, `0011`–`0014` — `tests/test_platform_postgres_security.py` |
| Репозитории, Unit of Work, три скоупа, нормализация ошибок, граница идентификаторов | **CURRENT / VERIFIED** | `app/platform/persistence/{database,repositories,mapper,protocols,errors}.py` — `tests/test_platform_persistence.py` |
| Два логина, по одному скоупу на каждый, доказанные по каталогу ролей, а не по договорённости | **CURRENT / VERIFIED** | `app/platform/persistence/database.py` — `tests/test_platform_persistence.py` |
| Обнаружение aborted-транзакции: перехваченная ошибка не может быть закоммичена молча | **CURRENT / VERIFIED** | `app/platform/persistence/database.py` — `tests/test_platform_persistence.py` |
| Discovery, привязанный к субъекту, один субъект на транзакцию | **CURRENT / VERIFIED** | миграция `0015`, `app/platform/persistence/database.py` — `tests/test_platform_persistence.py`, `tests/test_platform_postgres_security.py` |
| Колоночно-ограниченная запись идентичностей: непересекающиеся права на скоуп | **CURRENT / VERIFIED** | миграция `0015` — `tests/test_platform_persistence.py`, `tests/test_platform_postgres_security.py` |
| Compare-and-set переходы жизненного цикла запуска | **CURRENT / VERIFIED** | `app/platform/persistence/repositories.py` — `tests/test_platform_persistence.py` |
| Прикладные сервисы Platform поверх этих репозиториев | **PLANNED / NOT IMPLEMENTED** | такого модуля не существует; `app/platform/persistence/protocols.py` — интерфейс, которым они будут пользоваться |
| Граница доверия аутентификации: `AuthenticatedPrincipal`, порт верификатора, fail-closed отказы | **CURRENT / VERIFIED** (только контракт; механизма креденлов нет) | `app/platform/principal.py` — `tests/test_platform_auth_contracts.py` |
| Механизм аутентификации (сессия / JWT / федерация) | **PLANNED / NOT IMPLEMENTED** | открытое решение O-2; порт определён, реализация не поставляется |
| Срез Application Services: discovery AS-1 и список проектов AS-2, алгоритм авторизации, словарь прикладных ошибок | **CURRENT / VERIFIED** | `app/platform/application/` — `tests/test_platform_application_discovery.py` |
| Application Services AS-3..AS-9 (создание проекта, подготовка запуска, `RunRecord`, порт Platform -> Core) | **DESIGNED / NOT IMPLEMENTED** | `docs/STAGE-1-STEP-4-AUTH-APPLICATION-SERVICES-DESIGN.md`, раздел 5 |
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

### 3.8 Материализация проектов, локальный запуск и предпросмотр

| Область | Статус | Доказательство |
| --- | --- | --- |
| Материализация в собственную рабочую область проекта | **CURRENT / VERIFIED** | `app/projects/materialize.py` (вызывает `app/agent_runtime/code_writer.apply_changes`; решают граница и deny-list писателя) — `tests/test_materialization.py` |
| Точка входа только из манифеста артефактов | **CURRENT / VERIFIED** | `app/projects/entrypoint.py` — `tests/test_materialization.py`, `tests/test_local_execution.py` |
| Запускаемые виды (python script/module, node script, объявленный npm-скрипт) | **CURRENT / VERIFIED** | `app/projects/entrypoint.py` — `tests/test_local_execution.py`; `app/smoke_python_preview.py`, `app/smoke_node_preview.py` |
| Статическая страница запускаема (`runnable: true`, `run_command` = `открыть в браузере (preview)`) через существующий маршрут предпросмотра | **CURRENT / VERIFIED** | `app/projects/entrypoint.py` (`STATIC_RUN_COMMAND`, `runnable`), `app/projects/presentation.py` (карточка предлагает оба действия запуска), `ui/src/screens/Projects.tsx` (кнопка запуска в панели запуска вызывает маршрут предпросмотра) — `tests/test_materialization.py`, `tests/test_project_truth_presentation.py`; `tsc --noEmit`, `vite build` |
| Эффективный режим записи в записи запуска (`project_state["write_mode"]`) и отсутствие шаблона для реального провайдера | **CURRENT / VERIFIED** | `app/api/service.py` (`WRITE_MODE_PROJECT_STATE_KEY`, `NO_FILES_FROM_MODEL_SENTENCE`), `app/projects/mock_answer.py` (`deterministic_answer_for_provider`) — `tests/test_write_output_contract.py`, `tests/test_projects_change_run.py` |
| Одобрение без диспетчеризации (`ExecutionCoordinator.authorize`) | **CURRENT / VERIFIED** | `app/execution/authorizer.py` — `tests/test_execution_authorization_hardening*.py`, `tests/test_local_execution.py` |
| Управляемый локальный процесс (состояния, остановка дерева, таймаут, ограниченный очищенный лог) | **CURRENT / VERIFIED** | `app/projects/runner.py` — `tests/test_local_execution.py`, `tests/test_run_sandbox.py` |
| Содержание процесса проекта в job object (kill-on-close, лимит памяти, лимит активных процессов) | **CURRENT / VERIFIED** | `app/execution/containment.py`, `app/execution/adapter.py` (`set_process_tree_job_limits`), `app/projects/runner.py` — `tests/test_run_sandbox.py` |
| Токен низкой целостности для процесса проекта с сообщением измеренного уровня | **CURRENT / VERIFIED** | `app/execution/containment.py` (`lower_process_integrity`, `process_integrity_level`) — `tests/test_run_sandbox.py`; payload сообщает `restricted_token`/`integrity` и `detail.integrity_level` |
| Измеренная граница записи процесса проекта (`writes_outside`) | **CURRENT / VERIFIED (измерение, а не гарантия)** | `app/execution/containment.py` (`probe_write_boundary`) — `tests/test_run_sandbox.py`; значение `allowed`, когда данные самого Forge несут ту же метку целостности, что и ребёнок |
| Настроенное максимальное время жизни обслуживающего процесса | **CURRENT / VERIFIED** | `LocalRunner(server_lifetime=…)` через `FORGE_SANDBOX_SERVER_LIFETIME_S`, по умолчанию нет — `tests/test_run_sandbox.py` |
| Общее уничтожение дерева процессов (job object / Toolhelp / `taskkill /T`) | **CURRENT / VERIFIED** | `app/execution/adapter.py` (`terminate_process_tree`) — `tests/test_execution_timeout_and_tree_kill.py`, `tests/test_local_execution.py` |
| Процессный предпросмотр работающего сервера | **CURRENT / VERIFIED** | `app/projects/preview.py` (`register_process`, наблюдаемый слушающий порт) — `tests/test_local_execution.py`, `tests/test_materialization.py` |
| Маршруты локального запуска и предпросмотра | **CURRENT / VERIFIED** | `app/api/project_routes.py` — `tests/test_local_execution.py` |
| Открытие проекта в его собственном окне (`POST /api/projects/{id}/launch`, только наблюдаемый порт, без консольного окна) | **CURRENT / VERIFIED** | `app/projects/launcher.py`, `app/projects/app_window.py`, `app/api/project_routes.py`, `app/projects/runner.py` (`CREATE_NO_WINDOW`), `app/execution/containment.py` (probe) — `tests/test_project_launch.py` |
| Порт запуска и прогона обнаруживается по выводу самого процесса плюс по сокетам его pid-дерева, и каждый кандидат проверяется настоящим loopback-`GET /`; публикуется `observed_port`, а `notice` называет заявку манифеста при расхождении | **CURRENT / VERIFIED** | `app/projects/ports.py`, `app/projects/runner.py` (`_discover_port`, `_answering_output_port`, `_finish`), `app/projects/launcher.py` (`_launch_notice`), `app/api/project_routes.py` (`_manifest_port`) — `tests/test_project_launch.py` (`PortDiscoveryTests`, `test_12`, `test_13`) |
| Ребёнок, завершившийся с кодом 0, при том что объявленный им адрес уже отвечает, — успех с `reused: true` (уже запущенный экземпляр), а не отказ | **CURRENT / VERIFIED** | `app/projects/runner.py` (`_finish`), `app/projects/launcher.py` — `tests/test_project_launch.py` |
| Процесс, не открывший порт, — типизированный отказ с состоянием, кодом выхода и очищенным хвостом журнала; статическая страница предлагается как `fallback_available`/`fallback_url` и показывается только с `{"fallback": true}` | **CURRENT / VERIFIED** | `app/projects/launcher.py` (`_port_refusal`, `looks_like_console_prompt`), `app/api/project_routes.py` — `tests/test_project_launch.py` |
| Слова этапа `READY_TO_RUN` / `RUNNING` / `STOPPED` / `BLOCKED` | **CURRENT / VERIFIED** | `app/projects/stage.py` — `tests/test_local_execution.py`, `tests/test_materialization.py` |
| Блок запуска и шаг подтверждения в карточке проекта | **CURRENT / VERIFIED** (проверено типами и сборкой; проверено по HTTP, но не автотестом интерфейса) | `ui/src/screens/Projects.tsx`, `ui/src/api/forge.ts` — `tsc --noEmit`, `vite build` |
| Сетевая изоляция процесса проекта | **REJECTED для этого блока** — дочерний процесс наследует сеть машины; ответ говорит `network_inherited` | `DECISIONS.md` Local run and preview v0.2 |
| Установка зависимостей (`pip install` / `npm install`) как часть запуска | **REJECTED** — отсутствующая зависимость сообщается как `RUN_BLOCKED` со следующим шагом | `DECISIONS.md`, `app/projects/runner.py` |

**Честные границы.** Супервизор их называет, а не скрывает: дочерний процесс **не** изолирован от
сети (`network_policy: "inherited"`, `sandbox.network: "inherited (not isolated)"`), содержание — это
**job object Windows плюс токен низкой целостности**, а не kernel-level файловая песочница:
`writes_outside` — *измеренный* ответ, и он честно равен `allowed`, когда данные самого Forge несут
ту же метку целостности, что и дочерний процесс (так бывает, когда сам Forge работает при низкой
целостности), лог — это ограниченный захват 64 КиБ на поток (дальнейший вывод отбрасывается с
маркером), URL предпросмотра существует только пока процесс жив, а процесс, переживший жёсткое
убийство процесса API, после перезапуска не отслеживается (состояние в памяти и начинается с
`NOT_RUN`).

## 4. Проверенные ограничения

Это текущие наблюдаемые свойства реализации. Они зафиксированы, чтобы будущий
агент не открывал их заново и чтобы ни один документ не утверждал иного.

### 4.1 Проверка fingerprint одобрения безусловна (исправлено)

**Ограничение закрыто в обеих половинах этого документа и сохраняется только как
запись.** Ранее здесь утверждалось, что координатор проверяет возвращённый fingerprint
«только когда resolver передал непустой», и цитировался код
`if res_fp and res_fp != intent.fingerprint:` с комментарием, что пустой fingerprint
закорачивает проверку. Это было верно; сейчас это не так.

Коммит `1220398` ("security: close execution authority hardening block") заменил этот
блок. Проверка в `app/execution/authorizer.py` теперь читается так:

```python
if not resolution.approved_fingerprint or resolution.approved_fingerprint != intent.fingerprint:
    return self._deny(..., reason="intent_fingerprint_mismatch")
```

Resolver, возвращающий голый `ApprovalState.APPROVED`, неверный fingerprint или объект
неожиданного типа, отклоняется. Регрессионный тест
`tests/test_execution_authorization_hardening_v03.py::test_09_empty_approved_fingerprint_denied_fail_closed`
утверждает противоположное старому задокументированному результату пробы.

**Статус: ИСПРАВЛЕНО коммитом `1220398`.** Пункт ошибочно всё ещё числился кандидатом
на усиление.

### 4.2 Классификация capabilities: fail-closed для нераспознанного argv, с остатком

**Частично исправлено.** Прежний текст утверждал, что `ExecutionCapability` содержит
четыре члена, а «всё, что вне этих списков, получает `capabilities == frozenset()`, и
тогда политика допускает строковую запись `allowed_commands` с **произвольным argv**».
Коммит `693dac5` добавил **пятый** член, `OPAQUE`
(`app/execution/capabilities.py:17`), и `classify_invocation` теперь добавляет его
всякий раз, когда инвокация не распознана и несёт аргументы (`:272-274`).
`app/execution/policy.py:111-117` отклоняет такую инвокацию с `argv_not_authorized`,
если профиль не предоставляет `OPAQUE`, а `capabilities` по умолчанию равно пустому
множеству (`app/execution/profile.py:70`). Fail-closed-половина модели
declared-invocation, таким образом, реализована, и она покрыта
`tests/test_execution_authorization_hardening_v03.py`.

**Что действительно остаётся открытым** — и это более узкое утверждение, чем раньше:

- распознавание по-прежнему основано на **списке имён**. `_KNOWN_STANDARD_TOOLS`
  (41 имя) плюс `_WRAPPER_EXECUTABLES` (17) и `_NETWORK_EXECUTABLES` (17) решают, что
  считается распознанным, а `_KNOWN_STANDARD_TOOLS` классифицирует известные
  инструменты вроде `make`, `rm`, `sed` и `tar` в **пустое** множество capabilities,
  поэтому они не требуют явного предоставления capability. Риск сместился с
  «неизвестный wrapper не ограничен» на «известный инструмент ограничен слишком
  слабо», что является реальным остатком, а не незакрытой дырой.
- счётчики в прежнем тексте были неверны: `_NETWORK_EXECUTABLES` содержит 17 записей,
  а не 16, и в enum пять членов, а не четыре.

**Статус: PARTIAL.** `OPAQUE` закрывает fail-closed-разрыв; остаток списка
распознавания зафиксирован в `DECISIONS.md` ("Execution Authorization Contract v0.2").

### 4.3 Окружение профиля входит в fingerprint, но не передаётся дочернему процессу (исправлено)

**Ограничение закрыто и сохраняется только как запись.** Ранее здесь утверждалось, что
адаптер накладывает окружение **профиля** при старте процесса, и что изменение
`ProjectExecutionProfile.environment_variables` поэтому не меняет fingerprint интента.
Обе половины были неверны, причём в противоположных направлениях:

- окружение профиля **является** полноценным полем интента
  (`app/execution/intent.py:35`), заполняется в `:209-210` и **входит** в fingerprint в
  `:50-52`; `tests/test_execution_authorization_hardening_v03.py` утверждает, что его
  изменение меняет fingerprint;
- адаптер копирует в дочерний процесс **только** `intent.environment_variables`
  (`app/execution/adapter.py:298-299`). Значения окружения профиля — это список
  авторизации, а не полезная нагрузка, доставляемая процессу.

**Статус: ИСПРАВЛЕНО коммитом `693dac5`.** Вторая половина теперь описана как
предусмотренное поведение, а не как дефект, потому что так и задумано: профиль объявляет
*значения, которые может получить объявленная команда*, и объявление должно совпадать с
ними точно. Доставка — задача запроса.

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
маскированием секретов. Он **не** предоставляет namespaces, cgroups, Job Objects как границы изоляции
или файловое ограничение на уровне ядра. Дочерний процесс наследует security token
родителя и может открыть любой доступный этому токену путь; ребёнок, открывающий
абсолютный путь вне workspace, не останавливается. Этот остаточный риск записан в
`RESIDUAL_RISK` и доказан тестом
`tests/test_execution_sandbox_boundary.py::test_child_process_can_still_read_outside_the_workspace`.

**Block 2 разделён на фактические части.** Файловая/workspace-граница —
**PARTIAL/IMPLEMENTED**, как выше. OS-level песочница и изоляция остаются
**DEFERRED**. Подготовка/mirroring входов workspace остаётся **PARTIAL** (см. §4.5).
Прежнее утверждение, что Block 2 «НЕ реализован», было устаревшим и удалено.

**Один вид процессов теперь действительно имеет OS-level содержание.** Процесс проекта,
который запускает локальный супервизор (`app/projects/runner.py`), создаётся внутри job object
Windows с `KILL_ON_JOB_CLOSE`, лимитом памяти на процесс и лимитом активных процессов, а его
primary token понижается до низкой целостности до запуска (`app/execution/containment.py`,
`tests/test_run_sandbox.py`). Это не kernel-level файловая песочница: дочерний процесс по-прежнему
читает то, что позволяет его токен, и по-прежнему может писать данные самого Forge, когда они несут
ту же низкую метку (payload тогда сообщает `writes_outside: "allowed"`). Токены, которыми пользуется
сама плоскость исполнения для шагов сборки, и все остальные подсистемы не изменились: утверждение
«не реализовано» в этом разделе — про них, а не про локальный запуск.

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

`EphemeralWorkspaceManager` копирует переданный корень workspace во временный
scratch-каталог. **Не** копируются: `.git`, `__pycache__`, `*.pyc` и каждый путь,
который отвергает общий распознаватель секретов по именам (см. §4.10 — сам фильтр и его
остаток). Нет белого списка объявленных входов и нет digest содержимого для referenced
inputs. Frozen `RunScope` теперь связывает workspace через canonical path и
`WorkspaceIdentity`. Что вызывающий передал как `workspace_root`, то
и копируется, за вычетом этих исключений.

**Статус: PARTIAL** (объявленные входы и digest содержимого остаются отложенными;
привязка идентичности workspace реализована и проверена).

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

### 4.9 У Core API нет аутентификации, и launcher об этом говорит

Поверхность Core API (`app/api/server.py`) **не имеет аутентификации**: ни один маршрут
не требует учётных данных и не проверяет идентичность. Она существует для локального
доверенного оператора; работа Platform над аутентификацией покрывает другую
поверхность. В этом проходе изменены три вещи, и одна намеренно не изменена:

- CORS больше не использует `allow_origins=["*"]` вместе с `allow_credentials=True`.
  Allow-list явный и локальный по умолчанию, credentialed CORS выключен и не может быть
  включён из этого модуля, а `*` в `FORGE_API_CORS_ORIGINS` **отклоняется**, а не сужается
  молча, потому что оператор, написавший `*`, считает поверхность открытой.
- `app/api/main.py` — launcher. Раньше его не было: `create_api_app` существовал, тесты
  его импортировали, но ничто не привязывало хост, — именно поэтому разрешающий CORS
  пережил ревью. По умолчанию он привязывается к loopback и отказывает до запуска, если
  запрошен другой адрес, поскольку у поверхности нет аутентификации.
- Аутентификация **не изобретена**. Добавление здесь механизма идентичности — открытое
  решение O-2, и оно создало бы вторую модель доверия рядом с моделью Platform.
  Остаточный риск зафиксирован здесь, а `tests/test_api_exposure.py::test_14` утверждает,
  что ни один маршрут не получил зависимости аутентификации, поэтому ситуация не может
  измениться молча ни в одну из сторон.

**Статус: PARTIAL — требуется решение владельца.** Либо Core API получает слой
аутентификации, либо он не должен выводиться за loopback, либо продуктовой поверхностью
становится Platform, а этот API остаётся внутренним.

### 4.10 Подготовка секретов — это фильтр по именам, а не allow-list объявленных входов

Staging-каталог исполнения удерживает файлы, **имя** которых помечает их как credential
material, и обрезает каталоги учётных данных до спуска в них. Содержимое файлов не
читается: чтение файла для его классификации было бы той самой утечкой, которую этот код
предотвращает.

**Один распознаватель отвечает за все обходы.** Правила живут в
`app/execution/secret_files.py`, и в него вызывают как шаг staging исполнения
(`app/execution/workspace_manager.py`), так и сканер проекта
(`app/understanding/scanner.py`) — `is_secret_file` для файлов и `is_secret_directory`
для каталогов. Прежде сканер держал собственный набор `_IGNORED_DIRS`, называвший только
артефакты сборки, поэтому он спускался в `.terraform`, `keys`, `certs`, `.ssh` и
подобные каталоги и инвентаризировал файлы, имена которых не несут секретных маркеров.
Это не было раскрытием — распознанные файлы записываются с `fingerprint=None` и никогда не
читаются, — но это расходовало лимит числа файлов на содержимое, не являющееся исходниками,
и означало, что два обхода расходятся в том, что считать территорией учётных данных.

**Обрезаемые каталоги:** `.ssh`, `.aws`, `.gnupg`, `.kube`, `.docker`, `.azure`,
`.terraform`, `.terraform.d`, `keys`, `certs`, `private`. Каталог обрезается до спуска,
поэтому ничего внутри него не исследуется.

**Распознаваемые имена, расширения и шаблоны.** Точные имена включают `.env`, `.envrc`,
`credentials[.json]`, `service_account.json`, `application_default_credentials.json`,
`aws_credentials`, `kubeconfig`, `secrets.{yaml,yml,json}`, `secret.{yaml,yml,json}`,
`terraform.tfstate[.backup]`, `.git-credentials`, `.gitconfig`, `.terraformrc`, `.netrc`,
`.pgpass`, `.npmrc`, `.pypirc`, `.htpasswd`, `htpasswd`, `authorized_keys`, `cacerts`,
`keystore`, `secring.gpg`. Шаблоны покрывают `firebase-adminsdk-*.json`, `*.tfstate[.backup]`
и **зашифрованный ключевой материал** (`*.pem.enc`, `*.key.enc`, `*.p12.enc`, `*.pfx.enc`).
Расширения включают `.pem`, `.key`, `.pfx`, `.p12`, `.pkcs12`, `.kdbx`, `.jks`, `.keystore`,
`.ppk`, `.asc`, `.gpg`, `.ovpn`.

Правило `.pem.enc` стоит назвать отдельно. Проверяемое утверждение — только то, что это
правило **достижимо сейчас**: оно проверяется до правила расширений, поэтому
`server.pem.enc` распознаётся, а `notes.enc` и `archive.tar.enc` по-прежнему возвращают
`None` — правило соответствует зашифрованному ключевому материалу, а не всему, что
оканчивается на `.enc`. Утверждать, что правило было «недостижимым до этого прохода»,
нельзя: `app/execution/secret_files.py` не отслеживается Git, и `*.pem.enc` не встречается
в `HEAD`, поэтому проверить прежнее поведение нечем.

**Намеренно не секреты**, потому что отказ от них — ложное срабатывание, ничего не
защищающее: коммитируемые шаблоны (`.env.example`, `.env.sample`, `.env.dist` и любое имя
с таким сегментом, разделённым точками), **публичная** половина пары ключей (`*.pub`,
поэтому `id_rsa.pub` проходит, а `id_rsa` — нет) и `known_hosts`, который является
публичными данными о ключах хостов. Правило `.pub` — это проверка суффикса, а не общее
освобождение: `notes.txt.pub` проходит, потому что он никогда не был секретом, и ни один
файл не становится похожим на секрет от добавления этого суффикса.

**Нормализация явная, и платформенная зависимость названа.** Windows разрешает
`secret.pem.` и `secret.pem ` в `secret.pem`, поэтому завершающие точки и пробелы
снимаются до сопоставления; иначе один символ обходил правило расширений. Регистр
приводится к нижнему. И то и другое измерено, а не предположено, и поведение с
завершающей точкой — свойство Windows; тесты это фиксируют, а не подразумевают, что так
везде.

**Что по-прежнему не распознаётся, сказано прямо:** секрет внутри обычного имени
(`notes.txt`) **не** ловится, и он **читается** и получает отпечаток, если лежит в обычном
каталоге. `tests/test_execution_secret_staging.py::test_27` утверждает это ограничение, а
не скрывает его. Полное решение — allow-list объявленных входов с deny-by-default
staging — остаётся отложенным (Block 2).

**Статус: PARTIAL.** Измеренные к настоящему моменту утечки — `.env`, ключевые файлы,
credential JSON, двадцать два имени, найденных повторным аудитом, зашифрованный ключевой
материал и обход через завершающую точку — закрыты, и staging измерен end-to-end: при
наличии в исходном дереве всех удерживаемых имён и всех каталогов учётных данных ни один
из них не достиг staging-каталога. Deny-list не может быть полным по построению, поэтому
остаточный случай выше задокументирован, а не снят умолчанием.

### 4.11 `Tcl_AsyncDelete` больше не теряет сводку однопроцессного прогона

Однопроцессный `unittest discover` прежде прерывался на teardown с
`Tcl_AsyncDelete: async handler deleted by the wrong thread`, теряя сводку `Ran N tests`.
Проверено на чистом checkout `d1c007a` в отдельном worktree: он сообщал
`Ran 1766 tests` и неуспешный код возврата при усечённой сводке.

**В одной форме вызова это всё ещё воспроизводится (измерено на `d1c007a` плюс это
рабочее дерево):** сводка формируется при verbosity 1 и 2 и теряется при verbosity 0 по
умолчанию, где падение происходит до сброса stdout. Запускайте

```
python -X utf8 -u -m unittest discover -s tests -t tests -v
```

чтобы получить сводку в одном процессе: **`Ran 1971 tests ... OK (skipped=36)`** (1967
пройдено, 4 пропущенных тестовых метода, 0 падений, 0 ошибок). Само число не зависит от
формы вызова — инструментированный `unittest.TextTestResult` сообщает те же числа
1971 / 1967 / 4 / 2206 собранных при verbosity 0, — поэтому от формы вызова зависит
только печатаемая сводка, а не результат набора. Прежние редакции этого файла приводили
`Ran 1841 tests ... OK (skipped=35)` без DSN и `Ran 2076 tests ... OK (skipped=3)` с живым
PostgreSQL; эти цифры были верны для своего дерева и больше не воспроизводимы, потому что
дерево выросло. Прерывание — взаимодействие окружения и teardown, а не дефект продукта, и
оно зафиксировано, потому что делает форму сводки зависимой от verbosity. Сами числа
сверены в §4.16.

**Статус: PARTIAL — сводка выживает при verbosity 1 и выше; verbosity 0 всё ещё
прерывается на teardown уже после того, как тот же результат вычислен.** Два падения, на
которые ссылался этот раздел, исчезли; см. §4.12.

### 4.12 Диспетчеризация пытается использовать настроенный провайдер по умолчанию (исправлено)

`tests/test_api.py::test_07_task_run_via_api` и
`tests/test_desktop.py::test_04_home_view_task_dispatch` прежде падали с
`Provider fallback chain exhausted`. Сначала они были записаны здесь как environmental,
и это было неверно в важной части: триггером был локальный леджер токенов, но дефект
находился в маршрутизации.

**Корневая причина.** Для специализированной категории план попыток строился только из
статического списка предпочтений политики:

```python
elif cost_aware:
    attempts = list(decision.candidates)   # code -> openrouter, deepseek, openai, anthropic, groq
```

`mock` в этом списке отсутствует, а ветка, добавляющая `default_provider`, выполняется
только для неспециализированных категорий. Поэтому деплой, у которого
`default_provider` был `mock`, **никогда не пытался** использовать явно настроенный
провайдер. Решающее измерение: при установленном только mock задача категории `code`
падала с
`Provider fallback chain exhausted: openrouter: provider is unavailable or not registered`.
`TaskRunRequest().category` по умолчанию равен `code`, то есть это основной путь
продукта.

**Исправление.** `Dispatcher._with_default_provider` добавляет настроенный default, если
его нет, — после тех же проверок допуска, которые проходит любой другой кандидат
(`_automatic_route_rejection`: зарегистрирован, capabilities зарегистрированы, включён
конфигурацией, не запрещённый платный, имеет объявленный ключ), плюс проверки, что это
зарегистрированный **агент**, а не только зарегистрированный провайдер. Порядок
кандидатов политики не изменён, `DispatchPolicy.decide` не изменён; default выполняется
последним.

**Тестовый шов.** Оба теста прежде работали против поставляемой конфигурации
провайдеров, поэтому их исход зависел от окружения разработчика и при наличии ключа они
могли обратиться к живому провайдеру. Теперь они собирают runtime с
`enabled_providers=("mock",)`, что делает офлайн-провайдера единственным кандидатом и
убирает внешнюю зависимость вместо её маскировки.

**Статус: FIXED.** Оба теста проходят, полный набор не сообщает падений. Остающийся
пробел для владельца — `TaskRunResponse.provider_used` по-прежнему пуст по другой
причине, см. §4.13.

### 4.13 Отказ сообщает причину из терминального Run, а не из событий

В извлечении исхода запуска было два независимых дефекта, и они исправлены вместе,
потому что каждый маскировал другой.

**Отказ определялся сканированием событий.** Отклонённая *промежуточная* попытка тоже
испускает `provider_result(success=False, error=...)`, поэтому **успешный** запуск, у
которого первое предпочтение было отклонено, сообщался как падение с устаревшим
сообщением. Наблюдалось: завершённый запуск, вывод которого был ответом mock-провайдера,
сообщал `error="provider is disabled by configuration"`.

**Затем причина читалась не из того поля.** Первое исправление читало отказ только из
`run.result`. Но `RunExecutor` присваивает `run.result` внутри блока `try`, а его общий
`except` заполняет только `run.error`, поэтому провайдер, бросающий исключение, которое
`ProviderAgent` не транслирует в `ProviderError`, оставляет `run.result is None` при
заполненном `run.error`. Это давало `success=False, error=None`: упавший запуск с молча
потерянной причиной — хуже поведения, которое он заменял, и достижимо однострочным
двойником провайдера.

**Исправление.** Причина берётся из `run.error`, который executor заполняет на каждом
пути отказа; `run.result` даёт успех и вывод. События используются только для
человекочитаемого вывода. `tests/test_dispatcher.py::FailedRunReportsItsReasonTests`
утверждает наблюдаемый контракт — упавший запуск называет причину, успешный не сообщает
ошибки, — и он падает на промежуточной версии этого кода.

**`provider_used` и `model_used` возвращаются (исправлено).** Прежде они всегда были
`""`: `ForgeApiService` создавал свой `CapabilityFabric` **после** разрешения runtime,
поэтому оркестратор получал `fabric=None`, `record_usage` диспетчера не срабатывал, и
ответ читал пустой список `attempt_records`. `CostDashboardService` читает тот же объект,
поэтому Dashboard не видел ни одного запуска.

Исправление находится только в `app/api/service.py`: fabric создаётся первым и
передаётся в `create_runtime(fabric=...)`, а `bind_fabric_to_runtime` привязывает именно
этот объект к оркестратору, который будет выполнять диспетчеризацию, — это покрывает и
runtime, переданный вызывающим, поскольку такой runtime тоже строится без fabric.
`Orchestrator` не хранит атрибут `model_registry`, поэтому это единственное значение
читается из заменяемого диспетчера. Изменения в `app/runtime/bootstrap.py`, который уже
принимал `fabric`, и в `app/orchestrator/dispatcher.py`, который уже записывал usage при
его наличии, не потребовались.

**Изменение наблюдаемое, а не молчаливое:** `GET /api/dashboard` теперь агрегирует
реальные запуски вместо пустого набора.
`tests/test_dashboard.py::DashboardObservesApiRunsTests` утверждает, что Dashboard видит
запуск, которого прежде видеть не мог, а
`tests/test_api.py::test_11_service_and_runtime_share_one_fabric` утверждает тождество
объекта, которое это делает достижимым.

**Статус: FIXED (оба дефекта извлечения).** Оба поля атрибуции заполняются, а тот факт,
что успешный провайдер — последняя записанная попытка, утверждается, а не
предполагается (`tests/test_dispatcher.py::AttemptAccountingTests`).

**Атрибуция берётся из терминального результата, а не из списка попыток.** Чтение
провайдера из `attempt_records[-1]` было неверным всякий раз, когда запуск не имел ни
одного успеха: диспетчер пробует всех кандидатов и затем возвращает
`TaskResult(provider=selected, ...)`, называя **предпочтительного** провайдера, тогда как
последняя запись попытки называет последнего кандидата, которого он успел попробовать.
Измерено с удалённым mock-агентом: `run.result.provider == 'openrouter'`, а последняя
попытка — `'groq'`, то есть упавший запуск приписывался провайдеру, которого диспетчер
ответственным не считал. При успехе оба источника совпадают по построению: диспетчер
возвращается немедленно на первом успехе, и ни одна попытка не может следовать за
успешной. Теперь `provider_used`, `model_used` и вывод `code` читаются из `run.result`, а
`attempt_records` сохранён только как запасной источник для запуска, результат которого
недоступен
(`tests/test_api.py::test_13_a_failed_run_is_attributed_to_the_terminal_result`).

**У запуска со статусом `FAILED` всегда есть причина.** `RunExecutor` присваивает
`RunState.FAILED` ровно в двух местах, и обоим непосредственно предшествует присваивание
`run.error`, поэтому статус подразумевает заполненный `run.error`. Достижимая форма —
исключение, выброшенное до присваивания `run.result` (там исполняется сборщик контекста),
которое оставляет `run.result is None` при заполненном `run.error`; API сообщает эту
причину. Защитная ветка не добавлена, потому что ни один путь не производит `FAILED` без
неё.

**Ожидание одобрения отдаётся как `success=False`, и это существующий контракт.** Путь
declared-verification уже утверждает `success=False` с `error == "approval_waiting"` рядом
с `state == "WAITING_FOR_APPROVAL"` (`tests/test_api_declared_execution.py`), поэтому
здесь `success` означает «не завершено», а не «что-то сломалось». Внутри `run_task` путь
host-исполнения несёт собственную описательную причину, а tool-путь — ответ которого был
бы `success=False, error=None` — **недостижим через API в поставляемой конфигурации**:
`_effective_tool_ids()` пуст, если сервис не создан с `tool_intents`, поэтому ни один
инструмент не может быть вызван. Изменение того, как представляется ожидание одобрения, —
решение владельца об API-контракте, а не дефект для молчаливого исправления. Desktop-вид
отображает любой неуспешный ответ как `[Result: FAILED]`, поэтому оператор, читающий
только эту строку, не отличит «ожидает одобрения» от «упало»; различает их поле `state`.

### 4.14 TEST-runtime не может дойти до живого провайдера (реализовано, структурно)

**Дефект.** Безопасность набора тестов зависела от того, вспомнит ли каждый тест
подставить offline-runtime. `ForgeApiService()` строит свой runtime через
`app.runtime.bootstrap.create_runtime`, который без аргументов вызывает
`load_settings()` и потому читает реальный, gitignored `.env` разработчика. Поставляемый
`.env.example` включает `mock,openrouter`, в `.env` разработчика присутствуют имена девяти
живых ключей провайдеров, а `create_runtime` включает каждого провайдера, названного в
`enabled_providers`. Одна забытая подстановка, следовательно, доходит до сети и тратит
реальные деньги. Это измерено, а не предположено: `tests/test_desktop.py::test_04` дошёл до
OpenRouter и вернул 586 реальных токенов.

**Архитектурная гарантия.** У `create_runtime` теперь есть явное понятие TEST-runtime,
построенное на уже существовавшем переключателе, а не на втором:

* `create_runtime(..., test_mode=True)` **или** `FORGE_ENV=test` в окружении делает
  композицию TEST-runtime. `ApplicationEnvironment.TEST` уже существовал, и `FORGE_ENV`
  уже его выбирал — член просто был инертным. Ни один из сигналов не является значением по
  умолчанию: без `FORGE_ENV` конфигурация — `development`, а `test_mode` по умолчанию
  `False`, поэтому production-композиция не изменена ни на байт
  (`tests/test_offline_runtime_guarantee.py::test_01c_production_composition_is_unchanged`
  утверждает, что полный реестр из девяти провайдеров по-прежнему строится).
* TEST-runtime **всегда** сужает `RuntimeSettings`: `default_provider` становится offline
  провайдером, `enabled_providers` становится `(OFFLINE_PROVIDER_NAME,)`,
  `provider_fallback_chain` очищается, а `allow_paid_providers` принудительно выключается.
  Это применяется и к настройкам, переданным вызывающим, поэтому вызывающий не может
  передать настройки, заново включающие живого провайдера в test-режиме.
* На **ambient**-пути — без аргумента `settings`, то есть значения берутся из окружения
  процесса и `.env` — TEST-runtime дополнительно конструирует **только** тех провайдеров,
  которых он включает. У живого провайдера тогда нет ни агента, ни метаданных
  capabilities, ни записи в реестре, а значит и транспорта, поэтому он недостижим ни
  автоматической маршрутизацией, ни настроенной цепочкой fallback, ни явным запросом
  `provider_name`.

**Почему одного флага конфигурации недостаточно и что не обходится.** Выключение флага
`enabled` у живого провайдера — **не** достаточная граница, и набор тестов говорит это
явно: `tests/test_dispatcher.py::test_explicit_provider_keeps_priority_over_auto_availability`
закрепляет, что явно названный провайдер доходит до исполнения **несмотря** на то, что
выключен конфигурацией. Поэтому выключение в конфигурации всё ещё оставляет достижимый
транспорт; недостижимость создаёт именно отсутствие регистрации. Промежуточная редакция
этого прохода добавила в диспетчер проверку, отвергавшую явно названного выключенного
провайдера; эта проверка была **откатана**, потому что меняла поведение, которое другой
тест сознательно закрепляет, а принуждение перенесено в композицию, где оно убирает
транспорт, а не перерешает существующий контракт. Авторизация не затронута: `RunScope`,
`ApprovalPolicy` и `ExecutionCoordinator` не изменены, второй execution plane или
параллельная авторизация не создавались.

**Вызывающий, передающий свои `settings`, сохраняет историческую форму реестра** (все
провайдеры сконструированы, включены только разрешённые). Это сознательная граница
совместимости, а не пробел: `tests/test_runtime.py` закрепляет эту форму, и такой
вызывающий явно заявил форму своего runtime, тогда как сужение всё равно убрало живых
провайдеров из `enabled_providers`.

**Принуждение, точно.** `app/runtime/bootstrap.py`: `_test_mode_requested()` определяет
сигнал, `_offline_only_settings()` — сужение, а выражение `provider_names_to_register` —
ambient-фильтр регистрации. `OFFLINE_PROVIDER_NAME = "mock"` в `app/config/settings.py` —
единственное написание «offline». `tests/offline_runtime.py` — фикстур, которым тест
подписывается на гарантию; теперь он передаёт `test_mode=True` явно, поэтому гарантия —
свойство runtime, а не настроек, которые этот фикстур случайно строит.

**Проверено наблюдением, а не наличием класса:** `tests/test_offline_runtime_guarantee.py`
(10 тестов) утверждает при искусственных фиктивных значениях ключей в окружении, что
TEST-runtime сообщает только offline провайдера, что живой провайдер отсутствует в реестре
и в capabilities, что явный запрос к нему **падает без fallback** (fallback позволил бы
тесту пройти, ничего не доказывая о названном провайдере), и что представительный workflow
делает **ноль исходящих попыток соединения**. Утверждение о сети перехватывает реальные
швы `socket.socket.connect`, `socket.socket.connect_ex`, `socket.create_connection` и
`socket.getaddrinfo`, всегда восстанавливая их в `finally`, и каждый такой тест
дополнительно доказывает, что перехват наблюдал реальный вызов `connect()`, чтобы сломанный
spy не сделал утверждение пустым. Отсутствие секретов утверждается для каждой
захваченной записи лога, отрендеренной собственным форматтером приложения, включая
аргументы записи.

**Остаточный риск (OPEN).** Гарантия действует, когда TEST-runtime запрошен. Тест,
вызывающий `create_runtime()` без `test_mode=True` и без `FORGE_ENV=test`, по-прежнему
читает ambient `.env` ровно как раньше; поэтому `FORGE_ENV=test` — это *ambient*-блокировка
и рекомендуемый способ запускать весь набор, но ничто в репозитории не устанавливает его
за вас, и ни один тест не требует его наличия. Часть тестов законно проверяет production-
композицию и передаёт явные настройки, поэтому всеобщее значение по умолчанию «test» было
бы неверным; честная формулировка: гарантия структурна **для каждой композиции** и
включается **для каждого запуска** отдельно.

### 4.15 Declared-verification не делает генеративной диспетчеризации (исправлено)

**Дефект.** `ForgeApiService.run_declared_verification` диспетчеризовал генеративную задачу
с описанием `"Declared verification execution."`, а затем полностью отбрасывал её вывод
(`run.execution_results[0]` — это результат host-исполнения; вывод диспетчеризации никогда
не читается). Поэтому один workflow делал **два** вызова провайдера, что дважды измерено
независимо. Стоимость была меньшей половиной дефекта: `RunExecutor` достигал ветки
host-исполнения только когда эта диспетчеризация успешна, поэтому когда ни один провайдер
не мог ответить, уже авторизованная оператором объявленная команда **молча не запускалась**,
а прогон сообщал `"no declared execution was requested"`. Это pre-existing поведение этой
точки входа, а не привнесённое слайсом генерации.

**Исправление.** Две задачи разделены. `RunExecutor.execute(..., generate_decision=False)`
означает «решение уже существует вне этого прогона»: генеративная диспетчеризация не
делается, прогон записывает одно событие `DECISION_MADE` с
`decision_source="declared_execution"`, `decision_provider=None`, `decision_model=None`,
`generative_dispatch=False`, и ветка host-исполнения достигается напрямую.
`run_declared_verification` передаёт `generate_decision=False`; значение по умолчанию
`True`, поэтому `run_task`, acceptance-цикл и все прочие вызывающие не изменены.

**Что не обходится.** Замороженный `RunScope`, обязательный `ApprovalPolicy`,
`ExecutionCoordinator` (по-прежнему единственный, кто может выпустить
`AuthorizedExecution`) и локальный адаптер всё ещё работают, и реальный код возврата
объявленной команды по-прежнему единственное, что определяет сообщаемое состояние.
Положительный контроль утверждает, что ненулевой код возврата всё ещё сообщается как
падение, отсутствие approval policy всё ещё отказывает, а политика, требующая одобрения,
всё ещё останавливает команду.

**Скрытый дефект персистентности, найденный и исправленный заодно.** `RunExecutor.execute`
вызывал `_record` без `run_store` для `RUN_STARTED` и `CONTEXT_ASSEMBLED`, а
`_record_context_assembled` не имел способа его получить. Поэтому эти два события никогда
не попадали в долговременный лог, а объект store поглощался в метаданные самого события под
ключом `run_store`. Post-mortem не мог увидеть, что прогон начался. Все три места вызова
теперь пробрасывают `run_store`.

**Проверено.** `tests/test_declared_verification_dispatch.py` (7 тестов) считает настоящие
вызовы `Provider.generate` через транспорт, внедрённый в существующий шов провайдера, и
независимо считает вызовы диспетчеризации через считающий оркестратор: одна declared-
verification делает **ноль** генеративных решений, текст модели никогда не становится
выводом прогона, и ни одно событие `PROVIDER_SELECTED`/`PROVIDER_ATTEMPT` не записывается.
Fails-before / passes-after доказано вырезанием исправления из `app/orchestrator/run.py`
на месте, запуском одиночного теста (он упал с
`AssertionError: 1 != 0 ... ['Declared verification execution.']`), восстановлением файла из
байт-точного экземпляра, хранящегося вне репозитория, и подтверждением SHA-256
`61f543ab166afb8cc58e961d34e1ea03e488579ff84da30b9ee16847c8cbfb1d` до и после.

### 4.16 Сверка чисел тестов: 2110, 1948/1954 и 2206

Для «сколько всего тестов в этом репозитории» приводились три разные цифры. Это не три
измерения одной величины; это одна неверная цифра, одна частичная и одна полная.

**Объявленных тестовых методов = собранных загрузчиком = 2206.** AST-обход каждого файла
`tests/*.py` и `unittest.TestLoader().discover(...)` совпадают точно, модуль за модулем:
каждый `def test*` в этом наборе находится в подклассе `unittest.TestCase`, функций
`test*` уровня модуля нет, функций протокола `load_tests` нет, ошибок импорта нет. Число
файлов и число тестов — разные величины: 116 модулей соответствуют `test*.py` и содержат
2206 методов, поэтому «112 files / 2110 tests» нельзя проверить сравнением одних лишь
количеств файлов.

**`Ran 1971 tests` — это то, что сообщает один процесс, и это не число существующих
тестов.** Измерено в одном процессе на этом дереве, без живого PostgreSQL:
`Ran 1971 tests ... OK (skipped=36)`. Учёт:

| Величина | Количество |
| --- | --- |
| Тестовых методов собрано загрузчиком | 2206 |
| `Ran N tests`, сообщённое runner'ом | 1971 |
| из них пройдено | 1967 |
| из них пропущено как реально достигнутые тестовые методы | 4 |
| из них классовых пропусков `setUpClass`, которые runner считает, но не исполняет и не называет | 32 |
| **собрано, но вообще не сообщено** | **235** |
| упало / ошибок | 0 |

Арифметику нужно формулировать аккуратно, потому что итог runner'а дважды учитывает
классовые пропуски: `1967 пройдено + 4 реальных пропуска + 235 не сообщено = 2206
собранных`, тогда как `1971 = 1967 + 4 + 32`, и эти 32 классовые записи — *маркеры*
классов, методы которых входят в те самые 235, а не дополнительные тесты. Читатель,
складывающий `1971 + 235`, получит 2206 **по совпадению**, а не по верному тождеству;
верное тождество — первое.

**Почему 235 собранных методов исчезают.** Тесты Platform с базой данных наследуют
`setUpClass` из общей базы, первое действие которой — `raise unittest.SkipTest(...)`, когда
`asyncpg` отсутствует или живой сервер не отвечает. Когда классовый фикстур поднимает
`SkipTest`, `unittest.suite.TestSuite._handleClassSetUp` записывает классовый пропуск и
отбрасывает весь класс, **не вызывая `addSkip` для отдельных тестовых методов**. Сводка при
этом всё равно считает классовую запись (`testsRun` для неё инкрементируется) — именно
поэтому два числа могут расходиться на 235, а прогон при этом не сообщает ни одного
падения. Эти 235:

| Модуль | Молчащих методов |
| --- | --- |
| `test_platform_persistence.py` | 107 |
| `test_platform_application_discovery.py` | 34 |
| `test_platform_postgres_schema.py` | 59 |
| `test_platform_postgres_security.py` | 35 |
| **итого** | **235** |

Девять методов в этих четырёх модулях, которые *всё же* исполняются, принадлежат обычным
подклассам `unittest.TestCase` (`NoSubjectParameterTests`, `SchemaArtefactTests`), не
наследующим пропускающий фикстур. Реально достигнуты и пропущены ровно **4** тестовых
метода, по этим причинам: `FORGE_ALLOW_REAL_PROVIDER_GENERATION` не задан
(`test_project_generation_real_provider.py`), `OPENAI_API_KEY` не задан
(`test_agent_harness_ai_integration.py`), и два пропуска из-за недоступности файловых
символических ссылок (`test_execution_secret_staging.py`, `test_workspace_boundary.py`).
Ничего не упало и ничто молча не превратилось в успех: собранный-но-несообщённый тест — это
**пробел**, и здесь он и сообщается как пробел. Эти 235 молчащих методов не исполнены, не
пропущены сообщённым образом и тем более **не** являются успехами.

**Цифра 1948 была прогоном по одному файлу, исключавшим `test_desktop.py`** (в этом проходе
измерено как `Ran 1954` до двух новых тестовых модулей), и это число того же рода, что и
1971: итог runner'а, а не число объявленных тестов. Оно не противоречит 1954/1971 — это
другая, более узкая команда.

**Цифра 2110 была неверна для этого дерева.** В §4.11 она записана как наблюдение
`python -m unittest discover -s tests` с живым PostgreSQL. На `d1c007a` плюс это рабочее
дерево она не воспроизводится: дерево теперь объявляет 2206 методов и сообщает 1971 в одном
процессе. Два независимых факта объясняют разрыв без отсылки к мистике: (а) живой DSN
превращает 32 классовых пропуска в реальные исполнения, что меняет сообщённый итог, но
никогда не может сделать его больше числа существующих методов; и (б) в рабочем дереве есть
11 незакоммиченных тестовых/вспомогательных файлов и 79 тестовых методов, добавленных после
того, как цифра была снята (только `test_platform_application_discovery.py` даёт 37, а он
добавлен коммитом `d1c007a`, текущим HEAD). Верная формулировка: **2110 — датированное
наблюдение другого дерева, а не измерение этого, и её нельзя приводить как текущую.** Цифры
в этом файле, измеренные на `d1c007a` плюс текущее рабочее дерево: 2206 объявлено, 1971
сообщено, 1967 пройдено, 36 пропусков, 0 падений, 235 собрано-но-не сообщено. Измеренная
команда и результат:

```
python -X utf8 -u -m unittest discover -s tests -t tests -v
Ran 1971 tests in <elapsed>s
OK (skipped=36)
```

а из инструментированного `unittest.TextTestResult` при verbosity 0, который сообщает
каждый исход отдельно (потому что verbosity по умолчанию теряет сводку из-за прерывания
teardown из §4.11): `collected=2206`, `passed=1967`, `skipped=36` (из них 32 — классовые
записи), `never_reported=235`, `failed=0`, `errors=0`, и код возврата Python для той же
команды — `0`.

**Ограничение окружения за этими 235 (доказано, а не предположено).** Контрактные тесты
базы данных Platform нуждаются в семантике PostgreSQL — row-level security, `FORCE ROW
LEVEL SECURITY`, `current_setting`, частичные уникальные индексы, составные внешние ключи —
и сознательно не симулируют их в SQLite; без `FORGE_PLATFORM_TEST_DSN` они пропускаются. В
окружении этого прохода нет сервера на порту по умолчанию фикстура `127.0.0.1:5432`
(`postgres_fixture.live_postgres()` вернул `None`), а живой PostgreSQL, о котором сообщил
разработчик на `127.0.0.1:5433`, сбрасывает рукопожатие (`asyncpg.connect()` поднимает
`ConnectionResetError [WinError 64]` — порт принимает TCP, а затем протокол падает). Ни
конфигурация PostgreSQL, ни конфигурация системы не менялись в обход этого, и **эти 235
методов не сообщаются как пройденные**: они не исполнялись.

### 4.17 Остаток: монитор здоровья провайдеров всё ещё звонит на живые адреса в тестах (исправлено; см. §4.19)

**Обновление статуса.** Этот пункт исправлен. Измерение, привязка по кадрам стека и
причина, по которой он был зафиксирован, а не замолчан, сохранены ниже как историческая
запись; описанного в них поведения больше нет. Исправление, явный вердикт «не проверено»,
граница совместимости и проверка — в §4.19.

Гарантия §4.14 убирает каждый путь от **решения** к живому провайдеру. Она не убирает один
независимый путь, и этот раздел фиксирует его, а не замалчивает.

`app/dashboard/provider_health.py` проверяет доступность провайдеров, открывая **сырые TCP-
соединения** (`_check_deepseek` → `api.deepseek.com:443`, `_check_openrouter` →
`openrouter.ai:443`). `ForgeApiService.get_system_status`, `get_dashboard_report` и
`refresh_dashboard` запускают этот монитор, поэтому такие вызовы происходят в тестах всякий
раз, когда сервис или дашборд собирается без замены монитора — в том числе с offline-
runtime, потому что монитор не является частью реестра провайдеров.

**Измерено, а не выведено.** Во время полного однопроцессного прогона набора с перехватом
реального шва `socket.create_connection` наблюдались **22 исходящие попытки**, привязанные
по кадру стека: 11 к `api.deepseek.com:443` и 11 к `openrouter.ai:443`, из пяти тестовых
методов — `test_api.py::test_02_system_status`,
`test_api.py::test_08_no_secret_exposure`,
`test_dashboard.py::test_01_a_run_reaches_the_dashboard_with_its_attempts`,
`test_dashboard.py::test_18_dashboard_service_and_json_export` и
`test_dashboard.py::test_21_one_run_id_across_fallback_and_22_artifact_linkage`. Набор всё
равно проходит, потому что эти соединения падают и монитор сообщает провайдера как
недоступного; но попытки — это реальный сетевой выход из тестового прогона.

**Серьёзность, честно.** Это неаутентифицированные TCP-пробы доступности: они не передают
credential, не отправляют prompt и не могут потратить токены. Именно поэтому это остаточный
риск, а не повтор дефекта §4.14. Тем не менее это **сетевой выход из набора тестов**,
поэтому верное утверждение узкое: *test-режимный runtime не делает вызовов-решений к
провайдеру*, а не *набор не открывает ни одного сокета*.
`tests/test_offline_runtime_guarantee.py` доказывает первое; он сознательно не заявляет
второе и устанавливает собственный spy на собственный сервис и runtime, поэтому он не
измеряет монитор здоровья.

**Сознательно не исправлено здесь.** Заставить монитор не проверять выключенных
провайдеров либо заставить набор тестов заменять его по умолчанию — значит изменить либо
production API-контракт, либо композицию тестов, которые законно проверяют дашборд. И то, и
другое — решения владельца, поэтому ни одно не принято.

### 4.18 Durable-запись прогона классифицируется по тому, что она доказывает; «в процессе» не считается доверенным

**Что запись собой представляет и чего не доказывает.** `RunStore` пишет на прогон два
файла: `<root>/<run_id>/events.jsonl` и `<root>/<run_id>/state.json`. Снимок
(`RunStateSnapshot`) несёт `run_id`, `task_id`, `state`, `status`, `attempt_number`,
`completed_attempt_indexes`, `project_state`, `accounting`, `event_count`, `updated_at`
и `interrupted`. В нём **нет** ни heartbeat, ни идентичности владельца/процесса, ни
lease, ни срока действия, ни времени старта/финиша, ни терминального маркера. Поэтому
снимок доказывает только то, что его писатель наблюдал **в момент записи**. Это и есть
вся доступная на диске доказательная база.

**Дефект.** Запись, заявляющая `state='RUNNING'`, ранее читалась обратно дословно как
`RUNNING`, и ничто не отличало писателя, который всё ещё исполняет, от того, чей процесс
умер. Оба случая дают побайтово идентичные файлы, поэтому читателю сообщалось
определённое состояние там, где честный ответ — «запись этого установить не может».
Измерено на изолированном `RunStore(root=<temp>)`: запись пишется и читается обратно
достоверно (`state='RUNNING'`, `status='RUNNING'`, `interrupted=False`, то же самое из
свежего экземпляра store), а до этого изменения store **не давал** никакого способа
отличить живого писателя от мёртвого — путь записи достоверен, значит это пробел на
стороне чтения, а не дефект пути записи. Отдельно: в `RunState` нет членов `CANCELLED` и
`TIMEOUT`; они существуют только как метки `ExecutionOutcomeStatus`
(`EXECUTION_TIMEOUT`, `POLICY_DENIED`, …), поэтому у читателя не было и словаря, чтобы их
выразить.

**Исправление.** `app/runtime/run_store.py` добавляет классификацию, выводимую **во время
чтения** из самой записи: `ExecutionClassification` (`str`-enum),
`RunRecordClassification` (результат + код причины + точная провенанс-пара поле/значение +
список свидетельств) и `RunStore.classify_record(run_id)`. Значения и точные условия:

| Результат | Условие |
| --- | --- |
| `TERMINAL_SUCCESS` | метка терминального успеха (`COMPLETED`/`SUCCESS`) в любом из полей |
| `TERMINAL_FAILURE` | метка терминального падения (`FAILED`, `FAILURE`, `EXECUTION_FAILURE`, `LIMIT_REACHED`, `ERROR`, `EXECUTION_ERROR`) |
| `TERMINAL_TIMEOUT` | явная метка таймаута (`TIMEOUT`, `EXECUTION_TIMEOUT`) |
| `TERMINAL_CANCELLED` | явная метка отмены (`CANCELLED`, `CANCELED`) |
| `TERMINAL_REFUSED` | метка отказа (`DENIED`, `POLICY_DENIED`, `PERMISSION_DENIED`, `APPROVAL_REJECTED`) |
| `WAITING` | метка ожидания (`WAITING`, `WAITING_FOR_APPROVAL`, `APPROVAL_WAITING`) |
| `INDETERMINATE` | установлен `interrupted`, либо поле заявляет прогресс (`CREATED`, `RUNNING`, `REVISING`, `INITIAL`) без терминальной метки |
| `UNKNOWN` | нет распознанной метки, оба поля пусты, две терминальные метки **противоположной полярности**, либо нет снимка и нет терминального события |

Два размеченных поля, противоречащих друг другу, разрешаются сначала по полярности:
«успех» рядом с «падением» — это противоречие, дающее `UNKNOWN`, и никогда не успех.
Внутри одной полярности выигрывает более специфичная метка, поэтому
`state='FAILED'` при `status='EXECUTION_TIMEOUT'` даёт `TERMINAL_TIMEOUT` и сохраняет
причину, а не отбрасывает её. Нераспознанное значение всегда только нейтрально: оно не
может ни поднять запись до успеха, ни понизить настоящий терминальный результат. Когда
снимка нет, используется журнал событий и решает **последнее** терминальное событие
(`ENGINEERING_RUN_COMPLETED`/`RUN_COMPLETED`/`HARNESS_COMPLETED` против
`ENGINEERING_RUN_FAILED`/`RUN_FAILED`/`HARNESS_FAILED`/`HARNESS_LIMIT_REACHED`); если нет
ни того, ни другого — `UNKNOWN`. `RunStore.load` по-прежнему возвращает `None` для run id,
который никогда не сохранялся, а `classify_record` для него сообщает `UNKNOWN`: ничего не
записано — значит ничего не доказано.

**Почему запись, заявляющая прогресс, не считается доверенной автоматически.** Ни одно
поле записи не может установить живость процесса. Время с момента `updated_at` намеренно
**не** использовалось как замена: это изобрело бы lease и срок действия, которых у
формата на диске нет, и молча переклассифицировало бы медленный, но живой прогон как
мёртвый. Отказ никогда не является успехом, неизвестное никогда не является успехом, а
противоречие никогда не является успехом — `RunRecordClassification.is_success` истинно
только для `TERMINAL_SUCCESS`.

**Граница совместимости, сознательная.** `RunState` **не** расширяется: члены `CANCELLED`
и `TIMEOUT` не добавлены. `RunState` — это живой жизненный цикл в памяти, и он
сравнивается по идентичности по всей кодовой базе: `run.state == RunState.COMPLETED`
определяет успех в `app/api/service.py`, а `app/orchestrator/run.py`,
`app/orchestrator/revision.py` и `app/orchestrator/engineering.py` ветвятся по нему, —
поэтому новый член молча создал бы третье, необработанное живое состояние в каждом из этих
сравнений. Классификация — отдельный словарь на стороне чтения, и ни один путь исполнения
не содержит её значения. `read_state`, `load`, `accounting_summary` и `summary()` не
изменены: `summary()` возвращает ровно прежний набор ключей, и классификация в него не
встроена. Классификация вычисляется из полей, которые писал любой более ранний вариант,
поэтому **ни один существующий `state.json` не мигрируется, не перезаписывается и не
читается иначе**, и никакое второе хранилище или база данных не добавлялись. Неизвестный
usage остаётся неизвестным: отсутствующий блок accounting читается как `None`, а не как
ноль токенов и не как нулевая стоимость.

**Проверено.** `tests/test_execution_record_classification.py`, 26 тестов, каждый на своём
собственном `RunStore(root=<temp dir>)`: терминальный успех, терминальное падение, запись
ожидания (сообщается как ожидание, не как падение и не как успех), явно прерванная запись,
запись, заявляющая прогресс, сообщаемая как indeterminate **с причиной, названной из самой
записи** (два разных заявления дают два разных detail), отказы, никогда не сообщаемые как
успех, неизвестные/отсутствующие поля, сообщаемые как неизвестные, проверки неизменности
формы `summary()` и `accounting_summary()`, и вручную написанная old-shape фикстура
`state.json` (без `interrupted`, без `project_state`), которая всё ещё загружается,
классифицируется и суммируется. Он также утверждает, что у `RunState` по-прежнему ровно
шесть членов. Fails-before / passes-after доказано вырезанием исправления из
`app/runtime/run_store.py` **на месте** (заявление о прогрессе пропускалось как
терминальный успех), запуском этого единственного файла (**`Ran 26 tests`,
`FAILED (failures=8)`**, включая
`test_a_record_claiming_progress_is_indeterminate_not_running` и
`test_terminal_failure_record_reads_back_as_terminal_failure`), восстановлением файла из
байт-точного экземпляра в памяти в блоке `finally` **бинарными**
`read_bytes`/`write_bytes` и подтверждением одного и того же SHA-256
`74d882dc55306d5131303af127e0d8fa3dc2f2133de09acaf83b09c7a80acfaf` до и после, после
чего тот же файл сообщил `Ran 26 tests`, `OK`.

**Что не изменено.** Ничего в исполнении, одобрении, возобновлении или пересчёте
accounting не изменилось; классификация читает файлы и не пишет ни одного (это утверждает
тест, который классифицирует трижды и сравнивает байты на диске).

### 4.19 Монитор здоровья провайдеров не делает сетевых попыток в test-режиме

**Что изменилось.** 22 исходящие попытки, зафиксированные в §4.17, устранены: пробы
доступности теперь зависят от собственных настроек композиции.
`app/dashboard/provider_health.py` принимает необязательный `settings`
(`ProviderHealthMonitor(..., settings=...)`); без аргумента он разрешает те же
`RuntimeSettings`, что и голый `create_runtime()`, через ту же функцию `load_settings()`.
Пробы подавляются, если выполняется **любое** из условий:

* `settings.environment is ApplicationEnvironment.TEST` — существовавший ранее переключатель
  `FORGE_ENV=test`, тот же самый, что использует `create_runtime`. Второй переключатель не
  вводился.
* не включён ни один провайдер, кроме `OFFLINE_PROVIDER_NAME` (`"mock"`) — это
  *разрешённая* форма того же факта, потому что в test-режиме `create_runtime` сужает
  `enabled_providers` до одного offline-провайдера.

**Что сообщает непроверенный провайдер.** Новый `ProviderStatus.NOT_CHECKED`
(`app/dashboard/models.py`) возвращается для каждого провайдера, которого монитор не
проверял: `balance_display="NOT CHECKED"`, `balance_numeric=None`,
`alert=AlertLevel.INFO`, `last_checked=""` и
`details={"reason": "test_mode_no_network_probe", "environment": ...}`. Он никогда не
сообщает `READY`, `READY_GATEWAY`, `FREE_TIER`, `NO_CREDITS`, `NETWORK_BLOCKED`,
`AUTH_FAILED` или `UNAVAILABLE`, потому что ничего из этого не наблюдалось.
`last_checked` оставлен **пустым**, а не заполнен «сейчас»: у монитора нет времени
наблюдения для проверки, которой не было.

**Граница совместимости.** `NOT_CHECKED` — это новое *значение* существующего ключа
`status`, а не новый ключ. `ProviderAccountStatus.to_dict()` и
`DashboardReport.to_dict()` сохраняют ровно прежние наборы ключей, а `ProviderStatus` —
это `str`-enum, поэтому потребитель, который переключается по `.value`, сравнивает со
строкой или просто отображает значение, продолжает работать. Потребитель, считавший
прежние восемь членов исчерпывающими, теперь видит девятый; это и есть намеренное
документированное расширение, и это единственный способ не фабриковать статус.
`checks_enabled` доступен на мониторе, чтобы вызывающий мог задать вопрос напрямую.

**Production не изменён.** У `RuntimeSettings()` по умолчанию
`environment=development` и включён живой провайдер, поэтому обе проверки подавления дают
`False` и реальные пробы выполняются ровно как раньше: те же семь провайдеров, тот же
вердикт `NETWORK_BLOCKED` / `UNKNOWN` / `WARNING` для отказанного соединения, то же имя
исключения в `details` и реальный `last_checked`. Production-путь не ослаблен ради
тишины в тестах, и никакого test-only monkeypatch собственного кода Forge здесь нет:
гейт читает конфигурацию, а тесты лишь устанавливают spy на модуль `socket` процесса.

**Проверено.** `tests/test_dashboard_offline_health.py`, 7 тестов, `OK`. Под одним
блокирующим socket-spy — установленным на `socket.socket.connect`, `connect_ex`,
`socket.create_connection` и `socket.getaddrinfo`, восстановленным в `finally`, причём
`getaddrinfo` возвращает адрес из документационного диапазона (`203.0.113.1`), так что
даже DNS не может выйти наружу — прогон монитора в test-режиме даёт **ноль** исходящих
попыток, тогда как тот же spy фиксирует попытки `api.deepseek.com` / `openrouter.ai` до
исправления. Каждый тест утверждает, что spy был взведён, делая одну намеренную локальную
попытку соединения, поэтому ноль не может быть следствием сломанного перехвата. Измерено
до исправления, под тем же spy и `FORGE_ENV=test`: `CostDashboardService().get_report()`
делал 2 исходящие попытки соединения; после исправления — 0. На пяти дефектных методах из
§4.17 (`test_api.py::test_02`, `test_api.py::test_08`,
`test_dashboard.py::test_01`, `::test_18`, `::test_21`), измеренных с `FORGE_ENV=test`:
0 исходящих попыток, 0 падений. Fails-before / passes-after доказано записью
доисправочного `provider_health.py` обратно **на месте** **бинарным** `write_bytes`,
запуском регрессионного метода
(`test_06_regression_env_selected_test_mode_makes_zero_outbound_attempts`) и
восстановлением из base64-копии в блоке `finally`:

| состояние | SHA-256 `provider_health.py` | одиночный метод | попытки |
| --- | --- | --- | --- |
| до исправления (записан на месте) | `922047a2…f6d4` | exit 1, `AssertionError: Lists differ: [('create_connection', 'api.deepseek.com')] != []`; вердикт `<ProviderStatus.NETWORK_BLOCKED>` | 2 |
| после исправления (восстановлен) | `7bcb55de…f764` | exit 0, `Ran 1 test`, `OK` | 0 |

SHA-256 восстановления
`7bcb55dee65c91c8163e5af21663072ff7128084651aefea53ae210d06d7f764` идентичен до патча и
после восстановления, и затем весь файл сообщил `Ran 7 tests`, `OK`.

**Честная граница.** Разрешение по умолчанию читает `settings` из окружения процесса,
поэтому тестовый раннер должен выбрать test-режим (`FORGE_ENV=test`) — или передать
`settings=` — чтобы подавление сработало. Без такого выбора голое конструирование является
production-конструированием и всё равно делает пробы; именно поэтому production-поведение
не изменилось. `tests/offline_runtime.py` **не** устанавливает `FORGE_ENV`, поэтому он
сужает `enabled_providers` *runtime*, но не окружение монитора, который сервис создал
раньше, чем появился runtime; пять методов выше свободны от сети при `FORGE_ENV=test`
потому, что монитор сервиса разрешает окружение, а не потому, что `offline_runtime()` его
устанавливает.

**Что не изменено.** Ни одно решение о провайдере, диспетчеризация, fallback,
accounting или путь исполнения не изменились. Всё ограничивается `app/dashboard/**` и этим
файлом; `app/api/service.py`, `app/runtime/bootstrap.py` и `app/config/settings.py` не
изменялись.

### 4.20 Write mode применяет ответ модели, но реальный код может дать только живой провайдер

**Статус: РЕАЛИЗОВАНО / ПРОВЕРЕНО локальным фейком; половина с живым провайдером
здесь не проверена.** `app/agent_runtime/code_writer.py`, `tests/test_code_writer.py`
(23 теста). `POST /api/tasks/run` с `write_mode: true` и `category: "code"` передаёт
ответ провайдера защищённому писателю, который разбирает фенсед-блок с заголовком пути
или unified diff, проверяет каждый путь по замороженной границе рабочей области запуска
и по deny-list, пишет атомарно с резервными копиями, компилирует каждый затронутый `.py`
и откатывает весь запуск при ошибке компиляции. Измерено на новом in-process сервисе на
одноразовой рабочей области: созданы два файла, настоящий diff возвращён и сохранён под
`project_state["code_write"]`, `py_compile` прошёл; второй ответ с `../escape.txt`
отклонён со статусом `rejected` и ничего не записал.

**Честная граница.** В этой среде ни один живой провайдер не включён, поэтому ветка
провайдера проверена локальным фейком на стыке исполнителя, а write mode offline-провайдера
честно сообщает `no_changes` (его детерминированный текст плана не называет ни одного
файла). Сквозная запись реального кода проекта поэтому всё ещё требует включённого
провайдера, чей ответ использует поддерживаемую форму. Также не проверено намеренно: ответ,
патчащий файл, которого модель не видела, отклоняется
(`unified_diff_not_applicable:diff_context_mismatch`), потому что передаются только имена
файлов, а не содержимое; удаления отклоняются; у write mode нет отдельного гейта одобрения
(открытое решение владельца).

### 4.21 Приложение само просит у модели файлы, и просит ровно один раз

**Статус: РЕАЛИЗОВАНО / ПРОВЕРЕНО локальным фейком на реальном пути сервиса.**
`app/agent_runtime/output_contract.py`, `app.api.service.ForgeServiceApi._write_task_description`,
`tests/test_write_output_contract.py` (14 тестов). Измеренный дефект: задача
«создать безопасный экран проверки настроенных API-провайдеров» получила от модели план и
вопрос «какой вариант выбираем A/B/C?», файлов не появилось, а запуск был показан как
завершённый (`state=COMPLETED`, девять ✅, «Запись сохранена: да»).

Что теперь делает код:

* в prompt write-mode запуска последним абзацем добавляется детерминированный контракт
  (`OUTPUT_CONTRACT`): сначала что делаешь, затем по одному фенсед-блоку
  ```` ```<язык> path=<относительный путь> ```` на файл; никаких вопросов, вариантов A/B/C и
  отчётов вместо кода; при нехватке данных — не более трёх вопросов и
  `intent=CLARIFICATION_REQUIRED`; разрешённый корень записи, запрет `.env`/секретов/`.git`,
  «только файлы из плана», «это запуск `code` в режиме записи». Контракт добавляет
  приложение — владелец по-прежнему пишет только идею;
* если ответ всё равно не содержит ни одного файлового блока (`carries_file_changes` —
  чистый разбор без записи), выполняется **один** повтор с короткой жёсткой инструкцией
  (`RETRY_INSTRUCTION`) через существующий `RunExecutor` (`_run=run`, `_attempt_number=1`).
  `MAX_WRITE_RETRIES = 1`, цикла нет; факт повтора пишется в `project_state["write_retry"]`
  и в ответ (`write_retry_attempted`);
* карточка результата в интерфейсе выбирает одну фразу из фактов
  (`ui/src/forge-result.ts`): файлы+проверка → «Готово: создано N файлов»; файлы без проверки →
  «Файлы созданы (N), проверка ещё не запускалась»; вопросы → «Forge задал вопросы…»; ноль
  файлов в write-mode → «Проект не создан: модель вернула план вместо файлов…»; отказ/ошибка →
  честная причина по фиксированному словарю. `state`/`status`/`run_id`/`event_count`/
  `classification.outcome`, английский `code_write.reason` и diff вынесены в свёрнутые
  «Технические детали»; последний шаг из девяти помечается «Завершено без файлов», если файлов нет.

**Честная граница.** Проверено на реальном сервисе с локальным фейком провайдера (реальная
замороженная область запуска, реальный писатель, реальный run store): проза → 2 вызова
провайдера и `write_retry_attempted=true`; фенсед-файл → 1 вызов, файл на диске,
`files_created=1`. Сквозной прогон через живого платного провайдера не выполнялся (запрет на
платные вызовы), и в интерфейсе эти две карточки сняты на реальном UI с воспроизведением
записанных ответов сервиса, потому что форма намеренно не включает write mode для offline
провайдера, а все настроенные живые провайдеры здесь платные.

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

---

# Source-of-truth boundaries (documentation pass)

Five sources answer five different questions. Where they disagree, the earlier rows
win, and the disagreement is recorded as an open question rather than settled by
editing prose.

| Source | Question it answers | Authority |
|---|---|---|
| `app/` and the passing test suite | What does the system actually do? | **Highest.** A claim that contradicts the code or a green test is wrong, whatever a document says. |
| `docs/SOURCE_OF_TRUTH.md` (this file) | What is the consolidated verified current state? | Verified statements only; an unverified statement must be marked as such. |
| `docs/ARCHITECTURE.md` | How is the current implementation built? | Descriptive of the code; it must change when the code changes. |
| `docs/TECHNICAL_SPECIFICATION.md` and `docs/FORGE_VISION.md` | What is the target, and why? | Direction, never evidence that something exists. |
| `docs/ROADMAP.md` | What is planned, in what order, with which exit criteria? | Planning and dependency order. |
| `docs/DECISIONS.md` | Why was a decision made, and what was rejected? | Rationale and history; a superseded decision stays visible as superseded. |
| `docs/audits/` | What was checked, when, with what result? | Engineering evidence: commands, defects, limitations, commits. |

Non-negotiable invariants, unchanged by any reorganisation of this file:

* **Recommendation ≠ Permission.**
* **Permission ≠ Execution.**
* **Report ≠ Evidence.**

Also unchanged: the Platform / Core / Fabric / Registry separation, per-project
isolation, the requirement that secrets never enter the repository or a log, that
execution happens only through the authorization chain, and that evidence is
reproducible.

## Open contradiction (needs the owner)

The finalization brief that commissioned this pass states that blocker #2 (`resume`
restoring an unsupported success) is **open**, while the repository says otherwise:
commit `a28c38a` fixes it, five regression tests cover it, and the full suite is green
at `Ran 2359 tests / OK (skipped=36)`. This document follows the repository, per the
authority order above. If the brief is authoritative for a reason not visible here,
the fix must be re-examined rather than the documentation adjusted to match the brief.

---

# Границы источников истины (документационный проход)

Пять источников отвечают на пять разных вопросов. При расхождении выигрывают
верхние строки, а расхождение фиксируется как открытый вопрос, а не «решается»
правкой текста.

* `app/` и проходящий набор тестов — что система делает фактически. **Высший
  авторитет.** Утверждение, противоречащее коду или зелёному тесту, неверно.
* `docs/SOURCE_OF_TRUTH.md` — сводное проверенное текущее состояние; непроверенное
  обязано быть помечено как непроверенное.
* `docs/ARCHITECTURE.md` — как устроена текущая реализация; меняется вместе с кодом.
* `docs/TECHNICAL_SPECIFICATION.md`, `docs/FORGE_VISION.md` — цель и направление,
  но не доказательство существования.
* `docs/ROADMAP.md` — что планируется, в каком порядке и с какими критериями.
* `docs/DECISIONS.md` — почему решение принято и что отклонено; отменённое решение
  остаётся видимым как отменённое.
* `docs/audits/` — инженерные доказательства: команды, дефекты, ограничения, коммиты.

Инварианты, не меняющиеся ни при какой реорганизации: **Recommendation ≠
Permission**, **Permission ≠ Execution**, **Report ≠ Evidence**; разделение
Platform / Core / Fabric / Registry; изоляция проектов; запрет секретов в
репозитории и логах; исполнение только через цепочку авторизации; воспроизводимость
доказательств.

**Открытое противоречие (нужно решение владельца):** задание на этот проход
называет блокер №2 открытым, тогда как репозиторий говорит обратное — коммит
`a28c38a`, пять регрессионных тестов, полный набор `2359 тестов / OK (skipped=36)`.
Этот документ следует репозиторию. Если задание авторитетно по невидимой здесь
причине, пересматривать нужно исправление, а не документацию.

## Structured Intent v0.1 — verified current state

This section states only what the code does and what a passing check proves. Commit:
`feat: add structured intent and beta verification` (the commit that adds the module).

**Implemented and verified:**

* `app/agent_runtime/intent.py` exists and is pure, stdlib-only, non-raising.
  `IntentType` has exactly five members (`CLARIFICATION_REQUIRED`, `PLAN_READY`,
  `GENERATE_PROJECT`, `REVIEW`, `FINAL_ANSWER`);
  `parse_model_intent(answer_text) -> ModelIntent` returns a `ModelIntent` for any input,
  including `None`, `""`, a truncated envelope and an unknown type.
* The parser accepts a bare JSON object, a `json` fence and an object embedded in prose,
  and validates `questions` / `plan` / `files` per type. A malformed envelope, an
  unknown type and a missing required field are each reported with a fixed error code;
  none of them raises and none of them stops the run.
* `derive_outcome` accepts the optional `intent_type` fact. `intent_allows_completion`
  admits only `GENERATE_PROJECT` (or no declared intent), so `FINAL_ANSWER`,
  `CLARIFICATION_REQUIRED`, `PLAN_READY` and `REVIEW` cannot produce
  `PROJECT_COMPLETED` even with every completion fact present.
* `TaskRunResponse.model_intent` publishes `intent_type`, `summary`, `valid`,
  `validation_errors`, `next_action` and `raw_text_preserved=true` on the provider run
  path; the raw answer is not echoed to the client.
* The explicit provider/model override, the fallback chain, the approval boundary and
  the paid-model policy are unchanged; the new field is published, never obeyed.

**Verified by:**

* `tests/test_structured_intent.py` — 31 tests, offline, no provider call;
* `tests/test_beta_path.py` — 10 tests, offline, the sixteen-step path and the seven
  negative checks (the local runner is exercised for real; no provider is called);
* `app/smoke_structured_generation.py` — exit 0, prints the task, the detected intent,
  the summary, the next action and the absence of a false `COMPLETED` for a
  clarification, a plan, a generation with fake files and a malformed envelope.

**Limits of this evidence (statable, not hidden):**

* No real AI provider was called. The provider path is exercised with an in-process
  fake registered under a real provider name, exactly as the existing suites do.
* The deterministic offline path (`provider == mock`) parses no model answer, so its
  `model_intent` row is empty. That is an honest "no intent declared", not a claim.
* Provider availability and any paid-model behaviour in a real deployment are not
  demonstrated here and are reported separately as manual evidence.

## Structured Intent v0.1 — проверенное текущее состояние

Этот раздел утверждает только то, что делает код и что доказывает проходящая проверка.
Коммит: `feat: add structured intent and beta verification` (коммит, добавляющий модуль).

**Реализовано и проверено:**

* `app/agent_runtime/intent.py` существует и является чистым, только на stdlib, не
  бросающим исключений. В `IntentType` ровно пять членов (`CLARIFICATION_REQUIRED`,
  `PLAN_READY`, `GENERATE_PROJECT`, `REVIEW`, `FINAL_ANSWER`);
  `parse_model_intent(answer_text) -> ModelIntent` возвращает `ModelIntent` для любого
  ввода, включая `None`, `""`, обрезанный конверт и неизвестный тип.
* Парсер принимает голый JSON-объект, `json`-фенс и объект, встроенный в текст, и
  проверяет `questions` / `plan` / `files` по типу. Сломанный конверт, неизвестный тип и
  отсутствующее обязательное поле сообщаются фиксированным кодом ошибки; ни одно из них
  не бросает исключение и не останавливает запуск.
* `derive_outcome` принимает необязательный факт `intent_type`.
  `intent_allows_completion` допускает только `GENERATE_PROJECT` (или отсутствие
  заявленного намерения), поэтому `FINAL_ANSWER`, `CLARIFICATION_REQUIRED`, `PLAN_READY`
  и `REVIEW` не могут дать `PROJECT_COMPLETED` даже при всех фактах завершения.
* `TaskRunResponse.model_intent` публикует `intent_type`, `summary`, `valid`,
  `validation_errors`, `next_action` и `raw_text_preserved=true` на пути запуска через
  провайдера; исходный ответ не возвращается клиенту.
* Явный override провайдера/модели, цепочка fallback, граница подтверждения и политика
  платных моделей не изменены; новое поле публикуется, но ему не подчиняются.

**Проверено:**

* `tests/test_structured_intent.py` — 31 тест, офлайн, без вызова провайдера;
* `tests/test_beta_path.py` — 10 тестов, офлайн, шестнадцать шагов пути и семь
  негативных проверок (локальный runner используется по-настоящему; провайдер не
  вызывается);
* `app/smoke_structured_generation.py` — код 0, печатает задачу, распознанное намерение,
  summary, следующее действие и отсутствие ложного `COMPLETED` для уточнения, плана,
  генерации с фейковыми файлами и сломанного конверта.

**Границы этих доказательств (называются, а не скрываются):**

* Ни один реальный AI-провайдер не вызывался. Путь провайдера проверен внутрипроцессным
  фейком, зарегистрированным под настоящим именем провайдера, как это делают
  существующие наборы.
* Детерминированный офлайн-путь (`provider == mock`) не парсит ответ модели, поэтому его
  строка `model_intent` пуста. Это честное «намерение не заявлено», а не утверждение.
* Доступность провайдеров и любое поведение платных моделей в реальном развёртывании
  здесь не демонстрируются и сообщаются отдельно как ручное доказательство.
