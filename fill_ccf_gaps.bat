@echo off
REM ============================================================
REM   BTSM CCF Gap Backfill  --  ONE-OFF, run on the LAPTOP
REM
REM   Fills the chart (CCF) holes that the nightly job will NOT
REM   self-cure: the nightly only looks back --since-days 6, and
REM   every remaining hole is older than that (5/8 .. 7/21/2026).
REM
REM   Mechanism (same as run_ccf_nightly.bat, wider window):
REM     --pair-from-db  : fetch ONLY cards we already hold a DRF
REM                       for that still LACK charts (idempotent;
REM                       cards already charted are skipped).
REM     --start/--end   : 2026-05-01 .. 2026-08-04 window.
REM   All target dates are >= 2026-03-01, so they are inside the
REM   free CCF window -- $0 to fetch.
REM
REM   SCOPE: real gap tracks only. FP is EXCLUDED (its two DRFs
REM   are misfiled EVD/FPK cards that already have charts). The
REM   obscure tracks (BPD CCP EQB EQK EQZ LBG SWA SWL WBR) are
REM   attempted but BRISnet may not carry CCF for all of them --
REM   the log will show ok/miss per track.
REM
REM   RUN WHEN the desktop is NOT logging into Brisnet (avoid
REM   7 AM / 12 PM) -- concurrent sessions can invalidate.
REM ============================================================
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

REM 13 mainstream real-gap tracks (charts definitely exist on BRIS):
set "MAIN=SAR,IND,TDN,MNR,PRX,DEL,CNL,EVD,MTH,ELP,PRM,WO,RP"
REM Obscure tracks (attempt; may not be offered as CCF):
set "OBSCURE=BPD,CCP,EQB,EQK,EQZ,LBG,SWA,SWL,WBR"

set "TRACKS=%MAIN%,%OBSCURE%"

echo [%date% %time%] CCF gap backfill: %TRACKS%
"%PY%" brisnet_ccf.py --product CCF --pair-from-db --tracks %TRACKS% --start 2026-05-01 --end 2026-08-04 >> "ccf_backfill.log" 2>&1
set "RC=%ERRORLEVEL%"

REM File whatever landed into <TRACK>\RAW_DATA\RESULTS (idempotent copy).
"%PY%" archive_charts.py >> "ccf_backfill.log" 2>&1

echo [%date% %time%] done (sweep exit %RC%). See ccf_backfill.log
endlocal
