# jianzhile.vip — Claude Fable 5 (肥波5) (2026-07-26)

**Host:** Aliyun NewAPI `47.112.162.80`  
**Upstream:** `https://jianzhile.vip`  
**Channel:** `#127` `jianzhile-fable`  
**Backup:** `/opt/new-api/backups/one-api.before-jianzhile-fable-20260726-151900.db`

## What it is

NewAPI-style hub. Public pricing lists **only** `claude-fable-5` (Anthropic), gated to group **`Claude-CC-MAX`**.

Token default group is **`GPT`** → without override, every model returns 503  
`No available channel … under group GPT`.

## Channel config (ZG)

| Field | Value |
|-------|--------|
| id | `#127` |
| type | 14 (Anthropic `/v1/messages`) |
| status | 1 |
| priority / weight | **50 / 20**（高于 Opus 主池 pri45，且 **仅** fable 模型，不抢 Opus 流量） |
| models | `claude-fable-5`, `claude-fable-5[1M]` |
| model_mapping | `[1M]` / `[1m]` → `claude-fable-5` |
| header_override | `New-Api-Group: Claude-CC-MAX` (+ anthropic-version / UA) |

Do **not** put Opus/Sonnet/Haiku on this channel — upstream has no channels for them under usable groups.

## Smoke

Direct upstream (with `New-Api-Group`): English creative prompt → `stop_reason=end_turn`, non-empty text.  
Some short/CN prompts → `refusal` or 403 (upstream filter) — treat as noise, not channel down.

Via ZG gateway: `claude-fable-5` / `[1M]` after `podman restart new-api`.

## Ops notes

- Key lives only in NewAPI DB — **never** commit to git / patches.
- Stock cc-switch: Fable role still maps via provider `ANTHROPIC_DEFAULT_FABLE_MODEL` (often Opus5). To *use* 肥波5, client must request `claude-fable-5` (or set Fable upstream to that id in provider env — config only, no cc-switch rebuild).
- Policy: `docs/ops/do-not-modify-cc-switch.md`.

## Related

- Routing: `docs/ops/zg-claude-routing.md`
