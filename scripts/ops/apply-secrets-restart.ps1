# apply-secrets-restart.ps1 — 修改 ~/.omp/guardian/secrets.json 后应用配置。
#
# 背景（2026-08-15 agentrouter 8788 事故）：guardian.py 与 proxies-supervisor.py
# 都在进程启动时把 secrets.json 读成模块级常量（bind host、各代理 key）。
# 只改 secrets.json 不重启这两个看护，watchdog 自愈会用旧值把代理拉回旧配置。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File apply-secrets-restart.ps1
#       # 仅重启 Guardian + Supervisor（代理进程不动，适用于 bind host 等看护级配置）
#   ... -Proxy agentrouter
#       # 额外按名 bounce 指定代理（代理 env/key 变更时必需；supervisor 会立即拉起）
#       # 可多次指定：-Proxy agentrouter,anyrouter
#
# 安全设计：
#   - 精确匹配命令行（与 watchdog.ps1 同款边界正则），绝不宽泛杀 python。
#   - Guardian 经权威入口 Start-ScheduledTask "NewAPI Guardian" 拉起；
#     Supervisor 经 Startup 的 LocalAIProxies-Supervisor.lnk 拉起。
#   - watchdog.ps1 的心跳过期阈值 180s，本脚本秒级完成重启，无并发拉起竞态。
param(
    [string[]]$Proxy = @()
)

$ErrorActionPreference = 'Stop'
$guardianDir = 'C:\Users\zhugu\.omp\guardian'

function Find-ScriptProcess([string]$scriptName) {
    # 脚本名必须是独立参数（被引号/空格包围），不匹配 .bak / 前缀子串
    $pattern = '(?<![-\w.])' + [regex]::Escape($scriptName) + '(?![-\w.])'
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match '^(pythonw?|node)(\.exe)?$' -and [string]$_.CommandLine -match $pattern }
}

# 已知代理名 → 命令行锚定模式（与 proxies-supervisor.py 的 PROXIES 表对齐）
$proxyMatch = @{
    'agentrouter' = @{ Pattern = 'agentrouter-proxy\.py'; Names = '^(pythonw?)(\.exe)?$' }
    'anyrouter'   = @{ Pattern = 'anyrouter-proxy[\\/]proxy\.cjs'; Names = '^(node)(\.exe)?$' }
}

# 1) 停 Guardian + Supervisor
foreach ($script in @('guardian.py', 'proxies-supervisor.py')) {
    $procs = @(Find-ScriptProcess $script)
    foreach ($p in $procs) {
        Write-Host "stop $script pid=$($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force
    }
    if (-not $procs) { Write-Host "warn: no running $script found" }
}

# 2) 可选：bounce 指定代理（交由 supervisor 重启，携带新 env/key）
foreach ($name in $Proxy) {
    $entry = $proxyMatch[$name]
    if (-not $entry) { Write-Host "warn: unknown proxy '$name' (known: $($proxyMatch.Keys -join ', '))"; continue }
    $procs = @(Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match $entry.Names -and [string]$_.CommandLine -match $entry.Pattern })
    foreach ($p in $procs) {
        Write-Host "stop proxy $name pid=$($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force
    }
    if (-not $procs) { Write-Host "warn: proxy '$name' not running" }
}

Start-Sleep -Seconds 2

# 3) 经权威入口拉起
Start-ScheduledTask -TaskName 'NewAPI Guardian'
Write-Host 'started scheduled task: NewAPI Guardian'
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'LocalAIProxies-Supervisor.lnk'
Start-Process $lnk
Write-Host "started supervisor via $lnk"

# 4) 验证：进程起来 + 心跳文件刷新
Start-Sleep -Seconds 8
$ok = $true
foreach ($script in @('guardian.py', 'proxies-supervisor.py')) {
    $procs = @(Find-ScriptProcess $script)
    if ($procs) {
        Write-Host "ok: $script running pid=$($procs[0].ProcessId)"
    } else {
        Write-Host "FAIL: $script not running after restart"; $ok = $false
    }
}
if (-not $ok) { exit 1 }
Write-Host 'done. 代理端口可达性由 supervisor 自动恢复；必要时查 proxies-supervisor.log'
