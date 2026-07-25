# NewAPI health_check v5.1 (2026-07-26)

**VPS:** `/opt/new-api/health_check.py`  
**Repo mirror:** `scripts/ops/health_check.vps.py`  
**Cron:** `*/30` with `flock` (unchanged)

## Why

v5 still probed the first `*opus*` model (often `claude-opus-4-6` while live traffic is `claude-opus-5`), retried via `http://127.0.0.1:7890` (false **400** on HTTPS upstreams), and counted community `No available accounts` / 503 toward `FAIL_THRESHOLD=6` → could **disable the Opus hot pool** during normal 公益站 flaps.

## v5.1 changes

| Item | v5.1 |
|------|------|
| Probe model | Prefer `claude-opus-5[1M]` → `claude-opus-5` → other opus |
| Proxy tries | Channel `setting.proxy` then **direct only** (no global sing-box) |
| Community transient | `no available accounts` / `auth_unavailable` / bare 503 → `FAIL-TRANSIENT`, **no fail-count** |
| Disable threshold | **12** consecutive **hard** fails |
| Auto-reactivate | Still off |
| Pinned skip | `PINNED_EXCLUDE`∋`11,75,77–81,118–120` (alias `AUTO_REACTIVATE_EXCLUDE`) |

## Related

- Ops posture: `docs/ops/newapi-dx-cursor-ops.md`「NewAPI 运维姿态」
- Routing snapshot: `docs/ops/zg-claude-routing.md`
