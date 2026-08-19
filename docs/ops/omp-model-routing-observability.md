# OMP model routing observability and adaptive compaction

## Scope

This ops layer makes OMP's existing model resolution visible without adding a
model field to `task` or `scout`. OMP 17.3.7 already renders each settled
subagent's `resolvedModel` when `task.showResolvedModelBadge=true`; the extension
does not duplicate that badge or patch the OMP package.

Repository sources:

```text
scripts/ops/omp-model-routing-observability.js
scripts/ops/omp-model-tool-canary-probe.js
scripts/ops/omp-global-compaction-model.js
scripts/ops/omp-sota-escalation.js
```

Production extension:

```text
~/.omp/agent/extensions/omp-model-routing-observability.js
~/.omp/agent/canary/omp-model-tool-canary-probe.js
```

## Structured Route Log

When the observability extension is loaded it registers a process-local writer
used by the SOTA and compaction extensions. Records are appended to:

```text
~/.omp/agent/logs/omp-model-routing.jsonl
```

The file is capped at 2 MiB. Before a new record would exceed the cap, the
previous file is moved to `omp-model-routing.jsonl.1` and the active file is
recreated. A failed write is a local diagnostic only and never changes routing.

Safe fields are limited to `route`, `role`, `roleHash`, configured/resolved
selectors, duration/usage counters, fallback/result fields, failure class,
threshold metadata, safe job IDs/ages, hashed gateway request IDs, and numeric
channel IDs. Prompts, assignments, transcript/output, raw errors, URLs, raw
headers/request IDs, API keys, cookies, and image payloads are discarded before
serialization.

Use the opt-in command inside OMP:

```text
/model-routing-status
```

It reports bounded counts and the latest safe selector for `task`, `scout`,
`canary`, `watchdog`, `sota`, and `compaction`, plus the latest offline-refresh
state, role hash, and unresolved/indeterminate role counts. Continue to use the
dedicated status commands for their detailed state machines.

Every task call records a redacted `started` event from only the normalized
agent type and role hash. Detached jobs therefore remain visible even when no
terminal `TaskToolDetails.results` payload reaches the parent session. The
extension never serializes the task/context fields and does not depend on OMP's
private EventBus.

## Role Refresh And Validation

At session start, before each agent turn, and immediately before a `task` tool
call, the extension calls `ctx.modelRegistry.refresh("offline")`. This reloads
local model definitions and cached discovery without provider traffic. It then
reads the current effective roles from the optional settings API used by the
installed `omp-model-profile` plugin, hashes the sorted role/value JSON, and
checks exact selectors against the authenticated model registry.

Validation is diagnostic-only. An unresolved role is logged with its hash and
count, while native OMP auth fallback and task execution remain responsible for
the final resolution. Role aliases (`@task`), wildcard/fuzzy patterns, and
thinking suffixes are reported as indeterminate or normalized. Adding a future
model or changing `modelRoles.task` does not require editing this extension or
worker templates.

## Real Tool-Call Canary

The extension dynamically discovers the effective `default`, `task`, and
`smol` selectors plus every authenticated `omp-sota-*` model. A selector is
tested when first seen, after a seven-day successful TTL, or after a 30-minute
failed TTL. Automatic sweeps are serialized by a process promise and an
exclusive, ten-minute stale lease under:

```text
~/.omp/agent/model-tool-canary/
```

Each test launches a two-minute, no-session child with extensions and skills
disabled except for the explicit probe extension. Only the native `read` tool
is enabled. The model must read an unpredictable nonce file and return the
exact nonce. The probe independently observes the structured tool arguments,
tool result, final assistant text, and provider response metadata.

The child also receives an ephemeral `--config` overlay setting
`retry.maxRetries: 0` and `retry.modelFallback: false`. The overlay is removed
with the nonce and result files in every terminal path. This is an attribution
boundary: a fallback model completing the nonce must not be recorded as success
for the requested selector.

State contains only result class, timing, selector, a hashed request ID, and a
numeric channel ID when the gateway exposes one. A request-ID hash is an
attribution anchor, not proof of a specific NewAPI channel; unavailable channel
headers are reported as such and never inferred.

Commands:

```text
/model-tool-canary                  # force all managed selectors
/model-tool-canary <selector>       # force one authenticated selector
/model-tool-canary-status           # cached redacted results
```

## Coordination Guard And Agent Watchdog

Every flat or batched task assignment receives an idempotent contract: workers
operate independently, report to Main, do not build nested coordination trees,
and yield partial evidence before their budget expires. `hub send await=true`
is rewritten to fire-and-forget, peer-specific waits are blocked, and passive
job waits are capped at 15 seconds. Process supervision waits with `name` are
unchanged.

The watchdog reads only public task progress and async-job snapshots. It flags:

| Condition | Threshold |
| --- | ---: |
| In-flight `web_search` | 2 minutes |
| No progress signature change | 5 minutes |
| Total background task age | 15 minutes |

One incident emits one redacted event and a hidden steer message. That wakes a
blocked parent wait and instructs Main to inspect `hub jobs`, cancel the stale
job when appropriate, retain partial evidence, and continue. Further passive
waits are blocked until progress resumes or the job settles. OMP 17.3.7 does
not expose a public extension cancellation method, so the watchdog deliberately
does not reach into private `AsyncJobManager` state or claim that detection
itself killed the process.

Use `/agent-watchdog-status` for the safe incident snapshot.

## Model-Specific Compaction Thresholds

The existing background compaction extension keeps its Flash, GLM, and LongCat
candidate order and selected main model. When both native settings remain at
their defaults (`compaction.thresholdPercent=-1` and
`compaction.thresholdTokens=-1`), it applies a runtime token threshold based on
the active model's context window:

| Context window | Managed threshold |
| ---: | ---: |
| up to 272K | 70% |
| 272K to 400K | 78% |
| 400K to 512K | 82% |
| above 512K | 85% |

The threshold is recalculated on session start and before every agent turn. OMP
17.3.7 does not expose a `model_select` extension event, so a manual model switch
is picked up at the next `before_agent_start` boundary. An explicit user value
clears the extension's runtime override and is reported as
`ownership=user-configured`. The selected provider/id and `compactionModel`
candidate are never changed. `/compaction-status` includes the active ownership,
context window, percentage, and token threshold.

## Deployment And Rollback

Run local gates first:

```powershell
node --check scripts/ops/omp-model-routing-observability.js
node --check scripts/ops/omp-model-tool-canary-probe.js
node --check scripts/ops/omp-global-compaction-model.js
node --check scripts/ops/omp-sota-escalation.js
node --test scripts/ops/test_omp_model_routing_observability.js
node --test scripts/ops/test_omp_model_tool_canary_probe.js
node --test scripts/ops/test_omp_model_routing_deploy.js
node --test scripts/ops/test_omp_global_compaction_model.js
node --test scripts/ops/test_omp_sota_escalation.js
```

Deploy with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ops/deploy-omp-model-routing-observability.ps1
```

The deployer treats the routing extension and explicitly loaded probe as one
pair. The probe lives outside `extensions/` so the main session cannot discover
it; only the bounded child loads it with `-e`. The deployer creates a
timestamped backup or absence marker for each, stages both beside their
destinations, atomically replaces each file, removes and backs up any legacy
top-level discovered probe, verifies both hashes, and restores every previous
state if any step fails. It records
`restartPerformed=false`. A matching file hash does not prove an existing OMP process loaded the revision; use
`/reload-plugins` or a normal restart and do not kill the active PID.

Rollback restores `previous.js` and `previous-probe.js` (or removes files whose
absence markers were recorded), restores `legacy-discovered-probe.js` when a
legacy top-level probe was migrated, verifies the restored hashes, and reloads
plugins normally.

### 2026-08-19 deployment evidence

The orchestration-hardening deployment produced this verified pair:

| Artifact | SHA-256 |
| --- | --- |
| routing r5 | `F25A53622C6CC973EBBEB948C2FFE53260436EF9F684E831BCC835F2DEFD64A0` |
| explicit canary probe r1 | `259FF0F22CEC6EC9D84C5A3CDEC08C50D63C3B32FFDE79ADB8A1F98824701A62` |

The manifest and rollback artifacts are under:

```text
C:\Users\zhugu\.omp\agent\extension-backups\omp-model-routing-observability-20260819-160019-27604
```

The manifest records `restartPerformed=false`, matching source/live hashes for
both artifacts, and `legacyProbeRemoved=false` because the legacy top-level
probe was already absent. Ambient extension discovery therefore cannot load
the probe. The earlier isolated RPC PID 14536 registered
`/model-routing-status`, `/model-tool-canary`, `/model-tool-canary-status`, and
`/agent-watchdog-status`, with zero missing commands and zero extension-load
errors, then exited normally.

The first live window timed out on Luna and Haiku. Later Muse runs appeared to
pass in 24,917 ms and 15,688 ms, but investigation showed the Canary inherited
global model fallback: Muse's failure was followed by another model completing
the nonce. Those records are historical false positives and are not target
model evidence.

Revision r5 disables retries and model fallback in every Canary child. With the
corrected contract, Luna failed in 10,191 ms, Haiku genuinely completed the
native read in 85,013 ms, and staged Muse initially failed in 4,908 ms while
the workspace training setting was disabled. After the user enabled that
setting, `zg-newapi/muse-spark-1.2-contributor:max` passed staged and post-final
native-read Canaries in 14,511 ms and 8,922 ms. Independent NewAPI logs
attributed the successful requests to ch48, and a final production-shaped Muse
Responses smoke returned HTTP 200 in 3,212 ms. The final projection removes
Luna from ch48, OMP models, roles, and fallbacks. See
`docs/ops/opencode-go-muse-cutover-2026-08-19.md` for the guarded cutover,
final hashes, and rollback artifact.

Pre-existing interactive PID 13308 remained responsive and was not restarted or
reloaded, so it does not yet prove the r5 runtime behavior. The following older
routing/compaction/SOTA deployment evidence remains historical context.

The final repository and production extension hashes matched:

| Extension revision | SHA-256 |
| --- | --- |
| routing r3 | `91306F33571D1191F70A5674A7165B78454E9A872C574654D416F324B544DAD8` |
| compaction r4 | `8BF6571A1BB0DF270B986A1411A41CD3B3438DC2E8C85EF8A8C9005E0B65D4B5` |
| SOTA r3 | `A8F83CFFD5ADFE5B548AEEF27E798B95DB29398295672A6977AAC3D5CE919DC3` |

Rollback copies and manifests are under:

```text
C:\Users\zhugu\.omp\agent\extension-backups\omp-model-routing-observability-20260819-005342-9088
C:\Users\zhugu\.omp\agent\extension-backups\omp-global-compaction-model-20260819-004936-23864
C:\Users\zhugu\.omp\agent\extension-backups\omp-sota-escalation-20260819-004940-6724
```

Each manifest records `restartPerformed=false`, and each `previous.js` hash
matches the preceding deployment. A fresh `omp --mode rpc --no-session` process
(temporary PID 13164) registered `/model-routing-status`,
`/compaction-status`, and `/sota-status` with zero extension-load errors, then
exited normally. The pre-existing PID 16296 remained responsive and was not
reloaded or restarted, so this evidence does not claim that old session has
loaded these final revisions.

## Community Evidence

The design follows upstream [PR #8864](https://github.com/can1357/oh-my-pi/pull/8864)
(refresh roles before discovery), [Issue #4736](https://github.com/can1357/oh-my-pi/issues/4736)
and [Issue #6546](https://github.com/can1357/oh-my-pi/issues/6546) (surface
resolved subagent models), [Issue #5018](https://github.com/can1357/oh-my-pi/issues/5018)
(usage-aware role fallback), and [Issue #3812](https://github.com/can1357/oh-my-pi/issues/3812)
(bounded subagent concurrency). The installed OMP release already includes the
native resolved-model fields and execution-time agent discovery, so this layer
adds observability and safe policy projection only. The orchestration guard also
addresses [Issue #6032](https://github.com/can1357/oh-my-pi/issues/6032)
(peer wait deadlocks), [Issue #8711](https://github.com/can1357/oh-my-pi/issues/8711)
and [Issue #8956](https://github.com/can1357/oh-my-pi/issues/8956) (stale
background/search jobs), and [Issue #7954](https://github.com/can1357/oh-my-pi/issues/7954)
(custom-model tool dialect inference), and
[Issue #8957](https://github.com/can1357/oh-my-pi/issues/8957) (Muse on the
wrong Chat Completions dialect), and OpenCode
[Issue #43379](https://github.com/anomalyco/opencode/issues/43379) (Muse Chat
streams closing without `finish_reason`).
