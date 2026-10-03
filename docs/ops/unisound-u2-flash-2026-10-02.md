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
裁决：**保留 ch138**（64.6 coding-agentic 档、¥1/¥2 价格极廉、auto_ban=1
fail-closed），并按窗口后价**提前落库**（即刻生效于本地簿记；10 月用量
转化为成本投影，上游 10.31 前仍免费）：

| 项 | 值 | 换算 |
|---|---|---|
| ModelRatio `u2-flash` | 0 → **0.07** | ¥1/M ≈ $0.141 ≈ 0.07×$2/M |
| CompletionRatio `u2-flash` | 无 → **2** | ¥2/¥1 |
| CacheRatio `u2-flash` | 无 → **0.2** | ¥0.2/¥1 |

备份 `new-api-before-u2flash-pricing-20261004-003735.db`，admin API 读回一致。
回滚=还原快照或 admin API 改回。若 11.01 上游余额门控触发 auto_ban 禁用，
确认是计费切换（届时账户需有余额/Token Plan）而非故障。

## 回滚

`POST /api/channel/138/status {"status":2}` 停用；或删渠道 + `POST /api/channel/fix`
+ models.yml 移除条目（omp-agent 仓还原对应 commit）。
