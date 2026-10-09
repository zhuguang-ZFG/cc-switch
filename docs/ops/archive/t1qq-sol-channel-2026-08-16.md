# t1qq gpt-5.6-sol last-resort channel (2026-08-16)

## Scope

The t1qq gateway (`https://ai.t1qq.com`, OpenAI-compatible) was aggregated
into NewAPI as channel **ch90** `t1qq-gpt-5.6-sol` — a multi-key (2 keys,
polling) **last-resort** backup for `gpt-5.6-sol`.

Priority ladder after this change:

| Channel | Priority | Role |
|---|---|---|
| ch83 muyuan-sol | 50 | primary (degraded — see sol-chain-muyuan-degradation) |
| ch45 agentrouter | 40 | first backup |
| ch87 ooioo | 30 | second backup |
| **ch90 t1qq** | **20** | **last resort (new)** |

Gateway model list (both keys): `gpt-5.4, gpt-5.4-mini, gpt-5.5, gpt-5.6,
gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra`. Only the sol trio was aggregated
per request scope (兜底); the rest remain available for future channels.

## Channel parameters

- type=1 (OpenAI), base_url=`https://ai.t1qq.com` — no `/v1` suffix
- models: `gpt-5.6-sol,zg-gpt-5.6-sol,zg-agent-gpt-5.6-sol`
- model_mapping: `zg-gpt-5.6-sol`/`zg-agent-gpt-5.6-sol` → `gpt-5.6-sol`
  (same alias set as ch83/ch45/ch87)
- **2 keys, multi-key polling** (keys passed via argv; not stored in repo)
- priority 20, weight 5, group `default`, status 1
- Creation helper: `scripts/ops/add_t1qq_sol_channel.py` — idempotent;
  re-run on a healthy channel is verify-only.

## Upstream incident during onboarding

~23:38–23:47 the gateway was hard-down: `/v1/models` kept listing models
for both keys, but every completion (both keys, minimal and full OMP wire
shape, incl. gpt-5.5) instantly returned
`{"error":{"message":"Service temporarily unavailable","type":"api_error"}}`.
Recovered by ~23:49 (first post-fix channel test passed in 2.3s). Treat this
gateway as flaky; its position at priority 20 reflects that.

## Fork multi-key traps (all hit, all fixed)

Documented originally in `docs/ops/tabitoken-channel-2026-08-09.md`; this
channel added new observations:

1. **`mode:"single"` + newline-joined keys = broken channel.** The key is
   stored verbatim; every request dies with
   `net/http: invalid header field value for "Authorization"`. Only
   `mode:"multi_to_single"` is correct. A mis-created channel can never be
   fixed in place (is_multi_key is creation-time only) — delete + recreate.
2. **Even `multi_to_single` did not set `channel_info.is_multi_key`** on
   this build: post-create channel_info was
   `{"is_multi_key":false,"multi_key_size":0,"multi_key_status_list":null,...}`.
3. **A follow-up PUT regenerates channel_info from the request body** and
   clobbers partial DB fixes (my `multi_key_mode`-only PUT reset
   status_list back to null). Do NOT PUT after the DB fix.
4. **Working recipe**: DB-write the full ch75-shaped BLOB —
   `{"is_multi_key":true,"multi_key_size":2,"multi_key_status_list":{},
   "multi_key_polling_index":0,"multi_key_mode":"polling"}` — then wait for
   `SyncChannelCache` (60s) instead of any PUT.

## Verification evidence (2026-08-16)

- Both keys: `/v1/models` 200 (7 models each).
- Pre-fix channel test: instant `do_request_failed` +
  `invalid header field value for "Authorization"` in server log (root cause
  of trap #1/#2).
- Post-fix: 3× admin channel test `GET /api/channel/test/90?model=gpt-5.6-sol`
  → success 2.3s / 1.8s / 2.1s; server log shows `IsMultiKey: true`;
  DB `multi_key_polling_index` advanced 0→1 (both keys exercised).
- E2E via `127.0.0.1:3002` full OMP wire shape: 200 SSE clean `[DONE]`
  (served by ch83 per priority — expected; ch90 engages only when
  83/45/87 all fail).
- Readback: ch90 type=1 status=1 priority=20 weight=5; abilities rows for all
  three models enabled at 20/5; channel_info matches the target shape.
- Idempotent re-run of the creation script: verify-only PASS.

## Rollback

```powershell
# disable the channel (Guardian/SQLite SSOT remains consistent)
POST /api/channel/90/status {"status": 2}
```

DB snapshots: `new-api-before-t1qq-gpt-5.6-sol-20260816-234221.db` (before
first create) and `...-234738.db` (before multi-key recreate), both under
`~/.new-api-local/backups/`.

## Related

- `docs/ops/tabitoken-channel-2026-08-09.md` — original multi-key contract
  (ch75); this runbook extends it with traps #2/#3.
- `docs/ops/sol-chain-muyuan-degradation-2026-08-16.md` — why a fourth sol
  channel matters: ch83 is actively 504-ing under load.
- `docs/ops/ooioo-gpt56sol-channel-2026-08-16.md` — ch87, the next rung up.
