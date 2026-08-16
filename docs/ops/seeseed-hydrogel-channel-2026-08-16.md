# seeseed1ck hydrogel channel (2026-08-16)

## Scope

The hydrogel gateway (`https://api-yi-hydrogel.seeseed1ck.icu`,
OpenAI-compatible) was aggregated into NewAPI as channel **ch89**
`seeseed1ck-hydrogel`. Only models verified live at creation time were
included; the gateway's dead/saturated models were deliberately excluded.

## Gateway liveness sweep (direct, 2026-08-16 ~23:15)

| Model | Result | Included |
|---|---|---|
| GLM-5.3 | 200 stream | yes |
| grok-4.6 | 200 stream | yes |
| grok-chat-fast | 200 | yes |
| mimo-v2.5 | 200 stream | yes |
| GLM-5.2 | 429 `concurrent limit exceeded: running=6 max=6` (saturated shared account) | no |
| deepseek-v4-pro / -v4-flash | 502 / upstream `do_request_failed` | no |
| glm-5.2 (lowercase) | 502 | no |
| kimi-k2.6, qwen3.7-max/plus, qwen3.8-max | 502 | no |
| grok-4.5, gpt-oss-120b | upstream `do_request_failed` | no |
| longcat-2.0-free | `无可用渠道（distributor）` | no |

Rationale for exclusion: dead models would fail Guardian channel tests and
risk auto-disabling the whole channel; saturated GLM-5.2 has the same flap
risk. Re-sweep before adding any of them later.

## Channel parameters

- type=1 (OpenAI), base_url=`https://api-yi-hydrogel.seeseed1ck.icu` — no
  `/v1` suffix (NewAPI appends `/v1/chat/completions` itself)
- models: `GLM-5.3,grok-4.6,grok-chat-fast,mimo-v2.5`
- **param_override**: `{"operations":[{"path":"enable_thinking","mode":"delete"}]}`
  — GLM-5.3 is always-thinking and 400s on `enable_thinking:false`
  ("该模型始终思考，不支持关闭思考"). OMP's wire shape includes
  `enable_thinking`, so the field is stripped for the whole channel; server
  defaults then apply (grok/mimo accept the field but default fine without it).
- `prompt_cache_key` is tolerated upstream (GLM-5.3/grok-4.6 streamed 200 with
  it) — no delete op needed, unlike runinfra ch88.
- priority 0, weight 0, group `default`, status 1. No other channel serves
  these models, so priority is neutral until same-model backups appear.
- Real key passed via argv at creation time; not stored in this repo.
- Creation helper: `scripts/ops/add_seeseed_hydrogel_channel.py` — idempotent;
  re-run performs dup-check + readback verification only.

## Verification evidence (2026-08-16)

- Direct upstream wire-shape probes (stream + `stream_options` +
  `prompt_cache_key` + `enable_thinking`): GLM-5.3 200 SSE (only after
  dropping `enable_thinking` → motivated the override), grok-4.6 200 SSE,
  mimo-v2.5 200 SSE.
- Create readback: ch89 type=1 status=1; abilities rows for all four models
  enabled; param_override readback JSON-matches.
- E2E via `127.0.0.1:3002` with client key, full OMP wire shape:
  - grok-4.6: 200 SSE, clean `[DONE]`, usage present (23:20).
  - GLM-5.3: first attempt hit a transient token TPM window
    ("Model tpm limit exceeded" — NewAPI-side limiter, not upstream); retry
    after 45s → 200 SSE, clean `[DONE]` (23:21). Proves the param_override
    strips `enable_thinking` correctly (otherwise 400 always-thinking error).
- Consume log: `logs` row model `GLM-5.3` `channel_id=89` at 23:21:07.

## Rollback

```powershell
# disable the channel (Guardian/SQLite SSOT remains consistent)
POST /api/channel/89/status {"status": 2}
```

DB snapshot before channel creation:
`~/.new-api-local/backups/new-api-before-seeseed1ck-hydrogel-20260816-231938.db`.

## Follow-ups

- If GLM-5.2's concurrency frees up, consider adding it plus a
  `zai-glm-5-2`→`GLM-5.2` model_mapping to plug this gateway into the glm
  chain as a backup for ch85 (see
  `docs/ops/mistral-glm-channel-2026-08-16.md`).
- If deepseek-v4-pro/flash recover upstream, they would become a fourth
  backup for the k3 fallback chain (current live pool: ch48 prio 51,
  ch42 prio 50, ch84 prio 40 — see `docs/ops/deepseek-v4-pro-pool-2026-08-13.md`).

## Related

- `docs/ops/ooioo-gpt56sol-channel-2026-08-16.md` — ch87 runbook whose
  workflow contract this channel followed.
- `docs/ops/runinfra-qwen-via-newapi-2026-08-16.md` — ch88 runbook; the
  param_override delete-op pattern reused here.
- `docs/ops/provider-prompt-cache-key-sweep-2026-08-16.md` — wire-shape
  tolerance methodology.
