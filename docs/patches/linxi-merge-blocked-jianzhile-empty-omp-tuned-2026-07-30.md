# 林夕双 Key 合并受阻 + jianzhile 空账户 + omp 模型对齐（2026-07-30）

## 1. 林夕双 Key 合并：不应合并，现状最优

用户给两个林夕（k40.shengqainbang.cn）key 要求合并成单渠道双 key。实测后结论是**不要合并**。

### 1.1 第二个 key 无效

- `sk-8d182…0a5f6`（K1）：直连 `https://k40.shengqainbang.cn/v1/messages` → 200，ch9 test 2.37s ✅
- `sk-53d48…57feb`（K2）：直连同一上游 → **401 `INVALID_API_KEY`** ❌

K2 是坏的/失效 key。合并双 key 后轮询到 K2 会 401，可用性反而低于单 key。

### 1.2 API 路设多 key 走不通（即使 K2 有效也不行）

`XFPddtX/…`（New-Api-User: 1）admin 令牌实测：

- `PUT /api/channel/` 能写入 `key` 字段（换行分隔多 key），但 `channel_info.is_multi_key` **始终落不上** `true`——NewAPI Update 时从 key 重算，不识别换行多 key，GET 回来仍是 `false`。
- `multi_key_status_list` 期望 `map[int]int`（JSON object），传数组报 `cannot unmarshal array`；传 string 报 `cannot unmarshal string`。
- `PUT` body 带 `status` 字段会被拒（`Invalid parameters`），且更新 key 时 NewAPI 会把 `status` 自动改成 `2`（禁用），需 `POST /api/channel/:id/status` 单独恢复。
- `POST /api/channel`（新建）历史上触发 Go nil-pointer panic；多 key 的 `channel_info` 历史上靠 sqlite 直写 BLOB 绕过。
- SSH 三台全死：`lima`(47.112.162.80) `Permission denied`、`tencent`(100.94.119.7) `Connection closed`、`node`(100.83.32.95) `Permission denied`。sqlite 直写路也走不了。

→ 合并双 key 只能靠后台 UI（前端 JS 正确构造 `channel_info` BLOB）。但 K2 坏的，无意义。

### 1.3 最终状态（已验证，保持现状）

| Channel | key | status | test |
|---|---|---|---|
| 9 `linxi-k40` | K1（有效） | 1 启用 | 2.37s ✅ |
| 18 `linxi-k40-opus5-backup` | 原 key（未动，有效） | 1 启用 | 2.44s ✅ |

`models` = `claude-opus-4-7,4-8,5`，`base_url` = `https://k40.shengqainbang.cn`，`type`=14(Anthropic)，均无 `model_mapping`。上游 k40 当日先 502 后恢复。两个单 key 渠道各用一个有效 key，故障转移正常。**不再尝试合并。**

当初（2026-07-28）拆成两个单 key 渠道的根因（多 key 换行被当单个 `X-Api-Key` 值 → `invalid header field value` 500）仍然成立，本次复现印证。

## 2. jianzhile.vip：空账户，搁置

两个 key（`sk-lNgc…` / `sk-7fAJ…`）接入探查：

- `https://jianzhile.vip` 首页是 NewAPI React SPA（"You need to enable JavaScript to run this app"）→ **它本身是 NewAPI 实例**，非上游供应商。
- `GET /v1/models` → `{"data":[],"success":true}` 空。
- 逐模型 `POST /v1/chat/completions`：我们用的模型名（gpt-5.6-sol/gpt-5.5/grok-4.5/claude-opus-4-8/gpt-5.6-luna/deepseek-v4-flash）+ 9 个通用名（gpt-4o/claude-3-5-sonnet/deepseek-chat/glm-4/qwen-plus/gemini-1.5-pro…）**全部 503 `model_not_found`**，报 `under group GPT (distributor)`。
- `/api/user/self` `/api/models` → `Unauthorized, invalid access token`（`sk-` key 无权调管理端点）。

→ 这两个 key 在 jianzhile 上属于 `GPT (distributor)` 分销组，但该组下没绑定任何上游渠道。是 jianzhile 那边的账户配置问题，外部 key 持有者动不了。需要站方/分销商后台给该组绑渠道，或换有模型的 key/组。**搁置。**

## 3. omp（oh-my-pi）模型对齐

`omp` v17.1.8 已装（`~/.bun/bin/omp.exe`），配置在 `C:/Users/zhugu/.omp/agent/`。本次按 NewAPI `/v1/models` 实有清单修正 `models.yml`。

### 3.1 NewAPI 可用模型清单（23 个，distributor 令牌视角）

```
claude-opus-4-7, claude-opus-4-8, claude-opus-5, claude-sonnet-5,
codex-auto-review, deepseek-v4-flash, glm-5.2, gpt-5.5, gpt-5.6-luna,
gpt-5.6-sol, gpt-5.6-terra, grok-4.20-0309-non-reasoning,
grok-4.20-0309-reasoning, grok-4.20-multi-agent-0309, grok-4.3, grok-4.5,
grok-build-0.1, k3, kimi-k3, qwen3.8-max-preview, sensenova-6.7-flash-lite,
sensenova-u1-fast, welfare-codex-gpt-5.6-sol
```

关键：**`claude-sonnet-4-6` 不在清单里**（清单是 `sonnet-5`）；但 `sonnet-5` 经 distributor 令牌实测 **503 `model_not_found under group default (distributor)`**——和 jianzhile 同类问题，该令牌组下 sonnet-5 无渠道。`mimo-v2.5-pro` / `claude-haiku-4-5` / `fable-5` / `grok-composer-2.5-fast` 清单不含，不加。

### 3.2 models.yml 修正

- **删** `claude-sonnet-4-6`（NewAPI 没有，调用必 503）
- **加** `claude-opus-4-7`（200 ✅）、`gpt-5.6-luna`（200 ✅）、`gpt-5.6-terra`（200 ✅）
- 不加 `claude-sonnet-5`（distributor 令牌 503）

thinking 标记按 `~/.kimi-code/AGENTS.md`：gpt 全系 / deepseek-v4-flash / sensenova / mimo / grok-composer 不返回 `reasoning_content` → 不标 `reasoning:true`；claude / glm-5.2 / grok-4.5 / qwen3.8 / k3 标。

context/maxTokens 按 AGENTS.md 校准：gpt-5.6-* = 1050000/128000；gpt-5.5 = 272000/128000；grok-4.5 = 500000/33000；claude 全 200000（opus=128000）；glm-5.2/deepseek = 1048576；qwen3.8/k3 = 1000000。

### 3.3 冒烟（均 stop，exit 0）

```
omp -p --model gpt-5.6-luna "reply OK-LUNA"        → OK-LUNA   ✅
omp -p --model gpt-5.6-terra "reply OK-TERRA"     → OK-TERRA  ✅
omp -p --model claude-opus-4-7 --thinking high …  → OK-OPUS47 ✅
```

（先前已验证 glm-5.2 → OK-GLM 且 `cacheRead:28544` 缓存命中；claude-opus-4-8 → OK-CLAUDE。）

## 4. 关键坑（给下个 agent）

1. NewAPI admin API 用 `Authorization: Bearer <token>` + `New-Api-User: <数字user_id>`，缺 `New-Api-User` 返 401。访问令牌（如 `daLHfyldrr…`）不是 admin，`sk-` 的 user token 也不是。
2. NewAPI `PUT /api/channel` 不能在 body 带 `status`；改 status 用 `POST /api/channel/:id/status {status:N}`。更新 key 会把 status 自动改 2，需单独恢复。
3. NewAPI 这个版本多 key（`is_multi_key`）只能 sqlite 直写 BLOB，API PUT 设不上；Anthropic 类（type 14）多 key 当年拆单 key 就是为规避 `invalid header field value` 500。
4. omp `models.yml` 的 `id` 即 wire 模型名（NewAPI 裸名，无 `zg-newapi/` 前缀）；`CustomModelDefinitionLike` 无 wire 名覆盖字段。
5. distributor 令牌下 `sonnet-5` 等 model 在 `/v1/models` 列了但实际 503（组下无渠道），不能仅凭清单上模型，必须冒烟。
6. Git Bash 的 `/tmp` 与 Windows python 看到的 `/tmp` 不一致，curl 写文件再 python 读会 `FileNotFoundError`；用管道或绝对 Windows 路径。
