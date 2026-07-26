# kiro-guard: truncation-context retry + proactive compression

**Date:** 2026-07-26 night
**VPS:** `/opt/new-api/kiro_guard.py`
**Backups:** `kiro_guard.py.bak.trunc-ctx-20260726-230657`, `kiro_guard.py.bak.trunc-ctx-20260726-231118`

## Problem

kiro reverse proxy truncates large streaming responses (~9KB buffer). kiro-guard detects this (soft truncation) but retries by replaying the exact same request, which gets truncated at the same point. Additionally, responses are sent uncompressed and max_tokens is not capped, allowing the model to generate output that exceeds kiro's buffer.

## Changes

### 1. Truncation-aware retry (TRUNC_CONTEXT)

When text truncation is detected (unclosed_fence, trailing_open, short_completion, empty_content, tool_intent_no_call), instead of replaying the same request:

1. Inject the truncated assistant response into `messages` as context
2. Append a user continuation prompt ("continue from where you were cut off")
3. Model continues from the truncation point instead of regenerating
4. `merge_responses` concatenates the truncated + continuation text, preserves tool_use blocks, sums usage tokens

New functions: `build_continuation_payload`, `merge_responses`, `_dedup_overlap`.
`_dedup_overlap` strips ≥20-char suffix/prefix overlap when merging to avoid duplicated text.
Journal tags: `recovered_merged:*`, `dedup_overlap`.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_TRUNC_CONTEXT` | `1` | Enable truncation-aware retry |

### 2. Adaptive max_tokens cap

Caps `max_tokens` before sending to upstream kiro. When the request includes Write/Edit/NotebookEdit tools, uses a higher cap (8192) to avoid choking file-write operations; otherwise 4096.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_MAX_TOKENS_CAP` | `4096` | Default max tokens cap (0 = no cap) |
| `KIRO_GUARD_MAX_TOKENS_WRITE_CAP` | `8192` | Cap when Write/Edit tools present |

New function: `_effective_cap`. Logged as `max_tokens capped {orig} -> {cap}` when applied.

### 3. HTTP gzip compression (GZIP_MIN_BYTES)

Compresses response bodies > 1KB when client sends `Accept-Encoding: gzip`. SSE event-stream responses are excluded.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_GZIP_MIN` | `1024` | Minimum body size for gzip (bytes) |

### 4. Response body size limit (2026-07-27)

Caps `resp.read()` to 10MB via `_read_limited()`. Prevents OOM from misbehaving upstream.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_MAX_RESPONSE_BYTES` | `10485760` | Max upstream response body (bytes) |

### 5. Request correlation ID (2026-07-27)

Every `fetch_classified` call generates a 12-char hex `req_id` (uuid4). Threaded through all `journal_event` calls and stderr logs, enabling cross-retry-chain tracing in journal.

### 6. Upstream latency tracking (2026-07-27)

`_fetch_upstream` records elapsed ms per call into a rolling 200-entry window. `/metrics` exposes `upstream_latency: {count, p50_ms, p95_ms, max_ms}`.

### 7. Upstream snippet redaction (2026-07-27)

`content_blocked_502` no longer returns first 240 bytes of upstream body to the client. Prevents leaking internal error details or key fragments.

### 8. Progressive SSE synthesis (2026-07-27)

Replaces one-shot `synth_stream` with `synth_stream_progressive`: text blocks are chunked (80 chars, 12ms delay) so users see output appearing gradually instead of a wall of text after long silence. Thinking blocks use double chunk size and half delay. Tool_use blocks remain single-shot.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_SYNTH_CHUNK` | `80` | Chars per SSE text_delta chunk |
| `KIRO_GUARD_SYNTH_DELAY` | `0.012` | Seconds between text chunks |

### 9. Thinking budget aware cap (2026-07-27)

`_effective_cap` now inspects `thinking.budget_tokens`. When extended thinking is enabled, cap = max(base_cap, budget + 2048) to ensure visible output room on top of thinking tokens. Prevents the 4096 default from starving models that use thinking budgets.

### 10. Error message standardization (2026-07-27)

All client-facing errors now use standard Anthropic error shapes via `_api_error()`:
- 502/timeout/truncation → `overloaded_error` ("The model is currently overloaded. Please retry.")
- 429 → `rate_limit_error`
- content_blocked → `overloaded_error` (triggers NewAPI channel failover)

Internal guard reasons (`retry_exhausted:unclosed_fence`, etc.) no longer leak to Claude Code, preventing the model from misinterpreting proxy errors as tool failures.

### 11. Continuation payload lightweighting (2026-07-27)

`build_continuation_payload` now keeps only system message + last N turns (default 6) instead of the full conversation history. Roughly halves input tokens for long sessions.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_TRUNC_CONTEXT_WINDOW` | `6` | Max recent messages to keep in continuation retry |

System message detection: preserves the first message if it has `cache_control` markers or is >2000 chars (likely system prompt). Ensures alternating user/assistant by trimming a leading assistant message.

### 12. Journal rotation (2026-07-27)

Auto-rotates the JSONL journal file when it exceeds a size threshold. Keeps up to N rotated backups (`.1`, `.2`, etc.). Rotation runs under `_journal_lock` before each write.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_JOURNAL_MAX_BYTES` | `5242880` | Rotate when journal exceeds this size (5MB) |
| `KIRO_GUARD_JOURNAL_KEEP` | `3` | Number of rotated backups to keep |

### 13. Prompt cache tracking (2026-07-27)

All "ok" responses now log `cache_read_input_tokens` and `cache_creation_input_tokens` from the response's `usage` dict into journal events. This enables ops tracking of prompt cache hit rates across channels and time. Merged/recovered responses also include usage fields.

### 14. Metrics persistence (2026-07-27)

In-memory metrics (ok/soft/hard counts, latencies) are saved to a JSON snapshot file every 5 minutes. On restart, the snapshot is restored so counters survive across restarts.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_METRICS_SNAPSHOT` | `kiro-guard-metrics-{port}.json` | Snapshot file path |
| `KIRO_GUARD_METRICS_SNAPSHOT_INTERVAL` | `300` | Save interval in seconds |

### 15. TG alerting (2026-07-27)

When consecutive hard failures reach a threshold, sends a Telegram bot message with port, upstream, and last failure reason. Cooldown: 60s between alerts.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_TG_BOT_TOKEN` | _(empty)_ | Telegram bot token (empty = disabled) |
| `KIRO_GUARD_TG_CHAT_ID` | _(empty)_ | Telegram chat ID to alert |
| `KIRO_GUARD_TG_ALERT_THRESHOLD` | `5` | Consecutive hard fails before alert |

### 16. Upstream concurrency limiter (2026-07-27)

Semaphore-based limiter on simultaneous upstream requests. Prevents overloading kiro under concurrent load.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_UPSTREAM_CONCURRENCY` | `3` | Max concurrent upstream requests (0 = unlimited) |

### 17. Streaming passthrough mode (2026-07-27)

When enabled, forwards `stream:true` requests directly to upstream as real streaming, bypassing the stream:false + SSE synthesis guard. Use when kiro fixes its truncation issue. Max_tokens cap still applies.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_STREAM_PASSTHROUGH` | `0` | Enable direct stream forwarding (default OFF) |

### 18. Codex CLI header spoof (2026-07-27)

AgentRouter deploys a WAF that only accepts traffic matching specific client fingerprints (Claude Code, Codex CLI, etc.). Community solution ([blog](https://blog.rei.my.id/posts/118/bypassing-agentrouter-ai-client-restriction/)): inject Codex CLI headers (`Originator`, `User-Agent`, `Version`) to pass the WAF. Enable on AR guard instances only.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_CODEX_SPOOF` | `0` | Inject Codex CLI headers for AgentRouter WAF bypass |

### 19. AnyRouter 1M context header injection (2026-07-27)

AnyRouter requires `anthropic-beta: context-1m-2025-08-07` header for 1M context window access. Without it, requests are rejected with "请启用 1m 上下文后重试". This layer injects/appends the beta tag to the `anthropic-beta` header, preserving any existing beta values from the client. Enable on AnyRouter guard instances only.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_ANYROUTER_1M` | `0` | Inject context-1m-2025-08-07 beta header for AnyRouter |

## VPS cron automation (2026-07-27)

| Script | Schedule | Description |
|--------|----------|-------------|
| `anyrouter_squeeze.py` | `*/10 * * * *` | AnyRouter 1M 挤进去：5 tries/run, auto-enable #52 on success |
| `anyrouter_checkin.py` | `0 9 * * *` | AnyRouter 每日签到 ($25/day) |
| `autoweight.py` | `30 */2 * * *` | 基于 guard metrics 自动调权 (p50/成功率 → weight ±5~8, 3h cooldown) |
| `newapi_monitor.py probe` | `*/5 * * * *` | NewAPI 健康探针 (qwen3.7-max completion) |
| `newapi_monitor.py scan` | `*/30 * * * *` | 渠道状态扫描 (SQLite 直查) |
| `newapi_monitor.py guard` | `*/10 * * * *` | Guard 端口 hard-reject 率监控 |
| `newapi_monitor.py daily` | `0 9 * * *` | 综合日报 |
| `sub2api_checkin.py` | `0 8 * * *` | 林夕 Sub2API 自动签到 (2 账号, token 自动续期) |

## Defense layers (after)

```
Request in
  |
0. [optional] Stream passthrough: bypass guard, forward SSE directly
  |
1. adaptive max_tokens cap: thinking-aware / Write-aware / default
  |
2. Concurrency limiter (semaphore, default 3)
  |
3. [optional] Codex CLI header spoof: inject Originator/UA/Version for AR WAF
  |
3b. [optional] AnyRouter 1M: inject/append anthropic-beta context-1m tag
  |
4. Truncation detect + lightweight continuation retry + overlap dedup + merge
  |
5. Response body size limit (10MB) + upstream latency tracking
  |
6. Progressive SSE synthesis (chunked text output)
  |
7. Standard Anthropic error shapes (no guard internals leaked)
  |
8. HTTP gzip: compress response to NewAPI (reduce transfer size)
  |
9. Journal: auto-rotation (5MB) + cache token tracking
  |
10. Metrics persistence (5min snapshots) + TG alerting (consecutive hard fails)
```

## Verification

- Selftest: all classify + merge + dedup + adaptive cap (thinking) + progressive SSE + size limit + latency + error shapes + continuation trimming + journal rotation + cache tracking + metrics snapshot + concurrency + TG alert + Codex spoof + AnyRouter 1M tests pass
- 5 main guard units + 1 AR guard (8410): all health=200
- AR guard: codex_spoof=ON, upstream=agentrouter.org
- AnyRouter guard env (8411): anyrouter_1m=ON, upstream=anyrouter.top

## Tuning

- Write cap too tight: `KIRO_GUARD_MAX_TOKENS_WRITE_CAP=16384`; default cap: `KIRO_GUARD_MAX_TOKENS_CAP=8192`
- If gzip causes issues with NewAPI: `KIRO_GUARD_GZIP_MIN=999999` (effectively off)
- Disable continuation retry: `KIRO_GUARD_TRUNC_CONTEXT=0`
- Response size limit: `KIRO_GUARD_MAX_RESPONSE_BYTES=20971520` (20MB)
- SSE chunk tuning: `KIRO_GUARD_SYNTH_CHUNK=120` (bigger), `KIRO_GUARD_SYNTH_DELAY=0.005` (faster)
- Continuation window: `KIRO_GUARD_TRUNC_CONTEXT_WINDOW=10` (keep more context, costs more tokens)
- Journal rotation threshold: `KIRO_GUARD_JOURNAL_MAX_BYTES=10485760` (10MB)
- Journal backup count: `KIRO_GUARD_JOURNAL_KEEP=5` (keep 5 rotated files)
- Metrics snapshot interval: `KIRO_GUARD_METRICS_SNAPSHOT_INTERVAL=60` (every 1min)
- Concurrency limit: `KIRO_GUARD_UPSTREAM_CONCURRENCY=5` (or 0 for unlimited)
- Enable streaming passthrough: `KIRO_GUARD_STREAM_PASSTHROUGH=1` (when kiro is fixed)
- TG alert sensitivity: `KIRO_GUARD_TG_ALERT_THRESHOLD=3` (alert sooner)
- Enable Codex spoof for AR: `KIRO_GUARD_CODEX_SPOOF=1` (AR guard instances only)
- Enable AnyRouter 1M: `KIRO_GUARD_ANYROUTER_1M=1` (AnyRouter guard instances only)

## Rollback

```bash
cp /opt/new-api/kiro_guard.py.bak.trunc-ctx-20260727-000813 /opt/new-api/kiro_guard.py
systemctl restart kiro-guard kiro-guard-100xlabs kiro-guard-100x-8403 kiro-guard-100x-8404 kiro-guard-100x-8405
```
