# NewAPI health_check v5 (2026-07-26)

**VPS:** `/opt/new-api/health_check.py`  
**Repo mirror:** `scripts/ops/health_check.vps.py`  
**Cron:** `*/30` with `flock` (unchanged)

## Why

v4 probed **all** channels every 30 minutes, auto-reactivated on flaky success, blanket-toggled `abilities.enabled` on status flips, and counted multi-proxy retry wall time as “slow.” That burned quota, flapped parked AR (`#118` TG disable spam), and fought DX weight automation.

## v5 behavior

| Item | v5 |
|------|-----|
| Probe scope | **Only** Opus pool `#9/#10/#20/#60` |
| On consecutive fails (≥6) | Disable via `/api/channel/{id}/status` + TG |
| Auto-reactivate | **Off** (manual / TG enable only) |
| DISABLE-QUOTA shortcut | **Removed** (quota-ish text → normal fail path) |
| Slow probe | TG alert only; **no** priority/weight write |
| `abilities` | **Not** blanket `enabled=0/1` on status change |
| Pinned skip | `AUTO_REACTIVATE_EXCLUDE`∋`11,75,77–81,118–120` (never probe) |
| 降智探针 | Off (removed from runtime path) |

Weight / soft-trunc tuning remains with `analyze_newapi_dx` (4h schtask).

## Related

- Local FQ policy: `docs/ops/zg-claude-routing.md`
- Cursor ops belt: `docs/ops/newapi-dx-cursor-ops.md`
