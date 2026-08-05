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
12. **`agentrouter` 上游对短流式请求敏感词误杀**：`omp bench agentrouter/claude-opus-5:xhigh` 3/3 返回 `500 sensitive words detected`；代理日志证明 stream=True 短请求全 500、stream=False 全 200、`msgs=134` 的长流式会话 200 通过。过滤在 agentrouter.org 上游，非本地非 NewAPI。slow/plan/vision 主路由已切走（见第二轮调优）

## 第二轮调优（2026-08-03 晚间，备份 `config.yml.20260803-211748.bak`）

### OMP 角色/subagent 最终配置（本轮重排）

| 角色                 | 模型（最终）                                                   | 变更理由                                                                                                  |
| -------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| slow / plan / vision | `zg-newapi-anthropic/claude-opus-5:max`                        | 主路由从 agentrouter 切走（上游敏感词误杀，见下）；聚合池 5/5 采样 200，thinking 2048/4096 正常           |
| commit / tiny / task | `zg-newapi/opencode-go:max`                                    | 恢复用户确认值；bench `:high` TTFT 1.6s/50 tok/s vs `:max` 1.5s/74 tok/s，max 不慢反快                    |
| smol                 | `codebuddy/glm-5.2`（无后缀）                                  | 全档支持 minimal~max，scout/librarian/sonic 的 frontmatter（medium/minimal）真正生效；本地代理 20/20 稳定 |
| designer             | `zg-newapi/gpt-5.6-sol:high`                                   | 保持                                                                                                      |
| default              | `zg-newapi-anthropic/claude-opus-5:high`                       | OMP 运行时重选覆盖（21:47 写入 gpt-5.6-sol:high → 21:55 观测已变 claude-opus-5:high），易变快照           |
| security-reviewer    | `@slow`（用户覆盖 `~/.omp/agent/agents/security-reviewer.md`） | bundled 无 model 字段 → 实测继承会话模型不可预测；覆盖后固定 slow                                         |

**fallbackRevertPolicy: never → cooldown-expiry**：复杂工程长会话中一次瞬时故障会永久降级整场会话，改为冷却后自动回主模型。

**链重排**：smol/slow/plan/vision 链首改为 agentrouter（本地后备，真实会话可通过）；慢链/plan 链/vision 链主模型与链首一致化。

### 实测基准（bench，本轮新增证据）

- `zg-newapi/opencode-go:max` 3/3 OK，TTFT 1608ms、79.6 tok/s（上轮）；`:high` vs `:max` 同题对比：TTFT 1.6s vs 1.5s、tok/s 50 vs 74——max 不慢反快
- `codebuddy/glm-5.2:minimal` TTFT 2.3s/49 tok/s；`:medium` 2.8s/41 tok/s——scout 用 medium 档时成本/延迟可接受
- `zg-newapi-anthropic/claude-opus-5:max` 2/2 OK，TTFT 2.6s（64 token 短输出）
- `agentrouter/claude-opus-5:xhigh` 3/3 失败 `500 sensitive words detected`（见下）

### agentrouter 上游敏感词误杀（本轮新发现，踩坑 12）

`omp bench agentrouter/claude-opus-5:xhigh` 3/3 返回 `500 sensitive words detected (type=new_api_error param=sensitive_words_detected)`。追查代理日志（`~/.kimi-code/proxies/agentrouter-proxy/proxy.log`）结论：

- **stream=True 的请求全 500**（bench 用流式），**stream=False 全 200**（curl 直打 8788 全过）
- 同刻 OMP 主会话 `msgs=134` 的 stream=True 大请求 200 通过——**上游对短流式请求（2-msg 精简 prompt）误杀，对长真实会话放行**
- 过滤在 agentrouter.org 上游（代理转发目标），非本地、非 NewAPI（NewAPI 日志 0 条该错误）

**处置**：slow/plan/vision 主路由切走（保留 agentrouter 作链首 fallback，真实 subagent 会话 msgs≥2 且内容复杂时能通过，reviewer 探针 2m27s 成功为证）。

### NewAPI 渠道修复（本轮）

| 渠道                        | 处置                                                                                                                                                                                                                                                  |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ch3 baibei-100xlabs         | `auto_ban=0`（根因见下）+ prio 57→50、w 40→20（打散公益池独占顶层）                                                                                                                                                                                   |
| ch9 linxi-k40               | 显式禁用 status=2（自禁 status=3 + 实测超时，防 AutomaticEnable 反复拉起）                                                                                                                                                                            |
| ch18 linxi-k40-opus5-backup | 降层 prio 54→40、w 10→2（同源 502）                                                                                                                                                                                                                   |
| ch36 stepfun-step-plan      | 禁用 status=2（无 StepPlan 订阅 400）                                                                                                                                                                                                                 |
| ch50/55 inferx-deepseek     | 禁用 status=2（429/慢）                                                                                                                                                                                                                               |
| ch56 hf-deepseek-0731       | 禁用 status=2（端点退役 404）                                                                                                                                                                                                                         |
| ch30 fastaitoken-gpt        | 禁用 status=2（INSUFFICIENT_BALANCE 403）                                                                                                                                                                                                             |
| ch62/63/64/65 centos-gpt    | models 摘掉 `gpt-5.5`（上游 codex测试 组无该模型，503）→ 只留 gpt-5.6-sol；实测 4/4 OK（1.9-3.8s）                                                                                                                                                    |
| ch26/27/28/57 gorouter 池   | 提权 w 5/3/4/4 → 8/6/6/6（实测 2.4-2.6s 健康）；**后查 ch27/ch57 上游 key 余额 ¥0.047，claude-opus-5 预扣 ¥0.2 失败（403 insufficient_user_quota）→ 禁用 status=2**，池剩 ch26/ch28（实测 OK）                                                        |
| ch53 atomcode-bridge        | w 0→5（实测 1.8s 健康）                                                                                                                                                                                                                               |
| ch46/47 bazaarlink          | 降权 w 3→2（实测 5-7s 慢）                                                                                                                                                                                                                            |
| ch49/54 inferx-glm52        | prio 30→50（实测 1.5-1.8s 比 codebuddy 快）                                                                                                                                                                                                           |
| ch45 agentrouter            | 提权 w 15→20 同层分流（本地代理稳定）                                                                                                                                                                                                                 |
| ch20 fengwind-gpt56sol      | 复用原 `fengwind-grok`（禁用渠道，同 base_url）改造为 gpt-5.6-sol 独立源：新 key、models 改 `gpt-5.6-sol`、w=15、prio=50、`auto_ban=0`、启用；实测 OK 4.4s，为 gpt-5.6-sol 池新增独立上游（与 centos/agentrouter 不同源，分散 default/designer 风险） |

**全局选项**：`ChannelDisableThreshold` 3→50（恢复 newapi-audit-2026-07-29 记录值；公益池抖动不该 3 连击即封）。

### baibei-100xlabs (ch3) 反复自动禁用根因（用户问询）

**不是密钥坏了，是 NewAPI auto-ban 对公益池抖动太敏感形成的 flap 循环**：

1. 100xlabs 是公益共享池：上游并发上限（`Concurrency limit exceeded` 500/429）+ Cloudflare 间歇 502（origin overloaded），抖动是常态
2. ch3 开着 `auto_ban=1`，`ChannelDisableThreshold=3` → 连续 3 次失败即 auto-ban（`status_reason: All keys are disabled`，multi_key key 0/4 status=3 残留为证）
3. `AutomaticEnableChannelEnabled=true` → 之后自动恢复 → 流量又打进去 → 又抖 3 次 → 又禁 = **flap 循环**
4. Guardian `state.json` 无 ch3（disabled 只有 ch38）——**是 NewAPI 自身机制，不是 Guardian**

证据：3 次 `test_channel` 全 200（2.2-3.4s）、日志有正常成功请求；但文档早有记录（`zg-claude-routing.md`：100xlabs 账号并发上限 / `gorouter-claude-newapi.md`：ch3 opus-5 近乎 100% 500 Concurrency limit）。

**修复**：ch3 `auto_ban=0`（对齐 `newapi-aggregation-pools-2026-08-01.md` 公益源 auto_ban=0 策略，同 ch46/47）+ 全局阈值 3→50。

### Guardian 探活加固（guardian.py）

`check_local_proxy` 返回 `(healthy, msg, alive)` 三元组：

- **alive=False**（进程无响应）→ 重启；**alive=True 且 healthy=False**（上游/凭据异常）→ 只告警不重启，避免无效重启风暴
- anyrouter 曾用 `/health` + `/v1/messages` 探针确认「进程活着但上游死 key」；确认后已从 OMP `disabledProviders` 和 Guardian `LOCAL_PROXIES` 移除，当前 Guardian 不再周期探测它
- agentrouter/codebuddy/atomcode 保持 `/v1/models`/`/v1/usage` 浅探针

同步更新：主监控循环、重启后验证、OMP 角色写回探活；仓库 Guardian 测试当前 `74 passed, 2 subtests passed`。

## 当前状态（2026-08-03 观测，非永久结论）

- OMP 配置第二轮终态核验全有效：9 角色 + 12 链 100% 可解析，0 断裂，0 指向 disabled provider；`gpt-5.5` 已从 config.yml 两条链 + models.yml 彻底移除（NewAPI 侧零启用渠道，`contextPromotionTarget` 一并删除）
- **复杂多 agent 工程烟测（第二轮）**：5 角色并发（scout/task/designer/reviewer/security-reviewer）全部完成零失败；raw JSONL 确认模型解析：scout→`codebuddy/glm-5.2`（thinkingLevel=**medium**，frontmatter 生效）、task→`opencode-go:max`、designer→`gpt-5.6-sol:high`、reviewer/security→`zg-newapi-anthropic/claude-opus-5:max`（@slow 覆盖生效）
- **重启持久性**：config.yml 自 21:47 写入后 8 分钟未被 OMP 改写（default 保持 claude-opus-5:high）；OMP 运行中不持续覆盖配置
- **failover 验证**：新慢链主模型 `zg-newapi-anthropic/claude-opus-5:max` bench 2/2 OK（TTFT 2.6s）；agentrouter fallback 对真实 subagent 会话可用（reviewer 探针 2m27s 成功），仅短流式 bench 请求被上游敏感词误杀（踩坑 12）
- **gorouter 欠费定位**：用户报告 403 `insufficient_user_quota`（$0.047/$0.2），NewAPI 日志无记录（预扣失败在计费中间件拦截，不写 type=5）；OMP 会话 `retryRecovery` 元数据确认错误来自 `zg-newapi-anthropic/claude-opus-5`；逐渠道实测 `test_channel?model=claude-opus-5` 精确复现：**ch27 gorouter-claude-2 + ch57 gorouter** 上游 key 余额 ¥0.047 → 禁用 status=2，池剩 ch26/ch28（实测 OK）；修复后慢链 bench 2/2 OK
- **fengwind gpt-5.6-sol 接入**：新 key @ `api.fengwind.com`，实测 gpt-5.6-sol/luna/terra 全可用（含 reasoning_content + 流式 + 长输出 831 tokens/26.5s）；复用 ch20（原 fengwind-grok 禁用渠道）改造，gpt-5.6-sol 池增独立源（6 渠道：ch20/45/62/63/64/65）
- 渠道侧：38 渠道全量复核，禁用 ch9/27/30/36/50/55/56/57（死渠道 + 余额不足 + gorouter 欠费），ch62-65 摘 gpt-5.5，ch20 接入 fengwind，claude-opus-5 池重排（ch3 降层、ch45 提权、gorouter 池清欠费），`ChannelDisableThreshold` 3→50
- Guardian 探活加固已落地（三元组 + 存活/推理故障分流），仓库测试 `74 passed, 2 subtests passed`；anyrouter 已从 `LOCAL_PROXIES` 移除，相关告警停止
- **代码 review 修复闭环**：仓库测试旧二元契约已同步；anyrouter 残留配置/重启分支删除；重启验证重复不可达分支删除；NewAPI 失败 streak 在恢复和重启后归零
- 已知未修（可接受）：`zg-newapi/mercury-2`、`zg-newapi/grok-4.5` 零引用但实测存活（200），保留作手动切换候选
- 长输出压测暴露**上游侧**限制，非配置问题：`agentrouter` 与 `zg-newapi-anthropic` 在 16K 输出的非流式请求上 >200s 无响应；改流式后正常出流。**长任务应依赖 OMP 默认的流式路径**
- 本轮备份：`config.yml.20260803-211748.bak`、`config.yml.20260803-203411.bak`、`models.yml.20260803-203411.bak`
- Guardian（自愈）+ watchdog 当日在运行；`default` 角色取值当前主要由 OMP 启动重选决定，任何时点以 `~/.omp/agent/config.yml` 实际内容为准

## 相关文件

- OMP 配置: `~/.omp/agent/config.yml`、`~/.omp/agent/models.yml`
- Guardian: `scripts/ops/guardian.py` + `~/.omp/guardian/`
- NewAPI: `https://aliyun.donglicao.com`

### TTFT 首字延迟优化（2026-08-03 深夜，社区/GitHub 方案落地）

**证据链**：日志 1000 条 0 错误，但 claude-opus-5 avg 37s、ch45 agentrouter 22% >60s；NewAPI issue #4992（网关 TTFT 劣化 3-4x，thinking done 后 token 慢）官方 closed-not-planned；Claude 官方文档：thinking budget 越大 TTFT 越长。

**关键发现**：OMP `ANTHROPIC_THINKING`（pi-ai/src/stream.ts:1263）：`xhigh: 32768, max: 32768`——**xhigh 与 max 预算相同**，从 max 降 xhigh 不省 TTFT；要省必须降到 high（16384）/medium（8192）。OMP 走 interleaved thinking（`thinkingEnabled+thinkingBudgetTokens`），无 signature-only 开关（`thinking.type:"disabled"` 未暴露）。

**已执行**：

1. OMP slow/plan/vision：`claude-opus-5:max` → `:high`（预算 32768→16384，TTFT 减半预期；备份 `config.yml.20260803-234140.bak`）
2. ch45 agentrouter 降权 w 20→10（22% >60s 不该最高权重）+ abilities 重建（38/0）

**验证**：`omp launch -p --model zg-newapi-anthropic/claude-opus-5:high` → OK（7.2s）；9 角色全可解析。

**未做（OMP 无 signature-only）**：thinking `type:"disabled"` 加速需 OMP 上游支持，记录为未来优化点。

### 大型项目加固（2026-08-04 凌晨）

大型项目风险评估后落地两条确定性加固：

1. **opencode-go 双渠道**（消除 task/commit/tiny 单点）：ch53 atomcode-bridge（本地 9457，实测支持 max thinking 2.0s）加 `opencode-go` 模型 + model_mapping→deepseek-v4-flash，w=5 备用。opencode-go 现为 ch48(w22 主力) + ch53(w5 备用)，实测双渠道 OK（3.3s / 7.0s）。OMP 端到端 `zg-newapi/opencode-go:max` → OK。
2. **ch9 linxi-k40 auto_ban=0**（防翻转循环）：与 ch3 同策略——公益源抖动不该 auto-ban。之前一天内多次 禁用→自动恢复→再禁用。

验证：abilities 重建 38/0；opencode-go 池 2 渠道；OMP task 主模型端到端 OK。

### bigctx 大上下文角色（2026-08-04 凌晨）

新增 `bigctx` 角色 → `longcat/LongCat-2.0`（官方 api.longcat.chat，**1M 上下文 / 131K 输出**，比 claude-opus-5 的 200K 大 5 倍），适合超大 repo 分析 / 长文档理解。

- **bigctx 链**：`longcat/LongCat-2.0` → `zg-newapi/gpt-5.6-sol` → `codebuddy/kimi-k3` → `zg-newapi-anthropic/claude-opus-5`
- 备份：`config.yml.20260803-235519.bak`
- 验证：OMP 端到端 OK（9.3s）；官方端点 200（2.2s/11 chunks）

**工程项目角色闭环**：10 角色 + 13 链 0 断裂，全可解析：

- slow/plan/vision → claude-opus-5:high（强推理）
- task/commit/tiny → opencode-go:max（双渠道 ch48+ch53）
- smol → glm-5.2（轻量 agent）
- designer → gpt-5.6-sol:high
- **bigctx → LongCat-2.0（1M 上下文，新增）**
- default → deepseek-v4-flash:max

### smol 角色提速（2026-08-04 凌晨）

`smol` 主模型 `codebuddy/glm-5.2` → **`zg-newapi/sensenova-6.7-flash-lite`**（商汤 ch15，实测 0.35-0.5s，比 glm-5.2 快 ~5 倍）。

- **权衡**：sensenova 文档实测不返回 `reasoning_content`（思考混在正文），scout/librarian/sonic 的 frontmatter medium/minimal thinking 会静默失效——smol 是轻量只读侦察/摘要任务，速度优先于思考深度，可接受
- **链**：sensenova → glm-5.2（保 thinking）→ opencode-go → …（6 项），glm-5.2 降为 thinking 兜底
- models.yml 加声明：`zg-newapi/sensenova-6.7-flash-lite`（131K ctx/32K out，无 reasoning 标记）
- 备份：`config.yml.20260804-001017.bak`、`models.yml.20260804-001017.bak`
- 验证：OMP 端到端 OK（5.6s 含启动，纯推理 0.5s）；10 角色 13 链 0 断裂

### 本机安全与可靠性加固（2026-08-04）

- NewAPI 管理 token 已轮换；旧 token 验证失效，Guardian 已加载新 token。管理请求继续使用 `Authorization: Bearer` + `New-Api-User`，secret 仅存 `~/.omp/guardian/secrets.json`。
- atomcode（9457）与 agentrouter（8788）改为仅监听 `127.0.0.1`，均强制 API key。atomcode 同时接受标准 `Authorization: Bearer` 与兼容的 `X-API-Key`；无认证均返回 401，正确认证返回 200，LAN/Tailscale 地址不可达。
- Guardian 本地代理探针按端口注入对应 Bearer key，不再回退到 Tailscale 地址；重启命令显式传入 localhost 绑定与 secret 环境变量。运行时与仓库镜像已同步。
- OMP 并发上限：全局 subagent `maxConcurrency=6`、`maxRuntimeMs=1200000`；活跃 provider `maxInFlightRequests=4`；`zg-newapi` 请求重试收敛为 1 次。NewAPI `RetryTimes=1`，自动重试状态码排除 401/402/403/504，避免认证、余额与超时错误放大。
- Guardian 将 429/rate-limit 归类为瞬时故障：不禁用渠道；连续 3 次软失败后才降权。Telegram HTML 输出统一转义渠道名、错误摘要和 request ID；失败告警 best-effort 补充 NewAPI/upstream request ID。外部命令改为参数数组 + `shell=False`。
- Windows 解释器通配入站防火墙规则已禁用；运行所需代理依赖 loopback，不再需要 Node/Python 任意端口入站。OMP/Guardian credential 文件 ACL 已限制为当前用户、SYSTEM、Administrators。
- NewAPI 日志清理通过一次性 `POST /api/system-task/log-cleanup?target_timestamp=<unix>` 执行；当前部署未提供持久 retention-days 配置，需按运维周期再次触发。

**验证证据**：Guardian `py_compile` 通过，完整 `scripts/ops/test_guardian.py` 为 83/83 OK；OMP 10 个角色、13 条 fallback chain 可解析；两个本地代理认证边界按上述 401/200/不可达组合实测通过。

### 书生 Intern S2 Preview 接入（2026-08-04）

- 官方 Claude-like API：`https://chat.intern-ai.org.cn/v1/messages`，模型 ID `intern-s2-preview`，认证头 `x-api-key`。官方文档标注 256K 上下文，单次 `max_tokens` 建议不超过 8K；工具调用场景保持 thinking 开启。
- 两枚 key 直连最小推理均返回 200（约 0.6s）。为保留逐 key 的健康、限流和额度可观测性，先建为两个独立 NewAPI Anthropic 渠道：ch66 `internlm-s2-a`、ch67 `internlm-s2-b`；均为 priority 50、weight 5、`auto_ban=1`。
- NewAPI 逐渠道各复测 2 次，4/4 成功，耗时 0.37–0.69s；abilities 重建结果 40 success / 0 fail。
- OMP 在 `zg-newapi-anthropic` 注册 `intern-s2-preview`：`reasoning: true`、text/image、context 262144、max output 8192。未改现有角色或 fallback chain，仅增加可选模型。
- OMP 端到端烟测：`omp launch -p --model zg-newapi-anthropic/intern-s2-preview --thinking high` 返回 `OK`，总耗时 6.14s。

### 外部证据 librarian 加固（2026-08-04）

- 不新增 `community-scout`；复用现有 `librarian`，保持大型工程角色数量与编排成本稳定。
- 用户覆盖 `~/.omp/agent/agents/librarian.md` 将证据优先级明确为：官方文档/发布源码/合并代码 → issues/PR/discussions → 社区报告。GitHub 结果必须区分 released、merged-unreleased、open、proposal、stale；社区内容只可标为 signal，不得作为证明。
- 默认继续走 `@smol`；需要跨仓深度源码追踪时返回现有证据并建议该具体调查升级 `@slow`，不允许猜测。
- 烟测以 InternLM/Claude Code 兼容性为题完成：返回官方文档、官方仓库、GitHub issue 状态和社区信号，结构化来源契约生效，全程只读。

### Guardian 本地代理矛盾告警修复（2026-08-04）

atomcode 在 01:22:44 报“已重启并验证存活”，01:22:45 紧接“无响应”。根因不是代理再次掉线，而是 `_check_cycle` 在 `restart_local_proxy()` 成功后仍无条件使用重启前的失败 `msg` 发送故障告警。调用方现仅在重启返回 `False` 时发送“本地代理故障”；成功通知继续由重启函数发送。新增成功/失败两条行为测试，Guardian 完整测试 85/85 通过，运行时已重启加载修复。

### 多模型独立审查完成门（2026-08-04）

多模型并发不能保证“自动发现所有问题”；相同任务切片、相同提示和相同测试会产生相关盲区。后续行为变更使用三道相互独立的完成门：

1. `@task` 实现并运行针对 observable contract 的机械测试。
2. `@slow` reviewer 不接受实现摘要，必须读取 diff、所有 caller/consumer，并输出每条行为路径的 trigger、顺序状态转换、外部副作用、terminal outcome 和源码证据。
3. 主线程只接受 `review_status=completed` 且 `overall_correctness=correct`；reviewer 超时、中断、取消、缺 final payload、`blocked` 或存在未解决 finding 均为未完成，测试通过不得替代。

用户级 `~/.omp/agent/agents/reviewer.md` 已编码上述契约。重点强制检查返回值是否被 caller 消费、调用后是否继续使用旧状态、成功/失败通知是否互斥、重复写入/告警、retry/rollback/timeout/cancellation/cleanup，以及测试是否真正断言副作用顺序。实现 worker 常规走 `@task`（opencode-go），reviewer 固定走独立模型族 `@slow`（Claude Opus），降低同模型自审的相关盲区。

### OMP 故障路由现场复审（2026-08-04 02:25）

当前 `~/.omp/agent/config.yml` 使用 `retry.maxRetries=2`、`baseDelayMs=3000`、`maxDelayMs=60000`、`modelFallback=true`、`fallbackRevertPolicy=cooldown-expiry`。模型注册完整，`omp models` 可解析 6 个 provider、22 个模型；问题不在 selector 断裂，而在故障判定和链路独立性。

#### 现场探针

同一提示 `Return exactly ROUTE_OK.`，每模型 1 run、48 max tokens、并发 2：

| 路由                                     | 结果 |       TTFT / 总耗时 | 结论                                                         |
| ---------------------------------------- | ---- | ------------------: | ------------------------------------------------------------ |
| `zg-newapi-anthropic/claude-opus-5:high` | 200  | **188.2s / 188.2s** | 请求最终成功，因此 OMP 不触发 fallback；用户先承受三分钟空等 |
| `agentrouter/claude-opus-5:high`         | 500  |            立即失败 | `sensitive_words_detected`，已知上游短流式误杀仍存在         |
| `zg-newapi/opencode-go:max`              | 200  |       2.10s / 2.54s | 快速、独立模型，可作有效降级                                 |
| `codebuddy/glm-5.2:medium`               | 200  |       1.47s / 2.75s | 本地独立入口，可作有效降级                                   |

#### 当前高风险

1. **没有首字节故障门。** OMP 的 provider fallback 由错误驱动；上游只要保持连接并最终返回 200，188 秒 TTFT 也不会切换。`retry.maxDelayMs` 是重试退避上限，不是请求或首字节超时。
2. **Claude 第一备用是已知坏路由。** `slow` / `plan` / `vision` 的首个 fallback 均为 `agentrouter/claude-opus-5`；短流式探针持续被敏感词过滤，先消耗一次失败才能进入下一候选。
3. **备用并不真正独立。** `zg-newapi-anthropic/claude-opus-5` 进入 NewAPI Claude 池；该池也包含 channel 45 `agentrouter`。随后再切本机 `agentrouter/*` 可能命中同一上游故障域，不是可靠冗余。
4. **同源/同模型重复过多。** `slow` 链同时含 NewAPI Opus 5、NewAPI Opus 4.8、本机 AgentRouter Opus 5/4.8。鉴权、内容过滤、Tailscale、本地代理或 AgentRouter 上游故障时会连续失败；模型名不同不等于故障域不同。
5. **重试放大。** OMP 每候选最多重试 2 次；NewAPI 自身还有渠道重试和池内切换。慢连接或 429/5xx 下，总等待是多层重试乘积，不是简单的 2 次。
6. **`default` 链语义过宽。** 主模型若没有 exact/provider/role 专用链，会落到 `default`；其中先经过 opencode-go、两个 AgentRouter Claude，再到 Kimi/LongCat/NewAPI Claude。能力、图像支持、上下文长度和工具协议并不等价，降级后可能成功返回但质量或能力静默变化。
7. **`cooldown-expiry` 只解决恢复，不解决误切。** 它能让会话冷却后回主模型，但无法识别“极慢的成功”、相关故障域或候选能力不匹配。

#### 当前操作判据

- `fallback applied` 只证明 OMP 收到了可切换错误，不证明首字节卡顿会自愈。
- 对 Claude 慢请求，先看 NewAPI 日志的 channel id / `use_time`；不能仅看最终 HTTP 200。
- AgentRouter 在短流式请求修复前，不应作为 Claude 链首备用；保留为人工直连诊断入口比自动首跳更安全。
- 自动链应优先跨故障域：NewAPI Claude → `zg-newapi/opencode-go` → `codebuddy/glm-5.2` → LongCat/Kimi；同源 Claude 变体放末尾或移除。
- 不建议仅缩短 retry delay；真正缺失的是有取消能力的请求/首字节 deadline。OMP 当前配置未暴露该 deadline，不能用 `maxDelayMs` 冒充。

本节是审计结论，未直接改写运行时 fallback 顺序：移动候选会改变 slow/plan/vision 的质量、图像能力与供应商边界，应在独立 smoke 中验证后再切。

### 稳定化落地与现场验证（2026-08-04 03:10）

#### OMP 运行路由

- `slow` / `plan` / `vision` 主模型为 `zg-newapi-anthropic/claude-opus-5:high`；`commit` / `tiny` / `task` 为 `zg-newapi/opencode-go:high`；`smol` 为 `codebuddy/glm-5.2`；`default` 为 `codebuddy/gpt-5.6-sol`。
- `retry.maxRetries=2`，`fallbackRevertPolicy=cooldown-expiry`，全局并发 6；provider 级并发限制为 4。
- `slow` 自动备用链已把已知存在敏感词误杀的 `agentrouter/*` 放到末尾。Reviewer smoke 在 NewAPI Opus 路由上超过 10 分钟未完成，因此用户级 `reviewer` 与 `security-reviewer` 固定到 `zg-newapi/gpt-5.6-sol:high`；保留 Opus 给需要长推理的 `slow` / `plan` / `vision`，避免审查门被慢首字节阻塞。

#### Guardian 自愈边界

- 本地代理探活执行最小 `/v1/chat/completions` 推理；`2xx` 为健康，`401/403` 为进程存活但鉴权异常，`5xx` 为进程存活但上游异常。推理连接异常后额外探测 TCP 端口：端口可连接只告警，不重启；端口连续 3 轮不可达才进入代理重启。
- 该二阶段判定修复了 02:54–03:16 的现场误报：推理等待超过 8 秒时，旧逻辑错误标记 `alive=false`，造成重复“无响应”通知。部署修复并清除旧断路器状态后，Guardian 自动恢复 AgentRouter；现场探针最终确认 AgentRouter、CodeBuddy、AtomCode 三个入口均完成真实推理。
- NewAPI 不可达时仍检查本地代理，但跳过渠道扫描、日志、余额、abilities 等依赖请求，避免同一故障扇出。
- NewAPI 重启需要连续 3 次失败；本地代理端口也需要连续 3 次失败。重启验证每 2 秒复测、最多 5 次；断路器通知由重启状态机唯一负责，避免同轮重复“重启失败”和“本地代理故障”。渠道恢复需要 3 次复测至少 2 次通过，随后重新加入池，并接受 10 分钟稳定性回滚。
- Telegram `/agents` 从 OMP JSONL 会话事件提取角色、实际模型和生命周期；`yield` 或 `session_exit` 记为 completed，5 分钟无新事件记为 stalled。这是外部 Guardian 的观测投影；OMP 进程内 `AgentRegistry` / `hub` 仍是权威实时状态。

#### 验证证据

- Guardian 回归套件：92 tests，全部通过。
- 定向故障注入：NewAPI 不可达、代理 502、TCP 连接失败、推理超时但 TCP 存活、连续失败防抖和重启断路器，全部通过。
- 并发 subagent smoke：coding 使用 `zg-newapi/opencode-go` 完成；research 使用 `zg-newapi/sensenova-6.7-flash-lite` 完成；JSONL 状态投影能在运行中显示实际模型，并在 `yield` 后切到 completed。Reviewer 的 Opus 路由卡住，GPT-5.6 重试未产出有效审查结果，因此未将其误报为成功；角色已切至响应稳定的 GPT-5.6。
- 关键路由独立烟测：`zg-newapi/opencode-go:high` 2.44s 成功，`codebuddy/gpt-5.6-sol:high` 3.16s 成功；三路并发烟测因 Opus 超过 300s 被整体超时，证明 Opus 仍只适合作为长任务能力路由，不应承担快速审查门。
- Guardian 重启后 PID/心跳更新；自动恢复后 `restart_counts` 与 `proxy_fail_streaks` 均归零，AgentRouter、CodeBuddy、AtomCode 监听端口和真实推理全部验证成功。

### Guardian 独立审查修复（2026-08-04）

- `401/403` 现分类为“进程存活但鉴权异常”：触发推理异常告警、不会清除故障状态，也不会无效重启进程。
- 重启验证分别记录进程存活与推理健康。端口恢复但推理仍异常时，进程重启记为成功并单独告警；只有端口始终不可达才累计重启失败和打开断路器。
- 推理探针只捕获预期的传输异常；意外程序异常继续抛给主循环边界记录 traceback，避免伪装成网络故障。
- Telegram `/agents` 标题改为“Subagent 近期会话状态”，与 JSONL 外部观测投影的实际语义一致。
- 验证：`py_compile` 通过；Guardian 回归套件 95/95 通过，包含鉴权异常、重启后进程存活但推理失败、意外异常传播三类新增/更新契约。

### Agnes AI 双端点 fallback 接入（2026-08-04）

- 新增两个 OpenAI 类型（type=1）Haiku-tier fallback 渠道，仅覆盖已配价模型，未触碰任何现有渠道：
  - **ch68 `agnes-com-haiku`**：pri38/w20，status=1（启用），base_url=`http://100.83.32.95:9460`（**本机 Tailscale 中转**，见下）——实测 `claude-haiku-4-5` 映射推理 21.554s、`agnes-2.0-flash` 原生 6.172s 全部 200；慢速兜底位
  - **ch69 `agnes-cn-haiku`**：`https://api.agnes-ai.cn`，pri39/w10，status=1（启用）——实测 `claude-haiku-4-5` 映射推理 2.456s、`agnes-2.0-flash` 原生 0.192s 全部 200；快渠优先（fork 按 `channels.priority DESC` 选渠）
- models：`agnes-2.0-flash,agnes-2.5-pro-alpha,LongCat-2.0,claude-haiku-4-5,claude-haiku-4-5-20251001,claude-haiku-4-5[1M],claude-haiku-4-5[1m]`（`agnes-2.5-flash`/`agnes-2.5-pro` 未配价故不暴露）
- model_mapping：`LongCat-2.0` / `claude-haiku-4-5*` → `agnes-2.0-flash`（复刻已删除的 #122 模式）
- 创建契约复踩：POST `/api/channel/` 的 `channel.model_mapping` 必须是 **JSON 字符串**，传对象报 `cannot unmarshal object into Go struct field Channel.channel.model_mapping of type string`
- `POST /api/channel/fix` 重建 abilities：42 success / 0 fails
- 渠道全量快照 diff：现有 38 渠道字段零变化，主路由优先级未受影响
- 计费：ModelRatio/CompletionRatio 已含 `agnes-2.0-flash`（0.5/2）与 `agnes-2.5-pro-alpha`，无需新增
- 安全：两枚 Agnes key 曾在聊天明文出现，建议在 Agnes 控制台轮换后 `PUT /api/channel/68|69`（body 含 `key`、不含 `status`）更新

#### ch68 本机中转架构（2026-08-04）

VPS（阿里云国内）直连 `apihub.agnes-ai.com` 60s 超时，但本机可达，且 VPS↔本机经 Tailscale（`100.83.32.95`）互通：

- 本机运行固定上游透传代理 `C:/Users/zhugu/.omp/proxies/agnes-relay/agnes-relay.js`：默认仅绑定 Tailscale `100.83.32.95:9460`（不再监听 `0.0.0.0`），`/v1/*` 原样转发 `https://apihub.agnes-ai.com/v1/*`，Authorization 由 NewAPI 注入；`GET /healthz` 返回带 `service/port/version/upstream` 的稳定身份供幂等探测
- 稳定性加固（2026-08-04）：无效 URL 与 HTTP parser 错误均返回 400、不再崩溃；健康探测要求 200、3.5s wall deadline、4KiB body 上限和精确身份；上游 response error/abort 与下游断开均清理双向流；异服务占用或 listen 竞态非零退出；隔离回归 5/5 通过
- **当前上线自启模式（免提权）**：Windows 计划任务 `agnes-relay`，当前用户 `zhugu`、`AtLogOn`、`RunLevel=Limited`、`StartWhenAvailable`、`IgnoreNew`；action 直接调用 `powershell.exe -NonInteractive -WindowStyle Hidden`，不再经过可见 `cmd.exe`；`run-agnes-relay.ps1` 常驻监督 Node，stdout/stderr 追加到 `agnes-relay.log`
- 同一计划任务包含 AtLogOn trigger 与每分钟 watchdog trigger；正常运行时由 `IgnoreNew` 拒绝重复实例，异常停止时下一周期恢复，始终只有 Task Scheduler 一个 runtime owner。安装器先停止并注销旧任务、清理精确匹配的旧 supervisor/Node，再注册新定义
- 监督恢复实测：Task Scheduler 真正持有 `Node → hidden PowerShell task` 进程树；hub 中旧 `agnes-relay` 已失败且 restart=no，不再拥有端口；relay 当前 `100.83.32.95:9460/healthz` 返回 version=1 的 200
- **严格系统启动模式尚未安装**：`install-autostart.ps1` 已准备 SYSTEM + `AtStartup` + Highest 版本，但当前非提权终端无法操作 UAC secure desktop，两次安装均未注册任务；该版本会先复制脚本到 `%ProgramData%\agnes-relay` 并递归锁定为 SYSTEM/Administrators-only ACL，避免 SYSTEM 执行用户可写代码。管理员执行即可原地升级；当前 AtLogOn 模式必须在用户登录后才运行
- ch68 `base_url=http://100.83.32.95:9460`；最终计划任务所有权切换后 NewAPI 真实测试 `agnes-2.0-flash` 6.659s success=true（此前原生/映射双模型也均成功）
- **依赖本机在线且已登录**：本机离线/未登录时 ch68 失败；Haiku 请求按 pri DESC 先打 ch69（直连），ch68 仅兜底，风险可接受
- 优先级倒挂教训：fork 按 `channels.priority DESC` 选渠，慢渠若 pri 更高会抢快渠流量；ch68 初始 pri40 > ch69 pri39 即倒挂，已降为 pri38

### vip.j3gb.com GPT 聚合（2026-08-04）

- 新建 **ch70 `vip-j3gb-gpt`**（type=1，status=1，group=`default`，pri50/w10，`auto_ban=1`，base_url=`https://vip.j3gb.com`）；密钥未写入仓库或文档
- 上游 `/v1/models` 返回 12 个模型；其中 5 个当前推理返回 403 `Insufficient account balance`（`gpt-5.2`、`gpt-5.4-mini`、`gpt-5.4-openai-compact`、`gpt-5.5-openai-compact`、`gpt-5.6-luna`），1 个无现有计费配置（`gpt-5.3-codex-spark`），均未加入渠道
- ch70 暴露并实测通过的 6 个模型：`codex-auto-review`、`gpt-5.4`、`gpt-5.5`、`gpt-5.6`、`gpt-5.6-sol`、`gpt-5.6-terra`；专属 `/api/channel/test/70` 六项均 `success=true`，耗时 2.291–2.731s
- `POST /api/channel/fix` 返回 `43 success / 0 fails`；聚合入口实推 `gpt-5.6-terra` 返回 HTTP 200、2.288s，NewAPI 日志 `35033`/`35032` 均显示 `channel=70`、`type=2`、`use_time=2`
- 计费配置已覆盖上述 6 个模型（input 0.5 / completion 2）；余额恢复且通过独立测试后，才考虑扩展当前排除模型

### 多 subagent 协同与 Windows 弹窗约束（2026-08-04）

- **单一 runtime owner**：同一服务/端口只允许 Task Scheduler、hub detached、Startup 或其他 supervisor 中的一个负责生产实例；临时 hub 进程必须在计划任务切换前退出，验收需核对 listener 数量和父进程链
- **单写者**：同一批次每个共享运行时文件仅分配一个 writer；scout/librarian 只读调查，writer 只实现，verifier 只运行定向场景，reviewer/security-reviewer 只读审查，Main 唯一执行生产切换与 push
- **完成语义**：subagent 的 `completed` 只表示任务返回，不代表验收；必须由 Main 观察真实运行路径，再由独立 reviewer 检查；reviewer 输出 schema 保持最小，失败时允许普通文本降级，禁止因 schema 复杂度丢失审查结论
- **Windows 后台任务**：Interactive 计划任务必须同时使用 `Settings.Hidden=true`、`-NonInteractive`、`-WindowStyle Hidden`，不得以 `cmd.exe` 作为长期 action；启动目录不得放置可执行 `.bat/.cmd`，使用 `wscript.exe`/VBS 隐藏入口
- 本轮全量修复：`agnes-relay`、`JoyClaw-Daily-PC-Maintenance`、`newapi-backup-pull`、`WeChatACPDailyReport` 均补齐 PowerShell 隐藏参数并设 Hidden；已有隐藏参数但 `Settings.Hidden=false` 的 `KimiCodeAutoUpgrade`、`UserFastClean-Caches` 也已设 Hidden。原定义备份到 `C:/Users/zhugu/.omp/backups/popup-tasks-20260804-132000/`
- Startup 的可见 `cline-glm-proxy.bat` 已改名 `.disabled`，由 `cline-glm-proxy-hidden.vbs` 取代；VBS `/check` 语法验证通过，Startup 中不再存在启用的 `.bat/.cmd`
- 实测停止 `agnes-relay` 后，每分钟隐藏 trigger 自动恢复：任务 Running、单 Node/单 supervisor、`/healthz` 200；跨 trigger 桌面复核仅发现用户当前 Windows Terminal。最终全量扫描启用的用户级 console tasks：`POPUP_RISK=0`

### CatPaw Bridge 接入（2026-08-04，当日移除）

- 曾接入：Windows CatPaw 实时会话凭据 + 本机 Tailscale `100.83.32.95:4567` Bridge + NewAPI ch71 `catpaw-bridge` + OMP 6 个 `catpaw-*` 模型（`contextWindow=8000`/`maxTokens=4096`）。
- **2026-08-04 移除**：实测 CatPaw REST 端点（`/api/gpt/chat/completions`）有效上下文 ≈13k tokens（单条超限返回 `code 9999 内容长度异常`；多轮超 ~13k 被服务端压缩到 ~10k），且无思维链强度参数（thinking/reasoning_effort/chain_of_thought 等全部静默忽略，推理内嵌 content）。无法支撑正经编码任务，整体下线。
- 移除动作：Bridge 目录删除、watchdog 进程停止、Startup 启动行移除、NewAPI ch71 删除（`DELETE /api/channel/71`）、OMP models.yml 6 个 catpaw 条目删除、`secrets.json` 中 `catpaw_bridge_api_key` 删除。

### 上游对照与 modelFallback 回归修复（2026-08-05 傍晚）

**版本**：本地 17.2.9 = npm/GitHub 最新（2026-08-05 凌晨发布），无需升级。17.2.9 恰好修复了 subagent 显式 `model: "default"` 被路由到父会话模型的问题（issue #6438）——官方建议 task 调用省略 `model` 字段让 agent frontmatter 生效。

**本轮发现并修复**：

1. **`retry.modelFallback` 回归 false → 已改回 true**。08-04 02:25 现场复审记录为 `true`，当前文件却是 `false`（今天的两个备份里已是 false，翻转时点/原因无记录，疑似自动改写或某次手工未留痕）。官方默认就是 `true`；为 `false` 时 13 条 fallbackChains 全部失效，故障时只重试不切模型——此前投入的跨故障域链设计（agentrouter 沉底、opencode-go/codebuddy 独立域）等于没生效。恢复后 `cooldown-expiry` 仍保证冷却后自动回主模型。
2. **models.yml 删除 `equivalence` 块**（16.2.12 起 inert，踩坑 4 早有记录，块一直留着）。
3. **models.yml 删除 anyrouter provider 块**（上游 key 已失效，08-03 踩坑 10 确认"进程活着但凭据死了"；provider 本就在 `disabledProviders`，定义留着只会被误恢复）。`disabledProviders` 条目保留，防 provider discovery 重新拾取。

**验证**：YAML 双文件 parse OK；10 角色 + 13 链全部引用可解析，0 断裂（脚本交叉校验）。备份 `config.yml.20260805-184358-modelfallback.bak`、`models.yml.20260805-184358-cleanup.bak`。**OMP ConfigFile 进程内缓存，改动需下次启动 OMP 生效**（官方设计，无热重载）。

**社区/官方新认知（未改动，备查）**：

- 官方维护者（#4317）：稀缺旗舰模型放 `plan` 杠杆最高；**不要放 `advisor`**（每个主 turn 吃一次全量增量 transcript，线性增长）；`default` 放主力干活模型。本地角色分布与此一致。
- `fallbackChains` 只在可重试错误时触发，不会主动降级省配额；触发阈值不可配（#6764 open，单次可重试错误即可能切换——若观察到误切，这是上游行为而非本地配置）。
- 项目层 `.omp/config.yml` 的数组键（`disabledProviders` 等）是**整体替换**而非合并全局层——最常见的配置意外，写项目级覆盖时数组要给全量。
- `omp config reset` 是把 schema 默认值写进文件，不是删键。
- `providers.autoThinkingMaxEffort`（17.2.0 新增，默认 `xhigh`）控制 `defaultThinkingLevel: auto` 可解析的上限；本地 `auto` + 显式 `:high` 后缀的角色不受影响，未改。

### codebuddy/gpt-5.6-sol 回归 OMP（2026-08-05 晚）

08-03 因 WorkBuddy 硬 403 移除（修复链 6）的 `codebuddy/gpt-5.6-sol`，在
converter 前言注入修复（见 local-gateway-hardening-2026-08-05.md）后重新接入：

- **直连实测**：`POST 100.83.32.95:8787/v1/chat/completions` model=
  `gpt-5.6-sol` → 200（9.6s），prompt 489 tok 证实前言注入生效
- **models.yml**：codebuddy provider 补回 `gpt-5.6-sol`（reasoning: true、
  contextWindow 1048576、maxTokens 128000，与 zg-newapi 同名条目对齐）
- **config.yml**：designer 链插入第 3 位（zg-newapi → **codebuddy 直连** →
  claude-opus-5）——直连 converter 绕开 NewAPI，是独立故障域，配合今日
  恢复的 `modelFallback: true` 真正可用
- 备份 `*.20260805-191731-wbsol.bak`；YAML + 10 角色 13 链交叉校验 0 断裂；
  `omp models` 显示 codebuddy (4)。改动需下次 OMP 启动生效

### 大工程路由门禁与 Guardian 单写者收敛（2026-08-05 晚）

本节覆盖此前关于 Guardian 自动写回 `modelRoles` 的历史描述；旧段保留用于事故时间线，不再代表当前行为。

- 从 `slow`、`plan`、`vision` fallback 链删除 `agentrouter/claude-opus-5` / `agentrouter/claude-opus-4-8`。该上游存在已确认的短流式敏感词 500，不能消耗关键链候选。
- `slow` 当前跨域顺序：NewAPI Opus 5 → Opus 4.8 → NewAPI GPT-5.6 Sol → CodeBuddy Kimi K3 → LongCat 2.0。
- `plan` 当前顺序：NewAPI Opus 5 → Opus 4.8 → NewAPI GPT-5.6 Sol → CodeBuddy Kimi K3。
- `vision` 保留支持图像的 `agentrouter/gpt-5.6-sol`，但不再包含 AgentRouter Claude。
- Guardian 删除 `_update_omp_roles` 写入口；渠道恢复只维护 NewAPI 健康状态和 weight，不得覆盖 OMP 角色或人工 priority。
- 新增 `scripts/ops/test_omp_routes.py`：门禁 `modelFallback=true`、`cooldown-expiry`、关键链无已知坏候选、`omp models` 可解析关键 provider。
- 已知上游缺口：OMP 17.2.9 没有可配置首字节 deadline；极慢但最终 200 的请求仍不会触发 fallback，不能用 `retry.maxDelayMs` 冒充请求超时。
- 验证：`omp models` 解析 6 个 provider / 22 个模型；完整 ops 测试 98/98 通过。

### K3 fallback 统一为官方路由（2026-08-05 晚）

- OMP 生效配置中的 `default`、`slow`、`plan`、`bigctx` 四条链已将 `codebuddy/kimi-k3` 替换为官方聚合路由 `zg-newapi/k3`。
- 官方 K3 注册能力：1M 上下文、128K 输出、reasoning、图像；CodeBuddy K3 已从 OMP 模型注册删除。
- CodeBuddy provider 最终只允许 `hy3-preview-agent` 和 `gpt-5.6-sol`；`deepseek-v4-flash` 同样从模型注册及所有 fallback 链删除。Flash 自动链使用 `zg-newapi/opencode-go`、`atomcode/deepseek-v4-flash`、`zg-newapi/deepseek-official-v4-flash`。
- `hy3` 链继续使用 CodeBuddy Hy3，并以官方 `zg-newapi/k3` 兜底；designer 链保留 CodeBuddy Sol。
- 路由门禁覆盖全部 fallback chain 和 CodeBuddy 模型注册，防 K3/DeepSeek 回流 CodeBuddy。

### OMP fallback 主模型去重与跨域优先（2026-08-05 深夜）

- OMP 17.2.9 的 fallback 去重按完整 selector 字符串执行；角色主模型 `provider/model:high` 与链内无后缀的 `provider/model` 不相等，会把同一模型当成第一备用，既白耗切换又可能改变 thinking 档位。
- 已从 `smol`、`slow`、`vision`、`tiny`、`designer`、`plan`、`bigctx` 链删除各自主模型；`task`/`commit` 原配置未重复。
- `slow` / `plan` 主模型保持 NewAPI Opus 5；自动备用优先改为 CodeBuddy Sol → LongCat 2.0，再回到 NewAPI Opus 4.8 / GPT-5.6 Sol / K3。NewAPI 整体入口故障时，第一跳即可跨 provider，而不是在同一 NewAPI 故障域内连续换模型。
- `vision` 主模型保持 AtomCode Qwen3-VL；备用链只保留其他具备图像能力的 Claude/GPT 路由。`designer` 主模型保持 NewAPI Sol，第一备用为 AgentRouter Sol，第二备用为 CodeBuddy Sol。
- `scripts/ops/test_omp_routes.py` 新增门禁：按 provider/model 归一化 thinking 后缀，禁止任一角色 fallback 重复其主模型；同时验证 Anthropic provider 固定走 `127.0.0.1:3003`、API 为 `anthropic-messages` 且不得回退到 `PROXY_MANAGED`。本次 RED 在旧配置发现 7 条重复链；当前路由门禁 7/7 通过，`omp models` 仍解析 6 个 provider。
- 原生 OMP watchdog 不足以约束可见 token：NewAPI Anthropic 流会先发送 `ping`/`message_start`，即使后续长期无文本也不会触发首事件 watchdog。现改由 loopback `scripts/ops/omp-ttft-gateway.cjs`（生产副本 `~/.omp/guardian/omp-ttft-gateway.cjs`）缓存 SSE，只有收到 text/tool 内容才提交 200；60 秒无可见内容返回 504，交给 OMP fallback。supervisor 以唯一 owner 维护 `127.0.0.1:3003 → 127.0.0.1:3002`。

#### TTFT 网关运行与验收

- 项目实现：`scripts/ops/omp-ttft-gateway.cjs`；协议回归：`scripts/ops/test_omp_ttft_gateway.cjs`。默认监听 `127.0.0.1:3003`，上游为 `127.0.0.1:3002`；响应头和首个可见 text/tool 输出门限均为 60 秒，预提交 SSE 缓冲上限为 1 MiB，超限返回 504。
- 生产副本由 `~/.omp/guardian/proxies-supervisor.py` 管理。supervisor 使用 Windows named mutex `Local\\OMPProxiesSupervisor` 保证单 owner，并通过 HKCU `Run\\OMPProxiesSupervisor` 在用户登录时启动，不依赖管理员权限；旧的 Disabled 计划任务不再作为唯一启动保障。
- 变更后的验证命令：`node scripts/ops/test_omp_ttft_gateway.cjs`、`py -m unittest scripts.ops.test_omp_routes`。现场验收还应确认 `127.0.0.1:3003` 监听、`GET /api/status` 返回 200，并观察一次主路由成功或 OMP fallback 成功。
- 详细故障复盘、防复发状态机规则与必测矩阵见 `docs/ops/omp-ttft-gateway-lessons-2026-08-06.md`。

#### 竞态缺陷复盘与防复发经验（2026-08-06）

**根因分类**：D（测试覆盖缺口）+ E（隐式状态假设）+ C（运行时副本传播）。初版只验证 keepalive 超时、文本成功和非 SSE 透传，未验证缓冲溢出与上游响应头阶段；实现还假设 `upstreamRes.destroy()` 后不会再触发能提交 200 的结束路径。

**已复现的失败链**：thinking-only SSE 的首个 Node chunk 可直接超过 `maxBufferBytes`；旧代码先销毁上游并尝试返回 504，但没有设置统一终态，随后 `end` 路径仍调用 `commit()`，客户端最终收到截断 thinking 流和 HTTP 200。这个结果证明“调用 `res.end()`”不能替代显式状态机，也不能假设 destroy 后没有后续事件。

**另一个边界缺口**：语义计时器只在收到上游响应头后创建；若上游接受连接但不返回响应头，请求不受 60 秒可见输出门限约束。现在请求创建即启动独立 header timer，收到 `response` 后清除，再启动 semantic timer。

**防复发规则**：

1. timeout、buffer overflow、upstream error、client abort、upstream end 必须共享单一 terminal state；任何失败先设置终态，再销毁流，所有后续 handler 首先检查终态。
2. SSE 网关测试必须分别覆盖 header timeout、semantic timeout、thinking 非语义、buffer overflow、正常 text/tool commit 和非 SSE 透传。不能用“happy path + 一个 timeout”代表完整状态机。
3. 分块文本解析使用 `StringDecoder`，不能逐 chunk `toString("utf8")`；提交后的转发必须尊重 `res.write()` 背压。
4. 仓库实现与 `~/.omp/guardian/` 生产副本是两个交付面。修改后必须同步副本、重启精确匹配的 gateway 进程，并现场确认 3003、`/api/status` 与 supervisor 单 owner。
5. 文档中的测试数量是时间点证据，新增测试后必须同步更新；优先写“当前 N/N + 命令”，禁止保留与现状冲突的旧数字。
