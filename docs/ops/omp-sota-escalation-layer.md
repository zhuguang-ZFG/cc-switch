# OMP SOTA escalation layer

## Purpose

OMP uses SOTA models as a bounded upgrade layer for complex or risky work. It
does not make a SOTA model the default, does not add one to ordinary fallback
chains, and does not use one for background compaction.

The routing identity is always:

```text
omp-sota-<base-model>
```

The first registered route is `zg-newapi/omp-sota-claude-opus-5`. The marked
alias is hosted by a dedicated single-key NewAPI channel named
`omp-sota-sotamodel`; it is never appended to the multi-key `tabitoken` channel
75. The marker identifies traffic; it is not an independent context,
multimodal, pricing, or current-health claim.

## Runtime behavior

The extension discovers authenticated OMP models whose ids start with
`omp-sota-`. This makes later SOTA additions data-driven: add the NewAPI alias
and OMP model registration, then reload extensions. No candidate list in the
extension needs editing.

One read-only child `omp` process may run when:

- the user explicitly runs `/sota`, `/sota-review`, `/sota-plan`, or
  `/sota-escalate`;
- the request matches a high-risk class such as authentication, secrets,
  production deployment, database migration, routing, concurrency, or billing;
- the request exceeds the configured complexity threshold; or
- at least two tool calls failed in the turn. This rescue path runs immediately
  after failure number two instead of waiting for terminal settle.

The child is ephemeral (`--no-session`), loads no extensions or skills, has a
three-minute limit, and can only use `read,grep,glob,lsp`. It receives a
redacted, bounded request plus at most 40 sanitized changed-file paths. A
successful immediate rescue is delivered as a `steer` message to the current
turn. A successful terminal high-risk/complex/explicit review is delivered as
one `nextTurn` continuation. The extension suppresses automatic SOTA on that
continuation, so review cannot recursively trigger review. The selected main
model and thinking level are never changed, and the child never receives
edit/write/bash tools.

Each turn has a one-run budget and session-local single-flight guard. A strict
child failure cools that marked candidate for five minutes. Cancellation and
local execution failures do not create retry loops. If every candidate is
unavailable, stale, or cooling, the extension records a redacted skipped
reason, fails closed, and leaves the original workflow unchanged.

Production candidate selection also reads
`~/.omp/agent/sota-readiness.json`. The semantic probe updates this file
atomically when `--readiness-path` is supplied. Only a fresh `ready` entry is
selectable; HTTP, semantic, or NewAPI attribution failure writes
`unavailable`. The file stores only selector, status, reason, check time,
channel id, and TTL.

`/sota-status` reports the extension revision, trigger, attempts, successes,
failures, target, cooldowns, and candidate readiness without prompts, URLs,
credentials, or raw provider errors.

## hutuji project automation

When the workspace basename is `hutuji`, the extension captures the dirty-file
set and Git object hashes before the main turn, then captures them again at
terminal settle. Only paths whose working-tree object changed during the turn
are classified. Successful OMP `edit`/`write` result paths are merged into that
delta so mutations in an external firmware repository remain visible even
though Git collection runs from hutuji. Failed tool calls are not treated as
mutations. This prevents an existing dirty tree from repeatedly invoking SOTA
while still detecting another edit to an already-dirty file. Hash failure falls
closed by treating the current changed-file set as new; file contents are never
persisted or included in status output.

The following paths are high-risk signals even when the user prompt is short:

- `scripts/agent_gate.py`;
- protocol, release-readiness, and agent gate/constraint contracts;
- `deploy/**`;
- MCP bitmap, bridge, configuration, G-code, SVG, and server boundaries;
- paths under the external Grbl or xiaozhi firmware repositories.

At settle, the same normalized path set selects a gate plan:

- documentation-only changes -> `python scripts/agent_gate.py --profile docs`;
- repository code/service changes -> `python scripts/agent_gate.py --profile hub`;
- external Grbl changes -> hutuji `full` plus the separate fz `standard` gate.

The `full` plan reports `available=false` when `GRBL_ROOT` is absent. Gate
selection is automatic, but execution remains explicit: the extension never
runs serial, HIL, firmware, deployment, or production commands. The plan is a
non-triggering message and is also available through `/hutuji-gate-status`.

Worker templates live in `scripts/ops/omp-agents/`. `hutuji-worker` is the
primary project worker; `dsv4pro-worker` remains as a compatibility name. Both
use `model: "@task"` rather than a concrete model id, so the current Luna task
role and future task-role replacements are inherited automatically.

Deploy the pair with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ops/deploy-omp-hutuji-workers.ps1
```

The script validates both templates before touching the destination, records a
hash-verified prior copy or absence marker for each file, replaces them
atomically, and restores the pair on failure. Existing OMP sessions require
`/reload` or a normal restart before updated agent definitions are visible.

An xiaozhi firmware path is a SOTA risk signal, but it is not represented as a
successful hutuji `full` gate: that repository separately requires host release
tests, a selected board/variant build, and applicable hardware checks. The
extension must not infer a variant or convert a build into HIL evidence.

## Separation from compaction

When the routing observability extension is loaded, SOTA
start/success/failure/skipped events use the same redacted JSONL route log as
compaction and task/scout. Trigger and failure class are bounded atoms. The
extension does not expose prompts or child output in that log; `/sota-status`
remains the detailed SOTA state view.

Every marked OMP model keeps:

```yaml
compactionModel: zg-newapi/deepseek-v4-flash
```

Ordinary task work and compaction continue to use the existing Flash, GLM, and
LongCat policy. Raw `sotamodel*` selectors remain forbidden from roles and
fallback chains; only the marked `zg-newapi/omp-sota-*` route can enter SOTA
discovery after readiness verification.

## Dry-run and apply

First create or refresh the isolated single-key channel through the secure local
prompt. It keeps the key out of process arguments and repository files:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ops/create-omp-sota-channel-secure.ps1
```

For an already verified independent channel, alias planning is read-only by
default and must name that channel explicitly:

```powershell
python scripts/ops/add_omp_sota_newapi_alias.py --channel-id 93 --base-model claude-opus-5
python scripts/ops/add_omp_sota_newapi_alias.py --channel-id 93 --base-model claude-opus-5 --apply
```

Apply creates an integrity-checked online SQLite backup, preserves the channel
secret in memory, omits `status` from PUT, and verifies the channel plus the
rebuilt enabled ability row. It never prints keys or raw API responses.

Register the same marked id in OMP and deploy the separate extension:

```powershell
node scripts/ops/add_omp_sota_model.mjs --path $env:USERPROFILE\.omp\agent\models.yml
node scripts/ops/add_omp_sota_model.mjs --path $env:USERPROFILE\.omp\agent\models.yml --apply
powershell -ExecutionPolicy Bypass -File scripts/ops/deploy-omp-sota-escalation.ps1
```

The model updater copies input and context capability metadata from
`zg-newapi-anthropic/claude-opus-5`, applies a 16K output cap for the bounded
review workload, creates a timestamped hash-verified backup, and atomically
updates `models.yml`. The cap is an OMP request budget, not an upstream
capability claim. The deployment script independently backs
up and hash-verifies `~/.omp/agent/extensions/omp-sota-escalation.js`. A running
OMP session must use `/reload-plugins` or restart normally; do not kill it.

## Rollback

Remove the NewAPI marker symmetrically:

```powershell
python scripts/ops/add_omp_sota_newapi_alias.py --channel-id 93 --base-model claude-opus-5 --remove
python scripts/ops/add_omp_sota_newapi_alias.py --channel-id 93 --base-model claude-opus-5 --remove --apply
```

Removal also creates a database backup and verifies that both the alias and
ability row disappeared. Restore the recorded `models.yml` backup and the
extension deployment `previous.js` (or remove the extension when the
`destination.absent` marker exists). Do not restore the whole NewAPI database
while the service is running; the automatic channel PUT rollback is the first
recovery path.

## Validation

```powershell
node --check scripts/ops/omp-sota-escalation.js
node --test scripts/ops/test_omp_sota_escalation.js scripts/ops/test_add_omp_sota_model.mjs
node --test scripts/ops/test_omp_hutuji_workers.mjs scripts/ops/test_omp_hutuji_worker_deploy.mjs
node --test scripts/ops/test_omp_sota_deploy.js
python -m unittest scripts.ops.test_add_omp_sota_newapi_alias scripts.ops.test_omp_routes
pnpm typecheck
```

The explicit semantic probe is billable and therefore requires `--run`:

```powershell
python scripts/ops/probe_omp_sota_alias.py
python scripts/ops/probe_omp_sota_alias.py --run --readiness-path $env:USERPROFILE\.omp\agent\sota-readiness.json
```

It requests only eight output tokens and requires both exact semantic output
and a fresh NewAPI log row attributed to the marked model and expected channel.
Failure updates readiness to `unavailable`, preventing repeated child calls to
a known-broken route.

## Live evidence (2026-08-19)

- The dedicated single-key `omp-sota-sotamodel` channel was created as ch93,
  with the marked alias mapped to `claude-opus-5`; it remains disabled because
  its management probe returned upstream HTTP 429 `daily_free_credits_exhausted`.
- The existing ch75 `tabitoken` channel remains a separate multi-key Opus pool;
  no SOTA key was added to it.
- Enabled Opus 5 channels 3, 9, and 18 remain registered, but aggregate
  `claude-opus-5` and `claude-opus-5-thinking` probes returned HTTP 503 with
  `No available accounts`; no marked alias was migrated or enabled.
- The current isolated marked semantic probe returned HTTP 503 while ch93 was
  fail-closed disabled; readiness is `unavailable`.
- Seven historical extension invocations (explicit, rescue, and high-risk)
  all exited with code 1; no successful SOTA review was observed.
- `gpt-5.6-sol` passed an independent exact semantic probe, but it was not
  registered as a SOTA reviewer because it is the current main failure domain
  and would not provide independent review.

## Historical evidence (2026-08-18)

- The pre-change dry-run found ch75 enabled at priority 50 / weight 8 with the
  base model present and the marked alias absent.
- Apply created verified backup
  `new-api-before-omp-sota-ch75-20260818-215527.db` (83,705,856 bytes). Readback
  confirmed the exact alias mapping and one enabled ability row at p50/w8.
- `omp models` resolved exactly one `omp-sota-claude-opus-5` registration.
  Route gates confirmed it is absent from roles and fallback chains and keeps
  DeepSeek Flash as its compaction model.
- The first full OMP probe exposed a real operational constraint: copying the
  base 128K output maximum required a `$0.80` NewAPI precharge while the local
  user had about `$0.21`. The dedicated alias was corrected to a 16K review
  output budget; the next exact OMP probe returned `OMP-SOTA-OK` in 18.2s.
- The independent eight-token probe returned HTTP 200 in 2.3s and a fresh
  NewAPI log row for model `omp-sota-claude-opus-5`, channel 75, non-streaming,
  with positive usage (7203 input / 4 output tokens).
- The production extension SHA-256 matches the repository source. Deployment
  did not restart OMP; existing sessions need `/reload-plugins` or a normal
  restart to load it.
- The hutuji worker pair was deployed from the repository with a verified
  rollback directory at
  `C:\Users\zhugu\.omp\agent\agent-backups\hutuji-workers-20260818-230359-4536`.
  Both live hashes match their repository templates. The backup records that
  `hutuji-worker` was previously absent and preserves the previous
  `dsv4pro-worker` definition.
- The final project-aware extension deployment created rollback directory
  `C:\Users\zhugu\.omp\agent\extension-backups\omp-sota-escalation-20260818-232855-2880`.
  Repository and live SHA-256 are both
  `981C8FF2C84207E688D3FF4FE1231D9E45551AE395AD549A70E37977C6D54ACE`.
- At deployment, OMP PID 16296 predated the final extension and PID 17136
  started afterward. Final readback still finds PID 16296 active, so that
  process needs `/reload-plugins` (or a normal restart) to load revision
  `2026.08.18-sota-r2`; PID 17136 is no longer active. Neither process was
  killed or force-restarted by the rollout.

Upstream unit pricing was not independently verified. The alias inherits the
channel's existing billing and must be monitored by marked-model usage in
NewAPI.
