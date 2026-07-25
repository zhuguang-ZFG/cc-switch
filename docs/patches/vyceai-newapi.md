# VyceAI on ZG NewAPI

**Date:** 2026-07-26  
**Status:** Live `#125 vyceai-claude` (type=14) + `#126 vyceai-openai` (type=1)  
**Dashboard:** `https://vyceai.com/dashboard-v2`  
**API:** `https://vyceai.com/v1`

## Why

Multi-model host with working Anthropic `/v1/messages` for Sonnet/Haiku. Not an Opus substitute — do not map `claude-opus-*` here.

## Channels

| Id / name | Type | Pri/W | Models (enabled) | Notes |
|-----------|------|-------|------------------|--------|
| `#125` `vyceai-claude` | 14 | 35/20 | `claude-sonnet-4-6`, `claude-sonnet-5`, `claude-haiku-4-5` (+ dated/`[1M]` → haiku) | Haiku ladder: Agnes 40 → **Vyce 35** → LongCat 30 |
| `#126` `vyceai-openai` | 1 | 48/15 | `deepseek-v4-flash`, `minimax-m3`, `mimo-v2.5-pro` | After hongshi 50；gpt/glm/fable disabled upstream |

## Explicit non-goals

- Do **not** put VyceAI in Opus primary pool or map Opus/Fable onto it.
- Skip temporarily disabled models (`claude-fable-5`, `gpt-5.6-sol`, `glm-5.2` at smoke time).

## Verification

- Gateway `/v1/messages`: `claude-sonnet-4-6` / `claude-sonnet-5` / `claude-haiku-4-5` → `OK`
- Gateway chat `deepseek-v4-flash` returned 200 (empty text once — watch)
- Insert via SQLite (admin POST panics) + `podman restart new-api`
- Backup: `/opt/new-api/backups/one-api.before-123458-vyce-*.db`
