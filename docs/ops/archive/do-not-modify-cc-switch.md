# Ops policy: do not modify cc-switch (2026-07-26)

**Status:** Binding for daily DX / NewAPI work on this host.  
**Scope:** Installed **official** CC Switch (farion1231 v3.18.0-class) + live Claude path.

## Rule

**Do not change cc-switch to fix routing / auto-mode / GLM / Opus issues.**

Forbidden without an explicit new user request:

- Edit `src-tauri/` / rebuild / `cargo build` / `pnpm tauri build` for ops fixes
- Replace `CC Switch\cc-switch.exe` or `~\.local\bin\cc-switch.exe`
- Bump / migrate `~\.cc-switch\cc-switch.db` schema (`user_version`) for fork builds
- “Just swap in the fork binary” experiments

## Why

1. Plain `cargo build --release` can embed `--cfg dev` → WebView hits `localhost:3000` → UI `ERR_CONNECTION_REFUSED`.
2. Fork binary migrated DB to **v18**; official app only supports **v16** →「数据库版本过新」.
3. DX problems in this ops loop are almost always **NewAPI channels**, **live Claude env**, or **provider JSON** — not missing UI features.

## Allowed directions (prefer in order)

1. **NewAPI** (Aliyun): channels, abilities, `model_mapping`, weights, `param_override` — then `podman restart new-api` when required. VPS 优化层（kiro-guard、health_check、路由脚本等）已移除，见 `docs/ops/newapi-vps-minimal-state-2026-07-28.md`。
2. **Provider config in DB** (`zg-gateway-claude.settings_config.env`): role upstreams; **keep `ANTHROPIC_MODEL` absent** so old mapper cannot rewrite `glm-*` → Opus.
3. **Live Claude** `~\.claude\settings.json`: takeover-style **official aliases** only (`claude-sonnet-4-6[1M]`, `claude-opus-5[1M]`, `claude-haiku-4-5`); `PROXY_MANAGED` + `:15721`; `model=opus`. Do **not** put raw `glm-5.2[1M]` into live Sonnet (fights takeover sync).
4. Docs / patches under `docs/ops/` and `docs/patches/`.

## Live posture that works with stock mapper

| Layer | Expectation |
|-------|-------------|
| App | Official installer binary; DB `user_version=16` |
| Provider Sonnet upstream | `glm-5.2[1M]` |
| Provider Opus / Fable / Haiku | `claude-opus-5[1M]` / **`claude-fable-5`** / `LongCat-2.0` |
| Provider `ANTHROPIC_MODEL` | **unset** |
| Live Sonnet / Opus / Haiku | `claude-sonnet-4-6[1M]` / `claude-opus-5[1M]` / `claude-haiku-4-5` |
| Auto-mode Bash | Uses Sonnet **alias** → proxy keyword map → glm (Zhipu) |

## Incident notes (2026-07-26)

- Symptom: `glm-5.2[1M] is temporarily unavailable` (auto-mode Bash).
- Mechanism on **stock** mapper: unmatched ids + `ANTHROPIC_MODEL` → Opus; Opus 503 blamed as GLM.
- Mitigation used: remove provider `ANTHROPIC_MODEL` + live official aliases (ops-only).
- Repo commits that touch `model_mapper` / takeover (`b0e3db1d`…`cc4b7a50`) are **not** deployed to the installed app; treat as future upstream only — **do not install from this branch for daily use**.

## Related

- Routing snapshot: `docs/ops/zg-claude-routing.md`
- Current NewAPI/Kimi/MCP/Claude state: `docs/ops/newapi-kimi-mcp-claude-current-state-2026-07-28.md`
- Historical Cursor ops loop (retired): `docs/ops/newapi-dx-cursor-ops.md`
- Takeover alias design: `docs/patches/local-claude-takeover-aliases-2026-07-26.md`
