# WorkBuddy 内置模型聚合进本地 NewAPI + OMP（2026-10-10）

## 背景

用户要求把 WorkBuddy（codebuddy 桌面端 5.7.6）账号内置的 `hy3` / `hy4` /
`deepseek-v4.1-flash` 代理进本地 NewAPI 聚合，并接入 OMP。`hy4` 经查证是账号
目录里的真实 id（`hy4-preview-f`），不是笔误。最终落地三个对外名：
`hy3-wb`、`hy4-wb`、`deepseek-v4.1-flash-wb`。

## 一、凭据捕获与常驻转换器

- 定向系统代理（`127.0.0.1:18899`，仅 terminate WB 域名）捕获
  `GET https://www.workbuddy.ai/api/memory/profile` 的 `Authorization: Bearer`。
- 两次独立捕获的长度与指纹完全一致（`len=1315`）→ **桌面端不轮换 access token**。
- bearer 只进常驻转换器进程环境（pid 5304，监听 `127.0.0.1:18801`），不落盘、
  不进仓库、不进日志；转换器单实例 + 有界退避，不产生重启风暴。
- `/health`：`{"status":"ok","mode":"direct-proxy (native function calling)",
  "logged_in":true,"token_expired":false,"custom_models":["gpt-5.6-sol"]}`。

## 二、上游契约（直连实测，非文档推断）

| 规则 | 证据 |
|---|---|
| `messages[0]` 必须是 `system` | 否则 code 11128 |
| 必须 `stream=true` | 非流式确定性 400 code 11101 `Non-stream chat request is currently not supported`（直连复现 2 次） |
| 未知模型 | code 11102 |
| hy4 是 reasoning-only | `max_tokens=64` → `finish=length`、reasoning=64、content 空；`≥1024` → 200 "OK" |

账号目录 `~/.workbuddy/cache/acc-product-config-v3.json`（54 模型，2026-10-07）：

- credits：`hy3` x0.00、`hy4-preview-f` x0.00（免费 Hy4 preview 档）、
  `hy4-preview` x0.29（同模型付费档）、`deepseek-v4.1-flash` x0.11。
- `hy4-preview-f`：in 960k / out 64k，上下文档 300k/600k/960k，effort 仅 `high`，
  `onlyReasoning:true`、`canDisableThinking:false`。
- `deepseek-v4.1-flash`：in 1M / out 128k，effort `low/high/max`。

`_catalog_models()` 按 truthy `credits` 过滤目录，x0.00 的免费档因此不出现在
`/v1/models`；渠道模型名直接取目录 id，不靠列表枚举。

## 三、NewAPI 侧：ch204 `workbuddy-local-bridge`

| 项 | 值 | 理由 |
|---|---|---|
| 对外名 | 全部加 `-wb` 后缀 | plain `hy3` 属 ch111、plain `deepseek-v4.1-flash` 是 ch15+ch203 的活池，短名会静默把 WB 拉进既有路由 |
| type / base_url | 1 / `http://127.0.0.1:18801` | NewAPI 追加 `/v1/chat/completions` |
| key | 固定非秘密占位符 | 转换器忽略上游 key |
| group / priority / weight | default / **0** / 5 | p0 且名字只存在于 ch204，任何池都不可能与它争抢 |
| auto_ban | **0** | bearer 只在 RAM 里，认证失败必须稳定报错而不是排队禁用 |
| ModelRatio / CompletionRatio | 三个新名钉 **0** | WB 套餐 credits 才是真成本，本地再计一次就是对回环桥双重计费 |
| model_mapping | `-wb` 名 → 目录 id | 直映射 |

备份：`new-api-before-workbuddy-20261010-211213.db`（45617152 B，integrity ok）。
快照序列还包含更早一次失败尝试的 `...-210958.db`。

## 四、这个 fork 的渠道写契约（本次踩实）

- **channel PUT 不可用**：本 fork 的 PUT 会把 GET 回来的 `key:""` 原样落库、
  清空真实 key（`adjust_deepseek_v4_flash_pool.py` 已记录同一结论）。扩模型只能
  走直接 SQL：`UPDATE channels SET models, model_mapping` + 逐名
  `INSERT INTO abilities(... enabled=0)`，再等 ~75s 渠道缓存。首次 cutover 就是
  因为按惯例发了 PUT，报 `HTTP 200 {'message': 'record not found'}` 而失败。
- **状态切换只能 `POST /api/channel/{id}/status`**；PUT 带 `status` 返回
  `Invalid parameters`（`add_103_39_64_76_relay_channel.py` 的 set_status 契约）。
- **`abilities` 真实列名** = `group, model, channel_id, enabled, priority, weight,
  tag`——不是 `model_name` / `model_id`；且 `group` 在 SQL 里必须加引号。
  `logs."group"` 同理。
- **管理探测会真实计费**：`/api/channel/test/{id}` 按当时的 ratio 表结算。脚本原先
  在 pin 之前探测，三行探测 quota 37163 / 38700 / 37200，其中归因到 ch204 的两行
  合计 **75900**——即每次 `--apply` 白结约 7.6 万配额。已改为 pin 先行（见脚本注释）。
- 环境缺 `sqlite3` CLI，DB 走
  `C:/Users/zhugu/scoop/apps/python313/current/python.exe` + `file:…?mode=ro`。

## 五、验证（未观测到真实 completion 之前不接渠道）

1. 直连 WB 上游：非流式 400/11101、流式 200 — 确认契约。
2. 经转换器 18801，三模型 stream + 非 stream 全部 200，非流式带完整 usage
   （hy3 22/14、hy4 71/60 reasoning 57、deepseek-v4.1-flash 15/1）。
3. 建渠道（disabled）→ 三模型管理探测 ok → pin ratio → 等缓存 →
   `POST status` 启用 → 等 abilities 路由 → abilities 3 行 enabled。
4. 经网关 3002 用网关 token 端到端 relay：三个 `-wb` 名非流式 HTTP 200 且
   `finish=stop` + usage，流式 HTTP 200 且有增量内容。
5. logs 归因：8 行落 ch204，零输出行 0；6 条 relay 行 quota 0（pin 生效）。
6. `verify_newapi_routing_weights.py` → **verdict CLEAN**：全池无 weight_drift /
   ghost_route / disabled_route / orphan_ability / duplicate_ability；
   ch204 = status 1、w5、p0，三条 abilities 与其 channel 定义完全一致。

## 六、OMP 接入

`~/.omp/agent/models.yml` → `providers.zg-newapi`（`baseUrl
http://127.0.0.1:3002/v1`，`api openai-completions`，`authHeader true`）新增三个
id，注册数 **84**。逐模型参数取自 WB 目录：

| id | contextWindow | maxTokens | efforts |
|---|---|---|---|
| `hy3-wb` | 192000 | 64000 | low, high |
| `hy4-wb` | 300000 | 64000 | high |
| `deepseek-v4.1-flash-wb` | 300000 | 128000 | low, high, max |

统一 `compactionModel: zg-newapi/deepseek-v4-flash`、`input: [text]`
（图像能力未经转换器证实，不声明）。

- **该文件是 CRLF**，且含 YAML 锚点（`&id002`）与注释 → 只能做文本级插入并配
  parse 校验，**禁止 `yaml.dump` 整写**。本会话第一次就把它整文件重写成 LF
  （29750 → 29599 B），已从 `models.yml.bak-20261010-211419-wb` 还原；工具现在
  强制保持主导换行，并做字节级证明：删掉插入块必须逐字节还原原文。
  终态 `30750 B / CRLF 1151 / bare LF 0`，净增恰为 1000 B。
- 备份 `models.yml.bak-20261010-211601-wb`（sha256 校验），失败即自动回滚。
- `verify_omp_newapi_alignment.py`：三个 `-wb` id 均有 enabled 路由，不在孤儿表内。
- models.yml **仅启动时读取** → 需重启 OMP 才会看到新模型。

## 七、遗留与风险

1. **WB 客户端 3002 报障（已定位，属我这套捕获流程的缺陷）**：拿到 bearer 后立刻
   恢复系统代理，而 WB 进程内仍缓存代理 → `ECONNREFUSED 127.0.0.1:18899`
   （Error Code 3002）。即时处置 = 完全退出（含托盘）再重开 WB。待修：恢复应
   linger 15–20 s。
2. `deepseek-v4.1-flash-wb` 走 WB 套餐 **x0.11 credits**，是真实消耗
   （hy3 / hy4-preview-f 均 x0.00）；是否长期常开需用户裁决。
3. bearer 过期后需重新捕获。ch204 `auto_ban=0` 保证不会被自动摘除，故障形态是
   稳定 4xx 而非静默降级——但也没有自愈，需要人工重捕。
4. **存量问题（本次未引入、也未改动）**：4 个 OMP 注册 id 无 enabled 路由——
   `qwen3.7-max-normal`(ch89)、`grok-chat-fast`(ch39/89)、
   `deepseek-v4-flash-0731`(ch196)、`claude-opus-4-8`(20 条禁用行，含
   ch72/86/134/135/136 重复对)；30 个 dead_names；可路由名中 43 项缺
   ModelRatio、63 项缺 CompletionRatio（含 `deepseek-v4.1-flash`）。
   GroupRatio 不含新名，默认 1，对 quota 0 的名无影响。

## 回滚

```bash
# 撤渠道 + unpin ratio（转换器可离线，teardown 不需要 bearer）
python scripts/ops/add_workbuddy_channel.py --teardown --apply
# OMP 注册
cp ~/.omp/agent/models.yml.bak-20261010-211601-wb ~/.omp/agent/models.yml   # 然后重启 OMP
# DB 快照（仅在需要整体回退时）
# new-api-before-workbuddy-20261010-211213.db
```

转换器本身的下线开关是 `steer.py`（`show|enable|restore`），手动控制系统代理，
不做自动接管。

## 涉及文件

- `scripts/ops/add_workbuddy_channel.py` — 建/扩/探测/计费钉零/回滚
- `scripts/ops/add_omp_workbuddy_models.py` — OMP 注册（`--check` / `--apply`）
- `scripts/ops/verify_newapi_routing_weights.py` — 全池权重/路由审计
- `scripts/ops/verify_omp_newapi_alignment.py` — OMP↔NewAPI 跨层孤儿审计
