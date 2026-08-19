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

## Dead `bigctx` role removed, vision moved to Dots (2026-08-19, same session)

The official OMP runtime honors exactly ten role keys (`default, smol, slow,
vision, plan, designer, commit, tiny, task, advisor`; see
[docs/models.md](https://github.com/can1357/oh-my-pi/blob/main/docs/models.md)).
The configured `bigctx: zg-newapi/k3:max` role and its `bigctx:` fallback
chain appeared nowhere in the runtime bundles, the local extensions, or the
guardian — a dead config entry with zero effect. Both were removed.

`vision` moved from `zg-newapi/agnes-2.5-pro` (officially paid, and the China
station enforces a balance pre-auth threshold on large requests) to
`zg-newapi/dots-3-note-prev` (ch77), whose live text/image/OCR evidence is
recorded in `omp-global-compaction-model.md`. The role-level `vision` chain
falls back to `zg-newapi/agnes-2.5-flash` then
`zg-newapi/sensenova-6.7-flash-lite`; the redundant first entry (same model as
the new primary) was dropped. The earlier last-resort choice
`agnes-2.5-pro-alpha` was removed because it is officially a paid model
($0.45/M input, $0.90/M output) and would silently spend money on fallback.

Vision-chain probe evidence (2026-08-19, repository screenshot `add-en.png`):

| Probe | Result |
|---|---|
| `dots-3-note-prev`, text trivial | HTTP 200 in 0.6s |
| `agnes-2.5-flash`, image input | HTTP 200 in 1.2s, correctly read the screenshot subject — free tier does accept image input via NewAPI |
| `sensenova-6.7-flash-lite`, image input, two attempts | both hung past the client timeout (90s/180s) with no gateway completion record; officially documented as image-capable, but live evidence is negative — treat this third leg as weak |

Smoke after edit: `dots-3-note-prev` via NewAPI returned HTTP 200 in 0.6s for
a trivial completion. Backups: `config.yml.bak-20260819-bigctx-vision`,
`config.yml.bak-20260819-vision-free-fallback`.
Effective on next OMP restart, same as the role changes above.

## SOTA channel ch93 re-enabled (2026-08-19, same session)

Symptom: every SOTA escalation today failed — three attempts burned exactly
~180s before `aborted` (`rescue`/`prompt`/`hutuji-path` triggers in
`omp-model-routing.jsonl`). Direct probe returned instant
`503 No available channel for model omp-sota-claude-opus-5`.

Cause: ch93 `omp-sota-sotamodel` was disabled (`channels.status=2`, both
abilities `enabled=0`). Notably `AutomaticDisableChannelEnabled=false` and
zero error logs for ch93 in 24h (last success 10:45), so this was a manual or
scripted disable, not NewAPI auto-ban. The model name
`omp-sota-claude-opus-5` exists only on ch93, which keeps the SOTA lane
isolated from every other channel by construction — per policy, do not
re-route SOTA traffic onto shared channels as a "fix".

Fix: direct SQLite update (`status=1`, abilities `enabled=1`) inside a single
transaction after a full DB backup; the admin API `PUT /api/channel/` was
deliberately abandoned because it rejects partial updates, and PUTting the
GET-readback object would write back a masked key and corrupt the channel.
NewAPI (no memory channel cache configured) picked the change up immediately.

Verification: `omp-sota-claude-opus-5` via OMP's `zg-newapi` route returned
HTTP 200 in 1.7s with cache-read billing fields populated; the NewAPI log
attributes the request to ch93 — isolation confirmed.

Known remaining gaps (not fixed in this session):

- The SOTA escalation extension slow-fails: a hard-down channel (instant 503
  at the distributor) still produced ~180s `aborted` attempts. It should
  fail fast on immediate gateway-level "no available channel" responses.
- Canary selectors `zg-newapi/gpt-5.6-sol:max` and `zg-newapi/gpt-5.6-luna:max`
  repeatedly fail with `probe-result-missing` (38–79s). Neither selector is on
  any current role; the red canaries mask real alerts. Fix or remove them from
  the canary list.

## smol fallback chain reordered free-first (2026-08-19, same session)

The role-level `smol` chain previously led with the paid
`muse-spark-1.2-contributor`, so a LongCat outage would fall straight onto a
paid model. Reordered to put verified-live inexpensive/free legs first:
`fengwind/deepseek-v4-flash` (probe 200/2.5s), `zg-newapi/mercury-2`
(200/0.5s), `zg-newapi/sensenova-6.7-flash-lite` (200/6.3s), with
`muse-spark-1.2-contributor` kept last as the quality floor. Backup:
`config.yml.bak-20260819-smol-chain-reorder`.

## task role back to Muse contributor (2026-08-19, same session)

Clarification from the user: `muse-spark-1.2-contributor` rides the OpenCode
Go subscription — prepaid, so idle quota is waste, and it should carry real
coding work. `task` (the subagent code-execution role) therefore returns to
`zg-newapi/muse-spark-1.2-contributor:max`, exactly the final projection in
`opencode-go-muse-cutover-2026-08-19.md` (Responses-only model with its
model-level API override; r5 no-fallback canary already proven on this
selector). LongCat keeps `smol` + `commit`; the earlier worry about burning
the paid LongCat key on high-volume `:max` reasoning no longer applies to
`task`. The muse-spark model-level chain (`fengwind/deepseek-v4-flash` then
`sensenova-6.7-flash-lite`) covers task failures. Backup:
`config.yml.bak-20260819-task-muse`.

## Advisor enabled on the SOTA channel, plan raised to k3:max (2026-08-19, same session)

Capability-layer changes per user decision:

- `advisor` role moved from `zg-newapi-anthropic/claude-opus-5:high` to
  `zg-newapi/omp-sota-claude-opus-5:high` — advisor traffic now rides the
  dedicated SOTA channel ch93, keeping Opus-5 off shared lanes per the
  isolation policy. `advisor.enabled` flipped `false -> true`, so OMP now
  consults Opus-5 for review/risk identification at key checkpoints, matching
  the user's stated SOTA usage policy.
- `plan` raised `zg-newapi/k3:medium -> zg-newapi/k3:max` (`:max` was already
  proven in-config via the `designer` role).

Probe after edit: `omp-sota-claude-opus-5` with `reasoning_effort=high`
returned HTTP 200 in 1.6s via ch93. Backup:
`config.yml.bak-20260819-advisor-plan`. Effective on next OMP restart.

## Rollback

- Relay: copy the `.bak-20260819-upstream-timeout-600s` file over
  `agnes-relay.js` and let the supervisor restart it (or stop the node
  process; the supervisor retries within 60 seconds).
- Roles: copy the newest matching `config.yml.bak-20260819-*` over
  `config.yml` and restart OMP.
- Name: copy `models.yml.bak-20260819-longcat-name-mojibake` over
  `models.yml` and restart OMP.
- ch93: set `channels.status=2` and `abilities.enabled=0` for `channel_id=93`
  (or disable in the NewAPI UI). Pre-change DB backup:
  `~/.new-api-local/new-api.db.bak-20260819-reenable-ch93`.
