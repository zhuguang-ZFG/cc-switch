# ZG NewAPI — Claude role routing (ops snapshot)

**Updated:** 2026-07-26 (anti-stall: Opus w 50/42/12/3; AR park 119/120; analyze 4h)  
**Gateway:** `https://aliyun.donglicao.com` (NewAPI on Aliyun `47.112.162.80`)  
**Night / gov logs:** `docs/patches/newapi-dx-2026-07-26-night.md`, `docs/patches/newapi-dx-gov-b-2026-07-26.md`, `docs/patches/newapi-dx-anti-stall-2026-07-26.md`

Ops snapshot for Claude Code through ZG NewAPI. Channel IDs/weights drift — verify on live admin UI after changes. Prefer fixing NewAPI for developer experience; do not assume “healthy” without smoke.

## Strict failover order (2026-07-26)

Higher `priority` first. **Same priority is weight-biased pick, not a fixed sequence** — on error NewAPI retries (`RetryTimes=3`) and may draw another channel in the same pri band.

### Local Claude FQ (cc-switch)

1. `zg-gateway-claude` (current, sort 10)  
2. `agentrouter-2` (sort 20)  
林夕 / anyrouter **不在** FQ。

### NewAPI ladders

| Ladder | Order (pri/w) |
|--------|----------------|
| **GPT** | `#21` 60/40 → `#124` 55/25 → `#123` 50/20（跨 pri 才是严格先后） |
| **GLM / default Sonnet** | `#41/#42` zhipu 80/50·8 → `#123` hongshi 50/20 |
| **Haiku** (`claude-haiku-*` / dated) | `#122` Agnes 40/20 → `#125` Vyce 35/20 → `#90` LongCat 30/10 |
| **Haiku alias** `LongCat-2.0` | Agnes `#122` maps → `agnes-2.0-flash`（与上不同 model id） |
| **Anthropic Sonnet** | `#125` Vyce 35/20 only |
| **Opus** | pri45 `#9/#10/#20/#60`（w **50/42/12/3**）→ AR `#118` w6；`#119/#120` Opus abilities off；**`#81/#11` status=2**；analyze 每 4h 自动降慢渠 |
| **Vyce OpenAI** | `#126` 48/15（deepseek/minimax/mimo；在 hongshi 之后） |

`#83/#84/#86` AR-GPT 与其它噪声渠：`abilities.enabled=0`。`#11`/`#81` **status=2** + health `AUTO_REACTIVATE_EXCLUDE`。Vyce **无** Opus。防卡顿：`docs/patches/newapi-dx-anti-stall-2026-07-26.md`（慢渠只降不升 + FORCE_DEMOTE）。

## Local entry (required)

| Item | Value |
|------|--------|
| Current provider | `ZG网关 Claude` (`zg-gateway-claude`) → `https://aliyun.donglicao.com` |
| Default / Sonnet | `glm-5.2[1M]` (via ZG) |
| Failover queue | **1.** ZG → **2.** `agentrouter-2`（林夕 / Sub2API / 百倍直连 **不进 FQ**） |
| Proxy knobs | `streaming_first_byte_timeout=25`, `max_retries=2` |
| Daily model | ZG `ANTHROPIC_MODEL=claude-opus-5[1M]`；Sonnet→`glm-5.2[1M]`；Haiku 客户端可发 `LongCat-2.0`（Agnes 映射）或 `claude-haiku-*`（梯队 Agnes→Vyce→LongCat） |
| Guard coverage | 经 ZG：百倍 `#9/#10/#20`→`:8403-5`、k40/林夕 `#60`→`:8400` **有** kiro-guard。本机直连林夕/Sub2API/百倍/AR2 **无** guard — **勿手动切 current**；FQ#2 直连 AR 是有意取舍（ZG 全挂时仍有后备，不叠本机 guard） |
| Do not | Make Sub2API / 林夕 / 百倍直连 / anyrouter current for daily Claude；勿把 FQ#2 也指回 ZG（无真正后备） |
| Opus-first | Keep `claude-opus-5[1M]` for Opus/Fable/Subagent/Reasoning; **do not** demote to glm for speed. Speed = latency-weighted Opus channels + guard 502 (community / Kiro-Go #141 spirit). Keep `first_byte=25s`, `max_retries=2` — no ad-hoc tighter first_byte. |

## AgentRouter / AnyRouter (2026-07-26)

| Surface | State | How it works |
|---------|--------|----------------|
| NewAPI `#118/#119/#120` agentrouter-claude | **Live** type=14, pri 30/28/26, w 12/10/8 | base=`127.0.0.1:841x` AR-guard；proxy 在 guard（`KIRO_GUARD_PROXY=7890`），渠 `setting.proxy` 已空；map `claude-opus-5`/`[1M]` → `claude-opus-4-8`（**no** upstream `[1m]`） |
| NewAPI `#83/#84/#86` agentrouter GPT/GLM | **Parked** type=1；`abilities.enabled=0` | 保留渠道配置，不参与 GPT/GLM 路由 |
| Local `agentrouter-2` | In FQ #2 after ZG | Desktop 直连 AR（**无** VPS guard）；仅 ZG 不可用时兜底；勿 map `[1m]`；勿改指 ZG |
| NewAPI `#52` anyrouter-anthropic | **Staged, status=2** | Headers + `[1m]` models ready; FC still **503**; `auto_ban=0`; enable only after smoke |
| Local `anyrouter.top` | ACL blocked | 403「令牌无权访问 …[1m]」→ rebuild token with unrestricted models |

## Role → upstream (intended)

| Claude Code role | Requested model id(s) | Primary NewAPI route | Notes |
|------------------|----------------------|----------------------|--------|
| Opus / Fable / Subagent / Reasoning | `claude-opus-5` / `claude-opus-5[1M]` | pri45 w50/42/12/3（`#9/#10/#20/#60`）→ AR `#118` w6 | `#119/#120` Opus off；`#81/#11` pinned；analyze 4h 自动降慢渠 |
| Sonnet / default | `glm-5.2` / `glm-5.2[1M]` | Zhipu `#41/#42` (80) → hongshi `#123` (50) | `enable_thinking=false` on zhipu |
| Haiku | `claude-haiku-*` / dated | Agnes `#122` (40) → Vyce `#125` (35) → LongCat `#90` (30) | `LongCat-2.0` id → Agnes map `agnes-2.0-flash` |
| GPT (OpenAI path) | `gpt-5.5` / `gpt-5.6-*` / … | `#21` (60) → `#124` (55) → `#123` (50) | type=1；123458 需浏览器 UA |
| Anthropic-native Sonnet | `claude-sonnet-4-6` / `claude-sonnet-5` | Vyce `#125` (35) | 日常 Sonnet 仍走 glm；`docs/patches/vyceai-newapi.md` |

## DX fixes (2026-07-25)

| Issue | Symptom | Fix |
|-------|---------|-----|
| `glm-5.2[1M]` missing | `No available channel` if client sends `[1M]` | Abilities + map → `glm-5.2` on `#41/#42/#123`; ModelRatio/CompletionRatio |
| GLM empty completion | `reasoning_tokens` eats `max_tokens` | Zhipu `#41/#42` `param_override={"enable_thinking":false}` |
| Agnes / dated Haiku | price / ability gaps | Agnes `#122` maps + prices |
| Ability weight=0 | Channel weight tuning ignored | Sync `abilities.priority/weight` from `channels` for managed pools |
| Kiro fake-complete stop | HTTP 200 + `end_turn` mid-answer | `kiro_guard`: force non-stream + soft classify → retry → 502; text heuristics; `SHORT_OUT=64`; SOFT_LIMIT for empty tools. Community: Kiro-Go #141/#142/#143 (we don't self-host). |
| Health-check key on argv | `curl … Authorization: Bearer …` visible in `ps` | `health_check.py` v4 uses urllib; disabled `#13` until key rotated |
| Fake 「额度耗尽 429」 | `#11` DISABLE-QUOTA on 503 / no accounts | Tighten quota classifier: billing signals only; bare 429/rate_limit → fails path |
| Nested retries too deep | Guard soft-retry × NewAPI RetryTimes × local retries → 504 | Global `RetryTimes=3`; local `max_retries=2` / FB=25s |
| Dual k40 high weight | `#11`+#60 both → `8400` fake failover | Keep `#11` disabled; `#60` w8 until k40 stable |
| `#11` flap back on | health DISABLE-QUOTA then REENABLE / AUTO-REACTIVATE + `/status` re-enables abilities | Pin `status=2`; `AUTO_REACTIVATE_EXCLUDE` incl. 11 + SKIP-REENABLE; do not bounce `/status` |
| `#81` Opus after strip | DB abilities=0 but memory still routes | Always `podman restart new-api` after ability/model strip |
| AgentRouter keyword / sensitive_words | 400/405 passed through → NewAPI may not switch | `kiro_guard` remaps content-block → **502** immediately (`CONTENT_BLOCK_FAILOVER`) |
| AgentRouter long-prompt WAF | `500 sensitive_words_detected` (community) | AR-only Cyrillic-Bypass (`KIRO_GUARD_CYRILLIC_BYPASS=1` on 8410–8412); source: marko1olo/agentrouter-setup-guide |
| Broken 03:00 backup | Copies missing `/opt/new-api/one-api.db` | Removed; keep `backup_db.sh` at 03:17 |
| Local current=Sub2API | Bypasses ZG pool + guard; Sonnet forced to Opus | Restore ZG current; lean FQ; FB=25s / retries=2 |

Smoke (gateway + local `:15721`): `glm-5.2` / Haiku / `claude-opus-5[1M]` / sonnet-alias→`glm-5.2` → 200.

Detail: `docs/patches/newapi-dx-2026-07-26-night.md`, `docs/patches/newapi-dx-2026-07-25.md`, `docs/patches/agnes-haiku-newapi.md`.

## Cursor ops loop (ongoing)

- Runbook: `docs/ops/newapi-dx-cursor-ops.md`
- Local: `python scripts/ops/newapi-dx-analyze.py`
- Auto in-band: soft-trunc env (`KIRO_GUARD_SHORT_*`) + Opus weights; escalate when out of band.
- 2026-07-26 night: Opus weights **#9/#10/#20 → 50/40/32**; report `dx-20260726-021931.md`.

## Client note (cc-switch)

- Prefer **official** [farion1231/cc-switch](https://github.com/farion1231/cc-switch/releases) installers for daily use (e.g. v3.18.0 → DB support **v16**).
- Fork schema v17+ (`enabled_pi`) / v18 will trip **数据库版本过新** on official — after backup, `PRAGMA user_version = 16;` is the known downgrade for this ops path (extra columns ignored).
- Do not replace the installed UI with `cargo build --release` only.

## Explicit do-nots

- Do not promote Agnes / GPT-only free keys into type=14 Opus.
- Do not invent fake `claude-*` aliases on LongCat (tool JSON 400s).
- Do not leave `glm-5.2[1M]` without NewAPI ability (proxy strip is not always present).
- Do not re-enable zhipu thinking unless Claude Code `max_tokens` budget is proven large enough.
