@echo off
REM ============================================================
REM   BTSM Pipeline — manual run wrapper
REM   Use this when running interactively. Captures all output
REM   (stdout + stderr) to pipeline_run.log so you can upload
REM   the file instead of copy/pasting the console.
REM
REM   For Task Scheduler use, see run_pipeline.bat (the poller
REM   that adds failure-email notification).
REM ============================================================

setlocal
cd /d "%~dp0"

REM Clear stale Python bytecode so any code edits always take effect.
REM (Insurance against __pycache__/*.pyc shadowing updated source.)
if exist "__pycache__" rmdir /s /q "__pycache__"

echo Running pipeline... output will go to pipeline_run.log
python run_pipeline.py > "pipeline_run.log" 2>&1
set RC=%ERRORLEVEL%

echo.
if %RC% EQU 0 (
    echo OK. Last 20 lines:
) else (
    echo FAILED with exit code %RC%. Last 20 lines:
)
echo ----------------------------------------
powershell -NoProfile -Command "Get-Content pipeline_run.log -Tail 20"
echo ----------------------------------------
echo.
echo Full log: %~dp0pipeline_run.log
exit /b %RC%
