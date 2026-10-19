# Forge AI — Project State (2026-10-16 18:33)

## Desktop artifact (P0-A — CLOSED, VERIFIED)

| Field | Value |
|---|---|
| Path | `dist/Forge AI.exe` |
| Size | **46080 bytes** |
| Built | **2026-10-16 18:00:20** |
| SHA-256 | **E5E2013B403523A1ABB64499A022D653BD20A0B70D4E7CDDFA6F3A793270B88D** |
| `--check` on this artifact | ran by the build script itself: "All prerequisites present", exit code 0 |

The previous artifact (14:57:31, `F1F2671F7372E0A0…`) is superseded. Build rule, proven by measurement:
`csc.exe` refuses to write when it is reached through a nested parent chain (shell → Python → PowerShell →
`csc`); the build **must** be started from PowerShell directly
(`powershell -File scripts\build_desktop_exe.ps1`). Windows policies were not changed or bypassed.

## Tests (measured on this tree)

| Check | Command | Result |
|---|---|---|
| Full suite | `.venv\Scripts\python.exe -B -X utf8 .forge\run_confined.py discover -s tests -v` | `Ran 2524 tests in 899.828s` → **OK (skipped=36)**, exit 0 |
| Launcher profile | `… -p test_launcher_profile_conflict.py` | 6 tests, **0 skips**, OK |
| API exposure | `… -p test_api_exposure.py` | 16 tests, OK |
| Phase 2 composition | `… -p test_production_composition_idempotency.py` | 8 tests, OK |
| Acceptance module | `… -p test_acceptance_production.py` | 43 tests, OK (3/3 isolated runs) |
| Compilation / whitespace | `compileall app tests`, `git diff --check` | exit 0 / exit 0 |
| Frontend | `tsc --noEmit`, `vite build` (ui/) | exit 0 / exit 0 |

## CI investigation (P0-C — measurements recorded, cause NOT established)

Full-suite wall clock on the same tree: **312 s → 638 s → 899 s** (~3x spread). Individually measured, one
process per module: `test_project_discovery_production.py` **2.678 s**, `test_desktop_program.py` **3.766 s**,
`test_api_host_execution.py` **0.563 s**, `test_agent_harness_ai_integration.py` **0.752 s** — 12.2 s for all
four. The earlier suspicion that `test_api_host_execution` was slow is therefore **refuted**; no single module
explains the spread, and **the cause is not yet established**. One full run failed once in
`test_acceptance_production.CriterionIdentityTests.test_run_reports_one_consistent_criterion_identity`, which
passed 3/3 in isolation and again in the latest full run (1 failure in 2 full runs, 0 in 3 isolated runs).
GitHub CI: `platform-postgres` passes; the general suite is cancelled at the 90-minute job limit; the
diagnostic job is cancelled at GitHub's 1 h 40 m hard cap. The mandatory job has not been weakened.

## Git

* HEAD `19fa11cc63d5`, branch `master`, `origin/master` = `a8ecd4c` (one local commit ahead, unpushed).
* Modified: `app/api/main.py`, `app/api/server.py` (Phase 2 guard), `tools/forge_launch.py` (busy-profile fix),
  `ui/src/design/tokens.css`, `ui/src/design/components.css` (brand layer).
* Untracked: build script, C# launcher, `dist/`, the two new test files, the three consolidation documents and
  the inventoried temporary directories. **No commit, no push.** Protected artifacts untouched.

## Ready / needs manual verification / blocks "finished product"

* **Ready:** browser UI and Core API through the existing launcher; declared execution through the step graph;
  Phase 2 guard wired into the standard composition; a real, verified desktop EXE.
* **Manual:** window open on a busy profile, minimise/restore/maximise, clean close without leftover Forge
  processes, anvil icon in title bar and taskbar, and creating the `Forge AI` shortcut.
* **Blocking completion:** no live-model planning ever ran inside Forge; project creation from the UI does not
  exist and the Core↔Platform `project_id` contract is a proposal, not code; the end-to-end scenario is proven
  only for operator-declared execution; CI cannot complete a full run; Platform/Core invariants were not
  re-measured this session.
