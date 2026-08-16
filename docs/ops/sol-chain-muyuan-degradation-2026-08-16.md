# sol chain muyuan ch83 degradation baseline (2026-08-16)

## Finding

The gpt-5.6-sol aggregation chain has an active, ongoing degradation on its
primary channel that probes cannot see:

| Channel | Role | 4h errors (19:41–23:30) | 24h consumes | p50 use_time |
|---|---|---|---|---|
| ch83 muyuan-sol (prio 50) | primary | **42** (35×504 Cloudflare, 4×500, 3×502; escalating 2→23→17 per hour) | 642 | 18s |
| ch45 agentrouter (prio 40) | first backup | 0 | 83 (mostly ch83 retry spillover) | 10s |
| ch87 ooioo (prio 30) | second backup | 0 | 8 | 3s |

Admin channel tests pass on all three (1.6s / 4.7s / 2.5s at 23:30) because
the test probe is a tiny request; the 504s only trigger on large payloads /
long streams. Guardian therefore never auto-disables ch83 — this failure
mode is invisible to health probes by design.

## Loss points

1. **Billed empty streams** ×3 on ch83 (14:11, 21:40, 22:48): prompt
   127K–187K tokens billed (~243K quota total), `completion_tokens=0`,
   empty content. The stream establishes (headers 200, prompt processed and
   billed) then stalls/RSTs. Neither `AutomaticRetryStatusCodes` (500-504)
   nor `AutomaticRetryOnEmptyResponseEnabled=true` covers this — both only
   engage at response-header time, not mid-stream.
2. **504 storm absorbed by retry**: `RetryTimes=1` routes failed attempts to
   ch45, so user-visible impact is low (that is where ch45's 83 consumes
   came from). `RetryTimes=1` is a deliberate budget
   (`scripts/ops/update_newapi_retry_budget.py`) — do not raise it casually.

## Decision (2026-08-16, user)

**Keep current priority order (83→45→87), monitor only.** Rejected options:
ch45↔ch83 swap (agentrouter capacity at primary volume unproven; it also
has 30% slow streams ≥55s, max 211s) and demoting ch83 below ch87 (ooioo
too new, 8 samples).

## Monitoring anchors

- Error rate: `grep -c "channel error (channel #83" ~/.new-api-local/logs/oneapi-*.log`
  per process-lifetime window. Baseline 2026-08-16: ~10/hour and rising.
  Revisit routing if it sustains >20/hour or ch45 starts erroring too.
- Billed-empty streams:
  `SELECT COUNT(*) FROM logs WHERE model_name LIKE '%sol%' AND type=2 AND
  completion_tokens=0 AND created_at > <day_ago>` — baseline 3/day.
- ch83 consume share dropping toward ch45/ch87 over days = users being
  silently carried by backups; that is the signal to demote ch83.
- rc.24 upgrade (pending, see GitHub #6249) adds HTTP/2 GetBody transparent
  retry after upstream stream reset — may reduce the mid-stream RST class;
  re-measure after upgrading.

## Related

- `docs/ops/ooioo-gpt56sol-channel-2026-08-16.md` — ch87 runbook, priority ladder.
