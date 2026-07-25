# GPT front `newapi.123458.online` on ZG NewAPI

**Date:** 2026-07-26  
**Status:** Live `#124 gpt-123458`

## Why

OpenAI-compatible front probed earlier (browser UA required). Complements `#21 gpt-8317`. **No Claude.** `grok-4.5` currently `auth_unavailable` — not enabled.

## Channel

| Field | Value |
|-------|--------|
| Id / name | `#124` `gpt-123458` |
| Type | `1` (OpenAI) |
| Priority / weight | `55` / `25` (strict order: `#21` 60 → **`#124` 55** → `#123` 50) |
| Base | `https://newapi.123458.online` |
| Header | browser-like `User-Agent` (else CF 403/1010) |
| Models | `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-terra` |

## Verification

- Gateway chat: `gpt-5.5` / `gpt-5.6-terra` → `OK` (~2s)
- Note: NewAPI admin `POST /api/channel/` panics on this build — channel inserted via SQLite + `podman restart new-api`
- Backup: `/opt/new-api/backups/one-api.before-123458-vyce-*.db`
