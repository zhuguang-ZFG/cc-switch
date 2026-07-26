# Live Claude settings drift vs takeover aliases (2026-07-26)

## What looked like “settings corruption”

`~\.claude\settings.json` briefly showed:

- Opus → `claude-opus-4-8[1M]`
- Sonnet → `claude-sonnet-4-6[1M]`
- Haiku → `claude-haiku-4-5`

while `zg-gateway-claude` provider still had Opus5 / `glm-5.2[1M]` / LongCat.

This is **by design** for proxy live takeover (`src-tauri/src/services/proxy.rs`):

1. Live `*_MODEL` must be **stable Claude role aliases** (what Claude Code expects in the model menu).
2. Local proxy `model_mapper` maps aliases → provider upstream (`glm-5.2[1M]`, `claude-opus-5[1M]`, `LongCat-2.0`, …).
3. Takeover **strips** `ANTHROPIC_MODEL` from live env (override key list).

Writing raw upstream ids (`glm-5.2[1M]`) into `settings.json` **fights** takeover: the next proxy sync overwrites them back to aliases.

## Real bug (separate)

When live/provider still had `ANTHROPIC_MODEL=claude-opus-5[1M]` and Claude sent already-resolved `glm-5.2[1M]`, old mapper default-fallback rewrote glm→Opus → auto-mode Bash error. Fixed in `model_mapper` + remove provider `ANTHROPIC_MODEL` for installed binary.

## Ops posture (this host)

| Layer | Value |
|-------|--------|
| Provider `zg-gateway-claude` | Sonnet=`glm-5.2[1M]`, Opus=`claude-opus-5[1M]`, Haiku=`LongCat-2.0`; **no** `ANTHROPIC_MODEL` |
| Live `settings.json` | Aliases: sonnet-4-6[1M] / **opus-5[1M]** / haiku-4-5; `model=opus` |
| Smoke | `claude-sonnet-4-6`→`glm-5.2`; `claude-opus-4-8`/`5`→`claude-opus-5`; haiku→Agnes |

## Code follow-ups

- `CLAUDE_TAKEOVER_OPUS_MODEL`: `claude-opus-4-8` → **`claude-opus-5`** (client alias; reduces AUP if anything skips mapper).
- Mapper preserve match: case-insensitive.

Installed official binary still embeds old takeover const until rebuild; live file already uses opus-5 alias. Next sync from old binary may rewrite Opus live key to `claude-opus-4-8[1M]` — still maps to Opus5 via keyword.
