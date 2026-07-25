# Schema v18 — TEXT `created_at` crash + local binary pitfalls

**Date:** 2026-07-25  
**Status:** Fixed in tree (`SCHEMA_VERSION = 18`)

## Symptoms

1. Claude Code / setup hook: `500 数据库错误: Invalid column type Text at index: 5, name: created_at` (often with long client retries).
2. `crash.log`: `Failed to setup app: … Invalid column type Text … name: created_at`.
3. After a cargo-only release binary: UI `ERR_CONNECTION_REFUSED` to `localhost` (WebView hits `devUrl` `http://localhost:3000`).
4. After that binary raised the DB to v18, rolling back a v17 UI package shows **数据库版本过新** and refuses to start.

## Root cause

- Ops / ad-hoc SQL sometimes wrote `datetime('now')` into `providers.created_at` (declared INTEGER millis).
- DAO used `row.get::<_, Option<i64>>(…)` → rusqlite type error → app setup abort.
- `cargo build --release` does **not** embed `frontendDist`; only `pnpm tauri build` (or an official installer) does.

## Code fix

| Piece | Role |
|-------|------|
| `src-tauri/src/database/timestamp.rs` | `OptionalUnixMillis` — decode INTEGER or common TEXT forms; never brick on TEXT |
| `migrate_v17_to_v18` | Rewrite TEXT datetime/numeric cells to INTEGER millis for providers/prompts/profiles |
| `save_provider` | Default insert millis; update uses `COALESCE(?5, created_at)` |
| DAO reads in providers / prompts / profiles | Use `OptionalUnixMillis` |

Also retained in the same change set: Reasonix→Anthropic `[1M]` strip + `context-1m` beta; `[[route:…[1M]]]` marker parse; Reasonix restore without stash must not clear live proxy env; treat bare `cc-switch-proxy` as a local placeholder model.

## Ops notes

- Prefer app-layer millis or `strftime('%s','now')*1000` — never `datetime('now')` on INTEGER timestamp columns.
- Local binary: use `pnpm tauri build` for anything that must show UI.
- If a newer build briefly set `user_version=18` and you must run a v17 UI binary again: v18 added **no columns** (data normalize only). After backup, `PRAGMA user_version = 17;` is acceptable. Do not apply this pattern to unknown future migrations.
- Proxy health (`http://127.0.0.1:15721/health`) ≠ UI health; both must be checked after a binary swap.
