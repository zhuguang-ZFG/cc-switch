# OMP global background compaction model

## Purpose

OMP keeps the user's selected main model unchanged and runs context summaries
through a separate authenticated model. The runtime extension applies the same
policy to every current or newly registered main model, so adding or switching
models does not require editing every `compactionModel` field by hand.

Runtime extension:

```text
~/.omp/agent/extensions/omp-global-compaction-model.js
```

Repository source:

```text
scripts/ops/omp-global-compaction-model.js
```

## Candidate order

1. `zg-newapi/deepseek-v4-flash` (SenseNova ch15)
2. `zg-newapi/glm-5.2` (SenseNova ch15, reserved candidate)
3. `zg-newapi/zai-glm-5-2` (Mistral conversations relay ch85)
4. `longcat/LongCat-2.0` (official provider)

Only models present in OMP's authenticated model list are eligible. DeepSeek
remains the normal target. A failed automatic compaction cools that target for
five minutes; the one-second reconciler then projects the next eligible target
onto every model. The extension does not issue an immediate retry, restart OMP,
or change the main model. Aborted, skipped, and user-cancelled compactions do
not trigger a cooldown.

This is deliberately not a remote health probe. It adds no idle traffic and no
retry storm. A model that is absent from the authenticated registry is skipped;
an upstream failure is learned only from OMP's real auto-compaction result.

## Model evidence (2026-08-18)

| Route | Probe | Result |
|---|---|---|
| SenseNova DeepSeek V4 Flash, ch15 | 50,094 prompt tokens, streaming | HTTP 200, TTFT 3.121s, total 3.629s |
| SenseNova GLM 5.2, ch15 | same 50K request | HTTP 429 in 0.593s |
| SenseNova GLM 5.2, ch15 | subsequent small request | HTTP 429 in 0.152s |
| Mistral relay GLM 5.2, ch85 | two small OMP bench runs | average 13.98s |
| Official LongCat 2.0 | two small OMP bench runs | average 3.73s |

The live NewAPI DB shows ch15's `glm-5.2` ability enabled, but enabled metadata
is not proof of usable capacity. Its 429s match the earlier shared-TPM incident,
so the selector is intentionally not exposed in the current OMP registry. The
candidate remains in code so a later, explicitly authenticated model addition
can inherit the policy without another extension edit. LongCat is fast enough
for emergency use, but prior production review rejected it for general summary
quality; it remains last.

## Image history

OMP core serializes ordinary compaction input as text and excludes image bytes.
The extension counts `image` and `image_url` blocks for diagnostics but never
reads, logs, hashes, or forwards their payloads. Logs include only the count and
`imagePolicy=text-only-serialization`. This prevents a text-only compaction
route from receiving binary image history or leaking image URLs.

## Observability

The extension emits structured background logs for:

- target reconciliation and target-unavailable state;
- compaction start with target, main-model selector, token count, and image
  count;
- successful completion with duration and `tokensBefore`;
- failed, aborted, skipped, or stale compaction state.

No normal notification is shown. `/compaction-status` is an opt-in command that
shows the current target and last observed result without exposing transcript,
summary, image, or credential content.

## Validation

```powershell
node --check scripts/ops/omp-global-compaction-model.js
node --test scripts/ops/test_omp_global_compaction_model.js
node --test scripts/ops/test_omp_global_compaction_deploy.js
```

## Deployment

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ops/deploy-omp-global-compaction-model.ps1
```

The deployer creates a timestamped backup (or a destination-absence marker),
stages the file beside the destination, verifies SHA-256 before and after the
atomic replacement, and restores the previous file if deployment fails. Its
`deployment.json` records both hashes and `restartPerformed=false`.

Deployment does not restart OMP. Use `/reload` in a suitable session or allow
the next normal process start to load the new extension.

Rollback uses the `previous.js` or `atomic-replace-backup.js` file in the
reported backup directory. If that deployment recorded
`destination.absent`, remove only the deployed extension after confirming the
exact path.
