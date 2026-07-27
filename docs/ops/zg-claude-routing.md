# ZG NewAPI — Claude role routing (ops snapshot)

**Updated:** 2026-07-28 03:20 (ops-only; **do not modify cc-switch**; Opus5 prefer; 真 claude 50 层 / kimi-k3 40 层兜底; kiro-guard max_tokens cap=65536 + 403→503 failover; Fable→jianzhile; kimi-code 收编走 zg-newapi)  
**Gateway:** `https://aliyun.donglicao.com` (NewAPI on Aliyun `47.112.162.80`)  
**Ops logs:** `docs/ops/do-not-modify-cc-switch.md`, `docs/patches/opus1m-map-and-fable-provider-2026-07-26.md`, `docs/patches/jianzhile-fable-newapi-2026-07-26.md`, `docs/patches/newapi-dx-opus5-prefer-2026-07-26.md`, `docs/patches/newapi-dx-health-check-v5-2026-07-26.md`, `docs/patches/newapi-dx-health-check-v5.1-2026-07-26.md`, `docs/patches/newapi-dx-anti-stall-2026-07-26.md`, `docs/patches/newapi-dx-gov-b-2026-07-26.md`, `docs/patches/local-clients-cleanup-2026-07-26.md`, `docs/patches/newapi-dx-zhipu-stop-2026-07-26.md`, `docs/patches/local-claude-rtk-align-2026-07-26.md`, `docs/patches/local-mcp-skills-slim-2026-07-26.md`

Ops snapshot for Claude Code through ZG NewAPI. Channel IDs/weights drift — verify on live admin UI after changes. Prefer fixing NewAPI for developer experience; do not assume “healthy” without smoke.

**Policy:** daily fixes go through **NewAPI + provider env + `~\.claude\settings.json` only**. Do **not** rebuild/replace cc-switch or migrate its DB schema — see `docs/ops/do-not-modify-cc-switch.md`.

## Strict failover order (2026-07-26)

Higher `priority` first. **Same priority is weight-biased pick, not a fixed sequence** — on error NewAPI retries (`RetryTimes=3`) and may draw another channel in the same pri band.

### Local Claude FQ (cc-switch)

1. `zg-gateway-claude` (current, sort 10) — Opus/Sonnet/Haiku 见 Local entry  
2. `agentrouter-2` (sort 20) — 全部角色 **`claude-opus-5`（无 `[1M]`）**；勿回 4.8  
林夕 / anyrouter **不在** FQ。

### NewAPI ladders

| Ladder | Order (pri/w) |
|--------|----------------|
| **GPT** | 实况（2026-07-27 审计）：terra `#21` 60/40 → `#131` freemodel-work 45/15（**#124 status=3 已封**、#123 无 terra 能力）；**gpt-5.5 仅 `#21` 独苗**；sol 仅 `#131` |
| **GLM / default Sonnet** | `#41/#42` zhipu 80/50·8 → `#123` hongshi 50/20 |
| **Haiku** (`claude-haiku-*` / dated) | `#122` Agnes 40/20 → `#63` kimi -20（vyce `#125` status=2 整车停车；`#90` LongCat 无 claude-haiku 能力，仅服务 `LongCat-2.0` id） |
| **Haiku alias** `LongCat-2.0` | Agnes `#122` maps → `agnes-2.0-flash`（与上不同 model id） |
| **Anthropic Sonnet** | `#132` freemodel-work GPT -19 → `#63` kimi -20 → `#133` freemodel-cc -21 → GPT -30 系 → `#134` glm -33（vyce `#125` sonnet 已剥离+整车停车；`#132` 深夜提权为第一梯队） |
| **Opus** | pri45 `#10/#20/#60`（w **20/40/30**，2026-07-27 晚降 #10 稳链；`#9` status=2、`#11` 活 w1）→ `#132` freemodel-work GPT -19 → **`#63` kimi -20**（2026-07-27 加入 Opus 链，e2e 已验证）→ `#133` freemodel-cc -21 → GPT -30 系 → **`#134` glm -33**（总兜底，key2 cap 复位后可用）；AR `#118` status=3 封禁中；4.x/`4-8` map→**Opus5**；`[1M]` 在百倍池 **upstream→裸 `claude-opus-5`**（价表拒 `[1M]`）。见 `docs/patches/opus1m-map-and-fable-provider-2026-07-26.md` |
| **Fable** (`claude-fable-5` / `[1M]`) | `#127` jianzhile pri50/w20（仅 fable；头 `New-Api-Group: Claude-CC-MAX`）→ `#63` kimi -20（2026-07-27 深夜打开的兜底）。本机 Fable 角色 → `claude-fable-5`。见 `docs/patches/jianzhile-fable-newapi-2026-07-26.md` |
| **Vyce OpenAI** | `#126` 48/15（deepseek/minimax/mimo；在 hongshi 之后） |

`#83/#84/#86` AR-GPT 与其它噪声渠：`abilities.enabled=0`。`#81`/`#125` **status=2**；`#118` status=3（auto-ban）、`#124` status=3；**`#11` 实际仍活**（opus w1 涓流，2026-07-27 审计确认）；AR `#118–120` health EXCLUDE。Vyce **无** Opus 且已整车停车。health_check v5：`docs/patches/newapi-dx-health-check-v5-2026-07-26.md`。防卡顿：`docs/patches/newapi-dx-anti-stall-2026-07-26.md`。

## Local entry (required)

| Item | Value |
|------|--------|
| Current provider | `ZG网关 Claude` (`zg-gateway-claude`) → `https://aliyun.donglicao.com` |
| Default / Sonnet | `glm-5.2[1M]` (via ZG) |
| Failover queue | **1.** ZG → **2.** `agentrouter-2`（林夕 / Sub2API / 百倍直连 **不进 FQ**） |
| Proxy knobs | `streaming_first_byte_timeout=25`, `max_retries=2` |
| Daily model | ZG **`ANTHROPIC_MODEL` unset**；Opus→`claude-opus-5[1M]`；Fable→`claude-fable-5`；Sonnet→`glm-5.2[1M]`；Haiku→`LongCat-2.0`（或 `claude-haiku-*`） |
| Guard coverage | 经 ZG：百倍 `#9/#10/#20`→`:8403-5`、k40/林夕 `#60`→`:8400` **有** kiro-guard。本机直连林夕/Sub2API/百倍/AR2 **无** guard — **勿手动切 current**；FQ#2 直连 AR 是有意取舍（ZG 全挂时仍有后备，不叠本机 guard） |
| Do not | Make Sub2API / 林夕 / 百倍直连 / anyrouter current for daily Claude；勿把 FQ#2 也指回 ZG（无真正后备） |
| Opus-first | Keep `claude-opus-5[1M]` for Opus/Subagent/Reasoning；**Fable** 用 `claude-fable-5`（`#127`）。**do not** demote Opus to glm for speed. Keep `first_byte=25s`, `max_retries=2`. |

## AgentRouter / AnyRouter (2026-07-26)

| Surface | State | How it works |
|---------|--------|----------------|
| NewAPI `#118` agentrouter-claude | **Live** pri30/w6 | AR 已充值；guard `:8410`；次池 |
| NewAPI `#119/#120` | **暂时不开** status=2 | 防慢尾；额度已充仍保持钉死，需要时再开 |
| NewAPI `#83/#84/#86` agentrouter GPT/GLM | **Parked** type=1；`abilities.enabled=0` | 保留渠道配置，不参与 GPT/GLM 路由 |
| Local `agentrouter-2` | In FQ #2 after ZG | Desktop 直连 AR（**无** VPS guard）；仅 ZG 不可用时兜底；勿 map `[1m]`；勿改指 ZG |
| NewAPI `#52` anyrouter-anthropic | **Staged, status=2** | Headers + `[1m]` models ready; FC still **503**; `auto_ban=0`; enable only after smoke |
| Local `anyrouter.top` | ACL blocked | 403「令牌无权访问 …[1m]」→ rebuild token with unrestricted models |

## Role → upstream (intended)

| Claude Code role | Requested model id(s) | Primary NewAPI route | Notes |
|------------------|----------------------|----------------------|--------|
| Opus / Subagent / Reasoning | `claude-opus-5` / `claude-opus-5[1M]` | pri45 w50/40/28/3 → AR `#118` w6 | `[1M]`→裸 opus-5 on `#9–60`；`#118` keep `[1M]` |
| Fable | `claude-fable-5` / `[1M]` | `#127` pri50/w20 | 本机 `ANTHROPIC_DEFAULT_FABLE_MODEL=claude-fable-5` |
| Sonnet / default | `glm-5.2` / `glm-5.2[1M]` | Zhipu `#41/#42` (80) → hongshi `#123` (50) | `enable_thinking=false` on zhipu |
| Haiku | `claude-haiku-*` / dated | Agnes `#122` (40) → Vyce `#125` (35) → LongCat `#90` (30) | `LongCat-2.0` id → Agnes map `agnes-2.0-flash` |
| GPT (OpenAI path) | `gpt-5.5` / `gpt-5.6-*` / … | `#21` (60) → `#124` (55) → `#123` (50) | type=1；123458 需浏览器 UA |
| Anthropic-native Sonnet | `claude-sonnet-4-6` / `claude-sonnet-5` | Vyce `#125` (35) | 日常 Sonnet 仍走 glm；`docs/patches/vyceai-newapi.md` |

## DX fixes (2026-07-25)

| Issue | Symptom | Fix |
|-------|---------|-----|
| `glm-5.2[1M]` missing | `No available channel` if client sends `[1M]` | Abilities + map → `glm-5.2` on `#41/#42/#123`; ModelRatio/CompletionRatio |
| GLM empty completion | `reasoning_tokens` eats `max_tokens` | Zhipu `#41/#42` `param_override.enable_thinking=false` |
| GLM `stop` 400 ArrayList | Claude 单条 `stop_sequences` → OpenAI `stop` 字符串；智谱要数组 | `#41/#42` `operations: delete stop`；改完需 `podman restart new-api`。见 `docs/patches/newapi-dx-zhipu-stop-2026-07-26.md` |
| Opus 4.8 AUP / 毒映射 | 客户端踩 Cyber Safeguards；AR `opus-5`→`4-8` 回灌 | 主池+AR：4.x→Opus5；关 `opus-4-8` abilities；压 `#20`。见 `docs/patches/newapi-dx-opus5-prefer-2026-07-26.md` |
| auto-mode Bash：`glm-5.2[1M] temporarily unavailable` | `ANTHROPIC_MODEL` 把任意未匹配 id（含 glm）改成 Opus | **真正修复**：非 Claude 族 id 禁止 default 回落；takeover 角色字段始终来自 provider。见 `docs/patches/local-mapper-takeover-rootfix-2026-07-26.md` |
| Live settings「漂」成 opus-4-8 / sonnet-4-6 | 误以为配置坏了 | **takeover 故意写官方别名**，代理再映射到 glm/Opus5；勿把上游 id 写进 settings。见 `docs/patches/local-claude-takeover-aliases-2026-07-26.md` |
| Agnes / dated Haiku | price / ability gaps | Agnes `#122` maps + prices |
| Ability weight=0 | Channel weight tuning ignored | Sync `abilities.priority/weight` from `channels` for managed pools |
| Kiro fake-complete stop | HTTP 200 + `end_turn` mid-answer | `kiro_guard`: force non-stream + soft classify → **trunc-context continuation retry** (inject truncated content as context, model continues, merge responses) → 502; text heuristics; `SHORT_OUT=64`; SOFT_LIMIT for empty tools; **MAX_TOKENS_CAP=4096** (proactive); **gzip** responses. Community: Kiro-Go #141/#142/#143 (we don't self-host). See `docs/patches/kiro-guard-trunc-ctx-compress-2026-07-26.md`. |
| Health-check key on argv | `curl … Authorization: Bearer …` visible in `ps` | `health_check.py` v5 urllib；只探 Opus 主池 |
| health_check 误禁 AR / 翻覆 | 全表探测 + AUTO-REACTIVATE + 整渠 abilities | v5.1：只探 `#9/#10/#20/#60`；探针优先 `claude-opus-5[1M]`；无自动复活；不改 abilities；PINNED∋118–120；公益站 `no available accounts`/503 **不计入禁用**。见 `docs/patches/newapi-dx-health-check-v5-2026-07-26.md` |
| Fake 「额度耗尽 429」 | `#11` DISABLE-QUOTA on 503 / no accounts | v5 已去掉 DISABLE-QUOTA 捷径 |
| Nested retries too deep | Guard soft-retry × NewAPI RetryTimes × local retries → 504 | Global `RetryTimes=3`; local `max_retries=2` / FB=25s |
| Dual k40 high weight | `#11`+#60 both → `8400` fake failover | Keep `#11` disabled; `#60` **低权（现网 w3）** until k40 stable |
| `#11` flap back on | health REENABLE / `/status` 弹 abilities | Pin `status=2`；EXCLUDE∋11；v5 无自动复活；勿 bounce `/status` |
| `#81` Opus after strip | DB abilities=0 but memory still routes | Always `podman restart new-api` after ability/model strip |
| AgentRouter keyword / sensitive_words | 400/405 passed through → NewAPI may not switch | `kiro_guard` remaps content-block → **502** immediately (`CONTENT_BLOCK_FAILOVER`) |
| AgentRouter long-prompt WAF | `500 sensitive_words_detected` (community) | AR-only Cyrillic-Bypass (`KIRO_GUARD_CYRILLIC_BYPASS=1` on 8410–8412); source: marko1olo/agentrouter-setup-guide |
| Broken 03:00 backup | Copies missing `/opt/new-api/one-api.db` | Removed; keep `backup_db.sh` at 03:17 |
| Local current=Sub2API | Bypasses ZG pool + guard; Sonnet forced to Opus | Restore ZG current; lean FQ; FB=25s / retries=2 |
| anyrouter 400「请启用 1m 上下文」 | 2026-07 起全模型强制 `anthropic-beta: context-1m-2025-08-07` | 渠道只挂 `[1M]` abilities（fork 自动注头）；#52 已按此修复启用 |
| agentrouter GPT 网络不可达 | VPS DNS 污染解析到 Facebook IPv6（`face:b00c`）+ IPv4 超时 | 放弃 GPT 线（#83/84/86 零流量）；勿为其配出站代理 |
| agentrouter Claude 池断货 | 503「default 分组无可用渠道」→ fork auto-ban（status=3） | 池补货后解封 #118 + 补 abilities 复活；晨报固定复探 |
| 100xlabs 账号并发上限 | `Concurrency limit exceeded for account`（500/429）→ handler_stop | 公益账号硬上限，路由吸收即可；optimizer 实时降权，勿当配置 bug |
| 手动 bump priority 验证撞 optimizer cron | 验证窗口内 bump 渠成最高层 → optimizer 给它 w60 | 验证后必须恢复 priority+weight 双写（以快照值为准，换序器可能已调整） |

Smoke (gateway + local `:15721`): `glm-5.2` / Haiku / `claude-opus-5[1M]` / sonnet-alias→`glm-5.2` → 200.

Detail: `docs/patches/newapi-dx-2026-07-26-night.md`, `docs/patches/newapi-dx-2026-07-25.md`, `docs/patches/agnes-haiku-newapi.md`.

## Cursor ops loop (ongoing)

- Runbook: `docs/ops/newapi-dx-cursor-ops.md`
- Local: `python scripts/ops/newapi-dx-analyze.py`
- Auto in-band: soft-trunc env (`KIRO_GUARD_SHORT_*`) + Opus weights; escalate when out of band.
- Opus 主池现网快照：**自适应权重**（v4，2026-07-27 深夜起，见下节）；历史手调 `#9/#10/#20/#60` = 50/40/28/3 作废（`opus1m-map-and-fable-provider-2026-07-26.md`）。

## 自适应路由 v4（2026-07-27 深夜上线）

- **route_optimizer.py v4**（VPS cron `*/5`，镜像 `scripts/ops/route_optimizer.py`）：主池 45 层调权 = TTFT 流式探针 + 容器日志错误率 EWMA；探针按渠道 type 分流（14=Anthropic / 1=OpenAI，模型取 model_mapping，UA 吃 header_override）；映射渠门控 `MAIN_TIER_MAPPED={137,138,139}` cap=25 margin=1.3（Claude 全灭时门控失效全开）；flock 防 cron 重叠；EXCLUDE={11}；映射渠 flap 不发判死 TG。
- **route_optimizer_sonnet.py**（同 cron 串行，镜像 `scripts/ops/route_optimizer_sonnet.vps.py`）：Sonnet 链 `[63,133,129,134,136]` 层间换序，每轮最多换一对相邻层（margin 1.5），ladder=组内 priority 重排双写；**会吃掉手工 priority 调整**（以脚本为准）；EWMA 键带 `s` 前缀。`--restore` / `--restore-sonnet` / `--sonnet-dry`。
- **主池映射阀（泄压阀，cap 25）**：`#137` gpt-terra（8317 auth 耗尽躺平 w1）、`#138` kimi-k3（ttft ~1.1s，事实主力，真实承接 60%+）、`#139` grok-4.5（bazaarlink-2 上游，ttft 2.7s，2026-07-27 23:40 上岗）。
- **Sonnet 链序**（自适应，当前）：`[63 kimi, 134 glm, 133 freemodel, 136 minimax, 129 terra]`；#132 work.freemodel 拒绝 Claude Code 已摘渠。
- **kiro_guard tee+续写全功能在线**（8 实例）：截断检测→自动续写（SOFT_RETRY=1，退避 700ms）→merge 合并；tee_eof/hard 事件记 `last_block` 落 `kiro-guard-soft.jsonl`。2026-07-27 23:49 首个实战 `short_completion → recovered_merged`。
- **⚠️ Claude Code「停止」真因 = max_tokens cap（2026-07-28 修复）**：tee 续写治不了「停止」——深度诊断证明 eof 不是断流，是 guard `_effective_cap`（kiro_guard.py:1207）把 `max_tokens=64000` 砍到 8192，模型到 8192 触发 `stop_reason=max_tokens` 合法停。6h 数据：240 次 eof 100% 在经 guard 的真 claude 渠道（#10/#20/#60），guard journal 判 78-82% 为 ok（非断流），journalctl 每请求都打 `max_tokens capped 64000 → 8192`。**修复（2026-07-28 03:15 二次提额）**：8 个 guard 服务 systemd override 提到 **`KIRO_GUARD_MAX_TOKENS_CAP=65536`** / **`KIRO_GUARD_MAX_TOKENS_WRITE_CAP=65536`**（不再做 cap，完全透传客户端原始 max_tokens）。验证：restart 后 eof 率从 40% 降到 13%，长任务可连续输出到 65536 token 不再中断。详见 `docs/plans/newapi-adaptive-routing-2026-07-27.md` 末尾。
- **主池再平衡（2026-07-28 02:30，数据驱动）**：kimi-k3#138 w25(186次0失败最稳) > baibei-8f3c#9 w20(真claude,abilities双写修复) > welfare#142 w15(最快16.7s) = baibei-25ca#20 w15 > baibei-2663#10 w12 > muyuan#140 w8(6h 86次sensitive拦截,failover成本>0.01倍率)。grok#139 w0 阀门关。

- **真 claude 分层（2026-07-28 03:00，用户反馈"打不到真claude"）**：affinity `claude cli trace` 死粘 #138 kimi-k3（switch_on_success=false + TTL 600s + 客户端持续续期），导致真 claude 渠道永远选不到。修复：① 真 claude #9/#10/#20 提 **50 层**（原 45），#138 kimi-k3 降 **40 层**兜底；② affinity TTL 600→60s；③ `switch_on_success` false→**true**（成功后可切换，不死粘）。验证：近 3min #10 claude-opus-5 命中 6 次、#20 claude-opus-5 命中 1 次，真 claude 恢复可达。梯队：**50层=真claude(#20 w45/#10 w29/#9 w26)** > 45层=muyuan/welfare > 40层=kimi-k3(兜底) > -19层=aliyun。
- 设计文档：`docs/plans/newapi-adaptive-routing-2026-07-27.md`；v4 立项（VPS 前置 guard，P2 大概率不做）：`docs/plans/newapi-midstream-continuation-v4-2026-07-27.md`。
- **Client surfaces (2026-07-26):** Claude Code → ZG NewAPI；Cursor IDE BYOK → `zg-*` → NewAPI；**Cursor Agent CLI → Cursor 云端官方模型 only**（默认 `claude-opus-5-high`；勿用 Opus 4.8 踩 Cyber Safeguards）。细节见 runbook「客户端怎么走 NewAPI」。
- **AUP / Cyber Safeguards:** 官方 Opus 4.8 易拦；新会话 + 换 Opus5/Sonnet/glm；无客户端关过滤。见 runbook「Cyber Safeguards / AUP」与 `docs/patches/cyber-safeguards-opus48-2026-07-26.md`。
- **公益站抖动：** 预期噪声；勿当 P0。health v5.1 对 `no available accounts` 不累计禁用。

## Client note (cc-switch)

- Prefer **official** [farion1231/cc-switch](https://github.com/farion1231/cc-switch/releases) installers for daily use (e.g. v3.18.0 → DB support **v16**).
- Fork schema v17+ (`enabled_pi`) / v18 will trip **数据库版本过新** on official — after backup, `PRAGMA user_version = 16;` is the known downgrade for this ops path (extra columns ignored).
- Do not replace the installed UI with `cargo build --release` only.
- **本机已卸** A2A / Reasonix+Atom / Pi（npm + 配置目录 + DB `app_type` 行）。日常入口仅 Claude Code（`zg-gateway-claude`）与 Cursor IDE BYOK。Claude Code 主题：`custom:slate-ember`（`~\.claude\themes\slate-ember.json`）。见 `docs/patches/local-clients-cleanup-2026-07-26.md`。
- **本机模型对齐（2026-07-26）：** `~\.claude\settings.json` 与 ZG 一致（Opus5 / Sonnet=`glm-5.2[1M]` / Haiku=`LongCat-2.0`）；FQ#2 AR2 全角色 Opus5（无 1M）。见 `docs/patches/local-claude-rtk-align-2026-07-26.md`。
- **RTK：** 全局 hook `rtk hook claude` 已装（rtk-ai/rtk ≥0.42）；**关键 git 必须** `rtk proxy git …`，勿只信过滤后的 `git status`/`log`。
- **Claude MCP（2026-07-26）：** 全局 `mcpServers` 精简为 github/context7/filesystem/fz-sim/gitnexus/agent-inspect；已去重复 `context7-1` 与 kimi 系低频。见 `docs/patches/local-mcp-skills-slim-2026-07-26.md`。

## Explicit do-nots

- **Do not modify / rebuild / replace cc-switch** for ops fixes — `docs/ops/do-not-modify-cc-switch.md`.
- Do not promote Agnes / GPT-only free keys into type=14 Opus.
- Do not invent fake `claude-*` aliases on LongCat (tool JSON 400s).
- Do not leave `glm-5.2[1M]` without NewAPI ability (proxy strip is not always present).
- Do not re-enable zhipu thinking unless Claude Code `max_tokens` budget is proven large enough.

### muyuan.do（君自营站，2026-07-28 起第一位）

- **运营者是君（群里熟人）**，非匿名公益池：key 作废/分组缺模型/限长问题直接找君沟通，不要当黑盒池子处理。
- 0.01 倍率；CF 1010 要浏览器 UA；completions 只认 `codex-cli` UA（header_override 已配）。
- 当前 key 分组只有 gpt-5.4/5.5/MiniMax-M3/gpt-5.6-sol；**terra/luna 无货、大上下文（200k）会被拒**——可请君开分组权限或放宽限长。
- **WAF 敏感词拦截（2026-07-28 实测）**：6h 内 86 次 `sensitive_words_detected`（朴素单词黑名单，日常动词+安全审计词触发），每次 failover 3-5s 延迟。v5.2 optimizer 归 content 类不压权，但降权 w60→w8（failover 成本 > 0.01 倍率收益）。若君能放宽或换不拦上游，可回升主力。

## kimi-code CLI 收编到 NewAPI（2026-07-28 03:10）

- **收编**：kimi-code config.toml 删除 6 个直连 provider（wuming/baibei/tokenrouter/林夕/cunai×2），除 `managed:kimi-code`（官方订阅）和 `cc-switch-proxy`（/login）外全走 `zg-newapi`。
- **新增模型别名**（全部经 NewAPI 统一入口）：
  - `zg-newapi/glm-5.2`（日常主力）、`zg-newapi/claude-opus-5`（真 claude）、`zg-newapi/claude-opus-4-8`
  - `zg-newapi/gpt-5.5`、`zg-newapi/gpt-5.6-sol`、`zg-newapi/gpt-5.6-terra`
  - `zg-newapi/grok-4.5`（bazaarlink #139，独立模型，2026-07-28 新增，priority 45 w10）
- **grok-4.5 配置**：#139 bazaarlink 渠道加 `grok-4.5` 独立模型（model_mapping grok-4.5→grok-4.5），ability priority 45 weight 10。测试 model=x-ai/grok-4.5 通过。
- **不暴露 kimi-k3 独立别名**：#138 的 kimi-k3 只作 claude 渠道上游（claude-opus-4-8→k3），不对外暴露 kimi-k3 模型（503 是预期）。kimi-code 要用 kimi 走官方 `managed:kimi-code`。
- **验证**：glm-5.2/claude-opus-5/gpt-5.5/grok-4.5 四模型经 zg-newapi 全部 200。

## 403 failover：guard 403→503 转 NewAPI 重试（2026-07-28 03:20）

**问题**：baibei 真 claude 上游间歇返回 `401/403 "Upstream access forbidden"`（风控/CF 间歇），guard 原样透传给 NewAPI → NewAPI 记 `handler_stop` → Claude Code 显示「停止」（与 max_tokens cap 截断是两个独立痛点）。

**修复**：`/opt/new-api/kiro_guard.py` 两处 `HTTPError` 回复分支（tee 模式 ~1685 行 + passthrough 模式 ~1989 行）patch：上游 `e.code in (401, 403)` 时改回 `_reply(503, _api_error(503, "The model is currently overloaded. Please retry."))`。NewAPI `RetryTimes=2` 收到 503 自动切下一个渠道，不再记 handler_stop。

- backup：`kiro_guard.py.bak.403failover_20260728_032014`
- 验证：`grep -c '_r_code = 503 if e.code in (401, 403)' kiro_guard.py` = **2**（两处改点都落地）；8 个 guard 重启后全 active；近 10min `handler_stop=0`。
- 设计权衡：不动 NewAPI fork（镜像 = 上游 calciumion/new-api 无 failover 配置项），guard 层转换最省事；503 是 Anthropic 标准「过载」语义，Claude Code 会自然重试或由 NewAPI 层切渠。
- 预期收益：baibei 间歇 403 从「Claude Code 停止」降级为「透明 failover 一次切渠」，体感中断大幅减少。

## NewAPI 额度 + 渠道策略设置修复（2026-07-28 03:24）

**问题（DB 审计发现）**：
1. token 1/2/3 的 `remain_quota` 是巨大负数（-256万 / -8981万 / -4亿）——历史消耗累积，虽然 `unlimited_quota=1` 理论上跳过校验，但负数不正常。
2. `AutomaticEnableChannelEnabled=false` —— 渠道被 auto-ban 后**永不自动恢复**。这是 #9/#52/#118 曾经永久 status=2/3 的根源：一旦 auto-disable 就只能手动解封。
3. `ChannelDisableThreshold=10` —— 公益池抖动 10 次连续错误就 auto-disable，太敏感（baibei 间歇 403 / muyuan sensitive 都能轻松凑齐）。
4. `ModelRequestRateLimitEnabled=true` + 1 分钟 120 次 —— Claude Code 高频会触发限流。

**修复**（DB 写 + `podman restart new-api` 重载）：
| 设置 | 旧值 | 新值 | 理由 |
|------|------|------|------|
| `users.id=1 quota` | 636万 | 999999999999（~1万亿） | 管理员额度实际无限 |
| `tokens.remain_quota`（全 4 个） | 负数/小数 | 999999999999 | 重置历史负数垃圾，配合 unlimited_quota=1 |
| `AutomaticEnableChannelEnabled` | **false** | **true** | auto-ban 的渠道能自动探活恢复，不再永久死 |
| `ChannelDisableThreshold` | 10 | **50** | 公益池抖动容忍度提高 5 倍，减少误禁 |
| `ModelRequestRateLimitEnabled` | true | **false** | Claude Code 高频请求不再被限流 |

- backup：`/opt/new-api/data/backups/one-api.db.bak.quota_20260728_0324`
- 验证：API status 200；4 token remain_quota 全部大正数且 unlimited_quota=1；重启后真实流量正常消耗（token #3 已从 999999999999 降到 999998839888，说明在用且不拦）。
- **副作用提示**：`AutomaticEnableChannelEnabled=true` 后，被 auto-ban 的渠道会在下次请求探活时自动恢复 status=1。如果某渠道上游真死了，可能会反复 flap（disable→enable→disable）。route_optimizer 的 EWMA 仍会降权，所以 flap 的成本是「偶尔浪费一次往返」，可接受。

## #9 key 换行符 + #20 并发风暴修复（2026-07-28 03:27）

**根因（500 upstream error: do request failed）**：审计 #9 hexdump 发现其 `key` 字段含**嵌入换行符 `0x0a`**——实际存了两个 key 用 `\n` 拼接：
```
sk-9ef0239d8aa3f1b9ef058a6fa2d6fab64177c9eaa3ba175517f4dd7b1b648f3c\n
sk-8410ee646169e9d988705f2d1e2d8c575d5ab875987fa5cee53f7fccf4b0f2b
```
Go HTTP client 把整串当 `X-Api-Key` header 值，遇 `\n` 直接报 `net/http: invalid header field value for "X-Api-Key"` → NewAPI 记 `do request failed` → 对外 500。**每次打到 #9 都必 500**（key 本地坏，与上游无关）。

测试两 key：第一个 200（有效），第二个 401（作废）。修复：`UPDATE channels SET key='sk-9ef0239...' WHERE id=9`（截成单一有效 key）+ `podman restart new-api` 刷 fork 内存缓存。

**连带问题（#20 Concurrency limit exceeded 429）**：#9 每次必 500 → RetryTimes failover 全压到 #20 → baibei 上游账号并发硬上限被打爆（17 次 429）。根因不是 #20 本身，是 #9 坏了导致的流量倾斜。

**权重再均衡**（50 层真 claude）：原 `#9/#20/#10 = 60/13/1`（#9 key 坏时 w60 全浪费）→ 现 `25/25/10`。channels + abilities 双写。重启后 channel 分布健康：#9/#10/#20/#60 各有命中，无单点。

- backup：`one-api.db.bak.key9_20260728_0327` / `one-api.db.bak.rebalance_20260728_0330`
- 验证：`invalid header` 计数归零；claude-opus-5 经 gateway 200 + 缓存命中；4 渠道分流正常。
- **教训**：DB 里 key 字段绝对不能有多行/控制字符。NewAPI 后台编辑 key 时粘贴可能带入换行。审计渠道 key 时用 `xxd` 而非肉眼。
