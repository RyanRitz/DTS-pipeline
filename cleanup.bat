@echo off
REM ============================================================
REM BTSM FullAutomation cleanup script
REM Moves debug/old files into archive subdirectories.
REM No production files are touched.
REM ============================================================

cd /d "%~dp0"

echo Creating archive directories...
mkdir archive 2>nul
mkdir archive\debug_scripts 2>nul
mkdir archive\old_versions 2>nul
mkdir archive\investigation 2>nul
mkdir archive\old_snapshots 2>nul
mkdir archive\scheduler 2>nul

echo.
echo Moving debug scripts...
move "audit_missing_features.py"  archive\debug_scripts\ 2>nul
move "dump_turf_features.py"      archive\debug_scripts\ 2>nul
move "test_scratches.py"          archive\debug_scripts\ 2>nul
move "test_apply_scratches.py"    archive\debug_scripts\ 2>nul
move "test_track_status.py"       archive\debug_scripts\ 2>nul
move "check_drf_naming.py"        archive\debug_scripts\ 2>nul
move "brisnet_inspect.py"         archive\debug_scripts\ 2>nul
move "brisnet_capture_url.py"     archive\debug_scripts\ 2>nul
move "brisnet_scope_dump.py"      archive\debug_scripts\ 2>nul
move "brisnet_scope_dump2.py"     archive\debug_scripts\ 2>nul

echo.
echo Moving old downloader versions...
move "brisnet_download_v2.py"    archive\old_versions\ 2>nul
move "brisnet_download_v3.py"    archive\old_versions\ 2>nul
move "brisnet_download_fixed.py" archive\old_versions\ 2>nul

echo.
echo Moving investigation artifacts...
move "brisnet_debug.png"                  archive\investigation\ 2>nul
move "brisnet_debug_2.png"                archive\investigation\ 2>nul
move "brisnet_debug_3.png"                archive\investigation\ 2>nul
move "brisnet_debug_err.png"              archive\investigation\ 2>nul
move "brisnet_debug_login.png"            archive\investigation\ 2>nul
move "login_failure.png"                  archive\investigation\ 2>nul
move "diag_screenshot.png"                archive\investigation\ 2>nul
move "diag_screenshot_before_relogin.png" archive\investigation\ 2>nul
move "diag_page.html"                     archive\investigation\ 2>nul
move "diag_page_before_relogin.html"      archive\investigation\ 2>nul
move "no_viewable.png"                    archive\investigation\ 2>nul
move "cd_raw.html"                        archive\investigation\ 2>nul
move "cd_mobile.html"                     archive\investigation\ 2>nul
move "cd_weather.html"                    archive\investigation\ 2>nul
move "brisnet_page.html"                  archive\investigation\ 2>nul
move "brisnet_page_rendered.html"         archive\investigation\ 2>nul
move "brisnet_page_rendered.png"          archive\investigation\ 2>nul
move "scope_dump.json"                    archive\investigation\ 2>nul
move "scope_dump2.json"                   archive\investigation\ 2>nul
move "brisnet_cookies.json"               archive\investigation\ 2>nul
move "brisnet_track_dump.json"            archive\investigation\ 2>nul
move "audit_report.txt"                   archive\investigation\ 2>nul
move "missing_features.csv"               archive\investigation\ 2>nul
move "turf_feature_dump.csv"              archive\investigation\ 2>nul
move "turf_feature_dump.log"              archive\investigation\ 2>nul
move "run_output.txt"                     archive\investigation\ 2>nul

echo.
echo Moving old snapshots and zips...
move "files.zip"                  archive\old_snapshots\ 2>nul
move "files0509.zip"              archive\old_snapshots\ 2>nul
move "filesDL.zip"                archive\old_snapshots\ 2>nul
move "filesSAStoPy_Final.zip"     archive\old_snapshots\ 2>nul
move "filesSAStoPy_Final"         archive\old_snapshots\ 2>nul
move "BTSM_KEE_April8_2026.xlsx"  archive\old_snapshots\ 2>nul

echo.
echo Moving Task Scheduler references...
move "BTSM-Pipeline-Poller.xml"        archive\scheduler\ 2>nul
move "BTSM-Daily-Download.xml"         archive\scheduler\ 2>nul
move "BTSM TaskSchedulerSetup.pdf"     archive\scheduler\ 2>nul

echo.
echo Deleting noise files...
if exist "python" del "python"
if exist "__pycache__" rmdir /s /q "__pycache__"

echo.
echo ============================================================
echo Cleanup complete. Review the archive\ folder.
echo ============================================================
pause