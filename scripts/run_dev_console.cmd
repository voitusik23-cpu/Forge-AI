@echo off
REM Forge AI -- local dev launcher for the existing server-authoritative web console.
REM
REM Why a .cmd and not a .ps1: this host's PowerShell execution policy refuses to run
REM an unsigned script ("... does not have a digital signature"), so a .ps1 launcher is
REM not a reproducible command here. A batch launcher is unaffected by that policy, and
REM nothing about the security policy is changed to make it work.
REM
REM Loopback only, authorization untouched, no data seeded: the console renders what
REM the API reports, including UNKNOWN and INDETERMINATE.
REM
REM Usage:   scripts\run_dev_console.cmd [port]     (default 8011)
REM Then open http://127.0.0.1:<port>/

setlocal
set "REPO=%~dp0.."
cd /d "%REPO%"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8011"

if not exist ".venv\Scripts\python.exe" (
    echo Forge AI needs the project virtual environment at .venv\Scripts\python.exe
    echo Create it first - see docs\USER_AND_DEVELOPER_GUIDE.md section 5.
    exit /b 1
)

set "FORGE_ENV=test"
set "FORGE_API_HOST=127.0.0.1"
set "FORGE_API_PORT=%PORT%"

echo Forge AI console
echo   bind   : http://127.0.0.1:%PORT%/  (loopback only)
echo   console: /  ^|  assets: /app.css, /app.js
echo   data   : /api/product/onboarding, /api/product/verification-plan
echo   stop   : Ctrl+C in this window
echo.

".venv\Scripts\python.exe" -B -X utf8 -m app.api.main
exit /b %ERRORLEVEL%
