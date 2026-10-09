# agentrouter 渠道 NewAPI 层拦截（2026-09-11）

**Status:** 已生效。ch45/ch120 双置禁用；ch86 原本已禁用。
**Scope:** 只动 NewAPI 渠道状态；未动 OMP config、8788 桥、cc-switch、schema。

## 1. 需求

用户：agent 渠道的模型经常报错，要在 NewAPI 上直接拦截掉。

## 2. 取证（17:45–18:05）

agent 族渠道（name 含 agent）共 3 个：

| 渠道 | name | 变更前 | priority/weight | 上游 | models |
|---|---|---|---|---|---|
| 45 | agentrouter | **启用** | p40/w5 | `100.83.32.95:8788` 桥 | glm-5.3 |
| 120 | agentrouter-glm-5.3 | **启用** | p40/w5 | 同上桥 | glm-5.3 |
| 86 | agentrouter-claude | 已 status=2 | p50/w2 | `ps.air-outer.com` 直连 | claude-opus-5/4-8 + zg-* 别名 |

- NewAPI logs 表**不可**用 type=5 错误行作证据——type=5 行 08-01 后停止写库，零 type=5 是日志盲区而非无错（见 agentrouter-waf-glm53-2026-09-04 定案）；有效证据 = 消费迁移数据（近 7d glm-5.3 仅 454 探针行 + 913 真实消费行，token local-windows-clients，后者止于 09-10 01:05）+ Guardian 文件尾告警流。近 24h glm-5.3 消费全部来自 Guardian 探针 token "模型测试"（18+18 条，非流式）——**无真实消费走 NewAPI**。
- 报错的真实观察面 = Guardian 告警流（flapping 记录：09-06 14:24 ch45/120 degraded 5→2；09-06 17:04 ch86 full_scan HTML；09-06 18:17 ch45/120 502 upstream；09-06 23:43 / 09-07 13:33 ch45 402 Budget pool；09-07 08:57 自动恢复入池）+ OMP 直连路径（advisor `agentrouter/glm-5.3:high`、compaction fallback `agentrouter/deepseek`，经 8788 直连 agentrouter.org，**不经过 NewAPI，拦截不覆盖**，见 §5）。
- 当下 admin test 45/120/86 均 200（3s/3s/17s）——探针健康、真实流量历史反复 flapping（402 预算池、Clash 出口路径病、deepseek 敏感词 WAF，见 agentrouter-deepseek 敏感词 runbook），属"探针过、真实流量病"。

## 3. 变更

`POST /api/channel/{id}/status {"status":2}` 对 ch45/ch120；API 返回 success，status=2，**abilities 自动同步**：`('glm-5.3',45,0)`、`('glm-5.3',120,0)` 双置确认（ch118 同款先例）。

机制保障（guardian.py 契约核实）：

- error scan（L1511）与 full health scan（L2197）只探测 `status==1 && weight>0` 渠道 → 禁用后 Guardian 不再探针这两渠道，告警噪音止息。
- 手动 status=2 = `disabled_orphan` 生命周期：不进恢复队列（`_sync_newapi_auto_bans` 只收 status=3+auto_ban=1），**不会自动复活**。
- 恢复快照（回滚 artifact）：`C:/Users/zhugu/.new-api-local/bak-agent-ch-disable-20260911.json`（变更前 channel 对象 ×2 + abilities 行 ×2，key 已脱敏）。

## 4. 验证（18:02–18:05）

| 探针 | 结果 | 结论 |
|---|---|---|
| 3002 `/v1/chat/completions` glm-5.3（relay 实弹） | **503** `No available channel for model glm-5.3 under group default` | 拦截生效，池内无任何 enabled 渠道可服务该模型 |
| 3002 `/v1/chat/completions` claude-haiku-4-5-20251001 | **200 / 0.4s**（ch68/69 agnes 替身） | 无误伤，无关路由完好 |

## 5. 边界与遗留

- OMP 侧 agentrouter 直连消费（advisor=glm-5.3:high、compaction fallback=deepseek）走 8788 桥直达上游，不经过 NewAPI——本拦截不影响它们；其报错治理仍走 09-10 敏感词 runbook §4 的待示下建议（deepseek 长会话固定 ch15 主路）。
- glm-5.3 在 NewAPI 池内自此无 enabled 渠道（whyyin ch108 仍 status=2）；任何经 3002 请求 glm-5.3 得 503 属预期。
- 恢复程序（上游预算池/路径恢复后手工执行）：`POST /api/channel/45/status {"status":1}` + ch120 同款 → `POST /api/channel/fix` 校验 abilities 双置 1（ch118 复活先例）；Guardian 恢复后自动接管健康治理。
