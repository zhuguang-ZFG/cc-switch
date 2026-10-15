# NewAPI Guardian — 自愈监控系统

完整的健康检查、自动修复、Telegram 报警、每日报告、权重闭环自愈系统。

## 功能

- **健康检查**: 15 秒间隔监控 NewAPI、渠道、本地代理
- **错误渠道扫描**: 每 5 分钟分批主动探测 402/401/502 等瞬间返回的错误（慢渠道检测无法覆盖）
- **自动修复**: 慢渠道降权→禁用、错误渠道直接禁用；NewAPI 故障只告警（本地服务自动重启由独立 watchdog 负责）；本地代理的周期性推理探针已禁用（省上游费用），崩溃复活由 proxies-supervisor/watchdog 负责，Telegram `/restart` 手动重启仍可用
- **权重闭环自愈**: 渠道恢复 → 自动启用 → 加入聚合池 → 仅恢复 weight → 性能监控 → 自动降权/禁用；priority 保留人工策略
- **固定路由保护**: ch48/ch83/ch91/ch92 不参与动态调权；错误扫描和全量扫描共享真实软失败计数，连续 3 次后隔离，恢复时回到契约 weight
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
  ├─ 固定路由软错误: 错误扫描/全量扫描合计 3 次后直接隔离，不漂移 weight
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

ch48/ch83/ch91/ch92 是显式固定路由。每批最多测试 2 个固定路由，并至少为普通渠道保留
3 个槽位。ch48/ch91/ch92 使用流式 Responses 管理探针；429 和探针形态不兼容不计入
软失败。固定路由成功、被禁用或身份指纹变化时清除计数。渠道 PUT 必须从本地只读
SQLite SSOT 回填未掩码 key，并同时校验 `id + name`；无法验证时拒绝写入。

## NewAPI smoke 管理会话

`newapi-local-smoke.py` reuses `.admin-token-cache.json` to avoid scheduled
session churn and `AUTH_SESSION_LIMIT`. Authentication order is cached token,
then Guardian's long-lived `newapi_token`, then password login only when no
Guardian token is configured. A cached 401/403 is discarded; 403 falls through
to the Guardian token. 429/5xx/network errors keep the cache and fail the
current run. A configured Guardian token returning 401 fails closed without a
password login.

真实语义 smoke 只有在某模型的所有声明渠道均不可路由，且每个禁用都能归因给
Guardian、NewAPI auto-ban 或显式隔离策略时才输出 `SKIP`。未知禁用、没有声明路由、
仍启用但 weight 为 0 等状态继续失败；`SKIP` 只证明隔离/fallback 姿态正确，不证明上游恢复。

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

本机关闭 NewAPI 内置定时渠道测试以控制探测成本，因此 Guardian 每轮会把 `status=3 && auto_ban=1` 的渠道同步到自身恢复队列。同步不立即启用：先等待至少 5 分钟，再执行 3 次探测、至少 2 次通过、恢复权重和 10 分钟稳定性回滚。手工 `status=2`、`auto_ban=0` 以及 `AUTO_BAN_RECOVERY_EXCLUSIONS` 中的明确隔离渠道不进入自动恢复；Guardian 会持续执行 status/weight 双锁。

计划任务 `CCSwitch-NewAPI-DX-Ops` 每轮运行 `newapi-local-smoke.py`。`newapi-smoke-alert.py` 在首次失败和失败后的首次恢复时发送一次 Telegram 告警；投递失败不落状态，下一轮继续尝试，避免“任务一直红但无人知道”。容量门禁只计算 `status=1 && weight>0` 的真实可路由渠道。

仓库中的 `proxies-supervisor.py` 仅在 Telegram 返回 `ok=true` 后记录 30 分钟告警冷却；
投递失败按服务最多每 60 秒重试一次。`anyrouter-window-canary.py` 的 JSON 探针要求非空文本
和正常结束原因；直连 SSE 探针要求文本与正常 `message_stop`，HTTP 200、空流、流内错误或
截断均不能单独证明可用。响应读取限制为 64 KiB，直连流另有时间预算。

回归测试从仓库加载 supervisor，并隔离 HOME、日志与网络。可分别运行：

```powershell
python3 -B -m unittest discover -s scripts/ops -p test_anyrouter_window_canary.py
python3 -B -m unittest discover -s scripts/ops -p test_proxies_supervisor_status.py
python3 -B -m unittest discover -s scripts/ops -p test_supervisor_session_cleanup.py
```

上述为仓库行为约定；生产副本是否已部署须另行核验，不能由仓库测试通过推断。

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
| `~/.omp/guardian/anyrouter-window-canary.py` | anyrouter 开窗哨兵：计划任务 `AnyRouter Window Canary` 每 **5min** 触发一次（2026-09-23 由 30min 收紧，即跑即退，IgnoreNew 防重叠），haiku/16 tokens 探测 8789 桥，每轮有界多挤（最多 5 次×间隔 10s，429 不耗额度）并附探 gpt-5.6-sol（独立 sol_state 告警，代理已内置 8×5s 挤），仅 closed→open 跳变发 Telegram；**告警发送失败自愈**：不落 open 态，下轮重发直到送达或窗口关闭。源=scripts/ops 同名文件（双端 md5 须一致）。告警依赖 telegram_proxy=本地 Clash 端口，Clash Verge 须常开。门禁 test_omp_routes.py `test_default_role_has_no_model_fallback_chain` 禁止自动挂链，开窗后人工显式选用 |
| 同上哨兵 opus 探测（2026-09-24 起） | opus-5-5 双路径：8789 桥（对照，5×10s 挤）+ **直连 anyrouter.top**（主信号，3×5s 挤，形状照抄 direct-probe.cjs 已验证 claude-cli 头集；读 `~/.claude/settings.json` 的 token/BASE_URL，BASE_URL 被 cc-switch 接管改写为非 anyrouter host 时跳过并落 `skipped: takeover active`，绝不打 15721）。haiku 控制组 09-23 实证常驻满载≠opus 关窗，故 opus 独立 `opus_state`/`opus_direct_state`，告警=任一路径 open 的合并跳变 |
| `~/.omp/guardian/anyrouter-canary-state.json` | 哨兵状态（上次 open/closed + 细节），防重复告警 |
| `~/.omp/agent/` | OMP 配置目录，**独立本地 git 仓**（2026-08-15 起，禁加 remote——models.yml 含明文 key）；改 config.yml/models.yml 后即 commit，取代 .bak 手工备份 |
| `~/.omp/guardian/proxies-supervisor.log` | supervisor 运行日志 |
| Startup `LocalAIProxies-Supervisor.lnk` | supervisor 唯一活跃启动入口（同名计划任务 Disabled） |
| `~/.omp/guardian/heartbeat.json` | 心跳（Guardian.run() 每轮原子写 ts+pid） |
| `~/.omp/guardian/guardian.log` | 日志（RotatingFileHandler, 5MB × 5） |
| `~/.omp/guardian/state.json` | 状态（禁用/降权/加入/权重历史） |
| `repair_guardian_channel_state.py` | Guardian 停止时清理指定渠道的旧恢复元数据；默认 dry-run，apply 前双备份并原子回滚 |
| `repair_muse_channel_posture.py` | 只修复 ch48 p51/w12 channel/ability 姿态，保留 status；启用时要求两次 Responses、512-token 语义/usage 与日志归属探针，并拒绝 incomplete 响应 |
| `quarantine_newapi_channels.py` | 显式渠道 status=2/weight=0 双锁；创建脱敏摘要和完整性校验 DB 快照，失败恢复原姿态 |
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
- qwen3-8-27b 二源聚合脚本：`scripts/ops/add_qwen38_27b_pool.py`（ch112 `yjs-qwen3-8-27b` 专用单模型渠道，复用 ch110 key，mapping `qwen3-8-27b→qwen-3.8-27b`，p0/w1 与 ch88 同权 1:1；abilities 表是 channel 行派生物——per-model 权重只能靠渠道级字段控制，挂 19 模型的 ch110 不可为单模型调权故走专用渠道；未加 param_override——yjs 实测接受 `prompt_cache_key` 且借此切缓存感知后端，runbook 见 `docs/ops/qwen38-27b-aggregation-2026-08-28.md`）
- seeseed1ck hydrogel 渠道脚本：`scripts/ops/add_seeseed_hydrogel_channel.py`（ch89；GLM-5.3/grok-4.6/grok-chat-fast/mimo-v2.5，仅收录直连实测存活模型；param_override 删 `enable_thinking`——GLM-5.3 强制思考拒 `enable_thinking:false`，runbook 见 `docs/ops/seeseed-hydrogel-channel-2026-08-16.md`）
- t1qq sol 兜底渠道脚本：`scripts/ops/add_t1qq_sol_channel.py`（ch90，双 key 轮询，priority 20 垫底；fork 多 key 三连坑——`multi_to_single` 创建仍不落 `is_multi_key`、PUT 会整体重生成 channel_info 冲掉 DB 修复、可用配方=完整 BLOB 直写+等 60s 缓存同步，runbook 见 `docs/ops/t1qq-sol-channel-2026-08-16.md`）
- justwoker opus-thinking 聚合渠道脚本：`scripts/ops/add_justwoker_opus_channel.py`（ch94/ch95 双单 key 渠道，claude-opus-5-thinking/claude-opus-4-8-thinking，p50/w8 与 tabitoken ch75 同级；上游 Cloudflare 拦非浏览器 UA 报 1010，header_override 浏览器 UA 为必需，runbook 见 `docs/ops/justwoker-opus-channels-2026-08-20.md`）
- gorouter ch57：余额不足禁用中；2026-08-20 实测上游也在 Cloudflare 后（默认 UA 1010），已预防性补齐 header_override 浏览器 UA（备份 `new-api-before-gorouter-ua-20260820-134048.db`，渠道保持 status=2），余额恢复后可直接重启用
- opencode-zen 免费池渠道脚本：`scripts/ops/add_opencode_zen_free_channels.py`（ch96 `opencode-zen-free`，复用 ch48 的 Go key 打 Zen 免费端点；当前 4 模型=big-pickle/mimo-v2.5-free/hy3-free/muse-spark-1.2-contributor-free，p10/w5 免费兜底池，ModelRatio=0，浏览器 UA 必需；muse-free 走 /v1/responses 直通、无需 chat→responses 策略；429 FreeUsageLimitError=额度耗尽非故障；两个 nemotron 试用模型刻意不收，runbook 见 `docs/ops/opencode-zen-free-channels-2026-08-20.md`）。池变动脚本：`remove_opencode_zen_free_model.py <model>`（通用摘除：2026-08-21 摘除 deepseek-v4-flash-free 促销结束硬 401、laguna-s-2.1-free 持续 503）。**Ox Alpha 全量下线（2026-08-27）**：免费档取消、付费版亦停，`remove_ox_alpha_newapi.py` 从 ch96/ch110/ch109 摘除 `x-preview-f-free`/`x-preview-f` 并清 ModelRatio，专用聚合渠道 ch100/102/103/104 此前已删；历史接入脚本（`add_opencode_ox_alpha_model.py`、`add_openrouter_ox_alpha_channel.py`、`add_ai168661_ox_alpha_channel.py`、`add_608885_ox_alpha_channel.py`、`add_opencode_go_oxalpha_channel.py`）与 `add_yjs_free_channel.py` 的 ox 映射均已归档/切断、禁止重加，runbook 见 `docs/ops/ox-alpha-removal-2026-08-27.md`
- opencode-go omen-alpha 专用渠道脚本：`scripts/ops/add_opencode_go_omen_alpha.py`（ch125 `opencode-go-omen-alpha`，单模型 p0/w5，key 复用 ch101 donor 不动 ch101；abilities 是渠道行派生物——per-model 权重只能靠渠道级字段控制，同 qwen3-8-27b 池机制；Go 套餐 $60/月包月边际成本 0 → ModelRatio=0；浏览器 UA 必需（CF 1010）；runbook 见 `docs/ops/opencode-go-omen-alpha-2026-09-04.md`）
- bai 免费池纯化脚本：`scripts/ops/sync_bai_free_models.py`（ch111 `bai-free` 2026-09-04 纯化至哨兵验证真免费 2 模型=**mimo-v2.5,hy3**；摘 mimo-v2.5-pro(503 陈旧)、hy4-preview/minimax-m2.7(余额门槛型：余额跌破模型要求即全 400)、deepseek-v4-flash/deepseek-v4-flash-vision-exp(哨兵实测扣费 -9/-30 quota)；教训=**200 OK ≠ 免费**；deepseek-v4-flash 全局 0.5 比率保留（ch110/ch118 共用）；test_model 已修为 mimo-v2.5（残留 deepseek-v4-flash 令 Guardian 扫描必失败，auto_ban=1 有整池隔离风险）；脚本已加 test_model 自愈（摘除命中 test_model 时同 PUT 改指首个存活模型，verify() 断言 test_model ∈ 存活列表）；回加条件=充值+直连200+哨兵零消耗，runbook 见 `docs/ops/bai-free-model-sync-2026-09-04.md`）
- tabitoken 多 key 拆分脚本：`scripts/ops/split_tabitoken_channel.py`（ch75 三 key 轮询被 key#2 欠费 $0.22 拖垮整渠道，拆为 ch97/98/99 单 key 渠道；欠费 key 小探针假活，偿付探测必须带真实档 max_tokens=8192；ch98 欠费停放、ch75 tombstone 均已入 Guardian/smoke 排除集防复活，runbook 见 `docs/ops/tabitoken-split-single-key-2026-08-20.md`）
- Sol 配置顺序：ch92 `zzzcoding-gpt-5.6-sol` p60/w15、ch91 `jianzhile-gpt-5.6-sol` p55/w5、ch83 `muyuan-sol` p50/w5、ch45 `agentrouter` p40/w5。2026-08-20 当前 ch83/ch91/ch92 均因真实探针失败保持 `status=2`，只有 ch45 在服务；这里的 p60/p55/p50 是恢复后的顺序，不代表当前可用。`update_zzzcoding_sol_primary.py --allow-disabled-probe-failures --apply` 仅允许已禁用渠道在探针失败时修复 priority/weight，绝不豁免启用渠道，也不改变 status。
- Sol 硬化工具：`remove_omp_default_fallback.py [--apply]` 只删除当前默认 selector 的精确 fallback；`remove_omp_dead_fallback.py [--apply]` 只从 Agnes Flash 链删除已知不可解析候选；`drill_sol_failover.py [--preflight|--apply]` 在 `finally` 恢复 ch92 状态和三层姿态；`rollout_agentrouter_sol.py [--apply]` 仅在两次管理探针和 OMP 精确语义通过后启用 ch45；`monitor_sol_semantic.py` + `register-sol-semantic-monitor-task.ps1` 提供 30 分钟、`IgnoreNew`、无自动修复的 TTFT/语义监控；`campaign_sol_context.py --run` 以服务端 `prompt_tokens` 执行 200k/280k/340k/380k/396k 阶梯、约 +/-8k 边界二分和两个工具形态点。所有变更工具默认 dry-run，大上下文工具还要求显式 `--run`。
- zzzcoding claude key 工具：`scripts/ops/setup_zzzcoding_claude.py`（写 `~/.claude/zzzcoding-settings.json`；已加 `CLAUDE_CODE_ATTRIBUTION_HEADER=0` 关归因头）与 `scripts/ops/rotate_zzzcoding_claude_key.py`（轮换 ch123 claude-opus-5 key；`ZZ_KEY` 环境变量传入不入仓不入 argv；上游探活用 `/v1/models` 直连 200，管理面 `/v1/messages` 池空 503 是门控"down"信号非 key 失效，勿误判）
- Guardian 预算加固与睡眠假象复盘：runbook `docs/ops/guardian-budget-hangfix-2026-09-04.md`（"15.6h 周期挂死/23.5h 请求/watchdog 失灵"均为整机睡眠墙钟假象——唤醒→恢复仅 18s；真实缺陷三处已修：ch124 groq p49→**40**（NewAPI 优先级降序选路，60 会放大 400 为全部；groq 拒 chat_template_kwargs 仅兜底）、NewAPI `relay_timeout=900`（0=无界，隧道停摆清醒挂 6h）、Guardian `scan_error_channels` 预算感知（对齐 full_health_scan；预算超限 10-30 次/天→截断推迟）；**未做**：周期线程隔离/恢复退避改造（已有 2→60min）；存量 harness 破损修复（make_engine 缺 `_probe_soft_failures` + heartbeat tmp 命名断言）→ 142/142 OK；夜间整机睡眠致中继/监控 00-15h 全停属环境因素非缺陷）
- OMP/Clash 节点病态与 autoupdate 永久饥饿修复：runbook `docs/ops/omp-clash-node-autoupdate-2026-09-04.md`（当晚 ch125 500×13 / ch93 EOF+no-channel 刷屏 / github 下载失败的公共根因=Clash「宝可梦」组病态节点（日本01三网优化Vless），经 controller `127.0.0.1:9097` 运行时切到香港03(24ms) 后 zen-go 200/0.7s、sota/github 全恢复、21:25 后相关 ERR=0；拓扑事实：ProxyEnable=0+TUN 关但 mihomo fake-IP DNS 劫持仍在，"直连"实际仍走 mihomo 规则；`~/.omp/omp-autoupdate/omp-autoupdate.ps1` 成熟度逻辑修复（备份 `.bak-20260904-maturityfix`）——旧逻辑只看 atom[0]+2 天门槛+上游日更=永久饥饿，改为从新到旧取首个 age≥2d，手动跑通 SHA+rename-aside+版本复核后 omp.exe 18.0.11→**18.1.2**；ch115 sensova 429 文案变为 `token plan entitlement exhausted`（套餐额度耗尽，观察项））
- OMP smol 链净化与 commit/smol 角色切 omen-alpha：runbook `docs/ops/omp-smol-chain-cleanup-2026-09-04.md`（qwen3-8-27b 链故障善后：commit/smol→omen-alpha:high；smol 兜底链 9→5 摘 zen 四死条目（big-pickle/mimo-v2.5-free/nemotron-3-ultra-free/nemotron-3.5-lightning-free，ch96 上游劣化零启用渠道），净化后每跳有活渠道：omen-alpha→deepseek-v4-flash→mercury-2→mimo-v2.5→muse-spark→hy3-free；models.yml 四 zen 条目刻意保留休眠，zen 恢复即自动可路由；本地仓 commit e30fbc5）
- OMP LSP 三件套与 duckdb 工具装填：runbook `docs/ops/omp-lsp-duckdb-tooling-2026-09-04.md`（rust-analyzer 走 scoop（机内无 rustup/cargo）、ts/pyright 走 npm -g；新增 ~/.omp/agent/lsp.json 扩展 rootMarkers（rust-toolchain.toml 命中 cc-switch 根/*.py 命中 Guardian 目录）——cwd-only 探测的必要补偿；新会话端到端验证 main.tsx→ts-server ready、src-tauri/main.rs→rust-analyzer ready；**运行中会话持启动快照，变更需新会话生效**；duckdb 1.5.5 + duckdb-skills@claude-plugins-official 0.2.4（GIN 日志/SQLite 挂载分析）；官方 marketplace 291 插件筛完 0 个可装——`*-lsp` 系列对 OMP 原生 LSP 冗余，其余与既有技能/工具重叠，勿装）
- 嵌入式工具链 PATH 挂载与 OMP clangd 接入：runbook `docs/ops/omp-embedded-toolchain-2026-09-04.md`（11 目录入用户注册表 PATH（xtensa/riscv gcc 15.2.0 v6.0.1 配对、双 gdb 16.3、openocd 20260304、esp-clang 20.1.1、idf.py/ninja/cmake3.30.2/dfu-util）+ WM_SETTINGCHANGE 广播，新终端生效；两个关键 quirk：**OMP bash 执行层只认 PATH 解析（绝对路径直呼必 command not found）**、**wrapper 向原生子进程传 POSIX PATH 不转换（从 OMP bash 测新会话必须注入注册表 PATH 的 Windows 格式）**；lsp.json 增 clangd 覆盖（rootMarkers 命中 CMakeLists/sdkconfig，command 继承内置经 PATH 解析），throwaway C 工程 `int x = "type-mismatch"` e2e 实证报出 -Wint-conversion；**DAP esp-gdb 阻断**：esp-gdb 16.3 Windows 构建无 threading→`-i dap` 不可用（上游缺陷），dap.json 已撤，板级调试走 openocd gdbserver+手动 gdb；`idf-exe` 二进制名是 `idf.py.exe` 命令名 `idf.py`；本地仓 commit ced7eb2）
- agentrouter WAF 挑战终局修复（glm-5.3 全链恢复）：runbook `docs/ops/agentrouter-waf-glm53-2026-09-04.md`（09/04 ~22:30 起双域 ps.air-outer.com/agentrouter.org 对家宽 IP 触发阿里云 WAF 全量 JS 挑战，上游返回 200+HTML 假成功/旧代理 500；先做了代理诚实化（200 体校验+诚实 502，备份 `.bak-20260904-waf`）与 Guardian file-tail 错误侧观测，再三层门定案：**WAF=出口 IP 级**（家宽被封，香港02直连/香港05原生穿透，其余 HK/JP 节点 TLS 直接被掐，与 TLS 指纹无关）+ **UA 必须 claude-cli 样**（裸 UA→401 unauthorized client）+ **池 key 健康**（代理 keys.json 4 把 sk- 轮换转发；NewAPI ch45/120 的 64 字符 key 仅是本地鉴权不透传上游）；终局修复=Clash `Agentrouter-EG` 专属组（双域 DOMAIN-SUFFIX 置顶，默认香港02直连）+ supervisor 给代理注入 `HTTP(S)_PROXY=7897`（代理本就 trust_env；改 env 必须重启 supervisor 本体，旧进程内存不重读）；端到端验证：NewAPI glm-5.3 非流式 200/c=35 真实消费 + 流式 177 chunks/finish=stop；**Verge 重生成 runtime 会丢注入**→ `scripts/ops/apply-agentrouter-egress-rules.py` 幂等补回（py launcher 认不了脚本 shebang，须 `py -V:Astral/CPython3.12.13` 跑）；附带发现：全局 Merge.yaml（sharedchat/linux.do 规则）是孤儿文件从未进过 runtime，mihomo 只是容忍未知键忽略之——sharedchat 系一直按 profile 默认规则路由，独立问题未动）
- 压缩模型切 omen-alpha 与 DeepSeek 渠道复活重组：runbook `docs/ops/compaction-omen-alpha-deepseek-recovery-2026-09-09.md`（压缩候选 [omen-alpha, agentrouter/deepseek-v4-flash 直连兜底, glm-5.2 预留, qwen3-8-27b]，revision r7，repo/live SHA `b25228dd…`；models.yml 88 处 compactionModel + config.yml 4 处 fallbackChains deepseek→omen-alpha；ch15 sensenova 经 DB 双置复活接管 deepseek-v4-flash 主路（pri50>ch118 的 30，relay 200/2s 实证），ch118 seeseed 429 配额 720h 窗口+500 抖动由 Guardian 降权 5→2 压制；ch110 403 封禁、ch107 token 池空、ch108 whyyin 死、deepseek-v4-pro-0813 成死端；**NewAPI 渠道 API 契约**：PUT /api/channel/ 体禁带 status（Invalid parameters），启停用走 POST /api/channel/{id}/status，渠道 ops 自动 UpdateAbilities，/api/channel/fix 兜底，GET 抹 key 勿回 PUT；smol 空心链主备同 omen-alpha 由用户明示接受，route 门禁加 ACCEPTED_PRIMARY_REPEAT 显式豁免）
- hashneuron 渠道接入脚本：`scripts/ops/add_hashneuron_channel.py`（ch129 `hashneuron`，上游 `https://hashneuron.space/v1`（routeopen），p30/w5、ModelRatio=0；2026-09-13 上游 4 模型仅 `z-ai/glm-5.3-free` 实测可用，composer-2.5/grok-4.5/grok-4.6 为账号级日预算耗尽（`daily_sdk_token_limit_exceeded`，UTC 00:00 重置）→ `--map-glm` 已把 `glm-5.3` 经 model_mapping 指向上游 `z-ai/glm-5.3-free`（双名 + abilities 双行 enabled，glm-5.3 池 = ch45 p40 → ch129 p30 fallback；ch121 实为 glm-5.3-flash 别名池不相关），重置后 `--extend-pool` 扩全池（composer→composer-2.5 映射，p0/w3 备份档挨 imagic ch109；PUT 按 ch127 契约 GET 抹 key 须显式回填；探活 quota=预算未重置非故障）；fork 非 WAL：备份读锁撞后台写会 200+success=false 包 SQLITE_BUSY 且可能半提交（models 行已写、abilities 中断）→ 脚本 `put_channel_retry` 重试 + 幂等检查含 abilities 回读，BUSY 后必须 DB 验 abilities；上游限用户级并发，偶发 429 `Concurrent request limit` 属瞬态；runbook 见 `docs/ops/hashneuron-channel-2026-09-13.md`）
- any 渠道空回 + ch126 探针误判降权：runbook `docs/ops/any-ch126-unbilled-empty-2026-09-14.md`（ch126 `any-gpt-6-astra` 两类性质不同的问题叠加：**①未计费空回** 7d 21.8%（423/1941，type=2 且 prompt=0/completion=0/quota=0，content "上游没有返回计费信息，无法扣费"，is_stream=1），旧 opus 空响应监控带 `prompt_tokens>=1000` 门槛把它读作 0.0% → 新增按 (channel_id, model) 分组的未计费空回监控（`UNBILLED_EMPTY_WINDOW_HOURS=6`/`MIN_SAMPLES=30`/`THRESHOLD=0.20` 与 opus 同档，7d 分桶实测仅 ch126 峰值 51.7% 与 ch3 opus 30.1% 越线故无噪音，冷却键 `unbilled_empty:{channel_id}:{model}` 按腿独立，DB 缺失→None fail-safe），**告警 only**：根因在上游 200 收流不回计费信息，NewAPI 无错误码可挂 `AutomaticRetryStatusCodes` 故不可重试；**②管理探针 404** `当前 API 不支持所选模型 gpt-6-astra` 不含 `invalid_request_error` → 通用 `_is_probe_incompatible` 匹配不到 → `full_health_scan` 软失败累积降权 5→2（同窗口真实计费流量空回率 0%）→ `codex_window_pool.is_any_pool()`（id+name+type+models+host 五重校验，urlsplit 失败按 False）分池豁免，凭证/余额类（invalid_api_key/insufficient_balance/余额不足）先返回 False，**刻意不做全局放宽**（同一 404 对 ch15、对改名后的 ch126 仍致命）；`PINNED_CHANNEL_WEIGHTS[126]=("any-gpt-6-astra",5)` **必须与豁免同批上线**（只 pin 不豁免＝把假故障从反复降权变成反复禁用）；线上 weight 4→5 修复走 `_hydrate_channel_key` 从 SSOT 补真 key 再 PUT（管理 API GET 读出 key 为空，直接 PUT 抹凭证），清 `degraded_channels["126"]` 陈旧记录**须在引擎停止期间**做（Guardian 内存持有 state 每轮回写）；`configure_codex_agent_any.py --verify` 门禁修复前 `RuntimeError: AnyRouter tier drifted`、修复后 rc=0；两个 ops 测试文件**必须分开跑**（合并收集双导入 test_guardian → 双临时 HOME → 9 个 ProxyRestartTests 在干净 HEAD 上同样失败）；未来做 sole-leg guard 须避开 `degrade_channel_weight` 触底(<=MIN_WEIGHT)直接 `disable_slow_channel` 的升级分支）
- any-Claude 池开窗故障排查收口（出口 A/B 实验）：runbook `docs/ops/any-claude-pool-egress-ab-2026-09-23.md`（"opus 用不了"实弹排查：key/额度、[1M] 过滤、链路、**出口 IP（8789 桥 + forwarder 8791 经 Clash API 9097 轮换 36 节点全 429）、版本串（桥 CC_VERSION/billing cc_version 升实机 2.1.267）五层全排除 → 上游池全局关停窗口期，"别人都在用"=窗口抽签，不改配置；桥 config.json 支持 config 驱动 upstream pivot（缺省直连不变，留作单点试验工具）；canary 节奏 30min→5min + repo/live 部署漂移同步）
- opencode-go Space Bunny 免费周接入：runbook `docs/ops/opencode-go-space-bunny-free-2026-09-23.md`（上游 opencode.ai/zen Go 平台 `/v1/models` 34 模型含 `space-bunny-free`（免费周 ~0930，1M 上下文）；zen 端点强制 `x-opencode-session` 头 + 浏览器 UA，ch125 靠渠道 `header_override` 注 Chrome UA+静态 session 免头转发；**专用 ch130** `opencode-go-space-bunny-free` 照抄 override（勿塞 ch125）+ models.yml zg-newapi entry（`zg-newapi/space-bunny-free`，contextWindow 1M，reasoning 实测有）；多模态实测 400 invalid params → 暂声明 text-only；回滚=禁 ch130 + models.yml.bak-20260923-spacebunny，快照 `~/.new-api-local/backups/space-bunny-wiring-20260923.json`）
- any opus-5-5 入 CC Switch 供应商：runbook `docs/ops/anyrouter-opus55-ccswitch-provider-2026-09-25.md`（providers 行 `anyrouter-opus55-1790311108648`「AnyRouter Opus 5.5」直连 `https://anyrouter.top`，env 照抄 settings.json.bak-20260923-direct-test——该备份名误导、实为 8789 桥配置且 token 与 `anyrouter_proxy_key` 同一；`anyrouter_proxy_key` 即真 key 非桥客户端凭证；forwarder 透传 claude-cli UA 故直连无指纹门；**新发现：anyrouter 裸 `claude-opus-5-5` 已强制 1M beta（400），须 `[1M]` 变体或 context-1m beta**；meta.apiFormat 必须 anthropic；实弹 503=池窗关（canary takeover 下直连探针跳过，非证据）；WAL 模式文件拷贝备份不含 WAL 须用 sqlite3 backup API——快照 `.bak-20260925-anyrouter-opus55-walsafe`；回滚=删单行）
- opencode-go key 轮换 + 四模型切换：runbook `docs/ops/opencode-go-key-rotation-2026-10-02.md`（新 `oc_sk_` key 实弹上游 36 模型；ch125/ch130 换 key，新建 ch131 muse-spark-1.3/ch132 gpt-6-luna/ch133 longcat-2.5-preview-free（照抄 ch130 header_override），删 ch48/ch117；**gpt-6-luna 与 muse-1.3 均 responses-only**（chat 面 400 ModelProtocolUnsupported，fork /v1/responses 原生透传 200，models.yml 声明 `api: openai-responses`）；muse-1.3 曾被 workspace Privacy 训练数据门拦（contributor 整档），用户当晚放行后 4/4 全绿；ModelRatio 四模型=0；ch96 zen-free 仍旧 key 未动）
- agentrouter Claude 四 key 均衡池 + 账户级 402：runbook `docs/ops/agentrouter-claude-keypool-2026-10-02.md`（ch86 单 key → ch86+ch134/135/136 四单 key 渠道池（克隆 type=14 全字段，prio50 w2×4，abilities 5×4 落表）；**已否假设：现有渠道 PUT 多行 key 不可行**（fork 仅建渠道时算 channel_info.is_multi_key，PUT 强制保留原值 → relay do_request_failed time 0）、**预算非按 key**（4 key 全模型数分钟同 402 → 账户级共享池耗尽，Claude+GPT 全灭，待用户控制台加预算）；OMP 侧 Claude 由 ch3 prio54/ch9 prio52 承载不受影响；cc-switch 当前供应商用户已自切 AnyRouter）
- Unisound（云知声）u2-flash 限免接入：runbook `docs/ops/unisound-u2-flash-2026-10-02.md`（ch138 `unisound-u2-flash`，key 走 `UNISOUND_KEY` env；上游 22 模型目录，**u2-flash 为 reasoning 模型**（reasoning_content 吃 completion 预算，max_tokens 要给足），流式正常；限免窗口 09.30-10.31 → ModelRatio=0，**11 月前必须复核计费切换**；u2/u2-med 同活但定价未公告故未接入）
- Atria Dawn Preview 接入：runbook `docs/ops/atria-dawn-preview-2026-10-02.md`（ch139 `atria-dawn-preview`，key 走 `ATRIA_KEY` env；目录仅 `Atria-Dawn-Preview` 单模型，reasoning 型，**TTFT 慢 ~20-28s**（适合后台/批量角色）；1 亿 token 额度 → ModelRatio=0，耗尽行为未实测，auto_ban 自动禁用即 fail-closed）
- StepFun Step Plan 恢复接入：runbook `docs/ops/stepfun-step-plan-2026-10-03.md`（ch144 `stepfun-step-plan`（历史 ch36 已删故新建），step-3.7-flash 262K/vision/reasoning/三协议 + step-router-v1；**定价只读校验不改写**（router 必须 0.5/2 已在库，flash 残留 0/3 待官方价）；**abilities 缓存竞态实录**（channel/fix 后 chat 200 无日志行，脚本已加 6×5s 重试环）；ch110 yjs-free 残留 disabled ability（prio6）碰撞警示；vision 链首备胎 + models.yml×2，E2E STEP37_OK）
- LongCat 官方源接入：runbook `docs/ops/longcat-official-2026-10-03.md`（ch145 `longcat-official`，LongCat-2.5-Preview 上游标称 1M ctx/262K out；与 ch133 free 变体不同 id 并存；定价未设 open item；一次全绿，E2E LC_OK）
- LongCat 2.5 Preview 双源聚合：runbook `docs/ops/longcat-preview-unify-2026-10-03.md`（**用户指正：ch133 free 与 ch145 官方是同一模型**；`unify_longcat_preview_sources.py` sqlite 直写（fork 拒 PUT 渠道更新）双渠道互加对方 id + model_mapping，ch145 提 prio 10 为主 / ch133 留 0 为备；abilities 4 行 + 双映射 admin test + 两 id 网关实弹均落 ch145）
- intern-discovery 池扩充：runbook `docs/ops/intern-discovery-pool-expansion-2026-10-03.md`（+ch146 k5/ch147 k6 单 key 渠道对齐池 prio40/w1；**pool 级归因不能证明新 key** → sha256 幂等 + 逐 key 直连 + 逐渠道 admin test 硬门；既有问题：池载 `qwen3-8-27b` 上游实为 `qwen3.8-27b` 死映射待统一；glm-5.3 官方货源增厚至 6 key 池）
- opencode-go 套餐变更（Go/Go Plus 双档 + per-model 月度 $ 限额）与 `space-bunny-free`→`space-bunny` 改名：runbook `docs/ops/opencode-go-space-bunny-rename-2026-10-06.md`（ch130 改名 + `ModelRatio[space-bunny]=0`（漏配会按 37.5 计费）+ 临时兼容别名 `models=space-bunny,space-bunny-free` + `model_mapping`（桥接启动期缓存旧名的会话，下架流程见 runbook）；Guardian 对 responses-only 模型（ch131/132/137）补 `endpoint_type=openai-response&stream=true` 测试覆盖 → ch132 自愈回池、ch131 权重复原；`fix_opencode_go_space_bunny_20261006.py`，快照 `new-api-before-opencode-go-spacebunny-20261006-185217.db`；误计费 2,475,076 quota（≈$4.95）待用户确认退还）
- 42x.shop DeepSeek 渠道（禁用态 fail-closed）：runbook `docs/ops/42x-deepseek-channel-2026-10-06.md`（ch172 `42x-deepseek-v4-flash-0731` p20/w1 手工禁用；上游 `deepseek-v4-flash-0731`/`-free` 实测 502、`deepseek-flash-free` 本 token 403 → 只注册实测存在的 id；启用=上游复测 200 + 定价后 `POST /api/channel/172/status {"status":1}`；`add_42x_deepseek_channel.py`（dry-run 默认，key 走 `FORTYTWOX_KEY` env），快照 `new-api-before-42x-deepseek-20261006-190648.db`）
- 君の公益 muyuan.do 聚合接入：runbook `docs/ops/muyuan-gongyi-channel-2026-10-06.md`（ch173 `muyuan-gongyi` p20/w1，**27 模型** exact-id 全量透传（GLM-5.3-200k/GLM-5.2-200k/glm-4.5-flash/grok-4.7/qwen3.8-27b/Mistral 全家含 mistral-large-4-0/-4）；glm-5.2 与 ch15 共享池（ch15 p50 主，ch173 备）；其余 26 模型为唯一在营渠道；mistral-large-4-0 首探上游未定价（400），22:13 上游定价后 `--extend` 补入；定价只读对账未改写（25 模型走网关默认倍率，glm-5.2=2/3、glm-4.5-flash=0 已在库）；`add_muyuan_gongyi_channel.py`（dry-run 默认，key 走 `MUYUAN_KEY` env，幂等 resume，纯增量 `--extend`），快照 `new-api-before-muyuan-gongyi-20261006-220441.db` / `-221422.db`；与 ch83 `muyuan-sol`/ch119 `muyuan-glm-5.2`（均 status=2）同域不同渠道勿混淆）
- grok-heavy.878.indevs.in 接入（ch174）：runbook `docs/ops/grok-heavy-indevs-channel-2026-10-06.md`（ch174 `grok-heavy-indevs` p-10/w1，3 模型 gpt-6-luna/gpt-5.6-luna/gpt-5.6-terra（resp_* 套壳）；ch132/ch137 opencode-go 套餐耗尽经用户授权 status=2 后为 luna 系唯一在营；OMP models.yml 加 gpt-5.6-terra、回加 gpt-5.6-luna（api=openai-responses）；`add_grok_heavy_channel.py`（`GROK_HEAVY_KEY` env），快照 `new-api-before-grok-heavy-indevs-20261006-230018.db`）
- hubway.cc 接入（ch175 `hubway`）：runbook `docs/ops/hubway-channel-2026-10-07.md`（p-20/w1，7 模型 codex-auto-review/gpt-5.5/gpt-5.6-sol/gpt-5.6-terra/gpt-6-astra/gpt-6-sol/gpt-6.1-sol；gpt-5.6 上游别名死排除；CF 仅 ban python-urllib UA 无需 header_override；canary 两轮 0 issue 含 nested tool；**发现 ch127 agentrouter-codex-gpt 启用态上游死**（sol 503 无可用渠道 / astra 402 budget pool），sol/astra 现由 p-20 层接管；`add_hubway_channel.py`（`HUBWAY_KEY` env），快照 `new-api-before-hubway-20261007-004717.db`）
- apisub.vsakura.top 接入（ch176 `vsakura-gpt`）：runbook `docs/ops/vsakura-gpt-channel-2026-10-07.md`（深夜福利 3 key：K1 Claude 账号池尽、K2 GLM 分组协议封锁均未接入；K3 组 4 模型 sol/terra/astra/6-sol p-20/w1；注入 ~3.5k token/次、nested tool_choice 缺口、偶发 429/gateway_concurrency_limit 留痕；`add_vsakura_gpt_channel.py`（`VSAKURA_KEY` env），快照 `new-api-before-vsakura-gpt-20261007-005920.db`）
- asvla.bbqwq.com 接入（ch177 `asvla-claude`）：runbook `docs/ops/asvla-claude-channel-2026-10-07.md`（sub2api“claude满血全系列”，**type=14**，p-20/w1，10 个 Claude 模型；opus-4-6/4-7/sonnet-4-6/5 零渠道唯一来源；canary 0 issue 含 nested tool；接入当日 key 余额耗尽（402 `余额不足`，fail-closed，Guardian 扫描将隔离、充值后恢复）；OMP models.yml 新增 6 条目（zg-newapi-anthropic）；`add_asvla_channel.py`（`ASVLA_KEY` env），快照 `new-api-before-asvla-claude-20261007-011306.db`）
- zombie 停泊 ch127/ch9（用户授权，2026-10-07）：runbook `docs/ops/zombie-park-ch127-ch9-2026-10-07.md`（ch127 agentrouter 预算池 402/503、ch9 linxi 账号池 503/404 → **status=2 + auto_ban=1** 房式停泊、abilities 随禁、smoke 零新增（`unexpected_disabled=none`）；**实证 fork admin `/status` 拒 status=3**；sol/astra 回归 attr=176/175；ch18 同源死亡待裁决；ch148 活体未动但其 claude 预算池当晚耗尽致 opus-5 全链告急（budsin 充值/重置、linxi 回血、asvla 充值为外部恢复项）；脚本 `park_zombie_channels_20261007.py`，快照 `new-api-before-park-ch127-ch9-20261007-021031.db`）

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

### NewAPI smoke auth contract

`newapi-local-smoke.py` authenticates with the session cache first, then the
long-lived Guardian `newapi_token` from `~/.omp/guardian/secrets.json`. A
configured Guardian token that returns 401 fails closed; the smoke does not
fall back to password login and create another server session. Password login
is used only when no Guardian token is configured, which prevents scheduled
runs from exhausting NewAPI's `AUTH_SESSION_LIMIT`.

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
its runtime `compactionModel`. Since 2026-09-09 the ordered candidates are
Omen Alpha (primary), agentrouter DeepSeek V4 Flash (direct Tailscale
fallback), a reserved SenseNova GLM 5.2 selector, then runinfra Qwen 3.8 27B
(ch88). It reconciles at
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

## OMP model routing observability

`omp-model-routing-observability.js` records bounded, redacted task/scout route
events and validates effective `modelRoles` after an offline registry refresh.
It shares `~/.omp/agent/logs/omp-model-routing.jsonl` with the compaction and
SOTA extensions and exposes `/model-routing-status`, including refresh and role
validation health; OMP's native resolved-model badge remains the per-task UI.
Detached task dispatches record only normalized agent type and role hash, never
prompt or assignment text.

When native compaction thresholds remain `-1/-1`, the compaction extension uses
the active main model's context window to select 70%, 78%, 82%, or 85%. A manual
model switch is reconciled at the next `before_agent_start` boundary because OMP
17.3.7 has no `model_select` extension event. Explicit user thresholds always
win. Deploy the observability extension with
`deploy-omp-model-routing-observability.ps1`; see
`docs/ops/omp-model-routing-observability.md` for schema, validation, rollout,
and rollback evidence.

## OMP SOTA escalation layer

`omp-sota-escalation.js` discovers readiness-verified `omp-sota-*` model
aliases and runs at most one ephemeral, read-only child OMP review for
explicit, high-risk, complex, or repeated-tool-failure turns. Failure number
two triggers immediate rescue and steers the result into the current turn;
terminal reviews may schedule one recursion-suppressed continuation. It never
switches the main model and never enters ordinary fallback or compaction
routing. `/sota-status` reports only bounded operational state.

Automatic SOTA calls also consult `~/.omp/agent/sota-workload-health.json`.
Two timeout-class child executions since the last success block further
automatic reviews for that selector; an explicit `/sota*` command may perform
one deliberate retry. A successful child clears the breaker. The marked SOTA
selector may be assigned only to `modelRoles.advisor`; it remains forbidden for
default/task/slow roles, fallback chains, and compaction.

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
`add_omp_sota_newapi_alias.py --channel-id ID [--base-model MODEL]
[--apply|--remove --apply]`; the default is dry-run. OMP registration uses
`add_omp_sota_model.mjs [--apply]`, and the independent production extension
is deployed with `deploy-omp-sota-escalation.ps1`. See
`docs/ops/omp-sota-escalation-layer.md` for the complete contract and rollback.
`probe_omp_sota_alias.py [--run] [--readiness-path PATH]` is the opt-in
capability check. It requires an exact eight-token semantic response, a second
forced `report_review` tool call with exact bounded arguments, and fresh log
attribution for both requests; without `--channel-id` it
prefers a dedicated `omp-sota-*` NewAPI channel. Since 2026-08-20 the marked
alias is carried ONLY by the dedicated channel: ch75 `tabitoken` no longer
lists it (models/model_mapping/ability removed via direct DB write, never API
PUT — ch75 is multi-key), so a downed dedicated channel fails the alias
loudly (503 no available channel) instead of silently falling back to the
shared paid pool. To
create or refresh that isolated single-key channel, run
`create-omp-sota-channel-secure.ps1`; it prompts locally, creates an online
backup, configures the channel-local Clash proxy, probes while disabled, and
enables only on success. `install-omp-sota-readiness-task.ps1` installs a
non-overlapping ten-minute readiness refresh; each run tests only the isolated
marked channel, enables it only after a management probe, and disables it on a
strict semantic/log failure. The scripts write
only bounded readiness metadata and never print keys or raw provider bodies.

- zen-free-bridge（2026-10-07）：`scripts/ops/zen_free_bridge.mjs` 本地 OpenAI 兼容桥，驱动真实 opencode CLI
  消费 OpenCode Zen 免费档（raw API 一律 403 FreeTierError，CLI 会话通过门禁；账号级每日额度 429=配额非故障）；
  `scripts/ops/add_zen_free_bridge_channel.py --apply` 按标准合约入网 NewAPI ch178（12 模型 ModelRatio=0，
  探针 subset 3 模型全 quota-pass），runbook 见 `docs/ops/zen-free-bridge-2026-10-07.md`；
  配额恢复后逐模型实弹补验（`--probe-all`），Jev 属 SystemOne 决策接口不适用。
- jev-systemone（2026-10-08）：200 枚官方 key 已入 NewAPI ch181 多 key 轮询池（逐字读回，200/200 官方探针成功）。
  `scripts/ops/jev_systemone_bridge.mjs`（8413）转发 NewAPI 所选 Bearer 至 api.typesafe.ai；
  显式 `jev.systemone` 封装保留自选 state/questions，无标记输入保持固定模板。
  `scripts/ops/add_jev_systemone_channel.py --keys-file <私密文件> --apply` 备份后更新池；
  `jev_mcp_tool.mjs` 经 NewAPI 调用（不入聊天角色），真实 OMP/MCP/SSE 验证通过；
  新增 6 项回归通过但 task_verify 仍需评审新测试，见 `docs/ops/jev-systemone-2026-10-08.md`。
- ss2a（2026-10-08）：glm-5.3 max 官号 relay 入网并**升为主档** — NewAPI ch183
  `ss2a`（type=1，`https://ss2a.top` 裸 base，单模型 exact-id `glm-5.3`，
  **p50/w5/auto_ban=1**）。探测：models 200 仅 1 id；直连/SSE 内容+usage 正常，
  `reasoning_effort=max` 接受；UA 矩阵全 200（无需 header_override）；
  `glm-5.3-max`/`:max` 404（不建别名）；无 billing 端点。canary
  `empty-semantic-output` 为 max_tokens=32 预算工件（推理模型吃掉预算），
  max_tokens=800 重放语义+tool 全过，缓存探测无 suspicious-first-hit。
  升档后网关实弹 3/3 attr=183（`PRIME_OK`）；intern p40 档
  ch140/141/142/143/146 保留为自动故障转移档；禁用式故障演练未做（显式
  未验证）。DB 快照 `new-api-before-ss2a-20261008-232954.db`、
  `new-api-before-ss2a-promote-20261008-233550.db`（integrity=ok）；
  文档 `docs/ops/ss2a-channel-2026-10-08.md`。
- opencode-go-step-5-preview（2026-10-08）：OpenCode 官方限时免费活动
  （1 周，不消耗 Go 套餐额度）——Step-5-Preview 入网 NewAPI ch184
  `opencode-go-step-5-preview`（type=1，base `https://opencode.ai/zen/go`，
  key/header 克隆自 ch130 不落盘，p0/w2，auto=1）。暴露双 id：
  `step-5-preview-free`（exact 上游 id）+ `step-5-preview`（alias→
  -free）。实测 200：chat/SSE/vision(1x1 png 判色)/reasoning_effort=max/
  tool_calls/max_tokens=131072；v1 探测 38 模型目录含 `step-5-preview-free`。
  网关双 id 实弹归因 ch184；真实 OMP `--print` 全程 STEP_OK；OMP models.yml
  zg-newapi 增 `step-5-preview-free`（1M context 官方口径未 1M 实测填充、
  input text+image，活动结束约 2026-10-15 需摘除或改指 arcdent）；
  文档 `docs/ops/opencode-go-step-5-preview-2026-10-08.md`。
- k3-outage（2026-10-09）：用户报"glm-5.3 总是报错"实为 **k3 无可用渠道**
  （OMP plan/designer/task=k3:max；ch33 10-07 起手改禁用、ch115/148 配额死）。
  NewAPI 即时 503 `No available channel for model k3` ×12（00:07:44-00:08:15），
  glm-5.3 ch183 同窗 13+ 次全成功。管理探针证 kimi 官方周额度已恢复 →
  备份后 `POST /api/channel/33/status {"status":1}` 启用（勿用 PUT 改状态），
  网关 k3 200 + OMP `k3:max` E2E `OMP_K3_OK`；快照
  `new-api-before-k3-ch33-enable-20261009-001559.db`（integrity=ok）；
  文档 `docs/ops/k3-outage-misattributed-glm53-2026-10-09.md`。
- jev-enhance（2026-10-09）：Jev 双开增强——(1) 全局技能
  `global:jev-second-opinion`（风险交付/方案摇摆时调 jev_judge 拿独立概率，
  并列上报、分歧标注、失败不阻塞）；(2) `scripts/ops/jev_ops_triage.py`
  周报判定（channels status≠1 + Guardian disabled/degraded 池 → jev 判
  persistence/severity，report-only fail-open；**本 fork 不把请求错误写入
  logs 表**，勿用 logs 扫错误）。实测 76 信号 76 行 ch181 精确 1:1 归因；
  **契约坑：信封须带 `"type":"jev.systemone"`** 否则桥走固定模板；
  约束不变：建议信号不 gate 自动决策；见
  `docs/ops/jev-systemone-2026-10-08.md` 增强节。
- ss2a-kimi（2026-10-09，⏳待上游恢复 apply）：ss2a.top 新 key 组探测
  ——仅放行 Anthropic `/v1/messages`（4 kimi id 全 200 实弹；OpenAI 路径
  502 forbidden → **type=14**；glm/claude id 404=独立分组）。5 模型面：
  4 真实 id exact-id + `zg-k3→k3` 别名腿（镜像 ch33，消除 Cursor BYOK
  zg-k3 单源）。**稳定性：burst 型官号池**，首分钟 6×200 后 503/502
  连续 8/8（~35min 冷却）；
  同 host glm 组 ch183 同期零异常。定位 ch33（p50）之下的**彩票备份腿**
  p20/w1/auto_ban=1，直指 k3-outage 遗留的单腿风险。
  `add_ss2a_kimi_channel.py`（`SS2A_KIMI_KEY` env；管理探针直连门挡
  冷却期 apply；python3 运行，py launcher 忌 shebang）。OMP 侧
  k3-256k/kimi-for-coding(-highspeed) 复活条件已满足（网关目录重现），
  形状自 git 522a6d5/24853ce 回捞，待渠道落地后插回并 E2E；
  文档 `docs/ops/ss2a-kimi-channel-2026-10-09.md`。
- jojatoken 四 key 池（2026-10-09，ch188-191）：jojatoken.com 8 模型（GPT
  sol/terra/luna/astra 家族）exact-id 全量透传，p-20/w1×4 单 key 渠道聚合
  （agentrouter 四 key 池先例，规避 fork 多 key 坑）；gpt-6-luna/gpt-6-sol
  零活跃渠道补空；**上游有反探测风控**（短输入 400 distillation/heartbeat、
  连发 429）→ relay 探针自然长度 prompt + 4s 节流 + 有界重试（含
  TimeoutError/OSError 裸抛捕获）；gpt-6-sol/6.1-sol/6-astra 无 ModelRatio
  走默认倍率（既有 open item 只读）；`add_jojatoken_channel.py`
  （`--keys-file`、`--resume`），快照 ×4，runbook 见
  `docs/ops/jojatoken-longai-channels-2026-10-09.md`
- longai 单 key 聚合（2026-10-09，ch192）：llm.longai.vip 7 模型
  p19/w1（居所有现有主力之下），glm-5.3-flash/deepseek-v4-pro/kimi-k2.6/
  kimi-k2.7-code/qwen3.8-max 零活跃渠道补空；qwen3.8-flash 排除（上游自身
  distributor 冷却 503 + 本地无 ModelRatio）；glm-5.3 首响 ~60s 慢，
  test_model 走 glm-5.3-flash；**key 2026-10-20 到期**，续期后 `--resume`；
  同晚 ch173 muyuan-gongyi 上游 401 Invalid token 经用户授权禁用，
  glm-5.2 由 ch192 接管；`add_longai_channel.py`，runbook 同上
- agentrouter ch127 五 key 复活（2026-10-09 晚）：10-07 死亡判定被用户推翻，
  实证 5 key 全活。双重根因：①上游新增**客户端指纹门**（裸请求 401
  `unauthorized client`，header_override claude-cli 头集后 401→402/200，
  直连 401 先跑 UA 矩阵再判 key 失效）；②channel_info **sqlite 直写 TEXT
  毒化 distributor**（`Scan error on column index 28 … unexpected end of
  JSON input`，殃及 deepseek-v4-flash 全路由跨渠道 503；在营行必须
  `typeof=blob`，修复配方=`CAST(? AS BLOB)` 紧凑 JSON 直写 + 60s 缓存同步，
  `fix_ch127_channel_info.py`，t1qq BLOB 配方同源）；p52 提权遍历 10/10 200、
  multi_key_index 0–4 全命中、status_list 空；回归 p40（ch180 p51 主之下）
  default+Free；预算池：deepseek-v4-flash 活、astra 不足回退 ch182 有效、
  sol 403 待补货；runbook `docs/ops/agentrouter-ch127-revival-jhsy-midjok-2026-10-09.md`
- jhsy-glm ch201（2026-10-09 晚）：ai.jhsy0721.xyz `/v1/models` 仅
  glm-5.3-flash，直连 200/2.5s，单 key p30/w5 default+Free；同轮 ch118 seeseed
  glm 腿 3/4 超时（回退 ch192 30–50s）→ 降 p25 备胎，链=ch201 p30→ch118
  p25→ch192 p19；网关归因 `use_channel:["201"]` 200/2.0s；快照
  `new-api-before-jhsy-ch201-20261009-203241.db`；key base64 传入不落仓
- midjok ch193/194（2026-10-09 晚，脚本 `add_midjok_channel.py` 补录）：
  midjok.lol 双 key 单 key 渠道池 p10/w1 auto_ban=0，7 模型直连 200 才收录
  （gpt-5.5/5.6-sol/5.6-terra/6-sol/6.1-sol/6-astra/codex-auto-review），
  404/429/502 变体排除；**open item：abilities 仅 default 组、无 Free 行**
  （Free 组 token 暂不可路由，补否待裁决）；当晚 gpt-5.6-sol 已见 ch193 真实
  服务归因
- SenseAudio 福利羊毛（2026-10-09）：api.senseaudio.cn 端点存活（401 规范
  应答），key 为兽音译者混淆文本、**用户明示不解密**；30 万积分当日到期，
  明文 key 到手再议，否则归档
- Free 组补行 + ctyun 羊毛（2026-10-10）：gpt-5.6-luna 6 渠道×Free 行补齐
  （此前 default 有 6 条启用而 Free 零行 ⇒ OMP 报无渠道；实弹 200 归因
  ch199 dddai 62s 慢腿）；midjok ch193/194 ×7 模型补 Free 行，实弹 terra
  403 `Insufficient account balance`=**key 组余额耗尽非路由缺陷**（此前有
  200 消费记录）；ch135 复核维持停泊（单 key 渠道无轮询语义，预算回血一步
  启用）；**ch202 ctyun-oc-pool**：电信 eaiChat 羊毛（key 双重 base64=278
  字符 JWT），`kimi-k3-oc`/`glm-5.3-oc` 零渠道补空，p30/w5 双组 auto_ban=0，
  4/4 全 200；**5h 额度窗（00:11/05:11/10:11/15:11 刷新），key 10-10 15:11
  到期即摘除**；runbook 见
  `docs/ops/agentrouter-ch127-revival-jhsy-midjok-2026-10-09.md` 增补节
- Free 组批量补行（2026-10-10，用户授权）：全库聚合审计发现 60 模型
  default 有路由而 Free 零行（glm-5.3/claude 全家/grok 全家/step-5-preview
  等，OMP 走 Free ⇒ 批量"无渠道"假象）；镜像 default 行批量 INSERT 99 行
  （渠道限 status=1），回读遗漏=0，抽测 glm-5.3→ch142/step-5→ch184/
  u2-flash→ch138 全 200；快照 `new-api-before-free-backfill-<ts>.db`；
  **单腿高危遗留**：ch192 到期（10-20）前需为 deepseek-v4-pro/glm-5.2/
  kimi-k2.6/k2.7-code 找备份源；审计脚本为临时件未落仓，SQL 口径见 runbook
- OMP kimi 家族条目复活（2026-10-11，用户指令「把 newapi 中的 k3 和
  kimi-for-coding 加入 omp」）：ch33 `kimi-official-k3` 5 模型
  （k3/k3-256k/kimi-for-coding/kimi-for-coding-highspeed/zg-k3）status=1
  enabled=1 全部在线，`docs/ops/archive/ss2a-kimi-channel-2026-10-09.md`
  复活条件达成 ⇒ `~/.omp/agent/models.yml` zg-newapi 块 gpt-5.6-sol 之后
  插回 4 条（原始形状自 git 522a6d5/24853ce 回捞：k3 1M/128K+efforts
  minimal..max、k3-256k 262K/32K、kimi-for-coding
  `contextPromotionTarget: zg-newapi/k3`、highspeed 262K/32K；备份
  `models.yml.bak-20261011-2130-kimi-restore`）。验证：网关
  /v1/chat/completions 4 模型全 200 真实补全（0.7–2.6s，含非空 text 块
  ——归档文档 62-68 行两项待补复核一并关闭：SSE 28 帧+[DONE]+usage、
  text 块非空）；`omp models` 85 ids 可见 4 条；`omp --model
  zg-newapi/k3:max --print`→`OMP_K3_OK`(54.4s)、`--model
  zg-newapi/kimi-for-coding`→`OMP_KFC_OK`(22.0s)；
  `verify_omp_newapi_alignment.py` 新增 0 孤儿（既有 5 孤儿与本轮无关）。
  ss2a-kimi 备份腿仍处 staged 未 apply（`add_ss2a_kimi_channel.py
  --apply`，SS2A_KIMI_KEY env 传入），ch33 单腿风险遗留不变
- 同日勘误（effort 档位实测）：`kimi-for-coding` 上游**支持六档**
  （同 prompt / 4000 tok 预算：minimal 1434 → high 3441 → xhigh/max
  ≈3997 reasoning tok；high 档起即被预算截断，故 xhigh 与 max 在本预算内
  不可区分，但均显著高于 minimal ⇒ 高档位真实生效）。git 回捞的旧形状
  **没有** `thinking:` 块——属 OMP 侧欠声明，不是上游缺档；已补
  `thinking.mode: effort` + `efforts: [minimal, low, medium, high, xhigh,
  max]`（备份 `models.yml.bak-20261011-kfc-efforts`），`omp models`
  该模型档位列变六档，`omp --model
  zg-newapi/kimi-for-coding:max --print`→`OMP_KFC_MAX_OK`(43.2s)。
  `kimi-for-coding-highspeed` 初测（4000 预算）minimal==max==3999 曾误判
  "固定档直通"——8192 预算复测 minimal 4358 vs max 7293（26s/41s）证明
  **effort 同样生效**，4000 下只是双重饱和；已同样补六档（备份
  `models.yml.bak-20261011-highspeed-efforts`）。同轮 xhigh 5148 vs max
  8189（再顶格）⇒ kfc 六档全实、max 为最高天花板。判别饱和 vs 无档
  **必须加大预算复测**，缩小预算只会双重截断。
  越界值实测：k3 `effort=ultramax` → 200 且 reasoning 仅 12 tok（上游对
  未知名静默降到最低档、不报错）⇒ 多声明的档位名无风险，但也不会凭空获得能力。
  **教训：档位可用性以上游实弹为准，git 旧形状与 `omp models` 显示列
  都不是证据。**
- **kimi 主腿切换福利官号（2026-10-11 晚，用户指令「先用这个，再用我自己的
  订阅」）**：社区流传 `sk-kimi-*` 官方套餐 key（"Kimi K3 (1M)"，
  `api.kimi.com/coding/v1` 直连实测 4 模型目录 + k3 补全 200/2.4s + SSE 15
  帧）。新渠道 **ch215 `kimi-official-k3-welfare`** type=1、base/mapping
  镜像 ch33（`zg-k3→k3`）、5 模型、**p60/w10 auto_ban=1**（羊毛 key 随时
  可被回收，auto_ban 降级即自动回落 ch33 订阅）；脚本
  `scripts/ops/add_kimi_welfare_leg.py`（直连门→备份→建渠→读回→fix→
  admin test→归因断言；key 经 env 不落盘）。**过程三坑**（脚本已修，均
  为 add_ss2a 模板首次真实 apply 才暴露）：①本 fork `GET /api/channel/`
  返回 `{items,page,...}` 分页包装（默认页 10 行）——fetch 误把 dict 当
  list 迭代拿空表，POST 已建成 ch215 却"不可见"；②resume 无漂移仍 PUT
  （fork PUT 前科，改为 key+配置全同则跳过）；③k3 系推理模型 200 tok
  预算会 200 空 content，gate 提到 800+重试。**验证**：admin test 0.47s
  200；网关归因 kfc-highspeed 与 k3 **均 ch215**（羊毛腿赢=主用生效）；
  OMP `zg-newapi/k3:max --print`→`WELFARE_E2E_OK` 29.9s 归因 ch215
  （18K prompt/7s）；ch33 (1,50,10) 全程未动=自动兜底；alignment 门零新增
  孤儿；快照 `new-api-before-kimi-welfare-20261011-233445.db`。
  **连带**：ss2a-kimi staged 备份腿（`add_ss2a_kimi_channel.py`）就此
  作废不再 apply；羊毛 key 公共流传，勿单独依赖（每模型都有 ch33 兜底，
  风险已被姿态覆盖）。
- 压缩候选池扩容 r10（2026-10-11，用户指令）：全局压缩扩展
  `omp-global-compaction-model.js` DEFAULT 候选尾部追加
  `zg-newapi/u2-flash` / `zg-newapi/qwen3.8-flash-next` /
  `zg-newapi/step-5-preview`（三模型 24h 实弹健康：ch138/ch173/ch205+184；
  longcat 主位不动——10-09 用户定向）。revision r9→r10，deploy 脚本
  部署（live SHA 2B73BBBD 与源一致，备份+manifest 落盘）。测试基线对照：
  改前 16 过 5 挂，改后 17 过 5 挂——5 挂为 r9 存量（测试读 live
  models.yml 注册表所致，与本轮无关，未在范围内修复）。u2-flash 窗口：
  实测 >109K（24h 日志），官方页只写"long context"无数字，用户称 1M——
  未做满窗探针前按深兜底使用。
- **阶跃星辰官方 key 接入 ch216（2026-10-11 深夜，用户发 key，Base58
  解码）**：`stepfun-official` type=1 base `https://api.stepfun.com`，
  4 chat 模型（step-5-preview/step-3.5-flash/-2603/step-3.7-flash，镜像
  本地目录），p0/**w1** 同档末位 auto_ban=1；同档内为加权随机（上游语义
  w5:w2:w1≈5/8:2/8:1/8，主腿健康时新腿约 1/8 概率承接，本次主腿死亡
  未能实测比例）。脚本
  `scripts/ops/add_stepfun_official_leg.py`（复用 welfare 骨架+归因限流
  重试；修 `or -1` 对 priority=0 的 falsy 误判，welfare 脚本同源同修）。
  **接入即救场**：admin 直测 ch205 `no left credit for step plan`（套餐
  额度耗尽）、ch184 `Endpoint is unavailable`（zen 中继失效）——双腿
  俱死，step-5-preview 实流量经 429 穿透全部落 ch216（网关 200，日志
  归因实证）。快照 `new-api-before-stepfun-official-20261011-235801.db`。
- step flash 杂活档接入 OMP + ch216 提级（2026-10-12 凌晨，用户指令
  「可以接入干点杂活」）：step-3.5-flash/`-2603`/step-3.7-flash 接入
  `~/.omp/agent/models.yml`（官方规格：三者均 256K ctx；2603 官方
  `reasoning_effort low/high`；3.7 官方三档 low/medium/high + 多模态
  图文——按官方声明录入，maxTokens 32768 三模型实弹全接受；备份
  `models.yml.bak-20261012-step-flash-chores`）。**路由事故修复**：
  加权随机 p0 档内 ch205(w5，套餐额度死)/ch184(w2，端点死) 仍 enabled，
  ~5/6 请求撞死腿 429 且不回退 → 我建的 ch216 提 p0→**p10**（脚本常量
  同步），高位先尝试、死腿自动绕开，双腿留作回血后兜底；快照
  `new-api-before-ch216-prio10-20261012-001342.db`。**验证**：5 发网关
  全 200 全归因 ch216；`omp models` 88 ids 三条可见且 efforts 列正确；
  E2E `zg-newapi/step-3.7-flash:high --print`→`SF37_OK`(32.5s)；
  alignment 零新增孤儿；三模型 abilities (216,enabled,p10)/(205,p0)。
- OMP 配置审计 + 缺陷清扫（2026-10-12 凌晨续）：审计结论——99 selector
  引用零断链、6 扩展 SHA 零漂移、四 provider 端点全可达（401=仅探针无
  key）；发现 5 孤儿仍可被 OMP 选中即 500（intern-s2/ deepseek-v4-flash
  -vision/ qwen3.7-max-normal/ grok-chat-fast/ deepseek-v4-flash-0731，
  后端 abilities 全手动禁用）+ vision 回退链一环踩孤儿 deepseek-v4-flash
  -vision + 压缩池 2 个死候选（omen-alpha 从未注册、agentrouter/
  deepseek-v4-flash 该 provider 无此模型——死语义为静默跳过不崩）。揩
  除：models.yml 摘 5 孤儿（备份 `models.yml.bak-20261012-*-orphan-
  sweep`；88→83 ids、selectors 99→94）、config.yml vision 链删死环、
  压缩 r11（`deploy-omp-global-compaction-model.ps1`，live/源 SHA
  `4dcd8c624c0f` 一致，候选 11→10：删 omen-alpha、agentrouter/dsv4f→
  zg-newapi/deepseek-v4-flash，glm-5.2 注记过期已更正——现已注册有路
  由）。扩测同步：r10 曾把首个候选改为 longcat 但测试常量 TARGET 仍钉
  omen-alpha 致 5 挂未回补；r11 一并修复（TARGET=longcat、mock 换血、
  删档断言改现实值），`test_omp_global_compaction_model.js` 21/21 全绿
  （r9 基线 16/5），deploy 测试 1/1。
- ch91 any 组先收边后完全撤出：runbook `docs/ops/any-agent-claude-gpt-restore-2026-10-10.md` §16-17（10-12）。
  §16：`channels.group=default,any` 挂组后渠道更新的 abilities 全量重建把 6 模型全铺进
  any，稀释 §14 隔离；删 4 行 any abilities（gpt-5.6-sol + 3 别名），备份
  `new-api-before-ch91-any-trim-20261012-162223.db`（backup API 一致快照；**教训：
  在线 WAL 库勿 cp 三件套后备份删 sidecar**）。§17：用户裁决按渠道名隔离
  （"anyrouter 是 any 的，jianzhile 是 jianzhile"），ch91 完全撤出 any——删
  abilities 2 行 + `channels.group` 回 default（备份
  `new-api-before-ch91-leave-any-20261012-162947.db`）。验证：sol/5.6-sol=503、
  新会话 126->126（stdout 重试行）、4min 无重建。**排查三事实**：affinity
  （codex cli trace）优先于 abilities，TTL 300s 内存量会话照走 ch91；ch126 上游
  当时 500"负载达到上限"（单腿脆弱性=§14 动机）；channel error 无 usage 不落
  logs 表只落 stderr。any GPT 现状=ch126 单腿 astra，sol 在 any 面悬空。
  §18（10-12 16:4x）：客户端投影修正——OMP models.yml 删 any-newapi 的 sol 条目
  +注释改 ch126-only；qodercli settings.json 删 sol；cc-switch DB Any provider
  base_url 补 /v1 + notes 更新（原投影缺 /v1，写回即 §15 同款事故）。备份
  ts=20261012-163914。观测：16:30 后 any 零成功，ch126 过载窗口 10min+ 未回血。
  §19（10-12 17:2x）：ch126 硬饱和 47min+（6 连打 0/6，非瞬时拥堵），不回滚
  jianzhile——default 组盘点 5 条非 jianzhile 活腿（ch175 hubway、ch188-191
  jojatoken）以 abilities 行 p30/w1 只挂 `gpt-6-astra×any`（不动 channels.group，
  防 CacheSync 全铺）。验证 token10×astra 200，use_channel=["126","188"]
  （500 触发同组 failover）。any 终态=ch126 主+5 兜底，sol/5.6-sol 不进 any，
  jianzhile 只在 default。备份 `new-api-before-any-fallback-legs-20261012-172243.db`。
  §20-22（10-12 17:3x-17:5x）：UA override 对 CF 1010 无效（TLS 指纹层）；
  failover 选腿按 **channels.priority**（abilities 只是缓存，双写缺一不可）；
  403 不在重试码表会断链——照 ch126 先例给兜底腿补 SCM `"403":"503"` +
  RetryTimes=6，链单次遍历 7 跳机制满分。ch182 asvla 启用（CF 间歇门）；
  ch217 ip-glm53 接入 default glm 池（仅 glm-5.3，非 any 腿）。any 可用性
  =CF 边缘放行率的函数，机制已最优，观测 ch126 stderr 停刷+token10 200。

- **OpenCode Go 三模型聚合与全线死腿收口（2026-10-12 晚，用户指令
  「OPENCODEgo只能用这三个模型 继续；newapi」）**：许可集（plan 级，
  用户确认）=longcat-2.5-preview-free / step-5-preview-free /
  deepseek-v4.1-flash。上游**未**技术锁定三模型（glm-5.2/space-bunny/
  mimo 对照探针全 200），合规靠网关侧收口：Go key 全表指纹扫描仅
  ch133/184/218 共享，暴露面恰三个许可 id（alias 均映射回许可 id）。
  新第三腿 ch218（`scripts/ops/add_opencode_go_deepseek_v41_flash_channel.py`，
  克隆 ch133 key+session 头，p0/w2 auto_ban=1，donor 不改不打印）：
  disabled 探针 2.1s→enable→fix→abilities (default,1,0,2)→enabled 探针
  1.3s→relay 200 全绿；**ModelRatio 不动**（共享 id，设 0 会清零
  ch203/208 付费流量计费口径）。
- **聚合报错收口（每腿根因实测原文）**：手动停用 ch184（`429 Endpoint
  is unavailable`，17:42 起持续，管理测试+直连 ×10 一致）、ch203
  （`weekly Clinepass limit resets in 3d`，10-16 重置）、ch205（`no left
  credit for step plan`，step-3.5-flash 同错=plan 级全空）。停用后 relay
  实测：kimi-k3/glm-5.3/deepseek-v4.1-flash 直落活腿 200；
  step-5-preview/deepseek-v4-pro 0s 确定性 503（替代多跳白烧）。
  longcat 主腿修复：ch133 p0→p1（`PUT /api/channel/` 拒 Invalid
  parameters →DB 写 channels.priority+`/api/channel/fix`，abilities 即
  跟随；与 §20-22"双写缺一不可"结论一致），桥降 p0 真兜底；relay
  归属由 5/7 桥彩票（150s 504）变 ch133 直落 2.9/4.5s。回滚+恢复操作
  卡：`~/.new-api-local/ops-notes-20261012-disable-dead-legs.md`。
- **zen free 实测与死腿摘除**：free tier 唯一 sanctioned transport=
  opencode CLI 桥（匿名/裸 key/带 key 直连一律 `403 FreeTierError: only
  from within OpenCode`；zen/go 匿名 chat 先 400 MissingSessionID 再
  401 `Go requires a credential`——zen/zen/go/openrouter 三链目录均可
  匿名浏览、调用全要凭证，**无匿名模型**）。**目录≠可用**
  （nemotron-3-ultra-free 不在 43-id 目录 CLI 仍 200；jev-1.13-free
  目录内 CLI 反拒）。桥单飞（150s 超时+600s 冷却）禁并发探针、双干净
  证据才摘：ch178 摘 6 死腿（muse-1.3、big-pickle、space-bunny-free、
  mimo-v2.6-flash-free、ling-3.1、ling-3.0-flash-fin），活 5 成员
  （nemotron-ultra/3.5、muse-1.2、step-5-preview-free、longcat 兜底）
  全部同窗 relay 200 对照。muyuan ch173 同法摘 5（200k×2=muyuan 自身
  分销无上游 `under group auto (distributor)`、voxtral-mini×2=400
  Invalid model、glm-5.2=401 per-model 权限），留 21 活（17 chat+4
  embed 含维度实测）。探针三戒：CF 前直连须带浏览器 UA（opencode.ai/
  muyuan.do 实测破 1010，区别于 §20 jianzhile TLS 指纹层案例）、多行
  key 先 split、`sk-` 一律消毒。
- **门禁增强**：`verify_omp_newapi_alignment.py` 新增 QUARANTINE 名单
  （临时死腿与 r11 型"从未存在"孤儿区分：条目保留 models.yml、恢复零
  注册；覆盖"行禁用"与"行摘除"两形态；`--no-quarantine` 保留严格
  语义）——verdict CLEAN/12Q、strict 列全 12；cmp 21+1 全绿；
  `omp -p --model zg-newapi/deepseek-v4-flash` 端到端 84s OK。备份链
  183053/184820/185847/191125/192625/193643。

- **福利腿与新渠道接入及 OMP 治理（2026-10-13）**：
  - **jhsy gpt-6-luna (ch219)**：接入 `https://ai.jhsy0721.xyz`，新建 ch219，p30/w5 设为池头，auto_ban=1。
  - **lucky0625 全量福利腿 (ch220)**：接入 `https://new.lucky0625.qzz.io`，实测 23 个存活模型（含 chat-* 三重奏与 gemini 系列），因 5 RPM 硬限制定位于 p-20/w1 深度兜底。补齐缺失模型 0/0 定价。
  - **muyuan-gongyi (ch173) 扩容**：接入 `deepseek-v4.1-flash` (p26/w5)、`glm-5.3-flash` (p20/w1) 以及 mistral 系列新模型；修复 PUT 重置 abilities quirk 并验证双 key 轮询。
  - **cpa2-clinepass (ch221)**：接入 `https://cpa2.803099.xyz` 的 `cline-pass/deepseek-v4.1-flash`，模型映射至主池，p22/w2。
  - **S2API / ss2a (ch183)**：更新为双 Key 轮询 (`is_multi_key: true, size: 2`)，接入 `deepseek-v4-flash` 与 `glm-5.3-flash`，剔除 502 的 4.1-flash，p25/w5。经网关实测转发归因命中 ch183。
  - **chengapi-gemini (ch222)**：灰测接入 `gemini-3.8-flash-high`，后续 Key 失效 (401)，已自动禁用并加入隔离名单待恢复。
  - **OMP 路由治理**：`modelRoles.default` 锁定为 `zg-newapi/qwen3.8-flash-next:max`；彻底清理 `fallbackChains` 中残留的 `gemini-3.8-flash` 与 `step-5-preview-free`；`test_omp_routes.py` 40/40 OK，`verify_omp_newapi_alignment.py` CLEAN。
  - **OMP 接入 glm-5.3-flash（13:05）**：models.yml 注册 `glm-5.3-flash`（4 腿池 ch208 p30/ch118+ch183 p25/ch173 p20；131K/32K；effort 档 low→max；备份 `.bak-20261013-125830-before-glm-flash`）。`tiny`/`smol` 兜底链首位插入 glm-5.3-flash（tiny 主 gemini 5RPM 打爆先落 4 腿池而非 2 腿 deepseek）。网关 200/1.49s 归因 ch208 池头；`omp bench` TTFT 2.2s / gen 89.6 tok/s；门禁 40/40 + CLEAN。
  - **codex-auto-review 隔离（13:0x）**：ch175 hubway 12:48 被外部 manual operation 禁用（本会话未调用其 status 接口），该 id 唯一活腿熄灭 → 入 QUARANTINE，hubway 恢复后重开。
  - **ClinePass 福利双 key（ch223，fail-closed）**：runbook `docs/ops/clinepass-welfare-1013-2026-10-13.md`。OpenAI 兼容端点 `api.cline.bot/api/v1/chat/completions`；两 key 活着但 5h 窗口打满（429 INFERENCE_CAP），14 id 映射（含 mimo-v2.6-flash/pro 新池名 0/0 价）双 key 轮询 p10/w1；≈21:55 CST 回血绿测后启用。同批外部 manual 禁用潮（ch175 12:48 / ch220 15:37）→ codex-auto-review + gemini-3.8-flash 入 QUARANTINE；tiny 兜底链 E2E 实证落 glm-5.3-flash（日志 266488 ch183）。
  - **聚合池报错归因修复（19:20-20:0x，runbook `docs/ops/pool-error-triage-2026-10-13.md`）**：8866 条渠道错误普查+上游直连判别定三因——① ch208 上游钱包 $0.003→负（预扣费 503 随规模必挂、不按 auto_ban 摘除；**消息系上游实例配额透传，本网关 users/tokens 实测无限**）→ 降权后禁用，k3/m3 现无腿待充值/ch223；② OMP 注入 `prompt_cache_key` ch209 拒 400（ch173 真凶是 **reasoning_effort=xhigh/medium 拒档**，直连上游字节级复现，各腿枚举异构入 runbook 普查表）→ ch173/ch209 param_override 删 pck+条件 remap 档位，ch209 remap 经禁用判别验证兑现；③ ch118/ch183 双 5h 窗口与 opus 池饱和属容量项列遗留。门禁 40/40+CLEAN。

- **BigModel ch207 免费模型 + 阶跃福利双 key（2026-10-14）**：
  - **ch207 bigmodel-glm53-gift 免费模型（休眠挂载）**：官方 11-id 目录无免费 id，
    但礼品 key 直调免费层 200（glm-4-flash-250414 / glm-4.7-flash / glm-z1-flash /
    glm-4v-flash / glm-4.1v-thinking-flash 实测；glm-4.6v-flash 拥堵=1305，而
    **1305 实为 HTTP 429**，故无假成功面、无需 status_code_mapping）→ DB 直写 models
    追加六个免费 id + ModelRatio 置 0（六 id 在 abilities 零承载，无共享 id 计费改写；
    glm-5.3 0.7 / glm-5.3-flash 0.075 未动，不写 ModelPrice），fix 后 ch207 abilities
    8 行 enabled=0；网关探针 `glm-4-flash-250414` → 503 model_not_found = 预期
    fail-closed（启用前的休眠验证）。快照 `newapi-before-zai-free-models-20261014-155246.db`；
    runbook `docs/ops/bigmodel-ch207-free-models-2026-10-14.md`。
  - **ch207 已启用（用户同意，2026-10-14 17:30）**：服务端费率断言六免费 id 全 0
    → 备份 `new-api-before-stepfun-official-20261014-173040.db` → status 2→1 + fix
    → 8 abilities 行 `(default,1,20,1)`；admin test `glm-4-flash-250414` 200
    （0.596s）；75s 缓存窗后网关 relay 200（usage 10/3/13）**归因 ch207**；读回
    status=1/8 模型/p20/w1/key49。双门禁复跑 40/40 + CLEAN（86 ids 0 无路由）。
    OMP `models.yml` 注册六个免费 id 仍为可选项、未做。
  - **阶跃福利双 key（公开福利帖）**：key1 @`api.stepfun.ai/step_plan` 活但被薅饱和
    （429 并发 9/8）→ ch205 teardown 后第 1/2 次创建均被上游门拒（step-3.5-flash 429，
    门未弱化），第 3 次过门重建为 **ch224** step-plan（p0/w5、6 id、key 文件转 base58
    blob，旧 blob 留档 `step-plan-key-drained-20261014.txt`）；绿测 step-3.7-flash
    (4.57s/流 15 chunk) 与 step-5-preview (1.62s/流 21 chunk) 网关 200、6 行日志归因
    ch224 零空回。key2 @`api.stepfun.com` 首探 200、约 15 分钟后全模型 **402 额度耗尽**
    → 新脚本 `scripts/ops/rotate_stepfun_official_key.py`（dry-run 已验，含 p10→p0/w1
    事故挡位回位）未应用，ch216 保持禁用。快照 163045/163348；verify CLEAN（隔离
    15→11，step 四项解除）、`test_omp_routes` 40/40。
  - **复核修正（咨询评审）**：`GET /api/option/`（服务端内存）实测六个免费 id
    ModelRatio 已为 **0**（DB 直写已生效，glm-4.7-flash 不再残留 0.5）——启用 ch207
    前须先断言服务端比值，见 runbook；`rotate_stepfun_official_key.py` 已改为
    PUT → **先启用**（status 1）→ fix → abilities（禁用渠道 abilities.enabled=0 会
    卡断言）→ admin test → 缓存窗 → relay，归因断言放宽为 {224,216}（双 p0 腿）；
    key 形态确定性判别：两把新 key 均含非 base58 字符=裸 key，key1
    @stepfun.com 401（step_plan-only），旧 blob 两段均 "no left credit"（无第三腿）。

- **muyuan ch173 目录轮换同步（2026-10-14，runbook
  `docs/ops/muyuan-model-sync-20261014.md`）**：上游 token 组目录 31→7。
  ch173 PUT 裁至 **4 id**（deepseek-v4.1-flash/glm-4.5-flash/glm-5.3-flash/grok-4.7；
  `test_model` 换 glm-4.5-flash；旧 qwen3-8-27b model_mapping 清除；备份
  `new-api-before-muyuan-sync-20261014-224602.db`；脚本
  `scripts/ops/sync_muyuan_models_20261014.py`）。剔除 22 id：21 个上游 503
  `model_not_found`（mistral/ministral/codestral/voxtral 家族、qwen3.8-flash-next、
  glm-5.2 等）+ **qwen3.8-27b**（目录在册但聊天 504×5/1h，Cloudflare 间歇，
  注册门拒绝）。jev-latest/preview 400 `invalid_request` → 留官方桥 ch181。
  OMP `models.yml` 86→**68**（块级删除带字节级回证明；文件现为 LF，备份 LF
  行数差恰为删除数）；glm-5.2 主来源改标 ch220（ch15 已死）；
  `config.yml` vision 角色→`glm-4.6v`（vision 实测正确描述图像，ch185）、
  vision 链→`[qwen3.8-max, step-3.7-flash]`（voxtral 死、agnes 403 移出）、
  embedding→`jina-embeddings-v3`（200/dim1024/ch187）；补
  `zg-newapi/kimi-for-coding` 审批 fallback 链（OMP 自行把 default 切成
  kimi，门禁审批形状）。双门禁 40/40 + CLEAN（orphan 18→1，余 Atria 存量）；
  smoke FAIL 集合与基线逐项一致。遗留：qwen3.8-flash-next 无替代（长会话
  主力，建议迁 qwen3-8-27b/gemini-3.8-flash）；models.yml 待 OMP 重启生效。

- **ch208 happycoding 欠费禁用（2026-10-14 23:01，runbook
  `docs/ops/ch208-wallet-disable-2026-10-14.md`）**：用户报欠费未 ban——
  上游明报预扣费失败（余额 $0.126→$0.0248/28min），但 3h 仍有 286 笔
  小额成功故 auto_ban 不触发；10-13「降权后禁用」只落了降权。执行
  status=2（备份 `new-api-before-ch208-disable-20261014-230149.db`），
  glm-5.3 网关转 503 fail-closed。ch208 是 glm-5.3/kimi-k3/minimax-m3
  sole source：config.yml plan/designer/task 与 slow 链重指 `glm-5.2:max`
  （ch220 活腿，reasoning budget 端到端 200 归因 ch220）；**advisor 保持
  glm-5.3 暗路由**——白名单 {glm-5.3, omen-alpha, space-bunny} 全灭，
  改名单待用户批。QUARANTINE 注册表加 glm-5.3、修正 glm-5.2 过时条目。
  双门禁 40/40 + 1 orphan（存量）。遗留：advisor 裁决、ch220 单腿饱和
  风险、kimi-k3/minimax-m3 充值前 503。
- **advisor 重指获批为常态（2026-10-14，同 runbook 补记）**：advisor →
  `zg-newapi/glm-5.3-flash`，gate 白名单已正式加入（用户确认；ch208
  充值回开后 glm-5.3 可回任，二者并行）。~~运行中的 OMP 未重启~~
  **已重启（2026-10-14）**：gate 40/40、`omp models` 生效、glm-5.3-flash
  relay 200 归因 ch183。遗留收敛为：ch220 单腿饱和观察、kimi-k3/
  minimax-m3 充值前 503、qwen3.8-27b/qwen3.8-flash-next 观察。

- **deepseek-v4.1-flash 免费化（2026-10-15，runbook
  `docs/ops/dsv41f-free-pricing-2026-10-15.md`）**：opencode-go 免费腿
  ch218（10-12 接入，p0/w2）已承担该名全部实流（24h 30/34 行），但共享 id
  当时未钉价、按隐含 37.5 虚增本地 quota。付费腿 ch15 sensenova admin
  test 403（计划外，auto_ban=0 常驻不干活）→ 钉价无付费计费改写。
  服务端钉 `ModelRatio/CompletionRatio = 0`（备份
  `new-api-before-dsv41f-free-20261015-161205.db`，读回断言，邻居未动）；
  models.yml 改标 opencode-go FREE 主腿。门禁 40/40；smoke
  unregistered-ratio 违规该名消失（余量为存量基线）。回接付费腿前提回比率。
  能力实测（同日）：contextWindow 262144→**1048576**（阶梯探测 8 档全 200，
  1.27M→400 明报 1M 硬顶）；maxTokens 131072→**200000**（用户指示，上游
  实测接受）；thinking 五档 low..max 全生效（reasoning_tok 16→27，异于
  muyuan 拒档）。models.yml 改动待下次 OMP 重启生效。
  同日补：dsv41f 主路迁 opencode——ch15 摘除该名（sensenova 计划外 403 墙，
  库直写）+ ch218 p0→p30，relay 200 归因 ch218（备份
  `new-api-before-dsv41f-reroute-20261015-163928.db`）。

- **死模型大扫除（2026-10-15，runbook `docs/ops/dead-model-triage-2026-10-15.md`）**：
  用户报「几乎都用不了」。归因：ch220 lucky0625 福利钱包负额（$-0.000278，
  auto_ban 正确）→ glm-5.2/gemini-3.8-flash/agnes 全灭；agnes 账号受限波
  （ch68 上游死/ch69 账号封）→ haiku/LongCat-2.0 灭；ch109 移除 mimo +
  ch111 钱包空 → mimo/grok 全家/muse-contributor 灭。孤儿 12 全为上游真死，
  无腿可捞。修复：plan/designer/task/slow 链 → **k3**（ch33 活、1M、满档，
  relay 200 归因确认）；tiny → glm-5.3-flash；链清尸（mimo/haiku/agnes 链）；
  QUARANTINE +13 条死因条目，verdict 12→1；双门禁 40/40。遗留：ch220 回血
  观察、agnes 解封观察、OMP 待重启生效。

- **收尾清理：OMP 仓补提交 / Atria 定案 / ch178 桥回加（2026-10-15 晚，
  runbook `docs/ops/followup-cleanup-2026-10-15.md`）**：
  - `~/.omp/agent` 补提交 6 天未提交改动（commit `f7a0e75`；含 compaction
    扩展死候选 `qwen3.8-flash-next` 移除、mcp.json +jev/+rea、`.gitignore
    +*.backup`）；
  - Atria ch139 定案 **不回开**（admin test 200/success=false，上游 429
    `Token quota exhausted`；Guardian 12 轮探测预算用尽）→ 入 QUARANTINE，
    alignment **CLEAN：0 orphan / 20 quarantined**；
  - ch178 zen 桥回加新脚本 `sync_zen_free_bridge_models_20261015.py`
    （catalog=`opencode models`；bridge 真实补全探活，重试×3；503 busy ≠
    故障；默认 **additive**，`--allow-drop` 才摘除）：5→11 模型（re-add
    big-pickle/cat-jam-free/deepseek-v4.1-flash-free/mimo-v2.6-flash-free/
    muse-spark-1.3-contributor-free/space-bunny-free），备份
    `new-api-before-zen-free-bridge-recover-20261015-173645.db`，readback +
    admin test 全过；smoke 17:38 轮 FAIL 集合与基线逐项一致；
  - **slow 已重指 k3（2026-10-15 晚，待 OMP 重启）**：`claude-opus-4-8`
    全天 0%（perf req=373 ok=0；ch72 唯一腿 admin test 429 Service
    Unavailable，ch94/95 status=2）→ `modelRoles.slow` 改 `k3:max`、
    `fallbackChains.slow` 改 `[qwen3.8-max]`（避 DeepSeek 静态门禁 +
    不重复主选），`test_omp_routes` 40/40；
  - **待裁决**：ModelRatio 缺口 6 名（qwen3.8-flash/glm-4.6v/jina-embeddings-v3
    等，值待用户给）。

- **全渠道体检 + 状态更新（2026-10-15 晚，runbook
  `docs/ops/channel-health-sweep-2026-10-15.md`）**：102 渠道零成本遥感 +
  51 渠道真探针（33 enabled 全量 + 18 条有恢复线索的 disabled）。
  - **ch208 happycoding 回开**（钱包回血，glm-5.3 3/3 探针 200）→ 恢复
    glm-5.3 / kimi-k3 / minimax-m3，并给 glm-5.3-flash、deepseek-v4.1-flash
    各加一腿；
  - **park 三条硬证据死腿**（status=2 + auto_ban=1）：ch127（402 预算池）、
    ch186（403 订阅额度不足，qwen3.8-flash 唯一腿）、ch224（step-plan 额度尽）；
  - enabled 死腿未动（ch3 超时/Guardian 降权、ch72 窗口型、ch171/ch182
    瞬态存疑、ch181/ch204 本地桥）；
  - **装甲三处被停用待裁决**：`NewAPI ch3 100xlabs Reprobe`、
    `AnyRouter Window Canary`、`NewAPI Guardian Watchdog` 均 Disabled；
  - 门禁：alignment CLEAN（0 orphan/24 quarantined）、smoke FAIL 8 类（当晚经
    RetryTimes 修复降至 7 类，见下条）、enabled 33→31；备份
    `new-api-before-chstatus-20261015-1809*.db`。

- **运行时三警告修复（2026-10-15 晚，runbook
  `docs/ops/runtime-warnings-fix-2026-10-15.md`）**：① memory auto-review
  双传输失败——override 钉在已死的 `agnes-2.5-flash` → 改
  `deepseek-v4.1-flash`（`~/.pi/agent/hermes-memory-config.json`，下次启动
  生效）；② advisor 回退落到已死 `claude-opus-4-8` → 链改
  `[deepseek-v4.1-flash]`；③ advisor 530（ch118 CF 抖动，530 不在
  retry codes）→ 网关 `RetryTimes` 6→1 回归契约（房规脚本，即时生效）。
  smoke `automatic channel recovery` 由 FAIL 转 OK，**FAIL 基线 8→7 类**；
  `test_omp_routes` 40/40。遗留：subprocess 传输 Windows CLI 解析（上游
  缺陷）、530 入 retry codes 待裁。

- **本机优化（2026-10-15 晚，runbook
  `docs/ops/machine-optimization-2026-10-15.md`）**：① 清理可再生成物
  **+3.0 GB**（codex daemon 旧版本 2.15 G、npm 缓存、七月 WebView 备份、
  陈旧插件备份、guardian 旧 task-backups、codex 日志表 62.8 M）；
  ② 看护装甲**维持原状**：10-10 同晨批量停用的 `NewAPI Guardian Watchdog` /
  `ch3 100xlabs Reprobe` / `AnyRouter Window Canary` / `OMP AutoUpdate` 均
  保持 Disabled（曾短暂启用，经评审核对为有意停用后复原），列裁决项；
  仅两条有界清理任务（`OMP Session Log Cleanup`、`Codex Log Cleanup`，
  10-04 后停用、非同一批次）恢复 Ready；③ 待裁：会话数据保留策略
  （OMP 3.0 G / codex 1.2 G）、new-api backups 轮转（1.4 G）、上述四处任务。

- **NewAPI 门禁归因清理（2026-10-15 晚，runbook
  `docs/ops/newapi-gate-attribution-2026-10-15.md`）**：冒烟 FAIL 基线
  **7 → 5 类**。① `ch57/68/69` 入 `DEGRADED_ACCEPTED_DISABLED`（gorouter
  余额 = README:229 背书、Guardian 无记录；agnes 双腿上游死/账号受限）；
  ② **ch39 `ai-168661-grok` 复原**——10-11 11:42 快照仍在、10-12 23:50 起
  缺失，窗口内 commits/docs/guardian.log 无删除记载，契约与 KNOWN_BROKEN
  注释均要求保留该隔离恢复点 → 判为无记载误删，按 yanglechu-114217 快照
  逐字段复原（复原前快照 `new-api-before-restore-ch39-20261015-1900{00,23}.db`），
  逐模型复核**零新增路由**；③ DB 备份轮转回收 **1162 MB**（13 份旧 backups/ +
  7 份主目录 legacy `.bak-*`），保留 12 份（733 MB）。门禁：`test_smoke.py`
  47/47、alignment CLEAN。

- **聚合渠道报错定位与修复（2026-10-15 晚，runbook
  `docs/ops/aggregation-triage-2026-10-15.md`）**：16 条 enabled 多模型渠道 ×
  全部模型 = **84 对探活矩阵 → 63 OK / 21 FAIL**。① 裁目录 6 条渠道：ch15
  `sensenova-u1.5-lite`(404)、ch118 四名（qwen3.8-max / grok-4.5 /
  qwen3.7-max 500×2 净窗、claude-sonnet-5 402）、ch188-191 `gpt-5.5`（上游
  "not supported when using Codex"）；整渠死 ch3/ch72/ch204 不裁；
  ② 4 个新 orphan 入门禁（Q CLEAN 恢复）；③ OMP 死链修复：vision 双跳全死→
  `[zg-newapi-anthropic/intern-s2-preview, zg-newapi/dots-3-note-prev]`、
  plan hop2→`glm-5.3`、slow→intern-s2-preview、compaction 候选
  `gpt-5.5→glm-5.3-flash` 并删 `glm-5.2`/`step-5-preview`；
  ④ 错误来源定性：`(distributor)` 串 = 本地 fork 自带、`Not enough quota to
  pre-consume` = 上游透传；⑤ 附带：`muse-spark-1.2-contributor-free` 已自愈
  （ch178 9.1s 200）。门禁：`test_omp_routes` 40/40、alignment CLEAN、冒烟
  5 类、`ops-gates` 11/11。

- **聚合三补（2026-10-15 晚，同 runbook）**：① 单独归因上游余额类
  `Not enough quota to pre-consume`（gpt-5.6-sol ×5；四条 jojatoken 腿 key
  指纹互异、报文 remaining 历次 0.008–0.30 有升有降；直连被 CF 1010 拦；
  网关同尺寸大请求 200 → 尺寸/余额型间歇，**裁剪与链修复覆盖不到**，选项=
  充值/第二货源/接受）；② **compaction 双副本漂移修复**——repo 源同步为 live
  r12（此前停在 r11，部署脚本默认 repo→live 覆盖会回滚当晚候选修复），
  扩展测试同步至 r12 口径 **21/21**；③ `omp-extensions` check 增补
  compaction 源+测试（此前无门禁）→ **100/100**；④ muse-spark-1.2 改为
  "未定论"（3 连 200 但上游目录未回列，宽限不取消）。

- **zen-free-bridge 静默截断修复（2026-10-15 晚，runbook
  `docs/ops/zen-bridge-truncation-fix-2026-10-15.md`）**：桥只发
  `messages[0]` 且截 32k → 全量序列化 + 去截断 + image fail-loud；重启后
  决定性验证（38k 标记读回 ✓、多轮历史 `GIRAFFE` ✓）；新测试
  `test_zen_free_bridge.mjs` 7/7；cat-jam-free 注册（实测 ≥200k ctx，
  tools/vision/reasoning 均不支持）。
- **agentrouter 上新接入（2026-10-15 晚，runbook
  `docs/ops/agentrouter-newmodels-2026-10-15.md`）**：群公告上新
  `gpt-6.1-sol`/`claude-opus-5-5` → ch180 += gpt-6.1-sol、ch86-136 +=
  `claude-opus-5-5` 并回开（上游池恢复）；网关实弹 200（ch180 / ch135）；
  更正：ch127 key 有效（dsv4f admin test 200；codex 系 402 预算尽→保持 park）、
  8788 代理健康（先前 401 是撞了它的客户端鉴权门，非 keys.json 失效）。
  **后续处置（评审要求的明确裁决）**：ch127 trim 到 `deepseek-v4-flash` 单名
  + 回开 → 给 dsv4f（default 角色）加第四条活腿；冒烟 FAIL 基线 5→4 类
  （opus-4-8 容量因 ch86-136 回开而过线）。
