# Sonnet 链改道：vyceai 硬挂毒化分类器（2026-07-27）

## 现象

Claude Code auto 模式反复报 `claude-sonnet-4-6[1M] is temporarily unavailable`
（分类器失败，所有 Bash 操作被拦）。

## 根因

- Sonnet 链第一棒 `#125 vyceai-claude`（ch_pri 35）硬挂：连接吊死 60s+ /
  Cloudflare 524 / 502 交替，单请求实测卡 **6m31s**。
- 兜底链串行：第一棒「挂起型」失败吃光分类器短超时（十几秒），`#63` kimi
  来不及接手，客户端先报错。兜底防的是快速失败（401/403/429），防不住挂起。
- `auto_ban` 只摘鉴权错误（401/403），超时/524 不触发，死渠赖在链上。

## 改动

| 项 | 前 | 后 |
|----|----|----|
| `#125` models | sonnet+haiku | **仅 haiku**（sonnet 物理剥离） |
| `#133` priority | 15 | **-21**（免费池 39s+ 慢响应/SSL 间歇 reset，不宜挡在 #63 前） |
| Sonnet 链 | `#125`→`#63` | **`#63`kimi(-20)→`#133`(-21)→`#132`GPT(-32)** |

## 关键坑（复发预防）

**此 fork 按 `channels.models` 路由**：`abilities.enabled=0` 不足以摘渠，
`use_channel` 仍会出现该渠道。必须从 `channels.models` CSV 移除 model 并
`podman restart new-api`。

验证：`claude-sonnet-4-6[1M]` 200 / 4.2s / `use_channel=["63"]`；
`claude-opus-5` 200 / 4.6s / 主池不受影响。

## 回退

vyceai 恢复后，把 sonnet models 加回 `#125.models`（ch_pri 35>-20，自动回主渠）：

```sql
UPDATE channels SET models='claude-sonnet-4-6,claude-sonnet-5,claude-haiku-4-5,claude-haiku-4-5-20251001,claude-haiku-4-5[1M],claude-sonnet-4-6[1m],claude-sonnet-4-6[1M]' WHERE id=125;
UPDATE abilities SET enabled=1 WHERE channel_id=125 AND model LIKE 'claude-sonnet%';
-- podman restart new-api
```

DB 备份：`/opt/new-api/data/backups/one-api.before-vyce-sonnet-off-*.db`。

## 追加（20:11）：Opus 主池 #10 不稳 → 降权

- 现象：客户端「总是停止」——`claude-opus-4-8` 请求经 #10 时流中途 EOF
  （`soft_errors=1, received=3`）+ 间歇 `500 Upstream access forbidden`；
  NewAPI 重试由 #20/#60 补完，但 thinking 已流出的请求无法续，客户端只能「继续」。
- 处置：权重 `#10` 50→**20**、`#60` k40 18→**30**（#20 保持 40）。
  新分布 #10 22% / #20 44% / #60 33%。dx 自动权重次日会再调。
- 注意：#9 status=2 未动；#10 若持续 forbidden 可再降或 status=2。

## 追加（21:10）：GLM 分类器故障 — 权重翻转

- 现象：另一客户端 auto 分类器（`glm-5.2[1M]`）报 temporarily unavailable。
- 根因：`#41` zhipu-glm-key2（w50，层内 86% 流量）触发智谱「5 小时使用上限」
  429（22:46:47 复位），请求跳 2 Retry 还要撞 `#123` 腾讯 TPM 429，短超时
  分类器等不到活渠 `#42`。
- 处置：`#41` w50→**5**、`#42` w8→**50**（channels+abilities，glm% 范围）。
  无需重启——fork 60s DB 同步生效。验证 200/3.2s/`use_channel=["42"]`。
- 复位后：#41 22:46 恢复，权重维持 #42 主（#41 有 5h cap 前科）；如需还原
  `#41=50/#42=8` 一行 SQL。
- 教训：**同层权重悬殊 + 主渠配额死 = 分类器故障**；optimizer 扩展 GLM 层
  （需 OpenAI 格式探针）列为 v2.1。

## 追加（21:15）：国模进 Claude 路由 — kimi 进 Opus 链 + #134 GLM 总兜底

- **#63 kimi 加 `claude-opus-5`/`[1M]` 能力**（mapping→kimi-for-coding）：
  强制路由实测 200/4.2s/`use_channel=["63"]`。Opus 链：
  主池→`#132`GPT(-19)→**`#63`kimi(-20)**→`#133`→GPT -30 系。
- **新增 `#134 glm-claude-backstop`**（克隆 #41 智谱 key2，**pri=-33 最末位**，
  Opus/Sonnet→`glm-5.2`）：机械链路正确，强制测试时 429 是 key2 的 5h cap
  （22:46:47 复位），非配置问题；失败后链路自动 134→60 / 134→132→63 走完，
  恰好实战验证了多层兜底。选 key2 是为与 GLM 主载 key1(#42) 配额隔离。
- GPT 池此前已在 Claude 路由内（#132/#129/#130），本次补齐的是国模层。

## 追加（23:02）：#134 cap 复位验证 — backstop 可用

- 22:46:47 智谱 key2 的 5h cap 复位后做一次性验证：#134 priority 临时 100，
  等 fork 同步后打 `claude-opus-5`（max_tokens 16）。
- 结果：**HTTP 200**，1s 完成，podman 日志确认 `use_channel=["134"]`，
  映射上游 `glm-5.2`，`stop_reason=max_tokens` 正常。
- 回退：priority 已还原为 **-22**（注意不是最初文档的 -33——sonnet 换序器
  当晚已把 #134 从 -33 调至 -22，回退以实际快照值为准）。
- 结论：#134 GLM 总兜底功能正常，此前 429 确为 cap 期现象。链序当前
  `[63, 133, 136, 129, 134]`，#129 terra 探针持续 FAIL（公益池 auth 耗尽）
  分数归零自然沉底，#136 minimax 自动爬过 #129。
