# NewAPI DX governance B — 2026-07-26

Ops pass after evidence showed `#81` still eating Opus traffic (12h: n≈84, p50≈31s / p90≈121s) while main pool `#9` p50≈9s.

## Goals

1. Stop slow `#81` / pinned `#11` from serving Opus  
2. Prefer faster Opus weights; keep nested retries bounded  
3. AR keyword → immediate channel switch  
4. Document how to triage “stuck” sessions  

## Applied

| Area | Change |
|------|--------|
| `#81` | `status=2`, abilities off, models empty, w=1; `AUTO_REACTIVATE_EXCLUDE`∋81 |
| `#11` | remain `status=2`, abilities off |
| Opus weights | `#9/#10/#20/#60` → **50/42/24/8** (p50-driven; `#20` demoted) |
| analyze | `OPUS_POOL=[9,10,20,60]`; `LAST_RESORT=∅`; denylist 11/81 |
| guard env | `KIRO_GUARD_CONTENT_BLOCK_FAILOVER=1` in shared env + AR units; `SOFT_RETRY=1` |
| restart | kiro-guard 8403–8405 / 8410–8412 / 8400 + `podman restart new-api` |

Backup: `/opt/new-api/backups/one-api.before-gov-b-*.db`

## Triage: 首字慢 vs 中途停

| Symptom | Likely cause | What to check |
|---------|--------------|---------------|
| Spinner long, then full answer | TTFT / slow channel | NewAPI `logs.use_time` by `channel_id`; soft journal `upstream_http_50x` |
| Fast start, answer cuts mid-way | Kiro soft-trunc / empty tool | `kiro-guard-soft.jsonl` reasons (`empty_content`, `tool_intent_no_call`, …); guard `/metrics` soft counters |
| 400/405 then switch or fail | AR keyword / WAF | journal `content_blocked:*` / `sensitive_words*`; expect **502** failover (not soft-retry) |
| Local 504 / many retries | Nested retry | local `max_retries=2`, NewAPI `RetryTimes=3`, guard soft_retry=1 — do not raise |

## Do not

- Re-enable `#81` / `#11` for Opus without fresh latency evidence  
- Put `#81` back into `OPUS_POOL` / `LAST_RESORT`  
- Tighten local `first_byte` below 25s without community contract  
- Bounce `/api/channel/:id/status` to “fix” abilities on pinned channels  

## Related

- Strict ladders: `docs/ops/zg-claude-routing.md`  
- Cursor ops loop: `docs/ops/newapi-dx-cursor-ops.md`  
