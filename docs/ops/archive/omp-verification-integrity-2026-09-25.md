# OMP verification integrity and repeatable bug evaluation

This revision adds evidence protection and more project checks to the existing
OMP verification extension. It does not change CCS, the installed OMP binary,
model roles, advisor, fallback configuration, MCP, or the disabled Opus reviewer.
New OMP sessions load the extension; existing sessions retain their loaded code.

## Integrity contract

At user-turn start, pin the operator policy and hashes of test files, test
configuration, explicit repository runner entry points and protected paths.
Check them before planning/running, between checks, after checks, on cached
status, and before offering the one automatic repair continuation.

Modified/deleted assertions, skipped tests and changed policy commands cannot
produce an automatic pass: **any** protected input change becomes `unverified`
with relative paths and reason codes. This includes changes committed during
the turn. New commands from a changed policy are never accepted during that
turn. Existing unchanged user edits form the baseline. Extension continuations
retain it; a subsequent user turn establishes a new baseline.

This deliberately conservative check also flags legitimate new regression
tests. Explain their changes and obtain review before continuing in a new user
turn. It does not decide whether an assertion change is semantically weaker.
It is not a security sandbox: indirect dependencies, the full dependency tree,
external executables and changes reverted between snapshots are not completely
monitored. Snapshot errors fail closed. Limits are 5,000 protected files, 8 MiB
per file and 32 MiB total; subprocess output is bounded and never returned raw.

## Project coverage

| Project            | Configured verification                                                                  | Measured baseline                                                                                               |
| ------------------ | ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| cc-switch ops      | Extension, deployment and native control tests selected by changed paths                 | 79 Node tests; 4 original deployment tests; 5 upgrade tests; four native scenarios and nine GitNexus assertions |
| cc-switch renderer | TypeScript and complete Vitest suite for `src/`, `tests/` and listed runner/config files | TypeScript passed; 502 tests passed, zero skipped                                                               |
| hutuji             | Offline WeChat mock tests covering `draw-portal/weapp/` and `draw-portal/tests/weapp/`   | 252 passed, zero skipped                                                                                        |
| newapi-aly         | No runnable checkout: `.git` only, unborn HEAD                                           | Unverified; no policy installed                                                                                 |

hutuji has a large unrelated temporary-file tree. Both input snapshots and code
snapshots explicitly limit themselves to the configured small-program paths.
Its report returns `scopedStatus: passed`, `status: unverified` and
`reason: partial-project-coverage`: firmware, backend and production services
are not certified by those tests. No hutuji source/configuration was edited and
no device, OTA, Telegram or production-service calls were part of its suite.

Checks run sequentially with a repository lease and a 120-second aggregate
budget. A budget limit leaves remaining checks unverified. Unknown cc-switch
code paths remain uncovered; no Rust/Tauri/CCS build is part of this policy.

## Evaluation catalog

`scripts/ops/bugfix-cases.mjs` holds three isolated tasks:

- Pagination boundaries: synthetic control case.
- Empty-response canary classification: reduced historical regression from the
  routing-observability tests.
- Preserving existing MCP configuration: reduced historical regression from
  the verification deployment tests.

Every case proves that the original fails, the reference fix passes, and an
empty replacement test cannot satisfy its separate holdout. Reference fixes
and holdouts are not placed in the model's temporary workspace. This separation
prevents accidental exposure; it is not isolation against a hostile model with
arbitrary host-file access.

```powershell
# Offline fixture validation and native OMP control flow; no paid model
node --test scripts/ops/test_omp_bugfix_cases.js
node scripts/ops/bench_omp_bugfix.mjs

# Explicit paid/live sample; select a case or all, repeat 1..3
node scripts/ops/bench_omp_bugfix.mjs --live --case all --repeat 1 --report tmp/omp-evaluations/run-001.json
node scripts/ops/compare_omp_bugfix.mjs tmp/omp-evaluations/run-001.json tmp/omp-evaluations/run-002.json
```

Use a new report filename each time. Reports include case provenance/hash,
model selector, Git revision, implementation hash, elapsed time, unexpected
edits and success. Comparisons require identical live cases, case hashes,
model selectors and repetition counts. They exclude scripted controls. Model
names alone do not establish identical upstream routes, so keep route/config
conditions stable when interpreting comparisons. Existing files are not
overwritten. The normal configured benchmark never uses `--live`.

Live sessions disable retry/fallback and use a 180-second session limit with a
190-second outer bound. No new case starts after ten minutes. Unexpected edits,
test/policy drift, failed regressions or failed holdouts prevent success.
Complete structured usage events can supply token counts and a model-reported
cost estimate. Truncated/missing evidence remains null; estimated prices are
not billed cost. The current route did not provide usable complete usage for
the recorded samples, so their tokens and costs are **unknown**, not zero.

The final live sample used `zg-newapi/kimi-for-coding`: pagination **12.777 s**,
empty-response **12.917 s**, config-preservation **38.101 s**. All three passed
regressions and holdouts without unintended edits; mean **21.265 s**. These are
three small cases, not a general coding success rate or evidence of improvement
over a previous model. The local report is
`.trellis/evaluations/20260925-r3-final.json` (ignored).

The native tampering scenario first fails, enters the permitted repair, then
replaces tests with an empty assertion body. Integrity validation reports
unverified and stops without a second repair. Other scenarios prove successful
repair, bounded repeated failure and no automatic repair for HTTP 403.

## Deployment and recovery

The updater now accepts both the original and subsequent upgrade manifests.
Additional hutuji coverage is explicit via `--hutuji-root`; its test entry
points must exist. Later upgrades preserve already configured extra projects.
Deployment/rollback checks run against temporary homes before live writes.

Verified backup directory:
`C:\Users\zhugu\.omp\agent\extension-backups\solving-upgrade-20260925-025232-1790275952086650900`.

Its self-contained `rollback.cmd` restores the previous two extensions and
policy, verifying backup hashes and refusing later user drift. Nine protected
configuration/extension files retained their hashes. No service restarted.
Supervisor PID 3696, Guardian PID 18492 and all nine listener owners were
unchanged; eight supervisor services were healthy. This is runtime-state
evidence, not an end-to-end test of every NewAPI model or fallback route.

Post-deployment, the actual installed extension ran all selected checks for
the changed ops files with no uncovered files, and the operator forced the
hutuji scoped suite: 252 passed, whole-project status explicitly unverified.
The broader renderer baseline was tested before deployment. Local validation
used the available Node 24.14 runtime; the repository's Node 22 pin and CI Node
20 were not separately exercised.
