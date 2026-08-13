# Guardian watchdog: check heartbeat.json freshness; if stale, kill the
# wedged Guardian process and restart it through the canonical scheduled task.
# Background: Guardian is single-threaded; a wedged network call leaves the
# process alive but heartbeat stale.
# Pitfall 1: heartbeat written by Guardian.run() each loop (ts + pid).
# Pitfall 2: restart attempts are rate-limited to prevent crash loops.
# Pitfall 3: only kill the PID recorded in the heartbeat, revalidated that it
#            is a python/pythonw process running guardian.py as a standalone arg.
#            Never broad-kill by substring - a stale heartbeat from a dead
#            instance must not kill a healthy replacement.
# Usage: run persistently from the NewAPI Guardian Watchdog task, checks every 30s.
$hb = "C:\Users\zhugu\.omp\guardian\heartbeat.json"
$staleSec = 180
$log = "C:\Users\zhugu\.omp\guardian\watchdog.log"
$guardianTask = "NewAPI Guardian"
$restartBackoffSec = 300
$script:lastRestartAttempt = [datetime]::MinValue
$script:lastSupRestartAttempt = [datetime]::MinValue

function Write-Log($msg) {
    # 轮转：超 1MB 时把当前日志改名 .old 再续写，防无限增长（2026-08-08 加入）
    if (Test-Path $log) {
        $len = (Get-Item $log).Length
        if ($len -gt 1MB) {
            $old = "$log.old"
            Remove-Item $old -ErrorAction SilentlyContinue
            Move-Item $log $old -ErrorAction SilentlyContinue
        }
    }
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function Get-HeartbeatPid($value) {
    # 2026-08-11 评审 P3-18：PID 必须是正整数，但 `-is [int]` 过严。
    # guardian 写入的是 int，JSON 反序列化在 PS7 可能得到 [double]、在
    # PS5.1 可能得到 [decimal]（如 12345.0）——原判定会把合法心跳当"无
    # 有效 pid"，卡死实例永远不会被拉起。放宽到任意数值型 + 整数校验，
    # 仍拒绝 null / 字符串 / 布尔 / 小数 / 非正值 / 超 int 范围。
    $isNumeric = $value -is [int] -or $value -is [long] -or $value -is [double] -or $value -is [decimal]
    if (-not $isNumeric) { return $null }
    $asDouble = [double]$value
    if ($asDouble -ne [math]::Floor($asDouble)) { return $null }
    if ($asDouble -le 0 -or $asDouble -gt [int]::MaxValue) { return $null }
    return [int]$asDouble
}

function Start-GuardianRecovery($reason) {
    $sinceLastAttempt = (Get-Date) - $script:lastRestartAttempt
    if ($sinceLastAttempt.TotalSeconds -lt $restartBackoffSec) {
        Write-Log "Guardian restart suppressed by backoff (reason=$reason)"
        return
    }

    $script:lastRestartAttempt = Get-Date
    try {
        Start-ScheduledTask -TaskName $guardianTask -ErrorAction Stop
        Write-Log "Started scheduled task '$guardianTask' (reason=$reason)"
    } catch {
        Write-Log "Failed to start scheduled task '$guardianTask' (reason=$reason): $_"
    }
}

$watchdogMutex = [System.Threading.Mutex]::new(
    $false,
    "Local\CCSwitchGuardianWatchdog"
)
try {
    $ownsWatchdogMutex = $watchdogMutex.WaitOne(0, $false)
} catch [System.Threading.AbandonedMutexException] {
    $ownsWatchdogMutex = $true
}
if (-not $ownsWatchdogMutex) {
    Write-Log "Guardian watchdog already running; duplicate exits"
    exit 0
}

Write-Log "Guardian watchdog started"
while ($true) {
    try {
    $stale = $false
    $hbPid = $null
    try {
        if (Test-Path $hb) {
            $data = Get-Content -Path $hb -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($data.ts -is [string]) { $ts = [datetime]::Parse($data.ts) } else { $ts = $data.ts }
            $age = (Get-Date) - $ts
            if ($age.TotalSeconds -gt $staleSec) { $stale = $true }
            $hbPid = Get-HeartbeatPid $data.pid
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
            $isPython = $proc -and $proc.Name -match '^pythonw?(\.exe)?$'
            # guardian.py 必须是独立参数（被引号或空格包围），不是 notguardian.py / .bak 子串
            $isGuardian = $cmd -match '(?<![-\w.])guardian\.py(?![-\w.])'
            if ($isPython -and $isGuardian) {
                Write-Log "Heartbeat stale (pid=$hbPid), killing Guardian"
                try {
                    Stop-Process -Id $hbPid -Force -ErrorAction Stop
                    Write-Log "Killed pid=$hbPid"
                    Start-Sleep -Seconds 2
                    Start-GuardianRecovery "stale heartbeat pid=$hbPid"
                } catch {
                    # 进程可能已自行退出（竞态）或权限失败
                    Write-Log "Stop-Process pid=$hbPid failed: $_"
                }
            } elseif (-not $proc) {
                Write-Log "Heartbeat stale and recorded pid=$hbPid is no longer running"
                Start-GuardianRecovery "dead heartbeat pid=$hbPid"
            } else {
                Write-Log "Heartbeat stale but pid=$hbPid not a python/guardian.py process (skip; name=$($proc.Name), cmd match=$isGuardian)"
            }
        } else {
            # 无 PID 依据（心跳缺失/解析失败/pid 非正整数）：不宽泛杀进程
            Write-Log "Heartbeat stale but no valid pid in heartbeat; not broad-killing"
        }
        Start-Sleep -Seconds 15
    }

    # ── Supervisor 看护（2026-08-08 加入）──────────────────────────────
    # supervisor-status.json 是 supervisor 每轮循环写入的心跳；stale 说明
    # supervisor 卡死或已死（OMP 重启等场景会带走它，Run 键/登录任务不会
    # 在运行中拉起）。与 Guardian 同模式：PID 精确验证 + 退避 + 拉起。
    $supStatus = "C:\Users\zhugu\.omp\guardian\supervisor-status.json"
    $supStaleSec = 180
    $supScript = "C:\Users\zhugu\.omp\guardian\proxies-supervisor.py"
    # 用 python.exe 而非 pythonw.exe：pythonw 在 PowerShell 启动环境下
    # 初始化即退（无控制台 + logging StreamHandler 副作用），python.exe 稳定。
    $supPy = "C:\Users\zhugu\scoop\apps\python313\current\python.exe"
    $supStale = $false
    $supPid = $null
    try {
        if (Test-Path $supStatus) {
            $sdata = Get-Content -Path $supStatus -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($sdata.ts -is [string]) { $sts = [datetime]::Parse($sdata.ts) } else { $sts = $sdata.ts }
            if (((Get-Date) - $sts).TotalSeconds -gt $supStaleSec) { $supStale = $true }
            $supPid = Get-HeartbeatPid $sdata.pid
        } else {
            $supStale = $true
        }
    } catch {
        Write-Log "Supervisor status read/parse error: $_"
        $supStale = $true
    }

    if ($supStale) {
        $sinceSup = (Get-Date) - $script:lastSupRestartAttempt
        if ($sinceSup.TotalSeconds -lt $restartBackoffSec) {
            Write-Log "Supervisor restart suppressed by backoff (stale pid=$supPid)"
        } else {
            $script:lastSupRestartAttempt = Get-Date
            $supProc = if ($supPid) { Get-CimInstance Win32_Process -Filter "ProcessId=$supPid" -ErrorAction SilentlyContinue } else { $null }
            $supCmd = if ($supProc) { [string]$supProc.CommandLine } else { "" }
            $supIsPython = $supProc -and $supProc.Name -match '^pythonw?(\.exe)?$'
            $supIsSupervisor = $supCmd -match '(?<![-\w.])proxies-supervisor\.py(?![-\w.])'
            if ($supProc -and -not ($supIsPython -and $supIsSupervisor)) {
                Write-Log "Supervisor stale but pid=$supPid is not a supervisor process (skip; name=$($supProc.Name))"
            } else {
                $killOk = $true
                if ($supProc) {
                    Write-Log "Supervisor stale (pid=$supPid), killing and restarting"
                    try { Stop-Process -Id $supPid -Force -ErrorAction Stop; Write-Log "Killed supervisor pid=$supPid"; Start-Sleep -Seconds 2 } catch { $killOk = $false; Write-Log "Stop-Process supervisor pid=$supPid failed: $_ (skip start this round)" }
                } else {
                    Write-Log "Supervisor stale and pid=$supPid no longer running, restarting"
                }
                if ($killOk) {
                    try {
                        # CreateNoWindow：防 cmd 弹窗（2026-08-08）
                        $psi = [System.Diagnostics.ProcessStartInfo]::new()
                        $psi.FileName = $supPy
                        $psi.Arguments = $supScript
                        $psi.CreateNoWindow = $true
                        $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
                        [System.Diagnostics.Process]::Start($psi) | Out-Null
                        Write-Log "Started supervisor (reason=stale pid=$supPid)"
                    } catch {
                        Write-Log "Failed to start supervisor: $_"
                    }
                }
            }
        }
    }
    Start-Sleep -Seconds 30
    } catch {
        # 任何未捕获异常：留痕崩溃日志并继续循环，绝不退出（2026-08-08 加入）
        $crashMsg = "FATAL loop error: $($_.Exception.Message)"
        try { Write-Log $crashMsg } catch {}
        Add-Content -Path "C:\Users\zhugu\.omp\guardian\watchdog-crash.log" -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $crashMsg" -Encoding UTF8 -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
    }
}
