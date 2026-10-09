# ss2a.top 渠道接入并升主档（ch183）（2026-10-08）

## 结论

用户提供 `https://ss2a.top/v1` + key（走 `SS2A_KEY` env，不落盘不回显），
描述为 **glm-5.3 max 官号**（官方 max 档账号）。接入后按用户明确指令
（“这个要为主档”）**升格为 glm-5.3 主档**：
新建 **ch183 `ss2a`**（type=1，**p50/w5**，auto_ban=1，test_model=`glm-5.3`，
base `https://ss2a.top` 不带 /v1），单一 exact 模型 id `glm-5.3`。

姿态=**primary**：glm-5.3 流量由 ch183 承接（实弹 3/3 归因 ch183），
intern-discovery p40 档（ch140/141/142/143/146，5 路同池）保留启用，
作为自动故障转移（hard error → Guardian/auto_ban 隔离 → p40 顶位）。

## 上游探测证据（只读，key 不落盘）

- `GET /v1/models` → HTTP 200，**恰好 1 个 id：`glm-5.3`**（多一个
  `display_name` 字段，无害）。
- 直连实弹（chat/completions，max_tokens=800）：
  `content="SS2A_OK"`，finish=stop，`reasoning_content` 存在（55 reasoning
  tokens）；SSE 200，**129 data 帧 + [DONE] + usage**；`reasoning_effort:
  "max"` 接受（200）。
- **别名不存在**：`glm-5.3-max` / `glm-5.3:max` 均 404
  `Model "glm-5.3-max" is not supported by any configured account in this
  group` → 只挂裸 id，`:max` 是 OMP 侧 effort 语义（models.yml efforts
  已声明），不建 NewAPI 别名。
- **UA 矩阵**：curl / Go-http-client/1.1 / 空 UA / python-urllib 全 200
  → NewAPI Go 客户端无需 header_override（与 hubway 的 CF 签名封禁不同）。
- 并发：2 路并发 chat 200/3.1s+4.4s。
- 无 `/v1/dashboard/billing/subscription`、无 `/api/status`（404）→ 无余额
  接口，用量/配额不可观测（单 key，属接受风险）。
- 身份指纹：响应带 `request_id` + `2026...` 形态 completion id →
  NewAPI 风格 relay 前端（用户口径：Zhipu max 官号；relay 层身份未独立
  确证，仅按 OpenAI 兼容面自述记录）。

## conformance canary：预算工件，非缺

`probe_untrusted_openai_provider.py --run`（6 请求）报
`empty-semantic-output` x2 + `tool-arguments-invalid` x1，但 canary 载荷
**硬编码 max_tokens=32**，而 glm-5.3 是推理模型（重放实测 reasoning_tokens
53/26，吃掉全部 32 token 预算）→ 属工具预算工件。以 max_tokens=800 重放：
- 语义探测：`content=="CANARY_SEM_OK"` 精确匹配 ✓
- 强制 tool 探测：report_canary 参数 JSON 解码
  `{"value":"CANARY_TOOL_OK"}` 完全匹配 ✓
- 缓存探测：`repeat_cache_observed=true`、`second_cache_read_tokens=640`、
  `suspicious_first_request_cache_hit=false` ✓

## 变更

执行脚本：`scripts/ops/add_ss2a_channel.py`
（key 走 `SS 2A_KEY` env，dry-run 默认，幂等 resume，纯增量 `--extend`，
contested 断言自适应主档存活态）。

| 项 | 值 |
|---|---|
| 渠道 | **ch183 `ss2a`**（type=1，base `https://ss2a.top` 不带 /v1） |
| 模型 | glm-5.3（exact-id，无 mapping） |
| 姿态 | **p50/w5，主档**，auto_ban=1；fallback=intern-discovery ch140/141/142/143/146（p40） |
| abilities | 1 行读回 `(default,1,50,5)` |
| 定价 | 只读对账未改写：ModelRatio=0.7 / CompletionRatio=3.14 已有条目 |
| DB 快照 | `new-api-before-ss2a-20261008-232954.db`（36851712B，integrity=ok）；`new-api-before-ss2a-20261008-233022.db`（36855808B，integrity=ok，resume 复跑）；`new-api-before-ss2a-promote-20261008-233550.db`（integrity=ok，升档前） |
| OMP | models.yml glm-5.3 条目名称改为 `GLM 5.3 (ss2a ch183 主档; ch140-143/146 p40 备档)`；角色/efforts 未动 |

## 关键机制实证

1. **同 id 多档共存按优先级路由**：升档后网关实弹 `glm-5.3` 3 次，
   attribution 全部 **ch183**（内容 `PRIME_OK`）——p50 主档实际承接流量；
   再次运行 `add_ss2a_channel.py --apply`（幂等 resume）亦复证 attr=183。
2. **故障转移档 = p40 同池**（ch140/141/142/143/146 同一 intern-discovery
   池，仅取第一个实测 200 代表整档）：primary hard error → auto_ban 隔离 →
   p40 顶位；**未做禁用式故障演练**（advisor 生产关键，5 路同池全下线的
   窗口不可承受），转移仅由机制推断，属显式未验证项。
3. 启用态 ≠ 可用态：admin 自测 ch183 200（接入 3.5s/复跑 1.4s/升档复跑
   2.638s），自动判定本档本地可用。

## 验证

- 接入/复跑两次全 pass（exit 0）：read-back models=1、abilities 读回、
  admin 自测 200、网关 attribution 保持主档；升档 PUT 后仍回读 ch183。
- 探测（轮廓见上）均 200；直连非流 + SSE 内容/usage 正常。
- `newapi-local-smoke.py` policy gate：终跑结论见当日日志（零新增 FAIL）。

## 后续（未做，需授权）

- 故障演练（临时整体 disable p40 档，验证 attr=183 承接；或反向演练：
  disable ch183 → p40 顶位）——建议低峰期手动执行，`gateway_chat_retry`
  已可复用。
- 上限量/余额观测：该 relay 无 billing 端点，只能靠通道软失败计数
  （auto_ban + Guardian 作隔离网）。
- 若上游放出多模型，可 `--extend` 增列；当前仅收录已实弹验证的 id。

## 可靠性注记（2026-10-09 追记）

- 00:06:57 直连上游出现过一次 503（`Service temporarily unavailable`，
  ~1 分钟内 3/3 恢复）；NewAPI RetryTimes=1 + 500-503 自动重试覆盖，
  **未产生任何客户端可见失败**（logs 无 glm-5.3 type=1 行）。
- 同晚用户报"glm-5.3 总是报错"经查为 k3 无可用渠道的误归因
  （glm-5.3 同窗 13+ 次全成功，含 60–98K prompt）——详见
  `docs/ops/k3-outage-misattributed-glm53-2026-10-09.md`。

## 回滚

- 降回备份档：PUT ch183 priority=-20/weight=1（恢复接入时姿态）。
- 禁用：`POST /api/channel/183/status` status=2（仅改状态，不清配置）；
  恢复=status=1。
- 删除：`DELETE /api/channel/183`（备份 `new-api-before-ss2a-*.db` 在
  `~/.new-api-local/backups/`，integrity=ok）。