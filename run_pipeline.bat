@echo off
REM ============================================================
REM   BTSM Pipeline Poller
REM   Triggered by Task Scheduler every 30 min, 11:00 AM - 8:00 PM ET
REM   (Plus once just after the 10 AM download completes)
REM ============================================================

setlocal
cd /d "%~dp0"

REM Use the project venv interpreter (locked versions + weasyprint), not global python.
set "PY=%~dp0.venv\Scripts\python.exe"

REM Clear stale Python bytecode so any code edits always take effect.
REM (Insurance against __pycache__/*.pyc shadowing updated source.)
if exist "__pycache__" rmdir /s /q "__pycache__"

"%PY%" run_pipeline.py > "pipeline_run.log" 2>&1
set "RC=%ERRORLEVEL%"

REM Promote the day's downloaded DRFs into the permanent per-track database
REM (<TRACK>\RAW_DATA\RACINGFORM\<year>\) so history accumulates for free and
REM never has to be re-purchased. Runs every tick; idempotent, skips duplicates.
"%PY%" archive_drfs.py >> "pipeline_run.log" 2>&1

if %RC% NEQ 0 (
    echo Pipeline tick failed with exit code %RC% — sending email

    "%PY%" notify.py "Pipeline tick failed (exit %RC%)" "The pipeline tick returned a non-zero exit code. This now fires only when a FINAL (race-day) publish fails; transient preview-upload hiccups retry on the next tick and no longer trigger this alert." --logfile "%~dp0pipeline_run.log" --tail 40
    exit /b %RC%
)

echo Pipeline tick OK
exit /b 0
