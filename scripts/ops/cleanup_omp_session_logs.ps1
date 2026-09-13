# cleanup_omp_session_logs.ps1 — bound OMP session log growth.
#
# Background: 2026-09-13 a single 10.bash.log reached 30GB (runaway command
# output), nearly filling C: and risking the whole OMP/Guardian chain. OMP
# has no log rotation, so this is the guard rail.
#
# Scope (deliberately narrow):
#   - only ~\.omp\agent\sessions\**\*.bash.log / *.async.log
#   - only files > $MaxSizeMB
#   - only files not modified for $MinAgeHours (protects an active session's
#     currently-appended log)
#   - never touches *.jsonl / *.sqlite* (OMP resume depends on them)
# Dry-run with -WhatIf; scheduled weekly via schtasks.
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$SessionsRoot = "$env:USERPROFILE\.omp\agent\sessions",
    [int]$MaxSizeMB = 2048,
    [int]$MinAgeHours = 24
)

$cutoff = (Get-Date).AddHours(-$MinAgeHours)
$total = 0L
$removed = 0

Get-ChildItem -Path $SessionsRoot -Recurse -File -Include '*.bash.log', '*.async.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.Length -gt ($MaxSizeMB * 1MB) -and $_.LastWriteTime -lt $cutoff } |
    ForEach-Object {
        $total += $_.Length
        $removed++
        if ($PSCmdlet.ShouldProcess("$($_.FullName) ($([math]::Round($_.Length / 1GB, 2)) GB)", 'Remove')) {
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
        }
    }

Write-Output ("removed {0} files, freed {1} GB" -f $removed, [math]::Round($total / 1GB, 2))
