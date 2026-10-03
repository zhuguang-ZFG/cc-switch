# Unisound（云知声）MaaS u2-flash 接入（2026-10-02）

## 结论

用户提供 Unisound MaaS key 与上线公告："U2 Flash 全新上线！限时免费无限量调用
（2026.09.30-2026.10.31）"，base `https://maas-api.unisound.com/v1`。
**ch138** `unisound-u2-flash` 已建并在册启用，网关与 OMP 端到端实弹均 200。

## 直连实证（key 走 env 注入，不写仓库/日志/文档；--apply 后作为渠道凭据
  持久化于 NewAPI DB——渠道 key 的 SSOT）

- `GET /v1/models` → 22 模型：`u2-flash` / `u2` / `u2-med` + 第三方转发档
  （glm-5.x、qwen3.x、kimi-k2.x/k3、deepseek-v4、MiniMax-Mx）。
- `u2-flash` chat 200：**reasoning 模型**，`reasoning_content` 吃 completion 预算
  （max_tokens=64 → finish=length 仅见 'p'，预算要给足）。
- 流式正常：reasoning+content delta 分块，finish=stop（17 chunks）。
- `u2` 同活（finish=stop 'pong'），**但定价未公告**——限免仅限 u2-flash，
  故只接入 u2-flash；u2/u2-med 待定价明确再说。

## 变更

执行脚本：`scripts/ops/add_unisound_u2_flash_channel.py`（key 走 `UNISOUND_KEY`
env，dry-run 默认）。

| 项 | 值 |
|---|---|
| 渠道 | ch138 `unisound-u2-flash`（type=1，base `https://maas-api.unisound.com`） |
| 姿态 | priority 0 / weight 2，auto_ban=1，test_model=u2-flash |
| ModelRatio | `u2-flash` = 0（限免窗口 09.30-10.31） |
| abilities | `(default,1,0,2)` 读回一致 |
| DB 快照 | `new-api-before-unisound-u2-flash-20261002-234924.db`（integrity=ok） |
| OMP models.yml | `u2-flash`（reasoning: true；contextWindow 131072/maxTokens 32768
  为保守标称 **[未实测]**，实弹后可调） |

## 验证

- 网关 `127.0.0.1:3002/v1/chat/completions` 200，usage 110/13，logs 归因 ch138，quota=0。
- OMP 端到端：`omp -p --model zg-newapi/u2-flash` → `U2_OK`（16.4s，logs 11552
  prompt tokens 归因 ch138，quota=0）。**教训**：`omp -p <model> <prompt>` 位置参数
  形式会把模型名当 prompt（会话实录首条 user=模型名、主模型回落 k3 跑题 195s
  进 Recovery）——OMP 探针必须显式 `--model`。

## 到期提醒 → **已处置（2026-10-04）**

~~**限免 2026-10-31 截止**。11 月前必须复核~~ → 官方模型页已挂出窗口后价卡：
**输入 ¥1/M、输出 ¥2/M、缓存命中 ¥0.2/M**（页面划线价，限时免费
2026.09.30–10.31，maas.unisound.com/models/u2-flash，2026-10-04 抓取）。
裁决：**保留 ch138**（64.6 coding-agentic 档、¥1/¥2 价格极廉），并按窗口后价
**提前落库**（即刻生效于本地簿记；10 月用量转化为成本投影，上游 10.31 前仍免费）：

| 项 | 值 | 换算 |
|---|---|---|
| ModelRatio `u2-flash` | 0 → **0.07** | ¥1/M ≈ $0.141 ≈ 0.07×$2/M |
| CompletionRatio `u2-flash` | 无 → **2** | ¥2/¥1 |
| CacheRatio `u2-flash` | 无 → **0.2** | ¥0.2/¥1 |

备份 `new-api-before-u2flash-pricing-20261004-003735.db`，admin API 读回一致。

### 11.01 人工复核程序（auto_ban 不可依赖）

- **两层暴露拆开**：上游计费打在用户真实云知声账户上（与本地 ModelRatio 无关，
  本地比值只影响成本可见性）；若 10.31 前账户无余额/Token Plan，11.01 起调用
  在上游侧失败。
- **auto_ban 不能当保险**：10-04 实证（ch89）auto_ban 对 500 类失效不及时触发；
  余额门控若呈 402/429 或许会触发，若呈 5xx 则不会。故 11.01 当天**人工复核**：
  ① admin 渠道自测 ch138 一发；② 云知声控制台查余额/用量增量；③ 若失败且
  决定不再付费 → 手工双表禁用 ch138（smoke accepted 逻辑接纳）。
- **Token Plan 选项**（用户决策）：控制台可购 Token Plan（低至 5.9 折，注册曾
  领 1 亿 token）——若接受预购，u2-flash 实际成本可低于按量 ¥1/¥2；不购则按量
  计费走真实账户。本地三种出路：购 Plan 保渠道 / 按量付费保渠道（当前姿态）/
  11.01 停用 ch138。

## 回滚

- 定价：还原 `new-api-before-u2flash-pricing-20261004-003735.db`，或 admin API 把
  `u2-flash` 三行（ModelRatio/CompletionRatio/CacheRatio）改回 0/删除。

`POST /api/channel/138/status {"status":2}` 停用；或删渠道 + `POST /api/channel/fix`
+ models.yml 移除条目（omp-agent 仓还原对应 commit）。
