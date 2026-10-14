@echo off
REM FORGE AI -- Command Center shell.
REM Starts the Core API (if it is not running), the static preview server, and opens the
REM interface in an Edge WebView2 window without browser chrome. The old tkinter
REM application stays available: python -m app.desktop
setlocal
cd /d "%~dp0.."
set "PORT=8020"

if not exist "ui\dist\index.html" (
  echo ui\dist is missing. Build the frontend first:
  echo   cd ui ^&^& npm install ^&^& npm run build
  pause
  exit /b 1
)

start "Forge Core API" /min ".venv\Scripts\python.exe" -B -X utf8 -m app.api.main
timeout /t 3 /nobreak >nul
start "Forge Command Center" /min ".venv\Scripts\python.exe" -B -X utf8 tools\ui_preview_server.py --port %PORT%
timeout /t 2 /nobreak >nul
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" ^
  --app="http://127.0.0.1:%PORT%/" --window-size=1600,1000 ^
  --user-data-dir="%CD%\.forge\webview-profile" --no-first-run --no-default-browser-check
endlocal
