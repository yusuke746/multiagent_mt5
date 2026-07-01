@echo off
setlocal EnableExtensions
REM ==========================================================================
REM TradingAgents x XMTrading auto-trader - single-cycle runner
REM Intended to be launched by Windows Task Scheduler.
REM Runs once per day during the US session (JST 00:30, Tue-Sat).
REM NOTE: keep this file ASCII-only. cmd.exe reads .bat in the console OEM
REM codepage, so non-ASCII comments get mangled and may break parsing.
REM ==========================================================================

REM Move to this script's own directory (required for relative paths).
cd /d "%~dp0"

REM Ensure logs folder exists (avoids redirection failure on first run).
if not exist "logs" mkdir "logs"

REM --------------------------------------------------------------------------
REM Resolve the Python interpreter (in priority order):
REM   1) env var TA_XM_PYTHON, if set and the path exists (explicit prod override)
REM   2) bundled virtualenv .venv\Scripts\python.exe, if present
REM   3) python on PATH (try the py launcher first, then python)
REM --------------------------------------------------------------------------
set "PYTHON="

if defined TA_XM_PYTHON (
    if exist "%TA_XM_PYTHON%" set "PYTHON=%TA_XM_PYTHON%"
)

if not defined PYTHON (
    if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"
)

if not defined PYTHON (
    where py >nul 2>&1 && set "PYTHON=py"
)

if not defined PYTHON (
    where python >nul 2>&1 && set "PYTHON=python"
)

if not defined PYTHON (
    echo [%date% %time%] ERROR: Python not found. Set TA_XM_PYTHON or add python to PATH.>> "logs\scheduler_run.log"
    exit /b 9009
)

echo [%date% %time%] cycle start (python=%PYTHON%)>> "logs\scheduler_run.log"
"%PYTHON%" scheduler.py >> "logs\scheduler_run.log" 2>&1
echo [%date% %time%] cycle end (exit=%errorlevel%)>> "logs\scheduler_run.log"

endlocal
