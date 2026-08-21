# 3003 网关乱序 SSE 告警排查（2026-08-21）

症状：OMP 日志 warn `anthropic: ignoring malformed stream envelope:
received content_block_stop for unopened index 0`，2026-08-21 全天 161 次。

## 定性：网关无罪，是 justwoker opus 空响应轮的可计数症状

1. **网关纯透传**：`~/.omp/guardian/omp-ttft-gateway.cjs`（3003）只在首个
   语义事件前缓冲、之后逐字节转发，不生成/重排任何 SSE 事件。乱序信封
   由上游（NewAPI 3002 的转换层）产生。
2. **渠道归因**（161 个告警时间戳 ±2s 与 NewAPI 消费日志关联）：
   `claude-opus-5`（justwoker ch94/95 + ch86）123 次、
   `omp-sota-claude-opus-5`（ch93）49 次——全部指向 justwoker opus 服务线。
3. **机制**：上游流结束但从未产出任何内容块时，NewAPI 转换层仍合成
   `content_block_stop(0)`——无对应 start，OMP 忽略并正常收尾。
   **每一次告警 ≈ 一次空回复/零输出轮次**，与 justwoker runbook 已记录的
   "ch86 大 prompt 挂起 / 120s 零输出"同族
   （`justwoker-opus-channels-2026-08-20.md`）。
4. **正常请求复现不了**：3 发流式探针（claude-opus-5 via 3003）帧序全正常，
   乱序只在异常流出现。

## 结论与口径

- 信封本身**无害**（OMP 容忍忽略），别再当网关 bug 查；它是
  justwoker 空转轮的计数器。
- 影响在业务侧：一天 161 次空轮 = 用户体感的"opus 渠道好久没发请求/
  空响应"。
- 治本在 justwoker 上游；new-api 转换层"不应合成孤儿 stop"属低优先级
  噪音问题，可选提上游 issue。

## 待办

- [x] Guardian 监控口径：把 `claude-opus-5` / `omp-sota-claude-opus-5` 的
  空响应率做成可告警指标（超阈值告警），替代体感。已在 `scripts/ops/guardian.py`
  落地，实现口径：最近 6 小时、prompt_tokens>=1000 的样本数 >=30 时，空轮率
  >20% 触发 Telegram 告警；告警复用 `AlertManager` warning 级冷却，避免重复刷屏。
  新增 4 项单元测试（`scripts/ops/test_guardian.py` 共 171 项，全绿）覆盖样本不足、
  正常率、超阈值告警与冷却去重。

## 排查方法备查

OMP warn 无模型字段，用时间戳 ±2s 关联 NewAPI `logs` 表
（`type=2` 消费行）归因渠道；网关复现探针：对 3003 `/v1/messages`
发流式请求逐帧跟踪 `content_block_start/stop` 的 index 配对。
