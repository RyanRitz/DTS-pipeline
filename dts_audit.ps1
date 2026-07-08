# ============================================================
#  DTS Pipeline Audit  -  read-only health check
#  Run:   powershell -ExecutionPolicy Bypass -File .\dts_audit.ps1
#  Safe:  makes NO changes (no commits, no pipeline runs).
#         One 'git fetch' to compare local vs GitHub. Nothing else.
#  Best run on the DESKTOP (production runner); also works on the laptop.
# ============================================================

$Repo = "C:\Users\ryanr\Documents\BTSM\FullAutomation"
if (-not (Test-Path $Repo)) { Write-Host "Repo not found: $Repo" -ForegroundColor Red; exit 1 }
Set-Location $Repo

function Ok  ($m){ Write-Host "  [PASS] $m" -ForegroundColor Green }
function Bad ($m){ Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Note($m){ Write-Host "  [ .. ] $m" -ForegroundColor DarkGray }
function Head($m){ Write-Host "`n== $m ==" -ForegroundColor Cyan }

Write-Host "DTS Pipeline Audit  -  $(Get-Date)" -ForegroundColor White
Write-Host "Repo: $Repo`n"

# 1) Git: in sync with origin/main, and all four fixes in history
Head "Git / deployment"
git fetch --quiet 2>$null
$local  = (git rev-parse --short HEAD 2>$null)
$remote = (git rev-parse --short origin/main 2>$null)
Note "HEAD $local   origin/main $remote"
if ($local -and $local -eq $remote) { Ok "up to date with origin/main" }
else { Bad "NOT in sync with origin/main  ->  run: git pull" }

$commits = (git log --oneline -15) -join "`n"
$expect = [ordered]@{
  "turf attribution (blank comments)" = "wire SAR turf v8 ensemble"
  "gold gate (cumulative win-prob)"   = "cumulative win-prob"
  "scratch feed timeout (FINAL hang)" = "enforce feed timeout"
  "typographic masthead header"       = "typographic Deep Forest masthead"
}
foreach ($k in $expect.Keys) {
  if ($commits -match [regex]::Escape($expect[$k])) { Ok "commit present: $k" }
  else { Bad "commit MISSING: $k" }
}

# 2) Fixes actually live in the source files
Head "Fix markers in source"
function Marker($file,$pattern,$label){
  if ((Test-Path $file) -and (Select-String -Path $file -Pattern $pattern -Quiet)) { Ok $label }
  else { Bad "$label   ($file)" }
}
Marker "attribution.py" 'getattr\(config, "TURF_MODELS"' "attribution turf loader iterates family models"
Marker "output.py"      "prob_above"                     "gold gate keyed on cumulative prob"
Marker "scratches.py"   "setdefaulttimeout"              "scratch feed has a timeout guard"
Marker "pdf.py"         "dts-masthead"                   "new typographic header in pdf.py"

# 3) SAR turf coefficient files present
Head "SAR turf coefficients"
$turf = Get-ChildItem "coefficients\sar_turf_*.sas7bdat" -ErrorAction SilentlyContinue
if ($turf.Count -ge 17) { Ok "$($turf.Count) sar_turf coefficient files present (want 17)" }
else { Bad "only $($turf.Count) sar_turf coefficient files (want 17)" }

# 4) Runtime signals from the last pipeline log
Head "Last pipeline run (pipeline_run.log)"
if (Test-Path pipeline_run.log) {
  $log = Get-Content pipeline_run.log
  $attr = ($log | Select-String "attribution: loaded" | Select-Object -Last 1).Line
  if ($attr) {
    Note $attr.Trim()
    if ($attr -match "0 turf") { Bad "attribution loaded 0 turf -> turf comments will be blank" }
    else { Ok "turf coefficients loaded into attribution" }
  } else { Note "no 'attribution: loaded' line in current log yet" }

  $fin = ($log | Select-String "\[FINAL\]" | Select-Object -Last 3)
  if ($fin) { Note "recent FINAL activity:"; $fin | ForEach-Object { Note ("   " + $_.Line.Trim()) } }
  else { Note "no [FINAL] lines in the current log" }

  $errs = $log | Select-String "Traceback|ERROR|timed out|hang" | Select-Object -Last 4
  if ($errs) { $errs | ForEach-Object { Bad ("log: " + $_.Line.Trim()) } }
  else { Ok "no tracebacks / timeouts in the current log" }
} else { Note "pipeline_run.log not found (no run captured here yet)" }

# 5) Desktop auto-update log (git pull -> run wrapper)
Head "Auto-update (update.log)"
if (Test-Path update.log) { (Get-Content update.log -Tail 4) | ForEach-Object { Note $_ } }
else { Note "update.log not found (looks like the laptop, not the scheduled desktop)" }

# 6) Any lingering pipeline process (a hung tick)
Head "Running processes"
$py = Get-Process python -ErrorAction SilentlyContinue
if ($py) {
  Note "$($py.Count) python process(es) running (expected only mid-tick):"
  $py | Select-Object Id, StartTime | Format-Table -AutoSize | Out-String | Write-Host
} else { Ok "no lingering python process" }

# 7) Scheduled task points at the auto-updating wrapper
Head "Scheduled task"
$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
  $act = ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " "
  ($_.TaskName -match "DTS|BTSM|pipeline") -or ($act -match "BTSM|FullAutomation|run_pipeline|update_and_run")
}
if ($tasks) {
  foreach ($t in $tasks) {
    $exec = ($t.Actions | ForEach-Object { $_.Execute + " " + $_.Arguments }) -join "; "
    $flag = "NO auto-pull (points at: $exec)"
    if ($exec -match "update_and_run") { $flag = "auto-pull ON (update_and_run.bat)" }
    Note "$($t.TaskName) - state $($t.State) - $flag"
  }
} else { Note "no obvious DTS task found; check Task Scheduler manually" }

Write-Host "`nAudit complete.`n" -ForegroundColor White
