# hashneuron channel onboarding — 2026-09-13

## Outcome

ch129 `hashneuron` live in local NewAPI (127.0.0.1:3002):

- type=1 (OpenAI), base_url `https://hashneuron.space` (upstream `/v1` is
  OpenAI-compatible; owned_by=routeopen), key `sk-ro-…` (54 chars).
- models: `glm-5.3` (mapped → upstream `z-ai/glm-5.3-free`) + `z-ai/glm-5.3-free`
  (direct), priority 30 / weight 5, group default, auto_ban=1,
  test_model=`z-ai/glm-5.3-free`. model_mapping=`{"glm-5.3":"z-ai/glm-5.3-free"}`.
- ModelRatio: `z-ai/glm-5.3-free = 0` (free source).
- Full-DB backup: `~/.new-api-local/backups/new-api-before-hashneuron-20260913-212359.db`
  (186,118,144 bytes, PRAGMA integrity_check ok).
- Script: `scripts/ops/add_hashneuron_channel.py` (create / `--map-glm` / `--extend-pool`,
  dry-run by default; key passed via `--key` argv, never written to repo).

## Verification evidence

- Management probe `test/129?model=z-ai/glm-5.3-free`: ok (created disabled, enabled
  only after probe passed).
- Relay probe through 3002 `/v1/chat/completions` (OMP token path): 200.
- Strict readback: channel fields + `abilities.enabled=1` + ModelRatio 0.
- Independent relay consumption: 200 via ch129 (`use_channel:["129"]`,
  token_name `local-windows-clients`, quota 0).
- 21:25:52 one 429 `Concurrent request limit exceeded for this user` from upstream;
  21:26:08 retry 200 → transient upstream per-user concurrency limit, not channel
  defect. Single-channel model = no failover sibling; expect occasional [ERR] rows.

## glm-5.3 mapping (2026-09-13, `--map-glm`)

`z-ai/glm-5.3-free` as a standalone name had no consumers; the mapped name joins
the live glm-5.3 failover pool (ch45 agentrouter p40/w5 primary → ch129 p30/w5
first fallback). ch121 serves `glm-5.3-flash`, a different name — unrelated.

- Verified: ch129 abilities rows both `glm-5.3` and `z-ai/glm-5.3-free` enabled=1;
  per-channel management probes ok for both; relay `glm-5.3` via 3002 → 200
  "pong"; relay `z-ai/glm-5.3-free` → 200.
- Idempotency: sync re-runs repair if models/mapping match but abilities rows
  are missing/disabled (a real failure mode, see BUSY note).

## SQLITE_BUSY incident (2026-09-13 21:32)

First map-glm PUT returned HTTP 200 `success=false` with
`database is locked (5) (SQLITE_BUSY)`: the python backup's read lock and
NewAPI's own writer (66-channel test poller / usage workers) collide on the
non-WAL DB. The models row HAD committed while the abilities sync aborted —
the fork's HTTP 200 failure message understates what was applied.

- Script now: `put_channel_retry` (4 attempts, 8s spacing) on sync and rollback
  PUTs, success check on rollback, 2s settle after backup, and abilities-aware
  idempotency (shape check includes abilities readback).
- Doctrine: after any BUSY from the fork, verify `abilities` rows in the DB —
  HTTP status alone cannot distinguish committed vs aborted.

## Upstream state 2026-09-13

`/v1/models` declares 4 models:

| model | probed | result |
|---|---|---|
| z-ai/glm-5.3-free | 200 | live (reasoning model: reasoning_content, usage counts reasoning_tokens) |
| composer-2.5 | — | `daily_sdk_token_limit_exceeded`, resets 2026-09-14T00:00:00Z |
| grok-4.5 | — | same account-level daily budget exhaustion |
| grok-4.6 | — | same |

Budget is account-level, not per-model: paid trio blocked until UTC midnight.

## Why the trio was NOT added on day 1

- grok-4.5/grok-4.6/composer are already served by ch109 `imagic` (p0/w5,
  enabled) + ch89 `seeseed1ck-hydrogel` (grok-4.6) + ch39 (disabled backup).
- Adding budget-dead models would inject 429 retry noise into the live trio
  pool and risk auto-ban disabling the whole channel — which would also take
  down glm-5.3-free routing.

## Extend procedure (run after 2026-09-14T00:00Z reset)

```
python3 scripts/ops/add_hashneuron_channel.py --key <hashneuron key> --extend-pool --apply
```

- PUT contract per ch127 runbook: fork rejects minimal PUT bodies; GET masks the
  key to empty string → rebuild full object, drop `status`, set the real key
  explicitly; fork syncs abilities on update.
- sync path (`sync_flow`): readback → BUSY-retried full-object PUT → per-model
  probes (tolerate `quota`) → 75s cache wait → relay probe (TEST_MODEL only)
  → strict verify (models set + mapping + abilities + p/w); rollback PUT on failure.
- Per-model probe report: `quota` = budget not reset yet (safe, channel stays
  enabled); `ok` = joined the pool. abilities readback must show all 4 enabled.
- No ModelRatio added for the trio: upstream pricing unverified; decide ratios
  when the budget/billing shape of the paid tier is known.

## Operational notes

- glm-5.3 is a reasoning model: small `max_tokens` yields empty content with
  `finish_reason=length` (reasoning eats the budget) — allocate headroom.
- `token_name=模型测试` type-2 rows are channel-test records, not real consumption.
- Rollback: full snapshot above; targeted removal = `DELETE /api/channel/129`
  (routing gone immediately) or `POST /api/channel/129/status {"status":2}`.
