# AgentRouter Claude 四 key 均衡池 + 账户级预算耗尽（2026-10-02）

## 结论

用户问「agentrouter 有 4 把 key，ccs 里 Claude 只用了一把（sk-a56rl0…4Wc8）吗」——**是**：
NewAPI ch86（agentrouter-claude）单 key，cc-switch「AgentRouter」供应商（failover 腿）同一把。
已将 Claude 侧扩为 4 单 key 渠道池；但扩池过程中预算**账户级**打穿，4 把 key 全部模型
（Claude + GPT）同时 402 `Budget pool quota has been exhausted`——**上游账户预算池耗尽，
非本地故障**，解决 = agentrouter 控制台加预算/换预算池/等重置。本地全部保持自愈姿态。

## 证据链

- ch86 agentrouter-claude（type=14，ps.air-outer.com）：1 把 key；ch127 agentrouter-codex-gpt：
  同账户 4 把 key 早已多 key 轮询（GPT 模型）。
- 4 把 key 直连 `/v1/messages` 初探均 200（22:57）→ 23:00 起 k2/k4 402 → 23:01 k1/k3 也 402
  （Claude 双模型 + ch127 GPT 全 402）→ **预算池为账户级共享**（按 key/按模型的假设均被否）。
- 用户侧 cc-switch 当前供应商会话中已从 AgentRouter 切到 **AnyRouter Opus 5.5**（直连），
  Claude Code 主路径不受 agentrouter 402 影响；AgentRouter(k1) 仅剩 failover 腿身份。

## 已否假设（勿复踩）

1. **给现有渠道 PUT 多行 key 可行** → 否：fork 仅在创建时（`mode:"multi_to_single"`）计算
   `channel_info.is_multi_key`；PUT 被服务端强制保留原 channel_info（runbook 早有记录），
   多行 key + `is_multi_key:false` → relay 直接 `do_request_failed`（time 0，请求未出站）。
   且 ch127 的多 key 是 type=1，type=14 多 key 在本 fork 未验证。
2. **预算按 key 独立** → 否：4 key 数分钟内全模型同时 402，只能是账户级共享池。

## 变更（可回滚）

统一克隆 ch86 字段（type=14 / base_url / 5 模型 + zg 映射 / claude-cli UA header_override /
prio 50 / auto_ban 1）：

| 渠道 | key | 状态 |
|---|---|---|
| ch86 agentrouter-claude | k1 (…4Wc8) | enabled w2 |
| ch134 agentrouter-claude-k2 | k2 (…Ki5j) | enabled w2 |
| ch135 agentrouter-claude-k3 | k3 (…uKLV) | enabled w2 |
| ch136 agentrouter-claude-k4 | k4 (…YWND) | enabled w2 |

- abilities 5 模型 × 4 渠道全部 `(default,1,50,2)`；`POST /api/channel/fix` 已跑。
- 全部 enabled 是刻意的：402 诚实回传 + NewAPI 在同 prio 池内 retry（ch3 baibei prio54、
  ch9 linxi prio52 是更高优先 Claude 承载，OMP 侧 Claude 不受 agentrouter 影响）；
  预算重置后 4 渠道自动恢复分流，无需人工干预。ch134/136 曾短暂禁用（误以为按 key 耗
  尽），证据翻转后复启。
- 快照：`~/.new-api-local/backups/new-api-before-ch86-keypool-20261002-225644.db`、
  `new-api-before-ch86-pool-split-20261002-225947.db`（均 integrity=ok）。
- ch127（GPT 多 key 轮询）未动；同账户预算恢复后自愈。

## 回滚

`DELETE /api/channel/{134,135,136}` + ch86 weight 回 2（本就 2）→ 即还原单 key 池；
或 DB 恢复快照 + `/api/channel/fix`。

## 遗留

- **账户预算重置/加额是用户动作**（agentrouter 控制台）。重置后验证：
  `GET /api/channel/test/{86,134,135,136}?model=claude-opus-5` 应全绿。
- cc-switch「AgentRouter」供应商（failover 腿）仍单挂 k1：账户级池下换 key 无意义；
  预算恢复后如需分散限流，可在 cc-switch UI 把 token 换成 k2–k4 任一把（或保持现状）。
