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

## Defense layers (after)

```
Request in
  |
1. adaptive max_tokens cap: thinking-aware / Write-aware / default
  |
2. Truncation detect + lightweight continuation retry + overlap dedup + merge
  |
3. Response body size limit (10MB) + upstream latency tracking
  |
4. Progressive SSE synthesis (chunked text output)
  |
5. Standard Anthropic error shapes (no guard internals leaked)
  |
6. HTTP gzip: compress response to NewAPI (reduce transfer size)
```

## Verification

- Selftest: all classify + merge + dedup + adaptive cap (thinking) + progressive SSE + size limit + latency + error shapes + continuation trimming tests pass
- 5 guard units: all active, all health=200

## Tuning

- Write cap too tight: `KIRO_GUARD_MAX_TOKENS_WRITE_CAP=16384`; default cap: `KIRO_GUARD_MAX_TOKENS_CAP=8192`
- If gzip causes issues with NewAPI: `KIRO_GUARD_GZIP_MIN=999999` (effectively off)
- Disable continuation retry: `KIRO_GUARD_TRUNC_CONTEXT=0`
- Response size limit: `KIRO_GUARD_MAX_RESPONSE_BYTES=20971520` (20MB)
- SSE chunk tuning: `KIRO_GUARD_SYNTH_CHUNK=120` (bigger), `KIRO_GUARD_SYNTH_DELAY=0.005` (faster)
- Continuation window: `KIRO_GUARD_TRUNC_CONTEXT_WINDOW=10` (keep more context, costs more tokens)

## Rollback

```bash
cp /opt/new-api/kiro_guard.py.bak.trunc-ctx-20260727-000813 /opt/new-api/kiro_guard.py
systemctl restart kiro-guard kiro-guard-100xlabs kiro-guard-100x-8403 kiro-guard-100x-8404 kiro-guard-100x-8405
```
