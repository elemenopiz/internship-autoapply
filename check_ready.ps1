<#
.SYNOPSIS
  Lists everything still missing before the applier may start (profile fields, resume PDF, OPENAI_API_KEY, ...).

.DESCRIPTION
  Exit code 0 means the applier is ready. Any other exit code means something is missing; the list is printed.
  Add -Json for machine-readable output.

  Usage:  powershell -ExecutionPolicy Bypass -File .\check_ready.ps1 [-Json]
#>
[CmdletBinding()]
param([switch]$Json)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
. (Join-Path $repoRoot 'scripts\_common.ps1')

Import-UserEnvVar -Name 'OPENAI_API_KEY'
$python = Get-VenvPython -RepoRoot $repoRoot
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'The virtual environment does not exist yet. Run launch.ps1 once to set everything up.'
    exit 2
}

Set-Location -LiteralPath $repoRoot
if ($Json) {
    & $python -m autoapply check-ready --json
}
else {
    & $python -m autoapply check-ready
}
exit $LASTEXITCODE
