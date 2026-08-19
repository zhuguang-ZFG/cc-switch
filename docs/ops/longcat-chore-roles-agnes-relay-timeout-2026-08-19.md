# LongCat chore roles + Agnes relay upstream timeout fix

Date: 2026-08-19. Scope: OMP role routing in `~/.omp/agent/config.yml`,
`~/.omp/agent/models.yml` display-name fix, and the local Agnes relay at
`~/.omp/proxies/agnes-relay/agnes-relay.js`. No cc-switch source, NewAPI
schema, or channel configuration was changed.

## Background

Two independent items from the same session:

1. The previous session's Agnes large-context investigation ended with an
   unresolved boundary: small `agnes-2.5-pro` requests returned HTTP 200 on
   both ch68 (international, via local relay) and ch69 (China), while a ~50K
   prompt-tier request returned 403 (China balance pre-auth) and 502
   (international, via the local relay).
2. The paid LongCat-2.0 key (official `https://api.longcat.chat/openai/v1`)
   was configured as a direct OMP provider but carried no real workload —
   it appeared only as the first fallback of the `bigctx` role chain.

## Agnes relay 502 root cause and fix

Root cause: `agnes-relay.js` passed `timeout: 120000` to the upstream
`https.request` for `apihub.agnes-ai.com`. Node applies this as a socket
inactivity timeout, so any upstream that produces no bytes for 120 seconds —
typical for a large-context non-streaming request still being computed — was
actively destroyed by the relay, surfacing as
`502 agnes relay upstream error: upstream timeout`. Small requests have short
TTFT and were unaffected, which matches the observed split exactly.

Fix: `timeout: 120000` -> `timeout: 600000` (bounded at ten minutes; streaming
responses never trip an inactivity timeout while bytes flow).

Backup before edit:

```text
~/.omp/proxies/agnes-relay/agnes-relay.js.bak-20260819-upstream-timeout-600s
```

Deployment: the supervisor restarts the relay within 60 seconds after the old
process exits; `node --check` passed before the restart, and `/healthz`
returned the expected identity document after the supervisor brought the new
process up.

Verification: a non-streaming `agnes-2.5-pro` request with 45,264 prompt
tokens through the relay returned HTTP 200 in 15.0s. Note honestly: the
upstream answered in 15s, well below even the old 120s window, so the exact
>120s stall from the previous day was not reproduced; the widened window
covers that stall class by construction. The throwaway probe script was
deleted; no credentials were written to disk.

Beneficiaries: ch68 serves `claude-haiku-4-5` (aliased to `agnes-2.0-flash`)
and the Agnes family for OMP; large requests on that channel no longer die at
the 120s relay boundary. OMP background compaction does not use this channel.

## LongCat-2.0 chore roles

Role changes in `~/.omp/agent/config.yml` (`modelRoles`):

| Role | Before | After |
|---|---|---|
| `task` | `zg-newapi/muse-spark-1.2-contributor:max` | `longcat/LongCat-2.0` |
| `smol` | `zg-newapi/claude-haiku-4-5` | `longcat/LongCat-2.0` |
| `commit` | `zg-newapi/claude-haiku-4-5` | `longcat/LongCat-2.0` |
| `tiny` | `zg-newapi/muse-spark-1.2-contributor` | `zg-newapi/agnes-2.5-flash` |

Rationale:

- The LongCat key is paid; the user explicitly wants it carrying routine
  work instead of idling as a fallback.
- `muse-spark-1.2-contributor` is also paid, so `tiny` (the most trivial
  role) moved to the free Agnes pool instead of LongCat.
- The `task` selector intentionally drops the `:max` thinking suffix:
  LongCat reasoning is built in (a trivial probe already emitted
  `reasoning_tokens`), and `task` is the highest-volume role; pinning `:max`
  would multiply paid-quota burn. Re-pin `longcat/LongCat-2.0:max` only if
  subagent quality regresses in practice.

New model-level fallback chains (`retry.fallbackChains`):

```yaml
longcat/LongCat-2.0:
  - zg-newapi/claude-haiku-4-5
  - zg-newapi/muse-spark-1.2-contributor
zg-newapi/agnes-2.5-flash:
  - zg-newapi/agnes-2.0-flash
  - zg-newapi/sensenova-6.7-flash-lite
```

The pre-existing role-level `smol` chain remains active on top, so `smol` has
double-layer fallback.

Backups before edits, newest first:

```text
~/.omp/agent/config.yml.bak-20260819-task-longcat
~/.omp/agent/config.yml.bak-20260819-tiny-free
~/.omp/agent/config.yml.bak-20260819-longcat-chore-roles
```

### Capability evidence

| Probe | Result |
|---|---|
| LongCat-2.0 direct, trivial completion | HTTP 200 in 3.9s, `reasoning_tokens` present |
| LongCat-2.0 direct, forced `tool_choice` | HTTP 200, valid `tool_calls` (name + JSON arguments), `finish_reason=tool_calls` |
| `zg-newapi/agnes-2.5-flash`, trivial completion | HTTP 200 in 0.5s (256 cached prompt tokens) |

### Activation and follow-up

A running OMP session keeps its startup config; these roles take effect on the
next OMP restart. The role-refresh canary had no `longcat/LongCat-2.0`
selector records at deploy time; first canary results land in
`~/.omp/agent/logs/omp-model-routing.jsonl` after restart — check them before
trusting the role in anger. Watch paid-quota burn on the LongCat platform:
with `task` on a paid model, consumption rises noticeably; reverting `task`
to the free pool is a one-line change if the burn rate is unacceptable.

## Compaction pipeline verification (no change)

Post-deployment review of the 2026-08-18 global compaction model
(`zg-newapi/deepseek-v4-flash`, see `omp-global-compaction-model.md`) against
live data:

- NewAPI logs show 75 `deepseek-v4-flash` calls since deployment; each large
  call pairs with a session compaction event minutes later.
- 80–92K prompt-token compactions complete in 40–83s; every post-deployment
  compaction entry carries a complete summary (5–15K characters).
- The original "compaction is very slow" complaint is resolved by the
  dedicated model; no further change was made to that path.

## models.yml display-name mojibake fix

The direct `longcat` provider entry in `~/.omp/agent/models.yml` stored
`name: LongCat 2.0 (瀹樻柟)` — UTF-8 bytes of `官方` mis-decoded as GBK and
re-encoded. Fixed to `LongCat 2.0 (官方)`; other Chinese names in the file
were already correct, so the damage was isolated to this entry. Backup:

```text
~/.omp/agent/models.yml.bak-20260819-longcat-name-mojibake
```

OMP has no `zg-newapi/LongCat-2.0` route, so the NewAPI-side `LongCat-2.0`
alias on ch68/ch69 (mapped to `agnes-2.0-flash`) cannot be hit accidentally
from OMP; the direct provider and the NewAPI alias do not interfere.

## Rollback

- Relay: copy the `.bak-20260819-upstream-timeout-600s` file over
  `agnes-relay.js` and let the supervisor restart it (or stop the node
  process; the supervisor retries within 60 seconds).
- Roles: copy the newest matching `config.yml.bak-20260819-*` over
  `config.yml` and restart OMP.
- Name: copy `models.yml.bak-20260819-longcat-name-mojibake` over
  `models.yml` and restart OMP.
