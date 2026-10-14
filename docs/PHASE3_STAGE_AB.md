# Phase 3 — Stages A and B: audit, diagnosis, and the model catalogue

Measured on this machine at HEAD `74cf95d` (checkpoint tag `checkpoint-before-master-execution`).

## Stage A — audit and recovery

### What was wrong

`/api/status` intermittently answered **503** through the shell origin. Cause, measured rather
than guessed: **two orphaned API processes were alive while port 8011 was free** (pids 26468 and
30556, started 19:13:01, still resident at 21:26). The shell proxy had nothing to forward to and
reported "core unreachable" — the API itself was healthy whenever it ran (its log shows 200 for
every endpoint).

### What changed

* **Diagnosis instead of a bare failure.** The shell server grew `GET /diag`, which reports the
  API's reachability, both port states, the build time of `ui/dist`, and the tail of
  `.forge/logs/api.log`. It carries no credentials or environment values. Measured output:
  `{"api_reachable": true, "api_port_listening": true, "shell_port_listening": true, ...}`.
* **A watchdog.** While the window is open the launcher checks the API every five seconds and
  restarts it **once** if it stops answering, writing both facts to the journal.
* **Process hygiene that cannot backfire.** The first version of the launcher *terminated* other
  Forge AI server processes found by path. Measured: the launch stalled immediately after the
  line "stale Forge AI server pid … stopping it" and the servers never came up, while the same
  launcher completed perfectly with `--selftest` (which skips that step) — the enumeration can
  select a process belonging to the launch that is starting. Terminating on that basis is unsafe,
  so the step now **reports** the pids it found and touches nothing; the supervisor already
  reuses a healthy server and only ever stops processes it started itself.
* Both the enumeration and the kill path are bounded by timeouts, so a slow shell can never delay
  a launch.

### Reproduction now

```
launcher journal:  no other Forge AI server processes are running
                   core-api: starting on 127.0.0.1:8011 -> ready after 3s
                   shell-server: starting on 127.0.0.1:8020 -> ready after 1s
GET  /api/status -> HTTP 200 {"core":"connected","api":"connected","provider_status":"DEGRADED",
                              "providers_ready":0,"providers_total":7,"active_project":"default"}
GET  /diag       -> HTTP 200 with reachability, ports, api.log tail, ui build time
```

### Data sources verified (presence only; no secret content was read)

| Thing | State |
|---|---|
| `.env` | **exists** (never opened) |
| `.env.example` | exists |
| `FORGE_*` variables in the shell | none set — configuration lives in `.env`, which the launcher does not modify |
| Model registry | `app/agents/providers/model_registry.py` with `create_default_registry()` |
| Workspace / project | `default` — "Forge Main Workspace" |
| Run records on disk | 2604 directories under `%LOCALAPPDATA%\ForgeAI\runs\run-api-*` |

## Stage B — Models and Providers on real data

### The new read-only contract

`GET /api/models` over the existing `ModelRegistry` (no registry change, no schema change):

```
HTTP 200 | source=ModelRegistry providers=9 models=21 accounts=0
provider[0] {"provider_id":"anthropic","display_name":"Anthropic",
             "protocol":"ProviderProtocol.ANTHROPIC_MESSAGES","is_gateway":false,
             "model_count":2,"secret_env_name":"ANTHROPIC_API_KEY","has_base_url":true}
model[0]    {"provider_id":"anthropic","model_id":"claude-3-5-haiku-20241022",
             "display_name":"Claude 3.5 Haiku","context_window":200000,"max_output_tokens":8192,
             "capabilities":["…CODE","…REVIEW","…TOOL_CALLING"],"cost_tier":"CostTier.CHEAP",
             "supports_system_prompt":true}
account[0]  (none — no provider account is configured)
```

Three facts are deliberately kept apart, because conflating them is how an interface lies:

1. **membership** — the model is defined in the registry (this endpoint);
2. **configuration** — a boolean saying whether a secret reference exists, never the reference
   and never the value;
3. **availability** — reported only by the account probe in `/api/dashboard`, and `NOT_CHECKED`
   until that probe runs.

### What the measurement means for the owner

**accounts = 0** is the reason `providers_ready` is `0/7`. Without a configured account no
provider is probed and no model can be called. The screen states this as its next step instead of
implying that 21 registered models are usable. This is also exactly the precondition for
**TEST API**: a key supplied as an environment variable on the server; key values never enter the
interface, the bundle, a URL or the logs.

### The screen

`ui/src/screens/Models.tsx` — four KPIs (providers registered, models registered, accounts
configured, providers ready), the catalogue table (provider, model, context, capabilities, cost
tier, availability with a status dot), a provider panel with the secret **variable name**, all
four money measures named exactly (`forge_spent`, `estimated_cost`, `provider_reported_cost`,
provider balance), and a next-step card. Screenshot: `docs/images/ui-models.png`.

## Tests

`tests/test_api_models_catalogue.py` (7 tests, all green):

* the catalogue comes from the real `ModelRegistry` and is non-empty;
* every model carries the fields a UI needs, with a positive context window;
* a provider payload has exactly the allowed key set — a payload that grows a field able to carry
  a value fails the test;
* **no environment secret value appears anywhere in the payload** (every variable whose name
  contains `API_KEY`, `TOKEN`, `SECRET` or `PASSWORD` is searched for in the serialised JSON);
* an account reports `secret_configured` as a boolean and never exposes `secret_ref`;
* registry membership is not reported as availability;
* the endpoint is registered on the application (found by walking the `app.api` package).

Two of these tests were wrong when first written and were corrected: one forbade the string
`api_key`, which is legitimately the *variable name*, and one imported a factory that does not
exist. Both corrections are in the commit history rather than hidden.

## Known limits after this stage

1. **No provider account is configured**, so no model can actually be called yet. That is the
   owner's next action (a key in the server environment) — measured, not assumed.
2. **Run-record read-back** still 404s (`GET /api/runs/{id}`) while records are written to disk;
   the proposal and its consequences are in `docs/PHASE3_STAGE1.md` §4 and still need a decision.
3. **Idempotency of `POST /api/tasks/run`** remains interface-side only.
4. Stages C–I of the master order (RunStore/Usage, personal scenario, project creation dialogue,
   desktop packaging, web, TEST FREE/API, acceptance) are not started.
