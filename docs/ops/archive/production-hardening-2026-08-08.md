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

## 8. NewAPI user_sessions 每日自动清理（2026-08-08 02:25）

### 问题

`user_sessions` 上限 50，Guardian 每周期登录累积，打满后所有登录 409 `AUTH_SESSION_LIMIT`（重启不清，昨晚已手动清空一次）。机制性复发风险。

### 修复（proxies-supervisor.py）

新增 `cleanup_user_sessions()`：每日 03:00 后（与每日备份同一时机）：

1. `DELETE FROM user_sessions WHERE expires_at < now`（删除已过期会话）
2. 按 `last_active_at DESC` 只保留最近 10 条（`SESSION_KEEP=10`），删除其余

删除安全：Guardian/管理端下次请求会自动重新登录建立新会话（昨晚清空验证过）。对真实 DB 手动验证：2 条活跃会话删 0 条，无副作用。

### 回归与验证

```text
test_supervisor_session_cleanup.py   2/2 OK（60 条造数：删 30 过期 + 20 超量，保留最新 10 条；幂等）
test_proxies_supervisor_status.py    2/2 OK
```

Supervisor 已重启（PID 21608），schema v2 状态正常，四服务健康。

## 9. 死渠道隔离、zzzcoding 绕道、3002 端口收口（2026-08-08 02:31）

### 渠道 70/71 标 known-broken

- 渠道 71 `hugai-claude-opus5`：`group: default` 存在，"Failed to resolve routing group" 是**上游 hugai.vip 网关自身**错误，非本机配置；恢复失败 14 次。
- 渠道 70 `vip-j3gb-gpt`：上游真死，恢复失败 15 次。
- 处置：`AUTO_BAN_RECOVERY_EXCLUSIONS`（guardian.py）与 `KNOWN_BROKEN_CHANNELS`（smoke）同步加入 70/71；Guardian state 清理记录（4→2，剩 18/73）；Guardian 重启后 sync 未加回。
- 回滚：恢复渠道后从两个集合移除 id 即可。

### zzzcoding 上游 405 的 OMP 绕道

- 实测：`agentrouter/gpt-5.6-sol` 可用（14s）。
- OMP cooldown 机制（`modelFallback: true` + `fallbackRevertPolicy: cooldown-expiry`）已覆盖大部分 fallback 链的死模型跳损。
- 唯一必踩的坑：designer 主模型 `zg-newapi/gpt-5.6-sol`（渠道 73）已死。临时改为 `agentrouter/gpt-5.6-sol:high`（config.yml 行内注释标注，zzzcoding 恢复后还原）。
- 备份：`config.yml.before-designer-agentrouter-20260808.bak`。
- 显式 `--model zg-newapi/gpt-5.6-sol` 调用在恢复前仍会 503（NewAPI 无可用渠道），属预期。

### NewAPI 3002 端口收口（已执行）

- 管理员脚本 `~/.new-api-local/harden-firewall-3002.ps1`（UAC 提权执行）：删除全放行规则 `new-api.exe`，新增 `new-api-3002-local`（LocalPort=3002、RemoteAddress=LocalSubnet、Allow、Enabled）。
- 验证：规则详情正确；NewAPI smoke 全过（status 200、全部代理正常、两模型 200）；本机 loopback 与 Tailscale/LAN 子网仍可用，公网任意 IP 已无法直达 3002。

### 验证汇总

```text
newapi-local-smoke: status 200 / 全代理 OK / 隔离 OK / 两模型 200
渠道检查: known_broken 含 70；unexpected 只剩 73（上游真死，Guardian 继续跟踪）
Guardian state: disabled_channels=[18, 73]，70/71 未被 sync 加回
```

### Code Review 复核（2026-08-08 02:40，VERIFIED WITH CAVEATS）

复核范围 `95e448db` + `f021725d`，核心声明全部复现。两个边界需知：

1. **LocalSubnet 不含 Tailscale 其他节点**：Tailscale 接口前缀为 /32（实测 `PrefixLength=32`），Windows 防火墙 `LocalSubnet` 只含本机各接口本地子网。因此 `new-api-3002-local` 规则实际比预期更严格：其他 Tailscale 节点（VPS/其他设备）访问 3002 会被拒。当前实测 3002 消费者全部来自 127.0.0.1，无影响；若未来需要跨 Tailscale 节点访问，需显式新增 `RemoteAddress 100.64.0.0/10` 的规则。
2. **designer 改动需 reload**：修改 config.yml 时运行中的 OMP 会话仍用旧 designer 主模型；新启动的 OMP 进程自动加载新值。交互会话执行 `/reload` 或重启后生效。

另注：`guardian.log` 超 1MB 自动截断保留尾部，渠道 70 早期恢复失败记录已被轮转（既有行为，非本次改动）。

## 10. snapcompact.toolResults 实验启用（2026-08-08 03:10）

社区调研（OMP issue #1568，pi-blackhole 项目）后确认：17.2.10 内置 `snapcompact.toolResults` 实验项——把**大型历史工具输出**渲染为密集 PNG 图片（仅视觉模型），替代文本继续占用上下文。不调 LLM、纯本地。

- 配置：`~/.omp/agent/config.yml` 追加 `snapcompact.toolResults: true`（`systemPrompt` 保持 `none`，只归档工具输出，不动系统提示词 → 缓存影响面最小）。
- 主压缩策略保持 `shake` 不变；toolResults 是独立实验开关。
- 影响模型：K3/Sol（视觉）会收到归档图片；`omp config get snapcompact.toolResults` → true 已验证。
- **基线**（开启前，2026-08-08 03:10）：本会话 K3 74 请求 100% 缓存命中（18.1M/18.9M），Sol 48 请求 12.5%（1.17M/8.15M）。
- **评估标准**：1-2 天后对比 cache-optimizer stats 的 cachedInputTokens 占比；若命中率明显下降而 token 节省有限，关闭还原（备份 `config.yml.before-snapcompact-20260808.bak`）。
- 注意：`algorithmic` 压缩策略（OMP 主分支已合并 pi-blackhole）17.2.10 不含，等稳定版再评估。

## 11. AnyRouter 上游故障诊断（2026-08-08 03:20）

### 症状

`anyrouter/claude-opus-5`、`anyrouter/claude-opus-4-8` 调用失败。

### 诊断（逐层排除）

| 检查 | 结果 |
|---|---|
| 本地代理 8789 | 进程 PID 2456 监听正常，Supervisor 健康 |
| 代理代码/指纹 | 完整（含 context-1m-2025-08-07 等 9 个 beta 头、billing_header、harness_block） |
| key 有效性 | `/v1/models` 200，17 个模型可列 |
| `/v1/messages`（全模型） | **全部 429/503 Service Unavailable**（含 claude-3-5-haiku 轻量模型） |
| 错误类型 | 网关活着、推理端点整体停摆 |

### 结论

**上游 `anyrouter.top` 推理端点整体故障**（08-07 18:47 起持续），非本机配置/key/指纹问题。`/v1/models` 200 但 `/v1/messages` 全模型 429/503。OMP 中 anyrouter 只在 `slow` fallback 链尾，不影响主路径，无需改动配置。恢复靠上游；Supervisor 保持代理存活，恢复后自动可用。

## 12. 上游故障潮与子代理模型调整（2026-08-08 03:40）

### 实测：claude-opus-5 渠道 3/9 持续 429

`zg-newapi-anthropic/claude-opus-5`（slow/plan/vision 主模型）3 连测全 429 Service Unavailable——渠道 3（baibei-100xlabs）、9（linxi-k40）上游真死，与 zzzcoding（ch73）、anyrouter 同一波上游故障潮。渠道 45（agentrouter NewAPI 侧）也被 auto-ban，但 OMP 的 agentrouter provider 直连 8788 不受影响，实测可用（54s）。

### 可用性强模型盘点（2026-08-08 03:40 实测）

| 模型 | 状态 |
|---|---|
| `agentrouter/gpt-5.6-sol` | ✅ 可用（54s） |
| `zg-newapi/k3` | ✅ 可用（100% 缓存命中） |
| `zg-newapi/deepseek-v4-flash` | ✅ 可用 |
| `zg-newapi/gpt-5.6-sol` | ❌ ch73 zzzcoding 405 |
| `claude-opus-5`（渠道 3/9） | ❌ 429 |
| `codebuddy/gpt-5.6-sol` | ❌ WorkBuddy 402（03:43 重置） |
| anyrouter 全模型 | ❌ 上游 503 |

### 子代理模型调整

reviewer / security-reviewer 原绑定 `zg-newapi/gpt-5.6-sol:high`（死渠道 73）→ 先改 `@slow`（claude-opus-5，未料也 429）→ 最终指向 `agentrouter/gpt-5.6-sol:high`（唯一可用强模型），行内注释标注恢复后还原。

librarian/scout/sonic 用 `@smol`（sensenova）✅ 合理；task 用 `@task`（deepseek）✅ 合理——弱模型已正确用于搜索/探索/机械任务。

备份：`reviewer.md.before-slow-20260808.bak`、`security-reviewer.md.before-slow-20260808.bak`。

### 待观察

- WorkBuddy Sol 03:43 重置后恢复
- claude-opus-5 渠道 3/9 上游恢复后还原 reviewer 为 `@slow`
- 渠道 3/9 应纳入 Guardian 跟踪（当前 error_scan 尚未发现，需确认周期）