# UI recovery — the window opened minimised, so nothing was visible

Starting HEAD: `a79b072`. The application itself was never broken; the window it opened was.

## Reproduced symptom

The owner reports that the application does not display. Reproduced by starting the documented
launcher; the API and the shell server both became ready and the browser window was opened, yet
nothing appeared on screen.

## Verified root cause

Window enumeration while the launcher was running, on a 2560×1440 display:

```
title='FORGE AI — Command Center - Google Chrome'   rect=-16000,-16000,-15843,-15975   iconic=True
title='FORGE AI — Command Center'                   rect=12,12,1276,1391              iconic=False   (after the fix)
```

`-16000,-16000` is where Windows parks a **minimised** window, and `iconic=True` states it. Two
separate mechanisms produced this:

1. **The desktop shortcuts were created with `WindowStyle = 7` (minimised)** — my earlier mistake.
   That show-state is handed to the launched process, so the window was created minimised. The
   launcher had no step that asked for it back, so it stayed invisible while the application ran
   correctly behind it.
2. **A second launch on a profile an instance already holds exits immediately.** Measured with two
   controlled instances: instance A alive, instance B exited within 8 s with code 0, because the
   URL is forwarded to the running instance. So after the first invisible window, clicking the
   shortcut again started nothing and the launcher exited silently.

The application content was verified independently of any window state: a headless capture of the
same URL (`http://127.0.0.1:8020/`) shows the full interface with real data — 2 recorded runs,
0/7 providers `DEGRADED`, project `default`, 21 models reported by `/api/models`.

## Change

`tools/forge_launch.py` only, plus no contract, data or design change:

* `window_handles()` / `window_title()` — enumerate visible windows; `bring_to_front()` restores
  (`SW_RESTORE`, `SW_SHOW`), foregrounds, and **verifies** that `IsIconic` became false;
* a window is reused only if that verification succeeds; a foreign browser window
  (`- Google Chrome` / `- Microsoft Edge` suffix — measured: a `chrome.exe` window that ignores
  `SW_RESTORE`) is skipped explicitly and a fresh browser is started instead;
* the launcher now waits for a **usable window** rather than for the process, because a forwarded
  launch exits at once;
* if no usable window appears, the journal says so and names the profile, instead of finishing as
  though everything had worked.

## Verification

| Check | Result |
|---|---|
| Launcher start, owner's path (`pythonw tools/forge_launch.py`) | API ready 3 s, shell ready 2–3 s, window step executed |
| Window before the change | `iconic=True`, `-16000,-16000` (invisible) |
| Window after the launcher restored it | `iconic=False`, `12,12`, 1264×1379 (on screen) |
| Foreign-window handling | journal: `was_minimised=True, still_minimised=True` → `that window did not restore … so it is not reused` → fresh browser started |
| Honest failure reporting | journal: `no usable window appeared; the application window could not be shown` |
| Page and assets through the window's origin | `/` 200 (index.html referencing `./assets/index-Bruf_O-P.js`), JS 200 `text/javascript`, CSS 200 `text/css` |
| API through the same origin | `/api/status`, `/api/models`, `/api/dashboard`, `/api/agents`, `/api/product/runs`, `/diag` → all 200 |
| TypeScript / production build | `tsc --noEmit` exit 0 / `vite build` exit 0 |
| `python -m compileall -q app tests` and `-q tools` | exit 0 / exit 0 |
| `git diff --check` | exit 0 |

## Owner actions (cannot be done from here)

The Desktop lies outside the repository, so the sandbox refused to write the shortcuts
(`UnauthorizedAccessException`) even with the widest permission. In the owner's own session:

```powershell
$ws = New-Object -ComObject WScript.Shell
foreach ($n in @('Forge AI Command Center.lnk','Forge AI - Вход.lnk')) {
  $p = Join-Path ([Environment]::GetFolderPath('Desktop')) $n
  $s = $ws.CreateShortcut($p); $s.WindowStyle = 1; $s.Save()
}
```

If a stale browser still holds the launcher profile, this ends it (filtered by process name, so it
cannot touch an ordinary browsing session):

```powershell
Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
  Where-Object { $_.CommandLine -like '*webview-profile*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

## Limitations of this verification

* A **visible** window cannot be produced from this non-interactive session: a fresh browser spawn
  creates no on-screen window here, and only the restore of an existing window can be observed. The
  rendering evidence is therefore the headless capture of the real URL, and the restore evidence is
  the measured `iconic`/rect transition. Final visual confirmation belongs to the owner's session.
* The headless capture uses the built bundle and the live API; no mock data was introduced and no
  empty state is used to hide a failure.
* Stage D has not been started, and no paid provider was contacted.
