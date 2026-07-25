# ZG NewAPI — Claude role routing (ops snapshot)

**Updated:** 2026-07-25 (DX pass)  
**Gateway:** `https://aliyun.donglicao.com` (NewAPI on Aliyun `47.112.162.80`)

Ops snapshot for Claude Code through ZG NewAPI. Channel IDs/weights drift — verify on live admin UI after changes. Prefer fixing NewAPI for developer experience; do not assume “healthy” without smoke.

## Role → upstream (intended)

| Claude Code role | Requested model id(s) | Primary NewAPI route | Notes |
|------------------|----------------------|----------------------|--------|
| Opus / Fable / Subagent / Reasoning | `claude-opus-5` / `claude-opus-5[1M]` | type=14 kiro/100x/k40 (`#9/#10/#11/#20/#60`, `#81` last-resort) | Upstream TTFT often 9–50s+; weights tuned to avoid `#81` |
| Sonnet / default | `glm-5.2` / `glm-5.2[1M]` | Zhipu `#41` (w50) / `#42` (w8) pri80; hongshi `#123` pri55 backup | **Must** have ability for bare `glm-5.2[1M]`; zhipu `param_override={"enable_thinking":false}` |
| Haiku | `LongCat-2.0` / `claude-haiku-*` / `claude-haiku-4-5-20251001` | Agnes `#122` → `agnes-2.0-flash` pri32; LongCat `#90` pri30 | Prices configured for bare `agnes-2.0-flash` |
| GPT (OpenAI path) | `gpt-4o` / `gpt-4o-mini` / … | hongshi `#123` (and other GPT pools) | No Claude on hongshi — type=1 only |

## DX fixes (2026-07-25 evening)

| Issue | Symptom | Fix |
|-------|---------|-----|
| `glm-5.2[1M]` missing | `No available channel` if client sends `[1M]` (official CC Switch / no strip) | Abilities + map → `glm-5.2` on `#41/#42/#123`; ModelRatio/CompletionRatio |
| GLM empty completion | `reasoning_tokens` eats `max_tokens`; empty text / half answers | Zhipu `#41/#42` `param_override={"enable_thinking":false}` |
| Agnes bare id | `price not configured` | ModelRatio/CompletionRatio for `agnes-2.0-flash` (+ alpha) |
| Haiku dated id | `claude-haiku-4-5-20251001` had no ability | Map on Agnes `#122` |
| Slow Opus channel | `#81` avg ~50s in 6h logs | priority 20 / weight 1; `#20` weight 5 |
| Slow GLM key | `#42` much slower than `#41` | weights 8 vs 50 |

Smoke after change (gateway `/v1/messages`): `glm-5.2`, `glm-5.2[1M]`, `agnes-2.0-flash`, `claude-haiku-4-5-20251001`, `claude-opus-5[1M]` must return non-empty text under `max_tokens=64`.

Detail: `docs/patches/newapi-dx-2026-07-25.md`, `docs/patches/agnes-haiku-newapi.md`, `docs/patches/hongshi-openai-newapi.md`.

## Client note (cc-switch)

- Prefer **official** [farion1231/cc-switch](https://github.com/farion1231/cc-switch/releases) installers for daily use (e.g. v3.18.0 → DB support **v16**).
- Fork schema v17+ (`enabled_pi`) / v18 will trip **数据库版本过新** on official — after backup, `PRAGMA user_version = 16;` is the known downgrade for this ops path (extra columns ignored).
- Do not replace the installed UI with `cargo build --release` only.

## Explicit do-nots

- Do not promote Agnes / GPT-only free keys into type=14 Opus.
- Do not invent fake `claude-*` aliases on LongCat (tool JSON 400s).
- Do not leave `glm-5.2[1M]` without NewAPI ability (proxy strip is not always present).
- Do not re-enable zhipu thinking unless Claude Code `max_tokens` budget is proven large enough.
