# Zhipu GLM `stop` string 400 fix (2026-07-26)

**Host:** Aliyun NewAPI `47.112.162.80`  
**Backup:** `/opt/new-api/backups/one-api.before-zhipu-stop-fix-20260726-133145.db`

## Symptom

Claude Code → NewAPI `/v1/messages` → zhipu `#41/#42` returned **400**:

```text
ChatCompletionRequest["stop"]: Cannot construct instance of ArrayList
from String value ('</block>')
```

## Root cause

1. Claude sends `stop_sequences: ["</block>"]` (often length 1).
2. NewAPI `service/convert.go` converts Claude→OpenAI with:
   - 1 sequence → `stop` **string**
   - 2+ sequences → `stop` **array**
3. Zhipu coding API (`open.bigmodel.cn` via `/zhipu-coding` nginx shim) types `stop` as `ArrayList` only → rejects the string form.

## Fix

`channels.param_override` on **`#41` and `#42`**:

```json
{
  "enable_thinking": false,
  "operations": [{"path": "stop", "mode": "delete"}]
}
```

- Keeps existing `enable_thinking: false` (legacy merge + operations).
- Deletes `stop` before upstream — Claude’s `</block>` is irrelevant on the OpenAI-function tool path used for GLM.
- **Requires `podman restart new-api`** — param_override is cached in-process; DB write alone does not apply.

## Verify

```text
POST /v1/messages  model=glm-5.2|glm-5.2[1M]
stop_sequences=["</block>"]  max_tokens=32..64
→ 200, non-empty text (e.g. OK)
```

Before restart: same body → 400 ArrayList. After: 200.

## Short-completion triage (same window)

24h `pt≥1500 & ct≤80` ≈210 rows:

| Bucket | n | Reading |
|--------|---|---------|
| ultra-short `ct≤15` (toolish) | ~123 | Mostly normal tool/ack turns — **not** outages |
| text tiny `16–80` | ~73 | Mix of brief replies + possible soft trunc |
| `stream_error` / client_gone | ~13 | Client disconnect |
| soft `short_completion` recover | 17 recovered / 9 exhausted (tail) | Real soft trunc exists; **within** DX band — no SHORT_OUT change |

**Action:** none on soft thresholds; keep watching exhausted rate. Prefer Opus 5 over 4.8.

## Related

- Thinking-off origin: `docs/patches/newapi-dx-2026-07-25.md`
- Routing: `docs/ops/zg-claude-routing.md`
- DX runbook: `docs/ops/newapi-dx-cursor-ops.md`
