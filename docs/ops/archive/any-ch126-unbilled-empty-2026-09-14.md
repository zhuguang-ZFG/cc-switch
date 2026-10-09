# any 渠道空回 + ch126 探针误判降权 — 2026-09-14

## 背景

用户反馈"any 渠道空回"。ch126（`any-gpt-6-astra`，type=1，`https://anyrouter.top`，
唯一模型 gpt-6-astra）实测两类问题叠加，性质完全不同：

| 现象 | 计量口径 | 数据 | 性质 |
|---|---|---|---|
| 未计费空回 | type=2 且 prompt=0/completion=0/quota=0 | 7d 21.8%（423/1941）、24h 10.6%、6h 2.2% | 上游 200 结束但无内容无计费 |
| 探针 404 误判 | `channel test bad response` | 每 30–40min 一次 | 探针形态不相容，非渠道故障 |

空回记录的 content 为"上游没有返回计费信息，无法扣费（可能是上游超时）"，
is_stream=1。**旧 opus 空响应监控完全看不到它**：那条监控带
`prompt_tokens >= 1000` 门槛，而未计费空回的 prompt_tokens 恰好是 0 →
21.8% 的故障率在监控里读作 0.0%。

## 根因 1：探针 404 被当成渠道健康结论（已修）

探针实际报文：

```
bad response status code 404, message: 当前 API 不支持所选模型 gpt-6-astra
```

AnyRouter 这条腿只对 Codex 协议供 gpt-6-astra，管理探针的通用形态被上游拒收——
拒的是**请求**，不是渠道。该文案**不含 "invalid_request_error"**，Guardian 的
通用 `_is_probe_incompatible` 匹配不到 → `full_health_scan` 软失败累积 →
达阈值降权 **5→2**（state `degraded_channels["126"]`，2026-09-13T08:04:52），
而同窗口真实计费流量空回率 0%。降权后落到 4 一直未回。

**修复**（`codex_window_pool.py`）：新增 `is_any_pool()`（id+name+type+models+
host 五重校验，`urlsplit` 解析失败按 False）；`is_codex_probe_incompatible()`
先判凭证/余额类（invalid_api_key / insufficient_balance / 余额不足）直接返回
False，再对 any-pool 匹配该 404 文案。**刻意不做全局放宽**：同一条 404 对
其他渠道（ch15 等）、对改名/迁移后的 ch126 仍是致命错误。

## 根因 2：ch126 权重漂移无人 gate（已修）

`configure_codex_agent_any.py:128` 的 `verify_projection` 硬断言 ch126
`status == 1 且 weight == 5`；线上实际 weight=4 → 投影门禁本就处于 fail 状态
（本次修复前实跑 `--verify`：`RuntimeError: AnyRouter tier drifted`）。

**修复**：`PINNED_CHANNEL_WEIGHTS` 增加 `126: ("any-gpt-6-astra", 5)`。
固定路由层用权重排不掉流量，pin 后 `_auto_adjust_weights` 跳过该渠道
（:2015），软失败改走 `disable_slow_channel` 隔离 + 恢复时按契约权重回填。

**顺序依赖（重要）**：pin 必须与探针豁免同批上线。只 pin 不豁免，会把同一个
假故障从"反复降权"变成"反复禁用"。

## 根因 3：未计费空回不可观测（已修）

新增按 (channel_id, model) 分组的未计费空回监控，不写死模型名，任何腿超标都
自己暴露：

- 常量：`UNBILLED_EMPTY_WINDOW_HOURS=6` / `UNBILLED_EMPTY_MIN_SAMPLES=30` /
  `UNBILLED_EMPTY_THRESHOLD=0.20`（与 opus 监控同档）。
- `_query_unbilled_empty_rounds()`：只读 SQL，`type=2` 且
  `prompt_tokens=0 AND completion_tokens=0 AND quota=0` 计空，
  `HAVING total >= ?` 过样本量；DB 缺失/OSError/sqlite3.Error → None（fail-safe，
  不告警）。
- `_step_unbilled_empty_rounds()`：接入 `_run_step`（:3273，在 opus 步之后）；
  冷却键 `unbilled_empty:{channel_id}:{model}` **按腿独立**，一条腿的故障窗口
  不吞掉另一条腿的首告。

**告警阈值定档依据**（7d 全量 3h 分桶聚合，19 个 channel+model 组合）：
仅 ch126（滚动 6h 峰值 51.7%）与 ch3 claude-opus-5（30.1%）越过 20%，
仅 ch126 越过 35% → 20% 不会造成告警噪音。ch126 滚动 6h 中位 18.9%，
22 个窗口中 8 个 >20%。

## 变更

- `scripts/ops/codex_window_pool.py`：`is_any_pool()` + `is_codex_probe_incompatible()` 分池豁免。
- `scripts/ops/guardian.py`：pin ch126；未计费空回监控（常量 + 查询 + check + step + 接线）。
- `scripts/ops/test_codex_window_pool.py`：3 用例（探针形态不等于健康结论 / 不洗白
  401/402/窗口额度 / 其他池含 ch127+ch128 仍致命；身份负例 id/name/type/models/host）。
- `scripts/ops/test_guardian.py`：pin 回归 1 用例 + `UnbilledEmptyRoundsTests` 6 用例
  （零 token 零 quota 计空 / 阈值与样本量双门 / 告警点名渠道与模型 / 冷却按腿独立 /
  窗口排除更早轮次 / DB 缺失返回 None）。
- 线上同步 `~/.omp/guardian/`：guardian.py、codex_window_pool.py、test_guardian.py，
  并**首次补齐** test_codex_window_pool.py（此前线上缺失）。四个文件 sha 与 repo 一致。
- 备份：`*.bak-20260914-033000`（线上三份原件，与 git HEAD 逐字节相同）、
  `state.json.bak-20260914-033549`。

## 线上修复 ch126 权重 4→5

- 回滚件：`~/.new-api-local/backups/ch126-weight-pre-20260914-033432.json`
  （key 字段落盘为 `<redacted sha256:...>` 占位，38 字符；真 key 51 字符，
  已复核落盘值既非真 key 也非其指纹——**不含可用凭证**）。
- 走 Guardian 自己的 `update_channel()`：`_hydrate_channel_key()` 从本地 SSOT
  取真 key 再 PUT（管理 API 读出的 key 为空，直接 PUT 会抹凭证）。
- 结果：DB weight=5、abilities `(126, gpt-6-astra, 1, 40, 5)` 同步、
  key 指纹 `6901459ad151` 前后一致、`--verify` 门禁 rc=0（修复前 fail）。

## 清理陈旧 degraded 记录

pin 后 `_auto_adjust_weights` 跳过 ch126 → :2066 的"完全恢复即清记录"分支
永不再执行，`degraded_channels["126"]` 会永久残留（污染 metrics 与
`derive_channel_lifecycle` 的 degraded 视图）。故在引擎停止期间清理（Guardian
内存持有 state 并每轮回写，运行中改文件会被覆盖）。

`weight_history["126"]` 保留 weight=5，与恢复后的契约权重一致。

## 引擎重启

schtasks `\NewAPI Guardian` /end（旧 PID 28412 确认死亡）→ 清 state →
/run（新 PID 4964，心跳 03:36:41 已切新 PID）。watchdog 仅在心跳 stale 180s
且 300s 退避后动作，本次停-改-起在窗口内，无抢跑。

## 验证

- 单测：`test_guardian.py` 210 绿、`test_codex_window_pool.py` 12 绿
  （两文件**必须分开跑**：合并收集会双导入 test_guardian 造成双临时 HOME，
  9 个 ProxyRestartTests 在干净 HEAD 上也同样失败，与本次改动无关）。
- 3.12 复核：Guardian 实际由 uv cpython-3.12.13 的 pythonw 启动，已在 3.12 下
  compile + import + 实跑查询（非仅 3.13）。
- 实数据回放（线上库，部署后的代码与阈值）：当前 6h 窗口无 offender（0.61s）；
  向后回放历史 6h 窗口命中 ch126 20.2%（330/1632）、20.7%（380/1835）——
  证明"当前干净"是真干净，不是监控失灵。
- 豁免现场校验：对**逐字**线上报文 `is_any_pool=True`、判定 soft=True（保腿）；
  401 invalid_api_key / 402 余额不足 / 500 upstream 均 soft=False；
  同一 404 落到 ch15 或改名后的 ch126 均 soft=False。
- 重启后日志：ERROR/CRITICAL 0、step 失败 0、ch126 降权 0、禁用 0。
- 另注：日志中 16 条 `_step_pool_legs` 的 `'Guardian' object has no attribute
  'state'` 全部落在 09-13 23:16–23:18，属 PID 28412（23:19:00 启动）之前的
  上一代进程；部署版该方法用 `self.autofix.state`，重启后无复现。

## 未处理 / 后续

- **空回根因在上游**：AnyRouter 以 200 结束流且不回计费信息，NewAPI 对静默空回
  无法重试（无错误码可挂 `AutomaticRetryStatusCodes`）。本次交付的是可观测性 +
  分类正确性；若告警持续触发，处置手段是查上游或降该腿权重（降权需同步改
  `verify_projection` 的断言，否则门禁 fail）。
- ch3 justwoker claude-opus-5 6h 窗口曾达 23.5%，已在新监控覆盖内，未单独处置。
- `pool_model_drift()` 仍无调用方。
- 线上遗留 `guardian.py.deploy-20820.tmp`（非本次产生，未动）。
- **未来若要做 "最后一条腿不禁用" 的 sole-leg guard，必须避开降权链的触底升级**：
  `degrade_channel_weight` 在 `current_weight <= MIN_WEIGHT` 时直接
  `return self.disable_slow_channel(channel)`（guardian.py:1970-1972）。
  也就是 10→5→2→1 走完后，"降权代替禁用" 会静默变成禁用，绕过 guard——
  恰好在渠道最虚弱、最需要保腿时失效。可行形状二选一：guard 路径降到
  `MIN_WEIGHT` 即封底并只告警；或另立 hold 记录（`_auto_adjust_weights` 与
  升级分支都不认它），唯一出口是恢复判定。
  当前交付**不可达**该陷阱，勿据此改代码：`_step_unbilled_empty_rounds` /
  `_step_opus_empty_response` / `_step_pool_legs` 三步均只告警（零 mutating
  调用，已核）；`degrade_channel_weight` 现存调用方只有
  `_auto_adjust_weights` :2034/:2040，两处都在 pin 跳过（:2015）之后，
  已 pin 的 ch126 进不了降权链。
