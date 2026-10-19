# Forge AI — Next Session Handoff (2026-10-16 17:45)

Read `docs/PROJECT_COMPLETION_LEDGER.md` first: it is the register, and `docs/PROJECT_STATE.md` is the
snapshot. Do not trust earlier chat transcripts; re-measure.

## Order of work

1. **P0-1 EXE rebuild (owner action, then verify).** Ask the owner to run, from their own PowerShell:
   `powershell -File C:\Users\User\Desktop\Forge-AI\scripts\build_desktop_exe.ps1` and then
   `dist\"Forge AI.exe" --check`. Confirm the size, mtime and SHA-256 differ from
   46080 bytes / 2026-10-16 18:00:20 / `E5E2013B403523A1ABB64499A022D653BD20A0B70D4E7CDDFA6F3A793270B88D`. The measured blocker is that `csc.exe` returns
   `CS0016 "Отказано в доступе"` when invoked through a nested parent chain; a direct invocation succeeds.
   Do not change Windows policies; do not bypass them.
2. **P0-3 shortcut.** Have the owner run the shortcut command once (creates `Forge AI`, sets
   `WindowStyle = 1`, icon `assets\forge.ico`). Desktop writes are denied from the agent session.
3. **P0-4 test intermittency and CI.** Re-run the full suite and count the frequency of the
   `CriterionIdentityTests` failure against isolated runs of that module. Then add a per-module timeout to
   the diagnostic CI job so a hanging module names itself (do not gate on it, do not weaken the mandatory
   job). The suite's own duration spread (312/638/899 s) is the current best lead.
4. **P1-2 project creation.** The owner chose Platform as the project layer. Before writing code, settle the
   Core→Platform check for `project_id`; a client-supplied project id is not permission. Then build the
   smallest vertical slice with acceptance criteria.
5. **P1-1 live-model scenario.** Prepare it exactly and stop before the paid call:
   **BLOCKED — OWNER APPROVAL REQUIRED**.
6. **P2-1 UI.** Only after P0: audit the existing sidebar/topbar and states, keep the token-based brand layer,
   change nothing structural without evidence of a real gap.
7. **Cleanup (needs confirmation):** `pip-metadata-g0rrt5yb/`, `pip-metadata-p353jwjm/`,
   `pip-unpack-5fbps_tg/`, `pip-unpack-br33raxi/` (failed PyInstaller install residue) and
   `forge_b10_runstore_srnngvs0/` (earlier test residue). Never touch the protected untracked artifacts, the
   WorkLog artifacts under `.forge/e2e-worklog/`, or `.forge/` logs and build output.

## Rules that must not be bent

* `authorizer.py`, `.env`, secrets and the three protected untracked artifacts stay untouched.
* Do not weaken, skip or delete tests, and do not replace real checks with mocks.
* No paid model calls without explicit approval; no commits or pushes without approval.
* Keep the Phase 2 guard wiring (`app/api/main.py`, `app/api/server.py`) exactly as it is unless a proven
  defect requires a change.

## Useful facts

* Confined runner: `.venv\Scripts\python.exe -B -X utf8 .forge\run_confined.py discover -s tests -p <file>`.
* Frontend: `node node_modules/typescript/bin/tsc --noEmit -p tsconfig.json` and
  `node node_modules/vite/bin/vite.js build`, both in `ui/` (bundled Node).
* Launcher journal: `.forge/logs/launcher.log`; the busy-profile retry logs
  `retrying the window with a separate profile`.
* Compiler output is CP1251, not UTF-8 — decode it accordingly when reading `csc` diagnostics.

## Latest measurements (2026-10-16 18:33)

* The desktop artifact is now `dist/Forge AI.exe`, **46080 bytes**, **2026-10-16 18:00:20**, SHA-256 **E5E2013B403523A1ABB64499A022D653BD20A0B70D4E7CDDFA6F3A793270B88D**, and
  `--check` was run on **this** file by the build script (exit 0). Build it from PowerShell directly.
* Per-module timings (one process each): `test_project_discovery_production.py` 2.678 s,
  `test_desktop_program.py` 3.766 s, `test_api_host_execution.py` 0.563 s,
  `test_agent_harness_ai_integration.py` 0.752 s. The earlier suspicion about
  `test_api_host_execution` is refuted; the full-suite spread (312/638/899 s) has **no established cause**.
* A per-module timeout and module-name notice were added **only** to the diagnostic CI job
  (`full-suite-timing`), which is `continue-on-error: true`; the mandatory job is unchanged.
