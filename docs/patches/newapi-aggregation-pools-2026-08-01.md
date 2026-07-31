# NewAPI 聚合池总览（2026-08-01）

本文件是 NewAPI 各模型聚合池的**当前事实快照**，防止文档漂移。任何渠道增删/权重调整/状态变化都应同步更新本文件。

## 1. deepseek-v4-flash 聚合池（七源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch15 | sensenova（商汤日日新） | 付费中转 | 10 | 1 | |
| ch35 | cline-free 多账号池 | 免费 | 10 | 1 | `model_mapping: deepseek-v4-flash→deepseek/deepseek-v4-flash` |
| ch37 | tokenrhythm-1（基元） | 付费中转 | 10 | 1 | |
| ch38 | tokenrhythm-2（基元） | 付费中转 | 10 | 1 | |
| ch42 | DeepSeek 官方直连 | 官方 | 10 | 1 | models 含裸名，官方别名走 `deepseek-official-v4-flash` |
| ch43 | atomcode CodingPlan Lite | 免费 | 10 | 1 | 800 次/5h 滚动窗口 |
| ch44 | codebuddy（WorkBuddy 本机） | 桌面依赖 | 5 | 0 | Tailscale 100.83.32.95:8787 |

## 2. glm-5.2 聚合池（五源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch14 | wintoken | 付费中转 | 20 | 1 | 主源权重翻倍 |
| ch35 | cline-free 多账号池 | 免费 | 10 | 1 | `model_mapping: glm-5.2→cline-free/glm-5.2` |
| ch37 | tokenrhythm-1 | 付费中转 | 10 | 1 | |
| ch38 | tokenrhythm-2 | 付费中转 | 10 | 1 | |
| ch44 | codebuddy（本机） | 桌面依赖 | 5 | 0 | |

## 3. gpt-5.6-sol 聚合池（五源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch16 | centos-api-backup | 付费中转 | 5 | 1 | |
| ch25 | centos-api-newkey | 付费中转 | 5 | 1 | |
| ch30 | fastaitoken | 付费中转 | 5 | 1 | |
| ch44 | codebuddy（本机） | 桌面依赖 | 5 | 0 | |
| ch45 | agentrouter（本机） | 代理池 | 5 | 0 | Tailscale 100.83.32.95:8788 |

> ch34（4router-gpt）已于 2026-08-01 删除（用户账户余额不足触发 403 排查时移除）。

## 4. claude-opus-5 / claude-opus-4-8 聚合池（四源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch26 | gorouter-claude | 付费中转 | 5 | 0 | type=1（原 14 已改） |
| ch27 | gorouter-claude-2 | 付费中转 | 3 | 0 | type=1 |
| ch28 | gorouter-claude-opus-3 | 付费中转 | 4 | 0 | type=1 |
| ch45 | agentrouter（本机） | 代理池 | 5 | 0 | |

> ch18（linxi-k40-opus5-backup）已于 2026-07-31 禁用（502 频发）+ abilities 同步禁用；claude-opus-4-7 无替代源，已从 OMP/Kimi 移除。

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
- **auto_ban 策略**：本机源（ch44/45）auto_ban=0（桌面断连是常态）；VPS/付费源全部 auto_ban=1（稳定源真挂该禁）。
- **gorouter type 修复**：ch26/27/28 由 type=14（Anthropic）改 type=1（OpenAI）——type=14 时 NewAPI 定时测试用 OpenAI 格式返回空 → 内存标记降级 → 路由跳过全走 ch45。改 type=1 + 重建容器清缓存后恢复正常分流。

## 7. 验证记录

- deepseek-v4-flash 近 1h 命中：ch42:44, ch43:32, ch15:28, ch35:27, ch38:30, ch37:21, ch44:9 —— 全源命中，权重均衡。
- claude-opus-5 Anthropic 格式 10 发：ch26/27/28/45 全部分流。
- ch18 禁用后近 2min 零错误（此前每分钟 10+ 502）。

> 安全：本文档不含任何 API key。
