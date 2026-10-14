# Forge AI — Phase 2 completion: launcher, appearance, help, navigation audit

Everything in this document was measured on this machine. Commands whose result matters are
recorded, and anything that does not work is named as such.

---

## Priority 1 — Desktop launcher

### What exists now

| Piece | Path | Purpose |
|---|---|---|
| Shortcut | `%USERPROFILE%\Desktop\Forge AI Command Center.lnk` | the thing the user double-clicks |
| Supervisor | `tools/forge_launch.py` | starts the servers, waits, opens the window, cleans up |
| Icon | `assets/forge.ico` (7 sizes: 16-256), preview `assets/forge-256.png` | brand mark, drawn by `tools/make_icon.py` |
| Hidden fallback | `scripts/forge-command-center.cmd` | for the case where the shortcut is missing or blocked |
| Journal | `.forge/logs/launcher.log`, plus `.forge/logs/api.log` and `.forge/logs/ui.log` | readable start-up record |

### How the launch works

The shortcut targets `.venv\Scripts\pythonw.exe` with the arguments
`-B -X utf8 tools\forge_launch.py`. `pythonw` is a GUI-subsystem binary, so **no console
window appears at any point** — the launcher and both servers run under it. The supervisor
then:

1. checks each port. A port that already answers is **reused and never stopped**, so a
   second launch cannot create a duplicate server;
2. starts the Core API on `127.0.0.1:8011` and the shell server on `127.0.0.1:8020`, both
   loopback only, and never binds a non-loopback interface;
3. waits for readiness by polling `/api/status` and the shell page, up to 45 seconds, and
   logs each step;
4. opens the interface in an Edge WebView2 window (`--app=`, own profile under
   `.forge/webview-profile`);
5. waits for that window to close, then stops **only the processes it started**, by PID,
   and reports whether each port became free;
6. on failure, shows a Windows message box naming the log file, and exits non-zero.

### Verification (measured)

```
18:08:16  launcher start (selftest=True)
18:08:17  core-api: starting on 127.0.0.1:8011
18:08:24  core-api: ready after 8s
18:08:25  shell-server: starting on 127.0.0.1:8020
18:08:26  shell-server: ready after 1s
18:08:26  selftest: readiness confirmed, stopping our own processes
18:08:26  core-api: stopping pid 30136
18:08:26  shell-server: stopping pid 30580
18:08:26  port 8011: free
18:08:27  port 8020: free
18:08:27  launcher finished
```

The shortcut was read back from the shell: target `…\.venv\Scripts\pythonw.exe`, arguments
`-B -X utf8 tools\forge_launch.py`, working directory the repository, icon
`…\assets\forge.ico,0`. The icon loads in Windows (`System.Drawing.Icon` reports 32×32 for
the shell's default size) and contains all seven resolutions.

### How to check the launch without a console

Run this and watch the journal — it performs the whole lifecycle, including the cleanup, and
prints nothing to a console window:

```
.venv\Scripts\python.exe -B -X utf8 tools\forge_launch.py --selftest
type .forge\logs\launcher.log
```

A caveat worth recording: an earlier ad-hoc cleanup command of mine matched processes by
command-line text and therefore also matched the harness that was running the command. The
launcher itself never does this — it holds `Popen` handles and terminates those PIDs only.

---

## Priority 2 — Appearance (Stage C) and Help (Stage D)

### Appearance

* Dark, Light and System, four accents (violet, cyan, amber, emerald), two densities and a
  reduced-motion switch.
* One token system: the switches only set `data-theme`, `data-accent`, `data-density` and
  `data-motion` on `<html>`; every rule reads CSS variables, so no component knows which
  theme is active.
* Preferences live in `localStorage` under `forge.appearance` and are applied by an inline
  bootstrap in `index.html` **before** the bundle loads, so Light never flashes a white page.
* System mode follows the Windows setting and re-applies when it changes.
* A theme button in the top bar cycles Dark → Light → System; the URL may also carry
  `?theme=light&accent=cyan&density=compact&motion=reduced`, which is applied and saved like
  a click (used for the screenshots below, and useful for sharing a look).

**A real defect found by screenshotting and then fixed.** The first Light screenshot showed
collapsed contrast: backgrounds switched but panels kept dark navy literals and notice text
kept pastel colours. 56 literal colours were audited; the 18 that encode a *surface* or a
*message text* were replaced with new tokens (`--surface-rail`, `--surface-topbar`,
`--surface-card`, `--surface-raised`, `--surface-hover`, `--surface-input`,
`--surface-sunken`, `--surface-auth`, `--ok-text`, `--warn-text`, `--danger-text`) defined for
both themes. Accent washes stayed literal on purpose — they read correctly on either
background. After the fix the Light screenshot is legible.

### Help

`ui/src/screens/Help.tsx` with content in `ui/src/help-content.ts`: ten topics in both
languages — projects, models and providers, creating an agent, reading a system prompt,
connecting tools, running a task and checking the result, reading statuses and errors, cost
and limits and provider balance, permissions and approvals, theme and settings. Every screen
offers a **"How does this work?"** link that opens the topic matching that screen
(`CONTEXT_TOPIC` in `help-content.ts`); the topic list is navigable, and RU/EN relabels the
whole guide.

The guide states what is *not* implemented as plainly as what is: no agent system prompt
exists in the model, no per-agent balance or limit exists, provider balances are not probed
in this mode, project creation and the run form are not wired.

---

## Priority 3 — Navigation audit

Status codes: **работает** = built and fed by real data; **частично** = some real data shown,
not a finished section; **заглушка** = placeholder on purpose; **нет данных** = the backend
has nothing to show.

| Section | What works today | Real data connected | Still missing | Minimal next step |
|---|---|---|---|---|
| **Projects** | placeholders + "How does this work?" + project panel inside Overview | `GET /api/projects`, `GET /api/product/onboarding` — one project, "Forge Main Workspace", path, status `active` | no dedicated screen; no create/edit | port the read-only list (endpoint ready); then propose `POST /api/projects` over the existing store |
| **Agents** | full section: KPIs, search, filters, sort, table, agent card, provider aggregates, loading/empty/error+retry | `GET /api/agents`, `GET /api/dashboard`, `GET /api/tools`, `GET /api/capabilities` | create agent; per-agent runs; per-agent permissions; config timestamp | propose `GET /api/product/runs?agent=` (read-only filter over existing records) |
| **Models** | placeholder + guide topic | **no data**: `/api/dashboard.models` is `[]`, `/api/capabilities` carries only `task` and `tool` domains | the model list itself | propose a read-only endpoint over the existing model registry, or leave as "no data" until then |
| **Providers** | provider cards in Overview; provider aggregates in Agents | `GET /api/dashboard` — 7 providers with `account_status`, `forge_spent`, `estimated_cost`, `provider_reported_cost`, `effective_cost`, tokens, success/failure | no dedicated screen; quota fields not surfaced | small screen reusing the aggregates panel plus `account_status.details.reason` |
| **Runs** | placeholder + guide topic; "New run" not wired | `GET /api/product/runs` (empty, `total_recorded: 0`), `GET /api/product/runs/{id}`, `POST /api/tasks/run` exist | run table, run detail, run form | port the read-only Runs table first (endpoint ready), then the form with visible authorization |
| **Tools** | tools listed inside the agent card; capabilities via `GET /api/capabilities` | `GET /api/tools` — `read_project_file`, `write_project_file` | no dedicated screen | small Tools screen listing tools plus the 3 capabilities |
| **Memory** | placeholder | `GET/POST /api/product/memory`, `/memory/{id}/invalidate`, `/memory/refresh` exist | not wired at all; payload not probed in this stage | probe the endpoint, then port a read-only view |
| **Usage & Costs** | totals in Overview KPIs; per-provider money in Agents, each measure named exactly | `today_totals`, `all_time_totals`, per-provider money fields | no dedicated screen | port totals + provider table, no new semantics |
| **Settings** | **working**: Appearance (theme/accent/density/motion), storage status, "where things live" | `localStorage`, and the real paths of this project | provider keys form (must not keep secrets in the page), language move | owner decision on how a key should be entered server-side |
| **Help** | **working**: 10 topics RU/EN, contextual links from every screen | content written from the measured audit | screenshots inside the guide | add the two UI screenshots to the guide |

Nothing in this audit invented data to fill a screen, and no section claims a capability the
backend does not have.

---

## Screenshots

| File | What it shows |
|---|---|
| `docs/images/ui-overview.png` | Overview, dark, real core status |
| `docs/images/ui-agents.png`, `ui-agents-en.png` | Agents with the agent card, dark, RU and EN |
| `docs/images/ui-settings.png` | Settings → Appearance, dark |
| `docs/images/ui-settings-light.png` | Settings → Appearance, Light with a cyan accent |
| `docs/images/ui-agents-light.png` | Agents in Light |
| `docs/images/ui-help.png`, `ui-help-en.png` | the User Guide, RU and EN |
| `assets/forge-256.png` | the product icon |

---

## Changed files in this stage

* `tools/forge_launch.py`, `tools/make_icon.py` (new)
* `assets/forge.ico`, `assets/forge-256.png` (new)
* `scripts/forge-command-center.cmd` (now a hidden fallback)
* `ui/src/design/tokens.css` (themes, accents, density, motion, surface tokens)
* `ui/src/design/components.css` (18 literals replaced by tokens; row selection)
* `ui/src/appearance.ts` (new), `ui/src/help-content.ts` (new)
* `ui/src/screens/Settings.tsx`, `ui/src/screens/Help.tsx` (new)
* `ui/src/components/NavRail.tsx` (theme control, help button, badge count), `ui/src/App.tsx`
  (routing, appearance state, contextual help), `ui/src/i18n.ts` (new keys both languages),
  `ui/index.html` (pre-paint appearance bootstrap)
* this document

**No Python module under `app/` was touched**: Core, Platform, Fabric, Registry, SQL, RLS,
the authorization chain and the financial semantics are exactly as they were. The full suite
ran green before this stage (2419 tests, 36 skips, all skips being the PostgreSQL-dependent
Platform tests) and this stage changed no Python application code.
