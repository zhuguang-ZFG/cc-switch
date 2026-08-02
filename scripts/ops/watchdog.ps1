# Guardian watchdog: check heartbeat.json freshness; if stale, kill the
# wedged Guardian process so omp hub restart=on-failure brings up a new one.
# Background: Guardian is single-threaded; a wedged network call leaves the
# process alive but heartbeat stale. hub on-failure only fires on exit.
# Pitfall 1: heartbeat written by Guardian.run() each loop (ts + pid).
# Pitfall 2: killing the process lets hub restart it; this script does not.
# Pitfall 3: only kill the PID recorded in the heartbeat, revalidated that it
#            is a python process running guardian.py as a standalone arg.
#            Never broad-kill by substring - a stale heartbeat from a dead
#            instance must not kill a healthy replacement.
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
            # PID 必须是正整数（JSON 布尔/小数/负数均拒绝）
            if ($data.pid -is [int] -and $data.pid -gt 0) { $hbPid = $data.pid }
        } else {
            $stale = $true
        }
    } catch {
        Write-Log "Heartbeat read/parse error: $_"
        $stale = $true
    }

    if ($stale) {
        if ($hbPid -and $hbPid -gt 0) {
            # 精确验证：PID 存在、进程名是 python、命令行含独立的 guardian.py 参数
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$hbPid" -ErrorAction SilentlyContinue
            $cmd = if ($proc) { [string]$proc.CommandLine } else { "" }
            $isPython = $proc -and $proc.Name -match '^python(\.exe)?$'
            # guardian.py 必须是独立参数（被引号或空格包围），不是 notguardian.py / .bak 子串
            $isGuardian = $cmd -match '(?<![-\w.])guardian\.py(?![-\w.])'
            if ($isPython -and $isGuardian) {
                Write-Log "Heartbeat stale (pid=$hbPid), killing Guardian"
                try {
                    Stop-Process -Id $hbPid -Force -ErrorAction Stop
                    Write-Log "Killed pid=$hbPid"
                } catch {
                    # 进程可能已自行退出（竞态）或权限失败
                    Write-Log "Stop-Process pid=$hbPid failed: $_"
                }
            } else {
                Write-Log "Heartbeat stale but pid=$hbPid not a python/guardian.py process (skip; name=$($proc.Name), cmd match=$isGuardian)"
            }
        } else {
            # 无 PID 依据（心跳缺失/解析失败/pid 非正整数）：不宽泛杀进程
            Write-Log "Heartbeat stale but no valid pid in heartbeat; not broad-killing"
        }
        Start-Sleep -Seconds 15
    }
    Start-Sleep -Seconds 30
}
