# Real fixes: model_mapper default + takeover provider source (2026-07-26)

## Root causes (not workarounds)

### 1. `model_mapper` default fallback

`ANTHROPIC_MODEL` used to rewrite **any** unmatched id (including `glm-5.2[1M]`)
to the default (usually Opus). Claude Code / auto-mode then blamed GLM when Opus
503'd.

**Fix:** default fallback only for Claude-family leftovers (`claude*`,
`anthropic/…`). Third-party / explicit upstream ids always passthrough (after
role-keyword + configured-upstream preserve).

### 2. Takeover role fields sourced from live

`apply_claude_takeover_fields_for_provider` for non-managed providers called
`build_claude_takeover_model_fields(live_config)`. On proxy start, live already
holds aliases → `[1M]` / display names no longer tracked from provider upstream
(`glm-5.2[1M]` etc.).

**Fix:** always build takeover role fields from `provider.settings_config`.

### 3. Client Opus alias (earlier)

`CLAUDE_TAKEOVER_OPUS_MODEL`: `claude-opus-4-8` → `claude-opus-5`.

## Tests

- `test_unlisted_glm_not_rewritten_to_default_opus`
- `test_unknown_non_claude_model_passthrough_not_default`
- `test_unknown_claude_family_uses_default`
- `normal_claude_takeover_sources_role_models_from_provider_not_stale_live`

## Ops note

Removing `ANTHROPIC_MODEL` from the provider remains fine under takeover (live
strips it anyway). With this mapper fix it is no longer required as a workaround.
