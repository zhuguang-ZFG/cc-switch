[CmdletBinding()]
param(
    [string]$SourcePath,
    [string]$DestinationPath,
    [string]$BackupRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $SourcePath) {
    $SourcePath = Join-Path $PSScriptRoot "omp-global-compaction-model.js"
}
if (-not $DestinationPath) {
    if (-not $env:USERPROFILE) {
        throw "USERPROFILE is required when DestinationPath is omitted."
    }
    $DestinationPath = Join-Path $env:USERPROFILE ".omp\agent\extensions\omp-global-compaction-model.js"
}

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath(
        [Environment]::ExpandEnvironmentVariables($Path)
    )
}

$source = Get-NormalizedPath $SourcePath
$destination = Get-NormalizedPath $DestinationPath
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Source extension does not exist: $source"
}

$destinationDirectory = Split-Path -Parent $destination
if (-not $destinationDirectory) {
    throw "Destination must include a parent directory: $destination"
}

if (-not $BackupRoot) {
    $agentDirectory = Split-Path -Parent $destinationDirectory
    $BackupRoot = Join-Path $agentDirectory "extension-backups"
}
$backupRootPath = Get-NormalizedPath $BackupRoot

New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $backupRootPath | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDirectory = Join-Path $backupRootPath "omp-global-compaction-model-$stamp-$PID"
New-Item -ItemType Directory -Path $backupDirectory | Out-Null

$hadDestination = Test-Path -LiteralPath $destination -PathType Leaf
$previousCopy = Join-Path $backupDirectory "previous.js"
$absenceMarker = Join-Path $backupDirectory "destination.absent"
$atomicBackup = Join-Path $backupDirectory "atomic-replace-backup.js"
$stagingPath = "$destination.deploy-$PID.tmp"
$rollbackStagingPath = "$destination.rollback-$PID.tmp"

if ($hadDestination) {
    Copy-Item -LiteralPath $destination -Destination $previousCopy
    $previousHash = (Get-FileHash -LiteralPath $previousCopy -Algorithm SHA256).Hash
} else {
    Set-Content -LiteralPath $absenceMarker -Value "Destination did not exist before deployment." -Encoding Ascii
    $previousHash = $null
}

try {
    Copy-Item -LiteralPath $source -Destination $stagingPath
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $stagingHash = (Get-FileHash -LiteralPath $stagingPath -Algorithm SHA256).Hash
    if ($sourceHash -ne $stagingHash) {
        throw "Staged extension SHA-256 mismatch."
    }

    if ($hadDestination) {
        [System.IO.File]::Replace($stagingPath, $destination, $atomicBackup, $true)
    } else {
        [System.IO.File]::Move($stagingPath, $destination)
    }

    $destinationHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    if ($sourceHash -ne $destinationHash) {
        throw "Deployed extension SHA-256 mismatch."
    }

    $manifest = [ordered]@{
        deployedAt = (Get-Date).ToString("o")
        source = $source
        destination = $destination
        sourceSha256 = $sourceHash
        destinationSha256 = $destinationHash
        destinationExisted = $hadDestination
        previousSha256 = $previousHash
        restartPerformed = $false
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupDirectory "deployment.json") -Encoding Ascii

    [pscustomobject]@{
        Destination = $destination
        Sha256 = $destinationHash
        BackupDirectory = $backupDirectory
        PreviousDestinationExisted = $hadDestination
        RestartPerformed = $false
    }
} catch {
    try {
        if ($hadDestination -and (Test-Path -LiteralPath $previousCopy -PathType Leaf)) {
            Copy-Item -LiteralPath $previousCopy -Destination $rollbackStagingPath
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                [System.IO.File]::Replace($rollbackStagingPath, $destination, $null, $true)
            } else {
                [System.IO.File]::Move($rollbackStagingPath, $destination)
            }
        } elseif (-not $hadDestination -and (Test-Path -LiteralPath $destination -PathType Leaf)) {
            Remove-Item -LiteralPath $destination -Force
        }
    } finally {
        if (Test-Path -LiteralPath $stagingPath) {
            Remove-Item -LiteralPath $stagingPath -Force
        }
        if (Test-Path -LiteralPath $rollbackStagingPath) {
            Remove-Item -LiteralPath $rollbackStagingPath -Force
        }
    }
    throw
}
