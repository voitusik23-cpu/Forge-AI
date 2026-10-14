# Phase 3, Stage 1 — Projects and Runs, plus the autonomy roadmap

Every claim below was measured. The functional test was a real dispatch, not a screenshot.

## 1. What was investigated first

| Question | Answer found in the code and by probing |
|---|---|
| Request contract | `POST /api/tasks/run` accepts `description` (required), `category`, `task_id`, `provider_name`, `project_id`, `context` |
| Response contract | `run_id, task_id, project_id, state, success, output, error, tokens, cost, duration_seconds, provider_used, model_used` |
| Authorization | `app/api/service.py::run_task` generates the run id, binds a durable `RunStore` and **freezes the run perimeter before any privileged action**, so authority and project isolation come from the existing chain, not from the interface |
| Categories | read out of `TaskCategory` instead of being invented: `code, analysis, review, coding, reasoning, large-context, cheap/free, fast/cheap, other` |
| Project creation | **no contract exists**: `app/projects/` contains `state.py` (state derivation) only, and `/api/projects` is read-only |
| Run ids | guarded by `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`; traversal forms are refused with 404 |

## 2. What was built

* **Projects** (`ui/src/screens/Projects.tsx`) — the real list from `/api/projects` with id, name,
  path, status and which project is current; the missing creation contract is stated together
  with the reason (no write endpoint invented).
* **Runs** (`ui/src/screens/Runs.tsx`) — a form with project, category, agent/provider and task
  description; dispatch through `POST /api/tasks/run`; the result panel shows the real telemetry
  (state, success, tokens, cost, duration, provider, model, output, error); the session list keeps
  the answers Core actually returned; the product/workflow history is labelled as a different
  pipeline; the read-back gap is stated with the concrete decision it needs.
* Both screens are routed in `App.tsx`, labelled in RU and EN, and offer the contextual guide link.

## 3. Functional verification (the important part)

```
POST /api/tasks/run   (through the shell origin, the same URL the browser uses)
  -> {"run_id":"run-api-f98e026d","task_id":"task-553c8c","project_id":"default",
      "state":"COMPLETED","success":true,
      "output":"MockProvider response: Phase 3 smoke: dispatch through the command center path",
      "error":null,"tokens":0,"cost":0.0,"duration_seconds":0.038,
      "provider_used":"mock","model_used":"mock"}

POST /api/tasks/run   with {}            -> HTTP 400  (refusal relayed unchanged)
GET  /api/runs/..%2F..%2Fwindows         -> HTTP 404  (traversal refused)
on disk: %LOCALAPPDATA%\ForgeAI\runs\run-api-*\state.json + events.jsonl   (2604 records)
```

**A defect in my own shell server, found only by running the form.** `POST` answered
`501 Not Implemented`, because `tools/ui_preview_server.py` implemented only `do_GET`. The form
looked perfect on screen and could never have dispatched anything in the packaged application.
Fixed: `do_POST`/`do_PUT`/`do_PATCH`/`do_DELETE` now forward the method, the body and the content
type, and the backend's own status and body are relayed unchanged (which is why the 400 test above
returns the backend's message rather than a generic one).

## 4. Remaining limitation, with evidence, needing an owner decision

Run records **are persisted**: 2604 directories exist under `%LOCALAPPDATA%\ForgeAI\runs\`,
the newest containing `state.json` and `events.jsonl`. But:

```
GET /api/runs/b10-good    -> 404      (b10-good exists on disk)
GET /api/runs/mvp-run-A   -> 404      (mvp-run-A exists on disk)
GET /api/runs/run-api-f98e026d -> 404 (the smoke run exists on disk)
```

The id guard passes those ids, so the read path is looking at a different store than the write
path: the writer used the default root, while `get_run_record` resolves the store through
`project_run_store_root(self._workspace.root)` (`...\runs\projects\ws-<hash>`).

**Proposal (not implemented, because it touches Core wiring):** point the API service's read
store and the dispatch write store at one root — either always the project root, or always the
default root with the project id recorded inside the record. Consequences: `GET /api/runs/{id}`
starts answering for real, the Runs screen can show persisted history instead of the session
buffer, and any existing consumer that reads the default root keeps working if the project root
is used as an additional lookup rather than a replacement. Risks: two roots may already hold
records for the same id, so a migration or a precedence rule is required; that rule is a
product decision, not a refactor.

## 5. Autonomy roadmap (Stage 5) — dependencies, risks, acceptance criteria

Nothing here is implemented in this stage. Each item names what it depends on and how it would be
accepted.

| Direction | Depends on | Main risk | Acceptance criteria |
|---|---|---|---|
| **Context Selector / DecisionContextAssembler** | the existing capability registry (`/api/capabilities`) and the run perimeter | assembling context that silently widens a run's authority | the selected context is recorded in the run record; a run can be replayed from the record and select the same context; a wider perimeter is refused |
| **Context budgeting** | token accounting that already exists in `Usage` | budgets that are announced but not enforced | exceeding the budget fails the run with a named reason instead of truncating silently |
| **Knowledge and memory management** | the four `/api/product/memory` endpoints | memory that outlives its project or its invalidation | every memory entry carries project and provenance; invalidation is visible in the record; no new vector store without a separate decision |
| **Model choice and fallback** | the model registry and provider account state | presenting an unverified model as available | availability is reported as `NOT_CHECKED` until probed; fallback is recorded in the run, not hidden |
| **Planning and queue** | the existing workflow graph and step runner | a second execution engine appearing beside the first | the queue drives the existing runner; no parallel engine; queue state is queryable |
| **Long-running multi-agent runs** | idempotency ledger, run store, approval chain | a resumed run repeating an already-applied action | resumption is idempotent and proven by a test that replays a journal |
| **Provenance, audit and telemetry** | run records and the event metadata policy | telemetry that records secrets or raw payloads | every event passes the existing metadata policy; a record can explain each number it shows |

Two cross-cutting prerequisites apply to all of them: (1) the run-record read path must work
(§4), otherwise nothing can be audited or replayed; (2) an idempotency key on
`POST /api/tasks/run` is needed before any automated dispatch, because today double-submit
protection lives in the interface only.

## 6. Files changed in this stage

* `ui/src/screens/Projects.tsx`, `ui/src/screens/Runs.tsx` (new)
* `ui/src/App.tsx` (routes), `ui/src/i18n.ts` (RU/EN keys)
* `tools/ui_preview_server.py` (write methods proxied — the 501 defect)
* `docs/PHASE3_STAGE1.md` (this file), `docs/images/ui-runs.png`, `docs/images/ui-projects.png`

No module under `app/` was touched. Core, Platform, Fabric, Registry, SQL, RLS, the authorization
chain and the financial semantics are unchanged, and the full suite result for this stage is
recorded in the accompanying report.
