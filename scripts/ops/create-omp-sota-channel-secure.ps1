[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$pythonCandidates = @(
    (Join-Path $env:USERPROFILE "scoop\apps\python313\current\python.exe"),
    (Join-Path $env:USERPROFILE "scoop\apps\python312\current\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) { throw "A supported local Python runtime was not found." }

$secure = Read-Host -Prompt "Paste the isolated SOTA channel key (input hidden)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:CC_SWITCH_SOTA_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    & $python (Join-Path $PSScriptRoot "create_omp_sota_channel.py") --apply
    if ($LASTEXITCODE -ne 0) { throw "Isolated SOTA channel setup failed." }
} finally {
    if ($env:CC_SWITCH_SOTA_KEY) { $env:CC_SWITCH_SOTA_KEY = $null }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
