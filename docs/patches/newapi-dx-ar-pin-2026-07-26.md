# NewAPI AgentRouter pin (2026-07-26)

## Why

Failover from百倍 soft/502 onto AR `#119/#120` returned upstream  
`token quota is not enough (remain ~$0.02 / need ~$0.66)` as client **403**,  
with Claude Code misleading `Please run /login`.

ZG `cc-switch` token is unlimited; the empty wallet was the **AR API token**.

## Applied

| Channel | Action |
|---------|--------|
| `#118` `#119` `#120` | `status=2`, `abilities.enabled=0` |
| health_check v5 | Already EXCLUDE∋118–120 (no probe / no auto-reactivate) |
| Opus path | `#9/#10/#20/#60` only inside NewAPI |

Smoke after pin: `claude-opus-5` → `#9` HTTP 200.

## Client fallback

Local FQ remains `ZG → agentrouter-2` (desktop direct AR, no VPS guard)  
for full ZG outage only — not used for NewAPI in-band Opus failover.

## Re-enable later

Only after AR token remain_quota ≫ typical Opus pre-charge (~$1+ headroom), then:

1. Top up the specific API tokens (`sk-GhabK…` / `sk-vbtBC…` / `sk-BiWMF…`)
2. `POST /api/channel/{id}/status {"status":1}` + enable needed abilities
3. Prefer single `#118` with low weight before bringing 119/120 back
