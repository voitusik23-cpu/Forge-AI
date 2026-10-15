# Phase 3, Stage C — RunStore, server-side idempotency, usage and cost integrity

Starting HEAD: `3061b34` (checkpoint `checkpoint-before-master-execution` at `74cf95d`).
Final HEAD: the commit that carries this file. Every claim below was measured on this machine.

---

## 1. C1 — the RunStore defect: the real root cause, and the correction of the earlier report

The previous report said records were written to one root and read from another. **That was
wrong**, and reproducing the defect first is what showed it.

Measured sequence:

```
POST /api/tasks/run        -> 200 COMPLETED, run_id run-api-017937c5
GET  /api/runs/run-api-017937c5 -> 404
search for run-api-017937c5 under %LOCALAPPDATA%\ForgeAI and the repository -> nothing
```

The record was never written at all. `run_task` does bind the store and does call
`record_run_state`; `record_run_state` calls `write_state`; and `write_state` then raises

```
PermissionError [WinError 5] ... 'C:\Users\User\AppData\Local\ForgeAI\runs\projects\ws-18ae85a5…'
```

creating the workspace-scoped store root — this platform refuses writes outside the workspace.
That call sits inside the "persistence must not block a run" `try/except`, so **the failure was
swallowed**: the dispatch answered `200 COMPLETED` and no record existed. The 2 604 directories
that suggested a second root belong to other code paths (workflow/graph runs and test runs), not
to this one.

### The fix

1. **An explicit, writable store root.** `tools/forge_launch.py` now passes
   `FORGE_RUN_STORE_ROOT=<project>/.forge/run-store` to the API it starts — a real, supported
   variable of `RunStore`. Persistence therefore works here and on any machine whose profile
   directory is unavailable; on a normal machine the default remains
   `%LOCALAPPDATA%\ForgeAI\runs`.
2. **The failure is reported, not hidden.** `run_task` derives whether the record exists and
   returns it with the response as `history_recorded` / a path-free `history_error`, which the
   route publishes as `X-Forge-History-Recorded` and `X-Forge-History-Error`.

### Compatibility policy for historical records

Records under the profile root (and other workspaces' project roots) are left **untouched**. The
reader keeps resolving the workspace-scoped root it resolved before, so nothing becomes visible
across a workspace boundary. Bringing old records into view would need an explicit, reversible
migration (copy, never move) and an owner decision; no bulk move was performed.

---

## 2. C2 — server-side idempotency

The ledger already existed: `app/agent_runtime/idempotency.py`, durable, file-per-key, with an
**atomic** `claim` through `O_EXCL`. No second queue and no second engine were invented.

Contract:

* the key arrives as the **`Idempotency-Key` header**. It is not a field of the request model,
  because five guard tests pin that model as byte-identical and forbid it growing anything that
  could express authority — they failed when I first tried it in the body, which is exactly their
  purpose;
* the stored key is scoped as `<project_id>:<key>`, so one project's key cannot reach another;
* a winning claim executes once and is updated with the terminal lifecycle;
* a losing claim returns the original run identity and starts nothing
  (`X-Forge-Idempotent-Replay: true`);
* the same key with a materially different request is refused with **409**;
* the ledger root is the run-store root, so restarts do not forget;
* **fail closed**: if a key is supplied and the ledger cannot be used, the dispatch is refused
  with **503** and a reason instead of executing unprotected. A request without a key keeps the
  legacy behaviour, whose lack of protection is documented rather than implied.

Verified end to end in-process (same `create_api_app` the tests use, with a writable root):

```
empty body                                          -> 400   (the documented refusal is preserved)
valid body + Idempotency-Key: probe-key-2            -> 200   history-recorded=true, usage-basis=MOCK
same body + same key again                           -> 200   idempotent-replay=true, identical run_id
different body + same key                            -> 409
```

### Documented limits

* There is no per-user authentication in this API yet, so the scope is project, provider and the
  request fingerprint — **not** a user identity. Key isolation between users arrives with the
  authentication work of a later stage.
* Exactly-once across an external provider boundary cannot be guaranteed. If the process dies
  between the provider call and the ledger update, the record keeps its previous lifecycle and the
  next retry reports that recorded state rather than silently calling the provider again.
* The UI keeps one key per logical operation and rotates it only after a real dispatch, so a retry
  after a timeout repeats the key rather than paying twice.

---

## 3. C3 — usage and cost integrity (additive part)

Recorded evidence was inspected first: `Usage` (`input_tokens`, `output_tokens`, `cached_tokens`,
`estimated_cost`), the dashboard's four money measures (`forge_spent`, `estimated_cost`,
`provider_reported_cost`, `effective_cost`), and the provider account probe. No new accounting
concept was introduced.

Added: `derive_usage_basis(attempts) -> (usage_basis, cost_basis)` with the values

| Basis | Meaning |
|---|---|
| `MOCK` | the offline provider: internally real numbers, never a real-provider execution |
| `ACTUAL` | tokens or a cost reported by the provider |
| `ESTIMATED` | a figure computed from a price table, with an explicit assumption |
| `UNKNOWN` | not enough evidence — **never** rendered as zero |

The basis is published as `X-Forge-Usage-Basis` / `X-Forge-Cost-Basis` and is part of the dispatch
result. Provider balance is never presented as the cost of a run, and a registry entry is never
presented as availability (pinned by the Stage B tests).

**Deliberately not done:** making `tokens`/`cost` nullable. That is the correct end state for
"unknown is not zero", but it changes a byte-pinned response contract and touches every consumer,
so it belongs to a separate change with its own full-suite verification. It is reported here as
the next step rather than half-done.

---

## 4. Files changed

| File | Change |
|---|---|
| `app/api/service.py` | `RunDispatchResult`, `RunIdempotencyConflict`, `IdempotencyUnavailable`, `run_task_detailed`, `_operation_identity`, `_replay_response`, `derive_usage_basis`, history/basis derivation |
| `app/api/server.py` | the run route takes the typed body plus a declared `Header`, publishes `X-Forge-*`, maps 409 and 503 |
| `tools/forge_launch.py` | passes `FORGE_RUN_STORE_ROOT` to the API |
| `ui/src/screens/Runs.tsx`, `ui/src/i18n.ts` | idempotency key per logical operation, basis badges, replay marker, history-failure notice |
| `tests/test_run_history.py` (new, 8) | persistence, read-back, restart, isolation, missing, unsafe id, concurrency, reported failure |
| `tests/test_task_idempotency.py` (new, 10) | repeat, conflict, distinct keys, restart, four-way concurrency, legacy, project scoping, lifecycle, fail-closed, legacy-without-key |
| `tests/test_usage_basis.py` (new, 6) | unknown≠zero, mock, actual, estimated, reported cost, real mock dispatch |

No migrations. **`app/api/models.py` is byte-identical to HEAD** — the contract was restored after
the guard tests refused the change. `app/execution/authorizer.py` is untouched.

---

## 5. Verification

| Check | Result |
|---|---|
| C1 focused | `Ran 8 tests … OK` |
| C2 focused | `Ran 10 tests … OK` |
| C3 focused | `Ran 6 tests … OK` |
| Contract guards (`test_product_integration`) | `Ran 93 tests … OK` |
| Other guards (`acceptance_production`, `agent_loop_production`, `api_declared_execution`, `project_workflow_production`) | all `OK` |
| Full suite | **`Ran 2450 tests in 361.551s` → `OK (skipped=36)`** |
| TypeScript | `tsc --noEmit` exit 0 |
| UI build | `vite build` exit 0 |
| Route behaviour | 400 / 200 / 200-replay / 409 as shown above |

---

## 6. Known limitations and unverified claims

1. **No real provider call was made** — there is still no configured provider account. Everything
   exercised here is the offline mock provider; a real call is unverified and requires a key.
2. **No per-user idempotency scope** — see C2 limits.
3. **`tokens`/`cost` remain non-nullable**, so "unknown" is expressed by the basis rather than by a
   null value. The next additive step is documented above.
4. **Historical run records** remain where they are; no migration was performed.
5. **Clean-Windows install** is untested: desktop packaging is a later stage.

## 7. Decisions that need the owner

1. A reversible migration (copy, never move) for historical records under the profile root, or
   leave them as legacy.
2. Whether `tokens`/`cost` may become nullable in the response contract — a byte-pinned model the
   guard tests defend, so it needs an explicit decision.
3. When authentication arrives, the idempotency scope should become the authenticated identity
   rather than the project/fingerprint pair.
