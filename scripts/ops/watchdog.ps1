# Guardian watchdog: check heartbeat.json freshness; if stale, kill the
# wedged Guardian process so omp hub restart=on-failure brings up a new one.
# Background: Guardian is single-threaded; a wedged network call leaves the
# process alive but heartbeat stale. hub on-failure only fires on exit.
# Pitfall 1: heartbeat written by Guardian.run() each loop (ts + pid).
# Pitfall 2: killing the process lets hub restart it; this script does not.
# Usage: run persistently (hub start or Startup), checks every 30s.
$hb = "C:\Users\zhugu\.omp\guardian\heartbeat.json"
$staleSec = 180
$log = "C:\Users\zhugu\.omp\guardian\watchdog.log"

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $log -Value $line -Encoding UTF8
}

Write-Log "Guardian watchdog started"
while ($true) {
    $stale = $false
    try {
        if (Test-Path $hb) {
            $data = Get-Content -Path $hb -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($data.ts -is [string]) { $ts = [datetime]::Parse($data.ts) } else { $ts = $data.ts }
            $age = (Get-Date) - $ts
            if ($age.TotalSeconds -gt $staleSec) { $stale = $true }
        } else {
            $stale = $true
        }
    } catch {
        Write-Log "Heartbeat read/parse error: $_"
        $stale = $true
    }

    if ($stale) {
        $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'guardian\.py' }
        if ($procs) {
            foreach ($p in $procs) {
                Write-Log "Heartbeat stale, killing Guardian pid=$($p.ProcessId)"
                Stop-Process -Id $p.ProcessId -Force
            }
        } else {
            Write-Log "Heartbeat stale but no guardian.py process found"
        }
        Start-Sleep -Seconds 15
    }
    Start-Sleep -Seconds 30
}
