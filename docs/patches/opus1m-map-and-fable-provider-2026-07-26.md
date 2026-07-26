# Opus [1M] upstream map + local Fable→肥波5 (2026-07-26)

**Host:** Aliyun NewAPI `47.112.162.80`  
**Backups:**
- `/opt/new-api/backups/one-api.before-opus1m-map-20260726-153819.db`
- `/opt/new-api/backups/one-api.before-ar118-mapfix-20260726-154051.db`

## Problem

Upstream 100xlabs (`#9/#10/#20`) returns **pricing restriction** for model id `claude-opus-5[1M]` while bare `claude-opus-5` works.  
Gateway then surfaced a misleading final 503「无可用渠道」after retries (often via `#118`).

Separately, stock ZG provider had `ANTHROPIC_DEFAULT_FABLE_MODEL=claude-opus-5[1M]`, so Claude Fable role never hit jianzhile `#127`.

## Fix (ops-only; no cc-switch binary)

### NewAPI `model_mapping`

| Channels | Change |
|----------|--------|
| `#9/#10/#20/#60` | `claude-opus-5[1M]` / `4.x[1M]` / `zg-claude-opus-5` → **`claude-opus-5`** (upstream send) |
| `#118` agentrouter | Keep **`[1M]` identity** / `zg→[1M]` — AR nested NewAPI does not accept bare `claude-opus-5` the same way |

Ability rows for `claude-opus-5[1M]` stay enabled so clients can still request the `[1M]` alias; mapping only rewrites the upstream model name on the 100xlabs pool.

### Local provider (`zg-gateway-claude`)

| Env | Value |
|-----|--------|
| `ANTHROPIC_DEFAULT_FABLE_MODEL` | **`claude-fable-5`** (was `claude-opus-5[1M]`) |
| `ANTHROPIC_MODEL` | **unset** (unchanged policy) |

Local DB backup: `~\.cc-switch\cc-switch.db.bak-fable-*`

## Smoke (gateway)

- `claude-opus-5` / `claude-opus-5[1M]` → 200, `end_turn`
- `claude-fable-5` → `#127`, 200

## Live weights (snapshot)

`#9/#10/#20/#60` = **50 / 40 / 28 / 3** (pri45); `#118` w6. Docs previously said 50/42/18/3 — align to live.

## Related

- `docs/patches/jianzhile-fable-newapi-2026-07-26.md`
- `docs/ops/zg-claude-routing.md`
- `docs/ops/do-not-modify-cc-switch.md`
