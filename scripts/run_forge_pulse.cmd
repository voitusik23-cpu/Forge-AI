@echo off
REM Forge Pulse -- desktop widget launcher.
REM
REM Runs the widget with pythonw so no console window appears, from the repository
REM root (the widget imports ``app.*``, so the working directory matters).
REM
REM Double-click this file, or copy it to the Desktop and edit REPO below to an
REM absolute path if you want it to work from anywhere.

setlocal
set "REPO=%~dp0.."
cd /d "%REPO%"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Forge Pulse needs the project virtual environment at .venv\Scripts\pythonw.exe
    echo Create it first, then run this launcher again.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -B -m app.desktop.widget
endlocal
