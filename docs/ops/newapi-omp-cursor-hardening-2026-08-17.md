# NewAPI / OMP / Cursor hardening (2026-08-17)

## Scope

This maintenance pass is intentionally limited to the local NewAPI gateway,
OMP routing/runtime supervision, and the public Cursor-compatible entry point.
It does not modify CC Switch application code, Tauri/Rust, or the CC Switch
database/schema.

## Applied production changes

- NewAPI options converged through the admin API:
  - `RetryTimes=1`
  - `AutomaticRetryStatusCodes=408,500-503`
  - `AutomaticDisableChannelEnabled=false`
- Sol pool posture restored:
  - ch83 `muyuan-sol`: disabled during the current upstream outage, tier kept
    at priority/weight `50/5`, abilities disabled at the same tier.
  - ch45 `agentrouter`: enabled fallback at `40/5`.
- ch57 `gorouter`, whose balance is exhausted, was found at `status=1` with
  `weight=0` and was restored to the deliberate double lock `status=2`,
  `weight=0`.
- Guardian now classifies `429` as transient only when no hard quota, balance,
  or credential marker is present. A response such as `429 quota exhausted`
  enters quarantine instead of being probed indefinitely.
- `NewAPI Guardian Watchdog` was restarted through its canonical scheduled
  task and remained `Running` across multiple 30-second intervals. The
  independent one-minute `LocalNewAPI-Watchdog` remains the second owner.
- Scheduled smoke now sends transition-only Telegram alerts on first failure
  and first recovery. Failed delivery is retried on the next scheduled run.
- Capacity checks exclude `status=1, weight=0` rows because NewAPI cannot route
  traffic to them.

Retired dead entries are not restored merely to make a monitor green:

- ch79 HY3 remained `INVALID_API_KEY` and is retired.
- ch84 Teamorouter exhausted its free quota and is retired.
- Their historical shape contracts remain documented for a future re-import
  with newly verified credentials, but neither is counted as live capacity.

## Rollback artifacts

- NewAPI option backup:
  `~/.new-api-local/backups/newapi-retry-budget-20260817-162317.json`
- Pre-change Sol database snapshot:
  `~/.new-api-local/backups/new-api-before-sol-posture-20260817-162328.db`
  - size: `79,343,616` bytes
  - SHA-256: `6DBB35F2AB400C2A4A0BEBF76F18C049FC06CD695812BFEF891793CAFC094CD5`
- ch57 channel backup:
  `~/.new-api-local/backups/channels-57-20260817-163319.json`
- Guardian runtime backup:
  `~/.omp/guardian/guardian.py.bak-20260817-162600-quota429`
  - SHA-256: `E63638202155BA3668F6813B027E48865CA1078FD53E8E72C4088083EE4CCB59`

## Verification evidence

- Repository/live Guardian SHA-256 match:
  `98E9D2E6ABC489F17B3FA9884224B95ACC2804472BE878B35CE49482E8E2FCD4`.
- Guardian rolled from PID `23752` to PID `15464`; the new heartbeat refreshed
  normally.
- NewAPI option and Sol posture tools both report `already configured` after
  deployment.
- Focused Python regression: `231/231` passed, covering Guardian quota
  classification, smoke policy, retry rollback, alert deduplication, routing
  update tools, and OMP route gates. The jianzhile OMP updater Node tests passed
  `2/2`; `system-health-check.py` passed `25/25`.
- `omp models` resolves the configured NewAPI, Anthropic, AgentRouter, and
  dedicated jianzhile model selectors.
- Real OMP probe:
  `zg-newapi/jianzhile-codex-gpt-5.6-sol` returned
  `JIANZHILE_OMP_OK`; NewAPI logs attribute both verification requests to
  ch91, streaming, in approximately 6-7 seconds.
- Cursor/public boundary:
  - `https://aliyun.donglicao.com/api/status` -> HTTP 200
  - unauthenticated `https://aliyun.donglicao.com/v1/models` -> HTTP 401
- Final live `newapi-local-smoke.py` exited successfully after the route and
  quarantine repairs.

## Operational rules retained

- jianzhile must be validated with a real Codex Responses request or the
  dedicated OMP alias. A generic Chat Completions management probe may return
  403 and is not proof of channel failure.
- Do not add global 429 retries back to NewAPI while OMP/client fallback is
  active. This avoids nested retry amplification.
- Do not re-enable or re-create retired channels without a new credential,
  direct upstream proof, aggregate attribution, and an updated smoke contract.
