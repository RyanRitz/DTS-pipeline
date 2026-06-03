@echo off
REM ============================================================
REM   BTSM Pipeline Poller
REM   Triggered by Task Scheduler every 30 min, 11:00 AM - 8:00 PM ET
REM   (Plus once just after the 10 AM download completes)
REM ============================================================

setlocal
cd /d "%~dp0"

python run_pipeline.py > "pipeline_run.log" 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo Pipeline tick failed with exit code %ERRORLEVEL% — sending email

    REM Grab the last 30 lines of the log to embed in the email
    powershell -NoProfile -Command "Get-Content pipeline_run.log -Tail 30 | Out-File -Encoding utf8 _tail.txt"
    set /p TAIL=<_tail.txt
    python notify.py "Pipeline tick failed (exit %ERRORLEVEL%)" "Last log lines:\n\n!TAIL!\n\nFull log: %~dp0pipeline_run.log"
    del _tail.txt 2>nul
    exit /b %ERRORLEVEL%
)

echo Pipeline tick OK
exit /b 0
