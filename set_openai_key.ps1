<#
.SYNOPSIS
  Saves your OpenAI API key as the Windows user environment variable OPENAI_API_KEY.

.DESCRIPTION
  The key is typed into a hidden prompt: it is never echoed, never written to a file and never passed on a
  command line. It is stored in your Windows user environment. The applier reads it from the environment at
  start-up and never copies it into data\config.json or the Windows credential store, so running this script
  again always replaces the key the applier uses.

  Usage:  powershell -ExecutionPolicy Bypass -File .\set_openai_key.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$secure = Read-Host -Prompt 'Paste your OpenAI API key (input is hidden)' -AsSecureString
$bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
}
finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$plain = "$plain".Trim().Trim('"').Trim("'")
if ([string]::IsNullOrWhiteSpace($plain)) {
    Write-Error 'No key was entered. Nothing was changed.'
    exit 1
}
if (-not $plain.StartsWith('sk-')) {
    Write-Warning 'That does not look like an OpenAI key (they normally start with "sk-"). Saving it anyway.'
}

[Environment]::SetEnvironmentVariable('OPENAI_API_KEY', $plain, 'User')
[Environment]::SetEnvironmentVariable('OPENAI_API_KEY', $plain, 'Process')
$length = $plain.Length
$plain = $null

Write-Host "OPENAI_API_KEY saved for your Windows user account ($length characters)."
Write-Host 'Run launch.ps1 (or open a new PowerShell window) so the applier picks it up.'
