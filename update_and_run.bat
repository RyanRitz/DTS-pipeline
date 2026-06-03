@echo off
REM ============================================================
REM   BTSM Pipeline -- DESKTOP (production) launcher
REM
REM   Step 1: pull the latest code from GitHub (origin/main)
REM   Step 2: hand off to run_pipeline.bat (clears pycache, runs poller,
REM           emails on failure -- all unchanged)
REM
REM   Point the DESKTOP's Task Scheduler at THIS file instead of
REM   run_pipeline.bat. The laptop (dev) does NOT use this wrapper --
REM   on the laptop you commit and push by hand.
REM
REM   Design choice: if "git pull" fails (network blip, a local edit on
REM   prod, etc.) we LOG it and run the existing code anyway. Production
REM   must keep producing sheets; a git hiccup should never stop a tick.
REM ============================================================

setlocal
cd /d "%~dp0"

echo [%date% %time%] update_and_run: pulling origin/main >> update.log
git pull origin main >> update.log 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [%date% %time%] WARNING: git pull failed (exit %ERRORLEVEL%) -- running existing code >> update.log
) else (
    echo [%date% %time%] git pull OK >> update.log
)

REM Always run, even if the pull failed.
call run_pipeline.bat
exit /b %ERRORLEVEL%
