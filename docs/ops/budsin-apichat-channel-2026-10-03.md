# budsin-apichat 渠道接入（ch148：聚合站备份位）（2026-10-03）

## 结论

用户提供 `apichat.budsin.dev` 连接信息（聚合站，617 模型）。新建 **ch148
`budsin-apichat`**（type=1），**纯备份姿态 p20/w1**，4 个本地正典模型 exact-id
接入 + `k3→kimi-k3` 映射，abilities×4 读回一致，admin 渠道自测 200
（日志 218103 归因 ch148），政策门禁零新增违规，15721 链路生产流量
22:55-22:58 持续 200。

## 关键教训：NewAPI 优先级方向

**数值大 = 先服务**。证据：opus-5 在营主渠道 ch3 p54 / ch9 p52 / ch18 p50；
文档化 fallback ch45/72 坐 p40；政策 `BACKUP_CHANNEL_POSTURES` 要求启用中的
备份渠道 priority ≤ 50；ch123 曾因 p60 空窗泄漏 500 个生产请求被降级 p0。
备份姿态 = priority **低于**各池在营最小值。

## 实证（直连上游，env 注入 key，不落盘）

- `GET /v1/models`：617 模型（urllib 默认 UA 被 403，curl 正常）。
- 6 候选直探 chat 全 200 真实补全：deepseek-v4-flash、qwen3.8-max、
  step-3.7-flash、glm-5.3、claude-opus-5、kimi-k3。

## 变更

执行脚本：`scripts/ops/add_budsin_apichat_channel.py`（key 走 `BUDSIN_KEY`
env，dry-run 默认，幂等 resume：渠道已存在且配置一致则复用，漂移则拒绝）。

| 项 | 值 |
|---|---|
| 渠道 | ch148 `budsin-apichat`（type=1，base `https://apichat.budsin.dev` 不带 /v1） |
| 模型 | `deepseek-v4-flash`、`glm-5.3`、`claude-opus-5`、`k3` |
| 映射 | `{"k3": "kimi-k3"}`（上游无裸 `k3` id） |
| 姿态 | **priority 20 / weight 1**，严格低于各池在营最小值（deepseek 30 / glm 40 / k3 50 / opus 50）→ 仅在主渠道全灭时服役；auto_ban=1，test_model=deepseek-v4-flash |
| abilities | 4 模型均 `(default,1,20,1)` 读回一致 |
| 定价 | **只读对账未改写**：deepseek/opus=0.5/2、k3=2/2 已在库；**glm-5.3 全站无 ModelRatio 条目**（6 个在营渠道同状态，走网关默认倍率，ch148 平价一致，非本次引入） |
| DB 快照 | `new-api-before-budsin-apichat-20261003-225259.db` 与 `-225422.db`（均 integrity=ok） |
| 池深变化 | deepseek-v4-flash 1→2、k3 1→2、claude-opus-5 3→4、glm-5.3 6→7 |
| OMP/models.yml | **零改动**（全部 exact-id，正典模型透明冗余） |

## 被否设计（留痕）

1. **p60/w1 "严格备份"** —— 方向搞反：p60 会把 ch148 加冕为六池主渠道，
   重演 ch123 空窗泄漏事故。被 policy 证据 + ch3/9/18 在营优先级否决。
2. **qwen3.8-max + step-3.7-flash 入列** —— 两池唯一在营渠道都在 p0
   （ch89 **w0** / ch144），没有更低的备份位；qwen 池以 p0/w1 对 ch89 w0
   会独吞全部 qwen 流量。移出 v1，待专门姿态决策。
3. **"ModelRatio 缺条目 = 错误计费"硬断言** —— glm-5.3 实证全站无条目，
   6 渠道均走默认倍率；脚本首次 apply 因此中断（渠道已建、fix 未跑），
   改为只读平价报告 + 幂等 resume 后完成。

## 验证

- admin 渠道自测：HTTP 200 success，3.528s，日志 `(218103, ch148,
  deepseek-v4-flash, "模型测试")` 实锤归因。
- 网关链健康：glm-5.3 chat 200（17/16），归因 ch140（既有主渠道）——
  ch148 未抢流量，备份姿态行为级确认。
- abilities 前后对账（备份库 vs 现库）：**仅 +1 行** (claude-opus-5,148,1,20,1)，
  零删除；1M 后缀模型集合不变。
- 政策门禁 `newapi-local-smoke.py`：ch148 相关全 OK（pool capacity
  opus-5 enabled=4 含 148）；两个 FAIL（primary opus pool posture、
  opus-4-8 capacity）与当日 11:25/15:25/19:25 计划运行**逐字节相同** =
  存量，非本次引入。
- 15721 链路：生产流量在 ch148 上线后持续 200（proxy_request_logs
  22:55:57/22:56:48/22:58:01，request_model=claude-opus-5 →
  anthropic/claude-fable-5.1，真实 tokens）。
- 探针留痕（非回归）：裸 `/v1/messages` 最小头探针 400（链路要求
  `anthropic-beta: context-1m-2025-08-07`）；`claude-opus-5[1M]` 字面量
  或 plain+beta → anyrouter 上游 503。生产流量因 cc-switch 关键词映射
  走 fable-5.1 成功——探针形状与生产不同，cc-switch 映射层按政策不触碰。
- haiku 路径 429：agnes 免费层限流（ch68/69），存量现象，与本次无关。

## 风险与备忘

1. 上游计费/配额未知 → 故纯备份位；观察 logs 中 ch148 出现频率，
   若常态服役说明主池在退化（应查主池而非提拔 ch148）。
2. 凭据经聊天明文传递（同 stepfun 前例，用户裁决不轮换）。
3. 上游聚合站本身可能再聚合免费/逆向源（模型列表含大量 `:free` 与
   `unorouter`/`crax`/`hyb` 前缀），质量与稳定性未背书——备份定位正合适。
4. glm-5.3 定价全站缺口是 open item（非本次引入）：若要精确计费需补
   ModelRatio/CompletionRatio 条目。
5. `space-bunny-free ≈ gpt-6-astra`（用户听闻，未实测）；budsin 上游
   亦有 `gpt-6-astra` exact id，可作日后 astra 池备份候选。

## 回滚

禁用 ch148（channels.status=2 + abilities.enabled=0 双表，或直接删渠道后
POST /api/channel/fix）；或还原
`backups/new-api-before-budsin-apichat-20261003-225259.db`（注意：会同时
回滚 22:54 之后的所有变更）。OMP 零改动无需回滚。
