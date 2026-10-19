# Forge AI — Project Completion Ledger

Single register of unfinished agreements, audited against the repository on 2026-10-16.
Status vocabulary: **VERIFIED**, **FAILED**, **NOT VERIFIED**, **BLOCKED**, **PLANNED**, **DECISION REQUIRED**.

Audit basis: working tree at HEAD `19fa11cc63d5` (branch `master`, `origin/master` = `a8ecd4c`), the test suite,
`docs/*.md`, `.github/workflows/*.yml`, and measurements taken in this working session. Earlier chat
transcripts are not treated as evidence; where a claim could not be re-measured it is marked NOT VERIFIED.

## Register

### P0-1 · Desktop EXE rebuild — BLOCKED
* **Requirement:** a real Windows `.exe` launcher, rebuilt reproducibly by `scripts/build_desktop_exe.ps1`.
* **Source:** desktop delivery order; `scripts/build_desktop_exe.ps1`, `tools/forge_desktop_launcher.cs`.
* **Status:** the artifact exists and runs (`dist/Forge AI.exe`, 46080 bytes, built 2026-10-16 14:57:31, sha256 `f1f2671f7372e0a0`), but a
  **fresh rebuild is BLOCKED**: `csc.exe` answers `error CS0016 … "Отказано в доступе"` when it is invoked
  through a nested chain (shell → Python → PowerShell → `csc`). A **direct** `pwsh → csc` invocation
  succeeded earlier in the session, which is how the current artifact was produced.
* **Evidence:** full compiler message captured and decoded (the output is CP1251, not UTF-8); the file-lock
  hypothesis was **refuted** — no process holds the artifact and every candidate file renames cleanly; the
  failure reproduces even with a brand-new unique staging filename.
* **Missing acceptance criterion:** a rebuild whose mtime and SHA-256 differ from the values above.
* **Blocker:** environment/sandbox restriction on `csc` through intermediate parents. Windows policies were
  not changed and were not bypassed.
* **Next step:** the owner runs `powershell -File scripts\build_desktop_exe.ps1` from their own PowerShell,
  then `dist\"Forge AI.exe" --check`, and reports the new size/mtime/hash.
* **Owner decision needed:** no (an action by the owner is required, not a decision).

### P0-2 · Window lifecycle with a busy Edge profile — VERIFIED in tests, NOT VERIFIED live
* **Requirement:** a held `.forge/webview-profile` must not stop the window from opening; occupied profiles
  must not be deleted, foreign Edge/Python processes must not be terminated, and only Forge's own servers
  may be stopped.
* **Source:** incident of 2026-10-16 (journal 15:33: API ready in 7 s, shell in 1 s, no window).
* **Status:** implemented in `tools/forge_launch.py` — `window_profile_candidates()` (primary + bounded
  `-alt1…-alt4`), `claim_window_profile()` (atomic `mkdir(exist_ok=False)`), and
  `open_window_with_profile()` returning `(usable window, profile conflict)` where a conflict is **only** the
  measured early browser exit, never a slow window.
* **Evidence:** `tests/test_launcher_profile_conflict.py` — **6 tests, 0 skips, OK**; `tests/test_api_exposure.py`
  — 16 tests OK.
* **Missing acceptance criterion:** a real window appearing on a busy profile in the owner's session.
* **Next step:** owner check (see manual checklist below).

### P0-3 · Desktop shortcut `Forge AI` — BLOCKED
* **Requirement:** one double-click starts Forge in its own window; correct working directory, launcher path
  and anvil icon; no duplicate shortcuts; `WindowStyle` must not be the minimised show-state.
* **Status:** existing shortcuts carry `WindowStyle = 7` (minimised) and there is no shortcut named exactly
  `Forge AI`. Writing to the desktop is denied from this session (`UnauthorizedAccessException`).
* **Evidence:** shortcut inspection via `WScript.Shell`; repeated denied writes.
* **Next step:** owner runs the shortcut command (below) once.
* **Owner decision needed:** no.

### P0-4 · Test suite and CI — FAILED (one error) / BLOCKED (CI cause)
* **Requirement:** a full green suite on the final tree; a CI run that completes.
* **Status:** fast checks are green (`compileall` 0, `git diff --check` 0, `tsc` 0, `vite build` 0, the four
  targeted modules OK). The last full run reported `Ran 2524 tests in 638.899s → FAILED (errors=1)` with the
  single error in `test_acceptance_production.CriterionIdentityTests.test_run_reports_one_consistent_criterion_identity`.
  That module passes **3/3 in isolation** (`Ran 43 tests` → OK, 20.7/24.9/22.7 s), so the failure is
  **context-dependent and not yet explained** — it is not called flaky without evidence.
* **CI:** `platform-postgres` passes; the general suite is cancelled at the 90-minute job limit; the
  diagnostic job is cancelled at GitHub's 1 h 40 m hard cap. Timing annotations show the first ~10 modules
  running in 0.0–0.8 s with `fail=0 err=0` (faster than locally), so the suite is **not uniformly slow**;
  the alphabetical successor of the last reported module (`test_api_execution_scope`) is
  `test_api_host_execution` (≈55 s locally) — **hypothesis**, not proof.
* **Next step:** re-run the full suite; if the acceptance error repeats, capture the assertion from that same
  run and compare with the isolated logs; add a per-module timeout to the diagnostic job so a hanging module
  names itself.
* **Owner decision needed:** no.

### P1-1 · Real end-to-end user scenario — VERIFIED for declared execution, BLOCKED for live planning
* **Requirement:** task → clarification → editable plan → explicit confirmation → authorised execution → real
  artifacts → result checks → history and status.
* **Status:** the declared-execution path is proven: through the product's step graph the engine executed two
  real commands (`build` exit 0, `verify` exit 0), produced 15 passing tests and a working WorkLog
  application (21/21 HTTP checks including Cyrillic and persistence across restart).
* **What is *not* proven:** live-model planning inside Forge. The only wired provider is the deterministic
  offline mock; no paid call has been authorised, so no model has ever produced a plan in production.
* **Also honest:** the WorkLog application is an isolated example under `.forge/e2e-worklog/`, not integrated
  production functionality.
* **Next step:** prepare the live-model scenario and stop before the paid call — **BLOCKED — OWNER APPROVAL REQUIRED**.
* **Owner decision needed:** yes (spending authorisation).

### P1-2 · Project creation and the Core/Platform trust boundary — PLANNED / DECISION RECORDED
* **Requirement:** a user can create a project from the UI; the project is persisted, isolated, and usable for
  a task; a client-supplied project id is not authorisation.
* **Status:** the Core API `/api/projects` is a **read-only single-workspace stub**
  (`app/api/service.py::list_projects` returns one synthetic `ProjectInfo`); there is no project write path in
  `app/`. Real project services with owner, organisation and RLS live in the Platform layer
  (`tests/test_platform_application_discovery.py` calls `list_projects(owner, org)`).
* **Decision recorded (owner, this session):** Platform is the primary project-management layer; Core stays the
  independent execution core and must not gain a second project store.
* **Missing acceptance criterion:** a vertical slice create → authorise → persist → display → use in a task.
* **Open architectural gap:** `POST /api/tasks/run` accepts `project_id` from the client, while the invariant
  says a client project id is not permission. A verifiable Core→Platform check is required before the slice is
  safe. **DECISION REQUIRED** on that contract before implementation.
* **Next step:** present the minimal slice and its acceptance criteria for approval; do not implement yet.

### P1-3 · Platform/Core invariants — NOT VERIFIED (no re-measurement this session)
* Recommendation ≠ Permission; Permission ≠ Execution; Report ≠ Evidence; server-side derivation of
  organisation/project/workspace; Core independence; no second authoritative lifecycle in Platform; no
  plaintext secrets in DTOs, logs or docs.
* **Status:** no invariant violation was observed in the code read this session, but the full Platform/Core
  surface was **not** audited here. `authorizer.py` was not read or modified.
* **Next step:** a dedicated read-only audit of the Platform/Core boundary, recorded in this ledger.

### P2-1 · UI refinement — PLANNED (brand layer VERIFIED)
* **Status:** branding now rides the existing design tokens (`ui/src/design/tokens.css` — 9 brand token
  overrides: anvil gold, restrained blue, charcoal surfaces) plus identity/state helpers in
  `ui/src/design/components.css`. No second theme, no route or data changes. The unused `ui/src/brand.css`
  was removed.
* **Not done:** structural sidebar/topbar refinement, which the owner deferred until P0 passes.
* **Next step:** audit the existing sidebar/topbar and states, then make the smallest coherent change.

### P2-2 · Architecture backlog (Stage 1.5 / Master Context) — PLANNED
Capability & Resource Fabric and capability-aware routing; deduplicated routing; cached-token usage;
project-state/run/artifact linkage; resource resolution; durable execution with checkpoints and pause/resume;
event log, provenance and observability; Golden Projects and the Forge Evaluation Suite; Decision Inbox and
visual project map; dry run, rollback and human takeover; cost/resource budgets and background jobs; project
health; controlled evolution of Forge Knowledge.
* **Status:** none of these was implemented or verified this session; each needs a decision on whether it is
  required for the current stage. Not to be mixed with approved contracts.

### P3 · Temporary files — inventoried, not deleted
* Safe to remove once their origin is confirmed: `pip-metadata-g0rrt5yb/`, `pip-metadata-p353jwjm/`,
  `pip-unpack-5fbps_tg/`, `pip-unpack-br33raxi/` (residue of a failed PyInstaller install attempt in this
  session), `forge_b10_runstore_srnngvs0/` (earlier test residue).
* Must not be touched: `Forge-AI-DeepSeek-Review-2026-10-04.zip`, `INDEPENDENT_STAGE1_AUDIT.md`,
  `tests/test_project_factory_vertical_slice.py~RF1a139fd6.TMP` (protected untracked artifacts), the WorkLog
  artifacts under `.forge/e2e-worklog/`, and `.forge/` build and log output.

## Manual checklist for the owner (window verification)

1. Close every Forge window and server process.
2. Run the shortcut command once (creates `Forge AI` and removes `WindowStyle = 7`).
3. Double-click `Forge AI` (or `dist\Forge AI.exe`): the window must appear; header and title `Forge AI`; anvil icon.
4. Verify: minimise leaves Forge on the taskbar; restore works; maximise/resize works; close exits cleanly.
5. After closing, no Forge `python.exe`/`pythonw.exe` remains in Task Manager.
6. With a busy profile, `.forge/logs/launcher.log` must show `retrying the window with a separate profile`.

## Session update — 2026-10-16 17:45

**Fresh full suite on the current tree: GREEN.**
`python .forge\run_confined.py discover -s tests -v` → `Ran 2524 tests in 899.828s` → `OK (skipped=36)`, exit 0.

Fast checks, all exit 0: `test_launcher_profile_conflict.py` (6 tests, 0 skips), `test_api_exposure.py` (16),
`test_production_composition_idempotency.py` (8), `test_acceptance_production.py` (43), `compileall app tests`,
`git diff --check`, `tsc --noEmit`, `vite build`.

**New measured evidence for the CI investigation (P0-4).** The same suite on the same tree took
**312 s → 638 s → 899 s** across three runs today: a ~3x spread. One of those runs failed once in
`test_acceptance_production.CriterionIdentityTests.test_run_reports_one_consistent_criterion_identity`,
which then passed 3/3 in isolation and passed again in the latest full run: **1 failure in 2 full runs,
0 in 3 isolated runs** — intermittent and still unexplained. A variable-duration run plus a CI job that is
cancelled at its limit is a better lead than any single test being uniformly slow.

**EXE (P0-1) remains BLOCKED** with the same measured cause: `csc.exe` through a nested parent chain returns
`CS0016 "Отказано в доступе"`; a direct `pwsh → csc` invocation works. Artifact unchanged:
`dist/Forge AI.exe`, 46 080 bytes, 2026-10-16 14:57:31, sha256 `f1f2671f7372e0a0`.

**No commit and no push were performed.** The working tree holds five modified files and the new launcher,
build script, EXE and two test files; all await the owner's review of the change set.

## Session update — P0-A closed, P0-C measured (2026-10-16 18:10)

**P0-1 (EXE) is now CLOSED — VERIFIED.** The cause of `CS0016` is proven to be the **nested parent chain**:
`csc.exe` refuses to write when it is reached through shell → Python → PowerShell → `csc`, and succeeds when
the build script is invoked **directly from PowerShell**. Running `powershell -File scripts\build_desktop_exe.ps1`
from the shell produced:

* `Built: C:\Users\User\Desktop\Forge-AI\dist\Forge AI.exe (45 KB)`, script exit **0**;
* new artifact: size **46080**, modified **2026-10-16 18:00:20**, SHA-256
  **E5E2013B403523A1ABB64499A022D653BD20A0B70D4E7CDDFA6F3A793270B88D**
  (previous build: 14:57:31, `F1F2671F7372E0A0…`) → **changed: True**;
* `--check` ran **on the new artifact** by the script itself: "All prerequisites present", `exit code 0`.

Rule for the future: **build from PowerShell directly**; do not drive the build through another interpreter.
The failed automated attempts are recorded, not hidden.

**P0-4 (duration).** Local measurements of the suspected heavy modules, one process each:

* `test_project_discovery_production.py` — 3.7s — Ran 36 tests in 2.678s | OK
* `test_desktop_program.py` — 4.9s — Ran 42 tests in 3.766s | OK
* `test_api_host_execution.py` — 2.0s — Ran 27 tests in 0.563s | OK
* `test_agent_harness_ai_integration.py` — 1.5s — Ran 16 tests in 0.752s | OK (skipped=1)

Measured total for these four: **12.2s**. Full-suite wall clock on the same tree varied
312 s → 638 s → 899 s, so the spread is not explained by any single module being uniformly slow; the numbers
above are the beginning of a per-module profile and are **measurements, not a conclusion**. The honest next
step remains a per-module timeout plus timing annotation in the diagnostic CI job (it does not gate, and the
mandatory job is unchanged), so a hanging module names itself instead of the job dying at the 1 h 40 m cap.

## Proposed Core ↔ Platform contract for `project_id` (2026-10-16 18:33) — DECISION REQUIRED

**Facts measured in the code.** `app/api/service.py::list_projects` returns one synthetic `ProjectInfo` for the
service's own workspace: the Core API has no project store. `POST /api/tasks/run` accepts `project_id` from the
request body. The Platform layer owns real projects with owner, organisation and RLS
(`tests/test_platform_application_discovery.py` calls `list_projects(owner, org)`). Therefore a client-supplied
`project_id` reaches the Core today without any check that the caller may use that project.

**Minimal proposal — one injected authority, no second store, no migration.**
1. Core defines a narrow protocol (in `app/`, no Platform import):
   `authorize(principal, project_id) -> AuthorizedProject(project_id, organization_id, workspace_root)`,
   raising a typed refusal for unknown project, missing membership or a workspace outside the trusted root.
2. The Core derives the execution workspace **only** from the returned `AuthorizedProject`; the request's
   `project_id` is a lookup key, never a path and never permission.
3. The production composition injects an adapter over the existing Platform services; tests inject a
   deterministic in-memory double. No Platform object ever enters Core DTOs, logs or responses.
4. Fail-closed by construction: a missing authority, an unknown principal, a project in another organisation,
   or a workspace outside the trusted root all refuse **before** any run record or command.

**Acceptance tests for the slice.** allowed owner runs in the project's workspace; a non-member is refused
before execution with no run record; a substituted `project_id` from another organisation is refused; an
unknown project is refused; a missing authority refuses; another project's workspace path is never used;
project and run remain isolated; Platform persistence survives a restart.

**Why no code was written yet.** The check above changes a Core execution contract, so it needs the owner's
approval of the wording and of the Platform adapter's source of truth; nothing in the current schema blocks it,
and no migration is required.

## Diagnostic CI instrumentation — attempted, reverted, honest outcome

**What was tried.** `FORGE_MODULE_TIMEOUT: "300"` was added to the diagnostic only job and a
`MODULE_TIMEOUT_SECONDS` constant was read in `scripts/run_test_shard.py`.

**Why it was reverted.** Measured: the diagnostic runner executes each module **inside its own process**
(`unittest.TestLoader()` + `TextTestRunner`), so a per-module subprocess timeout has no call site and the
variable would have changed nothing. It was removed rather than left in place as decoration, and
`.github/workflows/platform-postgres.yml` is byte-identical to its committed state (verified with
`git status`/`git diff` on that path).

**What is actually known.** The diagnostic job dies at GitHub's 1 h 40 m hard cap and only the first modules'
timings are published; the general suite is cancelled at its 90-minute limit; per-module timings measured
locally are all small (2.678 s / 3.766 s / 0.563 s / 0.752 s), so no single module explains the 312/638/899 s
spread. **The cause is not established.**

**Minimal proposals, in order of preference, none of them touching a mandatory job.**
1. Split the diagnostic suite across several jobs by module range so each job's annotations publish before any
   cap — the same instrument, just chunked; no runner change and no test change.
2. Write the per-module timings to `$GITHUB_STEP_SUMMARY` as they are measured, so the partial result is
   visible even when the job is cancelled.
3. Only if 1 and 2 are insufficient: give the diagnostic runner a real per-module subprocess boundary with a
   timeout. That is a runner change with its own design and its own review; it must never gate the mandatory
   suite.

## Core ↔ Platform project authority — contract implemented (`app/api/project_authority.py`)

**Owner decision applied:** `authorize(principal, project_id) -> AuthorizedProject` is the seam. The Core owns
execution only; projects, membership and organisations stay with the Platform, so no second project store was
created and no migration was introduced.

* `ProjectAuthorityRefusal` carries a stable `code`; `AuthorizedProject` carries `project_id`,
  `organization_id`, `workspace_root`, `principal_id`, `name`.
* `AbsentProjectAuthority` is the default and **always refuses** (`project_authority_unavailable`): without a
  composed authority no project-scoped operation is permitted.
* `InMemoryProjectAuthority` performs real membership checks against a trusted root; it refuses
  `project_unknown`, `project_forbidden`, `project_workspace_untrusted`, `principal_without_organization`,
  `project_name_required`.
* `workspace_root` is resolved and must lie inside the trusted root; the incoming `project_id` is only a
  lookup key.

**Tests — `tests/test_project_authority.py` (9 required behaviours).** Allowed project; unauthorised principal;
project of another organisation; substituted project id; missing authority (fail-closed); untrusted workspace;
**no dispatch at all when authorisation is refused**; project isolation; and a created project surviving a
restart of the authority.

**Deliberately NOT done in this step, and not claimed:** the HTTP and UI wiring. `ForgeApiService.__init__`
could not be extended safely by pattern in this session (its exact signature differs from the assumed shape),
and a blind edit to a Phase 2 file was refused rather than guessed. The next step is therefore explicit:
compose the authority through `create_api_app(project_authority=...)`, expose `POST /api/projects`, and make
the run route authorise a supplied `project_id` **before** any run record exists. Until that is done, the
contract is real and tested but **not yet reachable from the interface**.

## HTTP wiring attempt — safely rejected three times, exact blocker recorded

**What was attempted.** Adding `project_authority` / `principal_resolver` to `create_api_app`, publishing them
on `app.state`, exposing `POST /api/projects` and `GET /api/projects/authorized`, and inserting an authorisation
guard at the top of the `POST /api/tasks/run` handler.

**What happened.** Every attempt was validated **in memory** (`compile()` on the patched source) before any
write, and every one was **rejected**: first because the inserted decorators carried four spaces of indentation
where the file has none for that route block, then because the route block's *body* text did not match the
shape assumed from trimmed `grep` output. **`app/api/server.py` and `app/api/service.py` therefore contain no
partial edit of this work** — the check above asserts that directly.

**Exact blocker, no guessing.** The routes in `app/api/server.py` must be patched from their **verbatim**
source, read with the file reader rather than a trimmed listing. The correct sequence is: read the
`create_api_app` body and the `/api/tasks/run` handler verbatim, then apply the same patch with that exact
text; keep the in-memory `compile()` gate before writing.

**Proven so far (unchanged by the failed attempts).** `app/api/project_authority.py` implements the accepted
contract with a fail-closed default, and `tests/test_project_authority.py` proves all nine required
behaviours (allowed project; unauthorised principal; other organisation; substituted id; missing authority;
untrusted workspace; no dispatch on refusal; isolation; persistence across restart).

**Not done, not claimed.** The HTTP layer, the UI form, and the production Platform adapter. Until the routes
are wired, the contract is **not reachable from the interface**, and the Platform remains the system of record
only in design, not in code.

## HTTP wiring — verbatim anchors established, fourth attempt withheld

**New data (read with the file reader, not a trimmed listing).**
* `create_api_app` builds the service verbatim as `api_service = service or ForgeApiService(idempotency_guard=..., approval_policy=ApprovalPolicy())`, then `app = FastAPI(title="Forge AI API", version="0.1.0")`, registers every route inside the factory (decorators at **4** spaces, bodies at **8**) and ends with `return app`.
* `POST /api/tasks/run` is declared as `def run_task(payload: dict[str, Any], idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> Any:` — **there is no `Request` parameter**, so an authorisation guard must add one (FastAPI injects it; it adds no body field, so the request-schema guard is unaffected).
* The first side effect in that handler is the `TaskRunRequest` construction, followed by `api_service.run_task_detailed(...)`. The guard therefore goes immediately above the `# The typed body parameter must stay:` comment, which is the first statement in the body.
* `app/api/server.py` and `app/api/service.py` once again contain **no** trace of this work (asserted above).

**Why the fourth attempt was withheld.** The previous failure was in my own patch generator (an f-string brace error), not in the server code and not in the environment. Re-running the same generator would repeat it; the corrected generator must build the endpoint text with plain concatenation instead of f-strings, keep the in-memory `compile()` gate, and apply the anchors listed above. That is a mechanical change with the data now in hand.

**Step 2 — the trust source, measured.** `app/api/main.py` states it plainly: this surface **has no authentication**, and it refuses to bind beyond loopback for that reason. There is therefore **no trusted principal in the current Core HTTP surface**: `principal_id`, `organization_id` and any workspace sent by a client are untrusted by construction. The consequence is architectural, not cosmetic: project-scoped operations on this surface can only be **fail-closed (503)** until a trusted identity source — the Platform's owner/organisation membership with RLS — is composed into it. No fake authorisation was introduced, and the `InMemoryPrincipalResolver` exists solely for tests.

**Still not connected:** `POST /api/projects`, `GET /api/projects/authorized`, the run-route guard, the UI form and the production Platform adapter. Until those exist, the vertical slice is **not** usable from the interface.

## Platform-first project slice — reconnaissance (owner chose path 2)

**Decision recorded:** project creation and management belong to the Platform. The Core does **not** gain
project CRUD routes, and its pinned request contract is not changed for this feature.

**Two blockers were already proven by measurement (previous step), and they define the work:**
1. `POST /api/projects` cannot be added to the Core surface: the project's own request-schema guard refuses any
   new body-taking route with `invalid_request_schema` (`executed: false`). Changing that guard is a contract
   change, which path 2 avoids entirely.
2. Enforcing project authorisation inside `POST /api/tasks/run` changes existing behaviour: it broke
   Phase 2's isolation test, which legitimately dispatches with arbitrary `project_id`s today. So the Core
   currently accepts an unvalidated `project_id`, and tightening it must be done through a trusted composed
   context rather than by reinterpreting a client field.

**Trusted identity is still missing at the Core surface.** `app/api/main.py` documents that the surface has no
authentication and refuses non-loopback binds, so `owner_id`, `organization_id` and `workspace_root` sent by a
client are untrusted by construction. Path 2 therefore requires the trusted identity to be established inside
the Platform, where owners, organisations, membership and RLS already live, and only a verified context to
cross into the Core.

**Next concrete step.** Locate the Platform's real project service and membership checks (this entry is
followed by that reconnaissance), then add the Platform-side create/list endpoints and the UI form there, and
pass a verified project context to the Core without altering the Core's public request contract.

## Platform facts, extracted verbatim from the code

Search basis, stated precisely so absence is not over-claimed: a pattern scan over all 211 modules under `app/` for `def create_project`, `def register_project`, `class ProjectService`, `class ProjectRegistry`, `def list_projects`, `@router.(post|get)`, `APIRouter(`, plus targeted reads of the files below. No `create_project` implementation and no `APIRouter` registration were found **by that search**; this does not prove they exist nowhere else in the repository.

### platform project listing service

```
app/platform/application/organizations.py:208: 
app/platform/application/organizations.py:209:     # -- AS-2 --------------------------------------------------------------- #
app/platform/application/organizations.py:210:     async def list_projects(
app/platform/application/organizations.py:211:         self,
app/platform/application/organizations.py:212:         principal: Optional[AuthenticatedPrincipal],
app/platform/application/organizations.py:213:         organization_id: Optional[str],
app/platform/application/organizations.py:214:     ) -> Sequence[ProjectSummary]:
app/platform/application/organizations.py:215:         """AS-2: the projects in the organization the caller selected.
app/platform/application/organizations.py:216: 
app/platform/application/organizations.py:217:         ``organization_id`` is a **claim**. It is checked against the caller's own
app/platform/application/organizations.py:218:         candidate set, and the server never substitutes a different one. A claim outside
app/platform/application/organizations.py:219:         the set is answered ``not_found`` rather than ``not_permitted``, so the caller
app/platform/application/organizations.py:220:         cannot tell another tenant's identifier from a non-existent one (design section
app/platform/application/organizations.py:221:         6.3).
app/platform/application/organizations.py:222:         """
app/platform/application/organizations.py:223: 
app/platform/application/organizations.py:224:    
```

### trusted principal

```

```

### existing project authorisation helper

```
app/api/routes.py:598: 
app/api/routes.py:599: 
app/api/routes.py:600: def authorize_project(app: Any, project_id: object) -> str:
app/api/routes.py:601:     """The one authorization check for the project boundary.
app/api/routes.py:602: 
app/api/routes.py:603:     ``project_id`` is optional at the HTTP layer: when it is absent the request is
app/api/routes.py:604:     about the app's own project, which is what a single-project deployment means.
app/api/routes.py:605:     When it IS supplied it must match exactly, because a mismatch means the caller
app/api/routes.py:606:     is asking for a different project than the one this app is bound to -- and the
app/api/routes.py:607:     answer to that is a refusal, not an empty list.
app/api/routes.py:608: 
app/api/routes.py:609:     Returns the authoritative project id. Raises ``PermissionError`` on a
app/api/routes.py:610:     mismatch, which the caller converts into a 403.
app/api/routes.py:611:     """
app/api/routes.py:612: 
app/api/routes.py:613:     authoritative = str(app.state.forge_project_id)
app/api/routes.py:614:     if project_id is None or project_id == "":
app/api/routes.py:615:         return authoritative
app/api/routes.py:616:     if not isinstance(project_id, str) or project_id != authoritative:
app/api/routes.py:617:         # The supplied value is deliberately NOT echoed back: reflecting a
app/api/routes.py:618:         # caller-supplied string into an error body is how a response becomes a
app/api/routes.py:61
```

### core active workspace

```
app/api/service.py:471:         self._dashboard_service = dashboard_service or CostDashboardService(
app/api/service.py:472:             fabric=self._fabric,
app/api/service.py:473:             health_monitor=self._health_monitor,
app/api/service.py:474:         )
app/api/service.py:475:         self._active_project_id = "default"
app/api/service.py:476: 
app/api/service.py:477:     def _effective_tool_ids(self) -> frozenset[str]:
app/api/service.py:478:         """Compute the tools this service may authorize for a Run.
app/api/service.py:479: 
app/api/service.py:480:         Effective tools are the operator allowlist intersected with the tools
```

### platform identity modules

```
app\platform\application\authorization.py
app\platform\principal.py
```


### Platform project path — verbatim read of the repository and the service

The application service method is `async def list_projects(self, principal, organization_id) -> Sequence[ProjectSummary]`
in `app/platform/application/organizations.py`, and it opens a tenant-scoped unit of work through
`self._authorized(principal, organization_id)` before calling `unit.projects.list_for_current_tenant()`.
The repository is reached only through that unit, so tenant isolation is structural rather than a filter the
caller can forget.

### ProjectRepository contract — read verbatim

```
app/platform/persistence/protocols.py:165:     holding ``INSERT`` on ``memberships``.
app/platform/persistence/protocols.py:166:     """
app/platform/persistence/protocols.py:167: 
app/platform/persistence/protocols.py:168:     async def create(self, organization_id: str, membership: Membership) -> Membership:
app/platform/persistence/protocols.py:169:         """Insert a membership for an explicitly named organization."""
app/platform/persistence/protocols.py:170: 
app/platform/persistence/protocols.py:171: 
app/platform/persistence/protocols.py:172: @runtime_checkable
app/platform/persistence/protocols.py:173: class ProjectRepository(Protocol):
app/platform/persistence/protocols.py:174:     """Tenant-owned work containers.
app/platform/persistence/protocols.py:175: 
app/platform/persistence/protocols.py:176:     There is no ``organization_id`` parameter anywhere: a project is created in,
app/platform/persistence/protocols.py:177:     listed for, and fetched from the tenant of the surrounding Unit of Work.
app/platform/persistence/protocols.py:178:     """
app/platform/persistence/protocols.py:179: 
app/platform/persistence/protocols.py:180:     async def create(
app/platform/persistence/protocols.py:181:         self, project_id: str, name: str, *, slug: str = "",
app/platform/persistence/protocols.py:182:         workspace_ref: str = "",
app/platform/persistence/protocols.py:183:     ) -> Project:
app/platform/persistence/protocols.py:184:         """Insert a project in the current tenant.
app/platform/persistence/protocols.py:185: 
app/platform/persistence/protocols.py:186:         ``workspace_ref`` is stored as given and interpreted nowhere. It is a
app/platform/persistence/protocols.py:187:         server-derived opaque reference, not a filesystem path.
app/platform/persistence/protocols.py:188:         """
app/platform/persistence/protocols.py:189: 
app/platform/persistence/protocols.py:190:     async def get(self, project_id: str) -> Optional[Project]:
app/platform/persistence/protocols.py:191:         """Return a project of the current tenant, or ``None``."""
app/platform/persistence/protocols.py:192: 
app/platform/persistence/protocols.py:193:     async def list_for_current_tenant(self) -> Sequence[Project]:
app/platform/persistence/protocols.py:194:         """Return the current tenant's projects."""
app/platform/persistence/protocols.py:195: 
app/platform/persistence/protocols.py:196:     async def find_by_slug(self, slug: str) -> Optional[Project]:
app/platform/persistence/protocols.py:197:         """Return a project by its tenant-scoped slug, or ``None``."""
app/platform/persistence/protocols.py:198: 
app/platform/persistence/protocols.py:199:     async def set_name(self, project_id: str, name: str) -> Project:
app/platform/persistence/protocols.py:200:         """Rename a project."""
app/platform/persistence/protocols.py:201: 
app/platform/persistence/protocols.py:202:     async def set_slug(self, project_id: str, slug: str) -> Project:
app/platform/persistence/protocols.py:203:         """Change a tenant-scoped slug. Uniqueness is enforced by the database."""
app/platform/persistence/protocols.py:204: 
app/platform/persistence/protocols.py:205:     async def set_workspace_ref(self, project_id: str, workspace_ref: str) -> Project:
app/platform/persistence/protocols.py:206:         """Set the opaque workspace reference."""
app/platform/persistence/protocols.py:207: 
app/platform/persistence/protocols.py:208:     async def set_status(self, project_id: str, status: ProjectStatus) -> Project:
app/platform/persistence/protocols.py:209:         """Activate or archive."""
app/platform/persistence/protocols.py:210: 
app/platform/persistence/protocols.py:211: 
app/platform/persistence/protocols.py:212: @runtime_checkable
app/platform/persistence/protocols.py:213: class TenantProviderAccountRepository(Protocol):
app/platform/persistence/protocols.py:214:     """Provider credentials owned by a tenant (BYOK).
app/platform/persistence/protocols.py:215: 
```

## create_project — implementation point reached, three facts still unread

**What is now verified about the Platform application service** (`app/platform/application/organizations.py`,
256 lines):

* the service class is **`DiscoveryService`** (line 98), holding the AS-1/AS-2 use cases;
* `async def list_projects(self, principal, organization_id) -> Sequence[ProjectSummary]` (line 210);
* `async def _authorized(self, principal, organization_id)` (line 159) resolves the caller with
  `self._resolve(principal)`, selects the organization with `select_candidate(candidates, organization_id)`
  and opens the tenant transaction with `self._tenant.tenant(str(selected.id), user_id=subject.subject_id)`,
  yielding `(AuthorizedContext, tenant_unit)` — so a create inside that block is tenant-scoped by construction;
* `ProjectSummary(project_id, name, slug, status)` (line 81) with `to_dict`, and `_summarize` (line 199)
  converts a stored project into it;
* the refusal convention is `translate_persistence_error(exc)` from `app.platform.application.errors`;
* the repository operation already exists and needs no change:
  `ProjectRepository.create(self, project_id: str, name: str, *, slug: str = "", workspace_ref: str = "")`
  (`app/platform/persistence/protocols.py:180`), implemented by `PostgresProjectRepository` with
  `INSERT INTO projects`, and it takes **no organization_id** because the Unit of Work carries the tenant.

**The three facts still unread, and why no code was written without them.** Writing `create_project` needs
(1) the exact body of `list_projects` and `_summarize` to mirror their error handling and summarisation,
(2) the project's existing identifier convention — the repository receives `project_id` from the service, so
the service must generate it, and the project's own convention for that was not yet read, and (3) the
application-service test fixture conventions (`tests/test_platform_application_discovery.py`, 37 tests) so a
new test uses the same harness rather than a parallel one. Guessing any of the three in the Platform's most
security-sensitive layer is exactly the failure mode this session already hit three times with blind patches,
so it is deliberately not attempted.

**Next step, precisely.** Read `organizations.py` lines 193–256 (`list_projects`, `_summarize`,
`authorized_context`) and the fixture in `tests/test_platform_application_discovery.py`; then add

```python
async def create_project(self, principal, organization_id, name, *, slug="", workspace_ref="") -> ProjectSummary
```

that opens `async with self._authorized(principal, organization_id) as (context, unit)`, derives `project_id`
and `workspace_ref` **server-side** (never from the caller), calls `unit.projects.create(...)` and returns
`self._summarize(created)`. Owner and organization come only from `_authorized`; `workspace_ref` must be
server-derived and opaque (the domain contract test asserts it is not a path authority:
`test_project_workspace_reference_is_opaque_not_a_path_authority`).

## create_project — status after implementation, and the O-2 memo

### What is IMPLEMENTED (code exists, compiles, no regression)

`DiscoveryService.create_project(principal, organization_id, name, *, slug="", workspace_ref="")` in
`app/platform/application/organizations.py`:
`async with self._authorized(principal, organization_id) as (_, unit)` → `unit.projects.create(new_id(), name,
slug=slug, workspace_ref=workspace_ref)` → `translate_persistence_error` on failure → returns the existing
`ProjectSummary`. The identifier uses the house convention `new_id()` (`app/platform/models.py:85`), the
repository takes no `organization_id` (the Unit of Work carries the tenant), and `workspace_ref` stays the
opaque server reference the domain contract already asserts is not a path authority.

Evidence actually obtained: `python -m compileall -q app/platform/application/organizations.py` → exit 0;
`tests/test_platform_domain_contracts.py` → `Ran 58 tests … OK`;
`tests/test_platform_application_discovery.py` → `Ran 3 tests in 15.159s … OK (skipped=6)`.

### What is NOT TESTED (the honest gap)

No test yet exercises `create_project` itself. The suite that must host those tests states its own
requirement in its docstring: it runs against **real PostgreSQL 17**, because what it asserts is behaviour a
fake database cannot have (row-level security refusing an unauthorised read, subject-bound discovery, a
suspended account being invisible, and the non-disclosure rule measured as identical code *and* message for a
cross-tenant and a non-existent identifier). The throwaway database, its migrations and the two
non-privileged logins come from `tests/persistence_fixture.py`.

Writing those tests therefore needs the fixture's exact harness conventions and a live PostgreSQL, neither of
which was read or available in the remaining session budget. They are **not** claimed as done, and no mock
test is presented as evidence of PostgreSQL persistence.

Required cases, ready to write once the harness is read: creation through `DiscoveryService.create_project`;
the created project appearing in `list_projects`; refusal for an organization outside the caller's candidate
set (answered `not_found`, not `not_permitted`); refusal for a missing or invalid principal; and a persistence
error translated by the existing convention.

### O-2 memo — credential verification (DECISION REQUIRED, no mechanism chosen)

**Verified context.** `AuthenticatedPrincipal` (`app/platform/principal.py:125`) carries four fields only
(`subject_id`, `subject_kind`, `method`, `verified_at`) and deliberately excludes organization, project, role
and workspace; the bare constructor raises and only `create()` builds one. The application tests use an
authentication double that "lives in `tests/`, is never imported from `app/`", and the docstring records
credential verification as open decision **O-2** with **no mechanism shipping**. Nothing in `app/` was found
that constructs a principal from an inbound request.

**Options compatible with this architecture** (each keeps the verification outside the application services,
so no service changes and no new trust in request fields):
1. **Reverse-proxy / gateway-asserted identity** — a trusted local component terminates authentication and
   passes a signed, short-lived assertion; the API verifies the signature and mints the principal. Fits the
   loopback-only Core surface, needs a shared verification key and clock discipline.
2. **Local session store owned by Platform** — credentials verified by a Platform component that stores only
   password hashes, issues an opaque session token, and resolves it to a subject; the API looks the token up
   server-side. No third-party dependency; needs session lifecycle and revocation.
3. **External OIDC provider** — the API validates an ID/access token against the provider's keys and maps
   `sub` to `subject_id`. Fewest secrets to store; adds an external dependency and a redirect flow, and is the
   least reversible of the three.

**How the credential would be checked (all options).** The check happens in the HTTP composition only, before
any application service is called; an absent, malformed, expired or revoked credential yields a refusal before
any read or write. `AuthenticatedPrincipal` is then built through `create()` with `method` recording how the
subject was established, and passed as the ordinary `principal` argument that `DiscoveryService` already
takes. No header, body field or query parameter is ever treated as identity without that verification step.

**How refusal is tested.** Absent credential → refusal, no service call observed; malformed or wrong-scheme
credential → refusal; expired or revoked credential → refusal; a credential for subject A used to address
subject B's organization → the existing `not_found` non-disclosure answer. These tests belong beside the
authentication adapter, not in the application services.

**No choice is made here.** Selecting an option is an owner decision; options 1 and 2 avoid adding an external
identity provider, which makes them the more reversible starting points.

### create_project tests — harness mapped, database measured, still NOT TESTED

**Harness entry points now known, so the tests can be written without another discovery pass:**

* `tests/persistence_fixture.py` (350 lines): `live_postgres()` (skip helper), `new_database_name()`,
  `class PersistenceDatabase` (throwaway database + migrations), `class TenantSeed`, and the DSN helpers
  `admin_kwargs()`, `connection_parts()`, `app_login_kwargs(database)`, `app_dsn(database)`,
  `system_dsn(database)`.
* Configuration arrives through environment variable **names** only (values never read or printed):
  `FORGE_PLATFORM_TEST_HOST`, `FORGE_PLATFORM_TEST_PORT`, `FORGE_PLATFORM_TEST_USER`,
  `FORGE_PLATFORM_TEST_PASSWORD`, `FORGE_PLATFORM_TEST_DATABASE`,
  `FORGE_PLATFORM_TEST_APP_LOGIN_PASSWORD`.
* The suite to extend is `tests/test_platform_application_discovery.py`, class
  `ApplicationDiscoveryTestCase` (`setUpClass` line 321, `tearDownClass` line 349). Its existing patterns are
  exactly the ones a create test must follow: `AS2ListProjectsTests.test_020_lists_the_projects_of_the_selected_organization`
  for listing, and `AS1IdentityTests.test_010_no_principal_is_refused` for the no-principal refusal.

**Measured database state.** Running the suite in this environment gives
`Ran 3 tests in 15.294s | OK (skipped=6)` with the database-dependent cases **skipped** rather than failing: PostgreSQL 17 is not running
here, and the fixture degrades by skipping. Therefore the five required `create_project` cases cannot be
executed in this environment, and `create_project` stays **IMPLEMENTED / NOT TESTED**. No mock was substituted
for the integration check.

**Ready to write, in this order:** (1) create in an available organization; (2) the created project appearing
in `list_projects`; (3) an organization outside the caller's candidate set answered `not_found`, never
`not_permitted`; (4) a missing and an invalid principal refused with no write; (5) a persistence error
translated by `translate_persistence_error`. Status moves to TESTED only after those run green against the
real database.

### create_project tests written; test_074 isolation fixed; integration still BLOCKED

**Fixed.** `AS3CreateProjectTests.test_074` closed the shared `self._tenant_database` that
`ApplicationDiscoveryTestCase` owns, which violated the fixture lifecycle (``tearDown`` would then close an
already-closed connection). It now opens its **own** connection with the pattern the harness itself uses in
``setUp`` — ``run_async(RecordingDatabase(self._handle.dsn).connect())`` — closes that connection on purpose
to provoke a genuine driver-level persistence failure, wraps it in ``DiscoveryService(self._database, broken)``,
asserts the failure surfaces as an application error carrying a ``code`` from the closed list rather than the
driver exception, and closes only its own connection in ``finally``. Verified by reading the method back:
it no longer contains ``self._tenant_database.close()``.

**Added but not executed.** Five tests — ``test_070`` (create in an available organization), ``test_071``
(the created project appears in ``list_projects``), ``test_072`` (a foreign or unknown organization answers
the same ``NOT_FOUND`` code *and* the same message, never ``NotPermitted``), ``test_073`` (a missing or
invalid principal is refused and the project spread is unchanged afterwards), ``test_074`` (persistence
error translated).

**Exact reason for BLOCKED.** No PostgreSQL 17 is reachable from this environment: ports 5432/5433/5434
refused, no installation under Program Files, no Windows service, no ``psql``/``postgres``/``initdb`` on
PATH, and no docker or podman. ``asyncpg`` is present; ``FORGE_PLATFORM_TEST_DSN`` is not configured.

**What a skipped run does and does not prove.** ``Ran 3 tests … OK (skipped=7)`` shows the new class is
collected and wired into the existing harness; it does **not** confirm ``create_project``. ``create_project``
therefore stays **IMPLEMENTED**, and the tests stay **BLOCKED** — not TESTED — until ``test_070``–``test_074``
actually report ``ok`` with ``skipped`` back at 6.

### DiscoveryService.create_project — TESTED (closing entry, evidence from the actual run log)

**Result:** `Ran 42 tests in 45.732s` → **OK**; process exit **0**; no `FAIL:` and no `ERROR:` lines.
Each of the five tests was confirmed from its own unittest output line — in two cases the verdict sits on the
continuation line, because unittest prints the first docstring line after the test name:

* `test_070_creates_a_project_in_an_available_organization` — **ok**
* `test_071_the_created_project_appears_in_list_projects` — **ok**
* `test_072_a_foreign_or_unknown_organization_is_not_found` — **ok** (verdict on the continuation line:
  "Creation reuses the AS-2 decision, so the non-disclosure rule holds here too. ... ok")
* `test_073_a_missing_or_invalid_principal_is_refused_without_a_write` — **ok**
* `test_074_a_persistence_error_surfaces_as_the_application_error` — **ok** (verdict on the continuation line)

**Environment:** the PostgreSQL used was the local throwaway test cluster **`.forge/pgdata-test`**
(PostgreSQL 17.11; `data_directory` verified as `.forge/pgdata-test`; port **5433**;
`listen_addresses=127.0.0.1`), reached through `FORGE_PLATFORM_TEST_DSN`.
Local checks: `python -m compileall -q app tests` = 0; `git diff --check` = 0.

**Status:** `DiscoveryService.create_project` moves from IMPLEMENTED to **TESTED**.

**Recorded limitation (a fixture requirement; not a proven product defect, and not hidden):** migration
`0002_roles.sql` changes a role's SUPERUSER attribute, and PostgreSQL permits that only for a superuser, so
provisioning the test fixture needs SUPERUSER privileges. Measured as
`asyncpg.exceptions.InsufficientPrivilegeError: permission denied to alter role`, raised from
`app/platform/persistence/migrations.py:198` through `tests/persistence_fixture.py:187`, and resolved by using
the throwaway test cluster's own superuser as the provisioning account, under the owner's explicit
authorisation for that disposable local cluster. No product code, test or migration was changed, and no
production credential was used. Whether the migration could avoid touching the SUPERUSER attribute stays an
open design question, recorded here rather than acted on.
