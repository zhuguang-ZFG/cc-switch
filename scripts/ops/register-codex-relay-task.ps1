[CmdletBinding()]
param(
    [string]$PythonPath,
    [ValidateRange(1, 65535)]
    [int]$Port = 15999,
    [string]$TaskName = "OMP Codex Relay",
    [string]$Upstream = "https://api.zzzcoding.org/responses",
    [ValidatePattern('^[A-Za-z0-9_]+$')]
    [string]$SecretName = "zzzcoding_codex_key",
    [string]$LogFile
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Wait-RelayPortReleased {
    param([int]$LocalPort)

    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Port $LocalPort is still owned after waiting 10 seconds"
}

function Get-RelayListenerOwner {
    param(
        [int]$LocalPort,
        [string]$ExpectedPython,
        [string]$ExpectedScript
    )

    $connection = Get-NetTCPConnection `
        -State Listen `
        -LocalAddress "127.0.0.1" `
        -LocalPort $LocalPort `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $connection) {
        return $null
    }

    $owner = Get-CimInstance `
        -ClassName Win32_Process `
        -Filter "ProcessId = $($connection.OwningProcess)" `
        -ErrorAction SilentlyContinue
    if (-not $owner -or -not $owner.ExecutablePath -or -not $owner.CommandLine) {
        return $null
    }

    $pythonMatches = [string]::Equals(
        [IO.Path]::GetFullPath($owner.ExecutablePath),
        [IO.Path]::GetFullPath($ExpectedPython),
        [StringComparison]::OrdinalIgnoreCase
    )
    $scriptPattern = '(?i)(?:^|\s)"?' + [regex]::Escape($ExpectedScript) + '"?(?:\s|$)'
    $portPattern = '(?i)(?:^|\s)--port\s+' + [regex]::Escape([string]$LocalPort) + '(?:\s|$)'
    if ($pythonMatches -and $owner.CommandLine -match $scriptPattern -and $owner.CommandLine -match $portPattern) {
        return $owner
    }
    return $null
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sourceRelay = (Resolve-Path (Join-Path $PSScriptRoot "codex-relay.py")).Path
$sourceTemplate = (Resolve-Path (Join-Path $PSScriptRoot "codex-relay-template.json")).Path

$upstreamUri = [Uri]$Upstream
if ($upstreamUri.Scheme -ne "https" -or -not $upstreamUri.Host -or $upstreamUri.UserInfo) {
    throw "Upstream must be an HTTPS URL without userinfo"
}
if ($Upstream.Contains('"') -or ($LogFile -and $LogFile.Contains('"'))) {
    throw "Upstream and LogFile must not contain quote characters"
}

if (-not $PythonPath) {
    $scoopPythonw = Join-Path $env:USERPROFILE "scoop\apps\python313\current\pythonw.exe"
    if (Test-Path -LiteralPath $scoopPythonw) {
        $PythonPath = $scoopPythonw
    } else {
        $PythonPath = (Get-Command pythonw -ErrorAction Stop).Source
    }
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
if ([IO.Path]::GetFileName($PythonPath) -ine "pythonw.exe") {
    throw "PythonPath must point to pythonw.exe so the interactive task stays windowless"
}
$validationPython = Join-Path (Split-Path -Parent $PythonPath) "python.exe"
$validationPython = (Resolve-Path -LiteralPath $validationPython).Path

if (-not $LogFile) {
    $defaultLogName = if ($Port -eq 15999) { "codex-relay.log" } else { "codex-relay-$Port.log" }
    $LogFile = Join-Path $env:USERPROFILE ".omp\guardian\$defaultLogName"
}

$validationCache = Join-Path ([IO.Path]::GetTempPath()) "codex-relay-pycache-$PID"
$previousPycachePrefix = $env:PYTHONPYCACHEPREFIX
try {
    $env:PYTHONPYCACHEPREFIX = $validationCache
    & $validationPython -m py_compile $sourceRelay
    if ($LASTEXITCODE -ne 0) {
        throw "codex-relay.py failed Python syntax validation"
    }
} finally {
    if ([string]::IsNullOrEmpty($previousPycachePrefix)) {
        Remove-Item Env:PYTHONPYCACHEPREFIX -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPYCACHEPREFIX = $previousPycachePrefix
    }
    Remove-Item -LiteralPath $validationCache -Recurse -Force -ErrorAction SilentlyContinue
}
Get-Content -Raw -LiteralPath $sourceTemplate | ConvertFrom-Json | Out-Null

$guardianDir = Join-Path $env:USERPROFILE ".omp\guardian"
$runtimeDir = Join-Path $guardianDir "codex-relay-$Port"
$relayScript = Join-Path $runtimeDir "codex-relay.py"
$runtimeTemplate = Join-Path $runtimeDir "codex-relay-template.json"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $guardianDir "task-backups\codex-relay-register-$Port-$timestamp-$PID"
$stagingDir = Join-Path $guardianDir ".codex-relay-staging-$Port-$PID"

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$existingTaskXml = if ($existingTask) { Export-ScheduledTask -TaskName $TaskName } else { $null }
$wasRunning = [bool]($existingTask -and $existingTask.State -eq "Running")
$relayExisted = Test-Path -LiteralPath $relayScript
$templateExisted = Test-Path -LiteralPath $runtimeTemplate
$taskReplaced = $false
$runtimeDeployed = $false
$owner = $null

try {
    New-Item -ItemType Directory -Path $runtimeDir, $backupDir, $stagingDir -Force | Out-Null
    $stagedRelay = Join-Path $stagingDir "codex-relay.py"
    $stagedTemplate = Join-Path $stagingDir "codex-relay-template.json"
    Copy-Item -LiteralPath $sourceRelay -Destination $stagedRelay -Force
    Copy-Item -LiteralPath $sourceTemplate -Destination $stagedTemplate -Force

    if ($existingTaskXml) {
        Set-Content -LiteralPath (Join-Path $backupDir "task.xml") -Value $existingTaskXml -Encoding Unicode
    }
    if ($relayExisted) {
        Copy-Item -LiteralPath $relayScript -Destination (Join-Path $backupDir "codex-relay.py") -Force
    }
    if ($templateExisted) {
        Copy-Item -LiteralPath $runtimeTemplate -Destination (Join-Path $backupDir "codex-relay-template.json") -Force
    }

    if ($wasRunning) {
        Stop-ScheduledTask -TaskName $TaskName
    }
    Wait-RelayPortReleased -LocalPort $Port

    Copy-Item -LiteralPath $stagedRelay -Destination $relayScript -Force
    Copy-Item -LiteralPath $stagedTemplate -Destination $runtimeTemplate -Force
    $runtimeDeployed = $true

    $arguments = '"{0}" --port {1} --log-file "{2}" --upstream "{3}" --secret-name "{4}"' -f `
        $relayScript, $Port, $LogFile, $Upstream, $SecretName
    $action = New-ScheduledTaskAction `
        -Execute $PythonPath `
        -Argument $arguments `
        -WorkingDirectory $repoRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal `
        -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
        -LogonType Interactive `
        -RunLevel Limited
    $definition = New-ScheduledTask `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Local Codex-compatible relay on 127.0.0.1:$Port"

    Register-ScheduledTask -TaskName $TaskName -InputObject $definition -Force | Out-Null
    $taskReplaced = $true
    Start-ScheduledTask -TaskName $TaskName

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        $task = Get-ScheduledTask -TaskName $TaskName
        if ($task.State -eq "Running") {
            $owner = Get-RelayListenerOwner `
                -LocalPort $Port `
                -ExpectedPython $PythonPath `
                -ExpectedScript $relayScript
            if ($owner) {
                break
            }
        }
    }
    if (-not $owner) {
        throw "$TaskName did not own 127.0.0.1:$Port within 10 seconds"
    }
} catch {
    $failure = $_
    try {
        $currentTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($currentTask -and $currentTask.State -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName
        }
        Wait-RelayPortReleased -LocalPort $Port

        if ($runtimeDeployed) {
            if ($relayExisted) {
                Copy-Item -LiteralPath (Join-Path $backupDir "codex-relay.py") -Destination $relayScript -Force
            } else {
                Remove-Item -LiteralPath $relayScript -Force -ErrorAction SilentlyContinue
            }
            if ($templateExisted) {
                Copy-Item -LiteralPath (Join-Path $backupDir "codex-relay-template.json") -Destination $runtimeTemplate -Force
            } else {
                Remove-Item -LiteralPath $runtimeTemplate -Force -ErrorAction SilentlyContinue
            }
        }

        if ($taskReplaced) {
            if ($existingTaskXml) {
                Register-ScheduledTask -TaskName $TaskName -Xml $existingTaskXml -Force | Out-Null
            } else {
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            }
        }
        if ($wasRunning -and $existingTaskXml) {
            Start-ScheduledTask -TaskName $TaskName
        }
    } catch {
        Write-Warning "Rollback for $TaskName failed: $($_.Exception.Message)"
    }
    throw $failure
} finally {
    Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue
}

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[PSCustomObject]@{
    TaskName = $TaskName
    State = $task.State
    LastTaskResult = $info.LastTaskResult
    Port = $Port
    OwnerPid = $owner.ProcessId
    RuntimeDirectory = $runtimeDir
    BackupDirectory = $backupDir
    RestartCount = $task.Settings.RestartCount
    RestartInterval = $task.Settings.RestartInterval
    MultipleInstances = $task.Settings.MultipleInstances
}
