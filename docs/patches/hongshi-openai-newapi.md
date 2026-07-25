# hongshi — OpenAI/GLM backup on ZG NewAPI

**Date:** 2026-07-25  
**Status:** Live `#123 hongshi-openai-free`

## Why

Free OpenAI-compatible upstream for GPT / glm / deepseek backup. **No Claude** — do not add as type=14.

## Channel

| Field | Value |
|-------|--------|
| Id / name | `#123` `hongshi-openai-free` |
| Type | `1` (OpenAI) |
| Priority | `50` (strict: zhipu 80 → GPT `#21/124` → **hongshi 50** → Vyce OpenAI 48) |
| Base | `https://api.hongshi.cc.cd` |
| Models | `gpt-4o`, `gpt-4o-mini`, `gpt-4`, `gpt-4-turbo`, `gpt-3.5-turbo`, `glm-5.2`, `z-ai/glm-5.2`, `deepseek-v4-*`, `openai/gpt-oss-*`, `glm-5.2[1M]` (mapped) |

## Notes

- GPT models needed ModelRatio/CompletionRatio before gateway would accept them.
- `gpt-5*` rejected upstream (Cloudflare 1010) — removed from channel model list.
- Smoke via gateway: `gpt-4o` / `gpt-4o-mini` returned expected text on channel 123.
