# agentrouter 8788 绑定地址事故修复 + 看护配置应用脚本（2026-08-15 晚）

## 1. 事件

用户收到 supervisor 告警："本地代理 agentrouter 端口 8788 重启后仍不可达，
请检查 proxies/agentrouter-proxy 日志"。手工多次重启代理均复现。

## 2. 根因（证据链）

- 代理进程健康：PID 1928，`/health` 返回 401（鉴权拦截 = 存活）。
- 但只绑定 Tailscale 网卡：`netstat` 仅 `100.83.32.95:8788 LISTENING`；
  `127.0.0.1`/`localhost` curl 连接拒绝（code 7），`100.83.32.95` 正常。
- 启动参数 `--host 100.83.32.95` 来自**双层看护**，二者都在进程启动时把
  `~/.omp/guardian/secrets.json` 的 `local_proxy_bind_host` 读成模块级常量：
  - `guardian.py:106-108`（`LOCAL_PROXY_BIND_HOST`，默认 `0.0.0.0`）→ `:1914` 拉起代理；
  - `proxies-supervisor.py:76`（`BIND_HOST`）→ `:83` 拉起代理。
- 该值被设为 `100.83.32.95`（应为默认 `0.0.0.0`，guardian.py:103-105 注释：
  本机客户端走 loopback、NewAPI 渠道 45 走 Tailscale，二者都要服务）。
  **每次"重启"都由看护用旧值拉起 → 故障必然复现。**

## 3. 修复

备份 `secrets.json.bak-20260815-bindhost`（未打印内容）→ `local_proxy_bind_host`
改回 `0.0.0.0` → 依次重启 proxy、Guardian、supervisor（后者经 Startup 的
`LocalAIProxies-Supervisor.lnk` 权威入口）。

**验证**：
- `netstat`：`0.0.0.0:8788 LISTENING`；
- `/health` 三路径（127.0.0.1 / localhost / 100.83.32.95）全部 401；
- 带真实 key 经 loopback 请求 `/v1/models` → 400（鉴权通过到达应用层，非 401）；
- supervisor 日志：`supervisor 启动，探测 127.0.0.1，绑定 0.0.0.0`。

## 4. 监督拓扑（本次厘清，备查）

| 层 | 入口 | 职责 |
|---|---|---|
| `watchdog.ps1` | 计划任务 "NewAPI Guardian Watchdog"（30s 轮询） | Guardian 心跳 stale 180s → 杀记录 PID + `Start-ScheduledTask "NewAPI Guardian"`；supervisor status stale 180s → 杀 + 直接拉起。均有 300s 退避 |
| `guardian.py` | 计划任务 "NewAPI Guardian"（pythonw） | 渠道治理 + 本地代理探针/重启（`restart_local_proxy`） |
| `proxies-supervisor.py` | Startup `LocalAIProxies-Supervisor.lnk`（conhost --headless + bat） | 本地代理端口看护（8788/8789/3003/15721/15999/16000），每轮写 `supervisor-status.json` |

计划任务 `LocalAIProxies-Supervisor(-Logon)` 为 Disabled，supervisor 的活跃入口是 Startup lnk。

## 5. 交付：`apply-secrets-restart.ps1`

位置 `~/.omp/guardian/apply-secrets-restart.ps1`（仓库镜像 `scripts/ops/apply-secrets-restart.ps1`）。今后改 `secrets.json` 后跑一次：

```powershell
# 看护级配置（bind host 等）：代理进程不动
powershell -NoProfile -ExecutionPolicy Bypass -File ~\.omp\guardian\apply-secrets-restart.ps1
# 代理 key/env 变更：连代理一起 bounce（supervisor 立即用新 env 拉起）
... -Proxy agentrouter
```

设计要点：
- 进程定位复用 watchdog.ps1 的边界正则（独立参数匹配，不误杀 `.bak`/子串）；
- Guardian 走 `Start-ScheduledTask`，supervisor 走 Startup lnk——与开机自启路径一致；
- 秒级完成重启，与 watchdog 的 180s 心跳阈值无竞态；
- `-Proxy` 白名单与 supervisor 的 `PROXIES` 表对齐，未知名字只告警不动手。

**烟测**：真实执行一次，guardian(11112→16024)、supervisor(18796→12920) 换血成功，
代理 PID 23284 未受影响，双栈 `/health` 401，心跳 14s/4s 新鲜。

## 6. 环境坑（已记入脚本头注释）

PS 5.1 读**无 BOM** 的 `.ps1` 按 ANSI 解析，中文注释会产生非法 token 直接
ParserError——`.ps1` 必须以 UTF-8 BOM 落盘（watchdog.ps1 亦带 BOM）。
