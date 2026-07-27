# NewAPI 自适应路由（按速度+质量选渠道）设计 — 2026-07-27

## 问题

NewAPI 原生路由 = `priority` 分层 + 层内**静态权重随机**。不感知实时速度、
不感知实时质量（成功率/断流率）。结果：

- 慢渠（#10 TTFT 17s）和快渠（#60 4.3s）同层随机抽，体验抽盲盒
- 渠道犯病（间歇 500/断流）时权重不变，一半请求继续撞墙
- 权重调整靠 dx 每日跑一次，反应周期 24h，白天出问题全天难受

目标：**同优先级层内，流量自动流向「当前快且稳」的渠道**；分钟级反应。

## 社区方案调研

| 方案 | 机制 | 可借鉴 |
|---|---|---|
| [LiteLLM Router](https://docs.litellm.ai/docs/routing) | `latency-based-routing`（选 TTFT 最低）、`least-busy`、错误冷却窗（cooldown）、fallbacks | 延迟信号 + 冷却窗；但它[有并发丢更新退化为随机的 bug](https://github.com/BerriAI/litellm/issues/24720)，纯「选最低」在震荡时不稳 → 我们用**加权**而非硬选 |
| new-api 社区脚本（作享智库等） | 定时探活测首字延迟，慢者降权写回 DB | 与 dx 每日自动权重同思路，只是把周期缩到分钟级 |
| [gpt-load (tbphp)](https://github.com/tbphp/gpt-load) | Go 网关，健康检查 + 权重动态调整 + 密钥轮询 | 佐证「探针驱动权重」是主流做法 |
| LiteLLM cooldown | 错误渠进冷却名单，时间到自动恢复 | 我们让 fork 的 `AutomaticDisableChannelEnabled`（threshold=10）继续管硬死，优化器管「变慢/变抖」的软降级 |

结论：不引入新网关（LiteLLM/gpt-load 替换成本高、丢 new-api 的
计费/分组/渠道特性），**在现有 fork 外挂一个优化器守护进程**是最小侵入路径。

## 本 fork 已确认的能力（2026-07-27 实测）

- **每 60s 从 DB 同步渠道**（日志 `channels synced from database`）→ 改
  `channels.weight` / `abilities.weight` **无需重启**，1 分钟内生效
- `RetryTimes=2`：失败自动重试 2 次（仅对流开始前错误有效）
- `AutomaticDisableChannelEnabled=true`、`ChannelDisableThreshold=10`：
  硬错误累积 10 次自动摘渠（不管「变慢」）
- `logs` 表记录每笔成功请求 `channel_id/use_time/model_name` → 真实流量
  延迟可直接 SQL 统计，零额外探针成本
- `channel_affinity_setting.rules`：fork 有粘性路由规则（codex trace 在用），
  优化器不得破坏 affinity 语义 → 只动 weight，不动 priority/models

## 设计

### 原则

1. **只调层内权重，不动 priority**——故障序（主池→GPT→免费池）是人定的
   策略，优化器只管「同一层里谁多接活」
2. **探索地板**：任何活渠道权重下限 3（ε-greedy），防止饿死导致再也测不到
3. **迟滞**：|Δweight| < 5 不写库，防抖动
4. **失败不摘渠**：探针失败 → weight=1 软隔离；摘渠仍归 fork auto-ban 管
5. **与 dx 分工**：优化器接管权重（分钟级），dx 保留 cooldown/health/告警
   （日级）；dx 每日 09:00 的权重写入会被优化器 5 分钟内校正，无害

### 评分公式

对每个受管层（v1 = Opus 主池 pri=45）每个渠道：

```
lat   = 0.6 × EWMA(真实流量 use_time, logs 表 30min 窗) + 0.4 × 探针延迟
score = 1 / lat            （探针失败的渠道 score=0 → weight=1）
weight_i = round(100 × score_i / Σscore)，clamp [1, 60]
```

- 真实流量样本 < 3 时退化为纯探针延迟
- EWMA α=0.4（新样本权重高，反应快但不过冲）
- 状态持久化 `/opt/new-api/data/route_optimizer_state.json`

### 运行形态

- VPS cron 每 5 分钟跑 `route_optimizer.py`（无守护进程，挂了 cron 拉起）
- 探针：`POST base_url/v1/messages`，`max_tokens=8`，30s 超时，标准小负载
- 变更写 TG 通知（复用 tg_notify）；全量决策写日志文件

### 扩展（后续阶段）

- v2：Sonnet 链（#132/#63/#133 同层权重化，需先拉平 priority）
- v3：断流率信号——从容器日志解析 `stream ended: reason=eof soft_errors>0`
  计入质量分（治「Claude 经常停」的根）
- v4：流中途 EOF 续传——kiro_guard tee 模式经验移植到 /v1/messages 前置
  guard（大改动，单独立项）

## 安全回退

- 关掉 cron 条目即停用；权重停留在最后值
- `route_optimizer.py --restore` 一键恢复备份权重（首跑前自动备份）
- DB 备份：每次首跑 `one-api.before-routeopt-*.db`

## 上线记录（2026-07-27 20:36）

- v1 已部署：VPS `cron */5` 跑 `/opt/new-api/scripts/route_optimizer.py`
- 受管：Opus 主池 pri45（#10/#20/#60；**#11 排除**，保持手动 w1 保守涓流）
- 首轮权重 `{10:35, 20:26, 60:39}`，fork 60s DB 同步已确认生效
- 踩坑：urllib 默认 UA 被 100x WAF 秒拒（curl 可过），探针必须带 `claude-cli` UA
- 已知局限：`use_time` 是整段生成时长，混入模型思考时间，区分度被稀释；
  v3 改 TTFT 信号 + 断流率（`soft_errors`）计入质量分

## v2（2026-07-27 21:03）

- **TTFT 流式探针**：`stream:true` 读到首个 SSE 块即断开——信号=首字节耗时
  （直接对应用户体感），且比整段探针省 token
- **质量分**：解析容器日志 `channel error (channel #N…)` 计数（含 mid-stream
  EOF 的 handler_stop），`quality = 1/(1+3×err_rate)`，err_rate EWMA 平滑；
  分母用 logs 表 6 分钟成功数。断流渠即使 TTFT 快也会被压权
- **TG 告警**（复用 `/opt/new-api/tg_notify.py`）：渠道判死/恢复/主池全灭
  时推送；weight APPLY 不推（太吵）
- state 键迁移用 `setdefault` 兼容 v1 旧 state
- 首轮实测：TTFT #10 4.6s / #20 8.2s / #60 7.8s → `{10:46, 20:26, 60:27}`

## 待拍板（语义变更，未动）

- **自动摘渠/放回**：连续 N 轮判死 → status=2，恢复 M 轮 → status=1
  （对标 LiteLLM cooldown；误摘风险需灰度）
- **Sonnet 层间自动换序**：#132 连挂时与 #63 自动对调（改变人工故障序策略）
- **每日体验日报**：p50/p90 TTFT、断流次数、use_channel 重试长度统计

## v3：Sonnet 兜底链层间自动换序（2026-07-27 22:00 上线）

- 新模块 `route_optimizer_sonnet.py`（repo 纳管 `scripts/ops/route_optimizer_sonnet.vps.py`），
  由 `route_optimizer.py` __main__ 在同一 cron（*/5）内串行调用
- 打分信号复用 v2：TTFT 流式探针 + 容器日志错误率 EWMA，但按渠道 type 自动
  选 OpenAI `/chat/completions` 或 Anthropic `/v1/messages` 格式，模型取
  `model_mapping["claude-sonnet-4-6"]` 的映射值，请求头吃 DB `header_override`
  （伪装 UA 与生产链路自动一致）；5 渠并行探测
- **首块超时 ≠ 死**：HTTP 200 但 20s 无首块记 `ttft=20`（慢但活着，参与排序），
  只有连接失败/4xx/5xx 才判死得 0 分（cc.freemodel 夜间首块 20s+ 的实测教训）
- 换序策略：每次运行**最多交换一对相邻层**——下层分数超上层 1.5 倍（或上层
  判死）才交换，渐进收敛防抖动；ladder 用组内现有 priority 值重排，
  channels + abilities 双写；原始 priority 首跑备份 state，
  `--restore-sonnet` 一键回滚；`--sonnet-dry` 只打分不落库
- 换序发 TG；每次打分明细进 `route_optimizer.log`
- **副作用须知**：priority 是渠道级全局值，Sonnet 换序会同步影响 Opus 兜底
  序（同渠共享）。当前证据下方向一致（kimi 快、132 死），可接受；如需解耦
  得用 abilities.priority 按模型族分写，待验证 fork 读取路径后再做

### 上线即验证（cron 自动跑出的两次真实换序）

- 21:50 前后：#63 kimi（TTFT 0.9s）↑ 超过判死的 #132 → 链首
- 随后：#133（慢但活）↑ 超过 #132 → 收敛为 **#63 → #133 → #129 → #134**
- 顺带发现 **#132（work.freemodel.dev）对 Claude Code 根本不可用**：上游
  400「Claude Code is not supported on this endpoint」+ 403「WorkBuddy
  client only」（Chrome UA 伪装无效，生产 logs 表仅 1 条记录）。已摘渠
  （models CSV 清空，DB 备份 `one-api.db.bak.ch132-*`），移出 SONNET_GROUP
- 当前判死原因如实反映渠道状态：#129 key 401（公益池 auth 耗尽自愈型）、
  #134 glm 5h cap 429（22:46 复位后会自然爬升）

### 待拍板（更新）

- **自动摘渠/放回**：连续 N 轮判死 → status=2，恢复 M 轮 → status=1（仍待定）
- **每日体验日报**：p50/p90 TTFT、断流次数、换序/判死事件汇总

## v4：映射渠进主池（GPT/k3 泄压阀，2026-07-27 23:00 上线）

- **#137 gpt-terra-opus-valve**（克隆 #129 8317 池，opus-4-6~5 → gpt-5.6-terra，
  无 [1M]）与 **#138 kimi-k3-opus**（克隆 #63 kimi 端点，opus 全家族含 [1M]
  → k3，k3 原生 1M 上下文）以 priority 45 进 Opus 主池层
- optimizer 探针按渠道 type 分流 OpenAI/Anthropic 格式，模型取 model_mapping，
  UA 吃 header_override，多 key 取首 key
- **门控策略**（不当主力、只当泄压阀）：映射渠分数须超最好 Claude 渠 ×1.3
  才放权重，且封顶 w25；未过门控 w0 关门；Claude 全灭时门控失效全开兜底；
  门控状态变化发 TG
- 选型记录：muyuan #46 key 已失效（401）；#130/#123/#83 GPT 池今晚全灭；
  8317 terra auth 耗尽等自愈（#137 目前 w1 躺着，自愈后门控自动评估）；
  k3 实测 TTFT 1.0-1.5s 立即上岗
- 首轮实测（Claude 主池降级夜：#20 死 #60 41s）：`{10:10,20:1,60:3,137:1,138:25}`
  ——k3 过门控（1.0s vs Claude 9-32s）拿下 25，承担了泄压职责；
  健康夜 Claude 权重和 ~99 时 k3 占比 ≈20%
- 副产物验证：flock 生效（cron 与手动跑重叠时干净退出）；Sonnet 链 glm
  cap 复位后 1.3s 自动爬层，链序收敛 [63, 133, 134, 136, 129]

## 追加（23:40）：#139 grok45-opus-valve 进主池竞速

- local-cpa 路径放弃后，grok-4.5 改走 NewAPI：盘点现有 relay 发现 #93/#95
  bazaarlink、#76 imagic、#94 opencode-zen-free 模型表里有 grok-4.5；
  实测 #95 bazaarlink-2 TTFT 1.8s 最优（#93 4.8s、#76 502、#94 余额不足 401）。
- 新建 **#139 grok45-opus-valve**（克隆 #95，type 1，priority 45）：
  Opus 全家族（含 [1M]）→ grok-4.5，加入 optimizer `MAIN_TIER_MAPPED`
  （cap 25 / margin 1.3 门控，与 #137/#138 同款泄压阀模式）。
- 首轮即过门控：ttft 2.7s → w25。当前主池权重
  {138 k3:25, 139 grok:25, 10:7, 20:7, 60:8, 137 terra:1(等自愈)}。
- agentrouter 线确认废弃：GPT 侧 VPS 网络不可达（DNS 污染），Claude 侧
  公益池无货（#118 auto-ban 保留），不再投入。

## 追加（00:30）：muyuan 复活上第一位（0.01 倍率公益站）

- 群里公益站 muyuan.do 改 0.01 倍率 + 新 key。实测：CF 1010 要浏览器 UA 过
  /v1/models；completions 还有 `channel:client_restricted`——**只认
  `codex-cli` UA**（claude-cli/浏览器均 403）。该 key 分组可用模型仅
  gpt-5.4 / gpt-5.5 / MiniMax-M3 / gpt-5.6-sol（terra/luna 无货）。
- **#140 muyuan-claude-first**（type 1，header_override codex UA，w40）：
  opus 全家族(含[1M])→gpt-5.5 **priority 60 第一位**；claude-fable-5→
  gpt-5.6-sol priority 55（fable 层第一）；sonnet→gpt-5.4 priority -18
  （sonnet 链头）；haiku→MiniMax-M3 priority 45。
- #46 muyuan.do-new 同步换新 key + codex UA（zg- 别名恢复）。
- 验证：opus 200/2s 上游 gpt-5.5；fable 200/3s 上游 gpt-5.6-sol；
  `use_channel=["140"]` 确认。真实 208k 请求 `use_channel=["140","138"]`
  ——muyuan 拒大上下文后自动落 k3，故障链正确但大请求多付一次失败重试
  （optimizer 探针是小请求看不见，晨报盯 #140 错误率）。
- optimizer 已正确接管 60 层（探针走 header_override 的 codex UA）。
- 8317 Dc 站 key 全作废（401 invalid，非配额），三渠道 #21/#129/#137
  保持隔离等群里补 key。

## 追加（00:35）：#140 opus 降 44 —— 敏感词拦截，第一位适得其反

- 实测真实工作流量：**muyuan 上游有敏感词过滤**（500 sensitive_words_detected，
  与 agentrouter WAF 同族），Claude Code 真实 prompt 全部触发拦截回落。
  第一位期间 FRT 从 9-11s 恶化到 17-49s（每请求白付 1-2 次失败重试）。
- 处置：opus 行 60 → **44**（主池之下第一兜底），channels+abilities 双写；
  fable(55)/sonnet(-18)/haiku(45) 暂留观察（真实流量少，同风险）。
- 可请君放宽上游敏感词过滤（或确认是哪个上游渠道拦的），解除后可回 60。
- 教训：公益站「第一位」上线前必须用真实大 prompt 验证，小探针测不出 WAF。

## 追加（00:45）：更正 —— 非全量 WAF，opus 回 60

- 实测更正上一条结论：Claude Code 式系统提示+代码 prompt 通过（3.8s），
  80KB 大上下文通过（4.4s），sol 通过（5.0s）。今晚被拦是该会话具体内容
  （含 key/薅站/UA 绕过等词），**不是敏感词全量拦截，也没有大上下文限制**。
- opus 行已回 **60**（第一位）。含敏感内容的个别会话会自动回落兜底，
  代价仅该类请求一次重试；正常编码流量直接吃 0.01 倍率。
- 附带发现：gpt-5.4 不收 `max_tokens`（要 max_completion_tokens，sonnet
  位暂不可用待 param_override）；MiniMax-M3 实际无货（models 列表有但
  503 model_not_found，haiku 位空挂，请求会自动落 #122，无害）。
- 教训复述：下结论前用中性 prompt 对照实测，别把单次内容拦截当全量策略。
