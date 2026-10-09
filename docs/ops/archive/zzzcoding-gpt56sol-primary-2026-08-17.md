# zzzcoding gpt-5.6-sol primary (2026-08-17)

## Decision

`https://api.zzzcoding.org` is NewAPI ch92 `zzzcoding-gpt-5.6-sol` and is the
Sol primary at priority 60 / weight 15. ch91 `jianzhile-gpt-5.6-sol` is the
strict direct backup at priority 55 / weight 5. ch83 `muyuan-sol` retains
priority 50 / weight 5 and its existing status. Each channel and its ability
rows use the same posture.

These tiers describe recovery order, not unconditional availability. As of
2026-08-20, ch83/ch91/ch92 are all `status=2` after current protocol-shaped
probes failed; ch45 `agentrouter` remains enabled at p40/w5. Do not enable a
dedicated Sol tier to satisfy a route-count check. Guardian owns bounded
recovery and will restore the fixed weight only after successful probes.

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
python3 scripts/ops/update_zzzcoding_sol_primary.py --allow-disabled-probe-failures --apply
python3 scripts/ops/verify_zzzcoding_sol_primary.py
python3 scripts/ops/newapi-local-smoke.py
```

The posture updater is read-only unless `--apply` is present. It validates the
exact ch91/ch92 identities and BLOB `channel_info`, creates an online SQLite
snapshot with `PRAGMA integrity_check`, atomically changes only channel and
ability priority/weight, preserves status and secrets, waits 75 seconds for
the NewAPI cache, and restores all three tiers on any failure.

The `--allow-disabled-probe-failures` exception is deliberately narrow: it may
repair priority/weight for an already-disabled ch91/ch92 while preserving that
disabled status. Every enabled channel still requires two successful forced
management probes, and post-write probes run for enabled channels only. The
flag is not permission to enable an unhealthy route.

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

## Hardening follow-up (2026-08-17 20:43-21:02 CST)

Credential rotation remained explicitly deferred. No key was printed, copied
to the repository, or included in telemetry.

Completed production changes:

- Removed only `fallbackChains.zg-newapi/k3` from OMP while preserving
  `modelRoles.default=zg-newapi/k3:high` and role-specific chains. Backup:
  `config.yml.20260817-204309-before-default-fallback.bak` (3350 bytes).
  `omp models` and all 34 OMP route tests passed afterward.
- Rechecked ch45 with two NewAPI management probes and exact OMP direct text
  `AGENTROUTER-THIRD-OK`, then enabled it at p40/w5. Rollback snapshot:
  `new-api-before-zzzcoding-sol-posture-20260817-204443.db` (the shared backup
  helper still used its old filename prefix for this first rollout).
- Registered `OMP Sol Semantic Monitor` every 30 minutes with
  `MultipleInstances=IgnoreNew`, `ExecutionTimeLimit=PT5M`, `RestartCount=0`,
  and no channel mutation/restart path. The first live record was HTTP 200,
  exact semantic text, positive usage, and `channel_id=92`. Runtime source
  hashes matched the repository; registration backup directory:
  `~/.omp/guardian/task-backups/sol-monitor-20260817-204613-864`.

Final ch92 context evidence used server-returned usage rather than body size:

| Shape | Returned `prompt_tokens` | Result | TTFT | Total | Log/channel |
| --- | ---: | --- | ---: | ---: | --- |
| plain | 200012 | HTTP 200, semantic, `[DONE]` | 7272 ms | 10065 ms | 86120 / 92 |
| plain | 280012 | HTTP 200, semantic, `[DONE]` | 7822 ms | 8901 ms | 86122 / 92 |
| plain | 340012 | HTTP 200, semantic, `[DONE]` | 26308 ms | 26418 ms | 86124 / 92 |
| plain | 380012 | HTTP 200, semantic, `[DONE]` | 13052 ms | 13486 ms | 86127 / 92 |
| plain | 396012 | HTTP 200, semantic, `[DONE]` | 10957 ms | 11317 ms | 86128 / 92 |
| one tool | 364130 | HTTP 200, semantic, `[DONE]` | 7786 ms | 8026 ms | 86131 / 92 |
| one tool | 396130 | HTTP 200, semantic, `[DONE]` | 9078 ms | 9541 ms | 86133 / 92 |

The first calibration used request-size tolerance too loosely and produced a
400348-token probe. It succeeded, but was discarded as the acceptance run
because it consumed the intended 400k margin. The final calibration above was
within roughly 130 tokens of each target. OMP `contextWindow: 400000` remains
unchanged.

### Deferred by live upstream health

ch91 remained enabled at its pre-change p50/w5 posture. Four independent
forced management preflights across the rollout window returned distributor
503 (`No available channel for model gpt-5.6-sol under group GPT`). The 200k
forced-alias campaign likewise returned 503 before usage was produced.

Therefore the p55 promotion and ch92-disable failover drill were not executed.
`update_zzzcoding_sol_primary.py --apply` now requires ch92 and ch91 to pass
two forced management probes each before it writes anything. Once ch91
recovers, run:

```powershell
python3 scripts/ops/drill_sol_failover.py --preflight
python3 scripts/ops/update_zzzcoding_sol_primary.py --apply
python3 scripts/ops/drill_sol_failover.py --apply
python3 scripts/ops/verify_zzzcoding_sol_primary.py
```

Until then, `verify_zzzcoding_sol_primary.py --allow-pending-posture` verifies
ch92 without pretending the p55 migration has completed.

Final live smoke passed NewAPI status, all proxy listeners, option/affinity
policy, channel isolation, ch45 fallback posture, critical pool capacity, and
multi-key health. It exited nonzero for the expected pending ch91 channel and
three ability rows at p50 instead of p55, plus an unrelated live
`gpt-5.6-luna` 403. Repository/route/Guardian tests remained 249/249 green;
the retained ch45 snapshot was 80,527,360 bytes and passed
`PRAGMA integrity_check`.

## Fixed-tier quarantine follow-up (2026-08-20)

The earlier pending p55 posture was later applied while the route remained
disabled. Current channel/ability posture is:

| Channel | Status | Priority / weight | Owner/evidence |
| --- | ---: | ---: | --- |
| ch92 zzzcoding | 2 | 60 / 15 | fixed tier, current probe unhealthy |
| ch91 jianzhile | 2 | 55 / 5 | fixed tier, current probe unhealthy |
| ch83 muyuan | 2 | 50 / 5 | fixed tier, current probe unhealthy |
| ch45 agentrouter | 1 | 40 / 5 | serving independent fallback |

Guardian no longer dynamically reweights ch83/ch91/ch92. Error and full scans
share a three-soft-failure threshold; successful, disabled, or identity-changed
channels clear the in-memory streak. ch91/ch92 use streaming Responses
management probes. Recovery restores the exact configured weight instead of a
stale historical or pool-balanced value.

The live NewAPI smoke now accepts a real-model `SKIP` only when every declared
route is disabled and attributed to Guardian, NewAPI auto-ban, or explicit
policy. Unknown disables, zero-weight enabled channels, and missing route
declarations remain failures. The 2026-08-20 smoke passed SenseNova with HTTP
200 and skipped Muse because its only declared route, ch48, was attributed
disabled. That result says nothing about the three Sol upstreams and must not
be used to re-enable them.

### Monitor/auth follow-up (2026-08-20 01:38-01:47 CST)

The scheduled smoke now reuses Guardian's existing long-lived NewAPI admin
token after the session cache, so the missing-cache path no longer creates a
password session on every run. A configured Guardian token is fail-closed on
401; it is never rotated or printed. The repository smoke read the management
API through this path, passed all policy checks, and finished with
`summary: ALL OK`; SenseNova returned HTTP 200 and Muse was explicitly skipped for its fully
attributed disabled route.

The updated monitor runtime was deployed with verified backups under
`~/.omp/guardian/task-backups/smoke-auth-20260820-014127/` and
`~/.omp/guardian/task-backups/smoke-monitor-classification-20260820-014710/`.
The monitor remains read-only. A temporary-output validation request returned
HTTP 200, exact semantic text, positive usage, `[DONE]`, and `channel_id=45`;
it was classified as `route_category=non_primary` and
`error_category=primary_not_attributed`, with `alert=false` for the first
failure. This is evidence that ch45 is serving as the independent fallback
while ch92 is unavailable, not evidence that ch92 recovered. The production
monitor's historical state and `LastTaskResult=1` were intentionally retained
and no channel was re-enabled.

## Hermes background K3 diagnosis and Agnes cutover (2026-08-17 21:31-21:43 CST)

Repeated K3 traffic after the fallback edit was not evidence that the Sol
route had fallen back. Process inspection showed the active chain
`omp.exe -> pi-coding-agent -> pi-hermes-memory -> pi-coding-agent`, with the
Hermes child explicitly launched as `--model zg-newapi/k3 --thinking off`.
The independent cause was
`~/.pi/agent/hermes-memory-config.json:llmModelOverride=zg-newapi/k3`.

This override is separate from both OMP `modelRoles` and `retry.fallbackChains`.
It covers background review, correction save, session flush, and memory
consolidation. The default review cadence is every 10 turns or 15 tool calls.
Rapid `failures.md` recovery/retired writes confirmed active memory work, but
recovery-file count alone is not an LLM-request count.

The override was changed to `zg-newapi/agnes-2.5-flash` with thinking disabled.
Agnes was selected over `dots-3-note-prev` because Agnes is the registered
free/fast pool and passed an isolated OMP no-tools probe with exact output
`HERMES_AGNES_OK` in 6.6 seconds; the XiaoHongShu/Dots ch77 route had recent
repeated 429 evidence and was not suitable for recurring background work.

Only `llmModelOverride` changed. The JSON parsed successfully after the edit,
and the verified rollback copy is
`~/.pi/agent/hermes-memory-config.json.20260817-2142-before-agnes.bak` (166
bytes, SHA-256 identical to the pre-change source). The extension loads this
configuration once at OMP startup, so the already-running interactive OMP
session retained its K3 child until a normal OMP restart. It was not forcibly
terminated. OMP roles that explicitly select K3 remain unchanged and may
still generate intentional foreground K3 traffic.

## OMP extensibility hardening follow-up (2026-08-17 23:37 CST)

The Hermes extension was subsequently upgraded from `0.9.5` to `0.9.6` after
an online backup of its 151,875,584-byte session database passed
`PRAGMA integrity_check`. The Agnes background-model override remains
unchanged. OMP's default skill catalog was also reduced from 59 to 26 entries,
project-root MCP discovery was disabled, and tool approval changed from
`yolo` to `write`. No provider, model role, fallback chain, NewAPI channel, or
credential changed in this follow-up.

The existing OMP process was left running and requires a later normal restart
to load the upgraded extension. Full configuration, verification, rollback,
and upstream-issue details are recorded in
[`omp-plugins-mcp-skills-hardening-2026-08-17.md`](./omp-plugins-mcp-skills-hardening-2026-08-17.md).
