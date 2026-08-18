# OMP model routing observability and adaptive compaction

## Scope

This ops layer makes OMP's existing model resolution visible without adding a
model field to `task` or `scout`. OMP 17.3.7 already renders each settled
subagent's `resolvedModel` when `task.showResolvedModelBadge=true`; the extension
does not duplicate that badge or patch the OMP package.

Repository sources:

```text
scripts/ops/omp-model-routing-observability.js
scripts/ops/omp-global-compaction-model.js
scripts/ops/omp-sota-escalation.js
```

Production extension:

```text
~/.omp/agent/extensions/omp-model-routing-observability.js
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
selectors, duration/usage counters, fallback/result fields, failure class, and
threshold metadata. Prompts, assignments, transcript/output, raw errors, URLs,
headers, API keys, cookies, and image payloads are discarded before
serialization.

Use the opt-in command inside OMP:

```text
/model-routing-status
```

It reports bounded counts and the latest safe selector for `task`, `scout`,
`sota`, and `compaction`, plus the latest offline-refresh state, role hash, and
unresolved/indeterminate role counts. Continue to use `/compaction-status` and
`/sota-status` for their detailed state machines.

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
node --check scripts/ops/omp-global-compaction-model.js
node --check scripts/ops/omp-sota-escalation.js
node --test scripts/ops/test_omp_model_routing_observability.js
node --test scripts/ops/test_omp_model_routing_deploy.js
node --test scripts/ops/test_omp_global_compaction_model.js
node --test scripts/ops/test_omp_sota_escalation.js
```

Deploy with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ops/deploy-omp-model-routing-observability.ps1
```

The deployer creates a timestamped backup or `destination.absent` marker,
stages beside the destination, atomically replaces it, and verifies source and
destination hashes. It records `restartPerformed=false`. A matching file hash
does not prove an existing OMP process loaded the revision; use
`/reload-plugins` or a normal restart and do not kill the active PID.

Rollback restores only `previous.js` (or removes the file when the absence
marker was recorded), verifies the restored hash, and reloads plugins normally.

### 2026-08-19 deployment evidence

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
adds observability and safe policy projection only.
