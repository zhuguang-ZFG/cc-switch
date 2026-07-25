# NewAPI anti-stall automation — 2026-07-26

## Why

Logs showed usable `#9/#10` (~3–6s) mixed with stall tails: AR `#118` up to **316s**, `#20` historically slower, `#119/#120` 80–90s+, k40 `#60` 503. Goal: keep Claude Code usable without manual babysitting.

## Immediate

| Channel | Action |
|---------|--------|
| `#9/#10` | w **50/42** (primary) |
| `#20` | w **12** (demoted) |
| `#60` | w **3** |
| `#118` | w **6** (only AR backup) |
| `#119/#120` | Opus/Fable **abilities off** |
| `#11/#81` | remain status=2 |

## Automation (`analyze_newapi_dx.py`)

- Watch `OPUS_POOL` + `AR_POOL`
- Rank by p50; band weights; **no-raise if p50 > 1.6× best**
- Hard caps: p50≥35s → w≤8; p90≥90s → w≤3; AR park at p50≥80s → w1
- `FORCE_DEMOTE` bypasses 6h cooldown when cutting a stalling channel
- Weight apply triggers `podman restart new-api`
- Soft-env writes **preserve** non-managed keys (`CONTENT_BLOCK`, etc.)
- Windows schtask: every **4 hours** → `scripts/ops/newapi-dx-analyze.bat`

## Verify

- dry-run escalate includes `#20 no-raise …`
- smoke Opus ~3.5s ×3
- Backup: `one-api.before-anti-stall-*.db`
