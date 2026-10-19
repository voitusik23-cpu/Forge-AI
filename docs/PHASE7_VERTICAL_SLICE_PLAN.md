# Phase 7 — vertical slice plan (session → organization → project → plan → confirmation → Core → artifacts → history)

Status: **PLANNED.** Nothing below is implemented. Future slices are not marked as done anywhere in this
document, and none of them may open an HTTP endpoint or connect UI before O-2 is approved and implemented —
see `docs/ADR_O2_CREDENTIAL_VERIFICATION.md`, which remains **PROPOSED — awaiting owner approval**.

## 1. Current state, by layer

**Platform (partly implemented — confirmed in code).** `AuthenticatedPrincipal`
(`app/platform/principal.py:125`) with four fields and a guarded `create()`; `SubjectIdentity`
(`app/platform/application/authorization.py:62`) re-checks the subject against storage; `DiscoveryService`
(`app/platform/application/organizations.py`) provides AS-1 `list_organizations`, AS-2 `list_projects` and
**AS-3 `create_project` — TESTED** (42 tests, OK, exit 0, on the throwaway cluster `.forge/pgdata-test`,
port 5433); tenant-scoped Unit of Work via `_authorized`; `ProjectRepository` protocol plus
`PostgresProjectRepository` with `INSERT INTO projects`; migration `0002_roles.sql` creates the parent roles
and touches a SUPERUSER attribute (hence the recorded fixture-provisioning limitation); `authorized_context()`
is exposed explicitly "for the next slice".

**Core (unchanged by this plan).** `app/api/routes.py:600 authorize_project(app, project_id) -> str` is the
single project boundary (compares against the instance's bound project, refuses on mismatch, never echoes the
supplied value); `app/api/service.py:475 self._active_project_id = "default"` — a known risk: one active
workspace per instance, so cross-project isolation is **not** yet demonstrated; Step-graph execution and
`RunStore` history exist; Phase 2 `RunIdempotencyGuard` is wired into the standard composition
(`app/api/main.py`) and stays untouched.

**Fabric / Model Registry (confirms, does not gate).** Provider and model descriptors are surfaced read-only
through `/api/models`, `/api/capabilities`, `/api/tools` and the dashboard; the only wired generative provider
is the deterministic offline mock, and no paid call has been authorised. Nothing here needs to change for the
slices below.

**Missing (not to be assumed present).** Production credential verification (O-2); an HTTP surface for the
Platform application services; a trusted link from a chosen project to the Core's workspace; task plan and
explicit confirmation as first-class objects on this path; usage accounting over the same path.

## 2. Slice sequence

1. **Verified session** — establish, present, expire, revoke. *Result:* the caller has a session the server
   trusts, and no client-supplied identity field is ever treated as fact.
2. **Organization selection** — the caller sees only its own organisations. *Result:* an organisation is
   chosen server-side from the caller's candidate set.
3. **Project selection** — the projects of the bound organisation. *Result:* a project is chosen, still only a
   lookup key.
4. **Project → Core trusted link** — the authorised project determines the workspace the run uses.
   *Result:* execution happens in the authorised workspace, and a refusal creates no run record.
5. **Task plan** — the plan is formed and shown before anything runs. *Result:* a plan exists as an object.
6. **Explicit confirmation** — Recommendation ≠ Permission ≠ Execution. *Result:* nothing executes without a
   recorded confirmation.
7. **Artifacts and history** — real files, real tests, statuses and errors, tied to the run.
   *Result:* the user sees what actually happened.
8. **Usage accounting** — tokens and cost per the existing nullable contract (null = unknown, proven zero = 0).

## 3. Candidate files and existing contracts

| Slice | Anchors that already exist | Candidate files (new or minimal edits) |
|---|---|---|
| 1 | `AuthenticatedPrincipal.create`, `SubjectIdentity`, `SecretStore` boundary, `app/api/main.py` (loopback-only, "no authentication") | new session store + a verification step in the HTTP composition |
| 2 | AS-1 `list_organizations`, `_resolve`, `select_candidate` | `app/platform/application/organizations.py` (read-only use) |
| 3 | AS-2 `list_projects`, `ProjectRepository.list_for_current_tenant` | same file (read-only use) |
| 4 | `authorize_project` (single boundary), `AuthorizedProject`, `LocalExecutionAdapter(workspace_root=…)` | Core composition; **no** new project store, **no** project CRUD in Core |
| 5–6 | step graph (`OperatingSurface.start_graph`), approval policy, `RunScope` | `app/api/operating_surface.py` |
| 7 | `RunStore`, `/api/runs/{run_id}`, artifact harvesting (`harvests_artifacts=True`) | existing machinery |
| 8 | `UsageRecord`, the six cost states in `app/dashboard/cost_truth.py` | existing machinery |

No second project store, no parallel authorization mechanism, and no change to `authorize_project`,
`RunIdempotencyGuard` or Phase 2.

## 4. Positive and negative tests per slice

**1.** Log in with valid credentials → session issued; no credential → refused **before** any read or write;
malformed / wrong scheme / expired / revoked → refused (revoked immediately); the token value never appears
in any log, error body or trace.
**2.** Own organisations listed; another tenant's organisation and a non-existent one answer the **same**
`not_found` code **and** message (non-disclosure, already asserted for AS-2 today).
**3.** Own projects listed; a `project_id` outside the set refused the same way; lists never intersect.
**4.** Allowed project → run executes in the authorised workspace; substituted `project_id` or workspace →
refused **before** a run record exists; two projects → two distinct workspaces; a refusal leaves the run
history byte-identical.
**5–6.** A plan can be produced and edited; execution without confirmation is refused; a confirmation is
recorded and binds to the plan.
**7.** Artifacts exist on disk and are linked to the run; a failing test is reported as a failure, never as
success; history survives a restart.
**8.** Unknown usage stays `null`; a proven zero stays `0`; no fabricated numbers.

## 5. Acceptance criteria and dependencies

* Each slice lands with its own tests green and the existing Platform suite (`42 tests`) still green.
* `python -m compileall -q app tests` = 0 and `git diff --check` = 0 for every slice.
* **Hard dependency:** slices 1–8 all require **O-2** approval and implementation. Until then **no HTTP
  endpoint is opened and no UI is wired** to the Platform surface.
* Slice 1 additionally requires the owner's decision on the credential form (see the ADR's open questions).

## 6. Dedicated verification stages

* **Workspace isolation:** prove two projects execute in two distinct, authorised workspaces, and that a
  client cannot influence the path (no request field, header, or idempotency fingerprint may select it);
  retire the `self._active_project_id = "default"` risk explicitly rather than assuming it away.
* **Cancellation:** verify a cancelled run stops its owned process tree, records a terminal cancelled state,
  leaves no orphaned child processes, and never reports success.
* **Idempotency:** re-verify replay semantics with a project in scope (same key → one execution; a materially
  different request under the same key → conflict), without changing `RunIdempotencyGuard`.
* **Usage accounting:** verify per-run tokens/cost provenance on this path, including the unknown-versus-zero
  distinction, and that a refused or cancelled run records no spend it cannot prove.

## 7. Ledger update order

`docs/PROJECT_COMPLETION_LEDGER.md` is updated **after** each slice's tests actually pass, with: the slice id,
the changed files, the exact command, the observed result (including `skipped`), the status
(IMPLEMENTED / TESTED / BLOCKED), and the remaining blocker. Nothing is recorded as done in advance, statuses
are never upgraded without a passing run, and `create_project`'s existing `TESTED` entry is not touched by
this plan.
