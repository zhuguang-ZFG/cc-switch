# ZG NewAPI — Claude role routing (ops snapshot)

**Updated:** 2026-07-25 (DX exec — guard soft-truncation + local ZG restore)  
**Gateway:** `https://aliyun.donglicao.com` (NewAPI on Aliyun `47.112.162.80`)

Ops snapshot for Claude Code through ZG NewAPI. Channel IDs/weights drift — verify on live admin UI after changes. Prefer fixing NewAPI for developer experience; do not assume “healthy” without smoke.

## Local entry (required)

| Item | Value |
|------|--------|
| Current provider | `ZG网关 Claude` (`zg-gateway-claude`) → `https://aliyun.donglicao.com` |
| Default / Sonnet | `glm-5.2[1M]` (via ZG) |
| Failover queue | ZG → `agentrouter-2` → 「林夕」公益站（单条） |
| Proxy knobs | `streaming_first_byte_timeout=25`, `max_retries=3` |
| Do not | Make Sub2API / anyrouter current for daily Claude work |

## Role → upstream (intended)

| Claude Code role | Requested model id(s) | Primary NewAPI route | Notes |
|------------------|----------------------|----------------------|--------|
| Opus / Fable / Subagent / Reasoning | `claude-opus-5` / `claude-opus-5[1M]` | type=14 kiro/100x/k40 (`#9/#10/#11/#20/#60`, `#81` last-resort) | Upstream TTFT often 9–50s+; weights tuned to avoid `#81` |
| Sonnet / default | `glm-5.2` / `glm-5.2[1M]` | Zhipu `#41` (w50) / `#42` (w8) pri80; hongshi `#123` pri55 backup | **Must** have ability for bare `glm-5.2[1M]`; zhipu `param_override={"enable_thinking":false}` |
| Haiku | `LongCat-2.0` / `claude-haiku-*` / `claude-haiku-4-5-20251001` | Agnes `#122` → `agnes-2.0-flash` pri32; LongCat `#90` pri30 | Prices configured for bare `agnes-2.0-flash` |
| GPT (OpenAI path) | `gpt-4o` / `gpt-4o-mini` / … | hongshi `#123` (and other GPT pools) | No Claude on hongshi — type=1 only |

## DX fixes (2026-07-25)

| Issue | Symptom | Fix |
|-------|---------|-----|
| `glm-5.2[1M]` missing | `No available channel` if client sends `[1M]` | Abilities + map → `glm-5.2` on `#41/#42/#123`; ModelRatio/CompletionRatio |
| GLM empty completion | `reasoning_tokens` eats `max_tokens` | Zhipu `#41/#42` `param_override={"enable_thinking":false}` |
| Agnes / dated Haiku | price / ability gaps | Agnes `#122` maps + prices |
| Ability weight=0 | Channel weight tuning ignored | Sync `abilities.priority/weight` from `channels` for managed pools |
| Kiro fake-complete stop | HTTP 200 + `end_turn` mid-answer | Harden `kiro_guard.py`: soft classify → same-upstream retry → 502 |
| Health-check key on argv | `curl … Authorization: Bearer …` visible in `ps` | `health_check.py` v4 uses urllib; disabled `#13` until key rotated |
| Broken 03:00 backup | Copies missing `/opt/new-api/one-api.db` | Removed; keep `backup_db.sh` at 03:17 |
| Local current=Sub2API | Bypasses ZG pool + guard; Sonnet forced to Opus | Restore ZG current; lean FQ; FB=25s / retries=3 |

Smoke (gateway `/v1/messages`, `max_tokens=64`): `glm-5.2`, `glm-5.2[1M]`, `claude-haiku-4-5-20251001`, `claude-opus-5[1M]` → 200 + non-empty text.

Detail: `docs/patches/newapi-dx-2026-07-25.md`, `docs/patches/agnes-haiku-newapi.md`, `docs/patches/hongshi-openai-newapi.md`.

## Cursor ops loop (ongoing)

- Runbook: `docs/ops/newapi-dx-cursor-ops.md`
- Local: `python scripts/ops/newapi-dx-analyze.py`
- Auto in-band: soft-trunc env (`KIRO_GUARD_SHORT_*`) + Opus weights; escalate when out of band.
- First live cycle 2026-07-26: `SHORT_OUT` 32→40; Opus weights rebalanced from 24h latency; smoke OK.

## Client note (cc-switch)

- Prefer **official** [farion1231/cc-switch](https://github.com/farion1231/cc-switch/releases) installers for daily use (e.g. v3.18.0 → DB support **v16**).
- Fork schema v17+ (`enabled_pi`) / v18 will trip **数据库版本过新** on official — after backup, `PRAGMA user_version = 16;` is the known downgrade for this ops path (extra columns ignored).
- Do not replace the installed UI with `cargo build --release` only.

## Explicit do-nots

- Do not promote Agnes / GPT-only free keys into type=14 Opus.
- Do not invent fake `claude-*` aliases on LongCat (tool JSON 400s).
- Do not leave `glm-5.2[1M]` without NewAPI ability (proxy strip is not always present).
- Do not re-enable zhipu thinking unless Claude Code `max_tokens` budget is proven large enough.
