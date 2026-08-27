@echo off
REM ============================================================
REM   BTSM Nightly CCF (chart) Backfill  --  LAPTOP job
REM
REM   The DESKTOP downloads DRFs (run_download.bat) + scores.
REM   The LAPTOP owns the modelling DB, so CHART accumulation
REM   lives here.
REM
REM   SCOPE (expanded 2026-08-12): the FULL DTS_TRACK_WHITELIST,
REM   not just the flagships -- so the lower-track (pooled) model
REM   accumulates outcomes for every track we download DRFs for.
REM   Current-window charts are covered by the Comprehensive Chart
REM   Files subscription; deep history was caught up via
REM   catchup_ccf_all.bat. Keep this list == config.py whitelist.
REM
REM   --pair-from-db  : only cards we already hold a DRF for and
REM                     that still LACK charts (idempotent).
REM   --since-days 6  : re-checks the last 6 days; charts finalize
REM                     (productStatus 'R') after races run, and a
REM                     card missed one night backfills the next.
REM
REM   Schedule: Task Scheduler, once nightly ~9:00 PM local (after
REM   the day's results are final). Do NOT overlap the desktop's
REM   Brisnet logins (7 AM / 12 PM) -- concurrent sessions can
REM   invalidate each other.
REM ============================================================
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

REM Full whitelist. Edit this one line to change chart spend scope.
set "TRACKS=CD,SAR,BEL,SA,GP,OP,DMR,AQU,BAQ,KEE,KD,MTH,PRX,FG,PIM,TAM,WO,RP,IND,LRL,LS,DEL,PEN,PID,HOU,MVR,MNR,ZIA,ELP,TDN,SUN,EVD,CNL,PRM,TP,CBY,CT"

if exist "__pycache__" rmdir /s /q "__pycache__"

echo [%date% %time%] CCF nightly: sweeping charts for %TRACKS%
"%PY%" brisnet_ccf.py --product CCF --pair-from-db --tracks %TRACKS% --since-days 6 >> "ccf_nightly.log" 2>&1
set "RC=%ERRORLEVEL%"

REM File whatever landed. Runs ONLY after the sweep fully exits (serial in a .bat),
REM so the case-insensitive double-glob race is moot and nothing is half-written.
"%PY%" archive_charts.py >> "ccf_nightly.log" 2>&1

if %RC% NEQ 0 (
    echo [%date% %time%] CCF sweep exit %RC% -- see ccf_nightly.log
    exit /b %RC%
)
echo [%date% %time%] CCF nightly OK
exit /b 0
