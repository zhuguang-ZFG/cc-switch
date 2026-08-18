[CmdletBinding()]
param(
    [string]$SourceRoot,
    [string]$DestinationRoot,
    [string]$BackupRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $SourceRoot) {
    $SourceRoot = Join-Path $PSScriptRoot "omp-agents"
}
if (-not $DestinationRoot) {
    if (-not $env:USERPROFILE) {
        throw "USERPROFILE is required when DestinationRoot is omitted."
    }
    $DestinationRoot = Join-Path $env:USERPROFILE ".omp\agent\agents"
}
if (-not $BackupRoot) {
    $agentRoot = Split-Path -Parent $DestinationRoot
    $BackupRoot = Join-Path $agentRoot "agent-backups"
}

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath(
        [Environment]::ExpandEnvironmentVariables($Path)
    )
}

$sourceRootPath = Get-NormalizedPath $SourceRoot
$destinationRootPath = Get-NormalizedPath $DestinationRoot
$backupRootPath = Get-NormalizedPath $BackupRoot
$agentNames = @("hutuji-worker", "dsv4pro-worker")

New-Item -ItemType Directory -Force -Path $destinationRootPath | Out-Null
New-Item -ItemType Directory -Force -Path $backupRootPath | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDirectory = Join-Path $backupRootPath "hutuji-workers-$stamp-$PID"
New-Item -ItemType Directory -Path $backupDirectory | Out-Null

$records = @()
foreach ($name in $agentNames) {
    $source = Join-Path $sourceRootPath "$name.md"
    $destination = Join-Path $destinationRootPath "$name.md"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Worker template does not exist: $source"
    }
    $content = [System.IO.File]::ReadAllText($source)
    if ($content -notmatch "(?m)^name: $([regex]::Escape($name))$") {
        throw "Worker template name mismatch: $name"
    }
    if ($content -notmatch '(?m)^model:\r?\n  - "@task"$') {
        throw "Worker template must inherit @task: $name"
    }
    if ($content -match '(?i)deepseek|gpt-5\.6-luna|zg-newapi/') {
        throw "Worker template contains a concrete or stale model: $name"
    }

    $hadDestination = Test-Path -LiteralPath $destination -PathType Leaf
    $previousCopy = Join-Path $backupDirectory "previous-$name.md"
    $absenceMarker = Join-Path $backupDirectory "$name.destination.absent"
    if ($hadDestination) {
        Copy-Item -LiteralPath $destination -Destination $previousCopy
        $previousHash = (Get-FileHash -LiteralPath $previousCopy -Algorithm SHA256).Hash
        if ($previousHash -ne (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash) {
            throw "Worker backup hash mismatch: $name"
        }
    } else {
        Set-Content -LiteralPath $absenceMarker -Value "Destination did not exist." -Encoding Ascii
        $previousHash = $null
    }
    $records += [pscustomobject]@{
        Name = $name
        Source = $source
        Destination = $destination
        HadDestination = $hadDestination
        PreviousCopy = $previousCopy
        PreviousHash = $previousHash
        SourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        Staging = "$destination.deploy-$PID.tmp"
        AtomicBackup = Join-Path $backupDirectory "atomic-$name.md"
    }
}

try {
    foreach ($record in $records) {
        Copy-Item -LiteralPath $record.Source -Destination $record.Staging
        if ((Get-FileHash -LiteralPath $record.Staging -Algorithm SHA256).Hash -ne $record.SourceHash) {
            throw "Worker staging hash mismatch: $($record.Name)"
        }
        if ($record.HadDestination) {
            [System.IO.File]::Replace(
                $record.Staging,
                $record.Destination,
                $record.AtomicBackup,
                $true
            )
        } else {
            [System.IO.File]::Move($record.Staging, $record.Destination)
        }
        if ((Get-FileHash -LiteralPath $record.Destination -Algorithm SHA256).Hash -ne $record.SourceHash) {
            throw "Worker deployed hash mismatch: $($record.Name)"
        }
    }

    $manifest = [ordered]@{
        deployedAt = (Get-Date).ToString("o")
        restartPerformed = $false
        workers = @($records | ForEach-Object {
            [ordered]@{
                name = $_.Name
                source = $_.Source
                destination = $_.Destination
                sourceSha256 = $_.SourceHash
                destinationSha256 = (Get-FileHash -LiteralPath $_.Destination -Algorithm SHA256).Hash
                destinationExisted = $_.HadDestination
                previousSha256 = $_.PreviousHash
            }
        })
    }
    $manifestPath = Join-Path $backupDirectory "deployment.json"
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding Ascii
    [pscustomobject]@{
        BackupDirectory = $backupDirectory
        Manifest = $manifestPath
        RestartPerformed = $false
    }
} catch {
    foreach ($record in $records) {
        if ($record.HadDestination -and (Test-Path -LiteralPath $record.PreviousCopy -PathType Leaf)) {
            Copy-Item -LiteralPath $record.PreviousCopy -Destination $record.Staging -Force
            if (Test-Path -LiteralPath $record.Destination -PathType Leaf) {
                [System.IO.File]::Replace($record.Staging, $record.Destination, $null, $true)
            } else {
                [System.IO.File]::Move($record.Staging, $record.Destination)
            }
        } elseif (-not $record.HadDestination -and (Test-Path -LiteralPath $record.Destination)) {
            Remove-Item -LiteralPath $record.Destination -Force
        }
    }
    throw
} finally {
    foreach ($record in $records) {
        if (Test-Path -LiteralPath $record.Staging) {
            Remove-Item -LiteralPath $record.Staging -Force
        }
    }
}
