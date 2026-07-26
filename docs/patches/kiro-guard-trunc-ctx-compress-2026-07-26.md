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

New functions: `build_continuation_payload`, `merge_responses`.
Journal tag: `recovered_merged:*`.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_TRUNC_CONTEXT` | `1` | Enable truncation-aware retry |

### 2. Proactive max_tokens cap (MAX_TOKENS_CAP)

Caps `max_tokens` before sending to upstream kiro. Reduces the probability of generating output that exceeds kiro's buffer limit.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_MAX_TOKENS_CAP` | `4096` | Max tokens cap (0 = no cap) |

Logged as `max_tokens capped {orig} -> {cap}` when applied.

### 3. HTTP gzip compression (GZIP_MIN_BYTES)

Compresses response bodies > 1KB when client sends `Accept-Encoding: gzip`. SSE event-stream responses are excluded.

| Env var | Default | Description |
|---------|---------|-------------|
| `KIRO_GUARD_GZIP_MIN` | `1024` | Minimum body size for gzip (bytes) |

## Defense layers (after)

```
Request in
  |
1. max_tokens cap: 4096 (prevent overly long output)
  |
2. Truncation detect + continuation retry + merge (recover if still truncated)
  |
3. HTTP gzip: compress response to NewAPI (reduce transfer size)
```

## Verification

- Selftest: all existing + new `build_continuation_payload` + `merge_responses` tests pass
- 5 guard units: all active, all health=200
- `/metrics` shows `trunc_context=True`, `max_tokens_cap=4096`, `gzip_min_bytes=1024`

## Tuning

- If 4096 cap is too tight for long file writes: `KIRO_GUARD_MAX_TOKENS_CAP=8192`
- If gzip causes issues with NewAPI: `KIRO_GUARD_GZIP_MIN=999999` (effectively off)
- Disable continuation retry: `KIRO_GUARD_TRUNC_CONTEXT=0`

## Rollback

```bash
cp /opt/new-api/kiro_guard.py.bak.trunc-ctx-20260726-230657 /opt/new-api/kiro_guard.py
systemctl restart kiro-guard kiro-guard-100xlabs kiro-guard-100x-8403 kiro-guard-100x-8404 kiro-guard-100x-8405
```
