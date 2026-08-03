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
