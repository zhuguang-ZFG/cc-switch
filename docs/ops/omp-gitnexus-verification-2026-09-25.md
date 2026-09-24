# OMP GitNexus and task verification

The later [bug-fix loop upgrade](omp-bugfix-loop-2026-09-25.md) adds safe failure
locations and one opt-in repair continuation. Its stop behavior and rollback
instructions supersede the corresponding original-deployment details below.

## Deployed behavior

OMP can inspect indexed callers/dependencies through GitNexus MCP and run
operator-configured checks through `task_verify`. Verification distinguishes
`passed`, `failed`, `unverified`, and `not-required`; it does not certify general
task completion or replace source review.

The deployment adds only these files/entries under `~/.omp/agent`:

- `extensions/omp-task-verification.js`.
- `task-verification-policy.json`, resolved for `D:\Users\cc-switch`.
- A `gitnexus` entry in `mcp.json`, launching the existing GitNexus CLI with the
  absolute Node executable. All other MCP fields are preserved.

Start a **new OMP session** to load the extension and MCP configuration. Models,
advisor, fallback chains, existing extensions, Guardian and CCS are unchanged.
No global OMP package modification or application rebuild was performed. The
previous slow/Opus review policy remains disabled because its upstream route has
not recovered; this change does not enable it.

## Using the tools

Ask OMP to inspect callers and impact before a change, and invoke
`task_verify({action:"plan"})` / `task_verify({action:"run"})` before reporting
completion. `status` rechecks whether the previous result is still current.

OMP 18.3.0 normally mounts these tools under `xd://`. Read the current tool
directory and write JSON arguments to the advertised device path. Do not guess
paths: `impact` and `route_impact` are different tools. An explicit CLI `--tools`
list can exclude MCP; the native fixture therefore uses normal tool discovery.

For GitNexus, first list repositories, inspect their context/freshness, then use
`context` and `impact` with a repository selector and an unambiguous symbol UID.
The cc-switch index contains roughly 35,000 code nodes. Existing `clideck`
registration and index were preserved.

**Windows limitation:** GitNexus 1.6.5 deliberately skips loading FTS in its
read-only connection pool to avoid a native crash. Consequently `query` returns
empty keyword results with a misleading "FTS indexes missing" warning even
after successful indexing. Rebuilding cannot fix this platform guard. Use `rg`
for keyword search; graph context/impact work. No embeddings were downloaded.

Graph edges are heuristic, not compiler proof: inspection found an unrelated
`.match` callee candidate. Confirm relevant edges against source. A zero-impact
result is not evidence that a Tauri command has no frontend consumers.

Refresh this repository deliberately after commits using:

```powershell
gitnexus.cmd analyze --index-only --name cc-switch
```

`--index-only` avoids generated AGENTS/skill edits. Do not refresh unrelated
repositories automatically. Commit freshness alone does not cover dirty files.

## Verification boundaries

The initial policy covers the named OMP extension regression suites, this
deployment/rollback suite, and `probe_omp_verification.py`. It does not run CCS
builds or claim coverage for renderer/Rust changes. Uncovered code and unknown
repositories remain `unverified`; Markdown-only changes need no executable
check under this policy.

Each user turn records Git HEAD and dirty-file hashes. Unchanged pre-existing
user work is excluded. Checks use only operator-owned policy commands, without
shell interpolation or model-supplied command arguments. The policy matches an
exact canonical repository root. Run OMP from that root.

Checks run sequentially under a shared repository lease, with a 120-second total
execution budget, per-command timeouts and cancellation. Test output stays in
memory; only status, test count, exit code and timing are reported. Successful
exit without passing test evidence is insufficient for Node/Python test suites.
Results are invalidated when tracked/untracked files change during or after a
check, including files committed during the turn. Ignored files and external
service state are outside this snapshot contract.

OMP is instructed to verify before its final answer. A bounded `session_stop`
hook also checks modifications left unverified, without starting another model
turn. Native testing found that OMP 18.3.0 print mode did not deliver `agent_end`;
the implementation therefore awaits `session_stop` instead of scheduling a
timer. This fallback does not rewrite an already generated final answer or turn
the CLI exit status into a CI gate. Use the explicit tool result as evidence.

## Validation

- The installed policy executed all 66 relevant Node regression tests and all
  four deployment tests successfully, then passed the native fixture. It
  reported no uncovered code files for this change.
- Four isolated deployment tests passed: exact preservation/rollback, partial
  write recovery, baseline drift rejection, and rollback drift/corruption checks.
- A native OMP process with a local SSE model fixture passed nine assertions:
  completion, pass, stale result, failure, uncovered code, real GitNexus list,
  context with known callers, impact with known dependants, and automatic
  verification of the final write. No upstream model is used by that fixture.
- Independent deployed-byte/backup checks passed. Existing MCP values,
  configuration hashes and extension hashes were preserved.
- After deployment, all nine listener owners were unchanged, all eight monitored
  services were healthy; Supervisor PID 3696 and Guardian PID 18492 were retained.

An additional **live Kimi smoke** selected `zg-newapi/kimi-for-coding`, read a
random nonce from disk, called GitNexus context and `task_verify plan`, and
returned the exact nonce. Tool-result observations confirmed all three actions,
with zero tool errors; the process exited successfully in 102.39 seconds. It used
the deployed extension/MCP configuration, ephemeral session, and a bounded child
with session fallback disabled. This proves that selected route's tool workflow,
not every model, streaming transport, fallback path or long-duration session.

## Recovery

Pre-change baseline:
`C:\Users\zhugu\.omp\agent\extension-backups\gitnexus-verification-20260925-012602`.

Verified deployment manifest and self-contained `rollback.cmd`:
`C:\Users\zhugu\.omp\agent\extension-backups\verification-20260925-015326-1790272406750378700`.

Run the backup's `rollback.cmd` and start a new OMP session to restore the exact
previous MCP file and remove the two newly installed verification files.
Rollback refuses to overwrite subsequent user changes. Backups contain private
MCP configuration: keep them local and never commit their contents.

Rollback deliberately preserves `.gitnexus` and its global registry. Disconnecting
the MCP makes the new index inert. If index removal is desired, remove only this
repository's registration/index after checking current users; do not restore the
whole historical registry over later additions.
