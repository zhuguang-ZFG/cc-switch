# ZG NewAPI — Claude role routing (ops snapshot)

**Updated:** 2026-07-25  
**Gateway:** `https://aliyun.donglicao.com` (NewAPI on Aliyun)

This is an ops snapshot for the fork’s Claude Code path through ZG NewAPI. Channel IDs may drift; verify on the live admin UI.

## Role → upstream (intended)

| Claude Code role | Requested model id(s) | Primary NewAPI route | Notes |
|------------------|----------------------|----------------------|--------|
| Opus / Fable / Subagent / Reasoning | `claude-opus-5` / `claude-opus-5[1M]` | type=14 kiro/100x/k40 pool (pri≈45) | Slow TTFT (~9–14s) is upstream, not the local proxy |
| Sonnet | `glm-5.2` (mapped from sonnet id) | Zhipu `#41/#42` pri80 | Fast daily path |
| Haiku | `LongCat-2.0` / `claude-haiku-*` | **Agnes `#122`** → `agnes-2.0-flash` pri32; LongCat `#90` pri30 fallback | Free long-term OpenAI-compatible; see `docs/patches/agnes-haiku-newapi.md` |

## Explicit do-nots

- Do not promote Agnes / GPT-only free keys into type=14 Opus.
- Do not invent fake `claude-*` aliases on LongCat (tool JSON 400s).
- Prefer `pnpm tauri build` for local binaries that must show UI (`docs/patches/schema-v18-created-at-text.md`).
