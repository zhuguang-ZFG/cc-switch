# OMP fallback and waiting limits, September 25, 2026

The user approved removing unavailable/shared-channel fallback hops, bounding
waiting, and improving failure diagnostics. The existing `smol` primary repeat
is intentional and remains unchanged. No CCS binary/database/schema, globally
installed OMP package, model roles, provider concurrency, or NewAPI configuration
was changed. Existing OMP sessions were not terminated.

## Configuration applied

| Setting | Before | After |
| --- | --- | --- |
| `retry.fallbackChains.slow` | Opus 4.8, K3 | K3 |
| `retry.fallbackChains.plan` | Opus 4.8, Intern S2 | Intern S2 |
| `retry.fallbackChains.zg-newapi/kimi-for-coding` | K3, DeepSeek V4 Flash | DeepSeek V4 Flash |
| `providers.streamFirstEventTimeoutSeconds` | provider/environment default | 120 |
| `providers.streamIdleTimeoutSeconds` | provider/environment default | 60 |
| `retry.maxDelayMs` | 90000 | 60000 |
| `task.maxRuntimeMs` | 1800000 | 900000 |

At deployment, Opus 4.8 had zero enabled NewAPI channels; Kimi and K3 both used
channel 33. The existing DeepSeek fallback used channel 118, and Intern S2 used
66/67. These are enabled-channel observations, not a claim that different
NewAPI channels survive an outage of the common NewAPI process/database.

`harden_omp_fallbacks.py` checks this posture before applying. It edits only the
reviewed fields, preserves comments and unrelated YAML values, rejects duplicate
keys/unreviewed values, verifies backup/readback bytes, and defaults to a redacted
dry run. The default fallback gate permits this exact Kimi-to-DeepSeek exception;
reasoning-role DeepSeek exclusions remain in force. The advisor gate now accepts
the existing Omen Alpha role alongside the previously allowed low-cost GLM route.

First-event timeout is a native stream/request-stage budget; it is not a total
wall-clock bound for an entire interactive conversation or fallback chain.
Native SDK retries can add attempts. The 15-minute task limit caps the whole
subagent run; `retry.maxRetries=3` and task concurrency 4 remain unchanged.

## Diagnostics deployed

Routing extension r7 and canary probe r2 distinguish child startup, auth,
rate-limit, unavailable-model, gateway-database, transport, timeout, empty-output,
invalid/missing artifact, missing tool, and invalid tool/final-output proof.
Raw errors/output and credentials are never persisted. Probe r2 records terminal
no-tool evidence using the expected path from the generated CLI prompt and
normalizes equivalent Windows paths.

## Validation

- 40 live route checks passed without skips.
- 5 configuration/backup/drift/rollback tests passed.
- 18 routing/watchdog/canary tests, 4 probe tests, and 2 deployment rollback tests
  passed. The deployment failure case intentionally triggers a replacement error.
- An isolated OMP home and local HTTP fixture verified native streaming behavior:
  primary 503 reached the fallback (3.41 seconds, 4 primary attempts plus 1
  fallback); a one-second first-event budget exited with timeout (3.44 seconds,
  2 attempts); a one-second idle budget exited with timeout (1.94 seconds,
  1 attempt). These are fault-injection budgets, not production timeout values.
- Before application, real native-read canaries passed for DeepSeek V4 Flash
  (28.3 seconds) and Intern S2 (18.9 seconds).
- After application and probe deployment, the same nonce/tool/final-output
  checks passed for Kimi for Coding (19.7 seconds) and K3 (49.3 seconds).
  Real probes disabled retries and model fallback. No production outage was
  forced to test failover; actual failover was exercised only against the local
  fixture. Response headers still supplied no channel/request attribution.
- Native `omp config list --json` confirmed all four timeout values. Installed
  extension/probe hashes match repository bytes. All nine listener owners and
  Supervisor/Guardian PIDs were unchanged; all eight supervised services healthy.

## Activation and rollback

New OMP sessions read the changes. Reload/restart an existing session when ready;
do not terminate active work just to activate this configuration.

Verified configuration backup:
`~/.omp/agent/backups/fallback-stability-20260925-002655/`.
From the repository, restore it with:

```powershell
python3 scripts/ops/harden_omp_fallbacks.py --rollback "$env:USERPROFILE/.omp/agent/backups/fallback-stability-20260925-002655"
```

Rollback refuses to overwrite subsequent user edits. Inspect/merge such edits
instead of forcing replacement. To restore the pre-change extension pair, use
`previous.js` and `previous-probe.js` from:
`~/.omp/agent/extension-backups/omp-model-routing-observability-20260925-002707-25068/`.
Pass those as `-SourcePath` and `-ProbeSourcePath` to the existing verified
`deploy-omp-model-routing-observability.ps1` deployer, then reload OMP.

Opus 4.8 is removed only from automatic fallback lists, not from model
registration. Reintroduction requires fresh enabled-channel evidence and
successful direct text/tool probes; a channel becoming enabled alone is not
enough. Database contention, SOTA readiness refresh, and storage retention are
separate outstanding work and were not changed in this rollout.
