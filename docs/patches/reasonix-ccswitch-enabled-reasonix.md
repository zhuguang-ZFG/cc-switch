# Reasonix upstream patch: MCP import uses `enabled_reasonix`

Upstream file: `esengine/DeepSeek-Reasonix` `internal/config/ccswitch.go` (`main-v2`).

## Why

Stock Reasonix only imports MCP rows with `enabled_codex = 1` / `apps.codex`.
CC Switch already persists `enabled_reasonix` (DB v16). Without this patch,
toggling Reasonix in the MCP UI does not affect Reasonix CLI plugin import.

## Change (summary)

1. SQLite query:
   - before: `WHERE enabled_codex = 1`
   - after: `WHERE enabled_reasonix = 1 OR enabled_codex = 1` (transitional OR)
2. Legacy `config.json` apps: accept `apps.reasonix` (fallback `apps.codex`).
3. Comments / error strings say Reasonix-enabled.

## Local vendor copy

Patched file lives at:

`.vendor/DeepSeek-Reasonix/internal/config/ccswitch.go`

Copy over a Reasonix source tree before building Reasonix, or open an upstream PR.

## Upstream PR status

Not merged in this cc-switch repo; requires a separate DeepSeek-Reasonix PR.
