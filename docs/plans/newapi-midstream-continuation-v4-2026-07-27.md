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
