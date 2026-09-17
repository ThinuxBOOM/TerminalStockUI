# Local cron dispatcher — runs the scheduled data jobs against your LOCAL
# backend. This is the localhost replacement for the GitHub Actions workflows
# (.github/workflows/*.yml), which cannot reach your machine: GitHub-hosted
# runners live in the cloud and your localhost is not publicly routable.
#
# Usage: .\local-cron.ps1 -Job <name> [-WithRetention]
#   jobs: ingest | sp500 | calibrate | snapshot | evaluate | score |
#         health | retention | daily | all
#
#   daily  = ingest + calibrate + score + health   (the overnight batch)
#   all    = daily + sp500 + snapshot + evaluate   (everything but retention)
#            add -WithRetention to include the weekly purge.
#
# Schedule it with Task Scheduler (see install-windows-tasks.ps1) or run jobs
# by hand. Every job is independent — a failing job never blocks the rest;
# the script exits non-zero if ANY job failed. Endpoints are idempotent, so
# overlapping or retried runs are safe.
param(
  [Parameter(Mandatory = $true)][string]$Job,
  [switch]$WithRetention,
  [string]$BackendUrl = "",
  [string]$CronSecret = ""
)

$ErrorActionPreference = "Continue"
$script:Failed = $false

function Invoke-Job {
  param([string]$Label, [string]$Path, [string]$Method = "GET", [string]$Body = "")
  Write-Output "=== [$Label] $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) ==="
  $args = @{ Path = $Path; Method = $Method }
  if ($Body -ne "") { $args["Body"] = $Body }
  if ($BackendUrl -ne "") { $args["BackendUrl"] = $BackendUrl }
  if ($CronSecret -ne "") { $args["CronSecret"] = $CronSecret }
  & (Join-Path $PSScriptRoot "cron-call.ps1") @args
  if ($LASTEXITCODE -eq 0) {
    Write-Output "--- [$Label] OK ---"
  } else {
    Write-Output "--- [$Label] FAILED ---"
    $script:Failed = $true
  }
}

switch ($Job) {
  "ingest"    { Invoke-Job "ingest" "/api/cron/ingest" }
  "calibrate" { Invoke-Job "calibrate" "/api/cron/calibrate" }
  "snapshot"  { Invoke-Job "snapshot" "/api/cron/snapshot" }
  "evaluate"  { Invoke-Job "evaluate" "/api/cron/evaluate" }
  "score"     { Invoke-Job "score" "/api/cron/score" }
  "health"    { Invoke-Job "health" "/api/cron/health" }
  "retention" { Invoke-Job "retention" "/api/cron/retention" "POST" '{"apply": true}' }
  "sp500" {
    for ($shard = 1; $shard -le 10; $shard++) {
      Invoke-Job "sp500-shard-$shard" "/api/cron/ingest?universe=sp500&shard=$shard&shards=10"
    }
  }
  "daily" {
    foreach ($j in @("ingest", "calibrate", "score", "health")) {
      $p = @{ Job = $j }
      if ($BackendUrl -ne "") { $p["BackendUrl"] = $BackendUrl }
      if ($CronSecret -ne "") { $p["CronSecret"] = $CronSecret }
      & (Join-Path $PSScriptRoot "local-cron.ps1") @p
      if ($LASTEXITCODE -ne 0) { $script:Failed = $true }
    }
  }
  "all" {
    foreach ($j in @("ingest", "calibrate", "score", "health", "snapshot", "evaluate", "sp500")) {
      $p = @{ Job = $j }
      if ($BackendUrl -ne "") { $p["BackendUrl"] = $BackendUrl }
      if ($CronSecret -ne "") { $p["CronSecret"] = $CronSecret }
      & (Join-Path $PSScriptRoot "local-cron.ps1") @p
      if ($LASTEXITCODE -ne 0) { $script:Failed = $true }
    }
    if ($WithRetention) {
      $p = @{ Job = "retention" }
      if ($BackendUrl -ne "") { $p["BackendUrl"] = $BackendUrl }
      if ($CronSecret -ne "") { $p["CronSecret"] = $CronSecret }
      & (Join-Path $PSScriptRoot "local-cron.ps1") @p
      if ($LASTEXITCODE -ne 0) { $script:Failed = $true }
    }
  }
  default {
    Write-Output "usage: local-cron.ps1 -Job <ingest|sp500|calibrate|snapshot|evaluate|score|health|retention|daily|all> [-WithRetention]"
    exit 2
  }
}

if ($script:Failed) { exit 1 } else { exit 0 }
