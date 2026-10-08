# NewAPI DeepSeek V4 Flash 聚合（2026-10-08）

## 目标

把 NewAPI（`127.0.0.1:3002`，`C:/Users/zhugu/.new-api-local/new-api.db`）中所有可用 DeepSeek 渠道聚合为
`deepseek-v4-flash` 的可靠路由池，消除单一渠道 429 限流 / 宕机导致不可用的问题。

## 盘点结论（全库 SQL 扫描 34 个含 deepseek 引用渠道 + 37 路并发直连探针）

| 分类 | 渠道 | 说明 |
|---|---|---|
| ✅ 主 | ch180 agentrouter | `deepseek-v4-flash` 本体，健康，接受 `max_tokens=131072` |
| ✅ 备 | ch118 seeseed | `deepseek-v4-flash` 本体，复活（原 auto_ban=1 由 ReadTimeout 触发） |
| ✅ 末 | ch15 sensenova | `deepseek-v4-flash` 本体 + 1M 上下文；429 tpm/rpm 限流，作末位兜底 |
| ✅ vision 池 | ch140-146/147 intern-discovery | 仅 `deepseek-v4-flash-vision`（本体 404 `not supported by TokenPlan`） |
| ❌ 死 | ch110 yjs（403）、ch148 budsin（403 CF 1010）、ch149 nimbridge（502）、ch150-170 tokenrhythm 21 渠（402）、ch172 42x（403 CF 1010） | 维持停用 |

关键探测：intern-discovery 系（140-147）接本体 `deepseek-v4-flash` 一律 404 `not supported by TokenPlan`，
只能进 vision 池。

## 最终池配置（channels + abilities 两表）

- ch180 agentrouter：`status=1 auto_ban=0 priority=51 weight=5`（主）
- ch118 seeseed：`status=1 auto_ban=0 priority=30 weight=5`（备）
- ch15 sensenova：`status=1 auto_ban=0 priority=25 weight=1`（末位，1M 能力，429 限流故压到 ch118 之后）
- vision：ch140/141/142/143/146/147 均 `enabled=1 priority=40`（ch147 复活，第 6 渠）

## 实施方式

本版本 NewAPI `PUT /api/channel/` 与 `PUT /api/channel/{id}` 均 404，无渠道更新 API → 走 SQLite 直改先例
（先备份，后 UPDATE 两表，channel_cache 约 1 分钟自动同步，无需重启）。

- 备份：`C:/Users/zhugu/.new-api-local/backups/new-api-before-deepseek-pool-v2-20261008-151305.db`
- 首次聚合（v1）备份：`new-api-before-deepseek-pool-adjust-20261008-144253.db`、`...144351.db`

## 验证证据

- 直连探针：ch180 `deepseek-v4-flash` 200；ch118 200；ch15 200（429 风暴已过）；140-147 vision 200。
- 端到端（经 NewAPI `/v1/chat/completions`，OMP models.yml `zg-newapi` key）：
  - `deepseek-v4-flash` 并发 6 请求 → **6/6 HTTP 200**（2.85~3.94s）
  - `deepseek-v4-flash-vision` → **200**
- 日志：`C:/Users/zhugu/.new-api-local/logs/oneapi-20261008111259.log` 中 11:55:30/11:55:51（UTC）请求
  `channel_id=180` relay 200（新流量主落 ch180）。

## OMP 侧归一

`C:/Users/zhugu/.omp/agent/models.yml` L8 `zg-newapi/deepseek-v4-flash`：

- name：`DeepSeek V4 Flash (NewAPI 聚合池: ch180 主 / ch118 备 / ch15 末)`
- contextWindow：`1000000 → 262144`（对齐主渠 ch180 实测能力，避免 1M 申报导致 262K~1M 输入失败）
- maxTokens：`131072` 保留（ch180 实测接受）
- 新增注释说明聚合池构成与申报依据

## 注意事项

- 429 不会触发 NewAPI 渠道 failover（实测 429 直接透传给客户端），因此限流渠道必须压到健康渠道之后。
- ch15（1M）仅在 ch180+ch118 双失败时兜底；大上下文（>262K）请求无法靠 ch180 承载，需 OMP 侧在
  contextWindow 内紧凑（已通过 262144 申报实现）。