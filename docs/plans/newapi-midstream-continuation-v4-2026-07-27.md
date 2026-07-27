# v4 立项：/v1/messages 流中途续传（Mid-stream Continuation）— 2026-07-27

## 要除的根

Claude Code「总是停止」的最终根因：上游（百倍等）SSE 流在回合中途 EOF，
网关把已收到的部分交给客户端后回合就结束了，用户只能手打「继续」。

已上线的缓解（治标）：渠道路由优化（route_optimizer）、auto-continue 客户端
监听器。本项是治本：让客户端**根本感知不到**中途断流。

## 已有证据与既有资产

- NewAPI 日志：`stream ended: reason=eof soft_errors=1, received=3`、
  `reason=handler_stop end_error=...` —— fork 已有软错误计数，但收到事件
  较少时才能透明重试；thinking 流出几分钟后断流则无法重试（客户端已见字）
- kiro_guard tee 模式（2026-07-27 上线）：SSE 边转发边缓冲 + 末尾校验 +
  中途 EOF 续写，TTFB 14s→3.4s。**模式已验证，可移植**

## 技术约束（先想清楚）

1. **thinking 块无法续写**：encrypted signature 不可伪造，EOF 发生在
   thinking 段中间时无法合法续。只能续纯 text 段
2. **tool_use 半块**：partial JSON 续写易错，v4.1 不处理，fail-soft
   （保留已收内容，交给客户端/auto-continue）
3. **续写方式**：Anthropic API 支持 assistant prefill——把已生成的 partial
   assistant 内容作为最后一条 assistant message 重新请求，模型接着写。
   缝合时去重前缀
4. **成本**：续写请求重发全部上下文，prompt cache 命中可控损；未命中则
   一次断流 ≈ 双倍 input 费用。缓存断点策略需评估
5. **计费**：guard 侧续写对 NewAPI 是第二笔请求，计费/日志要能对账
   （request_id 链）

## 架构选项

| 方案 | 做法 | 评 |
|---|---|---|
| A. fork Go 补丁 | relay 层 EOF 检测 + prefill 续写缝合 | 最透明，但改 fork 代码、升级维护负担大 |
| B. VPS 前置 guard（Python）| 复用 kiro_guard 骨架，client→guard:新端口→new-api | 不动 fork；需客户端改 endpoint 或端口对调 |
| C. 混合 | guard 只做「EOF 检测+续写」，其余直转 | 同 B，tee 模式经验直接套 |

**推荐 B/C**：先起 staging 端口（如 :3002）灰度，验证缝合质量后再谈切换；
期间客户端仍直连 :3000，零风险。

## 阶段计划

- **P0 调研（下一步）**：读 fork 容器内 relay 配置项（options 表 `%tream%`
  `%ontinuation%`），确认它是否已有可配的续传开关——有就直接用，不用造
- **P1 guard 骨架**：kiro_guard 移植，仅 tee+EOF 检测，先不续写，观测断流
  现场（位置分布：thinking/text/tool_use 各占多少）→ 决定续写器支持面
- **P2 text 段续写缝合**：prefill 续写 + 前缀去重 + 计费对账
- **P3 灰度切换**：staging 端口 → 对调 :3000，全量；回退=端口对调回去

## 验收标准

- 人为掐流测试（kill 上游连接）下客户端回合**不结束**，文本连续无重复
- 连续 3 天生产观测：客户端侧「停止」事件（jsonl 中未完成 end_turn）
  下降 ≥80%
- 成本回归：断流续写导致的额外 input 费用 < 5%/日

## 风险

- thinking 段断流占比若很高（P1 观测），续写覆盖率受限 → 价值打折，
  届时重估是否继续
- 缝合 bug 产生重复/错乱文本 → staging 期用 diff 校验器兜底

---

## P1 执行记录（2026-07-27 晚）：目标架构已存在，转向观测补强

**关键发现：方案 B/C 要造的 guard 已经在线。** Opus 主池渠道（#10/#20/#60/#11）
的 base_url 本来就指向 VPS 本机 kiro_guard 实例（8400/8404/8405，另 8403/8410-8412
服务其他上游），而 kiro_guard.py（2537 行）tee 模式已实现 v4 计划的全部核心：

- SSE 边转发边缓冲（`_SSEParser` + `_MessageAssembler`），TTFB 不牺牲
- 中途 EOF 检测：`stop_reason` 缺失即 `missing_stop_reason`，tee 分支判为可续
- text 段 prefill 续写缝合：`build_continuation_payload`（保留 system +
  窗口裁剪 + truncated assistant turn + 续写提示）+ `merge_responses` +
  `_dedup_overlap` 前缀去重 + 续写轮次索引重映射（`_IndexRemap`，跳过
  thinking 块、首 text 块缓冲去重）
- 假 end_turn 启发式（`short_completion`/`unclosed_fence`/`trailing_open`/
  `tool_intent_no_call`）+ tool_use 半块检测（`empty_tool_input`）
- journal 观测（`/opt/new-api/kiro-guard-soft.jsonl`）+ TG 硬告警

**原计划新建 claude_upstream_guard.vps.py 已废弃**（纯重复造轮子，未部署）。

**生产 journal 统计（全量，~1300 事件）：**

- `hard:upstream_http_503` 497 —— 绝对大头是上游过载，由渠道故障路由兜底，
  非断流问题
- `soft:short_completion` 124（recovered 28 + merged 21 + exhausted 34）——
  假 end_turn 是截断主因，续写器在生产中正常工作（今日多起
  `tee_retry_ok_merged`）
- `hard:missing_stop_reason` 仅 6 + 少量 `tee_eof` —— **真正的中流 EOF 很
  罕见**，立项时担心的「thinking 段断流占比」问题基本不成立
- `content_blocked:marker:sensitive_words_detected` 24 —— 内容审核拦截，
  走 failover

**P1 观测补强（已上线）**：journal 的 tee EOF/hard 事件此前不记录断流位置。
已给 kiro_guard.py 打增量补丁：`tee_eof:*` 与 `hard phase=tee` 事件新增
`last_block` 字段（thinking/text/tool_use/none），备份
`kiro_guard.py.bak.lastblock`，8 个运行实例已重启加载。观察 1-2 天后据此定
P2 是否还需要做（当前证据：text 段续写已覆盖主要截断场景）。

**P2 状态**：核心能力（text 段续写缝合）**已在生产**，原 P2 范围只剩
「计费对账」和视 last_block 观测结果而定的 thinking 段策略。P3 灰度切换
不适用——guard 本来就在链路上。

剩余真正缺口（与 v4 无关，沿用既有清单）：P2-11/13/14、P2-12 残余、
dx cooldown / health alerted 待 cron 周期验证。
