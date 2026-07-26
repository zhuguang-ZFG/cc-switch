# Fix: glm Sonnet rewritten to Opus (2026-07-26)

## Symptom

Claude Code auto mode:

```text
Error: glm-5.2[1M] is temporarily unavailable, so auto mode cannot
determine the safety of Bash right now.
```

## Root cause

cc-switch `model_mapper` maps role keywords (`sonnet`/`opus`/…) to
`ANTHROPIC_DEFAULT_*_MODEL`. Claude Code then **re-sends the resolved
upstream id** (e.g. `glm-5.2[1M]`). That id has no role keyword, so the
old mapper fell through to `ANTHROPIC_MODEL` (= `claude-opus-5[1M]`).

Effect: every Sonnet/glm/Haiku classifier call was forced onto the Opus
community pool. When Opus returned 503「无可用渠道」, the client blamed
`glm-5.2[1M]`.

Evidence (proxy logs): `request_model=glm-5.2` → `model=claude-opus-5`.

## Fix

1. **Code** (`src-tauri/src/proxy/model_mapper.rs`): before default
   fallback, preserve any request that already equals a configured role
   upstream (`sonnet`/`opus`/`haiku`/`fable`/`subagent`/`ANTHROPIC_MODEL`),
   ignoring `[1M]` suffix. Unit test:
   `test_sonnet_upstream_id_preserved_before_default_fallback`.

2. **Live config (installed binary, until rebuild):** removed
   `ANTHROPIC_MODEL` from `zg-gateway-claude` + `~\.claude\settings.json`
   so the old mapper cannot rewrite glm→opus. Role models kept:
   Sonnet=`glm-5.2[1M]`, Opus=`claude-opus-5[1M]`, Haiku=`LongCat-2.0`.

   DB backup: `~\.cc-switch\cc-switch.db.bak.glm-passthrough-*`

## Verify

```text
POST :15721 /v1/messages model=glm-5.2[1M] → resp.model=glm-5.2 OK
model=claude-sonnet-4-6 → resp.model=glm-5.2 OK
model=LongCat-2.0 → agnes-2.0-flash OK
```

Restart Claude Code after config change. Opus 503 flaps remain community
noise and no longer block Bash auto-mode when using Sonnet/glm.
