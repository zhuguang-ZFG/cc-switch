# NewAPI Guardian — 自愈监控系统

完整的健康检查、自动修复、Telegram 报警、每日报告、权重闭环自愈系统。

## 功能

- **健康检查**: 15 秒间隔监控 NewAPI、渠道、本地代理
- **错误渠道扫描**: 每 5 分钟分批主动探测 402/401/502 等瞬间返回的错误（慢渠道检测无法覆盖）
- **自动修复**: 慢渠道降权→禁用、错误渠道直接禁用；NewAPI 故障只告警（本地服务自动重启由独立 watchdog 负责）；本地代理的周期性推理探针已禁用（省上游费用），崩溃复活由 proxies-supervisor/watchdog 负责，Telegram `/restart` 手动重启仍可用
- **权重闭环自愈**: 渠道恢复 → 自动启用 → 加入聚合池 → 仅恢复 weight → 性能监控 → 自动降权/禁用；priority 保留人工策略
- **OMP 角色观测**: 每 20 分钟主动探测角色端点并报警；Guardian 不自动修改 OMP `modelRoles`，避免覆盖人工路由策略
- **Telegram 报警**: 实时报警、每日健康报告、余额趋势预警
- **指标导出**: JSON metrics 文件，可与其他监控系统集成

## 监控指标

| 指标 | 阈值 | 动作 |
|---|---|---|
| NewAPI 健康 | `/api/status` 连续 3 次非 200 | 只告警（自动重启已禁用；本地服务由 LocalNewAPI-Watchdog 复活） |
| 渠道响应时间 | 3 个不同测试结果均 > 60 秒，且主动复测失败 | 降权 → 禁用 |
| 渠道错误码 | 402/401/502 + 关键词匹配 | 直接禁用 |
| 本地代理健康 | 周期探针已禁用 | 不触发（复活由 proxies-supervisor 负责；`/restart` 手动可用） |
| 错误率 | > 10% | 报警 |
| 余额 | < 100 万 quota | 报警 |
| 余额趋势 | 预计 < 24h 耗尽 | 预警 |

## 权重闭环自愈流程

```
渠道故障
  ├─ 慢渠道: 3 个不同 test_time 均 > 60s，主动复测失败后降权
  ├─ 全量扫描软错误: 连续 3 次失败后降权
  ├─ 硬错误: 402/401/502 + 关键词匹配后直接禁用
  └─ 禁用后
       ├─ 至少等待 5 分钟冷却，并按失败次数指数退避
       ├─ 恢复验证: 3 次 test_channel，至少 2 次通过
       ├─ 自动启用 + 加入聚合池
       │    ├─ 从 weight_history 恢复权重/优先级
       │    ├─ PUT /api/channel/ 同步 abilities
       │    ├─ 同模型健康渠道 ≥5 时，恢复权重不超过同伴平均值
       │    └─ 仅为对应本地 provider 恢复 OMP 角色
       └─ 加入后 10 分钟稳定性监控
            ├─ 每 45 秒 test_channel
            ├─ 连续 2 次失败 → 禁用 status=2，并重新进入恢复队列
            └─ 稳定 → 清理监控记录
```

## 自动权重调整

| 条件 | 动作 |
|---|---|
| 20 个不同测试结果的成功率 < 80% | 降权 (weight × 0.5) |
| 20 个不同测试结果的平均响应 > 45s | 降权 (weight × 0.5) |
| 已降权渠道成功率 ≥ 95% 且响应 < 10s | 每个完整窗口加权 1，最多恢复到历史权重 |
| 权重已降至 1 仍不健康 | 禁用 |

## 错误渠道扫描

`check_channel` 只检查 `response_time`，402 余额不足瞬间返回（rt < 1s）不会触发慢渠道检测。
`scan_error_channels` 每 5 分钟分批用 `test_channel` 主动探测，匹配以下关键词后自动禁用：

- 余额不足 / INSUFFICIENT_BALANCE / credit balance / quota
- 402 / 401 / invalid

## NewAPI smoke 管理会话

`newapi-local-smoke.py` 复用 `.admin-token-cache.json` 中的管理令牌，避免定时任务反复创建持久化 session 并触发 `AUTH_SESSION_LIMIT`。缓存校验仅在 HTTP 401 时重新登录；HTTP 403 表示权限问题，429/5xx/网络错误表示瞬态故障，这些响应均保留缓存并使本轮 smoke 失败。

## NewAPI 侧配置

Guardian 依赖以下 NewAPI 设置（已通过 API 配置）：

| 设置 | 值 | 说明 |
|---|---|---|
| `AutomaticDisableStatusCodes` | `401,402,403,502` | 自动禁用的 HTTP 状态码 |
| `AutomaticDisableKeywords` | 余额不足, INSUFFICIENT_BALANCE, credit balance, ... | 自动禁用的错误关键词 |
| `AutomaticEnableChannelEnabled` | `true` | NewAPI 可自动启用；Guardian 会重新验证并在不稳定时禁用 |
| `AutomaticDisableChannelEnabled` | `false` | 本机由 Guardian 统一负责主动隔离；显式关闭，禁止保留歧义值 `<nil>` |
| `AutomaticRetryStatusCodes` | `408,500-503` | 仅重试瞬时超时/服务端错误；认证、余额和 429 交给客户端退避/OMP 回退，避免嵌套放大 |
| `ChannelDisableThreshold` | `90` | NewAPI 自动测试的慢响应阈值（秒），不是连续失败次数 |
| `RetryTimes` | `1` | NewAPI 内层最多重试一次，避免与 OMP fallback 叠乘 |

本机关闭 NewAPI 内置定时渠道测试以控制探测成本，因此 Guardian 每轮会把 `status=3 && auto_ban=1` 的渠道同步到自身恢复队列。同步不立即启用：先等待至少 5 分钟，再执行 3 次探测、至少 2 次通过、恢复权重和 10 分钟稳定性回滚。手工 `status=2`、`auto_ban=0` 以及明确隔离的 2/62/63/64/65 不进入自动恢复。

计划任务 `CCSwitch-NewAPI-DX-Ops` 每轮运行 `newapi-local-smoke.py`。`newapi-smoke-alert.py` 在首次失败和失败后的首次恢复时发送一次 Telegram 告警；投递失败不落状态，下一轮继续尝试，避免“任务一直红但无人知道”。容量门禁只计算 `status=1 && weight>0` 的真实可路由渠道。

## 安装

### 1. 配置

将密钥写入 `~/.omp/guardian/secrets.json`，或使用同名大写环境变量覆盖：

```json
{
  "newapi_base": "https://your-newapi.example",
  "newapi_token": "...",
  "newapi_user": "1",
  "telegram_token": "...",
  "telegram_allowed_users": "5345665818",
  "codebuddy_api_key": "...",
  "agentrouter_proxy_key": "...",
  "anyrouter_proxy_key": "...",
  "local_proxy_bind_host": "0.0.0.0"
}
```

⚠️ `guardian.py` 与 `proxies-supervisor.py` 都在**进程启动时**把 secrets 读成模块级常量
（含 `local_proxy_bind_host` 与各代理 key）。改 `secrets.json` 后必须重启两个看护进程，
否则 watchdog 自愈会用旧值回弹（2026-08-15 agentrouter 8788 只绑 Tailscale 事故，
见 `docs/ops/agentrouter-bind-host-fix-2026-08-15.md`）。用脚本一次完成：

```powershell
# 看护级配置（bind host 等）：代理进程不动
powershell -NoProfile -ExecutionPolicy Bypass -File ~\.omp\guardian\apply-secrets-restart.ps1
# 代理 key/env 变更：连代理一起 bounce（supervisor 立即用新 env 拉起）
... -Proxy agentrouter
```

`local_proxy_bind_host` 默认 `0.0.0.0`：本机客户端走 loopback、NewAPI 经 Tailscale
地址访问本地代理，二者都要服务；改为具体网卡地址会让另一侧全部连接拒绝。

`telegram_allowed_users` 为逗号分隔的 Telegram 用户 ID 白名单；未配置时仅接受私聊 owner
（`from.id == chat.id`），群组成员命令一律拒绝。


### 2. 启动

```bash
python guardian.py
```

Windows 以 `NewAPI Guardian` 计划任务作为 Guardian 的规范启动入口，以 `NewAPI Guardian Watchdog` 计划任务常驻 `watchdog.ps1`。watchdog 仅在心跳超过 180 秒且精确核验心跳 PID 后终止卡死实例，再通过 Guardian 计划任务重新拉起。watchdog 进程使用命名互斥，计划任务采用 `IgnoreNew`、允许电池供电运行且电源切换时不中止，重启尝试有 5 分钟退避；旧 Startup 入口即使重复触发也会立即退出。

本地代理看护 `proxies-supervisor.py` 的活跃入口是 Startup 的 `LocalAIProxies-Supervisor.lnk`
（conhost --headless + `start-proxies-supervisor.bat`）；同名计划任务为 Disabled 遗留。
watchdog.ps1 同时监视 supervisor 的 `supervisor-status.json` 心跳（stale 180s → 精确核验 PID 后拉起）。

## Telegram 命令

- `/status` — 查看当前系统状态（含余额趋势）
- `/channels` — 列出所有渠道状态（含权重）
- `/report` — 立即生成健康报告
- `/restart <proxy>` — 手动重启本地代理
- `/enable <channel_id>` — 手动启用渠道
- `/disable <channel_id>` — 手动禁用渠道
- `/help` — 显示帮助

## 文件

| 文件 | 说明 |
|---|---|
| `~/.omp/guardian/guardian.py` | 主程序 |
| `~/.omp/guardian/secrets.json` | 本机密钥配置，不应提交版本库；改后须跑 apply-secrets-restart.ps1 |
| `~/.omp/guardian/proxies-supervisor.py` | 本地代理端口看护（8788/8789/3003/15721/15999/16000/16001），每轮写 supervisor-status.json 心跳 |
| `~/.omp/guardian/start-proxies-supervisor.bat` | supervisor 启动脚本（Startup lnk 目标） |
| `~/.omp/guardian/start.bat` | Guardian 手动/调试用启动脚本（带退出码 75 外的 10s 重试循环；生产入口为计划任务） |
| `~/.omp/guardian/apply-secrets-restart.ps1` | 改 secrets.json 后重启 Guardian + Supervisor（可选 bounce 指定代理）；必须使用，否则旧配置回弹 |
| `~/.omp/guardian/supervisor-status.json` | supervisor 心跳（ts + pid + bind_host + restarts_today） |
| `~/.omp/guardian/anyrouter-window-canary.py` | anyrouter Claude 池开窗哨兵：计划任务 `AnyRouter Window Canary` 每 30min 触发一次（即跑即退），haiku/16 tokens 探测 8789 桥，仅 closed→open 跳变发 Telegram。门禁 test_omp_routes.py:487 禁止自动挂链，开窗后人工显式选用 |
| `~/.omp/guardian/anyrouter-canary-state.json` | 哨兵状态（上次 open/closed + 细节），防重复告警 |
| `~/.omp/agent/` | OMP 配置目录，**独立本地 git 仓**（2026-08-15 起，禁加 remote——models.yml 含明文 key）；改 config.yml/models.yml 后即 commit，取代 .bak 手工备份 |
| `~/.omp/guardian/proxies-supervisor.log` | supervisor 运行日志 |
| Startup `LocalAIProxies-Supervisor.lnk` | supervisor 唯一活跃启动入口（同名计划任务 Disabled） |
| `~/.omp/guardian/heartbeat.json` | 心跳（Guardian.run() 每轮原子写 ts+pid） |
| `~/.omp/guardian/guardian.log` | 日志（RotatingFileHandler, 5MB × 5） |
| `~/.omp/guardian/state.json` | 状态（禁用/降权/加入/权重历史） |
| `~/.omp/guardian/metrics.json` | 指标导出 |
| 计划任务 `NewAPI Guardian` | Guardian 唯一规范启动/恢复入口 |
| 计划任务 `NewAPI Guardian Watchdog` | 登录触发，单实例常驻；自身失败最多重启 3 次、间隔 1 分钟；电池供电不停止 |
| `~/.omp/guardian/watchdog.ps1` | Guardian watchdog：心跳超 180s 后精确核验并终止卡死进程，通过 `NewAPI Guardian` 计划任务拉起；重启退避 5 分钟 |
| `~/.omp/guardian/watchdog.log` | watchdog 运行日志 |
| `~/.omp/guardian/mistral-relay-16001/mistral-conversations-relay.py` | OpenAI↔Mistral `/v1/conversations` 转换 relay（127.0.0.1:16001，源：`scripts/ops/mistral-conversations-relay.py`） |

## 本地格式转换 relay

`mistral-conversations-relay.py`（部署 `~/.omp/guardian/mistral-relay-16001/`，supervisor 条目
`mistral-relay-16001`）：api.mistral.ai 的 `glm-5-2`/`zai-glm-5-2` 仅经私有 `/v1/conversations`
提供（标准 `/v1/chat/completions` 恒 429），本 relay 在 127.0.0.1:16001 做 OpenAI↔conversations
双向转换，供 NewAPI 渠道 **ch85**（type=1，base_url=`http://127.0.0.1:16001`——NewAPI 自拼
`/v1/chat/completions`，base_url 不得带 `/v1`）。要点：

- 上游真 key 在 `secrets.json[mistral_glm_key]`；relay 仅绑回环、不鉴权，渠道 key 为占位符
- `stream:true` 直通上游 SSE（started/delta/done → OpenAI chunk）；上游流建立失败回退缓冲合成
- tools/非文本多模态 fail-loud 400，不静默丢能力
- keep-alive 陷阱：404/GET 路径必须排干 Content-Length，否则残留 body 被解析成下一条请求行报 501
- 渠道创建/验证脚本：`scripts/ops/add_mistral_glm_channel.py`（幂等，重跑仅回读验证）
- ooioo 备用渠道脚本：`scripts/ops/add_ooioo_gpt56sol_channel.py`（ch87，priority 30，幂等；key 走 argv 不入仓，runbook 见 `docs/ops/ooioo-gpt56sol-channel-2026-08-16.md`）
- runinfra qwen3-8-27b 渠道脚本：`scripts/ops/add_runinfra_qwen_channel.py`（ch88；上游硬拒 `prompt_cache_key`，已配 param_override delete 剥离，runbook 见 `docs/ops/runinfra-qwen-via-newapi-2026-08-16.md`；PUT 渠道会清 key 的坑见该文档）
- seeseed1ck hydrogel 渠道脚本：`scripts/ops/add_seeseed_hydrogel_channel.py`（ch89；GLM-5.3/grok-4.6/grok-chat-fast/mimo-v2.5，仅收录直连实测存活模型；param_override 删 `enable_thinking`——GLM-5.3 强制思考拒 `enable_thinking:false`，runbook 见 `docs/ops/seeseed-hydrogel-channel-2026-08-16.md`）
- t1qq sol 兜底渠道脚本：`scripts/ops/add_t1qq_sol_channel.py`（ch90，双 key 轮询，priority 20 垫底；fork 多 key 三连坑——`multi_to_single` 创建仍不落 `is_multi_key`、PUT 会整体重生成 channel_info 冲掉 DB 修复、可用配方=完整 BLOB 直写+等 60s 缓存同步，runbook 见 `docs/ops/t1qq-sol-channel-2026-08-16.md`）
- Sol 主备：ch92 `zzzcoding-gpt-5.6-sol` 为 p60/w15 主力，ch91 `jianzhile-gpt-5.6-sol` 为 p55/w5 严格第二，ch83 `muyuan-sol` 保留 p50/w5 及原状态。`scripts/ops/update_zzzcoding_sol_primary.py [--apply]` 默认 dry-run，以完整性校验快照、原子 channel/abilities 双写、75 秒缓存等待和失败回滚执行切换；`scripts/ops/verify_zzzcoding_sol_primary.py` 连续验证 ch92 Responses 探针、聚合 SSE 语义与 `channel_id=92` 日志归属。两条主备上游都按 channel-local policy 将 OMP Chat 转为 Responses，Guardian 使用 `endpoint_type=openai-response&stream=true`；zzzcoding 另需 `parallel_tool_calls=false`。runbook 见 `docs/ops/zzzcoding-gpt56sol-primary-2026-08-17.md`，ch91 历史见 `docs/ops/jianzhile-gpt56sol-channel-2026-08-17.md`。
- Sol 硬化工具：`remove_omp_default_fallback.py [--apply]` 只删除当前默认 selector 的精确 fallback；`drill_sol_failover.py [--preflight|--apply]` 在 `finally` 恢复 ch92 状态和三层姿态；`rollout_agentrouter_sol.py [--apply]` 仅在两次管理探针和 OMP 精确语义通过后启用 ch45；`monitor_sol_semantic.py` + `register-sol-semantic-monitor-task.ps1` 提供 30 分钟、`IgnoreNew`、无自动修复的 TTFT/语义监控；`campaign_sol_context.py --run` 以服务端 `prompt_tokens` 执行 200k/280k/340k/380k/396k 阶梯、约 +/-8k 边界二分和两个工具形态点。所有变更工具默认 dry-run，大上下文工具还要求显式 `--run`。

## sol 链劣化与亲和迁移（2026-08-16/17）

- ch83 muyuan 在载荷下持续 504/流停滞，小探针盲区 Guardian 不会禁用；`RetryTimes=1` 为刻意预算勿上调
- 计费空流（prompt 计费 completion=0）无配置层重试覆盖；监控锚点见 `docs/ops/sol-chain-muyuan-degradation-2026-08-16.md`
- 已开 `channel_affinity_setting.switch_on_success=true`：failover 成功后亲和钉迁移到健康渠道（修复前钉死 300s="muyuan 一挂就停"）；故障转移实测演练证据见该文档

## 机器治理（2026-08-17，RCA 见 `docs/ops/boot-popup-storm-tunnel-2026-08-17.md`）

- 开机弹窗：edge-tunnel vbs 死循环（VPS 残留转发占 3000）为最大源，已加 `conhost --headless`+指数退避；残余 powershell `-WindowStyle Hidden` 闪窗项全部改 `conhost --headless`
- `~/spike_catcher.py`：CPU≥70% 尖峰自动采样凶手名单写 `~/spike-catch.log`（开机自启、单实例锁）；`~/popup_hunter.py` 记录控制台进程诞生链
- Chrome/Edge 预加载自启已删（备份 `~/.omp/guardian/removed-autolaunch-backup.txt`）；火绒信任区含 .bun/.omp/.claude/.new-api-local/scoop\\apps/cc-switch，衍生爆发实测 AV CPU 不起跳


## 安全

- Telegram 命令鉴权：chat_id + 发送者白名单（`telegram_allowed_users`）双重校验，群组成员默认拒绝
- NewAPI 容器重启需连续 3 次探测失败才触发，避免瞬态抖动误重启；SSH 走 argv 调用，无本地 podman fallback
- 所有自愈动作都发送 Telegram 通知，可随时人工干预
- 自动禁用/启用渠道需要 NewAPI 管理权限
- 单轮预算 `CYCLE_BUDGET_SEC=90`：故障时跳过低优先级步骤（错误率/余额/指标/全扫/cleanup/报告），稳定性回滚与代理重启始终执行
- state.json 损坏时快照备份（`state.json.corrupt-<ts>-<pid>`，保留 5 份），不静默丢弃

## 参考

- NewAPI 源码: https://github.com/QuantumNous/new-api
- NewAPI Channel.Update() → UpdateAbilities(nil): abilities 表自动同步证据
- OpenClaw WatchDog: https://clawhub.ai/abdullah4ai/openclaw-watchdog

## Untrusted provider conformance canary

`probe_untrusted_openai_provider.py` is a default-dry-run, serial compatibility
probe for APIs with unverifiable model names. It reads the key only from an
environment variable and emits no prompt/response bodies. The live suite
checks catalog ids, JSON versus SSE behavior, response-model mismatches,
alias collapse, usage/cache accounting, and one required tool call. It never
creates a NewAPI channel or edits OMP. The separately managed
`sotamodel-canary` OMP provider is manual-only; its 1M context and 128K output
metadata are permissive client test limits, not verified upstream capability.
`test_omp_routes.py` keeps `sotamodel*` out of roles and automatic fallbacks. See
`docs/ops/untrusted-openai-provider-canary.md`.

```powershell
python3 scripts/ops/probe_untrusted_openai_provider.py
python3 scripts/ops/probe_untrusted_openai_provider.py --run
```

## OMP unexpected-stop guard

`omp-unexpected-stop-guard.js` uses OMP's supported `session_stop` extension event to resume a main-agent turn that explicitly promises immediate work but ends without a tool call. It is deterministic, supports Chinese and English promises, rejects completion/question/blocker language, and caps a continuation chain at three turns.

Validation:

```powershell
node --test scripts/ops/test_omp_unexpected_stop_guard.js
```

Production copy: `~/.omp/agent/extensions/omp-unexpected-stop-guard.js`. See `docs/ops/omp-unexpected-stop-guard.md` for deployment, reload, limitations, and rollback.

## OMP global compaction model

`omp-global-compaction-model.js` keeps the selected main model unchanged while
projecting an authenticated background target onto every available model as
its runtime `compactionModel`. The ordered candidates are SenseNova DeepSeek V4
Flash, a reserved SenseNova GLM 5.2 selector, Mistral-relay GLM 5.2, then
official LongCat 2.0. It reconciles at
session start, before each agent turn, before compaction, and once per second so
models added or refreshed in a running OMP session inherit the policy without
per-model maintenance. A strict provider failure cools the failed target for
five minutes and may schedule one managed backup compaction against the next
eligible candidate. Aborted, skipped, cancelled, local, and unknown failures do
not retry. When all authenticated candidates are cooling, automatic compaction
fails closed instead of bypassing cooldown or using the selected main model.

Compaction state is written only to structured background logs. The optional
`/compaction-status` command shows the extension revision, last result, managed
retry state, and per-candidate availability/cooldown/counters. Raw upstream
errors are reduced to a safe class and optional HTTP status. Image payloads
remain excluded by OMP's text serializer; the extension records only an
image-block count and never logs image data or URLs.

The ch77 XiaoHongShu/Dots model passed live text, screenshot OCR, streaming,
and 50K-input probes, but remains outside this compaction chain: ordinary OMP
compaction strips image bytes before the model call, Dots is limited to a 128K
window, and its 50K probe was about three times slower than SenseNova DeepSeek.
Use it for vision tasks, not as a selector-only image-compaction workaround.

Validation:

```powershell
node --check scripts/ops/omp-global-compaction-model.js
node --test scripts/ops/test_omp_global_compaction_model.js
node --test scripts/ops/test_omp_global_compaction_deploy.js
py -m unittest scripts.ops.test_omp_routes
```

Production copy: `~/.omp/agent/extensions/omp-global-compaction-model.js`.
Deploy with `deploy-omp-global-compaction-model.ps1`; it backs up or records an
absence marker, atomically replaces the extension, verifies SHA-256, and rolls
back on failure. Keep the repository and production hashes identical. A
running OMP process must execute `/reload-plugins` or restart normally before newly
deployed extension code is active; do not kill an active session merely to
load it. Detailed evidence and rollback are in
`docs/ops/omp-global-compaction-model.md`.

## OMP SOTA escalation layer

`omp-sota-escalation.js` discovers authenticated `omp-sota-*` model aliases
and runs at most one ephemeral, read-only child OMP review for explicit,
high-risk, complex, or repeated-tool-failure turns. It never switches the main
model and never enters ordinary fallback or compaction routing. `/sota-status`
reports only bounded operational state.

For the `hutuji` workspace, the same extension compares Git object hashes at
turn start and terminal settle, then merges successful OMP `edit`/`write`
paths so external firmware mutations remain visible. It classifies only files
changed during that turn. Contract, gate, deploy, MCP transport, and G-code
paths can trigger the bounded SOTA review even when the prompt omits risk
words. A separate non-triggering message selects the required `docs`, `hub`,
or `full` gate; `full` fails closed without `GRBL_ROOT`. The extension selects
and reports the gate but does not execute hardware, firmware, deployment, or
production work. `/hutuji-gate-status` shows the last plan.

Repository-owned worker templates are under `omp-agents/`. Both the primary
`hutuji-worker` and legacy `dsv4pro-worker` compatibility name resolve through
`@task`, so later `modelRoles.task` changes propagate without another agent
edit. Their contract preserves dirty worktrees and distinguishes software gate
results from HIL/deployment evidence. Deploy both atomically with
`deploy-omp-hutuji-workers.ps1`; the script validates role inheritance, creates
per-file backup/absence records, and rolls both files back on failure.

NewAPI aliases are managed by
`add_omp_sota_newapi_alias.py [--channel-id ID] [--base-model MODEL]
[--apply|--remove --apply]`; the default is dry-run. OMP registration uses
`add_omp_sota_model.mjs [--apply]`, and the independent production extension
is deployed with `deploy-omp-sota-escalation.ps1`. See
`docs/ops/omp-sota-escalation-layer.md` for the complete contract and rollback.
`probe_omp_sota_alias.py [--run]` is the opt-in, eight-token semantic and log
attribution check; it never prints the client key or raw error bodies.
