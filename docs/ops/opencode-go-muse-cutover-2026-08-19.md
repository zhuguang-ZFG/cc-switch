# OpenCode Go Muse Spark contributor cutover (2026-08-19)

## Decision and current status

The user selected the lower-cost `muse-spark-1.2-contributor` tier after the
data policy was surfaced: OpenCode documents Muse Spark as using prompts and
completions to train future Meta models and as non-ZDR. Consent alone did not
activate the account. Direct Responses requests still returned HTTP 403
`DataPolicyError`, so the attempted cutover was rolled back. Do not silently
apply this route to another account or environment.

OpenCode's gateway source checks the workspace `allow_training` field for this
exact contributor model. The authenticated workspace Go page exposes it as the
`Allow training` toggle backed by `go.allowTraining.set`. This is a console
session action, not an API-key endpoint; do not scrape browser cookies or infer
consent from possession of the Go key.

Muse is a Responses-only model on OpenCode Go. OMP must declare a model-level
`api: openai-responses` override even though the surrounding `zg-newapi`
provider uses `openai-completions`. OMP 17.3.7 with Chat Completions reproduces
the missing-`finish_reason` tool failure tracked by oh-my-pi issue 8957.

## Current production projection

- NewAPI ch48: enabled, `opencode-go-luna`, model `gpt-5.6-luna`, empty
  mapping, priority 51, weight 12.
- OMP roles: `task` is `zg-newapi/gpt-5.6-luna:max`; `tiny` is
  `zg-newapi/gpt-5.6-luna`. `models.yml` contains Luna and no Muse entry.
- The Muse model definition is staged only by the deployer. It uses
  `api: openai-responses`, a 1,048,576-token context declaration, 131,072 max
  output tokens, and text/image input.
- The existing OMP PID was not restarted. Fresh OMP processes load the new
  Canary extension; a pre-existing interactive process keeps its in-memory
  extension/model registry until a normal reload/restart boundary.

## Deployment contract

The repository-owned deployer is:

```text
scripts/ops/cutover_opencode_go_muse.mjs
```

It has three idempotent phases:

1. `stage`: add Muse beside Luna in ch48 and `models.yml`, leave roles intact.
2. Run a direct Responses probe and a native-read nonce Canary with
   `retry.maxRetries=0` and `retry.modelFallback=false`.
3. `finalize`: make Muse exclusive, remove the Luna model, and replace every
   exact Luna selector in `config.yml`.

`finalize` reads the default
`~/.omp/agent/model-tool-canary/model-tool-canary-state.json` and fails before
any mutation unless the exact contributor `:max` selector has a successful
revision-r5 proof less than ten minutes old. `--canary-state` may point to an
isolated state file for testing, but does not weaken revision, selector, result,
or freshness validation.

`rollback` restores the Luna-only channel/model/role projection. Apply mode
requires a pre-change NewAPI database backup so the full ch48 key is hydrated
in memory without printing or storing it in the repository. The deployer
validates ch48 identity, creates byte-hashed OMP file backups, stages atomic
replacements, verifies API/file readback, and restores both layers after a
failed final readback.

Focused gate:

```powershell
node --test scripts/ops/test_cutover_opencode_go_muse.mjs
node scripts/ops/cutover_opencode_go_muse.mjs --phase finalize --accept-contributor-data-policy
```

The second command is a dry-run. Both `stage` and `finalize` require the explicit
data-policy flag; `rollback` does not. Do not finalize until the account has accepted
the contributor data policy in OpenCode Go and the staged no-fallback Canary
passes. Add `--database-backup <path> --apply` only after those gates.

## Production evidence

- Pre-change NewAPI backup:
  `new-api-before-opencode-go-muse-20260819-150304.db`, 85,983,232 bytes,
  SHA-256 `C2B76C20B27A8E6C65CF7CFE800E4EEF7306F65CCEA2181F93CB1480EB9A7037`.
- Current rollback hashes: `models.yml`
  `5E11A9E5119C44B36A545E8F34DD806ACA44434DA6415AB5B31F7156617F8740`;
  `config.yml`
  `CAA074EDB0860CBAF57D80F48D654F3C78CCDA820838E78FC45C1D5BFACC3C59`.
- The original staged Canary appeared to succeed in 24,917 ms, and a later run
  appeared to succeed in 15,688 ms. Those results are invalid Muse evidence:
  the child inherited global `modelFallback=true`, Muse failed, and another
  model completed the nonce under Muse's selector.
- Routing revision r5 injects a per-run no-fallback overlay. Under that
  contract Luna failed in 10,191 ms with `probe-result-missing`, Haiku genuinely
  completed the native read in 85,013 ms, and staged Muse failed in 4,908 ms
  with `probe-result-missing`. No target attribution was invented.
- After the failed Muse proof, `rollback` restored the Luna-only channel,
  `models.yml`, and unchanged role/fallback configuration. A subsequent
  rollback dry-run reported no changes.

## Rollback

Run the deployer with `--phase rollback`, the same pre-change database backup,
and `--apply`. If the management plane is unavailable, restore the timestamped
`models.yml` and `config.yml` backups and use the database snapshot only as the
last-resort NewAPI recovery artifact. Do not replace the whole live database
while NewAPI is running; prefer the channel management API and verify the
Luna-only ch48 readback before sending traffic.

## References

- OpenCode Go model endpoints and privacy table:
  <https://opencode.ai/docs/zh-cn/go/>
- OMP Chat Completions tool failure:
  <https://github.com/can1357/oh-my-pi/issues/8957>
- OpenCode Muse Chat streaming report:
  <https://github.com/anomalyco/opencode/issues/43379>
- OpenCode gateway training-policy check and console toggle:
  <https://github.com/anomalyco/opencode/blob/dev/packages/console/app/src/routes/zen/util/handler.ts>,
  <https://github.com/anomalyco/opencode/blob/dev/packages/console/app/src/routes/workspace/%5Bid%5D/go/lite-section.tsx>
- Independent Responses-stream report:
  <https://github.com/NousResearch/hermes-agent/issues/89836>
