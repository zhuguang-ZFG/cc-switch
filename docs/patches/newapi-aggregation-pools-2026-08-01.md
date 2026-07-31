# NewAPI 聚合池总览（2026-08-01）

本文件是 NewAPI 各模型聚合池的**当前事实快照**，防止文档漂移。任何渠道增删/权重调整/状态变化都应同步更新本文件。

## 1. deepseek-v4-flash 聚合池（十源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch15 | sensenova（商汤日日新） | 付费中转 | 10 | 1 | |
| ch35 | cline-free 多账号池 | 免费 | 10 | 1 | `model_mapping: deepseek-v4-flash→deepseek/deepseek-v4-flash` |
| ch37 | tokenrhythm-1（基元） | 付费中转 | 10 | 1 | |
| ch38 | tokenrhythm-2（基元） | 付费中转 | 10 | 1 | |
| ch42 | DeepSeek 官方直连 | 官方 | 10 | 1 | models 含裸名，官方别名走 `deepseek-official-v4-flash` |
| ch43 | atomcode CodingPlan Lite | 免费 | 10 | 1 | 800 次/5h 滚动窗口 |
| ch44 | codebuddy（WorkBuddy 本机） | 桌面依赖 | 5 | 0 | Tailscale 100.83.32.95:8787 |
| ch46 | bazaarlink-flash-1 | 免费 | 3 | 0 | 10 RPM/150 每日加权扣量；base_url `https://bazaarlink.ai/api`（NewAPI 自动补 /v1） |
| ch47 | bazaarlink-flash-2 | 免费 | 3 | 0 | 同上，第二 key 单渠道（避开多 key 换行 header 坑） |
| ch48 | opencode-go-flash | 订阅 | 5 | 0 | `https://opencode.ai/zen/go`（NewAPI 补 /v1/chat/completions，带 /v1 会 404）；OpenCode Go $10/月订阅 |

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

## 7. 验证记录

- deepseek-v4-flash 近 1h 命中：ch42:44, ch43:32, ch15:28, ch35:27, ch38:30, ch37:21, ch44:9 —— 全源命中，权重均衡。
- claude-opus-5 Anthropic 格式 10 发：ch26/27/28/45 全部分流。
- deepseek-v4-flash 隔离验证 ch46/47：base_url 初设 `https://bazaarlink.ai/api/v1` 致 NewAPI 拼成 `/api/v1/v1/...` 返 404，改 `https://bazaarlink.ai/api` 后 `finish:stop content:BZ-OK` 通过。九源全 enabled。
- ch18 禁用后近 2min 零错误（此前每分钟 10+ 502）。
- k40/baibei 守护脚本端到端验证：模拟 ch3 status=3 → 脚本输出 `赦免 auto_ban: ch3(baibei-100xlabs)` → 回 status=1；手动 status=2 不被赦免。

> 安全：本文档不含任何 API key。
