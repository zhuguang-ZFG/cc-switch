# NewAPI DX exec — soft truncation guard + local ZG restore

**Date:** 2026-07-25 night  
**Task:** `.trellis/tasks/07-25-optimize-newapi-dx`  
**Host:** Aliyun NewAPI (`aliyun.donglicao.com` / `47.112.162.80`)

## Applied

1. **kiro-guard hardened** (`/opt/new-api/kiro_guard.py`)  
   Soft-truncation classify (missing usage / empty content / short completion gate) → same-upstream retry once → 502. Units on `:8400/:8401/:8403-8405` restarted. Backup: `kiro_guard.py.bak.20260725-235822`.

2. **abilities ↔ channels sync** for managed pools (`#9/#10/#11/#20/#21/#41/#42/#60/#81/#90/#96/#122/#123`) — priority/weight mismatch cleared.

3. **health_check v4** — urllib probes (no Bearer on process argv); slow demote needs 2 consecutive hits; demote/recover updates abilities. Backup: `health_check.py.bak.dx-*`.

4. **Cron** — removed broken `0 3 * * * cp /opt/new-api/one-api.db …`; kept `17 3 * * * backup_db.sh`.

5. **Credential exposure** — killed leftover curl probes; `#13 free.lyclaude.site-Jofy` already disabled (`status=2`). Re-enable only after upstream key rotation.

6. **Local cc-switch** — `zg-gateway-claude` current; failover `ZG → agentrouter-2 → 林夕`; `first_byte=25s`, `max_retries=3`.

## Smoke (gateway `/v1/messages`, max_tokens=64)

| Model | Result |
|-------|--------|
| glm-5.2 | 200 OK ~4.7s |
| glm-5.2[1M] | 200 OK ~2.8s |
| claude-haiku-4-5-20251001 | 200 OK ~3.5s |
| claude-opus-5[1M] | 200 OK ~4.5s |

## Operator follow-up

- Rotate key for `#13` at upstream before re-enable.
- Restart Claude Code / Cursor so local provider switch is picked up if the process cached the old current.
- Watch `journalctl -u kiro-guard*` for `soft_retry` / `soft_exhausted` reason codes under real Opus load.
