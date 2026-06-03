@echo off
REM ============================================================
REM  BTSM Daily DTS Cleanup
REM  Run via Windows Task Scheduler — daily @ 3:00 AM Eastern
REM
REM  This wrapper exists so that Task Scheduler can detect a
REM  catastrophic failure (Python missing, script missing, etc.)
REM  and email about it. cleanup_dts.py fires its own granular
REM  alerts for normal failure modes (401, 5xx, network, etc.)
REM  via notify.py — this wrapper only handles the edge cases
REM  where cleanup_dts.py couldn't even start.
REM
REM  Exit codes:
REM    0  — cleanup completed successfully (or had recoverable issues,
REM         in which case cleanup_dts.py already alerted)
REM    1  — cleanup_dts.py ran but reported a failure (already alerted)
REM    2+ — wrapper-level catastrophe (Python missing, script missing)
REM ============================================================

setlocal

REM Move to the script's directory so relative paths in .env work
cd /D "%~dp0"

REM Run the cleanup
python cleanup_dts.py
set RC=%ERRORLEVEL%

REM ── Catastrophic-failure path ─────────────────────────────
REM If exit code >= 2, something went wrong before cleanup_dts.py
REM could fire its own alert (Python not found, script missing,
REM dependency import crash, etc.).
IF %RC% GEQ 2 (
    echo Wrapper-level failure — cleanup_dts.py did not run cleanly. Exit: %RC%

    REM Try to send a wrapper alert. notify.py is the same module
    REM cleanup_dts.py uses, but we call it directly here in case
    REM cleanup_dts.py was the thing that crashed.
    python notify.py "DTS cleanup wrapper FAILED (exit %RC%)" ^
        "run_cleanup.bat exited with code %RC%. This means cleanup_dts.py could not run at all — typically Python is missing from PATH, a dependency failed to import, or the script file is missing. The cleanup did NOT happen today. Check Task Scheduler history and run `python cleanup_dts.py` manually to diagnose."

    exit /b %RC%
)

REM ── Normal exit paths ─────────────────────────────────────
REM RC=0  — success, no action needed
REM RC=1  — cleanup_dts.py reported failure and already emailed
REM         (no second email from the wrapper — that would just be noise)
exit /b %RC%
