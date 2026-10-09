# OMP / NewAPI stability deployment, September 24–25, 2026

Implemented and deployed three script updates:

- OMP routing extension `2026.09.24-routing-r6`: live owners cannot lose their
  canary lease solely because a sweep lasts over ten minutes; heartbeat, owner
  token, and serialized dead-owner reclamation prevent overlapping sweeps.
- Guardian supervisor: failed notification delivery does not consume the long
  success cooldown; retries remain bounded. Restart budgets and completed daily
  maintenance survive a supervisor restart, including across midnight.
- AnyRouter window canary: JSON/SSE checks reject empty output, errors, missing
  terminal events, truncation, and oversized/overdue responses.

The repository health checker now rejects invalid/future snapshots, incomplete
service rosters, non-boolean health flags, and blocked restarts. It also probes
the justwoker relay on 8790 and supports read-only `--no-log` inspection.

No CCS binary replacement/rebuild, global OMP package change, model routing
change, NewAPI configuration change, or CCS database/schema change was made.

## Verification

276 focused checks passed: 22 Node tests and 254 Python tests, including Guardian,
supervisor, health-check, canary, deployment/rollback, and production mirror tests.
The deployment failure test intentionally injects a replacement failure and
verifies restoration. Frontend/Rust checks were not rerun for these ops-only edits.

Two real OMP native-read canaries passed with retries and fallback disabled:

| Selector | Route | Duration | Validation |
| --- | --- | --- | --- |
| `zg-newapi/kimi-for-coding` | NewAPI `127.0.0.1:3002/v1` | 42.8 s | tool arguments, file contents, final nonce |
| `zg-newapi/k3` | NewAPI `127.0.0.1:3002/v1` | 49.7 s | tool arguments, file contents, final nonce |

These used the configured OpenAI Chat Completions provider through OMP. Raw
transport framing, non-stream mode, fallback behavior, other selectors, and the
CCS ingress route were not separately exercised. Probe response headers supplied
no channel/request attribution. Earlier sandbox/runner attempts timed out or
exited without a probe result; they do not count as successful route checks.

Supervisor activation changed only its PID (13416 to 3696). All nine listener
owners remained unchanged, including CCS PID 23972 and NewAPI PID 14328.
Guardian stayed at PID 18492; eight supervised services reported healthy. The
existing same-day CCS restart count of 1 survived activation.

## Backup, activation, and rollback

Verified backup directory:
`~/.omp/guardian/backups/stability-20260924-235421-25524/`.
It contains the three previous scripts, SHA-256/size/time metadata, safe status
and log metadata, configuration hashes, a deployment manifest, and `rollback.cmd`.

Run that backup's `rollback.cmd` to restore the previous script bytes. Before
reactivating the supervisor, inspect its exact current script PID and creation
time; stop only that PID, then launch the existing Startup shortcut
`LocalAIProxies-Supervisor.lnk`. Verify a fresh heartbeat and unchanged proxy
listener ownership. Do not kill proxies or use a broad Guardian restart script.

OMP's updated extension applies to new or reloaded sessions. Existing sessions
were not terminated. The AnyRouter scheduled task picks up its new script on
the next invocation; no manual notification-producing run was triggered.

## Remaining observations

- The September 24 23:25 scheduled comprehensive smoke failed with database
  locking / HTTP 500. Later read-only DB access and both canaries succeeded;
  this does not establish that contention is fixed. The database currently uses
  DELETE journal mode; no journal-mode/configuration change was made.
- Earlier comprehensive checks also reported Opus pool posture/capacity drift.
  Those model policies were preserved pending separate evidence-based review.
- A post-deployment scan coincided with the watchdog's scheduled execution and
  returned `0x41301` (running), which this checker deliberately does not treat as
  proof of successful completion. The subsequent 00:03:42 watchdog execution
  was independently checked and returned exit code 0.
- `~/.omp` occupies about 5.47 GB against the health check's 5 GB threshold.
  No user sessions, caches, or backups were deleted to make the check green.
