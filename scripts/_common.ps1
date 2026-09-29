# Shared helpers for set_openai_key.ps1 / check_ready.ps1 / launch.ps1.
# Dot-source this file (. "$PSScriptRoot\scripts\_common.ps1"); it has no side effects when loaded.

function Import-UserEnvVar {
    # A variable saved with set_openai_key.ps1 lives in the Windows *user* environment. Copy it into this
    # process so it works even in a PowerShell window that was opened before the key was saved.
    param([Parameter(Mandatory = $true)][string]$Name)
    $current = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrEmpty($current)) {
        $stored = [Environment]::GetEnvironmentVariable($Name, 'User')
        if (-not [string]::IsNullOrEmpty($stored)) {
            [Environment]::SetEnvironmentVariable($Name, $stored, 'Process')
        }
    }
}

function Test-PythonVersion {
    # True when the given interpreter is Python 3.11 or newer.
    param([Parameter(Mandatory = $true)][string]$Exe, [string[]]$PrefixArgs = @())
    try {
        $out = & $Exe @PrefixArgs -c "import sys; print(int(sys.version_info >= (3, 11)))" 2>$null
        return (($LASTEXITCODE -eq 0) -and ("$out".Trim() -eq '1'))
    }
    catch {
        return $false
    }
}

function Get-VenvPython {
    # Path of the project's virtual-environment interpreter (may not exist yet).
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    return (Join-Path $RepoRoot '.venv\Scripts\python.exe')
}

function Initialize-Venv {
    # Creates .venv with a Python >= 3.11 when it does not exist yet. Returns the venv interpreter path.
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $venvPython = Get-VenvPython -RepoRoot $RepoRoot
    if (Test-Path -LiteralPath $venvPython) { return $venvPython }

    $created = $false
    $candidates = @(
        @{ Exe = 'py'; PrefixArgs = @('-3.13') },
        @{ Exe = 'py'; PrefixArgs = @('-3.12') },
        @{ Exe = 'py'; PrefixArgs = @('-3.11') },
        @{ Exe = 'python'; PrefixArgs = @() },
        @{ Exe = 'python3'; PrefixArgs = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        if (-not (Test-PythonVersion -Exe $candidate.Exe -PrefixArgs $candidate.PrefixArgs)) { continue }
        $exe = $candidate.Exe
        $prefix = [string[]]$candidate.PrefixArgs
        Write-Host "Creating virtual environment with $exe $($prefix -join ' ')..."
        $venvDir = Join-Path $RepoRoot '.venv'
        & $exe @prefix -m venv $venvDir | Out-Null
        if ($LASTEXITCODE -eq 0) { $created = $true; break }
    }
    if (-not $created) {
        throw 'Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this script again.'
    }
    return $venvPython
}

function Install-Dependencies {
    # Installs the project and the Playwright browser into .venv, but only when pyproject.toml changed.
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$VenvPython)
    $marker = Join-Path $RepoRoot '.venv\.autoapply-installed'
    $pyproject = Join-Path $RepoRoot 'pyproject.toml'
    $hash = (Get-FileHash -LiteralPath $pyproject -Algorithm SHA256).Hash
    if ((Test-Path -LiteralPath $marker) -and ((Get-Content -LiteralPath $marker -Raw).Trim() -eq $hash)) { return }

    Write-Host 'Installing dependencies (first run takes a few minutes)...'
    & $VenvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
    & $VenvPython -m pip install --disable-pip-version-check -e $RepoRoot
    if ($LASTEXITCODE -ne 0) { throw 'Installing the project failed.' }
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Installing the Chromium browser for Playwright failed.' }
    Set-Content -LiteralPath $marker -Value $hash -Encoding ASCII
}
