[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$pythonCandidates = @(
    (Join-Path $env:USERPROFILE "scoop\apps\python313\current\python.exe"),
    (Join-Path $env:USERPROFILE "scoop\apps\python312\current\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) { throw "A supported local Python runtime was not found." }

& $python (Join-Path $PSScriptRoot "refresh_omp_sota_readiness.py")
exit $LASTEXITCODE
