# Patch: Cursor CLI vs IDE vs NewAPI client surfaces (2026-07-26)

## Why

Ops session tried to put ZG NewAPI models into “Cursor”; user was on **Agent CLI**, not IDE. Cyber Safeguards errors on **Opus 4.8 (1M)** were Cursor-cloud Anthropic, not NewAPI.

## Decisions

| Surface | Route |
|---------|--------|
| Claude Code | ZG `https://aliyun.donglicao.com` (unchanged) |
| Cursor IDE BYOK | Base URL `/v1` + `zg-*` ids mapped on NewAPI to real models |
| Cursor Agent CLI | Official catalog only; default `claude-opus-5-high`; avoid `claude-opus-4-8-*` for cyber work |

## Live NewAPI (Cursor IDE aliases)

Channel `model_mapping` includes e.g. `zg-claude-opus-5` → `claude-opus-5[1M]` (and glm/gpt/longcat siblings). Smoke via `/v1/chat/completions` returned 200 for the zg-* ids used.

## Docs

- `docs/ops/newapi-dx-cursor-ops.md` — client matrix + CLI/IDE BYOK
- `docs/ops/zg-claude-routing.md` — pointer under Cursor ops loop
