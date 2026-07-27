# kiro_guard tee 流式模式（2026-07-27）

> 状态：已部署 8 个 guard 单元（滚动重启全 active），selftest + 假上游 e2e + 线上冒烟通过。

## 动机

缓冲模式下 guard 把上游 `stream` 改写为 `false`，整段收完、classify 后才用
`synth_stream_progressive` 合成 SSE——TTFT ≈ 整段生成时间（实测长回答 ~14s）。
上游实际支持流式（TTFB ~3-7s）。

## tee 模式行为（`KIRO_GUARD_TEE`，默认 1）

- 客户端 `stream:true` → `_stream_tee`：上游改回 `stream:true`，SSE 逐事件**实时转发**
  （`_SSEParser` 增量解析 + 原样字节 relay），同时 `_MessageAssembler` 重建完整 message。
- 终止事件（带 stop_reason 的 `message_delta` / `message_stop`）先扣住；流结束后对重建
  msg 跑 `classify_response`：
  - ok → 补发扣住的终止事件；
  - 文本截断类 soft → 流式 continuation（`build_continuation_payload`，次数=SOFT_RETRY，
    新一轮跳过 message_start/thinking，首个 text block 缓冲 ≥600 字后 `_dedup_overlap`
    去重再发，index 经 `_IndexRemap` 稠密重映射，usage 合并 output 求和）；
  - hard / 断流 → SSE error 事件（200 已发，无法改状态）。
- **上游非 200 时客户端尚未收到任何字节 → 透传真实状态码**（401/403 不再被包成
  200+SSE error，NewAPI 可正常判死渠道——P2-13 在 tee 路径根治；缓冲慢路径同步细分
  `authentication_error`/`permission_error`）。
- 总 deadline = TIMEOUT×(1+SOFT_RETRY)+backoff；累计字节超 MAX_RESPONSE_BYTES 即断。

实测（:8404 直测）：TTFB 3.4s、事件流实时到达、stop_reason/usage 正常。
CYRILLIC_BYPASS 实例（AR）自动回落缓冲路径（原样转发无法对混淆流做 decode）。

## 同批修复

- **P2-11**：`KIRO_GUARD_MAX_ACTIVE_REQUESTS`（默认 16），`BoundedSemaphore` 非阻塞
  acquire，超限立即 503（overloaded_error），覆盖 tee/缓冲/passthrough 三路径；
  /metrics 新增 `active_requests`/`max_active_requests`/`tee`。
- **P2-12 残余**：缓冲路径 `fetch_classified` 加 `acc_msg` 累积式 continuation
  （第 N 轮基于已 merge 全历史；SOFT_RETRY=1 行为不变）；tee 路径天然累积。
- **P2-14**：cyrillic decode 改上下文正则 `(?<=[A-Za-z0-9])с(?=[A-Za-z0-9])`，
  真俄语文本不再被污染；encode 侧不变。

## 运维

- 回退：`KIRO_GUARD_TEE=0`（逐实例 env）即回缓冲模式，无需改代码。
- 新 journal phase：`tee_first`/`tee_round_N`/`tee_retry_ok_merged`/`tee_exhausted`/`tee_cont`。
- selftest 新增 ~20 断言（parser+assembler roundtrip、截断判定、index remap、
  俄语保留、累积 merge 链、active 信号量）。

## 追加：上游中途断流续写（2026-07-27 晚）

诱因：当日 15:08-15:24 百倍池风暴（CF 524 origin timeout / 503 / 429 并发限流），
Claude Code 报「Connection closed mid-response」。tee 下 524 表现为 200 后 SSE
流中途 EOF（无 message_stop），原先判 hard → sse error。

现 `_stream_tee` 对 `missing_stop_reason` 且已有部分文本的情况按截断处理——
部分文本 merge 进 acc 后走 continuation 续写（同样受 SOFT_RETRY 限制），
客户端无感。journal phase：`tee_eof:missing_stop_reason`。
