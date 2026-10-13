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
