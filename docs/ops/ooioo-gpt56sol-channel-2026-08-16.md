# ooioo gpt-5.6-sol backup channel (2026-08-16)

## Scope

The ooioo gateway (`https://ooioo.work`, OpenAI-compatible) was aggregated into
NewAPI as channel **ch87** `ooioo-gpt-5.6-sol`, a second-line backup for
`gpt-5.6-sol`. No relay needed — the upstream serves standard
`/v1/chat/completions` directly.

Priority ladder after this change:

| Channel | Priority | Role |
|---|---|---|
| ch83 muyuan-sol | 50 | primary |
| ch45 agentrouter | 40 | first backup |
| **ch87 ooioo-gpt-5.6-sol** | **30** | **second backup (new)** |

## Channel parameters

- type=1 (OpenAI), base_url=`https://ooioo.work` — no `/v1` suffix (NewAPI
  appends `/v1/chat/completions` itself; storing `…/v1` yields `/v1/v1/…` 404)
- models: `gpt-5.6-sol,zg-gpt-5.6-sol,zg-agent-gpt-5.6-sol`
- model_mapping: `zg-gpt-5.6-sol`/`zg-agent-gpt-5.6-sol` → `gpt-5.6-sol`
  (same alias set as the ch83/ch45 active pair)
- priority 30, weight 5, group `default`, status 1
- Real key passed via argv at creation time; not stored in this repo.
- Creation helper: `scripts/ops/add_ooioo_gpt56sol_channel.py` — idempotent;
  re-run performs dup-check + readback verification only.

## Verification evidence (2026-08-16)

- Upstream direct probe `POST https://ooioo.work/v1/chat/completions`
  model `gpt-5.6-sol`: HTTP 200, 2.8s, normal chat completion.
- Create readback: ch87 type=1 status=1 priority=30; abilities rows for all
  three models enabled at priority 30.
- NewAPI admin channel test: `gpt-5.6-sol` success (2.2s);
  `zg-gpt-5.6-sol` alias success (3.8s, proves model_mapping).
- End-to-end traffic still routes to ch83/ch45 first by priority; ch87 only
  engages when both higher-priority channels are down. No primary-channel
  behavior change.

## Rollback

```powershell
# disable the channel (Guardian/SQLite SSOT remains consistent)
POST /api/channel/87/status {"status": 2}
```

DB snapshot before channel creation:
`~/.new-api-local/backups/new-api-before-ooioo-gpt-5.6-sol-20260816-210540.db`.

## Related

- `docs/ops/mistral-glm-channel-2026-08-16.md` — the ch85 runbook whose
  workflow contract (dup-check, DB snapshot, double-wrapped create body,
  readback) this channel followed.
