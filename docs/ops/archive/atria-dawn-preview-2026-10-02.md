# Atria Dawn Preview 接入（2026-10-02）

## 结论

用户提供 Atria key 与端点 `https://api.atria-asi.ai/v1/chat/completions` +
"1亿token" 额度说明。**ch139** `atria-dawn-preview` 已建并在册启用，
网关实弹 200 归因正确。

## 直连实证（key 走 env 注入，不写仓库/日志/文档；--apply 后作为渠道凭据
  持久化于 NewAPI DB——渠道 key 的 SSOT）

- `GET /v1/models` → 仅 1 模型：`Atria-Dawn-Preview`。
- 非流式 chat 200（27.8s），finish=stop，**reasoning 模型**
  （ping 即 reasoning_tokens=14/17）。
- 流式正常：14 chunks，reasoning+content delta，finish=stop。
- **TTFT 慢**：ping 级请求也要 ~20-28s（reasoning + 慢首包）。OMP
  `streamFirstEventTimeoutSeconds=120`、NewAPI `relay_timeout=900` 均够用，
  但交互体感偏慢，适合后台/批量角色而非实时对话。

## 变更

执行脚本：`scripts/ops/add_atria_dawn_channel.py`（key 走 `ATRIA_KEY` env，
dry-run 默认）。

| 项 | 值 |
|---|---|
| 渠道 | ch139 `atria-dawn-preview`（type=1，base `https://api.atria-asi.ai`） |
| 姿态 | priority 0 / weight 2，auto_ban=1，test_model=Atria-Dawn-Preview |
| ModelRatio | `Atria-Dawn-Preview` = 0（1 亿 token 额度，边际成本 0 直到耗尽） |
| abilities | `(default,1,0,2)` 读回一致 |
| DB 快照 | `new-api-before-atria-dawn-20261002-235532.db`（integrity=ok） |
| OMP models.yml | `Atria-Dawn-Preview`（reasoning: true；contextWindow 131072/
  maxTokens 32768 保守标称 **[未实测]**） |

## 验证

- 网关 `127.0.0.1:3002/v1/chat/completions` 200，usage 17/15，logs 归因 ch139，quota=0。
- OMP 端到端：`omp -p --model zg-newapi/Atria-Dawn-Preview` → `ATRIA_OK`（20.2s，
  logs 17994 prompt tokens 归因 ch139，quota=0）。

## 额度耗尽预案

**1 亿 token 是硬上限，非无限免费**；无公告到期日，耗尽行为未实测
（预计 402/403 → auto_ban 自动禁用 = 正确 fail-closed）。
累计消耗（本地 logs 侧口径，仅经本网关流量）：
`SELECT SUM(prompt_tokens+completion_tokens) FROM logs WHERE model_name='Atria-Dawn-Preview' AND type=2`
若 ch139 被自动禁用，先跑上式对照 1 亿量级，确认额度耗尽再判故障。

## 回滚

`POST /api/channel/139/status {"status":2}` 停用；或删渠道 +
`POST /api/channel/fix` + models.yml 移除条目。
