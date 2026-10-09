# OMP Unexpected-Stop Guard

Updated: 2026-08-07

## Purpose

`scripts/ops/omp-unexpected-stop-guard.js` prevents an OMP main-agent turn from settling after an explicit immediate-action promise when the same assistant message contains no tool call.

The incident signature was:

```text
stopReason=stop
hasToolCalls=false
text=我会继续，结合 MCP 官方规范、Python SDK、GitHub issue/PR 和社区实现重新校准 findings，并区分规范缺陷与测试质量问题。
```

OMP itself remained healthy. The model returned a valid terminal turn, so the ordinary runtime treated the promise as the final answer.

## Design

The guard uses OMP 17.2.10's supported `session_stop` extension event. It does not patch the globally installed OMP package.

The deterministic classifier:

- accepts explicit Chinese and English immediate-action promises;
- rejects turns with tool calls or non-`stop` termination;
- rejects negated actions, conditional offers, questions, completion claims, and concrete blockers;
- limits one uninterrupted continuation chain to three turns, below OMP's internal cap of eight;
- injects a hidden instruction to perform the next concrete action instead of narrating another promise.

OMP's built-in `features.unexpectedStopDetection` remains disabled to prevent duplicate continuation mechanisms. Its model-based classifier is broader but has documented false-positive and token-cost risk.

## Source And Production Copy

| Role             | Path                                                   |
| ---------------- | ------------------------------------------------------ |
| Source of truth  | `scripts/ops/omp-unexpected-stop-guard.js`             |
| Regression tests | `scripts/ops/test_omp_unexpected_stop_guard.js`        |
| Production copy  | `~/.omp/agent/extensions/omp-unexpected-stop-guard.js` |

After a source change, run:

```powershell
node --test scripts/ops/test_omp_unexpected_stop_guard.js
node --check scripts/ops/omp-unexpected-stop-guard.js
```

Back up any existing production file, copy the tested source, and verify that source and destination SHA-256 hashes match. A running OMP process must execute `/reload` or be reopened before it loads a newly deployed extension.

## Deployment Evidence

Deployed on 2026-08-07 to `C:\Users\zhugu\.omp\agent\extensions\omp-unexpected-stop-guard.js` without changing the global OMP 17.2.10 package. The repository and production SHA-256 values both were:

```text
2DE0BC48A892C6B1F2BF618D801FF0C48E4775A706CBB7029F9BB0FCFDF61E3D
```

No production file existed before deployment. The rollback artifact is:

```text
C:\Users\zhugu\.omp\agent\extension-backups\omp-unexpected-stop-guard-20260807-204710\predeploy-absent.marker
created/modified: 2026-08-07T20:47:10.9544750+08:00
size: 0 bytes
SHA-256: E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855
```

An isolated load smoke returned `OMP_GUARD_LOAD_OK`. A production behavior smoke emitted `unexpected-stop guard requested continuation` in `C:\Users\zhugu\.omp\logs\omp.2026-08-07.4124.log`, then settled normally when the following turn reported a concrete blocker. This proves one trigger and one non-looping stop; it is not a long-duration Goal Mode test.

No OMP TUI was running at final verification. The next OMP launch will auto-discover the deployed extension; an already running process would require `/reload`.

## Operational Use

- Ordinary obvious abandoned promise: the extension schedules a hidden continuation.
- Long autonomous task: start it with `/goal set <objective>`; the guard is a fallback, not a replacement for Goal Mode.
- Real blocker: the model should state the blocker and ask only for the required input; blocker language deliberately stops the guard.
- Unexpected loop: press Escape, then remove or restore the production extension and reload OMP.

## Rollback

If a previous production file existed, restore the timestamped backup and verify its hash. If the deployment created the file for the first time, remove only `~/.omp/agent/extensions/omp-unexpected-stop-guard.js`. Reload OMP after rollback.

## Upstream Evidence

- [can1357/oh-my-pi#3695](https://github.com/can1357/oh-my-pi/pull/3695): proposes deterministic local auto-continuation for obvious English promises; still open when this guard was added.
- [can1357/oh-my-pi#6540](https://github.com/can1357/oh-my-pi/issues/6540): documents false positives and paid-turn waste from model-classifying legitimate final answers.
- [can1357/oh-my-pi#5264](https://github.com/can1357/oh-my-pi/issues/5264): documents an active Goal Mode continuation stall after compaction.
- OMP 17.2.10 source: `SessionStopEvent`, `SessionStopEventResult`, and the internal eight-continuation cap.
