# NewAPI 聚合池总览（2026-08-01）

本文件是 NewAPI 各模型聚合池的**当前事实快照**，防止文档漂移。任何渠道增删/权重调整/状态变化都应同步更新本文件。

## 1. deepseek-v4-flash 聚合池（十六源）

> **2026-08-02 nihaox-k3 + p0-systems 接入**：新增 ch59（nihaox-k3，weight=5）和 ch60（p0-systems，weight=5）两个 0731 版本渠道。nihaox 配额已用完（weight=0 禁用），p0 可用（weight=5）。**关键修复**：POST 创建渠道需要 `{"mode":"single","channel":{...}}` 包装层（之前平铺致 nil pointer panic）；PUT 更新不能含 `status` 字段（`UpdateChannel` 明确禁止）。

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch37 | tokenrhythm-1（基元） | 付费中转 | 20 | 1 | 快源（avg 5s），主源 |
| ch38 | tokenrhythm-2（基元） | 付费中转 | 20 | 1 | 快源（avg 6s），主源 |
| ch35 | cline-free 多账号池 | 免费 | 18 | 1 | 快源（avg 7s）；`model_mapping: deepseek-v4-flash→deepseek/deepseek-v4-flash` |
| ch47 | bazaarlink-flash-2 | 免费 | 12 | 0 | 快源（avg 5s）；base_url `https://bazaarlink.ai/api`（NewAPI 自动补 /v1） |
| ch15 | sensenova（商汤日日新） | 付费中转 | 10 | 1 | **2026-08-28 20:2x 禁用**（status=2 + abilities enabled=0 双保险）：token plan 配额耗尽返 429 `token plan entitlement exhausted`（k3 大 prompt 挤占同计划配额，见当日补记）；配额回血后手动恢复 |
| ch48 | opencode-go-flash | 订阅 | 8 | 0 | `https://opencode.ai/zen/go`（带 /v1 会 404）；OpenCode Go $10/月订阅；中速（avg 21s），从 22 降权 |
| ch42 | DeepSeek 官方直连 | 官方 | 8 | 1 | 官方稳定（avg 22s）；models 含裸名，官方别名走 `deepseek-official-v4-flash`；从 1 提权（用户要求） |
| **ch59** | **nihaox-k3** | **免费** | **0** | **1** | **2026-08-02 新增**：`https://k3.nihaox.cc.cd/v1`；**0731 版本**；**配额已用完，weight=0 禁用** |
| **ch60** | **p0-systems** | **免费** | **5** | **1** | **2026-08-02 新增**：`https://api.p0.systems/api/agents`（**不带 /v1**——初设带 /v1 致 NewAPI 拼 `/v1/v1/...` 404，去 /v1 后修复）；**0731 版本**；**可用** |
| ch56 | hf-deepseek-0731 | 免费 | 3 | 0 | `huggingface.co` 端点 `deepseek-ai/DeepSeek-V4-Flash-0731`；key 任意；IP 限流 20 突发/12 每分钟 |
| ch53 | atomcode-bridge | 免费 | 8 | 0 | 中速（avg 11s，2-22s）；本机 `atomgit-opencode-bridge`（Tailscale 100.83.32.95:9457，base_url 不带 /v1）；CodingPlan Lite 额度；从 3 提权（比 sensenova/opencode 快） |
| ch58 | hfspace-deepseek | 免费 | 2 | 0 | `2c2ch1u11-share-api-0.hf.space`（base_url 不带 /v1）；120 RPM/key 限流；上游慢（冷启动 60s+）；上下文非 1M |
| ch46 | bazaarlink-flash-1 | 免费 | 3 | 0 | 慢源（avg 32s）降权；10 RPM/150 每日加权扣量 |
| ch44 | codebuddy（WorkBuddy 本机） | 桌面依赖 | 5 | 0 | 慢源（avg 28s）降权；Tailscale 100.83.32.95:8787 |
| ~~ch55~~ | ~~inferx-deepseek-b~~ | 免费 | 1 | 0 | **2026-08-02 已禁用**（avg 65s 最慢毒瘤）；`model.inferx.net/endpoints`（不带 /v1）；每 100 万 token 免费 |
| ~~ch50~~ | ~~inferx-deepseek~~ | 免费 | 1 | 0 | **2026-08-02 已禁用**（avg 38s 慢源）；同上第一 key |
> **ch43（旧 Python 代理）已于 2026-08-01 删除**，被本机 `atomgit-opencode-bridge` 替代（ch53）。旧代理签名算法不对被上游拒，bridge 用正确 `atomcode-signing-v1` HMAC 签名 + 真实 UA，成功过上游验证。

## 2. glm-5.2 聚合池（七源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch14 | wintoken | 付费中转 | 20 | 1 | 主源权重翻倍 |
| ch35 | cline-free 多账号池 | 免费 | 10 | 1 | `model_mapping: glm-5.2→cline-free/glm-5.2` |
| ch37 | tokenrhythm-1 | 付费中转 | 10 | 1 | |
| ch38 | tokenrhythm-2 | 付费中转 | 10 | 1 | |
| ch44 | codebuddy（本机） | 桌面依赖 | 5 | 0 | |
| ch49 | inferx-glm52 | 免费 | 5 | 0 | `model.inferx.net/endpoints`（不带 /v1）；`glm-52`（上游 `cyankiwi/GLM-5.2-AWQ-INT4`）免费 |
| ch54 | inferx-glm52-b | 免费 | 5 | 0 | 同上，第二 key；与 ch49 轮换分摊容量 |

## 3. gpt 聚合池（centos 摘除后）

| 模型 | 渠道 | 权重 | auto_ban | 备注 |
|------|------|------|----------|------|
| gpt-5.6-sol | ch30 fastaitoken | 5→20（修复） | 1 | 2026-08-01 修复：abilities priority 10→50、weight 5→20（此前被降级致付费主源零命中，流量全压免费本机源） |
| gpt-5.6-sol | ch44 codebuddy（本机） | 5 | 0 | |
| gpt-5.6-sol | ch45 agentrouter（本机） | 5 | 0 | |
| gpt-5.5 | ch30 fastaitoken | 20 | 1 | 单源 |
| gpt-image-2 | ch30 fastaitoken | 20 | 1 | 单源 |

> ch16/ch25（centos.hk 同上游两 key）已于 2026-08-01 禁用：centos 上游账户欠费返 403「预扣费额度失败 用户剩余额度 ¥0.09」（该"用户"指 centos 账户，**非**本地无限钱包），且 NewAPI 对上游 403 默认不 failover，故双保险摘除（status=2 + abilities enabled=0）。
> **副作用**：gpt-5.6-luna / gpt-5.6-terra 仅挂 centos，摘除后零源，已从 OMP/Kimi 配置移除（避免选到报"无可用渠道"）；centos 充值/换 key 后可恢复 ch16/25。

## 4. claude-opus-5 / claude-opus-4-8 聚合池（四源）

| 渠道 | 来源 | 类型 | 权重 | priority | auto_ban | 备注 |
|------|------|------|------|----------|----------|------|
| ch94 | justwoker-opus-1 | 付费中转 | 8 | 50 | 1 | `https://api.justwoker.icu`；主源之一（8/26 ≈ 31%） |
| ch95 | justwoker-opus-2 | 付费中转 | 8 | 50 | 1 | 同上游第二 key；主源之一（8/26 ≈ 31%） |
| ch57 | gorouter 合并（三 key） | 付费中转 | 5 | 50 | 0 | `https://gorouter.app`；ch26/27/28 三把 key 合并换行分隔（5/26 ≈ 19%）。**多 key 正确姿势**：key 真实换行（0x0A）分隔 + `channel_info` 以 **bytes** 写入（`is_multi_key:true, multi_key_size:3, multi_key_status_list:{"0":1,"1":1,"2":1}, multi_key_mode:"polling"`）——str 写入致 GORM 二次编码、`multi_key_mode` 写数字、key 写 `\n` 字面量，三种坑都导致渠道不可路由 |
| ch116 | kktoken | 免费中转 | 5 | 50 | 1 | `https://kktoken.cc`（base_url 不带 `/v1`）；2026-08-28 深夜接入；四模型全 200（opus-5 1.8s / opus-4-8 1.7s / 两 thinking 变体 2.4-3.0s）；Cloudflare UA 门（python 默认 UA 1010，Go/NewAPI UA 放行）（5/26 ≈ 19%） |

> **2026-08-01 整合**：原七源 → 二源。ch26/27/28（gorouter 三把 key 同上游）合并为 ch57；ch45 权重 5→15 成主源；ch3/9/18（baibei/linxi-k40）直测全部 503 `All available accounts exhausted`（上游账户耗尽），ch3 已禁用（status=2 + abilities enabled=0）。
> **2026-08-02 终态**：ch9/ch18 曾被 auto-ban 赦免回 1 且上游恢复，测得 avg 51-67s 龟速拖慢 claude 池 → **重新禁用**（status=2 + abilities.enabled=0 双保险，防 AutomaticEnableChannelEnabled 再拉回）。claude 池最终二源：ch45 agentrouter（weight=15, priority=50 主源）+ ch57 gorouter（weight=4 备源）。
> **2026-08-28 深夜 kktoken 第四源（ch116）**：用户给的 `https://kktoken.cc` key 目录 4 模型（`claude-opus-5`、`claude-opus-5-thinking`、`claude-opus-4-8`、`claude-opus-4-8-thinking`），四者直连 completion 全 **200**（pong/stop，1.7-3.0s，thinking 变体带 reasoning）。新建 ch116 `kktoken`（base_url 不带 `/v1`，四模型全挂，p50/w5/auto_ban=1，脚本 `add_kktoken_claude_pool.py`，key 经 KK_KEY 环境变量入渠道行、不落仓库；备份 `new-api-before-kktoken-claude-*.db`）。与 ch94/95 同 p50 → 四源按权重 8:8:5:5 轮询。**Cloudflare UA 门**：python 默认 UA 被 1010 拦，`curl/Go-http-client/new-api/OneAPI/浏览器` UA 全 200——NewAPI 的 Go 客户端天然放行，无需特殊处理；直连探测须带 UA。
> **重要教训**：NewAPI 渠道禁用**必须双保险**——`status=2`（ManuallyDisabled）+ `abilities.enabled=0`。只改 status 不生效（路由过滤看 abilities 表；status=0 是 Unknown 非 Disabled，更无效）。之前 ch16/25/43 的「status=0 禁用」实际从未生效，靠 abilities 兜底。
> **保守后备守护**：`/opt/new-api/auto-ban-revive.py` + systemd timer `k40-baibei-revive.timer`（每分钟）赦免**所有**被 auto_ban 的渠道（`status=3 AND auto_ban=1`→1；替代原仅覆盖 ch3/9/18 的 k40-baibei-revive.py）。SQL 带 `AND status=3` 故手动禁用（status=2）不被误赦免；`auto_ban=0` 渠道（免费源）不赦免（有意不禁用）。判活权交 NewAPI 下轮定时测试，零误判。改 DB 后依赖 NewAPI channels sync goroutine（~1-2min）拉入内存。
> **2026-08-29 zzzcoding claude 第五源（ch123，暂禁用）**：用户给的 `https://api.zzzcoding.org` 新 key 目录仅 1 模型 `claude-opus-5`，且 OpenAI `/v1/chat/completions` 被拒（`/v1/messages only`）→ **Claude Code 订阅型中继**，走 type=14 Anthropic。新建 ch123 `zzzcoding-claude-opus-5`（base_url 不带 `/v1`，`header_override` 用 claude-cli 指纹 `User-Agent: claude-cli/2.1.158 (external, sdk-cli)` + `x-app: cli` + `anthropic-beta`，同 ch86 agentrouter-claude 成熟打法，p50/w5/auto_ban=1）。**实测不可用**：直连（Python/Bun，含完整 claude-cli 指纹 + stream + metadata）与 NewAPI 自身 relay 路径（`/api/channel/test/123`）均 **503 `No available accounts: this group only allows Claude Code clients`**——非客户端/指纹/配置问题（ch86 同指纹跑 agentrouter 正常），是中继侧该 key 的 group **无可分配账户**。ch92（zzzcoding GPT 组）亦 2026-08-19 后停用，佐证该中继池近期吃紧。ch123 暂 **disabled（status=2）不污染池**，待中继侧账户可用后重测 `/api/channel/test/123`，200 则置 status=1 入池。**后续深查推翻初判：中继池其实有货（真实 claude-cli 2.1.220 直连 zzzcoding = 200 成功并计费），但 `No available accounts` 是 TLS 指纹（JA3/JA4）白名单拦截**——用捕获代理验证：真实 claude 经 Python 代理转发（Python 建 TLS）重试 8 次全失败，直连却 200，同一客户端同一请求头仅 TLS 归属不同；Bun（OMP 运行时）全指纹也 503。结论：zzzcoding claude 组**只能由官方 Claude Code CLI（Node.js 栈）直连**，NewAPI（Go）/Python/Bun 任何代理都过不了 TLS 指纹，**无法接入聚合池**。ch123 保持 disabled 作为记录，`header_override` 已更新为完整 claude-cli 指纹（含 `anthropic-dangerous-direct-browser-access` + 完整 `anthropic-beta` + `X-Stainless-*` + `X-Claude-Code-Session-Id`）以备后用。**2026-08-29 二次深查彻底翻案**：zzcoding 无严格指纹校验，错误即字面义「池中无可用账户」。关键证据链：①`api.zzzcoding.org` 经本机 Clash Verge TUN（verge-mihomo）解析到 fake-IP `198.18.0.236`，此前所有「客户端被拒」实为**代理节点变量**，与 TLS 无关；②裸 TCP 监听器抓 claude.exe 真实 TLS ClientHello（cipher 17 项/扩展 12 项/ALPN=http/1.1），curl_cffi `ja3=` 精确复刻后 ClientHello **逐字段一致**却仍 503→**TLS 不是判别点**；③真实 claude.exe 直连与 curl_cffi 同一时刻**同 503 同错误**→无客户端区分；④14:41 冒烟曾 5.5s 单发 200 成功（池间歇回血）→key 有效。根因=**池间歇性耗尽**（Claude 订阅账户使用窗口，抢光即 503，回血即复）。处置：Clash Verge 全局规则加 `DOMAIN-SUFFIX,zzzcoding.org,DIRECT`（备份 `profiles/r9vStGA8zCBv.yaml.bak-zzzcoding-direct-*`）让客户端 TLS 端到端透传（排除代理节点变量；DIRECT 下 mihomo 纯字节转发）；ch123 由**自动门控** `scripts/ops/zz_gate.py`（hub 进程 `zzgate`，detached 常驻，on-failure 自重启）驱动：**60s 探测，单探 up 即启用**（抓瞬态窗口），**2 连探 down 才禁用**（status=2，不被 guardian 误赦免），停留 60s 防抖；翻转时 Windows 气泡提醒。状态 `~/.omp/zz_gate_state.json`，日志 `~/.omp/zz_gate.log`。key 从 `~/.claude/zzzcoding-settings.json` 读（ZZ_KEY env 可覆盖），不入仓。

**2026-08-29 三次深查（修正二次结论）**：zzcoding 无客户端判别，是纯争抢下的**亚分钟级瞬态窗口**。证据：①用户真实 claude.exe 15:14 经 zzzcoding 直连成功（19s+21s 一次通过），而探测 15:13:13/15:16:13 夹住该时刻均 503——用户请求在 15:13:35 左右恰好落进瞬态窗口；②池空时**真实 claude.exe 也收一模一样的 "this group only allows Claude Code clients"**（14:49-14:54 实测）——该文案是静态组描述，非客户端判决；③鉴别矩阵全灭：header 4 组（UA 2.1.220/2.1.251、anthropic-beta、x-stainless 全套）、auth 3 式（x-api-key/Bearer/双发）、claude 式 body（system+metadata.user_id+stream）、curl_cffi chrome120、claude 逐字节 JA3、ALPN h1-only——全部同 503，无可复刻指纹；④14:41 单发探测曾 5.5s 200。结论：窗口出现时谁抢到归谁；真实 claude 靠**内部持续重试**抓到窗口，定时单发探测大概率错过。含义：ch123 只能吃到被探测撞上的窗口（60s 探测+单探启用已最大化捕获率），它是有货即上的弹性主力而非稳定主力；用户直连（zzzcoding.cmd）因重试机制体验更好。直连启动器 `~/zzzcoding.cmd` 保留（池有货时真实 claude 可用）。备份 `new-api-before-zzzcoding-claude-20260829-133630.db`。
> **2026-08-30 自动门控终态（ch123 → p60/w10 弹性主力）**：`scripts/ops/zz_gate.py`（hub 进程 `zzgate`，detached + restart=always）驱动 ch123 跟随池窗口自动上/下线：**60s 探测、单探 up 即启用**（抓亚分钟瞬态窗口）、**2 连探 down 才禁用**（status=2 手动档，guardian 赦免循环不碰）、停留 60s 防抖、翻转 Windows 气泡提醒；探测请求带完整 claude 特征（anthropic-beta/x-stainless/system/metadata/stream）。启用=SQL status=1 + 渠道 API no-op 事件触发 sync goroutine 重派生 abilities（直接 SQL 改 status 不会重派生 enabled）。key 从 `~/.claude/zzzcoding-settings.json` 读（ZZ_KEY env 覆盖），不入仓。状态 `~/.omp/zz_gate_state.json`、日志 `~/.omp/zz_gate.log`。**Windows 锁文件血案（2026-08-30 15:19 实发）**：`os.kill(pid, 0)` 在 CPython/Windows 映射为 `TerminateProcess`——双启动场景把 supervisor 自动重启的旧门控活杀后自己 exit 1，5.7h 无门控在跑。修复：`pid_alive()` 用 `tasklist /FI "PID eq N"`（**GBK 容错解码**，zh-CN 下 text=True 直接 UnicodeDecodeError）非破坏检测；另主循环全包 try/except 自愈。教训：**Windows 上 `os.kill(pid, 0)` 永远不能当探活**。
> **2026-08-30 深夜终态修正（ch123 p60→p0 兜底层 + OMP 直连入口 zz-coding）**：p60 弹性主力被实测否决——空窗期手动启用 ch123(p60) 后 3 发真实 relay：req0 200/6.8s（ch123 503→NewAPI 重试 ch94 成功）、**req1 500 `do_request_failed`（failover 失败直接泄漏给调用方）**、req2 200/2.3s；且 NewAPI **auto_ban 不因上游 503 触发**（ch123 保持 status=1 继续吃流量）。即 p60 档在空窗期让每个 claude 消费者（含 Claude Code 15721 链）承担 2-7s 惩罚 + ~1/3 失败率 → **ch123 降级 p0/w1 兜底层**（主源 ch94/95/57/116 全挂才被路由），门控继续托管（角色=窗口通知 + 主源全灭保险）。**NewAPI ability 重派生真相**：sync goroutine 只在渠道字段**实际变化**时触发——同 remark 的 no-op PUT 等 150s+ 后 ability.enabled 仍 0，remark 加时间戳 diff 后 6s 内翻转；zz_gate.py 原 api_noop（回发当前 remark）因此存在隐性 enable 失败 bug，已改 `api_touch`（时间戳强制 diff + ability 验证失败单次重试）。**OMP 直连入口**：`~/.omp/agent/models.yml` 新 provider `zz-coding`（api: anthropic-messages → `https://api.zzzcoding.org`，key 同 `~/.claude/zzzcoding-settings.json` 不入仓），模型 `zz-coding/claude-opus-5`（contextWindow 200k / maxTokens 128k）；`config.yml` fallbackChains 新增 `zz-coding/claude-opus-5 → [zg-newapi-anthropic/claude-opus-5, zg-newapi/omp-sota-claude-opus-5]`，maxInFlightRequests zz-coding:2（保护订阅池）。池有货→满血直连（无 NewAPI 跳）；池空 503→OMP fallback 链落 NewAPI 池（同 qwen3-8-27b 400 修复的链式机制）；需 OMP 重启生效；`zzzcoding.cmd` 直连启动器保留（重试抓窗口体验仍最好）。**zz_gate.py 加固**：凭证加载提前到取锁前（settings 缺失 fail-fast，不留 stale lock）；锁改**心跳 TTL 180s + O_EXCL 原子获取 + 陈旧回收**——Windows TerminateProcess 跳过 finally 块，杀进程必留 stale lock（实测 hub stop 后遗留 pid 10964 锁文件；pid 复用卡死窗口被 TTL 有界化为 180s）；修复期间实测出现 68 次重启风暴（INTERVAL_S 常量在编辑中丢失致 NameError 循环崩溃），修复后 60s 间隔稳定多周期探测。smoke 归因：ch123 入 `DEGRADED_ACCEPTED_DISABLED` + `BACKUP_CHANNEL_POSTURES`（max_priority 0/max_weight 1，禁用宽容、启用封顶，防未来静默回 p60）。

## 5. 其他单源模型（非聚合）

| 模型 | 渠道 |
|------|------|
| sensenova-6.7-flash-lite | **零源**（仅 ch15 曾挂，2026-08-28 禁用；OMP 链条目命中将 503 后跳下一档，配额回血恢复 ch15 即复活） |
| grok-4.5 | ch17 (w10) / ch29 (w20) / ch39 (w10) 三源 |
| gpt-5.5 | ch30 单源（ch2/16/25 已禁、ch41 已删） |
| gpt-5.6-luna | **零源**（仅 centos ch16/25 曾挂，已禁；ch41 已删） |
| gpt-5.6-terra | **零源**（仅 centos ch25 曾挂，已禁） |
| claude-sonnet-5 | ch57（ch26/27 已合并禁用） |
| qwen3.8-max-preview | ch31 |
| qwen3-8-27b | ch88/ch112/ch124 主档 p49/w1（1:1:1）+ ch113 兜底 p0/w1（2026-08-29 降权 B.AI；2026-08-30 接入 ch124 Groq 免费 qwen/qwen3.8-27b 450tok/s，model_mapping qwen3-8-27b→qwen/qwen3.8-27b；注意 Groq 的 Cloudflare 按 TLS 签名封 Python urllib（error 1010），Go/浏览器指纹正常，channel test+relay 6/6 验证） |
| qwen3.8-max-free | ch114 (w1, prio 0) / ch117 (w1, prio 0) 二源 1:1（2026-08-28 深夜聚合，opencode-go 第二源） |
| k3/kimi-for-coding | ch33 主源 + ch115 手动禁用（status=2，2026-08-28 晚）/ ch108（p49/w5）/ ch110（p6/w5） |
| step-router-v1 | ch36 |

> **2026-08-29 降权（ch113 → 兜底档）**：用户报 `400 credit insufficient`（B.AI credit 不足，12 tools + 32768 max_completion_tokens + thinking，差 7266）。三源原同 p0/w1 1:1:1 轮询，ch113 命中即 400。NewAPI 源码确认 `Order("priority DESC")`——**priority 数字大=优先**。调整：ch88/ch112 → p49/w1（主档 1:1），ch113 保持 p0/w1（兜底）。降权后 6/6 relay 请求全走 ch88/ch112，ch113 零命中。备份 `new-api-before-qwen38-27b-downgrade-20260829-132423.db`。

| glm-5.3 | ch108 (p49/w5) + ch45/ch120 agentrouter (p40/w5) | 三源（2026-08-29 聚合） |
| qwen3.8-flash | ch122 bai-qwen3.8-flash (p30/w5) | 单源（2026-08-29 接入） |
| glm-5.3-flash | ch121 bai-glm-5.3-flash (p30/w5) | 单源（2026-08-29 接入） |

> **2026-08-29 qwen3.8-flash 接入**：B.AI 免费目录含 `qwen3.8-flash`（200 'pong' 实测），与 `qwen3.8-max` 并列但为 flash 轻量版。新建 ch122 `bai-qwen3.8-flash`（`https://api.b.ai`，复用 ch111 key，`models=qwen3.8-flash`，p30/w5/auto_ban=1，status=1），OMP models.yml 新增 `qwen3.8-flash` 模型条目。暂为单源池，后续可按惯例聚合第二源。

> **2026-08-29 glm-5.3 聚合**：ch108 whyyin 主源 p49/w5；ch45 agentrouter 与 ch120 agentrouter-glm-5.3 均为 p40/w5（同一本地代理 `100.83.32.95:8788` 复用 ch45 key，GLM 不受 agentrouter.org Claude/GPT 分批限额影响）。B.AI 的 `glm-5.3` 为 premium（403 deposit-required），`glm-5.3-flash` 免费可用但**是不同模型**，不可映射进 glm-5.3 池——已修正为独立模型 `glm-5.3-flash`（ch121，p30/w5）。tokenrouter 候选 key 余额 $0.000000，未入池。
> **2026-08-28 补记**：本节原写 gpt-5.5 三源 ch16/25/30、luna 二源 ch16/25、terra 单源 ch25、sonnet-5 二源 ch26/27——均过时（ch16/25 已禁、ch41 已删、ch26/27 已并入 ch57）。以上为实测后版本。

> **2026-08-28 补记**：ch88 `runinfra-qwen3-8-27b`（`https://api.runinfra.ai`，单 key，weight=1，priority=0，auto_ban=1，group=default）后补接入，为 `qwen3-8-27b` **唯一源、未聚合**；该模型现是 OMP default 模型（兼 commit/smol/translator 角色与 compaction 第 4 候选），单源风险点。后续聚合须按三坑教训配置新渠道（group=default、priority=50、正确 weight）。
> **2026-08-28 聚合落地**：`qwen3-8-27b` 二源池 —— ch88 `runinfra-qwen3-8-27b`（weight=1, priority=0, auto_ban=1，零改动）+ 新增 ch112 `yjs-qwen3-8-27b`（`https://api.yjs.im`，复用 ch110 yjs-free 的 key，上游 id `qwen-3.8-27b` 经 model_mapping 暴露为 `qwen3-8-27b`，weight=1, priority=0, auto_ban=1，group=default）。同优先级按权重 1:1 轮询，auto_ban 容灾。**机制实测（重要教训）**：abilities 表是 channel 行的派生物——行存在性镜像 `channels.models`，(priority,weight) 镜像渠道级字段，sync goroutine 在渠道 API 事件后重新派生（当日对 ch88 行直接 SQL 改 50/50，数秒内被还原为渠道字段 0/1；回滚 ch110 增模后孤儿行亦被 goroutine 清除）——**per-model 权重只能靠渠道级字段控制**；多模型渠道（ch110 挂 19 模型）不能为单个模型调权，故第二源建**专用单模型渠道**（与 ch88 同模式，ch88 单模型、字段无纠缠，零改动即成 1:1）。另坑：Python sqlite3 legacy 事务不显式 commit，DML 在 close 时静默回滚（当日 v1 脚本 verify 抓出，v2 已修）。供应商扫描（当日）：api.yjs.im（既有 key）与 OpenRouter `qwen/qwen3.8-27b`、HF `Qwen/Qwen3.8-27B` 均提供同一开源模型，取既有 key 零新账户。快照：`backups/new-api-before-qwen38-27b-pool-20260828-011003.db`；脚本 `scripts/ops/add_qwen38_27b_pool.py`（dry-run 默认）。OMP 侧零改动。
> **2026-08-28 param_override 核查**：ch88 带 override 剥 `prompt_cache_key`（runinfra 拒未知参数）；OMP 真实请求体另含 `stream_options`/`enable_thinking`。直连 api.yjs.im 逐参数矩阵实测四变体全 HTTP 200——带 `prompt_cache_key` 时 yjs 切缓存感知后端（响应 `model:"Qwen/Qwen3.8-27B"`+`chatcmpl-` id），剥掉反损失缓存局部性，故 **ch112 不加 override**。经网关完整 OMP 形请求 12/12 成功（同 cache key 被 "qwen trace" 亲和规则粘钉首命中渠道；本会话 key 钉 ch88，不受池变更影响）。
> **2026-08-28 晚间 B.AI 第三源（ch113）**：B.AI 免费档目录在 ch111 白名单划界后扩张（`/v1/models` 44 项 vs 白名单 5 项）。实测：ch111 key 直连 `qwen3.8-27b` **HTTP 200**（上游模型 `Qwen/Qwen3.8-27B-FP8`，pong/stop，3.94s），premium ID（claude-opus-5、kimi-k3）仍 403 deposit-required（ch111 remark 边界复核成立）。新增 ch113 `bai-qwen3-27b`（`https://api.b.ai`，复用 ch111 key，上游 id `qwen3.8-27b` 点号形经 mapping 暴露为 `qwen3-8-27b`，weight=1, priority=0, auto_ban=1, group=default，脚本 `add_qwen38_27b_bai_pool.py`，备份 `new-api-before-qwen38-27b-pool-20260828-160158.db`）。三渠道同 p0/w1 → 1:1:1。**已知项**：应用后 12 发功能测试首 20s 内 2 发上游 500 `do request failed`（logs 表不记失败行，渠道归属靠时间相关性：两发均贴首波 ch113 流量，其后 ch113 3/3 全成功，ch88/ch112 窗口内零失败）——B.AI 免费档冷启动/限流行为未压测，auto_ban=1 兜底；若 429/500 持续，处理路径=调 ch113 渠道级 priority 降级为应急档（等价于方案 a），勿直接 SQL 改 abilities。
> **2026-08-28 深夜 tokenrouter 免费模型（ch114）**：用户提供的 `api.tokenrouter.com` key 为 **token 级目录**（两个 key 各见一个模型）。key2 目录 `qwen/qwen3.8-max-free` 实测 200（上游 `qwen3.8-max-pd`，reasoning，pong/stop 4.3s）→ 新建 ch114 `tokenrouter-qwen3.8-max-free`（**建渠时 base_url 用 `https://api.tokenrouter.com` 去掉用户给的 `/v1`**——NewAPI type=1 自动补 `/v1`，带 `/v1` 会拼成 `/v1/v1/...` 404，ch46/47 bazaarlink 先例见三坑记录；探测直连用完整 `/v1` 无妨；mapping `qwen3.8-max-free -> qwen/qwen3.8-max-free`，p0/w1/auto_ban=1，脚本 `add_tokenrouter_free_model.py`，key 经 TR_KEY 环境变量入渠道行、不落仓库；备份 `new-api-before-tokenrouter-20260828-170559.db`）。新 OMP 模型 `qwen3.8-max-free`（1M/131K，text-only，不入任何角色链/压缩梯——用户仅要求可用）。key1 目录 `moonshotai/kimi-k3-free` 三次 503 "no available channel under group default (distributor)"——死目标不注册（zai 先例）。
> **2026-08-28 深夜 qwen3.8-max-free 二源聚合（ch117）**：OMP `default` 角色当日切到 `zg-newapi/qwen3.8-max-free:high`（在最后一次 config 备份之后，无回滚物），而该模型仅 ch114 单源、default 又按不变量无 fallback 链（硬失败）→ 单点故障。供应商扫描（逐 status=1 type=1 渠道直连 `/v1/models`，key 不落盘）：**无任何现存 relay 承载免费变体** `qwen3.8-max-free`/上游 `qwen3.8-max-pd`（仅 ch114）；付费档 `qwen3.8-max` 在 ch101 opencode-go 与 ch89 seeseed 均有，且二者对 "ping" 的 reasoning 签名与 ch114 的 `qwen3.8-max-pd` **逐字相同**（同一 Qwen 3.8 Max，不同档位/中转）；ch111 bai-free 的 `qwen3.8-max` 为 premium 锁定（403 deposit-required）排除。选 **opencode-go（ch101 key donor）**：活跃姿态 p10/w5、订阅维护中、全形流式探针更快（2.4s vs seeseed 5.4s）、与 tokenrouter 故障域不同。新建专用单模型渠道 ch117 `opencode-go-qwen3.8-max-free`（base_url `https://opencode.ai/zen/go` 不带 /v1，key 复制自 ch101，`models=qwen3.8-max-free`，mapping `qwen3.8-max-free -> qwen3.8-max`，p0/w1/auto_ban=1/group=default，脚本 `add_qwen38_max_free_pool.py`，备份 `new-api-before-qwen38-max-free-pool-20260828-234956.db`）。ch114 零改动，同 p0/w1 → 1:1。**验证**：12 发网关功能测试归属 {ch114:7, ch117:4}；当日 qwen3.8-max-free 全部 110 条 logs 均 type=2、零失败，真实大 prompt 流量 ch114:21/ch117:19 约 1:1。**潜在 effort 不对称（未触发，仅预警）**：ch114 上游只收 `low/medium/xhigh`（`high/max/minimal/none` 返 400），ch117 全收；OMP 该模型生效白名单为家族推断 `minimal,low,medium,high`、无 `thinking` 块故 `:high` 后缀**通过门禁但不实际下发** reasoning_effort（当日 110 条全成功即证），现状无碍。若未来给它加 `thinking` 块启用 effort 并保留 `:high`，ch114 将拒约半数流量——届时须把声明 efforts 改为两源交集 `[low,medium,xhigh]` 并把 default 引用从 `:high` 换 `:xhigh`。
> **2026-08-28 深夜 sensenova-k3 第四源（ch115）**：商汤日日新 token plan（`https://token.sensenova.cn`，**建渠时同样去掉 `/v1`**——同 tokenrouter，type 1 自动补 `/v1`，带 `/v1` 则 404；探测直连用完整 `/v1/chat/completions`）实测目录仅 6 模型（无 K3，需手工 mapping），候选 `kimi-k3` 直连 `POST /v1/chat/completions` **200**（`model:kimi-k3，pong/stop，7.0s`，`k3` 裸名 404）。新建专用单模型渠道 ch115 `sensenova-k3`（复用 ch15 的 key，`models=k3`，`model_mapping k3→kimi-k3`，p50/w10/auto_ban=1，group=default，脚本 `add_sensenova_k3_pool.py`，备份 `new-api-before-sensenova-k3-20260828-172247.db`）。与 ch33 同 p50/w10 → 头部 1:1 分担（ch108 p49/w5、ch110 p6/w5 为降级档）。**验证**：12 发网关功能测试 10 成功（纯净 prompt 91 计数 ch33:7 / ch115:3；全窗口 11:6 含并发用户大 prompt 流量），2 发 `TimeoutError`（上游 kimi-k3 瞬时超时，ch115 status=1 未被 auto-ban，属瞬时抖动；并发窗口内 ch115 另有 4 次大 prompt 真实命中 312k/163k/315k/316k，确认已入路由）。单发网关 `k3→kimi-k3` **200 5.8s**（reasoning 空 content，属 k3 思考模型常态）。**注意**：token plan 工作区配额跨模型共享（此前 ch15 上 glm-5.2 429 "workspace quota exceeded"），k3 流量激增可能波及同计划上 deepseek-v4-flash 压缩头部 Nam-cKdw 的可用性，必要时降 ch115 权重。
> **2026-08-28 夜间 sensenova 计划配额耗尽事件**：ch115（sensenova-k3）入池后 k3 大 prompt（多次 300k+）与 deepseek 压缩头部共享同一 token plan 工作区配额（ch115 脚注预警的风险兑现）。19:53/20:02 两次自动压缩 `Auto-compaction failed, retrying` ×3 → `ended without a summary aborted`，错误 `429 token plan entitlement exhausted` / `rpm exhausted`（quota_exceeded_error，param=8）——ch15 吸收流量返 429 但 429 不在 `AutomaticRetryStatusCodes`、auto_ban 亦不触发（quota 类 429 非连续失败语义），无 failover（centos 403 先例同款坑）。处置：ch15 双保险禁用（status=2 + abilities enabled=0），流量落 ch111（p30）/ch110（p6），网关 6/6 200 恢复；ch115 此前已被手动禁用（status=2，非 auto-ban）。**教训**：共享配额计划上的渠道，配额耗尽类 429 必须靠人工/守护摘除，NewAPI 自动机制不兜底；恢复路径=配额回血后 ch15/ch115 手动 status=1 + abilities enabled=1。
> **2026-08-29 凌晨 deepseek-v4-flash 第三源补齐（ch118）**：承上条——ch15 禁用后该池仅剩 ch111（p30/w5）+ ch110（p6/w5），而 `deepseek-v4-flash` 是多数模型的 `compactionModel` 兼 smol 链头（压缩失败即会话中断，见上条 `ended without a summary aborted`），2 源过薄。上游扫描：多个在启渠道的 key 目录含该模型（ch88 runinfra、ch89 seeseed、ch96/ch101 opencode、ch112/ch113 yjs/bai），但均为其他模型的专用渠道 → **复用 key、另建专用单模型渠道**（abilities 是 channel 行派生物，多模型渠道无法为单模型调权，同 ch112/113/115/117 打法）。候选直连 `POST /v1/chat/completions` 实测：**ch89 seeseed 200**（pong/stop，2.1s，无 reasoning）、**ch101 opencode-go 200**（pong/stop，1.3s）、ch88 runinfra 200 但 `finish_reason=length`（推理烧掉 token budget，不干净）、ch96 opencode-zen-free **401 CreditsError**（余额不足）排除。选 **seeseed（ch89 key donor）**：输出干净无推理、故障域全新；ch101 的 key 已承载 ch117（qwen3.8-max-free 第二源），不再进一步集中。新建 ch118 `seeseed-deepseek-v4-flash`（base_url `https://api-yi-hydrogel.seeseed1ck.icu` **不带 /v1**，key 复制自 ch89，`models=deepseek-v4-flash`，`model_mapping={}`（identity，上游裸名同名），p30/w5/auto_ban=1/group=default，脚本 `add_deepseek_v4_flash_pool.py`，备份 `new-api-before-deepseek-v4-flash-pool-20260829-000815.db`）。ch111 零改动，与 ch118 同 p30/w5 → **双 co-primary 1:1 轮询**，ch110（p6）保持降级兜底，ch15 仍双保险禁用。**验证**：12 发网关功能测试 12/12 200，归属 {ch111:8, ch118:4}；6 小时真实流量窗口 ch111:76 / ch110:15 / ch118:8 全部 type=2 零失败。**脚本两处 bug（同类新脚本须避）**：① functional test 误用 admin token 打 `/v1` → 必须用 relay key（`~/.omp/agent/models.yml` 的 `zg-newapi.apiKey`，见 `read_gateway_key()`）；② logs 表列名是 **`model_name`** 而非 `model`，误用致归属统计恒空。另加 `create_channel` DB 回退（POST 响应缺 `body["data"]` 时按名回查 id）与重复渠道名守卫。

## 6. 路由与亲和策略（2026-08-01 生效）

- **claude 亲和性已移除**：`channel_affinity_setting.rules` 删除 `claude cli trace`，Claude 模型纯按权重轮询（agentrouter ch45 主源 + gorouter ch57 备源）。
- **亲和系统全局关闭**：`channel_affinity_setting.enabled=false`、`switch_on_success=false`（保留 codex/glm/grok/deepseek/longcat/qwen 六条规则定义但未启用）。
- **auto_ban 策略**：本机源（ch44/45）+ 免费紧 RPM 源（ch46/47 bazaarlink 10 RPM）auto_ban=0（桌面断连/突发 429 是常态，误杀得不偿失）；VPS/付费源全部 auto_ban=1（稳定源真挂该禁）。
- **gorouter 三源合并 + claude 池重组**：ch26/27/28（gorouter 同上游三 key）合并为 ch57，weight=4；ch45 权重 5→15 成主源；ch3/9/18（baibei/linxi-k40）直测 503 `All available accounts exhausted` 禁用，与旧 ch26/27/28 一并 status=2 + abilities enabled=0。
- **k40/baibei 已禁用**：ch3/9/18 上游账户全耗尽（503→自动摘除），不再需要保守后备守护。
- **402 加入重试状态码**（2026-08-01）：`AutomaticRetryStatusCodes` 由 `100-199,300-399,409-499,500-504,505-599` 改为 `100-199,300-399,402,409-499,500-504,505-599`。根因：bazaarlink（ch46/47）免费额度打满时上游返回 402 `Insufficient credits`，但 402 不在重试范围 → NewAPI 不 failover 直接返给客户端 → OMP 报 `402 Insufficient credits`。修复后额度打满的源 402 触发重试/切其他源，不再报错。402 是网关层快速失败（额度检查毫秒级），重试代价可忽略；ch46/47 auto_ban=0 保持（免费源突发 429 常态，见上）。
- **auto_ban 阈值 50→3**（2026-08-01）：`ChannelDisableThreshold` 由 50 降 3——免费/月度配额打满源（sensenova、bazaarlink 等）返 429/402 时 3 次即自动禁用，`AutomaticEnableChannelEnabled=true` 额度回血自动拉回。代价：任何渠道连续 3 次失败会被临时禁（网络闪断可能导致），靠自动恢复机制拉回。
- **ch43 atomcode 根因纠正 + 本机 bridge 替代（ch53）**（2026-08-01）：旧 Python 代理（`atomcode-open-api`）签名算法不对——`atomcode-signing-v1` 实现错误，被上游 `llm-api.atomgit.com` 拒（`SIG_MISSING`）；旧网关 `api-ai.gitcode.com` 则对 deepseek 返业务 403 `not enabled for codingplan 'Lite'`（伪装"每月token额度已不足"）。**根因非额度**（status-v2 显示 calls_used:0）。改用 GitHub 开源项目 `Small-tailqwq/atomgit-opencode-bridge`：正确 `atomcode-signing-v1` HMAC 签名（HKDF-SHA256 + master key）+ 真实 UA + 自动 token 续命，本机（Windows）跑 `node proxy.js` 监听 0.0.0.0:9457，读本机 `~/.atomcode/auth.toml`。ch53 经 Tailscale 100.83.32.95:9457 接 NewAPI，验证 `finish:stop content:BRIDGE-NEWAPI-OK`。旧 ch43 已删除。Qwen3-VL 纯文本可用，图片仍被"独享"校验拦截（未进池）。

## 7. 验证记录

- **deepseek 权重重排后路由验证**（2026-08-02）：重启 new-api 后连打 20 发实测分布 ch37:5 ch38:4 ch42:3 ch46:3 ch47:3 ch48:3 ch53:3 ch56:2 ch15:1——**快源主导（ch37/38 权重 20 命中 9/27≈33%）**，整体 avg ~2.5s（修复前 ~27s），**慢源 ch50/ch55 零命中**（已禁用）。此前观测到 ch35 偶发 502（上游 `10.88.0.1:3457` 间歇性故障），NewAPI 自动重试到其他源，不影响整体。
- **deepseek 十二源均衡命中验证**（2026-08-01）：ch15:8 ch42:6 ch37/38:5 ch44:5 ch47:5 ch46:3 ch48:2 ch53:2 ch55:2 ch50:1（权重 10/1/10/10/5/3/3/22/5/5/5 比例吻合）。
- **ch43 退出后 deepseek 池验证**：连打 5 发全 `finish:stop`（length 为 12 token 推理被吃），近 2min ch43 命中 0 —— 退出生效，deepseek 靠其他 9 源正常。
- **ch49/50 inferx 接入验证**：glm-5.2 `finish:stop content:GLM-IX-OK`；deepseek `finish:stop content:DS-IX-OK`。
- **ch53 本机 bridge 验证**：`finish:stop content:BRIDGE-NEWAPI-OK`（本机 atomgit-opencode-bridge 经 Tailscale 接入）。
- **新渠道不参与路由的三坑修复**（2026-08-01）：新增渠道（ch49/50/53/54/55）创建时 abilities 行 `group` 为空（不在 default 组）、`priority=30`（主渠道为 50）、base_url 带 `/v1`（NewAPI type=1 自动补 `/v1` 拼成 `/v1/v1/...` 404）。修复后 deepseek 十二源全渠道均衡命中（ch15:8 ch42:6 ch37/38:5 ch44:5 ch47:5 ch46:3 ch48:2 ch53:2 ch55:2 ch50:1）。
- **qwen3-8-27b 三源池验证**（2026-08-28 晚间）：ch113 入池后 12 发网关请求 10 成功（ch88:3 / ch112:4 / ch113:3），2 发上游 500（首 20s，时间相关性指向 ch113 冷启动）；ch113 终态 status=1 未被 auto-ban。
- **k3 四源池验证**（2026-08-28 深夜）：ch115（sensenova-k3）入池后 12 发网关 10 成功（prompt 91 纯净计数 ch33:7 / ch115:3，全窗口 11:6 含并发大 prompt 流量）、2 发 `TimeoutError`（上游 kimi-k3 瞬时超时，ch115 status=1 未被 auto-ban）；单发网关 `k3→kimi-k3` **200 5.8s**（reasoning 空 content 属思考模型常态）；并发窗口内 ch115 另有 4 次大 prompt 真实命中（312k/163k/315k/316k），确认已入路由。
- **kktoken claude 四源池验证**（2026-08-28 深夜）：ch116 入池后 12 发网关（`claude-opus-5`）11 成功（ch94:3 / ch95:2 / ch116:3 / ch57:2 + 1 在途），1 发 403 `bad_response_status_code`（归属未知——logs 表不记失败行；kktoken 直连 8/8 200、ch116 status=1 未被 auto-ban，判上游瞬时抖动）。直连四模型全 200（opus-5 1.8s / opus-4-8 1.7s / thinking 变体 2.4-3.0s）。Cloudflare UA 门实测：python 默认 UA `/v1/models` 与 completion 均 1010，`curl/8.5`、`Go-http-client/1.1|2.0`、`new-api`、`OneAPI`、浏览器 UA 全 200——NewAPI Go 客户端放行，直连探测须带 UA。
- claude-opus-5 Anthropic 格式 10 发：ch26/27/28/45 全部分流。
- deepseek-v4-flash 隔离验证 ch46/47：base_url 初设 `https://bazaarlink.ai/api/v1` 致 NewAPI 拼成 `/api/v1/v1/...` 返 404，改 `https://bazaarlink.ai/api` 后 `finish:stop content:BZ-OK` 通过。九源全 enabled。
- ch18 禁用后近 2min 零错误（此前每分钟 10+ 502）。
- k40/baibei 守护脚本端到端验证：模拟 ch3 status=3 → 脚本输出 `赦免 auto_ban: ch3(baibei-100xlabs)` → 回 status=1；手动 status=2 不被赦免。

> 安全：本文档不含任何 API key。
