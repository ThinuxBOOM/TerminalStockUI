# Single cron call against a LOCAL backend (no GitHub cloud involved).
# GitHub-hosted runners cannot reach your machine's localhost, so these
# scripts run ON the host itself (Task Scheduler / cron / self-hosted runner).
#
# Usage: .\cron-call.ps1 -Path /api/cron/snapshot
#        .\cron-call.ps1 -Path /api/cron/retention -Method POST -Body '{"apply": true}'
#        .\cron-call.ps1 -Path /api/cron/snapshot -BackendUrl http://192.168.1.20:8000
#
# CRON_SECRET: pass -CronSecret, set $env:CRON_SECRET, or leave it in
# infra/docker/.env (parsed automatically). Empty = header omitted (open local dev).
param(
  [Parameter(Mandatory = $true)][string]$Path,
  [string]$Method = "GET",
  [string]$Body = "",
  [string]$BackendUrl = "",
  [string]$CronSecret = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BackendUrl)) { $BackendUrl = $env:BACKEND_URL }
if ([string]::IsNullOrWhiteSpace($BackendUrl)) { $BackendUrl = "http://127.0.0.1:8000" }
$BackendUrl = $BackendUrl.Trim().TrimEnd("/")

if ([string]::IsNullOrWhiteSpace($CronSecret)) { $CronSecret = $env:CRON_SECRET }
if ([string]::IsNullOrWhiteSpace($CronSecret)) {
  # Resolve relative to repo root (scripts/cron -> ../..).
  $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
  $envFile = Join-Path $repoRoot "infra\docker\.env"
  if (Test-Path -LiteralPath $envFile) {
    foreach ($line in (Get-Content -LiteralPath $envFile)) {
      $t = $line.Trim()
      if ($t -eq "" -or $t.StartsWith("#") -or ($t.IndexOf("=") -lt 0)) { continue }
      $k = $t.Substring(0, $t.IndexOf("=")).Trim()
      $v = $t.Substring($t.IndexOf("=") + 1).Trim()
      if ($k -eq "CRON_SECRET" -and $v -ne "") { $CronSecret = $v }
    }
  }
}

$headers = @{}
if (-not [string]::IsNullOrWhiteSpace($CronSecret)) {
  $headers["Authorization"] = "Bearer $CronSecret"
}

$url = "$BackendUrl$Path"
try {
  if ($Method -eq "POST") {
    if ([string]::IsNullOrWhiteSpace($Body)) { $Body = "{}" }
    $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 55 -Uri $url -Method POST -Headers $headers -ContentType "application/json" -Body $Body
  } else {
    $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 55 -Uri $url -Method GET -Headers $headers
  }
  Write-Output "[$Method $Path] HTTP $($resp.StatusCode)"
  Write-Output $resp.Content
  if ($resp.StatusCode -ne 200) { exit 1 }
} catch {
  Write-Output "[$Method $Path] FAILED: $($_.Exception.Message)"
  exit 1
}
