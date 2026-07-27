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
