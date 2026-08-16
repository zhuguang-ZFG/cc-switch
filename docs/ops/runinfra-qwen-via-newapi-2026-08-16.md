# runinfra qwen3-8-27b via NewAPI + param_override strip (2026-08-16)

## Scope

`qwen3-8-27b` (runinfra hosted inference, `https://api.runinfra.ai`) is usable
from OMP as `zg-newapi/qwen3-8-27b`, routed through NewAPI channel **ch88**
`runinfra-qwen3-8-27b`. A direct OMP provider entry was tried first and
removed the same day — see "Why not direct".

## Why not direct

OMP's chat/completions transport always injects `prompt_cache_key`
(pi-ai `applyOpenAIChatCompletionsPromptCachePolicy`; gated only by the
request-level `cacheRetention:"none"` option). runinfra hosted inference
hard-400s that field (`hosted_parameter_not_supported`). The models.yml schema
(`models-config-schema-bundle.ts` `ModelDefinitionSchema` + compat flags) has
**no per-provider suppressor**, so a direct provider entry cannot work.

NewAPI relay alone does **not** save it either: the fork passes
`prompt_cache_key` through to the upstream unchanged (verified: E2E via
ch88 without override → same 400).

## Fix

ch88 `param_override` (fork's documented JSON Patch-style operations, see
`docs/patches/newapi-dx-zhipu-stop-2026-07-26.md`):

```json
{"operations": [{"path": "prompt_cache_key", "mode": "delete"}]}
```

The field is deleted before upstream; everything else (stream,
`stream_options`, `enable_thinking`, `max_completion_tokens`) passes through
and is accepted by runinfra.

OMP side: model entry under the existing `zg-newapi` provider in
`~/.omp/agent/models.yml` (`reasoning: true`, contextWindow 262144,
maxTokens 32768 — from `/v1/models` metadata). No separate provider, no
fallback chain entries.

## Pitfalls hit during bring-up

1. **PUT /api/channel/ wipes the key if you round-trip the GET row** — GET
   returns `key: ""` (redacted); PUT-ing that row back stores the empty key.
   Always re-supply the real key on PUT. Recovery: PUT again with the key.
2. PUT body must not contain `status` (fork rejects with "Invalid
   parameters"); use the dedicated `POST /api/channel/{id}/status` for that.
3. Verification with plain curl is insufficient for OMP-hosted providers —
   the request must match OMP's wire shape (`prompt_cache_key`,
   `stream_options`, `enable_thinking`, `max_completion_tokens`, stream).

## Verification evidence (2026-08-16)

- ch88 created (type=1, base `https://api.runinfra.ai`, no `/v1`), abilities
  row enabled; DB snapshot `new-api-before-runinfra-qwen3-8-27b-20260816-214052.db`.
- Admin channel test ch88: success (1.0s).
- E2E via `127.0.0.1:3002` non-stream + `prompt_cache_key`: 200, reasoning
  content returned.
- E2E full OMP wire shape (stream + `stream_options` + `enable_thinking` +
  `max_completion_tokens` + `prompt_cache_key`): 200 SSE, 22 chunks, usage
  chunk, clean `[DONE]`.
- Consume log: both E2E rows recorded with `channel_id=88`.

## Rollback

```powershell
POST /api/channel/88/status {"status": 2}   # disable channel
# remove the qwen3-8-27b entry from ~/.omp/agent/models.yml (zg-newapi)
```

Creation helper: `scripts/ops/add_runinfra_qwen_channel.py` (idempotent;
key via argv). The helper includes `param_override` in the create payload
and verifies it on readback (JSON-normalized compare), so a fresh create
is usable immediately. Verify-only re-runs compare the full contract:
base_url, models, type, status, priority, weight, group, param_override,
and exact abilities rows. (The initial ch88 create predated the fix; the
override was applied via API and the script now guards it.)
