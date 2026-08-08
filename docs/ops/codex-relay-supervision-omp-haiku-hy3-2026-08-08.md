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

## 待办

1. **commit/tiny 首触发复核**：非 agent 角色无法探针验证；首次触发时查 NewAPI consume log（commit 应显示 `claude-haiku-4-5 → agnes-2.0-flash`、tiny 应显示 `hy3-preview-agent`）。
2. **reviewer/security-reviewer 已还原 @slow**（2026-08-08 下午，claude-opus-5 429 实测恢复后还原；agentrouter/gpt-5.6-sol 覆盖已移除）。
3. 计划任务（LogonTrigger）登录时仍会拉起 pythonw relay，与 supervisor 管理的 python.exe 双实例共存（Windows SO_REUSEADDR 双绑，无冲突）；如需单一管理源，可停两个计划任务让 supervisor 全权接管（未执行，保持现状）。
4. **ch73/74 relay 渠道类型（OpenAI vs Responses）**：relay 只接受 /v1/responses，NewAPI 按 OpenAI 类型发 chat/completions 探测 → 405 阻碍渠道自动恢复；当前两渠道均禁用且上游有真实问题（zzzcoding 405），待上游恢复后若 NewAPI 支持 Responses 渠道类型再调整。

## 相关文件

- `~/.omp/guardian/proxies-supervisor.py`（+ `.bak-20260808-114158-add-relay`）
- `~/.omp/agent/config.yml`、`~/.omp/agent/models.yml`
- `~/.omp/guardian/secrets.json`（key 来源，不落明文）
