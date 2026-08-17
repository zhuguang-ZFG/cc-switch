# jianzhile gpt-5.6-sol fifth-line channel (2026-08-17)

> Current posture (2026-08-17 19:50 CST): ch91 is now the p50/w5 direct
> backup to ch92 zzzcoding p60/w15. The tables below preserve the original
> admission-time history. See `zzzcoding-gpt56sol-primary-2026-08-17.md`.

## Scope

The jianzhile gateway (`https://jianzhile.vip`, a NewAPI fork relay exposing
exactly one model, `gpt-5.6-sol`) was aggregated into NewAPI as channel
**ch91** `jianzhile-gpt-5.6-sol` — a **2-key polling** channel. It went in
single-key at 12:20 and was converted to multi-key at 12:37 the same day
when a second key arrived (delete + `multi_to_single` recreate, id
preserved at 91).

This is a re-admission: the same gateway was **refused on 2026-08-13**
(`jianzhile-channel-2026-08-13.md`) because `gpt-5.6-sol` deterministically
403'd on every endpoint. With the new key the 2026-08-17 admission probe
passed both gates (`/v1/models` 200, `/v1/chat/completions` 200), satisfying
the "direct 200 required" threshold from the seekai/sotamodel precedent.

Priority ladder after this change:

| Channel | Priority | Role |
|---|---|---|
| ch83 muyuan-sol | 50 | primary (degraded — see sol-chain-muyuan-degradation) |
| ch45 agentrouter | 40 | first backup |
| ch87 ooioo | 30 | second backup |
| ch90 t1qq | 20 | last resort |
| **ch91 jianzhile** | **10** | **fifth line (new)** |

## Channel parameters

- type=1 (OpenAI), base_url=`https://jianzhile.vip` — no `/v1` suffix
  (NewAPI appends the request-specific path, including `/v1/responses`)
- models: `gpt-5.6-sol,zg-gpt-5.6-sol,zg-agent-gpt-5.6-sol,jianzhile-codex-gpt-5.6-sol`
- model_mapping: `zg-gpt-5.6-sol`/`zg-agent-gpt-5.6-sol` → `gpt-5.6-sol`
  (same alias set as ch83/ch45/ch87/ch90)
- `jianzhile-codex-gpt-5.6-sol` → `gpt-5.6-sol` is the isolated
  Responses-only E2E alias.
- **2 keys, multi-key polling** (keys passed via argv; not stored in repo)
- priority 10, weight 5, group `default`, status 1, `auto_ban=1`
- `test_model=jianzhile-codex-gpt-5.6-sol`, so a channel test resolves the
  Responses-compatible alias instead of the generic Chat model. The upstream
  still requires `stream=true`; Guardian supplies the explicit endpoint and
  stream query parameters.
- `header_override` passes safe incoming headers with `"*"` and explicitly
  pins the verified Codex 0.147 fallback envelope. A conditional
  `param_override` passes dynamic Codex headers when the request is from
  `codex_exec`; OMP requests use the deterministic fallback values.
  `Accept` is deliberately not pinned: fixing it to `text/event-stream`
  makes a non-stream Responses probe receive SSE and fail JSON parsing.
- NewAPI `global.chat_completions_to_responses_policy` includes ch91 and the
  four sol aliases. This converts OMP's normal Chat Completions request to
  `/v1/responses` only after the router selects ch91.
- idempotent; re-run performs dup-check + readback verification only

## Verification evidence (2026-08-17)

- Upstream direct probe `GET /v1/models`: HTTP 200, 2.6s, exactly one model
  `gpt-5.6-sol` (matches the 2026-08-13 observation; relay still a NewAPI
  fork).
- Upstream direct probe `POST /v1/chat/completions` model `gpt-5.6-sol`
  (`max_tokens 16`): HTTP 200, 3.5s, normal completion — the 2026-08-13
  deterministic 403 is gone.
- Create readback: ch91 type=1 status=1 priority=10 weight=5; all four
  ability rows enabled at 10/5.
- NewAPI's default non-stream admin test is not a valid health probe: after
  reaching Responses it receives an SSE body and reports a 500 JSON parse
  error. Explicit `endpoint_type=openai-response&stream=true` succeeds
  (2.497s). A manual test of `gpt-5.6-sol` with the endpoint left blank instead
  exercises Chat Completions and can return the upstream 403.
- Direct OMP-shaped Chat Completions through `127.0.0.1:3002` returned HTTP
  200 SSE (`OMP - CHAT - OK`) after channel-local conversion.
- Forced OMP command `zg-newapi/jianzhile-codex-gpt-5.6-sol` returned
  `OMP-CH91-STREAM-OK`; NewAPI log 84676 records ch91, the dedicated alias,
  38,914 prompt tokens, 12 completion tokens, and 11s completion time. The
  normal aggregate
  `zg-newapi/gpt-5.6-sol` also returned `OMP-AGG-OK`, but correctly selected
  higher-priority ch87 in that run, so it is not evidence that every aggregate
  request uses ch91.
- The deployed Guardian Responses+stream profile passed 3/3 bounded probes
  after one earlier transient `do request failed`; the transient is retained
  as upstream stability evidence, not hidden as protocol success.
- Idempotent re-run of the creation script: verify-only path (dup-check by
  name short-circuits to readback).

### Multi-key conversion (12:37)

- Second key admission probe: `/v1/models` 200 (1.1s); chat/completions
  with `prompt_cache_key` 200 (2.5s).
- ch91 deleted and recreated via `multi_to_single` (id preserved at 91);
  DB snapshot `new-api-before-jianzhile-gpt-5.6-sol-20260817-123750.db`.
- Fork trap re-confirmed: even `multi_to_single` left
  `multi_key_status_list: null` + `multi_key_mode: ""` — fixed by the
  scripted DB BLOB write (no PUT), 75s `SyncChannelCache` wait.
- Post-fix admin tests ×3 (1.5s/1.8s/1.4s, all success): DB
  `multi_key_polling_index` advanced 0→1→0→1 — both keys exercised;
  `zg-gpt-5.6-sol` alias also passes through multi-key.

## Risk notes

- The upstream validates a Codex-shaped Responses envelope. A generic Chat
  probe sent directly to the upstream can still return 403, but OMP traffic is
  converted when it actually routes through ch91. Guardian's ch91 profile
  explicitly requests Responses with `stream=true`, so the channel remains
  eligible for real auto-ban/recovery governance without relying on the
  incompatible generic probe.
- Single model: no model-level failover within the channel (key-level
  polling only). Per-key upstream death degrades to the surviving key.

## Incident: downstream 403 recurrence (2026-08-17 12:54)

~15 minutes after the multi-key conversion verified clean, the relay's
downstream started refusing again — the same `bad_response_status_code`
403 shape as the 2026-08-13 refusal:

- key1 (`...P6fx`): deterministic 403, ~1.0s
- key2 (`...MwB7`): 429 `Too many pending requests` ×2, then same 403 —
  the 429 phase suggests a shared congested/dying downstream, not per-key
  quota
- `/v1/models` still 200 for both keys: auth valid, model listed — the
  refusal is strictly at completion time (identical to the 08-13 profile)

**Guardian auto_ban disabled ch91 (status=2) on its own** — no manual
action taken. channel_info (multi-key BLOB) intact; `polling_index=1`
shows both keys had been exercised before the ban.

Re-enable checklist (when the provider fixes their downstream):

1. Direct probe both keys — must return 200 with content, twice in a row,
   minutes apart (a single 200 is not enough: this gateway flipped from
   healthy to 403 within 15 minutes on 2026-08-17):
   `curl -s https://jianzhile.vip/v1/chat/completions -H "Authorization: Bearer $KEY" -d '{"model":"gpt-5.6-sol","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16}'`
2. `POST /api/channel/91/status {"status": 1}`
3. Admin channel test ×2, confirm `multi_key_polling_index` advances.

## Root-cause correction and fix (2026-08-17 13:45)

The 12:54 conclusion above was incomplete. The provider was not simply down:
the same key returned 200 when a real Codex 0.147 request was forwarded
unchanged, but returned 403 through NewAPI.

Wire capture found the exact difference:

- NewAPI preserved the complete Responses body, including `input`, `tools`,
  `reasoning`, `prompt_cache_key`, and `client_metadata`.
- Static `User-Agent` and `Originator` overrides were applied correctly.
- NewAPI's normal outbound header setup dropped the dynamic Codex fingerprint:
  `Session-Id`, `Thread-Id`, `X-Client-Request-Id`,
  `X-Codex-Turn-Metadata`, `X-Codex-Beta-Features`,
  `X-Codex-Window-Id`, and `X-Openai-Internal-Codex-Responses-Lite`.
- Adding only `X-Client-Request-Id` still returned 403. The upstream validates
  the complete client envelope, not one header or the key alone.

The fix is channel-local and does not modify CC Switch or NewAPI binaries:

1. `header_override` now contains `"*": ""`, which invokes NewAPI's safe
   client-header passthrough, plus explicit Codex UA/Originator pins. Unsafe
   authentication/host/hop-by-hop headers remain filtered by NewAPI.
2. Added isolated alias `jianzhile-codex-gpt-5.6-sol` so an E2E Codex probe
   can select ch91 without changing the four higher-priority sol channels.
3. Enabled NewAPI's channel-local Chat→Responses policy for ch91 and the sol
   aliases; kept `auto_ban=1`, set `test_model` to the dedicated alias, and
   gave Guardian a ch91-specific Responses+stream test profile.
4. Added a conditional `param_override` for dynamic Codex headers while
   retaining deterministic fallback headers for OMP/admin requests.
5. `scripts/ops/fix_jianzhile_codex_channel.py` applies the repair with an
   online backup and verifies that the multi-key `channel_info` BLOB is
   byte-for-byte unchanged.

Production verification through `127.0.0.1:3002/v1/responses`:

- Codex 0.147 → NewAPI → ch91 → jianzhile returned
  `CH91-NEWAPI-OK` and `CH91-SECOND-OK` in two consecutive streaming calls.
- NewAPI logs `84475` and `84480` both record ch91, the isolated alias,
  positive prompt/completion usage, and 4s/3s completion time.
- `multi_key_polling_index` advanced `0 → 1`, proving both stored keys were
  exercised; `multi_key_status_list` remained empty.
- A plain Chat request sent directly to `jianzhile.vip` remains unsupported;
  the supported OMP path is Chat → NewAPI ch91 conversion → Responses.

## Rollback

```powershell
# disable the channel (Guardian/SQLite SSOT remains consistent)
POST /api/channel/91/status {"status": 2}
```

DB snapshot before channel creation:
`~/.new-api-local/backups/new-api-before-jianzhile-gpt-5.6-sol-20260817-122045.db`
(78,516,224 bytes).
Snapshot before the multi-key recreate:
`~/.new-api-local/backups/new-api-before-jianzhile-gpt-5.6-sol-20260817-123750.db`
(78,614,528 bytes).

Snapshot before the final stream/non-stream header correction:
`~/.new-api-local/backups/new-api-before-ch91-codex-pass-20260817-145654.db`
(79,036,416 bytes, `integrity_check=ok`). Earlier repair snapshots are
`new-api-before-ch91-codex-pass-20260817-143956.db` and
`new-api-before-ch91-codex-pass-20260817-142441.db`.

The live OMP model file backup is
`~/.omp/agent/models.yml.20260817-143448-jianzhile-ch91.bak`; the latest
runtime Guardian backup is
`~/.omp/guardian/guardian.py.bak-20260817-1508-ch91-stream`.

## Related

- `docs/ops/jianzhile-channel-2026-08-13.md` — the refused first attempt and
  the re-test checklist this runbook followed.
- `docs/ops/ooioo-gpt56sol-channel-2026-08-16.md` — ch87, the template for
  this channel's parameters and script.
- `docs/ops/t1qq-sol-channel-2026-08-16.md` — ch90, the next rung up.
