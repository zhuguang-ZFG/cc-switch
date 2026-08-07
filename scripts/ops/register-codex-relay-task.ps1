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

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sourceRelay = (Resolve-Path (Join-Path $PSScriptRoot "codex-relay.py")).Path
$sourceTemplate = (Resolve-Path (Join-Path $PSScriptRoot "codex-relay-template.json")).Path
$runtimeDir = Join-Path $env:USERPROFILE ".omp\guardian"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$relayScript = Join-Path $runtimeDir "codex-relay.py"
$runtimeTemplate = Join-Path $runtimeDir "codex-relay-template.json"
Copy-Item -LiteralPath $sourceRelay -Destination $relayScript -Force
Copy-Item -LiteralPath $sourceTemplate -Destination $runtimeTemplate -Force
if (-not $PythonPath) {
    $scoopPythonw = Join-Path $env:USERPROFILE "scoop\apps\python313\current\pythonw.exe"
    if (Test-Path -LiteralPath $scoopPythonw) {
        $PythonPath = $scoopPythonw
    } else {
        $PythonPath = (Get-Command pythonw -ErrorAction Stop).Source
    }
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
if (-not $LogFile) {
    $LogFile = Join-Path $env:USERPROFILE ".omp\guardian\codex-relay-$Port.log"
}
$upstreamUri = [Uri]$Upstream
if ($upstreamUri.Scheme -ne "https" -or -not $upstreamUri.Host -or $upstreamUri.UserInfo) {
    throw "Upstream must be an HTTPS URL without userinfo"
}
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$arguments = '"{0}" --port {1} --log-file "{2}" --upstream "{3}" --secret-name "{4}"' -f `
    $relayScript, $Port, $LogFile, $Upstream, $SecretName
$action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument $arguments `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal `
    -UserId $user `
    -LogonType Interactive `
    -RunLevel Limited
$definition = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Local Codex-compatible relay on 127.0.0.1:$Port"

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask -and $existingTask.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName
}
Register-ScheduledTask -TaskName $TaskName -InputObject $definition -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        $ready = $true
        break
    }
}
if (-not $ready) {
    throw "$TaskName did not listen on 127.0.0.1:$Port within 10 seconds"
}

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[PSCustomObject]@{
    TaskName = $TaskName
    State = $task.State
    LastTaskResult = $info.LastTaskResult
    Port = $Port
    RestartCount = $task.Settings.RestartCount
    RestartInterval = $task.Settings.RestartInterval
    MultipleInstances = $task.Settings.MultipleInstances
}
