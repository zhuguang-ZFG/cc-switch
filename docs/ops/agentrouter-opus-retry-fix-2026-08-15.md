# agentrouter Opus 修复 + 日志排查治理（2026-08-15）

## 1. agentrouter-proxy `_is_retryable` 缺陷修复

**问题**：`~/.kimi-code/proxies/agentrouter-proxy/agentrouter-proxy.py` 的重试判定只含
502/503/504 与文本关键词，**402/403 配额错误与 429 限流不会换 key**——proxy.log 实测
08-14 00:23 上游连续 402 "Budget pool quota has been exhausted"，首个 key 失败即中止，
3 key 池形同虚设。


## 2. OMP config 变更

| 变更 | 状态 | 说明 |
|---|---|---|
| bigctx 链首加 `longcat/LongCat-2.0` | ✅ 生效 | 修复 k3(1M) 挂后无 ≥380k 后备的结构性缺口；LongCat 实测 17.2s OK（百万级窗口未压测） |
| `maxInFlightRequests.agentrouter` 4→2 | ✅ 生效 | 公益站克制并发，与 anyrouter=2 先例一致 |
| slow 链尾加 `agentrouter/claude-opus-5` | ❌ 被门禁拒绝 | `test_omp_routes.py` 硬约束：AgentRouter Claude 禁入关键链（上游敏感词误杀，08-13 实测 500）。已回退，**勿再尝试** |

备份 `config.yml.20260815-bigctx-slow-chain.bak`；route gate 33/33 OK。

## 3. ai.168661 key 失效（ch78/79）→ OMP 摘除

探针实测 ch78/79 均 401 Invalid API key（账号侧失效，非临时故障）。
OMP `models.yml` 摘除 `deepseek-v4-flash-0731`、`hy3` 两条目
（备份 `models.yml.20260815-remove-dead-168661.bak`）；NewAPI ch78/79 保持 status=2
留恢复点（用户续 key 后可回）。

## 4. Guardian 心跳 WinError 5 重试修复

**问题**：`_write_heartbeat()` 的 `os.replace` 间歇 `拒绝访问`（疑似 360/索引锁 tmp），
全天 17 次 ERROR 噪音；心跳虽保持新鲜，但存在 watchdog 误杀窗口。

**修复**（运行时 + 仓库镜像同步，备份 `guardian.py.bak-20260815-heartbeat-retry`）：
唯一 tmp 名（`heartbeat.json.<pid>.<attempt>.tmp`）+ 0.2s 递增退避 ×3，全部失败才记日志，
保持"绝不抛出打断自愈"契约。新增重试路径测试（瞬时失败→第二次成功→零错误日志）。

**验证**：`test_guardian.py` 141/141 OK；kill 后 watchdog 经计划任务拉起新实例；
新实例日志零心跳错误。

## 5. 日志排查结论（NewAPI DB + oneapi log + guardian.log + OMP logs）

- **主链路健康**：近 1h claude-opus-5（ch76，528 次全成功）、k3（ch33，缓存命中 ~99%）、
  deepseek-v4-flash（ch48）全部 200；OMP 日志无模型层错误。
- **P1 opus-5 单点**：亲和规则 "claude trace" 钉死 ch76（sotamodel），ch75/ch57 已禁；
  ch76 实测健康（2.8s），guardian `MIN_ENABLED_CRITICAL_MODELS` 覆盖 0 渠道告警。
  结构性风险待新源（7758/anyrouter 恢复后评估）。
- **P2 ch82 (7758) 保持禁用**：上游仍 503（7758 自身池 "No available channel...group Free"）。
  status=2 为手动语义，禁用者非 guardian（仅 1/3 soft failure）非 auto_ban（应 status=3），
  来源未明待用户确认。fork `PUT /api/channel` `Invalid parameters` 约束复踩
  （08-14 已记录：走 DB 直改 + `/api/channel/fix`）。
- **P3 ch31 (aliyun-qwen38) 周配额耗尽**：全天 429（1-week quota），周重置自愈；
  guardian 已限流跳过不再白探测。维持现状，充值可加速。
- **P4 ch77 (dots-note3) 429**：vision 兜底链首限流，链内自愈掉 agnes-2.5-flash，无需动。

## 6. anyrouter 窗口调查（余额消耗）

- 8789 指纹桥工作正常（上游错误均带 request id），故障全在上游侧：
  Claude 全模型 429（opus/sonnet/haiku 同池）、gpt-5.6-sol 500 "负载已经达到上限"（×5/75s）、
  gemini-2.5-pro 无可用协议路径（messages 404 / chat 500 空 body）。
  **本次窗口未找到成功路径**；ch72 保持禁用正确。
- 社区方案评估：签到脚本 `millylee/anyrouter-check-in`（1.3k star，GitHub Actions 每 6h、
  $25/天/账号，支持 anyrouter+agentrouter）只续额度**不解 429**；源码审查未见外传，
  凭据走 GitHub Environment secrets。⚠️ 勿用 `shindouhiro/anyrouter` fork（仓库内提交 `.env`）。
- ~~未部署任何自动探活（需用户逐条授权）~~ → 当晚已获授权并部署，见下条。
- **窗口哨兵已部署（20:23，用户授权）**：`~/.omp/guardian/anyrouter-window-canary.py`
  （镜像 `scripts/ops/`），计划任务 `AnyRouter Window Canary` 每 30min 一次性触发，
  探测 8789 桥 `claude-haiku-4-5-20251001`（16 tokens，单次 <$0.001，429 不计费），
  仅 closed→open 跳变发 Telegram；开窗后按门禁约束人工显式选用
  `anyrouter/claude-opus-5` / `anyrouter/claude-opus-4-8`。
- 当晚会战复测（~20:18）：Claude 全池仍 429、gpt-5.6-sol 仍 500、
  gemini-2.5-pro/gpt-5-codex 404 "当前 API 不支持所选模型"；余额接口被 WAF
  JS 挑战拦截（非 401），无法核查；429 body 为上游过载转发而非配额语义。
  首跑实测：exit 0、state=closed、无误报。

## 7. 0v0.club key 探测（用户提供）

`/v1/models` 仅 `glm-5.3`、`glm-5.2-fast`；两模型 chat 均 400 "该模型仅限 Linux Do 用户使用"。
key 有效但无消费权限，**未接入**任何配置。

## 8. 备份清单

- `~/.kimi-code/proxies/agentrouter-proxy/agentrouter-proxy.py.bak-20260815-quota-retry`
- `~/.omp/agent/config.yml.20260815-bigctx-slow-chain.bak`
- `~/.omp/agent/models.yml.20260815-remove-dead-168661.bak`
- `~/.omp/guardian/guardian.py.bak-20260815-heartbeat-retry`
- `scripts/ops/guardian.py.bak-20260815-heartbeat-retry`（仓库镜像）

## 9. 安全挂账

- 本会话 models.yml 明文 key 经读取出现在传输中，按泄露处理：建议轮换 OMP `zg-newapi` 与
  `agentrouter` 两个 provider key，及 0v0 key（若不使用则吊销）。

## 10. 闲置模型上岗（2026-08-15 晚）

**商汤 sensenova-6.7-flash-lite**：
- 实测为 reasoning 模型（`reasoning` 字段），`models.yml` 补 `reasoning: true`
  （无标记时 OMP 侧 content 解析为空）。
- 时延实测：足量 max_tokens 下琐碎 prompt ~3s；max_tokens 过小会被思考吃光致 content 空
  （64/1024 均不够，≥4096 正常）。`enable_thinking=false` / `reasoning_effort=low` 上游均忽略。
- 上岗位置（全链尾，平时零流量）：`zg-newapi/deepseek-v4-flash` 链（tiny）、
  `zg-newapi/claude-haiku-4-5` 链（commit）、`smol` 角色链——免费池 429 时的吸收层。

**其他闲置模型审计 + 上岗**（canary 全过，全部链尾）：

| 模型 | canary | 上岗位置 |
|---|---|---|
| mercury-2 (ch61) | 200 / 539ms | `smol` 链尾 |
| agnes-2.5-pro-alpha (ch68/69) | 200 / 3.2s | `vision` 链尾（image 已声明） |
| intern-s2-preview (ch66/67) | 200 / 682ms | `plan` 链尾 |

**保持闲置（如实记录）**：
- `gpt-5.5`：全部 carrier abilities enabled=0（ch2/30/70 封禁、ch82 随 7758 池干），条目保留待恢复。
- `zai-glm-5-2`：ch81 配置与 ability 正常，**muyuan 自身 NewAPI 池对该模型无渠道**（503 distributor），自愈型。
- `qwen3.8-max`：周配额耗尽（§5-P3）。
- `kimi-for-coding`：contextPromotion 源模型，机制内在用。
- `gpt-5.6-sol`：reviewer/security-reviewer agents frontmatter 在用（agentrouter 路径）；
  OMP 链按 §2 门禁不进。

验证：route gate 33/33 OK；OMP 端到端 `sensenova-6.7-flash-lite` 正确返回。
备份：`models.yml.20260815-sensenova-reasoning.bak`、`config.yml.20260815-sensenova-chains.bak`。
**修复**（备份 `agentrouter-proxy.py.bak-20260815-quota-retry`）：

- 429 直接判 retryable（冷却换 key）；
- 402/403 **仅当响应体含额度关键词**（quota/exhausted/insufficient/budget/rate limit/额度/余额）
  才冷却换 key——认证类 403（invalid key）保持快速失败，不错误轮换放大；
- 500 "sensitive words detected"（上游内容过滤误杀）**不重试**，避免 NewAPI 预扣费放大。

**验证**：单元 9/9（含真实观测的 402 body、403 auth、500 敏感词）；kill 后 proxies-supervisor
30s 内自愈（新 PID）；直连 8788 `claude-opus-5`/`claude-opus-4-8`/`gpt-5.6-sol` 全部 200；
OMP 端到端 `agentrouter/claude-opus-5` 与 `agentrouter/claude-opus-4-8` 均返回预期 token
（后者 69s 偏慢，上游质量非配置问题）。

**ch45 未动**：NewAPI ch45 当前仅挂 3 个 sol 模型（08-14 孤儿清理的既定决策），
OMP 直连路径已通；恢复 ch45 Opus（含 Cursor `zg-agent-claude-opus-*` 别名）属路由策略
变更，未授权不执行。

