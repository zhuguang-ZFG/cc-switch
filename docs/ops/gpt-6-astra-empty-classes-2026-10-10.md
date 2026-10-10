# gpt-6-astra 两类空回：口径补齐 + 计费空回取证（2026-10-10 晚）

由 21:26/21:31 两条「未计费空回超标」告警（ch127 gpt-6-astra 6h 44.8% 65/145）
触发。全程只读：不改路由、不改权重、不改任何在线配置；DB 一律用「快照 → 查询 →
立即删除副本」的方式读（副本含全部渠道 key，用完即删，不打印 key）。

## 一、两类空回是不同缺陷，不是一个

同一渠道同一模型里混着两种形状，`logs.other` 能干净切开：

| | A 未计费空回 | B 零输出但已计费 |
|---|---|---|
| 行数（6h 窗口内，ch127） | 65 | 11（10 发挤在 20:23:01–20:23:17 的 16 秒里，第 11 发 20:33:38） |
| `request_path` | `/v1/responses` 65/65 | `/v1/chat/completions` 11/11 |
| `stream_status` | `ok/eof` 64、`scanner_error/unexpected EOF` 1 | `ok/eof` 10、`client_gone/context canceled` 1 |
| `frt`（首包） | 1451–6048 ms，**首包到了** | **全部 -1000**，一个 token 都没出 |
| `usage_billing_path` | `upstream` | `local` + `local_count_tokens` 在场 |
| prompt / quota | 0 / **0**（上游没回 usage，无法计费） | 199,019–200,370 / **82,175,027** 合计（≈$164，按 500000 quota/$） |
| 调用方 | token `codex-agent`、group `agent` | token `master`、group `default` |
| `use_channel` 跳数 | 65/65 单跳 | 9 单跳、2 双跳 |
| `channel_affinity` | 65/65 True | 11/11 True |

结论：A 是「流开起来了、首包之后上游悄悄收尾且不带 usage」，NewAPI 无从计费，
用户看到空回合；B 是「首包从未到达、上游以 EOF 干净收尾」，fork 走
**local_count_tokens 兜底把 200k 输入全额计费**，输出 0，用户同样看到空回合。
B 比 A 更坏：同样是失败，B 还烧钱。

B 里 prompt 逐段 +43 token（199019 → 199062 → 199105 → 199148，每段重复 3–4 次），
是**调用侧在原地重试同一会话**，不是 NewAPI 的重试——9/11 行 `use_channel` 只有
一跳。每发单独 7.46M quota。

## 二、监控口径补齐（本仓库改动）

先前两条口径都看不见 B：

- 未计费空回要求 `quota = 0`，B 的 quota > 0；
- opus 空响应率的谓词其实是对的（`completion_tokens <= 2 AND prompt_tokens >= 1000`），
  但被 `OPUS_EMPTY_RESPONSE_MODELS` 这个硬编码模型名单限死，gpt-6-astra 不在名单里。

**盲区是名单，不是谓词**——所以没有新开第三条并行监控，而是在
`_query_unbilled_empty_rounds` 的同一趟 (channel_id, model_name) 分组扫描里加两个聚合：

```sql
SUM(CASE WHEN completion_tokens = 0 AND quota > 0 THEN 1 ELSE 0 END)                  AS billed_empty,
COALESCE(SUM(CASE WHEN completion_tokens = 0 AND quota > 0 THEN quota ELSE 0 END), 0) AS billed_quota,
GROUP_CONCAT(DISTINCT CASE WHEN <A 谓词> THEN token_name END)                         AS unbilled_tokens,
GROUP_CONCAT(DISTINCT CASE WHEN <A 谓词> THEN "group"  END)                          AS unbilled_groups,
GROUP_CONCAT(DISTINCT CASE WHEN <B 谓词> THEN token_name END)                         AS billed_tokens,
GROUP_CONCAT(DISTINCT CASE WHEN <B 谓词> THEN "group"  END)                          AS billed_groups
```

配套改动：

- 新常量 `BILLED_EMPTY_MIN_ROWS = 3`、`BILLED_EMPTY_MIN_QUOTA = 20_000_000`（≈$40）。
- 调用方标签按**形态**分别聚合（`unbilled_tokens/groups`、`billed_tokens/groups`），
  不是整组一份：同一 (channel, model) 里 A 与 B 的调用方往往不同，合并上报会把排查
  指向错误调用方——第一版就是这么写的，22:03 用真实数据渲染告警时才暴露（见上面验证块）。
  B 的可行动信息是「谁在重试」，不是「哪条腿」。
- **样本门槛从 `HAVING total >= ?` 移到 Python**：B 必须能在小样本下暴露，而 A 仍按
  30 样本 + 20% 比率。改完逐条核对既有 5 个未计费用例的期望值不变。
- B 按「行数 + 烧掉的 quota」双门槛判，**不按比率**：该组比率只有 7.6%，按比率永不响。
- `_step_unbilled_empty_rounds` 拆成两个独立冷却键：`unbilled_empty:{ch}:{model}`
  与 `billed_empty:{ch}:{model}`，同组两种形态各自告警，互相不压制。
- A 的告警文案补两句 affinity 事实：`channel_affinity_setting` 按 `prompt_cache_key`
  把会话钉在原渠道，**降权不移动这部分流量**，摘腿前要先确认该 group 还有别的
  enabled 腿（原文案建议「降低该腿权重」是无效动作）。

验证：

- `python3 -m unittest scripts.ops.test_guardian` → **218/218 OK**（新增 4 例：
  小样本按 quota 告警、双门槛各自不足则不告、同组两个键各响一次、只有 B 时不误报 A；
  夹具 `logs` 表补了 `token_name` / `"group"` 两列，真实库本就有）。
- 打补丁后的监控在**在线库的一次性快照**上整只跑过（quick_check ok，窗口 949→1021 行，
  22:03 那次是完整 `_step_unbilled_empty_rounds`，Telegram 用 Mock 捕获不外发）：
  窗口内**全 fleet 只有 ch127 gpt-6-astra 一组**过门槛，发出两条告警——

  ```
  ----- [warning] 未计费空回超标 -----
  渠道 ch127（）模型 gpt-6-astra 最近 6 小时空回率 44.8%（65/145），高于阈值 20%
  …
    >> 调用方：codex-agent（group agent）

  ----- [warning] 零输出但已计费 -----
  渠道 ch127（）模型 gpt-6-astra 最近 6 小时内有 11 轮 completion_tokens=0 却照扣 quota，
  合计 82175027（≈$164.35）。
  …
    >> 调用方：master（group default）
  ```

  即：与 21:26 那条告警逐位对齐的 A，加上先前完全隐形的 B，并且调用方标签与手工取证
  一致（A=codex-agent/agent，B=master/default）。ch86 同时段的 `claude-opus-4-8`
  12 行同形状但只烧 52,188 quota（≈$0.10），被 quota 门槛正确挡掉——新口径不刷群。
- ⚠️ 顺带发现一个既有小缺陷：这两条告警里 `渠道 ch127（）` 的括号是空的——
  ch127 的日志行 `channel_name` 为空串，未计费空回告警从上线起就没带上过渠道名。

## 三、重试为什么没覆盖（定性 + 证据）

在线选项读到的事实：`AutomaticRetryOnEmptyResponseEnabled=true`、`RetryTimes=1`、
`AutomaticRetryStatusCodes=408,500-503`、`AutomaticDisableChannelEnabled=false`、
ch127 `auto_ban=0`。

日志侧证据说明两条都搭不上手：

1. `stream_status` 是 `ok/eof`——relay 认为流是**正常结束**的，没有状态码可匹配，
   `AutomaticRetryStatusCodes` 无用武之地；
2. `use_channel` 9/11 单跳、`frt=-1000`、quota 照记——空回重试没有触发；
   且这类行**已经产生计费记录**，与「响应体为空」的判定前提不符。

这与 2026-08-16 `docs/ops/archive/sol-chain-muyuan-degradation-2026-08-16.md` 的判断
一致：两者只在**响应头时刻**生效，不覆盖流中途。该文档当时已写下
「Neither `AutomaticRetryStatusCodes` nor `AutomaticRetryOnEmptyResponseEnabled` covers this」。
2026-10-06 `ch3-100xlabs-unbilled-empty-2026-10-06.md` 已第二次记录该形态
（2 发 prompt=56713/completion=0/quota=2,126,738），并把「计费空回口径未覆盖」列为
后续项——今晚是第三次，本次把它补上了。

⚠️ 未做到的部分：fork 的 Go 源码不在本机（只有 08-05 退役归档），因此
「空回重试为何不触发」是**日志侧推断**，不是源码级证明。要坐实需读 fork relay 代码。

## 四、被本次取证推翻/修正的说法

- **上下文边界假设不成立**：曾猜 B 是接近窗口上限的请求被上游吃掉，但 ch127 成功计费
  行的 prompt 最大到 **309,907**（ch91 70 行成功均值 109,575），199k 远不是天花板。
- **当晚 21:34 那次「peers ch91 0/105、ch182 0/4 干净 → 单腿而非上游普遍劣化」的算法有瑕疵**:
  对比用的生命周期计数没套窗口过滤。套上 6h 窗口后，B 是 ch127×11 + ch91×1，
  「今晚是 ch127 单腿」仍成立；但 B 这个**形态**当天在 ch69(agnes-2.5-flash,
  p=110,668/quota=55,334)、ch86、ch91、ch127 上都出现过——所以它是 fork 的计费行为
  问题，不是某条腿的故障，任何路由调整都修不掉它。
- 先前把 B 描述成「重试风暴反复计 200k 上下文」——方向对，主体错：重试来自**调用侧**
  （master/default，同一 prompt 连发 3–4 次），不是 NewAPI 的重试。

## 五、ch127 路由：未作改动，等裁决

三个选项仍在桌上，本次一个都没执行：A 不动（滑窗 ~01:52 自行清空，A 类故障段已证实
19:43–19:52 后无新增）；B 改 `codex cli trace` affinity 规则/TTL，代价是 prompt cache
命中率；C 禁 ch127——但 `group=agent` 里 gpt-6-astra **只有 ch127 一条 enabled 腿**，
禁用即把 `codex-agent` 悬空，必须先补腿。

本次取证新增的判断：A 类（65 行）确实只属于 ch127 今晚，而 B 类与路由无关。
所以「要不要动 ch127」只取决于 A 是否复发，B 该由计费口径与调用侧重试预算解决。

## 六、部署与回滚

- 改动只在 `scripts/ops/guardian.py` / `scripts.ops.test_guardian.py`，
  **没有部署**。在线跑的是 `~/.omp/guardian/guardian.py`。
- 部署前状态校验过：`git show HEAD:scripts/ops/guardian.py` 与
  `~/.omp/guardian/guardian.py` **逐字节相同**（零漂移），所以部署是直拷、无需合并，
  回滚也是直拷回 HEAD 版本。
- 部署会改在线监控行为，属生产变更，按约束等明确授权后进行；回滚物已在 git HEAD。

## 七、遗留

- [ ] 谁在 20:23 用 `master`/default 把一个 199k 提示在 16 秒内连打 10 次？
      调用侧重试预算未查（这是 B 的直接放大器）。
- [ ] fork 源码级确认：空回重试的判定谓词到底看什么（body 空 / usage 缺失 / 状态码）。
- [ ] B 是否该不计费：`completion_tokens=0 且 frt<0` 时走 local_count 兜底全额收 prompt，
      属计费策略问题，需裁决，不在本次范围。
- [ ] 部署新口径到 `~/.omp/guardian/`（待授权）。

## 涉及文件

- `scripts/ops/guardian.py`：常量、`_query_unbilled_empty_rounds`、`_step_unbilled_empty_rounds`
- `scripts/ops/test_guardian.py`：`UnbilledEmptyRoundsTests` +4 例
- 取证脚本（一次性，未入库）：`D:\Temp\User\mitm\recheck_ch127_billed.py`、
  `shape_ch127_billed.py`、`shape_ch127_stream.py`、`shape_ch127_endreason.py`、
  `shape_ch127_billing.py`、`shape_ch127_who.py`、`shape_ch127_boundary.py`
