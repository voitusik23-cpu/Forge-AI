# FINAL HANDOFF TO CHATGPT — independent audit package

Prepared by the agent that ran the current stage. Everything below is either a
command that was run or a file that exists; nothing is reported from memory.

---

## A. Starting point

| Fact | Value |
|---|---|
| Published HEAD before this stage | `1621548` (what was on `origin/master`) |
| HEAD when this stage began | `af842a6496010d063e6967a245b24a9fcd23a90a` |
| Branch / upstream | `master` / `origin/master` |
| Remote | `https://github.com/voitusik23-cpu/Forge-AI.git` |
| HEAD after this stage | `44e51165c94dd76615d80f943b2b0259db26db9e` |
| Commits pushed in this stage | 30 (15 from this agent + 15 inherited, see below) |
| Working tree at stage start | 24 modified, 15 untracked (never committed by the previous agents) |
| Working tree after this stage | clean except the two protected untracked artifacts |

**Inherited work (NOT this agent's).** Before this stage the local branch already
contained 15 unpushed commits made by another agent (`Codex <codex@localhost>`,
2026-10-11/12): reparse-point handling, workspace identity binding, canonical path
validator unification, fail-closed API exposure, resolved-executable pinning,
Windows process-tree timeouts, discovery depth bound, declared runtime dependencies,
harness fixture alignment, and preservation of missing-artifact verification. They
were **not** rewritten, amended or reordered; they were published as they were. Their
own reports should be audited separately from this stage's work.

**Preservation.** The full pre-commit diff was saved to
`.forge/preserved-tracked.diff` (64 522 bytes, gitignored) before any commit.

---

## B. Defect matrix (this stage)

Format: problem → file/function → before → after → reproducing test → result → commit.
Every fix followed: BASELINE PASS → INJECTION → TARGET TEST RED → BYTE-EXACT RESTORE
(SHA-256 compared) → BASELINE PASS.

| # | Problem | Location | Before → After | Test that reproduced it | Commit |
|---|---|---|---|---|---|
| 1 | `value.strip()` silently aliased `"a.txt "` to `"a.txt"` and hid trailing-dot/space forms from every name-based screen | `app/execution/paths.py::normalize_workspace_relative_path` | accepted (REFUSE→ACCEPT regression vs. the old tool validator) → refused, leading space kept literal | `tests/test_workspace_path_normalization.py` 7 FAIL before | `e78e13b` |
| 2 | Unresolvable bare executable fell back to the bare name, so one approved fingerprint executed two different images depending on spawn-time PATH | `app/execution/intent.py::_resolve_executable`, `app/execution/adapter.py::_run_process` | fail-open (launch by name) → empty pin, adapter refuses before a process exists; declared empty `PATH` honoured | `tests/test_execution_intent_pinning.py` 3 FAIL before | `2b817ba` |
| 3 | Tree termination could not reach a surviving descendant; `INVALID_HANDLE_VALUE` check was dead (ctypes truncated the handle, no `restype`), so the `taskkill` fallback was unreachable; pipe close outside the timeout branch left wall-clock unbounded | `app/execution/adapter.py` | 8 tests, 1 ERROR, 98 s → 8 tests OK, 6.6 s | `tests/test_execution_timeout_and_tree_kill.py` (existing) | `7fb4f17` |
| 4 | Harvest opened and hashed credential-shaped files named by a client-supplied verification target (measured: `.env` read, digest returned) | `app/execution/workspace_manager.py::harvest_artifacts` | read+hash → refused by name, same recognizer staging and the scanner use | `tests/test_harvest_refuses_credentials.py` 4 FAIL before | `7b66736` |
| 5 | A directory replaced at the same path was served by the tool boundary: `resolve_target` returned the replacement's file and its content was readable | `app/tools/workspace.py::assert_available`, `app/runtime/run_scope.py::validate_workspace` | path-only comparison → identity captured at construction and re-proved per operation, on both layers | `tests/test_workspace_identity_on_tool_path.py` 4 FAIL before | `8d2e945` |
| 6 | `_SAFE_RUN_ID` allowed `".."`; `run_dir` joined it onto the root; `GET /api/runs/%2E%2E` returned `state.json` from **above** the store root | `app/runtime/run_store.py`, `app/api/server.py::get_run` | traversal served → refused by pattern **and** canonical containment; route answers 404 | `tests/test_run_store_id_containment.py` 3 FAIL + 1 ERROR before | `c9de0d6` |
| 7 | A non-numeric field in a stored record raised a plain `ValueError` out of the reader documented as never raising, aborting the whole recovery scan | `app/workflows/step_graph.py::read_step_graph_record`, `graph_from_declared_plan` | `ValueError` escaped → reported as `untrustworthy_step_graph_record:<type>`; rebuilder raises the documented `StepGraphStateError` | `tests/test_step_graph_record_robustness.py` 3 ERROR before | `3b7d99e` |
| 8 | On replay the recorded step payload was rewritten, so one step read `SUCCEEDED, exit 0, step_run_id set` **and** `command_started: false`; commit `af842a6` had painted a red assertion green without touching production | `app/api/operating_surface.py` (replay branch) | record rewritten → returned as recorded, with `recorded_result: true` and per-response fields describing this call only | `tests/test_block10_http_errors_and_execution_semantics.py::test_24` (`False is not True` when injected) | `06e3daf` |
| 9 | `bool(payload.get("invalidate", False))` made the strings `"false"`, `"no"`, `"0"` perform a durable invalidation | `app/api/routes.py::stale_facts` | truthy coercion → only a JSON boolean accepted, otherwise structured 400 | `tests/test_block9_evidence_and_verification.py::test_21b` (`200 != 400` when injected) | `1c4a267` |
| 10 | `ScanLimits.max_depth` had no upper bound (10**9 accepted) and `MAX_DEPTH_EXCEEDED` fired when only ignored children were pruned | `app/understanding/models.py`, `app/understanding/scanner.py` | unbounded / false warning → capped at 256; warning only when a descent is really pruned | `tests/test_understanding_security.py` (2 tests) | `44e5116` |
| 11 | Dead perimeter call `validate_tool_set(getattr(request, "allowed_tool_ids", None))` — the field does not exist, so the call was always a no-op; duplicate `run_scope` field in the same dataclass | `app/agent_runtime/harness.py`, `models.py` | false boundary / duplicate → removed; measured `allowed_tool_ids: False`, `run_scope count: 1` | measurement + 69 neighbouring tests green (no behavioural change is provable for dead code, stated plainly) | `f27d0b0` |
| 12 | The real tool perimeter ("declared tool intents exceed the frozen run tool set") had **no test anywhere** | `app/agent_runtime/harness.py::_enforce_run_scope` | 0 tests → 4 tests; injection removes the `raise` and 2 go red | `tests/test_harness_tool_perimeter.py` | `0095376` |
| 13 | Launcher docstring promised a `RuntimeWarning` while the code refuses; error-envelope docstring promised the exception's class name while the code publishes the message | `app/api/main.py`, `app/api/routes.py` | documentation only | not applicable (no behaviour change) — stated explicitly rather than faked | `4188830`, `1c4a267` |

### The three critical risks named in this task

| Risk | Status | Evidence |
|---|---|---|
| **1. Artifact provenance** (pre-existing file → `PASSED`) | **CONFIRMED, NOT FIXED** | `app/workflows/step_runner.py` matches a harvested artifact by `relative_path` alone. An independent audit measured `P3 status: COMPLETED / verification: PASSED artifact_produced=made.txt sha256=b7d7…` for a step whose command was `MAKE_NOTHING` and whose file existed beforehand. No snapshot of the target's pre-execution state exists anywhere in the pipeline. |
| **2. `resume` fabricating `COMPLETED`** | **CONFIRMED, NOT FIXED** | Independent audit probe P3: `resume` over a record written through the public codec (`status=SUCCEEDED`, no `execution_outcome`, no `exit_code`, no `verification_status`) produced `StepGraphStatus.COMPLETED`, `commands_started: 0`, `verified: False, NOT_RUN`, while the ledger said `IdempotencyVerdict.START`. Precondition: the record's evidence fields did not come from this runner; no runner-reachable path was found. |
| **3. Damaged record ≠ absent record** | **HALF FIXED** | The *reader* half is fixed (`3b7d99e`): a damaged record is reported instead of raising, and the recovery scan can no longer be aborted by one bad row. The *writer* half is NOT fixed: `step_runner.py` still discards the reason (`record, _reason = ...`) and treats an unreadable record as "nothing was attempted", then persists a fresh snapshot over it. Independent audit probe P8 measured a real `COMPLETED` record being replaced by `INDETERMINATE/UNKNOWN` (no re-execution happened — the ledger answered `REPLAY`). |
| **GET routes mutating durable state** | **CONFIRMED, NOT FIXED** | Audit probes 6 and 10: `GET /api/product/verification-plan` created a baseline record (`baseline_record_before: null` → `baseline_record_after: {...}`) and changed the onboarding stage (`VERIFIED` → `IN_PROGRESS`, latest run id replaced by the surface's own `project-baseline` record), while `operating_surface.py` states "a read must not write". |

---

## C. Test ledger

Commands (the runner sets a deterministic temp directory and an isolated
`FORGE_RUN_STORE_ROOT`, because this host's sandbox denies writes into `0o700`
directories and its `tempfile.gettempdir()` falls back to the repository root):

```powershell
# targeted
.\.venv\Scripts\python.exe -B -X utf8 .forge\run_confined.py discover -s tests -p "<file>.py" -v
# full suite
.\.venv\Scripts\python.exe -B -X utf8 .forge\run_confined.py discover -s tests -v
# static checks
.\.venv\Scripts\python.exe -B -X utf8 -m compileall -q app tests
$o = git diff --check 2>&1; $code = $LASTEXITCODE     # capture explicitly: CRLF warnings go to stderr
```

| Run | Result |
|---|---|
| Full suite, frozen tree after the last change | `Ran 2344 tests in 499.599s` / `OK (skipped=36)` / exit 0 |
| Widget suite | `Ran 11 tests` / `OK` (includes real tkinter rendering, drag, honest degradation, no spontaneous window) |
| Harness perimeter suite | `Ran 4 tests` / `OK` (2 red under injection) |
| `compileall -q app tests` | exit 0 |
| `git diff --check` | exit 0 |

**Skips — 36, each with its reason (SKIP is not PASS):**

| Count | Reason |
|---|---|
| 32 | `asyncpg is not installed` — Platform persistence/database contract tests require a real PostgreSQL driver and a live server; RLS, FORCE RLS, partial unique indexes and composite FKs are PostgreSQL semantics and are deliberately not simulated |
| 3 | filesystem symlink creation is not permitted in this environment |
| 1 | `OPENAI_API_KEY` not set (a deliberate offline guarantee, not a gap) |

**Not verifiable here at all.** The Platform track needs a PostgreSQL server: this
host has no service, no `psql`, no `C:\Program Files\PostgreSQL`, ports 5432/5433
closed, no container runtime, an empty WSL, and `pip install asyncpg` hung for ~30
minutes before being killed. No stub was introduced to hide this.

---

## D. Residual risks

**Confirmed, blocking, not fixed in this stage**
1. Artifact provenance: existence is accepted as production (`step_runner.py`, ARTIFACT_PRODUCED branch).
2. `resume` can derive `COMPLETED` from a record whose evidence fields are missing or inconsistent.
3. An unreadable/incompatible record is treated as absent and overwritten, destroying prior evidence.
4. GET routes (`verification-plan`, `change-impact`) write durable state and move the onboarding stage.

**Confirmed, deliberately not changed**
5. A step verification may name an artifact target the declaration never declared, which *adds* it to the request's targets (documented model: "the artifact targets this verification forces the step to declare"). Strict refusal was implemented, measured (5 tests in 3 suites depend on the documented model) and reverted byte-exactly; it needs an owner decision. The credential-material half of this risk is closed by `7b66736`.

**Unconfirmed hypotheses**
6. Cross-process races in `RunScope._FROZEN` / `AgentHarness._bindings` (no concurrency test was written).
7. `Adapters`: TOCTOU between the authority check and spawn (acknowledged in `DECISIONS.md` as an OS-level residual).

**Environment limitations**
8. No OS sandbox: path checks are a server-side contract boundary, not process isolation.
9. No authentication on the API surface; loopback binding plus refusal of non-loopback binds is the only control.
10. No live PostgreSQL / `asyncpg` (see above).
11. Real LLM generation was never exercised (mock/fake providers only).
12. The DSH file sandbox on this host is broken (any command hangs); all commands in this stage were run with a one-off wider permission grant. This affects the harness, not the results.

**Deliberate architecture (not defects)**
13. `EXACTLY_ONCE` is intentionally absent; the guarantee is at-most-once per attempt while the journal is readable.
14. `RunState` is not extended (68 identity comparisons across 20 files).
15. `AuthorizedExecution` is minted only through the module-private sentinel; `app/execution/authorizer.py` was not modified in this stage (SHA-256 `189c97cfeef672b7c764fd98a3bd0f62387a136f77d04e5bbabe8cc2a716db7d`, 24 642 bytes).

**Optional improvements, not required to close this stage**
16. Secret recognizer breadth (`secrets.json.dist`, `id_rsa.example`) — a leak was never demonstrated, so this is not declared a defect.
17. `MAX_DEPTH_EXCEEDED` reporting could name the pruned subtree in more detail.
18. `docs/audits/FINAL_HANDOFF_TO_CHATGPT.md` (this file) is the first entry in a new `docs/audits/` directory.

---

## E. Evidence index

**Commits (oldest → newest), all pushed to `origin/master`**

```
e78e13b fix(paths): refuse whitespace-aliased workspace paths
2b817ba fix(execution): pin a bare executable or refuse to launch it
7fb4f17 fix(execution): terminate the process tree through a job object
7b66736 fix(execution): never harvest credential material
8d2e945 fix(workspace): bind the boundary to the directory, not the path
c9de0d6 fix(run-store): refuse run ids that escape the store root
3b7d99e fix(workflows): report a damaged record instead of raising from the reader
06e3daf fix(api): return the recorded attempt unchanged on replay
1c4a267 fix(api): require a JSON boolean for invalidate
f27d0b0 chore(agent-runtime): drop a dead perimeter call and a duplicated field
0095376 test(agent-runtime): cover the frozen tool perimeter
4188830 docs(api): align the launcher docstring with the refusal
9d92be1 docs: synchronise secret, job-object, pinning, depth and replay statements
964bc0e feat(desktop): add the Forge Pulse widget with launcher and tests
44e5116 fix(understanding): bound max_depth and stop false depth warnings
```

**Open these first**
1. `app/execution/adapter.py` — job-object tree kill, pin gate, handle check (largest production change, +168/−31).
2. `app/tools/workspace.py` and `app/runtime/run_scope.py` — directory identity on both layers.
3. `app/runtime/run_store.py` + `app/api/server.py` — run-id containment.
4. `app/workflows/step_graph.py` — damaged-record reporting, and (unfixed) `derive_graph_status`.
5. `app/workflows/step_runner.py` — the three unfixed risks live here (provenance, reason discarded, overwrite).
6. `tests/test_step_graph_record_robustness.py`, `tests/test_run_store_id_containment.py`, `tests/test_harvest_refuses_credentials.py`, `tests/test_harness_tool_perimeter.py` — smallest reproductions.

**Before/after fragments**

```
# 4: credential harvest, injected run
FAIL: test_01_a_dotenv_target_is_refused   AssertionError: ValueError not raised
# 4: after
Ran 5 tests in 0.091s   OK

# 6: store escape, before the fix
AssertionError: PersistedRun(run_id='..', snapshot=RunStateSnapshot(run_id='outside', ...))
# 6: after
Ran 6 tests in 1.170s   OK

# 8: replay contract, injected (production reverted)
AssertionError: False is not True
# 8: after
Ran 24 tests in 3.888s   OK

# 12: perimeter, injection removes the raise
FAIL: test_01_an_intent_outside_the_frozen_perimeter_is_refused
FAIL: test_03_a_second_undeclared_intent_is_refused_even_beside_an_allowed_one
Ran 4 tests   FAILED (failures=2)
# 12: after restore (SHA-256 identical)
Ran 4 tests   OK
```

**Desktop deliverable:** `app/desktop/widget.py`, `app/desktop/__main__.py`,
`scripts/run_forge_pulse.cmd`, `tests/test_desktop_widget.py`,
`docs/images/forge-pulse-widget.png` (real screen capture of the tkinter render).

---

## F. Final status

```
BLOCKED
```

Reason, stated precisely: the stage's *preservation, verification, commit and push*
work is complete (30 commits published, remote HEAD `44e5116`, full suite green), but
four **confirmed** defects that this task asked to close remain unfixed in
`app/workflows/step_runner.py` and `app/api/operating_surface.py`: artifact
provenance, `resume` deriving `COMPLETED`, damaged-record overwrite, and GET-route
mutations. They are not blocked technically — they are blocked by the remaining
budget of the session that produced this file. No test in the suite covers them, and
this document does not claim otherwise.

An independent audit has **not** been performed; this file is the input for it.

---

## Update - finalization stage 1 (no fixes)

The next session investigated Blocker #1 (artifact provenance) and stopped at a
safe point without changing any code. See
[`FINALIZATION_CHECKPOINT.md`](FINALIZATION_CHECKPOINT.md) for the verified
starting state, the exact call sites, the agreed minimal design and the precise
next operation. All four blockers recorded above remain unfixed, so the status
`BLOCKED` still stands.

---

## Update - finalization stage 2 (Blocker #1 fixed)

Blocker #1 (artifact provenance) is **FIXED_AND_TESTED** in commit `6605e35`:
`app/execution/workspace_manager.py` now snapshots each declared target's existence,
size, `mtime_ns` and SHA-256 immediately before the child is created and compares it
with what the harvest measures afterwards; `app/workflows/step_runner.py` accepts
`ARTIFACT_PRODUCED` as `PASSED` only on a proven change by this attempt, and reports
a pre-existing unchanged file or a missing provenance as `ERROR` instead.

Reproduced before the fix (`AssertionError: PASSED == PASSED` for a file created
before a step that writes nothing), fixed, and proven by injection with a byte-exact
restore. Full suite after the fix: `Ran 2354 tests in 177.232s` / `OK (skipped=36)`.

Blockers #2 (`resume` false `COMPLETED`), #3 second half (damaged record overwritten)
and #4 (GET mutating durable state) remain unfixed, so the overall status is still
`BLOCKED`. See [`FINALIZATION_CHECKPOINT.md`](FINALIZATION_CHECKPOINT.md) for the
current per-blocker status and the exact next step.

---

## Update - documentation consolidation stage

`README.md` gained an honest "Current state" section (English canonical, Russian
equivalent) with the verified commands, environment limits and a documentation map;
`docs/ROADMAP.md` had 23 encoding-corrupted sequences (`вЂ”` where an em dash belongs)
repaired. No production code, tests, migrations or configuration were touched, and no
technical blocker status changed: Blocker #1 stays FIXED_AND_TESTED, Blockers #2, #3
(second half) and #4 stay open.

---

## Update - finalization stage 3 (Blocker #2 fixed)

`resume` no longer summarises an unsupported `SUCCEEDED` as `COMPLETED`. The cause was
that `derive_graph_status()` sees only step statuses, while `_resume_snapshot()` only
downgraded a durable `RUNNING` record; a record saved as `SUCCEEDED` with no execution
outcome, no exit code and a declared `EXIT_CODE` verification left `NOT_RUN` passed
through untouched. A restored `SUCCEEDED` now requires real execution evidence, must not
contradict its own recorded outcome, and must not leave a declared check un-run;
otherwise it is downgraded to `UNKNOWN` (graph `INDETERMINATE`) with the original claim
preserved as evidence.

Reproduced first (`FAILED (failures=3)`, `AssertionError: COMPLETED == COMPLETED`), fixed
in commit `a28c38a`, proven by injection with a byte-exact restore, and verified with the
full suite: `Ran 2359 tests in 155.120s` / `OK (skipped=36)`.

Blockers #3 (second half) and #4 remain open, so the overall status stays `BLOCKED`.

---

## Update - documentation consolidation stage 2 (no code changes)

Documents changed, and why:

| Document | Change |
|---|---|
| `docs/ROADMAP.md` | a bilingual seven-stage plan (Stabilization, Independent Verification, Documentation & Developer Experience, Platform Foundation, Provider/Model Layer, Execution-Agents-Tools, UI/Product Experience) with goal, tasks, dependencies, exit criteria, tests, actual status and open risks for each; every pre-existing section below it is preserved |
| `docs/SOURCE_OF_TRUTH.md` | a bilingual section defining the authority of the five sources (code and tests first, then this file, architecture, specification/vision, roadmap, decisions, audits), restating the invariants Recommendation != Permission, Permission != Execution, Report != Evidence, and recording an **open contradiction** |
| `docs/ARCHITECTURE.md` | a bilingual section describing artifact provenance (baseline before the child is created, comparison after, attempt binding, fail-closed verdicts) and the restored-success evidence rule, matching `6605e35` and `a28c38a` |
| `docs/USER_AND_DEVELOPER_GUIDE.md` | **new**: bilingual user and developer guide -- repository layout, the authority chain, environment requirements, verified install/run/test commands, configuration and secrets, execution/verification/recovery, a diagnostics table, known limitations, and an explicit list of screenshots that would be needed and are deliberately not mocked |

Checks performed: 12 relative links in the new guide -- 0 missing; every referenced
path exists (`app/api/main.py`, `scripts/run_forge_pulse.cmd`, `requirements.txt`,
`.env.example`, `.forge/run_confined.py`, the test file, the widget image, the
checkpoint); encoding clean in all four documents (0 mojibake); secret scan 0 hits;
`git diff --check` exit 0. The full test suite was **not** re-run, because no code,
test, migration or configuration file was touched. Last recorded suite result stands
at `Ran 2359 tests in 155.120s` / `OK (skipped=36)`.

### Open contradiction for the owner

The brief commissioning this pass states that blocker #2 is open. The repository says
otherwise: `a28c38a` fixes it, five regression tests cover it
(`tests/test_step_graph_managed_execution.py`), injection reproduces the old failure
when the hunk is reverted, and the full suite is green. This stage documents the
repository's version, per the authority order recorded in `docs/SOURCE_OF_TRUTH.md`.
The blocker list is therefore: #1 fixed, #2 fixed, #3 partial, #4 open.

### Residual housekeeping, not a repository change

The local (gitignored) test runner pinned its deterministic temp directory inside the
repository, and because some tests stage the repository into a scratch tree, repeated
runs produced a deeply nested `.forge/tmp/forge_scratch_*/...` skeleton. All files in
it were removed (0 files, 0 bytes remain; only empty directories left behind by
long-path limits). Nothing there was ever tracked by git. A follow-up should move that
temp root outside the repository so the nesting cannot recur.

---

## Update - finalization stage 4 (Blocker #3 closed)

Blocker #3 is now `FIXED_AND_TESTED` in both halves. `StepGraphRunner._attempt()` read
the record with `recorded()`, which deliberately collapses "nothing was recorded" and
"a record exists that this build will not trust" into `None`; the reason was discarded,
so `run()` treated a damaged record as absent and wrote a fresh graph over it. The
reader contributed the same conflation for a payload that was present but not a mapping
and for an explicit `null`.

Now `_attempt()` reads with `recorded_or_reason()` and refuses -- `REFUSED`,
`executed=False`, `stopped_reason` naming the reason -- before building any snapshot,
running any command or writing any byte; `read_step_graph_record` distinguishes a
missing key from an invalid or empty payload. Reproduced first (`AssertionError: True is
not false`, `'status': 'COMPLETED'` over a record that could not be read), fixed in
`90b2af7` and `0c61092`, and proven by two isolated injection passes with byte-exact
restores. The tests compare raw bytes and SHA-256 before and after `run()`.

Full suite: `Ran 2365 tests in 133.321s` / `OK (skipped=36)`.

Blocker #4 (GET routes mutating durable state) remains open, so the overall status is
still `BLOCKED`. Next stage: blocker #4, in its own change.

---

## Update - finalization stage 5 (Blocker #4 closed; Stage 1 blocker list empty)

`GET /api/product/verification-plan` no longer writes durable state. Two implicit
writes lived in the read path: `_resolve_baseline()` seeded and persisted a baseline
record when none existed, and the same analysis merged stale-fact history through
`_remember_stale_facts`. Answering the question therefore created or rewrote
`project-baseline/state.json` (measured: `project-baseline/state.json was rewritten by
a read`); `GET /api/product/change-impact` had the same defect.

Recording is now explicit: `change_analysis(read_only=True)` (used by both GET routes)
resolves a baseline in memory without persisting it and reports already-recorded
history without extending it, while `POST /api/product/stale-facts` and
`POST /api/product/baseline` keep their documented writes. Proven by a test that seeds
through the write path, snapshots the store's own files by name and exact bytes, calls
the route twice, and asserts nothing changed; reverting the fix reproduces the
rewrite, and the restore was byte-exact.

Full suite: `Ran 2366 tests in 135.295s` / `OK (skipped=36)`.

All four Stage 1 blockers are now `FIXED_AND_TESTED` (#1 `6605e35`, #2 `a28c38a`,
#3 `3b7d99e`/`90b2af7`/`0c61092`, #4 `fc5b098`). Stage 1 is **not** declared complete:
independent verification has not happened, and the verification-contract question
("verify a pre-existing state" cannot be expressed by any `VerificationKind`) still
needs the owner's decision.

---

## Update - audit finding F-1 closed

`run()` no longer echoes a stored `COMPLETED` that its own records do not justify.
`_result_from_snapshot` used to return `status=snapshot.status` verbatim while the
evidence screen introduced by `a28c38a` lived only in `_resume_snapshot`, so the same
record was `COMPLETED` through `run` and `INDETERMINATE` through `resume` (audit probe:
`run() -> COMPLETED | executed: False`, `resume() -> INDETERMINATE`).

The screen now lives in one place, `StepGraphRunner._screen_saved_record`, and
`_screen_snapshot` applies it to every saved record and re-derives the graph status from
them. Both entry points use it; the screened snapshot stays in memory, and the stored
bytes are asserted unchanged. Reproduced first (4 failures, including
`COMPLETED != INDETERMINATE`), fixed in `45cdcd1`, proven by injection with a
byte-exact restore.

Full suite: `Ran 2373 tests in 146.682s` / `OK (skipped=36)`.

Audit finding **F-2** (baseline/harvest size limits disagree: a >1 MiB pre-existing
artifact modified by the attempt yields `artifact_provenance_unavailable` instead of
production) is **still open** and is the next task.

---

## Update - audit finding F-2 closed

The provenance baseline and the harvest now measure with one limit. The baseline was
taken with `max_output_bytes` (1 MiB, the **stream** cap) while the harvest used
`max_artifact_bytes` (10 MiB), so a pre-existing artifact larger than 1 MiB that the
attempt really modified was reported as
`ERROR / artifact_provenance_unavailable` -- a real production reported as unmeasurable.

`EphemeralWorkspaceManager.max_artifact_bytes` is now a public property,
`_run_process(..., artifact_baseline_limit=...)` receives it from `execute()` (the
composed manager's limit when isolating, `DEFAULT_MAX_ARTIFACT_BYTES` otherwise, which
is exactly what the harvest's default manager uses), and the snapshot passes it
through. No limit was raised; a file over the artifact cap is still refused explicitly
by the harvest and never becomes `PASSED`.

Reproduced first (through the real adapter path: `ERROR / artifact_provenance_unavailable`),
fixed in `52b376d`, proven by injection with a byte-exact restore. Full suite:
`Ran 2377 tests in 140.899s` / `OK (skipped=36)`.

Both audit findings F-1 and F-2 are now closed. The remaining Stage 1 items are the
owner decisions and the environment-limited Platform track (32 skips).

---

## Update - master execution stage (UI launched, docs corrected)

* The repository's existing **web console was launched and verified by HTTP**: `GET /`
  → 200 with the real console HTML, `/app.css` and `/app.js` → 200, and
  `/api/product/onboarding` → 200 carrying the real project record. Start command:
  `python -m app.api.main` (loopback), then open `http://127.0.0.1:<port>/`.
* A **documentation defect was found and fixed**: the documented launch command
  `python app\api\main.py` fails with `ModuleNotFoundError: No module named 'app'`;
  it is now `python -m app.api.main` in both the README and the guide, verified by
  starting the server. The Russian half of the guide was synchronised.
* **No browser screenshot** of the console could be produced: the only browser here
  (Edge) cannot run headless inside the DSH sandbox. Nothing was faked.
* **PostgreSQL remains unavailable** on this host (no server, no container runtime, no
  driver), so the 32 Platform skips stay unverified; provisioning steps are documented.

Stage 1 verdict: **`CONDITIONAL GO`** -- all in-environment criteria confirmed, the
Platform persistence contract still unverified, TOCTOU/concurrency residuals documented
rather than closed.
