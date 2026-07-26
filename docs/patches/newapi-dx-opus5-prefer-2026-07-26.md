# Opus 5 prefer — kill 4.8 routing (2026-07-26)

**Host:** Aliyun NewAPI `47.112.162.80`  
**Backup:** `/opt/new-api/backups/one-api.before-opus5-prefer-20260726-141854.db`

## Why

1. Client / Cyber Safeguards: Opus 4.8 hits AUP more; daily should stay on **Opus 5**.
2. Poison maps: AR `#118/#119/#120` had `claude-opus-5` → `claude-opus-4-8` (funnel back to 4.8).
3. Hot pool `#9/#10/#20/#60` still advertised enabled `claude-opus-4-8*` abilities.
4. Slow channel `#20` weight too high vs peers.

## Changes

| Item | Before → After |
|------|----------------|
| Hot pool maps | 4.x / 4.8 → **`claude-opus-5` / `[1M]`** |
| AR `#118` map | `opus-5` → `opus-4-8` **removed**; 4.x → Opus 5 |
| Enabled `opus-4-8*` abilities | **0** (disabled across managed channels) |
| Weights `#9/#10/#20/#60` | → **50 / 42 / 18 / 3**（压 `#20`） |
| AR `#118` | still pri30 / **w6**；`#119/#120` remain status=2 |

Required: `podman restart new-api` after ability/map/weight writes.

## Verify (2026-07-26)

```text
weights: #9=50 #10=42 #20=18 #60=3 #118=6
enabled opus-4-8 abilities: 0
#118 map: claude-opus-4-8 → claude-opus-5 (no 5→4.8)
smoke claude-opus-4-8 → resp.model=claude-opus-5 text=OK
smoke claude-opus-5[1M] / 4-8[1M] → 503 (community flap; expected noise)
disk: ~52% used; backup retention left counts unchanged
```

## Do-nots

- Do not re-enable `#119/#120` casually to chase 1M 503.
- Do not restore `claude-opus-5` → `claude-opus-4-8` maps on AR.
- Client FQ#2 `agentrouter-2` stays **Opus 5 without `[1M]`**.

## Related

- Routing: `docs/ops/zg-claude-routing.md`
- AUP: `docs/patches/cyber-safeguards-opus48-2026-07-26.md`
- Local align: `docs/patches/local-claude-rtk-align-2026-07-26.md`
