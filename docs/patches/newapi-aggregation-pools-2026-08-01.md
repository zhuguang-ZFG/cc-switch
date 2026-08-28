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
| ch15 | sensenova（商汤日日新） | 付费中转 | 10 | 1 | 中速（avg 19s） |
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

## 4. claude-opus-5 / claude-opus-4-8 / claude-opus-4-7 聚合池（二源）

| 渠道 | 来源 | 类型 | 权重 | priority | auto_ban | 备注 |
|------|------|------|------|----------|----------|------|
| ch45 | agentrouter（本机） | 代理池 | 15 | 50 | 0 | Tailscale 100.83.32.95:8788；主源（权重 15/19 ≈ 79%） |
| ch57 | gorouter 合并（三 key） | 付费中转 | 4 | 40 | 0 | `https://gorouter.app`；ch26/27/28 三把 key 合并换行分隔；备源（权重 4/19 ≈ 21%）。**多 key 正确姿势**：key 真实换行（0x0A）分隔 + `channel_info` 以 **bytes** 写入（`is_multi_key:true, multi_key_size:3, multi_key_status_list:{"0":1,"1":1,"2":1}, multi_key_mode:"polling"`）——str 写入致 GORM 二次编码、`multi_key_mode` 写数字、key 写 `\n` 字面量，三种坑都导致渠道不可路由 |

> **2026-08-01 整合**：原七源 → 二源。ch26/27/28（gorouter 三把 key 同上游）合并为 ch57；ch45 权重 5→15 成主源；ch3/9/18（baibei/linxi-k40）直测全部 503 `All available accounts exhausted`（上游账户耗尽），ch3 已禁用（status=2 + abilities enabled=0）。
> **2026-08-02 终态**：ch9/ch18 曾被 auto-ban 赦免回 1 且上游恢复，测得 avg 51-67s 龟速拖慢 claude 池 → **重新禁用**（status=2 + abilities.enabled=0 双保险，防 AutomaticEnableChannelEnabled 再拉回）。claude 池最终二源：ch45 agentrouter（weight=15, priority=50 主源）+ ch57 gorouter（weight=4 备源）。
> **重要教训**：NewAPI 渠道禁用**必须双保险**——`status=2`（ManuallyDisabled）+ `abilities.enabled=0`。只改 status 不生效（路由过滤看 abilities 表；status=0 是 Unknown 非 Disabled，更无效）。之前 ch16/25/43 的「status=0 禁用」实际从未生效，靠 abilities 兜底。
> **保守后备守护**：`/opt/new-api/auto-ban-revive.py` + systemd timer `k40-baibei-revive.timer`（每分钟）赦免**所有**被 auto_ban 的渠道（`status=3 AND auto_ban=1`→1；替代原仅覆盖 ch3/9/18 的 k40-baibei-revive.py）。SQL 带 `AND status=3` 故手动禁用（status=2）不被误赦免；`auto_ban=0` 渠道（免费源）不赦免（有意不禁用）。判活权交 NewAPI 下轮定时测试，零误判。改 DB 后依赖 NewAPI channels sync goroutine（~1-2min）拉入内存。

## 5. 其他单源模型（非聚合）

| 模型 | 渠道 |
|------|------|
| sensenova-6.7-flash-lite | ch15 |
| grok-4.5 | ch17 (w10) / ch29 (w20) / ch39 (w10) 三源 |
| gpt-5.5 | ch30 单源（ch2/16/25 已禁、ch41 已删） |
| gpt-5.6-luna | **零源**（仅 centos ch16/25 曾挂，已禁；ch41 已删） |
| gpt-5.6-terra | **零源**（仅 centos ch25 曾挂，已禁） |
| claude-sonnet-5 | ch57（ch26/27 已合并禁用） |
| qwen3.8-max-preview | ch31 |
| qwen3-8-27b | ch88 (w1, prio 0) / ch112 (w1, prio 0) / ch113 (w1, prio 0) 三源 1:1:1（2026-08-28 聚合，晚间 B.AI 第三源） |
| qwen3.8-max-free | ch114（tokenrouter 免费档，2026-08-28 深夜） |
| k3/kimi-for-coding | ch33 + ch115 1:1（p50/w10）/ ch108（p49/w5）/ ch110（p6/w5） |
| step-router-v1 | ch36 |

> **2026-08-02 实测更正**：本节原写 gpt-5.5 三源 ch16/25/30、luna 二源 ch16/25、terra 单源 ch25、sonnet-5 二源 ch26/27——均过时（ch16/25 已禁、ch41 已删、ch26/27 已并入 ch57）。以上为实测后版本。

> **2026-08-28 补记**：ch88 `runinfra-qwen3-8-27b`（`https://api.runinfra.ai`，单 key，weight=1，priority=0，auto_ban=1，group=default）后补接入，为 `qwen3-8-27b` **唯一源、未聚合**；该模型现是 OMP default 模型（兼 commit/smol/translator 角色与 compaction 第 4 候选），单源风险点。后续聚合须按三坑教训配置新渠道（group=default、priority=50、正确 weight）。
> **2026-08-28 聚合落地**：`qwen3-8-27b` 二源池 —— ch88 `runinfra-qwen3-8-27b`（weight=1, priority=0, auto_ban=1，零改动）+ 新增 ch112 `yjs-qwen3-8-27b`（`https://api.yjs.im`，复用 ch110 yjs-free 的 key，上游 id `qwen-3.8-27b` 经 model_mapping 暴露为 `qwen3-8-27b`，weight=1, priority=0, auto_ban=1，group=default）。同优先级按权重 1:1 轮询，auto_ban 容灾。**机制实测（重要教训）**：abilities 表是 channel 行的派生物——行存在性镜像 `channels.models`，(priority,weight) 镜像渠道级字段，sync goroutine 在渠道 API 事件后重新派生（当日对 ch88 行直接 SQL 改 50/50，数秒内被还原为渠道字段 0/1；回滚 ch110 增模后孤儿行亦被 goroutine 清除）——**per-model 权重只能靠渠道级字段控制**；多模型渠道（ch110 挂 19 模型）不能为单个模型调权，故第二源建**专用单模型渠道**（与 ch88 同模式，ch88 单模型、字段无纠缠，零改动即成 1:1）。另坑：Python sqlite3 legacy 事务不显式 commit，DML 在 close 时静默回滚（当日 v1 脚本 verify 抓出，v2 已修）。供应商扫描（当日）：api.yjs.im（既有 key）与 OpenRouter `qwen/qwen3.8-27b`、HF `Qwen/Qwen3.8-27B` 均提供同一开源模型，取既有 key 零新账户。快照：`backups/new-api-before-qwen38-27b-pool-20260828-011003.db`；脚本 `scripts/ops/add_qwen38_27b_pool.py`（dry-run 默认）。OMP 侧零改动。
> **2026-08-28 param_override 核查**：ch88 带 override 剥 `prompt_cache_key`（runinfra 拒未知参数）；OMP 真实请求体另含 `stream_options`/`enable_thinking`。直连 api.yjs.im 逐参数矩阵实测四变体全 HTTP 200——带 `prompt_cache_key` 时 yjs 切缓存感知后端（响应 `model:"Qwen/Qwen3.8-27B"`+`chatcmpl-` id），剥掉反损失缓存局部性，故 **ch112 不加 override**。经网关完整 OMP 形请求 12/12 成功（同 cache key 被 "qwen trace" 亲和规则粘钉首命中渠道；本会话 key 钉 ch88，不受池变更影响）。
> **2026-08-28 晚间 B.AI 第三源（ch113）**：B.AI 免费档目录在 ch111 白名单划界后扩张（`/v1/models` 44 项 vs 白名单 5 项）。实测：ch111 key 直连 `qwen3.8-27b` **HTTP 200**（上游模型 `Qwen/Qwen3.8-27B-FP8`，pong/stop，3.94s），premium ID（claude-opus-5、kimi-k3）仍 403 deposit-required（ch111 remark 边界复核成立）。新增 ch113 `bai-qwen3-27b`（`https://api.b.ai`，复用 ch111 key，上游 id `qwen3.8-27b` 点号形经 mapping 暴露为 `qwen3-8-27b`，weight=1, priority=0, auto_ban=1, group=default，脚本 `add_qwen38_27b_bai_pool.py`，备份 `new-api-before-qwen38-27b-pool-20260828-160158.db`）。三渠道同 p0/w1 → 1:1:1。**已知项**：应用后 12 发功能测试首 20s 内 2 发上游 500 `do request failed`（logs 表不记失败行，渠道归属靠时间相关性：两发均贴首波 ch113 流量，其后 ch113 3/3 全成功，ch88/ch112 窗口内零失败）——B.AI 免费档冷启动/限流行为未压测，auto_ban=1 兜底；若 429/500 持续，处理路径=调 ch113 渠道级 priority 降级为应急档（等价于方案 a），勿直接 SQL 改 abilities。
> **2026-08-28 深夜 tokenrouter 免费模型（ch114）**：用户提供的 `api.tokenrouter.com` key 为 **token 级目录**（两个 key 各见一个模型）。key2 目录 `qwen/qwen3.8-max-free` 实测 200（上游 `qwen3.8-max-pd`，reasoning，pong/stop 4.3s）→ 新建 ch114 `tokenrouter-qwen3.8-max-free`（**建渠时 base_url 用 `https://api.tokenrouter.com` 去掉用户给的 `/v1`**——NewAPI type=1 自动补 `/v1`，带 `/v1` 会拼成 `/v1/v1/...` 404，ch46/47 bazaarlink 先例见三坑记录；探测直连用完整 `/v1` 无妨；mapping `qwen3.8-max-free -> qwen/qwen3.8-max-free`，p0/w1/auto_ban=1，脚本 `add_tokenrouter_free_model.py`，key 经 TR_KEY 环境变量入渠道行、不落仓库；备份 `new-api-before-tokenrouter-20260828-170559.db`）。新 OMP 模型 `qwen3.8-max-free`（1M/131K，text-only，不入任何角色链/压缩梯——用户仅要求可用）。key1 目录 `moonshotai/kimi-k3-free` 三次 503 "no available channel under group default (distributor)"——死目标不注册（zai 先例）。
> **2026-08-28 深夜 sensenova-k3 第四源（ch115）**：商汤日日新 token plan（`https://token.sensenova.cn`，**建渠时同样去掉 `/v1`**——同 tokenrouter，type 1 自动补 `/v1`，带 `/v1` 则 404；探测直连用完整 `/v1/chat/completions`）实测目录仅 6 模型（无 K3，需手工 mapping），候选 `kimi-k3` 直连 `POST /v1/chat/completions` **200**（`model:kimi-k3，pong/stop，7.0s`，`k3` 裸名 404）。新建专用单模型渠道 ch115 `sensenova-k3`（复用 ch15 的 key，`models=k3`，`model_mapping k3→kimi-k3`，p50/w10/auto_ban=1，group=default，脚本 `add_sensenova_k3_pool.py`，备份 `new-api-before-sensenova-k3-20260828-172247.db`）。与 ch33 同 p50/w10 → 头部 1:1 分担（ch108 p49/w5、ch110 p6/w5 为降级档）。**验证**：12 发网关功能测试 10 成功（纯净 prompt 91 计数 ch33:7 / ch115:3；全窗口 11:6 含并发用户大 prompt 流量），2 发 `TimeoutError`（上游 kimi-k3 瞬时超时，ch115 status=1 未被 auto-ban，属瞬时抖动；并发窗口内 ch115 另有 4 次大 prompt 真实命中 312k/163k/315k/316k，确认已入路由）。单发网关 `k3→kimi-k3` **200 5.8s**（reasoning 空 content，属 k3 思考模型常态）。**注意**：token plan 工作区配额跨模型共享（此前 ch15 上 glm-5.2 429 "workspace quota exceeded"），k3 流量激增可能波及同计划上 deepseek-v4-flash 压缩头部 Nam-cKdw 的可用性，必要时降 ch115 权重。

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
- claude-opus-5 Anthropic 格式 10 发：ch26/27/28/45 全部分流。
- deepseek-v4-flash 隔离验证 ch46/47：base_url 初设 `https://bazaarlink.ai/api/v1` 致 NewAPI 拼成 `/api/v1/v1/...` 返 404，改 `https://bazaarlink.ai/api` 后 `finish:stop content:BZ-OK` 通过。九源全 enabled。
- ch18 禁用后近 2min 零错误（此前每分钟 10+ 502）。
- k40/baibei 守护脚本端到端验证：模拟 ch3 status=3 → 脚本输出 `赦免 auto_ban: ch3(baibei-100xlabs)` → 回 status=1；手动 status=2 不被赦免。

> 安全：本文档不含任何 API key。
