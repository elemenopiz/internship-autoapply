$ErrorActionPreference = 'Stop'
$secure = Read-Host 'OpenAI API key (input is hidden)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    [Environment]::SetEnvironmentVariable('OPENAI_API_KEY', $key, 'User')
    Write-Output 'Saved OPENAI_API_KEY for your Windows user account. Restart the applier before running it.'
}
finally {
    if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
