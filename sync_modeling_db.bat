@echo off
REM ============================================================
REM   DTS - Modelling database sync  (LAPTOP)
REM   Pulls new DRFs + chart files from the home desktop's archive
REM   (via Google Drive) into this laptop's BTSM for SAS modelling.
REM   Safe to run daily via Task Scheduler; adds only, never deletes.
REM ============================================================
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
"%PY%" sync_modeling_db.py >> "sync_modeling_db.log" 2>&1
exit /b %ERRORLEVEL%
