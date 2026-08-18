# Guardian cycle-budget hardening (2026-08-18)

## Scope

This change fixes a Guardian cycle-starvation case in the local NewAPI/OMP
stack. A slow full-health scan could consume the 90-second cycle budget and
skip ability repair, stale-state cleanup, and the daily report. The full scan
now uses the remaining cycle budget as a bounded per-channel timeout, stops
before the next probe when less than one second remains, preserves rotation for
deferred channels, and runs critical maintenance before the next scan batch.

No CC Switch/Tauri files, database schema, credentials, channel status, or
provider configuration were changed.

## Evidence before deployment

- Guardian log recorded a 117.4s cycle at 11:50:07, followed by skipped
  ability fix, state cleanup, and daily report.
- A verified rollback copy was created before deployment:
  `C:\Users\zhugu\.omp\guardian\guardian.py.bak-20260818-130614-cycle-budget`
  (127,102 bytes; previous SHA-256
  `0ED839659980EF0E6BA02B6439C40359F6082F468C39D877AD1E0732D03B6E75`).

## Deployment verification

- Repository, staged runtime, and live runtime Guardian SHA-256:
  `FA231126AC76D0674C003FFA2C4A8D861CB4BC653BB9144961DCD936AF4D72E7`.
- Scheduled task `NewAPI Guardian` was restarted in a bounded stop/start
  operation. Exactly one `guardian.py` process remained (PID 19148) and the
  heartbeat refreshed at 13:09:28.
- NewAPI `/api/status` returned HTTP 200.
- Ports 3002, 8788, 8789, 15999, 16000, and 16001 were listening with one
  identified owner each; no proxy restart was needed.
- Guardian metrics at 13:09:13 showed `newapi_fail_streak=0`, no degraded
  channels, and `restarted_proxies=0`.

## Post-deploy smoke

`newapi-local-smoke.py` completed its checks at 13:09:06. The base status,
five local proxy probes, automatic recovery, channel/model isolation, accepted
disabled-channel policy, fallback posture, pool capacity, and multi-key health
checks passed.

The run intentionally remained non-green for already-known production
conditions:

- Sol posture/ability contracts for #91 and #92 are still drifted from the
  target p55/w5 and p60/w15 contract. They were not modified because the
  forced #91 Responses probe passed 2/2 while #92 still returns an HTTP 200
  wrapper containing an upstream nginx 405. The rollout contract requires both
  channels to pass before promotion or failover drilling.
- `gpt-5.6-luna` returned HTTP 403 (quota/upstream condition).

These failures are recorded as expected/blocked evidence; no automatic channel
mutation or retry amplification was performed. The rollback copy remains
available at the path above.

## Repository checks

- Guardian syntax compilation passed.
- Guardian focused tests: 147 passed.
- Combined ops suite (`test_update_scripts`, `test_smoke`, `test_guardian`,
  `test_omp_routes`): 240 passed.
- `git diff --check` passed.
