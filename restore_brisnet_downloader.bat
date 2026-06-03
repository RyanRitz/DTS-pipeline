@echo off
REM ============================================================
REM  restore_brisnet_downloader.bat
REM  ------------------------------------------------------------
REM  Installs brisnet_download_v3.py as the active downloader.
REM
REM  This script expects:
REM    %BASE%\brisnet_download_v3_whitelisted.py    (patched v3 — preferred)
REM    or
REM    %BASE%\archive\old_versions\brisnet_download_v3.py
REM        (unpatched v3 — the May-9 verified working version)
REM
REM  Pick the patched one if present (downloads only BTSM whitelist
REM  tracks).  Fall back to the unpatched one if not.
REM
REM  Run from any directory; this script cd's into FullAutomation.
REM ============================================================

setlocal EnableDelayedExpansion

set "BASE=C:\Users\ryanr\Documents\BTSM\FullAutomation"
set "V3_PATCHED=%BASE%\brisnet_download_v3_whitelisted.py"
set "V3_ARCHIVED=%BASE%\archive\old_versions\brisnet_download_v3.py"
set "ACTIVE=%BASE%\brisnet_download.py"
set "BACKUP_DIR=%BASE%\archive"
set "PROFILE_DIR=%BASE%\chrome_selenium_profile"

cd /d "%BASE%" || (
    echo ERROR: cannot cd to %BASE%
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Installing brisnet_download_v3 as active downloader
echo ============================================================
echo.

REM ── 1. Pick which v3 source to install ─────────────────────────
if exist "%V3_PATCHED%" (
    set "SRC=%V3_PATCHED%"
    set "SRCDESC=whitelist-patched v3 (drops non-BTSM tracks)"
) else if exist "%V3_ARCHIVED%" (
    set "SRC=%V3_ARCHIVED%"
    set "SRCDESC=archived v3 (downloads ALL viewable tracks)"
) else (
    echo ERROR: no v3 source found.  Looked at:
    echo   %V3_PATCHED%
    echo   %V3_ARCHIVED%
    echo.
    echo Aborting. Nothing changed.
    pause
    exit /b 1
)
echo [1/5] Source: !SRCDESC!
echo       File:   !SRC!

REM ── 2. Back up the current brisnet_download.py ─────────────────
REM     Timestamped so repeat runs don't clobber prior backups.
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set "DT=%%a"
set "STAMP=%DT:~0,8%_%DT:~8,4%"
set "BACKUP=%BACKUP_DIR%\brisnet_download_PREV_%STAMP%.py"

if exist "%ACTIVE%" (
    if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
    copy /Y "%ACTIVE%" "%BACKUP%" >nul
    if errorlevel 1 (
        echo ERROR: backup copy failed.
        pause
        exit /b 1
    )
    echo [2/5] Backed up current brisnet_download.py to:
    echo       %BACKUP%
) else (
    echo [2/5] No existing brisnet_download.py to back up - skipping.
)

REM ── 3. Install v3 as brisnet_download.py ───────────────────────
copy /Y "!SRC!" "%ACTIVE%" >nul
if errorlevel 1 (
    echo ERROR: failed to copy v3 to active location.
    pause
    exit /b 1
)
echo [3/5] Installed !SRCDESC! as %ACTIVE%

REM ── 4. Remove stale Chrome profile copy ────────────────────────
REM     v3 mirrors the live Chrome profile on first run.  Deleting
REM     the cache forces v3 to pick up fresh cookies after a
REM     password reset.
if exist "%PROFILE_DIR%" (
    rmdir /S /Q "%PROFILE_DIR%"
    if exist "%PROFILE_DIR%" (
        echo WARNING: could not fully remove %PROFILE_DIR%
        echo          Close any Chrome windows and try again, or
        echo          delete the folder manually.
    ) else (
        echo [4/5] Removed stale Chrome profile copy: %PROFILE_DIR%
    )
) else (
    echo [4/5] No stale Chrome profile copy to remove.
)

REM ── 5. Show .env credentials so the operator can verify them ───
echo.
echo [5/5] Verifying .env has BRISNET_* credentials set
echo ------------------------------------------------------------
if exist "%BASE%\.env" (
    findstr /B "BRISNET_USER BRISNET_PASS" "%BASE%\.env"
) else (
    echo WARNING: %BASE%\.env not found.
    echo          v3 needs BRISNET_USER and BRISNET_PASS in .env.
    echo          Create that file before running brisnet_download.py.
)
echo ------------------------------------------------------------

echo.
echo ============================================================
echo  Done. Next steps:
echo ============================================================
echo.
echo   1. Confirm BRISNET_PASS in .env matches the password you
echo      reset to today. If not, edit .env now.
echo.
echo   2. Run:
echo        python brisnet_download.py
echo.
echo      On first run after the profile reset, v3 will copy
echo      your live Chrome profile (one-time, 30-60 seconds).
echo.
echo   3. Watch for the summary line at the end:
echo        Downloaded: N  Skipped: M  Failed: 0
echo.
echo   Backup of previous version is at:
echo     %BACKUP%
echo.
pause
endlocal
