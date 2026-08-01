# NewAPI 聚合池总览（2026-08-01）

本文件是 NewAPI 各模型聚合池的**当前事实快照**，防止文档漂移。任何渠道增删/权重调整/状态变化都应同步更新本文件。

## 1. deepseek-v4-flash 聚合池（九源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch15 | sensenova（商汤日日新） | 付费中转 | 10 | 1 | |
| ch35 | cline-free 多账号池 | 免费 | 10 | 1 | `model_mapping: deepseek-v4-flash→deepseek/deepseek-v4-flash` |
| ch37 | tokenrhythm-1（基元） | 付费中转 | 10 | 1 | |
| ch38 | tokenrhythm-2（基元） | 付费中转 | 10 | 1 | |
| ch42 | DeepSeek 官方直连 | 官方 | 10 | 1 | models 含裸名，官方别名走 `deepseek-official-v4-flash` |
| ch44 | codebuddy（WorkBuddy 本机） | 桌面依赖 | 5 | 0 | Tailscale 100.83.32.95:8787 |
| ch46 | bazaarlink-flash-1 | 免费 | 3 | 0 | 10 RPM/150 每日加权扣量；base_url `https://bazaarlink.ai/api`（NewAPI 自动补 /v1） |
| ch47 | bazaarlink-flash-2 | 免费 | 3 | 0 | 同上，第二 key 单渠道（避开多 key 换行 header 坑） |
| ch48 | opencode-go-flash | 订阅 | 5 | 0 | `https://opencode.ai/zen/go`（NewAPI 补 /v1/chat/completions，带 /v1 会 404）；OpenCode Go $10/月订阅 |

> **ch43（atomcode CodingPlan Lite）已于 2026-08-01 退出 deepseek 池**（abilities enabled=0 + models 清空）。根因（详见 §6「ch43 atomcode 根因纠正」）：`status-v2` 显示 `calls_used:0/usage_percent:0`（额度**未消耗**），不是"额度打满"；实为上游双网关策略——旧网关 `api-ai.gitcode.com` 对 deepseek 返业务 403 `model is not enabled for codingplan 'Lite'`（Lite 档不启用），新网关 `llm-api.atomgit.com` 要求真客户端签名（代理被拒 `SIG_MISSING`）。ch43 对 deepseek 不可用，退出池避免污染。

## 2. glm-5.2 聚合池（五源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch14 | wintoken | 付费中转 | 20 | 1 | 主源权重翻倍 |
| ch35 | cline-free 多账号池 | 免费 | 10 | 1 | `model_mapping: glm-5.2→cline-free/glm-5.2` |
| ch37 | tokenrhythm-1 | 付费中转 | 10 | 1 | |
| ch38 | tokenrhythm-2 | 付费中转 | 10 | 1 | |
| ch44 | codebuddy（本机） | 桌面依赖 | 5 | 0 | |

## 3. gpt 聚合池（centos 摘除后）

| 模型 | 渠道 | 权重 | auto_ban | 备注 |
|------|------|------|----------|------|
| gpt-5.6-sol | ch30 fastaitoken | 5 | 1 | |
| gpt-5.6-sol | ch44 codebuddy（本机） | 5 | 0 | |
| gpt-5.6-sol | ch45 agentrouter（本机） | 5 | 0 | |
| gpt-5.5 | ch30 fastaitoken | 20 | 1 | 单源 |
| gpt-image-2 | ch30 fastaitoken | 20 | 1 | 单源 |

> ch16/ch25（centos.hk 同上游两 key）已于 2026-08-01 禁用：centos 上游账户欠费返 403「预扣费额度失败 用户剩余额度 ¥0.09」（该"用户"指 centos 账户，**非**本地无限钱包），且 NewAPI 对上游 403 默认不 failover，故双保险摘除（status=0 + abilities enabled=0）。
> **副作用**：gpt-5.6-luna / gpt-5.6-terra 仅挂 centos，摘除后零源，已从 OMP/Kimi 配置移除（避免选到报"无可用渠道"）；centos 充值/换 key 后可恢复 ch16/25。

## 4. claude-opus-5 / claude-opus-4-8 / claude-opus-4-7 聚合池（七源）

| 渠道 | 来源 | 类型 | 权重 | priority | auto_ban | 备注 |
|------|------|------|------|----------|----------|------|
| ch3 | baibei-100xlabs | 付费中转 | 40 | 30 | 1 | type=14；多 key 池额度大账号多，不稳定 |
| ch9 | linxi-k40 | 付费中转 | 20 | 30 | 1 | type=14；不稳定但额度大 |
| ch18 | linxi-k40-opus5-backup | 付费中转 | 10 | 30 | 1 | type=14；502 频发但额度大 |
| ch26 | gorouter-claude | 付费中转 | 5 | 40 | 0 | type=1（原 14 已改） |
| ch27 | gorouter-claude-2 | 付费中转 | 3 | 40 | 0 | type=1 |
| ch28 | gorouter-claude-opus-3 | 付费中转 | 4 | 40 | 0 | type=1 |
| ch45 | agentrouter（本机） | 代理池 | 5 | 40 | 0 | Tailscale |

> ch3/9/18 为 **type=14**：OpenAI 格式 `/v1/chat/completions` 测试路由不到它们（与 gorouter 旧坑同源），真实 OMP 走 `zg-newapi-anthropic` 端点才命中。priority 由 57/54 降至 30 作**保守后备**——稳定时靠高 weight 吃流量，挂时不优先吸流量拖累体验。
> **保守后备守护**：`/opt/new-api/k40-baibei-revive.py` + systemd timer `k40-baibei-revive.timer`（每分钟）赦免被 auto_ban 的 ch3/9/18（status=3→1），SQL 带 `AND status=3` 故手动禁用（status=2）不被误赦免；判活权交 NewAPI 下轮定时测试，零误判。改 DB 后依赖 NewAPI channels sync goroutine（~1-2min）拉入内存。

## 5. 其他单源模型（非聚合）

| 模型 | 渠道 |
|------|------|
| sensenova-6.7-flash-lite | ch15 |
| grok-4.5 | ch17 (w10) / ch29 (w20) / ch39 (w10) 三源 |
| gpt-5.5 | ch16/25/30 三源 |
| gpt-5.6-luna | ch16/25 二源 |
| gpt-5.6-terra | ch25 单源 |
| claude-sonnet-5 | ch26/27 二源 |
| qwen3.8-max-preview | ch31 |
| k3/kimi-for-coding | ch33 |
| step-router-v1 | ch36 |

## 6. 路由与亲和策略（2026-08-01 生效）

- **claude 亲和性已移除**：`channel_affinity_setting.rules` 删除 `claude cli trace`，Claude 模型纯按权重轮询（gorouter 三渠道 + agentrouter 均匀分流）。
- **亲和系统全局关闭**：`channel_affinity_setting.enabled=false`、`switch_on_success=false`（排查 gorouter 不分流时关闭，保留 codex/glm/grok/deepseek/longcat/qwen 六条规则定义但未启用）。
- **auto_ban 策略**：本机源（ch44/45）+ 免费紧 RPM 源（ch46/47 bazaarlink 10 RPM）auto_ban=0（桌面断连/突发 429 是常态，误杀得不偿失）；VPS/付费源全部 auto_ban=1（稳定源真挂该禁）。
- **gorouter type 修复**：ch26/27/28 由 type=14（Anthropic）改 type=1（OpenAI）——type=14 时 NewAPI 定时测试用 OpenAI 格式返回空 → 内存标记降级 → 路由跳过全走 ch45。改 type=1 + 重建容器清缓存后恢复正常分流。
- **k40/baibei 保守后备**：ch3/9/18 priority 降 30 + 每分钟赦免守护（见 §4 注）。auto_ban 负责 ban，守护负责及时恢复，priority 降后备防抖动。
- **402 加入重试状态码**（2026-08-01）：`AutomaticRetryStatusCodes` 由 `100-199,300-399,409-499,500-504,505-599` 改为 `100-199,300-399,402,409-499,500-504,505-599`。根因：bazaarlink（ch46/47）免费额度打满时上游返回 402 `Insufficient credits`，但 402 不在重试范围 → NewAPI 不 failover 直接返给客户端 → OMP 报 `402 Insufficient credits`。修复后额度打满的源 402 触发重试/切其他源，不再报错。402 是网关层快速失败（额度检查毫秒级），重试代价可忽略；ch46/47 auto_ban=0 保持（免费源突发 429 常态，见上）。
- **auto_ban 阈值 50→3**（2026-08-01）：`ChannelDisableThreshold` 由 50 降 3——免费/月度配额打满源（sensenova、bazaarlink 等）返 429/402 时 3 次即自动禁用，`AutomaticEnableChannelEnabled=true` 额度回血自动拉回。代价：任何渠道连续 3 次失败会被临时禁（网络闪断可能导致），靠自动恢复机制拉回。
- **ch43 atomcode 根因纠正 + 退出 deepseek 池**（2026-08-01）：之前判定"每月token额度打满"是**错误的**——`status-v2` 显示 `calls_used:0 / usage_percent:0`（额度未消耗）。实为上游双网关策略：旧网关 `api-ai.gitcode.com` 对 deepseek 返业务 403 `not enabled for codingplan 'Lite'`（Lite 档不启用），新网关 `llm-api.atomgit.com` 要求真客户端签名（代理被拒 `SIG_MISSING`）。代理补真实签名（`sign_request`）+ `User-Agent: atomcode/5.0.3` + `x-atomcode-session-id` 后，旧网关不再伪装"每月token额度已不足"（200 content 陷阱）而是暴露真 403 → ch43 对 deepseek 不可用（上游业务决策 + 签名墙，非配置可解），已从 deepseek 聚合池摘除（abilities enabled=0 + models 清空），deepseek 靠其他 9 源。代理签名/UA/429 拦截补丁直接改 `gateway.py`/`server.py`，`pip upgrade` 覆盖会丢（ch43 停用中，丢了不影响）。Qwen3-VL 纯文本在旧网关可用（签名修复后），图片输入仍被上游"独享"校验拦截，未进任何池。

## 7. 验证记录

- deepseek-v4-flash 近 1h 命中：ch42:44, ch43:32, ch15:28, ch35:27, ch38:30, ch37:21, ch44:9 —— 全源命中，权重均衡。
- **ch43 退出后 deepseek 池验证**：连打 5 发全 `finish:stop`（length 为 12 token 推理被吃），近 2min ch43 命中 0 —— 退出生效，deepseek 靠其他 9 源正常。
- claude-opus-5 Anthropic 格式 10 发：ch26/27/28/45 全部分流。
- deepseek-v4-flash 隔离验证 ch46/47：base_url 初设 `https://bazaarlink.ai/api/v1` 致 NewAPI 拼成 `/api/v1/v1/...` 返 404，改 `https://bazaarlink.ai/api` 后 `finish:stop content:BZ-OK` 通过。九源全 enabled。
- ch18 禁用后近 2min 零错误（此前每分钟 10+ 502）。
- k40/baibei 守护脚本端到端验证：模拟 ch3 status=3 → 脚本输出 `赦免 auto_ban: ch3(baibei-100xlabs)` → 回 status=1；手动 status=2 不被赦免。

> 安全：本文档不含任何 API key。
