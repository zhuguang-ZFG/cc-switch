# OMP evidence-driven bug-fix loop

This upgrades the existing problem-solving and task-verification extensions.
It improves the feedback available to the model and tests the repair workflow;
it is not a claim that prompts make every model equally capable.

## Changes

- The working instructions distinguish bug reproduction, competing hypotheses,
  a discriminating experiment, the smallest cause-level fix, and regression
  evidence. Feature work is guided toward a narrow complete flow and boundary
  checks. Existing scoped historical checkpoints remain available.
- Strategy reminders recognize three variant commands failing in the same
  category, in addition to two exact repeats. Nonzero process exit codes count
  even when tool invocation itself is marked successful. Categories distinguish
  missing resources, permission/auth, transport/capacity and code/test failures.
- Failed configured checks return a category, up to six existing repository
  code locations, and an actionable next step. Raw logs, assertion messages,
  test names, source excerpts and credential values are not sent to the model.
- With `repairOnFailure: true`, a normal stop after verified code/test failure
  can request **one** additional model continuation per user turn. It must stay
  within the original user authorization and rerun `task_verify`. It cannot
  authorize policy changes, weakened tests, dependency installation, routing
  changes or permission bypasses. Unknown coverage, infrastructure failures,
  cancellation, missing dependencies and auth failures do not trigger it.

The setting is enabled only for the existing exact cc-switch verification
project. Other projects still need an operator-reviewed policy; unknown files
remain unverified. This is not a general whole-machine auto-fixer. Existing
model roles, advisor, fallback chains, Opus review disablement, MCP config,
global OMP package and CCS are preserved. Start a new OMP session to activate.

## Measured behavior

`node scripts/ops/bench_omp_bugfix.mjs` uses isolated repositories, a local SSE
fixture and the real OMP binary:

| Scenario | Observed result |
| --- | --- |
| Bad pagination patch followed by a valid repair | Verification triggered one continuation; regression and independent holdout passed; four model requests |
| Model continues to claim completion without fixing | One continuation, then stop; three requests; tests remain failing |
| Configured command reports HTTP 403 | No repair continuation; two requests; failure remains visible |

The deterministic fixture proves control flow, not model intelligence.

`node scripts/ops/bench_omp_bugfix.mjs --live` exercised the existing
`zg-newapi/kimi-for-coding` selector with an isolated agent home and a
180-second session cap (190-second outer process bound). Session retry/fallback
was disabled. One run completed in **19.477 seconds**:

- The original implementation failed the four regression tests.
- Kimi's repaired implementation passed the four tests and seven independent
  holdout assertions: partial page, empty input, out-of-range page, fractional
  page/size rejection, input preservation and fresh result allocation.
- Test files and operator policy retained their exact hashes.

This is one bounded task, not an aggregate success-rate or before/after model
benchmark. Expand with real recurring bugs before drawing broader conclusions.
The normal benchmark never invokes a paid model; `--live` is explicit.

The extension regression suite passed **69 tests**, and the upgrade deployment
suite passed three cases covering exact restoration, partial-write rollback and
drift refusal. The existing native GitNexus/verification fixture also passed all
nine assertions. No CCS build or Rust/frontend test suite was needed for these
ops-only changes.

## Deployment and rollback

`scripts/ops/deploy-omp-solving-upgrade.py --previous-manifest <manifest>` checks
the known GitNexus deployment manifest, backs up the two extensions and policy,
then replaces them with verified bytes. It snapshots and checks unchanged model,
MCP, review policy and other extension hashes. It does not restart services.

Use the new backup's self-contained `rollback.cmd` to restore the three previous
files. Rollback refuses to overwrite later user changes. The earlier GitNexus
deployment rollback is for the previous revision and will correctly refuse to
overwrite this upgrade until this upgrade has first been rolled back.

Deployment backup:
`C:\Users\zhugu\.omp\agent\extension-backups\solving-upgrade-20260925-021230-1790273550358993900`.

The stop-hook continuation is a recovery opportunity, not a guarantee. A model
may still misunderstand or fail to fix a bug; final evidence stays failed or
unverified. CLI process exit status is not a substitute for test results.
