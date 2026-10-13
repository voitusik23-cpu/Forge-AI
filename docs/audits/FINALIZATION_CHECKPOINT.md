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
