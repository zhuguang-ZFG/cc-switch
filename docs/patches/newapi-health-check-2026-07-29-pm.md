# NewAPI 渠道健康巡检 + luna/codex 分组澄清 + 4router 拒接（2026-07-29 晚）

**Date:** 2026-07-29 ~22:10 CST
**Status:** 诊断 only — VPS / NewAPI / Kimi 配置**零改动**（用户巡检需求，非变更单）

## 1. 渠道健康快照（近 2–6h 日志）

按报错量排序,最近的实际问题:

| 优先级 | 现象 | 渠道 | 判读 |
|---|---|---|---|
| P0 | `504 origin did not respond`（Cloudflare 源站超时） | gpt-5.6-sol 全线:ch2=186、ch24=125、ch16=90、ch25=90（6h） | **上游 origin 过载/宕**,4 个渠道无一幸免,failover 无处可切。非思维链/非本机配置问题,只能等上游或降权停用 |
| P1 | `500 Concurrency limit exceeded for account` | claude-opus-4-8/opus-5,主要 ch3(48 次/24h) | 账号级并发上限,ch3 已被健康检查自动停用 |
| P2 | `502 engine not available` / `429 quota` | deepseek-v4-flash ch15 | 偶发,量小 |
| P3 | `503 system cpu overloaded (92–94%, threshold 90%)` | NewAPI 自带过载保护 | **瞬时尖峰**,当前 load 0.16(2 核/1.8G,new-api CPU 0.5%),非常态。高并发会复发 |

**自动停用渠道(status=2):**
- ch20 `fengwind-grok` — `response_time=56928ms`(57s!),即 AGENTS.md 记的"grok 在 Kimi CLI 下持续超时"的实测源
- ch9 `linxi-k40` / ch18 `linxi-k40-opus5-backup` — claude,延迟高被停
- ch3 `baibei-100xlabs` — 并发超限被停

## 2. gpt-5.6-luna「under group codex」503 — 已澄清,无需处理

24h 内 105 次 `No available channel for model gpt-5.6-luna under group codex`,一度疑似配置缺口。实查结论:**误配已自愈,不需要建 codex 分组。**

证据:
- token `cc-switch`(id=3)分组 = `default`,status=1。Kimi Code CLI 走此 token,请求全落 `default`
- `default` 分组内 gpt-5.6-luna/sol/terra 均在（abilities:luna 挂 ch2/16/21/25,enabled=1）→ Kimi CLI 现在调 luna 能正常路由
- `codex` 分组在系统内**不存在**:tokens 全 `default`/空、user admin 空(=default)、14 渠道全 `default`、`GroupRatio={"default":1,"claude-max":1}`
- 105 次报错全挤在 `2026-07-29 01:54` 一分钟内,之后 20h 无复发 → 一次性误配(token/请求临时指向不存在的 codex 分组),早已改回 default

**结论:** Kimi Code CLI 已在 default 分组正常运行。除非要做流量隔离(独立限速/模型白名单/计费倍率),否则建 codex 分组只是复制 default,无收益。若日后要隔离,正解 = admin API 改渠道 group + 重建 abilities + 刷内存缓存(`MEMORY_CACHE_ENABLED=true`,须 `podman restart new-api`),再把 token 切到 codex,**禁止裸改 DB**(abilities 是 channels 派生的物化表,直改会 desync)。

## 3. 4router.net 拒接

- 上游:`https://4router.net/v1`(用户提供 `sk-suSn…9TCa`,key 已明文出现于会话,建议持有方轮换）
- `GET /v1/models` → 200,但**只声明 1 个模型 `gpt-5.5`**
- `POST /v1/chat/completions` 连打 4 次全 **500** `分组 Temp 下模型 gpt-5.5 的所有可用渠道均请求失败`(`get_channel_failed`）→ 其自身聚合层后端渠道全挂,非抖动
- **未接入 NewAPI**:单模型 + 100% 失败的上游进池只会污染 failover

## 4. 缓存命中 / 渠道亲和 结论（澄清此前武断表述）

- **渠道亲和提升缓存命中,仅在同一模型有 ≥2 个渠道时成立**:亲和把 session 钉在同一渠道,使该渠道上游前缀缓存保持热态。单渠道时亲和对路由无意义(本来就全去那一个账号)
- 活证据:ch15 deepseek `channel_affinity{key_path:prompt_cache_key, rule_name:"deepseek trace"}`,`cache_ratio≈0.25`,cache_tokens ~94k/95k(~98% 命中)
- **单个 Kimi 渠道走 NewAPI 不增加命中**;唯一杠杆是"NewAPI 补一个 CLI 没发的 prompt_cache_key"(未验证 Kimi CLI 直连是否已发)
- **计费维度**:`managed:kimi-code` 是包月订阅,缓存命中省的是延迟/限流余量,**不省账单 token**;真省钱须上游按 token 计费

## Related

- 状态基线:`docs/ops/newapi-vps-minimal-state-2026-07-28.md`
- 前序诊断:`docs/patches/zzzcoding-codex-investigation-2026-07-29.md`（上一 key 调查）
- 环境备忘:VPS `47.112.162.80` root(密码 `D:/Downloads/VPS.txt`);NewAPI=podman `new-api`,DB 宿主机 `/opt/new-api/data/one-api.db`,用宿主机 `/usr/bin/sqlite3`(不支持 `-json`,用 `-separator`)
