# Guardian watchdog: check heartbeat.json freshness; if stale, kill the
# wedged Guardian process so omp hub restart=on-failure brings up a new one.
# Background: Guardian is single-threaded; a wedged network call leaves the
# process alive but heartbeat stale. hub on-failure only fires on exit.
# Pitfall 1: heartbeat written by Guardian.run() each loop (ts + pid).
# Pitfall 2: killing the process lets hub restart it; this script does not.
# Pitfall 3: only kill the PID recorded in the heartbeat, revalidated against
#            guardian.py command line. Never broad-kill by substring - a stale
#            heartbeat from a dead instance must not kill a healthy replacement.
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
    $hbPid = $null
    try {
        if (Test-Path $hb) {
            $data = Get-Content -Path $hb -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($data.ts -is [string]) { $ts = [datetime]::Parse($data.ts) } else { $ts = $data.ts }
            $age = (Get-Date) - $ts
            if ($age.TotalSeconds -gt $staleSec) { $stale = $true }
            # 记录心跳声明的 PID（无论新鲜与否），杀进程只认它
            if ($data.pid) { $hbPid = [int]$data.pid }
        } else {
            $stale = $true
        }
    } catch {
        Write-Log "Heartbeat read/parse error: $_"
        $stale = $true
    }

    if ($stale) {
        if ($hbPid -and $hbPid -gt 0) {
            # 精确验证：该 PID 存在且命令行仍匹配 guardian.py，才杀
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$hbPid" -ErrorAction SilentlyContinue
            if ($proc -and $proc.CommandLine -match 'guardian\.py') {
                Write-Log "Heartbeat stale (pid=$hbPid), killing Guardian"
                Stop-Process -Id $hbPid -Force
            } else {
                Write-Log "Heartbeat stale but pid=$hbPid not a guardian.py process (skip, $($proc.Count) match)"
            }
        } else {
            # 无 PID 依据（心跳缺失/解析失败）：不宽泛杀进程，避免误杀健康新实例
            Write-Log "Heartbeat stale but no valid pid in heartbeat; not broad-killing"
        }
        Start-Sleep -Seconds 15
    }
    Start-Sleep -Seconds 30
}
