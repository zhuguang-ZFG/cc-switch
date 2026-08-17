# jianzhile gpt-5.6-sol fifth-line channel (2026-08-17)

## Scope

The jianzhile gateway (`https://jianzhile.vip`, a NewAPI fork relay exposing
exactly one model, `gpt-5.6-sol`) was aggregated into NewAPI as channel
**ch91** `jianzhile-gpt-5.6-sol` with a fresh user-supplied key.

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
  (NewAPI appends `/v1/chat/completions` itself)
- models: `gpt-5.6-sol,zg-gpt-5.6-sol,zg-agent-gpt-5.6-sol`
- model_mapping: `zg-gpt-5.6-sol`/`zg-agent-gpt-5.6-sol` → `gpt-5.6-sol`
  (same alias set as ch83/ch45/ch87/ch90)
- single key passed via argv at creation time; not stored in this repo
- priority 10, weight 5, group `default`, status 1
- Creation helper: `scripts/ops/add_jianzhile_gpt56sol_channel.py` —
  idempotent; re-run performs dup-check + readback verification only

## Verification evidence (2026-08-17)

- Upstream direct probe `GET /v1/models`: HTTP 200, 2.6s, exactly one model
  `gpt-5.6-sol` (matches the 2026-08-13 observation; relay still a NewAPI
  fork).
- Upstream direct probe `POST /v1/chat/completions` model `gpt-5.6-sol`
  (`max_tokens 16`): HTTP 200, 3.5s, normal completion — the 2026-08-13
  deterministic 403 is gone.
- Create readback: ch91 type=1 status=1 priority=10 weight=5; abilities rows
  for all three models enabled at 10/5.
- NewAPI admin channel test: `gpt-5.6-sol` success (2.7s);
  `zg-gpt-5.6-sol` alias success (2.8s, proves model_mapping).
- No E2E through `127.0.0.1:3002` claimed: sol traffic still routes to
  ch83/ch45 first by priority; ch91 engages only when 83/45/87/90 all fail.
- OMP production wire shape probe: plain `/v1/chat/completions` +
  `prompt_cache_key` (the field OMP injects unconditionally) → HTTP 200,
  2.7s. **No Codex masquerade needed**: no UA spoofing, no `instructions`/
  `store` fields, no param_override on the channel.
- Idempotent re-run of the creation script: verify-only path (dup-check by
  name short-circuits to readback).

## Risk notes

- This gateway 403'd hard for days in the past; treat it as flaky. Guardian
  auto_ban will quarantine it on repeat failure — that is the intended
  behavior and does not pollute the pool at priority 10.
- Single key, single model: no failover within the channel.

## Rollback

```powershell
# disable the channel (Guardian/SQLite SSOT remains consistent)
POST /api/channel/91/status {"status": 2}
```

DB snapshot before channel creation:
`~/.new-api-local/backups/new-api-before-jianzhile-gpt-5.6-sol-20260817-122045.db`
(78,516,224 bytes).

## Related

- `docs/ops/jianzhile-channel-2026-08-13.md` — the refused first attempt and
  the re-test checklist this runbook followed.
- `docs/ops/ooioo-gpt56sol-channel-2026-08-16.md` — ch87, the template for
  this channel's parameters and script.
- `docs/ops/t1qq-sol-channel-2026-08-16.md` — ch90, the next rung up.
