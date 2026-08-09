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

## 8. cc-switch 代理（15721）纳入 Supervisor 监管（2026-08-08 下午）

### 背景

cc-switch 是 OMP 主链路关键节点（Claude Code → 15721），但**无任何自启动/自愈机制**（无 Run 键、无计划任务、guardian LOCAL_PROXIES 不含它）——崩溃后只能手动/重启恢复。

### 变更（`~/.omp/guardian/proxies-supervisor.py`）

1. PROXIES 新增 `cc-switch-proxy`（port 15721、probe_host 127.0.0.1、dir 安装目录、match `cc-switch\.exe`）。**仅进程级自愈**：崩溃时重启 exe，不触碰本体代码/配置/DB；cc-switch 启动时自动恢复代理接管状态（restore_proxy_state_on_startup）。
2. env 检查兼容无密钥条目（`info.get("env")` 防警告噪音）。
3. 重启逻辑兼容单元素 exe 命令（`len(cmd) > 1` 才做 script 存在性检查）。

### 踩坑

- **相对路径 exe 启动失败**：`cmd: ["cc-switch.exe"]` + cwd 下 Popen 报 `WinError 2`（Windows CreateProcess 对相对 exe 名解析异常）；改为**绝对路径**后成功。
- 重启验证窗口 4s 对 Tauri 应用偏短（启动含 DB 初始化 + 代理恢复，实测 ~15-20s），可能产生 "port unavailable after restart" 假告警；当前可接受（崩溃场景本就该告警），后续可考虑按服务配置验证等待。

### 验证（完整演练）

1. kill cc-switch（PID 1420）→ supervisor 30s 内自动拉起（绝对路径 Popen）→ cc-switch 启动并恢复代理接管（15721 重新监听）✅
2. 主链路 smoke：POST 15721/v1/messages → 200（完整响应含 signature）✅
3. 7 服务全 OK（agentrouter/anyrouter/codebuddy/omp-ttft/codex-relay-15999/codex-relay-16000/cc-switch-proxy）✅
4. cc-switch 主进程 + WebView 子进程（msedgewebview2）正常共存 ✅

### 回滚

```text
proxies-supervisor.py.bak-*-ccswitch
```

## 9. 全链路验证套件修复（2026-08-08 下午）

系统性验证发现并修复 3 处运维资产漂移，**全套测试转绿**：

| 套件 | 修复前 | 修复 | 修复后 |
|------|--------|------|--------|
| Guardian 运行时测试（~/.omp/guardian/test_guardian.py） | 95 tests / 6 errors | `FakeNewAPI.test_channel` 补 `timeout` 参数（生产代码新增 `RECOVERY_PROBE_TIMEOUT` 后测试未跟进） | 95/95 OK |
| OMP 路由测试（test_omp_routes.py） | 31/32 | designer fallback 链首与主模型重复（`agentrouter/gpt-5.6-sol` 重复）→ 链改为 `codebuddy/gpt-5.6-sol` → `zg-newapi/gpt-5.6-sol` → `claude-opus-5`（codebuddy 实测可用，独立故障域） | 32/32 OK |
| NewAPI smoke（newapi-local-smoke.py） | channels FAIL | ch73（zzzcoding 上游 405 真死）加入 `KNOWN_BROKEN_CHANNELS`，guardian 同步加入 `AUTO_BAN_RECOVERY_EXCLUSIONS`（避免每周期白耗恢复探测；上游恢复后需手动启用并从两集移除） | **ALL OK** |

另：仓库镜像 `scripts/ops/guardian.py` 排除集落后运行时（缺 70/71/73），已同步——**后续修改 guardian.py 需同时更新运行时与仓库镜像**。

### 全量验证矩阵（2026-08-08 13:07 快照）

```text
newapi-local-smoke:   ALL OK（status 200 / 7 代理 OK / 渠道预期一致 / 双模型 smoke 200）
test_guardian.py:     95/95 OK（运行时）
test_omp_routes.py:   32/32 OK
test_codex_relay.py:  14/14 OK
test_omp_ttft_gateway.cjs: 5/5 OK
```

### 回滚

```text
guardian.py.bak-*-ch73-exclude
newapi-local-smoke.py.bak-*-ch73
config.yml（designer 链改动，无独立备份，历史备份见 §4）
test_guardian.py（FakeNewAPI.test_channel 签名，无独立备份）
```

## 10. 运维成熟化：日志轮转 / 管理源收敛 / 崩溃留痕（2026-08-08 下午）

### 变更清单

1. **watchdog.log 轮转**：Write-Log 超 1MB 自动改名 `.old` 再续写（guardian.log 已有 5MB×5 轮转，watchdog 此前无限增长）。
2. **relay 计划任务收敛**：`OMP Codex Relay` / `OMP SharedChat Codex Relay` 已 Disable——relay 由 supervisor 单一管理源控制（消除登录时双实例 + 配置漂移；可随时 Enable 回滚）。
3. **NewAPI 备份 drill 计划化**：新建计划任务 `NewAPI-Backup-Restore-Drill`（每月 1 日 04:00 跑 `scripts/ops/newapi_backup_restore_drill.py`，production-hardening §6 建议落地）。
4. **watchdog 崩溃留痕 + 循环不退出**：while 循环体包 try/catch，任何未捕获异常写 `watchdog-crash.log` 并继续循环（此前异常即死且无痕迹，曾出现 2 分钟内自发死亡无法定位）。
5. **资源观察**：~/.omp 3.2G（agent/sessions 1.1G 会话历史 + plugins 919M + puppeteer 420M + run/daemons 398M headless profile）——均为运行数据，不动；日志总量 53M 可控。

### 踩坑（重要操作教训）

- **PowerShell 查询自匹配**：`Get-CimInstance | Where { $_.CommandLine -match 'watchdog.ps1' }` 会匹配到**查询命令自己**（-Command 参数含目标字符串）→ 输出 PID 是查询进程，误判 watchdog 存活/死亡；更危险的是**无差别 kill 会误杀真实 watchdog**（曾因此误杀 13:01:31 实例）。
- **BOM 二踩**：edit 工具每次重写 .ps1 都会移除 UTF-8 BOM（§7 修复过又复发）→ **修改含中文的 .ps1 后必须重加 BOM + Parser 验证**（已记入持久记忆）。
- **验证 watchdog 存活的可靠方法**：手动跑 `watchdog.ps1` 看日志是否出现 "already running"（mutex 探测），或精确匹配 `-like '*-File*watchdog.ps1*'` 且人工核对命令行。

### 观察（13:16 启动后 10 分钟）

watchdog 崩溃捕获已生效；若 crash log 出现内容说明循环内有未捕获异常（当前无）。

## 11. 核心自愈路径实测 + 定价完整性（2026-08-08 下午）

### 未定价模型补齐（成本爆炸风险消除）

渠道 49 个模型中有 4 个无 ModelRatio 定价——NewAPI 按 37.5 倍兜底计费（mercury-2 同款坑）。其中 **k3-256k（ch33 健康）与 qwen3.8-max（ch31 健康，OMP 已注册模型）有真实使用风险**。已补齐：

| 模型 | 定价 | 参考 |
|------|------|------|
| k3-256k | 2 | 同 k3 |
| qwen3.8-max | 0.5 | 同 qwen 档 |
| gpt-5.3-codex-spark | 0.5 | gpt 档 |
| gpt-image-2 | 2 | 图像档 |

### 核心自愈路径实测（此前从未演练）

| 路径 | 演练 | 结果 |
|------|------|------|
| guardian 崩溃 → watchdog 拉起 | kill guardian → 180s 心跳过期 → watchdog 检测 dead pid → Start-ScheduledTask 拉起 | ✅ 总恢复 ~3.5min（新 pid 33568） |
| NewAPI 崩溃 → 自动拉起 | kill new-api.exe → 3002 断开 → <90s 恢复（新 pid 6056，LocalNewAPI-Watchdog 每分钟触发） | ✅ |
| 恢复后全链路 | smoke ALL OK + guardian/supervisor 心跳新鲜 + 15721 主链路 200 | ✅ |

### 配置矛盾记录（未改动）

`ModelRequestRateLimitEnabled: false` + `Count: 0`，与公告"每用户限速 120 次/分钟"不符。当前由 OMP `maxInFlightRequests`（应用层）承担限流；NewAPI 级限流开启可能误伤 6 并发 task 场景，**保持关闭**，记录观察。

## 12. 双看护冗余：LocalNewAPI-Watchdog 扩展（2026-08-08 下午）

### 背景

watchdog.ps1 存在**自发死亡**现象（计划任务实例存活 2-20 分钟不等，崩溃捕获未触发=循环外死亡，死因未定位；此前 13:11 误杀 + probe 探测自杀为部分诱因，但 C/E 实例死亡无解释）。单看护存在盲区。

### 变更（`~/.new-api-local/watchdog.ps1`，已备份 `.bak-*-triple`）

由"仅 NewAPI 3002 探活"扩展为**三路看护**（每分钟触发式，原架构不变）：

1. **NewAPI**：3002 端口不可达 → start.ps1（原有）
2. **guardian**：heartbeat 180s 过期 + PID 精确验证 → Start-ScheduledTask `NewAPI Guardian`（新增）
3. **supervisor**：supervisor-status 180s 过期 + PID 精确验证 → python.exe 拉起（新增）

文件保持 ASCII-only（避免 BOM 吞行坑）。由此形成**双看护冗余**：watchdog.ps1（30s 常驻）与 LocalNewAPI-Watchdog（每分钟触发）都可拉起 guardian/supervisor，任一死亡不致命。

### 验证

- kill guardian → LocalNewAPI-Watchdog 在心跳过期后拉起（新 pid 1824，总恢复 ~4min）✅
- NewAPI 崩溃拉起（此前演练，<90s）✅
- 语法 Parser 验证通过 ✅

### 当前守护体系（终态）

| 组件 | 看护 1 | 看护 2 |
|------|--------|--------|
| guardian | watchdog.ps1（30s） | LocalNewAPI-Watchdog（1min） |
| supervisor | watchdog.ps1（30s） | LocalNewAPI-Watchdog（1min） |
| NewAPI | LocalNewAPI-Watchdog（1min） | guardian 自身（3 连败后重启） |
| 本地代理 7 服务 | supervisor（30s） | — |

## 13. 自监控机制化：System-Health-Check（2026-08-08 下午）

### 变更

新增 `scripts/ops/system-health-check.py` 一键巡检（17 项）：NewAPI 3002 HTTP、TTFT 3003、cc-switch 15721、5 本地代理端口、guardian 心跳+进程、supervisor 状态、watchdog 崩溃记录、渠道健康快照、~/.omp 体积、3 关键日志大小。

- 退出码：0 全绿 / 1 有失败（可供告警链判断）
- `--json` 结构化输出
- **结果自动追加** `~/.omp/guardian/health-check.log`（脚本内写文件，规避计划任务 `>>` 重定向引号解析问题——已踩坑）
- 注册计划任务 **System-Health-Check**：每 4 小时（hourly /mo 4），手动触发实测通过（17/17 ALL GREEN 落盘）

### 巡检命令（日常/agent 会话复用）

```powershell
python D:\Users\cc-switch\scripts\ops\system-health-check.py
```

## 14. cmd 弹窗问题根除（2026-08-08 下午，用户报告）

### 弹窗源（4 类）

1. **计划任务 System-Health-Check / NewAPI-Backup-Restore-Drill**：Action 直接跑 `python.exe`（控制台程序）→ 每 4 小时/每月弹窗。
2. **watchdog.ps1 / LocalNewAPI-Watchdog** 拉起 supervisor：`[Process]::Start(python.exe)` 无隐藏 flag → 崩溃恢复时弹窗。
3. **start.ps1**：`& $exePath`（new-api.exe 是 Go 控制台程序）→ NewAPI 拉起时弹窗。
4. 注：supervisor.py / guardian.py 的 Popen **原本就有** `CREATE_NO_WINDOW`（确认非弹窗源）。

### 修复

| 位置 | 修改 |
|------|------|
| 两个计划任务 | Action → `powershell.exe -WindowStyle Hidden -Command "Start-Process -FilePath python.exe -ArgumentList ... -WindowStyle Hidden -Wait"`（双重隐藏） |
| watchdog.ps1 / LocalNewAPI-Watchdog.ps1 | `ProcessStartInfo { CreateNoWindow=$true, WindowStyle=Hidden }` |
| start.ps1 | `Start-Process -FilePath new-api.exe -WindowStyle Hidden -Wait`（保留同步防重入 + stdout/stderr 重定向） |

### 验证

- System-Health-Check 任务改后手动触发：20/20 OK 落盘 ✅
- start.ps1 语法 + exit 0（3002 已监听分支）✅
- watchdog/supervisor/guardian 全部重启加载新代码 ✅
- 注意：watchdog.ps1 修改后重加 BOM（同 §7 规则）

## 15. LongCat-2.0 全面剔除（2026-08-08 晚，用户判定质量不达标）

### 变更（`~/.omp/agent/config.yml`，备份 `.bak-*-nolongcat`）

| 位置 | 原 | 现 |
|------|-----|-----|
| bigctx 主模型 | `longcat/LongCat-2.0`（1M ctx） | `zg-newapi/gpt-5.6-sol`（400k ctx，实测 200；anyrouter/opus-5 曾候选但实测 502 弃用） |
| slow 链第 2 项 | longcat | 移除（codebuddy→opus-4-8→gpt-5.6-sol→k3→anyrouter/opus-5） |
| plan 链第 2 项 | longcat | 移除 |
| default 链第 3 项 | longcat | 移除 |
| maxInFlightRequests `longcat: 4` | 保留后删除 | 清理（顺带修复行粘连） |

bigctx 链去主模型重复：`k3 → opus-5 → deepseek-v4-flash`。

### 验证

- YAML 有效；全文件 0 处 longcat 引用
- test_omp_routes 32/32 OK（无悬空引用）
- anyrouter/opus-5 实测 502（上游不稳，未采用）
- 注：OMP 重启后生效（当前会话仍用旧配置）

## 16. LongCat 转职：翻译子代理（2026-08-08 晚）

LongCat-2.0 从主路由剔除后，用户决策：**专门用作翻译子代理**（1M 上下文适合长文本翻译）。

### 变更

1. 新建 `~/.omp/agent/agents/translator.md`：翻译专用子代理，`model: longcat/LongCat-2.0`（显式指定，不受 fallbackChains 影响）、`thinkingLevel: minimal`、最小工具集（read/write/yield）
2. `config.yml` providers 段加回 `longcat: 2`（翻译并发限制）

### 验证

- frontmatter YAML 解析 OK；config/models.yml 均有效
- longcat provider 注册确认（LongCat-2.0，ctx=1048576）
- **OMP 重启后生效**：重启后 spawn `translator` 子代理应路由到 longcat/LongCat-2.0（探针验证）

### 使用方式（重启后）

```text
让 translator 翻译：<文本或文件路径> 到 <目标语言>
```

## 17. pi-hermes-memory 整合超时修复（2026-08-08 晚）

### 现象

频繁弹 `Auto-consolidation failed for 'failure': Consolidation subprocess was terminated (likely timeout or cancellation). Timeout: 180000ms`。

### 根因

- 记忆插件 pi-hermes-memory（v0.9.3）自动整合子进程默认超时 `DEFAULT_CONSOLIDATION_TIMEOUT_MS=180000`
- **163 个 `.MEMORY.md.recovery-*` 崩溃残留文件**（8/7 起每次整合超时留下）——长期反复失败
- failures.md 达 16.4KB（failure 记忆积累大）

### 修复

1. 新建 `~/.pi/agent/hermes-memory-config.json`：`{"consolidationTimeoutMs": 600000}`（10 分钟，字段名对照源码 config.ts:61/119-120 验证；**OMP 重启后生效**）
2. 清理 recovery 残留：163 → 保留最新 3 个（主记忆 MEMORY.md/USER.md/failures.md 完好）

### 影响

记忆从未丢失（recovery 机制本身即崩溃保护）；警告无碍系统运行。重启后若仍弹：需精简 failures.md 或深查整合子进程模型调用。

### 2026-08-09 跟进：根因定位 + llmModelOverride 修复

600s 仍弹报警。深查 `pi-hermes-memory` 源码（`src/handlers/auto-consolidate.ts` + `pi-child-process.ts` + `index.ts:248`）后定位根因：

- **整合子进程用 `modelRoles.default` = `zg-newapi-anthropic/claude-opus-5:max`**（thinking auto）——`llmModelOverride` 未配置时子进程继承默认模型
- 整合回合 = 完整 Pi CLI 对话：16KB failures.md 全量条目 + 要求多轮 memory 工具读写（每次写全量重写 + fingerprint 校验），多 OMP 实例并发写同文件还叠加写冲突重试
- 慢模型 × 大 prompt × 多轮工具 = 600s 完不成；08-08 的 180s→600s 只是延后被杀，没解决回合过重

**修复**（2026-08-09）：`hermes-memory-config.json` 加 `"llmModelOverride": "zg-newapi/deepseek-v4-flash"`（字段名对照 config.ts:149-151），整合子进程改用 flash 模型（~2s/回合）。**OMP 重启后生效**，验证方式：观察不再弹报警，或 `/memory-consolidate` 手动触发。

**澄清**：641 个 `.MEMORY.md.recovery-*` 不是 641 次失败——每次记忆写入都留 recovery 快照，插件 7 天宽限期（`RECOVERY_ACTIVE_GRACE_MS`）后才 prune，属设计行为，非故障指标。08-08 手工清理只是腾空间，对超时无帮助。

## 18. 日志告警扫描与优化（2026-08-09 凌晨）

### 扫描结果

| 告警源 | 发现 | 处置 |
|--------|------|------|
| guardian | **ch18 linxi-k40-opus5-backup 恢复测试持续超时**（13.7s，多次 12s 超时）——不在排除集，每周期白耗 | ✅ 加入排除集 |
| guardian | **ch57 gorouter 余额不足**（$0.05 < 预扣 $0.30，403 billing_error）——不在排除集，白耗 error scan | ✅ 加入排除集 |
| guardian | ch72 anyrouter 500 负载上限（gpt-5.6-sol）——瞬态，活跃渠道 | 观察（不可排除） |
| supervisor | cc-switch FileNotFoundError（12:57）——已修复的相对路径 bug 历史残留 | 无需动作 |
| health-check | **备份检查按"今日"误报**（凌晨 0-3 点昨日已过/今日未到） | ✅ 改为 24h 窗口 |
| OMP 主日志（8/9） | JSON 格式，0 条 warn/error | 干净 |

### 变更

排除集三处同步：`{2, 18, 57, 62, 63, 64, 65, 70, 71, 73, 74}`（guardian 运行时 + 镜像 + smoke）。guardian 已重启生效（pid 20732）。health-check 备份检查改最近 24h。

### 验证

20/20 ALL GREEN；guardian 心跳新鲜；语法全过。

## 19. claude-opus-5 渠道亲和配置（2026-08-09 凌晨）

### 背景

用户要求 claude 渠道配置亲和（百倍/林夕/agentrouter）。调查发现：百倍单点承载 opus-5（林夕 weight=0 闲置、aagent 全禁用）；林夕实测已恢复健康（2.2s）但被零权重闲置。

### 配置（NewAPI）

| 渠道 | status | weight | priority | 角色 |
|------|--------|--------|----------|------|
| ch3 百倍 | 1 | 20 | 50 | 主层（加权） |
| ch9 林夕 | 1 | 10（原 0） | 50（原 50→40→50） | 主层（加权） |
| ch45 agentrouter | **1（原 2 已启用）** | 5 | 40 | 兜底层 |

### 踩坑：NewAPI priority 语义是【数值大优先】

首次配置按"小优先"假设（百倍 p40 / agent 兜底 p50）→ 实测 3 请求全命中 agentrouter。反转后（主层 p50 / 兜底 p40）行为正确。**NewAPI 渠道选择：priority 降序（大优先），同 priority 按 weight 加权随机**。

### 验证（实测）

- 主层分流：近 8 请求分布 {ch9: 4, ch3: 2}——两渠道均参与（20:10 加权），林夕延迟 1.8-6s 健康
- 兜底触发：主层瞬时失败时 agentrouter 接住（28-53s，慢但不丢请求）
- 注意：agentrouter 兜底延迟高（其上游质量），仅作最后防线

### 管理说明

guardian 会继续自动管理这三渠道（测试失败自动降权/恢复）；本次为手动基线配置，guardian 权重闭环在其上运行。

## 20. opus-5 请求 400（上下文超限）根因与修复（2026-08-09）

### 现象

OMP 频繁报 `bad response status code 400`，原始请求存 `~/.omp/logs/http-400-requests/`（45 个，6 类）。

### 根因链（用户遇到的主类）

1. 8/8 渠道亲和配置前，opus-5 走 agentrouter/anyrouter（代理透传不检查上下文）；配置后**主流量走百倍（上游严格 200k 上限）**
2. OMP 认为 opus-5 窗口 200k（anyrouter 条目甚至宣称 1M）→ 长会话上下文膨胀到 200k+（实测 400 请求体 375KB→622KB/44 条消息）
3. 百倍上游拒绝超限请求 → **400**（与"Auto-shake 压缩不彻底"是同一问题两面）

### 修复（`~/.omp/agent/models.yml`，备份 `.bak-*-ctx`）

| 条目 | 原 | 现 | 理由 |
|------|-----|-----|------|
| zg-newapi-anthropic/claude-opus-5（3003 网关） | 200000 | **170000** | 压缩阈值 85% → 144.5k，更早压缩留安全边际 |
| anyrouter/claude-opus-5 | **1000000** | 200000 | 修正虚假宣称（代理透传但上游仍 200k），防未来参与时同坑 |

路由测试 32/32 OK。**OMP 重启后生效**。

### 其余 5 类 400（观察/已知）

- content-blocked（17x，上游审核拦截，无法修复）
- glm-5.2/deepseek 上下文超限（64k 输出配置 vs 模型上限，配置问题，模型未用）
- deepseek reasoning_content 未回传（2x，旧版）

## 21. OMP resume 无压缩——结构性限制（2026-08-09）

### 现象

OMP 重启后恢复 623KB 旧会话 → 首次续写 400（每次恢复都发生）。

### 机制证据

- compaction 决策日志全部是 `phase: post-agent-end`（agent 回合结束），**无 resume 阶段压缩**
- pi-coding-agent dist 无 compactBeforeResume/resumeCompaction 机制；CLI `--resume` 无 compact 选项
- **结论**：OMP 恢复会话不做压缩 = 结构性设计，非配置可修

### 处置规则

1. **新会话**：170k 窗口（§20 配置）在 agent_end 提前压缩，不再超限——根治
2. **存量超限会话**（修复前积累的大会话）：恢复一次 400 一次，**唯一处置是重开**，不可恢复续用
3. 恢复长期闲置旧会话前需评估其大小（OMP 恢复不压缩）

## 22. 成本优化：重试策略修复 + 渠道粘性路由（2026-08-09，社区实践）

### 依据

- NewAPI 维护者 Calcium-Ion（discussion #2097）：**单 key 单渠道才能触发上游输入 token 缓存；多 key 轮询很难触发**。实测 DeepSeek v3.2 exp 缓存命中 3M tokens = 0.7-0.8 元，无缓存 = 6+ 元（**8 倍成本差**）
- 原配置 `AutomaticRetryStatusCodes` 含 400-499 + `RetryTimes=5`：**400 请求错误也重试 5 次**——623KB 超限请求每次全量重发，纯浪费

### 变更

1. **重试策略**：`AutomaticRetryStatusCodes` → `408,429,500-503`（仅超时/限流/服务端错误重试，请求错误立即失败）；`RetryTimes` 5 → 3
2. **渠道粘性路由**（替代 §19 加权分流，缓存优先）：

| 渠道 | weight | priority | 角色 |
|------|--------|----------|------|
| ch3 百倍 | 20 | **51** | 粘性主渠道（缓存命中率最大化） |
| ch9 林夕 | 10 | 50 | 备（百倍故障/超时切换） |
| ch45 agentrouter | 5 | 40 | 兜底 |

### 验证

实测 3 请求 opus-5 → **3/3 全命中百倍**（粘性 100%），延迟 3.5-12.5s。

### 效果

- 400 类请求不再 5 次重发（省 5 倍无效流量）
- opus-5 请求固定走百倍上游 key → Anthropic prompt cache 命中率最大化（CacheRatio 已支持 0.1x 缓存计费）
- 代价：百倍慢时请求滞留（NewAPI 超时切换兜底）——接受（成本优先）

## 23. 参数覆盖与 memory.backend 核查结论 + deepseek 窗口修复（2026-08-09）

### 核查结论（两项均不做，有依据）

1. **参数覆盖（temperature/thinking）不配置**：45 个 400 中仅 4 个参数类——2 个是 deepseek reasoning_content 未回传（客户端多轮问题，参数覆盖无法修）、2 个是输出超限（见下）。盲目覆盖 temperature 会改变 coding 行为且无收益。
2. **OMP memory.backend 保持 off**：pi-hermes-memory 已确认是 **system prompt 注入型**记忆（preview-context.ts inject）；开启 OMP 内置 memory 会**双重注入**（token 翻倍 + 冲突）。不叠加。

### 新发现并修复：deepseek 窗口虚假声明（同 anyrouter 1M 问题）

- 400 详情：deepseek-v4-flash "max context 393216, requested 64000 output + 329217 input = 393217"——**超 1 token**
- 根因：models.yml `deepseek-v4-flash/deepseek-official-v4-flash` contextWindow=500000 **大于上游实际 393216** → OMP 压缩阈值 425k 超过实际可用 → 输入 329k+ 时输出 64k 必超限
- 修复：contextWindow 500000 → **380000**（压缩阈值降至 ~323k，输入不再触及危险区）。**OMP 重启后生效**
- glm-5.2 同类 400（3 次 8/2）：该模型未在 models.yml 注册（非 OMP 发起），无需动作

## 24. deepseek 官方直连粘性 + relay 缓存实测（2026-08-09）

### deepseek 粘性官方直连（已配置）

deepseek-v4-flash 原在 ch42（官方直连 api.deepseek.com，w5）与 ch48（opencode-go relay，w20）间同优先级轮换 → 官方渠道仅分到 20% 流量。改为 **ch42 p51 粘性主 / ch48 p50 备**。实测 3/3 命中官方，延迟 0.7-0.9s（比 relay 2.4s 快）。

### relay 是否透传 prompt cache——实测（用户提出多 key 亲和质疑）

方法：绕过 NewAPI，用单 key 直发两次相同长前缀请求（10k tokens + cache_control），看 Anthropic 响应 usage 的 cache_creation/cache_read 字段：

| 渠道 | 第 1 次 | 第 2 次 | 结论 |
|------|--------|--------|------|
| 百倍 ch3（relay 6 key） | cache_creation=0 | cache_read=0 | **不透传缓存**（单 key 也无效） |
| 林夕 ch9（relay 2 key） | 502 | 502 | 上游故障 |
| deepseek 官方 ch42 | cache_hit=0（写入） | **cache_hit=1920（96% 命中）** | **缓存真实生效** |

### 结论（2026-08-09 二次修正：百倍/林夕 = Kiro 反代，官方来源佐证）

用户指出百倍/林夕是 **Kiro 反代**，且 OMP 状态栏显示 1/103 缓存命中（101.2k/11.5M，非 0）。此前"不透传缓存"结论不准确。GitHub 权威因果链（CLIProxyAPIPlus #125，closed）：

1. 客户端发 `cache_control` ✓
2. **Kiro 请求翻译层（BuildKiroPayload）丢弃 cache_control**（不转发到 Kiro 私有协议）
3. Kiro 后端按独立未缓存请求处理 → 不返回 tokenUsage 缓存字段
4. 响应侧无数据源 → cache_read/creation 恒 0

即：**Kiro 反代默认丢 cache_control，缓存基本无效**；仅优化版反代实现（80aj：优化 kiro.rs 适配 Prompt Cache 语义）可透传。百倍/林夕实测 1% 命中 = 其反代实现部分/偶发透传（或后端偶发命中）。

修正后结论：
1. 多 key 轮换破坏粘性的原理成立；Kiro 反代缓存收益 ≈0（1%）→ 拆单 key 无意义
2. deepseek 缓存收益在官方直连（96%）与 opencode-go（98%，OpenAI 自动前缀缓存，非 Kiro 路径）
3. claude 渠道保持百倍粘性（故障收敛用途，缓存收益 ≈0）
4. 百倍 relay 有 Cloudflare 防护，直连需浏览器 UA（error 1010）

## 25. deepseek 路由反转：opencode-go 主力 / 官方备用（2026-08-09，用户决策）

§24 曾配置官方直连粘性（缓存收益 ~1/10）。**用户决策：优先消耗 opencode-go 套餐额度，官方作备用**——反转：

| 渠道 | weight | priority | 角色 |
|------|--------|----------|------|
| ch48 opencode-go-flash | 20 | **51** | 主力（消耗套餐） |
| ch42 deepseek-official | 5 | 50 | 备用（opencode-go 故障/限流接管） |

实测 3/3 命中 opencode-go，延迟 1.8-2.3s。

**重要修正（用户指出后实测）**：opencode-go **透传 DeepSeek 输入缓存**——单 key 直发两次相同长前缀，第 2 次 `prompt_cache_hit_tokens=2048/2086`（**98% 命中**）。§24"relay 不透传缓存"的结论仅适用于百倍/林夕，**opencode-go（opencode.ai 官方 zen 服务）是例外**。且 ch48 为**单 key**（无轮换破坏）。

**最终结论：当前配置三重收益全占**——消耗套餐额度 + 缓存折扣（98% 命中）+ 官方兜底。无需调整，用户决策恰为最优解。

## 27. NewAPI + OMP 配置全面审查（2026-08-09，官方来源对照）

### 方法

全量 dump NewAPI options + OMP `config list --json`（450 项 schema），逐项对照官方文档/GitHub issue，只采纳有官方来源佐证的改动。

### OMP：实施 5 项（`omp config set`，schema 校验落盘）

| 配置 | 原值 | 新值 | 依据 |
|------|------|------|------|
| `display.cacheMissMarker` | false | **true** | 官方 schema：缓存未命中时在该轮上方显示分隔线——缓存优化效果直接可视 |
| `display.showTokenUsage` | false | **true** | 每轮显示 token 用量——成本可视 |
| `task.showResolvedModelBadge` | false | **true** | 子代理 widget 显示实际解析的模型 ID——直接支持"commit/tiny 首触发复核"待办 |
| `edit.streamingAbort` | false | **true** | patch 预览失败时中止流式 edit 调用，省 token（失败编辑提前返回） |
| `task.maxRuntimeMs` | 1200000 | **1800000** | 修复实测问题：reviewer 子代理 20 分钟被强杀；30 分钟 + softRequestBudget=80 双护栏 |

### OMP：评估后不改（附依据）

- `compaction.idleEnabled`（空闲压缩）：阈值 200k > opus 窗口 170k，常规压缩先触发，开了无用
- `autolearn.enabled`：额外消耗 token 且与 hermes 记忆双重注入（既有决策）
- `providers.streamIdleTimeoutSeconds`/`streamFirstEventTimeoutSeconds` = -1（用 provider 默认）：无挂流证据，claude thinking 首事件可超 60s，不设死
- `bash.autoBackground.enabled`：工作流变化大，留给用户决策
- OMP 版本 17.2.11 = npm 最新（2026-08-07 发布）

### NewAPI：全部不改（官方来源否决）

1. **claude/deepseek 不加渠道亲和规则**——三重证据：① priority 路由已实现渠道级粘性（ch3 p51 / ch48 p51 全量命中）；② key 级亲和 issue #5992 被官方关闭（not planned），亲和只绑渠道不绑 key，百倍多 key 轮缓存照样破；③ BER 博客实测坑：亲和会把请求钉死在故障渠道上（`keep_on_channel_disabled=false` 才缓解）。加了零收益还引入故障钉死风险
2. **渠道透传（pass_through）不开**——issue #2796 的低缓存命中仅限 Codex/Responses 路径；本机 deepseek 实测 98% 命中（chat/completions 路径无前缀破坏）；透传会绕过 model mapping，claude 渠道全部依赖 mapping，风险远大于收益
3. **StreamCacheQueueLength=0**：官方文档/源码未查到语义（本机 v0.0.0 自编译版），无依据不动
4. **checkin 签到**：本机 build 渠道表无 checkin 列，功能不可用
5. 既有项复核全部合理：RetryTimes=3 + 408,429,500-503、自动禁用 401/402/403/502+余额关键词、monitor 自动测试关闭（guardian 接管）、日志清理开、磁盘缓存开、claude.thinking_adapter 开

### 观察项（非本次范围）

- `qwen3.8-max` 近 24h 158 请求 / 45M prompt tokens——非 OMP 角色（疑 Kimi Code 等其他工具），若为付费模型是最大单项成本，建议排查来源

## 待办

1. **commit/tiny 首触发复核**：非 agent 角色无法探针验证；首次触发时查 NewAPI consume log（commit 应显示 `claude-haiku-4-5 → agnes-2.0-flash`、tiny 应显示 `hy3-preview-agent`）。
2. **reviewer/security-reviewer 已还原 @slow**（2026-08-08 下午，claude-opus-5 429 实测恢复后还原；agentrouter/gpt-5.6-sol 覆盖已移除）。
3. 计划任务（LogonTrigger）登录时仍会拉起 pythonw relay，与 supervisor 管理的 python.exe 双实例共存（Windows SO_REUSEADDR 双绑，无冲突）；如需单一管理源，可停两个计划任务让 supervisor 全权接管（未执行，保持现状）。
4. **ch73/74 relay 渠道类型（OpenAI vs Responses）**：relay 只接受 /v1/responses，NewAPI 按 OpenAI 类型发 chat/completions 探测 → 405 阻碍渠道自动恢复；当前两渠道均禁用且上游有真实问题（zzzcoding 405），待上游恢复后若 NewAPI 支持 Responses 渠道类型再调整。

## 相关文件

- `~/.omp/guardian/proxies-supervisor.py`（+ `.bak-20260808-114158-add-relay`）
- `~/.omp/agent/config.yml`、`~/.omp/agent/models.yml`
- `~/.omp/guardian/secrets.json`（key 来源，不落明文）
