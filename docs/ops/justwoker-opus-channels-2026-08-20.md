# justwoker opus-thinking aggregate channels (2026-08-20)

## Scope

Two keys for the justwoker gateway (`https://api.justwoker.icu`,
OpenAI-compatible, Cloudflare-fronted) were aggregated into local NewAPI as
two single-key channels:

| Channel | Name | Key | Priority/Weight | Role |
|---|---|---|---|---|
| ch94 | justwoker-opus-1 | key1 | 50 / 8 | thinking 池主力（与 ch75 同级） |
| ch95 | justwoker-opus-2 | key2 | 50 / 8 | thinking 池主力（与 ch75 同级） |

Both serve only `claude-opus-5-thinking` and `claude-opus-4-8-thinking`
(the complete upstream catalog, verified via `/v1/models` for both keys).
Created at p40/w5 as secondary capacity, promoted to p50/w8 the same day
after probes passed — now peers with tabitoken ch75.

Two single-key channels were chosen over one multi-key channel because this
fork has documented multi-key pitfalls (`multi_to_single` create does not
persist `is_multi_key`; PUT regenerates `channel_info` and wipes DB repairs —
see `docs/ops/t1qq-sol-channel-2026-08-16.md`).

## Channel parameters

- type=1 (OpenAI), base_url=`https://api.justwoker.icu` — no `/v1` suffix
- models: `claude-opus-5-thinking,claude-opus-4-8-thinking` (no aliases)
- test_model: `claude-opus-4-8-thinking`
- priority 50, weight 8, group `default`, auto_ban=1
- **header_override `User-Agent` = browser UA is required**: the upstream is
  behind Cloudflare and returns error 1010 for non-browser UAs (verified
  2026-08-20: python-urllib and curl default UAs blocked, browser UA 200).
  Do not remove the override on future edits.
- Real keys passed via argv at creation time; not stored in this repo.
- Creation helper: `scripts/ops/add_justwoker_opus_channel.py` — default
  dry-run; with `--apply` it backs up the DB, creates each channel disabled,
  management-probes it while disabled, enables on probe success, and verifies
  channel + abilities readback. Re-run probes and verifies existing channels
  without recreating and without touching their status (an intentional or
  Guardian-driven disable is never silently re-enabled).

## Verification evidence (2026-08-20)

- Upstream `/v1/models` with browser UA: both keys HTTP 200, identical
  2-model catalog.
- DB snapshot before creation:
  `~/.new-api-local/backups/new-api-before-justwoker-opus-20260820-121428.db`
  (89,300,992 bytes, integrity=ok).
- ch94/ch95 created disabled → management probe `claude-opus-4-8-thinking`
  through NewAPI → upstream: both ok (proves the header_override defeats the
  CF 1010 block on the real serving path).
- Readback after 75s cache sync: both channels status=1, p40/w5, abilities
  rows enabled for both models at p40/w5.

## Rollback

```powershell
# disable both channels (Guardian/SQLite SSOT remains consistent)
POST /api/channel/94/status {"status": 2}
POST /api/channel/95/status {"status": 2}
```

## Related

- `docs/ops/t1qq-sol-channel-2026-08-16.md` — multi-key pitfalls that
  motivated the two-single-key design.
- `docs/ops/ooioo-gpt56sol-channel-2026-08-16.md` — workflow contract this
  channel followed.
