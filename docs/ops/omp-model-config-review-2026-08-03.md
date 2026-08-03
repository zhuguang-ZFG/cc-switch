# OMP 模型配置审查与聚合渠道修复记录 (2026-08-03)

**Status:** Active
**Scope:** OMP modelRoles/fallbackChains 配置、NewAPI 聚合渠道健康、Inception Labs mercury-2 接入

## 背景

用户反馈 subagent 模型设置不合理 + 聚合渠道（GLM/deepseek-v4-flash/Claude/GPT）存在健康问题。经多轮 subagent 审查（ModelConfReviewer / PostRestartReviewer / ConfigReviewer / PostRestart2Reviewer）发现并修复。

## OMP 模型配置观测快照（modelRoles，截至 2026-08-03）

| 角色                        | 模型                                    | 说明                                                                                                    |
| --------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| slow / plan / vision        | `agentrouter/claude-opus-5:xhigh`       | 强推理，走本地 agentrouter                                                                              |
| commit / tiny / smol / task | `zg-newapi/opencode-go:max`             | DeepSeek V4 Flash（opencode-go 独立渠道），思维强度 max（task 于 18:38 降配、18:55 改 opencode-go:max） |
| designer                    | `zg-newapi/gpt-5.6-sol:high`            | 设计任务（403 已修）                                                                                    |
| default                     | `zg-newapi-anthropic/claude-opus-5:max` | OMP 启动重选结果；Guardian 恢复路径在 ch44 重新包含 gpt-5.6-sol 时也会改写（见踩坑 6），非固定值        |

> 上表是 2026-08-03 对 `~/.omp/agent/config.yml` 的观测快照，不是永久最终态。`default` 一行尤其易变：OMP 启动自动重选会覆盖手工配置；Guardian 恢复写回是**条件性**的第二写入源（见踩坑 2/6），当前 ch44 已移除 gpt-5.6-sol，该路径当前不写 `default`，仅在模型重新加入 ch44 后恢复竞争。`zg-newapi/deepseek-v4-flash` 聚合池渠道不支持 `reasoning_effort=max`（商汤渠道仅到 high，见踩坑 7），故 flash 角色统一走支持 max 的 `opencode-go` 独立渠道。

**修复链**：

1. **smol/tiny/commit 原指向 gpt-5.6-sol**（历史卡死模型）→ 改 omp-free → 因 omp-free 429 限流 → 最终改 zg-newapi/deepseek-v4-flash
2. **plan/commit 消除嵌套别名**（resolver 只展开一跳，@smol/@slow 链会解析失败）→ 直接写具体值
3. **删除 `agentrouter/*` 与 `anyrouter/*` 通配键**（曾吞掉 slow/plan/vision/task 四条角色链 24 条目）
4. **opencode-go 补进 default/tiny/smol/commit 链首**（V4FLASH 用 opencode-go）
5. **清理 6 类失效模型**（qwen3.8-max-preview、cline-free/glm-5.2、stepfun/step-3.7-flash、deepseek/deepseek-v4-flash、poolside/laguna-s-2.1:free 在 config.yml/models.yml 已 0 残留；deepseek-v4-flash-0731 已从全部 fallbackChains 移除，models.yml 残留条目与 p0-systems provider 于 2026-08-03 18:39 一并删除，equivalence 对应映射同步清理）
6. **移除 codebuddy/gpt-5.6-sol**（WorkBuddy 客户端专属硬 403，5 条链死项 + models.yml 条目）
7. **删除 nihaox-k3 provider**（3 模型全死：glm-4.7-flash 403/503、deepseek-v4-flash-0731 503、mimo-v2.5 400 未定价；2026-08-03 18:39 连同 models.yml 中残留的 p0-systems provider 块一并清理）
8. **gpt-5.6-sol 补 contextWindow 1048576**（gpt-5.5→gpt-5.6-sol promotion 触发条件）

## Inception Labs mercury-2 接入

- **NewAPI**：改造 ch61（原 p0-systems-deepseek 禁用渠道）→ `inceptionlabs-mercury2`，base `api.inceptionlabs.ai`，models `mercury-2`，weight 5
- **坑**：POST /api/channel/ 用平铺载荷（缺 `channel` 包装层）会触发 Go nil-pointer panic → HTTP 500。正确载荷是 `{"mode":"single","channel":{...}}`——`AddChannelRequest.Channel` 是 `*model.Channel` 指针，平铺时该指针保持 nil，**这是客户端载荷形状错误，创建功能本身可用**；而部署版 rc.21 的 `validateChannel` 在判空之前先调 `channel.ValidateSettings()`，解引用先于 nil 检查，使错误表面成 500 panic 而非 4xx 校验信息——**这是服务端校验顺序缺陷**（上游 main 已把 nil 检查前置，返回 "channel cannot be empty"）。两个事实须分开记
- **绕过方式**：本次改用 PUT /api/channel/ 对既有（原禁用）渠道 ch61 原地改造，复用其渠道 ID；PUT 是「更新既有渠道」语义（按 body 内 `id` 定位），body 不能含 `status` 字段，也无法新建渠道
- **坑**：SelfUseModeEnabled 非免费，未配价模型按 37.5 兜底倍率计费（$75/1M，厂商价 300 倍）→ 配 ModelRatio `mercury-2: 2.0`
- **OMP**：zg-newapi 加 mercury-2 模型（contextWindow 128000 / maxTokens 50000，实测 max_tokens=50000 恰为上限）
- 官方模型限制：仅 `mercury-2` 可用（mercury/mercury-coder 需 2026-02-24 前账户）

## 聚合渠道健康修复（截至 2026-08-03 观测）

| 渠道                   | 问题                           | 修复                                                                                                                                               |
| ---------------------- | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| ch35 cline-free-proxy  | 502（4 账户 empty response）   | 禁用 status=2                                                                                                                                      |
| ch50 inferx-deepseek   | 持续超时 70s                   | 降权 w 2→1                                                                                                                                         |
| ch53 atomcode-bridge   | weight=0                       | 确认软下线（本地 9457 健康）                                                                                                                       |
| ch55 inferx-deepseek-b | 极慢 62s（瞬时）               | 观察（复测 0.9s 恢复）                                                                                                                             |
| ch44 codebuddy         | gpt-5.6-sol WorkBuddy 专属 403 | 移除 gpt-5.6-sol，保留 glm-5.2（注：移除后 Guardian 恢复路径因 channel_models 门控不再写 `default`；模型若重新加入 ch44 则写回竞争恢复，见踩坑 6） |

> 上表为 NewAPI 服务端（aliyun.donglicao.com）当日观测结果，本地文件无法持续核验；渠道健康随时间变化，复核需重新探测。

## 关键踩坑记录

1. **OMP fallbackChains 优先级**：含 `/` 的键（`provider/model`、`provider/*`）优先级 > 角色链。角色主模型命中 `provider/*` 通配键时，角色链整条失效
2. **config.yml 有两个自动写回源**：① OMP 启动自动重选 default；② Guardian 恢复路径 `_update_omp_roles`（条件性，按角色受渠道 models 门控，见踩坑 6）。手动配置可能被任一方覆盖，重启或渠道恢复后都需重新核对
3. **OMP ConfigFile 进程内缓存**：models.yml/config.yml 改动需重启 OMP 生效（tryLoad 不重读）
4. **equivalence 块已 inert**（16.2.12 起），补映射无意义
5. **subagent 模型配置**：scout/librarian/sonic 走 @smol，reviewer 走 @slow；派发时指定不存在的 agent 名会回退通用 task
6. **Guardian 写回契约与写入竞争**（`scripts/ops/guardian.py:1214-1364`，触发点 :1036）：仅当此前被禁用的本地代理渠道恢复（≥2/3 复测通过 + 自动入池成功 + base_url host 匹配 100.83.32.95 且端口正确、无 userinfo + 本地代理存活）时才触发。**并且**写回按角色逐条受 `channel_models` 门控（:1290 从渠道实时 `models` 字段构造，:1314 目标模型不在其中即 `continue` 跳过该角色）。codebuddy 恢复 → 契约是把 `default` 写成 `codebuddy/gpt-5.6-sol:max`，即修复链 6 中因 403 移除的模型；但修复链 6 已把 gpt-5.6-sol 从 ch44 移除，门控不再放行，**当前恢复路径不会写 `default`**，与本文档 modelRoles 快照当前不构成写入竞争。门控读的是恢复时刻的渠道实时 models，一旦 gpt-5.6-sol 重新加入 ch44，写回与竞争即自动恢复——Guardian 仍是 `default` 的潜在第二写入源。agentrouter 恢复 → 只回写 `slow`/`vision` 为 `agentrouter/claude-opus-5:xhigh`，**不含 plan/task**。写回为块级正则定位 modelRoles + 原子替换，静默生效
7. **聚合池 `deepseek-v4-flash` 不支持 `reasoning_effort=max`**（2026-08-03 实测：`max` 返回 404 退役渠道 / 402 无余额 / `invalid, should be one of: low, medium, high, xhigh, none`）。商汤日日新 SenseNova 渠道（转售 DeepSeek）的 `reasoning_effort` 仅支持 `none/low/medium/high`（见 github.com/liliMozi/openhanako#1998），官方 DeepSeek 才是 `low/medium/high/max`。聚合池内 `deepseek-v4-flash` 最高可用 `high`；**`opencode-go` 独立渠道实测支持 `max`**（HTTP 200 + reasoning_content），故 flash 类角色（task/commit/tiny/smol）统一走 `zg-newapi/opencode-go:max`。`atomcode` 本地端点（9457）亦支持 `max`（default 使用）
8. **`models.yml` 声明的 `maxTokens` 会真实截断输出，且不受上游能力校正**：`agentrouter/claude-opus-5` 声明 16384，同渠道 `claude-opus-4-8` 声明 128000；实测上游对 `max_tokens: 128000` 返回 200（`finish_reason: stop`），说明 16384 是本地误配而非上游上限。OMP 对 anthropic 路径按 `maxTokensWithThinkingBudget`（`pi-ai/src/stream.ts:1238`）用 `Math.min(caller + budget, model.maxTokens)` 收口，`xhigh` 档 `ANTHROPIC_THINKING.xhigh = 32768`（`:1254`）已超过 16384，触发 `:1556` 的 `thinkingBudget = maxTokens - MIN_OUTPUT_TOKENS` 降档——**slow/plan/vision 三个角色标称 xhigh 实际拿不到 xhigh 预算，且长输出会被 16K 截断**。已改为 128000
9. **`reasoning: true` 缺失会静音丢弃思维强度**：`clampThinkingLevelForModel` 对 `!model.reasoning` 返回 `undefined`，`:max` 后缀无声失效。`zg-newapi/deepseek-official-v4-flash` 原未标 reasoning，实测返回 `reasoning_content` 且 `reasoning_tokens=288`、`low/medium/high/xhigh/max` 五档全部 200——已补标。反向核验：`gpt-5.5` 与 `codebuddy/hy3-preview-agent` 实测无 `reasoning_content`，保持不标是正确的
10. **`/health` 探活不等于推理可用**：`anyrouter`（本地 8789，pythonw PID 31408）`/health` 稳定返回 `{"status":"ok","keys":1}`，但 `/v1/messages` 5/5 全返回 502 `all keys failed: HTTP 503 (server)`——上游 anyrouter.top 的 key 已失效。Guardian 判活用的正是 `/health`（`guardian.py:569`），因此不会发现也不会自愈这种「进程活着但凭据死了」的状态。该 provider 已加入 `disabledProviders`，恢复时需手工移除
11. **`zg-newapi/step-router-v1` 是死条目**：3/3 返回 400 `you have no active step plan subscription`（无 StepPlan 订阅）。原位于 `default` 链第 9 位，故障时会白耗一次重试；已从 models.yml 与 default 链移除

## 当前状态（2026-08-03 观测，非永久结论）

- OMP 配置当日核验全有效：9 角色 + 12 链共 62 条引用 100% 可解析，0 断裂，0 指向 disabled provider
- **真实推理探测**（不止列模型）：23 组 `模型 × 思维档` 逐个发 chat 请求，20 成功。5 连采样：`agentrouter/claude-opus-5` 5/5、`zg-newapi-anthropic/claude-opus-5` 5/5、`zg-newapi/opencode-go` 5/5、`zg-newapi/gpt-5.6-sol` 4/5（1 次 504/超时，designer 角色偶发抖动，链已覆盖）
- 本轮审计修复（备份 `*.20260803-203411.bak`）：agentrouter/claude-opus-5 maxTokens 16384→128000（踩坑 8）；deepseek-official-v4-flash 补 `reasoning: true`（踩坑 9）；anyrouter 入 disabledProviders（踩坑 10）；step-router-v1 从 models.yml + default 链移除（踩坑 11）
- 已知未修（可接受）：`zg-newapi/mercury-2`、`zg-newapi/grok-4.5` 零引用但实测存活（200），保留作手动切换候选
- 长输出压测暴露**上游侧**限制，非配置问题：`agentrouter` 与 `zg-newapi-anthropic` 在 16K 输出的非流式请求上 >200s 无响应（agentrouter 一次返回 502 `deadline exceeded (>120s) ... HTTP 504`）；改流式后两者均正常出流（93KB/282 chunk、170KB/1230 event）。**长任务应依赖 OMP 默认的流式路径**
- gpt-5.6-sol 池 6 渠道当日 8/8 测试通过（403 消除）；服务端健康非持续保证，复核需重新探测
- mercury-2 计费已配价（2.0 倍率）
- Guardian（自愈）+ watchdog 当日在运行；`default` 角色取值当前主要由 OMP 启动重选决定（Guardian codebuddy 写回被 ch44 的 channel_models 门控挡住，见踩坑 6），任何时点以 `~/.omp/agent/config.yml` 实际内容为准

## 相关文件

- OMP 配置: `~/.omp/agent/config.yml`、`~/.omp/agent/models.yml`
- Guardian: `scripts/ops/guardian.py` + `~/.omp/guardian/`
- NewAPI: `https://aliyun.donglicao.com`
