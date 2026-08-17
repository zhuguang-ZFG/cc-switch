[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$TaskName = "OMP Sol Semantic Monitor"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sourceNames = @(
    "monitor_sol_semantic.py",
    "verify_zzzcoding_sol_primary.py",
    "update_zzzcoding_sol_primary.py",
    "newapi-local-smoke.py"
)

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
    throw "PythonPath must point to pythonw.exe"
}
$validationPython = (Resolve-Path -LiteralPath (
    Join-Path (Split-Path -Parent $PythonPath) "python.exe"
)).Path

$guardianDir = Join-Path $env:USERPROFILE ".omp\guardian"
$runtimeDir = Join-Path $guardianDir "sol-semantic-monitor-runtime"
$outputDir = Join-Path $guardianDir "sol-semantic-monitor"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $guardianDir "task-backups\sol-monitor-$timestamp-$PID"
$stagingDir = Join-Path $guardianDir ".sol-monitor-staging-$PID"
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$existingTaskXml = if ($existingTask) { Export-ScheduledTask -TaskName $TaskName } else { $null }
$taskReplaced = $false
$existingFiles = @{}
$deployedNames = [System.Collections.Generic.List[string]]::new()

try {
    New-Item -ItemType Directory -Path $runtimeDir, $outputDir, $backupDir, $stagingDir -Force | Out-Null
    if ($existingTaskXml) {
        Set-Content -LiteralPath (Join-Path $backupDir "task.xml") -Value $existingTaskXml -Encoding Unicode
    }

    foreach ($name in $sourceNames) {
        $source = (Resolve-Path (Join-Path $PSScriptRoot $name)).Path
        $staged = Join-Path $stagingDir $name
        $runtime = Join-Path $runtimeDir $name
        Copy-Item -LiteralPath $source -Destination $staged -Force
        & $validationPython -m py_compile $staged
        if ($LASTEXITCODE -ne 0) {
            throw "$name failed Python syntax validation"
        }
        $existingFiles[$name] = Test-Path -LiteralPath $runtime
        if ($existingFiles[$name]) {
            Copy-Item -LiteralPath $runtime -Destination (Join-Path $backupDir $name) -Force
        }
        Copy-Item -LiteralPath $staged -Destination $runtime -Force
        $deployedNames.Add($name)
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash
        $runtimeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $runtime).Hash
        if ($sourceHash -ne $runtimeHash) {
            throw "$name runtime hash mismatch"
        }
    }
    $monitorScript = Join-Path $runtimeDir "monitor_sol_semantic.py"
    $arguments = '"{0}" --output-dir "{1}" --timeout 120' -f $monitorScript, $outputDir
    $action = New-ScheduledTaskAction `
        -Execute $PythonPath `
        -Argument $arguments `
        -WorkingDirectory $runtimeDir
    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 30)
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
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
        -Description "Read-only Sol semantic and TTFT monitor every 30 minutes"

    Register-ScheduledTask -TaskName $TaskName -InputObject $definition -Force | Out-Null
    $taskReplaced = $true
    $beforeRun = (Get-ScheduledTaskInfo -TaskName $TaskName).LastRunTime
    Start-ScheduledTask -TaskName $TaskName
    $completed = $false
    for ($attempt = 0; $attempt -lt 180; $attempt++) {
        Start-Sleep -Seconds 1
        $task = Get-ScheduledTask -TaskName $TaskName
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        if ($task.State -ne "Running" -and $info.LastRunTime -gt $beforeRun) {
            $completed = $true
            break
        }
    }
    if (-not $completed) {
        throw "$TaskName did not finish its validation run within 180 seconds"
    }
    if ($info.LastTaskResult -ne 0) {
        throw "$TaskName validation result was $($info.LastTaskResult)"
    }
    $resultPath = Join-Path $outputDir "results.jsonl"
    $lastResult = Get-Content -LiteralPath $resultPath -Tail 1 | ConvertFrom-Json
    if (-not $lastResult.ok -or $lastResult.channel_id -ne 92) {
        throw "$TaskName first semantic result did not validate ch92"
    }
} catch {
    $failure = $_
    try {
        if ($taskReplaced) {
            $current = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($current -and $current.State -eq "Running") {
                Stop-ScheduledTask -TaskName $TaskName
            }
            if ($existingTaskXml) {
                Register-ScheduledTask -TaskName $TaskName -Xml $existingTaskXml -Force | Out-Null
            } else {
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            }
        }
        if ($deployedNames.Count -gt 0) {
            foreach ($name in $deployedNames) {
                $runtime = Join-Path $runtimeDir $name
                if ($existingFiles[$name]) {
                    Copy-Item -LiteralPath (Join-Path $backupDir $name) -Destination $runtime -Force
                } else {
                    Remove-Item -LiteralPath $runtime -Force -ErrorAction SilentlyContinue
                }
            }
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
    RepetitionInterval = $task.Triggers[0].Repetition.Interval
    ExecutionTimeLimit = $task.Settings.ExecutionTimeLimit
    MultipleInstances = $task.Settings.MultipleInstances
    RestartCount = $task.Settings.RestartCount
    RuntimeDirectory = $runtimeDir
    OutputDirectory = $outputDir
    BackupDirectory = $backupDir
}
