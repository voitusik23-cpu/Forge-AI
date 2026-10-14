@echo off
REM Forge AI Command Center -- hidden fallback launcher.
REM The desktop shortcut points straight at pythonw + tools\forge_launch.py (no console at
REM all). This file exists for the case where the shortcut is missing or blocked.
setlocal
cd /d "%~dp0.."
start "" ".venv\Scripts\pythonw.exe" -B -X utf8 "tools\forge_launch.py"
endlocal
