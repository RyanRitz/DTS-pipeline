@echo off
REM ============================================================
REM  BTSM Daily DRF Downloader
REM  Run via Windows Task Scheduler — twice daily:
REM    7:00 AM  (catches overnight additions)
REM   12:00 PM  (catches late morning post-position lock)
REM ============================================================

REM Load credentials from .env file (keep this file private)
FOR /F "tokens=1,2 delims==" %%A IN (
    "%~dp0.env"
) DO SET %%A=%%B

REM Run the downloader
python "%~dp0brisnet_download.py" ^
    --days 4 ^
    --out  "C:\Users\ryanr\Documents\BTSM\FullAutomation\raw_data"

IF %ERRORLEVEL% NEQ 0 (
    echo Download had failures — check the log
    exit /b 1
)
echo Download complete
exit /b 0
