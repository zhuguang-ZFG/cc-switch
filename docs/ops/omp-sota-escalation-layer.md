# OMP SOTA escalation layer

## Purpose

OMP uses SOTA models as a bounded upgrade layer for complex or risky work. It
does not make a SOTA model the default/task/slow model, does not add one to
ordinary fallback chains, and does not use one for background compaction. A
marked selector may be assigned to the dedicated `advisor` role; no other role
may route through `omp-sota-*`.

The routing identity is always:

```text
omp-sota-<base-model>
```

The first registered route is `zg-newapi/omp-sota-claude-opus-5`. The marked
alias is hosted by a dedicated single-key NewAPI channel named
`omp-sota-sotamodel`; it is never appended to the multi-key `tabitoken` channel
75. The channel uses the local Clash HTTP proxy at `127.0.0.1:7897` because
direct TLS to the upstream is reset on this host. The marker identifies
traffic; it is not an independent context, multimodal, pricing, or
current-health claim.

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

Automatic child execution has a second, workload-shaped health boundary in
`~/.omp/agent/sota-workload-health.json`. A child killed near the three-minute
deadline increments `consecutiveTimeouts`; two timeout-class executions since
the last success mark that selector `automaticBlocked`. High-risk, complexity,
and rescue triggers then fail closed without another child. An explicit
`/sota*` command may perform one deliberate retry through the breaker, and a
successful child resets the count. The file contains only selector health,
bounded result class, count, and timestamp.

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
Qwen 3.8 27B policy. Raw `sotamodel*` selectors remain forbidden from roles and
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

The explicit capability probe is billable and therefore requires `--run`:

```powershell
python scripts/ops/probe_omp_sota_alias.py
python scripts/ops/probe_omp_sota_alias.py --run --readiness-path $env:USERPROFILE\.omp\agent\sota-readiness.json
```

It first requests an eight-token exact semantic marker, then sends a second
64-token bounded request that must call `report_review` exactly once with the
expected schema and arguments. Both requests require fresh NewAPI log rows
attributed to the marked model and expected channel. Failure updates readiness
to `unavailable`, preventing repeated child calls to a route that answers cheap
text but cannot perform the real review tool shape.

Install the bounded Windows refresh after the isolated channel passes its first
manual probe:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ops/install-omp-sota-readiness-task.ps1
```

The task runs every ten minutes, ignores overlapping instances, and has a
four-minute execution cap. It management-tests only the dedicated
`omp-sota-*` channel, then runs the exact semantic/log probe. A strict failure
marks readiness unavailable and disables that channel; it never mutates ch75.

## Live evidence (2026-08-19)

Current note (2026-08-20): ch93 is disabled by NewAPI auto-ban after current
workload health regressed. This is unrelated to the Muse ch48 repair. Leave it
disabled until both the semantic and forced review-tool probes pass; do not use
its historical HTTP 200 evidence as current readiness.

Isolation note (2026-08-20): despite the "never appended to ch75" contract,
ch75 `tabitoken` had drifted to also carry the marked alias (models +
model_mapping + enabled ability at p50/w8). With ch93 disabled, 11 advisor-role
calls (~30k prompt tokens each, ~¥190 total, 12:20-12:24) silently fell back to
the shared paid pool. The alias was removed from ch75 by direct DB write
(ch75 is multi-key; API PUT would regenerate `channel_info`, see the t1qq
runbook) after backup `new-api-before-ch75-sota-isolation-20260820-123917.db`.
Readback confirmed ch75 no longer lists the alias and the only remaining
ability is ch93 (disabled); a live negative probe returned 503
`No available channel for model omp-sota-claude-opus-5`. The alias now fails
loudly when the dedicated channel is down — no shared-pool fallback.

Strict isolation, both directions (2026-08-20, user decision): ch93 itself was
also narrowed to carry ONLY the marked alias — plain `claude-opus-5` removed
from models and abilities, `test_model` set to the alias (backup
`new-api-before-ch93-strict-isolation-20260820-124826.db`). Regular Opus traffic
can never land on the SOTA channel, and SOTA traffic can never land on shared
channels. `refresh_omp_sota_readiness.py` now management-probes ch93 with the
alias (the channel model_mapping rewrites it to the upstream base model);
mapped aliases are valid in the management test path (same pattern as the
zzzcoding ch92 probe). Verified while disabled: alias request -> 503
`No available channel`, plain `claude-opus-5` still resolves via the normal
pool.

Tooling guards added the same day so the drift cannot silently recur:
`add_omp_sota_newapi_alias.py` now refuses to add the alias to any channel not
named `omp-sota-*`, and refuses ALL operations (including `--remove`) on
multi-key channels — its API PUT would regenerate `channel_info` (verified
live: a dry-run against ch75 is refused; multi-key drift must be cleaned by
direct DB write plus a cache-sync wait). `create_omp_sota_channel.py` /
`create-omp-sota-channel-secure.ps1` now build ch93 alias-only with
`test_model` set to the alias.

- The dedicated single-key `omp-sota-sotamodel` channel is ch93, with the
  marked alias mapped to `claude-opus-5`. After upstream quota recovery, direct
  TLS still failed on this host; adding only the channel-local Clash proxy
  restored HTTP 200 without changing NewAPI globally.
- The existing ch75 `tabitoken` channel remains a separate multi-key Opus pool;
  no SOTA key was added to it.
- Enabled Opus 5 channels 3, 9, and 18 remain registered, but aggregate
  `claude-opus-5` and `claude-opus-5-thinking` probes returned HTTP 503 with
  `No available accounts`; no marked alias was migrated or enabled.
- The backed-up ch93 proxy update is
  `new-api-before-omp-sota-ch93-proxy-20260819-122643.db`. Channel proxy
  readback and management probe passed before enable.
- The marked semantic probe returned exact `OMP-SOTA-OK` over HTTP 200 in
  1.5s; a fresh NewAPI log attributed non-streaming positive usage to ch93 and
  readiness became `ready`.
- A separate real OMP invocation of
  `zg-newapi/omp-sota-claude-opus-5` returned exact `OMP-SOTA-E2E-OK`.
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

## Readiness-task self-DoS fixed (2026-08-21): probe flake was disabling ch93

Symptom: advisor turns failing with 503 `No available channel for model
omp-sota-claude-opus-5` in ~5-7 minute windows all day (1442 occurrences since
2026-08-19), while ch93 was enabled in DB and scheduled channel tests passed.

Root cause: the `OMP SOTA Readiness Refresh` scheduled task (every 10 min,
installed 2026-08-19 — matches the histogram start) runs
`refresh_omp_sota_readiness.py`, which disables ch93 (status=2,
`status_reason="manual operation"`) on ANY probe failure and re-enables it on
the next run whose management probe passes. Two flake sources made probe
failures routine on a healthy channel:

1. Model variance/truncation: marker probe sent `max_tokens=8`, review probe
   `max_tokens=64` against a thinking-heavy upstream; verified live that
   completions of 95-188 tokens are normal, so a strict single-shot match
   fails intermittently (observed `HTTP 200 toolMatch=false`).
2. Log-attribution race: `latest_log_after` polled only 5s for the async
   consume-log row; rows often land later, so `verify_log` failed even after
   both probes passed.

Fix (repository `scripts/ops/probe_omp_sota_alias.py`): both probes now retry
up to 3 attempts with a 5s delay before reporting failure; marker probe
`max_tokens` 8 -> 64, review probe 64 -> 256; `latest_log_after` default poll
window 5s -> 20s. A genuinely dead upstream (nightly
`daily_free_credits_exhausted`, 5xx) still fails all attempts, so real-outage
detection and the strict-isolation contract are unchanged. Verified: unit
tests 7/7 green, live probe 3/3 pass after the fix (was failing ~2/3 before).

Operational note: during a disable window the advisor has no NewAPI-side
fallback by design (strict isolation); the OMP-side chain
(`omp-sota-claude-opus-5` -> muse-free -> hy3-free -> x-preview-f-free) only
helps if those tail models are themselves healthy — on 2026-08-21 19:4x they
were not (Zen 503 + OpenRouter free-tier daily cap), so the advisor still
hard-failed. Tail health is a separate concern tracked in the Ox Alpha
runbook.

## Open-stdin child hang fixed (2026-08-23): every automatic run "timed out"

Symptom: automatic SOTA reviews silently stopped for 57.5h. `sota-workload-health.json`
held `consecutiveTimeouts=2, automaticBlocked=true, lastResult="timeout"` at
`checkedAt=1787217748448` (2026-08-20T09:22:28Z) and 71 subsequent escalation events
were emitted as `result="skipped"` with `failureClass="unhealthy"`. Success rate over
the whole window was 0/163.

Root cause chain (each link measured, not inferred):

1. The escalation child was launched through the host's `pi.exec`, which leaves the
   child's **stdin attached to an open pipe**. An `omp -p` child with open stdin never
   reaches EOF and never exits on its own.
2. The parent therefore always hit its kill ceiling. Measured open-pipe kills:
   180.1s, 180.1s, 300.1s, 300.1s — every one with `stdout_len=0`, so even completed
   model work was discarded.
3. With stdin at EOF the same prompt converges normally: exit 0 in 52.7s / 121.1s /
   154.2s / 172.7s (Node, `stdio` stdin `ignore`) and 52.7s / 173.9s (Python probes).
4. Because every run was scored a timeout, two runs tripped the workload breaker. The
   latch had no expiry and only a _successful_ run could clear it — but the latch
   itself blocked every run, so the state was self-sustaining.

Fix (`scripts/ops/omp-sota-escalation.js`, revision `2026.08.23-sota-r7`):

- `runSotaChild(args, { cwd, timeoutMs })` owns the spawn:
  `spawn("omp", args, { cwd, stdio: ["ignore", "pipe", "pipe"] })`. Closing stdin is
  the actual convergence fix. `runEscalation` resolves it as
  `pi.runSotaChild ?? runSotaChild`, so tests stay injectable while production no
  longer depends on unobservable host stdin behaviour.
- `WORKLOAD_BREAKER_TTL_MS = 60 * 60 * 1000`: an expired latch passes through so one
  probe can re-decide. Only a real success or TTL expiry unblocks; the latch is never
  cleared by hand.
- Ceiling raised to `DEFAULT_TIMEOUT_MS = 300_000` with `--max-time 300`. The measured
  success tail reached 172.7s, so the old 180s ceiling truncated legitimate runs.
  `--max-time` is a hard ceiling, not a flush point: no graceful self-exit exists, so
  convergence is enforced by a prompt budget of at most 8 tool calls, bounded reads
  (grep or <=200 lines), and a 10-line answer cap.
- The timeout classifier stays killed-only:
  `killed === true && durationMs >= Math.max(0, timeoutMs - 5000)`. The speculative
  `killed=false` self-exit branch was removed as dead code; it would have relabelled
  real failures as timeouts and re-inflated the very latch being fixed.

Verification:

- Repo/live SHA-256 parity `2b88c78e3af676cf52fbb8bbb9a36a389b835febb99896bea7b59511adbca07b`.
- Live end-to-end through the **deployed** spawn path, with no test override:
  `result="success"`, `code=0`, `killed=false`, `timedOut=false`, 82.4s wall, one
  `sota-escalation-review` message containing a real review verdict. First success in
  163 attempts. NewAPI logs confirm ch93 served it (8 streaming calls, 3-14s each).
- Breaker cleared through the sanctioned API (`recordWorkloadResult(..., {ok:true})`),
  not by editing the file: `consecutiveTimeouts=0, automaticBlocked=false,
lastResult="success"`.
- Gates: `node --test scripts/ops/test_omp_sota_escalation.js` 23/23 in 124ms (was
  300s+ because unit fixtures were spawning real children); route gates 38/38;
  refresh gates 2/2.

Test-isolation regression fixed at the same time: five `pi` fixtures could reach
`runEscalation` without stubbing the child runner, so `pi.runSotaChild ?? runSotaChild`
fell through to a real `spawn("omp", ...)` with a 300s SIGKILL timer — unit tests were
issuing live model calls (observed 76s and 110s). All fixtures now build their stub
from a shared `makeChildRunner(stdout)` helper, assert against recorded child runs
instead of `execCalls.at(-1)`, and every `exec` stub throws on any non-`git` command so
a future missing stub fails loudly and instantly instead of hanging.

Residual, non-blocking: after the successful run the readiness refresh reported
`management-probe-failed` for ch93 and fail-closed it (`status=2`, by design at
`refresh_omp_sota_readiness.py:84`). The management probe returned a genuine upstream
`429 daily_free_credits_exhausted`, not a flake — 1966 client calls burned 14.9M prompt
tokens on the alias that day. This is real quota exhaustion; the channel returns on the
next refresh whose probe passes.

`status_reason` is not a `channels` column in this schema; it is nested inside
`other_info` and only surfaced via the admin API. `GET /api/channel/93` returned
`{"status_reason":"manual operation","status_time":1787435011}`, i.e. the refresh run
that immediately followed the successful review. Forcing `status=1` by any route would
simply re-ban on the next probe while the upstream quota is still exhausted.

NewAPI access discipline used throughout this investigation, and required for future
ones: NewAPI was live (transient `new-api.db-journal` observed; `journal_mode=delete`,
so there is no WAL to isolate concurrent access). Every database read therefore used
`sqlite3.connect("file:...?mode=ro", uri=True)` — verified by an explicit
`UPDATE channels SET status=1 WHERE id=93` that SQLite rejected with
`attempt to write a readonly database`. No option or channel value was mutated at any
point. Channel state changes must go through the admin API (`POST /api/channel/<id>/status`,
as `refresh_omp_sota_readiness.py:49-56` already does): NewAPI caches options in memory,
so DB-level edits are ignored or overwritten, and writing to the live database risks
lock and journal damage.

### r8: first live rescues drove three trigger/payload fixes (2026-08-23)

r7 上线当天，2 次工具失败路径实弹触发了两次救援。第一次证明链路端到端可用，
第二次暴露了三个真实缺陷，均在 r8（`2026.08.23-sota-r8`）修复：

1. **空证据救援**：无变更文件且 `gatePlan.profile === "none"` 时，救援会启动一个
   完整子进程去"审查空气"。现在该情形直接跳过，路由事件记
   `failureClass="no-evidence"`。
2. **同回合双派发**：failure #2 的 steer 救援不会抑制同一回合的 `agent_end`
   自动升级——此前只靠单目标冷却挡住；一旦配置第二个 SOTA 候选，就会再烧一整份
   子进程预算。现在 `escalatedThisTurn` 标志在回合开始时重置、任意派发置位、
   `agent_end` 入口检查。
3. **载荷缺上下文**：升级提示词原本只有 reason/gate/用户请求/变更文件；零文件时
   评审者无可行动对象。现在携带最近 3 次失败工具的 name/args/error（各经
   `boundedText` 40/200/200 有界+脱敏），untrusted-data 声明同步覆盖失败块；
   `safeExecArgs` 渲染边界独立再做一次脱敏——它是公开导出，不信任调用方预处理。

暂缓项：失败分类过滤（区分瞬态错误与真实缺陷）需要真实失败类型分布数据，
贸然分类有漏掉真救援的风险；继续保守计数全部 `isError`。
