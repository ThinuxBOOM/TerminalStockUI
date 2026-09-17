# Registers Windows Scheduled Tasks that run the data jobs against your LOCAL
# backend — the localhost replacement for .github/workflows/*.yml (cloud
# runners cannot reach your PC's localhost).
#
# Usage (run in PowerShell from any directory):
#   .\install-windows-tasks.ps1                                   # install all
#   .\install-windows-tasks.ps1 -BackendUrl http://127.0.0.1:8000 -CronSecret xxx
#   .\install-windows-tasks.ps1 -Uninstall                        # remove all
#
# Tasks are named OneMarket\<job> and run whether or not you are logged on
# (schtasks prompts for the account password on some editions). The backend
# must be running when a task fires; missed runs are simply skipped — every
# endpoint is idempotent, so just run the job by hand to catch up:
#   .\local-cron.ps1 -Job daily
param(
  [switch]$Uninstall,
  [string]$BackendUrl = "",
  [string]$CronSecret = ""
)

$ErrorActionPreference = "Stop"
$TaskFolder = "OneMarket"
$CronScript = Join-Path $PSScriptRoot "local-cron.ps1"

# Mirror the cloud cadences (local time — the cloud runs UTC; either is fine
# for these jobs, just keep ONE scheduler active per database).
$Jobs = @(
  @{ Name = "evaluate";  Schedule = "MINUTE"; Modifier = "15";            Start = "";        Job = "evaluate" },
  @{ Name = "snapshot";  Schedule = "HOURLY"; Modifier = "1";             Start = "";        Job = "snapshot" },
  @{ Name = "ingest";    Schedule = "DAILY";  Modifier = "1";             Start = "05:30";   Job = "ingest" },
  @{ Name = "sp500";     Schedule = "DAILY";  Modifier = "1";             Start = "06:00";   Job = "sp500" },
  @{ Name = "calibrate"; Schedule = "DAILY";  Modifier = "1";             Start = "06:30";   Job = "calibrate" },
  @{ Name = "score";     Schedule = "DAILY";  Modifier = "1";             Start = "07:00";   Job = "score" },
  @{ Name = "health";    Schedule = "DAILY";  Modifier = "1";             Start = "08:00";   Job = "health" },
  @{ Name = "retention"; Schedule = "WEEKLY"; Modifier = "1";  Day = "SUN"; Start = "03:00"; Job = "retention" }
)

if ($Uninstall) {
  foreach ($j in $Jobs) {
    $tn = "$TaskFolder\$($j.Name)"
    & schtasks.exe /Delete /TN $tn /F 2>$null
    Write-Output "removed $tn (if it existed)"
  }
  exit 0
}

$envPart = ""
if ($BackendUrl -ne "") { $envPart = '$env:BACKEND_URL="' + $BackendUrl + '"; ' }
if ($CronSecret -ne "") { $envPart = $envPart + '$env:CRON_SECRET="' + $CronSecret + '"; ' }
# Without explicit values the scripts fall back to http://127.0.0.1:8000 and
# CRON_SECRET from infra/docker/.env — both fine for default local setups.

foreach ($j in $Jobs) {
  $tn = "$TaskFolder\$($j.Name)"
  $tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "' + $envPart + '& ''' + $CronScript + ''' -Job ' + $j.Job + '"'
  $create = @("/Create", "/TN", $tn, "/TR", $tr, "/SC", $j.Schedule, "/MO", $j.Modifier, "/F")
  if ($j.Schedule -eq "WEEKLY") { $create += @("/D", $j.Day) }
  if ($j.Start -ne "") { $create += @("/ST", $j.Start) }
  & schtasks.exe @create
  if ($LASTEXITCODE -ne 0) { throw "schtasks failed for $tn (exit $LASTEXITCODE)" }
  Write-Output "installed $tn"
}
Write-Output "Done. View with: schtasks.exe /Query /TN 'OneMarket\*'  (backend must be running when tasks fire)"
