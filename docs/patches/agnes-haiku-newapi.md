# Agnes AI — free OpenAI pool + NewAPI Haiku tier

**Date:** 2026-07-25  
**Status:** Ops wiring (local cc-switch providers + ZG NewAPI `#122`)

## Why

Agnes is a long-term free OpenAI-compatible multimodal API. It has **no Claude/Opus upstream**, so it must not join NewAPI type=14 Claude pools or the Claude failover queue. It **is** useful as:

1. Local OpenAI-compatible providers (Reasonix / Pi / Codex / Kimi Code)
2. NewAPI **Haiku-tier** routing (cheap/fast text for `claude-haiku-*` / `LongCat-2.0`)

## Endpoints

| Role | URL |
|------|-----|
| Console (not API) | `https://platform.agnes-ai.com/` |
| API Base | `https://apihub.agnes-ai.com/v1` |

Primary text model: `agnes-2.0-flash` (smoke ~0.8–4.5s). Also listed: `agnes-2.5-pro-alpha`, image/video (image often 503).

## Local cc-switch providers

Added (not `is_current`, not Claude failover):

| App | Provider id | Notes |
|-----|-------------|--------|
| reasonix | `agnes-reasonix` | `kind=openai` |
| pi | `agnes-pi` | `api=openai-completions` |
| codex | `agnes-codex` | `wire_api=chat` |
| kimicode | `agnes-kimicode` | `api_mode=chat_completions` |

Switch the corresponding app’s current provider in the UI when you want to use Agnes directly.

## ZG NewAPI Haiku route

| Field | Value |
|-------|--------|
| Channel | `#122 agnes-haiku-free` |
| Type | `1` (OpenAI) |
| Priority | `40` / weight `20` (Haiku ladder: Agnes 40 → Vyce `#125` 35 → LongCat `#90` 30) |
| Upstream | `https://apihub.agnes-ai.com` |
| Mapping | `LongCat-2.0` / `claude-haiku-*` / `claude-haiku-4-5-20251001` / `[1M]` → `agnes-2.0-flash` |
| Fallback | Vyce `#125` pri35 then LongCat `#90` pri30 |
| Pricing | `agnes-2.0-flash` (+ alpha) ModelRatio/CompletionRatio set 2026-07-25 |

Claude Code / `zg-gateway-claude` can keep requesting `LongCat-2.0` (or `claude-haiku-4-5` / dated Haiku); NewAPI prefers Agnes at pri32.

Bare `agnes-2.0-flash` on the gateway is OK after the DX pricing pass (`docs/patches/newapi-dx-2026-07-25.md`).

## Explicit non-goals

- Do **not** add Agnes as Opus/Sonnet type=14 Claude channels.
- Do **not** put Agnes in the Claude Code failover queue ahead of ZG / agentrouter for Opus work.
- `api.520pro.top` style GPT-only free keys are separate; evaluate per-smoke before promoting.
