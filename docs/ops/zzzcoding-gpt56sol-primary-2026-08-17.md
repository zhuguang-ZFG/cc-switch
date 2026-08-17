# zzzcoding gpt-5.6-sol primary (2026-08-17)

## Decision

`https://api.zzzcoding.org` is NewAPI ch92 `zzzcoding-gpt-5.6-sol` and is the
Sol primary at priority 60 / weight 15. ch91 `jianzhile-gpt-5.6-sol` is the
direct backup at priority 50 / weight 5. The channel and all four ability rows
use the same posture.

The API key is stored only in NewAPI. Do not place it in repository files,
command arguments, logs, or runbooks.

## Protocol contract

- Upstream `/v1/models` returns only `gpt-5.6-sol`.
- Generic Chat Completions is rejected with 403.
- Codex-shaped `/responses` and `/v1/responses` stream successfully.
- NewAPI exposes `gpt-5.6-sol`, `zg-gpt-5.6-sol`,
  `zg-agent-gpt-5.6-sol`, and `zzzcoding-codex-gpt-5.6-sol` on ch92.
- The channel-local Chat-to-Responses policy includes ch92 and those aliases.
- The Responses request override pins `parallel_tool_calls=false`; omitting it
  fails against this upstream's Codex Responses Lite contract.
- Guardian tests ch92 with
  `endpoint_type=openai-response&stream=true` and retains normal auto-ban and
  recovery behavior.

## Safe apply and rollback

```powershell
python3 scripts/ops/update_zzzcoding_sol_primary.py
python3 scripts/ops/update_zzzcoding_sol_primary.py --apply
python3 scripts/ops/verify_zzzcoding_sol_primary.py
python3 scripts/ops/newapi-local-smoke.py
```

The posture updater is read-only unless `--apply` is present. It validates the
exact ch91/ch92 identities and BLOB `channel_info`, creates an online SQLite
snapshot with `PRAGMA integrity_check`, atomically changes only channel and
ability priority/weight, preserves status and secrets, waits 75 seconds for
the NewAPI cache, and restores both tiers on any failure.

Rollback artifacts retained under `~/.new-api-local/backups/`:

- `new-api-before-zzzcoding-sol-primary-20260817-193406.db` (first attempt)
- `new-api-before-zzzcoding-sol-primary-20260817-193736.db` (channel apply)
- `new-api-before-zzzcoding-sol-posture-20260817-195036.db` (primary switch)

All three snapshots passed `PRAGMA integrity_check`. Guardian's pre-change
runtime copy is
`~/.omp/guardian/guardian.py.bak-20260817-193346-zzzcoding-ch92`.

## Verification evidence

At 2026-08-17 19:53 CST, the read-only verifier reported:

- channel and ability readback: ch92 p60/w15, ch91 p50/w5;
- two ch92 streaming Responses management probes: 1844 ms and 2115 ms;
- aggregate model: `gpt-5.6-sol`, streaming Chat ingress converted to Responses;
- exact semantic text: `CH92-PRIMARY-OK`;
- HTTP 200, SSE `[DONE]`, prompt/completion usage 4396/10;
- elapsed time 2220 ms;
- fresh NewAPI log id 85805, `channel_id=92`, stream=1, use_time=2 seconds.

The subsequent full `newapi-local-smoke.py` run reported `ALL OK`, including
Sol primary posture, critical ability posture, proxy listeners, and real
`sensenova-6.7-flash-lite` / `gpt-5.6-luna` model requests.
