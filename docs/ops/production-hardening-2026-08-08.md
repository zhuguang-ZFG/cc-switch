# 生产系统加固（2026-08-08）

**状态：** 已完成 5/6 项；1 项需管理员权限由用户执行
**范围：** WorkBuddy converter、Guardian、密钥 ACL、Supervisor 可观测、NewAPI 备份演练

## 1. WorkBuddy 余额耗尽 key 持久隔离（已完成）

### 问题

`custom_keys.json` 的 4 个 `gpt-5.6-sol` key 中部分余额耗尽，上游返回：

```text
HTTP 401 {"error":"Insufficient balance"}
```

converter 只对 502/503/504/`unavailable`/`temporarily`/`bad gateway`/403 `unsupported_client` 冷却 key，401 被当作不可重试错误直接透传；OMP 收到后整请求重发，形成「401 → 重发完整请求 → 401 → 重发 → 成功」模式。实测近一小时：23 次 Insufficient balance 401、94 次成功、16 次网络错误。

### 修复（`~/.kimi-code/proxies/codebuddy2openai/converter.py`）

1. 新增 `_is_exhausted_key_error`：精确识别 `status==401 && body 含 "insufficient balance"`，普通 401 不变。
2. 新增持久隔离：`_mark_key_exhausted` 把 key 的 **SHA-256 指纹**写入 `key_state.json`（不落明文），默认隔离 **12 小时**（`EXHAUSTED_KEY_QUARANTINE_S` 可覆盖）。
3. 流式/非流式路径在识别到余额耗尽时：持久隔离该 key + 同请求内换下一个 key。
4. 顺带修正 `_pick_key` 跨请求冷却 bug：原 `fail_at <= request_epoch` 使 180s 冷却只在同一请求内生效，新请求立即重试刚失败的 key；改为按当前时间判断。
5. 隔离到期自动清除并重探（自愈）。

### 回滚

```text
converter.py.before-exhausted-key-quarantine-20260808.bak
```

### 验证

```text
scripts/ops/test_workbuddy_converter_keypool.py  4/4 OK
omp -p --no-session --model codebuddy/gpt-5.6-sol  → WB_QUARANTINE_OK
```

**注意**：converter 由 Supervisor 托管，需重启 converter 进程才加载新逻辑（旧进程仍跑旧代码）。

## 2. Guardian 恢复探测超时（已完成）

### 问题

Guardian 已有指数退避（`RECOVERY_BACKOFF_BASE=2`，上限 60min），但恢复探测用 `TEST_CHANNEL_TIMEOUT=30s`，慢渠道两次探测就吃满 90s 周期预算，导致「Cycle budget exceeded, skipping …」反复出现（实测 cycle 93–166s）。

### 修复（`~/.omp/guardian/guardian.py`）

新增 `RECOVERY_PROBE_TIMEOUT = 12`，恢复探测专用短超时；权重调整慢渠道复测保持 30s 不变。

### 回滚

```text
guardian.py.before-recovery-probe-timeout-20260808.bak
```

### 验证

```text
scripts/ops/test_guardian.py  101/101 OK
```

**注意**：Guardian 由计划任务托管，需重启 Guardian 进程才生效。

## 3. 敏感文件 ACL 收紧（已完成）

对以下文件移除继承 + 只保留 zhugu/SYSTEM/Administrators 完全控制：

```text
~/.kimi-code/proxies/codebuddy2openai/custom_keys.json
~/.kimi-code/proxies/agentrouter-proxy/keys.json
~/.omp/agent/models.yml
~/.new-api-local/new-api.db
~/.new-api-local/backups/new-api-2026-08-07.db
```

修复前 `CodexSandboxUsers` 组对这些文件有 Modify 权限；修复后 ACL 仅三主体。

### 验证

- `icacls` 逐文件确认
- `omp models --json` 27 模型正常（OMP 仍可读）
- `GET /api/status` 200（NewAPI 仍可读）

## 4. NewAPI 端口暴露（需管理员执行）

### 现状

- NewAPI `new-api.exe` 监听 `::`（IPv6 任意地址），启动脚本只设 `PORT=3002` 无监听地址参数。
- 现有防火墙规则 `new-api.exe` 两条均「Allow any port / any remote address」。
- 实测活跃连接全部来自 `127.0.0.1`，无外部消费者。

### 待执行（管理员 PowerShell）

```powershell
# 1. 删除宽泛放行规则
Remove-NetFirewallRule -DisplayName "new-api.exe"

# 2. 新增仅本地子网精确规则（Tailscale/LAN 需要时仍可用；外部公网 IP 被拒绝）
New-NetFirewallRule -DisplayName "new-api-3002-local" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3002 -RemoteAddress LocalSubnet -Profile Any
```

当前 shell 非管理员，未执行，服务未受影响。

## 5. Supervisor 心跳状态（已完成，待重启生效）

`proxies-supervisor.py` 新增 `write_status()`：每轮循环写 `supervisor-status.json`（schema_version/ts/pid/bind_host/端口探测结果/当日重启数/最后备份日期），解决「Hub 旧任务状态误导」问题。

### 回滚

```text
proxies-supervisor.py.before-heartbeat-20260808.bak
```

**注意**：Supervisor 由计划任务 `LocalAIProxies-Supervisor` 拉起（conhost --headless start-proxies-supervisor.bat），需结束旧进程 + 手动启动一次 bat 才写状态文件。

## 6. NewAPI 备份恢复演练（已完成）

新增 `scripts/ops/newapi_backup_restore_drill.py`：复制最新备份到临时目录 → `integrity_check` → 校验关键表存在 → 抽样行数。只读，不碰生产。

### 验证

```text
backup: new-api-2026-08-07.db (37756928 bytes, age 0d 6h)
integrity_check: ok
tables: 34
channels: 33 rows
options: 53 rows
OK: backup restore drill passed
```

### 月度计划任务（可选）

```powershell
schtasks /create /tn "NewAPI-Backup-Restore-Drill" /tr "C:\Users\zhugu\scoop\apps\python313\current\python.exe D:\Users\cc-switch\scripts\ops\newapi_backup_restore_drill.py" /sc monthly /d 1 /st 04:00
```

## 7. 附带：NewAPI user_sessions 打满清理

`newapi-local-smoke.py` 登录报 `409 AUTH_SESSION_LIMIT`（文档已知：user_sessions 上限 50，打满后登录 409，重启不清）。本次处置：

```text
user_sessions 50 → 0（备份: backups/new-api-before-session-clear-20260808-011924.db）
```

清理后 smoke 登录恢复，渠道检查只剩已知问题渠道 70（vip-j3gb-gpt，Guardian 跟踪中）。

## 验证汇总

| 门禁 | 结果 |
|---|---|
| WorkBuddy keypool | 4/4 OK |
| Guardian | 101/101 OK |
| OMP routes | 32/32 OK |
| OMP cache registry | PASS |
| OMP unexpected-stop + TTFT | 8/8 OK |
| NewAPI smoke | 除已知渠道 70 外全 OK |
| 备份恢复演练 | OK |

## Code Review 修复（2026-08-08 凌晨，4bf46ad5 后追加）

1. **converter `_persist_key_state` 锁内 I/O**（P1）：原实现在 `_CUSTOM_LOCK` 内做 mkstemp+fsync+replace，高并发下阻塞所有 key 选择；且无锁读 `_KEY_EXHAUSTED_UNTIL` 存在并发遍历风险。改为锁内快照、锁外写文件；`_mark_key_exhausted` 锁内只改字典、锁外持久化。
2. **supervisor `restarts_today` 跨天不清零**（P2）：字段名 `_today` 但自启动起累计。按日重置。
3. **drill glob 误匹配手工快照**（P2）：`new-api-*.db` 会把 `new-api-before-*` 快照（如 before-session-clear）纳入候选，可能选中错误备份。改为正则只匹配 `new-api-YYYY-MM-DD.db` 每日备份格式。

## 三进程重启完成（2026-08-08 01:24-01:26）

| 进程 | 方式 | 结果 |
|---|---|---|
| Guardian | 计划任务 `NewAPI Guardian` Stop/Start | Running，heartbeat PID 16892 新鲜 |
| Supervisor | kill 旧进程 → Start-Process 新 python | PID 29744，`supervisor-status.json` 正常写入 |
| converter | kill 旧进程 → 新 Supervisor 30s 内探测重启 | PID 26304，`restarts_today.codebuddy=1` |

重启后真实请求验证：converter 日志出现 `↻ exhausted 401 | key quarantined`——新隔离分支实测触发。

## 当前上游状态（非代码问题）

WorkBuddy 全部 Sol key 于 01:27 起返回：

```text
HTTP 402 {"error":"Usage limit reached, will reset on today at 3:43 AM (UTC+8)"}
```

每日用量额度整体耗尽，今天 03:43 自动重置。01:18 同路由 smoke 仍成功（`WB_QUARANTINE_OK`），额度在审计期间测试中消耗。402 不属于可重试错误，converter 原样透传、OMP 重试后失败，符合 fail-closed 设计；若 402 为单 key 限额而非共享额度，后续可考虑将 402 usage-limit 纳入隔离，但需先确认 key 独立性。


## Code Review 二轮：并发租约 + Supervisor 结构化状态（2026-08-08 01:50-02:00）

### 问题

1. **并发 key 重复使用**：多个请求可在第一个 401 返回前同时选中同一 key，产生重复 401 和完整请求。
2. **Supervisor 状态布尔化**：`port_state[name]` 只有 true/false，无法区分瞬时失败、重启失败、熔断阻断或脚本缺失。

### 修复（converter.py）

- 新增 `_KEY_IN_FLIGHT`（plaintext key → active lease count）。
- 新增 `_lease_key`（原子选中并占用）、`_release_key`（幂等释放）。
- 非流式 `_chat_custom`：每次 attempt 前 `_lease_key`，所有路径 `finally` 释放。
- 流式 `_stream_custom`：生成器首次迭代时才 `_lease_key`（避免客户端在生成器启动前断开造成泄漏）；换 key 时先释放旧租约再租新 key；生成器关闭/取消时 `finally` 释放当前租约。

### 修复（proxies-supervisor.py）

- `write_status` 升级为 **schema_version=2**：`services: {name: {healthy, restartBlocked, lastError, restartsLastHour}}`。
- 每轮循环记录每服务健康、阻断、错误原因、近一小时重启次数。

### 回归

```text
test_workbuddy_converter_keypool.py   9/9 OK
test_proxies_supervisor_status.py    2/2 OK
test_guardian.py                     101/101 OK
test_omp_routes.py                   31/32 OK（1 个已知 fallback 重复 primary，非本次改动）
```

### 生产重启（2026-08-08 01:49-01:50）

| 进程 | 方式 | 结果 |
|---|---|---|
| Supervisor | kill 旧进程 → 后台启动 | PID 24580，schema v2 状态正常写入 |
| converter | kill 旧进程 → 新 Supervisor 30s 内拉起 | PID 29884，`restarts_today.codebuddy=1` |

### 验证

- `hy3-preview-agent` 同路径成功（01:54，2.4s，tokens=26）。
- `gpt-5.6-sol` 持续 **402 Usage limit reached, will reset on today at 3:43 AM (UTC+8)**——上游每日额度耗尽，非代码问题。
- 租约生效证据：converter 日志显示网络错误后正确轮换 4 个 key（attempt=1/4→4/4），无重复占用。

### 当前上游状态

WorkBuddy Sol 全部 key 于 01:53 起返回：

```text
HTTP 402 {"error":"Usage limit reached, will reset on today at 3:43 AM (UTC+8)"}
```

每日用量额度整体耗尽，今天 03:43 自动重置。若 03:43 后 Sol 仍不可用，需确认 key 独立性（单 key 限额 vs 共享额度）。