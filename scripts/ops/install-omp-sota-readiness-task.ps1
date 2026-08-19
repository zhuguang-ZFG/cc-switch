[CmdletBinding()]
param(
    [string]$TaskName = "OMP SOTA Readiness Refresh",
    [int]$Minutes = 10
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($Minutes -lt 5) { throw "Minutes must be at least 5." }

$refreshScript = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "refresh-omp-sota-readiness.ps1")).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $refreshScript
)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutes)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description (
        "Bounded health refresh for the isolated OMP SOTA NewAPI route."
    ) -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
[pscustomobject]@{
    TaskName = $task.TaskName
    State = $task.State
    IntervalMinutes = $Minutes
    RefreshScript = $refreshScript
}
