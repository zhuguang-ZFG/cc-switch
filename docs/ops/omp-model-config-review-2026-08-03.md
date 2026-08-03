# OMP 模型配置审查与聚合渠道修复记录 (2026-08-03)

**Status:** Active
**Scope:** OMP modelRoles/fallbackChains 配置、NewAPI 聚合渠道健康、Inception Labs mercury-2 接入

## 背景

用户反馈 subagent 模型设置不合理 + 聚合渠道（GLM/deepseek-v4-flash/Claude/GPT）存在健康问题。经多轮 subagent 审查（ModelConfReviewer / PostRestartReviewer / ConfigReviewer / PostRestart2Reviewer）发现并修复。

## OMP 模型配置最终态（modelRoles）

| 角色 | 模型 | 说明 |
|---|---|---|
| slow / plan / vision / task | `agentrouter/claude-opus-5:xhigh/high` | 强推理，走本地 agentrouter |
| commit / tiny / smol | `zg-newapi/deepseek-v4-flash` | 快模型，走 NewAPI 聚合池 |
| designer | `zg-newapi/gpt-5.6-sol:high` | 设计任务（403 已修） |
| default | `atomcode/deepseek-v4-flash:max` | OMP 启动自动选择（本地快） |

**修复链**：
1. **smol/tiny/commit 原指向 gpt-5.6-sol**（历史卡死模型）→ 改 omp-free → 因 omp-free 429 限流 → 最终改 zg-newapi/deepseek-v4-flash
2. **plan/commit 消除嵌套别名**（resolver 只展开一跳，@smol/@slow 链会解析失败）→ 直接写具体值
3. **删除 agentrouter/* 与 anyrouter/* 通配键**（曾吞掉 slow/plan/vision/task 四条角色链 24 条目）
4. **opencode-go 补进 default/tiny/smol/commit 链首**（V4FLASH 用 opencode-go）
5. **清理 6 类失效模型**（qwen3.8-max-preview、cline-free/glm-5.2、stepfun/step-3.7-flash、deepseek/deepseek-v4-flash、deepseek-v4-flash-0731、poolside/laguna-s-2.1:free）
6. **移除 codebuddy/gpt-5.6-sol**（WorkBuddy 客户端专属硬 403，5 条链死项 + models.yml 条目）
7. **删除 nihaox-k3 provider**（3 模型全死：glm-4.7-flash 403/503、deepseek-v4-flash-0731 503、mimo-v2.5 400 未定价）
8. **gpt-5.6-sol 补 contextWindow 1048576**（gpt-5.5→gpt-5.6-sol promotion 触发条件）

## Inception Labs mercury-2 接入

- **NewAPI**：改造 ch61（原 p0-systems-deepseek 禁用渠道）→ `inceptionlabs-mercury2`，base `api.inceptionlabs.ai`，models `mercury-2`，weight 5
- **坑**：POST /api/channel/ 后端 panic（NewAPI bug）→ 用 PUT 复用现有渠道绕过
- **坑**：SelfUseModeEnabled 非免费，未配价模型按 37.5 兜底倍率计费（$75/1M，厂商价 300 倍）→ 配 ModelRatio `mercury-2: 2.0`
- **OMP**：zg-newapi 加 mercury-2 模型（contextWindow 128000 / maxTokens 50000，实测 max_tokens=50000 恰为上限）
- 官方模型限制：仅 `mercury-2` 可用（mercury/mercury-coder 需 2026-02-24 前账户）

## 聚合渠道健康修复

| 渠道 | 问题 | 修复 |
|---|---|---|
| ch35 cline-free-proxy | 502（4 账户 empty response） | 禁用 status=2 |
| ch50 inferx-deepseek | 持续超时 70s | 降权 w 2→1 |
| ch53 atomcode-bridge | weight=0 | 确认软下线（本地 9457 健康） |
| ch55 inferx-deepseek-b | 极慢 62s（瞬时） | 观察（复测 0.9s 恢复） |
| ch44 codebuddy | gpt-5.6-sol WorkBuddy 专属 403 | 移除 gpt-5.6-sol，保留 glm-5.2 |

## 关键踩坑记录

1. **OMP fallbackChains 优先级**：含 `/` 的键（provider/model、provider/*）优先级 > 角色链。角色主模型命中 provider/* 通配键时，角色链整条失效
2. **OMP 启动会改写 config.yml**（default 自动重选）——手动配置可能被覆盖，重启后需核对
3. **OMP ConfigFile 进程内缓存**：models.yml/config.yml 改动需重启 OMP 生效（tryLoad 不重读）
4. **equivalence 块已 inert**（16.2.12 起），补映射无意义
5. **subagent 模型配置**：scout/librarian/sonic 走 @smol，reviewer 走 @slow；派发时指定不存在的 agent 名会回退通用 task

## 当前状态

- OMP 配置全有效（9 角色 + 12 链引用 100% 可解析）
- gpt-5.6-sol 池 6 渠道健康（403 消除，8/8 测试通过）
- mercury-2 计费已配价（2.0 倍率）
- Guardian（自愈）+ watchdog 运行中

## 相关文件

- OMP 配置: `~/.omp/agent/config.yml`、`~/.omp/agent/models.yml`
- Guardian: `scripts/ops/guardian.py` + `~/.omp/guardian/`
- NewAPI: `https://aliyun.donglicao.com`
