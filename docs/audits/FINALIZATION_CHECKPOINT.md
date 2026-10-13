# FINALIZATION CHECKPOINT — before the final stage

Written at the end of a session whose context budget ran out mid-investigation of
Blocker #1. **No production or test code was changed in this session.** The working
tree is clean and the remote HEAD is unchanged, so nothing has to be undone.

---

## 1. Starting state (verified, not assumed)

| Fact | Value |
|---|---|
| HEAD | `4397eea29647b98e30143ee7aa5b74cce195dd85` |
| Branch / upstream | `master` / `origin/master` |
| Divergence | `0 0` (nothing to push, nothing to pull) |
| Working tree | clean except the two protected untracked artifacts (`Forge-AI-DeepSeek-Review-2026-10-04.zip`, `tests/test_project_factory_vertical_slice.py~RF1a139fd6.TMP`) and `tests/test_step_graph_managed_execution.py`, which shows as modified with **no content diff** (line-ending flag only) |
| Baseline test result | `Ran 2344 tests in 499.599s` / `OK (skipped=36)` |

---

## 2. Blocker #1 — artifact provenance: investigation result

### What exists today (verified by reading the current code, line numbers current)

| Point | Location | Behaviour |
|---|---|---|
| Isolation branch | `app/execution/adapter.py:352` `scratch_root = ws_mgr.scratch_root`, `:355` boundary from that root, `:362` `_run_process(...)` | the command runs inside the staged scratch tree |
| Non-isolation branch | `app/execution/adapter.py:382` `_run_process(...)` | the command runs in the token's workspace root |
| Process spawn | `app/execution/adapter.py:655` `proc = subprocess.Popen(...)` | `start_time = time.monotonic()` is taken at `:650` |
| Harvest | `app/execution/adapter.py:399-470` (`_harvest_artifacts`) → three `manager.harvest_artifacts(intent.artifact_targets, run_id=…, provenance_source=…, root=…)` calls | opens and hashes each declared target **after** the run; passes no link to the attempt |
| Verdict | `app/workflows/step_runner.py:1014-1023` | `ARTIFACT_PRODUCED` returns `PASSED` on `artifact.relative_path == target` alone |

**Confirmed defect:** nothing anywhere records whether the target existed before the
attempt, so a file that predates the run yields `PASSED`. An earlier independent
audit reproduced it (`MAKE_NOTHING` step, file created beforehand →
`status: COMPLETED`, `verification: PASSED artifact_produced=made.txt sha256=b7d7…`).

### Minimal design agreed for the next session (no schema or API change)

1. **Baseline at the last safe moment.** Take the state of every declared target
   *inside* `_run_process`, immediately before `subprocess.Popen` (not in `execute()`,
   which would open a wider race window), using **the same root the harvest will
   use** — `scratch_root` in the isolating branch, the token workspace root
   otherwise. Open question that must be answered first: confirm that the
   `boundary` object passed into `_run_process` exposes that root (or pass it as an
   extra keyword); the code shows the root is already computed at `:352`.
   State per target: `(exists, size_bytes, mtime_ns, sha256)`.
2. **Carry it on the existing result.** `_run_process` already receives a `metadata`
   dict that every return path reuses; set `metadata["artifact_baseline"] = baseline`
   before spawning, so no signature change and no new storage is needed.
3. **Attach the verdict to the artifact.** `_harvest_artifacts` reads that baseline and
   passes `baseline=` to `EphemeralWorkspaceManager.harvest_artifacts`, which compares
   the after-state and puts `existed_before`, `changed_by_this_attempt` and
   `sha256_before` into `Artifact.metadata` (the field and the
   `create_artifact_from_file(..., metadata=…)` parameter already exist).
4. **Fail closed in the verifier.** `ARTIFACT_PRODUCED` returns `PASSED` only when
   `changed_by_this_attempt is True`; `False` → `ERROR` with
   `verification_error:artifact_preexisting_and_unchanged`; a missing provenance
   record → `ERROR` with `verification_error:artifact_provenance_unavailable`. The
   graph then reads `INDETERMINATE`, never `COMPLETED`.
5. **Contract question for the owner** (do not decide silently): verifying a
   *pre-existing correct state* cannot be expressed by any current
   `VerificationKind`. With this fix such a check becomes `ERROR`, which is honest
   but not what an operator may intend. Options: (a) accept `ERROR` and document it;
   (b) add an explicit kind for state verification (a contract change, out of scope
   here).

### Tests still to write (all six required by the task)

pre-existing and unchanged → not `PASSED`; created by this attempt → `PASSED`;
pre-existing and modified → `PASSED`; replaced at the same path → `PASSED`;
provenance unavailable → not `PASSED`; valid pre-existing state under the current
contract → must not be silently `PASSED` (documents the contract gap). Plus: a
result from one attempt must not be attributable to another parallel/later attempt.

### Why it was not implemented in this session

The session's context budget was exhausted after the investigation above. The change
touches three production modules plus their tests and an injection proof; starting it
and stopping half-way is exactly what this stage forbids. **Nothing was changed.**

---

## 3. Blockers #2–#4 — starting points for the next session

| Blocker | Exact next operation |
|---|---|
| **#2 `resume` false `COMPLETED`** | Read `derive_graph_status` (`app/workflows/step_graph.py`, branch `all(status is SUCCEEDED)` → `COMPLETED`) and `_resume_snapshot` (`app/workflows/step_runner.py`). Reproduce with a record written through the public codec (`SUCCEEDED`, no `execution_outcome`, no `exit_code`, declared `EXIT_CODE` verification) — the earlier audit measured `COMPLETED` with `commands_started: 0`, `verified: False NOT_RUN` while the ledger said `START`. Then require, per contract, that a restored `SUCCEEDED` carries its mandatory evidence (outcome present; a declared verification not `NOT_RUN`; no contradiction with `execution_exit_code`) and otherwise derive `INDETERMINATE` without rewriting the record. |
| **#3 damaged record overwritten** | Reader half is fixed (`3b7d99e`). Remaining: in `step_runner.run()` the read reason is discarded (`record, _reason = …`) and a damaged record is treated as absent, then a fresh snapshot is persisted over it (audit probe P8: a real `COMPLETED` record became `INDETERMINATE/UNKNOWN`). Fix: keep the reason, refuse to start a new attempt over an untrustworthy record, never overwrite, return a diagnostic indeterminate state, and keep the recovery scan going for other records. |
| **#4 GET mutating durable state** | `GET /api/product/verification-plan` → `change_analysis` → `_remember_stale_facts` (`app/api/operating_surface.py`, near the old line 1819). Two audit probes measured a baseline record being created and the onboarding stage moving `VERIFIED → IN_PROGRESS` on a plain GET. Fix: remove the write from the read path, keep the mutation on the existing `POST /api/product/stale-facts` (`invalidate=true`), and test with a store snapshot before/after two consecutive GETs. |

---

## 4. Variant A

Not to be implemented without the owner's decision. Open question to answer first:
does the provenance fix of Blocker #1 remove the false-`PASSED` part of this risk,
leaving only "a client may name an arbitrary undeclared file, which is then read and
hashed"? The credential-material half is already closed (`7b66736`). Strict refusal of
any undeclared target was implemented, measured (5 tests in 3 suites depend on the
documented model "the artifact targets this verification forces the step to declare")
and reverted byte-exactly.

---

## 5. Status

```
BLOCKED
```

All four blockers remain unfixed; this session produced investigation and this
checkpoint only. The suite is green at the state above, the remote is in sync, and no
work is at risk. Continue from `4397eea` with Blocker #1, step 1 of §2 above.

---

## Blocker #1 — FIXED_AND_TESTED (commit `6605e35`)

The `BLOCKED` line in §5 above describes the state **before** this commit. Current
per-blocker status:

| Blocker | Status |
|---|---|
| #1 artifact provenance | **FIXED_AND_TESTED** — commit `6605e35` |
| #2 `resume` false `COMPLETED` | `BLOCKED` — not started |
| #3 damaged record overwritten | `PARTIAL` — reader half fixed in `3b7d99e`; the writer half (overwrite on `run()`) is not started |
| #4 GET mutating durable state | `BLOCKED` — not started |

### What changed (3 production files, +155/−2)

| File | Change |
|---|---|
| `app/execution/workspace_manager.py` | new `_file_state()` (existence, size, `mtime_ns`, SHA-256) and `snapshot_artifact_states()` (reads only inside the given root; refuses absolute, traversing and credential-shaped targets); `harvest_artifacts(..., baseline=…)` compares before/after and writes `existed_before`, `changed_by_this_attempt`, `sha256_before`, `size_before`, `baseline_recorded_for` into `Artifact.metadata` |
| `app/execution/adapter.py` | baseline taken **inside `_run_process` immediately before `subprocess.Popen`**, from `boundary.root` — the same root the harvest measures in both branches; carried through the existing `metadata` dict (no new storage, no API change) and passed to all harvest calls |
| `app/workflows/step_runner.py` | `ARTIFACT_PRODUCED` returns `PASSED` only when `changed_by_this_attempt is True`; `False` → `ERROR` / `verification_error:artifact_preexisting_and_unchanged`; missing provenance → `ERROR` / `verification_error:artifact_provenance_unavailable`. A matching path alone is no longer success, so the graph cannot reach `COMPLETED` on this path |

### Reproduction

RED, before the fix — `tests/test_step_graph_managed_execution.py::VerificationTests::test_a_preexisting_artifact_is_never_reported_as_produced`
(created before the run, step writes nothing, `isolate_workspace=True`, artifacts harvested):

```
AssertionError: <VerificationStatus.PASSED: 'PASSED'> == <VerificationStatus.PASSED: 'PASSED'>
FAILED (failures=1)
```

GREEN, after the fix: that test passes and the suite is green.

### Tests

* `tests/test_artifact_provenance.py` — 9 tests: pre-existing and unchanged → not
  production; created by this attempt → production; pre-existing and modified →
  production; replacement decided by content even with an identical timestamp; no
  baseline → the question is unanswered (`None`), not "yes"; a pre-existing correct
  state is never reported as production; the recorded answer names the attempt that
  measured it; the snapshot reads only inside its root; a credential-shaped target is
  not even snapshotted.
* `test_a_preexisting_artifact_is_never_reported_as_produced` — the end-to-end
  reproduction above.
* Related suites: `test_step_graph_managed_execution` 32, `test_local_execution_adapter`
  9, `test_execution_plane` 26, `test_execution_secret_staging` 32 (1 skip),
  `test_block9_evidence_and_verification` 34, `test_product_integration` 93 — all OK.
* Full suite: `Ran 2354 tests in 177.232s` / `OK (skipped=36)`; `compileall` exit 0;
  `git diff --check` exit 0.

### Injection proof

Reverting only the verifier hunk, keeping production and tests otherwise identical:
`INJECTED exit=1` with `AssertionError: PASSED == PASSED`; restoring the patch gave
`byte-exact restore: True` (SHA-256 identical) and the suite green again.

### Contract question for the owner (unchanged, now sharper)

No `VerificationKind` can express "verify a state that already existed". Under the
current contract such a check now yields `ERROR`
(`artifact_preexisting_and_unchanged`), which is honest but may not be what an
operator intends. Options: (a) keep it and document; (b) add an explicit kind for
state verification — a contract change, deliberately not made here.

### Not verified

Cross-process races between the baseline and the spawn are narrowed to the statements
between `start_time` and `Popen`, but no concurrency test was written. `SEE`
(no PostgreSQL/`asyncpg`) is unchanged: 32 skips remain environment-only.

---

## Blocker #2 — FIXED_AND_TESTED (commit `a28c38a`)

Current per-blocker status (the earlier tables above stay as history):

| Blocker | Status |
|---|---|
| #1 artifact provenance | **FIXED_AND_TESTED** — `6605e35` |
| #2 `resume` inventing completion | **FIXED_AND_TESTED** — `a28c38a` |
| #3 damaged record overwritten | `PARTIAL` — reader half fixed in `3b7d99e`; the writer half is not started |
| #4 GET mutating durable state | `BLOCKED` — not started |

### Exact cause

`derive_graph_status()` (`app/workflows/step_graph.py`) returns `COMPLETED` when every
record's `status` is `SUCCEEDED`, and it has nothing else to look at. `_resume_snapshot()`
(`app/workflows/step_runner.py`) downgraded only a durable `RUNNING` record; a record
saved as `SUCCEEDED` passed through untouched. A record carrying `status=SUCCEEDED` with
no `execution_outcome`, no `execution_exit_code` and a declared `EXIT_CODE` verification
still `NOT_RUN` was therefore summarised as `COMPLETED`: work that never ran was
reported as finished.

### Files changed

| File | Change |
|---|---|
| `app/workflows/step_runner.py` (+35) | in `_resume_snapshot`, a restored `SUCCEEDED` is downgraded to `UNKNOWN` unless the record carries real execution evidence (a recorded outcome, an exit code, or a PASSED verification), unless its recorded outcome contradicts success (e.g. `EXECUTION_TIMEOUT`), and unless a **declared `EXIT_CODE` check** is left un-run. The original claim is preserved verbatim as evidence (`saved_claim_was_succeeded_and_is_not_proof`) so the gap is recorded, not hidden |
| `tests/test_step_graph_managed_execution.py` (+251) | five tests, all writing their record through the public `RunStore` and the public `StepGraphSnapshot` codec, then resuming through the public `runner.resume(...)` |

Nothing universal is demanded of every step: a step that declares no check is judged by
its recorded execution alone, and its `NOT_RUN` verification stays legitimate.

### RED → GREEN → INJECTION

RED (before the fix), three failures, each `AssertionError: <StepGraphStatus.COMPLETED>
== <StepGraphStatus.COMPLETED>`:

```
FAIL test_a_saved_success_without_execution_evidence_is_not_completion
FAIL test_a_declared_exit_code_verification_that_never_ran_is_not_confirmation
FAIL test_contradictory_execution_evidence_is_not_success
Ran 37 tests   FAILED (failures=3)
```

The two positive controls passed even before the fix, which is what shows the rule
targets unsupported claims rather than success in general.

GREEN (after the fix): `Ran 37 tests in 5.777s  OK`.

INJECTION: reverting only this hunk reproduced the same three failures;
`byte-exact restore: True` (SHA-256 identical) and green again.

Related suites, all OK: `test_recovery_and_project_memory` 33, `test_block10_http_errors…`
24, `test_artifact_provenance` 9, `test_block9_evidence_and_verification` 34,
`test_product_integration` 93, `test_run_record_classification_api` 14.

Full suite after the fix: `Ran 2359 tests in 155.120s` / `OK (skipped=36)`;
`compileall` exit 0; `git diff --check` exit 0.

### The five tests

1. a saved success with no execution evidence is not completion (also asserts nothing was
   executed to obtain new proof, and that resuming twice answers the same way);
2. a declared `EXIT_CODE` check that never ran is not confirmation;
3. contradictory evidence (outcome `EXECUTION_TIMEOUT` with `SUCCEEDED`) is not success;
4. the positive control: a full, consistent record still resumes as `COMPLETED`;
5. a step that declares no check keeps its legitimate `NOT_RUN` verification and still
   completes, so the rule is not universal.

### Reading of "do not rewrite the saved record"

`resume` re-persists the snapshot by design (it re-stamps the declaration and envelope),
so whole-file byte comparison is the wrong assertion. The finding recorded here is that
the downgrade **is** persisted: storage moves from `SUCCEEDED` to `UNKNOWN`. Two readings
were available — leave `SUCCEEDED` in storage and re-derive on every resume, or record the
discovery durably. The second was chosen because it matches the codebase's existing
treatment of an interrupted `RUNNING` step, and because a durable record still asserting
`SUCCEEDED` would keep misleading every consumer that reads storage without resuming. To
keep that honest, the original claim is preserved verbatim as evidence rather than dropped.
If the owner prefers the first reading, the change is small and this paragraph is the
place to say so.

### Not verified

No concurrency test was written (two resumes racing on the same record). Platform
database tests still skip: 32 skips remain environment-only, 3 are symlink-related and 1
lacks `OPENAI_API_KEY`. SKIP is not PASS.

---

## Blocker #3 — FIXED_AND_TESTED, both halves (commits `90b2af7`, `0c61092`)

Current per-blocker status (earlier tables stay as history):

| Blocker | Status |
|---|---|
| #1 artifact provenance | **FIXED_AND_TESTED** — `6605e35` |
| #2 `resume` inventing completion | **FIXED_AND_TESTED** — `a28c38a` |
| #3 damaged record | **FIXED_AND_TESTED** — reader `3b7d99e`, writer `90b2af7`, empty-payload `0c61092` |
| #4 GET mutating durable state | `BLOCKED` — not started |

### Exact cause

`StepGraphRunner._attempt()` read the saved record with `recorded()`, whose documented
job is to collapse *"no attempt was recorded"* and *"an attempt was recorded in a form
this build refuses to trust"* into the same `None`. With the reason discarded, a
damaged record looked exactly like an absent one, so `run()` built a fresh snapshot and
wrote it over the damaged file. The reader itself contributed: a payload that was
present but not a mapping (and an explicit `null`) were reported as
`no_step_graph_record`, the same reason an absent key produces.

### Files changed

| File | Change |
|---|---|
| `app/workflows/step_runner.py` | `_attempt()` now reads with `recorded_or_reason()`; if the record is absent **for a reason** -- corruption, unsupported schema, invalid types, empty payload -- it returns `StepGraphResult(status=REFUSED, stopped_reason="untrustworthy_step_graph_record:<reason>", executed=False)` **before** any snapshot is built, any command runs or any byte is written. The reason travels with the result instead of being discarded. `resume` without an explicit graph now names the untrustworthy record instead of claiming there is none |
| `app/workflows/step_graph.py` | the reader distinguishes a missing key (`no_step_graph_record`) from a present-but-not-a-mapping payload and from an explicit `null` (`untrustworthy_step_graph_record:not_a_mapping:*` / `:empty_payload`) |
| `tests/test_step_graph_managed_execution.py` | six tests driving the public `RunStore` and the public `run()` |

### RED → GREEN → INJECTION

RED (before the fix), three failures, each `AssertionError: True is not false` with
`'status': 'COMPLETED'` -- i.e. `run()` executed and completed over a record it could
not read:

```
FAIL test_a_damaged_record_is_never_overwritten_by_a_new_run
FAIL test_an_unsupported_schema_version_is_not_treated_as_absent
FAIL test_an_unreadable_structure_is_not_treated_as_absent
```

GREEN: `Ran 43 tests in 5.786s  OK`.

INJECTION, in two isolated passes so each hunk is proven on its own: reverting the
runner hunk reproduced those three failures; reverting the reader hunk reproduced
`test_a_present_but_empty_record_is_not_treated_as_absent`. Both restores were
byte-exact (SHA-256 identical).

Full suite after both fixes: `Ran 2365 tests in 133.321s` / `OK (skipped=36)`;
`compileall` exit 0; `git diff --check` exit 0; secret scan 0.

### Preservation of the original bytes

Every damaged-record test captures the state file's bytes **and** SHA-256 before
`run()` and asserts both are unchanged afterwards, that `result.executed` is `False`,
that the graph status is `REFUSED`, that `stopped_reason` contains `untrustworthy`, and
that the command produced no side effect (`a.txt` was never written). A repeat call
answers identically and still does not erase the evidence. The positive control
(truly absent record) still starts a new attempt and completes, so absence is
unaffected.

### Residuals, stated rather than hidden

* A read failure that raises `OSError` **propagates** out of `run()`; nothing is
  executed and nothing is written, but the caller gets the exception rather than a
  `REFUSED` result. That is deliberate -- a transient I/O problem is not evidence that
  the record is corrupt -- and it is what the test asserts.
* Concurrency was still not tested: two callers resuming the same damaged record in
  different processes are not covered by a test, only by the in-process claim.

### Not verified

The Platform database suites still skip (32), as do the symlink tests (3) and the one
needing `OPENAI_API_KEY`. SKIP is not PASS.

---

## Blocker #4 — FIXED_AND_TESTED (commit `fc5b098`)

### Exact cause

Two durable writes sat inside the product **read** path, both implicit side effects of
computing a change analysis:

1. `change_analysis()` resolved the baseline through `_resolve_baseline()`, which
   **seeded and persisted** a baseline record when none existed (`_persist_baseline`).
2. The same analysis called `stale_facts()`, which merged the stale-fact ids into the
   baseline record (`_remember_stale_facts`).

`GET /api/product/verification-plan` calls `change_analysis()`, so answering the
question created or rewrote `project-baseline/state.json` -- measured as
`project-baseline/state.json was rewritten by a read`. `GET /api/product/change-impact`
had the same defect through its own seeding block.

### The fix (behaviour, not suppression)

Recording is now something a caller asks for:

| Where | Change |
|---|---|
| `_resolve_baseline(..., *, persist: bool = True)` | a read-only caller still gets a usable in-memory baseline and simply does not create durable state |
| `stale_facts(..., record_history: bool = True)` | with `record_history=False` it **reads** the already-recorded ids through the new `_recorded_stale_fact_ids()` and writes nothing; the response still carries `history_retained` |
| `change_analysis(..., read_only: bool = False)` | passes `persist=not read_only` and `record_history=not read_only` |
| `verification_plan` | calls `change_analysis(read_only=True)` |
| `change_impact(..., read_only: bool = True)` | read-only by default; its GET route cannot seed a baseline |

The POST paths keep their documented behaviour: `POST /api/product/stale-facts` still
records history (its own docstring requires it, and `test_21` asserts it) and
`POST /api/product/baseline` remains the documented way to create a baseline.

### Evidence

RED, before the fix, driving the real route and comparing the store's own files by
name and exact bytes (not HTTP responses):

```
AssertionError: ... : project-baseline/state.json was rewritten by a read
```

GREEN: `Ran 1 test  OK`, then the whole file `Ran 35 tests  OK`.

INJECTION: reverting only this change reproduced
`project-baseline/state.json was rewritten by a read`; the restore was byte-exact
(SHA-256 identical).

The test seeds durable state through the **write** path first
(`POST /api/product/baseline`) and asserts the store is non-empty before comparing, so
it cannot pass vacuously; it then checks two consecutive
`GET /api/product/verification-plan` calls, that the plan body is stable, and that the
onboarding stage does not move.

Related suites, all OK: block9 35, product_integration 93, api 15, api_exposure 16,
recovery_and_project_memory 33, block10_http_errors 24 -- 216 tests.

Full suite: `Ran 2366 tests in 135.295s` / `OK (skipped=36)`; `compileall` exit 0;
`git diff --check` exit 0; secret scan 0.

### Residuals

* `_persist_baseline` and `_remember_stale_facts` remain reachable only from write
  paths; a caller that constructs an `OperatingSurface` directly and asks for a
  read-only analysis gets no baseline on disk until an explicit write happens. That is
  the intended contract, but it is a behaviour change for any out-of-tree caller.
* Concurrency between a read and an explicit write was not tested.
* The 32 Platform database tests still skip (no PostgreSQL); 3 skip on symlinks; 1
  needs `OPENAI_API_KEY`. SKIP is not PASS.

### Stage 1 status: no open blockers

| Blocker | Status |
|---|---|
| #1 artifact provenance | **FIXED_AND_TESTED** — `6605e35` |
| #2 `resume` inventing completion | **FIXED_AND_TESTED** — `a28c38a` |
| #3 damaged record | **FIXED_AND_TESTED** — `3b7d99e`, `90b2af7`, `0c61092` |
| #4 GET mutating durable state | **FIXED_AND_TESTED** — `fc5b098` |

The four Stage 1 blockers are closed. Stage 1 itself is **not** declared complete:
`docs/ROADMAP.md` lists its remaining exit criteria (independent verification, and the
verification-contract question that still needs an owner decision).

---

## Audit finding F-1 — FIXED_AND_TESTED (commit `45cdcd1`)

### The inconsistency the audit measured

`run()` over an already-recorded snapshot echoed the stored status
(`_result_from_snapshot` returned `status=snapshot.status`), while `resume()` screened
the same records for execution evidence. The same record was therefore `COMPLETED`
through one entry point and `INDETERMINATE` through the other. Measured by the audit
probe:

```
PROBE2 run()    -> StepGraphStatus.COMPLETED | executed: False
PROBE2 resume() -> StepGraphStatus.INDETERMINATE
```

### The fix

The evidence screen was extracted from `_resume_snapshot` into
`StepGraphRunner._screen_saved_record(record)` — one implementation, not a copy — and a
new `_screen_snapshot(snapshot)` applies it to every saved record and **re-derives** the
graph status from the screened records (`derive_graph_status`, with the snapshot's
`cancelled` flag). Both entry points now go through it: `resume` via its existing loop,
`run` via the `already_recorded` path. No new `VerificationKind`, no API change, no new
write path: the screened snapshot is in-memory only, and the stored bytes are asserted
unchanged.

### RED → GREEN → INJECTION

RED, before the fix, four failures:

```
FAIL test_an_unproven_saved_completion_is_not_returned_by_run        (COMPLETED)
FAIL test_an_unrun_declared_check_is_not_confirmation_by_run         (COMPLETED)
FAIL test_contradictory_recorded_evidence_is_not_completed_by_run    (COMPLETED)
FAIL test_run_and_resume_agree_about_the_same_record
     AssertionError: COMPLETED != INDETERMINATE
```

GREEN: `Ran 50 tests in 5.486s  OK`.

INJECTION: reverting the change reproduced exactly those four failures; the restore was
byte-exact (SHA-256 identical).

### Tests added (7, all through the public store and `run()`)

1. an unproven saved completion is not returned by `run()` (and the record is not
   rewritten);
2. `run()` and `resume()` agree about the same record;
3. `run()` over a saved record is deterministic and never rewrites it;
4. **positive control**: a fully evidenced record is still returned as `COMPLETED`;
5. a step that declares no check still completes (legitimate step types unaffected);
6. contradictory recorded evidence (`EXECUTION_TIMEOUT`) is not completed;
7. an unrun declared `EXIT_CODE` check is not confirmation.

Related suites, all OK: recovery_and_project_memory 33, artifact_provenance 9,
step_graph_record_robustness 5, run_store_id_containment 6, block9 35,
product_integration 93, block10_http_errors 24 — 205 tests.

Full suite: `Ran 2373 tests in 146.682s` / `OK (skipped=36)`; `compileall` exit 0;
`git diff --check` exit 0; secret scan 0.

### Behaviour change to be aware of

`run()` on an existing record whose evidence does not support its stored status now
reports `INDETERMINATE` (with `stopped_reason="already_recorded"`) instead of echoing
the claim. Records with complete evidence are unaffected, as the positive controls show.

### F-2 remains open

The baseline/harvest size-limit mismatch found by the same audit (a >1 MiB modified
artifact reporting `artifact_provenance_unavailable`) is **not** addressed here, by
explicit scope. It is the next task.

---

## Audit finding F-2 — FIXED_AND_TESTED (commit `52b376d`)

### The measured inconsistency

The provenance baseline was snapshotted with `max_bytes=max_output_bytes`
(`adapter.py`), while the harvest measures with the manager's `max_artifact_bytes`.
Those limits have different jobs and are not interchangeable:

| Limit | Job | Default |
|---|---|---|
| `intent.max_output_bytes` | bounds captured **stdout/stderr** | 1 MiB |
| `max_artifact_bytes` / `DEFAULT_MAX_ARTIFACT_BYTES` | bounds **artifact measurement** | 10 MiB |

A pre-existing artifact larger than 1 MiB that the attempt really modified was
therefore recorded as unmeasurable, and the step reported
`ERROR / artifact_provenance_unavailable` for a production that did happen. Measured
before the fix:

```
AssertionError: <VerificationStatus.ERROR> is not <VerificationStatus.PASSED>
... 'artifact_provenance_unavailable=big.txt' | 'the harvest did not record whether
    this attempt produced the artifact'
```

### The chosen contract

One limit for measuring an artifact, taken from the same source the harvest uses:

* `EphemeralWorkspaceManager.max_artifact_bytes` is now a public read-only property —
  the manager owns its limit, and the baseline asks it;
* `_run_process(..., artifact_baseline_limit=...)` receives that limit from `execute()`
  and passes it to `snapshot_artifact_states`; when it is `None` the snapshot function
  applies `DEFAULT_MAX_ARTIFACT_BYTES`, so the default path cannot silently diverge;
* the isolating branch passes `ws_mgr.max_artifact_bytes` (the composed manager's own
  limit); the non-isolating branch passes `DEFAULT_MAX_ARTIFACT_BYTES` explicitly,
  because that is what the harvest's inline default manager uses.

Limits were **not** raised: the artifact limit is unchanged at 10 MiB, and the stream
cap is still 1 MiB. A file over the artifact limit is still refused, by the harvest's
own explicit `ValueError` (`create_artifact_from_file`), which the adapter reports as
`ERROR / artifact_harvest_failed` — never as `PASSED`.

### Guarantees re-verified after the change

* a matching path alone still cannot pass (the verifier still requires
  `changed_by_this_attempt is True`);
* baseline and harvest work in one boundary root;
* missing evidence still cannot become success;
* credential-shaped targets are still skipped by the snapshot and refused by the
  harvest;
* nothing outside the root is read;
* provenance stays bound to the attempt (`baseline_recorded_for`).

### RED → GREEN → INJECTION

RED, before the fix, through the real adapter path (isolated run, 2 MiB pre-existing
target appended to by the attempt): `ERROR / artifact_provenance_unavailable`.

GREEN: the step reports `PASSED`, the graph `COMPLETED`
(`Ran 51 tests` in the step-graph suite, `Ran 12 tests` in the provenance suite).

INJECTION: reverting only the adapter hunk reproduced the `artifact_provenance_unavailable`
failure; the restore was byte-exact (SHA-256 identical).

### Tests added

* `tests/test_artifact_provenance.py` — a >1 MiB pre-existing file changed by the
  attempt is production; a file over the limit is refused predictably (the baseline
  records `unreadable`/`sha256=None` and the harvest raises); a baseline with no entry
  for the target answers nothing (never `True`).
* `tests/test_step_graph_managed_execution.py` — the end-to-end reproduction above,
  using a new `append_big` declaration that appends to a pre-existing file.

Related suites, all OK: step-graph 51, provenance 12, local adapter 9, execution plane
26, secret staging 32 (1 skip), recovery 33, block9 35, product integration 93 — 291.

Full suite: `Ran 2377 tests in 140.899s` / `OK (skipped=36)`; `compileall` exit 0;
`git diff --check` exit 0; secret scan 0.

### Residuals

* A target larger than the artifact limit cannot be verified at all — by design, and
  now consistently on both sides. An operator who needs larger artifacts must raise
  the composed manager's `max_artifact_bytes`, which the baseline now follows.
* The TOCTOU windows (baseline→spawn, exit→harvest) and cross-process concurrency
  remain as recorded in the audit; they are out of this finding's scope.
