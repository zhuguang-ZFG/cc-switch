# OMP problem-solving support (2026-09-25)

OMP receives a strategy-change reminder after the same tool action fails twice,
and can save structured project checkpoints with `problem_checkpoint`.
The approved slow/Opus 5 reviewer is implemented but **disabled**: live probes
returned upstream routing-group errors. It is not yet a working live review service.

## Behavior

- The failure tracker hashes the tool name and canonical arguments in memory.
  It sends one steering message at the second error, retains counters through
  extension continuations, and resets the matching counter after success.
  It does not automatically rerun or block legitimate diagnostic/test commands.
- `problem_checkpoint` saves goal, constraints, evidence, rejected hypotheses,
  verified tests, next step and completion criteria. These are model-authored
  notes, not independently verified proof that a task is complete.
- Files live in `~/.omp/agent/problem-solving-memory/<canonical-cwd-hash>.json`.
  Each field is bounded, lists contain at most six entries, total serialized
  size is capped at 32 KiB, and obvious credential patterns are redacted.
  Never put secrets into checkpoints; pattern redaction is not a guarantee.
- Saves are atomic, retain a `.previous` copy and reject stale concurrent
  writers. Invalid existing memory fails closed. A lock prevents concurrent
  replacement. Reload before replacing a checkpoint changed by another session.
- Context is restored before agent work and after compaction. It is explicitly
  historical, untrusted context; current user intent takes precedence. Records
  older than 30 days are not injected. No whole transcript or raw failed command
  is persisted by this extension.

## Review policy and limits

`sota-review-policy.json` opts into the exact registered selector
`zg-newapi-anthropic/claude-opus-5`; it does not reassign the `slow` role or add
fallbacks. Missing policy preserves the old dedicated `omp-sota-*` behavior.
Disabled, malformed, or unreadable policy selects no reviewer and produces a
diagnostic. The installed policy has `enabled: false` because of the live errors.

When enabled after upstream recovery, existing high-risk/complexity triggers,
repeated failures with reviewable files, and explicit `/sota` commands can start
one review per turn. The reviewer uses a cross-process lease, 300-second hard
child timeout, five-minute failure cooldown and the existing one-hour breaker
after two timeouts. Terminal-review continuations do not recursively review.
Configured eligibility is not a fresh provider-health claim.

The child disables session-level retries and model fallback. The provider's
transport still retries some transient failures; one review is one child, not
one HTTP request. All such attempts remain inside the hard child deadline.
A separately loaded
`review/omp-review-guard.js` allows only `read`, `grep`, and `glob`, rejects paths
outside the workspace and reads without a 1–200-line limit, and caps tool calls
at eight. LSP is excluded because some operations can mutate files. Normal
sessions do not load this guard. Child stdin is closed, output buffers are
bounded, and failures never become a passed review. Review findings still need
the main agent's judgment.

## Validation

- 59 Node checks: SOTA lifecycle, policy, memory, failure strategy, reviewer guard,
  shared routing lease, deployment helper and unexpected-stop compatibility.
- Three deployment tests: verified backup/rollback, partial failure restoration,
  and refusal to overwrite drift.
- Five native OMP/local SSE scenarios with dummy credentials: repeated failure
  steering and checkpoint save; new-process recovery; forbidden write/path/tool
  budget enforcement; actual reviewer child launcher and guard; repeated 503
  responses remain on the selected model and the parent kills the child at the
  test's 20-second deadline. All passed.
- Real Opus probes used Anthropic streaming through local port 3003 (TTFT gateway)
  to NewAPI port 3002, with model fallback and session retry budgets disabled
  (native transport retries still exist). The tool canary
  failed after 10.4 seconds; bounded reviews failed after 19.1 and 7.5 seconds
  with `Failed to resolve routing group` and `API Key is not assigned to any group`.
  `/v1/models` accepted both existing auth header forms (HTTP 200). Local token
  acceptance therefore does not prove upstream generation is healthy. No complete
  live Opus review or per-request channel attribution is claimed. Other successful
  log rows were not attributed to these probes.
- After deployment, the installed checkpoint extension passed a real streaming
  `zg-newapi/kimi-for-coding` read/checkpoint/output check via port 3002 in 44.1
  seconds. This proves basic tool integration, not improved solve rates across
  arbitrary tasks. All nine listener owners, Supervisor PID 3696, Guardian PID
  18492 and eight healthy service states were preserved. Installed/backup hashes
  and unchanged model/config hashes were independently verified.

```powershell
node --test scripts/ops/test_omp_sota_escalation.js scripts/ops/test_omp_problem_solving.js scripts/ops/test_omp_model_routing_observability.js scripts/ops/test_omp_sota_deploy.js scripts/ops/test_omp_unexpected_stop_guard.js
python3 -m unittest discover -s scripts/ops -p test_deploy_omp_problem_solving.py
python3 scripts/ops/probe_omp_problem_solving.py
```

## Deployment and recovery

`python3 scripts/ops/deploy-omp-problem-solving.py` verifies the reviewed
production SOTA base and routing dependency, backs up existing bytes with
size/time/hash checks, records absent destinations, then installs the guard,
SOTA extension, problem-solving extension and disabled policy. Partial failure
restores the complete set. It checks unchanged `models.yml` and `config.yml`
hashes and performs no process restart.

Backups are under `~/.omp/agent/extension-backups/problem-solving-*` and include
a manifest and self-contained `rollback.cmd`. Rollback refuses to overwrite
subsequent user edits. Open a new OMP session to load the new extensions;
running sessions retain their loaded code.

Final deployment manifest:
`~/.omp/agent/extension-backups/problem-solving-20260925-010816-1790269696062573300/deployment.json`.

After an administrator restores the upstream Opus account's routing group,
repeat both the actual tool canary and a bounded read-only review using the
approved selector. Only after both succeed, back up the policy and change
`enabled` to `true`. Do not silently select another paid model or recreate the
removed dedicated channel. CCS binaries/database, NewAPI channel configuration,
model roles, advisor, concurrency and existing fallback chains are untouched.
