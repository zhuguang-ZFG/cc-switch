# grok-heavy.878.indevs.in 渠道接入（ch174）（2026-10-06）

## 结论

用户提供 `grok-heavy.878.indevs.in` 连接信息（域名暗示 grok-heavy，实际暴露
OpenAI 系 luna 家族）。接入完成：新建 **ch174 `grok-heavy-indevs`**
（type=1，**p-10**/w1，auto_ban=1，test_model=`gpt-6-luna`），3 个 exact-id
模型（gpt-6-luna / gpt-5.6-luna / gpt-5.6-terra）。接入时发现
**ch132/ch137（opencode-go Go 套餐）已 429 耗尽**（`GoUsageLimitError`，
guardian 错误扫描关键词不匹配不会自动禁用），经用户授权一并 `status=2`
禁用，ch174 现时为 luna 系唯一在营渠道。OMP models.yml 新增
`gpt-5.6-terra` / 回加 `gpt-5.6-luna` 条目（api=openai-responses，照抄
gpt-6-luna 结构）。OMP 门禁 40/40 OK，政策门禁零新增违规（3 个存量 FAIL
与 ch174 无关），三模型网关实弹全 200 归因 ch174。

## 上游探测证据（只读，key 不落盘）

- `GET /v1/models` → HTTP 200，0.87s，3 模型（gpt-6-luna / gpt-5.6-luna /
  gpt-5.6-terra）。
- 三模型直连 chat 实弹全 200（首轮 11-14s，返回 `resp_*` id +
  `service_tier:"default"`）。
- 带 `reasoning_effort:"high"` 参数 → 200（2.7s），参数被接受（是否真
  生效未验证，usage 无 reasoning_tokens 字段）。
- **身份指纹**：responses 端点响应注入 `"You are Codex, a coding agent based
  on GPT-5"` persona → 上游为套壳代理（非纯模型透传），真实后端未明。

## 变更

执行脚本：`scripts/ops/add_grok_heavy_channel.py`
（key 走 `GROK_HEAVY_KEY` env，dry-run 默认，幂等 resume；首次执行
 contested 断言拦截姿态错误后修正重跑）。

| 项 | 值 |
|---|---|
| 渠道 | ch174 `grok-heavy-indevs`（type=1，base `https://grok-heavy.878.indevs.in` 不带 /v1） |
| 模型 | gpt-6-luna、gpt-5.6-luna、gpt-5.6-terra（exact-id，无映射） |
| 姿态 | **p-10**/w1，auto_ban=1（备份必须低于主渠道 p0——NewAPI 高 priority 值先走，实证见下） |
| 配套 | ch132 `opencode-go-gpt-6-luna`、ch137 `opencode-go-gpt-5.6-luna` 经授权 `status=2`（套餐 429 耗尽） |
| abilities | 3 模型全部读回 `(default,1,-10,1)`；ch132/137 abilities 随禁 `enabled=0` |
| 定价 | 只读对账未改写：gpt-6-luna ModelRatio=0、gpt-5.6-luna 0/2、gpt-5.6-terra 0.5/2（既有条目） |
| DB 快照 | `new-api-before-grok-heavy-indevs-20261006-230018.db`（integrity=ok） |
| OMP | models.yml 新增 gpt-5.6-terra + gpt-5.6-luna（备份 `models.yml.bak-20261006-grok-heavy`）；config.yml 链位零改动 |

## 关键机制实证（本轮新确认）

1. **NewAPI priority 语义：值大者先走**。实证两次：ch174 误配 p20 时赢
   ch132 p0（网关归因 ch174）；muyuan 先例 ch15 p50 主 > ch173 p20 备。
   备份渠道必须配**负 priority** 才能保持备份位。
2. **网关 429 不做跨渠道 failover**：ch174 健康在池，gpt-6-luna 网关请求
   仍直返 ch132 的 429（RetryTimes=1 未改变结果；429 不落消费日志）。
   → 死主渠道 + 活备份的组合会整模型卡死，必须人工处置死主。
3. **guardian 错误扫描关键词不匹配 `GoUsageLimitError`**（列表：
   余额不足/INSUFFICIENT_BALANCE/credit balance/quota/402/401/invalid）
   → 套餐耗尽型 429 不会触发自动禁用，需人工或扩关键词。

## 验证

- admin 渠道自测：HTTP 200 success，7.7s，`gpt-6-luna`。
- 网关 chat 实弹（ch132/137 禁用后）：gpt-6-luna 200、gpt-5.6-luna 200、
  gpt-5.6-terra 200，全部归因 ch174。
- responses 端点（OMP 实际路径）：`POST /v1/responses` gpt-6-luna → 200，
  3.58s，status=completed。
- OMP 门禁 `test_omp_routes.py`：40/40 OK（新增条目热加载生效，
  `omp models` 已列 gpt-5.6-luna / gpt-5.6-terra，efforts low..max，
  tools=yes）。
- 政策门禁 `newapi-local-smoke.py`（23:13）：FAILURES={channel model
  isolation, fallback channel posture, primary opus pool posture}——三存量
  FAIL（分别首现 10-04 15:25 / 10-05 19:25 / 08-25），ch174 出现 0 次。

## 被否设计（留痕）

1. **p20 备份姿态**（muyuan 模板默认值）——本池主渠道在 p0，p20 反而成主。
   contested 断言拦截后改 p-10。教训：priority 相对值必须按**目标池现存
   主渠道的绝对值**定，不能照抄上一个渠道的模板值。
2. **"429 时备份会自动接管，放着不管"**——否，实测网关 429 不 failover。
3. **OMP 条目用 openai-completions 默认 api**——否，luna 家族统一
   openai-responses（与 gpt-6-luna 既有条目一致，newapi 转换路径已对
   ch132 长期工作）；responses 端点已对 ch174 实测 200。
4. **terra/5.6-luna 进 advisor 链**——否，sole-carrier 套壳渠道进链会
   给 advisor 增加单点故障位；用户消费走显式 `--model` 即可，链位维持
   glm-5.3:max → k3:max → gpt-6-luna:max → Atria-Dawn-Preview 不动。
5. **回加 gpt-5.6-luna 时保留旧名注释 "opencode-go ch137"**——否，ch137
   已禁用；name 如实标注 grok-heavy ch174 主 + ch137 禁用态。

## 风险与备忘

0. **瞬时影响留痕**：ch174 首建时误配 p20（~22:58-23:00 数分钟），期间
   gpt-6-luna 生产流量实际由 ch174（未明套壳上游）服务；23:00 删号重建
   p-10 后恢复设计姿态。另：脚本在 ch132/137 禁用后若再执行 `--apply`
   会在 contested 断言处失败（attr 现为 ch174）——属世界状态已变的预期
   结果，非脚本缺陷；渠道维护请直接用 admin status 端点，勿重跑脚本。
1. **上游身份未明**：域名 grok-heavy、暴露 luna id、注入 Codex persona——
   套壳聚合。质量/行为与 opencode-go 正主可能有别；同 id 池化后 OMP 无感
   知（ch132 恢复后同池双源质量漂移）。
2. **ch132/ch137 恢复路径**：opencode-go 套餐重置后，guardian 恢复验证
   （test_channel 3 过 2）会自动启用并拉回 weight——luna 系自动回到
   ch132 主 + ch174 备。若 GoUsageLimitError 持续，guardian 不会自动
   禁用（关键词不匹配，见机制实证 #3），需人工观察。
3. **慢上游**：首轮 11-14s，带 effort 参数 2.7s；advisor 级调用需关注
   超时预算。网关 32s 见过一次 `gateway_concurrency_limit`（本地并发
   槽满，与渠道无关，重试即过）。
4. **凭据经聊天明文传递**（同前例，用户裁决不轮换）。
5. 若上游再给新模型定价/上架，编辑脚本 MODELS 并 `--apply --extend`
   （纯增量幂等）；定价（ModelRatio）需按官方价另补，防 37.5 默认倍率
   陷阱。
6. `gpt-5.6-luna` 今天（10-06）上午刚从 OMP 下架（opencode-go 上游改名
   所致），今晚经新上游复活——历史条目可从
   `models.yml.bak-20261006-luna-dereg` 对照。

## 回滚

- 禁用 ch174：`POST /api/channel/174/status {"status":2}` + abilities 随禁。
- 恢复 ch132/137：`POST /api/channel/132/status {"status":1}` 与
  `/api/channel/137/status {"status":1}`（abilities 随启；注意两渠道上游
  仍 429，恢复后该模型立即回到卡死态，仅在套餐重置后有意义）。
- 还原 DB：`backups/new-api-before-grok-heavy-indevs-20261006-230018.db`
  （会同时回滚 23:00 之后的所有变更）。
- OMP：回拷 `models.yml.bak-20261006-grok-heavy`（删除两个新增条目）。
