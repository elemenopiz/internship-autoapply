<#
.SYNOPSIS
  Sets everything up on first run, then starts the dashboard at http://127.0.0.1:8765.

.DESCRIPTION
  - creates .venv and installs the project plus the Playwright Chromium browser (only when pyproject.toml changes)
  - imports OPENAI_API_KEY from your Windows user environment (see set_openai_key.ps1)
  - starts the dashboard. Profile, screening answers, resume and search settings are edited there.

  The applier itself refuses to apply until check_ready.ps1 reports nothing missing, and the schedule stays
  disabled until you switch it on in the dashboard after a successful dry run.

  Usage:  powershell -ExecutionPolicy Bypass -File .\launch.ps1 [-Port 8765] [-NoBrowser]
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
. (Join-Path $repoRoot 'scripts\_common.ps1')

Set-Location -LiteralPath $repoRoot
Import-UserEnvVar -Name 'OPENAI_API_KEY'

$python = Initialize-Venv -RepoRoot $repoRoot
Install-Dependencies -RepoRoot $repoRoot -VenvPython $python

Write-Host ''
Write-Host 'Readiness check:'
& $python -m autoapply check-ready
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Not ready yet: finish the items above in the dashboard (the applier will not apply until they are done).'
}

Write-Host ''
Write-Host "Starting the dashboard on http://127.0.0.1:$Port (press Ctrl+C to stop)..."
$serveArgs = @('-m', 'autoapply', 'serve', '--host', '127.0.0.1', '--port', "$Port")
if (-not $NoBrowser) { $serveArgs += '--open' }
& $python @serveArgs
exit $LASTEXITCODE
