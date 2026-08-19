[CmdletBinding()]
param(
    [string]$SourcePath,
    [string]$DestinationPath,
    [string]$ProbeSourcePath,
    [string]$ProbeDestinationPath,
    [string]$LegacyProbeDestinationPath,
    [string]$BackupRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $SourcePath) { $SourcePath = Join-Path $PSScriptRoot "omp-model-routing-observability.js" }
if (-not $ProbeSourcePath) { $ProbeSourcePath = Join-Path (Split-Path -Parent $SourcePath) "omp-model-tool-canary-probe.js" }
if (-not $DestinationPath) {
    if (-not $env:USERPROFILE) { throw "USERPROFILE is required when DestinationPath is omitted." }
    $DestinationPath = Join-Path $env:USERPROFILE ".omp\agent\extensions\omp-model-routing-observability.js"
}
if (-not $ProbeDestinationPath) {
    $ProbeDestinationPath = Join-Path (Split-Path -Parent (Split-Path -Parent $DestinationPath)) "canary\omp-model-tool-canary-probe.js"
}
if (-not $LegacyProbeDestinationPath) {
    $LegacyProbeDestinationPath = Join-Path (Split-Path -Parent $DestinationPath) "omp-model-tool-canary-probe.js"
}

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Path))
}

function Restore-Artifact {
    param(
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][bool]$Existed,
        [Parameter(Mandatory = $true)][string]$Previous,
        [Parameter(Mandatory = $true)][string]$Staging
    )
    if ($Existed -and (Test-Path -LiteralPath $Previous -PathType Leaf)) {
        Copy-Item -LiteralPath $Previous -Destination $Staging
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            $restoreBackup = "$Staging.replaced"
            [System.IO.File]::Replace($Staging, $Destination, $restoreBackup, $true)
            if (Test-Path -LiteralPath $restoreBackup) { Remove-Item -LiteralPath $restoreBackup -Force }
        } else {
            [System.IO.File]::Move($Staging, $Destination)
        }
    } elseif (-not $Existed -and (Test-Path -LiteralPath $Destination -PathType Leaf)) {
        Remove-Item -LiteralPath $Destination -Force
    }
}

$source = Get-NormalizedPath $SourcePath
$probeSource = Get-NormalizedPath $ProbeSourcePath
$destination = Get-NormalizedPath $DestinationPath
$probeDestination = Get-NormalizedPath $ProbeDestinationPath
$legacyProbeDestination = Get-NormalizedPath $LegacyProbeDestinationPath
foreach ($requiredSource in @($source, $probeSource)) {
    if (-not (Test-Path -LiteralPath $requiredSource -PathType Leaf)) { throw "Source extension does not exist: $requiredSource" }
}
$destinationDirectory = Split-Path -Parent $destination
$probeDestinationDirectory = Split-Path -Parent $probeDestination
if (-not $destinationDirectory -or -not $probeDestinationDirectory) { throw "Each destination must include a parent directory." }
if (-not $BackupRoot) { $BackupRoot = Join-Path (Split-Path -Parent $destinationDirectory) "extension-backups" }
$backupRootPath = Get-NormalizedPath $BackupRoot
New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $probeDestinationDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $backupRootPath | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDirectory = Join-Path $backupRootPath "omp-model-routing-observability-$stamp-$PID"
New-Item -ItemType Directory -Path $backupDirectory | Out-Null
$hadDestination = Test-Path -LiteralPath $destination -PathType Leaf
$hadProbeDestination = Test-Path -LiteralPath $probeDestination -PathType Leaf
$hadLegacyProbeDestination = $legacyProbeDestination -ne $probeDestination -and (Test-Path -LiteralPath $legacyProbeDestination -PathType Leaf)
$previousCopy = Join-Path $backupDirectory "previous.js"
$previousProbeCopy = Join-Path $backupDirectory "previous-probe.js"
$legacyProbeCopy = Join-Path $backupDirectory "legacy-discovered-probe.js"
$absenceMarker = Join-Path $backupDirectory "destination.absent"
$probeAbsenceMarker = Join-Path $backupDirectory "probe-destination.absent"
$atomicBackup = Join-Path $backupDirectory "atomic-replace-backup.js"
$atomicProbeBackup = Join-Path $backupDirectory "atomic-replace-probe-backup.js"
$stagingPath = "$destination.deploy-$PID.tmp"
$probeStagingPath = "$probeDestination.deploy-$PID.tmp"
$rollbackStagingPath = "$destination.rollback-$PID.tmp"
$probeRollbackStagingPath = "$probeDestination.rollback-$PID.tmp"

if ($hadDestination) {
    Copy-Item -LiteralPath $destination -Destination $previousCopy
    $previousHash = (Get-FileHash -LiteralPath $previousCopy -Algorithm SHA256).Hash
} else {
    Set-Content -LiteralPath $absenceMarker -Value "Destination did not exist before deployment." -Encoding Ascii
    $previousHash = $null
}
if ($hadProbeDestination) {
    Copy-Item -LiteralPath $probeDestination -Destination $previousProbeCopy
    $previousProbeHash = (Get-FileHash -LiteralPath $previousProbeCopy -Algorithm SHA256).Hash
} else {
    Set-Content -LiteralPath $probeAbsenceMarker -Value "Probe destination did not exist before deployment." -Encoding Ascii
    $previousProbeHash = $null
}
if ($hadLegacyProbeDestination) {
    Copy-Item -LiteralPath $legacyProbeDestination -Destination $legacyProbeCopy
    $legacyProbeHash = (Get-FileHash -LiteralPath $legacyProbeCopy -Algorithm SHA256).Hash
} else {
    $legacyProbeHash = $null
}

try {
    Copy-Item -LiteralPath $source -Destination $stagingPath
    Copy-Item -LiteralPath $probeSource -Destination $probeStagingPath
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $probeSourceHash = (Get-FileHash -LiteralPath $probeSource -Algorithm SHA256).Hash
    if ($sourceHash -ne (Get-FileHash -LiteralPath $stagingPath -Algorithm SHA256).Hash) { throw "Staged main extension SHA-256 mismatch." }
    if ($probeSourceHash -ne (Get-FileHash -LiteralPath $probeStagingPath -Algorithm SHA256).Hash) { throw "Staged probe extension SHA-256 mismatch." }

    if ($hadDestination) { [System.IO.File]::Replace($stagingPath, $destination, $atomicBackup, $true) }
    else { [System.IO.File]::Move($stagingPath, $destination) }
    if ($hadProbeDestination) { [System.IO.File]::Replace($probeStagingPath, $probeDestination, $atomicProbeBackup, $true) }
    else { [System.IO.File]::Move($probeStagingPath, $probeDestination) }

    $destinationHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    $probeDestinationHash = (Get-FileHash -LiteralPath $probeDestination -Algorithm SHA256).Hash
    if ($sourceHash -ne $destinationHash) { throw "Deployed main extension SHA-256 mismatch." }
    if ($probeSourceHash -ne $probeDestinationHash) { throw "Deployed probe extension SHA-256 mismatch." }
    if ($hadLegacyProbeDestination) {
        Remove-Item -LiteralPath $legacyProbeDestination -Force
        if (Test-Path -LiteralPath $legacyProbeDestination) { throw "Legacy discovered probe removal failed." }
    }
    [ordered]@{
        deployedAt = (Get-Date).ToString("o")
        source = $source
        destination = $destination
        sourceSha256 = $sourceHash
        destinationSha256 = $destinationHash
        destinationExisted = $hadDestination
        previousSha256 = $previousHash
        probeSource = $probeSource
        probeDestination = $probeDestination
        probeSourceSha256 = $probeSourceHash
        probeDestinationSha256 = $probeDestinationHash
        probeDestinationExisted = $hadProbeDestination
        previousProbeSha256 = $previousProbeHash
        legacyProbeDestination = $legacyProbeDestination
        legacyProbeRemoved = $hadLegacyProbeDestination
        legacyProbeSha256 = $legacyProbeHash
        restartPerformed = $false
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupDirectory "deployment.json") -Encoding Ascii
    [pscustomobject]@{
        Destination = $destination
        Sha256 = $destinationHash
        ProbeDestination = $probeDestination
        ProbeSha256 = $probeDestinationHash
        BackupDirectory = $backupDirectory
        PreviousDestinationExisted = $hadDestination
        PreviousProbeDestinationExisted = $hadProbeDestination
        LegacyProbeRemoved = $hadLegacyProbeDestination
        RestartPerformed = $false
    }
} catch {
    try {
        Restore-Artifact -Destination $destination -Existed $hadDestination -Previous $previousCopy -Staging $rollbackStagingPath
        Restore-Artifact -Destination $probeDestination -Existed $hadProbeDestination -Previous $previousProbeCopy -Staging $probeRollbackStagingPath
        if ($hadLegacyProbeDestination -and -not (Test-Path -LiteralPath $legacyProbeDestination -PathType Leaf)) {
            Copy-Item -LiteralPath $legacyProbeCopy -Destination $legacyProbeDestination
        }
    } finally {
        foreach ($temporary in @($stagingPath, $probeStagingPath, $rollbackStagingPath, $probeRollbackStagingPath)) {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
        }
    }
    throw
}
