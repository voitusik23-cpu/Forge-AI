# Forge AI — Phase 2, Stage A: diagnostics

Everything below was read from the running backend or from the source, not assumed.
Commands and responses are recorded so each claim can be re-checked.

## 1. Why the user did not see agents or money

Three separate causes, in order of responsibility:

1. **The Overview screen never asked for them.** It requested `/api/status`,
   `/api/dashboard`, `/api/product/runs` and `/api/product/onboarding`. It never called
   `/api/agents`, which answers `200` with a real agent. That is a defect in my screen, not
   in the backend.
2. **`/api/dashboard` carries its own, different, empty collections.** Its payload has the
   keys `providers, models, agents, projects, runs, today_totals, all_time_totals,
   generated_at`, and in this environment `models`, `agents`, `projects` and `runs` are all
   `[]` — while `/api/agents` returns one agent and `/api/projects` returns one project.
   These are **two different sources**: the dashboard aggregates Platform (database-backed)
   records, which are empty here because no PostgreSQL is configured; the registry and the
   project store are in-process and populated. A screen that reads only the dashboard
   concludes "nothing exists", which is exactly what happened.
3. **Provider balances are deliberately not checked in this mode.** Each provider's
   `account_status` answers `status: NOT_CHECKED`, `balance_display: "NOT CHECKED"`,
   `balance_numeric: null`, with `details.reason = test_mode_no_network_probe`. The balance
   field exists and says it was not checked. Any number shown there would have been
   invented — the first Overview draft did invent one, and it was deleted.

## 2. UI section → API endpoint → backend source → actual fields → status

| UI section | API endpoint | Backend source | Actual fields (measured) | Status |
|---|---|---|---|---|
| Core status | `GET /api/status` | `ForgeApiService` runtime | `core, api, provider_status, providers_ready, providers_total, active_project, timestamp` | **live** — `core=connected, api=connected, provider_status=DEGRADED, 0/7, default` |
| Platform totals | `GET /api/dashboard` | dashboard service | `providers[], models[], agents[], projects[], runs[], today_totals, all_time_totals, generated_at` | **live**; `models/agents/projects/runs` are `[]` |
| Cost & spend | `GET /api/dashboard` | dashboard service | `today_totals{total_spent,total_runs,total_requests,total_tokens,avg_run_cost,successful_runs,failed_runs}`, `all_time_totals{…}` | **live** — real aggregates, currently all zero because no run exists |
| Provider spend | `GET /api/dashboard` | dashboard service | per provider: `forge_spent, total_requests, total_runs, total_attempts, input_tokens, output_tokens, cached_tokens, total_tokens, estimated_cost, provider_reported_cost, effective_cost, retry_count, fallback_count, success_count, failure_count` | **live** — three distinct cost measures, not interchangeable |
| Provider balance / quota | `GET /api/dashboard` | provider account probe | `account_status{status,balance_display,balance_numeric,quota_limit,quota_usage,alert,last_checked,details}` | **live but NOT_CHECKED** (`test_mode_no_network_probe`) |
| Agents | `GET /api/agents` | `AgentRegistry` (runtime) | `name, provider, model, status, capabilities` | **live** — one agent: `mock` / `mock` / `default` / `available` / `[code, analysis, review]` |
| Projects | `GET /api/projects` and `/api/product/onboarding` | project store | `project_id, name, path, status` | **live** — `default`, "Forge Main Workspace" |
| Tools | `GET /api/tools` | tool registry | `id, name, description` | **live** — `read_project_file`, `write_project_file` |
| Capabilities | `GET /api/capabilities` | capability registry | `capability_id, domain, name, description, metadata` | **live** — model, tool and workspace capabilities |
| Runs (product) | `GET /api/product/runs` | run store | `project_id, runs[], total_recorded, truncated` | **live, empty** — `total_recorded: 0` |
| Run detail | `GET /api/product/runs/{run_id}` | run store | — | available, no run to read |
| Approval / recovery | `/api/product/approvals/{id}`, `/api/product/recovery` | workflow store | — | recovery answers `409` with no graph run; real state, not an error to hide |
| Tasks | `POST /api/tasks/run` | orchestrator | — | exists; **not wired into this delivery** |
| Logs / traces | — | — | — | **no endpoint** |
| Agent instruction (system prompt) | — | — | — | **does not exist per agent** — see §3 |
| Per-agent permissions | — | — | — | **no per-agent permission model exposed**; authorization lives in the run authority chain |
| Config change timestamp | — | — | — | **not exposed for agents** |

## 3. Agent instructions: what actually exists

Searched the whole `app/` tree for `system_prompt` / `instructions`:

* agents do **not** carry a prompt. `MockAgent` holds `name`, `provider_name`,
  `tool_invocations`, `output`; `ProviderAgent` holds a provider and forwards
  `task.description`. `AgentInfo.to_dict()` exposes only `name, provider, model, status,
  capabilities`. Showing "the agent's system prompt" would require inventing it.
* **skills** do carry instructions: `app/skills/assembler.py` reads
  `skill.instructions`, trims to 1000 characters and assembles them into the prompt;
  `app/skills/builtin.py` defines e.g. `_PYTHON_TEST_RUNNER_INSTRUCTIONS`.
* models advertise `supports_system_prompt` (`app/agents/providers/models.py`).

So the honest statement in the interface is: *an agent has no system prompt of its own in
the current model; instructions belong to skills and are assembled at run time.* The agent
card will show the capability and tool set that determine behaviour, and will name this gap
explicitly rather than fill it with text.

## 4. Money: what exists, and the exact meaning of each measure

Nothing here is a per-agent balance. The measured vocabulary is:

| Measure | Where | Meaning |
|---|---|---|
| `usage.estimated_cost` | `app/usage.py`, carried from a provider through a task result | Forge's own estimate for one provider call |
| `estimated_cost` | `/api/dashboard` per provider | Sum of Forge's estimates for that provider |
| `provider_reported_cost` | `/api/dashboard` per provider | The cost the provider itself reported |
| `effective_cost` | `/api/dashboard` per provider | The figure Forge charges to its accounting for that provider |
| `forge_spent` | `/api/dashboard` per provider | What Forge has recorded as spent with that provider |
| `total_spent`, `avg_run_cost` | `today_totals`, `all_time_totals` | Aggregates over recorded runs |
| `account_status.balance_display`, `balance_numeric`, `quota_limit`, `quota_usage` | provider account probe | **Provider** balance and quota, not the agent's |

There is **no** agent budget, no agent limit, no agent ledger, and no remainder to compute:
`RevisionBudget` bounds a revision loop (a count, not money) and `IdempotencyLedger` prevents
duplicate execution (not accounting). Therefore the Agents screen will:

* label provider balance as a **provider** balance and never copy it onto an agent;
* state "Отдельный баланс агента не предусмотрен" where an agent balance would go;
* show run cost and provider spend with the measure named exactly;
* show "Недоступно" plus the reason where a value was not obtained.

## 5. Minimal safe way to get what is missing

Only read-only gaps need filling, and none of them requires touching Core, Platform, Fabric,
Registry, SQL, RLS or the authorization chain:

1. **Agent list with money attribution** — already possible: join `/api/agents` with
   `/api/dashboard` provider rows by provider name. No new contract.
2. **Per-agent runs** — not possible today. `usage` travels inside a task result, and run
   records are not queryable by agent. The minimal change is a **read-only** query filter on
   the existing run store (`/api/product/runs?agent=<name>`), which needs no schema change
   because the record already stores the agent; it is proposed, not implemented.
3. **Agent instruction** — needs a decision, not code: either agents gain an `instructions`
   field in the registry (a model change, therefore Core), or the interface keeps pointing at
   the skills that actually hold instructions. Reported, not invented.
4. **Per-agent permissions and config timestamps** — not exposed anywhere; they stay
   "недоступно" until the authority chain publishes them.

## 6. Source of every claim

```
GET /api/agents          -> 200 [{"name":"mock","provider":"mock","model":"default",
                                 "status":"available","capabilities":["code","analysis","review"]}]
GET /api/projects        -> 200 [{"project_id":"default","name":"Forge Main Workspace",...}]
GET /api/tools           -> 200 [{"id":"read_project_file",...},{"id":"write_project_file",...}]
GET /api/capabilities    -> 200 [{capability_id, domain, name, description, metadata}...]
GET /api/status          -> 200 {"core":"connected","api":"connected","provider_status":"DEGRADED",
                                 "providers_ready":0,"providers_total":7,"active_project":"default"}
GET /api/dashboard       -> 200 keys: providers, models, agents, projects, runs,
                                 today_totals, all_time_totals, generated_at
GET /api/product/runs    -> 200 {"project_id":"default","runs":[],"total_recorded":0,"truncated":false}
GET /api/product/recovery-> 409 (no graph run recorded)
app/agents/mock_agent.py, app/agents/provider_agent.py, app/api/service.py::list_agents,
app/usage.py, app/skills/assembler.py, app/agents/providers/models.py
```

Stage A conclusion: **the data exists; my screen asked the wrong endpoints.** The Agents
section can be built entirely on existing contracts. Nothing in this document required a
backend change, and none was made.
