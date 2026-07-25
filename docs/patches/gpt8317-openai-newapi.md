# GPT pool `216.195.211.206:8317` on ZG NewAPI

**Date:** 2026-07-26  
**Status:** Live `#21 gpt-8317` (reused former `dc-216-grok`)

## Why

User-correct OpenAI-compatible GPT upstream. Prefer this over ad-hoc fronts (e.g. `newapi.123458.online`). **No Claude** — type=1 only. Grok ids on this host currently return `auth_unavailable` — not enabled.

## Channel

| Field | Value |
|-------|--------|
| Id / name | `#21` `gpt-8317` |
| Type | `1` (OpenAI) |
| Priority / weight | `60` / `40` (strict GPT ladder: **`#21` 60** → `#124` 55 → `#123` 50) |
| Base | `http://216.195.211.206:8317` |
| Header | `User-Agent` browser-like (some fronts CF-block bare clients) |
| Models | `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-4o-mini`, `codex-auto-review` |

## Verification

- NewAPI channel test: `gpt-5.5` / `gpt-5.6-terra` OK  
- Gateway `https://aliyun.donglicao.com/v1/chat/completions`: `gpt-5.5` / `gpt-5.6-terra` / `gpt-4o-mini` → `OK` (~2s)  
- Backup: `/opt/new-api/backups/one-api.before-gpt8317-*.db`

## Related

- Secondary GPT front: `#124 gpt-123458` — `docs/patches/gpt123458-openai-newapi.md`
- VyceAI (Claude/OpenAI, separate host): `#125/#126` — `docs/patches/vyceai-newapi.md`
