# OpenCode Go Muse Spark contributor cutover (2026-08-19)

## Decision and current status

The user selected the lower-cost `muse-spark-1.2-contributor` tier after the
data policy was surfaced: OpenCode documents Muse Spark as using prompts and
completions to train future Meta models and as non-ZDR. The first attempt was
rolled back because the workspace had not enabled training and direct Responses
requests returned HTTP 403 `DataPolicyError`. After the user enabled the
workspace setting, the guarded cutover completed. Do not silently apply this
route to another account or environment.

OpenCode's gateway source checks the workspace `allow_training` field for this
exact contributor model. The authenticated workspace Go page exposes it as the
`Allow training` toggle backed by `go.allowTraining.set`. This is a console
session action, not an API-key endpoint; do not scrape browser cookies or infer
consent from possession of the Go key.

Muse is a Responses-only model on OpenCode Go. OMP must declare a model-level
`api: openai-responses` override even though the surrounding `zg-newapi`
provider uses `openai-completions`. OMP 17.3.7 with Chat Completions reproduces
the missing-`finish_reason` tool failure tracked by oh-my-pi issue 8957.

**Current status (2026-08-20 recovery recheck):** the configured projection remains
Muse at p51/w12 and ch48 is currently `status=1` with its matching ability
enabled. The earlier three country-403 probes and Guardian quarantine remain
the historical failure record; Guardian later recovered the fixed route. Two
fresh forced streaming Responses probes passed, followed by a fresh aggregate
Responses request that returned exact `OK`, positive usage, and a NewAPI log
row attributed to ch48. This is current recovery evidence, not a guarantee of
long-term regional availability; Guardian remains the sole owner of future
status transitions.

## Configured projection and current posture

- NewAPI ch48: `opencode-go-muse`, model `muse-spark-1.2-contributor`, empty
  mapping, priority 51, weight 12. Current readback is `status=1` with the
  matching ability enabled; a quarantine keeps the same p51/w12 tier while
  setting status and ability routing off.
- OMP roles: `task` is `zg-newapi/muse-spark-1.2-contributor:max`; `tiny` is
  the base contributor selector. `models.yml` contains the Muse model and no
  Luna entry; `config.yml` contains no Luna selector in roles or fallbacks.
- The Muse model uses `api: openai-responses`, a 1,048,576-token context
  declaration, 131,072 max output tokens, and text/image input.
- The existing OMP PID was not restarted. Fresh OMP processes load the final
  Muse projection and r5 Canary extension; a pre-existing interactive process
  keeps its in-memory extension/model registry until a normal restart boundary.

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

The dedicated posture repair is
`repair_muse_channel_posture.py [--apply]`. It changes only ch48 channel and
ability priority/weight, preserves status and BLOB `channel_info`, creates an
integrity-checked online SQLite backup, and rolls back on readback or probe
failure. If ch48 is enabled it requires two forced streaming Responses probes,
an aggregate semantic/usage probe with enough output budget for reasoning, and
fresh NewAPI log attribution before the write; an explicit Responses
`incomplete` status is rejected even when usage is positive. An already-disabled
ch48 remains disabled and is never probed through an aggregate route during
posture repair.

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

- Final pre-change online NewAPI backup:
  `new-api-before-opencode-go-muse-finalize-20260819-161700.db`, 86,179,840
  bytes, `PRAGMA integrity_check=ok`, SHA-256
  `BB370CECB7C3ED1D1D3F2DF758DF1CEC9F575D3DA25D7C2AB182EFCC28235D23`.
- Final OMP hashes: `models.yml`
  `ABA7B872AFE682A7566D74E0E153E0E30DA356FDAF20A1E066E62E3A57A39BB2`;
  `config.yml`
  `11D4D36442FAD14411DB03BB6013BE7B669E87A2F9E9388BF81F56D9F6F431F2`.
- The original staged Canary appeared to succeed in 24,917 ms, and a later run
  appeared to succeed in 15,688 ms. Those results are invalid Muse evidence:
  the child inherited global `modelFallback=true`, Muse failed, and another
  model completed the nonce under Muse's selector.
- Routing revision r5 injects a per-run no-fallback overlay. Before the
  workspace opt-in, staged Muse correctly failed in 4,908 ms rather than being
  rescued by another model. After opt-in, the staged Muse native-read Canary
  succeeded in 14,511 ms and the post-final Canary succeeded in 8,922 ms.
- Independent NewAPI log rows attribute both successful verification windows
  to `channel_id=48`. Response headers still exposed no numeric channel ID, so
  Canary status honestly reports `gatewayAttribution=missing`.
- The final production-shaped `/v1/responses` smoke returned HTTP 200 in
  3,212 ms. The aggregate smoke still reported unrelated pre-existing posture
  drift on ch39, ch91/92, and ch9/18; those channels were not changed here.
- Final readback found zero Luna references across `models.yml` and
  `config.yml`, seven Muse references, one exclusive ch48 Muse ability at
  priority 51 / weight 12, and a no-change finalize dry-run.

## Guardian isolation evidence (2026-08-20)

- Pre-repair NewAPI snapshot:
  `new-api-before-muse-posture-20260820-004916.db`, 87,875,584 bytes,
  `PRAGMA integrity_check=ok`, SHA-256
  `F4EDC4690904E9D9766790FB0E8F28746B6A3434BD98164622EA938F50AA4172`.
- Guardian repository/runtime SHA-256 matched at verification:
  `D4A513F866602458E11116EA554FBCBEC93076B0D9E99FE40A075691C9E6D887`.
- The three forced ch48 probes returned the same country 403. Guardian disabled
  ch48 after the bounded soft-failure threshold and retained p51/w12 for future
  recovery instead of dynamically changing the route weight.
- The OMP task role remains
  `zg-newapi/muse-spark-1.2-contributor:max`. Its ordered fallbacks are
  `fengwind/deepseek-v4-flash` then
  `zg-newapi/sensenova-6.7-flash-lite`.
- A fresh `--no-session --no-tools` OMP probe returned exact
  `OMP_MUSE_FALLBACK_OK` in about 49.4 seconds while ch48 stayed disabled.
  NewAPI had no successful Muse or SenseNova row in that window, so the first
  direct fallback completed the request. This proves fallback behavior only.

## Recovery and OMP configuration evidence (2026-08-20)

- The truncated live OMP files were preserved before recovery:
  `config.yml.truncated-20260820-023237.bak` (75 bytes) and
  `models.yml.truncated-20260820-023237.bak` (67 bytes). Their hashes were
  independently checked before replacement.
- The live pair was restored from the verified historical projection, then the
  exact dead `zg-newapi/agnes-2.0-flash` fallback was removed by the repository
  editor. The editor created
  `config.yml.20260820-023527-before-dead-fallback.bak` and passed the 37/37
  OMP route gate.
- Current live hashes are `config.yml`
  `D657FB593B7B8C4256706FE86DCFE3430CD5CF0114DDE2C972DD66FC25521806` and
  `models.yml`
  `ABA7B872AFE682A7566D74E0E153E0E30DA356FDAF20A1E066E62E3A57A39BB2`.
  The active role/fallback projection includes Muse, the Anthropic semantic
  gateway, Qwen, and the marked SOTA advisor registration.
- The first post-recovery aggregate probe used only 128 output tokens and
  correctly exposed an `incomplete` response because low-effort reasoning
  consumed the budget. The posture probe now uses a 512-token bound and
  explicitly rejects incomplete Responses. The corrected live probe passed
  exact `OK`, positive usage, and ch48 log attribution; no key or response body
  was persisted.

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
