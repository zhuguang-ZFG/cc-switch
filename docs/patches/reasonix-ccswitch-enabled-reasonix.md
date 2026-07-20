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

Local vendor tree (`.vendor/DeepSeek-Reasonix`) has branch
`feat/ccswitch-enabled-reasonix` @ `37a258f` with this change applied
(and UTF-8 BOM stripped).

To open an upstream PR from that tree:

```bash
cd .vendor/DeepSeek-Reasonix
git push -u origin feat/ccswitch-enabled-reasonix
# or push to your fork if you lack write on esengine/DeepSeek-Reasonix:
# git remote add fork git@github.com:<you>/DeepSeek-Reasonix.git
# git push -u fork feat/ccswitch-enabled-reasonix
gh pr create --repo esengine/DeepSeek-Reasonix --base main-v2 \
  --title "fix(ccswitch): import MCP servers with enabled_reasonix" \
  --body "$(cat <<'EOF'
## Summary
- Prefer \`enabled_reasonix = 1\` / \`apps.reasonix\` when importing MCP from CC Switch.
- Keep \`enabled_codex\` / \`apps.codex\` as transitional fallback for older rows.

## Why
CC Switch DB v16 stores Reasonix MCP toggles separately from Codex. Without this, flipping Reasonix in the MCP UI does not affect Reasonix plugin import.

## Test plan
- [ ] With only \`enabled_reasonix=1\` rows, \`LoadCCSwitchMCP\` returns them.
- [ ] With only legacy \`enabled_codex=1\` rows, import still works.
- [ ] Isolated home still returns empty.
EOF
)"
```

If push to `esengine/DeepSeek-Reasonix` is denied, open the PR from a personal fork.
