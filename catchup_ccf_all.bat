@echo off
REM ============================================================
REM  ONE-OFF FULL CCF CATCH-UP  --  entire DTS whitelist
REM  Fills every chart the DB holds a DRF for but is missing,
REM  3/1/26 free window, so the lower-track (pooled) model has
REM  outcomes to train on. Idempotent (skips already-charted).
REM  Run on the LAPTOP.
REM
REM  LONG RUN (can be 1-3h across 37 tracks x 5 months).
REM   - Do NOT overlap the desktop's 7 AM / 12 PM Brisnet logins.
REM   - Either finish before the 9 PM nightly, OR disable the
REM     "BTSM CCF Nightly" task for tonight (this covers its work).
REM  COST: assumes 2026-03-01+ charts are free for all tracks
REM  (Comprehensive Chart Files window). If unsure, add  --limit 10
REM  to the command below to preview the fetch count first.
REM ============================================================
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

set "TRACKS=CD,SAR,BEL,SA,GP,OP,DMR,AQU,BAQ,KEE,KD,MTH,PRX,FG,PIM,TAM,WO,RP,IND,LRL,LS,DEL,PEN,PID,HOU,MVR,MNR,ZIA,ELP,TDN,SUN,EVD,CNL,PRM,TP,CBY,CT"

echo [%date% %time%] FULL CCF catch-up: %TRACKS%
"%PY%" brisnet_ccf.py --product CCF --pair-from-db --tracks %TRACKS% --start 2026-03-01 --end 2026-08-31 >> "ccf_catchup.log" 2>&1
set "RC=%ERRORLEVEL%"

"%PY%" archive_charts.py >> "ccf_catchup.log" 2>&1

echo [%date% %time%] catch-up done (sweep exit %RC%). See ccf_catchup.log
endlocal
