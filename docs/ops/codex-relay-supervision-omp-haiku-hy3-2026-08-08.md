# codex-relay 纳入 Supervisor 监管 + OMP haiku/hy3 角色分配（2026-08-08 下午）

**Status:** 已生效（OMP 配置改动需重启 OMP 进程加载）
**Scope:** `~/.omp/guardian/proxies-supervisor.py`、`~/.omp/agent/config.yml`、`~/.omp/agent/models.yml`、agnes haiku 渠道启用、16001 孤儿 relay 停机

## 1. codex-relay 纳入 Supervisor 监管 + 16001 停机

### 背景

本机三个 codex-relay 实例此前**仅靠 Task Scheduler（LogonTrigger）拉起，崩溃无自愈**：

| 端口 | 上游 | key | NewAPI 引用 |
|------|------|-----|-------------|
| 15999 | `api.zzzcoding.org/responses` | zzzcoding_codex_key | ch73 zzzcoding-codex-relay（禁用中） |
| 16000 | `new.sharedchat.cc/codex/v1/responses` | sharedchat_codex_key | ch74 sharedchat-codex-sol（禁用中） |
| 16001 | `new.sharedchat.cc/codex/v1/responses` | sharedchat_codex_key | **无任何引用（孤儿，与 16000 完全重复）** |

### 变更

1. **停 16001**：确认无配置/渠道引用后 kill 进程，端口释放。16001 不属于任何计划任务，不会自动复活。
2. **PROXIES 表新增 `codex-relay-15999` / `codex-relay-16000`**：cmd 与计划任务一致（`--secret-name` 从 secrets.json 读 key），`match` 正则锚定目录（`codex-relay-15999[\\/]codex-relay\.py`）防误杀。

### 踩坑（本次修复，重要）

1. **probe_host 必须显式 `127.0.0.1`**：relay 固定监听 `127.0.0.1`（codex-relay.py:636），而 supervisor 默认用 `BIND_HOST`（secrets.json `local_proxy_bind_host` = 100.83.32.95）探测 → 误判端口不可达 → **重启风暴**（5 次/小时触顶，restartBlocked）。与 omp-ttft/anyrouter 同模式：`"probe_host": "127.0.0.1"`。
2. **abandoned mutex 误判**：`acquire_single_instance` 原逻辑对 `CreateMutexW` 返回 `ERROR_ALREADY_EXISTS(183)` 一律判定重复退出。**持有者被强杀后 mutex 变 abandoned，新实例仍会拿到 183** → supervisor 一旦被 taskkill 就永远起不来（Start-Process / 计划任务 / 手动全部静默失败）。修复：183 时 `WaitForSingleObject(handle, 0)`，返回 `WAIT_ABANDONED(0x80)` 说明旧持有者已死，接管继续。
3. **Start-Process 启动 pythonw 无效**（静默失败，无错误输出）；`cmd /c start` 报"拒绝访问"。实测有效方式：bash 后台直接执行 `pythonw.exe <script>` 或前台 `python.exe` 运行。

### 验证

- 故障演练：kill 15999 relay（pythonw）→ 40s 内 supervisor 自动用 `python.exe` 拉起新实例（PID 25728）→ 端口恢复、`healthy=True`、`restartsLastHour=1`。
- 最终状态：supervisor（pythonw PID 32108）6 服务全 healthy（agentrouter/codebuddy/omp-ttft/anyrouter/codex-relay-15999/codex-relay-16000）。

### 回滚

```text
proxies-supervisor.py.bak-20260808-114158-add-relay
```

## 2. agnes haiku 渠道启用（OMP commit 角色）

### 根因链：为什么 haiku 请求从来到不了 agnes

- agnes haiku 渠道（ch68 agnes-com-haiku w20 / ch69 agnes-cn-haiku w10）配置完好：status=1、实测 523/881ms、`model_mapping: claude-haiku-4-5 → agnes-2.0-flash`，但 `used_quota=0`（从未承接流量）。
- 实证：发 `model=claude-haiku-4-5` 到 15721 → NewAPI consume log 显示 `model_name=claude-opus-5`、`use_channel=["3","72","45"]`（全 opus 渠道）。
- 根因：**cc-switch 代理（15721）用 `local-newapi` provider 的 `settings_config.env` 改写模型**，该 env 全部模型字段都是 `claude-opus-5`（含 `ANTHROPIC_DEFAULT_HAIKU_MODEL`）。NewAPI 侧无任何模型重定向（/api/option 已核）。**Claude Code 主链路（经 15721）无法使用 haiku，除非改 cc-switch provider 配置——用户明确不改（本体边界）。**
- 出路：**OMP 的 `zg-newapi` provider 直连 NewAPI 3002**（models.yml baseUrl，不走 15721）→ OMP 角色请求模型名原样到达 NewAPI。

### 验证

OMP zg-newapi key 直测 `claude-haiku-4-5` → 返回 `model=agnes-2.0-flash`（<1s）——**agnes 渠道首次承载真实流量**。

### 变更

- `models.yml`：zg-newapi 注册 `claude-haiku-4-5`（contextWindow 200000 / maxTokens 32768，不标 reasoning）。
- `config.yml`：`commit` 角色 → `zg-newapi/claude-haiku-4-5`（commit 消息高频轻活，haiku 完全胜任）。
- 回退链：`zg-newapi/claude-haiku-4-5` → `deepseek-v4-flash` → `sensenova-6.7-flash-lite`。

## 3. hy3 上岗（tiny 角色）

- `codebuddy/hy3-preview-agent`（WorkBuddy Hunyuan）在 models.yml 注册已久但无角色引用（闲置）。
- 实测 8787 链路 OK（`hy3-preview-agent` 24 tokens）。
- 变更：`tiny` 角色 → `codebuddy/hy3-preview-agent`；回退链 → `deepseek-v4-flash` → `sensenova-6.7-flash-lite`。
- `task` 角色**回退** `zg-newapi/deepseek-v4-flash:high`（用户决策：task 是主力子代理角色，保持 deepseek 保质量；haiku/hy3 用于轻量角色）。

## 4. 最终 OMP modelRoles（快照，重启后生效）

| 角色 | 模型 | 说明 |
|------|------|------|
| slow / plan / vision | `zg-newapi-anthropic/claude-opus-5:high` | 重活（不变） |
| **task** | `zg-newapi/deepseek-v4-flash:high` | 主力子代理（回退恢复） |
| **commit** | `zg-newapi/claude-haiku-4-5` | agnes 渠道（ch68/69），新增启用 |
| **tiny** | `codebuddy/hy3-preview-agent` | WorkBuddy Hunyuan，新增启用 |
| smol | `zg-newapi/sensenova-6.7-flash-lite` | 不变 |
| designer | `agentrouter/gpt-5.6-sol:high` | 不变 |
| bigctx | `longcat/LongCat-2.0` | 不变 |
| default | `zg-newapi/deepseek-v4-flash:max` | 不变 |

回退链：haiku / hy3 失败均 → deepseek-v4-flash → sensenova（不丢任务）。

### 备份

```text
models.yml.bak-*-haiku
config.yml.bak-*-haiku-subagent
config.yml.bak-*-roles
```

## 5. 3002 防火墙现状更正（复核结论）

production-hardening-2026-08-08.md 第 9 节已记录 02:31 收口：删除宽泛放行规则，新增 `new-api-3002-local`（LocalPort=3002、RemoteAddress=LocalSubnet、Allow）。本次复核确认：

- `LocalSubnet` 不含 Tailscale 其他节点（Tailscale 接口 /32），当前 3002 消费者全部 127.0.0.1，无影响。
- 若未来需要跨 Tailscale 节点访问 3002，需显式新增 `RemoteAddress 100.64.0.0/10` 规则（已记录于该文档，此处仅为交叉确认）。

## 6. 重启后路由验证（2026-08-08 下午）

OMP 重启后四探针验证（每个子代理仅报告自身 system prompt 的 Model 行）：

| 子代理 | 实际模型 | 配置来源 | 判定 |
|--------|---------|---------|------|
| task | `zg-newapi/deepseek-v4-flash` | modelRoles.task（回退生效） | ✅ |
| scout | `zg-newapi/sensenova-6.7-flash-lite` | modelRoles.smol | ✅ |
| reviewer | `agentrouter/gpt-5.6-sol:high` | **reviewer.md frontmatter 覆盖** | ✅ 用户临时配置 |
| security-reviewer | `agentrouter/gpt-5.6-sol:high` | **security-reviewer.md frontmatter 覆盖** | ✅ 用户临时配置 |

### reviewer/security-reviewer 临时覆盖（重要现状）

`~/.omp/agent/agents/reviewer.md` 与 `security-reviewer.md` frontmatter 均含：

```yaml
model:
  - "agentrouter/gpt-5.6-sol:high"  # 临时：claude-opus-5 上游 429（2026-08-08），恢复后还原 @slow
```

claude-opus-5 上游 429 期间的临时切换（用户决策），恢复后需还原 `@slow`。**08-03 文档"reviewer 走 @slow"记录已过时**，以 agent 文件 frontmatter 为准。

### 附带确认

- config.yml 重启后未被 OMP 启动重选改写（mtime 未变；踩坑 2 的覆盖本次未发生）。
- `commit → claude-haiku-4-5`、`tiny → hy3` 无法用子代理探针验证（非 agent 角色），首次触发时以 NewAPI consume log 复核（commit 应显示 `model=claude-haiku-4-5 → agnes-2.0-flash`）。

## 7. 稳定性加固：watchdog 看护 supervisor + UTF-8 BOM 踩坑（2026-08-08 下午）

### 背景

OMP 重启会带走其进程树（含 supervisor 等由 shell 拉起的进程）；supervisor 死后只有登录时的 Run 键/启动文件夹会拉起，**运行中崩溃无任何看护**（实测：OMP 重启后 supervisor 消失，无人拉起）。Guardian 有 watchdog.ps1（计划任务常驻，30s 循环检查 heartbeat 新鲜度），supervisor 无。

### 变更（`~/.omp/guardian/watchdog.ps1`）

1. **新增 supervisor 看护块**：与 Guardian 同模式——读 `supervisor-status.json` 的 ts/pid，stale（>180s）且 PID 精确验证（python/pythonw + `proxies-supervisor.py` 独立参数）后杀旧进程并拉起；独立 5 分钟退避（`$script:lastSupRestartAttempt`）。
2. **拉起用 `python.exe` 而非 `pythonw.exe`**：实测 pythonw 在 PowerShell 启动环境下初始化即退（无控制台 + logging StreamHandler 副作用），python.exe 稳定；用 `[System.Diagnostics.Process]::Start` 启动（`Start-Process` 同样静默失败）。

### 踩坑（重要）：PowerShell 5.1 无 BOM UTF-8 中文注释吞行

- watchdog.ps1 无 BOM，PowerShell 5.1 按 ANSI(GBK) 解码 UTF-8 中文注释；**注释行尾字节与下一行 `\n` 配对，把相邻的 `$supStatus`/`$supPy` 赋值行吞进乱码注释** → 运行时 Path 空值 / Process.Start 无文件名 / pid 空，三处连锁失败，且 AST 解析 0 错误、语法检查通过——**极难定位**。
- 原文件中文注释恰好未触发（行尾字节安全），本次插入的注释触发。
- 修复：文件转 **UTF-8 with BOM**（`WriteAllText` + `UTF8Encoding($true)`），PowerShell 5.1 正确识别。AST 复查赋值语句全部恢复。

### 验证（完整自愈演练）

1. kill supervisor → watchdog 30s 内检测 stale → 自动拉起（python.exe）→ status 新 pid、6 服务全 OK ✅
2. relay 15999/16000 在演练中由 supervisor 自愈（10928 为新拉起实例）✅
3. mutex 正确挡重复实例（[Process]::Start 测试实例正常退出）✅
4. watchdog 日志无 Path 错误、pid 正确读取 ✅

### 回滚

```text
watchdog.ps1.bak-*-supervisor
```

## 待办

1. **commit/tiny 首触发复核**：非 agent 角色无法探针验证；首次触发时查 NewAPI consume log（commit 应显示 `claude-haiku-4-5 → agnes-2.0-flash`、tiny 应显示 `hy3-preview-agent`）。
2. **reviewer/security-reviewer 已还原 @slow**（2026-08-08 下午，claude-opus-5 429 实测恢复后还原；agentrouter/gpt-5.6-sol 覆盖已移除）。
3. 计划任务（LogonTrigger）登录时仍会拉起 pythonw relay，与 supervisor 管理的 python.exe 双实例共存（Windows SO_REUSEADDR 双绑，无冲突）；如需单一管理源，可停两个计划任务让 supervisor 全权接管（未执行，保持现状）。
4. **15721 cc-switch 代理无自愈**：guardian LOCAL_PROXIES 仅 agentrouter/codebuddy；cc-switch 进程崩溃后无人拉起（用户边界，未纳入监管，需人工确认后决定）。

## 相关文件

- `~/.omp/guardian/proxies-supervisor.py`（+ `.bak-20260808-114158-add-relay`）
- `~/.omp/agent/config.yml`、`~/.omp/agent/models.yml`
- `~/.omp/guardian/secrets.json`（key 来源，不落明文）
